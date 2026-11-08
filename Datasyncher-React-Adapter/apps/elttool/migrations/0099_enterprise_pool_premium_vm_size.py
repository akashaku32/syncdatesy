"""Enterprise worker pool uses e2-standard-4 (same VM size as premium, 1 pod/node)."""

from django.db import migrations

ENTERPRISE_POOL_LIKE_PREMIUM_VM = {
    "machine_type": "e2-standard-4",
    "allocatable_memory": "13Gi",
    "allocatable_cpu": "3800m",
    "overhead_memory": "2Gi",
    "overhead_cpu": "200m",
    "max_pods_per_node": 1,
}


def apply_enterprise_pool_premium_vm(apps, schema_editor):
    WorkerNodePoolConfig = apps.get_model("elttool", "WorkerNodePoolConfig")
    WorkerNodePoolConfig.objects.filter(pool_name="worker-enterprise").update(
        **ENTERPRISE_POOL_LIKE_PREMIUM_VM
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0098_enterprise_same_spec_as_premium"),
    ]

    operations = [
        migrations.RunPython(apply_enterprise_pool_premium_vm, noop_reverse),
    ]
