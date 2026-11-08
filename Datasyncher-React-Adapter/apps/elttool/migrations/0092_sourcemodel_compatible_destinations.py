from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds the source_destination_compatibility join table (M2M between SourceModel and DestinationModel).

    After this migration all sources have zero rows in the join table and are therefore
    blocked everywhere (fail-closed). Migration 0093 immediately seeds the correct rows
    per the compatibility matrix.
    """

    dependencies = [
        ('elttool', '0111_source_file_mapping_timestamp_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='sourcemodel',
            name='compatible_destinations',
            field=models.ManyToManyField(
                blank=True,
                db_table='source_destination_compatibility',
                related_name='compatible_sources',
                to='elttool.destinationmodel',
            ),
        ),
    ]
