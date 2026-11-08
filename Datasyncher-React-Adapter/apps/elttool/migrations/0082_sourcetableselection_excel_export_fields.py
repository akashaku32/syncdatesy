from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0081_sourcetableselection_notification_email_group_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="excel_conditional_formats",
            field=models.JSONField(blank=True, default=list, null=True),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="excel_options",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="excel_sheet_name",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
