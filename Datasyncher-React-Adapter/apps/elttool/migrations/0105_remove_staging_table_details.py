"""Remove staging_table_details — per-chunk merge uses last_synced + migration_record."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0104_databricks_source_model"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="migrationrecord",
            name="staging_details",
        ),
        migrations.RemoveField(
            model_name="sourcetableselection",
            name="staging_details",
        ),
        migrations.DeleteModel(
            name="StagingTableDetails",
        ),
    ]
