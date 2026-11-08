"""Orchestration unit tests for Salesforce → Databricks mapping path."""

import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("SNOWFLAKE_STORAGE_INTEGRATION", "test_integration")
os.environ.setdefault("GCS_BUCKET_URL", "gs://test-bucket")
os.environ.setdefault("GCS_BUCKET_NAME", "test-bucket")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

# Avoid importing the real Salesforce client module (GCS client init at import).
_fake_sf = types.ModuleType("services.salesforce.client")


class _BulkAPI2Client:
    def __init__(self, *a, **k):
        pass

    async def create_query_job(self, *a, **k):
        return "job-1"

    async def get_job_results(self, job_id):
        return []


_fake_sf.BulkAPI2Client = _BulkAPI2Client
_fake_sf.MigrationFailureTracker = MagicMock
_fake_sf.check_field_exists = MagicMock(return_value=True)
_fake_sf.get_all_salesforce_fields = MagicMock(return_value={})
_fake_sf.get_last_checked_dates_and_fields = MagicMock(return_value={})
_fake_sf.is_unsupported_salesforce_object = MagicMock(return_value=False)
_fake_sf.process_salesforce_results_in_chunks = lambda results, size: [results]
_fake_sf.salesforce_connection = MagicMock()
_fake_sf.build_salesforce_oauth_context = MagicMock(return_value={})
_fake_sf.is_non_retryable_salesforce_auth_error = MagicMock(return_value=False)
_fake_sf.update_last_synced_date = MagicMock()
_fake_sf.update_table_status_sf_to_snowflake = MagicMock()
sys.modules["services.salesforce.client"] = _fake_sf

sys.modules.setdefault("datasyncher_connections", MagicMock())
sys.modules.setdefault("datasyncher_connections.snowflake", MagicMock())
sys.modules.setdefault("datasyncher_connections.snowflake.pem", MagicMock())

from services.salesforce.as_source.to_databricks import (  # noqa: E402
    _chunk_modstamp_checkpoint,
    _effective_last_synced,
    _is_incremental_salesforce_sync,
    _normalize_selected_tables,
    _rows_migrated,
    process_salesforce_to_databricks_mapping,
)


def test_normalize_selected_tables_flat_and_pairs():
    assert _normalize_selected_tables(["Account", "Contact"]) == ["Account", "Contact"]
    assert _normalize_selected_tables([["Account", "ACCOUNT"], ["Contact"]]) == [
        "Account",
        "Contact",
    ]
    assert _normalize_selected_tables([None, [], "Lead"]) == ["Lead"]


def test_rows_migrated_prefers_extract_then_migration_record():
    assert _rows_migrated(5, None) == 5
    assert _rows_migrated(0, {"staging_records_count": 843}) == 843
    assert _rows_migrated(0, {"staging_records_count": 0}) == 0


def test_effective_last_synced_parses_db_watermark():
    from datetime import datetime, timezone

    db = datetime(2026, 1, 1, tzinfo=timezone.utc)
    effective = _effective_last_synced(db.isoformat())
    assert effective == db


def test_drop_and_reload_skips_incremental_soql_filter():
    watermark = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _is_incremental_salesforce_sync(watermark, True, False) is True
    assert _is_incremental_salesforce_sync(watermark, True, True) is False
    assert _is_incremental_salesforce_sync(watermark, False, False) is False


def test_chunk_modstamp_checkpoint_prefixes_sfmod():
    import pandas as pd

    df = pd.DataFrame(
        {
            "ID": ["1"],
            "SYSTEMMODSTAMP": ["2026-09-04T11:00:46.000+0000"],
        }
    )
    ckpt = _chunk_modstamp_checkpoint(df, 120)
    assert ckpt is not None
    assert ckpt.startswith("sfmod:")
    assert "10:58:46" in ckpt


@pytest.mark.asyncio
async def test_process_mapping_calls_copy_per_chunk_no_object_end_merge(monkeypatch):
    copy_calls = []
    transfer_calls = []

    class FakeBulk:
        def __init__(self, *a, **k):
            pass

        async def create_query_job(self, *a, **k):
            return "job-1"

        async def get_job_results(self, job_id):
            return [
                {"Id": "001A", "Name": "Acme", "IsDeleted": False},
                {"Id": "001B", "Name": "Beta", "IsDeleted": False},
            ]

    class FakeSFObject:
        def describe(self):
            return {
                "fields": [
                    {"name": "Id", "type": "id"},
                    {"name": "Name", "type": "string"},
                    {"name": "IsDeleted", "type": "boolean"},
                    {"name": "SystemModstamp", "type": "datetime"},
                ]
            }

    class FakeSF:
        def __getattr__(self, name):
            return FakeSFObject()

    def fake_copy(*args, **kwargs):
        copy_calls.append({"blob": args[-1] if args else None, "rows": len(args[0]) if args else 0})

    mod = "services.salesforce.as_source.to_databricks"
    monkeypatch.setattr(f"{mod}.salesforce_connection", lambda *a, **k: FakeSF())
    monkeypatch.setattr(f"{mod}.initialize_dynamic_checker", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.is_unsupported_salesforce_object", lambda *a, **k: False)
    monkeypatch.setattr(
        f"{mod}.get_all_salesforce_fields",
        lambda *a, **k: {"Id": {}, "Name": {}, "IsDeleted": {}, "SystemModstamp": {}},
    )
    monkeypatch.setattr(
        f"{mod}.check_field_exists",
        lambda sf, obj, field: field in ("IsDeleted", "SystemModstamp"),
    )
    monkeypatch.setattr(f"{mod}.get_last_checked_dates_and_fields", lambda *a, **k: {})
    monkeypatch.setattr(f"{mod}.update_table_status_sf_to_snowflake", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.update_last_synced_date", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.check_table_exists_databricks", lambda *a, **k: False)
    monkeypatch.setattr(
        f"{mod}.ensure_databricks_table_from_salesforce_describe",
        lambda *a, **k: {"ID": "STRING", "NAME": "STRING"},
    )
    monkeypatch.setattr(f"{mod}.BulkAPI2Client", FakeBulk)
    monkeypatch.setattr(
        f"{mod}.process_salesforce_results_in_chunks",
        lambda results, size: [results],
    )
    monkeypatch.setattr(f"{mod}.get_migration_record_info", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.get_last_synced_for_table", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.get_migration_record_staging_count", lambda *a, **k: 2)
    monkeypatch.setattr(f"{mod}.should_resume_migration", lambda *a, **k: False)
    monkeypatch.setattr(f"{mod}.finalize_migration_record", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.flush_orphan_staging_tables", lambda *a, **k: 0)
    monkeypatch.setattr(f"{mod}.drop_staging_table", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.copy_function", fake_copy)
    monkeypatch.setattr(
        f"{mod}.data_transfer_logs",
        lambda *a, **k: transfer_calls.append({"args": a, "kwargs": k}),
    )
    monkeypatch.setattr(
        f"{mod}.transform_salesforce_chunk_to_dataframe",
        lambda rows, *a, **k: __import__("pandas").DataFrame(rows),
    )
    monkeypatch.setattr(
        f"{mod}.build_attributes_details_field",
        lambda *a, **k: {},
    )
    monkeypatch.setattr(
        f"{mod}.salesforce_staging_blob_name",
        lambda *a, **k: "temp_data_salesforce_ACCOUNT_11.csv",
    )

    rows = await process_salesforce_to_databricks_mapping(
        mapping="Account",
        salesforce_details={
            "state": "x",
            "access_token": "t",
            "instance_url": "https://example.salesforce.com",
            "refresh_token": "r",
            "CLIENT_ID": "id",
            "CLIENT_SECRET": "sec",
        },
        destination_engine=MagicMock(),
        databricks_schema="bronze",
        catalog="main",
        connection_id=11,
        job_name="sf_dbx_job",
        migration_session_id=99,
        cmp_id=1,
        user_id=1,
        environment="production",
        chunk_count=1000,
        session=1,
        is_updated="_FIVETRAN_SYNCED",
        is_deleted="_FIVETRAN_DELETED",
        safety_interval_cutoff=datetime(2026, 1, 15, tzinfo=timezone.utc),
        drop_and_reload=False,
    )

    assert rows == 2
    assert len(copy_calls) == 1
    assert copy_calls[0]["blob"] == "temp_data_salesforce_ACCOUNT_11.csv"
    assert len(transfer_calls) == 1
    assert transfer_calls[0]["args"][3] == "Account"
    assert transfer_calls[0]["args"][8] == 2
    assert transfer_calls[0]["kwargs"]["job_name"] == "sf_dbx_job"


@pytest.mark.asyncio
async def test_unsupported_salesforce_object_raises_after_marking_failed(monkeypatch):
    """Unsupported objects must raise so _run_one counts them as failed, not success."""
    status_calls = []
    mod = "services.salesforce.as_source.to_databricks"

    monkeypatch.setattr(f"{mod}.salesforce_connection", lambda *a, **k: MagicMock())
    monkeypatch.setattr(f"{mod}.initialize_dynamic_checker", lambda *a, **k: None)
    monkeypatch.setattr(f"{mod}.is_unsupported_salesforce_object", lambda *a, **k: True)
    monkeypatch.setattr(f"{mod}.get_last_checked_dates_and_fields", lambda *a, **k: {})
    monkeypatch.setattr(
        f"{mod}.update_table_status_sf_to_snowflake",
        lambda *a, **k: status_calls.append(a),
    )
    monkeypatch.setattr(
        "utils.error_utils.log_error_to_tool_log",
        lambda **k: None,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="SKIPPED:.*AttachedContentNote.*not queryable"):
        await process_salesforce_to_databricks_mapping(
            mapping="AttachedContentNote",
            salesforce_details={
                "state": "x",
                "access_token": "t",
                "instance_url": "https://example.salesforce.com",
                "refresh_token": "r",
                "CLIENT_ID": "id",
                "CLIENT_SECRET": "sec",
            },
            destination_engine=MagicMock(),
            databricks_schema="bronze",
            catalog="main",
            connection_id=11,
            job_name="sf_dbx_job",
            migration_session_id=99,
            cmp_id=1,
            user_id=1,
            environment="production",
            chunk_count=1000,
            session=1,
            is_updated="_FIVETRAN_SYNCED",
            is_deleted="_FIVETRAN_DELETED",
            safety_interval_cutoff=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )

    assert status_calls
    assert status_calls[-1][2] == "failed"
