import io
import json
import logging
import os
import re
import tempfile
import time
import traceback as _traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
import snowflake.connector
from fastapi import HTTPException

from models.google_drive_models import GoogleDriveMigrationRequest
from services.source_file_helpers.file_writing_utils import (
    _decode_csv_char,
    _write_csv,
    _write_excel,
    _write_json,
    _write_parquet,
    build_utc_filename_suffix,
    check_excel_limits_preflight,
    lookup_table_export_settings,
    resolve_relative_target_folder,
)
from services.google_drive.client import (
    GOOGLE_DRIVE_API_BASE,
    GOOGLE_DRIVE_UPLOAD_BASE,
    _drive_request,
    _extract_google_error,
    _raise_google_drive_folder_error,
    validate_google_drive_connection,
)
from services.snowflake.as_source.to_sftp import (
    MigrationFailureTracker,
    _build_snowflake_select_query,
    _extract_error_message,
    _log_stage_error,
    _map_snowflake_export_error,
    _mark_table_failed_with_error,
    _normalize_selected_fields,
)
from services.snowflake.as_source.to_snowflake import _build_conn_params
from services.transform.source_script_transform import (
    TransformingCursor,
    get_active_source_script,
)
from utils.celery_cleanup_utils import handle_migration_completion
from utils.database_utils import data_transfer_logs, update_table_status_snowflake
from utils.error_utils import log_error_to_tool_log, log_migration_completion, log_migration_failure, update_migration_status_log
from utils.simple_progress_tracker import SimpleProgressTracker
from utils.email_templates import create_google_drive_success_email
from utils.email_utils import EmailConfiguration, send_email_with_retry
from utils.websocket_utils import (
    trigger_company_connections_status_update,
    trigger_connection_activity_log_update,
    trigger_connection_ws_status_update,
    trigger_ws_status_update,
)

_gd_logger = logging.getLogger(__name__)

def _send_google_drive_success_notification(
    *,
    request: GoogleDriveMigrationRequest,
    table_name: str,
    table_settings: Dict[str, Any],
    remote_path: str,
    total_rows: int,
    col_count: int,
    completed_at: datetime,
) -> bool:
    recipients = []
    seen_recipients = set()
    for email in table_settings.get("notification_emails", []) or []:
        normalized_email = str(email or "").strip()
        lowered_email = normalized_email.lower()
        if not normalized_email or lowered_email in seen_recipients:
            continue
        seen_recipients.add(lowered_email)
        recipients.append(normalized_email)

    if not recipients:
        return False

    config = EmailConfiguration()
    config.recipients = recipients
    if not config.is_configured():
        _gd_logger.warning(
            "Skipping Google Drive success notification for table %s; email config incomplete (missing=%s)",
            table_name,
            ", ".join(config.get_missing_config()),
        )
        return False

    connection_name = request.connection_name or f"Connection {request.connection_id}"
    company_name = str(request.company_name or f"Company {request.cmp_id}").strip()
    if not company_name:
        company_name = f"Company {request.cmp_id}"
    group_names = [str(name or "").strip() for name in (table_settings.get("notification_email_group_names") or []) if str(name or "").strip()]
    completed_at_text = completed_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = f"DataSyncher: {connection_name} - {table_name} Google Drive export completed"
    html_content, plain_text = create_google_drive_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context={
            "company_name": company_name,
            "service_name": "Snowflake to Google Drive",
            "rows_exported": total_rows,
            "columns_exported": col_count,
            "google_drive_path": remote_path,
            "completed_at": completed_at_text,
            "email_groups": group_names,
        },
    )

    try:
        success = send_email_with_retry(
            config=config,
            subject=subject,
            html_content=html_content,
            plain_text=plain_text,
        )
    except Exception as exc:
        _gd_logger.warning("Google Drive success notification failed for table %s: %s", table_name, exc)
        return False

    if success:
        _gd_logger.info(
            "Google Drive success notification sent | table=%s recipients=%s",
            table_name,
            len(recipients),
        )
    else:
        _gd_logger.warning("Google Drive success notification not sent | table=%s", table_name)
    return success


def _quote_identifier(value: str) -> str:
    escaped = str(value or "").replace('"', '""')
    return f'"{escaped}"'


def _normalize_file_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned or "export"


def _normalize_selected_mappings(selected_tables: List[Any]) -> List[Tuple[str, str]]:
    mappings: List[Tuple[str, str]] = []
    for item in selected_tables or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            source_table = str(item[0] or "").strip()
            target_name = str(item[1] or "").strip()
        else:
            source_table = str(item or "").strip()
            target_name = source_table

        if not source_table:
            continue

        mappings.append((source_table, target_name or source_table))
    return mappings


def _trigger_google_drive_status_refresh(request: GoogleDriveMigrationRequest) -> None:
    try:
        trigger_ws_status_update(request.migration_session_id)
    except Exception as ws_err:
        _gd_logger.warning(
            "[GD_MIGRATION] WebSocket session trigger failed | session=%s | error=%s",
            request.migration_session_id,
            ws_err,
        )
    try:
        trigger_connection_ws_status_update(request.connection_id)
    except Exception as ws_err:
        _gd_logger.warning(
            "[GD_MIGRATION] WebSocket connection trigger failed | connection_id=%s | error=%s",
            request.connection_id,
            ws_err,
        )
    try:
        trigger_connection_activity_log_update(request.connection_id)
    except Exception as ws_err:
        _gd_logger.warning(
            "[GD_MIGRATION] WebSocket activity-log trigger failed | connection_id=%s | error=%s",
            request.connection_id,
            ws_err,
        )
    try:
        trigger_company_connections_status_update(request.cmp_id)
    except Exception as ws_err:
        _gd_logger.warning(
            "[GD_MIGRATION] WebSocket company trigger failed | cmp_id=%s | error=%s",
            request.cmp_id,
            ws_err,
        )


def _mark_unprocessed_tables_failed(
    request: GoogleDriveMigrationRequest,
    table_mappings: List[Tuple[str, str]],
    error_message: str,
    processed_tables: List[str],
) -> None:
    processed = set(processed_tables or [])
    for source_table, _target_name in table_mappings:
        if source_table in processed:
            continue
        try:
            _mark_table_failed_with_error(
                request=request,
                table_name=source_table,
                job_name=request.job_name,
                error_message=error_message,
            )
        except Exception as table_err:
            _gd_logger.warning(
                "[GD_MIGRATION] Failed to mark unprocessed table as failed | table=%s | error=%s",
                source_table,
                table_err,
            )


def _get_file_mime_type(file_format: str) -> str:
    normalized = str(file_format or "").strip().lower()
    if normalized == "json":
        return "application/json"
    if normalized == "parquet":
        return "application/octet-stream"
    if normalized == "excel":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "text/csv"


def _google_drive_query(
    access_token: str,
    folder_id: str,
    file_name: str,
    folder_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    safe_file_name = str(file_name or "").replace("'", "\\'")
    query = f"name='{safe_file_name}' and '{folder_id}' in parents and trashed=false"
    resp = _drive_request(
        "GET",
        f"{GOOGLE_DRIVE_API_BASE}/files",
        access_token,
        params={
            "q": query,
            "fields": "files(id,name)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
    )
    if resp.status_code != 200:
        _raise_google_drive_folder_error(
            resp,
            folder_id=folder_id,
            folder_name=folder_name,
            action="query Google Drive files",
        )
    return resp.json().get("files", [])


def _get_or_create_folder(
    access_token: str,
    parent_folder_id: str,
    folder_name: str,
    parent_folder_name: Optional[str] = None,
) -> str:
    if not folder_name or str(folder_name).strip() == "":
        return parent_folder_id

    path = str(folder_name).replace("\\", "/").strip().strip("/")
    if not path:
        return parent_folder_id

    current_parent_id = parent_folder_id
    for segment in [segment.strip() for segment in path.split("/") if segment.strip()]:
        safely_cleaned_name = re.sub(r"[^A-Za-z0-9._\- ]", "_", segment)
        query = (
            f"name='{safely_cleaned_name}' and '{current_parent_id}' in parents "
            "and mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        resp = _drive_request(
            "GET",
            f"{GOOGLE_DRIVE_API_BASE}/files",
            access_token,
            params={
                "q": query,
                "fields": "files(id,name)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        if resp.status_code != 200:
            _raise_google_drive_folder_error(
                resp,
                folder_id=current_parent_id,
                folder_name=parent_folder_name,
                action="query Google Drive folders",
            )

        existing_folders = resp.json().get("files", [])
        if existing_folders:
            current_parent_id = existing_folders[0].get("id")
            continue

        metadata = {
            "name": safely_cleaned_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [current_parent_id],
        }
        create_resp = _drive_request(
            "POST",
            f"{GOOGLE_DRIVE_API_BASE}/files",
            access_token,
            json=metadata,
            params={
                "fields": "id,name",
                "supportsAllDrives": "true",
            },
        )
        if create_resp.status_code not in (200, 201):
            _raise_google_drive_folder_error(
                create_resp,
                folder_id=current_parent_id,
                folder_name=parent_folder_name,
                action=f"create Google Drive folder '{segment}'",
            )

        new_folder = create_resp.json()
        current_parent_id = new_folder.get("id")
        parent_folder_name = safely_cleaned_name
        _gd_logger.info(
            "[GD_MIGRATION] Created Google Drive folder: %s (ID: %s) under parent %s",
            safely_cleaned_name,
            current_parent_id,
            parent_folder_id,
        )

    return current_parent_id


def _delete_existing_files(
    access_token: str,
    folder_id: str,
    file_name: str,
    folder_name: Optional[str] = None,
) -> None:
    for file_info in _google_drive_query(access_token, folder_id, file_name, folder_name=folder_name):
        file_id = file_info.get("id")
        if not file_id:
            continue
        resp = _drive_request(
            "DELETE",
            f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
            access_token,
            params={"supportsAllDrives": "true"},
        )
        if resp.status_code not in (200, 204):
            raise HTTPException(
                status_code=422,
                detail=f"Failed to replace existing Google Drive file '{file_name}': {_extract_google_error(resp)}",
            )


def _upload_file_to_drive(
    access_token: str,
    folder_id: str,
    file_name: str,
    file_bytes: bytes,
    mime_type: str,
    folder_name: Optional[str] = None,
) -> Dict[str, Any]:
    _delete_existing_files(access_token, folder_id, file_name, folder_name=folder_name)

    metadata = {
        "name": file_name,
        "parents": [folder_id],
    }
    files = {
        "metadata": ("metadata", io.BytesIO(json.dumps(metadata).encode("utf-8")), "application/json"),
        "file": (file_name, io.BytesIO(file_bytes), mime_type),
    }
    resp = requests.post(
        f"{GOOGLE_DRIVE_UPLOAD_BASE}/files",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "uploadType": "multipart",
            "fields": "id,name,webViewLink",
            "supportsAllDrives": "true",
        },
        files=files,
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        _raise_google_drive_folder_error(
            resp,
            folder_id=folder_id,
            folder_name=folder_name,
            action="upload file to Google Drive",
        )
    return resp.json()


def _upload_file_path_to_drive(
    access_token: str,
    folder_id: str,
    file_name: str,
    local_path: str,
    mime_type: str,
    folder_name: Optional[str] = None,
) -> Dict[str, Any]:
    _delete_existing_files(access_token, folder_id, file_name, folder_name=folder_name)

    file_size = os.path.getsize(local_path)
    multipart_upload_limit = 5 * 1024 * 1024
    resumable_chunk_size = 8 * 1024 * 1024
    upload_timeout_seconds = 300
    metadata = {
        "name": file_name,
        "parents": [folder_id],
    }

    if file_size <= multipart_upload_limit:
        with open(local_path, "rb") as file_handle:
            files = {
                "metadata": ("metadata", io.BytesIO(json.dumps(metadata).encode("utf-8")), "application/json"),
                "file": (file_name, file_handle, mime_type),
            }
            resp = requests.post(
                f"{GOOGLE_DRIVE_UPLOAD_BASE}/files",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "uploadType": "multipart",
                    "fields": "id,name,webViewLink",
                    "supportsAllDrives": "true",
                },
                files=files,
                timeout=upload_timeout_seconds,
            )
        if resp.status_code not in (200, 201):
            _raise_google_drive_folder_error(
                resp,
                folder_id=folder_id,
                folder_name=folder_name,
                action="upload file to Google Drive",
            )
        return resp.json()

    session_resp = requests.post(
        f"{GOOGLE_DRIVE_UPLOAD_BASE}/files",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(file_size),
        },
        params={
            "uploadType": "resumable",
            "fields": "id,name,webViewLink",
            "supportsAllDrives": "true",
        },
        json=metadata,
        timeout=upload_timeout_seconds,
    )
    if session_resp.status_code not in (200, 201):
        _raise_google_drive_folder_error(
            session_resp,
            folder_id=folder_id,
            folder_name=folder_name,
            action="start Google Drive resumable upload",
        )

    upload_url = session_resp.headers.get("Location")
    if not upload_url:
        raise HTTPException(status_code=422, detail="Google Drive did not return a resumable upload URL.")

    uploaded = 0
    final_resp = None
    with open(local_path, "rb") as file_handle:
        while uploaded < file_size:
            chunk = file_handle.read(resumable_chunk_size)
            if not chunk:
                break

            start = uploaded
            end = uploaded + len(chunk) - 1
            headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Type": mime_type,
            }
            chunk_resp = requests.put(
                upload_url,
                headers=headers,
                data=chunk,
                timeout=upload_timeout_seconds,
            )
            if chunk_resp.status_code == 308:
                uploaded = end + 1
                continue
            if chunk_resp.status_code in (200, 201):
                final_resp = chunk_resp
                uploaded = file_size
                break

            _raise_google_drive_folder_error(
                chunk_resp,
                folder_id=folder_id,
                folder_name=folder_name,
                action="upload file chunk to Google Drive",
            )

    if final_resp is None:
        raise HTTPException(status_code=422, detail="Google Drive resumable upload did not complete.")

    return final_resp.json()


def sync_snowflake_to_google_drive(request: GoogleDriveMigrationRequest) -> Dict[str, Any]:
    start_time = time.time()
    snowflake_conn = None
    successful_tables: List[str] = []
    failed_tables: List[str] = []
    failure_reasons: Dict[str, str] = {}
    failure_tracker = MigrationFailureTracker()
    total_rows_transferred = 0
    folder_id = None
    folder_name = None
    table_mappings: List[Tuple[str, str]] = _normalize_selected_mappings(request.selected_tables)
    processed_tables: List[str] = []
    started_at = datetime.now(timezone.utc)
    results = {
        "status": "success",
        "status_code": 200,
        "job_name": request.job_name,
        "connection_id": request.connection_id,
        "migration_session_id": request.migration_session_id,
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "tables": {},
        "total_tables": len(table_mappings),
        "successful_tables": 0,
        "failed_tables": 0,
        "total_rows": 0,
        "folder_id": None,
        "folder_name": None,
        "service_name": "Snowflake-GoogleDrive",
    }

    try:
        if not table_mappings:
            raise HTTPException(status_code=400, detail="No selected tables were provided for Google Drive migration.")

        update_migration_status_log(
            job_name=request.job_name,
            message="Migration in progress",
            status="I",
            session=request.migration_session_id,
        )

        try:
            drive_validation = validate_google_drive_connection({"GoogleDrive": request.destination_config})
        except HTTPException as gd_auth_err:
            _gd_logger.error(
                "[GD_MIGRATION] Google Drive auth failed | connection_id=%s | detail=%s",
                request.connection_id,
                gd_auth_err.detail,
            )
            log_error_to_tool_log(
                cmp_id=request.cmp_id,
                connection_id=request.connection_id,
                user_id=request.user_id,
                table_name="Google Drive Auth",
                error_message=f"Google Drive authentication failed: {gd_auth_err.detail}",
                session=request.session,
                job_name=request.job_name,
            )
            raise

        access_token = drive_validation.get("access_token")
        folder_id = drive_validation.get("folder_id")
        folder_name = drive_validation.get("folder_name")
        results["folder_id"] = folder_id
        results["folder_name"] = folder_name
        if not access_token or not folder_id:
            raise HTTPException(status_code=422, detail="Google Drive folder validation did not return a folder_id.")

        source_schema = str(request.source_config.get("schema") or "").strip()
        if not source_schema:
            raise HTTPException(
                status_code=400,
                detail='Snowflake source configuration missing required "schema" field.',
            )

        try:
            conn_params = _build_conn_params(request.source_config)
            snowflake_conn = snowflake.connector.connect(**conn_params)
        except Exception as sf_conn_err:
            _gd_logger.error(
                "[GD_MIGRATION] Snowflake connection failed | connection_id=%s | error=%s",
                request.connection_id,
                sf_conn_err,
            )
            log_error_to_tool_log(
                cmp_id=request.cmp_id,
                connection_id=request.connection_id,
                user_id=request.user_id,
                table_name="Snowflake Connection",
                error_message=f"Snowflake connection failed: {sf_conn_err}",
                session=request.session,
                job_name=request.job_name,
            )
            raise HTTPException(status_code=500, detail=f"Snowflake connection failed: {sf_conn_err}")

        if source_schema:
            with snowflake_conn.cursor() as schema_cur:
                schema_cur.execute(f"USE SCHEMA {_quote_identifier(source_schema)}")

        file_format = str((request.destination_config or {}).get("file_format") or "csv").strip().lower()
        if file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        default_delimiter = _decode_csv_char((request.destination_config or {}).get("csv_delimiter") or ",")
        default_quotechar = _decode_csv_char(
            (request.destination_config or {}).get("csv_quote_char")
            or (request.destination_config or {}).get("csv_quotechar")
            or '"'
        )
        escapechar = None
        null_sequence = (request.destination_config or {}).get("null_sequence")
        null_sequence = "" if null_sequence is None else str(null_sequence)

        fetch_size = int(request.chunk_count) if request.chunk_count else 5000
        if fetch_size <= 0:
            fetch_size = 5000

        table_export_settings = request.table_export_settings or {}
        execution_order = str(request.execution_order or "sequential").strip().lower()
        _gd_logger.info(
            "[GD_MIGRATION] Execution mode=%s | selected_tables=%s | connection_id=%s",
            execution_order,
            len(table_mappings),
            request.connection_id,
        )

        for source_table, target_name in table_mappings:
            table_start = datetime.now(timezone.utc)
            total_rows = 0
            col_count = 0
            bytes_uploaded = 0
            uploaded_file_link = None
            file_name = f"{_normalize_file_stem(target_name)}.csv"
            try:
                update_table_status_snowflake(
                    request.connection_id,
                    source_table,
                    "in_progress",
                    request.job_name,
                    request.migration_session_id,
                )

                try:
                    SimpleProgressTracker.reset_staging_count(
                        connection_id=request.connection_id,
                        migration_session_id=str(request.migration_session_id),
                        job_name=request.job_name,
                        table_name=source_table,
                    )
                except Exception as progress_reset_err:
                    _gd_logger.warning(
                        "[GD_MIGRATION] Failed to reset live staging count | table=%s | error=%s",
                        source_table,
                        progress_reset_err,
                    )

                table_settings = lookup_table_export_settings(table_export_settings, source_table)
                if not isinstance(table_settings, dict):
                    table_settings = {}

                selected_fields = _normalize_selected_fields(table_settings.get("selected_fields"))
                sort_columns = table_settings.get("sort_columns") or (table_settings.get("excel_options") or {}).get("sort_columns")
                query = _build_snowflake_select_query(source_table, selected_fields, sort_columns)
                table_file_format = str(table_settings.get("file_format") or file_format or "csv").strip().lower()
                if table_file_format not in ("csv", "json", "parquet", "excel"):
                    raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")
                if table_file_format == "excel":
                    check_excel_limits_preflight(snowflake_conn, query, source_table)

                with snowflake_conn.cursor() as cur:
                    try:
                        cur.execute(query)
                    except Exception as snowflake_read_err:
                        raise _map_snowflake_export_error(source_table, snowflake_read_err) from snowflake_read_err

                    # Apply the active generic source script before the shared
                    # writers. Google Drive receives the same transformed file
                    # data as the other Snowflake file destinations.
                    active_transform = get_active_source_script(
                        request.connection_id, source_table
                    )
                    script_text = (active_transform or {}).get("script_text")
                    if script_text and str(script_text).strip():
                        cur = TransformingCursor(
                            cur,
                            fetch_size=fetch_size,
                            script_text=script_text,
                            entry_point=(active_transform or {}).get("entry_point") or "",
                        )

                    requested_target_folder = str(
                        table_settings.get("target_folder")
                        or (request.folderpath_dict or {}).get(source_table)
                        or ""
                    ).strip()
                    target_folder_path = resolve_relative_target_folder(
                        folder_name,
                        requested_target_folder,
                    )
                    upload_folder_id = folder_id
                    upload_folder_name = folder_name
                    if target_folder_path:
                        upload_folder_id = _get_or_create_folder(
                            access_token,
                            folder_id,
                            target_folder_path,
                            parent_folder_name=folder_name,
                        )
                        upload_folder_name = target_folder_path
                        _gd_logger.info(
                            "[GD_MIGRATION] Using target folder | table=%s | target_path=%s | folder_id=%s",
                            source_table,
                            target_folder_path,
                            upload_folder_id,
                        )

                    base_name = str(table_settings.get("output_file_name") or "").strip() or target_name or source_table
                    add_ts = table_settings.get("add_utc_timestamp")
                    add_ts = True if add_ts is None else bool(add_ts)
                    ts = build_utc_filename_suffix(table_settings.get("filename_date_format"))
                    ext = {
                        "csv": "csv",
                        "json": "json",
                        "parquet": "parquet",
                        "excel": "xlsx",
                    }[table_file_format]
                    normalized_base = _normalize_file_stem(base_name)
                    file_name = f"{normalized_base}_{ts}.{ext}" if add_ts else f"{normalized_base}.{ext}"

                    with tempfile.TemporaryDirectory(prefix="sf_google_drive_") as tmpdir:
                        local_path = os.path.join(tmpdir, file_name)
                        progress_label = f"Google Drive table={source_table} file={file_name}"

                        def _progress_callback(_rows_in_chunk: int, total_rows_written: int) -> None:
                            try:
                                SimpleProgressTracker.set_staging_count(
                                    connection_id=request.connection_id,
                                    migration_session_id=str(request.migration_session_id),
                                    job_name=request.job_name,
                                    table_name=source_table,
                                    count_value=total_rows_written,
                                )
                            except Exception as progress_err:
                                _gd_logger.warning(
                                    "[GD_MIGRATION] Failed to update staging count | table=%s | total_rows=%s | error=%s",
                                    source_table,
                                    total_rows_written,
                                    progress_err,
                                )

                        if table_file_format == "csv":
                            delimiter = _decode_csv_char(table_settings.get("csv_delimiter") or default_delimiter)
                            quotechar = _decode_csv_char(table_settings.get("csv_quote_char") or default_quotechar)
                            if not delimiter:
                                delimiter = ","
                            if not quotechar:
                                quotechar = '"'
                            total_rows, col_count = _write_csv(
                                cur,
                                local_path,
                                delimiter=delimiter,
                                quotechar=quotechar,
                                escapechar=escapechar,
                                null_sequence=null_sequence,
                                fetch_size=fetch_size,
                                progress_label=progress_label,
                                progress_callback=_progress_callback,
                            )
                        elif table_file_format == "json":
                            ns = null_sequence if null_sequence != "" else None
                            total_rows, col_count = _write_json(
                                cur,
                                local_path,
                                null_sequence=ns,
                                fetch_size=fetch_size,
                                progress_label=progress_label,
                                progress_callback=_progress_callback,
                            )
                        elif table_file_format == "parquet":
                            total_rows, col_count = _write_parquet(
                                cur,
                                local_path,
                                fetch_size=fetch_size,
                                progress_label=progress_label,
                                progress_callback=_progress_callback,
                            )
                        else:
                            total_rows, col_count = _write_excel(
                                cur,
                                local_path,
                                sheet_name=table_settings.get("excel_sheet_name"),
                                excel_options=table_settings.get("excel_options"),
                                conditional_formats=table_settings.get("excel_conditional_formats"),
                                fetch_size=fetch_size,
                                progress_label=progress_label,
                                progress_callback=_progress_callback,
                            )

                        mime_type = _get_file_mime_type(table_file_format)
                        upload_result = _upload_file_path_to_drive(
                            access_token,
                            upload_folder_id,
                            file_name,
                            local_path,
                            mime_type,
                            folder_name=upload_folder_name,
                        )
                        bytes_uploaded = os.path.getsize(local_path)
                        uploaded_file_link = upload_result.get("webViewLink") or upload_result.get("id")
                        total_rows_transferred += total_rows

                table_end = datetime.now(timezone.utc)
                data_transfer_logs(
                    request.cmp_id,
                    request.connection_id,
                    request.user_id,
                    source_table,
                    table_start,
                    table_end,
                    col_count,
                    request.session,
                    total_rows,
                    0,
                    0,
                    0,
                    job_name=request.job_name,
                )
                try:
                    SimpleProgressTracker.set_staging_count(
                        connection_id=request.connection_id,
                        migration_session_id=str(request.migration_session_id),
                        job_name=request.job_name,
                        table_name=source_table,
                        count_value=total_rows,
                    )
                except Exception as progress_err:
                    _gd_logger.warning(
                        "[GD_MIGRATION] Failed to update staging_records_count | table=%s | error=%s",
                        source_table,
                        progress_err,
                    )
                update_table_status_snowflake(
                    request.connection_id,
                    source_table,
                    "completed",
                    request.job_name,
                    request.migration_session_id,
                    sync_end_time=table_end,
                )
                successful_tables.append(source_table)
                processed_tables.append(source_table)
                _gd_logger.info(
                    "[GD_MIGRATION] Table completed | connection_id=%s | table=%s | rows=%s | file=%s",
                    request.connection_id,
                    source_table,
                    total_rows,
                    file_name,
                )
                results["tables"][source_table] = {
                    "status": "success",
                    "rows": total_rows,
                    "columns": col_count,
                    "remote_path": uploaded_file_link or file_name,
                    "bytes_uploaded": bytes_uploaded,
                    "error": None,
                }
                
                # Send email notification if configured
                _send_google_drive_success_notification(
                    request=request,
                    table_name=source_table,
                    table_settings=table_settings,
                    remote_path=uploaded_file_link or file_name,
                    total_rows=total_rows,
                    col_count=col_count,
                    completed_at=table_end,
                )

                _trigger_google_drive_status_refresh(request)

            except Exception as table_error:
                failed_tables.append(source_table)
                processed_tables.append(source_table)
                error_detail = _extract_error_message(table_error)
                failure_reasons[source_table] = str(error_detail)
                failure_tracker.add_failure(source_table, str(error_detail), "Snowflake to Google Drive")
                _gd_logger.error(
                    "[GD_MIGRATION] Table failed | connection_id=%s | table=%s | error=%s\n%s",
                    request.connection_id,
                    source_table,
                    table_error,
                    _traceback.format_exc(),
                )
                _mark_table_failed_with_error(
                    request=request,
                    table_name=source_table,
                    job_name=request.job_name,
                    error_message=str(error_detail),
                )
                _log_stage_error(
                    cmp_id=request.cmp_id,
                    connection_id=request.connection_id,
                    function_name="sync_snowflake_to_google_drive.table",
                    error=table_error,
                    extra_message=f"table={source_table}",
                )
                results["tables"][source_table] = {
                    "status": "failed",
                    "rows": 0,
                    "columns": 0,
                    "remote_path": None,
                    "bytes_uploaded": 0,
                    "error": str(error_detail),
                }
                _trigger_google_drive_status_refresh(request)

        execution_time = time.time() - start_time

        # Require failed table status to be saved before returning the API result.
        for failed_table in failed_tables:
            update_table_status_snowflake(
                request.connection_id, failed_table, "failed",
                request.job_name, request.migration_session_id,
            )

        results["successful_tables"] = len(successful_tables)
        results["failed_tables"] = len(failed_tables)
        results["total_rows"] = total_rows_transferred
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        if results["failed_tables"] == 0:
            results["status"] = "success"
        elif results["successful_tables"] == 0:
            results["status"] = "failed"
        else:
            results["status"] = "partial"
        results["status_code"] = 200 if results["failed_tables"] == 0 else 206

        completion_message = (
            f"Snowflake to Google Drive migration completed in {execution_time:.2f} seconds. "
            f"Uploaded {len(successful_tables)} file(s) to folder '{folder_name}'."
            + (f" Failed: {len(failed_tables)} table(s)." if failed_tables else "")
        )
        results["message"] = completion_message
        _gd_logger.info("[GD_MIGRATION] %s | connection_id=%s", completion_message, request.connection_id)

        if not request.celery_child_mode:
            handle_migration_completion(
                connection_name=request.connection_name or f"GOOGLE_DRIVE_{request.connection_id}",
                job_name=request.job_name,
                completion_message=completion_message,
            )
        # Supply the child-result contract used by the Celery finalizer.
        results["execution_time"] = execution_time
        results["successful_tables_count"] = results["successful_tables"]
        results["failed_tables_count"] = results["failed_tables"]
        results["successful_tables_list"] = [
            table for table, result in results["tables"].items() if result["status"] == "success"
        ]
        results["failed_tables_list"] = [
            table for table, result in results["tables"].items() if result["status"] != "success"
        ]

        # Child tasks report results; only the parent finalizes the activity log.
        if not request.celery_child_mode:
            log_migration_completion(
                execution_time=results["execution_time"],
                total_rows=total_rows_transferred,
                cmp_id=request.cmp_id,
                connection_id=request.connection_id,
                user_id=request.user_id,
                session=request.migration_session_id,
                total_tables=len(table_mappings),
                successful_tables=len(successful_tables),
                failed_tables=len(failed_tables),
                job_name=request.job_name,
                log_id=request.log_id,
                include_operation_counts=False,
            )
        failure_tracker.send_consolidated_error_email(
            connection_name=request.connection_name or f"Google Drive {request.connection_id}",
            service_name="Snowflake to Google Drive",
            user_id=request.user_id,
            cmp_id=request.cmp_id,
            connection_id=request.connection_id,
            job_name=request.job_name,
            migration_session_id=request.migration_session_id,
            company_name=request.company_name or "Unknown Company",
            is_final_attempt=True,
        )
        _trigger_google_drive_status_refresh(request)

        return results

    except HTTPException as exc:
        execution_time = time.time() - start_time
        error_message = _extract_error_message(exc)
        _mark_unprocessed_tables_failed(
            request=request,
            table_mappings=table_mappings,
            error_message=error_message,
            processed_tables=processed_tables,
        )
        try:
            log_migration_failure(
                job_name=request.job_name,
                error_message=error_message,
                session=request.migration_session_id,
                cmp_id=request.cmp_id,
                connection_id=request.connection_id,
                user_id=request.user_id,
                execution_time=execution_time,
                total_rows=total_rows_transferred,
                successful_tables=len(successful_tables),
                total_tables=len(table_mappings),
            )
        except Exception as failure_log_err:
            _gd_logger.warning(
                "[GD_MIGRATION] Failed to write migration failure log | connection_id=%s | error=%s",
                request.connection_id,
                failure_log_err,
            )
        _trigger_google_drive_status_refresh(request)
        raise
    except Exception as exc:
        execution_time = time.time() - start_time
        error_message = _extract_error_message(exc)
        _gd_logger.error(
            "[GD_MIGRATION] Unexpected failure | connection_id=%s | error=%s\n%s",
            request.connection_id,
            exc,
            _traceback.format_exc(),
        )
        _mark_unprocessed_tables_failed(
            request=request,
            table_mappings=table_mappings,
            error_message=error_message,
            processed_tables=processed_tables,
        )
        log_error_to_tool_log(
            cmp_id=request.cmp_id,
            connection_id=request.connection_id,
            user_id=request.user_id,
            table_name="Migration Process",
            error_message=f"Unexpected migration failure: {error_message}",
            session=request.session,
            job_name=request.job_name,
        )
        try:
            log_migration_failure(
                job_name=request.job_name,
                error_message=error_message,
                session=request.migration_session_id,
                cmp_id=request.cmp_id,
                connection_id=request.connection_id,
                user_id=request.user_id,
                execution_time=execution_time,
                total_rows=total_rows_transferred,
                successful_tables=len(successful_tables),
                total_tables=len(table_mappings),
            )
        except Exception as failure_log_err:
            _gd_logger.warning(
                "[GD_MIGRATION] Failed to write migration failure log | connection_id=%s | error=%s",
                request.connection_id,
                failure_log_err,
            )
        _trigger_google_drive_status_refresh(request)
        raise HTTPException(status_code=500, detail=error_message)
    finally:
        if snowflake_conn is not None:
            try:
                snowflake_conn.close()
            except Exception as close_err:
                _gd_logger.warning(
                    "[GD_MIGRATION] Failed to close Snowflake connection | error=%s",
                    close_err,
                )
