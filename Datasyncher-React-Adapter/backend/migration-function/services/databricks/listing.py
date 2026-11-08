"""SQL helpers for Databricks source schema listing (reverse ETL)."""

from __future__ import annotations

import re

from services.databricks.load_helpers import _q_ident

_STAGING_TABLE_RE = re.compile(
    r"^DS_.+_STAGING_[0-9]{8}_[0-9]{6}$",
    re.IGNORECASE,
)


def is_internal_listing_table(table_name: str) -> bool:
    name = str(table_name or "").strip()
    if not name:
        return True
    if "__datasyncher" in name.lower():
        return True
    return bool(_STAGING_TABLE_RE.match(name))


def _information_schema(catalog: str | None, relation: str) -> str:
    if catalog:
        return f"{_q_ident(catalog)}.information_schema.{relation}"
    return f"information_schema.{relation}"


def build_databricks_list_tables_sql(catalog: str | None, schema: str) -> str:
    schema_u = str(schema).replace("'", "''").upper()
    return (
        f"SELECT table_name, table_type\n"
        f"FROM {_information_schema(catalog, 'tables')}\n"
        f"WHERE upper(table_schema) = '{schema_u}'"
    )


def build_databricks_list_columns_sql(catalog: str | None, schema: str) -> str:
    schema_u = str(schema).replace("'", "''").upper()
    return (
        f"SELECT table_name, column_name, data_type, ordinal_position\n"
        f"FROM {_information_schema(catalog, 'columns')}\n"
        f"WHERE upper(table_schema) = '{schema_u}'\n"
        f"ORDER BY table_name, ordinal_position"
    )


def build_databricks_list_primary_keys_sql(catalog: str | None, schema: str) -> str:
    schema_u = str(schema).replace("'", "''").upper()
    cols = _information_schema(catalog, "key_column_usage")
    cons = _information_schema(catalog, "table_constraints")
    return (
        f"SELECT kcu.table_name, kcu.column_name\n"
        f"FROM {cols} kcu\n"
        f"JOIN {cons} tc\n"
        f"  ON kcu.constraint_name = tc.constraint_name\n"
        f" AND kcu.table_schema = tc.table_schema\n"
        f" AND kcu.table_catalog = tc.table_catalog\n"
        f"WHERE upper(kcu.table_schema) = '{schema_u}'\n"
        f"  AND upper(tc.constraint_type) = 'PRIMARY KEY'"
    )
