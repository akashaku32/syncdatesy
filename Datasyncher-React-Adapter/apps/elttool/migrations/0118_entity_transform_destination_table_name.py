from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0117_repair_entity_transform_idle_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitytransform",
            name="json_table_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="entitytransform",
            name="destination_table_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="entitytransform",
            name="destination_name_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("DEFAULT", "Default"),
                    ("JSON", "From mapping JSON"),
                    ("MANUAL", "Manual override"),
                ],
                default="DEFAULT",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="entitytransform",
            name="previous_destination_table_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="entitytransform",
            name="destination_rename_mode",
            field=models.CharField(
                blank=True,
                choices=[
                    ("create_new", "Create new table and full reload"),
                    ("rename_existing", "Rename existing table"),
                ],
                max_length=32,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="entitytransform",
            index=models.Index(
                fields=["connection", "destination_table_name", "is_active"],
                name="entity_tran_dest_name_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="entitytransform",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True))
                & models.Q(("destination_table_name__isnull", False))
                & ~models.Q(("destination_table_name", "")),
                fields=("connection", "destination_table_name"),
                name="entity_transform_active_dest_name_uniq",
            ),
        ),
    ]
