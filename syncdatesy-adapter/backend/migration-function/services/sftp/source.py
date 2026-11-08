"""SFTP source listing and schema discovery service."""

from __future__ import annotations

import json
import csv
import logging
import socket
import stat
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from models.sftp_source_models import SFTPListingTables, SFTPPatternPreviewRequest, SFTPPKSuggestionRequest
from models.amazon_s3_models import FileType, FileMappingMethod, LoadMethod, JSONMode, XMLMode, DelimitedFileOptions
from services.amazon_s3.service import (
    extract_columns_for_pk_suggestion,
    _fetch_snowflake_tables_from_config,
    _resolve_single_file_mapped_name,
    _unique_table_name_for_file,
    _build_file_basename_counts,
    _log_schema_validation_failure_to_table,
    sanitize_column_name,
)
from utils.s3_utils import log

from services.source_file_helpers.file_format_readers import read_dataframe_sample
from services.source_file_helpers.file_path_utils import (
    join_posix_path,
    matches_any_pattern,
    normalize_posix_path,
    normalize_root_path,
    relative_to_root,
)
from services.source_file_helpers.file_source_base import (
    SourceFileEntry,
    infer_columns_from_dataframe,
    sanitize_identifier,
    table_from_file_key,
    utc_isoformat,
)
from services.source_file_helpers.file_source_mapping import resolve_file_entries
from utils.database_utils import postgres_connection

logger = logging.getLogger(__name__)


def _add_metadata_to_df(df, file_key, last_modified):
    """Add metadata columns (_FILE, _ROW, _MODIFIED, etc.) to match S3's DataFrame schema."""
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


def _build_schema_validation_error_message(file_key: str, file_type: str, details: Optional[str] = None) -> str:
    """Build standardized schema-validation error text for SFTP update schema failures."""
    clean_details = str(details).strip().lstrip(",:; ").strip() if details else None
    if clean_details:
        return f"Update schema failed: File '{file_key}' could not be parsed. Details: {clean_details}"
    return f"Update schema failed: File '{file_key}' could not be parsed."


def _is_valid_avro_container(content: bytes) -> bool:
    """Validate Avro Object Container File magic bytes."""
    return isinstance(content, (bytes, bytearray)) and len(content) >= 4 and content[:4] == b"Obj\x01"


def _is_valid_parquet_file(content: bytes) -> bool:
    """Validate Parquet magic bytes."""
    return (
        isinstance(content, (bytes, bytearray))
        and len(content) >= 8
        and content[:4] == b"PAR1"
        and content[-4:] == b"PAR1"
    )


def _binary_format_validation_error(file_type: str, content: bytes) -> Optional[str]:
    """Return a friendly validation error for binary formats, if invalid."""
    ft = (file_type or "").lower().strip()
    if ft == "avro" and not _is_valid_avro_container(content):
        return "Invalid Avro container file header (expected magic bytes 'Obj\\x01')."
    if ft == "parquet" and not _is_valid_parquet_file(content):
        return "Invalid parquet file signature (expected 'PAR1' at start/end)."
    return None


def _first_non_whitespace_char(text: str) -> Optional[str]:
    for ch in text or "":
        if not ch.isspace():
            return ch
    return None


def _normalize_delimiter(value: Optional[str], file_type: str) -> str:
    if value not in (None, "", False):
        text = str(value)
        if text in (r"\t", r"\\t"):
            return "\t"
        if text in (r"\n", r"\\n"):
            return "\n"
        if text in (r"\r", r"\\r"):
            return "\r"
        return text[0]
    return "\t" if str(file_type).lower() == "tsv" else ","


def _read_sftp_head_text(sftp, key: str, max_bytes: int = 4096) -> str:
    if max_bytes <= 0:
        return ""
    try:
        with sftp.open(key, "rb") as remote_file:
            head_bytes = remote_file.read(max_bytes)
    except Exception as read_err:
        logger.warning("[SFTP][LIST_SCHEMA] Failed to read head bytes for '%s': %s", key, read_err)
        return ""
    if isinstance(head_bytes, bytes):
        return head_bytes.decode("utf-8", errors="ignore")
    return str(head_bytes or "")


def _validate_sftp_parquet_signature(sftp, key):
    """Validate PAR1 magic at both ends. A prefix read misses the footer of a large file."""
    try:
        with sftp.open(key, "rb") as stream:
            if stream.read(4) != b"PAR1":
                return False, "Invalid parquet header (expected PAR1)."
            stream.seek(0, 2)
            if stream.tell() < 8:
                return False, "Invalid parquet file: too short."
            stream.seek(-4, 2)
            if stream.read(4) != b"PAR1":
                return False, "Invalid parquet footer (expected PAR1)."
        return True, None
    except (OSError, ValueError) as exc:
        return False, f"Could not validate parquet file: {exc}"


def _read_sftp_head_bytes(sftp, key: str, max_bytes: int = 4096) -> bytes:
    """Read a small prefix of an SFTP file for lightweight format checks."""
    if max_bytes <= 0:
        return b""
    try:
        with sftp.open(key, "rb") as remote_file:
            head_bytes = remote_file.read(max_bytes)
    except Exception as read_err:
        logger.warning("[SFTP][LIST_SCHEMA] Failed to read head bytes for '%s': %s", key, read_err)
        return b""
    if isinstance(head_bytes, bytes):
        return head_bytes
    return str(head_bytes or "").encode("utf-8", errors="ignore")


def _validate_sftp_file_compatible_with_type(
    sftp,
    key: str,
    file_type: str,
    delimiter: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    ft = str(file_type or "").strip().lower()
    if not ft:
        return True, None

    head_text = _read_sftp_head_text(sftp, key, max_bytes=4096)
    if not head_text:
        return False, "file is empty or head bytes could not be read"

    first_char = _first_non_whitespace_char(head_text)
    if ft == "avro":
        head_bytes = _read_sftp_head_bytes(sftp, key, max_bytes=4096)
        if _is_valid_avro_container(head_bytes):
            return True, None
        return False, "Invalid Avro container file header (expected magic bytes 'Obj\\x01')."
    if ft == "parquet":
        head_bytes = _read_sftp_head_bytes(sftp, key, max_bytes=4096)
        if _is_valid_parquet_file(head_bytes):
            return True, None
        return False, "Invalid parquet file signature (expected 'PAR1' at start/end)."
    if ft == "xml":
        if first_char == "<":
            return True, None
        return False, "does not look like XML"
    if ft == "json":
        if first_char in ("{", "["):
            return True, None
        return False, "does not look like JSON"
    if ft in ("csv", "tsv"):
        delim = _normalize_delimiter(delimiter, ft)
        if first_char in ("<", "{", "["):
            return False, f"does not look like delimited {ft.upper()} content. delimiter {repr(delim)} not found"
        return True, None
    return True, None

SUPPORTED_SOURCE_FILE_TYPES = {"csv", "tsv", "json", "xml", "avro", "parquet"}


def _extract_config(request: SFTPListingTables) -> Dict[str, Any]:
    """Return the SFTP config dictionary from the request."""
    config = request.SFTP if isinstance(request.SFTP, dict) else {}
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Invalid SFTP config; expected object.")
    return config


def _validate_source_config(config: Dict[str, Any]) -> None:
    """Validate source-side SFTP settings without relying on destination helpers."""
    host = str(config.get("sftp_host") or config.get("host") or "").strip()
    port = config.get("sftp_port") if config.get("sftp_port") is not None else config.get("port")
    username = str(config.get("sftp_username") or config.get("username") or "").strip()
    auth_type = str(config.get("authentication_type") or "password").strip().lower()
    file_type = str(config.get("file_type") or "").strip().lower()

    if not host:
        raise HTTPException(status_code=422, detail="Missing required field: sftp_host")
    if not username:
        raise HTTPException(status_code=422, detail="Missing required field: sftp_username")

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 22
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid sftp_port; must be an integer") from exc
    if port_int <= 0 or port_int > 65535:
        raise HTTPException(status_code=422, detail="Invalid sftp_port; must be 1..65535")

    if auth_type not in ("password", "key_pair"):
        raise HTTPException(status_code=422, detail="Invalid authentication_type; expected password or key_pair")

    if auth_type == "password":
        password = config.get("sftp_password") or config.get("password")
        if not password:
            raise HTTPException(status_code=422, detail="Missing required field: sftp_password")
    else:
        private_key = config.get("sftp_private_key") or config.get("private_key")
        if not private_key:
            raise HTTPException(status_code=422, detail="Missing required field: sftp_private_key")

    root_folder = str(config.get("root_folder") or config.get("folder_path") or "").strip()
    if not root_folder:
        raise HTTPException(status_code=422, detail="Missing required field: root_folder")

    if file_type and file_type not in SUPPORTED_SOURCE_FILE_TYPES:
        raise HTTPException(
            status_code=422,
            detail="Invalid file_type; expected csv, tsv, json, xml, avro, or parquet",
        )


def _map_sftp_connection_error(error: Exception, host: str, port: int) -> HTTPException:
    """Map transport errors to user-friendly HTTP errors."""
    message = str(error or "").strip().lower()

    if isinstance(error, socket.gaierror):
        return HTTPException(
            status_code=422,
            detail=f"SFTP host could not be resolved: {host}. Please verify the server address.",
        )
    if isinstance(error, (TimeoutError, socket.timeout)) or "timed out" in message or "timeout" in message:
        return HTTPException(
            status_code=422,
            detail=f"SFTP connection timed out while reaching {host}:{port}. Please verify the host, port, and network access.",
        )
    if isinstance(error, ConnectionRefusedError) or "connection refused" in message:
        return HTTPException(
            status_code=422,
            detail=f"SFTP server refused the connection on {host}:{port}. Please verify the port and that the SFTP service is running.",
        )
    if isinstance(error, OSError) and getattr(error, "errno", None) in {101, 113}:
        return HTTPException(
            status_code=422,
            detail=f"SFTP network route is unreachable for {host}:{port}. Please verify firewall, VPN, or network routing.",
        )
    if "auth fail" in message or "authentication failed" in message:
        return HTTPException(status_code=422, detail="SFTP authentication failed. Please verify credentials.")
    return HTTPException(status_code=422, detail="Failed to connect to SFTP. Please verify connection details.")


def _ensure_sftp_dep():
    """Import paramiko lazily so the service can fail gracefully."""
    try:
        import paramiko
    except Exception as exc:
        raise HTTPException(status_code=500, detail="SFTP dependency missing: paramiko is not installed") from exc
    return paramiko


def _load_private_key(paramiko_module, private_key_pem: str, passphrase: Optional[str]):
    """Load a private key using paramiko's supported key classes."""
    import io

    key_stream = io.StringIO(private_key_pem)
    password = passphrase if passphrase not in ("", None) else None
    key_loaders = (
        paramiko_module.RSAKey,
        paramiko_module.ECDSAKey,
        getattr(paramiko_module, "Ed25519Key", None),
        paramiko_module.DSSKey,
    )
    last_error = None
    for key_cls in key_loaders:
        if key_cls is None:
            continue
        try:
            key_stream.seek(0)
            return key_cls.from_private_key(key_stream, password=password)
        except Exception as exc:
            last_error = exc
    raise HTTPException(status_code=422, detail=f"Invalid sftp_private_key; failed to parse key ({last_error})")


def _open_sftp_client(config: Dict[str, Any]):
    """Open an authenticated SFTP client.

    Args:
        config: SFTP configuration dictionary.

    Returns:
        Tuple of (sftp_client, transport, socket).
    """
    paramiko = _ensure_sftp_dep()

    host = str(config.get("sftp_host") or config.get("host") or "").strip()
    port = config.get("sftp_port") if config.get("sftp_port") is not None else config.get("port")
    username = str(config.get("sftp_username") or config.get("username") or "").strip()
    auth_type = str(config.get("authentication_type") or "password").strip().lower()

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 22
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid sftp_port; must be an integer") from exc

    if not host or not username:
        raise HTTPException(status_code=422, detail="Missing required SFTP connection fields")

    connect_timeout = int(config.get("connect_timeout_seconds") or 10)
    sock = None
    transport = None
    sftp = None
    try:
        sock = socket.create_connection((host, port_int), timeout=connect_timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = connect_timeout
        transport.auth_timeout = connect_timeout

        if auth_type == "password":
            password = config.get("sftp_password") or config.get("password")
            transport.connect(username=username, password=password)
        elif auth_type == "key_pair":
            private_key = config.get("sftp_private_key") or config.get("private_key")
            passphrase = config.get("sftp_passphrase") or config.get("passphrase")
            pkey = _load_private_key(paramiko, str(private_key or ""), str(passphrase or "") if passphrase is not None else None)
            transport.connect(username=username, pkey=pkey)
        else:
            raise HTTPException(status_code=422, detail="Invalid authentication_type; expected password or key_pair")

        sftp = paramiko.SFTPClient.from_transport(transport)
        return sftp, transport, sock
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_sftp_connection_error(exc, host, port_int) from exc


def _close_sftp_client(sftp, transport, sock) -> None:
    """Close the SFTP client and underlying transport."""
    for handle in (sftp, transport, sock):
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass


def _effective_root_folder(config: Dict[str, Any]) -> str:
    """Return the configured root folder, including any base folder path."""
    root_folder = str(config.get("root_folder") or config.get("folder_path") or "").strip()
    base_folder_path = str(config.get("base_folder_path") or config.get("sftp_base_prefix") or "").strip()
    return normalize_root_path(root_folder, base_folder_path)


def _sftp_listing_root_folder(config: Dict[str, Any]) -> str:
    """Return only the top-level SFTP folder used for chdir()."""
    return str(config.get("root_folder") or config.get("folder_path") or "").strip()


def _sftp_listing_base_folder(config: Dict[str, Any]) -> str:
    """Return the optional base folder to list inside the root folder."""
    return normalize_posix_path(config.get("base_folder_path") or config.get("sftp_base_prefix") or "")


def _build_reader_kwargs(config: Dict[str, Any], file_type: str) -> Dict[str, Any]:
    """Build file-reader keyword arguments from the SFTP config."""
    kwargs: Dict[str, Any] = {
        "file_type": file_type,
        "json_mode": config.get("json_mode"),
        "xml_mode": config.get("xml_mode"),
        "sample_rows": int(config.get("sample_rows") or 1000),
    }
    if file_type == "tsv":
        kwargs["delimiter"] = config.get("tsv_delimiter") or config.get("delimiter")
        kwargs["quotechar"] = config.get("tsv_quotechar") or config.get("quotechar")
        kwargs["escape_char"] = config.get("tsv_escape_char") or config.get("escape_char")
        kwargs["skipheaderlines"] = config.get("tsv_skipheaderlines") if config.get("tsv_skipheaderlines") is not None else config.get("skipheaderlines")
        kwargs["skip_footer_lines"] = config.get("tsv_skip_footer_lines") if config.get("tsv_skip_footer_lines") is not None else config.get("skip_footer_lines")
        kwargs["line_separator"] = config.get("line_separator")
    elif file_type == "csv":
        kwargs["delimiter"] = config.get("delimiter")
        kwargs["quotechar"] = config.get("quotechar")
        kwargs["escape_char"] = config.get("escape_char")
        kwargs["skipheaderlines"] = config.get("skipheaderlines")
        kwargs["skip_footer_lines"] = config.get("skip_footer_lines")
        kwargs["line_separator"] = config.get("line_separator")
    return kwargs


def _walk_sftp_files(sftp, current_path: str, root_path: str, include_subfolders: bool) -> List[SourceFileEntry]:
    """Recursively enumerate SFTP files."""
    discovered: List[SourceFileEntry] = []
    current_path = normalize_posix_path(current_path, keep_leading_slash=current_path.startswith("/"))
    root_path = normalize_posix_path(root_path, keep_leading_slash=root_path.startswith("/"))

    try:
        attrs = sftp.listdir_attr(current_path or ".")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"Configured root folder does not exist on SFTP: {current_path or root_path}") from exc
    except Exception as exc:
        raise _map_sftp_connection_error(exc, str(current_path or root_path), 22) from exc

    for attr in attrs:
        name = str(getattr(attr, "filename", "") or "").strip()
        if not name:
            continue
        child_path = join_posix_path(current_path or root_path, name, keep_leading_slash=(current_path or root_path).startswith("/"))
        modified_at = datetime.fromtimestamp(getattr(attr, "st_mtime", 0) or 0, tz=timezone.utc)
        size_bytes = int(getattr(attr, "st_size", 0) or 0)
        if stat.S_ISDIR(getattr(attr, "st_mode", 0)):
            if include_subfolders:
                discovered.extend(_walk_sftp_files(sftp, child_path, root_path, include_subfolders=True))
            continue

        discovered.append(
            SourceFileEntry(
                file_key=normalize_posix_path(child_path, keep_leading_slash=current_path.startswith("/") or root_path.startswith("/")),
                relative_path=relative_to_root(child_path, root_path),
                basename=name,
                size_bytes=size_bytes,
                modified_at=modified_at,
                inferred_file_type=name.rsplit(".", 1)[-1].lower() if "." in name else None,
            )
        )
    return discovered


def _normalize_file_fields(raw_columns: Any) -> Dict[str, Any]:
    """Normalize inferred column metadata before persisting it.

    Uses sanitize_column_name (same helper as the S3 service) which keeps
    leading underscores intact — so _FILE, _ROW, _MODIFIED are stored correctly
    in source_table_selection and match the Snowflake target table schema.
    """
    normalized: Dict[str, Any] = {}
    if not isinstance(raw_columns, dict):
        return normalized

    for raw_col_name, raw_meta in raw_columns.items():
        col_name = sanitize_column_name(str(raw_col_name))
        if not col_name:
            continue
        meta = raw_meta.copy() if isinstance(raw_meta, dict) else {}
        meta["is_primary_key"] = bool(meta.get("is_primary_key", False))
        meta.setdefault("data_type", "VARCHAR(255)")
        normalized[col_name] = meta
    return normalized


def _get_delta_fields_for_source(source_id: Optional[int]) -> List[str]:
    """Fetch delta fields from source_delta_table for a given source_id."""
    if not source_id:
        return []
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT delta_fields 
            FROM source_delta_table 
            WHERE source_id = %s
            """,
            (source_id,),
        )
        result = cursor.fetchone()
        if result and result[0]:
            return result[0] if isinstance(result[0], list) else json.loads(result[0])
        return []
    except Exception as e:
        logger.error(f"Error fetching delta fields for SFTP: {e}")
        return []


def _check_table_has_delta_fields(table_fields: Dict[str, Any], delta_fields: List[str]) -> bool:
    """Check if a table contains any of the specified delta fields."""
    if not table_fields or not delta_fields:
        return False
    # Check if any delta field exists in the table fields
    found_delta_fields = [field for field in delta_fields if field in table_fields]
    return len(found_delta_fields) > 0


def _build_table_records(
    tables: List[Dict[str, Any]],
    *,
    file_mapping_mode: str,
    base_folder_path: str,
    source_id: Optional[int] = None,
) -> Tuple[List[Tuple[Any, ...]], Dict[str, str], Dict[str, List[str]]]:
    """Prepare database rows and mapping payloads from a resolved table listing."""
    rows: List[Tuple[Any, ...]] = []
    single_file_mapping: Dict[str, str] = {}
    table_to_files_mapping: Dict[str, List[str]] = {}

    delta_fields = _get_delta_fields_for_source(source_id) if source_id else []

    for sequence, table_info in enumerate(tables, start=1):
        raw_table_name = table_info.get("table")
        table_name = sanitize_identifier(str(raw_table_name).strip()) if raw_table_name else ""
        if not table_name:
            continue

        raw_columns = table_info.get("columns", {})
        columns = _normalize_file_fields(raw_columns)
        primary_keys = [
            col_name
            for col_name, meta in columns.items()
            if isinstance(meta, dict) and meta.get("is_primary_key", False)
        ]
        table_fields_json = json.dumps(columns or {})
        primary_keys_json = json.dumps(primary_keys)

        file_key = str(table_info.get("file_key") or "").strip()
        relative_path = str(table_info.get("relative_path") or "").strip()
        folder_path_value = (
            base_folder_path
            if file_mapping_mode == "multi_files_single_table" and base_folder_path
            else file_key
        )
        json_path_value = relative_path or file_key or folder_path_value
        # Discovery must not mark an unprocessed file as already migrated.
        last_synced = None

        is_delta = False
        if delta_fields and columns:
            is_delta = _check_table_has_delta_fields(columns, delta_fields)

        if file_mapping_mode == "single_table_per_file" and file_key:
            single_file_mapping[file_key] = table_name
        elif file_mapping_mode == "multi_files_single_table" and file_key:
            table_to_files_mapping.setdefault(table_name, []).append(file_key)

        rows.append(
            (
                table_name,
                table_fields_json,
                primary_keys_json,
                False,
                True,
                datetime.now(timezone.utc),
                False,
                is_delta,
                folder_path_value or None,
                json_path_value or None,
                last_synced,
                sequence,
            )
        )

    return rows, single_file_mapping, table_to_files_mapping


def _upsert_source_tables_and_mapping(
    *,
    connection_id: int,
    tables_result: Dict[str, Any],
    single_file_mapping: Dict[str, str],
    table_to_files_mapping: Dict[str, List[str]],
    cmp_id: int,
    user_id: int,
    source_id: Optional[int] = None,
    job_name: Optional[str] = None,
    migration_session_id: Optional[int] = None,
) -> Dict[str, int]:
    """Persist discovered SFTP tables and mapping state into PostgreSQL."""
    conn = None
    cursor = None
    inserted_or_updated = 0
    deleted = 0
    mapping_mode = str(
        tables_result.get("file_mapping_mode")
        or tables_result.get("file_mapping_method")
        or "single_table_per_file"
    ).strip()
    base_folder_path = str(tables_result.get("base_folder_path") or "").strip()
    prune_missing_tables = bool(tables_result.get("prune_missing_tables", False))

    try:
        conn = postgres_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT "table"
            FROM source_table_selection
            WHERE connection_id = %s
            """,
            (connection_id,),
        )
        existing_tables = {
            sanitize_identifier(str(row[0]).strip())
            for row in cursor.fetchall()
            if str(row[0]).strip()
        }
        logger.info(
            "[SFTP][DIAG][DB-1] connection_id=%s | existing_tables in DB=%r | "
            "mapping_mode=%s | prune_missing_tables=%s",
            connection_id, sorted(existing_tables), mapping_mode, prune_missing_tables
        )

        incoming_tables = []
        rows, normalized_single_map, normalized_multi_map = _build_table_records(
            list(tables_result.get("tables") or []),
            file_mapping_mode=mapping_mode,
            base_folder_path=base_folder_path,
            source_id=source_id,
        )

        for row in rows:
            table_name = row[0]
            folder_path_value = row[8]
            incoming_tables.append(table_name)
            logger.info("[SFTP][DIAG][DB-2] incoming table: '%s' | folder_path='%s'", table_name, folder_path_value)

            # If the same file path is remapped to a new table name, remove old rows.
            if mapping_mode == "single_table_per_file" and folder_path_value:
                cursor.execute(
                    """
                    DELETE FROM source_table_selection
                    WHERE connection_id = %s
                      AND folder_path = %s
                      AND UPPER("table") <> UPPER(%s)
                    """,
                    (connection_id, folder_path_value, table_name),
                )
            elif mapping_mode == "multi_files_single_table":
                cursor.execute(
                    """
                    DELETE FROM source_table_selection
                    WHERE connection_id = %s
                      AND UPPER("table") <> UPPER(%s)
                    """,
                    (connection_id, table_name),
                )

            # Normalize existing case-variant rows to canonical table name.
            cursor.execute(
                """
                UPDATE source_table_selection
                SET "table" = %s
                WHERE connection_id = %s
                  AND UPPER("table") = UPPER(%s)
                  AND "table" <> %s
                """,
                (table_name, connection_id, table_name, table_name),
            )

        if rows:
            from psycopg2.extras import execute_values

            # Verify layout length before execution to fail loudly in Python if mismatched
            expected_column_count = 12
            for row in rows:
                if len(row) != expected_column_count:
                    raise ValueError(
                        f"SFTP Row tuple has {len(row)} values, expected {expected_column_count} "
                        f"matching columns (excluding connection_id): got {row}"
                    )

            # Preserve the committed checkpoint, including a concurrent migration.
            sql_upsert = """
                INSERT INTO source_table_selection
                    (connection_id, "table", table_fields, primary_keys, selected,
                     is_mapping_checked, added_on, is_history, is_delta, folder_path, json_path, last_synced, sequence)
                VALUES %s
                ON CONFLICT (connection_id, "table")
                DO UPDATE SET
                    table_fields = EXCLUDED.table_fields,
                    primary_keys = EXCLUDED.primary_keys,
                    is_mapping_checked = EXCLUDED.is_mapping_checked,
                    selected = CASE
                        WHEN source_table_selection.selected = TRUE THEN TRUE
                        ELSE EXCLUDED.selected
                    END,
                    is_history = EXCLUDED.is_history,
                    is_delta = EXCLUDED.is_delta,
                    folder_path = EXCLUDED.folder_path,
                    json_path = EXCLUDED.json_path,
                    last_synced = source_table_selection.last_synced,
                    sequence = COALESCE(source_table_selection.sequence, EXCLUDED.sequence)
            """
            execute_values(cursor, sql_upsert, [(connection_id, *row) for row in rows], page_size=500)
            inserted_or_updated = len(rows)

        logger.info(
            "[SFTP][DIAG][DB-3] After upsert: "
            "incoming_tables=%r | existing_tables=%r | prune_missing_tables=%s",
            sorted(incoming_tables), sorted(existing_tables), prune_missing_tables
        )

        stale_tables_set = set()
        if prune_missing_tables:
            stale_tables_from_result = {
                sanitize_identifier(str(t).strip())
                for t in (tables_result.get("stale_tables_to_prune") or [])
                if str(t).strip()
            }
            logger.info("[SFTP][DIAG][DB-3] stale_tables_from_result=%r", sorted(stale_tables_from_result))
            if stale_tables_from_result:
                stale_tables_set = existing_tables & stale_tables_from_result
            else:
                stale_tables_set = existing_tables - set(incoming_tables)
            logger.info("[SFTP][DIAG][DB-3] stale_tables_set to DELETE/uncheck=%r", sorted(stale_tables_set))
            if stale_tables_set:
                if mapping_mode == "multi_files_single_table":
                    cursor.execute(
                        """
                        UPDATE source_table_selection
                        SET selected = FALSE, is_mapping_checked = FALSE
                        WHERE connection_id = %s
                          AND "table" = ANY(%s)
                        """,
                        (connection_id, sorted(stale_tables_set)),
                    )
                    logger.info("[SFTP][DIAG][DB-3] Marked stale tables unchecked (multi mode): %r", sorted(stale_tables_set))
                else:
                    cursor.execute(
                        """
                        DELETE FROM source_table_selection
                        WHERE connection_id = %s
                          AND "table" = ANY(%s)
                        """,
                        (connection_id, sorted(stale_tables_set)),
                    )
                    deleted = len(stale_tables_set)
                    logger.info("[SFTP][DIAG][DB-3] DELETED stale tables: %r", sorted(stale_tables_set))
            else:
                logger.info("[SFTP][DIAG][DB-3] No stale tables to delete (stale_tables_set empty)")
        else:
            logger.info("[SFTP][DIAG][DB-3] prune_missing_tables=False — skipping stale delete")

        # For single-file mode, keep metadata for un-mapped tables but mark them unchecked.
        if mapping_mode == "single_table_per_file":
            unchecked_tables = (existing_tables - set(incoming_tables)) - stale_tables_set
            logger.info(
                "[SFTP][DIAG][DB-4] unchecked_tables calculation: "
                "(%r - %r) - %r = %r",
                sorted(existing_tables), sorted(incoming_tables),
                sorted(stale_tables_set), sorted(unchecked_tables)
            )
            if unchecked_tables:
                cursor.execute(
                    """
                    UPDATE source_table_selection
                    SET is_mapping_checked = FALSE, selected = FALSE
                    WHERE connection_id = %s
                      AND "table" = ANY(%s)
                    """,
                    (connection_id, sorted(unchecked_tables)),
                )
                logger.info("[SFTP][DIAG][DB-4] Marked unchecked (removed from UI): %r", sorted(unchecked_tables))
            else:
                logger.info("[SFTP][DIAG][DB-4] No tables to mark unchecked — nothing will be removed from UI")

        cursor.execute(
            """
            INSERT INTO source_file_mapping
                (connection_id, file_mapping_mode, single_file_table_mapping, table_to_files_mapping,
                 cmp_id, source_id, basefolder_path, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id)
            DO UPDATE SET
                file_mapping_mode = EXCLUDED.file_mapping_mode,
                single_file_table_mapping = EXCLUDED.single_file_table_mapping,
                table_to_files_mapping = EXCLUDED.table_to_files_mapping,
                cmp_id = EXCLUDED.cmp_id,
                source_id = EXCLUDED.source_id,
                basefolder_path = EXCLUDED.basefolder_path,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            """,
            (
                connection_id,
                mapping_mode,
                json.dumps(normalized_single_map or {}),
                json.dumps(normalized_multi_map or {}),
                cmp_id,
                source_id,
                base_folder_path or None,
                user_id,
                user_id,
            ),
        )

        # Stage 5: Update migration record status if job_name provided
        if job_name:
            sql_update_migration_record = """
                UPDATE migration_record
                SET status = %s, updated_at = NOW()
                WHERE job_name = %s;
            """
            cursor.execute(sql_update_migration_record, ('completed', job_name))

        conn.commit()
        logger.info(
            "SFTP source tables persisted | connection_id=%s tables=%s mode=%s deleted=%s",
            connection_id,
            inserted_or_updated,
            mapping_mode,
            deleted,
        )

        # Update the log message to indicate schema refresh completion
        if job_name:
            try:
                from utils.error_utils import update_migration_status_log
                total_tables = inserted_or_updated
                removed_count = deleted
                execution_time = None
                try:
                    conn_et = postgres_connection()
                    cursor_et = conn_et.cursor()
                    cursor_et.execute(
                        """
                        SELECT start_time
                        FROM migration_record
                        WHERE job_name = %s
                        ORDER BY created_at DESC
                        LIMIT 1;
                        """,
                        (job_name,),
                    )
                    row = cursor_et.fetchone()
                    start_time_dt = row[0] if row and row[0] else None
                    if isinstance(start_time_dt, datetime):
                        if start_time_dt.tzinfo is None:
                            execution_time = (datetime.utcnow() - start_time_dt).total_seconds()
                        else:
                            execution_time = (datetime.now(timezone.utc) - start_time_dt).total_seconds()
                except Exception as time_err:
                    logger.warning("Failed to compute execution_time: %s", time_err)
                    execution_time = None
                finally:
                    try:
                        cursor_et.close()
                    except Exception:
                        pass
                    try:
                        conn_et.close()
                    except Exception:
                        pass

                if execution_time is None:
                    execution_time = 0.0

                if removed_count > 0:
                    removed_label = "table" if removed_count == 1 else "tables"
                    completion_message = (
                        f"Update schema completed successfully in {execution_time:.2f} seconds. "
                        f"Removed {removed_count} {removed_label}."
                    )
                else:
                    completion_message = (
                        f"Update schema completed successfully in {execution_time:.2f} seconds."
                    )
                update_migration_status_log(
                    job_name=job_name,
                    message=completion_message,
                    status="S",  # Success status
                    session=migration_session_id,
                )
                try:
                    from utils.websocket_utils import trigger_connection_activity_log_update
                    trigger_connection_activity_log_update(connection_id)
                except Exception as activity_ws_error:
                    logger.warning("Connection activity log WebSocket trigger failed (non-fatal): %s", activity_ws_error)
            except Exception as log_error:
                logger.warning("Update schema completion log update failed (non-fatal): %s", log_error)

        # Trigger WebSocket update for schema refresh completion
        if job_name:
            try:
                from utils.websocket_utils import trigger_update_schema_ws_status_update
                trigger_update_schema_ws_status_update(connection_id)
            except Exception as ws_error:
                logger.warning("WebSocket trigger failed (non-fatal): %s", ws_error)

        return {
            "tables_written": inserted_or_updated,
            "tables_deleted": deleted,
        }
    except Exception as e:
        error_msg = str(e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        
        # Update migration record to FAILED if we have job_name
        if job_name and conn:
            try:
                cursor_err = conn.cursor()
                sql_update_migration_record_error = """
                    UPDATE migration_record
                    SET status = %s, updated_at = NOW()
                    WHERE job_name = %s;
                """
                cursor_err.execute(sql_update_migration_record_error, ('failed', job_name))
                conn.commit()

                # Update the log message to indicate schema refresh failure
                try:
                    from utils.error_utils import update_migration_status_log
                    failure_message = f"Update schema failed: {error_msg}"
                    update_migration_status_log(
                        job_name=job_name,
                        message=failure_message,
                        status="E",  # Error status
                        session=migration_session_id,
                    )
                except Exception as log_err:
                    logger.warning("Failed to update status log on error: %s", log_err)

                # Trigger WebSocket update for schema refresh failure
                try:
                    from utils.websocket_utils import trigger_update_schema_ws_status_update
                    trigger_update_schema_ws_status_update(connection_id)
                except Exception as ws_error:
                    logger.warning("WebSocket trigger failed (non-fatal): %s", ws_error)
            except Exception as update_error:
                logger.warning("Failed to update migration record: %s", update_error)
        raise
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


class SFTPSourceService:
    """Source-side SFTP listing and schema discovery."""

    @staticmethod
    def list_files(data: SFTPListingTables) -> Dict[str, Any]:
        """List SFTP files under the configured root folder."""
        config = _extract_config(data)
        _validate_source_config(config)
        logger.info(
            "SFTP file listing started | connection_id=%s host=%s root_folder=%s",
            data.connection_id,
            config.get("sftp_host"),
            config.get("root_folder"),
        )
        sftp, transport, sock = _open_sftp_client(config)
        try:
            root_folder = _sftp_listing_root_folder(config)
            base_folder_path = _sftp_listing_base_folder(config)
            include_subfolders = str(config.get("include_subfolders") or "").strip().lower() in {"1", "true", "yes", "on"}
            file_type = str(config.get("file_type") or "").strip().lower()

            if root_folder:
                try:
                    sftp.chdir(root_folder)
                except IOError as e:
                    err_msg = str(e).lower()
                    err_no = getattr(e, "errno", None)
                    if isinstance(e, FileNotFoundError) or err_no == 2 or "no such file" in err_msg:
                        raise HTTPException(
                            status_code=404,
                            detail=f"The configured root folder does not exist on SFTP: {root_folder}. Please verify the folder path.",
                        )
                    elif isinstance(e, PermissionError) or err_no == 13 or "permission denied" in err_msg or "access denied" in err_msg:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied to access root folder: {root_folder}. Please verify read permissions for the user.",
                        )
                    else:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Root folder '{root_folder}' is not accessible on SFTP: {str(e)}",
                        )


            listing_start = base_folder_path or "."
            files = _walk_sftp_files(sftp, listing_start, listing_start, include_subfolders=include_subfolders)
            files = sorted(files, key=lambda item: item.file_key.lower())

            # Load file mappings and Snowflake configuration logic to lock previously migrated tables
            file_mapping_mode = str(config.get("file_mapping_method") or config.get("file_mapping_mode") or "single_table_per_file").strip()
            single_file_map = config.get("single_file_table_mapping") or {}
            
            mapped_lookup = {}
            if file_mapping_mode == "single_table_per_file" and single_file_map:
                for mapped_file_key, mapped_table in single_file_map.items():
                    file_key = str(mapped_file_key).strip()
                    if not file_key:
                        continue
                    mapped_table_name = sanitize_identifier(str(mapped_table).strip())
                    mapped_lookup[file_key] = mapped_table_name
            elif file_mapping_mode == "multi_files_single_table":
                table_to_files = config.get("table_to_files_mapping") or {}
                if isinstance(table_to_files, dict) and table_to_files:
                    for table_name, mapping_info in table_to_files.items():
                        table_name = sanitize_identifier(str(table_name).strip())
                        if not table_name:
                            continue
                        file_list = mapping_info if isinstance(mapping_info, list) else []
                        for mapped_file_key in file_list:
                            file_key = str(mapped_file_key).strip()
                            if not file_key:
                                continue
                            mapped_lookup[file_key] = table_name

            all_file_keys = [entry.file_key for entry in files]
            basename_counts = _build_file_basename_counts(all_file_keys)

            dest_config = config.get("destination_config")
            existing_sf_tables = _fetch_snowflake_tables_from_config(dest_config)
            logger.debug(
                "SFTP Snowflake table lookup: %s",
                "ok" if existing_sf_tables is not None else "skipped/failed"
            )

            existing_file_metadata = {}
            if data.connection_id:
                try:
                    conn_db = postgres_connection()
                    cursor_db = conn_db.cursor()
                    cursor_db.execute(
                        """
                        SELECT "table", folder_path, last_synced, primary_keys
                        FROM source_table_selection
                        WHERE connection_id = %s
                        """,
                        (data.connection_id,),
                    )
                    for row in cursor_db.fetchall() or []:
                        table_name_raw, folder_path_raw, last_synced_raw, primary_keys_raw = row
                        file_path = str(folder_path_raw or "").strip()
                        if not file_path:
                            continue
                        pk_list = []
                        if isinstance(primary_keys_raw, list):
                            pk_list = [str(k).strip() for k in primary_keys_raw if str(k).strip()]
                        elif isinstance(primary_keys_raw, str):
                            try:
                                parsed_pk = json.loads(primary_keys_raw)
                                if isinstance(parsed_pk, list):
                                    pk_list = [str(k).strip() for k in parsed_pk if str(k).strip()]
                            except Exception:
                                pk_list = []
                        meta = {
                            "table": sanitize_identifier(str(table_name_raw).strip()) if str(table_name_raw).strip() else "",
                            "is_migrated": bool(str(last_synced_raw or "").strip()),
                            "primary_keys": pk_list,
                            "last_synced": str(last_synced_raw or "").strip(),
                        }
                        if file_path not in existing_file_metadata:
                            existing_file_metadata[file_path] = meta
                        else:
                            # Prefer the most recently synced row when duplicates exist.
                            def _parse_synced(value: str):
                                v = str(value or "").strip()
                                if not v:
                                    return None
                                try:
                                    return datetime.fromisoformat(v)
                                except Exception:
                                    return None

                            current = existing_file_metadata[file_path]
                            current_dt = _parse_synced(current.get("last_synced"))
                            incoming_dt = _parse_synced(meta.get("last_synced"))

                            if incoming_dt and (not current_dt or incoming_dt > current_dt):
                                existing_file_metadata[file_path] = meta
                    cursor_db.close()
                    conn_db.close()
                except Exception as existing_meta_err:
                    logger.warning(
                        "Could not load existing file metadata for SFTP: %s",
                        existing_meta_err,
                    )

            tables = []
            used_table_names = set()
            for entry in files:
                key = entry.file_key
                existing_meta = existing_file_metadata.get(key) or {}
                locked_table_name = sanitize_identifier(str(existing_meta.get("table") or "").strip())
                is_migrated = bool(existing_meta.get("is_migrated"))
                mapped_table = mapped_lookup.get(key)

                if not mapped_table and file_mapping_mode == "single_table_per_file":
                    mapped_table = _resolve_single_file_mapped_name(
                        file_key=key,
                        single_file_map=single_file_map,
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

                if lock_table_name:
                    table_name = _unique_table_name_for_file(
                        file_key=key,
                        mapped_table_name=locked_table_name,
                        used_names=used_table_names,
                    )
                else:
                    table_name = _unique_table_name_for_file(
                        file_key=key,
                        mapped_table_name=mapped_table,
                        used_names=used_table_names,
                    )

                tables.append(
                    {
                        "table": table_name,
                        "file_key": key,
                        "relative_path": entry.relative_path,
                        "basename": entry.basename,
                        "size_bytes": entry.size_bytes,
                        "last_modified": utc_isoformat(entry.modified_at),
                        "file_type": entry.inferred_file_type or file_type or None,
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
                "root_folder": root_folder,
                "include_subfolders": include_subfolders,
                "file_type": file_type or None,
            }
        finally:
            _close_sftp_client(sftp, transport, sock)

    @staticmethod
    def list_tables_with_schema(data: SFTPListingTables) -> Dict[str, Any]:
        """List SFTP files and infer a schema sample for each table."""
        config = _extract_config(data)
        _validate_source_config(config)
        logger.info(
            "SFTP table listing started | connection_id=%s host=%s root_folder=%s file_type=%s",
            data.connection_id,
            config.get("sftp_host"),
            config.get("root_folder"),
            config.get("file_type"),
        )
        sftp, transport, sock = _open_sftp_client(config)
        try:
            root_folder = _sftp_listing_root_folder(config)
            base_folder_path = _sftp_listing_base_folder(config)
            include_subfolders = str(config.get("include_subfolders") or "").strip().lower() in {"1", "true", "yes", "on"}
            file_type = str(config.get("file_type") or "").strip().lower()

            # Extract custom_primary_key (handle both dict and JSON string)
            custom_primary_key = config.get("custom_primary_key")
            if isinstance(custom_primary_key, str):
                try:
                    custom_primary_key = json.loads(custom_primary_key)
                except Exception:
                    custom_primary_key = {}
            if not isinstance(custom_primary_key, dict):
                custom_primary_key = {}

            load_method = str(config.get("load_method") or "upsert_file_row").strip()
            if load_method == "upsert_custom_key":
                if not isinstance(custom_primary_key, dict) or not custom_primary_key:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "custom_primary_key must be provided as a non-empty dict in the request "
                            "when load_method is 'upsert_custom_key'"
                        ),
                    )

            if root_folder:
                try:
                    sftp.chdir(root_folder)
                except IOError as e:
                    err_msg = str(e).lower()
                    err_no = getattr(e, "errno", None)
                    if isinstance(e, FileNotFoundError) or err_no == 2 or "no such file" in err_msg:
                        raise HTTPException(
                            status_code=404,
                            detail=f"The configured root folder does not exist on SFTP: {root_folder}. Please verify the folder path.",
                        )
                    elif isinstance(e, PermissionError) or err_no == 13 or "permission denied" in err_msg or "access denied" in err_msg:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied to access root folder: {root_folder}. Please verify read permissions for the user.",
                        )
                    else:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Root folder '{root_folder}' is not accessible on SFTP: {str(e)}",
                        )


            listing_start = base_folder_path or "."
            all_files = _walk_sftp_files(sftp, listing_start, listing_start, include_subfolders=include_subfolders)
            all_files = sorted(all_files, key=lambda item: item.file_key.lower())

            existing_sftp_file_keys = {f.file_key for f in all_files}
            file_mapping_mode = str(config.get("file_mapping_method") or config.get("file_mapping_mode") or "single_table_per_file").strip()
            single_file_map = config.get("single_file_table_mapping") or {}

            # ----------------------------------------------------------------
            # DIAG LOG 1: raw value from request before any parsing
            # ----------------------------------------------------------------
            logger.info(
                "[SFTP][DIAG][1] file_mapping_mode='%s' | "
                "single_file_table_mapping type=%s value=%r",
                file_mapping_mode, type(single_file_map).__name__, single_file_map
            )

            # The frontend sends single_file_table_mapping as a JSON-encoded string
            # (e.g. "{\"TATA.csv\":\"TATA\"}"). Parse it into a dict before use.
            # This matches how S3 handles it (amazon_s3_service.py uses json.loads).
            if isinstance(single_file_map, str):
                try:
                    single_file_map = json.loads(single_file_map)
                    if not isinstance(single_file_map, dict):
                        single_file_map = {}
                except Exception as _parse_err:
                    logger.warning("[SFTP][DIAG][1] Failed to parse single_file_table_mapping JSON string: %s", _parse_err)
                    single_file_map = {}

            logger.info(
                "[SFTP][DIAG][2] After JSON parse: single_file_map=%r (%d entries)",
                single_file_map, len(single_file_map)
            )

            # DB fallback: if the frontend didn't send single_file_table_mapping in the request
            # (or it was empty after parsing), load it from the database — exactly matching
            # S3's behavior (amazon_s3_service.py lines 5960-5966).
            if (
                not single_file_map
                and file_mapping_mode == "single_table_per_file"
                and data.connection_id
            ):
                logger.info("[SFTP][DIAG][2b] Mapping empty after parse — attempting DB fallback...")
                try:
                    from utils.database_utils import get_s3_mapping
                    existing_mapping = get_s3_mapping(data.connection_id)
                    logger.info("[SFTP][DIAG][2b] DB mapping fetched: %r", existing_mapping)
                    if (
                        existing_mapping
                        and (existing_mapping.get("file_mapping_mode") or "").strip() == "single_table_per_file"
                    ):
                        single_file_map = existing_mapping.get("single_file_table_mapping") or {}
                        if not isinstance(single_file_map, dict):
                            single_file_map = {}
                        logger.info(
                            "[SFTP][DIAG][2b] Loaded from DB: %d entries → %r",
                            len(single_file_map), single_file_map
                        )
                except Exception as _map_load_err:
                    logger.warning("[SFTP][DIAG][2b] Could not load mapping from DB: %s", _map_load_err)

            logger.info(
                "[SFTP][DIAG][3] FINAL single_file_map=%r | %d entries — this controls file filtering",
                single_file_map, len(single_file_map)
            )

            stale_single_table_names = set()
            mapped_files = set()
            non_existent_files = []
            if file_mapping_mode == "single_table_per_file" and single_file_map:
                for mapped_file_key, mapped_table in single_file_map.items():
                    mapped_key = str(mapped_file_key).strip()
                    if not mapped_key:
                        continue
                    matched = False
                    for key in existing_sftp_file_keys:
                        if key == mapped_key or key.endswith("/" + mapped_key):
                            mapped_files.add(key)
                            matched = True
                            logger.info("[SFTP][LIST_SCHEMA]      Matched: %s  %s", mapped_key, key)
                            break
                    if not matched:
                        non_existent_files.append(mapped_key)
                        normalized_table = sanitize_identifier(str(mapped_table).strip())
                        if normalized_table:
                            stale_single_table_names.add(normalized_table)
                            logger.info(
                                "[SFTP][DIAG][3] Stale: mapped file '%s' not found on SFTP → table '%s' will be pruned",
                                mapped_key, normalized_table
                            )
                if non_existent_files:
                    logger.warning(
                        "[SFTP][LIST_SCHEMA] WARNING: %d file(s) in mapping not found in SFTP folder:",
                        len(non_existent_files),
                    )
                    for missing_file in non_existent_files:
                        logger.warning("[SFTP][LIST_SCHEMA]   Not found: %s", missing_file)
            prune_missing_tables = bool(stale_single_table_names)
            logger.info(
                "[SFTP][DIAG][3] stale_single_table_names=%r | prune_missing_tables=%s",
                stale_single_table_names, prune_missing_tables
            )

            if not all_files:
                stale_tables_from_map = []
                if file_mapping_mode == "single_table_per_file" and single_file_map:
                    stale_tables_from_map = [
                        sanitize_identifier(str(t).strip())
                        for t in single_file_map.values()
                        if sanitize_identifier(str(t).strip())
                    ]
                return {
                    "tables": [],
                    "files_count": 0,
                    "compatible_files_count": 0,
                    "incompatible_files_count": 0,
                    "root_folder": root_folder,
                    "include_subfolders": include_subfolders,
                    "file_mapping_method": config.get("file_mapping_method") or "single_table_per_file",
                    "prune_missing_tables": bool(stale_tables_from_map),
                    "stale_tables_to_prune": sorted(stale_tables_from_map),
                }

            # Query existing table metadata from postgres (similar to S3)
            existing_file_metadata = {}
            if data.connection_id:
                try:
                    conn_db = postgres_connection()
                    cursor_db = conn_db.cursor()
                    cursor_db.execute(
                        """
                        SELECT folder_path, "table", selected, last_synced, primary_keys, is_history
                        FROM source_table_selection
                        WHERE connection_id = %s;
                        """,
                        (data.connection_id,),
                    )
                    for file_path, table_name, selected, last_synced, primary_keys, is_history in (cursor_db.fetchall() or []):
                        if not file_path:
                            continue
                        
                        is_migrated = bool(last_synced)
                        
                        if last_synced and hasattr(last_synced, "isoformat"):
                            last_synced_value = last_synced.isoformat()
                        else:
                            last_synced_value = str(last_synced or "").strip() or None
                        meta = {
                            "table": table_name,
                            "selected": selected,
                            "last_synced": last_synced_value,
                            "is_migrated": is_migrated,
                            "primary_keys": primary_keys,
                            "is_history": is_history,
                        }
                        
                        if file_path not in existing_file_metadata:
                            existing_file_metadata[file_path] = meta
                        else:
                            def _parse_synced(value: str):
                                v = str(value or "").strip()
                                if not v:
                                    return None
                                try:
                                    return datetime.fromisoformat(v)
                                except Exception:
                                    return None

                            current = existing_file_metadata[file_path]
                            current_dt = _parse_synced(current.get("last_synced"))
                            incoming_dt = _parse_synced(meta.get("last_synced"))

                            if incoming_dt and (not current_dt or incoming_dt > current_dt):
                                existing_file_metadata[file_path] = meta
                    cursor_db.close()
                    conn_db.close()
                except Exception as existing_meta_err:
                    logger.warning("Could not load existing file metadata: %s", existing_meta_err)

            # Query Snowflake for existing tables (similar to S3)
            existing_sf_tables = None
            if data.connection_id:
                try:
                    dest_config = config.get("destination_config")
                    if dest_config:
                        existing_sf_tables = _fetch_snowflake_tables_from_config(dest_config)
                except Exception as e:
                    logger.warning("Failed to check Snowflake tables: %s", e)

            logger.info(
                "[SFTP][DIAG][4] Calling resolve_file_entries: "
                "total_files=%d | single_file_map=%r",
                len(all_files), single_file_map
            )
            if file_mapping_mode == "single_table_per_file" and single_file_map:
                if mapped_files:
                    all_files = [entry for entry in all_files if entry.file_key in mapped_files]
                    logger.info(
                        "[SFTP][LIST_SCHEMA] Filtered to %d mapped file(s) for schema refresh",
                        len(all_files),
                    )
                else:
                    logger.warning(
                        "[SFTP][LIST_SCHEMA] No files matched single_file_table_mapping; continuing so stale mappings can be pruned"
                    )
                    all_files = []
            table_groups, meta = resolve_file_entries(
                all_files,
                single_file_table_mapping=single_file_map,  # use resolved map (with DB fallback)
                file_mapping_method=file_mapping_mode,
                multi_files_prefix=config.get("multi_files_prefix"),
                multi_files_table_name=config.get("multi_files_table_name"),
            )
            logger.info(
                "[SFTP][DIAG][4] resolve_file_entries result: table_groups keys=%r",
                list(table_groups.keys())
            )

            if file_mapping_mode == "single_table_per_file":
                if not single_file_map:
                    summary_msg = (
                        "Update schema failed: single_file_table_mapping is required for single_table_per_file mode."
                    )
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file="single_table_per_file",
                        message=summary_msg,
                    )
                    raise HTTPException(status_code=400, detail=summary_msg)

            tables: List[Dict[str, Any]] = []
            reader_kwargs = _build_reader_kwargs(config, file_type)
            grouped_file_mapping: Dict[str, List[str]] = {}

            for table_name in sorted(table_groups.keys()):
                files_for_table = table_groups.get(table_name) or []
                if not files_for_table:
                    continue
                representative = files_for_table[0]
                sample_df = None
                is_compatible, type_reason = _validate_sftp_file_compatible_with_type(
                    sftp,
                    representative.file_key,
                    file_type,
                    delimiter=config.get("tsv_delimiter") or config.get("delimiter"),
                )
                if not is_compatible:
                    err_msg = _build_schema_validation_error_message(representative.file_key, file_type, type_reason)
                    logger.warning("[SFTP][LIST_SCHEMA] %s", err_msg)
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file=table_name,
                        message=err_msg,
                    )
                    raise HTTPException(status_code=400, detail=err_msg)

                remote_file = sftp.open(representative.file_key, "rb")
                try:
                    sample_df = read_dataframe_sample(remote_file, **reader_kwargs)
                    sample_df = _add_metadata_to_df(sample_df, representative.file_key, representative.modified_at)
                finally:
                    try:
                        remote_file.close()
                    except Exception:
                        pass

                load_method = str(config.get("load_method") or "upsert_file_row").strip()
                columns = infer_columns_from_dataframe(sample_df, load_method=load_method)

                # Apply custom primary key mapping if load_method is upsert_custom_key
                if load_method == "upsert_custom_key" and custom_primary_key:
                    table_name_upper = table_name.upper()
                    pk_cols = None
                    for key, cols in custom_primary_key.items():
                        if key.upper() == table_name_upper:
                            pk_cols = [c.upper() for c in cols]
                            break
                    if pk_cols:
                        for col_name, meta in columns.items():
                            if col_name.upper() in pk_cols:
                                meta["is_primary_key"] = True
                                meta["data_type"] = "VARCHAR(512)"

                # Resolve file_key to preserve path mapping prefix (long path) matching S3
                file_key_to_use = representative.file_key
                if file_mapping_mode == "single_table_per_file" and single_file_map:
                    for mapped_file_key in single_file_map.keys():
                        if representative.file_key == mapped_file_key or representative.file_key.endswith("/" + mapped_file_key) or mapped_file_key.endswith("/" + representative.file_key):
                            file_key_to_use = mapped_file_key
                            break

                # Resolve locking details
                lookup_key = config.get("base_folder_path") if config.get("file_mapping_method") == "multi_files_single_table" else file_key_to_use
                existing_meta = existing_file_metadata.get(lookup_key) or {}
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
                        "last_modified": utc_isoformat(representative.modified_at),
                        "columns": columns,
                        "grouped_files": [entry.file_key for entry in files_for_table],
                        
                        # Lock metadata matching S3
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

            compatible_count = len(all_files)
            return {
                "tables": tables,
                "files_count": meta.get("candidate_files_count", len(all_files)),
                "compatible_files_count": compatible_count,
                "incompatible_files_count": 0,
                "prune_missing_tables": prune_missing_tables,
                "stale_tables_to_prune": sorted(stale_single_table_names),
                "file_mapping_method": meta.get("file_mapping_method") or config.get("file_mapping_method") or "single_table_per_file",
                "base_folder_path": config.get("base_folder_path") or "",
                "table_to_files_mapping": grouped_file_mapping,
            }
        finally:
            _close_sftp_client(sftp, transport, sock)

    @staticmethod
    def preview_pattern(data: SFTPPatternPreviewRequest) -> Dict[str, Any]:
        """Preview SFTP files that match the configured multi-file pattern."""
        config = _extract_config(data) if hasattr(data, "SFTP") else {}
        if not isinstance(config, dict):
            config = {}
        _validate_source_config(config)
        logger.info(
            "SFTP pattern preview started | connection_id=%s pattern=%s",
            data.connection_id,
            data.multi_files_prefix,
        )
        sftp, transport, sock = _open_sftp_client(config)
        try:
            root_folder = _sftp_listing_root_folder(config)
            base_folder_path = _sftp_listing_base_folder(config)
            include_subfolders = str(config.get("include_subfolders") or "").strip().lower() in {"1", "true", "yes", "on"}
            file_type = str(config.get("file_type") or "").strip().lower()
            pattern = str(data.multi_files_prefix or config.get("multi_files_prefix") or "").strip()
            max_results = int(data.max_results or 50)

            if root_folder:
                try:
                    sftp.chdir(root_folder)
                except IOError as e:
                    err_msg = str(e).lower()
                    err_no = getattr(e, "errno", None)
                    if isinstance(e, FileNotFoundError) or err_no == 2 or "no such file" in err_msg:
                        raise HTTPException(
                            status_code=404,
                            detail=f"The configured root folder does not exist on SFTP: {root_folder}. Please verify the folder path.",
                        )
                    elif isinstance(e, PermissionError) or err_no == 13 or "permission denied" in err_msg or "access denied" in err_msg:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied to access root folder: {root_folder}. Please verify read permissions for the user.",
                        )
                    else:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Root folder '{root_folder}' is not accessible on SFTP: {str(e)}",
                        )
            listing_start = base_folder_path or "."
            all_files = _walk_sftp_files(sftp, listing_start, listing_start, include_subfolders=include_subfolders)
            if pattern:
                logger.info("[SFTP][PREVIEW] multi_files_prefix is deprecated and ignored (pattern='%s')", pattern)

            matched_files = sorted(all_files, key=lambda item: item.file_key.lower())

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
                        "size_bytes": entry.size_bytes,
                        "last_modified": utc_isoformat(entry.modified_at),
                        "matches_pattern": True,
                    }
                    for entry in matched_files[:max_results]
                ],
                "pattern_ignored": bool(pattern),
            }
        finally:
            _close_sftp_client(sftp, transport, sock)

    @staticmethod
    async def suggest_primary_keys(
        request: SFTPPKSuggestionRequest
    ) -> Dict[str, Any]:
        """
        Analyze SFTP file columns and return column statistics
        so the user can select custom primary keys.
        """
        import pandas as pd
        sftp_config = request.sftp
        log(f"[SFTP][PK_SUGGEST] Starting PK suggestion")

        # 1. Enforce business rule
        if sftp_config.load_method != LoadMethod.upsert_custom_key:
            raise ValueError("Primary key suggestion is only supported for load_method = upsert_custom_key")

        # 2. Skip unsupported formats early
        if sftp_config.file_type == FileType.xml and ((sftp_config.xml_mode.value if sftp_config.xml_mode else "packed") == "packed"):
            return {
                "status": "success",
                "tables": [],
                "message": "XML packed format does not expose columns"
            }

        if sftp_config.file_type == FileType.json and sftp_config.json_mode == JSONMode.packed:
            return {
                "status": "success",
                "tables": [],
                "message": "JSON packed format does not expose columns"
            }

        # Convert request.sftp to config dictionary
        config = sftp_config.dict() if hasattr(sftp_config, "dict") else sftp_config.model_dump()
        from enum import Enum
        for k, v in list(config.items()):
            if isinstance(v, Enum):
                config[k] = v.value
            elif isinstance(v, dict):
                for sub_k, sub_v in list(v.items()):
                    if isinstance(sub_v, Enum):
                        v[sub_k] = sub_v.value
        _validate_source_config(config)

        # 3. Open SFTP client
        sftp, transport, sock = _open_sftp_client(config)
        try:
            root_folder = _sftp_listing_root_folder(config)
            base_folder_path = _sftp_listing_base_folder(config)
            if root_folder:
                try:
                    sftp.chdir(root_folder)
                except IOError as e:
                    err_msg = str(e).lower()
                    err_no = getattr(e, "errno", None)
                    if isinstance(e, FileNotFoundError) or err_no == 2 or "no such file" in err_msg:
                        raise HTTPException(
                            status_code=404,
                            detail=f"The configured root folder does not exist on SFTP: {root_folder}. Please verify the folder path.",
                        )
                    elif isinstance(e, PermissionError) or err_no == 13 or "permission denied" in err_msg or "access denied" in err_msg:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied to access root folder: {root_folder}. Please verify read permissions for the user.",
                        )
                    else:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Root folder '{root_folder}' is not accessible on SFTP: {str(e)}",
                        )


            include_subfolders = sftp_config.include_subfolders
            file_type = sftp_config.file_type.value
            listing_start = base_folder_path or "."

            # Save the initial working directory before any chdir
            initial_dir = sftp.getcwd()

            # 4. Extract file keys based on mapping method
            if sftp_config.file_mapping_method == FileMappingMethod.single_table_per_file:
                # Walk SFTP to find existing files first
                all_files = _walk_sftp_files(sftp, listing_start, listing_start, include_subfolders=include_subfolders)
                existing_file_keys = {entry.file_key for entry in all_files}

                file_keys = []
                seen_basenames = set()
                mapping = sftp_config.single_file_table_mapping or {}
                for mapped_file_key in mapping.keys():
                    matched_key = None
                    for key in existing_file_keys:
                        # Robust 3-way path check
                        if key == mapped_file_key or key.endswith("/" + mapped_file_key) or mapped_file_key.endswith("/" + key):
                            matched_key = key
                            break
                    if matched_key:
                        norm_key = normalize_posix_path(matched_key)
                        if norm_key not in seen_basenames:
                            seen_basenames.add(norm_key)
                            file_keys.append(matched_key)
                is_multi_file_mode = False
            else:
                is_multi_file_mode = True
                # Multi-file mode resolves files directly from root folder
                all_files = _walk_sftp_files(sftp, listing_start, listing_start, include_subfolders=include_subfolders)
                file_keys = [entry.file_key for entry in all_files]
                log(f"[SFTP][PK_SUGGEST] Found {len(file_keys)} files in scope")

            if not file_keys:
                return {
                    "status": "success",
                    "tables": [],
                    "message": "No files provided for analysis"
                }

            log(f"[SFTP][PK_SUGGEST] Files to analyze: {len(file_keys)}")

            # 5. Resolve file format options
            if sftp_config.file_type == FileType.csv:
                opts = sftp_config.csv_options or DelimitedFileOptions()
            elif sftp_config.file_type == FileType.tsv:
                opts = sftp_config.tsv_options or DelimitedFileOptions()
            else:
                opts = None

            # 6. Read files & infer columns
            tables_buffer = []

            # OPTIMIZATION: For multi-file setups, only analyze a sample of files (e.g., first 5)
            # to prevent timeouts and memory exhaustion while still getting accurate schema.
            files_to_read = file_keys[:5] if is_multi_file_mode else file_keys
            log(f"[SFTP][PK_SUGGEST] Analyzing {len(files_to_read)} files (out of {len(file_keys)})")

            reader_kwargs = {
                "file_type": file_type,
                "delimiter": opts.delimiter if opts else None,
                "quotechar": opts.quotechar if opts else None,
                "escape_char": opts.escape_char if opts else None,
                "skipheaderlines": opts.skipheaderlines if opts else None,
                "skip_footer_lines": opts.skip_footer_lines if opts else None,
                "line_separator": opts.line_separator if opts else None,
                "json_mode": sftp_config.json_mode.value if sftp_config.json_mode else None,
                "xml_mode": sftp_config.xml_mode.value if sftp_config.xml_mode else None,
                "sample_rows": request.sample_rows,
            }

            for file_key in files_to_read:
                log(f"[SFTP][PK_SUGGEST] Reading file: {file_key}")
                remote_file = None
                mtime = None
                try:
                    # Robust open with path resolution fallbacks
                    remote_file = None
                    open_exception = None
                    try:
                        # Fallback 1: Try direct open
                        remote_file = sftp.open(file_key, "rb")
                    except Exception as e1:
                        open_exception = e1
                        # Fallback 2: Try relative to initial directory
                        try:
                            sftp.chdir(initial_dir)
                            remote_file = sftp.open(file_key, "rb")
                        except Exception as e2:
                            open_exception = e2
                            # Fallback 3: Try basename in root_folder
                            try:
                                if root_folder:
                                    sftp.chdir(root_folder)
                                basename = file_key.split("/")[-1]
                                remote_file = sftp.open(basename, "rb")
                            except Exception as e3:
                                open_exception = e3

                    if not remote_file:
                        raise open_exception or FileNotFoundError(f"Could not open file: {file_key}")

                    # Restore directory to root_folder for subsequent operations
                    if root_folder:
                        try:
                            sftp.chdir(root_folder)
                        except Exception:
                            pass

                    try:
                        # Try stat using whatever path worked
                        head_stat = sftp.stat(file_key)
                    except Exception:
                        try:
                            head_stat = sftp.stat(file_key.split("/")[-1])
                        except Exception:
                            head_stat = None
                    mtime = datetime.fromtimestamp(head_stat.st_mtime, tz=timezone.utc) if head_stat else None

                    df = read_dataframe_sample(remote_file, **reader_kwargs)
                    df = _add_metadata_to_df(df, file_key, mtime)
                except Exception as read_err:
                    log(f"[SFTP][PK_SUGGEST] Error reading file {file_key}: {read_err}")
                    continue
                finally:
                    if remote_file:
                        try:
                            remote_file.close()
                        except Exception:
                            pass

                if df is None or df.empty:
                    log(f"[SFTP][PK_SUGGEST] Skipping empty file: {file_key}")
                    continue

                # Resolve table name
                internal_name = table_from_file_key(file_key)
                if is_multi_file_mode and sftp_config.multi_files_table_name:
                    table_name = sanitize_identifier(str(sftp_config.multi_files_table_name).strip())
                else:
                    mapping = sftp_config.single_file_table_mapping or {}
                    mapped_name = mapping.get(file_key) or mapping.get(file_key.split("/")[-1])
                    table_name = sanitize_identifier(str(mapped_name).strip()) if mapped_name else internal_name

                columns = extract_columns_for_pk_suggestion(df)

                tables_buffer.append({
                    "table_name": table_name,
                    "file_key": file_key,
                    "columns": columns,
                    "df": df,
                })

            if not tables_buffer:
                raise HTTPException(
                    status_code=400,
                    detail="No readable files found for primary key suggestion. Check file paths and format options.",
                )

            # 7. Multi-file schema validation
            if is_multi_file_mode and len(tables_buffer) > 1:
                expected_columns = None
                if request.connection_id:
                    try:
                        target_table = (
                            sanitize_identifier(str(sftp_config.multi_files_table_name).strip())
                            if sftp_config.multi_files_table_name
                            else None
                        )
                        if target_table:
                            conn_db = postgres_connection()
                            cur_db = conn_db.cursor()
                            cur_db.execute(
                                """
                                SELECT table_fields
                                FROM source_table_selection
                                WHERE connection_id = %s AND UPPER("table") = UPPER(%s)
                                """,
                                (request.connection_id, target_table),
                            )
                            row = cur_db.fetchone()
                            cur_db.close()
                            conn_db.close()
                            if row and row[0]:
                                table_fields = row[0]
                                if isinstance(table_fields, str):
                                    try:
                                        table_fields = json.loads(table_fields)
                                    except Exception:
                                        table_fields = {}
                                if isinstance(table_fields, dict):
                                    expected_columns = {str(k).strip() for k in table_fields.keys() if str(k).strip()}
                    except Exception as e:
                        log(f"[SFTP][PK_SUGGEST] Failed to read expected schema for multi-file PK suggestion: {e}")

                valid_files = []
                skipped_files = []

                if expected_columns:
                    for item in tables_buffer:
                        if set(item["columns"].keys()) == expected_columns:
                            valid_files.append(item)
                        else:
                            skipped_files.append(item["file_key"])
                    if not valid_files:
                        expected_columns = None

                if not expected_columns:
                    reference = tables_buffer[0]["columns"]
                    for item in tables_buffer:
                        if set(item["columns"].keys()) == set(reference.keys()):
                            valid_files.append(item)
                        else:
                            skipped_files.append(item["file_key"])

                tables_buffer = valid_files[:1]
                tables_buffer[0]["file_keys"] = [i["file_key"] for i in valid_files]
                tables_buffer[0]["skipped_files"] = skipped_files

            # 8. Cardinality analysis
            analyzed_tables = []
            table_pk_state = {}
            snowflake_tables = _fetch_snowflake_tables_from_config(
                getattr(sftp_config, "destination_config", None)
            )
            if snowflake_tables is not None:
                log(f"[SFTP][PK_SUGGEST][DEBUG] Snowflake tables available: {len(snowflake_tables)}")

            if request.connection_id:
                try:
                    conn_db = postgres_connection()
                    cur_db = conn_db.cursor()
                    cur_db.execute(
                        """
                        SELECT "table", primary_keys, last_synced
                        FROM source_table_selection
                        WHERE connection_id = %s
                        """,
                        (request.connection_id,),
                    )
                    for table_name, primary_keys_raw, last_synced in (cur_db.fetchall() or []):
                        table_key = str(table_name or "").strip().upper()
                        if not table_key:
                            continue

                        existing_primary_keys = []
                        if isinstance(primary_keys_raw, list):
                            existing_primary_keys = [
                                str(pk).strip() for pk in primary_keys_raw if str(pk).strip()
                            ]
                        elif isinstance(primary_keys_raw, str):
                            try:
                                parsed_pk = json.loads(primary_keys_raw)
                                if isinstance(parsed_pk, list):
                                    existing_primary_keys = [
                                        str(pk).strip() for pk in parsed_pk if str(pk).strip()
                                    ]
                            except Exception:
                                existing_primary_keys = []
                                log(
                                    f"[SFTP][PK_SUGGEST][WARN] Failed to parse primary_keys for "
                                    f"table '{table_key}'. Raw value: {primary_keys_raw}"
                                )

                        table_pk_state[table_key] = {
                            "pk_locked": bool(last_synced) and bool(existing_primary_keys),
                            "existing_primary_keys": existing_primary_keys,
                        }
                    cur_db.close()
                    conn_db.close()
                except Exception as e:
                    log(f"[SFTP][PK_SUGGEST] Failed to read PK lock metadata: {e}")

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
                        if orig_col == col_name or sanitize_identifier(orig_col) == col_name or orig_col.upper() == col_name.upper():
                            series = df_sample[orig_col]
                            break

                    if series is None:
                        column_obj = {
                            "column_name": col_name,
                            "data_type": col_meta.get("data_type"),
                            "uniqueness_score": 0.0,
                            "non_null_score": 0.0,
                            "warnings": ["Column not found in sample"]
                        }
                        if "is_selected" in col_meta:
                            column_obj["is_selected"] = col_meta["is_selected"]
                        analyzed_columns.append(column_obj)
                        continue

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

                analyzed_columns.sort(
                    key=lambda c: item["columns"][c["column_name"]]["ordinal_position"]
                )
                table_key = str(item["table_name"]).strip().upper()
                table_state = table_pk_state.get(table_key, {})
                if (
                    snowflake_tables is not None
                    and table_key
                    and table_key not in snowflake_tables
                    and table_state.get("pk_locked")
                ):
                    log(
                        f"[SFTP][PK_SUGGEST][DEBUG] Table '{table_key}' not found in Snowflake; "
                        "unlocking PK (migrated)"
                    )
                    saved_pks = table_state.get("existing_primary_keys", [])
                    table_state = {
                        "pk_locked": False,
                        "existing_primary_keys": saved_pks,
                    }

                analyzed_tables.append({
                    "table_name": item["table_name"],
                    "file_key": item.get("file_key"),
                    "file_keys": item.get("file_keys"),
                    "rows_analyzed": rows_analyzed,
                    "columns": analyzed_columns,
                    "skipped_files": item.get("skipped_files"),
                    "pk_locked": table_state.get("pk_locked", False),
                    "existing_primary_keys": table_state.get("existing_primary_keys", []),
                })

            return {
                "status": "success",
                "tables": analyzed_tables,
                "total_files_analyzed": len(file_keys),
                "total_rows_analyzed": sum(t["rows_analyzed"] for t in analyzed_tables),
            }
        finally:
            _close_sftp_client(sftp, transport, sock)

    @staticmethod
    def refresh_and_update_sftp_tables_in_database(
        connection_id: int,
        tables_result: Dict[str, Any],
        job_name: Optional[str],
        migration_session_id: Optional[int] = None,
    ) -> Dict[str, int]:
        """Persist discovered SFTP tables and mapping metadata to PostgreSQL."""
        cmp_id = int(tables_result.get("cmp_id") or 0)
        user_id = int(tables_result.get("user_id") or 0)
        source_id = tables_result.get("source_id")
        return _upsert_source_tables_and_mapping(
            connection_id=connection_id,
            tables_result=tables_result,
            single_file_mapping=tables_result.get("single_file_table_mapping") or {},
            table_to_files_mapping=tables_result.get("table_to_files_mapping") or {},
            cmp_id=cmp_id,
            user_id=user_id,
            source_id=source_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
        )
