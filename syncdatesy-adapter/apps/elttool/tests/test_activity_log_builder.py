import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from apps.elttool.services.activity_log_builder import (
    _resolve_trigger_type,
    extract_migration_id_from_log,
    resolve_activity_log_batch_context,
    serialize_connection_activity_log,
)


class ActivityLogBuilderTests(unittest.TestCase):
    def test_extract_migration_id_from_job_name(self):
        log = SimpleNamespace(
            connection_id=6,
            job_name="D365_TEST_NEW_manual_301_custinvoicetrans",
            migration_record_id=None,
            migration_record=None,
        )
        with patch(
            "apps.elttool.services.activity_log_builder.MigrationRecord.objects"
        ) as migration_records:
            migration_records.filter.return_value.order_by.return_value.first.return_value = (
                SimpleNamespace(migration_session_id=301)
            )
            self.assertEqual(extract_migration_id_from_log(log), 301)

    def test_extract_migration_id_from_listing_tables_job_name(self):
        log = SimpleNamespace(
            connection_id=12,
            job_name="My_S3_Conn_listing_tables_452",
            migration_record_id=None,
            migration_record=None,
        )
        with patch(
            "apps.elttool.services.activity_log_builder.MigrationRecord.objects"
        ) as migration_records:
            migration_records.filter.return_value.order_by.return_value.first.return_value = None
            migration_records.filter.return_value.exists.return_value = True
            self.assertEqual(extract_migration_id_from_log(log), 452)

    def test_resolve_pipeline_batch_context(self):
        log = SimpleNamespace(
            connection_id=6,
            job_name="pipeline_run_161_node_14__D365_TEST_NEW_manual_300_tables",
            session=300,
        )
        node = SimpleNamespace(
            id=14,
            batch_id=14,
            batch=SimpleNamespace(name="batch cust"),
            pipeline_id=1,
            pipeline=SimpleNamespace(name="Main Pipeline"),
            node_label=None,
        )
        with patch(
            "apps.elttool.services.activity_log_builder.PipelineNode.objects.select_related"
        ) as select_related:
            select_related.return_value.get.return_value = node
            context = resolve_activity_log_batch_context(log)

        self.assertEqual(context["batch_name"], "batch cust")
        self.assertEqual(context["pipeline_run_id"], 161)
        self.assertEqual(context["pipeline_id"], 1)
        self.assertEqual(context["pipeline_name"], "Main Pipeline")

    def test_resolve_pipeline_from_migration_record_when_log_job_stripped(self):
        """Summary logs often omit pipeline_run_* from job_name — recover via MigrationRecord."""
        log = SimpleNamespace(
            connection_id=8,
            job_name="CONNECTION_SPECTRA_manual_895_account->budgettransactionheader",
            session=895,
        )
        node = SimpleNamespace(
            id=43,
            batch_id=22,
            batch=SimpleNamespace(name="bud_account_batch"),
            pipeline_id=11,
            pipeline=SimpleNamespace(name="spectra_pipeline"),
            node_label=None,
        )
        with patch(
            "apps.elttool.services.activity_log_builder.MigrationRecord.objects"
        ) as migration_records, patch(
            "apps.elttool.services.activity_log_builder.PipelineNode.objects.select_related"
        ) as select_related:
            migration_records.filter.return_value.exclude.return_value.exclude.return_value.order_by.return_value.values_list.return_value.first.return_value = (
                "pipeline_run_355_node_43__CONNECTION_SPECTRA_manual_895_account"
            )
            select_related.return_value.get.return_value = node
            context = resolve_activity_log_batch_context(log)

        self.assertEqual(context["batch_name"], "bud_account_batch")
        self.assertEqual(context["pipeline_run_id"], 355)
        self.assertEqual(context["pipeline_id"], 11)
        self.assertEqual(context["pipeline_name"], "spectra_pipeline")

    def test_resolve_trigger_type_auto_sync(self):
        log = SimpleNamespace(
            message="Migration in progress",
            job_name="D365_TEST_auto_sync_301_tables",
            session=301,
            connection_id=6,
        )
        self.assertEqual(_resolve_trigger_type(log), "Auto/Scheduled")

    def test_resolve_trigger_type_pipeline_scheduled_via_migration_record(self):
        log = SimpleNamespace(
            message="Migration in progress",
            job_name="D365_TEST_manual_300_tables",
            session=300,
            connection_id=6,
        )
        with patch(
            "apps.elttool.services.activity_log_builder.MigrationRecord.objects"
        ) as migration_records, patch(
            "apps.elttool.models.PipelineRun.objects"
        ) as pipeline_runs:
            migration_records.filter.return_value.exclude.return_value.exclude.return_value.order_by.return_value.values_list.return_value.first.return_value = (
                "pipeline_run_161_node_14__D365_TEST_manual_300_tables"
            )
            pipeline_runs.only.return_value.get.return_value = SimpleNamespace(
                metadata={"source": "scheduler"}
            )
            self.assertEqual(_resolve_trigger_type(log), "Auto/Scheduled")

    def test_resolve_trigger_type_pipeline_manual_run_now(self):
        log = SimpleNamespace(
            message="Migration in progress",
            job_name="pipeline_run_77_node_3__D365_TEST_manual_302_tables",
            session=302,
            connection_id=6,
        )
        with patch("apps.elttool.models.PipelineRun.objects") as pipeline_runs:
            pipeline_runs.only.return_value.get.return_value = SimpleNamespace(
                metadata={"requested_at": "2026-06-25T10:00:00+00:00"}
            )
            self.assertEqual(_resolve_trigger_type(log), "Manual")

    def test_serialize_connection_activity_log_includes_trigger_type(self):
        log = SimpleNamespace(
            message="Migration in progress",
            job_name="D365_TEST_auto_sync_301_tables",
            session=301,
            connection_id=6,
            log_id=99,
            time=None,
            status="I",
            user=None,
        )
        payload = serialize_connection_activity_log(log)
        self.assertEqual(payload["trigger_type"], "Auto/Scheduled")


if __name__ == "__main__":
    unittest.main()
