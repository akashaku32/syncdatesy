"""
Unit tests for SFTP to Snowflake Service.
Tests all service layer functionality including credential validation, connection setup, and status override calculations.
"""
import pytest
import asyncio
import sys
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime

# Mock Django modules before importing service
sys.modules['apps'] = MagicMock()
sys.modules['apps.elttool'] = MagicMock()
sys.modules['apps.elttool.models'] = MagicMock()

# Mock Django models
mock_connection = MagicMock()
sys.modules['apps.elttool.models'].Connection = mock_connection

# Mock database_utils module to avoid Django imports
mock_db_utils = MagicMock()
mock_db_utils.get_last_checked_dates_and_fields = MagicMock(return_value={})
sys.modules['utils.database_utils'] = mock_db_utils

# Import model & service under test
from models.sftp_snowflake_models import SFTPSnowflakeMigrationRequest
from services.sftp.as_source.to_snowflake import SFTPSnowflakeService

@pytest.mark.asyncio
async def test_sftp_migration_credential_validation_failure():
    """Test validation fails when essential configs are missing."""
    request = SFTPSnowflakeMigrationRequest(
        source_config={
            # Missing sftp_host / host
            "sftp_username": "sftpuser",
            "authentication_type": "password",
            "sftp_password": "sftppassword",
            "file_type": "csv"
        },
        destination_config={
            "account": "testaccount",
            "user": "testuser",
            "password": "testpass",
            "warehouse": "TEST_WH",
            "database": "TEST_DB",
            "schema": "TEST_SCHEMA"
        },
        connection_id=123,
        selected_tables=["data/file1.csv"],
        safety_interval="0",
        migration_session_id=789,
        cmp_id=1,
        user_id=1,
        session=456,
        execution_order="sequential",
        chunk_count=5000
    )

    with patch('services.sftp.as_source.to_snowflake.handle_service_error') as mock_handle_error:
        mock_handle_error.return_value = {"email_sent": True}
        
        with pytest.raises(ValueError) as exc:
            await SFTPSnowflakeService.migrate(request)
            
        assert "Missing required SFTP credentials" in str(exc.value)
        mock_handle_error.assert_called_once()
        assert mock_handle_error.call_args[1]["send_email"] is True


@pytest.mark.asyncio
async def test_sftp_connection_failure_triggers_email():
    """Test that SFTP connection failures send connection failure emails."""
    request = SFTPSnowflakeMigrationRequest(
        source_config={
            "sftp_host": "sftp.example.com",
            "sftp_username": "sftpuser",
            "authentication_type": "password",
            "sftp_password": "sftppassword",
            "file_type": "csv"
        },
        destination_config={
            "account": "testaccount",
            "user": "testuser",
            "password": "testpass",
            "warehouse": "TEST_WH",
            "database": "TEST_DB",
            "schema": "TEST_SCHEMA"
        },
        connection_id=123,
        selected_tables=["data/file1.csv"],
        safety_interval="0",
        migration_session_id=789,
        cmp_id=1,
        user_id=1,
        session=456,
        execution_order="sequential",
        chunk_count=5000
    )

    # Mock sf_connect to succeed, but _open_sftp_client to fail
    with patch('services.sftp.as_source.to_snowflake.sf_connect') as mock_sf_connect, \
         patch('services.sftp.as_source.to_snowflake._open_sftp_client') as mock_open_sftp, \
         patch('services.sftp.as_source.to_snowflake.create_connection_failure_email') as mock_create_email, \
         patch('services.sftp.as_source.to_snowflake.EmailConfiguration') as mock_email_config, \
         patch('services.sftp.as_source.to_snowflake.send_email_with_retry') as mock_send_email:
        
        mock_sf_connect.return_value = MagicMock()
        mock_open_sftp.side_effect = Exception("SFTP Socket Timeout")
        mock_create_email.return_value = ("<html></html>", "plain text")
        
        mock_config_instance = MagicMock()
        mock_config_instance.is_configured.return_value = True
        mock_email_config.return_value = mock_config_instance

        with pytest.raises(Exception) as exc:
            await SFTPSnowflakeService.migrate(request)

        assert "SFTP Socket Timeout" in str(exc.value)
        mock_create_email.assert_called_once()
        mock_send_email.assert_called_once()


@pytest.mark.asyncio
async def test_snowflake_schema_auto_creation():
    """Test Snowflake schema is verified and created if missing."""
    request = SFTPSnowflakeMigrationRequest(
        source_config={
            "sftp_host": "sftp.example.com",
            "sftp_username": "sftpuser",
            "authentication_type": "password",
            "sftp_password": "sftppassword",
            "file_type": "csv",
            "single_file_table_mapping": {"data/file1.csv": "unified_table"}
        },
        destination_config={
            "account": "testaccount",
            "user": "testuser",
            "password": "testpass",
            "warehouse": "TEST_WH",
            "database": "TEST_DB",
            "schema": "TEST_SCHEMA",
            "gcs_service_account_json": "{}"
        },
        connection_id=123,
        selected_tables=["data/file1.csv"],
        safety_interval="0",
        migration_session_id=789,
        cmp_id=1,
        user_id=1,
        session=456,
        execution_order="sequential",
        chunk_count=5000
    )

    # Mock connections to succeed
    with patch('services.sftp.as_source.to_snowflake.sf_connect') as mock_sf_connect, \
         patch('services.sftp.as_source.to_snowflake._open_sftp_client') as mock_open_sftp, \
         patch('services.sftp.as_source.to_snowflake.gcs_client') as mock_gcs_client, \
         patch('services.sftp.as_source.to_snowflake.ensure_gcs_storage_integration') as mock_esi, \
         patch('services.sftp.as_source.to_snowflake.get_gcp_service_account') as mock_gsa, \
         patch('services.sftp.as_source.to_snowflake.ensure_bucket_iam_for_integration') as mock_iam, \
         patch('services.sftp.as_source.to_snowflake.assert_allowed_prefix_matches') as mock_prefix:
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        
        # Simulate USE SCHEMA raising exception (missing schema) to trigger schema creation
        mock_cursor.execute.side_effect = [
            None,  # USE DATABASE
            Exception("Schema does not exist"),  # USE SCHEMA fail
            None,  # CREATE SCHEMA
            None,  # USE SCHEMA success
        ]
        mock_sf_connect.return_value = mock_conn
        
        mock_open_sftp.return_value = (MagicMock(), MagicMock(), MagicMock())
        mock_gcs_client.return_value = MagicMock()

        # We raise exception later in migration flow to terminate early after connection setups
        with patch('services.sftp.as_source.to_snowflake.get_last_checked_dates_and_fields', side_effect=Exception("Terminal Test Interrupt")):
            with pytest.raises(Exception):
                await SFTPSnowflakeService.migrate(request)

        # Check that CREATE SCHEMA IF NOT EXISTS was executed
        assert mock_cursor.execute.call_count >= 3
        calls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        assert any("CREATE SCHEMA IF NOT EXISTS" in c for c in calls)


@pytest.mark.asyncio
async def test_sftp_migration_multi_file_all_success():
    """Test multi-file mode where all files are processed successfully."""
    request = SFTPSnowflakeMigrationRequest(
        source_config={
            "sftp_host": "sftp.example.com",
            "sftp_username": "sftpuser",
            "authentication_type": "password",
            "sftp_password": "sftppassword",
            "file_type": "csv",
            "file_mapping_mode": "multi_files_single_table",
            "multi_files_table_name": "unified_table",
            "include_subfolders": False
        },
        destination_config={
            "account": "testaccount",
            "user": "testuser",
            "password": "testpass",
            "warehouse": "TEST_WH",
            "database": "TEST_DB",
            "schema": "TEST_SCHEMA",
            "gcs_service_account_json": "{}"
        },
        connection_id=123,
        selected_tables=["unified_table"],
        safety_interval="0",
        migration_session_id=789,
        cmp_id=1,
        user_id=1,
        session=456,
        execution_order="sequential",
        chunk_count=5000
    )

    with patch('services.sftp.as_source.to_snowflake.sf_connect') as mock_sf_connect, \
         patch('services.sftp.as_source.to_snowflake._open_sftp_client') as mock_open_sftp, \
         patch('services.sftp.as_source.to_snowflake.gcs_client') as mock_gcs_client, \
         patch('services.sftp.as_source.to_snowflake.ensure_gcs_storage_integration') as mock_esi, \
         patch('services.sftp.as_source.to_snowflake.get_gcp_service_account') as mock_gsa, \
         patch('services.sftp.as_source.to_snowflake.ensure_bucket_iam_for_integration') as mock_iam, \
         patch('services.sftp.as_source.to_snowflake.assert_allowed_prefix_matches') as mock_prefix, \
         patch('services.sftp.as_source.to_snowflake._walk_sftp_files') as mock_walk, \
         patch('services.sftp.as_source.to_snowflake.is_sftp_file_compatible_with_type') as mock_compat, \
         patch('services.sftp.as_source.to_snowflake.read_sftp_file_in_chunks') as mock_read_chunks, \
         patch('services.sftp.as_source.to_snowflake.upload_chunk_to_gcs') as mock_upload, \
         patch('services.sftp.as_source.to_snowflake.copy_into_staging_from_gcs_batch') as mock_copy, \
         patch('services.sftp.as_source.to_snowflake.merge_staging_into_final') as mock_merge, \
         patch('services.sftp.as_source.to_snowflake.log_migration_completion') as mock_log_completion, \
         patch('services.sftp.as_source.to_snowflake.handle_migration_completion') as mock_handle_completion:

        mock_conn = MagicMock()
        mock_sf_connect.return_value = mock_conn

        mock_sftp_cli = MagicMock()
        mock_open_sftp.return_value = (mock_sftp_cli, MagicMock(), MagicMock())
        mock_gcs_client.return_value = MagicMock()
        mock_compat.return_value = (True, None)

        # Mock walk files (discovered 1 file)
        from services.source_file_helpers.file_source_base import SourceFileEntry
        mock_walk.return_value = [
            SourceFileEntry(
                file_key="data/file1.csv",
                relative_path="data/file1.csv",
                basename="file1.csv",
                size_bytes=1024,
                modified_at=datetime(2024, 1, 1)
            )
        ]

        # Mock chunk reader
        import pandas as pd
        mock_df = pd.DataFrame([{"id": 1, "name": "John"}])
        mock_read_chunks.return_value = [(mock_df, 1, True, datetime(2024, 1, 1), 1)]

        # Mock merge result
        mock_merge.return_value = {"inserted": 1, "updated": 0, "deleted": 0}

        result = await SFTPSnowflakeService.migrate(request)

        assert result["status"] == "success"
        mock_log_completion.assert_called_once()
        # Verify status override is None or "S"
        call_kwargs = mock_log_completion.call_args[1]
        assert call_kwargs["status_override"] == "S"


@pytest.mark.asyncio
async def test_sftp_migration_multi_file_partial_success():
    """Test multi-file mode where 1 file succeeds and 1 file is incompatible."""
    request = SFTPSnowflakeMigrationRequest(
        source_config={
            "sftp_host": "sftp.example.com",
            "sftp_username": "sftpuser",
            "authentication_type": "password",
            "sftp_password": "sftppassword",
            "file_type": "csv",
            "file_mapping_mode": "multi_files_single_table",
            "multi_files_table_name": "unified_table",
            "include_subfolders": False
        },
        destination_config={
            "account": "testaccount",
            "user": "testuser",
            "password": "testpass",
            "warehouse": "TEST_WH",
            "database": "TEST_DB",
            "schema": "TEST_SCHEMA",
            "gcs_service_account_json": "{}"
        },
        connection_id=123,
        selected_tables=["unified_table"],
        safety_interval="0",
        migration_session_id=789,
        cmp_id=1,
        user_id=1,
        session=456,
        execution_order="sequential",
        chunk_count=5000
    )

    with patch('services.sftp.as_source.to_snowflake.sf_connect') as mock_sf_connect, \
         patch('services.sftp.as_source.to_snowflake._open_sftp_client') as mock_open_sftp, \
         patch('services.sftp.as_source.to_snowflake.gcs_client') as mock_gcs_client, \
         patch('services.sftp.as_source.to_snowflake.ensure_gcs_storage_integration') as mock_esi, \
         patch('services.sftp.as_source.to_snowflake.get_gcp_service_account') as mock_gsa, \
         patch('services.sftp.as_source.to_snowflake.ensure_bucket_iam_for_integration') as mock_iam, \
         patch('services.sftp.as_source.to_snowflake.assert_allowed_prefix_matches') as mock_prefix, \
         patch('services.sftp.as_source.to_snowflake._walk_sftp_files') as mock_walk, \
         patch('services.sftp.as_source.to_snowflake.is_sftp_file_compatible_with_type') as mock_compat, \
         patch('services.sftp.as_source.to_snowflake.read_sftp_file_in_chunks') as mock_read_chunks, \
         patch('services.sftp.as_source.to_snowflake.upload_chunk_to_gcs') as mock_upload, \
         patch('services.sftp.as_source.to_snowflake.copy_into_staging_from_gcs_batch') as mock_copy, \
         patch('services.sftp.as_source.to_snowflake.merge_staging_into_final') as mock_merge, \
         patch('services.sftp.as_source.to_snowflake.log_migration_completion') as mock_log_completion, \
         patch('services.sftp.as_source.to_snowflake.handle_migration_completion') as mock_handle_completion, \
         patch('services.sftp.as_source.to_snowflake.postgres_connection') as mock_pg:

        mock_conn = MagicMock()
        mock_sf_connect.return_value = mock_conn

        mock_sftp_cli = MagicMock()
        mock_sftp_cli.stat.return_value = MagicMock(st_mtime=1700000000, st_size=1024)
        mock_open_sftp.return_value = (mock_sftp_cli, MagicMock(), MagicMock())
        mock_gcs_client.return_value = MagicMock()

        # Mock walk files (discovered 2 files: data/file1.csv, data/file2.csv)
        from services.source_file_helpers.file_source_base import SourceFileEntry
        mock_walk.return_value = [
            SourceFileEntry(
                file_key="data/file1.csv",
                relative_path="data/file1.csv",
                basename="file1.csv",
                size_bytes=1024,
                modified_at=datetime(2024, 1, 1)
            ),
            SourceFileEntry(
                file_key="data/file2.csv",
                relative_path="data/file2.csv",
                basename="file2.csv",
                size_bytes=1024,
                modified_at=datetime(2024, 1, 2)
            )
        ]

        # file1 compatible, file2 incompatible
        mock_compat.side_effect = [
            (True, None),
            (False, "incompatible schema")
        ]

        # Mock chunk reader (only called for file1)
        import pandas as pd
        mock_df = pd.DataFrame([{"id": 1, "name": "John"}])
        mock_read_chunks.return_value = [(mock_df, 1, True, datetime(2024, 1, 1), 1)]

        # Mock merge result
        mock_merge.return_value = {"inserted": 1, "updated": 0, "deleted": 0}

        # Mock postgres connection for warning logs
        mock_pg_conn = MagicMock()
        mock_pg_cursor = MagicMock()
        mock_pg_cursor.fetchone.return_value = [999]  # migration_record_id
        mock_pg_conn.cursor.return_value = mock_pg_cursor
        mock_pg.return_value = mock_pg_conn

        result = await SFTPSnowflakeService.migrate(request)

        assert result["status"] == "success"
        mock_log_completion.assert_called_once()
        # Verify status override is "P"
        call_kwargs = mock_log_completion.call_args[1]
        assert call_kwargs["status_override"] == "P"


@pytest.mark.asyncio
async def test_sftp_migration_multi_file_all_skipped():
    """Test multi-file mode where all files are skipped."""
    request = SFTPSnowflakeMigrationRequest(
        source_config={
            "sftp_host": "sftp.example.com",
            "sftp_username": "sftpuser",
            "authentication_type": "password",
            "sftp_password": "sftppassword",
            "file_type": "csv",
            "file_mapping_mode": "multi_files_single_table",
            "multi_files_table_name": "unified_table",
            "include_subfolders": False
        },
        destination_config={
            "account": "testaccount",
            "user": "testuser",
            "password": "testpass",
            "warehouse": "TEST_WH",
            "database": "TEST_DB",
            "schema": "TEST_SCHEMA",
            "gcs_service_account_json": "{}"
        },
        connection_id=123,
        selected_tables=["unified_table"],
        safety_interval="0",
        migration_session_id=789,
        cmp_id=1,
        user_id=1,
        session=456,
        execution_order="sequential",
        chunk_count=5000
    )

    with patch('services.sftp.as_source.to_snowflake.sf_connect') as mock_sf_connect, \
         patch('services.sftp.as_source.to_snowflake._open_sftp_client') as mock_open_sftp, \
         patch('services.sftp.as_source.to_snowflake.gcs_client') as mock_gcs_client, \
         patch('services.sftp.as_source.to_snowflake.ensure_gcs_storage_integration') as mock_esi, \
         patch('services.sftp.as_source.to_snowflake.get_gcp_service_account') as mock_gsa, \
         patch('services.sftp.as_source.to_snowflake.ensure_bucket_iam_for_integration') as mock_iam, \
         patch('services.sftp.as_source.to_snowflake.assert_allowed_prefix_matches') as mock_prefix, \
         patch('services.sftp.as_source.to_snowflake._walk_sftp_files') as mock_walk, \
         patch('services.sftp.as_source.to_snowflake.is_sftp_file_compatible_with_type') as mock_compat, \
         patch('services.sftp.as_source.to_snowflake.log_migration_completion') as mock_log_completion, \
         patch('services.sftp.as_source.to_snowflake.handle_migration_completion') as mock_handle_completion, \
         patch('services.sftp.as_source.to_snowflake.postgres_connection') as mock_pg:

        mock_conn = MagicMock()
        mock_sf_connect.return_value = mock_conn

        mock_sftp_cli = MagicMock()
        mock_sftp_cli.stat.return_value = MagicMock(st_mtime=1700000000, st_size=1024)
        mock_open_sftp.return_value = (mock_sftp_cli, MagicMock(), MagicMock())
        mock_gcs_client.return_value = MagicMock()

        # Mock walk files (discovered 1 file)
        from services.source_file_helpers.file_source_base import SourceFileEntry
        mock_walk.return_value = [
            SourceFileEntry(
                file_key="data/file1.csv",
                relative_path="data/file1.csv",
                basename="file1.csv",
                size_bytes=1024,
                modified_at=datetime(2024, 1, 1)
            )
        ]

        # file1 incompatible
        mock_compat.return_value = (False, "incompatible schema")

        # Mock postgres connection for failure logs
        mock_pg_conn = MagicMock()
        mock_pg_cursor = MagicMock()
        mock_pg_cursor.fetchone.return_value = [999]  # migration_record_id
        mock_pg_conn.cursor.return_value = mock_pg_cursor
        mock_pg.return_value = mock_pg_conn

        result = await SFTPSnowflakeService.migrate(request)

        assert result["status"] == "failed"
        mock_log_completion.assert_called_once()
        # Verify status override is "F"
        call_kwargs = mock_log_completion.call_args[1]
        assert call_kwargs["status_override"] == "F"


