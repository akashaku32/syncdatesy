import io
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from services.google_drive.as_destination.from_snowflake import (
    _get_or_create_folder,
    _upload_file_path_to_drive,
    _upload_file_to_drive,
)
from services.google_drive.client import _resolve_folder, validate_google_drive_connection
from services.google_drive.as_source.to_snowflake import (
    GoogleDriveFileClient,
    GoogleDriveSnowflakeService,
    SUPPORTED_GOOGLE_DRIVE_SNOWFLAKE_FILE_TYPES,
    _complete_google_drive_table_zero_rows,
    _infer_google_drive_migration_file_type,
    _is_manual_google_drive_migration,
    _last_synced_from_table_state,
    _log_skipped_transfer,
    _persist_drive_table_failure,
    _read_drive_dataframe_sample,
    _report_drive_partial_table_failure,
    _resolve_selected_files,
    _should_apply_delta_check,
    _should_skip_unchanged_file,
    prepare_drive_file_for_read,
    read_drive_file_in_chunks,
)
from models.google_drive_snowflake_models import GoogleDriveSnowflakeMigrationRequest
from models.google_drive_models import GoogleDriveListingTables


class _MockResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON")
        return self._payload

    @property
    def headers(self):
        return getattr(self, "_headers", {})


class _StreamWithoutReadable:
    """Match older SpooledTemporaryFile objects used by the Drive runtime."""

    def __init__(self, content):
        self._stream = io.BytesIO(content)

    def read(self, *args, **kwargs):
        return self._stream.read(*args, **kwargs)

    def seek(self, *args, **kwargs):
        return self._stream.seek(*args, **kwargs)

    def close(self):
        return self._stream.close()


class _DriveClientWithLegacyStream:
    def __init__(self, key, content):
        self.key = key
        self.content = content

    def open(self, key, mode="rb"):
        assert key == self.key
        return _StreamWithoutReadable(self.content)

    def stat(self, key):
        assert key == self.key
        return SimpleNamespace(st_mtime=0, st_size=len(self.content))


@patch("services.google_drive.as_source.to_snowflake.read_dataframe_sample")
def test_drive_sample_reader_passes_rewound_bytesio_to_shared_parser(mock_read_sample):
    content = b"id\tname\n1\tAnn\n"
    source_stream = _StreamWithoutReadable(content)
    source_stream.seek(0, io.SEEK_END)
    client = MagicMock()
    client.open.return_value = source_stream
    client.stat.return_value = SimpleNamespace(st_mtime=0, st_size=len(content))

    parsed = object()

    def inspect_stream(stream, **reader_options):
        assert isinstance(stream, io.BytesIO)
        assert stream.tell() == 0
        assert stream.read() == content
        assert reader_options == {"file_type": "tsv", "delimiter": r"\t"}
        return parsed

    mock_read_sample.side_effect = inspect_stream

    result = _read_drive_dataframe_sample(
        client,
        "customers.tsv",
        file_type="tsv",
        delimiter=r"\t",
    )

    assert result is parsed
    client.open.assert_called_once_with("customers.tsv", "rb")
    assert source_stream._stream.closed is True


@pytest.mark.parametrize(
    ("key", "content", "file_type", "options", "expected_rows"),
    [
        ("customers.csv", b"id,name\n1,Ann\n2,Bob\n", "csv", {"delimiter": ","}, 2),
        ("customers.tsv", b"id\tname\n1\tAnn\n2\tBob\n", "tsv", {"delimiter": "\\t"}, 2),
        ("customers.json", b'[{"id": 1}, {"id": 2}]', "json", {}, 2),
        (
            "customers.xml",
            b"<customers><customer><id>1</id></customer><customer><id>2</id></customer></customers>",
            "xml",
            {"xml_mode": "unpacked"},
            2,
        ),
    ],
)
def test_drive_chunk_reader_adapts_streams_without_readable(
    key,
    content,
    file_type,
    options,
    expected_rows,
):
    chunks = list(
        read_drive_file_in_chunks(
            _DriveClientWithLegacyStream(key, content),
            key,
            file_type=file_type,
            chunk_size=1,
            **options,
        )
    )

    data_chunks = [chunk for chunk, _number, _last, _modified, _total in chunks if not chunk.empty]
    assert sum(len(chunk) for chunk in data_chunks) == expected_rows
    assert chunks[-1][2] is True
    assert chunks[-1][4] == expected_rows


def test_drive_chunk_reader_adapts_binary_streams_without_readable():
    import fastavro
    import pyarrow as pa
    import pyarrow.parquet as pq

    avro_content = io.BytesIO()
    fastavro.writer(
        avro_content,
        {
            "type": "record",
            "name": "Customer",
            "fields": [{"name": "id", "type": "int"}],
        },
        [{"id": 1}, {"id": 2}],
    )
    parquet_content = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2]}), parquet_content, row_group_size=1)

    for key, file_type, content in (
        ("customers.avro", "avro", avro_content.getvalue()),
        ("customers.parquet", "parquet", parquet_content.getvalue()),
    ):
        chunks = list(
            read_drive_file_in_chunks(
                _DriveClientWithLegacyStream(key, content),
                key,
                file_type=file_type,
                chunk_size=1,
            )
        )

        data_chunks = [chunk for chunk, _number, _last, _modified, _total in chunks if not chunk.empty]
        assert sum(len(chunk) for chunk in data_chunks) == 2
        assert chunks[-1][2] is True
        assert chunks[-1][4] == 2


@pytest.mark.parametrize(
    ("key", "content", "file_type", "expected_error"),
    [
        ("customers.json", b"id,name\n1,Ann\n", "json", "does not look like JSON"),
        ("customers.xml", b'[{"id": 1}]', "xml", "does not look like XML"),
        ("customers.avro", b"not-avro", "avro", "missing Avro magic bytes"),
        ("customers.parquet", b"not-parquet", "parquet", "missing PAR1 magic bytes"),
    ],
)
def test_drive_preflight_rejects_content_that_does_not_match_file_type(
    key,
    content,
    file_type,
    expected_error,
):
    with pytest.raises(ValueError, match=expected_error):
        prepare_drive_file_for_read(
            _DriveClientWithLegacyStream(key, content),
            key,
            file_type=file_type,
        )


@patch("services.google_drive.as_source.to_snowflake.update_table_status_snowflake")
@patch("services.google_drive.as_source.to_snowflake.handle_service_error")
def test_google_drive_persists_error_before_failed_status(mock_handle_error, mock_update_status):
    events = []
    mock_handle_error.side_effect = lambda **kwargs: events.append("log") or {"log_logged": True}
    mock_update_status.side_effect = lambda *args, **kwargs: events.append("status")

    _persist_drive_table_failure(
        error=ValueError("bad employee row"),
        connection_id=60,
        table_name="EMPLOYEES",
        job_name="drive_job",
        migration_session_id=26030,
        log_id=100,
        cmp_id=1,
        user_id=2,
        connection_name="DRIVE_TEST_1",
        company_name="Test Company",
    )

    assert events == ["log", "status"]
    assert mock_handle_error.call_args.kwargs["send_email"] is True
    assert mock_handle_error.call_args.kwargs["is_final_attempt"] is True


@patch("services.google_drive.as_source.to_snowflake.update_table_status_snowflake")
@patch("services.google_drive.as_source.to_snowflake.handle_service_error")
def test_google_drive_does_not_publish_failed_status_without_error_log(
    mock_handle_error,
    mock_update_status,
):
    mock_handle_error.return_value = {"log_logged": False}

    with pytest.raises(RuntimeError, match="Failed to persist Google Drive failure message"):
        _persist_drive_table_failure(
            error=ValueError("bad employee row"),
            connection_id=60,
            table_name="EMPLOYEES",
            job_name="drive_job",
            migration_session_id=26030,
            log_id=100,
            cmp_id=1,
            user_id=2,
            connection_name="DRIVE_TEST_1",
            company_name="Test Company",
        )

    mock_update_status.assert_not_called()


@patch("utils.email_utils.send_error_notification", return_value=True)
@patch("services.google_drive.as_source.to_snowflake.insert_tool_log")
@patch("services.google_drive.as_source.to_snowflake.get_migration_record_id", return_value=501)
@patch("services.google_drive.as_source.to_snowflake.postgres_connection")
def test_google_drive_partial_multi_file_failure_uses_table_name_in_email_and_summary_log(
    mock_pg_conn,
    mock_get_record,
    mock_insert_log,
    mock_send_email,
):
    mock_pg_conn.return_value.cursor.return_value = MagicMock()

    result = _report_drive_partial_table_failure(
        connection_id=60,
        table_name="EMPLOYEES",
        failed_file_messages=["employees_bad.csv: invalid CSV content"],
        job_name="drive_job",
        migration_session_id=26030,
        cmp_id=1,
        user_id=2,
        connection_name="DRIVE_TEST_1",
        company_name="Test Company",
    )

    assert result == {"warning_logged": True, "email_sent": True}
    assert mock_insert_log.call_args.kwargs["table_name"] == "EMPLOYEES"
    assert mock_insert_log.call_args.kwargs["status"] == "P"
    email = mock_send_email.call_args.kwargs
    assert email["table_name"] == "EMPLOYEES"
    assert email.get("is_consolidated", False) is False
    assert email["additional_context"]["partial_success"] is True


def _valid_payload(**overrides):
    config = {
        "CLIENT_ID": "client-123",
        "CLIENT_SECRET": "secret-123",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "folder_id": "folder-123",
        "folder_name": "exports",
    }
    config.update(overrides)
    return {"GoogleDrive": config}


def _drive_snowflake_request(**overrides):
    payload = {
        "source_config": {
            "CLIENT_ID": "client-123",
            "CLIENT_SECRET": "secret-123",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "folder_id": "folder-123",
            "folder_name": "exports",
            "file_type": "csv",
        },
        "destination_config": {
            "account": "acct",
            "user": "user",
            "password": "pass",
            "warehouse": "WH",
            "database": "DB",
            "schema": "PUBLIC",
        },
        "connection_id": 85,
        "cmp_id": 70,
        "user_id": 7,
        "session": 1015,
        "selected_tables": ["EMPLOYEES_20260526T072647Z"],
        "safety_interval": "0",
        "job_name": "DRive_manual_1015_EMPLOYEES_20260526T072647Z",
        "migration_session_id": 1015,
        "execution_order": "sequential",
        "chunk_count": 5000,
        "log_id": 911937,
    }
    payload.update(overrides)
    return GoogleDriveSnowflakeMigrationRequest(**payload)


def test_google_drive_manual_job_bypasses_safety_interval():
    assert _is_manual_google_drive_migration("G_DRIVE_CON_manual_1023_COURSES->CUSTOMERS")
    assert not _is_manual_google_drive_migration("G_DRIVE_CON_auto_sync_1023_COURSES->CUSTOMERS")


def test_google_drive_delta_load_methods_skip_unchanged_file():
    last_synced = datetime(2026, 6, 30, 1, 36, tzinfo=timezone.utc)
    same_modified_time = datetime(2026, 6, 30, 1, 31, tzinfo=timezone.utc)

    assert _should_skip_unchanged_file("append_file_modified", last_synced, same_modified_time)
    assert _should_skip_unchanged_file("upsert_custom_key", last_synced, same_modified_time)
    assert _should_skip_unchanged_file("upsert_file_row", last_synced, same_modified_time)


def test_google_drive_uses_the_adls_manual_delta_policy():
    assert not _should_apply_delta_check("append_file_modified", is_manual_migration=True)
    assert not _should_apply_delta_check("upsert_custom_key", is_manual_migration=True)
    assert _should_apply_delta_check("upsert_file_row", is_manual_migration=True)
    assert _should_apply_delta_check("append_file_modified", is_manual_migration=False)


def test_google_drive_uses_shared_last_checked_timestamp_key_for_delta_load():
    last_synced = datetime(2026, 6, 30, 1, 36, tzinfo=timezone.utc)

    assert _last_synced_from_table_state({"last_checked_date": last_synced}) == last_synced
    assert _last_synced_from_table_state({"last_synced": last_synced}) is None


def test_google_drive_snowflake_supports_configured_avro_file_type():
    client = GoogleDriveFileClient(
        "access-token",
        [{"id": "file-1", "name": "employees.avro", "path": "employees.avro"}],
    )

    file_type = _infer_google_drive_migration_file_type(
        {"file_type": "avro"},
        client,
        ["employees.avro"],
    )

    assert file_type == "avro"
    assert file_type in SUPPORTED_GOOGLE_DRIVE_SNOWFLAKE_FILE_TYPES


def test_google_drive_snowflake_infers_avro_file_type_from_extension():
    client = GoogleDriveFileClient(
        "access-token",
        [{"id": "file-1", "name": "employees.avro", "path": "exports/employees.avro"}],
    )

    file_type = _infer_google_drive_migration_file_type({}, client, ["exports/employees.avro"])

    assert file_type == "avro"
    assert file_type in SUPPORTED_GOOGLE_DRIVE_SNOWFLAKE_FILE_TYPES


def test_google_drive_snowflake_supports_configured_parquet_file_type():
    client = GoogleDriveFileClient(
        "access-token",
        [{"id": "file-1", "name": "employees.parquet", "path": "employees.parquet"}],
    )

    file_type = _infer_google_drive_migration_file_type(
        {"file_type": "parquet"},
        client,
        ["employees.parquet"],
    )

    assert file_type == "parquet"
    assert file_type in SUPPORTED_GOOGLE_DRIVE_SNOWFLAKE_FILE_TYPES


def test_google_drive_snowflake_infers_parquet_file_type_from_extension():
    client = GoogleDriveFileClient(
        "access-token",
        [{"id": "file-1", "name": "employees.parquet", "path": "exports/employees.parquet"}],
    )

    file_type = _infer_google_drive_migration_file_type({}, client, ["exports/employees.parquet"])

    assert file_type == "parquet"
    assert file_type in SUPPORTED_GOOGLE_DRIVE_SNOWFLAKE_FILE_TYPES


def test_google_drive_prefers_newer_sheet_for_mapped_table():
    drive_files = [
        {
            "id": "old-tsv",
            "name": "customers.tsv",
            "path": "tsv/customers.tsv",
            "mimeType": "text/tab-separated-values",
            "modifiedTime": "2026-06-26T09:04:18Z",
        },
        {
            "id": "new-sheet",
            "name": "customers",
            "path": "customers",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "modifiedTime": "2026-06-30T09:00:00Z",
        },
    ]

    selected_files = _resolve_selected_files(
        selected_tables=["CUSTOMERS"],
        drive_files=drive_files,
        single_file_map={"tsv/customers.tsv": "CUSTOMERS"},
        file_mapping_mode="single_table_per_file",
        multi_files_table_name="CUSTOMERS",
    )

    assert selected_files == ["customers"]


@patch("services.google_drive.as_source.to_snowflake._walk_drive_files")
@patch("services.google_drive.as_source.to_snowflake._validated_drive_listing_context")
def test_google_drive_schema_listing_prunes_deleted_exact_path_even_with_same_basename(
    mock_validated_context,
    mock_walk_drive_files,
):
    config = {
        "access_token": "access-token",
        "folder_id": "folder-123",
        "file_mapping_mode": "single_table_per_file",
        "single_file_table_mapping": {
            "archive/employees.csv": "EMPLOYEES",
        },
    }
    mock_validated_context.return_value = config
    # A different same-name file must not keep the deleted mapped path alive.
    mock_walk_drive_files.return_value = [
        {
            "id": "replacement-id",
            "name": "employees.csv",
            "path": "employees.csv",
            "mimeType": "text/csv",
            "modifiedTime": "2026-08-20T10:00:00Z",
            "size": "10",
        }
    ]

    result = GoogleDriveSnowflakeService.list_tables_with_schema(
        GoogleDriveListingTables(
            connection_id=85,
            GoogleDrive=config,
            cmp_id=70,
            user_id=7,
        )
    )

    assert result["tables"] == []
    assert result["prune_missing_tables"] is True
    assert result["stale_tables_to_prune"] == ["EMPLOYEES"]


@patch("services.google_drive.as_source.to_snowflake.data_transfer_logs")
def test_google_drive_skipped_transfer_logs_zero_rows(mock_data_transfer_logs):
    skipped_at = datetime(2026, 6, 30, 1, 36, tzinfo=timezone.utc)

    _log_skipped_transfer(
        cmp_id=70,
        connection_id=85,
        user_id=7,
        table_name="CUSTOMERS",
        skipped_at=skipped_at,
        session=1015,
        job_name="drive_auto_1015",
    )

    mock_data_transfer_logs.assert_called_once_with(
        cmp_id=70,
        connection_id=85,
        user_id=7,
        table_name="CUSTOMERS",
        start_time=skipped_at,
        end_time=skipped_at,
        columns_data=0,
        session=1015,
        insert_success=0,
        insert_failure=0,
        upsert_success=0,
        upsert_failure=0,
        job_name="drive_auto_1015",
    )


@patch("services.google_drive.as_source.to_snowflake.data_transfer_logs")
@patch("services.google_drive.as_source.to_snowflake.update_table_status_snowflake")
def test_google_drive_empty_file_completes_with_zero_rows(
    mock_update_status,
    mock_data_transfer_logs,
):
    _complete_google_drive_table_zero_rows(
        cmp_id=70,
        connection_id=85,
        user_id=7,
        table_name="CUSTOMERS",
        session=1015,
        job_name="drive_manual_1015",
        migration_session_id=1015,
    )

    mock_update_status.assert_called_once()
    status_args = mock_update_status.call_args[0]
    assert status_args[:5] == (85, "CUSTOMERS", "completed", "drive_manual_1015", 1015)
    mock_data_transfer_logs.assert_called_once()
    assert mock_data_transfer_logs.call_args.kwargs["total_rows"] == 0


def test_google_drive_skipped_tables_belong_in_successful_outcome_lists():
    processed_tables = {"CUSTOMERS", "DEPARTMENTS"}
    failed_table_names: set[str] = set()

    successful_tables_list = sorted(processed_tables)
    failed_tables_list = sorted(failed_table_names)

    assert successful_tables_list == ["CUSTOMERS", "DEPARTMENTS"]
    assert failed_tables_list == []


class _MockHeaderResponse(_MockResponse):
    def __init__(self, status_code, payload=None, text="", headers=None):
        super().__init__(status_code, payload=payload, text=text)
        self._headers = headers or {}


@pytest.mark.asyncio
@patch("services.google_drive.as_source.to_snowflake.handle_service_error")
@patch("services.google_drive.as_source.to_snowflake.update_table_status_snowflake")
@patch("services.google_drive.as_source.to_snowflake.validate_google_drive_connection")
@patch("services.google_drive.as_source.to_snowflake._get_access_token")
async def test_google_drive_snowflake_deleted_folder_logs_and_marks_failed(
    mock_get_access_token,
    mock_validate,
    mock_update_status,
    mock_handle_error,
):
    mock_get_access_token.return_value = "access-token"
    mock_validate.side_effect = HTTPException(
        status_code=422,
        detail="Configured Google Drive root folder was deleted or is in trash: exports",
    )

    result = await GoogleDriveSnowflakeService.migrate(_drive_snowflake_request())

    assert result["status"] == "failed"
    assert "deleted or is in trash" in result["message"]
    mock_update_status.assert_called_once_with(
        85,
        "EMPLOYEES_20260526T072647Z",
        "failed",
        "DRive_manual_1015_EMPLOYEES_20260526T072647Z",
        1015,
    )
    mock_handle_error.assert_called_once()
    context = mock_handle_error.call_args.kwargs["context"]
    assert context["is_terminal_failure"] is True
    assert context["service_name"] == "Google Drive to Snowflake"


@pytest.mark.asyncio
@patch("services.google_drive.as_source.to_snowflake.handle_migration_completion")
@patch("services.google_drive.as_source.to_snowflake.log_migration_completion")
@patch("services.google_drive.as_source.to_snowflake.update_table_status_snowflake")
@patch("services.google_drive.as_source.to_snowflake.handle_service_error", return_value={"log_logged": True})
@patch("services.google_drive.as_source.to_snowflake.data_transfer_logs")
@patch("services.google_drive.as_source.to_snowflake.copy_into_staging_from_gcs_batch")
@patch("services.google_drive.as_source.to_snowflake.upload_chunk_to_gcs")
@patch("services.google_drive.as_source.to_snowflake.reorder_chunk_columns_for_snowflake")
@patch("services.google_drive.as_source.to_snowflake.align_chunk_to_table_column_order")
@patch("services.google_drive.as_source.to_snowflake.ensure_tables")
@patch("services.google_drive.as_source.to_snowflake.prepare_drive_file_for_read")
@patch("services.google_drive.as_source.to_snowflake.read_sftp_file_in_chunks")
@patch("services.google_drive.as_source.to_snowflake.assert_allowed_prefix_matches")
@patch("services.google_drive.as_source.to_snowflake.ensure_bucket_iam_for_integration")
@patch("services.google_drive.as_source.to_snowflake.get_gcp_service_account")
@patch("services.google_drive.as_source.to_snowflake.ensure_gcs_storage_integration")
@patch("services.google_drive.as_source.to_snowflake.sf_connect")
@patch("services.google_drive.as_source.to_snowflake.gcs_client")
@patch("services.google_drive.as_source.to_snowflake._infer_google_drive_migration_file_type", return_value="csv")
@patch("services.google_drive.as_source.to_snowflake._resolve_selected_files", return_value=["employees.csv"])
@patch("services.google_drive.as_source.to_snowflake._walk_drive_files")
@patch("services.google_drive.as_source.to_snowflake.validate_google_drive_connection")
@patch("services.google_drive.as_source.to_snowflake._get_access_token")
async def test_google_drive_single_table_per_file_append_modified_no_op_is_success(
    mock_get_access_token,
    mock_validate,
    mock_walk_drive_files,
    _mock_resolve_selected_files,
    _mock_infer_file_type,
    mock_gcs_client,
    mock_sf_connect,
    mock_ensure_gcs_storage_integration,
    mock_get_gcp_service_account,
    mock_ensure_bucket_iam_for_integration,
    mock_assert_allowed_prefix_matches,
    mock_read_sftp_file_in_chunks,
    mock_prepare_drive_file_for_read,
    mock_ensure_tables,
    mock_align_chunk_to_table_column_order,
    mock_reorder_chunk_columns_for_snowflake,
    mock_upload_chunk_to_gcs,
    mock_copy_into_staging_from_gcs_batch,
    mock_data_transfer_logs,
    mock_handle_service_error,
    mock_update_table_status_snowflake,
    mock_log_migration_completion,
    mock_handle_migration_completion,
):
    import pandas as pd

    mock_get_access_token.return_value = "access-token"
    mock_validate.return_value = {
        "access_token": "access-token",
        "folder_id": "folder-123",
        "folder_name": "exports",
    }
    mock_walk_drive_files.return_value = [
        {
            "id": "file-1",
            "name": "employees.csv",
            "path": "employees.csv",
            "mimeType": "text/csv",
            "modifiedTime": "2026-08-25T00:00:00Z",
            "size": "12",
        }
    ]
    mock_gcs_client.return_value = MagicMock()
    mock_sf_connect.return_value = MagicMock()
    mock_ensure_gcs_storage_integration.return_value = False
    mock_get_gcp_service_account.return_value = "service@example.com"
    mock_ensure_bucket_iam_for_integration.return_value = None
    mock_assert_allowed_prefix_matches.return_value = None
    mock_prepare_drive_file_for_read.return_value = MagicMock()
    mock_read_sftp_file_in_chunks.return_value = [
        (pd.DataFrame({"id": [1]}), 1, True, datetime(2026, 8, 25, tzinfo=timezone.utc), 1)
    ]
    mock_ensure_tables.return_value = "EMPLOYEES_STAGE"
    mock_align_chunk_to_table_column_order.side_effect = lambda conn, df, *_args, **_kwargs: df
    mock_reorder_chunk_columns_for_snowflake.side_effect = lambda df, *_args, **_kwargs: df
    mock_upload_chunk_to_gcs.return_value = "employees_chunk.csv"
    mock_copy_into_staging_from_gcs_batch.return_value = None
    mock_data_transfer_logs.return_value = None
    mock_log_migration_completion.return_value = None
    mock_handle_migration_completion.return_value = None

    result = await GoogleDriveSnowflakeService.migrate(
        _drive_snowflake_request(
            file_mapping_mode="single_table_per_file",
            source_config={
                "CLIENT_ID": "client-123",
                "CLIENT_SECRET": "secret-123",
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "folder_id": "folder-123",
                "folder_name": "exports",
                "file_type": "csv",
                "load_method": "append_file_modified",
            },
        )
    )

    assert result["status"] == "success"
    assert result["files_processed"] == 1
    assert result["total_rows"] == 0
    assert result["successful_tables_count"] == 1
    assert result["failed_tables_count"] == 0
    assert result["failed_files_list"] == []
    mock_data_transfer_logs.assert_called_once()
    assert any(call.args[2] == "completed" for call in mock_update_table_status_snowflake.call_args_list)
    assert not any(call.args[2] == "failed" for call in mock_update_table_status_snowflake.call_args_list)
    mock_handle_service_error.assert_not_called()


@patch("services.google_drive.client.requests.request")
def test_validate_google_drive_connection_rejects_trashed_folder(mock_request):
    mock_request.return_value = _MockResponse(
        200,
        {
            "id": "folder-123",
            "name": "exports",
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": True,
        },
    )

    with pytest.raises(HTTPException) as exc:
        validate_google_drive_connection(_valid_payload())

    assert exc.value.status_code == 422
    assert "Configured Google Drive root folder was deleted or is in trash" in exc.value.detail
    assert "exports" in exc.value.detail


@patch("services.google_drive.as_destination.from_snowflake._drive_request")
def test_google_drive_upload_maps_deleted_folder_query_failure(mock_drive_request):
    mock_drive_request.return_value = _MockResponse(
        404,
        {"error": {"code": 404, "status": "NOT_FOUND", "message": "File not found"}},
    )

    with pytest.raises(HTTPException) as exc:
        _upload_file_to_drive(
            "access-token",
            "folder-123",
            "table.csv",
            b"a,b\n1,2\n",
            "text/csv",
            folder_name="exports",
        )

    assert exc.value.status_code == 422
    assert "Configured Google Drive root folder was not found or has been deleted" in exc.value.detail
    assert "exports" in exc.value.detail


@patch("services.google_drive.client.requests.request")
def test_validate_google_drive_connection_maps_permission_failure(mock_request):
    mock_request.return_value = _MockResponse(
        403,
        {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Insufficient permissions"}},
    )

    with pytest.raises(HTTPException) as exc:
        validate_google_drive_connection(_valid_payload())

    assert exc.value.status_code == 422
    assert "Configured Google Drive root folder is not accessible" in exc.value.detail
    assert "exports" in exc.value.detail


@patch("services.google_drive.as_destination.from_snowflake.requests.post")
@patch("services.google_drive.as_destination.from_snowflake._drive_request")
def test_google_drive_upload_maps_deleted_folder_upload_failure(mock_drive_request, mock_post):
    mock_drive_request.return_value = _MockResponse(200, {"files": []})
    mock_post.return_value = _MockResponse(
        404,
        {"error": {"code": 404, "status": "NOT_FOUND", "message": "File not found"}},
    )

    with pytest.raises(HTTPException) as exc:
        _upload_file_to_drive(
            "access-token",
            "folder-123",
            "table.csv",
            b"a,b\n1,2\n",
            "text/csv",
            folder_name="exports",
        )

    assert exc.value.status_code == 422
    assert "Configured Google Drive root folder was not found or has been deleted" in exc.value.detail
    assert "exports" in exc.value.detail


@patch("services.google_drive.as_destination.from_snowflake.requests.put")
@patch("services.google_drive.as_destination.from_snowflake.requests.post")
@patch("services.google_drive.as_destination.from_snowflake._drive_request")
def test_google_drive_large_file_uses_resumable_upload(mock_drive_request, mock_post, mock_put, tmp_path):
    mock_drive_request.return_value = _MockResponse(200, {"files": []})
    mock_post.return_value = _MockHeaderResponse(
        200,
        headers={"Location": "https://upload.example/session"},
    )
    mock_put.return_value = _MockResponse(
        200,
        {"id": "file-123", "name": "large.csv", "webViewLink": "https://drive.example/file"},
    )
    local_path = tmp_path / "large.csv"
    local_path.write_bytes(b"x" * ((5 * 1024 * 1024) + 1))

    result = _upload_file_path_to_drive(
        "access-token",
        "folder-123",
        "large.csv",
        str(local_path),
        "text/csv",
        folder_name="exports",
    )

    assert result["id"] == "file-123"
    assert mock_post.call_args.kwargs["params"]["uploadType"] == "resumable"
    assert mock_put.call_count == 1


@patch("services.google_drive.as_destination.from_snowflake._drive_request")
def test_get_or_create_folder_maps_deleted_root_during_subfolder_creation(mock_drive_request):
    mock_drive_request.side_effect = [
        _MockResponse(200, {"files": []}),
        _MockResponse(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "File not found"}}),
    ]

    with pytest.raises(HTTPException) as exc:
        _get_or_create_folder(
            "access-token",
            "folder-123",
            "nested",
            parent_folder_name="exports",
        )

    assert exc.value.status_code == 422
    assert "Configured Google Drive root folder was not found or has been deleted" in exc.value.detail
    assert "exports" in exc.value.detail


@patch("services.google_drive.client.requests.request")
def test_validate_google_drive_connection_rejects_non_folder_id(mock_request):
    mock_request.return_value = _MockResponse(
        200,
        {
            "id": "file-123",
            "name": "exports.csv",
            "mimeType": "text/csv",
            "trashed": False,
        },
    )

    with pytest.raises(HTTPException) as exc:
        validate_google_drive_connection(_valid_payload(folder_id="file-123", folder_name="exports.csv"))

    assert exc.value.status_code == 422
    assert exc.value.detail == "The provided Folder ID does not belong to a Google Drive folder."


@patch("services.google_drive.client.requests.request")
def test_validate_google_drive_connection_rejects_folder_id_name_mismatch(mock_request):
    mock_request.return_value = _MockResponse(
        200,
        {
            "id": "folder-123",
            "name": "actual-folder",
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": False,
        },
    )

    with pytest.raises(HTTPException) as exc:
        validate_google_drive_connection(_valid_payload(folder_name="exports"))

    assert exc.value.status_code == 422
    assert exc.value.detail == "Folder ID and Folder Name do not match. Please enter the correct folder details."


@patch("services.google_drive.client.requests.request")
def test_resolve_google_drive_folder_path_from_root_id(mock_request):
    mock_request.side_effect = [
        _MockResponse(
            200,
            {
                "id": "root-folder",
                "name": "DRIVE_FOLDER",
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": False,
            },
        ),
        _MockResponse(200, {"files": [{"id": "csv-folder", "name": "csv"}]}),
        _MockResponse(200, {"files": [{"id": "daily-folder", "name": "daily"}]}),
    ]

    result = _resolve_folder(
        "access-token",
        "root-folder",
        "DRIVE_FOLDER/csv/daily",
    )

    assert result == {
        "folder_id": "daily-folder",
        "folder_name": "DRIVE_FOLDER/csv/daily",
    }
    assert "'root-folder' in parents" in mock_request.call_args_list[1].kwargs["params"]["q"]
    assert "name='csv'" in mock_request.call_args_list[1].kwargs["params"]["q"]
    assert "'csv-folder' in parents" in mock_request.call_args_list[2].kwargs["params"]["q"]


@patch("services.google_drive.client.requests.request")
def test_resolve_google_drive_relative_folder_path_from_root_id(mock_request):
    mock_request.side_effect = [
        _MockResponse(
            200,
            {
                "id": "root-folder",
                "name": "DRIVE_FOLDER",
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": False,
            },
        ),
        _MockResponse(200, {"files": [{"id": "csv-folder", "name": "csv"}]}),
    ]

    result = _resolve_folder("access-token", "root-folder", "/csv")

    assert result == {
        "folder_id": "csv-folder",
        "folder_name": "DRIVE_FOLDER/csv",
    }


@patch("services.google_drive.client.requests.request")
def test_resolve_google_drive_saved_folder_path_accepts_leaf_id(mock_request):
    mock_request.return_value = _MockResponse(
        200,
        {
            "id": "csv-folder",
            "name": "csv",
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": False,
        },
    )

    result = _resolve_folder(
        "access-token",
        "csv-folder",
        "DRIVE_FOLDER/csv",
    )

    assert result == {
        "folder_id": "csv-folder",
        "folder_name": "DRIVE_FOLDER/csv",
    }
    assert mock_request.call_count == 1


@patch("services.google_drive.client.requests.post")
@patch("services.google_drive.client.requests.request")
def test_validate_google_drive_connection_refreshes_expired_token_and_succeeds(mock_request, mock_post):
    mock_request.side_effect = [
        _MockResponse(
            401,
            {"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "Invalid Credentials"}},
        ),
        _MockResponse(
            200,
            {
                "id": "folder-123",
                "name": "exports",
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": False,
            },
        ),
    ]
    mock_post.return_value = _MockResponse(
        200,
        {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
    )

    result = validate_google_drive_connection(_valid_payload())

    assert result["access_token"] == "new-access-token"
    assert result["refresh_token"] == "new-refresh-token"
    assert result["folder_id"] == "folder-123"
    assert result["folder_name"] == "exports"
    assert mock_post.call_count == 1
    assert mock_request.call_count == 2


@patch("services.google_drive.client.requests.post")
@patch("services.google_drive.client.requests.request")
def test_validate_google_drive_connection_refresh_failure_is_reported(mock_request, mock_post):
    mock_request.return_value = _MockResponse(
        401,
        {"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "Invalid Credentials"}},
    )
    mock_post.return_value = _MockResponse(
        400,
        {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
    )

    with pytest.raises(HTTPException) as exc:
        validate_google_drive_connection(_valid_payload())

    assert exc.value.status_code == 400
    assert exc.value.detail == "Token has been expired or revoked."
