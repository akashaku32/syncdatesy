"""Backfill Salesforce / SalesforceSandbox source_model.max_count for chunk resolver."""

from django.db import migrations


def apply_salesforce_max_count(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    # Bulk API practical batch ceiling; aligns with Dynamics-style connector caps.
    SourceModel.objects.filter(name__in=["Salesforce", "SalesforceSandbox"]).update(
        max_count=200000,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0102_staging_table_details"),
    ]

    operations = [
        migrations.RunPython(apply_salesforce_max_count, noop_reverse),
    ]
