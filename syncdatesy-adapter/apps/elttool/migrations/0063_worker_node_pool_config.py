from django.db import migrations, models
import django.db.models.deletion


WORKER_NODE_POOL_SEED = [
    {
        'pool_name': 'worker-standard',
        'machine_type': 'e2-standard-4',
        'allocatable_memory': '13Gi',
        'allocatable_cpu': '3800m',
        'node_selector': {'pool': 'worker-standard'},
        'taint_key': None,
        'taint_value': None,
    },
    {
        'pool_name': 'worker-premium',
        'machine_type': 'e2-standard-8',
        'allocatable_memory': '26Gi',
        'allocatable_cpu': '7800m',
        'node_selector': {'pool': 'worker-premium'},
        'taint_key': 'pool',
        'taint_value': 'worker-premium',
    },
    {
        'pool_name': 'worker-enterprise',
        'machine_type': 'e2-standard-16',
        'allocatable_memory': '58Gi',
        'allocatable_cpu': '15800m',
        'node_selector': {'pool': 'worker-enterprise'},
        'taint_key': 'pool',
        'taint_value': 'worker-enterprise',
    },
]

PLAN_WORKER_POOL_SEED = {
    'free': 'worker-standard',
    'standard': 'worker-standard',
    'discounted': 'worker-standard',
    'premium': 'worker-premium',
    'enterprise': 'worker-enterprise',
}


def seed_worker_node_pools(apps, schema_editor):
    WorkerNodePoolConfig = apps.get_model('elttool', 'WorkerNodePoolConfig')
    for row in WORKER_NODE_POOL_SEED:
        defaults = {k: v for k, v in row.items() if k != 'pool_name'}
        WorkerNodePoolConfig.objects.update_or_create(
            pool_name=row['pool_name'],
            defaults=defaults,
        )


def seed_plan_worker_pools(apps, schema_editor):
    PlanResourceConfig = apps.get_model('elttool', 'PlanResourceConfig')
    WorkerNodePoolConfig = apps.get_model('elttool', 'WorkerNodePoolConfig')
    for plan_name, pool_name in PLAN_WORKER_POOL_SEED.items():
        pool = WorkerNodePoolConfig.objects.filter(pool_name=pool_name).first()
        if pool:
            PlanResourceConfig.objects.filter(plan_name=plan_name).update(
                worker_pool_name=pool,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0061_chunk_pct_and_max_concurrency'),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkerNodePoolConfig',
            fields=[
                ('pool_name', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('machine_type', models.CharField(max_length=64)),
                ('allocatable_memory', models.CharField(max_length=16)),
                ('allocatable_cpu', models.CharField(max_length=16)),
                ('overhead_memory', models.CharField(default='2Gi', max_length=16)),
                ('overhead_cpu', models.CharField(default='200m', max_length=16)),
                ('max_pods_per_node', models.IntegerField(default=1)),
                ('node_selector', models.JSONField(default=dict)),
                ('taint_key', models.CharField(blank=True, max_length=128, null=True)),
                ('taint_value', models.CharField(blank=True, max_length=128, null=True)),
                ('taint_effect', models.CharField(default='NoSchedule', max_length=32)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'worker_node_pool_config',
            },
        ),
        migrations.RunPython(seed_worker_node_pools, migrations.RunPython.noop),
        migrations.AddField(
            model_name='planresourceconfig',
            name='worker_pool_name',
            field=models.ForeignKey(
                blank=True,
                db_column='worker_pool_name',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='elttool.workernodepoolconfig',
                to_field='pool_name',
            ),
        ),
        migrations.RunPython(seed_plan_worker_pools, migrations.RunPython.noop),
        migrations.AddField(
            model_name='companyresourceoverride',
            name='worker_pool_name',
            field=models.ForeignKey(
                blank=True,
                db_column='worker_pool_name',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='elttool.workernodepoolconfig',
                to_field='pool_name',
            ),
        ),
    ]
