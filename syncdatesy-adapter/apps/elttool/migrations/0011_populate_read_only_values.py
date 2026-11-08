# Generated manually - Data migration to populate read_only values

from django.db import migrations


def populate_read_only_values(apps, schema_editor):
    """
    Populate read_only field for DestinationAttribute and SourceAttribute.
    
    Logic:
    - read_only=False: Field is editable when DataTransfer records exist
    - read_only=True: Field is NOT editable when DataTransfer records exist
    """
    DestinationAttribute = apps.get_model('elttool', 'DestinationAttribute')
    SourceAttribute = apps.get_model('elttool', 'SourceAttribute')
    DestinationModel = apps.get_model('elttool', 'DestinationModel')
    SourceModel = apps.get_model('elttool', 'SourceModel')
    
    # ─────────────────────────────────────────────────────────────
    # Populate DestinationAttribute read_only values
    # ─────────────────────────────────────────────────────────────
    
    # For Snowflake destination
    try:
        snowflake_dest = DestinationModel.objects.get(name='Snowflake')
        snowflake_attrs = DestinationAttribute.objects.filter(dst=snowflake_dest)
        
        # Fields that are editable when DataTransfer exists (read_only=False)
        editable_fields_snowflake = [
            'destination_name',
            'authentication_type',
            'warehouse',
            'password',
            'private_key',
            'public_key',
            'passphrase'
        ]
        
        for attr in snowflake_attrs:
            if attr.attribute_name in editable_fields_snowflake:
                attr.read_only = False
                print(f"  ✅ Snowflake.{attr.attribute_name}: read_only=False")
            else:
                attr.read_only = True
                print(f"  ❌ Snowflake.{attr.attribute_name}: read_only=True")
            attr.save()
    except DestinationModel.DoesNotExist:
        print("  ⚠️  Snowflake destination not found")
    
    # For other destinations (Salesforce, SalesforceSandbox, etc.)
    other_destinations = DestinationModel.objects.exclude(name='Snowflake')
    for dest in other_destinations:
        attrs = DestinationAttribute.objects.filter(dst=dest)
        for attr in attrs:
            if attr.attribute_name == 'destination_name':
                attr.read_only = False
                print(f"  ✅ {dest.name}.{attr.attribute_name}: read_only=False")
            else:
                attr.read_only = True
                print(f"  ❌ {dest.name}.{attr.attribute_name}: read_only=True")
            attr.save()
    
    # ─────────────────────────────────────────────────────────────
    # Populate SourceAttribute read_only values
    # ─────────────────────────────────────────────────────────────
    
    # For all sources, schema and destination_schema are read-only when DataTransfer exists
    # All other config fields are also read-only when DataTransfer exists
    all_sources = SourceModel.objects.all()
    for source in all_sources:
        attrs = SourceAttribute.objects.filter(src=source)
        for attr in attrs:
            # schema and destination_schema: read_only=True (not editable when DataTransfer exists)
            # All other config fields: read_only=True (not editable when DataTransfer exists)
            attr.read_only = True
            print(f"  ❌ {source.name}.{attr.attribute_name}: read_only=True")
            attr.save()


def reverse_populate_read_only_values(apps, schema_editor):
    """Reverse migration - set all read_only to False (default)"""
    DestinationAttribute = apps.get_model('elttool', 'DestinationAttribute')
    SourceAttribute = apps.get_model('elttool', 'SourceAttribute')
    
    DestinationAttribute.objects.all().update(read_only=False)
    SourceAttribute.objects.all().update(read_only=False)


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0010_add_read_only_to_attributes'),
    ]

    operations = [
        migrations.RunPython(populate_read_only_values, reverse_populate_read_only_values),
    ]

