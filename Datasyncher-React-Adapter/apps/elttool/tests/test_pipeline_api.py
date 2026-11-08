"""Tests for pipeline run REST API views."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.elttool.v1.pipeline_views import (
    PipelineRunDetailView,
    PipelineRunListView,
    PipelineRunNowView,
    _PLAN_MAX_CONCURRENT_FLOWS,
    _company_max_concurrent_flows,
    _display_latest_run_status,
    run_pipeline_job,
)


def _analyst_user():
    return SimpleNamespace(
        user_id=1,
        is_authenticated=True,
        cmp=SimpleNamespace(id=1),
        roles=SimpleNamespace(role_name="Administrator"),
    )


def _standard_company():
    return SimpleNamespace(cmp_id=10, plan="Standard")


class PipelineLatestRunStatusTests(SimpleTestCase):
    def test_running_with_capacity_wait_shows_as_queued(self):
        self.assertEqual(
            _display_latest_run_status(
                "running",
                {"waiting_for_capacity": True},
            ),
            "queued",
        )

    def test_queued_status_passthrough(self):
        self.assertEqual(_display_latest_run_status("queued", {}), "queued")

    def test_running_without_wait_stays_running(self):
        self.assertEqual(_display_latest_run_status("running", {}), "running")


class CompanyFlowCapHelpersTests(SimpleTestCase):
    def test_plan_fallback_standard_allows_two_flows(self):
        self.assertEqual(_PLAN_MAX_CONCURRENT_FLOWS["standard"], 2)

    @patch("apps.elttool.models.PlanResourceConfig.objects.filter")
    def test_company_max_concurrent_flows_reads_db_for_standard(self, mock_filter):
        mock_filter.return_value.values_list.return_value.first.return_value = 2
        company = _standard_company()
        self.assertEqual(_company_max_concurrent_flows(company), 2)
        mock_filter.assert_called_once()

    @patch("apps.elttool.models.PlanResourceConfig.objects.filter")
    def test_company_max_concurrent_flows_fallback_when_db_missing(self, mock_filter):
        mock_filter.return_value.values_list.return_value.first.return_value = None
        company = _standard_company()
        self.assertEqual(_company_max_concurrent_flows(company), 2)


class PipelineRunNowViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _analyst_user()

    @patch("apps.elttool.v1.pipeline_views._company_active_flow_count", return_value=0)
    @patch("apps.elttool.v1.pipeline_views._company_max_concurrent_flows", return_value=2)
    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.v1.pipeline_views.PipelineRun.objects.create")
    @patch("apps.elttool.v1.pipeline_views.load_validated_graph_for_execution")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_in_progress", return_value=False)
    @patch("apps.elttool.v1.pipeline_views.ensure_start_node")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_run_now_returns_202_and_dispatches_celery_task(
        self,
        mock_get_pipeline,
        mock_ensure_start,
        mock_run_in_progress,
        mock_load_graph,
        mock_create_run,
        mock_dispatch_pipeline_run,
        mock_max_flows,
        mock_active_flows,
    ):
        pipeline = SimpleNamespace(
            id=1,
            validated_graph={"fingerprint": "published"},
            cmp=_standard_company(),
        )
        mock_get_pipeline.return_value = pipeline
        mock_load_graph.return_value = ([], [])
        created_run = SimpleNamespace(id=42)
        mock_create_run.return_value = created_run

        request = self.factory.post("/api/v1/pipelines/1/run/")
        force_authenticate(request, user=self.user)
        response = PipelineRunNowView.as_view()(request, pipeline_id=1)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["pipeline_run_id"], 42)
        mock_dispatch_pipeline_run.assert_called_once_with(42)

    @patch("apps.elttool.v1.pipeline_views._company_active_flow_count", return_value=1)
    @patch("apps.elttool.v1.pipeline_views._company_max_concurrent_flows", return_value=2)
    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.v1.pipeline_views.PipelineRun.objects.create")
    @patch("apps.elttool.v1.pipeline_views.load_validated_graph_for_execution")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_in_progress", return_value=False)
    @patch("apps.elttool.v1.pipeline_views.ensure_start_node")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_run_now_allows_second_standard_flow_under_cap(
        self,
        mock_get_pipeline,
        mock_ensure_start,
        mock_run_in_progress,
        mock_load_graph,
        mock_create_run,
        mock_dispatch_pipeline_run,
        mock_max_flows,
        mock_active_flows,
    ):
        pipeline = SimpleNamespace(
            id=2,
            validated_graph={"fingerprint": "published"},
            cmp=_standard_company(),
        )
        mock_get_pipeline.return_value = pipeline
        mock_load_graph.return_value = ([], [])
        mock_create_run.return_value = SimpleNamespace(id=43)

        request = self.factory.post("/api/v1/pipelines/2/run/")
        force_authenticate(request, user=self.user)
        response = PipelineRunNowView.as_view()(request, pipeline_id=2)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_dispatch_pipeline_run.assert_called_once_with(43)

    @patch("apps.elttool.v1.pipeline_views._company_active_flow_count", return_value=2)
    @patch("apps.elttool.v1.pipeline_views._company_max_concurrent_flows", return_value=2)
    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.v1.pipeline_views.PipelineRun.objects.create")
    @patch("apps.elttool.v1.pipeline_views.load_validated_graph_for_execution")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_in_progress", return_value=False)
    @patch("apps.elttool.v1.pipeline_views.ensure_start_node")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_run_now_rejects_when_org_at_flow_cap(
        self,
        mock_get_pipeline,
        mock_ensure_start,
        mock_run_in_progress,
        mock_load_graph,
        mock_create_run,
        mock_dispatch_pipeline_run,
        mock_max_flows,
        mock_active_flows,
    ):
        pipeline = SimpleNamespace(
            id=3,
            validated_graph={"fingerprint": "published"},
            cmp=_standard_company(),
        )
        mock_get_pipeline.return_value = pipeline
        mock_load_graph.return_value = ([], [])

        request = self.factory.post("/api/v1/pipelines/3/run/")
        force_authenticate(request, user=self.user)
        response = PipelineRunNowView.as_view()(request, pipeline_id=3)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get("code"), "plan_flow_cap")
        self.assertIn("concurrent", response.data["warning"].lower())
        self.assertIn("2", response.data["warning"])
        mock_create_run.assert_not_called()
        mock_dispatch_pipeline_run.assert_not_called()

    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_in_progress")
    @patch("apps.elttool.v1.pipeline_views.load_validated_graph_for_execution")
    @patch("apps.elttool.v1.pipeline_views.ensure_start_node")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_run_now_rejects_when_run_in_progress(
        self,
        mock_get_pipeline,
        mock_ensure_start,
        mock_load_graph,
        mock_run_in_progress,
        mock_dispatch_pipeline_run,
    ):
        mock_get_pipeline.return_value = SimpleNamespace(
            id=1,
            validated_graph={"fingerprint": "published"},
            cmp=_standard_company(),
        )
        mock_load_graph.return_value = ([], [])
        mock_run_in_progress.return_value = True

        request = self.factory.post("/api/v1/pipelines/1/run/")
        force_authenticate(request, user=self.user)
        response = PipelineRunNowView.as_view()(request, pipeline_id=1)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("in progress", response.data["error"].lower())
        mock_dispatch_pipeline_run.assert_not_called()

    @patch("apps.elttool.v1.pipeline_views.ensure_start_node")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_run_now_rejects_without_published_graph(
        self, mock_get_pipeline, mock_ensure_start
    ):
        mock_get_pipeline.return_value = SimpleNamespace(id=1, validated_graph=None)

        request = self.factory.post("/api/v1/pipelines/1/run/")
        force_authenticate(request, user=self.user)
        response = PipelineRunNowView.as_view()(request, pipeline_id=1)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("published", response.data["error"].lower())


class ScheduledPipelineRunQueueTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.v1.pipeline_views.PipelineRun.objects.create")
    @patch("apps.elttool.v1.pipeline_views.company_has_flow_capacity", return_value=False)
    @patch("apps.elttool.v1.pipeline_views._company_active_flow_count", return_value=2)
    @patch("apps.elttool.v1.pipeline_views._company_max_concurrent_flows", return_value=2)
    @patch("apps.elttool.v1.pipeline_views.load_validated_graph_for_execution")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_in_progress", return_value=False)
    @patch("apps.elttool.v1.pipeline_views.Pipeline.objects.select_related")
    def test_scheduled_run_still_dispatches_when_org_at_cap(
        self,
        mock_select_related,
        mock_run_in_progress,
        mock_load_graph,
        mock_max_flows,
        mock_active_flows,
        mock_has_capacity,
        mock_create_run,
        mock_dispatch_pipeline_run,
    ):
        company = _standard_company()
        pipeline = SimpleNamespace(
            id=7,
            validated_graph={"fingerprint": "published"},
            cmp=company,
        )
        mock_select_related.return_value.get.return_value = pipeline
        mock_load_graph.return_value = ([], [])
        mock_create_run.return_value = SimpleNamespace(id=99)

        with patch(
            "apps.elttool.v1.pipeline_views._scheduled_dispatch_jitter_seconds",
            return_value=12,
        ), patch(
            "apps.elttool.v1.pipeline_views.transaction.atomic",
        ), patch(
            "apps.elttool.v1.pipeline_views._company_flow_slot_xact_lock",
        ):
            run_pipeline_job(7)

        mock_create_run.assert_called_once()
        self.assertEqual(mock_create_run.call_args.kwargs["status"], "queued")
        self.assertTrue(mock_create_run.call_args.kwargs["metadata"]["waiting_for_capacity"])
        mock_dispatch_pipeline_run.assert_called_once_with(99, countdown=12)


class PipelineRunListViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _analyst_user()

    @patch("apps.elttool.v1.pipeline_views._build_pipeline_run_summary")
    @patch("apps.elttool.v1.pipeline_views.PipelineRun.objects.filter")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_list_returns_run_summaries(
        self, mock_get_pipeline, mock_run_filter, mock_build_summary
    ):
        mock_get_pipeline.return_value = SimpleNamespace(id=1)
        run = SimpleNamespace(id=83, status="failed")
        mock_ordered = MagicMock()
        mock_ordered.__getitem__ = MagicMock(return_value=[run])
        mock_run_filter.return_value.order_by.return_value = mock_ordered
        mock_build_summary.return_value = {
            "pipeline_run_id": 83,
            "status": "failed",
            "started_at": "2026-06-26T08:18:29+00:00",
            "finished_at": "2026-06-26T08:18:59+00:00",
            "overall": {"nodes_total": 1, "nodes_completed": 0},
            "error": "Migration ended with status: failed",
        }

        request = self.factory.get("/api/v1/pipelines/1/runs/")
        force_authenticate(request, user=self.user)
        response = PipelineRunListView.as_view()(request, pipeline_id=1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["runs"]), 1)
        self.assertEqual(response.data["runs"][0]["pipeline_run_id"], 83)
        self.assertEqual(response.data["runs"][0]["status"], "failed")
        mock_build_summary.assert_called_once()
        self.assertFalse(mock_build_summary.call_args.kwargs.get("reconcile_running"))


class PipelineRunDetailViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _analyst_user()

    @patch("apps.elttool.v1.pipeline_views._build_pipeline_run_detail")
    @patch("apps.elttool.v1.pipeline_views.get_object_or_404")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_detail_includes_nodes_and_migration_status(
        self, mock_get_pipeline, mock_get_run, mock_build_detail
    ):
        mock_get_pipeline.return_value = SimpleNamespace(id=1)
        mock_get_run.return_value = SimpleNamespace(id=83)
        mock_build_detail.return_value = {
            "pipeline_run_id": 83,
            "pipeline_id": 1,
            "status": "failed",
            "started_at": "2026-06-26T08:18:29+00:00",
            "finished_at": "2026-06-26T08:18:59+00:00",
            "current_node_id": None,
            "overall": {
                "nodes_total": 1,
                "nodes_completed": 0,
                "nodes_failed": 1,
                "tables_total": 1,
                "tables_completed": 0,
            },
            "nodes": [
                {
                    "node_id": 5,
                    "batch_name": "first",
                    "status": "failed",
                    "migration_session_id": 103,
                    "table_count": 1,
                    "migration_status": {
                        "overall_status": "failed",
                        "tables": [{"table_name": "account", "status": "failed"}],
                    },
                }
            ],
            "error": "Migration ended with status: failed",
        }

        request = self.factory.get("/api/v1/pipelines/1/runs/83/")
        force_authenticate(request, user=self.user)
        response = PipelineRunDetailView.as_view()(
            request, pipeline_id=1, run_id=83
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["pipeline_run_id"], 83)
        self.assertEqual(len(response.data["nodes"]), 1)
        self.assertEqual(response.data["nodes"][0]["migration_session_id"], 103)
        mock_build_detail.assert_called_once()
        self.assertTrue(mock_build_detail.call_args.kwargs.get("include_migration_status"))
