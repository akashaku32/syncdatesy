"""Tests for pipeline email notification settings and delivery."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.elttool.services.pipeline_email_templates import (
    build_pipeline_batch_email,
    build_pipeline_flow_email,
    BRAND_PRIMARY_DARK,
)
from apps.elttool.services.pipeline_notifications import (
    normalize_notification_emails,
    resolve_pipeline_recipients,
    send_pipeline_batch_notification,
    send_pipeline_flow_notification,
)
from apps.elttool.v1.pipeline_views import (
    PipelineDetailView,
    PipelineNodeDetailView,
    _apply_pipeline_notification_patch,
)


def _analyst_user():
    return SimpleNamespace(
        user_id=1,
        is_authenticated=True,
        cmp=SimpleNamespace(id=1),
        roles=SimpleNamespace(role_name="Administrator"),
    )


class PipelineNotificationHelperTests(SimpleTestCase):
    def test_normalize_notification_emails_dedupes(self):
        emails = normalize_notification_emails(
            ["Ops@Co.com", "ops@co.com", "admin@co.com"],
        )
        self.assertEqual(emails, ["Ops@Co.com", "admin@co.com"])

    def test_normalize_notification_emails_rejects_invalid(self):
        with self.assertRaises(ValueError):
            normalize_notification_emails(["not-an-email"])

    @patch("apps.elttool.services.pipeline_notifications._build_company_email_group_payload")
    def test_resolve_pipeline_recipients_merges_groups_and_direct(self, mock_payload):
        mock_payload.return_value = {"notification_emails": ["group@co.com"]}
        pipeline = SimpleNamespace(
            cmp=SimpleNamespace(id=1),
            notification_email_group_ids=[1],
            notification_emails=["direct@co.com", "group@co.com"],
        )
        recipients = resolve_pipeline_recipients(pipeline)
        self.assertEqual(recipients, ["group@co.com", "direct@co.com"])


class PipelineEmailTemplateTests(SimpleTestCase):
    def test_flow_email_includes_brand_header_and_batch_table(self):
        subject, text_body, html_body = build_pipeline_flow_email(
            pipeline_name="Pipeline 1",
            run_id=42,
            run_status="completed",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T01:00:00+00:00",
            error_message=None,
            batch_rows=[
                {
                    "batch_name": "pr",
                    "status": "completed",
                    "table_count": 3,
                    "error": "",
                }
            ],
        )
        self.assertIn("Datasyncher Pipeline", subject)
        self.assertIn("Pipeline 1", text_body)
        self.assertIn(BRAND_PRIMARY_DARK, html_body)
        self.assertIn("receipt-header.png", html_body)
        self.assertIn("Batch summary", html_body)
        self.assertIn("pr", html_body)

    def test_batch_email_uses_branded_layout(self):
        subject, text_body, html_body = build_pipeline_batch_email(
            pipeline_name="Pipeline 1",
            run_id=7,
            batch_name="few",
            table_count=2,
            duration="5m 10s",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:05:10+00:00",
        )
        self.assertIn("few", subject)
        self.assertIn("Batch completed", html_body)
        self.assertIn("Datasyncher", html_body)


class PipelineNotificationPatchTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _analyst_user()

    @patch("apps.elttool.v1.pipeline_views._sync_pipeline_scheduler")
    @patch("apps.elttool.v1.pipeline_views.PipelineSerializer")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    @patch("apps.elttool.v1.pipeline_views._validate_company_email_group_ids")
    @patch("apps.elttool.v1.pipeline_views._normalize_notification_email_group_ids")
    def test_pipeline_patch_accepts_notification_fields(
        self,
        mock_normalize_groups,
        mock_validate_groups,
        mock_get_pipeline,
        mock_serializer,
        _mock_scheduler,
    ):
        pipeline = SimpleNamespace(
            id=1,
            cmp=self.user.cmp,
            name="Pipeline 1",
            status="active",
            schedule_type="interval",
            time_frequency="15",
            schedule_config={},
            sync_start_date=None,
            notify_on_flow_finish=True,
            notification_email_group_ids=[],
            notification_emails=[],
            save=MagicMock(),
        )
        mock_get_pipeline.return_value = pipeline
        mock_normalize_groups.return_value = [2]
        mock_validate_groups.return_value = [2]
        mock_serializer.return_value.data = {"id": 1}

        request = self.factory.patch(
            "/api/v1/pipelines/1/",
            {
                "notify_on_flow_finish": False,
                "notification_email_group_ids": [2],
                "notification_emails": ["ops@co.com"],
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = PipelineDetailView.as_view()(request, pipeline_id=1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(pipeline.notify_on_flow_finish)
        self.assertEqual(pipeline.notification_email_group_ids, [2])
        self.assertEqual(pipeline.notification_emails, ["ops@co.com"])
        pipeline.save.assert_called_once()

    @patch("apps.elttool.v1.pipeline_views.PipelineNodeSerializer")
    @patch("apps.elttool.v1.pipeline_views.get_object_or_404")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_node_patch_allows_notify_on_completion_for_batch(
        self,
        mock_get_pipeline,
        mock_get_node,
        mock_serializer,
    ):
        pipeline = SimpleNamespace(id=1)
        node = SimpleNamespace(
            id=5,
            node_kind="batch",
            notify_on_completion=False,
            save=MagicMock(),
        )
        mock_get_pipeline.return_value = pipeline
        mock_get_node.return_value = node
        mock_serializer.return_value.data = {"id": 5, "notify_on_completion": True}

        request = self.factory.patch(
            "/api/v1/pipelines/1/nodes/5/",
            {"notify_on_completion": True},
            format="json",
        )
        force_authenticate(request, user=self.user)
        with patch("apps.elttool.v1.pipeline_views.is_start_node", return_value=False):
            response = PipelineNodeDetailView.as_view()(request, pipeline_id=1, node_id=5)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(node.notify_on_completion)

    @patch("apps.elttool.v1.pipeline_views.get_object_or_404")
    @patch("apps.elttool.v1.pipeline_views._get_pipeline_for_user")
    def test_node_patch_rejects_recipient_fields(self, mock_get_pipeline, mock_get_node):
        pipeline = SimpleNamespace(id=1)
        node = SimpleNamespace(id=5, node_kind="batch")
        mock_get_pipeline.return_value = pipeline
        mock_get_node.return_value = node

        request = self.factory.patch(
            "/api/v1/pipelines/1/nodes/5/",
            {"notification_emails": ["ops@co.com"]},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = PipelineNodeDetailView.as_view()(request, pipeline_id=1, node_id=5)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Start node", response.data["error"])


class PipelineNotificationSendTests(SimpleTestCase):
    @patch("apps.elttool.services.pipeline_notifications.Pipeline.objects.select_related")
    @patch("apps.elttool.services.pipeline_notifications._send_email")
    @patch("apps.elttool.services.pipeline_notifications.resolve_pipeline_recipients")
    def test_send_flow_notification_once(self, mock_recipients, mock_send, mock_select):
        mock_recipients.return_value = ["ops@co.com"]
        pipeline = SimpleNamespace(
            id=1,
            name="Pipeline 1",
            notify_on_flow_finish=True,
        )
        mock_select.return_value.get.return_value = pipeline
        run = SimpleNamespace(
            id=10,
            pipeline_id=1,
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T01:00:00+00:00",
            metadata={},
            save=MagicMock(),
        )

        send_pipeline_flow_notification(run)
        send_pipeline_flow_notification(run)

        mock_send.assert_called_once()

    @patch("apps.elttool.services.pipeline_notifications.Pipeline.objects.select_related")
    @patch("apps.elttool.services.pipeline_notifications._send_email")
    @patch("apps.elttool.services.pipeline_notifications.resolve_pipeline_recipients")
    def test_send_batch_notification_only_when_enabled(
        self,
        mock_recipients,
        mock_send,
        mock_select,
    ):
        mock_recipients.return_value = ["ops@co.com"]
        pipeline = SimpleNamespace(id=1, name="Pipeline 1")
        mock_select.return_value.get.return_value = pipeline
        run = SimpleNamespace(
            id=10,
            pipeline_id=1,
            metadata={"nodes": {"5": {"batch_name": "pr", "table_count": 2}}},
            save=MagicMock(),
        )
        enabled_node = SimpleNamespace(
            id=5,
            notify_on_completion=True,
            batch_id=1,
            batch=SimpleNamespace(name="pr"),
        )
        disabled_node = SimpleNamespace(
            id=6,
            notify_on_completion=False,
            batch_id=2,
            batch=SimpleNamespace(name="few"),
        )

        send_pipeline_batch_notification(run, disabled_node)
        send_pipeline_batch_notification(run, enabled_node)

        mock_send.assert_called_once()

    def test_apply_pipeline_notification_patch_invalid_email(self):
        pipeline = SimpleNamespace(
            cmp=SimpleNamespace(id=1),
            notify_on_flow_finish=True,
            notification_email_group_ids=[],
            notification_emails=[],
        )
        from rest_framework import serializers

        with self.assertRaises(serializers.ValidationError):
            _apply_pipeline_notification_patch(
                pipeline,
                {"notification_emails": ["bad-email"]},
            )
