from django.db import migrations


def reorder_amazons3_attributes(apps, schema_editor):
    SourceAttribute = apps.get_model('elttool', 'SourceAttribute')
    SourceModel = apps.get_model('elttool', 'SourceModel')

    try:
        amazon_s3 = SourceModel.objects.get(name='AmazonS3')
    except SourceModel.DoesNotExist:
        return

    src_id = amazon_s3.src_id

    # Attribute names
    ATTRS = {
        'multi_files_table_name': 51,
        'multi_files_prefix': 52,
        'load_method': 53,
        'custom_primary_key': 54,
    }

    TEMP_BASE = 900000

    # Step 1: Move affected attributes to temporary IDs
    for i, name in enumerate(ATTRS.keys(), start=1):
        SourceAttribute.objects.filter(
            src_id=src_id,
            attribute_name=name
        ).update(src_attr_id=TEMP_BASE + i)

    # Step 2: Assign final correct IDs
    for name, final_id in ATTRS.items():
        SourceAttribute.objects.filter(
            src_id=src_id,
            attribute_name=name
        ).update(src_attr_id=final_id)

    print("✅ AmazonS3 attribute order fixed successfully")


def reverse_reorder_amazons3_attributes(apps, schema_editor):
    """
    Reverse not supported because original IDs are intentionally changed.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0027_align_index_names_with_tables'),
    ]

    operations = [
        migrations.RunPython(
            reorder_amazons3_attributes,
            reverse_reorder_amazons3_attributes
        ),
    ]
