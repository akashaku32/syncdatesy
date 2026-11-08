"""
RFC-001 §11.1 Prometheus metrics for per-job worker isolation.

No-op when CELERY_PROMETHEUS_METRICS_ENABLED is false (default).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

METRICS_ENABLED = os.getenv("CELERY_PROMETHEUS_METRICS_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
METRICS_PORT = int(os.getenv("CELERY_METRICS_PORT", "9100"))
CLUSTER_MAX_PODS = int(os.getenv("CELERY_CLUSTER_MAX_JOB_WORKER_PODS", "40"))

_provision_started_at: dict[str, float] = {}
_metrics_server_started = False
_metrics_lock = threading.Lock()

# Lazy-init metrics on first use to avoid import cost when disabled.
_registry = None
_metrics = None


def _label(value: Optional[str], default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _get_metrics():
    global _registry, _metrics
    if _metrics is not None:
        return _metrics
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    _registry = CollectorRegistry()
    _metrics = {
        "registry": _registry,
        "job_worker_replicas": Gauge(
            "datasyncher_job_worker_replicas",
            "Active replicas for a per-job worker Deployment",
            ["company_id", "plan", "job_hash"],
            registry=_registry,
        ),
        "job_provision_latency_seconds": Histogram(
            "datasyncher_job_provision_latency_seconds",
            "Seconds from provision request to readyReplicas>=1",
            ["plan"],
            buckets=(5, 15, 30, 60, 90, 120, 180, 300),
            registry=_registry,
        ),
        "job_worker_ready_timeout_total": Counter(
            "datasyncher_job_worker_ready_timeout_total",
            "Provision readiness gate timeouts",
            ["plan"],
            registry=_registry,
        ),
        "job_teardown_total": Counter(
            "datasyncher_job_teardown_total",
            "Job worker Deployment teardown attempts",
            ["status"],
            registry=_registry,
        ),
        "migration_worker_memory_bytes": Gauge(
            "datasyncher_migration_worker_memory_bytes",
            "Process RSS bytes on migration worker pod",
            ["pod", "connector"],
            registry=_registry,
        ),
        "chunk_reduced_total": Counter(
            "datasyncher_chunk_reduced_total",
            "Chunk size reductions (plan clamp or memory estimate)",
            ["connector", "reason"],
            registry=_registry,
        ),
        "oom_killed_total": Counter(
            "datasyncher_oom_killed_total",
            "Worker pod OOMKilled events",
            ["job_hash"],
            registry=_registry,
        ),
        "cluster_worker_pods_used": Gauge(
            "datasyncher_cluster_worker_pods_used",
            "Sum of active per-job worker replicas",
            registry=_registry,
        ),
        "provision_queue_depth": Gauge(
            "datasyncher_provision_queue_depth",
            "Deferred provision queue depth",
            registry=_registry,
        ),
        "provision_deferred_total": Counter(
            "datasyncher_provision_deferred_total",
            "Provisions deferred to queue",
            ["reason"],
            registry=_registry,
        ),
        "startup_orphan_sweep_deleted_total": Counter(
            "datasyncher_startup_orphan_sweep_deleted_total",
            "Orphan job worker Deployments deleted on control worker startup",
            registry=_registry,
        ),
        "concurrency_reduced_total": Counter(
            "datasyncher_concurrency_reduced_total",
            "Times effective concurrency was below plan due to memory estimate",
            ["plan"],
            registry=_registry,
        ),
    }
    return _metrics


def metrics_enabled() -> bool:
    return METRICS_ENABLED


def get_registry():
    if not METRICS_ENABLED:
        return None
    return _get_metrics()["registry"]


def mark_provision_started(job_name: str) -> None:
    if not METRICS_ENABLED:
        return
    _provision_started_at[job_name] = time.monotonic()


def observe_provision_ready(
    job_name: str,
    *,
    plan: Optional[str] = None,
) -> None:
    if not METRICS_ENABLED:
        return
    started = _provision_started_at.pop(job_name, None)
    if started is None:
        return
    _get_metrics()["job_provision_latency_seconds"].labels(
        plan=_label(plan),
    ).observe(time.monotonic() - started)


def inc_ready_timeout(plan: Optional[str] = None) -> None:
    if not METRICS_ENABLED:
        return
    _get_metrics()["job_worker_ready_timeout_total"].labels(
        plan=_label(plan),
    ).inc()


def inc_teardown(success: bool) -> None:
    if not METRICS_ENABLED:
        return
    status = "success" if success else "fail"
    _get_metrics()["job_teardown_total"].labels(status=status).inc()


def set_job_replicas(
    *,
    company_id: Optional[int],
    plan: Optional[str],
    job_hash: str,
    replicas: int,
) -> None:
    if not METRICS_ENABLED:
        return
    _get_metrics()["job_worker_replicas"].labels(
        company_id=_label(str(company_id) if company_id is not None else None),
        plan=_label(plan),
        job_hash=_label(job_hash),
    ).set(max(0, int(replicas)))


def clear_job_replicas(
    *,
    company_id: Optional[int],
    plan: Optional[str],
    job_hash: str,
    job_name: Optional[str] = None,
) -> None:
    if not METRICS_ENABLED:
        return
    try:
        _get_metrics()["job_worker_replicas"].labels(
            company_id=_label(str(company_id) if company_id is not None else None),
            plan=_label(plan),
            job_hash=_label(job_hash),
        ).set(0)
    except Exception:
        pass
    if job_name:
        _provision_started_at.pop(job_name, None)


def observe_memory_rss(
    rss_bytes: int,
    *,
    pod: Optional[str] = None,
    connector: Optional[str] = None,
) -> None:
    if not METRICS_ENABLED:
        return
    _get_metrics()["migration_worker_memory_bytes"].labels(
        pod=_label(pod or os.getenv("HOSTNAME", "unknown")),
        connector=_label(connector),
    ).set(max(0, int(rss_bytes)))


def inc_chunk_event(connector: Optional[str], reason: str) -> None:
    if not METRICS_ENABLED:
        return
    _get_metrics()["chunk_reduced_total"].labels(
        connector=_label(connector),
        reason=_label(reason, "unknown"),
    ).inc()


def inc_oom(job_hash: str) -> None:
    if not METRICS_ENABLED:
        return
    _get_metrics()["oom_killed_total"].labels(
        job_hash=_label(job_hash),
    ).inc()


def inc_provision_deferred(reason: str) -> None:
    if not METRICS_ENABLED:
        return
    _get_metrics()["provision_deferred_total"].labels(
        reason=_label(reason, "unknown"),
    ).inc()


def inc_orphan_sweep_deleted(count: int = 1) -> None:
    if not METRICS_ENABLED:
        return
    if count > 0:
        _get_metrics()["startup_orphan_sweep_deleted_total"].inc(count)


def inc_concurrency_reduced(plan: Optional[str]) -> None:
    if not METRICS_ENABLED:
        return
    _get_metrics()["concurrency_reduced_total"].labels(
        plan=_label(plan),
    ).inc()


def refresh_cluster_gauges(r, *, active_meta_key_prefix: str = "celery:job-worker:") -> None:
    """Reconcile replica gauge and cluster/queue gauges from Redis."""
    if not METRICS_ENABLED:
        return
    m = _get_metrics()
    total_replicas = 0
    try:
        from celery_job_worker_pool import (
            PROVISION_QUEUE_KEY,
            REDIS_ACTIVE_SET_KEY,
            _meta_key_from_hash,
        )

        members = r.smembers(REDIS_ACTIVE_SET_KEY) or []
        for raw in members:
            h = raw.decode() if isinstance(raw, bytes) else str(raw)
            meta_raw = r.get(_meta_key_from_hash(h))
            if not meta_raw:
                continue
            meta = json.loads(meta_raw)
            replicas = int(meta.get("replicas") or 0)
            total_replicas += replicas
            set_job_replicas(
                company_id=meta.get("company_id"),
                plan=meta.get("plan_name"),
                job_hash=h,
                replicas=replicas,
            )
        depth = r.zcard(PROVISION_QUEUE_KEY) if hasattr(r, "zcard") else len(
            r.zrange(PROVISION_QUEUE_KEY, 0, -1) if hasattr(r, "zrange") else []
        )
        m["provision_queue_depth"].set(int(depth or 0))
    except Exception as exc:
        logger.debug("METRICS_REFRESH_FAILED | err=%s", exc)
    m["cluster_worker_pods_used"].set(total_replicas)


def start_metrics_server(port: Optional[int] = None) -> bool:
    """Start Prometheus HTTP /metrics on control worker (idempotent)."""
    global _metrics_server_started
    if not METRICS_ENABLED:
        return False
    with _metrics_lock:
        if _metrics_server_started:
            return True
        port = port if port is not None else METRICS_PORT
        registry = get_registry()
        if registry is None:
            return False
        try:
            from prometheus_client import start_http_server

            start_http_server(port, registry=registry)
            _metrics_server_started = True
            logger.info("PROMETHEUS_METRICS_SERVER_STARTED | port=%s", port)
            return True
        except Exception as exc:
            logger.warning("PROMETHEUS_METRICS_SERVER_FAILED | port=%s | err=%s", port, exc)
            return False


def init_metrics_for_test(registry=None):
    """Bind metrics to an isolated registry (unit tests only)."""
    global _registry, _metrics, _metrics_server_started, METRICS_ENABLED
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    METRICS_ENABLED = True
    _metrics_server_started = False
    _provision_started_at.clear()
    _registry = registry or CollectorRegistry()
    _metrics = {
        "registry": _registry,
        "job_worker_replicas": Gauge(
            "datasyncher_job_worker_replicas",
            "Active replicas for a per-job worker Deployment",
            ["company_id", "plan", "job_hash"],
            registry=_registry,
        ),
        "job_provision_latency_seconds": Histogram(
            "datasyncher_job_provision_latency_seconds",
            "Seconds from provision request to readyReplicas>=1",
            ["plan"],
            buckets=(5, 15, 30, 60, 90, 120, 180, 300),
            registry=_registry,
        ),
        "job_worker_ready_timeout_total": Counter(
            "datasyncher_job_worker_ready_timeout_total",
            "Provision readiness gate timeouts",
            ["plan"],
            registry=_registry,
        ),
        "job_teardown_total": Counter(
            "datasyncher_job_teardown_total",
            "Job worker Deployment teardown attempts",
            ["status"],
            registry=_registry,
        ),
        "migration_worker_memory_bytes": Gauge(
            "datasyncher_migration_worker_memory_bytes",
            "Process RSS bytes on migration worker pod",
            ["pod", "connector"],
            registry=_registry,
        ),
        "chunk_reduced_total": Counter(
            "datasyncher_chunk_reduced_total",
            "Chunk size reductions (plan clamp or memory estimate)",
            ["connector", "reason"],
            registry=_registry,
        ),
        "oom_killed_total": Counter(
            "datasyncher_oom_killed_total",
            "Worker pod OOMKilled events",
            ["job_hash"],
            registry=_registry,
        ),
        "cluster_worker_pods_used": Gauge(
            "datasyncher_cluster_worker_pods_used",
            "Sum of active per-job worker replicas",
            registry=_registry,
        ),
        "provision_queue_depth": Gauge(
            "datasyncher_provision_queue_depth",
            "Deferred provision queue depth",
            registry=_registry,
        ),
        "provision_deferred_total": Counter(
            "datasyncher_provision_deferred_total",
            "Provisions deferred to queue",
            ["reason"],
            registry=_registry,
        ),
        "startup_orphan_sweep_deleted_total": Counter(
            "datasyncher_startup_orphan_sweep_deleted_total",
            "Orphan job worker Deployments deleted on control worker startup",
            registry=_registry,
        ),
        "concurrency_reduced_total": Counter(
            "datasyncher_concurrency_reduced_total",
            "Times effective concurrency was below plan due to memory estimate",
            ["plan"],
            registry=_registry,
        ),
    }
    return _metrics
