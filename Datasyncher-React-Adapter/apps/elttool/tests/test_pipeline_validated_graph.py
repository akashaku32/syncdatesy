"""Tests for validated vs draft pipeline graph snapshots."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import serializers

from apps.elttool.v1.pipeline_graph import (
    build_validated_graph_snapshot,
    disconnected_batch_node_ids,
    pipeline_graph_fingerprint,
)
from apps.elttool.v1.pipeline_views import (
    PipelineValidateView,
    _build_pipeline_run_validation,
    _published_graph_summary,
    run_pipeline_job,
)


def _graph_nodes():
    return [
        SimpleNamespace(
            id=1,
            node_kind='start',
            batch_id=None,
            connection_id=None,
            execution_order='parallel',
            node_label='Start',
        ),
        SimpleNamespace(
            id=2,
            node_kind='batch',
            batch_id=10,
            connection_id=3,
            execution_order='parallel',
            batch=SimpleNamespace(name='bud_bat'),
        ),
        SimpleNamespace(
            id=3,
            node_kind='batch',
            batch_id=11,
            connection_id=3,
            execution_order='parallel',
            batch=SimpleNamespace(name='acc_bat'),
        ),
        SimpleNamespace(
            id=4,
            node_kind='batch',
            batch_id=12,
            connection_id=4,
            execution_order='parallel',
            batch=SimpleNamespace(name='INVENT_BATCH'),
        ),
    ]


def _connected_edges():
    return [
        SimpleNamespace(from_node_id=1, to_node_id=2),
        SimpleNamespace(from_node_id=1, to_node_id=3),
        SimpleNamespace(from_node_id=2, to_node_id=3),
    ]


class ValidatedGraphHelperTests(SimpleTestCase):
    def test_disconnected_batch_node_ids(self):
        draft_ids = disconnected_batch_node_ids(_graph_nodes(), _connected_edges())
        self.assertEqual(draft_ids, [4])

    def test_build_validated_graph_snapshot_rejects_orphan(self):
        with self.assertRaises(serializers.ValidationError):
            build_validated_graph_snapshot(_graph_nodes(), _connected_edges())

    def test_build_validated_graph_snapshot_connected_canvas(self):
        nodes = _graph_nodes()[:3]
        edges = _connected_edges()[:2]
        snapshot = build_validated_graph_snapshot(nodes, edges)
        self.assertEqual(snapshot['version'], 1)
        self.assertEqual(len(snapshot['nodes']), 2)
        self.assertEqual(snapshot['start_node_id'], 1)

    def test_pipeline_graph_fingerprint_changes_with_orphan(self):
        connected = pipeline_graph_fingerprint(_graph_nodes()[:3], _connected_edges()[:2])
        with_orphan = pipeline_graph_fingerprint(_graph_nodes(), _connected_edges())
        self.assertNotEqual(connected, with_orphan)

    def test_published_graph_summary(self):
        nodes = _graph_nodes()[:3]
        edges = _connected_edges()[:2]
        snapshot = build_validated_graph_snapshot(nodes, edges)
        pipeline = SimpleNamespace(
            validated_graph=snapshot,
            validated_at=None,
        )
        summary = _published_graph_summary(pipeline)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary['start_node_id'], 1)
        self.assertEqual(sorted(summary['node_ids']), [2, 3])
        self.assertEqual(len(summary['edges']), 2)
        self.assertEqual(summary['fingerprint'], snapshot['fingerprint'])

    def test_published_graph_summary_none(self):
        self.assertIsNone(_published_graph_summary(None))
        self.assertIsNone(
            _published_graph_summary(SimpleNamespace(validated_graph=None, validated_at=None))
        )


class PipelineValidationPublishTests(SimpleTestCase):
    def test_validation_allows_wide_level_when_each_node_has_two_children(self):
        """Four nodes at one DAG level is OK if no parent exceeds 2 children."""
        nodes = [
            SimpleNamespace(
                id=1, node_kind='start', batch_id=None, connection_id=None,
                execution_order='parallel', node_label='Start',
            ),
            SimpleNamespace(
                id=2, node_kind='batch', batch_id=10, connection_id=3,
                execution_order='parallel', batch=SimpleNamespace(name='a'),
            ),
            SimpleNamespace(
                id=3, node_kind='batch', batch_id=11, connection_id=3,
                execution_order='parallel', batch=SimpleNamespace(name='b'),
            ),
            SimpleNamespace(
                id=4, node_kind='batch', batch_id=12, connection_id=3,
                execution_order='parallel', batch=SimpleNamespace(name='c'),
            ),
            SimpleNamespace(
                id=5, node_kind='batch', batch_id=13, connection_id=3,
                execution_order='parallel', batch=SimpleNamespace(name='d'),
            ),
            SimpleNamespace(
                id=6, node_kind='batch', batch_id=14, connection_id=3,
                execution_order='parallel', batch=SimpleNamespace(name='e'),
            ),
        ]
        # Start→a,b ; a→c,d ; b→e  → level with c,d,e has 3 nodes (was blocked by old rule)
        edges = [
            SimpleNamespace(from_node_id=1, to_node_id=2),
            SimpleNamespace(from_node_id=1, to_node_id=3),
            SimpleNamespace(from_node_id=2, to_node_id=4),
            SimpleNamespace(from_node_id=2, to_node_id=5),
            SimpleNamespace(from_node_id=3, to_node_id=6),
        ]
        result = _build_pipeline_run_validation(nodes, edges)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(result['max_children_per_node'], 2)

    def test_validation_rejects_three_children_from_one_node(self):
        nodes = _graph_nodes()[:4]  # start + 3 batches
        edges = [
            SimpleNamespace(from_node_id=1, to_node_id=2),
            SimpleNamespace(from_node_id=1, to_node_id=3),
            SimpleNamespace(from_node_id=1, to_node_id=4),
        ]
        result = _build_pipeline_run_validation(nodes, edges)
        self.assertFalse(result['valid'])
        self.assertTrue(
            any('outgoing connections' in err and 'children per node' in err for err in result['errors']),
            result['errors'],
        )

    def test_validation_fails_with_orphan_and_keeps_publish_flag(self):
        pipeline = SimpleNamespace(
            validated_graph={'fingerprint': 'old'},
            validated_at=None,
        )
        result = _build_pipeline_run_validation(
            _graph_nodes(),
            _connected_edges(),
            pipeline=pipeline,
        )
        self.assertFalse(result['valid'])
        self.assertEqual(result['draft_node_ids'], [4])
        self.assertTrue(result['has_published_graph'])
        self.assertTrue(any('INVENT_BATCH' in err for err in result['errors']))
        self.assertTrue(
            any('last published flow' in w.lower() for w in result['warnings'])
        )

    @patch('apps.elttool.v1.pipeline_views.PipelineEdge.objects.filter')
    @patch('apps.elttool.v1.pipeline_views.PipelineNode.objects.filter')
    @patch('apps.elttool.v1.pipeline_views.ensure_start_node')
    @patch('apps.elttool.v1.pipeline_views._get_pipeline_for_user')
    @patch('apps.elttool.v1.pipeline_views._publish_validated_graph')
    def test_validate_failure_does_not_publish(
        self,
        mock_publish,
        mock_get_pipeline,
        mock_ensure_start,
        mock_node_filter,
        mock_edge_filter,
    ):
        from rest_framework.test import APIRequestFactory, force_authenticate

        pipeline = SimpleNamespace(id=1, validated_graph={'fingerprint': 'old'})
        mock_get_pipeline.return_value = pipeline
        mock_node_filter.return_value.select_related.return_value = _graph_nodes()
        mock_edge_filter.return_value = _connected_edges()

        user = SimpleNamespace(
            user_id=1,
            is_authenticated=True,
            cmp=SimpleNamespace(id=1),
            roles=SimpleNamespace(role_name='Administrator'),
        )
        request = APIRequestFactory().post('/api/v1/pipelines/1/validate/')
        force_authenticate(request, user=user)
        response = PipelineValidateView.as_view()(request, pipeline_id=1)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['valid'])
        mock_publish.assert_not_called()


class ScheduledRunPublishedGraphTests(SimpleTestCase):
    @patch('apps.elttool.v1.pipeline_views._dispatch_pipeline_run')
    @patch('apps.elttool.v1.pipeline_views.PipelineRun.objects.create')
    @patch('apps.elttool.v1.pipeline_views.load_validated_graph_for_execution')
    @patch('apps.elttool.v1.pipeline_views._pipeline_run_in_progress', return_value=False)
    @patch('apps.elttool.v1.pipeline_views.Pipeline.objects.select_related')
    def test_scheduled_run_uses_published_graph_when_canvas_has_orphan(
        self,
        mock_select_related,
        mock_in_progress,
        mock_load_graph,
        mock_create_run,
        mock_dispatch,
    ):
        pipeline = SimpleNamespace(
            id=1,
            validated_graph={'fingerprint': 'published'},
            cmp=SimpleNamespace(id=1, cmp_id=1),
            created_by_id=1,
        )
        mock_select_related.return_value.get.return_value = pipeline
        mock_load_graph.return_value = (_graph_nodes()[:3], _connected_edges()[:2])
        created_run = SimpleNamespace(id=99)
        mock_create_run.return_value = created_run

        with patch(
            'apps.elttool.v1.pipeline_views._scheduled_dispatch_jitter_seconds',
            return_value=0,
        ), patch(
            'apps.elttool.v1.pipeline_views.transaction.atomic',
        ), patch(
            'apps.elttool.v1.pipeline_views._company_flow_slot_xact_lock',
        ), patch(
            'apps.elttool.v1.pipeline_views.company_has_flow_capacity',
            return_value=True,
        ):
            run_pipeline_job(1, user_id=1)

        mock_load_graph.assert_called_once_with(pipeline)
        mock_create_run.assert_called_once()
        metadata = mock_create_run.call_args.kwargs['metadata']
        self.assertEqual(metadata['graph_source'], 'validated')
        mock_dispatch.assert_called_once_with(99, countdown=None)

    @patch('apps.elttool.v1.pipeline_views._dispatch_pipeline_run')
    @patch('apps.elttool.v1.pipeline_views._auto_publish_draft_for_schedule', return_value=False)
    @patch('apps.elttool.v1.pipeline_views.Pipeline.objects.select_related')
    @patch('apps.elttool.v1.pipeline_views._pipeline_run_in_progress', return_value=False)
    def test_scheduled_run_auto_validates_when_unpublished(
        self,
        mock_in_progress,
        mock_select_related,
        mock_auto_publish,
        mock_dispatch,
    ):
        pipeline = SimpleNamespace(
            id=1,
            validated_graph=None,
            created_by_id=1,
            cmp_id=1,
            cmp=None,
        )
        mock_select_related.return_value.get.return_value = pipeline

        run_pipeline_job(1, user_id=1)

        mock_auto_publish.assert_called_once()
        mock_dispatch.assert_not_called()

    @patch('apps.elttool.v1.pipeline_views._dispatch_pipeline_run')
    @patch('apps.elttool.v1.pipeline_views.PipelineRun.objects.create')
    @patch('apps.elttool.v1.pipeline_views.load_validated_graph_for_execution')
    @patch(
        'apps.elttool.v1.pipeline_views._auto_publish_draft_for_schedule',
        return_value=True,
    )
    @patch('apps.elttool.v1.pipeline_views.Pipeline.objects.select_related')
    @patch('apps.elttool.v1.pipeline_views._pipeline_run_in_progress', return_value=False)
    def test_scheduled_run_dispatches_after_auto_publish(
        self,
        mock_in_progress,
        mock_select_related,
        mock_auto_publish,
        mock_load_graph,
        mock_create_run,
        mock_dispatch,
    ):
        pipeline = SimpleNamespace(
            id=1,
            validated_graph=None,
            created_by_id=1,
            cmp=SimpleNamespace(id=1, cmp_id=1),
            cmp_id=1,
        )

        def _refresh(*, fields=None):
            pipeline.validated_graph = {'fingerprint': 'auto'}

        pipeline.refresh_from_db = _refresh
        mock_select_related.return_value.get.return_value = pipeline
        mock_load_graph.return_value = (_graph_nodes()[:3], _connected_edges()[:2])
        mock_create_run.return_value = SimpleNamespace(id=42)

        with patch(
            'apps.elttool.v1.pipeline_views._scheduled_dispatch_jitter_seconds',
            return_value=0,
        ), patch(
            'apps.elttool.v1.pipeline_views.transaction.atomic',
        ), patch(
            'apps.elttool.v1.pipeline_views._company_flow_slot_xact_lock',
        ), patch(
            'apps.elttool.v1.pipeline_views.company_has_flow_capacity',
            return_value=True,
        ), patch(
            'apps.elttool.v1.pipeline_views._company_redis_fleet_count',
            return_value=0,
        ), patch(
            'apps.elttool.v1.pipeline_views._company_max_concurrent_flows',
            return_value=2,
        ), patch(
            'apps.elttool.v1.pipeline_views._reclaim_orphan_flow_fleets_for_company',
        ):
            run_pipeline_job(1, user_id=1)

        mock_auto_publish.assert_called_once()
        mock_load_graph.assert_called_once_with(pipeline)
        mock_create_run.assert_called_once()
        mock_dispatch.assert_called_once_with(42, countdown=None)

    @patch('apps.elttool.v1.pipeline_views.PipelineRun.objects.create')
    @patch('apps.elttool.v1.pipeline_views._publish_validated_graph')
    @patch('apps.elttool.v1.pipeline_views._build_pipeline_run_validation')
    @patch('apps.elttool.v1.pipeline_views.PipelineEdge.objects.filter')
    @patch('apps.elttool.v1.pipeline_views.PipelineNode.objects.filter')
    @patch('apps.elttool.v1.pipeline_views.ensure_start_node')
    def test_auto_publish_records_failed_run_on_validation_error(
        self,
        mock_ensure_start,
        mock_node_filter,
        mock_edge_filter,
        mock_build_validation,
        mock_publish,
        mock_create_run,
    ):
        from apps.elttool.v1.pipeline_views import _auto_publish_draft_for_schedule

        pipeline = SimpleNamespace(id=7, validated_graph=None)
        mock_node_filter.return_value.select_related.return_value = []
        mock_edge_filter.return_value = []
        mock_build_validation.return_value = {
            'valid': False,
            'errors': ['Pipeline has no batch nodes.'],
        }
        mock_create_run.return_value = SimpleNamespace(id=55)

        ok = _auto_publish_draft_for_schedule(pipeline, resolved_user_id=3)

        self.assertFalse(ok)
        mock_publish.assert_not_called()
        mock_create_run.assert_called_once()
        kwargs = mock_create_run.call_args.kwargs
        self.assertEqual(kwargs['status'], 'failed')
        self.assertEqual(kwargs['triggered_by_id'], 3)
        self.assertIn('no batch nodes', kwargs['metadata']['error'].lower())
        self.assertEqual(
            kwargs['metadata']['validation_errors'],
            ['Pipeline has no batch nodes.'],
        )


class StaleScheduleResumeTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views._claim_pipeline_resume", return_value=True)
    @patch("apps.elttool.v1.pipeline_views._pipeline_orchestrator_is_stale", return_value=True)
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_is_resumable", return_value=True)
    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.v1.pipeline_views.PipelineRun.objects.filter")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_in_progress", return_value=True)
    @patch("apps.elttool.v1.pipeline_views.Pipeline.objects.select_related")
    def test_scheduled_tick_resumes_a_stale_run_instead_of_skipping(
        self,
        mock_select_related,
        mock_in_progress,
        mock_filter,
        mock_dispatch,
        mock_resumable,
        mock_stale,
        mock_claim,
    ):
        pipeline = SimpleNamespace(id=57, validated_graph={"fingerprint": "published"})
        mock_select_related.return_value.get.return_value = pipeline
        stale_run = SimpleNamespace(id=5064, pipeline_id=57, status="running")
        mock_filter.return_value.order_by.return_value = [stale_run]

        run_pipeline_job(57)

        mock_dispatch.assert_called_once_with(5064)
        mock_claim.assert_called_once_with(5064)
