from django.db import migrations


def drop_connection_dedicated_dbx_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for column in (
            "create_dedicated_dbx_serverless_job",
            "databricks_spark_job_id",
            "databricks_spark_job_name",
            "databricks_spark_job_provision_status",
            "databricks_spark_job_provision_error",
        ):
            cursor.execute(f"ALTER TABLE connection DROP COLUMN IF EXISTS {column}")


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0127_remove_connection_dbx_spark_provision_display"),
    ]

    operations = [
        migrations.RunPython(drop_connection_dedicated_dbx_columns, migrations.RunPython.noop),
    ]
