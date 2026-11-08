"""Enterprise chunk_size_pct 75% (150k on 200k Dynamics ceiling); backfill src max_count."""

from django.db import migrations


def apply_enterprise_chunk_pct_75(apps, schema_editor):
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")
    SourceModel = apps.get_model("elttool", "SourceModel")

    PlanResourceConfig.objects.filter(plan_name="enterprise").update(
        chunk_size_pct=75.0,
    )
    # Dynamics exports use 200k row folders; NULL max_count forced absolute_fallback.
    SourceModel.objects.filter(name="MicrosoftDynamics365_FO", max_count__isnull=True).update(
        max_count=200000,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0099_enterprise_pool_premium_vm_size"),
    ]

    operations = [
        migrations.RunPython(apply_enterprise_chunk_pct_75, noop_reverse),
    ]
