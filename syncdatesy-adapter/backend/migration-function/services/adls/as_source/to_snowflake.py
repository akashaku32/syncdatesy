# services/adls_to_snowflake_service.py
"""Standalone ADLS to Snowflake migration service."""

from __future__ import annotations

import io
import hashlib
import json
import logging
import time
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from google.cloud import storage

from models.adls_source_models import ADLSListingTables, ADLSSnowflakeMigrationRequest
from services.adls.source import (
    ADLSSourceService,
    _as_bool,
    _effective_root_folder,
    _get_adls_file_system_client,
    _list_adls_files,
    _normalize_single_file_map,
    _parse_mapping,
)
from services.amazon_s3.service import (
    GCP_PROJECT_ID,
    GCS_BUCKET_NAME,
    MigrationFailureTracker,
    Q,
    _snowflake_table_exists,
    align_chunk_to_table_column_order,
    copy_into_staging_from_gcs_batch,
    ensure_tables,
    merge_staging_into_final,
    normalize_and_log_snowflake_identifier,
    parse_datetime_from_db,
    reorder_chunk_columns_for_snowflake,
    resolve_gcs_bucket_name,
    resolve_snowflake_storage_integration,
    sanitize_column_name,
    sanitize_identifier,
    sf_connect,
    staging_name,
    table_from_file_key,
    upload_chunk_to_gcs,
)
from services.sftp.as_source.to_snowflake import (
    compute_common_new_columns_for_multi_files_sftp,
    infer_columns_from_sftp_sample,
    is_sftp_file_compatible_with_type,
    read_sftp_file_in_chunks,
    validate_sftp_file_structure_matches,
)
from utils.celery_cleanup_utils import handle_migration_completion
from utils.database_utils import (
    data_transfer_logs,
    get_migration_record_id,
    get_last_checked_dates_and_fields,
    postgres_connection,
    update_table_status_snowflake,
)
from utils.error_utils import (
    handle_service_error,
    insert_tool_log,
    log_migration_completion,
    update_table_migration_status_log,
)
from utils.gcp_utils import (
    assert_allowed_prefix_matches,
    ensure_bucket_iam_for_integration,
    ensure_gcs_storage_integration,
    gcs_client,
    get_gcp_service_account,
)
from utils.s3_utils import log, log_prefix
from utils.simple_progress_tracker import SimpleProgressTracker

logger = logging.getLogger(__name__)

SUPPORTED_ADLS_SNOWFLAKE_FILE_TYPES = {"csv", "tsv", "json", "xml", "parquet", "avro"}
METADATA_COLS = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}


class ADLSFileClientWrapper:
    """Expose ADLS files through the small stat/open protocol used by stream readers."""

    def __init__(self, file_system_client):
        self.file_system_client = file_system_client

    def _file_client(self, key: str):
        return self.file_system_client.get_file_client(str(key or "").strip().lstrip("/"))

    def stat(self, key: str) -> SimpleNamespace:
        props = self._file_client(key).get_file_properties()
        last_modified = getattr(props, "last_modified", None) or datetime.now(timezone.utc)
        if last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=timezone.utc)
        size = getattr(props, "size", None)
        if size is None:
            size = getattr(props, "content_length", None)
        return SimpleNamespace(st_mtime=last_modified.timestamp(), st_size=int(size or 0))

    def open(self, key: str, mode: str = "rb"):
        if "b" not in mode:
            raise ValueError("ADLSFileClientWrapper only supports binary reads")
        download = self._file_client(key).download_file()
        spool = tempfile.SpooledTemporaryFile(mode="w+b", max_size=5 * 1024 * 1024)
        try:
            if hasattr(download, "readinto"):
                try:
                    download.readinto(spool)
                    spool.seek(0)
                    return spool
                except Exception:
                    spool.seek(0)
                    spool.truncate(0)
            if hasattr(download, "chunks"):
                for chunk in download.chunks():
                    if chunk:
                        spool.write(chunk)
            else:
                for chunk in iter(lambda: download.read(1024 * 1024), b""):
                    if chunk:
                        spool.write(chunk)
            spool.seek(0)
            return spool
        except Exception:
            try:
                spool.close()
            except Exception:
                pass
            raise


def _is_adls_file_compatible(file_client, file_key, file_type, **options):
    if file_type == "parquet":
        # A prefix cannot contain the footer of a large Parquet file.
        with file_client.open(file_key, "rb") as stream:
            header = stream.read(4)
            stream.seek(-4, 2)
            footer = stream.read(4)
        if header != b"PAR1" or footer != b"PAR1":
            return False, "Invalid Parquet signature (expected PAR1 at start/end)."
        return True, None
    return is_sftp_file_compatible_with_type(file_client, file_key, file_type, **options)


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _normalize_file_type(value: Any) -> str:
    raw = _value(value)
    return str(raw or "").strip().lower().replace("filetype.", "")


def _build_safety_cutoff(safety_interval: Any) -> Optional[datetime]:
    raw = str(safety_interval or "").strip()
    if not raw or raw.lower() == "none":
        return None
    try:
        minutes = int(raw)
    except Exception:
        return None
    if minutes <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _is_manual_adls_migration(job_name: Any) -> bool:
    return "_manual_" in str(job_name or "").lower()


def _http_error_detail(error: Exception) -> str:
    detail = getattr(error, "detail", None)
    return str(detail) if detail else (str(error).strip() or repr(error))


def _option(source_config: Dict[str, Any], opts: Dict[str, Any], option_key: str, flat_key: str, default=None):
    if option_key in opts:
        return _value(opts.get(option_key))
    return _value(source_config.get(flat_key, default))


def _reader_options(source_config: Dict[str, Any], file_type: str) -> Dict[str, Any]:
    csv_options = _parse_mapping(source_config.get("csv_options"))
    tsv_options = _parse_mapping(source_config.get("tsv_options"))
    opts = csv_options if file_type == "csv" else tsv_options if file_type == "tsv" else {}
    prefix = "tsv_" if file_type == "tsv" else ""
    return {
        "delimiter": _option(source_config, opts, "delimiter", f"{prefix}delimiter" if prefix else "delimiter"),
        "quotechar": _option(source_config, opts, "quotechar", f"{prefix}quotechar" if prefix else "quotechar"),
        "skipheaderlines": _option(
            source_config,
            opts,
            "skipheaderlines",
            f"{prefix}skipheaderlines" if prefix else "skipheaderlines",
        ),
        "escape_char": _option(source_config, opts, "escape_char", f"{prefix}escape_char" if prefix else "escape_char"),
        "null_sequence": _option(source_config, opts, "null_sequence", f"{prefix}null_sequence" if prefix else "null_sequence"),
        "skip_footer_lines": _option(
            source_config,
            opts,
            "skip_footer_lines",
            f"{prefix}skip_footer_lines" if prefix else "skip_footer_lines",
        ),
        "line_separator": _option(source_config, opts, "line_separator", "line_separator"),
        "json_mode": _normalize_file_type(source_config.get("json_mode")) or source_config.get("json_mode"),
        "xml_mode": _normalize_file_type(source_config.get("xml_mode")) or source_config.get("xml_mode"),
    }


def _normalize_custom_pk_mapping(value: Any) -> Dict[str, List[str]]:
    mapping = _parse_mapping(value)
    normalized: Dict[str, List[str]] = {}
    for table_name, columns in mapping.items():
        table_key = sanitize_identifier(str(table_name or "").strip()).upper()
        if not table_key:
            continue
        if isinstance(columns, str):
            try:
                columns = json.loads(columns)
            except Exception:
                columns = [columns]
        if not isinstance(columns, list):
            columns = [columns]
        normalized[table_key] = [str(col).strip() for col in columns if str(col).strip()]
    return normalized


def _entry_file_type(entry, configured_file_type: str) -> str:
    return configured_file_type or str(getattr(entry, "inferred_file_type", "") or "").strip().lower()


def _infer_migration_file_type(source_config: Dict[str, Any], selected_files: List[str]) -> str:
    configured = _normalize_file_type(source_config.get("file_type"))
    if configured:
        return configured
    first_key = selected_files[0] if selected_files else ""
    if "." in first_key:
        return first_key.rsplit(".", 1)[-1].lower()
    return "csv"


def _matching_existing_key(candidate: str, existing_file_keys: Set[str]) -> Optional[str]:
    raw = str(candidate or "").strip().lstrip("/")
    if not raw:
        return None
    if raw in existing_file_keys:
        return raw
    for file_key in existing_file_keys:
        if file_key.endswith("/" + raw):
            return file_key
    basename_matches = [key for key in existing_file_keys if key.rsplit("/", 1)[-1] == raw]
    return basename_matches[0] if len(basename_matches) == 1 else None


def _table_for_file(file_key: str, single_file_map: Dict[str, str]) -> str:
    mapped = single_file_map.get(file_key) or single_file_map.get(file_key.lstrip("/")) or single_file_map.get(file_key.rsplit("/", 1)[-1])
    if not mapped:
        for mapped_file_key, mapped_table in single_file_map.items():
            mapped_key = str(mapped_file_key or "").strip().lstrip("/")
            if file_key == mapped_key or file_key.endswith("/" + mapped_key) or mapped_key.endswith("/" + file_key):
                mapped = mapped_table
                break
    return sanitize_identifier(str(mapped).strip()) if mapped else table_from_file_key(file_key)


def _adls_gcs_prefix_for_file(table: str, file_key: str, migration_session_id: Any) -> str:
    file_hash = hashlib.sha1(str(file_key or "").encode("utf-8")).hexdigest()[:16]
    session_part = str(migration_session_id or "manual").strip() or "manual"
    return f"{sanitize_identifier(table)}/adls_{session_part}_{file_hash}_{time.time_ns()}"


def _resolve_selected_files(
    selected_tables: List[Any],
    entries: List[Any],
    single_file_map: Dict[str, str],
    table_to_files_map: Dict[str, Any],
    file_mapping_mode: str,
) -> List[str]:
    existing_file_keys = {entry.file_key for entry in entries}
    if file_mapping_mode == "multi_files_single_table":
        selected: List[str] = []
        seen: Set[str] = set()
        for table_name, mapped_files in (table_to_files_map or {}).items():
            if selected_tables:
                table_key = sanitize_identifier(str(table_name or "").strip()).upper()
                selected_table_keys = {
                    sanitize_identifier(str(value or "").strip()).upper()
                    for value in selected_tables
                    if str(value or "").strip()
                }
                if table_key and selected_table_keys and table_key not in selected_table_keys:
                    continue
            if not isinstance(mapped_files, list):
                continue
            for mapped_file_key in mapped_files:
                key = _matching_existing_key(str(mapped_file_key or ""), existing_file_keys)
                if key and key not in seen:
                    seen.add(key)
                    selected.append(key)
        return selected or sorted(existing_file_keys)

    if not selected_tables:
        mapped = [_matching_existing_key(key, existing_file_keys) for key in single_file_map.keys()]
        selected = [key for key in mapped if key]
        return selected or sorted(existing_file_keys)

    mapped_table_lookup = {
        sanitize_identifier(str(table).strip()).upper(): str(file_key).strip().lstrip("/")
        for file_key, table in single_file_map.items()
        if str(file_key).strip() and str(table).strip()
    }
    table_name_lookup = {
        sanitize_identifier(table_from_file_key(file_key)).upper(): file_key
        for file_key in existing_file_keys
    }

    selected: List[str] = []
    seen: Set[str] = set()
    for value in selected_tables or []:
        raw = str(value or "").strip()
        if not raw:
            continue
        key = _matching_existing_key(raw, existing_file_keys)
        if not key:
            key = _matching_existing_key(mapped_table_lookup.get(sanitize_identifier(raw).upper(), ""), existing_file_keys)
        if not key:
            key = table_name_lookup.get(sanitize_identifier(raw).upper())
        if key and key not in seen:
            seen.add(key)
            selected.append(key)
    return selected


def _resolve_table_to_files_map(table_to_files_map: Dict[str, Any], entries: List[Any]) -> Dict[str, List[str]]:
    existing_file_keys = {entry.file_key for entry in entries}
    resolved: Dict[str, List[str]] = {}
    for raw_table_name, raw_files in (table_to_files_map or {}).items():
        table_name = sanitize_identifier(str(raw_table_name or "").strip())
        if not table_name or not isinstance(raw_files, list):
            continue

        seen: Set[str] = set()
        for raw_file_key in raw_files:
            file_key = _matching_existing_key(str(raw_file_key or ""), existing_file_keys)
            if not file_key or file_key in seen:
                continue
            seen.add(file_key)
            resolved.setdefault(table_name, []).append(file_key)

    return {table: files for table, files in resolved.items() if files}


def _build_file_to_table_lookup(table_to_files_map: Dict[str, List[str]]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for table_name, file_keys in (table_to_files_map or {}).items():
        table = sanitize_identifier(str(table_name or "").strip())
        if not table:
            continue
        for file_key in file_keys or []:
            normalized_file_key = str(file_key or "").strip().lstrip("/")
            if normalized_file_key:
                lookup[normalized_file_key] = table
    return lookup


def _lookup_table_state(last_checked_data: Dict[str, Any], table: str) -> Dict[str, Any]:
    state = last_checked_data.get(table) or last_checked_data.get(str(table).upper())
    if state:
        return state
    upper_lookup = {str(key).upper(): value for key, value in (last_checked_data or {}).items()}
    return upper_lookup.get(str(table).upper(), {}) or {}


def _should_skip_unchanged_file(last_synced_dt: Optional[datetime], file_modified: datetime) -> bool:
    if not last_synced_dt:
        return False
    if file_modified.tzinfo is None:
        file_modified = file_modified.replace(tzinfo=timezone.utc)
    return file_modified <= last_synced_dt


def _should_apply_delta_check(load_method: Any, is_manual_migration: bool) -> bool:
    """Keep file+row upserts incremental even when manually triggered."""
    return not is_manual_migration or str(_value(load_method) or "").strip() == "upsert_file_row"


def _dt_for_log(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, datetime):
        dt_value = value
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value.isoformat()
    return str(value)


def _preview_for_log(values: Any, *, limit: int = 5) -> str:
    items = list(values or [])
    preview = ", ".join(str(item) for item in items[:limit])
    if len(items) > limit:
        preview = f"{preview}, ... (+{len(items) - limit} more)" if preview else f"... (+{len(items) - limit} more)"
    return preview or "None"


def _count_snowflake_table_rows(conn, table_name: str, sf_database: str, sf_schema: str) -> int:
    qualified_table = f"{Q(sf_database)}.{Q(sf_schema)}.{Q(table_name)}"
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {qualified_table}")
        row = cur.fetchone()
    return int((row[0] if row else 0) or 0)


def _type_family(sf_type: str) -> str:
    sf_type = str(sf_type or "").upper()
    if any(part in sf_type for part in ("FLOAT", "DOUBLE", "REAL", "NUMBER", "DECIMAL", "NUMERIC")):
        return "numeric"
    if "TIMESTAMP" in sf_type or sf_type == "DATE":
        return "datetime"
    if "BOOLEAN" in sf_type:
        return "boolean"
    if "VARIANT" in sf_type:
        return "variant"
    return "text"


def _widen_type(existing: Optional[str], incoming: Optional[str]) -> Optional[str]:
    if not existing:
        return incoming
    if not incoming:
        return existing
    existing_u = str(existing).upper()
    incoming_u = str(incoming).upper()
    if _type_family(existing_u) != _type_family(incoming_u):
        return existing
    if _type_family(existing_u) == "numeric" and ("FLOAT" in existing_u or "FLOAT" in incoming_u):
        return "FLOAT"
    return existing


def _build_multi_file_schema_override(
    file_client: ADLSFileClientWrapper,
    selected_files: List[str],
    file_type: str,
    reader_kwargs: Dict[str, Any],
) -> Optional[Dict[str, Dict[str, Any]]]:
    merged_schema: Dict[str, Dict[str, Any]] = {}
    attempted = 0
    skipped = 0
    log("[ADLS][SCHEMA_PRE] Inferring schema across all files for multi-file table creation")
    for file_key in selected_files:
        attempted += 1
        actual_columns, err = infer_columns_from_sftp_sample(
            file_client,
            file_key,
            file_type,
            delimiter=reader_kwargs.get("delimiter"),
            quotechar=reader_kwargs.get("quotechar"),
            skipheaderlines=reader_kwargs.get("skipheaderlines"),
            json_mode=reader_kwargs.get("json_mode"),
            xml_mode=reader_kwargs.get("xml_mode"),
            escape_char=reader_kwargs.get("escape_char"),
            null_sequence=reader_kwargs.get("null_sequence"),
            skip_footer_lines=reader_kwargs.get("skip_footer_lines"),
            line_separator=reader_kwargs.get("line_separator"),
        )
        if actual_columns is None:
            skipped += 1
            log(f"[ADLS][SCHEMA_PRE] Skipping '{file_key}' (no columns inferred): {err}")
            continue
        for col_name, col_meta in actual_columns.items():
            col_u = sanitize_column_name(str(col_name)).upper()
            if not col_u or col_u in METADATA_COLS:
                continue
            incoming_type = col_meta.get("data_type") if isinstance(col_meta, dict) else None
            existing_type = merged_schema.get(col_u, {}).get("data_type")
            merged_schema[col_u] = {"data_type": _widen_type(existing_type, incoming_type)}
    log(f"[ADLS][SCHEMA_PRE] Files scanned for schema: {attempted}, skipped: {skipped}")
    return merged_schema or None


def _log_skipped_transfer(
    *,
    cmp_id: int,
    connection_id: int,
    user_id: int,
    table_name: str,
    skipped_at: datetime,
    session: Any,
    job_name: str,
) -> None:
    if not job_name:
        return
    data_transfer_logs(
        cmp_id=cmp_id,
        connection_id=connection_id,
        user_id=user_id,
        table_name=table_name,
        start_time=skipped_at,
        end_time=skipped_at,
        columns_data=0,
        session=session,
        insert_success=0,
        insert_failure=0,
        upsert_success=0,
        upsert_failure=0,
        job_name=job_name,
    )


def _mark_selected_adls_tables_failed(
    *,
    connection_id: int,
    selected_tables: List[Any],
    source_config: Dict[str, Any],
    file_mapping_mode: str,
    multi_files_table_name: str,
    job_name: str,
    migration_session_id: int,
) -> None:
    candidates: Set[str] = set()
    if file_mapping_mode == "multi_files_single_table":
        candidates.add(sanitize_identifier(multi_files_table_name))
    else:
        single_file_map = _normalize_single_file_map(source_config)
        for value in selected_tables or []:
            raw = str(value or "").strip()
            if not raw:
                continue
            candidates.add(sanitize_identifier(single_file_map.get(raw) or raw))
    for table_name in sorted(name for name in candidates if name):
        try:
            update_table_status_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
        except Exception as status_error:
            log(f"[ADLS][MIGRATION] Failed to mark table '{table_name}' as failed: {status_error}")


def _build_gcs_client(gcs_service_account_json: Any, gcp_project_id: Any):
    try:
        return gcs_client(gcs_service_account_json, gcp_project_id)
    except Exception as config_error:
        log(f"[ADLS][MIGRATION] GCS connection with config failed: {config_error}")
        try:
            from google.auth import compute_engine

            credentials = compute_engine.Credentials()
            return storage.Client(credentials=credentials, project=gcp_project_id or GCP_PROJECT_ID)
        except Exception as fallback_error:
            try:
                return storage.Client(project=gcp_project_id or GCP_PROJECT_ID)
            except Exception as final_error:
                raise Exception(
                    f"GCS connection failed: {config_error}. "
                    f"Fallback attempts also failed: {fallback_error}, {final_error}"
                ) from final_error


class ADLSSnowflakeService:
    """Migration service for Azure Data Lake Storage to Snowflake jobs."""

    @staticmethod
    async def migrate(request: ADLSSnowflakeMigrationRequest) -> Dict[str, Any]:
        token = log_prefix.set("ADLS")
        try:
            return await ADLSSnowflakeService._sync_adls_to_snowflake(request)
        finally:
            log_prefix.reset(token)

    @staticmethod
    async def _sync_adls_to_snowflake(request: ADLSSnowflakeMigrationRequest) -> Dict[str, Any]:
        log("=" * 80)
        log("[ADLS][MIGRATION] Starting ADLS to Snowflake migration")
        log("=" * 80)

        source_config = dict(request.source_config or {})
        dest_config = dict(request.destination_config or {})
        connection_id = request.connection_id
        cmp_id = request.cmp_id
        user_id = request.user_id
        session = request.session
        migration_session_id = request.migration_session_id
        job_name = request.job_name or f"adls_migration_{migration_session_id}"
        connection_name = (request.connection_name or f"ADLS_CONNECTION_{connection_id}").strip()
        company_name = (request.company_name or "").strip()
        # ADLS handles table failures and returns without a Celery retry, so
        # failure logs and emails must be published on the current attempt.
        is_final_attempt = True
        chunk_count = request.chunk_count or 5000
        drop_and_reload = bool(request.drop_and_reload)
        log_id = getattr(request, "log_id", None)
        start_time = time.time()

        file_mapping_mode = str(
            source_config.get("file_mapping_mode")
            or source_config.get("file_mapping_method")
            or "single_table_per_file"
        ).strip()
        if file_mapping_mode not in {"single_table_per_file", "multi_files_single_table"}:
            file_mapping_mode = "single_table_per_file"
        multi_files_table_name = sanitize_identifier(source_config.get("multi_files_table_name") or "ADLS_FILES")
        single_file_map = _normalize_single_file_map(source_config)
        table_to_files_map = _parse_mapping(source_config.get("table_to_files_mapping"))

        try:
            file_system_client, account_name, container_name = _get_adls_file_system_client(source_config)
            root_folder = _effective_root_folder(source_config)
            include_subfolders = _as_bool(source_config.get("include_subfolders"), default=False)
            configured_file_type = _normalize_file_type(source_config.get("file_type"))
            raw_entries = _list_adls_files(file_system_client, root_folder, include_subfolders=include_subfolders)
            entries = sorted(raw_entries, key=lambda item: item.file_key.lower())
            log(
                f"[ADLS][LIST] account={account_name}, container={container_name}, "
                f"root='{root_folder or ''}', include_subfolders={include_subfolders}, "
                f"configured_file_type='{configured_file_type or 'auto'}', "
                f"files={len(entries)}"
            )
            if raw_entries:
                sample_entries = [
                    (
                        f"{entry.file_key} "
                        f"(type={entry.inferred_file_type or 'unknown'}, "
                        f"size={entry.size_bytes}, modified={_dt_for_log(entry.modified_at)})"
                    )
                    for entry in sorted(raw_entries, key=lambda item: item.file_key.lower())[:5]
                ]
                log(f"[ADLS][LIST] Sample files: {_preview_for_log(sample_entries, limit=5)}")
        except Exception as setup_error:
            error_message = _http_error_detail(setup_error)
            _mark_selected_adls_tables_failed(
                connection_id=connection_id,
                selected_tables=request.selected_tables or [],
                source_config=source_config,
                file_mapping_mode=file_mapping_mode,
                multi_files_table_name=multi_files_table_name,
                job_name=job_name,
                migration_session_id=migration_session_id,
            )
            handle_service_error(
                error=setup_error,
                context={
                    "job_name": job_name,
                    "log_id": log_id,
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "operation": "MIGRATION",
                    "table_name": "Migration Setup",
                    "is_terminal_failure": True,
                    "service_name": "ADLS to Snowflake",
                    "session": migration_session_id,
                    "migration_session_id": migration_session_id,
                    "connection_name": connection_name,
                    "company_name": company_name,
                    "execution_time": time.time() - start_time,
                },
                send_email=True,
                is_final_attempt=is_final_attempt,
            )
            return {
                "status": "failed",
                "message": error_message,
                "files_processed": 0,
                "failed_tables": len(request.selected_tables or []) or 1,
                "processed_tables": [],
            }

        resolved_table_to_files_map = (
            _resolve_table_to_files_map(table_to_files_map, entries)
            if file_mapping_mode == "multi_files_single_table"
            else {}
        )
        multi_table_mapping_mode = bool(resolved_table_to_files_map)
        unified_multi_file_mode = file_mapping_mode == "multi_files_single_table" and not multi_table_mapping_mode
        batch_multi_file_mode = unified_multi_file_mode or multi_table_mapping_mode
        file_to_table_lookup = _build_file_to_table_lookup(resolved_table_to_files_map)

        # Detect deleted single-file mappings before selection resolution drops
        # them. This mirrors S3's missing_files_single_mode signal.
        missing_files_single_mode: Set[str] = set()
        if file_mapping_mode == "single_table_per_file" and single_file_map:
            existing_file_keys = {entry.file_key for entry in entries}
            selected_table_names = {
                sanitize_identifier(str(value or "").strip()).upper()
                for value in (request.selected_tables or [])
                if str(value or "").strip()
            }
            for mapped_file_key, mapped_table in single_file_map.items():
                mapped_table_name = sanitize_identifier(str(mapped_table or "").strip()).upper()
                if selected_table_names and mapped_table_name not in selected_table_names:
                    continue
                if not _matching_existing_key(str(mapped_file_key or ""), existing_file_keys):
                    missing_files_single_mode.add(str(mapped_file_key).strip())

        def _refresh_after_missing_files() -> None:
            if not missing_files_single_mode or request.celery_child_mode:
                return
            refresh_job_name = (
                f"{job_name}_post_missing_file_schema_refresh"
                if job_name
                else f"ADLS_post_missing_file_schema_refresh_{migration_session_id}"
            )
            try:
                insert_tool_log(
                    cmp_id=cmp_id,
                    connection_id=connection_id,
                    usr_id=user_id,
                    table_name="Update Schema",
                    status="I",
                    message="Update schema in progress...",
                    session=migration_session_id,
                    job_name=refresh_job_name,
                    migration_record_id=None,
                )
            except Exception as start_log_error:
                log(f"[ADLS][MIGRATION] Could not create post-refresh start log: {start_log_error}")
            listing_request = ADLSListingTables(
                connection_id=connection_id,
                AzureDataLakeStorage=source_config,
                job_name=refresh_job_name,
                migration_session_id=migration_session_id,
                cmp_id=cmp_id,
                user_id=user_id,
                log_id=log_id,
            )
            refreshed_tables_result = ADLSSourceService.list_tables_with_schema(listing_request)
            ADLSSourceService.refresh_and_update_adls_tables_in_database(
                connection_id=connection_id,
                tables_result=refreshed_tables_result,
                job_name=refresh_job_name,
                migration_session_id=migration_session_id,
            )
            try:
                from utils.websocket_utils import trigger_connection_ws_status_update

                trigger_connection_ws_status_update(connection_id)
            except Exception as ws_error:
                log(f"[ADLS][MIGRATION] Post-migration schema refresh WS trigger failed: {ws_error}")
            log("[ADLS][MIGRATION] Post-migration schema refresh completed after missing-file detection")

        selected_files = _resolve_selected_files(
            request.selected_tables or [],
            entries,
            single_file_map,
            table_to_files_map,
            file_mapping_mode,
        )
        if not selected_files:
            error_msg = "No ADLS files selected for migration"
            _mark_selected_adls_tables_failed(
                connection_id=connection_id,
                selected_tables=request.selected_tables or [],
                source_config=source_config,
                file_mapping_mode=file_mapping_mode,
                multi_files_table_name=multi_files_table_name,
                job_name=job_name,
                migration_session_id=migration_session_id,
            )
            try:
                _refresh_after_missing_files()
            except Exception as post_refresh_error:
                log(f"[ADLS][MIGRATION] Post-migration schema refresh failed (non-fatal): {post_refresh_error}")
            handle_service_error(
                error=FileNotFoundError(error_msg),
                context={
                    "job_name": job_name,
                    "log_id": log_id,
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "operation": "MIGRATION",
                    "table_name": "Migration Setup",
                    "is_terminal_failure": True,
                    "successful_tables": 0,
                    "total_tables": len(request.selected_tables or []),
                    "total_rows": 0,
                    "session": migration_session_id,
                    "migration_session_id": migration_session_id,
                    "service_name": "ADLS to Snowflake",
                    "connection_name": connection_name,
                    "company_name": company_name,
                    "execution_time": time.time() - start_time,
                },
                send_email=True,
                is_final_attempt=is_final_attempt,
            )
            return {
                "status": "failed",
                "message": error_msg,
                "files_processed": 0,
                "failed_tables": len(request.selected_tables or []) or 1,
                "failed_tables_count": len(request.selected_tables or []) or 1,
                "successful_tables_count": 0,
                "successful_tables_list": [],
                "failed_tables_list": list(request.selected_tables or []),
                "processed_tables": [],
                "single_mode_missing_files_detected": bool(missing_files_single_mode),
                "single_mode_missing_files": sorted(missing_files_single_mode),
            }

        file_type = _infer_migration_file_type(source_config, selected_files)
        if file_type not in SUPPORTED_ADLS_SNOWFLAKE_FILE_TYPES:
            raise HTTPException(status_code=400, detail=f"ADLS to Snowflake does not support file_type '{file_type}'")

        if file_mapping_mode == "multi_files_single_table":
            entry_by_key = {entry.file_key: entry for entry in entries}
            before_count = len(selected_files)
            selected_files = [
                file_key
                for file_key in selected_files
                if _entry_file_type(entry_by_key.get(file_key), configured_file_type) == file_type
            ]
            if multi_table_mapping_mode:
                selected_files = [file_key for file_key in selected_files if file_key in file_to_table_lookup]
            skipped_by_type = before_count - len(selected_files)
            if skipped_by_type:
                log(f"[ADLS][MIGRATION] Ignoring {skipped_by_type} non-{file_type.upper()} file(s)")
            if not selected_files:
                return {
                    "status": "failed",
                    "message": f"No ADLS {file_type.upper()} files selected for migration",
                    "files_processed": 0,
                    "failed_tables": 1,
                    "processed_tables": [],
                }

        reader_kwargs = _reader_options(source_config, file_type)
        load_method = str(_value(source_config.get("load_method")) or "upsert_file_row").strip()
        custom_pk_mapping = _normalize_custom_pk_mapping(source_config.get("custom_primary_key"))

        files_per_table: Dict[str, int] = {}
        table_to_selected_files: Dict[str, List[str]] = {}
        for file_key in selected_files:
            table_for_count = (
                file_to_table_lookup.get(file_key)
                if multi_table_mapping_mode
                else multi_files_table_name if unified_multi_file_mode
                else _table_for_file(file_key, single_file_map)
            )
            table_for_count = sanitize_identifier(table_for_count)
            if table_for_count:
                files_per_table[table_for_count] = files_per_table.get(table_for_count, 0) + 1
                table_to_selected_files.setdefault(table_for_count, []).append(file_key)

        sf_account = dest_config.get("account")
        sf_user = dest_config.get("user") or dest_config.get("username")
        sf_password = dest_config.get("password")
        sf_warehouse = normalize_and_log_snowflake_identifier(dest_config.get("warehouse"), "warehouse")
        sf_database = normalize_and_log_snowflake_identifier(dest_config.get("database"), "database")
        sf_schema = normalize_and_log_snowflake_identifier(dest_config.get("schema"), "schema")
        sf_role = normalize_and_log_snowflake_identifier(dest_config.get("role"), "role") or "ACCOUNTADMIN"

        if not sf_account or not sf_user or not sf_database or not sf_schema:
            raise ValueError("Missing required Snowflake credentials (account, user, database, or schema)")
        if not sf_password and not dest_config.get("private_key"):
            raise ValueError("Missing required Snowflake authentication: either password or private_key must be provided")

        gcs_service_account_json = dest_config.get("gcs_service_account_json") or source_config.get("gcs_service_account_json")
        gcp_project_id = dest_config.get("gcp_project_id") or source_config.get("gcp_project_id")
        gcs_bucket_name = resolve_gcs_bucket_name(dest_config, source_config)
        sf_storage_integration = resolve_snowflake_storage_integration(dest_config, source_config)

        is_manual_migration = _is_manual_adls_migration(job_name)
        safety_cutoff = None if is_manual_migration else _build_safety_cutoff(request.safety_interval)
        if is_manual_migration and request.safety_interval:
            log("[ADLS][MIGRATION] Manual migration - safety interval bypassed")

        table_names = [
            (
                file_to_table_lookup.get(file_key)
                if multi_table_mapping_mode
                else multi_files_table_name if file_mapping_mode == "multi_files_single_table"
                else _table_for_file(file_key, single_file_map)
            )
            for file_key in selected_files
        ]
        table_names = [sanitize_identifier(table_name) for table_name in table_names if table_name]
        last_checked_data = get_last_checked_dates_and_fields(connection_id, list(sorted(set(table_names)))) if table_names else {}

        conn = None
        file_client = ADLSFileClientWrapper(file_system_client)
        processed = 0
        failed = 0
        total_rows = 0
        total_source_rows_read = 0
        total_inserts = 0
        total_updates = 0
        total_deletes = 0
        processed_tables: Set[str] = set()
        skipped_tables_delta: Set[str] = set()
        skipped_tables_safety: Set[str] = set()
        processed_file_keys: List[str] = []
        processed_file_keys_by_table: Dict[str, List[str]] = {}
        failed_file_messages: List[str] = []
        failed_file_messages_by_table: Dict[str, List[str]] = {}
        failure_tracker = MigrationFailureTracker()
        missing_files_by_table: Dict[str, List[str]] = {}
        incompatible_files_by_table: Dict[str, List[str]] = {}
        staging_tables: Set[str] = set()
        shared_staging_tables_by_table: Dict[str, str] = {}
        table_run_totals: Dict[str, Dict[str, Any]] = {}
        multi_staging_table_name = (
            staging_name(multi_files_table_name.upper())
            if unified_multi_file_mode and multi_files_table_name
            else None
        )
        shared_staging_table = None
        multi_table_start_time = None
        multi_table_columns_count = 0
        table_exists_cache: Dict[str, bool] = {}
        truncated_tables: Set[str] = set()
        hard_failures_by_table: Dict[str, List[str]] = {}
        allowed_new_columns_by_table: Dict[str, Any] = {}
        schema_override_by_table: Dict[str, Optional[Dict[str, Dict[str, Any]]]] = {}

        def _truncate_before_merge(table):
            if (not drop_and_reload and table not in truncated_tables
                    and _as_bool(_lookup_table_state(last_checked_data, table).get("truncate_data", False))):
                with conn.cursor() as cur:
                    cur.execute(f"TRUNCATE TABLE {Q(sf_database)}.{Q(sf_schema)}.{Q(table)}")
                truncated_tables.add(table)

        try:
            log("[ADLS][MIGRATION] Connecting to GCS")
            gcs = _build_gcs_client(gcs_service_account_json, gcp_project_id)
            log("[ADLS][MIGRATION] GCS connected")

            conn = sf_connect(
                sf_account=sf_account,
                sf_user=sf_user,
                sf_password=sf_password,
                sf_warehouse=sf_warehouse,
                sf_database=sf_database,
                sf_schema=sf_schema,
                sf_role=sf_role,
                private_key=dest_config.get("private_key"),
                passphrase=dest_config.get("passphrase"),
                authentication_type=dest_config.get("authentication_type", "password"),
            )

            try:
                gcs_allowed_url_prefix = f"gcs://{gcs_bucket_name}/"
                existed = ensure_gcs_storage_integration(conn, sf_storage_integration, gcs_allowed_url_prefix)
                try:
                    service_account = get_gcp_service_account(conn, sf_storage_integration)
                    ensure_bucket_iam_for_integration(gcs, gcs_bucket_name, service_account)
                    log("[ADLS][MIGRATION] Storage integration IAM configured")
                except Exception as iam_error:
                    log(f"[ADLS][MIGRATION] Storage integration IAM check: {iam_error if not existed else 'continuing...'}")
                assert_allowed_prefix_matches(conn, gcs_allowed_url_prefix, sf_storage_integration)
            except Exception as preflight_error:
                raise Exception(f"Storage integration validation failed: {preflight_error}") from preflight_error

            if drop_and_reload:
                tables_to_drop = set(table_names)
                with conn.cursor() as drop_cur:
                    for table_name in tables_to_drop:
                        if table_name:
                            log(f"[ADLS][DROP] Dropping Snowflake table {sf_database}.{sf_schema}.{table_name}")
                            drop_cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(table_name)}")

            allowed_new_columns = None
            multi_files_schema_override = None
            if batch_multi_file_mode:
                batch_tables = [multi_files_table_name] if unified_multi_file_mode else sorted(table_to_selected_files)
                for batch_table in batch_tables:
                    batch_files = selected_files if unified_multi_file_mode else table_to_selected_files.get(batch_table, [])
                    if not batch_table or not batch_files:
                        continue
                    try:
                        update_table_status_snowflake(connection_id, batch_table, "in_progress", job_name, migration_session_id)
                    except Exception:
                        pass
                    table_meta = _lookup_table_state(last_checked_data, batch_table)
                    expected_columns_for_multi = table_meta.get("table_fields", {}) if isinstance(table_meta, dict) else {}
                    table_allowed_new_columns = None
                    if expected_columns_for_multi:
                        table_allowed_new_columns = compute_common_new_columns_for_multi_files_sftp(
                            file_client,
                            batch_files,
                            file_type,
                            expected_columns_for_multi,
                            delimiter=reader_kwargs.get("delimiter"),
                            quotechar=reader_kwargs.get("quotechar"),
                            skipheaderlines=reader_kwargs.get("skipheaderlines"),
                            json_mode=reader_kwargs.get("json_mode"),
                            xml_mode=reader_kwargs.get("xml_mode"),
                            escape_char=reader_kwargs.get("escape_char"),
                            null_sequence=reader_kwargs.get("null_sequence"),
                            skip_footer_lines=reader_kwargs.get("skip_footer_lines"),
                            line_separator=reader_kwargs.get("line_separator"),
                        )
                    table_schema_override = _build_multi_file_schema_override(
                        file_client,
                        batch_files,
                        file_type,
                        reader_kwargs,
                    )
                    if unified_multi_file_mode:
                        allowed_new_columns = table_allowed_new_columns
                        multi_files_schema_override = table_schema_override
                    else:
                        allowed_new_columns_by_table[batch_table] = table_allowed_new_columns
                        schema_override_by_table[batch_table] = table_schema_override

            for file_key in selected_files:
                table = (
                    file_to_table_lookup.get(file_key)
                    if multi_table_mapping_mode
                    else multi_files_table_name if unified_multi_file_mode
                    else _table_for_file(file_key, single_file_map)
                )
                table = sanitize_identifier(table)
                file_start_time = datetime.now(timezone.utc)
                gcs_urls: List[str] = []
                current_staging_table = (
                    shared_staging_table
                    if unified_multi_file_mode
                    else shared_staging_tables_by_table.get(table) if multi_table_mapping_mode
                    else None
                )
                first_chunk_df = None
                file_rows = 0
                gcs_table_prefix = _adls_gcs_prefix_for_file(table, file_key, migration_session_id)

                try:
                    stat = file_client.stat(file_key)
                    file_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)

                    ok, reason = _is_adls_file_compatible(
                        file_client,
                        file_key,
                        file_type,
                        delimiter=reader_kwargs.get("delimiter"),
                        quotechar=reader_kwargs.get("quotechar"),
                        skipheaderlines=reader_kwargs.get("skipheaderlines"),
                        escape_char=reader_kwargs.get("escape_char"),
                        skip_footer_lines=reader_kwargs.get("skip_footer_lines"),
                        line_separator=reader_kwargs.get("line_separator"),
                    )
                    if not ok:
                        incompatible_files_by_table.setdefault(table, []).append(file_key)
                        raise ValueError(f"Skipping file '{file_key}': {reason}")

                    table_state = _lookup_table_state(last_checked_data, table)
                    expected_columns = table_state.get("table_fields", {}) if isinstance(table_state, dict) else {}
                    if batch_multi_file_mode and expected_columns:
                        table_allowed_new_columns = (
                            allowed_new_columns
                            if unified_multi_file_mode
                            else allowed_new_columns_by_table.get(table)
                        )
                        is_compatible, _, validation_error = validate_sftp_file_structure_matches(
                            file_client,
                            file_key,
                            file_type,
                            expected_columns,
                            delimiter=reader_kwargs.get("delimiter"),
                            quotechar=reader_kwargs.get("quotechar"),
                            skipheaderlines=reader_kwargs.get("skipheaderlines"),
                            json_mode=reader_kwargs.get("json_mode"),
                            xml_mode=reader_kwargs.get("xml_mode"),
                            escape_char=reader_kwargs.get("escape_char"),
                            null_sequence=reader_kwargs.get("null_sequence"),
                            skip_footer_lines=reader_kwargs.get("skip_footer_lines"),
                            line_separator=reader_kwargs.get("line_separator"),
                            enforce_type_compatibility=True,
                            allowed_new_columns=table_allowed_new_columns,
                        )
                        if not is_compatible:
                            incompatible_files_by_table.setdefault(table, []).append(file_key)
                            raise ValueError(f"Skipping file '{file_key}' for table '{table}': {validation_error}")

                    apply_delta_check = _should_apply_delta_check(load_method, is_manual_migration)
                    truncate_requested = _as_bool(table_state.get("truncate_data", False))
                    if not drop_and_reload and apply_delta_check:
                        last_synced = table_state.get("last_checked_date") if isinstance(table_state, dict) else None
                        last_synced_dt = parse_datetime_from_db(last_synced) if last_synced else None
                        if truncate_requested:
                            last_synced_dt = None
                        if last_synced_dt:
                            table_key = table.upper()
                            if table_key not in table_exists_cache:
                                table_exists_cache[table_key] = _snowflake_table_exists(conn, sf_database, sf_schema, table_key)
                            if not table_exists_cache[table_key]:
                                last_synced_dt = None
                        if safety_cutoff and file_modified > safety_cutoff:
                            log(f"[ADLS][SKIP] Skipping '{file_key}' due to safety interval")
                            if not batch_multi_file_mode:
                                update_table_status_snowflake(connection_id, table, "completed", job_name, migration_session_id, sync_end_time=None)
                                _log_skipped_transfer(
                                    cmp_id=cmp_id,
                                    connection_id=connection_id,
                                    user_id=user_id,
                                    table_name=table,
                                    skipped_at=datetime.now(timezone.utc),
                                    session=session,
                                    job_name=job_name,
                                )
                            skipped_tables_safety.add(table)
                            continue
                        if _should_skip_unchanged_file(last_synced_dt, file_modified):
                            log(f"[ADLS][SKIP] Skipping '{file_key}' because it is not newer than last sync")
                            skip_time = datetime.now(timezone.utc)
                            if not batch_multi_file_mode:
                                update_table_status_snowflake(connection_id, table, "completed", job_name, migration_session_id, skip_time)
                                _log_skipped_transfer(
                                    cmp_id=cmp_id,
                                    connection_id=connection_id,
                                    user_id=user_id,
                                    table_name=table,
                                    skipped_at=skip_time,
                                    session=session,
                                    job_name=job_name,
                                )
                            skipped_tables_delta.add(table)
                            continue
                    elif is_manual_migration and not apply_delta_check:
                        log(
                            f"[ADLS][MIGRATION] Manual migration - delta skip bypassed for "
                            f"'{file_key}' (file_modified={file_modified.isoformat()})"
                        )

                    if not batch_multi_file_mode:
                        update_table_status_snowflake(connection_id, table, "in_progress", job_name, migration_session_id)

                    json_expected_keys_state = {"expected": None}
                    chunks = read_sftp_file_in_chunks(
                        file_client,
                        file_key,
                        file_type=file_type,
                        chunk_size=chunk_count,
                        delimiter=reader_kwargs.get("delimiter"),
                        quotechar=reader_kwargs.get("quotechar"),
                        skipheaderlines=reader_kwargs.get("skipheaderlines"),
                        json_mode=reader_kwargs.get("json_mode"),
                        xml_mode=reader_kwargs.get("xml_mode"),
                        escape_char=reader_kwargs.get("escape_char"),
                        null_sequence=reader_kwargs.get("null_sequence"),
                        skip_footer_lines=reader_kwargs.get("skip_footer_lines"),
                        line_separator=reader_kwargs.get("line_separator"),
                        enforce_row_schema_consistency=True,
                        enforce_json_key_consistency=True,
                        json_expected_keys_state=json_expected_keys_state,
                    )

                    for chunk_df, chunk_num, is_last_chunk, _last_modified, total_rows_val in chunks:
                        if chunk_df.empty:
                            if is_last_chunk:
                                break
                            continue

                        is_first_data_chunk = first_chunk_df is None
                        if is_first_data_chunk:
                            first_chunk_df = chunk_df.copy()
                            if batch_multi_file_mode and expected_columns:
                                inferred_cols = {
                                    sanitize_column_name(str(col)).upper()
                                    for col in chunk_df.columns
                                    if sanitize_column_name(str(col)).upper() not in METADATA_COLS
                                }
                                expected_cols = {
                                    sanitize_column_name(str(col)).upper()
                                    for col in expected_columns.keys()
                                    if sanitize_column_name(str(col)).upper() not in METADATA_COLS
                                }
                                removed_cols = expected_cols - inferred_cols
                                added_cols = inferred_cols - expected_cols
                                table_allowed_new_columns = (
                                    allowed_new_columns
                                    if unified_multi_file_mode
                                    else allowed_new_columns_by_table.get(table)
                                )
                                allowed_new_cols = {
                                    sanitize_column_name(str(col)).upper()
                                    for col in (table_allowed_new_columns or [])
                                }
                                if removed_cols and added_cols and (not allowed_new_cols or added_cols.issubset(allowed_new_cols)):
                                    removed_preview = ", ".join(sorted(removed_cols)[:3])
                                    added_preview = ", ".join(sorted(added_cols)[:3])
                                    rename_message = (
                                        f"Possible column rename detected for file '{file_key}' and table '{table}'. "
                                        f"Removed: {removed_preview}. Added: {added_preview}. "
                                        "Please update schema mapping before migration."
                                    )
                                    incompatible_files_by_table.setdefault(table, []).append(file_key)
                                    raise ValueError(rename_message)

                        should_create_staging_table = (chunk_num == 1) and not current_staging_table
                        if should_create_staging_table:
                            table_staging_name = (
                                multi_staging_table_name
                                if unified_multi_file_mode
                                else staging_name(table.upper()) if multi_table_mapping_mode
                                else None
                            )
                            table_schema_override = (
                                multi_files_schema_override
                                if unified_multi_file_mode
                                else schema_override_by_table.get(table) if multi_table_mapping_mode
                                else None
                            )
                            current_staging_table = ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                custom_pk_mapping.get(table.upper(), []),
                                sf_database=sf_database,
                                sf_schema=sf_schema,
                                staging_table_name=table_staging_name,
                                schema_override=table_schema_override,
                            )
                            staging_tables.add(current_staging_table)
                            if unified_multi_file_mode:
                                shared_staging_table = current_staging_table
                            elif multi_table_mapping_mode:
                                shared_staging_tables_by_table[table] = current_staging_table
                        elif current_staging_table:
                            ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                custom_pk_mapping.get(table.upper(), []),
                                sf_database=sf_database,
                                sf_schema=sf_schema,
                                staging_table_name=current_staging_table,
                                schema_override=(
                                    multi_files_schema_override
                                    if unified_multi_file_mode
                                    else schema_override_by_table.get(table) if multi_table_mapping_mode
                                    else None
                                ),
                            )

                        chunk_df = align_chunk_to_table_column_order(
                            conn,
                            chunk_df,
                            current_staging_table,
                            sf_database=sf_database,
                            sf_schema=sf_schema,
                        )
                        chunk_df = reorder_chunk_columns_for_snowflake(
                            chunk_df,
                            load_method,
                            custom_pk_mapping.get(table.upper(), []),
                        )
                        if batch_multi_file_mode:
                            if multi_table_start_time is None:
                                multi_table_start_time = file_start_time
                            multi_table_columns_count = max(multi_table_columns_count, len(chunk_df.columns))

                        gcs_path = upload_chunk_to_gcs(
                            gcs,
                            chunk_df,
                            gcs_table_prefix,
                            chunk_num,
                            gcs_bucket_name=gcs_bucket_name,
                            delimiter=",",
                            quotechar='"',
                            file_type="csv",
                        )
                        gcs_urls.append(f"gcs://{gcs_bucket_name}/{gcs_path}")
                        file_rows = int(total_rows_val or file_rows)
                        SimpleProgressTracker.accumulate_staging_progress_batch(
                            connection_id=connection_id,
                            migration_session_id=migration_session_id,
                            job_name=job_name,
                            table_name=table,
                            batch_rows=len(chunk_df),
                        )

                    if not gcs_urls:
                        raise ValueError(f"No rows were read from ADLS file '{file_key}'")

                    copy_into_staging_from_gcs_batch(
                        conn,
                        gcs_urls,
                        current_staging_table,
                        sf_database,
                        sf_schema,
                        sf_storage_integration,
                        gcs_bucket_name,
                        delimiter=",",
                        quotechar='"',
                        skipheaderlines=1,
                        file_type="csv",
                        connection_id=connection_id,
                        migration_session_id=str(migration_session_id),
                        job_name=job_name,
                        table_name=table,
                    )

                    if batch_multi_file_mode:
                        processed += 1
                        total_source_rows_read += file_rows
                        processed_tables.add(table)
                        if unified_multi_file_mode:
                            processed_file_keys.append(file_key)
                        processed_file_keys_by_table.setdefault(table, []).append(file_key)
                        skipped_tables_delta.discard(table)
                        migration_end_time = datetime.now(timezone.utc)
                        run = table_run_totals.setdefault(
                            table,
                            {
                                "total_rows": 0,
                                "inserts": 0,
                                "updates": 0,
                                "deletes": 0,
                                "source_rows": 0,
                                "start_time": file_start_time,
                                "end_time": migration_end_time,
                                "columns_data": len(first_chunk_df.columns) if first_chunk_df is not None else 0,
                                "files_done": 0,
                                "staging_table": current_staging_table,
                            },
                        )
                        run["source_rows"] += file_rows
                        run["start_time"] = min(run["start_time"], file_start_time)
                        run["end_time"] = max(run["end_time"], migration_end_time)
                        if first_chunk_df is not None:
                            run["columns_data"] = max(run["columns_data"], len(first_chunk_df.columns))
                        run["files_done"] += 1
                        run["staging_table"] = current_staging_table
                        continue

                    _truncate_before_merge(table)
                    merge_result = merge_staging_into_final(
                        conn,
                        table,
                        current_staging_table,
                        load_method,
                        custom_pk_mapping.get(table.upper(), []),
                        sf_database,
                        sf_schema,
                        source_file_key=file_key,
                        delete_missing_rows=(not drop_and_reload) and load_method == "upsert_custom_key",
                    )
                    inserts = merge_result.get("inserted", 0)
                    updates = merge_result.get("updated", 0)
                    deletes = merge_result.get("deleted", 0)
                    effective_rows = inserts + updates + deletes
                    total_inserts += inserts
                    total_updates += updates
                    total_deletes += deletes
                    total_rows += effective_rows
                    total_source_rows_read += file_rows
                    processed += 1
                    processed_tables.add(table)
                    skipped_tables_delta.discard(table)

                    migration_end_time = datetime.now(timezone.utc)
                    run = table_run_totals.setdefault(
                        table,
                        {
                            "total_rows": 0,
                            "inserts": 0,
                            "updates": 0,
                            "deletes": 0,
                            "start_time": file_start_time,
                            "end_time": migration_end_time,
                            "columns_data": len(first_chunk_df.columns) if first_chunk_df is not None else 0,
                            "files_done": 0,
                        },
                    )
                    run["total_rows"] += effective_rows
                    run["inserts"] += inserts
                    run["updates"] += updates
                    run["deletes"] += deletes
                    run["start_time"] = min(run["start_time"], file_start_time)
                    run["end_time"] = max(run["end_time"], migration_end_time)
                    if first_chunk_df is not None:
                        run["columns_data"] = max(run["columns_data"], len(first_chunk_df.columns))
                    run["files_done"] += 1

                    if all([connection_id, migration_session_id, job_name, table]):
                        try:
                            SimpleProgressTracker.set_staging_count(
                                connection_id=connection_id,
                                migration_session_id=str(migration_session_id),
                                job_name=job_name,
                                table_name=table,
                                count_value=run["total_rows"] or effective_rows,
                            )
                        except Exception as progress_err:
                            log(f"[ADLS][MIGRATION] Failed to update progress for '{table}': {progress_err}")

                    update_table_status_snowflake(
                        connection_id,
                        table,
                        "in_progress",
                        job_name,
                        migration_session_id,
                    )
                except Exception as file_error:
                    failed += 1
                    failure_detail = _http_error_detail(file_error)
                    failed_file_messages.append(f"{file_key}: {failure_detail}")
                    failed_file_messages_by_table.setdefault(table, []).append(f"{file_key}: {failure_detail}")
                    if isinstance(file_error, FileNotFoundError) or getattr(file_error, "error_code", None) in (
                        "PathNotFound", "BlobNotFound", "FilesystemNotFound",
                    ):
                        missing_files_by_table.setdefault(table, []).append(file_key)
                        if file_mapping_mode == "single_table_per_file":
                            missing_files_single_mode.add(str(file_key).strip())
                    is_skip = (
                        file_key in missing_files_by_table.get(table, [])
                        or file_key in incompatible_files_by_table.get(table, [])
                    )
                    if not (batch_multi_file_mode and is_skip):
                        hard_failures_by_table.setdefault(table, []).append(f"{file_key}: {failure_detail}")
                        failure_tracker.add_failure(table, failure_detail, "ADLS to Snowflake")
                    log(f"[ADLS][ERROR] Failed processing '{file_key}' as table '{table}': {file_error}")
                    if not batch_multi_file_mode:
                        partial_run = table_run_totals.get(table)
                        if partial_run and partial_run.get("files_done", 0) > 0:
                            try:
                                data_transfer_logs(
                                    cmp_id=cmp_id,
                                    connection_id=connection_id,
                                    user_id=user_id,
                                    table_name=table,
                                    start_time=partial_run["start_time"],
                                    end_time=partial_run["end_time"],
                                    columns_data=partial_run["columns_data"],
                                    session=session,
                                    total_rows=partial_run["total_rows"],
                                    inserts=partial_run["inserts"],
                                    updates=partial_run["updates"],
                                    deletes=partial_run["deletes"],
                                    job_name=job_name,
                                )
                            except Exception:
                                pass
                        try:
                            update_table_status_snowflake(connection_id, table, "failed", job_name, migration_session_id)
                        except Exception:
                            pass
                    handle_service_error(
                        error=file_error,
                        context={
                            "job_name": job_name,
                            "log_id": log_id,
                            "cmp_id": cmp_id,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "operation": "MIGRATION",
                            "table_name": table,
                            "is_terminal_failure": False,
                            "service_name": "ADLS to Snowflake",
                            "connection_name": connection_name,
                            "company_name": company_name,
                            "session": migration_session_id,
                            "migration_session_id": migration_session_id,
                        },
                    )

            if unified_multi_file_mode and failed and not processed:
                try:
                    update_table_status_snowflake(connection_id, multi_files_table_name, "failed", job_name, migration_session_id)
                except Exception:
                    pass

            if (unified_multi_file_mode and shared_staging_table and processed > 0
                    and not hard_failures_by_table.get(multi_files_table_name)):
                _truncate_before_merge(multi_files_table_name)
                merge_result = merge_staging_into_final(
                    conn,
                    multi_files_table_name,
                    shared_staging_table,
                    load_method,
                    custom_pk_mapping.get(multi_files_table_name.upper(), []),
                    sf_database,
                    sf_schema,
                    source_file_keys=processed_file_keys,
                    delete_missing_rows=(not drop_and_reload) and load_method == "upsert_custom_key",
                )
                inserts = merge_result.get("inserted", 0)
                updates = merge_result.get("updated", 0)
                deletes = merge_result.get("deleted", 0)
                effective_rows = inserts + updates + deletes
                total_inserts += inserts
                total_updates += updates
                total_deletes += deletes
                total_rows += effective_rows

                log(
                    f"[ADLS][MIGRATION] Final merge for '{multi_files_table_name}': "
                    f"inserted={inserts:,}, updated={updates:,}, deleted={deletes:,} "
                    f"across {processed} file(s) (source rows read: {total_source_rows_read:,})"
                )

                if all([connection_id, migration_session_id, job_name, multi_files_table_name]):
                    try:
                        SimpleProgressTracker.set_staging_count(
                            connection_id=connection_id,
                            migration_session_id=str(migration_session_id),
                            job_name=job_name,
                            table_name=multi_files_table_name,
                            count_value=effective_rows,
                        )
                    except Exception as progress_err:
                        log(f"[ADLS][MIGRATION] Failed to update progress for '{multi_files_table_name}': {progress_err}")

                migration_end_time = datetime.now(timezone.utc)
                update_table_status_snowflake(
                    connection_id,
                    multi_files_table_name,
                    "completed",
                    job_name,
                    migration_session_id,
                    None if multi_files_table_name in skipped_tables_safety else migration_end_time,
                )
                data_transfer_logs(
                    cmp_id=cmp_id,
                    connection_id=connection_id,
                    user_id=user_id,
                    table_name=multi_files_table_name,
                    start_time=multi_table_start_time or datetime.now(timezone.utc),
                    end_time=migration_end_time,
                    columns_data=multi_table_columns_count,
                    session=session,
                    total_rows=effective_rows,
                    inserts=inserts,
                    updates=updates,
                    deletes=deletes,
                    job_name=job_name,
                )

            if multi_table_mapping_mode:
                for table, run in table_run_totals.items():
                    staging_table = run.get("staging_table") or shared_staging_tables_by_table.get(table)
                    files_done = int(run.get("files_done") or 0)
                    if not staging_table or files_done <= 0 or hard_failures_by_table.get(table):
                        continue

                    table_processed_files = processed_file_keys_by_table.get(table, [])
                    _truncate_before_merge(table)
                    merge_result = merge_staging_into_final(
                        conn,
                        table,
                        staging_table,
                        load_method,
                        custom_pk_mapping.get(table.upper(), []),
                        sf_database,
                        sf_schema,
                        source_file_keys=table_processed_files,
                        delete_missing_rows=(not drop_and_reload) and load_method == "upsert_custom_key",
                    )
                    inserts = merge_result.get("inserted", 0)
                    updates = merge_result.get("updated", 0)
                    deletes = merge_result.get("deleted", 0)
                    effective_rows = inserts + updates + deletes
                    total_inserts += inserts
                    total_updates += updates
                    total_deletes += deletes
                    total_rows += effective_rows
                    run["total_rows"] = effective_rows
                    run["inserts"] = inserts
                    run["updates"] = updates
                    run["deletes"] = deletes

                    log(
                        f"[ADLS][MIGRATION] Final merge for mapped table '{table}': "
                        f"inserted={inserts:,}, updated={updates:,}, deleted={deletes:,} "
                        f"across {files_done} file(s) (source rows read: {int(run.get('source_rows') or 0):,})"
                    )

                    if all([connection_id, migration_session_id, job_name, table]):
                        try:
                            SimpleProgressTracker.set_staging_count(
                                connection_id=connection_id,
                                migration_session_id=str(migration_session_id),
                                job_name=job_name,
                                table_name=table,
                                count_value=effective_rows,
                            )
                        except Exception as progress_err:
                            log(f"[ADLS][MIGRATION] Failed to update progress for '{table}': {progress_err}")

                    migration_end_time = datetime.now(timezone.utc)
                    run["end_time"] = max(run.get("end_time") or migration_end_time, migration_end_time)
                    update_table_status_snowflake(
                        connection_id,
                        table,
                        "completed",
                        job_name,
                        migration_session_id,
                        None if table in skipped_tables_safety else run["end_time"],
                    )
                    data_transfer_logs(
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        table_name=table,
                        start_time=run.get("start_time") or datetime.now(timezone.utc),
                        end_time=run["end_time"],
                        columns_data=run.get("columns_data") or 0,
                        session=session,
                        total_rows=effective_rows,
                        inserts=inserts,
                        updates=updates,
                        deletes=deletes,
                        job_name=job_name,
                    )

                for table in table_to_selected_files:
                    if table in processed_tables:
                        continue
                    if failed_file_messages_by_table.get(table):
                        try:
                            update_table_status_snowflake(connection_id, table, "failed", job_name, migration_session_id)
                        except Exception:
                            pass
                    elif table in skipped_tables_delta or table in skipped_tables_safety:
                        skip_time = datetime.now(timezone.utc)
                        try:
                            update_table_status_snowflake(
                                connection_id, table, "completed", job_name, migration_session_id,
                                None if table in skipped_tables_safety else skip_time,
                            )
                            _log_skipped_transfer(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                user_id=user_id,
                                table_name=table,
                                skipped_at=skip_time,
                                session=session,
                                job_name=job_name,
                            )
                        except Exception:
                            pass

            table_warning_details: Dict[str, Dict[str, str]] = {}
            batch_tables_for_status = [multi_files_table_name] if unified_multi_file_mode else sorted(table_to_selected_files)
            if batch_multi_file_mode:
                for status_table in batch_tables_for_status:
                    table_failed_messages = failed_file_messages_by_table.get(status_table, [])
                    skipped_file_keys = sorted(
                        set(
                            incompatible_files_by_table.get(status_table, [])
                            + missing_files_by_table.get(status_table, [])
                        )
                    )
                    processed_for_table = int(table_run_totals.get(status_table, {}).get("files_done") or 0)
                    skipped_count = len(skipped_file_keys)
                    if hard_failures_by_table.get(status_table):
                        table_warning_details[status_table] = {
                            "status": "F",
                            "message": "; ".join(hard_failures_by_table[status_table][:3]),
                        }
                    elif table_failed_messages and processed_for_table == 0:
                        table_warning_details[status_table] = {
                            "status": "F",
                            "message": "; ".join(table_failed_messages[:3]) or "All files failed to process.",
                        }
                    elif skipped_count > 0 and processed_for_table > 0:
                        sample = ", ".join(skipped_file_keys[:2])
                        more = f" (+{skipped_count - 2} more)" if skipped_count > 2 else ""
                        table_warning_details[status_table] = {
                            "status": "P",
                            "message": f"{skipped_count} file(s) skipped: {sample}{more}",
                        }
                    elif table_failed_messages:
                        table_warning_details[status_table] = {
                            "status": "P",
                            "message": "; ".join(table_failed_messages[:3]),
                        }

            failed_table_count = failed
            if batch_multi_file_mode:
                failed_table_count = sum(
                    1 for detail in table_warning_details.values() if detail.get("status") == "F"
                )

            if batch_multi_file_mode:
                for status_table, detail in table_warning_details.items():
                    if detail["status"] == "F":
                        processed_tables.discard(status_table)
                        update_table_status_snowflake(
                            connection_id, status_table, "failed", job_name, migration_session_id,
                        )
                # Complete unchanged/safety-only unified runs without losing the watermark.
                if unified_multi_file_mode and not processed and not failed:
                    update_table_status_snowflake(
                        connection_id, multi_files_table_name, "completed", job_name, migration_session_id,
                        None if multi_files_table_name in skipped_tables_safety else datetime.now(timezone.utc),
                    )
                if failed_table_count and not processed_tables:
                    status_override = "F"
                elif failed or any(detail.get("status") == "P" for detail in table_warning_details.values()):
                    status_override = "P"
                else:
                    status_override = "S"
            else:
                # Do not report completion while a failed status write is pending.
                for failure in failure_tracker.failed_tables:
                    update_table_status_snowflake(
                        connection_id, failure["table_name"], "failed", job_name, migration_session_id,
                    )
                    processed_tables.discard(failure["table_name"])
                status_override = "F" if failed and not processed else ("P" if failed else "S")
            include_operation_counts = load_method not in ("upsert_file_row", "append_file_modified")

            log_migration_completion(
                execution_time=time.time() - start_time,
                total_rows=total_rows,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                session=migration_session_id,
                total_tables=len(set(table_names)) or len(selected_files),
                successful_tables=len(processed_tables),
                failed_tables=failed_table_count,
                job_name=job_name,
                inserts=total_inserts,
                updates=total_updates,
                deletes=total_deletes,
                include_operation_counts=include_operation_counts,
                log_id=log_id,
                status_override=status_override,
                suppress_reason_in_completion=True,
                celery_child_mode=request.celery_child_mode,
            )

            if batch_multi_file_mode and table_warning_details and all([cmp_id, connection_id, user_id, job_name, migration_session_id]):
                for warning_table, detail in table_warning_details.items():
                    warning_detail_for_ui = detail.get("message")
                    log_status_override = detail.get("status")
                    if not warning_detail_for_ui or log_status_override not in {"F", "P"}:
                        continue
                    try:
                        warn_conn = postgres_connection()
                        warn_cursor = warn_conn.cursor()
                        warning_record_id = get_migration_record_id(
                            warn_cursor,
                            job_name=job_name,
                            table_name=warning_table,
                            connection_id=connection_id,
                        )
                        warn_cursor.close()
                        warn_conn.close()
                        if warning_record_id:
                            if log_status_override == "F":
                                updated = update_table_migration_status_log(
                                    migration_record_id=warning_record_id,
                                    message=warning_detail_for_ui,
                                    status="F",
                                    session=migration_session_id,
                                    job_name=job_name,
                                    connection_id=connection_id,
                                )
                                if not updated:
                                    insert_tool_log(
                                        cmp_id,
                                        connection_id,
                                        user_id,
                                        warning_table,
                                        "F",
                                        warning_detail_for_ui,
                                        migration_session_id,
                                        job_name=job_name,
                                        migration_record_id=warning_record_id,
                                    )
                            else:
                                insert_tool_log(
                                    cmp_id,
                                    connection_id,
                                    user_id,
                                    warning_table,
                                    "P",
                                    warning_detail_for_ui,
                                    migration_session_id,
                                    job_name=job_name,
                                    migration_record_id=warning_record_id,
                                )
                    except Exception as warn_log_err:
                        log(f"[ADLS][MIGRATION] Failed to write ADLS partial/failure log: {warn_log_err}")

            if failure_tracker.has_failures():
                try:
                    failure_tracker.send_consolidated_error_email(
                        connection_name=connection_name,
                        service_name="ADLS to Snowflake",
                        user_id=user_id,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        company_name=company_name,
                        is_final_attempt=is_final_attempt,
                    )
                except Exception as email_err:
                    log(f"[ADLS][MIGRATION] Failed to send consolidated failure email: {email_err}")

            try:
                handle_migration_completion(
                    connection_name=connection_name,
                    job_name=job_name,
                    completion_message=(
                        f"ADLS migration completed: {processed}/{len(selected_files)} files, "
                        f"{total_source_rows_read:,} source rows, {total_rows:,} changed rows"
                    ),
                )
            except Exception:
                pass

            try:
                _refresh_after_missing_files()
            except Exception as post_refresh_error:
                log(f"[ADLS][MIGRATION] Post-migration schema refresh failed (non-fatal): {post_refresh_error}")

            return {
                "status": "failed" if status_override == "F" else "success",
                "files_processed": processed,
                "total_rows": total_rows,
                "total_inserts": total_inserts,
                "total_updates": total_updates,
                "total_deleted_recids": total_deletes,
                "execution_time": time.time() - start_time,
                "failed_tables": failed_table_count,
                "successful_tables_count": len(processed_tables),
                "failed_tables_count": failed_table_count,
                "successful_tables_list": list(processed_tables),
                "failed_tables_list": [
                    table_name
                    for table_name, detail in table_warning_details.items()
                    if detail.get("status") == "F"
                ] if batch_multi_file_mode else [
                    failure["table_name"] for failure in failure_tracker.failed_tables
                ],
                "processed_tables": list(processed_tables),
                "skipped_tables_delta": list(skipped_tables_delta),
                "skipped_tables_safety": list(skipped_tables_safety),
                "failed_files": failed_file_messages,
                "single_mode_missing_files_detected": bool(missing_files_single_mode),
                "single_mode_missing_files": sorted(missing_files_single_mode),
            }
        finally:
            if conn:
                try:
                    for staging_table in set(staging_tables):
                        if staging_table:
                            with conn.cursor() as cur:
                                cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(staging_table)}")
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
