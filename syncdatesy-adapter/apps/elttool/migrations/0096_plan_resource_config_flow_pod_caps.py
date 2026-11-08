from django.db import migrations


# Flow-based pod limits (per flow / job): free/standard/discounted=2, premium=4, enterprise=6.
# Mirror celery_resource_config._YAML_PLAN_DEFAULTS for max_pods + max_inflight_tables.
# Enterprise inflight = premium concurrency (4) * enterprise pods (6) = 24 (see 0098).
PLAN_POD_CAP_UPDATES = {
    "free": {"max_pods": 2, "max_inflight_tables": 6},
    "standard": {"max_pods": 2, "max_inflight_tables": 6},
    "discounted": {"max_pods": 2, "max_inflight_tables": 6},
    "premium": {"max_pods": 4, "max_inflight_tables": 16},
    "enterprise": {"max_pods": 6, "max_inflight_tables": 24},
}


def update_plan_pod_caps(apps, schema_editor):
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")
    for plan_name, fields in PLAN_POD_CAP_UPDATES.items():
        PlanResourceConfig.objects.filter(plan_name=plan_name).update(**fields)


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0095_pipeline_validated_graph"),
    ]

    operations = [
        migrations.RunPython(update_plan_pod_caps, migrations.RunPython.noop),
    ]
