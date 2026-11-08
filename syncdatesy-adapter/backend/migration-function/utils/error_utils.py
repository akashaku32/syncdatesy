"""
Error Handling and Logging Utility Module

This module provides centralized error handling, logging, and file creation
functionality for the DataSyncher migration services.

Features:
- Centralized log table logging
- Error file logging (JSONL and CSV formats)
- Standardized error response creation
- Retry mechanisms for logging operations

Usage:
    from utils.error_utils import log_error_to_tool_log, log_error_to_files

    # Log error to log table
    log_error_to_tool_log(
        cmp_id=cmp_id,
        connection_id=connection_id,
        user_id=user_id,
        table_name="Account",
        error_message="Connection failed",
        session=session
    )

    # Log error to files
    log_error_to_files(
        error_data={
            "table_name": "Account",
            "error_message": "Connection failed",
            "error_type": "TABLE_MIGRATION_FAILED",
            "connection_id": connection_id,
            "job_name": job_name,
            "migration_session_id": migration_session_id
        },
        log_files={
            'csv_file': Path('path/to/file.csv'),
            'response_file': Path('path/to/file.jsonl')
        }
    )
"""

import json
import re
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import pandas as pd

# Set up logging for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def create_migration_start_message(trigger_type: str, migration_type: str = "full") -> str:
    """
    Generate standardized migration start message.
    
    Args:
        trigger_type: "manual", "auto_sync", or "scheduled"
        migration_type: "full", "delta", "schema_refresh", etc.
        
    Returns:
        str: Formatted start message
    """
    trigger_mapping = {
        "manual": "Manual",
        "auto_sync": "Auto/Scheduled",
        "scheduled": "Auto/Scheduled"
    }
    trigger_display = trigger_mapping.get(trigger_type, trigger_type.capitalize())
    
    if migration_type == "schema_refresh":
        return f"Update schema in progress (Trigger: {trigger_display})"
    else:
        return f"Migration in progress (Trigger: {trigger_display})"


def create_table_status_message(table_name: str, status: str, operation_type: str = None) -> str:
    """
    Generate standardized single-table status message.
    
    Args:
        table_name: Name of the table
        status: "in_progress", "completed", "failed"
        operation_type: "delta_refresh", "full_load", "schema_refresh", etc.
        
    Returns:
        str: Formatted table status message
    """
    status_display = {
        "in_progress": "initiated",
        "completed": "completed successfully",
        "failed": "failed"
    }.get(status, status)
    
    if operation_type == "delta_refresh":
        return f"Table {table_name} delta refresh {status_display}"
    elif operation_type == "full_load":
        return f"Table {table_name} full initial load {status_display}"
    elif operation_type == "schema_refresh":
        return f"Table {table_name} schema refresh {status_display}"
    else:
        return f"Table {table_name} migration {status_display}"

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


def log_error_to_tool_log(
    cmp_id: Optional[int],
    connection_id: Optional[int],
    user_id: Optional[int],
    table_name: str,
    error_message: str,
    session: Optional[str],
    job_name: Optional[str] = None,
    migration_record_id: Optional[int] = None,
    max_retries: int = 3
) -> bool:
    """
    Log error to log table with retry mechanism.

    Args:
        cmp_id: Company ID
        connection_id: Connection ID
        user_id: User ID
        table_name: Table name or operation identifier
        error_message: Error message/details
        session: Session identifier
        job_name: Optional job name
        migration_record_id: Optional migration record ID
        max_retries: Maximum retry attempts

    Returns:
        bool: True if logged successfully, False otherwise
    """
    from utils.database_utils import postgres_connection

    # 🚫 Prevent duplicate job-level migration logs (handled by log_migration_failure)
    if table_name == "Migration Process":
        logger.debug("Skipping job-level insert in log_error_to_tool_log (handled by migration lifecycle)")
        return True

    # Some auth/test endpoints do not have migration context.
    # Avoid DB NOT NULL violations/noisy retries when required fields are missing.
    if cmp_id is None or connection_id is None or user_id is None:
        logger.warning(
            "Skipping log_error_to_tool_log due to missing context: cmp_id=%s, connection_id=%s, user_id=%s, table_name=%s",
            cmp_id,
            connection_id,
            user_id,
            table_name,
        )
        return False

    for attempt in range(max_retries):
        try:
            conn = postgres_connection()
            if conn is None:
                logger.error(f"❌ Attempt {attempt + 1}: Failed to connect to database")
                if attempt < max_retries - 1:
                    continue
                return False

            cursor = conn.cursor()

            # Proactively look up migration_record_id if not provided
            if migration_record_id is None and (job_name or table_name):
                try:
                    from utils.database_utils import get_migration_record_id
                    
                    migration_record_id = get_migration_record_id(
                        cursor,
                        job_name=job_name,
                        table_name=table_name if table_name != 'Unknown' else None,
                        connection_id=connection_id
                    )
                except Exception as lookup_err:
                    logger.warning(f"⚠️ Failed to look up migration_record_id in log_error_to_tool_log: {lookup_err}")

            # Insert error log
            insert_tool_log(
                cmp_id, connection_id, user_id, table_name, "F", 
                error_message, session, job_name=job_name, 
                migration_record_id=migration_record_id
            )

            cursor.close()
            conn.close()

            logger.info(f"✅ Error logged to log for table '{table_name}'")
            return True

        except Exception as e:
            logger.error(f"❌ Attempt {attempt + 1}: Failed to log error to log: {e}")
            if attempt < max_retries - 1:
                continue
            return False

    return False


_TERMINAL_JOB_LEVEL_STATUSES = frozenset({"S", "P", "F"})


def _normalize_log_status(status: Optional[str]) -> str:
    return str(status or "").strip().upper()


def job_level_terminal_write_allowed(
    job_name: Optional[str],
    status: str,
    celery_child_mode: bool = False,
) -> bool:
    """
    Return True when a job-level terminal log write (S/P/F) is permitted.

    Progress writes (status I) bypass this helper. Children in fan-out mode and
    any caller while migration_record rows remain in_progress must not finalize
    the job-level log — only finalize_migration_orchestration may.
    """
    normalized = _normalize_log_status(status)
    if normalized not in _TERMINAL_JOB_LEVEL_STATUSES:
        return True

    if celery_child_mode:
        logger.info(
            "SKIP job-level completion while tables in progress | job=%s | status=%s | reason=celery_child_mode",
            job_name,
            normalized,
        )
        return False

    if not job_name:
        return True

    try:
        from utils.database_utils import postgres_connection

        conn = postgres_connection()
        if conn is None:
            logger.warning(
                "job_level_terminal_write_allowed: no DB connection; blocking write | job=%s",
                job_name,
            )
            return False

        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1
                        FROM migration_record
                        WHERE job_name = %s
                          AND status = 'in_progress'
                    ),
                    (
                        SELECT COUNT(*)
                        FROM migration_record
                        WHERE job_name = %s
                          AND table_name IS NOT NULL
                          AND table_name <> ''
                    ),
                    (
                        SELECT COUNT(*)
                        FROM migration_record
                        WHERE job_name = %s
                          AND table_name IS NOT NULL
                          AND table_name <> ''
                          AND status IN ('completed', 'failed')
                    )
                """,
                (job_name, job_name, job_name),
            )
            has_in_progress, total_tables, terminal_tables = cursor.fetchone()
            has_in_progress = bool(has_in_progress)
            total_tables = int(total_tables or 0)
            terminal_tables = int(terminal_tables or 0)
        finally:
            cursor.close()
            conn.close()

        # Update schema (listing_tables) uses one job-level migration_record row
        # (table_name IS NULL). Allow terminal log writes while that row is still
        # in_progress — MR status is finalized by the listing service / celery layer.
        if (
            has_in_progress
            and total_tables == 0
            and job_name
            and (
                "listing_tables" in job_name
                or "post_missing_file_schema_refresh" in job_name
            )
        ):
            return True

        if has_in_progress:
            logger.info(
                "SKIP job-level completion while tables in progress | job=%s | status=%s | reason=mr_in_progress",
                job_name,
                normalized,
            )
            return False
        if total_tables > 0 and terminal_tables < total_tables:
            logger.info(
                "SKIP job-level completion while tables in progress | job=%s | status=%s | "
                "reason=mr_tables_not_terminal | terminal=%s | total=%s",
                job_name,
                normalized,
                terminal_tables,
                total_tables,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "job_level_terminal_write_allowed check failed (blocking write) | job=%s | err=%s",
            job_name,
            exc,
        )
        return False


def insert_tool_log(
    cmp_id,
    connection_id,
    usr_id,
    table_name,
    status,
    message,
    session,
    job_name=None,
    migration_record_id=None,
    celery_child_mode: bool = False,
):
    """
    Insert a log entry into the log table with race condition protection.

    Args:
        cmp_id: Company ID
        connection_id: Connection ID
        usr_id: User ID
        table_name: Table name (deprecated, kept for backward compatibility)
        status: Status (e.g., 'success', 'failed')
        message: Log message
        session: Session identifier
        job_name: Optional job name for migration-related logs
        migration_record_id: Optional migration record ID for table-specific logs
    """
    from utils.database_utils import postgres_connection
    import psycopg2

    normalized_status = _normalize_log_status(status)
    if (
        migration_record_id is None
        and job_name
        and normalized_status in _TERMINAL_JOB_LEVEL_STATUSES
        and not job_level_terminal_write_allowed(job_name, normalized_status, celery_child_mode)
    ):
        return

    conn = postgres_connection()
    cursor = conn.cursor()

    max_retries = 5
    for attempt in range(max_retries):
        try:
            # Use PostgreSQL advisory lock to serialize log_id generation
            # Lock ID 12345 is arbitrary but consistent for log
            # cursor.execute("SELECT pg_advisory_xact_lock(12345)")
            
            # Get next log_id (protected by advisory lock)
            # cursor.execute("SELECT COALESCE(MAX(log_id), 0) + 1 FROM log")
             # ✅ Use PostgreSQL sequence for atomic log_id generation (race-condition-free)
            cursor.execute("SELECT nextval('log_log_id_seq')")
            new_log_id = cursor.fetchone()[0]

            # Insert log entry (removed "table" column, added job_name and migration_record_id)
            cursor.execute('''
                INSERT INTO log (log_id, cmp_id, connection_id, user_id, time, status, message, session, job_name, migration_record_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                new_log_id, cmp_id, connection_id, usr_id,
                datetime.now(timezone.utc), status, message, session,
                job_name, migration_record_id
            ))

            conn.commit()  # Commit releases advisory lock automatically
            cursor.close()
            conn.close()

            # ✅ TRIGGER: WebSocket update for activity logs (only for job-level logs)
            if migration_record_id is None:
                try:
                    from utils.websocket_utils import (
                        trigger_connection_activity_log_update,
                        trigger_company_connections_status_update,
                        trigger_connection_ws_status_update,
                    )
                    trigger_connection_activity_log_update(connection_id)
                    if cmp_id:
                        trigger_company_connections_status_update(cmp_id)
                    # Overview / connection status WS listens here for migration_status (job-level logs).
                    trigger_connection_ws_status_update(connection_id)
                except Exception as ws_err:
                    logger.warning(f"⚠️ Error triggering activity log WebSocket update in insert_tool_log: {ws_err}")

            return  # Success
            
        except psycopg2.errors.UniqueViolation:
            # Race condition - retry
            conn.rollback()
            if attempt < max_retries - 1:
                logger.warning(f"⚠️ Duplicate log_id detected (attempt {attempt + 1}), retrying...")
                import time
                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
                continue
            else:
                logger.error(f"❌ Failed to insert log after {max_retries} attempts due to race condition")
                cursor.close()
                conn.close()
                raise
                
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            raise


def update_migration_status_log(
    job_name: str,
    message: str,
    status: str,
    session: Optional[str] = None,
    log_id: Optional[int] = None,
    connection_id: Optional[int] = None,
    celery_child_mode: bool = False,
) -> bool:
    """
    Update existing job-level migration status log entry with safety guards.
    
    Safety layers (all enforced at DB level):
      1. SELECT ... FOR UPDATE — row lock prevents concurrent modification
      2. Terminal State Guard — skip if current status is already 'S', 'P', or 'F'
      3. Atomic Status Precedence — F > P > S (CASE-based, single UPDATE)
    
    Escalation policy:
      - IN_PROGRESS → S | P | F  (allowed)
      - S | P| F → anything      (blocked - terminal states)
    
    This function is fully exception-safe: logging errors are swallowed
    to never crash the calling migration process.
    
    Args:
        job_name: Job name for the migration (required)
        message: New message to set
        status: New status to set (S, P, F)
        session: Optional session identifier for additional filtering
    
    Returns:
        bool: True if update successful, False otherwise
    """
    try:
        from utils.database_utils import postgres_connection
        
        if not job_name:
            logger.warning("⚠️ update_migration_status_log: job_name is required")
            return False

        normalized_status = _normalize_log_status(status)
        if normalized_status in _TERMINAL_JOB_LEVEL_STATUSES and not job_level_terminal_write_allowed(
            job_name, normalized_status, celery_child_mode=celery_child_mode
        ):
            return False
        
        conn = postgres_connection()
        if conn is None:
            logger.error("❌ Failed to establish database connection")
            return False
        
        cursor = conn.cursor()
        
        try:
            # ── Advisory lock for cross-process serialization ──
            # Use provided log_id or hash of job_name to avoid global locking
            if log_id:
                lock_id = log_id
            else:
                import zlib
                lock_id = zlib.crc32(job_name.encode('utf-8'))
            
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
            
            # ── Build base WHERE clause ──
            # ── Build base WHERE clause ──
            if log_id:
                where_clause = "log_id = %s"
                params = [log_id]
                logger.info(f"🔍 Using log_id={log_id} for update (job_name={job_name})")
            else:
                where_clause = "job_name = %s AND migration_record_id IS NULL"
                params = [job_name]
                
                if session:
                    where_clause += " AND session = %s"
                    params.append(session)
            
            # ── Row lock + state check in one query ──
            check_query = f"""
                SELECT log_id, status, message, connection_id, cmp_id FROM log
                WHERE {where_clause}
                ORDER BY log_id DESC
                LIMIT 1
                FOR UPDATE
            """
            cursor.execute(check_query, params)
            existing_entry = cursor.fetchone()
            
            if not existing_entry:
                # Fallback: if session-specific lookup misses, retry by job_name only.
                # This prevents creating a new "completed" row while stale
                # "Migration in progress" row remains for the same job.
                if (not log_id) and session:
                    fallback_query = """
                        SELECT log_id, status, message, connection_id, cmp_id FROM log
                        WHERE job_name = %s
                          AND migration_record_id IS NULL
                        ORDER BY log_id DESC
                        LIMIT 1
                        FOR UPDATE
                    """
                    cursor.execute(fallback_query, [job_name])
                    existing_entry = cursor.fetchone()

                if not existing_entry:
                    logger.warning(f"⚠️ No existing job-level log entry found for job_name: {job_name}")
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    return False
            
            db_log_id, current_status, current_message, db_connection_id, db_cmp_id = existing_entry
            
            # ── Terminal State Guard ──
            # Once S, P, or F is reached, it is terminal.
            # (P is terminal because it means we have recorded table successes, 
            # and we should not allow a late 'Job Crash' to overwrite it with 'F').
            if current_status in ('S', 'P', 'F'):
                logger.debug(f"🔒 Terminal state reached ({current_status}) for {job_name}. Ignoring update to '{status}'.")
                conn.rollback()
                cursor.close()
                conn.close()
                return True
            
            # ── Message Guard ──
            # Skip update only if BOTH status and message are already what we want.
            # This allows progress updates (same status 'I', different message) to pass through.
            if current_status == status and current_message == message:
                conn.rollback()
                cursor.close()
                conn.close()
                return True
            
            # ── Atomic status precedence update (F > P > S) ──
            # The CASE ensures that severity F overrides P which overrides S.
            # Note: Because of our terminal guard above, once it reaches P, 
            # it stays in a terminal state for the purpose of this function.
            update_query = f"""
                UPDATE log
                SET message = %s,
                    status = CASE
                        WHEN %s = 'F' THEN 'F'
                        WHEN %s = 'P' AND status NOT IN ('F') THEN 'P'
                        WHEN %s = 'S' AND status NOT IN ('F', 'P') THEN 'S'
                        ELSE status
                    END,
                    time = %s
                WHERE log_id = %s
                  AND status NOT IN ('S', 'P', 'F')
            """
            update_params = [
                message, status, status, status,
                datetime.now(timezone.utc),
                db_log_id
            ]
            
            cursor.execute(update_query, update_params)
            rows_updated = cursor.rowcount
            
            if rows_updated == 0:
                logger.warning(
                    f"⚠️ Update query returned 0 rows for job_name: {job_name} "
                    f"(current_status={current_status}, requested={status})"
                )
                conn.rollback()
                cursor.close()
                conn.close()
                return False

            # Close duplicate stale in-progress rows for the same job (same job_name).
            if db_connection_id and job_name and status in ('S', 'P', 'F'):
                cursor.execute(
                    """
                    UPDATE log
                    SET message = %s,
                        status = %s,
                        time = %s
                    WHERE connection_id = %s
                      AND migration_record_id IS NULL
                      AND job_name = %s
                      AND status = 'I'
                      AND log_id <> %s
                    """,
                    (message, status, datetime.now(timezone.utc), db_connection_id, job_name, db_log_id),
                )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"✅ Updated migration status log for job_name: {job_name}, status: {status}")

            # Trigger activity-log websocket for in-place updates.
            ws_connection_id = connection_id or db_connection_id
            if ws_connection_id:
                try:
                    from utils.websocket_utils import (
                        trigger_connection_activity_log_update,
                        trigger_company_connections_status_update,
                        trigger_connection_ws_status_update,
                    )
                    trigger_connection_activity_log_update(ws_connection_id)
                    if db_cmp_id:
                        trigger_company_connections_status_update(db_cmp_id)
                    # Keep connection overview (connection_table_status WS) in sync while job-level log moves I→S/E/P/F.
                    trigger_connection_ws_status_update(ws_connection_id)
                except Exception as ws_err:
                    logger.warning(
                        f"⚠️ Failed to trigger activity-log websocket for connection {ws_connection_id}: {ws_err}"
                    )
            
            return True
            
        except Exception as e:
            try:
                conn.rollback()
                cursor.close()
                conn.close()
            except Exception:
                pass  # Swallow cleanup errors
            logger.error(f"❌ Failed to update migration status log: {e}")
            return False
    
    except Exception as outer_err:
        # Outermost safety net — never crash the migration
        logger.error(f"❌ update_migration_status_log outer error (swallowed): {outer_err}")
        return False


def update_table_migration_status_log(
    migration_record_id: int,
    message: str,
    status: str,
    session: Optional[str] = None,
    job_name: Optional[str] = None,
    connection_id: Optional[int] = None,
) -> bool:
    """
    Update the latest table-level log entry (migration_record_id is set) with a new message/status.
    This mirrors update_migration_status_log behavior but targets table logs.
    """
    try:
        from utils.database_utils import postgres_connection
        if not migration_record_id:
            logger.warning("⚠️ update_table_migration_status_log: migration_record_id is required")
            return False

        conn = postgres_connection()
        if conn is None:
            logger.error("❌ Failed to establish database connection")
            return False

        cursor = conn.cursor()
        try:
            where_clause = "migration_record_id = %s"
            params = [migration_record_id]
            if job_name:
                where_clause += " AND job_name = %s"
                params.append(job_name)
            if session:
                where_clause += " AND session = %s"
                params.append(session)

            update_query = f"""
                UPDATE log
                SET message = %s,
                    status = %s,
                    time = %s
                WHERE log_id = (
                    SELECT log_id
                    FROM log
                    WHERE {where_clause}
                    ORDER BY log_id DESC
                    LIMIT 1
                )
            """
            update_params = [message, status, datetime.now(timezone.utc)] + params
            cursor.execute(update_query, update_params)
            rows_updated = cursor.rowcount

            if rows_updated == 0:
                conn.rollback()
                cursor.close()
                conn.close()
                logger.warning(
                    "⚠️ No table-level log entry found for migration_record_id=%s, job_name=%s",
                    migration_record_id, job_name
                )
                return False

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(
                "✅ Updated table-level migration log for migration_record_id=%s, status=%s",
                migration_record_id, status
            )

            ws_connection_id = connection_id
            if ws_connection_id:
                try:
                    from utils.websocket_utils import trigger_connection_activity_log_update
                    trigger_connection_activity_log_update(ws_connection_id)
                except Exception as ws_err:
                    logger.warning(
                        "⚠️ Failed to trigger activity-log websocket for connection %s: %s",
                        ws_connection_id, ws_err
                    )

            return True
        except Exception as e:
            try:
                conn.rollback()
                cursor.close()
                conn.close()
            except Exception:
                pass
            logger.error(f"❌ Failed to update table-level migration log: {e}")
            return False
    except Exception as outer_err:
        logger.error(f"❌ update_table_migration_status_log outer error (swallowed): {outer_err}")
        return False


def log_error_to_files(
    error_data: Dict[str, Any],
    log_files: Dict[str, Path],
    max_retries: int = 3
) -> bool:
    """
    Log error to both JSONL response file and CSV file.

    Args:
        error_data: Dictionary containing error information
        log_files: Dictionary with 'csv_file' and 'response_file' paths
        max_retries: Maximum retry attempts for file operations

    Returns:
        bool: True if logged successfully, False otherwise
    """
    success = True

    # Prepare error entry for JSONL logging
    jsonl_entry = {
        "table_name": error_data.get("table_name", "Unknown"),
        "error_message": error_data.get("error_message", "Unknown error"),
        "error_type": error_data.get("error_type", "GENERAL_ERROR"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceback": error_data.get("traceback", ""),
        "connection_id": error_data.get("connection_id"),
        "job_name": error_data.get("job_name"),
        "migration_session_id": error_data.get("migration_session_id")
    }

    # Prepare error entry for CSV logging
    csv_entry = {
        'chunk_no': error_data.get('chunk_no', 'N/A'),
        'batch_index': error_data.get('batch_index', 'N/A'),
        'connection_id': error_data.get('connection_id'),
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'operation': 'ERROR',
        'table_name': error_data.get('table_name', 'Unknown'),
        'error_message': error_data.get('error_message', 'Unknown error'),
        'error_type': error_data.get('error_type', 'GENERAL_ERROR')
    }

    # Log to JSONL response file
    response_file = log_files.get('response_file')
    if response_file:
        for attempt in range(max_retries):
            try:
                with open(response_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(jsonl_entry) + '\n')
                logger.info(f"📄 Error logged to JSONL file: {response_file}")
                break
            except Exception as e:
                logger.error(f"❌ Attempt {attempt + 1}: Failed to log to JSONL file: {e}")
                if attempt == max_retries - 1:
                    success = False

    # Log to CSV file
    csv_file = log_files.get('csv_file')
    if csv_file and csv_file.exists():
        for attempt in range(max_retries):
            try:
                error_df = pd.DataFrame([csv_entry])
                error_df.to_csv(csv_file, mode='a', header=False, index=False)
                logger.info(f"📄 Error logged to CSV file: {csv_file}")
                break
            except Exception as e:
                logger.error(f"❌ Attempt {attempt + 1}: Failed to log to CSV file: {e}")
                if attempt == max_retries - 1:
                    success = False

    return success


def create_error_log_entry(
    table_name: str,
    error_message: str,
    error_type: str = "GENERAL_ERROR",
    connection_id: Optional[int] = None,
    job_name: Optional[str] = None,
    migration_session_id: Optional[str] = None,
    chunk_index: Optional[int] = None,
    additional_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Create a standardized error log entry.

    Args:
        table_name: Name of the table or operation
        error_message: Error message
        error_type: Type of error (e.g., 'TABLE_MIGRATION_FAILED', 'CONNECTION_ERROR')
        connection_id: Connection ID
        job_name: Job name
        migration_session_id: Migration session ID
        chunk_index: Chunk index if applicable
        additional_context: Additional context information

    Returns:
        Dict containing standardized error information
    """
    error_entry = {
        "table_name": table_name,
        "error_message": str(error_message),
        "error_type": error_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceback": traceback.format_exc(),
        "connection_id": connection_id,
        "job_name": job_name,
        "migration_session_id": migration_session_id
    }

    if chunk_index is not None:
        error_entry["chunk_index"] = chunk_index

    if additional_context:
        error_entry.update(additional_context)

    return error_entry


_ERROR_MESSAGE_PREFIXES = (
    "Unexpected system error: ",
    "Unexpected error during token refresh: ",
)


def _strip_redundant_error_prefix(message: str) -> str:
    """Remove generic wrappers so UI/logs show the underlying error."""
    cleaned = str(message or "").strip()
    if not cleaned:
        return cleaned
    changed = True
    while changed:
        changed = False
        lowered = cleaned.lower()
        for prefix in _ERROR_MESSAGE_PREFIXES:
            if lowered.startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
                changed = True
                break
    return cleaned


def _pick_direct_error_message(*candidates: Optional[str]) -> str:
    """Prefer the most specific non-empty error text for user-visible logs."""
    for candidate in candidates:
        cleaned = _strip_redundant_error_prefix(str(candidate or "").strip())
        if cleaned:
            simplified = _simplify_snowflake_copy_error_for_display(cleaned)
            return simplified or cleaned
    return "Unexpected system error (no details available)."


def _simplify_snowflake_copy_error_for_display(msg: str) -> Optional[str]:
    """Lightweight Snowflake COPY error simplification for display messages."""
    if not msg:
        return None
    if "NULL result in a non-nullable column" in msg:
        try:
            col_match = re.search(r'\["([^"]+)"\s*:', msg)
        except Exception:
            col_match = None
        col_name = col_match.group(1) if col_match else None
        if col_name:
            return f"NULL value found in required column '{col_name}'. Please provide a value."
        return "NULL value found in a required column. Please provide a value."
    if "too long and would be truncated" in msg:
        try:
            col_match = re.search(r"column\s+([A-Za-z0-9_]+)\s+with error", msg, re.IGNORECASE)
        except Exception:
            col_match = None
        col_name = col_match.group(1) if col_match else None
        if col_name:
            return f"Value too long for column '{col_name}'. Please shorten the data."
        return "Value too long for one or more columns. Please shorten the data."
    return None


def classify_error(error: Exception) -> Tuple[str, str]:
    """
    Classify error into failure_type and user_friendly_message.
    Shared logic for handle_service_error and routers.
    """
    # ✅ FIX: Handle HTTPException where str(error) might be empty or generic
    if hasattr(error, "detail") and error.detail:
        error_message = str(error.detail)
    else:
        error_message = str(error)
        # Fallback to repr if str is empty (critical for empty exceptions)
        if not error_message:
            error_message = repr(error)

    raw_error_message = error_message
    error_str_low = raw_error_message.lower().strip()
    
    # Default values
    failure_type = "CRASH"
    user_friendly_message = raw_error_message

    # =========================================================================
    # 1. SPECIALIZED S3/AWS ERROR HANDLING (Case-Insensitive)
    # =========================================================================
    
    # 1a. AWS credentials errors (Invalid Key, Signature Mismatch, Security Token)
    if any(keyword in error_str_low for keyword in [
        'invalidclienttokenid', 
        'signaturedoesnotmatch', 
        'invalidaccesskeyid',
        'authfailure'
    ]):
        failure_type = "AUTH"
        user_friendly_message = "Invalid AWS credentials. Please check your access key, secret key and region."

    # 1b. S3 permissions errors (Access Denied, Forbidden)
    elif any(keyword in error_str_low for keyword in [
        'accessdenied', 
        'forbidden', 
        '403', 
        'access denied',
        'allaccessdisabled'
    ]):
        # Distinguish between generic credential issues and permission issues
        failure_type = "AUTH"
        user_friendly_message = "S3 access denied. Check AWS credentials and bucket permissions."

    # 1c. S3 bucket/object errors (Not Found)
    elif any(keyword in error_str_low for keyword in [
        'nosuchbucket', 
        'notfound', 
        '404', 
        'bucket not found',
        'nosuchkey'
    ]):
        failure_type = "CONNECTION"
        user_friendly_message = f"S3 resource not found. Check bucket name and file path."

    # 1d. DataSyncher specific S3 configuration errors
    elif "files were not found" in error_str_low:
        failure_type = "CONFIGURATION_ERROR"
        user_friendly_message = "One or more selected files were not found in S3."

    # =========================================================================
    # 1e. Snowflake Trial Expired / Billing Suspension
    # =========================================================================
    elif any(keyword in error_str_low for keyword in [
        "your free trial has ended",
        "trial has ended",
        "trial expired",
        "virtual warehouses have been suspended",
        "add billing information in the snowflake web ui",
        "billing information",
        "snowflakecomputing.com"
    ]):
        failure_type = "BILLING"
        user_friendly_message = (
            "Snowflake trial expired or billing is inactive. "
            "Please add billing in Snowflake or reactivate your warehouse."
        )

    elif "failed to parse" in error_str_low:
        failure_type = "DATA_ERROR"
        user_friendly_message = raw_error_message

    # =========================================================================
    # 2. GENERIC OAUTH / AUTHENTICATION ERRORS
    # =========================================================================
    # Prioritize Auth/Token errors (Salesforce, Dynamics, etc.)
    # STRICTER CHECK: Do not just match "token" to avoid matching "InvalidClientTokenId" or "JWT token"
    elif any(keyword in error_str_low for keyword in [
        'invalid_grant', 
        'refresh token', 
        'expired access/refresh token', 
        'invalid_session', 
        'unauthorized',
        'authentication failed'
    ]):
        failure_type = "AUTH"
        if raw_error_message.strip():
            user_friendly_message = _strip_redundant_error_prefix(raw_error_message)
        else:
            user_friendly_message = "Authentication failed. Refresh token expired. Please reconnect source."

    # 3. API_LIMIT
    elif any(keyword in error_str_low for keyword in [
        'request limit', 'rate limit', 'too many requests', '429', '503', 
        'service unavailable', 'request_limit_exceeded', 'totalrequests limit exceeded',
        'salesforcerefusedrequest'
    ]):
        failure_type = "API_LIMIT"
        user_friendly_message = "API request limit exceeded. Please try again later."
    
    # 4. CONNECTION
    elif any(keyword in error_str_low for keyword in [
        'timeout', 'connection', 'econnrefused', 'network error', 'max retries',
        'refused' # Added from snowflake router
    ]):
        failure_type = "CONNECTION"
        user_friendly_message = "Connection to source failed. Please check network connectivity."
        
    # 5. CRASH (Default) — show the underlying error directly in UI/logs.
    else:
        failure_type = "CRASH"
        user_friendly_message = _pick_direct_error_message(raw_error_message)

    return failure_type, user_friendly_message


def handle_update_schema_listing_error(
    error: Exception,
    data,
    *,
    service_name: str,
    total_tables: int = 0,
    is_final_attempt: bool = True,
) -> None:
    """Log terminal Update Schema failures for file-source listing-tables routes."""
    handle_service_error(
        error=error,
        context={
            "job_name": getattr(data, "job_name", None),
            "log_id": getattr(data, "log_id", None),
            "cmp_id": getattr(data, "cmp_id", None),
            "connection_id": getattr(data, "connection_id", None),
            "user_id": getattr(data, "user_id", None),
            "session": getattr(data, "migration_session_id", None),
            "migration_session_id": getattr(data, "migration_session_id", None),
            "operation": "UPDATE_SCHEMA",
            "is_terminal_failure": True,
            "total_tables": total_tables,
            "successful_tables": 0,
            "total_rows": 0,
            "service_name": service_name,
        },
        is_final_attempt=is_final_attempt,
    )


def handle_service_error(
    error: Exception,
    context: Dict[str, Any],
    log_files: Optional[Dict[str, Path]] = None,
    send_email: bool = False,
    is_final_attempt: bool = True  # ✅ ADD: Email deduplication flag
) -> Dict[str, Any]:
    """
    Comprehensive error handling for service operations.

    Args:
        error: The exception that occurred
        context: Context dictionary with operation details
        log_files: Optional file paths for logging
        send_email: Whether to send error notification email
        is_final_attempt: Whether this is the final retry attempt (for email deduplication)

    Returns:
        Dict with error handling results
    """
    def _extract_root_error_message(err: Exception) -> str:
        # Keep full chain context for exact troubleshooting details.
        parts = []
        current = err
        seen = set()
        while current and id(current) not in seen:
            seen.add(id(current))
            if hasattr(current, "detail") and getattr(current, "detail", None):
                msg = str(current.detail).strip()
            else:
                msg = str(current).strip() or repr(current)
            if msg:
                parts.append(msg)
            current = current.__cause__ or current.__context__
        if not parts:
            return str(err).strip() or repr(err)
        # Deduplicate while preserving order.
        deduped = []
        seen_msg = set()
        for p in parts:
            key = p.strip()
            if key and key not in seen_msg:
                seen_msg.add(key)
                deduped.append(key)
        return " | Caused by: ".join(deduped)

    # Use centralized classification
    failure_type, user_friendly_message = classify_error(error)
    
    # ✅ FIX: Use robust way to extract raw message (str(HTTPException) is empty)
    if hasattr(error, "detail") and error.detail:
        raw_error_message = str(error.detail)
    else:
        raw_error_message = str(error) if str(error).strip() else repr(error)
    root_error_message = _extract_root_error_message(error)

    # Show the underlying error directly in UI/logs (no generic wrapper).
    error_message = _pick_direct_error_message(
        root_error_message,
        raw_error_message,
        user_friendly_message,
    )

    table_name = context.get('table_name', 'Unknown')
    service_name = context.get('service_name', 'Unknown Service')
    job_name = context.get('job_name')
    migration_record_id = context.get('migration_record_id')

    # ✅ Check if this is a terminal migration failure (high-level job failure)
    is_terminal_failure = (
        table_name == "Migration Process" or 
        context.get('is_terminal_failure', False)
    )

    if is_terminal_failure:
        logger.warning(f"🚨 TERMINAL FAILURE DETECTED in handle_service_error for job: {job_name}")

    #  # Look up migration_record_id if job_name provided but ID missing
    if job_name and not migration_record_id:
        from utils.database_utils import postgres_connection, get_migration_record_id
        try:
            conn = postgres_connection()
            if conn:
                cursor = conn.cursor()
                migration_record_id = get_migration_record_id(
                    cursor,
                    job_name=job_name,
                    table_name=table_name if table_name != 'Unknown' else None,
                    connection_id=context.get('connection_id')
                )
                cursor.close()
                conn.close()
        except Exception as e:
            logger.error(f"⚠️ Failed to look up migration_record_id in handle_service_error: {e}")

    # ✅ If this is a terminal failure, log it using the specialized function.
    # Retry behavior: write terminal/job-level log only on final attempt to avoid
    # duplicate F entries while Celery retries are still in progress.
    if is_terminal_failure and job_name and is_final_attempt:
        # 🛡️ Idempotency check: Prevent logging terminal failure multiple times for the same context
        if not context.get('already_logged_terminal_failure'):
            log_migration_failure(
                job_name=job_name,
                error_message=error_message,
                session=context.get('session'),
                cmp_id=context.get('cmp_id'),
                connection_id=context.get('connection_id'),
                user_id=context.get('user_id'),
                log_id=context.get('log_id'),
                failure_type=failure_type,            # ✅ Pass classified type
                execution_time=context.get('execution_time', 0.0),
                total_rows=context.get('total_rows', 0),
                successful_tables=context.get('successful_tables', 0),
                total_tables=context.get('total_tables', 0),
                operation=context.get('operation', 'MIGRATION'),  # ✅ Pass operation
                celery_child_mode=bool(context.get('celery_child_mode', False)),
            )
            # Mark as logged to prevent duplicates in retry loops
            context['already_logged_terminal_failure'] = True
        else:
            logger.info(f"⏭️ Skipping duplicate terminal failure log for job: {job_name}")

            # 🛡️ Defensive logging for missing log_id (Risk 4)
            if not context.get('log_id'):
                logger.error(f"CRITICAL: Missing log_id for terminal failure in job {job_name} - duplicate logs may occur")

    if is_terminal_failure and job_name and not is_final_attempt:
        logger.info(
            f"⏭️ Skipping terminal/job-level log write for non-final attempt "
            f"(job_name={job_name})"
        )

    # Log to log (table-level):
    # - Only non-terminal errors go through log_error_to_tool_log
    # - Write only on final attempt to avoid duplicate failure rows during retry
    if not is_terminal_failure:
        if is_final_attempt:
            tool_log_success = log_error_to_tool_log(
                cmp_id=context.get('cmp_id'),
                connection_id=context.get('connection_id'),
                user_id=context.get('user_id'),
                table_name=table_name,
                error_message=error_message,
                session=context.get('session'),
                job_name=job_name,
                migration_record_id=migration_record_id
            )
        else:
            logger.info(
                f"⏭️ Skipping table-level log write for non-final attempt "
                f"(table={table_name}, job_name={job_name})"
            )
            tool_log_success = True
    else:
        tool_log_success = True

    # ✅ Also log to error_log_entry for detailed debugging/monitoring
    # IMPORTANT: Use raw_error_message here for technical details!
    error_log_success = False
    try:
        error_log_detail = root_error_message or raw_error_message or error_message
        error_log_success = insert_error_log_entry(
            cmp_id=context.get('cmp_id'),
            connection_id=context.get('connection_id'),
            message=f"[{failure_type}] {error_log_detail}",
            function_name=f"{service_name}.{table_name}"
        )
    except Exception as err_log_err:
        logger.warning(f"⚠️ Failed to write to error_log_entry (non-fatal): {err_log_err}")

    # Log to files if provided
    file_log_success = False
    if log_files:
        error_data = create_error_log_entry(
            table_name=table_name,
            error_message=raw_error_message,      # ✅ Log RAW error to files
            error_type=failure_type,              # ✅ Use classified type
            connection_id=context.get('connection_id'),
            job_name=context.get('job_name'),
            migration_session_id=context.get('migration_session_id'),
            additional_context=context
        )
        file_log_success = log_error_to_files(error_data, log_files)

    # Send email if requested AND this is the final attempt
    email_success = False
    if send_email:
        if is_final_attempt:
            try:
                from utils.email_utils import send_error_notification
                email_success = send_error_notification(
                    table_name=table_name,
                    error_message=f"Error in {service_name}: {raw_error_message}",
                    connection_name=context.get('connection_name', 'Unknown'),
                    service_name=service_name,
                    user_id=context.get('user_id'),
                    company_id=context.get('cmp_id'),
                    additional_context=context
                )
                logger.info(f"📧 Error notification email sent (final attempt)")
            except Exception as email_error:
                logger.error(f"⚠️ Failed to send error notification email: {email_error}")
        else:
            logger.info(f"⏭️ SKIPPING error notification email (not final attempt)")

    return {
        "tool_log_logged": tool_log_success,
        "log_logged": tool_log_success,  # Compatibility key for older callers
        "files_logged": file_log_success,
        "error_log_logged": error_log_success,
        "email_sent": email_success,
        "error_message": user_friendly_message
    }


def should_include_operation_counts(load_method: Any = None) -> bool:
    """Match S3/file-source completion: show I/U/D for PK-based loads only."""
    method = str(load_method or "upsert_file_row").strip()
    return method not in ("upsert_file_row", "append_file_modified")


def should_use_failures_in_completion(
    connector_type: Optional[str] = None,
    function_path: Optional[str] = None,
) -> bool:
    """RETL (Snowflake→Salesforce destination) uses Failures; file sources use Deletes."""
    normalized = str(connector_type or "").lower()
    if normalized in ("snowflake_to_salesforce", "snowflake_to_salesforce_sandbox"):
        return True
    path = str(function_path or "")
    return path in (
        "/Salesforce-Snowflake-migration-yield",
        "/SalesforceSandbox-Snowflake-migration-yield",
    )


def log_migration_completion(
    execution_time: float,
    total_rows: int,
    cmp_id: Optional[int],
    connection_id: Optional[int],
    user_id: Optional[int],
    session: Optional[str],
    inserts: Optional[int] = None,
    updates: Optional[int] = None,
    deletes: Optional[int] = None,
    failures: Optional[int] = None,
    total_tables: Optional[int] = None,
    successful_tables: Optional[int] = None,
    failed_tables: Optional[int] = None,
    skipped_tables: Optional[int] = None,  # Counted as failed for status calculation
    job_name: Optional[str] = None,
    migration_record_id: Optional[int] = None,
    operation_type: Optional[str] = None,  # 'drop_and_reload' or 'single_table_refresh' for generic messages
    trigger_type: Optional[str] = "manual",  # Added trigger_type parameter with default
    include_operation_counts: bool = True,  # New parameter to control whether to include operation counts
    log_id: Optional[int] = None,  # ✅ Add log_id for stable updates
    failure_reason: Optional[str] = None,
    status_override: Optional[str] = None,
    suppress_reason_in_completion: bool = False,
    celery_child_mode: bool = False,
) -> bool:
    """
    Unified function to log migration completion messages to log.
    This is the single source of truth for all migration completion messages.
    
    Standard format (same rhythm as update-schema completion — one sentence per clause):
    - Ordinary source→Snowflake (Salesforce, Dynamics, etc.): time, total rows, table counts only
    - RETL / Snowflake→Salesforce: "... Inserts: A, Updates: B, Failures: C. Tables: ..."
    - File sources (Drive/SFTP/S3/ADLS→Snowflake, PK upsert): "... Inserts: A, Updates: B, Deletes: C. Tables: ..."
    
    Args:
        execution_time: Execution time in seconds (required)
        total_rows: Total rows migrated (required)
        cmp_id: Company ID (required)
        connection_id: Connection ID (required)
        user_id: User ID (required)
        session: Session identifier (required)
        inserts: Number of inserts (optional)
        updates: Number of updates (optional)
        deletes: Number of deletes (optional)
        failures: Number of failed writes (optional; shown as Failures instead of Deletes)
        total_tables: Total number of tables (optional)
        successful_tables: Number of successful tables (optional)
        failed_tables: Number of failed tables (optional)
        skipped_tables: Number of skipped tables (optional, counted as failed for status calculation)
        job_name: Job name for the migration (optional)
        migration_record_id: Migration record ID (optional, None for high-level logs)
        operation_type: Type of operation ('drop_and_reload' or 'single_table_refresh') for generic messages
        trigger_type: Trigger type (default: "manual")
        include_operation_counts: Whether to include operation counts (Inserts, Updates, Deletes) in the message (default: True)
    
    Returns:
        bool: True if logged successfully, False otherwise
    """
    try:
        # Build standardized message parts
        # Determine base message based on failures
        final_failed = (failed_tables or 0) + (skipped_tables or 0)
        final_successful = successful_tables or (total_tables - final_failed if total_tables else 0)
        final_total = total_tables or (final_successful + final_failed)
        
        override = str(status_override or "").strip().upper()

        # Choose appropriate base message
        if override == "P":
            base_message = "Migration completed with warnings"
        elif override == "F":
            base_message = "Migration failed"
        elif override == "S":
            base_message = "Migration completed successfully"
        elif final_total == 0 or final_failed == 0:
            base_message = "Migration completed successfully"
        elif final_failed == final_total:
            base_message = "Migration failed"
        else:
            base_message = "Migration completed"
        
        # Period-separated sentences (matches "Update schema completed successfully in … . Upserted …" style).
        sentences: List[str] = [
            f"{base_message} in {execution_time:.2f} seconds.",
            f"Total rows: {total_rows:,}.",
        ]

        # Add operation counts if provided and include_operation_counts is True
        operation_parts = []
        if include_operation_counts and inserts is not None:
            operation_parts.append(f"Inserts: {inserts:,}")
        if include_operation_counts and updates is not None:
            operation_parts.append(f"Updates: {updates:,}")
        if include_operation_counts and failures is not None:
            operation_parts.append(f"Failures: {failures:,}")
        elif include_operation_counts and deletes is not None:
            operation_parts.append(f"Deletes: {deletes:,}")

        if operation_parts:
            sentences.append(", ".join(operation_parts) + ".")

        # Add table count — always include for clarity
        if total_tables is not None and final_successful is not None:
            table_info = f"Tables: {final_successful}/{final_total} successful"
            if final_failed > 0 and final_failed == final_total:
                table_info += " (all tables failed)"
            elif final_failed > 0:
                table_info += f", {final_failed} failed"
            sentences.append(table_info + ".")
        elif failed_tables and failed_tables > 0:
            sentences.append(f"{failed_tables} table(s) failed.")

        # Include concise root-cause reason when everything failed, unless suppressed by caller.
        if (
            not suppress_reason_in_completion
            and failure_reason
            and (final_total > 0 and final_failed == final_total)
        ):
            clean_reason = str(failure_reason).strip()
            if clean_reason:
                max_len = 300
                if clean_reason.startswith("Skipping file '"):
                    max_len = 1000
                if len(clean_reason) > max_len:
                    clean_reason = clean_reason[:max_len].rstrip() + "..."
                sentences.append(f"Reason: {clean_reason}.")

        message = " ".join(sentences)
        
        # Calculate final status using exact requirements:
        # S (Success) → All tables completed successfully
        # P (Partial Complete) → At least one table failed (or skipped), but not all failed
        # F (Failed/Error) → All tables failed
        
        # Note: final_failed, final_successful, final_total already calculated above for base message
        
        # Status calculation logic
        if override in {"S", "P", "F"}:
            status = override
        elif final_total == 0:
            status = "S"  # Default to success if no tables
        elif final_failed == 0:
            status = "S"  # All successful
        elif final_failed == final_total:
            status = "F"  # All failed
        else:
            status = "P"  # Mixed results (partial success)
        
        if (
            job_name
            and migration_record_id is None
            and status in _TERMINAL_JOB_LEVEL_STATUSES
            and not job_level_terminal_write_allowed(
                job_name, status, celery_child_mode=celery_child_mode
            )
        ):
            return False

        # If this is a job-level log (job_name provided and migration_record_id is None),
        # try to update the existing "Migration in progress" entry instead of creating a new one
        update_successful = False
        if job_name and migration_record_id is None:
            logger.info(f"🔍 DEBUG: Attempting to UPDATE existing log for job_name: {job_name}, session: {session}")
            update_successful = update_migration_status_log(
                job_name=job_name,
                message=message,
                status=status,
                session=session,
                log_id=log_id,
                celery_child_mode=celery_child_mode,
            )
            
            if update_successful:
                logger.info(f"✅ Migration completion logged (updated existing entry): {message}")
            else:
                # Retry once without session filter to avoid leaving stale
                # "Migration in progress" rows when session value drifts.
                logger.info(
                    f"⚠️ Session-scoped update missed for job_name: {job_name}; retrying update without session filter"
                )
                update_successful = update_migration_status_log(
                    job_name=job_name,
                    message=message,
                    status=status,
                    session=None,
                    log_id=log_id,
                    celery_child_mode=celery_child_mode,
                )
                if update_successful:
                    logger.info(f"✅ Migration completion logged (fallback update by job_name): {message}")
                else:
                    logger.warning(
                        "No existing job-level log to update; skipping insert fallback for job_name=%s",
                        job_name,
                    )
                    return False
        
        if not update_successful and log_id is None:
            if job_name and migration_record_id is None:
                logger.warning(
                    "Skipping job-level completion insert fallback for job_name=%s",
                    job_name,
                )
                return False

            # Table-level logs only (migration_record_id set).
            logger.info(
                "Calling insert_tool_log with job_name=%s migration_record_id=%s",
                job_name,
                migration_record_id,
            )
            insert_tool_log(
                cmp_id=cmp_id,
                connection_id=connection_id,
                usr_id=user_id,
                table_name="Migration",
                status=status,
                message=message,
                session=session,
                job_name=job_name,
                migration_record_id=migration_record_id,
                celery_child_mode=celery_child_mode,
            )
            logger.info(f"✅ Migration completion logged (new entry): {message}")
        
        # Trigger WebSocket update for activity logs when a write actually happened.
        if update_successful or (migration_record_id is not None and log_id is None):
            try:
                from utils.websocket_utils import trigger_connection_activity_log_update
                trigger_connection_activity_log_update(connection_id)
            except Exception as ws_err:
                logger.warning(
                    "Error triggering activity log WebSocket update for connection %s: %s",
                    connection_id,
                    ws_err,
                )
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to log migration completion: {e}")
        return False


def log_migration_failure(
    job_name: str,
    error_message: str,
    session: Optional[str] = None,
    cmp_id: Optional[int] = None,
    connection_id: Optional[int] = None,
    user_id: Optional[int] = None,
    failure_type: Optional[str] = "CRASH",  # CRASH, CONNECTION, AUTH, API_LIMIT, etc.
    log_id: Optional[int] = None,  # ✅ Add log_id for stable updates
    execution_time: float = 0.0,
    total_rows: int = 0,
    successful_tables: int = 0,
    total_tables: int = 0,
    operation: str = "MIGRATION",  # ✅ Add operation parameter
    celery_child_mode: bool = False,
) -> bool:
    """
    Update existing job-level migration status log with failure message.
    """
    try:
        if not job_name:
            logger.warning("⚠️ log_migration_failure: job_name is required")
            return False
        
        # Safe defaults for values
        time_val = max(0.0, execution_time or 0.0)
        rows_val = max(0, total_rows or 0)
        success_val = max(0, successful_tables or 0)
        total_val = max(0, total_tables or 0)
        
        # 🛡️ Handle empty error message (Risk 2)
        reason = error_message
        if not reason or not reason.strip():
            reason = "Unexpected system error (no details available)."

        # Keep job-level failure messages concise.
        # Table-level logs already carry the full error details.
        try:
            if reason:
                # If detailed row values are present, keep only the summary line.
                if "Row Values:" in reason:
                    reason = reason.split("Row Values:", 1)[0].strip()
                # If multi-line, keep just the first line.
                if "\n" in reason:
                    reason = reason.splitlines()[0].strip()
        except Exception:
            pass
            
        f_type = failure_type or "CRASH"

        # Determine prefix based on operation
        prefix = "Migration failed"
        if operation == "UPDATE_SCHEMA":
            prefix = "Update schema failed"

        # ✅ Improved formatting:
        # 1. Early Failure (Risk 2): If failure happens before any table succeeds and time is negligible
        if successful_tables == 0 and time_val < 1.0 and total_tables > 0:
             # For MIGRATION, we use a more descriptive early failure message
             if operation == "MIGRATION":
                 failure_message = (
                    f"{prefix} before processing any tables. "
                    f"{reason} "
                    f"({success_val}/{total_val} tables processed)"
                )
             else:
                 # For UPDATE_SCHEMA, we keep it concise as per user request
                 failure_message = f"{prefix}. {reason}"
        else:
            # 2. Standard Failure: Hide time if it's negligible (e.g. immediate auth failure)
            time_str = ""
            if time_val >= 0.01:
                time_str = f" in {time_val:.2f} seconds"

            # For UPDATE_SCHEMA, usually counts are not needed if successful_tables is 0
            stats_str = ""
            if (operation != "UPDATE_SCHEMA" and total_tables > 0) or (operation == "UPDATE_SCHEMA" and successful_tables > 0):
                stats_str = f" ({success_val}/{total_val} tables processed)"

            failure_message = (
                f"{prefix}{time_str}. "
                f"{reason}{stats_str}"
            )
        
        # Append structured tag only if not already in message (to keep it clean but present)
        # Note: The caller (handle_service_error) usually sends classified messages now,
        # but we add the tag at the end just in case for visibility, or we can rely on standard format.
        # User asked for: "Migration failed... [API_LIMIT] Salesforce..." 
        # But also liked: "Migration failed. Salesforce... (0/8 tables)"
        # Let's add the tag if it's NOT 'CRASH' to be helpful, or if the reason doesn't start with it.
        # Actually, let's keep it simple and clean as per "User sees" example.
        
        # ── Also log to error_log_entry for detailed debugging ──
        try:
            insert_error_log_entry(
                cmp_id=cmp_id,
                connection_id=connection_id,
                message=f"[{failure_type}] {error_message}",
                function_name=f"log_migration_failure({job_name})"
            )
        except Exception as err_log_err:
            logger.warning(f"⚠️ Failed to write to error_log_entry (non-fatal): {err_log_err}")
        
        if not job_level_terminal_write_allowed(job_name, "F", celery_child_mode=celery_child_mode):
            return False

        # ── Try to update existing job-level log entry ──
        update_success = update_migration_status_log(
            job_name=job_name,
            message=failure_message,
            status="F",
            session=session,
            log_id=log_id,
            celery_child_mode=celery_child_mode,
        )
        
        if update_success:
            logger.info(f"✅ Migration failure logged (updated existing entry): {failure_message}")
            
            try:
                from utils.websocket_utils import trigger_connection_activity_log_update
                trigger_connection_activity_log_update(connection_id)
            except Exception:
                pass
            
            return True
        elif log_id is None:
            # ── Crash fallback: no existing entry → create minimal failure entry ──
            logger.warning(f"⚠️ No existing job-level log found for failure, creating new entry for job_name: {job_name}")
            
            if cmp_id and connection_id and user_id:
                try:
                    insert_tool_log(
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        usr_id=user_id,
                        table_name="Migration",
                        status="F",
                        message=failure_message,
                        session=session,
                        job_name=job_name,
                        migration_record_id=None,
                        celery_child_mode=celery_child_mode,
                    )
                    logger.info(f"✅ Migration failure logged (new entry): {failure_message}")
                    
                    try:
                        from utils.websocket_utils import trigger_connection_activity_log_update
                        trigger_connection_activity_log_update(connection_id)
                    except Exception:
                        pass
                    
                    return True
                except Exception as e:
                    logger.error(f"❌ Failed to insert migration failure log: {e}")
                    return False
            else:
                logger.error(f"❌ Cannot insert migration failure log: missing required context (cmp_id, connection_id, user_id)")
                return False
    
    except Exception as outer_err:
        # Outermost safety net — never crash the migration
        logger.error(f"❌ log_migration_failure outer error (swallowed): {outer_err}")
        return False


def insert_error_log_entry(
    cmp_id: Optional[int],
    connection_id: Optional[int],
    message: str,
    function_name: str
) -> bool:
    """
    Insert a detailed error record into the error_log_entry table.
    
    This is separate from the migration status log (log table).
    Used for debugging, monitoring, and alerting — never modifies
    migration status.
    
    Fully exception-safe: failures are swallowed to avoid crashing the caller.
    
    Args:
        cmp_id: Company ID
        connection_id: Connection ID
        message: Detailed error message (may include structured tags like [API_LIMIT])
        function_name: Name of the function where the error occurred
    
    Returns:
        bool: True if inserted successfully, False otherwise
    """
    try:
        # error_log_entry.cmp_id is NOT NULL in DB. Skip insert when absent.
        if cmp_id is None:
            logger.warning(
                "Skipping insert_error_log_entry due to missing cmp_id | connection_id=%s | function_name=%s",
                connection_id,
                function_name,
            )
            return False

        from utils.database_utils import postgres_connection
        
        conn = postgres_connection()
        if conn is None:
            logger.error("❌ insert_error_log_entry: Failed to connect to database")
            return False
        
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO error_log_entry (cmp_id, connection_id, message, function_name, created_on)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                cmp_id,
                connection_id,
                message[:2000],  # Truncate to prevent overflow
                function_name[:255],
                datetime.now(timezone.utc)
            ))
            conn.commit()
            cursor.close()
            conn.close()
            logger.info(f"✅ Error logged to error_log_entry: {function_name}")
            return True
        except Exception as e:
            try:
                conn.rollback()
                cursor.close()
                conn.close()
            except Exception:
                pass
            logger.error(f"❌ Failed to insert into error_log_entry: {e}")
            return False
    
    except Exception as outer_err:
        logger.error(f"❌ insert_error_log_entry outer error (swallowed): {outer_err}")
        return False

def format_exception_message(exc: Exception) -> str:
    """Format raw Python/gRPC exceptions into clear, human-readable error messages."""
    if exc is None:
        return ""
    err_str = str(exc)
    
    # Clean up gRPC _InactiveRpcError / RpcError messages
    if "InactiveRpcError" in type(exc).__name__ or "RpcError" in type(exc).__name__ or "StatusCode.UNAVAILABLE" in err_str or "Agent for customer" in err_str:
        details = getattr(exc, "details", lambda: "")() or err_str
        match = re.search(r"Agent for customer ([\w-]+)", details + " " + err_str)
        agent_id = match.group(1) if match else None
        if agent_id:
            return f"Proxy Agent '{agent_id}' is offline or disconnected. Please ensure the agent process is running on the client machine."
        return "Proxy Agent is offline or disconnected. Please ensure the agent process is running on the client machine."
        
    return err_str if err_str and err_str.strip() else repr(exc)