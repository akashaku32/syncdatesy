from django.db import migrations, models


def backfill_status(apps, schema_editor):
    EntityTransform = apps.get_model("elttool", "EntityTransform")
    EntityTransform.objects.filter(is_active=True).update(status="validated")
    EntityTransform.objects.filter(is_active=False).update(status="validated")


class Migration(migrations.Migration):
    # Column may already exist on some envs; keep migration idempotent.
    atomic = False

    dependencies = [
        ("elttool", "0121_uniq_batch_one_pipeline_node"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="entitytransform",
                    name="status",
                    field=models.CharField(
                        choices=[("draft", "Draft"), ("validated", "Validated")],
                        db_index=True,
                        default="draft",
                        max_length=16,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE entity_transform "
                        "ADD COLUMN IF NOT EXISTS status varchar(16) NOT NULL DEFAULT 'draft';"
                        "CREATE INDEX IF NOT EXISTS entity_transform_status_idx "
                        "ON entity_transform (status);"
                    ),
                    reverse_sql=(
                        "DROP INDEX IF EXISTS entity_transform_status_idx;"
                        "ALTER TABLE entity_transform DROP COLUMN IF EXISTS status;"
                    ),
                ),
            ],
        ),
        migrations.RunPython(backfill_status, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddConstraint(
                    model_name="entitytransform",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(status="draft"),
                        fields=("connection", "service_name", "entity_name"),
                        name="entity_transform_one_draft_per_entity",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE UNIQUE INDEX IF NOT EXISTS entity_transform_one_draft_per_entity "
                        "ON entity_transform (connection_id, service_name, entity_name) "
                        "WHERE status = 'draft';"
                    ),
                    reverse_sql=(
                        "DROP INDEX IF EXISTS entity_transform_one_draft_per_entity;"
                    ),
                ),
            ],
        ),
    ]
