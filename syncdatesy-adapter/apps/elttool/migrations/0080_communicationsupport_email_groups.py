from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0079_remove_legacy_sftp_export_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="communicationsupport",
            name="email_groups",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of notification email groups",
            ),
        ),
        migrations.AddField(
            model_name="communicationsupport",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="communication_support_updated",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
