"""Guard rails when batches tied to a connection are referenced by a pipeline."""

from __future__ import annotations

from apps.elttool.models import MigrationBatch, PipelineNode


BATCH_IN_PIPELINE_DELETE_MSG = (
    "Cannot delete this batch while it is used in a pipeline. "
    "Remove it from Scheduling first."
)

CONNECTION_BATCHES_IN_PIPELINE_DELETE_MSG = (
    "Cannot delete this connection while one or more batches are used in a pipeline. "
    "Remove them from Scheduling first."
)


def build_batch_pipeline_usage_map(batches) -> dict[int, dict]:
    """Map batch_id -> {pipeline_id, pipeline_name} for batch nodes on a canvas."""
    batch_ids = [batch.id for batch in batches if getattr(batch, "id", None)]
    if not batch_ids:
        return {}

    usage: dict[int, dict] = {}
    nodes = (
        PipelineNode.objects.filter(batch_id__in=batch_ids, node_kind="batch")
        .select_related("pipeline", "batch")
        .order_by("batch_id", "id")
    )
    for node in nodes:
        if node.batch_id in usage:
            continue
        pipeline = node.pipeline
        usage[node.batch_id] = {
            "pipeline_id": node.pipeline_id,
            "pipeline_name": (pipeline.name if pipeline else None) or f"Pipeline {node.pipeline_id}",
            "batch_id": node.batch_id,
            "batch_name": node.batch.name if node.batch_id and node.batch else None,
        }
    return usage


def list_connection_pipeline_batch_usage(connection) -> list[dict]:
    """All real batches on this connection that appear on a pipeline canvas."""
    batch_ids = list(
        MigrationBatch.objects.filter(connection=connection)
        .exclude(name="Unassigned")
        .values_list("id", flat=True)
    )
    if not batch_ids:
        return []

    rows: list[dict] = []
    nodes = (
        PipelineNode.objects.filter(batch_id__in=batch_ids, node_kind="batch")
        .select_related("pipeline", "batch")
        .order_by("pipeline__name", "batch__name", "id")
    )
    seen: set[int] = set()
    for node in nodes:
        if not node.batch_id or node.batch_id in seen:
            continue
        seen.add(node.batch_id)
        pipeline = node.pipeline
        rows.append(
            {
                "batch_id": node.batch_id,
                "batch_name": node.batch.name if node.batch else f"Batch {node.batch_id}",
                "pipeline_id": node.pipeline_id,
                "pipeline_name": (pipeline.name if pipeline else None) or f"Pipeline {node.pipeline_id}",
            }
        )
    return rows


def connection_has_batches_in_pipeline(connection) -> bool:
    return bool(list_connection_pipeline_batch_usage(connection))


def batch_is_in_pipeline(batch) -> bool:
    return PipelineNode.objects.filter(batch=batch, node_kind="batch").exists()
