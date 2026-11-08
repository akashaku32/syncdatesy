from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from fastapi import HTTPException

from models.sharepoint_models import SharePointAuthorizationRequest
from models.snowflake_sharepoint_models import SnowflakeSharePointMigrationRequest
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
from services.sharepoint.client import (
    GRAPH_BASE_URL,
    GRAPH_TIMEOUT_SECONDS,
    _acquire_graph_token,
    _encode_graph_path,
    _extract_graph_error,
    _normalize_root_folder,
    _resolve_document_library,
    _resolve_sharepoint_site,
    validate_sharepoint_authorization,
)
from services.snowflake.service import SnowflakeService
from services.transform.source_script_transform import (
    TransformingCursor,
    get_active_source_script,
)
from services.snowflake.as_source.to_sftp import (
    _build_snowflake_select_query,
    _extract_error_message,
    _extract_snowflake_schema,
    _log_stage_error,
    _map_snowflake_export_error,
    _mark_table_failed_with_error,
    _normalize_selected_fields,
    _trigger_ws_all,
)
from utils.database_utils import data_transfer_logs, update_table_status_snowflake
from utils.email_templates import build_file_destination_success_email
from utils.email_utils import EmailConfiguration, send_email_with_retry
from utils.error_utils import log_migration_completion, log_migration_failure, update_migration_status_log, handle_service_error
from utils.simple_progress_tracker import SimpleProgressTracker

logger = logging.getLogger(__name__)
import threading

# MIGRATION FAILURE TRACKER CLASS
# =============================================================================
class MigrationFailureTracker:
    """
    Tracks failed tables during migration process for consolidated error reporting.
    Collects table failures and sends one consolidated email at the end of migration.
    """
    def __init__(self):
        self.failed_tables = []
        self.migration_start_time = datetime.now(timezone.utc)
        self._lock = threading.Lock()

    def add_failure(self, table_name: str, error_message: str, service_name: str = ""):
        """
        Add a failed table to the tracker.
        """
        table_name = str(table_name or "").strip()
        if not table_name:
            return
        failure_time = datetime.now(timezone.utc)
        failure = {
            "table_name": table_name,
            "error_message": error_message,
            "failure_time": failure_time,
            "service_name": service_name
        }
        with self._lock:
            # Check for duplicates to avoid duplicate logging
            for item in self.failed_tables:
                if str(item.get("table_name", "")).strip().upper() == table_name.upper():
                    item["error_message"] = error_message
                    item["failure_time"] = failure_time
                    item["service_name"] = service_name
                    return
            self.failed_tables.append(failure)
        logger.info(f"📝 Added failure for table '{table_name}' to migration tracker")

    def has_failures(self):
        """Check if there are any failed tables."""
        with self._lock:
            return len(self.failed_tables) > 0

    def get_failure_summary(self):
        """Get a summary of all failed tables."""
        with self._lock:
            return {
                "total_failures": len(self.failed_tables),
                "failed_tables": list(self.failed_tables),
                "migration_start_time": self.migration_start_time
            }

    def send_consolidated_error_email(self, connection_name: str, service_name: str, user_id: int, cmp_id: int, connection_id: int, job_name: str, migration_session_id: int, company_name: str, is_final_attempt: bool = True):
        """
        Send one consolidated email with all failed tables.
        ✅ ONLY sends on final retry attempt to avoid duplicate emails.
        """
        if not self.has_failures():
            logger.info("✅ No failures to report - skipping consolidated error email")
            return
        
        # ✅ CHECK: Only send email on final retry attempt
        if not is_final_attempt:
            logger.info(f"⏭️ SKIPPING consolidated error email (not final attempt) - {len(self.failed_tables)} failures tracked")
            return

        try:
            from utils.email_utils import send_error_notification

            # Create consolidated error message
            failure_summary = self.get_failure_summary()

            # Build detailed error message for each failed table
            error_details = []
            for failure in failure_summary["failed_tables"]:
                timestamp = failure["failure_time"].strftime("%Y-%m-%d %H:%M:%S UTC")
                error_details.append(f"""
🔴 Table: {failure['table_name']}
   🕒 Failed at: {timestamp}
   📋 Error: {failure['error_message']}
   ----------------------------------""")

            consolidated_error_message = f"""Migration completed with {len(self.failed_tables)} failed table(s).

FAILED TABLES SUMMARY:
{''.join(error_details)}"""

            # Send consolidated email
            email_success = send_error_notification(
                table_name="Migration Summary",
                error_message=consolidated_error_message,
                connection_name=connection_name,
                service_name=service_name,
                user_id=user_id,
                company_id=cmp_id,
                additional_context={
                    "job_name": job_name,
                    "migration_session_id": migration_session_id,
                    "company_name": company_name,
                    "connection_id": connection_id,
                }
            )
            if email_success:
                logger.info(f"Consolidated error email sent successfully")
            else:
                logger.warning(f"Failed to send consolidated error email")
        except Exception as e:
            logger.error(f"Error sending consolidated error email: {e}")


# Maximum number of Excel-format tables writing simultaneously.
# openpyxl's write-only mode is single-threaded by design (GIL + openpyxl
# internal state), so more than 2 concurrent Excel writers wastes memory
# without gaining throughput. Non-Excel tables are not affected.
_EXCEL_WRITE_SEMAPHORE_DEFAULT = 1
SHAREPOINT_SIMPLE_UPLOAD_LIMIT = 250 * 1024 * 1024
# Was 5 MB — too small for large files (too many round-trips, Graph API overhead per chunk).
# 20 MB gives 4× fewer requests and fits well within Graph's 60 MB session chunk limit.
SHAREPOINT_UPLOAD_CHUNK_SIZE = 20 * 1024 * 1024
SHAREPOINT_CHUNK_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
SHAREPOINT_CHUNK_MAX_RETRIES = 5
SHAREPOINT_SESSION_RENEWAL_MAX_ATTEMPTS = 3
import threading
from dataclasses import dataclass, field

@dataclass
class _GraphTokenManager:
    """
    Thread-safe token cache that auto-refreshes before expiry.
    Pass one instance across all table workers so they share a single
    refresh lock instead of thundering-herd on the token endpoint.
    """
    dest_config: Dict[str, Any]
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _token: Optional[str] = field(default=None, init=False)
    _expires_at: float = field(default=0.0, init=False)
    # Refresh 5 minutes before actual expiry so in-flight requests never
    # hit a just-expired token.
    _REFRESH_BUFFER_SECONDS: float = 300.0

    def get_token(self) -> str:
        with self._lock:
            if time.monotonic() >= self._expires_at:
                self._refresh()
            return self._token  # type: ignore[return-value]

    def _refresh(self) -> None:
        """Acquire a new token. Caller must hold _lock."""
        logger.info("_GraphTokenManager: refreshing Graph access token")
        self._token = _acquire_graph_token(self.dest_config)
        # Azure AD tokens are valid for 3600 s by default. We use 3300 s so we
        # refresh 5 minutes early. If your tenant issues shorter tokens, lower
        # this value.
        self._expires_at = time.monotonic() + 3300.0
        logger.info("_GraphTokenManager: token refreshed, next refresh in %.0fs", 3300.0)


def _send_sharepoint_success_notification(
    *,
    request: SnowflakeSharePointMigrationRequest,
    table_name: str,
    table_settings: Dict[str, Any],
    remote_path: str,
    sharepoint_url: Optional[str],
    total_rows: int,
    col_count: int,
    completed_at: datetime,
) -> bool:
    started_at = time.perf_counter()
    logger.info("Starting _send_sharepoint_success_notification | table=%s", table_name)
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
        logger.info(
            "Finished _send_sharepoint_success_notification | table=%s reason=no_recipients elapsed_seconds=%.3f",
            table_name,
            time.perf_counter() - started_at,
        )
        return False

    config = EmailConfiguration()
    config.recipients = recipients
    if not config.is_configured():
        logger.info(
            "Skipping SharePoint success notification for table %s; email config incomplete (missing=%s)",
            table_name,
            ", ".join(config.get_missing_config()),
        )
        logger.info(
            "Finished _send_sharepoint_success_notification | table=%s reason=missing_config elapsed_seconds=%.3f",
            table_name,
            time.perf_counter() - started_at,
        )
        return False

    connection_name = request.connection_name or f"Connection {request.connection_id}"
    company_name = request.company_name or f"Company {request.cmp_id}"
    group_names = [str(name or "").strip() for name in (table_settings.get("notification_email_group_names") or []) if str(name or "").strip()]
    completed_at_text = completed_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    # Merge saved template (base) with per-table email_custom_fields overrides.
    # views.py stores the full serialized EmailTemplate under "email_template"
    # and any per-table field overrides under "email_custom_fields".
    _email_template = table_settings.get("email_template") if isinstance(table_settings, dict) else None
    _email_custom   = table_settings.get("email_custom_fields") if isinstance(table_settings, dict) else None
    email_custom_fields = {
        **(_email_template if isinstance(_email_template, dict) else {}),
        **(_email_custom   if isinstance(_email_custom,   dict) else {}),
    } or None
    logger.info(
        "[file_destination_email_input] table=%s settings_keys=%s email_custom_fields_keys=%s file_url=%s",
        table_name,
        sorted(table_settings.keys()) if isinstance(table_settings, dict) else [],
        sorted(email_custom_fields.keys()) if isinstance(email_custom_fields, dict) else [],
        sharepoint_url,
    )
    subject, html_content, plain_text = build_file_destination_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context={
            "company_name": company_name,
            "service_name": "Snowflake to SharePoint",
            "destination": "SharePoint",
            "rows_exported": total_rows,
            "columns_exported": col_count,
            "file_path": remote_path,
            "file_url": sharepoint_url,
            "sharepoint_path": remote_path,
            "sharepoint_url": sharepoint_url,
            "path_label": "SharePoint Path",
            "completed_at": completed_at_text,
            "email_groups": group_names,
        },
        email_custom_fields=email_custom_fields,
    )

    try:
        success = send_email_with_retry(
            config=config,
            subject=subject,
            html_content=html_content,
            plain_text=plain_text,
        )
    except Exception as exc:
        logger.info("SharePoint success notification failed for table %s: %s", table_name, exc)
        return False

    if success:
        logger.info(
            "SharePoint success notification sent | table=%s recipients=%s",
            table_name,
            len(recipients),
        )
    else:
        logger.warning("SharePoint success notification not sent | table=%s", table_name)
    logger.info(
        "Finished _send_sharepoint_success_notification | table=%s success=%s elapsed_seconds=%.3f",
        table_name,
        success,
        time.perf_counter() - started_at,
    )
    return success


def _mark_unprocessed_tables_failed(
    *,
    request: SnowflakeSharePointMigrationRequest,
    job_name: str,
    error_message: str,
    processed_tables: Optional[List[str]] = None,
) -> None:
    """Mark tables failed when a terminal error happens before table workers run."""
    processed = {str(name or "").strip() for name in (processed_tables or []) if str(name or "").strip()}
    selected = [str(name or "").strip() for name in (request.selected_tables or []) if str(name or "").strip()]

    for table_name in selected:
        if table_name in processed:
            continue
        try:
            _mark_table_failed_with_error(
                request=request,
                table_name=table_name,
                job_name=job_name,
                error_message=error_message,
            )
        except Exception as table_err:
            logger.warning("Failed to mark unprocessed SharePoint table %s as failed: %s", table_name, table_err)


def _get_retry_delay_seconds(response: requests.Response, attempt: int) -> float:
    """Compute a retry delay for transient SharePoint upload failures."""
    retry_after = str(response.headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return max(1.0, float(retry_after))
    # Exponential backoff with a small cap to keep long uploads moving.
    return min(30.0, float(2 ** max(0, attempt - 1)))


def _parse_next_expected_offset(payload: dict, file_size: int, fallback_offset: int) -> int:
    """Extract the next byte offset SharePoint expects from an upload response."""
    if not isinstance(payload, dict):
        return fallback_offset

    ranges = payload.get("nextExpectedRanges")
    if not isinstance(ranges, list) or not ranges:
        return fallback_offset

    first_range = str(ranges[0] or "").strip()
    if not first_range:
        return fallback_offset

    start_str = first_range.split("-", 1)[0].strip()
    if not start_str.isdigit():
        return fallback_offset

    next_offset = int(start_str)
    if next_offset < 0:
        return fallback_offset
    return min(file_size, next_offset)


def _is_session_recoverable_response(response: requests.Response, detail: str) -> bool:
    """Determine whether a failed upload response likely needs a new upload session."""
    status = int(getattr(response, "status_code", 0) or 0)
    content_type = str(response.headers.get("Content-Type") or "").lower()
    detail_text = str(detail or "").lower()

    if status in {401, 403, 404, 410}:
        return True
    if "text/html" in content_type:
        return True
    return any(
        marker in detail_text
        for marker in (
            "upload session",
            "session has expired",
            "session expired",
            "invalid session",
            "cannot be found",
            "request has expired",
        )
    )


def _sharepoint_request(
    method: str,
    url: str,
    access_token: str,
    *,
    json_body: Optional[dict] = None,
    data: Any = None,
    extra_headers: Optional[dict] = None,
    suppress_not_found_log: bool = False,
) -> dict:
    """Execute a Microsoft Graph request for SharePoint migration operations.

    Args:
        method: HTTP method to send to Microsoft Graph.
        url: Fully qualified Graph endpoint URL.
        access_token: OAuth access token used for authorization.
        json_body: Optional JSON payload for POST-style requests.
        data: Optional raw request body, typically file bytes.
        extra_headers: Optional headers merged into the default Graph headers.
        suppress_not_found_log: When True, do not emit warning-level logs for
            HTTP 404 responses that are expected during existence probes.

    Returns:
        dict: Parsed JSON response body, or an empty dict for no-content responses.

    Raises:
        HTTPException: If the Graph request fails, returns an error response, or
            returns invalid data needed by the migration flow.
    """
    started_at = time.perf_counter()
    logger.info("Starting _sharepoint_request | method=%s url=%s", method, url)
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    logger.info("SharePoint migration: Graph request | method=%s url=%s", method, url)
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            data=data,
            timeout=GRAPH_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("SharePoint migration: Graph request failed | url=%s error=%s", url, str(exc))
        logger.info(
            "Finished _sharepoint_request | method=%s url=%s outcome=request_exception elapsed_seconds=%.3f",
            method,
            url,
            time.perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=422,
            detail="Failed to reach Microsoft Graph during SharePoint upload. Please verify network access and try again.",
        ) from exc

    if response.status_code >= 400:
        detail = _extract_graph_error(response)
        if response.status_code == 404 and suppress_not_found_log:
            logger.info(
                "SharePoint migration: Graph item not found during probe | status=%s url=%s detail=%s",
                response.status_code,
                url,
                detail,
            )
        else:
            logger.warning(
                "SharePoint migration: Graph request returned error | status=%s url=%s detail=%s",
                response.status_code,
                url,
                detail,
            )
            logger.info(
                "Finished _sharepoint_request | method=%s url=%s outcome=http_error status=%s elapsed_seconds=%.3f",
                method,
                url,
                response.status_code,
                time.perf_counter() - started_at,
            )
        raise HTTPException(status_code=response.status_code, detail=detail)

    if not response.content:
        logger.info(
            "Finished _sharepoint_request | method=%s url=%s outcome=no_content elapsed_seconds=%.3f",
            method,
            url,
            time.perf_counter() - started_at,
        )
        return {}

    try:
        payload = response.json()
        logger.info(
            "Finished _sharepoint_request | method=%s url=%s outcome=success elapsed_seconds=%.3f",
            method,
            url,
            time.perf_counter() - started_at,
        )
        return payload
    except ValueError:
        logger.info(
            "Finished _sharepoint_request | method=%s url=%s outcome=non_json elapsed_seconds=%.3f",
            method,
            url,
            time.perf_counter() - started_at,
        )
        return {}


def _resolve_root_folder_item(access_token: str, drive_id: str, root_folder: str) -> dict:
    """Resolve the configured root folder item inside a SharePoint drive.

    Args:
        access_token: OAuth access token for Microsoft Graph.
        drive_id: SharePoint drive identifier for the selected document library.
        root_folder: Configured base folder path inside the document library.

    Returns:
        dict: Graph metadata for the resolved folder item.

    Raises:
        HTTPException: If the folder does not exist or is not a folder item.
    """
    started_at = time.perf_counter()
    logger.info("Starting _resolve_root_folder_item | drive_id=%s root_folder=%s", drive_id, root_folder)
    normalized_root = _normalize_root_folder(root_folder)
    if not normalized_root:
        url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root"
        payload = _sharepoint_request("GET", url, access_token)
        logger.info(
            "Finished _resolve_root_folder_item | drive_id=%s root_folder=%s elapsed_seconds=%.3f",
            drive_id,
            root_folder,
            time.perf_counter() - started_at,
        )
        return payload

    encoded_path = _encode_graph_path(normalized_root)
    url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root:/{encoded_path}"
    try:
        payload = _sharepoint_request("GET", url, access_token, suppress_not_found_log=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Configured root folder does not exist in SharePoint document library: {root_folder}. "
                    "Please create the folder or update the connection to use an existing folder."
                ),
            ) from exc
        if exc.status_code in (401, 403):
            raise HTTPException(
                status_code=422,
                detail=f"Configured root folder is not accessible in SharePoint document library: {root_folder}",
            ) from exc
        raise
    if "folder" not in payload:
        raise HTTPException(
            status_code=422,
            detail=f"Root folder path is not a folder in the document library: {root_folder}",
        )
    logger.info(
        "Finished _resolve_root_folder_item | drive_id=%s root_folder=%s elapsed_seconds=%.3f",
        drive_id,
        root_folder,
        time.perf_counter() - started_at,
    )
    return payload


def _ensure_sharepoint_subdir(access_token: str, drive_id: str, root_folder: str, sub_dir: str) -> str:
    """Ensure a table-specific SharePoint subfolder exists under the root folder.

    Args:
        access_token: OAuth access token for Microsoft Graph.
        drive_id: SharePoint drive identifier for the selected document library.
        root_folder: Configured base folder path inside the document library.
        sub_dir: Optional per-table subfolder path from export settings.

    Returns:
        str: Normalized folder path relative to the drive root where the file
        should be uploaded.

    Raises:
        HTTPException: If the folder path is inaccessible, invalid, or cannot be
            created.
    """
    started_at = time.perf_counter()
    logger.info(
        "Starting _ensure_sharepoint_subdir | drive_id=%s root_folder=%s sub_dir=%s",
        drive_id,
        root_folder,
        sub_dir,
    )
    root_item = _resolve_root_folder_item(access_token, drive_id, root_folder)
    current_item_id = str(root_item.get("id") or "")
    current_path = _normalize_root_folder(root_folder)
    target = _normalize_root_folder(sub_dir)

    if not target:
        logger.info(
            "Finished _ensure_sharepoint_subdir | drive_id=%s root_folder=%s sub_dir=%s result=%s elapsed_seconds=%.3f",
            drive_id,
            root_folder,
            sub_dir,
            current_path,
            time.perf_counter() - started_at,
        )
        return current_path

    accumulated_segments = [segment for segment in current_path.split("/") if segment]
    for segment in [part for part in target.split("/") if part]:
        accumulated_segments.append(segment)
        candidate_path = "/".join(accumulated_segments)
        encoded_path = _encode_graph_path(candidate_path)
        get_url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root:/{encoded_path}"
        try:
            item = _sharepoint_request("GET", get_url, access_token, suppress_not_found_log=True)
        except HTTPException as exc:
            if exc.status_code != 404:
                if exc.status_code in (401, 403):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Target folder is not accessible in document library: {candidate_path}",
                    ) from exc
                raise

            create_url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/items/{quote(current_item_id, safe='')}/children"
            try:
                item = _sharepoint_request(
                    "POST",
                    create_url,
                    access_token,
                    json_body={
                        "name": segment,
                        "folder": {},
                        "@microsoft.graph.conflictBehavior": "fail",
                    },
                )
            except HTTPException as create_exc:
                if create_exc.status_code != 409:
                    raise

                # Another parallel table can create the same folder between our
                # GET 404 and POST create. Re-read the path before failing.
                item = None
                for _ in range(3):
                    try:
                        item = _sharepoint_request("GET", get_url, access_token, suppress_not_found_log=True)
                        break
                    except HTTPException as retry_exc:
                        if retry_exc.status_code != 404:
                            raise
                        time.sleep(0.2)

                if item is None:
                    raise create_exc

        if "folder" not in item:
            raise HTTPException(
                status_code=422,
                detail=f"Target folder path is not a folder in document library: {candidate_path}",
            )
        current_item_id = str(item.get("id") or "")

    result_path = "/".join(accumulated_segments)
    logger.info(
        "Finished _ensure_sharepoint_subdir | drive_id=%s root_folder=%s sub_dir=%s result=%s elapsed_seconds=%.3f",
        drive_id,
        root_folder,
        sub_dir,
        result_path,
        time.perf_counter() - started_at,
    )
    return result_path


def _build_sharepoint_remote_path(folder_path: str, filename: str) -> str:
    """Build the SharePoint-relative destination path for an exported file.

    Args:
        folder_path: Normalized folder path relative to the document library root.
        filename: Exported file name.

    Returns:
        str: Combined relative path used by Microsoft Graph file upload APIs.
    """
    base = _normalize_root_folder(folder_path)
    if not base:
        return filename
    return f"{base}/{filename}"


def _create_upload_session(access_token: str, drive_id: str, remote_path: str) -> str:
    """Create a Microsoft Graph upload session for a large SharePoint file.

    Args:
        access_token: OAuth access token for Microsoft Graph.
        drive_id: SharePoint drive identifier for the selected document library.
        remote_path: Relative destination path for the uploaded file.

    Returns:
        str: Upload session URL returned by Microsoft Graph.

    Raises:
        HTTPException: If Graph does not return a usable upload session.
    """
    started_at = time.perf_counter()
    logger.info("Starting _create_upload_session | drive_id=%s remote_path=%s", drive_id, remote_path)
    encoded_path = _encode_graph_path(remote_path)
    url = (
        f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root:/{encoded_path}:/createUploadSession"
    )
    payload = _sharepoint_request(
        "POST",
        url,
        access_token,
        json_body={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
    )
    upload_url = payload.get("uploadUrl")
    if not upload_url:
        raise HTTPException(
            status_code=422,
            detail="Microsoft Graph did not return an upload session for the SharePoint file upload.",
        )
    logger.info(
        "Finished _create_upload_session | drive_id=%s remote_path=%s elapsed_seconds=%.3f",
        drive_id,
        remote_path,
        time.perf_counter() - started_at,
    )
    return str(upload_url)


def _extract_drive_item_web_url(response: Any) -> Optional[str]:
    if isinstance(response, dict):
        payload = response
    else:
        try:
            response_content = getattr(response, "content", None)
            payload = response.json() if response_content else {}
        except (AttributeError, ValueError, TypeError):
            payload = {}
    if isinstance(payload, dict):
        web_url = str(payload.get("webUrl") or "").strip()
        if web_url:
            return web_url
    return None


def _upload_small_file(access_token: str, drive_id: str, local_path: str, remote_path: str) -> tuple[int, Optional[str]]:
    """Upload a small local export file to SharePoint in a single request.

    Args:
        access_token: OAuth access token for Microsoft Graph.
        drive_id: SharePoint drive identifier for the selected document library.
        local_path: Local filesystem path to the exported file.
        remote_path: Relative SharePoint destination path for the file.

    Returns:
        int: Number of bytes uploaded.
    """
    started_at = time.perf_counter()
    logger.info("Starting _upload_small_file | local_path=%s remote_path=%s", local_path, remote_path)
    encoded_path = _encode_graph_path(remote_path)
    url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root:/{encoded_path}:/content"
    file_size = os.path.getsize(local_path)
    logger.info(
        "Starting SharePoint simple upload | local_path=%s remote_path=%s file_size_bytes=%s",
        local_path,
        remote_path,
        file_size,
    )
    with open(local_path, "rb") as local_file:
        response = _sharepoint_request(
            "PUT",
            url,
            access_token,
            data=local_file.read(),
            extra_headers={"Content-Type": "application/octet-stream"},
        )
    web_url = _extract_drive_item_web_url(response)
    print(
        "Completed SharePoint simple upload | local_path=%s remote_path=%s file_size_bytes=%s",
        local_path,
        remote_path,
        file_size,
    )
    print(
        "Finished _upload_small_file | local_path=%s remote_path=%s elapsed_seconds=%.3f",
        local_path,
        remote_path,
        time.perf_counter() - started_at,
    )
    print(
        "Finished _upload_small_file | local_path=%s remote_path=%s elapsed_seconds=%.3f",
        local_path,
        remote_path,
        time.perf_counter() - started_at,
    )
    return file_size, web_url

def _upload_large_file(
    token_manager: "_GraphTokenManager",
    drive_id: str,
    local_path: str,
    remote_path: str,
) -> tuple[int, Optional[str]]:
    """
    Upload a large file to SharePoint using resumable chunked upload.

    Token expiry is handled transparently: when a chunk response indicates an
    expired/forbidden session, the token is refreshed via token_manager and a
    new upload session is created from the last committed byte offset.
    """
    started_at = time.perf_counter()
    file_size = os.path.getsize(local_path)
    uploaded = 0
    web_url: Optional[str] = None

    for session_attempt in range(1, SHAREPOINT_SESSION_RENEWAL_MAX_ATTEMPTS + 1):
        # Always use a fresh token when (re-)creating an upload session.
        access_token = token_manager.get_token()
        upload_url = _create_upload_session(access_token, drive_id, remote_path)
        logger.info(
            "_upload_large_file session created | remote_path=%s attempt=%s/%s resume_offset=%s",
            remote_path, session_attempt, SHAREPOINT_SESSION_RENEWAL_MAX_ATTEMPTS, uploaded,
        )
        print(
            "_upload_large_file session created | remote_path=%s attempt=%s/%s resume_offset=%s",
            remote_path, session_attempt, SHAREPOINT_SESSION_RENEWAL_MAX_ATTEMPTS, uploaded,
        )

        session_broken = False
        with open(local_path, "rb") as fh:
            fh.seek(uploaded)

            while uploaded < file_size:
                chunk = fh.read(SHAREPOINT_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break

                chunk_end = uploaded + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {uploaded}-{chunk_end}/{file_size}",
                }

                response = None
                chunk_committed = False

                for attempt in range(1, SHAREPOINT_CHUNK_MAX_RETRIES + 1):
                    try:
                        response = requests.put(
                            upload_url,
                            headers=headers,
                            data=chunk,
                            timeout=GRAPH_TIMEOUT_SECONDS,
                        )
                    except requests.RequestException as exc:
                        if attempt >= SHAREPOINT_CHUNK_MAX_RETRIES:
                            raise HTTPException(
                                status_code=422,
                                detail=(
                                    "Network failure uploading file chunks to SharePoint. "
                                    "Please verify network access and retry."
                                ),
                            ) from exc
                        delay = min(30.0, float(2 ** (attempt - 1)))
                        logger.warning(
                            "_upload_large_file network error | attempt=%s/%s remote_path=%s error=%s retry_in=%.1fs",
                            attempt, SHAREPOINT_CHUNK_MAX_RETRIES, remote_path, exc, delay,
                        )
                        time.sleep(delay)
                        continue

                    # ── success ───────────────────────────────────────────────
                    if response.status_code in (200, 201, 202):
                        chunk_committed = True
                        break

                    detail      = _extract_graph_error(response)
                    status      = response.status_code
                    content_type = str(response.headers.get("Content-Type") or "").lower()

                    logger.warning(
                        "_upload_large_file chunk error | attempt=%s/%s status=%s remote_path=%s detail=%s",
                        attempt, SHAREPOINT_CHUNK_MAX_RETRIES, status, remote_path, detail,
                    )

                    # ── token / session expired: break to outer session loop ──
                    if _is_session_recoverable_response(response, detail):
                        logger.warning(
                            "_upload_large_file session expired/forbidden; refreshing token and renewing session "
                            "| status=%s offset=%s", status, uploaded,
                        )
                        # Force token refresh so the next session uses a new token.
                        with token_manager._lock:
                            token_manager._expires_at = 0.0
                        session_broken = True
                        break

                    # ── transient: retry same chunk ───────────────────────────
                    if status in SHAREPOINT_CHUNK_RETRYABLE_STATUSES and attempt < SHAREPOINT_CHUNK_MAX_RETRIES:
                        delay = _get_retry_delay_seconds(response, attempt)
                        logger.info(
                            "_upload_large_file retrying chunk | attempt=%s/%s retry_in=%.1fs",
                            attempt + 1, SHAREPOINT_CHUNK_MAX_RETRIES, delay,
                        )
                        time.sleep(delay)
                        continue

                    # ── hard failure: raise immediately ───────────────────────
                    if "text/html" in content_type:
                        detail = (
                            "SharePoint returned an HTML page instead of the expected upload response. "
                            "The session may have been redirected. "
                            f"Snippet: {detail[:300]}"
                        )
                    raise HTTPException(status_code=status, detail=detail)

                if session_broken:
                    break  # jump to next session_attempt

                if not chunk_committed:
                    # Exhausted retries without a success or a session error.
                    raise HTTPException(
                        status_code=422,
                        detail=f"SharePoint chunk upload failed after {SHAREPOINT_CHUNK_MAX_RETRIES} retries.",
                    )

                # ── advance offset ────────────────────────────────────────────
                uploaded = chunk_end + 1
                logger.info(
                    "_upload_large_file chunk committed | remote_path=%s uploaded=%s total=%s chunk_bytes=%s",
                    remote_path, uploaded, file_size, len(chunk),
                )
                print(
                    "_upload_large_file chunk committed | remote_path=%s uploaded=%s total=%s chunk_bytes=%s",
                    remote_path, uploaded, file_size, len(chunk),
                )

                if response is not None and response.status_code == 202:
                    try:
                        rp = response.json() if response.content else {}
                    except ValueError:
                        rp = {}
                    expected = _parse_next_expected_offset(rp, file_size, uploaded)
                    if expected != uploaded:
                        logger.info(
                            "_upload_large_file offset adjusted by server | local=%s server=%s",
                            uploaded, expected,
                        )
                        print(
                            "_upload_large_file offset adjusted by server | local=%s server=%s",
                            uploaded, expected,
                        )
                        uploaded = expected
                        fh.seek(uploaded)
                elif response is not None and response.status_code in (200, 201):
                    web_url = _extract_drive_item_web_url(response)

        if not session_broken:
            # Upload completed without needing another session.
            break
    else:
        raise HTTPException(
            status_code=422,
            detail=(
                "SharePoint upload session could not be kept alive after "
                f"{SHAREPOINT_SESSION_RENEWAL_MAX_ATTEMPTS} attempts. "
                "The token may be configured with a very short lifetime. Please retry."
            ),
        )

    logger.info(
        "_upload_large_file complete | remote_path=%s bytes=%s elapsed=%.3fs",
        remote_path, uploaded, time.perf_counter() - started_at,
    )
    print(
        "_upload_large_file complete | remote_path=%s bytes=%s elapsed=%.3fs",
        remote_path, uploaded, time.perf_counter() - started_at,
    )
    return file_size, web_url

def _upload_file_to_sharepoint(
    token_manager: "_GraphTokenManager",
    drive_id: str,
    local_path: str,
    remote_path: str,
) -> tuple[int, Optional[str]]:
    """Dispatch to simple or chunked upload based on file size."""
    file_size = os.path.getsize(local_path)
    if file_size <= SHAREPOINT_SIMPLE_UPLOAD_LIMIT:
        # Small upload: still guard against token expiry by getting a fresh one.
        return _upload_small_file(token_manager.get_token(), drive_id, local_path, remote_path)
    return _upload_large_file(token_manager, drive_id, local_path, remote_path)



def _process_snowflake_to_sharepoint_table(
    *,
    request: SnowflakeSharePointMigrationRequest,
    table_name: str,
    source_config: Dict[str, Any],
    dest_config: Dict[str, Any],
    token_manager: "_GraphTokenManager",
    drive_id: str,
    root_folder_path: str,
    file_format: str,
    default_delimiter: str,
    default_quotechar: str,
    escapechar: Optional[str],
    null_sequence: str,
    fetch_size: int,
    table_export_settings: Dict[str, Any],
    job_name: str,
    excel_semaphore: Optional[threading.Semaphore] = None,
    failure_tracker: Optional[MigrationFailureTracker] = None,
) -> Dict[str, Any]:
    """
    Export one Snowflake table and upload the result to SharePoint.

    Never raises — all failures are captured into the returned result dict so
    the caller can continue processing remaining tables unaffected.
    """
    started_at = time.perf_counter()
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    table_started = datetime.now(timezone.utc)

    result: Dict[str, Any] = {
        "table_name": table_name,
        "status": "failed",
        "rows": 0,
        "columns": 0,
        "remote_path": None,
        "bytes_uploaded": 0,
        "web_url": None,
        "error": None,
        "table_started": table_started,
        "table_completed_at": None,
    }

    # Check if this table has already been successfully processed in a previous attempt of the current session.
    try:
        from utils.database_utils import check_table_completed_status
        completed_info = check_table_completed_status(
            connection_id=connection_id,
            table_name=table_name,
            migration_session_id=migration_session_id,
            job_name=job_name,
        )
        if completed_info:
            logger.info(
                "Table %s already completed in a previous attempt. Skipping run and returning cached result | rows=%s columns=%s",
                table_name,
                completed_info["rows"],
                completed_info["columns"],
            )
            return {
                "table_name": table_name,
                "status": "success",
                "rows": completed_info["rows"],
                "columns": completed_info["columns"],
                "remote_path": "(skipped: already uploaded)",
                "bytes_uploaded": 0,
                "web_url": "(skipped: already uploaded)",
                "error": None,
                "table_started": table_started,
                "table_completed_at": datetime.now(timezone.utc),
            }
    except Exception as check_err:
        logger.warning(
            "Failed to check if table %s was completed; will proceed with migration: %s",
            table_name,
            check_err,
        )

    sf_conn = None
    try:
        # ── mark in-progress (non-fatal if it fails) ──────────────────────
        try:
            update_table_status_snowflake(
                connection_id, table_name, "in_progress", job_name, migration_session_id
            )
        except Exception as e:
            logger.warning("Failed to mark table in_progress | table=%s error=%s", table_name, e)

        try:
            SimpleProgressTracker.reset_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
            )
        except Exception as e:
            logger.warning("Failed to reset staging count | table=%s error=%s", table_name, e)

        # ── Snowflake connection ───────────────────────────────────────────
        sf_service = SnowflakeService(
            source_config, destination_schema=None, validate_destination_schema=False
        )
        sf_conn = sf_service.create_connection()
        schema_name = _extract_snowflake_schema(source_config)
        if schema_name:
            with sf_conn.cursor() as schema_cur:
                schema_cur.execute(f"USE SCHEMA {schema_name}")

        table_settings = dict(lookup_table_export_settings(table_export_settings, table_name))
        ts = build_utc_filename_suffix(table_settings.get("filename_date_format"))
        selected_fields = _normalize_selected_fields(table_settings.get("selected_fields"))
        sort_columns = table_settings.get("sort_columns") or (table_settings.get("excel_options") or {}).get("sort_columns")
        # ── resolve export parameters ─────────────────────────────────────
        table_file_format = str(
            table_settings.get("file_format") or file_format or "csv"
        ).strip().lower()
        if table_file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid file_format {table_file_format!r}; expected csv, json, parquet, or excel",
            )

        # ── Excel pre-flight: check limits before any chunk fetching ──────
        if table_file_format == "excel":
            preflight_query = _build_snowflake_select_query(table_name, selected_fields,sort_columns)
            check_excel_limits_preflight(sf_conn, preflight_query, table_name)

        table_target_folder = resolve_relative_target_folder(
            root_folder_path,
            table_settings.get("target_folder"),
        )
        folder_path = _ensure_sharepoint_subdir(
            token_manager.get_token(), drive_id, root_folder_path, table_target_folder
        )

        base_name = str(table_settings.get("output_file_name") or "").strip() or table_name
        add_ts    = table_settings.get("add_utc_timestamp")
        add_ts    = True if add_ts is None else bool(add_ts)
        ext       = {"csv": "csv", "json": "json", "parquet": "parquet", "excel": "xlsx"}[table_file_format]
        filename  = f"{base_name}_{ts}.{ext}" if add_ts else f"{base_name}.{ext}"
        remote_path = _build_sharepoint_remote_path(folder_path, filename)

        # ── progress callback (defined once, not inside a retry loop) ─────
        def _progress_callback(_chunk: int, total: int) -> None:
            try:
                SimpleProgressTracker.set_staging_count(
                    connection_id=connection_id,
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    table_name=table_name,
                    count_value=total,
                )
            except Exception as cb_err:
                logger.warning(
                    "Failed to update staging count | table=%s total=%s error=%s",
                    table_name, total, cb_err,
                )

        # ── write + upload inside a temp directory ────────────────────────
        with tempfile.TemporaryDirectory(prefix="sf_sharepoint_") as tmpdir:
            local_path     = os.path.join(tmpdir, filename)
            progress_label = f"SharePoint table={table_name}"

            with sf_conn.cursor() as cur:
                query = _build_snowflake_select_query(table_name, selected_fields,sort_columns)
                logger.info(
                    "_process_snowflake_to_sharepoint_table executing query | table=%s fields=%s",
                    table_name, selected_fields or "ALL",
                )
                try:
                    cur.execute(query)
                except Exception as e:
                    raise _map_snowflake_export_error(table_name, e) from e

                # Only this Snowflake-as-source path applies source scripts.
                active_transform = get_active_source_script(connection_id, table_name)
                script_text = (active_transform or {}).get("script_text")
                if script_text and str(script_text).strip():
                    # Keep the existing writers unchanged; the adapter exposes
                    # transformed pandas batches as the same cursor interface.
                    cur = TransformingCursor(
                        cur,
                        fetch_size=fetch_size,
                        script_text=script_text,
                        entry_point=(active_transform or {}).get("entry_point") or "",
                    )

                # ── write local file ──────────────────────────────────────
                write_started_at = time.perf_counter()

                if table_file_format == "csv":
                    delimiter = (
                        _decode_csv_char(table_settings.get("csv_delimiter") or default_delimiter) or ","
                    )
                    quotechar = (
                        _decode_csv_char(table_settings.get("csv_quote_char") or default_quotechar) or '"'
                    )
                    total_rows, col_count = _write_csv(
                        cur, local_path,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        escapechar=escapechar,
                        null_sequence=null_sequence,
                        fetch_size=fetch_size,
                        progress_label=progress_label,
                        progress_callback=_progress_callback,
                    )
                elif table_file_format == "json":
                    total_rows, col_count = _write_json(
                        cur, local_path,
                        null_sequence=null_sequence or None,
                        fetch_size=fetch_size,
                        progress_label=progress_label,
                        progress_callback=_progress_callback,
                    )
                elif table_file_format == "parquet":
                    total_rows, col_count = _write_parquet(
                        cur, local_path,
                        fetch_size=fetch_size,
                        progress_label=progress_label,
                        progress_callback=_progress_callback,
                    )
                else:  # excel
                    if excel_semaphore is not None:
                        logger.info(
                            "_process_snowflake_to_sharepoint_table waiting for excel semaphore | table=%s",
                            table_name,
                        )
                        with excel_semaphore:
                            logger.info(
                                "_process_snowflake_to_sharepoint_table acquired excel semaphore | table=%s",
                                table_name,
                            )
                            total_rows, col_count = _write_excel(
                                cur, local_path,
                                sheet_name=table_settings.get("excel_sheet_name"),
                                excel_options=table_settings.get("excel_options"),
                                conditional_formats=table_settings.get("excel_conditional_formats"),
                                fetch_size=fetch_size,
                                progress_label=progress_label,
                                progress_callback=_progress_callback,
                            )
                    else:
                        total_rows, col_count = _write_excel(
                            cur, local_path,
                            sheet_name=table_settings.get("excel_sheet_name"),
                            excel_options=table_settings.get("excel_options"),
                            conditional_formats=table_settings.get("excel_conditional_formats"),
                            fetch_size=fetch_size,
                            progress_label=progress_label,
                            progress_callback=_progress_callback,
                        )

                write_elapsed = time.perf_counter() - write_started_at

            # ── file size diagnostics ─────────────────────────────────────
            file_size_bytes = os.path.getsize(local_path) if os.path.exists(local_path) else 0
            file_size_mb    = file_size_bytes / (1024 * 1024)
            logger.info(
                "_process_snowflake_to_sharepoint_table file written | "
                "table=%s format=%s rows=%s cols=%s size_mb=%.2f write_elapsed=%.3fs",
                table_name, table_file_format, total_rows, col_count, file_size_mb, write_elapsed,
            )

            # Guard: if the file writer silently produced nothing, fail clearly.
            if not os.path.exists(local_path):
                raise RuntimeError(
                    f"Export file was not created for table {table_name!r}; "
                    "the file writer may have failed silently."
                )

            # ── upload to SharePoint ──────────────────────────────────────
            upload_started_at = time.perf_counter()
            bytes_uploaded, web_url = _upload_file_to_sharepoint(
                token_manager, drive_id, local_path, remote_path
            )
            upload_elapsed = time.perf_counter() - upload_started_at

            logger.info(
                "_process_snowflake_to_sharepoint_table upload complete | "
                "table=%s size_mb=%.2f upload_elapsed=%.3fs throughput_mbps=%.2f",
                table_name, file_size_mb, upload_elapsed,
                (file_size_mb / max(upload_elapsed, 0.001)),
            )

        # ── success ────────────────────────────────────────────────────────
        table_completed_at = datetime.now(timezone.utc)
        result.update({
            "status": "success",
            "rows": total_rows,
            "columns": col_count,
            "remote_path": remote_path,
            "bytes_uploaded": bytes_uploaded,
            "web_url": web_url,
            "error": None,
            "table_completed_at": table_completed_at,
        })

        # ── post-success logging (non-fatal) ──────────────────────────────
        try:
            SimpleProgressTracker.set_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                count_value=total_rows,
            )
            update_table_status_snowflake(
                connection_id, table_name, "completed", job_name, migration_session_id,
                sync_end_time=table_completed_at,
            )
            data_transfer_logs(
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                usr_id=request.user_id,
                table_name=table_name,
                start_time=table_started,
                end_time=table_completed_at,
                inserts=total_rows,
                columns_data=col_count,
                session=migration_session_id,
                job_name=job_name,
                migration_session_id=migration_session_id,
            )
        except Exception as log_err:
            _log_stage_error(
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                function_name="_process_snowflake_to_sharepoint_table.logging",
                error=log_err,
                extra_message=f"table={table_name}",
            )

        try:
            _send_sharepoint_success_notification(
                request=request,
                table_name=table_name,
                table_settings=table_settings,
                remote_path=remote_path,
                sharepoint_url=web_url,
                total_rows=total_rows,
                col_count=col_count,
                completed_at=table_completed_at,
            )
        except Exception as notif_err:
            logger.warning(
                "Success notification failed (non-fatal) | table=%s error=%s",
                table_name, notif_err,
            )

        logger.info(
            "_process_snowflake_to_sharepoint_table success | table=%s rows=%s bytes=%s elapsed=%.3fs",
            table_name, total_rows, bytes_uploaded, time.perf_counter() - started_at,
        )

    except Exception as exc:
        # ── table failed: record error, mark failed in DB, never raise ────
        error_message = _extract_error_message(exc)
        result["error"] = error_message

        logger.error(
            "_process_snowflake_to_sharepoint_table failed | table=%s error=%s elapsed=%.3fs",
            table_name, error_message, time.perf_counter() - started_at,
            exc_info=True,
        )

        if failure_tracker is not None:
            failure_tracker.add_failure(table_name, error_message, "Snowflake to SharePoint")

        try:
            _mark_table_failed_with_error(
                request=request,
                table_name=table_name,
                job_name=job_name,
                error_message=error_message,
            )
        except Exception as mark_err:
            logger.warning(
                "Failed to mark table as failed in DB | table=%s error=%s",
                table_name, mark_err,
            )

        try:
            _log_stage_error(
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                function_name="_process_snowflake_to_sharepoint_table",
                error=exc,
                extra_message=f"table={table_name}",
            )
        except Exception as stage_err:
            logger.warning(
                "Failed to write stage error log | table=%s error=%s",
                table_name, stage_err,
            )

    finally:
        try:
            if sf_conn is not None:
                sf_conn.close()
        except Exception as close_err:
            logger.warning(
                "Failed to close Snowflake connection | table=%s error=%s",
                table_name, close_err,
            )

    return result

def migrate_snowflake_to_sharepoint(request: SnowflakeSharePointMigrationRequest) -> Dict[str, Any]:
    started_at = time.perf_counter()
    started_dt = datetime.now(timezone.utc)
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    job_name = request.job_name or f"Snowflake-SharePoint Migration {connection_id}"

    is_final_attempt = True
    failure_tracker = MigrationFailureTracker()

    results: Dict[str, Any] = {
        "status": "success",
        "status_code": 200,
        "job_name": job_name,
        "connection_id": connection_id,
        "migration_session_id": migration_session_id,
        "started_at": started_dt.isoformat(),
        "completed_at": None,
        "tables": {},
        "total_tables": len(request.selected_tables or []),
        "successful_tables": 0,
        "failed_tables": 0,
        "total_rows": 0,
        "service_name": "Snowflake-SharePoint",
    }

    try:
        # Reconcile any completed statuses that may have diverged due to a previous crash
        try:
            from utils.database_utils import reconcile_completed_tables
            reconcile_completed_tables(
                connection_id=connection_id,
                job_name=job_name,
                migration_session_id=migration_session_id,
                selected_tables=request.selected_tables or [],
            )
        except Exception as recon_err:
            logger.warning("Failed to reconcile completed tables status at startup: %s", recon_err)

        update_migration_status_log(job_name=job_name, message="Migration in progress", status="I", session=migration_session_id)

        source_config = request.source_config or {}
        dest_config = dict(request.destination_config or {})
        dest_config.setdefault("cmp_id", request.cmp_id)
        dest_config.setdefault("connection_id", request.connection_id)

        validate_sharepoint_authorization(SharePointAuthorizationRequest(destination_config=dest_config))

        root_folder_path = _normalize_root_folder(str(dest_config.get("root_folder") or "").strip())

        # ── single token manager for the whole job ────────────────────────────
        # All table workers (sequential or parallel) share this instance.
        # It refreshes transparently when the token is within 5 minutes of expiry.
        token_manager = _GraphTokenManager(dest_config=dest_config)
        initial_token = token_manager.get_token()   # validates credentials early

        site  = _resolve_sharepoint_site(initial_token, str(dest_config.get("sharepoint_site_url") or ""))
        drive = _resolve_document_library(initial_token, str(site.get("id") or ""), str(dest_config.get("document_library") or ""))
        drive_id = str(drive.get("id") or "")

        # Validate root folder exists before dispatching any table work.
        _resolve_root_folder_item(token_manager.get_token(), drive_id, root_folder_path)

        file_format      = str(dest_config.get("file_format") or "csv").strip().lower()
        if file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        default_delimiter = _decode_csv_char(dest_config.get("csv_delimiter") or ",")
        default_quotechar = _decode_csv_char(dest_config.get("csv_quote_char") or dest_config.get("csv_quotechar") or '"')
        null_sequence     = "" if dest_config.get("null_sequence") is None else str(dest_config["null_sequence"])
        fetch_size        = max(1, int(request.chunk_count or 5000))

        table_export_settings = request.table_export_settings or {}
        selected_tables = [str(t or "").strip() for t in (request.selected_tables or []) if str(t or "").strip()]
        execution_order = str(request.execution_order or "sequential").strip().lower()

        # Build common kwargs once to avoid repetition.
        common_kwargs = dict(
            request=request,
            source_config=source_config,
            dest_config=dest_config,
            token_manager=token_manager,       # ← token manager, not bare token
            drive_id=drive_id,
            root_folder_path=root_folder_path,
            file_format=file_format,
            default_delimiter=default_delimiter,
            default_quotechar=default_quotechar,
            escapechar=None,
            null_sequence=null_sequence,
            fetch_size=fetch_size,
            table_export_settings=table_export_settings,
            job_name=job_name,
            failure_tracker=failure_tracker,
        )

        table_results: List[Dict[str, Any]] = []

        if execution_order == "parallel" and len(selected_tables) > 1:
            # Count how many tables will actually write Excel (job-level OR per-table override).
            excel_table_count = sum(
                1 for t in selected_tables
                if str(
                    (table_export_settings.get(t) or {}).get("file_format")
                    or file_format or "csv"
                ).strip().lower() == "excel"
            )
            non_excel_count = len(selected_tables) - excel_table_count

            # Non-excel tables run fully parallel (I/O bound: Snowflake fetch + upload).
            # Excel tables share a semaphore so at most N write simultaneously.
            # We still use a single ThreadPoolExecutor sized to the largest need.
            excel_sem_limit = int(dest_config.get("excel_parallel_limit") or _EXCEL_WRITE_SEMAPHORE_DEFAULT)
            excel_semaphore = threading.Semaphore(excel_sem_limit)
            max_workers = min(
                max(non_excel_count + excel_sem_limit, 1),
                len(selected_tables),
                2,  # hard cap reduced from 6 to 2 to prevent parallel OOM memory accumulation
            )

            logger.info(
                "migrate_snowflake_to_sharepoint parallel | workers=%s tables=%s excel=%s non_excel=%s excel_sem=%s",
                max_workers, len(selected_tables), excel_table_count, non_excel_count, excel_sem_limit,
            )
            print(
                "migrate_snowflake_to_sharepoint parallel | workers=%s tables=%s excel=%s non_excel=%s excel_sem=%s",
                max_workers, len(selected_tables), excel_table_count, non_excel_count, excel_sem_limit,
            )

            # Pass the semaphore through common_kwargs so each table worker can acquire it.
            common_kwargs["excel_semaphore"] = excel_semaphore

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _process_snowflake_to_sharepoint_table,
                        table_name=table_name,
                        **common_kwargs,
                    ): table_name
                    for table_name in selected_tables
                }
                for future in as_completed(futures):
                    tbl = futures[future]
                    try:
                        table_results.append(future.result())
                    except Exception as exc:
                        error_message = _extract_error_message(exc)
                        logger.exception("Parallel table future raised unexpectedly | table=%s error=%s", tbl, error_message)
                        table_results.append({
                            "table_name": tbl, "status": "failed", "rows": 0,
                            "columns": 0, "remote_path": None, "bytes_uploaded": 0,
                            "web_url": None, "error": error_message,
                        })
                        try:
                            _mark_table_failed_with_error(request=request, table_name=tbl, job_name=job_name, error_message=error_message)
                        except Exception:
                            pass
                        failure_tracker.add_failure(tbl, error_message, "Snowflake to SharePoint")
        else:
            common_kwargs["excel_semaphore"] = None
            logger.info("migrate_snowflake_to_sharepoint sequential | tables=%s", len(selected_tables))
            print("migrate_snowflake_to_sharepoint sequential | tables=%s", len(selected_tables))
            for table_name in selected_tables:
                table_results.append(
                    _process_snowflake_to_sharepoint_table(table_name=table_name, **common_kwargs)
                )
        # ── aggregate results ─────────────────────────────────────────────────
        for tr in table_results:
            tbl = tr["table_name"]
            results["tables"][tbl] = {k: tr[k] for k in ("status", "rows", "columns", "remote_path", "bytes_uploaded", "error")}
            if tr["status"] == "success":
                results["total_rows"] += tr["rows"]
                results["successful_tables"] += 1
            else:
                results["failed_tables"] += 1

            _trigger_ws_all(
                migration_session_id=migration_session_id,
                connection_id=connection_id,
                cmp_id=request.cmp_id,
            )

        # ── final status ──────────────────────────────────────────────────────
        elapsed = time.perf_counter() - started_at
        results["completed_at"] = datetime.now(timezone.utc).isoformat()

        if results["failed_tables"] == 0:
            results["status"] = "success"
        elif results["successful_tables"] == 0:
            results["status"] = "failed"
        else:
            results["status"] = "partial"

        results["status_code"] = 200 if results["failed_tables"] == 0 else 206

        # Supply the child-result contract used by the Celery finalizer.
        results["execution_time"] = elapsed
        results["successful_tables_count"] = results["successful_tables"]
        results["failed_tables_count"] = results["failed_tables"]
        results["successful_tables_list"] = [
            table for table, result in results["tables"].items() if result["status"] == "success"
        ]
        results["failed_tables_list"] = [
            table for table, result in results["tables"].items() if result["status"] != "success"
        ]

        # Require failed table status to be saved before returning the API result.
        for failed_table in results["failed_tables_list"]:
            update_table_status_snowflake(
                connection_id, failed_table, "failed", job_name, migration_session_id
            )

        # Child tasks report results; only the parent finalizes the activity log.
        if not request.celery_child_mode:
            log_migration_completion(
                execution_time=results["execution_time"],
                total_rows=results["total_rows"],
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                user_id=request.user_id,
                session=migration_session_id,
                total_tables=results["total_tables"],
                successful_tables=results["successful_tables"],
                failed_tables=results["failed_tables"],
                job_name=job_name,
                log_id=request.log_id,
            )

        logger.info(
            "migrate_snowflake_to_sharepoint complete | connection_id=%s status=%s ok=%s failed=%s rows=%s elapsed=%.3fs",
            connection_id, results["status"], results["successful_tables"],
            results["failed_tables"], results["total_rows"], elapsed,
        )
        print(
            "migrate_snowflake_to_sharepoint complete | connection_id=%s status=%s ok=%s failed=%s rows=%s elapsed=%.3fs",
            connection_id, results["status"], results["successful_tables"],
            results["failed_tables"], results["total_rows"], elapsed,
        )
        # Send consolidated error email if there were any failures
        failure_tracker.send_consolidated_error_email(
            connection_name=request.connection_name or f"SharePoint {connection_id}",
            service_name="Snowflake to SharePoint",
            user_id=request.user_id,
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            company_name=request.company_name or "Unknown Company",
            is_final_attempt=is_final_attempt,
        )

        return results

    except Exception as exc:
        error_message = _extract_error_message(exc)
        elapsed = time.perf_counter() - started_at
        
        # Log overall migration setup/connection error using centralized error handling
        error_context = {
            "table_name": "Migration Process",
            "service_name": "Snowflake to SharePoint",
            "error_type": "MIGRATION_FAILED",
            "cmp_id": request.cmp_id,
            "connection_id": connection_id,
            "user_id": request.user_id,
            "job_name": job_name,
            "log_id": request.log_id if hasattr(request, 'log_id') else None,
            "migration_session_id": migration_session_id,
            "company_name": request.company_name or "Unknown Company",
            "session": request.session,
            "total_tables": results["total_tables"],
            "execution_time": elapsed,
            "total_rows": results.get("total_rows", 0),
            "successful_tables": results.get("successful_tables", 0)
        }
        
        try:
            handle_service_error(
                error=exc,
                context=error_context,
                log_files=None,
                send_email=True,
                is_final_attempt=is_final_attempt,
            )
        except Exception as handler_err:
            logger.warning(f"⚠️ handle_service_error failed in outer exception block: {handler_err}")

        _mark_unprocessed_tables_failed(
            request=request, job_name=job_name, error_message=error_message,
            processed_tables=list(results.get("tables", {}).keys()),
        )
        try:
            update_migration_status_log(job_name=job_name, message=error_message, status="F", session=migration_session_id)
        except Exception:
            pass
        _log_stage_error(
            cmp_id=request.cmp_id, connection_id=connection_id,
            function_name="migrate_snowflake_to_sharepoint",
            error=exc, extra_message=f"job_name={job_name}",
        )
        try:
            log_migration_failure(
                job_name=job_name, error_message=error_message,
                session=migration_session_id, cmp_id=request.cmp_id,
                connection_id=connection_id, user_id=request.user_id,
                execution_time=elapsed, total_rows=results.get("total_rows", 0),
                successful_tables=results.get("successful_tables", 0),
                total_tables=results.get("total_tables", 0),
            )
        except Exception:
            pass
        logger.info(
            "migrate_snowflake_to_sharepoint failed | connection_id=%s error=%s elapsed=%.3fs",
            connection_id, error_message, elapsed,
        )
        print(
            "migrate_snowflake_to_sharepoint failed | connection_id=%s error=%s elapsed=%.3fs",
            connection_id, error_message, elapsed,
        )
        raise
