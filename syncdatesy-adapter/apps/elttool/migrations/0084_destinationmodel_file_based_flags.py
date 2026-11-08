from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0083_sourcetableselection_email_custom_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="destinationmodel",
            name="is_file_based",
            field=models.BooleanField(blank=True, default=False),
        ),
        migrations.AddField(
            model_name="destinationmodel",
            name="supports_notification_groups",
            field=models.BooleanField(blank=True, default=False),
        ),
    ]
