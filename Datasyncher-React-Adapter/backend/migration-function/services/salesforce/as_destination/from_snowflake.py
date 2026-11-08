"""Salesforce as destination ← Snowflake orchestrator."""

from __future__ import annotations

import requests
import traceback as tb_module
import hashlib
import base64
import secrets
import redis
import json
from datetime import datetime, timedelta
from fastapi import HTTPException
from simple_salesforce import Salesforce
import psycopg2
import os
import time
from psycopg2 import OperationalError
from services.snowflake.service import normalize_pem_key
from datetime import datetime
from utils.celery_cleanup_utils import handle_migration_completion
from utils.database_utils import data_transfer_logs
from utils.snowflake_utils import drop_staging_table, normalize_name
from utils.simple_progress_tracker import SimpleProgressTracker
import snowflake.connector
from urllib.parse import quote
from typing import List
from models.salesforce_models import SalesforceMigration,SalesforceSnowflakeMigration
from datetime import datetime, timezone
import multiprocessing
from pathlib import Path
import re
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import pandas as pd
import asyncio
import csv
import io
from typing import List, Dict, Any, Optional
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
import ast
import math
from decimal import Decimal
from datetime import datetime, date, timezone
from typing import Dict, List, Any, Optional
import logging
from sqlalchemy import text
from sqlalchemy.sql import text, bindparam
from sqlalchemy.types import JSON as SQLAlchemyJSON
from dateutil import parser
import numpy as np
from typing import List, Dict, Any, Set
from google.cloud import storage
from google.api_core.exceptions import NotFound
from google.cloud import storage, firestore
from google.auth import compute_engine
import gc
from google.api_core.exceptions import ServiceUnavailable
from sqlalchemy import insert, Table, MetaData
from typing import List, Dict, Any
import pytz
from pandas.errors import OutOfBoundsDatetime
from typing import List, Dict
from datetime import timedelta
from utils.salesforce_dynamic_checker import (
    is_salesforce_object_queryable,
    initialize_dynamic_checker,
    batch_check_object_queryability
)
from typing import Optional, Dict

from services.salesforce.client import (
    MigrationFailureTracker,
    update_table_status,
    update_table_status_timestamp_sf_to_snowflake,
)
from services.salesforce.as_destination.retl_common import (
    SharedListFailureTracker,
    bulk_upsert_to_salesforce,
    get_matching_fields,
    mark_unmapped_source_tables,
    process_mapping_core,
)
from services.salesforce.as_destination.sources.snowflake import (
    SnowflakeExtractAdapter,
    fetch_chunks_from_temp_table,
)

_SNOWFLAKE_ADAPTER = SnowflakeExtractAdapter()
SNOWFLAKE_RETL_SERVICE = "Snowflake to Salesforce"
SNOWFLAKE_RETL_LABEL = "Snowflake ➜ Salesforce"


def process_mapping(
    matching_fields, snowflake_details, salesforce_details,
    connection_id, session, job_name, migration_session_id,
    result_queue, cmp_id, user_id, environment, chunk_count,
    logs_creation, connection_name, company_name, failure_tracker=None,
    is_final_attempt=True  # ✅ ADD: Email deduplication flag
):
    return process_mapping_core(
        matching_fields,
        snowflake_details,
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
        extract_adapter=_SNOWFLAKE_ADAPTER,
        failure_tracker=failure_tracker,
        is_final_attempt=is_final_attempt,
        service_name=SNOWFLAKE_RETL_SERVICE,
        migration_type_label=SNOWFLAKE_RETL_LABEL,
        update_staging=True,
    )


def process_mapping_with_shared_tracker(
    matching_fields, snowflake_details, salesforce_details,
    connection_id, session, job_name, migration_session_id,
    result_queue, cmp_id, user_id, environment, chunk_count,
    logs_creation, connection_name, company_name, shared_failed_tables,
    is_final_attempt=True  # ✅ ADD: Email deduplication flag
):
    tracker = SharedListFailureTracker(shared_failed_tables)
    return process_mapping_core(
        matching_fields,
        snowflake_details,
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
        extract_adapter=_SNOWFLAKE_ADAPTER,
        failure_tracker=tracker,
        is_final_attempt=is_final_attempt,
        service_name=SNOWFLAKE_RETL_SERVICE,
        migration_type_label=SNOWFLAKE_RETL_LABEL,
        update_staging=False,
    )


async def sync_snowflake_to_salesforce(request: SalesforceMigration,environment:str):
    try:
        print("DEBUG: Received Request")
        print(f"DEBUG: request.source_config type: {type(request.source_config)}, Value: {json.dumps(request.source_config, indent=2)}")
        print(f"DEBUG: request.destination_config type: {type(request.destination_config)}, Value: {json.dumps(request.destination_config, indent=2)}")
        print(f"DEBUG: request.selected_tables type: {type(request.selected_tables)}, Value: {json.dumps(request.selected_tables, indent=2)}")

        snowflake_details = request.source_config
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
        chunk_count=request.chunk_count
        logs_creation=request.logs_creation
        connection_name=request.connection_name
        company_name=request.company_name
        drop_and_reload = getattr(request, 'drop_and_reload', False)  # ✅ Extract drop_and_reload
        log_id = getattr(request, 'log_id', None)  # ✅ Extract log_id from request

        matching_fields_list = get_matching_fields(connection_id, mapping_list)
        print(f"DEBUG: Matching Fields List: {json.dumps(matching_fields_list, indent=2)}")

        # ✅ Extract retry attempt info from request (for email deduplication)
        is_final_attempt = getattr(request, 'is_final_attempt', True)  # Default to True for safety
        retry_attempt = getattr(request, 'retry_attempt', 0)
        max_retries = getattr(request, 'max_retries', 3)
        
        print(f"📊 Retry Info: Attempt {retry_attempt + 1}/3, Final: {is_final_attempt}, Emails: {'ENABLED' if is_final_attempt else 'DISABLED'}")

        # Initialize failure tracker for consolidated error reporting
        failure_tracker = MigrationFailureTracker()

        mark_unmapped_source_tables(
            mapping_list=mapping_list,
            matching_fields_list=matching_fields_list,
            failure_tracker=failure_tracker,
            service_name=SNOWFLAKE_RETL_SERVICE,
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
            
            # Create a manager to share data between processes
            manager = multiprocessing.Manager()
            shared_failed_tables = manager.list()

            # Create and start a process for each mapping
            for matching_fields in matching_fields_list:
                process = multiprocessing.Process(
                    target=process_mapping_with_shared_tracker,
                    args=(matching_fields, snowflake_details, salesforce_details, connection_id, session, job_name, migration_session_id, result_queue, cmp_id, user_id,environment,chunk_count,logs_creation,connection_name,company_name, shared_failed_tables, is_final_attempt)  # ✅ ADD: Email deduplication
                )
                processes.append(process)
                process.start()

            # Wait for all processes to complete
            for process in processes:
                process.join()

            # Collect results from the queue
            while not result_queue.empty():
                total_rows_transmitted += result_queue.get()
            
            # Update the main failure tracker with failures from parallel processes
            for failure_info in shared_failed_tables:
                table_name, error_message, service_name = failure_info
                if failure_tracker:
                    failure_tracker.add_failure(table_name, error_message, service_name)
        else:
            # Sequential execution
            for matching_fields in matching_fields_list:
                rows = process_mapping(matching_fields, snowflake_details, salesforce_details, connection_id, session, job_name, migration_session_id, None, cmp_id, user_id,environment,chunk_count,logs_creation,connection_name,company_name, failure_tracker, is_final_attempt)  # ✅ ADD: Email deduplication
                total_rows_transmitted += rows if rows else 0
        print("total rows transmitted",total_rows_transmitted)
        
        # Clean up celery jobs to prevent stuck job scenarios
        print(f"⏱️ Starting cleanup at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")
        try:
            cleanup_result = handle_migration_completion(
                connection_name=connection_name or f"SALESFORCE_SNOWFLAKE_{connection_id}",
                job_name=job_name,
                completion_message=None
            )
            print(f"⏱️ Cleanup completed at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")
            
            if cleanup_result['status'] == 'success' and cleanup_result['cleaned_jobs']:
                print(f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} stuck celery jobs for Snowflake to Salesforce migration")
            elif cleanup_result['status'] == 'error':
                print(f"⚠️ Cleanup warning for Snowflake to Salesforce: {cleanup_result['message']}")
                
        except Exception as cleanup_error:
            # Don't fail the migration if cleanup fails - just log the warning
            print(f"⚠️ Failed to cleanup celery jobs: {cleanup_error}")
            tb_module.print_exc()

        # Send consolidated error email if there were any failures
        failure_tracker.send_consolidated_error_email(
            connection_name=connection_name,
            service_name="Snowflake to Salesforce",
            user_id=user_id,
            cmp_id=cmp_id,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            company_name=company_name,
            is_final_attempt=is_final_attempt  # ✅ Only send on final attempt
        )

        # ✅ Finalize the migration log
        # In fan-out child mode, each child only sees its own table subset. Skip
        # the per-child log so the Celery finalizer can write one correct
        # aggregated entry across all children.
        execution_time_sec = time.time() - start_time
        _failed_list = list(getattr(failure_tracker, "failed_tables", []) or [])
        _all_tables = list(matching_fields_list or [])
        _failed_table_names = MigrationFailureTracker.failed_table_names(_failed_list)
        _successful_list = [
            mapping for mapping in _all_tables
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
                include_operation_counts=False
            )
        else:
            print(
                f"🧩 CHILD_MODE: skipping per-child completion log for job={job_name}, "
                f"tables={len(_successful_list)}/{len(_all_tables)} successful, rows={total_rows_transmitted}"
            )

        # Ensure final migration-status and connection-status websocket push for RETL completion.
        try:
            from utils.websocket_utils import trigger_ws_status_update, trigger_connection_ws_status_update
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
        # ✅ Detect if this is an HTTPException for pass-through
        from fastapi import HTTPException as FastAPIHTTPException
        is_http_exception = isinstance(e, (FastAPIHTTPException, HTTPException))

        print(f"ERROR: Unexpected error: {e}")
        tb_module.print_exc()

        # Log overall migration error using centralized error handling
        from utils.error_utils import handle_service_error

        error_context = {
            "table_name": "Migration Process",
            "service_name": "Snowflake to Salesforce",
            "error_type": "MIGRATION_FAILED",
            "cmp_id": cmp_id,
            "connection_id": connection_id,
            "user_id": user_id,
            "job_name": job_name,
            "log_id": log_id, # ✅ Pass log_id for stable updates
            "migration_session_id": migration_session_id,
            "company_name": company_name,
            "session": session,
            "total_tables": len(matching_fields_list) if 'matching_fields_list' in locals() else 0,
            "execution_time": time.time() - start_time if 'start_time' in locals() else 0.0,
            "total_rows": total_rows_transmitted if 'total_rows_transmitted' in locals() else 0,
            "successful_tables": 0
        }

        # Log to log and send email
        error_result = handle_service_error(
            error=e,
            context=error_context,
            log_files=None,  # No file logging for overall migration errors
            send_email=True,
            is_final_attempt=is_final_attempt  # ✅ Only send email on final attempt
        )

        if error_result.get("tool_log_logged", error_result.get("log_logged", False)):
            print(f"✅ Migration error logged to log")
        if error_result.get("email_sent", False):
            print(f"📧 Migration error notification email sent")
        elif not error_result.get("email_sent", False):
            print(f"⚠️ Failed to send migration error notification email")

        # ✅ Final Hardening: Use True pass-through for HTTPException
        if is_http_exception:
            raise e
        else:
            # For non-HTTP exceptions, check for request limit errors
            error_message = str(e).strip() if str(e).strip() else repr(e)
            error_str_low = error_message.lower()
            
            if any(keyword in error_str_low for keyword in ['request limit', 'rate limit', 'too many requests', '503', '429', 'service unavailable', 'request_limit_exceeded']):
                raise HTTPException(status_code=429, detail=error_message)
            
            # Format other errors properly
            raise HTTPException(status_code=500, detail=error_message)


def run_sync_in_subprocess(args_and_kwargs):
    """
    Stand-alone wrapper for subprocess execution.
    Unpacks (args, kwargs) tuple.
    """
    import asyncio
    
    # Unpack tuple
    args, kwargs = args_and_kwargs
    
    try:
        async def run_migration():
            return await process_salesforce_to_snowflake_mapping(
                *args, **kwargs
            )
        
        result = asyncio.run(run_migration())
        return result
    except Exception as e:
        print(f"❌ Subprocess execution failed: {e}")
        raise


def get_optimal_process_strategy(table_count, available_memory_gb, cpu_count):
    """
    Determine the best processing strategy based on system resources.
    """
    memory_per_process_mb = 75  # Estimated memory per process
    total_memory_needed_gb = (table_count * memory_per_process_mb) / 1024

    print(f"📊 RESOURCE ANALYSIS:")
    print(f"   Tables to process: {table_count}")
    print(f"   Available memory: {available_memory_gb:.1f}GB")
    print(f"   CPU cores: {cpu_count}")
    print(f"   Estimated memory needed: {total_memory_needed_gb:.1f}GB")

    if table_count <= 5:
        print("🎯 Strategy: INDIVIDUAL (small table count)")
        return "individual", table_count
    elif total_memory_needed_gb > available_memory_gb * 0.6:  # Use 60% of available memory
        # Use batched approach to limit memory usage
        max_concurrent = max(2, min(cpu_count * 2, int((available_memory_gb * 0.6 * 1024) / memory_per_process_mb)))
        print(f"🎯 Strategy: BATCHED_INDIVIDUAL (memory limited, max_concurrent: {max_concurrent})")
        return "batched_individual", max_concurrent
    else:
        # Use individual processes but limit concurrent processes
        max_concurrent = min(table_count, cpu_count * 3)
        print(f"🎯 Strategy: BATCHED_INDIVIDUAL (optimal, max_concurrent: {max_concurrent})")
        return "batched_individual", max_concurrent


def run_sync_in_subprocess_with_monitoring(args_and_kwargs, table_name):
    """
    Wrapper for subprocess execution with enhanced monitoring using Salesforce job status.
    Unpacks (args, kwargs) tuple.
    """
    process_id = os.getpid()
    print(f"🚀 PROCESS {process_id}: Starting table '{table_name}'")

    try:
        # Unpack tuple
        args, kwargs = args_and_kwargs
        
        # Update timestamp before starting
        update_table_status_timestamp_sf_to_snowflake(args, table_name, "processing", f"Started in process {process_id}")

        # Run the actual processing with Salesforce job monitoring
        result = run_sync_in_subprocess_with_salesforce_monitoring(args_and_kwargs, table_name, process_id)

        return result

    except Exception as e:
        print(f"❌ PROCESS {process_id}: Table '{table_name}' failed: {e}")
        # Unpack again if possible for status update
        if isinstance(args_and_kwargs, tuple) and len(args_and_kwargs) == 2:
            args, _ = args_and_kwargs
            update_table_status_timestamp_sf_to_snowflake(args, table_name, "failed", f"Failed in process {process_id}: {str(e)}")
        raise


class SharedFailureTracker:
    """
    Wrapper class that provides the same interface as MigrationFailureTracker
    but delegates to a shared multiprocessing list.
    """
    def __init__(self, shared_list):
        self.shared_list = shared_list
        self.failed_tables = []  # For compatibility with len(failure_tracker.failed_tables)
    
    def add_failure(self, table_name, error_message, service_name):
        """Add a failure to the shared list"""
        self.shared_list.append((table_name, error_message, service_name))
        self.failed_tables.append(table_name)  # Keep local copy for len() calculation


def run_sync_in_subprocess_with_shared_tracking(args_and_kwargs, table_name):
    """
    Wrapper for subprocess execution with shared failure tracking.
    Unpacks (args, kwargs) tuple passed from parent process.
    """
    process_id = os.getpid()
    print(f"🚀 PROCESS {process_id}: Starting table '{table_name}' with shared tracking")

    try:
        # Unpack the tuple
        args, kwargs = args_and_kwargs
        
        # Update timestamp before starting
        update_table_status_timestamp_sf_to_snowflake(args, table_name, "processing", f"Started in process {process_id}")

        # Run the actual migration with correctly unpacked args and kwargs
        # Keep ONLY this call, ensuring signature alignment
        result = run_sync_in_subprocess_with_shared_tracking_inner(args, kwargs, table_name, process_id)

        return result

    except Exception as e:
        print(f"❌ PROCESS {process_id}: Table '{table_name}' failed: {e}")
        # Unpack again if possible for status update
        if isinstance(args_and_kwargs, tuple) and len(args_and_kwargs) == 2:
            args, kwargs = args_and_kwargs
            update_table_status_timestamp_sf_to_snowflake(args, table_name, "failed", f"Failed in process {process_id}: {str(e)}")
            
            # Add to shared failure list
            shared_failed_tables = kwargs.get('shared_failed_tables')
            if shared_failed_tables is not None and hasattr(shared_failed_tables, 'append'):
                shared_failed_tables.append((table_name, f"Process error: {str(e)}", "Salesforce to Snowflake"))
        
        raise


def run_sync_in_subprocess_with_shared_tracking_inner(args, kwargs, table_name, process_id):
    """
    Inner function to run the actual migration with shared failure tracking.
    Unpacks kwargs correctly for process_salesforce_to_snowflake_mapping_with_shared_tracking.
    """
    import asyncio
    
    try:
        # Run the migration with asyncio - call the async function properly
        async def run_migration():
            # Ensure shared_failed_tables and other trackers stay as keyword arguments
            return await process_salesforce_to_snowflake_mapping_with_shared_tracking(
                *args, **kwargs
            )
        
        result = asyncio.run(run_migration())
        return result
    except Exception as e:
        # Add failure to shared list if available
        shared_failed_tables = kwargs.get('shared_failed_tables')
        if shared_failed_tables is not None and hasattr(shared_failed_tables, 'append'):
            shared_failed_tables.append((table_name, f"Migration error: {str(e)}\nTraceback: {tb_module.format_exc()}", "Salesforce to Snowflake"))
        raise

