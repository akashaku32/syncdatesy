"""Load active EntityTransform from PostgreSQL at pipeline runtime."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SELECT_WITH_DEST = """
    SELECT version, mapping_json, mapping_overrides, script_text,
           script_sha256, entry_point, run_order, row_filter,
           service_name, entity_name,
           json_table_name, destination_table_name,
           destination_name_source, previous_destination_table_name,
           destination_rename_mode
    FROM entity_transform
    WHERE connection_id = %s
      AND table_name = %s
      AND is_active = TRUE
    ORDER BY version DESC
    LIMIT 1
"""

_SELECT_LEGACY = """
    SELECT version, mapping_json, mapping_overrides, script_text,
           script_sha256, entry_point, run_order, row_filter,
           service_name, entity_name
    FROM entity_transform
    WHERE connection_id = %s
      AND table_name = %s
      AND is_active = TRUE
    ORDER BY version DESC
    LIMIT 1
"""


def _row_to_dict(row, include_dest: bool) -> Dict[str, Any]:
    mapping_json = row[1]
    if isinstance(mapping_json, str):
        mapping_json = json.loads(mapping_json)
    mapping_overrides = row[2]
    if isinstance(mapping_overrides, str):
        mapping_overrides = json.loads(mapping_overrides)
    row_filter = row[7]
    if isinstance(row_filter, str):
        row_filter = json.loads(row_filter)
    result = {
        "version": row[0],
        "mapping_json": mapping_json,
        "mapping_overrides": mapping_overrides or {},
        "script_text": row[3],
        "script_sha256": row[4],
        "entry_point": row[5] or "",
        "run_order": row[6] or "filter_script_mapping",
        "row_filter": row_filter,
        "service_name": row[8],
        "entity_name": row[9],
    }
    if include_dest and len(row) > 14:
        result.update(
            {
                "json_table_name": row[10],
                "destination_table_name": row[11],
                "destination_name_source": row[12] or "DEFAULT",
                "previous_destination_table_name": row[13],
                "destination_rename_mode": row[14],
            }
        )
    else:
        result.update(
            {
                "json_table_name": None,
                "destination_table_name": None,
                "destination_name_source": "DEFAULT",
                "previous_destination_table_name": None,
                "destination_rename_mode": None,
            }
        )
    return result


def get_active_entity_transform(
    connection_id: int,
    table_name: str,
    existing_conn: Any = None,
) -> Optional[Dict[str, Any]]:
    should_close = False
    if existing_conn is not None:
        conn = existing_conn
    else:
        from utils.database_utils import postgres_connection

        conn = postgres_connection()
        should_close = True

    try:
        # SQLAlchemy Engine / Connection may be passed; prefer raw DBAPI.
        raw = conn
        if hasattr(conn, "raw_connection"):
            raw = conn.raw_connection()
        elif hasattr(conn, "connection") and hasattr(conn.connection, "cursor"):
            raw = conn.connection

        cursor = raw.cursor()
        try:
            try:
                cursor.execute(_SELECT_WITH_DEST, (connection_id, table_name))
                row = cursor.fetchone()
                include_dest = True
            except Exception as col_exc:
                err = str(col_exc).lower()
                if "destination_table_name" in err or "json_table_name" in err or "column" in err:
                    if hasattr(raw, "rollback"):
                        try:
                            raw.rollback()
                        except Exception:
                            pass
                    cursor = raw.cursor()
                    cursor.execute(_SELECT_LEGACY, (connection_id, table_name))
                    row = cursor.fetchone()
                    include_dest = False
                else:
                    raise
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        if not row:
            return None
        result = _row_to_dict(row, include_dest)
        print(
            "[get_active_entity_transform] "
            f"connection_id={connection_id} table_name={table_name!r}\n"
            f"{json.dumps(result, default=str, indent=2)}",
            flush=True,
        )
        return result
    except Exception as exc:
        err = str(exc).lower()
        if "entity_transform" in err and (
            "does not exist" in err or "undefinedtable" in err.replace(" ", "")
        ):
            logger.warning(
                "entity_transform table is missing; continuing without transform for %s",
                table_name,
            )
            return None
        logger.warning(
            "Failed to load entity transform for %s: %s", table_name, exc
        )
        return None
    finally:
        if should_close:
            conn.close()
