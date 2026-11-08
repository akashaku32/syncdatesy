"""Helpers for grouping and filtering source files."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .file_path_utils import matches_any_pattern, normalize_posix_path
from .file_source_base import (
    SourceFileEntry,
    build_basename_counts,
    resolve_single_file_mapped_name,
    unique_table_name,
)


class FileMappingMethod(str, Enum):
    """Supported file mapping modes."""

    single_table_per_file = "single_table_per_file"
    multi_files_single_table = "multi_files_single_table"


def normalize_single_file_mapping(mapping: Any) -> Dict[str, str]:
    """Normalize a file-to-table mapping payload."""
    if not isinstance(mapping, dict):
        return {}

    normalized: Dict[str, str] = {}
    for raw_file_key, raw_table_name in mapping.items():
        file_key = normalize_posix_path(raw_file_key)
        table_name = str(raw_table_name or "").strip()
        if file_key and table_name:
            normalized[file_key] = table_name
            normalized.setdefault(file_key.rsplit("/", 1)[-1], table_name)
    return normalized


def resolve_file_entries(
    files: Iterable[SourceFileEntry],
    *,
    file_type: Optional[str] = None,
    single_file_table_mapping: Any = None,
    file_mapping_method: str = "single_table_per_file",
    multi_files_prefix: Optional[str] = None,
    multi_files_table_name: Optional[str] = None,
) -> Tuple[Dict[str, List[SourceFileEntry]], Dict[str, Any]]:
    """Group file entries into table work units."""
    file_entries = list(files or [])
    normalized_mapping = normalize_single_file_mapping(single_file_table_mapping)
    basename_counts = build_basename_counts([entry.file_key for entry in file_entries])

    # To match S3 behavior: ignore multi_files_prefix pattern matching (always select all files in scope)
    selected_files = list(file_entries)

    table_groups: Dict[str, List[SourceFileEntry]] = {}
    mapping_method = str(file_mapping_method or "single_table_per_file").strip()

    if mapping_method == FileMappingMethod.multi_files_single_table.value:
        table_name = str(multi_files_table_name or "").strip() or "MULTI_FILES_TABLE"
        table_groups[table_name] = selected_files
        return table_groups, {
            "file_mapping_method": mapping_method,
            "candidate_files_count": len(selected_files),
            "basename_counts": basename_counts,
        }

    used_names: set[str] = set()
    for entry in selected_files:
        mapped_name = resolve_single_file_mapped_name(
            entry.file_key,
            normalized_mapping,
            basename_counts=basename_counts,
        )

        # If a mapping is defined (user explicitly selected files to map),
        # skip any file that does not appear in the mapping.
        # This matches S3 behavior: only mapped files are written to source_table_selection.
        if normalized_mapping and mapped_name is None:
            continue

        table_name = unique_table_name(entry.file_key, mapped_name, used_names)
        table_groups.setdefault(table_name, []).append(entry)

    return table_groups, {
        "file_mapping_method": mapping_method,
        "candidate_files_count": len(selected_files),
        "basename_counts": basename_counts,
    }
