"""Enterprise uses Premium pod specs on the worker-enterprise pool.

Premium: 4 pods @ 3/4 CPU, 10/12Gi, concurrency 4, chunk 100k, pool worker-premium
Enterprise: 6 pods @ same pod sizing, concurrency 4, chunk 150k, pool worker-enterprise
"""

from django.db import migrations

# Same pod sizing as premium; pool + pod count are the differentiators.
ENTERPRISE_LIKE_PREMIUM = {
    "max_pods": 6,
    "cpu_request": "3",
    "cpu_limit": "4",
    "memory_request": "10Gi",
    "memory_limit": "12Gi",
    "concurrency": 4,
    "max_chunk_size": 150000,
    "chunk_size_pct": 75.0,
    "max_concurrency": 16,
    "max_inflight_tables": 24,  # 6 pods * 4 concurrency
    "max_concurrent_jobs": 3,
    "priority": 90,
}


def apply_enterprise_premium_sizing(apps, schema_editor):
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")
    Plan = apps.get_model("elttool", "Plan")

    PlanResourceConfig.objects.filter(plan_name="enterprise").update(
        **ENTERPRISE_LIKE_PREMIUM,
        worker_pool_name_id="worker-enterprise",
    )
    # Keep plans catalog pod_count aligned with plan_resource_config.max_pods.
    Plan.objects.filter(plan_id=3).update(pod_count=6)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0097_add_premium_plan_sync_resource_config"),
    ]

    operations = [
        migrations.RunPython(apply_enterprise_premium_sizing, noop_reverse),
    ]
