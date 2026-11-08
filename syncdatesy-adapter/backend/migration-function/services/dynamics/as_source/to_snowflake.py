from fastapi import FastAPI, HTTPException, Request,APIRouter
from pydantic import BaseModel
from typing import List, Dict
import json
import logging
import hashlib
import tempfile
import traceback
import pytz
from datetime import datetime, timezone
import os

for _noisy_logger in (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.storage",
    "azure.storage.blob",
    "azure.identity",
    "snowflake.connector",
    "urllib3",
):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Import the required libraries
from azure.storage.blob import BlobServiceClient
import pandas as pd
import json
import re
import pytz
import time
from sqlalchemy import create_engine, types, text
from sqlalchemy import create_engine, Table, Column, MetaData,Integer, Float, String,Boolean,Text
from sqlalchemy.dialects import *
from sqlalchemy.exc import IntegrityError
from snowflake.sqlalchemy import TIMESTAMP_TZ
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine.url import make_url
import snowflake.connector
import configparser
from snowflake.connector.errors import Error
import adlfs
from sqlalchemy.exc import ProgrammingError
import psycopg2
from sqlalchemy.exc import ProgrammingError

from google.cloud import storage, firestore
import os# Initialize FastAPI
# app = FastAPI()
# router = APIRouter()

def _wait_for_gce_metadata(timeout_secs: float = 45.0) -> bool:
    import urllib.request

    url = "http://metadata.google.internal/computeMetadata/v1/"
    deadline = time.time() + max(1.0, float(timeout_secs))
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
            urllib.request.urlopen(req, timeout=2)
            return True
        except Exception:
            time.sleep(1.5)
    return False


def _build_gcs_storage_client():
    """Use ADC (Workload Identity on GKE); wait for metadata on freshly scaled nodes."""
    os.environ.setdefault("GOOGLE_CLOUD_UNIVERSE_DOMAIN", "googleapis.com")
    project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    _wait_for_gce_metadata(timeout_secs=45)
    last_err = None
    for _ in range(5):
        try:
            return storage.Client(project=project_id) if project_id else storage.Client()
        except Exception as exc:
            last_err = exc
            time.sleep(2)
    logging.warning("GCS_CLIENT_ADC_RETRY_EXHAUSTED | err=%s", last_err)
    return storage.Client(project=project_id) if project_id else storage.Client()


class _LazyStorageClient:
    """Build the GCS client on first use so module import does not race metadata."""

    def __init__(self):
        self._client = None

    def _get(self):
        if self._client is None:
            self._client = _build_gcs_storage_client()
        return self._client

    def __getattr__(self, name):
        return getattr(self._get(), name)


storage_client = _LazyStorageClient()
try:
    firestore_client = firestore.Client()
except Exception as exc:
    logging.warning("FIRESTORE_CLIENT_INIT_DEFERRED | err=%s", exc)
    firestore_client = None

from models.microsoft_dynamics_models import MigrationRequest

# Import cleanup utility
from utils.celery_cleanup_utils import handle_migration_completion
from utils.error_utils import insert_tool_log, log_migration_completion, log_migration_failure
from utils.azure_path_utils import (
    infer_dataverse_base_folder_from_paths,
    join_azure_blob_path,
    first_date_directory_in_blob_path,
    list_dataverse_date_folder_names,
    dataverse_csv_processing_order_key,
    is_dataverse_delete_export_csv,
)
from services.dynamics.source_common import list_table_csv_blobs, take_flush_slice
# Import unified data_transfer_logs function
from utils.database_utils import data_transfer_logs, postgres_connection
# Import staging table cleanup utility
from utils.snowflake_utils import (
    drop_staging_table,
    drop_existing_ds_staging_tables_for_table,
    lookup_session_staging_name,
    remember_session_staging_name,
    forget_session_staging_name,
    touch_session_staging_name,
)
# Import progress tracking utility
from utils.simple_progress_tracker import SimpleProgressTracker
# Import shared GCS utilities
from utils.gcp_utils import delete_blob_from_bucket

# =============================================================================
# GLOBAL CONSTANTS - Microsoft Dynamics Migration Service
# =============================================================================

# Snowflake/GCP configuration (centralized via environment variables)
SNOWFLAKE_STAGE = os.environ["SNOWFLAKE_STAGE"]
DEMO_STORAGE = os.environ["SNOWFLAKE_STORAGE_INTEGRATION"]
GCP_SERVICE_ACCOUNT = os.environ["GCP_SERVICE_ACCOUNT_EMAIL"]
GCS_BUCKET_URL = os.environ["GCS_BUCKET_URL"]
BUCKET_NAME = os.environ["GCS_BUCKET_NAME"]
PROJECT_ID = os.environ["GCP_PROJECT_ID"]


def _migration_verbose_logs():
    return os.environ.get("MIGRATION_VERBOSE_LOGS", "0").strip().lower() in (
        "1", "true", "on", "yes", "enabled",
    )


def _vprint(*args, **kwargs):
    """Expensive debug prints; enable with MIGRATION_VERBOSE_LOGS=1."""
    if _migration_verbose_logs():
        print(*args, **kwargs)


def _staging_count_sql(snowflake_schema: str, staging_table: str) -> str:
    schema = str(snowflake_schema or "").strip().upper()
    staging = str(staging_table or "").strip().upper()
    return f'SELECT COUNT(*) FROM {schema}."{staging}"'


def _sum_copy_rows_loaded(copy_rows) -> int:
    """Sum rows_loaded from Snowflake COPY INTO result set."""
    total = 0
    for row in copy_rows or []:
        payload = _snowflake_row_to_dict(row)
        loaded = payload.get("rows_loaded")
        if loaded is None:
            vals = list(payload.values())
            if len(vals) >= 4:
                loaded = vals[3]
        try:
            if loaded is not None:
                total += int(loaded)
        except (TypeError, ValueError):
            continue
    return total


def _count_staging_table_rows(destination_engine, snowflake_schema, staging_table_name):
    """Return row count in the per-job staging table, or None if the check fails."""
    if not destination_engine or not snowflake_schema or not staging_table_name:
        return None
    schema = snowflake_schema.strip().upper()
    staging = staging_table_name.strip().upper()
    try:
        with destination_engine.connect() as conn:
            result = conn.execute(text(f'SELECT COUNT(*) FROM {schema}."{staging}"'))
            return int(result.fetchone()[0])
    except Exception as exc:
        logger.warning(
            "[MERGE] staging row count failed schema=%s staging=%s error=%s",
            schema, staging, exc,
        )
        return None


def _merge_schema_df_from_staging(destination_engine, snowflake_schema, staging_table_name):
    """
    Build an empty DataFrame whose columns match the Snowflake staging table.
    Used when merge must run but the in-memory schema df was lost (e.g. last chunk
    only buffered rows without calling table_creation).
    """
    if not destination_engine or not snowflake_schema or not staging_table_name:
        return pd.DataFrame()
    schema = snowflake_schema.strip().upper()
    staging = staging_table_name.strip().upper()
    try:
        with destination_engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = '{schema}'
                      AND table_name = '{staging}'
                    ORDER BY ordinal_position
                    """
                )
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(columns=[r[0] for r in rows])
    except Exception as exc:
        logger.warning(
            "[MERGE] staging column lookup failed schema=%s staging=%s error=%s",
            schema, staging, exc,
        )
        return pd.DataFrame()


def _remember_merge_schema_df(current_df, candidate_df):
    """Keep the latest non-empty column schema df for the end-of-table MERGE."""
    if candidate_df is None or not hasattr(candidate_df, "columns"):
        return current_df
    if candidate_df.columns.empty:
        return current_df
    return candidate_df


def _migration_table_detail_log_dir():
    """
    Per-table migration detail files (text) for troubleshooting.
    Enable with MIGRATION_TABLE_DETAIL_LOG=1|true|on|yes|enabled.
    Override directory with MIGRATION_TABLE_LOG_DIR (created if missing).
    Default log root: <temp>/ds_migration_table_logs

    When MIGRATION_TABLE_DETAIL_LOG_GCS is enabled (default: on whenever detail
    logging is on), the completed file is uploaded under:

      gs://<bucket>/<MIGRATION_TABLE_LOG_GCS_PREFIX>/<YYYY-MM-DD>/<table>/<run_ts>/...

    See _migration_table_log_gcs_object_name().
    """
    flag = os.environ.get("MIGRATION_TABLE_DETAIL_LOG", "0").strip().lower()
    if flag not in ("1", "true", "on", "yes", "enabled"):
        return None
    explicit = os.environ.get("MIGRATION_TABLE_LOG_DIR", "").strip()
    if explicit:
        return explicit
    return os.path.join(tempfile.gettempdir(), "ds_migration_table_logs")


def _migration_table_log_gcs_enabled():
    """
    Upload detail log file to GCS on close when True.

    Explicit ops toggle (recommended in K8s):
      MIGRATION_TABLE_LOG_BUCKET_WRITE=true|false

    When unset, falls back to MIGRATION_TABLE_DETAIL_LOG_GCS (default on when detail logging runs).
    """
    bw = os.environ.get("MIGRATION_TABLE_LOG_BUCKET_WRITE", "").strip().lower()
    if bw in ("1", "true", "on", "yes", "enabled"):
        return True
    if bw in ("0", "false", "off", "no", "disabled"):
        return False
    raw = os.environ.get("MIGRATION_TABLE_DETAIL_LOG_GCS", "1").strip().lower()
    return raw not in ("0", "false", "off", "no", "disabled")


def _migration_table_log_gcs_bucket_name():
    return os.environ.get("MIGRATION_TABLE_LOG_GCS_BUCKET", "").strip() or BUCKET_NAME


def _migration_table_log_gcs_object_name(
    table_name,
    connection_id,
    migration_session_id,
    cmp_id,
    job_digest,
    now_utc,
    run_ts_slug,
):
    """
    GCS object key (POSIX-style, no leading slash):

      <prefix>/<YYYY-MM-DD>/<safe_table>/<run_ts_slug>/<file>.log

    Set MIGRATION_TABLE_LOG_GCS_SKIP_TABLE_FOLDER=1 to omit the <safe_table> segment.
    Prefix defaults to migration_table_detail_logs (override with MIGRATION_TABLE_LOG_GCS_PREFIX).
    """
    prefix = os.environ.get("MIGRATION_TABLE_LOG_GCS_PREFIX", "migration_table_detail_logs").strip().strip("/")
    date_folder = now_utc.strftime("%Y-%m-%d")
    safe_table = re.sub(r"[^\w\-.]+", "_", str(table_name))[:120]
    skip_table = os.environ.get("MIGRATION_TABLE_LOG_GCS_SKIP_TABLE_FOLDER", "0").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    parts = [p for p in (prefix, date_folder) if p]
    if not skip_table:
        parts.append(safe_table)
    parts.append(run_ts_slug)
    dir_key = "/".join(parts)
    fname = f"detail_conn{connection_id}_sess{migration_session_id}_cmp{cmp_id}_{job_digest}.log"
    return f"{dir_key}/{fname}"


def _upload_table_migration_log_to_gcs(local_path: str, object_name: str) -> bool:
    """Upload a finished local detail log to GCS (best-effort)."""
    try:
        from google.api_core.retry import Retry

        retry = Retry(initial=1.0, maximum=16.0, multiplier=2.0, deadline=120.0)
        bucket_name = _migration_table_log_gcs_bucket_name()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        blob.upload_from_filename(local_path, content_type="text/plain; charset=utf-8", retry=retry)
        logger.warning("TABLE_MIGRATION_DETAIL_GCS | gs://%s/%s", bucket_name, object_name)
        return True
    except Exception as e:
        logger.error("TABLE_MIGRATION_DETAIL_GCS_FAILED | object=%s err=%s", object_name, e)
        return False


class _TableMigrationDetailFileLog:
    """Append-only UTF-8 text log for a single process_table run (all tables when enabled)."""

    __slots__ = ("_fp", "path", "_chunk_lines", "_chunk_cap", "_gcs_object_name", "_upload_gcs")

    def __init__(self, table_name, connection_id, migration_session_id, job_name, cmp_id):
        self._fp = None
        self.path = None
        self._chunk_lines = 0
        self._gcs_object_name = None
        self._upload_gcs = False
        try:
            self._chunk_cap = max(0, int(os.environ.get("MIGRATION_TABLE_LOG_MAX_CHUNK_LINES", "500")))
        except ValueError:
            self._chunk_cap = 500
        base = _migration_table_detail_log_dir()
        if not base:
            return
        try:
            os.makedirs(base, exist_ok=True)
        except OSError:
            return
        job_digest = hashlib.sha256((job_name or "").encode("utf-8", errors="replace")).hexdigest()[:16]
        now_utc = datetime.now(timezone.utc)
        run_ts_slug = now_utc.strftime("%Y%m%d_%H%M%S_%f")
        safe_table = re.sub(r"[^\w\-.]+", "_", str(table_name))[:120]
        fname = f"{safe_table}_conn{connection_id}_sess{migration_session_id}_cmp{cmp_id}_{job_digest}_{run_ts_slug}.log"
        self.path = os.path.join(base, fname)
        if _migration_table_log_gcs_enabled():
            self._gcs_object_name = _migration_table_log_gcs_object_name(
                table_name,
                connection_id,
                migration_session_id,
                cmp_id,
                job_digest,
                now_utc,
                run_ts_slug,
            )
            self._upload_gcs = True
        try:
            self._fp = open(self.path, "a", encoding="utf-8", buffering=1)
        except OSError:
            self.path = None
            self._fp = None
            self._gcs_object_name = None
            self._upload_gcs = False
            return
        bucket_for_msg = _migration_table_log_gcs_bucket_name() if self._upload_gcs and self._gcs_object_name else ""
        gcs_uri = (
            f"gs://{bucket_for_msg}/{self._gcs_object_name}"
            if self._upload_gcs and self._gcs_object_name and bucket_for_msg
            else ""
        )
        host = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or ""
        pod = os.environ.get("POD_NAME", "")
        self._write_raw(
            "=== migration table detail log ===\n"
            f"table_name={table_name!r}\n"
            f"connection_id={connection_id}\n"
            f"migration_session_id={migration_session_id}\n"
            f"cmp_id={cmp_id}\n"
            f"job_name={job_name!r}\n"
            f"host={host!r} pid={os.getpid()} pod_name={pod!r}\n"
            f"log_file_local={self.path!r}\n"
            f"gcs_upload_on_close={self._upload_gcs}\n"
            f"gcs_uri={gcs_uri!r}\n"
            "---\n"
        )

    def enabled(self):
        return self._fp is not None

    def gcs_target_uri(self):
        """Destination gs:// URI after close() upload, or None if GCS upload disabled."""
        if not self._upload_gcs or not self._gcs_object_name:
            return None
        return f"gs://{_migration_table_log_gcs_bucket_name()}/{self._gcs_object_name}"

    def _write_raw(self, text):
        if not self._fp:
            return
        self._fp.write(text)
        if not text.endswith("\n"):
            self._fp.write("\n")
        self._fp.flush()

    def log(self, msg):
        if not self._fp:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%fZ")
        self._write_raw(f"[{ts}] {msg}")

    def log_chunk(self, msg):
        if not self._fp or self._chunk_cap == 0:
            return
        if self._chunk_lines >= self._chunk_cap:
            if self._chunk_lines == self._chunk_cap:
                self.log(
                    f"(suppressing further chunk lines; cap={self._chunk_cap} "
                    f"set MIGRATION_TABLE_LOG_MAX_CHUNK_LINES to raise)"
                )
                self._chunk_lines += 1
            return
        self.log(msg)
        self._chunk_lines += 1

    def close(self):
        local_path = self.path
        if self._fp:
            try:
                self._fp.close()
            finally:
                self._fp = None
        if not local_path or not os.path.isfile(local_path):
            return
        if self._upload_gcs and self._gcs_object_name:
            ok = _upload_table_migration_log_to_gcs(local_path, self._gcs_object_name)
            if ok and os.environ.get("MIGRATION_TABLE_LOG_DELETE_LOCAL_AFTER_GCS", "1").strip().lower() not in (
                "0",
                "false",
                "off",
                "no",
            ):
                try:
                    os.unlink(local_path)
                except OSError:
                    pass





async def migrate(request: MigrationRequest):
    """
    FastAPI endpoint to process Dynamics data.
    """
    start_time = time.time()
    
    # Parse inputs from the request body
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
    chunk_count=request.chunk_count
    drop_and_reload = request.drop_and_reload  # Flag for drop and reload operation
    log_id = request.log_id if hasattr(request, 'log_id') else None # ✅ Extract log_id
    
    # Verbose request diagnostics (enable with MIGRATION_VERBOSE_LOGS=1)
    request_dict = request.dict()
    _vprint("=" * 80)
    _vprint("DEBUG RAW REQUEST fields:", list(request_dict.keys()))
    _vprint("drop_and_reload:", request_dict.get("drop_and_reload", "NOT FOUND"))
    _vprint("drop_and_reload flag:", drop_and_reload, "type:", type(drop_and_reload))
    _vprint("selected_tables:", selected_tables)
    _vprint("=" * 80)
    if celery_child_mode:
        logger.info("CELERY CHILD MODE: forcing sequential execution")

    try:
        # Validate required parameters
        if not container_name or not connection_string or not selected_tables:
            raise ValueError("Missing required parameters: container_name, connection_string, or selected_tables")

        # Get last checked dates and fields
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
            table_fields[table_name] = data.get("table_fields", None)

        for table_name in selected_tables:
            _vprint("Table:", table_name, "last_checked:", last_checked_dates.get(table_name))

        # Run the main processing function
        aggregated_results = main(
            connection_string, container_name, selected_tables,
            connection_id, cmp_id, usr_id, session, updated_at, deleted_at,
            destination_connection, safety_interval, last_checked_dates, job_name,
            migration_session_id, table_fields,last_checked_datedirectory,execution_order,chunk_count,drop_and_reload
        )

        # Initialize counters and collect last_checked_dates
        total_rows = 0
        total_inserts = 0
        total_updates = 0
        total_delete_recids = 0
        column_size = 0
        last_checked_date = {}
        
        # Track successful and failed tables
        successful_tables = []
        failed_tables = []

        for table_name, result in aggregated_results:
            if result is not None:
                table_total_rows, table_total_inserts, table_total_updates, table_total_deletes, table_column_size, table_last_checked_date = result
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
        # In fan-out child mode, skip the job-level completion log here. Each child
        # only knows about its own table subset, so its counts/rows would be partial
        # and racing children would overwrite each other in the DB. The Celery
        # finalizer aggregates across all children and writes one correct message.
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
                log_id=log_id, # ✅ Pass log_id for stable updates
                include_operation_counts=False  # Exclude operation counts to match Salesforce format
            )
        else:
            logger.info(
                "CHILD_MODE: skipping per-child completion log job=%s tables=%s/%s rows=%s",
                job_name, len(successful_tables), len(selected_tables), total_rows,
            )
        
        # Clean up other stuck celery jobs for this connection (not in per-job child mode;
        # the control finalizer owns lifecycle there).
        if not celery_child_mode:
            try:
                connection_name = request.connection_name if request.connection_name else f"DYNAMICS_CONNECTION_{connection_id}"

                cleanup_result = handle_migration_completion(
                    connection_name=connection_name,
                    job_name=job_name,
                    completion_message=None
                )

                if cleanup_result['status'] == 'success' and cleanup_result['cleaned_jobs']:
                    logger.info(
                        "Cleaned up %s stuck celery jobs for %s",
                        len(cleanup_result['cleaned_jobs']), connection_name,
                    )
                elif cleanup_result['status'] == 'error':
                    logger.warning("Cleanup warning for %s: %s", connection_name, cleanup_result['message'])

            except Exception as cleanup_error:
                logger.warning("Failed to cleanup celery jobs: %s", cleanup_error)
                import traceback
                traceback.print_exc()

        # Construct the response
        return {
            "status_code": 200,
            "total_rows": total_rows,
            "total_inserts": total_inserts,
            "total_updates": total_updates,
            "total_deleted_recids": total_delete_recids,
            "column_size": column_size,
            "last_checked_date": last_checked_date,
            # Aggregation-friendly fields consumed by the Celery fan-in finalizer.
            "execution_time": execution_time,
            "total_tables": len(selected_tables),
            "successful_tables_count": len(successful_tables),
            "failed_tables_count": len(failed_tables),
            "successful_tables_list": successful_tables,
            "failed_tables_list": failed_tables,
        }

    except Exception as e:
        try:
            from utils.connection_progress_utils import clear_connection_retrieval_progress
            clear_connection_retrieval_progress(connection_id)
        except Exception:
            pass
        raise e



def connect_to_blob_storage(connection_string, container_name):
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service_client.get_container_client(container_name)
    _vprint("completed connect to blob")
    return container_client

def postgres_connection():
    from datasyncher_connections.postgres import create_postgres_connection

    connection = create_postgres_connection()
    _vprint("connection", connection)
    return connection


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

# insert_tool_log is now imported from utils.error_utils (centralized utility with race condition protection)

import ast



def parse_table_fields(table_fields):
    """
    Handles parsing of `table_fields` to ensure a consistent format.

    Parameters:
    - table_fields: The raw input, which may be a list, dictionary, single-serialized JSON string,
      or double-serialized JSON string.

    Returns:
    - A parsed list, dictionary, or None if parsing fails.
    """
    # If already a list, return it directly
    if isinstance(table_fields, list):
        return table_fields

    # If already a dictionary, return it directly
    if isinstance(table_fields, dict):
        return table_fields

    # If it's a string, attempt parsing
    if isinstance(table_fields, str):
        try:
            # First attempt to parse as single-serialized JSON
            parsed_fields = json.loads(table_fields)
            if isinstance(parsed_fields, (list, dict)):
                return parsed_fields
        except json.JSONDecodeError:
            pass  # Continue to try double-serialized format

        try:
            # Handle double-serialized JSON
            parsed_fields = json.loads(json.loads(table_fields))
            if isinstance(parsed_fields, (list, dict)):
                return parsed_fields
        except json.JSONDecodeError:
            pass  # Fall through to `ast.literal_eval` as a last resort

        try:
            # Use `ast.literal_eval` to handle non-standard serialized lists or dictionaries
            parsed_fields = ast.literal_eval(table_fields)
            if isinstance(parsed_fields, (list, dict)):
                return parsed_fields
        except (ValueError, SyntaxError):
            pass

    # Return None if parsing fails
    return None



def _connection_last_synced_folder_cursor(connection_id):
    """Fallback blob-list prefix when date_directory has no cached folders yet."""
    conn = None
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_synced FROM connection WHERE connection_id = %s;",
            (connection_id,),
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return None
        value = str(row[0]).strip().strip('"')
        if not value or value.lower() == "null":
            return None
        return value
    except Exception as exc:
        print(f"[WARN] Could not read connection.last_synced for folder cursor: {exc}")
        return None
    finally:
        if conn:
            conn.close()


def get_last_checked_date_directories(connection_id,cmp_id):
    """
    Fetches the last checked date directories for a given connection ID using raw SQL.

    Args:
    - connection_id (int): The ID of the connection.

    Returns:
    - The maximum (latest) date directory, or None if no directories exist.
    """
    try:
        _vprint(f"[DEBUG] Fetching directories for connection_id: {connection_id}")

        # Connect to PostgreSQL
        conn = postgres_connection()
        cursor = conn.cursor()

        # Define SQL query
        sql_query = """
        SELECT directories
        FROM date_directory
        WHERE connection_id = %s;
        """

        # Execute query
        cursor.execute(sql_query, (connection_id,))
        row = cursor.fetchone()  # Fetch single row
        _vprint(f"[DEBUG] Fetched row: {row}")  # Debugging fetched data

        # If no row is found, return None
        if not row:
            _vprint("[DEBUG] No directories found for this connection.")
            cursor.close()
            conn.close()
            return None

        # Extract directories and check its type
        directories_data = row[0]
        _vprint(f"[DEBUG] Type of directories_data: {type(directories_data)}")

        # If it's a string, convert it from JSON
        if isinstance(directories_data, str):
            try:
                date_directories = json.loads(directories_data)
            except json.JSONDecodeError as e:
                print(f"[ERROR] Failed to decode JSON: {str(e)}")
                cursor.close()
                conn.close()
                return None
        elif isinstance(directories_data, list):
            date_directories = directories_data  # Already a list, no need to parse
        else:
            print(f"[ERROR] Unexpected data type: {type(directories_data)}")
            cursor.close()
            conn.close()
            return None

        _vprint(f"[DEBUG] Extracted directories before sorting: {date_directories}")

        # Sort the directories (convert string to datetime for sorting)
        date_directories.sort(key=lambda x: datetime.strptime(x, "%Y-%m-%dT%H.%M.%SZ"))

        # Get the latest date if available
        max_date = date_directories[-1] if date_directories else None
        _vprint(f"[DEBUG] Max date found: {max_date}")

        # Close connection
        cursor.close()
        conn.close()

        return max_date  # Return latest date

    except Exception as e:
        print(f"[ERROR] Error fetching date directories: {str(e)}")
        message=f"[ERROR] Error fetching date directories: {str(e)}"
        insert_error_log(cmp_id, connection_id,message, "get_last_checked_date_directories")
        return None  # Return None on error


def get_all_date_directories_from_db(connection_id, cmp_id=0):
    """
    Return the full sorted list stored in date_directory.directories for a connection.
    Same row/query as get_last_checked_date_directories, but returns every folder name.
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT directories
            FROM date_directory
            WHERE connection_id = %s;
            """,
            (connection_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return []

        directories_data = row[0]
        if isinstance(directories_data, str):
            date_directories = json.loads(directories_data)
        elif isinstance(directories_data, list):
            date_directories = directories_data
        else:
            return []

        date_directories = [d for d in date_directories if d]
        date_directories.sort(key=lambda x: datetime.strptime(x, "%Y-%m-%dT%H.%M.%SZ"))
        return date_directories
    except Exception as e:
        print(f"[ERROR] Error fetching all date directories: {str(e)}")
        message = f"[ERROR] Error fetching all date directories: {str(e)}"
        insert_error_log(cmp_id, connection_id, message, "get_all_date_directories_from_db")
        return []





def insert_or_update_date_directories(connection_id, new_directories):
    """
    Inserts or updates the date directories for a given connection ID.
    
    Args:
        connection_id (int): The ID of the connection.
        new_directories (list): List of new date directories to add.
    
    Returns:
        str: Success or failure message.
    """

    # try:
    _vprint(f"[DEBUG] Connecting to PostgreSQL for connection_id: {connection_id}")
    # Connect to PostgreSQL
    conn = postgres_connection()
    cursor = conn.cursor()
    _vprint("[DEBUG] Connected to database successfully")
    # Fetch existing directories
    cursor.execute("""
        SELECT directories FROM date_directory WHERE connection_id = %s;
    """, (connection_id,))
    
    row = cursor.fetchone()
    _vprint(f"[DEBUG] Retrieved row: {row}")  # Debug fetched data
    if row:
        _vprint(f"[DEBUG] Type of row[0]: {type(row[0])}")  # Check data type before json.loads()
    
    existing_directories = row[0] if row and row[0] else []
    _vprint(f"[DEBUG] Existing directories: {existing_directories}")
    # Ensure new_directories is a list
    if not isinstance(new_directories, list):
        print("[ERROR] new_directories is not a list!")
        return "Error: new_directories must be a list."
    # Merge existing and new directories (avoiding duplicates)
    updated_directories = list(set(existing_directories + new_directories))
    _vprint(f"[DEBUG] Merged directories before saving: {updated_directories}")
    # Convert updated list to JSON format
    updated_json = json.dumps(updated_directories)  # ✅ Ensure JSON conversion is correct
    _vprint(f"[DEBUG] JSON to be stored: {updated_json}")
    # Insert or update the record
    if row:
        cursor.execute("""
            UPDATE date_directory 
            SET directories = %s, updated_at = %s 
            WHERE connection_id = %s;
        """, (updated_json, datetime.utcnow(), connection_id))
        _vprint(f"[DEBUG] Updated record for connection_id: {connection_id}")
    else:
        cursor.execute("""
            INSERT INTO date_directory (connection_id, directories, created_at, updated_at)
            VALUES (%s, %s, %s, %s);
        """, (connection_id, updated_json, datetime.utcnow(), datetime.utcnow()))
        _vprint(f"[DEBUG] Inserted new record for connection_id: {connection_id}")
    # Commit changes and close the connection
    conn.commit()
    cursor.close()
    conn.close()
    _vprint(f"[DEBUG] Final date directories: {updated_directories}")
    return sorted(updated_directories)

    # except Exception as e:
    #     print(f"[ERROR] Exception occurred: {str(e)}")
    #     return f"Error updating date directories: {str(e)}"


def get_last_checked_dates_and_fields(connection_id, selected_tables=None):
    """
    Fetches the last checked dates and table fields for specific tables associated with a given connection ID.

    Parameters:
    - connection_id: The ID of the connection.
    - selected_tables: A list of table names to filter the query. If None, fetches all tables.

    Returns:
    - A dictionary in the format {table_name: {"last_checked_date": last_checked_date, "table_fields": table_fields}}.
    """
    try:
        # Connect to the PostgreSQL database
        conn = postgres_connection()
        cursor = conn.cursor()
        
        # Prepare the base query
        base_query = """
        SELECT "table", last_synced, table_fields 
        FROM source_table_selection
        WHERE connection_id = %s
        """
        
        # Add filtering for selected tables if provided
        if selected_tables:
            placeholders = ', '.join(['%s'] * len(selected_tables))
            base_query += f" AND \"table\" IN ({placeholders})"
            query_params = [connection_id] + selected_tables
        else:
            query_params = [connection_id]
        
        # Execute the query
        cursor.execute(base_query, tuple(query_params))
        rows = cursor.fetchall()
        
        # Convert results into a dictionary
        last_checked_data = {}
        for row in rows:
            table_name = row[0]
            
            last_checked_date = row[1] if row[1] else None
            table_fields = row[2] if row[2] else None

            # # Parse `table_fields` into a consistent list format
            parsed_fields = parse_table_fields(table_fields)

            # Add to the dictionary
            last_checked_data[table_name] = {
                "last_checked_date": last_checked_date,
                "table_fields": parsed_fields,
            }
        
        
        return last_checked_data

    except Exception as e:
        print(f"Error fetching data for connection_id {connection_id}: {e}")
        return {}
    
    finally:
        # Close the cursor and connection
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


# data_transfer_logs() is now imported from utils.database_utils (unified function)
# Helper to List Date Directories in Blob Storage
import concurrent.futures
from datetime import datetime

from datetime import datetime

from datetime import datetime

def fetch_blob_names(container_client, prefix=None):
    """
    Fetches blob names from Azure Blob Storage with an optional prefix filter.

    Args:
        container_client: Azure Blob container client.
        prefix: Optional prefix to filter blobs (e.g., 'YYYY-MM-DD' for date grouping).

    Returns:
        A list of blob names.
    """
    blob_names = []
    blobs = container_client.list_blobs(name_starts_with=prefix) if prefix else container_client.list_blobs()

    for blob in blobs:
        blob_names.append(blob.name)

    print("Blob names fetching completed:", len(blob_names))
    return blob_names


import re
from datetime import datetime

# def list_date_directories(container_client, safety_interval_cutoff=None, prefix=None):
#     """
#     Lists all date directories that are:
#     - Greater than last_directory
#     - Less than safety_interval_cutoff
#     - (Optional) Uses a prefix to optimize API calls

#     Args:
#         container_client: Azure Blob container client.
#         safety_interval_cutoff: The maximum date limit ('YYYY-MM-DDTHH.MM.SSZ').
#         prefix: Optional prefix to optimize API calls (e.g., 'YYYY-MM').

#     Returns:
#         Sorted list of date directories matching the criteria.
#     """
#     date_directories = set()

#     # Fetch blobs using prefix if provided
#     blob_names = container_client.list_blobs(name_starts_with=prefix) if prefix else container_client.list_blobs()

#     date_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.\d{2}Z$"

#     for blob in blob_names:
#         folder = blob.name.split('/')[0]  # Extract first-level directory

#         # Ensure folder is a string and matches the expected format
#         if isinstance(folder, str) and re.match(date_pattern, folder):
#             try:
#                 folder_date = datetime.strptime(folder, "%Y-%m-%dT%H.%M.%SZ")  # Convert to datetime

#                 # Check safety_interval_cutoff condition
#                 if safety_interval_cutoff:
#                     safety_cutoff_date = datetime.strptime(safety_interval_cutoff, "%Y-%m-%dT%H.%M.%SZ")
#                     if folder_date >= safety_cutoff_date:
#                         continue  # Skip directories beyond cutoff

#                 date_directories.add(folder)  # ✅ Add folder as string for consistency

#             except ValueError as e:
#                 print(f"Skipping invalid date format: {folder} - Error: {str(e)}")

#         else:
#             print(f"Skipping non-date folder: {folder}")

#     return sorted(date_directories)  # ✅ Moved outside the loop for correct return
# import re
# import multiprocessing
# from datetime import datetime, timedelta
# from azure.storage.blob import ContainerClient



# def fetch_blobs_for_prefix(container_client, prefix, safety_interval_cutoff):
#     """
#     Fetches blobs for a given prefix and filters directories.
#     Used in multiprocessing to speed up fetching.

#     Args:
#         container_client: Azure Blob container client.
#         prefix: The prefix (YYYY-MM) to filter blobs.
#         safety_interval_cutoff: The max date limit ('YYYY-MM-DDTHH.MM.SSZ').

#     Returns:
#         A set of valid date directories.
#     """
#     date_directories = set()
#     date_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.\d{2}Z$"

#     # Fetch blobs (No while loop needed!)
#     blob_list = container_client.list_blobs(name_starts_with=prefix)

#     for blob in blob_list:
#         folder = blob.name.split('/')[0]  # Extract first-level directory
        
#         if isinstance(folder, str) and re.match(date_pattern, folder):
#             try:
#                 folder_date = datetime.strptime(str(folder), "%Y-%m-%dT%H.%M.%SZ")
                
#                 if safety_interval_cutoff:
#                     safety_cutoff_date = datetime.strptime(str(safety_interval_cutoff), "%Y-%m-%dT%H.%M.%SZ")
#                     if folder_date >= safety_cutoff_date:
#                         continue  # Skip directories beyond cutoff
                
#                 date_directories.add(folder)  # ✅ Add valid date folder
            
#             except ValueError:
#                 continue
#     print("length of datedirectories",len(date_directories))
#     return date_directories


import re
import os
from datetime import datetime, timedelta
from azure.storage.blob import ContainerClient

def fetch_blobs_for_prefix(container_client, prefix, safety_interval_cutoff,cmp_id,connection_id, base_folder=None):
    """Discover date directories via delimiter listing, with blob-iteration fallback."""
    date_directories = set()
    use_delimiter = os.environ.get("LISTING_USE_DELIMITER_SCAN", "true").strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )

    _vprint(
        f"[Process {os.getpid()}] Fetching date folders prefix={prefix if prefix else 'ALL'} "
        f"base_folder={base_folder!r} delimiter={use_delimiter}"
    )

    if use_delimiter:
        try:
            date_directories = set(
                list_dataverse_date_folder_names(
                    container_client,
                    base_folder=base_folder,
                    name_prefix=prefix,
                )
            )
            _vprint(
                f"[Process {os.getpid()}] Delimiter listing unique_dirs={len(date_directories)}"
            )
        except Exception as exc:
            print(f"[WARN] Delimiter listing failed, falling back to blob iteration: {exc}")

    if not date_directories:
        if prefix:
            list_prefix = join_azure_blob_path(base_folder, prefix)
        elif base_folder:
            list_prefix = join_azure_blob_path(base_folder)
        else:
            list_prefix = None

        blob_iter = (
            container_client.list_blobs(name_starts_with=list_prefix)
            if list_prefix
            else container_client.list_blobs()
        )
        for blob in blob_iter:
            folder = first_date_directory_in_blob_path(blob.name)
            if not folder or not is_date_format(folder):
                continue
            try:
                folder_date = datetime.strptime(folder, "%Y-%m-%dT%H.%M.%SZ")
                if not safety_interval_cutoff or folder_date < safety_interval_cutoff:
                    date_directories.add(folder)
                    _vprint(f"[Process {os.getpid()}] Added folder: {folder}")
            except ValueError as e:
                print(f"[Process {os.getpid()}] Date parsing error: {e}, Folder: {folder}")
                message=f"[Process {os.getpid()}] Date parsing error: {e}, Folder: {folder}"
                insert_error_log(cmp_id, connection_id,message, "fetch_blobs_for_prefix")

    elif safety_interval_cutoff:
        filtered = set()
        for folder in date_directories:
            try:
                folder_date = datetime.strptime(folder, "%Y-%m-%dT%H.%M.%SZ")
                if folder_date < safety_interval_cutoff:
                    filtered.add(folder)
            except ValueError:
                continue
        date_directories = filtered

    return date_directories

def list_date_directories(container_client,cmp_id,connection_id, safety_interval_cutoff=None, prefix=None, base_folder=None):
    """
    Lists all date directories that are:
    - Greater than the prefix (if provided).
    - Less than safety_interval_cutoff.
    - Optimized using multiprocessing for faster execution.

    Args:
        container_client: Azure Blob container client.
        safety_interval_cutoff: The maximum date limit ('YYYY-MM-DDTHH:MM:SSZ').
        prefix: Optional prefix to optimize API calls (e.g., 'YYYY-MM-DDTHH:MM:SSZ').
        base_folder: Dataverse org folder prefix (segments before date directories).

    Returns:
        Sorted list of date directories matching the criteria.
    """
    date_directories = set()
    current_date = datetime.utcnow().strftime("%Y-%m-%d")  # 'YYYY-MM-DD'

    if prefix:
        try:
            # Extract the date part from the prefix
            prefix_date = prefix.split('T')[0]  # Get 'YYYY-MM-DD' part
            datetime.strptime(prefix_date, "%Y-%m-%d")  # Validate format
        except ValueError:
            raise ValueError("Prefix must be a valid ISO 8601 datetime string")

        prefixes = [prefix_date]  # Start with the extracted date

        # Generate daily prefixes until the current date
        while prefix_date < current_date:
            next_day = datetime.strptime(prefix_date, "%Y-%m-%d") + timedelta(days=1)
            prefix_date = next_day.strftime("%Y-%m-%d")
            prefixes.append(prefix_date)
    else:
        prefixes = [None]  # If no prefix, fetch all blobs

    _vprint(f"[Main Process] Prefixes to process: {prefixes}")

    # Celery workers cannot safely spawn child processes. Keep this path sequential.
    results = [
        fetch_blobs_for_prefix(container_client, p, safety_interval_cutoff, cmp_id, connection_id, base_folder=base_folder)
        for p in prefixes
    ]

    # Combine results from parallel processes
    for res in results:
        date_directories.update(res)

    _vprint(f"[Main Process] Final sorted date directories: {sorted(date_directories)}")
    return sorted(date_directories)

def is_date_format(folder_name):
    try:
        datetime.strptime(folder_name, "%Y-%m-%dT%H.%M.%SZ")
        return True
    except ValueError:
        
        return False
    
from urllib.parse import quote
from sqlalchemy import create_engine
from services.snowflake.service import SnowflakeService

from sqlalchemy import create_engine
from urllib.parse import quote
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend


def _load_private_key_from_string(private_key_str, passphrase=None):
    """Load PEM private key string and convert to DER bytes for Snowflake key-pair auth."""
    from datasyncher_connections.snowflake.pem import load_private_key_der, normalize_pem_key as _normalize

    normalized = _normalize(private_key_str)
    return load_private_key_der(normalized, passphrase)


def normalize_pem_key(key_str):
    from datasyncher_connections.snowflake.pem import normalize_pem_key as _normalize_pem_key

    return _normalize_pem_key(key_str)


def destination_connection_function(destination_connection, connection_id, cmp_id):
    from datasyncher_connections.snowflake.sqlalchemy_engine import create_snowflake_sqlalchemy_engine

    try:
        engine, snowflake_schema = create_snowflake_sqlalchemy_engine(
            destination_connection,
            flavor="dynamics",
            ensure_schema=True,
            validate_version=True,
        )
        print("Connection and cursor operations successful.")
        return engine, snowflake_schema
    except Exception as e:
        print("Failed to connect:", str(e))
        message = "Failed to connect: " + str(e)
        insert_error_log(cmp_id, connection_id, message, "destination_connection_function")
        raise



def add_underscore(column_name):
    # Add underscores before numbers that follow letters
    column_name = re.sub(r'(?<=[a-zA-Z])(?=[0-9])', '_', column_name)
    # Add underscores after numbers if followed by letters
    column_name = re.sub(r'(?<=[0-9])(?=[a-zA-Z])', '_', column_name)
    # Add underscores before uppercase letters that follow lowercase letters
    column_name = re.sub(r'(?<=[a-z])([A-Z])', r'_\1', column_name)
    return column_name.upper()
def normalize_column_name(name):
    # Remove non-alphanumeric characters and convert to lowercase
    normalized_name = re.sub(r'\W+', '', name).lower()
    # Remove all underscores
    normalized_name = normalized_name.replace('_', '')
    return normalized_name



# def transformations(df, df_column_types, dtype_conversion, attribute_details,extra_fields,updated_at,deleted_at):
def transformations(df, dtype_conversion, attribute_details,extra_fields,updated_at,deleted_at):

    # Define the mapping of attribute types to Pandas-compatible types
    # Define the mapping of attribute types to Pandas-compatible types
    dtype_conversion_map = {
        'guid': 'string',
        'dateTime': 'datetime64[ns]',
        'dateTimeOffset': 'datetime64[ns]',
        'boolean': 'boolean',  # Preserves NaN handling
        'int64': 'int64',
        'string': 'string',
        'decimal': 'float64'  # Maps decimal to float64
    }

    df['IsDelete'] = df['IsDelete'].fillna(False)

    # Apply data type conversions using attribute_details before any other transformation
    for column, dtype in attribute_details.items():
        if column in df.columns:
            pandas_dtype = dtype_conversion_map.get(dtype, None)
            if pandas_dtype:
                try:
                    # Handle specific data type conversions
                    if pandas_dtype == 'datetime64[ns]':
                        # Normalize to UTC so tz-aware delete markers mix with naive CSV values.
                        df[column] = pd.to_datetime(df[column], errors='coerce', utc=True)

                    elif pandas_dtype == 'boolean':
                        # Replace NaN with False for boolean columns
                        df[column] = df[column].where(df[column].notna(), False).astype('bool')

                    elif pandas_dtype == 'int64':
                        # Replace NaN with 0 for integer columns
                        df[column] = pd.to_numeric(df[column], errors='coerce').fillna(0).astype('int64')

                    elif pandas_dtype == 'float64':
                        # Replace NaN with 0 for float columns
                        df[column] = pd.to_numeric(df[column], errors='coerce').fillna(0.0).astype(pandas_dtype)

                    else:
                        # Default conversion for other types
                        df[column] = df[column].astype(pandas_dtype)

                except Exception as e:
                    logger.warning("Error converting column %s to %s: %s", column, pandas_dtype, e)
                    
            else:
                _vprint(f"Warning: Unsupported data type '{dtype}' for column {column}. No conversion applied.")
        else:
            _vprint(f"Column {column} not found in DataFrame.")


    
    # Ensure 'IsDelete' is correctly handled (replace NaN with None)
    df['IsDelete'] = df['IsDelete'].fillna(False)


    # Remove 'updated_at' and 'deleted_at' if they exist in df.columns
    df = df.drop(columns=[col for col in [updated_at, deleted_at] if col in df.columns])



    # print(f"Received df_column_types: \n{df_column_types}")
    df.columns = df.columns.map(add_underscore)  # Apply add_underscore first
    
    # Standardize column names in df
    df.columns = df.columns.str.upper().str.strip()  # Clean up column names
    # print("Transformed column names from df_column_types: ", df_column_types['column_name'].tolist())
    
    # Ensure that all necessary columns from df_column_types exist in the DataFrame
    # missing_columns = [col for col in df_column_types['column_name'] if col not in df.columns]
    # if missing_columns:
    #     print(f"Warning: Missing columns in DataFrame: {missing_columns}")

    
    # Clean up column names (remove leading underscores, replace double underscores with a single one)
    df.columns = [col.lstrip('_').replace('__', '_') for col in df.columns]
    df.columns = df.columns.str.upper()  # Ensure all columns are uppercase

    # Remove duplicate columns
    df = df.loc[:, ~df.columns.duplicated()]

    # Ensure required columns exist before reordering
    first_three_columns = [col for col in ['ID', 'SINK_CREATED_ON', 'SINK_MODIFIED_ON'] if col in df.columns]
    new_order = first_three_columns + [col for col in df.columns if col not in first_three_columns]
    df = df[new_order]

    # Drop NaNs in df_column_types['column_name']
    # df_column_types = df_column_types.dropna(subset=['column_name'])


    # Normalize column names for mapping
    # normalized_to_original = {normalize_column_name(col): col for col in df.columns}
    # normalized_desired_column_names = [normalize_column_name(name) for name in df_column_types['column_name'].tolist()]
    # desired_to_normalized = {normalize_column_name(name): name for name in df_column_types['column_name']}

    # Create rename mapping
    # column_rename_mapping = {
    #     normalized_to_original[norm]: desired_to_normalized[norm] 
    #     for norm in normalized_to_original if norm in desired_to_normalized
    # }

    # print(f"Column rename mapping: {column_rename_mapping}")

    # Apply column renaming
    # df = df.rename(columns=column_rename_mapping, copy=False)
    # print("Columns after rename: ", df.columns)


    return df



import re
from sqlalchemy.exc import SQLAlchemyError



def check_staging_table_exists(destination_engine, table, destination_connection,cmp_id, connection_id, staging_table_name=None):
    """
    Checks whether the staging table (ds_{table_name}_staging) exists in Snowflake.
    Returns True if the staging table exists, False otherwise.
    """
    _vprint("Checking staging table for:", table)

    schema = destination_connection.get('schema')

    if not destination_engine:
        logger.warning("Destination engine is not provided for staging check")
        return False

    try:
        # Connect to Snowflake using the engine
        conn = destination_engine.connect()

        # Strict enforcement: use the passed staging table name
        staging_table = staging_table_name
        if not staging_table:
             raise ValueError(f"CRITICAL ERROR: staging_table_name is missing in check_staging_table_exists for table {table}")

        # Query to check if the staging table exists
        query = f"""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = '{schema.upper()}'
          AND table_name = '{staging_table}';
        """

        # Execute the query
        result = conn.execute(text(query)).fetchone()
        _vprint(f"Staging table '{staging_table}' existence:", result is not None)

        # Return True if the staging table exists, False otherwise
        return result is not None

    except SQLAlchemyError as e:
        logger.error("Error checking if staging table exists: %s", e)
        message=f"Error checking if staging table exists: {e}"
        insert_error_log(cmp_id, connection_id,message, "check_staging_table_exists")
        return False

    finally:
        # Ensure the connection is closed
        if 'conn' in locals() and conn:
            conn.close()


def check_table_exists_snowflake(destination_engine, table, destination_connection,connection_id,cmp_id):
    _vprint("table_name:", table)

    schema = destination_connection.get('schema')

    if not destination_engine:
        logger.warning("Destination engine is not provided for table check")
        return False

    try:
        # Connect to Snowflake using the engine
        conn = destination_engine.connect()

        # Insert underscores before uppercase letters and convert to uppercase
        table_name = re.sub(r'(?<=[a-z])([A-Z])', r'_\1', table).upper()
        table_name=add_underscore(table_name)

        # Query to check if the table exists
        query = f"""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = '{schema.upper()}'
          AND table_name = '{table_name}';
        """
        
        # Execute the query
        result = conn.execute(text(query)).fetchone()
        _vprint("Table exists check:", table_name, result is not None)
        # Return True if the table exists, False otherwise
        return result is not None

    except SQLAlchemyError as e:
        logger.error("Error checking if table exists: %s", e)
        message=f"Error checking if table exists: {e}"
        insert_error_log(cmp_id, connection_id,message, "check_table_exists_snowflake")
        return False  # You may return `None` or handle the error differently if required.

    finally:
        # Ensure the connection is closed
        if 'conn' in locals() and conn:
            conn.close()

def _mark_dataverse_delete_csv_chunk(chunk, attribute_names):
    """
    Rows in 2.csv (delete feed) may omit IsDelete or set it false; force delete semantics
    before transformations/snowflake_migration.
    """
    if chunk is None or chunk.empty:
        return chunk
    isdelete_col = None
    for name in list(attribute_names or []) + list(chunk.columns):
        if str(name).replace(" ", "").lower() == "isdelete":
            isdelete_col = name
            break
    if isdelete_col is None:
        chunk = chunk.copy()
        chunk["IsDelete"] = True
    else:
        chunk = chunk.copy()
        chunk[isdelete_col] = True
    # Prefer delete rows on ID dedupe when upserts and deletes share one buffer flush.
    for ts_col in ("SINK_MODIFIED_ON", "SinkModifiedOn", "sinkmodifiedon"):
        if ts_col in chunk.columns:
            chunk[ts_col] = pd.Timestamp.now(tz=timezone.utc)
            break
    return chunk


# Process Files in Chunks
def process_file_in_chunks(connection_string, container_name, base_path, date_dir, container_client, df, folderpath_dict, attribute_names, attribute_details,
                           table_name, destination_engine, connection_id, updated_at, deleted_at, destination_connection, table_exist,is_integration_checked,extra_fields,modified_fields,existing_fields,attributes_details_field,buffer_df,buffer_row_count,final_dir,cmp_id,chunk_count,migration_session_id=None,job_name=None, staging_table_name=None, table_detail_log=None):
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
        """Flush exact chunk_count slices so IN-lists never exceed the plan cap (150k)."""
        nonlocal buffer_df, buffer_row_count, table_exist, global_chunk_count
        nonlocal total_rows, total_inserts, total_updates, total_delete_recids
        nonlocal snowflake_schema, table_name_final, staging_table, primary_key, df_lat
        touch_session_staging_name(migration_session_id, table_name)
        while chunk_count and buffer_row_count >= chunk_count:
            start_time = time.time()
            flush_df, buffer_df = take_flush_slice(buffer_df, chunk_count)
            buffer_row_count = 0 if buffer_df is None or buffer_df.empty else len(buffer_df.index)
            data, inserts, deletes, updates, snowflake_schema, table_name_final, staging_table, primary_key, df_lat = table_creation(
                df, flush_df, attribute_names, attribute_details, table_name,
                destination_engine, connection_id, updated_at, deleted_at,
                current_utc_time, False,
                global_chunk_count, destination_connection, table_exist, is_integration_checked,
                extra_fields, modified_fields, existing_fields, attributes_details_field, cmp_id,
                migration_session_id, job_name, staging_table_name=staging_table_name,
            )
            total_rows += len(data)
            total_inserts += inserts
            total_updates += updates
            total_delete_recids += deletes
            end_time = time.time()
            _vprint(
                f"Chunk {global_chunk_count} processed in {end_time - start_time:.1f}s; "
                f"flushed_rows={len(flush_df.index)} remainder={buffer_row_count} total_rows={total_rows}"
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
    primary_key = "ID"  # Default primary key
    snowflake_schema = ""


    total_rows, total_inserts = 0, 0
    total_updates, total_delete_recids = 0, 0
    column_size = None
    # batch_size=50000
    global_chunk_count = 1
    all_chunks_empty = True  # Flag to track if all chunks are empty
    chunk_size=chunk_count-buffer_row_count
    print(f"[CHUNK] table={table_name} date_dir={date_dir} chunk_size_needed={chunk_size} buffer_row_count={buffer_row_count}")
    _tlog(
        f"enter table={table_name!r} date_dir={date_dir!r} chunk_size={chunk_size} "
        f"buffer_row_count={buffer_row_count} staging_table_name={staging_table_name!r} "
        f"migration_session_id={migration_session_id} job_name={job_name!r}"
    )

    fs = adlfs.AzureBlobFileSystem(connection_string=connection_string)
    blob_prefix, blob_names = list_table_csv_blobs(
        container_client, base_path, date_dir, table_name
    )

    # Four-digit shards first (1900, 2025), then other numeric, then single-digit (0 → 2 delete → 1 final).
    # Staging flush happens only when buffer_row_count >= chunk_count (not per delete shard or date_dir).
    blob_names = sorted(blob_names, key=dataverse_csv_processing_order_key)




    print(f"[CHUNK] table={table_name} date_dir={date_dir} csv_blobs_found={len(blob_names)}")
    if blob_names:
        _tlog(
            f"csv_list count={len(blob_names)} first={blob_names[0]!r} last={blob_names[-1]!r} "
            f"blob_prefix={blob_prefix!r}"
        )
    else:
        _tlog(f"csv_list empty blob_prefix={blob_prefix!r}")

    _tlog(f"csv_list empty blob_prefix={blob_prefix!r}")
    try:
        _buf_rows = len(buffer_df.index) if hasattr(buffer_df, "index") else 0
        _buf_cols = len(buffer_df.columns) if hasattr(buffer_df, "columns") else 0
    except Exception:
        _buf_rows, _buf_cols = 0, 0
    print(f"[CHUNK] table={table_name} buffer_rows={_buf_rows} buffer_cols={_buf_cols} buffer_row_count={buffer_row_count} table_exist={table_exist}")
    if column_size is None:
        column_size = len(buffer_df.columns)
            
    # Check if no blobs are found
    if not blob_names :
        print(f"No CSV blobs found in the path: {blob_prefix}. Proceeding to table_creation with empty data.")
        _tlog(
            f"no_csv_blobs path={blob_prefix!r} date_dir==final_dir={date_dir == final_dir} "
            f"buffer_row_count={buffer_row_count} table_exist={table_exist}"
        )
        # if date_dir == final_dir and (buffer_row_count > 0 or len(df_del) > 0):
        if buffer_row_count >= chunk_count:
            _flush_ready_buffer()
            _tlog(
                f"return_early no_csv_branch_with_buffer date_dir==final_dir total_rows={total_rows} "
                f"global_chunk_count={global_chunk_count} remainder={buffer_row_count}"
            )
            return total_rows, total_inserts, total_updates, total_delete_recids, column_size,buffer_df,buffer_row_count,existing_fields,table_name_final,staging_table,primary_key,df_lat
        if not table_exist:
            # Proceed directly to table_creation if no blobs are found
            table_creation(
                df, pd.DataFrame(), attribute_names, attribute_details, table_name,  
                destination_engine, connection_id, updated_at, deleted_at, 
                datetime.now(pytz.utc), True, 1, destination_connection, table_exist,is_integration_checked,extra_fields,modified_fields,existing_fields,attributes_details_field,cmp_id,migration_session_id,job_name, staging_table_name=staging_table_name
            )
            _tlog("return_early no_csv_table_creation_empty_df")
            return  # Exit after calling table_creation

    
    
    # Iterate over CSV blobs and process each in chunks
    for blob in blob_names:
        full_path = f"{container_name}/{blob}"
        _vprint("Processing blob:", full_path)
        _tlog(f"blob_start full_path={full_path!r}")

        is_delete_csv = is_dataverse_delete_export_csv(blob)

        # Read the CSV in chunks and call table_creation for each chunk
        for chunk, _, is_last_chunk in read_csv_in_chunks(fs, full_path, attribute_names,chunk_size=chunk_count):
            if chunk.empty:
                _vprint(f"Chunk from blob {full_path} is empty. Skipping this chunk.")
                # table_exist=True
                continue  # Skip empty chunks

            if is_delete_csv:
                chunk = _mark_dataverse_delete_csv_chunk(chunk, attribute_names)
                _tlog(f"delete_csv_marked blob={blob!r} rows={len(chunk.index)}")

            all_chunks_empty = False  # Mark that not all chunks are empty

            buffer_df = pd.concat([buffer_df, chunk], ignore_index=True)
            buffer_row_count += len(chunk)
            _flush_ready_buffer(blob_path=full_path)
                
        
    
    # If all chunks were empty, proceed to table_creation with empty data
    if all_chunks_empty:
        print("All chunks were empty. Proceeding to table_creation with empty data.")
        _tlog(f"all_chunks_empty table_exist={table_exist}")
        if not table_exist:
            table_creation(
                df, pd.DataFrame(), attribute_names, attribute_details, table_name,  
                destination_engine, connection_id, updated_at, deleted_at, 
                current_utc_time,True,global_chunk_count, destination_connection, table_exist,is_integration_checked,extra_fields,modified_fields,existing_fields,attributes_details_field,cmp_id,migration_session_id,job_name, staging_table_name=staging_table_name
            )
            _tlog("return_after all_chunks_empty table_creation")
            return  # Exit after calling table_creation
        
    
    logger.info(
        "[CHUNK] table=%s done rows=%s inserts=%s updates=%s deletes=%s cols=%s",
        table_name, total_rows, total_inserts, total_updates, total_delete_recids, column_size,
    )

    _tlog(
        f"exit_ok total_rows={total_rows} inserts={total_inserts} updates={total_updates} "
        f"deletes={total_delete_recids} column_size={column_size} "
        f"buffer_row_count={buffer_row_count} primary_key={primary_key!r} "
        f"staging_table={staging_table!r} table_name_final={table_name_final!r}"
    )
    if df_lat.columns.empty and hasattr(df, "columns") and not df.columns.empty:
        df_lat = df
    return total_rows, total_inserts, total_updates, total_delete_recids, column_size,buffer_df,buffer_row_count,existing_fields,table_name_final,staging_table,primary_key,df_lat

import pandas as pd
import csv

def read_csv_in_chunks(fs, full_path, attribute_names, delimiter=',', quotechar='"', skiprows=0, chunk_size=50000):
    """
    Reads a CSV file from Azure Blob Storage in chunks and processes each chunk.
    Args:
        fs (adlfs.AzureBlobFileSystem): Azure Blob Filesystem instance.
        full_path (str): Full path to the CSV file in blob storage.
        attribute_names (list): List of attribute names for the CSV headers.
        delimiter (str): CSV delimiter.
        quotechar (str): CSV quote character.
        skiprows (int): Number of rows to skip from the beginning of the file.
        chunk_size (int): Number of rows to process per chunk.
    Yields:
        Tuple: (DataFrame chunk, chunk count, is_last_chunk)
    """
    chunk_count = 0  # To track the number of chunks processed
    column_converters = {col: str for col in attribute_names} 
    try:
        # Open the file as a stream (read in chunks instead of downloading entire content)
        with fs.open(full_path, 'rb') as file_stream:
            # Initialize the chunk iterator using the file stream
            chunk_iter = pd.read_csv(file_stream, delimiter=delimiter, 
                                     quotechar=quotechar, skiprows=skiprows, 
                                     chunksize=chunk_size, header=None, names=attribute_names, 
                                     low_memory=False,converters=column_converters)

            # Track and process each chunk
            for chunk in chunk_iter:
                chunk_count += 1
                if chunk.empty:
                    print(f"Chunk {chunk_count} is empty, skipping.")
                    continue

                # Yield each chunk and flag indicating it's not the last chunk
                yield chunk, chunk_count, False  

    except UnicodeDecodeError:
        print("Encoding error encountered. Trying with 'ISO-8859-1' encoding.")
        with fs.open(full_path, 'rb') as file_stream:
            chunk_iter = pd.read_csv(file_stream, delimiter=delimiter, 
                                     quotechar=quotechar, skiprows=skiprows, 
                                     encoding='ISO-8859-1', chunksize=chunk_size, 
                                     header=None, names=attribute_names, low_memory=False,converters=column_converters)

            for chunk in chunk_iter:
                chunk_count += 1
                if chunk.empty:
                    print(f"Chunk {chunk_count} is empty, skipping.")
                    continue

                yield chunk, chunk_count, False  

    # Indicate the end of chunks
    yield pd.DataFrame(), chunk_count, True



import os

def infer_base_folder(container_client,cmp_id,connection_id, max_blobs=10):
    """
    Infers the base folder by analyzing a subset of blob paths in the container.

    Args:
        container_client: Azure Blob Storage container client.
        max_blobs: Maximum number of blobs to consider for inferring the base folder.

    Returns:
        The inferred base folder as a string.
    """
    try:
        # List a limited number of blobs directly while fetching
        blob_iterator = container_client.list_blobs(results_per_page=max_blobs).by_page()
        first_page = next(blob_iterator)
        paths = [blob.name for blob in first_page]

        if not paths:
            raise ValueError("No blobs found in the container to infer a base folder.")

        # Segment- and date-aware (avoids os.path.commonprefix truncating mid-segment)
        return infer_dataverse_base_folder_from_paths(paths)

    except ValueError as ve:
        print(f"ValueError occurred: {ve}")
        message=f"ValueError occurred: {ve}"
        insert_error_log(cmp_id, connection_id,message, "infer_base_folder")
        raise

    except Exception as e:
        print(f"An error occurred: {e}")
        message1=f"An error occurred: {e}"
        insert_error_log(cmp_id, connection_id,message1, "infer_base_folder")
        raise


def is_directory_greater_than(directory_name, last_checked_date):
    dir_date = datetime.strptime(directory_name, "%Y-%m-%dT%H.%M.%SZ")
    last_checked = datetime.strptime(last_checked_date, "%Y-%m-%dT%H.%M.%SZ")
    return dir_date >= last_checked 



def safe_json_load(data):
    """Safely loads a JSON string. Returns an empty list if the string is invalid or empty."""
    try:
        # If data is already a dict or list, return it as is
        if isinstance(data, (dict, list)):
            return data
        # Attempt to load data from a JSON string
        return json.loads(data) if data else []
    except (ValueError, TypeError):
        # Return an empty list if JSON loading fails
        return []

def update_table_status(connection_id, table_name, status, last_checked_dates, job_name, migration_session_id, existing_fields,cmp_id, table_detail_log=None):
    """
    Updates the status of the SourceTableSelection and MigrationRecord entries in PostgreSQL.
    Also sets start_time when status changes to 'in_progress'.

    Parameters:
    - connection_id: The ID of the connection.
    - table_name: The name of the table whose status needs to be updated.
    - status: The new status value (e.g., 'in_progress', 'completed', 'failed').
    - last_checked_dates: Dictionary containing the last checked dates for tables.
    - job_name: Name of the migration job.
    - migration_session_id: ID of the migration session.
    - existing_fields: A JSON-like string or object to be processed for the table_fields column.
    - cmp_id: Company id.
    - table_detail_log: Optional _TableMigrationDetailFileLog for per-table file diagnostics.
    """
    _vprint(
        "update_table_status connection_id=%s table=%s status=%s job=%s session=%s",
        connection_id, table_name, status, job_name, migration_session_id,
    )

    # Safe handling of existing_fields with json.loads
    last_checked_date = last_checked_dates.get(table_name)  # Safe fallback if table_name is not found

    table_fields = safe_json_load(existing_fields) or []

    # Ensure table_fields is serialized as JSON (string) for postgres
    table_fields_json = json.dumps(table_fields) if isinstance(table_fields, (list, dict)) else '[]'

    # Connect to PostgreSQL
    conn = postgres_connection()  # Ensure this function returns a valid connection

    try:
        # Create a cursor object
        cursor = conn.cursor()
        sts_rows = None
        mr_rows = None
        row = None
        
        # Update source_table_selection
        sql_update_source_table = """
        UPDATE source_table_selection
        SET status = %s, last_synced = %s, table_fields = %s
        WHERE connection_id = %s AND "table" = %s;
        """
        cursor.execute(sql_update_source_table, (status, last_checked_date, table_fields_json, connection_id, table_name))
        sts_rows = cursor.rowcount
        cursor.execute(
            """
            SELECT id, status, start_time
            FROM migration_record
            WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
            """,
            (job_name, migration_session_id, table_name)
        )

        row = cursor.fetchone()
        _vprint("migration_record before update:", row)

        # Update migration_record with table_name condition - set start_time when status is 'in_progress'
        # NOTE: start_time will be updated later in data_transfer_logs with actual transfer start time
        if status == 'in_progress':
            sql_update_migration_record = """
            UPDATE migration_record
            SET status = %s, updated_at = NOW()
            WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
            """
        else:
            sql_update_migration_record = """
            UPDATE migration_record
            SET status = %s, updated_at = NOW()
            WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
            """
        cursor.execute(sql_update_migration_record, (status, job_name, migration_session_id, table_name))
        mr_rows = cursor.rowcount
        
        # Commit the changes
        conn.commit()
        logger.info("Updated table %s status to %s", table_name, status)

        if table_detail_log and table_detail_log.enabled():
            table_detail_log.log(
                f"update_table_status committed table={table_name!r} status={status!r} "
                f"source_table_selection_rows={sts_rows} migration_record_rows={mr_rows} "
                f"migration_record_before={row!r}"
            )

        # ✅ Trigger WebSocket status update for real-time UI tracking
        try:
            from utils.websocket_utils import trigger_ws_status_update, trigger_connection_ws_status_update
            trigger_ws_status_update(migration_session_id)
            trigger_connection_ws_status_update(connection_id)
        except Exception as ws_err:
            logger.warning("WebSocket trigger failed: %s", ws_err)
        
    except Exception as e:
        logger.error("Error updating table or migration record: %s", e)
        if table_detail_log and table_detail_log.enabled():
            try:
                table_detail_log.log(
                    f"update_table_status FAILED table={table_name!r} intended_status={status!r} "
                    f"sts_rows={sts_rows!r} mr_rows={mr_rows!r} err={e!r}"
                )
            except Exception:
                pass
        message=f"Error updating table or migration record: {e}"
        insert_error_log(cmp_id, connection_id,message, "update_table_status")
        
    finally:
        # Close the cursor and connection
        cursor.close()
        conn.close()

def delete_csv_from_bucket(table_name, connection_id, cmp_id):
    """Delete the temp Dynamics CSV from GCS after COPY-to-staging.

    Thin wrapper around `utils.gcp_utils.delete_blob_from_bucket` so every
    connector shares the same GCS cleanup primitive. Errors are logged
    here and swallowed (staging load already succeeded; a leaked temp file
    is not a reason to fail the migration).
    """
    bucket_name = BUCKET_NAME
    file_name = f"temp_data_dynamics_f&o_{table_name}_{connection_id}.csv"
    try:
        ok = delete_blob_from_bucket(bucket_name, file_name, client=storage_client)
        if not ok:
            insert_error_log(
                cmp_id, connection_id,
                f"delete_blob_from_bucket returned False for {bucket_name}/{file_name}",
                "delete_csv_from_bucket",
            )
        return f"File {file_name} deleted successfully from bucket {bucket_name}."
    except Exception as e:
        message = f"An error occurred while deleting the file {file_name}: {e}"
        insert_error_log(cmp_id, connection_id, message, "delete_csv_from_bucket")
        return message


# Snowflake data type mapping
field_dtype_conversion = {
    'guid': 'VARCHAR(512)',         # GUID/UUID mapped to VARCHAR(512)
    'dateTime': 'TIMESTAMP_TZ(9)',  # Both dateTime and dateTimeOffset use TIMESTAMP_TZ(9)
    'dateTimeOffset': 'TIMESTAMP_TZ(9)',
    'int64': 'NUMBER(38,0)',        # Large integer
    'decimal': 'NUMBER(38,6)',             # Decimal mapped to FLOAT for compatibility
    'float': 'FLOAT',               # Float type mapped to Snowflake FLOAT
    'string': 'VARCHAR',            # String with a defined max length
    'boolean': 'BOOLEAN'            # Logical type
}

def get_snowflake_type(field_type, max_length=None):
    """Maps field type to Snowflake data type with size if applicable."""
    # Default to VARCHAR with a max length if type is not found in the mapping
    if field_type == 'string' and max_length:
            # Adjust VARCHAR length to nearest multiple of 512
            length = ((max_length - 1) // 512 + 1) * 512
            snowflake_type = f"VARCHAR({length})"
        
            
    elif field_type in field_dtype_conversion:
        snowflake_type = field_dtype_conversion[field_type]
    else:
        snowflake_type = 'VARCHAR(512)' 
         # Default type if unknown
    return snowflake_type


from concurrent.futures import ThreadPoolExecutor, as_completed

def process_table(
    table_name, connection_string, container_name, base_folder, date_directories, 
    container_client, connection_id, cmp_id, usr_id, session, 
    updated_at, deleted_at, destination_connection, 
    last_checked_date, job_name, migration_session_id, table_fields, is_integration_checked,chunk_count
):
    # Set after Snowflake connect: legacy cleanup runs before this name exists in Snowflake.
    staging_table_name = None
    existing_fields = {}
    destination_engine = None
    snowflake_schema = ""

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

    # Place the inner logic of processing each table here
    # Extracted and kept similar to the original implementation
        destination_engine,snowflake_schema = destination_connection_function(destination_connection,connection_id,cmp_id)
        detail_log.log(
            f"destination_connection ok snowflake_schema={snowflake_schema!r} "
            f"engine={type(destination_engine).__name__}"
        )
        # Drop prior DS_<TABLE>_STAGING_* objects before we create the new staging table (copy path).
        # Reuse staging for the same migration session so a duplicate Celery delivery cannot
        # drop the table another worker is still loading into.
        reused_staging = lookup_session_staging_name(migration_session_id, table_name)
        if reused_staging:
            staging_table_name = reused_staging
            dropped_pre = 0
            detail_log.log(
                f"prechunk_staging_cleanup skipped reason=reuse_session_staging "
                f"staging_table_name={staging_table_name!r}"
            )
        else:
            dropped_pre = drop_existing_ds_staging_tables_for_table(
                destination_engine,
                snowflake_schema,
                table_name,
                keep_staging_name=None,
                migration_session_id=migration_session_id,
            )
            detail_log.log(
                f"prechunk_staging_cleanup dropped_legacy_staging_count={dropped_pre} "
                f"(protected in-use staging registered in Redis for {table_name!r})"
            )
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            staging_table_name = f"DS_{table_name.upper()}_STAGING_{timestamp}"
            remember_session_staging_name(migration_session_id, table_name, staging_table_name)
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
        df=pd.DataFrame()
        merge_columns_df = pd.DataFrame()

        table_name_final = None
        staging_table = staging_table_name
        primary_key = "ID"  # Default primary key
        

        existing_fields = (table_fields or {}).get(table_name) or {}
        table_exist = check_table_exists_snowflake(destination_engine, table_name, destination_connection,connection_id,cmp_id)
        detail_log.log(f"check_table_exists_snowflake table_exist={table_exist}")
        extra_fields = []
        modified_fields={}
        # In-progress at table start so DB/UI match long-running work (avoids stale "completed").
        update_table_status(
            connection_id, table_name, "in_progress", last_checked_date, job_name,
            migration_session_id, existing_fields, cmp_id, table_detail_log=detail_log,
        )

        if not table_exist:
            last_checked_date[table_name] = None
        final_dir=date_directories[-1]
        constant_last_checked = last_checked_date.get(table_name)
        start_processing = last_checked_date.get(table_name, None) is None
        start_time = datetime.now()
        last_valid_dir = last_checked_date.get(table_name)
        
        
        
        # df_del = pd.DataFrame()
        buffer_df = pd.DataFrame()  # Initialize an empty DataFrame to buffer rows
        buffer_row_count = 0  # Counter to track rows in the buffer
        attribute_details = {}
        attribute_details_field = {}
        attribute_names = []
        detail_log.log(
            f"date_dir_loop: final_dir={final_dir!r} start_processing={start_processing} "
            f"constant_last_checked={constant_last_checked!r}"
        )
        for date_dir in date_directories:





            if start_processing or is_directory_greater_than(date_dir, constant_last_checked):
                detail_log.log(
                    f"date_dir: consider date_dir={date_dir!r} start_processing={start_processing} "
                    f"constant_last_checked={constant_last_checked!r}"
                )
                table_dir_path = join_azure_blob_path(base_folder, date_dir, table_name) + "/"
                if not directory_exists_in_date_directory(container_client, base_folder, date_dir, table_name,cmp_id,connection_id):
                    print(f"[DIR_CHECK] not found in directory: table={table_name} path={table_dir_path}")
                    detail_log.log(f"DIR_CHECK not_found path={table_dir_path!r}")
                    json_path = join_azure_blob_path(
                        base_folder, date_dir, "Microsoft.Athena.TrickleFeedService", f"{table_name}-model.json"
                    )
                    json_blob = container_client.get_blob_client(json_path)

                    if json_blob.exists() and not table_exist:
                        blob_content = json_blob.download_blob().readall()
                        schema_data = json.loads(blob_content)
                        # Some upstream schema JSON may contain explicit nulls.
                        # Treat null the same as missing to avoid "NoneType is not iterable".
                        entities = schema_data.get('entities') or []

                        for entity in entities:
                            attributes = entity.get('attributes') or []
                            attribute_details = {}
                            attribute_details_field={}

                            # Process attributes to include data type and size
                            for attr in attributes:
                                field_name = attr['name']
                                field_type = attr['dataType']
                                max_length = attr.get('maxLength', None)

                                # Get the Snowflake data type, with size adjustment if applicable
                                snowflake_type = get_snowflake_type(field_type, max_length)
                                attribute_details = {attr['name']: attr['dataType'] for attr in attributes}
                                # Add to the attribute details
                                attribute_details_field[field_name] = snowflake_type
                            attribute_details_field[updated_at] = "TIMESTAMP_TZ(9)"
                            attribute_details_field[deleted_at] = "BOOLEAN"
                            attribute_names = list(attribute_details.keys())

                            df = pd.DataFrame(columns=attribute_names)

                            if table_exist:
                                # Align fields with existing schema
                                df, modified_fields, extra_fields = align_fields_with_existing(df, attribute_details_field, existing_fields, table_name)

                                if modified_fields:
                                    _vprint(f"Modified fields for table '{table_name}': {modified_fields}")
                                    # Update existing_fields with modified field definitions
                                    for field, new_type in modified_fields.items():
                                        existing_fields[field] = new_type

                                if extra_fields:
                                    _vprint(f"Extra fields for table '{table_name}': {extra_fields}")
                                    # Add extra fields to existing_fields
                                    for field in extra_fields:
                                        existing_fields[field] = attribute_details_field[field]

                            else:

                                existing_fields = attribute_details_field

                            process_file_in_chunks(
                                connection_string, container_name, base_folder, date_dir, container_client, df,
                                {table_name: table_dir_path}, {table_name: json_path},
                                attribute_details, table_name, destination_engine, connection_id, updated_at, deleted_at,
                                destination_connection, table_exist, is_integration_checked, extra_fields,modified_fields,existing_fields,attribute_details_field,buffer_df,buffer_row_count,final_dir,cmp_id,chunk_count, staging_table_name=staging_table_name,
                                table_detail_log=detail_log,
                            )

                        table_exist = True


                    if date_dir!=final_dir:
                        continue
                    elif(buffer_row_count==0):
                        continue

                if not table_exist:
                    table_exist = check_table_exists_snowflake(destination_engine, table_name, destination_connection,connection_id,cmp_id)
                print(f"[DIR_CHECK] found in directory: table={table_name} date_dir={date_dir} path={table_dir_path}")
                detail_log.log(f"DIR_CHECK found date_dir={date_dir!r} path={table_dir_path!r}")
                folderpath_dict = {table_name: table_dir_path}
                json_path = join_azure_blob_path(
                    base_folder, date_dir, "Microsoft.Athena.TrickleFeedService", f"{table_name}-model.json"
                )
                blob_client = container_client.get_blob_client(json_path)
                blob_content = blob_client.download_blob().readall()
                schema_data = json.loads(blob_content)

                entities = schema_data.get('entities') or []
                for entity in entities:
                    attributes = entity.get('attributes') or []
                    attribute_details = {}
                    attribute_details_field={}

                            # Process attributes to include data type and size
                    for attr in attributes:
                        field_name = attr['name']
                        field_type = attr['dataType']
                        max_length = attr.get('maxLength', None)

                        # Get the Snowflake data type, with size adjustment if applicable
                        snowflake_type = get_snowflake_type(field_type, max_length)
                        attribute_details = {attr['name']: attr['dataType'] for attr in attributes}
                        # Add to the attribute details
                        attribute_details_field[field_name] = snowflake_type
                    attribute_details_field[updated_at] = "TIMESTAMP_TZ(9)"
                    attribute_details_field[deleted_at] = "BOOLEAN"
                    attribute_names = list(attribute_details.keys())

                    df = pd.DataFrame(columns=attribute_names)

                    if table_exist:
                        # Align fields with existing schema
                        df, modified_fields, extra_fields = align_fields_with_existing(df, attribute_details_field, existing_fields, table_name)


                    else:

                        existing_fields = attribute_details_field



                    total_rows, total_inserts, total_updates, total_delete_recids, column_size,buffer_df,buffer_row_count,existing_fields,table_name_final,staging_table,primary_key,chunk_schema_df = process_file_in_chunks(
                        connection_string, container_name, base_folder, date_dir, container_client, df,
                        folderpath_dict, list(attribute_details.keys()), attribute_details, table_name, destination_engine,
                        connection_id, updated_at, deleted_at, destination_connection, table_exist,
                        is_integration_checked, extra_fields,modified_fields,existing_fields,attribute_details_field,buffer_df,buffer_row_count,final_dir,cmp_id,chunk_count,migration_session_id,job_name, staging_table_name=staging_table_name,
                        table_detail_log=detail_log,
                    )
                    merge_columns_df = _remember_merge_schema_df(merge_columns_df, chunk_schema_df)

                    all_total_rows += total_rows
                    all_total_inserts += total_inserts
                    all_total_updates += total_updates
                    all_total_deletes += total_delete_recids


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
                tail_note, buffer_row_count, chunk_count,
            )
            detail_log.log(
                f"process_table: end_flush {tail_note} rows={buffer_row_count} "
                f"chunk_count={chunk_count}"
            )
            data, inserts, deletes, updates, snowflake_schema, table_name_final, staging_table, primary_key, tail_schema_df = table_creation(
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
            )
            merge_columns_df = _remember_merge_schema_df(merge_columns_df, tail_schema_df)
            all_total_rows += len(data) if data is not None else 0
            all_total_inserts += inserts
            all_total_updates += updates
            all_total_deletes += deletes
            buffer_df = pd.DataFrame()
            buffer_row_count = 0

        staging_rows_before_merge = _count_staging_table_rows(
            destination_engine, snowflake_schema, staging_table_name,
        )
        merge_df = merge_columns_df
        if merge_df.columns.empty:
            merge_df = _merge_schema_df_from_staging(
                destination_engine, snowflake_schema, staging_table_name,
            )
        _vprint(
            f"process_table:before_merge table={table_name} "
            f"all_total_rows={all_total_rows} all_total_inserts={all_total_inserts} "
            f"all_total_updates={all_total_updates} all_total_deletes={all_total_deletes} "
            f"buffer_row_count={buffer_row_count} staging_table_name={staging_table_name} "
            f"staging_rows={staging_rows_before_merge} "
            f"merge_df_rows={len(merge_df.index) if hasattr(merge_df, 'index') else 'NA'} "
            f"merge_df_cols={len(merge_df.columns) if hasattr(merge_df, 'columns') else 'NA'}"
        )
        logger.warning(
            "[MERGE] before_merge table=%s staging=%s staging_rows=%s "
            "merge_df_cols=%s all_total_rows=%s inserts=%s updates=%s deletes=%s",
            table_name,
            staging_table_name,
            staging_rows_before_merge,
            len(merge_df.columns) if hasattr(merge_df, "columns") else 0,
            all_total_rows,
            all_total_inserts,
            all_total_updates,
            all_total_deletes,
        )
        detail_log.log(
            "before_merge "
            f"all_total_rows={all_total_rows} all_total_inserts={all_total_inserts} "
            f"all_total_updates={all_total_updates} all_total_deletes={all_total_deletes} "
            f"buffer_row_count={buffer_row_count} staging_table_name={staging_table_name!r} "
            f"staging_rows={staging_rows_before_merge} "
            f"merge_df_rows={len(merge_df.index) if hasattr(merge_df, 'index') else 'NA'} "
            f"merge_df_cols={len(merge_df.columns) if hasattr(merge_df, 'columns') else 'NA'}"
        )
        if not merge_df.columns.empty:
            merge_into_snowflake(
                destination_engine, snowflake_schema, table_name, primary_key, merge_df,
                updated_at, deleted_at, cmp_id, connection_id,
                staging_table_name=staging_table_name,
                table_detail_log=detail_log,
            )
        elif staging_rows_before_merge and staging_rows_before_merge > 0:
            raise RuntimeError(
                f"Cannot merge {snowflake_schema}.{staging_table_name}: "
                f"staging has {staging_rows_before_merge} rows but no column schema was resolved"
            )
        else:
            logger.warning(
                "[MERGE] merge_skipped table=%s reason=empty_staging_and_no_schema",
                table_name,
            )
            detail_log.log("merge_skipped: empty staging and no merge column schema")
        end_time = datetime.now()
        last_checked_date[table_name] = final_dir
        # Sole success path: mark table completed only after merge (if any), data_transfer_logs,
        # and all chunk processing above — nowhere else in this service should set "completed".
        status = "completed"
        data_transfer_logs(cmp_id, connection_id, usr_id, table_name, start_time, end_time, all_total_inserts,all_total_updates,all_total_deletes, column_size, session,job_name=job_name)
        logger.info(
            "process_table completed table=%s rows=%s inserts=%s updates=%s deletes=%s",
            table_name, all_total_rows, all_total_inserts, all_total_updates, all_total_deletes,
        )
        detail_log.log(
            f"process_table:end status={status} rows={all_total_rows} inserts={all_total_inserts} "
            f"updates={all_total_updates} deletes={all_total_deletes}"
        )
        update_table_status(
            connection_id, table_name, status, last_checked_date, job_name,
            migration_session_id, existing_fields, cmp_id, table_detail_log=detail_log,
        )
        delete_csv_from_bucket(table_name,connection_id,cmp_id)
        # Drop staging only after the whole table finished successfully.
        try:
            if staging_table_name and destination_engine and snowflake_schema:
                logger.info(
                    "Dropping staging table after table complete: %s.%s",
                    snowflake_schema,
                    staging_table_name,
                )
                drop_staging_table(destination_engine, staging_table_name, snowflake_schema)
                forget_session_staging_name(migration_session_id, table_name)
        except Exception as cleanup_err:
            logger.warning(
                "Failed to drop staging table %s after table complete: %s",
                staging_table_name,
                cleanup_err,
            )
        return all_total_rows, all_total_inserts, all_total_updates, all_total_deletes, column_size, last_checked_date 
    
    except Exception as e:
        try:
            detail_log.log(f"EXCEPTION: {e!r}\n{traceback.format_exc()}")
        except Exception:
            pass
        logger.error("process_table failed table=%s: %s", table_name, e)
        # Handle any unexpected errors, ensure the process is completed or logged properly
        error_message=f"An error occurred while processing the table '{table_name}': {e}"
        update_table_status(
            connection_id, table_name, "failed", last_checked_date, job_name,
            migration_session_id, existing_fields, cmp_id, table_detail_log=detail_log,
        )
        insert_tool_log(cmp_id, connection_id, usr_id, "Migration", "E", error_message, session,job_name=job_name)
        insert_error_log(cmp_id, connection_id,error_message, "process_table")
        # Keep staging on failure so a retry can reuse the same session table.
        # Return None so migrate() counts this table as failed (not successful_tables).
        return None

    finally:
        detail_log.close()


def log_with_time(message, start_time=None, end_time=None):
    """Helper function to log messages with start time, end time, and duration."""
    if start_time and end_time:
        duration = (end_time - start_time).total_seconds()
        start_str = start_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        end_str = end_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        print(f"{message} - Start: {start_str}, End: {end_str}, Duration: {duration:.3f} seconds")
    else:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        print(f"{current_time} - {message}")


def filter_date_directories(date_directories, safety_interval_cutoff):
    """
    Filters the list of date directories to include only those that are less than the safety_interval_cutoff.

    Args:
        date_directories: List of date directories in 'YYYY-MM-DDTHH.MM.SSZ' format.
        safety_interval_cutoff: A datetime object representing the maximum date limit.

    Returns:
        A list of date directories that are less than the safety_interval_cutoff.
    """
    # Ensure safety_interval_cutoff is a datetime object
    if isinstance(safety_interval_cutoff, str):
        safety_interval_cutoff = datetime.strptime(safety_interval_cutoff, "%Y-%m-%dT%H.%M.%SZ")

    # Filter the date directories
    filtered_directories = [
        folder_date for folder_date in date_directories
        if datetime.strptime(folder_date, "%Y-%m-%dT%H.%M.%SZ") < safety_interval_cutoff
    ]

    return filtered_directories
def process_table_wrapper(
    table_name, connection_string, container_name, base_folder, date_directories,
    container_client, connection_id, cmp_id, usr_id, session, updated_at, deleted_at,
    destination_connection, last_checked_date, job_name, migration_session_id,
    table_fields, is_integration_checked,chunk_count
):
    try:
        result = process_table(
            table_name, connection_string, container_name, base_folder, date_directories,
            container_client, connection_id, cmp_id, usr_id, session, updated_at, deleted_at,
            destination_connection, last_checked_date, job_name, migration_session_id,
            table_fields, is_integration_checked,chunk_count
        )
        print(f"Table {table_name} processed: {result}")
        return (table_name, result)
    except Exception as e:
        print(f"Error processing table {table_name}: {e}")
        message = f"Error processing table {table_name}: {e}"
        insert_error_log(cmp_id, connection_id, message, "main")
        return (table_name, None)

def main(
    connection_string, container_name, selected_tables,
    connection_id, cmp_id, usr_id, session, updated_at, deleted_at,
    destination_connection, safety_interval, last_checked_date, job_name,
    migration_session_id, table_fields, last_checked_datedirectory, execution_order ,chunk_count, drop_and_reload=False
):
    # Verbose startup diagnostics (MIGRATION_VERBOSE_LOGS=1)
    _vprint("main drop_and_reload:", drop_and_reload, "tables:", selected_tables)
    
    _vprint("Starting connection to blob storage")
    start_time = datetime.now()
    container_client = connect_to_blob_storage(connection_string, container_name)
    end_time = datetime.now()
    _vprint("Finished connection to blob storage")
    log_with_time("Connected to blob storage", start_time, end_time)

    _vprint("Starting to infer base folder")
    start_time = datetime.now()
    base_folder = infer_base_folder(container_client, cmp_id, connection_id)
    _vprint("Base folder:", base_folder)
    end_time = datetime.now()
    log_with_time("Inferred base folder", start_time, end_time)

    # Get the current UTC time
    current_utc_time_safety = pd.Timestamp.now(tz="UTC")

    # Parse the safety interval
    safety_interval_duration = pd.Timedelta(0) if not safety_interval or safety_interval == 'None' else pd.Timedelta(minutes=int(safety_interval))

    # Calculate the cutoff time
    safety_interval_cutoff = (current_utc_time_safety - safety_interval_duration).strftime("%Y-%m-%dT%H.%M.%SZ")
    safety_interval_cutoff_dt = datetime.strptime(safety_interval_cutoff, "%Y-%m-%dT%H.%M.%SZ")

    _vprint("Getting filtered date directories")
    start_time = datetime.now()
    date_directories_save = list_date_directories(
        container_client, cmp_id, connection_id, safety_interval_cutoff_dt, last_checked_datedirectory, base_folder=base_folder
    )
    date_directories_filter = insert_or_update_date_directories(connection_id, date_directories_save)
    date_directories = filter_date_directories(date_directories_filter, safety_interval_cutoff)
    end_time = datetime.now()
    log_with_time("Got filtered date directories", start_time, end_time)
    _vprint("Final date directories:", date_directories)

    is_integration_checked = False
    aggregated_results = []

    # ═══════════════════════════════════════════════════════════════
    # DROP TABLES IF drop_and_reload FLAG IS TRUE
    # This ensures a clean slate before reloading data
    # ═══════════════════════════════════════════════════════════════
    _vprint("drop_and_reload check:", drop_and_reload)
    
    if drop_and_reload:
        logger.info("DROP AND RELOAD: dropping %s table(s) before reload", len(selected_tables))
        
        # Import the drop utility function
        from utils.table_utils import drop_snowflake_table
        
        # Create Snowflake engine for drop operations
        try:
            # Extract Snowflake connection parameters
            account = destination_connection.get('account', '')
            user = destination_connection.get('user') or destination_connection.get('username', '')
            password = destination_connection.get('password', '')
            database = destination_connection.get('database', '')
            schema = destination_connection.get('schema', 'PUBLIC').upper()
            warehouse = destination_connection.get('warehouse', '')
            role = destination_connection.get('role', '')
            private_key_str = destination_connection.get('private_key', '')
            passphrase = destination_connection.get('passphrase', '')
            
            # Import required modules
            from urllib.parse import quote
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            
            _vprint("Creating Snowflake engine for drop operations account=%s db=%s schema=%s", account, database, schema)
            
            # Extract authentication type from destination_connection
            auth_type = destination_connection.get('authentication_type', 'password').lower()
            
            # Detect authentication type and build engine accordingly
            if auth_type == 'key_pair' and private_key_str:
                # --- Key pair authentication ---
                _vprint("Authentication: Key pair")
                
                # Normalize and load private key
                from services.snowflake.service import normalize_pem_key
                normalized_private_key = normalize_pem_key(private_key_str)
                private_key_bytes = normalized_private_key.encode()
                
                # Use passphrase if provided for encrypted keys
                passphrase_bytes = None
                if passphrase and passphrase.strip():
                    passphrase_bytes = passphrase.encode("utf-8")
                    _vprint("Using passphrase for encrypted private key")
                else:
                    _vprint("No passphrase provided, attempting unencrypted key")
                
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
                
            elif auth_type == 'password' and password:
                # --- Password authentication ---
                _vprint("Authentication: Password")
                
                snowflake_username_quoted = quote(user)
                snowflake_password_quoted = quote(password)
                
                connection_string_sf = (
                    f"snowflake://{snowflake_username_quoted}:{snowflake_password_quoted}@{account}/"
                    f"{database}?warehouse={warehouse}"
                )
                if role:
                    connection_string_sf += f"&role={role}"
                
                destination_engine = create_engine(connection_string_sf)
                
            else:
                raise ValueError(f"Authentication failed: authentication_type='{auth_type}', but required credentials not provided. Expected {'private_key' if auth_type == 'key_pair' else 'password'} for {auth_type} authentication")
            
            # Set role and schema if specified
            with destination_engine.connect() as conn:
                if role:
                    conn.execute(text(f"USE ROLE {role}"))
                    _vprint("Role set to:", role)
                conn.execute(text(f"USE SCHEMA {schema}"))
                _vprint("Schema set to:", schema)
            
            # Drop each selected table
            for table_name in selected_tables:
                logger.info("Dropping table: %s", table_name)
                drop_result = drop_snowflake_table(
                    destination_engine=destination_engine,
                    table_name=table_name,
                    schema=schema,
                    connection_id=connection_id,
                    cmp_id=cmp_id
                )
                
                if drop_result['success']:
                    logger.info("%s", drop_result['message'])
                else:
                    logger.warning("%s", drop_result['message'])
            
            # Dispose engine after drop operations
            destination_engine.dispose()
            logger.info("Drop phase completed for %s table(s)", len(selected_tables))
            
        except Exception as e:
            error_msg = f"Error during drop phase: {str(e)}"
            logger.error("%s", error_msg)
            import traceback
            logger.error("Drop phase traceback: %s", traceback.format_exc())
            insert_error_log(cmp_id, connection_id, error_msg, "main_drop_tables")
            logger.warning("Continuing with migration despite drop errors")

    # Keep table processing sequential inside Celery workers.
    # Parallelism is handled at the task level via Celery fanout.
    if execution_order == "parallel":
        _vprint("Execution order parallel requested; using sequential mode in Celery-safe path")
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
                table_name, connection_string, container_name, base_folder, date_directories,
                container_client, connection_id, cmp_id, usr_id, session, updated_at, deleted_at,
                destination_connection, last_checked_date, job_name, migration_session_id,
                table_fields, is_integration_checked,chunk_count
            )
        )

    clear_connection_retrieval_progress(connection_id)
    logger.info("All tables processed")
    return aggregated_results



import re

def align_fields_with_existing(df, attribute_details, existing_fields, table_name):
    """
    Aligns DataFrame columns with the expected schema based on attribute_details and existing fields in the table.
    
    Parameters:
    - df: The DataFrame to be aligned.
    - attribute_details: Dictionary of required attributes and their types (including size where applicable).
    - existing_fields: Dictionary of fields that currently exist in the table, including their types and sizes.
    - table_name: Name of the table being processed.
    
    Returns:
    - Updated DataFrame.
    - List of modified fields (with type/size changes, if any).
    - List of extra fields (new fields not in the existing schema).
    """
    # Identify fields missing in the DataFrame (extra fields)
    missing_fields = [field for field in existing_fields if field not in df.columns]
    if missing_fields:
        print(f"Adding missing fields to DataFrame for table '{table_name}': {missing_fields}")
        for field in missing_fields:
            df[field] = None  # Add missing fields with NULL values
    
    # Identify fields in DataFrame but not in the existing schema (extra fields)
    extra_fields = [field for field in df.columns if field not in existing_fields]
    _vprint(f"Extra fields for table '{table_name}': {extra_fields}")

    # Identify modified fields (type or size changes)
    modified_fields = {}
    
    for field in df.columns:
        if field in existing_fields:
            # Check if the field's type and size in attribute_details match with the existing field type and size
            expected_type = attribute_details.get(field, None)
            if expected_type:
                # Assuming the existing fields have types and size stored as a string like 'VARCHAR(255)'
                existing_type = existing_fields[field]  # Assuming this stores the type in a similar format
                if expected_type != existing_type:
                    modified_fields[field]=expected_type  # Add field to modified_fields if the type or size doesn't match
                    print(f"Field '{field}' has a type/size mismatch: Expected '{expected_type}', found '{existing_type}'")

    _vprint(f"Modified fields for table '{table_name}': {modified_fields}")

    return df, modified_fields, extra_fields


# def align_fields_with_existing(df, attribute_details, existing_fields, table_name):
#     """
#     Aligns DataFrame columns with the expected schema based on attribute_details and existing fields in the table.
    
#     Parameters:
#     - df: The DataFrame to be aligned.
#     - attribute_details: Dictionary of required attributes and their types.
#     - existing_fields: List of fields that currently exist in the table.
#     - table_name: Name of the table being processed.
    
#     Returns:
#     - Updated DataFrame.
#     """
#     # Identify fields missing in the DataFrame
#     missing_fields = [field for field in existing_fields if field not in df.columns]
#     if missing_fields:
#         print(f"Adding missing fields to DataFrame for table '{table_name}': {missing_fields}")
#         for field in missing_fields:
#             df[field] = None  # Add missing fields with NULL values
    
#     # Identify fields in DataFrame but not in the existing schema
#     extra_fields = [field for field in df.columns if field not in existing_fields]
    

#     return df,extra_fields
def directory_exists_in_date_directory(container_client, base_folder, date_dir, table_name,cmp_id,connection_id):
    """
    Check if the table directory exists inside the given date directory.
    Args:
        container_client: The Azure Blob Storage container client.
        base_folder: Dataverse org folder prefix (may be empty for flat layouts).
        date_dir: The date directory to check.
        table_name: The table name to look for within the date directory.
    Returns:
        True if the table directory exists in the date directory, False otherwise.
    """
    # Must include org base folder — Dataverse paths are {base}/{date}/{table}/...
    table_dir_prefix = join_azure_blob_path(base_folder, date_dir, table_name) + "/"

    try:
        # List blobs in the container that match the table directory prefix
        blobs = container_client.list_blobs(name_starts_with=table_dir_prefix)
        
        # If there are any blobs that start with the table directory prefix, it means the directory exists
        for blob in blobs:
            if blob.name.startswith(table_dir_prefix):
                print(f"Table directory {table_name} found in {date_dir}.")
                return True  # The directory exists

        # If no matching blobs are found, the directory doesn't exist
        return False

    except Exception as e:
        print(f"Error checking directory {table_dir_prefix} in {date_dir}: {e}")
        message=f"Error checking directory {table_dir_prefix} in {date_dir}: {e}"
        insert_error_log(cmp_id, connection_id,message, "directory_exists_in_date_directory")
        return False

# def extract_table_column_type(table_name,cmp_id, connection_id):
#     """
#     Extracts the column names, types, and sizes from the specified table in the PostgreSQL database.
#     Retrieves information from existing_tbl_hdr and existing_tbl_itm based on the table_name.
# 
#     Args:
#         table_name (str): Name of the table to extract information from.
# 
#     Returns:
#         pd.DataFrame: A DataFrame containing the column names, types, and sizes.
#     """
#     try:
#         # Connect to PostgreSQL
#         conn = postgres_connection()
#         if not conn:
#             raise ValueError("Failed to establish connection to the database.")
#         
#         cur = conn.cursor()
# 
#         # Query to retrieve table ID from tool_existing_tbl_hdr
#         cur.execute("SELECT tbl_id FROM existing_tbl_hdr WHERE tbl_name = %s", (table_name,))
#         tbl_id = cur.fetchone()
#         if not tbl_id:
#             raise ValueError(f"Table '{table_name}' not found in existing_tbl_hdr.")
# 
#         tbl_id = tbl_id[0]
# 
#         # Query to retrieve fields from existing_tbl_itm
#         cur.execute("SELECT tbl_field, tbl_type, tbl_size FROM existing_tbl_itm WHERE tbl_id = %s", (tbl_id,))
#         rows = cur.fetchall()
# 
#         # Prepare data for DataFrame
#         data = []
#         for row in rows:
#             field_name, field_type, field_size = row
#             data.append((field_name, field_type, field_size if field_size else 'N/A'))
# 
#         # Create DataFrame
#         df = pd.DataFrame(data, columns=['column_name', 'column_type', 'column_size'])
# 
#         return df
# 
#     except Exception as e:
#         print(f"An error occurred: {e}")
#         message=f"An error occurred: {e}"
#         insert_error_log(cmp_id, connection_id,message, "extract_table_column_type")
#         return pd.DataFrame(columns=['column_name', 'column_type', 'column_size'])
# 
#     finally:
#         if conn:
#             conn.close()


def table_creation(df, row_data, attribute_names,attribute_details, table_name,
                   destination_engine, connection_id, updated_at, deleted_at, 
                    current_utc_time, is_last_chunk, chunk_number, 
                   destination_connection, table_exist,is_integration_checked,extra_fields,modified_fields,existing_fields,attribute_details_fields,cmp_id,migration_session_id=None,job_name=None, staging_table_name=None):
    
    primary_key = "ID"  # Default primary key
    snowflake_schema = None
    
    
    # df_column_types = extract_table_column_type(table_name.upper(),cmp_id, connection_id)
    # print("postgres column types retrieved",df_column_types)
    # df_column_types = extract_table_column_type(table_name.upper())


    dtype_conversion = {
    'guid': 'VARCHAR(36)',         # GUID/UUID mapped to VARCHAR(36)
    'dateTime': 'TIMESTAMP_TZ',    # Both dateTime and dateTimeOffset use TIMESTAMP_TZ
    'dateTimeOffset': 'TIMESTAMP_TZ',
    'int64': 'NUMBER(38,0)',       # Large integer
    'decimal': 'NUMBER(38,6)',            # Decimal mapped to FLOAT for compatibility
    'float': 'NUMBER(38,6)',              # Float type mapped to Snowflake FLOAT
    'string': 'VARCHAR(512)',      # String with a defined max length
    'boolean': 'BOOLEAN'           # Logical type
    }


    add_primary_key = False
    
    # Initialize maximum modified time tracker for this chunk
    
    
    # Retrieve existing modification time from PostgreSQL if table exists
    if not table_exist:
        add_primary_key = True
        
        
    current_utc_time = pd.to_datetime(current_utc_time, utc=True)
    inserts,deletes,updates = 0,0,0
    
    df = pd.concat([df, row_data], ignore_index=True, axis=0)

    print(f"[TRANSFORM] table={table_name} rows={len(df.index)} cols={len(df.columns)}")
    df = transformations(df, dtype_conversion, attribute_details,extra_fields,updated_at,deleted_at)
    df[updated_at] = current_utc_time
    # Fill missing values with False in the 'IS_DELETE' column
    df[deleted_at] = df['IS_DELETE'].fillna(False)
    # Only apply updates and inserts if data exists and table exists
    

    inserts,updates,deletes,snowflake_schema,table_name_final,staging_table,primary_key,df=snowflake_migration(df, table_name, 'ID', deletes, updates, table_exist, destination_engine, add_primary_key, is_last_chunk, destination_connection,deleted_at,updated_at,is_integration_checked,extra_fields,modified_fields,existing_fields,attribute_details_fields,connection_id,cmp_id,migration_session_id,job_name, staging_table_name=staging_table_name)
    # Return the max modified time from this chunk for comparison with other chunks
    return df, inserts,deletes, updates,snowflake_schema,table_name_final,staging_table,primary_key,df


# Snowflake SQL compiler limit is 200_000 expressions in a list. Stay well under it.
SNOWFLAKE_IN_LIST_BATCH_SIZE = 10000


def _format_snowflake_id_literals(ids):
    """Format ID values for a Snowflake IN (...) clause."""
    parts = []
    for id_val in ids:
        escaped = str(id_val).replace("'", "''")
        parts.append("'" + escaped + "'")
    return ", ".join(parts)


def _iter_id_batches(ids, batch_size=SNOWFLAKE_IN_LIST_BATCH_SIZE):
    unique_ids = list(dict.fromkeys(ids or []))
    size = max(1, int(batch_size or SNOWFLAKE_IN_LIST_BATCH_SIZE))
    for offset in range(0, len(unique_ids), size):
        yield unique_ids[offset : offset + size]


def _delete_snowflake_ids_in_batches(connection, schema, staging_table, delete_ids, batch_size=SNOWFLAKE_IN_LIST_BATCH_SIZE):
    """DELETE staging rows by ID in batches so IN (...) stays under Snowflake's 200k cap."""
    unique_ids = list(dict.fromkeys(delete_ids or []))
    if not unique_ids:
        return 0
    batches = 0
    for batch in _iter_id_batches(unique_ids, batch_size=batch_size):
        in_clause = _format_snowflake_id_literals(batch)
        delete_query = f"""
        DELETE FROM {schema}.{staging_table}
        WHERE ID IN ({in_clause})
        """
        connection.execute(text(delete_query))
        batches += 1
    connection.commit()
    _vprint(
        f"[SNOWFLAKE] batched_delete table={schema}.{staging_table} "
        f"ids={len(unique_ids)} batches={batches} batch_size={batch_size}"
    )
    return len(unique_ids)


def _fetch_snowflake_ids_in_batches(engine, qualified_table, ids_to_check, batch_size=SNOWFLAKE_IN_LIST_BATCH_SIZE):
    """Return IDs present in qualified_table, restricted to ids_to_check (batched IN queries)."""
    if not ids_to_check:
        return []

    unique_ids = list(dict.fromkeys(ids_to_check))
    found = []
    with engine.connect() as snowflake_connection:
        for batch_num, batch in enumerate(_iter_id_batches(unique_ids, batch_size=batch_size), start=1):
            in_clause = _format_snowflake_id_literals(batch)
            snowflake_query = f"""
                SELECT "ID"
                FROM {qualified_table}
                WHERE "ID" IN ({in_clause})
            """
            _vprint(f"Executing query (batch {batch_num}): {snowflake_query[:500]}...")
            snowflake_result = snowflake_connection.execute(text(snowflake_query))
            found.extend(row[0] for row in snowflake_result)
    return found


def retrieve_snowflake_recids(
    engine,
    table_name,
    schema,
    cmp_id,
    connection_id,
    staging_table_name=None,
    ids_to_check=None,
):

    if not staging_table_name:
        raise ValueError(f"CRITICAL ERROR: staging_table_name is missing in retrieve_snowflake_recids for table {table_name}")
    
    staging_table = f"{schema.upper()}.{staging_table_name}"

    if ids_to_check is not None:
        try:
            return _fetch_snowflake_ids_in_batches(engine, staging_table, ids_to_check)
        except KeyError as key_error:
            from uuid import uuid4
            debug_id = str(uuid4())
            context = (
                f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} "
                f"schema={schema} staging={staging_table} ids={len(ids_to_check)}"
            )
            print(f"[ERROR] {context} kind=KeyError detail={key_error}")
            message = f"Column not found: {str(key_error)} | {debug_id}"
            insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            raise
        except TypeError as type_error:
            from uuid import uuid4
            debug_id = str(uuid4())
            context = (
                f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} "
                f"schema={schema} staging={staging_table} ids={len(ids_to_check)}"
            )
            print(f"[ERROR] {context} kind=TypeError detail={type_error} hint='snowflake ocsp/cryptography mismatch likely'")
            message = f"TypeError: {str(type_error)} | {debug_id}"
            try:
                insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            except Exception:
                pass
            raise TypeError(message)
        except AttributeError as attr_error:
            from uuid import uuid4
            debug_id = str(uuid4())
            context = (
                f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} "
                f"schema={schema} staging={staging_table} ids={len(ids_to_check)}"
            )
            is_errno_bug = "'errno'" in str(attr_error)
            hint = "snowflake .errno handler bug; set insecure_mode=True or upgrade connector/cryptography" if is_errno_bug else ""
            print(f"[ERROR] {context} kind=AttributeError detail={attr_error} hint='{hint}'")
            message = f"AttributeError: {str(attr_error)} | {debug_id}"
            try:
                insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            except Exception:
                pass
            raise AttributeError(message)
        except SQLAlchemyError as sql_error:
            from uuid import uuid4
            debug_id = str(uuid4())
            context = (
                f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} "
                f"schema={schema} staging={staging_table} ids={len(ids_to_check)}"
            )
            print(f"[ERROR] {context} kind=SQLAlchemyError detail={sql_error}")
            message = f"Database error: {str(sql_error)} | {debug_id}"
            insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            raise
        except Exception as e:
            from uuid import uuid4
            debug_id = str(uuid4())
            context = (
                f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} "
                f"schema={schema} staging={staging_table} ids={len(ids_to_check)}"
            )
            et = type(e).__name__
            print(f"[ERROR] {context} kind={et} detail={e}")
            message = f"{et}: {str(e)} | {debug_id}"
            try:
                insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            except Exception:
                pass
            raise

    # Legacy path: full staging scan (avoid — pass ids_to_check from callers).
    logger.warning(
        "retrieve_snowflake_recids called without ids_to_check for %s.%s — full staging scan",
        schema, staging_table_name,
    )
    with engine.connect() as snowflake_connection:
        snowflake_query = f"""
            SELECT "ID"
            FROM {staging_table}
            
        """
        _vprint(f"Executing query: {snowflake_query}")
        try:
            snowflake_result = snowflake_connection.execute(text(snowflake_query))
            rows = list(snowflake_result)

            if not rows:
                return []

            # SQLAlchemy 2.x Rows no longer support string-key indexing (row["id"]).
            # Since the SELECT projects a single column, index positionally.
            snowflake_recids = [row[0] for row in rows]
            return snowflake_recids

        except KeyError as key_error:
            # Handle column name case sensitivity issues (concise single-line log)
            from uuid import uuid4
            debug_id = str(uuid4())
            context = f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} schema={schema} staging={staging_table}"
            keys = (rows[0].keys() if rows else 'no_rows')
            print(f"[ERROR] {context} kind=KeyError detail={key_error}; keys={keys} query={snowflake_query.strip()}")
            message = f"Column not found: {str(key_error)} | {debug_id}"
            insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            raise

        except TypeError as type_error:
            # Handle TypeError separately to avoid .errno access issues (concise log + hint)
            from uuid import uuid4
            debug_id = str(uuid4())
            context = f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} schema={schema} staging={staging_table}"
            print(f"[ERROR] {context} kind=TypeError detail={type_error} hint='snowflake ocsp/cryptography mismatch likely'")
            message = f"TypeError: {str(type_error)} | {debug_id}"
            try:
                insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            except:
                pass  # Ignore logging errors
            raise TypeError(message)

        except AttributeError as attr_error:
            # Snowflake handler sometimes tries to access .errno on a TypeError
            from uuid import uuid4
            debug_id = str(uuid4())
            context = f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} schema={schema} staging={staging_table}"
            is_errno_bug = "'errno'" in str(attr_error)
            hint = "snowflake .errno handler bug; set insecure_mode=True or upgrade connector/cryptography" if is_errno_bug else ""
            print(f"[ERROR] {context} kind=AttributeError detail={attr_error} hint='{hint}'")
            message = f"AttributeError: {str(attr_error)} | {debug_id}"
            try:
                insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            except:
                pass
            raise AttributeError(message)

        except SQLAlchemyError as sql_error:
            # Handle SQLAlchemy database errors (concise log)
            from uuid import uuid4
            debug_id = str(uuid4())
            context = f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} schema={schema} staging={staging_table}"
            print(f"[ERROR] {context} kind=SQLAlchemyError detail={sql_error}")
            message = f"Database error: {str(sql_error)} | {debug_id}"
            insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            raise

        except Exception as e:
            # Catch-all for other exceptions (concise single-line log)
            from uuid import uuid4
            debug_id = str(uuid4())
            context = f"debug_id={debug_id} func=retrieve_snowflake_recids table={table_name} schema={schema} staging={staging_table}"
            et = type(e).__name__
            print(f"[ERROR] {context} kind={et} detail={e}")
            message = f"{et}: {str(e)} | {debug_id}"
            try:
                insert_error_log(cmp_id, connection_id, message, "retrieve_snowflake_recids")
            except:
                pass  # Ignore logging errors
            raise



from sqlalchemy import Integer, Float, Boolean, String,Numeric

def map_pandas_to_snowflake_type(dtype,max_length):
    if pd.api.types.is_integer_dtype(dtype):
        return Integer  # Maps to Snowflake NUMBER(38,0)
    elif pd.api.types.is_float_dtype(dtype):
        return Integer  # Maps to Snowflake FLOAT
    elif pd.api.types.is_bool_dtype(dtype):
        return Boolean  # Maps to Snowflake BOOLEAN
    elif pd.api.types.is_datetime64_any_dtype(dtype):  # Matches any datetime64 type
        return TIMESTAMP_TZ  # Maps to Snowflake TIMESTAMP_TZ
    elif pd.api.types.is_string_dtype(dtype):
        return String(max_length)  # Maps to Snowflake VARCHAR(512)
    
    else:
        raise ValueError(f"Unsupported dtype: {dtype}. Please map it explicitly.")
    
def map_snowflake_to_sqlalchemy_type(snowflake_type):
    """
    Maps Snowflake types (like VARCHAR(512), NUMBER(38,0)) to SQLAlchemy types.
    """
    if snowflake_type.startswith("VARCHAR"):
        # Extract the length from the type, e.g., 'VARCHAR(512)'
        length = int(snowflake_type.split("(")[1].strip(")"))
        return String(length)  # SQLAlchemy String with length
    elif snowflake_type.startswith("NUMBER"):
        # Extract precision and scale if specified, e.g., 'NUMBER(38,6)'
        if "(" in snowflake_type:
            precision, scale = map(int, snowflake_type.split("(")[1].strip(")").split(","))
            return Numeric(precision=precision, scale=scale)  # SQLAlchemy Numeric with precision and scale
        return Integer  # Default to Integer if no precision/scale is specified
    elif snowflake_type.startswith("FLOAT"):
        return Numeric(precision=38, scale=6)  # Maps to Snowflake's FLOAT to SQLAlchemy Numeric with default precision/scale
    elif snowflake_type.startswith("TIMESTAMP_TZ"):
        return TIMESTAMP_TZ  # Maps to Snowflake's TIMESTAMP_TZ to SQLAlchemy TIMESTAMP
    elif snowflake_type == "BOOLEAN":
        return Boolean  # Maps to Snowflake's BOOLEAN to SQLAlchemy Boolean
    else:
        raise ValueError(f"Unsupported Snowflake type: {snowflake_type}")

from google.cloud import storage
from google.api_core.retry import Retry
import pandas as pd
import time
import logging

def upload_to_bucket(df: pd.DataFrame, table_name: str, connection_id: int) -> str:
    """
    Uploads a DataFrame to Google Cloud Storage as a CSV file with retry support.

    Args:
        df (pd.DataFrame): DataFrame to upload.
        table_name (str): Name of the destination table.
        connection_id (int): Unique identifier for tracking/logging.

    Returns:
        str: GCS path of the uploaded CSV file.
    """
    bucket_name = BUCKET_NAME
    temp_csv_blob_name = f"temp_data_dynamics_f&o_{table_name}_{connection_id}.csv"

    # Convert DataFrame to CSV string (QUOTE_NONNUMERIC avoids extra parsed columns from embedded commas).
    csv_data = df.to_csv(index=False, quoting=csv.QUOTE_NONNUMERIC)
    logging.info(f"✅ CSV conversion completed for table {table_name}")

    # Initialize storage client (reuse if defined globally)
    storage_client = storage.Client()

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(temp_csv_blob_name)

    # Define retry config: max ~60s total wait with exponential backoff
    retry = Retry(
        initial=1.0,         # initial delay (s)
        maximum=16.0,        # max delay (s)
        multiplier=2.0,      # exponential factor
        deadline=60.0        # total max retry window (s)
    )

    try:
        # Upload CSV string with retry
        blob.upload_from_string(csv_data, content_type='text/csv', retry=retry)
        logging.info(f"✅ Upload successful: gs://{bucket_name}/{temp_csv_blob_name}")
        return f"gcs://{bucket_name}/{temp_csv_blob_name}"

    except Exception as e:
        logging.error(f"❌ Failed to upload to GCS: {e}")
        raise  # Re-raise so caller can handle/log further


def _normalize_gcs_bucket_url(bucket_url: str) -> str:
    url = str(bucket_url or "").strip()
    if not url:
        return url
    if not url.endswith("/"):
        url = f"{url}/"
    return url


def _parse_storage_allowed_locations(desc_value: str) -> list:
    raw = str(desc_value or "").strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    locations = []
    for part in raw.split(","):
        loc = part.strip().strip("'\"")
        if loc:
            locations.append(_normalize_gcs_bucket_url(loc))
    return locations


def ensure_gcs_storage_integration(engine, integration_name, bucket_url,connection_id,cmp_id):
    
    try:
        bucket_url = _normalize_gcs_bucket_url(bucket_url)
        with engine.connect() as connection:
            check_integration_sql = f"""
            SHOW STORAGE INTEGRATIONS LIKE '{integration_name}';
            """
            result = connection.execute(text(check_integration_sql)).fetchall()

            if result:
                desc_rows = connection.execute(text(f"DESC INTEGRATION {integration_name}")).fetchall()
                allowed = []
                for row in desc_rows:
                    prop = row[0] if len(row) > 0 else None
                    val = row[2] if len(row) > 2 else None
                    if prop == "STORAGE_ALLOWED_LOCATIONS":
                        allowed = _parse_storage_allowed_locations(val)
                        break

                if bucket_url in allowed:
                    print(
                        f"Storage integration '{integration_name}' already allows {bucket_url}."
                    )
                    return result

                merged = list(dict.fromkeys(allowed + [bucket_url]))
                locations_sql = ", ".join(f"'{loc}'" for loc in merged)
                alter_sql = f"""
                ALTER STORAGE INTEGRATION {integration_name}
                SET STORAGE_ALLOWED_LOCATIONS = ({locations_sql});
                """
                print(
                    f"Extending storage integration '{integration_name}' "
                    f"with allowed location {bucket_url}..."
                )
                connection.execute(text(alter_sql))
                if hasattr(connection, "commit"):
                    connection.commit()
                print(f"Storage integration '{integration_name}' updated successfully.")
                return result

            print(f"Creating storage integration '{integration_name}'...")
            create_integration_sql = f"""
            CREATE STORAGE INTEGRATION {integration_name}
            TYPE = EXTERNAL_STAGE
            STORAGE_PROVIDER = 'GCS'
            ENABLED = TRUE
            STORAGE_ALLOWED_LOCATIONS = ('{bucket_url}');
            """
            connection.execute(text(create_integration_sql))
            if hasattr(connection, "commit"):
                connection.commit()
            print(f"Storage integration '{integration_name}' created successfully.")
            return None
    except Exception as e:
        print(f"Error creating storage integration: {str(e)}")
        message=f"Error creating storage integration: {str(e)}"
        insert_error_log(cmp_id, connection_id,message, "ensure_gcs_storage_integration")
        raise

from google.cloud import storage
from google.api_core.exceptions import Forbidden, NotFound

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

    except Forbidden as e:
        # Celery SA often has objectAdmin only; getIamPolicy/setIamPolicy need bucket IAM (e.g. legacyBucketOwner).
        warn = (
            f"Skipping automatic Snowflake SA IAM on bucket '{bucket_name}' (403): {e}. "
            f"Grant the Celery/GCS identity `storage.buckets.getIamPolicy` on this bucket, or manually add "
            f"Snowflake's STORAGE_GCP_SERVICE_ACCOUNT with roles/storage.objectAdmin. Migration may still work "
            f"if the Snowflake service account was already granted."
        )
        print(warn)
        insert_error_log(cmp_id, connection_id, warn, "ensure_service_account_and_permissions")
        return

    except Exception as e:
        error_message = f"Error ensuring permissions for '{integration_name}' on bucket '{bucket_name}': {str(e)}"
        print(error_message)
        insert_error_log(cmp_id, connection_id,error_message, "ensure_service_account_and_permissions")
        raise RuntimeError(error_message)

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
def map_pandas_to_snowflake_type_alter(dtype) -> str:
    """
    Maps pandas dtype to a Snowflake-compatible data type.
    
    Args:
        dtype: pandas dtype (e.g., `int64`, `float64`, `bool`, `object`, `datetime64`).
    
    Returns:
        str: Snowflake-compatible data type as a string.
    """
    if pd.api.types.is_integer_dtype(dtype):
        return "NUMBER(38, 0)"  # Snowflake equivalent of INTEGER
    elif pd.api.types.is_float_dtype(dtype):
        return "NUMBER(38, 6)"  # Snowflake FLOAT
    elif pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"  # Snowflake BOOLEAN
    elif pd.api.types.is_datetime64_any_dtype(dtype):  # Matches any datetime64 type
        return "TIMESTAMP_TZ"  # Snowflake TIMESTAMP_TZ
    elif pd.api.types.is_string_dtype(dtype):
        return "VARCHAR(512)"  # Snowflake VARCHAR(512)
    
    else:
        raise ValueError(f"Unsupported dtype: {dtype}. Please map it explicitly.")

from sqlalchemy import MetaData, Table, text


def _fetch_snowflake_column_names(destination_engine, snowflake_schema, table_name):
    """Return ordered Snowflake column names (uppercase) for a table."""
    with destination_engine.connect() as connection:
        column_order_query = text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = :table_name AND TABLE_SCHEMA = :schema_name
            ORDER BY ORDINAL_POSITION
        """)
        result = connection.execute(
            column_order_query,
            {"table_name": table_name.upper(), "schema_name": snowflake_schema.upper()},
        )
        return [str(row[0]).upper() for row in result]


def _sync_dataframe_columns_to_snowflake_table(
    destination_engine,
    snowflake_schema,
    physical_table_name,
    snowflake_table_qualified,
    df,
    existing_fields,
    attributes_details_field,
    cmp_id,
    connection_id,
):
    """
    ALTER ADD any DataFrame column that exists in the batch but not yet on the Snowflake table.
    Prevents COPY column-count mismatches when staging/base tables lag behind Dynamics exports.
    """
    if df is None or df.empty:
        return

    if df.columns.duplicated().any():
        dupes = list(df.columns[df.columns.duplicated()])
        logger.warning("[SCHEMA_SYNC] dropping duplicate DataFrame columns: %s", dupes[:15])

    df_cols = list(dict.fromkeys(str(c).upper() for c in df.columns))
    snowflake_cols = set(_fetch_snowflake_column_names(destination_engine, snowflake_schema, physical_table_name))
    missing = [c for c in df_cols if c not in snowflake_cols]
    if not missing:
        return

    logger.info(
        "[SCHEMA_SYNC] adding %s column(s) to %s",
        len(missing), snowflake_table_qualified,
    )
    _vprint(f"[SCHEMA_SYNC] columns: {missing[:10]}{'...' if len(missing) > 10 else ''}")
    df_upper = df.copy()
    df_upper.columns = [str(c).upper() for c in df_upper.columns]
    alter_table_add_column(
        destination_engine=destination_engine,
        snowflake_table_formatted=snowflake_table_qualified,
        snowflake_schema=snowflake_schema,
        table_name=physical_table_name,
        extra_fields=missing,
        modified_fields={},
        df=df_upper,
        existing_fields=existing_fields,
        cmp_id=cmp_id,
        connection_id=connection_id,
    )


def reorder_dataframe_to_match_snowflake_schema(df, destination_engine, snowflake_schema, table_name):
    """
    Reorders a DataFrame to match the column order in Snowflake.
    Drops columns not in the table and deduplicates headers so CSV column count matches staging.
    """
    _vprint("Fetching column order from Snowflake for reordering...")

    final_column_order = _fetch_snowflake_column_names(destination_engine, snowflake_schema, table_name)
    if not final_column_order:
        raise ValueError(
            f"No Snowflake columns found for {snowflake_schema}.{table_name} — cannot align DataFrame."
        )
    _vprint(f"Snowflake column count for {table_name}: {len(final_column_order)}")

    if df.columns.duplicated().any():
        dupes = list(df.columns[df.columns.duplicated()])
        logger.warning("Dropping duplicate DataFrame columns before COPY: %s", dupes[:15])
        df = df.loc[:, ~df.columns.duplicated()]

    df = df.copy()
    df.columns = [str(c).upper() for c in df.columns]

    extra_in_df = sorted(set(df.columns) - set(final_column_order))
    if extra_in_df:
        logger.warning(
            "Dropping %s DataFrame column(s) not in Snowflake table %s.%s",
            len(extra_in_df), snowflake_schema, table_name,
        )
        df = df.drop(columns=extra_in_df, errors="ignore")

    missing_columns = set(final_column_order) - set(df.columns)
    if missing_columns:
        _vprint(f"Warning: Missing columns in the DataFrame (will NULL-fill): {len(missing_columns)}")

    for column in missing_columns:
        df[column] = None

    reordered_df = df.reindex(columns=final_column_order, fill_value=None)
    if len(reordered_df.columns) != len(final_column_order):
        raise ValueError(
            f"DataFrame column alignment failed for {table_name}: "
            f"df_cols={len(reordered_df.columns)} snowflake_cols={len(final_column_order)}"
        )
    _vprint(
        f"DataFrame aligned to Snowflake schema: cols={len(reordered_df.columns)} "
        f"rows={len(reordered_df.index)}"
    )
    return reordered_df


def alter_table_add_column(destination_engine, snowflake_table_formatted, snowflake_schema, table_name, extra_fields, modified_fields, df, existing_fields,cmp_id, connection_id):
    """
    Alters a Snowflake table to add new columns and modify existing columns based on their types and sizes from the DataFrame.
    """
    _vprint("Starting the alter_table_add_column process... for ", snowflake_table_formatted)

    if not isinstance(extra_fields, list) or not isinstance(modified_fields, dict):
        raise ValueError("extra_fields must be a list and modified_fields must be a dictionary of column names and types.")

    # Transform extra fields by adding underscores
    extra_fields = [add_underscore(field) for field in extra_fields]
    _vprint(f"Transformed extra fields: {extra_fields}")

    # Fetch existing columns and their data types from Snowflake
    with destination_engine.connect() as connection:
        query = text(""" 
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = :table_name AND TABLE_SCHEMA = :schema_name
        """)
        result = connection.execute(query, {"table_name": table_name.upper(), "schema_name": snowflake_schema.upper()})
        existing_columns = {row[0].upper(): {"data_type": row[1], "max_length": row[2]} for row in result.fetchall()}

    _vprint(f"Existing columns in Snowflake: count={len(existing_columns)} table={table_name}")

    # Alter table and add new columns for extra fields
    for field in extra_fields:
        if field.upper() not in df.columns:
            _vprint(f"Field '{field}' does not exist in DataFrame. Skipping...")
            continue

        field_type = map_pandas_to_snowflake_type_alter(df[field].dtype)
        _vprint(f"Processing field '{field}' of type '{field_type}'...")

        if field.upper() not in existing_columns:
            alter_query = f"ALTER TABLE {snowflake_table_formatted} ADD COLUMN {field} {field_type}"
            _vprint(f"Executing query: {alter_query}")
            with destination_engine.connect() as connection:
                try:
                    connection.execute(text(alter_query))
                    _vprint(f"Added new column: {field} ({field_type})")
                except Exception as e:
                    logger.warning("Error adding column %s: %s", field, e)
                    error_message=f"Error adding column {field}: {e}"
                    insert_error_log(cmp_id, connection_id,error_message, "alter_table_add_column")
        else:
            _vprint(f"Column '{field}' already exists in the table.")

    # Identify and modify existing columns if their data type or size differs
    for field, expected_type in modified_fields.items():
        if field.upper() not in df.columns:
            _vprint(f"Field '{field}' does not exist in DataFrame. Skipping...")
            continue

        # Get the current type and size from the existing columns in Snowflake
        if field.upper() in existing_columns:
            # Combine the data type and size into a single string
            current_data_type = existing_columns[field.upper()]["data_type"]
            current_max_length = existing_columns[field.upper()]["max_length"]
            current_full_type = f"{current_data_type}({current_max_length})" if current_max_length else current_data_type

            _vprint(f"Current type of '{field}': {current_full_type}")

            # Only widen VARCHAR lengths; never shrink existing column size.
            expected_upper = str(expected_type).upper()
            current_upper = str(current_full_type).upper()
            expected_varchar = re.match(r"^VARCHAR\((\d+)\)$", expected_upper)
            current_varchar = re.match(r"^VARCHAR\((\d+)\)$", current_upper)

            if expected_varchar and current_varchar:
                expected_len = int(expected_varchar.group(1))
                current_len = int(current_varchar.group(1))
                if expected_len <= current_len:
                    _vprint(
                        f"Skipping modify for '{field}': existing VARCHAR({current_len}) "
                        f">= expected VARCHAR({expected_len})"
                    )
                    continue

            # Check if the expected type differs from the current type
            if expected_upper != current_upper:
                alter_query = f"ALTER TABLE {snowflake_table_formatted} MODIFY COLUMN {field} SET DATA TYPE {expected_type}"
                _vprint(f"Executing query: {alter_query}")
                with destination_engine.connect() as connection:
                    try:
                        connection.execute(text(alter_query))
                        _vprint(f"Modified column '{field}' to type {expected_type}")
                    except Exception as e:
                        logger.warning("Error modifying column %s: %s", field, e)
                        message=f"Error modifying column {field}: {e}"
                        insert_error_log(cmp_id, connection_id,message, "alter_table_add_column")

    _vprint("Completed altering table.")

def escape_backslashes(value):
    """
    Escapes single backslashes in string values by replacing them with double backslashes.
    """
    if isinstance(value, str):  # Only process strings
        return re.sub(r'\\', r'\\\\', value)
    return value  # Return non-string values unchanged


def _migration_copy_log_errors_enabled() -> bool:
    return (os.environ.get("MIGRATION_COPY_LOG_ERRORS", "0").strip().lower() in (
        "1", "true", "yes", "on",
    ))


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
    """
    Extract first_error + first_error_column_name robustly from Snowflake COPY
    validation rows (handles both mapping-style and positional rows).
    """
    err_dict = _snowflake_row_to_dict(err_row)
    first_error = str(err_dict.get("first_error", "") or "")
    error_col = str(err_dict.get("first_error_column_name", "") or "")

    # Fallback for positional rows when named keys are absent.
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


# Snowflake COPY options for Dynamics CSV loads. Empty fields must map to NULL for
# NUMBER/TIMESTAMP columns — otherwise Snowflake raises 100038 on blank cells.
_DYNAMICS_COPY_FILE_FORMAT = """
            TYPE = CSV
            FIELD_OPTIONALLY_ENCLOSED_BY = '"'
            SKIP_HEADER = 1
            ESCAPE_UNENCLOSED_FIELD = '\\\\'
            FIELD_DELIMITER = ','
            RECORD_DELIMITER = '\\n'
            TIMESTAMP_FORMAT = 'AUTO'
            DATE_FORMAT = 'AUTO'
            EMPTY_FIELD_AS_NULL = TRUE
            NULL_IF = ('', 'NULL', 'null', 'None')
            ENCODING = 'UTF8'
        """


def _is_copy_char_limit_error(first_error: str, err_dict: dict) -> bool:
    """
    Detect Snowflake COPY "value too long" errors from either human text
    (legacy payloads) or numeric/vendor codes (newer payloads).
    """
    fe = str(first_error or "").lower()
    if (
        "character length limit" in fe
        or "max length" in fe
        or "too long" in fe
    ):
        return True

    # Newer payloads may only expose error code(s), e.g. 100074.
    code_haystack = " ".join(str(v) for v in (err_dict or {}).values()).lower()
    return "100074" in code_haystack


def _log_snowflake_copy_rows(
    rows,
    *,
    label: str,
    table_name: str,
    cmp_id: int,
    connection_id: int,
    insert_db_log: bool = False,
) -> None:
    """Print COPY / VALIDATION result rows; optional insert into tool error log."""
    if not _migration_copy_log_errors_enabled():
        return
    if not rows:
        print(f"[COPY_DEBUG] {label} table={table_name} result_rows=0")
        return
    print(f"[COPY_DEBUG] {label} table={table_name} result_rows={len(rows)}")
    for i, row in enumerate(rows[:100]):
        payload = _snowflake_row_to_dict(row)
        line = f"[COPY_DEBUG] {label}[{i}] table={table_name} {payload}"
        print(line)
        if insert_db_log and i < 25:
            insert_error_log(
                cmp_id,
                connection_id,
                line[:4000],
                "copy_function",
            )


# def copy_function(df, table_name, flag, destination_engine, snowflake_schema, snowflake_table_formatted, is_integration_checked, extra_field,table_exist,modified_fields,existing_fields,attributes_details_field,connection_id):
#     """
#     Handles copying a DataFrame into Snowflake, including schema alignment and escaping.
#     """
#     print("Original df columns:", df.columns)

#     snowflake_stage = 'SAMPLE_STAGE'
#     DEMO_STORAGE ='DATASYNCHER3'
#     gcp_service_account='98150358561-compute@developer.gserviceaccount.com'
#     gcs_bucket_url='gcs://meta_data_bucket_ati_3/'
#     bucket_name="meta_data_bucket_ati_3"
#     project_id = "elt-project-433103"
    
    
#     display_name = "snowflake-integration-service-account"
#     role = ["roles/storage.objectAdmin"] 
    
    
        
    

#     # Schema alignment using alter_table_add_column
#     if extra_field:
#         print("Aligning schema with Snowflake...")
#         alter_table_add_column(
#             destination_engine=destination_engine,
#             snowflake_table_formatted=snowflake_table_formatted,
#             snowflake_schema=snowflake_schema,
#             table_name=table_name,
#             extra_fields=extra_field,
#             modified_fields=modified_fields,
#             df=df,
#             existing_fields=existing_fields
#         )

#     # Reorder DataFrame to match Snowflake schema
#     if table_exist:
#         df = reorder_dataframe_to_match_snowflake_schema(
#             df=df,
#             destination_engine=destination_engine,
#             snowflake_schema=snowflake_schema,
#             table_name=table_name
#         )
#     # Escape backslashes in the DataFrame
#     if not df.empty:
#         # Apply the escape function only if there's data
#         df = df.applymap(escape_backslashes)
#         print("Escaped DataFrame preview:")
#         print(df.head())
#     print("Escaped DataFrame preview:", df.head())
#     temp_csv_gcs_url = upload_to_bucket(df,table_name,connection_id)
#     print(f"Data uploaded to GCS at: {temp_csv_gcs_url}")
#     # Create table metadata
#     columns = []
#     metadata = MetaData()
#     # Normalize the column names in attribute_details_field to match the format we need (uppercase + underscore)
#     normalized_attribute_details = {
#     add_underscore(key).upper(): value for key, value in attributes_details_field.items()
#     }

#     print("normalized inside copy",normalized_attribute_details)

#     for col in df.columns:
#         # Normalize the column name to uppercase with underscores
#         adjusted_col_name = add_underscore(col).upper()

#         # Fetch metadata for the column (type of the column from attribute_details_field)
#         snowflake_type = normalized_attribute_details.get(adjusted_col_name)
#         if not snowflake_type:
#             raise ValueError(f"No metadata found for column '{adjusted_col_name}'")

#         # Map the Snowflake type to an SQLAlchemy type
#         sqlalchemy_type = map_snowflake_to_sqlalchemy_type(snowflake_type)

#         # Create the column with the appropriate SQLAlchemy type
#         column = Column(adjusted_col_name, sqlalchemy_type)
#         columns.append(column)


#     # Prevent empty table creation
#     if not columns:
#         raise ValueError("No columns available to create the table in Snowflake.")

#     your_dynamic_table = Table(
#         table_name.lower(), metadata, *columns,
#         extend_existing=True, schema=snowflake_schema.upper()
#     )

#     # Create table in Snowflake
#     print("Creating table in Snowflake...")
#     metadata.create_all(destination_engine)

#     # Check and apply storage integration
#     if not is_integration_checked:
#         res = ensure_gcs_storage_integration(destination_engine, DEMO_STORAGE, gcs_bucket_url)
#         if not res:
#             ensure_service_account_and_permissions(destination_engine, DEMO_STORAGE, bucket_name, project_id)
#         is_integration_checked = True 

#     # Snowflake COPY INTO command
#     copy_command = f"""
#         COPY INTO {snowflake_table_formatted}
#         FROM 'gcs://meta_data_bucket_ati_3/temp_data_dynamics_f&o_{table_name}_{connection_id}.csv'
#         FILE_FORMAT = (
#             TYPE = CSV
#             FIELD_OPTIONALLY_ENCLOSED_BY = '"'
#             SKIP_HEADER = 1
#             ESCAPE_UNENCLOSED_FIELD = '\\\\'
#         )
#         STORAGE_INTEGRATION = {DEMO_STORAGE}
#         ON_ERROR = CONTINUE;
#     """

#     with destination_engine.connect() as connection:
#         print(f"Executing Snowflake COPY INTO command")
#         result = connection.execute(copy_command)
#         status = result.fetchall()
#         print(f"Status: {status}")

from sqlalchemy import MetaData, Table, Column
import snowflake.connector

import re
from sqlalchemy.exc import SQLAlchemyError


def ensure_snowflake_base_table_exists(
    destination_engine,
    table_name,
    snowflake_schema,
    df,
    attributes_details_field,
    connection_id,
    cmp_id,
):
    """
    Create the Snowflake base (destination) table if it does not exist.
    Shared by copy_function (before staging LIKE) and snowflake_migration (before delete ID lookup).
    """
    if not attributes_details_field:
        return False

    with destination_engine.connect() as connection:
        connection.execute(text(f"USE SCHEMA {snowflake_schema};"))
        check_table_exists = f"""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{snowflake_schema.upper()}'
            AND TABLE_NAME = '{table_name.upper()}';
        """
        if connection.execute(text(check_table_exists)).fetchone()[0] > 0:
            return True

    columns = []
    metadata = MetaData()
    normalized_attribute_details = {
        add_underscore(key).upper(): value for key, value in attributes_details_field.items()
    }
    col_source = (
        list(df.columns)
        if df is not None and hasattr(df, "columns") and not df.columns.empty
        else list(attributes_details_field.keys())
    )
    for col in col_source:
        adjusted_col_name = add_underscore(col).upper()
        snowflake_type = normalized_attribute_details.get(adjusted_col_name)
        if not snowflake_type:
            raise ValueError(f"No metadata found for column '{adjusted_col_name}'")
        sqlalchemy_type = map_snowflake_to_sqlalchemy_type(snowflake_type)
        columns.append(Column(adjusted_col_name, sqlalchemy_type))

    if not columns:
        _vprint(f"[ENSURE_BASE] skip table={table_name}: no columns available")
        return False

    your_dynamic_table = Table(
        table_name.lower(),
        metadata,
        *columns,
        extend_existing=True,
        schema=snowflake_schema.upper(),
    )
    logger.info("[ENSURE_BASE] Creating base table %s.%s", snowflake_schema, table_name)
    metadata.create_all(destination_engine)
    logger.info("[ENSURE_BASE] Base table %s created", table_name)
    return True


def copy_function(df, table_name, flag, destination_engine, snowflake_schema, snowflake_table_formatted,snowflake_table_formatted_original,
                  is_integration_checked, extra_field, table_exist, modified_fields, existing_fields, 
                  attributes_details_field, connection_id, primary_key,snowflake_database,cmp_id,
                  migration_session_id=None, job_name=None, staging_table_name=None):
    """
    Handles copying a DataFrame into Snowflake, including dynamic table creation and staging.
    Merging is handled separately using `merge_into_snowflake`.
    """
    _vprint(f"copy_function table={table_name} df_cols={len(df.columns)}")

    # Using global constants for Snowflake configuration
    
    display_name = "snowflake-integration-service-account"
    role = ["roles/storage.objectAdmin"] 
    
    # Ensure schema exists
    with destination_engine.connect() as connection:
        schema_exists = connection.execute(
            text(
                f"SELECT COUNT(*) FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = '{snowflake_schema.upper()}';"
            )
        ).fetchone()[0]

        if schema_exists == 0:
            raise ValueError(f"Schema '{snowflake_schema}' does not exist in Snowflake.")

        connection.execute(text(f"USE SCHEMA {snowflake_schema};"))

    ensure_snowflake_base_table_exists(
        destination_engine,
        table_name,
        snowflake_schema,
        df,
        attributes_details_field,
        connection_id,
        cmp_id,
    )

    # Check and apply storage integration
    if not is_integration_checked:
        res = ensure_gcs_storage_integration(destination_engine, DEMO_STORAGE, GCS_BUCKET_URL,connection_id,cmp_id)
        if not res:
            ensure_service_account_and_permissions(destination_engine, DEMO_STORAGE, BUCKET_NAME, PROJECT_ID,cmp_id, connection_id)
        is_integration_checked = True 

    # Ensure schema alignment
    if extra_field or modified_fields:
        _vprint("snowflake table is been altered", snowflake_table_formatted)
        alter_table_add_column(
            destination_engine, snowflake_table_formatted_original, snowflake_schema,
            table_name, extra_field, modified_fields, df, existing_fields,cmp_id, connection_id
        )

    # Reorder DataFrame to match Snowflake schema
    # if table_exist:
    #     df = reorder_dataframe_to_match_snowflake_schema(
    #         df, destination_engine, snowflake_schema, table_name
    #     )

    

    

    # Define a staging table - strictly use the passed timestamped name
    staging_table = staging_table_name
    if not staging_table:
        raise ValueError(f"CRITICAL ERROR: staging_table_name is missing in copy_function for table {table_name}")

    # Step 1: Check if the staging table exists
    check_staging_table_sql = f"""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_SCHEMA = '{snowflake_schema.upper()}' 
        AND TABLE_NAME = '{staging_table.upper()}';
    """

    with destination_engine.connect() as connection:
        result = connection.execute(text(check_staging_table_sql)).fetchone()
        staging_table_exists = result[0] > 0

    # Step 2: Create the staging table if it doesn't exist
    if staging_table_exists:
        if extra_field or modified_fields:
            alter_table_add_column(
                destination_engine, snowflake_table_formatted, snowflake_schema,
                staging_table, extra_field, modified_fields, df, existing_fields,cmp_id, connection_id
            )
    else:
        create_staging_table = f"""
            CREATE OR REPLACE TABLE {snowflake_schema}.{staging_table} 
            LIKE {snowflake_schema}.{table_name};
        """
        with destination_engine.connect() as connection:
            logger.info("Creating staging table %s.%s", snowflake_schema, staging_table)
            connection.execute(text(create_staging_table))
            logger.info("Staging table %s.%s created", snowflake_schema, staging_table)

    # Sync any new Dynamics columns onto base + staging before CSV upload (prevents 295 vs 293 COPY errors).
    _sync_dataframe_columns_to_snowflake_table(
        destination_engine,
        snowflake_schema,
        table_name,
        snowflake_table_formatted_original,
        df,
        existing_fields,
        attributes_details_field,
        cmp_id,
        connection_id,
    )
    _sync_dataframe_columns_to_snowflake_table(
        destination_engine,
        snowflake_schema,
        staging_table,
        snowflake_table_formatted,
        df,
        existing_fields,
        attributes_details_field,
        cmp_id,
        connection_id,
    )

    # If a prior run left a stale empty staging DDL, refresh it from the base table.
    # Never CREATE OR REPLACE when staging already has columns/rows — that would
    # wipe chunk data mid-table.
    try:
        base_col_count = len(_fetch_snowflake_column_names(destination_engine, snowflake_schema, table_name))
        staging_col_count = len(_fetch_snowflake_column_names(destination_engine, snowflake_schema, staging_table))
        df_col_count = len(dict.fromkeys(str(c).upper() for c in df.columns))
        if staging_col_count == 0 and max(base_col_count, df_col_count) > 0:
            print(
                f"[SCHEMA_SYNC] creating empty staging table {snowflake_schema}.{staging_table}: "
                f"staging_cols={staging_col_count} base_cols={base_col_count} df_cols={df_col_count}"
            )
            with destination_engine.connect() as connection:
                connection.execute(
                    text(
                        f"CREATE OR REPLACE TABLE {snowflake_schema}.{staging_table} "
                        f"LIKE {snowflake_schema}.{table_name}"
                    )
                )
                connection.commit()
            _sync_dataframe_columns_to_snowflake_table(
                destination_engine,
                snowflake_schema,
                staging_table,
                snowflake_table_formatted,
                df,
                existing_fields,
                attributes_details_field,
                cmp_id,
                connection_id,
            )
        elif staging_col_count < max(base_col_count, df_col_count):
            print(
                f"[SCHEMA_SYNC] keeping existing staging {snowflake_schema}.{staging_table} "
                f"(no REPLACE mid-table); staging_cols={staging_col_count} "
                f"base_cols={base_col_count} df_cols={df_col_count}"
            )
    except Exception as refresh_err:
        print(f"[SCHEMA_SYNC] staging refresh check skipped: {refresh_err}")

    # Ensuring DataFrame matches staging table (exact column count for COPY)
    df = reorder_dataframe_to_match_snowflake_schema(df, destination_engine, snowflake_schema, staging_table)
    # Escape backslashes
    if not df.empty:
        df = df.applymap(escape_backslashes)

    # Ensure primary key exists (case-insensitive check)
    if primary_key.lower() not in map(str.lower, df.columns):
        raise ValueError(f"Primary key '{primary_key}' not found in DataFrame columns.")
    
    # Upload CSV to GCS
    # Step 1: Upload Data to GCS
    temp_csv_gcs_url = upload_to_bucket(df, table_name, connection_id)
    _vprint(f"Data uploaded to GCS at: {temp_csv_gcs_url}")

    # Step 2: Run COPY with Validation Mode to Get Failed Indexes
    validation_query = f"""
        COPY INTO {snowflake_schema}.{staging_table}
        FROM '{GCS_BUCKET_URL}temp_data_dynamics_f&o_{table_name}_{connection_id}.csv'
        FILE_FORMAT = ({_DYNAMICS_COPY_FILE_FORMAT})
        STORAGE_INTEGRATION = {DEMO_STORAGE}
        VALIDATION_MODE = RETURN_ERRORS;
    """

    failed_records = []
    copy_rows_loaded_for_progress = None
    with destination_engine.connect() as connection:
        try:
            _vprint(
                f"[COPY_DEBUG] table={table_name} schema={snowflake_schema} staging={staging_table} "
                f"df_rows={len(df.index)} df_cols={len(df.columns)}"
            )
            _vprint("Validating data before COPY...")
            result = connection.execute(text(validation_query))
            errors = result.fetchall()
            _vprint(f"[COPY_DEBUG] validation_error_count={len(errors)}")
            _log_snowflake_copy_rows(
                errors,
                label="validation_RETURN_ERRORS",
                table_name=table_name,
                cmp_id=cmp_id,
                connection_id=connection_id,
                insert_db_log=True,
            )

            if errors:
                column_hints = []
                resize_specs = {}
                for err_row in errors[:20]:
                    first_error, error_col, err_dict = _extract_copy_validation_error_fields(err_row)

                    # Example payload:
                    # "DS_TABLE_STAGING_..."["LOG":52]
                    m = re.search(r'\["([^"]+)":\d+\]', error_col)
                    col_name = m.group(1) if m else ""
                    has_char_limit_error = _is_copy_char_limit_error(first_error, err_dict)

                    # Best-effort column normalization against DataFrame columns.
                    if col_name and col_name not in df.columns:
                        col_name_upper = col_name.upper()
                        col_name_lower = col_name.lower()
                        for df_col in df.columns:
                            df_col_s = str(df_col)
                            if df_col_s.upper() == col_name_upper or df_col_s.lower() == col_name_lower:
                                col_name = df_col_s
                                break

                    if col_name and col_name in df.columns:
                        try:
                            max_len = int(df[col_name].fillna("").astype(str).map(len).max())
                            column_hints.append(
                                f"{col_name}: max_in_batch={max_len}, snowflake_error={first_error}"
                            )
                            if has_char_limit_error and max_len > 0:
                                # Safety margin for future rows in same run.
                                resize_specs[col_name] = min(max_len + 256, 16777216)
                        except Exception:
                            column_hints.append(f"{col_name}: snowflake_error={first_error}")
                    else:
                        if error_col or first_error:
                            column_hints.append(
                                f"column={error_col or 'unknown'} snowflake_error={first_error}"
                            )
                        # Keep raw row payload in hints to avoid "no column hints" blind spots.
                        if not (error_col or first_error):
                            column_hints.append(f"raw_validation_row={err_dict}")

                if resize_specs:
                    modified_fields = {
                        col_name: f"VARCHAR({int(new_len)})"
                        for col_name, new_len in resize_specs.items()
                    }
                    _vprint(
                        f"[COPY_DEBUG] auto_alter_columns table={table_name} "
                        f"modified_fields={modified_fields}"
                    )
                    # Reuse centralized schema alignment helper for both tables.
                    alter_table_add_column(
                        destination_engine=destination_engine,
                        snowflake_table_formatted=f"{snowflake_schema}.{staging_table}",
                        snowflake_schema=snowflake_schema,
                        table_name=staging_table,
                        extra_fields=[],
                        modified_fields=modified_fields,
                        df=df,
                        existing_fields=existing_fields,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                    )
                    alter_table_add_column(
                        destination_engine=destination_engine,
                        snowflake_table_formatted=f"{snowflake_schema}.{table_name}",
                        snowflake_schema=snowflake_schema,
                        table_name=table_name,
                        extra_fields=[],
                        modified_fields=modified_fields,
                        df=df,
                        existing_fields=existing_fields,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                    )
                    _vprint(
                        f"[COPY_DEBUG] auto_alter_complete table={table_name} "
                        f"columns={list(resize_specs.keys())}"
                    )

                    # Re-run validation once after widening columns.
                    result = connection.execute(text(validation_query))
                    errors = result.fetchall()
                    _vprint(f"[COPY_DEBUG] validation_error_count_after_auto_alter={len(errors)}")
                    _log_snowflake_copy_rows(
                        errors,
                        label="validation_after_auto_alter",
                        table_name=table_name,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        insert_db_log=True,
                    )

                if errors:
                    hint_block = "; ".join(column_hints) if column_hints else "no column hints"
                    strict_message = (
                        f"COPY validation failed for table={table_name} staging={snowflake_schema}.{staging_table} "
                        f"errors={len(errors)}. Retry attempted={bool(resize_specs)}. "
                        f"Details: {hint_block}"
                    )
                    logger.error("[COPY_DEBUG] %s", strict_message)
                    insert_error_log(cmp_id, connection_id, strict_message, "copy_function")
                    raise RuntimeError(strict_message)

            copy_to_staging = validation_query.replace(
                "VALIDATION_MODE = RETURN_ERRORS", "ON_ERROR = ABORT_STATEMENT"
            )
            schema_u = snowflake_schema.strip().upper()
            staging_u = staging_table.strip().upper()
            count_sql = _staging_count_sql(schema_u, staging_u)
            rows_before = connection.execute(text(count_sql)).fetchone()[0]
            _vprint(f"[COPY_DEBUG] staging_rows_before={rows_before} table={staging_u}")

            _vprint("Copying data from GCS to staging table (ON_ERROR=ABORT_STATEMENT)...")
            _vprint(f"[COPY_DEBUG] copy_sql={copy_to_staging}")
            result = connection.execute(text(copy_to_staging))
            copy_rows = result.fetchall() if result is not None else []
            rows_loaded_reported = _sum_copy_rows_loaded(copy_rows)
            _log_snowflake_copy_rows(
                copy_rows,
                label="copy_ON_ERROR_ABORT_STATEMENT",
                table_name=table_name,
                cmp_id=cmp_id,
                connection_id=connection_id,
                insert_db_log=True,
            )
            # Summarize typical Snowflake COPY result columns when present.
            for row in copy_rows[:5]:
                d = _snowflake_row_to_dict(row)
                vals = list(d.values())
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
                if not summary and len(vals) >= 7:
                    summary = {
                        "status": vals[1] if len(vals) > 1 else None,
                        "rows_parsed": vals[2] if len(vals) > 2 else None,
                        "rows_loaded": vals[3] if len(vals) > 3 else None,
                        "errors_seen": vals[5] if len(vals) > 5 else None,
                        "first_error": vals[6] if len(vals) > 6 else None,
                        "first_error_column_name": vals[8] if len(vals) > 8 else None,
                    }
                if summary:
                    _vprint(f"[COPY_DEBUG] copy_summary table={table_name} {summary}")
            _vprint("✅ COPY INTO finished (ON_ERROR=ABORT_STATEMENT).")
            _vprint(f"[COPY_DEBUG] copy_result_rowcount={result.rowcount if result else 'Unknown'}")

            # CRITICAL: Commit the COPY before COUNT. SQLAlchemy 2.x begins an
            # implicit transaction on `engine.connect()`; COUNT in the same
            # uncommitted transaction can see 0 rows for a large COPY.
            connection.commit()

            rows_after = connection.execute(text(count_sql)).fetchone()[0]
            rows_inserted = max(0, int(rows_after) - int(rows_before or 0))
            staging_count_after_copy = rows_after
            if rows_inserted == 0 and rows_loaded_reported > 0:
                rows_inserted = rows_loaded_reported
                staging_count_after_copy = max(rows_after, rows_before + rows_loaded_reported)
            _vprint(f"Staging row count after COPY: {staging_count_after_copy} (delta={rows_inserted})")
            _vprint(
                f"[COPY_DEBUG] post_copy_counts df_rows={len(df.index)} "
                f"staging_rows={staging_count_after_copy} rows_loaded_reported={rows_loaded_reported} "
                f"staging={schema_u}.{staging_u}"
            )
            if len(df.index) > 0 and rows_inserted == 0:
                raise RuntimeError(
                    f"COPY loaded 0 rows into staging table {schema_u}.{staging_u} "
                    f"from non-empty source dataframe ({len(df.index)} rows)."
                )
            logger.info(
                "[COPY] table=%s staging=%s.%s rows=%s",
                table_name, schema_u, staging_u, rows_inserted,
            )
            _vprint(
                f"[COPY_DEBUG] committed staging={schema_u}.{staging_u} rows={rows_inserted}"
            )
            copy_rows_loaded_for_progress = rows_inserted

        except Exception as e:
            logger.error("Error during data load for table=%s: %s", table_name, e)
            message = f"❌ Error during data load: {e}"
            insert_error_log(cmp_id, connection_id, message, "copy_function")
            raise

    # Progress tracker: pass batch delta to avoid a second Snowflake COUNT race.
    if all([migration_session_id, job_name, table_name]):
        try:
            progress_kwargs = dict(
                engine=destination_engine,
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                staging_table=staging_table,
                snowflake_schema=snowflake_schema,
            )
            if copy_rows_loaded_for_progress is not None:
                progress_kwargs["batch_rows"] = copy_rows_loaded_for_progress
            SimpleProgressTracker.update_staging_count(**progress_kwargs)
        except Exception as e:
            logger.warning("Failed to update progress tracking: %s", e)

    return staging_table, df



import re
import pandas as pd
from sqlalchemy import text

def merge_into_snowflake(
    destination_engine,
    snowflake_schema,
    table,
    primary_key,
    df,
    updated_at,
    deleted_at,
    cmp_id,
    connection_id,
    staging_table_name=None,
    table_detail_log=None,
):
    """
    Merges data from the staging table into the main Snowflake table.
    """
    max_sql = max(1024, int(os.environ.get("MIGRATION_MERGE_SQL_LOG_CHARS", "120000") or "120000"))

    def _mlog(msg):
        if table_detail_log is not None and table_detail_log.enabled():
            table_detail_log.log(f"merge_into_snowflake | {msg}")

    # Convert DataFrame columns to a list of strings
    columns = [col.strip() for col in df.columns.tolist()]

    # Ensure table name follows Snowflake naming conventions
    table_name = re.sub(r'(?<=[a-z])([A-Z])', r'_\1', table).upper()
    table_name = add_underscore(table_name)
    
    # Use the passed staging table name
    staging_table = staging_table_name
    if not staging_table:
        raise ValueError(f"CRITICAL ERROR: staging_table_name is missing in merge_into_snowflake for table {table}")
    logger.warning(
        "[MERGE] start table=%s target=%s.%s staging=%s.%s df_rows=%s n_cols=%s",
        table,
        snowflake_schema,
        table_name,
        snowflake_schema,
        staging_table,
        len(df.index),
        len(columns),
    )
    _mlog(
        f"resolved physical_table={table_name!r} staging={staging_table!r} schema={snowflake_schema!r} "
        f"primary_key={primary_key!r} df_rows={len(df.index)} n_cols={len(columns)}"
    )

    # Reliable staging-table existence check.
    check_staging_table = f"""
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = '{snowflake_schema.upper()}'
          AND TABLE_NAME = '{staging_table.upper()}'
    """

    # Debugging: Count rows before merge
    count_target_before = f"SELECT COUNT(*) FROM {snowflake_schema}.{table_name};"
    count_staging_before = f"SELECT COUNT(*) FROM {snowflake_schema}.{staging_table};"

    merge_command = f"""
    MERGE INTO {snowflake_schema}.{table_name} AS target
    USING {snowflake_schema}.{staging_table} AS source
    ON target.{primary_key} = source.{primary_key}
    WHEN MATCHED THEN
        UPDATE SET 
        {', '.join([
            f"target.{col} = CASE WHEN source.IS_DELETE THEN target.{col} ELSE source.{col} END"
            for col in columns if col.lower() != primary_key.lower() and col.lower() not in ['is_delete', 'sink_modified_on', deleted_at.lower(), updated_at.lower()]
        ])},
        target.IS_DELETE = source.IS_DELETE,
        target.SINK_MODIFIED_ON = source.SINK_MODIFIED_ON,
        target.{deleted_at} = source.{deleted_at},
        target.{updated_at} = source.{updated_at}
    WHEN NOT MATCHED THEN
        INSERT ({', '.join(columns)}) 
        VALUES ({', '.join([f'source.{col}' for col in columns])});
    """

    sql_preview = merge_command
    if len(sql_preview) > max_sql:
        sql_preview = (
            merge_command[:max_sql]
            + f"\n... ({len(merge_command) - max_sql} chars truncated; MIGRATION_MERGE_SQL_LOG_CHARS={max_sql})"
        )
    _mlog(f"merge_sql:\n{sql_preview}")

    count_target_after = f"SELECT COUNT(*) FROM {snowflake_schema}.{table_name};"

    with destination_engine.connect() as connection:
        try:
            _vprint(
                f"[MERGE_DEBUG] table={table_name} schema={snowflake_schema} staging={staging_table} "
                f"df_rows={len(df.index)} primary_key={primary_key}"
            )
            _vprint(f"[MERGE_DEBUG] merge_sql={merge_command}")
            staging_exists = connection.execute(text(check_staging_table)).fetchone()[0] > 0
            _vprint(f"[MERGE_DEBUG] staging_exists={staging_exists} table={snowflake_schema}.{staging_table}")
            _mlog(f"staging_exists={staging_exists} check_sql_snippet=INFORMATION_SCHEMA.TABLES")
            if not staging_exists:
                raise RuntimeError(
                    f"Staging table {snowflake_schema}.{staging_table} does not exist before merge."
                )

            target_before = connection.execute(text(count_target_before)).fetchone()[0]
            staging_before = connection.execute(text(count_staging_before)).fetchone()[0]
            logger.warning(
                "[MERGE] pre_merge table=%s target_before=%s staging_before=%s df_rows=%s",
                table_name,
                target_before,
                staging_before,
                len(df.index),
            )
            _mlog(
                f"pre_merge_counts target_before={target_before} staging_before={staging_before} "
                f"df_rows={len(df.index)}"
            )

            if len(df.index) > 0 and staging_before == 0:
                raise RuntimeError(
                    f"Staging table {snowflake_schema}.{staging_table} has 0 rows before merge "
                    f"while source dataframe has {len(df.index)} rows."
                )

            if staging_before > 0:
                logger.warning(
                    "[MERGE] executing table=%s staging_rows=%s target_before=%s",
                    table_name,
                    staging_before,
                    target_before,
                )
                result = connection.execute(text(merge_command))
                _vprint(f"Merge executed. Rows affected: {result.rowcount if result else 'Unknown'}")
                _mlog(f"merge_executed rowcount={result.rowcount if result else 'Unknown'}")
            else:
                logger.warning(
                    "[MERGE] sql_skipped table=%s reason=staging_empty staging_before=%s",
                    table_name,
                    staging_before,
                )
                _mlog(f"merge_sql_skipped staging_before={staging_before}")

            target_after = connection.execute(text(count_target_after)).fetchone()[0]
            logger.warning(
                "[MERGE] post_merge table=%s target_after=%s delta=%s",
                table_name,
                target_after,
                target_after - target_before,
            )
            _mlog(f"post_merge target_after={target_after} delta={target_after - target_before}")

            # Staging is dropped only after the whole table finishes in process_table.
            connection.commit()
            _vprint(
                f"[MERGE_DEBUG] committed merge table={snowflake_schema}.{table_name} "
                f"staging_kept={snowflake_schema}.{staging_table}"
            )
            _mlog("merge_committed_staging_kept")
            logger.warning(
                "[MERGE] committed table=%s staging_kept=%s target_after=%s",
                table_name,
                staging_table,
                target_after,
            )

        except Exception as e:
            try:
                _mlog(f"MERGE_EXCEPTION: {e!r}\n{traceback.format_exc()}")
            except Exception:
                pass
            logger.error(
                "[MERGE] failed table=%s staging=%s.%s df_rows=%s error=%s",
                table_name,
                snowflake_schema,
                staging_table,
                len(df.index),
                e,
            )
            message=f"Error during upsert operation: {e}"
            insert_error_log(cmp_id, connection_id,message, "merge_into_snowflake")
            raise


def check_primary_key_exists(destination_engine, table_name):
    query = f"""
    SELECT constraint_name 
    FROM information_schema.table_constraints
    WHERE table_name = '{table_name}' AND constraint_type = 'PRIMARY KEY'
    """
    
    with destination_engine.connect() as conn:
        result = conn.execute(text(query)).fetchone()
    return result is not None  # Returns True if a primary key exists


from sqlalchemy.exc import OperationalError
def retrieve_snowflake_ids_delete(engine, table_name, schema, ids_to_check,connection_id,cmp_id):
    table = f"{schema.upper()}.{table_name.upper()}"
    if not ids_to_check:
        return []

    try:
        return _fetch_snowflake_ids_in_batches(engine, table, ids_to_check)
    except (OperationalError, ProgrammingError) as e:
        err = str(e).lower()
        if "42s02" in err or "does not exist" in err or "not authorized" in err:
            logger.warning("Table %s missing for delete lookup: %s", table, e)
            message_1 = f"Table {table} missing or not accessible for delete lookup: {e}"
            insert_error_log(cmp_id, connection_id, message_1, "retrieve_snowflake_ids_delete")
            return []
        raise
    except Exception as e:
        logger.error("Error in retrieve_snowflake_ids_delete: %s", e)
        message=f"Error executing query or processing result: {e}"
        insert_error_log(cmp_id, connection_id,message, "retrieve_snowflake_ids_delete")
        raise

def snowflake_migration(df, snowflake_table, primary_key_column,deletes_lst,updates_lst,table_exist,destination_engine,add_primary_key,is_last_chunk,destination_connection,deleted_at,updated_at,is_integration_checked,extra_fields,modified_fields,existing_fields,attributes_details_field,connection_id,cmp_id,migration_session_id=None,job_name=None, staging_table_name=None):
    
    common_ids=[]

    ids_with_conflict = df.groupby('ID')['IS_DELETE'].nunique().loc[lambda x: x > 1].index
    
    # Use passed staging table name (strictly enforced)
    staging_table = staging_table_name
    
    if not staging_table:
        raise ValueError(f"CRITICAL ERROR: staging_table_name is missing in snowflake_migration for table {snowflake_table}")
    
    inserts_count = 0
    updates_count = 0
    deletes_count = 0
    
    engine = destination_engine
    if df.shape[0] > 0:
        df['SINK_MODIFIED_ON'] = pd.to_datetime(df['SINK_MODIFIED_ON'], utc=True)

                    # Sort by ID (ascending) and SINK_MODIFIED_ON (descending)
        df = df.sort_values(by=['ID', 'SINK_MODIFIED_ON'], ascending=[True, False])

                    # Keep only the first row for each ID (max SINK_MODIFIED_ON)
        df_before_dedupe = df
        df = df.drop_duplicates(subset=['ID'], keep='first').reset_index(drop=True)
        print(f"[SNOWFLAKE] table={snowflake_table} deduped_rows={len(df.index)} table_exist={table_exist}")
    with engine.connect() as connection:
        # SQLAlchemy 2.x requires sqlalchemy.text() for raw SQL strings.
        # snowflake_schema = connection.execute(text("SELECT CURRENT_SCHEMA()")).scalar()
        snowflake_database = connection.execute(text("SELECT CURRENT_DATABASE()")).scalar()
        snowflake_schema = destination_connection.get('schema')
        snowflake_table_formatted = f'{snowflake_database}.{snowflake_schema}."{staging_table}"'
        snowflake_table_formatted_original=f'{snowflake_database}.{snowflake_schema}."{snowflake_table.upper()}"'

        table_exist=check_staging_table_exists(destination_engine, snowflake_table, destination_connection,cmp_id, connection_id, staging_table_name=staging_table)
        _vprint(
            f"[SNOWFLAKE] table={snowflake_table} schema={snowflake_schema} staging={staging_table} "
            f"df_rows={len(df.index)} staging_exists={table_exist}"
        )

        #handling updates
        if table_exist:
            if df.shape[0] > 0:
                _vprint(f"[SNOWFLAKE] updates_pass table={snowflake_table} rows_to_process={len(df.index)}")

                id_list = df['ID'].unique().tolist()
                
                # Only look up IDs from this chunk in staging (not the full table).
                snowflake_recids = retrieve_snowflake_recids(
                    destination_engine,
                    snowflake_table,
                    snowflake_schema,
                    cmp_id,
                    connection_id,
                    staging_table_name=staging_table,
                    ids_to_check=id_list,
                )
                
                common_ids = snowflake_recids
                _vprint(
                    f"[SNOWFLAKE] id_overlap table={snowflake_table} df_unique_ids={len(id_list)} "
                    f"snowflake_existing_ids={len(snowflake_recids)} common_ids={len(common_ids)}"
                )

                if common_ids:
                    df_common = df[df['ID'].isin(common_ids)]
                    _vprint(f"[SNOWFLAKE] pre_delete table={snowflake_table} common_rows={len(df_common.index)}")
                    if not df_common.empty:
                        delete_ids = df_common['ID'].unique().tolist()
                        _delete_snowflake_ids_in_batches(
                            connection,
                            snowflake_schema,
                            staging_table,
                            delete_ids,
                        )
                        try:
                            staging_after_delete = connection.execute(
                                text(f"SELECT COUNT(*) FROM {snowflake_schema}.{staging_table}")
                            ).fetchone()[0]
                        except Exception as _e:
                            staging_after_delete = -1
                        _vprint(
                            f"[SNOWFLAKE] post_delete table={snowflake_schema}.{staging_table} "
                            f"deleted_ids={len(delete_ids)} staging_rows_after={staging_after_delete}"
                        )

        if table_exist:
            try:
                staging_before_return = connection.execute(
                    text(f"SELECT COUNT(*) FROM {snowflake_schema}.{staging_table}")
                ).fetchone()[0]
            except Exception as _e:
                staging_before_return = -1
            _vprint(
                f"[SNOWFLAKE] before_return table={snowflake_table} "
                f"staging={snowflake_schema}.{staging_table} staging_rows={staging_before_return} "
                f"df_rows={len(df.index)}"
            )
        else:
            _vprint(
                f"[SNOWFLAKE] before_return table={snowflake_table} staging_not_yet_created "
                f"staging={snowflake_schema}.{staging_table} df_rows={len(df.index)}"
            )

                
        is_delete_true_df = df[df['IS_DELETE'] == True] if df.shape[0] > 0 else pd.DataFrame()
        if not is_delete_true_df.empty:
            ids_to_check = is_delete_true_df['ID'].tolist()
            # Ensure base exists before delete ID lookup (same as copy_function before staging LIKE).
            if attributes_details_field:
                ensure_snowflake_base_table_exists(
                    destination_engine,
                    snowflake_table,
                    snowflake_schema,
                    df,
                    attributes_details_field,
                    connection_id,
                    cmp_id,
                )
            original_table_ids = retrieve_snowflake_ids_delete(
                destination_engine, snowflake_table, snowflake_schema, ids_to_check, connection_id, cmp_id
            )
            # During a run, rows loaded earlier live in staging until merge — include those IDs too.
            if table_exist and staging_table:
                staging_ids = retrieve_snowflake_ids_delete(
                    destination_engine, staging_table, snowflake_schema, ids_to_check, connection_id, cmp_id
                )
                original_table_ids = list(set(original_table_ids) | set(staging_ids))
        else:
            original_table_ids = []
        # Filter rows where IS_DELETE is True and ID is in ids_with_conflict.union(common_ids)
        # if common_ids:
        #     true_filter = (df['IS_DELETE'] == True) & (df['ID'].isin(ids_with_conflict.union(common_ids).union(original_table_ids)))
        # else:
        #     true_filter = (df['IS_DELETE'] == True) & (df['ID'].isin(ids_with_conflict.union(original_table_ids)))

        # # Retain the filtered rows for True and all other rows as they are
        # df = df[true_filter | (df['IS_DELETE'] == False)]

        df_before_delete_filter = df
        df = df[(df['IS_DELETE'] == False) | (df['ID'].isin(original_table_ids))]
        delete_df = df[df['IS_DELETE'] == True]

        # Count deletes
        deletes_count = len(delete_df)

        # Total rows in the DataFrame
        total_rows = len(df)

        

        # Calculate inserts
        inserts_count = total_rows - deletes_count 

        # Adjust inserts count if there are common IDs
        if common_ids:  # Only proceed if common_ids is non-empty

            # Rows that overlap between the new data and existing data
            common_ids_count = len(common_ids)
            inserts_count -=  common_ids_count
            # Count rows in common_ids that are also marked as deletes
            common_deletes_count = len(delete_df[delete_df['ID'].isin(common_ids)])
            # Add back rows that are both common and deleted
            inserts_count += common_deletes_count
        deletes_count= len(delete_df)

        
        if table_exist == False:
            # if df.shape[0]>0:
            # inserts_count=len(df)
            staging_table,df = copy_function(df,snowflake_table,True,destination_engine,snowflake_schema,snowflake_table_formatted,snowflake_table_formatted_original,is_integration_checked,extra_fields,table_exist,modified_fields,existing_fields,attributes_details_field,connection_id,'ID',snowflake_database,cmp_id,migration_session_id,job_name, staging_table_name=staging_table_name)
        
        # table_updation
        else:
            if df.shape[0]>0:
                
                # inserts_count = len(df) 
                staging_table,df = copy_function(df,snowflake_table,False,destination_engine,snowflake_schema,snowflake_table_formatted,snowflake_table_formatted_original,is_integration_checked,extra_fields,table_exist,modified_fields,existing_fields,attributes_details_field,connection_id,'ID',snowflake_database,cmp_id,migration_session_id,job_name, staging_table_name=staging_table_name)
                
                # 🧪 TEST EXCEPTION: Uncomment to test staging table cleanup
                #raise Exception("🧪 TEST: Verifying staging table cleanup works")
                
#             
        primary_key_exists = check_primary_key_exists(destination_engine, snowflake_table)
    
        # Only add the primary key if it does not already exist
        if add_primary_key and not primary_key_exists:
            primary_key_sql = f"ALTER TABLE {snowflake_table_formatted} ADD PRIMARY KEY ({primary_key_column})"
            primary_key_sql_original = f"ALTER TABLE {snowflake_table_formatted_original} ADD PRIMARY KEY ({primary_key_column})"
            try:
                connection.execute(text(primary_key_sql))
                connection.execute(text(primary_key_sql_original))
            except ProgrammingError as e:
                _vprint(f"Primary key already exists for table {snowflake_table}: {e}")
                
        else:
            _vprint(f"Primary key already exists for {snowflake_table}, skipping the addition.")
    return inserts_count, updates_count, deletes_count,snowflake_schema,snowflake_table,staging_table,primary_key_column,df

