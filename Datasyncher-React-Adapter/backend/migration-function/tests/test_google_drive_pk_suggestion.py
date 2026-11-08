"""Dedicated Google Drive PK-suggestion and mapping-lock tests."""

import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from models.google_drive_models import (
    GoogleDriveListingTables,
    GoogleDrivePKSuggestionRequest,
)
from services.google_drive.as_source.to_snowflake import GoogleDriveSnowflakeService


@pytest.fixture
def drive_files():
    return [
        {
            "id": "file-1",
            "name": "library.csv",
            "path": "library.csv",
            "mimeType": "text/csv",
            "modifiedTime": "2026-06-17T12:00:00Z",
            "size": "68",
        },
        {
            "id": "file-2",
            "name": "books.csv",
            "path": "books.csv",
            "mimeType": "text/csv",
            "modifiedTime": "2026-06-17T12:00:00Z",
            "size": "40",
        },
    ]


@pytest.fixture
def base_drive_config():
    return {
        "CLIENT_ID": "drive-client",
        "CLIENT_SECRET": "drive-secret",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "token_expires_at": 2_000_000_000,
        "folder_id": "folder-1",
        "folder_name": "Imports",
        "include_subfolders": False,
        "file_type": "csv",
        "file_mapping_method": "single_table_per_file",
        "single_file_table_mapping": {"library.csv": "LIBRARY_TABLE"},
        "load_method": "upsert_custom_key",
        "destination_config": {"account": "test-account"},
    }


def _validated_config(config):
    return {**config, "access_token": "access-token", "folder_id": "folder-1"}


def _open_drive_file(_self, key, _mode="rb"):
    if key == "library.csv":
        content = b"id,name,author\n1,Harry Potter,JK Rowling\n2,The Hobbit,Tolkien\n"
    elif key == "books.csv":
        content = b"id,title\n1,Books\n2,Hobbit\n"
    else:
        raise FileNotFoundError(key)

    stream = tempfile.SpooledTemporaryFile(mode="w+b")
    stream.write(content)
    return stream


@pytest.mark.asyncio
@patch("services.google_drive.as_source.to_snowflake._fetch_snowflake_tables_from_config")
@patch("services.google_drive.as_source.to_snowflake.postgres_connection")
@patch("services.google_drive.as_source.to_snowflake.GoogleDriveFileClient.open", _open_drive_file)
@patch("services.google_drive.as_source.to_snowflake._walk_drive_files")
@patch("services.google_drive.as_source.to_snowflake._validated_drive_listing_context")
async def test_suggest_primary_keys_success(
    mock_validate,
    mock_walk,
    mock_pg_conn,
    mock_fetch_sf,
    drive_files,
    base_drive_config,
):
    mock_validate.side_effect = _validated_config
    mock_walk.return_value = drive_files[:1]
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [("LIBRARY_TABLE", ["id"], None)]
    mock_pg_conn.return_value.cursor.return_value = mock_cursor

    request = GoogleDrivePKSuggestionRequest(
        sample_rows=10,
        connection_id=123,
        cmp_id=1,
        user_id=2,
        GoogleDrive=base_drive_config,
    )
    result = await GoogleDriveSnowflakeService.suggest_primary_keys(request)

    assert result["status"] == "success"
    assert len(result["tables"]) == 1
    table = result["tables"][0]
    assert table["table_name"] == "LIBRARY_TABLE"
    assert table["file_key"] == "library.csv"
    id_column = next(column for column in table["columns"] if column["column_name"] == "ID")
    assert id_column["uniqueness_score"] == 100.0
    assert id_column["non_null_score"] == 100.0


@patch("services.google_drive.as_source.to_snowflake._fetch_snowflake_tables_from_config")
@patch("services.google_drive.as_source.to_snowflake.postgres_connection")
@patch("services.google_drive.as_source.to_snowflake.GoogleDriveFileClient.open", _open_drive_file)
@patch("services.google_drive.as_source.to_snowflake._walk_drive_files")
@patch("services.google_drive.as_source.to_snowflake._validated_drive_listing_context")
def test_list_tables_with_schema_locking_metadata(
    mock_validate,
    mock_walk,
    mock_pg_conn,
    mock_fetch_sf,
    drive_files,
    base_drive_config,
):
    mock_validate.side_effect = _validated_config
    mock_walk.return_value = drive_files[:1]
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        ("LIBRARY_TABLE", "library.csv", datetime(2026, 6, 17, 12, 0, 0), ["id"])
    ]
    mock_pg_conn.return_value.cursor.return_value = mock_cursor

    request = GoogleDriveListingTables(
        connection_id=123,
        GoogleDrive=base_drive_config,
        cmp_id=1,
        user_id=2,
    )
    result = GoogleDriveSnowflakeService.list_tables_with_schema(request)

    assert len(result["tables"]) == 1
    table = result["tables"][0]
    assert table["table"] == "LIBRARY_TABLE"
    assert table["is_migrated"] is True
    assert table["table_name_locked"] is True
    assert table["locked_table_name"] == "LIBRARY_TABLE"
    assert table["locked_primary_keys"] == ["id"]


@patch("services.google_drive.as_source.to_snowflake._walk_drive_files")
@patch("services.google_drive.as_source.to_snowflake._validated_drive_listing_context")
def test_google_drive_schema_refresh_rejects_selected_file_type_mismatch(
    mock_validated_context,
    mock_walk_drive_files,
    base_drive_config,
):
    config = {**base_drive_config, "file_type": "csv"}
    mock_validated_context.return_value = config
    mock_walk_drive_files.return_value = [
        {
            "id": "orders-json",
            "name": "orders.json",
            "path": "orders.json",
            "mimeType": "application/json",
            "modifiedTime": "2026-08-20T10:00:00Z",
            "size": "20",
        }
    ]

    with pytest.raises(HTTPException, match="configured file_type 'csv' does not match"):
        GoogleDriveSnowflakeService.list_tables_with_schema(
            GoogleDriveListingTables(
                connection_id=85,
                GoogleDrive={
                    **config,
                    "single_file_table_mapping": {"orders.json": "ORDERS"},
                },
                cmp_id=70,
                user_id=7,
            )
        )


@patch("services.google_drive.as_source.to_snowflake._fetch_snowflake_tables_from_config")
@patch("services.google_drive.as_source.to_snowflake.postgres_connection")
@patch("services.google_drive.as_source.to_snowflake._walk_drive_files")
@patch("services.google_drive.as_source.to_snowflake._validated_drive_listing_context")
def test_list_files_locking_metadata(
    mock_validate,
    mock_walk,
    mock_pg_conn,
    mock_fetch_sf,
    drive_files,
    base_drive_config,
):
    mock_validate.side_effect = _validated_config
    mock_walk.return_value = drive_files
    mock_fetch_sf.return_value = {"LIBRARY_TABLE"}
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        ("LIBRARY_TABLE", "library.csv", datetime(2026, 6, 17, 12, 0, 0), ["id"])
    ]
    mock_pg_conn.return_value.cursor.return_value = mock_cursor

    request = GoogleDriveListingTables(
        connection_id=123,
        GoogleDrive=base_drive_config,
        cmp_id=1,
        user_id=2,
    )
    result = GoogleDriveSnowflakeService.list_files(request)

    assert result["files_count"] == 2
    library = next(item for item in result["tables"] if item["file_key"] == "library.csv")
    assert library["table"] == "LIBRARY_TABLE"
    assert library["is_migrated"] is True
    assert library["table_name_locked"] is True
    assert library["locked_table_name"] == "LIBRARY_TABLE"
    assert library["locked_primary_keys"] == ["id"]

    books = next(item for item in result["tables"] if item["file_key"] == "books.csv")
    assert books["table"] == "BOOKS"
    assert books["is_migrated"] is False
    assert books["table_name_locked"] is False
