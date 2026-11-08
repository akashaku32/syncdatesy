from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0058_company_add_missing_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="migrationbatch",
            name="schedule_config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="migrationbatch",
            name="schedule_type",
            field=models.CharField(
                choices=[("interval", "interval"), ("cron", "cron")],
                default="interval",
                max_length=20,
            ),
        ),
    ]
