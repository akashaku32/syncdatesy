"""Direct Snowflake readonly table listing via datasyncher_connections (optional Django path)."""

from __future__ import annotations


def fetch_existing_tables_from_snowflake(config_data: dict, schema_name: str) -> set[str]:
    """
    List table names in a Snowflake schema using the shared connection library.

    Mirrors migration-function ``Snowflake-listing-tables-readonly`` behavior.
    """
    from datasyncher_connections.snowflake.connector import create_snowflake_connector

    snowflake_payload = dict(config_data or {})
    snowflake_payload["schema"] = schema_name
    database_name = str(snowflake_payload.get("database") or "").strip()
    if not database_name or not schema_name:
        raise ValueError("Missing required Snowflake fields: database and schema.")

    conn = create_snowflake_connector(snowflake_payload, preflight=False)
    cur = conn.cursor()
    try:
        safe_db = '"' + database_name.replace('"', '""') + '"'
        safe_schema = '"' + schema_name.replace('"', '""') + '"'
        safe_schema_like = schema_name.replace("'", "''")

        cur.execute(f"SHOW SCHEMAS LIKE '{safe_schema_like}' IN DATABASE {safe_db}")
        schema_rows = cur.fetchall() or []
        schema_exists = any(
            isinstance(row, (list, tuple))
            and len(row) > 1
            and str(row[1]).strip().upper() == schema_name.upper()
            for row in schema_rows
        )
        if not schema_exists:
            return set()

        cur.execute(f"SHOW TABLES IN SCHEMA {safe_db}.{safe_schema}")
        table_rows = cur.fetchall() or []
        return {
            str(row[1]).strip().upper()
            for row in table_rows
            if isinstance(row, (list, tuple)) and len(row) > 1 and str(row[1]).strip()
        }
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
