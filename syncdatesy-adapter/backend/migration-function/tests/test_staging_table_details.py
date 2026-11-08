"""Unit tests for load checkpoint helpers (replaces staging_table_details)."""

from unittest.mock import MagicMock, patch

from utils.load_checkpoint import (
    SF_MODSTAMP_CHECKPOINT_PREFIX,
    finalize_migration_record,
    last_synced_from_checkpoint,
    normalize_load_checkpoint,
    parse_sync_datetime,
    safe_folder_checkpoint,
    safe_salesforce_modstamp_checkpoint,
    should_resume_migration,
)


def test_safe_folder_checkpoint_default_30_minutes():
    assert safe_folder_checkpoint("2026-08-21T10.00.00Z", 30) == "2026-08-21T09.30.00Z"
    assert safe_folder_checkpoint("2026-08-21T10.00.00Z") == "2026-08-21T09.30.00Z"


def test_safe_salesforce_modstamp_checkpoint_applies_safety_seconds():
    raw = "2026-09-04T11:00:46+00:00"
    assert safe_salesforce_modstamp_checkpoint(raw, 120) == "2026-09-04T10:58:46+0000"
    stored, is_sf = normalize_load_checkpoint(
        f"{SF_MODSTAMP_CHECKPOINT_PREFIX}{raw}",
        safety_interval_seconds=120,
    )
    assert is_sf
    assert stored == f"{SF_MODSTAMP_CHECKPOINT_PREFIX}2026-09-04T10:58:46+0000"
    assert last_synced_from_checkpoint(stored) == "2026-09-04T10:58:46+0000"
    assert parse_sync_datetime(stored) is not None


def test_should_resume_migration_statuses():
    assert should_resume_migration("in_progress", "2026-08-01T11.30.00Z")
    assert should_resume_migration("failed", "2026-08-01T11.30.00Z")
    assert not should_resume_migration("failed", None)
    assert not should_resume_migration("completed", "2026-08-01T11.30.00Z")
    assert not should_resume_migration(None, None)


def test_finalize_migration_record_updates_count(monkeypatch):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    with patch("utils.database_utils.postgres_connection", return_value=conn):
        finalize_migration_record(
            connection_id=23,
            table_name="custtable",
            migration_session_id=100,
            job_name="job-a",
            final_records_count=500,
        )

    cur.execute.assert_called_once()
    conn.commit.assert_called_once()
