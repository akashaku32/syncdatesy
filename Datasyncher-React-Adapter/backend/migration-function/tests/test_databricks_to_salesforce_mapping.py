"""Orchestration unit tests for Databricks → Salesforce reverse ETL."""

import asyncio
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("SNOWFLAKE_STORAGE_INTEGRATION", "test_integration")
os.environ.setdefault("GCS_BUCKET_URL", "gs://test-bucket")
os.environ.setdefault("GCS_BUCKET_NAME", "test-bucket")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

_fake_sf = types.ModuleType("services.salesforce.client")


class _FailureTracker:
    def __init__(self):
        self.failed_tables = []

    def add_failure(self, table_name, error_message, service_name):
        self.failed_tables.append(table_name)

    def send_consolidated_error_email(self, **kwargs):
        return None

    @staticmethod
    def failed_table_names(failed_list):
        return {str(t) for t in (failed_list or [])}


_fake_sf.MigrationFailureTracker = _FailureTracker
_fake_sf.postgres_connection = MagicMock()
_fake_sf.salesforce_connection = MagicMock()
_fake_sf.update_table_status = MagicMock()
_fake_sf.create_log_paths = MagicMock()
sys.modules["services.salesforce.client"] = _fake_sf

from services.salesforce.as_destination.from_databricks import (  # noqa: E402
    DATABRICKS_RETL_LABEL,
    DATABRICKS_RETL_SERVICE,
    process_mapping,
    sync_databricks_to_salesforce,
)
from services.salesforce.as_destination.sources.databricks import (  # noqa: E402
    DatabricksExtractAdapter,
)


def test_process_mapping_uses_databricks_adapter(monkeypatch):
    captured = {}

    def fake_core(*args, **kwargs):
        captured["adapter"] = kwargs.get("extract_adapter")
        captured["service_name"] = kwargs.get("service_name")
        captured["label"] = kwargs.get("migration_type_label")
        captured["update_staging"] = kwargs.get("update_staging")
        return 7

    monkeypatch.setattr(
        "services.salesforce.as_destination.from_databricks.process_mapping_core",
        fake_core,
    )
    rows = process_mapping(
        {"source_table": "ACCOUNT", "destination_table": "Account"},
        {"catalog": "main", "schema": "sf"},
        {},
        1,
        1,
        "job",
        1,
        None,
        1,
        1,
        "production",
        500,
        False,
        "conn",
        "co",
    )
    assert rows == 7
    assert isinstance(captured["adapter"], DatabricksExtractAdapter)
    assert captured["service_name"] == DATABRICKS_RETL_SERVICE
    assert captured["label"] == DATABRICKS_RETL_LABEL
    assert captured["update_staging"] is True


def test_sync_databricks_to_salesforce_sequential(monkeypatch):
    matching = [
        {
            "source_table": "ACCOUNT",
            "destination_table": "Account",
            "fields": ["Id", "Name"],
        }
    ]
    mapping_calls = []

    monkeypatch.setattr(
        "services.salesforce.as_destination.from_databricks.get_matching_fields",
        lambda conn, mapping: matching,
    )
    monkeypatch.setattr(
        "services.salesforce.as_destination.from_databricks.mark_unmapped_source_tables",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "services.salesforce.as_destination.from_databricks.process_mapping",
        lambda *args, **kwargs: mapping_calls.append(args[0]) or 13,
    )
    monkeypatch.setattr(
        "services.salesforce.as_destination.from_databricks.handle_migration_completion",
        lambda **kwargs: {"status": "success", "cleaned_jobs": []},
    )
    monkeypatch.setattr(
        "utils.error_utils.log_migration_completion",
        lambda **kwargs: None,
    )

    request = SimpleNamespace(
        source_config={"catalog": "main", "schema": "sf"},
        destination_config={"username": "u"},
        connection_id=25,
        session=1,
        selected_tables=[["ACCOUNT", "Account"]],
        job_name="retl_job",
        migration_session_id=9,
        cmp_id=1,
        user_id=1,
        execution_order="sequential",
        celery_child_mode=False,
        chunk_count=500,
        logs_creation=False,
        connection_name="sf",
        company_name="Co",
        log_id=None,
        is_final_attempt=True,
        retry_attempt=0,
    )
    result = asyncio.run(sync_databricks_to_salesforce(request, "production"))
    assert result["total_rows"] == 13
    assert result["total_tables"] == 1
    assert result["successful_tables_count"] == 1
    assert mapping_calls == matching
