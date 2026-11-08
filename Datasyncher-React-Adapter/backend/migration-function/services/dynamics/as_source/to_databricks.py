"""
Microsoft Dynamics 365 F&O → Databricks migration orchestrator.

Source (Azure Dataverse export) helpers live in microsoft_dynamics_source_common.
Destination load helpers live in databricks_dynamics_load_helpers.
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from datetime import datetime

import adlfs
import pandas as pd
import pytz

from models.microsoft_dynamics_models import MigrationRequest
from services.databricks.load_helpers import (
    AZURE_SPARK_LOAD_STRATEGY_BULK,
    AZURE_SPARK_READ_MODE_STAGING,
    LOAD_ENGINE_SPARK_JOB,
    SPARK_LANDING_CLOUD_AZURE,
    SPARK_LANDING_CLOUD_GCS,
    SPARK_LANDING_CLOUD_S3,
    SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL,
    apply_data_max_watermark,
    check_staging_table_exists,
    check_table_exists_databricks,
    copy_function,
    destination_connection_function,
    drop_databricks_table,
    drop_existing_ds_staging_tables_for_table,
    drop_staging_table,
    ensure_databricks_base_table_exists,
    field_dtype_conversion,
    flush_orphan_staging_tables,
    get_databricks_type,
    retrieve_snowflake_ids_delete,
    resolve_azure_spark_load_strategy,
    resolve_azure_spark_read_mode,
    resolve_databricks_load_engine,
    resolve_spark_bulk_job_timeout_seconds,
    resolve_spark_landing_cloud,
    resolve_spark_staging_load_strategy,
    resolve_spark_staging_read_mode,
    verify_databricks_adls_prefix_readable,
    verify_databricks_gcs_prefix_readable,
    verify_databricks_s3_prefix_readable,
)
from services.databricks.spark_job_client import (
    DatabricksSparkJobError,
    ensure_dynamics_spark_job,
    submit_and_wait,
)
from services.dynamics.source_common import (
    _TableMigrationDetailFileLog,
    _connection_last_synced_folder_cursor,
    _mark_dataverse_delete_csv_chunk,
    _vprint,
    align_fields_with_existing,
    connect_to_blob_storage,
    dataverse_csv_processing_order_key,
    delete_csv_from_bucket,
    directory_exists_in_date_directory,
    discover_table_date_dir_for_spark_direct,
    dynamics_gcs_landing_prefix,
    dynamics_gcs_staging_table_landing_prefix,
    dynamics_s3_staging_table_landing_prefix,
    cleanup_adls_staging_after_spark_merge,
    cleanup_gcs_staging_after_spark_merge,
    cleanup_s3_staging_after_spark_merge,
    resolve_adls_staging_cleanup_enabled,
    resolve_bulk_staging_reuse_enabled,
    resolve_gcs_staging_cleanup_enabled,
    resolve_s3_staging_cleanup_enabled,
    count_adls_staging_csv_blobs,
    count_gcs_staging_csv_blobs,
    count_s3_staging_csv_blobs,
    land_table_bulk_to_adls_staging,
    land_table_bulk_to_gcs_staging,
    land_table_bulk_to_s3_staging,
    land_table_date_dir_to_adls_staging,
    land_table_date_dir_to_gcs,
    land_table_date_dir_to_s3,
    dynamics_adls_staging_table_landing_prefix,
    verify_adls_staging_has_csvs,
    verify_gcs_staging_has_csvs,
    verify_s3_staging_has_csvs,
    write_landing_manifest,
    DATASYNCHER_AZURE_STAGING_URL,
    BUCKET_NAME,
    filter_date_directories,
    filter_null_primary_key_rows,
    filter_orphan_delete_rows,
    get_all_date_directories_from_db,
    get_last_checked_date_directories,
    get_last_checked_dates_and_fields,
    list_table_csv_blobs,
    resolve_dataverse_base_folder,
    insert_error_log,
    insert_or_update_date_directories,
    is_dataverse_delete_export_csv,
    is_directory_greater_than,
    list_date_directories,
    log_with_time,
    parse_table_fields,
    postgres_connection,
    read_csv_in_chunks,
    take_flush_slice,
    transformations,
    update_table_status,
    upload_to_bucket,
)
from utils.azure_path_utils import join_azure_blob_path
from utils.pipeline_fleet_lease import refresh_pipeline_flow_fleet_lease
from utils.celery_cleanup_utils import handle_migration_completion
from utils.database_utils import data_transfer_logs, get_migration_record_id, postgres_connection
from utils.error_utils import insert_tool_log, log_migration_completion
from utils.snowflake_utils import (
    lookup_session_staging_name,
    remember_session_staging_name,
    touch_session_staging_name,
)

# re-export for helpers backward compat during transition
# helpers should import from source_common; keep these available
__all__ = [
    "migrate",
    "main",
    "process_table",
    "process_table_wrapper",
    "process_file_in_chunks",
    "table_creation",
    "databricks_load_chunk",
    "upload_to_bucket",
    "delete_csv_from_bucket",
]

for _noisy_logger in (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.storage",
    "azure.storage.blob",
    "azure.identity",
    "urllib3",
):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _spark_staging_cleanup_notebook_param(landing_cloud: str) -> str:
    """Pass through env/connection cleanup toggle to Spark merge notebooks."""
    if landing_cloud == SPARK_LANDING_CLOUD_GCS:
        enabled = resolve_gcs_staging_cleanup_enabled()
    elif landing_cloud == SPARK_LANDING_CLOUD_S3:
        enabled = resolve_s3_staging_cleanup_enabled()
    else:
        enabled = resolve_adls_staging_cleanup_enabled()
    return "true" if enabled else "false"


def _is_initial_load_for_selected_tables(last_checked_dates, selected_tables) -> bool:
    """True when every selected table has no per-table watermark (full history expected)."""
    if not selected_tables:
        return False
    for table_name in selected_tables:
        watermark = (last_checked_dates or {}).get(table_name)
        if watermark:
            return False
    return True


def _force_full_folder_scan_for_missing_base_tables(
    *,
    destination_connection,
    connection_id,
    cmp_id,
    selected_tables,
    last_checked_date: dict,
    last_checked_datedirectory,
):
    """
    If the Databricks base table is missing, always treat the run as initial load.

    Postgres ``last_synced`` / per-table watermarks must not limit folder scan when
    there is no destination table to merge into (dropped base, never created, etc.).
    """
    if not selected_tables:
        return last_checked_datedirectory, []

    missing_full: list[str] = []
    destination_engine = None
    try:
        destination_engine, schema = destination_connection_function(
            destination_connection,
            connection_id,
            cmp_id,
            skip_external_location_ensure=True,
        )
        dest_conn = dict(destination_connection or {})
        if schema and not dest_conn.get("schema"):
            dest_conn["schema"] = schema

        for table_name in selected_tables:
            try:
                table_exist = check_table_exists_databricks(
                    destination_engine,
                    table_name,
                    dest_conn,
                    connection_id,
                    cmp_id,
                )
            except Exception as exists_exc:
                logger.warning(
                    "DBX|missing_base_precheck_exists_failed | table=%s | err=%s",
                    table_name,
                    exists_exc,
                )
                table_exist = False
            if table_exist:
                continue

            missing_full.append(table_name)
            last_checked_date[table_name] = None

        if missing_full:
            logger.warning(
                "DBX|missing_base_force_initial_load | connection_id=%s | tables=%s | "
                "clearing_folder_cursor=%s",
                connection_id,
                missing_full,
                last_checked_datedirectory,
            )
            last_checked_datedirectory = None
    except Exception as precheck_exc:
        logger.warning(
            "DBX|missing_base_precheck_failed | connection_id=%s | err=%s",
            connection_id,
            precheck_exc,
        )
    finally:
        if destination_engine is not None:
            try:
                destination_engine.dispose()
            except Exception:
                pass

    return last_checked_datedirectory, missing_full


def _is_retryable_migration_error(exc: Exception) -> bool:
    """Transient errors that Celery child tasks may retry — do not mark terminal failed yet."""
    if exc.__class__.__name__ == "DatabricksWarehouseUnavailable":
        return False
    text = f"{exc!r} {exc}".lower()
    if "resource_exhausted" in text or "cannot run queries" in text:
        return False
    retry_tokens = (
        "write operation timed out",
        "connection aborted",
        "connection reset",
        "connection broken",
        "timed out",
        "timeout",
        "temporary failure",
        "service unavailable",
        "503",
        "429",
        "throttl",
        "server busy",
        "metadata server",
        "169.254.169.254",
        "metadata.google.internal",
        "access token from metadata",
        "gcs workload identity",
    )
    return any(token in text for token in retry_tokens)


def _failure_stage_hint(exc: Exception) -> str:
    text = str(exc or "").lower()
    if any(
        token in text
        for token in (
            "metadata.google.internal",
            "169.254.169.254",
            "universe_domain",
            "metadata server",
            "access token from metadata",
        )
    ):
        return "gcs_auth"
    if any(token in text for token in ("gcs://", "gcs upload", "upload_to_bucket", "storage.client")):
        return "gcs_upload"
    if any(token in text for token in ("copy into", "merge into", "databricks", "sql warehouse")):
        return "databricks_sql"
    if any(token in text for token in ("azure", "blob", "adlfs", "container")):
        return "dynamics_azure_read"
    return "unknown"


def _mark_table_terminal_failure(
    *,
    exc: Exception,
    table_name: str,
    connection_id: int,
    migration_session_id,
    job_name: str,
    last_checked_date,
    existing_fields,
    cmp_id,
    usr_id,
    session,
    detail_log=None,
    error_context: str = "process_table",
) -> None:
    """Mark table/MR failed unless Celery may still retry this error."""
    error_message = (
        f"An error occurred while processing the table '{table_name}': {exc}"
    )
    if _is_retryable_migration_error(exc):
        logger.warning(
            "DBX|process_table_retryable | table=%s | connection_id=%s | session=%s | "
            "keeping_status=in_progress | err=%s",
            table_name,
            connection_id,
            migration_session_id,
            exc,
        )
        try:
            if detail_log is not None:
                detail_log.log(
                    f"retryable_error defer_terminal_failed err={exc!r}"
                )
        except Exception:
            pass
        insert_error_log(cmp_id, connection_id, error_message, error_context)
        return

    try:
        from utils.load_checkpoint import mark_migration_failed

        mark_migration_failed(
            connection_id=connection_id,
            table_name=table_name,
            migration_session_id=migration_session_id,
            job_name=job_name,
        )
    except Exception:
        pass
    update_table_status(
        connection_id,
        table_name,
        "failed",
        last_checked_date,
        job_name,
        migration_session_id,
        existing_fields,
        cmp_id,
        table_detail_log=detail_log,
    )
    insert_tool_log(
        cmp_id,
        connection_id,
        usr_id,
        "Migration",
        "F",
        error_message,
        session,
        job_name=job_name,
        migration_record_id=None,
    )
    migration_record_id = None
    try:
        _conn = postgres_connection()
        _cur = _conn.cursor()
        migration_record_id = get_migration_record_id(
            _cur, job_name, table_name, connection_id
        )
        if migration_record_id:
            insert_tool_log(
                cmp_id,
                connection_id,
                usr_id,
                table_name,
                "F",
                error_message,
                session,
                job_name=job_name,
                migration_record_id=migration_record_id,
            )
        _cur.close()
        _conn.close()
    except Exception:
        pass
    insert_error_log(cmp_id, connection_id, error_message, error_context)


# ---------------------------------------------------------------------------
# Local helpers (staging merge schema / row count)
# ---------------------------------------------------------------------------

def _qualified_table(engine, schema: str, table_name: str) -> str:
    catalog = None
    if hasattr(engine, "config"):
        catalog = (engine.config.get("catalog") or "").strip() or None
    parts = []
    if catalog:
        parts.append(f"`{catalog}`")
    parts.append(f"`{schema}`")
    parts.append(f"`{table_name}`")
    return ".".join(parts)


def _drop_table(engine, schema, table_name):
    """DROP TABLE IF EXISTS for Databricks (catalog.schema.table)."""
    result = drop_databricks_table(engine, table_name, schema=schema)
    if not result.get("success"):
        raise RuntimeError(result.get("message") or f"Failed to drop {schema}.{table_name}")


def _count_staging_table_rows(destination_engine, schema, staging_table_name):
    """Return row count in the per-job staging table, or None if the check fails."""
    if not destination_engine or not schema or not staging_table_name:
        return None
    try:
        q = _qualified_table(destination_engine, schema, staging_table_name)
        with destination_engine.connect() as conn:
            result = conn.execute(f"SELECT COUNT(*) FROM {q}")
            return int(result.fetchone()[0])
    except Exception as exc:
        logger.warning(
            "[MERGE] staging row count failed schema=%s staging=%s error=%s",
            schema,
            staging_table_name,
            exc,
        )
        return None


def _merge_schema_df_from_staging(destination_engine, schema, staging_table_name):
    """
    Build an empty DataFrame whose columns match the Databricks staging table.
    Used when merge must run but the in-memory schema df was lost.
    """
    if not destination_engine or not schema or not staging_table_name:
        return pd.DataFrame()
    catalog = None
    if hasattr(destination_engine, "config"):
        catalog = (destination_engine.config.get("catalog") or "").strip() or None
    schema_u = schema.strip().upper()
    staging_u = staging_table_name.strip().upper()
    try:
        with destination_engine.connect() as conn:
            if catalog:
                sql = f"""
                    SELECT column_name
                    FROM `{catalog}`.information_schema.columns
                    WHERE upper(table_schema) = '{schema_u}'
                      AND upper(table_name) = '{staging_u}'
                    ORDER BY ordinal_position
                """
            else:
                sql = f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE upper(table_schema) = '{schema_u}'
                      AND upper(table_name) = '{staging_u}'
                    ORDER BY ordinal_position
                """
            rows = conn.execute(sql).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(columns=[r[0] for r in rows])
    except Exception as exc:
        logger.warning(
            "[MERGE] staging column lookup failed schema=%s staging=%s error=%s",
            schema,
            staging_table_name,
            exc,
        )
        return pd.DataFrame()


def _remember_merge_schema_df(current_df, candidate_df):
    """Keep the latest non-empty column schema df for the end-of-table MERGE."""
    if candidate_df is None or not hasattr(candidate_df, "columns"):
        return current_df
    if candidate_df.columns.empty:
        return current_df
    return candidate_df


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

async def migrate(request: MigrationRequest):
    """
    FastAPI endpoint to process Dynamics data into Databricks.
    """
    start_time = time.time()

    source_connection = request.source_config
    destination_connection = request.destination_config
    connection_id = request.connection_id
    cmp_id = request.cmp_id
    usr_id = request.user_id
    session = request.session
    updated_at = request.is_updated
    deleted_at = request.is_deleted
    safety_interval = request.safety_interval
    container_name = source_connection.get("container_name")
    connection_string = source_connection.get("connection_string")
    selected_tables = request.selected_tables
    job_name = request.job_name
    migration_session_id = request.migration_session_id
    execution_order = request.execution_order
    celery_child_mode = bool(getattr(request, "celery_child_mode", False))
    if celery_child_mode and execution_order == "parallel":
        execution_order = "sequential"
    chunk_count = request.chunk_count
    drop_and_reload = request.drop_and_reload
    log_id = request.log_id if hasattr(request, "log_id") else None

    request_dict = request.dict()
    _vprint("=" * 80)
    _vprint("DEBUG RAW REQUEST fields:", list(request_dict.keys()))
    _vprint("drop_and_reload:", request_dict.get("drop_and_reload", "NOT FOUND"))
    _vprint("drop_and_reload flag:", drop_and_reload, "type:", type(drop_and_reload))
    _vprint("selected_tables:", selected_tables)
    _vprint("=" * 80)
    if celery_child_mode:
        logger.info("CELERY CHILD MODE: forcing sequential execution")

    logger.warning(
        "DBX|migrate_start | connection_id=%s | session=%s | cmp_id=%s | tables=%s | "
        "chunk_count=%s | celery_child=%s | job=%s",
        connection_id,
        migration_session_id,
        cmp_id,
        selected_tables,
        chunk_count,
        celery_child_mode,
        (job_name or "")[:120],
    )

    try:
        if not container_name or not connection_string or not selected_tables:
            raise ValueError(
                "Missing required parameters: container_name, connection_string, or selected_tables"
            )

        last_checked_data = get_last_checked_dates_and_fields(connection_id)
        last_checked_datedirectory = get_last_checked_date_directories(connection_id, cmp_id)
        if not last_checked_datedirectory:
            last_checked_datedirectory = _connection_last_synced_folder_cursor(connection_id)
            if last_checked_datedirectory:
                _vprint(
                    "date_directory empty; using connection.last_synced folder cursor: %s",
                    last_checked_datedirectory,
                )
        last_checked_dates = {}
        table_fields = {}

        for table_name, data in last_checked_data.items():
            if not isinstance(data, dict):
                continue
            last_checked_dates[table_name] = data.get("last_checked_date", None)
            table_fields[table_name] = data.get("table_fields") or {}

        for table_name in selected_tables:
            _vprint("Table:", table_name, "last_checked:", last_checked_dates.get(table_name))

        initial_load_mode = _is_initial_load_for_selected_tables(
            last_checked_dates, selected_tables
        )
        if initial_load_mode or drop_and_reload:
            if last_checked_datedirectory:
                logger.warning(
                    "DBX|initial_load_folder_scan | connection_id=%s | session=%s | "
                    "drop_and_reload=%s | ignoring folder cursor=%s",
                    connection_id,
                    migration_session_id,
                    drop_and_reload,
                    last_checked_datedirectory,
                )
            last_checked_datedirectory = None
            if initial_load_mode:
                for table_name in selected_tables:
                    last_checked_dates[table_name] = None
        logger.warning(
            "DBX|migrate_folder_plan | connection_id=%s | session=%s | "
            "initial_load=%s | drop_and_reload=%s | folder_cursor=%s | "
            "table_watermarks=%s",
            connection_id,
            migration_session_id,
            initial_load_mode,
            drop_and_reload,
            last_checked_datedirectory,
            {t: last_checked_dates.get(t) for t in selected_tables},
        )

        aggregated_results = main(
            connection_string,
            container_name,
            selected_tables,
            connection_id,
            cmp_id,
            usr_id,
            session,
            updated_at,
            deleted_at,
            destination_connection,
            safety_interval,
            last_checked_dates,
            job_name,
            migration_session_id,
            table_fields,
            last_checked_datedirectory,
            execution_order,
            chunk_count,
            drop_and_reload,
            source_connection=source_connection,
        )

        total_rows = 0
        total_inserts = 0
        total_updates = 0
        total_delete_recids = 0
        column_size = 0
        last_checked_date = {}

        successful_tables = []
        failed_tables = []

        for table_name, result in aggregated_results:
            if result is not None:
                (
                    table_total_rows,
                    table_total_inserts,
                    table_total_updates,
                    table_total_deletes,
                    table_column_size,
                    table_last_checked_date,
                ) = result
                total_rows += table_total_rows
                total_inserts += table_total_inserts
                total_updates += table_total_updates
                total_delete_recids += table_total_deletes
                column_size = table_column_size
                last_checked_date.update(table_last_checked_date)
                successful_tables.append(table_name)
            else:
                failed_tables.append(table_name)

        execution_time = time.time() - start_time
        if not celery_child_mode:
            log_migration_completion(
                execution_time=execution_time,
                total_rows=total_rows,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=usr_id,
                session=session,
                inserts=total_inserts,
                updates=total_updates,
                deletes=total_delete_recids,
                total_tables=len(selected_tables),
                successful_tables=len(successful_tables),
                failed_tables=len(failed_tables),
                job_name=job_name,
                log_id=log_id,
                include_operation_counts=False,
            )
        else:
            logger.info(
                "CHILD_MODE: skipping per-child completion log job=%s tables=%s/%s rows=%s",
                job_name,
                len(successful_tables),
                len(selected_tables),
                total_rows,
            )

        logger.warning(
            "DBX|migrate_done | connection_id=%s | session=%s | ok=%s | failed=%s | rows=%s | duration_s=%.3f",
            connection_id,
            migration_session_id,
            len(successful_tables),
            len(failed_tables),
            total_rows,
            execution_time,
        )

        if not celery_child_mode:
            try:
                connection_name = (
                    request.connection_name
                    if request.connection_name
                    else f"DYNAMICS_CONNECTION_{connection_id}"
                )

                cleanup_result = handle_migration_completion(
                    connection_name=connection_name,
                    job_name=job_name,
                    completion_message=None,
                )

                if cleanup_result["status"] == "success" and cleanup_result["cleaned_jobs"]:
                    logger.info(
                        "Cleaned up %s stuck celery jobs for %s",
                        len(cleanup_result["cleaned_jobs"]),
                        connection_name,
                    )
                elif cleanup_result["status"] == "error":
                    logger.warning(
                        "Cleanup warning for %s: %s",
                        connection_name,
                        cleanup_result["message"],
                    )

            except Exception as cleanup_error:
                logger.warning("Failed to cleanup celery jobs: %s", cleanup_error)
                traceback.print_exc()

        status = "failed" if failed_tables else "completed"
        return {
            "status": status,
            "status_code": 200,
            "total_rows": total_rows,
            "total_inserts": total_inserts,
            "total_updates": total_updates,
            "total_deleted_recids": total_delete_recids,
            "column_size": column_size,
            "last_checked_date": last_checked_date,
            "execution_time": execution_time,
            "total_tables": len(selected_tables),
            "successful_tables_count": len(successful_tables),
            "failed_tables_count": len(failed_tables),
            "successful_tables_list": successful_tables,
            "failed_tables_list": failed_tables,
            "message": (
                f"Tables failed: {failed_tables}" if failed_tables else None
            ),
        }

    except Exception as e:
        try:
            from utils.connection_progress_utils import clear_connection_retrieval_progress

            clear_connection_retrieval_progress(connection_id)
        except Exception:
            pass
        raise e


def process_table_wrapper(
    table_name,
    connection_string,
    container_name,
    base_folder,
    date_directories,
    container_client,
    connection_id,
    cmp_id,
    usr_id,
    session,
    updated_at,
    deleted_at,
    destination_connection,
    last_checked_date,
    job_name,
    migration_session_id,
    table_fields,
    is_integration_checked,
    chunk_count,
    safety_interval_minutes=None,
):
    try:
        result = process_table(
            table_name,
            connection_string,
            container_name,
            base_folder,
            date_directories,
            container_client,
            connection_id,
            cmp_id,
            usr_id,
            session,
            updated_at,
            deleted_at,
            destination_connection,
            last_checked_date,
            job_name,
            migration_session_id,
            table_fields,
            is_integration_checked,
            chunk_count,
            safety_interval_minutes=safety_interval_minutes,
        )
        logger.info("DBX|process_table_wrapper done | table=%s", table_name)
        return (table_name, result)
    except Exception as e:
        import traceback as _tb
        logger.error(
            "DBX|process_table_wrapper failed | table=%s | connection_id=%s | "
            "session=%s | err=%s\n%s",
            table_name, connection_id, migration_session_id, e, _tb.format_exc(),
        )
        message = f"Error processing table {table_name}: {e}"
        insert_error_log(cmp_id, connection_id, message, "main")
        migration_record_id = None
        try:
            _conn = postgres_connection()
            _cur = _conn.cursor()
            migration_record_id = get_migration_record_id(
                _cur, job_name, table_name, connection_id
            )
            _cur.close()
            _conn.close()
        except Exception:
            pass
        try:
            insert_tool_log(
                cmp_id,
                connection_id,
                usr_id,
                table_name,
                "F",
                message,
                session,
                job_name=job_name,
                migration_record_id=migration_record_id,
            )
        except Exception:
            pass
        _mark_table_terminal_failure(
            exc=e,
            table_name=table_name,
            connection_id=connection_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            last_checked_date=last_checked_date,
            existing_fields={},
            cmp_id=cmp_id,
            usr_id=usr_id,
            session=session,
            error_context="process_table_wrapper",
        )
        return (table_name, None)


def main(
    connection_string,
    container_name,
    selected_tables,
    connection_id,
    cmp_id,
    usr_id,
    session,
    updated_at,
    deleted_at,
    destination_connection,
    safety_interval,
    last_checked_date,
    job_name,
    migration_session_id,
    table_fields,
    last_checked_datedirectory,
    execution_order,
    chunk_count,
    drop_and_reload=False,
    source_connection=None,
):
    _vprint("main drop_and_reload:", drop_and_reload, "tables:", selected_tables)

    _vprint("Starting connection to blob storage")
    start_time = datetime.now()
    container_client = connect_to_blob_storage(connection_string, container_name)
    end_time = datetime.now()
    logger.warning(
        "DBX|azure_connect_ok | connection_id=%s | container=%s | duration_s=%.3f",
        connection_id,
        container_name,
        (end_time - start_time).total_seconds(),
    )
    _vprint("Finished connection to blob storage")
    log_with_time("Connected to blob storage", start_time, end_time)

    _vprint("Starting to infer base folder")
    start_time = datetime.now()
    base_folder = resolve_dataverse_base_folder(
        container_client,
        cmp_id,
        connection_id,
        source_connection,
    )
    _vprint("Base folder:", base_folder)
    end_time = datetime.now()
    log_with_time("Inferred base folder", start_time, end_time)

    current_utc_time_safety = pd.Timestamp.now(tz="UTC")
    safety_interval_minutes = 30
    try:
        if safety_interval and str(safety_interval) != "None":
            safety_interval_minutes = int(safety_interval)
    except (TypeError, ValueError):
        safety_interval_minutes = 30
    safety_interval_duration = (
        pd.Timedelta(0)
        if not safety_interval or safety_interval == "None"
        else pd.Timedelta(minutes=int(safety_interval_minutes))
    )
    safety_interval_cutoff = (
        current_utc_time_safety - safety_interval_duration
    ).strftime("%Y-%m-%dT%H.%M.%SZ")
    safety_interval_cutoff_dt = datetime.strptime(
        safety_interval_cutoff, "%Y-%m-%dT%H.%M.%SZ"
    )

    if drop_and_reload:
        logger.info(
            "DROP AND RELOAD: resetting watermarks and dropping %s table(s) before reload",
            len(selected_tables),
        )
        last_checked_datedirectory = None
        for table_name in selected_tables:
            last_checked_date[table_name] = None
        try:
            destination_engine, schema = destination_connection_function(
                destination_connection,
                connection_id,
                cmp_id,
                skip_external_location_ensure=True,
            )
            for table_name in selected_tables:
                logger.info("Dropping Databricks table: %s", table_name)
                try:
                    _drop_table(destination_engine, schema, table_name)
                    logger.info("Dropped table %s.%s", schema, table_name)
                except Exception as drop_err:
                    logger.warning(
                        "Failed to drop table %s: %s", table_name, drop_err
                    )
            try:
                destination_engine.dispose()
            except Exception:
                pass
            logger.info("Drop phase completed for %s table(s)", len(selected_tables))
        except Exception as e:
            error_msg = f"Error during drop phase: {str(e)}"
            logger.error("%s", error_msg)
            logger.error("Drop phase traceback: %s", traceback.format_exc())
            insert_error_log(cmp_id, connection_id, error_msg, "main_drop_tables")
            logger.warning("Continuing with migration despite drop errors")
    else:
        # Missing Databricks base → full folder scan (before listing folders).
        last_checked_datedirectory, missing_base_tables = (
            _force_full_folder_scan_for_missing_base_tables(
                destination_connection=destination_connection,
                connection_id=connection_id,
                cmp_id=cmp_id,
                selected_tables=selected_tables,
                last_checked_date=last_checked_date,
                last_checked_datedirectory=last_checked_datedirectory,
            )
        )
        if missing_base_tables:
            logger.warning(
                "DBX|folder_plan_after_missing_base | connection_id=%s | "
                "folder_cursor=%s | cleared_watermarks=%s",
                connection_id,
                last_checked_datedirectory,
                missing_base_tables,
            )

    _vprint("Getting filtered date directories")
    start_time = datetime.now()
    date_directories_save = list_date_directories(
        container_client,
        cmp_id,
        connection_id,
        safety_interval_cutoff_dt,
        last_checked_datedirectory,
        base_folder=base_folder,
    )
    date_directories_filter = insert_or_update_date_directories(
        connection_id, date_directories_save
    )
    date_directories = filter_date_directories(
        date_directories_filter, safety_interval_cutoff
    )
    end_time = datetime.now()
    log_with_time("Got filtered date directories", start_time, end_time)
    _vprint("Final date directories:", date_directories)
    logger.warning(
        "DBX|date_directories_ready | connection_id=%s | count=%s | first=%s | last=%s | "
        "safety_cutoff=%s | drop_and_reload=%s",
        connection_id,
        len(date_directories),
        date_directories[0] if date_directories else None,
        date_directories[-1] if date_directories else None,
        safety_interval_cutoff,
        drop_and_reload,
    )
    if not date_directories:
        msg = (
            f"No Azure date folders to process for connection_id={connection_id}. "
            f"safety_cutoff={safety_interval_cutoff!r}. "
            "Check blob export paths or safety interval."
        )
        logger.error("DBX|date_directories_empty | %s", msg)
        insert_error_log(cmp_id, connection_id, msg, "main_date_directories_empty")

    is_integration_checked = False
    aggregated_results = []

    if execution_order == "parallel":
        _vprint(
            "Execution order parallel requested; using sequential mode in Celery-safe path"
        )
    else:
        _vprint("Executing in sequential mode")

    from utils.connection_progress_utils import (
        clear_connection_retrieval_progress,
        update_connection_retrieval_progress,
    )

    total_tables = len(selected_tables)
    if total_tables > 0:
        update_connection_retrieval_progress(connection_id, 0, total_tables)

    for table_index, table_name in enumerate(selected_tables, start=1):
        update_connection_retrieval_progress(connection_id, table_index, total_tables)
        aggregated_results.append(
            process_table_wrapper(
                table_name,
                connection_string,
                container_name,
                base_folder,
                date_directories,
                container_client,
                connection_id,
                cmp_id,
                usr_id,
                session,
                updated_at,
                deleted_at,
                destination_connection,
                last_checked_date,
                job_name,
                migration_session_id,
                table_fields,
                is_integration_checked,
                chunk_count,
                safety_interval_minutes=safety_interval_minutes,
            )
        )

    clear_connection_retrieval_progress(connection_id)
    logger.info("All tables processed")
    return aggregated_results


# ---------------------------------------------------------------------------
# Per-table orchestration
# ---------------------------------------------------------------------------

def _fetch_model_schema_for_table(
    container_client,
    base_folder,
    date_dir,
    table_name,
    updated_at,
    deleted_at,
):
    """Load Dynamics model.json attribute metadata for one table/date folder."""
    json_path = join_azure_blob_path(
        base_folder,
        date_dir,
        "Microsoft.Athena.TrickleFeedService",
        f"{table_name}-model.json",
    )
    json_blob = container_client.get_blob_client(json_path)
    if not json_blob.exists():
        return {}, {}, []
    schema_data = json.loads(json_blob.download_blob().readall())
    entities = schema_data.get("entities") or []
    attribute_details = {}
    attribute_details_field = {}
    attribute_names = []
    for entity in entities:
        attributes = entity.get("attributes") or []
        attribute_details = {a["name"]: a["dataType"] for a in attributes}
        for attr in attributes:
            field_name = attr["name"]
            field_type = attr["dataType"]
            max_length = attr.get("maxLength", None)
            attribute_details_field[field_name] = get_databricks_type(field_type, max_length)
        attribute_names = list(attribute_details.keys())
    if attribute_names:
        from services.dynamics.source_common import canonical_meta_column

        # Ensure sync columns exist for Spark forced-schema CSV reads (single canonical name).
        sync_col = canonical_meta_column(updated_at, "_FIVETRAN_SYNCED")
        del_col = canonical_meta_column(deleted_at, "_FIVETRAN_DELETED")
        for extra, dtype in ((sync_col, "TIMESTAMP"), (del_col, "BOOLEAN")):
            if extra not in attribute_details_field:
                attribute_details_field[extra] = dtype
            if extra not in attribute_names:
                attribute_names.append(extra)
    return attribute_details, attribute_details_field, attribute_names


def _resolve_model_schema_for_table(
    container_client,
    base_folder,
    table_name,
    updated_at,
    deleted_at,
    *,
    preferred_dirs: list[str] | None = None,
    candidate_dirs: list[str] | None = None,
    detail_log=None,
):
    """
    Find a date folder whose ``{table}-model.json`` exists and return its attributes.

    Bulk / reuse paths must not assume ``folders_to_process[0]`` has model.json —
    early watermark dirs are often empty. Without attribute names, Spark falls back
    to CSV headers and headerless shards turn data rows into column names (no ``ID``).
    """
    tried: list[str] = []
    seen: set[str] = set()

    def _try(date_dir: str):
        key = str(date_dir or "").strip()
        if not key or key in seen:
            return None
        seen.add(key)
        tried.append(key)
        details, fields, names = _fetch_model_schema_for_table(
            container_client,
            base_folder,
            key,
            table_name,
            updated_at,
            deleted_at,
        )
        if names:
            return key, details, fields, names
        return None

    # Prefer dirs that actually landed files, then newest candidates (more likely present).
    for date_dir in list(preferred_dirs or []):
        hit = _try(date_dir)
        if hit:
            if detail_log:
                detail_log.log(
                    f"spark_job model_schema_ok table={table_name} date_dir={hit[0]!r} "
                    f"attrs={len(hit[3])}"
                )
            return hit

    candidates = list(candidate_dirs or [])
    for date_dir in reversed(candidates):
        hit = _try(date_dir)
        if hit:
            if detail_log:
                detail_log.log(
                    f"spark_job model_schema_ok table={table_name} date_dir={hit[0]!r} "
                    f"attrs={len(hit[3])} (scanned newest-first)"
                )
            return hit

    if detail_log:
        detail_log.log(
            f"spark_job model_schema_missing table={table_name} tried={len(tried)} "
            f"sample={tried[:5]!r}"
        )
    return None, {}, {}, []


def _process_table_spark_job(
    table_name,
    connection_string,
    container_name,
    base_folder,
    date_directories,
    container_client,
    connection_id,
    cmp_id,
    usr_id,
    session,
    updated_at,
    deleted_at,
    destination_connection,
    last_checked_date,
    job_name,
    migration_session_id,
    table_fields,
    safety_interval_minutes,
    detail_log,
    is_initial_load=False,
):
    """Run Databricks Spark loads (bulk staging copy + one job, or per-folder loop)."""
    destination_engine = None
    databricks_schema = ""
    start_time = datetime.now()
    all_total_rows = 0
    existing_fields = (table_fields or {}).get(table_name) or {}
    primary_key = "ID"

    try:
        destination_engine, databricks_schema = destination_connection_function(
            destination_connection,
            connection_id,
            cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
        )
        catalog = str((destination_connection or {}).get("catalog") or "").strip()
        table_exist = check_table_exists_databricks(
            destination_engine,
            table_name,
            destination_connection,
            connection_id,
            cmp_id,
        )
        detail_log.log(f"spark_job path table_exist={table_exist}")
        if not table_exist and last_checked_date.get(table_name):
            logger.warning(
                "DBX|spark_job|missing_base_force_initial | table=%s | connection_id=%s | "
                "session=%s | cleared_stale_watermark=%s",
                table_name,
                connection_id,
                migration_session_id,
                last_checked_date.get(table_name),
            )
            last_checked_date[table_name] = None
            is_initial_load = True

        update_table_status(
            connection_id,
            table_name,
            "in_progress",
            last_checked_date,
            job_name,
            migration_session_id,
            existing_fields,
            cmp_id,
            table_detail_log=detail_log,
        )

        landing_cloud = resolve_spark_landing_cloud(destination_connection)
        staging_read_mode = resolve_spark_staging_read_mode(
            landing_cloud, destination_connection
        )
        staging_load_strategy = (
            resolve_spark_staging_load_strategy(landing_cloud, destination_connection)
            if staging_read_mode == AZURE_SPARK_READ_MODE_STAGING
            else None
        )
        # Keep Azure aliases for log/compat with existing tests and ops tooling.
        azure_spark_read_mode = (
            staging_read_mode if landing_cloud == SPARK_LANDING_CLOUD_AZURE else None
        )
        azure_spark_load_strategy = (
            staging_load_strategy if landing_cloud == SPARK_LANDING_CLOUD_AZURE else None
        )
        detail_log.log(
            f"spark_job landing_cloud={landing_cloud} "
            f"staging_read_mode={staging_read_mode} "
            f"staging_load_strategy={staging_load_strategy} "
            f"azure_spark_read_mode={azure_spark_read_mode} "
            f"azure_spark_load_strategy={azure_spark_load_strategy}"
        )

        spark_job_info = ensure_dynamics_spark_job(
            destination_connection,
            destination_engine,
            connection_id=connection_id,
            cmp_id=cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
            landing_cloud=landing_cloud,
            container_name=container_name,
            connection_string=connection_string,
            base_folder=base_folder,
        )
        job_id = int(spark_job_info["job_id"])
        spark_staging_access = str(
            spark_job_info.get("spark_staging_access") or ""
        ).strip().lower()
        detail_log.log(
            f"spark_job ensured job_id={job_id} landing_cloud={spark_job_info.get('landing_cloud')} "
            f"spark_staging_access={spark_staging_access or 'external_location'}"
        )

        final_dir = date_directories[-1] if date_directories else None
        if not final_dir:
            raise RuntimeError(f"No date folders for table '{table_name}'")

        constant_last_checked = last_checked_date.get(table_name)
        start_processing = last_checked_date.get(table_name, None) is None
        folders_considered = 0
        folders_with_data = 0
        last_valid_dir = last_checked_date.get(table_name)
        folders_to_process: list[str] = []

        use_bulk_staging = (
            landing_cloud
            in (
                SPARK_LANDING_CLOUD_AZURE,
                SPARK_LANDING_CLOUD_GCS,
                SPARK_LANDING_CLOUD_S3,
            )
            and staging_read_mode == AZURE_SPARK_READ_MODE_STAGING
            and staging_load_strategy == AZURE_SPARK_LOAD_STRATEGY_BULK
        )

        # Watermark filter only. Bulk path skips directory_exists (list_table_csv_blobs
        # inside land_table_bulk_to_adls_staging does the real existence check in parallel).
        candidate_dirs: list[str] = []
        for date_dir in date_directories:
            if not (
                start_processing
                or is_directory_greater_than(date_dir, constant_last_checked)
            ):
                continue
            folders_considered += 1
            candidate_dirs.append(date_dir)

        if use_bulk_staging:
            folders_to_process = candidate_dirs
            detail_log.log(
                f"spark_job bulk_skip_dir_exists_scan candidates={len(folders_to_process)}"
            )
        else:
            for date_dir in candidate_dirs:
                if not directory_exists_in_date_directory(
                    container_client,
                    base_folder,
                    date_dir,
                    table_name,
                    cmp_id,
                    connection_id,
                ):
                    detail_log.log(f"spark_job skip_no_dir date_dir={date_dir!r}")
                    continue
                folders_to_process.append(date_dir)

        if use_bulk_staging and folders_to_process:
            force_recopy = str(
                os.environ.get("DYNAMICS_DBX_BULK_FORCE_RECOPY", "") or ""
            ).strip().lower() in {"1", "true", "yes"}
            reuse_staging = resolve_bulk_staging_reuse_enabled()
            allow_staging_reuse = bool(reuse_staging and not force_recopy and table_exist)

            existing_csv_count = 0
            existing_prefix = ""
            if allow_staging_reuse:
                if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                    existing_csv_count, existing_prefix = count_adls_staging_csv_blobs(
                        connection_id, table_name
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    existing_csv_count, existing_prefix = count_s3_staging_csv_blobs(
                        connection_id, table_name
                    )
                else:
                    existing_csv_count, existing_prefix = count_gcs_staging_csv_blobs(
                        connection_id, table_name
                    )
            else:
                if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                    deleted_pre = cleanup_adls_staging_after_spark_merge(
                        connection_id,
                        table_name,
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    deleted_pre = cleanup_s3_staging_after_spark_merge(
                        connection_id,
                        table_name,
                    )
                else:
                    deleted_pre = cleanup_gcs_staging_after_spark_merge(
                        connection_id,
                        table_name,
                    )
                detail_log.log(
                    f"spark_job bulk_pre_staging_cleanup cloud={landing_cloud} "
                    f"blobs_deleted={deleted_pre} "
                    f"(reuse disabled; set DYNAMICS_DBX_BULK_REUSE_STAGING=1 to resume copy)"
                )

            # Opt-in only: skip re-copy when staging CSVs remain from a prior failed run.
            if existing_csv_count > 0 and allow_staging_reuse:
                if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                    staging_url = str(DATASYNCHER_AZURE_STAGING_URL or "").strip()
                    landing_prefix = dynamics_adls_staging_table_landing_prefix(
                        connection_id, table_name, staging_url
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    landing_prefix = dynamics_s3_staging_table_landing_prefix(
                        connection_id, table_name
                    )
                else:
                    landing_prefix = dynamics_gcs_staging_table_landing_prefix(
                        connection_id, table_name, bucket_name=BUCKET_NAME
                    )
                landed_files = [
                    {"date_dir": "", "staging_blob": f"reuse:{i}"}
                    for i in range(existing_csv_count)
                ]
                folders_with_data = existing_csv_count
                last_valid_dir = folders_to_process[-1]
                detail_log.log(
                    f"spark_job bulk_reuse_staging cloud={landing_cloud} "
                    f"csv_count={existing_csv_count} "
                    f"prefix={existing_prefix!r} landing_prefix={landing_prefix!r} "
                    f"(skip copy; DYNAMICS_DBX_BULK_REUSE_STAGING=1)"
                )
            else:
                detail_log.log(
                    f"spark_job bulk_staging_copy_start cloud={landing_cloud} "
                    f"folders={len(folders_to_process)}"
                    + (
                        f" force_recopy existing_csvs={existing_csv_count}"
                        if force_recopy
                        else ""
                    )
                )
                if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                    landing_prefix, landed_files, folders_with_data, last_valid_dir = (
                        land_table_bulk_to_adls_staging(
                            container_client,
                            base_folder,
                            folders_to_process,
                            table_name,
                            connection_id,
                            lease_refresh_job_name=job_name,
                        )
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    landing_prefix, landed_files, folders_with_data, last_valid_dir = (
                        land_table_bulk_to_s3_staging(
                            container_client,
                            base_folder,
                            folders_to_process,
                            table_name,
                            connection_id,
                            lease_refresh_job_name=job_name,
                        )
                    )
                else:
                    landing_prefix, landed_files, folders_with_data, last_valid_dir = (
                        land_table_bulk_to_gcs_staging(
                            container_client,
                            base_folder,
                            folders_to_process,
                            table_name,
                            connection_id,
                            bucket_name=BUCKET_NAME,
                            lease_refresh_job_name=job_name,
                        )
                    )
                detail_log.log(
                    f"spark_job bulk_staging_copy_done cloud={landing_cloud} "
                    f"folders_with_data={folders_with_data} "
                    f"files={len(landed_files)} landing_prefix={landing_prefix!r}"
                )

            if landed_files:
                preferred_schema_dirs = []
                for item in landed_files:
                    d = str((item or {}).get("date_dir") or "").strip()
                    if d and not d.startswith("reuse:") and d not in preferred_schema_dirs:
                        preferred_schema_dirs.append(d)
                schema_dir, attribute_details, attribute_details_field, attribute_names = (
                    _resolve_model_schema_for_table(
                        container_client,
                        base_folder,
                        table_name,
                        updated_at,
                        deleted_at,
                        preferred_dirs=preferred_schema_dirs,
                        candidate_dirs=folders_to_process,
                        detail_log=detail_log,
                    )
                )
                if not attribute_names:
                    raise RuntimeError(
                        f"Bulk Spark requires {table_name}-model.json attribute headers; "
                        f"none found under preferred={preferred_schema_dirs[:3]!r} "
                        f"or {len(folders_to_process)} candidate date dirs. "
                        "Refusing CSV header inference (headerless shards become fake columns)."
                    )
                if attribute_details_field and not table_exist:
                    schema_df = pd.DataFrame(
                        columns=attribute_names or list(attribute_details_field)
                    )
                    ensure_databricks_base_table_exists(
                        destination_engine,
                        table_name,
                        databricks_schema,
                        schema_df,
                        attribute_details_field,
                        connection_id,
                        cmp_id,
                        destination_connection=destination_connection,
                    )
                    table_exist = True

                if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                    staging_csv_count = verify_adls_staging_has_csvs(
                        connection_id,
                        table_name,
                        expected_count=len(landed_files),
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    staging_csv_count = verify_s3_staging_has_csvs(
                        connection_id,
                        table_name,
                        expected_count=len(landed_files),
                    )
                else:
                    staging_csv_count = verify_gcs_staging_has_csvs(
                        connection_id,
                        table_name,
                        expected_count=len(landed_files),
                    )
                detail_log.log(
                    f"spark_job bulk_staging_preflight_ok cloud={landing_cloud} "
                    f"csv_count={staging_csv_count} landed_files={len(landed_files)} "
                    f"spark_staging_access={spark_staging_access or 'external_location'}"
                )

                # Warehouse LIST only works through UC external locations. Scoped-credential
                # classic clusters authenticate via spark_conf; the Spark job itself is the
                # read proof.
                if spark_staging_access == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL:
                    detail_log.log(
                        "spark_job bulk_uc_list_skipped reason=scoped_credential "
                        f"landing_prefix={landing_prefix!r}"
                    )
                else:
                    if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                        uc_list = verify_databricks_adls_prefix_readable(
                            destination_engine,
                            landing_prefix.rstrip("/"),
                            require_entries=True,
                        )
                    elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                        uc_list = verify_databricks_s3_prefix_readable(
                            destination_engine,
                            landing_prefix.rstrip("/"),
                            require_entries=True,
                        )
                    else:
                        uc_list = verify_databricks_gcs_prefix_readable(
                            destination_engine,
                            landing_prefix.rstrip("/"),
                            require_entries=True,
                        )
                    if not uc_list.get("ok"):
                        err = (
                            uc_list.get("error")
                            or uc_list.get("skipped_reason")
                            or "LIST failed"
                        )
                        raise RuntimeError(
                            f"Databricks cannot LIST staging landing_prefix={landing_prefix!r} "
                            f"after bulk copy (cloud={landing_cloud} listed {staging_csv_count} CSVs). "
                            f"UC/external-location issue: {err}"
                        )
                    detail_log.log(
                        f"spark_job bulk_uc_list_ok sample_entries={uc_list.get('entry_count')}"
                    )

                schema_columns = ",".join(
                    str(c) for c in (attribute_names or []) if str(c).strip()
                )
                detail_log.log(
                    f"spark_job bulk_schema_columns count={len(attribute_names)} "
                    f"schema_dir={schema_dir!r} sample={attribute_names[:8]!r}"
                )
                notebook_params = {
                    "catalog": catalog,
                    "schema": databricks_schema,
                    "table_name": table_name,
                    "landing_prefix": landing_prefix.rstrip("/"),
                    "primary_key": primary_key,
                    "updated_at_col": updated_at,
                    "deleted_at_col": deleted_at,
                    "connection_id": str(connection_id),
                    "migration_session_id": str(migration_session_id),
                    "schema_columns": schema_columns,
                    # Bulk initial: COUNT base table after MERGE (avoids slow staging scan).
                    "is_initial_load": (
                        "true"
                        if (
                            bool(is_initial_load)
                            or not last_checked_date.get(table_name)
                        )
                        else "false"
                    ),
                    "staging_cleanup_after_merge": _spark_staging_cleanup_notebook_param(
                        landing_cloud
                    ),
                }
                bulk_timeout = resolve_spark_bulk_job_timeout_seconds(destination_connection)
                detail_log.log(f"spark_job bulk_merge_start timeout_s={bulk_timeout}")
                run_result = submit_and_wait(
                    destination_engine,
                    job_id,
                    notebook_params,
                    timeout_seconds=bulk_timeout,
                    on_poll=lambda: refresh_pipeline_flow_fleet_lease(job_name=job_name),
                )
                spark_rows = int(run_result.get("row_count") or 0)
                file_count = len(landed_files)
                # Never report CSV file count as migrated rows — that produced the
                # false "7646 rows" when notebook_output.result was missing/unparsed.
                if spark_rows <= 0:
                    detail_log.log(
                        f"spark_job bulk_merge_row_count_missing "
                        f"run_id={run_result.get('run_id')} files={file_count} "
                        f"(Spark row count unavailable; not substituting file count)"
                    )
                    merged_rows = 0
                else:
                    merged_rows = spark_rows
                all_total_rows += merged_rows
                detail_log.log(
                    f"spark_job bulk_merge_success run_id={run_result.get('run_id')} "
                    f"rows={merged_rows} files={file_count}"
                )
                if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                    deleted_blobs = cleanup_adls_staging_after_spark_merge(
                        connection_id,
                        table_name,
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    deleted_blobs = cleanup_s3_staging_after_spark_merge(
                        connection_id,
                        table_name,
                    )
                else:
                    deleted_blobs = cleanup_gcs_staging_after_spark_merge(
                        connection_id,
                        table_name,
                    )
                detail_log.log(
                    f"spark_job bulk_staging_cleanup cloud={landing_cloud} "
                    f"blobs_deleted={deleted_blobs}"
                )
            else:
                detail_log.log("spark_job bulk_staging_no_files")

        elif not use_bulk_staging:
            for date_dir in folders_to_process:
                attribute_details, attribute_details_field, attribute_names = (
                    _fetch_model_schema_for_table(
                        container_client,
                        base_folder,
                        date_dir,
                        table_name,
                        updated_at,
                        deleted_at,
                    )
                )
                if attribute_details_field and not table_exist:
                    schema_df = pd.DataFrame(columns=attribute_names or list(attribute_details_field))
                    ensure_databricks_base_table_exists(
                        destination_engine,
                        table_name,
                        databricks_schema,
                        schema_df,
                        attribute_details_field,
                        connection_id,
                        cmp_id,
                        destination_connection=destination_connection,
                    )
                    table_exist = True

                if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
                    if staging_read_mode == AZURE_SPARK_READ_MODE_STAGING:
                        landing_prefix, landed_files = land_table_date_dir_to_adls_staging(
                            container_client,
                            base_folder,
                            date_dir,
                            table_name,
                            connection_id,
                        )
                    else:
                        landing_prefix, landed_files = discover_table_date_dir_for_spark_direct(
                            container_client,
                            container_name,
                            connection_string,
                            base_folder,
                            date_dir,
                            table_name,
                        )
                    manifest_uri = None
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    landing_prefix, landed_files = land_table_date_dir_to_s3(
                        container_client,
                        base_folder,
                        date_dir,
                        table_name,
                        connection_id,
                    )
                    manifest_uri = None
                else:
                    landed_files = land_table_date_dir_to_gcs(
                        container_client,
                        base_folder,
                        date_dir,
                        table_name,
                        connection_id,
                    )
                    landing_prefix = None
                    manifest_uri = None

                if not landed_files:
                    detail_log.log(f"spark_job no_csv_files date_dir={date_dir!r}")
                    start_processing = True
                    last_valid_dir = date_dir
                    continue

                if landing_cloud == SPARK_LANDING_CLOUD_GCS:
                    manifest_uri, landing_prefix = write_landing_manifest(
                        connection_id,
                        table_name,
                        date_dir,
                        landed_files,
                        extra={
                            "primary_key": primary_key,
                            "updated_at_col": updated_at,
                            "deleted_at_col": deleted_at,
                            "catalog": catalog,
                            "schema": databricks_schema,
                        },
                    )
                    detail_log.log(
                        f"spark_job landed date_dir={date_dir!r} files={len(landed_files)} "
                        f"manifest={manifest_uri!r}"
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    detail_log.log(
                        f"spark_job s3_staging_copy date_dir={date_dir!r} "
                        f"files={len(landed_files)} landing_prefix={landing_prefix!r}"
                    )
                else:
                    read_label = (
                        "staging_copy"
                        if staging_read_mode == AZURE_SPARK_READ_MODE_STAGING
                        else "direct_read"
                    )
                    detail_log.log(
                        f"spark_job {read_label} date_dir={date_dir!r} files={len(landed_files)} "
                        f"landing_prefix={landing_prefix!r}"
                    )

                schema_columns = ",".join(
                    str(c) for c in (attribute_names or []) if str(c).strip()
                )
                notebook_params = {
                    "catalog": catalog,
                    "schema": databricks_schema,
                    "table_name": table_name,
                    "landing_prefix": landing_prefix.rstrip("/"),
                    "primary_key": primary_key,
                    "updated_at_col": updated_at,
                    "deleted_at_col": deleted_at,
                    "connection_id": str(connection_id),
                    "migration_session_id": str(migration_session_id),
                    "schema_columns": schema_columns,
                    # Per-folder always uses MERGE metrics / staging COUNT (not base table —
                    # base COUNT would over-count across folders on initial load).
                    "is_initial_load": "false",
                    "staging_cleanup_after_merge": _spark_staging_cleanup_notebook_param(
                        landing_cloud
                    ),
                }
                run_result = submit_and_wait(
                    destination_engine,
                    job_id,
                    notebook_params,
                    on_poll=lambda: refresh_pipeline_flow_fleet_lease(job_name=job_name),
                )
                spark_rows = int(run_result.get("row_count") or 0)
                file_count = len(landed_files)
                if spark_rows <= 0:
                    detail_log.log(
                        f"spark_job row_count_missing date_dir={date_dir!r} "
                        f"run_id={run_result.get('run_id')} files={file_count} "
                        f"(not substituting file count as rows)"
                    )
                    merged_rows = 0
                else:
                    merged_rows = spark_rows
                all_total_rows += merged_rows
                folders_with_data += 1
                start_processing = True
                last_valid_dir = date_dir
                last_checked_date[table_name] = last_valid_dir
                detail_log.log(
                    f"spark_job success date_dir={date_dir!r} "
                    f"run_id={run_result.get('run_id')} rows={merged_rows} files={file_count}"
                )
                if (
                    landing_cloud == SPARK_LANDING_CLOUD_AZURE
                    and staging_read_mode == AZURE_SPARK_READ_MODE_STAGING
                ):
                    deleted_blobs = cleanup_adls_staging_after_spark_merge(
                        connection_id,
                        table_name,
                        date_dir=date_dir,
                    )
                    detail_log.log(
                        f"spark_job staging_cleanup date_dir={date_dir!r} "
                        f"blobs_deleted={deleted_blobs}"
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_GCS:
                    deleted_blobs = cleanup_gcs_staging_after_spark_merge(
                        connection_id,
                        table_name,
                        date_dir=date_dir,
                    )
                    detail_log.log(
                        f"spark_job staging_cleanup cloud=gcs date_dir={date_dir!r} "
                        f"blobs_deleted={deleted_blobs}"
                    )
                elif landing_cloud == SPARK_LANDING_CLOUD_S3:
                    deleted_blobs = cleanup_s3_staging_after_spark_merge(
                        connection_id,
                        table_name,
                        date_dir=date_dir,
                    )
                    detail_log.log(
                        f"spark_job staging_cleanup cloud=s3 date_dir={date_dir!r} "
                        f"blobs_deleted={deleted_blobs}"
                    )

        from utils.load_checkpoint import finalize_migration_record

        migrated_rows = int(all_total_rows or 0)
        finalize_migration_record(
            connection_id=connection_id,
            table_name=table_name,
            migration_session_id=migration_session_id,
            job_name=job_name,
            final_records_count=migrated_rows,
        )

        last_checked_date[table_name] = final_dir
        last_checked_date = apply_data_max_watermark(last_checked_date, table_name)
        end_time = datetime.now()
        column_size = len(existing_fields) if existing_fields else 0
        data_transfer_logs(
            cmp_id,
            connection_id,
            usr_id,
            table_name,
            start_time,
            end_time,
            migrated_rows,
            0,
            0,
            column_size,
            session,
            job_name=job_name,
        )
        logger.warning(
            "DBX|spark_job|process_table_done | table=%s | connection_id=%s | "
            "session=%s | folders_considered=%s | folders_with_data=%s | rows=%s",
            table_name,
            connection_id,
            migration_session_id,
            folders_considered,
            folders_with_data,
            migrated_rows,
        )
        update_table_status(
            connection_id,
            table_name,
            "completed",
            last_checked_date,
            job_name,
            migration_session_id,
            existing_fields,
            cmp_id,
            table_detail_log=detail_log,
        )
        return migrated_rows, migrated_rows, 0, 0, column_size, last_checked_date
    except DatabricksSparkJobError as exc:
        detail_log.log(f"spark_job failed: {exc!r}")
        raise
    finally:
        if destination_engine is not None:
            try:
                destination_engine.dispose()
            except Exception:
                pass


def process_table(
    table_name,
    connection_string,
    container_name,
    base_folder,
    date_directories,
    container_client,
    connection_id,
    cmp_id,
    usr_id,
    session,
    updated_at,
    deleted_at,
    destination_connection,
    last_checked_date,
    job_name,
    migration_session_id,
    table_fields,
    is_integration_checked,
    chunk_count,
    safety_interval_minutes=None,
):
    staging_table_name = None
    existing_fields = {}
    destination_engine = None
    databricks_schema = ""
    if safety_interval_minutes is None:
        safety_interval_minutes = 30

    detail_log = _TableMigrationDetailFileLog(
        table_name, connection_id, migration_session_id, job_name, cmp_id
    )
    if detail_log.enabled():
        gcs_uri = detail_log.gcs_target_uri()
        if gcs_uri:
            logger.warning(
                "TABLE_MIGRATION_DETAIL_FILE | local=%s | gcs=%s | table=%s | connection_id=%s",
                detail_log.path,
                gcs_uri,
                table_name,
                connection_id,
            )
        else:
            logger.warning(
                "TABLE_MIGRATION_DETAIL_FILE | path=%s | table=%s | connection_id=%s",
                detail_log.path,
                table_name,
                connection_id,
            )

    try:
        _vprint(
            f"process_table:start table={table_name} connection_id={connection_id} "
            f"job_name={job_name} migration_session_id={migration_session_id} "
            f"date_directories_count={len(date_directories) if date_directories else 0} "
            f"initial_last_checked={last_checked_date.get(table_name)}"
        )
        detail_log.log(
            "process_table:start "
            f"table={table_name!r} connection_id={connection_id} "
            f"migration_session_id={migration_session_id} "
            f"date_directories_count={len(date_directories) if date_directories else 0} "
            f"initial_last_checked={last_checked_date.get(table_name)!r}"
        )

        is_initial_load = not last_checked_date.get(table_name)
        load_engine = resolve_databricks_load_engine(
            source_type="dynamics",
            is_initial_load=is_initial_load,
            destination_connection=destination_connection,
        )
        if load_engine == LOAD_ENGINE_SPARK_JOB:
            logger.warning(
                "DBX|spark_job|process_table_start | table=%s | connection_id=%s | session=%s | "
                "is_initial_load=%s",
                table_name,
                connection_id,
                migration_session_id,
                is_initial_load,
            )
            detail_log.log(
                f"process_table: using load_engine=spark_job is_initial_load={is_initial_load}"
            )
            return _process_table_spark_job(
                table_name=table_name,
                connection_string=connection_string,
                container_name=container_name,
                base_folder=base_folder,
                date_directories=date_directories,
                container_client=container_client,
                connection_id=connection_id,
                cmp_id=cmp_id,
                usr_id=usr_id,
                session=session,
                updated_at=updated_at,
                deleted_at=deleted_at,
                destination_connection=destination_connection,
                last_checked_date=last_checked_date,
                job_name=job_name,
                migration_session_id=migration_session_id,
                table_fields=table_fields,
                safety_interval_minutes=safety_interval_minutes,
                detail_log=detail_log,
                is_initial_load=is_initial_load,
            )

        destination_engine, databricks_schema = destination_connection_function(
            destination_connection,
            connection_id,
            cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
        )
        logger.warning(
            "DBX|process_table_connected | table=%s | connection_id=%s | session=%s | schema=%s",
            table_name,
            connection_id,
            migration_session_id,
            databricks_schema,
        )
        detail_log.log(
            f"destination_connection ok databricks_schema={databricks_schema!r} "
            f"engine={type(destination_engine).__name__}"
        )

        reused_staging = lookup_session_staging_name(migration_session_id, table_name)
        from utils.load_checkpoint import (
            get_last_synced_for_table,
            get_migration_record_info,
            should_resume_migration,
        )

        migration_info = get_migration_record_info(
            connection_id=connection_id,
            table_name=table_name,
            migration_session_id=migration_session_id,
            job_name=job_name,
        )
        last_synced_cursor = get_last_synced_for_table(connection_id, table_name)
        resuming_failed = should_resume_migration(
            (migration_info or {}).get("status"),
            last_synced_cursor,
        )

        if reused_staging:
            staging_table_name = reused_staging
            detail_log.log(
                f"prechunk_staging_cleanup skipped reason=reuse_session_staging "
                f"staging_table_name={staging_table_name!r}"
            )
        elif resuming_failed and last_synced_cursor:
            detail_log.log(
                f"resume_from_last_synced cursor={last_synced_cursor!r} "
                f"migration_status={(migration_info or {}).get('status')!r}"
            )
            last_checked_date[table_name] = last_synced_cursor
            try:
                flush_orphan_staging_tables(
                    destination_engine,
                    databricks_schema,
                    table_name,
                    "ID",
                    [],
                    updated_at,
                    deleted_at,
                )
            except Exception as flush_exc:
                logger.warning(
                    "DBX|resume_staging_flush_failed | table=%s | err=%s",
                    table_name,
                    flush_exc,
                )
                raise

        if not staging_table_name:
            dropped_pre = drop_existing_ds_staging_tables_for_table(
                destination_engine,
                databricks_schema,
                table_name,
                migration_session_id=migration_session_id,
            )
            detail_log.log(
                f"prechunk_staging_cleanup dropped_legacy_staging_count={dropped_pre} "
                f"(protected in-use staging registered in Redis for {table_name!r})"
            )
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            staging_table_name = f"DS_{table_name.upper()}_STAGING_{timestamp}"
            remember_session_staging_name(
                migration_session_id, table_name, staging_table_name
            )
        touch_session_staging_name(migration_session_id, table_name)
        logger.warning(
            "STAGING_TABLE_ALLOCATED | staging=%s | table=%s | connection_id=%s | "
            "migration_session_id=%s | job_name=%s",
            staging_table_name,
            table_name,
            connection_id,
            migration_session_id,
            job_name,
        )
        detail_log.log(f"staging_table_name={staging_table_name!r}")

        all_total_rows = 0
        all_total_inserts = 0
        all_total_updates = 0
        all_total_deletes = 0
        column_size = 0
        df = pd.DataFrame()
        merge_columns_df = pd.DataFrame()

        table_name_final = None
        staging_table = staging_table_name
        primary_key = "ID"

        existing_fields = (table_fields or {}).get(table_name) or {}
        table_exist = check_table_exists_databricks(
            destination_engine,
            table_name,
            destination_connection,
            connection_id,
            cmp_id,
        )
        detail_log.log(f"check_table_exists_databricks table_exist={table_exist}")
        extra_fields = []
        modified_fields = {}

        update_table_status(
            connection_id,
            table_name,
            "in_progress",
            last_checked_date,
            job_name,
            migration_session_id,
            existing_fields,
            cmp_id,
            table_detail_log=detail_log,
        )

        initial_table_watermark = last_checked_date.get(table_name)
        is_initial_load = not initial_table_watermark

        if is_initial_load:
            last_checked_date[table_name] = None
            logger.warning(
                "DBX|table_initial_load | table=%s | connection_id=%s | session=%s | "
                "table_exist=%s | forcing full folder scan",
                table_name,
                connection_id,
                migration_session_id,
                table_exist,
            )
        elif not table_exist:
            last_checked_date[table_name] = None
            is_initial_load = True
            logger.warning(
                "DBX|table_missing_base_force_initial | table=%s | connection_id=%s | "
                "session=%s | cleared stale watermark (base table missing)",
                table_name,
                connection_id,
                migration_session_id,
            )
        final_dir = date_directories[-1] if date_directories else None
        if not final_dir:
            msg = (
                f"No date folders available for table '{table_name}' "
                f"(connection_id={connection_id}). Cannot load data."
            )
            logger.error("DBX|process_table_no_folders | table=%s | %s", table_name, msg)
            insert_error_log(cmp_id, connection_id, msg, "process_table_no_folders")
            raise RuntimeError(msg)
        constant_last_checked = last_checked_date.get(table_name)
        start_processing = last_checked_date.get(table_name, None) is None
        start_time = datetime.now()
        last_valid_dir = last_checked_date.get(table_name)
        folders_considered = 0
        folders_skipped_no_dir = 0
        folders_with_data = 0

        buffer_df = pd.DataFrame()
        buffer_row_count = 0
        attribute_details = {}
        attribute_details_field = {}
        attribute_names = []
        detail_log.log(
            f"date_dir_loop: final_dir={final_dir!r} start_processing={start_processing} "
            f"constant_last_checked={constant_last_checked!r}"
        )

        for date_dir in date_directories:
            if start_processing or is_directory_greater_than(
                date_dir, constant_last_checked
            ):
                folders_considered += 1
                detail_log.log(
                    f"date_dir: consider date_dir={date_dir!r} start_processing={start_processing} "
                    f"constant_last_checked={constant_last_checked!r}"
                )
                table_dir_path = (
                    join_azure_blob_path(base_folder, date_dir, table_name) + "/"
                )
                if not directory_exists_in_date_directory(
                    container_client,
                    base_folder,
                    date_dir,
                    table_name,
                    cmp_id,
                    connection_id,
                ):
                    folders_skipped_no_dir += 1
                    print(
                        f"[DIR_CHECK] not found in directory: table={table_name} path={table_dir_path}"
                    )
                    detail_log.log(f"DIR_CHECK not_found path={table_dir_path!r}")
                    json_path = join_azure_blob_path(
                        base_folder,
                        date_dir,
                        "Microsoft.Athena.TrickleFeedService",
                        f"{table_name}-model.json",
                    )
                    json_blob = container_client.get_blob_client(json_path)

                    if json_blob.exists() and not table_exist:
                        blob_content = json_blob.download_blob().readall()
                        schema_data = json.loads(blob_content)
                        # Some upstream schema JSON may contain explicit nulls.
                        entities = schema_data.get("entities") or []

                        for entity in entities:
                            attributes = entity.get("attributes") or []
                            attribute_details = {}
                            attribute_details_field = {}

                            for attr in attributes:
                                field_name = attr["name"]
                                field_type = attr["dataType"]
                                max_length = attr.get("maxLength", None)

                                dbx_type = get_databricks_type(field_type, max_length)
                                attribute_details = {
                                    a["name"]: a["dataType"] for a in attributes
                                }
                                attribute_details_field[field_name] = dbx_type
                            attribute_details_field[updated_at] = "TIMESTAMP"
                            attribute_details_field[deleted_at] = "BOOLEAN"
                            attribute_names = list(attribute_details.keys())

                            df = pd.DataFrame(columns=attribute_names)

                            if table_exist:
                                df, modified_fields, extra_fields = (
                                    align_fields_with_existing(
                                        df,
                                        attribute_details_field,
                                        existing_fields,
                                        table_name,
                                    )
                                )

                                if modified_fields:
                                    _vprint(
                                        f"Modified fields for table '{table_name}': {modified_fields}"
                                    )
                                    for field, new_type in modified_fields.items():
                                        existing_fields[field] = new_type

                                if extra_fields:
                                    _vprint(
                                        f"Extra fields for table '{table_name}': {extra_fields}"
                                    )
                                    for field in extra_fields:
                                        existing_fields[field] = (
                                            attribute_details_field[field]
                                        )
                            else:
                                existing_fields = attribute_details_field

                            process_file_in_chunks(
                                connection_string,
                                container_name,
                                base_folder,
                                date_dir,
                                container_client,
                                df,
                                {table_name: table_dir_path},
                                {table_name: json_path},
                                attribute_details,
                                table_name,
                                destination_engine,
                                connection_id,
                                updated_at,
                                deleted_at,
                                destination_connection,
                                table_exist,
                                is_integration_checked,
                                extra_fields,
                                modified_fields,
                                existing_fields,
                                attribute_details_field,
                                buffer_df,
                                buffer_row_count,
                                final_dir,
                                cmp_id,
                                chunk_count,
                                migration_session_id,
                                job_name,
                                staging_table_name=staging_table_name,
                                table_detail_log=detail_log,
                                usr_id=usr_id,
                                session=session,
                                folder_checkpoint=date_dir,
                                safety_interval_minutes=safety_interval_minutes,
                            )

                        table_exist = True

                    if date_dir != final_dir:
                        continue
                    elif buffer_row_count == 0:
                        continue

                if not table_exist:
                    table_exist = check_table_exists_databricks(
                        destination_engine,
                        table_name,
                        destination_connection,
                        connection_id,
                        cmp_id,
                    )
                print(
                    f"[DIR_CHECK] found in directory: table={table_name} date_dir={date_dir} path={table_dir_path}"
                )
                detail_log.log(
                    f"DIR_CHECK found date_dir={date_dir!r} path={table_dir_path!r}"
                )
                folderpath_dict = {table_name: table_dir_path}
                json_path = join_azure_blob_path(
                    base_folder,
                    date_dir,
                    "Microsoft.Athena.TrickleFeedService",
                    f"{table_name}-model.json",
                )
                blob_client = container_client.get_blob_client(json_path)
                if not blob_client.exists():
                    detail_log.log(
                        f"model_json_missing skip_folder date_dir={date_dir!r} path={json_path!r}"
                    )
                    continue
                blob_content = blob_client.download_blob().readall()
                schema_data = json.loads(blob_content)

                entities = schema_data.get("entities") or []
                for entity in entities:
                    attributes = entity.get("attributes") or []
                    attribute_details = {}
                    attribute_details_field = {}

                    for attr in attributes:
                        field_name = attr["name"]
                        field_type = attr["dataType"]
                        max_length = attr.get("maxLength", None)

                        dbx_type = get_databricks_type(field_type, max_length)
                        attribute_details = {
                            a["name"]: a["dataType"] for a in attributes
                        }
                        attribute_details_field[field_name] = dbx_type
                    attribute_details_field[updated_at] = "TIMESTAMP"
                    attribute_details_field[deleted_at] = "BOOLEAN"
                    attribute_names = list(attribute_details.keys())

                    df = pd.DataFrame(columns=attribute_names)

                    if table_exist:
                        df, modified_fields, extra_fields = align_fields_with_existing(
                            df,
                            attribute_details_field,
                            existing_fields,
                            table_name,
                        )
                    else:
                        existing_fields = attribute_details_field

                    (
                        total_rows,
                        total_inserts,
                        total_updates,
                        total_delete_recids,
                        column_size,
                        buffer_df,
                        buffer_row_count,
                        existing_fields,
                        table_name_final,
                        staging_table,
                        primary_key,
                        chunk_schema_df,
                    ) = process_file_in_chunks(
                        connection_string,
                        container_name,
                        base_folder,
                        date_dir,
                        container_client,
                        df,
                        folderpath_dict,
                        list(attribute_details.keys()),
                        attribute_details,
                        table_name,
                        destination_engine,
                        connection_id,
                        updated_at,
                        deleted_at,
                        destination_connection,
                        table_exist,
                        is_integration_checked,
                        extra_fields,
                        modified_fields,
                        existing_fields,
                        attribute_details_field,
                        buffer_df,
                        buffer_row_count,
                        final_dir,
                        cmp_id,
                        chunk_count,
                        migration_session_id,
                        job_name,
                        staging_table_name=staging_table_name,
                        table_detail_log=detail_log,
                        usr_id=usr_id,
                        session=session,
                        folder_checkpoint=date_dir,
                        safety_interval_minutes=safety_interval_minutes,
                    )
                    merge_columns_df = _remember_merge_schema_df(
                        merge_columns_df, chunk_schema_df
                    )

                    all_total_rows += total_rows
                    all_total_inserts += total_inserts
                    all_total_updates += total_updates
                    all_total_deletes += total_delete_recids
                    if total_rows:
                        folders_with_data += 1

                start_processing = True
                last_valid_dir = date_dir
                last_checked_date[table_name] = last_valid_dir

        # End-of-job tail flush: always move remaining buffer rows into staging before MERGE.
        if buffer_row_count > 0:
            tail_note = (
                "tail_below_chunk_cap"
                if buffer_row_count < chunk_count
                else "tail_at_chunk_cap"
            )
            logger.warning(
                "[MERGE] process_table: flushing %s buffer rows=%s chunk_count=%s before merge",
                tail_note,
                buffer_row_count,
                chunk_count,
            )
            detail_log.log(
                f"process_table: end_flush {tail_note} rows={buffer_row_count} "
                f"chunk_count={chunk_count}"
            )
            (
                data,
                inserts,
                deletes,
                updates,
                databricks_schema,
                table_name_final,
                staging_table,
                primary_key,
                tail_schema_df,
            ) = table_creation(
                df,
                buffer_df,
                attribute_names,
                attribute_details,
                table_name,
                destination_engine,
                connection_id,
                updated_at,
                deleted_at,
                datetime.now(pytz.utc),
                True,
                1,
                destination_connection,
                table_exist,
                is_integration_checked,
                extra_fields,
                modified_fields,
                existing_fields,
                attribute_details_field,
                cmp_id,
                migration_session_id,
                job_name,
                staging_table_name=staging_table_name,
                usr_id=usr_id,
                session=session,
                folder_checkpoint=last_valid_dir or final_dir,
                safety_interval_minutes=safety_interval_minutes,
            )
            merge_columns_df = _remember_merge_schema_df(
                merge_columns_df, tail_schema_df
            )
            all_total_rows += len(data) if data is not None else 0
            all_total_inserts += inserts
            all_total_updates += updates
            all_total_deletes += deletes
            buffer_df = pd.DataFrame()
            buffer_row_count = 0

        staging_rows_before_merge = _count_staging_table_rows(
            destination_engine,
            databricks_schema,
            staging_table_name,
        )
        merge_df = merge_columns_df
        if merge_df.columns.empty and staging_rows_before_merge and staging_rows_before_merge > 0:
            merge_df = _merge_schema_df_from_staging(
                destination_engine,
                databricks_schema,
                staging_table_name,
            )
        detail_log.log(
            "end_of_table_cleanup "
            f"staging_rows={staging_rows_before_merge} "
            f"merge_df_cols={len(merge_df.columns) if hasattr(merge_df, 'columns') else 0} "
            f"all_total_rows={all_total_rows}"
        )
        if staging_rows_before_merge and staging_rows_before_merge > 0:
            if merge_df.columns.empty:
                raise RuntimeError(
                    f"Cannot safety-flush {databricks_schema}.{staging_table_name}: "
                    f"staging has {staging_rows_before_merge} rows but no column schema"
                )
            updated_col, deleted_col = None, None
            try:
                from services.databricks.load_helpers import (
                    _merge_staging_to_base,
                    _resolve_merge_timestamp_columns,
                )

                updated_col, deleted_col = _resolve_merge_timestamp_columns(
                    list(merge_df.columns), updated_at, deleted_at
                )
                _merge_staging_to_base(
                    destination_engine,
                    (destination_connection or {}).get("catalog"),
                    databricks_schema,
                    table_name,
                    staging_table_name,
                    list(merge_df.columns),
                    primary_key,
                    updated_col,
                    deleted_col,
                )
                detail_log.log(
                    f"safety_flush_staging staging={staging_table_name!r} "
                    f"rows={staging_rows_before_merge}"
                )
            except Exception as flush_exc:
                logger.warning(
                    "DBX|end_safety_flush_failed | table=%s | staging=%s | err=%s",
                    table_name,
                    staging_table_name,
                    flush_exc,
                )
                raise
        try:
            if staging_table_name and destination_engine and databricks_schema:
                drop_staging_table(
                    destination_engine, staging_table_name, databricks_schema
                )
                detail_log.log(
                    f"dropped_staging_after_table staging={staging_table_name!r}"
                )
        except Exception as drop_exc:
            logger.warning(
                "DBX|drop_staging_after_table_failed | table=%s | staging=%s | err=%s",
                table_name,
                staging_table_name,
                drop_exc,
            )

        from utils.load_checkpoint import (
            finalize_migration_record,
            get_migration_record_staging_count,
        )

        migrated_rows = get_migration_record_staging_count(
            connection_id=connection_id,
            table_name=table_name,
            migration_session_id=migration_session_id,
            job_name=job_name,
        )
        if migrated_rows <= 0:
            migrated_rows = int(all_total_rows or 0)
        finalize_migration_record(
            connection_id=connection_id,
            table_name=table_name,
            migration_session_id=migration_session_id,
            job_name=job_name,
            final_records_count=migrated_rows,
        )
        log_inserts = migrated_rows
        log_updates = 0
        log_deletes = 0
        logger.warning(
            "DBX|folder_scan_summary | table=%s | connection_id=%s | session=%s | "
            "initial_load=%s | folders_total=%s | folders_considered=%s | "
            "folders_skipped_no_dir=%s | folders_with_data=%s | rows=%s",
            table_name,
            connection_id,
            migration_session_id,
            is_initial_load,
            len(date_directories),
            folders_considered,
            folders_skipped_no_dir,
            folders_with_data,
            all_total_rows,
        )
        detail_log.log(
            f"folder_scan_summary initial_load={is_initial_load} "
            f"folders_total={len(date_directories)} folders_considered={folders_considered} "
            f"folders_skipped_no_dir={folders_skipped_no_dir} folders_with_data={folders_with_data} "
            f"rows={all_total_rows}"
        )
        if (not column_size) and hasattr(merge_df, "columns") and len(merge_df.columns):
            column_size = len(merge_df.columns)
        if is_initial_load and migrated_rows == 0:
            zero_row_msg = (
                f"Initial load completed with 0 rows for table '{table_name}'. "
                f"folders_considered={folders_considered} "
                f"folders_skipped_no_dir={folders_skipped_no_dir} "
                f"folders_with_data={folders_with_data} final_dir={final_dir!r}. "
                "Drop the Databricks base table, clear watermarks, "
                "and re-run with drop_and_reload=True."
            )
            logger.error("DBX|initial_load_zero_rows | %s", zero_row_msg)
            insert_error_log(
                cmp_id,
                connection_id,
                zero_row_msg,
                "process_table_initial_load_zero_rows",
            )
            insert_tool_log(
                cmp_id,
                connection_id,
                usr_id,
                table_name,
                "W",
                zero_row_msg,
                session,
                job_name=job_name,
            )

        last_checked_date[table_name] = final_dir
        last_checked_date = apply_data_max_watermark(last_checked_date, table_name)
        status = "completed"
        end_time = datetime.now()
        data_transfer_logs(
            cmp_id,
            connection_id,
            usr_id,
            table_name,
            start_time,
            end_time,
            log_inserts,
            log_updates,
            log_deletes,
            column_size,
            session,
            job_name=job_name,
        )
        logger.warning(
            "DBX|process_table_done | table=%s | connection_id=%s | session=%s | "
            "rows=%s | migrated=%s | inserts=%s | updates=%s | deletes=%s",
            table_name,
            connection_id,
            migration_session_id,
            all_total_rows,
            migrated_rows,
            log_inserts,
            log_updates,
            log_deletes,
        )
        detail_log.log(
            f"process_table:end status={status} rows={all_total_rows} migrated={migrated_rows} "
            f"inserts={log_inserts} updates={log_updates} deletes={log_deletes}"
        )
        update_table_status(
            connection_id,
            table_name,
            status,
            last_checked_date,
            job_name,
            migration_session_id,
            existing_fields,
            cmp_id,
            table_detail_log=detail_log,
        )
        delete_csv_from_bucket(table_name, connection_id, cmp_id)
        return (
            migrated_rows,
            log_inserts,
            log_updates,
            log_deletes,
            column_size,
            last_checked_date,
        )

    except Exception as e:
        stage_hint = _failure_stage_hint(e)
        try:
            detail_log.log(f"EXCEPTION stage={stage_hint}: {e!r}\n{traceback.format_exc()}")
        except Exception:
            pass
        logger.error(
            "DBX|process_table_failed | table=%s | connection_id=%s | session=%s | "
            "stage_hint=%s | err=%s",
            table_name,
            connection_id,
            migration_session_id,
            stage_hint,
            e,
        )
        _mark_table_terminal_failure(
            exc=e,
            table_name=table_name,
            connection_id=connection_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            last_checked_date=last_checked_date,
            existing_fields=existing_fields,
            cmp_id=cmp_id,
            usr_id=usr_id,
            session=session,
            detail_log=detail_log,
        )

        # Keep Databricks staging for resume on next run (last_synced + orphan flush).
        # (Previously dropped staging here, which broke deferred-merge resume.)
        try:
            if staging_table_name:
                detail_log.log(
                    f"failure_keep_staging staging={staging_table_name!r} "
                "(resume via last_synced + migration_record)"
                )
        except Exception:
            pass
        return None

    finally:
        detail_log.close()


# ---------------------------------------------------------------------------
# Chunk processing / load
# ---------------------------------------------------------------------------

def process_file_in_chunks(
    connection_string,
    container_name,
    base_path,
    date_dir,
    container_client,
    df,
    folderpath_dict,
    attribute_names,
    attribute_details,
    table_name,
    destination_engine,
    connection_id,
    updated_at,
    deleted_at,
    destination_connection,
    table_exist,
    is_integration_checked,
    extra_fields,
    modified_fields,
    existing_fields,
    attributes_details_field,
    buffer_df,
    buffer_row_count,
    final_dir,
    cmp_id,
    chunk_count,
    migration_session_id=None,
    job_name=None,
    staging_table_name=None,
    table_detail_log=None,
    usr_id=None,
    session=None,
    folder_checkpoint=None,
    safety_interval_minutes=None,
):
    def _tlog(msg):
        if table_detail_log is not None and table_detail_log.enabled():
            table_detail_log.log(f"process_file_in_chunks | {msg}")

    def _tlog_chunk(msg):
        if table_detail_log is not None and table_detail_log.enabled():
            table_detail_log.log_chunk(f"process_file_in_chunks | {msg}")

    def _apply_field_updates():
        if extra_fields:
            _vprint(f"Extra fields for table '{table_name}': {extra_fields}")
            for field in extra_fields:
                existing_fields[field] = attributes_details_field[field]
        if modified_fields:
            _vprint(f"Modified fields for table '{table_name}': {modified_fields}")
            for field, new_type in modified_fields.items():
                existing_fields[field] = new_type

    def _flush_ready_buffer(blob_path=None):
        nonlocal buffer_df, buffer_row_count, table_exist, global_chunk_count
        nonlocal total_rows, total_inserts, total_updates, total_delete_recids
        nonlocal databricks_schema, table_name_final, staging_table, primary_key, df_lat
        touch_session_staging_name(migration_session_id, table_name)
        while chunk_count and buffer_row_count >= chunk_count:
            start_time = time.time()
            flush_df, buffer_df = take_flush_slice(buffer_df, chunk_count)
            buffer_row_count = 0 if buffer_df is None or buffer_df.empty else len(buffer_df.index)
            (
                data,
                inserts,
                deletes,
                updates,
                databricks_schema,
                table_name_final,
                staging_table,
                primary_key,
                df_lat,
            ) = table_creation(
                df,
                flush_df,
                attribute_names,
                attribute_details,
                table_name,
                destination_engine,
                connection_id,
                updated_at,
                deleted_at,
                current_utc_time,
                False,
                global_chunk_count,
                destination_connection,
                table_exist,
                is_integration_checked,
                extra_fields,
                modified_fields,
                existing_fields,
                attributes_details_field,
                cmp_id,
                migration_session_id,
                job_name,
                staging_table_name=staging_table_name,
                usr_id=usr_id,
                session=session,
                folder_checkpoint=folder_checkpoint,
                safety_interval_minutes=safety_interval_minutes,
            )
            total_rows += len(data)
            total_inserts += inserts
            total_updates += updates
            total_delete_recids += deletes
            end_time = time.time()
            logger.warning(
                "[CHUNK] table=%s chunk=%s flushed rows=%s remainder=%s duration_s=%.3f total_rows=%s blob=%s",
                table_name,
                global_chunk_count,
                len(flush_df.index),
                buffer_row_count,
                end_time - start_time,
                total_rows,
                blob_path,
            )
            _tlog_chunk(
                f"chunk_flushed global_chunk={global_chunk_count} duration_s={end_time - start_time:.3f} "
                f"flushed_rows={len(flush_df.index)} remainder={buffer_row_count} "
                f"total_rows={total_rows} inserts={total_inserts} updates={total_updates} "
                f"deletes={total_delete_recids} blob={blob_path!r}"
            )
            global_chunk_count += 1
            table_exist = True
            _apply_field_updates()

    current_utc_time = datetime.now(pytz.utc)
    df_lat = pd.DataFrame()

    table_name_final = None
    staging_table = staging_table_name
    primary_key = "ID"
    databricks_schema = ""

    total_rows, total_inserts = 0, 0
    total_updates, total_delete_recids = 0, 0
    column_size = None
    global_chunk_count = 1
    all_chunks_empty = True
    chunk_size = chunk_count - buffer_row_count
    logger.warning(
        "[CHUNK] table=%s date_dir=%s chunk_size_needed=%s buffer_row_count=%s",
        table_name,
        date_dir,
        chunk_size,
        buffer_row_count,
    )
    _tlog(
        f"enter table={table_name!r} date_dir={date_dir!r} chunk_size={chunk_size} "
        f"buffer_row_count={buffer_row_count} staging_table_name={staging_table_name!r} "
        f"migration_session_id={migration_session_id} job_name={job_name!r}"
    )

    fs = adlfs.AzureBlobFileSystem(connection_string=connection_string)
    blob_prefix, blob_names = list_table_csv_blobs(
        container_client, base_path, date_dir, table_name
    )

    logger.warning(
        "[CHUNK] table=%s date_dir=%s csv_blobs_found=%s blob_prefix=%s",
        table_name,
        date_dir,
        len(blob_names),
        blob_prefix,
    )
    if blob_names:
        _tlog(
            f"csv_list count={len(blob_names)} first={blob_names[0]!r} last={blob_names[-1]!r} "
            f"blob_prefix={blob_prefix!r}"
        )
    else:
        _tlog(f"csv_list empty blob_prefix={blob_prefix!r}")

    try:
        _buf_rows = len(buffer_df.index) if hasattr(buffer_df, "index") else 0
        _buf_cols = len(buffer_df.columns) if hasattr(buffer_df, "columns") else 0
    except Exception:
        _buf_rows, _buf_cols = 0, 0
    logger.warning(
        "[CHUNK] table=%s buffer_rows=%s buffer_cols=%s buffer_row_count=%s table_exist=%s",
        table_name,
        _buf_rows,
        _buf_cols,
        buffer_row_count,
        table_exist,
    )
    if column_size is None:
        column_size = len(buffer_df.columns)

    if not blob_names:
        logger.warning(
            "[CHUNK] table=%s date_dir=%s no_csv_blobs path=%s",
            table_name,
            date_dir,
            blob_prefix,
        )
        _tlog(
            f"no_csv_blobs path={blob_prefix!r} date_dir==final_dir={date_dir == final_dir} "
            f"buffer_row_count={buffer_row_count} table_exist={table_exist}"
        )
        if buffer_row_count >= chunk_count:
            _flush_ready_buffer()
            _tlog(
                f"return_early no_csv_branch_with_buffer date_dir==final_dir total_rows={total_rows} "
                f"global_chunk_count={global_chunk_count} remainder={buffer_row_count}"
            )
            return (
                total_rows,
                total_inserts,
                total_updates,
                total_delete_recids,
                column_size,
                buffer_df,
                buffer_row_count,
                existing_fields,
                table_name_final,
                staging_table,
                primary_key,
                df_lat,
            )
        if not table_exist:
            table_creation(
                df,
                pd.DataFrame(),
                attribute_names,
                attribute_details,
                table_name,
                destination_engine,
                connection_id,
                updated_at,
                deleted_at,
                datetime.now(pytz.utc),
                True,
                1,
                destination_connection,
                table_exist,
                is_integration_checked,
                extra_fields,
                modified_fields,
                existing_fields,
                attributes_details_field,
                cmp_id,
                migration_session_id,
                job_name,
                staging_table_name=staging_table_name,
                usr_id=usr_id,
                session=session,
                folder_checkpoint=folder_checkpoint or date_dir,
                safety_interval_minutes=safety_interval_minutes,
            )
            _tlog("return_early no_csv_table_creation_empty_df")
            return

    for blob in blob_names:
        full_path = f"{container_name}/{blob}"
        logger.warning(
            "DBX|azure_blob_read_start | table=%s | path=%s",
            table_name,
            full_path,
        )
        _vprint("Processing blob:", full_path)
        _tlog(f"blob_start full_path={full_path!r}")

        is_delete_csv = is_dataverse_delete_export_csv(blob)

        read_chunk_size = max(1, int(chunk_count) - int(buffer_row_count or 0))
        for chunk, _, is_last_chunk in read_csv_in_chunks(
            fs, full_path, attribute_names, chunk_size=read_chunk_size
        ):
            if chunk.empty:
                _vprint(f"Chunk from blob {full_path} is empty. Skipping this chunk.")
                continue

            if is_delete_csv:
                chunk = _mark_dataverse_delete_csv_chunk(chunk, attribute_names)
                _tlog(f"delete_csv_marked blob={blob!r} rows={len(chunk.index)}")

            all_chunks_empty = False

            buffer_df = pd.concat([buffer_df, chunk], ignore_index=True)
            buffer_row_count += len(chunk)
            _flush_ready_buffer(blob_path=full_path)

    if all_chunks_empty:
        logger.warning(
            "[CHUNK] table=%s all_chunks_empty table_exist=%s",
            table_name,
            table_exist,
        )
        _tlog(f"all_chunks_empty table_exist={table_exist}")
        if not table_exist:
            table_creation(
                df,
                pd.DataFrame(),
                attribute_names,
                attribute_details,
                table_name,
                destination_engine,
                connection_id,
                updated_at,
                deleted_at,
                current_utc_time,
                True,
                global_chunk_count,
                destination_connection,
                table_exist,
                is_integration_checked,
                extra_fields,
                modified_fields,
                existing_fields,
                attributes_details_field,
                cmp_id,
                migration_session_id,
                job_name,
                staging_table_name=staging_table_name,
                usr_id=usr_id,
                session=session,
                folder_checkpoint=folder_checkpoint or date_dir,
                safety_interval_minutes=safety_interval_minutes,
            )
            _tlog("return_after all_chunks_empty table_creation")
            return

    logger.warning(
        "DBX|[CHUNK] table=%s done rows=%s inserts=%s updates=%s deletes=%s cols=%s",
        table_name,
        total_rows,
        total_inserts,
        total_updates,
        total_delete_recids,
        column_size,
    )

    _tlog(
        f"exit_ok total_rows={total_rows} inserts={total_inserts} updates={total_updates} "
        f"deletes={total_delete_recids} column_size={column_size} "
        f"buffer_row_count={buffer_row_count} primary_key={primary_key!r} "
        f"staging_table={staging_table!r} table_name_final={table_name_final!r}"
    )
    if df_lat.columns.empty and hasattr(df, "columns") and not df.columns.empty:
        df_lat = df
    return (
        total_rows,
        total_inserts,
        total_updates,
        total_delete_recids,
        column_size,
        buffer_df,
        buffer_row_count,
        existing_fields,
        table_name_final,
        staging_table,
        primary_key,
        df_lat,
    )


def table_creation(
    df,
    row_data,
    attribute_names,
    attribute_details,
    table_name,
    destination_engine,
    connection_id,
    updated_at,
    deleted_at,
    current_utc_time,
    is_last_chunk,
    chunk_number,
    destination_connection,
    table_exist,
    is_integration_checked,
    extra_fields,
    modified_fields,
    existing_fields,
    attribute_details_fields,
    cmp_id,
    migration_session_id=None,
    job_name=None,
    staging_table_name=None,
    usr_id=None,
    session=None,
    folder_checkpoint=None,
    safety_interval_minutes=None,
):
    primary_key = "ID"
    databricks_schema = None

    dtype_conversion = dict(field_dtype_conversion)

    add_primary_key = False
    if not table_exist:
        add_primary_key = True

    current_utc_time = pd.to_datetime(current_utc_time, utc=True)
    inserts, deletes, updates = 0, 0, 0

    df = pd.concat([df, row_data], ignore_index=True, axis=0)

    logger.warning(
        "DBX|table_creation_start | table=%s | chunk=%s | rows=%s | cols=%s | is_last=%s",
        table_name,
        chunk_number,
        len(df.index),
        len(df.columns),
        is_last_chunk,
    )
    df = transformations(
        df, dtype_conversion, attribute_details, extra_fields, updated_at, deleted_at
    )
    from services.dynamics.source_common import (
        canonical_meta_column,
        meta_column_name_variants,
    )

    sync_col = canonical_meta_column(updated_at, "_FIVETRAN_SYNCED")
    del_col = canonical_meta_column(deleted_at, "_FIVETRAN_DELETED")
    meta_variants = meta_column_name_variants(updated_at, deleted_at)
    stray_meta = [
        c
        for c in df.columns
        if str(c).upper() in meta_variants and str(c).upper() not in {sync_col, del_col}
    ]
    if stray_meta:
        df = df.drop(columns=stray_meta)
    df[sync_col] = current_utc_time
    df[del_col] = df["IS_DELETE"].fillna(False)

    (
        inserts,
        updates,
        deletes,
        databricks_schema,
        table_name_final,
        staging_table,
        primary_key,
        df,
    ) = databricks_load_chunk(
        df,
        table_name,
        "ID",
        deletes,
        updates,
        table_exist,
        destination_engine,
        add_primary_key,
        is_last_chunk,
        destination_connection,
        deleted_at,
        updated_at,
        is_integration_checked,
        extra_fields,
        modified_fields,
        existing_fields,
        attribute_details_fields,
        connection_id,
        cmp_id,
        migration_session_id,
        job_name,
        staging_table_name=staging_table_name,
        usr_id=usr_id,
        session=session,
        folder_checkpoint=folder_checkpoint,
        safety_interval_minutes=safety_interval_minutes,
    )
    return (
        df,
        inserts,
        deletes,
        updates,
        databricks_schema,
        table_name_final,
        staging_table,
        primary_key,
        df,
    )


def databricks_load_chunk(
    df,
    table_name,
    primary_key_column,
    deletes_lst,
    updates_lst,
    table_exist,
    destination_engine,
    add_primary_key,
    is_last_chunk,
    destination_connection,
    deleted_at,
    updated_at,
    is_integration_checked,
    extra_fields,
    modified_fields,
    existing_fields,
    attributes_details_field,
    connection_id,
    cmp_id,
    migration_session_id=None,
    job_name=None,
    staging_table_name=None,
    usr_id=None,
    session=None,
    folder_checkpoint=None,
    safety_interval_minutes=None,
):
    """
    Deduplicate chunk, COPY INTO staging, per-chunk MERGE into base (orphan deletes skipped on INSERT).
    Skips Snowflake primary-key ALTER. updates_count stays 0.
    """
    logger.warning(
        "DBX|databricks_load_chunk_start | table=%s | session=%s | rows=%s | staging=%s | table_exist=%s",
        table_name,
        migration_session_id,
        len(df.index) if hasattr(df, "index") else 0,
        staging_table_name,
        table_exist,
    )
    staging_table = staging_table_name
    if not staging_table:
        raise ValueError(
            f"CRITICAL ERROR: staging_table_name is missing in databricks_load_chunk "
            f"for table {table_name}"
        )

    inserts_count = 0
    updates_count = 0
    deletes_count = 0

    engine = destination_engine
    if df.shape[0] > 0:
        df["SINK_MODIFIED_ON"] = pd.to_datetime(df["SINK_MODIFIED_ON"], utc=True)
        df = df.sort_values(
            by=["ID", "SINK_MODIFIED_ON"], ascending=[True, False]
        )
        df = df.drop_duplicates(subset=["ID"], keep="first").reset_index(drop=True)
        df = filter_null_primary_key_rows(df, primary_key_column)
        is_delete_true_df = df[df["IS_DELETE"] == True]  # noqa: E712
        original_table_ids = []
        if not is_delete_true_df.empty:
            ids_to_check = [
                i
                for i in is_delete_true_df[primary_key_column].tolist()
                if i is not None
            ]
            if ids_to_check:
                if attributes_details_field:
                    ensure_databricks_base_table_exists(
                        destination_engine,
                        table_name,
                        (destination_connection or {}).get("schema") or "",
                        df,
                        attributes_details_field,
                        connection_id,
                        cmp_id,
                        destination_connection=destination_connection,
                    )
                schema_for_lookup = (destination_connection or {}).get("schema") or ""
                original_table_ids = retrieve_snowflake_ids_delete(
                    destination_engine,
                    table_name,
                    schema_for_lookup,
                    ids_to_check,
                    connection_id,
                    cmp_id,
                )
                if table_exist and staging_table:
                    staging_ids = retrieve_snowflake_ids_delete(
                        destination_engine,
                        staging_table,
                        schema_for_lookup,
                        ids_to_check,
                        connection_id,
                        cmp_id,
                    )
                    original_table_ids = list(set(original_table_ids) | set(staging_ids))
        df = filter_orphan_delete_rows(
            df,
            original_table_ids,
            deleted_col="IS_DELETE",
            primary_key=primary_key_column,
        )
        logger.warning(
            "DBX|databricks_load_chunk_deduped | table=%s | rows=%s | table_exist=%s",
            table_name,
            len(df.index),
            table_exist,
        )

    catalog = (
        (destination_connection or {}).get("catalog")
        or (destination_connection or {}).get("database")
        or ""
    )
    schema = (destination_connection or {}).get("schema")
    table_formatted = f'{catalog}.{schema}."{staging_table}"'
    table_formatted_original = f'{catalog}.{schema}."{table_name.upper()}"'

    delete_df = df[df["IS_DELETE"] == True] if df.shape[0] > 0 else pd.DataFrame()
    deletes_count = len(delete_df)
    total_rows = len(df)
    inserts_count = total_rows - deletes_count

    _copy_kwargs = dict(
        usr_id=usr_id,
        session=session,
        folder_checkpoint=folder_checkpoint,
        safety_interval_minutes=safety_interval_minutes,
        updated_at=updated_at,
        deleted_at=deleted_at,
    )

    if table_exist is False:
        staging_table, df = copy_function(
            df,
            table_name,
            True,
            destination_engine,
            schema,
            table_formatted,
            table_formatted_original,
            is_integration_checked,
            extra_fields,
            table_exist,
            modified_fields,
            existing_fields,
            attributes_details_field,
            connection_id,
            primary_key_column,
            catalog,
            cmp_id,
            migration_session_id,
            job_name,
            staging_table_name=staging_table_name,
            **_copy_kwargs,
        )
    else:
        if df.shape[0] > 0:
            staging_table, df = copy_function(
                df,
                table_name,
                False,
                destination_engine,
                schema,
                table_formatted,
                table_formatted_original,
                is_integration_checked,
                extra_fields,
                table_exist,
                modified_fields,
                existing_fields,
                attributes_details_field,
                connection_id,
                primary_key_column,
                catalog,
                cmp_id,
                migration_session_id,
                job_name,
                staging_table_name=staging_table_name,
                **_copy_kwargs,
            )

    # add_primary_key intentionally unused — Databricks path skips ALTER PRIMARY KEY
    _ = add_primary_key
    _ = is_last_chunk
    _ = deletes_lst
    _ = updates_lst
    _ = engine

    logger.warning(
        "DBX|databricks_load_chunk_done | table=%s | rows=%s | inserts=%s | updates=%s | deletes=%s",
        table_name,
        total_rows,
        inserts_count,
        updates_count,
        deletes_count,
    )

    return (
        inserts_count,
        updates_count,
        deletes_count,
        schema,
        table_name,
        staging_table,
        primary_key_column,
        df,
    )
