from django.db import migrations


def raise_free_plan_chunk_limit(apps, schema_editor):
    PlanResourceConfig = apps.get_model('elttool', 'PlanResourceConfig')
    PlanResourceConfig.objects.filter(plan_name='free').update(max_chunk_size=50000)


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0063_worker_node_pool_config'),
    ]

    operations = [
        migrations.RunPython(raise_free_plan_chunk_limit, migrations.RunPython.noop),
    ]
