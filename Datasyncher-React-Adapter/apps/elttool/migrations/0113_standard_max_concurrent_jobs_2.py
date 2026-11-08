from django.db import migrations


def update_standard_concurrent_flows(apps, schema_editor):
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")
    PlanResourceConfig.objects.filter(plan_name="standard").update(
        max_concurrent_jobs=2
    )


def revert_standard_concurrent_flows(apps, schema_editor):
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")
    PlanResourceConfig.objects.filter(plan_name="standard").update(
        max_concurrent_jobs=1
    )


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0112_ops_snapshot_queue"),
        ("elttool", "0093_populate_source_destination_compatibility"),
    ]

    operations = [
        migrations.RunPython(
            update_standard_concurrent_flows,
            revert_standard_concurrent_flows,
        ),
    ]
