from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import logging
import os
import socket
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import threading
from fastapi import HTTPException

from models.snowflake_sftp_models import SnowflakeSFTPMigrationRequest
from models.sftp_models import SFTPAuthorizationRequest
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
from services.sftp.client import test_sftp_connection, validate_sftp_authorization
from services.snowflake.service import SnowflakeService
from services.transform.source_script_transform import (
    TransformingCursor,
    get_active_source_script,
)
from utils.database_utils import data_transfer_logs, update_table_status_snowflake
from utils.simple_progress_tracker import SimpleProgressTracker
from utils.websocket_utils import (
    trigger_company_connections_status_update,
    trigger_connection_activity_log_update,
    trigger_connection_ws_status_update,
    trigger_ws_status_update,
)
from utils.error_utils import (
    insert_error_log_entry,
    insert_tool_log,
    log_migration_failure,
    log_error_to_tool_log,
    update_migration_status_log,
    update_table_migration_status_log,
    log_migration_completion,
    handle_service_error,
)

from utils.email_templates import build_file_destination_success_email
from utils.email_utils import EmailConfiguration, send_email_with_retry
logger = logging.getLogger(__name__)

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



def _send_sftp_success_notification(
    *,
    request: SnowflakeSFTPMigrationRequest,
    table_name: str,
    table_settings: Dict[str, Any],
    remote_path: str,
    total_rows: int,
    col_count: int,
    completed_at: datetime,
) -> bool:
    started_at = time.perf_counter()
    logger.info("Starting _send_sftp_success_notification | table=%s", table_name)

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
            "Finished _send_sftp_success_notification | table=%s reason=no_recipients elapsed_seconds=%.3f",
            table_name, time.perf_counter() - started_at,
        )
        return False



    config = EmailConfiguration()
    config.recipients = recipients
    if not config.is_configured():
        logger.info(
            "Skipping SFTP success notification for table %s; email config incomplete (missing=%s)",
            table_name, ", ".join(config.get_missing_config()),
        )
        logger.info(
            "Finished _send_sftp_success_notification | table=%s reason=missing_config elapsed_seconds=%.3f",
            table_name, time.perf_counter() - started_at,
        )
        return False

    connection_name = request.connection_name or f"Connection {request.connection_id}"
    company_name    = request.company_name    or f"Company {request.cmp_id}"
    group_names     = [
        str(name or "").strip()
        for name in (table_settings.get("notification_email_group_names") or [])
        if str(name or "").strip()
    ]
    completed_at_text   = completed_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
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
        "[file_destination_email_input] table=%s settings_keys=%s email_custom_fields_keys=%s remote_path=%s",
        table_name,
        sorted(table_settings.keys()) if isinstance(table_settings, dict) else [],
        sorted(email_custom_fields.keys()) if isinstance(email_custom_fields, dict) else [],
        remote_path,
    )

    subject, html_content, plain_text = build_file_destination_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context={
            "company_name":     company_name,
            "service_name":     "Snowflake to SFTP",
            "destination":      "SFTP",
            "rows_exported":    total_rows,
            "columns_exported": col_count,
            "file_path":        remote_path,
            "file_url":         None,          # SFTP has no browser URL
            "path_label":       "SFTP Path",
            "completed_at":     completed_at_text,
            "email_groups":     group_names,
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
        logger.warning("SFTP success notification failed for table %s: %s", table_name, exc)
        return False

    if success:
        logger.info(
            "SFTP success notification sent | table=%s recipients=%s",
            table_name, len(recipients),
        )
    else:
        logger.warning("SFTP success notification not sent | table=%s", table_name)

    logger.info(
        "Finished _send_sftp_success_notification | table=%s success=%s elapsed_seconds=%.3f",
        table_name, success, time.perf_counter() - started_at,
    )
    return success

def _log_stage_error(
    *,
    cmp_id: Optional[int],
    connection_id: Optional[int],
    function_name: str,
    error: Exception,
    extra_message: str = "",
) -> None:
    message = f"{extra_message}: {error}" if extra_message else str(error)
    logger.error("%s failed | %s", function_name, message, exc_info=True)
    try:
        insert_error_log_entry(
            cmp_id=cmp_id,
            connection_id=connection_id,
            message=message,
            function_name=function_name,
        )
    except Exception:
        pass


def _extract_error_message(error: Exception) -> str:
    logger.info("Entering _extract_error_message")
    if isinstance(error, HTTPException):
        detail = getattr(error, "detail", None)
        if detail:
            return str(detail)
    return str(error) or error.__class__.__name__


def _build_sftp_folder_not_writable_error(folder_path: str) -> HTTPException:
    cleaned_folder = str(folder_path or "").strip() or "."
    folder_label = "root folder" if cleaned_folder in {".", "/"} else "target folder"
    return HTTPException(
        status_code=422,
        detail=(
            f"The configured {folder_label} is not writable: {cleaned_folder}. "
            "The SFTP connection succeeded, but the configured user does not have permission "
            "to create files in this folder. Please grant write permission on this folder "
            "or choose a different destination folder."
        ),
    )


def _extract_remote_folder_path(remote_path: str) -> str:
    normalized_path = str(remote_path or "").strip()
    if not normalized_path:
        return "."
    if "/" not in normalized_path:
        return "."
    folder_path = normalized_path.rsplit("/", 1)[0]
    return folder_path or "/"


def _mark_table_failed_with_error(
    *,
    request: SnowflakeSFTPMigrationRequest,
    table_name: str,
    job_name: str,
    error_message: str,
) -> None:
    logger.info("Entering _mark_table_failed_with_error | table=%s", table_name)
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id

    try:
        update_table_status_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
    except Exception as status_err:
        logger.warning("Failed to update table status to failed for %s: %s", table_name, status_err)

    migration_record_id = None
    conn = cursor = None
    try:
        from utils.database_utils import postgres_connection, get_migration_record_id

        conn = postgres_connection()
        if conn is not None:
            cursor = conn.cursor()
            migration_record_id = get_migration_record_id(
                cursor,
                job_name=job_name,
                table_name=table_name,
                connection_id=connection_id,
            )
    except Exception as lookup_err:
        logger.warning("Failed to resolve migration_record_id for table %s: %s", table_name, lookup_err)
    finally:
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            pass
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    try:
        if migration_record_id:
            updated = update_table_migration_status_log(
                migration_record_id=migration_record_id,
                message=error_message,
                status="F",
                session=migration_session_id,
                job_name=job_name,
                connection_id=connection_id,
            )
            if updated:
                return

            insert_tool_log(
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                usr_id=request.user_id,
                table_name=table_name,
                status="F",
                message=error_message,
                session=migration_session_id,
                job_name=job_name,
                migration_record_id=migration_record_id,
            )
            return

        log_error_to_tool_log(
            request.cmp_id,
            connection_id,
            request.user_id,
            table_name,
            error_message,
            migration_session_id,
            job_name=job_name,
        )
    except Exception as log_err:
        logger.warning("Failed to write table failure log for %s: %s", table_name, log_err)


def _mark_unprocessed_tables_failed(
    *,
    request: SnowflakeSFTPMigrationRequest,
    job_name: str,
    error_message: str,
    processed_tables: Optional[List[str]] = None,
) -> None:
    """Mark selected tables failed when a terminal error happens before table workers run."""
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
            logger.warning("Failed to mark unprocessed SFTP table %s as failed: %s", table_name, table_err)


def _q(identifier: str) -> str:
    logger.info("Entering _q")
    return '"' + str(identifier).replace('"', '""') + '"'


def _normalize_remote_dir(path: str) -> str:
    logger.info("Entering _normalize_remote_dir")
    cleaned = str(path or "").strip()
    if not cleaned:
        return ""
    cleaned = cleaned.replace("\\", "/")
    # Keep trailing slash off for joins.
    return cleaned.rstrip("/")


def _join_remote_path(remote_dir: str, filename: str) -> str:
    logger.info("Entering _join_remote_path")
    rd = _normalize_remote_dir(remote_dir)
    name = str(filename or "").lstrip("/")
    if not rd:
        return name
    return f"{rd}/{name}"


def _extract_snowflake_schema(source_config: Dict[str, Any]) -> Optional[str]:
    logger.info("Entering _extract_snowflake_schema")
    schema_val = source_config.get("schema") or source_config.get("snowflake_schema") or source_config.get("schema_")
    schema_str = str(schema_val or "").strip()
    return schema_str or None


def _normalize_selected_fields(raw_selected_fields: Any) -> List[str]:
    logger.info("Entering _normalize_selected_fields")
    if raw_selected_fields is None:
        return []

    if isinstance(raw_selected_fields, str):
        candidates = [part.strip() for part in raw_selected_fields.split(",")]
    elif isinstance(raw_selected_fields, (list, tuple)):
        candidates = [str(part).strip() for part in raw_selected_fields]
    else:
        raise HTTPException(status_code=422, detail="Invalid selected_fields; expected a list of field names")

    normalized_fields = [field for field in candidates if field]
    return normalized_fields


def _map_snowflake_export_error(table_name: str, error: Exception) -> HTTPException:
    message = str(error or "").strip()
    lowered = message.lower()

    if any(marker in lowered for marker in ("does not exist", "object does not exist", "invalid identifier")):
        return HTTPException(
            status_code=422,
            detail=(
                f"Snowflake source table or view '{table_name}' is no longer available. "
                "Please verify the selected table still exists and try again."
            ),
        )

    return HTTPException(
        status_code=422,
        detail=f"Failed to read Snowflake source table '{table_name}': {message or error.__class__.__name__}",
    )


def _build_snowflake_select_query(
    table_name: str,
    selected_fields: List[str],
    sort_columns: Optional[List[dict]] = None,
) -> str:
    logger.info("Entering _build_snowflake_select_query | table=%s | selected_fields=%s", table_name, len(selected_fields))
    if "." in table_name:
        parts = [p for p in table_name.split(".") if p]
        if len(parts) == 2:
            from_schema, from_table = parts
            from_clause = f"{_q(from_schema)}.{_q(from_table)}"
        elif len(parts) == 3:
            db, from_schema, from_table = parts
            from_clause = f"{_q(db)}.{_q(from_schema)}.{_q(from_table)}"
        else:
            from_clause = _q(table_name)
    else:
        from_clause = _q(table_name)

    if selected_fields:
        select_list = ", ".join(_q(field) for field in selected_fields)
    else:
        select_list = "*"

    query = f"SELECT {select_list} FROM {from_clause}"
    if sort_columns:
        order_parts = []
        for sort in sort_columns:
            if isinstance(sort, str):
                column_name, direction = sort, "asc"
            else:
                column_name = sort.get("column_name") or sort.get("column") or sort.get("field")
                direction = sort.get("direction") or sort.get("order") or "asc"
            column_name = str(column_name or "").strip()
            direction = str(direction or "asc").strip().lower()
            if not column_name:
                raise HTTPException(status_code=422, detail="sort_columns entries require column_name")
            if direction not in {"asc", "desc"}:
                raise HTTPException(status_code=422, detail="sort_columns direction must be asc or desc")
            order_parts.append(f"{_q(column_name)} {direction.upper()}")
        if order_parts:
            query += " ORDER BY " + ", ".join(order_parts)
    return query

def _ensure_sftp_dep() -> Any:
    logger.info("Entering _ensure_sftp_dep")
    try:
        import paramiko
    except Exception:
        raise HTTPException(status_code=500, detail="SFTP dependency missing: paramiko is not installed")
    return paramiko


def _load_paramiko_pkey(paramiko, private_key_pem: str, passphrase: Optional[str]):
    logger.info("Entering _load_paramiko_pkey")
    # Reuse the same key parsing strategy as in services/sftp_service.py
    key_stream = io.StringIO(private_key_pem or "")
    password = passphrase if passphrase not in ("", None) else None

    key_loaders = (
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        getattr(paramiko, "Ed25519Key", None),
        paramiko.DSSKey,
    )
    last_error = None
    for key_cls in key_loaders:
        if key_cls is None:
            continue
        try:
            key_stream.seek(0)
            return key_cls.from_private_key(key_stream, password=password)
        except Exception as e:
            last_error = e

    raise HTTPException(status_code=422, detail=f"Invalid sftp_private_key; failed to parse key ({last_error})")


def _open_sftp_client(dest_config: Dict[str, Any]):
    logger.info("Entering _open_sftp_client")
    """
    Open an SFTP client from destination_config fields.

    Returns (sftp, transport, sock) for cleanup.
    """
    paramiko = _ensure_sftp_dep()

    host = str(dest_config.get("sftp_host") or dest_config.get("host") or "").strip()
    port = dest_config.get("sftp_port") if dest_config.get("sftp_port") is not None else dest_config.get("port")
    username = str(dest_config.get("sftp_username") or dest_config.get("username") or "").strip()
    auth_type = str(dest_config.get("authentication_type") or "password").strip().lower()

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 22
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid sftp_port; must be an integer")

    connect_timeout = 10
    sock = None
    transport = None
    sftp = None
    try:
        sock = socket.create_connection((host, port_int), timeout=connect_timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = connect_timeout
        transport.auth_timeout = connect_timeout

        if auth_type == "password":
            password = dest_config.get("sftp_password") or dest_config.get("password")
            transport.connect(username=username, password=password)
        else:
            private_key = dest_config.get("sftp_private_key") or dest_config.get("private_key")
            passphrase = dest_config.get("sftp_passphrase") or dest_config.get("passphrase")
            pkey = _load_paramiko_pkey(paramiko, str(private_key or ""), str(passphrase or "") if passphrase is not None else None)
            transport.connect(username=username, pkey=pkey)

        sftp = paramiko.SFTPClient.from_transport(transport)
        return sftp, transport, sock
    except HTTPException:
        raise
    except Exception as e:
        _log_stage_error(
            cmp_id=dest_config.get("cmp_id"),
            connection_id=dest_config.get("connection_id"),
            function_name="_open_sftp_client",
            error=e,
            extra_message=f"host={host} port={port_int} username={username}",
        )
        msg = str(e or "")
        msg_low = msg.lower()
        if "authentication failed" in msg_low or "auth fail" in msg_low:
            raise HTTPException(status_code=422, detail="SFTP authentication failed. Please verify credentials.")
        if "timed out" in msg_low or "timeout" in msg_low:
            raise HTTPException(status_code=422, detail="SFTP connection timed out. Please verify host/port/network.")
        raise HTTPException(status_code=422, detail="Failed to connect to SFTP. Please verify connection details.") from e


def _ensure_remote_dir(sftp: Any, remote_dir: str, home_dir: str = ".") -> str:
    logger.info("Entering _ensure_remote_dir")
    """
    POSIX-like remote paths; create iteratively.
    Always starts by resetting to a known base (root or current home) to ensure
    consistent behavior across multiple calls using the same SFTP client.
    """
    path = (remote_dir or "").strip()
    if not path or path == "/":
        return "/" if path == "/" else (home_dir or ".")

    parts = [p for p in path.split("/") if p]
    
    # 1. Determine starting point and reset SFTP client cursor
    if path.startswith("/"):
        current = "/"
    else:
        current = home_dir or "."

    try:
        sftp.chdir(current)
    except Exception as e:
        _log_stage_error(
            cmp_id=None,
            connection_id=None,
            function_name="_ensure_remote_dir",
            error=e,
            extra_message=f"base_path={current}",
        )
        logger.warning("SFTP failed to chdir to base path=%s error=%s", current, e)
        raise HTTPException(status_code=422, detail=f"Base folder not accessible: {current}") from e

    # 2. Iterate and create missing parts using absolute paths for robustness
    for part in parts:
        current = f"{current.rstrip('/')}/{part}"
        try:
            sftp.chdir(current)
        except Exception:
            try:
                sftp.mkdir(current)
                sftp.chdir(current)
            except Exception as e:
                # A parallel worker may have created the folder after our first
                # chdir failed but before mkdir ran. Retry chdir once to absorb
                # that race instead of failing the table.
                try:
                    sftp.chdir(current)
                    continue
                except Exception:
                    _log_stage_error(
                        cmp_id=None,
                        connection_id=None,
                        function_name="_ensure_remote_dir",
                        error=e,
                        extra_message=f"path={current}",
                    )
                    logger.warning("SFTP mkdir/chdir failed for path=%s error=%s", current, e)
                    raise HTTPException(status_code=422, detail=f"Remote folder not accessible: {path}") from e
    return current


def _require_existing_remote_dir(sftp: Any, remote_dir: str, home_dir: str = ".") -> str:
    logger.info("Entering _require_existing_remote_dir")
    path = (remote_dir or "").strip()
    if not path or path == "/":
        return "/" if path == "/" else (home_dir or ".")

    parts = [p for p in path.split("/") if p]
    current = "/" if path.startswith("/") else (home_dir or ".")

    try:
        sftp.chdir(current)
    except Exception as e:
        _log_stage_error(
            cmp_id=None,
            connection_id=None,
            function_name="_require_existing_remote_dir",
            error=e,
            extra_message=f"base_path={current}",
        )
        raise HTTPException(
            status_code=422,
            detail=f"Configured root folder is not accessible on SFTP: {current}",
        ) from e

    for part in parts:
        current = f"{current.rstrip('/')}/{part}"
        try:
            sftp.chdir(current)
        except Exception as e:
            _log_stage_error(
                cmp_id=None,
                connection_id=None,
                function_name="_require_existing_remote_dir",
                error=e,
                extra_message=f"path={current}",
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Configured root folder does not exist on SFTP: {path}. "
                    "Please create the folder or update the connection to use an existing folder."
                ),
            ) from e

    return current


def _ensure_remote_subdir(sftp: Any, root_dir: str, sub_dir: str, home_dir: str = ".") -> str:
    logger.info("Entering _ensure_remote_subdir")
    """
    Ensure root_dir and sub_dir both exist.
    We join them into one path and use _ensure_remote_dir for recursive creation.
    """
    root = _normalize_remote_dir(root_dir)
    if not root:
        raise HTTPException(status_code=422, detail="Missing required field: root_folder")

    existing_root = _require_existing_remote_dir(sftp, root, home_dir=home_dir)
    target = _normalize_remote_dir(sub_dir)
    if not target:
        return existing_root

    full_path = f"{root}/{target}"
    try:
        abs_path = _ensure_remote_dir(sftp, full_path, home_dir=home_dir)
        return abs_path
    except Exception as e:
        _log_stage_error(
            cmp_id=None,
            connection_id=None,
            function_name="_ensure_remote_subdir",
            error=e,
            extra_message=f"full_path={full_path}",
        )
        logger.warning("SFTP folder access/creation failed path=%s error=%s", full_path, e)
        # Preserve specific error details for debugging
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=422, detail=f"Target folder not accessible: {full_path}") from e


def _upload_file(sftp, local_path: str, remote_path: str) -> int:
    logger.info("Entering _upload_file")
    # Returns bytes uploaded.
    file_size = os.path.getsize(local_path)
    logger.info(
        "Starting SFTP upload | local_path=%s remote_path=%s file_size_bytes=%s",
        local_path,
        remote_path,
        file_size,
    )
    try:
        with open(local_path, "rb") as f:
            sftp.putfo(f, remote_path)
    except Exception as e:
        logger.warning(
            "SFTP upload failed | local_path=%s remote_path=%s error_type=%s error=%s",
            local_path,
            remote_path,
            type(e).__name__,
            str(e),
        )
        error_message = str(e or "").lower().strip()
        if getattr(e, "errno", None) == 13 or "permission denied" in error_message or "errno 13" in error_message:
            raise _build_sftp_folder_not_writable_error(_extract_remote_folder_path(remote_path)) from e
        raise
    logger.info(
        "Completed SFTP upload | local_path=%s remote_path=%s file_size_bytes=%s",
        local_path,
        remote_path,
        file_size,
    )
    return file_size


def _trigger_ws_all(*, migration_session_id: int, connection_id: int, cmp_id: int) -> None:
    logger.info("Entering _trigger_ws_all")
    try:
        trigger_ws_status_update(migration_session_id)
    except Exception as e:
        logger.warning("WS trigger failed for migration session %s: %s", migration_session_id, e)
    try:
        trigger_connection_ws_status_update(connection_id)
    except Exception as e:
        logger.warning("WS trigger failed for connection %s: %s", connection_id, e)
    try:
        trigger_connection_activity_log_update(connection_id)
    except Exception as e:
        logger.warning("WS activity-log trigger failed for connection %s: %s", connection_id, e)
    try:
        trigger_company_connections_status_update(cmp_id)
    except Exception as e:
        logger.warning("WS company trigger failed for cmp_id %s: %s", cmp_id, e)


def _process_snowflake_to_sftp_table(
    *,
    request: SnowflakeSFTPMigrationRequest,
    table_name: str,
    source_config: Dict[str, Any],
    dest_config: Dict[str, Any],
    root_folder_path: str,
    file_format: str,
    default_delimiter: str,
    default_quotechar: str,
    escapechar: Optional[str],
    null_sequence: str,
    fetch_size: int,
    table_export_settings: Dict[str, Any],
    job_name: str,
    excel_semaphore: Optional[threading.Semaphore] = None,  # ← added
    failure_tracker: Optional[MigrationFailureTracker] = None,
) -> Dict[str, Any]:
    logger.info("Entering _process_snowflake_to_sftp_table | table=%s", table_name)
    started_at = time.perf_counter()
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    table_started = datetime.now(timezone.utc)
    sftp = transport = sock = None
    sf_conn = None

    result = {
        "table_name": table_name,
        "status": "failed",
        "rows": 0,
        "columns": 0,
        "remote_path": None,
        "bytes_uploaded": 0,
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

    try:
        try:
            update_table_status_snowflake(connection_id, table_name, "in_progress", job_name, migration_session_id)
        except Exception:
            pass
        try:
            SimpleProgressTracker.reset_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
            )
        except Exception as progress_reset_err:
            logger.warning("Failed to reset live staging count for SFTP table %s: %s", table_name, progress_reset_err)

        sftp, transport, sock = _open_sftp_client(dest_config)
        try:
            home_dir = sftp.normalize(".")
        except Exception:
            home_dir = "."

        sf_service = SnowflakeService(source_config, destination_schema=None, validate_destination_schema=False)
        sf_conn = sf_service.create_connection()
        schema_name = _extract_snowflake_schema(source_config)
        if schema_name:
            with sf_conn.cursor() as schema_cur:
                schema_cur.execute(f"USE SCHEMA {schema_name}")

        table_settings = lookup_table_export_settings(table_export_settings, table_name)
        if not isinstance(table_settings, dict):
            table_settings = {}
        ts = build_utc_filename_suffix(table_settings.get("filename_date_format"))

        selected_fields = _normalize_selected_fields(table_settings.get("selected_fields"))
        sort_columns = table_settings.get("sort_columns") or (table_settings.get("excel_options") or {}).get("sort_columns")

        table_file_format = str(table_settings.get("file_format") or file_format or "csv").strip().lower()
        if table_file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        # Excel Preflight Limit Guards
        if table_file_format == "excel":
            preflight_query = _build_snowflake_select_query(table_name, selected_fields,sort_columns)
            check_excel_limits_preflight(sf_conn, preflight_query, table_name)

        with sf_conn.cursor() as cur:
            query = _build_snowflake_select_query(table_name, selected_fields,sort_columns)
            try:
                cur.execute(query)
            except Exception as snowflake_read_err:
                raise _map_snowflake_export_error(table_name, snowflake_read_err) from snowflake_read_err

            # Apply the active generic source script before the shared file
            # writers. The no-script path still uses the native Snowflake
            # cursor exactly as before.
            active_transform = get_active_source_script(connection_id, table_name)
            script_text = (active_transform or {}).get("script_text")
            if script_text and str(script_text).strip():
                cur = TransformingCursor(
                    cur,
                    fetch_size=fetch_size,
                    script_text=script_text,
                    entry_point=(active_transform or {}).get("entry_point") or "",
                )

            table_target_folder = resolve_relative_target_folder(
                root_folder_path,
                table_settings.get("target_folder"),
            )
            folder_path = _ensure_remote_subdir(sftp, root_folder_path, table_target_folder, home_dir=home_dir)

            base_name = str(table_settings.get("output_file_name") or "").strip() or table_name
            add_ts = table_settings.get("add_utc_timestamp")
            add_ts = True if add_ts is None else bool(add_ts)

            ext = {"csv": "csv", "json": "json", "parquet": "parquet", "excel": "xlsx"}[table_file_format]
            filename = f"{base_name}_{ts}.{ext}" if add_ts else f"{base_name}.{ext}"
            remote_path = _join_remote_path(folder_path, filename)

            with tempfile.TemporaryDirectory(prefix="sf_sftp_") as tmpdir:
                local_path = os.path.join(tmpdir, filename)
                progress_label = f"SFTP table={table_name} file={filename}"

                def _progress_callback(_rows_in_chunk: int, total_rows_written: int) -> None:
                    try:
                        SimpleProgressTracker.set_staging_count(
                            connection_id=connection_id,
                            migration_session_id=migration_session_id,
                            job_name=job_name,
                            table_name=table_name,
                            count_value=total_rows_written,
                        )
                    except Exception as progress_err:
                        logger.warning(
                            "Failed to update live staging count for SFTP table %s at total_rows=%s: %s",
                            table_name, total_rows_written, progress_err,
                        )

                logger.info(
                    "Starting Snowflake export for SFTP | table=%s fetch_size=%s format=%s local_path=%s remote_path=%s",
                    table_name, fetch_size, table_file_format, local_path, remote_path,
                )

                # ── write local file ──────────────────────────────────────
                write_started_at = time.perf_counter()

                if table_file_format == "csv":
                    delimiter = _decode_csv_char(table_settings.get("csv_delimiter") or default_delimiter) or ","
                    quotechar = _decode_csv_char(table_settings.get("csv_quote_char") or default_quotechar) or '"'
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
                        null_sequence=null_sequence if null_sequence != "" else None,
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
                            "_process_snowflake_to_sftp_table waiting for excel semaphore | table=%s",
                            table_name,
                        )
                        with excel_semaphore:
                            logger.info(
                                "_process_snowflake_to_sftp_table acquired excel semaphore | table=%s",
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

                # ── file size diagnostics ─────────────────────────────────
                file_size_bytes = os.path.getsize(local_path) if os.path.exists(local_path) else 0
                file_size_mb    = file_size_bytes / (1024 * 1024)
                logger.info(
                    "_process_snowflake_to_sftp_table file written | "
                    "table=%s format=%s rows=%s cols=%s size_mb=%.2f write_elapsed=%.3fs",
                    table_name, table_file_format, total_rows, col_count, file_size_mb, write_elapsed,
                )

                if not os.path.exists(local_path):
                    raise RuntimeError(
                        f"Export file was not created for table {table_name!r}; "
                        "the file writer may have failed silently."
                    )

                # ── upload to SFTP ────────────────────────────────────────
                upload_started_at = time.perf_counter()
                bytes_uploaded = _upload_file(sftp, local_path, remote_path)
                upload_elapsed = time.perf_counter() - upload_started_at

                logger.info(
                    "_process_snowflake_to_sftp_table upload complete | "
                    "table=%s size_mb=%.2f upload_elapsed=%.3fs throughput_mbps=%.2f",
                    table_name, file_size_mb, upload_elapsed,
                    (file_size_mb / max(upload_elapsed, 0.001)),
                )

        # ── success ───────────────────────────────────────────────────────
        table_completed_at = datetime.now(timezone.utc)
        result.update({
            "status": "success",
            "rows": total_rows,
            "columns": col_count,
            "remote_path": remote_path,
            "bytes_uploaded": bytes_uploaded,
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
                function_name="_process_snowflake_to_sftp_table.logging",
                error=log_err,
                extra_message=f"table={table_name}",
            )

        # ── success notification (non-fatal, outside logging try/except) ──
        try:
            _send_sftp_success_notification(
                request=request,
                table_name=table_name,
                table_settings=table_settings,
                remote_path=remote_path,
                total_rows=total_rows,
                col_count=col_count,
                completed_at=table_completed_at,
            )
        except Exception as notif_err:
            logger.warning(
                "SFTP success notification failed (non-fatal) | table=%s error=%s",
                table_name, notif_err,
            )

        logger.info(
            "_process_snowflake_to_sftp_table success | table=%s rows=%s bytes=%s elapsed=%.3fs",
            table_name, total_rows, bytes_uploaded, time.perf_counter() - started_at,
        )
        return result

    except HTTPException as e:
        result["error"] = _extract_error_message(e)
        if failure_tracker is not None:
            failure_tracker.add_failure(table_name, result["error"], "Snowflake to SFTP")
        _mark_table_failed_with_error(
            request=request, table_name=table_name,
            job_name=job_name, error_message=result["error"],
        )
        _log_stage_error(
            cmp_id=request.cmp_id, connection_id=connection_id,
            function_name="_process_snowflake_to_sftp_table.http",
            error=e, extra_message=f"table={table_name}",
        )
        return result
    except Exception as e:
        result["error"] = _extract_error_message(e)
        if failure_tracker is not None:
            failure_tracker.add_failure(table_name, result["error"], "Snowflake to SFTP")
        _mark_table_failed_with_error(
            request=request, table_name=table_name,
            job_name=job_name, error_message=result["error"],
        )
        _log_stage_error(
            cmp_id=request.cmp_id, connection_id=connection_id,
            function_name="_process_snowflake_to_sftp_table",
            error=e, extra_message=f"table={table_name}",
        )
        return result
    finally:
        for resource, name in ((sftp, "sftp"), (transport, "transport"), (sock, "sock"), (sf_conn, "sf_conn")):
            try:
                if resource is not None:
                    resource.close()
            except Exception as close_err:
                logger.warning(
                    "_process_snowflake_to_sftp_table cleanup failed | resource=%s table=%s error=%s",
                    name, table_name, close_err,
                )

def migrate_snowflake_to_sftp(request: SnowflakeSFTPMigrationRequest) -> Dict[str, Any]:
    logger.info("Entering migrate_snowflake_to_sftp | connection_id=%s | tables=%s", request.connection_id, len(request.selected_tables or []))
    """
    Snowflake -> SFTP migration implementation.

    This function:
    1) Validates SFTP config
    2) Connects to Snowflake and SFTP
    3) Exports each selected table/view to file (csv/json/parquet)
    4) Uploads exported file to SFTP folder_path
    5) Updates table status + logs + websocket pushes for UI
    """
    started_at = datetime.now(timezone.utc)
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    job_name = request.job_name or f"Snowflake-SFTP Migration {connection_id}"
    is_final_attempt = True
    failure_tracker = MigrationFailureTracker()
    results = {
        "status": "success",
        "status_code": 200,
        "job_name": job_name,
        "connection_id": connection_id,
        "migration_session_id": migration_session_id,
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "tables": {},
        "total_tables": len(request.selected_tables or []),
        "successful_tables": 0,
        "failed_tables": 0,
        "total_rows": 0,
        "service_name": "Snowflake-SFTP",
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

        # Log migration start
        update_migration_status_log(
            job_name=job_name,
            message="Migration in progress",
            status="I",
            session=migration_session_id
        )

        source_config = request.source_config or {}
        dest_config = dict(request.destination_config or {})
        dest_config.setdefault("cmp_id", request.cmp_id)
        dest_config.setdefault("connection_id", request.connection_id)

        # Validate destination config fields using existing SFTP validation rules.
        validate_sftp_authorization(SFTPAuthorizationRequest(destination_config=dest_config))
        test_sftp_connection(SFTPAuthorizationRequest(destination_config=dest_config))

        root_folder = str(dest_config.get("root_folder") or dest_config.get("folder_path") or "").strip()
        if not root_folder:
            raise HTTPException(status_code=422, detail="Missing required field: root_folder")

        root_folder_path = _normalize_remote_dir(root_folder)

        # Snowflake->SFTP currently exports CSV by default (no file_format field in UI).
        file_format = str(dest_config.get("file_format") or "csv").strip().lower()
        if file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        # Defaults when table-level settings are missing.
        default_delimiter = _decode_csv_char(dest_config.get("csv_delimiter") or ",")
        default_quotechar = _decode_csv_char(dest_config.get("csv_quote_char") or dest_config.get("csv_quotechar") or '"')

        escapechar = None
        null_sequence = dest_config.get("null_sequence")
        null_sequence = "" if null_sequence is None else str(null_sequence)

        fetch_size = int(request.chunk_count) if request.chunk_count else 5000
        if fetch_size <= 0:
            fetch_size = 5000
        logger.info(
            "Snowflake->SFTP fetch size resolved | connection_id=%s requested_chunk_count=%s effective_fetch_size=%s",
            connection_id,
            request.chunk_count,
            fetch_size,
        )

        table_export_settings = request.table_export_settings or {}
        selected_tables = [str(table_name or "").strip() for table_name in (request.selected_tables or []) if str(table_name or "").strip()]
        execution_order = str(request.execution_order or "sequential").strip().lower()
        logger.info(
            "Snowflake->SFTP execution mode=%s | selected_tables=%s",
            execution_order,
            len(selected_tables),
        )

        table_results: List[Dict[str, Any]] = []
        if execution_order == "parallel" and len(selected_tables) > 1:
            # Count how many tables will write Excel (using job-level configuration or per-table overrides)
            excel_table_count = sum(
                1 for t in selected_tables
                if str(
                    (table_export_settings.get(t) or {}).get("file_format")
                    or file_format or "csv"
                ).strip().lower() == "excel"
            )
            
            # Excel writing is CPU and memory heavy. To prevent OOM memory issues,
            # we serialize Excel writing processes using a Semaphore with a limit of 1
            # so that no two heavy Excel files write at the exact same time.
            excel_semaphore = threading.Semaphore(1) if excel_table_count > 0 else None
            
            # Limit the concurrent database extraction threads to at most 2 to avoid
            # resource exhaustion in the process pool.
            max_workers = min(2, len(selected_tables))
            logger.info(
                "Executing Snowflake->SFTP in parallel mode | max_workers=%s | excel_tables=%s | serialization=True",
                max_workers, excel_table_count
            )
            parallel_failures: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _process_snowflake_to_sftp_table,
                        request=request,
                        table_name=table_name,
                        source_config=source_config,
                        dest_config=dest_config,
                        root_folder_path=root_folder_path,
                        file_format=file_format,
                        default_delimiter=default_delimiter,
                        default_quotechar=default_quotechar,
                        escapechar=escapechar,
                        null_sequence=null_sequence,
                        fetch_size=fetch_size,
                        table_export_settings=table_export_settings,
                        job_name=job_name,
                        excel_semaphore=excel_semaphore,
                        failure_tracker=failure_tracker,
                    ): table_name
                    for table_name in selected_tables
                }
                for future in as_completed(futures):
                    table_name = futures[future]
                    try:
                        table_results.append(future.result())
                    except Exception as table_err:
                        error_message = _extract_error_message(table_err)
                        logger.exception(
                            "Parallel SFTP table export failed | table=%s connection_id=%s error=%s",
                            table_name,
                            connection_id,
                            error_message,
                        )
                        parallel_failures.append(
                            {
                                "table_name": table_name,
                                "status": "failed",
                                "rows": 0,
                                "columns": 0,
                                "remote_path": None,
                                "bytes_uploaded": 0,
                                "error": error_message,
                            }
                        )
                        try:
                            _mark_table_failed_with_error(
                                request=request,
                                table_name=table_name,
                                job_name=job_name,
                                error_message=error_message,
                            )
                        except Exception as mark_err:
                            logger.warning(
                                "Failed to mark SFTP table %s as failed after parallel error: %s",
                                table_name,
                                mark_err,
                            )
                        failure_tracker.add_failure(table_name, error_message, "Snowflake to SFTP")
                        table_results.append(parallel_failures[-1])
        else:
            logger.info("Executing Snowflake->SFTP in sequential mode")
            for table_name in selected_tables:
                table_results.append(
                    _process_snowflake_to_sftp_table(
                        request=request,
                        table_name=table_name,
                        source_config=source_config,
                        dest_config=dest_config,
                        root_folder_path=root_folder_path,
                        file_format=file_format,
                        default_delimiter=default_delimiter,
                        default_quotechar=default_quotechar,
                        escapechar=escapechar,
                        null_sequence=null_sequence,
                        fetch_size=fetch_size,
                        table_export_settings=table_export_settings,
                        job_name=job_name,
                        failure_tracker=failure_tracker,
                    )
                )

        for table_result in table_results:
            tbl = table_result["table_name"]
            results["tables"][tbl] = {
                "status": table_result["status"],
                "rows": table_result["rows"],
                "columns": table_result["columns"],
                "remote_path": table_result["remote_path"],
                "bytes_uploaded": table_result["bytes_uploaded"],
                "error": table_result["error"],
            }
            if table_result["status"] == "success":
                results["total_rows"] += table_result["rows"]
                results["successful_tables"] += 1
            else:
                results["failed_tables"] += 1

            _trigger_ws_all(
                migration_session_id=migration_session_id,
                connection_id=connection_id,
                cmp_id=request.cmp_id,
            )

        if results["failed_tables"] == 0:
            results["status"] = "success"
        elif results["successful_tables"] == 0:
            results["status"] = "failed"
        else:
            results["status"] = "partial"

        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["status_code"] = 200 if results["failed_tables"] == 0 else 206

        # Final migration completion log
        execution_time = time.time() - started_at.timestamp()
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

        # Send consolidated error email if there were any failures
        failure_tracker.send_consolidated_error_email(
            connection_name=request.connection_name or f"SFTP {connection_id}",
            service_name="Snowflake to SFTP",
            user_id=request.user_id,
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            company_name=request.company_name or "Unknown Company",
            is_final_attempt=is_final_attempt,
        )

        return results
    except Exception as e:
        error_message = _extract_error_message(e)
        
        # Log overall migration setup/connection error using centralized error handling
        error_context = {
            "table_name": "Migration Process",
            "service_name": "Snowflake to SFTP",
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
            "execution_time": time.time() - started_at.timestamp(),
            "total_rows": results.get("total_rows", 0),
            "successful_tables": results.get("successful_tables", 0)
        }
        
        try:
            handle_service_error(
                error=e,
                context=error_context,
                log_files=None,
                send_email=True,
                is_final_attempt=is_final_attempt,
            )
        except Exception as handler_err:
            logger.warning(f"⚠️ handle_service_error failed in outer exception block: {handler_err}")

        _mark_unprocessed_tables_failed(
            request=request,
            job_name=job_name,
            error_message=error_message,
            processed_tables=list(results.get("tables", {}).keys()),
        )
        _log_stage_error(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            function_name="migrate_snowflake_to_sftp",
            error=e,
            extra_message=f"job_name={job_name}",
        )
        try:
            log_migration_failure(
                job_name=job_name,
                error_message=error_message,
                session=migration_session_id,
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                user_id=request.user_id,
                execution_time=time.time() - started_at.timestamp(),
                total_rows=results.get("total_rows", 0),
                successful_tables=results.get("successful_tables", 0),
                total_tables=results.get("total_tables", 0),
            )
        except Exception as failure_log_err:
            logger.warning("Failed to write migration failure log for %s: %s", job_name, failure_log_err)
        raise
