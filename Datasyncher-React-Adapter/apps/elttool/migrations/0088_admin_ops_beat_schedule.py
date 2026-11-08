import json

from django.db import migrations


def seed_admin_ops_beat_schedule(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=15,
        period="seconds",
    )
    PeriodicTask.objects.update_or_create(
        name="push_admin_ops_snapshot",
        defaults={
            "interval": schedule,
            "task": "apps.elttool.tasks.ops_monitoring_tasks.push_admin_ops_snapshot",
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
        },
    )


def remove_admin_ops_beat_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="push_admin_ops_snapshot").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0087_merge_20260616_0001"),
    ]

    operations = [
        migrations.RunPython(seed_admin_ops_beat_schedule, remove_admin_ops_beat_schedule),
    ]
