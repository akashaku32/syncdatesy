import sys
import io
import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

from models.sftp_source_models import SFTPPKSuggestionRequest, SFTPListingTables
from services.sftp.source import SFTPSourceService


class FakeStat:
    def __init__(self, filename, st_mode, st_size, st_mtime):
        self.filename = filename
        self.st_mode = st_mode
        self.st_size = st_size
        self.st_mtime = st_mtime


@pytest.fixture
def mock_sftp_client():
    sftp = MagicMock()
    # Mock listdir_attr to return files
    sftp.listdir_attr.return_value = [
        FakeStat("library.csv", 32768, 100, 1600000000),  # normal file (S_IFREG = 32768)
        FakeStat("books.json", 32768, 500, 1600000000),
    ]
    
    # Mock open to return CSV / JSON content
    def mock_open(path, mode):
        if "library.csv" in path:
            return io.BytesIO(b"id,name,author\n1,Harry Potter,JK Rowling\n2,The Hobbit,Tolkien\n")
        elif "books.json" in path:
            return io.BytesIO(b'{"id": 1, "title": "Books"}\n{"id": 2, "title": "Hobbit"}\n')
        return io.BytesIO(b"")
        
    sftp.open.side_effect = mock_open
    return sftp


@pytest.fixture
def base_sftp_config():
    return {
        "sftp_host": "sftp.test.com",
        "sftp_port": 22,
        "sftp_username": "testuser",
        "sftp_password": "password",
        "authentication_type": "password",
        "root_folder": "upload",
        "file_type": "csv",
        "file_mapping_method": "single_table_per_file",
        "single_file_table_mapping": {"library.csv": "LIBRARY_TABLE"},
        "load_method": "upsert_custom_key",
        "custom_primary_key": {"library.csv": ["id"]},
    }


@pytest.mark.asyncio
@patch("services.sftp.source._open_sftp_client")
@patch("services.sftp.source.postgres_connection")
@patch("services.sftp.source._fetch_snowflake_tables_from_config")
async def test_suggest_primary_keys_success(
    mock_fetch_sf, mock_pg_conn, mock_open_client, mock_sftp_client, base_sftp_config
):
    # Setup mock clients & transport
    mock_open_client.return_value = (mock_sftp_client, MagicMock(), MagicMock())
    
    # Setup pg mock cursor to return active mapping metadata (3 columns unpacked)
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        ("LIBRARY_TABLE", ["id"], None)
    ]
    mock_pg_conn.return_value.cursor.return_value = mock_cursor
    
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}

    payload = {
        "sample_rows": 10,
        "connection_id": 123,
        "cmp_id": 1,
        "user_id": 1,
        "sftp": base_sftp_config,
    }
    request = SFTPPKSuggestionRequest(**payload)
    
    result = await SFTPSourceService.suggest_primary_keys(request)
    
    assert result["status"] == "success"
    assert len(result["tables"]) == 1
    table_info = result["tables"][0]
    assert table_info["table_name"] == "LIBRARY_TABLE"
    assert table_info["file_key"] == "library.csv"
    
    # Verify column statistics are computed
    cols = table_info["columns"]
    id_col = next(c for c in cols if c["column_name"] == "ID")
    assert id_col["uniqueness_score"] == 100.0
    assert id_col["non_null_score"] == 100.0


@patch("services.sftp.source._open_sftp_client")
@patch("services.sftp.source.postgres_connection")
@patch("services.sftp.source._fetch_snowflake_tables_from_config")
def test_list_tables_with_schema_locking_metadata(
    mock_fetch_sf, mock_pg_conn, mock_open_client, mock_sftp_client, base_sftp_config
):
    mock_open_client.return_value = (mock_sftp_client, MagicMock(), MagicMock())
    
    # Setup pg mock cursor returning a table selection with a last_synced value (migrated, 6 columns unpacked)
    mock_cursor = MagicMock()
    import datetime
    mock_cursor.fetchall.return_value = [
        ("library.csv", "LIBRARY_TABLE", True, datetime.datetime(2026, 6, 17, 12, 0, 0), ["id"], False)
    ]
    mock_pg_conn.return_value.cursor.return_value = mock_cursor
    
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}
    
    # Add destination_config to configuration so Snowflake table exists check runs
    config_with_dest = base_sftp_config.copy()
    config_with_dest["destination_config"] = {
        "account": "test-acct",
        "username": "test-user",
        "database": "test-db",
        "schema": "test-schema",
    }
    
    payload = {
        "connection_id": 123,
        "SFTP": config_with_dest,
        "cmp_id": 1,
        "user_id": 1,
    }
    request = SFTPListingTables(**payload)
    result = SFTPSourceService.list_tables_with_schema(request)
    print("DEBUG Locking result:", result)
    assert len(result["tables"]) == 1
    table_info = result["tables"][0]
    assert table_info["table"] == "LIBRARY_TABLE"
    assert table_info["is_migrated"] is True
    assert table_info["table_name_locked"] is True
    assert table_info["locked_table_name"] == "LIBRARY_TABLE"
    assert table_info["locked_primary_keys"] == ["id"]


@patch("services.sftp.source._open_sftp_client")
@patch("services.sftp.source.postgres_connection")
@patch("services.sftp.source._fetch_snowflake_tables_from_config")
def test_list_files_locking_metadata(
    mock_fetch_sf, mock_pg_conn, mock_open_client, mock_sftp_client, base_sftp_config
):
    mock_sftp_client.listdir_attr.return_value = [
        FakeStat("library.csv", 32768, 100, 1600000000),
        FakeStat("books.csv", 32768, 500, 1600000000),
    ]
    mock_open_client.return_value = (mock_sftp_client, MagicMock(), MagicMock())
    
    mock_cursor = MagicMock()
    import datetime
    mock_cursor.fetchall.return_value = [
        ("LIBRARY_TABLE", "library.csv", datetime.datetime(2026, 6, 17, 12, 0, 0), ["id"])
    ]
    mock_pg_conn.return_value.cursor.return_value = mock_cursor
    
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}
    
    config_with_dest = base_sftp_config.copy()
    config_with_dest["destination_config"] = {
        "account": "test-acct",
        "username": "test-user",
        "database": "test-db",
        "schema": "test-schema",
    }
    
    payload = {
        "connection_id": 123,
        "SFTP": config_with_dest,
        "cmp_id": 1,
        "user_id": 1,
    }
    request = SFTPListingTables(**payload)
    result = SFTPSourceService.list_files(request)
    
    assert result["files_count"] == 2
    
    lib_info = next(t for t in result["tables"] if t["file_key"] == "library.csv")
    assert lib_info["table"] == "LIBRARY_TABLE"
    assert lib_info["is_migrated"] is True
    assert lib_info["table_name_locked"] is True
    assert lib_info["locked_table_name"] == "LIBRARY_TABLE"
    assert lib_info["locked_primary_keys"] == ["id"]
    
    books_info = next(t for t in result["tables"] if t["file_key"] == "books.csv")
    assert books_info["table"] == "BOOKS"
    assert books_info["is_migrated"] is False
    assert books_info["table_name_locked"] is False



