"""Tests for skipping in-progress tables during pipeline flow execution."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.elttool.services.table_operation_guard import (
    PIPELINE_TABLE_SKIP_REASON,
    filter_tables_for_migration,
)
from apps.elttool.v1.pipeline_views import _build_pipeline_node_execution_context


class TableOperationGuardTests(SimpleTestCase):
    @patch("apps.elttool.services.table_operation_guard.get_in_progress_table_names")
    def test_filter_tables_for_migration_splits_busy_tables(self, mock_busy):
        mock_busy.return_value = {"account"}
        connection = SimpleNamespace(connection_id=1)
        runnable, skipped = filter_tables_for_migration(
            connection,
            ["account", "contact"],
            is_reverse_etl=False,
        )
        self.assertEqual(runnable, ["contact"])
        self.assertEqual(skipped, ["account"])

    @patch("apps.elttool.services.table_operation_guard.get_in_progress_table_names")
    def test_filter_tables_for_migration_handles_retl_pairs(self, mock_busy):
        mock_busy.return_value = {"FINANCIAL__C"}
        connection = SimpleNamespace(connection_id=1)
        runnable, skipped = filter_tables_for_migration(
            connection,
            [["FINANCIAL__C", "Financial__c"], ["CONTACT", "Contact"]],
            is_reverse_etl=True,
        )
        self.assertEqual(runnable, [["CONTACT", "Contact"]])
        self.assertEqual(skipped, ["FINANCIAL__C"])


class PipelineTableSkipContextTests(SimpleTestCase):
    def _node(self, table_names):
        batch = SimpleNamespace(
            name="BATCH_A",
            execution_order="parallel",
            time_frequency="15",
            tables=SimpleNamespace(
                values_list=MagicMock(return_value=table_names),
            ),
        )
        src_model = SimpleNamespace(is_rtl=False, name="Dynamics365")
        dst_model = SimpleNamespace(name="Snowflake")
        src_cfg = SimpleNamespace(src=src_model, data={"host": "x"}, name="CONN")
        dst_cfg = SimpleNamespace(dst=dst_model, data={})
        connection = SimpleNamespace(
            connection_id=11,
            src_config=src_cfg,
            dst_config=dst_cfg,
            dst_schema="",
        )
        return SimpleNamespace(
            batch=batch,
            connection=connection,
            execution_order="parallel",
            time_frequency="15",
        )

    @patch("apps.elttool.services.table_operation_guard.filter_tables_for_migration")
    @patch("apps.elttool.v1.views.ensure_config_is_dict", side_effect=lambda data, *_a, **_k: data or {})
    def test_build_context_returns_skipped_tables(self, _mock_ensure, mock_filter):
        mock_filter.return_value = (["contact"], ["account"])
        ctx = _build_pipeline_node_execution_context(self._node(["account", "contact"]))
        self.assertEqual(ctx["selected_tables"], ["contact"])
        self.assertEqual(ctx["skipped_tables"], ["account"])
        self.assertEqual(ctx["batch_table_count"], 2)

    @patch("apps.elttool.services.table_operation_guard.filter_tables_for_migration")
    @patch("apps.elttool.v1.views.ensure_config_is_dict", side_effect=lambda data, *_a, **_k: data or {})
    def test_skip_reason_constant(self, _mock_ensure, _mock_filter):
        self.assertIn("refresh/reload", PIPELINE_TABLE_SKIP_REASON.lower())
