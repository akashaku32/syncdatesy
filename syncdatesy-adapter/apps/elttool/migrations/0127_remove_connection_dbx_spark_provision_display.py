from django.db import migrations


def drop_legacy_provision_columns(apps, schema_editor):
    """No-op safe: removes display-only columns if an older 0126 added them."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE connection DROP COLUMN IF EXISTS databricks_spark_job_provision_status"
        )
        cursor.execute(
            "ALTER TABLE connection DROP COLUMN IF EXISTS databricks_spark_job_provision_error"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0126_connection_dedicated_dbx_spark_job"),
    ]

    operations = [
        migrations.RunPython(drop_legacy_provision_columns, migrations.RunPython.noop),
    ]
