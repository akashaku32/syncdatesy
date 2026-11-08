"""Restore Enterprise to batch right-sized tier (not Premium-like).

Enterprise: 6 pods @ 7/8 CPU, 22/24Gi on e2-standard-8, concurrency 6,
chunk 175k @ 100%, max_inflight 36 (6 × 6).
"""

from django.db import migrations

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
    "max_inflight_tables": 36,  # 6 pods * 6 concurrency
    "max_concurrent_jobs": 3,
    "priority": 90,
}


def apply_enterprise_batch_sizing(apps, schema_editor):
    WorkerNodePoolConfig = apps.get_model("elttool", "WorkerNodePoolConfig")
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")
    Plan = apps.get_model("elttool", "Plan")

    WorkerNodePoolConfig.objects.filter(pool_name="worker-enterprise").update(
        **ENTERPRISE_POOL
    )
    PlanResourceConfig.objects.filter(plan_name="enterprise").update(
        **ENTERPRISE_PLAN,
        worker_pool_name_id="worker-enterprise",
    )
    Plan.objects.filter(plan_id=3).update(pod_count=6)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0100_enterprise_chunk_pct_75"),
    ]

    operations = [
        migrations.RunPython(apply_enterprise_batch_sizing, noop_reverse),
    ]
