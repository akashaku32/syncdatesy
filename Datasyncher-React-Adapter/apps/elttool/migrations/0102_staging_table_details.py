"""Add staging_table_details + nullable staging_details_id FKs (Databricks-only usage).

Additive migration: existing rows keep staging_details_id NULL. Non-Databricks
connectors never populate this table.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0101_revert_enterprise_batch_sizing"),
    ]

    operations = [
        migrations.CreateModel(
            name="StagingTableDetails",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("staging_table_name", models.CharField(max_length=255)),
                ("staging_records_count", models.PositiveIntegerField(default=0)),
                ("last_folder_checkpoint", models.CharField(blank=True, max_length=100, null=True)),
                (
                    "load_phase",
                    models.CharField(
                        choices=[
                            ("idle", "Idle"),
                            ("loading", "Loading"),
                            ("ready_to_merge", "Ready to merge"),
                            ("failed", "Failed"),
                        ],
                        default="idle",
                        max_length=32,
                    ),
                ),
                ("migration_session_id", models.PositiveIntegerField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "staging_table_details",
            },
        ),
        migrations.AddIndex(
            model_name="stagingtabledetails",
            index=models.Index(fields=["migration_session_id"], name="staging_tab_migrati_idx"),
        ),
        migrations.AddIndex(
            model_name="stagingtabledetails",
            index=models.Index(fields=["load_phase"], name="staging_tab_load_ph_idx"),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="staging_details",
            field=models.ForeignKey(
                blank=True,
                db_column="staging_details_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="source_tables",
                to="elttool.stagingtabledetails",
            ),
        ),
        migrations.AddField(
            model_name="migrationrecord",
            name="staging_details",
            field=models.ForeignKey(
                blank=True,
                db_column="staging_details_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="migration_records",
                to="elttool.stagingtabledetails",
            ),
        ),
    ]
