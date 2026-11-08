import sys

from pathlib import Path

from unittest.mock import MagicMock, patch



MIGRATION_FUNCTION_DIR = Path(__file__).resolve().parents[1]

if str(MIGRATION_FUNCTION_DIR) not in sys.path:

    sys.path.insert(0, str(MIGRATION_FUNCTION_DIR))



from utils.error_utils import (  # noqa: E402

    job_level_terminal_write_allowed,

    log_migration_completion,

    should_include_operation_counts,

    should_use_failures_in_completion,

)





def _mock_db_gate(has_in_progress: bool, total_tables: int = 2, terminal_tables: int = 2):

    cursor = MagicMock()

    cursor.fetchone.return_value = (has_in_progress, total_tables, terminal_tables)

    conn = MagicMock()

    conn.cursor.return_value = cursor

    return conn, cursor





def test_gate_allows_progress_status_without_db_check():

    assert job_level_terminal_write_allowed("job-1", "I", celery_child_mode=False) is True





def test_gate_blocks_terminal_when_celery_child_mode():

    assert job_level_terminal_write_allowed("job-1", "S", celery_child_mode=True) is False

    assert job_level_terminal_write_allowed("job-1", "P", celery_child_mode=True) is False

    assert job_level_terminal_write_allowed("job-1", "F", celery_child_mode=True) is False





@patch("utils.database_utils.postgres_connection")

def test_gate_blocks_terminal_when_migration_records_in_progress(mock_pg):

    conn, _ = _mock_db_gate(True, total_tables=3, terminal_tables=1)

    mock_pg.return_value = conn



    assert job_level_terminal_write_allowed("job-1", "S", celery_child_mode=False) is False





@patch("utils.database_utils.postgres_connection")

def test_gate_allows_listing_tables_terminal_while_job_level_mr_in_progress(mock_pg):

    conn, _ = _mock_db_gate(True, total_tables=0, terminal_tables=0)

    mock_pg.return_value = conn



    assert job_level_terminal_write_allowed(

        "S3_CON_listing_tables_25653", "F", celery_child_mode=False

    ) is True





@patch("utils.database_utils.postgres_connection")

def test_gate_blocks_terminal_when_not_all_tables_terminal(mock_pg):

    conn, _ = _mock_db_gate(False, total_tables=5, terminal_tables=3)

    mock_pg.return_value = conn



    assert job_level_terminal_write_allowed("job-1", "S", celery_child_mode=False) is False





@patch("utils.database_utils.postgres_connection")

def test_gate_allows_terminal_when_all_migration_records_terminal(mock_pg):

    conn, _ = _mock_db_gate(False, total_tables=3, terminal_tables=3)

    mock_pg.return_value = conn



    assert job_level_terminal_write_allowed("job-1", "S", celery_child_mode=False) is True





@patch("utils.database_utils.postgres_connection")

def test_gate_blocks_terminal_when_db_unavailable(mock_pg):

    mock_pg.return_value = None



    assert job_level_terminal_write_allowed("job-1", "S", celery_child_mode=False) is False





@patch("utils.database_utils.postgres_connection")

def test_gate_blocks_terminal_on_db_exception(mock_pg):

    mock_pg.side_effect = RuntimeError("db down")



    assert job_level_terminal_write_allowed("job-1", "S", celery_child_mode=False) is False





@patch("utils.error_utils.update_migration_status_log", return_value=False)

@patch("utils.error_utils.insert_tool_log")

@patch("utils.error_utils.job_level_terminal_write_allowed", return_value=True)

def test_log_migration_completion_skips_job_level_insert_fallback(

    _mock_gate,

    mock_insert,

    _mock_update,

):

    result = log_migration_completion(

        execution_time=1.0,

        total_rows=10,

        cmp_id=1,

        connection_id=2,

        user_id=3,

        session="sess-1",

        job_name="job-1",

        migration_record_id=None,

    )



    assert result is False

    mock_insert.assert_not_called()





@patch("utils.error_utils.update_migration_status_log", return_value=True)

@patch("utils.error_utils.job_level_terminal_write_allowed", return_value=False)

def test_log_migration_completion_blocked_by_gate(_mock_gate, _mock_update):

    result = log_migration_completion(

        execution_time=1.0,

        total_rows=10,

        cmp_id=1,

        connection_id=2,

        user_id=3,

        session="sess-1",

        job_name="job-1",

        migration_record_id=None,

        celery_child_mode=True,

    )



    assert result is False

    _mock_update.assert_not_called()


def test_should_include_operation_counts_matches_s3_file_source_rules():
    assert should_include_operation_counts("upsert_custom_key") is True
    assert should_include_operation_counts("upsert_file_row") is False
    assert should_include_operation_counts("append_file_modified") is False
    assert should_include_operation_counts(None) is False


def test_should_use_failures_in_completion_for_retl_only():
    assert should_use_failures_in_completion("snowflake_to_salesforce") is True
    assert should_use_failures_in_completion("snowflake_to_salesforce_sandbox") is True
    assert should_use_failures_in_completion(
        function_path="/Salesforce-Snowflake-migration-yield"
    ) is True
    assert should_use_failures_in_completion(
        function_path="/SalesforceSandbox-Snowflake-migration-yield"
    ) is True
    assert should_use_failures_in_completion("salesforce_to_snowflake") is False
    assert should_use_failures_in_completion("google_drive_to_snowflake") is False
    assert should_use_failures_in_completion("amazon_s3") is False
    assert should_use_failures_in_completion("sftp_to_snowflake") is False


@patch("utils.error_utils.update_migration_status_log", return_value=True)
@patch("utils.error_utils.job_level_terminal_write_allowed", return_value=True)
def test_log_migration_completion_message_uses_failures_for_retl(_mock_gate, mock_update):
    log_migration_completion(
        execution_time=43.74,
        total_rows=9,
        cmp_id=1,
        connection_id=68,
        user_id=2,
        session="26527",
        inserts=9,
        updates=0,
        failures=0,
        total_tables=2,
        successful_tables=2,
        failed_tables=0,
        job_name="REV_ETL_CON_migration_26527",
        include_operation_counts=True,
    )
    message = mock_update.call_args.kwargs["message"]
    assert "Inserts: 9, Updates: 0, Failures: 0" in message
    assert "Deletes:" not in message


@patch("utils.error_utils.update_migration_status_log", return_value=True)
@patch("utils.error_utils.job_level_terminal_write_allowed", return_value=True)
def test_log_migration_completion_message_uses_deletes_for_file_source(_mock_gate, mock_update):
    log_migration_completion(
        execution_time=43.74,
        total_rows=9,
        cmp_id=1,
        connection_id=60,
        user_id=2,
        session="25747",
        inserts=9,
        updates=0,
        deletes=0,
        total_tables=2,
        successful_tables=2,
        failed_tables=0,
        job_name="GOOGLE_DRIVE_SNOW_migration_25747",
        include_operation_counts=True,
    )
    message = mock_update.call_args.kwargs["message"]
    assert "Inserts: 9, Updates: 0, Deletes: 0" in message
    assert "Failures:" not in message



def test_handle_update_schema_listing_error_logs_terminal_update_schema_failure():
    from fastapi import HTTPException

    from utils.error_utils import handle_update_schema_listing_error

    data = MagicMock()
    data.job_name = "GOOGLE_DRIVE_SNOW_listing_tables_25747"
    data.log_id = 99
    data.cmp_id = 1
    data.connection_id = 2
    data.user_id = 3
    data.migration_session_id = 25747

    with patch("utils.error_utils.handle_service_error") as mock_handle:
        handle_update_schema_listing_error(
            HTTPException(status_code=422, detail="Failed to parse CSV file"),
            data,
            service_name="Google Drive",
        )

    mock_handle.assert_called_once()
    assert mock_handle.call_args.kwargs["context"]["operation"] == "UPDATE_SCHEMA"
    assert mock_handle.call_args.kwargs["context"]["is_terminal_failure"] is True
    assert mock_handle.call_args.kwargs["context"]["service_name"] == "Google Drive"

