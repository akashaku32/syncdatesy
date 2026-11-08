from django.db import migrations, models


def backfill_destination_schema_key(apps, schema_editor):
    from apps.elttool.models import Connection
    from apps.elttool.services.entity_transform_service import (
        destination_schema_key_for_connection,
    )

    EntityTransform = apps.get_model("elttool", "EntityTransform")
    connection_ids = (
        EntityTransform.objects.values_list("connection_id", flat=True).distinct()
    )
    for connection_id in connection_ids:
        connection = (
            Connection.objects.filter(pk=connection_id)
            .select_related("dst_config")
            .first()
        )
        if connection is None:
            continue
        key = destination_schema_key_for_connection(connection)
        if not key:
            continue
        EntityTransform.objects.filter(connection_id=connection_id).update(
            destination_schema_key=key
        )


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0118_entity_transform_destination_table_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitytransform",
            name="destination_schema_key",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.RunPython(
            backfill_destination_schema_key,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="entitytransform",
            name="entity_transform_active_dest_name_uniq",
        ),
        migrations.RemoveIndex(
            model_name="entitytransform",
            name="entity_tran_dest_name_idx",
        ),
        migrations.AddIndex(
            model_name="entitytransform",
            index=models.Index(
                fields=["destination_schema_key", "destination_table_name", "is_active"],
                name="entity_tran_schema_dest_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="entitytransform",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True))
                & models.Q(("destination_table_name__isnull", False))
                & ~models.Q(("destination_table_name", ""))
                & models.Q(("destination_schema_key__isnull", False))
                & ~models.Q(("destination_schema_key", "")),
                fields=("destination_schema_key", "destination_table_name"),
                name="entity_transform_active_dest_name_uniq",
            ),
        ),
    ]
