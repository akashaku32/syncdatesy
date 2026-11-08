from django.db import migrations, models


def dedupe_batch_nodes(apps, schema_editor):
    """Keep the newest batch node per batch; remove older duplicates across flows."""
    PipelineNode = apps.get_model("elttool", "PipelineNode")
    PipelineEdge = apps.get_model("elttool", "PipelineEdge")

    batch_ids = (
        PipelineNode.objects.filter(node_kind="batch", batch_id__isnull=False)
        .values_list("batch_id", flat=True)
        .distinct()
    )
    for batch_id in batch_ids:
        nodes = list(
            PipelineNode.objects.filter(node_kind="batch", batch_id=batch_id).order_by(
                "-id"
            )
        )
        if len(nodes) <= 1:
            continue
        keep = nodes[0]
        for node in nodes[1:]:
            PipelineEdge.objects.filter(from_node_id=node.id).delete()
            PipelineEdge.objects.filter(to_node_id=node.id).delete()
            if node.id != keep.id:
                node.delete()


class Migration(migrations.Migration):
    # RunPython deletes fire triggers; ALTER in the same txn fails with
    # "cannot ALTER TABLE ... because it has pending trigger events".
    atomic = False

    dependencies = [
        ("elttool", "0120_reap_stuck_pipeline_runs"),
    ]

    operations = [
        migrations.RunPython(dedupe_batch_nodes, migrations.RunPython.noop),
        # Production may be missing the old per-pipeline unique; drop IF EXISTS.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="pipelinenode",
                    name="uniq_pipeline_node_pipeline_batch",
                ),
                migrations.AddConstraint(
                    model_name="pipelinenode",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(node_kind="batch"),
                        fields=("batch",),
                        name="uniq_pipeline_node_batch_global",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE pipeline_node "
                        "DROP CONSTRAINT IF EXISTS uniq_pipeline_node_pipeline_batch;"
                    ),
                    reverse_sql=(
                        "CREATE UNIQUE INDEX uniq_pipeline_node_pipeline_batch "
                        "ON pipeline_node (pipeline_id, batch_id) "
                        "WHERE node_kind = 'batch';"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE UNIQUE INDEX uniq_pipeline_node_batch_global "
                        "ON pipeline_node (batch_id) "
                        "WHERE node_kind = 'batch';"
                    ),
                    reverse_sql=(
                        "DROP INDEX IF EXISTS uniq_pipeline_node_batch_global;"
                    ),
                ),
            ],
        ),
    ]
