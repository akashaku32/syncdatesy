from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework import serializers

from apps.elttool.v1.pipeline_graph import (
    bridge_edges_on_delete,
    compute_delete_bridge_pairs,
    entry_node_ids,
    execution_node_ids_and_edges,
    validate_connected_to_start,
)


class PipelineGraphTests(SimpleTestCase):
    def _nodes(self):
        return [
            SimpleNamespace(id=1, node_kind="start", batch_id=None),
            SimpleNamespace(id=2, node_kind="batch", batch_id=10, batch=SimpleNamespace(name="f")),
            SimpleNamespace(id=3, node_kind="batch", batch_id=11, batch=SimpleNamespace(name="s")),
        ]

    def _edges(self):
        return [
            SimpleNamespace(from_node_id=1, to_node_id=2),
            SimpleNamespace(from_node_id=2, to_node_id=3),
        ]

    def test_entry_node_ids_from_start(self):
        self.assertEqual(entry_node_ids(self._nodes(), self._edges()), [2])

    def test_execution_levels_skip_start_edge(self):
        node_ids, pairs = execution_node_ids_and_edges(self._nodes(), self._edges())
        self.assertEqual(node_ids, [2, 3])
        self.assertEqual(pairs, [(2, 3)])

    def test_validate_connected_to_start_rejects_orphan(self):
        nodes = self._nodes() + [
            SimpleNamespace(id=4, node_kind="batch", batch_id=12, batch=SimpleNamespace(name="orphan")),
        ]
        with self.assertRaises(serializers.ValidationError):
            validate_connected_to_start(nodes, self._edges())

    def test_bridge_edges_on_delete_rejects_start(self):
        with self.assertRaises(serializers.ValidationError):
            bridge_edges_on_delete(None, self._nodes()[0])

    def test_compute_delete_bridge_pairs_simple_1_to_n(self):
        pairs = compute_delete_bridge_pairs(
            incoming_ids=[1],
            outgoing_ids=[3, 4],
            existing_edge_keys={(1, 2), (2, 3), (2, 4)},
            parent_outgoing_counts={1: 1, 2: 2},
            max_children_per_node=2,
        )
        self.assertEqual(pairs, [(1, 3), (1, 4)])

    def test_compute_delete_bridge_pairs_skips_multi_multi(self):
        pairs = compute_delete_bridge_pairs(
            incoming_ids=[10, 11],
            outgoing_ids=[3, 4],
            existing_edge_keys={(10, 2), (11, 2), (2, 3), (2, 4)},
            parent_outgoing_counts={10: 1, 11: 1, 2: 2},
            max_children_per_node=2,
        )
        self.assertEqual(pairs, [])

    def test_compute_delete_bridge_pairs_skips_over_capacity(self):
        # Parent already has deleted + sibling → bridging 2 succs would make 3 children.
        pairs = compute_delete_bridge_pairs(
            incoming_ids=[1],
            outgoing_ids=[3, 4],
            existing_edge_keys={(1, 2), (1, 5), (2, 3), (2, 4)},
            parent_outgoing_counts={1: 2, 2: 2},
            max_children_per_node=2,
        )
        self.assertEqual(pairs, [])
