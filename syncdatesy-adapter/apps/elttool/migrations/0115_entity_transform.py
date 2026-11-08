from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0114_pipeline_schedule_paused"),
    ]

    operations = [
        migrations.CreateModel(
            name="EntityTransform",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("service_name", models.CharField(max_length=150)),
                ("entity_name", models.CharField(max_length=150)),
                ("table_name", models.CharField(max_length=255)),
                ("version", models.PositiveIntegerField()),
                ("is_active", models.BooleanField(default=False)),
                ("mapping_json", models.JSONField(blank=True, null=True)),
                ("mapping_overrides", models.JSONField(blank=True, default=dict)),
                ("script_text", models.TextField(blank=True, null=True)),
                ("script_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("entry_point", models.CharField(blank=True, default="", max_length=255)),
                (
                    "run_order",
                    models.CharField(
                        choices=[
                            (
                                "filter_script_mapping",
                                "SAP filter, script, mapping on leftover columns, load",
                            ),
                            ("mapping_script", "Mapping first, then script"),
                        ],
                        default="filter_script_mapping",
                        max_length=64,
                    ),
                ),
                ("row_filter", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_transforms",
                        to="elttool.connection",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="entity_transforms_created",
                        to="elttool.user",
                    ),
                ),
            ],
            options={
                "db_table": "entity_transform",
            },
        ),
        migrations.AddIndex(
            model_name="entitytransform",
            index=models.Index(
                fields=["connection", "table_name", "is_active"],
                name="entity_trans_conn_tbl_active_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="entitytransform",
            index=models.Index(
                fields=["connection", "service_name", "entity_name"],
                name="entity_trans_conn_svc_ent_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="entitytransform",
            constraint=models.UniqueConstraint(
                fields=("connection", "service_name", "entity_name", "version"),
                name="entity_transform_conn_svc_entity_ver_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="entitytransform",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("connection", "service_name", "entity_name"),
                name="entity_transform_one_active_per_entity",
            ),
        ),
        migrations.AddField(
            model_name="migrationrecord",
            name="transform_version",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="EntityTransform version used for this run",
                null=True,
            ),
        ),
        migrations.RunPython(
            code=lambda apps, schema_editor: _add_transform_permission(apps),
            reverse_code=migrations.RunPython.noop,
        ),
    ]


def _add_transform_permission(apps):
    try:
        ContentType = apps.get_model("contenttypes", "ContentType")
        Permission = apps.get_model("auth", "Permission")
        ct, _ = ContentType.objects.get_or_create(
            app_label="elttool",
            model="entitytransform",
        )
        Permission.objects.get_or_create(
            codename="transform_manage",
            name="Can manage entity transform scripts",
            content_type=ct,
        )
    except Exception:
        # Do not fail schema creation if auth/contenttypes are unavailable.
        return
