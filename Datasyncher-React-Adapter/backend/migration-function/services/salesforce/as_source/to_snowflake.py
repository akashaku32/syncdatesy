"""Salesforce as source → Snowflake orchestrator."""

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
    BUCKET_NAME,
    BulkAPI2Client,
    DEMO_STORAGE,
    GCS_BUCKET_URL,
    MigrationFailureTracker,
    PROJECT_ID,
    build_salesforce_extract_where_clause,
    build_salesforce_oauth_context,
    check_field_exists,
    create_log_files,
    credentials,
    destination_connection_function,
    get_all_salesforce_fields,
    get_last_checked_dates_and_fields,
    is_unsupported_salesforce_object,
    log_error_to_response_file,
    logger,
    postgres_connection,
    process_salesforce_results_in_chunks,
    salesforce_connection,
    storage_client,
    update_last_synced_date,
    update_table_status_sf_to_snowflake,
    update_table_status_snowflake,
    update_table_status_timestamp_sf_to_snowflake,
)


async def process_salesforce_to_snowflake_mapping_with_shared_tracking(
    mapping, salesforce_details, snowflake_details,
    connection_id, job_name, migration_session_id, cmp_id,
    user_id, environment, chunk_count, logs_creation,
    connection_name, company_name, session, is_updated, is_deleted, 
    safety_interval_cutoff, 
    *,  # FORCE keyword-only for absolute positional safety
    is_final_attempt=True, 
    drop_and_reload=False, 
    shared_failed_tables=None,
    shared_row_counts=None,
    failure_tracker=None
):
    """
    Version of process_salesforce_to_snowflake_mapping that handles shared failure tracking for multiprocessing.
    """
    try:
        # Call the original function with both trackers for flexible support
        # Ensure ONLY 17 positional arguments are passed before *
        await process_salesforce_to_snowflake_mapping(
            mapping, salesforce_details, snowflake_details,
            connection_id, job_name, migration_session_id, cmp_id,
            user_id, environment, chunk_count, logs_creation,
            connection_name, company_name, session, is_updated, is_deleted, 
            safety_interval_cutoff, 
            failure_tracker=failure_tracker, 
            is_final_attempt=is_final_attempt, 
            drop_and_reload=drop_and_reload,
            shared_failed_tables=shared_failed_tables,
            shared_row_counts=shared_row_counts
        )
    except Exception as e:
        # Add to shared failure list instead of a local failure tracker if provided
        if shared_failed_tables is not None and hasattr(shared_failed_tables, 'append'):
            shared_failed_tables.append((mapping, f"Mapping error: {str(e)}\nTraceback: {tb_module.format_exc()}", "Salesforce to Snowflake"))
        elif failure_tracker:
            failure_tracker.add_failure(mapping, f"Mapping error: {str(e)}\nTraceback: {tb_module.format_exc()}", "Salesforce to Snowflake")
        # Re-raise the exception to maintain proper error handling
        raise


def run_sync_in_subprocess_with_salesforce_monitoring(args_and_kwargs, table_name, process_id):
    """
    Enhanced subprocess execution with Salesforce Bulk API job monitoring.
    """
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
    
    # Unpack tuple
    args, kwargs = args_and_kwargs
    
    # Extract Salesforce details for monitoring
    salesforce_details = args[1]  # salesforce_details is 2nd argument
    instance_url = salesforce_details.get("instance_url")
    access_token = salesforce_details.get("access_token")
    
    print(f"🔍 PROCESS {process_id}: Enhanced monitoring for table '{table_name}'")
    
    # Run the migration in a separate thread so we can monitor it
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Thread 1: Run the actual migration
        migration_future = executor.submit(run_sync_in_subprocess, args_and_kwargs)
        
        # Thread 2: Monitor Salesforce job status (if we can extract job IDs)
        monitoring_active = True
        
        def monitor_salesforce_jobs():
            """Monitor Salesforce bulk jobs and progress-based operations while migration runs."""
            nonlocal monitoring_active
            
            # Initialize Salesforce job monitor if we have the required details
            salesforce_monitor = None
            if instance_url and access_token:
                try:
                    from utils.salesforce_timeout_detector import SalesforceJobMonitor
                    salesforce_monitor = SalesforceJobMonitor(instance_url, access_token)
                    print(f"🔍 SALESFORCE MONITOR: Initialized for table '{table_name}'")
                except ImportError:
                    print(f"⚠️ Salesforce monitoring utilities not available - using basic monitoring")
                except Exception as e:
                    print(f"⚠️ Could not initialize Salesforce monitor: {e}")
            
            while monitoring_active:
                try:
                    # Check if migration is still running
                    if migration_future.done():
                        break
                    
                    # Try progress-based monitoring first (for Bulk API 1.0 fallback operations)
                    progress_status = None
                    try:
                        from utils.salesforce_progress_monitor import get_all_active_monitors
                        active_monitors = get_all_active_monitors()
                        
                        # Look for monitors associated with this table
                        for operation_id, monitor in active_monitors.items():
                            if monitor.table_name == table_name:
                                should_continue, reason, metadata = monitor.should_continue()
                                progress_info = monitor.update_progress(monitor.metrics.total_records)
                                
                                progress_status = (
                                    f"Progress: {progress_info['current_records']:,} records, "
                                    f"{progress_info['progress_percent']:.1f}%, "
                                    f"{progress_info['overall_rate']:.1f}/sec - {reason}"
                                )
                                
                                update_table_status_timestamp_sf_to_snowflake(
                                    args, table_name, "processing", 
                                    f"Process {process_id} - {progress_status}"
                                )
                                print(f"💓 PROGRESS: Table '{table_name}' - {progress_status}")
                                break
                    except ImportError:
                        pass  # Progress monitoring not available
                    except Exception as e:
                        print(f"⚠️ Progress monitoring check failed: {e}")
                    
                    # If no progress monitoring, try Salesforce job monitoring
                    if not progress_status and salesforce_monitor:
                        # Check if we have any registered jobs for this table
                        is_salesforce_active, status_message = salesforce_monitor.check_job_activity(table_name)
                        
                        if is_salesforce_active:
                            update_table_status_timestamp_sf_to_snowflake(
                                args, table_name, "processing", 
                                f"Process {process_id} active - Salesforce working: {status_message}"
                            )
                            print(f"🔄 SALESFORCE ACTIVE: Table '{table_name}' - {status_message}")
                        else:
                            update_table_status_timestamp_sf_to_snowflake(
                                args, table_name, "processing", 
                                f"Process {process_id} active - Salesforce status: {status_message}"
                            )
                            print(f"📊 SALESFORCE STATUS: Table '{table_name}' - {status_message}")
                    elif not progress_status:
                        # Fallback to basic monitoring (original behavior)
                        update_table_status_timestamp_sf_to_snowflake(
                            args, table_name, "processing", 
                            f"Process {process_id} active - monitoring Salesforce operations"
                        )
                    
                    time.sleep(60)  # Check every minute
                    
                except Exception as e:
                    print(f"⚠️ Monitoring error for table '{table_name}': {e}")
                    # Continue with basic monitoring even if enhanced monitoring fails
                    try:
                        update_table_status_timestamp_sf_to_snowflake(
                            args, table_name, "processing", 
                            f"Process {process_id} active - basic monitoring (enhanced failed)"
                        )
                    except:
                        pass
                    break
        
        # Start monitoring thread
        monitor_future = executor.submit(monitor_salesforce_jobs)
        
        try:
            # Wait for migration to complete with 2-hour timeout
            result = migration_future.result(timeout=7200)  # 2 hours
            monitoring_active = False
            
            print(f"✅ PROCESS {process_id}: Migration completed for table '{table_name}'")
            return result
            
        except FutureTimeoutError:
            monitoring_active = False
            print(f"⏰ PROCESS {process_id}: Migration timed out for table '{table_name}' after 2 hours")
            
            # Check if Salesforce is still actively working
            if instance_url and access_token:
                print(f"🔍 Checking if Salesforce is still processing table '{table_name}'...")
                try:
                    from utils.salesforce_timeout_detector import SalesforceJobMonitor
                    salesforce_monitor = SalesforceJobMonitor(instance_url, access_token)
                    
                    # Check if Salesforce is actively working on this table
                    is_salesforce_active, status_message = salesforce_monitor.check_job_activity(table_name)
                    
                    if is_salesforce_active:
                        print(f"🔄 SALESFORCE STILL ACTIVE: {status_message}")
                        print(f"⚠️ Process timed out but Salesforce is still working - consider extending timeout")
                    else:
                        print(f"🚨 SALESFORCE INACTIVE: {status_message}")
                        print(f"✅ Process timeout confirmed - Salesforce not actively processing")
                        
                except ImportError:
                    print(f"⚠️ Salesforce monitoring utilities not available")
                    print(f"🚨 Process timeout - unable to verify Salesforce job status")
                except Exception as e:
                    print(f"⚠️ Error checking Salesforce job status: {e}")
                    print(f"🚨 Process timeout - unable to verify Salesforce job status")
            else:
                print(f"🚨 Process timeout - no Salesforce connection details available")
            
            # Cancel the migration
            migration_future.cancel()
            raise TimeoutError(f"Migration process timed out after 2 hours for table '{table_name}'")
            
        except Exception as e:
            monitoring_active = False
            print(f"❌ PROCESS {process_id}: Migration failed for table '{table_name}': {e}")
            raise


async def process_with_batched_individual_sf_to_snowflake(parallel_args_list, max_concurrent, connection_id, job_name, migration_session_id, failure_tracker=None, shared_failed_tables=None):
    """
    Process Salesforce to Snowflake tables in batches using individual processes.
    """
    completed_tables = 0
    total_tables = len(parallel_args_list)

    print(f"🚀 BATCHED INDIVIDUAL PROCESSING:")
    print(f"   Total tables: {total_tables}")
    print(f"   Max concurrent processes: {max_concurrent}")
    print(f"   Estimated batches: {(total_tables + max_concurrent - 1) // max_concurrent}")

    # Process in batches to limit memory usage
    for batch_start in range(0, total_tables, max_concurrent):
        batch_end = min(batch_start + max_concurrent, total_tables)
        batch_args = parallel_args_list[batch_start:batch_end]
        batch_number = batch_start // max_concurrent + 1
        total_batches = (total_tables + max_concurrent - 1) // max_concurrent

        print(f"🔄 BATCH {batch_number}/{total_batches}: Processing tables {batch_start+1}-{batch_end}")

        processes = []
        process_table_map = {}
        batch_start_time = time.time()

        # Start batch processes
        for i, args_and_kwargs in enumerate(batch_args):
            # args_and_kwargs[0] is original args, args_and_kwargs[0][0] is table_name
            args, kwargs = args_and_kwargs
            table_name = args[0] if isinstance(args[0], str) else f"table_{batch_start + i}"

            process = multiprocessing.Process(
                target=run_sync_in_subprocess_with_shared_tracking,
                args=(args_and_kwargs, table_name)
            )
            processes.append(process)
            process.start()
            process_table_map[process.pid] = table_name

            print(f"🚀 Started process {process.pid} for table '{table_name}'")

        # Monitor batch completion with 2-hour timeout
        process_timeout = 7200  # 2 hours per table
        check_interval = 15     # Check every 15 seconds

        while any(p.is_alive() for p in processes):
            time.sleep(check_interval)

            current_time = time.time()
            batch_duration = current_time - batch_start_time

            # Check for stuck processes in this batch
            for process in processes:
                if process.is_alive():
                    table_name = process_table_map.get(process.pid, "unknown")

                    # Kill processes running too long
                    if batch_duration > process_timeout:
                        print(f"🚨 TIMEOUT: Killing stuck process {process.pid} (table '{table_name}') after {batch_duration:.1f}s (2 hours)")
                        try:
                            process.terminate()
                            process.join(timeout=10)
                            if process.is_alive():
                                print(f"🔪 FORCE KILL: Process {process.pid} (table '{table_name}')")
                                process.kill()
                            # Add to failure tracker for consolidated email
                            if failure_tracker:
                                failure_tracker.add_failure(
                                    table_name=table_name,
                                    error_message="Process timeout - killed after 2 hours",
                                    service_name="Salesforce to Snowflake"
                                )
                            # Update database status
                            update_table_status_sf_to_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
                        except Exception as e:
                            print(f"❌ Error killing process {process.pid}: {e}")

                    # Warning for long-running processes
                    elif batch_duration > process_timeout * 0.75:  # 75% of timeout (1.5 hours)
                        print(f"⚠️ WARNING: Process {process.pid} (table '{table_name}') running for {batch_duration:.1f}s")

        # Wait for all processes in batch to complete
        for process in processes:
            table_name = process_table_map.get(process.pid, "unknown")
            try:
                process.join(timeout=60)
                if process.is_alive():
                    print(f"🔪 Final kill for process {process.pid} (table '{table_name}')")
                    process.kill()
                elif process.exitcode != 0:
                    print(f"❌ Process {process.pid} (table '{table_name}') exited with code {process.exitcode}")
                    # Ensure failure is added to tracker/shared list if not already caught
                    already_logged = False
                    if shared_failed_tables is not None:
                        already_logged = any(item[0] == table_name for item in shared_failed_tables)
                    
                    if not already_logged:
                        error_msg = f"Process crashed with exit code {process.exitcode}"
                        if shared_failed_tables is not None:
                            shared_failed_tables.append((table_name, error_msg, "Salesforce to Snowflake"))
                        elif failure_tracker:
                            failure_tracker.add_failure(table_name, error_msg, "Salesforce to Snowflake")
                    
                    # Update database status and tool log if it crashed
                    update_table_status_sf_to_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
                    from utils.error_utils import insert_tool_log
                    insert_tool_log(
                        cmp_id=kwargs.get('cmp_id', 0), # Extract from kwargs if possible
                        connection_id=connection_id,
                        table_name=table_name,
                        status='failed',
                        message=error_msg,
                        job_name=job_name,
                        session=kwargs.get('session', '')
                    )
                else:
                    print(f"✅ Process {process.pid} (table '{table_name}') completed successfully")
            except Exception as e:
                print(f"❌ Error waiting for process {process.pid} (table '{table_name}'): {e}")
                # Failure already added to failure_tracker above

        completed_tables += len(batch_args)
        batch_duration = time.time() - batch_start_time
        print(f"✅ BATCH {batch_number} COMPLETED: {len(batch_args)} tables in {batch_duration:.1f}s")
        print(f"📊 PROGRESS: {completed_tables}/{total_tables} tables processed")

        # Brief pause between batches to allow memory cleanup
        if batch_end < total_tables:  # Not the last batch
            print("🔄 Pausing 3 seconds for memory cleanup...")
            time.sleep(3)

    # Get failed tables from trackers
    failed_tables_set = set()
    
    # 1. From local failure tracker
    if failure_tracker and failure_tracker.has_failures():
        for f in failure_tracker.get_failure_summary()["failed_tables"]:
            failed_tables_set.add(f["table_name"])
            
    # 2. From shared failed tables (multiprocessing)
    if shared_failed_tables is not None:
        for failure_info in shared_failed_tables:
            # failure_info is typically (table_name, error_message, service_name)
            if isinstance(failure_info, (list, tuple)) and len(failure_info) > 0:
                failed_tables_set.add(failure_info[0])

    failed_tables = list(failed_tables_set)


    success_count = total_tables - len(failed_tables)
    print(f"🎯 BATCHED PROCESSING COMPLETED:")
    print(f"   ✅ Successful tables: {success_count}")
    print(f"   ❌ Failed tables: {len(failed_tables)}")
    if failed_tables:
        print(f"   Failed table list: {failed_tables}")

    return success_count, failed_tables


async def sync_salesforce_to_snowflake(request: SalesforceSnowflakeMigration, environment: str):
    try:
        print("DEBUG: Starting Salesforce to Snowflake sync")

        # 🔓 Unpack request details
        salesforce_details = request.source_config
        snowflake_details = request.destination_config
        connection_id = request.connection_id
        mapping_list = request.selected_tables
        job_name = request.job_name
        migration_session_id = request.migration_session_id
        cmp_id = request.cmp_id
        user_id = request.user_id
        use_multiprocessing = request.execution_order
        celery_child_mode = bool(getattr(request, "celery_child_mode", False)) or bool(
            getattr(request, "celery_fanout_mode", False)
        )
        if celery_child_mode and use_multiprocessing == "parallel":
            use_multiprocessing = "sequential"
        chunk_count = request.chunk_count
        logs_creation = request.logs_creation
        connection_name = request.connection_name
        company_name = request.company_name
        session = request.session
        is_updated = request.is_updated
        is_deleted = request.is_deleted
        safety_interval = request.safety_interval

        # ⏱️ Compute safety interval cutoff
        try:
            interval_seconds = int(safety_interval or 0)
            interval_seconds = max(interval_seconds, 120)  # default to 2 mins
        except (TypeError, ValueError):
            interval_seconds = 120

        safety_interval_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)).replace(microsecond=0)

        # ✅ Extract retry attempt info from request (for email deduplication)
        is_final_attempt = getattr(request, 'is_final_attempt', True)  # Default to True for safety
        retry_attempt = getattr(request, 'retry_attempt', 0)
        max_retries = getattr(request, 'max_retries', 3)
        log_id = getattr(request, 'log_id', None)  # ✅ Extract log_id for stable updates
        
        print(f"📊 Retry Info: Attempt {retry_attempt + 1}/3, Final: {is_final_attempt}, Emails: {'ENABLED' if is_final_attempt else 'DISABLED'}")

        # Initialize failure tracker for consolidated error reporting
        failure_tracker = MigrationFailureTracker()

        # ✅ TEST CONNECTIONS BEFORE STARTING MIGRATION
        # This prevents wasting time if connections are invalid
        print("🔍 Testing connections before starting migration...")

        from services.salesforce.client import prepare_salesforce_credentials

        salesforce_details, sf_oauth_context = prepare_salesforce_credentials(
            connection_id, "source", salesforce_details
        )

        try:
            # Test Salesforce connection
            print(f"🔗 Testing Salesforce connection for '{connection_name}'...")
            sf_test = salesforce_connection(
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
            print("✅ Salesforce connection successful")
            
        except Exception as sf_error:
            # ✅ Extract error message properly (HTTPException has .detail attribute)
            from fastapi import HTTPException as FastAPIHTTPException
            
            if isinstance(sf_error, (FastAPIHTTPException, HTTPException)):
                # HTTPException - extract detail
                error_message = getattr(sf_error, 'detail', str(sf_error))
            else:
                # Regular exception - get string representation
                error_message = str(sf_error)
            
            # Add traceback for debugging
            error_message_with_trace = f"{error_message}\n\n--- Traceback ---\n{tb_module.format_exc()}"
            
            error_msg = f"❌ SALESFORCE CONNECTION FAILED: {error_message}"
            print(error_msg)
            
            # Mark all tables as failed first and add to failure tracker
            for table in mapping_list:
                try:
                    update_table_status_sf_to_snowflake(connection_id, table, "failed", job_name, migration_session_id)
                    # Add to failure tracker for accurate completion message
                    if failure_tracker:
                        failure_tracker.add_failure(table, error_msg, "Salesforce to Snowflake")
                except:
                    pass
            
            # ✅ CHECK: Only send email on final retry attempt
            if is_final_attempt:
                # Send immediate email notification for connection failure
                try:
                    from utils.email_templates import create_connection_failure_email
                    from utils.email_utils import EmailConfiguration, send_email_with_retry
                    
                    html_content, plain_text = create_connection_failure_email(
                        connection_type="Salesforce",
                        connection_name=connection_name,
                        error_message=error_message_with_trace,  # ✅ Include full traceback
                        context={
                            "job_name": job_name,
                            "company_name": company_name,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "cmp_id": cmp_id
                        }
                    )
                    
                    # Send email
                    email_config = EmailConfiguration(user_id=user_id, company_id=cmp_id)
                    if email_config.is_configured():
                        subject = f"🔴 Salesforce Connection Failed - {connection_name} - {company_name}"
                        send_email_with_retry(email_config, subject, html_content, plain_text)
                        print("📧 Salesforce connection failure email sent (final attempt)")
                    else:
                        print("⚠️ Email not configured - connection failure notification not sent")
                        
                except Exception as email_error:
                    print(f"⚠️ Failed to send Salesforce connection failure email: {email_error}")
            else:
                print(f"⏭️ SKIPPING Salesforce connection failure email (attempt {retry_attempt + 1}/3 - not final)")
            
            # Raise exception to stop migration (will be caught by outer handler)
            # ✅ CRITICAL FIX: Re-raise original HTTPException to preserve .detail for classifier.
            # Previously: detail=f"Salesforce connection failed: {str(sf_error)}"
            # str(HTTPException) returns EMPTY → classifier saw "Salesforce connection failed: " → CONNECTION
            if isinstance(sf_error, HTTPException):
                raise sf_error
            raise HTTPException(
                status_code=500,
                detail=f"Salesforce connection failed: {str(sf_error) or repr(sf_error)}"
            )
        
        try:
            # Test Snowflake connection
            print(f"🔗 Testing Snowflake connection...")
            
            # Validate Snowflake credentials before attempting connection
            snowflake_password = snowflake_details.get("password")
            snowflake_private_key = snowflake_details.get("private_key")
            
            if not snowflake_password and not snowflake_private_key:
                raise Exception("Snowflake authentication failed: Password is empty and no private key configured. Please provide either password or private key for authentication.")
            
            snowflake_test = destination_connection_function(snowflake_details)
            print("✅ Snowflake connection successful")
            
        except Exception as sf_error:
            # ✅ Extract error message properly (HTTPException has .detail attribute)
            from fastapi import HTTPException as FastAPIHTTPException
            
            if isinstance(sf_error, (FastAPIHTTPException, HTTPException)):
                # HTTPException - extract detail
                error_message = getattr(sf_error, 'detail', str(sf_error))
            else:
                # Regular exception - get string representation
                error_message = str(sf_error)
            
            # Add traceback for debugging
            error_message_with_trace = f"{error_message}\n\n--- Traceback ---\n{tb_module.format_exc()}"
            
            error_msg = f"❌ SNOWFLAKE CONNECTION FAILED: {error_message}"
            print(error_msg)
            
            # Mark all tables as failed first and add to failure tracker
            for table in mapping_list:
                try:
                    update_table_status_sf_to_snowflake(connection_id, table, "failed", job_name, migration_session_id)
                    # Add to failure tracker for accurate completion message
                    if failure_tracker:
                        failure_tracker.add_failure(table, error_msg, "Salesforce to Snowflake")
                except:
                    pass
            
            # ✅ CHECK: Only send email on final retry attempt
            if is_final_attempt:
                # Send immediate email notification for connection failure
                try:
                    from utils.email_templates import create_connection_failure_email
                    from utils.email_utils import EmailConfiguration, send_email_with_retry
                    
                    html_content, plain_text = create_connection_failure_email(
                        connection_type="Snowflake",
                        connection_name=connection_name,
                        error_message=error_message_with_trace,  # ✅ Include full traceback
                        context={
                            "job_name": job_name,
                            "company_name": company_name,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "cmp_id": cmp_id
                        }
                    )
                    
                    # Send email
                    email_config = EmailConfiguration(user_id=user_id, company_id=cmp_id)
                    if email_config.is_configured():
                        subject = f"🔴 Snowflake Connection Failed - {connection_name} - {company_name}"
                        send_email_with_retry(email_config, subject, html_content, plain_text)
                        print("📧 Snowflake connection failure email sent (final attempt)")
                    else:
                        print("⚠️ Email not configured - connection failure notification not sent")
                        
                except Exception as email_error:
                    print(f"⚠️ Failed to send Snowflake connection failure email: {email_error}")
            else:
                print(f"⏭️ SKIPPING Snowflake connection failure email (attempt {retry_attempt + 1}/3 - not final)")
            
            # Raise exception to stop migration (will be caught by outer handler)
            # ✅ CRITICAL FIX: Re-raise original HTTPException to preserve .detail for classifier.
            if isinstance(sf_error, HTTPException):
                raise sf_error
            raise HTTPException(
                status_code=500,
                detail=f"Snowflake connection failed: {str(sf_error) or repr(sf_error)}"
            )
        
        print("✅ Both connections validated - starting migration...")

        start_time = time.time()
        
        # Extract drop_and_reload flag
        drop_and_reload = request.drop_and_reload
        
        # DEBUG: Log the drop_and_reload flag
        print("=" * 80)
        print(f"🔍 DEBUG SALESFORCE: drop_and_reload flag received: {drop_and_reload}")
        print(f"🔍 DEBUG SALESFORCE: Type: {type(drop_and_reload)}")
        print(f"🔍 DEBUG SALESFORCE: Selected tables: {mapping_list}")
        print("=" * 80)
        
        # ═══════════════════════════════════════════════════════════════
        # DROP TABLES IF drop_and_reload FLAG IS TRUE
        # This ensures a clean slate before reloading data from Salesforce
        # ═══════════════════════════════════════════════════════════════
        if drop_and_reload:
            print("=" * 80)
            print("🗑️  DROP AND RELOAD MODE ENABLED (SALESFORCE)")
            print(f"   Dropping {len(mapping_list)} table(s) before reload...")
            print("=" * 80)
            
            # Import the drop utility function
            from utils.table_utils import drop_snowflake_table
            
            # Create Snowflake engine for drop operations
            try:
                # Extract Snowflake connection parameters
                account = snowflake_details.get('account', '')
                user = snowflake_details.get('user') or snowflake_details.get('username', '')
                password = snowflake_details.get('password', '')
                database = snowflake_details.get('database', '')
                schema = snowflake_details.get('schema', 'PUBLIC').upper()
                warehouse = snowflake_details.get('warehouse', '')
                role = snowflake_details.get('role', '')
                private_key_str = snowflake_details.get('private_key', '')
                passphrase = snowflake_details.get('passphrase', '')
                
                # ✅ FIX: Check authentication_type first to determine which auth method to use
                authentication_type = snowflake_details.get('authentication_type', 'password').lower()
                
                # Import required modules
                from urllib.parse import quote
                from sqlalchemy import create_engine, text
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.backends import default_backend
                
                print(f"🔗 Creating Snowflake engine for drop operations...")
                print(f"   Account: {account}")
                print(f"   Database: {database}")
                print(f"   Schema: {schema}")
                print(f"   Warehouse: {warehouse}")
                print(f"   Authentication type: {authentication_type}")
                
                # ✅ FIX: Check authentication_type first, not just if private_key exists
                if authentication_type == "key_pair" and private_key_str:
                    # --- Key pair authentication ---
                    print("   Authentication: Key pair")
                    
                    # Normalize and load private key
                    normalized_private_key = normalize_pem_key(private_key_str)
                    private_key_bytes = normalized_private_key.encode()
                    
                    # Use passphrase if provided for encrypted keys
                    passphrase_bytes = None
                    if passphrase and passphrase.strip():
                        passphrase_bytes = passphrase.encode("utf-8")
                        print(f"   Using passphrase for encrypted private key")
                    else:
                        print("   No passphrase provided, attempting to load unencrypted key")
                    
                    private_key = serialization.load_pem_private_key(
                        private_key_bytes,
                        password=passphrase_bytes,
                        backend=default_backend()
                    )
                    
                    # Build SQLAlchemy engine with private key
                    connection_string_sf = f"snowflake://{user}@{account}/{database}/{schema}?warehouse={warehouse}"
                    if role:
                        connection_string_sf += f"&role={role}"
                    
                    destination_engine = create_engine(
                        connection_string_sf,
                        connect_args={"private_key": private_key}
                    )
                    
                elif authentication_type == "password" and password:
                    # --- Password authentication ---
                    print("   Authentication: Password")
                    
                    snowflake_username_quoted = quote(user)
                    snowflake_password_quoted = quote(password)
                    
                    connection_string_sf = (
                        f"snowflake://{snowflake_username_quoted}:{snowflake_password_quoted}@"
                        f"{account}/"
                        f"{database}?warehouse={warehouse}"
                    )
                    if role:
                        connection_string_sf += f"&role={role}"
                    
                    destination_engine = create_engine(connection_string_sf)
                    
                else:
                    # Provide detailed error messages based on authentication_type
                    if authentication_type == "key_pair" and not private_key_str:
                        raise ValueError("Private key is required for key-pair authentication.")
                    elif authentication_type == "password" and not password:
                        raise ValueError("Password is required for password authentication.")
                    else:
                        raise ValueError(f"Either password or private_key must be provided for authentication. Authentication type: {authentication_type}")
                
                # Set role and schema if specified
                with destination_engine.connect() as conn:
                    if role:
                        conn.execute(text(f"USE ROLE {role}"))
                        print(f"✅ Role set to: {role}")
                    conn.execute(text(f"USE SCHEMA {schema}"))
                    print(f"✅ Schema set to: {schema}")
                
                # Drop each selected table
                for table_name in mapping_list:
                    print(f"\n🗑️  Dropping table: {table_name}")
                    drop_result = drop_snowflake_table(
                        destination_engine=destination_engine,
                        table_name=table_name,
                        schema=schema,
                        connection_id=connection_id,
                        cmp_id=cmp_id
                    )
                    
                    if drop_result['success']:
                        print(f"✅ {drop_result['message']}")
                    else:
                        print(f"⚠️  {drop_result['message']}")
                
                # Dispose engine after drop operations
                destination_engine.dispose()
                print(f"\n✅ All tables dropped successfully")
                print("=" * 80)
                
            except Exception as e:
                error_msg = f"Error during drop phase: {str(e)}"
                print(f"❌ {error_msg}")

                print(f"   Traceback: {tb_module.format_exc()}")
                insert_error_log(cmp_id, connection_id, error_msg, "salesforce_drop_tables")
                # Continue with migration even if drop fails (table might not exist)
                print("⚠️  Continuing with migration despite drop errors...")
                print("=" * 80)

        # 🧵 Prepare args list
        # Note: Tracker arguments (failure_tracker, shared_failed_tables) 
        # will be passed as keyword arguments later
        args_list = []
        for mapping in mapping_list:
            # Create a clean 17-element tuple for positional arguments
            # This must match process_salesforce_to_snowflake_mapping signature exactly
            args_tuple = (
                mapping, salesforce_details, snowflake_details,
                connection_id, job_name, migration_session_id, cmp_id,
                user_id, environment, chunk_count, logs_creation,
                connection_name, company_name, session, is_updated,
                is_deleted, safety_interval_cutoff
            )
            # Ensure it is exactly 17 elements
            if len(args_tuple) != 17:
                print(f"⚠️ INTERNAL ERROR: args_tuple has {len(args_tuple)} elements instead of 17")
            args_list.append(args_tuple)

        # 🚀 Execute with enhanced batched individual process approach
        if use_multiprocessing == "parallel":
            print("🚀 ENHANCED PARALLEL EXECUTION: Using batched individual processes with 2-hour timeouts")

            # Get system resources
            try:
                import psutil
                available_memory_gb = psutil.virtual_memory().available / (1024**3)
                cpu_count = os.cpu_count()
            except:
                # Fallback if psutil not available
                available_memory_gb = 4.0  # Assume 4GB available
                cpu_count = 4

            # Determine optimal strategy
            strategy, max_concurrent = get_optimal_process_strategy(
                table_count=len(args_list),
                available_memory_gb=available_memory_gb,
                cpu_count=cpu_count
            )

            # Create a manager to share data between processes
            manager = multiprocessing.Manager()
            shared_failed_tables = manager.list()
            shared_row_counts = manager.list()

            # Create args list with shared_failed_tables (manager.list)
            parallel_args_list = []
            for args in args_list:
                # Build kwarg dict for trackers and flags
                kwargs = {
                    'failure_tracker': None, # We use shared_failed_tables in parallel
                    'is_final_attempt': is_final_attempt,
                    'drop_and_reload': drop_and_reload,
                    'shared_failed_tables': shared_failed_tables,
                    'shared_row_counts': shared_row_counts
                }
                parallel_args_list.append((args, kwargs))

            if strategy == "individual":
                print("🔄 Using individual processes for all tables")
                processes = []
                process_table_map = {}

                for i, args_and_kwargs in enumerate(parallel_args_list):
                    # args_and_kwargs[0] is original args, args_and_kwargs[0][0] is table_name
                    args, kwargs = args_and_kwargs
                    table_name = args[0] if isinstance(args[0], str) else f"table_{i}"

                    # Use the wrapper function that handles shared failure tracking
                    process = multiprocessing.Process(
                        target=run_sync_in_subprocess_with_shared_tracking,
                        args=(args_and_kwargs, table_name)
                    )
                    processes.append(process)
                    process.start()
                    process_table_map[process.pid] = table_name
                    print(f"🚀 Started process {process.pid} for table '{table_name}'")

                # Wait for all processes with 2-hour timeout and collect failures
                for process in processes:
                    table_name = process_table_map.get(process.pid, "unknown")
                    try:
                        process.join(timeout=7200)  # 2 hour timeout
                        if process.is_alive():
                            print(f"🚨 KILLING STUCK PROCESS: {process.pid} (table '{table_name}') after 2 hours")
                            process.terminate()
                            process.join(timeout=10)
                            if process.is_alive():
                                print(f"🔪 FORCE KILL: Process {process.pid} (table '{table_name}')")
                                process.kill()

                            # Add to failure tracker for consolidated email
                            failure_tracker.add_failure(
                                table_name=table_name,
                                error_message="Process timeout - killed after 2 hours",
                                service_name="Salesforce to Snowflake"
                            )
                            update_table_status_sf_to_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)

                        elif process.exitcode != 0:
                            print(f"❌ Process {process.pid} (table '{table_name}') exited with code {process.exitcode}")
                            # Check if already added to shared_failed_tables via wrapper (crashes typically aren't)
                            already_logged = False
                            if shared_failed_tables is not None:
                                already_logged = any(item[0] == table_name for item in shared_failed_tables)
                            
                            if not already_logged:
                                error_msg = f"Process crashed with exit code {process.exitcode}"
                                if shared_failed_tables is not None:
                                    shared_failed_tables.append((table_name, error_msg, "Salesforce to Snowflake"))
                                elif failure_tracker:
                                    failure_tracker.add_failure(table_name, error_msg, "Salesforce to Snowflake")
                            
                            # Update database status and tool log if it crashed
                            update_table_status_sf_to_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
                            from utils.error_utils import insert_tool_log
                            insert_tool_log(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                table_name=table_name,
                                status='failed',
                                message=error_msg,
                                job_name=job_name,
                                session=session
                            )
                        else:
                            # print(f"✅ Process {process.pid} (table '{table_name}') completed successfully")
                            pass
                    except Exception as e:
                        print(f"❌ Error with process {process.pid} (table '{table_name}'): {e}")
                        # Add to failure tracker for consolidated email
                        failure_tracker.add_failure(
                            table_name=table_name,
                            error_message=f"Process error: {str(e)}",
                            service_name="Salesforce to Snowflake"
                        )


                # Update the main failure tracker with failures from parallel processes
                # The SharedFailureTracker keeps its own failed_tables list for compatibility
                print(f"🔍 Consolidating failures from shared_failed_tables:")
                print(f"   shared_failed_tables (manager.list) length: {len(shared_failed_tables)}")
                print(f"   shared_failed_tables contents: {list(shared_failed_tables)}")
                print(f"   failure_tracker.failed_tables BEFORE: {failure_tracker.failed_tables}")
                
                for failure_info in shared_failed_tables:
                    table_name, error_message, service_name = failure_info
                    failure_tracker.add_failure(table_name, error_message, service_name)
                    print(f"   ➕ Added failure: {table_name}")
                
                print(f"   failure_tracker.failed_tables AFTER: {failure_tracker.failed_tables}")


            else:  # batched_individual
                print(f"🔄 Using batched individual processes (max_concurrent: {max_concurrent})")
                success_count, failed_tables = await process_with_batched_individual_sf_to_snowflake(
                    parallel_args_list, max_concurrent, connection_id, job_name, migration_session_id, failure_tracker, shared_failed_tables
                )

                print(f"📊 BATCHED PROCESSING RESULTS:")
                print(f"   ✅ Successful: {success_count}/{len(parallel_args_list)}")
                print(f"   ❌ Failed: {len(failed_tables)}/{len(parallel_args_list)}")

            # --- CONSOLIDATION PHASE ---
            # Update the main failure tracker with failures from parallel processes
            print(f"🔍 Consolidating failures from shared_failed_tables:")
            print(f"   shared_failed_tables length: {len(shared_failed_tables)}")
            
            # Use both shared_failed_tables and any locally tracked failures in batched caller
            for failure_info in shared_failed_tables:
                table_name, error_message, service_name = failure_info
                # Avoid duplicates in failure_tracker.failed_tables
                already_in_tracker = any(f["table_name"] == table_name for f in failure_tracker.failed_tables)
                if not already_in_tracker:
                    failure_tracker.add_failure(table_name, error_message, service_name)
                    print(f"   ➕ Added failure from shared list: {table_name}")
                else:
                    print(f"   ⏭️ Skipping duplicate failure: {table_name}")

        else:
            print("🔄 Sequential execution")
            shared_row_counts = []  # Use a regular list for sequential execution
            for args in args_list:
                # Use keyword arguments for optional trackers and flags
                await process_salesforce_to_snowflake_mapping(
                    *args, 
                    failure_tracker=failure_tracker, 
                    is_final_attempt=is_final_attempt,
                    drop_and_reload=drop_and_reload,
                    shared_failed_tables=None,
                    shared_row_counts=shared_row_counts
                )

        # Add overall migration completion message and log (after all tables are processed)
        execution_time = time.time() - start_time
        from utils.error_utils import log_migration_completion
        # Determine operation type for generic messages (single table operations)
        operation_type = None
        if drop_and_reload and len(mapping_list) == 1:
            operation_type = 'drop_and_reload'
        elif len(mapping_list) == 1 and not drop_and_reload:
            operation_type = 'single_table_refresh'
        
        # Calculate table statistics
        total_tables = len(mapping_list)
        failed_count = len(failure_tracker.failed_tables) if failure_tracker else 0
        successful_count = total_tables - failed_count
        
        # Aggregate row counts from all subprocesses
        total_rows_migrated = sum(shared_row_counts) if shared_row_counts else 0
        
        print(f"📊 Migration Statistics:")
        print(f"   Total tables: {total_tables}")
        print(f"   Total rows migrated: {total_rows_migrated}")
        print(f"   Failed count from tracker: {failed_count}")
        print(f"   Failed tables list: {failure_tracker.failed_tables if failure_tracker else 'No tracker'}")
        print(f"   Successful count: {successful_count}")
        
        # In fan-out child mode, skip per-child job-level log. The Celery
        # finalizer aggregates totals from all children and writes one entry.
        if not celery_child_mode:
            log_migration_completion(
                execution_time=execution_time,
                total_rows=total_rows_migrated,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                session=session,
                total_tables=total_tables,
                successful_tables=successful_count,
                failed_tables=failed_count,
                job_name=job_name,
                migration_record_id=None,  # High-level log
                operation_type=operation_type,
                log_id=log_id  # ✅ Pass log_id for stable updates
            )
        else:
            print(
                f"🧩 CHILD_MODE: skipping per-child completion log for job={job_name}, "
                f"tables={successful_count}/{total_tables} successful, rows={total_rows_migrated}"
            )
        
        
        if not celery_child_mode:
            print(f"⏱️ Starting async cleanup at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")
            try:
                cleanup_result = handle_migration_completion(
                    connection_name=connection_name or f"SALESFORCE_SNOWFLAKE_{connection_id}",
                    job_name=job_name,
                    completion_message=None
                )
                print(f"⏱️ Async cleanup completed at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")

                if cleanup_result['status'] == 'success' and cleanup_result['cleaned_jobs']:
                    print(f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} stuck celery jobs for Salesforce to Snowflake migration")
                elif cleanup_result['status'] == 'error':
                    print(f"⚠️ Cleanup warning for Salesforce to Snowflake: {cleanup_result['message']}")

            except Exception as cleanup_error:
                print(f"⚠️ Failed to cleanup celery jobs: {cleanup_error}")
                tb_module.print_exc()

        # Send consolidated error email if there were any failures
        failure_tracker.send_consolidated_error_email(
            connection_name=connection_name,
            service_name="Salesforce to Snowflake",
            user_id=user_id,
            cmp_id=cmp_id,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            company_name=company_name,
            is_final_attempt=is_final_attempt  # ✅ Only send on final attempt
        )

        _sf_failed_list = list(getattr(failure_tracker, "failed_tables", []) or []) if failure_tracker else []
        return {
            "message": "✅ Data synced successfully from Salesforce to Snowflake",
            "total_rows": total_rows_migrated,
            "execution_time": execution_time,
            "total_tables": total_tables,
            "successful_tables_count": successful_count,
            "failed_tables_count": failed_count,
            "successful_tables_list": [],  # Salesforce service doesn't track successful list explicitly
            "failed_tables_list": _sf_failed_list,
        }

    except Exception as e:
        print(f"❌ ERROR: {e}")
        tb_module.print_exc()

        # ✅ Check if this is HTTPException (connection failure already handled with email)
        from fastapi import HTTPException as FastAPIHTTPException
        is_http_exception = isinstance(e, (FastAPIHTTPException, HTTPException))
        
        if is_http_exception:
            print("⚠️ HTTPException detected - connection failure email already sent, skipping duplicate")

        # Log overall migration error using centralized error handling
        from utils.error_utils import handle_service_error

        error_context = {
            "table_name": "Migration Process",
            "service_name": "Salesforce to Snowflake",
            "error_type": "MIGRATION_FAILED",
            "is_terminal_failure": True,  # ✅ Mark as terminal failure
            "log_id": log_id,  # ✅ Pass log_id for stable updates
            "cmp_id": cmp_id,
            "connection_id": connection_id,
            "user_id": user_id,
            "job_name": job_name,
            "migration_session_id": migration_session_id,
            "company_name": company_name,
            "connection_name": connection_name,
            "session": session,
            "total_tables": len(mapping_list) if 'mapping_list' in locals() else 0,
            "execution_time": time.time() - start_time if 'start_time' in locals() else 0.0,
            "total_rows": total_rows_migrated if 'total_rows_migrated' in locals() else 0,
            "successful_tables": 0
        }

        # ✅ Log to tool_log but DON'T send duplicate email for HTTPException (connection failures)
        error_result = handle_service_error(
            error=e,
            context=error_context,
            log_files=None,  # No file logging for overall migration errors
            send_email=not is_http_exception,  # ✅ Skip email for HTTPException (already sent)
            is_final_attempt=is_final_attempt
        )

        if error_result["tool_log_logged"]:
            print(f"✅ Migration error logged to tool_log")
        if error_result["email_sent"]:
            print(f"📧 Migration error notification email sent")
        elif not error_result["email_sent"] and not is_http_exception:
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


def ensure_snowflake_table_exists(engine, table_name: str, fields: List[str], describe_meta: List[dict],
                                   schema_name: str = None, primary_key: str = None,is_deleted:str = None,is_updated:str = None):
    

    SALESFORCE_TO_SNOWFLAKE_TYPE = {
        "id": "VARCHAR", "string": "VARCHAR", "textarea": "VARCHAR", "longtextarea": "VARCHAR",
        "richtextarea": "VARCHAR", "email": "VARCHAR", "url": "VARCHAR", "phone": "VARCHAR",
        "combopicklist": "VARCHAR", "picklist": "VARCHAR", "multipicklist": "VARCHAR", "reference": "VARCHAR",
        "boolean": "BOOLEAN", "int": "NUMBER", "integer": "NUMBER", "double": "FLOAT", "currency": "NUMBER",
        "percent": "FLOAT", "date": "DATE", "datetime": "TIMESTAMP_TZ", "time": "TIME", "base64": "VARCHAR",
        "encryptedstring": "VARCHAR", "location": "VARCHAR", "address": "VARIANT", "complexvalue": "VARIANT",
        "anytype": "VARCHAR", "junctionidlist": "ARRAY"
    }

    normalized_fields = [normalize_name(f) for f in fields]
    normalized_table_name = normalize_name(table_name)
    normalized_primary_key = normalize_name(primary_key) if primary_key else None

    field_metadata_map = {
        normalize_name(f["name"]): {
            "type": f["type"].lower(),
            "length": f.get("length"),
            "precision": f.get("precision"),
            "scale": f.get("scale")
        } for f in describe_meta if normalize_name(f["name"]) in normalized_fields
    }

    try:
        with engine.begin() as con:
            if schema_name:
                schema_exists = con.execute(
                    text("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = :s"),
                    {"s": schema_name}
                ).scalar() > 0
                if not schema_exists:
                    con.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
                    logger.info(f"✅ Created Snowflake schema: {schema_name}")
                full_table_name = f'"{schema_name}"."{normalized_table_name}"'
                exists = con.execute(
                    text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = :s AND table_name = :t"),
                    {"s": schema_name, "t": normalized_table_name}
                ).scalar() > 0
            else:
                full_table_name = f'"{normalized_table_name}"'
                exists = con.execute(
                    text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = :t"),
                    {"t": normalized_table_name}
                ).scalar() > 0

            if not exists:
                col_defs = []
                ordered_fields = [normalized_primary_key] + [f for f in normalized_fields if f != normalized_primary_key] if normalized_primary_key else normalized_fields
                for f in ordered_fields:
                    meta = field_metadata_map.get(f, {})
                    sf_type = SALESFORCE_TO_SNOWFLAKE_TYPE.get(meta.get("type", "string"), "VARCHAR")
                    if sf_type == "VARCHAR" and meta.get("length"):
                        col = f'"{f}" {sf_type}({meta["length"]})'
                    elif sf_type == "NUMBER":
                        # if meta.get("precision") and meta.get("scale") is not None:
                        #     col = f'"{f}" {sf_type}({meta["precision"]},{meta["scale"]})'
                        # elif meta.get("precision"):
                        #     col = f'"{f}" {sf_type}({meta["precision"]})'
                        # else:
                        #     col = f'"{f}" {sf_type}'
                        col = f'"{f}" {sf_type}(35,17)'

                    else:
                        col = f'"{f}" {sf_type}'
                    if normalized_primary_key and f == normalized_primary_key:
                        col += " PRIMARY KEY"
                    col_defs.append(col)
                if is_updated not in ordered_fields:
                    col_defs.append(f'"{is_updated}" TIMESTAMP_TZ')
                if is_deleted not in ordered_fields:
                    col_defs.append(f'"{is_deleted}" BOOLEAN')



                con.execute(text(f'CREATE TABLE IF NOT EXISTS {full_table_name} ({", ".join(col_defs)})'))
                logger.info(f"✅ Created Snowflake table: {full_table_name}")
            else:
                existing_cols = con.execute(
                    text("""
                        SELECT column_name, data_type, character_maximum_length 
                        FROM information_schema.columns 
                        WHERE table_name = :t""" + (" AND table_schema = :s" if schema_name else "")
                    ),
                    {"t": normalized_table_name, **({"s": schema_name} if schema_name else {})}
                ).fetchall()

                col_map = {row[0].upper(): {"type": row[1].upper(), "length": row[2]} for row in existing_cols}

                for f in normalized_fields:
                    meta = field_metadata_map.get(f, {})
                    sf_type = SALESFORCE_TO_SNOWFLAKE_TYPE.get(meta.get("type", "string"), "VARCHAR")
                    existing = col_map.get(f.upper())

                    if not existing:
                        if sf_type == "VARCHAR" and meta.get("length"):
                            col_def = f'"{f}" {sf_type}({meta["length"]})'
                        elif sf_type == "NUMBER":
                            if meta.get("precision") and meta.get("scale") is not None:
                                col_def = f'"{f}" {sf_type}({meta["precision"]},{meta["scale"]})'
                            elif meta.get("precision"):
                                col_def = f'"{f}" {sf_type}({meta["precision"]})'
                            else:
                                col_def = f'"{f}" {sf_type}'
                        else:
                            col_def = f'"{f}" {sf_type}'

                        con.execute(text(f'ALTER TABLE {full_table_name} ADD COLUMN IF NOT EXISTS {col_def}'))
                        logger.info(f"➕ Added column: {col_def}")
                    elif sf_type == "VARCHAR" and meta.get("length") and existing["type"] == "VARCHAR":
                        current_len = existing.get("length") or 0
                        new_len = meta.get("length")
                        if new_len > current_len:
                            con.execute(text(
                                f'ALTER TABLE {full_table_name} ALTER COLUMN "{f}" SET DATA TYPE VARCHAR({new_len})'
                            ))
                            logger.info(f"🔁 Increased column '{f}' length from {current_len} to {new_len}")
                        elif new_len < current_len:
                            logger.warning(f"⚠️ Skipping shrink of column '{f}' from {current_len} to {new_len}")
            
        return exists

    except Exception as e:
        logger.error(f"❌ Error ensuring table exists: {str(e)}", exc_info=True)
        raise


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


def create_staging_table(engine, source_table: str, staging_table: str):
    """Create a staging table with the same structure as the source table"""
    norm_source = normalize_name(source_table)
    norm_staging = normalize_name(staging_table)
    with engine.connect() as con:
        con.execute(text(f'CREATE TABLE IF NOT EXISTS "{norm_staging}" LIKE "{norm_source}"'))
        logger.info(f"✅ Created staging table: {norm_staging}")


def truncate_staging_table(engine, staging_table: str):
    """Truncate the staging table"""
    norm_staging = normalize_name(staging_table)
    with engine.connect() as con:
        con.execute(text(f'TRUNCATE TABLE "{norm_staging}"'))
        logger.info(f"✅ Truncated staging table: {norm_staging}")


def escape_backslashes(value):
    """
    Escapes single backslashes in string values by replacing them with double backslashes.
    """
    if isinstance(value, str):  # Only process strings
        return re.sub(r'\\', r'\\\\', value)
    return value  # Return non-string values unchanged


def clean_nan_recursive(obj):
    if isinstance(obj, float) and np.isnan(obj):
        return None
    elif isinstance(obj, dict):
        return {k: clean_nan_recursive(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nan_recursive(v) for v in obj]
    return obj


def parse_variant_fields(row: Dict[str, Any], variant_fields: Set[str]) -> Dict[str, Any]:
    for field in variant_fields:
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return row


def ensure_gcs_storage_integration(engine, integration_name, bucket_url,connection_id,cmp_id):
    
    try:
        with engine.connect() as connection:
            # Check if the integration already exists
            check_integration_sql = f"""
            SHOW STORAGE INTEGRATIONS LIKE '{integration_name}';
            """
            result = connection.execute(text(check_integration_sql)).fetchall()

            if result:
                print(f"Storage integration '{integration_name}' already exists. Skipping creation.")
                return result
            else:
                # If the integration does not exist, create it
                print(f"Creating storage integration '{integration_name}'...")
                create_integration_sql = f"""
                CREATE STORAGE INTEGRATION {integration_name}
                TYPE = EXTERNAL_STAGE
                STORAGE_PROVIDER = 'GCS'
                ENABLED = TRUE
                STORAGE_ALLOWED_LOCATIONS = ('{bucket_url}');
                """
                connection.execute(text(create_integration_sql))
                print(f"Storage integration '{integration_name}' created successfully.")
                return None
    except Exception as e:
        print(f"Error creating storage integration: {str(e)}")
        message=f"Error creating storage integration: {str(e)}"
        insert_error_log(cmp_id, connection_id,message, "ensure_gcs_storage_integration")
        raise


def get_gcp_service_account(engine, integration_name,cmp_id,connection_id):
    """
    Fetches the STORAGE_GCP_SERVICE_ACCOUNT for a given storage integration from Snowflake.

    Args:
        engine: SQLAlchemy engine connected to Snowflake.
        integration_name (str): Name of the storage integration.

    Returns:
        str: The GCP service account email associated with the storage integration.
    """
    try:
        with engine.connect() as connection:
            query = f"DESC INTEGRATION {integration_name}"
            result = connection.execute(text(query))
            
            for row in result:
                c_property, c_type, c_value, c_default = row
                if c_property == "STORAGE_GCP_SERVICE_ACCOUNT":
                    print(f"Fetched GCP Service Account: {c_value}")
                    return c_value
            
            error_message = f"STORAGE_GCP_SERVICE_ACCOUNT not found for integration '{integration_name}'."
            print(error_message)
            raise ValueError(error_message)

    except Exception as e:
        error_message = f"Error fetching GCP Service Account for integration '{integration_name}': {str(e)}"
        insert_error_log(cmp_id, connection_id,error_message, "get_gcp_service_account")
        print(error_message)
        raise RuntimeError(error_message)


def ensure_service_account_and_permissions(engine, integration_name, bucket_name, project_id,cmp_id, connection_id):
    """
    Ensures the STORAGE_GCP_SERVICE_ACCOUNT is added as a principal to the GCS bucket's IAM policy
    with the 'roles/storage.objectAdmin' role.

    Args:
        engine: SQLAlchemy engine connected to Snowflake.
        integration_name (str): Name of the storage integration in Snowflake.
        bucket_name (str): Name of the GCS bucket.
        project_id (str): GCP Project ID.

    Returns:
        None
    """
    try:
        # Step 1: Fetch the service account from Snowflake
        service_account_email = get_gcp_service_account(engine, integration_name,cmp_id,connection_id)
        print(f"Service account fetched: {service_account_email}")
        member = f"serviceAccount:{service_account_email}"
        role = "roles/storage.objectAdmin"

        # Step 2: Authenticate and initialize the GCS client
        #storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)

        # Step 3: Fetch the current IAM policy
        policy = bucket.get_iam_policy(requested_policy_version=3)

        # Step 4: Check if the service account already has the role
        role_found = False
        for binding in policy.bindings:
            if binding["role"] == role:
                if member in binding["members"]:
                    print(f"'{member}' already has role '{role}' on bucket '{bucket_name}'.")
                    role_found = True
                else:
                    print(f"Adding '{member}' to role '{role}' on bucket '{bucket_name}'.")
                    binding["members"].add(member)
                break

        # Step 5: Add the role if not found
        if not role_found:
            print(f"Adding role '{role}' for '{member}'.")
            policy.bindings.append({
                "role": role,
                "members": {member},
            })

        # Step 6: Update the IAM policy
        bucket.set_iam_policy(policy)
        print(f"Role '{role}' successfully granted to '{member}' on bucket '{bucket_name}'.")

    except Exception as e:
        error_message = f"Error ensuring permissions for '{integration_name}' on bucket '{bucket_name}': {str(e)}"
        print(error_message)
        insert_error_log(cmp_id, connection_id,error_message, "ensure_service_account_and_permissions")
        raise RuntimeError(error_message)


def sanitize_string(value):
    if isinstance(value, str):
        # Remove all control characters except \t (\x09), \n (\x0A), \r (\x0D), and VT (\x0B)
        # value = re.sub(r'[\x00-\x08\x0C\x0E-\x1F\x7F]', '', value)
        return value
    return value


def upload_to_bucket(df, table_name, connection_id, force=False, max_retries=5):
    bucket_name = BUCKET_NAME
    blob_name = f"temp_data_dynamics_f&o_{table_name}_{connection_id}.csv"

    try:
        # Initialize GCS client and bucket
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        # Optional: Delete existing blob if force=True
        if force and blob.exists():
            print(f"🗑️ Deleting existing blob: {blob_name}")
            blob.delete()

        # Convert DataFrame to CSV
        csv_data = df.to_csv(index=False, encoding='utf-8', quoting=csv.QUOTE_ALL, escapechar='\\')

        # Retry upload on 503 errors
        for attempt in range(max_retries):
            try:
                print(f"⬆️ Uploading to GCS (attempt {attempt+1}): {blob_name}")
                blob.upload_from_string(csv_data)
                print("✅ Upload successful.")
                return f"gcs://{bucket_name}/{blob_name}"
            except ServiceUnavailable as e:
                print(f"⚠️ GCS service unavailable (503) on attempt {attempt+1}: {e}")
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            except Exception as other_error:
                print(f"❌ Upload failed on attempt {attempt+1}: {other_error}")
                tb_module.print_exc()
                break  # Don't retry on non-503 errors

    except Exception as final_error:
        print(f"❌ Fatal error uploading '{table_name}': {final_error}")
        tb_module.print_exc()

    return None  # Ensure failure is detectable


def insert_error_log(cmp_id, connection_id, message, function_name):
    """
    Inserts a log entry into the `errorlog_entry` table.

    Args:
        cmp_id (int): Company ID.
        connection_id (int): Connection ID.
        message (str): Log message describing the error.
        function_name (str): Name of the function where the error occurred.

    Returns:
        int: The newly inserted log ID, or None if an error occurs.
    """
    try:
        connection = postgres_connection()  # Ensure this function is implemented correctly
        if connection is None:
            print("Failed to establish connection to the database.")
            return None

        cursor = connection.cursor()

        # Get the current timestamp
        current_time = datetime.utcnow()

        # Insert the log entry (Auto-incrementing ID, so we don't manually handle it)
        log_insert_query = '''
            INSERT INTO error_log_entry (cmp_id, connection_id, message, function_name, created_on)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        '''
        cursor.execute(
            log_insert_query,
            (cmp_id, connection_id, message, function_name, current_time)
        )

        # Retrieve new log ID
        log_id = cursor.fetchone()[0]

        # Commit transaction
        connection.commit()

        print(f"Error log entry inserted successfully with log_id: {log_id}")
        return log_id

    except psycopg2.Error as e:
        print(f"Error inserting into errorlog_entry: {str(e)}")
        return None

    finally:
        # Ensure cursor and connection are closed
        if connection:
            cursor.close()
            connection.close()


def round_up_to_multiple(x: int, base: int = 512) -> int:
    """
    Round x up to the next multiple of base.
    If x is already a multiple, it returns x + base.
    Examples:
      round_up_to_next_multiple(510, 512) -> 512
      round_up_to_next_multiple(512, 512) -> 1024
      round_up_to_next_multiple(513, 512) -> 1024
    """
    return ((x // base) + 1) * base


def handle_limit_exceeded_errors(conn, errors, staging_table, table_name=None, execute_alter=False, increment=512):
    """
    Always increases VARCHAR columns by +512 chars upon overflow error,
    without checking existing lengths.
    """
    import re

    alter_statements = []
    seen_columns = set()
    target_tables = [staging_table]
    if table_name and table_name != staging_table:
        target_tables.append(table_name)

    for error in errors:
        error_str = str(error)

        if "User character length limit" not in error_str:
            continue

        col_match = re.search(r'\["([^"]+)":\d+\]', error_str)
        if not col_match:
            continue
        column_name = col_match.group(1)
        if column_name in seen_columns:
            continue
        seen_columns.add(column_name)

        # Optional: Extract actual value (can skip if not used)
        data_match = re.search(r"exceeded by string '(.*?)'", error_str)
        if data_match:
            try:
                overflow_string = data_match.group(1).encode('utf-8').decode('unicode_escape')
                actual_length = len(overflow_string)
            except Exception:
                actual_length = None
        else:
            actual_length = None

        for target in target_tables:
            # Instead of checking actual size, just add +512 blindly
            alter_sql = f'ALTER TABLE "{target}" ALTER COLUMN "{column_name}" SET DATA TYPE VARCHAR({increment} + CHARACTER_MAXIMUM_LENGTH);'

            # But since Snowflake SQL doesn't support dynamic VARCHAR sizing directly,
            # we simulate it by fetching current length first and calculating manually:
            col_size_query = f"""
                SELECT CHARACTER_MAXIMUM_LENGTH
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = '{target}' AND COLUMN_NAME = '{column_name.upper()}'
            """
            try:
                current_length_result = conn.execute(text(col_size_query)).fetchone()
                current_length = current_length_result[0] if current_length_result else 0
                new_length = current_length + increment

                alter_sql = f'ALTER TABLE "{target}" ALTER COLUMN "{column_name}" SET DATA TYPE VARCHAR({new_length});'
                alter_statements.append(alter_sql)

                if execute_alter:
                    try:
                        conn.execute(text(alter_sql))
                        print(f"✅ Executed: {alter_sql}")
                    except Exception as e:
                        print(f"❌ Failed to execute ALTER for '{column_name}' on table '{target}': {e}")
                else:
                    print(f"🛠️ Suggested: {alter_sql} (+{increment}, previous {current_length})")

            except Exception as e:
                print(f"⚠️ Could not fetch column length for {column_name} in {target}: {e}")

    return alter_statements


def _snowflake_row_to_dict(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "_mapping"):
        return {str(k): row._mapping[k] for k in row._mapping.keys()}
    try:
        return {str(i): v for i, v in enumerate(tuple(row))}
    except Exception:
        return {"value": repr(row)}


def _extract_copy_validation_error_fields(err_row):
    """Extract first_error + column from Snowflake COPY validation rows."""
    err_dict = _snowflake_row_to_dict(err_row)
    first_error = str(err_dict.get("first_error", "") or "")
    error_col = str(err_dict.get("first_error_column_name", "") or "")

    if not first_error or not error_col:
        try:
            vals = list(err_dict.values())
            if not first_error and len(vals) > 6:
                first_error = str(vals[6] or "")
            if not error_col and len(vals) > 8:
                error_col = str(vals[8] or "")
        except Exception:
            pass

    return first_error, error_col, err_dict


def _is_copy_char_limit_error(first_error: str, err_dict: dict) -> bool:
    fe = str(first_error or "").lower()
    if (
        "character length limit" in fe
        or "max length" in fe
        or "too long" in fe
        or "user character length limit" in fe
    ):
        return True
    code_haystack = " ".join(str(v) for v in (err_dict or {}).values()).lower()
    return "100074" in code_haystack


def _build_copy_validation_hints(errors, df, max_hints=20):
    column_hints = []
    for err_row in errors[:max_hints]:
        first_error, error_col, err_dict = _extract_copy_validation_error_fields(err_row)
        m = re.search(r'\["([^"]+)":\d+\]', error_col)
        col_name = m.group(1) if m else ""
        if col_name and col_name not in df.columns:
            col_upper = col_name.upper()
            for df_col in df.columns:
                if str(df_col).upper() == col_upper:
                    col_name = str(df_col)
                    break
        if col_name:
            column_hints.append(f"{col_name}: snowflake_error={first_error}")
        elif error_col or first_error:
            column_hints.append(f"column={error_col or 'unknown'} snowflake_error={first_error}")
        else:
            column_hints.append(f"raw_validation_row={err_dict}")
    return column_hints


def _summarize_copy_result_rows(copy_rows):
    summaries = []
    for row in copy_rows or []:
        d = _snowflake_row_to_dict(row)
        summary = {
            k: d.get(k)
            for k in (
                "status",
                "rows_parsed",
                "rows_loaded",
                "errors_seen",
                "first_error",
                "first_error_column_name",
                "first_error_line",
            )
            if k in d
        }
        if not summary:
            vals = list(d.values())
            if len(vals) >= 7:
                summary = {
                    "status": vals[1] if len(vals) > 1 else None,
                    "rows_parsed": vals[2] if len(vals) > 2 else None,
                    "rows_loaded": vals[3] if len(vals) > 3 else None,
                    "errors_seen": vals[5] if len(vals) > 5 else None,
                    "first_error": vals[6] if len(vals) > 6 else None,
                    "first_error_column_name": vals[8] if len(vals) > 8 else None,
                }
        if summary:
            summaries.append(summary)
    return summaries


def log_copy_validation_errors_append(
    errors: list,
    table_name: str,
    log_dir: Path,
    connection_id: int,
    object_name: str
) -> Path:
    """
    Appends COPY INTO validation errors into a single CSV file per connection.

    Args:
        errors (list): COPY INTO error rows from Snowflake (tuples).
        table_name (str): Name of the staging table where COPY INTO failed.
        log_dir (Path): Base directory where log files are stored.
        connection_id (int): Connection ID for file naming.
        object_name (str): Salesforce object name (e.g., "Account").

    Returns:
        Path: Path to the written CSV error log file.
    """

    # Ensure the directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create a single per-connection log file
    log_file_name = f"{object_name}_{connection_id}_copy_errors_log.csv"
    log_file_path = log_dir / log_file_name

    header = [
        "Timestamp", "Table Name", "CSV File", "Row Number", "Column Name",
        "SFID", "Error Code", "Error Message"
    ]

    file_exists = log_file_path.exists()

    with open(log_file_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)

        for err in errors:
            try:
                # Unpack known fields
                error_message = err[0]
                csv_file = err[1]
                row_number = err[2]
                error_code = err[7] if len(err) > 7 else "UNKNOWN"
                column_ref = err[8] if len(err) > 8 else "UNKNOWN"

                # Extract just the column name from Snowflake column format
                column_match = re.search(r'\["(.+?)"', column_ref)
                column_name = column_match.group(1) if column_match else column_ref

                # Extract SFID from last field, which is usually raw CSV string
                raw_row = str(err[-1]) if len(err) > 9 else ""
                sfid = raw_row.split(",")[0] if raw_row else "UNKNOWN"

                writer.writerow([
                    datetime.now().isoformat(),
                    table_name,
                    csv_file,
                    row_number,
                    column_name,
                    sfid,
                    error_code,
                    error_message
                ])
            except Exception as e:
                print(f"⚠️ Could not log error properly: {e} → {err}")

    print(f"📄 COPY error log updated at: {log_file_path}")
    return log_file_path


def check_and_print_n_p_in_dataframe(df: pd.DataFrame):
    """
    Checks each row in the DataFrame for string fields containing
    exactly 'n' or 'p' (case-insensitive) and logs them along with the record ID.
    """
    if df.empty:
        logger.info("DataFrame is empty, no 'n' or 'p' values to check.")
        return

    # Identify string columns
    string_cols = df.select_dtypes(include='object').columns

    for index, row in df.iterrows():
        record_id = row.get('ID') # Assuming 'Id' is always present and correctly capitalized
        found_fields = {}

        for col in string_cols:
            value = row.get(col)
            if isinstance(value, str) and (value.lower() == 'n'):
                found_fields[col] = value

        if found_fields:
            logger.info(f"🔍 Record ID: {record_id} (from DataFrame) has 'n' or 'p' values in the following fields:")
            for key, value in found_fields.items():
                logger.info(f"  - {key}: '{value}'")


def safe_parse_datetime_series(s: pd.Series, dtype: str) -> pd.Series:
    """
    Convert a Series to datetime, handling pandas limits via row-by-row fallback
    for out-of-bounds or complex timezone cases.
    """
    try:
        # Fast path with pandas
        s2 = pd.to_datetime(s, errors='raise', utc=True)
    except (OutOfBoundsDatetime, ValueError):
        # Fallback to slower row-by-row parsing
        def parse_cell(v):
            if pd.isna(v) or v == "":
                return pd.NaT
            try:
                return parser.parse(v)
            except Exception:
                return pd.NaT

        s2 = s.map(parse_cell)
    return s2


def _coerce_salesforce_is_deleted(value) -> bool:
    if pd.isna(value) or value == "":
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _filter_is_deleted_rows_from_df(df: pd.DataFrame) -> pd.DataFrame:
    """Drop Salesforce recycle-bin rows (IsDeleted=true) before staging on initial load."""
    for col in ("IS_DELETED", "ISDELETED"):
        if col not in df.columns:
            continue
        before = len(df)
        mask = df[col].map(_coerce_salesforce_is_deleted)
        filtered = df.loc[~mask].copy()
        dropped = before - len(filtered)
        if dropped:
            print(f"🗑️ Initial load: excluded {dropped} IsDeleted=true row(s) before staging")
        return filtered
    return df


def insert_into_staging(
    engine, staging_table: str, data: List[Dict[str, Any]], fields: List[str],
    table_name: str, snowflake_schema: str, is_integration_checked: bool,
    connection_id: int, cmp_id: int, is_updated: str, is_deleted: str, log_files: dict,
    migration_session_id: str = None, job_name: str = None,
    exclude_deleted_rows: bool = False,
):
    if not data:
        print("\u26a0\ufe0f No data received for insertion.")
        return is_integration_checked

    print("📊 Creating DataFrame...")
    df = pd.DataFrame(data)
    df.columns = [normalize_name(c) for c in df.columns]

    if exclude_deleted_rows:
        df = _filter_is_deleted_rows_from_df(df)
        if df.empty:
            print("⚠️ No rows left after excluding IsDeleted=true on initial load.")
            return is_integration_checked
    
    is_updated = normalize_name(is_updated)
    is_deleted = normalize_name(is_deleted)

    df[is_updated] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    df[is_deleted] = df["IS_DELETED"].astype(bool) if "IS_DELETED" in df.columns else False

    normalized_fields = [normalize_name(f) for f in fields]
    if is_updated not in normalized_fields:
        normalized_fields.append(is_updated)
    if is_deleted not in normalized_fields:
        normalized_fields.append(is_deleted)
    df = df[[f for f in normalized_fields if f in df.columns]]

    print(f"📋 Columns in DataFrame: {df.columns.tolist()}")
    print(f"🔢 Initial record count: {len(df)}")

    print("🧹 Replacing common null-like values...")
    # df.replace({pd.NaT: None, "nan": None, "NaN": None, "": None, "None": None}, inplace=True)
    def clean_nulls(val):
        return "" if pd.isna(val) else val


    df = df.applymap(clean_nulls)
    df = df.where(pd.notnull(df), None)
    
    print("🔍 Fetching Snowflake column types and order...")
    def get_snowflake_column_types_and_order(engine, schema: str, table: str):
        query = f"""
        SELECT COLUMN_NAME, DATA_TYPE 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}'
        ORDER BY ORDINAL_POSITION
        """
        with engine.connect() as conn:
            result = conn.execute(text(query)).fetchall()
        column_types = {row[0]: row[1].upper() for row in result}
        column_order = [row[0] for row in result]
        return column_types, column_order

    normalized_table_name = normalize_name(table_name)
    normalized_staging_table = normalize_name(staging_table)

    column_types, column_order = get_snowflake_column_types_and_order(
        engine, snowflake_schema, normalized_staging_table
    )
    print(f"✅ Retrieved column types: {column_types}")

    if is_updated not in column_order:
        column_order.append(is_updated)
    if is_deleted not in column_order:
        column_order.append(is_deleted)

    print("🛠️ Converting columns to Snowflake-compatible types...")
    import json
    for col in df.columns:
        if col in column_types:
            dtype = column_types[col]
            try:
                print(f"⚖️ Processing column '{col}' as {dtype}")
                if dtype in ['DATE', 'DATETIME', 'TIMESTAMP', 'TIMESTAMP_NTZ', 'TIMESTAMP_TZ', 'TIMESTAMP_LTZ']:
                    # Safely parse even far-future dates
                    parsed = safe_parse_datetime_series(df[col], dtype)
                    
                    # Apply your existing formatting rules
                    if dtype == 'DATE':
                        df[col] = parsed.dt.strftime('%Y-%m-%d')
                    elif dtype in ['TIMESTAMP_NTZ', 'DATETIME']:
                        df[col] = parsed.apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if pd.notnull(x) else None)
                    elif dtype in ['TIMESTAMP_TZ', 'TIMESTAMP_LTZ']:
                        df[col] = parsed.apply(lambda x:
                                            x.astimezone(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S.%f %z') if pd.notnull(x) else None)
                    else:  # e.g. plain TIMESTAMP
                        df[col] = parsed.apply(lambda x:
                                            x.strftime('%Y-%m-%d %H:%M:%S.%f %z')[:-3] if pd.notnull(x) else None)
                elif dtype == 'BOOLEAN':
                    df[col] = df[col].apply(lambda x: str(x).lower() in ['true', '1'] if pd.notna(x) else False)
                elif dtype in ['NUMBER', 'FLOAT', 'DECIMAL']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                elif dtype == 'INTEGER':
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
                elif dtype == 'VARIANT':
                    import json
                    import re

                    def safe_json(v):
                        try:
                            # Handle None/NaN values first
                            if pd.isna(v) or v is None:
                                return None

                            if isinstance(v, (dict, list)):
                                return json.dumps(v)

                            if isinstance(v, str):
                                # v = v.strip()

                                if not v:
                                    return None

                                v = re.sub(r'\b(nul+|None|NONE)\b', 'null', v)

                                v = v.replace('\\', '\\\\')  # Escape backslashes first
                                v = v.replace('"', '\\"')    # Escape inner double quotes
                                v = v.replace('\n', '\\n')
                                v = v.replace('\r', '\\r')
                                v = v.replace('\t', '\\t')


                                # Remove trailing commas
                                v = re.sub(r',\s*}', '}', v)
                                v = re.sub(r',\s*]', ']', v)

                                # Validate JSON
                                if v.startswith('{') or v.startswith('['):
                                    try:
                                        test_v = v.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t').replace('\\\\', '\\')
                                        json.loads(test_v)
                                        return v
                                    except:
                                        pass

                                return f'"{v}"'

                            return json.dumps(v)

                        except Exception as e:
                            print(f"⚠️ Invalid JSON: {v} - {e}")
                            return None

                    df[col] = df[col].apply(safe_json)
                


                
                elif dtype == 'GEOGRAPHY':
                    def to_geojson(val):
                        try:
                            if pd.isna(val) or val is None or (isinstance(val, str) and val.strip() in ["", "null", "NULL", "None"]):
                                return None

                            if isinstance(val, str):
                                val = json.loads(val)

                            if isinstance(val, dict) and 'latitude' in val and 'longitude' in val:
                                return json.dumps({
                                    "type": "Point",
                                    "coordinates": [val["longitude"], val["latitude"]]
                                })

                            return json.dumps(val) if isinstance(val, (dict, list)) else val

                        except Exception as e:
                            print(f"⚠️ Invalid GEOGRAPHY in column '{col}': {val} - {e}")
                            return None

                    df[col] = df[col].apply(to_geojson)

                else:
                    def is_geography_like(val):
                        # Check if it's a dict with lat/lon, or a valid GeoJSON string
                        try:
                            if pd.isna(val) or val is None:
                                return False

                            if isinstance(val, str):
                                val = val.strip()
                                if val in ["", "null", "NULL", "None"]:
                                    return False
                                val = json.loads(val)

                            if isinstance(val, dict):
                                # Latitude/Longitude pair
                                if 'latitude' in val and 'longitude' in val:
                                    return True
                                # Valid GeoJSON
                                if val.get("type") == "Point" and isinstance(val.get("coordinates"), list):
                                    return True
                        except Exception:
                            return False

                        return False

                    def serialize_if_geo(val):
                        try:
                            if is_geography_like(val):
                                if isinstance(val, str):
                                    val = json.loads(val)
                                if 'latitude' in val and 'longitude' in val:
                                    return json.dumps({
                                        "type": "Point",
                                        "coordinates": [val["longitude"], val["latitude"]]
                                    })
                                return json.dumps(val)
                            return str(val) if pd.notna(val) else None
                        except Exception as e:
                            print(f"⚠️ Error in VARCHAR Geo check for column '{col}': {val} - {e}")
                            return str(val) if pd.notna(val) else None

                    df[col] = df[col].apply(serialize_if_geo)  # optional .apply(str) if needed

            except Exception as e:
                print(f"❌ Error converting column {col} to {dtype}: {e}")


    # ✅ Fill missing Snowflake columns in the DataFrame with NULLs (None)
    for col in column_order:
        if col not in df.columns:
            print(f"🧩 Missing column '{col}' in DataFrame, filling with NULLs")
            df[col] = None


    print("📑 Reordering columns to match Snowflake...")
    df = df[[col for col in column_order if col in df.columns]]

    # CHANGE 6: Skip backslash escaping for VARIANT columns
    print("🔒 Escaping backslashes...")
    # def selective_escape(x, col_name):
    #     if isinstance(x, str) and col_name in column_types and column_types[col_name] != 'VARIANT':
    #         return x.replace('\\', '\\\\')
    #     return x
    
    # for col in df.columns:
    #     df[col] = df[col].apply(lambda x: selective_escape(x, col))
    def prepare_string_for_snowflake(value, column_type):
        if pd.isna(value) or value is None:
            return ""
        if isinstance(value, str):
            if column_type in ['TEXT', 'STRING', 'VARCHAR']:
                value = value.replace('\\', '\\\\')  # Escape backslash first
                value = value.replace('"', '\\"')    # Escape double quote
                value = value.replace('\n', '\\n')
                value = value.replace('\r', '\\r')
                value = value.replace('\t', '\\t')
            return value
        return value

    df[col] = df[col].apply(lambda x: prepare_string_for_snowflake(x, dtype))

    
    print("🧬 Parsing variant fields...")
    raw_data = df.to_dict(orient="records")
    raw_data = [parse_variant_fields(row, set(df.columns)) for row in raw_data]

    # Using global constants for Snowflake configuration

    if not is_integration_checked:
        print("🔐 Checking GCS integration...")
        res = ensure_gcs_storage_integration(engine, DEMO_STORAGE, GCS_BUCKET_URL, connection_id, cmp_id)
        if not res:
            print("🔧 Setting up service account and permissions...")
            ensure_service_account_and_permissions(engine, DEMO_STORAGE, BUCKET_NAME, PROJECT_ID, cmp_id, connection_id)
        is_integration_checked = True

    print("🗼 Final cleanup before CSV upload...")
    # CHANGE 7: Don't fill None with empty string for VARIANT columns
    for col in df.columns:
        if col in column_types and column_types[col] == 'VARIANT':
            # Keep None for VARIANT columns, convert to empty string
            df[col] = df[col].fillna("")
        else:
            df[col] = df[col].fillna("").astype(str)

    print("🧪 Sample rows before CSV upload:")
    if table_name=="USER":
        print(df[[is_updated, is_deleted,'DEFAULT_GROUP_NOTIFICATION_FREQUENCY']].head(3))
        check_and_print_n_p_in_dataframe(df)
    else:
        print(df[[is_updated, is_deleted]].head(3))

    print("📤 Uploading DataFrame to GCS...")
    temp_csv_gcs_url = upload_to_bucket(df, table_name, connection_id, force=False)
    print(f"✅ Uploaded to GCS at: {temp_csv_gcs_url}")

    print("📦 Checking if staging table exists...")
    check_sql = f"""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_SCHEMA = '{snowflake_schema}' 
        AND TABLE_NAME = '{normalized_staging_table}';
    """
    with engine.connect() as conn:
        exists = conn.execute(text(check_sql)).fetchone()[0] > 0
        print(f"🔍 Table exists: {exists}")
        if not exists:
            print(f"🤟 Creating staging table '{normalized_staging_table}'...")
            create_staging_table(engine, normalized_table_name, normalized_staging_table)

    print("📥 Running COPY INTO validation mode...")
    file_path = f"{GCS_BUCKET_URL}temp_data_dynamics_f&o_{table_name}_{connection_id}.csv"

    file_format_options = """
                    TYPE = CSV
                    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
                    SKIP_HEADER = 1
                    ESCAPE = '\\\\'
                    ESCAPE_UNENCLOSED_FIELD = '\\\\'
                    FIELD_DELIMITER = ','
                    RECORD_DELIMITER = '\\n'
                    TIMESTAMP_FORMAT = 'AUTO'
                    DATE_FORMAT = 'AUTO'
                    NULL_IF = ('', 'NULL')
                    ENCODING = 'UTF8'
                """

    # validation_query = f"""
    #     COPY INTO {snowflake_schema}."{staging_table}"
    #     FROM '{file_path}'
    #     FILE_FORMAT = (
    #         TYPE = CSV
    #         FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    #         SKIP_HEADER = 1
    #         ESCAPE_UNENCLOSED_FIELD = '\\\\'
    #         TIMESTAMP_FORMAT = 'AUTO'
    #         DATE_FORMAT = 'AUTO'
    #         NULL_IF = ('', 'None', 'null', 'NULL')
    #     )
    #     STORAGE_INTEGRATION = {DEMO_STORAGE}
    #     VALIDATION_MODE = RETURN_ERRORS;
    # """
    validation_query = f"""
                    COPY INTO {snowflake_schema}."{staging_table}"
                    FROM '{file_path}'
                    FILE_FORMAT = ({file_format_options})
                    STORAGE_INTEGRATION = {DEMO_STORAGE}
                    VALIDATION_MODE = RETURN_ERRORS;
                """

    def _log_validation_errors_to_file(errors):
        copy_error_file = None
        if log_files is not None:
            copy_error_file = log_files.get('copy_error_file')
        if copy_error_file is not None:
            log_copy_validation_errors_append(
                errors=errors,
                table_name=normalized_staging_table,
                log_dir=copy_error_file.parent,
                connection_id=connection_id,
                object_name=table_name,
            )
        else:
            print("⚠️ log_files is None or copy_error_file missing — skipping log file write.")

    with engine.connect() as conn:
        try:
            print(
                f"[COPY_DEBUG] table={table_name} schema={snowflake_schema} "
                f"staging={snowflake_schema}.{staging_table} df_rows={len(df.index)}"
            )
            errors = conn.execute(text(validation_query)).fetchall()
            print(f"[COPY_DEBUG] validation_error_count={len(errors)}")
            retry_attempted = False

            if errors:
                print(f"🚨 Found {len(errors)} validation errors.")
                print(errors)
                _log_validation_errors_to_file(errors)

                alter_statements = handle_limit_exceeded_errors(
                    conn, errors, normalized_staging_table, normalized_table_name, execute_alter=True
                )
                if alter_statements:
                    retry_attempted = True
                    print("🔁 Re-uploading file after schema update...")
                    upload_to_bucket(df, table_name, connection_id, force=True)

                errors = conn.execute(text(validation_query)).fetchall()
                print(f"[COPY_DEBUG] validation_error_count_after_auto_alter={len(errors)}")
                if errors:
                    _log_validation_errors_to_file(errors)
            else:
                print("✅ No validation errors found.")

            if errors:
                column_hints = _build_copy_validation_hints(errors, df)
                hint_block = "; ".join(column_hints) if column_hints else "no column hints"
                strict_message = (
                    f"COPY validation failed for table={table_name} "
                    f"staging={snowflake_schema}.{staging_table} errors={len(errors)}. "
                    f"Retry attempted={retry_attempted}. Details: {hint_block}"
                )
                print(f"[COPY_DEBUG] {strict_message}")
                insert_error_log(cmp_id, connection_id, strict_message[:4000], "insert_into_staging")
                raise RuntimeError(strict_message)

            count_before_query = f'SELECT COUNT(*) FROM {snowflake_schema}."{normalized_staging_table}"'
            rows_before = conn.execute(text(count_before_query)).fetchone()[0]
            print(f"📊 Rows in staging table before COPY: {rows_before}")

            print("📥 Inserting data with final COPY INTO (ON_ERROR=ABORT_STATEMENT)...")
            final_copy_query = validation_query.replace(
                "VALIDATION_MODE = RETURN_ERRORS", "ON_ERROR = ABORT_STATEMENT"
            )
            print(f"[COPY_DEBUG] copy_sql={final_copy_query}")

            result = conn.execute(text(final_copy_query))
            copy_results = result.fetchall() if result is not None else []
            for summary in _summarize_copy_result_rows(copy_results):
                print(f"[COPY_DEBUG] copy_summary table={table_name} {summary}")
                errors_seen = summary.get("errors_seen")
                if errors_seen is not None and int(errors_seen or 0) > 0:
                    copy_fail = (
                        f"COPY INTO reported errors for table={table_name} "
                        f"staging={snowflake_schema}.{staging_table}: {summary}"
                    )
                    insert_error_log(cmp_id, connection_id, copy_fail[:4000], "insert_into_staging")
                    raise RuntimeError(copy_fail)
                status = str(summary.get("status") or "").upper()
                if status == "PARTIALLY_LOADED":
                    copy_fail = (
                        f"COPY INTO partially loaded data for table={table_name} "
                        f"staging={snowflake_schema}.{staging_table}: {summary}"
                    )
                    insert_error_log(cmp_id, connection_id, copy_fail[:4000], "insert_into_staging")
                    raise RuntimeError(copy_fail)

            rows_after = conn.execute(text(count_before_query)).fetchone()[0]
            rows_inserted = rows_after - rows_before
            print(f"📊 Rows in staging table after COPY: {rows_after}")
            print(f"✅ Total rows inserted: {rows_inserted}")

            if len(df.index) > 0 and rows_inserted == 0:
                zero_rows_msg = (
                    f"COPY loaded 0 rows into staging {snowflake_schema}.{staging_table} "
                    f"from non-empty dataframe ({len(df.index)} rows)."
                )
                insert_error_log(cmp_id, connection_id, zero_rows_msg, "insert_into_staging")
                raise RuntimeError(zero_rows_msg)

            # CRITICAL: Commit COPY (SQLAlchemy 2.x implicit txn rolls back on exit).
            # Without this, merge/progress COUNT from a new connection sees 0 rows.
            conn.commit()
            print(
                f"[COPY_DEBUG] committed staging={snowflake_schema}.{normalized_staging_table} "
                f"rows_inserted={rows_inserted}"
            )

            # Update progress tracking (after commit so COUNT/batch_rows are visible)
            if migration_session_id and job_name:
                SimpleProgressTracker.update_staging_count(
                    engine=engine,
                    connection_id=connection_id,
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    table_name=table_name,
                    staging_table=normalized_staging_table,
                    snowflake_schema=snowflake_schema,
                    batch_rows=rows_inserted,
                )

            

            print(f"✅ Data processing completed for staging table '{normalized_staging_table}'")

        except Exception as e:
            message = f"❌ COPY INTO failed: {e}"
            print(message)
            insert_error_log(cmp_id, connection_id, message, "insert_into_staging")
            raise


def merge_staging_to_target(
    engine,
    staging_table: str,
    target_table: str,
    fields: List[str],
    primary_key: str,
    is_deleted: str,
    is_updated: str
) -> Dict[str, int]:
    """
    Merge data from staging table to target table using MERGE statement
    and return statistics on inserted and updated rows.
    """
    try:
        norm_staging = normalize_name(staging_table)
        norm_target = normalize_name(target_table)
        norm_fields = [normalize_name(f) for f in fields]
        norm_primary = normalize_name(primary_key)
        is_deleted = normalize_name(is_deleted)
        is_updated = normalize_name(is_updated)

        if norm_primary not in norm_fields:
            raise ValueError(f"❌ Primary key '{primary_key}' not in provided fields")

        # ✅ Ensure audit fields are included
        for audit_field in [is_deleted, is_updated]:
            if audit_field not in norm_fields:
                norm_fields.append(audit_field)

        # ✅ Prepare update and insert clauses
        update_fields = [f for f in norm_fields if f != norm_primary]
        update_set = ", ".join([f'"{f}" = s."{f}"' for f in update_fields])
        insert_columns = ", ".join([f'"{f}"' for f in norm_fields])
        insert_values = ", ".join([f's."{f}"' for f in norm_fields])

        # ✅ Check if this is a Salesforce History table (FieldHistory tables have duplicate IDs)
        is_history_table = 'FIELDHISTORY' in norm_target.upper() or 'HISTORY' in norm_target.upper()
        
        # ✅ For History tables, de-duplicate staging data using qualified subquery
        if is_history_table:
            logger.info(f"📚 Detected History table: {norm_target} - Using de-duplicated merge")
            
            # Use a subquery with ROW_NUMBER() to get only the latest row per ID
            # Keep the row with the most recent _FIVETRAN_SYNCED timestamp
            # If timestamps are equal, use CREATED_DATE as tiebreaker, then ID for full determinism
            created_date_col = normalize_name('CREATED_DATE') if 'CREATED_DATE' in [f.upper() for f in fields] else None
            
            # Build deterministic ORDER BY clause
            order_by_parts = [f'"{is_updated}" DESC NULLS LAST']
            if created_date_col:
                order_by_parts.append(f'"{created_date_col}" DESC NULLS LAST')
            order_by_parts.append(f'"{norm_primary}" DESC')  # Final tiebreaker using ID
            
            order_by_clause = ', '.join(order_by_parts)
            
            staging_source = f"""(
                SELECT * FROM (
                    SELECT *, 
                           ROW_NUMBER() OVER (PARTITION BY "{norm_primary}" ORDER BY {order_by_clause}) as rn
                    FROM "{norm_staging}"
                )
                WHERE rn = 1
            )"""
            logger.info(f"🔍 De-duplication ORDER BY: {order_by_clause}")
        else:
            staging_source = f'"{norm_staging}"'

        merge_query = f"""
        MERGE INTO "{norm_target}" t
        USING {staging_source} s
        ON t."{norm_primary}" = s."{norm_primary}"
        WHEN MATCHED THEN
            UPDATE SET {update_set}
        WHEN NOT MATCHED THEN
            INSERT ({insert_columns})
            VALUES ({insert_values});
        """

        logger.info(f"📊 Starting MERGE from staging: '{norm_staging}' to target: '{norm_target}' using PK: '{norm_primary}'")
        if is_history_table:
            logger.info(f"⚠️  History table detected - De-duplicating staging data before merge")
        logger.debug(f"📄 MERGE SQL:\n{merge_query.strip()}")
        logger.info("📥 Executing MERGE statement...")

        with engine.connect() as con:
            con.execute(text(merge_query))

            # ⚠️ Snowflake does not return MERGE row counts by default
            count_query = f'SELECT COUNT(*) FROM "{norm_staging}"'
            estimated_inserted = con.execute(text(count_query)).scalar() or 0
            logger.info(f"📊 Estimated rows merged from staging: {estimated_inserted}")

            con.commit()
            logger.info(f"📊 MERGE committed for staging '{norm_staging}' -> target '{norm_target}'")

            return {
                "inserted": estimated_inserted,
                "updated": 0  # Not tracked unless you use query history or RETURNING
            }

    except Exception as e:
        logger.error(f"❌ Merge failed for table '{target_table}': {e}", exc_info=True)
        raise


async def process_salesforce_to_snowflake_mapping(
    mapping: str, salesforce_details: dict, snowflake_details: dict,
    connection_id: int, job_name: str, migration_session_id: int, cmp_id: int,
    user_id: int, environment: str, chunk_count: int, logs_creation: bool,
    connection_name: str, company_name: str, session: str, is_updated: str, is_deleted: str, safety_interval_cutoff: datetime,
    *,  # Use keyword-only arguments for trackers and flags to prevent positional shifts
    failure_tracker=None, 
    is_final_attempt: bool = True, 
    drop_and_reload: bool = False,
    shared_failed_tables=None,
    shared_row_counts=None
):
    """
    Process Salesforce to Snowflake mapping with dynamic object checking.
    
    This function uses dynamic API calls to determine if objects are queryable
    and performs the complete Salesforce to Snowflake data sync.
    """
    
    print("is updated", is_updated)
    print("is_deleted", is_deleted)
    table_exist = False
    delete_transfer_needed = False
    process_logger = logging.getLogger(f"{__name__}.process_salesforce_to_snowflake_mapping")

    try:
        salesforce_object = mapping
        
        process_logger.info(f"Starting sync for {salesforce_object}")
        update_table_status_snowflake(connection_id, salesforce_object, "in_progress", job_name, migration_session_id)

        last_checked_data = get_last_checked_dates_and_fields(connection_id, [salesforce_object], True)
        last_synced = last_checked_data.get(salesforce_object, {}).get("last_checked_date", None)
        is_history = last_checked_data.get(salesforce_object, {}).get("is_history", False)

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
        
        # 🚀 Initialize dynamic checker with the Salesforce client
        initialize_dynamic_checker(sf, cache_ttl_minutes=60)
        
        # 🚫 DYNAMIC CHECK: Skip objects that are not queryable
        if is_unsupported_salesforce_object(salesforce_object, sf):
            skip_reason = f"Salesforce object '{salesforce_object}' is not queryable (checked via API)"
            print(f"⏭️ SKIPPING: {skip_reason}")
            process_logger.warning(skip_reason)
            
            # Add to failure tracker (for sequential execution) or shared list (for parallel execution)
            print(f"🔍 Skip tracking - failure_tracker: {failure_tracker}, shared_failed_tables type: {type(shared_failed_tables)}")
            
            # Track failure for summary calculation
            if shared_failed_tables is not None:
                if hasattr(shared_failed_tables, 'append'):
                    shared_failed_tables.append((salesforce_object, f"SKIPPED: {skip_reason}", "Salesforce to Snowflake"))
                    print(f"📝 Added skipped table '{salesforce_object}' to shared_failed_tables")
                elif hasattr(shared_failed_tables, 'add_failure'):
                    shared_failed_tables.add_failure(salesforce_object, f"SKIPPED: {skip_reason}", "Salesforce to Snowflake")
                    print(f"📝 Added skipped table '{salesforce_object}' to tracker proxy")
            
            if failure_tracker:
                failure_tracker.add_failure(salesforce_object, f"SKIPPED: {skip_reason}", "Salesforce to Snowflake")
                print(f"📝 Added skipped table '{salesforce_object}' to failure_tracker")
            
            # Update status to failed in database
            # Use the consolidated function if available, otherwise the utility specific one
            update_table_status_sf_to_snowflake(connection_id, salesforce_object, "failed", job_name, migration_session_id)
            
            # Log individual failure log entry
            try:
                from utils.error_utils import log_error_to_tool_log
                log_error_to_tool_log(
                    cmp_id=cmp_id,
                    connection_id=connection_id,
                    user_id=user_id,
                    table_name=salesforce_object,
                    error_message=f"SKIPPED: {skip_reason}",
                    session=session,
                    job_name=job_name
                )
            except Exception as log_err:
                process_logger.error(f"⚠️ Failed to log skip to tool_log: {log_err}")
            
            return  # Exit early - don't process this object
        
        print(f"✅ Object '{salesforce_object}' confirmed as queryable via Salesforce API")

        describe = sf.__getattr__(salesforce_object).describe()
        all_fields = get_all_salesforce_fields(sf, salesforce_object, logger=process_logger)

        primary_key = "Id"
        fields_for_query = list(set(all_fields.keys()) | {primary_key})
        if check_field_exists(sf, salesforce_object, "IsDeleted") and "IsDeleted" not in fields_for_query:
            fields_for_query.append("IsDeleted")

        has_system_modstamp = check_field_exists(sf, salesforce_object, "SystemModstamp")
        has_last_modified_date = check_field_exists(
            sf, salesforce_object, "LastModifiedDate"
        )
        has_last_modified = check_field_exists(sf, salesforce_object, "LastModified")
        has_login_time = check_field_exists(sf, salesforce_object, "LoginTime")
        has_created_date = check_field_exists(sf, salesforce_object, "CreatedDate")
        process_logger.info(f"SystemModstamp field exists: {has_system_modstamp}")

        field_type_map = {}
        for field in describe['fields']:
            name = field['name']
            ftype = field['type'].upper()
            field_type_map[name] = {
                'DOUBLE': 'float', 'CURRENCY': 'float', 'PERCENT': 'float',
                'INT': 'int', 'INTEGER': 'int',
                'BOOLEAN': 'boolean',
                'DATE': 'datetime', 'DATETIME': 'datetime'
            }.get(ftype, 'string')

        process_logger.info(f"Created field type map for {len(field_type_map)} fields")

        log_files = create_log_files(logs_creation, company_name, connection_name, connection_id, salesforce_object) if logs_creation else None
        print(log_files)
        start_time = datetime.now(timezone.utc)
        total_inserted = 0
        total_updated = 0
        total_failed = 0

        bulk_api_2_client = BulkAPI2Client(sf, field_type_map)

        query = f"SELECT {', '.join(fields_for_query)} FROM {salesforce_object}"
        snowflake_conn = destination_connection_function(snowflake_details)
        table_exist = ensure_snowflake_table_exists(snowflake_conn, salesforce_object, fields_for_query,describe['fields'], snowflake_details.get("schema"), primary_key,is_deleted,is_updated)
        # Fetch: always queryAll (restricted + deleted visibility). Staging filter on initial/reload only.
        is_incremental_sync = bool(last_synced and table_exist and not drop_and_reload)
        exclude_deleted_rows = not is_incremental_sync
        delete_transfer_needed = True

        process_logger.info(
            f"🔍 DEBUG [{salesforce_object}]: drop_and_reload={drop_and_reload}, "
            f"table_exist={table_exist}, last_synced={last_synced}, "
            f"queryAll=True, exclude_deleted_rows_before_staging={exclude_deleted_rows}"
        )

        upper_ts = safety_interval_cutoff.strftime('%Y-%m-%dT%H:%M:%S+0000')
        lower_ts = None
        if is_incremental_sync:
            try:
                last_synced_dt = datetime.fromisoformat(last_synced.replace("Z", "+00:00"))

                # Ensure cutoff is > last_synced
                if safety_interval_cutoff <= last_synced_dt:
                    safety_interval_cutoff = last_synced_dt + timedelta(seconds=1)

                lower_ts = last_synced_dt.strftime('%Y-%m-%dT%H:%M:%S+0000')
                process_logger.info(
                    f"📌 Applied incremental window: BETWEEN ({lower_ts}, {upper_ts}]"
                )
            except Exception as e:
                process_logger.warning(f"⚠️ Failed to apply incremental filter: {e}")

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




        process_logger.info(f"SOQL Query: {query}")
        job_id = await bulk_api_2_client.create_query_job(salesforce_object, query,chunk_count,delete_transfer_needed)
        results = await bulk_api_2_client.get_job_results(job_id)
        # Note: Don't log len(results) here - could be async generator (fallback) or list (Bulk API 2.0)

        # Generate staging table name with timestamp for uniqueness
        staging_table_raw = f"{salesforce_object}_STAGING_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        staging_table = normalize_name(staging_table_raw)
        create_staging_table(snowflake_conn, salesforce_object, staging_table)
        
        snowflake_schema = snowflake_details.get('schema')
        is_integration_checked = False
        chunk_index = 0
        
        # Check for both old and new fallback job ID formats (streaming async generator)
        if job_id == "fallback_bulk_1" or job_id.startswith("fallback_bulk1_"):
            # Bulk API 1.0 fallback - results is an async generator
            process_logger.info(f"📊 Processing streaming results from Bulk API 1.0 fallback (job: {job_id})")
            chunk_index = 0
            async for chunk in results:
                chunk_index += 1
                process_logger.info(f"Processing streamed chunk {chunk_index} with {len(chunk)} records")
                try:
                    is_integration_checked = insert_into_staging(
                        snowflake_conn, staging_table, chunk, fields_for_query,
                        salesforce_object, snowflake_schema,
                        is_integration_checked, connection_id, cmp_id,is_updated,is_deleted,log_files,
                        str(migration_session_id), job_name,
                        exclude_deleted_rows=exclude_deleted_rows,
                    )
                    
                    # Progress updated inside insert_into_staging after COPY commit.
                except Exception as chunk_error:
                    process_logger.error(f"❌ ERROR: Failed to process chunk {chunk_index}: {chunk_error}")
                    process_logger.error(tb_module.format_exc())
                    total_failed += len(chunk)
                    
                    # Send error notification email for chunk failure
                    # ✅ CHECK: Only send email on final retry attempt
                    if is_final_attempt:
                        try:
                            from utils.email_utils import send_error_notification
                            send_error_notification(
                                table_name=f"{mapping} (Chunk {chunk_index})",
                                error_message=f"Chunk processing error: {str(chunk_error)}\n\nTraceback:\n{tb_module.format_exc()}",
                                connection_name=connection_name,
                                service_name="Salesforce to Snowflake",
                                user_id=user_id,
                                company_id=cmp_id,
                                additional_context={
                                    "job_name": job_name,
                                    "user_id": user_id,
                                    "company_id": cmp_id,
                                    "company_name": company_name,
                                    "migration_session_id": migration_session_id,
                                    "connection_id": connection_id,  # Still available in context if needed
                                    "session": session,
                                    "chunk_index": chunk_index,
                                    "chunk_size": len(chunk)
                                }
                            )
                            process_logger.info(f"📧 Chunk failure email sent (final attempt)")
                        except Exception as email_error:
                            process_logger.error(f"⚠️ Failed to send chunk error notification email: {email_error}")
                    else:
                        process_logger.info(f"⏭️ SKIPPING chunk failure email (not final attempt)")
                    
                    if logs_creation and log_files:
                        log_error_to_response_file(log_files['response_file'], chunk_error, chunk_index)

                    process_logger.error(
                        f"Stopping table {salesforce_object} after chunk {chunk_index} failure "
                        f"— remaining chunks will not run."
                    )
                    raise
        else:
            # Bulk API 2.0 results - results is a list of records
            process_logger.info(f"📊 Retrieved {len(results)} records from Salesforce Bulk API 2.0")
            for chunk in process_salesforce_results_in_chunks(results, chunk_count):
                chunk_index += 1
                process_logger.info(f"Processing chunk {chunk_index} with {len(chunk)} records")
                try:
                    is_integration_checked = insert_into_staging(
                        snowflake_conn, staging_table, chunk, fields_for_query,
                        salesforce_object, snowflake_schema,
                        is_integration_checked, connection_id, cmp_id,is_updated,is_deleted,log_files,
                        str(migration_session_id), job_name,
                        exclude_deleted_rows=exclude_deleted_rows,
                    )
                    
                    # Progress updated inside insert_into_staging after COPY commit.
                except Exception as chunk_error:
                    process_logger.error(f"❌ ERROR: Failed to process chunk {chunk_index}: {chunk_error}")
                    process_logger.error(tb_module.format_exc())
                    total_failed += len(chunk)
                    
                    # Send error notification email for chunk failure
                    # ✅ CHECK: Only send email on final retry attempt
                    if is_final_attempt:
                        try:
                            from utils.email_utils import send_error_notification
                            send_error_notification(
                                table_name=f"{mapping} (Chunk {chunk_index})",
                                error_message=f"Chunk processing error: {str(chunk_error)}\n\nTraceback:\n{tb_module.format_exc()}",
                                connection_name=connection_name,
                                service_name="Salesforce to Snowflake",
                                user_id=user_id,
                                company_id=cmp_id,
                                additional_context={
                                    "job_name": job_name,
                                    "user_id": user_id,
                                    "company_id": cmp_id,
                                    "company_name": company_name,
                                    "migration_session_id": migration_session_id,
                                    "connection_id": connection_id,  # Still available in context if needed
                                    "session": session,
                                    "chunk_index": chunk_index,
                                    "chunk_size": len(chunk)
                                }
                            )
                            process_logger.info(f"📧 Chunk failure email sent (final attempt)")
                        except Exception as email_error:
                            process_logger.error(f"⚠️ Failed to send chunk error notification email: {email_error}")
                    else:
                        process_logger.info(f"⏭️ SKIPPING chunk failure email (not final attempt)")
                    
                    if logs_creation and log_files:
                        log_error_to_response_file(log_files['response_file'], chunk_error, chunk_index)

                    process_logger.error(
                        f"Stopping table {salesforce_object} after chunk {chunk_index} failure "
                        f"— remaining chunks will not run."
                    )
                    raise
        # Finalization
        try:
            print(table_exist)
            table_exist = ensure_snowflake_table_exists(snowflake_conn, salesforce_object, fields_for_query,describe['fields'], snowflake_details.get("schema"), primary_key,is_deleted,is_updated)

            if table_exist:
                merge_result = merge_staging_to_target(snowflake_conn, staging_table, salesforce_object, fields_for_query, primary_key,is_deleted,is_updated)
                total_inserted = merge_result['inserted']
                total_updated = merge_result['updated']
                print("inside merge")
                print(f"insert records{total_inserted}")
                process_logger.info(f"Chunk {chunk_index}: Inserted={merge_result['inserted']}, Updated={merge_result['updated']}")
                # if logs_creation and log_files:
                #     log_chunk_data_with_operation(
                #         log_files['csv_file'], results, salesforce_object,
                #         salesforce_object, connection_name, chunk_index,
                #         merge_result
                #     )
        finally:
            drop_staging_table(snowflake_conn, staging_table, snowflake_schema)
            snowflake_conn.dispose()

        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()

        process_logger.info(f"""
        ✅ Sync completed for {salesforce_object}:
        - Duration: {duration:.2f} seconds
        - Inserted: {total_inserted}
        - Updated: {total_updated}
        - Failed: {total_failed}
        - Total processed: {total_inserted + total_updated + total_failed}
        """)

        data_transfer_logs(
            cmp_id, connection_id, user_id, salesforce_object,
            start_time, end_time, len(fields_for_query),
            session, total_inserted, total_failed, total_updated, 0,job_name=job_name
        )

        if total_inserted > 0 or total_updated > 0:
            update_last_synced_date(connection_id, salesforce_object, safety_interval_cutoff)

        update_table_status_snowflake(connection_id, salesforce_object, "completed", job_name, migration_session_id)
        process_logger.info(f"✅ Successfully synced from {salesforce_object} to Snowflake.")

        # Track row count for completion message
        table_row_count = total_inserted + total_updated
        if shared_row_counts is not None and hasattr(shared_row_counts, 'append'):
            shared_row_counts.append(table_row_count)
            process_logger.info(f"📊 Reported {table_row_count} rows for '{salesforce_object}' to shared_row_counts")

    except Exception as e:
        process_logger.error(f"❌ ERROR: Failed to process mapping {mapping}: {e}")
        process_logger.error(tb_module.format_exc())

        # ✅ Cleanup staging table if processing fails
        # Use staging_table_raw if available (exact name), otherwise use staging_table
        cleanup_table_name = staging_table_raw if 'staging_table_raw' in locals() else (staging_table if 'staging_table' in locals() else None)
        if cleanup_table_name and 'snowflake_conn' in locals() and snowflake_conn:
            try:
                process_logger.info(f"🧹 Cleaning up staging table {cleanup_table_name} after failure...")
                drop_staging_table(snowflake_conn, cleanup_table_name, snowflake_schema)
            except Exception as cleanup_err:
                process_logger.warning(f"⚠️ Failed to cleanup staging table in exception handler: {cleanup_err}")

        # ✅ Check if this is a Salesforce API busy/rate limit error
        error_str = str(e).lower()
        is_salesforce_busy_error = any(keyword in error_str for keyword in [
            'request limit', 'rate limit', 'too many requests', '429', '503', 
            'service unavailable', 'busy', 'unable_to_lock_row'
        ])
        
        # ✅ Log migration completion with specific message if retry attempts exhausted
        if is_final_attempt and is_salesforce_busy_error:
            try:
                from utils.error_utils import insert_tool_log
                
                process_logger.warning(f"⚠️ All retry attempts exhausted for table '{mapping}' - Salesforce API busy/rate limited")
                
                # Log with specific status message using insert_tool_log
                insert_tool_log(
                    cmp_id=cmp_id,
                    connection_id=connection_id,
                    usr_id=user_id,
                    table_name=mapping,
                    status="F",  # Failed status
                    message="The Source System is currently busy. Try after sometimes",
                    session=session,
                    job_name=job_name,
                    migration_record_id=None
                )
                
                process_logger.info(f"✅ Logged retry exhaustion for table '{mapping}' with status message")
            except Exception as log_err:
                process_logger.error(f"⚠️ Failed to log retry exhaustion: {log_err}")

        # Log error using centralized error handling
        from utils.error_utils import handle_service_error, create_error_log_entry

        error_context = {
            "table_name": mapping,
            "service_name": "Salesforce to Snowflake",
            "error_type": "TABLE_MIGRATION_FAILED",
            "cmp_id": cmp_id,
            "connection_id": connection_id,
            "user_id": user_id,
            "job_name": job_name,
            "migration_session_id": migration_session_id,
            "session": session
        }

        # Log to log and files
        error_result = handle_service_error(
            error=e,
            context=error_context,
            log_files=log_files if logs_creation and 'log_files' in locals() and log_files else None,
            send_email=False  # Email handled separately via failure tracker
        )

        if error_result.get("tool_log_logged", error_result.get("log_logged", False)):
            process_logger.info(f"✅ Error logged to tool_log for table: {mapping}")
        if error_result["files_logged"]:
            process_logger.info(f"📄 Error logged to files for table: {mapping}")

        # Add table failure to tracker for consolidated email at end of migration
        table_name = mapping
        error_details = f"Error: {str(e)}\n\nTraceback:\n{tb_module.format_exc()}"
        if failure_tracker:
            failure_tracker.add_failure(table_name, error_details, "Salesforce to Snowflake")
            process_logger.info(f"📝 Added table failure to migration tracker: {table_name}")
        else:
            process_logger.warning(f"⚠️ No failure tracker provided - table failure not tracked: {table_name}")

        # CRITICAL FIX: Only mark as FAILED on the final attempt.
        # Intermediate failures in the retry loop should NOT set status to 'failed'
        # to prevent UI flickering and premature error states.
        if is_final_attempt:
            update_table_status_snowflake(connection_id, mapping, "failed", job_name, migration_session_id)
        else:
            process_logger.warning(f"⚠️ Intermediate failure for {mapping} - NOT updating status to FAILED (waits for final attempt)")

        raise

