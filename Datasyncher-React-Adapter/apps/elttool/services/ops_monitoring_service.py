"""Platform ops monitoring: aggregate DB, Redis, and Celery state for admin dashboard."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional
from urllib import request as urlrequest
from urllib.error import URLError

import redis
from django.conf import settings
from apps.elttool.models import MigrationRecord, PlanResourceConfig
from apps.elttool.services.celery_inspect_service import (
    get_celery_worker_status,
    safe_inspect_call,
    tasks_for_job_name,
)
from apps.elttool.services.chunk_limit_resolver import (
    normalize_plan_name,
    resolve_chunk_size_django,
)

logger = logging.getLogger(__name__)

REDIS_ACTIVE_SET_KEY = "celery:job-worker:active"
PROVISION_QUEUE_KEY = "celery:provision-queue"
MIGRATION_CONTROL_QUEUE = os.getenv("CELERY_MIGRATION_CONTROL_QUEUE", "migration-control")
WORKER_PODS_CAP = int(os.getenv("CELERY_CLUSTER_MAX_JOB_WORKER_PODS", "200"))
POOL_POD_CAPS = {
    "worker-standard": int(os.getenv("CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_STANDARD", "100")),
    "worker-premium": int(os.getenv("CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_PREMIUM", "50")),
    "worker-enterprise": int(os.getenv("CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_ENTERPRISE", "50")),
}
ISOLATION_MODE = os.getenv("CELERY_JOB_ISOLATION_MODE", "per_job")


def job_hash(job_name: str) -> str:
    return hashlib.sha256(job_name.encode("utf-8")).hexdigest()[:12]


def _meta_key_from_hash(h: str) -> str:
    return f"celery:job-worker:{h}:meta"


def get_ops_redis():
    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


def _fetch_prometheus_gauges() -> dict[str, float]:
    url = getattr(
        settings,
        "OPS_METRICS_URL",
        os.getenv(
            "OPS_METRICS_URL",
            "http://celery-worker-control-metrics:9100/metrics",
        ),
    )
    if not url:
        return {}
    try:
        with urlrequest.urlopen(url, timeout=2) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, ValueError) as exc:
        logger.debug("OPS_METRICS_FETCH_FAILED | url=%s | err=%s", url, exc)
        return {}
    gauges: dict[str, float] = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(datasyncher_\w+)(?:\{.*\})?\s+([0-9.eE+-]+)$", line.strip())
        if m:
            try:
                gauges[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return gauges


def load_job_worker_meta(r, job_name: str) -> Optional[dict]:
    try:
        raw = r.get(_meta_key_from_hash(job_hash(job_name)))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.debug("JOB_META_LOAD_FAILED | job=%s | err=%s", job_name, exc)
        return None


def list_job_workers_from_redis(r) -> list[dict]:
    workers: list[dict] = []
    try:
        members = r.smembers(REDIS_ACTIVE_SET_KEY) or []
        for h in members:
            raw = r.get(_meta_key_from_hash(h))
            if not raw:
                continue
            meta = json.loads(raw)
            meta["job_hash"] = h
            workers.append(meta)
    except Exception as exc:
        logger.debug("REDIS_JOB_WORKERS_LIST_FAILED | err=%s", exc)
    return workers


def sum_worker_pods_used(r) -> int:
    total = 0
    for meta in list_job_workers_from_redis(r):
        total += int(meta.get("replicas") or 0)
    return total


def sum_worker_pods_by_pool(r) -> dict[str, int]:
    totals: dict[str, int] = {}
    for meta in list_job_workers_from_redis(r):
        pool = str(meta.get("node_pool") or "unknown")
        totals[pool] = totals.get(pool, 0) + int(meta.get("replicas") or 0)
    return totals


def pipeline_reserved_unstarted_count() -> int:
    """Reserved pipeline tasks that have not been given to a worker process."""
    bucket = safe_inspect_call("reserved", timeout=2)
    count = 0
    for tasks in bucket.values():
        for task in tasks or []:
            if not isinstance(task, dict) or task.get("time_start") is not None:
                continue
            name = str(task.get("name") or "")
            if "pipeline" in name:
                count += 1
    return count


def executorless_running_runs() -> list[dict[str, Any]]:
    """Running flows that never received an executor. These are stuck, not queued."""
    from apps.elttool.models import PipelineRun

    now = datetime.now(timezone.utc)
    rows = []
    runs = PipelineRun.objects.filter(status="running").only("id", "started_at", "metadata")
    for run in runs:
        meta = run.metadata or {}
        if meta.get("executor") or meta.get("failure_pending"):
            continue
        started = run.started_at
        age = int((now - started).total_seconds()) if started else 0
        rows.append({"run_id": run.id, "age_seconds": age})
    return rows


def _effective_chunk_for_connection(connection) -> Optional[int]:
    if connection is None:
        return None
    cmp = connection.cmp
    src = connection.src_config.src if connection.src_config else None
    dst = connection.dst_config.dst if connection.dst_config else None
    if not src or not dst:
        return None
    plan_name = normalize_plan_name(getattr(cmp, "plan", None) if cmp else "free")
    try:
        plan_row = PlanResourceConfig.objects.get(plan_name=plan_name)
    except PlanResourceConfig.DoesNotExist:
        plan_row = PlanResourceConfig.objects.filter(plan_name="free").first()
        if plan_row is None:
            return None
    override = getattr(cmp, "resource_override", None) if cmp else None
    result = resolve_chunk_size_django(
        connection_chunk_count=connection.chunk_count,
        plan_config=plan_row,
        src_min=getattr(src, "min_count", None),
        src_max=getattr(src, "max_count", None),
        dst_min=getattr(dst, "min_count", None),
        dst_max=getattr(dst, "max_count", None),
        is_reverse_etl=bool(getattr(src, "is_rtl", False)),
        override=override,
    )
    return result.effective_chunk


def _base_job_queryset():
    return (
        MigrationRecord.objects.filter(status="in_progress")
        .select_related(
            "connection",
            "connection__cmp",
            "connection__src_config__src",
            "connection__dst_config__dst",
        )
        .order_by("-updated_at")
    )


def _table_counts_for_job(records) -> dict[str, int]:
    counts = {
        "tables_total": 0,
        "tables_completed": 0,
        "tables_failed": 0,
        "tables_in_progress": 0,
    }
    for rec in records:
        if rec.table_name is None:
            continue
        counts["tables_total"] += 1
        if rec.status == "completed":
            counts["tables_completed"] += 1
        elif rec.status == "failed":
            counts["tables_failed"] += 1
        elif rec.status == "in_progress":
            counts["tables_in_progress"] += 1
    return counts


def build_job_summary(
    job_name: str,
    records: list,
    r,
    *,
    include_tables: bool = False,
) -> dict[str, Any]:
    first = records[0]
    conn = first.connection
    cmp = conn.cmp if conn else None
    src = conn.src_config.src if conn and conn.src_config else None
    dst = conn.dst_config.dst if conn and conn.dst_config else None
    counts = _table_counts_for_job(records)
    meta = load_job_worker_meta(r, job_name) if r else None
    row: dict[str, Any] = {
        "job_name": job_name,
        "migration_session_id": first.migration_session_id,
        "connection_id": conn.connection_id if conn else None,
        "company_id": cmp.cmp_id if cmp else None,
        "company_name": getattr(cmp, "cmp_name", None) if cmp else None,
        "plan": getattr(cmp, "plan", None) if cmp else None,
        "chunk_count": conn.chunk_count if conn else None,
        "effective_chunk": _effective_chunk_for_connection(conn),
        "src_max_count": getattr(src, "max_count", None) if src else None,
        "dst_max_count": getattr(dst, "max_count", None) if dst else None,
        "src_name": getattr(src, "name", None) if src else None,
        "dst_name": getattr(dst, "name", None) if dst else None,
        "job_hash": job_hash(job_name),
        **counts,
        "worker_meta": meta,
        "updated_at": max((r.updated_at for r in records if r.updated_at), default=None),
    }
    if row["updated_at"]:
        row["updated_at"] = row["updated_at"].isoformat()
    if include_tables:
        row["tables"] = [
            {
                "table_name": rec.table_name,
                "status": rec.status,
                "staging_records_count": rec.staging_records_count,
                "task_id": rec.task_id,
                "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
            }
            for rec in records
            if rec.table_name is not None
        ]
        row["child_tasks"] = tasks_for_job_name(job_name)
    return row


def build_ops_overview(r=None) -> dict[str, Any]:
    if r is None:
        r = get_ops_redis()
    qs = _base_job_queryset()
    session_ids = set(qs.values_list("migration_session_id", flat=True))
    job_names = set(qs.values_list("job_name", flat=True))
    celery_summary = get_celery_worker_status()
    pods_used = sum_worker_pods_used(r)
    try:
        provision_depth = int(r.zcard(PROVISION_QUEUE_KEY) or 0)
    except Exception:
        provision_depth = 0
    try:
        control_depth = int(r.llen(MIGRATION_CONTROL_QUEUE) or 0)
    except Exception:
        control_depth = 0
    try:
        pipeline_depth = int(r.llen("pipeline") or 0)
    except Exception:
        pipeline_depth = 0
    executorless = executorless_running_runs()
    prom = _fetch_prometheus_gauges()
    if prom.get("datasyncher_cluster_worker_pods_used") is not None:
        pods_used = int(prom["datasyncher_cluster_worker_pods_used"])
    if prom.get("datasyncher_provision_queue_depth") is not None:
        provision_depth = int(prom["datasyncher_provision_queue_depth"])
    return {
        "active_sessions": len(session_ids),
        "active_jobs": len(job_names),
        "worker_pods_used": pods_used,
        "worker_pods_cap": WORKER_PODS_CAP,
        "worker_pods_by_pool": sum_worker_pods_by_pool(r),
        "worker_pod_caps_by_pool": POOL_POD_CAPS,
        "provision_queue_depth": provision_depth,
        "control_queue_depth": control_depth,
        "pipeline_queue_depth": pipeline_depth,
        "pipeline_reserved_unstarted": pipeline_reserved_unstarted_count(),
        "executorless_running_runs": executorless,
        "celery_workers": {
            "workers_available": celery_summary.get("workers_available"),
            "total_workers": celery_summary.get("total_workers", 0),
            "total_active_tasks": celery_summary.get("total_active_tasks", 0),
            "error": celery_summary.get("error"),
        },
        "isolation_mode": ISOLATION_MODE,
        "prometheus_gauges": prom or None,
    }


def build_ops_jobs_list(
    *,
    company_id: Optional[int] = None,
    plan: Optional[str] = None,
    limit: int = 50,
    r=None,
) -> list[dict]:
    if r is None:
        r = get_ops_redis()
    qs = _base_job_queryset()
    if company_id is not None:
        qs = qs.filter(connection__cmp_id=company_id)
    if plan:
        qs = qs.filter(connection__cmp__plan__iexact=plan.strip())
    grouped: dict[str, list] = defaultdict(list)
    for rec in qs[: max(limit * 20, 200)]:
        grouped[rec.job_name].append(rec)
    jobs = []
    for job_name, records in grouped.items():
        jobs.append(build_job_summary(job_name, records, r))
    jobs.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return jobs[:limit]


def build_ops_job_detail(job_name: str, r=None) -> Optional[dict[str, Any]]:
    if r is None:
        r = get_ops_redis()
    records = list(
        MigrationRecord.objects.filter(job_name=job_name)
        .select_related(
            "connection",
            "connection__cmp",
            "connection__src_config__src",
            "connection__dst_config__dst",
        )
        .order_by("table_name")
    )
    if not records:
        return None
    return build_job_summary(job_name, records, r, include_tables=True)


def build_ops_workers(r=None) -> dict[str, Any]:
    if r is None:
        r = get_ops_redis()
    celery_summary = get_celery_worker_status()
    all_workers = celery_summary.get("workers") or {}
    control_workers = {
        k: v for k, v in all_workers.items() if "control" in k.lower()
    }
    job_worker_hosts = {
        k: v for k, v in all_workers.items() if "celery-worker-job" in k.lower()
    }
    return {
        "control_worker": control_workers,
        "job_worker_hosts": job_worker_hosts,
        "job_workers": list_job_workers_from_redis(r),
        "celery_inspect": celery_summary,
    }


def build_ops_snapshot() -> dict[str, Any]:
    r = get_ops_redis()
    overview = build_ops_overview(r)
    jobs = build_ops_jobs_list(r=r, limit=50)
    workers = build_ops_workers(r)
    return {
        "event": "ops_snapshot",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overview": overview,
        "jobs": jobs,
        "workers": workers,
    }
