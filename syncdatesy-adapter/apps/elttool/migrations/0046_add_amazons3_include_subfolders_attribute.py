from django.db import migrations
from django.db.models import Max


def add_include_subfolders_attribute(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceAttribute = apps.get_model("elttool", "SourceAttribute")

    try:
        amazon_s3 = SourceModel.objects.get(name="AmazonS3")
    except SourceModel.DoesNotExist:
        return

    exists = SourceAttribute.objects.filter(
        src=amazon_s3,
        attribute_name="include_subfolders",
    ).exists()
    if exists:
        return

    last_id = SourceAttribute.objects.aggregate(max_id=Max("src_attr_id")).get("max_id") or 0
    SourceAttribute.objects.create(
        src_attr_id=last_id + 1,
        src=amazon_s3,
        attribute_name="include_subfolders",
        input_type="ChoiceField",
        label="Include Subfolders",
        required=True,
        is_visible=True,
        choices=[
            {"value": "true", "display": "Yes"},
            {"value": "false", "display": "No"},
        ],
        default_value="false",
    )


def remove_include_subfolders_attribute(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceAttribute = apps.get_model("elttool", "SourceAttribute")

    try:
        amazon_s3 = SourceModel.objects.get(name="AmazonS3")
    except SourceModel.DoesNotExist:
        return

    SourceAttribute.objects.filter(
        src=amazon_s3,
        attribute_name="include_subfolders",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0045_active_name_unique_constraints"),
    ]

    operations = [
        migrations.RunPython(
            add_include_subfolders_attribute,
            remove_include_subfolders_attribute,
        ),
    ]
