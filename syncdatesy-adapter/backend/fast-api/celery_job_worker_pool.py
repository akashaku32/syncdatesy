"""
Per-job Celery worker pool: queue naming, K8s Deployment lifecycle, Redis registry.

Used when CELERY_JOB_ISOLATION_MODE=per_job.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TYPE_CHECKING

import requests
from sqlalchemy import text

import celery_job_metrics as job_metrics

if TYPE_CHECKING:
    from celery_resource_config import EffectiveResourceConfig

logger = logging.getLogger(__name__)

_K8S_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_K8S_SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
_K8S_API_HOST = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
_K8S_API_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")

CELERY_JOB_ISOLATION_MODE = os.getenv("CELERY_JOB_ISOLATION_MODE", "per_job").strip().lower()
CELERY_MIGRATION_CONTROL_QUEUE = os.getenv("CELERY_MIGRATION_CONTROL_QUEUE", "migration-control")
CELERY_WORKER_ROLE = os.getenv("CELERY_WORKER_ROLE", "").strip().lower()
CELERY_JOB_WORKER_READY_TIMEOUT = int(os.getenv("CELERY_JOB_WORKER_READY_TIMEOUT", "120"))
# Safety total across every plan pool. The cap that isolates accounts is per pool.
# Defaults sized for ~100 concurrent accounts; override via env in control workers.
CELERY_CLUSTER_MAX_JOB_WORKER_PODS = int(os.getenv("CELERY_CLUSTER_MAX_JOB_WORKER_PODS", "200"))
# Each plan's node pool (standard, premium, enterprise) gets its own budget.
# A full premium pool must not defer a new free or standard account.
CELERY_CLUSTER_MAX_JOB_WORKER_PODS_PER_POOL = int(
    os.getenv("CELERY_CLUSTER_MAX_JOB_WORKER_PODS_PER_POOL", "100")
)
# Optional per-pool overrides. Unset pools keep CELERY_CLUSTER_MAX_JOB_WORKER_PODS_PER_POOL.
_POOL_CAP_ENV = {
    "worker-standard": "CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_STANDARD",
    "worker-premium": "CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_PREMIUM",
    "worker-enterprise": "CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_ENTERPRISE",
}


def _pool_pod_cap(pool_name: Optional[str]) -> int:
    env_name = _POOL_CAP_ENV.get((pool_name or "").strip())
    if env_name and os.getenv(env_name):
        return int(os.environ[env_name])
    return CELERY_CLUSTER_MAX_JOB_WORKER_PODS_PER_POOL
_PLAN_NODE_POOL = {
    "free": "worker-standard",
    "standard": "worker-standard",
    "discounted": "worker-standard",
    "premium": "worker-premium",
    "enterprise": "worker-enterprise",
}
CELERY_JOB_WORKER_TEARDOWN_GRACE_SECONDS = int(os.getenv("CELERY_JOB_WORKER_TEARDOWN_GRACE_SECONDS", "60"))
CELERY_K8S_NAMESPACE = os.getenv("CELERY_K8S_NAMESPACE", "").strip()
CELERY_K8S_POD_STATUS_ENABLED = os.getenv("CELERY_K8S_POD_STATUS_ENABLED", "true").lower() in (
    "1", "true", "yes",
)
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "3"))
CELERY_KEDA_MAX_REPLICAS = int(os.getenv("CELERY_KEDA_MAX_REPLICAS", "8"))
CELERY_KEDA_MAX_PODS_PER_JOB = max(1, int(os.getenv("CELERY_KEDA_MAX_PODS_PER_JOB", "6")))
# Default on: one Redis/K8s fleet per pipeline run so Batch 2 cannot hit
# company_job_cap while Batch 1 of the same run still holds a fleet slot.
PIPELINE_FLOW_FLEET_REUSE_ENABLED = os.getenv(
    "PIPELINE_FLOW_FLEET_REUSE_ENABLED", "true"
).lower() in ("1", "true", "yes")
FLOW_FLEET_LEASE_TTL_SECONDS = int(os.getenv("FLOW_FLEET_LEASE_TTL_SECONDS", "86400"))

JOB_WORKER_LABEL_MANAGED_BY = "job-pool"
JOB_WORKER_LABEL_APP = "celery-worker-job"
FLOW_WORKER_LABEL_APP = "celery-worker-flow"
JOB_WORKER_LABEL_HASH = "datasyncher.com/job-hash"
JOB_WORKER_LABEL_MANAGED = "datasyncher.com/managed-by"
JOB_WORKER_LABEL_COMPANY = "datasyncher.com/company-id"
JOB_WORKER_LABEL_PIPELINE_RUN = "datasyncher.com/pipeline-run-id"
JOB_WORKER_LABEL_FLEET = "datasyncher.com/fleet"

# Fallback when no worker_node_pool_config row is resolved (batch cluster pool).
WORKER_POOL_NODE_SELECTOR = {"pool": "worker-standard"}
JOB_WORKER_LABEL_PLAN = "datasyncher.com/plan"
JOB_WORKER_LABEL_POOL = "datasyncher.com/worker-pool"

REDIS_ACTIVE_SET_KEY = "celery:job-worker:active"
PROVISION_QUEUE_KEY = "celery:provision-queue"
PROVISION_PAYLOAD_PREFIX = "celery:provision-payload:"
AWAITING_PROVISION_PREFIX = "celery:awaiting-provision:"
AWAITING_PROVISION_SET = "celery:awaiting-provision:active"
PROVISION_RATE_KEY = "celery:provision-rate:minute"
FLOW_FLEET_LEASE_PREFIX = "celery:flow-fleet:run:"
# Cluster-wide safety only. Each organisation is limited separately so one
# company's burst cannot use up the minute for every other company.
CELERY_PROVISION_RATE_PER_MINUTE = int(os.getenv("CELERY_PROVISION_RATE_PER_MINUTE", "60"))
CELERY_COMPANY_PROVISION_RATE_PER_MINUTE = int(
    os.getenv("CELERY_COMPANY_PROVISION_RATE_PER_MINUTE", "6")
)
COMPANY_DEFER_REASONS = frozenset({
    "company_job_cap",
    "company_pod_cap",
    "company_rate_limit",
})
STARTUP_ORPHAN_MAX_DELETES = int(os.getenv("CELERY_STARTUP_ORPHAN_MAX_DELETES", "10"))

META_STATE_PENDING = "pending"
META_STATE_PROVISIONING = "provisioning"
META_STATE_READY = "ready"
META_STATE_DRAINING = "draining"
META_STATE_DELETED = "deleted"

_PIPELINE_RUN_ID_RE = re.compile(r"^pipeline_run_(\d+)")
# Exact run-scoped fleet identity (not pipeline_run_<id>_node_...).
_RUN_SCOPED_FLEET_KEY_RE = re.compile(r"^pipeline_run_(\d+)$")


def job_isolation_enabled() -> bool:
    return CELERY_JOB_ISOLATION_MODE == "per_job"


def flow_fleet_reuse_enabled() -> bool:
    return bool(PIPELINE_FLOW_FLEET_REUSE_ENABLED)


def is_control_worker() -> bool:
    return CELERY_WORKER_ROLE == "control"


def is_reconcile_worker() -> bool:
    return CELERY_WORKER_ROLE == "control-reconcile"


def job_hash(job_name: str) -> str:
    return hashlib.sha256((job_name or "").encode("utf-8")).hexdigest()[:12]


def parse_pipeline_run_id(job_name: str) -> Optional[int]:
    match = _PIPELINE_RUN_ID_RE.match(job_name or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def is_run_scoped_fleet_key(job_name: str) -> bool:
    """True for exact identity ``pipeline_run_<id>`` (shared flow fleet key)."""
    return bool(_RUN_SCOPED_FLEET_KEY_RE.match((job_name or "").strip()))


# Optional per-call fleet identity from Django (flow_pod_state.fleet_key).
_fleet_key_override: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "fleet_key_override",
    default=None,
)


def is_flow_fleet_job(job_name: str) -> bool:
    """True when this job should use (or already uses) a run-scoped flow fleet.

    Also true for exact ``pipeline_run_<id>`` keys when reuse is currently off so
    teardown still targets ``celery-worker-flow-*`` deployments left from when
    reuse was enabled. Honours an explicit fleet_key override from the run.
    """
    override = (_fleet_key_override.get() or "").strip()
    if override and is_run_scoped_fleet_key(override):
        return True
    if is_run_scoped_fleet_key(job_name):
        return True
    return flow_fleet_reuse_enabled() and parse_pipeline_run_id(job_name) is not None


def fleet_key_for_job(job_name: str, fleet_key: Optional[str] = None) -> str:
    """Stable identity for Redis/K8s: run-scoped for pipeline jobs when reuse is on.

    An explicit ``fleet_key`` (from the pipeline run metadata) wins over the
    environment flag so Batch 2 of the same run always shares Batch 1's fleet.
    """
    override = (fleet_key or _fleet_key_override.get() or "").strip()
    if override:
        return override
    if is_run_scoped_fleet_key(job_name):
        return (job_name or "").strip()
    run_id = parse_pipeline_run_id(job_name)
    if flow_fleet_reuse_enabled() and run_id is not None:
        return f"pipeline_run_{run_id}"
    return job_name or ""

def fleet_hash(job_name: str) -> str:
    return job_hash(fleet_key_for_job(job_name))


def job_queue_name(job_name: str) -> str:
    return f"migration:job:{job_hash(job_name)}"


def fleet_queue_name(job_name: str) -> str:
    if is_flow_fleet_job(job_name):
        return f"migration:flow:{fleet_hash(job_name)}"
    return job_queue_name(fleet_key_for_job(job_name))


def deployment_name(job_name: str) -> str:
    name = f"celery-worker-job-{job_hash(job_name)}"
    return name[:63].rstrip("-")


def fleet_deployment_name(job_name: str) -> str:
    if is_flow_fleet_job(job_name):
        name = f"celery-worker-flow-{fleet_hash(job_name)}"
        return name[:63].rstrip("-")
    return deployment_name(fleet_key_for_job(job_name))


def flow_fleet_lease_key(pipeline_run_id: int) -> str:
    return f"{FLOW_FLEET_LEASE_PREFIX}{int(pipeline_run_id)}"


def refresh_flow_fleet_lease(r, pipeline_run_id: int, ttl: Optional[int] = None) -> None:
    if r is None or pipeline_run_id is None:
        return
    r.set(
        flow_fleet_lease_key(int(pipeline_run_id)),
        "1",
        ex=int(ttl if ttl is not None else FLOW_FLEET_LEASE_TTL_SECONDS),
    )


def clear_flow_fleet_lease(r, pipeline_run_id: int) -> None:
    if r is None or pipeline_run_id is None:
        return
    r.delete(flow_fleet_lease_key(int(pipeline_run_id)))


def flow_fleet_lease_active(r, pipeline_run_id: int) -> bool:
    if r is None or pipeline_run_id is None:
        return False
    try:
        return bool(r.get(flow_fleet_lease_key(int(pipeline_run_id))))
    except Exception:
        return False


def _meta_key(job_name: str) -> str:
    return f"celery:job-worker:{fleet_hash(job_name)}:meta"


def _identity_key(job_name: str) -> str:
    return fleet_key_for_job(job_name)


def _normalize_plan(plan: Optional[str]) -> str:
    return (plan or "").strip().lower()


def plan_pod_cap(plan: Optional[str], pod_cap: Optional[int] = None) -> int:
    if pod_cap is not None:
        cap = int(pod_cap)
        if cap > 0:
            return max(1, min(cap, CELERY_KEDA_MAX_PODS_PER_JOB))
    normalized = _normalize_plan(plan)
    plan_caps = {
        "free": 2,
        "standard": 2,
        "premium": 4,
        "enterprise": 6,
        "discounted": 2,
    }
    return max(1, min(plan_caps.get(normalized, 2), CELERY_KEDA_MAX_PODS_PER_JOB))


def plan_concurrency(plan: Optional[str], pod_cap: Optional[int] = None) -> int:
    """Tables per pod (Celery --concurrency) from plan tier."""
    normalized = _normalize_plan(plan)
    plan_conc = {
        "free": 3,
        "standard": 3,
        "premium": 4,
        "enterprise": 6,
        "discounted": 3,
    }
    base = plan_conc.get(normalized, CELERY_WORKER_CONCURRENCY)
    return max(1, min(int(base), CELERY_WORKER_CONCURRENCY))


def compute_effective_pods(
    table_count: int,
    plan: Optional[str] = None,
    pod_cap: Optional[int] = None,
) -> int:
    """
    required_pods = ceil(table_count / plan_concurrency)
    effective_pods = max(1, min(required_pods, plan_max_pods))
    """
    tables = max(0, int(table_count))
    if tables == 0:
        return 0
    max_pods = plan_pod_cap(plan, pod_cap=pod_cap)
    conc = plan_concurrency(plan, pod_cap=pod_cap)
    required = math.ceil(tables / conc)
    # Never spawn empty pods: cap by table count as well as plan max.
    return max(1, min(required, max_pods, tables))


def resolve_flow_fleet_provision_replicas(
    effective_pods: int,
    *,
    plan: Optional[str] = None,
    pod_cap: Optional[int] = None,
) -> int:
    """
    Size a run-scoped flow fleet deployment.

    When ``pod_cap`` is set (pipeline budget slice for a node), right-size to
    ``min(table_need, slice_cap)`` instead of always using the full plan max.
    When unset, preserve multi-node fleet reuse: at least the plan max.
    """
    need = max(1, int(effective_pods or 1))
    plan_max = plan_pod_cap(plan)
    if pod_cap is not None:
        slice_cap = max(1, min(plan_max, int(pod_cap)))
        return max(1, min(need, slice_cap))
    return max(need, plan_max)


def compute_effective_concurrency(
    plan: Optional[str] = None,
    pod_cap: Optional[int] = None,
    safe_concurrency: Optional[int] = None,
) -> int:
    safe = safe_concurrency if safe_concurrency is not None else CELERY_WORKER_CONCURRENCY
    return max(1, min(plan_concurrency(plan, pod_cap=pod_cap), safe))


def migration_queue_for_job(job_name: str, default_queue: str) -> str:
    if job_isolation_enabled():
        return fleet_queue_name(job_name)
    return default_queue


def _pipeline_jobs_in_progress(in_progress: set[str], pipeline_run_id: int) -> bool:
    prefix = f"pipeline_run_{int(pipeline_run_id)}"
    for job in in_progress:
        if job == prefix or job.startswith(prefix + "_"):
            return True
    return False


def _meta_still_needed(meta: dict, in_progress: set[str], r=None) -> bool:
    """Whether a registered fleet should survive the stale-worker sweep."""
    kind = (meta.get("kind") or "").strip().lower()
    run_id = meta.get("pipeline_run_id")
    fleet_key = meta.get("fleet_key") or ""
    job_name = meta.get("job_name") or ""
    if kind == "flow" or (fleet_key and str(fleet_key).startswith("pipeline_run_")):
        if run_id is None and fleet_key.startswith("pipeline_run_"):
            try:
                run_id = int(str(fleet_key).split("_", 2)[2])
            except (TypeError, ValueError, IndexError):
                run_id = None
        if run_id is not None:
            if _pipeline_jobs_in_progress(in_progress, int(run_id)):
                return True
            if flow_fleet_lease_active(r, int(run_id)):
                return True
            return False
    if job_name in in_progress:
        return True
    if fleet_key and fleet_key in in_progress:
        return True
    return False


def control_queue_name() -> str:
    return CELERY_MIGRATION_CONTROL_QUEUE


def _k8s_namespace() -> str:
    if CELERY_K8S_NAMESPACE:
        return CELERY_K8S_NAMESPACE
    try:
        with open(_K8S_SA_NAMESPACE_PATH, encoding="utf-8") as fh:
            return fh.read().strip() or "default"
    except OSError:
        return "default"


def k8s_api_request(
    method: str,
    path: str,
    body: Optional[dict] = None,
    timeout: float = 10.0,
) -> Optional[dict]:
    if not CELERY_K8S_POD_STATUS_ENABLED:
        return None
    if not os.path.isfile(_K8S_SA_TOKEN_PATH):
        return None
    try:
        with open(_K8S_SA_TOKEN_PATH, encoding="utf-8") as fh:
            token = fh.read().strip()
        url = f"https://{_K8S_API_HOST}:{_K8S_API_PORT}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.request(
            method.upper(),
            url,
            headers=headers,
            json=body,
            verify=_K8S_SA_CA_PATH if os.path.isfile(_K8S_SA_CA_PATH) else True,
            timeout=timeout,
        )
        if method.upper() == "PATCH" and resp.status_code == 415:
            headers["Content-Type"] = "application/strategic-merge-patch+json"
            resp = requests.request(
                "PATCH",
                url,
                headers=headers,
                json=body,
                verify=_K8S_SA_CA_PATH if os.path.isfile(_K8S_SA_CA_PATH) else True,
                timeout=timeout,
            )
        if resp.status_code == 404:
            return {"_http_status": 404}
        if resp.status_code in (200, 201):
            if resp.content:
                return resp.json()
            return {}
        logger.warning(
            "JOB_WORKER_K8S_HTTP | method=%s | path=%s | status=%s | body=%s",
            method,
            path,
            resp.status_code,
            resp.text[:500],
        )
        return {"_http_status": resp.status_code, "_error": resp.text[:500]}
    except Exception as exc:
        logger.warning("JOB_WORKER_K8S_FAILED | method=%s | path=%s | err=%s", method, path, exc)
        return None


def _pool_name_from_plan(plan_name: Optional[str]) -> str:
    return _PLAN_NODE_POOL.get((plan_name or "").strip().lower(), "worker-standard")


def _pool_name_from_config(config: Optional[dict]) -> str:
    _, _, node_pool = _scheduling_from_config(config)
    if node_pool and getattr(node_pool, "pool_name", None):
        return str(node_pool.pool_name)
    return "worker-standard"


def _meta_node_pool(meta: dict) -> str:
    stored = meta.get("node_pool")
    if stored:
        return str(stored)
    return _pool_name_from_plan(meta.get("plan_name"))


def _sum_active_replicas(r, node_pool: Optional[str] = None) -> int:
    total = 0
    try:
        members = r.smembers(REDIS_ACTIVE_SET_KEY) or []
        for raw_hash in members:
            h = raw_hash.decode() if isinstance(raw_hash, bytes) else str(raw_hash)
            meta_raw = r.get(_meta_key_from_hash(h))
            if not meta_raw:
                continue
            meta = json.loads(meta_raw)
            if node_pool is not None and _meta_node_pool(meta) != node_pool:
                continue
            total += int(meta.get("replicas") or 0)
    except Exception:
        pass
    return total


def _meta_key_from_hash(h: str) -> str:
    return f"celery:job-worker:{h}:meta"


def _update_job_worker_meta(r, job_name: str, state: str, **extra) -> None:
    identity = _identity_key(job_name)
    h = job_hash(identity)
    raw = r.get(_meta_key(job_name))
    meta = json.loads(raw) if raw else {"job_name": identity}
    meta["job_name"] = identity
    meta["state"] = state
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    if is_flow_fleet_job(job_name):
        run_id = parse_pipeline_run_id(job_name)
        meta["kind"] = "flow"
        meta["fleet_key"] = identity
        if run_id is not None:
            meta["pipeline_run_id"] = int(run_id)
        active = list(meta.get("active_jobs") or [])
        if job_name and job_name not in active and job_name != identity:
            active.append(job_name)
            meta["active_jobs"] = active
    meta.update(extra)
    r.set(_meta_key(job_name), json.dumps(meta), ex=86400 * 2)
    if state in (META_STATE_READY, META_STATE_PROVISIONING):
        r.sadd(REDIS_ACTIVE_SET_KEY, h)


def _register_job_worker(
    r,
    job_name: str,
    replicas: int,
    queue: str,
    dep_name: str,
    *,
    state: str = META_STATE_PROVISIONING,
    company_id: Optional[int] = None,
    plan_name: Optional[str] = None,
    node_pool: Optional[str] = None,
    source_job_name: Optional[str] = None,
) -> None:
    identity = _identity_key(job_name)
    h = job_hash(identity)
    existing_raw = r.get(_meta_key(job_name))
    existing = json.loads(existing_raw) if existing_raw else {}
    active_jobs = list(existing.get("active_jobs") or [])
    lease_job = source_job_name or job_name
    if lease_job and lease_job not in active_jobs and lease_job != identity:
        active_jobs.append(lease_job)
    meta = {
        "job_name": identity,
        "queue": queue,
        "replicas": replicas,
        "deployment_name": dep_name,
        "state": state,
        "company_id": company_id,
        "plan_name": plan_name,
        "node_pool": node_pool or _pool_name_from_plan(plan_name),
        "created_at": existing.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "active_jobs": active_jobs,
    }
    if is_flow_fleet_job(job_name):
        run_id = parse_pipeline_run_id(job_name)
        meta["kind"] = "flow"
        meta["fleet_key"] = identity
        if run_id is not None:
            meta["pipeline_run_id"] = int(run_id)
            refresh_flow_fleet_lease(r, int(run_id))
    r.set(_meta_key(job_name), json.dumps(meta), ex=86400 * 2)
    r.sadd(REDIS_ACTIVE_SET_KEY, h)


def _provision_queue_score(priority: int) -> float:
    return float(priority) * 1_000_000_000_000 + (2_000_000_000_000 - time.time())


def _rate_bucket_allowed(r, key: str, limit: int) -> bool:
    try:
        count = r.incr(key)
        if count == 1:
            r.expire(key, 60)
        return int(count) <= int(limit)
    except Exception:
        return True


def _provision_rate_reason(r, company_id: Optional[int] = None) -> Optional[str]:
    """Return a defer reason when a rate bucket is exhausted, else None.

    A company bucket only defers that company. The cluster bucket defers everyone.
    """
    if not _rate_bucket_allowed(r, PROVISION_RATE_KEY, CELERY_PROVISION_RATE_PER_MINUTE):
        return "rate_limit"
    if company_id is None:
        return None
    company_key = f"{PROVISION_RATE_KEY}:company:{int(company_id)}"
    if not _rate_bucket_allowed(r, company_key, CELERY_COMPANY_PROVISION_RATE_PER_MINUTE):
        return "company_rate_limit"
    return None


def _deployment_http_status(
    job_name: str,
    k8s_request: Callable = k8s_api_request,
) -> Optional[int]:
    """Return 404 when the fleet Deployment is gone, 200 when it exists, None if unknown."""
    try:
        body = k8s_request(
            "GET",
            f"/apis/apps/v1/namespaces/{_k8s_namespace()}/deployments/{fleet_deployment_name(job_name)}",
        )
    except Exception:
        return None
    if not body:
        return None
    status = body.get("_http_status")
    if status is None:
        return 200
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def drop_registry_if_deployment_missing(
    r,
    job_name: str,
    k8s_request: Callable = k8s_api_request,
) -> bool:
    """
    Drop a Redis fleet registration whose Deployment no longer exists.

    A deleted Deployment used to keep its lease and stay in the active set.
    Free-plan organisations allow one concurrent job, so that ghost blocked
    every later migration until the lease TTL expired.
    """
    if _deployment_http_status(job_name, k8s_request) != 404:
        return False
    identity = _identity_key(job_name)
    _unregister_job_worker(r, job_name)
    logger.warning(
        "JOB_WORKER_GHOST_REGISTRY_DROPPED | job=%s | fleet=%s",
        job_name,
        identity,
    )
    return True


def _active_company_replicas(r, company_id: int) -> dict[str, int]:
    """Map fleet identity -> replica count for one organisation."""
    usage: dict[str, int] = {}
    try:
        members = r.smembers(REDIS_ACTIVE_SET_KEY) or []
    except Exception:
        return usage
    for raw_hash in members:
        h = raw_hash.decode() if isinstance(raw_hash, bytes) else str(raw_hash)
        meta_raw = r.get(_meta_key_from_hash(h))
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw)
        except (TypeError, ValueError):
            continue
        raw_company = meta.get("company_id")
        if raw_company is None:
            continue
        try:
            if int(raw_company) != int(company_id):
                continue
        except (TypeError, ValueError):
            continue
        identity = str(meta.get("job_name") or h)
        usage[identity] = int(meta.get("replicas") or 0)
    return usage


def _company_usage_job_key(identity: str) -> str:
    """Collapse all fleets of one pipeline run into a single org job slot."""
    run_id = parse_pipeline_run_id(identity)
    if run_id is not None:
        return f"pipeline_run_{run_id}"
    return identity or ""


def company_provision_defer_reason(
    r,
    job_name: str,
    replicas: int,
    resource_config: Optional["EffectiveResourceConfig"] = None,
    k8s_request: Callable = k8s_api_request,
) -> Optional[str]:
    """Defer only this organisation when it is already at its plan allowance.

    Other organisations are not affected. Returns None when this job may provision.

    All Redis/K8s registrations for the same ``pipeline_run_<id>`` count as one
    concurrent job, so Batch 2 of a Free-plan flow never hits ``company_job_cap``
    against Batch 1 of the same run — even when fleet reuse is off and each
    batch has a node-scoped identity.
    """
    if resource_config is None or getattr(resource_config, "company_id", None) is None:
        return None
    try:
        company_id = int(resource_config.company_id)
    except (TypeError, ValueError):
        return None
    max_jobs = max(1, int(getattr(resource_config, "max_concurrent_jobs", 1) or 1))
    max_pods = max(1, int(getattr(resource_config, "max_pods", 1) or 1))
    pod_ceiling = max_pods * max_jobs
    usage = _active_company_replicas(r, company_id)
    usage = {
        identity: replicas_held
        for identity, replicas_held in usage.items()
        if not drop_registry_if_deployment_missing(r, identity, k8s_request)
    }
    identity = _identity_key(job_name)
    request_job_key = _company_usage_job_key(identity)
    jobs: dict[str, int] = {}
    for held_identity, replicas_held in usage.items():
        key = _company_usage_job_key(held_identity)
        jobs[key] = jobs.get(key, 0) + int(replicas_held or 0)

    already_same_run = request_job_key in jobs
    if not already_same_run and len(jobs) >= max_jobs:
        return "company_job_cap"

    requested = max(1, int(replicas or 1))
    if identity in usage:
        # Scaling an existing fleet identity — replace that identity's count.
        existing_identity = int(usage.get(identity) or 0)
        new_total = sum(usage.values()) - existing_identity + requested
    elif already_same_run:
        # Another batch of the same run — add pods under the shared job slot.
        new_total = sum(usage.values()) + requested
    else:
        new_total = sum(usage.values()) + requested
    if new_total > pod_ceiling:
        return "company_pod_cap"
    return None


def _defer_provision(
    r,
    identity: str,
    *,
    replicas: int,
    reason: str,
    resource_config: Optional["EffectiveResourceConfig"],
    worker_concurrency,
    serialized_config,
    source_job_name: str,
    fleet_key: Optional[str] = None,
) -> dict:
    payload = {
        "replicas": replicas,
        "priority": resource_config.priority if resource_config else 50,
        "reason": reason,
        "resource_config": resource_config.to_dict() if resource_config else None,
        "worker_concurrency": worker_concurrency,
        "config": serialized_config,
        "source_job_name": source_job_name,
    }
    if fleet_key:
        payload["fleet_key"] = fleet_key
    enqueue_deferred_provision(r, identity, payload)
    return {"status": "deferred", "reason": reason}


def enqueue_deferred_provision(r, job_name: str, payload: dict) -> None:
    priority = int(payload.get("priority") or 50)
    r.zadd(PROVISION_QUEUE_KEY, {job_name: _provision_queue_score(priority)})
    r.set(f"{PROVISION_PAYLOAD_PREFIX}{job_name}", json.dumps(payload), ex=86400)
    _update_job_worker_meta(r, job_name, META_STATE_PENDING, replicas=payload.get("replicas"))
    reason = str(payload.get("reason", "cluster_cap"))
    job_metrics.inc_provision_deferred(reason)
    logger.warning(
        "PROVISION_DEFERRED | job=%s | replicas=%s | priority=%s | reason=%s",
        job_name,
        payload.get("replicas"),
        priority,
        reason,
    )


def save_awaiting_fanout_state(
    r,
    job_name: str,
    *,
    child_ids: list,
    max_inflight: int,
    use_staggered: bool,
    connection_id,
    refresh_waiting_since: bool = False,
) -> None:
    existing = load_awaiting_fanout_state(r, job_name)
    waiting_since = (existing or {}).get("waiting_since")
    if refresh_waiting_since or not waiting_since:
        waiting_since = datetime.now(timezone.utc).isoformat()
    r.set(
        f"{AWAITING_PROVISION_PREFIX}{job_name}",
        json.dumps({
            "child_ids": child_ids,
            "max_inflight": max_inflight,
            "use_staggered": use_staggered,
            "connection_id": connection_id,
            "waiting_since": waiting_since,
        }),
        ex=86400,
    )
    r.sadd(AWAITING_PROVISION_SET, job_name)


def load_awaiting_fanout_state(r, job_name: str) -> Optional[dict]:
    try:
        raw = r.get(f"{AWAITING_PROVISION_PREFIX}{job_name}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def clear_awaiting_fanout_state(r, job_name: str) -> None:
    r.delete(f"{AWAITING_PROVISION_PREFIX}{job_name}")
    r.srem(AWAITING_PROVISION_SET, job_name)
    r.zrem(PROVISION_QUEUE_KEY, job_name)
    r.delete(f"{PROVISION_PAYLOAD_PREFIX}{job_name}")


def list_awaiting_fanout_jobs(r) -> list:
    try:
        members = r.smembers(AWAITING_PROVISION_SET) or []
        return [m.decode() if isinstance(m, bytes) else str(m) for m in members]
    except Exception:
        return []


def _unregister_job_worker(r, job_name: str) -> None:
    identity = _identity_key(job_name)
    h = job_hash(identity)
    _update_job_worker_meta(r, job_name, META_STATE_DELETED)
    r.delete(_meta_key(job_name))
    r.srem(REDIS_ACTIVE_SET_KEY, h)
    clear_awaiting_fanout_state(r, job_name)
    clear_awaiting_fanout_state(r, identity)
    run_id = parse_pipeline_run_id(job_name)
    if run_id is not None:
        clear_flow_fleet_lease(r, run_id)


def _worker_image() -> str:
    return os.getenv(
        "CELERY_JOB_WORKER_IMAGE",
        os.getenv(
            "CELERY_WORKER_IMAGE",
            "gcr.io/datasyncher-kubernetes/datasyncher-fast-api:qa",
        ),
    )


def _image_pull_policy(image: str) -> str:
    """Digest-pinned images are immutable, so a warm node can skip the registry check."""
    if "@sha256:" in (image or ""):
        return "IfNotPresent"
    return "Always"


def _serialize_execution_config(config: Optional[dict]) -> Optional[dict]:
    if not config:
        return None
    out = dict(config)
    node_pool = out.get("node_pool")
    if node_pool is not None and hasattr(node_pool, "to_dict"):
        out["node_pool"] = node_pool.to_dict()
    return out


def _execution_config_from_payload(payload: Optional[dict]) -> Optional[dict]:
    if not payload:
        return None
    from celery_resource_config import NodePoolConfig

    out = dict(payload)
    node_pool = out.get("node_pool")
    if isinstance(node_pool, dict):
        out["node_pool"] = NodePoolConfig.from_dict(node_pool)
    return out


def _scheduling_from_config(config: Optional[dict]) -> tuple[dict, list, Optional[Any]]:
    node_pool = None
    if config:
        node_pool = config.get("node_pool")
        if isinstance(node_pool, dict):
            from celery_resource_config import NodePoolConfig
            node_pool = NodePoolConfig.from_dict(node_pool)

    if node_pool:
        node_selector = dict(node_pool.node_selector or {})
        tolerations = []
        if node_pool.taint_key and node_pool.taint_value:
            tolerations = [{
                "key": node_pool.taint_key,
                "operator": "Equal",
                "value": node_pool.taint_value,
                "effect": node_pool.taint_effect or "NoSchedule",
            }]
        return node_selector, tolerations, node_pool

    logger.warning(
        "NODE_POOL_FALLBACK | using default nodeSelector pool=worker | no node_pool resolved",
    )
    return dict(WORKER_POOL_NODE_SELECTOR), [], None


def _build_deployment_spec(
    job_name: str,
    replicas: int,
    queue: str,
    worker_concurrency: int,
    resource_config: Optional["EffectiveResourceConfig"] = None,
    config: Optional[dict] = None,
) -> dict:
    identity = _identity_key(job_name)
    is_flow = is_flow_fleet_job(job_name)
    h = job_hash(identity)
    dep = fleet_deployment_name(job_name)
    namespace = _k8s_namespace()
    app_label = FLOW_WORKER_LABEL_APP if is_flow else JOB_WORKER_LABEL_APP
    labels = {
        "app": app_label,
        JOB_WORKER_LABEL_HASH: h,
        JOB_WORKER_LABEL_MANAGED: JOB_WORKER_LABEL_MANAGED_BY,
    }
    if is_flow:
        run_id = parse_pipeline_run_id(job_name)
        labels[JOB_WORKER_LABEL_FLEET] = "flow"
        if run_id is not None:
            labels[JOB_WORKER_LABEL_PIPELINE_RUN] = str(run_id)
    if resource_config is not None:
        if resource_config.company_id is not None:
            labels[JOB_WORKER_LABEL_COMPANY] = str(resource_config.company_id)
        if resource_config.plan_name:
            labels[JOB_WORKER_LABEL_PLAN] = resource_config.plan_name
    node_selector, tolerations, node_pool = _scheduling_from_config(config)
    labels[JOB_WORKER_LABEL_POOL] = node_pool.pool_name if node_pool else "default"
    env_from_worker = [
        ("CELERY_JOB_ISOLATION_MODE", "per_job"),
        ("CELERY_WORKER_ROLE", "job"),
        ("CELERY_MIGRATION_QUEUE", queue),
        ("CELERY_WORKER_CONCURRENCY", str(worker_concurrency)),
        ("CELERY_JOB_NAME", identity),
        ("CELERY_JOB_HASH", h),
    ]
    if is_flow:
        run_id = parse_pipeline_run_id(job_name)
        env_from_worker.append(("CELERY_FLOW_FLEET", "1"))
        if run_id is not None:
            env_from_worker.append(("CELERY_PIPELINE_RUN_ID", str(run_id)))
    mem_lim_env = (
        config.get("memory_limit")
        if config else (resource_config.memory_limit if resource_config else "4Gi")
    )
    chunk_env = (
        str(config.get("chunk_size", 25000))
        if config else (str(resource_config.max_chunk_size) if resource_config else "25000")
    )
    if resource_config is not None or config is not None:
        env_from_worker.extend([
            ("CELERY_POD_MEMORY_LIMIT", mem_lim_env),
            ("CELERY_PLAN_MAX_CHUNK_SIZE", chunk_env),
        ])
    if resource_config is not None:
        env_from_worker.append(("CELERY_PLAN_NAME", resource_config.plan_name))
    env_keys = (
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "DATABASE_URL",
        "FASTAPI_DATABASE_URL",
        "DJANGO_INTERNAL_BASE_URL",
        "INTERNAL_API_KEY",
        "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS",
        "CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS",
        "CELERY_STARTED_CHILD_REQUEUE_ENABLED",
        "CELERY_STARTED_CHILD_REQUEUE_AFTER_SECONDS",
        "CELERY_STARTED_CHILD_HARD_FAIL_AFTER_SECONDS",
        "CELERY_STARTED_HARD_FAIL_REQUIRE_DEAD_POD",
        "CELERY_ORPHAN_AUTO_REQUEUE_ENABLED",
        "CELERY_WORKER_HEARTBEAT_TTL_SECONDS",
        "CELERY_WORKER_HEARTBEAT_REFRESH_INTERVAL_SECONDS",
        "GCS_BUCKET_NAME",
        "GCS_BUCKET_URL",
        "GCP_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        # Databricks Unity Catalog external location auto-creation (COPY INTO from GCS)
        "DATABRICKS_GCS_SA_JSON",
        "DATABRICKS_GCS_CREDENTIAL_NAME",
        "DATABRICKS_GCS_LOCATION_NAME",
        "DATABRICKS_GCS_GRANT_PRINCIPAL",
    )
    base_env = [{"name": k, "value": os.getenv(k)} for k in env_keys if os.getenv(k) is not None]
    base_env.extend([
        {
            "name": "CELERY_TASK_TIME_LIMIT_SECONDS",
            "value": os.getenv("CELERY_TASK_TIME_LIMIT_SECONDS", "0"),
        },
        {
            "name": "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS",
            "value": os.getenv("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", "0"),
        },
        # Skip google-auth's metadata universe_domain lookup on GKE WI nodes
        # that are still booting gke-metadata-server.
        {
            "name": "GOOGLE_CLOUD_UNIVERSE_DOMAIN",
            "value": os.getenv("GOOGLE_CLOUD_UNIVERSE_DOMAIN", "googleapis.com"),
        },
    ])
    if not os.getenv("CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS"):
        base_env.append({
            "name": "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS",
            "value": os.getenv("CELERY_BROKER_VISIBILITY_TIMEOUT_DEFAULT", "2592000"),
        })
    if not os.getenv("CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS"):
        base_env.append({
            "name": "CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS",
            "value": os.getenv("CELERY_MIGRATION_HTTP_TIMEOUT_DEFAULT", "133200"),
        })
    base_env.extend({"name": k, "value": v} for k, v in env_from_worker)
    # Ensure per-job flow workers can upload chunk CSVs to GCS without relying on
    # GCE metadata alone (metadata can fail on cold worker-premium nodes).
    if not any(entry.get("name") == "DATABRICKS_GCS_SA_JSON" for entry in base_env):
        base_env.append(
            {
                "name": "DATABRICKS_GCS_SA_JSON",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": os.getenv(
                            "DATABRICKS_GCS_SA_SECRET",
                            "databricks-gcs-sa-json",
                        ),
                        "key": os.getenv(
                            "DATABRICKS_GCS_SA_SECRET_KEY",
                            "sa-key.json",
                        ),
                        "optional": True,
                    }
                },
            }
        )
    if config:
        cpu_req = config.get("cpu_request", "1500m")
        cpu_lim = config.get("cpu_limit", "2")
        mem_req = config.get("memory_request", "4Gi")
        mem_lim = config.get("memory_limit", "6Gi")
    else:
        cpu_req = resource_config.cpu_request if resource_config else "500m"
        cpu_lim = resource_config.cpu_limit if resource_config else "2"
        mem_req = resource_config.memory_request if resource_config else "1Gi"
        mem_lim = resource_config.memory_limit if resource_config else "4Gi"
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": dep,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": app_label, JOB_WORKER_LABEL_HASH: h}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": os.getenv("CELERY_JOB_WORKER_SERVICE_ACCOUNT", "celery-gcs"),
                    "nodeSelector": node_selector,
                    "tolerations": tolerations,
                    "terminationGracePeriodSeconds": 120,
                    "containers": [
                        {
                            "name": "celery-worker",
                            "image": _worker_image(),
                            "imagePullPolicy": _image_pull_policy(_worker_image()),
                            "command": [
                                "celery",
                                "-A",
                                "celery_worker.celery_app",
                                "worker",
                                "--loglevel=info",
                                f"--concurrency={worker_concurrency}",
                                "-Q",
                                queue,
                                "--prefetch-multiplier=1",
                                "--max-tasks-per-child=50",
                            ],
                            "env": base_env,
                            "envFrom": [
                                {
                                    "secretRef": {
                                        "name": os.getenv(
                                            "CELERY_JOB_WORKER_SECRET",
                                            "datasyncher-secrets",
                                        ),
                                    },
                                },
                            ],
                            "resources": {
                                "requests": {"cpu": cpu_req, "memory": mem_req},
                                "limits": {"cpu": cpu_lim, "memory": mem_lim},
                            },
                        }
                    ],
                },
            },
        },
    }


def provision_job_workers(
    job_name: str,
    replicas: int,
    concurrency: Optional[int] = None,
    *,
    config: Optional[dict] = None,
    r=None,
    plan: Optional[str] = None,
    pod_cap: Optional[int] = None,
    resource_config: Optional["EffectiveResourceConfig"] = None,
    worker_concurrency: Optional[int] = None,
    db=None,
    k8s_request: Callable = k8s_api_request,
    fleet_key: Optional[str] = None,
) -> dict:
    """
    Create or patch a per-job (or run-scoped flow) worker Deployment. Returns status dict.

    ``fleet_key`` (from Django ``flow_pod_state``) forces a shared run fleet so
    parallel batches of one flow use one org job slot and the plan pod budget.
    """
    if not job_isolation_enabled():
        return {"status": "skipped", "reason": "not_per_job_mode"}

    if r is None:
        from celery_worker import _get_redis
        r = _get_redis()

    override = (fleet_key or "").strip() or None
    token = _fleet_key_override.set(override) if override else None
    try:
        return _provision_job_workers_inner(
            job_name,
            replicas,
            concurrency,
            config=config,
            r=r,
            plan=plan,
            pod_cap=pod_cap,
            resource_config=resource_config,
            worker_concurrency=worker_concurrency,
            db=db,
            k8s_request=k8s_request,
            fleet_key=override,
        )
    finally:
        if token is not None:
            _fleet_key_override.reset(token)


def _provision_job_workers_inner(
    job_name: str,
    replicas: int,
    concurrency: Optional[int] = None,
    *,
    config: Optional[dict] = None,
    r=None,
    plan: Optional[str] = None,
    pod_cap: Optional[int] = None,
    resource_config: Optional["EffectiveResourceConfig"] = None,
    worker_concurrency: Optional[int] = None,
    db=None,
    k8s_request: Callable = k8s_api_request,
    fleet_key: Optional[str] = None,
) -> dict:
    replicas = max(1, int(replicas))
    conc_param = concurrency if concurrency is not None else worker_concurrency
    identity = fleet_key_for_job(job_name, fleet_key=fleet_key)

    serialized_config = _serialize_execution_config(config)

    existing_meta_raw = r.get(_meta_key(job_name))
    existing_meta = json.loads(existing_meta_raw) if existing_meta_raw else {}
    existing_registered_replicas = int(existing_meta.get("replicas") or 0)

    company_reason = company_provision_defer_reason(
        r, job_name, replicas, resource_config, k8s_request=k8s_request,
    )
    if company_reason:
        return _defer_provision(
            r,
            identity,
            replicas=replicas,
            reason=company_reason,
            resource_config=resource_config,
            worker_concurrency=conc_param,
            serialized_config=serialized_config,
            source_job_name=job_name,
            fleet_key=fleet_key,
        )

    company_id = getattr(resource_config, "company_id", None) if resource_config else None
    rate_reason = _provision_rate_reason(r, company_id)
    if rate_reason:
        return _defer_provision(
            r,
            identity,
            replicas=replicas,
            reason=rate_reason,
            resource_config=resource_config,
            worker_concurrency=conc_param,
            serialized_config=serialized_config,
            source_job_name=job_name,
            fleet_key=fleet_key,
        )

    pool_name = _pool_name_from_config(config)
    pool_sum = _sum_active_replicas(r, pool_name)
    cluster_sum = _sum_active_replicas(r)
    delta = max(0, replicas - existing_registered_replicas)
    over_pool = pool_sum + delta > _pool_pod_cap(pool_name)
    over_cluster = cluster_sum + delta > CELERY_CLUSTER_MAX_JOB_WORKER_PODS
    if over_pool or over_cluster:
        return _defer_provision(
            r,
            identity,
            replicas=replicas,
            reason="cluster_cap",
            resource_config=resource_config,
            worker_concurrency=conc_param,
            serialized_config=serialized_config,
            source_job_name=job_name,
            fleet_key=fleet_key,
        )

    job_metrics.mark_provision_started(identity)

    queue = fleet_queue_name(job_name)
    dep = fleet_deployment_name(job_name)
    if resource_config is not None:
        conc = conc_param if conc_param is not None else resource_config.effective_concurrency
    else:
        conc = conc_param if conc_param is not None else compute_effective_concurrency(plan, pod_cap=pod_cap)
    namespace = _k8s_namespace()
    spec = _build_deployment_spec(
        job_name, replicas, queue, conc,
        resource_config=resource_config,
        config=config,
    )
    node_selector, _, node_pool = _scheduling_from_config(config)
    mem_req = (
        config.get("memory_request", "4Gi") if config
        else (resource_config.memory_request if resource_config else "1Gi")
    )

    def _patch_existing_deployment(existing_body: dict) -> tuple[Optional[dict], int]:
        """Reuse an existing fleet deployment (stable replica count, never shrink mid-run)."""
        nonlocal replicas
        existing_replicas = int(
            ((existing_body.get("spec") or {}).get("replicas"))
            or ((existing_body.get("status") or {}).get("replicas"))
            or 0
        )
        stable_replicas = max(replicas, existing_replicas) if existing_replicas > 0 else replicas
        if stable_replicas != replicas:
            logger.warning(
                "JOB_WORKER_STABLE_FLEET_KEEP | job=%s | fleet=%s | requested=%s | existing=%s | using=%s",
                job_name,
                identity,
                replicas,
                existing_replicas,
                stable_replicas,
            )
            replicas = stable_replicas
        patch_body = {
            "spec": {
                "replicas": replicas,
                "template": spec["spec"]["template"],
            }
        }
        patch_result = k8s_request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{dep}",
            body=patch_body,
        )
        return patch_result, replicas

    existing = k8s_request(
        "GET",
        f"/apis/apps/v1/namespaces/{namespace}/deployments/{dep}",
    )
    if existing and existing.get("_http_status") != 404:
        result, replicas = _patch_existing_deployment(existing)
        action = "patched"
    else:
        result = k8s_request(
            "POST",
            f"/apis/apps/v1/namespaces/{namespace}/deployments",
            body=spec,
        )
        action = "created"
        # Parallel pipeline nodes can both GET 404 then race POST; treat 409 as reuse.
        if result is not None and result.get("_http_status") == 409:
            logger.warning(
                "JOB_WORKER_PROVISION_ALREADY_EXISTS | job=%s | fleet=%s | deployment=%s | retry=patch",
                job_name,
                identity,
                dep,
            )
            existing = k8s_request(
                "GET",
                f"/apis/apps/v1/namespaces/{namespace}/deployments/{dep}",
            )
            if existing and existing.get("_http_status") != 404:
                result, replicas = _patch_existing_deployment(existing)
                action = "patched_after_409"
            else:
                return {
                    "status": "failed",
                    "action": action,
                    "deployment": dep,
                    "k8s": result,
                    "reason": "already_exists_but_get_failed",
                }

    if result is None or result.get("_http_status", 200) >= 400:
        return {"status": "failed", "action": action, "deployment": dep, "k8s": result}

    company_id = resource_config.company_id if resource_config else None
    plan_name = resource_config.plan_name if resource_config else None
    _register_job_worker(
        r,
        job_name,
        replicas,
        queue,
        dep,
        state=META_STATE_PROVISIONING,
        company_id=company_id,
        plan_name=plan_name,
        node_pool=pool_name,
        source_job_name=job_name,
    )
    job_metrics.set_job_replicas(
        company_id=company_id,
        plan=plan_name,
        job_hash=fleet_hash(job_name),
        replicas=replicas,
    )
    logger.info(
        "JOB_WORKER_PROVISIONED | job=%s | fleet=%s | hash=%s | replicas=%s | concurrency=%s | "
        "pool=%s | machine=%s | memory_request=%s | node_selector=%s | queue=%s",
        job_name,
        identity,
        fleet_hash(job_name),
        replicas,
        conc,
        node_pool.pool_name if node_pool else "default",
        node_pool.machine_type if node_pool else "unknown",
        mem_req,
        node_selector,
        queue,
    )
    return {
        "status": "ok",
        "action": action,
        "deployment": dep,
        "replicas": replicas,
        "queue": queue,
        "fleet_key": identity,
    }


def job_worker_ready_now(
    job_name: str,
    *,
    r=None,
    k8s_request: Callable = k8s_api_request,
) -> bool:
    """Non-blocking readiness check (single K8s GET)."""
    if not job_isolation_enabled():
        return True

    dep = fleet_deployment_name(job_name)
    namespace = _k8s_namespace()
    body = k8s_request(
        "GET",
        f"/apis/apps/v1/namespaces/{namespace}/deployments/{dep}",
    )
    if not body or body.get("_http_status") == 404:
        return False
    status = body.get("status") or {}
    ready = int(status.get("readyReplicas") or 0)
    if ready < 1:
        return False

    plan_name = None
    company_id = None
    if r is not None:
        _update_job_worker_meta(r, job_name, META_STATE_READY, ready_replicas=ready)
        meta_raw = r.get(_meta_key(job_name))
        if meta_raw:
            meta = json.loads(meta_raw)
            plan_name = meta.get("plan_name")
            company_id = meta.get("company_id")
        run_id = parse_pipeline_run_id(job_name)
        if run_id is not None:
            refresh_flow_fleet_lease(r, run_id)
    job_metrics.observe_provision_ready(_identity_key(job_name), plan=plan_name)
    job_metrics.set_job_replicas(
        company_id=company_id,
        plan=plan_name,
        job_hash=fleet_hash(job_name),
        replicas=ready,
    )
    logger.warning(
        "JOB_WORKER_READY | job=%s | fleet=%s | deployment=%s | ready=%s",
        job_name,
        _identity_key(job_name),
        dep,
        ready,
    )
    return True


def ensure_job_worker_ready(
    job_name: str,
    *,
    timeout: Optional[int] = None,
    r=None,
    k8s_request: Callable = k8s_api_request,
) -> bool:
    """Wait until readyReplicas >= 1. On timeout, teardown partial deployment."""
    if not job_isolation_enabled():
        return True

    timeout = timeout if timeout is not None else CELERY_JOB_WORKER_READY_TIMEOUT
    unlimited = timeout <= 0
    deadline = None if unlimited else time.monotonic() + timeout
    while unlimited or time.monotonic() < deadline:
        if job_worker_ready_now(job_name, r=r, k8s_request=k8s_request):
            return True
        time.sleep(5)

    plan_name = None
    if r is not None:
        meta_raw = r.get(_meta_key(job_name))
        if meta_raw:
            plan_name = json.loads(meta_raw).get("plan_name")
    job_metrics.inc_ready_timeout(plan_name)
    logger.error(
        "JOB_WORKER_READY_TIMEOUT | job=%s | deployment=%s | timeout=%s",
        job_name,
        fleet_deployment_name(job_name),
        timeout,
    )
    # Only hard-delete on timeout for non-flow fleets; flow fleets release lease.
    if is_flow_fleet_job(job_name):
        release_flow_fleet_lease(job_name, r=r)
    else:
        teardown_job_workers(job_name, fast=True, r=r, k8s_request=k8s_request)
    return False


def release_flow_fleet_lease(
    job_name: str,
    *,
    r=None,
) -> dict:
    """Drop a node job from the flow fleet active set without deleting the Deployment."""
    if r is None:
        from celery_worker import _get_redis
        r = _get_redis()

    if not is_flow_fleet_job(job_name):
        return {"status": "skipped", "reason": "not_flow_fleet"}

    identity = _identity_key(job_name)
    meta_raw = r.get(_meta_key(job_name))
    if not meta_raw:
        clear_awaiting_fanout_state(r, job_name)
        return {"status": "ok", "fleet_key": identity, "active_jobs": []}

    meta = json.loads(meta_raw)
    active = [j for j in (meta.get("active_jobs") or []) if j != job_name]
    meta["active_jobs"] = active
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    r.set(_meta_key(job_name), json.dumps(meta), ex=86400 * 2)
    clear_awaiting_fanout_state(r, job_name)
    run_id = parse_pipeline_run_id(job_name)
    if run_id is not None:
        refresh_flow_fleet_lease(r, run_id)
    logger.info(
        "FLOW_FLEET_LEASE_RELEASED | job=%s | fleet=%s | remaining=%s",
        job_name,
        identity,
        len(active),
    )
    return {"status": "ok", "fleet_key": identity, "active_jobs": active}


def teardown_flow_fleet(
    pipeline_run_id: int,
    *,
    fast: bool = True,
    r=None,
    k8s_request: Callable = k8s_api_request,
) -> dict:
    """Delete the run-scoped flow fleet Deployment."""
    fleet_key = f"pipeline_run_{int(pipeline_run_id)}"
    if r is None:
        from celery_worker import _get_redis
        r = _get_redis()
    clear_flow_fleet_lease(r, int(pipeline_run_id))
    return teardown_job_workers(fleet_key, fast=fast, r=r, k8s_request=k8s_request)


def teardown_job_workers(
    job_name: str,
    *,
    fast: bool = True,
    r=None,
    k8s_request: Callable = k8s_api_request,
) -> dict:
    """Scale to zero and delete Deployment; clear Redis registry."""
    if r is None:
        from celery_worker import _get_redis
        r = _get_redis()

    identity = _identity_key(job_name)
    dep = fleet_deployment_name(job_name)
    namespace = _k8s_namespace()
    h = fleet_hash(job_name)
    meta_raw = r.get(_meta_key(job_name))
    meta = json.loads(meta_raw) if meta_raw else {}
    _update_job_worker_meta(r, job_name, META_STATE_DRAINING)

    patch_result = k8s_request(
        "PATCH",
        f"/apis/apps/v1/namespaces/{namespace}/deployments/{dep}",
        body={"spec": {"replicas": 0}},
    )
    if fast:
        time.sleep(min(CELERY_JOB_WORKER_TEARDOWN_GRACE_SECONDS, 5))
    delete_result = k8s_request(
        "DELETE",
        f"/apis/apps/v1/namespaces/{namespace}/deployments/{dep}",
    )
    _unregister_job_worker(r, job_name)
    delete_status = (delete_result or {}).get("_http_status")
    teardown_ok = delete_status in (None, 200, 202, 404)
    job_metrics.inc_teardown(teardown_ok)
    job_metrics.clear_job_replicas(
        company_id=meta.get("company_id"),
        plan=meta.get("plan_name"),
        job_hash=h,
        job_name=identity,
    )
    logger.warning(
        "JOB_WORKER_TEARDOWN | job=%s | fleet=%s | deployment=%s | fast=%s",
        job_name,
        identity,
        dep,
        fast,
    )
    promoted = process_provision_queue(r, k8s_request=k8s_request, max_promote=1)
    return {
        "status": "ok",
        "deployment": dep,
        "patch": patch_result,
        "delete": delete_result,
        "promoted": promoted,
    }


def process_provision_queue(
    r,
    *,
    db=None,
    k8s_request: Callable = k8s_api_request,
    max_promote: int = 1,
) -> int:
    """Promote deferred provisions when cluster/rate limits allow (RFC §9.2).

    A company that is already at its own plan cap is skipped so the next
    organisation in the queue can still be promoted.
    """
    if not job_isolation_enabled():
        return 0
    promoted = 0
    scan = max(int(max_promote or 1), 25)
    try:
        candidates = r.zrevrange(PROVISION_QUEUE_KEY, 0, scan - 1)
    except Exception:
        return 0
    for raw_name in candidates or []:
        if promoted >= max_promote:
            break
        job_name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
        payload_raw = r.get(f"{PROVISION_PAYLOAD_PREFIX}{job_name}")
        if not payload_raw:
            r.zrem(PROVISION_QUEUE_KEY, job_name)
            continue
        payload = json.loads(payload_raw)
        cfg_dict = payload.get("resource_config")
        resource_config = None
        if cfg_dict:
            from celery_resource_config import config_from_dict
            resource_config = config_from_dict(cfg_dict)
        execution_config = _execution_config_from_payload(payload.get("config"))
        provision_name = payload.get("source_job_name") or job_name
        payload_fleet_key = payload.get("fleet_key")
        token = None
        if payload_fleet_key:
            token = _fleet_key_override.set(str(payload_fleet_key).strip())
        try:
            company_reason = company_provision_defer_reason(
                r,
                provision_name,
                int(payload.get("replicas") or 1),
                resource_config,
            )
        finally:
            if token is not None:
                _fleet_key_override.reset(token)
        if company_reason:
            logger.info(
                "PROVISION_SKIP_COMPANY_CAP | job=%s | reason=%s",
                provision_name,
                company_reason,
            )
            continue
        result = provision_job_workers(
            provision_name,
            int(payload.get("replicas") or 1),
            r=r,
            resource_config=resource_config,
            worker_concurrency=payload.get("worker_concurrency"),
            config=execution_config,
            k8s_request=k8s_request,
            fleet_key=payload_fleet_key,
        )
        if result.get("status") == "ok":
            if job_worker_ready_now(provision_name, r=r, k8s_request=k8s_request):
                r.zrem(PROVISION_QUEUE_KEY, job_name)
                r.delete(f"{PROVISION_PAYLOAD_PREFIX}{job_name}")
                promoted += 1
                logger.warning("PROVISION_PROMOTED | job=%s", provision_name)
        elif result.get("reason") in COMPANY_DEFER_REASONS:
            continue
        elif result.get("status") != "deferred":
            r.zrem(PROVISION_QUEUE_KEY, job_name)
            r.delete(f"{PROVISION_PAYLOAD_PREFIX}{job_name}")
        else:
            # Cluster-wide rate or pod cap: later candidates would hit the same wall.
            break
    return promoted


def sweep_stale_job_workers(
    db,
    r,
    *,
    k8s_request: Callable = k8s_api_request,
) -> int:
    """Teardown job workers with no matching in_progress migration_record row."""
    if not job_isolation_enabled():
        return 0

    in_progress = {
        row[0]
        for row in db.execute(
            text("SELECT DISTINCT job_name FROM migration_record WHERE status = 'in_progress'"),
        ).fetchall()
    }
    active_hashes = list(r.smembers(REDIS_ACTIVE_SET_KEY) or [])
    deleted = 0
    for raw_hash in active_hashes:
        h = raw_hash.decode() if isinstance(raw_hash, bytes) else str(raw_hash)
        meta_raw = r.get(_meta_key_from_hash(h))
        if not meta_raw:
            r.srem(REDIS_ACTIVE_SET_KEY, h)
            continue
        meta = json.loads(meta_raw)
        job_name = meta.get("job_name") or ""
        if _meta_still_needed(meta, in_progress, r=r):
            if drop_registry_if_deployment_missing(r, job_name, k8s_request):
                deleted += 1
                logger.warning(
                    "JOB_WORKER_SWEEP_GHOST | job=%s | hash=%s",
                    job_name,
                    h,
                )
            continue
        teardown_job_workers(job_name, fast=True, r=r, k8s_request=k8s_request)
        deleted += 1
        logger.warning("JOB_WORKER_SWEEP_CLEANUP | job=%s | hash=%s", job_name, h)
    return deleted


def startup_orphan_sweep(
    db_session_factory,
    r,
    *,
    k8s_request: Callable = k8s_api_request,
) -> dict:
    """
    On control worker startup: list job-pool deployments and remove orphans.
    """
    if not job_isolation_enabled() or not is_control_worker():
        return {"found": 0, "deleted": 0}

    namespace = _k8s_namespace()
    list_body = k8s_request(
        "GET",
        f"/apis/apps/v1/namespaces/{namespace}/deployments"
        f"?labelSelector={JOB_WORKER_LABEL_MANAGED}={JOB_WORKER_LABEL_MANAGED_BY}",
    )
    items = (list_body or {}).get("items") or []
    found = len(items)

    with db_session_factory() as db:
        in_progress = {
            row[0]
            for row in db.execute(
                text("SELECT DISTINCT job_name FROM migration_record WHERE status = 'in_progress'"),
            ).fetchall()
        }
        active_hashes = {fleet_hash(j) for j in in_progress}
        # Also keep flow fleets that still have a run lease.
        for item in items:
            labels = (item.get("metadata") or {}).get("labels") or {}
            run_label = labels.get(JOB_WORKER_LABEL_PIPELINE_RUN)
            if run_label and flow_fleet_lease_active(r, int(run_label)):
                active_hashes.add(labels.get(JOB_WORKER_LABEL_HASH, ""))
        deleted = 0
        for item in items:
            if deleted >= STARTUP_ORPHAN_MAX_DELETES:
                break
            labels = (item.get("metadata") or {}).get("labels") or {}
            h = labels.get(JOB_WORKER_LABEL_HASH, "")
            dep_name = (item.get("metadata") or {}).get("name", "")
            run_label = labels.get(JOB_WORKER_LABEL_PIPELINE_RUN)
            if run_label and (
                _pipeline_jobs_in_progress(in_progress, int(run_label))
                or flow_fleet_lease_active(r, int(run_label))
            ):
                continue
            if h not in active_hashes:
                k8s_request(
                    "DELETE",
                    f"/apis/apps/v1/namespaces/{namespace}/deployments/{dep_name}",
                )
                r.srem(REDIS_ACTIVE_SET_KEY, h)
                r.delete(_meta_key_from_hash(h))
                if run_label:
                    clear_flow_fleet_lease(r, int(run_label))
                deleted += 1

    if deleted:
        job_metrics.inc_orphan_sweep_deleted(deleted)
    logger.warning("STARTUP_ORPHAN_SWEEP | found=%s | deleted=%s", found, deleted)
    return {"found": found, "deleted": deleted}
