"""Shared NetSuite extraction: catalog, incremental batches, deletes."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from services.netsuite.transports.base import QueryTransport

logger = logging.getLogger(__name__)

DELTA_COLUMN = "lastmodifieddate"
ID_COLUMN = "id"
DEFAULT_BATCH_SIZE = 1000

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Invalid NetSuite identifier: {name!r}")
    return name


def list_tables_and_columns(transport: QueryTransport) -> List[Dict[str, Any]]:
    """Discover tables and column metadata for schema listing."""
    tables_sql = (
        "SELECT TABLE_NAME AS table_name FROM OA_TABLES "
        "WHERE TABLE_TYPE = 'TABLE' ORDER BY TABLE_NAME"
    )
    try:
        table_rows = transport.run_query(tables_sql, limit=5000)
    except Exception:
        table_rows = transport.run_query(
            "SELECT table_name AS table_name FROM SuiteAnalyticsMetadata.table ORDER BY table_name",
            limit=5000,
        )

    entities: List[Dict[str, Any]] = []
    for row in table_rows:
        table_name = str(row.get("table_name") or row.get("TABLE_NAME") or "").strip()
        if not table_name:
            continue
        columns = _fetch_columns(transport, table_name)
        field_defs = {}
        primary_keys: List[str] = []
        has_delta = False
        for col in columns:
            col_name = str(col.get("column_name") or col.get("COLUMN_NAME") or "").strip()
            if not col_name:
                continue
            data_type = str(col.get("type_name") or col.get("TYPE_NAME") or "STRING").upper()
            field_defs[col_name] = {
                "data_type": _map_ns_type(data_type),
                "is_primary_key": col_name.lower() == ID_COLUMN,
            }
            if col_name.lower() == ID_COLUMN:
                primary_keys.append(col_name)
            if col_name.lower() == DELTA_COLUMN:
                has_delta = True
        if not primary_keys and ID_COLUMN in {k.lower() for k in field_defs}:
            for k in field_defs:
                if k.lower() == ID_COLUMN:
                    primary_keys.append(k)
        entities.append(
            {
                "entity_name": table_name,
                "fields": [
                    {
                        "name": name,
                        "type": meta["data_type"],
                        "is_primary_key": meta.get("is_primary_key", False),
                    }
                    for name, meta in field_defs.items()
                ],
                "is_delta": has_delta,
                "delta_name": DELTA_COLUMN if has_delta else None,
            }
        )
    return entities


def _fetch_columns(transport: QueryTransport, table_name: str) -> List[Dict[str, Any]]:
    safe_table = _quote_ident(table_name)
    cols_sql = (
        f"SELECT COLUMN_NAME AS column_name, TYPE_NAME AS type_name "
        f"FROM OA_COLUMNS WHERE TABLE_NAME = '{safe_table}' ORDER BY ORDINAL_POSITION"
    )
    try:
        return transport.run_query(cols_sql, limit=2000)
    except Exception:
        return []


def _map_ns_type(type_name: str) -> str:
    t = (type_name or "STRING").upper()
    if "TIMESTAMP" in t or "DATE" in t:
        return "TIMESTAMP"
    if any(x in t for x in ("INT", "LONG", "NUMERIC", "DECIMAL", "DOUBLE", "FLOAT")):
        return "NUMBER"
    if "BOOL" in t:
        return "BOOLEAN"
    return "STRING"


def iter_batches(
    transport: QueryTransport,
    table_name: str,
    *,
    columns: Optional[List[str]] = None,
    watermark: Optional[datetime] = None,
    safety_interval_seconds: int = 120,
    batch_size: int = DEFAULT_BATCH_SIZE,
    last_id: Optional[int] = None,
) -> Iterator[List[Dict[str, Any]]]:
    """Keyset paging by id; optional incremental filter on lastmodifieddate."""
    safe_table = _quote_ident(table_name)
    col_list = "*"
    if columns:
        col_list = ", ".join(_quote_ident(c) for c in columns)

    where_parts: List[str] = []
    if watermark is not None:
        cutoff = watermark - timedelta(seconds=max(0, safety_interval_seconds))
        cutoff_str = cutoff.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        where_parts.append(f"{DELTA_COLUMN} > TO_TIMESTAMP('{cutoff_str}', 'YYYY-MM-DD HH24:MI:SS')")
    if last_id is not None:
        where_parts.append(f"{ID_COLUMN} > {int(last_id)}")

    where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    current_last_id = last_id

    while True:
        id_filter = ""
        if current_last_id is not None:
            id_filter = f" AND {ID_COLUMN} > {int(current_last_id)}" if where_parts else f" WHERE {ID_COLUMN} > {int(current_last_id)}"
        sql = (
            f"SELECT {col_list} FROM {safe_table}{where_clause}{id_filter} "
            f"ORDER BY {ID_COLUMN} FETCH NEXT {int(batch_size)} ROWS ONLY"
        )
        rows = transport.run_query(sql)
        if not rows:
            break
        yield rows
        id_values = [r.get(ID_COLUMN) or r.get("id") for r in rows if r.get(ID_COLUMN) or r.get("id") is not None]
        if not id_values:
            break
        current_last_id = max(int(v) for v in id_values)
        if len(rows) < batch_size:
            break


def fetch_deletes(
    transport: QueryTransport,
    table_name: str,
    since: Optional[datetime],
) -> List[Dict[str, Any]]:
    """Return deleted record ids for soft-delete column mapping."""
    if since is None:
        return []
    since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    safe_table = _quote_ident(table_name)
    sql = (
        "SELECT recordid AS id, recordtype AS record_type FROM DeletedRecord "
        f"WHERE recordtype = '{safe_table}' AND deleteddate > TO_TIMESTAMP('{since_str}', 'YYYY-MM-DD HH24:MI:SS')"
    )
    try:
        return transport.run_query(sql, limit=50000)
    except Exception as exc:
        logger.warning("DeletedRecord query failed for %s: %s", table_name, exc)
        return []
