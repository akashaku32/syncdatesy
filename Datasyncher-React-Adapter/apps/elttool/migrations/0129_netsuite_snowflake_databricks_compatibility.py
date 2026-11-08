from django.db import migrations, models

SNOWFLAKE_DEST_NAME = "Snowflake"
DATABRICKS_DEST_NAME = "Databricks"
NETSUITE_SOURCE_NAME = "NetSuite"


def populate_netsuite_compatibility(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    DestinationModel = apps.get_model("elttool", "DestinationModel")

    snowflake_dst = DestinationModel.objects.get(name=SNOWFLAKE_DEST_NAME)
    databricks_dst = DestinationModel.objects.get(name=DATABRICKS_DEST_NAME)
    src, _created = SourceModel.objects.get_or_create(
        name=NETSUITE_SOURCE_NAME,
        defaults={
            "src_id": (
                (SourceModel.objects.aggregate(m=models.Max("src_id")).get("m") or 0) + 1
            ),
            "dst_schema": "",
            "deflt_interval": "15",
            "is_rtl": False,
            "display_name": "NetSuite",
            "disable_update_schema": False,
        },
    )
    src.compatible_destinations.set([snowflake_dst, databricks_dst])


def reverse_netsuite_compatibility(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    try:
        src = SourceModel.objects.get(name=NETSUITE_SOURCE_NAME)
    except SourceModel.DoesNotExist:
        return
    src.compatible_destinations.clear()


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0128_remove_connection_dedicated_dbx_spark_job"),
    ]

    operations = [
        migrations.RunPython(populate_netsuite_compatibility, reverse_netsuite_compatibility),
    ]
