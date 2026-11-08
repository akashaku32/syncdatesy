# services/amazon_s3_service.py
"""
Amazon S3 to Snowflake Migration Service - Complete Consolidated Implementation

This is a comprehensive, DRY service file that consolidates all S3 to Snowflake functionality:
- Connection validation and authentication
- Table/file listing (with and without schema)
- Schema inference and refresh
- Full migration with delta reload, drop & reload
- Support for all file types: CSV, TSV, JSON, XML, Parquet, Avro
- All load methods: UPSERT, APPEND, CUSTOM PK
- All file mapping modes: Single table/file, Multi-file  Single table

Features:
- Streaming chunk-based processing for memory efficiency
- GCS staging for Snowflake COPY INTO
- Comprehensive error handling and logging
- Email notifications for failures
- Progress tracking and status updates
"""

from __future__ import annotations
import io
import os
import re
import csv
import codecs
import calendar
import json
import time
import traceback
import socket
import logging
import html
from uuid import uuid4
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Set
from botocore.exceptions import ClientError, EndpointConnectionError, ParamValidationError
import boto3
import fastavro
import pandas as pd
import numpy as np
import pyarrow.parquet as pq
import snowflake.connector
import xml.etree.ElementTree as ET
from fastapi import HTTPException
from google.cloud import storage
from utils.celery_cleanup_utils import handle_migration_completion
from utils.s3_utils import (
    log,
    normalized_prefix,
    sanitize_identifier,
    validate_s3_bucket_name,
    detect_s3_bucket_region,
    s3_client,
    list_files_recursive,
    list_files_non_recursive,
)
from utils.gcp_utils import (
    gcs_client,
    ensure_gcs_storage_integration,
    get_gcp_service_account,
    ensure_bucket_iam_for_integration,
    assert_allowed_prefix_matches,
)

# Import models
from models.amazon_s3_models import (
    AmazonS3ValidationRequest,
    AmazonS3ListingTables,
    AmazonS3MigrationRequest,
    AmazonS3PKSuggestionRequest,
    AmazonS3PatternPreviewRequest,
    FileType,FileMappingMethod,LoadMethod,JSONMode,DelimitedFileOptions
)

# Import utilities
from utils.database_utils import (
    postgres_connection,
    update_table_status_snowflake,
    data_transfer_logs,
    get_last_checked_dates_and_fields,
    get_s3_mapping,
    save_s3_mapping,
    get_migration_record_id,
)
from utils.error_utils import handle_service_error, log_error_to_files, insert_tool_log, log_migration_completion
from utils.email_utils import EmailConfiguration, send_email_with_retry, send_error_notification
from utils.email_templates import create_connection_failure_email
from services.snowflake.service import normalize_pem_key, SnowflakeService
# Import staging table cleanup utility
from utils.snowflake_utils import drop_staging_table
# Import progress tracking utility
from utils.simple_progress_tracker import SimpleProgressTracker


logger = logging.getLogger(__name__)


def table_from_file_key(key: str) -> str:
    """Derive table name from S3 file key."""
    basename = key.split("/")[-1]
    name = os.path.splitext(basename)[0]
    return sanitize_identifier(name)

def extract_date_from_filename(filename: str) -> Tuple[Optional[date], float]:
    """Extract date from filename, return (date, score)."""
    try:
        m = re.search(r"(20\d{2})[-_ ]?(0[1-9]|1[0-2])", filename)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            return date(year, month, 1), 1.0
        m = re.search(r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[-_ ]?(20\d{2})", filename)
        if m:
            month_str, year = m.group(1).lower(), int(m.group(2))
            month_map = {m.lower(): i + 1 for i, m in enumerate(calendar.month_abbr[1:])}
            return date(year, month_map[month_str[:3]], 1), 0.9
        return None, 0.0
    except Exception:
        return None, 0.0

def suggest_common_table_name(keys: List[str]) -> str:
    """Suggest common table name from file keys."""
    if not keys:
        return "UNIFIED_TABLE"
    basenames = [os.path.splitext(k.split("/")[-1])[0] for k in keys]
    common_prefix = os.path.commonprefix(basenames)
    if common_prefix:
        return sanitize_identifier(common_prefix)
    return sanitize_identifier(basenames[0])


def _structured_print(*args, **kwargs):
    """Compatibility wrapper: route legacy print calls into structured logs."""
    sep = kwargs.get("sep", " ")
    msg = sep.join(str(arg) for arg in args) if args else ""
    logger.info(msg)


print = _structured_print

# =============================================================================
# CONSTANTS & CONFIGURATION
# =============================================================================

from utils.migration_storage_config import (
    GCP_PROJECT_ID,
    GCS_BUCKET_NAME,
    GCS_BUCKET_URL,
    SF_STORAGE_INTEGRATION,
    SNOWFLAKE_STORAGE_INTEGRATION,
    resolve_gcs_bucket_name,
    resolve_gcp_project_id,
    resolve_snowflake_storage_integration,
)

def gcs_join(*parts: str) -> str:
    """
    Join GCS path parts into a single path string.
    
    This function normalizes path components by removing leading/trailing slashes
    and joining them with a single forward slash. Used to construct GCS object paths
    for staging files before loading into Snowflake.
    
    Args:
        *parts: Variable number of path components to join
        
    Returns:
        str: Normalized GCS path (e.g., "table_name/chunk_0001_20231221T120000.csv")
        
    Example:
        >>> gcs_join("table_name", "chunk_0001.csv")
        'table_name/chunk_0001.csv'
        >>> gcs_join("/table_name/", "/chunk_0001.csv/")
        'table_name/chunk_0001.csv'
    """
    return "/".join(p.strip("/") for p in parts if p)


def sanitize_column_name(name: str) -> str:
    """
    Sanitize column name to be Snowflake-compliant.
    
    Snowflake identifiers must:
    - Start with a letter or underscore
    - Contain only alphanumeric characters and underscores
    - Be case-insensitive (we uppercase for consistency)
    
    This function ensures column names from source files are valid Snowflake identifiers
    by removing invalid characters, replacing spaces with underscores, and ensuring
    the name starts with a valid character.
    
    Args:
        name: Original column name from source file
        
    Returns:
        str: Sanitized column name in UPPERCASE (Snowflake convention)
        
    Example:
        >>> sanitize_column_name("User Name")
        'USER_NAME'
        >>> sanitize_column_name("123column")
        '_123COLUMN'
        >>> sanitize_column_name("col-name")
        'COLNAME'
    """
    # Remove leading/trailing whitespace and replace spaces with underscores
    cleaned = name.strip().replace(" ", "_")
    # Remove all characters except alphanumeric and underscores
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", cleaned)
    # Ensure name starts with letter or underscore (Snowflake requirement)
    if cleaned and not cleaned[0].isalpha() and cleaned[0] != "_":
        cleaned = "_" + cleaned
    return cleaned.upper()


def staging_name(table: str) -> str:
    """
    Generate staging table name from main table name with timestamp.
    
    Staging tables are temporary tables used during migration to load data
    before merging into the final table. This prevents partial data in the
    main table if migration fails mid-process.
    
    Args:
        table: Main table name (e.g., "CUSTOMERS")
        
    Returns:
        str: Staging table name with timestamp (e.g., "CUSTOMERS__STAGING_20260202_143022")
        
    Example:
        >>> staging_name("CUSTOMERS")
        'CUSTOMERS__STAGING_20260202_143022'
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f"{table}__STAGING_{timestamp}"


def Q(x: str) -> str:
    """
    Quote Snowflake identifier for use in SQL statements.
    
    Snowflake uses double quotes for case-sensitive identifiers. This function
    wraps identifiers to ensure they are properly quoted in SQL statements,
    preserving case and allowing special characters in names.
    
    Args:
        x: Identifier name (table, column, schema, etc.)
        
    Returns:
        str: Quoted identifier (e.g., '"CUSTOMERS"')
        
    Example:
        >>> Q("CUSTOMERS")
        '"CUSTOMERS"'
        >>> Q("My Table")
        '"My Table"'
    """
    return f'"{x}"'


def normalize_snowflake_identifier(value: Optional[str]) -> Optional[str]:
    """Normalize Snowflake object identifiers to safe uppercase names."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() == "none":
        return None
    return sanitize_identifier(raw)


def normalize_and_log_snowflake_identifier(
    value: Optional[str],
    field_name: str,
) -> Optional[str]:
    """
    Normalize Snowflake identifier and log explicit canonicalization when changed.
    """
    normalized = normalize_snowflake_identifier(value)
    if value is None:
        return normalized
    raw = str(value).strip()
    if not raw or raw.lower() == "none":
        return normalized
    raw_upper = raw.upper()
    if normalized and normalized != raw_upper:
        log(
            f"[MIGRATION]  Normalized Snowflake {field_name}: "
            f"'{raw}' -> '{normalized}'"
        )
    return normalized


def _unique_table_name_for_file(
    file_key: str,
    mapped_table_name: Optional[str],
    used_names: set[str],
) -> str:
    """
    Build a stable unique table name for listing/inference flows.
    If the base name collides (e.g., same filename under different folders),
    add parent-folder suffixes, then numeric suffix as fallback.
    """
    base = sanitize_identifier(str(mapped_table_name).strip()) if mapped_table_name else table_from_file_key(file_key)
    if base not in used_names:
        used_names.add(base)
        return base

    path_parts = [sanitize_identifier(p) for p in str(file_key).split("/")[:-1] if str(p).strip()]
    path_parts = [p for p in path_parts if p]

    for depth in range(1, len(path_parts) + 1):
        suffix = "_".join(path_parts[-depth:])
        candidate = sanitize_identifier(f"{base}_{suffix}")
        if candidate and candidate not in used_names:
            used_names.add(candidate)
            return candidate

    idx = 2
    while True:
        candidate = sanitize_identifier(f"{base}_{idx}")
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        idx += 1


def _build_file_basename_counts(file_keys: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for fk in file_keys or []:
        key = str(fk or "").strip()
        if not key:
            continue
        base = key.split("/")[-1].strip()
        if not base:
            continue
        counts[base] = counts.get(base, 0) + 1
    return counts


def _resolve_single_file_mapped_name(
    file_key: str,
    single_file_map: Dict[str, Any],
    basename_counts: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    key = str(file_key or "").strip()
    if not key or not isinstance(single_file_map, dict) or not single_file_map:
        return None

    mapped = single_file_map.get(key)
    if mapped is not None and str(mapped).strip():
        return sanitize_identifier(str(mapped).strip())

    base = key.split("/")[-1].strip()
    if not base:
        return None
    mapped = single_file_map.get(base)
    if mapped is None or not str(mapped).strip():
        return None

    if basename_counts and basename_counts.get(base, 0) > 1:
        log(
            f"[S3][MAP]  Ambiguous basename mapping ignored for '{base}' "
            f"(multiple files share same name). Use full file key mapping."
        )
        return None

    return sanitize_identifier(str(mapped).strip())


def _normalize_multi_file_mapping_legacy(mapping: Any) -> Dict[str, List[str]]:
    """
    Normalize multi-file mapping to legacy format:
    {TABLE_NAME: [file_key, ...]}
    Accepts legacy list inputs and safely handles malformed values.
    """
    if not isinstance(mapping, dict):
        return {}

    normalized: Dict[str, List[str]] = {}
    for raw_table, raw_value in mapping.items():
        table_name = sanitize_identifier(str(raw_table).strip())
        if not table_name:
            continue

        if isinstance(raw_value, list):
            file_keys = raw_value
        else:
            file_keys = []

        cleaned: List[str] = []
        seen = set()
        for fk in file_keys:
            key = str(fk).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)

        normalized[table_name] = cleaned

    return normalized

DISALLOWED_CUSTOM_PK_COLUMNS = {"_SPECT_SYNCED", "_SPECT_DELETED"}
CUSTOM_PK_COLUMN_TYPE = "VARCHAR(512)"
VARCHAR_WIDEN_MARGIN = 256
SNOWFLAKE_MAX_VARCHAR_LENGTH = 16777216

def sanitize_custom_pk_columns(user_pk: List[str]) -> List[str]:
    """
    Normalize and filter custom PK columns for S3 migrations.
    Explicitly disallow internal metadata columns that must never be used as PK.
    """
    normalized = [sanitize_column_name(c).upper() for c in (user_pk or [])]
    return [c for c in normalized if c not in DISALLOWED_CUSTOM_PK_COLUMNS]


def normalize_delimiter(val: Any, file_type: str = "csv") -> str:
    """
    Normalize delimiter character with type-specific defaults.
    
    If no delimiter is provided, uses defaults based on file type:
    - CSV: comma (",")
    - TSV: tab ("\t")
    - Other: comma (",")
    
    Args:
        val: Delimiter value (e.g., ",", "\t", "|", or None)
        file_type: File type ("csv", "tsv", etc.)
        
    Returns:
        str: Normalized delimiter character
    """
    if val not in (None, "", False):
        s = str(val).strip()
        escape_map = {
            r"\t": "\t",
            r"\n": "\n",
            r"\r": "\r",
        }
        s = escape_map.get(s, s)
        if not s:
            # Empty after normalization: fall back to file-type default
            ft = file_type.lower() if file_type else ""
            return "\t" if ft == "tsv" else ","
        return s[0]
    
    ft = file_type.lower() if file_type else ""
    if ft == "tsv":
        return "\t"
    return ","


def default_multi_files_prefix(file_type: Optional[str]) -> str:
    """
    Return default glob pattern for multi-file matching based on selected file type.
    Example: csv -> *.csv
    """
    ft = (file_type or "").strip().lower()
    if not ft:
        return ""
    return f"*.{ft}"


def normalize_escapechar(val: Any) -> Optional[str]:
    """
    Normalize escape character for CSV/TSV parsing.
    Returns None when no escape char is configured.
    """
    if val in (None, "", False):
        return None
    s = str(val).strip()
    if not s:
        return None
    escape_map = {
        r"\t": "\t",
        r"\n": "\n",
        r"\r": "\r",
        r"\\": "\\",
    }
    s = escape_map.get(s, s)
    return s[0]


def normalize_quotechar(val: Any) -> str:
    """
    Normalize quote character for CSV/TSV parsing.
    Accepts literal escapes like "\\\"" and returns a single character.
    Return an empty string for disabled quoting, as expected by both readers.
    """
    if val in (None, "", False):
        return ""
    s = str(val).strip()
    if not s:
        return ""
    escape_map = {
        r"\"": "\"",
        r"\t": "\t",
        r"\n": "\n",
        r"\r": "\r",
        r"\\": "\\",
    }
    s = escape_map.get(s, s)
    return s[0]


def normalize_line_separator(val: Any) -> Optional[str]:
    """
    Normalize line separator for CSV/TSV parsing.
    Accepts literal escapes like "\\n", "\\r", "\\r\\n", "\\t".
    """
    if val in (None, "", False):
        return None
    s = str(val)
    if not s:
        return None
    escape_map = {
        r"\n": "\n",
        r"\r": "\r",
        r"\r\n": "\r\n",
        r"\t": "\t",
    }
    return escape_map.get(s, s)


def _debug_count_bad_csv_lines(
    text: str,
    delimiter: str,
    quotechar: Optional[str],
    escapechar: Optional[str],
    skipheaderlines: int,
    skip_footer_lines: int = 0,
    max_lines: int = 2000,
) -> Dict[str, int]:
    """
    Best-effort pre-scan of a CSV/TSV text prefix to estimate malformed rows.
    This is only for debug logging; it should never raise.
    """
    stats: Dict[str, Any] = {
        "lines_scanned": 0,
        "data_lines": 0,
        "blank_lines": 0,
        "bad_lines": 0,
        "expected_cols": 0,
        "header_line_num": None,
        "header_line": None,
        "first_bad_line_num": None,
        "first_bad_line": None,
        "first_bad_cols": None,
        "bad_lines_detail": [],
    }
    try:
        q = normalize_quotechar(quotechar)
        quote_disabled = q == ""
        if quote_disabled:
            quoting = csv.QUOTE_NONE
            effective_quotechar = '"'
        else:
            quoting = csv.QUOTE_MINIMAL
            effective_quotechar = q or '"'
        reader = csv.reader(
            io.StringIO(text),
            delimiter=delimiter,
            quotechar=effective_quotechar,
            escapechar=escapechar,
            quoting=quoting,
        )
        rows: List[Tuple[int, List[Any]]] = []
        line_index = -1
        for row in reader:
            line_index += 1
            if line_index >= max_lines:
                break
            stats["lines_scanned"] += 1
            rows.append((line_index, row))

        expected_cols = None
        header_line_num = None
        header_line_index = None
        for line_index, row in rows:
            if not row or all((c is None or str(c).strip() == "") for c in row):
                stats["blank_lines"] += 1
                continue
            if line_index < skipheaderlines:
                continue
            expected_cols = len(row)
            stats["expected_cols"] = expected_cols
            header_line_index = line_index
            header_line_num = line_index + 1
            stats["header_line_num"] = header_line_num
            try:
                stats["header_line"] = delimiter.join([str(c) for c in row])
            except Exception:
                stats["header_line"] = None
            break

        if expected_cols is None or header_line_index is None:
            stats["expected_cols"] = 0
            return stats

        try:
            footer_cut = normalize_skipheaderlines(skip_footer_lines) if skip_footer_lines else 0
        except Exception:
            footer_cut = 0

        data_rows: List[Tuple[int, List[Any]]] = []
        for line_index, row in rows:
            if line_index <= header_line_index:
                continue
            if not row or all((c is None or str(c).strip() == "") for c in row):
                stats["blank_lines"] += 1
                continue
            data_rows.append((line_index, row))

        if footer_cut and len(data_rows) > footer_cut:
            data_rows = data_rows[:-footer_cut]
        elif footer_cut and len(data_rows) <= footer_cut:
            data_rows = []

        for line_index, row in data_rows:
            stats["data_lines"] += 1
            if len(row) != expected_cols:
                stats["bad_lines"] += 1
                if stats["first_bad_line_num"] is None:
                    stats["first_bad_line_num"] = line_index + 1
                    try:
                        stats["first_bad_line"] = delimiter.join([str(c) for c in row])
                    except Exception:
                        stats["first_bad_line"] = None
                    stats["first_bad_cols"] = len(row)
                try:
                    stats["bad_lines_detail"].append({
                        "line_num": line_index + 1,
                        "cols": len(row),
                        "row": delimiter.join([str(c) for c in row])
                    })
                except Exception:
                    stats["bad_lines_detail"].append({
                        "line_num": line_index + 1,
                        "cols": len(row),
                        "row": None
                    })
    except Exception:
        # Never let debug pre-scan affect the actual pipeline.
        return stats
    return stats


def _read_s3_head_bytes(s3, bucket: str, key: str, max_bytes: int = 4096) -> bytes:
    """Read a small prefix of an S3 object for lightweight format checks."""
    if max_bytes <= 0:
        return b""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}")
        try:
            return obj["Body"].read(max_bytes)
        finally:
            obj["Body"].close()
    except Exception as range_err:
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            try:
                return obj["Body"].read(max_bytes)
            finally:
                obj["Body"].close()
        except Exception as read_err:
            log(
                f"[S3][TYPE_CHECK]  Failed to read head bytes for '{key}': "
                f"{range_err} / {read_err}"
            )
            return b""


def _first_non_whitespace_char(text: str) -> Optional[str]:
    for ch in text:
        if not ch.isspace():
            return ch
    return None


def _validate_delimited_text(
    text: str,
    delimiter: str,
    skipheaderlines: Optional[int],
    quotechar: Optional[str],
    escape_char: Optional[str],
    skip_footer_lines: Optional[int],
    line_separator: Optional[str],
) -> Tuple[bool, Optional[str]]:
    if not text:
        return False, "empty file"
    if line_separator:
        lines = [line for line in text.split(line_separator) if line.strip()]
    else:
        lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False, "no non-empty lines"
    if skipheaderlines:
        try:
            skip = int(skipheaderlines)
        except Exception:
            skip = 0
        if skip > 0:
            if len(lines) <= skip:
                return False, "skipheaderlines removed all rows"
            lines = lines[skip:]
    if skip_footer_lines:
        try:
            skip_footer = int(skip_footer_lines)
        except Exception:
            skip_footer = 0
        if skip_footer > 0:
            if len(lines) <= skip_footer:
                return False, "skip_footer_lines removed all rows"
            lines = lines[:-skip_footer]
    # Require delimiter presence in at least one line to avoid false positives.
    if not any(delimiter in line for line in lines[:5]):
        return False, f"delimiter '{delimiter}' not found"
    try:
        q = (quotechar or "")
        q = q[0] if q else None
        e = (escape_char or "")
        e = e[0] if e else None
        reader = csv.reader(
            lines[:5],
            delimiter=delimiter,
            quotechar=(q if q else None),
            escapechar=(e if e else None),
            quoting=(csv.QUOTE_NONE if not q else csv.QUOTE_MINIMAL),
        )
        counts = [len(row) for row in reader if row is not None]
    except Exception:
        return False, "failed to parse with configured delimiter/quote/escape"
    if not counts:
        return False, "no parsed rows"
    if len(set(counts)) != 1:
        return False, f"inconsistent columns using delimiter '{delimiter}'"
    if counts[0] < 2:
        return False, f"only one column detected with delimiter '{delimiter}'"
    return True, None


def is_file_compatible_with_type(
    s3,
    bucket: str,
    key: str,
    file_type: str,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    escape_char: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Lightweight content-based compatibility check.
    Returns (is_compatible, reason_if_incompatible).
    """
    ft = (file_type or "").strip().lower()
    if not ft:
        return True, None

    head_bytes = _read_s3_head_bytes(s3, bucket, key, max_bytes=4096)
    if not head_bytes:
        return False, "file is empty or head bytes could not be read"

    if ft == "parquet":
        if head_bytes.startswith(b"PAR1"):
            return True, None
        return False, "missing PAR1 magic bytes"
    if ft == "avro":
        if head_bytes.startswith(b"Obj"):
            return True, None
        return False, "missing Avro magic bytes"

    try:
        text = head_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return False, "failed to decode head bytes"

    first_char = _first_non_whitespace_char(text)
    if ft == "json":
        if first_char in ("{", "["):
            return True, None
        return False, "does not look like JSON"
    if ft == "xml":
        if first_char == "<":
            return True, None
        return False, "does not look like XML"
    if ft in ("csv", "tsv"):
        delim = normalize_delimiter(delimiter, ft)
        qchar = normalize_quotechar(quotechar)
        echar = normalize_escapechar(escape_char)
        line_sep = normalize_line_separator(line_separator)
        ok, reason = _validate_delimited_text(
            text,
            delim,
            skipheaderlines,
            qchar,
            echar,
            skip_footer_lines,
            line_sep,
        )
        if ok:
            return True, None
        # Report only the specific mismatch reason, not all options.
        return False, f"does not look like delimited {ft.upper()} content. {reason}"

    return True, None


def normalize_skipheaderlines(val: Any) -> int:
    """
    Normalize skip header/footer lines parameter.
    
    Converts None, empty strings, or non-numeric values to 0.
    Ensures the return value is always an integer for safe comparison.
    
    Args:
        val: Value to normalize (int, str, or None)
        
    Returns:
        int: Normalized integer value (defaults to 0)
    """
    if val in (None, "", False):
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def parse_datetime_from_db(timestamp_value: Any) -> Optional[datetime]:
    """
    Parse datetime from database format (handles multiple formats like Django serializers).
    
    This function handles various timestamp formats that may come from PostgreSQL/Django,
    ensuring consistent datetime parsing for incremental sync logic. It supports:
    - '2025-12-21 08:13:48.139459+00:00' (with timezone seconds)
    - '2025-12-21 08:13:48.139459+00' (without timezone seconds)
    - '2025-12-21T08:13:48.139459+00:00' (ISO format)
    - datetime objects (returns as-is, ensures timezone-aware)
    
    Used for comparing file modification times with last sync timestamps to determine
    if files need to be reprocessed in incremental sync mode.
    
    Args:
        timestamp_value: String or datetime object to parse (from database)
        
    Returns:
        datetime: Timezone-aware datetime object, or None if parsing fails
        
    Example:
        >>> parse_datetime_from_db('2025-12-21 08:13:48.139459+00:00')
        datetime.datetime(2025, 12, 21, 8, 13, 48, 139459, tzinfo=timezone.utc)
        >>> parse_datetime_from_db(None)
        None
    """
    if timestamp_value is None:
        return None
    
    # If already a datetime object, ensure it's timezone-aware
    if isinstance(timestamp_value, datetime):
        if timestamp_value.tzinfo is None:
            return timestamp_value.replace(tzinfo=timezone.utc)
        return timestamp_value
    
    if not isinstance(timestamp_value, str):
        return None
    
    # Try strptime with fallback formats (like Django serializers do)
    try:
        # Format 1: '2025-12-21 08:13:48.139459+00:00' (with timezone seconds)
        return datetime.strptime(timestamp_value, '%Y-%m-%d %H:%M:%S.%f+00:00')
    except ValueError:
        try:
            # Format 2: '2025-12-21 08:13:48.139459+00' (without timezone seconds)
            dt = datetime.strptime(timestamp_value, '%Y-%m-%d %H:%M:%S.%f+00')
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                # Format 3: ISO format with 'T' - normalize timezone offset
                timestamp_str = timestamp_value.replace('Z', '+00:00')
                # Replace space with 'T' if present
                if ' ' in timestamp_str and 'T' not in timestamp_str:
                    timestamp_str = timestamp_str.replace(' ', 'T', 1)
                # Normalize timezone offset: +00 or -00 -> +00:00 or -00:00
                if re.search(r'[+-]\d{2}$', timestamp_str):
                    timestamp_str = re.sub(r'([+-])(\d{2})$', r'\1\2:00', timestamp_str)
                return datetime.fromisoformat(timestamp_str)
            except (ValueError, AttributeError):
                return None


def map_s3_error_to_http_detail(error: Exception, operation: str = "S3 operation") -> Tuple[int, str]:
    """
    Convert low-level S3 exceptions into field-specific, user-actionable HTTP details.
    """
    if isinstance(error, HTTPException):
        detail = error.detail if isinstance(error.detail, str) else str(error.detail)
        return error.status_code, detail or f"{operation} failed"

    if isinstance(error, ClientError):
        err = error.response.get("Error", {}) if getattr(error, "response", None) else {}
        code = err.get("Code", "Unknown")
        message = (err.get("Message") or "").strip()
        status_map = {
            # AWS credential/config validation errors should not use HTTP 401,
            # because 401 is reserved for application auth/session failures.
            "InvalidAccessKeyId": 422,
            "SignatureDoesNotMatch": 422,
            "InvalidClientTokenId": 422,
            "AuthFailure": 422,
            "UnrecognizedClientException": 422,
            "ExpiredToken": 422,
            "InvalidToken": 422,
            "RequestExpired": 422,
            "MissingAuthenticationToken": 422,
            "AccessDenied": 403,
            "Forbidden": 403,
            "NoSuchBucket": 404,
            "NotFound": 404,
            "PermanentRedirect": 422,
            "AuthorizationHeaderMalformed": 422,
        }
        detail_map = {
            "InvalidAccessKeyId": "Invalid `aws_access_key_id`. Please verify AWS Access Key ID.",
            "SignatureDoesNotMatch": "Invalid `aws_secret_access_key` (signature mismatch). Please verify AWS Secret Access Key.",
            "InvalidClientTokenId": "Invalid AWS credentials. Verify both `aws_access_key_id` and `aws_secret_access_key`.",
            "AuthFailure": "AWS authentication failed. Verify `aws_access_key_id` and `aws_secret_access_key`.",
            "UnrecognizedClientException": "AWS authentication failed. Verify `aws_access_key_id` and `aws_secret_access_key`.",
            "ExpiredToken": "AWS credentials/session token expired. Refresh credentials and try again.",
            "InvalidToken": "Invalid AWS security token. Verify credentials/session token.",
            "RequestExpired": "AWS request expired. Verify server clock and retry.",
            "MissingAuthenticationToken": "Missing AWS authentication token. Verify credentials configuration.",
            "AccessDenied": "Access denied for this bucket. Verify IAM permissions for the provided credentials and bucket policy.",
            "Forbidden": "Access forbidden for this bucket. Verify IAM permissions and bucket policy.",
            "NoSuchBucket": "Invalid `s3_bucket`. Bucket does not exist.",
            "NotFound": "Invalid `s3_bucket` or object path. Resource not found.",
            "PermanentRedirect": "Invalid `aws_region` for this bucket. Use the bucket's actual region.",
            "AuthorizationHeaderMalformed": "Invalid `aws_region` for this bucket. Authorization header region does not match bucket region.",
        }
        detail = detail_map.get(code) or f"{operation} failed: {code}"
        if message and code not in detail_map:
            detail = f"{detail}. {message}"
        if code not in status_map:
            msg_low = message.lower()
            if any(token in msg_low for token in ["credential", "signature", "token", "authentication"]):
                return 422, "Invalid AWS credentials. Verify `aws_access_key_id` and `aws_secret_access_key`."
        return status_map.get(code, 500), detail

    if isinstance(error, EndpointConnectionError):
        return 422, "Could not reach AWS endpoint. Verify `aws_region` and network connectivity."

    if isinstance(error, ParamValidationError):
        message = str(error)
        if "Invalid bucket name" in message or "bucket name must match the regex" in message.lower():
            return 422, "Enter valid bucket name."
        return 422, f"{operation} failed: invalid request parameters."

    text = str(error).strip()
    text_l = text.lower()
    if "invalid endpoint" in text_l or "could not connect to the endpoint url" in text_l:
        return 422, "Invalid `aws_region` or endpoint for S3. Verify region format and connectivity."
    if "credentials" in text_l:
        return 422, "Invalid AWS credentials. Verify `aws_access_key_id` and `aws_secret_access_key`."
    if "invalid bucket name" in text_l or "bucket name must match the regex" in text_l:
        return 422, "Enter valid bucket name."
    if "prefix" in text_l or "folder" in text_l:
        return 404, "Invalid `s3_base_prefix` (folder path). Verify folder path in the bucket."
    return 500, f"{operation} failed: {text or type(error).__name__}"


def normalize_s3_folder_prefix(prefix: str) -> str:
    """
    Normalize S3 folder prefix for strict folder matching.
    Example: "CSV" -> "CSV/", "/CSV//" -> "CSV/".
    """
    normalized = normalized_prefix(prefix or "")
    if normalized and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def _as_bool(value: Any, default: bool = False) -> bool:
    """Parse bool-like values from API payloads consistently."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _list_s3_keys_by_scope(s3, s3_bucket: str, s3_folder_prefix: str, include_subfolders: bool) -> List[str]:
    """List S3 keys using recursive or immediate-folder strategy."""
    if include_subfolders:
        keys = list_files_recursive(s3, s3_bucket, s3_folder_prefix)
    else:
        keys = list_files_non_recursive(s3, s3_bucket, s3_folder_prefix)
    # Filter out folder markers (keys ending with '/')
    return [k for k in keys if str(k).strip() and not str(k).strip().endswith("/")]


def pandas_to_sf_types(df: pd.DataFrame) -> Dict[str, str]:
    """
    Map pandas DataFrame column dtypes to Snowflake SQL types.
    
    This function provides a basic type mapping for schema inference.
    For more intelligent type detection, use detect_column_type_from_values()
    which analyzes actual data values for better type accuracy.
    
    Args:
        df: pandas DataFrame with columns to map
        
    Returns:
        dict: Mapping of column names to Snowflake types
        
    Example:
        >>> df = pd.DataFrame({'id': [1, 2], 'name': ['a', 'b']})
        >>> pandas_to_sf_types(df)
        {'id': 'NUMBER', 'name': 'VARCHAR(16777216)'}
    """
    type_map = {
        "int64": "NUMBER",
        "float64": "FLOAT",
        "bool": "BOOLEAN",
        "datetime64[ns]": "TIMESTAMP_NTZ",
        "object": "VARCHAR(16777216)",
    }
    return {col: type_map.get(str(df[col].dtype), "VARCHAR(16777216)") for col in df.columns}


def build_snowflake_file_format(
    delimiter: str = ",",
    quotechar: str = '"',
    skiprows: int = 0,
    file_type: str = "csv",
) -> str:
    """
    Build Snowflake file format SQL clause for COPY INTO statements.
    
    Snowflake's COPY INTO command requires a file format definition that specifies
    how to parse the source files. This function generates the appropriate format
    clause based on file type and parsing parameters.
    
    For CSV/TSV files, includes delimiter, quote character, and header skip settings.
    For other file types (JSON, Parquet, Avro), uses type-specific defaults.
    
    Args:
        delimiter: Field delimiter character (default: ",")
        quotechar: Quote character for fields (default: '"')
        skiprows: Number of header rows to skip (default: 0)
        file_type: File type ("csv", "tsv", "json", "parquet", "avro")
        
    Returns:
        str: SQL file format clause wrapped in parentheses
        
    Example:
        >>> build_snowflake_file_format(delimiter=",", quotechar='"', skiprows=1, file_type="csv")
        "(\\n        TYPE = 'CSV'\\n        FIELD_DELIMITER = ','\\n        ...)"
    """
    ft = file_type.lower() if file_type else "csv"
    delim = normalize_delimiter(delimiter, ft)
    qchar = normalize_quotechar(quotechar)
    delim_sql = delim.replace("'", "''")
    qchar_sql = qchar.replace("'", "''") if qchar else ""

    if ft in ("csv", "tsv"):
        # Build the format string conditionally
        format_parts = [
            "TYPE = 'CSV'",
            f"FIELD_DELIMITER = '{delim_sql}'",
        ]
        
        # Only include FIELD_OPTIONALLY_ENCLOSED_BY if quotechar is not empty
        if qchar:
            format_parts.append(f"FIELD_OPTIONALLY_ENCLOSED_BY = '{qchar_sql}'")
        
        format_parts.append(f"SKIP_HEADER = {skiprows}")
        # pandas writes literal backslashes; Snowflake must not consume them.
        format_parts.append("ESCAPE_UNENCLOSED_FIELD = NONE")
        
        return f"""({chr(10)}        {(chr(10) + '        ').join(format_parts)}{chr(10)}        )"""
    return f"(TYPE = '{ft.upper()}')"


def adaptive_varchar_size(df: pd.DataFrame, col: str) -> str:
    """
    Calculate adaptive VARCHAR size for Snowflake column based on actual data length.
    
    This function analyzes the maximum string length in a column to determine
    an appropriate VARCHAR size, optimizing storage while avoiding truncation.
    Uses tiered sizing: 256, 512, 1024, 4096, or max (16777216).
    
    Args:
        df: pandas DataFrame containing the column
        col: Column name to analyze
        
    Returns:
        str: Snowflake VARCHAR type with appropriate size
        
    Example:
        >>> df = pd.DataFrame({'name': ['short', 'very long string here']})
        >>> adaptive_varchar_size(df, 'name')
        'VARCHAR(256)'
    """
    non_null = df[col].dropna()
    if len(non_null) == 0:
        return "VARCHAR(512)"  # Default for empty columns
    max_len = non_null.astype(str).str.len().max()
    # Tiered sizing based on maximum length found
    if max_len <= 256:
        return "VARCHAR(256)"
    elif max_len <= 512:
        return "VARCHAR(512)"
    elif max_len <= 1024:
        return "VARCHAR(1024)"
    elif max_len <= 4096:
        return "VARCHAR(4096)"
    else:
        return "VARCHAR(16777216)"  # Snowflake max VARCHAR size


def parse_varchar_length_from_type(type_spec: str) -> Optional[int]:
    """Extract VARCHAR length from a Snowflake type string (e.g. 'VARCHAR(512) NOT NULL')."""
    base = str(type_spec or "").strip().upper().split()[0]
    match = re.match(r"^VARCHAR\((\d+)\)$", base)
    return int(match.group(1)) if match else None


def compute_varchar_widen_target(
    df: pd.DataFrame,
    col_u: str,
    type_spec: str,
    *,
    margin: int = VARCHAR_WIDEN_MARGIN,
    min_length: Optional[int] = None,
) -> Optional[int]:
    """
    Compute target VARCHAR width from inferred type and current chunk values.
    Mirrors Dynamics behavior: widen with safety margin, never shrink.
    """
    parsed = parse_varchar_length_from_type(type_spec)
    chunk_target = parsed
    df_col = None
    for col in df.columns:
        if sanitize_column_name(col).upper() == str(col_u or "").upper():
            df_col = col
            break
    if df_col is not None:
        try:
            series = df[df_col].fillna("").astype(str)
            if len(series) > 0:
                max_len = int(series.map(len).max())
                if max_len > 0:
                    chunk_target = min(max_len + margin, SNOWFLAKE_MAX_VARCHAR_LENGTH)
        except Exception:
            pass
    target = chunk_target
    if parsed is not None and chunk_target is not None:
        target = max(parsed, chunk_target)
    elif parsed is not None:
        target = parsed
    if min_length is not None and target is not None:
        target = max(target, min_length)
    return target


def detect_column_type_from_values(df: pd.DataFrame, col: str) -> str:
    """
    Intelligently detect Snowflake column type from DataFrame values.
    
    This function analyzes actual data values (not just pandas dtypes) to determine
    the most appropriate Snowflake type. It handles:
    - Complex types (dict/list)  VARIANT
    - Numeric types  NUMBER or FLOAT
    - Boolean  BOOLEAN
    - Datetime  TIMESTAMP_NTZ
    - Strings  Adaptive VARCHAR sizing
    
    This provides more accurate type inference than pandas dtypes alone, especially
    for string columns where we can optimize VARCHAR size.
    
    Args:
        df: pandas DataFrame containing the column
        col: Column name to analyze
        
    Returns:
        str: Snowflake SQL type (e.g., "NUMBER(38,0)", "VARCHAR(256)", "VARIANT")
        
    Example:
        >>> df = pd.DataFrame({'id': [1, 2], 'data': [{'key': 'value'}, {'key2': 'value2'}]})
        >>> detect_column_type_from_values(df, 'id')
        'NUMBER(38,0)'
        >>> detect_column_type_from_values(df, 'data')
        'VARIANT'
    """
    non_null = df[col].dropna()
    if len(non_null) == 0:
        return "VARCHAR(512)"  # Default for empty columns

    # Check for complex types (dict/list) - use VARIANT for JSON-like data
    sample = non_null.iloc[0]
    if isinstance(sample, (dict, list, np.ndarray)):
        return "VARIANT"

    # Check pandas dtype for basic type detection
    dtype = str(df[col].dtype)
    if dtype.startswith("int"):
        return "NUMBER(38,0)"  # Snowflake default integer precision
    elif dtype.startswith("float"):
        return "FLOAT"
    elif dtype.startswith("bool"):
        return "BOOLEAN"
    elif dtype.startswith("datetime"):
        return "TIMESTAMP_NTZ"  # No timezone (assumes UTC)
    elif dtype == "object":
        # Heuristic for object columns:
        # If all non-null values are numeric-like strings, treat as numeric
        # to avoid false schema mismatches across files where one parser pass
        # inferred int and another inferred object due quoting/whitespace.
        try:
            cleaned = non_null.astype(str).str.strip()
            if len(cleaned) > 0:
                numeric = pd.to_numeric(cleaned, errors="coerce")
                if numeric.notna().all():
                    # If every parsed value is integral, use NUMBER; else FLOAT.
                    if (numeric % 1 == 0).all():
                        return "NUMBER(38,0)"
                    return "FLOAT"
        except Exception:
            pass
        # Fallback: string type with adaptive sizing.
        return adaptive_varchar_size(df, col)
    else:
        return "VARCHAR(512)"  # Default fallback


def compute_pk_candidates(df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    """
    Compute primary key candidate scores for each column.
    
    This function analyzes each column to determine its suitability as a primary key
    by calculating uniqueness ratio (unique values / total rows) and null ratio
    (null values / total rows). Used by UI to suggest primary key columns for
    custom key load method.
    
    Ideal PK candidates have:
    - High uniqueness ratio (close to 1.0 = all values unique)
    - Low null ratio (close to 0.0 = no nulls)
    
    Metadata columns (starting with "_") are excluded as they are system-generated.
    
    Args:
        df: pandas DataFrame to analyze
        
    Returns:
        dict: Mapping of column names (uppercase) to candidate scores:
            {
                "COLUMN_NAME": {
                    "unique_ratio": float,  # 0.0 to 1.0
                    "null_ratio": float     # 0.0 to 1.0
                }
            }
        
    Example:
        >>> df = pd.DataFrame({'id': [1, 2, 3], 'name': ['a', 'b', None]})
        >>> compute_pk_candidates(df)
        {'ID': {'unique_ratio': 1.0, 'null_ratio': 0.0}, 'NAME': {'unique_ratio': 0.67, 'null_ratio': 0.33}}
    """
    candidates = {}
    n = len(df)
    if n == 0:
        return candidates
    for col in df.columns:
        # Exclude metadata columns (system-generated, not user data)
        if col.startswith("_"):
            continue
        # Calculate uniqueness: ratio of unique values to total rows
        unique_ratio = df[col].nunique() / n
        # Calculate null ratio: percentage of null values
        null_ratio = df[col].isna().mean()
        candidates[col.upper()] = {
            "unique_ratio": unique_ratio,
            "null_ratio": null_ratio,
        }
    return candidates

def _derive_host_from_account(account: str) -> str:
    """
    Derive Snowflake hostname from account identifier.
    
    Snowflake account identifiers can be in different formats:
    - Simple: "myaccount"  "myaccount.snowflakecomputing.com"
    - With region: "myaccount.us-east-1"  "myaccount.us-east-1.snowflakecomputing.com"
    
    This function constructs the full hostname for connection.
    
    Args:
        account: Snowflake account identifier (with or without region)
        
    Returns:
        str: Full Snowflake hostname
        
    Example:
        >>> _derive_host_from_account("myaccount")
        'myaccount.snowflakecomputing.com'
        >>> _derive_host_from_account("myaccount.us-east-1")
        'myaccount.us-east-1.snowflakecomputing.com'
    """
    # Snowflake hostname format: <account>.<region>.snowflakecomputing.com
    # If account already contains region (has dot), use as-is
    # Otherwise, account is just the account name
    if "." in account:
        return f"{account}.snowflakecomputing.com"
    return f"{account}.snowflakecomputing.com"

def _preflight_network_test(host: str, port: int = 443) -> bool:
    """
    Test network connectivity to Snowflake host before attempting connection.
    
    This preflight check helps identify network/firewall issues early before
    attempting the full Snowflake authentication, providing faster error feedback.
    Uses a simple TCP socket connection test with 5-second timeout.
    
    Args:
        host: Snowflake hostname (e.g., "myaccount.snowflakecomputing.com")
        port: Port to test (default: 443 for HTTPS)
        
    Returns:
        bool: True if connection successful, False otherwise
        
    Note:
        This is a non-blocking check. Connection failures here don't prevent
        the actual Snowflake connection attempt, which may succeed if this fails
        due to firewall rules allowing HTTPS but not raw TCP.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)  # 5-second timeout for network test
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0  # 0 = success, non-zero = failure
    except Exception:
        return False  # Any exception = connection failed

def sf_connect(
    sf_account: str = None,
    sf_user: str = None,
    sf_password: str = None,
    sf_warehouse: str = None,
    sf_database: str = None,
    sf_schema: str = None,
    sf_role: Optional[str] = None,
    private_key: str = None,
    passphrase: str = None,
    authentication_type: str = "password"
):
    """
    Create Snowflake connection with authentication and error handling.
    
    This function establishes a connection to Snowflake using either password
    or private key authentication. It handles:
    - Network preflight checks (non-blocking)
    - Private key normalization and decryption
    - Connection parameter validation
    - Standard error handling compatible with all services
    
    Supports two authentication methods:
    1. Password authentication: Provide sf_password
    2. Key-pair authentication: Provide private_key (and optional passphrase)
    
    Args:
        sf_account: Snowflake account identifier (e.g., "myaccount" or "myaccount.us-east-1")
        sf_user: Snowflake username
        sf_password: Snowflake password (required if private_key not provided)
        sf_warehouse: Snowflake warehouse name
        sf_database: Snowflake database name
        sf_schema: Snowflake schema name
        sf_role: Snowflake role (default: "ACCOUNTADMIN")
        private_key: PEM-formatted private key string (required if password not provided)
        passphrase: Optional passphrase for encrypted private key
        
    Returns:
        snowflake.connector.SnowflakeConnection: Active Snowflake connection object
        
    Raises:
        ValueError: If authentication fails or required parameters are missing
        
    Example:
        >>> conn = sf_connect(
        ...     sf_account="myaccount",
        ...     sf_user="user",
        ...     sf_password="password",
        ...     sf_warehouse="WH",
        ...     sf_database="DB",
        ...     sf_schema="SCHEMA"
        ... )
    """
    from datasyncher_connections.snowflake.connector import create_snowflake_connector

    host = _derive_host_from_account(sf_account)
    if not _preflight_network_test(host):
        log(f" Network preflight failed for {host}, continuing anyway...")

    auth_type = authentication_type.lower() if authentication_type else "password"
    config = {
        "account": sf_account,
        "user": sf_user,
        "password": sf_password,
        "warehouse": sf_warehouse,
        "database": sf_database,
        "schema": sf_schema,
        "role": sf_role or os.getenv("SNOWFLAKE_DEFAULT_ROLE", "ACCOUNTADMIN"),
        "private_key": private_key,
        "passphrase": passphrase,
        "authentication_type": auth_type,
    }

    if auth_type == "key_pair" and private_key:
        try:
            normalized_private_key = normalize_pem_key(private_key)
            log(f"[SNOWFLAKE] Normalized private key length: {len(normalized_private_key)}")
            log("[SNOWFLAKE] Private key loaded successfully using standard approach")
        except Exception as e:
            log(f"[SNOWFLAKE] ERROR loading private key: {str(e)}")
            raise ValueError(f"Failed to process private key: {str(e)}") from e

    try:
        return create_snowflake_connector(config, preflight=False)
    except ValueError as e:
        if auth_type == "key_pair":
            log(f"[SNOWFLAKE] ERROR loading private key: {str(e)}")
            raise ValueError(f"Failed to process private key: {str(e)}") from e
        raise

# =============================================================================
# DESTINATION CONFIG HELPERS (Snowflake existence checks)
# =============================================================================

def _fetch_snowflake_tables_from_config(dest_config: Optional[Dict[str, Any]]) -> Optional[set]:
    """
    Return a set of TABLE_NAMEs present in the Snowflake schema using a provided
    destination config (no Django decrypt). Returns None if lookup fails.
    """
    if not dest_config or not isinstance(dest_config, dict):
        return None

    sf_account = dest_config.get("account")
    sf_user = dest_config.get("user") or dest_config.get("username")
    sf_password = dest_config.get("password")
    sf_warehouse = normalize_and_log_snowflake_identifier(dest_config.get("warehouse"), "warehouse")
    sf_database = normalize_and_log_snowflake_identifier(dest_config.get("database"), "database")
    sf_schema = normalize_and_log_snowflake_identifier(dest_config.get("schema"), "schema")
    sf_role = normalize_and_log_snowflake_identifier(dest_config.get("role"), "role") or "ACCOUNTADMIN"
    authentication_type = dest_config.get("authentication_type", "password")
    private_key = dest_config.get("private_key")
    passphrase = dest_config.get("passphrase")

    if not sf_account or not sf_user or not sf_database or not sf_schema:
        log("[S3][SNOWFLAKE]  Missing Snowflake fields; cannot verify table existence.")
        return None

    conn = None
    try:
        conn = sf_connect(
            sf_account=sf_account,
            sf_user=sf_user,
            sf_password=sf_password,
            sf_warehouse=sf_warehouse,
            sf_database=sf_database,
            sf_schema=sf_schema,
            sf_role=sf_role,
            private_key=private_key,
            passphrase=passphrase,
            authentication_type=authentication_type,
        )
        with conn.cursor() as cur:
            sql = f"""
                SELECT TABLE_NAME
                FROM {Q(sf_database)}.INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
            """
            cur.execute(sql, (sf_schema,))
            rows = cur.fetchall() or []
            tables = {str(r[0]).upper() for r in rows if r and r[0]}
            if tables:
                sample = ", ".join(sorted(tables)[:10])
                log(
                    f"[S3][LIST_FILES][DEBUG] Snowflake tables (sample {min(10, len(tables))}/{len(tables)}): "
                    f"{sample}"
                )
            else:
                log("[S3][LIST_FILES][DEBUG] Snowflake tables list is empty")
            return tables
    except Exception as e:
        log(f"[S3][SNOWFLAKE]  Failed to fetch table list: {e}")
        return None
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _snowflake_table_exists(conn, sf_database: str, sf_schema: str, table_name: str) -> bool:
    """
    Check if a table exists in Snowflake using an existing connection.
    """
    if not conn or not sf_database or not sf_schema or not table_name:
        log(
            f"[S3][SNOWFLAKE]  Table existence check skipped "
            f"(conn/schema/table missing). Assuming exists for '{table_name}'."
        )
        return True  # Fail open to avoid blocking migration on missing metadata
    try:
        normalized_table = sanitize_identifier(table_name)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT 1
                FROM {Q(sf_database)}.INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = %s
                LIMIT 1
                """,
                (sf_schema, normalized_table),
            )
            exists = cur.fetchone() is not None
            log(
                f"[S3][SNOWFLAKE]  Table existence check: {sf_database}.{sf_schema}.{normalized_table} "
                f"=> {'FOUND' if exists else 'NOT FOUND'}"
            )
            return exists
    except Exception as e:
        log(f"[S3][SNOWFLAKE]  Table existence check failed for '{table_name}': {e}")
        return True



# =============================================================================
# STORAGE INTEGRATION HELPERS
# =============================================================================
# Note: Storage integration functions (ensure_gcs_storage_integration,
# get_gcp_service_account, ensure_bucket_iam_for_integration,
# assert_allowed_prefix_matches) are imported from utils.gcp_utils
# to maintain consistency across all services.

# =============================================================================
# FILE OPERATIONS
# =============================================================================
# Note: File operation functions (list_files_recursive, filter_by_type,
# filter_by_pattern) are imported from utils.s3_utils to maintain consistency
# across all services and avoid code duplication.

# =============================================================================
# XML PARSING HELPERS
# =============================================================================

def parse_xml_record_packed(record: ET.Element, row_index: int) -> Dict[str, Any]:
    """
    Parse a single XML record element into a dictionary (packed mode).
    
    XML files are parsed in "packed" mode, where each XML element is stored
    as a string in the _DATA column. This preserves the full XML structure
    and allows users to query it using Snowflake's XML functions.
    
    Args:
        record: XML element to parse (from ElementTree)
        row_index: Zero-based row index for this record
        
    Returns:
        dict: Dictionary with _DATA (XML string) and _ROW (row index)
        
    Example:
        >>> elem = ET.fromstring('<user><name>John</name></user>')
        >>> parse_xml_record_packed(elem, 0)
        {'_DATA': '<user><name>John</name></user>', '_ROW': 0}
    """
    xml_string = ET.tostring(record, encoding='unicode')
    return {
        "_DATA": xml_string,
        "_ROW": row_index
    }

def parse_xml_packed(root: ET.Element) -> Optional[pd.DataFrame]:
    """
    Parse XML root element into DataFrame (packed mode - only supported mode).
    
    This function processes XML files by converting each child element (or the root
    itself if no children) into a row with the XML stored as a string in _DATA column.
    This allows preserving full XML structure for querying in Snowflake.
    
    Args:
        root: Root XML element from ElementTree.parse() or ET.fromstring()
        
    Returns:
        pd.DataFrame: DataFrame with _DATA and _ROW columns, or None if empty
        
    Example:
        >>> root = ET.fromstring('<root><item>1</item><item>2</item></root>')
        >>> df = parse_xml_packed(root)
        >>> len(df)
        2
        >>> df.columns.tolist()
        ['_DATA', '_ROW']
    """
    rows = []
    records = list(root)  # Get all child elements
    # If no children, treat root as single record
    if not records:
        records = [root]
    log(f"  [XML] Packed mode: found {len(records)} records")
    for idx, record in enumerate(records):
        rec = parse_xml_record_packed(record, idx)
        rows.append(rec)
    return pd.DataFrame(rows) if rows else None


def _strip_xml_namespace(tag: str) -> str:
    """Remove namespace URI/prefix from XML tag names."""
    if not tag:
        return ""
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    if ":" in tag:
        tag = tag.split(":", 1)[1]
    return tag


def _normalize_xml_key_part(name: str) -> str:
    """Normalize XML node/attribute names into safe key fragments."""
    value = _strip_xml_namespace(str(name or "")).strip()
    if not value:
        return "FIELD"
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        return "FIELD"
    if not value[0].isalpha() and value[0] != "_":
        value = f"_{value}"
    return value.upper()


def _xml_record_base_path(record: ET.Element) -> str:
    return _normalize_xml_key_part(record.tag)


def _drop_root_prefix_from_xml_key(key: str, root_prefix: str) -> str:
    """
    Drop top-level record prefix from unpacked XML keys.

    Example:
    - EMPLOYEE__ID -> ID
    - EMPLOYEE__ADDRESS__CITY -> ADDRESS__CITY
    """
    if not key:
        return key
    prefix = f"{root_prefix}__"
    if root_prefix and key.startswith(prefix):
        return key[len(prefix):]
    return key


def _append_xml_value(accumulator: Dict[str, List[Any]], key: str, value: Any) -> None:
    if value is None:
        return
    accumulator.setdefault(key, []).append(value)


def _flatten_xml_element_unpacked(
    element: ET.Element,
    path: str,
    accumulator: Dict[str, List[Any]],
) -> None:
    """Recursively flatten XML element into path-based column/value lists."""
    # Attributes become dedicated columns.
    for attr_name, attr_val in (element.attrib or {}).items():
        attr_key = f"{path}__ATTR__{_normalize_xml_key_part(attr_name)}"
        attr_text = str(attr_val).strip() if attr_val is not None else ""
        if attr_text:
            _append_xml_value(accumulator, attr_key, attr_text)

    children = list(element)
    text_val = (element.text or "").strip()

    # Leaf node: direct scalar value.
    if not children:
        if text_val:
            _append_xml_value(accumulator, path, text_val)
        return

    # Mixed content: preserve parent text separately.
    if text_val:
        _append_xml_value(accumulator, f"{path}__TEXT", text_val)

    for child in children:
        child_part = _normalize_xml_key_part(child.tag)
        child_path = f"{path}__{child_part}"
        _flatten_xml_element_unpacked(child, child_path, accumulator)


def _finalize_xml_record_unpacked(accumulator: Dict[str, List[Any]], root_prefix: str) -> Dict[str, Any]:
    """
    Convert collected XML values to a row dict.
    - single value -> scalar
    - repeated values -> JSON array string (stable for Snowflake VARCHAR/VARIANT inference)
    """
    row: Dict[str, Any] = {}
    for key, values in accumulator.items():
        output_key = _drop_root_prefix_from_xml_key(key, root_prefix)
        cleaned = [v for v in values if v is not None and str(v).strip() != ""]
        if not cleaned:
            continue
        if len(cleaned) == 1:
            row[output_key] = cleaned[0]
        else:
            row[output_key] = json.dumps(cleaned, ensure_ascii=False)
    return row


def parse_xml_record_unpacked(record: ET.Element, row_index: int) -> Dict[str, Any]:
    """Parse one XML record into flattened, production-friendly unpacked columns."""
    accumulator: Dict[str, List[Any]] = {}
    base_path = _xml_record_base_path(record)
    _flatten_xml_element_unpacked(record, base_path, accumulator)
    row = _finalize_xml_record_unpacked(accumulator, base_path)
    row["_ROW"] = row_index
    return row


def _is_wordml_root(root: ET.Element) -> bool:
    """Detect Word 2003 WordML root element."""
    if root is None or not getattr(root, "tag", None):
        return False
    tag = str(root.tag)
    local = tag.split("}", 1)[-1] if "}" in tag else tag
    if local == "wordDocument":
        return True
    if "schemas.microsoft.com/office/word/2003/wordml" in tag:
        return True
    return False


def _extract_embedded_xml_from_wordml(root: ET.Element) -> Optional[bytes]:
    """
    Extract embedded XML that is stored as escaped text inside <w:t> nodes.
    Returns UTF-8 bytes of reconstructed XML or None if not found/invalid.
    """
    if root is None:
        return None

    parts: list[str] = []
    for elem in root.iter():
        tag = str(elem.tag or "")
        local = tag.split("}", 1)[-1] if "}" in tag else tag
        if local == "t" and elem.text:
            parts.append(elem.text)

    if not parts:
        return None

    combined = "".join(parts)
    unescaped = html.unescape(combined).strip()
    if not unescaped:
        return None

    # Keep only the XML payload if text contains extra surrounding content.
    if "<" in unescaped and ">" in unescaped:
        start = unescaped.find("<")
        end = unescaped.rfind(">")
        if start >= 0 and end > start:
            unescaped = unescaped[start:end + 1].strip()

    if not unescaped.startswith("<"):
        return None

    return unescaped.encode("utf-8")


def _parse_xml_root(
    content: bytes,
    return_content: bool = False,
) -> Optional[ET.Element] | tuple[Optional[ET.Element], Optional[bytes]]:
    """Validate XML content and return parsed root when valid.

    If WordML is detected, attempt to extract embedded XML from <w:t> nodes.
    When return_content=True, also return the (possibly normalized) XML bytes
    that match the returned root for iterparse/streaming use.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        log(f"   XML parse error: {e}")
        return (None, None) if return_content else None
    except Exception as e:
        log(f"   XML error: {type(e).__name__}: {e}")
        return (None, None) if return_content else None

    normalized_content = content
    if _is_wordml_root(root):
        embedded = _extract_embedded_xml_from_wordml(root)
        if embedded:
            try:
                embedded_root = ET.fromstring(embedded)
                log("  [XML] Detected WordML; extracted embedded XML from <w:t> nodes.")
                root = embedded_root
                normalized_content = embedded
            except Exception as e:
                log(f"   WordML embedded XML parse error: {type(e).__name__}: {e}")

    if return_content:
        return root, normalized_content
    return root


def _is_valid_avro_container(content: bytes) -> bool:
    """
    Validate Avro Object Container File magic bytes.
    Avro container files must start with b'Obj\\x01'.
    """
    return isinstance(content, (bytes, bytearray)) and len(content) >= 4 and content[:4] == b"Obj\x01"


def _is_valid_parquet_file(content: bytes) -> bool:
    """
    Validate Parquet magic bytes.
    A parquet file must start and end with b'PAR1'.
    """
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


def _build_schema_validation_error_message(file_key: str, file_type: str, details: Optional[str] = None) -> str:
    """Build standardized schema-validation error text for UI/log table."""
    clean_details = str(details).strip().lstrip(",:; ").strip() if details else None
    if (file_type or "").lower() == "xml":
        base = f"File '{file_key}' is not a valid XML. Please check and upload a valid XML file."
        if clean_details:
            return f"Update schema failed: {base} Details: {clean_details}"
        return f"Update schema failed: {base}"
    if clean_details:
        return f"Update schema failed: File '{file_key}' could not be parsed. Details: {clean_details}"
    return f"Update schema failed: File '{file_key}' could not be parsed."


def _mark_listing_migration_record_failed(job_name: Optional[str]) -> None:
    """Ensure listing/update-schema jobs do not stay in_progress after a terminal failure."""
    if not job_name:
        return
    conn = None
    cursor = None
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE migration_record
            SET status = %s, updated_at = NOW()
            WHERE job_name = %s
              AND status = %s
            """,
            ("failed", job_name, "in_progress"),
        )
        conn.commit()
    except Exception as mark_err:
        log(f"[LIST_SCHEMA]  Failed to mark migration_record failed for job={job_name}: {mark_err}")
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _log_schema_validation_failure_to_table(
    *,
    cmp_id: Optional[int],
    connection_id: Optional[int],
    user_id: Optional[int],
    job_name: Optional[str],
    table_or_file: str,
    message: str,
) -> None:
    """Best-effort write of schema validation failures into the log table."""
    if cmp_id is None or connection_id is None or user_id is None:
        return
    try:
        # Prefer updating the existing job-level "Update schema in progress..." log
        # so UI shows a single entry transitioning to failed.
        if job_name:
            try:
                from utils.error_utils import update_migration_status_log
                updated = update_migration_status_log(
                    job_name=job_name,
                    message=message,
                    status="F",
                )
                _mark_listing_migration_record_failed(job_name)
                if updated:
                    return
            except Exception as update_err:
                log(f"[LIST_SCHEMA]  Failed to update existing update-schema log: {update_err}")

        insert_tool_log(
            cmp_id,
            connection_id,
            user_id,
            table_or_file or "UPDATE_SCHEMA",
            "F",
            message,
            None,
            job_name=job_name,
        )
    except Exception as log_err:
        log(f"[LIST_SCHEMA]  Failed to write schema validation error to log table: {log_err}")


def _detect_xml_records_for_unpacked(root: ET.Element) -> List[ET.Element]:
    """Best-effort record detection for common XML layouts."""
    records = list(root)
    if not records:
        return [root]

    # Case 1: root directly contains repeating record nodes.
    root_child_names = [_normalize_xml_key_part(r.tag) for r in records]
    if len(set(root_child_names)) == 1:
        return records

    # Case 2: root wraps one container whose children are record nodes.
    if len(records) == 1:
        nested = list(records[0])
        if nested:
            nested_names = [_normalize_xml_key_part(r.tag) for r in nested]
            if len(set(nested_names)) == 1:
                return nested

    # Fallback: treat first-level children as records.
    return records


def parse_xml_unpacked(root: ET.Element) -> Optional[pd.DataFrame]:
    """Parse XML into flattened rows/columns for unpacked mode."""
    rows = []
    records = _detect_xml_records_for_unpacked(root)
    log(f"  [XML] Unpacked mode: found {len(records)} record nodes")
    for idx, record in enumerate(records):
        rows.append(parse_xml_record_unpacked(record, idx))
    if rows:
        _validate_json_record_keys(rows, None)
    return pd.DataFrame(rows) if rows else None


class XMLNoBusinessColumnsError(RuntimeError):
    """Raised when unpacked XML yields no business columns."""


def _xml_unpacked_has_business_data(df: pd.DataFrame) -> bool:
    """
    Return True if unpacked XML has at least one non-empty business value.
    Prevents writing metadata-only rows when parsing yielded empty fields.
    """
    if df is None or df.empty or df.columns is None or len(df.columns) == 0:
        return False
    work_df = df.copy()
    text_cols = work_df.select_dtypes(include=["object", "string"]).columns
    if len(text_cols) > 0:
        work_df[text_cols] = work_df[text_cols].replace(r"^\s*$", pd.NA, regex=True)
    return not work_df.isna().all(axis=None)


def _unwrap_single_key_json_container(doc: Any) -> Any:
    """
    Unwrap JSON payloads shaped like {"SomeKey": [...]} or {"SomeKey": {...}}
    in unpacked mode so business columns become first-class DataFrame columns.
    """
    if not isinstance(doc, dict) or len(doc) != 1:
        return doc
    try:
        only_value = next(iter(doc.values()))
    except Exception:
        return doc
    if isinstance(only_value, (list, dict)):
        return only_value
    return doc


def _iter_s3_json_values_from_body(body, *, packed: bool):
    """Yield JSON values from an S3 Body stream without loading the full payload."""
    decoder = json.JSONDecoder()
    text_decoder = codecs.getincrementaldecoder("utf-8-sig")()
    buffer = ""
    mode = None
    eof = False

    while True:
        if not eof:
            chunk = body.read(65536)
            if not chunk:
                eof = True
                buffer += text_decoder.decode(b"", final=True)
            else:
                if isinstance(chunk, bytes):
                    buffer += text_decoder.decode(chunk)
                else:
                    buffer += str(chunk)

        progressed = False
        while True:
            buffer = buffer.lstrip()
            if not buffer:
                break

            if mode is None:
                first = buffer[0]
                if first == "[":
                    mode = "array"
                    buffer = buffer[1:]
                    progressed = True
                    continue
                if first == "{":
                    mode = "object"
                else:
                    if packed:
                        raise ValueError("Packed JSON must be an array or object")
                    mode = "lines"

            if mode == "lines":
                newline_index = buffer.find("\n")
                if newline_index == -1:
                    if eof:
                        line = buffer.strip()
                        if line:
                            yield json.loads(line)
                        buffer = ""
                    break

                line = buffer[:newline_index].strip()
                buffer = buffer[newline_index + 1 :]
                if line:
                    yield json.loads(line)
                    progressed = True
                continue

            if mode == "array":
                if buffer[0] == "]":
                    buffer = buffer[1:]
                    mode = "done"
                    continue
                if buffer[0] == ",":
                    buffer = buffer[1:]
                    progressed = True
                    continue
                try:
                    value, offset = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    if eof:
                        raise
                    break
                yield value
                buffer = buffer[offset:]
                progressed = True
                continue

            if mode == "object":
                try:
                    value, offset = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    if eof:
                        raise
                    break
                yield value
                # Continue reading objects for JSON Lines, including when the
                # next record arrives in a later S3 body read.
                buffer = buffer[offset:]
                progressed = True
                continue

            if mode == "done":
                raise ValueError("Unexpected trailing content after JSON array")

        if eof:
            if mode == "array":
                raise ValueError("Unterminated JSON array")
            break

# =============================================================================
# DATA PROCESSING FUNCTIONS
# =============================================================================

def read_s3_file_to_df(
    s3, bucket: str, key: str,
    file_type: str,
    delimiter: Optional[str],
    quotechar: Optional[str],
    skipheaderlines: Optional[int],
    json_mode: Optional[str],
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """
    Read entire S3 file into pandas DataFrame (non-streaming, for small files or schema inference).
    
    This function loads the complete file into memory and parses it into a DataFrame.
    Used for:
    - Schema inference (reading sample data to determine columns)
    - Small files that fit in memory
    - File structure validation
    
    For large files, use read_s3_file_in_chunks() which streams data in chunks.
    
    Supports all file types:
    - CSV/TSV: Configurable delimiter, quote char, header skip, escape char, null sequence
    - JSON: Normal mode (flattened) or packed mode (stored in _DATA column)
    - Parquet: Direct pandas read
    - Avro: Using fastavro library
    - XML: Packed mode (_DATA XML string) or unpacked mode (flattened columns)
    
    Automatically adds metadata columns:
    - _FILE: Source S3 object key (full path within bucket)
    - _ROW: Row index within file
    - _MODIFIED: File last modified timestamp (UTC)
    - _SPECT_SYNCED: DataSyncher sync timestamp (UTC)
    - _SPECT_DELETED: Soft-delete marker (FALSE for incoming source rows)
    
    Args:
        s3: Boto3 S3 client
        bucket: S3 bucket name
        key: S3 object key (file path)
        file_type: File type ("csv", "tsv", "json", "parquet", "avro", "xml")
        delimiter: Field delimiter for CSV/TSV (default: "," for CSV, "\t" for TSV)
        quotechar: Quote character for CSV/TSV (default: '"')
        skipheaderlines: Number of header lines to skip (default: 0)
        json_mode: JSON parsing mode ("packed" or None for normal)
        xml_mode: XML parsing mode ("packed" or "unpacked")
        escape_char: Escape character for CSV/TSV (optional)
        null_sequence: String sequence representing null values (optional)
        skip_footer_lines: Number of footer lines to skip (optional)
        line_separator: Line separator character (optional, supports \\n, \\r\\n, \\r)
        
    Returns:
        pd.DataFrame: DataFrame with data and metadata columns, or None if parsing fails
        
    Example:
        >>> df = read_s3_file_to_df(s3, "my-bucket", "data.csv", "csv", ",", '"', 1)
        >>> df.columns.tolist()
        ['_FILE', '_ROW', '_MODIFIED', '_SPECT_SYNCED', '_SPECT_DELETED', 'col1', 'col2', ...]
    """
    log(f"[READ] s3://{bucket}/{key} type={file_type}")
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"]
        last_modified = obj["LastModified"]
    except ClientError as e:
        log(f"   S3 error: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        log(f"   Unexpected error reading from S3: {type(e).__name__}: {e}")
        return None

    df: Optional[pd.DataFrame] = None

    # CSV / TSV
    if file_type in ("csv", "tsv"):
        content = body.read()
        delim = normalize_delimiter(delimiter, file_type)
        q = normalize_quotechar(quotechar)
        quote_disabled = q == ""
        skip = normalize_skipheaderlines(skipheaderlines)
        
        if skip == 0:
            pandas_skiprows = None
            pandas_header = 0
        else:
            # Skip the first N physical lines, then treat the next line as header.
            pandas_skiprows = list(range(skip))
            pandas_header = 0
        
        escapechar_val = normalize_escapechar(escape_char)
        
        na_values_list = None
        if null_sequence and null_sequence.strip():
            na_values_list = [null_sequence.strip()]
        
        skipfooter_val = normalize_skipheaderlines(skip_footer_lines) if skip_footer_lines else 0
        
        lineterminator_val = normalize_line_separator(line_separator)
        
        try:
            read_csv_params = {
                "delimiter": delim,
                "skiprows": pandas_skiprows,
                "header": pandas_header,
            }
            if quote_disabled:
                read_csv_params["quoting"] = csv.QUOTE_NONE
            else:
                read_csv_params["quotechar"] = q
            if escapechar_val:
                read_csv_params["escapechar"] = escapechar_val
            if na_values_list:
                read_csv_params["na_values"] = na_values_list
            if skipfooter_val > 0:
                read_csv_params["skipfooter"] = skipfooter_val
                read_csv_params["engine"] = "python"
            if lineterminator_val and skipfooter_val == 0:
                read_csv_params["lineterminator"] = lineterminator_val
            
            if skipfooter_val > 0:
                # Python engine with skipfooter expects text input.
                text_data = content.decode("utf-8")
                read_csv_params.pop("encoding", None)
                df = pd.read_csv(io.StringIO(text_data), **read_csv_params)
            else:
                df = pd.read_csv(io.BytesIO(content), **read_csv_params)
        except UnicodeDecodeError:
            try:
                if skipfooter_val > 0:
                    text_data = content.decode("ISO-8859-1")
                    read_csv_params.pop("encoding", None)
                    df = pd.read_csv(io.StringIO(text_data), **read_csv_params)
                else:
                    read_csv_params["encoding"] = "ISO-8859-1"
                    df = pd.read_csv(io.BytesIO(content), **read_csv_params)
            except Exception as e2:
                log(f"   CSV/TSV parse failed: {e2}")
                return None
        except Exception as e:
            log(f"   CSV/TSV parse error: {type(e).__name__}: {e}")
            return None

    # JSON
    elif file_type == "json":
        try:
            mode = (json_mode or "").lower()
            json_rows = []
            for value in _iter_s3_json_values_from_body(body, packed=(mode == "packed")):
                if mode == "packed":
                    json_rows.append({"_DATA": value, "_ROW": len(json_rows)})
                    continue
                value = _unwrap_single_key_json_container(value)
                if isinstance(value, list):
                    json_rows.extend(value)
                else:
                    json_rows.append(value)

            if not json_rows:
                log("   JSON parse failed: no valid data found")
                return None

            if mode != "packed":
                _validate_json_record_keys(json_rows, None)
                df = pd.DataFrame(json_rows)
                try:
                    assert_no_fully_empty_rows(df)
                except Exception as e:
                    log(f"   JSON empty-row validation error: {e}")
                    raise
            else:
                df = pd.DataFrame(json_rows)
        except json.JSONDecodeError as e:
            log(f"   JSON parse error: {type(e).__name__}: {e}")
            return None
        except Exception as e:
            log(f"   JSON parse error: {type(e).__name__}: {e}")
            return None

    # Parquet
    elif file_type == "parquet":
        try:
            content = body.read()
            if not _is_valid_parquet_file(content):
                log("   Parquet parse error: Invalid parquet file signature (expected 'PAR1' at start/end).")
                return None
            df = pd.read_parquet(io.BytesIO(content))
        except Exception as e:
            log(f"   Parquet parse error: {type(e).__name__}: {e}")
            return None

    # Avro
    elif file_type == "avro":
        try:
            content = body.read()
            if not _is_valid_avro_container(content):
                log("   Avro parse error: Invalid Avro container file header (expected magic bytes 'Obj\\x01').")
                return None
            with io.BytesIO(content) as b:
                reader = fastavro.reader(b)
                records = list(reader)
                df = pd.DataFrame(records)
        except Exception as e:
            log(f"   Avro parse error: {type(e).__name__}: {e}")
            return None

    # XML
    elif file_type == "xml":
        content = body.read()
        root = _parse_xml_root(content)
        if root is None:
            return None
        
        mode = (xml_mode or "packed").lower()
        if mode == "unpacked":
            df = parse_xml_unpacked(root)
        else:
            if mode != "packed":
                log(f"   XML mode '{xml_mode}' is not supported. Using 'packed' mode.")
            df = parse_xml_packed(root)
        
        if df is None or df.empty:
            log("   XML parsing produced no data")
            return None
        if mode == "unpacked" and not _xml_unpacked_has_business_data(df):
            raise XMLNoBusinessColumnsError("Update schema failed: no business columns in XML")

    if df is None:
        log("   DataFrame is None after parsing")
        return None
    
    if df.empty:
        log("   DataFrame is empty (no rows)")
        return None

    # Add metadata columns
    source_file_key = str(key).strip()
    df.insert(0, "_FILE", source_file_key)
    if "_ROW" not in df.columns:
        df.insert(1, "_ROW", range(len(df)))
    else:
        df["_ROW"] = df["_ROW"].astype(int)

    lm_utc = pd.to_datetime(last_modified, utc=True)
    ds_utc = pd.Timestamp.now(tz="UTC")
    df.insert(2, "_MODIFIED", lm_utc)
    df.insert(3, "_SPECT_SYNCED", ds_utc)
    df.insert(4, "_SPECT_DELETED", False)

    df = strip_trailing_linebreaks_in_text_columns(df)
    df = normalize_s3_text_nulls(df)
    log(f"   Final DataFrame: {len(df)} rows, {len(df.columns)} cols (including metadata)")
    return df

def add_metadata_to_chunk(
    chunk_df: pd.DataFrame,
    key: str,
    last_modified: datetime,
    chunk_number: int,
    global_row_offset: int = 0
) -> pd.DataFrame:
    """Add DataSyncher metadata columns to a chunk."""
    if chunk_df is None or chunk_df.empty:
        return chunk_df
    
    source_file_key = str(key).strip()
    if "_FILE" not in chunk_df.columns:
        chunk_df.insert(0, "_FILE", source_file_key)
    else:
        chunk_df["_FILE"] = source_file_key
    
    if "_ROW" not in chunk_df.columns:
        chunk_df.insert(1, "_ROW", range(global_row_offset, global_row_offset + len(chunk_df)))
    else:
        chunk_df["_ROW"] = range(global_row_offset, global_row_offset + len(chunk_df))
    
    lm_utc = pd.to_datetime(last_modified, utc=True)
    ds_utc = pd.Timestamp.now(tz="UTC")
    if "_MODIFIED" not in chunk_df.columns:
        chunk_df.insert(2, "_MODIFIED", lm_utc)
    if "_SPECT_SYNCED" not in chunk_df.columns:
        chunk_df.insert(3, "_SPECT_SYNCED", ds_utc)
    if "_SPECT_DELETED" not in chunk_df.columns:
        chunk_df.insert(4, "_SPECT_DELETED", False)
    
    chunk_df = strip_trailing_linebreaks_in_text_columns(chunk_df)
    chunk_df = normalize_s3_text_nulls(chunk_df)
    return chunk_df


def normalize_s3_text_nulls(df: pd.DataFrame) -> pd.DataFrame:
    """Replace text null markers without comparing nested values to strings."""
    result = df.copy()
    markers = {"<NA>", "NaN", "nan", "None"}
    for column in result.select_dtypes(include=["object", "string"]).columns:
        result[column] = result[column].map(
            lambda value: None if isinstance(value, str) and value in markers else value
        )
    return result


def strip_trailing_linebreaks_in_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize text cells by removing trailing CR/LF characters.
    Prevents false updates like 'value' vs 'value\\r' in delta comparisons.
    """
    if df is None or df.empty:
        return df

    text_cols = df.select_dtypes(include=["object", "string"]).columns
    for col in text_cols:
        df[col] = df[col].map(
            lambda v: v.rstrip("\r\n") if isinstance(v, str) else v
        )
    return df


def drop_fully_empty_business_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop rows where all non-metadata columns are NULL/blank.
    """
    if df is None or df.empty:
        return df

    metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED"}
    business_cols = [c for c in df.columns if c not in metadata_cols]
    if not business_cols:
        return df

    work_df = df[business_cols].copy()
    text_cols = work_df.select_dtypes(include=["object", "string"]).columns
    if len(text_cols) > 0:
        work_df[text_cols] = work_df[text_cols].replace(r"^\s*$", pd.NA, regex=True)

    keep_mask = ~work_df.isna().all(axis=1)
    return df.loc[keep_mask].copy()


def assert_no_fully_empty_rows(df: pd.DataFrame) -> None:
    """
    Fail when a row has no business values (all NULL/blank).
    """
    if df is None or df.empty:
        return

    metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED"}
    business_cols = [c for c in df.columns if c not in metadata_cols]
    if not business_cols:
        return

    work_df = df[business_cols].copy()
    text_cols = work_df.select_dtypes(include=["object", "string"]).columns
    null_markers = {"<NA>", "NaN", "nan", "None", "null"}
    for col in text_cols:
        work_df[col] = work_df[col].map(
            lambda value: pd.NA
            if isinstance(value, str) and (not value.strip() or value in null_markers)
            else value
        )

    all_empty_mask = work_df.isna().all(axis=1)
    if bool(all_empty_mask.any()):
        raise ValueError(
            "Invalid row detected: all business columns are empty/null. "
            "Please correct the file and re-run."
        )


def validate_no_nested_values_in_unpacked_json(df: pd.DataFrame) -> None:
    """
    Fail when unpacked JSON contains nested objects/arrays in any column.
    This enforces consistent primitive types across all rows.
    """
    if df is None or df.empty:
        return
    bad_cols = []
    for col in df.columns:
        series = df[col]
        if series.apply(lambda v: isinstance(v, (dict, list, np.ndarray))).any():
            bad_cols.append(col)
    if bad_cols:
        preview = ", ".join(sorted(set(bad_cols))[:5])
        raise ValueError(
            f"Mixed types detected in unpacked JSON. Column(s) contain objects/arrays: {preview}. "
            "Please fix the file or use packed JSON."
        )

def _validate_json_record_keys(
    records: List[Dict[str, Any]],
    expected_keys: Optional[set] = None,
) -> Optional[set]:
    """
    Enforce consistent JSON keys across all records in unpacked mode.
    Missing or extra keys are treated as an error.
    """
    if not records:
        return expected_keys
    for record in records:
        if not isinstance(record, dict):
            continue
        keys = set(record.keys())
        if expected_keys is None:
            expected_keys = keys
            continue
        if keys != expected_keys:
            missing = expected_keys - keys
            extra = keys - expected_keys
            missing_preview = ", ".join(sorted(list(missing))[:5])
            extra_preview = ", ".join(sorted(list(extra))[:5])
            details = []
            if missing:
                details.append(f"Missing: {missing_preview}")
            if extra:
                details.append(f"Extra: {extra_preview}")
            detail_msg = "; ".join(details) if details else "Schema differs across rows"
            raise ValueError(
                "Inconsistent JSON keys detected in unpacked mode. "
                "All rows must have the same set of fields. "
                f"{detail_msg}. Please fix the file or use packed JSON."
            )
    return expected_keys

def drop_rows_with_missing_trailing_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Drop rows that appear to have fewer columns than the header
    (detected as trailing NaNs across the row).
    """
    if df is None or df.empty:
        return df, 0

    # Detect trailing NaNs: last non-null index is before the last column.
    non_na = (~df.isna()).to_numpy()
    if non_na.size == 0:
        return df, 0
    last_idx = non_na.shape[1] - 1
    has_any = non_na.any(axis=1)
    last_non_na = np.where(
        has_any,
        last_idx - np.argmax(non_na[:, ::-1], axis=1),
        -1,
    )
    trailing_missing = last_idx - last_non_na
    # Drop only when MOST columns are missing (trailing). Define "most" as >= half.
    threshold = max(1, (non_na.shape[1] + 1) // 2)
    drop_mask = (last_non_na >= 0) & (trailing_missing >= threshold)
    dropped = int(drop_mask.sum())
    if dropped == 0:
        return df, 0
    return df.loc[~drop_mask].copy(), dropped


def reorder_chunk_columns_for_snowflake(
    chunk_df: pd.DataFrame,
    load_method: str,
    user_pk: Optional[List[str]] = None
) -> pd.DataFrame:
    """
    Reorder chunk columns to match ensure_tables() creation order:
    1) metadata prefix columns (_FILE, _ROW, _MODIFIED) first
    2) PK columns next (excluding prefix)
    3) regular columns
    4) metadata tail columns (_SPECT_SYNCED, _SPECT_DELETED) last
    """
    if chunk_df is None or chunk_df.empty:
        return chunk_df

    if load_method == "upsert_file_row":
        pk_cols = ["_FILE", "_ROW"]
    elif load_method == "append_file_modified":
        pk_cols = ["_FILE", "_ROW", "_MODIFIED"]
    elif load_method == "upsert_custom_key":
        pk_cols = [sanitize_column_name(c).upper() for c in (user_pk or [])]
        if not pk_cols:
            pk_cols = ["_FILE", "_ROW"]
    else:
        pk_cols = ["_FILE", "_ROW"]

    metadata_prefix = ["_FILE", "_ROW", "_MODIFIED"]
    metadata_tail = ["_SPECT_SYNCED", "_SPECT_DELETED"]

    normalized_to_actual = {}
    for col in chunk_df.columns:
        norm = col.upper() if col.startswith("_") else sanitize_column_name(col).upper()
        if norm not in normalized_to_actual:
            normalized_to_actual[norm] = col

    ordered_cols = []

    for pref in metadata_prefix:
        if pref in normalized_to_actual:
            actual_col = normalized_to_actual[pref]
            if actual_col not in ordered_cols:
                ordered_cols.append(actual_col)

    for pk in pk_cols:
        if pk in normalized_to_actual:
            actual_col = normalized_to_actual[pk]
            if actual_col not in ordered_cols:
                ordered_cols.append(actual_col)

    for col in chunk_df.columns:
        norm = col.upper() if col.startswith("_") else sanitize_column_name(col).upper()
        if col in ordered_cols:
            continue
        if norm in metadata_tail:
            continue
        ordered_cols.append(col)

    for tail_col in metadata_tail:
        if tail_col in normalized_to_actual:
            actual_col = normalized_to_actual[tail_col]
            if actual_col not in ordered_cols:
                ordered_cols.append(actual_col)

    return chunk_df[ordered_cols]


def align_chunk_to_table_column_order(
    conn,
    chunk_df: pd.DataFrame,
    table_name: str,
    sf_database: str,
    sf_schema: str,
) -> pd.DataFrame:
    """
    Align DataFrame columns to exact Snowflake table ordinal order.

    COPY INTO maps fields by position when explicit target columns are used.
    If an existing table was evolved over time (ALTER ADD COLUMN), ordinal order
    can diverge from our inferred chunk order and cause values to land in wrong
    columns (for example _ROW becoming NULL in a NOT NULL column).
    """
    if chunk_df is None or chunk_df.empty:
        return chunk_df

    sql_cols = f"""
        SELECT COLUMN_NAME
        FROM {Q(sf_database)}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{sf_schema.upper()}'
          AND TABLE_NAME   = '{table_name.upper()}'
        ORDER BY ORDINAL_POSITION
    """
    with conn.cursor() as cur:
        cur.execute(sql_cols)
        target_cols = [r[0] for r in cur.fetchall()]

    if not target_cols:
        return chunk_df

    # Map normalized chunk names -> actual chunk column names.
    norm_to_actual = {}
    for col in chunk_df.columns:
        norm = col.upper() if col.startswith("_") else sanitize_column_name(col).upper()
        if norm not in norm_to_actual:
            norm_to_actual[norm] = col

    aligned_df = pd.DataFrame(index=chunk_df.index)
    for target_col in target_cols:
        source_col = norm_to_actual.get(target_col.upper())
        if source_col is not None:
            aligned_df[target_col] = chunk_df[source_col]
        else:
            aligned_df[target_col] = None

    return aligned_df

# =============================================================================
# STREAMING CHUNK GENERATOR
# =============================================================================

class _S3RangeReader(io.RawIOBase):
    """Seekable S3 object reader; Parquet can fetch its footer without a full download."""

    def __init__(self, s3, bucket, key, metadata=None):
        super().__init__()
        self.s3, self.bucket, self.key = s3, bucket, key
        metadata = metadata or s3.head_object(Bucket=bucket, Key=key)
        self.size = metadata["ContentLength"]
        self.etag = metadata.get("ETag")
        self.position = 0
        self._cache_start = 0
        self._cache = b""

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        position = offset if whence == io.SEEK_SET else (
            self.position + offset if whence == io.SEEK_CUR else
            self.size + offset if whence == io.SEEK_END else -1
        )
        if position < 0:
            raise ValueError("Invalid S3 seek position")
        self.position = position
        return position

    def read(self, size=-1):
        if self.closed:
            raise ValueError("Read from closed S3 object")
        remaining = max(0, self.size - self.position)
        size = remaining if size is None or size < 0 else min(size, remaining)
        if not size:
            return b""
        cache_offset = self.position - self._cache_start
        if 0 <= cache_offset and cache_offset + size <= len(self._cache):
            data = self._cache[cache_offset:cache_offset + size]
            self.position += size
            return data
        # TextIOWrapper asks for small reads; amortize S3 requests with a bounded cache.
        fetch_size = min(remaining, max(size, 1024 * 1024))
        args = dict(Bucket=self.bucket, Key=self.key,
                    Range=f"bytes={self.position}-{self.position + fetch_size - 1}")
        if self.etag:
            args["IfMatch"] = self.etag
        body = self.s3.get_object(**args)["Body"]
        try:
            data = body.read(fetch_size)
        finally:
            body.close()
        if len(data) != fetch_size:
            raise IOError("Incomplete S3 range response")
        if fetch_size <= 1024 * 1024:
            self._cache_start, self._cache = self.position, data
        self.position += size
        return data[:size]

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def _iter_csv_physical_lines(stream, separator, quotechar, escapechar):
    """Feed csv.reader logical lines for a custom record separator."""
    if not separator or separator in ("\n", "\r", "\r\n"):
        yield from stream
        return
    pending, quoted, escaped = [], False, False
    while True:
        text = stream.read(65536)
        if not text:
            break
        for char in text:
            if escaped:
                escaped = False
            elif escapechar and char == escapechar:
                escaped = True
            elif quotechar and char == quotechar:
                quoted = not quoted
            elif char == separator and not quoted:
                yield "".join(pending) + "\n"
                pending = []
                continue
            pending.append(char)
    if pending:
        yield "".join(pending)


def _iter_s3_csv_records(s3, bucket, key, delimiter, quotechar, skip,
                         escapechar, footer, separator, encoding):
    from collections import deque
    # Source fields may contain large JSON/text values (csv defaults to only 128 KiB).
    csv.field_size_limit(max(csv.field_size_limit(), 2**31 - 1))
    with io.TextIOWrapper(io.BufferedReader(_S3RangeReader(s3, bucket, key)),
                          encoding=encoding, newline="") as stream:
        lines = iter(_iter_csv_physical_lines(stream, separator, quotechar, escapechar))
        for _ in range(skip):
            next(lines, None)

        def without_footer():
            pending = deque()
            for line in lines:
                pending.append(line)
                if len(pending) > footer:
                    yield pending.popleft()

        reader = csv.reader(without_footer(), delimiter=delimiter,
                            quotechar=quotechar or None,
                            quoting=csv.QUOTE_MINIMAL if quotechar else csv.QUOTE_NONE,
                            escapechar=escapechar, strict=True)
        for row in reader:
            if row:  # pandas skips physically blank lines too.
                yield row


def _validate_s3_delimited_rows(s3, bucket, key, delimiter, quotechar, skip,
                               escapechar, footer, separator):
    # Validate before yielding any data. Empty cells are valid fields; pandas NaNs
    # cannot tell them apart from fields that were absent in the source record.
    for encoding in ("utf-8-sig", "ISO-8859-1"):
        records = _iter_s3_csv_records(s3, bucket, key, delimiter, quotechar, skip,
                                      escapechar, footer, separator, encoding)
        try:
            header = next(records, None)
            if header is None:
                return encoding
            for row_number, row in enumerate(records, 2):
                if len(row) != len(header):
                    raise ValueError(
                        f"Invalid CSV/TSV record {row_number} in '{key}': "
                        f"expected {len(header)} columns, found {len(row)}."
                    )
            return encoding
        except UnicodeDecodeError:
            if encoding != "utf-8-sig":
                raise
        finally:
            records.close()


def read_s3_pk_sample(s3, bucket, key, file_type, sample_rows=1000, **options):
    """Read at most the requested records, closing streams when sampling stops."""
    limit = max(1, int(sample_rows))
    if file_type in ("csv", "tsv"):
        for encoding in ("utf-8-sig", "ISO-8859-1"):
            records = _iter_s3_csv_records(
                s3, bucket, key, normalize_delimiter(options.get("delimiter"), file_type),
                normalize_quotechar(options.get("quotechar")),
                normalize_skipheaderlines(options.get("skipheaderlines")),
                normalize_escapechar(options.get("escape_char")),
                normalize_skipheaderlines(options.get("skip_footer_lines")),
                normalize_line_separator(options.get("line_separator")), encoding,
            )
            try:
                header = next(records, None)
                if header is None:
                    return pd.DataFrame()
                rows = []
                for row in records:
                    if len(row) != len(header):
                        raise ValueError("CSV/TSV record does not match header column count")
                    rows.append(row)
                    if len(rows) == limit:
                        break
                text = io.StringIO()
                writer = csv.writer(text)
                writer.writerow(header)
                writer.writerows(rows)
                text.seek(0)
                null = options.get("null_sequence")
                df = pd.read_csv(text, na_values=[null.strip()] if null and null.strip() else None)
                metadata = s3.head_object(Bucket=bucket, Key=key)
                return add_metadata_to_chunk(df, key, metadata["LastModified"], 1, 0)
            except UnicodeDecodeError:
                if encoding != "utf-8-sig":
                    raise
            finally:
                records.close()
    if file_type == "xml":
        mode = (options.get("xml_mode") or "packed").lower()
        records = []
        with io.BufferedReader(_S3RangeReader(s3, bucket, key)) as stream:
            context = iter(ET.iterparse(stream, events=("start", "end")))
            _, root = next(context)
            depth = 0
            record_tag = None
            for event, elem in context:
                if event == "start":
                    depth += 1
                    if depth == 1 and record_tag is None:
                        record_tag = elem.tag
                else:
                    if depth == 1:
                        if elem.tag == record_tag:
                            parser = parse_xml_record_unpacked if mode == "unpacked" else parse_xml_record_packed
                            records.append(parser(elem, len(records)))
                        root.remove(elem)
                        if len(records) >= limit:
                            break
                    depth -= 1
            if not records:
                parser = parse_xml_record_unpacked if mode == "unpacked" else parse_xml_record_packed
                records.append(parser(root, 0))
        df = pd.DataFrame(records)
        if mode == "unpacked" and not _xml_unpacked_has_business_data(df):
            raise XMLNoBusinessColumnsError("No business columns in XML")
        metadata = s3.head_object(Bucket=bucket, Key=key)
        return add_metadata_to_chunk(df, key, metadata["LastModified"], 1, 0)
    chunks = read_s3_file_in_chunks(
        s3, bucket, key, file_type, chunk_size=limit,
        enforce_json_key_consistency=True, json_expected_keys_state={"expected": None},
        **options,
    )
    try:
        for frame, *_ in chunks:
            if not frame.empty:
                return frame.head(limit).copy()
        return pd.DataFrame()
    finally:
        chunks.close()


def read_s3_file_in_chunks(
    s3, bucket: str, key: str,
    file_type: str,
    chunk_size: int = 5000,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
    enforce_row_schema_consistency: bool = False,
    enforce_json_key_consistency: bool = False,
    json_expected_keys_state: Optional[Dict[str, Optional[set]]] = None,
):
    """
    Generator function that yields chunks of DataFrames from S3 file (streaming mode).
    Raises real exceptions (no sentinel yields) so table-level logs show the
    precise failure cause instead of a generic streaming error.
    
    This function processes large files in memory-efficient chunks, reading and parsing
    data incrementally rather than loading the entire file into memory. This is critical
    for handling large files (GB+) without running out of memory.
    
    Streaming strategies by file type:
    - CSV/TSV: Uses pandas chunksize parameter for row-based streaming
    - JSON: Incrementally decodes top-level records
    - Parquet: Uses S3 range reads and bounded Arrow record batches
    - Avro: Reads records incrementally using fastavro streaming reader
    - XML: Uses iterparse for incremental parsing (memory-efficient)
    
    Each chunk includes metadata columns (_FILE, _ROW, _MODIFIED, _SPECT_SYNCED, _SPECT_DELETED) with
    proper row offsets to maintain global row numbering across chunks.
    
    Args:
        s3: Boto3 S3 client
        bucket: S3 bucket name
        key: S3 object key (file path)
        file_type: File type ("csv", "tsv", "json", "parquet", "avro", "xml")
        chunk_size: Number of rows per chunk (default: 5000)
        delimiter: Field delimiter for CSV/TSV
        quotechar: Quote character for CSV/TSV
        skipheaderlines: Number of header lines to skip
        json_mode: JSON parsing mode ("packed" or None)
        xml_mode: XML parsing mode ("packed" or "unpacked")
        escape_char: Escape character for CSV/TSV
        null_sequence: String sequence representing null values
        skip_footer_lines: Number of footer lines to skip
        line_separator: Line separator character
        
    Yields:
        tuple: (chunk_df, chunk_number, is_last_chunk, last_modified, total_rows_processed)
            - chunk_df: pandas DataFrame with chunk data (empty DataFrame on last yield)
            - chunk_number: Sequential chunk number (1-based)
            - is_last_chunk: Boolean indicating if this is the final chunk
            - last_modified: File last modified timestamp (datetime)
            - total_rows_processed: Cumulative row count across all chunks
        
    Example:
        >>> for chunk_df, chunk_num, is_last, last_mod, total_rows in read_s3_file_in_chunks(
        ...     s3, "bucket", "large.csv", "csv", chunk_size=10000
        ... ):
        ...     if not chunk_df.empty:
        ...         log(f"Processing chunk {chunk_num}: {len(chunk_df)} rows")
        ...     if is_last:
        ...         log(f"Total rows: {total_rows}")
    """
    log(f"[STREAM] Starting chunked read: s3://{bucket}/{key}")
    
    try:
        chunk_rows = max(1, int(chunk_size)) if chunk_size is not None else 5000
    except (TypeError, ValueError):
        chunk_rows = 5000

    try:
        skip = int(skipheaderlines) if skipheaderlines not in (None, "", False) else 0
    except (TypeError, ValueError):
        skip = 0

    log(f"  [STREAM] File type: {file_type}, chunk_size: {chunk_rows:,} rows")
    file_type = file_type.lower() if file_type else ""
    
    chunk_number = 0
    total_rows_processed = 0
    last_modified = None
    
    try:
        # Get file metadata for logging and timestamp tracking
        obj_meta = s3.head_object(Bucket=bucket, Key=key)
        last_modified = obj_meta["LastModified"]
        file_size = obj_meta["ContentLength"]
        log(f"  [STREAM] File metadata: size={file_size:,} bytes, last_modified={last_modified}")
        
        # CSV/TSV: Use pandas chunksize (BEST for streaming)
        if file_type in ("csv", "tsv"):
            delim = normalize_delimiter(delimiter, file_type)
            q = normalize_quotechar(quotechar)
            quote_disabled = q == ""
            
            if skip == 0:
                pandas_skiprows = None
                pandas_header = 0
            else:
                # Skip the first N physical lines, then treat the next line as header.
                pandas_skiprows = list(range(skip))
                pandas_header = 0
            
            escapechar_val = normalize_escapechar(escape_char)
            
            na_values_list = None
            if null_sequence and null_sequence.strip():
                na_values_list = [null_sequence.strip()]
            
            skipfooter_val = normalize_skipheaderlines(skip_footer_lines) if skip_footer_lines else 0
            
            lineterminator_val = normalize_line_separator(line_separator)
            
            csv_encoding = "utf-8"
            if enforce_row_schema_consistency:
                csv_encoding = _validate_s3_delimited_rows(
                    s3, bucket, key, delim, q, skip, escapechar_val,
                    skipfooter_val, lineterminator_val,
                )

            obj = s3.get_object(Bucket=bucket, Key=key)
            file_stream = obj["Body"]

            # Debug pre-scan to estimate malformed rows (when pandas is set to skip bad lines)
            try:
                head_bytes = _read_s3_head_bytes(s3, bucket, key, max_bytes=65536)
                if head_bytes:
                    try:
                        head_text = head_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        head_text = head_bytes.decode("ISO-8859-1", errors="replace")
                    debug_stats = _debug_count_bad_csv_lines(
                        head_text,
                        delimiter=delim,
                        quotechar=q,
                        escapechar=escapechar_val,
                        skipheaderlines=skip,
                        skip_footer_lines=skip_footer_lines,
                        max_lines=2000,
                    )
                    log(
                        "[STREAM][DEBUG] CSV pre-scan (prefix) "
                        f"lines={debug_stats['lines_scanned']}, "
                        f"blank={debug_stats['blank_lines']}, "
                        f"data={debug_stats['data_lines']}, "
                        f"bad={debug_stats['bad_lines']}, "
                        f"expected_cols={debug_stats['expected_cols']}"
                    )
                    if debug_stats.get("header_line_num") is not None:
                        log(
                            "[STREAM][DEBUG] CSV header (prefix) "
                            f"line={debug_stats['header_line_num']}, "
                            f"header={debug_stats.get('header_line')}"
                        )
                    if debug_stats.get("first_bad_line_num") is not None:
                        log(
                            "[STREAM][DEBUG] CSV first bad line (prefix) "
                            f"line={debug_stats['first_bad_line_num']}, "
                            f"cols={debug_stats.get('first_bad_cols')}, "
                            f"row={debug_stats.get('first_bad_line')}"
                        )
                    bad_lines_detail = debug_stats.get("bad_lines_detail") or []
                    if bad_lines_detail:
                        log("[STREAM][DEBUG] CSV bad lines (prefix):")
                        for item in bad_lines_detail:
                            log(
                                "  - "
                                f"line={item.get('line_num')}, "
                                f"cols={item.get('cols')}, "
                                f"row={item.get('row')}"
                            )
            except Exception:
                pass
            
            def _iterate_with_metadata(df_iterable):
                nonlocal chunk_number, total_rows_processed
                for chunk_df in df_iterable:
                    chunk_number += 1
                    if chunk_df.empty:
                        continue
                    try:
                        assert_no_fully_empty_rows(chunk_df)
                    except Exception as e:
                        log(f"  [STREAM] Invalid empty row(s) detected: {e}")
                        raise
                    chunk_df = drop_fully_empty_business_rows(chunk_df)
                    if not chunk_df.empty and not enforce_row_schema_consistency:
                        chunk_df, dropped_rows = drop_rows_with_missing_trailing_columns(chunk_df)
                        if dropped_rows:
                            if enforce_row_schema_consistency:
                                raise ValueError(
                                    f"Invalid row detected: {dropped_rows} row(s) have missing trailing columns. "
                                    "All rows must match the header column count."
                                )
                            log(
                                f"  [STREAM] Dropped {dropped_rows} malformed row(s) with most columns missing "
                                f"(trailing) for '{key}'"
                            )
                    if chunk_df.empty:
                        log(f"  [STREAM] Skipped chunk {chunk_number}: all rows were empty")
                        continue
                    global_row_offset = total_rows_processed
                    chunk_df = add_metadata_to_chunk(
                        chunk_df, key, last_modified, chunk_number, global_row_offset
                    )
                    total_rows_processed += len(chunk_df)
                    yield chunk_df, chunk_number, False, last_modified, total_rows_processed

            try:
                base_read_csv_params = {
                    "delimiter": delim,
                    "skiprows": pandas_skiprows,
                    "header": pandas_header,
                    "encoding": csv_encoding,
                    "on_bad_lines": "error" if enforce_row_schema_consistency else "skip"
                }
                if quote_disabled:
                    base_read_csv_params["quoting"] = csv.QUOTE_NONE
                else:
                    base_read_csv_params["quotechar"] = q
                if escapechar_val:
                    base_read_csv_params["escapechar"] = escapechar_val
                if na_values_list:
                    base_read_csv_params["na_values"] = na_values_list
                if lineterminator_val and skipfooter_val == 0:
                    base_read_csv_params["lineterminator"] = lineterminator_val

                if skipfooter_val > 0:
                    # pandas does not support skipfooter with iterator/chunksize.
                    # Read once with skipfooter and then slice manually into chunks.
                    full_read_csv_params = dict(base_read_csv_params)
                    full_read_csv_params["skipfooter"] = skipfooter_val
                    full_read_csv_params["engine"] = "python"

                    content = file_stream.read()
                    text_data = content.decode(csv_encoding)
                    full_read_csv_params.pop("encoding", None)
                    full_df = pd.read_csv(io.StringIO(text_data), **full_read_csv_params)
                    for start_idx in range(0, len(full_df), chunk_rows):
                        yield from _iterate_with_metadata([full_df.iloc[start_idx:start_idx + chunk_rows].copy()])
                else:
                    chunk_read_csv_params = dict(base_read_csv_params)
                    chunk_read_csv_params["chunksize"] = chunk_rows
                    chunk_iter = pd.read_csv(file_stream, **chunk_read_csv_params)
                    yield from _iterate_with_metadata(chunk_iter)

                yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed

            except UnicodeDecodeError:
                file_stream.close()
                obj = s3.get_object(Bucket=bucket, Key=key)
                file_stream = obj["Body"]

                base_read_csv_params = {
                    "delimiter": delim,
                    "skiprows": pandas_skiprows,
                    "header": pandas_header,
                    "encoding": "ISO-8859-1",
                    "on_bad_lines": "error" if enforce_row_schema_consistency else "skip"
                }
                if quote_disabled:
                    base_read_csv_params["quoting"] = csv.QUOTE_NONE
                else:
                    base_read_csv_params["quotechar"] = q
                if escapechar_val:
                    base_read_csv_params["escapechar"] = escapechar_val
                if na_values_list:
                    base_read_csv_params["na_values"] = na_values_list
                if lineterminator_val and skipfooter_val == 0:
                    base_read_csv_params["lineterminator"] = lineterminator_val

                if skipfooter_val > 0:
                    full_read_csv_params = dict(base_read_csv_params)
                    full_read_csv_params["skipfooter"] = skipfooter_val
                    full_read_csv_params["engine"] = "python"

                    content = file_stream.read()
                    text_data = content.decode("ISO-8859-1")
                    full_read_csv_params.pop("encoding", None)
                    full_df = pd.read_csv(io.StringIO(text_data), **full_read_csv_params)
                    for start_idx in range(0, len(full_df), chunk_rows):
                        yield from _iterate_with_metadata([full_df.iloc[start_idx:start_idx + chunk_rows].copy()])
                else:
                    chunk_read_csv_params = dict(base_read_csv_params)
                    chunk_read_csv_params["chunksize"] = chunk_rows
                    chunk_iter = pd.read_csv(file_stream, **chunk_read_csv_params)
                    yield from _iterate_with_metadata(chunk_iter)

                yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed
        
        # JSON: Custom chunking (split array into chunks)
        elif file_type == "json":
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj["Body"]
            mode = (json_mode or "").lower()

            try:
                json_records: List[Any] = []
                json_row_index = 0

                for value in _iter_s3_json_values_from_body(body, packed=(mode == "packed")):
                    if mode == "packed":
                        json_records.append({"_DATA": value, "_ROW": json_row_index})
                        json_row_index += 1
                    else:
                        value = _unwrap_single_key_json_container(value)
                        if isinstance(value, list):
                            json_records.extend(value)
                        else:
                            json_records.append(value)

                    while len(json_records) >= chunk_rows:
                        chunk_data = json_records[:chunk_rows]
                        json_records = json_records[chunk_rows:]
                        if mode != "packed" and enforce_json_key_consistency and json_expected_keys_state is not None:
                            json_expected_keys_state["expected"] = _validate_json_record_keys(
                                chunk_data, json_expected_keys_state.get("expected")
                            )
                        chunk_df = pd.DataFrame(chunk_data)
                        if mode != "packed":
                            try:
                                assert_no_fully_empty_rows(chunk_df)
                            except Exception as e:
                                log(f"   JSON empty-row validation error: {e}")
                                raise
                        chunk_df = add_metadata_to_chunk(
                            chunk_df, key, last_modified, chunk_number + 1, total_rows_processed
                        )
                        chunk_number += 1
                        total_rows_processed += len(chunk_df)
                        yield chunk_df, chunk_number, False, last_modified, total_rows_processed

                if json_records:
                    if mode != "packed" and enforce_json_key_consistency and json_expected_keys_state is not None:
                        json_expected_keys_state["expected"] = _validate_json_record_keys(
                            json_records, json_expected_keys_state.get("expected")
                        )
                    chunk_df = pd.DataFrame(json_records)
                    if mode != "packed":
                        try:
                            assert_no_fully_empty_rows(chunk_df)
                        except Exception as e:
                            log(f"   JSON empty-row validation error: {e}")
                            raise
                    chunk_number += 1
                    chunk_df = add_metadata_to_chunk(
                        chunk_df, key, last_modified, chunk_number, total_rows_processed
                    )
                    total_rows_processed += len(chunk_df)
                    yield chunk_df, chunk_number, False, last_modified, total_rows_processed

                yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed
            except json.JSONDecodeError as e:
                log(f"   JSON parse error: {e}")

                raise ValueError(f"JSON parse error: {e}")
            except Exception as e:
                log(f"   JSON processing error: {type(e).__name__}: {e}")
                raise
            finally:
                body.close()

        # Binary formats use bounded range reads, never a whole-object BytesIO.
        elif file_type in ("parquet", "avro"):
            with io.BufferedReader(_S3RangeReader(s3, bucket, key, obj_meta),
                                   buffer_size=1024 * 1024) as stream:
                if file_type == "parquet":
                    parquet_file = pq.ParquetFile(stream)
                    frames = (batch.to_pandas() for batch in parquet_file.iter_batches(batch_size=chunk_rows))
                else:
                    def avro_frames():
                        records = []
                        for record in fastavro.reader(stream):
                            records.append(record)
                            if len(records) == chunk_rows:
                                yield pd.DataFrame(records)
                                records = []
                        if records:
                            yield pd.DataFrame(records)
                    frames = avro_frames()
                for chunk_df in frames:
                    assert_no_fully_empty_rows(chunk_df)
                    chunk_number += 1
                    chunk_df = add_metadata_to_chunk(
                        chunk_df, key, last_modified, chunk_number, total_rows_processed
                    )
                    total_rows_processed += len(chunk_df)
                    yield chunk_df, chunk_number, False, last_modified, total_rows_processed
            yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed

        # XML: Read records incrementally using iterparse (packed/unpacked)
        elif file_type == "xml":
            mode = (xml_mode or "packed").lower()
            if mode not in ("packed", "unpacked"):
                log(f"   XML mode '{xml_mode}' is not supported. Using 'packed' mode.")
                mode = "packed"
            
            obj = s3.get_object(Bucket=bucket, Key=key)
            content = obj["Body"].read()
            try:
                temp_root, normalized_content = _parse_xml_root(content, return_content=True)
                if temp_root is None:
                    yield pd.DataFrame(), 0, True, last_modified, 0
                    return
                record_elements = list(temp_root)
                if not record_elements:
                    # Single root element, use non-streaming parser
                    df = read_s3_file_to_df(
                        s3, bucket, key, file_type,
                        delimiter, quotechar, skipheaderlines, json_mode, xml_mode
                    )
                    if df is not None and not df.empty:
                        yield df, 1, True, last_modified, len(df)
                    else:
                        yield pd.DataFrame(), 0, True, last_modified, 0
                    return
                
                record_tag_local = record_elements[0].tag.split('}')[-1] if '}' in record_elements[0].tag else record_elements[0].tag
                expected_xml_keys = None
                
                xml_stream = io.BytesIO(normalized_content or content)
                context = ET.iterparse(xml_stream, events=('start', 'end'))
                context_iter = iter(context)
                event, root_elem = next(context_iter)
                
                chunk_records = []
                records_processed = 0
                current_depth = 0
                
                for event, elem in context_iter:
                    elem_tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                    
                    if event == 'start':
                        current_depth += 1
                    elif event == 'end':
                        if current_depth == 1 and elem_tag == record_tag_local:
                            records_processed += 1
                            if mode == "unpacked":
                                rec = parse_xml_record_unpacked(elem, records_processed - 1)
                                if enforce_row_schema_consistency:
                                    expected_xml_keys = _validate_json_record_keys(
                                        [rec], expected_xml_keys
                                    )
                            else:
                                rec = parse_xml_record_packed(elem, records_processed - 1)
                            chunk_records.append(rec)
                            
                            if len(chunk_records) >= chunk_rows:
                                chunk_number += 1
                                chunk_df = pd.DataFrame(chunk_records)
                                if mode == "unpacked" and not _xml_unpacked_has_business_data(chunk_df):
                                    raise XMLNoBusinessColumnsError("Update schema failed: no business columns in XML")
                                global_row_offset = total_rows_processed
                                chunk_df = add_metadata_to_chunk(
                                    chunk_df, key, last_modified, chunk_number, global_row_offset
                                )
                                total_rows_processed += len(chunk_df)
                                yield chunk_df, chunk_number, False, last_modified, total_rows_processed
                                chunk_records = []
                            elem.clear()
                        current_depth -= 1
                
                if chunk_records:
                    chunk_number += 1
                    chunk_df = pd.DataFrame(chunk_records)
                    if mode == "unpacked" and not _xml_unpacked_has_business_data(chunk_df):
                        raise XMLNoBusinessColumnsError("Update schema failed: no business columns in XML")
                    global_row_offset = total_rows_processed
                    chunk_df = add_metadata_to_chunk(
                        chunk_df, key, last_modified, chunk_number, global_row_offset
                    )
                    total_rows_processed += len(chunk_df)
                    yield chunk_df, chunk_number, False, last_modified, total_rows_processed
                
                yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed
            except Exception as e:
                log(f"   XML processing error: {type(e).__name__}: {e}")
                if isinstance(e, XMLNoBusinessColumnsError):
                    raise
                df = read_s3_file_to_df(
                    s3, bucket, key, file_type,
                    delimiter, quotechar, skipheaderlines, json_mode, xml_mode
                )
                if df is not None and not df.empty:
                    yield df, 1, True, last_modified, len(df)
                else:
                    yield pd.DataFrame(), 0, True, last_modified, 0
        
        else:
            df = read_s3_file_to_df(
                s3, bucket, key, file_type,
                delimiter, quotechar, skipheaderlines, json_mode, xml_mode
            )
            if df is not None and not df.empty:
                yield df, 1, True, last_modified, len(df)
            else:
                yield pd.DataFrame(), 0, True, last_modified, 0
    
    except ClientError as e:
        log(f"   S3 streaming error: {type(e).__name__}: {e}")
        raise
    except Exception as e:
        log(f"   Streaming error: {type(e).__name__}: {e}")
        raise
    finally:
        # Closing a partially consumed generator must also close its HTTP response.
        for stream_name in ("file_stream", "body"):
            stream = locals().get(stream_name)
            if stream is not None:
                stream.close()


# =============================================================================
# SCHEMA INFERENCE
# =============================================================================

def infer_columns_from_df(
    df: pd.DataFrame,
    load_method: str = "upsert_file_row",
    table_name: Optional[str] = None,
    internal_name: Optional[str] = None,
    custom_primary_key: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Infer column names and types from a sample DataFrame.
    Also marks primary key columns based on load_method.
    Includes ordinal_position to preserve column order when stored in PostgreSQL JSONB.
    Primary keys are always placed first in the ordinal order.
    
    Args:
        load_method: One of "upsert_file_row", "append_file_modified", "upsert_custom_key"
    """
    columns: Dict[str, Dict[str, Any]] = {}
    entries: List[Dict[str, Any]] = []

    log(f"[S3][INFER_COLUMNS] Load method: {load_method}")
    if load_method == "upsert_file_row":
        pk_cols = ["_FILE", "_ROW"]
        log(f"[S3][INFER_COLUMNS] Using primary keys: {pk_cols}")
    elif load_method == "append_file_modified":
        pk_cols = ["_FILE", "_ROW", "_MODIFIED"]
        log(f"[S3][INFER_COLUMNS] Using primary keys: {pk_cols}")
    elif load_method == "upsert_custom_key":
        log(
            f"[S3][INFER_COLUMNS] table_name={table_name}, "
            f"custom_primary_key={custom_primary_key}"
        )
        if not table_name:
            raise ValueError("table_name is required when load_method='upsert_custom_key'")
        if not custom_primary_key or not isinstance(custom_primary_key, dict):
            raise ValueError(
                f"Custom primary key is required for table '{table_name}' "
                "when using 'upsert_custom_key'"
            )

        pk_cols = None
        table_name_upper = table_name.upper() if table_name else None
        internal_name_upper = internal_name.upper() if internal_name else None
        
        search_keys = [k.upper() for k in custom_primary_key.keys()]
        log(
            f"[S3][INFER_COLUMNS] Searching PK for table '{table_name_upper}' "
            f"(internal: '{internal_name_upper}') in keys: {search_keys}"
        )
        
        # Priority 1: Exact match with mapped table name
        if table_name_upper:
            for key, cols in custom_primary_key.items():
                if key.upper() == table_name_upper:
                    pk_cols = [c.upper() for c in cols]
                    log(f"[S3][INFER_COLUMNS]  Using custom primary keys (mapped match): {pk_cols}")
                    break
        
        # Priority 2: Fallback match with internal name (initial derived name)
        if not pk_cols and internal_name_upper:
            for key, cols in custom_primary_key.items():
                if key.upper() == internal_name_upper:
                    pk_cols = [c.upper() for c in cols]
                    log(f"[S3][INFER_COLUMNS]  Using custom primary keys (internal fallback): {pk_cols}")
                    break
                    
        if not pk_cols:
            raise ValueError(
                f"No custom primary key defined for table '{table_name}'. "
                "User must select at least one column as primary key."
            )
    else:
        pk_cols = ["_FILE", "_ROW"]

    pk_cols_norm = [sanitize_column_name(c).upper() for c in pk_cols]
    pk_order_map = {name: i for i, name in enumerate(pk_cols_norm)}

    for col in df.columns:
        col_upper = col.upper()

        if col_upper == "_FILE":
            entries.append({"name": "_FILE", "norm": "_FILE", "data_type": "VARCHAR(1024)", "is_primary_key": "_FILE" in pk_cols_norm})
            continue
        if col_upper == "_ROW":
            row_type = CUSTOM_PK_COLUMN_TYPE if (load_method == "upsert_custom_key" and "_ROW" in pk_cols_norm) else "NUMBER(38,0)"
            entries.append({"name": "_ROW", "norm": "_ROW", "data_type": row_type, "is_primary_key": "_ROW" in pk_cols_norm})
            continue
        if col_upper == "_SPECT_SYNCED":
            continue
        if col_upper == "_SPECT_DELETED":
            continue
        if col_upper == "_MODIFIED":
            entries.append({"name": "_MODIFIED", "norm": "_MODIFIED", "data_type": "TIMESTAMP_TZ", "is_primary_key": "_MODIFIED" in pk_cols_norm})
            continue
        if col_upper == "_DATA":
            entries.append({"name": "_DATA", "norm": "_DATA", "data_type": "VARIANT", "is_primary_key": False})
            continue

        col_norm = sanitize_column_name(col).upper()
        non_null = df[col].dropna()
        if len(non_null) > 0 and isinstance(non_null.iloc[0], (dict, list)):
            sf_type = "VARIANT"
        else:
            sf_type = detect_column_type_from_values(df, col)
        if load_method == "upsert_custom_key" and col_norm in pk_cols_norm:
            # Force custom PK columns to VARCHAR instead of inferred numeric types.
            sf_type = CUSTOM_PK_COLUMN_TYPE

        entries.append({
            "name": col,  # preserve source casing for update-schema UI
            "norm": col_norm,
            "data_type": sf_type,
            "is_primary_key": col_norm in pk_cols_norm,
        })

    pk_entries = [e for e in entries if e["is_primary_key"]]
    non_pk_entries = [e for e in entries if not e["is_primary_key"]]
    pk_entries.sort(key=lambda e: pk_order_map.get(e["norm"], 10_000))

    ordinal = 0
    for entry in pk_entries + non_pk_entries:
        columns[entry["name"]] = {
            "data_type": entry["data_type"],
            "is_primary_key": entry["is_primary_key"],
            "ordinal_position": ordinal,
        }
        if entry["is_primary_key"]:
            log(f"[S3][INFER_COLUMNS]  PK column '{entry['name']}' assigned ordinal {ordinal}")
        ordinal += 1

    log(f"[S3][INFER_COLUMNS] Total columns: {len(columns)} (PKs: {len(pk_entries)}, Non-PKs: {len(non_pk_entries)})")
    return columns


# =============================================================================
# LIST COLUMNS IN PK SUGGESTIONS 
# =============================================================================
def extract_columns_for_pk_suggestion(
    df: pd.DataFrame
) -> Dict[str, Dict[str, Any]]:
    """
    Extract columns for PRIMARY KEY SUGGESTION.

    Rules:
    - _FILE and _ROW are auto-selected as default primary keys
    - _SPECT_SYNCED/_SPECT_DELETED are hidden from selection
    - User can modify selections as needed
    - System columns are exposed but not enforced
    """

    columns: Dict[str, Dict[str, Any]] = {}

    ALLOWED_SYSTEM_COLUMNS = {"_FILE", "_ROW", "_MODIFIED"}
    HIDDEN_COLUMNS = {"_DATA", "_SPECT_SYNCED", "_SPECT_DELETED"}
    DEFAULT_SELECTED_PK = {"_FILE", "_ROW"}

    ordinal = 0

    def _col_upper(value: Any) -> str:
        return str(value).strip().upper()

    # Ensure system columns appear first in a fixed order, if present in file
    system_order = ["_FILE", "_ROW", "_MODIFIED"]
    system_present = []
    remaining_cols = []

    for col in df.columns:
        col_upper = _col_upper(col)
        if col_upper in system_order:
            if col_upper not in system_present:
                system_present.append(col_upper)
            continue
        remaining_cols.append(col)

    for col_upper in system_order:
        if col_upper not in system_present:
            continue

        if col_upper == "_FILE":
            data_type = "VARCHAR(1024)"
        elif col_upper == "_ROW":
            data_type = "NUMBER(38,0)"
        elif col_upper == "_MODIFIED":
            data_type = "TIMESTAMP_TZ"
        else:
            data_type = "TIMESTAMP_TZ"

        columns[col_upper] = {
            "data_type": data_type,
            "is_system": True,
            "is_selected": col_upper in DEFAULT_SELECTED_PK,
            "ordinal_position": ordinal,
        }
        ordinal += 1

    for col in remaining_cols:
        col_upper = _col_upper(col)

        #  Skip hidden internal columns
        if col_upper in HIDDEN_COLUMNS:
            continue

        # ---- Normal user column ----
        # Use Snowflake-safe canonical naming consistently across S3 service flows.
        col_name = sanitize_column_name(str(col))

        non_null = df[col].dropna()
        if not non_null.empty:
            v = non_null.iloc[0]
            if isinstance(v, (dict, list, np.ndarray)):
                sf_type = "VARIANT"
            else:
                sf_type = detect_column_type_from_values(df, col)
        else:
            sf_type = "VARCHAR"

        columns[col_name] = {
            "data_type": sf_type,
            "is_system": False,
            "ordinal_position": ordinal,
        }
        ordinal += 1

    return columns

# =============================================================================
# FILE STRUCTURE VALIDATION AND NEW FILE DISCOVERY
# =============================================================================

def infer_columns_from_s3_sample(
    s3, s3_bucket: str, file_key: str, file_type: str,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Dict[str, Any]]], Optional[str]]:
    """
    Read a small sample of the file and infer columns.
    Returns (columns, error_message).
    """
    df_sample = None
    try:
        for chunk_df, chunk_num, is_last_chunk, last_mod, total_rows_chunk in read_s3_file_in_chunks(
            s3, s3_bucket, file_key, file_type,
            chunk_size=100,
            delimiter=delimiter,
            quotechar=quotechar,
            skipheaderlines=skipheaderlines,
            json_mode=json_mode,
            xml_mode=xml_mode,
            escape_char=escape_char,
            null_sequence=null_sequence,
            skip_footer_lines=skip_footer_lines,
            line_separator=line_separator
        ):
            if chunk_df is not None and not chunk_df.empty:
                df_sample = chunk_df
                break
    except Exception as e:
        return None, f"Error reading file sample: {str(e)}"

    if df_sample is None or df_sample.empty:
        return None, "File is empty or could not be read"

    columns = infer_columns_from_df(df_sample, load_method="upsert_file_row")
    if not isinstance(columns, dict) or not columns:
        return None, "Could not infer columns from sample"
    return columns, None


def compute_common_new_columns_for_multi_files(
    s3, s3_bucket: str, keys: List[str], file_type: str,
    expected_columns: Dict[str, Dict[str, Any]],
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Set[str]:
    """
    For multi-files-single-table mode:
    Only allow new columns if they appear in ALL files in the batch.
    Returns a set of allowed new columns (normalized uppercase).
    """
    if not isinstance(expected_columns, dict) or not expected_columns:
        return set()

    expected_col_names = {sanitize_column_name(k).upper() for k in expected_columns.keys()}
    metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
    expected_col_names = expected_col_names - metadata_cols

    common_cols = None
    for key in keys:
        actual_columns, err = infer_columns_from_s3_sample(
            s3, s3_bucket, key, file_type,
            delimiter=delimiter,
            quotechar=quotechar,
            skipheaderlines=skipheaderlines,
            json_mode=json_mode,
            xml_mode=xml_mode,
            escape_char=escape_char,
            null_sequence=null_sequence,
            skip_footer_lines=skip_footer_lines,
            line_separator=line_separator,
        )
        if actual_columns is None:
            log(f"[VALIDATE]  Unable to infer columns for '{key}': {err}")
            return set()

        actual_col_names = {sanitize_column_name(k).upper() for k in actual_columns.keys()}
        actual_col_names = actual_col_names - metadata_cols

        common_cols = actual_col_names if common_cols is None else (common_cols & actual_col_names)
        if not common_cols:
            return set()

    if common_cols is None:
        return set()

    new_cols = common_cols - expected_col_names
    if new_cols:
        log(f"[VALIDATE]  New columns present in ALL files: {new_cols}")
    return new_cols

def validate_file_structure_matches(
    s3, s3_bucket: str, file_key: str, file_type: str,
    expected_columns: Dict[str, Dict[str, Any]],
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
    enforce_type_compatibility: bool = False,
    allowed_new_columns: Optional[Set[str]] = None,
) -> Tuple[bool, Dict[str, Dict[str, Any]], Optional[str]]:
    """
    Validate that a file matches the expected structure (columns and file type).
    
    This function reads a sample of the file, infers its columns, and compares
    them with the expected structure from the existing mapping.
    
    Args:
        s3: S3 client
        s3_bucket: S3 bucket name
        file_key: File key to validate
        file_type: Expected file type
        expected_columns: Expected column structure from existing mapping
        ...: File parsing parameters
    
    Returns:
        Tuple of (is_valid, actual_columns, error_message)
        - is_valid: True if file structure matches (all expected columns present)
        - actual_columns: Inferred columns from file
        - error_message: Error description if validation fails
    """
    log(f"[VALIDATE] Validating file structure for '{file_key}'")
    
    try:
        # Read a sample of the file to infer columns
        df_sample = None
        for chunk_df, chunk_num, is_last_chunk, last_mod, total_rows_chunk in read_s3_file_in_chunks(
            s3, s3_bucket, file_key, file_type,
            chunk_size=100,  # Small sample for validation
            delimiter=delimiter,
            quotechar=quotechar,
            skipheaderlines=skipheaderlines,
            json_mode=json_mode,
            xml_mode=xml_mode,
            escape_char=escape_char,
            null_sequence=null_sequence,
            skip_footer_lines=skip_footer_lines,
            line_separator=line_separator
        ):
            if chunk_df is not None and not chunk_df.empty:
                df_sample = chunk_df
                break
        
        if df_sample is None or df_sample.empty:
            return False, {}, f"File '{file_key}' is empty or could not be read"
        
        # Infer columns from sample
        actual_columns = infer_columns_from_df(df_sample, load_method="upsert_file_row")
        
        # Normalize column names for comparison (uppercase, sanitized)
        expected_col_names = {sanitize_column_name(k).upper() for k in expected_columns.keys()}
        actual_col_names = {sanitize_column_name(k).upper() for k in actual_columns.keys()}
        
        # Exclude metadata columns from comparison (they're added automatically)
        metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
        expected_col_names = expected_col_names - metadata_cols
        actual_col_names = actual_col_names - metadata_cols
        
        # Check if all expected columns exist in actual columns
        missing_cols = expected_col_names - actual_col_names
        if missing_cols:
            return False, actual_columns, f"File '{file_key}' is missing required columns: {missing_cols}"
        
        # Check if there are new columns (this is OK, will be handled by schema update)
        new_cols = actual_col_names - expected_col_names
        if new_cols:
            if allowed_new_columns is not None:
                disallowed_new = new_cols - allowed_new_columns
                if disallowed_new:
                    return (
                        False,
                        actual_columns,
                        f"File '{file_key}' has unexpected new columns: {disallowed_new}",
                    )
            log(f"[VALIDATE]  File '{file_key}' has new columns: {new_cols} (will be added to schema)")
        
        if enforce_type_compatibility:
            def _sf_type_family(sf_type: Optional[str]) -> str:
                t = str(sf_type or "").strip().upper()
                if not t:
                    return "unknown"
                if t.startswith("NUMBER") or t.startswith("DECIMAL") or t.startswith("NUMERIC") or t.startswith("INT") or t.startswith("BIGINT") or t.startswith("FLOAT") or t.startswith("DOUBLE"):
                    return "numeric"
                if t.startswith("TIMESTAMP"):
                    return "timestamp"
                if t == "DATE":
                    return "date"
                if t.startswith("BOOLEAN"):
                    return "boolean"
                if t.startswith("VARIANT"):
                    return "variant"
                if t.startswith("VARCHAR") or t.startswith("CHAR") or t.startswith("TEXT") or t.startswith("STRING"):
                    return "text"
                return "unknown"

            expected_by_norm = {
                sanitize_column_name(k).upper(): (v or {}).get("data_type")
                for k, v in (expected_columns or {}).items()
            }
            actual_by_norm = {
                sanitize_column_name(k).upper(): (v or {}).get("data_type")
                for k, v in (actual_columns or {}).items()
            }
            metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
            incompatible = []
            for col_norm, expected_type in expected_by_norm.items():
                if col_norm in metadata_cols:
                    continue
                actual_type = actual_by_norm.get(col_norm)
                if actual_type is None:
                    continue
                e_family = _sf_type_family(expected_type)
                a_family = _sf_type_family(actual_type)

                # Compatibility matrix:
                # - text/variant/unknown expected are permissive
                # - numeric only accepts numeric
                # - date/timestamp accept temporal interchange
                # - boolean only accepts boolean
                if e_family in {"text", "variant", "unknown"}:
                    continue
                if e_family == "numeric" and a_family != "numeric":
                    incompatible.append((col_norm, expected_type, actual_type))
                    continue
                if e_family in {"date", "timestamp"} and a_family not in {"date", "timestamp"}:
                    incompatible.append((col_norm, expected_type, actual_type))
                    continue
                if e_family == "boolean" and a_family != "boolean":
                    incompatible.append((col_norm, expected_type, actual_type))
                    continue

            if incompatible:
                preview = ", ".join(
                    [f"{c} expected {e} got {a}" for c, e, a in incompatible[:5]]
                )
                return (
                    False,
                    actual_columns,
                    "Type mismatch with mapped table schema. "
                    f"{preview}. This usually indicates different file structure, delimiter, or header options."
                )

        log(f"[VALIDATE]  File '{file_key}' structure matches (expected: {len(expected_col_names)}, actual: {len(actual_col_names)}, new: {len(new_cols)})")
        return True, actual_columns, None
        
    except Exception as e:
        error_msg = f"Error validating file '{file_key}': {str(e)}"
        log(f"[VALIDATE]  {error_msg}")
        return False, {}, error_msg


def merge_schema_columns(
    existing_columns: Dict[str, Dict[str, Any]],
    new_columns: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """
    Merge new columns into existing schema, preserving existing column definitions.
    
    When a new file has additional columns, this function merges them into the
    existing schema. Existing column definitions are preserved to maintain consistency.
    
    Args:
        existing_columns: Current column schema from SourceTableSelection
        new_columns: New columns discovered from a new file
    
    Returns:
        Merged column schema with all columns (existing + new)
    """
    merged = existing_columns.copy()

    # Case-insensitive lookup prevents duplicates like "emp_id" + "EMP_ID".
    existing_key_by_upper = {
        sanitize_column_name(existing_key).upper(): existing_key
        for existing_key in merged.keys()
    }

    for col_name, col_meta in new_columns.items():
        col_name_upper = sanitize_column_name(col_name).upper()

        # Skip metadata columns (added automatically during processing)
        if col_name_upper in {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}:
            continue

        existing_key = existing_key_by_upper.get(col_name_upper)

        # Add only if logical column doesn't exist (case-insensitive)
        if not existing_key:
            merged[col_name_upper] = col_meta
            existing_key_by_upper[col_name_upper] = col_name_upper
            log(f"[SCHEMA_MERGE] Added new column '{col_name_upper}': {col_meta}")
        else:
            # Preserve existing definition but log type mismatch
            existing_type = merged[existing_key].get("data_type", "")
            new_type = col_meta.get("data_type", "")
            if existing_type != new_type:
                log(
                    f"[SCHEMA_MERGE] Column '{col_name_upper}' type mismatch: "
                    f"existing={existing_type}, new={new_type} (keeping existing)"
                )

    return merged


def validate_existing_files_for_schema_changes(
    s3, s3_bucket: str, file_type: str,
    existing_file_keys: List[str],
    expected_columns: Dict[str, Dict[str, Any]],
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Validate existing files for schema changes (new columns added).
    
    This function checks if any existing files have been updated with new columns.
    It validates all existing files to ensure they still match the expected structure
    and detects any new columns that need to be added to the schema.
    
    Args:
        s3: S3 client
        s3_bucket: S3 bucket name
        file_type: File type filter
        existing_file_keys: List of already mapped file keys to validate
        expected_columns: Expected column structure from existing mapping
        ...: File parsing parameters
    
    Returns:
        Tuple of (updated_schema, files_with_changes)
        - updated_schema: Merged schema with any new columns found in existing files
        - files_with_changes: List of file keys that have new columns
    """
    log(f"[VALIDATE_EXISTING] Validating {len(existing_file_keys)} existing files for schema changes")
    log(f"[VALIDATE_EXISTING] Expected columns: {list(expected_columns.keys())}")
    
    if not existing_file_keys:
        log(f"[VALIDATE_EXISTING] No existing files to validate")
        return expected_columns, []
    
    updated_schema = expected_columns.copy()
    files_with_changes = []
    
    for file_key in existing_file_keys:
        try:
            is_valid, actual_columns, error_msg = validate_file_structure_matches(
                s3, s3_bucket, file_key, file_type,
                expected_columns,
                delimiter=delimiter,
                quotechar=quotechar,
                skipheaderlines=skipheaderlines,
                json_mode=json_mode,
                xml_mode=xml_mode,
                escape_char=escape_char,
                null_sequence=null_sequence,
                skip_footer_lines=skip_footer_lines,
                line_separator=line_separator,
                enforce_type_compatibility=True,
            )
            
            if is_valid:
                # Check if file has new columns
                expected_col_names = {sanitize_column_name(k).upper() for k in expected_columns.keys()}
                actual_col_names = {sanitize_column_name(k).upper() for k in actual_columns.keys()}
                metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
                expected_col_names = expected_col_names - metadata_cols
                actual_col_names = actual_col_names - metadata_cols
                
                new_cols = actual_col_names - expected_col_names
                if new_cols:
                    log(f"[VALIDATE_EXISTING]  File '{file_key}' has new columns: {new_cols}")
                    files_with_changes.append(file_key)
                    # Merge new columns into schema
                    updated_schema = merge_schema_columns(updated_schema, actual_columns)
                else:
                    log(f"[VALIDATE_EXISTING]  File '{file_key}' structure unchanged")
            else:
                log(f"[VALIDATE_EXISTING]  File '{file_key}' validation failed: {error_msg}")
                # File structure changed (missing columns) - this is a problem
                # We'll log it but continue processing other files
                log(f"[VALIDATE_EXISTING]  WARNING: Existing file '{file_key}' no longer matches expected structure!")
        except Exception as e:
            log(f"[VALIDATE_EXISTING]  Error validating existing file '{file_key}': {e}")
            # Continue with other files even if one fails
    
    if updated_schema != expected_columns:
        new_cols = set(updated_schema.keys()) - set(expected_columns.keys())
        log(f"[VALIDATE_EXISTING]  Schema changes detected: {len(new_cols)} new columns found in {len(files_with_changes)} file(s)")
        log(f"[VALIDATE_EXISTING] New columns: {new_cols}")
    else:
        log(f"[VALIDATE_EXISTING]  No schema changes detected in existing files")
    
    return updated_schema, files_with_changes


def discover_new_matching_files(
    s3, s3_bucket: str, s3_base_prefix: str, file_type: str,
    existing_file_keys: List[str],
    expected_columns: Dict[str, Dict[str, Any]],
    file_mapping_mode: str,
    include_subfolders: bool = False,
    pattern: Optional[str] = None,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """
    Discover new files in S3 that match the existing structure for multi-file single table mapping.
    
    This function:
    1. Lists all files in S3 under base prefix matching the file type
    2. Filters by regex pattern if provided
    3. Filters out already-mapped files
    4. Validates each candidate file's structure matches the expected schema
    5. Merges any new columns found into the schema
    6. Returns new matching files and updated schema
    
    Args:
        s3: S3 client
        s3_bucket: S3 bucket name
        s3_base_prefix: Base prefix/folder to search
        file_type: File type filter (csv, json, parquet, etc.)
        existing_file_keys: List of already mapped file keys
        expected_columns: Expected column structure from existing mapping
        file_mapping_mode: File mapping mode (must be "multi_files_single_table")
        pattern: Optional regex pattern to filter files (multi_files_prefix)
        ...: File parsing parameters
    
    Returns:
        Tuple of (new_file_keys, updated_schema)
        - new_file_keys: List of new file keys that match structure
        - updated_schema: Merged schema with any new columns found
    """
    log(f"[DISCOVER] Discovering new matching files for multi-file single table mapping")
    log(f"[DISCOVER] Base folder: {s3_base_prefix or '(root)'}")
    log(
        f"[DISCOVER]  Listing scope: "
        f"{'recursive (include subfolders)' if include_subfolders else 'base-folder only (no subfolders)'}"
    )
    log(f"[DISCOVER] Pattern: {pattern or '(none)'}")
    log(f"[DISCOVER] Existing files: {len(existing_file_keys)}")
    log(f"[DISCOVER] Expected columns: {list(expected_columns.keys())}")
    
    if file_mapping_mode != "multi_files_single_table":
        log(f"[DISCOVER]  Not in multi-file mode, skipping discovery")
        return [], expected_columns
    
    # Respect include_subfolders in migration discovery, same as preview/list APIs.
    prefix = normalize_s3_folder_prefix(s3_base_prefix)
    all_keys = _list_s3_keys_by_scope(
        s3=s3,
        s3_bucket=s3_bucket,
        s3_folder_prefix=prefix,
        include_subfolders=include_subfolders,
    )
    
    if pattern:
        log(f"[DISCOVER]  multi_files_prefix is deprecated and ignored (pattern='{pattern}')")
    filtered_keys = all_keys
    
    # Filter out existing files
    existing_set = set(existing_file_keys)
    candidate_keys = [k for k in filtered_keys if k not in existing_set]
    if file_type:
        compatible_keys = []
        incompatible = []
        for k in candidate_keys:
            ok, reason = is_file_compatible_with_type(
                s3=s3,
                bucket=s3_bucket,
                key=k,
                file_type=file_type,
                delimiter=delimiter,
                quotechar=quotechar,
                skipheaderlines=skipheaderlines,
                escape_char=escape_char,
                skip_footer_lines=skip_footer_lines,
                line_separator=line_separator,
            )
            if ok:
                compatible_keys.append(k)
            else:
                incompatible.append((k, reason or "incompatible"))
        if incompatible:
            log(f"[DISCOVER] Skipping {len(incompatible)} incompatible file(s) based on content check")
            for k, reason in incompatible[:20]:
                log(f"[DISCOVER]   - {k}: {reason}")
        candidate_keys = compatible_keys
    
    log(f"[DISCOVER] Found {len(candidate_keys)} candidate files (excluding {len(existing_file_keys)} existing)")
    
    if not candidate_keys:
        log(f"[DISCOVER] No new files to process")
        return [], expected_columns
    
    # Validate each candidate file
    new_file_keys = []
    updated_schema = expected_columns.copy()
    
    for file_key in candidate_keys:
        is_valid, actual_columns, error_msg = validate_file_structure_matches(
            s3, s3_bucket, file_key, file_type,
            expected_columns,
            delimiter=delimiter,
            quotechar=quotechar,
            skipheaderlines=skipheaderlines,
            json_mode=json_mode,
            xml_mode=xml_mode,
            escape_char=escape_char,
            null_sequence=null_sequence,
            skip_footer_lines=skip_footer_lines,
            line_separator=line_separator,
            enforce_type_compatibility=True,
        )
        
        if is_valid:
            new_file_keys.append(file_key)
            # Merge any new columns found
            updated_schema = merge_schema_columns(updated_schema, actual_columns)
            log(f"[DISCOVER]  File '{file_key}' matches structure - added to mapping")
        else:
            log(f"[DISCOVER]  File '{file_key}' does not match structure: {error_msg}")
    
    log(f"[DISCOVER]  Discovery complete: {len(new_file_keys)} new matching files found")
    if updated_schema != expected_columns:
        new_cols = set(updated_schema.keys()) - set(expected_columns.keys())
        log(f"[DISCOVER]  Schema updated: {len(new_cols)} new columns added: {new_cols}")
    
    return new_file_keys, updated_schema

# =============================================================================
# SNOWFLAKE DDL/DML
# =============================================================================

def ensure_tables(
    conn,
    table: str,
    df: pd.DataFrame,
    load_method: str,
    user_pk: List[str],
    sf_database: str = None,
    sf_schema: str = None,
    staging_table_name: Optional[str] = None,
    schema_override: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """
    Ensure main + staging tables exist with correct schema.
    
    Args:
        load_method: One of "upsert_file_row", "append_file_modified", "upsert_custom_key"
    """
    if not sf_database:
        raise ValueError("sf_database is required")
    if not sf_schema:
        raise ValueError("sf_schema is required")
    
    database = sf_database
    schema = sf_schema
    
    log(f"[SF] Ensuring tables for {table}")
    
    table_u = table.upper()
    stg_u = sanitize_identifier(staging_table_name) if staging_table_name else staging_name(table_u)
    
    # Store staging table name for return and cleanup tracking
    staging_table_name = stg_u
    
    main_table = f'{Q(database)}.{Q(schema)}.{Q(table_u)}'
    stg_table = f'{Q(database)}.{Q(schema)}.{Q(stg_u)}'
    
    desired_cols: Dict[str, str] = {}
    
    schema_override_upper = {}
    if isinstance(schema_override, dict):
        for k, v in schema_override.items():
            key_u = sanitize_column_name(k).upper()
            if key_u:
                schema_override_upper[key_u] = v

    for col in df.columns:
        col_upper = col.upper()
        
        if col_upper == "_FILE":
            desired_cols["_FILE"] = "VARCHAR(1024) NOT NULL"
            continue
        if col_upper == "_ROW":
            desired_cols["_ROW"] = "NUMBER(38,0) NOT NULL"
            continue
        if col_upper in ("_MODIFIED", "_SPECT_SYNCED"):
            desired_cols[col_upper] = "TIMESTAMP_TZ"
            continue
        if col_upper == "_SPECT_DELETED":
            desired_cols[col_upper] = "BOOLEAN"
            continue
        if col_upper == "_DATA":
            desired_cols["_DATA"] = "VARIANT"
            continue
        
        col_u = sanitize_column_name(col)

        override_meta = schema_override_upper.get(col_u.upper())
        override_type = None
        if isinstance(override_meta, dict):
            override_type = override_meta.get("data_type")
        if override_type:
            desired_cols[col_u] = override_type
            log(
                f"[S3][ENSURE_TABLES][TYPE_INFER] {table_u}.{col_u}: "
                f"override_type={override_type} (multi-file merged schema)"
            )
            continue
        
        non_null = df[col].dropna()
        sample_values = []
        try:
            if len(non_null) > 0:
                sample_values = non_null.astype(str).head(5).tolist()
        except Exception:
            sample_values = []
        if len(non_null) > 0:
            v = non_null.iloc[0]
            if isinstance(v, (dict, list, np.ndarray)):
                desired_cols[col_u] = "VARIANT"
                log(
                    f"[S3][ENSURE_TABLES][TYPE_INFER] {table_u}.{col_u}: "
                    f"sample={sample_values} -> VARIANT (dict/list detected)"
                )
                continue
        
        sf_type = detect_column_type_from_values(df, col)
        desired_cols[col_u] = sf_type
        try:
            inferred_dtype = str(df[col].dtype)
        except Exception:
            inferred_dtype = "unknown"
        log(
            f"[S3][ENSURE_TABLES][TYPE_INFER] {table_u}.{col_u}: "
            f"pandas_dtype={inferred_dtype}, sample={sample_values} -> {sf_type}"
        )

    # Add any missing columns from schema_override (multi-file merged schema)
    for col_u, meta in schema_override_upper.items():
        if col_u in desired_cols:
            continue
        if col_u in {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}:
            continue
        override_type = None
        if isinstance(meta, dict):
            override_type = meta.get("data_type")
        if override_type:
            desired_cols[col_u] = override_type
            log(
                f"[S3][ENSURE_TABLES][TYPE_INFER] {table_u}.{col_u}: "
                f"override_type={override_type} (missing from current chunk)"
            )
    
    # Determine primary key columns based on load_method
    log(f"[S3][ENSURE_TABLES] Load method: {load_method}")
    if load_method == "upsert_file_row":
        pk_cols = ["_FILE", "_ROW"]
        log(f"[S3][ENSURE_TABLES] Using primary keys: {pk_cols}")
    elif load_method == "append_file_modified":
        pk_cols = ["_FILE", "_ROW", "_MODIFIED"]
        log(f"[S3][ENSURE_TABLES] Using primary keys: {pk_cols}")
    elif load_method == "upsert_custom_key":
        pk_cols = sanitize_custom_pk_columns(user_pk)
        if not pk_cols:
            pk_cols = ["_FILE", "_ROW"]
            log(f"[S3][ENSURE_TABLES]  No custom PK provided, using default: {pk_cols}")
        else:
            log(f"[S3][ENSURE_TABLES] Using custom primary keys: {pk_cols}")
        if "_FILE" not in desired_cols:
            desired_cols["_FILE"] = "VARCHAR(1024)"
        if "_ROW" not in desired_cols:
            desired_cols["_ROW"] = "NUMBER(38,0)"
        # Custom PK columns should be VARCHAR for stable key matching.
        for pk_col in pk_cols:
            if pk_col in desired_cols and pk_col != "_FILE":
                desired_cols[pk_col] = CUSTOM_PK_COLUMN_TYPE
    else:
        raise ValueError(f"Invalid load method: {load_method}. Must be one of: 'upsert_file_row', 'append_file_modified', 'upsert_custom_key'")
    
    pk_cols_quoted = [Q(c) for c in pk_cols]
    pk_clause = f"PRIMARY KEY ({', '.join(pk_cols_quoted)}) RELY"

    metadata_prefix = ["_FILE", "_ROW", "_MODIFIED"]
    metadata_tail = ["_SPECT_SYNCED", "_SPECT_DELETED"]
    ordered_cols = []

    # 1) Metadata prefix columns first
    for c in metadata_prefix:
        if c in desired_cols and c not in ordered_cols:
            ordered_cols.append(c)

    # 2) PK columns next (in configured order, excluding prefix)
    for c in pk_cols:
        if c in desired_cols and c not in ordered_cols:
            ordered_cols.append(c)

    # 3) Regular non-PK, non-tail columns
    for c in desired_cols.keys():
        if c in ordered_cols:
            continue
        if c in metadata_tail:
            continue
        ordered_cols.append(c)

    # 4) Metadata tail columns last (unless already included as PK)
    for c in metadata_tail:
        if c in desired_cols and c not in ordered_cols:
            ordered_cols.append(c)

    col_defs = ", ".join(f'"{c}" {desired_cols[c]}' for c in ordered_cols)
    col_defs = f"{col_defs}, {pk_clause}"
    
    ddl_main = f"CREATE TABLE IF NOT EXISTS {main_table} ({col_defs})"
    ddl_stg = f"CREATE TABLE IF NOT EXISTS {stg_table} ({col_defs})"
    
    with conn.cursor() as cur:
        cur.execute(ddl_main)
        cur.execute(ddl_stg)
    
    def get_existing_cols(tbl):
        sql = f"""
            SELECT COLUMN_NAME
            FROM {Q(database)}.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema}'
              AND TABLE_NAME   = '{tbl}'
        """
        with conn.cursor() as c:
            c.execute(sql)
            return {r[0].upper() for r in c.fetchall()}

    def get_existing_varchar_length(tbl, col_name):
        sql = f"""
            SELECT CHARACTER_MAXIMUM_LENGTH
            FROM {Q(database)}.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema}'
              AND TABLE_NAME   = '{tbl}'
              AND COLUMN_NAME  = '{col_name}'
            LIMIT 1
        """
        with conn.cursor() as c:
            c.execute(sql)
            row = c.fetchone()
            if not row:
                return None
            try:
                return int(row[0]) if row[0] is not None else None
            except Exception:
                return None
    
    existing_main = get_existing_cols(table_u)
    existing_stg = get_existing_cols(stg_u)
    
    for col_u, col_type in desired_cols.items():
        if col_u not in existing_main:
            sql = f'ALTER TABLE {main_table} ADD COLUMN "{col_u}" {col_type}'
            with conn.cursor() as cur:
                cur.execute(sql)
        if col_u not in existing_stg:
            sql = f'ALTER TABLE {stg_table} ADD COLUMN "{col_u}" {col_type}'
            with conn.cursor() as cur:
                cur.execute(sql)

    # Widen existing VARCHAR columns when incoming data needs more space (Dynamics-style).
    for col_u, col_type in desired_cols.items():
        min_len = 1024 if col_u == "_FILE" else None
        target_len = compute_varchar_widen_target(
            df,
            col_u,
            col_type,
            min_length=min_len,
        )
        if target_len is None:
            continue
        for tbl_name, qualified, existing_cols in (
            (table_u, main_table, existing_main),
            (stg_u, stg_table, existing_stg),
        ):
            if col_u not in existing_cols:
                continue
            existing_len = get_existing_varchar_length(tbl_name, col_u)
            if existing_len is not None and existing_len < target_len:
                with conn.cursor() as cur:
                    cur.execute(
                        f'ALTER TABLE {qualified} ALTER COLUMN "{col_u}" '
                        f'SET DATA TYPE VARCHAR({target_len})'
                    )
                log(
                    f"[SF] Widened {tbl_name}.\"{col_u}\" "
                    f"VARCHAR({existing_len}) -> VARCHAR({target_len})"
                )

    log(f"   Tables ensured (load_method = {load_method}, pk={pk_cols})")
    
    # Return the staging table name for tracking
    return staging_table_name

def _staging_json_default(value):
    """Serialize typed values found inside Avro/Parquet records."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Unsupported nested value for S3 staging: {type(value).__name__}")


def upload_chunk_to_gcs(
    gcs: storage.Client,
    df: pd.DataFrame,
    table: str,
    chunk_number: int,
    gcs_bucket_name: str = None,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    file_type: str = "csv"
) -> str:
    """Upload a single chunk DataFrame to GCS as CSV."""
    df = df.copy()
    bucket_name = gcs_bucket_name or GCS_BUCKET_NAME
    
    bucket = gcs.bucket(bucket_name)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    # Root level upload (no prefix) - matching other services
    object_path = gcs_join(table, f"chunk_{chunk_number:04d}_{ts}_{uuid4().hex}.csv")
    
    for c in df.columns:
        series = df[c]
        col_u = c.upper()
        if col_u == "_DATA":
            df[c] = series.apply(lambda v: json.dumps(v, default=_staging_json_default) if v is not None else None)
            continue
        if series.dropna().size > 0:
            sample = series.dropna().iloc[0]
            if isinstance(sample, (dict, list, np.ndarray)):
                df[c] = series.apply(lambda v: json.dumps(v, default=_staging_json_default) if v is not None else None)
    
    buf = io.StringIO()
    delim = normalize_delimiter(delimiter, file_type)
    qchar_norm = normalize_quotechar(quotechar)
    quote_disabled = qchar_norm == ""
    qchar = qchar_norm if not quote_disabled else '"'
    
    quoting = csv.QUOTE_NONE if quote_disabled else csv.QUOTE_MINIMAL
    escapechar = "\\" if quote_disabled else None
    
    df.to_csv(
        buf,
        index=False,
        sep=delim,
        quotechar=qchar,
        quoting=quoting,
        lineterminator="\n",
        escapechar=escapechar
    )
    data = buf.getvalue().encode("utf-8")
    
    blob = bucket.blob(object_path)
    blob.upload_from_string(data, content_type="text/csv")
    return object_path

def copy_into_staging_from_gcs_batch(
    conn,
    gcs_object_paths: List[str],
    staging_table: str,
    sf_database: str = None,
    sf_schema: str = None,
    sf_storage_integration: str = None,
    gcs_bucket_name: str = None,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    file_type: str = "csv",
    # Progress tracking parameters
    connection_id: int = None,
    migration_session_id: str = None,
    job_name: str = None,
    table_name: str = None
):
    """Copy multiple GCS files into Snowflake staging table."""
    if not sf_database:
        raise ValueError("sf_database is required")
    if not sf_schema:
        raise ValueError("sf_schema is required")
    
    database = sf_database
    schema = sf_schema
    storage_integration = sf_storage_integration or SF_STORAGE_INTEGRATION
    bucket_name = gcs_bucket_name or GCS_BUCKET_NAME
    
    if not gcs_object_paths:
        return
    
    file_format_sql = build_snowflake_file_format(delimiter, quotechar, skipheaderlines, file_type)
    
    sql_cols = f"""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM {Q(database)}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{schema.upper()}'
          AND TABLE_NAME   = '{staging_table.upper()}'
        ORDER BY ORDINAL_POSITION
    """
    with conn.cursor() as cur:
        cur.execute(sql_cols)
        rows = cur.fetchall()
    
    if not rows:
        raise RuntimeError(f"No columns found for staging table {database}.{schema}.{staging_table}")
    
    col_names = [r[0] for r in rows]
    target_cols_sql = ", ".join(Q(c) for c in col_names)
    needs_json_conversion = file_type.lower() in ("csv", "tsv") and any(
        str(r[1]).upper() == "VARIANT" for r in rows
    )
    
    for idx, gcs_path in enumerate(gcs_object_paths, 1):
        if gcs_path.startswith("gcs://"):
            gcs_url = gcs_path
        else:
            gcs_url = f"gcs://{bucket_name}/{gcs_path}"

        if needs_json_conversion:
            # COPY transformations require a named stage, not a raw GCS URL.
            # Use the exact file name so another chunk cannot be loaded twice.
            stage_url, filename = gcs_url.rsplit("/", 1)
            stage_name = f"{Q(database)}.{Q(schema)}.{Q('S3_LOAD_' + uuid4().hex.upper())}"
            expressions = ", ".join(
                f"PARSE_JSON(${position})" if str(row[1]).upper() == "VARIANT"
                else f"${position}"
                for position, row in enumerate(rows, 1)
            )
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TEMPORARY STAGE {stage_name} "
                    f"URL = '{(stage_url + '/').replace(chr(39), chr(39) * 2)}' "
                    f"STORAGE_INTEGRATION = {Q(storage_integration)} "
                    f"FILE_FORMAT = {file_format_sql}"
                )
                try:
                    cur.execute(
                        f"COPY INTO {Q(database)}.{Q(schema)}.{Q(staging_table)} "
                        f"({target_cols_sql}) FROM (SELECT {expressions} FROM @{stage_name}) "
                        f"FILES = ('{filename.replace(chr(39), chr(39) * 2)}') "
                        "FORCE = TRUE ON_ERROR = 'ABORT_STATEMENT'"
                    )
                finally:
                    try:
                        cur.execute(f"DROP STAGE IF EXISTS {stage_name}")
                    except Exception:
                        logger.warning("Could not drop temporary S3 load stage", exc_info=True)
            continue
        
        sql = f"""
            COPY INTO {Q(database)}.{Q(schema)}.{Q(staging_table)}
                ({target_cols_sql})
            FROM '{gcs_url}'
            FILE_FORMAT = {file_format_sql}
            STORAGE_INTEGRATION = {Q(storage_integration)}
            FORCE = TRUE
            ON_ERROR = 'ABORT_STATEMENT';
        """
        with conn.cursor() as cur:
            cur.execute(sql)

def copy_into_staging_from_gcs(
    conn,
    gcs_object_path: str,
    staging_table: str,
    sf_database: str = None,
    sf_schema: str = None,
    sf_storage_integration: str = None,
    gcs_bucket_name: str = None,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    file_type: str = "csv",
):
    """Copy single GCS file into Snowflake staging table."""
    copy_into_staging_from_gcs_batch(
        conn, [gcs_object_path], staging_table,
        sf_database, sf_schema, sf_storage_integration,
        gcs_bucket_name,
        delimiter, quotechar, skipheaderlines, file_type
    )

def merge_staging_into_final(
    conn,
    table: str,
    staging_table: str,
    load_method: str,
    user_pk: List[str],
    sf_database: str = None,
    sf_schema: str = None,
    source_file_key: str = None,
    source_file_keys: Optional[List[str]] = None,
    delete_missing_rows: bool = False,
) -> Dict[str, int]:
    """
    Merge staging table into final table using specified load method.
    
    Args:
        load_method: One of "upsert_file_row", "append_file_modified", "upsert_custom_key"
    
    Returns:
        Dict with keys "inserted", "updated", and "deleted" containing row counts
    """
    if not sf_database:
        raise ValueError("sf_database is required")
    if not sf_schema:
        raise ValueError("sf_schema is required")
    
    database = sf_database
    schema = sf_schema

    # Keep Snowflake table naming consistent with ensure_tables() creation path.
    normalized_table = sanitize_identifier(table)
    final_table = f'{Q(database)}.{Q(schema)}.{Q(normalized_table)}'
    staging_tbl = f'{Q(database)}.{Q(schema)}.{Q(staging_table)}'
    
    col_sql = f"""
        SELECT COLUMN_NAME
        FROM {Q(database)}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{schema}'
          AND TABLE_NAME   = '{staging_table}'
        ORDER BY ORDINAL_POSITION
    """
    with conn.cursor() as cur:
        cur.execute(col_sql)
        cols = [r[0].upper() for r in cur.fetchall()]
    
    if not cols:
        raise Exception(f" No columns for staging table {staging_tbl}")
    
    # Determine primary key columns based on load_method
    log(f"[S3][MERGE_STAGING] Load method: {load_method}")
    if load_method == "upsert_file_row":
        pk_cols = ["_FILE", "_ROW"]
        log(f"[S3][MERGE_STAGING] Using primary keys: {pk_cols}")
    elif load_method == "append_file_modified":
        pk_cols = ["_FILE", "_ROW", "_MODIFIED"]
        log(f"[S3][MERGE_STAGING] Using primary keys: {pk_cols}")
    elif load_method == "upsert_custom_key":
        pk_cols = sanitize_custom_pk_columns(user_pk)
        if not pk_cols:
            pk_cols = ["_FILE", "_ROW"]
            log(f"[S3][MERGE_STAGING]  No custom PK provided, using default: {pk_cols}")
        else:
            log(f"[S3][MERGE_STAGING] Using custom primary keys: {pk_cols}")
        if "_FILE" not in cols:
            cols.append("_FILE")
        if "_ROW" not in cols:
            cols.append("_ROW")
    else:
        raise ValueError(f"Invalid load method: {load_method}. Must be one of: 'upsert_file_row', 'append_file_modified', 'upsert_custom_key'")
    
    on_clause = " AND ".join([f"t.{Q(c)} = s.{Q(c)}" for c in pk_cols])
    non_pk = [c for c in cols if c not in pk_cols]
    
    set_clause = ", ".join([f"t.{Q(c)} = s.{Q(c)}" for c in non_pk])
    insert_cols = ", ".join(Q(c) for c in cols)
    insert_vals = ", ".join(f"s.{Q(c)}" for c in cols)
    spect_deleted_col = "_SPECT_DELETED"
    revive_soft_deleted = load_method in ("upsert_custom_key", "upsert_file_row")

    if load_method == "upsert_file_row":
        # For file+row upserts, match Fivetran behavior:
        # delete all rows for the incoming file(s) and insert fresh data.
        # This avoids MERGE and ensures a true file refresh.
        staging_row_count = 0
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {staging_tbl}")
            staging_row_count = cur.fetchone()[0] or 0

        # Determine file scope from provided keys, or derive from staging.
        file_scope = []
        if source_file_keys:
            file_scope = sorted({str(k).strip() for k in source_file_keys if str(k).strip()})
        elif source_file_key:
            source_file_scope = str(source_file_key).strip()
            if source_file_scope:
                file_scope = [source_file_scope]
        if not file_scope:
            try:
                with conn.cursor() as cur:
                    cur.execute(f'SELECT DISTINCT "_FILE" FROM {staging_tbl}')
                    file_scope = [str(r[0]).strip() for r in (cur.fetchall() or []) if str(r[0]).strip()]
            except Exception as scope_err:
                log(f"[S3][MERGE_STAGING]  Failed to derive _FILE scope from staging: {scope_err}")

        deleted_count = 0
        if file_scope:
            placeholders = ", ".join(["%s"] * len(file_scope))
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {final_table}
                    WHERE "_FILE" IN ({placeholders})
                    """,
                    tuple(file_scope),
                )
                deleted_count = cur.fetchone()[0] or 0
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    DELETE FROM {final_table}
                    WHERE "_FILE" IN ({placeholders})
                    """,
                    tuple(file_scope),
                )
            
        else:
            log("[S3][MERGE_STAGING]  No _FILE scope found; skipping delete step")

        if staging_row_count:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {final_table} ({insert_cols})
                    SELECT {insert_cols}
                    FROM {staging_tbl}
                    """
                )
        log(
            f"[S3][MERGE_STAGING] File refresh insert: {staging_row_count} row(s) inserted"
        )

        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {staging_tbl}")

        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {staging_tbl}")

        # For file refresh mode, do not count deletes as operation rows.
        return {"inserted": staging_row_count, "updated": 0, "deleted": 0}

    # Consider a row "updated" only if non-system/business columns changed.
    # This avoids counting technical metadata-only refreshes as modifications.
    metadata_volatile_cols = {"_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED"}
    # In upsert_custom_key, _ROW is positional metadata and should not trigger updates.
    if load_method == "upsert_custom_key":
        metadata_volatile_cols.add("_ROW")
    comparable_cols = [c for c in non_pk if c not in metadata_volatile_cols]
    change_predicate = " OR ".join(
        [
            (
                f"REGEXP_REPLACE(COALESCE(TO_VARCHAR(t.{Q(c)}), '__NULL__'), '[\\\\r\\\\n]+$', '') "
                f"<> REGEXP_REPLACE(COALESCE(TO_VARCHAR(s.{Q(c)}), '__NULL__'), '[\\\\r\\\\n]+$', '')"
            )
            for c in comparable_cols
        ]
    )
    
    #  Count rows to insert/update before MERGE (for delta sync tracking)
    log(f"[S3][MERGE_STAGING] Counting rows before MERGE...")
    with conn.cursor() as cur:
        # Count rows that will be inserted (not in target)
        cur.execute(f"""
            SELECT COUNT(*)
            FROM {staging_tbl} s
            LEFT JOIN {final_table} t ON {on_clause}
            WHERE t.{Q(pk_cols[0])} IS NULL
        """)
        insert_count = cur.fetchone()[0] or 0
        
        # Count rows that will be updated (already in target)
        update_count_sql = f"""
            SELECT COUNT(*)
            FROM {staging_tbl} s
            INNER JOIN {final_table} t ON {on_clause}
        """
        if not revive_soft_deleted:
            update_count_sql += f"\nWHERE COALESCE(t.{Q(spect_deleted_col)}, FALSE) = FALSE"
        if change_predicate:
            update_count_sql += f"\n  AND {change_predicate}" if not revive_soft_deleted else f"\nWHERE {change_predicate}"
        cur.execute(update_count_sql)
        update_count = cur.fetchone()[0] or 0

        # Debug: how many staging rows match soft-deleted target rows
        soft_deleted_match_count_sql = f"""
            SELECT COUNT(*)
            FROM {staging_tbl} s
            INNER JOIN {final_table} t ON {on_clause}
            WHERE COALESCE(t.{Q(spect_deleted_col)}, FALSE) = TRUE
        """
        cur.execute(soft_deleted_match_count_sql)
        soft_deleted_match_count = cur.fetchone()[0] or 0
    
    log(f"[S3][MERGE_STAGING] Pre-merge counts: {insert_count} to insert, {update_count} to update")
    if soft_deleted_match_count:
        log(
            f"[S3][MERGE_STAGING][DEBUG] {soft_deleted_match_count} staging row(s) match soft-deleted "
            f"target row(s). These will NOT update or revive."
        )
        try:
            max_debug_rows = 10
            pk_select_sql = ", ".join([f"s.{Q(c)} AS {Q(c)}" for c in pk_cols])
            soft_deleted_debug_sql = f"""
                SELECT {pk_select_sql}
                FROM {staging_tbl} s
                INNER JOIN {final_table} t ON {on_clause}
                WHERE COALESCE(t.{Q(spect_deleted_col)}, FALSE) = TRUE
                LIMIT {max_debug_rows}
            """
            with conn.cursor() as cur:
                cur.execute(soft_deleted_debug_sql)
                debug_rows = cur.fetchall() or []
                debug_cols = [d[0] for d in (cur.description or [])]
            for row in debug_rows:
                row_map = {debug_cols[i]: row[i] for i in range(len(debug_cols))}
                log(f"[S3][MERGE_STAGING][DEBUG] soft-deleted match PK: {row_map}")
        except Exception as debug_err:
            log(f"[S3][MERGE_STAGING][DEBUG] Failed to extract soft-deleted match PKs: {debug_err}")

        # Debug: detect duplicate target rows for the same PK (active vs soft-deleted)
        try:
            max_debug_rows = 10
            pk_join = " AND ".join([f"t.{Q(c)} = s.{Q(c)}" for c in pk_cols])
            pk_group = ", ".join([f"t.{Q(c)}" for c in pk_cols])
            dup_pk_sql = f"""
                SELECT {pk_group},
                       COUNT(*) AS TARGET_ROW_COUNT,
                       SUM(IFF(COALESCE(t.{Q(spect_deleted_col)}, FALSE) = TRUE, 1, 0)) AS SOFT_DELETED_COUNT
                FROM {final_table} t
                INNER JOIN {staging_tbl} s ON {pk_join}
                GROUP BY {pk_group}
                HAVING COUNT(*) > 1
                LIMIT {max_debug_rows}
            """
            with conn.cursor() as cur:
                cur.execute(dup_pk_sql)
                dup_rows = cur.fetchall() or []
                dup_cols = [d[0] for d in (cur.description or [])]
            if dup_rows:
                log(
                    f"[S3][MERGE_STAGING][DEBUG] Found duplicate target rows for the same PK "
                    f"(showing up to {max_debug_rows})."
                )
                for row in dup_rows:
                    row_map = {dup_cols[i]: row[i] for i in range(len(dup_cols))}
                    log(f"[S3][MERGE_STAGING][DEBUG] duplicate PK summary: {row_map}")
        except Exception as debug_err:
            log(f"[S3][MERGE_STAGING][DEBUG] Failed to detect duplicate PKs: {debug_err}")

    # Debug: show which PK rows are treated as updates and which columns changed.
    # This is especially useful for upsert_file_row where _ROW shifts can create updates.
    if update_count > 0 and comparable_cols:
        try:
            max_debug_rows = 20
            changed_cols_sql = "ARRAY_TO_STRING(ARRAY_COMPACT(ARRAY_CONSTRUCT(" + ", ".join(
                [
                    (
                        f"IFF(REGEXP_REPLACE(COALESCE(TO_VARCHAR(t.{Q(c)}), '__NULL__'), '[\\\\r\\\\n]+$', '') "
                        f"<> REGEXP_REPLACE(COALESCE(TO_VARCHAR(s.{Q(c)}), '__NULL__'), '[\\\\r\\\\n]+$', ''), '{c}', NULL)"
                    )
                    for c in comparable_cols
                ]
            ) + ")), ',')"
            changed_values_sql = "ARRAY_TO_STRING(ARRAY_COMPACT(ARRAY_CONSTRUCT(" + ", ".join(
                [
                    (
                        f"IFF(REGEXP_REPLACE(COALESCE(TO_VARCHAR(t.{Q(c)}), '__NULL__'), '[\\\\r\\\\n]+$', '') "
                        f"<> REGEXP_REPLACE(COALESCE(TO_VARCHAR(s.{Q(c)}), '__NULL__'), '[\\\\r\\\\n]+$', ''), "
                        f"'{c}:[old=' || COALESCE(TO_VARCHAR(t.{Q(c)}), '__NULL__') || "
                        f"', new=' || COALESCE(TO_VARCHAR(s.{Q(c)}), '__NULL__') || ']', NULL)"
                    )
                    for c in comparable_cols
                ]
            ) + ")), ' | ')"
            pk_select_sql = ", ".join([f"s.{Q(c)} AS {Q(c)}" for c in pk_cols])
            update_debug_sql = f"""
                SELECT
                    {pk_select_sql},
                    {changed_cols_sql} AS CHANGED_COLUMNS,
                    {changed_values_sql} AS CHANGED_VALUES
                FROM {staging_tbl} s
                INNER JOIN {final_table} t ON {on_clause}
                WHERE {change_predicate}
                LIMIT {max_debug_rows}
            """
            with conn.cursor() as cur:
                cur.execute(update_debug_sql)
                debug_rows = cur.fetchall() or []
                debug_cols = [d[0] for d in (cur.description or [])]

            log(
                f"[S3][MERGE_STAGING][DEBUG] Found {update_count} update candidate row(s). "
                f"Showing up to {max_debug_rows}:"
            )
            for row in debug_rows:
                row_map = {debug_cols[i]: row[i] for i in range(len(debug_cols))}
                log(f"[S3][MERGE_STAGING][DEBUG] {row_map}")
        except Exception as debug_err:
            log(f"[S3][MERGE_STAGING][DEBUG] Failed to extract update-diff details: {debug_err}")
    
    # Hard fail on duplicate PKs in staging or target to avoid MERGE collisions.
    try:
        pk_group = ", ".join([f"{Q(c)}" for c in pk_cols])
        pk_select = ", ".join([f"s.{Q(c)} AS {Q(c)}" for c in pk_cols])
        max_dup_samples = 5
        # 1) Duplicate keys within staging.
        dup_staging_sql = f"""
            SELECT {pk_group}, COUNT(*) AS DUP_COUNT
            FROM {staging_tbl} s
            GROUP BY {pk_group}
            HAVING COUNT(*) > 1
            LIMIT {max_dup_samples}
        """
        with conn.cursor() as cur:
            cur.execute(dup_staging_sql)
            dup_staging_rows = cur.fetchall() or []
            dup_staging_cols = [d[0] for d in (cur.description or [])]
        if dup_staging_rows:
            samples = []
            for row in dup_staging_rows:
                row_map = {dup_staging_cols[i]: row[i] for i in range(len(dup_staging_cols))}
                samples.append(row_map)
            log(f"[S3][MERGE_STAGING][ERROR] Duplicate PKs found in staging: {samples}")
            raise ValueError(
                f"Duplicate key(s) detected in staging for table '{normalized_table}' "
                f"(pk={pk_cols}). Merge aborted."
            )

        # 2) Duplicate keys in target for keys present in staging.
        pk_join = " AND ".join([f"t.{Q(c)} = s.{Q(c)}" for c in pk_cols])
        pk_group_t = ", ".join([f"t.{Q(c)}" for c in pk_cols])
        dup_target_sql = f"""
            SELECT {pk_group_t}, COUNT(*) AS TARGET_ROW_COUNT
            FROM {final_table} t
            INNER JOIN {staging_tbl} s ON {pk_join}
            GROUP BY {pk_group_t}
            HAVING COUNT(*) > 1
            LIMIT {max_dup_samples}
        """
        with conn.cursor() as cur:
            cur.execute(dup_target_sql)
            dup_target_rows = cur.fetchall() or []
            dup_target_cols = [d[0] for d in (cur.description or [])]
        if dup_target_rows:
            samples = []
            for row in dup_target_rows:
                row_map = {dup_target_cols[i]: row[i] for i in range(len(dup_target_cols))}
                samples.append(row_map)
            log(f"[S3][MERGE_STAGING][ERROR] Duplicate PKs found in target: {samples}")
            raise ValueError(
                f"Duplicate key(s) detected in target for table '{normalized_table}' "
                f"(pk={pk_cols}). Merge aborted."
            )
    except Exception:
        # Re-raise to stop MERGE when duplicates are detected
        raise
    
    if change_predicate:
        matched_guard = ""
        if not revive_soft_deleted:
            matched_guard = f" AND COALESCE(t.{Q(spect_deleted_col)}, FALSE) = FALSE"
        merge_sql = f"""
            MERGE INTO {final_table} t
            USING {staging_tbl} s
            ON {on_clause}
            WHEN MATCHED{matched_guard} AND ({change_predicate}) THEN UPDATE SET {set_clause}
            WHEN NOT MATCHED THEN
                INSERT ({insert_cols}) VALUES ({insert_vals});
        """
    else:
        # No comparable business columns; only INSERT new rows.
        merge_sql = f"""
            MERGE INTO {final_table} t
            USING {staging_tbl} s
            ON {on_clause}
            WHEN NOT MATCHED THEN
                INSERT ({insert_cols}) VALUES ({insert_vals});
        """
    
    with conn.cursor() as cur:
        cur.execute(f"ALTER TABLE {final_table} ADD COLUMN IF NOT EXISTS {Q(spect_deleted_col)} BOOLEAN")

    with conn.cursor() as cur:
        cur.execute(merge_sql)

    deleted_count = 0
    if delete_missing_rows and load_method in ("upsert_file_row", "upsert_custom_key"):
        key_match_clause = " AND ".join([f"s.{Q(c)} = t.{Q(c)}" for c in pk_cols])

        if source_file_keys:
            source_file_keys_scope = sorted({str(k).strip() for k in source_file_keys if str(k).strip()})
            if source_file_keys_scope:
                placeholders = ", ".join(["%s"] * len(source_file_keys_scope))
                log(
                    f"[S3][MERGE_STAGING] Delete sync scope: {len(source_file_keys_scope)} file(s) "
                    f"(multi-file snapshot, full key) -> {source_file_keys_scope}"
                )
                delete_candidates_sql = f"""
                    SELECT COUNT(*)
                    FROM {final_table} t
                    WHERE t."_FILE" IN ({placeholders})
                      AND t."_SPECT_DELETED" = FALSE
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {staging_tbl} s
                          WHERE {key_match_clause}
                      )
                """
                with conn.cursor() as cur:
                    cur.execute(delete_candidates_sql, tuple(source_file_keys_scope))
                    deleted_count = cur.fetchone()[0] or 0
                if deleted_count:
                    soft_delete_sql = f"""
                        UPDATE {final_table} t
                        SET {Q(spect_deleted_col)} = TRUE,
                            "_SPECT_SYNCED" = CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())
                        WHERE t."_FILE" IN ({placeholders})
                          AND t."_SPECT_DELETED" = FALSE
                          AND NOT EXISTS (
                              SELECT 1
                              FROM {staging_tbl} s
                              WHERE {key_match_clause}
                          )
                    """
                    with conn.cursor() as cur:
                        cur.execute(soft_delete_sql, tuple(source_file_keys_scope))
                    log(
                        f"[S3][MERGE_STAGING] Soft delete sync applied for multi-file snapshot: "
                        f"{deleted_count} row(s) marked deleted"
                    )
        elif source_file_key:
            source_file_scope = str(source_file_key).strip()
            log(
                f"[S3][MERGE_STAGING] Delete sync scope: source_file_key='{source_file_key}', "
                f"source_file_scope='{source_file_scope}'"
            )
            delete_candidates_sql = f"""
                SELECT COUNT(*)
                FROM {final_table} t
                WHERE t."_FILE" = %s
                  AND t."_SPECT_DELETED" = FALSE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {staging_tbl} s
                      WHERE {key_match_clause}
                  )
            """
            with conn.cursor() as cur:
                cur.execute(delete_candidates_sql, (source_file_scope,))
                deleted_count = cur.fetchone()[0] or 0
            if deleted_count:
                soft_delete_sql = f"""
                    UPDATE {final_table} t
                    SET {Q(spect_deleted_col)} = TRUE,
                        "_SPECT_SYNCED" = CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())
                    WHERE t."_FILE" = %s
                      AND t."_SPECT_DELETED" = FALSE
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {staging_tbl} s
                      WHERE {key_match_clause}
                      )
                """
                with conn.cursor() as cur:
                    cur.execute(soft_delete_sql, (source_file_scope,))
                log(
                    f"[S3][MERGE_STAGING] Soft delete sync applied for file '{source_file_key}': "
                    f"{deleted_count} row(s) marked deleted"
                )

    # Soft-delete reset behavior:
    # - upsert_file_row: do NOT revive; only normalize NULL -> FALSE for legacy rows.
    # - upsert_custom_key: allow revive when the PK reappears in staging.
    if delete_missing_rows and load_method in ("upsert_file_row", "upsert_custom_key"):
        with conn.cursor() as cur:
            if revive_soft_deleted:
                cur.execute(f"""
                    UPDATE {final_table} t
                    SET {Q(spect_deleted_col)} = FALSE
                    WHERE EXISTS (
                        SELECT 1
                        FROM {staging_tbl} s
                        WHERE {key_match_clause}
                    )
                """)
            else:
                cur.execute(f"""
                    UPDATE {final_table} t
                    SET {Q(spect_deleted_col)} = FALSE
                    WHERE {Q(spect_deleted_col)} IS NULL
                      AND EXISTS (
                          SELECT 1
                          FROM {staging_tbl} s
                          WHERE {key_match_clause}
                      )
                """)
    
    log(f"[S3][MERGE_STAGING]  MERGE completed: {insert_count} inserted, {update_count} updated, {deleted_count} deleted")
    
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {staging_tbl}")
    
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {staging_tbl}")
    
    return {"inserted": insert_count, "updated": update_count, "deleted": deleted_count}

# =============================================================================
# LOGGING AND ERROR TRACKING
# =============================================================================

class MigrationFailureTracker:
    """Tracks failed tables during migration for consolidated error reporting."""
    
    def __init__(self):
        self.failed_tables = []
        self.migration_start_time = datetime.now(timezone.utc)

    def add_failure(self, table_name: str, error_message: str, service_name: str = ""):
        """Add a failed table to the tracker."""
        table_name = str(table_name or "").strip()
        if not table_name:
            return
        for item in self.failed_tables:
            if str(item.get("table_name", "")).strip().upper() == table_name.upper():
                # Keep one record per table; refresh details with latest error.
                item["error_message"] = error_message
                item["failure_time"] = datetime.now(timezone.utc)
                item["service_name"] = service_name
                return
        self.failed_tables.append({
            "table_name": table_name,
            "error_message": error_message,
            "failure_time": datetime.now(timezone.utc),
            "service_name": service_name
        })

    def has_failures(self):
        """Check if there are any failed tables."""
        return len(self.failed_tables) > 0

    def send_consolidated_error_email(self, connection_name: str, service_name: str, user_id: int,
                                     cmp_id: int, connection_id: int, job_name: str,
                                     migration_session_id: int, company_name: str,
                                     is_final_attempt: bool = True):
        """Send one consolidated email with all failed tables."""
        if not self.has_failures() or not is_final_attempt:
            return

        try:
            primary_failed_table = next(
                (
                    str(item.get("table_name") or "").strip()
                    for item in self.failed_tables
                    if isinstance(item, dict) and str(item.get("table_name") or "").strip()
                ),
                "Migration",
            )
            error_details = []
            for failure in self.failed_tables:
                timestamp = failure["failure_time"].strftime("%Y-%m-%d %H:%M:%S UTC")
                error_details.append(f"""
 Table: {failure['table_name']}
    Failed at: {timestamp}
    Error: {failure['error_message']}
   ----------------------------------""")

            consolidated_error_message = f"""Migration completed with {len(self.failed_tables)} failed table(s).

FAILED TABLES SUMMARY:
{''.join(error_details)}"""

            send_error_notification(
                table_name=primary_failed_table,
                error_message=consolidated_error_message,
                connection_name=connection_name,
                service_name=service_name,
                user_id=user_id,
                company_id=cmp_id,
                is_consolidated=False,
                additional_context={
                    "connection_id": connection_id,
                    "job_name": job_name,
                    "migration_session_id": migration_session_id,
                    "company_name": company_name,
                    "total_failed_tables": len(self.failed_tables),
                    "failed_tables_list": [f["table_name"] for f in self.failed_tables if f.get("table_name")],
                }
            )
        except Exception as e:
            log(f" Failed to send consolidated error email: {e}")

# =============================================================================
# HELPER FUNCTIONS FOR DELTA FIELD DETECTION
# =============================================================================

def get_source_id_from_connection(connection_id):
    """
    Get src_id from connection_id by following the relationship:
    connection -> source_config -> source_model
    
    This is used to determine which source type this connection belongs to,
    which is needed for delta field detection.
    
    Args:
        connection_id (int): The connection ID
        
    Returns:
        int or None: The source ID (src_id) if found, None otherwise
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT sm.src_id
            FROM connection tc
            JOIN source_config sc ON tc.src_config_id = sc.src_config_id
            JOIN source_model sm ON sc.src_id = sm.src_id
            WHERE tc.connection_id = %s
        """, (connection_id,))
        
        result = cursor.fetchone()
        source_id = result[0] if result else None
        
        if source_id:
            log(f"[S3][HELPER] Found source_id={source_id} for connection_id={connection_id}")
        else:
            log(f"[S3][HELPER]  No source_id found for connection_id={connection_id}")
        
        return source_id
        
    except Exception as e:
        log(f"[S3][HELPER]  Error fetching src_id for connection {connection_id}: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def get_delta_fields_for_source(source_id):
    """
    Fetch delta fields from source_delta_table for a given source_id.
    
    Delta fields are used to determine if a table supports incremental sync.
    If a table contains any of these delta fields, it can be marked as is_delta=True.
    This enables incremental data loading instead of full reload.
    
    Args:
        source_id (int): The source ID (e.g., for AmazonS3)
    
    Returns:
        list: List of delta field names for the source (e.g., ['last_modified', 'updated_at'])
    """
    if not source_id:
        return []
    
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT delta_fields 
            FROM source_delta_table 
            WHERE source_id = %s
        """, (source_id,))
        
        result = cursor.fetchone()
        if result and result[0]:
            # Parse the delta_fields JSON if it's stored as JSON
            delta_fields = result[0] if isinstance(result[0], list) else json.loads(result[0])
            log(f"[S3][HELPER] Found {len(delta_fields)} delta fields for source_id {source_id}: {delta_fields}")
            return delta_fields
        
        log(f"[S3][HELPER] No delta fields configured for source_id {source_id}")
        return []
        
    except Exception as e:
        log(f"[S3][HELPER]  Error fetching delta fields for source {source_id}: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()

def check_table_has_delta_fields(table_fields, delta_fields):
    """
    Check if a table contains any of the specified delta fields.
    
    This is used to determine if a table supports incremental sync (is_delta=True).
    If a table has any delta field (e.g., 'last_modified', 'updated_at'), it can be
    used for incremental data loading instead of full reload.
    
    Args:
        table_fields (dict): Dictionary of table field names and their metadata
        delta_fields (list): List of delta field names to check for
    
    Returns:
        bool: True if table contains any delta fields, False otherwise
    """
    if not table_fields or not delta_fields:
        return False
    
    # Convert table_fields to dict if it's a JSON string
    if isinstance(table_fields, str):
        try:
            table_fields = json.loads(table_fields)
        except (json.JSONDecodeError, ValueError):
            log(f"[S3][HELPER]  Failed to parse table_fields as JSON")
            return False
    
    # Check if any delta field exists in the table fields
    normalized_fields = {str(field).strip().casefold() for field in table_fields}
    found_delta_fields = [field for field in delta_fields
                          if str(field).strip().casefold() in normalized_fields]
    has_delta = len(found_delta_fields) > 0
    
    if has_delta:
        log(f"[S3][HELPER]  Table contains delta fields: {found_delta_fields}")
    
    return has_delta

def insert_error_log(cmp_id, connection_id, message, function_name):
    """
    Inserts a log entry into the `error_log_entry` table.
    
    This is a simple error logging function for tracking errors in the error log table.
    Used for consistency with other services (Salesforce, MicrosoftDynamics365_FO, etc.).
    
    Args:
        cmp_id (int): Company ID.
        connection_id (int): Connection ID.
        message (str): Log message describing the error.
        function_name (str): Name of the function where the error occurred.
    
    Returns:
        int: The newly inserted log ID, or None if an error occurs.
    """
    try:
        conn = postgres_connection()
        if conn is None:
            log("[S3][HELPER]  Failed to establish connection to the database.")
            return None
        
        cursor = conn.cursor()
        
        # Get the current UTC timestamp
        current_time = datetime.utcnow()
        
        # Insert the log entry (Auto-incrementing ID, so we don't manually handle it)
        log_insert_query = '''
            INSERT INTO error_log_entry (cmp_id, connection_id, message, function_name, created_on)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        '''
        cursor.execute(
            log_insert_query,
            (cmp_id, connection_id, message, function_name, current_time)
        )
        
        # Retrieve new log ID
        log_id = cursor.fetchone()[0]
        
        # Commit transaction
        conn.commit()
        
        log(f"[S3][HELPER]  Error log entry inserted successfully with log_id: {log_id}")
        return log_id
        
    except Exception as e:
        log(f"[S3][HELPER]  Error inserting into errorlog_entry: {str(e)}")
        if 'conn' in locals():
            try:
                conn.rollback()
            except Exception as rollback_error:
                log(f"[S3][HELPER]  Rollback failed: {rollback_error}")
        return None
        
    finally:
        # Ensure cursor and connection are closed
        if 'conn' in locals():
            try:
                cursor.close()
                conn.close()
            except Exception as close_error:
                log(f"[S3][HELPER]  Cleanup failed: {close_error}")

# =============================================================================
# MAIN SERVICE CLASS
# =============================================================================

class AmazonS3Service:
    """
    Complete Amazon S3 to Snowflake Migration Service.
    
    This service class provides a comprehensive, production-ready implementation
    for migrating data from Amazon S3 to Snowflake. It consolidates all S3
    operations into a single, well-organized service following the same patterns
    as other migration services (Salesforce, Microsoft Dynamics, etc.).
    
    Key Features:
    - Connection validation and authentication
    - Table/file listing (with and without schema inference)
    - Schema inference and automatic refresh
    - Full migration with delta reload and drop & reload support
    - Support for all file types: CSV, TSV, JSON, XML, Parquet, Avro
    - All load methods: UPSERT, APPEND, CUSTOM PK
    - All file mapping modes: Single table/file, Multi-file  Single table
    - Streaming chunk-based processing for memory efficiency
    - GCS staging for Snowflake COPY INTO
    - Comprehensive error handling and logging
    - Email notifications for failures
    - Progress tracking and status updates
    
    Architecture:
    - Static methods for stateless operations
    - Consistent error handling using handle_service_error()
    - Standardized logging using log() utility
    - Database integration via utils.database_utils
    - Email notifications via utils.email_utils
    
    Usage:
        # Validate connection
        result = await AmazonS3Service.authenticate(validation_request)
        
        # List files (no schema)
        result = await AmazonS3Service.list_tables(listing_request)
        
        # List tables with schema
        result = await AmazonS3Service.list_tables_with_schema(listing_request)
        
        # Migrate data
        result = await AmazonS3Service.migrate(migration_request)
    """
    
    @staticmethod
    async def authenticate(request: AmazonS3ValidationRequest):
        """Validate S3 connection by testing bucket access."""
        log("[S3][VALIDATE] Starting connection validation...")
        
        s3_config = request.AmazonS3
        aws_access_key_id = s3_config.get("aws_access_key_id")
        aws_secret_access_key = s3_config.get("aws_secret_access_key")
        s3_bucket = (s3_config.get("s3_bucket") or "").strip()
        aws_region = s3_config.get("aws_region")
        s3_base_prefix = (s3_config.get("s3_base_prefix") or "").strip()
        s3_folder_prefix = normalize_s3_folder_prefix(s3_base_prefix)
        
        if not all([aws_access_key_id, aws_secret_access_key, s3_bucket]):
            missing = [k for k, v in {"aws_access_key_id": aws_access_key_id, "aws_secret_access_key": aws_secret_access_key, "s3_bucket": s3_bucket}.items() if not v]
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        bucket_validation_error = validate_s3_bucket_name(s3_bucket)
        if bucket_validation_error:
            return {
                "status_code": 422,
                "body": bucket_validation_error,
                "error_code": "InvalidBucketName",
            }
        
        try:
            if not aws_region:
                aws_region = detect_s3_bucket_region(
                    s3_bucket, aws_access_key_id, aws_secret_access_key
                )
            
            s3 = s3_client(aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket, auto_detect_region=False)
            s3.head_bucket(Bucket=s3_bucket)

            # If a base folder is provided, validate that it exists in the bucket.
            # This prevents saving connections with invalid folder paths.
            if s3_base_prefix:
                prefix_probe = s3.list_objects_v2(
                    Bucket=s3_bucket,
                    Prefix=s3_folder_prefix,
                    MaxKeys=1,
                )
                if prefix_probe.get("KeyCount", 0) == 0:
                    return {
                        "status_code": 404,
                        "body": (
                            f"No files found under `s3_base_prefix`='{s3_base_prefix}'. "
                            "Verify folder path and trailing segments."
                        ),
                        "error_code": "InvalidS3BasePrefix",
                    }
            
            response = s3.list_objects_v2(
                Bucket=s3_bucket,
                Prefix=s3_folder_prefix if s3_folder_prefix else "",
                MaxKeys=5,
            )
            files = [obj["Key"] for obj in response.get("Contents", [])]
            
            return {
                "status_code": 200,
                "body": "Connection established successfully",
                "files": files[:5],
                "detected_region": aws_region
            }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message_map = {
                "InvalidAccessKeyId": "Invalid AWS access key ID. Please verify aws_access_key_id.",
                "SignatureDoesNotMatch": "Invalid AWS secret access key (signature mismatch). Please verify aws_secret_access_key.",
                "InvalidClientTokenId": "Invalid AWS credentials. Please verify aws_access_key_id and aws_secret_access_key.",
                "AccessDenied": "Access denied for bucket. Verify IAM permissions and bucket policy for the provided credentials.",
                "Forbidden": "Access denied for bucket. Credentials are invalid or do not have required S3 permissions.",
                "403": "Access denied for bucket. Credentials are invalid or do not have required S3 permissions.",
                "AllAccessDisabled": "Access to this S3 bucket is disabled.",
                "NoSuchBucket": "S3 bucket not found. Please verify s3_bucket.",
                "NotFound": "S3 bucket not found. Please verify s3_bucket.",
                "404": "S3 bucket not found. Please verify s3_bucket.",
                "PermanentRedirect": "Bucket is in a different region. Set aws_region or allow auto-detect.",
            }
            error_status_map = {
                "InvalidAccessKeyId": 422,
                "SignatureDoesNotMatch": 422,
                "InvalidClientTokenId": 422,
                "AccessDenied": 403,
                "Forbidden": 403,
                "403": 403,
                "AllAccessDisabled": 403,
                "NoSuchBucket": 404,
                "NotFound": 404,
                "404": 404,
                "PermanentRedirect": 422,
            }
            friendly_message = error_message_map.get(
                error_code,
                f"S3 connection failed: {error_code}"
            )
            return {
                "status_code": error_status_map.get(error_code, 422),
                "body": friendly_message,
                "error_code": error_code,
            }
        except Exception as e:
            raise e
    
    @staticmethod
    async def preview_pattern(request: AmazonS3PatternPreviewRequest) -> Dict[str, Any]:
        """
        Preview which S3 files match a regex pattern.
        
        Used to test multi-file pattern matching before running migration.
        Lists all files in base prefix and matches against the regex.
        """
        log(f"[S3][PREVIEW] Starting pattern preview for pattern: {request.multi_files_prefix}")
        
        # Extract config
        s3_config = request.AmazonS3
        s3_bucket = (s3_config.get("s3_bucket") or "").strip()
        aws_access_key_id = s3_config.get("aws_access_key_id")
        aws_secret_access_key = s3_config.get("aws_secret_access_key")
        aws_region = s3_config.get("aws_region")
        s3_base_prefix = s3_config.get("s3_base_prefix", "")
        include_subfolders = _as_bool(s3_config.get("include_subfolders"), default=False)
        file_type = s3_config.get("file_type", "csv").lower()
        pattern = request.multi_files_prefix
        max_results = request.max_results or 50

        bucket_validation_error = validate_s3_bucket_name(s3_bucket)
        if bucket_validation_error:
            raise HTTPException(status_code=422, detail=bucket_validation_error)
        
        try:
            if pattern:
                log(f"[S3][PREVIEW]  multi_files_prefix is deprecated and ignored (pattern='{pattern}')")
            
            # 2. Connect to S3
            if not aws_region:
                aws_region = detect_s3_bucket_region(
                    s3_bucket, aws_access_key_id, aws_secret_access_key
                )
            
            s3 = s3_client(aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket, auto_detect_region=False)
            
            # 3. List all files in base prefix
            log(f"[S3][PREVIEW] Listing files in bucket: {s3_bucket}, prefix: {s3_base_prefix}")
            all_keys = _list_s3_keys_by_scope(
                s3=s3,
                s3_bucket=s3_bucket,
                s3_folder_prefix=normalize_s3_folder_prefix(s3_base_prefix),
                include_subfolders=include_subfolders,
            )
            all_keys = [k for k in all_keys if str(k).strip() and not str(k).strip().endswith("/")]
            
            # 4. No pattern or extension filtering; list all keys in scope.
            matched_keys = all_keys
            # Keep deterministic UI order: folder path + file name.
            matched_keys = sorted(
                matched_keys,
                key=lambda k: str(k or "").strip().lower(),
            )
            log(f"[S3][PREVIEW] Listed {len(matched_keys)} files in scope")
            
            # 6. Gather metadata for matched files (up to max_results)
            matched_files = []
            for key in matched_keys[:max_results]:
                try:
                    head = s3.head_object(Bucket=s3_bucket, Key=key)
                    matched_files.append({
                        "file_key": key,
                        "file_name": key.split("/")[-1],
                        "size_bytes": head.get("ContentLength", 0),
                        "last_modified": head.get("LastModified").isoformat() if head.get("LastModified") else None,
                        "matches_pattern": True
                    })
                except Exception as e:
                    log(f"[S3][PREVIEW]  Error getting metadata for {key}: {e}")
                    matched_files.append({
                        "file_key": key,
                        "file_name": key.split("/")[-1],
                        "matches_pattern": True,
                        "error": str(e)
                    })
            
            return {
                "status": "success",
                "pattern": pattern,
                "total_files_in_folder": len(all_keys),
                "total_files_by_type": len(all_keys),
                "matched_files_count": len(matched_keys),
                "matched_files": matched_files,
                "pattern_ignored": bool(pattern),
            }
            
        except Exception as e:
            log(f"[S3][PREVIEW] Pattern preview failed: {str(e) or repr(e)}")
            log(traceback.format_exc())

            if isinstance(e, ClientError):
                err = e.response.get("Error", {}) if hasattr(e, "response") else {}
                code = str(err.get("Code") or "")

                if code in {
                    "InvalidAccessKeyId",
                    "InvalidClientTokenId",
                    "SignatureDoesNotMatch",
                    "AuthFailure",
                    "AccessDenied",
                    "AllAccessDisabled",
                    "ExpiredToken",
                    "TokenRefreshRequired",
                }:
                    raise HTTPException(
                        status_code=422,
                        detail="Invalid AWS credentials. Please check access key and secret key.",
                    )

                if code in {"NoSuchBucket", "NotFound"}:
                    raise HTTPException(status_code=422, detail="Enter valid bucket name.")

                if code in {"AuthorizationHeaderMalformed"}:
                    raise HTTPException(
                        status_code=422,
                        detail="Invalid AWS region for the selected bucket.",
                    )

                raise HTTPException(
                    status_code=422,
                    detail="Unable to access S3. Please verify S3 configuration and try again.",
                )

            if isinstance(e, ParamValidationError):
                raise HTTPException(status_code=422, detail="Enter valid bucket name.")

            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(
                status_code=422,
                detail="Unable to preview pattern. Please verify S3 configuration and try again.",
            )

    @staticmethod
    def auto_refresh_multi_file_mapping(
        s3,
        s3_bucket: str,
        s3_base_prefix: str,
        file_type: str,
        existing_mapping: Dict[str, Any],
        include_subfolders: bool = False,
        pattern: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Auto-refresh file list for multi-file mappings using multi_files_prefix.
        
        This function re-scans the S3 bucket for files matching the stored pattern
        and updates the file list.
        Used to automatically include new files (e.g., sales_2025_may.csv) without
        manual intervention.
        
        Args:
            s3: S3 client instance
            s3_bucket: S3 bucket name
            s3_base_prefix: Base folder prefix (e.g., "sales/")
            file_type: File type filter (e.g., "csv")
            existing_mapping: Existing multi-file grouping in legacy format
            include_subfolders: Whether to include nested folders while refreshing
            pattern: multi_files_prefix pattern used for refresh
        
        Returns:
            Updated multi-file grouping with refreshed file lists
        """
        refreshed_mapping = {}
        
        for table_name, mapping_info in existing_mapping.items():
            if isinstance(mapping_info, list):
                existing_files = mapping_info
            else:
                existing_files = []

            if pattern:
                log(f"[AUTO-REFRESH]  multi_files_prefix is deprecated and ignored (pattern='{pattern}')")

            log(f"[AUTO-REFRESH] Refreshing files for table '{table_name}' (pattern ignored)")
            log(
                f"[AUTO-REFRESH]  Listing scope: "
                f"{'recursive (include subfolders)' if include_subfolders else 'base-folder only (no subfolders)'}"
            )
            
            try:
                # Respect include_subfolders during runtime auto-refresh.
                all_keys = _list_s3_keys_by_scope(
                    s3=s3,
                    s3_bucket=s3_bucket,
                    s3_folder_prefix=normalize_s3_folder_prefix(s3_base_prefix),
                    include_subfolders=include_subfolders,
                )
                
                # No pattern or extension filtering; use all keys in scope.
                matched_files = all_keys
                
                old_count = len(existing_files)
                new_count = len(matched_files)
                
                if new_count > old_count:
                    log(f"[AUTO-REFRESH]  Found {new_count - old_count} new file(s) for '{table_name}' (was {old_count}, now {new_count})")
                elif new_count < old_count:
                    log(f"[AUTO-REFRESH]  File count decreased for '{table_name}' (was {old_count}, now {new_count})")
                else:
                    log(f"[AUTO-REFRESH] No new files for '{table_name}' ({new_count} files)")
                
                # Persist in legacy format (list of file keys).
                refreshed_mapping[table_name] = matched_files
            except Exception as e:
                log(f"[AUTO-REFRESH]  Error refreshing '{table_name}': {e}, keeping existing files")
                refreshed_mapping[table_name] = [str(f).strip() for f in existing_files if str(f).strip()]
        
        return refreshed_mapping

    @staticmethod
    async def suggest_primary_keys(
        request: AmazonS3PKSuggestionRequest
    ) -> Dict[str, Any]:
        """
        Suggest primary keys for S3 files.

        Only supported for:
        load_method = upsert_custom_key
        """

        s3 = request.s3
        log(f"[S3][PK_SUGGEST] Starting PK suggestion")

        # ------------------------------------------------------------------
        # 1. Enforce business rule
        # ------------------------------------------------------------------
        if s3.load_method != LoadMethod.upsert_custom_key:
            raise ValueError("Primary key suggestion is only supported for load_method = upsert_custom_key")

        # ------------------------------------------------------------------
        # 2. Skip unsupported formats early
        # ------------------------------------------------------------------
        if s3.file_type == FileType.xml and ((s3.xml_mode.value if s3.xml_mode else "packed") == "packed"):
            return {
                "status": "success",
                "tables": [],
                "message": "XML packed format does not expose columns"
            }

        if s3.file_type == FileType.json and s3.json_mode == JSONMode.packed:
            return {
                "status": "success",
                "tables": [],
                "message": "JSON packed format does not expose columns"
            }

        # ------------------------------------------------------------------
        # 3. Prepare S3 client (moved up to support pattern resolution)
        # ------------------------------------------------------------------
        bucket_name = (s3.s3_bucket or "").strip()
        if not bucket_name:
            raise HTTPException(status_code=422, detail="s3_bucket is required")

        aws_region = s3.aws_region or detect_s3_bucket_region(
            bucket_name,
            s3.aws_access_key_id,
            s3.aws_secret_access_key,
        )

        s3_client_instance = s3_client(
            s3.aws_access_key_id,
            s3.aws_secret_access_key,
            aws_region,
            bucket_name,
            auto_detect_region=False,
        )

        # ------------------------------------------------------------------
        # 4. Extract file keys based on mapping method
        # ------------------------------------------------------------------
        include_subfolders = _as_bool(getattr(s3, "include_subfolders", False), default=False)
        base_prefix = normalize_s3_folder_prefix(s3.base_folder_path or s3.s3_base_prefix or "")
        if s3.file_mapping_method == FileMappingMethod.single_table_per_file:
            file_keys = list(s3.single_file_table_mapping.keys())
            is_multi_file_mode = False
        else:
            is_multi_file_mode = True
            resolved_multi_files_prefix = (s3.multi_files_prefix or "").strip()
            if resolved_multi_files_prefix:
                log(f"[S3][PK_SUGGEST]  multi_files_prefix is deprecated and ignored (pattern='{resolved_multi_files_prefix}')")

            # Multi-file mode resolves files directly from S3 pattern.
            file_keys = []
            # Multi-file mode resolves files directly from S3 scope (no pattern filtering).
            all_keys = _list_s3_keys_by_scope(
                s3=s3_client_instance,
                s3_bucket=bucket_name,
                s3_folder_prefix=base_prefix,
                include_subfolders=include_subfolders,
            )
            file_keys = all_keys
            log(f"[S3][PK_SUGGEST] Found {len(file_keys)} files in scope")

        if not file_keys:
            return {
                "status": "success",
                "tables": [],
                "message": "No files provided for analysis"
            }

        log(f"[S3][PK_SUGGEST] Files to analyze: {len(file_keys)}")

        # ------------------------------------------------------------------
        # 5. Resolve file format options
        # ------------------------------------------------------------------
        if s3.file_type == FileType.csv:
            opts = s3.csv_options or DelimitedFileOptions()
        elif s3.file_type == FileType.tsv:
            opts = s3.tsv_options or DelimitedFileOptions()
        else:
            opts = None

        # ------------------------------------------------------------------
        # 6. Read files & infer columns
        # ------------------------------------------------------------------
        tables_buffer = []

        # OPTIMIZATION: For multi-file setups, only analyze a sample of files (e.g., first 5)
        # to prevent timeouts and memory exhaustion while still getting accurate schema.
        files_to_read = file_keys[:5] if is_multi_file_mode else file_keys
        log(f"[S3][PK_SUGGEST] Analyzing {len(files_to_read)} files (out of {len(file_keys)})")

        for file_key in files_to_read:
            log(f"[S3][PK_SUGGEST] Reading file: {file_key}")

            try:
                df = read_s3_pk_sample(
                    s3_client_instance,
                    bucket_name,
                    file_key,
                    s3.file_type.value,
                    sample_rows=request.sample_rows,
                    delimiter=opts.delimiter if opts else None,
                    quotechar=opts.quotechar if opts else None,
                    skipheaderlines=opts.skipheaderlines if opts else None,
                    escape_char=opts.escape_char if opts else None,
                    null_sequence=opts.null_sequence if opts else None,
                    skip_footer_lines=opts.skip_footer_lines if opts else None,
                    line_separator=opts.line_separator if opts else None,
                    json_mode=s3.json_mode.value if s3.json_mode else None,
                    xml_mode=s3.xml_mode.value if s3.xml_mode else None,
                )
            except XMLNoBusinessColumnsError as e:
                if s3.file_type != FileType.xml:
                    raise
                log(f"[S3][PK_SUGGEST] Skipping file with no business columns: {file_key}. {e}")
                continue

            if df is None or df.empty:
                log(f"[S3][PK_SUGGEST] Skipping empty file: {file_key}")
                continue

            # Resolve table name: Multi-file name > Mapping Match > Original Derived Name
            internal_name = table_from_file_key(file_key)
            if is_multi_file_mode and s3.multi_files_table_name:
                table_name = sanitize_identifier(str(s3.multi_files_table_name).strip())
            else:
                # Add safety check for cases where mapping is None
                mapping = s3.single_file_table_mapping or {}
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
                detail="No readable files found for primary key suggestion. Check bucket name, file paths, and format options.",
            )

        # ------------------------------------------------------------------
        # 7. Multi-file schema validation
        # ------------------------------------------------------------------
        if is_multi_file_mode and len(tables_buffer) > 1:
            expected_columns = None
            if request.connection_id:
                try:
                    target_table = (
                        sanitize_identifier(str(s3.multi_files_table_name).strip())
                        if s3.multi_files_table_name
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
                    log(f"[S3][PK_SUGGEST]  Failed to read expected schema for multi-file PK suggestion: {e}")

            valid_files = []
            skipped_files = []

            if expected_columns:
                for item in tables_buffer:
                    if set(item["columns"].keys()) == expected_columns:
                        valid_files.append(item)
                    else:
                        skipped_files.append(item["file_key"])
                if not valid_files:
                    expected_columns = None  # fallback to file-based reference

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

        # ------------------------------------------------------------------
        # 8. Cardinality analysis
        # ------------------------------------------------------------------
        analyzed_tables = []
        table_pk_state = {}
        snowflake_tables = _fetch_snowflake_tables_from_config(
            getattr(request.s3, "destination_config", None)
        )
        if snowflake_tables is not None:
            log(
                f"[S3][PK_SUGGEST][DEBUG] Snowflake tables available: {len(snowflake_tables)}"
            )

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
                                f"[S3][PK_SUGGEST][WARN] Failed to parse primary_keys for "
                                f"table '{table_key}'. Raw value: {primary_keys_raw}"
                            )

                    table_pk_state[table_key] = {
                        "pk_locked": bool(last_synced) and bool(existing_primary_keys),
                        "existing_primary_keys": existing_primary_keys,
                    }
                    log(
                        f"[S3][PK_SUGGEST][DEBUG] Table '{table_key}' "
                        f"pk_locked={bool(last_synced) and bool(existing_primary_keys)} "
                        f"existing_primary_keys={existing_primary_keys}"
                    )
                cur_db.close()
                conn_db.close()
            except Exception as e:
                log(f"[S3][PK_SUGGEST]  Failed to read PK lock metadata: {e}")

        for item in tables_buffer:
            df_sample = item["df"].head(request.sample_rows)
            rows_analyzed = len(df_sample)

            analyzed_columns = []

            for col_name, col_meta in item["columns"].items():
                col_upper = str(col_name).strip().upper()
                if col_upper in {"_SPECT_SYNCED", "_SPECT_DELETED"}:
                    # Defensive filter: keep these metadata columns out of PK suggestion output.
                    continue
                series = df_sample[col_name] if col_name in df_sample else None

                if series is None:
                    
                    # Preserve original metadata including is_selected status
                    column_obj = {
                        "column_name": col_name,
                        "data_type": col_meta.get("data_type"),
                        "uniqueness_score": 0.0,
                        "non_null_score": 0.0,
                        "warnings": ["Column not found in sample"]
                    }
                    
                    # Include is_selected if it exists in original metadata
                    if "is_selected" in col_meta:
                        column_obj["is_selected"] = col_meta["is_selected"]
                    
                    analyzed_columns.append(column_obj)
                    continue
                
                if rows_analyzed == 0:
                    unique_pct = 0.0
                    non_null_pct = 0.0
                else:
                    score_series = series.map(
                        lambda value: json.dumps(value, default=_staging_json_default, sort_keys=True)
                        if isinstance(value, (dict, list, np.ndarray)) else value
                    )
                    unique_pct = (score_series.nunique() / rows_analyzed) * 100
                    non_null_pct = ((rows_analyzed - series.isna().sum()) / rows_analyzed) * 100

                warnings = []
                if unique_pct < 100:
                    warnings.append("Contains duplicate values")
                if non_null_pct < 100:
                    warnings.append("Contains NULL values")

                # Preserve original metadata including is_selected status
                column_obj = {
                    "column_name": col_name,
                    "data_type": col_meta.get("data_type"),
                    "uniqueness_score": round(unique_pct, 2),
                    "non_null_score": round(non_null_pct, 2),
                    "warnings": warnings,
                }
                
                # Include is_selected if it exists in original metadata
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
                # Only unlock PKs if the table was previously migrated (locked) and is now missing in Snowflake.
                log(
                    f"[S3][PK_SUGGEST][DEBUG] Table '{table_key}' not found in Snowflake; "
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

    @staticmethod
    def _validate_and_persist_s3_mapping(
        connection_id: int,
        file_mapping_mode: str,
        single_file_map: Dict[str, Any],
        cmp_id: Optional[int] = None,
        source_id: Optional[int] = None,
        basefolder_path: Optional[str] = None,
        created_by: Optional[int] = None,
        updated_by: Optional[int] = None,
    ) -> None:
        """
        Validate and persist S3 mapping.
        
        For initial sync: saves the mapping.
        For subsequent syncs: validates immutability (mapping should not change).
        However, in multi_files_single_table mode, new files can be added to existing mapping.
        """
        try:
            existing_mapping = get_s3_mapping(connection_id)
            normalized_single_map = {}
            if isinstance(single_file_map, dict):
                normalized_single_map = {
                    str(k): sanitize_identifier(str(v).strip()) if str(v).strip() else table_from_file_key(str(k))
                    for k, v in single_file_map.items()
                }
                # Enforce unique target table per file within a connection.
                # Two different files cannot map to the same table in single-file mode.
                table_to_file = {}
                duplicate_tables = set()
                for file_key, table_name in normalized_single_map.items():
                    table_upper = str(table_name).strip().upper()
                    if not table_upper:
                        continue
                    existing_file = table_to_file.get(table_upper)
                    if existing_file and existing_file != file_key:
                        duplicate_tables.add(table_upper)
                    else:
                        table_to_file[table_upper] = file_key
                if duplicate_tables:
                    raise ValueError(
                        "Duplicate mapped table names are not allowed for single_table_per_file mode: "
                        + ", ".join(sorted(duplicate_tables))
                    )
            
            # If no existing mapping, save the new one
            if not existing_mapping:
                save_s3_mapping(
                    connection_id,
                    file_mapping_mode,
                    normalized_single_map,
                    {},
                    cmp_id=cmp_id,
                    source_id=source_id,
                    basefolder_path=basefolder_path,
                    created_by=created_by,
                    updated_by=updated_by,
                )
                log(f"[S3][MAPPING]  Initial mapping saved for connection {connection_id}")
                return
            
            # Validate immutability for single_table_per_file mode
            if file_mapping_mode == "single_table_per_file":
                existing_single = existing_mapping.get("single_file_table_mapping", {})
                if existing_single != normalized_single_map:
                    raise ValueError(
                        f"S3 mapping is immutable after initial sync. Cannot change single_file_table_mapping."
                    )
            if file_mapping_mode == "multi_files_single_table":
                save_s3_mapping(
                    connection_id,
                    file_mapping_mode,
                    {},
                    {},
                    cmp_id=cmp_id,
                    source_id=source_id,
                    basefolder_path=basefolder_path,
                    created_by=created_by,
                    updated_by=updated_by,
                )
                log(f"[S3][MAPPING] Updated multi-file mapping for connection {connection_id}")
                return
            
            log(f"[S3][MAPPING]  Mapping validation passed for connection {connection_id}")
        except ValueError:
            raise
        except Exception as e:
            log(f" Error validating/persisting S3 mapping: {e}")
            # Don't raise - allow migration to continue if mapping validation fails
            log(f"[S3][MAPPING]  Mapping validation failed (non-blocking): {e}")
    
    @staticmethod
    async def list_tables(data: AmazonS3ListingTables):
        """
        List files in S3 bucket, optionally filtered by prefix and file type.
        
        This method only lists files without reading their contents or inferring schema.
        Returns file metadata (table name, file_key, last_modified) for file mapping UI.
        """
        log("=" * 80)
        log("[S3][LIST_FILES] Starting file listing (no schema inference)...")
        log("=" * 80)
        log(f"[S3][LIST_FILES] Connection ID: {data.connection_id}")
        log(f"[S3][LIST_FILES]  This will only list files, NOT read contents or infer columns")
        
        s3_config = data.AmazonS3
        aws_access_key_id = s3_config.get("aws_access_key_id")
        aws_secret_access_key = s3_config.get("aws_secret_access_key")
        s3_bucket = (s3_config.get("s3_bucket") or "").strip()
        aws_region = s3_config.get("aws_region")
        s3_base_prefix = (s3_config.get("s3_base_prefix") or "").strip()
        s3_folder_prefix = normalize_s3_folder_prefix(s3_base_prefix)
        include_subfolders = _as_bool(s3_config.get("include_subfolders"), default=False)
        file_type = (s3_config.get("file_type") or "").lower()
        file_mapping_mode = (s3_config.get("file_mapping_mode") or "single_table_per_file").strip()
        multi_files_prefix = (s3_config.get("multi_files_prefix") or "").strip()
        multi_files_table_name = sanitize_identifier((s3_config.get("multi_files_table_name") or "").strip())
        dest_config = s3_config.get("destination_config")
        log(f"[S3][LIST_FILES][DEBUG] destination_config provided: {bool(dest_config)}")
        if multi_files_prefix:
            log(f"[S3][LIST_FILES]    multi_files_prefix is deprecated and ignored (pattern='{multi_files_prefix}')")

        single_file_map = s3_config.get("single_file_table_mapping") or {}
        if not isinstance(single_file_map, dict):
            single_file_map = {}
        if single_file_map:
            single_file_map = {
                str(k): sanitize_identifier(str(v).strip()) if str(v).strip() else table_from_file_key(str(k))
                for k, v in single_file_map.items()
            }

        existing_mapping = None
        if data.connection_id:
            existing_mapping = get_s3_mapping(data.connection_id)

        # Only restore single-file mapping if the stored mode is also single-table-per-file.
        if (
            not single_file_map
            and file_mapping_mode == "single_table_per_file"
            and existing_mapping
            and (existing_mapping.get("file_mapping_mode") or "").strip() == "single_table_per_file"
        ):
            single_file_map = existing_mapping.get("single_file_table_mapping") or {}
            if not isinstance(single_file_map, dict):
                single_file_map = {}
            else:
                single_file_map = {
                    str(k): sanitize_identifier(str(v).strip()) if str(v).strip() else table_from_file_key(str(k))
                    for k, v in single_file_map.items()
                }
        
        log(f"[S3][LIST_FILES] Config:")
        log(f"   - Bucket: {s3_bucket}")
        log(f"   - Base prefix: {s3_base_prefix or '(root)'}")
        log(f"   - Include subfolders: {include_subfolders}")
        log(f"   - File type (content-validated): {file_type or '(all)'}")
        log(f"   - Region: {aws_region or '(auto-detect)'}")
        
        missing_fields = []
        if not aws_access_key_id:
            missing_fields.append("aws_access_key_id")
        if not aws_secret_access_key:
            missing_fields.append("aws_secret_access_key")
        if not s3_bucket:
            missing_fields.append("s3_bucket")
        if missing_fields:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required S3 field(s): {', '.join(missing_fields)}"
            )

        bucket_validation_error = validate_s3_bucket_name(s3_bucket)
        if bucket_validation_error:
            raise HTTPException(status_code=422, detail=bucket_validation_error)
        
        try:
            if not aws_region:
                log(f"[S3][LIST_FILES] Auto-detecting bucket region...")
                aws_region = detect_s3_bucket_region(
                    s3_bucket, aws_access_key_id, aws_secret_access_key
                )
                log(f"[S3][LIST_FILES]    Detected region: {aws_region}")
            
            s3 = s3_client(aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket, auto_detect_region=False)
            log(f"[S3][LIST_FILES] Listing files from bucket...")
            all_keys = _list_s3_keys_by_scope(
                s3=s3,
                s3_bucket=s3_bucket,
                s3_folder_prefix=s3_folder_prefix,
                include_subfolders=include_subfolders,
            )
            # Filter out folder markers (keys ending with '/')
            all_keys = [k for k in all_keys if str(k).strip() and not str(k).strip().endswith("/")]
            log(f"[S3][LIST_FILES]    Found {len(all_keys)} total files")
            
            keys_for_mapping = all_keys
            log(f"[S3][LIST_FILES]    Using all {len(keys_for_mapping)} files in scope (no extension/pattern filtering)")
            # Deterministic ordering for UI consistency: folder + file name.
            keys_for_mapping = sorted(
                keys_for_mapping,
                key=lambda k: str(k or "").strip().lower(),
            )

            if not keys_for_mapping:
                # Give specific feedback when folder path is wrong.
                if s3_base_prefix:
                    try:
                        probe = s3.list_objects_v2(
                            Bucket=s3_bucket,
                            Prefix=s3_folder_prefix,
                            MaxKeys=1,
                        )
                        if probe.get("KeyCount", 0) == 0:
                            raise HTTPException(
                                status_code=404,
                                detail=(
                                    f"No files found under `s3_base_prefix`='{s3_base_prefix}'. "
                                    "Verify folder path and trailing segments."
                                ),
                            )
                    except HTTPException:
                        raise
                log(f"[S3][LIST_FILES]  No files found matching criteria")
                return {"tables": [], "files_count": 0}
            
            log(f"[S3][LIST_FILES] Processing files to extract metadata...")
            tables = []
            used_table_names = set()
            existing_file_metadata = {}
            basename_counts = _build_file_basename_counts(keys_for_mapping)

            existing_sf_tables = _fetch_snowflake_tables_from_config(dest_config)
            log(
                f"[S3][LIST_FILES][DEBUG] Snowflake table lookup: "
                f"{'ok' if existing_sf_tables is not None else 'skipped/failed'}"
            )
            if existing_sf_tables is not None:
                log(f"[S3][LIST_FILES]    Snowflake tables in schema: {len(existing_sf_tables)}")

            mapped_lookup = {}
            if file_mapping_mode == "single_table_per_file" and single_file_map:
                for mapped_file_key, mapped_table in single_file_map.items():
                    file_key = str(mapped_file_key).strip()
                    if not file_key:
                        continue
                    mapped_table_name = sanitize_identifier(str(mapped_table).strip())
                    mapped_lookup[file_key] = mapped_table_name
            elif file_mapping_mode == "multi_files_single_table":
                # Only pre-map files if a stored multi-file mapping exists.
                table_to_files = {}
                if existing_mapping and (existing_mapping.get("file_mapping_mode") or "").strip() == "multi_files_single_table":
                    table_to_files = existing_mapping.get("table_to_files_mapping") or {}
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

            # Load existing table/file metadata for this connection so UI can lock
            # table renames for already migrated files.
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
                    log(f"[S3][LIST_FILES]  Could not load existing file metadata: {existing_meta_err}")
            
            for key in keys_for_mapping:
                existing_meta = existing_file_metadata.get(key) or {}
                locked_table_name = sanitize_identifier(str(existing_meta.get("table") or "").strip())
                is_migrated = bool(existing_meta.get("is_migrated"))
                mapped_table = mapped_lookup.get(key)
                log(
                    f"[S3][LIST_FILES][DEBUG] file_key='{key}' "
                    f"is_migrated={is_migrated} "
                    f"locked_table_name='{locked_table_name or ''}' "
                    f"mapped_table_pre='{mapped_table or ''}'"
                )
                if not mapped_table and file_mapping_mode == "single_table_per_file":
                    mapped_table = _resolve_single_file_mapped_name(
                        file_key=key,
                        single_file_map=single_file_map,
                        basename_counts=basename_counts,
                    )
                    if mapped_table:
                        log(
                            f"[S3][LIST_FILES][DEBUG] file_key='{key}' "
                            f"mapped_table_resolved='{mapped_table}'"
                        )
                snowflake_table_exists = None
                if existing_sf_tables is not None:
                    # For non-migrated rows, use mapped_table (ignore locked_table_name).
                    if is_migrated and locked_table_name:
                        snowflake_table_exists = locked_table_name.upper() in existing_sf_tables
                    elif mapped_table:
                        snowflake_table_exists = str(mapped_table).upper() in existing_sf_tables
                log(
                    f"[S3][LIST_FILES][DEBUG] file_key='{key}' "
                    f"snowflake_table_exists={snowflake_table_exists}"
                )

                lock_table_name = bool(
                    is_migrated
                    and locked_table_name
                    and (snowflake_table_exists is True)
                )
                log(
                    f"[S3][LIST_FILES][DEBUG] file_key='{key}' "
                    f"lock_table_name={lock_table_name}"
                )

                if (
                    mapped_table
                    and existing_sf_tables is not None
                    and snowflake_table_exists is False
                    and is_migrated
                ):
                    # Only clear mapping when the file was already migrated and the target table
                    # is missing. For non-migrated files, keep the user mapping visible.
                    log(
                        f"[S3][LIST_FILES][DEBUG] file_key='{key}' "
                        f"clearing mapped_table='{mapped_table}' (missing in Snowflake; migrated)"
                    )
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
                try:
                    head = s3.head_object(Bucket=s3_bucket, Key=key)
                    last_modified = head["LastModified"].isoformat()
                except Exception as e:
                    log(f"[S3][LIST_FILES]    Could not get metadata for {key}: {e}")
                    last_modified = None
                
                tables.append({
                    "table": table_name,
                    "file_key": key,
                    "last_modified": last_modified,
                    "already_mapped": bool(mapped_table),
                    "mapped_table": mapped_table,
                    # Indicator for frontend: migrated file/table mapping is locked.
                    "is_migrated": is_migrated,
                    "table_name_locked": bool(lock_table_name),
                    "locked_table_name": locked_table_name if lock_table_name else None,
                    "locked_primary_keys": existing_meta.get("primary_keys") if lock_table_name else [],
                    "locked_last_synced": existing_meta.get("last_synced") or None,
                })
            
            log(f"[S3][LIST_FILES] Completed file listing")
            log(f"[S3][LIST_FILES]    Listed files: {len(tables)}")
            log(f"[S3][LIST_FILES]    Total files: {len(keys_for_mapping)}")
            
            return {
                "tables": tables,
                "files_count": len(keys_for_mapping)
            }
        except ClientError as e:
            status_code, detail = map_s3_error_to_http_detail(e, operation="S3 file listing")
            log(f"[S3][LIST_FILES]  ClientError mapped: status={status_code}, detail={detail}")
            raise HTTPException(status_code=status_code, detail=detail)
        except HTTPException:
            raise
        except Exception as e:
            status_code, detail = map_s3_error_to_http_detail(e, operation="S3 file listing")
            log(f"[S3][LIST_FILES]  Error mapped: status={status_code}, detail={detail}")
            raise HTTPException(status_code=status_code, detail=detail)
    
    @staticmethod
    async def list_tables_with_schema(data: AmazonS3ListingTables):
        """
        List S3-backed logical tables AND infer their column schema from a sample of each file.
        
        This method reads sample data from files to infer column structure.
        Returns tables with columns for writing to SourceTableSelection.table_fields.
        """
        log("=" * 80)
        log("[S3][LIST_SCHEMA] Starting table listing with schema inference...")
        log("=" * 80)
        log(f"[S3][LIST_SCHEMA] Connection ID: {data.connection_id}")
        log(f"[S3][LIST_SCHEMA]  This will read sample data from files to infer columns")
        
        s3_config = data.AmazonS3
        masked_config = dict(s3_config) if isinstance(s3_config, dict) else {}
        if "aws_secret_access_key" in masked_config and masked_config["aws_secret_access_key"]:
            masked_config["aws_secret_access_key"] = "***MASKED***"
        log(f"S3_CONFIG------ {masked_config}")
        
        aws_access_key_id = s3_config.get("aws_access_key_id")
        aws_secret_access_key = s3_config.get("aws_secret_access_key")
        s3_bucket = (s3_config.get("s3_bucket") or "").strip()
        aws_region = s3_config.get("aws_region")
        s3_base_prefix = s3_config.get("s3_base_prefix", "")
        s3_folder_prefix = normalize_s3_folder_prefix(s3_base_prefix)
        include_subfolders = _as_bool(s3_config.get("include_subfolders"), default=False)
        file_type = (s3_config.get("file_type") or "").lower()
        file_mapping_mode = (s3_config.get("file_mapping_mode") or "single_table_per_file").strip()
        multi_files_prefix = (s3_config.get("multi_files_prefix") or "").strip()
        multi_files_table_name = sanitize_identifier((s3_config.get("multi_files_table_name") or "").strip())
        if multi_files_prefix:
            log(f"[S3][LIST_SCHEMA]    multi_files_prefix is deprecated and ignored (pattern='{multi_files_prefix}')")
        
        log(f"[S3][LIST_SCHEMA] Config:")
        log(f"   - Bucket: {s3_bucket}")
        log(f"   - Base prefix: {s3_base_prefix or '(root)'}")
        log(f"   - Include subfolders: {include_subfolders}")
        log(f"   - File type (content-validated): {file_type or '(all)'}")
        log(f"   - Region: {aws_region or '(auto-detect)'}")
        delimiter = s3_config.get("delimiter")
        quotechar = s3_config.get("quotechar")
        skipheaderlines = s3_config.get("skipheaderlines") or 0
        json_mode = s3_config.get("json_mode")
        xml_mode = s3_config.get("xml_mode")
        escape_char = s3_config.get("escape_char")
        null_sequence = s3_config.get("null_sequence")
        skip_footer_lines = s3_config.get("skip_footer_lines")
        line_separator = s3_config.get("line_separator")
        
        if file_type == "tsv":
            # Explicit blank TSV options mean TSV defaults (or disabled quoting),
            # not the values left in the CSV form fields.
            delimiter = s3_config.get("tsv_delimiter", delimiter)
            quotechar = s3_config.get("tsv_quotechar", quotechar)
            skipheaderlines = s3_config.get("tsv_skipheaderlines", skipheaderlines)
            escape_char = s3_config.get("tsv_escape_char", escape_char)
            null_sequence = s3_config.get("tsv_null_sequence", null_sequence)
            skip_footer_lines = s3_config.get("tsv_skip_footer_lines", skip_footer_lines)
        
        load_method = s3_config.get("load_method", "upsert_file_row")
        # Ensure load_method is a string
        if not isinstance(load_method, str):
            log(f"[S3][LIST_SCHEMA]  Invalid load_method type: {type(load_method)}, defaulting to 'upsert_file_row'")
            load_method = "upsert_file_row"
        log(f"[S3][LIST_SCHEMA] Load method: {load_method}")
        
       #  Extract custom_primary_key (handle both dict and JSON string)
        custom_primary_key = s3_config.get("custom_primary_key")
        if isinstance(custom_primary_key, str):
            try:
                custom_primary_key = json.loads(custom_primary_key)
            except Exception:
                custom_primary_key = {}
        if load_method == "upsert_custom_key":
            if not isinstance(custom_primary_key, dict) or not custom_primary_key:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "custom_primary_key must be provided as a non-empty dict in the request "
                        "when load_method is 'upsert_custom_key'"
                    ),
                )


        missing_fields = []
        if not aws_access_key_id:
            missing_fields.append("aws_access_key_id")
        if not aws_secret_access_key:
            missing_fields.append("aws_secret_access_key")
        if not s3_bucket:
            missing_fields.append("s3_bucket")
        if missing_fields:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required S3 field(s): {', '.join(missing_fields)}"
            )

        bucket_validation_error = validate_s3_bucket_name(s3_bucket)
        if bucket_validation_error:
            raise HTTPException(status_code=422, detail=bucket_validation_error)

        if not aws_region:
            aws_region = detect_s3_bucket_region(
                s3_bucket, aws_access_key_id, aws_secret_access_key
            )

        s3 = s3_client(aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket, auto_detect_region=False)
        log(f"[S3][LIST_SCHEMA] Step 1: Listing files from bucket...")
        all_keys = _list_s3_keys_by_scope(
            s3=s3,
            s3_bucket=s3_bucket,
            s3_folder_prefix=s3_folder_prefix,
            include_subfolders=include_subfolders,
        )
        # Filter out folder markers (keys ending with '/')
        all_keys = [k for k in all_keys if str(k).strip() and not str(k).strip().endswith("/")]
        log(f"[S3][LIST_SCHEMA]    Found {len(all_keys)} total files")
        
        keys_for_mapping = all_keys
        log(f"[S3][LIST_SCHEMA]    Using all {len(keys_for_mapping)} files in scope (no extension/pattern filtering)")
        # Deterministic ordering for UI consistency: folder + file name.
        keys_for_mapping = sorted(
            keys_for_mapping,
            key=lambda k: str(k or "").strip().lower(),
        )

        # An empty listing must still resolve missing mappings so persistence
        # can prune stale source tables after the last file is removed.
        basename_counts = _build_file_basename_counts(keys_for_mapping)

        single_file_map = s3_config.get("single_file_table_mapping") or {}
        if isinstance(single_file_map, str):
            try:
                single_file_map = json.loads(single_file_map)
            except Exception:
                single_file_map = {}
        if not isinstance(single_file_map, dict):
            single_file_map = {}
        # ========================================================================
        # CRITICAL: Filter files based on single_file_table_mapping
        # When mapping is provided, ONLY process files that are explicitly mapped
        # ========================================================================
        file_mapping_mode = (s3_config.get("file_mapping_mode") or file_mapping_mode or "single_table_per_file").strip()
        prune_missing_tables = False
        stale_single_table_names: set[str] = set()
        
        if file_mapping_mode == "single_table_per_file" and single_file_map:
            log(f"[S3][LIST_SCHEMA]    File mapping mode: {file_mapping_mode}")
            log(f"[S3][LIST_SCHEMA]    Using {len(single_file_map)} custom table name mappings")
            log(f"[S3][LIST_SCHEMA]    Filtering files to only process mapped files...")
            
            # Build set of files to process based on mapping
            mapped_files = set()
            non_existent_files = []
            
            for mapped_file_key in single_file_map.keys():
                # Support both full path (e.g., "Main/10_Address.csv") and filename only (e.g., "10_Address.csv")
                matched = False
                for key in keys_for_mapping:
                    # Match if: exact match OR ends with the mapped filename
                    if key == mapped_file_key or key.endswith("/" + mapped_file_key):
                        mapped_files.add(key)
                        matched = True
                        log(f"[S3][LIST_SCHEMA]      Matched: {mapped_file_key}  {key}")
                        break
                
                if not matched:
                    non_existent_files.append(mapped_file_key)
            
            # Warn about non-existent files in mapping
            if non_existent_files:
                log(f"[S3][LIST_SCHEMA]    WARNING: {len(non_existent_files)} file(s) in mapping not found in bucket:")
                for nef in non_existent_files:
                    log(f"[S3][LIST_SCHEMA]      Not found: {nef}")
                    mapped_table = single_file_map.get(nef)
                    if mapped_table:
                        normalized_table = sanitize_identifier(str(mapped_table).strip())
                        if normalized_table:
                            stale_single_table_names.add(normalized_table)
            # Only prune SourceTableSelection rows for mappings whose files are truly missing in S3.
            # Do not prune just because user temporarily unchecked/unmapped tables.
            prune_missing_tables = bool(stale_single_table_names)
            
            # Update keys_for_mapping to only include mapped files
            if mapped_files:
                keys_for_mapping = sorted(
                    mapped_files,
                    key=lambda k: str(k or "").strip().lower(),
                )
                log(f"[S3][LIST_SCHEMA]    Filtered to {len(keys_for_mapping)} files based on mapping")
            else:
                # All mapped files are missing from S3. Keep going so stale mappings can be pruned.
                log(f"[S3][LIST_SCHEMA]    WARNING: No files matched the mapping; continuing with empty schema result")
                log(f"[S3][LIST_SCHEMA]    Mapped files: {list(single_file_map.keys())}")
                log(f"[S3][LIST_SCHEMA]    Available files: {keys_for_mapping[:10]}...")  # Show first 10
                keys_for_mapping = []
        elif file_mapping_mode == "single_table_per_file" and not single_file_map:
            # Empty mapping with single_table_per_file mode is an error
            log(f"[S3][LIST_SCHEMA]    ERROR: file_mapping_mode is 'single_table_per_file' but single_file_table_mapping is empty!")
            raise HTTPException(
                status_code=400,
                detail=(
                    "Update schema failed: single_file_table_mapping is required for single_table_per_file mode."
                ),
            )
        elif single_file_map:
            # Mapping provided but mode is not single_table_per_file - use mapping anyway
            log(f"[S3][LIST_SCHEMA]    Using {len(single_file_map)} custom table name mappings (mode: {file_mapping_mode})")

        # ========================================================================
        # CRITICAL: Validate no duplicate table names in mapping
        # Snowflake does NOT allow two tables with the same name in the same schema
        # ========================================================================
        if single_file_map:
            table_name_counts = {}
            for file_key, table_name in single_file_map.items():
                table_name_upper = table_name.upper()  # Snowflake is case-insensitive
                if table_name_upper not in table_name_counts:
                    table_name_counts[table_name_upper] = []
                table_name_counts[table_name_upper].append(file_key)
            
            # Check for duplicates
            duplicates = {name: files for name, files in table_name_counts.items() if len(files) > 1}
            if duplicates:
                log(f"[S3][LIST_SCHEMA]    ERROR: Duplicate table names detected in mapping!")
                log(f"[S3][LIST_SCHEMA]    Snowflake does NOT allow two tables with the same name in the same schema")
                for table_name, files in duplicates.items():
                    log(f"[S3][LIST_SCHEMA]      Table '{table_name}' is mapped from {len(files)} files:")
                    for f in files:
                        log(f"[S3][LIST_SCHEMA]       - {f}")
                # IMPORTANT: Do not mutate stored mapping on validation failure.
                # Keep already-migrated mappings intact and fail fast instead.
                log(
                    "[S3][LIST_SCHEMA]    Preserving existing mapping as-is "
                    "(duplicate validation failed; no auto-prune performed)"
                )

                summary_msg = (
                    f"Update schema failed: duplicate mapped table names detected ({', '.join(duplicates.keys())}). "
                    "Each file must map to a unique table name."
                )
                try:
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file="single_table_per_file",
                        message=summary_msg,
                    )
                except Exception as dup_log_err:
                    log(f"[S3][LIST_SCHEMA]    Failed to write duplicate-mapping log: {dup_log_err}")

                raise HTTPException(status_code=400, detail=summary_msg)



        # ========================================================================
        # Support multi_files_single_table grouping
        # ========================================================================
        grouped_files_by_table = {}
        if file_mapping_mode == "multi_files_single_table":
            # Multi-file mode is scope-driven. Build a transient single table group from resolved keys.
            dynamic_table_name = (
                multi_files_table_name
                or sanitize_identifier(suggest_common_table_name(keys_for_mapping))
                or "MULTI_FILES_TABLE"
            )
            grouped_files_by_table = {
                dynamic_table_name: [str(k).strip() for k in keys_for_mapping if str(k).strip()]
            }
            log(
                f"[S3][LIST_SCHEMA]    Built dynamic multi-file group from resolved keys "
                f"for table '{dynamic_table_name}' ({len(grouped_files_by_table.get(dynamic_table_name, []))} files)"
            )
            # In multi-file single-table mode, stale SourceTableSelection rows must be pruned
            # when the effective mapping changes (e.g., PRODUCTS -> EMPLOYEE remap).
            prune_missing_tables = bool(grouped_files_by_table)
        
        candidate_files_count = len(keys_for_mapping)
        log(f"[S3][LIST_SCHEMA] Step 2: Processing files to infer schema...")
        tables: list[Dict[str, Any]] = []
        parse_errors: list[str] = []
        invalid_single_mode_files: set[str] = set()
        invalid_multi_mode_files: Dict[str, set[str]] = {}
        used_table_names = set()
        allowed_keys = {str(k).strip() for k in keys_for_mapping if str(k).strip()}
        
        # Scenario A: Grouped mapping exists (Multi-File Single Table)
        if grouped_files_by_table:
            for table_name, mapping_info in grouped_files_by_table.items():
                table_name = sanitize_identifier(str(table_name).strip())
                files = mapping_info if isinstance(mapping_info, list) else []
                # Respect current file_type + pattern filters by intersecting with allowed keys.
                files = [f for f in (files or []) if str(f).strip() in allowed_keys]
                
                if not files:
                    continue
                
                log(f"[S3][LIST_SCHEMA]    Processing grouped table: {table_name} ({len(files)} files)")
                
                # Use the first file for schema inference (or sample a few)
                sample_file = files[0]
                df_sample = None
                last_modified_iso = None

                # Enforce user-configured CSV/TSV attributes before schema inference.
                if file_type in ("csv", "tsv"):
                    ok, reason = is_file_compatible_with_type(
                        s3=s3,
                        bucket=s3_bucket,
                        key=sample_file,
                        file_type=file_type,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        escape_char=escape_char,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                    )
                    if not ok:
                        err_msg = _build_schema_validation_error_message(
                            sample_file,
                            file_type,
                            reason or "File does not match configured CSV/TSV options.",
                        )
                        parse_errors.append(err_msg)
                        invalid_multi_mode_files.setdefault(table_name, set()).add(sample_file)
                        log(f"[S3][LIST_SCHEMA]      {err_msg}")
                        _log_schema_validation_failure_to_table(
                            cmp_id=data.cmp_id,
                            connection_id=data.connection_id,
                            user_id=data.user_id,
                            job_name=getattr(data, "job_name", None),
                            table_or_file=table_name,
                            message=err_msg,
                        )
                        continue

                # Pre-validate binary file signatures so detailed errors reach UI logs.
                if file_type in ("avro", "parquet"):
                    try:
                        pre_obj = s3.get_object(Bucket=s3_bucket, Key=sample_file)
                        pre_content = pre_obj["Body"].read()
                        validation_error = _binary_format_validation_error(file_type, pre_content)
                        if validation_error:
                            err_msg = _build_schema_validation_error_message(sample_file, file_type, validation_error)
                            parse_errors.append(err_msg)
                            log(f"[S3][LIST_SCHEMA]      {err_msg}")
                            _log_schema_validation_failure_to_table(
                                cmp_id=data.cmp_id,
                                connection_id=data.connection_id,
                                user_id=data.user_id,
                                job_name=getattr(data, "job_name", None),
                                table_or_file=table_name,
                                message=err_msg,
                            )
                            continue
                    except Exception as e:
                        err_msg = _build_schema_validation_error_message(sample_file, file_type, str(e))
                        parse_errors.append(err_msg)
                        log(f"[S3][LIST_SCHEMA]      {err_msg}")
                        _log_schema_validation_failure_to_table(
                            cmp_id=data.cmp_id,
                            connection_id=data.connection_id,
                            user_id=data.user_id,
                            job_name=getattr(data, "job_name", None),
                            table_or_file=table_name,
                            message=err_msg,
                        )
                        continue
                
                try:
                    # Logic to read sample...
                    for chunk_df, chunk_num, is_last, last_modified, total_rows in read_s3_file_in_chunks(
                        s3=s3, bucket=s3_bucket, key=sample_file, file_type=file_type,
                        delimiter=delimiter, quotechar=quotechar, skipheaderlines=skipheaderlines,
                        json_mode=json_mode, xml_mode=xml_mode, escape_char=escape_char,
                        null_sequence=null_sequence, skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator, chunk_size=50000
                    ):
                        if chunk_df is not None and not chunk_df.empty:
                            df_sample = chunk_df
                            last_modified_iso = last_modified.isoformat() if last_modified else None
                            break
                        if is_last: break
                except XMLNoBusinessColumnsError as e:
                    if (file_type or "").lower() != "xml":
                        raise
                    err_msg = _build_schema_validation_error_message(sample_file, file_type, str(e))
                    parse_errors.append(err_msg)
                    invalid_multi_mode_files.setdefault(table_name, set()).add(sample_file)
                    log(f"[S3][LIST_SCHEMA]      {err_msg}")
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file=table_name,
                        message=err_msg,
                    )
                    continue
                except Exception as e:
                    err_msg = _build_schema_validation_error_message(sample_file, file_type, str(e))
                    parse_errors.append(err_msg)
                    log(f"[S3][LIST_SCHEMA]      {err_msg}")
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file=table_name,
                        message=err_msg,
                    )
                    continue

                if df_sample is None or df_sample.empty:
                    err_msg = _build_schema_validation_error_message(sample_file, file_type)
                    parse_errors.append(err_msg)
                    log(f"[S3][LIST_SCHEMA]      {err_msg}")
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file=table_name,
                        message=err_msg,
                    )
                    continue

                columns = infer_columns_from_df(
                    df_sample, load_method=load_method, table_name=table_name,
                    internal_name=table_name,
                    custom_primary_key=custom_primary_key
                )

                compatible_group_files = [sample_file]
                for grouped_file in files:
                    grouped_file = str(grouped_file).strip()
                    if not grouped_file or grouped_file == sample_file:
                        continue
                    is_valid_group_file, _, group_err = validate_file_structure_matches(
                        s3=s3,
                        s3_bucket=s3_bucket,
                        file_key=grouped_file,
                        file_type=file_type,
                        expected_columns=columns,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                        enforce_type_compatibility=True,
                    )
                    if is_valid_group_file:
                        compatible_group_files.append(grouped_file)
                    else:
                        mismatch_msg = (
                            f"Update schema failed: File '{grouped_file}' has different structure/type "
                            f"for multi-file table '{table_name}'. {group_err}"
                        )
                        parse_errors.append(mismatch_msg)
                        invalid_multi_mode_files.setdefault(table_name, set()).add(grouped_file)
                        log(f"[S3][LIST_SCHEMA]      {mismatch_msg}")
                
                tables.append({
                    "table": table_name,
                    "file_key": sample_file, # Representative file
                    "last_modified": last_modified_iso,
                    "columns": columns,
                    "grouped_files": compatible_group_files # Only compatible files in group
                })
        
        if file_mapping_mode == "multi_files_single_table" and not grouped_files_by_table:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Update schema failed: no files resolved for multi-file mode. "
                    "Verify S3 base prefix and include_subfolders settings."
                ),
            )

        # Scenario B: Single file per table (Fallback/Default)
        if file_mapping_mode != "multi_files_single_table":
            basename_counts = _build_file_basename_counts(keys_for_mapping)
            processed_count = 0
            for key in keys_for_mapping:
                internal_name = table_from_file_key(key)
                mapped_table_name = _resolve_single_file_mapped_name(
                    file_key=key,
                    single_file_map=single_file_map,
                    basename_counts=basename_counts,
                )
                table_name = _unique_table_name_for_file(
                    file_key=key,
                    mapped_table_name=mapped_table_name or internal_name,
                    used_names=used_table_names,
                )
                processed_count += 1

                log(f"[S3][LIST_SCHEMA]   [{processed_count}/{len(keys_for_mapping)}] Processing: {key}")
                df_sample = None
                last_modified_iso = None

                # Enforce user-configured CSV/TSV attributes before schema inference.
                if file_type in ("csv", "tsv"):
                    ok, reason = is_file_compatible_with_type(
                        s3=s3,
                        bucket=s3_bucket,
                        key=key,
                        file_type=file_type,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        escape_char=escape_char,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                    )
                    if not ok:
                        err_msg = _build_schema_validation_error_message(
                            key,
                            file_type,
                            reason or "File does not match configured CSV/TSV options.",
                        )
                        parse_errors.append(err_msg)
                        log(f"[S3][LIST_SCHEMA]      {err_msg}")
                        if file_mapping_mode == "single_table_per_file" and single_file_map:
                            invalid_single_mode_files.add(str(key).strip())
                        _log_schema_validation_failure_to_table(
                            cmp_id=data.cmp_id,
                            connection_id=data.connection_id,
                            user_id=data.user_id,
                            job_name=getattr(data, "job_name", None),
                            table_or_file=table_name,
                            message=err_msg,
                        )
                        continue

                # Pre-validate binary file signatures so detailed errors reach UI logs.
                if file_type in ("avro", "parquet"):
                    try:
                        pre_obj = s3.get_object(Bucket=s3_bucket, Key=key)
                        pre_content = pre_obj["Body"].read()
                        validation_error = _binary_format_validation_error(file_type, pre_content)
                        if validation_error:
                            err_msg = _build_schema_validation_error_message(key, file_type, validation_error)
                            parse_errors.append(err_msg)
                            log(f"[S3][LIST_SCHEMA]      {err_msg}")
                            if file_mapping_mode == "single_table_per_file" and single_file_map:
                                invalid_single_mode_files.add(str(key).strip())
                            _log_schema_validation_failure_to_table(
                                cmp_id=data.cmp_id,
                                connection_id=data.connection_id,
                                user_id=data.user_id,
                                job_name=getattr(data, "job_name", None),
                                table_or_file=table_name,
                                message=err_msg,
                            )
                            continue
                    except Exception as e:
                        err_msg = _build_schema_validation_error_message(key, file_type, str(e))
                        parse_errors.append(err_msg)
                        log(f"[S3][LIST_SCHEMA]      {err_msg}")
                        if file_mapping_mode == "single_table_per_file" and single_file_map:
                            invalid_single_mode_files.add(str(key).strip())
                        _log_schema_validation_failure_to_table(
                            cmp_id=data.cmp_id,
                            connection_id=data.connection_id,
                            user_id=data.user_id,
                            job_name=getattr(data, "job_name", None),
                            table_or_file=table_name,
                            message=err_msg,
                        )
                        continue

                try:
                    for chunk_df, chunk_num, is_last, last_modified, total_rows in read_s3_file_in_chunks(
                        s3=s3, bucket=s3_bucket, key=key, file_type=file_type,
                        delimiter=delimiter, quotechar=quotechar, skipheaderlines=skipheaderlines,
                        json_mode=json_mode, xml_mode=xml_mode, escape_char=escape_char,
                        null_sequence=null_sequence, skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator, chunk_size=50000
                    ):
                        if chunk_df is not None and not chunk_df.empty:
                            df_sample = chunk_df
                            last_modified_iso = last_modified.isoformat() if last_modified else None
                            break
                        if is_last: break
                except XMLNoBusinessColumnsError as e:
                    if (file_type or "").lower() != "xml":
                        raise
                    err_msg = _build_schema_validation_error_message(key, file_type, str(e))
                    parse_errors.append(err_msg)
                    log(f"[S3][LIST_SCHEMA]      {err_msg}")
                    if file_mapping_mode == "single_table_per_file" and single_file_map:
                        invalid_single_mode_files.add(str(key).strip())
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file=table_name,
                        message=err_msg,
                    )
                    continue
                except Exception as e:
                    err_msg = _build_schema_validation_error_message(key, file_type, str(e))
                    parse_errors.append(err_msg)
                    log(f"[S3][LIST_SCHEMA]      {err_msg}")
                    if file_mapping_mode == "single_table_per_file" and single_file_map:
                        invalid_single_mode_files.add(str(key).strip())
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file=table_name,
                        message=err_msg,
                    )
                    continue

                if df_sample is None or df_sample.empty:
                    err_msg = _build_schema_validation_error_message(key, file_type)
                    parse_errors.append(err_msg)
                    log(f"[S3][LIST_SCHEMA]      {err_msg}")
                    if file_mapping_mode == "single_table_per_file" and single_file_map:
                        invalid_single_mode_files.add(str(key).strip())
                    _log_schema_validation_failure_to_table(
                        cmp_id=data.cmp_id,
                        connection_id=data.connection_id,
                        user_id=data.user_id,
                        job_name=getattr(data, "job_name", None),
                        table_or_file=table_name,
                        message=err_msg,
                    )
                    continue

                columns = infer_columns_from_df(
                    df_sample, load_method=load_method, table_name=table_name,
                    internal_name=internal_name,
                    custom_primary_key=custom_primary_key
                )

                tables.append({
                    "table": table_name,
                    "file_key": key,
                    "last_modified": last_modified_iso,
                    "columns": columns,
                })

        # Auto-prune invalid files from single_file_table_mapping so future runs don't keep retrying them.
        if file_mapping_mode == "single_table_per_file" and single_file_map and invalid_single_mode_files:
            invalid_basenames = {k.split("/")[-1] for k in invalid_single_mode_files if str(k).strip()}
            pruned_single_map = {}
            removed_keys = []
            for map_key, mapped_table in single_file_map.items():
                mk = str(map_key).strip()
                mk_basename = mk.split("/")[-1] if mk else ""
                if mk in invalid_single_mode_files or mk_basename in invalid_basenames:
                    removed_keys.append(mk)
                    continue
                pruned_single_map[map_key] = mapped_table
            if removed_keys:
                try:
                    save_s3_mapping(
                        data.connection_id,
                        "single_table_per_file",
                        pruned_single_map,
                        {},
                        raise_on_error=True,
                    )
                    log(
                        f"[S3][LIST_SCHEMA]    Removed {len(removed_keys)} invalid file mapping(s): "
                        f"{sorted(set(removed_keys))}"
                    )
                except Exception as mapping_error:
                    log(
                        f"[S3][LIST_SCHEMA]    Failed to prune invalid file mappings: {mapping_error}"
                    )

        # Auto-prune incompatible files from multi-file mapping so future runs don't retry them.
        if file_mapping_mode == "multi_files_single_table" and invalid_multi_mode_files:
            # IMPORTANT: Preserve existing multi-file mapping on validation failure.
            # Never remove whole table mappings just because one newly discovered file is incompatible.
            impacted_tables = sorted({
                str(tbl).strip()
                for tbl, bad_files in invalid_multi_mode_files.items()
                if str(tbl).strip() and any(str(k).strip() for k in (bad_files or set()))
            })
            impacted_files = sorted({
                str(k).strip()
                for bad_files in invalid_multi_mode_files.values()
                for k in (bad_files or set())
                if str(k).strip()
            })
            if impacted_tables:
                log(
                    f"[S3][LIST_SCHEMA]    Found incompatible files for {len(impacted_tables)} table(s) "
                    f"{impacted_tables}; incompatible file count={len(impacted_files)}. "
                    "Preserving current multi-file mapping (no auto-prune on validation failure)."
                )

        log(f"[S3][LIST_SCHEMA] Step 3: Completed schema inference")
        log(f"[S3][LIST_SCHEMA]    Total unique tables: {len(tables)}")

        tables_with_columns = sum(1 for t in tables if t.get("columns"))
        compatible_file_keys = set()
        for t in tables:
            grouped = t.get("grouped_files")
            if isinstance(grouped, list):
                compatible_file_keys.update([str(k).strip() for k in grouped if str(k).strip()])
            else:
                fk = str(t.get("file_key") or "").strip()
                if fk:
                    compatible_file_keys.add(fk)
        incompatible_file_keys = set([str(k).strip() for k in invalid_single_mode_files if str(k).strip()])
        for bad_files in invalid_multi_mode_files.values():
            incompatible_file_keys.update([str(k).strip() for k in (bad_files or set()) if str(k).strip()])

        log(
            f"[S3][LIST_SCHEMA]    File summary: candidates={candidate_files_count}, "
            f"compatible={len(compatible_file_keys)}, incompatible={len(incompatible_file_keys)}"
        )

        if file_mapping_mode == "multi_files_single_table" and invalid_multi_mode_files:
            summary_msg = (
                "Update schema failed Mapped files have different structure/type for multi-file single table. "
                "Please map files with matching structure and format options."
            )
            try:
                _log_schema_validation_failure_to_table(
                    cmp_id=data.cmp_id,
                    connection_id=data.connection_id,
                    user_id=data.user_id,
                    job_name=getattr(data, "job_name", None),
                    table_or_file=(multi_files_table_name or "multi_files_single_table"),
                    message=summary_msg,
                )
            except Exception as summary_log_err:
                log(f"[S3][LIST_SCHEMA]    Failed to write consolidated schema-mismatch log: {summary_log_err}")
            first_error = parse_errors[0] if parse_errors else "Mapped files have different structures."
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{summary_msg} "
                    f"First error: {first_error}"
                ),
            )

        if parse_errors and tables_with_columns == 0:
            first_error = parse_errors[0]
            detail = (
                "Failed to infer schema from S3 files due to parsing errors. "
                f"First error: {first_error}"
            )
            try:
                _log_schema_validation_failure_to_table(
                    cmp_id=data.cmp_id,
                    connection_id=data.connection_id,
                    user_id=data.user_id,
                    job_name=getattr(data, "job_name", None),
                    table_or_file="UPDATE_SCHEMA",
                    message=detail,
                )
            except Exception as summary_log_err:
                log(f"[S3][LIST_SCHEMA] Failed to write parse-error log: {summary_log_err}")
            raise HTTPException(status_code=400, detail=detail)
        
        return {
            "tables": tables,
            # Backward compatible: existing clients already use files_count.
            # This remains "candidates found by mapping/type/prefix before strict compatibility pruning".
            "files_count": candidate_files_count,
            # New explicit counters for UI consistency.
            "compatible_files_count": len(compatible_file_keys),
            "incompatible_files_count": len(incompatible_file_keys),
            "prune_missing_tables": prune_missing_tables,
            "stale_tables_to_prune": sorted(stale_single_table_names),
            "file_mapping_mode": file_mapping_mode,
            "base_prefix": s3_folder_prefix,
        }
    
    @staticmethod
    def refresh_and_update_s3_tables_in_database(connection_id, tables_result, job_name, migration_session_id=None):
        """
        Refresh and update S3 tables in the PostgreSQL database.
        
        This function writes table metadata (table name, columns, primary keys) to SourceTableSelection.
        It handles both new tables (INSERT) and existing tables (UPDATE) using PostgreSQL's ON CONFLICT.
        
        Args:
            connection_id (int): The connection ID for this S3 source
            tables_result (dict): Result from list_tables_with_schema containing:
                - tables: List of dicts with keys: table, file_key, last_modified, columns
                - files_count: Total number of files processed
            job_name (str): Job name for updating migration record status
            migration_session_id (int, optional): Migration session ID for tracking
            
        Returns:
            dict: Summary of operation with counts of tables inserted/updated
            
        Raises:
            Exception: If database operations fail after retries
        """
        local_start_time = time.time()
        log("=" * 80)
        log("[S3][DB_WRITE] Starting database write for S3 tables")
        log("=" * 80)
        log(f"[S3][DB_WRITE] Connection ID: {connection_id}")
        log(f"[S3][DB_WRITE] Job name: {job_name}")
        log(f"[S3][DB_WRITE] Tables to process: {len(tables_result.get('tables', []))}")
        file_mapping_mode = (tables_result.get("file_mapping_mode") or "single_table_per_file").strip()
        
        # ========================================================================
        # Stage 0: Get source_id and delta fields for delta detection
        # ========================================================================
        log(f"[S3][DB_WRITE] Stage 0: Checking for delta field configuration...")
        source_id = get_source_id_from_connection(connection_id)
        delta_fields = []
        if source_id:
            delta_fields = get_delta_fields_for_source(source_id)
            if delta_fields:
                log(f"[S3][DB_WRITE]    Found {len(delta_fields)} delta fields: {delta_fields}")
            else:
                log(f"[S3][DB_WRITE]     No delta fields configured for this source")
        else:
            log(f"[S3][DB_WRITE]    Could not determine source_id - delta detection skipped")
        
        conn = None
        cursor = None

        try:
            # ========================================================================
            # Stage 1: Establish database connection
            # ========================================================================
            log(f"[S3][DB_WRITE] Stage 1: Establishing database connection...")
            conn = postgres_connection()
            cursor = conn.cursor()
            log(f"[S3][DB_WRITE]    Database connection established")

            # ========================================================================
            # Stage 2: Fetch existing tables for this connection
            # ========================================================================
            log(f"[S3][DB_WRITE] Stage 2: Fetching existing tables from SourceTableSelection...")
            sql_fetch_existing = """
                SELECT "table" FROM source_table_selection 
                WHERE connection_id = %s;
            """
            cursor.execute(sql_fetch_existing, (connection_id,))
            existing_tables = {sanitize_identifier(str(row[0]).strip()) for row in cursor.fetchall() if str(row[0]).strip()}
            log(f"[S3][DB_WRITE]    Found {len(existing_tables)} existing tables in database")

            # ========================================================================
            # Stage 3: Process each table from the result
            # ========================================================================
            log(f"[S3][DB_WRITE] Stage 3: Processing tables and writing to database...")
            tables_to_insert = []
            tables_to_update = []
            tables_to_delete = []
            stale_tables = set()
            incoming_tables = set()

            for table_info in tables_result.get('tables', []):
                raw_table_name = table_info.get('table')
                table_name = sanitize_identifier(str(raw_table_name).strip()) if raw_table_name else ""
                raw_columns = table_info.get('columns', {})

                if not table_name:
                    log(f"[S3][DB_WRITE]    Skipping table entry with no table name")
                    continue

                incoming_tables.add(table_name)

                # Normalize columns to Snowflake-safe uppercase keys before persisting.
                columns = {}
                if isinstance(raw_columns, dict):
                    for raw_col_name, raw_meta in raw_columns.items():
                        col_name = sanitize_column_name(str(raw_col_name))
                        col_meta = raw_meta.copy() if isinstance(raw_meta, dict) else {}
                        col_meta["is_primary_key"] = bool(col_meta.get("is_primary_key", False))
                        if "data_type" not in col_meta:
                            # Keep _FILE wide enough for full S3 object key paths.
                            col_meta["data_type"] = "VARCHAR(1024)" if col_name == "_FILE" else "VARCHAR(256)"
                        columns[col_name] = col_meta

                # Extract primary keys from columns
                primary_keys = []
                if isinstance(columns, dict):
                    primary_keys = [
                        col_name
                        for col_name, meta in columns.items()
                        if isinstance(meta, dict) and meta.get("is_primary_key", False)
                    ]

                # Convert columns to JSON string
                if columns:
                    table_fields_json = json.dumps(columns)
                else:
                    table_fields_json = "{}"
                    log(f"[S3][DB_WRITE]    Table '{table_name}' has no columns (empty file)")

                primary_keys_json = json.dumps(primary_keys)

                # ========================================================================
                # Stage 3.1: Check if table contains delta fields (for incremental sync)
                # ========================================================================
                is_delta = False
                if delta_fields and columns:
                    is_delta = check_table_has_delta_fields(columns, delta_fields)
                    if is_delta:
                        found_delta_fields = [field for field in delta_fields if field in columns]
                        log(f"[S3][DB_WRITE]    Table '{table_name}' marked as delta table - contains delta fields: {found_delta_fields}")

                is_new = table_name not in existing_tables
                if is_new:
                    tables_to_insert.append(table_name)
                else:
                    tables_to_update.append(table_name)

                # ========================================================================
                # Stage 4: Insert or update table in SourceTableSelection
                # ========================================================================
                file_key = table_info.get('file_key', '')
                base_prefix = str(tables_result.get("base_prefix") or "").strip()
                folder_path_value = (
                    base_prefix
                    if file_mapping_mode == "multi_files_single_table" and base_prefix
                    else file_key
                )

                # If the same file path is remapped to a new table name, remove old rows.
                if file_mapping_mode == "single_table_per_file" and folder_path_value:
                    cursor.execute(
                        """
                        DELETE FROM source_table_selection
                        WHERE connection_id = %s
                          AND folder_path = %s
                          AND UPPER("table") <> UPPER(%s)
                        """,
                        (connection_id, folder_path_value, table_name),
                    )
                elif file_mapping_mode == "multi_files_single_table":
                    cursor.execute(
                        """
                        DELETE FROM source_table_selection
                        WHERE connection_id = %s
                          AND UPPER("table") <> UPPER(%s)
                        """,
                        (connection_id, table_name),
                    )

                # Normalize existing case-variant rows to canonical uppercase table name.
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

                sql_upsert = """
                    INSERT INTO source_table_selection 
                        (connection_id, "table", table_fields, primary_keys, selected, is_mapping_checked, added_on, is_history, is_delta, folder_path)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)
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
                        folder_path = EXCLUDED.folder_path;
                """
                cursor.execute(
                    sql_upsert,
                    (connection_id, table_name, table_fields_json, primary_keys_json, False, True, False, is_delta, folder_path_value)
                )

                action = "INSERTED" if is_new else "UPDATED"
                pk_info = f" (PKs: {primary_keys})" if primary_keys else " (no PKs)"
                log(f"[S3][DB_WRITE]    {action} table '{table_name}'{pk_info} - {len(columns)} columns")

            prune_missing_tables = bool(tables_result.get("prune_missing_tables", False))
            if prune_missing_tables:
                stale_tables_from_result = {
                    sanitize_identifier(str(t).strip())
                    for t in (tables_result.get("stale_tables_to_prune") or [])
                    if str(t).strip()
                }
                if stale_tables_from_result:
                    stale_tables = existing_tables & stale_tables_from_result
                else:
                    stale_tables = existing_tables - incoming_tables
                if stale_tables:
                    placeholders = ",".join(["%s"] * len(stale_tables))
                    if file_mapping_mode == "multi_files_single_table":
                        log(
                            f"[S3][DB_WRITE] Stage 3.2: Marking {len(stale_tables)} stale table(s) unchecked "
                            f"for multi-file mode (metadata preserved)"
                        )
                        sql_uncheck_stale = f"""
                            UPDATE source_table_selection
                            SET is_mapping_checked = FALSE, selected = FALSE
                            WHERE connection_id = %s
                              AND "table" IN ({placeholders});
                        """
                        cursor.execute(sql_uncheck_stale, (connection_id, *sorted(stale_tables)))
                        log(f"[S3][DB_WRITE]    Marked stale tables unchecked: {sorted(stale_tables)}")
                    else:
                        log(f"[S3][DB_WRITE] Stage 3.2: Removing {len(stale_tables)} stale table(s) not present in current listing...")
                        sql_delete_stale = f"""
                            DELETE FROM source_table_selection
                            WHERE connection_id = %s
                              AND "table" IN ({placeholders});
                        """
                        cursor.execute(sql_delete_stale, (connection_id, *sorted(stale_tables)))
                        tables_to_delete = sorted(stale_tables)
                        log(f"[S3][DB_WRITE]    Removed stale tables: {tables_to_delete}")

                        # Keep S3 mapping in sync by removing stale single-file mapping entries.
                        try:
                            existing_mapping = get_s3_mapping(connection_id) or {}
                            mapping_mode = existing_mapping.get("file_mapping_mode", "single_table_per_file")
                            existing_single_map = existing_mapping.get("single_file_table_mapping") or {}

                            if mapping_mode == "single_table_per_file" and isinstance(existing_single_map, dict):
                                stale_upper = {str(t).strip().upper() for t in stale_tables if str(t).strip()}
                                incoming_file_keys = {
                                    str(t.get("file_key")).strip()
                                    for t in (tables_result.get("tables", []) or [])
                                    if isinstance(t, dict) and str(t.get("file_key", "")).strip()
                                }

                                pruned_single_map = {}
                                removed_mapping_keys = []
                                for file_key, mapped_table in existing_single_map.items():
                                    fk = str(file_key).strip()
                                    mapped_table_upper = str(mapped_table).strip().upper()
                                    if mapped_table_upper in stale_upper:
                                        removed_mapping_keys.append(fk)
                                        continue
                                    if incoming_file_keys and fk not in incoming_file_keys:
                                        removed_mapping_keys.append(fk)
                                        continue
                                    pruned_single_map[file_key] = mapped_table

                                if removed_mapping_keys:
                                    save_s3_mapping(
                                        connection_id,
                                        mapping_mode,
                                        pruned_single_map,
                                        {},
                                        raise_on_error=True,
                                    )
                                    log(
                                        f"[S3][DB_WRITE]    Removed {len(removed_mapping_keys)} stale "
                                        f"single_file_table_mapping entries: {sorted(set(removed_mapping_keys))}"
                                    )
                                else:
                                    log("[S3][DB_WRITE]    No stale single_file_table_mapping entries to remove")
                        except Exception as mapping_cleanup_error:
                            log(f"[S3][DB_WRITE]    Failed to prune stale S3 mapping entries: {mapping_cleanup_error}")
                            raise Exception(
                                f"Failed to prune stale S3 mapping entries: {mapping_cleanup_error}"
                            )
                else:
                    log(f"[S3][DB_WRITE] Stage 3.2: No stale tables to remove")

            # For single-file mode, keep metadata for un-mapped tables but mark them unchecked.
            # This allows users to re-check/remap later while preserving locked table/PK history.
            if file_mapping_mode == "single_table_per_file":
                unchecked_tables = (existing_tables - incoming_tables) - stale_tables
                if unchecked_tables:
                    placeholders = ",".join(["%s"] * len(unchecked_tables))
                    sql_uncheck = f"""
                        UPDATE source_table_selection
                        SET is_mapping_checked = FALSE, selected = FALSE
                        WHERE connection_id = %s
                          AND "table" IN ({placeholders});
                    """
                    cursor.execute(sql_uncheck, (connection_id, *sorted(unchecked_tables)))
                    log(
                        f"[S3][DB_WRITE] Stage 3.3: Marked {len(unchecked_tables)} un-mapped table(s) unchecked "
                        f"(metadata preserved): {sorted(unchecked_tables)}"
                    )

            # ========================================================================
            # Stage 5: Update migration record status if job_name provided
            # ========================================================================
            if job_name:
                log(f"[S3][DB_WRITE] Stage 5: Updating migration record status...")
                sql_update_migration_record = """
                    UPDATE migration_record
                    SET status = %s, updated_at = NOW()
                    WHERE job_name = %s;
                """
                cursor.execute(sql_update_migration_record, ('completed', job_name))
                rows_updated = cursor.rowcount
                if rows_updated > 0:
                    log(f"[S3][DB_WRITE]    Updated migration record status to 'completed' ({rows_updated} row(s))")
                else:
                    log(f"[S3][DB_WRITE]    No migration record found for job_name '{job_name}' (this is OK for listing-only operations)")

            # ========================================================================
            # Stage 6: Commit transaction
            # ========================================================================
            log(f"[S3][DB_WRITE] Stage 6: Committing transaction...")
            conn.commit()
            log(f"[S3][DB_WRITE]    Transaction committed successfully")

            # Update the log message to indicate schema refresh completion
            if job_name:
                try:
                    from utils.error_utils import update_migration_status_log
                    total_tables = len(tables_to_insert) + len(tables_to_update)
                    removed_count = len(tables_to_delete)
                    execution_time = None
                    try:
                        if job_name:
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
                        log(f"[S3][DB_WRITE]    Failed to compute execution_time: {time_err}")
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
                        status="S"  # Success status
                    )
                    try:
                        from utils.websocket_utils import trigger_connection_activity_log_update
                        trigger_connection_activity_log_update(connection_id)
                    except Exception as activity_ws_error:
                        log(f"[S3][DB_WRITE]   Connection activity log WebSocket trigger failed (non-fatal): {activity_ws_error}")
                except Exception as log_error:
                    log(f"[S3][DB_WRITE]    Update schema completion log update failed (non-fatal): {log_error}")

            # Trigger WebSocket update for schema refresh completion
            if job_name:
                try:
                    from utils.websocket_utils import trigger_update_schema_ws_status_update
                    trigger_update_schema_ws_status_update(connection_id)
                except Exception as ws_error:
                    log(f"[S3][DB_WRITE]    WebSocket trigger failed (non-fatal): {ws_error}")

            # ========================================================================
            # Stage 7: Summary
            # ========================================================================
            total_tables = len(tables_to_insert) + len(tables_to_update)
            log("=" * 80)
            log(f"[S3][DB_WRITE]  Database write completed successfully")
            log(f"[S3][DB_WRITE]   - Total tables processed: {total_tables}")
            log(f"[S3][DB_WRITE]   - New tables inserted: {len(tables_to_insert)}")
            log(f"[S3][DB_WRITE]   - Existing tables updated: {len(tables_to_update)}")
            log(f"[S3][DB_WRITE]   - Stale tables removed: {len(tables_to_delete)}")
            log("=" * 80)

            return {
                "status": "success",
                "tables_inserted": len(tables_to_insert),
                "tables_updated": len(tables_to_update),
                "tables_deleted": len(tables_to_delete),
                "total_tables": total_tables,
                "new_tables": tables_to_insert,
                "updated_tables": tables_to_update,
                "deleted_tables": tables_to_delete
            }

        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            log(f"[S3][DB_WRITE]  Error: {error_type}: {error_msg}")

            # Rollback on error
            if conn:
                try:
                    conn.rollback()
                    log(f"[S3][DB_WRITE]    Transaction rolled back")
                except Exception as rollback_error:
                    log(f"[S3][DB_WRITE]    Rollback error: {rollback_error}")

            # Update migration record to FAILED if we have job_name
            if job_name and conn:
                try:
                    cursor = conn.cursor()
                    sql_update_migration_record_error = """
                        UPDATE migration_record
                        SET status = %s, updated_at = NOW()
                        WHERE job_name = %s;
                    """
                    cursor.execute(sql_update_migration_record_error, ('failed', job_name))
                    conn.commit()
                    log(f"[S3][DB_WRITE]    Updated migration record status to 'failed'")

                    # Trigger WebSocket update for schema refresh failure
                    try:
                        from utils.websocket_utils import trigger_update_schema_ws_status_update
                        trigger_update_schema_ws_status_update(connection_id)
                    except Exception as ws_error:
                        log(f"[S3][DB_WRITE]    WebSocket trigger failed (non-fatal): {ws_error}")
                except Exception as update_error:
                    log(f"[S3][DB_WRITE]    Failed to update migration record: {update_error}")

            raise Exception(f"Failed to write S3 tables to database: {error_msg}")

        finally:
            # Close connection if it exists
            if cursor:
                try:
                    cursor.close()
                except Exception as close_error:
                    log(f"[S3][DB_WRITE]    Error closing cursor: {close_error}")
            if conn:
                try:
                    conn.close()
                    log(f"[S3][DB_WRITE]    Database connection closed")
                except Exception as close_error:
                    log(f"[S3][DB_WRITE]    Error closing connection: {close_error}")
    
    @staticmethod
    async def migrate(request: AmazonS3MigrationRequest):
        """
        Main migration function: Sync data from Amazon S3 to Snowflake.
        
        This is the complete migration implementation with all features:
        - Delta reload (incremental sync)
        - Drop & reload
        - All file types (CSV, TSV, JSON, XML, Parquet, Avro)
        - All load methods (UPSERT, APPEND, CUSTOM PK)
        - All file mapping modes (Single table/file, Multi-file  Single table)
        """
        # This is a wrapper that calls the internal migration function
        # The full implementation is in _sync_amazon_s3_to_snowflake below
        return await AmazonS3Service._sync_amazon_s3_to_snowflake(request)
    
    @staticmethod
    async def _sync_amazon_s3_to_snowflake(request: AmazonS3MigrationRequest):
        """
        Internal migration function: Sync data from Amazon S3 to Snowflake.
        This is the complete implementation with all features.
        """
        log("=" * 80)
        log("[MIGRATION] Starting S3 to Snowflake migration")
        log("=" * 80)
        
        # Extract source config (S3)
        source_config = request.source_config
        log(f"[MIGRATION] Extracting source config (S3)...")
        aws_access_key_id = source_config.get("aws_access_key_id")
        aws_secret_access_key = source_config.get("aws_secret_access_key")
        s3_bucket = (source_config.get("s3_bucket") or "").strip()  # Strip whitespace to avoid validation errors
        aws_region = source_config.get("aws_region")
        s3_base_prefix = (source_config.get("s3_base_prefix") or "").strip()  #  Strip whitespace
        
        log(f"[MIGRATION] S3 Config: bucket={s3_bucket}, region={aws_region or '(auto-detect)'}, prefix={s3_base_prefix or '(root)'}")
        
        # Extract file mapping mode
        file_mapping_mode = source_config.get("file_mapping_mode", "single_table_per_file")
        if not isinstance(file_mapping_mode, str):
            file_mapping_mode = "single_table_per_file"
        log(f"[S3][MIGRATION] File mapping mode: {file_mapping_mode}")
        
        # Optional UI-driven mapping: file_key -> logical table name
        single_file_map = source_config.get("single_file_table_mapping") or {}
        if isinstance(single_file_map, str):
            try:
                single_file_map = json.loads(single_file_map)
            except Exception as e:
                log(f"[S3][MIGRATION][MAP]  Failed to parse single_file_table_mapping: {e}")
                single_file_map = {}
        if not isinstance(single_file_map, dict):
            single_file_map = {}
        log(f"[S3][MIGRATION][MAP] Final single_file_map: {single_file_map} ({len(single_file_map)} entries)")
        
        # Extract destination config (Snowflake)
        dest_config = request.destination_config
        log(f"[MIGRATION] Extracting destination config (Snowflake)...")
        sf_account = dest_config.get("account")
        sf_user = dest_config.get("user") or dest_config.get("username")
        sf_password = dest_config.get("password")
        sf_warehouse = normalize_and_log_snowflake_identifier(dest_config.get("warehouse"), "warehouse")
        sf_database = normalize_and_log_snowflake_identifier(dest_config.get("database"), "database")
        sf_schema = normalize_and_log_snowflake_identifier(dest_config.get("schema"), "schema")
        sf_role = normalize_and_log_snowflake_identifier(dest_config.get("role"), "role") or "ACCOUNTADMIN"
        
        log(f"[MIGRATION] Snowflake Config: account={sf_account}, database={sf_database}, schema={sf_schema}")
        
        # Extract GCS config
        gcs_service_account_json = dest_config.get("gcs_service_account_json") or source_config.get("gcs_service_account_json")
        gcp_project_id = dest_config.get("gcp_project_id") or source_config.get("gcp_project_id")
        gcs_bucket_name = resolve_gcs_bucket_name(dest_config, source_config)
        sf_storage_integration = resolve_snowflake_storage_integration(dest_config, source_config)

        log(f"[MIGRATION] GCS Config: bucket={gcs_bucket_name} (root level uploads)")
        log(f"[MIGRATION] Storage Integration: {sf_storage_integration}")
        
        # Extract migration parameters
        connection_id = request.connection_id
        cmp_id = request.cmp_id
        user_id = request.user_id
        session = request.session
        selected_tables = request.selected_tables
        mapping_removed_tables = []
        
        #  Extract log_id
        log_id = request.log_id if hasattr(request, 'log_id') else None
        
        #  Start timing
        start_time = time.time()
        
        # ========================================================================
        # CRITICAL FIX: Convert table names to S3 file paths
        # For S3, selected_tables contains table names, but we need file paths (folder_path)
        # Query database to get folder_path for each selected table
        # ========================================================================
        if selected_tables and file_mapping_mode == "single_table_per_file":
            log(f"[S3] [MIGRATION] Converting {len(selected_tables)} table names to S3 file paths...")
            selected_files = []
            try:
                mapped_table_names_upper = {
                    str(v).strip().upper()
                    for v in (single_file_map or {}).values()
                    if str(v).strip()
                }
                mapped_file_keys = {
                    str(k).strip()
                    for k in (single_file_map or {}).keys()
                    if str(k).strip()
                }

                conn_db = postgres_connection()
                cursor_db = conn_db.cursor()
                
                # Query folder_path for selected tables
                normalized_selected_tables = [
                    sanitize_identifier(str(t).strip()) for t in selected_tables if str(t).strip()
                ]
                if not normalized_selected_tables:
                    raise ValueError("No valid selected table names provided for S3 migration.")
                placeholders = ','.join(['%s'] * len(normalized_selected_tables))
                sql_get_paths = f"""
                    SELECT "table", folder_path 
                    FROM source_table_selection 
                    WHERE connection_id = %s AND UPPER("table") IN ({placeholders})
                """
                cursor_db.execute(sql_get_paths, (connection_id, *normalized_selected_tables))
                table_to_path = {str(row[0]).strip().upper(): row[1] for row in cursor_db.fetchall()}
                cursor_db.close()
                conn_db.close()
                
                # Convert table names to file paths
                unmapped_tables = []
                for table_name in selected_tables:
                    table_name_str = str(table_name).strip()
                    table_name_upper = table_name_str.upper()

                    # If selected value is a table name, it must exist in current S3 mapping values.
                    if table_name_upper in mapped_table_names_upper:
                        matched_file_key = next(
                            (
                                str(file_key)
                                for file_key, mapped_table in (single_file_map or {}).items()
                                if str(mapped_table).strip().upper() == table_name_upper
                            ),
                            None,
                        )
                        if matched_file_key:
                            selected_files.append(matched_file_key)
                            log(f"[S3] [MIGRATION] Mapped table '{table_name_str}'  file '{matched_file_key}'")
                            continue

                    # If selected value is a direct file key, it must still exist in current mapping.
                    if table_name_str in mapped_file_keys:
                        selected_files.append(table_name_str)
                        log(f"[S3] [MIGRATION] Using selected file key '{table_name_str}'")
                        continue

                    file_path = table_to_path.get(table_name_upper)
                    if file_path:
                        file_path_str = str(file_path).strip()
                        if file_path_str in mapped_file_keys:
                            selected_files.append(file_path_str)
                            log(f"[S3] [MIGRATION] Mapped table '{table_name_str}'  file '{file_path_str}'")
                        else:
                            unmapped_tables.append(table_name_str)
                            log(
                                f"[S3] [MIGRATION]  WARNING: Table '{table_name_str}' has stale folder_path "
                                f"'{file_path_str}' not present in current mapping"
                            )
                    else:
                        unmapped_tables.append(table_name_str)
                        log(f"[S3] [MIGRATION]  WARNING: Mapping missing for table '{table_name_str}'")

                if unmapped_tables:
                    mapping_removed_tables.extend(unmapped_tables)
                    missing_tables = ", ".join(sorted(set(unmapped_tables)))
                    log(
                        "[S3] [MIGRATION]  Mapping removed for selected table(s): "
                        f"{missing_tables}. These tables will be marked failed and skipped."
                    )
                
                # Replace selected_tables with selected_files (file paths)
                selected_tables = selected_files
                log(f"[MIGRATION] Using {len(selected_tables)} selected files (converted from {len(request.selected_tables)} table names)")
            except Exception as path_error:
                path_error_str = str(path_error or "")
                if "S3 table mapping removed for selected table(s):" in path_error_str:
                    # Non-terminal: preserve valid mapped files and mark stale table mappings as failed.
                    missing_part = path_error_str.split("S3 table mapping removed for selected table(s):", 1)[1]
                    missing_tables = [
                        tbl.strip().strip(".")
                        for tbl in missing_part.split(". Kindly update schema or remap before migration.")[0].split(",")
                        if tbl.strip()
                    ]
                    mapping_removed_tables.extend(missing_tables)
                    selected_tables = selected_files
                    log(
                        "[S3] [MIGRATION]  Error converting table names to file paths: "
                        f"{path_error}. Continuing with mapped file paths and marking stale tables as failed."
                    )
                else:
                    log(f"[S3] [MIGRATION]  Error converting table names to file paths: {path_error}")
                    raise

        #  FIX: Read S3-specific fields from source_config (not request)
        file_type = source_config.get("file_type", "").lower() if source_config.get("file_type") else ""
        json_mode = source_config.get("json_mode")
        xml_mode = source_config.get("xml_mode")
        delimiter = source_config.get("delimiter")
        quotechar = source_config.get("quotechar")
        skipheaderlines = source_config.get("skipheaderlines")
        
        # Extract new CSV/TSV options from source_config
        escape_char = source_config.get("escape_char")
        null_sequence = source_config.get("null_sequence")
        skip_footer_lines = source_config.get("skip_footer_lines")
        line_separator = source_config.get("line_separator")
        
        # TSV-specific options
        if file_type == "tsv":
            delimiter = source_config.get("tsv_delimiter", delimiter)
            quotechar = source_config.get("tsv_quotechar", quotechar)
            skipheaderlines = source_config.get("tsv_skipheaderlines", skipheaderlines)
            escape_char = source_config.get("tsv_escape_char", escape_char)
            null_sequence = source_config.get("tsv_null_sequence", null_sequence)
            skip_footer_lines = source_config.get("tsv_skip_footer_lines", skip_footer_lines)
        
        chunk_count = request.chunk_count
        #  FIX: Read S3-specific fields from source_config (not request)
        load_method = source_config.get("load_method", "upsert_file_row")
        
        # Read multi-files metadata (if provided) and strip whitespace.
        multi_files_table_name = (source_config.get("multi_files_table_name") or "").strip()
        multi_files_prefix = (source_config.get("multi_files_prefix") or "").strip()
        include_subfolders = _as_bool(source_config.get("include_subfolders"), default=False)
        
        #  Read custom_primary_key as per-table mapping if load_method is upsert_custom_key
        # Format: {"TABLE1": ["COL1", "COL2"], "TABLE2": ["ID"]}
        custom_pk_mapping = {}
        if load_method == "upsert_custom_key":
            custom_pk_mapping = source_config.get("custom_primary_key") or {}
            if isinstance(custom_pk_mapping, str):
                try:
                    custom_pk_mapping = json.loads(custom_pk_mapping)
                except Exception:
                    custom_pk_mapping = {}
            if not isinstance(custom_pk_mapping, dict):
                log(f"[MIGRATION]  custom_primary_key must be a dict, got {type(custom_pk_mapping)}")
                custom_pk_mapping = {}
            else:
                log(f"[MIGRATION] Custom PK mapping: {len(custom_pk_mapping)} table(s) configured")
        job_name = request.job_name or f"s3_migration_{request.migration_session_id}"
        migration_session_id = request.migration_session_id
        safety_interval = request.safety_interval
        logs_creation = request.logs_creation
        #  Strip whitespace from connection and company names
        connection_name = (request.connection_name or f"S3_CONNECTION_{connection_id}").strip()
        company_name = (request.company_name or "").strip()
        drop_and_reload = request.drop_and_reload or False
        
        if file_mapping_mode == "multi_files_single_table":
            log(f"[MIGRATION] Multi-files config:")
            log(f"  - Table name: {multi_files_table_name or '(auto-generate)'}")
            if multi_files_prefix:
                log(f"  - Prefix: {multi_files_prefix} (deprecated/ignored)")
            else:
                log(f"  - Prefix: (ignored)")
            log(
                f"  - Scope: "
                f"{'recursive (include subfolders)' if include_subfolders else 'base-folder only (no subfolders)'}"
            )
        
        # Ensure load_method is a string
        if not isinstance(load_method, str):
            log(f"[S3][MIGRATION]  Invalid load_method type: {type(load_method)}, defaulting to 'upsert_file_row'")
            load_method = "upsert_file_row"
        log(f"[S3][MIGRATION] Load method: {load_method}")
        
        # Persist S3 mapping metadata. Multi-file mode intentionally stores no table-to-file map.
        try:
            log("[S3][MAPPING_DEBUG]  Persisting mapping via _validate_and_persist_s3_mapping...")
            AmazonS3Service._validate_and_persist_s3_mapping(
                connection_id=connection_id,
                file_mapping_mode=file_mapping_mode,
                single_file_map=single_file_map,
                cmp_id=cmp_id,
                basefolder_path=s3_base_prefix,
                created_by=user_id,
                updated_by=user_id,
            )
        except Exception as e_inner:
            log(f"[MIGRATION]  Failed to validate/persist S3 mapping (non-fatal): {e_inner}")
            
        # S3 migration should not defer terminal logs/emails across retries.
        # Force final-attempt behavior for consistent status/log visibility.
        is_final_attempt = True
        retry_attempt = 0
        max_retries = 1
        
        log(f"[MIGRATION] Migration Parameters:")
        log(f"  Connection ID: {connection_id}")
        log(f"  Selected files: {len(selected_tables)}")
        log(f"  File type: {file_type}")
        log(f"  File mapping mode: {file_mapping_mode}")
        log(f"  Load method: {load_method}")
        log(f"  Chunk size: {chunk_count:,} rows")
        log(f"  Safety interval: {safety_interval} minutes")
        log(f"  Drop and reload: {drop_and_reload}")
        log(f"  Retry attempt: {retry_attempt + 1}/{max_retries}, Final: {is_final_attempt}")
        
        # Initialize failure tracker
        failure_tracker = MigrationFailureTracker()

        def _mark_selected_tables_failed_on_terminal_error() -> None:
            """
            Ensure SourceTableSelection + migration_record statuses are moved to failed
            for terminal errors that happen before per-table processing begins.
            """
            try:
                candidate_names = set()
                for item in selected_tables or []:
                    value = str(item).strip()
                    if not value:
                        continue
                    candidate_names.add(value)
                    mapped_name = single_file_map.get(value) or single_file_map.get(value.split("/")[-1])
                    if mapped_name:
                        candidate_names.add(str(mapped_name).strip())

                if file_mapping_mode == "multi_files_single_table" and multi_files_table_name:
                    candidate_names.add(str(multi_files_table_name).strip())

                if not candidate_names:
                    return

                conn_db = postgres_connection()
                cursor_db = conn_db.cursor()
                cursor_db.execute(
                    """
                    SELECT "table"
                    FROM source_table_selection
                    WHERE connection_id = %s
                    """,
                    (connection_id,),
                )
                existing_table_names = [str(r[0]).strip() for r in (cursor_db.fetchall() or []) if r and r[0]]
                cursor_db.close()
                conn_db.close()

                existing_lookup = {name.upper(): name for name in existing_table_names}
                resolved_tables = []
                for name in candidate_names:
                    resolved = existing_lookup.get(str(name).strip().upper())
                    if resolved:
                        resolved_tables.append(resolved)

                for table_name in sorted(set(resolved_tables)):
                    try:
                        update_table_status_snowflake(
                            connection_id, table_name, "failed", job_name, migration_session_id
                        )
                    except Exception as status_error:
                        log(
                            f"[MIGRATION]  Failed to mark table '{table_name}' as failed "
                            f"after terminal error: {status_error}"
                        )
            except Exception as mark_error:
                log(f"[MIGRATION]  Failed to mark selected tables as failed after terminal error: {mark_error}")
        
        #  FIX: Wrap validation in try-except to catch and email all errors
        try:
            # Validate required configs
            if not aws_access_key_id or not aws_secret_access_key or not s3_bucket:
                error_msg = "Missing required S3 credentials"
                log(f"[MIGRATION]  {error_msg}")
                raise ValueError(error_msg)
            
            # Validate required Snowflake fields (account, user, database, schema)
            if not sf_account or not sf_user or not sf_database or not sf_schema:
                error_msg = "Missing required Snowflake credentials (account, user, database, or schema)"
                log(f"[MIGRATION]  {error_msg}")
                raise ValueError(error_msg)
            
            # Validate authentication: either password OR private_key must be provided
            # (matches Salesforce service validation pattern)
            sf_private_key = dest_config.get("private_key")
            if not sf_password and not sf_private_key:
                error_msg = "Missing required Snowflake authentication: either password or private_key must be provided"
                log(f"[MIGRATION]  {error_msg}")
                raise ValueError(error_msg)
        except ValueError as validation_error:
            #  FIX: Catch validation errors and send email
            error_msg = str(validation_error)
            log(f"[MIGRATION]  Validation Error: {error_msg}")
            
            error_context = {
                "table_name": "Migration Setup",
                "service_name": "S3 to Snowflake",
                "error_type": "VALIDATION_ERROR",
                "cmp_id": cmp_id,
                "connection_id": connection_id,
                "user_id": user_id,
                "job_name": job_name,
                "migration_session_id": migration_session_id,
                "company_name": company_name,
                "connection_name": connection_name,
                "session": session
            }
            
            try:
                handle_service_error(
                    error=validation_error,
                    context=error_context,
                    log_files=None,
                    send_email=True,  #  Send email for validation errors
                    is_final_attempt=is_final_attempt
                )
            except Exception as email_err:
                log(f" Failed to send validation error email: {email_err}")
            _mark_selected_tables_failed_on_terminal_error()
            raise validation_error
        
        # Auto-detect region if not provided
        try:
            if not aws_region:
                log(f"[MIGRATION] Region not provided, auto-detecting...")
                aws_region = detect_s3_bucket_region(
                    bucket_name=s3_bucket,
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    fallback_region="us-east-1"
                )
                log(f"[MIGRATION]  Auto-detected region: {aws_region}")
            else:
                log(f"[MIGRATION] Using provided region: {aws_region}")
        except Exception as region_error:
            #  FIX: Catch region detection errors and send email
            error_msg = f"Failed to detect S3 bucket region: {str(region_error)}"
            log(f"[MIGRATION]  {error_msg}")
            
            if is_final_attempt:
                try:
                    error_context = {
                        "table_name": "Region Detection",
                        "service_name": "S3 to Snowflake",
                        "error_type": "REGION_DETECTION_FAILED",
                        "cmp_id": cmp_id,
                        "connection_id": connection_id,
                        "user_id": user_id,
                        "job_name": job_name,
                        "migration_session_id": migration_session_id,
                        "company_name": company_name,
                        "connection_name": connection_name,
                        "session": session
                    }
                    
                    handle_service_error(
                        error=region_error,
                        context=error_context,
                        log_files=None,
                        send_email=True,
                        is_final_attempt=is_final_attempt
                    )
                except Exception as email_error:
                    log(f" Failed to send region detection error email: {email_error}")
            _mark_selected_tables_failed_on_terminal_error()
            raise region_error
        
        # Test connections
        log(" Testing connections before starting migration...")
        
        # Test S3 connection
        try:
            log(f" Testing S3 connection for bucket '{s3_bucket}'...")
            s3 = s3_client(aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket=s3_bucket, auto_detect_region=False)
            s3.head_bucket(Bucket=s3_bucket)
            log(" S3 connection successful")
        except Exception as s3_error:
            error_message = str(s3_error)
            error_message_with_trace = f"{error_message}\n\n--- Traceback ---\n{traceback.format_exc()}"
            log(f" S3 CONNECTION FAILED: {error_message}")
            
            if is_final_attempt:
                try:
                    html_content, plain_text = create_connection_failure_email(
                        connection_type="Amazon S3",
                        connection_name=connection_name,
                        error_message=error_message_with_trace,
                        context={
                            "job_name": job_name,
                            "company_name": company_name,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "cmp_id": cmp_id
                        }
                    )
                    email_config = EmailConfiguration(user_id=user_id, company_id=cmp_id)
                    if email_config.is_configured():
                        subject = f" S3 Connection Failed - {connection_name} - {company_name}"
                        send_email_with_retry(email_config, subject, html_content, plain_text)
                        log(" S3 connection failure email sent (final attempt)")
                except Exception as email_error:
                    log(f" Failed to send S3 connection failure email: {email_error}")
            _mark_selected_tables_failed_on_terminal_error()
            raise s3_error
        
        # Test Snowflake connection
        try:
            log(f" Testing Snowflake connection...")
            sf_private_key = dest_config.get("private_key")
            sf_passphrase = dest_config.get("passphrase")
            
            if not sf_password and not sf_private_key:
                raise Exception("Snowflake authentication failed: Password is empty and no private key configured.")
            
            # Extract authentication type from destination config
            auth_type = dest_config.get('authentication_type', 'password')
            
            conn = sf_connect(
                sf_account, sf_user, sf_password, sf_warehouse, 
                sf_database, sf_schema, sf_role, 
                private_key=sf_private_key, 
                passphrase=sf_passphrase,
                authentication_type=auth_type
            )
            log(" Snowflake connection successful")
            
            # Create schema if it doesn't exist
            if sf_schema:
                log(f"[MIGRATION] Ensuring schema '{sf_schema}' exists in database '{sf_database}'...")
                try:
                    with conn.cursor() as cur:
                        if sf_database:
                            cur.execute(f"USE DATABASE {Q(sf_database)}")
                        try:
                            cur.execute(f"USE SCHEMA {Q(sf_schema)}")
                            log(f"[MIGRATION]  Schema '{sf_schema}' already exists")
                        except Exception:
                            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {Q(sf_schema)}")
                            log(f"[MIGRATION]  Schema '{sf_schema}' created successfully")
                            cur.execute(f"USE SCHEMA {Q(sf_schema)}")
                except Exception as schema_error:
                    log(f"[MIGRATION]  Warning: Could not ensure schema exists: {str(schema_error)}")
        except Exception as sf_error:
            error_message = str(sf_error)
            error_message_with_trace = f"{error_message}\n\n--- Traceback ---\n{traceback.format_exc()}"
            log(f" SNOWFLAKE CONNECTION FAILED: {error_message}")
            
            if is_final_attempt:
                try:
                    html_content, plain_text = create_connection_failure_email(
                        connection_type="Snowflake",
                        connection_name=connection_name,
                        error_message=error_message_with_trace,
                        context={
                            "job_name": job_name,
                            "company_name": company_name,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "cmp_id": cmp_id
                        }
                    )
                    email_config = EmailConfiguration(user_id=user_id, company_id=cmp_id)
                    if email_config.is_configured():
                        subject = f" Snowflake Connection Failed - {connection_name} - {company_name}"
                        send_email_with_retry(email_config, subject, html_content, plain_text)
                        log(" Snowflake connection failure email sent (final attempt)")
                except Exception as email_error:
                    log(f" Failed to send Snowflake connection failure email: {email_error}")
            _mark_selected_tables_failed_on_terminal_error()
            raise sf_error
        
        log(" Both connections validated - starting migration...")
        
        # Connect GCS
        log(f"[MIGRATION] Connecting to GCS...")
        try:
            # Try using config/environment credentials first
            gcs = gcs_client(gcs_service_account_json, gcp_project_id)
            log(f"[MIGRATION]  GCS connected")
        except Exception as e:
            # Fallback to default credentials (like Salesforce service does)
            log(f"[MIGRATION]  GCS connection with config failed: {e}")
            log(f"[MIGRATION] Attempting fallback to default GCS credentials...")
            try:
                from google.auth import compute_engine
                credentials = compute_engine.Credentials()
                gcs = storage.Client(credentials=credentials, project=gcp_project_id or GCP_PROJECT_ID)
                log(f"[MIGRATION]  GCS connected using default credentials")
            except Exception as fallback_error:
                # Last resort: try without explicit credentials (uses environment/default)
                try:
                    gcs = storage.Client(project=gcp_project_id or GCP_PROJECT_ID)
                    log(f"[MIGRATION]  GCS connected using environment/default credentials")
                except Exception as final_error:
                    error_msg = f"GCS connection failed: {e}. Fallback attempts also failed: {fallback_error}, {final_error}"
                    log(f"[MIGRATION]  {error_msg}")
                    
                    #  FIX: Send email for GCS connection failure
                    if is_final_attempt:
                        try:
                            error_context = {
                                "table_name": "GCS Connection",
                                "service_name": "S3 to Snowflake",
                                "error_type": "GCS_CONNECTION_FAILED",
                                "cmp_id": cmp_id,
                                "connection_id": connection_id,
                                "user_id": user_id,
                                "job_name": job_name,
                                "migration_session_id": migration_session_id,
                                "company_name": company_name,
                                "connection_name": connection_name,
                                "session": session
                            }
                            
                            error_result = handle_service_error(
                                error=Exception(error_msg),
                                context=error_context,
                                log_files=None,
                                send_email=True,
                                is_final_attempt=is_final_attempt
                            )
                            
                            if error_result.get("email_sent"):
                                log(f" GCS connection failure email sent")
                        except Exception as email_error:
                            log(f" Failed to send GCS connection failure email: {email_error}")
                    _mark_selected_tables_failed_on_terminal_error()
                    raise Exception(error_msg)
        
        # Get last checked dates for incremental sync
        last_checked_data = {}
        if selected_tables:
            selected_basename_counts = _build_file_basename_counts(
                [str(item).strip() for item in selected_tables if str(item).strip()]
            )
            table_names = []
            for key in selected_tables:
                if file_mapping_mode == "multi_files_single_table":
                    # Canonicalize to uppercase for consistent Snowflake-style matching.
                    table_name = sanitize_identifier(str(key).strip())
                else:
                    internal_name = table_from_file_key(key)
                    mapped_name = _resolve_single_file_mapped_name(
                        file_key=key,
                        single_file_map=single_file_map,
                        basename_counts=selected_basename_counts,
                    )
                    table_name = sanitize_identifier(str(mapped_name).strip()) if mapped_name else internal_name
                if table_name:
                    table_names.append(table_name)
            last_checked_data = get_last_checked_dates_and_fields(connection_id, table_names)
            log(f"[MIGRATION] Retrieved last checked data for {len(last_checked_data)} tables")
        
        # Multi-file mode no longer maintains a persisted table-to-file map.
        # File discovery/validation is performed directly from S3 scope/prefix during processing.
        
        # Compute safety interval cutoff.
        # If safety_interval is unset/None, do not apply safety cutoff filtering.
        safety_interval_cutoff = None
        interval_seconds = None
        raw_safety_interval = (str(safety_interval).strip() if safety_interval is not None else "")
        if raw_safety_interval and raw_safety_interval.lower() != "none":
            try:
                interval_minutes = int(raw_safety_interval)
                if interval_minutes <= 0:
                    # Explicit 0 (or negative) means safety cutoff disabled.
                    interval_seconds = None
                    safety_interval_cutoff = None
                    log("[MIGRATION] Safety interval set to 0 - safety cutoff disabled")
                elif interval_minutes > 1440:
                    # Backward compatibility: if already in seconds, keep as-is.
                    interval_seconds = interval_minutes
                    safety_interval_cutoff = (
                        datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)
                    ).replace(microsecond=0)
                    log(f"[MIGRATION] Safety interval cutoff: {safety_interval_cutoff} (interval: {interval_seconds}s)")
                else:
                    interval_seconds = interval_minutes * 60
                    safety_interval_cutoff = (
                        datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)
                    ).replace(microsecond=0)
                    log(f"[MIGRATION] Safety interval cutoff: {safety_interval_cutoff} (interval: {interval_seconds}s)")
            except (TypeError, ValueError):
                log(f"[MIGRATION] Invalid safety_interval='{safety_interval}', disabling safety cutoff")
        else:
            log("[MIGRATION] Safety interval is None/unset - safety cutoff disabled")
        
        # Process selected files
        #  FIX: Convert table names to file keys (Django sends table names, not file keys)
        multi_no_files_discovered = False  # Multi-file mode: track empty S3 prefix discovery
        if selected_tables:
            keys = []
            if file_mapping_mode == "single_table_per_file":
                # selected_tables may already be file keys (after upstream conversion) or table names.
                reverse_map = {str(v): str(k) for k, v in single_file_map.items()}
                unresolved_selected_items = []
                for item in selected_tables:
                    selected_item = str(item)
                    if selected_item in single_file_map:
                        keys.append(selected_item)
                        log(f"[MIGRATION] Using selected file key '{selected_item}'")
                        continue

                    file_key = reverse_map.get(selected_item)
                    if file_key:
                        keys.append(file_key)
                        log(f"[MIGRATION] Mapped table '{selected_item}' to file '{file_key}'")
                    else:
                        unresolved_selected_items.append(selected_item)
                        log(f"[MIGRATION]  Mapping missing for selected item '{selected_item}'")

                if unresolved_selected_items:
                    mapping_removed_tables.extend(unresolved_selected_items)
                    missing_items = ", ".join(sorted(set(unresolved_selected_items)))
                    log(
                        "[MIGRATION]  Mapping removed for selected table(s): "
                        f"{missing_items}. These tables will be marked failed and skipped."
                    )
                log(f"[MIGRATION] Using {len(keys)} selected files (single_table_per_file mode)")
            else:
                # multi_files_single_table: resolve file keys directly from S3 scope/prefix.
                try:
                    normalized_base_prefix = normalize_s3_folder_prefix(s3_base_prefix)
                    all_keys = _list_s3_keys_by_scope(
                        s3=s3,
                        s3_bucket=s3_bucket,
                        s3_folder_prefix=normalized_base_prefix,
                        include_subfolders=include_subfolders,
                    )
                    if multi_files_prefix:
                        log(f"[MIGRATION]  multi_files_prefix is deprecated and ignored (pattern='{multi_files_prefix}')")
                    keys = all_keys
                    if not keys:
                        candidate_keys = [str(item).strip() for item in selected_tables if str(item).strip()]
                        has_path_like_keys = any(("/" in item or "." in item) for item in candidate_keys)
                        if candidate_keys and has_path_like_keys:
                            keys = candidate_keys
                            log("[MIGRATION]  Prefix discovery returned no files, treating selected_tables as file keys")
                        else:
                            multi_no_files_discovered = True
                            log(
                                "[MIGRATION]  Prefix discovery returned no files for multi-file mode; "
                                "treating this as a table-level missing-files failure."
                            )
                except Exception as mapping_error:
                    log(f"[MIGRATION]  Error loading mapping: {mapping_error}")
                    raise
                log(f"[MIGRATION] Using {len(keys)} selected files (from {len(selected_tables)} table(s))")
        else:
            if file_mapping_mode == "single_table_per_file":
                # In single_table_per_file mode, never fallback to scanning the full bucket/prefix.
                # Process only files explicitly present in mapping.
                keys = [
                    str(file_key).strip()
                    for file_key in (single_file_map or {}).keys()
                    if str(file_key).strip()
                ]
                log(
                    f"[MIGRATION] No explicit selection provided. Using {len(keys)} mapped file(s) "
                    "from single_file_table_mapping."
                )
            else:
                # In multi_files_single_table mode, resolve files directly from S3 prefix scope.
                normalized_base_prefix = normalize_s3_folder_prefix(s3_base_prefix)
                all_keys = _list_s3_keys_by_scope(
                    s3=s3,
                    s3_bucket=s3_bucket,
                    s3_folder_prefix=normalized_base_prefix,
                    include_subfolders=include_subfolders,
                )
                if multi_files_prefix:
                    log(f"[MIGRATION]  multi_files_prefix is deprecated and ignored (pattern='{multi_files_prefix}')")
                keys = all_keys
                log(
                    f"[MIGRATION] No explicit selection provided. Using {len(keys)} discovered file(s) "
                    "from S3 prefix scope."
                )
        key_basename_counts = _build_file_basename_counts(keys)
        
        failed_removed_tables = sorted({str(t).strip() for t in mapping_removed_tables if str(t).strip()})
        if failed_removed_tables:
            try:
                conn_db = postgres_connection()
                cursor_db = conn_db.cursor()
                cursor_db.execute(
                    """
                    SELECT "table"
                    FROM source_table_selection
                    WHERE connection_id = %s
                    """,
                    (connection_id,),
                )
                existing_table_names = [str(r[0]).strip() for r in (cursor_db.fetchall() or []) if r and r[0]]
                cursor_db.close()
                conn_db.close()
                existing_lookup = {name.upper(): name for name in existing_table_names}
                failed_removed_tables = [existing_lookup.get(tbl.upper(), tbl) for tbl in failed_removed_tables]
            except Exception as resolve_err:
                log(f"[MIGRATION]  Failed to resolve exact table names for removed mappings: {resolve_err}")

        def _mark_removed_mapping_table_failed(table_name):
            """Mark removed-mapping table as failed in both table selection and migration record."""
            removed_mapping_message = "S3 table mapping removed. Kindly update schema or remap before migration."
            try:
                update_table_status_snowflake(
                    connection_id, table_name, "failed", job_name, migration_session_id
                )
            except Exception as status_error:
                log(
                    f"[MIGRATION]  Failed to update removed-mapping table "
                    f"'{table_name}' status: {status_error}"
                )

            try:
                conn_db = postgres_connection()
                cursor_db = conn_db.cursor()
                migration_record_id = None
                now_utc = datetime.now(timezone.utc)
                cursor_db.execute(
                    """
                    UPDATE migration_record
                    SET status = %s, updated_at = NOW()
                    WHERE job_name = %s
                      AND migration_session_id = %s
                      AND table_name = %s
                    """,
                    ("failed", job_name, migration_session_id, table_name),
                )
                exact_update_count = cursor_db.rowcount

                cursor_db.execute(
                    """
                    SELECT id
                    FROM migration_record
                    WHERE job_name = %s
                      AND migration_session_id = %s
                      AND table_name = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (job_name, migration_session_id, table_name),
                )
                row = cursor_db.fetchone()
                if row and row[0]:
                    migration_record_id = row[0]

                if exact_update_count == 0:
                    cursor_db.execute(
                        """
                        UPDATE migration_record
                        SET status = %s, updated_at = NOW()
                        WHERE job_name = %s
                          AND migration_session_id = %s
                          AND table_name ILIKE %s
                        """,
                        ("failed", job_name, migration_session_id, table_name),
                    )
                    cursor_db.execute(
                        """
                        SELECT id
                        FROM migration_record
                        WHERE job_name = %s
                          AND migration_session_id = %s
                          AND table_name ILIKE %s
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (job_name, migration_session_id, table_name),
                    )
                    row = cursor_db.fetchone()
                    if row and row[0]:
                        migration_record_id = row[0]

                if migration_record_id:
                    cursor_db.execute("SELECT nextval('log_log_id_seq')")
                    table_log_id = cursor_db.fetchone()[0]
                    cursor_db.execute(
                        """
                        INSERT INTO log
                            (log_id, cmp_id, connection_id, user_id, time, status, message, session, job_name, migration_record_id)
                        VALUES
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            table_log_id,
                            cmp_id,
                            connection_id,
                            user_id,
                            now_utc,
                            "F",
                            f"{table_name}: {removed_mapping_message}",
                            session,
                            job_name,
                            migration_record_id,
                        ),
                    )
                    cursor_db.execute(
                        """
                        INSERT INTO data_transfer
                            (connection_id, migration_record_id, table_name, start_time, end_time, log_id, new_rec, mod_rec, del_rec, total_rec)
                        VALUES
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            connection_id,
                            migration_record_id,
                            table_name,
                            now_utc,
                            now_utc,
                            table_log_id,
                            0,
                            0,
                            0,
                            0,
                        ),
                    )
                else:
                    log(
                        f"[MIGRATION]  Could not find migration_record_id for removed-mapping table "
                        f"'{table_name}' (job_name={job_name}, session={migration_session_id})"
                    )

                conn_db.commit()
                cursor_db.close()
                conn_db.close()
            except Exception as record_error:
                log(
                    f"[MIGRATION]  Failed to mark migration_record failed for "
                    f"removed-mapping table '{table_name}': {record_error}"
                )

        if not keys:
            if file_mapping_mode == "multi_files_single_table" and multi_no_files_discovered:
                table_name = sanitize_identifier(str(multi_files_table_name or "multi_files_single_table").strip())
                error_msg = "No files found in folder for processing."
                log(f"[MIGRATION]  {error_msg}")
                try:
                    error_context = {
                        "table_name": table_name,
                        "service_name": "S3 to Snowflake",
                        "error_type": "TABLE_MIGRATION_FAILED",
                        "cmp_id": cmp_id,
                        "connection_id": connection_id,
                        "user_id": user_id,
                        "job_name": job_name,
                        "migration_session_id": migration_session_id,
                        "session": session,
                        "source_file": None,
                        "log_id": log_id
                    }
                    handle_service_error(
                        error=FileNotFoundError(error_msg),
                        context=error_context,
                        log_files=None,
                        send_email=False,
                        is_final_attempt=is_final_attempt
                    )
                except Exception as log_error:
                    log(f"[MIGRATION]  Failed to write table-level error for '{table_name}': {log_error}")
                _mark_selected_tables_failed_on_terminal_error()
                try:
                    log_migration_completion(
                        execution_time=0.0,
                        total_rows=0,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        session=migration_session_id,
                        total_tables=1,
                        successful_tables=0,
                        failed_tables=1,
                        job_name=job_name,
                        failure_reason=error_msg,
                        status_override="F",
                        include_operation_counts=True,
                        suppress_reason_in_completion=True,
                        celery_child_mode=request.celery_child_mode,
                    )
                except Exception as completion_log_error:
                    log(f"[MIGRATION]  Failed to log migration completion: {completion_log_error}")
                return {
                    "status": "failed",
                    "message": error_msg,
                    "files_processed": 0,
                    "failed_tables": 1,
                    "processed_tables": [],
                }
            if failed_removed_tables:
                for table_name in failed_removed_tables:
                    _mark_removed_mapping_table_failed(table_name)
                error_msg = (
                    "S3 table mapping removed for selected table(s): "
                    + ", ".join(failed_removed_tables)
                    + ". Kindly update schema or remap before migration."
                )
                log(f"[MIGRATION]  {error_msg}")
                try:
                    log_migration_completion(
                        execution_time=0.0,
                        total_rows=0,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        session=migration_session_id,
                        total_tables=len(failed_removed_tables),
                        successful_tables=0,
                        failed_tables=len(failed_removed_tables),
                        job_name=job_name,
                        failure_reason=error_msg,
                        status_override="F",
                        include_operation_counts=True,
                        suppress_reason_in_completion=True,
                        celery_child_mode=request.celery_child_mode,
                    )
                except Exception as completion_log_error:
                    log(f"[MIGRATION]  Failed to log migration completion: {completion_log_error}")
                return {
                    "status": "failed",
                    "message": error_msg,
                    "files_processed": 0,
                    "failed_tables": len(failed_removed_tables),
                    "processed_tables": [],
                }
            error_msg = "No files to process"
            log(f"[MIGRATION]  {error_msg}")
            try:
                total_tables = len(selected_tables) if selected_tables is not None else 0
                failed_tables = total_tables if total_tables > 0 else 0
                log_migration_completion(
                    execution_time=0.0,
                    total_rows=0,
                    cmp_id=cmp_id,
                    connection_id=connection_id,
                    user_id=user_id,
                    session=migration_session_id,
                    total_tables=total_tables,
                    successful_tables=0,
                    failed_tables=failed_tables,
                    job_name=job_name,
                    failure_reason=error_msg,
                    status_override="F",
                    include_operation_counts=True,
                    suppress_reason_in_completion=True,
                    celery_child_mode=request.celery_child_mode,
                )
            except Exception as completion_log_error:
                log(f"[MIGRATION]  Failed to log migration completion: {completion_log_error}")
            return {"status": "failed", "message": error_msg, "files_processed": 0}
        
        # FILE MAPPING MODE -- Multi-file -> Single table setup
        single_table = None
        original_table_name = None  #  Track original table name for status updates
        multi_files_schema_override = None
        if file_mapping_mode == "multi_files_single_table":
            log(f"[MIGRATION] File mapping mode: Multi-file  Single table")
            if multi_files_table_name:
                original_table_name = sanitize_identifier(multi_files_table_name)
                single_table = sanitize_identifier(multi_files_table_name)
                log(f"[MIGRATION] Using user-specified unified table name: {single_table} (original: {original_table_name})")
            else:
                suggested_name = suggest_common_table_name(keys)
                original_table_name = sanitize_identifier(suggested_name)
                single_table = sanitize_identifier(suggested_name)
                log(f"[MIGRATION] Using unified table name from suggestion: {single_table} (original: {original_table_name})")
            
            # Sort files chronologically
            dated_keys = []
            for k in keys:
                date_obj, score = extract_date_from_filename(k)
                if date_obj:
                    dated_keys.append((k, date_obj))
                else:
                    try:
                        head = s3.head_object(Bucket=s3_bucket, Key=k)
                        last_mod = head["LastModified"].date()
                        dated_keys.append((k, last_mod))
                    except Exception:
                        dated_keys.append((k, date.today()))
            keys = [k for k, _ in sorted(dated_keys, key=lambda x: x[1])]
            log(f"[MIGRATION]  Files sorted chronologically")
        
        # In multi-files-single-table mode, only allow new columns if they are present
        # across ALL files in this batch. Otherwise, treat them as incompatibilities.
        allowed_new_columns = None
        if file_mapping_mode == "multi_files_single_table":
            table_meta = {}
            if isinstance(last_checked_data, dict) and single_table:
                table_meta = last_checked_data.get(single_table, {}) or last_checked_data.get(original_table_name, {})
                if not table_meta:
                    lookup = {str(k).upper(): v for k, v in last_checked_data.items()}
                    table_meta = lookup.get(str(single_table).upper(), {}) if single_table else {}
            expected_columns_for_multi = (
                table_meta.get("table_fields", {}) if isinstance(table_meta, dict) else {}
            )
            if isinstance(expected_columns_for_multi, dict) and expected_columns_for_multi:
                allowed_new_columns = compute_common_new_columns_for_multi_files(
                    s3, s3_bucket, keys, file_type,
                    expected_columns_for_multi,
                    delimiter=delimiter,
                    quotechar=quotechar,
                    skipheaderlines=skipheaderlines,
                    json_mode=json_mode,
                    xml_mode=xml_mode,
                    escape_char=escape_char,
                    null_sequence=null_sequence,
                    skip_footer_lines=skip_footer_lines,
                    line_separator=line_separator,
                )
                if not allowed_new_columns:
                    log("[VALIDATE]  No new columns common to all files; schema additions disabled for this run.")

        # In multi-file single-table mode, infer schema across ALL files before table creation
        # so numeric types (e.g., FLOAT vs NUMBER) are correctly resolved.
        if file_mapping_mode == "multi_files_single_table" and keys:
            def _type_family(t: str) -> str:
                t = str(t or "").upper()
                if any(x in t for x in ("FLOAT", "DOUBLE", "REAL", "NUMBER", "DECIMAL", "NUMERIC")):
                    return "numeric"
                if "TIMESTAMP" in t or t == "DATE":
                    return "datetime"
                if "BOOLEAN" in t:
                    return "boolean"
                if "VARIANT" in t:
                    return "variant"
                return "text"

            def _widen_type(existing: str, incoming: str) -> str:
                if not existing:
                    return incoming
                if not incoming:
                    return existing
                e = str(existing).upper()
                i = str(incoming).upper()
                ef = _type_family(e)
                if ef != _type_family(i):
                    # Keep existing when families differ; schema validation will catch incompatibilities.
                    return existing
                if ef == "numeric":
                    # Prefer FLOAT if any sample indicates decimals.
                    if "FLOAT" in e or "FLOAT" in i:
                        return "FLOAT"
                return existing

            merged_schema = {}
            schema_files_attempted = 0
            schema_files_skipped = 0
            log("[MIGRATION][SCHEMA_PRE] Inferring schema across all files for multi-file table creation...")
            for key in keys:
                schema_files_attempted += 1
                actual_columns, err = infer_columns_from_s3_sample(
                    s3, s3_bucket, key, file_type,
                    delimiter=delimiter,
                    quotechar=quotechar,
                    skipheaderlines=skipheaderlines,
                    json_mode=json_mode,
                    xml_mode=xml_mode,
                    escape_char=escape_char,
                    null_sequence=null_sequence,
                    skip_footer_lines=skip_footer_lines,
                    line_separator=line_separator,
                )
                if actual_columns is None:
                    schema_files_skipped += 1
                    log(f"[MIGRATION][SCHEMA_PRE]  Skipping '{key}' (no columns inferred): {err}")
                    continue
                for col_name, col_meta in actual_columns.items():
                    col_u = sanitize_column_name(col_name).upper()
                    if col_u in {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}:
                        continue
                    incoming_type = None
                    if isinstance(col_meta, dict):
                        incoming_type = col_meta.get("data_type")
                    existing_meta = merged_schema.get(col_u)
                    existing_type = existing_meta.get("data_type") if isinstance(existing_meta, dict) else None
                    chosen_type = _widen_type(existing_type, incoming_type)
                    merged_schema[col_u] = {"data_type": chosen_type}
            if merged_schema:
                multi_files_schema_override = merged_schema
                log(
                    f"[MIGRATION][SCHEMA_PRE] Merged schema across {len(keys)} file(s): "
                    f"{len(merged_schema)} column(s) (sampled)."
                )
                log(
                    f"[MIGRATION][SCHEMA_PRE] Files scanned for schema: {schema_files_attempted}, "
                    f"skipped: {schema_files_skipped}"
                )
            else:
                log("[MIGRATION][SCHEMA_PRE] No schema override derived; falling back to per-file inference.")
                log(
                    f"[MIGRATION][SCHEMA_PRE] Files scanned for schema: {schema_files_attempted}, "
                    f"skipped: {schema_files_skipped}"
                )
        
        # DROP AND RELOAD
        if drop_and_reload:
            log("=" * 80)
            log("  DROP AND RELOAD MODE ENABLED (S3)")
            log("=" * 80)
            try:
                tables_to_drop = set()
                for key in keys:
                    if file_mapping_mode == "multi_files_single_table":
                        tables_to_drop.add(single_table)
                    else:
                        internal_name = table_from_file_key(key)
                        mapped_name = _resolve_single_file_mapped_name(
                            file_key=key,
                            single_file_map=single_file_map,
                            basename_counts=key_basename_counts,
                        )
                        table_name = sanitize_identifier(str(mapped_name).strip()) if mapped_name else internal_name
                        tables_to_drop.add(table_name)
                
                normalized_tables = {sanitize_identifier(tbl).upper() for tbl in tables_to_drop}
                log(f"[DROP] Tables to drop: {normalized_tables}")
                
                with conn.cursor() as cur:
                    if sf_database:
                        cur.execute(f"USE DATABASE {sf_database}")
                    if sf_schema:
                        cur.execute(f"USE SCHEMA {sf_schema}")
                    
                    for table_name in normalized_tables:
                        try:
                            qualified_table = f'"{sf_database}"."{sf_schema.upper()}"."{table_name}"'
                            drop_sql = f"DROP TABLE IF EXISTS {qualified_table}"
                            cur.execute(drop_sql)
                            log(f"    Dropped table: {table_name}")
                        except Exception as drop_error:
                            log(f"    Failed to drop table {table_name}: {drop_error}")
                
                log(" Drop and reload preparation complete")
                log("=" * 80)
            except Exception as drop_error:
                log(f" Error during drop and reload: {drop_error}")
        
        # Setup Storage Integration (root level - no prefix)
        gcs_allowed_url_prefix = f"gcs://{gcs_bucket_name}/"
        log(f"[MIGRATION] Ensuring storage integration: {sf_storage_integration} with prefix: {gcs_allowed_url_prefix}")
        existed = ensure_gcs_storage_integration(conn, sf_storage_integration, gcs_allowed_url_prefix)
        try:
            sa_email = get_gcp_service_account(conn, sf_storage_integration)
            ensure_bucket_iam_for_integration(gcs, gcs_bucket_name, sa_email)
            log(f"[MIGRATION]  Storage integration IAM configured")
        except Exception as e:
            log(f"[MIGRATION]  Storage integration IAM check: {e if not existed else 'continuing...'}")
        
        #  FIX: Wrap storage integration check in try-except to catch and email errors
        try:
            assert_allowed_prefix_matches(conn, gcs_allowed_url_prefix, sf_storage_integration)
        except Exception as storage_error:
            error_msg = f"Storage integration validation failed: {str(storage_error)}"
            log(f"[MIGRATION]  {error_msg}")
            
            if is_final_attempt:
                try:
                    error_context = {
                        "table_name": "Storage Integration",
                        "service_name": "S3 to Snowflake",
                        "error_type": "STORAGE_INTEGRATION_ERROR",
                        "cmp_id": cmp_id,
                        "connection_id": connection_id,
                        "user_id": user_id,
                        "job_name": job_name,
                        "migration_session_id": migration_session_id,
                        "company_name": company_name,
                        "connection_name": connection_name,
                        "session": session
                    }
                    
                    error_result = handle_service_error(
                        error=storage_error,
                        context=error_context,
                        log_files=None,
                        send_email=True,
                        is_final_attempt=is_final_attempt
                    )
                    
                    if error_result.get("email_sent"):
                        log(f" Storage integration error email sent")
                except Exception as email_error:
                    log(f" Failed to send storage integration error email: {email_error}")
            _mark_selected_tables_failed_on_terminal_error()
            raise Exception(error_msg)
        
        # Process each file using streaming chunks
        processed = 0
        total_rows = 0
        total_inserts = 0
        total_updates = 0
        total_deletes = 0
        table_operation_counts = {}
        user_pk_map = {}
        migration_start_time = datetime.now(timezone.utc)
        truncated_tables = set()
        is_delta_refresh = False
        # Track successfully processed files for multi-file single table mapping update
        processed_files_by_table = {}  # {table_name: [list of file keys]}
        missing_files_by_table = {}    # {table_name: [list of missing file keys]}
        incompatible_files_by_table = {}  # {table_name: [list of schema/type-incompatible file keys]}
        missing_files_single_mode = set()  # track missing file keys in single_table_per_file mode
        available_files_seen = 0       # Count mapped files that still exist in S3
        first_chunk_df = None          # Safe default for final multi-file logging paths
        multi_staged_source_rows = 0   # Source rows staged across files (multi-file single-table)
        multi_staging_table_name = (
            staging_name(single_table.upper())
            if file_mapping_mode == "multi_files_single_table" and single_table
            else None
        )
        #  FIX: Track tables set to "in_progress" so we can update them to "failed" on error
        tables_in_progress = set()  # Track tables set to "in_progress"
        processed_tables = set()    #  Track tables that were processed (completed or failed)
        skipped_tables_delta = set()   #  Track tables skipped because already synced
        skipped_tables_safety = set()  #  Track tables skipped because modified too recently
        skipped_tables = set()         #  Combined set for easy reference
        table_exists_cache = {}        #  Cache Snowflake table existence checks

        if failed_removed_tables:
            for table_name in failed_removed_tables:
                _mark_removed_mapping_table_failed(table_name)
                processed_tables.add(table_name)
                if failure_tracker:
                    failure_tracker.add_failure(
                        table_name,
                        "S3 table mapping removed. Kindly update schema or remap before migration.",
                        "S3 to Snowflake"
                    )
            log("[MIGRATION]  Skipping removed-mapping table(s): " + ", ".join(failed_removed_tables))
        
        for table_name in last_checked_data.keys():
            table_meta = last_checked_data.get(table_name, {})
            if table_meta.get("last_checked_date") and not drop_and_reload:
                is_delta_refresh = True
                break
        
        try:
            # For multi-file single table operations, set status to "in_progress" once at the beginning
            if file_mapping_mode == "multi_files_single_table" and original_table_name:
                try:
                    update_table_status_snowflake(
                        connection_id, original_table_name, "in_progress", job_name, migration_session_id
                    )
                    log(f"[MIGRATION]  Set initial status to 'in_progress' for multi-file single table: {original_table_name}")
                except Exception as status_error:
                    log(f"[MIGRATION]  Failed to set initial status for multi-file single table: {status_error}")
            
            for key in keys:
                log("\n" + "=" * 72)
                log(f"[MIGRATION] Processing file: {key}")
                log("=" * 72)
                
                # Determine table name
                if file_mapping_mode == "multi_files_single_table":
                    table = single_table
                else:
                    internal_name = table_from_file_key(key)
                    mapped_name = _resolve_single_file_mapped_name(
                        file_key=key,
                        single_file_map=single_file_map,
                        basename_counts=key_basename_counts,
                    )
                    table = mapped_name or internal_name
                
                # Track staging table name for this file (assigned during first chunk)
                current_staging_table = multi_staging_table_name if file_mapping_mode == "multi_files_single_table" else None
                
                # Get table metadata for incremental sync
                table_metadata = last_checked_data.get(table, {})
                last_synced_timestamp = table_metadata.get("last_checked_date")
                truncate_table = table_metadata.get("truncate_data", False)
                expected_columns = table_metadata.get("table_fields", {}) if isinstance(table_metadata, dict) else {}
                
                #  Set status to 'in_progress' at the START of file processing
                # For multi-file single table operations, defer status update until end to preserve timing
                # This ensures start_time is captured in the migration_record (matches Dynamics service)
                if file_mapping_mode != "multi_files_single_table":
                    try:
                        #  Use original table name for status updates (matches source_table_selection)
                        # For multi-file mode: use original_table_name (e.g., "Sales_")
                        # For single-file mode: use table (already correct)
                        status_table_name = (original_table_name or table)
                        
                        update_table_status_snowflake(
                            connection_id, status_table_name, "in_progress", job_name, migration_session_id
                        )
                        tables_in_progress.add(table)  #  Track this table
                    except Exception:
                        pass
                else:
                    # For multi-file single table, track the table but don't update status yet
                    tables_in_progress.add(table)  #  Track this table
                
                # TRUNCATE if needed
                if truncate_table and table not in truncated_tables:
                    try:
                        if not sf_database or not sf_schema:
                            raise ValueError("sf_database and sf_schema are required for truncate operation")
                        final_table = f"{Q(sf_database)}.{Q(sf_schema)}.{Q(table)}"
                        with conn.cursor() as cur:
                            cur.execute(f"TRUNCATE TABLE {final_table}")
                        truncated_tables.add(table)
                    except Exception as e:
                        log(f"[FILE]  Failed to truncate target table for {table}: {e}")
                # Check if file should be processed (incremental sync + safety interval)
                should_process = True
                skip_reason = None
                
                try:
                    s3.head_object(Bucket=s3_bucket, Key=key)
                    available_files_seen += 1
                except ClientError as e:
                    error_code = (
                        e.response.get("Error", {}).get("Code")
                        if getattr(e, "response", None)
                        else None
                    )
                    if str(error_code) in ("404", "NoSuchKey", "NotFound"):
                        error_message = f"S3 file not found: s3://{s3_bucket}/{key}"
                        log(f"[MIGRATION]  {error_message}")
                        if file_mapping_mode == "multi_files_single_table":
                            missing_files_by_table.setdefault(table, []).append(key)
                            log(
                                f"[MIGRATION]  Multi-file mode: skipping missing file '{key}' "
                                f"for table '{table}' and continuing with remaining files"
                            )
                        else:
                            missing_files_single_mode.add(key)
                            if failure_tracker:
                                failure_tracker.add_failure(table, error_message, "S3 to Snowflake")
                            try:
                                error_context = {
                                    "table_name": table,
                                    "service_name": "S3 to Snowflake",
                                    "error_type": "TABLE_MIGRATION_FAILED",
                                    "cmp_id": cmp_id,
                                    "connection_id": connection_id,
                                    "user_id": user_id,
                                    "job_name": job_name,
                                    "migration_session_id": migration_session_id,
                                    "session": session,
                                    "source_file": key,
                                    "log_id": log_id
                                }
                                handle_service_error(
                                    error=FileNotFoundError(error_message),
                                    context=error_context,
                                    log_files=None,
                                    send_email=False,
                                    is_final_attempt=is_final_attempt
                                )
                            except Exception as log_error:
                                log(f"[MIGRATION]  Failed to write table-level error log for '{table}': {log_error}")
                            try:
                                status_table_name = (original_table_name or table)
                                update_table_status_snowflake(
                                    connection_id, status_table_name, "failed", job_name, migration_session_id
                                )
                                if table in tables_in_progress:
                                    tables_in_progress.remove(table)
                                processed_tables.add(table)
                                skipped_tables.discard(table)
                            except Exception as status_error:
                                log(f"[MIGRATION]  Failed to mark table '{table}' as failed: {status_error}")
                        continue
                        raise

                # Content-based compatibility check (no extension filtering).
                is_type_compatible, type_reason = is_file_compatible_with_type(
                    s3=s3,
                    bucket=s3_bucket,
                    key=key,
                    file_type=file_type,
                    delimiter=delimiter,
                    quotechar=quotechar,
                    skipheaderlines=skipheaderlines,
                    escape_char=escape_char,
                    skip_footer_lines=skip_footer_lines,
                    line_separator=line_separator,
                )
                if not is_type_compatible:
                    compatibility_message = (
                        f"Skipping file '{key}': incompatible with selected file_type '{file_type}'."
                    )
                    log(f"[MIGRATION][TYPE_CHECK]  {compatibility_message}")
                    incompatible_files_by_table.setdefault(table, []).append(key)
                    if failure_tracker:
                        failure_tracker.add_failure(table, compatibility_message, "S3 to Snowflake")
                    if file_mapping_mode != "multi_files_single_table":
                        try:
                            #  Log exact table-level error message (matches email content)
                            error_context = {
                                "table_name": table,
                                "service_name": "S3 to Snowflake",
                                "error_type": "FILE_TYPE_MISMATCH",
                                "cmp_id": cmp_id,
                                "connection_id": connection_id,
                                "user_id": user_id,
                                "job_name": job_name,
                                "migration_session_id": migration_session_id,
                                "session": session,
                                "source_file": key,
                                "log_id": log_id
                            }
                            handle_service_error(
                                error=ValueError(compatibility_message),
                                context=error_context,
                                log_files=None,
                                send_email=False,
                                is_final_attempt=is_final_attempt
                            )
                            status_table_name = (original_table_name or table)
                            update_table_status_snowflake(
                                connection_id, status_table_name, "failed", job_name, migration_session_id
                            )
                            if table in tables_in_progress:
                                tables_in_progress.remove(table)
                            processed_tables.add(table)
                            skipped_tables.discard(table)
                        except Exception as status_error:
                            log(f"[MIGRATION]  Failed to mark table '{table}' as failed: {status_error}")
                    continue

                # In multi-file/single-table mode, enforce schema compatibility before loading.
                # This avoids noisy Snowflake load errors when users accidentally map heterogeneous files.
                if file_mapping_mode == "multi_files_single_table" and isinstance(expected_columns, dict) and expected_columns:
                    is_compatible, _, validation_error = validate_file_structure_matches(
                        s3, s3_bucket, key, file_type,
                        expected_columns,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                        enforce_type_compatibility=True,
                        allowed_new_columns=allowed_new_columns,
                    )
                    if not is_compatible:
                        compatibility_message = (
                            f"Skipping file '{key}' for table '{table}': schema/type mismatch. "
                            f"{validation_error}"
                        )
                        log(f"[MIGRATION][MULTI_VALIDATION]  {compatibility_message}")
                        incompatible_files_by_table.setdefault(table, []).append(key)
                        if failure_tracker:
                            failure_tracker.add_failure(table, compatibility_message, "S3 to Snowflake")
                        continue

                if not drop_and_reload:
                    try:
                        head = s3.head_object(Bucket=s3_bucket, Key=key)
                        file_last_modified = head["LastModified"].replace(tzinfo=timezone.utc)
                        
                        # If destination table is missing, force full load (do not delta-skip).
                        if last_synced_timestamp and sf_database and sf_schema:
                            cache_key = str(table).strip().upper()
                            if cache_key not in table_exists_cache:
                                table_exists_cache[cache_key] = _snowflake_table_exists(
                                    conn, sf_database, sf_schema, cache_key
                                )
                            if not table_exists_cache[cache_key]:
                                log(
                                    f"[MIGRATION]  Destination table '{table}' is missing in Snowflake; "
                                    "forcing full load and clearing last_synced."
                                )
                                last_synced_timestamp = None
                                if table in last_checked_data and isinstance(last_checked_data[table], dict):
                                    last_checked_data[table]["last_checked_date"] = None
                                try:
                                    conn_db = postgres_connection()
                                    cursor_db = conn_db.cursor()
                                    cursor_db.execute(
                                        """
                                        UPDATE source_table_selection
                                        SET last_synced = NULL
                                        WHERE connection_id = %s
                                          AND (UPPER("table") = %s OR "table" = %s)
                                        """,
                                        (
                                            connection_id,
                                            str(table).strip().upper(),
                                            table,
                                        ),
                                    )
                                    conn_db.commit()
                                finally:
                                    if cursor_db:
                                        cursor_db.close()
                                    if conn_db:
                                        conn_db.close()

                        # Apply safety interval only when configured.
                        if safety_interval_cutoff and file_last_modified > safety_interval_cutoff:
                            should_process = False
                            skip_reason = "safety"
                            log(f"[FILE]  Skipping file '{key}' - modified too recently (after safety interval cutoff: {safety_interval_cutoff})")
                        
                        # Apply delta sync: skip files not modified since last sync
                        elif last_synced_timestamp:
                            last_synced_dt = parse_datetime_from_db(last_synced_timestamp)
                            if last_synced_dt:
                                if file_last_modified <= last_synced_dt:
                                    should_process = False
                                    skip_reason = "delta"
                                    log(f"[FILE]  Skipping file '{key}' - not modified since last sync (file: {file_last_modified}, last_sync: {last_synced_dt})")
                            else:
                                log(f"[FILE]  Could not parse last_synced_timestamp '{last_synced_timestamp}', processing anyway")
                    except Exception as e:
                        log(f"[FILE]  Could not check file modification time: {e}, processing anyway")
                #  FIX: Only set to "in_progress" when we actually start processing
                # Skip files that don't need processing (don't set status for skipped files)
                if not should_process:
                    log(f"[FILE]  Skipping file '{key}' for table '{table}' (not modified or too recent)")
                    skip_time_utc = datetime.now(timezone.utc)
                    if file_mapping_mode == "multi_files_single_table":
                        if skip_reason == "safety":
                            skipped_tables_safety.add(table)
                        else:
                            skipped_tables_delta.add(table)
                        skipped_tables.add(table)
                    else:
                        try:
                            # Mark skipped table complete immediately so end timing is captured at skip time.
                            if skip_reason == "safety":
                                # Keep last_synced unchanged for safety skips.
                                update_table_status_snowflake(
                                    connection_id, table, "completed", job_name, migration_session_id, sync_end_time=None
                                )
                                skipped_tables_safety.add(table)
                            else:
                                # Delta skip should advance last_synced to skip time.
                                update_table_status_snowflake(
                                    connection_id, table, "completed", job_name, migration_session_id, skip_time_utc
                                )
                                skipped_tables_delta.add(table)
                            skipped_tables.add(table)
                            if table in tables_in_progress:
                                tables_in_progress.remove(table)
                            processed_tables.add(table)
                        except Exception as status_error:
                            log(f"[FILE]  Failed to mark skipped table '{table}' as completed: {status_error}")

                        try:
                            if job_name:
                                data_transfer_logs(
                                    cmp_id=cmp_id,
                                    connection_id=connection_id,
                                    user_id=user_id,
                                    table_name=table,
                                    start_time=skip_time_utc,
                                    end_time=skip_time_utc,
                                    columns_data=0,
                                    session=session,
                                    insert_success=0,
                                    insert_failure=0,
                                    upsert_success=0,
                                    upsert_failure=0,
                                    job_name=job_name
                                )
                        except Exception as log_error:
                            log(f"[FILE]  Failed to log skipped table '{table}': {log_error}")
                    continue
                
                # Initialize file processing variables
                file_start_time = datetime.now(timezone.utc)
                gcs_parquet_urls = []
                first_chunk_df = None
                total_file_rows = 0
                chunk_count_for_file = 0
                insert_success = 0
                insert_failure = 0
                upsert_success = 0
                upsert_failure = 0
                delete_success = 0
                
                try:
                    # Stream chunks from S3
                    enforce_row_schema_consistency = True
                    enforce_json_key_consistency = (
                        enforce_row_schema_consistency
                        and file_type == "json"
                        and (json_mode or "unpacked").lower() == "unpacked"
                    )
                    json_expected_keys_state = (
                        {"expected": None} if enforce_json_key_consistency else None
                    )

                    for chunk_df, chunk_num, is_last_chunk, last_mod, total_rows_chunk in read_s3_file_in_chunks(
                        s3, s3_bucket, key, file_type,
                        chunk_size=chunk_count,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                        enforce_row_schema_consistency=enforce_row_schema_consistency,
                        enforce_json_key_consistency=enforce_json_key_consistency,
                        json_expected_keys_state=json_expected_keys_state,
                    ):
                        if chunk_df.empty:
                            if is_last_chunk:
                                break
                            continue
                        
                        chunk_count_for_file += 1
                        total_file_rows = total_rows_chunk
                        
                        # Store first chunk for schema detection
                        if chunk_num == 1:
                            first_chunk_df = chunk_df.copy()
                        
                        # LOAD METHOD -- Use custom PK for each table from mapping
                        if load_method == "upsert_custom_key" and chunk_num == 1:
                            table_u = table.upper()
                            if table_u not in user_pk_map:
                                # Look up PK for this specific table (case-insensitive)
                                table_pk = custom_pk_mapping.get(table) or custom_pk_mapping.get(table_u) or []
                                if table_pk:
                                    filtered_pk = sanitize_custom_pk_columns(table_pk)
                                    removed_cols = [sanitize_column_name(c).upper() for c in table_pk if sanitize_column_name(c).upper() in DISALLOWED_CUSTOM_PK_COLUMNS]
                                    user_pk_map[table_u] = filtered_pk
                                    if removed_cols:
                                        log(f"[MIGRATION]  Table '{table}' disallowed PK columns removed: {removed_cols}")
                                    log(f"[MIGRATION] Table '{table}' using custom PK: {table_pk}")
                                else:
                                    user_pk_map[table_u] = []
                                    log(f"[MIGRATION]  Table '{table}' has no custom PK configured")
                        
                        user_pk_for_table = user_pk_map.get(table.upper(), []) if load_method == "upsert_custom_key" else []

                        # Ensure tables exist (only on first chunk)
                        if chunk_num == 1:
                            # Keep SourceTableSelection schema in sync for single-table-per-file
                            # and multi-files-single-table modes. New columns discovered during
                            # migration were already being added in Snowflake, but
                            # source_table_selection.table_fields stayed stale.
                            if file_mapping_mode in ("single_table_per_file", "multi_files_single_table"):
                                try:
                                    target_table_for_schema = original_table_name or table
                                    inferred_columns = infer_columns_from_df(
                                        chunk_df,
                                        load_method,
                                        table_name=target_table_for_schema,
                                        internal_name=table,
                                        custom_primary_key=custom_pk_mapping if load_method == "upsert_custom_key" else None,
                                    )
                                    if isinstance(inferred_columns, dict) and inferred_columns:
                                        # Persist schema in standardized uppercase keys for consistency
                                        # with Snowflake identifier conventions.
                                        normalized_inferred_columns = {}
                                        for col_name, meta in inferred_columns.items():
                                            norm_name = sanitize_column_name(str(col_name)).upper()
                                            if not norm_name:
                                                continue
                                            normalized_inferred_columns[norm_name] = meta

                                        normalized_expected_columns = {}
                                        if isinstance(expected_columns, dict):
                                            for col_name, meta in expected_columns.items():
                                                norm_name = sanitize_column_name(str(col_name)).upper()
                                                if not norm_name:
                                                    continue
                                                normalized_expected_columns[norm_name] = meta
                                        
                                        # Detect column rename (remove + add) in single_table_per_file mode.
                                        # This is treated as an error to avoid silent schema drift.
                                        if (
                                            file_mapping_mode == "single_table_per_file"
                                            and not drop_and_reload
                                            and normalized_expected_columns
                                        ):
                                            metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
                                            inferred_cols = set(normalized_inferred_columns.keys()) - metadata_cols
                                            expected_cols = set(normalized_expected_columns.keys()) - metadata_cols
                                            removed_cols = expected_cols - inferred_cols
                                            added_cols = inferred_cols - expected_cols
                                            if removed_cols and added_cols:
                                                removed_preview = ", ".join(sorted(list(removed_cols))[:3])
                                                added_preview = ", ".join(sorted(list(added_cols))[:3])
                                                raise ValueError(
                                                    "Column name changed detected. "
                                                    f"Removed: {removed_preview}. Added: {added_preview}. "
                                                    "Please correct the file header or run Drop & Reload."
                                                )
                                        
                                        # Detect column rename in multi_files_single_table when ALL files
                                        # share the new columns (i.e., allowed_new_columns covers them).
                                        if (
                                            file_mapping_mode == "multi_files_single_table"
                                            and not drop_and_reload
                                            and normalized_expected_columns
                                            and allowed_new_columns is not None
                                        ):
                                            metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
                                            inferred_cols = set(normalized_inferred_columns.keys()) - metadata_cols
                                            expected_cols = set(normalized_expected_columns.keys()) - metadata_cols
                                            removed_cols = expected_cols - inferred_cols
                                            added_cols = inferred_cols - expected_cols
                                            if removed_cols and added_cols and added_cols.issubset(allowed_new_columns):
                                                removed_preview = ", ".join(sorted(list(removed_cols))[:3])
                                                added_preview = ", ".join(sorted(list(added_cols))[:3])
                                                raise ValueError(
                                                    "Column name changed detected. "
                                                    f"Removed: {removed_preview}. Added: {added_preview}. "
                                                    "Please correct the file header or run Drop & Reload."
                                                )

                                        if normalized_inferred_columns != normalized_expected_columns:
                                            should_update_schema = True
                                            if file_mapping_mode == "multi_files_single_table" and allowed_new_columns is not None:
                                                metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
                                                inferred_cols = set(normalized_inferred_columns.keys()) - metadata_cols
                                                expected_cols = set(normalized_expected_columns.keys()) - metadata_cols
                                                new_cols = inferred_cols - expected_cols
                                                disallowed_new = new_cols - allowed_new_columns
                                                if disallowed_new:
                                                    should_update_schema = False
                                                    log(
                                                        f"[MIGRATION][SCHEMA_SYNC]  Skipping schema update for '{table}' "
                                                        f"due to unexpected new columns: {disallowed_new}"
                                                    )
                                            if should_update_schema:
                                                conn_db = None
                                                cursor_db = None
                                                try:
                                                    conn_db = postgres_connection()
                                                    cursor_db = conn_db.cursor()
                                                    cursor_db.execute(
                                                        """
                                                        UPDATE source_table_selection
                                                        SET table_fields = %s
                                                        WHERE connection_id = %s
                                                          AND (UPPER("table") = %s OR "table" = %s)
                                                        """,
                                                        (
                                                            json.dumps(normalized_inferred_columns),
                                                            connection_id,
                                                            str(target_table_for_schema).strip().upper(),
                                                            target_table_for_schema,
                                                        ),
                                                    )
                                                    conn_db.commit()
                                                finally:
                                                    if cursor_db:
                                                        cursor_db.close()
                                                    if conn_db:
                                                        conn_db.close()
                                                expected_columns = normalized_inferred_columns
                                                if table in last_checked_data and isinstance(last_checked_data[table], dict):
                                                    last_checked_data[table]["table_fields"] = normalized_inferred_columns
                                                log(
                                                    f"[MIGRATION][SCHEMA_SYNC]  Updated source_table_selection schema for "
                                                    f"table '{table}' with {len(normalized_inferred_columns)} column(s)"
                                                )
                                                log(
                                                    f"[MIGRATION][SCHEMA_SYNC]  table_fields JSON persisted for "
                                                    f"table '{target_table_for_schema}': {json.dumps(normalized_inferred_columns, ensure_ascii=True)}"
                                                )
                                except Exception as schema_sync_error:
                                    log(
                                        f"[MIGRATION][SCHEMA_SYNC]  Failed to update source_table_selection schema "
                                        f"for table '{table}': {schema_sync_error}"
                                    )
                                    # If this is a column rename error, fail the table (do not proceed).
                                    if (
                                        file_mapping_mode in ("single_table_per_file", "multi_files_single_table")
                                        and "Column name changed detected" in str(schema_sync_error)
                                    ):
                                        raise
                            staging_table_name = ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                user_pk_for_table,
                                sf_database,
                                sf_schema,
                                staging_table_name=multi_staging_table_name if file_mapping_mode == "multi_files_single_table" else None,
                                schema_override=multi_files_schema_override if file_mapping_mode == "multi_files_single_table" else None,
                            )
                            current_staging_table = staging_table_name
                            # Store staging table name for cleanup tracking
                            if 'staging_tables' not in locals():
                                staging_tables = {}
                            staging_tables[table] = staging_table_name
                        elif current_staging_table:
                            ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                user_pk_for_table,
                                sf_database,
                                sf_schema,
                                staging_table_name=current_staging_table,
                                schema_override=multi_files_schema_override if file_mapping_mode == "multi_files_single_table" else None,
                            )

                        # Reorder columns to match Snowflake table order before CSV upload.
                        chunk_df = reorder_chunk_columns_for_snowflake(
                            chunk_df,
                            load_method=load_method,
                            user_pk=user_pk_for_table
                        )
                        if (
                            current_staging_table
                            and sf_database
                            and sf_schema
                        ):
                            chunk_df = align_chunk_to_table_column_order(
                                conn=conn,
                                chunk_df=chunk_df,
                                table_name=current_staging_table,
                                sf_database=sf_database,
                                sf_schema=sf_schema,
                            )
                            try:
                                debug_cols = list(chunk_df.columns)
                                debug_first = (
                                    chunk_df.iloc[0].to_dict()
                                    if not chunk_df.empty else {}
                                )
                                log(
                                    f"[SCHEMA_ALIGN][DEBUG] Chunk {chunk_num} aligned to staging "
                                    f"'{current_staging_table}' with {len(debug_cols)} column(s): {debug_cols}"
                                )
                                log(f"[SCHEMA_ALIGN][DEBUG] Chunk {chunk_num} first row after alignment: {debug_first}")
                            except Exception as debug_err:
                                log(f"[SCHEMA_ALIGN][DEBUG] Failed to log aligned chunk preview: {debug_err}")

                        # Upload chunk to GCS in a normalized internal format (CSV comma + quote).
                        # Source parsing still respects file-type options; only staging serialization
                        # is standardized to avoid COPY parsing issues (notably XML _DATA/VARIANT).
                        stage_delimiter = ","
                        stage_quotechar = '"'
                        stage_file_type = "csv"
                        if (file_type or "").lower() in ("tsv", "xml"):
                            log(
                                f"[{(file_type or '').upper()}][DEBUG] Staging chunk {chunk_num} as CSV "
                                f"(delimiter={repr(stage_delimiter)}, quotechar={repr(stage_quotechar)})"
                            )

                        chunk_gcs_path = upload_chunk_to_gcs(
                            gcs, chunk_df, table, chunk_num,
                            gcs_bucket_name,
                            delimiter=stage_delimiter,
                            quotechar=stage_quotechar,
                            file_type=stage_file_type
                        )
                        gcs_full_url = f"gcs://{gcs_bucket_name}/{chunk_gcs_path}"
                        gcs_parquet_urls.append(gcs_full_url)
                        SimpleProgressTracker.accumulate_staging_progress_batch(
                            connection_id=connection_id,
                            migration_session_id=migration_session_id,
                            job_name=job_name,
                            table_name=table,
                            batch_rows=len(chunk_df),
                        )

                        if is_last_chunk:
                            break
                    
                    # After all chunks collected, load to Snowflake
                    if gcs_parquet_urls:
                        user_pk_for_table = user_pk_map.get(table.upper(), []) if load_method == "upsert_custom_key" else []
                        
                        # IMPORTANT: Staging files in GCS are CSVs with a single header row.
                        copy_skiprows = 1
                        copy_delimiter = ","
                        copy_quotechar = '"'
                        if (file_type or "").lower() in ("tsv", "xml"):
                            try:
                                sql_cols_dbg = f"""
                                    SELECT COLUMN_NAME
                                    FROM {Q(sf_database)}.INFORMATION_SCHEMA.COLUMNS
                                    WHERE TABLE_SCHEMA = '{sf_schema.upper()}'
                                      AND TABLE_NAME   = '{current_staging_table.upper()}'
                                    ORDER BY ORDINAL_POSITION
                                """
                                with conn.cursor() as dbg_cur:
                                    dbg_cur.execute(sql_cols_dbg)
                                    staging_cols_dbg = [r[0] for r in dbg_cur.fetchall()]
                                dbg_tag = (file_type or "").upper()
                                log(f"[{dbg_tag}][DEBUG] COPY target staging columns ({len(staging_cols_dbg)}): {staging_cols_dbg}")
                                log(
                                    f"[{dbg_tag}][DEBUG] COPY file format -> TYPE=CSV, "
                                    f"FIELD_DELIMITER={repr(copy_delimiter)}, "
                                    f"FIELD_OPTIONALLY_ENCLOSED_BY={repr(copy_quotechar)}, "
                                    f"SKIP_HEADER={copy_skiprows}"
                                )
                                log(f"[{dbg_tag}][DEBUG] COPY source objects: {gcs_parquet_urls}")
                            except Exception as debug_err:
                                log(f"[{(file_type or '').upper()}][DEBUG] Failed to log COPY debug info: {debug_err}")
                        
                        copy_into_staging_from_gcs_batch(
                            conn, gcs_parquet_urls, current_staging_table,
                            sf_database, sf_schema, sf_storage_integration,
                            gcs_bucket_name,
                            delimiter=copy_delimiter, quotechar=copy_quotechar,
                            skipheaderlines=copy_skiprows, file_type="csv",  # Force CSV for staging
                            connection_id=connection_id,
                            migration_session_id=str(migration_session_id),
                            job_name=job_name,
                            table_name=table
                        )

                        # Multi-file single-table: stage each file now, merge once after all files.
                        if file_mapping_mode == "multi_files_single_table":
                            multi_staged_source_rows += int(total_file_rows or 0)
                            processed += 1
                            processed_tables.add(table)
                            skipped_tables.discard(table)
                            skipped_tables_delta.discard(table)
                            skipped_tables_safety.discard(table)
                            if table not in processed_files_by_table:
                                processed_files_by_table[table] = []
                            processed_files_by_table[table].append(key)
                            log(
                                f"[MIGRATION] Staged file '{key}' into shared staging table "
                                f"'{current_staging_table}'. Deferred MERGE until all files are staged."
                            )
                            continue
                        
                        # Final staging count update already handled progressively in the loop
                        # (Removed the jumpy 'all-at-once' update from here)
                        
                        # Merge staging into final
                        enable_delete_sync = (
                            (not drop_and_reload) and
                            load_method == "upsert_custom_key"
                        )
                        merge_result = merge_staging_into_final(
                            conn,
                            table,
                            current_staging_table,
                            load_method,
                            user_pk_for_table,
                            sf_database,
                            sf_schema,
                            source_file_key=key,
                            delete_missing_rows=enable_delete_sync,
                        )
                        merge_inserted = merge_result.get("inserted", 0)
                        merge_updated = merge_result.get("updated", 0)
                        merge_deleted = merge_result.get("deleted", 0)
                        counted_deletes = merge_deleted
                        
                        # Track success counts based on delta vs initial load
                        if is_delta_refresh:
                            # For delta sync: separate inserts and updates for proper logging
                            # data_transfer_logs uses: new_rec=insert_success, mod_rec=upsert_success
                            if load_method == "upsert_file_row":
                                insert_success = merge_inserted  # New records
                                upsert_success = merge_updated    # Modified records
                            elif load_method == "append_file_modified":
                                insert_success = merge_inserted   # New records
                                upsert_success = 0               # No updates in append mode
                            elif load_method == "upsert_custom_key":
                                insert_success = merge_inserted  # New records
                                upsert_success = merge_updated   # Modified records
                            else:
                                insert_success = merge_inserted  # New records
                                upsert_success = merge_updated   # Modified records
                            delete_success = counted_deletes
                            log(
                                f"[DELTA] Table '{table}': Inserted: {merge_inserted:,}, "
                                f"Updated: {merge_updated:,}, Deleted: {counted_deletes:,} "
                                f"(staged rows: {total_file_rows:,})"
                            )
                        else:
                            # For non-delta runs, still report actual operation counts from MERGE.
                            insert_success = merge_inserted
                            upsert_success = merge_updated
                            delete_success = counted_deletes
                            log(
                                f"[INITIAL] Table '{table}': Inserted: {merge_inserted:,}, "
                                f"Updated: {merge_updated:,}, Deleted: {counted_deletes:,}"
                            )

                        table_ops = table_operation_counts.setdefault(
                            table, {"inserts": 0, "updates": 0, "deletes": 0}
                        )
                        table_ops["inserts"] += insert_success
                        table_ops["updates"] += upsert_success
                        table_ops["deletes"] += delete_success

                        # Push only effective operation counts (not staged rows) for live websocket status.
                        # In multi-file single-table mode this accumulates correctly across files.
                        cumulative_effective_rows = (
                            table_ops["inserts"] + table_ops["updates"] + table_ops["deletes"]
                        )
                        if all([connection_id, migration_session_id, job_name, table]):
                            try:
                                # Keep progress updates aligned with the migration_record table_name.
                                # In multi-file single-table mode, records/status use original_table_name.
                                progress_table_name = (
                                    original_table_name
                                    if (file_mapping_mode == "multi_files_single_table" and original_table_name)
                                    else table
                                )
                                SimpleProgressTracker.set_staging_count(
                                    connection_id=connection_id,
                                    migration_session_id=str(migration_session_id),
                                    job_name=job_name,
                                    table_name=progress_table_name,
                                    count_value=cumulative_effective_rows,
                                )
                                log(
                                    f"[MIGRATION] Updated staging_records_count with effective rows for table '{progress_table_name}': "
                                    f"{cumulative_effective_rows:,} (Inserted+Updated+Deleted)"
                                )
                            except Exception as e:
                                log(
                                    f"[MIGRATION]  Failed to set effective staging_records_count for table '{progress_table_name}': {e}"
                                )
                        total_inserts += insert_success
                        total_updates += upsert_success
                        total_deletes += delete_success
                        
                        file_end_time = datetime.now(timezone.utc)
                        
                        # Log data transfer
                        # For multi-file single table operations, defer data_transfer_logs until end to show total count
                        if file_mapping_mode != "multi_files_single_table":
                            try:
                                # Safely get column count from first_chunk_df if available
                                columns_count = 0
                                if first_chunk_df is not None and hasattr(first_chunk_df, 'columns'):
                                    try:
                                        columns_count = len(first_chunk_df.columns)
                                    except Exception:
                                        columns_count = 0
                                
                                data_transfer_logs(
                                    cmp_id=cmp_id,
                                    connection_id=connection_id,
                                    user_id=user_id,
                                    table_name=table,
                                    start_time=file_start_time,
                                    end_time=file_end_time,
                                    columns_data=columns_count,
                                    session=session,
                                    total_rows=(insert_success + upsert_success + delete_success),
                                    inserts=insert_success,
                                    updates=upsert_success,
                                    deletes=delete_success,
                                    job_name=job_name
                                )
                            except Exception:
                                pass
                        # For multi-file single table, skip data_transfer_logs here - will be called once at the end
                        
                        # Update table status to 'completed'
                        # For multi-file single table operations, defer status update until end to show total count
                        if file_mapping_mode != "multi_files_single_table":
                            try:
                                #  Use original table name for status updates
                                status_table_name = (original_table_name or table)
                                update_table_status_snowflake(
                                    connection_id, status_table_name, "completed", job_name, migration_session_id, file_end_time
                                )
                                if table in tables_in_progress:
                                    tables_in_progress.remove(table)  #  Remove from tracking
                                processed_tables.add(table)  #  Track as processed
                                
                                #  Remove from all skip sets (it was processed)
                                skipped_tables.discard(table)
                                skipped_tables_delta.discard(table)
                                skipped_tables_safety.discard(table)
                            except Exception:
                                pass
                        else:
                            # For multi-file single table, just track as processed without status update
                            processed_tables.add(table)  #  Track as processed
                            skipped_tables.discard(table)
                            skipped_tables_delta.discard(table)
                            skipped_tables_safety.discard(table)
                        
                        processed += 1
                        # Update total_rows based on delta vs initial load
                        if is_delta_refresh:
                            # For delta: count actual changes (inserts + updates + deletes)
                            delta_rows = merge_inserted + merge_updated + counted_deletes
                            total_rows += delta_rows
                            log(
                                f"[DELTA] Added {delta_rows:,} delta rows to total "
                                f"(Inserted: {merge_inserted:,}, Updated: {merge_updated:,}, Deleted: {counted_deletes:,})"
                            )
                        else:
                            # For non-delta runs: still count actual operations from MERGE.
                            operation_rows = merge_inserted + merge_updated + counted_deletes
                            total_rows += operation_rows
                            log(
                                f"[INITIAL] Added {operation_rows:,} operation rows to total "
                                f"(Inserted: {merge_inserted:,}, Updated: {merge_updated:,}, Deleted: {counted_deletes:,})"
                            )
                        
                        # Track successfully processed files for mapping update (multi-file single table mode)
                        if file_mapping_mode == "multi_files_single_table":
                            if table not in processed_files_by_table:
                                processed_files_by_table[table] = []
                            processed_files_by_table[table].append(key)
                            log(f"[MAPPING] Tracked processed file '{key}' for table '{table}'")
                    else:
                        # For multi-file single table operations, defer status update until end to show total count
                        if file_mapping_mode != "multi_files_single_table":
                            try:
                                #  Use original table name for status updates
                                status_table_name = (original_table_name or table)
                                update_table_status_snowflake(
                                    connection_id, status_table_name, "completed", job_name, migration_session_id
                                )
                                if table in tables_in_progress:
                                    tables_in_progress.remove(table)  #  Remove from tracking
                                processed_tables.add(table)  #  Track as processed
                                
                                #  Remove from all skip sets (it was processed)
                                skipped_tables.discard(table)
                                skipped_tables_delta.discard(table)
                                skipped_tables_safety.discard(table)
                            except Exception:
                                pass
                        else:
                            # For multi-file single table, just track as processed without status update
                            processed_tables.add(table)  #  Track as processed
                            skipped_tables.discard(table)
                            skipped_tables_delta.discard(table)
                            skipped_tables_safety.discard(table)
                
                except Exception as file_error:
                    error_message = str(file_error)
                    error_details = f"Error: {error_message}\n\nTraceback:\n{traceback.format_exc()}"
                    log(f" ERROR: Failed to process file '{key}': {error_message}")
                    
                    error_context = {
                        "table_name": table,
                        "service_name": "S3 to Snowflake",
                        "error_type": "TABLE_MIGRATION_FAILED",
                        "cmp_id": cmp_id,
                        "connection_id": connection_id,
                        "user_id": user_id,
                        "job_name": job_name,
                        "migration_session_id": migration_session_id,
                        "session": session,
                        "source_file": key,
                        "log_id": log_id
                    }
                    
                    if failure_tracker:
                        failure_tracker.add_failure(table, error_details, "S3 to Snowflake")

                    try:
                        handle_service_error(
                            error=file_error,
                            context=error_context,
                            log_files=None,
                            send_email=False,
                            is_final_attempt=is_final_attempt,
                        )
                    except Exception as log_error:
                        log(f"[MIGRATION] Failed to log error for '{table}': {log_error}")
                    
                    try:
                        #  Use original table name for status updates
                        status_table_name = (original_table_name or table)
                        update_table_status_snowflake(
                            connection_id, status_table_name, "failed", job_name, migration_session_id
                        )
                        if table in tables_in_progress:
                            tables_in_progress.remove(table)  #  Remove from tracking
                        processed_tables.add(table)  #  Track as processed (failed)
                        skipped_tables.discard(table)  #  Remove from skipped (was processed)
                    except Exception:
                        pass

                    # Failure-path cleanup: drop per-file staging immediately for single-file mode.
                    # Multi-file single-table uses one shared staging table and merges at end.
                    try:
                        tracked_staging_name = staging_tables.get(table) if ('staging_tables' in locals()) else None
                        if (
                            file_mapping_mode != "multi_files_single_table"
                            and tracked_staging_name and conn and sf_database and sf_schema
                        ):
                            with conn.cursor() as cleanup_cur:
                                cleanup_cur.execute(
                                    f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(tracked_staging_name)}"
                                )
                            log(
                                f"[MIGRATION]  Failure cleanup - dropped staging table for "
                                f"'{table}': {sf_database}.{sf_schema}.{tracked_staging_name}"
                            )
                            staging_tables.pop(table, None)
                    except Exception as cleanup_err:
                        log(
                            f"[MIGRATION]  Failure cleanup could not drop staging for "
                            f"'{table}': {cleanup_err}"
                        )
                    
                    continue
            # Multi-file single table: perform one final MERGE after all files are staged.
            skip_multi_merge_due_to_failure = False
            if file_mapping_mode == "multi_files_single_table" and original_table_name and failure_tracker:
                normalized_table_name = str(original_table_name).strip().upper()
                failed_entries_for_table = [
                    item
                    for item in (failure_tracker.failed_tables or [])
                    if isinstance(item, dict)
                    and str(item.get("table_name", "")).strip().upper() == normalized_table_name
                ]
                has_non_skip_failure_for_multi = any(
                    not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                    for item in failed_entries_for_table
                )
                if has_non_skip_failure_for_multi:
                    skip_multi_merge_due_to_failure = True
                    log(
                        f"[MIGRATION]  Skipping final merge for '{original_table_name}' "
                        f"due to non-skip failure(s) during file processing."
                    )
            if (
                file_mapping_mode == "multi_files_single_table"
                and single_table
                and multi_staging_table_name
                and processed_files_by_table.get(single_table)
                and not skip_multi_merge_due_to_failure
            ):
                try:
                    user_pk_for_table = (
                        user_pk_map.get(single_table.upper(), [])
                        if load_method == "upsert_custom_key"
                        else []
                    )
                    enable_delete_sync = (
                        (not drop_and_reload)
                        and load_method == "upsert_custom_key"
                    )
                    merge_result = merge_staging_into_final(
                        conn,
                        single_table,
                        multi_staging_table_name,
                        load_method,
                        user_pk_for_table,
                        sf_database,
                        sf_schema,
                        source_file_key=None,
                        source_file_keys=processed_files_by_table.get(single_table, []),
                        delete_missing_rows=enable_delete_sync,
                    )
                    merge_inserted = merge_result.get("inserted", 0)
                    merge_updated = merge_result.get("updated", 0)
                    merge_deleted = merge_result.get("deleted", 0)
                    counted_deletes = merge_deleted

                    table_ops = table_operation_counts.setdefault(
                        single_table, {"inserts": 0, "updates": 0, "deletes": 0}
                    )
                    table_ops["inserts"] += merge_inserted
                    table_ops["updates"] += merge_updated
                    table_ops["deletes"] += counted_deletes
                    total_inserts += merge_inserted
                    total_updates += merge_updated
                    total_deletes += counted_deletes

                    # Keep websocket progress/staging_records_count aligned with actual DML impact
                    # for multi-file single-table mode (single final merge).
                    cumulative_effective_rows = (
                        table_ops["inserts"] + table_ops["updates"] + table_ops["deletes"]
                    )
                    if all([connection_id, migration_session_id, job_name, original_table_name]):
                        try:
                            SimpleProgressTracker.set_staging_count(
                                connection_id=connection_id,
                                migration_session_id=str(migration_session_id),
                                job_name=job_name,
                                table_name=original_table_name,
                                count_value=cumulative_effective_rows,
                            )
                            log(
                                f"[MIGRATION] Updated staging_records_count with effective rows for table "
                                f"'{original_table_name}': {cumulative_effective_rows:,} "
                                f"(Inserted+Updated+Deleted)"
                            )
                        except Exception as e:
                            log(
                                f"[MIGRATION]  Failed to set effective staging_records_count for table "
                                f"'{original_table_name}': {e}"
                            )

                    operation_rows = merge_inserted + merge_updated + counted_deletes
                    total_rows += operation_rows
                    log(
                        f"[MIGRATION] Final multi-file merge completed for table '{single_table}': "
                        f"Inserted={merge_inserted:,}, Updated={merge_updated:,}, Deleted={counted_deletes:,}, "
                        f"Staged source rows={multi_staged_source_rows:,}"
                    )
                    try:
                        staging_tables.pop(single_table, None)
                    except Exception:
                        pass
                except Exception as multi_merge_error:
                    log(f"[MIGRATION]  Final multi-file merge failed for table '{single_table}': {multi_merge_error}")
                    try:
                        with conn.cursor() as cleanup_cur:
                            cleanup_cur.execute(
                                f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(multi_staging_table_name)}"
                            )
                        log(
                            f"[MIGRATION]  Dropped staging table '{multi_staging_table_name}' "
                            "after merge abort"
                        )
                    except Exception as staging_drop_error:
                        log(
                            f"[MIGRATION]  Failed to drop staging table "
                            f"'{multi_staging_table_name}' after merge abort: {staging_drop_error}"
                        )
                    raise

            # Multi-file single table: fail only when all mapped files are missing.
            if file_mapping_mode == "multi_files_single_table" and original_table_name:
                missing_for_table = (
                    missing_files_by_table.get(single_table, [])
                    or missing_files_by_table.get(original_table_name, [])
                )
                all_files_missing = (
                    (bool(keys) and available_files_seen == 0 and bool(missing_for_table))
                    or multi_no_files_discovered
                )
                if all_files_missing:
                    missing_message = "All mapped files are missing in S3 for this multi-file table."
                    if failure_tracker:
                        failure_tracker.add_failure(
                            original_table_name,
                            missing_message,
                            "S3 to Snowflake",
                        )
                    try:
                        error_context = {
                            "table_name": original_table_name,
                            "service_name": "S3 to Snowflake",
                            "error_type": "TABLE_MIGRATION_FAILED",
                            "cmp_id": cmp_id,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "job_name": job_name,
                            "migration_session_id": migration_session_id,
                            "session": session,
                            "source_file": None,
                            "log_id": log_id
                        }
                        handle_service_error(
                            error=FileNotFoundError(missing_message),
                            context=error_context,
                            log_files=None,
                            send_email=False,
                            is_final_attempt=is_final_attempt
                        )
                    except Exception as log_error:
                        log(f"[MIGRATION]  Failed to write table-level error for '{original_table_name}': {log_error}")
            
            # Send consolidated error email only for true failures.
            # In multi-file single-table mode, skip-only schema mismatch warnings should not trigger
            # "Migration Summary failure" emails when at least one file was loaded.
            if failure_tracker.has_failures():
                should_send_consolidated_failure_email = True
                if file_mapping_mode == "multi_files_single_table" and original_table_name:
                    normalized_table_name = str(original_table_name).strip().upper()
                    failed_entries_for_table = [
                        item
                        for item in (failure_tracker.failed_tables or [])
                        if isinstance(item, dict)
                        and str(item.get("table_name", "")).strip().upper() == normalized_table_name
                    ]
                    processed_for_multi = len(processed_files_by_table.get(single_table, []))
                    has_non_skip_failure_for_multi = any(
                        not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                        for item in failed_entries_for_table
                    )
                    only_skip_warnings = (
                        len(failed_entries_for_table) > 0
                        and not has_non_skip_failure_for_multi
                        and processed_for_multi > 0
                    )
                    if only_skip_warnings:
                        should_send_consolidated_failure_email = False
                        log(
                            "[MIGRATION]  Skip-only file validation warnings detected in multi-file mode; "
                            "suppressing consolidated failure email."
                        )

                if should_send_consolidated_failure_email:
                    failure_tracker.send_consolidated_error_email(
                        connection_name=connection_name,
                        service_name="S3 to Snowflake",
                        user_id=user_id,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        company_name=company_name,
                        is_final_attempt=is_final_attempt
                    )
            
            # Final summary
            migration_end_time = datetime.now(timezone.utc)
            migration_duration = (migration_end_time - migration_start_time).total_seconds()
            
            log(f"\n" + "=" * 80)
            log(f"[MIGRATION]  DONE. Files processed: {processed}/{len(keys)}")
            if is_delta_refresh:
                log(f"[MIGRATION] Mode: DELTA SYNC (counting inserts/updates/deletes)")
            else:
                log(f"[MIGRATION] Mode: INITIAL/REFRESH (counting merge operations)")
            log(f"[MIGRATION] Total operation rows processed: {total_rows:,}")
            log(f"[MIGRATION] Duration: {migration_duration:.2f} seconds")
            if failure_tracker.has_failures():
                log(f"[MIGRATION]  {len(failure_tracker.failed_tables)} table(s) failed")
            log("=" * 80)
            
            # Multi-file mode does not persist table-to-file map; no post-run mapping maintenance needed.
            
            
            #  FIX: Update skipped tables to "completed" and log them with 0 rows
            # This ensures status is updated and logs are shown in delta refresh
            # We differentiate between "Delta Skips" (already synced) and "Safety Skips" (too recent)
            if skipped_tables_delta or skipped_tables_safety:
                migration_end_time_utc = datetime.now(timezone.utc)
                pending_delta_skips = [t for t in skipped_tables_delta if t not in processed_tables]
                pending_safety_skips = [t for t in skipped_tables_safety if t not in processed_tables]
                total_skipped = len(pending_delta_skips) + len(pending_safety_skips)
                log(f"[MIGRATION]  Updating {total_skipped} skipped table(s) to 'completed' and logging...")
                
                # 1. Handle Delta Skips (Advance last_synced timestamp)
                for table_name in pending_delta_skips:
                    # Don't update if it's also in safety skips (safety takes precedence to avoid advancing timestamp)
                    if table_name in skipped_tables_safety:
                        continue
                        
                    try:
                        # Update status to "completed" AND advance last_synced
                        update_table_status_snowflake(
                            connection_id, table_name, "completed", job_name, migration_session_id, migration_end_time_utc
                        )
                        
                        # Log if job_name exists
                        if job_name:
                            data_transfer_logs(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                user_id=user_id,
                                table_name=table_name,
                                start_time=migration_start_time,
                                end_time=migration_end_time_utc,
                                columns_data=0,
                                session=session,
                                insert_success=0,
                                insert_failure=0,
                                upsert_success=0,
                                upsert_failure=0,
                                job_name=job_name
                            )
                        log(f"[MIGRATION]  Updated and logged delta-skipped table '{table_name}'")
                    except Exception as log_error:
                        log(f"[MIGRATION]  Failed to update/log delta-skipped table '{table_name}': {log_error}")
                
                # 2. Handle Safety Skips (Status ONLY, DO NOT advance last_synced)
                for table_name in pending_safety_skips:
                    try:
                        # Update status only (keep old last_synced so it is picked up next time)
                        update_table_status_snowflake(
                            connection_id, table_name, "completed", job_name, migration_session_id, sync_end_time=None
                        )
                        
                        # Log if job_name exists
                        if job_name:
                            data_transfer_logs(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                user_id=user_id,
                                table_name=table_name,
                                start_time=migration_start_time,
                                end_time=migration_end_time_utc,
                                columns_data=0,
                                session=session,
                                insert_success=0,
                                insert_failure=0,
                                upsert_success=0,
                                upsert_failure=0,
                                job_name=job_name
                            )
                        log(f"[MIGRATION]  Updated status for safety-skipped table '{table_name}' (kept old timestamp)")
                    except Exception as log_error:
                        log(f"[MIGRATION]  Failed to update status for safety-skipped table '{table_name}': {log_error}")
                
                log(f"[MIGRATION]  Completed skip processing")
            
            # For multi-file single table operations, do final status update with total count
            if file_mapping_mode == "multi_files_single_table" and original_table_name:
                # Only fail the unified multi-file table for true table-level failures.
                # Per-file schema mismatch skips are allowed when at least one file loads.
                normalized_table_name = str(original_table_name).strip().upper()
                failed_entries_for_table = [
                    item
                    for item in (failure_tracker.failed_tables or [])
                    if isinstance(item, dict)
                    and str(item.get("table_name", "")).strip().upper() == normalized_table_name
                ]
                processed_for_multi = len(processed_files_by_table.get(single_table, []))
                has_non_skip_failure_for_multi = any(
                    not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                    for item in failed_entries_for_table
                )
                multi_failed = has_non_skip_failure_for_multi or (
                    len(failed_entries_for_table) > 0 and processed_for_multi == 0
                )
                if multi_failed:
                    log(
                        f"[MIGRATION]  Final status for multi-file table '{original_table_name}' set to failed "
                        f"(table-specific failure detected)"
                    )
                else:
                    log(
                        f"[MIGRATION]  Final status for multi-file table '{original_table_name}' remains completed "
                        f"(file-level skips are tolerated because compatible files loaded)"
                    )
                table_counts = (
                    table_operation_counts.get(single_table)
                    or table_operation_counts.get(original_table_name)
                    or {"inserts": 0, "updates": 0, "deletes": 0}
                )
                # Update status once with total row count and final pass/fail state
                # Use migration_end_time (defined at end of file processing loop) for consistent timing
                final_table_status = "failed" if multi_failed else "completed"
                # Never advance last_synced on failure.
                final_sync_end_time = migration_end_time if final_table_status == "completed" else None
                tables_in_progress.add(original_table_name)
                update_table_status_snowflake(
                    connection_id,
                    original_table_name,
                    final_table_status,
                    job_name,
                    migration_session_id,
                    final_sync_end_time,
                )
                tables_in_progress.discard(original_table_name)
                try:
                    # Log the final data transfer with total rows
                    if job_name:
                        table_total_rows = (
                            table_counts.get("inserts", 0)
                            + table_counts.get("updates", 0)
                            + table_counts.get("deletes", 0)
                        )
                        # Safely get column count from first_chunk_df if available
                        columns_count = 0
                        if first_chunk_df is not None and hasattr(first_chunk_df, 'columns'):
                            try:
                                columns_count = len(first_chunk_df.columns)
                            except Exception:
                                columns_count = 0
                        
                        data_transfer_logs(
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            user_id=user_id,
                            table_name=original_table_name,
                            start_time=migration_start_time,
                            end_time=migration_end_time,
                            columns_data=columns_count,
                            session=session,
                            total_rows=table_total_rows,
                            inserts=table_counts.get("inserts", 0),
                            updates=table_counts.get("updates", 0),
                            deletes=table_counts.get("deletes", 0),
                            job_name=job_name
                        )
                    log(
                        f"[MIGRATION]  Final status update for multi-file single table: {original_table_name} "
                        f"with operations New:{table_counts.get('inserts', 0):,}, "
                        f"Modified:{table_counts.get('updates', 0):,}, Deleted:{table_counts.get('deletes', 0):,}"
                    )
                except Exception as transfer_log_error:
                    log(f"[MIGRATION]  Failed to log final data transfer for multi-file single table: {transfer_log_error}")

            # ================================================================= =======
            if file_mapping_mode != "multi_files_single_table":
                # Retry any failed status write before reporting the migration result.
                for failure in failure_tracker.failed_tables:
                    update_table_status_snowflake(
                        connection_id, failure["table_name"], "failed", job_name, migration_session_id,
                    )

            # Log migration completion (matches Dynamics/Salesforce pattern)
            # Moved to the end to ensure all status updates are finished first
            # ================================================================= =======
            try:
                removed_mapping_count = len(failed_removed_tables) if file_mapping_mode == "single_table_per_file" else 0
                effective_total_tables = len(keys) + removed_mapping_count
                failure_reason_for_log = None
                log_status_override = None
                warning_detail_for_ui = None

                if failure_tracker and failure_tracker.has_failures():
                    for item in (failure_tracker.failed_tables or []):
                        if not isinstance(item, dict):
                            continue
                        candidate_reason = str(item.get("error_message") or "").strip()
                        if candidate_reason:
                            failure_reason_for_log = candidate_reason
                            break

                # Determine operation type for conditional logic
                operation_type = None
                # For multi_files_single_table mode, treat as single table regardless of number of files
                is_single_table_operation = (
                    (file_mapping_mode == "multi_files_single_table") or
                    (drop_and_reload and effective_total_tables == 1) or
                    (effective_total_tables == 1 and not drop_and_reload)
                )
                
                if is_single_table_operation and file_mapping_mode == "multi_files_single_table":
                    operation_type = 'multi_files_single_table'
                elif is_single_table_operation and drop_and_reload:
                    operation_type = 'drop_and_reload'
                elif is_single_table_operation:
                    operation_type = 'single_table_refresh'
                
                # For single table operations, use standard message format
                if operation_type in ('drop_and_reload', 'single_table_refresh', 'multi_files_single_table'):
                    if file_mapping_mode == "multi_files_single_table":
                        # For multi-file single-table mode, keep table summary at table level (1/1),
                        # but use explicit status override for partial (skip-only) outcomes.
                        total_tables = 1
                        failed_count = 0
                        failed_entries_for_table = [
                            item
                            for item in (failure_tracker.failed_tables or [])
                            if isinstance(item, dict)
                            and str(item.get("table_name", "")).strip().upper() == str(original_table_name).strip().upper()
                        ]
                        has_non_skip_failure_for_multi = any(
                            not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                            for item in failed_entries_for_table
                        )
                        skipped_file_keys = sorted(
                            set(
                                incompatible_files_by_table.get(single_table, [])
                                + missing_files_by_table.get(single_table, [])
                                + missing_files_by_table.get(original_table_name, [])
                            )
                        )
                        skipped_count = len(skipped_file_keys)
                        processed_for_multi = len(processed_files_by_table.get(single_table, []))

                        if has_non_skip_failure_for_multi:
                            failed_count = 1
                            successful_count = 0
                            log_status_override = "F"
                            if not failure_reason_for_log:
                                failure_reason_for_log = (
                                    "Multi-file table failed due to non-skip error(s) during processing."
                                )
                        elif skipped_count > 0 and processed_for_multi == 0:
                            # All files were skipped (schema/type mismatch or missing), so the table did not load at all.
                            # Treat this as a failure and surface a concrete error message.
                            failed_count = 1
                            successful_count = 0
                            log_status_override = "F"
                            latest_skip_detail = next(
                                (
                                    str(item.get("error_message", "")).strip()
                                    for item in reversed(failed_entries_for_table)
                                    if str(item.get("error_message", "")).strip().startswith("Skipping file '")
                                ),
                                "",
                            )
                            sample = ", ".join(skipped_file_keys[:2])
                            more = f" (+{skipped_count - 2} more)" if skipped_count > 2 else ""
                            failure_reason_for_log = (
                                latest_skip_detail
                                or f"All files skipped for table due to schema/type mismatch or missing files: {sample}{more}"
                            )
                            warning_detail_for_ui = failure_reason_for_log
                        elif skipped_count > 0 and processed_for_multi > 0:
                            failed_count = 0
                            successful_count = 1
                            log_status_override = "P"
                            sample = ", ".join(skipped_file_keys[:2])
                            more = f" (+{skipped_count - 2} more)" if skipped_count > 2 else ""
                            latest_skip_detail = next(
                                (
                                    str(item.get("error_message", "")).strip()
                                    for item in reversed(failed_entries_for_table)
                                    if str(item.get("error_message", "")).strip().startswith("Skipping file '")
                                ),
                                "",
                            )
                            failure_reason_for_log = (
                                latest_skip_detail
                                or f"{skipped_count} file(s) skipped due schema/type mismatch: {sample}{more}"
                            )
                            warning_detail_for_ui = failure_reason_for_log
                            if skipped_count > 1 and skipped_file_keys:
                                warning_detail_for_ui = (
                                    f"{warning_detail_for_ui} | Skipped files: {', '.join(skipped_file_keys[:5])}"
                                )
                        else:
                            failed_count = 0
                            successful_count = 1
                            log_status_override = "S"
                    else:
                        total_tables = effective_total_tables
                        failed_count = len(failure_tracker.failed_tables) if failure_tracker and failure_tracker.has_failures() else 0
                        successful_count = max(total_tables - failed_count, 0)
                    
                    include_operation_counts = load_method not in ("upsert_file_row", "append_file_modified")
                    deletes_for_log = total_deletes
                    log_migration_completion(
                        execution_time=migration_duration,
                        total_rows=total_rows,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        session=session,
                        total_tables=total_tables,
                        successful_tables=successful_count,
                        failed_tables=failed_count,
                        job_name=job_name,
                        inserts=total_inserts,
                        updates=total_updates,
                        deletes=deletes_for_log,
                        include_operation_counts=include_operation_counts,
                        log_id=log_id,
                        failure_reason=failure_reason_for_log,
                        status_override=log_status_override,
                        suppress_reason_in_completion=True,
                        celery_child_mode=request.celery_child_mode,
                    )
                    if failed_count == 0:
                        duration_seconds = (
                            migration_duration.total_seconds()
                            if hasattr(migration_duration, "total_seconds")
                            else float(migration_duration)
                        )
                        if include_operation_counts:
                            log(
                                f"[MIGRATION][DEBUG] Migration completed successfully in "
                                f"{duration_seconds:.2f} seconds, Total rows: {total_rows:,}, "
                                f"Inserts: {total_inserts:,}, Updates: {total_updates:,}, "
                                f"Deletes: {total_deletes:,}, Tables: {successful_count}/{total_tables} successful."
                            )
                        else:
                            log(
                                f"[MIGRATION][DEBUG] Migration completed successfully in "
                                f"{duration_seconds:.2f} seconds, Total rows: {total_rows:,}, "
                                f"Tables: {successful_count}/{total_tables} successful."
                            )
                    if (
                        file_mapping_mode == "multi_files_single_table"
                        and log_status_override == "P"
                        and warning_detail_for_ui
                        and all([cmp_id, connection_id, user_id, job_name, migration_session_id])
                    ):
                        # Write mismatch warning as TABLE-level log so it won't pollute
                        # connection activity (which shows only job-level logs).
                        try:
                            warning_table = original_table_name or single_table or "Migration"
                            warn_conn = postgres_connection()
                            warn_cursor = warn_conn.cursor()
                            warning_migration_record_id = get_migration_record_id(
                                warn_cursor,
                                job_name=job_name,
                                table_name=warning_table,
                                connection_id=connection_id,
                            )
                            warn_cursor.close()
                            warn_conn.close()
                            if warning_migration_record_id:
                                insert_tool_log(
                                    cmp_id=cmp_id,
                                    connection_id=connection_id,
                                    usr_id=user_id,
                                    table_name=warning_table,
                                    status="P",
                                    message=warning_detail_for_ui,
                                    session=migration_session_id,
                                    job_name=job_name,
                                    migration_record_id=warning_migration_record_id,
                                )
                        except Exception as warn_log_err:
                            log(f"[MIGRATION]  Failed to write S3 partial warning log: {warn_log_err}")
                    if (
                        file_mapping_mode == "multi_files_single_table"
                        and log_status_override == "F"
                        and warning_detail_for_ui
                        and all([cmp_id, connection_id, user_id, job_name, migration_session_id])
                    ):
                        # Write schema mismatch as TABLE-level failure log so UI shows exact error_message.
                        try:
                            from utils.error_utils import update_table_migration_status_log
                            warning_table = original_table_name or single_table or "Migration"
                            warn_conn = postgres_connection()
                            warn_cursor = warn_conn.cursor()
                            warning_migration_record_id = get_migration_record_id(
                                warn_cursor,
                                job_name=job_name,
                                table_name=warning_table,
                                connection_id=connection_id,
                            )
                            warn_cursor.close()
                            warn_conn.close()
                            if warning_migration_record_id:
                                updated = update_table_migration_status_log(
                                    migration_record_id=warning_migration_record_id,
                                    message=warning_detail_for_ui,
                                    status="F",
                                    session=migration_session_id,
                                    job_name=job_name,
                                    connection_id=connection_id,
                                )
                                if not updated:
                                    insert_tool_log(
                                        cmp_id=cmp_id,
                                        connection_id=connection_id,
                                        usr_id=user_id,
                                        table_name=warning_table,
                                        status="F",
                                        message=warning_detail_for_ui,
                                        session=migration_session_id,
                                        job_name=job_name,
                                        migration_record_id=warning_migration_record_id,
                                    )
                        except Exception as warn_log_err:
                            log(f"[MIGRATION]  Failed to write S3 failure log: {warn_log_err}")
                else:
                    failed_count = len(failure_tracker.failed_tables) if failure_tracker.has_failures() else 0
                    successful_count = max(effective_total_tables - failed_count, 0)
                    include_operation_counts = load_method not in ("upsert_file_row", "append_file_modified")
                    deletes_for_log = total_deletes
                    log_migration_completion(
                        execution_time=migration_duration,
                        total_rows=total_rows,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        session=session,
                        total_tables=effective_total_tables,
                        successful_tables=successful_count,
                        failed_tables=failed_count,
                        job_name=job_name,
                        inserts=total_inserts,
                        updates=total_updates,
                        deletes=deletes_for_log,
                        include_operation_counts=include_operation_counts,
                        log_id=log_id,
                        failure_reason=failure_reason_for_log,
                        status_override=log_status_override,
                        suppress_reason_in_completion=True,
                        celery_child_mode=request.celery_child_mode,
                    )
                    if failed_count == 0:
                        duration_seconds = (
                            migration_duration.total_seconds()
                            if hasattr(migration_duration, "total_seconds")
                            else float(migration_duration)
                        )
                        if include_operation_counts:
                            log(
                                f"[MIGRATION][DEBUG] Migration completed successfully in "
                                f"{duration_seconds:.2f} seconds, Total rows: {total_rows:,}, "
                                f"Inserts: {total_inserts:,}, Updates: {total_updates:,}, "
                                f"Deletes: {total_deletes:,}, Tables: {successful_count}/{effective_total_tables} successful."
                            )
                        else:
                            log(
                                f"[MIGRATION][DEBUG] Migration completed successfully in "
                                f"{duration_seconds:.2f} seconds, Total rows: {total_rows:,}, "
                                f"Tables: {successful_count}/{effective_total_tables} successful."
                            )
            except Exception as log_error:
                log(f"[MIGRATION]  Failed to log migration completion: {log_error}")
            
            # Clean up Celery jobs and push completion message
            try:
                cleanup_result = handle_migration_completion(
                    connection_name=connection_name,
                    job_name=job_name,
                    completion_message=f"S3 migration completed: {processed}/{len(keys)} files, {total_rows:,} rows"
                )
            except Exception:
                pass
            
            failed_count = len(failure_tracker.failed_tables) if failure_tracker.has_failures() else 0
            if file_mapping_mode == "multi_files_single_table":
                normalized_table_name = str(original_table_name or "").strip().upper()
                failed_entries_for_table = [
                    item
                    for item in (failure_tracker.failed_tables or [])
                    if isinstance(item, dict)
                    and str(item.get("table_name", "")).strip().upper() == normalized_table_name
                ]
                processed_for_multi = len(processed_files_by_table.get(single_table, []))
                has_non_skip_failure_for_multi = any(
                    not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                    for item in failed_entries_for_table
                )
                multi_failed = has_non_skip_failure_for_multi or (
                    len(failed_entries_for_table) > 0 and processed_for_multi == 0
                )
                final_status = "failed" if multi_failed else "success"
            elif failed_count > 0 and processed == 0:
                final_status = "failed"
            else:
                final_status = "success"

            # In async migration-yield flow, Django receives only task_id and cannot infer
            # missing-file cleanup triggers. Run schema refresh once at job completion.
            # Celery fan-out runs migrate() per child — defer refresh to the finalizer.
            if (
                file_mapping_mode == "single_table_per_file"
                and missing_files_single_mode
                and not getattr(request, "celery_child_mode", False)
            ):
                try:
                    log(
                        f"[MIGRATION]  Post-migration schema refresh triggered due to missing file(s): "
                        f"{sorted(missing_files_single_mode)}"
                    )
                    refresh_job_name = (
                        f"{job_name}_post_missing_file_schema_refresh" if job_name
                        else f"S3_post_missing_file_schema_refresh_{migration_session_id}"
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
                        log(f"[MIGRATION]  Could not create post-refresh start log (non-fatal): {start_log_error}")
                    listing_request = AmazonS3ListingTables(
                        connection_id=connection_id,
                        AmazonS3=source_config,
                        job_name=refresh_job_name,
                        cmp_id=cmp_id,
                        user_id=user_id,
                        log_id=log_id,
                    )
                    refreshed_tables_result = await AmazonS3Service.list_tables_with_schema(listing_request)
                    AmazonS3Service.refresh_and_update_s3_tables_in_database(
                        connection_id=connection_id,
                        tables_result=refreshed_tables_result,
                        job_name=refresh_job_name,
                        migration_session_id=migration_session_id,
                    )
                    try:
                        from utils.websocket_utils import trigger_connection_ws_status_update
                        trigger_connection_ws_status_update(connection_id)
                    except Exception as ws_err:
                        log(f"[MIGRATION]  Post-migration schema refresh WS trigger failed: {ws_err}")
                    log("[MIGRATION]  Post-migration schema refresh completed after missing-file detection")
                except Exception as post_refresh_error:
                    log(f"[MIGRATION]  Post-migration schema refresh failed (non-fatal): {post_refresh_error}")

            failed_tables_list = list(dict.fromkeys(
                str(item.get("table_name"))
                for item in (failure_tracker.failed_tables or [])
                if item.get("table_name")
            ))
            if file_mapping_mode == "multi_files_single_table":
                failed_tables_list = [original_table_name] if multi_failed else []
            failed_table_names_cf = {name.casefold() for name in failed_tables_list}
            completed_candidates = list(processed_tables) + list(skipped_tables_delta) + list(skipped_tables_safety)
            if file_mapping_mode == "multi_files_single_table" and not multi_failed:
                completed_candidates = [original_table_name]
            successful_tables_list = list(dict.fromkeys(
                tbl for tbl in completed_candidates if str(tbl).casefold() not in failed_table_names_cf
            ))
            failed_count = len(failed_tables_list)

            return {
                "status": final_status,
                "files_processed": processed,
                "total_files": len(keys),
                "total_rows": total_rows,
                "total_inserts": total_inserts,
                "total_updates": total_updates,
                "total_deleted_recids": total_deletes,
                "execution_time": (
                    migration_duration.total_seconds()
                    if hasattr(migration_duration, "total_seconds")
                    else float(migration_duration)
                ),
                "successful_tables_count": len(successful_tables_list),
                "failed_tables_count": failed_count,
                "successful_tables_list": successful_tables_list,
                "failed_tables_list": failed_tables_list,
                "connection_id": connection_id,
                "migration_session_id": migration_session_id,
                "failed_tables": failed_count,
                "processed_tables": list(processed_tables),  #  Return processed tables
                "skipped_tables_delta": list(skipped_tables_delta),
                "skipped_tables_safety": list(skipped_tables_safety),
                "duration_seconds": migration_duration,
                "single_mode_missing_files_detected": bool(
                    file_mapping_mode == "single_table_per_file" and missing_files_single_mode
                ),
                "single_mode_missing_files": sorted(missing_files_single_mode),
            }
        
        except Exception as e:
            error_msg = f"Migration failed: {str(e)}"
            log(f"[MIGRATION]  {error_msg}")
            traceback.print_exc()
            
            #  FIX: Update all tables that were in progress to "failed"
            if 'tables_in_progress' in locals():
                for table_name in tables_in_progress:
                    try:
                        update_table_status_snowflake(
                            connection_id, table_name, "failed", job_name, migration_session_id
                        )
                        log(f"[MIGRATION]  Updated table '{table_name}' status to 'failed'")
                    except Exception as status_error:
                        log(f"[MIGRATION]  Failed to update status for table '{table_name}': {status_error}")
                        raise RuntimeError(
                            f"{error_msg}; could not persist failed status for '{table_name}': {status_error}"
                        ) from status_error

            # Only mark all selected tables as failed for true terminal/common-job failures
            # before table-level processing starts. If table processing already began,
            # keep failure scope limited to tables explicitly tracked in-progress.
            should_mark_all_selected = True
            if 'processed_tables' in locals() and processed_tables:
                should_mark_all_selected = False
            elif 'tables_in_progress' in locals() and tables_in_progress:
                should_mark_all_selected = False

            if should_mark_all_selected:
                _mark_selected_tables_failed_on_terminal_error()

            #  Cleanup staging tables on terminal error using exact tracked names only
            try:
                if 'conn' in locals() and conn and 'staging_tables' in locals() and staging_tables:
                    log(f"[MIGRATION]  Attempting error cleanup of {len(staging_tables)} tracked staging table(s)...")
                    for table_name, tracked_staging_name in list(staging_tables.items()):
                        try:
                            full_staging_name = f"{sf_database}.{sf_schema}.{tracked_staging_name}"
                            log(f"[MIGRATION]  Error cleanup - dropping: {full_staging_name}")
                            with conn.cursor() as cur:
                                cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(tracked_staging_name)}")
                            log(f"[MIGRATION]  Error cleanup - dropped: {full_staging_name}")
                        except Exception as cleanup_err:
                            log(f"[MIGRATION]  Error cleanup failed for {tracked_staging_name}: {cleanup_err}")
                else:
                    log("[MIGRATION]  No tracked staging tables available for terminal error cleanup")
            except Exception as cleanup_error:
                log(f"[MIGRATION]  Error during staging table cleanup: {cleanup_error}")
            
            total_tables_for_error = len(keys) if 'keys' in locals() else 0
            if file_mapping_mode == "multi_files_single_table":
                total_tables_for_error = 1
            primary_error_table = next(
                (
                    str(item.get("table_name") or "").strip()
                    for item in (failure_tracker.failed_tables or [])
                    if isinstance(item, dict) and str(item.get("table_name") or "").strip()
                ),
                (original_table_name or single_table or "Migration"),
            )
            error_context = {
                "table_name": primary_error_table,
                "service_name": "S3 to Snowflake",
                "error_type": "MIGRATION_FAILED",
                "cmp_id": cmp_id,
                "connection_id": connection_id,
                "user_id": user_id,
                "job_name": job_name,
                "migration_session_id": migration_session_id,
                "company_name": company_name,
                "connection_name": connection_name,
                "session": session,
                "total_files": len(keys) if 'keys' in locals() else 0,
                #  Add execution metrics for safe failure message
                "log_id": log_id if 'log_id' in locals() else None,
                "execution_time": time.time() - start_time if 'start_time' in locals() else 0.0,
                "total_rows": total_rows if 'total_rows' in locals() else 0,
                "successful_tables": len(processed_tables) if 'processed_tables' in locals() else 0,
                "total_tables": total_tables_for_error,
                "celery_child_mode": request.celery_child_mode,
            }
            
            error_result = handle_service_error(
                error=e,
                context=error_context,
                log_files=None,
                send_email=True,
                is_final_attempt=is_final_attempt
            )
            
            return {
                "status": "failed",
                "message": error_msg,
                "files_processed": processed if 'processed' in locals() else 0,
                "connection_id": connection_id,
                "migration_session_id": migration_session_id
            }
        
        finally:
            try:
                conn.close()
                log(f"[MIGRATION]  Snowflake connection closed")
            except Exception:
                pass

# Export the service class
__all__ = ["AmazonS3Service", "MigrationFailureTracker"]



