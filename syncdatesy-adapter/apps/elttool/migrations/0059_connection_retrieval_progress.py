from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0058_plan_table_and_company_pod_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='connection',
            name='retrieval_progress',
            field=models.CharField(
                blank=True,
                default=None,
                help_text='Dynamics retrieval ratio e.g. 50/500; cleared when listing/migration completes.',
                max_length=32,
                null=True,
            ),
        ),
    ]
