from django.db import migrations, models


def deactivate_orphaned_source_configs(apps, schema_editor):
    SourceConfig = apps.get_model("elttool", "SourceConfig")
    Connection = apps.get_model("elttool", "Connection")

    for src in SourceConfig.objects.filter(is_active=True).iterator():
        has_non_deleted_connection = Connection.objects.filter(
            src_config_id=src.src_config_id
        ).exclude(status="D").exists()
        if not has_non_deleted_connection:
            src.is_active = False
            src.save(update_fields=["is_active"])


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0044_merge_20260215_1249"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="sourceconfig",
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name="destinationconfig",
            unique_together=set(),
        ),
        migrations.RunPython(
            deactivate_orphaned_source_configs,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="sourceconfig",
            constraint=models.UniqueConstraint(
                fields=("cmp", "name"),
                condition=models.Q(is_active=True),
                name="uniq_active_source_config_name_per_cmp",
            ),
        ),
        migrations.AddConstraint(
            model_name="destinationconfig",
            constraint=models.UniqueConstraint(
                fields=("cmp", "name"),
                condition=models.Q(is_active=True),
                name="uniq_active_destination_config_name_per_cmp",
            ),
        ),
    ]
