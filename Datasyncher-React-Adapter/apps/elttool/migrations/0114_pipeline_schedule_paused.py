from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0113_standard_max_concurrent_jobs_2'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipeline',
            name='schedule_paused',
            field=models.BooleanField(
                default=False,
                help_text='When true, automatic schedule is paused without changing flow status.',
            ),
        ),
    ]
