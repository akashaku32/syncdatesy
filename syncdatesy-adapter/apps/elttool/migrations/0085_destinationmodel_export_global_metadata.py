from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0084_destinationmodel_file_based_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="destinationmodel",
            name="path_label",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="destinationmodel",
            name="supported_formats",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="destinationmodel",
            name="default_format",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="destinationmodel",
            name="csv_defaults",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
