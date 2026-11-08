"""
Databricks load checkpoint helpers (per-chunk merge).

Persists folder/modstamp cursors on source_table_selection.last_synced and
cumulative merged row counts on migration_record — no staging_table_details table.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

FOLDER_FMT = "%Y-%m-%dT%H.%M.%SZ"
SF_MODSTAMP_CHECKPOINT_PREFIX = "sfmod:"
RESUMABLE_MIGRATION_STATUSES = frozenset({"failed", "in_progress"})


def parse_sync_datetime(value: Any) -> Optional[datetime]:
    """Parse ISO / Salesforce-style timestamps stored in last_synced."""
    if value is None:
        return None
    raw = str(value).strip()
    if raw.startswith(SF_MODSTAMP_CHECKPOINT_PREFIX):
        raw = raw[len(SF_MODSTAMP_CHECKPOINT_PREFIX) :]
    try:
        normalized = raw.replace("Z", "+00:00")
        if len(normalized) >= 5 and normalized[-5] in "+-" and normalized[-4:].isdigit():
            normalized = f"{normalized[:-5]}{normalized[-5:-2]}:{normalized[-2:]}"
        return datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None


def safe_salesforce_modstamp_checkpoint(
    modstamp: Any,
    safety_interval_seconds: Optional[int] = None,
) -> Optional[str]:
    """Return conservative SF watermark (modstamp minus safety window) as SOQL ISO."""
    dt = parse_sync_datetime(modstamp)
    if dt is None:
        return None
    seconds = 120
    if safety_interval_seconds is not None:
        try:
            seconds = max(int(safety_interval_seconds), 0)
        except (TypeError, ValueError):
            seconds = 120
    adjusted = (dt - timedelta(seconds=seconds)).replace(microsecond=0)
    if adjusted.tzinfo is None:
        adjusted = adjusted.replace(tzinfo=timezone.utc)
    return adjusted.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def safe_folder_checkpoint(
    folder_time: str,
    safety_interval_minutes: Optional[int] = None,
) -> Optional[str]:
    """
    Return folder cursor minus safety_interval (default 30 minutes).
    folder_time uses Dynamics Azure directory format: YYYY-MM-DDTHH.MM.SSZ
    """
    if not folder_time:
        return None
    raw = str(folder_time).strip()
    try:
        dt = datetime.strptime(raw, FOLDER_FMT)
    except ValueError:
        logger.warning("load_checkpoint | invalid folder_time=%r", raw)
        return None
    minutes = 30
    if safety_interval_minutes is not None:
        try:
            minutes = int(safety_interval_minutes)
        except (TypeError, ValueError):
            minutes = 30
        if minutes < 0:
            minutes = 30
    return (dt - timedelta(minutes=minutes)).strftime(FOLDER_FMT)


def normalize_load_checkpoint(
    checkpoint: str,
    *,
    safety_interval_minutes: Optional[int] = None,
    safety_interval_seconds: Optional[int] = None,
) -> tuple[Optional[str], bool]:
    """
    Normalize a load checkpoint for last_synced storage.

    Returns (stored_checkpoint, is_salesforce_modstamp).
    """
    raw = str(checkpoint).strip()
    if not raw:
        return None, False
    if raw.startswith(SF_MODSTAMP_CHECKPOINT_PREFIX):
        inner = raw[len(SF_MODSTAMP_CHECKPOINT_PREFIX) :]
        safe = safe_salesforce_modstamp_checkpoint(inner, safety_interval_seconds)
        if not safe:
            return None, True
        return f"{SF_MODSTAMP_CHECKPOINT_PREFIX}{safe}", True
    safe = safe_folder_checkpoint(raw, safety_interval_minutes)
    return safe, False


def last_synced_from_checkpoint(stored_checkpoint: Optional[str]) -> Optional[str]:
    """Value to persist on source_table_selection.last_synced for this checkpoint."""
    if not stored_checkpoint:
        return None
    raw = str(stored_checkpoint).strip()
    if raw.startswith(SF_MODSTAMP_CHECKPOINT_PREFIX):
        return raw[len(SF_MODSTAMP_CHECKPOINT_PREFIX) :]
    return raw


def get_last_synced_for_table(connection_id: int, table_name: str) -> Optional[str]:
    """Read source_table_selection.last_synced for a connection/table."""
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT last_synced FROM source_table_selection
            WHERE connection_id = %s AND "table" = %s
            """,
            (connection_id, table_name),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as exc:
        logger.warning(
            "load_checkpoint | get_last_synced failed connection_id=%s table=%s err=%s",
            connection_id,
            table_name,
            exc,
        )
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_migration_record_staging_count(
    *,
    connection_id: int,
    table_name: str,
    migration_session_id: Optional[int],
    job_name: Optional[str],
) -> int:
    """Return migration_record.staging_records_count (cumulative merged rows)."""
    if not all([migration_session_id, job_name]):
        return 0
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT staging_records_count FROM migration_record
            WHERE connection_id = %s
              AND migration_session_id = %s
              AND table_name = %s
              AND job_name = %s
            """,
            (connection_id, migration_session_id, table_name, job_name),
        )
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    except Exception as exc:
        logger.warning(
            "load_checkpoint | get_migration_count failed connection_id=%s table=%s err=%s",
            connection_id,
            table_name,
            exc,
        )
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_migration_record_info(
    *,
    connection_id: int,
    table_name: str,
    migration_session_id: Optional[int],
    job_name: Optional[str],
) -> Optional[dict[str, Any]]:
    """Return status and staging_records_count for the current migration_record row."""
    if not all([migration_session_id, job_name]):
        return None
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT status, staging_records_count
            FROM migration_record
            WHERE connection_id = %s
              AND migration_session_id = %s
              AND table_name = %s
              AND job_name = %s
            """,
            (connection_id, migration_session_id, table_name, job_name),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "status": row[0],
            "staging_records_count": int(row[1] or 0),
        }
    except Exception as exc:
        logger.warning(
            "load_checkpoint | get_migration_record failed connection_id=%s table=%s err=%s",
            connection_id,
            table_name,
            exc,
        )
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def should_resume_migration(
    migration_status: Optional[str],
    last_synced: Optional[str] = None,
) -> bool:
    """True when a prior run failed/in_progress and we have a cursor to resume from."""
    status = str(migration_status or "").strip().lower()
    if status not in RESUMABLE_MIGRATION_STATUSES:
        return False
    if status == "failed" and not last_synced:
        return False
    return True


def update_load_checkpoint(
    *,
    connection_id: int,
    table_name: str,
    folder_checkpoint: Optional[str],
    merged_records_count: int,
    migration_session_id: Optional[int] = None,
    job_name: Optional[str] = None,
    safety_interval_minutes: Optional[int] = None,
    safety_interval_seconds: Optional[int] = None,
) -> None:
    """
    After a successful per-chunk MERGE: advance last_synced and cumulative merged count.

    Checkpoint is written only after MERGE+TRUNCATE (not after COPY alone).
    """
    stored_checkpoint = folder_checkpoint
    if folder_checkpoint:
        stored_checkpoint, _is_sf = normalize_load_checkpoint(
            folder_checkpoint,
            safety_interval_minutes=safety_interval_minutes,
            safety_interval_seconds=safety_interval_seconds,
        )
    last_synced_value = last_synced_from_checkpoint(stored_checkpoint)

    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cur = conn.cursor()
        if last_synced_value:
            cur.execute(
                """
                UPDATE source_table_selection
                SET last_synced = %s
                WHERE connection_id = %s AND "table" = %s
                """,
                (last_synced_value, connection_id, table_name),
            )
        if migration_session_id and job_name:
            cur.execute(
                """
                UPDATE migration_record
                SET staging_records_count = %s,
                    status = 'in_progress',
                    updated_at = NOW()
                WHERE connection_id = %s
                  AND migration_session_id = %s
                  AND table_name = %s
                  AND job_name = %s
                """,
                (
                    int(merged_records_count or 0),
                    connection_id,
                    migration_session_id,
                    table_name,
                    job_name,
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.warning(
            "load_checkpoint | update failed connection_id=%s table=%s err=%s",
            connection_id,
            table_name,
            exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def mark_migration_failed(
    *,
    connection_id: int,
    table_name: str,
    migration_session_id: Optional[int] = None,
    job_name: Optional[str] = None,
) -> None:
    """Mark migration_record failed so the next run can resume from last_synced."""
    if not all([migration_session_id, job_name]):
        return
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE migration_record
            SET status = 'failed'
            WHERE connection_id = %s
              AND migration_session_id = %s
              AND table_name = %s
              AND job_name = %s
            """,
            (connection_id, migration_session_id, table_name, job_name),
        )
        conn.commit()
    except Exception as exc:
        logger.warning(
            "load_checkpoint | mark_failed connection_id=%s table=%s err=%s",
            connection_id,
            table_name,
            exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def finalize_migration_record(
    *,
    connection_id: int,
    table_name: str,
    migration_session_id: Optional[int] = None,
    job_name: Optional[str] = None,
    final_records_count: Optional[int] = None,
) -> None:
    """
    After successful table load: set final migrated count on migration_record.

    Does not wipe staging_records_count to 0 — Overview uses it like other connectors.
    """
    if not all([migration_session_id, job_name]):
        return
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cur = conn.cursor()
        if final_records_count is not None:
            cur.execute(
                """
                UPDATE migration_record
                SET staging_records_count = %s
                WHERE connection_id = %s
                  AND migration_session_id = %s
                  AND table_name = %s
                  AND job_name = %s
                """,
                (
                    int(final_records_count),
                    connection_id,
                    migration_session_id,
                    table_name,
                    job_name,
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.warning(
            "load_checkpoint | finalize failed connection_id=%s table=%s err=%s",
            connection_id,
            table_name,
            exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
