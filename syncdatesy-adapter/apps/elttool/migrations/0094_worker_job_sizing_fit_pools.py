"""Align plan + worker pool sizing with batch-cluster GKE node pools (e2-standard-2/4/8)."""

from django.db import migrations

WORKER_POOLS = {
    "worker-standard": {
        "machine_type": "e2-standard-2",
        "allocatable_memory": "6Gi",
        "allocatable_cpu": "1900m",
        "overhead_memory": "1.5Gi",
        "overhead_cpu": "150m",
        "max_pods_per_node": 1,
        "node_selector": {"pool": "worker-standard"},
        "taint_key": None,
        "taint_value": None,
        "taint_effect": "NoSchedule",
    },
    "worker-premium": {
        "machine_type": "e2-standard-4",
        "allocatable_memory": "13Gi",
        "allocatable_cpu": "3800m",
        "overhead_memory": "2Gi",
        "overhead_cpu": "200m",
        "max_pods_per_node": 1,
        "node_selector": {"pool": "worker-premium"},
        "taint_key": "pool",
        "taint_value": "worker-premium",
        "taint_effect": "NoSchedule",
    },
    "worker-enterprise": {
        "machine_type": "e2-standard-8",
        "allocatable_memory": "26Gi",
        "allocatable_cpu": "7800m",
        "overhead_memory": "2Gi",
        "overhead_cpu": "200m",
        "max_pods_per_node": 1,
        "node_selector": {"pool": "worker-enterprise"},
        "taint_key": "pool",
        "taint_value": "worker-enterprise",
        "taint_effect": "NoSchedule",
    },
}

PLAN_RESOURCES = {
    "free": ("1500m", "2", "4Gi", "6Gi"),
    "standard": ("1500m", "2", "4Gi", "6Gi"),
    "discounted": ("1500m", "2", "4Gi", "6Gi"),
    "premium": ("3", "4", "10Gi", "12Gi"),
    "enterprise": ("7", "8", "22Gi", "24Gi"),
}


def apply_worker_job_sizing(apps, schema_editor):
    WorkerNodePoolConfig = apps.get_model("elttool", "WorkerNodePoolConfig")
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")

    for pool_name, fields in WORKER_POOLS.items():
        WorkerNodePoolConfig.objects.filter(pool_name=pool_name).update(**fields)

    for plan_name, (cpu_req, cpu_lim, mem_req, mem_lim) in PLAN_RESOURCES.items():
        PlanResourceConfig.objects.filter(plan_name=plan_name).update(
            cpu_request=cpu_req,
            cpu_limit=cpu_lim,
            memory_request=mem_req,
            memory_limit=mem_lim,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0093_pipeline_sync_end_date_manual_schedule"),
    ]

    operations = [
        migrations.RunPython(apply_worker_job_sizing, migrations.RunPython.noop),
    ]
