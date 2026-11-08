import json

from django.db import migrations


def seed_reap_stuck_pipeline_runs(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    schedule, _ = IntervalSchedule.objects.get_or_create(every=5, period="minutes")
    PeriodicTask.objects.update_or_create(
        name="reap_stuck_pipeline_runs",
        defaults={
            "interval": schedule,
            "task": "apps.elttool.tasks.reap_stuck_pipeline_runs",
            "enabled": True,
            "queue": "ops",
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
        },
    )


def remove_reap_stuck_pipeline_runs(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="reap_stuck_pipeline_runs").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0119_entity_transform_destination_schema_key"),
    ]

    operations = [
        migrations.RunPython(
            seed_reap_stuck_pipeline_runs,
            remove_reap_stuck_pipeline_runs,
        ),
    ]
