"""SAP OData to Databricks orchestrator.

Mirrors ``to_snowflake.py`` for OData extract, delta tokens, row filters, and
entity transforms; swaps Snowflake GCS/COPY for Databricks ``load_helpers``
(per-chunk COPY INTO + MERGE), following Salesforce → Databricks load calls.
"""

from __future__ import annotations

import logging
import time
import traceback as tb_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import HTTPException
from models.sap_odata_models import SAPODataDatabricksMigrationRequest
from services.databricks.load_helpers import (
    check_table_exists_databricks,
    copy_function,
    destination_connection_function,
    drop_databricks_table,
    drop_staging_table,
)
from services.sap.as_source.destinations.databricks import (
    build_attributes_details_from_sap_metadata,
    build_target_schema_metadata_databricks,
    ensure_databricks_table_from_sap_metadata,
    normalize_sap_column_for_databricks,
    sap_staging_blob_name,
)
from services.sap.as_source.to_adls import (
    MigrationFailureTracker,
    _get_table_etl_state,
    _update_table_etl_state,
)
from services.sap.as_source.to_snowflake import (
    _clear_destination_rename_mode,
    _fetch_delta_df,
    _fetch_initial_df,
    _prep_df,
    _resolve_celery_child_mode,
    _resolve_server_address,
)
from services.sap.proxy import is_proxy_connection
from services.sap.row_filter import build_row_filter_expression
from services.sap.source import fetch_metadata
from services.transform.db_state import get_active_entity_transform
from services.transform.destination_table_name import is_valid_snowflake_destination_name
from services.transform.executor import execute_transform_chunk
from services.transform.mapping_resolver import resolve_effective_mapping
from services.transform.one_to_many import log_one_to_many_column_fill
from config.transform_limits import DRY_RUN_SAP_SELECT_BATCH_SIZE
from utils.database_utils import (
    data_transfer_logs,
    get_migration_record_id,
    postgres_connection,
    update_table_status_snowflake,
)
from utils.error_utils import (
    insert_tool_log,
    log_error_to_tool_log,
    update_migration_status_log,
    update_table_migration_status_log,
)
from utils.snowflake_utils import normalize_name
from utils.timing_utils import reset_timing_records

logger = logging.getLogger(__name__)


def _dbx_ident(name: str) -> str:
    """Unity Catalog table identifier — same rules as Snowflake transform names."""
    if is_valid_snowflake_destination_name(name):
        return str(name).strip().upper()
    return normalize_name(name)


def _new_staging_table_name(object_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return normalize_name(f"DS_{object_name}_STAGING_{ts}")


def _rename_databricks_table(
    destination_engine,
    schema: str,
    old_name: str,
    new_name: str,
    catalog: Optional[str] = None,
) -> None:
    old_ident = _dbx_ident(old_name)
    new_ident = _dbx_ident(new_name)
    if old_ident == new_ident:
        return
    if catalog:
        sql = (
            f"ALTER TABLE `{catalog}`.`{schema}`.`{old_ident}` "
            f"RENAME TO `{catalog}`.`{schema}`.`{new_ident}`"
        )
    else:
        sql = f"ALTER TABLE `{schema}`.`{old_ident}` RENAME TO `{schema}`.`{new_ident}`"
    with destination_engine.begin() as conn:
        conn.execute(sql)
    logger.info(
        "[SAP -> DBX] Renamed Databricks table %s.%s -> %s (user choice)",
        schema,
        old_ident,
        new_ident,
    )


def _record_table_failure(
    req: SAPODataDatabricksMigrationRequest,
    table_name: str,
    error_msg: str,
    db_conn: Any,
    function_name: str = "sap_odata_to_databricks_connector",
) -> None:
    """Persist table failure to DB logs so the UI can show the error message."""
    display_msg = f"Entity '{table_name}' migration failed: {error_msg}"
    try:
        update_table_status_snowflake(
            req.connection_id,
            table_name,
            "failed",
            req.job_name,
            req.migration_session_id,
        )

        migration_record_id = None
        if db_conn is not None:
            with db_conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO error_log_entry (cmp_id, connection_id, message, function_name, created_on)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (req.cmp_id, req.connection_id, display_msg, function_name),
                )
                db_conn.commit()
                migration_record_id = get_migration_record_id(
                    cursor,
                    job_name=req.job_name,
                    table_name=table_name,
                    connection_id=req.connection_id,
                )

        if migration_record_id:
            updated = update_table_migration_status_log(
                migration_record_id=migration_record_id,
                message=display_msg,
                status="F",
                session=req.session,
                job_name=req.job_name,
                connection_id=req.connection_id,
            )
            if not updated:
                insert_tool_log(
                    cmp_id=req.cmp_id,
                    connection_id=req.connection_id,
                    usr_id=req.user_id,
                    table_name=table_name,
                    status="F",
                    message=display_msg,
                    session=req.session,
                    job_name=req.job_name,
                    migration_record_id=migration_record_id,
                )
        else:
            log_error_to_tool_log(
                cmp_id=req.cmp_id,
                connection_id=req.connection_id,
                user_id=req.user_id,
                table_name=table_name,
                error_message=display_msg,
                session=req.session,
                job_name=req.job_name,
            )
    except Exception as log_err:
        logger.warning(
            "[SAP -> DBX] Failed to persist error log for %s: %s", table_name, log_err
        )


def _primary_key_for_merge(pks: List[str], columns: List[str]) -> List[str]:
    """
    Databricks MERGE/QUALIFY keys. Prefer the full OData composite key list.

    Using only the first key (e.g. BUKRS) collapses line-item extractors like
    0FI_GL_4 to one row per company code.
    """
    keys = [
        normalize_sap_column_for_databricks(pk)
        for pk in (pks or [])
        if pk and str(pk).strip()
    ]
    keys = [k for k in keys if k]
    if keys:
        return keys
    meta = {
        "_FIVETRAN_SYNCED",
        "_FIVETRAN_DELETED",
        "_SPECT_DELETED",
        "_SPECT_SYNCED",
    }
    for col in columns or []:
        key = normalize_sap_column_for_databricks(col)
        if key and key not in meta:
            return [key]
    return ["ID"]


def _migrate_single_table_databricks(
    req: SAPODataDatabricksMigrationRequest,
    table_name: str,
    db_engine: Any,
    destination_engine: Any,
    databricks_schema: str,
    catalog: Optional[str],
    run_id: str,
    failure_tracker: MigrationFailureTracker,
) -> Dict[str, Any]:
    """Extract one SAP entity-set, load into Databricks via copy_function."""
    t_start = time.time()
    now_utc = datetime.now(timezone.utc)
    transform_state = get_active_entity_transform(
        req.connection_id, table_name, existing_conn=db_engine
    )
    dest_from_transform = (transform_state or {}).get("destination_table_name")
    if dest_from_transform and is_valid_snowflake_destination_name(dest_from_transform):
        target_table = dest_from_transform.strip().upper()
    else:
        target_table = _dbx_ident(table_name)

    settings = (req.table_export_settings or {}).get(table_name, {})
    rf_config = build_row_filter_expression(settings.get("row_filter"))
    is_updated = req.is_updated or "_FIVETRAN_SYNCED"
    is_deleted = req.is_deleted or "_FIVETRAN_DELETED"

    sc = req.source_config or {}
    username = sc.get("username", "")
    password = sc.get("password", "")
    use_proxy = is_proxy_connection(sc)

    etl_state = _get_table_etl_state(
        req.connection_id, table_name, existing_conn=db_engine
    )
    server_addr = _resolve_server_address(sc)
    service_name = (
        etl_state.get("service_name") or sc.get("service_name") or "ZGW_ODATA_SRV"
    )

    load_method = (
        etl_state.get("load_method")
        if etl_state.get("load_method_locked")
        else (settings.get("load_method") or etl_state.get("load_method") or "initial")
    )
    load_method = str(load_method).strip().lower()
    delta_url = etl_state.get("delta_token")
    filter_expr = settings.get("filter_expr") or etl_state.get("row_filter_config")
    if isinstance(filter_expr, dict):
        filter_expr = None

    if (
        load_method == "initial"
        and etl_state.get("initial_completed_flag")
        and not req.drop_and_reload
    ):
        completed_at = datetime.now(timezone.utc)
        update_table_status_snowflake(
            req.connection_id,
            table_name,
            "completed",
            req.job_name,
            req.migration_session_id,
            sync_end_time=completed_at,
        )
        return {
            "table_name": table_name,
            "status": "success",
            "rows_extracted": 0,
            "rows_loaded": 0,
            "duration": round(time.time() - t_start, 2),
            "message": "Initial load already completed; table was not processed again.",
            "error": None,
        }

    if load_method == "initial":
        is_initial = True
    else:
        is_initial = (
            req.drop_and_reload
            or not etl_state.get("initial_completed_flag")
            or not delta_url
        )

    load_type = "initial" if is_initial else "delta"
    logger.info(
        "[SAP -> DBX] Table %s | Destination: %s | Mode: %s | Service: %s | Host: %s",
        table_name,
        target_table,
        load_type,
        service_name,
        server_addr,
    )

    update_table_status_snowflake(
        req.connection_id,
        table_name,
        "in_progress",
        req.job_name,
        req.migration_session_id,
    )

    staging_table = _new_staging_table_name(target_table)
    blob_name = sap_staging_blob_name(target_table, req.connection_id, parquet=False)
    dest_conn = {"schema": databricks_schema, "catalog": catalog}

    try:
        if not server_addr:
            raise ValueError(
                f"Missing SAP host/port in source config for table {table_name}"
            )
        if not service_name:
            raise ValueError(f"Missing SAP service_name for table {table_name}")

        meta_url = f"https://{server_addr}/sap/opu/odata/sap/{service_name}/$metadata"
        meta = fetch_metadata(meta_url, username, password, sc, entity_name=table_name)
        if not meta:
            raise ValueError(
                f"Failed to fetch SAP metadata for {service_name}/{table_name}"
            )
        pks = [c["name"] for c in meta.get("columns", []) if c.get("primaryKey")]

        previous_dest = (transform_state or {}).get("previous_destination_table_name")
        rename_mode = (transform_state or {}).get("destination_rename_mode")
        if (
            previous_dest
            and _dbx_ident(previous_dest) != target_table
            and rename_mode
        ):
            logger.info(
                "[SAP -> DBX] Destination rename choice=%s | %s -> %s | entity=%s",
                rename_mode,
                previous_dest,
                target_table,
                table_name,
            )
            if rename_mode == "rename_existing":
                if check_table_exists_databricks(
                    destination_engine,
                    _dbx_ident(previous_dest),
                    dest_conn,
                    req.connection_id,
                    req.cmp_id,
                ):
                    _rename_databricks_table(
                        destination_engine,
                        databricks_schema,
                        previous_dest,
                        target_table,
                        catalog=catalog,
                    )
            elif rename_mode == "create_new":
                _update_table_etl_state(
                    connection_id=req.connection_id,
                    table_name=table_name,
                    initial_completed_flag=False,
                    delta_token=None,
                    existing_conn=db_engine,
                )
                etl_state["initial_completed_flag"] = False
                delta_url = None
                is_initial = True
                load_type = "initial"
                logger.info(
                    "[SAP -> DBX] Full reload into new table %s; old table %s kept",
                    target_table,
                    previous_dest,
                )
            _clear_destination_rename_mode(req.connection_id, table_name, db_engine)

        if req.drop_and_reload:
            if check_table_exists_databricks(
                destination_engine,
                target_table,
                dest_conn,
                req.connection_id,
                req.cmp_id,
            ):
                drop_databricks_table(
                    destination_engine,
                    target_table,
                    schema=databricks_schema,
                    catalog=catalog,
                )
            _update_table_etl_state(
                connection_id=req.connection_id,
                table_name=table_name,
                initial_completed_flag=False,
                delta_token=None,
                existing_conn=db_engine,
            )
            etl_state["initial_completed_flag"] = False
            delta_url = None
            is_initial = True
            load_type = "initial"
            logger.info(
                "[SAP -> DBX] drop_and_reload: reset state for %s",
                target_table,
            )

        ddl_meta = meta
        resolved_mapping: Optional[Dict[str, Any]] = None
        sap_fields: Optional[List[Dict[str, Any]]] = None
        if transform_state:
            sap_fields = [
                {
                    "fieldname": col.get("name"),
                    "datatype": col.get("dataType"),
                    "length": col.get("maxLength") or col.get("length"),
                    "decimals": col.get("scale"),
                }
                for col in (meta.get("columns") or [])
                if col.get("name")
            ]
            resolved_mapping = resolve_effective_mapping(
                transform_state.get("mapping_json"),
                transform_state.get("mapping_overrides"),
                sap_fields,
            )
            target_meta = build_target_schema_metadata_databricks(
                resolved_mapping, meta
            )
            if target_meta.get("columns"):
                ddl_meta = target_meta
                pks = [
                    c["name"]
                    for c in target_meta.get("columns", [])
                    if c.get("primaryKey")
                ] or pks
                logger.info(
                    "[SAP -> DBX] Destination %s uses %d mapped target columns",
                    target_table,
                    len(target_meta["columns"]),
                )

        attributes_details = build_attributes_details_from_sap_metadata(
            ddl_meta,
            is_updated=is_updated,
            is_deleted=is_deleted,
        )
        table_exist = check_table_exists_databricks(
            destination_engine,
            target_table,
            dest_conn,
            req.connection_id,
            req.cmp_id,
        )
        if not table_exist:
            ensure_databricks_table_from_sap_metadata(
                destination_engine,
                target_table,
                databricks_schema,
                ddl_meta,
                req.connection_id,
                req.cmp_id,
                is_updated=is_updated,
                is_deleted=is_deleted,
                catalog=catalog,
            )
            table_exist = True

        next_delta_link: Optional[str] = None
        df: pd.DataFrame = pd.DataFrame()
        transform_version: Optional[int] = None
        select_columns: Optional[List[str]] = None
        if transform_state:
            transform_version = transform_state.get("version")
            resolved = resolved_mapping or resolve_effective_mapping(
                transform_state.get("mapping_json"),
                transform_state.get("mapping_overrides"),
            )
            select_columns = resolved.get("extract_columns") or None
            batch_cap = max(1, DRY_RUN_SAP_SELECT_BATCH_SIZE)
            if select_columns and len(select_columns) > batch_cap:
                logger.info(
                    "[SAP -> DBX] %d extract columns exceed OData $select limit (%d); "
                    "fetching full entity without $select",
                    len(select_columns),
                    batch_cap,
                )
                select_columns = None
            logger.info(
                "[SAP -> DBX] Transform v%s active for %s (%d extract columns)",
                transform_version,
                table_name,
                len(resolved.get("extract_columns") or []),
            )

        if use_proxy:
            raise ValueError(
                "SAP OData proxy mode is not yet supported for Databricks destination"
            )

        if is_initial:
            df = _fetch_initial_df(
                server_address=server_addr,
                service_name=service_name,
                table_name=table_name,
                username=username,
                password=password,
                chunk_count=req.chunk_count,
                row_filter_config=rf_config,
                now_utc=now_utc,
                filter_expr=filter_expr,
                delta_name=etl_state.get("delta_name"),
                source_config=sc,
                select_columns=select_columns,
            )
            next_delta_link = df.attrs.get("next_delta_link")
        else:
            try:
                df = _fetch_delta_df(
                    server_address=server_addr,
                    service_name=service_name,
                    table_name=table_name,
                    delta_token_url=delta_url,
                    username=username,
                    password=password,
                    chunk_count=req.chunk_count,
                    row_filter_config=rf_config,
                    now_utc=now_utc,
                    filter_expr=filter_expr,
                    select_columns=select_columns,
                )
            except ValueError as delta_exc:
                err_text = str(delta_exc)
                if (
                    "SAP delta HTTP 400" in err_text
                    or "Invalid system query option" in err_text
                ):
                    logger.warning(
                        "[SAP -> DBX] Delta pull failed for %s, falling back to initial load: %s",
                        table_name,
                        err_text[:200],
                    )
                    load_type = "initial_fallback"
                    is_initial = True
                    df = _fetch_initial_df(
                        server_address=server_addr,
                        service_name=service_name,
                        table_name=table_name,
                        username=username,
                        password=password,
                        chunk_count=req.chunk_count,
                        row_filter_config=rf_config,
                        now_utc=now_utc,
                        filter_expr=filter_expr,
                        delta_name=etl_state.get("delta_name"),
                        source_config=sc,
                        select_columns=select_columns,
                    )
                else:
                    raise
            next_delta_link = df.attrs.get("next_delta_link")

        rows_extracted = len(df)
        logger.info("[SAP -> DBX] Extracted %d rows for %s", rows_extracted, table_name)

        if transform_state and not df.empty:
            df = execute_transform_chunk(
                df,
                transform_state.get("mapping_json"),
                transform_state.get("mapping_overrides"),
                transform_state.get("script_text"),
                transform_state.get("entry_point"),
                transform_state.get("run_order") or "filter_script_mapping",
                sap_metadata_fields=sap_fields,
            )
            if resolved_mapping:
                log_one_to_many_column_fill(
                    df, resolved_mapping.get("rows") or []
                )

        rows_loaded = 0
        if not df.empty:
            df = _prep_df(df, is_updated, is_deleted, now_utc, is_initial=is_initial)
            # Align DataFrame headers with copy_function / staging DDL keys
            # (HWAE2 → HWAE_2). Plain .upper() caused COPY_INTO schema mismatch.
            renamed = {
                c: normalize_sap_column_for_databricks(c) for c in df.columns
            }
            df = df.rename(columns=renamed)
            if df.columns.duplicated().any():
                df = df.loc[:, ~df.columns.duplicated()]
            # Keep only columns present in attributes (plus audit cols already set)
            keep = [c for c in df.columns if c in attributes_details]
            if keep:
                df = df[keep]

            primary_keys = _primary_key_for_merge(pks, list(df.columns))
            logger.info(
                "[SAP -> DBX] MERGE primary keys for %s: %s",
                target_table,
                primary_keys,
            )
            try:
                safety_seconds = int(float(req.safety_interval or 120))
            except (TypeError, ValueError):
                safety_seconds = 120

            copy_function(
                df,
                target_table,
                False,
                destination_engine,
                databricks_schema,
                target_table,
                table_name,
                True,
                [],
                table_exist,
                {},
                attributes_details,
                attributes_details,
                req.connection_id,
                primary_keys,
                catalog or "",
                req.cmp_id,
                req.migration_session_id,
                req.job_name,
                staging_table,
                req.user_id,
                req.session,
                None,
                None,
                safety_seconds,
                blob_name,
                updated_at=str(is_updated).upper(),
                deleted_at=str(is_deleted).upper(),
            )
            rows_loaded = len(df.index)

        try:
            drop_staging_table(
                destination_engine,
                staging_table,
                schema=databricks_schema,
                catalog=catalog,
            )
        except Exception as drop_stg_exc:
            logger.warning(
                "[SAP -> DBX] drop_staging_failed | staging=%s | err=%s",
                staging_table,
                drop_stg_exc,
            )

        if next_delta_link or is_initial:
            _update_table_etl_state(
                connection_id=req.connection_id,
                table_name=table_name,
                initial_completed_flag=True,
                delta_token=next_delta_link or delta_url,
                existing_conn=db_engine,
            )

        duration = time.time() - t_start
        table_completed_at = datetime.now(timezone.utc)
        data_transfer_logs(
            cmp_id=req.cmp_id,
            connection_id=req.connection_id,
            usr_id=req.user_id,
            table_name=table_name,
            start_time=datetime.fromtimestamp(t_start, tz=timezone.utc),
            end_time=table_completed_at,
            inserts=rows_loaded,
            columns_data=len(df.columns) if not df.empty else 0,
            session=req.session,
            job_name=req.job_name,
            migration_session_id=req.migration_session_id,
        )
        update_table_status_snowflake(
            req.connection_id,
            table_name,
            "completed",
            req.job_name,
            req.migration_session_id,
            sync_end_time=table_completed_at,
        )

        if transform_version and req.migration_session_id:
            try:
                cursor = db_engine.cursor()
                try:
                    cursor.execute(
                        """
                        UPDATE migration_record
                        SET transform_version = %s
                        WHERE connection_id = %s
                          AND migration_session_id = %s
                          AND table_name = %s
                        """,
                        (
                            transform_version,
                            req.connection_id,
                            req.migration_session_id,
                            table_name,
                        ),
                    )
                    db_engine.commit()
                finally:
                    cursor.close()
            except Exception as tv_err:
                logger.warning(
                    "[SAP -> DBX] Failed to record transform_version for %s: %s",
                    table_name,
                    tv_err,
                )

        return {
            "table_name": table_name,
            "destination_table_name": target_table,
            "status": "success",
            "rows_extracted": rows_extracted,
            "rows_loaded": rows_loaded,
            "duration": round(duration, 2),
            "load_type": load_type,
            "transform_version": transform_version,
            "error": None,
        }

    except Exception as exc:
        try:
            drop_staging_table(
                destination_engine,
                staging_table,
                schema=databricks_schema,
                catalog=catalog,
            )
        except Exception as drop_err:
            logger.warning(
                "[SAP -> DBX] Failed to drop staging table %s after error: %s",
                staging_table,
                drop_err,
            )
        err_msg = str(
            getattr(exc, "detail", exc) if isinstance(exc, HTTPException) else exc
        )
        logger.error(
            "[SAP -> DBX] Table %s failed: %s\n%s",
            table_name,
            err_msg,
            tb_module.format_exc(),
        )
        failure_tracker.add_failure(table_name, err_msg, "SAP OData to Databricks")
        _record_table_failure(req, table_name, err_msg, db_engine)
        return {
            "table_name": table_name,
            "status": "failed",
            "error": err_msg,
            "rows_loaded": 0,
            "duration": round(time.time() - t_start, 2),
            "load_type": load_type,
        }


def _build_connector_response(
    req: SAPODataDatabricksMigrationRequest,
    results: List[Dict[str, Any]],
    total_time: float,
    failure_tracker: MigrationFailureTracker,
) -> Dict[str, Any]:
    """Normalize connector output for Celery child/finalizer handling."""
    successful = [
        r
        for r in results
        if str(r.get("status", "")).lower() in ("success", "completed")
    ]
    failed = [
        r
        for r in results
        if str(r.get("status", "")).lower() not in ("success", "completed")
    ]
    total_rows = sum(int(r.get("rows_loaded") or 0) for r in results)
    has_failures = bool(failed) or failure_tracker.has_failures()

    if has_failures and failure_tracker.has_failures():
        summary_error = "; ".join(
            f"{f.get('table_name')}: {f.get('error_message')}"
            for f in failure_tracker.get_failure_summary().get("failed_tables", [])
        )
    elif failed:
        summary_error = "; ".join(
            f"{r.get('table_name')}: {r.get('error')}"
            for r in failed
            if r.get("table_name")
        )
    else:
        summary_error = None

    completion_message = (
        summary_error
        if has_failures
        else f"Migration completed successfully in {total_time:.2f} seconds. Total rows: {total_rows}."
    )

    if req.job_name:
        update_migration_status_log(
            job_name=req.job_name,
            message=completion_message,
            status="F" if has_failures else "S",
            session=req.session,
            log_id=req.log_id,
            celery_child_mode=_resolve_celery_child_mode(req),
        )

    logger.info(
        "[SAP -> DBX] Completed in %ss. ok=%d failed=%d total_rows=%d",
        total_time,
        len(successful),
        len(failed),
        total_rows,
    )

    response: Dict[str, Any] = {
        "message": completion_message,
        "total_rows": total_rows,
        "total_rows_loaded": total_rows,
        "execution_time": total_time,
        "duration_seconds": total_time,
        "total_tables": len(req.selected_tables),
        "successful_tables_count": len(successful),
        "failed_tables_count": len(failed),
        "successful_tables_list": [
            r["table_name"] for r in successful if r.get("table_name")
        ],
        "failed_tables_list": [r["table_name"] for r in failed if r.get("table_name")],
        "tables": results,
    }
    if has_failures and len(failed) == len(req.selected_tables):
        response["status"] = "failed"
    return response


def sap_odata_to_databricks_connector(
    req: SAPODataDatabricksMigrationRequest,
) -> Dict[str, Any]:
    """Master orchestrator for SAP OData to Databricks replication."""
    reset_timing_records()
    t0 = time.time()
    db_engine = postgres_connection()
    failure_tracker = MigrationFailureTracker()
    results: List[Dict[str, Any]] = []
    celery_child_mode = _resolve_celery_child_mode(req)
    execution_order = req.execution_order
    if celery_child_mode and execution_order == "parallel":
        execution_order = "sequential"

    try:
        destination_engine, databricks_schema = destination_connection_function(
            req.destination_config,
            req.connection_id,
            req.cmp_id,
            migration_session_id=req.migration_session_id,
            job_name=req.job_name,
            usr_id=req.user_id,
            session=req.session,
        )
        catalog = None
        if hasattr(destination_engine, "config") and isinstance(
            destination_engine.config, dict
        ):
            catalog = (destination_engine.config.get("catalog") or "").strip() or None
        if not catalog:
            catalog = (req.destination_config.get("catalog") or "").strip() or None

        max_workers = (
            min(len(req.selected_tables), 4) if execution_order == "parallel" else 1
        )
        logger.info(
            "[SAP -> DBX] Starting migration of %d tables (workers=%d, celery_child=%s)",
            len(req.selected_tables),
            max_workers,
            celery_child_mode,
        )

        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _migrate_single_table_databricks,
                        req,
                        tbl,
                        db_engine,
                        destination_engine,
                        databricks_schema,
                        catalog,
                        f"{int(t0)}_{idx}",
                        failure_tracker,
                    ): tbl
                    for idx, tbl in enumerate(req.selected_tables)
                }
                for fut in as_completed(futures):
                    results.append(fut.result())
        else:
            for idx, tbl in enumerate(req.selected_tables):
                results.append(
                    _migrate_single_table_databricks(
                        req,
                        tbl,
                        db_engine,
                        destination_engine,
                        databricks_schema,
                        catalog,
                        f"{int(t0)}_{idx}",
                        failure_tracker,
                    )
                )

        return _build_connector_response(
            req, results, round(time.time() - t0, 2), failure_tracker
        )

    except Exception as exc:
        err_msg = str(exc)
        logger.error(
            "[SAP -> DBX] Orchestrator failed: %s\n%s", err_msg, tb_module.format_exc()
        )
        for tbl in req.selected_tables:
            if not any(r.get("table_name") == tbl for r in results):
                failure_tracker.add_failure(tbl, err_msg, "SAP OData to Databricks")
                _record_table_failure(req, tbl, err_msg, db_engine)
                results.append(
                    {
                        "table_name": tbl,
                        "status": "failed",
                        "error": err_msg,
                        "rows_loaded": 0,
                    }
                )
        response = _build_connector_response(
            req, results, round(time.time() - t0, 2), failure_tracker
        )
        response["status"] = "failed"
        return response
