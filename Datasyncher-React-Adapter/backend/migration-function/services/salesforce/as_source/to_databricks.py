"""
Salesforce → Databricks migration orchestrator.

Mirrors Dynamics→Databricks per-chunk MERGE staging while reusing Salesforce Bulk API
extract helpers from salesforce.client.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd

from models.salesforce_models import SalesforceDatabricksMigration
from services.databricks.load_helpers import (
    check_table_exists_databricks,
    copy_function,
    destination_connection_function,
    drop_databricks_table,
    drop_staging_table,
    flush_orphan_staging_tables,
)
from services.salesforce.as_source.destinations.databricks import (
    build_attributes_details_field,
    ensure_databricks_table_from_salesforce_describe,
    salesforce_staging_blob_name,
    transform_salesforce_chunk_to_dataframe,
)
from services.salesforce.client import (
    BulkAPI2Client,
    MigrationFailureTracker,
    build_salesforce_extract_where_clause,
    build_salesforce_oauth_context,
    check_field_exists,
    get_all_salesforce_fields,
    get_last_checked_dates_and_fields,
    format_salesforce_migration_auth_error,
    is_feeditem_id_filter_error,
    is_non_retryable_salesforce_auth_error,
    is_unsupported_salesforce_object,
    process_salesforce_results_in_chunks,
    salesforce_connection,
    update_last_synced_date,
    update_table_status_sf_to_snowflake,
)
from utils.celery_cleanup_utils import handle_migration_completion
from utils.database_utils import data_transfer_logs
from utils.salesforce_dynamic_checker import initialize_dynamic_checker
from utils.snowflake_utils import normalize_name
from utils.load_checkpoint import (
    SF_MODSTAMP_CHECKPOINT_PREFIX,
    finalize_migration_record,
    get_last_synced_for_table,
    get_migration_record_info,
    get_migration_record_staging_count,
    last_synced_from_checkpoint,
    mark_migration_failed,
    parse_sync_datetime,
    safe_salesforce_modstamp_checkpoint,
    should_resume_migration,
    update_load_checkpoint,
)

logger = logging.getLogger(__name__)


def _normalize_selected_tables(selected) -> List[str]:
    """Accept flat names or [[src, dest], ...] pairs; return SF object names."""
    out: List[str] = []
    for entry in selected or []:
        if entry is None:
            continue
        if isinstance(entry, (list, tuple)):
            if not entry:
                continue
            out.append(str(entry[0]))
        else:
            out.append(str(entry))
    return out


def _new_staging_table_name(object_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return normalize_name(f"DS_{object_name}_STAGING_{ts}")


def _rows_migrated(total_rows: int, migration_info: Optional[dict]) -> int:
    """Rows applied this run — include cumulative migration_record count on resume."""
    if total_rows > 0:
        return int(total_rows)
    if migration_info:
        return int(migration_info.get("staging_records_count") or 0)
    return int(total_rows)


def _effective_last_synced(last_synced) -> Optional[datetime]:
    """Parse DB watermark for incremental SOQL filters."""
    return parse_sync_datetime(last_synced)


def _is_incremental_salesforce_sync(
    effective_last_synced_dt: Optional[datetime],
    table_exist: bool,
    drop_and_reload: bool,
) -> bool:
    """Full reload must not apply incremental SOQL even after the table is recreated."""
    return bool(effective_last_synced_dt and table_exist and not drop_and_reload)


def _format_soql_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _modstamp_column(df: pd.DataFrame) -> Optional[str]:
    for col in (
        "SYSTEMMODSTAMP",
        "SystemModstamp",
        "LASTMODIFIEDDATE",
        "LastModifiedDate",
    ):
        if col in df.columns:
            return col
    return None


def _chunk_modstamp_checkpoint(
    df: pd.DataFrame,
    safety_interval_seconds: Optional[int],
) -> Optional[str]:
    col = _modstamp_column(df)
    if not col:
        return None
    series = df[col].dropna()
    if series.empty:
        return None
    max_val = series.max()
    raw = max_val.isoformat() if hasattr(max_val, "isoformat") else str(max_val)
    safe = safe_salesforce_modstamp_checkpoint(raw, safety_interval_seconds)
    if not safe:
        return None
    return f"{SF_MODSTAMP_CHECKPOINT_PREFIX}{safe}"


async def sync_salesforce_to_databricks(
    request: SalesforceDatabricksMigration,
    environment: str,
):
    """Top-level Salesforce → Databricks sync (production or sandbox)."""
    start_time = datetime.now(timezone.utc)
    salesforce_details = request.source_config or {}
    databricks_details = request.destination_config or {}
    connection_id = request.connection_id
    cmp_id = request.cmp_id
    user_id = request.user_id
    session = request.session
    mapping_list = _normalize_selected_tables(request.selected_tables)
    job_name = request.job_name
    migration_session_id = request.migration_session_id
    execution_order = request.execution_order or "sequential"
    chunk_count = int(request.chunk_count or 50000)
    connection_name = request.connection_name or ""
    company_name = request.company_name or ""
    is_updated = request.is_updated or "_FIVETRAN_SYNCED"
    is_deleted = request.is_deleted or "_FIVETRAN_DELETED"
    drop_and_reload = bool(getattr(request, "drop_and_reload", False))
    is_final_attempt = bool(getattr(request, "is_final_attempt", True))
    celery_child_mode = bool(getattr(request, "celery_child_mode", False)) or bool(
        getattr(request, "celery_fanout_mode", False)
    )
    if celery_child_mode and execution_order == "parallel":
        execution_order = "sequential"
        logger.info("SF_DBX|celery_child forcing sequential execution")

    try:
        interval_seconds = max(int(request.safety_interval or 0), 120)
    except (TypeError, ValueError):
        interval_seconds = 120
    safety_interval_cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)
    ).replace(microsecond=0)

    failure_tracker = MigrationFailureTracker()
    shared_row_counts: List[int] = []

    logger.warning(
        "SF_DBX|sync_start | connection_id=%s | session=%s | env=%s | tables=%s | "
        "chunk_count=%s | drop_and_reload=%s | celery_child=%s | job=%s",
        connection_id,
        migration_session_id,
        environment,
        mapping_list,
        chunk_count,
        drop_and_reload,
        celery_child_mode,
        (job_name or "")[:120],
    )

    from services.salesforce.client import prepare_salesforce_credentials

    salesforce_details, sf_oauth_context = prepare_salesforce_credentials(
        connection_id, "source", salesforce_details
    )

    try:
        salesforce_connection(
            salesforce_details.get("state"),
            salesforce_details.get("access_token"),
            salesforce_details.get("instance_url"),
            salesforce_details.get("refresh_token"),
            salesforce_details.get("CLIENT_ID"),
            salesforce_details.get("CLIENT_SECRET"),
            environment,
            salesforce_details.get("CUSTOM_DOMAIN"),
            oauth_context=sf_oauth_context,
        )
        logger.info("SF_DBX|salesforce_connection_ok")
    except Exception as sf_err:
        err = str(getattr(sf_err, "detail", None) or sf_err)
        auth_expired = is_non_retryable_salesforce_auth_error(err)
        if auth_expired:
            err = format_salesforce_migration_auth_error(
                err,
                environment=environment,
                credentials=salesforce_details,
            )
        should_mark_failed = is_final_attempt or auth_expired
        if should_mark_failed:
            for table in mapping_list:
                try:
                    update_table_status_sf_to_snowflake(
                        connection_id, table, "failed", job_name, migration_session_id
                    )
                except Exception:
                    pass
                failure_tracker.add_failure(table, err, "Salesforce to Databricks")
        if auth_expired:
            return {
                "status": "failed",
                "message": err,
                "total_tables": len(mapping_list),
                "successful_tables_count": 0,
                "failed_tables_count": len(mapping_list),
                "successful_tables_list": [],
                "failed_tables_list": list(mapping_list),
                "total_rows": 0,
                "execution_time": (datetime.now(timezone.utc) - start_time).total_seconds(),
            }
        raise

    destination_engine, databricks_schema = destination_connection_function(
        databricks_details,
        connection_id,
        cmp_id,
        migration_session_id=migration_session_id,
        job_name=job_name,
        usr_id=user_id,
        session=session,
    )
    catalog = None
    if hasattr(destination_engine, "config"):
        catalog = (destination_engine.config.get("catalog") or "").strip() or None
    logger.info(
        "SF_DBX|databricks_connection_ok | schema=%s | catalog=%s",
        databricks_schema,
        catalog,
    )

    if drop_and_reload:
        logger.warning("SF_DBX|drop_and_reload | tables=%s", mapping_list)
        for table_name in mapping_list:
            try:
                drop_databricks_table(
                    destination_engine,
                    normalize_name(table_name),
                    schema=databricks_schema,
                    catalog=catalog,
                )
            except Exception as drop_exc:
                logger.warning(
                    "SF_DBX|drop_table_failed | table=%s | err=%s", table_name, drop_exc
                )

    successful: List[str] = []
    failed: List[str] = []

    async def _run_one(mapping: str):
        try:
            await process_salesforce_to_databricks_mapping(
                mapping=mapping,
                salesforce_details=salesforce_details,
                destination_engine=destination_engine,
                databricks_schema=databricks_schema,
                catalog=catalog,
                connection_id=connection_id,
                job_name=job_name,
                migration_session_id=migration_session_id,
                cmp_id=cmp_id,
                user_id=user_id,
                environment=environment,
                chunk_count=chunk_count,
                session=session,
                is_updated=is_updated,
                is_deleted=is_deleted,
                safety_interval_cutoff=safety_interval_cutoff,
                safety_interval_seconds=interval_seconds,
                drop_and_reload=drop_and_reload,
                failure_tracker=failure_tracker,
                is_final_attempt=is_final_attempt,
                shared_row_counts=shared_row_counts,
            )
            successful.append(mapping)
        except Exception as exc:
            failed.append(mapping)
            logger.error(
                "SF_DBX|table_failed | table=%s | err=%s\n%s",
                mapping,
                exc,
                traceback.format_exc(),
            )
            failure_tracker.add_failure(mapping, str(exc), "Salesforce to Databricks")
            try:
                update_table_status_sf_to_snowflake(
                    connection_id, mapping, "failed", job_name, migration_session_id
                )
            except Exception:
                pass

    if execution_order == "parallel" and len(mapping_list) > 1:
        await asyncio.gather(*[_run_one(m) for m in mapping_list])
    else:
        for mapping in mapping_list:
            await _run_one(mapping)

    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    total_rows = sum(shared_row_counts) if shared_row_counts else 0

    if not celery_child_mode:
        try:
            handle_migration_completion(
                connection_name=connection_name or f"SALESFORCE_DATABRICKS_{connection_id}",
                job_name=job_name,
                completion_message=None,
            )
        except Exception as cleanup_exc:
            logger.warning("SF_DBX|completion_handler_failed | err=%s", cleanup_exc)

        try:
            failure_tracker.send_consolidated_error_email(
                connection_name=connection_name,
                service_name="Salesforce to Databricks",
                user_id=user_id,
                cmp_id=cmp_id,
                connection_id=connection_id,
                job_name=job_name,
                migration_session_id=migration_session_id,
                company_name=company_name,
                is_final_attempt=is_final_attempt,
            )
        except Exception as email_exc:
            logger.warning("SF_DBX|failure_email_failed | err=%s", email_exc)

    status = "completed" if not failed else ("partial" if successful else "failed")
    return {
        "status": status,
        "status_code": 200 if successful else 500,
        "message": (
            f"Salesforce→Databricks finished: {len(successful)} ok, "
            f"{len(failed)} failed, {total_rows} rows"
        ),
        "total_rows": total_rows,
        "execution_time": duration,
        "total_tables": len(mapping_list),
        "successful_tables_count": len(successful),
        "failed_tables_count": len(failed),
        "successful_tables_list": successful,
        "failed_tables_list": failed,
    }


migrate = sync_salesforce_to_databricks


async def process_salesforce_to_databricks_mapping(
    *,
    mapping: str,
    salesforce_details: dict,
    destination_engine,
    databricks_schema: str,
    catalog: Optional[str],
    connection_id: int,
    job_name: str,
    migration_session_id: int,
    cmp_id: int,
    user_id: int,
    environment: str,
    chunk_count: int,
    session,
    is_updated: str,
    is_deleted: str,
    safety_interval_cutoff: datetime,
    safety_interval_seconds: int = 120,
    drop_and_reload: bool = False,
    failure_tracker=None,
    is_final_attempt: bool = True,
    shared_row_counts=None,
) -> int:
    """Per-object ETL: Bulk extract → transform → COPY INTO staging → deferred MERGE."""
    salesforce_object = mapping
    start_time = datetime.now(timezone.utc)
    process_logger = logging.getLogger(f"{__name__}.{salesforce_object}")
    process_logger.info("SF_DBX|object_start | object=%s", salesforce_object)
    update_table_status_sf_to_snowflake(
        connection_id, salesforce_object, "in_progress", job_name, migration_session_id
    )

    last_checked_data = get_last_checked_dates_and_fields(
        connection_id, [salesforce_object], True
    )
    last_synced = last_checked_data.get(salesforce_object, {}).get("last_checked_date")
    is_history = bool(
        last_checked_data.get(salesforce_object, {}).get("is_history", False)
    )

    sf = salesforce_connection(
        salesforce_details.get("state"),
        salesforce_details.get("access_token"),
        salesforce_details.get("instance_url"),
        salesforce_details.get("refresh_token"),
        salesforce_details.get("CLIENT_ID"),
        salesforce_details.get("CLIENT_SECRET"),
        environment,
        salesforce_details.get("CUSTOM_DOMAIN"),
        oauth_context=build_salesforce_oauth_context(
            connection_id, "source", salesforce_details
        ),
    )
    initialize_dynamic_checker(sf, cache_ttl_minutes=60)

    if is_unsupported_salesforce_object(salesforce_object, sf):
        # Must raise: _run_one treats a normal return as success. Returning 0 here
        # used to mark Celery children successful while migration_record stayed failed
        # (seen on Pipeline run 9440 / AttachedContentNote).
        skip_reason = f"Salesforce object '{salesforce_object}' is not queryable"
        process_logger.warning("SF_DBX|skip | %s", skip_reason)
        if failure_tracker:
            failure_tracker.add_failure(
                salesforce_object, f"SKIPPED: {skip_reason}", "Salesforce to Databricks"
            )
        update_table_status_sf_to_snowflake(
            connection_id, salesforce_object, "failed", job_name, migration_session_id
        )
        try:
            from utils.error_utils import log_error_to_tool_log

            log_error_to_tool_log(
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                table_name=salesforce_object,
                error_message=f"SKIPPED: {skip_reason}",
                session=session,
                job_name=job_name,
            )
        except Exception as log_err:
            process_logger.warning(
                "SF_DBX|skip_log_failed | object=%s | err=%s",
                salesforce_object,
                log_err,
            )
        raise RuntimeError(f"SKIPPED: {skip_reason}")

    describe = sf.__getattr__(salesforce_object).describe()
    try:
        all_fields = get_all_salesforce_fields(
            sf, salesforce_object, logger=process_logger
        )
        fields_for_query = list(set(all_fields.keys()) | {"Id"})
    except Exception:
        fields_for_query = [
            f["name"] for f in describe.get("fields", []) if f.get("name")
        ]
        if "Id" not in fields_for_query:
            fields_for_query.append("Id")

    if (
        check_field_exists(sf, salesforce_object, "IsDeleted")
        and "IsDeleted" not in fields_for_query
    ):
        fields_for_query.append("IsDeleted")

    has_system_modstamp = check_field_exists(sf, salesforce_object, "SystemModstamp")
    has_last_modified_date = check_field_exists(
        sf, salesforce_object, "LastModifiedDate"
    )
    has_last_modified = check_field_exists(sf, salesforce_object, "LastModified")
    has_login_time = check_field_exists(sf, salesforce_object, "LoginTime")
    has_created_date = check_field_exists(sf, salesforce_object, "CreatedDate")

    field_type_map = {}
    for field in describe.get("fields", []):
        name = field["name"]
        ftype = str(field.get("type", "string")).upper()
        field_type_map[name] = {
            "DOUBLE": "float",
            "CURRENCY": "float",
            "PERCENT": "float",
            "INT": "int",
            "INTEGER": "int",
            "BOOLEAN": "boolean",
            "DATE": "datetime",
            "DATETIME": "datetime",
        }.get(ftype, "string")

    attributes_details = build_attributes_details_field(
        describe.get("fields", []),
        fields_for_query,
        is_updated=is_updated,
        is_deleted=is_deleted,
    )

    dest_conn = {"schema": databricks_schema, "catalog": catalog}
    table_exist = check_table_exists_databricks(
        destination_engine,
        normalize_name(salesforce_object),
        dest_conn,
        connection_id,
        cmp_id,
    )
    if not table_exist or drop_and_reload:
        ensure_databricks_table_from_salesforce_describe(
            destination_engine,
            salesforce_object,
            databricks_schema,
            describe.get("fields", []),
            fields_for_query,
            connection_id,
            cmp_id,
            is_updated=is_updated,
            is_deleted=is_deleted,
            catalog=catalog,
        )
        table_exist = True

    migration_info = get_migration_record_info(
        connection_id=connection_id,
        table_name=salesforce_object,
        migration_session_id=migration_session_id,
        job_name=job_name,
    )
    db_last_synced = get_last_synced_for_table(connection_id, salesforce_object) or last_synced
    effective_last_synced_dt = _effective_last_synced(db_last_synced)
    if should_resume_migration(
        (migration_info or {}).get("status"),
        db_last_synced,
    ):
        process_logger.info(
            "SF_DBX|resume_migration | object=%s | last_synced=%s | status=%s",
            salesforce_object,
            db_last_synced,
            (migration_info or {}).get("status"),
        )
        try:
            flush_orphan_staging_tables(
                destination_engine,
                databricks_schema,
                normalize_name(salesforce_object),
                "ID",
                list(fields_for_query),
                normalize_name(is_updated),
                normalize_name(is_deleted),
            )
        except Exception as flush_exc:
            process_logger.warning(
                "SF_DBX|resume_staging_flush_failed | object=%s | err=%s",
                salesforce_object,
                flush_exc,
            )
            raise

    is_incremental_sync = _is_incremental_salesforce_sync(
        effective_last_synced_dt, table_exist, drop_and_reload
    )
    exclude_deleted_rows = not is_incremental_sync

    query = f"SELECT {', '.join(fields_for_query)} FROM {salesforce_object}"
    cutoff = safety_interval_cutoff
    upper_ts = _format_soql_ts(cutoff)
    lower_ts = None
    if is_incremental_sync:
        try:
            last_synced_dt = effective_last_synced_dt
            if cutoff <= last_synced_dt:
                cutoff = last_synced_dt + timedelta(seconds=1)
            lower_ts = _format_soql_ts(last_synced_dt)
            upper_ts = _format_soql_ts(cutoff)
        except Exception as filter_exc:
            process_logger.warning(
                "SF_DBX|incremental_filter_failed | err=%s", filter_exc
            )
    query += build_salesforce_extract_where_clause(
        salesforce_object,
        is_incremental_sync=is_incremental_sync,
        is_history=is_history,
        lower_ts=lower_ts,
        upper_ts=upper_ts,
        has_system_modstamp=has_system_modstamp,
        has_last_modified_date=has_last_modified_date,
        has_last_modified=has_last_modified,
        has_login_time=has_login_time,
        has_created_date=has_created_date,
    )
    if is_incremental_sync and " WHERE " in query:
        process_logger.info("SF_DBX|incremental_where | %s", query)

    staging_table = _new_staging_table_name(salesforce_object)

    blob_name = salesforce_staging_blob_name(
        normalize_name(salesforce_object), connection_id, parquet=False
    )
    total_rows = 0
    last_df: Optional[pd.DataFrame] = None
    chunk_index = 0

    async def _load_chunk(chunk_rows: list):
        nonlocal total_rows, last_df, chunk_index
        if not chunk_rows:
            return
        chunk_index += 1
        df = transform_salesforce_chunk_to_dataframe(
            chunk_rows,
            fields_for_query,
            attributes_details,
            is_updated=is_updated,
            is_deleted=is_deleted,
            exclude_deleted_rows=exclude_deleted_rows,
        )
        if df is None or df.empty:
            return
        if "ID" in df.columns:
            df = df.drop_duplicates(subset=["ID"], keep="last").reset_index(drop=True)

        modstamp_ckpt = _chunk_modstamp_checkpoint(df, safety_interval_seconds)
        copy_function(
            df,
            normalize_name(salesforce_object),
            False,
            destination_engine,
            databricks_schema,
            normalize_name(salesforce_object),
            salesforce_object,
            True,
            [],
            table_exist,
            {},
            attributes_details,
            attributes_details,
            connection_id,
            "ID",
            catalog or "",
            cmp_id,
            migration_session_id,
            job_name,
            staging_table,
            user_id,
            session,
            modstamp_ckpt,
            None,
            safety_interval_seconds,
            blob_name,
            updated_at=normalize_name(is_updated),
            deleted_at=normalize_name(is_deleted),
        )
        total_rows += len(df.index)
        last_df = df
        process_logger.info(
            "SF_DBX|chunk_loaded | object=%s | chunk=%s | rows=%s | total=%s | checkpoint=%s",
            salesforce_object,
            chunk_index,
            len(df.index),
            total_rows,
            modstamp_ckpt,
        )

    try:
        bulk_client = BulkAPI2Client(sf, field_type_map)
        job_id = await bulk_client.create_query_job(
            salesforce_object, query, chunk_count, True
        )
        results = await bulk_client.get_job_results(job_id)
        process_logger.info("SF_DBX|soql | %s", query)
        if job_id == "fallback_bulk_1" or str(job_id).startswith("fallback_bulk1_"):
            async for chunk in results:
                await _load_chunk(chunk)
        else:
            for chunk in process_salesforce_results_in_chunks(results, chunk_count):
                await _load_chunk(chunk)

        rows_migrated = get_migration_record_staging_count(
            connection_id=connection_id,
            table_name=salesforce_object,
            migration_session_id=migration_session_id,
            job_name=job_name,
        )
        if rows_migrated <= 0:
            rows_migrated = _rows_migrated(total_rows, migration_info)

        try:
            drop_staging_table(
                destination_engine,
                staging_table,
                schema=databricks_schema,
                catalog=catalog,
            )
        except Exception as drop_stg_exc:
            process_logger.warning(
                "SF_DBX|drop_staging_failed | staging=%s | err=%s",
                staging_table,
                drop_stg_exc,
            )
        finalize_migration_record(
            connection_id=connection_id,
            table_name=salesforce_object,
            migration_session_id=migration_session_id,
            job_name=job_name,
            final_records_count=rows_migrated,
        )

        if rows_migrated > 0 or not is_incremental_sync:
            update_last_synced_date(connection_id, salesforce_object, cutoff)

        end_time = datetime.now(timezone.utc)
        try:
            data_transfer_logs(
                cmp_id,
                connection_id,
                user_id,
                salesforce_object,
                start_time,
                end_time,
                len(fields_for_query),
                session,
                rows_migrated,
                0,
                0,
                0,
                job_name=job_name,
            )
        except Exception as transfer_exc:
            process_logger.warning(
                "SF_DBX|data_transfer_logs_failed | object=%s | err=%s",
                salesforce_object,
                transfer_exc,
            )

        update_table_status_sf_to_snowflake(
            connection_id,
            salesforce_object,
            "completed",
            job_name,
            migration_session_id,
        )
        if shared_row_counts is not None and hasattr(shared_row_counts, "append"):
            shared_row_counts.append(rows_migrated)

        process_logger.info(
            "SF_DBX|object_done | object=%s | rows=%s",
            salesforce_object,
            rows_migrated,
        )
        return rows_migrated

    except Exception as exc:
        process_logger.error(
            "SF_DBX|object_error | object=%s | err=%s\n%s",
            salesforce_object,
            exc,
            traceback.format_exc(),
        )
        try:
            mark_migration_failed(
                connection_id=connection_id,
                table_name=salesforce_object,
                migration_session_id=migration_session_id,
                job_name=job_name,
            )
        except Exception:
            pass
        if is_final_attempt:
            update_table_status_sf_to_snowflake(
                connection_id,
                salesforce_object,
                "failed",
                job_name,
                migration_session_id,
            )
        raise
