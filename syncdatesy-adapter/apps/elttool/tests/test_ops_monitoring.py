"""Tests for platform admin ops monitoring (REST, WS, service, push helpers)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.elttool.services.ops_monitoring_service import (
    build_ops_overview,
    build_ops_snapshot,
    job_hash,
)
from apps.elttool.v1.ops_views import (
    InternalAdminOpsPushAPIView,
    OpsOverviewAPIView,
)
from apps.elttool.v1.permissions import IsPlatformSuperuser
from apps.elttool.websocket.consumers.admin_ops import authenticate_ws_token_superuser
from apps.elttool.websocket.ws_utils import push_admin_ops_snapshot


class IsPlatformSuperuserTests(SimpleTestCase):
    def test_allows_superuser(self):
        user = SimpleNamespace(is_authenticated=True, is_superuser=True)
        request = SimpleNamespace(user=user)
        self.assertTrue(IsPlatformSuperuser().has_permission(request, None))

    def test_denies_regular_user(self):
        user = SimpleNamespace(is_authenticated=True, is_superuser=False)
        request = SimpleNamespace(user=user)
        self.assertFalse(IsPlatformSuperuser().has_permission(request, None))


class OpsMonitoringServiceTests(SimpleTestCase):
    @patch("apps.elttool.services.ops_monitoring_service.get_celery_worker_status")
    @patch("apps.elttool.services.ops_monitoring_service._fetch_prometheus_gauges")
    @patch("apps.elttool.services.ops_monitoring_service.sum_worker_pods_used")
    @patch("apps.elttool.services.ops_monitoring_service._base_job_queryset")
    def test_build_ops_overview(
        self,
        mock_qs,
        mock_pods,
        mock_prom,
        mock_celery,
    ):
        mock_qs.return_value.values_list.side_effect = [
            [101, 102],
            ["job_a", "job_b"],
        ]
        mock_pods.return_value = 3
        mock_prom.return_value = {}
        mock_celery.return_value = {
            "workers_available": True,
            "total_workers": 2,
            "total_active_tasks": 1,
            "error": None,
        }
        r = MagicMock()
        r.zcard.return_value = 0
        r.llen.return_value = 1

        overview = build_ops_overview(r)
        self.assertEqual(overview["active_sessions"], 2)
        self.assertEqual(overview["active_jobs"], 2)
        self.assertEqual(overview["worker_pods_used"], 3)
        self.assertEqual(overview["control_queue_depth"], 1)

    @patch("apps.elttool.services.ops_monitoring_service.build_ops_workers")
    @patch("apps.elttool.services.ops_monitoring_service.build_ops_jobs_list")
    @patch("apps.elttool.services.ops_monitoring_service.build_ops_overview")
    @patch("apps.elttool.services.ops_monitoring_service.get_ops_redis")
    def test_build_ops_snapshot(self, mock_redis, mock_overview, mock_jobs, mock_workers):
        mock_overview.return_value = {"active_jobs": 0}
        mock_jobs.return_value = []
        mock_workers.return_value = {"job_workers": []}

        snap = build_ops_snapshot()
        self.assertEqual(snap["event"], "ops_snapshot")
        self.assertIn("timestamp", snap)
        self.assertEqual(snap["overview"]["active_jobs"], 0)

    def test_job_hash_length(self):
        self.assertEqual(len(job_hash("D365_manual_270")), 12)


class PushAdminOpsSnapshotTests(SimpleTestCase):
    @patch("apps.elttool.websocket.ws_utils.get_channel_layer")
    @patch("apps.elttool.websocket.ws_utils.async_to_sync")
    def test_push_calls_group_send(self, mock_async, mock_layer):
        sender = MagicMock()
        mock_async.return_value = sender
        mock_layer.return_value = MagicMock()

        payload = {"event": "ops_snapshot", "overview": {}}
        push_admin_ops_snapshot(payload)

        mock_async.assert_called_once()
        sender.assert_called_once()
        args, _kwargs = sender.call_args
        self.assertEqual(args[0], "admin_ops_platform")
        self.assertEqual(args[1]["type"], "admin_ops_update")
        self.assertEqual(args[1]["payload"], payload)


class OpsRestAuthTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.superuser = SimpleNamespace(
            is_authenticated=True,
            is_superuser=True,
            is_active=True,
        )
        self.regular = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            is_active=True,
        )

    @patch("apps.elttool.v1.ops_views.build_ops_overview")
    def test_overview_requires_superuser(self, mock_overview):
        mock_overview.return_value = {"active_jobs": 0}
        request = self.factory.get("/api/v1/admin/ops/overview/")
        force_authenticate(request, user=self.regular)
        response = OpsOverviewAPIView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("apps.elttool.v1.ops_views.build_ops_overview")
    def test_overview_allows_superuser(self, mock_overview):
        mock_overview.return_value = {"active_jobs": 1}
        request = self.factory.get("/api/v1/admin/ops/overview/")
        force_authenticate(request, user=self.superuser)
        response = OpsOverviewAPIView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["active_jobs"], 1)

    @patch("apps.elttool.v1.ops_views.build_ops_snapshot")
    @patch("apps.elttool.v1.ops_views.push_admin_ops_snapshot")
    def test_internal_push_endpoint(self, mock_push, mock_build):
        mock_build.return_value = {"event": "ops_snapshot"}
        request = self.factory.post("/api/v1/internal/admin-ops-push/")
        response = InternalAdminOpsPushAPIView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_push.assert_called_once()


class WsAuthTests(SimpleTestCase):
    @patch("apps.elttool.websocket.consumers.admin_ops.get_user_model")
    @patch("rest_framework_simplejwt.tokens.AccessToken")
    def test_jwt_superuser_authenticates(self, mock_token_cls, mock_get_user):
        superuser = SimpleNamespace(is_active=True, is_superuser=True)
        token_instance = MagicMock()
        token_instance.get.return_value = 1
        mock_token_cls.side_effect = lambda _raw: token_instance
        user_manager = MagicMock()
        user_manager.get.return_value = superuser
        mock_get_user.return_value.objects = user_manager

        scope = {"query_string": b"token=jwt-token"}
        user = authenticate_ws_token_superuser(scope)
        self.assertIs(user, superuser)
        user_manager.get.assert_called_once_with(pk=1)

    @patch("apps.elttool.websocket.consumers.admin_ops.get_user_model")
    @patch("rest_framework_simplejwt.tokens.AccessToken")
    def test_jwt_non_superuser_rejected(self, mock_token_cls, mock_get_user):
        regular = SimpleNamespace(is_active=True, is_superuser=False)
        token_instance = MagicMock()
        token_instance.get.return_value = 2
        mock_token_cls.side_effect = lambda _raw: token_instance
        user_manager = MagicMock()
        user_manager.get.return_value = regular
        mock_get_user.return_value.objects = user_manager

        scope = {"query_string": b"token=jwt-token"}
        self.assertIsNone(authenticate_ws_token_superuser(scope))

    def test_missing_token_rejected(self):
        scope = {"query_string": b""}
        self.assertIsNone(authenticate_ws_token_superuser(scope))
