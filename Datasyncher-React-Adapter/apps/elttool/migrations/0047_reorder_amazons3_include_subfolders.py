from django.db import migrations
from django.db.models import F


TEMP_BASE = 900000


def reorder_amazons3_include_subfolders(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceAttribute = apps.get_model("elttool", "SourceAttribute")

    try:
        amazon_s3 = SourceModel.objects.get(name="AmazonS3")
    except SourceModel.DoesNotExist:
        return

    base_attr = SourceAttribute.objects.filter(
        src=amazon_s3,
        attribute_name="base_folder_path",
    ).first()
    include_attr = SourceAttribute.objects.filter(
        src=amazon_s3,
        attribute_name="include_subfolders",
    ).first()

    if not base_attr or not include_attr:
        return

    target_id = base_attr.src_attr_id + 1
    include_current_id = include_attr.src_attr_id

    # Already in correct position or before it; nothing to do.
    if include_current_id <= target_id:
        return

    # Move include_subfolders out of the way first.
    SourceAttribute.objects.filter(src_attr_id=include_current_id).update(
        src_attr_id=TEMP_BASE + 1
    )

    # Move the affected range to temp IDs to avoid PK collisions.
    SourceAttribute.objects.filter(
        src=amazon_s3,
        src_attr_id__gte=target_id,
        src_attr_id__lt=include_current_id,
    ).update(src_attr_id=F("src_attr_id") + TEMP_BASE)

    # Shift all the moved attributes by +1 into their final IDs.
    SourceAttribute.objects.filter(
        src=amazon_s3,
        src_attr_id__gte=TEMP_BASE + target_id,
        src_attr_id__lt=TEMP_BASE + include_current_id,
    ).update(src_attr_id=F("src_attr_id") - TEMP_BASE + 1)

    # Put include_subfolders right after base_folder_path.
    SourceAttribute.objects.filter(src_attr_id=TEMP_BASE + 1).update(
        src_attr_id=target_id
    )


def reverse_reorder_amazons3_include_subfolders(apps, schema_editor):
    """
    Reverse not supported because original ordering can differ across environments.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0046_add_amazons3_include_subfolders_attribute"),
    ]

    operations = [
        migrations.RunPython(
            reorder_amazons3_include_subfolders,
            reverse_reorder_amazons3_include_subfolders,
        ),
    ]
