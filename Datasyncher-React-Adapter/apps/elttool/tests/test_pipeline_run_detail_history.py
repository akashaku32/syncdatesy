"""Historical run detail must not include batches added after the run."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.elttool.v1.pipeline_views import (
    _build_pipeline_run_detail,
    _pipeline_run_participating_node_ids,
)


class PipelineRunParticipatingNodesTests(SimpleTestCase):
    def test_participating_ids_prefer_frozen_list(self):
        meta = {
            'participating_node_ids': [27, 28, 29],
            'nodes': {
                '27': {'status': 'running', 'batch_name': 'bud_bat'},
            },
            'graph_source': 'validated',
        }
        pipeline = SimpleNamespace(
            validated_graph={
                'nodes': [
                    {'node_id': 27},
                    {'node_id': 28},
                    {'node_id': 29},
                    {'node_id': 43},
                ],
            },
        )
        live_nodes = [
            SimpleNamespace(id=27, node_kind='batch'),
            SimpleNamespace(id=99, node_kind='batch'),
        ]
        ids = _pipeline_run_participating_node_ids(meta, pipeline, live_nodes)
        self.assertEqual(ids, [27, 28, 29])
        self.assertNotIn(43, ids)
        self.assertNotIn(99, ids)

    def test_participating_ids_prefer_metadata_over_live_canvas(self):
        meta = {
            'nodes': {
                '27': {'status': 'completed', 'batch_name': 'bud_bat'},
                '28': {'status': 'completed', 'batch_name': 'acc_bat'},
            },
            'executed_node_ids': [27, 28],
        }
        pipeline = SimpleNamespace(validated_graph=None)
        live_nodes = [
            SimpleNamespace(id=27, node_kind='batch'),
            SimpleNamespace(id=28, node_kind='batch'),
            SimpleNamespace(id=43, node_kind='batch'),  # added later
        ]
        ids = _pipeline_run_participating_node_ids(meta, pipeline, live_nodes)
        self.assertEqual(ids, [27, 28])
        self.assertNotIn(43, ids)

    def test_validated_run_with_partial_meta_does_not_expand_to_newer_published(self):
        """
        Legacy runs without participating_node_ids must not pick up batches
        that were published after the run (e.g. node 43 added later).
        """
        meta = {
            'graph_source': 'validated',
            'nodes': {
                '27': {'status': 'running', 'batch_name': 'bud_bat'},
                '28': {'status': 'running', 'batch_name': 'acc_bat'},
            },
            'current_node_ids': [27, 28],
        }
        pipeline = SimpleNamespace(
            validated_graph={
                'nodes': [
                    {'node_id': 27},
                    {'node_id': 28},
                    {'node_id': 29},
                    {'node_id': 30},
                    {'node_id': 43},  # added / republished after this run
                ],
            },
        )
        live_nodes = [
            SimpleNamespace(id=27, node_kind='batch'),
            SimpleNamespace(id=28, node_kind='batch'),
            SimpleNamespace(id=29, node_kind='batch'),
            SimpleNamespace(id=30, node_kind='batch'),
            SimpleNamespace(id=43, node_kind='batch'),
            SimpleNamespace(id=45, node_kind='batch'),
        ]
        ids = _pipeline_run_participating_node_ids(meta, pipeline, live_nodes)
        self.assertEqual(ids, [27, 28])
        self.assertNotIn(43, ids)
        self.assertNotIn(45, ids)

    def test_validated_run_empty_meta_falls_back_to_published(self):
        meta = {
            'graph_source': 'validated',
            'nodes': {},
        }
        pipeline = SimpleNamespace(
            validated_graph={
                'nodes': [
                    {'node_id': 27},
                    {'node_id': 28},
                    {'node_id': 29},
                ],
            },
        )
        live_nodes = [
            SimpleNamespace(id=27, node_kind='batch'),
            SimpleNamespace(id=28, node_kind='batch'),
            SimpleNamespace(id=29, node_kind='batch'),
            SimpleNamespace(id=99, node_kind='batch'),
        ]
        ids = _pipeline_run_participating_node_ids(
            meta, pipeline, live_nodes, run_status='running'
        )
        self.assertEqual(ids, [27, 28, 29])
        self.assertNotIn(99, ids)

    def test_completed_run_does_not_expand_to_current_published(self):
        meta = {
            'graph_source': 'validated',
            'nodes': {},
        }
        pipeline = SimpleNamespace(
            validated_graph={
                'nodes': [
                    {'node_id': 27},
                    {'node_id': 28},
                    {'node_id': 99},  # added later
                ],
            },
        )
        live_nodes = [
            SimpleNamespace(id=27, node_kind='batch'),
            SimpleNamespace(id=28, node_kind='batch'),
            SimpleNamespace(id=99, node_kind='batch'),
        ]
        ids = _pipeline_run_participating_node_ids(
            meta, pipeline, live_nodes, run_status='completed'
        )
        self.assertEqual(ids, [])
        self.assertNotIn(99, ids)

    @patch('apps.elttool.services.migration_status_builder.build_migration_status')
    @patch('apps.elttool.v1.pipeline_views.PipelineEdge.objects.filter')
    @patch('apps.elttool.v1.pipeline_views.PipelineNode.objects.filter')
    def test_run_detail_hides_batches_added_after_run(
        self,
        mock_node_filter,
        mock_edge_filter,
        mock_build_status,
    ):
        mock_build_status.return_value = (None, None, 200)

        bud = SimpleNamespace(
            id=27,
            node_kind='batch',
            batch_id=1,
            connection_id=9,
            node_label='bud_bat',
            execution_order='parallel',
            order_index=1,
            batch=SimpleNamespace(
                name='bud_bat',
                execution_order='parallel',
                tables=SimpleNamespace(count=lambda: 3),
            ),
        )
        invent = SimpleNamespace(
            id=43,
            node_kind='batch',
            batch_id=99,
            connection_id=9,
            node_label='bud_account_batch',
            execution_order='parallel',
            order_index=5,
            batch=SimpleNamespace(
                name='bud_account_batch',
                execution_order='parallel',
                tables=SimpleNamespace(count=lambda: 4),
            ),
        )
        qs = MagicMockList([bud, invent])
        mock_node_filter.return_value = qs
        mock_edge_filter.return_value = []

        pipeline = SimpleNamespace(id=11, validated_graph=None)
        run = SimpleNamespace(
            id=350,
            pipeline=pipeline,
            pipeline_id=11,
            status='completed',
            started_at=None,
            finished_at=None,
            metadata={
                'nodes': {
                    '27': {
                        'status': 'completed',
                        'batch_name': 'bud_bat',
                        'migration_session_id': 868,
                        'table_count': 3,
                    },
                },
                'executed_node_ids': [27],
                'current_node_ids': [],
            },
        )

        detail = _build_pipeline_run_detail(run, include_migration_status=False)
        node_ids = [n['node_id'] for n in detail['nodes']]
        self.assertEqual(node_ids, [27])
        self.assertEqual(detail['overall']['nodes_total'], 1)
        self.assertEqual(detail['overall']['nodes_completed'], 1)
        self.assertIn('edges', detail)


class PipelineRunEdgesForDetailTests(SimpleTestCase):
    def test_prefers_frozen_edge_pairs(self):
        from apps.elttool.v1.pipeline_views import _pipeline_run_edges_for_detail

        meta = {
            'edge_pairs': [
                {'from_node_id': 1, 'to_node_id': 27},
                {'from_node_id': 27, 'to_node_id': 28},
            ],
            'graph_source': 'validated',
        }
        pipeline = SimpleNamespace(
            validated_graph={
                'edges': [
                    {'from_node_id': 1, 'to_node_id': 27},
                    {'from_node_id': 27, 'to_node_id': 99},  # newer published
                ],
            },
        )
        live = [
            SimpleNamespace(from_node_id=1, to_node_id=27),
            SimpleNamespace(from_node_id=27, to_node_id=43),
        ]
        edges = _pipeline_run_edges_for_detail(
            meta, pipeline, {27, 28}, live, start_node_id=1,
        )
        self.assertEqual(
            edges[0],
            [
                {'from_node_id': 1, 'to_node_id': 27},
                {'from_node_id': 27, 'to_node_id': 28},
            ],
        )
        self.assertTrue(edges[1])

    def test_falls_back_to_published_for_legacy_validated_runs(self):
        from apps.elttool.v1.pipeline_views import _pipeline_run_edges_for_detail

        meta = {'graph_source': 'validated'}
        pipeline = SimpleNamespace(
            validated_graph={
                'edges': [
                    {'from_node_id': 1, 'to_node_id': 27},
                    {'from_node_id': 27, 'to_node_id': 28},
                    {'from_node_id': 28, 'to_node_id': 43},  # outside participating
                ],
            },
        )
        edges, frozen = _pipeline_run_edges_for_detail(
            meta, pipeline, {27, 28}, [], start_node_id=1,
        )
        self.assertEqual(
            edges,
            [
                {'from_node_id': 1, 'to_node_id': 27},
                {'from_node_id': 27, 'to_node_id': 28},
            ],
        )
        self.assertFalse(frozen)

    def test_prefers_denser_live_edges_over_sparse_published(self):
        from apps.elttool.v1.pipeline_views import _pipeline_run_edges_for_detail

        meta = {'graph_source': 'validated'}
        pipeline = SimpleNamespace(
            validated_graph={
                'edges': [
                    {'from_node_id': 1, 'to_node_id': 27},
                    {'from_node_id': 1, 'to_node_id': 28},
                ],
            },
        )
        live = [
            SimpleNamespace(from_node_id=1, to_node_id=27),
            SimpleNamespace(from_node_id=1, to_node_id=28),
            SimpleNamespace(from_node_id=27, to_node_id=29),
            SimpleNamespace(from_node_id=28, to_node_id=30),
        ]
        edges, frozen = _pipeline_run_edges_for_detail(
            meta, pipeline, {27, 28, 29, 30}, live, start_node_id=1,
        )
        self.assertFalse(frozen)
        self.assertEqual(len(edges), 4)
        self.assertIn({'from_node_id': 27, 'to_node_id': 29}, edges)

    def test_sparse_frozen_edges_are_not_trusted(self):
        from apps.elttool.v1.pipeline_views import _pipeline_run_edges_for_detail

        # Old/bad freeze: only Start→two roots; rest of run would be orphans.
        meta = {
            'graph_source': 'validated',
            'edge_pairs': [
                {'from_node_id': 1, 'to_node_id': 27},
                {'from_node_id': 1, 'to_node_id': 28},
            ],
        }
        pipeline = SimpleNamespace(
            validated_graph={
                'edges': [
                    {'from_node_id': 1, 'to_node_id': 27},
                    {'from_node_id': 1, 'to_node_id': 28},
                ],
            },
        )
        live = [
            SimpleNamespace(from_node_id=1, to_node_id=27),
            SimpleNamespace(from_node_id=1, to_node_id=28),
            SimpleNamespace(from_node_id=27, to_node_id=29),
            SimpleNamespace(from_node_id=28, to_node_id=30),
            SimpleNamespace(from_node_id=29, to_node_id=31),
        ]
        edges, frozen = _pipeline_run_edges_for_detail(
            meta,
            pipeline,
            {27, 28, 29, 30, 31},
            live,
            start_node_id=1,
        )
        self.assertFalse(frozen)
        self.assertGreaterEqual(len(edges), 4)
        self.assertIn({'from_node_id': 27, 'to_node_id': 29}, edges)

    def test_infers_edges_by_batch_id_when_node_ids_changed(self):
        from apps.elttool.v1.pipeline_views import _infer_legacy_run_edges

        # Run stored old node ids 27/28; canvas now has 127/128 for same batches.
        meta = {
            'graph_source': 'validated',
            'nodes': {
                '27': {'batch_id': 101, 'batch_name': 'bud_bat'},
                '28': {'batch_id': 102, 'batch_name': 'acc_bat'},
                '29': {'batch_id': 103, 'batch_name': 'dimension_bat'},
            },
        }
        pipeline = SimpleNamespace(validated_graph={'edges': []})
        live_nodes = [
            SimpleNamespace(id=1, node_kind='start', batch_id=None),
            SimpleNamespace(id=127, node_kind='batch', batch_id=101),
            SimpleNamespace(id=128, node_kind='batch', batch_id=102),
            SimpleNamespace(id=129, node_kind='batch', batch_id=103),
        ]
        live_edges = [
            SimpleNamespace(from_node_id=1, to_node_id=127),
            SimpleNamespace(from_node_id=1, to_node_id=128),
            SimpleNamespace(from_node_id=127, to_node_id=129),
        ]
        edges = _infer_legacy_run_edges(
            meta,
            pipeline,
            {27, 28, 29},
            live_edges,
            start_node_id=1,
            live_nodes=live_nodes,
        )
        self.assertIn({'from_node_id': 1, 'to_node_id': 27}, edges)
        self.assertIn({'from_node_id': 1, 'to_node_id': 28}, edges)
        self.assertIn({'from_node_id': 27, 'to_node_id': 29}, edges)

class MagicMockList(list):
    def select_related(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self
