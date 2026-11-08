"""Shared file-source metadata and naming helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from .file_path_utils import normalize_posix_path, safe_path_basename, safe_path_stem


def sanitize_identifier(value: Optional[str]) -> str:
    """Normalize a value into a Snowflake-friendly identifier."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if cleaned and cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned.upper()


@dataclass(frozen=True)
class SourceFileEntry:
    """Metadata for a discovered source file."""

    file_key: str
    relative_path: str
    basename: str
    size_bytes: Optional[int] = None
    modified_at: Optional[datetime] = None
    inferred_file_type: Optional[str] = None


def utc_isoformat(value: Optional[datetime]) -> Optional[str]:
    """Return a UTC ISO-8601 string for a datetime."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def table_from_file_key(file_key: str) -> str:
    """Infer a table name from a file path."""
    return sanitize_identifier(safe_path_stem(file_key))


def build_basename_counts(file_keys: Iterable[str]) -> Dict[str, int]:
    """Count how often each filename appears across a scope."""
    counts: Dict[str, int] = {}
    for file_key in file_keys or []:
        basename = safe_path_basename(str(file_key or ""))
        if not basename:
            continue
        counts[basename] = counts.get(basename, 0) + 1
    return counts


def resolve_single_file_mapped_name(
    file_key: str,
    single_file_map: Dict[str, Any],
    basename_counts: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """Resolve a table name from a file-to-table mapping."""
    key = normalize_posix_path(file_key)
    if not key or not isinstance(single_file_map, dict) or not single_file_map:
        return None

    mapped = single_file_map.get(key)
    if mapped is not None and str(mapped).strip():
        return sanitize_identifier(str(mapped).strip())

    basename = safe_path_basename(key)
    if not basename:
        return None

    mapped = single_file_map.get(basename)
    if mapped is None or not str(mapped).strip():
        return None

    if basename_counts and basename_counts.get(basename, 0) > 1:
        return None

    return sanitize_identifier(str(mapped).strip())


def unique_table_name(
    file_key: str,
    mapped_table_name: Optional[str],
    used_names: Set[str],
) -> str:
    """Generate a deterministic unique table name for a file."""
    base_name = sanitize_identifier(mapped_table_name) if mapped_table_name else table_from_file_key(file_key)
    if not base_name:
        base_name = table_from_file_key(file_key) or "FILE"

    if base_name not in used_names:
        used_names.add(base_name)
        return base_name

    path_parts = [sanitize_identifier(part) for part in normalize_posix_path(file_key).split("/")[:-1]]
    path_parts = [part for part in path_parts if part]

    for depth in range(1, len(path_parts) + 1):
        suffix = "_".join(path_parts[-depth:])
        candidate = sanitize_identifier(f"{base_name}_{suffix}")
        if candidate and candidate not in used_names:
            used_names.add(candidate)
            return candidate

    index = 2
    while True:
        candidate = sanitize_identifier(f"{base_name}_{index}")
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index += 1


def infer_columns_from_dataframe(df, load_method: str = "upsert_file_row") -> Dict[str, Dict[str, Any]]:
    """Convert a pandas DataFrame into the table-fields shape used by the UI."""
    columns: Dict[str, Dict[str, Any]] = {}
    if df is None:
        return columns

    # Determine PK columns based on load_method
    if load_method == "upsert_file_row":
        pk_cols = {"_FILE", "_ROW"}
    elif load_method == "append_file_modified":
        pk_cols = {"_FILE", "_ROW", "_MODIFIED"}
    else:
        pk_cols = set()

    for index, column_name in enumerate(df.columns.tolist(), start=1):
        col_upper = str(column_name).upper()
        if col_upper in ("_SPECT_SYNCED", "_SPECT_DELETED"):
            continue

        series = df[column_name]
        dtype = str(getattr(series, "dtype", "object")).lower()
        # Default: compute an adaptive VARCHAR size for text-like columns
        data_type = "VARCHAR(255)"

        if col_upper == "_FILE":
            data_type = "VARCHAR(1024)"
        elif col_upper == "_ROW":
            data_type = "NUMBER(38,0)"
        elif col_upper == "_MODIFIED":
            data_type = "TIMESTAMP_TZ"
        elif col_upper == "_DATA":
            data_type = "VARIANT"
        else:
            if "int" in dtype:
                data_type = "NUMBER(38,0)"
            elif "float" in dtype or "double" in dtype:
                data_type = "FLOAT"
            elif "bool" in dtype:
                data_type = "BOOLEAN"
            elif "datetime" in dtype or "date" in dtype:
                data_type = "TIMESTAMP_NTZ"
            else:
                # For object / string-like columns, analyze actual values to pick a safe VARCHAR size.
                try:
                    non_null = series.dropna().astype(str)
                    if len(non_null) == 0:
                        data_type = "VARCHAR(512)"
                    else:
                        max_len = non_null.str.len().max()
                        if max_len <= 256:
                            data_type = "VARCHAR(256)"
                        elif max_len <= 512:
                            data_type = "VARCHAR(512)"
                        elif max_len <= 1024:
                            data_type = "VARCHAR(1024)"
                        elif max_len <= 4096:
                            data_type = "VARCHAR(4096)"
                        else:
                            data_type = "VARCHAR(16777216)"
                except Exception:
                    # Fallback if anything goes wrong during inspection
                    data_type = "VARCHAR(512)"
        columns[str(column_name)] = {
            "data_type": data_type,
            "is_primary_key": col_upper in pk_cols,
            "ordinal_position": index,
        }
    return columns
