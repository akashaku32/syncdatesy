import json
import ast
import requests
import csv
import io
import math
import traceback
from decimal import Decimal
from datetime import datetime, date, timezone
from typing import Dict, List, Any, Optional
import asyncio
import logging
from sqlalchemy import text
import pandas as pd
import os
import time
import psycopg2
from psycopg2 import OperationalError
from utils.s3_utils import sanitize_identifier
# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _normalize_s3_table_name(name: Any) -> str:
    value = str(name or "").strip()
    return sanitize_identifier(value) if value else ""


def _normalize_s3_single_map(single_map: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(single_map, dict):
        return {}
    normalized = {}
    for file_key, table_name in single_map.items():
        normalized[str(file_key)] = _normalize_s3_table_name(table_name) or _normalize_s3_table_name(file_key)
    return normalized


def get_migration_record_id(
    cursor,
    job_name: Optional[str],
    table_name: Optional[str],
    connection_id: Optional[int],
    allow_fallback: bool = True
) -> Optional[int]:
    """
    Get migration_record_id from MigrationRecord table with robust fallback logic.
    
    Args:
        cursor: Database cursor
        job_name: Job name to search for
        table_name: Table name to search for (can be None for job-level logs)
        connection_id: Connection ID to search for
        allow_fallback: If True, use fallback strategies when exact match fails
    
    Returns:
        migration_record_id (int) if found, None otherwise
    """
    if not job_name:
        return None
    
    migration_record_id = None
    
    # Strategy 1: Exact match (job_name + table_name + connection_id)
    if table_name and connection_id:
        cursor.execute('''
            SELECT id FROM migration_record 
            WHERE job_name = %s AND table_name = %s AND connection_id = %s
        ''', (job_name, table_name, connection_id))
        result = cursor.fetchone()
        if result:
            migration_record_id = result[0]
            print(f"✅ Found MigrationRecord (exact match): id={migration_record_id}, job_name={job_name}, table_name={table_name}")
            return migration_record_id
    
    if not allow_fallback:
        return None
    
    # Strategy 2: Case-insensitive match (job_name might have case differences)
    if table_name and connection_id:
        cursor.execute('''
            SELECT id FROM migration_record 
            WHERE UPPER(job_name) = UPPER(%s) 
              AND UPPER(table_name) = UPPER(%s) 
              AND connection_id = %s
        ''', (job_name, table_name, connection_id))
        result = cursor.fetchone()
        if result:
            migration_record_id = result[0]
            print(f"✅ Found MigrationRecord (case-insensitive): id={migration_record_id}")
            return migration_record_id
    
    # Strategy 3: Find by job_name + connection_id (any table for this job)
    if connection_id:
        cursor.execute('''
            SELECT id, table_name FROM migration_record 
            WHERE job_name = %s AND connection_id = %s
            ORDER BY id DESC
        ''', (job_name, connection_id))
        results = cursor.fetchall()
        if results:
            if table_name:
                # Try to find matching table_name (case-insensitive)
                for rec_id, rec_table in results:
                    if rec_table and table_name.upper() == rec_table.upper():
                        migration_record_id = rec_id
                        print(f"✅ Found MigrationRecord (job match, table match): id={migration_record_id}")
                        return migration_record_id
            # If no table match or table_name not provided, use first result
            migration_record_id = results[0][0]
            print(f"✅ Found MigrationRecord (job match, any table): id={migration_record_id}")
            return migration_record_id
    
    # Strategy 4: Case-insensitive job_name search
    if connection_id:
        cursor.execute('''
            SELECT id, table_name FROM migration_record 
            WHERE UPPER(job_name) = UPPER(%s) AND connection_id = %s
            ORDER BY id DESC
        ''', (job_name, connection_id))
        results = cursor.fetchall()
        if results:
            if table_name:
                for rec_id, rec_table in results:
                    if rec_table and table_name.upper() == rec_table.upper():
                        migration_record_id = rec_id
                        print(f"✅ Found MigrationRecord (case-insensitive job, table match): id={migration_record_id}")
                        return migration_record_id
            migration_record_id = results[0][0]
            print(f"✅ Found MigrationRecord (case-insensitive job): id={migration_record_id}")
            return migration_record_id
    
    # Strategy 5: Fallback - find by connection_id + table_name (most recent)
    if table_name and connection_id:
        cursor.execute('''
            SELECT id, job_name FROM migration_record 
            WHERE connection_id = %s AND table_name = %s
            ORDER BY id DESC LIMIT 1
        ''', (connection_id, table_name))
        fallback_result = cursor.fetchone()
        if fallback_result:
            migration_record_id = fallback_result[0]
            print(f"⚠️ Found MigrationRecord (fallback by table): id={migration_record_id}, job_name={fallback_result[1]}")
            return migration_record_id
    
    # Strategy 6: Last resort - find most recent MigrationRecord for this connection
    if connection_id:
        cursor.execute('''
            SELECT id, job_name, table_name FROM migration_record 
            WHERE connection_id = %s
            ORDER BY id DESC LIMIT 1
        ''', (connection_id,))
        last_resort = cursor.fetchone()
        if last_resort:
            migration_record_id = last_resort[0]
            print(f"⚠️ Found MigrationRecord (last resort): id={migration_record_id}, job_name={last_resort[1]}, table_name={last_resort[2]}")
            return migration_record_id
    
    # Not found
    print(f"❌ MigrationRecord not found: job_name={job_name}, table_name={table_name}, connection_id={connection_id}")
    return None


def postgres_connection():
    """
    Create a PostgreSQL connection with retry logic
    """
    from datasyncher_connections.postgres import create_postgres_connection

    print("🔌 Attempting database connection via datasyncher_connections...")
    connection = create_postgres_connection(retries=3, retry_delay=2, validate=True)
    print("✅ Database connection established")
    return connection

def update_table_status_snowflake(connection_id, table_name, status, job_name, migration_session_id, sync_end_time=None):
    """
    Updates the status and optionally the last synced time for the table.
    Also sets start_time when status changes to 'in_progress'.
    """
    print("\n=== update_table_status_snowflake CALLED ===")
    print("connection_id:", connection_id)
    print("table_name:", repr(table_name))
    print("status:", repr(status))
    print("job_name:", repr(job_name))
    print("migration_session_id:", repr(migration_session_id))
    print("sync_end_time:", sync_end_time)

    conn = None
    cursor = None
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            # Create a fresh connection for each update
            conn = postgres_connection()
            print("Postgres connection object:", conn)
            cursor = conn.cursor()

            # Set a statement timeout to prevent hanging
            cursor.execute("SET statement_timeout = '30s'")

            # Update source_table_selection with status and last_synced
            if sync_end_time:
                sql_update_source_table = """
                UPDATE source_table_selection
                SET status = %s, last_synced = %s
                WHERE connection_id = %s AND "table" = %s;
                """
                cursor.execute(sql_update_source_table, (status, sync_end_time, connection_id, table_name))
                print("source_table_selection rows updated (with sync_end_time):", cursor.rowcount)
            else:
                sql_update_source_table = """
                UPDATE source_table_selection
                SET status = %s
                WHERE connection_id = %s AND "table" = %s;
                """
                cursor.execute(sql_update_source_table, (status, connection_id, table_name))
                print("source_table_selection rows updated:", cursor.rowcount)

            # Update migration_record - set start_time when status is 'in_progress'
            # NOTE: start_time will be updated later in data_transfer_logs with actual transfer start time
            if status == 'in_progress':
                print("STATUS == in_progress → start_time SHOULD be set (will be updated later with actual transfer time)")
                sql_update_migration_record = """
                UPDATE migration_record
                SET status = %s, updated_at = NOW()
                WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
                """
            else:
                print("STATUS != in_progress → start_time WILL NOT be set")
                sql_update_migration_record = """
                UPDATE migration_record
                SET status = %s, updated_at = NOW()
                WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
                """
            cursor.execute(sql_update_migration_record, (status, job_name, migration_session_id, table_name))

            # Commit the changes
            conn.commit()
            print(f"\u2705 Successfully updated status for table '{table_name}' to '{status}'")

            # ✅ Trigger WebSocket status update for real-time UI tracking
            try:
                from utils.websocket_utils import (
                    trigger_ws_status_update,
                    trigger_connection_ws_status_update,
                    trigger_company_connections_status_update,
                )
                trigger_ws_status_update(migration_session_id)
                trigger_connection_ws_status_update(connection_id)
                if status == 'in_progress':
                    cursor.execute(
                        "SELECT cmp_id FROM connection WHERE connection_id = %s",
                        (connection_id,)
                    )
                    cmp_row = cursor.fetchone()
                    if cmp_row and cmp_row[0]:
                        trigger_company_connections_status_update(cmp_row[0])
            except Exception as ws_err:
                print(f"⚠️ WebSocket trigger failed: {ws_err}")

            break  # Success, exit the retry loop

        except psycopg2.OperationalError as e:
            retry_count += 1
            print(f"\u26a0\ufe0f Database connection error (attempt {retry_count}/{max_retries}): {e}")
            if retry_count >= max_retries:
                print(f"\u274c Failed to update status after {max_retries} attempts")
                raise
            time.sleep(2)  # Wait before retry

        except Exception as e:
            print(f"\u274c Error updating table status: {e}")
            if conn:
                conn.rollback()
            raise

        finally:
            # Always close connections
            if cursor:
                cursor.close()
            if conn:
                conn.close()


def check_table_completed_status(
    connection_id: int,
    table_name: str,
    migration_session_id: str,
    job_name: str,
) -> Optional[Dict[str, Any]]:
    """Checks if a table has been successfully migrated in a previous run of this session.

    Queries the `source_table_selection` table to check if the status is 'completed'.
    If it is completed, it looks up the associated `migration_record` and the last recorded
    `total_rec` row count in `data_transfer`. It also attempts to retrieve the column count
    from the corresponding log message.

    Args:
        connection_id: The identifier of the database connection.
        table_name: The name of the table being checked.
        migration_session_id: The session identifier of the migration job.
        job_name: The name of the migration job.

    Returns:
        A dictionary containing historical success metadata if the table was completed,
        or None if the table is not completed or its records could not be found.
        The returned dictionary has the keys:
            - "status" (str): "success"
            - "rows" (int): The number of rows successfully exported last time.
            - "columns" (int): The number of columns exported.
    """
    import re
    logger.info(
        "Entering check_table_completed_status | table=%s connection_id=%s migration_session_id=%s job_name=%s",
        table_name,
        connection_id,
        migration_session_id,
        job_name,
    )

    conn = None
    cursor = None
    try:
        # Create a fresh database connection
        conn = postgres_connection()
        cursor = conn.cursor()

        # Step 1: Check status in source_table_selection
        cursor.execute(
            'SELECT status FROM source_table_selection WHERE connection_id = %s AND "table" = %s',
            (connection_id, table_name)
        )
        row = cursor.fetchone()
        if not row:
            logger.info("No source_table_selection entry found for table: %s", table_name)
            return None

        status = row[0]
        logger.info("Table %s current status in database: %s", table_name, status)
        if status != 'completed':
            return None

        # Step 2: Get migration_record_id using fallback logic if needed
        migration_record_id = get_migration_record_id(
            cursor=cursor,
            job_name=job_name,
            table_name=table_name,
            connection_id=connection_id,
            allow_fallback=True
        )

        if not migration_record_id:
            logger.warning(
                "Table status is 'completed' but no migration_record found for table: %s",
                table_name
            )
            # Return fallback success result with zero counts if metadata is missing
            return {
                "status": "success",
                "rows": 0,
                "columns": 0,
            }

        # Step 3: Fetch total rows from data_transfer
        cursor.execute(
            'SELECT total_rec FROM data_transfer WHERE migration_record_id = %s AND table_name = %s ORDER BY end_time DESC LIMIT 1',
            (migration_record_id, table_name)
        )
        dt_row = cursor.fetchone()
        rows_count = dt_row[0] if dt_row else 0

        # Step 4: Fetch log message to extract column count if possible
        cursor.execute(
            'SELECT message FROM log WHERE migration_record_id = %s ORDER BY time DESC LIMIT 1',
            (migration_record_id,)
        )
        log_row = cursor.fetchone()
        columns_count = 0
        if log_row and log_row[0]:
            message = log_row[0]
            # Try Style 1: "... containing data with X rows and Y columns."
            match = re.search(r"containing data with \d+ rows and (\d+) columns", message)
            if match:
                columns_count = int(match.group(1))
            else:
                # Try Style 2: "... Columns - Y."
                match = re.search(r"Columns\s*-\s*(\d+)", message)
                if match:
                    columns_count = int(match.group(1))

        logger.info(
            "Successfully verified table %s is completed | rows=%s columns=%s migration_record_id=%s",
            table_name,
            rows_count,
            columns_count,
            migration_record_id,
        )

        return {
            "status": "success",
            "rows": rows_count,
            "columns": columns_count,
        }

    except Exception as e:
        logger.error(
            "Error checking completion status for table %s: %s",
            table_name,
            e,
            exc_info=True
        )
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def data_transfer_logs(
    *args,
    cmp_id: Optional[int] = None,
    connection_id: Optional[int] = None,
    usr_id: Optional[int] = None,
    user_id: Optional[int] = None,  # Alias for usr_id
    table_name: Optional[str] = None,
    object_name: Optional[str] = None,  # Alias for table_name (used by salesforce_datalake_delta)
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    columns_data: Optional[int] = None,
    session: Optional[str] = None,
    job_name: Optional[str] = None,
    migration_session_id: Optional[str] = None,
    # Salesforce-style parameters
    insert_success: Optional[int] = None,
    insert_failure: Optional[int] = None,
    upsert_success: Optional[int] = None,
    upsert_failure: Optional[int] = None,
    # Dynamics/Datalake-style parameters
    insert_rec: Optional[int] = None,
    inserts: Optional[int] = None,  # Alias for insert_rec
    modi_rec: Optional[int] = None,
    updates: Optional[int] = None,  # Alias for modi_rec
    del_rec: Optional[int] = None,
    deletes: Optional[int] = None,  # Alias for del_rec
    # Salesforce Datalake Delta style
    total_rows: Optional[int] = None,
    **kwargs
):
    """
    Unified function to log data transfer information to log and data_transfer tables.
    Supports multiple parameter styles for backward compatibility.
    
    Parameter Styles Supported:
    1. Salesforce/Google Reviews: (cmp_id, connection_id, usr_id, table_name, start_time, end_time, columns_data, session, insert_success, insert_failure, upsert_success, upsert_failure, job_name=None)
    2. Dynamics/Datalake: (cmp_id, connection_id, usr_id, table_name, start_time, end_time, insert_rec, modi_rec, del_rec, columns_data, session, job_name=None)
    3. Salesforce Datalake Delta: (connection_id, object_name, total_rows, inserts, updates, deletes, cmp_id=..., usr_id=..., session=..., job_name=...)
    
    Args:
        cmp_id: Company ID
        connection_id: Connection ID
        usr_id/user_id: User ID
        table_name/object_name: Table/object name
        start_time: Transfer start time
        end_time: Transfer end time
        columns_data: Number of columns
        session: Session identifier
        job_name: Job name (required for linking to MigrationRecord)
        migration_session_id: Migration session ID (optional)
        insert_success: Number of successful inserts (Salesforce style)
        insert_failure: Number of failed inserts (Salesforce style)
        upsert_success: Number of successful upserts (Salesforce style)
        upsert_failure: Number of failed upserts (Salesforce style)
        insert_rec/inserts: Number of records inserted (Dynamics/Datalake style)
        modi_rec/updates: Number of records modified (Dynamics/Datalake style)
        del_rec/deletes: Number of records deleted (Dynamics/Datalake style)
        total_rows: Total rows (Salesforce Datalake Delta style)
    """
    connection = None
    cursor = None
    
    try:
        # Parse positional arguments for backward compatibility
        # Style detection based on number of args:
        # - Style 1 (Salesforce/Google): 12 args - columns at 6, insert/upsert counts at 8-11
        # - Style 2 (Dynamics/Datalake): 11 args - insert/modify/delete at 6-8, columns at 9, session at 10
        
        if len(args) >= 6:
            # Common first 6 args for all styles
            cmp_id = args[0] if cmp_id is None else cmp_id
            connection_id = args[1] if connection_id is None else connection_id
            usr_id = args[2] if usr_id is None else usr_id
            table_name = args[3] if table_name is None else table_name
            start_time = args[4] if start_time is None else start_time
            end_time = args[5] if end_time is None else end_time
            
            if len(args) >= 12:
                # Style 1: Salesforce/Google Reviews (12+ positional args)
                columns_data = args[6] if columns_data is None else columns_data
                session = args[7] if session is None else session
                insert_success = args[8] if insert_success is None else insert_success
                insert_failure = args[9] if insert_failure is None and len(args) > 9 else insert_failure
                upsert_success = args[10] if upsert_success is None and len(args) > 10 else upsert_success
                upsert_failure = args[11] if upsert_failure is None and len(args) > 11 else upsert_failure
                if len(args) > 12:
                    job_name = args[12] if job_name is None else job_name
            elif len(args) >= 11:
                # Style 2: Dynamics/Datalake (11+ positional args)
                insert_rec = args[6] if insert_rec is None else insert_rec
                modi_rec = args[7] if modi_rec is None and len(args) > 7 else modi_rec
                del_rec = args[8] if del_rec is None and len(args) > 8 else del_rec
                columns_data = args[9] if columns_data is None and len(args) > 9 else columns_data
                session = args[10] if session is None and len(args) > 10 else session
                if len(args) > 11:
                    job_name = args[11] if job_name is None else job_name
        
        # Handle aliases
        if user_id is not None and usr_id is None:
            usr_id = user_id
        if object_name is not None and table_name is None:
            table_name = object_name
        if inserts is not None and insert_rec is None:
            insert_rec = inserts
        if updates is not None and modi_rec is None:
            modi_rec = updates
        if deletes is not None and del_rec is None:
            del_rec = deletes
        
        # Validate required parameters
        if cmp_id is None or connection_id is None or usr_id is None or table_name is None:
            error_msg = f"❌ Missing required parameters: cmp_id={cmp_id}, connection_id={connection_id}, usr_id={usr_id}, table_name={table_name}"
            print(error_msg)
            raise ValueError(error_msg)
        
        if start_time is None:
            start_time = datetime.now(timezone.utc)
        if end_time is None:
            end_time = datetime.now(timezone.utc)
        if columns_data is None:
            columns_data = 0
        
        # Normalize parameters - convert different styles to unified format
        if insert_success is not None or insert_failure is not None or upsert_success is not None or upsert_failure is not None:
            # Salesforce-style: insert_success + upsert_success = total inserts
            total_inserts = (insert_success or 0) + (upsert_success or 0)
            total_updates = upsert_success or 0  # Upserts count as updates too
            total_deletes = 0
            total_failures = (insert_failure or 0) + (upsert_failure or 0)
        elif insert_rec is not None or modi_rec is not None or del_rec is not None:
            # Dynamics/Datalake style: direct counts
            total_inserts = insert_rec or 0
            total_updates = modi_rec or 0
            total_deletes = del_rec or 0
            total_failures = 0
        elif total_rows is not None:
            # Salesforce Datalake Delta style
            total_inserts = inserts or 0
            total_updates = updates or 0
            total_deletes = deletes or 0
            total_failures = 0
        else:
            # Default to 0 if nothing provided
            total_inserts = 0
            total_updates = 0
            total_deletes = 0
            total_failures = 0
        
        total_rec = total_inserts + total_updates + total_deletes
        
        connection = postgres_connection()
        if connection is None:
            print("Failed to establish connection to the database.")
            return

        cursor = connection.cursor()
        
        # Set statement timeout
        cursor.execute("SET statement_timeout = '30s'")

        # Create message
        if total_failures > 0:
            message = f"{table_name} migration: Success - {total_rec}, Failure - {total_failures}, Columns - {columns_data}."
        else:
            message = f"Successfully migrated/updated '{table_name}' table, containing data with {total_rec} rows and {columns_data} columns."
        print(message)

        # Get migration_record_id using centralized helper function
        migration_record_id = get_migration_record_id(
            cursor=cursor,
            job_name=job_name,
            table_name=table_name,
            connection_id=connection_id,
            allow_fallback=True
        )

        # Get next log_id using a sequence-like approach
        # Cannot use FOR UPDATE with aggregate functions, so we lock the table instead
        # cursor.execute("LOCK TABLE log IN SHARE ROW EXCLUSIVE MODE")
        # cursor.execute("SELECT MAX(log_id) FROM log")
        # max_log_id = cursor.fetchone()[0]
        # new_log_id = max_log_id + 1 if max_log_id is not None else 1
        # ✅ Use PostgreSQL sequence for atomic log_id generation (race-condition-free)
        cursor.execute("SELECT nextval('log_log_id_seq')")
        new_log_id = cursor.fetchone()[0]

        # Updated log INSERT (removed "table" column, added job_name and migration_record_id)
        log_insert_query = '''
            INSERT INTO log (log_id, cmp_id, connection_id, user_id, time, status, message, session, job_name, migration_record_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING log_id
        '''
        cursor.execute(log_insert_query, (new_log_id, cmp_id, connection_id, usr_id, end_time, 'S', message, session, job_name, migration_record_id))
        log_id = cursor.fetchone()[0]
        print(f"✅ Inserted in log: log_id={log_id}")

        # Updated data_transfer INSERT (removed cmp_id, connection_id, user_id, added migration_record_id)
        if migration_record_id is None:
            error_msg = f"❌ ERROR: migration_record_id is required but not found. job_name={job_name}, table_name={table_name}, connection_id={connection_id}"
            print(error_msg)
            connection.rollback()  # Rollback the log insert too
            raise ValueError(error_msg)
        
        insert_query = '''
            INSERT INTO data_transfer (connection_id, migration_record_id, table_name, start_time, end_time, log_id, new_rec, mod_rec, del_rec, total_rec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        '''
        cursor.execute(insert_query, (connection_id, migration_record_id, table_name, start_time, end_time, log_id, total_inserts, total_updates, total_deletes, total_rec))

        # Update the corresponding MigrationRecord's start_time to match the Data_Transfer start_time using the variable
        update_migration_record_query = '''
            UPDATE migration_record 
            SET start_time = %s 
            WHERE id = %s
        '''
        cursor.execute(update_migration_record_query, (start_time, migration_record_id))
        print(f"✅ Updated MigrationRecord {migration_record_id} start_time to match Data_Transfer using variable value")

        connection.commit()
        print(f"✅ Data logged successfully to data_transfer. migration_record_id={migration_record_id}, log_id={log_id}")

    except psycopg2.Error as e:
        error_msg = f"❌ Database error in data_transfer_logs: {str(e)}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        if connection:
            connection.rollback()
        raise
    except Exception as e:
        error_msg = f"❌ Unexpected error in data_transfer_logs: {str(e)}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        if connection:
            connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def parse_table_fields(table_fields):
    """
    Handles parsing of `table_fields` to ensure a consistent format.
    """
    if isinstance(table_fields, list):
        return table_fields

    if isinstance(table_fields, dict):
        return table_fields

    if isinstance(table_fields, str):
        try:
            parsed_fields = json.loads(table_fields)
            if isinstance(parsed_fields, (list, dict)):
                return parsed_fields
        except json.JSONDecodeError:
            pass

        try:
            parsed_fields = json.loads(json.loads(table_fields))
            if isinstance(parsed_fields, (list, dict)):
                return parsed_fields
        except json.JSONDecodeError:
            pass

        try:
            parsed_fields = ast.literal_eval(table_fields)
            if isinstance(parsed_fields, (list, dict)):
                return parsed_fields
        except (ValueError, SyntaxError):
            pass

    # Safe fallback: always return dict to avoid propagating None downstream.
    return {}

def get_last_checked_dates_and_fields(connection_id, selected_tables=None):
    """
    Fetches the last checked dates, delta timestamp fields, parsed table fields,
    combination formulas, and truncate_data flag for specific tables 
    associated with a given connection ID.
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()

        base_query = """
        SELECT "table", last_synced, table_fields, delta_timestamp_field, 
               combination_formula, truncate_data
        FROM source_table_selection
        WHERE connection_id = %s
        """

        if selected_tables:
            placeholders = ', '.join(['%s'] * len(selected_tables))
            base_query += f" AND \"table\" IN ({placeholders})"
            query_params = [connection_id] + selected_tables
        else:
            query_params = [connection_id]

        cursor.execute(base_query, tuple(query_params))
        rows = cursor.fetchall()

        last_checked_data = {}
        for table_name, last_synced, table_fields, delta_field, combination_formula, truncate_data in rows:
            parsed_fields = parse_table_fields(table_fields)
            parsed_combination = (
                [col.strip() for col in combination_formula.split(",")]
                if combination_formula else None
            )

            last_checked_data[table_name] = {
                "last_checked_date": last_synced,
                "table_fields": parsed_fields,
                "delta_timestamp_field": delta_field,
                "formula_combination": parsed_combination,
                "truncate_data": truncate_data,  # ✅ added
            }

        return last_checked_data

    except Exception as e:
        logger.error(f"❌ Error fetching last checked data: {e}")
        return {}


def get_s3_mapping(connection_id: int) -> Dict[str, Any]:
    """
    Fetch stored S3 mapping using raw SQL (matches pattern of other functions).
    Returns {} if no mapping exists.
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                file_mapping_mode,
                single_file_table_mapping,
                cmp_id,
                source_id,
                basefolder_path,
                created_by,
                updated_by
            FROM source_file_mapping
            WHERE connection_id = %s
        """, (connection_id,))
        
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not row:
            return {}
        
        (
            file_mapping_mode,
            single_file_table_mapping,
            cmp_id,
            source_id,
            basefolder_path,
            created_by,
            updated_by,
        ) = row
        
        # Parse JSON fields (PostgreSQL JSONB comes as dict, JSON as string)
        if isinstance(single_file_table_mapping, str):
            single = json.loads(single_file_table_mapping) if single_file_table_mapping else {}
        else:
            single = single_file_table_mapping or {}
            
        # Ensure we always return plain dicts
        if not isinstance(single, dict):
            single = {}
        
        single = _normalize_s3_single_map(single)

        return {
            "file_mapping_mode": file_mapping_mode or "single_table_per_file",
            "single_file_table_mapping": single,
            "cmp_id": cmp_id,
            "source_id": source_id,
            "basefolder_path": basefolder_path,
            "created_by": created_by,
            "updated_by": updated_by,
        }
    except Exception as e:
        logger.error(f"❌ Error fetching S3 mapping for connection {connection_id}: {e}")
        return {}


def save_s3_mapping(
    connection_id: int,
    file_mapping_mode: str,
    single_file_table_mapping: Dict[str, Any],
    legacy_multi_state: Optional[Dict[str, Any]] = None,
    cmp_id: Optional[int] = None,
    source_id: Optional[int] = None,
    basefolder_path: Optional[str] = None,
    created_by: Optional[int] = None,
    updated_by: Optional[int] = None,
    raise_on_error: bool = False,
) -> None:
    """
    Persist S3 mapping using raw SQL (matches pattern of other functions).

    - First call (no existing row): inserts the mapping (initial sync).
    - Subsequent calls: updates the mapping. Immutability rules are enforced
      by the caller (_validate_and_persist_s3_mapping), not here.
    """
    conn = None
    cursor = None
    
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        # Resolve connection metadata (cmp/source) and validate existence.
        cursor.execute(
            """
            SELECT c.connection_id, c.cmp_id, sc.src_id
            FROM connection c
            LEFT JOIN source_config sc ON c.src_config_id = sc.src_config_id
            WHERE c.connection_id = %s
            """,
            (connection_id,),
        )
        conn_meta = cursor.fetchone()
        if not conn_meta:
            logger.error(f"❌ Cannot save S3 mapping: Connection {connection_id} not found")
            return
        _, connection_cmp_id, connection_source_id = conn_meta
        if cmp_id is None:
            cmp_id = connection_cmp_id
        if source_id is None:
            source_id = connection_source_id
        
        # Keep ownership fields consistent: if caller only supplies updated_by, reuse it for create.
        if created_by is None and updated_by is not None:
            created_by = updated_by

        # Canonicalize names before persist to keep one naming convention in DB.
        single_file_table_mapping = _normalize_s3_single_map(single_file_table_mapping or {})

        # Convert dicts to JSON (PostgreSQL JSONB accepts dict directly, but be safe)
        single_json = json.dumps(single_file_table_mapping) if single_file_table_mapping else '{}'
        
        # Use PostgreSQL UPSERT (INSERT ... ON CONFLICT) - same pattern as other functions
        cursor.execute("""
            INSERT INTO source_file_mapping (
                connection_id,
                cmp_id,
                source_id,
                file_mapping_mode,
                single_file_table_mapping,
                table_to_files_mapping,
                basefolder_path,
                created_by,
                updated_by,
                created_at,
                updated_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, '{}'::jsonb, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (connection_id)
            DO UPDATE SET
                cmp_id = COALESCE(EXCLUDED.cmp_id, source_file_mapping.cmp_id),
                source_id = COALESCE(EXCLUDED.source_id, source_file_mapping.source_id),
                file_mapping_mode = EXCLUDED.file_mapping_mode,
                single_file_table_mapping = EXCLUDED.single_file_table_mapping,
                table_to_files_mapping = '{}'::jsonb,
                basefolder_path = COALESCE(EXCLUDED.basefolder_path, source_file_mapping.basefolder_path),
                created_by = COALESCE(source_file_mapping.created_by, EXCLUDED.created_by, EXCLUDED.updated_by),
                updated_by = COALESCE(EXCLUDED.updated_by, source_file_mapping.updated_by),
                updated_at = CURRENT_TIMESTAMP
        """, (
            connection_id,
            cmp_id,
            source_id,
            file_mapping_mode,
            single_json,
            basefolder_path,
            created_by,
            updated_by,
        ))
        
        conn.commit()
        logger.info(f"✅ Saved S3 mapping for connection {connection_id}")
        
    except Exception as e:
        logger.error(f"❌ Error saving S3 mapping for connection {connection_id}: {e}")
        if conn:
            conn.rollback()
        if raise_on_error:
            raise
    finally:
        # Always close connections
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def update_combination_formula(connection_id, table_name, combination_columns):
    """
    Updates the combination_formula field for a specific table and connection_id.

    Args:
        connection_id (int): The connection ID.
        table_name (str): The name of the table to update.
        combination_columns (List[str]): List of column names to set as the formula.
    """
    try:
        if not combination_columns:
            raise ValueError("Combination columns list is empty")

        formula_str = ','.join(combination_columns)

        conn = postgres_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE source_table_selection
            SET combination_formula = %s
            WHERE connection_id = %s AND "table" = %s
        """, (formula_str, connection_id, table_name))

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[INFO] Updated combination_formula for {table_name}: {formula_str}")

    except Exception as e:
        logger.error(f"❌ Failed to update combination_formula for {table_name}: {e}")


def get_company_email(company_id: int) -> Optional[str]:
    """
    Fetch company admin email from the Company's admin users.
    
    Since the Company model doesn't have a direct email field,
    we fetch from the first admin/staff user of the company.
    
    Args:
        company_id: The company ID (cmp_id)
        
    Returns:
        str: Company admin email address or None if not found
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        # Get email from first admin user of the company
        cursor.execute("""
            SELECT company_email 
            FROM "user" 
            WHERE cmp_id = %s 
              AND is_staff = TRUE
              AND company_email IS NOT NULL
            ORDER BY user_id ASC
            LIMIT 1
        """, (company_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result and result[0]:
            logger.info(f"✅ Found company admin email for cmp_id {company_id}: {result[0]}")
            return result[0]
        else:
            logger.info(f"ℹ️ No company admin email found for cmp_id {company_id}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error fetching company email for cmp_id {company_id}: {e}")
        logger.error(traceback.format_exc())
        return None


def get_all_company_notification_emails(company_id: int) -> Dict[str, List[str]]:
    """
    Fetch all notification emails for a company from multiple sources.
    
    This function aggregates emails from:
    1. Company admin users (is_staff=True)
    2. CommunicationSupport table (dedicated support emails)
    3. All active users in the company (optional, for critical notifications)
    
    Args:
        company_id: The company ID (cmp_id)
        
    Returns:
        Dict with email sources and addresses:
        {
            "company_admin_emails": [...],
            "communication_support_emails": [...],
            "all_company_users": [...],
            "combined": [...]  # All unique emails combined
        }
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        # 1. Get all admin/staff user emails from this company
        cursor.execute("""
            SELECT DISTINCT company_email 
            FROM "user" 
            WHERE cmp_id = %s 
              AND is_staff = TRUE
              AND company_email IS NOT NULL
        """, (company_id,))
        admin_emails = [row[0] for row in cursor.fetchall() if row[0]]
        
        # 2. Get CommunicationSupport emails
        cursor.execute("""
            SELECT email_addresses 
            FROM communication_support 
            WHERE company_id = %s AND is_active = TRUE
        """, (company_id,))
        
        comm_support_emails = []
        result = cursor.fetchone()
        if result and result[0]:
            import json
            email_list = json.loads(result[0]) if isinstance(result[0], str) else result[0]
            if isinstance(email_list, list):
                comm_support_emails = [email.strip() for email in email_list if email.strip()]
        
        # 3. Get all active user emails from this company
        cursor.execute("""
            SELECT DISTINCT company_email 
            FROM "user" 
            WHERE cmp_id = %s 
              AND is_active = TRUE
              AND company_email IS NOT NULL
        """, (company_id,))
        all_user_emails = [row[0] for row in cursor.fetchall() if row[0]]
        
        cursor.close()
        conn.close()
        
        # Combine all emails (remove duplicates while preserving order)
        combined_emails = []
        seen = set()
        for email in admin_emails + comm_support_emails:
            if email not in seen:
                combined_emails.append(email)
                seen.add(email)
        
        logger.info(f"✅ Found notification emails for company {company_id}:")
        logger.info(f"   Admin emails: {len(admin_emails)}")
        logger.info(f"   CommunicationSupport emails: {len(comm_support_emails)}")
        logger.info(f"   All company users: {len(all_user_emails)}")
        logger.info(f"   Combined unique: {len(combined_emails)}")
        
        return {
            "company_admin_emails": admin_emails,
            "communication_support_emails": comm_support_emails,
            "all_company_users": all_user_emails,
            "combined": combined_emails
        }
        
    except Exception as e:
        logger.error(f"❌ Error fetching company notification emails for cmp_id {company_id}: {e}")
        logger.error(traceback.format_exc())
        return {
            "company_admin_emails": [],
            "communication_support_emails": [],
            "all_company_users": [],
            "combined": []
        }


def reconcile_completed_tables(
    connection_id: int,
    job_name: str,
    migration_session_id: str,
    selected_tables: List[str]
) -> None:
    """Reconciles migration record statuses with the source table selection.

    If a table is marked as 'completed' in `source_table_selection` but its
    associated `migration_record` is in a non-completed state (e.g. 'failed',
    'in_progress', or 'pending'), this function updates the migration record
    status to 'completed'. This corrects any status divergence caused by a
    previous job crash.

    Args:
        connection_id: The identifier of the database connection.
        job_name: The name of the migration job.
        migration_session_id: The session identifier of the migration job.
        selected_tables: The list of table names selected for migration.
    """
    logger.info(
        "Entering reconcile_completed_tables | connection_id=%s job_name=%s session=%s tables=%s",
        connection_id,
        job_name,
        migration_session_id,
        selected_tables,
    )
    print(
        f"Entering reconcile_completed_tables | connection_id={connection_id} job_name={job_name} "
        f"session={migration_session_id} tables={selected_tables}"
    )

    if not selected_tables:
        return

    conn = None
    cursor = None
    try:
        conn = postgres_connection()
        cursor = conn.cursor()

        for table_name in selected_tables:
            table_name = str(table_name or "").strip()
            if not table_name:
                continue

            # 1. Fetch status from source_table_selection
            cursor.execute(
                'SELECT status FROM source_table_selection WHERE connection_id = %s AND "table" = %s',
                (connection_id, table_name)
            )
            row = cursor.fetchone()
            if not row:
                continue

            source_status = row[0]
            if source_status == 'completed':
                # 2. Check current status in migration_record
                cursor.execute(
                    'SELECT status FROM migration_record WHERE job_name = %s AND migration_session_id = %s AND table_name = %s',
                    (job_name, migration_session_id, table_name)
                )
                rec_row = cursor.fetchone()
                if rec_row:
                    rec_status = rec_row[0]
                    if rec_status != 'completed':
                        logger.warning(
                            "Reconciliation trigger: Table %s status diverged (source_table_selection='completed', migration_record='%s'). "
                            "Updating migration_record status to 'completed'.",
                            table_name, rec_status
                        )
                        print(
                            f"Reconciliation trigger: Table {table_name} status diverged (source_table_selection='completed', migration_record='{rec_status}'). "
                            f"Updating migration_record status to 'completed'."
                        )
                        cursor.execute(
                            'UPDATE migration_record SET status = %s, updated_at = NOW() WHERE job_name = %s AND migration_session_id = %s AND table_name = %s',
                            ('completed', job_name, migration_session_id, table_name)
                        )

        conn.commit()
        logger.info("Reconciliation of completed tables complete.")
        print("Reconciliation of completed tables complete.")

    except Exception as e:
        logger.error("Error during completed tables status reconciliation: %s", e, exc_info=True)
        print(f"Error during completed tables status reconciliation: {e}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
