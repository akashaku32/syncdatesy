"""Restore Enterprise batch sizing (6 pods × 24Gi on e2-standard-8, 175k chunks).

Run inside the django pod (or with batch DB credentials):

  Get-Content scripts/revert_enterprise_batch_sizing.py |
    kubectl -n default exec -i deploy/django -- python -

Or via manage.py migrate after deploying migration 0101.
"""
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django

django.setup()

from apps.elttool.models import Plan, PlanResourceConfig, WorkerNodePoolConfig

ENTERPRISE_POOL = {
    "machine_type": "e2-standard-8",
    "allocatable_memory": "26Gi",
    "allocatable_cpu": "7800m",
    "overhead_memory": "2Gi",
    "overhead_cpu": "200m",
    "max_pods_per_node": 1,
}

ENTERPRISE_PLAN = {
    "max_pods": 6,
    "cpu_request": "7",
    "cpu_limit": "8",
    "memory_request": "22Gi",
    "memory_limit": "24Gi",
    "concurrency": 6,
    "max_chunk_size": 175000,
    "chunk_size_pct": 100.0,
    "max_concurrency": 24,
    "max_inflight_tables": 36,
    "max_concurrent_jobs": 3,
    "priority": 90,
}


def _print_state(label: str) -> None:
    pool = WorkerNodePoolConfig.objects.filter(pool_name="worker-enterprise").first()
    ent = PlanResourceConfig.objects.filter(plan_name="enterprise").first()
    print(f"=== {label} ===")
    if pool:
        print(
            "pool:",
            pool.pool_name,
            pool.machine_type,
            pool.allocatable_memory,
            pool.allocatable_cpu,
            pool.max_pods_per_node,
        )
    if ent:
        print(
            "plan:",
            ent.plan_name,
            "pods=",
            ent.max_pods,
            "cpu=",
            ent.cpu_request,
            ent.cpu_limit,
            "mem=",
            ent.memory_request,
            ent.memory_limit,
            "conc=",
            ent.concurrency,
            "chunk=",
            ent.max_chunk_size,
            ent.chunk_size_pct,
            "inflight=",
            ent.max_inflight_tables,
            "pool=",
            ent.worker_pool_name_id,
        )


_print_state("before")

WorkerNodePoolConfig.objects.filter(pool_name="worker-enterprise").update(**ENTERPRISE_POOL)
PlanResourceConfig.objects.filter(plan_name="enterprise").update(
    **ENTERPRISE_PLAN,
    worker_pool_name_id="worker-enterprise",
)
Plan.objects.filter(plan_id=3).update(pod_count=6)

_print_state("after")

# Flush Redis plan-resource cache so workers pick up immediately.
try:
    import redis

    redis_url = os.environ.get("REDIS_URL") or os.environ.get("CELERY_BROKER_URL")
    if redis_url:
        # Strip celery redis+ssl:// quirks if present
        url = redis_url.replace("redis+ssl://", "rediss://")
        r = redis.from_url(url, decode_responses=True)
        prefix = os.environ.get("PLAN_RESOURCE_CACHE_PREFIX", "plan:resource:")
        deleted = 0
        for key in r.scan_iter(match=f"{prefix}*", count=100):
            r.delete(key)
            deleted += 1
        print(f"redis plan cache keys deleted: {deleted}")
    else:
        print("REDIS_URL/CELERY_BROKER_URL not set; skip cache flush (TTL ~300s)")
except Exception as exc:
    print(f"redis cache flush skipped: {exc}")

print("DONE")
