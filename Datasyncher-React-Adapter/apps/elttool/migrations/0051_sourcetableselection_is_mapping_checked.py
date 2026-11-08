from django.db import migrations, models


def backfill_mapping_checked(apps, schema_editor):
    SourceTableSelection = apps.get_model("elttool", "SourceTableSelection")
    SourceTableSelection.objects.all().update(is_mapping_checked=True)


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0050_rename_s3mapping_to_sourcefilemapping"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="sourcetableselection",
                    name="is_mapping_checked",
                    field=models.BooleanField(default=False),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    ALTER TABLE source_table_selection
                        ADD COLUMN IF NOT EXISTS is_mapping_checked boolean NOT NULL DEFAULT false;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
        migrations.RunPython(backfill_mapping_checked, migrations.RunPython.noop),
    ]
