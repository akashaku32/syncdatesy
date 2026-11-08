"""Databricks extract adapter for warehouse → Salesforce RETL."""

from __future__ import annotations

from sqlalchemy import text

from services.databricks.load_helpers import (
    _catalog_from_config,
    _q_ident,
    _qualified,
    destination_connection_function,
)
from utils.simple_progress_tracker import SimpleProgressTracker


def _schema_from_config(source_config: dict | None) -> str:
    if not source_config:
        return ""
    return str(source_config.get("schema") or "").strip()


def _catalog_schema(source_config: dict | None) -> tuple[str | None, str]:
    catalog = _catalog_from_config(source_config)
    schema = _schema_from_config(source_config)
    if not schema:
        raise ValueError("Databricks schema is required for reverse ETL extract")
    return catalog, schema


def build_databricks_temp_extract_sql(
    catalog: str | None,
    schema: str,
    source_table: str,
    temp_table: str,
    fields: list[str],
) -> str:
    """CREATE OR REPLACE TABLE ... AS SELECT with ROW_NUMBER chunk index."""
    src = _qualified(catalog, schema, source_table)
    dst = _qualified(catalog, schema, temp_table)
    select_fields = [
        "ROW_NUMBER() OVER (ORDER BY (SELECT 1)) AS __datasyncher_row",
    ]
    for field in fields:
        ident = _q_ident(field)
        select_fields.append(f"CAST({ident} AS STRING) AS {ident}")
    return (
        f"CREATE OR REPLACE TABLE {dst} AS\n"
        f"SELECT {', '.join(select_fields)}\n"
        f"FROM {src}"
    )


def build_databricks_chunk_sql(
    catalog: str | None,
    schema: str,
    temp_table: str,
    fields: list[str],
    start_row: int,
    end_row: int,
) -> str:
    dst = _qualified(catalog, schema, temp_table)
    fields_query = ", ".join(["__datasyncher_row"] + [_q_ident(f) for f in fields])
    return (
        f"SELECT {fields_query}\n"
        f"FROM {dst}\n"
        f"WHERE __datasyncher_row BETWEEN {int(start_row)} AND {int(end_row)}\n"
        f"ORDER BY __datasyncher_row"
    )


def build_databricks_drop_sql(
    catalog: str | None, schema: str, temp_table: str
) -> str:
    return f"DROP TABLE IF EXISTS {_qualified(catalog, schema, temp_table)}"


def _result_column_names(result) -> list[str]:
    """Column names from SQLAlchemy or Databricks connector execute results."""
    keys_fn = getattr(result, "keys", None)
    if callable(keys_fn):
        try:
            return [str(k) for k in keys_fn()]
        except Exception:
            pass
    description = getattr(result, "description", None)
    if not description:
        return []
    names: list[str] = []
    for col in description:
        if isinstance(col, (list, tuple)) and col:
            names.append(str(col[0]))
        elif hasattr(col, "name"):
            names.append(str(col.name))
        else:
            names.append(str(col))
    return names


class DatabricksExtractAdapter:
    def __init__(
        self,
        connection_id=None,
        cmp_id=None,
        user_id=None,
        session=None,
        job_name=None,
        migration_session_id=None,
    ):
        self.connection_id = connection_id
        self.cmp_id = cmp_id
        self.user_id = user_id
        self.session = session
        self.job_name = job_name
        self.migration_session_id = migration_session_id

    def _engine(self, source_config):
        engine, _schema = destination_connection_function(
            source_config,
            self.connection_id,
            self.cmp_id,
            migration_session_id=self.migration_session_id,
            job_name=self.job_name,
            usr_id=self.user_id,
            session=self.session,
            skip_external_location_ensure=True,
        )
        return engine

    def create_temp_index_table(self, source_config, source_table, temp_table, fields):
        catalog, schema = _catalog_schema(source_config)
        sql = build_databricks_temp_extract_sql(
            catalog, schema, source_table, temp_table, fields
        )
        engine = self._engine(source_config)
        print("Creating Databricks temp extract table...")
        print(sql)
        with engine.connect() as conn:
            conn.execute(text(sql))

    def fetch_chunks(self, source_config, temp_table, fields, chunk_size):
        catalog, schema = _catalog_schema(source_config)
        engine = self._engine(source_config)
        start_row = 1
        chunk_index = 0
        size = int(chunk_size or 500)
        with engine.connect() as conn:
            while True:
                end_row = start_row + size - 1
                stmt = build_databricks_chunk_sql(
                    catalog, schema, temp_table, fields, start_row, end_row
                )
                print(
                    f"[DEBUG] Fetching Databricks chunk {chunk_index} rows {start_row}–{end_row}"
                )
                result = conn.execute(text(stmt))
                rows = result.fetchall()
                columns = _result_column_names(result)
                if not rows:
                    break
                yield [dict(zip(columns, row)) for row in rows]
                start_row = end_row + 1
                chunk_index += 1
        print("[DEBUG] Done chunked extract from Databricks temp table.")

    def drop_temp_table(self, source_config, temp_table):
        if not temp_table:
            print("WARNING: Temp table name not supplied. Skipping drop.")
            return
        catalog, schema = _catalog_schema(source_config)
        sql = build_databricks_drop_sql(catalog, schema, temp_table)
        try:
            engine = self._engine(source_config)
            with engine.connect() as conn:
                conn.execute(text(sql))
            print(f"INFO: Temp table dropped: {sql}")
        except Exception as e:
            print(f"WARNING: Failed to drop temp table {temp_table}: {e}")

    def update_staging_count(
        self,
        source_config,
        connection_id,
        migration_session_id,
        job_name,
        table_name,
        temp_table,
    ):
        catalog, schema = _catalog_schema(source_config)
        qualified = _qualified(catalog, schema, temp_table)
        engine = self._engine(source_config)
        try:
            SimpleProgressTracker.update_staging_count(
                engine=engine,
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                staging_table=temp_table,
                snowflake_schema=schema,
                qualified_table=qualified,
            )
        finally:
            try:
                engine.dispose()
            except Exception:
                pass
