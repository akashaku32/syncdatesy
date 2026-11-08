from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0080_communicationsupport_email_groups"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="notification_email_group_ids",
            field=models.JSONField(blank=True, default=list, null=True),
        ),
    ]
