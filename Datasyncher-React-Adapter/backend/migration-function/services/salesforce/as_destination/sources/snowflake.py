"""Snowflake extract adapter for warehouse → Salesforce RETL."""

from __future__ import annotations

from sqlalchemy import text

from services.salesforce.client import (
    create_temp_index_table as _create_temp_index_table,
    destination_connection_function,
    drop_temp_table as _drop_temp_table,
)
from utils.simple_progress_tracker import SimpleProgressTracker


def fetch_chunks_from_temp_table(destination_connection, temp_table, fields, chunk_size=500):
    """Yields chunks from the Snowflake temp extract table using BETWEEN."""
    engine = destination_connection_function(destination_connection)
    snowflake_schema = destination_connection.get("schema", "").upper()
    fields_query = ", ".join(["__datasyncher_row"] + [f'"{f}"' for f in fields])

    start_row = 1
    chunk_index = 0

    with engine.connect() as conn:
        while True:
            end_row = start_row + chunk_size - 1
            stmt = f"""
                SELECT {fields_query}
                FROM {snowflake_schema}.{temp_table}
                WHERE __datasyncher_row BETWEEN {start_row} AND {end_row}
                ORDER BY __datasyncher_row
            """
            print(
                f"[DEBUG] Fetching chunk {chunk_index} from temp table, rows {start_row}–{end_row}"
            )
            result = conn.execute(text(stmt))
            rows = result.fetchall()
            columns = result.keys()

            if not rows:
                break

            yield [dict(zip(columns, row)) for row in rows]

            start_row = end_row + 1
            chunk_index += 1

    print("[DEBUG] Done chunked extract from temp table.")


class SnowflakeExtractAdapter:
    def create_temp_index_table(self, source_config, source_table, temp_table, fields):
        _create_temp_index_table(source_config, source_table, temp_table, fields)

    def fetch_chunks(self, source_config, temp_table, fields, chunk_size):
        return fetch_chunks_from_temp_table(
            source_config, temp_table, fields, chunk_size=chunk_size
        )

    def drop_temp_table(self, source_config, temp_table):
        _drop_temp_table(source_config, temp_table)

    def update_staging_count(
        self,
        source_config,
        connection_id,
        migration_session_id,
        job_name,
        table_name,
        temp_table,
    ):
        engine = destination_connection_function(source_config)
        try:
            SimpleProgressTracker.update_staging_count(
                engine=engine,
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                staging_table=temp_table,
                snowflake_schema=source_config.get("schema", "PUBLIC"),
            )
        finally:
            try:
                engine.dispose()
            except Exception:
                pass
