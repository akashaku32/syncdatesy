"""Snowflake destination table name normalisation for SAP OData → Snowflake.

Rules (from product change request):
1. Trim whitespace.
2. Replace every character that is not a letter, digit, underscore or ``$`` with ``_``.
   Collapse repeated underscores. Remove leading and trailing underscores.
3. If the result starts with a digit, prefix ``T_``.
4. Upper-case (Snowflake default unquoted identifiers).
5. Truncate to 255 characters.
6. If the result is a Snowflake reserved word, suffix ``_T``.

Example: ``/IMO/CMFIGL04`` → ``IMO_CMFIGL04``.
"""

from __future__ import annotations

import re
from typing import Callable, Optional, Tuple

DESTINATION_NAME_SOURCE_DEFAULT = "DEFAULT"
DESTINATION_NAME_SOURCE_JSON = "JSON"
DESTINATION_NAME_SOURCE_MANUAL = "MANUAL"

DESTINATION_NAME_SOURCES = frozenset(
    {
        DESTINATION_NAME_SOURCE_DEFAULT,
        DESTINATION_NAME_SOURCE_JSON,
        DESTINATION_NAME_SOURCE_MANUAL,
    }
)

# Subset of Snowflake reserved words that are unsafe as unquoted table names.
_SNOWFLAKE_RESERVED = frozenset(
    {
        "ACCOUNT",
        "ALL",
        "ALTER",
        "AND",
        "ANY",
        "AS",
        "BETWEEN",
        "BY",
        "CASE",
        "CAST",
        "CHECK",
        "COLUMN",
        "CONNECT",
        "CONNECTION",
        "CONSTRAINT",
        "CREATE",
        "CROSS",
        "CURRENT",
        "CURRENT_DATE",
        "CURRENT_TIME",
        "CURRENT_TIMESTAMP",
        "CURRENT_USER",
        "DATABASE",
        "DELETE",
        "DISTINCT",
        "DROP",
        "ELSE",
        "EXISTS",
        "FALSE",
        "FOLLOWING",
        "FOR",
        "FROM",
        "FULL",
        "GRANT",
        "GROUP",
        "GSCLUSTER",
        "HAVING",
        "ILIKE",
        "IN",
        "INCREMENT",
        "INNER",
        "INSERT",
        "INTERSECT",
        "INTO",
        "IS",
        "ISSUE",
        "JOIN",
        "LATERAL",
        "LEFT",
        "LIKE",
        "LOCALTIME",
        "LOCALTIMESTAMP",
        "MINUS",
        "NATURAL",
        "NOT",
        "NULL",
        "OF",
        "ON",
        "OR",
        "ORDER",
        "ORGANIZATION",
        "QUALIFY",
        "REGEXP",
        "REVOKE",
        "RIGHT",
        "RLIKE",
        "ROW",
        "ROWS",
        "SAMPLE",
        "SCHEMA",
        "SELECT",
        "SET",
        "SOME",
        "START",
        "TABLE",
        "TABLESAMPLE",
        "THEN",
        "TO",
        "TRIGGER",
        "TRUE",
        "TRY_CAST",
        "UNION",
        "UNIQUE",
        "UPDATE",
        "USING",
        "VALUES",
        "VIEW",
        "WHEN",
        "WHENEVER",
        "WHERE",
        "WITH",
    }
)

_INVALID_CHAR_RE = re.compile(r"[^A-Za-z0-9_$]")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_IDENT_RE = re.compile(r"^[A-Z_][A-Z0-9_$]*$")


def extract_json_target_objectname(mapping_json: Optional[dict]) -> Optional[str]:
    """Return raw ``target.objectname`` from mapping JSON, or None if missing/blank."""
    if not mapping_json or not isinstance(mapping_json, dict):
        return None
    target = mapping_json.get("target")
    if not isinstance(target, dict):
        return None
    raw = target.get("objectname")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def normalize_destination_table_name(raw: Optional[str]) -> Tuple[str, list]:
    """Normalise a candidate Snowflake destination table name.

    Returns ``(normalised_name, warnings)``. Empty string means invalid / empty result.
    """
    warnings: list = []
    if raw is None:
        return "", warnings
    name = str(raw).strip()
    if not name:
        return "", warnings

    name = _INVALID_CHAR_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name)
    name = name.strip("_")
    if not name:
        warnings.append(
            "Table name normalised to an empty string; falling back to the default name."
        )
        return "", warnings

    if name[0].isdigit():
        name = f"T_{name}"

    name = name.upper()[:255]

    if name in _SNOWFLAKE_RESERVED:
        name = f"{name}_T"
        warnings.append(
            f"Name is a Snowflake reserved word; using '{name}' instead."
        )

    return name, warnings


def is_valid_snowflake_destination_name(name: Optional[str]) -> bool:
    if not name or not str(name).strip():
        return False
    candidate = str(name).strip().upper()
    if len(candidate) > 255:
        return False
    if candidate in _SNOWFLAKE_RESERVED:
        return False
    return bool(_IDENT_RE.match(candidate))


def default_destination_display_name(service_name: str, entity_name: str) -> str:
    """Human-readable default (service + entity), as shown in UI messages."""
    service = (service_name or "").strip()
    entity = (entity_name or "").strip()
    if service and entity:
        return f"{service}_{entity}"
    return entity or service


def _mirror_legacy_normalize_name(base: str) -> str:
    """Lightweight mirror of migration-function ``normalize_name`` for Django host."""
    name = base.replace("__c", "_C")
    name = re.sub(r"__+", "_", name)
    name = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", name)
    name = re.sub(r"(?<=[A-Za-z])(\d)", r"_\1", name)
    name = re.sub(r"(\d)(?=[A-Za-z])", r"\1_", name)
    return name.upper()


def default_destination_table_name_django(
    service_name: str, entity_name: str, table_name: str
) -> str:
    """Snowflake destination when no JSON/manual override is applied.

    Preserves today's pipeline behaviour: legacy ``normalize_name`` on the stored
    table/entity name (entity-only in source_table_selection).
    """
    base = (table_name or entity_name or "").strip()
    if not base:
        base = default_destination_display_name(service_name, entity_name)
    try:
        from utils.snowflake_utils import normalize_name  # type: ignore

        return normalize_name(base)
    except Exception:
        return _mirror_legacy_normalize_name(base)


def resolve_destination_table_name(
    *,
    service_name: str,
    entity_name: str,
    table_name: str,
    mapping_json: Optional[dict],
    destination_name_source: Optional[str] = None,
    manual_destination_table_name: Optional[str] = None,
    default_fn: Optional[Callable[[str, str, str], str]] = None,
) -> dict:
    """Resolve effective destination table name with MANUAL > JSON > DEFAULT precedence."""
    default_fn = default_fn or default_destination_table_name_django
    default_name = default_fn(service_name, entity_name, table_name)
    default_display = default_destination_display_name(service_name, entity_name)
    warnings: list = []

    json_raw = extract_json_target_objectname(mapping_json)
    source = (destination_name_source or "").strip().upper() or None
    if source and source not in DESTINATION_NAME_SOURCES:
        source = None

    # Infer source when caller did not set it explicitly.
    if source is None:
        if manual_destination_table_name and str(manual_destination_table_name).strip():
            source = DESTINATION_NAME_SOURCE_MANUAL
        elif json_raw:
            source = DESTINATION_NAME_SOURCE_JSON
        else:
            source = DESTINATION_NAME_SOURCE_DEFAULT

    if source == DESTINATION_NAME_SOURCE_MANUAL:
        manual = (manual_destination_table_name or "").strip()
        candidate = manual.upper()
        if not is_valid_snowflake_destination_name(candidate):
            candidate, norm_warnings = normalize_destination_table_name(manual)
            warnings.extend(norm_warnings)
        if not candidate or not is_valid_snowflake_destination_name(candidate):
            warnings.append("Manual destination table name is invalid.")
            return {
                "json_table_name": json_raw,
                "destination_table_name": default_name,
                "destination_name_source": DESTINATION_NAME_SOURCE_DEFAULT,
                "default_destination_table_name": default_name,
                "default_display_name": default_display,
                "warnings": warnings,
                "valid": False,
            }
        return {
            "json_table_name": json_raw,
            "destination_table_name": candidate,
            "destination_name_source": DESTINATION_NAME_SOURCE_MANUAL,
            "default_destination_table_name": default_name,
            "default_display_name": default_display,
            "warnings": warnings,
            "valid": True,
        }

    if source == DESTINATION_NAME_SOURCE_JSON and json_raw:
        normalised, norm_warnings = normalize_destination_table_name(json_raw)
        warnings.extend(norm_warnings)
        if not normalised:
            warnings.append(
                "JSON target.objectname normalised to empty; using default destination table name."
            )
            return {
                "json_table_name": json_raw,
                "destination_table_name": default_name,
                "destination_name_source": DESTINATION_NAME_SOURCE_DEFAULT,
                "default_destination_table_name": default_name,
                "default_display_name": default_display,
                "warnings": warnings,
                "valid": bool(default_name),
            }
        return {
            "json_table_name": json_raw,
            "destination_table_name": normalised,
            "destination_name_source": DESTINATION_NAME_SOURCE_JSON,
            "default_destination_table_name": default_name,
            "default_display_name": default_display,
            "warnings": warnings,
            "valid": True,
        }

    # DEFAULT (explicit Use default name, missing JSON key, or empty JSON name)
    return {
        "json_table_name": json_raw,
        "destination_table_name": default_name,
        "destination_name_source": DESTINATION_NAME_SOURCE_DEFAULT,
        "default_destination_table_name": default_name,
        "default_display_name": default_display,
        "warnings": warnings,
        "valid": bool(default_name),
    }


def normalize_objectname_for_compare(value: Optional[str]) -> str:
    """Compare SAP object / entity names treating letter O and digit 0 as equivalent."""
    if not value:
        return ""
    text = str(value).strip().upper()
    for prefix in ("ENTITYOF", "ENTITY_OF_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.replace("O", "0")
