from django.db import migrations, models
import django.db.models.deletion


PLAN_SEED = [
    (1, 'Free', 1),
    (2, 'Standard', 2),
    (3, 'Enterprise', 3),
]

PLAN_NAME_BY_LEGACY = {
    'free': 1,
    'discounted': 2,
    'standard': 2,
    'enterprise': 3,
}


def seed_plans(apps, schema_editor):
    Plan = apps.get_model('elttool', 'Plan')
    for plan_id, name, pod_count in PLAN_SEED:
        Plan.objects.update_or_create(
            plan_id=plan_id,
            defaults={'name': name, 'pod_count': pod_count},
        )


def backfill_company_plan_ref(apps, schema_editor):
    Company = apps.get_model('elttool', 'Company')
    for company in Company.objects.all().iterator():
        key = (company.plan or 'Free').strip().lower()
        plan_id = PLAN_NAME_BY_LEGACY.get(key, 1)
        if company.plan_ref_id != plan_id:
            company.plan_ref_id = plan_id
            company.save(update_fields=['plan_ref_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0057_remove_migration_batch_models'),
    ]

    operations = [
        migrations.CreateModel(
            name='Plan',
            fields=[
                ('plan_id', models.PositiveSmallIntegerField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=32, unique=True)),
                ('pod_count', models.PositiveSmallIntegerField()),
            ],
            options={
                'verbose_name': 'plan',
                'verbose_name_plural': 'plans',
                'db_table': 'plans',
            },
        ),
        migrations.RunPython(seed_plans, migrations.RunPython.noop),
        migrations.AddField(
            model_name='company',
            name='pod_count',
            field=models.IntegerField(
                default=0,
                help_text='Extra pods above plan catalog cap; used only when greater than plan.pod_count.',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='plan_ref',
            field=models.ForeignKey(
                blank=True,
                db_column='plan_id',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='companies',
                to='elttool.plan',
                to_field='plan_id',
            ),
        ),
        migrations.RunPython(backfill_company_plan_ref, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='company',
            name='plan',
            field=models.CharField(
                choices=[
                    ('Free', 'Free'),
                    ('Discounted', 'Discounted'),
                    ('Standard', 'Standard'),
                    ('Enterprise', 'Enterprise'),
                ],
                default='Free',
                max_length=20,
            ),
        ),
    ]
