"""ADLS source preview and primary-key suggestion service."""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.adls_source_models import ADLSListingTables, ADLSPKSuggestionRequest, ADLSPatternPreviewRequest
from models.amazon_s3_models import DelimitedFileOptions, FileMappingMethod, FileType, JSONMode, LoadMethod
from services.amazon_s3.service import extract_columns_for_pk_suggestion, _fetch_snowflake_tables_from_config
from services.snowflake.as_source.to_adls import (
    ADLS_CLIENT_KWARGS,
    _validate_adls_storage_account_dns,
    _map_adls_client_error,
    _require_adls_field,
    _validate_adls_destination_access,
)
from services.source_file_helpers.file_format_readers import read_dataframe_sample
from services.source_file_helpers.file_path_utils import normalize_root_path, relative_to_root, safe_path_basename
from services.source_file_helpers.file_source_base import (
    SourceFileEntry,
    build_basename_counts,
    infer_columns_from_dataframe,
    resolve_single_file_mapped_name,
    sanitize_identifier,
    table_from_file_key,
    unique_table_name,
    utc_isoformat,
)
from services.source_file_helpers.file_source_mapping import resolve_file_entries
from utils.database_utils import postgres_connection
from utils.s3_utils import log

def _get_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _get_nonnegative_int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


ADLS_VALIDATION_CLIENT_KWARGS = {
    "retry_total": _get_nonnegative_int_env("ADLS_VALIDATION_RETRY_TOTAL", 0),
    "retry_connect": _get_nonnegative_int_env("ADLS_VALIDATION_RETRY_CONNECT", 0),
    "retry_read": _get_nonnegative_int_env("ADLS_VALIDATION_RETRY_READ", 0),
    "retry_status": _get_nonnegative_int_env("ADLS_VALIDATION_RETRY_STATUS", 0),
    "connection_timeout": _get_positive_int_env("ADLS_VALIDATION_CONNECTION_TIMEOUT", 3),
    "read_timeout": _get_positive_int_env("ADLS_VALIDATION_READ_TIMEOUT", 5),
}


def _build_adls_service_client(
    adls_config: Dict[str, Any],
    *,
    validation_request: bool = False,
) -> Any:
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
        client_kwargs = ADLS_VALIDATION_CLIENT_KWARGS if validation_request else ADLS_CLIENT_KWARGS
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            **(ADLS_VALIDATION_CLIENT_KWARGS if validation_request else {}),
        )
        if validation_request:
            # Authenticate before making the storage request so invalid tenant,
            # client ID, and secret errors are returned directly and promptly.
            credential.get_token("https://storage.azure.com/.default")
        account_url = f"https://{account_name}.dfs.core.windows.net"
        return DataLakeServiceClient(account_url=account_url, credential=credential, **client_kwargs)
    except Exception as exc:
        container_name = str(adls_config.get("container_name") or "").strip()
        raise _map_adls_client_error(exc, account_name=account_name, container_name=container_name) from exc


logger = logging.getLogger(__name__)
SUPPORTED_SOURCE_FILE_TYPES = {"csv", "tsv", "json", "xml", "avro", "parquet"}
# Cap bytes pulled from ADLS for schema/PK inference — avoids OOM on multi-GB files.
ADLS_SCHEMA_SAMPLE_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _config_from_preview(data: ADLSPatternPreviewRequest) -> Dict[str, Any]:
    config = data.resolved_config()
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="ADLS config is required")
    return config


def _config_from_listing(data: ADLSListingTables) -> Dict[str, Any]:
    config = data.resolved_config()
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="ADLS config is required")
    return config


def _validate_source_config(config: Dict[str, Any]) -> None:
    _require_adls_field(config, "account_name", "storage_account_name")
    _require_adls_field(config, "container_name", "file_system", "filesystem")
    _require_adls_field(config, "tenant_id")
    _require_adls_field(config, "client_id")
    _require_adls_field(config, "client_secret")

    file_type = str(config.get("file_type") or "").strip().lower()
    if file_type and file_type not in SUPPORTED_SOURCE_FILE_TYPES:
        raise HTTPException(
            status_code=422,
            detail="Invalid file_type; expected csv, tsv, json, xml, avro, or parquet",
        )


def _effective_root_folder(config: Dict[str, Any]) -> str:
    folder_name = (
        config.get("folder_name")
        or config.get("root_folder")
        or config.get("folder_path")
        or config.get("base_folder_path")
        or config.get("adls_base_prefix")
        or ""
    )
    return normalize_root_path(folder_name, "").lstrip("/")


def _file_type_from_path(path: str) -> str:
    basename = safe_path_basename(path)
    if "." not in basename:
        return ""
    return basename.rsplit(".", 1)[-1].lower()


def _parse_mapping(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw in {"null", "undefined"}:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_single_file_map(config: Dict[str, Any]) -> Dict[str, str]:
    raw_map = _parse_mapping(config.get("single_file_table_mapping"))
    return {
        str(file_key).strip().lstrip("/"): str(table_name).strip()
        for file_key, table_name in raw_map.items()
        if str(file_key).strip() and str(table_name).strip()
    }


def _build_reader_kwargs(config: Dict[str, Any], file_type: str) -> Dict[str, Any]:
    csv_options = _parse_mapping(config.get("csv_options"))
    tsv_options = _parse_mapping(config.get("tsv_options"))
    opts = csv_options if file_type == "csv" else tsv_options if file_type == "tsv" else {}

    def pick(option_key: str, flat_key: str, default=None):
        if option_key in opts:
            return opts.get(option_key)
        return config.get(flat_key, default)

    return {
        "file_type": file_type,
        "delimiter": pick("delimiter", "delimiter"),
        "quotechar": pick("quotechar", "quotechar"),
        "escape_char": pick("escape_char", "escape_char"),
        "skipheaderlines": pick("skipheaderlines", "skipheaderlines"),
        "skip_footer_lines": pick("skip_footer_lines", "skip_footer_lines"),
        "line_separator": pick("line_separator", "line_separator"),
        "json_mode": config.get("json_mode"),
        "xml_mode": config.get("xml_mode"),
        "sample_rows": int(config.get("sample_rows") or 1000),
    }


def _validate_schema_file_types(
    table_groups: Dict[str, List[SourceFileEntry]],
    configured_file_type: str,
) -> None:
    """Fail Update Schema before sampling a file with a conflicting format."""
    expected_type = str(configured_file_type or "").strip().lower()
    if not expected_type:
        return

    for files_for_table in table_groups.values():
        for entry in files_for_table or []:
            detected_type = str(
                entry.inferred_file_type or _file_type_from_path(entry.file_key)
            ).strip().lower()
            if detected_type and detected_type != expected_type:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Update schema failed: configured file_type "
                        f"'{expected_type}' does not match file '{entry.file_key}' "
                        f"(detected '{detected_type}')."
                    ),
                )


def _parse_custom_primary_key(value: Any) -> Dict[str, Any]:
    parsed = _parse_mapping(value)
    return parsed if isinstance(parsed, dict) else {}


def _load_existing_file_metadata(connection_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    if not connection_id:
        return {}

    existing_file_metadata: Dict[str, Dict[str, Any]] = {}
    try:
        conn_db = postgres_connection()
        cursor_db = conn_db.cursor()
        cursor_db.execute(
            """
            SELECT folder_path, "table", selected, last_synced, primary_keys, is_history
            FROM source_table_selection
            WHERE connection_id = %s;
            """,
            (connection_id,),
        )
        for file_path, table_name, selected, last_synced, primary_keys, is_history in (cursor_db.fetchall() or []):
            file_path_key = str(file_path or "").strip()
            if not file_path_key:
                continue

            pk_list = []
            if isinstance(primary_keys, list):
                pk_list = [str(pk).strip() for pk in primary_keys if str(pk).strip()]
            elif isinstance(primary_keys, str):
                try:
                    parsed_pk = json.loads(primary_keys)
                    if isinstance(parsed_pk, list):
                        pk_list = [str(pk).strip() for pk in parsed_pk if str(pk).strip()]
                except Exception:
                    pk_list = []

            meta = {
                "table": sanitize_identifier(str(table_name).strip()) if str(table_name or "").strip() else "",
                "selected": selected,
                "last_synced": last_synced.isoformat() if hasattr(last_synced, "isoformat") else str(last_synced or ""),
                "is_migrated": bool(last_synced),
                "primary_keys": pk_list,
                "is_history": is_history,
            }
            if file_path_key not in existing_file_metadata:
                existing_file_metadata[file_path_key] = meta
            else:
                def _parse_synced(value: str):
                    raw = str(value or "").strip()
                    if not raw:
                        return None
                    try:
                        return datetime.fromisoformat(raw)
                    except Exception:
                        return None

                current = existing_file_metadata[file_path_key]
                current_dt = _parse_synced(current.get("last_synced"))
                incoming_dt = _parse_synced(meta.get("last_synced"))
                if incoming_dt and (not current_dt or incoming_dt > current_dt):
                    existing_file_metadata[file_path_key] = meta
        cursor_db.close()
        conn_db.close()
    except Exception as exc:
        logger.warning("Could not load existing ADLS file metadata: %s", exc)

    return existing_file_metadata


def _fetch_mapping_from_db(connection_id: Optional[int]) -> Dict[str, str]:
    if not connection_id:
        return {}
    try:
        from utils.database_utils import get_s3_mapping

        existing_mapping = get_s3_mapping(connection_id)
        if (
            existing_mapping
            and str(existing_mapping.get("file_mapping_mode") or "").strip() == "single_table_per_file"
        ):
            db_map = existing_mapping.get("single_file_table_mapping") or {}
            if isinstance(db_map, dict):
                return {
                    str(file_key).strip().lstrip("/"): str(table_name).strip()
                    for file_key, table_name in db_map.items()
                    if str(file_key).strip() and str(table_name).strip()
                }
    except Exception as exc:
        logger.warning("Could not load ADLS file mapping from database: %s", exc)
    return {}


def _get_adls_file_system_client(config: Dict[str, Any]):
    account_name = _require_adls_field(config, "account_name", "storage_account_name")
    container_name = _require_adls_field(config, "container_name", "file_system", "filesystem")
    service_client = _build_adls_service_client(config, validation_request=True)
    file_system_client = service_client.get_file_system_client(container_name)
    _validate_adls_destination_access(
        file_system_client,
        account_name=account_name,
        container_name=container_name,
    )
    return file_system_client, account_name, container_name


def _list_adls_files(file_system_client, root_folder: str, *, include_subfolders: bool) -> List[SourceFileEntry]:
    path_arg = root_folder or None
    entries: List[SourceFileEntry] = []
    paths = file_system_client.get_paths(path=path_arg, recursive=include_subfolders)

    for path_info in paths:
        if getattr(path_info, "is_directory", False):
            continue
        file_key = str(getattr(path_info, "name", "") or "").strip().lstrip("/")
        if not file_key:
            continue
        relative_path = relative_to_root(file_key, root_folder)
        entries.append(
            SourceFileEntry(
                file_key=file_key,
                relative_path=relative_path,
                basename=safe_path_basename(file_key),
                size_bytes=getattr(path_info, "content_length", None),
                modified_at=getattr(path_info, "last_modified", None),
                inferred_file_type=_file_type_from_path(file_key),
            )
        )

    return entries


def _open_adls_file_sample(
    file_system_client,
    file_key: str,
    *,
    max_bytes: int = ADLS_SCHEMA_SAMPLE_MAX_BYTES,
) -> io.BytesIO:
    """Download only the leading bytes of an ADLS file for schema/PK sampling."""
    normalized_key = str(file_key or "").lstrip("/")
    file_client = file_system_client.get_file_client(normalized_key)

    file_size = None
    try:
        props = file_client.get_file_properties()
        file_size = getattr(props, "size", None)
    except Exception as exc:
        logger.warning("Could not read ADLS file size for %s: %s", normalized_key, exc)

    download_length = int(max_bytes)
    if file_size is not None and file_size >= 0:
        download_length = min(int(file_size), int(max_bytes))

    if download_length <= 0:
        return io.BytesIO(b"")

    if file_size is not None and file_size > download_length:
        logger.info(
            "ADLS schema sample truncated | file=%s size_bytes=%s sample_bytes=%s",
            normalized_key,
            file_size,
            download_length,
        )

    download = file_client.download_file(offset=0, length=download_length)
    return io.BytesIO(download.readall())


def _open_adls_file(file_system_client, file_key: str) -> io.BytesIO:
    """Download an entire ADLS file (used only when a full read is required)."""
    file_client = file_system_client.get_file_client(str(file_key or "").lstrip("/"))
    download = file_client.download_file()
    return io.BytesIO(download.readall())


def _get_adls_file_modified_at(file_system_client, file_key: str) -> Optional[datetime]:
    try:
        props = file_system_client.get_file_client(str(file_key or "").lstrip("/")).get_file_properties()
        return getattr(props, "last_modified", None)
    except Exception:
        return None


def _add_metadata_to_df(df, file_key: str, last_modified: Optional[datetime]):
    if df is None or df.empty:
        return df
    import pandas as pd

    source_file_key = str(file_key).strip()
    if "_FILE" not in df.columns:
        df.insert(0, "_FILE", source_file_key)
    else:
        df["_FILE"] = source_file_key

    if "_ROW" not in df.columns:
        df.insert(1, "_ROW", range(len(df)))
    else:
        df["_ROW"] = df["_ROW"].astype(int)

    lm_utc = pd.to_datetime(last_modified or datetime.now(timezone.utc), utc=True)
    ds_utc = pd.Timestamp.now(tz="UTC")

    if "_MODIFIED" not in df.columns:
        df.insert(2, "_MODIFIED", lm_utc)
    else:
        df["_MODIFIED"] = lm_utc

    if "_SPECT_SYNCED" not in df.columns:
        df.insert(3, "_SPECT_SYNCED", ds_utc)
    else:
        df["_SPECT_SYNCED"] = ds_utc

    if "_SPECT_DELETED" not in df.columns:
        df.insert(4, "_SPECT_DELETED", False)
    else:
        df["_SPECT_DELETED"] = False

    return df


def _reader_options(source_config) -> Optional[DelimitedFileOptions]:
    if source_config.file_type == FileType.csv:
        return source_config.csv_options or DelimitedFileOptions()
    if source_config.file_type == FileType.tsv:
        return source_config.tsv_options or DelimitedFileOptions()
    return None


def _read_pk_state(connection_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    table_pk_state: Dict[str, Dict[str, Any]] = {}
    if not connection_id:
        return table_pk_state

    try:
        conn_db = postgres_connection()
        cur_db = conn_db.cursor()
        cur_db.execute(
            """
            SELECT "table", primary_keys, last_synced
            FROM source_table_selection
            WHERE connection_id = %s
            """,
            (connection_id,),
        )
        for table_name, primary_keys_raw, last_synced in (cur_db.fetchall() or []):
            table_key = str(table_name or "").strip().upper()
            if not table_key:
                continue

            existing_primary_keys = []
            if isinstance(primary_keys_raw, list):
                existing_primary_keys = [str(pk).strip() for pk in primary_keys_raw if str(pk).strip()]
            elif isinstance(primary_keys_raw, str):
                try:
                    parsed_pk = json.loads(primary_keys_raw)
                    if isinstance(parsed_pk, list):
                        existing_primary_keys = [str(pk).strip() for pk in parsed_pk if str(pk).strip()]
                except Exception:
                    existing_primary_keys = []

            table_pk_state[table_key] = {
                "pk_locked": bool(last_synced) and bool(existing_primary_keys),
                "existing_primary_keys": existing_primary_keys,
            }
        cur_db.close()
        conn_db.close()
    except Exception as exc:
        log(f"[ADLS][PK_SUGGEST] Failed to read PK lock metadata: {exc}")

    return table_pk_state


def _build_adls_table_to_files_mapping(tables_result: Dict[str, Any]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    existing_mapping = tables_result.get("table_to_files_mapping") or {}
    if isinstance(existing_mapping, dict):
        for table_name, files in existing_mapping.items():
            table_key = sanitize_identifier(str(table_name or "").strip())
            if not table_key or not isinstance(files, list):
                continue
            mapping[table_key] = [
                str(file_key).strip()
                for file_key in files
                if str(file_key).strip()
            ]

    for table_info in tables_result.get("tables") or []:
        table_key = sanitize_identifier(str(table_info.get("table") or "").strip())
        if not table_key:
            continue
        grouped_files = [
            str(file_key).strip()
            for file_key in (table_info.get("grouped_files") or [])
            if str(file_key).strip()
        ]
        if not grouped_files:
            continue
        existing_files = mapping.setdefault(table_key, [])
        for file_key in grouped_files:
            if file_key not in existing_files:
                existing_files.append(file_key)

    return {table: files for table, files in mapping.items() if files}


def _matching_adls_file_key(candidate: str, existing_file_keys: set[str]) -> Optional[str]:
    raw = str(candidate or "").strip().lstrip("/")
    if not raw:
        return None
    if raw in existing_file_keys:
        return raw
    for file_key in existing_file_keys:
        if file_key.endswith("/" + raw) or raw.endswith("/" + file_key):
            return file_key
    basename_matches = [key for key in existing_file_keys if key.rsplit("/", 1)[-1] == raw]
    return basename_matches[0] if len(basename_matches) == 1 else None


def _resolve_adls_table_groups_from_mapping(
    table_to_files_mapping: Any,
    entries: List[SourceFileEntry],
) -> Dict[str, List[SourceFileEntry]]:
    if not isinstance(table_to_files_mapping, dict):
        return {}

    entry_by_key = {entry.file_key: entry for entry in entries}
    existing_file_keys = set(entry_by_key.keys())
    table_groups: Dict[str, List[SourceFileEntry]] = {}

    for raw_table_name, raw_files in table_to_files_mapping.items():
        table_name = sanitize_identifier(str(raw_table_name or "").strip())
        if not table_name or not isinstance(raw_files, list):
            continue

        seen: set[str] = set()
        for raw_file_key in raw_files:
            file_key = _matching_adls_file_key(str(raw_file_key or ""), existing_file_keys)
            if not file_key or file_key in seen:
                continue
            seen.add(file_key)
            table_groups.setdefault(table_name, []).append(entry_by_key[file_key])

    return {table: files for table, files in table_groups.items() if files}


def _persist_adls_table_to_files_mapping(connection_id: int, tables_result: Dict[str, Any]) -> None:
    mapping_mode = str(
        tables_result.get("file_mapping_mode")
        or tables_result.get("file_mapping_method")
        or ""
    ).strip()
    if mapping_mode != "multi_files_single_table":
        return

    table_to_files_mapping = _build_adls_table_to_files_mapping(tables_result)
    if not table_to_files_mapping:
        return

    conn_db = None
    cursor_db = None
    try:
        conn_db = postgres_connection()
        cursor_db = conn_db.cursor()
        cursor_db.execute(
            """
            UPDATE source_file_mapping
            SET table_to_files_mapping = %s::jsonb,
                updated_at = NOW()
            WHERE connection_id = %s;
            """,
            (json.dumps(table_to_files_mapping), connection_id),
        )
        conn_db.commit()
    except Exception as exc:
        if conn_db is not None:
            try:
                conn_db.rollback()
            except Exception:
                pass
        logger.warning("Could not persist ADLS table_to_files_mapping: %s", exc)
    finally:
        try:
            if cursor_db is not None:
                cursor_db.close()
        except Exception:
            pass
        try:
            if conn_db is not None:
                conn_db.close()
        except Exception:
            pass


class ADLSSourceService:
    @staticmethod
    def list_files(data: ADLSListingTables) -> Dict[str, Any]:
        config = _config_from_listing(data)
        _validate_source_config(config)
        file_system_client, account_name, container_name = _get_adls_file_system_client(config)
        root_folder = _effective_root_folder(config)
        include_subfolders = _as_bool(config.get("include_subfolders"), default=False)

        try:
            files = _list_adls_files(file_system_client, root_folder, include_subfolders=include_subfolders)
        except Exception as exc:
            raise _map_adls_client_error(
                exc,
                account_name=account_name,
                container_name=container_name,
                path=root_folder,
            ) from exc

        files = sorted(files, key=lambda item: item.file_key.lower())
        file_mapping_mode = str(
            config.get("file_mapping_method")
            or config.get("file_mapping_mode")
            or "single_table_per_file"
        ).strip()
        single_file_map = _normalize_single_file_map(config)

        if not single_file_map and file_mapping_mode == "single_table_per_file":
            single_file_map = _fetch_mapping_from_db(data.connection_id)

        mapped_lookup = {}
        if file_mapping_mode == "single_table_per_file" and single_file_map:
            for mapped_file_key, mapped_table in single_file_map.items():
                file_key = str(mapped_file_key or "").strip().lstrip("/")
                mapped_table_name = sanitize_identifier(str(mapped_table or "").strip())
                if file_key and mapped_table_name:
                    mapped_lookup[file_key] = mapped_table_name
        elif file_mapping_mode == "multi_files_single_table":
            table_to_files = config.get("table_to_files_mapping") or {}
            if isinstance(table_to_files, dict):
                for table_name, mapping_info in table_to_files.items():
                    mapped_table_name = sanitize_identifier(str(table_name or "").strip())
                    if not mapped_table_name:
                        continue
                    file_list = mapping_info if isinstance(mapping_info, list) else []
                    for mapped_file_key in file_list:
                        file_key = str(mapped_file_key or "").strip().lstrip("/")
                        if file_key:
                            mapped_lookup[file_key] = mapped_table_name

        existing_file_metadata = _load_existing_file_metadata(data.connection_id)
        existing_sf_tables = None
        try:
            if config.get("destination_config"):
                existing_sf_tables = _fetch_snowflake_tables_from_config(config.get("destination_config"))
        except Exception as exc:
            logger.warning("Failed to check Snowflake tables for ADLS listing: %s", exc)

        basename_counts = build_basename_counts([entry.file_key for entry in files])
        used_table_names = set()
        tables = []
        for entry in files:
            key = entry.file_key
            existing_meta = existing_file_metadata.get(key) or {}
            locked_table_name = sanitize_identifier(str(existing_meta.get("table") or "").strip())
            is_migrated = bool(existing_meta.get("is_migrated"))
            mapped_table = mapped_lookup.get(key)

            if not mapped_table and file_mapping_mode == "single_table_per_file":
                mapped_table = resolve_single_file_mapped_name(
                    key,
                    single_file_map,
                    basename_counts=basename_counts,
                )

            snowflake_table_exists = None
            if existing_sf_tables is not None:
                if is_migrated and locked_table_name:
                    snowflake_table_exists = locked_table_name.upper() in existing_sf_tables
                elif mapped_table:
                    snowflake_table_exists = str(mapped_table).upper() in existing_sf_tables

            lock_table_name = bool(
                is_migrated
                and locked_table_name
                and (snowflake_table_exists is True)
            )

            if (
                mapped_table
                and existing_sf_tables is not None
                and snowflake_table_exists is False
                and is_migrated
            ):
                mapped_table = None

            table_name = unique_table_name(
                key,
                locked_table_name if lock_table_name else mapped_table,
                used_table_names,
            )

            tables.append(
                {
                    "table": table_name,
                    "file_key": key,
                    "relative_path": entry.relative_path,
                    "basename": entry.basename,
                    "size_bytes": entry.size_bytes,
                    "last_modified": utc_isoformat(entry.modified_at),
                    "file_type": entry.inferred_file_type or None,
                    "already_mapped": bool(mapped_table),
                    "mapped_table": mapped_table,
                    "is_migrated": is_migrated,
                    "table_name_locked": bool(lock_table_name),
                    "locked_table_name": locked_table_name if lock_table_name else None,
                    "locked_primary_keys": existing_meta.get("primary_keys") if lock_table_name else [],
                    "locked_last_synced": existing_meta.get("last_synced") or None,
                }
            )

        return {
            "tables": tables,
            "files_count": len(files),
            "folder_name": root_folder,
            "include_subfolders": include_subfolders,
            "file_type": config.get("file_type") or None,
        }

    @staticmethod
    def list_tables_with_schema(data: ADLSListingTables) -> Dict[str, Any]:
        config = _config_from_listing(data)
        _validate_source_config(config)
        # File selection is intentionally not filtered by type, but the reader
        # still needs the configured format to parse the representative file.
        file_type = str(config.get("file_type") or "").strip().lower()
        file_system_client, account_name, container_name = _get_adls_file_system_client(config)
        root_folder = _effective_root_folder(config)
        include_subfolders = _as_bool(config.get("include_subfolders"), default=False)

        try:
            all_entries = _list_adls_files(file_system_client, root_folder, include_subfolders=include_subfolders)
        except Exception as exc:
            raise _map_adls_client_error(
                exc,
                account_name=account_name,
                container_name=container_name,
                path=root_folder,
            ) from exc

        all_entries = sorted(all_entries, key=lambda item: item.file_key.lower())
        file_mapping_mode = str(
            config.get("file_mapping_method")
            or config.get("file_mapping_mode")
            or "single_table_per_file"
        ).strip()
        single_file_map = _normalize_single_file_map(config)
        if not single_file_map and file_mapping_mode == "single_table_per_file":
            single_file_map = _fetch_mapping_from_db(data.connection_id)

        custom_primary_key = _parse_custom_primary_key(config.get("custom_primary_key"))
        load_method = str(config.get("load_method") or "upsert_file_row").strip()
        if load_method == "upsert_custom_key" and not custom_primary_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    "custom_primary_key must be provided as a non-empty dict in the request "
                    "when load_method is 'upsert_custom_key'"
                ),
            )

        existing_file_keys = {entry.file_key for entry in all_entries}
        stale_single_table_names = set()
        if file_mapping_mode == "single_table_per_file" and single_file_map:
            for mapped_file_key, mapped_table in single_file_map.items():
                matched = any(
                    key == mapped_file_key
                    or key.endswith("/" + mapped_file_key)
                    or mapped_file_key.endswith("/" + key)
                    for key in existing_file_keys
                )
                if not matched:
                    normalized_table = sanitize_identifier(str(mapped_table).strip())
                    if normalized_table:
                        stale_single_table_names.add(normalized_table)

        if not all_entries:
            return {
                "tables": [],
                "files_count": 0,
                "compatible_files_count": 0,
                "incompatible_files_count": 0,
                "folder_name": root_folder,
                "base_folder_path": root_folder,
                "include_subfolders": include_subfolders,
                "file_mapping_method": file_mapping_mode,
                "single_file_table_mapping": single_file_map,
                "table_to_files_mapping": {},
                "cmp_id": data.cmp_id,
                "user_id": data.user_id,
                "source_id": config.get("source_id"),
                "prune_missing_tables": bool(stale_single_table_names),
                "stale_tables_to_prune": sorted(stale_single_table_names),
            }

        existing_file_metadata = _load_existing_file_metadata(data.connection_id)
        existing_sf_tables = None
        try:
            if config.get("destination_config"):
                existing_sf_tables = _fetch_snowflake_tables_from_config(config.get("destination_config"))
        except Exception as exc:
            logger.warning("Failed to check Snowflake tables for ADLS schema listing: %s", exc)

        mapped_table_groups = {}
        if file_mapping_mode == "multi_files_single_table":
            mapped_table_groups = _resolve_adls_table_groups_from_mapping(
                config.get("table_to_files_mapping"),
                all_entries,
            )

        if mapped_table_groups:
            table_groups = mapped_table_groups
            meta = {
                "file_mapping_method": file_mapping_mode,
                "candidate_files_count": sum(len(files) for files in mapped_table_groups.values()),
            }
        else:
            table_groups, meta = resolve_file_entries(
                all_entries,
                single_file_table_mapping=single_file_map,
                file_mapping_method=file_mapping_mode,
                multi_files_prefix=config.get("multi_files_prefix"),
                multi_files_table_name=config.get("multi_files_table_name"),
            )

        _validate_schema_file_types(table_groups, file_type)

        tables: List[Dict[str, Any]] = []
        grouped_file_mapping: Dict[str, List[str]] = {}
        reader_kwargs = _build_reader_kwargs(config, file_type)

        for table_name in sorted(table_groups.keys()):
            files_for_table = table_groups.get(table_name) or []
            if not files_for_table:
                continue

            representative = files_for_table[0]
            remote_file = _open_adls_file_sample(file_system_client, representative.file_key)
            sample_df = read_dataframe_sample(remote_file, **reader_kwargs)
            sample_df = _add_metadata_to_df(sample_df, representative.file_key, representative.modified_at)
            columns = infer_columns_from_dataframe(sample_df, load_method=load_method)

            if load_method == "upsert_custom_key" and custom_primary_key:
                table_name_upper = str(table_name).upper()
                pk_cols = None
                for key, cols in custom_primary_key.items():
                    if str(key).upper() == table_name_upper:
                        pk_cols = [str(col).upper() for col in cols]
                        break
                if pk_cols:
                    for col_name, column_meta in columns.items():
                        if str(col_name).upper() in pk_cols:
                            column_meta["is_primary_key"] = True
                            column_meta["data_type"] = "VARCHAR(512)"

            file_key_to_use = representative.file_key
            if file_mapping_mode == "single_table_per_file" and single_file_map:
                for mapped_file_key in single_file_map.keys():
                    if (
                        representative.file_key == mapped_file_key
                        or representative.file_key.endswith("/" + mapped_file_key)
                        or mapped_file_key.endswith("/" + representative.file_key)
                    ):
                        file_key_to_use = mapped_file_key
                        break

            lookup_key = file_key_to_use
            if file_mapping_mode == "multi_files_single_table" and root_folder and not mapped_table_groups:
                lookup_key = root_folder
            existing_meta = existing_file_metadata.get(str(lookup_key or "").strip()) or {}
            locked_table_name = sanitize_identifier(str(existing_meta.get("table") or "").strip())
            is_migrated = bool(existing_meta.get("is_migrated"))

            snowflake_table_exists = None
            if existing_sf_tables is not None:
                if is_migrated and locked_table_name:
                    snowflake_table_exists = locked_table_name.upper() in existing_sf_tables
                elif table_name:
                    snowflake_table_exists = str(table_name).upper() in existing_sf_tables

            lock_table_name = bool(
                is_migrated
                and locked_table_name
                and (snowflake_table_exists is True)
            )

            tables.append(
                {
                    "table": table_name,
                    "file_key": file_key_to_use,
                    "relative_path": representative.relative_path,
                    "basename": representative.basename,
                    "last_modified": utc_isoformat(representative.modified_at),
                    "columns": columns,
                    "grouped_files": [entry.file_key for entry in files_for_table],
                    "already_mapped": True,
                    "mapped_table": table_name,
                    "is_migrated": is_migrated,
                    "table_name_locked": bool(lock_table_name),
                    "locked_table_name": locked_table_name if lock_table_name else None,
                    "locked_primary_keys": existing_meta.get("primary_keys") if lock_table_name else [],
                    "locked_last_synced": existing_meta.get("last_synced") or None,
                }
            )
            grouped_file_mapping[table_name] = [entry.file_key for entry in files_for_table]

        return {
            "tables": tables,
            "files_count": meta.get("candidate_files_count", len(all_entries)),
            "compatible_files_count": len(all_entries),
            "incompatible_files_count": 0,
            "folder_name": root_folder,
            "base_folder_path": root_folder,
            "include_subfolders": include_subfolders,
            "file_type": config.get("file_type") or None,
            "file_mapping_method": meta.get("file_mapping_method") or file_mapping_mode,
            "single_file_table_mapping": single_file_map,
            "table_to_files_mapping": grouped_file_mapping,
            "cmp_id": data.cmp_id,
            "user_id": data.user_id,
            "source_id": config.get("source_id"),
            "prune_missing_tables": bool(stale_single_table_names),
            "stale_tables_to_prune": sorted(stale_single_table_names),
        }

    @staticmethod
    def refresh_and_update_adls_tables_in_database(
        connection_id: int,
        tables_result: Dict[str, Any],
        job_name: Optional[str],
        migration_session_id: Optional[int] = None,
    ) -> Dict[str, int]:
        from services.sftp.source import SFTPSourceService

        logger.info(
            "ADLS source tables persistence started | connection_id=%s tables=%s",
            connection_id,
            len(tables_result.get("tables") or []),
        )
        result = SFTPSourceService.refresh_and_update_sftp_tables_in_database(
            connection_id=connection_id,
            tables_result=tables_result,
            job_name=job_name,
            migration_session_id=migration_session_id,
        )
        _persist_adls_table_to_files_mapping(connection_id, tables_result)
        return result

    @staticmethod
    def preview_pattern(data: ADLSPatternPreviewRequest) -> Dict[str, Any]:
        config = _config_from_preview(data)
        file_system_client, account_name, container_name = _get_adls_file_system_client(config)
        root_folder = _effective_root_folder(config)
        include_subfolders = _as_bool(config.get("include_subfolders"), default=False)
        pattern = str(data.multi_files_prefix or config.get("multi_files_prefix") or "").strip()
        max_results = int(data.max_results or 50)

        try:
            if pattern:
                log(f"[ADLS][PREVIEW] multi_files_prefix is deprecated and ignored (pattern='{pattern}')")
            all_files = _list_adls_files(file_system_client, root_folder, include_subfolders=include_subfolders)
            matched_files = sorted(all_files, key=lambda item: item.file_key.lower())
            suggested_table_name = table_from_file_key(matched_files[0].file_key if matched_files else "")
            return {
                "status": "success",
                "pattern": pattern,
                "total_files_in_folder": len(all_files),
                "total_files_by_type": len(all_files),
                "matched_files_count": len(matched_files),
                "matched_files": [
                    {
                        "file_key": entry.file_key,
                        "file_name": entry.basename,
                        "relative_path": entry.relative_path,
                        "basename": entry.basename,
                        "size_bytes": entry.size_bytes,
                        "last_modified": utc_isoformat(entry.modified_at),
                        "matches_pattern": True,
                    }
                    for entry in matched_files[:max_results]
                ],
                "sample_non_matched_files": [],
                "pattern_validation": {"is_valid_regex": True, "error_message": ""},
                "suggested_table_name": suggested_table_name,
                "folder_name": root_folder,
                "include_subfolders": include_subfolders,
                "pattern_ignored": bool(pattern),
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise _map_adls_client_error(
                exc,
                account_name=account_name,
                container_name=container_name,
                path=root_folder,
            ) from exc

    @staticmethod
    async def suggest_primary_keys(request: ADLSPKSuggestionRequest) -> Dict[str, Any]:
        source_config = request.source
        if source_config is None:
            raise HTTPException(status_code=422, detail="ADLS config is required")

        log("[ADLS][PK_SUGGEST] Starting PK suggestion")

        if source_config.load_method != LoadMethod.upsert_custom_key:
            raise HTTPException(
                status_code=422,
                detail="Primary key suggestion is only supported for load_method = upsert_custom_key",
            )

        if source_config.file_type == FileType.xml and ((source_config.xml_mode.value if source_config.xml_mode else "packed") == "packed"):
            return {"status": "success", "tables": [], "message": "XML packed format does not expose columns"}

        if source_config.file_type == FileType.json and source_config.json_mode == JSONMode.packed:
            return {"status": "success", "tables": [], "message": "JSON packed format does not expose columns"}

        config = source_config.dict() if hasattr(source_config, "dict") else source_config.model_dump()
        file_system_client, account_name, container_name = _get_adls_file_system_client(config)
        root_folder = _effective_root_folder(config)
        include_subfolders = _as_bool(source_config.include_subfolders, default=False)

        try:
            all_entries = _list_adls_files(file_system_client, root_folder, include_subfolders=include_subfolders)
        except Exception as exc:
            raise _map_adls_client_error(
                exc,
                account_name=account_name,
                container_name=container_name,
                path=root_folder,
            ) from exc

        entry_by_key = {entry.file_key: entry for entry in all_entries}
        existing_file_keys = set(entry_by_key.keys())

        if source_config.file_mapping_method == FileMappingMethod.single_table_per_file:
            file_keys = []
            seen_file_keys = set()
            for mapped_file_key in (source_config.single_file_table_mapping or {}).keys():
                mapped_key = str(mapped_file_key or "").strip().lstrip("/")
                matched_key = None
                for file_key in existing_file_keys:
                    if file_key == mapped_key or file_key.endswith("/" + mapped_key) or mapped_key.endswith("/" + file_key):
                        matched_key = file_key
                        break
                if matched_key and matched_key not in seen_file_keys:
                    seen_file_keys.add(matched_key)
                    file_keys.append(matched_key)
            is_multi_file_mode = False
        else:
            prefix = str(source_config.multi_files_prefix or "").strip()
            if prefix:
                log(f"[ADLS][PK_SUGGEST] multi_files_prefix is deprecated and ignored (pattern='{prefix}')")
            file_keys = [entry.file_key for entry in all_entries]
            is_multi_file_mode = True

        if not file_keys:
            return {"status": "success", "tables": [], "message": "No files provided for analysis"}

        opts = _reader_options(source_config)
        reader_kwargs = {
            "file_type": source_config.file_type.value,
            "delimiter": opts.delimiter if opts else None,
            "quotechar": opts.quotechar if opts else None,
            "escape_char": opts.escape_char if opts else None,
            "skipheaderlines": opts.skipheaderlines if opts else None,
            "skip_footer_lines": opts.skip_footer_lines if opts else None,
            "line_separator": opts.line_separator if opts else None,
            "json_mode": source_config.json_mode.value if source_config.json_mode else None,
            "xml_mode": source_config.xml_mode.value if source_config.xml_mode else None,
            "sample_rows": request.sample_rows,
        }

        tables_buffer = []
        files_to_read = file_keys[:5] if is_multi_file_mode else file_keys
        log(f"[ADLS][PK_SUGGEST] Analyzing {len(files_to_read)} files (out of {len(file_keys)})")

        for file_key in files_to_read:
            try:
                log(f"[ADLS][PK_SUGGEST] Reading file: {file_key}")
                entry = entry_by_key.get(file_key)
                remote_file = _open_adls_file_sample(file_system_client, file_key)
                df = read_dataframe_sample(remote_file, **reader_kwargs)
                df = _add_metadata_to_df(
                    df,
                    file_key,
                    entry.modified_at if entry else _get_adls_file_modified_at(file_system_client, file_key),
                )
            except Exception as read_error:
                log(f"[ADLS][PK_SUGGEST] Error reading file {file_key}: {read_error}")
                continue

            if df is None or df.empty:
                log(f"[ADLS][PK_SUGGEST] Skipping empty file: {file_key}")
                continue

            mapping = source_config.single_file_table_mapping or {}
            if is_multi_file_mode and source_config.multi_files_table_name:
                table_name = sanitize_identifier(str(source_config.multi_files_table_name).strip())
            else:
                mapped_name = mapping.get(file_key) or mapping.get(file_key.split("/")[-1])
                if not mapped_name:
                    for mapped_file_key, mapped_table in mapping.items():
                        mapped_key = str(mapped_file_key or "").strip().lstrip("/")
                        if file_key == mapped_key or file_key.endswith("/" + mapped_key) or mapped_key.endswith("/" + file_key):
                            mapped_name = mapped_table
                            break
                table_name = sanitize_identifier(str(mapped_name).strip()) if mapped_name else table_from_file_key(file_key)

            tables_buffer.append(
                {
                    "table_name": table_name,
                    "file_key": file_key,
                    "columns": extract_columns_for_pk_suggestion(df),
                    "df": df,
                }
            )

        if not tables_buffer:
            raise HTTPException(
                status_code=400,
                detail="No readable files found for primary key suggestion. Check ADLS file paths and format options.",
            )

        if is_multi_file_mode and len(tables_buffer) > 1:
            reference = tables_buffer[0]["columns"]
            valid_files = []
            skipped_files = []
            for item in tables_buffer:
                if set(item["columns"].keys()) == set(reference.keys()):
                    valid_files.append(item)
                else:
                    skipped_files.append(item["file_key"])
            if not valid_files:
                raise HTTPException(status_code=400, detail="No files with compatible schemas found for primary key suggestion.")
            tables_buffer = valid_files[:1]
            tables_buffer[0]["file_keys"] = [item["file_key"] for item in valid_files]
            tables_buffer[0]["skipped_files"] = skipped_files

        table_pk_state = _read_pk_state(request.connection_id)
        snowflake_tables = _fetch_snowflake_tables_from_config(source_config.destination_config)

        analyzed_tables = []
        for item in tables_buffer:
            df_sample = item["df"].head(request.sample_rows)
            rows_analyzed = len(df_sample)
            analyzed_columns = []

            for col_name, col_meta in item["columns"].items():
                col_upper = str(col_name).strip().upper()
                if col_upper in {"_SPECT_SYNCED", "_SPECT_DELETED"}:
                    continue

                series = None
                for orig_col in df_sample.columns:
                    if orig_col == col_name or sanitize_identifier(orig_col) == col_name or str(orig_col).upper() == col_name.upper():
                        series = df_sample[orig_col]
                        break

                if series is None:
                    column_obj = {
                        "column_name": col_name,
                        "data_type": col_meta.get("data_type"),
                        "uniqueness_score": 0.0,
                        "non_null_score": 0.0,
                        "warnings": ["Column not found in sample"],
                    }
                else:
                    if rows_analyzed == 0:
                        unique_pct = 0.0
                        non_null_pct = 0.0
                    else:
                        unique_pct = (series.nunique() / rows_analyzed) * 100
                        non_null_pct = ((rows_analyzed - series.isna().sum()) / rows_analyzed) * 100
                    warnings = []
                    if unique_pct < 100:
                        warnings.append("Contains duplicate values")
                    if non_null_pct < 100:
                        warnings.append("Contains NULL values")
                    column_obj = {
                        "column_name": col_name,
                        "data_type": col_meta.get("data_type"),
                        "uniqueness_score": round(unique_pct, 2),
                        "non_null_score": round(non_null_pct, 2),
                        "warnings": warnings,
                    }

                if "is_selected" in col_meta:
                    column_obj["is_selected"] = col_meta["is_selected"]
                analyzed_columns.append(column_obj)

            analyzed_columns.sort(key=lambda col: item["columns"][col["column_name"]]["ordinal_position"])
            table_key = str(item["table_name"]).strip().upper()
            table_state = table_pk_state.get(table_key, {})
            if snowflake_tables is not None and table_key and table_key not in snowflake_tables and table_state.get("pk_locked"):
                table_state = {
                    "pk_locked": False,
                    "existing_primary_keys": table_state.get("existing_primary_keys", []),
                }

            analyzed_tables.append(
                {
                    "table_name": item["table_name"],
                    "file_key": item.get("file_key"),
                    "file_keys": item.get("file_keys"),
                    "rows_analyzed": rows_analyzed,
                    "columns": analyzed_columns,
                    "skipped_files": item.get("skipped_files"),
                    "pk_locked": table_state.get("pk_locked", False),
                    "existing_primary_keys": table_state.get("existing_primary_keys", []),
                }
            )

        return {
            "status": "success",
            "tables": analyzed_tables,
            "total_files_analyzed": len(file_keys),
            "total_rows_analyzed": sum(table["rows_analyzed"] for table in analyzed_tables),
        }

