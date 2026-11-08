import io
from datetime import datetime
from unittest.mock import patch

import pytest

from models.adls_source_models import ADLSListingTables, ADLSPKSuggestionRequest
from services.adls.source import ADLSSourceService
from services.source_file_helpers.file_source_base import SourceFileEntry


def _entry(file_key, *, size=100, modified_at=None):
    return SourceFileEntry(
        file_key=file_key,
        relative_path=file_key.rsplit("/", 1)[-1],
        basename=file_key.rsplit("/", 1)[-1],
        size_bytes=size,
        modified_at=modified_at or datetime(2026, 6, 17, 12, 0, 0),
        inferred_file_type="csv",
    )


@pytest.fixture
def base_adls_config():
    return {
        "tenant_id": "tenant-id",
        "client_id": "client-id",
        "client_secret": "secret",
        "account_name": "teststorageacct",
        "container_name": "test-container",
        "folder_name": "upload",
        "include_subfolders": False,
        "file_type": "csv",
        "file_mapping_method": "single_table_per_file",
        "single_file_table_mapping": {"library.csv": "LIBRARY_TABLE"},
        "load_method": "upsert_custom_key",
    }


@pytest.mark.asyncio
@patch("services.adls.source._fetch_snowflake_tables_from_config")
@patch("services.adls.source._read_pk_state")
@patch("services.adls.source._open_adls_file_sample")
@patch("services.adls.source._list_adls_files")
@patch("services.adls.source._get_adls_file_system_client")
async def test_suggest_primary_keys_success(
    mock_get_client,
    mock_list_files,
    mock_open_sample,
    mock_read_pk_state,
    mock_fetch_sf,
    base_adls_config,
):
    mock_get_client.return_value = (object(), "teststorageacct", "test-container")
    mock_list_files.return_value = [_entry("upload/library.csv")]
    mock_open_sample.return_value = io.BytesIO(
        b"id,name,author\n1,Harry Potter,JK Rowling\n2,The Hobbit,Tolkien\n"
    )
    mock_read_pk_state.return_value = {
        "LIBRARY_TABLE": {
            "pk_locked": False,
            "existing_primary_keys": ["id"],
        }
    }
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}

    request = ADLSPKSuggestionRequest(
        sample_rows=10,
        connection_id=123,
        cmp_id=1,
        user_id=1,
        ADLS=base_adls_config,
    )

    result = await ADLSSourceService.suggest_primary_keys(request)

    assert result["status"] == "success"
    assert len(result["tables"]) == 1
    table_info = result["tables"][0]
    assert table_info["table_name"] == "LIBRARY_TABLE"
    assert table_info["file_key"] == "upload/library.csv"

    columns = table_info["columns"]
    id_column = next(column for column in columns if column["column_name"] == "ID")
    assert id_column["uniqueness_score"] == 100.0
    assert id_column["non_null_score"] == 100.0


@patch("services.adls.source._fetch_snowflake_tables_from_config")
@patch("services.adls.source._load_existing_file_metadata")
@patch("services.adls.source._open_adls_file_sample")
@patch("services.adls.source._list_adls_files")
@patch("services.adls.source._get_adls_file_system_client")
def test_list_tables_with_schema_locking_metadata(
    mock_get_client,
    mock_list_files,
    mock_open_sample,
    mock_load_metadata,
    mock_fetch_sf,
    base_adls_config,
):
    mock_get_client.return_value = (object(), "teststorageacct", "test-container")
    mock_list_files.return_value = [_entry("upload/library.csv")]
    mock_open_sample.return_value = io.BytesIO(
        b"id,name,author\n1,Harry Potter,JK Rowling\n2,The Hobbit,Tolkien\n"
    )
    mock_load_metadata.return_value = {
        "upload/library.csv": {
            "table": "LIBRARY_TABLE",
            "is_migrated": True,
            "last_synced": "2026-06-17T12:00:00+00:00",
            "primary_keys": ["id"],
        }
    }
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}

    config = {
        **base_adls_config,
        "destination_config": {
            "account": "test-acct",
            "username": "test-user",
            "database": "test-db",
            "schema": "test-schema",
        },
        "custom_primary_key": {"LIBRARY_TABLE": ["id"]},
    }
    request = ADLSListingTables(
        connection_id=123,
        ADLS=config,
        cmp_id=1,
        user_id=1,
    )

    result = ADLSSourceService.list_tables_with_schema(request)

    assert len(result["tables"]) == 1
    table_info = result["tables"][0]
    assert table_info["table"] == "LIBRARY_TABLE"
    assert table_info["is_migrated"] is True
    assert table_info["table_name_locked"] is True
    assert table_info["locked_table_name"] == "LIBRARY_TABLE"
    assert table_info["locked_primary_keys"] == ["id"]


@patch("services.adls.source._fetch_snowflake_tables_from_config")
@patch("services.adls.source._load_existing_file_metadata")
@patch("services.adls.source._list_adls_files")
@patch("services.adls.source._get_adls_file_system_client")
def test_list_files_locking_metadata(
    mock_get_client,
    mock_list_files,
    mock_load_metadata,
    mock_fetch_sf,
    base_adls_config,
):
    mock_get_client.return_value = (object(), "teststorageacct", "test-container")
    mock_list_files.return_value = [
        _entry("upload/library.csv"),
        _entry("upload/books.csv", size=500),
    ]
    mock_load_metadata.return_value = {
        "upload/library.csv": {
            "table": "LIBRARY_TABLE",
            "is_migrated": True,
            "last_synced": "2026-06-17T12:00:00+00:00",
            "primary_keys": ["id"],
        }
    }
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}

    config = {
        **base_adls_config,
        "destination_config": {
            "account": "test-acct",
            "username": "test-user",
            "database": "test-db",
            "schema": "test-schema",
        },
    }
    request = ADLSListingTables(
        connection_id=123,
        ADLS=config,
        cmp_id=1,
        user_id=1,
    )

    result = ADLSSourceService.list_files(request)

    assert result["files_count"] == 2

    library = next(table for table in result["tables"] if table["file_key"] == "upload/library.csv")
    assert library["table"] == "LIBRARY_TABLE"
    assert library["is_migrated"] is True
    assert library["table_name_locked"] is True
    assert library["locked_table_name"] == "LIBRARY_TABLE"
    assert library["locked_primary_keys"] == ["id"]

    books = next(table for table in result["tables"] if table["file_key"] == "upload/books.csv")
    assert books["table"] == "BOOKS"
    assert books["is_migrated"] is False
    assert books["table_name_locked"] is False
