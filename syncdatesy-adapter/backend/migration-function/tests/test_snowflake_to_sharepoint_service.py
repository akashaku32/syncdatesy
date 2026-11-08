import json
import os

import pytest
from fastapi import HTTPException

from models.snowflake_sharepoint_models import SnowflakeSharePointMigrationRequest
from services import snowflake_to_sharepoint_service
from utils.email_templates import build_file_destination_success_email, build_sharepoint_success_email


class _FakeResponse:
    def __init__(self, status_code, *, headers=None, text="", url="https://example/upload", json_payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text or (json.dumps(json_payload) if json_payload is not None else "")
        self.url = url
        self.content = self.text.encode("utf-8") if self.text else b""
        self._json_payload = json_payload

    def json(self):
        if self._json_payload is None:
            raise ValueError("not json")
        return self._json_payload


def test_upload_large_file_retries_transient_503(monkeypatch, tmp_path):
    local_path = tmp_path / "large.bin"
    local_path.write_bytes(b"x" * (snowflake_to_sharepoint_service.SHAREPOINT_UPLOAD_CHUNK_SIZE + 10))

    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "_create_upload_session",
        lambda access_token, drive_id, remote_path: "https://example/upload-session",
    )

    sleep_calls = []
    monkeypatch.setattr(snowflake_to_sharepoint_service.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    responses = [
        _FakeResponse(503, headers={"Content-Type": "application/json"}, text="busy"),
        _FakeResponse(202, headers={"Content-Type": "application/json"}, json_payload={"nextExpectedRanges": [str(snowflake_to_sharepoint_service.SHAREPOINT_UPLOAD_CHUNK_SIZE)]}),
        _FakeResponse(201, headers={"Content-Type": "application/json"}, json_payload={}),
    ]

    def _fake_put(url, headers=None, data=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(snowflake_to_sharepoint_service.requests, "put", _fake_put)

    import threading
    class _MockTokenManager:
        def __init__(self):
            self._lock = threading.Lock()
        def get_token(self):
            return "token"

    uploaded_size, web_url = snowflake_to_sharepoint_service._upload_large_file(
        _MockTokenManager(),
        "drive-id",
        os.fspath(local_path),
        "qa.Datasyncher/BIG_FILES/test.csv",
    )

    assert uploaded_size == local_path.stat().st_size
    assert web_url is None
    assert sleep_calls == [1.0]


def test_upload_large_file_renews_expired_session_and_resumes(monkeypatch, tmp_path):
    local_path = tmp_path / "large.bin"
    chunk_size = snowflake_to_sharepoint_service.SHAREPOINT_UPLOAD_CHUNK_SIZE
    local_path.write_bytes(b"x" * (chunk_size + 10))

    created_sessions = []

    def _fake_create_upload_session(access_token, drive_id, remote_path):
        created_sessions.append(remote_path)
        return f"https://example/upload-session-{len(created_sessions)}"

    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "_create_upload_session",
        _fake_create_upload_session,
    )

    sleep_calls = []
    monkeypatch.setattr(snowflake_to_sharepoint_service.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    responses = [
        _FakeResponse(503, headers={"Content-Type": "application/json"}, text="busy"),
        _FakeResponse(503, headers={"Content-Type": "text/html"}, text="<!DOCTYPE html><html>expired</html>"),
        _FakeResponse(202, headers={"Content-Type": "application/json"}, json_payload={"nextExpectedRanges": [str(chunk_size)]}),
        _FakeResponse(201, headers={"Content-Type": "application/json"}, json_payload={}),
    ]

    def _fake_put(url, headers=None, data=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(snowflake_to_sharepoint_service.requests, "put", _fake_put)

    import threading
    class _MockTokenManager:
        def __init__(self):
            self._lock = threading.Lock()
        def get_token(self):
            return "token"

    uploaded_size, web_url = snowflake_to_sharepoint_service._upload_large_file(
        _MockTokenManager(),
        "drive-id",
        os.fspath(local_path),
        "qa.Datasyncher/BIG_FILES/test.csv",
    )

    assert uploaded_size == local_path.stat().st_size
    assert web_url is None
    assert len(created_sessions) == 2
    assert sleep_calls == [1.0]


def _base_request():
    return SnowflakeSharePointMigrationRequest(
        source_config={
            "account": "acc",
            "user": "user",
            "authentication_type": "password",
            "password": "pw",
            "database": "DB",
            "schema": "PUBLIC",
            "warehouse": "WH",
        },
        destination_config={
            "auth_type": "client_secret",
            "tenant_id": "tenant-123",
            "client_id": "client-123",
            "client_secret": "secret-value",
            "sharepoint_site_url": "https://example.sharepoint.com/sites/demo",
            "document_library": "QaDatasyncher",
            "root_folder": "/exports",
            "file_format": "csv",
        },
        connection_id=33,
        cmp_id=7,
        user_id=11,
        session=2,
        selected_tables=["T1", "T2"],
        safety_interval="0",
        job_name="JOB_1",
        migration_session_id=2,
        execution_order="sequential",
        chunk_count=1000,
        logs_creation=False,
    )


def test_send_sharepoint_success_notification_uses_selected_group_emails(monkeypatch):
    request = _base_request().copy(update={"connection_name": "Snowflake Prod", "company_name": "Acme"})
    send_calls = []

    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "EmailConfiguration",
        lambda: type(
            "Config",
            (),
            {
                "__init__": lambda self: setattr(self, "recipients", []),
                "is_configured": lambda self: True,
                "get_missing_config": lambda self: [],
            },
        )(),
    )
    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "send_email_with_retry",
        lambda **kwargs: send_calls.append(kwargs) or True,
    )

    success = snowflake_to_sharepoint_service._send_sharepoint_success_notification(
        request=request,
        table_name="CUSTOMERS",
        table_settings={
            "notification_email_group_names": ["Finance", "Ops"],
            "notification_emails": ["ops@example.com", "finance@example.com", "ops@example.com"],
        },
        remote_path="/exports/CUSTOMERS_20260525T120000Z.csv",
        sharepoint_url=None,
        total_rows=42,
        col_count=6,
        completed_at=snowflake_to_sharepoint_service.datetime(2026, 5, 25, 12, 0, 0, tzinfo=snowflake_to_sharepoint_service.timezone.utc),
    )

    assert success is True
    assert len(send_calls) == 1
    assert send_calls[0]["config"].recipients == ["ops@example.com", "finance@example.com"]
    assert "CUSTOMERS" in send_calls[0]["subject"]


def test_send_sharepoint_success_notification_skips_when_no_selected_group_emails(monkeypatch):
    request = _base_request()
    send_calls = []

    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "send_email_with_retry",
        lambda **kwargs: send_calls.append(kwargs) or True,
    )

    success = snowflake_to_sharepoint_service._send_sharepoint_success_notification(
        request=request,
        table_name="CUSTOMERS",
        table_settings={"notification_emails": []},
        remote_path="/exports/CUSTOMERS.csv",
        sharepoint_url=None,
        total_rows=1,
        col_count=1,
        completed_at=snowflake_to_sharepoint_service.datetime(2026, 5, 25, 12, 0, 0, tzinfo=snowflake_to_sharepoint_service.timezone.utc),
    )

    assert success is False
    assert send_calls == []


def test_build_sharepoint_success_email_falls_back_on_empty_custom_values():
    subject, html_content, plain_text = build_sharepoint_success_email(
        table_name="CUSTOMERS",
        connection_name="Snowflake Prod",
        context={
            "company_name": "Acme",
            "service_name": "Snowflake to SharePoint",
            "rows_exported": 42,
            "columns_exported": 6,
            "sharepoint_path": "/exports/CUSTOMERS_20260525T120000Z.csv",
            "sharepoint_url": "https://contoso.sharepoint.com/sites/demo/Shared%20Documents/CUSTOMERS.csv",
            "completed_at": "2026-05-25 12:00:00 UTC",
        },
        email_custom_fields={
            "subject": "",
            "body_fields": ["service", "table"],
            "greeting_name": "",
            "body_content": "",
            "team_name": "",
            "styles": {
                "bold": False,
                "italic": False,
                "color": "#000000",
                "font_family": "system-ui",
            },
        },
    )

    assert subject == "The SharePoint export for CUSTOMERS finished successfully."
    assert "Thanks &amp; Regards," in html_content
    assert "Rows Exported" not in html_content
    assert "Service: Snowflake to SharePoint" in plain_text
    assert "Table: CUSTOMERS" in plain_text


def test_build_sharepoint_success_email_renders_custom_styles_and_text():
    subject, html_content, plain_text = build_sharepoint_success_email(
        table_name="CUSTOMERS",
        connection_name="Snowflake Prod",
        context={
            "company_name": "Acme",
            "service_name": "Snowflake to SharePoint",
            "rows_exported": 42,
            "columns_exported": 6,
            "sharepoint_path": "/exports/CUSTOMERS_20260525T120000Z.csv",
            "sharepoint_url": "https://contoso.sharepoint.com/sites/demo/Shared%20Documents/CUSTOMERS.csv",
            "completed_at": "2026-05-25 12:00:00 UTC",
        },
        email_custom_fields={
            "subject": "The {destination} export for {table} finished successfully.",
            "body_fields": ["service", "connection", "company", "table", "rows", "columns", "path", "timestamp"],
            "greeting_name": "Maya",
            "body_content": "Great news for {table}.",
            "team_name": "Analytics",
            "subject_styles": {
                "color": "#0000ff",
            },
            "greeting_styles": {
                "color": "#111111",
            },
            "body_styles": {
                "bold": True,
                "italic": True,
                "color": "#ff0000",
                "font_family": "Georgia",
                "font_size": "18px",
            },
            "team_styles": {
                "font_family": "Georgia",
                "color": "#333333",
            },
        },
    )

    assert subject == "The SharePoint export for CUSTOMERS finished successfully."
    assert "Maya" in html_content
    assert "Hi Maya," not in html_content
    assert "Great news for CUSTOMERS." in html_content
    assert "border-left:4px solid #b39df7" not in html_content
    assert "font-family:Georgia" in html_content
    assert "font-size:18px" in html_content
    assert 'href="https://contoso.sharepoint.com/sites/demo/Shared%20Documents/CUSTOMERS.csv"' in html_content
    assert "color:#ff0000" in html_content
    assert "font-weight:bold" in html_content
    assert "font-style:italic" in html_content
    assert "color:#111111" in html_content
    assert "color:#333333" in html_content
    assert "Analytics" in plain_text


def test_build_file_destination_success_email_supports_generic_destination_links():
    subject, html_content, plain_text = build_file_destination_success_email(
        table_name="CUSTOMERS",
        connection_name="Google Drive Prod",
        context={
            "company_name": "Acme",
            "service_name": "DataSyncher",
            "destination": "Google Drive",
            "file_path": "folders/exports/CUSTOMERS.csv",
            "file_url": "https://drive.google.com/file/d/abc123/view",
            "completed_at": "2026-05-25 12:00:00 UTC",
        },
        email_custom_fields={
            "subject": "The {destination} export for {table} finished successfully.",
            "body_fields": ["service", "destination", "path", "company", "connection"],
            "greeting_name": "Team",
            "body_content": "{table} Report is Ready. Check here {path}. Sync is Completed on {timestamp}.",
            "team_name": "",
        },
    )

    assert subject == "The Google Drive export for CUSTOMERS finished successfully."
    assert "File Path" in html_content
    assert "View File" not in html_content
    assert "folders/exports/CUSTOMERS.csv" in html_content
    assert 'href="https://drive.google.com/file/d/abc123/view"' in html_content
    assert "Company" in html_content
    assert "Acme" in html_content
    assert "Georgia, 'Times New Roman', serif" not in html_content
    assert html_content.index("Service") < html_content.index("Destination") < html_content.index("File Path") < html_content.index("Company") < html_content.index("Connection")
    assert "Check here" in html_content
    assert "text-decoration:underline;" in html_content
    assert "background:#6d28d9" not in html_content
    assert "font-size:18px" in html_content or "font-size:12px" in html_content
    assert "Google Drive" in plain_text
    assert "folders/exports/CUSTOMERS.csv" in plain_text


def test_migrate_snowflake_to_sharepoint_marks_unprocessed_tables_failed_on_early_error(monkeypatch):
    request = _base_request()
    failure_calls = []
    marked_tables = []

    monkeypatch.setattr(snowflake_to_sharepoint_service, "update_migration_status_log", lambda **kwargs: True)
    monkeypatch.setattr(snowflake_to_sharepoint_service, "validate_sharepoint_authorization", lambda *args, **kwargs: None)
    monkeypatch.setattr(snowflake_to_sharepoint_service, "_acquire_graph_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(snowflake_to_sharepoint_service, "_resolve_sharepoint_site", lambda *args, **kwargs: {"id": "site-1"})
    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "_resolve_document_library",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=422,
                detail=(
                    "Configured SharePoint document library was not found: QaDatasyncher. "
                    "Please restore the library or update the connection to use an existing document library."
                ),
            )
        ),
    )
    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "_mark_table_failed_with_error",
        lambda **kwargs: marked_tables.append((kwargs["table_name"], kwargs["error_message"])),
    )
    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "log_migration_failure",
        lambda **kwargs: failure_calls.append(kwargs) or True,
    )

    with pytest.raises(HTTPException) as exc_info:
        snowflake_to_sharepoint_service.migrate_snowflake_to_sharepoint(request)

    assert "Configured SharePoint document library was not found: QaDatasyncher" in exc_info.value.detail
    assert marked_tables == [
        (
            "T1",
            "Configured SharePoint document library was not found: QaDatasyncher. Please restore the library or update the connection to use an existing document library.",
        ),
        (
            "T2",
            "Configured SharePoint document library was not found: QaDatasyncher. Please restore the library or update the connection to use an existing document library.",
        ),
    ]
    assert len(failure_calls) == 1
    assert failure_calls[0]["job_name"] == "JOB_1"
    assert failure_calls[0]["connection_id"] == 33
    assert failure_calls[0]["total_tables"] == 2


def test_migrate_snowflake_to_sharepoint_marks_unprocessed_tables_failed_when_root_folder_missing(monkeypatch):
    request = _base_request()
    failure_calls = []
    marked_tables = []

    monkeypatch.setattr(snowflake_to_sharepoint_service, "update_migration_status_log", lambda **kwargs: True)
    monkeypatch.setattr(snowflake_to_sharepoint_service, "validate_sharepoint_authorization", lambda *args, **kwargs: None)
    monkeypatch.setattr(snowflake_to_sharepoint_service, "_acquire_graph_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(snowflake_to_sharepoint_service, "_resolve_sharepoint_site", lambda *args, **kwargs: {"id": "site-1"})
    monkeypatch.setattr(snowflake_to_sharepoint_service, "_resolve_document_library", lambda *args, **kwargs: {"id": "drive-1"})
    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "_resolve_root_folder_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=422,
                detail=(
                    "Configured root folder does not exist in SharePoint document library: /exports. "
                    "Please create the folder or update the connection to use an existing folder."
                ),
            )
        ),
    )
    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "_mark_table_failed_with_error",
        lambda **kwargs: marked_tables.append((kwargs["table_name"], kwargs["error_message"])),
    )
    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "log_migration_failure",
        lambda **kwargs: failure_calls.append(kwargs) or True,
    )

    with pytest.raises(HTTPException) as exc_info:
        snowflake_to_sharepoint_service.migrate_snowflake_to_sharepoint(request)

    assert exc_info.value.detail == (
        "Configured root folder does not exist in SharePoint document library: /exports. "
        "Please create the folder or update the connection to use an existing folder."
    )
    assert marked_tables == [
        (
            "T1",
            "Configured root folder does not exist in SharePoint document library: /exports. Please create the folder or update the connection to use an existing folder.",
        ),
        (
            "T2",
            "Configured root folder does not exist in SharePoint document library: /exports. Please create the folder or update the connection to use an existing folder.",
        ),
    ]
    assert len(failure_calls) == 1


def test_check_table_completed_status_skips_and_restores_sharepoint(monkeypatch):
    """Verifies that check_table_completed_status skips processing and restores stats in SharePoint."""
    request = _base_request()

    from utils import database_utils
    monkeypatch.setattr(
        database_utils,
        "check_table_completed_status",
        lambda connection_id, table_name, migration_session_id, job_name: {
            "status": "success",
            "rows": 14471,
            "columns": 198,
        }
    )

    result = snowflake_to_sharepoint_service._process_snowflake_to_sharepoint_table(
        request=request,
        table_name="T1",
        source_config={},
        dest_config={},
        token_manager=None,
        drive_id="drive-123",
        root_folder_path="/exports",
        file_format="excel",
        default_delimiter=",",
        default_quotechar='"',
        escapechar=None,
        null_sequence="\\N",
        fetch_size=1000,
        table_export_settings={},
        job_name="JOB_1",
    )

    assert result["status"] == "success"
    assert result["rows"] == 14471
    assert result["columns"] == 198
    assert result["remote_path"] == "(skipped: already uploaded)"
    assert result["web_url"] == "(skipped: already uploaded)"
    assert result["error"] is None
