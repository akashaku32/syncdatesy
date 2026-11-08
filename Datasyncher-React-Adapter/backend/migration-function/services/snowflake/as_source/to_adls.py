from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import logging
import os
import re
import socket
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.snowflake_adls_models import ADLSAuthorizationRequest, SnowflakeADLSMigrationRequest
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
from services.snowflake.service import SnowflakeService
from services.transform.source_script_transform import (
    TransformingCursor,
    get_active_source_script,
)
from services.snowflake.as_source.to_sftp import (
    MigrationFailureTracker,
    _build_snowflake_select_query,
    _extract_error_message,
    _extract_snowflake_schema,
    _log_stage_error,
    _map_snowflake_export_error,
    _mark_table_failed_with_error,
    _normalize_selected_fields,
)
from utils.database_utils import data_transfer_logs, update_table_status_snowflake
from utils.email_templates import build_file_destination_success_email
from utils.email_utils import EmailConfiguration, send_email_with_retry
from utils.error_utils import log_migration_completion, log_migration_failure, update_migration_status_log
from utils.simple_progress_tracker import SimpleProgressTracker

logger = logging.getLogger(__name__)


INVALID_ADLS_CLIENT_SECRET_MESSAGE = (
    "ADLS client_secret is invalid. Use the client secret Value, not the Secret ID."
)


def _get_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


ADLS_CLIENT_KWARGS = {
    "retry_total": _get_positive_int_env("ADLS_RETRY_TOTAL", 5),
    "retry_connect": _get_positive_int_env("ADLS_RETRY_CONNECT", 3),
    "retry_read": _get_positive_int_env("ADLS_RETRY_READ", 5),
    "retry_status": _get_positive_int_env("ADLS_RETRY_STATUS", 5),
    "connection_timeout": _get_positive_int_env("ADLS_CONNECTION_TIMEOUT", 30),
    "read_timeout": _get_positive_int_env("ADLS_READ_TIMEOUT", 300),
}
ADLS_UPLOAD_TIMEOUT = _get_positive_int_env("ADLS_UPLOAD_TIMEOUT", 600)
ADLS_UPLOAD_MAX_CONCURRENCY = _get_positive_int_env("ADLS_UPLOAD_MAX_CONCURRENCY", 2)
ADLS_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024


def _send_adls_success_notification(
    *,
    request: SnowflakeADLSMigrationRequest,
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
        logger.info(
            "Finished _send_adls_success_notification | table=%s reason=no_recipients",
            table_name,
        )
        return False

    config = EmailConfiguration()
    config.recipients = recipients
    if not config.is_configured():
        logger.info(
            "Skipping ADLS success notification for table %s; email config incomplete (missing=%s)",
            table_name,
            ", ".join(config.get_missing_config()),
        )
        return False

    connection_name = request.connection_name or f"Connection {request.connection_id}"
    company_name = request.company_name or f"Company {request.cmp_id}"
    group_names = [
        str(name or "").strip()
        for name in (table_settings.get("notification_email_group_names") or [])
        if str(name or "").strip()
    ]
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
        "[file_destination_email_input] table=%s settings_keys=%s email_custom_fields_keys=%s adls_path=%s",
        table_name,
        sorted(table_settings.keys()) if isinstance(table_settings, dict) else [],
        sorted(email_custom_fields.keys()) if isinstance(email_custom_fields, dict) else [],
        remote_path,
    )
    subject, html_content, plain_text = build_file_destination_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context={
            "company_name": company_name,
            "service_name": "Snowflake to Azure Data Lake Storage",
            "destination": "Azure Data Lake Storage",
            "rows_exported": total_rows,
            "columns_exported": col_count,
            "file_path": remote_path,
            "file_url": None,
            "path_label": "ADLS Path",
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
        logger.info("ADLS success notification failed for table %s: %s", table_name, exc)
        return False

    if success:
        logger.info(
            "ADLS success notification sent | table=%s recipients=%s",
            table_name,
            len(recipients),
        )
    else:
        logger.warning("ADLS success notification not sent | table=%s", table_name)
    return success

def _adls_error_detail(message: str, *, account_name: str, container_name: str, path: Optional[str] = None) -> str:
    lowered = str(message or "").lower()

    if "aadsts700016" in lowered or "application with identifier" in lowered or "application was not found" in lowered:
        return "ADLS client_id is invalid or not found."
    if (
        "aadsts90002" in lowered
        or (
            "tenant" in lowered
            and "not found" in lowered
            and "application" not in lowered
            and "client" not in lowered
        )
    ):
        return "ADLS tenant_id is invalid or not found."
    if "aadsts7000215" in lowered or "invalid client secret provided" in lowered:
        return INVALID_ADLS_CLIENT_SECRET_MESSAGE
    if "aadsts7000222" in lowered or ("secret" in lowered and "expired" in lowered):
        return "ADLS client_secret is expired. Create a new client secret."
    if "aadsts7000112" in lowered or "application is disabled" in lowered:
        return "ADLS app registration is disabled."
    if "invalid_resource" in lowered or "invalid audience" in lowered:
        return "ADLS Azure authentication target is invalid."

    if (
        "authorizationpermissionmismatch" in lowered
        or "authorizationfailure" in lowered
        or "authorization failed" in lowered
        or "permission" in lowered
        or "forbidden" in lowered
        or "status code: 403" in lowered
    ):
        return (
            f"ADLS cannot access container: {container_name}. Check container name, Storage Blob Data Contributor role, and firewall rules."
        )

    if "filesystemnotfound" in lowered or "containernotfound" in lowered:
        return f"ADLS container not found: {container_name}."
    if "invalidresourcename" in lowered or "invalid resource name" in lowered:
        return "ADLS container_name is invalid."
    if "accountisdisabled" in lowered:
        return f"ADLS storage account is disabled: {account_name}."
    if "resourcenotfound" in lowered or "the specified resource does not exist" in lowered:
        return f"ADLS storage account or container not found: {account_name}/{container_name}."
    if (
        "name or service not known" in lowered
        or "nodename nor servname provided" in lowered
        or "getaddrinfo failed" in lowered
        or "failed to establish a new connection" in lowered
        or "temporarily failure in name resolution" in lowered
    ):
        return f"ADLS storage account not found or unreachable: {account_name}."
    if "connection timed out" in lowered or "timed out" in lowered:
        return (
            f"ADLS storage account timed out: {account_name}. Check firewall/network rules."
        )
    if "hierarchical namespace" in lowered or "hns" in lowered or "blob endpoint" in lowered:
        return "ADLS Gen2 hierarchical namespace is not enabled."

    target = f"abfss://{container_name}@{account_name}.dfs.core.windows.net"
    if path:
        target = f"{target}/{path}"
    return f"ADLS operation failed for {target}: {message}"


def _collect_exception_message(error: Exception) -> str:
    messages = []
    seen = set()

    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for value in (
            getattr(current, "message", None),
            getattr(current, "error", None),
            str(current),
        ):
            text = str(value or "").strip()
            if text and text not in messages:
                messages.append(text)
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    return " | ".join(messages)


def _validate_adls_storage_account_dns(account_name: str) -> None:
    host = f"{account_name}.dfs.core.windows.net"
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=422,
            detail=f"ADLS storage account not found or unreachable: {account_name}.",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"ADLS storage account not found or unreachable: {account_name}.",
        ) from exc


def _extract_config(request: ADLSAuthorizationRequest) -> dict:
    config = request.resolved_config()
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Invalid ADLS config; expected object.")
    return config


def _require_non_empty(config: dict, field_name: str, *aliases: str) -> str:
    for key in (field_name, *aliases):
        value = str(config.get(key) or "").strip()
        if value:
            return value
    raise HTTPException(status_code=422, detail=f"Missing required field: {field_name}")


def _redact_config(config: dict) -> dict:
    redacted = dict(config)
    for key in ("client_secret", "secret", "access_token"):
        if key in redacted:
            redacted[key] = "***REDACTED***"
    return redacted


def validate_adls_authorization(request: ADLSAuthorizationRequest) -> None:
    config = _extract_config(request)

    account_name = _require_non_empty(config, "account_name", "storage_account_name")
    container_name = _require_non_empty(config, "container_name", "file_system", "filesystem")
    _require_non_empty(config, "tenant_id")
    _require_non_empty(config, "client_id")
    _require_non_empty(config, "client_secret")

    if not re.match(r"^[a-z0-9]{3,24}$", account_name):
        raise HTTPException(status_code=422, detail="Invalid account_name; use the storage account name only, 3-24 lowercase letters/numbers.")
    if "." in account_name or "/" in account_name or "\\" in account_name:
        raise HTTPException(status_code=422, detail="Invalid account_name; use the storage account name only.")
    if not re.match(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$", container_name) or "--" in container_name:
        raise HTTPException(
            status_code=422,
            detail="Invalid container_name; use the exact lowercase container/file-system name, not a folder path.",
        )
    if "/" in container_name or "\\" in container_name:
        raise HTTPException(status_code=422, detail="Invalid container_name; use the container/file-system name only.")

    raw_file_format = config.get("file_format")
    file_format = str(raw_file_format).strip().lower() if raw_file_format not in ("", None) else "csv"
    if file_format not in ("csv", "json", "parquet", "excel"):
        raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")


def _map_adls_error(error: Exception, account_name: str, container_name: str) -> HTTPException:
    message = _collect_exception_message(error)
    detail = _adls_error_detail(message, account_name=account_name, container_name=container_name)
    return HTTPException(status_code=422, detail=detail)


def test_adls_connection(request: ADLSAuthorizationRequest) -> dict:
    validate_adls_authorization(request)
    config = _extract_config(request)
    logger.info("ADLS auth: starting destination validation | config=%s", _redact_config(config))

    try:
        from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
        from azure.identity import ClientSecretCredential
        from azure.storage.filedatalake import DataLakeServiceClient
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="ADLS dependencies missing: azure-identity and azure-storage-file-datalake are required.",
        ) from exc

    account_name = _require_non_empty(config, "account_name", "storage_account_name")
    container_name = _require_non_empty(config, "container_name", "file_system", "filesystem")
    _validate_adls_storage_account_dns(account_name)

    credential = ClientSecretCredential(
        tenant_id=_require_non_empty(config, "tenant_id"),
        client_id=_require_non_empty(config, "client_id"),
        client_secret=_require_non_empty(config, "client_secret"),
    )
    account_url = f"https://{account_name}.dfs.core.windows.net"
    service_client = DataLakeServiceClient(account_url=account_url, credential=credential, **ADLS_CLIENT_KWARGS)
    file_system_client = service_client.get_file_system_client(container_name)

    probe_path = f".datasyncher_write_probe_{uuid.uuid4().hex}"
    probe_created = False

    try:
        credential.get_token("https://storage.azure.com/.default")
        file_system_client.get_file_system_properties()

        probe_client = file_system_client.get_file_client(probe_path)
        probe_client.upload_data(b"", overwrite=True)
        probe_created = True

        logger.info(
            "ADLS auth: validation succeeded | account=%s container=%s",
            account_name,
            container_name,
        )
        return {
            "account_name": account_name,
            "container_name": container_name,
        }
    except HTTPException:
        raise
    except (ClientAuthenticationError, HttpResponseError, ResourceNotFoundError) as exc:
        raise _map_adls_error(exc, account_name, container_name) from exc
    except Exception as exc:
        logger.warning("ADLS auth validation failed | error_type=%s error=%s", type(exc).__name__, str(exc))
        raise _map_adls_error(exc, account_name, container_name) from exc
    finally:
        if probe_created:
            try:
                file_system_client.get_file_client(probe_path).delete_file()
            except Exception:
                logger.warning("ADLS auth write probe cleanup failed | path=%s", probe_path)


test_adls_connection.__test__ = False


def _normalize_adls_path(path: Any) -> str:
    cleaned = str(path or "").strip().replace("\\", "/")
    return cleaned.strip("/")


def _join_adls_path(*parts: Any) -> str:
    segments = []
    for part in parts:
        value = str(part or "").strip().replace("\\", "/").strip("/")
        if value:
            segments.append(value)
    return "/".join(segments)


def _extract_adls_base_path(adls_config: Dict[str, Any]) -> str:
    return _normalize_adls_path(
        adls_config.get("base_folder_path")
        or adls_config.get("base_folder")
        or adls_config.get("root_folder")
    )


def _sanitize_adls_filename(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return "export"
    return name.replace("/", "_").replace("\\", "_")


def _require_adls_field(config: Dict[str, Any], field_name: str, *aliases: str) -> str:
    for key in (field_name, *aliases):
        value = str(config.get(key) or "").strip()
        if value:
            return value
    raise HTTPException(status_code=422, detail=f"Missing required ADLS field(s): {field_name}")


def _map_adls_client_error(error: Exception, *, account_name: str, container_name: str, path: Optional[str] = None) -> HTTPException:
    message = _collect_exception_message(error)
    detail = _adls_error_detail(message, account_name=account_name, container_name=container_name, path=path)
    return HTTPException(status_code=422, detail=detail)


def _build_adls_service_client(adls_config: Dict[str, Any]) -> Any:
    try:
        from azure.identity import ClientSecretCredential
        from azure.storage.filedatalake import DataLakeServiceClient
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="ADLS dependencies missing: azure-identity and azure-storage-file-datalake are required.",
        ) from exc

    account_name = _require_adls_field(adls_config, "account_name", "storage_account_name")
    tenant_id = _require_adls_field(adls_config, "tenant_id")
    client_id = _require_adls_field(adls_config, "client_id")
    client_secret = _require_adls_field(adls_config, "client_secret")

    if "." in account_name or "/" in account_name or "\\" in account_name:
        raise HTTPException(status_code=422, detail="Invalid account_name; use the storage account name only.")

    try:
        _validate_adls_storage_account_dns(account_name)
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        account_url = f"https://{account_name}.dfs.core.windows.net"
        return DataLakeServiceClient(account_url=account_url, credential=credential, **ADLS_CLIENT_KWARGS)
    except Exception as exc:
        container_name = str(adls_config.get("container_name") or "").strip()
        raise _map_adls_client_error(exc, account_name=account_name, container_name=container_name) from exc


def _validate_adls_destination_access(file_system_client: Any, *, account_name: str, container_name: str) -> None:
    try:
        file_system_client.get_file_system_properties()
    except Exception as exc:
        raise _map_adls_client_error(exc, account_name=account_name, container_name=container_name) from exc


def _is_adls_not_found_error(error: Exception) -> bool:
    lowered = _collect_exception_message(error).lower()
    return (
        "resourcenotfound" in lowered
        or "pathnotfound" in lowered
        or "filesystemnotfound" in lowered
        or "containernotfound" in lowered
        or "the specified path does not exist" in lowered
        or "the specified resource does not exist" in lowered
    )


def _call_adls_with_optional_timeout(method: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return method(*args, timeout=ADLS_UPLOAD_TIMEOUT, **kwargs)
    except TypeError as exc:
        if "timeout" not in str(exc):
            raise
        return method(*args, **kwargs)


def _recreate_adls_file(file_client: Any, remote_path: str) -> None:
    try:
        _call_adls_with_optional_timeout(file_client.delete_file)
    except Exception as exc:
        if not _is_adls_not_found_error(exc):
            raise HTTPException(
                status_code=422,
                detail=f"Failed to overwrite existing ADLS file '{remote_path}': {exc}",
            ) from exc

    try:
        _call_adls_with_optional_timeout(file_client.create_file)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to create ADLS file '{remote_path}': {exc}",
        ) from exc


def _append_flush_adls_stream(
    file_client: Any,
    stream: Any,
    *,
    total_size: int,
    remote_path: str,
) -> None:
    offset = 0
    chunk_number = 0

    while True:
        chunk = stream.read(ADLS_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break

        chunk_number += 1
        chunk_len = len(chunk)
        try:
            _call_adls_with_optional_timeout(
                file_client.append_data,
                chunk,
                offset=offset,
                length=chunk_len,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"ADLS upload failed for '{remote_path}' at chunk {chunk_number} "
                    f"(offset={offset}, length={chunk_len}): {exc}"
                ),
            ) from exc

        offset += chunk_len

    if offset != total_size:
        raise HTTPException(
            status_code=422,
            detail=f"ADLS upload byte count mismatch for '{remote_path}': appended={offset}, expected={total_size}",
        )

    try:
        _call_adls_with_optional_timeout(file_client.flush_data, total_size)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"ADLS upload failed while flushing '{remote_path}' at total_size={total_size}: {exc}",
        ) from exc


def upload_file_to_adls(
    filename: str,
    file_data: Any,
    credentials: Dict[str, Any],
    *,
    length: Optional[int] = None,
) -> str:
    """
    Upload bytes or a readable file stream to ADLS Gen2 using the destination
    service principal configuration.
    """
    adls_config = dict(credentials or {})
    account_name = _require_adls_field(adls_config, "account_name", "storage_account_name")
    container_name = _require_adls_field(adls_config, "container_name")
    if "/" in container_name or "\\" in container_name:
        raise HTTPException(status_code=422, detail="Invalid container_name; use the container/file-system name only.")

    service_client = _build_adls_service_client(adls_config)
    file_system_client = service_client.get_file_system_client(container_name)
    remote_path = _normalize_adls_path(filename)
    try:
        file_client = file_system_client.get_file_client(remote_path)
        if isinstance(file_data, (bytes, bytearray, memoryview)):
            raw_data = bytes(file_data)
            stream = io.BytesIO(raw_data)
            total_size = len(raw_data) if length is None else int(length)
        else:
            stream = file_data
            if length is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"ADLS upload requires file length for streamed upload: {remote_path}",
                )
            total_size = int(length)

        _recreate_adls_file(file_client, remote_path)
        _append_flush_adls_stream(
            file_client,
            stream,
            total_size=total_size,
            remote_path=remote_path,
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        raise _map_adls_client_error(
            exc,
            account_name=account_name,
            container_name=container_name,
            path=remote_path,
        ) from exc

    return f"abfss://{container_name}@{account_name}.dfs.core.windows.net/{remote_path}"


def _upload_file_to_adls(
    adls_config: Dict[str, Any],
    *,
    remote_path: str,
    local_path: str,
) -> int:
    file_size = os.path.getsize(local_path)
    logger.info(
        "Starting ADLS upload | remote_path=%s local_path=%s file_size_bytes=%s",
        remote_path,
        local_path,
        file_size,
    )
    with open(local_path, "rb") as file_handle:
        upload_file_to_adls(
            remote_path,
            file_handle,
            adls_config,
            length=file_size,
        )
    logger.info("Completed ADLS upload | remote_path=%s file_size_bytes=%s", remote_path, file_size)
    return file_size


def _mark_unprocessed_tables_failed(
    *,
    request: SnowflakeADLSMigrationRequest,
    job_name: str,
    error_message: str,
    processed_tables: Optional[List[str]] = None,
) -> None:
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
            logger.warning("Failed to mark unprocessed ADLS table %s as failed: %s", table_name, table_err)


def _process_snowflake_to_adls_table(
    *,
    request: SnowflakeADLSMigrationRequest,
    table_name: str,
    source_config: Dict[str, Any],
    adls_config: Dict[str, Any],
    account_name: str,
    container_name: str,
    base_path: str,
    file_format: str,
    default_delimiter: str,
    default_quotechar: str,
    escapechar: Optional[str],
    null_sequence: str,
    fetch_size: int,
    table_export_settings: Dict[str, Any],
    job_name: str,
    failure_tracker: Optional[MigrationFailureTracker] = None,
) -> Dict[str, Any]:
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    table_started = datetime.now(timezone.utc)
    result = {
        "table_name": table_name,
        "status": "failed",
        "rows": 0,
        "columns": 0,
        "remote_path": None,
        "bytes_uploaded": 0,
        "error": None,
    }
    sf_conn = None

    try:
        update_table_status_snowflake(connection_id, table_name, "in_progress", job_name, migration_session_id)
        try:
            SimpleProgressTracker.reset_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
            )
        except Exception as progress_reset_err:
            logger.warning("Failed to reset live staging count for ADLS table %s: %s", table_name, progress_reset_err)

        sf_service = SnowflakeService(source_config, destination_schema=None, validate_destination_schema=False)
        sf_conn = sf_service.create_connection()
        schema_name = _extract_snowflake_schema(source_config)
        if schema_name:
            with sf_conn.cursor() as schema_cur:
                schema_cur.execute(f"USE SCHEMA {schema_name}")

        table_settings = lookup_table_export_settings(table_export_settings, table_name)
        if not isinstance(table_settings, dict):
            table_settings = {}

        selected_fields = _normalize_selected_fields(table_settings.get("selected_fields"))
        sort_columns = table_settings.get("sort_columns") or (table_settings.get("excel_options") or {}).get("sort_columns")
        ts = build_utc_filename_suffix(table_settings.get("filename_date_format"))

        table_file_format = str(table_settings.get("file_format") or file_format or "csv").strip().lower()
        if table_file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        query = _build_snowflake_select_query(table_name, selected_fields,sort_columns)
        if table_file_format == "excel":
            check_excel_limits_preflight(sf_conn, query, table_name)

        with sf_conn.cursor() as cur:
            try:
                cur.execute(query)
            except Exception as snowflake_read_err:
                raise _map_snowflake_export_error(table_name, snowflake_read_err) from snowflake_read_err

            # Apply the active generic source script before the shared file
            # writers while preserving the existing no-script cursor path.
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
                base_path,
                table_settings.get("target_folder"),
            )
            folder_path = _join_adls_path(base_path, table_target_folder)
            base_name = _sanitize_adls_filename(table_settings.get("output_file_name") or table_name)
            add_ts = table_settings.get("add_utc_timestamp")
            add_ts = True if add_ts is None else bool(add_ts)

            ext = {
                "csv": "csv",
                "json": "json",
                "parquet": "parquet",
                "excel": "xlsx",
            }[table_file_format]
            filename = f"{base_name}_{ts}.{ext}" if add_ts else f"{base_name}.{ext}"
            remote_path = _join_adls_path(folder_path, filename)

            with tempfile.TemporaryDirectory(prefix="sf_adls_") as tmpdir:
                local_path = os.path.join(tmpdir, filename)
                progress_label = f"ADLS table={table_name} file={filename}"

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
                            "Failed to update live staging count for ADLS table %s at total_rows=%s: %s",
                            table_name,
                            total_rows_written,
                            progress_err,
                        )

                logger.info(
                    "Starting Snowflake export for ADLS | table=%s fetch_size=%s format=%s local_path=%s remote_path=%s",
                    table_name,
                    fetch_size,
                    table_file_format,
                    local_path,
                    remote_path,
                )

                if table_file_format == "csv":
                    delimiter = _decode_csv_char(table_settings.get("csv_delimiter") or default_delimiter) or ","
                    quotechar = _decode_csv_char(table_settings.get("csv_quote_char") or default_quotechar) or '"'
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

                logger.info(
                    "Completed Snowflake export for ADLS | table=%s total_rows=%s columns=%s local_path=%s",
                    table_name,
                    total_rows,
                    col_count,
                    local_path,
                )
                bytes_uploaded = _upload_file_to_adls(
                    adls_config,
                    remote_path=remote_path,
                    local_path=local_path,
                )

        table_completed_at = datetime.now(timezone.utc)
        result.update(
            {
                "status": "success",
                "rows": total_rows,
                "columns": col_count,
                "remote_path": f"abfss://{container_name}@{account_name}.dfs.core.windows.net/{remote_path}",
                "bytes_uploaded": bytes_uploaded,
                "table_completed_at": table_completed_at,
            }
        )

        try:
            SimpleProgressTracker.set_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                count_value=total_rows,
            )
            update_table_status_snowflake(
                connection_id,
                table_name,
                "completed",
                job_name,
                migration_session_id,
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
            # ADLS-specific: refresh UI only after completed status and data transfer rows exist.
            try:
                from utils.websocket_utils import (
                    trigger_connection_ws_status_update,
                    trigger_ws_status_update,
                )
                trigger_ws_status_update(migration_session_id)
                trigger_connection_ws_status_update(connection_id)
            except Exception:
                pass
        except Exception as log_err:
            _log_stage_error(
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                function_name="_process_snowflake_to_adls_table.logging",
                error=log_err,
                extra_message=f"table={table_name}",
            )

        try:
            _send_adls_success_notification(
                request=request,
                table_name=table_name,
                table_settings=table_settings,
                remote_path=result["remote_path"],
                total_rows=total_rows,
                col_count=col_count,
                completed_at=table_completed_at,
            )
        except Exception as notif_err:
            logger.warning(
                "ADLS success notification failed (non-fatal) | table=%s error=%s",
                table_name,
                notif_err,
            )

        return result
    except HTTPException as exc:
        result["error"] = _extract_error_message(exc)
        if failure_tracker is not None:
            failure_tracker.add_failure(table_name, result["error"], "Snowflake to ADLS")
        _mark_table_failed_with_error(
            request=request,
            table_name=table_name,
            job_name=job_name,
            error_message=result["error"],
        )
        _log_stage_error(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            function_name="_process_snowflake_to_adls_table.http",
            error=exc,
            extra_message=f"table={table_name}",
        )
        return result
    except Exception as exc:
        result["error"] = _extract_error_message(exc)
        if failure_tracker is not None:
            failure_tracker.add_failure(table_name, result["error"], "Snowflake to ADLS")
        _mark_table_failed_with_error(
            request=request,
            table_name=table_name,
            job_name=job_name,
            error_message=result["error"],
        )
        _log_stage_error(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            function_name="_process_snowflake_to_adls_table",
            error=exc,
            extra_message=f"table={table_name}",
        )
        return result
    finally:
        try:
            if sf_conn is not None:
                sf_conn.close()
        except Exception:
            pass


def migrate_snowflake_to_adls(request: SnowflakeADLSMigrationRequest) -> Dict[str, Any]:
    logger.info(
        "Entering migrate_snowflake_to_adls | connection_id=%s | tables=%s",
        request.connection_id,
        len(request.selected_tables or []),
    )
    started_at = datetime.now(timezone.utc)
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    job_name = request.job_name or f"Snowflake-ADLS Migration {connection_id}"
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
        "service_name": "Snowflake-ADLS",
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

        update_migration_status_log(
            job_name=job_name,
            message="Migration in progress",
            status="I",
            session=migration_session_id,
        )

        source_config = request.source_config or {}
        adls_config = dict(request.destination_config or {})
        account_name = _require_adls_field(adls_config, "account_name", "storage_account_name")
        container_name = _require_adls_field(adls_config, "container_name")
        if "/" in container_name or "\\" in container_name:
            raise HTTPException(status_code=422, detail="Invalid container_name; use the container/file-system name only.")
        base_path = _extract_adls_base_path(adls_config)

        service_client = _build_adls_service_client(adls_config)
        file_system_client = service_client.get_file_system_client(container_name)
        _validate_adls_destination_access(
            file_system_client,
            account_name=account_name,
            container_name=container_name,
        )

        file_format = str(adls_config.get("file_format") or "csv").strip().lower()
        if file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        default_delimiter = _decode_csv_char(adls_config.get("csv_delimiter") or ",")
        default_quotechar = _decode_csv_char(adls_config.get("csv_quote_char") or adls_config.get("csv_quotechar") or '"')
        escapechar = None
        null_sequence = adls_config.get("null_sequence")
        null_sequence = "" if null_sequence is None else str(null_sequence)

        fetch_size = int(request.chunk_count) if request.chunk_count else 5000
        if fetch_size <= 0:
            fetch_size = 5000

        table_export_settings = request.table_export_settings or {}
        selected_tables = [str(table_name or "").strip() for table_name in (request.selected_tables or []) if str(table_name or "").strip()]
        execution_order = str(request.execution_order or "sequential").strip().lower()
        logger.info(
            "Snowflake->ADLS execution mode=%s | selected_tables=%s | account=%s | container=%s | base_path=%s",
            execution_order,
            len(selected_tables),
            account_name,
            container_name,
            base_path,
        )

        table_results: List[Dict[str, Any]] = []
        if execution_order == "parallel" and len(selected_tables) > 1:
            max_workers = min(3, len(selected_tables))
            logger.info("Executing Snowflake->ADLS in parallel mode | max_workers=%s", max_workers)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _process_snowflake_to_adls_table,
                        request=request,
                        table_name=table_name,
                        source_config=source_config,
                        adls_config=adls_config,
                        account_name=account_name,
                        container_name=container_name,
                        base_path=base_path,
                        file_format=file_format,
                        default_delimiter=default_delimiter,
                        default_quotechar=default_quotechar,
                        escapechar=escapechar,
                        null_sequence=null_sequence,
                        fetch_size=fetch_size,
                        table_export_settings=table_export_settings,
                        job_name=job_name,
                        failure_tracker=failure_tracker,
                    ): table_name
                    for table_name in selected_tables
                }
                for future in as_completed(futures):
                    table_results.append(future.result())
        else:
            logger.info("Executing Snowflake->ADLS in sequential mode")
            for table_name in selected_tables:
                table_results.append(
                    _process_snowflake_to_adls_table(
                        request=request,
                        table_name=table_name,
                        source_config=source_config,
                        adls_config=adls_config,
                        account_name=account_name,
                        container_name=container_name,
                        base_path=base_path,
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
            table_name = table_result["table_name"]
            results["tables"][table_name] = {
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

        if results["failed_tables"] == 0:
            results["status"] = "success"
        elif results["successful_tables"] == 0:
            results["status"] = "failed"
        else:
            results["status"] = "partial"

        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["status_code"] = 200 if results["failed_tables"] == 0 else 206

        # Supply the child-result contract used by the Celery finalizer.
        results["execution_time"] = time.time() - started_at.timestamp()
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

        failure_tracker.send_consolidated_error_email(
            connection_name=request.connection_name or f"ADLS {connection_id}",
            service_name="Snowflake to ADLS",
            user_id=request.user_id,
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            company_name=request.company_name or "Unknown Company",
            is_final_attempt=True,
        )

        # ADLS-specific: re-reconcile at end to fix any stale-checker overwrites
        try:
            from utils.database_utils import reconcile_completed_tables
            reconcile_completed_tables(
                connection_id=connection_id,
                job_name=job_name,
                migration_session_id=migration_session_id,
                selected_tables=request.selected_tables or [],
            )
        except Exception as recon_err:
            logger.warning(
                "ADLS end-of-migration reconcile failed: %s", recon_err
            )

        return results
    except Exception as exc:
        error_message = _extract_error_message(exc)
        _mark_unprocessed_tables_failed(
            request=request,
            job_name=job_name,
            error_message=error_message,
            processed_tables=list(results.get("tables", {}).keys()),
        )
        _log_stage_error(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            function_name="migrate_snowflake_to_adls",
            error=exc,
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
