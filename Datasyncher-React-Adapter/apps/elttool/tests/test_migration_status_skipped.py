"""Tests for skipped table status in migration_status_builder."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.elttool.services.migration_status_builder import build_migration_status


class MigrationStatusSkippedTests(SimpleTestCase):
    @patch("apps.elttool.services.migration_status_builder.Log")
    @patch("apps.elttool.services.migration_status_builder.Data_Transfer")
    @patch("apps.elttool.services.migration_status_builder.MigrationRecord")
    def test_skipped_tables_mark_overall_completed_with_warning(
        self, mock_records, mock_transfer, mock_log
    ):
        connection = SimpleNamespace(
            connection_id=1,
            src_config=SimpleNamespace(src=SimpleNamespace(name="Dynamics365")),
        )
        record = SimpleNamespace(
            id=99,
            table_name="account",
            status="skipped",
            start_time=None,
            job_name="pipeline_run_1_node_2",
            connection=connection,
            staging_records_count=0,
        )
        qs = MagicMock()
        qs.exists.return_value = True
        qs.first.return_value = record
        qs.__iter__ = lambda self: iter([record])
        mock_records.objects.filter.return_value.select_related.return_value = qs
        mock_transfer.objects.filter.return_value.order_by.return_value.first.return_value = None

        skip_log = SimpleNamespace(message="Skipped — table refresh/reload is in progress")
        failed_log_qs = MagicMock()
        failed_log_qs.order_by.return_value.first.return_value = None
        partial_log_qs = MagicMock()
        partial_log_qs.order_by.return_value.__getitem__.return_value = []
        terminal_log_qs = MagicMock()
        terminal_log_qs.order_by.return_value.first.return_value = None
        skip_log_qs = MagicMock()
        skip_log_qs.order_by.return_value.first.return_value = skip_log

        def _log_filter(**kwargs):
            result = MagicMock()
            if kwargs.get("status") == "F":
                result.order_by.return_value.first.return_value = None
                return result
            if kwargs.get("status") == "P":
                result.order_by.return_value.__getitem__.return_value = []
                return result
            if kwargs.get("status__in") == ["S", "P", "F"]:
                result.order_by.return_value.first.return_value = None
                return result
            if kwargs.get("migration_record") == record:
                result.order_by.return_value.first.return_value = skip_log
                return result
            result.order_by.return_value.first.return_value = None
            return result

        mock_log.objects.filter.side_effect = _log_filter

        data, error, status_code = build_migration_status(101)
        self.assertIsNone(error)
        self.assertEqual(status_code, 200)
        self.assertEqual(data["overall_status"], "completed")
        self.assertTrue(data["overall_has_warning"])
        self.assertEqual(data["tables"][0]["status_icon"], "skipped")
