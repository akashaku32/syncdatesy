"""Snowflake destination table name helpers for the migration worker.

Mirrors ``apps.elttool.services.snowflake_destination_name`` so the pipeline
can resolve ``destination_table_name`` without depending on Django.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

DESTINATION_NAME_SOURCE_DEFAULT = "DEFAULT"
DESTINATION_NAME_SOURCE_JSON = "JSON"
DESTINATION_NAME_SOURCE_MANUAL = "MANUAL"

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
    if len(candidate) > 255 or candidate in _SNOWFLAKE_RESERVED:
        return False
    return bool(_IDENT_RE.match(candidate))


def normalize_objectname_for_compare(value: Optional[str]) -> str:
    if not value:
        return ""
    text = str(value).strip().upper()
    for prefix in ("ENTITYOF", "ENTITY_OF_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.replace("O", "0")
