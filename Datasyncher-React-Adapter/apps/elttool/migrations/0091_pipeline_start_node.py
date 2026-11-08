from django.db import migrations, models
import django.db.models.deletion


def backfill_start_nodes(apps, schema_editor):
    Pipeline = apps.get_model("elttool", "Pipeline")
    PipelineNode = apps.get_model("elttool", "PipelineNode")
    PipelineEdge = apps.get_model("elttool", "PipelineEdge")

    for pipeline in Pipeline.objects.all():
        if PipelineNode.objects.filter(pipeline=pipeline, node_kind="start").exists():
            continue
        start = PipelineNode.objects.create(
            pipeline=pipeline,
            node_kind="start",
            node_label="Start",
            x=0,
            y=0,
            order_index=0,
            connection=None,
            batch=None,
        )
        nodes = list(PipelineNode.objects.filter(pipeline=pipeline))
        edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
        batch_ids = {n.id for n in nodes if n.node_kind == "batch"}
        has_incoming = {e.to_node_id for e in edges if e.to_node_id in batch_ids}
        for node in nodes:
            if node.node_kind != "batch":
                continue
            if node.id in has_incoming:
                continue
            PipelineEdge.objects.get_or_create(
                pipeline=pipeline,
                from_node_id=start.id,
                to_node_id=node.id,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0090_merge_kubernetes_and_batch_technique"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelinenode",
            name="node_kind",
            field=models.CharField(
                choices=[("start", "start"), ("batch", "batch")],
                default="batch",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="pipelinenode",
            name="batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pipeline_nodes",
                to="elttool.migrationbatch",
            ),
        ),
        migrations.AlterField(
            model_name="pipelinenode",
            name="connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pipeline_nodes",
                to="elttool.connection",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="pipelinenode",
            name="uniq_pipeline_node_pipeline_batch",
        ),
        migrations.AddConstraint(
            model_name="pipelinenode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("node_kind", "batch")),
                fields=("pipeline", "batch"),
                name="uniq_pipeline_node_pipeline_batch",
            ),
        ),
        migrations.AddConstraint(
            model_name="pipelinenode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("node_kind", "start")),
                fields=("pipeline",),
                name="uniq_pipeline_start_node_per_pipeline",
            ),
        ),
        migrations.RunPython(backfill_start_nodes, migrations.RunPython.noop),
    ]
