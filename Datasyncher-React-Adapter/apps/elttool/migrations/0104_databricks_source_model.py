"""Backfill Databricks as a reverse-ETL warehouse source (is_rtl=True)."""

from django.db import migrations, models


DATABRICKS_SOURCE_ATTRIBUTES = [
    {
        "attribute_name": "server_hostname",
        "input_type": "CharField",
        "label": "Server Hostname",
        "required": True,
        "is_visible": True,
        "read_only": True,
        "widget": None,
    },
    {
        "attribute_name": "http_path",
        "input_type": "CharField",
        "label": "HTTP Path",
        "required": True,
        "is_visible": True,
        "read_only": True,
        "widget": None,
    },
    {
        "attribute_name": "access_token",
        "input_type": "PasswordInput",
        "label": "Access Token",
        "required": True,
        "is_visible": True,
        "read_only": False,
        "widget": "PasswordInput",
    },
    {
        "attribute_name": "catalog",
        "input_type": "CharField",
        "label": "Catalog",
        "required": True,
        "is_visible": True,
        "read_only": True,
        "widget": None,
    },
    {
        "attribute_name": "schema",
        "input_type": "CharField",
        "label": "Schema",
        "required": True,
        "is_visible": True,
        "read_only": True,
        "widget": None,
    },
]


def apply_databricks_source(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceAttribute = apps.get_model("elttool", "SourceAttribute")

    source, created = SourceModel.objects.get_or_create(
        name="Databricks",
        defaults={
            "src_id": (
                (SourceModel.objects.aggregate(m=models.Max("src_id")).get("m") or 0) + 1
            ),
            "dst_schema": "",
            "deflt_interval": "15",
            "is_rtl": True,
            "display_name": "Databricks",
            "disable_update_schema": False,
        },
    )
    if not created:
        changed = []
        if source.is_rtl is not True:
            source.is_rtl = True
            changed.append("is_rtl")
        if source.display_name != "Databricks":
            source.display_name = "Databricks"
            changed.append("display_name")
        if changed:
            source.save(update_fields=changed)

    max_attr = SourceAttribute.objects.aggregate(m=models.Max("src_attr_id")).get("m") or 0
    next_id = max_attr + 1
    existing = set(
        SourceAttribute.objects.filter(src=source).values_list("attribute_name", flat=True)
    )
    for attr in DATABRICKS_SOURCE_ATTRIBUTES:
        if attr["attribute_name"] in existing:
            continue
        SourceAttribute.objects.create(
            src_attr_id=next_id,
            src=source,
            attribute_name=attr["attribute_name"],
            input_type=attr["input_type"],
            label=attr["label"],
            required=attr["required"],
            is_visible=attr["is_visible"],
            read_only=attr["read_only"],
            widget=attr["widget"],
        )
        next_id += 1


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0103_salesforce_source_max_count"),
    ]

    operations = [
        migrations.RunPython(apply_databricks_source, noop_reverse),
    ]
