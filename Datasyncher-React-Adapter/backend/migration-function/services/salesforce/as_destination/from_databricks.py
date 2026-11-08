"""Salesforce as destination ← Databricks reverse ETL orchestrator."""

from __future__ import annotations

import json
import multiprocessing
import time
import traceback as tb_module
from datetime import datetime, timezone

from fastapi import HTTPException

from models.salesforce_models import SalesforceMigration
from services.salesforce.as_destination.retl_common import (
    SharedListFailureTracker,
    get_matching_fields,
    mark_unmapped_source_tables,
    process_mapping_core,
)
from services.salesforce.as_destination.sources.databricks import DatabricksExtractAdapter
from services.salesforce.client import MigrationFailureTracker
from utils.celery_cleanup_utils import handle_migration_completion

DATABRICKS_RETL_SERVICE = "Databricks to Salesforce"
DATABRICKS_RETL_LABEL = "Databricks ➜ Salesforce"


def _adapter_for(connection_id, cmp_id, user_id, session, job_name, migration_session_id):
    return DatabricksExtractAdapter(
        connection_id=connection_id,
        cmp_id=cmp_id,
        user_id=user_id,
        session=session,
        job_name=job_name,
        migration_session_id=migration_session_id,
    )


def process_mapping(
    matching_fields,
    databricks_details,
    salesforce_details,
    connection_id,
    session,
    job_name,
    migration_session_id,
    result_queue,
    cmp_id,
    user_id,
    environment,
    chunk_count,
    logs_creation,
    connection_name,
    company_name,
    failure_tracker=None,
    is_final_attempt=True,
):
    return process_mapping_core(
        matching_fields,
        databricks_details,
        salesforce_details,
        connection_id,
        session,
        job_name,
        migration_session_id,
        result_queue,
        cmp_id,
        user_id,
        environment,
        chunk_count,
        logs_creation,
        connection_name,
        company_name,
        extract_adapter=_adapter_for(
            connection_id, cmp_id, user_id, session, job_name, migration_session_id
        ),
        failure_tracker=failure_tracker,
        is_final_attempt=is_final_attempt,
        service_name=DATABRICKS_RETL_SERVICE,
        migration_type_label=DATABRICKS_RETL_LABEL,
        update_staging=True,
    )


def process_mapping_with_shared_tracker(
    matching_fields,
    databricks_details,
    salesforce_details,
    connection_id,
    session,
    job_name,
    migration_session_id,
    result_queue,
    cmp_id,
    user_id,
    environment,
    chunk_count,
    logs_creation,
    connection_name,
    company_name,
    shared_failed_tables,
    is_final_attempt=True,
):
    tracker = SharedListFailureTracker(shared_failed_tables)
    return process_mapping_core(
        matching_fields,
        databricks_details,
        salesforce_details,
        connection_id,
        session,
        job_name,
        migration_session_id,
        result_queue,
        cmp_id,
        user_id,
        environment,
        chunk_count,
        logs_creation,
        connection_name,
        company_name,
        extract_adapter=_adapter_for(
            connection_id, cmp_id, user_id, session, job_name, migration_session_id
        ),
        failure_tracker=tracker,
        is_final_attempt=is_final_attempt,
        service_name=DATABRICKS_RETL_SERVICE,
        migration_type_label=DATABRICKS_RETL_LABEL,
        update_staging=False,
    )


async def sync_databricks_to_salesforce(request: SalesforceMigration, environment: str):
    try:
        print("DEBUG: Received Databricks→Salesforce Request")
        print(
            f"DEBUG: request.source_config type: {type(request.source_config)}, "
            f"Value: {json.dumps(request.source_config, indent=2)}"
        )
        print(
            f"DEBUG: request.destination_config type: {type(request.destination_config)}, "
            f"Value: {json.dumps(request.destination_config, indent=2)}"
        )
        print(
            f"DEBUG: request.selected_tables type: {type(request.selected_tables)}, "
            f"Value: {json.dumps(request.selected_tables, indent=2)}"
        )

        databricks_details = request.source_config
        salesforce_details = request.destination_config
        connection_id = request.connection_id
        session = request.session
        mapping_list = request.selected_tables
        job_name = request.job_name
        migration_session_id = request.migration_session_id
        cmp_id = request.cmp_id
        user_id = request.user_id
        use_multiprocessing = request.execution_order
        celery_child_mode = bool(getattr(request, "celery_child_mode", False))
        if celery_child_mode and use_multiprocessing == "parallel":
            use_multiprocessing = "sequential"
        chunk_count = request.chunk_count
        logs_creation = request.logs_creation
        connection_name = request.connection_name
        company_name = request.company_name
        log_id = getattr(request, "log_id", None)

        matching_fields_list = get_matching_fields(connection_id, mapping_list)
        print(f"DEBUG: Matching Fields List: {json.dumps(matching_fields_list, indent=2)}")

        is_final_attempt = getattr(request, "is_final_attempt", True)
        retry_attempt = getattr(request, "retry_attempt", 0)
        print(
            f"📊 Retry Info: Attempt {retry_attempt + 1}/3, Final: {is_final_attempt}, "
            f"Emails: {'ENABLED' if is_final_attempt else 'DISABLED'}"
        )

        failure_tracker = MigrationFailureTracker()
        mark_unmapped_source_tables(
            mapping_list=mapping_list,
            matching_fields_list=matching_fields_list,
            failure_tracker=failure_tracker,
            service_name=DATABRICKS_RETL_SERVICE,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            cmp_id=cmp_id,
            user_id=user_id,
            session=session,
            is_final_attempt=is_final_attempt,
        )

        total_rows_transmitted = 0
        start_time = time.time()

        if use_multiprocessing == "parallel":
            print("⚠️ Parallel execution detected - using shared manager for failure tracking")
            processes = []
            result_queue = multiprocessing.Queue()
            manager = multiprocessing.Manager()
            shared_failed_tables = manager.list()

            for matching_fields in matching_fields_list:
                process = multiprocessing.Process(
                    target=process_mapping_with_shared_tracker,
                    args=(
                        matching_fields,
                        databricks_details,
                        salesforce_details,
                        connection_id,
                        session,
                        job_name,
                        migration_session_id,
                        result_queue,
                        cmp_id,
                        user_id,
                        environment,
                        chunk_count,
                        logs_creation,
                        connection_name,
                        company_name,
                        shared_failed_tables,
                        is_final_attempt,
                    ),
                )
                processes.append(process)
                process.start()

            for process in processes:
                process.join()

            while not result_queue.empty():
                total_rows_transmitted += result_queue.get()

            for failure_info in shared_failed_tables:
                table_name, error_message, service_name = failure_info
                if failure_tracker:
                    failure_tracker.add_failure(table_name, error_message, service_name)
        else:
            for matching_fields in matching_fields_list:
                rows = process_mapping(
                    matching_fields,
                    databricks_details,
                    salesforce_details,
                    connection_id,
                    session,
                    job_name,
                    migration_session_id,
                    None,
                    cmp_id,
                    user_id,
                    environment,
                    chunk_count,
                    logs_creation,
                    connection_name,
                    company_name,
                    failure_tracker,
                    is_final_attempt,
                )
                total_rows_transmitted += rows if rows else 0
        print("total rows transmitted", total_rows_transmitted)

        print(
            f"⏱️ Starting cleanup at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC"
        )
        try:
            cleanup_result = handle_migration_completion(
                connection_name=connection_name or f"SALESFORCE_DATABRICKS_{connection_id}",
                job_name=job_name,
                completion_message=None,
            )
            print(
                f"⏱️ Cleanup completed at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC"
            )
            if cleanup_result["status"] == "success" and cleanup_result["cleaned_jobs"]:
                print(
                    f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} stuck celery jobs "
                    f"for Databricks to Salesforce migration"
                )
            elif cleanup_result["status"] == "error":
                print(f"⚠️ Cleanup warning for Databricks to Salesforce: {cleanup_result['message']}")
        except Exception as cleanup_error:
            print(f"⚠️ Failed to cleanup celery jobs: {cleanup_error}")
            tb_module.print_exc()

        failure_tracker.send_consolidated_error_email(
            connection_name=connection_name,
            service_name=DATABRICKS_RETL_SERVICE,
            user_id=user_id,
            cmp_id=cmp_id,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            company_name=company_name,
            is_final_attempt=is_final_attempt,
        )

        execution_time_sec = time.time() - start_time
        _failed_list = list(getattr(failure_tracker, "failed_tables", []) or [])
        _all_tables = list(matching_fields_list or [])
        _failed_table_names = MigrationFailureTracker.failed_table_names(_failed_list)
        _successful_list = [
            mapping
            for mapping in _all_tables
            if isinstance(mapping, dict)
            and str(mapping.get("source_table") or "") not in _failed_table_names
        ]
        if not celery_child_mode:
            from utils.error_utils import log_migration_completion

            log_migration_completion(
                execution_time=execution_time_sec,
                total_rows=total_rows_transmitted,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                session=session,
                total_tables=len(_all_tables),
                successful_tables=len(_successful_list),
                failed_tables=len(_failed_list),
                job_name=job_name,
                log_id=log_id,
                include_operation_counts=False,
            )
        else:
            print(
                f"🧩 CHILD_MODE: skipping per-child completion log for job={job_name}, "
                f"tables={len(_successful_list)}/{len(_all_tables)} successful, rows={total_rows_transmitted}"
            )

        try:
            from utils.websocket_utils import (
                trigger_connection_ws_status_update,
                trigger_ws_status_update,
            )

            trigger_ws_status_update(migration_session_id)
            trigger_connection_ws_status_update(connection_id)
        except Exception as ws_err:
            print(f"⚠️ Final RETL completion websocket trigger failed: {ws_err}")

        return {
            "message": "Data synced successfully",
            "total_rows": total_rows_transmitted,
            "execution_time": execution_time_sec,
            "total_tables": len(_all_tables),
            "successful_tables_count": len(_successful_list),
            "failed_tables_count": len(_failed_list),
            "successful_tables_list": _successful_list,
            "failed_tables_list": _failed_list,
        }

    except Exception as e:
        from fastapi import HTTPException as FastAPIHTTPException

        is_http_exception = isinstance(e, (FastAPIHTTPException, HTTPException))
        print(f"ERROR: Unexpected error: {e}")
        tb_module.print_exc()

        from utils.error_utils import handle_service_error

        error_context = {
            "table_name": "Migration Process",
            "service_name": DATABRICKS_RETL_SERVICE,
            "error_type": "MIGRATION_FAILED",
            "cmp_id": cmp_id if "cmp_id" in locals() else None,
            "connection_id": connection_id if "connection_id" in locals() else None,
            "user_id": user_id if "user_id" in locals() else None,
            "job_name": job_name if "job_name" in locals() else None,
            "log_id": log_id if "log_id" in locals() else None,
            "migration_session_id": (
                migration_session_id if "migration_session_id" in locals() else None
            ),
            "company_name": company_name if "company_name" in locals() else None,
            "session": session if "session" in locals() else None,
            "total_tables": len(matching_fields_list) if "matching_fields_list" in locals() else 0,
            "execution_time": time.time() - start_time if "start_time" in locals() else 0.0,
            "total_rows": total_rows_transmitted if "total_rows_transmitted" in locals() else 0,
            "successful_tables": 0,
        }

        error_result = handle_service_error(
            error=e,
            context=error_context,
            log_files=None,
            send_email=True,
            is_final_attempt=is_final_attempt if "is_final_attempt" in locals() else True,
        )

        if error_result.get("tool_log_logged", error_result.get("log_logged", False)):
            print("✅ Migration error logged to log")
        if error_result.get("email_sent", False):
            print("📧 Migration error notification email sent")
        elif not error_result.get("email_sent", False):
            print("⚠️ Failed to send migration error notification email")

        if is_http_exception:
            raise e
        error_message = str(e).strip() if str(e).strip() else repr(e)
        error_str_low = error_message.lower()
        if any(
            keyword in error_str_low
            for keyword in [
                "request limit",
                "rate limit",
                "too many requests",
                "503",
                "429",
                "service unavailable",
                "request_limit_exceeded",
            ]
        ):
            raise HTTPException(status_code=429, detail=error_message)
        raise HTTPException(status_code=500, detail=error_message)
