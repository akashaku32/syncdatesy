from django.db import migrations, models
import django.db.models.deletion


PLAN_RESOURCE_SEED = [
    {
        'plan_name': 'free',
        'max_pods': 1,
        'cpu_request': '2',
        'cpu_limit': '2',
        'memory_request': '8Gi',
        'memory_limit': '8Gi',
        'concurrency': 3,
        'max_chunk_size': 25000,
        'max_concurrent_jobs': 1,
        'max_inflight_tables': 3,
        'priority': 10,
        'extra_config': {},
    },
    {
        'plan_name': 'standard',
        'max_pods': 2,
        'cpu_request': '2',
        'cpu_limit': '2',
        'memory_request': '8Gi',
        'memory_limit': '8Gi',
        'concurrency': 3,
        'max_chunk_size': 50000,
        'max_concurrent_jobs': 1,
        'max_inflight_tables': 6,
        'priority': 50,
        'extra_config': {},
    },
    {
        'plan_name': 'discounted',
        'max_pods': 2,
        'cpu_request': '2',
        'cpu_limit': '2',
        'memory_request': '8Gi',
        'memory_limit': '8Gi',
        'concurrency': 3,
        'max_chunk_size': 50000,
        'max_concurrent_jobs': 1,
        'max_inflight_tables': 6,
        'priority': 40,
        'extra_config': {},
    },
    {
        'plan_name': 'premium',
        'max_pods': 3,
        'cpu_request': '4',
        'cpu_limit': '4',
        'memory_request': '16Gi',
        'memory_limit': '16Gi',
        'concurrency': 4,
        'max_chunk_size': 100000,
        'max_concurrent_jobs': 2,
        'max_inflight_tables': 12,
        'priority': 70,
        'extra_config': {},
    },
    {
        'plan_name': 'enterprise',
        'max_pods': 4,
        'cpu_request': '8',
        'cpu_limit': '8',
        'memory_request': '32Gi',
        'memory_limit': '32Gi',
        'concurrency': 6,
        'max_chunk_size': 200000,
        'max_concurrent_jobs': 3,
        'max_inflight_tables': 24,
        'priority': 90,
        'extra_config': {},
    },
]


def seed_plan_resource_config(apps, schema_editor):
    PlanResourceConfig = apps.get_model('elttool', 'PlanResourceConfig')
    for row in PLAN_RESOURCE_SEED:
        PlanResourceConfig.objects.update_or_create(
            plan_name=row['plan_name'],
            defaults=row,
        )


def backfill_company_resource_overrides(apps, schema_editor):
    """Map legacy company.pod_count > plan pods into company_resource_override.max_pods."""
    Company = apps.get_model('elttool', 'Company')
    Plan = apps.get_model('elttool', 'Plan')
    CompanyResourceOverride = apps.get_model('elttool', 'CompanyResourceOverride')
    plan_pods = {p.plan_id: int(p.pod_count) for p in Plan.objects.all()}

    for company in Company.objects.all().iterator():
        extra_pods = int(company.pod_count or 0)
        plan_id = company.plan_ref_id or 1
        plan_cap = plan_pods.get(plan_id, 1)
        if extra_pods > plan_cap:
            CompanyResourceOverride.objects.update_or_create(
                company_id=company.cmp_id,
                defaults={
                    'max_pods': extra_pods,
                    'reason': 'Migrated from company.pod_count',
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0059_connection_retrieval_progress'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlanResourceConfig',
            fields=[
                ('plan_name', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('max_pods', models.PositiveSmallIntegerField()),
                ('cpu_request', models.CharField(max_length=16)),
                ('cpu_limit', models.CharField(max_length=16)),
                ('memory_request', models.CharField(max_length=16)),
                ('memory_limit', models.CharField(max_length=16)),
                ('concurrency', models.PositiveSmallIntegerField()),
                ('max_chunk_size', models.PositiveIntegerField()),
                ('max_concurrent_jobs', models.PositiveSmallIntegerField(default=1)),
                ('max_inflight_tables', models.PositiveSmallIntegerField()),
                ('priority', models.SmallIntegerField(default=50)),
                ('extra_config', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'plan resource config',
                'verbose_name_plural': 'plan resource configs',
                'db_table': 'plan_resource_config',
            },
        ),
        migrations.CreateModel(
            name='CompanyResourceOverride',
            fields=[
                ('company', models.OneToOneField(
                    db_column='company_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    primary_key=True,
                    related_name='resource_override',
                    serialize=False,
                    to='elttool.company',
                )),
                ('max_pods', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('cpu_request', models.CharField(blank=True, max_length=16, null=True)),
                ('cpu_limit', models.CharField(blank=True, max_length=16, null=True)),
                ('memory_request', models.CharField(blank=True, max_length=16, null=True)),
                ('memory_limit', models.CharField(blank=True, max_length=16, null=True)),
                ('concurrency', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('max_chunk_size', models.PositiveIntegerField(blank=True, null=True)),
                ('max_concurrent_jobs', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('priority', models.SmallIntegerField(blank=True, null=True)),
                ('extra_config', models.JSONField(blank=True, null=True)),
                ('reason', models.TextField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'company resource override',
                'verbose_name_plural': 'company resource overrides',
                'db_table': 'company_resource_override',
            },
        ),
        migrations.RunPython(seed_plan_resource_config, migrations.RunPython.noop),
        migrations.RunPython(backfill_company_resource_overrides, migrations.RunPython.noop),
    ]
