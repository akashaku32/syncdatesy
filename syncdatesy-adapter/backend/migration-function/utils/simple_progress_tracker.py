"""
Simple progress tracker for migration staging records count.
Tracks the number of records in staging tables for migration monitoring.
"""

from typing import Optional
from sqlalchemy.engine import Engine
from sqlalchemy import text
import logging
import time

logger = logging.getLogger(__name__)


class SimpleProgressTracker:
    """
    Utility class to track staging records count for migration progress.
    """
    
    @staticmethod
    def update_staging_count(
        engine: Engine = None,
        connection_id: int = None,
        migration_session_id: str = None,
        job_name: str = None,
        table_name: str = None,
        staging_table: str = None,
        snowflake_schema: str = 'PUBLIC',
        batch_rows: int = None
    ) -> bool:
        """
        Update the staging records count for a migration record.
        
        Two modes:
          1. batch_rows provided → Accumulate in PostgreSQL (no Snowflake query).
             Progress is monotonically increasing. Used by S3 multi-file migrations.
          2. batch_rows NOT provided → Query Snowflake staging table COUNT(*) and
             write absolute count (legacy behavior, used by Salesforce and others).
        
        Args:
            engine: SQLAlchemy engine for Snowflake (only needed if batch_rows is None)
            connection_id: Connection ID
            migration_session_id: Migration session identifier
            job_name: Name of the job
            table_name: Name of the table being migrated
            staging_table: Name of the actual staging table in Snowflake
            snowflake_schema: Schema name for the staging table (defaults to 'PUBLIC')
            batch_rows: Number of rows in this batch. If provided, accumulates
                        instead of querying Snowflake. Only used by S3 connector.
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        postgres_conn = None
        postgres_cursor = None
        started_at = time.perf_counter()
        logger.info(
            "Starting update_staging_count | connection_id=%s migration_session_id=%s job_name=%s table_name=%s batch_rows=%s",
            connection_id,
            migration_session_id,
            job_name,
            table_name,
            batch_rows,
        )
        print(
            f"Starting update_staging_count | connection_id={connection_id} migration_session_id={migration_session_id} "
            f"job_name={job_name} table_name={table_name} batch_rows={batch_rows}"
        )
        
        try:
            # Convert migration_session_id to integer if it's a string
            if isinstance(migration_session_id, str):
                migration_session_id = int(migration_session_id)
            
            if batch_rows is not None:
                # ── Cumulative mode (S3): skip Snowflake, accumulate in PostgreSQL ──
                count_value = batch_rows
                
                from utils.database_utils import postgres_connection
                postgres_conn = postgres_connection()
                postgres_cursor = postgres_conn.cursor()
                
                # Atomic increment — progress is monotonically increasing
                update_query = """
                    UPDATE migration_record 
                    SET staging_records_count = COALESCE(staging_records_count, 0) + %s
                    WHERE connection_id = %s 
                    AND migration_session_id = %s 
                    AND job_name = %s 
                    AND table_name = %s
                """
                
                postgres_cursor.execute(
                    update_query,
                    (count_value, connection_id, migration_session_id, job_name, table_name)
                )
            else:
                # ── Legacy mode (Salesforce/others): query Snowflake for absolute count ──
                if not engine:
                    raise RuntimeError("engine is required when batch_rows is not provided")
                    
                schema = (snowflake_schema or "").strip().upper()
                if not schema:
                    raise ValueError(
                        "snowflake_schema is required when querying Snowflake staging row counts"
                    )
                staging = (staging_table or "").strip().upper()
                if not staging:
                    raise ValueError("staging_table is required for Snowflake staging row counts")

                # Match qualified-name style used in salesforce_service insert_into_staging COPY/COUNT.
                with engine.connect() as snowflake_conn:
                    count_query = f'SELECT COUNT(*) FROM {schema}."{staging}"'
                    count_result = snowflake_conn.execute(text(count_query))
                    count_value = count_result.fetchone()[0]
                    
                    logger.info(
                        f"Retrieved count from Snowflake staging table '{staging_table}': {count_value} records"
                    )
                
                from utils.database_utils import postgres_connection
                postgres_conn = postgres_connection()
                postgres_cursor = postgres_conn.cursor()
                
                # Absolute write — single call per table
                update_query = """
                    UPDATE migration_record 
                    SET staging_records_count = %s
                    WHERE connection_id = %s 
                    AND migration_session_id = %s 
                    AND job_name = %s 
                    AND table_name = %s
                """
                
                postgres_cursor.execute(
                    update_query,
                    (count_value, connection_id, migration_session_id, job_name, table_name)
                )
            
            postgres_conn.commit()
            
            if postgres_cursor.rowcount > 0:
                logger.info(
                    f"✅ Updated staging records count for session {migration_session_id}, "
                    f"table '{table_name}': {count_value} records"
                )
                print(
                    f"✅ Updated staging records count for session {migration_session_id}, table '{table_name}': {count_value} records"
                )

                # Push live migration-status updates so UI receives staging_records_count changes immediately.
                try:
                    from utils.websocket_utils import trigger_ws_status_update
                    trigger_ws_status_update(migration_session_id)
                except Exception as ws_err:
                    logger.warning(
                        f"⚠️ Failed to trigger migration status WebSocket update for "
                        f"session {migration_session_id}: {ws_err}"
                    )
                # Also push connection table-status updates; some reverse ETL screens
                # listen on this channel for live refresh.
                try:
                    from utils.websocket_utils import trigger_connection_ws_status_update
                    trigger_connection_ws_status_update(connection_id)
                except Exception as ws_err:
                    logger.warning(
                        f"⚠️ Failed to trigger connection status WebSocket update for "
                        f"connection {connection_id}: {ws_err}"
                    )
                return True
            else:
                logger.warning(
                    f"⚠️ No migration record found to update staging count for "
                    f"session {migration_session_id}, table '{table_name}'"
                )
                print(
                    f"⚠️ No migration record found to update staging count for session {migration_session_id}, table '{table_name}'"
                )
                return False
                    
        except Exception as e:
            logger.error(
                f"❌ Failed to update staging records count for session {migration_session_id}, "
                f"table '{table_name}': {str(e)}"
            )
            print(
                f"❌ Failed to update staging records count for session {migration_session_id}, table '{table_name}': {str(e)}"
            )
            if postgres_conn:
                postgres_conn.rollback()
            return False
        finally:
            # Clean up PostgreSQL connection resources
            if postgres_cursor:
                postgres_cursor.close()
            if postgres_conn:
                postgres_conn.close()
            logger.info(
                "Finished update_staging_count | connection_id=%s migration_session_id=%s job_name=%s table_name=%s elapsed_seconds=%.3f",
                connection_id,
                migration_session_id,
                job_name,
                table_name,
                time.perf_counter() - started_at,
            )
            print(
                f"Finished update_staging_count | connection_id={connection_id} migration_session_id={migration_session_id} "
                f"job_name={job_name} table_name={table_name} elapsed_seconds={time.perf_counter() - started_at:.3f}"
            )
    
    @staticmethod
    def reset_staging_count(
        connection_id: int,
        migration_session_id: str,
        job_name: str,
        table_name: str
    ) -> bool:
        """
        Reset the staging records count to 0 for a migration record.
        
        Args:
            connection_id: Connection ID
            migration_session_id: Migration session identifier
            job_name: Name of the job
            table_name: Name of the table being migrated
            
        Returns:
            bool: True if reset was successful, False otherwise
        """
        postgres_conn = None
        postgres_cursor = None
        started_at = time.perf_counter()
        logger.info(
            "Starting reset_staging_count | connection_id=%s migration_session_id=%s job_name=%s table_name=%s",
            connection_id,
            migration_session_id,
            job_name,
            table_name,
        )
        print(
            f"Starting reset_staging_count | connection_id={connection_id} migration_session_id={migration_session_id} "
            f"job_name={job_name} table_name={table_name}"
        )
        
        try:
            # Convert migration_session_id to integer if it's a string
            if isinstance(migration_session_id, str):
                migration_session_id = int(migration_session_id)
            
            # Import postgres_connection from database_utils
            from utils.database_utils import postgres_connection
            
            postgres_conn = postgres_connection()
            postgres_cursor = postgres_conn.cursor()
            
            # Reset the staging_records_count field to 0
            update_query = """
                UPDATE migration_record 
                SET staging_records_count = 0
                WHERE connection_id = %s 
                AND migration_session_id = %s 
                AND job_name = %s 
                AND table_name = %s
            """
            
            postgres_cursor.execute(
                update_query,
                (
                    connection_id,
                    migration_session_id,
                    job_name,
                    table_name
                )
            )
            
            postgres_conn.commit()
            
            if postgres_cursor.rowcount > 0:
                logger.info(
                    f"✅ Reset staging records count for session {migration_session_id}, "
                    f"table '{table_name}' to 0"
                )
                print(
                    f"✅ Reset staging records count for session {migration_session_id}, table '{table_name}' to 0"
                )
                return True
            else:
                logger.warning(
                    f"⚠️ No migration record found to reset staging count for "
                    f"session {migration_session_id}, table '{table_name}'"
                )
                print(
                    f"⚠️ No migration record found to reset staging count for session {migration_session_id}, table '{table_name}'"
                )
                return False
                    
        except Exception as e:
            logger.error(
                f"❌ Failed to reset staging records count for session {migration_session_id}, "
                f"table '{table_name}': {str(e)}"
            )
            print(
                f"❌ Failed to reset staging records count for session {migration_session_id}, table '{table_name}': {str(e)}"
            )
            if postgres_conn:
                postgres_conn.rollback()
            return False
        finally:
            # Clean up PostgreSQL connection resources
            if postgres_cursor:
                postgres_cursor.close()
            if postgres_conn:
                postgres_conn.close()
            logger.info(
                "Finished reset_staging_count | connection_id=%s migration_session_id=%s job_name=%s table_name=%s elapsed_seconds=%.3f",
                connection_id,
                migration_session_id,
                job_name,
                table_name,
                time.perf_counter() - started_at,
            )
            print(
                f"Finished reset_staging_count | connection_id={connection_id} migration_session_id={migration_session_id} "
                f"job_name={job_name} table_name={table_name} elapsed_seconds={time.perf_counter() - started_at:.3f}"
            )

    @staticmethod
    def accumulate_staging_progress_batch(
        connection_id: int = None,
        migration_session_id=None,
        job_name: str = None,
        table_name: str = None,
        batch_rows: int = None,
    ) -> bool:
        """
        Increment staging_records_count for live migration overview progress.
        Used by file-based connectors while reading/uploading source chunks.
        """
        if not all([connection_id, migration_session_id, job_name, table_name]):
            return False
        rows = int(batch_rows or 0)
        if rows <= 0:
            return False
        try:
            return SimpleProgressTracker.update_staging_count(
                connection_id=connection_id,
                migration_session_id=str(migration_session_id),
                job_name=job_name,
                table_name=table_name,
                batch_rows=rows,
            )
        except Exception as e:
            logger.warning(
                "Failed to accumulate staging_records_count for table '%s': %s",
                table_name,
                e,
            )
            return False

    @staticmethod
    def set_staging_count(
        connection_id: int,
        migration_session_id: str,
        job_name: str,
        table_name: str,
        count_value: int,
    ) -> bool:
        """
        Set staging_records_count to an absolute value for a migration record.
        Useful when final effective row count differs from staged chunk count
        (for example, delta MERGE where effective rows = inserts + updates).
        """
        postgres_conn = None
        postgres_cursor = None
        started_at = time.perf_counter()
        logger.info(
            "Starting set_staging_count | connection_id=%s migration_session_id=%s job_name=%s table_name=%s count_value=%s",
            connection_id,
            migration_session_id,
            job_name,
            table_name,
            count_value,
        )
        print(
            f"Starting set_staging_count | connection_id={connection_id} migration_session_id={migration_session_id} "
            f"job_name={job_name} table_name={table_name} count_value={count_value}"
        )

        try:
            if isinstance(migration_session_id, str):
                migration_session_id = int(migration_session_id)

            from utils.database_utils import postgres_connection

            postgres_conn = postgres_connection()
            postgres_cursor = postgres_conn.cursor()

            update_query = """
                UPDATE migration_record
                SET staging_records_count = %s
                WHERE connection_id = %s
                AND migration_session_id = %s
                AND job_name = %s
                AND table_name = %s
            """

            postgres_cursor.execute(
                update_query,
                (int(count_value or 0), connection_id, migration_session_id, job_name, table_name),
            )
            postgres_conn.commit()

            if postgres_cursor.rowcount > 0:
                logger.info(
                    "✅ Set staging_records_count for table '%s' to %s",
                    table_name,
                    int(count_value or 0),
                )
                print(f"✅ Set staging_records_count for table '{table_name}' to {int(count_value or 0)}")
                try:
                    from utils.websocket_utils import trigger_ws_status_update
                    trigger_ws_status_update(migration_session_id)
                except Exception:
                    pass
                try:
                    from utils.websocket_utils import trigger_connection_ws_status_update
                    trigger_connection_ws_status_update(connection_id)
                except Exception:
                    pass
                return True

            logger.warning("⚠️ No migration record found for table '%s' to set staging count", table_name)
            print(f"⚠️ No migration record found for table '{table_name}' to set staging count")
            return False
        except Exception as e:
            logger.exception("❌ Error setting staging_records_count for table '%s'", table_name)
            print(f"❌ Error setting staging_records_count for table '{table_name}': {str(e)}")
            if postgres_conn:
                postgres_conn.rollback()
            return False
        finally:
            if postgres_cursor:
                postgres_cursor.close()
            if postgres_conn:
                postgres_conn.close()
            logger.info(
                "Finished set_staging_count | connection_id=%s migration_session_id=%s job_name=%s table_name=%s elapsed_seconds=%.3f",
                connection_id,
                migration_session_id,
                job_name,
                table_name,
                time.perf_counter() - started_at,
            )
            print(
                f"Finished set_staging_count | connection_id={connection_id} migration_session_id={migration_session_id} "
                f"job_name={job_name} table_name={table_name} elapsed_seconds={time.perf_counter() - started_at:.3f}"
            )
