# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0009_sourcemodel_is_rtl'),
    ]

    operations = [
        migrations.AddField(
            model_name='destinationattribute',
            name='read_only',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='sourceattribute',
            name='read_only',
            field=models.BooleanField(default=False),
        ),
    ]

