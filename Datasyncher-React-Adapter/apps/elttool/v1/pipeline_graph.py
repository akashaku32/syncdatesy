"""Pipeline DAG helpers: Start node, connectivity, execution graph."""

from __future__ import annotations

from collections import defaultdict, deque
from types import SimpleNamespace
from typing import Iterable

from rest_framework import serializers

from apps.elttool.models import Pipeline, PipelineEdge, PipelineNode

NODE_KIND_START = "start"
NODE_KIND_BATCH = "batch"


def is_start_node(node) -> bool:
    return getattr(node, "node_kind", NODE_KIND_BATCH) == NODE_KIND_START


def is_batch_node(node) -> bool:
    return not is_start_node(node)


def batch_nodes(nodes: Iterable) -> list:
    return [n for n in nodes if is_batch_node(n)]


def get_start_node(nodes: Iterable):
    for node in nodes:
        if is_start_node(node):
            return node
    return None


def ensure_start_node(pipeline: Pipeline) -> PipelineNode:
    """Every pipeline has exactly one Start node (scheduling entry point)."""
    existing = PipelineNode.objects.filter(
        pipeline=pipeline, node_kind=NODE_KIND_START
    ).first()
    if existing:
        return existing

    start = PipelineNode.objects.create(
        pipeline=pipeline,
        node_kind=NODE_KIND_START,
        node_label="Start",
        x=0,
        y=0,
        order_index=0,
    )
    _backfill_start_edges(pipeline, start)
    return start


def _backfill_start_edges(pipeline: Pipeline, start: PipelineNode) -> None:
    """Connect legacy entry batches (no incoming edges) to Start."""
    nodes = list(PipelineNode.objects.filter(pipeline=pipeline))
    edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
    batch_ids = {n.id for n in nodes if is_batch_node(n)}
    has_incoming = {
        e.to_node_id for e in edges if e.to_node_id in batch_ids
    }
    for node in nodes:
        if not is_batch_node(node):
            continue
        if node.id in has_incoming:
            continue
        PipelineEdge.objects.get_or_create(
            pipeline=pipeline,
            from_node=start,
            to_node=node,
        )


def entry_node_ids(nodes: Iterable, edges: Iterable) -> list[int]:
    """Batch nodes directly reachable from Start (first execution level)."""
    start = get_start_node(nodes)
    if not start:
        return _legacy_root_node_ids(nodes, edges)
    return [
        e.to_node_id
        for e in edges
        if e.from_node_id == start.id and is_batch_node_id(e.to_node_id, nodes)
    ]


def is_batch_node_id(node_id: int, nodes: Iterable) -> bool:
    node_map = {n.id: n for n in nodes}
    node = node_map.get(node_id)
    return node is not None and is_batch_node(node)


def _legacy_root_node_ids(nodes, edges) -> list[int]:
    if not nodes:
        return []
    batch_ids = {n.id for n in nodes if is_batch_node(n)}
    has_incoming = {e.to_node_id for e in edges if e.to_node_id in batch_ids}
    return [n.id for n in nodes if is_batch_node(n) and n.id not in has_incoming]


def execution_node_ids_and_edges(nodes: Iterable, edges: Iterable) -> tuple[list[int], list[tuple[int, int]]]:
    """
    Batch-only execution graph. Edges from Start are ignored (those targets are level 0).
    """
    ids = [n.id for n in nodes if is_batch_node(n)]
    batch_id_set = set(ids)
    start_ids = {n.id for n in nodes if is_start_node(n)}
    pairs: list[tuple[int, int]] = []
    for edge in edges:
        if edge.to_node_id not in batch_id_set:
            continue
        if edge.from_node_id in start_ids:
            continue
        if edge.from_node_id in batch_id_set:
            pairs.append((edge.from_node_id, edge.to_node_id))
    return ids, pairs


def validate_connected_to_start(nodes: Iterable, edges: Iterable) -> None:
    start = get_start_node(nodes)
    if not start:
        raise serializers.ValidationError(
            "Pipeline is missing a Start node. Re-open the pipeline to repair it."
        )
    batches = batch_nodes(nodes)
    if not batches:
        raise serializers.ValidationError("Add at least one batch and connect it to Start.")

    adj: dict[int, list[int]] = defaultdict(list)
    for edge in edges:
        adj[edge.from_node_id].append(edge.to_node_id)

    reachable: set[int] = set()
    queue = deque([start.id])
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)

    disconnected = [n.batch.name if n.batch_id else n.node_label for n in batches if n.id not in reachable]
    if disconnected:
        names = ", ".join(disconnected[:5])
        suffix = "…" if len(disconnected) > 5 else ""
        raise serializers.ValidationError(
            f"Connect these batches to Start: {names}{suffix}."
        )


def disconnected_batch_node_ids(nodes: Iterable, edges: Iterable) -> list[int]:
    """Batch node IDs not reachable from Start."""
    start = get_start_node(nodes)
    batches = batch_nodes(nodes)
    if not start or not batches:
        return [n.id for n in batches]

    adj: dict[int, list[int]] = defaultdict(list)
    for edge in edges:
        adj[edge.from_node_id].append(edge.to_node_id)

    reachable: set[int] = set()
    queue = deque([start.id])
    seen = {start.id}
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                if is_batch_node_id(nxt, nodes):
                    reachable.add(nxt)
                queue.append(nxt)
    return [n.id for n in batches if n.id not in reachable]


def pipeline_graph_fingerprint(nodes: Iterable, edges: Iterable) -> str:
    """Stable fingerprint for graph structure (batch ids + edges)."""
    batch_part = sorted(
        f"{n.id}:{n.batch_id or 0}" for n in nodes if is_batch_node(n)
    )
    edge_part = sorted(
        f"{e.from_node_id}->{e.to_node_id}" for e in edges
    )
    return f"{','.join(batch_part)}|{','.join(edge_part)}"


def build_validated_graph_snapshot(nodes: Iterable, edges: Iterable) -> dict:
    """Build a persisted published-graph snapshot from the full connected canvas."""
    start = get_start_node(nodes)
    if not start:
        raise serializers.ValidationError(
            "Pipeline is missing a Start node. Re-open the pipeline to repair it."
        )
    validate_connected_to_start(nodes, edges)

    node_list = list(nodes)
    edge_list = list(edges)
    batch_node_list = batch_nodes(node_list)
    snapshot_nodes = []
    for node in batch_node_list:
        snapshot_nodes.append({
            'node_id': node.id,
            'batch_id': node.batch_id,
            'connection_id': node.connection_id,
            'execution_order': node.execution_order,
        })

    snapshot_edges = [
        {'from_node_id': e.from_node_id, 'to_node_id': e.to_node_id}
        for e in edge_list
    ]

    return {
        'version': 1,
        'fingerprint': pipeline_graph_fingerprint(node_list, edge_list),
        'start_node_id': start.id,
        'nodes': snapshot_nodes,
        'edges': snapshot_edges,
    }


class ValidatedGraphResolutionError(Exception):
    """Raised when a published graph cannot be resolved to live pipeline nodes."""


def load_validated_graph_for_execution(pipeline: Pipeline) -> tuple[list, list]:
    """
    Resolve the published validated_graph snapshot to live PipelineNode rows and
    edge-like objects for execution.
    """
    snapshot = pipeline.validated_graph
    if not snapshot or not isinstance(snapshot, dict):
        raise ValidatedGraphResolutionError(
            'Pipeline has no published graph. Validate the pipeline before running.'
        )

    live_nodes = list(
        PipelineNode.objects.select_related(
            'batch', 'connection', 'connection__src_config', 'connection__dst_config',
        ).filter(pipeline=pipeline).order_by('order_index')
    )
    live_node_by_id = {n.id: n for n in live_nodes}
    live_node_by_batch = {
        n.batch_id: n for n in live_nodes if is_batch_node(n) and n.batch_id
    }

    resolved_nodes: list = []
    resolved_node_ids: set[int] = set()
    for item in snapshot.get('nodes') or []:
        node_id = item.get('node_id')
        batch_id = item.get('batch_id')
        node = live_node_by_id.get(node_id) if node_id else None
        if node is None and batch_id:
            node = live_node_by_batch.get(batch_id)
        if node is None or not is_batch_node(node):
            label = batch_id or node_id
            raise ValidatedGraphResolutionError(
                f'Published batch node {label} is missing from the pipeline canvas.'
            )
        resolved_nodes.append(node)
        resolved_node_ids.add(node.id)

    start_id = snapshot.get('start_node_id')
    start_node = live_node_by_id.get(start_id) if start_id else get_start_node(live_nodes)
    if start_node and start_node.id not in resolved_node_ids:
        resolved_nodes.insert(0, start_node)

    resolved_edges: list = []
    for item in snapshot.get('edges') or []:
        from_id = item.get('from_node_id')
        to_id = item.get('to_node_id')
        from_node = live_node_by_id.get(from_id)
        to_node = live_node_by_id.get(to_id)
        if from_node is None or to_node is None:
            snap_nodes = {n['node_id']: n for n in (snapshot.get('nodes') or [])}
            from_batch = (snap_nodes.get(from_id) or {}).get('batch_id')
            to_batch = (snap_nodes.get(to_id) or {}).get('batch_id')
            if from_id == start_id:
                from_node = start_node
            elif from_batch:
                from_node = live_node_by_batch.get(from_batch)
            if to_batch:
                to_node = live_node_by_batch.get(to_batch)
        if from_node is None or to_node is None:
            raise ValidatedGraphResolutionError(
                f'Published edge {from_id}->{to_id} cannot be resolved on the canvas.'
            )
        resolved_edges.append(
            SimpleNamespace(
                from_node_id=from_node.id,
                to_node_id=to_node.id,
                from_node=from_node,
                to_node=to_node,
            )
        )

    return resolved_nodes, resolved_edges


def restore_canvas_to_published_graph(pipeline: Pipeline) -> None:
    """
    Drop draft/extra canvas nodes and edges so the live canvas matches the
    last published validated_graph snapshot.
    """
    snapshot = pipeline.validated_graph
    if not snapshot or not isinstance(snapshot, dict):
        raise serializers.ValidationError(
            'No published flow to restore. Validate the pipeline first.'
        )

    ensure_start_node(pipeline)
    live_nodes = list(
        PipelineNode.objects.filter(pipeline=pipeline).select_related('batch')
    )

    published_batch_node_ids: set[int] = set()
    for item in snapshot.get('nodes') or []:
        if not isinstance(item, dict):
            continue
        try:
            published_batch_node_ids.add(int(item['node_id']))
        except (TypeError, ValueError):
            continue

    missing = published_batch_node_ids - {n.id for n in batch_nodes(live_nodes)}
    if missing:
        sample = ', '.join(str(nid) for nid in sorted(missing)[:5])
        suffix = '…' if len(missing) > 5 else ''
        raise serializers.ValidationError(
            f'Cannot restore: published batch node(s) missing from canvas: {sample}{suffix}.'
        )

    published_edge_pairs: set[tuple[int, int]] = set()
    for item in snapshot.get('edges') or []:
        if not isinstance(item, dict):
            continue
        try:
            published_edge_pairs.add(
                (int(item['from_node_id']), int(item['to_node_id']))
            )
        except (KeyError, TypeError, ValueError):
            continue

    from apps.elttool.v1.batch_views import _remove_batch_job

    for node in list(batch_nodes(live_nodes)):
        if node.id in published_batch_node_ids:
            continue
        batch = node.batch
        PipelineEdge.objects.filter(pipeline=pipeline, from_node=node).delete()
        PipelineEdge.objects.filter(pipeline=pipeline, to_node=node).delete()
        node.delete()
        if batch and not PipelineNode.objects.filter(batch=batch).exists():
            try:
                _remove_batch_job(batch)
            except Exception:
                pass

    live_node_by_id = {
        n.id: n for n in PipelineNode.objects.filter(pipeline=pipeline)
    }

    for edge in list(PipelineEdge.objects.filter(pipeline=pipeline)):
        pair = (edge.from_node_id, edge.to_node_id)
        if pair not in published_edge_pairs:
            edge.delete()

    for from_id, to_id in published_edge_pairs:
        if from_id not in live_node_by_id or to_id not in live_node_by_id:
            continue
        PipelineEdge.objects.get_or_create(
            pipeline=pipeline,
            from_node_id=from_id,
            to_node_id=to_id,
        )

    nodes = list(PipelineNode.objects.filter(pipeline=pipeline))
    edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
    current_fp = pipeline_graph_fingerprint(nodes, edges)
    published_fp = snapshot.get('fingerprint')
    if published_fp and current_fp != published_fp:
        raise serializers.ValidationError(
            'Restore incomplete: canvas still differs from the published flow.'
        )


def compute_delete_bridge_pairs(
    incoming_ids: list[int],
    outgoing_ids: list[int],
    existing_edge_keys: set[tuple[int, int]],
    parent_outgoing_counts: dict[int, int],
    max_children_per_node: int = 2,
) -> list[tuple[int, int]]:
    """Decide which pred→succ shortcuts to create when deleting a node.

    Simple cases (1→1, 1→N, or N→1) are bridged when every parent stays within
    max_children_per_node after the remap.

    Complicated cases (N parents AND M children, both > 1) return no bridges —
    callers should leave the graph disconnected so the user reconnects manually.
    """
    if not incoming_ids or not outgoing_ids:
        return []

    # Multi×multi fan-out is ambiguous under the 2-children rule.
    if len(incoming_ids) > 1 and len(outgoing_ids) > 1:
        return []

    proposed: list[tuple[int, int]] = []
    for from_id in incoming_ids:
        for to_id in outgoing_ids:
            if from_id == to_id:
                continue
            key = (from_id, to_id)
            if key in existing_edge_keys:
                continue
            proposed.append(key)

    if not proposed:
        return []

    # Capacity: each parent loses the edge to the deleted node, then gains its
    # new bridges. Reject the whole remap if any parent would exceed the cap.
    new_by_parent: dict[int, int] = defaultdict(int)
    for from_id, _to_id in proposed:
        new_by_parent[from_id] += 1

    for from_id in incoming_ids:
        current_out = parent_outgoing_counts.get(from_id, 0)
        final_out = current_out - 1 + new_by_parent.get(from_id, 0)
        if final_out > max_children_per_node:
            return []

    return proposed


def bridge_edges_on_delete(
    pipeline,
    node: PipelineNode,
    max_children_per_node: int = 2,
) -> list[tuple[int, int]]:
    """Bridge preds→succs only when the remap is simple and legal.

    Returns the list of newly created (from_node_id, to_node_id) pairs.
    Empty list means no auto-rewire (orphaned neighbors; user reconnects).
    """
    if is_start_node(node):
        raise serializers.ValidationError("The Start node cannot be deleted.")

    incoming = list(PipelineEdge.objects.filter(pipeline=pipeline, to_node=node))
    outgoing = list(PipelineEdge.objects.filter(pipeline=pipeline, from_node=node))
    incoming_ids = [e.from_node_id for e in incoming]
    outgoing_ids = [e.to_node_id for e in outgoing]

    all_edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
    existing_keys = {(e.from_node_id, e.to_node_id) for e in all_edges}
    parent_out_counts: dict[int, int] = defaultdict(int)
    for e in all_edges:
        parent_out_counts[e.from_node_id] += 1

    pairs = compute_delete_bridge_pairs(
        incoming_ids,
        outgoing_ids,
        existing_keys,
        dict(parent_out_counts),
        max_children_per_node=max_children_per_node,
    )

    created: list[tuple[int, int]] = []
    for from_node_id, to_node_id in pairs:
        _, was_created = PipelineEdge.objects.get_or_create(
            pipeline=pipeline,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
        )
        if was_created:
            created.append((from_node_id, to_node_id))
    return created
