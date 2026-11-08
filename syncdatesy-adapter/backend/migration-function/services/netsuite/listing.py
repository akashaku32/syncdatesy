"""Discover NetSuite tables and persist to source_table_selection."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager

from services.netsuite.config import normalize_config
from services.netsuite.extract import list_tables_and_columns
from services.netsuite.transports.factory import build_transport
from utils.database_utils import postgres_connection

logger = logging.getLogger(__name__)

SERVICE_NAME = "NetSuite"


@contextmanager
def _db_connection():
    conn = postgres_connection()
    try:
        yield conn
    finally:
        if conn:
            conn.close()


def _upsert_entities(cursor, connection_id: int, entities: list) -> int:
    processed = 0
    for ent in entities:
        table_name = ent.get("entity_name")
        if not table_name:
            continue
        table_fields_dict = {}
        primary_keys_list = []
        for field in ent.get("fields") or []:
            f_name = field.get("name")
            if not f_name:
                continue
            table_fields_dict[f_name] = {
                "data_type": field.get("type", "STRING"),
                "is_primary_key": field.get("is_primary_key", False),
            }
            if field.get("is_primary_key"):
                primary_keys_list.append(f_name)
        sql_upsert = """
            INSERT INTO source_table_selection (
                connection_id, "table", table_fields, primary_keys, selected, added_on,
                is_history, is_delta, delta_name, service_name, status, is_mapping_checked
            )
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, NULL, %s)
            ON CONFLICT (connection_id, "table")
            DO UPDATE SET
                table_fields = EXCLUDED.table_fields,
                primary_keys = EXCLUDED.primary_keys,
                is_delta = EXCLUDED.is_delta,
                delta_name = EXCLUDED.delta_name,
                service_name = EXCLUDED.service_name,
                status = NULL;
        """
        cursor.execute(
            sql_upsert,
            (
                connection_id,
                table_name,
                json.dumps(table_fields_dict),
                json.dumps(primary_keys_list),
                False,
                False,
                ent.get("is_delta", False),
                ent.get("delta_name"),
                SERVICE_NAME,
                False,
            ),
        )
        processed += 1
    return processed


def _mark_migration(conn, job_name: str, status: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE migration_record SET status = %s, updated_at = NOW() WHERE job_name = %s;",
            (status, job_name),
        )
    conn.commit()


def _notify(connection_id: int, job_name: str, success: bool, message: str) -> None:
    try:
        from utils.error_utils import update_migration_status_log

        update_migration_status_log(
            job_name=job_name,
            message=message,
            status="S" if success else "E",
        )
    except Exception as log_err:
        logger.warning("NetSuite listing log update failed: %s", log_err)
    try:
        from utils.websocket_utils import trigger_update_schema_ws_status_update

        trigger_update_schema_ws_status_update(connection_id)
    except Exception as ws_err:
        logger.warning("NetSuite listing websocket trigger failed: %s", ws_err)


def discover_and_populate_netsuite(connection_id: int, credentials: dict, job_name: str) -> dict:
    start = time.time()
    config = normalize_config(credentials)
    transport = build_transport(config)
    try:
        entities = list_tables_and_columns(transport)
        logger.info("NetSuite listing: discovered %s tables for connection %s", len(entities), connection_id)
        with _db_connection() as conn:
            with conn.cursor() as cursor:
                count = _upsert_entities(cursor, connection_id, entities)
            conn.commit()
            _mark_migration(conn, job_name, "completed")
        elapsed = time.time() - start
        message = (
            f"Update schema completed successfully in {elapsed:.2f} seconds. "
            f"Discovered {count} tables."
        )
        _notify(connection_id, job_name, success=True, message=message)
        return {
            "status_code": 200,
            "message": f"Successfully discovered and registered {count} NetSuite tables.",
            "tables_discovered": count,
        }
    except Exception as exc:
        logger.exception("NetSuite listing failed: %s", exc)
        try:
            with _db_connection() as conn:
                _mark_migration(conn, job_name, "failed")
        except Exception:
            logger.warning("Failed to mark NetSuite listing migration failed", exc_info=True)
        _notify(connection_id, job_name, success=False, message=f"Update schema failed: {exc}")
        raise
    finally:
        transport.close()
