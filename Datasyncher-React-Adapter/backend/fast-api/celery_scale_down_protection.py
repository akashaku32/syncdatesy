"""
Safe scale-down helpers for celery-worker pods.

Adds:
- Per-pod active task counter in Redis
- Kubernetes pod-deletion-cost annotation (prefer evicting idle pods)
- Optional KEDA desired-replicas floor at busy-pod count (scale down idle pods only)
- Per-task heartbeats + stale-task detection (check_job_status auto-requeues orphans)
- Worker drain signalling on shutdown

Redis keys (decode_responses=True):
- celery:pod:{pod_name}:active_tasks          — integer count (INCR/DECR, floor 0)
- celery:task-heartbeat:{task_id}              — JSON {task_id, pod_name, updated_at}
- celery:task-orphaned:{task_id}               — JSON orphan metadata (stale detection)
- celery:pod:{pod_name}:drain                  — "1" while worker is draining
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)

POD_ACTIVE_TASKS_KEY_PREFIX = os.getenv(
    "CELERY_POD_ACTIVE_TASKS_KEY_PREFIX", "celery:pod:"
)
POD_ACTIVE_TASKS_KEY_SUFFIX = os.getenv(
    "CELERY_POD_ACTIVE_TASKS_KEY_SUFFIX", ":active_tasks"
)
TASK_HEARTBEAT_KEY_PREFIX = os.getenv(
    "CELERY_TASK_HEARTBEAT_KEY_PREFIX", "celery:task-heartbeat:"
)
TASK_ORPHANED_KEY_PREFIX = os.getenv(
    "CELERY_TASK_ORPHANED_KEY_PREFIX", "celery:task-orphaned:"
)
POD_DRAIN_KEY_PREFIX = os.getenv("CELERY_POD_DRAIN_KEY_PREFIX", "celery:pod:")
POD_DRAIN_KEY_SUFFIX = os.getenv("CELERY_POD_DRAIN_KEY_SUFFIX", ":drain")

CELERY_SCALE_DOWN_PROTECTION_ENABLED = os.getenv(
    "CELERY_SCALE_DOWN_PROTECTION_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
CELERY_POD_DELETION_COST_ENABLED = os.getenv(
    "CELERY_POD_DELETION_COST_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
CELERY_POD_DELETION_COST_BUSY = int(
    os.getenv("CELERY_POD_DELETION_COST_BUSY", "100")
)
CELERY_POD_DELETION_COST_IDLE = int(
    os.getenv("CELERY_POD_DELETION_COST_IDLE", "0")
)
POD_DELETION_COST_ANNOTATION = os.getenv(
    "POD_DELETION_COST_ANNOTATION",
    "controller.kubernetes.io/pod-deletion-cost",
)
CELERY_TASK_HEARTBEAT_INTERVAL_SECONDS = int(
    os.getenv("CELERY_TASK_HEARTBEAT_INTERVAL_SECONDS", "60")
)
CELERY_TASK_HEARTBEAT_TTL_SECONDS = int(
    os.getenv("CELERY_TASK_HEARTBEAT_TTL_SECONDS", "3600")
)
CELERY_TASK_HEARTBEAT_STALE_SECONDS = int(
    os.getenv("CELERY_TASK_HEARTBEAT_STALE_SECONDS", "1200")
)
CELERY_STALE_TASK_SCAN_MAX_KEYS = max(
    10, int(os.getenv("CELERY_STALE_TASK_SCAN_MAX_KEYS", "250"))
)
CELERY_POD_ACTIVE_TASKS_TTL_SECONDS = int(
    os.getenv("CELERY_POD_ACTIVE_TASKS_TTL_SECONDS", "86400")
)
CELERY_KEDA_FLOOR_BY_BUSY_PODS = os.getenv(
    "CELERY_KEDA_FLOOR_BY_BUSY_PODS", "true"
).lower() in ("1", "true", "yes", "on")
CELERY_KEDA_MAX_REPLICAS = max(
    1, int(os.getenv("CELERY_KEDA_MAX_REPLICAS", "10"))
)
CELERY_POD_ACTIVE_TASKS_RECONCILE_ENABLED = os.getenv(
    "CELERY_POD_ACTIVE_TASKS_RECONCILE_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
CELERY_POD_ACTIVE_TASKS_RECONCILE_TIMEOUT = float(
    os.getenv("CELERY_POD_ACTIVE_TASKS_RECONCILE_TIMEOUT", "10")
)

_K8S_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_K8S_SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
_K8S_API_HOST = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
_K8S_API_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
CELERY_K8S_NAMESPACE = os.getenv("CELERY_K8S_NAMESPACE", "")

_TRACKED_TASK_NAMES = frozenset(
    {
        "celery_worker.call_function_child",
        "celery_worker.orchestrate_migration",
        "celery_worker.call_function",
        "celery_worker.finalize_migration_orchestration",
    }
)
_EXCLUDED_TASK_NAMES = frozenset({"celery_worker.check_job_status"})

_last_deletion_cost_by_pod: dict[str, int] = {}
_last_deletion_cost_lock = threading.Lock()
_task_heartbeat_threads: dict[str, threading.Event] = {}
_task_heartbeat_threads_lock = threading.Lock()
_draining = False
_DELETION_COST_LOCK_TIMEOUT_SECONDS = 2.0
_K8S_PATCH_TIMEOUT_SECONDS = 3.0


def reset_after_fork() -> None:
    """Drop locks and threads inherited from the parent Celery process.

    A prefork child copies a locked ``threading.Lock`` as locked, with no
    owning thread, so the next prerun blocks until the task time limit.
    """
    global _last_deletion_cost_lock, _task_heartbeat_threads_lock, _draining
    _last_deletion_cost_by_pod.clear()
    _task_heartbeat_threads.clear()
    _last_deletion_cost_lock = threading.Lock()
    _task_heartbeat_threads_lock = threading.Lock()
    _draining = False


def protection_enabled() -> bool:
    return CELERY_SCALE_DOWN_PROTECTION_ENABLED


def is_draining() -> bool:
    return _draining


def current_pod_name() -> str:
    return os.getenv("HOSTNAME", "unknown")


def pod_active_tasks_key(pod_name: Optional[str] = None) -> str:
    pod = pod_name or current_pod_name()
    return f"{POD_ACTIVE_TASKS_KEY_PREFIX}{pod}{POD_ACTIVE_TASKS_KEY_SUFFIX}"


def task_heartbeat_key(task_id: str) -> str:
    return f"{TASK_HEARTBEAT_KEY_PREFIX}{task_id}"


def task_orphaned_key(task_id: str) -> str:
    return f"{TASK_ORPHANED_KEY_PREFIX}{task_id}"


def pod_drain_key(pod_name: Optional[str] = None) -> str:
    pod = pod_name or current_pod_name()
    return f"{POD_DRAIN_KEY_PREFIX}{pod}{POD_DRAIN_KEY_SUFFIX}"


def deletion_cost_for_active_count(active_count: int) -> int:
    if active_count > 0:
        return CELERY_POD_DELETION_COST_BUSY
    return CELERY_POD_DELETION_COST_IDLE


def keda_floor_by_busy_pods_enabled() -> bool:
    return protection_enabled() and CELERY_KEDA_FLOOR_BY_BUSY_PODS


def count_busy_pods(r) -> int:
    """Return celery-worker pods with celery:pod:{name}:active_tasks > 0."""
    pattern = f"{POD_ACTIVE_TASKS_KEY_PREFIX}*{POD_ACTIVE_TASKS_KEY_SUFFIX}"
    busy = 0
    try:
        for key in r.scan_iter(match=pattern):
            try:
                if int(r.get(key) or 0) > 0:
                    busy += 1
            except (TypeError, ValueError):
                continue
    except Exception as exc:
        logger.warning("COUNT_BUSY_PODS_FAILED | err=%s", exc)
    return busy


def floor_desired_replicas_by_busy_pods(workload_desired: int, r) -> int:
    """
    Never let KEDA/HPA scale below the number of pods still running migration work.

    Combined with pod-deletion-cost (idle=0, busy=100), HPA removes only idle pods
    until every busy pod finishes and decrements its active_tasks counter.
    """
    if not keda_floor_by_busy_pods_enabled():
        return workload_desired
    busy = count_busy_pods(r)
    if busy <= workload_desired:
        return workload_desired
    floored = min(busy, CELERY_KEDA_MAX_REPLICAS)
    logger.warning(
        "KEDA_DESIRED_BUSY_POD_FLOOR | workload_desired=%s | busy_pods=%s | floored=%s",
        workload_desired,
        busy,
        floored,
    )
    return floored


def should_track_task(task_name: str) -> bool:
    if not task_name or task_name in _EXCLUDED_TASK_NAMES:
        return False
    return task_name in _TRACKED_TASK_NAMES


def _k8s_namespace() -> str:
    if CELERY_K8S_NAMESPACE:
        return CELERY_K8S_NAMESPACE
    try:
        with open(_K8S_SA_NAMESPACE_PATH, encoding="utf-8") as fh:
            return fh.read().strip() or "default"
    except OSError:
        return "default"


def cleanup_stale_pod_protection_state(r, *, pod_name: str) -> None:
    """Drop Redis scale-down keys for a pod that no longer exists in Kubernetes."""
    if not pod_name or pod_name == "unknown":
        return
    try:
        for key in (pod_active_tasks_key(pod_name), pod_drain_key(pod_name)):
            r.delete(key)
    except Exception as exc:
        logger.debug(
            "POD_PROTECTION_STATE_CLEANUP_FAILED | pod=%s | err=%s",
            pod_name,
            exc,
        )
    with _last_deletion_cost_lock:
        _last_deletion_cost_by_pod.pop(pod_name, None)
    logger.debug(
        "POD_PROTECTION_STATE_CLEANED | pod=%s | reason=not_found",
        pod_name,
    )


def patch_pod_deletion_cost(
    cost: int,
    *,
    pod_name: Optional[str] = None,
    k8s_patch_fn: Optional[Callable[..., Optional[bool]]] = None,
) -> Optional[bool]:
    """Patch controller.kubernetes.io/pod-deletion-cost on the current pod."""
    if not CELERY_POD_DELETION_COST_ENABLED:
        return False
    pod = pod_name or current_pod_name()
    if not pod or pod == "unknown":
        return False
    patcher = k8s_patch_fn or _k8s_patch_pod_annotation
    return patcher(pod, POD_DELETION_COST_ANNOTATION, str(int(cost)))


def _k8s_patch_pod_annotation(
    pod_name: str, annotation_key: str, value: str
) -> Optional[bool]:
    if not os.path.isfile(_K8S_SA_TOKEN_PATH):
        logger.debug("POD_DELETION_COST_SKIP | pod=%s | reason=no_sa_token", pod_name)
        return False
    namespace = _k8s_namespace()
    path = f"/api/v1/namespaces/{namespace}/pods/{pod_name}"
    url = f"https://{_K8S_API_HOST}:{_K8S_API_PORT}{path}"
    body = {"metadata": {"annotations": {annotation_key: value}}}
    try:
        with open(_K8S_SA_TOKEN_PATH, encoding="utf-8") as fh:
            token = fh.read().strip()
        resp = requests.patch(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/merge-patch+json",
            },
            json=body,
            verify=_K8S_SA_CA_PATH if os.path.isfile(_K8S_SA_CA_PATH) else True,
            timeout=_K8S_PATCH_TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            logger.debug(
                "POD_DELETION_COST_SKIP | pod=%s | reason=not_found",
                pod_name,
            )
            return None
        if resp.status_code >= 400:
            logger.warning(
                "POD_DELETION_COST_PATCH_FAILED | pod=%s | status=%s | body=%s",
                pod_name,
                resp.status_code,
                resp.text[:300],
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "POD_DELETION_COST_PATCH_FAILED | pod=%s | err=%s",
            pod_name,
            exc,
        )
        return False


def sync_deletion_cost_from_redis(
    r,
    *,
    pod_name: Optional[str] = None,
    k8s_patch_fn: Optional[Callable[..., Optional[bool]]] = None,
) -> int:
    """Read active count from Redis and patch pod deletion cost if changed."""
    pod = pod_name or current_pod_name()
    active = get_active_task_count(r, pod)
    cost = deletion_cost_for_active_count(active)
    if not _last_deletion_cost_lock.acquire(timeout=_DELETION_COST_LOCK_TIMEOUT_SECONDS):
        logger.warning("POD_DELETION_COST_LOCK_TIMEOUT | pod=%s", pod)
        return active
    try:
        if _last_deletion_cost_by_pod.get(pod) == cost:
            return active
    finally:
        _last_deletion_cost_lock.release()

    patch_result = patch_pod_deletion_cost(
        cost, pod_name=pod, k8s_patch_fn=k8s_patch_fn
    )
    if patch_result is None:
        cleanup_stale_pod_protection_state(r, pod_name=pod)
        return active
    if patch_result:
        if _last_deletion_cost_lock.acquire(timeout=_DELETION_COST_LOCK_TIMEOUT_SECONDS):
            try:
                _last_deletion_cost_by_pod[pod] = cost
            finally:
                _last_deletion_cost_lock.release()
        logger.warning(
            "POD_DELETION_COST_UPDATED | pod=%s | active_tasks=%s | cost=%s",
            pod,
            active,
            cost,
        )
    return active


def _schedule_deletion_cost_sync(r, *, pod_name: Optional[str] = None) -> None:
    """Patch pod deletion cost off the task thread so a slow API cannot stall fan-out."""

    def _run() -> None:
        try:
            sync_deletion_cost_from_redis(r, pod_name=pod_name)
        except Exception as exc:
            logger.warning(
                "POD_DELETION_COST_ASYNC_FAILED | pod=%s | err=%s",
                pod_name,
                exc,
            )

    threading.Thread(
        target=_run,
        name="pod-deletion-cost-sync",
        daemon=True,
    ).start()


def reset_pod_active_tasks(r, *, pod_name: Optional[str] = None) -> None:
    pod = pod_name or current_pod_name()
    key = pod_active_tasks_key(pod)
    try:
        r.delete(key)
        logger.warning(
            "POD_ACTIVE_TASKS_RESET | pod=%s | key=%s",
            pod,
            key,
        )
    except Exception as exc:
        logger.warning("POD_ACTIVE_TASKS_RESET_FAILED | pod=%s | err=%s", pod, exc)
    sync_deletion_cost_from_redis(r, pod_name=pod)


def get_active_task_count(r, pod_name: Optional[str] = None) -> int:
    try:
        raw = r.get(pod_active_tasks_key(pod_name))
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0
    except Exception:
        return 0


def set_active_task_count(
    r,
    count: int,
    *,
    pod_name: Optional[str] = None,
) -> int:
    """Set Redis active_tasks for a pod (used by inspect reconciliation)."""
    pod = pod_name or current_pod_name()
    key = pod_active_tasks_key(pod)
    normalized = max(0, int(count))
    try:
        if normalized == 0:
            r.delete(key)
        else:
            r.set(key, str(normalized), ex=CELERY_POD_ACTIVE_TASKS_TTL_SECONDS)
    except Exception as exc:
        logger.warning(
            "POD_ACTIVE_TASKS_SET_FAILED | pod=%s | count=%s | err=%s",
            pod,
            normalized,
            exc,
        )
        return get_active_task_count(r, pod)
    return normalized


def _task_name_from_inspect_entry(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("name") or "")
    return str(getattr(task, "name", "") or "")


def pod_name_from_worker_hostname(hostname: str) -> Optional[str]:
    """Map Celery worker hostname (celery@pod-name) to Kubernetes pod name."""
    value = str(hostname or "").strip()
    if not value:
        return None
    if "@" in value:
        value = value.split("@", 1)[1]
    value = value.strip()
    return value or None


def count_tracked_tasks_from_inspect_maps(
    active_map: Optional[dict[str, list[Any]]],
    reserved_map: Optional[dict[str, list[Any]]] = None,
) -> dict[str, int]:
    """
    Count tracked migration tasks per pod from celery inspect active/reserved maps.

    Reserved tasks are prefetched on a worker and should also mark the pod busy.
    """
    counts: dict[str, int] = {}

    def _accumulate(task_map: Optional[dict[str, list[Any]]]) -> None:
        if not task_map:
            return
        for worker_hostname, tasks in task_map.items():
            pod = pod_name_from_worker_hostname(worker_hostname)
            if not pod:
                continue
            tracked = sum(
                1
                for task in (tasks or [])
                if should_track_task(_task_name_from_inspect_entry(task))
            )
            counts[pod] = counts.get(pod, 0) + tracked

    _accumulate(active_map)
    _accumulate(reserved_map)
    return counts


def reconcile_pod_active_tasks_from_inspect(
    r,
    celery_app,
    *,
    timeout: Optional[float] = None,
    inspect_active: Optional[dict[str, list[Any]]] = None,
    inspect_reserved: Optional[dict[str, list[Any]]] = None,
    k8s_patch_fn: Optional[Callable[..., Optional[bool]]] = None,
) -> dict[str, dict[str, int]]:
    """
    Align Redis active_tasks and pod deletion-cost with live Celery inspect state.

    Fixes drift when prerun/postrun counters desync from real in-flight work so
    busy pods are not mislabeled cost=0 during scale-down.
    """
    if not protection_enabled() or not CELERY_POD_ACTIVE_TASKS_RECONCILE_ENABLED:
        return {}

    if inspect_active is None or inspect_reserved is None:
        try:
            inspector = celery_app.control.inspect(
                timeout=timeout or CELERY_POD_ACTIVE_TASKS_RECONCILE_TIMEOUT
            )
            if inspect_active is None:
                inspect_active = inspector.active() or {}
            if inspect_reserved is None:
                inspect_reserved = inspector.reserved() or {}
        except Exception as exc:
            logger.warning("POD_ACTIVE_TASKS_RECONCILE_INSPECT_FAILED | err=%s", exc)
            return {}

    expected = count_tracked_tasks_from_inspect_maps(inspect_active, inspect_reserved)

    redis_pods: set[str] = set()
    try:
        pattern = f"{POD_ACTIVE_TASKS_KEY_PREFIX}*{POD_ACTIVE_TASKS_KEY_SUFFIX}"
        for key in r.scan_iter(match=pattern):
            pod = str(key).replace(POD_ACTIVE_TASKS_KEY_PREFIX, "").replace(
                POD_ACTIVE_TASKS_KEY_SUFFIX, ""
            )
            if pod:
                redis_pods.add(pod)
    except Exception as exc:
        logger.warning("POD_ACTIVE_TASKS_RECONCILE_SCAN_FAILED | err=%s", exc)

    all_pods = set(expected.keys()) | redis_pods
    current_pod = current_pod_name()
    if current_pod and current_pod != "unknown":
        all_pods.add(current_pod)

    summary: dict[str, dict[str, int]] = {}
    corrected = 0
    for pod in sorted(all_pods):
        if not pod or pod == "unknown":
            continue
        exp = int(expected.get(pod, 0))
        before = get_active_task_count(r, pod)
        if before != exp:
            set_active_task_count(r, exp, pod_name=pod)
            corrected += 1
            logger.warning(
                "POD_ACTIVE_TASKS_RECONCILED | pod=%s | redis_before=%s | inspect_expected=%s",
                pod,
                before,
                exp,
            )
        should_sync_k8s = (
            exp > 0 or pod in expected or pod == current_pod
        )
        if should_sync_k8s:
            sync_deletion_cost_from_redis(r, pod_name=pod, k8s_patch_fn=k8s_patch_fn)
        elif pod in redis_pods:
            cleanup_stale_pod_protection_state(r, pod_name=pod)
        summary[pod] = {"before": before, "after": exp}

    if corrected:
        logger.warning(
            "POD_ACTIVE_TASKS_RECONCILE_DONE | pods=%s | corrected=%s",
            len(summary),
            corrected,
        )
    return summary


def increment_active_tasks(
    r,
    *,
    pod_name: Optional[str] = None,
    defer_k8s_sync: bool = False,
) -> int:
    pod = pod_name or current_pod_name()
    key = pod_active_tasks_key(pod)
    try:
        count = int(r.incr(key))
        r.expire(key, CELERY_POD_ACTIVE_TASKS_TTL_SECONDS)
    except Exception as exc:
        logger.warning("POD_ACTIVE_TASKS_INCR_FAILED | pod=%s | err=%s", pod, exc)
        return get_active_task_count(r, pod)
    if defer_k8s_sync:
        _schedule_deletion_cost_sync(r, pod_name=pod)
    else:
        sync_deletion_cost_from_redis(r, pod_name=pod)
    return count


def decrement_active_tasks(
    r,
    *,
    pod_name: Optional[str] = None,
    defer_k8s_sync: bool = False,
) -> int:
    pod = pod_name or current_pod_name()
    key = pod_active_tasks_key(pod)
    try:
        current = get_active_task_count(r, pod)
        if current <= 0:
            r.set(key, "0", ex=CELERY_POD_ACTIVE_TASKS_TTL_SECONDS)
            count = 0
        else:
            count = max(0, int(r.decr(key)))
            r.expire(key, CELERY_POD_ACTIVE_TASKS_TTL_SECONDS)
    except Exception as exc:
        logger.warning("POD_ACTIVE_TASKS_DECR_FAILED | pod=%s | err=%s", pod, exc)
        count = get_active_task_count(r, pod)
    if defer_k8s_sync:
        _schedule_deletion_cost_sync(r, pod_name=pod)
    else:
        sync_deletion_cost_from_redis(r, pod_name=pod)
    return count


def _heartbeat_payload(task_id: str, pod_name: str) -> dict[str, str]:
    return {
        "task_id": task_id,
        "pod_name": pod_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_task_heartbeat(r, task_id: str, *, pod_name: Optional[str] = None) -> None:
    pod = pod_name or current_pod_name()
    try:
        r.set(
            task_heartbeat_key(task_id),
            json.dumps(_heartbeat_payload(task_id, pod)),
            ex=CELERY_TASK_HEARTBEAT_TTL_SECONDS,
        )
        logger.debug(
            "TASK_HEARTBEAT_UPDATED | task_id=%s | pod=%s",
            task_id,
            pod,
        )
    except Exception as exc:
        logger.warning(
            "TASK_HEARTBEAT_UPDATE_FAILED | task_id=%s | pod=%s | err=%s",
            task_id,
            pod,
            exc,
        )


def _stop_task_heartbeat_thread(task_id: str) -> None:
    with _task_heartbeat_threads_lock:
        stop = _task_heartbeat_threads.pop(task_id, None)
    if stop:
        stop.set()


def _start_task_heartbeat_thread(r, task_id: str, *, pod_name: Optional[str] = None) -> None:
    _stop_task_heartbeat_thread(task_id)
    stop = threading.Event()
    pod = pod_name or current_pod_name()
    interval = max(15, CELERY_TASK_HEARTBEAT_INTERVAL_SECONDS)

    def _loop() -> None:
        while not stop.wait(interval):
            write_task_heartbeat(r, task_id, pod_name=pod)

    with _task_heartbeat_threads_lock:
        _task_heartbeat_threads[task_id] = stop
    write_task_heartbeat(r, task_id, pod_name=pod)
    thread = threading.Thread(
        target=_loop,
        name=f"task-heartbeat-{task_id[:8]}",
        daemon=True,
    )
    thread.start()


def on_task_prerun(
    r,
    *,
    task_id: Optional[str],
    task_name: str,
) -> None:
    if not protection_enabled() or not should_track_task(task_name):
        return
    tid = str(task_id or "").strip()
    if not tid:
        return
    pod = current_pod_name()
    count = increment_active_tasks(r, pod_name=pod, defer_k8s_sync=True)
    _start_task_heartbeat_thread(r, tid, pod_name=pod)
    logger.warning(
        "SCALE_DOWN_TASK_START | task_id=%s | task_name=%s | pod=%s | active_tasks=%s | draining=%s",
        tid,
        task_name,
        pod,
        count,
        is_draining(),
    )


def on_task_postrun(
    r,
    *,
    task_id: Optional[str],
    task_name: str,
    state: Optional[str] = None,
) -> None:
    if not protection_enabled() or not should_track_task(task_name):
        return
    tid = str(task_id or "").strip()
    if not tid:
        return
    pod = current_pod_name()
    _stop_task_heartbeat_thread(tid)
    count = decrement_active_tasks(r, pod_name=pod, defer_k8s_sync=True)
    logger.warning(
        "SCALE_DOWN_TASK_FINISH | task_id=%s | task_name=%s | pod=%s | state=%s | active_tasks=%s",
        tid,
        task_name,
        pod,
        state,
        count,
    )


def enter_worker_drain(
    r,
    *,
    pod_name: Optional[str] = None,
    celery_app=None,
    migration_queue: str = "migration",
) -> None:
    global _draining
    if not protection_enabled():
        return
    _draining = True
    pod = pod_name or current_pod_name()
    try:
        r.set(pod_drain_key(pod), "1", ex=CELERY_POD_ACTIVE_TASKS_TTL_SECONDS)
    except Exception as exc:
        logger.warning("WORKER_DRAIN_REDIS_FAILED | pod=%s | err=%s", pod, exc)
    logger.warning(
        "WORKER_DRAIN_START | pod=%s | active_tasks=%s",
        pod,
        get_active_task_count(r, pod),
    )
    if celery_app is not None:
        try:
            hostname = os.getenv("CELERY_HOSTNAME") or pod
            destination = [hostname] if hostname else None
            celery_app.control.cancel_consumer(
                migration_queue,
                destination=destination,
                reply=False,
            )
            logger.warning(
                "WORKER_DRAIN_CONSUMER_CANCELLED | pod=%s | queue=%s | destination=%s",
                pod,
                migration_queue,
                destination,
            )
        except Exception as exc:
            logger.warning(
                "WORKER_DRAIN_CONSUMER_CANCEL_FAILED | pod=%s | err=%s",
                pod,
                exc,
            )


def exit_worker_drain(r, *, pod_name: Optional[str] = None) -> None:
    global _draining
    pod = pod_name or current_pod_name()
    _draining = False
    try:
        r.delete(pod_drain_key(pod))
    except Exception:
        pass
    logger.warning(
        "WORKER_DRAIN_END | pod=%s | active_tasks=%s",
        pod,
        get_active_task_count(r, pod),
    )


def _parse_heartbeat_age_seconds(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        updated = payload.get("updated_at")
        if not updated:
            return None
        ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return None


def detect_and_mark_stale_tasks(
    r,
    *,
    now: Optional[datetime] = None,
    stale_after_seconds: Optional[int] = None,
    max_keys: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Scan task heartbeats; mark orphans in Redis (no Celery requeue).
    Returns list of orphan records processed.
    """
    if not protection_enabled():
        return []
    threshold = stale_after_seconds or CELERY_TASK_HEARTBEAT_STALE_SECONDS
    scan_limit = max_keys if max_keys is not None else CELERY_STALE_TASK_SCAN_MAX_KEYS
    processed: list[dict[str, Any]] = []
    scanned = 0
    try:
        key_iter = r.scan_iter(match=f"{TASK_HEARTBEAT_KEY_PREFIX}*")
    except Exception as exc:
        logger.warning("STALE_TASK_SCAN_FAILED | err=%s", exc)
        return []
    for key in key_iter:
        scanned += 1
        if scanned > scan_limit:
            logger.warning(
                "STALE_TASK_SCAN_CAPPED | scanned=%s | limit=%s",
                scan_limit,
                scan_limit,
            )
            break
        tid = str(key).replace(TASK_HEARTBEAT_KEY_PREFIX, "", 1)
        if not tid:
            continue
        raw = r.get(key)
        age = _parse_heartbeat_age_seconds(raw)
        if age is None or age <= threshold:
            continue
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}
        pod = payload.get("pod_name") or "unknown"
        orphan_record = {
            "task_id": tid,
            "pod_name": pod,
            "heartbeat_age_secs": round(age, 1),
            "marked_at": (now or datetime.now(timezone.utc)).isoformat(),
        }
        try:
            r.set(
                task_orphaned_key(tid),
                json.dumps(orphan_record),
                ex=CELERY_TASK_HEARTBEAT_TTL_SECONDS,
            )
            r.delete(task_heartbeat_key(tid))
        except Exception as exc:
            logger.warning(
                "ORPHAN_TASK_MARK_FAILED | task_id=%s | err=%s",
                tid,
                exc,
            )
            continue
        # Best-effort decrement on the pod that owned the heartbeat.
        if pod and pod != "unknown":
            decrement_active_tasks(r, pod_name=pod)
        logger.warning(
            "ORPHAN_TASK_DETECTED | task_id=%s | pod=%s | heartbeat_age_secs=%.0f",
            tid,
            pod,
            age,
        )
        processed.append(orphan_record)
    return processed
