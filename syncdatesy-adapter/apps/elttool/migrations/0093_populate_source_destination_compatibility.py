from django.db import migrations

# Source and destination names as they appear in the source_model and destination_model tables.
# Names are used (not PKs) because auto-increment IDs differ across dev/staging/prod environments.
# If any name below does not exist in the DB, SourceModel.DoesNotExist or
# DestinationModel.DoesNotExist is raised and the migration fails loudly — blocking deploy
# until someone investigates. That is intentional: a partial join table is worse than a
# failed migration.

SNOWFLAKE_DEST_NAME = 'Snowflake'

# File-source scope: preserve the other connectors in the batch workspace.
RESTRICTED_SOURCE_NAMES = [
    'AmazonS3', 'SFTP', 'GoogleDrive', 'AzureDataLakeStorage',
]


def populate_compatibility(apps, schema_editor):
    SourceModel = apps.get_model('elttool', 'SourceModel')
    DestinationModel = apps.get_model('elttool', 'DestinationModel')

    snowflake_dst = DestinationModel.objects.get(name=SNOWFLAKE_DEST_NAME)

    for src_name in RESTRICTED_SOURCE_NAMES:
        # No try/except: if a name is wrong the migration fails loudly.
        # A silently skipped source would leave it with zero rows, blocking it
        # in both /source/all/ and connector creation (fail-closed behaviour),
        # but with no signal that something went wrong.
        src = SourceModel.objects.get(name=src_name)
        src.compatible_destinations.set([snowflake_dst])


def reverse_compatibility(apps, schema_editor):
    """Clear the four seeded file-source pairings."""
    SourceModel = apps.get_model('elttool', 'SourceModel')
    for src in SourceModel.objects.filter(name__in=RESTRICTED_SOURCE_NAMES):
        src.compatible_destinations.clear()


class Migration(migrations.Migration):
    """
    Populates source_destination_compatibility per the compatibility matrix:

    Source            | Compatible destinations
    ------------------|------------------------------------------
    Four file sources | Snowflake only

    File sources with zero rows are hidden for a selected destination and blocked on create.
    When adding a new source, write a new migration that calls .compatible_destinations.set([...]).
    """

    dependencies = [
        ('elttool', '0092_sourcemodel_compatible_destinations'),
    ]

    operations = [
        migrations.RunPython(populate_compatibility, reverse_compatibility),
    ]
