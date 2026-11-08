from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0092_pipeline_notification_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipeline',
            name='sync_end_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='pipeline',
            name='schedule_type',
            field=models.CharField(
                choices=[
                    ('manual', 'manual'),
                    ('interval', 'interval'),
                    ('cron', 'cron'),
                ],
                default='interval',
                max_length=20,
            ),
        ),
    ]
