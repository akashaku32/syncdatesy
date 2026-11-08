"""SQL identifier quoting helpers (Snowflake / Databricks)."""
from __future__ import annotations

import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(dialect: str, name: str) -> str:
    """
    Validate and quote a SQL identifier for the given dialect.

    Accepts dotted names (``schema.table``); each segment is validated.
    """
    if not name or not isinstance(name, str):
        raise ValueError("identifier must be a non-empty string")
    parts = name.split(".")
    for part in parts:
        if not _IDENT_RE.match(part):
            raise ValueError(f"invalid SQL identifier segment: {part!r}")
    dialect_l = (dialect or "").lower()
    if dialect_l in ("snowflake", "sf"):
        return ".".join(f'"{p}"' for p in parts)
    if dialect_l in ("databricks", "spark", "hive"):
        return ".".join(f"`{p}`" for p in parts)
    raise ValueError(f"unsupported dialect: {dialect!r}")
