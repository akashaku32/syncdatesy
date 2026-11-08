"""Align NetSuite source form fields with SuiteQL REST vs SuiteAnalytics Connect auth."""

from django.db import migrations

from apps.elttool.netsuite_source_attributes import (
    NETSUITE_SOURCE_ATTRIBUTES,
    NETSUITE_SOURCE_NAME,
)


def _upsert_netsuite_attributes(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceAttribute = apps.get_model("elttool", "SourceAttribute")

    try:
        source = SourceModel.objects.get(name=NETSUITE_SOURCE_NAME)
    except SourceModel.DoesNotExist:
        return

    max_id = (
        SourceAttribute.objects.order_by("-src_attr_id")
        .values_list("src_attr_id", flat=True)
        .first()
        or 0
    )
    next_id = max_id + 1

    for attribute_data in NETSUITE_SOURCE_ATTRIBUTES:
        name = attribute_data["attribute_name"]
        existing = SourceAttribute.objects.filter(src=source, attribute_name=name).first()
        kwargs = {
            "input_type": attribute_data["input_type"],
            "label": attribute_data["label"],
            "required": attribute_data.get("required", True),
            "is_visible": attribute_data.get("is_visible", True),
            "read_only": attribute_data.get("read_only", False),
            "depend_on": attribute_data.get("depend_on"),
            "dependency_value": attribute_data.get("dependency_value"),
            "choices": attribute_data.get("choices"),
            "widget": attribute_data.get("widget"),
            "placeholder": attribute_data.get("placeholder"),
            "description": attribute_data.get("description"),
            "default_value": attribute_data.get("default_value"),
        }
        if existing:
            for field, value in kwargs.items():
                setattr(existing, field, value)
            existing.save()
        else:
            SourceAttribute.objects.create(
                src_attr_id=next_id,
                src=source,
                attribute_name=name,
                **kwargs,
            )
            next_id += 1


def _noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0130_netsuite_source_attributes"),
    ]

    operations = [
        migrations.RunPython(_upsert_netsuite_attributes, _noop_reverse),
    ]
