"""Validate DTP-style column-mapping JSON."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from config.transform_limits import MAX_MAPPED_COLUMNS, MAX_MAPPING_JSON_BYTES


class MappingValidationError(Exception):
    def __init__(self, message: str, path: str = ""):
        self.path = path
        super().__init__(message)


def _err(path: str, message: str) -> MappingValidationError:
    return MappingValidationError(message, path)


def check_mapping_json_size(raw: bytes | str) -> None:
    if isinstance(raw, str):
        size = len(raw.encode("utf-8"))
    else:
        size = len(raw)
    if size > MAX_MAPPING_JSON_BYTES:
        mb = MAX_MAPPING_JSON_BYTES // (1024 * 1024)
        raise MappingValidationError(
            f"Mapping JSON exceeds maximum size of {mb} MB ({size} bytes)."
        )


def parse_mapping_json(raw: str | bytes) -> Dict[str, Any]:
    check_mapping_json_size(raw)
    try:
        if isinstance(raw, bytes):
            data = json.loads(raw.decode("utf-8"))
        else:
            data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MappingValidationError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _err("$", "Root must be a JSON object")
    return data


_NON_SOURCE_MAPPING_TYPES = frozenset({"CONSTANT", "SCRIPT", "EXPRESSION"})


def _mapping_requires_source(row: Dict[str, Any]) -> bool:
    constant = row.get("constant_value")
    if constant is not None and str(constant).strip() != "":
        return False
    mapping_type = str(row.get("mapping_type") or "").strip().upper()
    if not mapping_type or mapping_type in _NON_SOURCE_MAPPING_TYPES:
        return False
    return True


def validate_mapping_structure(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Return list of errors with json_path keys."""
    errors: List[Dict[str, str]] = []

    def add(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if "source" not in data:
        add("$.source", "Required key 'source' is missing")
    elif not isinstance(data["source"], dict):
        add("$.source", "Must be an object")
    else:
        if not data["source"].get("objectname"):
            add("$.source.objectname", "Required")

    if "target" not in data:
        add("$.target", "Required key 'target' is missing")
    elif not isinstance(data["target"], dict):
        add("$.target", "Must be an object")

    if "field_mapping" not in data:
        add("$.field_mapping", "Required key 'field_mapping' is missing")
    elif not isinstance(data["field_mapping"], list):
        add("$.field_mapping", "Must be an array")
    else:
        mappings = data["field_mapping"]
        if len(mappings) > MAX_MAPPED_COLUMNS:
            add(
                "$.field_mapping",
                f"Too many mappings ({len(mappings)}); maximum is {MAX_MAPPED_COLUMNS}",
            )
        for idx, row in enumerate(mappings):
            base = f"$.field_mapping[{idx}]"
            if not isinstance(row, dict):
                add(base, "Must be an object")
                continue
            sources = row.get("src_fieldnames")
            source_names = (
                [name for name in sources if str(name or "").strip()]
                if isinstance(sources, list)
                else []
            )
            if _mapping_requires_source(row) and not source_names:
                add(f"{base}.src_fieldnames", "Must be a non-empty array")
            if "tgt_fieldname" not in row or not row.get("tgt_fieldname"):
                add(f"{base}.tgt_fieldname", "Required non-empty string")

    return errors


def validate_mapping_json(raw: str | bytes) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]]]:
    try:
        data = parse_mapping_json(raw)
    except MappingValidationError as exc:
        return None, [{"path": exc.path or "$", "message": str(exc)}]
    errors = validate_mapping_structure(data)
    if errors:
        return data, errors
    return data, []
