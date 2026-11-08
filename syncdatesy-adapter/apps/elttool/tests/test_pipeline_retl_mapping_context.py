"""Pipeline RETL context must pass [source, destination] mapping pairs."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.elttool.v1.pipeline_views import _build_pipeline_node_execution_context


class PipelineRetlMappingContextTests(SimpleTestCase):
    def _node(self, table_names):
        batch = SimpleNamespace(
            name="RETL_BATCH",
            execution_order="parallel",
            time_frequency="15",
            tables=SimpleNamespace(
                values_list=MagicMock(return_value=table_names),
            ),
        )
        src_model = SimpleNamespace(is_rtl=True, name="Snowflake")
        dst_model = SimpleNamespace(name="Salesforce")
        src_cfg = SimpleNamespace(src=src_model, data={"database": "DB"}, name="SPECT_RETL")
        dst_cfg = SimpleNamespace(dst=dst_model, data={})
        connection = SimpleNamespace(
            connection_id=11,
            src_config=src_cfg,
            dst_config=dst_cfg,
            dst_schema="",
            DELETED="_SPECT_DELETED",
            SYNCED="_SPECT_SYNCED",
            safety_interval="",
            logs_creation=False,
            cmp_id=66,
        )
        return SimpleNamespace(
            batch=batch,
            connection=connection,
            execution_order="parallel",
            time_frequency="15",
        )

    @patch("apps.elttool.services.table_operation_guard.filter_tables_for_migration", side_effect=lambda _c, tables, **_: (tables, []))
    @patch("apps.elttool.v1.views.ensure_config_is_dict", side_effect=lambda data, *_a, **_k: data or {})
    @patch("apps.elttool.v1.views._is_file_based_destination", return_value=False)
    @patch("apps.elttool.models.TableMapping.objects")
    def test_retl_resolves_destination_from_mapping(
        self, mock_tm_objects, _mock_file_based, _mock_ensure, _mock_filter
    ):
        mapping = SimpleNamespace(
            source_table=SimpleNamespace(table="FINANCIAL__C"),
            destination_table=SimpleNamespace(table="Financial__c"),
        )
        mock_tm_objects.select_related.return_value.filter.return_value = [mapping]

        ctx = _build_pipeline_node_execution_context(self._node(["FINANCIAL__C"]))
        self.assertEqual(ctx["selected_tables"], [["FINANCIAL__C", "Financial__c"]])

    @patch("apps.elttool.services.table_operation_guard.filter_tables_for_migration", side_effect=lambda _c, tables, **_: (tables, []))
    @patch("apps.elttool.v1.views.ensure_config_is_dict", side_effect=lambda data, *_a, **_k: data or {})
    @patch("apps.elttool.v1.views._is_file_based_destination", return_value=False)
    @patch("apps.elttool.models.TableMapping.objects")
    def test_retl_missing_mapping_raises(self, mock_tm_objects, _mock_file_based, _mock_ensure, _mock_filter):
        mock_tm_objects.select_related.return_value.filter.return_value = []
        with self.assertRaises(RuntimeError) as ctx:
            _build_pipeline_node_execution_context(self._node(["FINANCIAL__C"]))
        self.assertIn("without a source→destination mapping", str(ctx.exception))
