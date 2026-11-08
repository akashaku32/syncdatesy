from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0091_pipeline_start_node'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipeline',
            name='notify_on_flow_finish',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='pipeline',
            name='notification_email_group_ids',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='pipeline',
            name='notification_emails',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='pipelinenode',
            name='notify_on_completion',
            field=models.BooleanField(default=False),
        ),
    ]
