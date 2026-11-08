"""Shared NetSuite → warehouse migration helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from services.netsuite.config import normalize_config
from services.netsuite.extract import fetch_deletes, iter_batches
from services.netsuite.transports.factory import build_transport
from services.sap.as_source.to_snowflake import _get_snowflake_engine
from utils.database_utils import postgres_connection, update_table_status_snowflake
from utils.snowflake_utils import normalize_name

logger = logging.getLogger(__name__)


def _load_table_metadata(connection_id: int, table_name: str) -> Dict[str, Any]:
    conn = postgres_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_fields, is_delta, delta_name
                FROM source_table_selection
                WHERE connection_id = %s AND "table" = %s
                """,
                (connection_id, table_name),
            )
            row = cursor.fetchone()
            if not row:
                return {"fields": {}, "is_delta": False}
            fields_raw = row[0]
            if isinstance(fields_raw, str):
                fields = json.loads(fields_raw)
            else:
                fields = fields_raw or {}
            return {
                "fields": fields,
                "is_delta": bool(row[1]),
                "delta_name": row[2],
            }
    finally:
        conn.close()


def _ensure_snowflake_table(engine, schema: str, table: str, sample: pd.DataFrame) -> None:
    if sample.empty:
        return
    cols = []
    for col in sample.columns:
        sf_col = normalize_name(str(col))
        dtype = "VARCHAR"
        if pd.api.types.is_numeric_dtype(sample[col]):
            dtype = "NUMBER"
        elif pd.api.types.is_datetime64_any_dtype(sample[col]):
            dtype = "TIMESTAMP_NTZ"
        cols.append(f'"{sf_col}" {dtype}')
    ddl = f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ({", ".join(cols)})'
    with engine.connect() as conn:
        conn.execute(text(ddl))
        conn.commit()


def _write_dataframe_snowflake(engine, schema: str, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    renamed = df.copy()
    renamed.columns = [normalize_name(str(c)) for c in renamed.columns]
    renamed.to_sql(
        table.lower(),
        engine,
        schema=schema,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=500,
    )
    return len(renamed)


def migrate_table_to_snowflake(
    source_config: Dict[str, Any],
    destination_config: Dict[str, Any],
    connection_id: int,
    table_name: str,
    *,
    safety_interval: str,
    deleted_col: str,
    synced_col: str,
    job_name: Optional[str] = None,
    migration_session_id: Optional[int] = None,
) -> Dict[str, Any]:
    config = normalize_config(source_config)
    transport = build_transport(config)
    meta = _load_table_metadata(connection_id, table_name)
    column_names = list(meta["fields"].keys()) if meta.get("fields") else None
    schema = destination_config.get("schema") or destination_config.get("destination_schema") or "PUBLIC"
    target_table = normalize_name(table_name)
    sf_engine = _get_snowflake_engine(destination_config)
    safety_seconds = int(safety_interval or "120")
    watermark = None
    if meta.get("is_delta"):
        watermark = datetime.now(timezone.utc)

    total_rows = 0
    try:
        for batch in iter_batches(
            transport,
            table_name,
            columns=column_names,
            watermark=watermark,
            safety_interval_seconds=safety_seconds,
        ):
            df = pd.DataFrame(batch)
            if df.empty:
                continue
            now = datetime.now(timezone.utc)
            df[synced_col] = now
            df[deleted_col] = False
            _ensure_snowflake_table(sf_engine, schema, target_table, df)
            total_rows += _write_dataframe_snowflake(sf_engine, schema, target_table, df)

        if meta.get("is_delta"):
            for del_row in fetch_deletes(transport, table_name, watermark):
                rec_id = del_row.get("id")
                if rec_id is None:
                    continue
                with sf_engine.connect() as conn:
                    conn.execute(
                        text(
                            f'UPDATE "{schema}"."{target_table}" '
                            f'SET "{normalize_name(deleted_col)}" = TRUE '
                            f'WHERE "{normalize_name("id")}" = :rid'
                        ),
                        {"rid": rec_id},
                    )
                    conn.commit()

        update_table_status_snowflake(
            connection_id,
            table_name,
            "completed",
            job_name or "NetSuite-Snowflake Migration",
            migration_session_id or 0,
        )
        return {"table_name": table_name, "status": "success", "rows_loaded": total_rows}
    except Exception as exc:
        logger.exception("NetSuite -> Snowflake failed for %s: %s", table_name, exc)
        update_table_status_snowflake(
            connection_id,
            table_name,
            "failed",
            job_name or "NetSuite-Snowflake Migration",
            migration_session_id or 0,
        )
        return {"table_name": table_name, "status": "failed", "error": str(exc), "rows_loaded": total_rows}
    finally:
        transport.close()


def netsuite_to_snowflake_connector(request) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    deleted_col = request.is_deleted or "_FIVETRAN_DELETED"
    synced_col = request.is_updated or "_FIVETRAN_SYNCED"
    for table in request.selected_tables or []:
        results.append(
            migrate_table_to_snowflake(
                request.source_config,
                request.destination_config,
                request.connection_id,
                table,
                safety_interval=request.safety_interval,
                deleted_col=deleted_col,
                synced_col=synced_col,
                job_name=getattr(request, "job_name", None),
                migration_session_id=getattr(request, "migration_session_id", None),
            )
        )
    failed = [r for r in results if r.get("status") == "failed"]
    return {
        "status": "failed" if failed and len(failed) == len(results) else "success",
        "results": results,
        "successful_tables": len(results) - len(failed),
        "total_tables": len(results),
    }


def migrate_table_to_databricks(
    source_config: Dict[str, Any],
    destination_config: Dict[str, Any],
    connection_id: int,
    table_name: str,
    *,
    safety_interval: str,
    deleted_col: str,
    synced_col: str,
) -> Dict[str, Any]:
    from datasyncher_connections.databricks.connector import create_databricks_sqlalchemy_like_engine

    config = normalize_config(source_config)
    transport = build_transport(config)
    meta = _load_table_metadata(connection_id, table_name)
    column_names = list(meta["fields"].keys()) if meta.get("fields") else None
    target_table = normalize_name(table_name).lower()
    db_engine, schema = create_databricks_sqlalchemy_like_engine(
        destination_config,
        ensure_schema=True,
    )
    safety_seconds = int(safety_interval or "120")
    watermark = datetime.now(timezone.utc) if meta.get("is_delta") else None
    total_rows = 0
    try:
        for batch in iter_batches(
            transport,
            table_name,
            columns=column_names,
            watermark=watermark,
            safety_interval_seconds=safety_seconds,
        ):
            df = pd.DataFrame(batch)
            if df.empty:
                continue
            df[synced_col] = datetime.now(timezone.utc)
            df[deleted_col] = False
            renamed = df.copy()
            renamed.columns = [normalize_name(str(c)).lower() for c in renamed.columns]
            renamed.to_sql(
                target_table,
                db_engine,
                schema=schema,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=500,
            )
            total_rows += len(renamed)
        return {"table_name": table_name, "status": "success", "rows_loaded": total_rows}
    except Exception as exc:
        logger.exception("NetSuite -> Databricks failed for %s: %s", table_name, exc)
        return {"table_name": table_name, "status": "failed", "error": str(exc), "rows_loaded": total_rows}
    finally:
        transport.close()


def netsuite_to_databricks_connector(request) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    deleted_col = request.is_deleted or "_FIVETRAN_DELETED"
    synced_col = request.is_updated or "_FIVETRAN_SYNCED"
    for table in request.selected_tables or []:
        results.append(
            migrate_table_to_databricks(
                request.source_config,
                request.destination_config,
                request.connection_id,
                table,
                safety_interval=request.safety_interval,
                deleted_col=deleted_col,
                synced_col=synced_col,
            )
        )
    failed = [r for r in results if r.get("status") == "failed"]
    return {
        "status": "failed" if failed and len(failed) == len(results) else "success",
        "results": results,
        "successful_tables": len(results) - len(failed),
        "total_tables": len(results),
    }
