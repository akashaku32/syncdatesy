"""Update connection.retrieval_progress for Dynamics schema/listing and migration UI."""

from __future__ import annotations

import os


def _postgres_connection():
    from datasyncher_connections.postgres import create_postgres_connection

    return create_postgres_connection(retries=1, validate=False)


def format_retrieval_progress(current: int, total: int) -> str:
    return f"{max(0, int(current))}/{max(0, int(total))}"


def update_connection_retrieval_progress(
    connection_id: int,
    current: int,
    total: int,
    *,
    notify_ws: bool = True,
) -> None:
    if not connection_id or total <= 0:
        return
    progress = format_retrieval_progress(current, total)
    conn = None
    cursor = None
    try:
        conn = _postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE connection
            SET retrieval_progress = %s, updated_at = NOW()
            WHERE connection_id = %s;
            """,
            (progress, connection_id),
        )
        conn.commit()
        if notify_ws:
            try:
                from utils.websocket_utils import trigger_connection_ws_status_update

                trigger_connection_ws_status_update(connection_id)
            except Exception:
                pass
    except Exception as exc:
        print(f"[RETRIEVAL_PROGRESS] failed connection_id={connection_id} progress={progress}: {exc}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def clear_connection_retrieval_progress(connection_id: int, *, notify_ws: bool = True) -> None:
    if not connection_id:
        return
    conn = None
    cursor = None
    try:
        conn = _postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE connection
            SET retrieval_progress = NULL, updated_at = NOW()
            WHERE connection_id = %s;
            """,
            (connection_id,),
        )
        conn.commit()
        if notify_ws:
            try:
                from utils.websocket_utils import trigger_connection_ws_status_update

                trigger_connection_ws_status_update(connection_id)
            except Exception:
                pass
    except Exception as exc:
        print(f"[RETRIEVAL_PROGRESS] clear failed connection_id={connection_id}: {exc}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
