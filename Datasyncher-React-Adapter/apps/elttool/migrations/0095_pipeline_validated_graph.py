from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0094_worker_job_sizing_fit_pools'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipeline',
            name='validated_graph',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pipeline',
            name='validated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pipeline',
            name='validated_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='validated_pipelines',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
