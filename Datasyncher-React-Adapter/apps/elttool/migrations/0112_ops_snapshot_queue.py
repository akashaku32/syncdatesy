from django.db import migrations


def route_ops_snapshot_off_pipeline_queue(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="push_admin_ops_snapshot").update(queue="ops")


def restore_ops_snapshot_default_queue(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="push_admin_ops_snapshot").update(queue=None)


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0111_source_file_mapping_timestamp_defaults"),
    ]

    operations = [
        migrations.RunPython(
            route_ops_snapshot_off_pipeline_queue,
            restore_ops_snapshot_default_queue,
        ),
    ]
