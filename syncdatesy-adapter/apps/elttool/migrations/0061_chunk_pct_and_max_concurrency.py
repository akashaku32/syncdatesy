from decimal import Decimal

from django.db import migrations, models


PLAN_CHUNK_CONCURRENCY_SEED = {
    'free': {'chunk_size_pct': Decimal('25.00'), 'max_concurrency': 8},
    'standard': {'chunk_size_pct': Decimal('25.00'), 'max_concurrency': 10},
    'discounted': {'chunk_size_pct': Decimal('25.00'), 'max_concurrency': 10},
    'premium': {'chunk_size_pct': Decimal('50.00'), 'max_concurrency': 16},
    'enterprise': {'chunk_size_pct': Decimal('100.00'), 'max_concurrency': 24},
}

SOURCE_MODEL_LIMITS = [
    (('Salesforce', 'SalesforceSandbox'), 1, 10000),
    (('Snowflake',), 1, 50000),
    (('Microsoft_Dynamics', 'Dynamics', 'Microsoft Dynamics'), 1, 50000),
    (('MySQL', 'Postgres', 'PostgreSQL'), 1, 50000),
]

DESTINATION_MODEL_LIMITS = [
    (('Salesforce', 'SalesforceSandbox'), 1, 500),
    (('Snowflake',), 1, 50000),
    (('MySQL', 'Postgres', 'PostgreSQL', 'Microsoft_Dynamics', 'Dynamics', 'Microsoft Dynamics'), 1, 50000),
]


def seed_plan_chunk_concurrency(apps, schema_editor):
    PlanResourceConfig = apps.get_model('elttool', 'PlanResourceConfig')
    for plan_name, values in PLAN_CHUNK_CONCURRENCY_SEED.items():
        PlanResourceConfig.objects.filter(plan_name=plan_name).update(**values)


def seed_source_model_limits(apps, schema_editor):
    SourceModel = apps.get_model('elttool', 'SourceModel')
    for names, min_count, max_count in SOURCE_MODEL_LIMITS:
        SourceModel.objects.filter(name__in=names).update(
            min_count=min_count,
            max_count=max_count,
        )


def seed_destination_model_limits(apps, schema_editor):
    DestinationModel = apps.get_model('elttool', 'DestinationModel')
    for names, min_count, max_count in DESTINATION_MODEL_LIMITS:
        DestinationModel.objects.filter(name__in=names).update(
            min_count=min_count,
            max_count=max_count,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0060_plan_and_company_resource_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='planresourceconfig',
            name='chunk_size_pct',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
        migrations.AddField(
            model_name='planresourceconfig',
            name='max_concurrency',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sourcemodel',
            name='min_count',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='sourcemodel',
            name='max_count',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(seed_plan_chunk_concurrency, migrations.RunPython.noop),
        migrations.RunPython(seed_source_model_limits, migrations.RunPython.noop),
        migrations.RunPython(seed_destination_model_limits, migrations.RunPython.noop),
    ]
