"""
Destination-agnostic Microsoft Dynamics (Dataverse / F&O) source helpers.

Extracted from microsoft_dynamics_to_databricks_migration_service.py so Snowflake
and Databricks orchestrators can share Azure blob listing, Postgres cursor/state,
CSV chunk reading, transforms, and GCS temp-upload utilities.

Destination-coupled logic (process_file_in_chunks → table_creation / snowflake_migration,
destination engines, MERGE/COPY) stays in the orchestrator modules.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import adlfs
import pandas as pd
import psycopg2
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from google.api_core.retry import Retry
from google.cloud import storage

from utils.azure_path_utils import (
    build_abfss_uri,
    dataverse_csv_processing_order_key,
    first_date_directory_in_blob_path,
    infer_dataverse_base_folder_from_paths,
    is_dataverse_delete_export_csv,
    join_azure_blob_path,
    list_dataverse_date_folder_names,
    parse_abfss_uri,
)
from utils.gcp_utils import delete_blob_from_bucket

logger = logging.getLogger(__name__)

# Active bulk ADLS staging copies on this pod (for copy-worker scaling).
_BULK_COPY_ACTIVE_COUNT = 0
_BULK_COPY_ACTIVITY_LOCK = threading.Lock()

for _noisy_logger in (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.storage",
    "azure.storage.blob",
    "azure.identity",
    "urllib3",
):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

# =============================================================================
# GLOBAL CONSTANTS / ENV (listing + GCS temp CSV)
# =============================================================================

# LISTING_*: delimiter scan for date folders (see fetch_blobs_for_prefix)
LISTING_USE_DELIMITER_SCAN = os.environ.get("LISTING_USE_DELIMITER_SCAN", "true")

GCS_BUCKET_URL = os.environ.get("GCS_BUCKET_URL", "")
BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")

S3_BUCKET_URL = os.environ.get("S3_BUCKET_URL", "")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "")

AZURE_STAGING_CONNECTION_STRING = os.environ.get("AZURE_STAGING_CONNECTION_STRING", "")
DATASYNCHER_AZURE_STAGING_URL = os.environ.get("DATASYNCHER_AZURE_STAGING_URL", "")

_azure_staging_container_client = None
_s3_client = None


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


def _gcs_project_id() -> str | None:
    return (
        os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or None
    )


def _gcs_client_from_env_json() -> storage.Client | None:
    """
    Prefer explicit SA JSON (``DATABRICKS_GCS_SA_JSON``) over GCE metadata.

    Flow worker pods receive this from the ``databricks-gcs-sa-json`` secret.
    """
    raw = str(os.environ.get("DATABRICKS_GCS_SA_JSON") or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        from google.oauth2 import service_account

        info = json.loads(raw)
        project_id = _gcs_project_id() or str(info.get("project_id") or "").strip() or None
        creds = service_account.Credentials.from_service_account_info(info)
        client = storage.Client(project=project_id, credentials=creds)
        logger.warning(
            "DBX|gcs_client_ready | mode=env_json | project_id=%s",
            project_id or "(default)",
        )
        return client
    except Exception as exc:
        logger.warning("DBX|gcs_client_env_json_failed | err=%s", exc)
        return None


def _gcs_client_from_adc() -> storage.Client:
    """Use ADC / Workload Identity; wait for metadata on freshly scaled nodes."""
    os.environ.setdefault("GOOGLE_CLOUD_UNIVERSE_DOMAIN", "googleapis.com")
    project_id = _gcs_project_id()
    metadata_ok = _wait_for_gce_metadata(timeout_secs=45)
    logger.warning(
        "DBX|gcs_client_metadata_wait | ready=%s | project_id=%s",
        metadata_ok,
        project_id or "(default)",
    )
    last_err = None
    for _ in range(5):
        try:
            return storage.Client(project=project_id) if project_id else storage.Client()
        except Exception as exc:
            last_err = exc
            time.sleep(2)
    raise RuntimeError(
        f"GCS Workload Identity / metadata auth failed after retries: {last_err}"
    )


def _build_gcs_storage_client():
    """Build GCS client: env SA JSON → GOOGLE_APPLICATION_CREDENTIALS → metadata ADC."""
    adc_path = str(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if adc_path and os.path.isfile(adc_path):
        project_id = _gcs_project_id()
        logger.warning(
            "DBX|gcs_client_ready | mode=adc_file | path=%s | project_id=%s",
            adc_path,
            project_id or "(default)",
        )
        return storage.Client(project=project_id) if project_id else storage.Client()

    client = _gcs_client_from_env_json()
    if client is not None:
        return client

    return _gcs_client_from_adc()


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


def _migration_verbose_logs():
    return os.environ.get("MIGRATION_VERBOSE_LOGS", "0").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
        "enabled",
    )


def _vprint(*args, **kwargs):
    """Expensive debug prints; enable with MIGRATION_VERBOSE_LOGS=1."""
    if _migration_verbose_logs():
        print(*args, **kwargs)


# =============================================================================
# Per-table migration detail file log (used by update_table_status / process_table)
# =============================================================================


def _migration_table_detail_log_dir():
    """
    Per-table migration detail files (text) for troubleshooting.
    Enable with MIGRATION_TABLE_DETAIL_LOG=1|true|on|yes|enabled.
    Override directory with MIGRATION_TABLE_LOG_DIR (created if missing).
    Default log root: <temp>/ds_migration_table_logs
    """
    flag = os.environ.get("MIGRATION_TABLE_DETAIL_LOG", "0").strip().lower()
    if flag not in ("1", "true", "on", "yes", "enabled"):
        return None
    explicit = os.environ.get("MIGRATION_TABLE_LOG_DIR", "").strip()
    if explicit:
        return explicit
    return os.path.join(tempfile.gettempdir(), "ds_migration_table_logs")


def _migration_table_log_gcs_enabled():
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
        fname = (
            f"{safe_table}_conn{connection_id}_sess{migration_session_id}_cmp{cmp_id}_"
            f"{job_digest}_{run_ts_slug}.log"
        )
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


# =============================================================================
# Azure / Postgres / date-folder helpers
# =============================================================================


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
    Inserts a log entry into the `error_log_entry` table.

    Returns:
        int: The newly inserted log ID, or None if an error occurs.
    """
    connection = None
    cursor = None
    try:
        connection = postgres_connection()
        if connection is None:
            print("Failed to establish connection to the database.")
            return None

        cursor = connection.cursor()
        current_time = datetime.utcnow()
        log_insert_query = """
            INSERT INTO error_log_entry (cmp_id, connection_id, message, function_name, created_on)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """
        cursor.execute(
            log_insert_query,
            (cmp_id, connection_id, message, function_name, current_time),
        )
        log_id = cursor.fetchone()[0]
        connection.commit()
        print(f"Error log entry inserted successfully with log_id: {log_id}")
        return log_id

    except psycopg2.Error as e:
        print(f"Error inserting into errorlog_entry: {str(e)}")
        return None

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


def get_last_checked_date_directories(connection_id, cmp_id):
    """
    Fetches the last checked date directories for a given connection ID using raw SQL.

    Returns:
        The maximum (latest) date directory, or None if no directories exist.
    """
    try:
        _vprint(f"[DEBUG] Fetching directories for connection_id: {connection_id}")

        conn = postgres_connection()
        cursor = conn.cursor()

        sql_query = """
        SELECT directories
        FROM date_directory
        WHERE connection_id = %s;
        """
        cursor.execute(sql_query, (connection_id,))
        row = cursor.fetchone()
        _vprint(f"[DEBUG] Fetched row: {row}")

        if not row:
            _vprint("[DEBUG] No directories found for this connection.")
            cursor.close()
            conn.close()
            return None

        directories_data = row[0]
        _vprint(f"[DEBUG] Type of directories_data: {type(directories_data)}")

        if isinstance(directories_data, str):
            try:
                date_directories = json.loads(directories_data)
            except json.JSONDecodeError as e:
                print(f"[ERROR] Failed to decode JSON: {str(e)}")
                cursor.close()
                conn.close()
                return None
        elif isinstance(directories_data, list):
            date_directories = directories_data
        else:
            print(f"[ERROR] Unexpected data type: {type(directories_data)}")
            cursor.close()
            conn.close()
            return None

        _vprint(f"[DEBUG] Extracted directories before sorting: {date_directories}")
        date_directories.sort(key=lambda x: datetime.strptime(x, "%Y-%m-%dT%H.%M.%SZ"))
        max_date = date_directories[-1] if date_directories else None
        _vprint(f"[DEBUG] Max date found: {max_date}")

        cursor.close()
        conn.close()
        return max_date

    except Exception as e:
        print(f"[ERROR] Error fetching date directories: {str(e)}")
        message = f"[ERROR] Error fetching date directories: {str(e)}"
        insert_error_log(cmp_id, connection_id, message, "get_last_checked_date_directories")
        return None


def get_all_date_directories_from_db(connection_id, cmp_id=0):
    """
    Return the full sorted list stored in date_directory.directories for a connection.
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
    """
    _vprint(f"[DEBUG] Connecting to PostgreSQL for connection_id: {connection_id}")
    conn = postgres_connection()
    cursor = conn.cursor()
    _vprint("[DEBUG] Connected to database successfully")
    cursor.execute(
        """
        SELECT directories FROM date_directory WHERE connection_id = %s;
    """,
        (connection_id,),
    )

    row = cursor.fetchone()
    _vprint(f"[DEBUG] Retrieved row: {row}")
    if row:
        _vprint(f"[DEBUG] Type of row[0]: {type(row[0])}")

    existing_directories = row[0] if row and row[0] else []
    _vprint(f"[DEBUG] Existing directories: {existing_directories}")
    if not isinstance(new_directories, list):
        print("[ERROR] new_directories is not a list!")
        return "Error: new_directories must be a list."
    updated_directories = list(set(existing_directories + new_directories))
    _vprint(f"[DEBUG] Merged directories before saving: {updated_directories}")
    updated_json = json.dumps(updated_directories)
    _vprint(f"[DEBUG] JSON to be stored: {updated_json}")
    if row:
        cursor.execute(
            """
            UPDATE date_directory
            SET directories = %s, updated_at = %s
            WHERE connection_id = %s;
        """,
            (updated_json, datetime.utcnow(), connection_id),
        )
        _vprint(f"[DEBUG] Updated record for connection_id: {connection_id}")
    else:
        cursor.execute(
            """
            INSERT INTO date_directory (connection_id, directories, created_at, updated_at)
            VALUES (%s, %s, %s, %s);
        """,
            (connection_id, updated_json, datetime.utcnow(), datetime.utcnow()),
        )
        _vprint(f"[DEBUG] Inserted new record for connection_id: {connection_id}")
    conn.commit()
    cursor.close()
    conn.close()
    _vprint(f"[DEBUG] Final date directories: {updated_directories}")
    return sorted(updated_directories)


def get_last_checked_dates_and_fields(connection_id, selected_tables=None):
    """
    Fetches the last checked dates and table fields for specific tables associated
    with a given connection ID.

    Returns:
        dict: {table_name: {"last_checked_date": ..., "table_fields": ...}}
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()

        base_query = """
        SELECT "table", last_synced, table_fields
        FROM source_table_selection
        WHERE connection_id = %s
        """

        if selected_tables:
            placeholders = ", ".join(["%s"] * len(selected_tables))
            base_query += f' AND "table" IN ({placeholders})'
            query_params = [connection_id] + selected_tables
        else:
            query_params = [connection_id]

        cursor.execute(base_query, tuple(query_params))
        rows = cursor.fetchall()

        last_checked_data = {}
        for row in rows:
            table_name = row[0]
            last_checked_date = row[1] if row[1] else None
            table_fields = row[2] if row[2] else None
            parsed_fields = parse_table_fields(table_fields)
            last_checked_data[table_name] = {
                "last_checked_date": last_checked_date,
                "table_fields": parsed_fields,
            }

        return last_checked_data

    except Exception as e:
        print(f"Error fetching data for connection_id {connection_id}: {e}")
        return {}

    finally:
        if "cursor" in locals():
            cursor.close()
        if "conn" in locals():
            conn.close()


def fetch_blob_names(container_client, prefix=None):
    """
    Fetches blob names from Azure Blob Storage with an optional prefix filter.
    """
    blob_names = []
    blobs = container_client.list_blobs(name_starts_with=prefix) if prefix else container_client.list_blobs()

    for blob in blobs:
        blob_names.append(blob.name)

    print("Blob names fetching completed:", len(blob_names))
    return blob_names


def fetch_blobs_for_prefix(
    container_client, prefix, safety_interval_cutoff, cmp_id, connection_id, base_folder=None
):
    """Discover date directories via delimiter listing, with blob-iteration fallback."""
    date_directories = set()
    use_delimiter = os.environ.get("LISTING_USE_DELIMITER_SCAN", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
        "enabled",
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
            _vprint(f"[Process {os.getpid()}] Delimiter listing unique_dirs={len(date_directories)}")
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
            container_client.list_blobs(name_starts_with=list_prefix) if list_prefix else container_client.list_blobs()
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
                message = f"[Process {os.getpid()}] Date parsing error: {e}, Folder: {folder}"
                insert_error_log(cmp_id, connection_id, message, "fetch_blobs_for_prefix")

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


def list_date_directories(
    container_client, cmp_id, connection_id, safety_interval_cutoff=None, prefix=None, base_folder=None
):
    """
    Lists all date directories that are:
    - Greater than the prefix (if provided).
    - Less than safety_interval_cutoff.
    """
    date_directories = set()
    current_date = datetime.utcnow().strftime("%Y-%m-%d")

    if prefix:
        try:
            prefix_date = prefix.split("T")[0]
            datetime.strptime(prefix_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Prefix must be a valid ISO 8601 datetime string")

        prefixes = [prefix_date]

        while prefix_date < current_date:
            next_day = datetime.strptime(prefix_date, "%Y-%m-%d") + timedelta(days=1)
            prefix_date = next_day.strftime("%Y-%m-%d")
            prefixes.append(prefix_date)
    else:
        prefixes = [None]

    _vprint(f"[Main Process] Prefixes to process: {prefixes}")

    # Celery workers cannot safely spawn child processes. Keep this path sequential.
    results = [
        fetch_blobs_for_prefix(
            container_client, p, safety_interval_cutoff, cmp_id, connection_id, base_folder=base_folder
        )
        for p in prefixes
    ]

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


def add_underscore(column_name):
    column_name = re.sub(r"(?<=[a-zA-Z])(?=[0-9])", "_", column_name)
    column_name = re.sub(r"(?<=[0-9])(?=[a-zA-Z])", "_", column_name)
    column_name = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", column_name)
    return column_name.upper()


def canonical_meta_column(name: str | None, default: str) -> str:
    """Uppercase sync/delete meta column name (e.g. ``_SPECT_SYNCED``)."""
    raw = str(name or default).strip()
    return (raw or default).upper()


def meta_column_name_variants(*names: str | None) -> set[str]:
    """All spellings that refer to the same sync/delete meta column."""
    variants: set[str] = set()
    for raw in names:
        if not raw:
            continue
        upper = str(raw).strip().upper()
        variants.add(upper)
        stripped = upper.lstrip("_")
        if stripped:
            variants.add(stripped)
    return variants


def resolve_configured_meta_column(col_name: str, updated_at: str | None, deleted_at: str | None) -> str:
    """
    Map a header to the configured meta column spelling.

    Prevents ``_SPECT_SYNCED`` and ``SPECT_SYNCED`` duplicates when normalizing
    Dynamics export headers.
    """
    upper = str(col_name or "").upper().strip()
    for configured in (updated_at, deleted_at):
        if not configured:
            continue
        cfg = str(configured).strip().upper()
        if upper == cfg or upper.lstrip("_") == cfg.lstrip("_"):
            return cfg
    return upper.lstrip("_").replace("__", "_")


def normalize_column_name(name):
    normalized_name = re.sub(r"\W+", "", name).lower()
    normalized_name = normalized_name.replace("_", "")
    return normalized_name


def transformations(df, dtype_conversion, attribute_details, extra_fields, updated_at, deleted_at):
    # Dynamics/CDM dataType → pandas conversion (keys match model.json dataType).
    dtype_conversion_map = {
        "guid": "string",
        "uuid": "string",
        "string": "string",
        "char": "string",
        "varchar": "string",
        "text": "string",
        "memo": "string",
        "json": "string",
        "xml": "string",
        "binary": "string",
        "dateTime": "datetime64[ns]",
        "datetime": "datetime64[ns]",
        "dateTimeOffset": "datetime64[ns]",
        "datetimeoffset": "datetime64[ns]",
        "timestamp": "datetime64[ns]",
        "date": "datetime64[ns]",
        "boolean": "boolean",
        "bool": "boolean",
        "bit": "boolean",
        "int64": "int64",
        "long": "int64",
        "bigint": "int64",
        "int32": "int64",
        "int16": "int64",
        "int": "int64",
        "integer": "int64",
        "byte": "int64",
        "decimal": "float64",
        "numeric": "float64",
        "money": "float64",
        "currency": "float64",
        "float": "float64",
        "double": "float64",
        "real": "float64",
    }

    df["IsDelete"] = df["IsDelete"].fillna(False)

    for column, dtype in attribute_details.items():
        if column in df.columns:
            pandas_dtype = dtype_conversion_map.get(dtype) or dtype_conversion_map.get(
                str(dtype).lower()
            )
            if pandas_dtype:
                try:
                    if pandas_dtype == "datetime64[ns]":
                        df[column] = pd.to_datetime(df[column], errors="coerce", utc=True)
                    elif pandas_dtype == "boolean":
                        df[column] = df[column].where(df[column].notna(), False).astype("bool")
                    elif pandas_dtype == "int64":
                        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype("int64")
                    elif pandas_dtype == "float64":
                        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0).astype(pandas_dtype)
                    else:
                        df[column] = df[column].astype(pandas_dtype)
                except Exception as e:
                    logger.warning("Error converting column %s to %s: %s", column, pandas_dtype, e)
            else:
                _vprint(f"Warning: Unsupported data type '{dtype}' for column {column}. No conversion applied.")
        else:
            _vprint(f"Column {column} not found in DataFrame.")

    df["IsDelete"] = df["IsDelete"].fillna(False)
    meta_variants = meta_column_name_variants(updated_at, deleted_at)
    drop_cols = [
        col
        for col in df.columns
        if str(col).upper() in meta_variants
        or str(col).upper().lstrip("_") in {v.lstrip("_") for v in meta_variants}
    ]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    df.columns = df.columns.map(add_underscore)
    df.columns = df.columns.str.upper().str.strip()
    df.columns = [
        resolve_configured_meta_column(col, updated_at, deleted_at) for col in df.columns
    ]
    df.columns = df.columns.str.upper()
    df = df.loc[:, ~df.columns.duplicated()]

    first_three_columns = [col for col in ["ID", "SINK_CREATED_ON", "SINK_MODIFIED_ON"] if col in df.columns]
    new_order = first_three_columns + [col for col in df.columns if col not in first_three_columns]
    df = df[new_order]

    return df


_NULL_PRIMARY_KEY_LITERALS = frozenset({"", "null", "none", "nan"})


def is_valid_primary_key_value(value) -> bool:
    """True when a Dynamics GUID/ID cell is usable for merge (Snowflake/SQL parity)."""
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except TypeError:
        pass
    text = str(value).strip()
    if not text:
        return False
    return text.lower() not in _NULL_PRIMARY_KEY_LITERALS


def filter_null_primary_key_rows(df, primary_key="ID"):
    """Drop rows with null/blank/placeholder primary keys before destination load."""
    if df is None or getattr(df, "empty", True):
        return df
    pk = str(primary_key or "ID")
    if pk not in df.columns:
        return df
    before = len(df.index)
    mask = df[pk].map(is_valid_primary_key_value)
    filtered = df.loc[mask].reset_index(drop=True)
    dropped = before - len(filtered.index)
    if dropped:
        logger.warning(
            "Dynamics|filter_null_primary_key | pk=%s | dropped=%s | kept=%s",
            pk,
            dropped,
            len(filtered.index),
        )
    return filtered


def filter_orphan_delete_rows(
    df,
    existing_ids,
    *,
    deleted_col="IS_DELETE",
    primary_key="ID",
):
    """
    Snowflake parity: keep upserts and deletes only for IDs already in target/staging.

    ``df[(df['IS_DELETE'] == False) | (df['ID'].isin(original_table_ids))]``
    """
    if df is None or getattr(df, "empty", True):
        return df
    del_col = str(deleted_col or "IS_DELETE")
    pk = str(primary_key or "ID")
    if del_col not in df.columns:
        return df
    existing = {i for i in (existing_ids or []) if is_valid_primary_key_value(i)}
    before = len(df.index)
    is_delete = df[del_col].fillna(False).astype(bool)
    keep_mask = (~is_delete) | df[pk].isin(existing)
    filtered = df.loc[keep_mask].reset_index(drop=True)
    dropped = before - len(filtered.index)
    if dropped:
        logger.warning(
            "Dynamics|filter_orphan_deletes | dropped=%s | kept=%s | existing_ids=%s",
            dropped,
            len(filtered.index),
            len(existing),
        )
    return filtered


def take_flush_slice(buffer_df, chunk_count):
    """
    Split a concat buffer so each destination flush is at most chunk_count rows.

    Read-chunk + leftover buffer can overshoot (e.g. 100k leftover + 150k read
    = 250k). Snowflake IN (...) is capped at 200_000 expressions, so a flush
    must never exceed the plan chunk size.
    """
    if buffer_df is None:
        empty = pd.DataFrame()
        return empty, empty
    limit = int(chunk_count or 0)
    n = len(buffer_df.index)
    if limit <= 0 or n <= limit:
        empty = buffer_df.iloc[0:0].copy()
        return buffer_df.reset_index(drop=True), empty
    flush_df = buffer_df.iloc[:limit].copy()
    remainder_df = buffer_df.iloc[limit:].reset_index(drop=True)
    return flush_df, remainder_df


def _mark_dataverse_delete_csv_chunk(chunk, attribute_names):
    """
    Rows in 2.csv (delete feed) may omit IsDelete or set it false; force delete semantics
    before destination transforms/load.
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
    for ts_col in ("SINK_MODIFIED_ON", "SinkModifiedOn", "sinkmodifiedon"):
        if ts_col in chunk.columns:
            chunk[ts_col] = pd.Timestamp.now(tz=timezone.utc)
            break
    return chunk


def read_csv_in_chunks(
    fs, full_path, attribute_names, delimiter=",", quotechar='"', skiprows=0, chunk_size=50000
):
    """
    Reads a CSV file from Azure Blob Storage in chunks.

    Yields:
        Tuple: (DataFrame chunk, chunk count, is_last_chunk)
    """
    chunk_count = 0
    column_converters = {col: str for col in attribute_names}
    try:
        with fs.open(full_path, "rb") as file_stream:
            chunk_iter = pd.read_csv(
                file_stream,
                delimiter=delimiter,
                quotechar=quotechar,
                skiprows=skiprows,
                chunksize=chunk_size,
                header=None,
                names=attribute_names,
                low_memory=False,
                converters=column_converters,
            )

            for chunk in chunk_iter:
                chunk_count += 1
                if chunk.empty:
                    print(f"Chunk {chunk_count} is empty, skipping.")
                    continue
                yield chunk, chunk_count, False

    except UnicodeDecodeError:
        print("Encoding error encountered. Trying with 'ISO-8859-1' encoding.")
        with fs.open(full_path, "rb") as file_stream:
            chunk_iter = pd.read_csv(
                file_stream,
                delimiter=delimiter,
                quotechar=quotechar,
                skiprows=skiprows,
                encoding="ISO-8859-1",
                chunksize=chunk_size,
                header=None,
                names=attribute_names,
                low_memory=False,
                converters=column_converters,
            )

            for chunk in chunk_iter:
                chunk_count += 1
                if chunk.empty:
                    print(f"Chunk {chunk_count} is empty, skipping.")
                    continue
                yield chunk, chunk_count, False

    yield pd.DataFrame(), chunk_count, True


def list_table_csv_blobs(container_client, base_path, date_dir, table_name):
    """
    List and order CSV blobs under {base_path}/{date_dir}/{table_name}/.
    Pure Azure listing helper used by orchestrator process_file_in_chunks.
    """
    blob_prefix = join_azure_blob_path(base_path, date_dir, table_name) + "/"
    blobs = container_client.list_blobs(name_starts_with=blob_prefix)
    csv_blobs = [blob for blob in blobs if blob.name.lower().endswith(".csv")]
    blob_names = [blob.name for blob in csv_blobs]
    blob_names = sorted(blob_names, key=dataverse_csv_processing_order_key)
    return blob_prefix, blob_names


def iter_table_csv_chunks(
    connection_string,
    container_name,
    container_client,
    base_path,
    date_dir,
    table_name,
    attribute_names,
    chunk_size=50000,
):
    """
    Destination-agnostic CSV iterator for one table date folder.

    Yields:
        (chunk_df, blob_name, is_delete_csv, is_last_file)
        Delete-export CSVs are already marked via _mark_dataverse_delete_csv_chunk.
    Empty terminal yield from read_csv_in_chunks is skipped.

    Orchestrators own buffering + table_creation / destination load
    (see process_file_in_chunks in migration service modules).
    """
    fs = adlfs.AzureBlobFileSystem(connection_string=connection_string)
    _, blob_names = list_table_csv_blobs(container_client, base_path, date_dir, table_name)
    last_file = blob_names[-1] if blob_names else None

    for blob in blob_names:
        full_path = f"{container_name}/{blob}"
        is_delete_csv = is_dataverse_delete_export_csv(blob)
        is_last_file = blob == last_file

        for chunk, _, is_eof_marker in read_csv_in_chunks(
            fs, full_path, attribute_names, chunk_size=chunk_size
        ):
            if is_eof_marker or chunk is None or chunk.empty:
                continue
            if is_delete_csv:
                chunk = _mark_dataverse_delete_csv_chunk(chunk, attribute_names)
            yield chunk, blob, is_delete_csv, is_last_file


_DATAVERSE_BASE_FOLDER_CONFIG_KEYS = (
    "base_folder",
    "folder_name",
    "org_folder",
    "base_folder_path",
)


def _collect_blob_path_samples(container_client, max_samples: int = 100) -> list[str]:
    """Collect representative blob/prefix paths for org base-folder inference."""
    paths: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        cleaned = (name or "").strip().rstrip("/")
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        paths.append(cleaned)

    try:
        for item in container_client.walk_blobs(delimiter="/"):
            _add(getattr(item, "name", "") or "")
            if len(paths) >= max_samples:
                return paths
    except Exception:
        pass

    try:
        blob_iterator = container_client.list_blobs(results_per_page=max_samples).by_page()
        for page in blob_iterator:
            for blob in page:
                _add(blob.name)
                if len(paths) >= max_samples:
                    return paths
            break
    except Exception:
        pass

    return paths


def infer_base_folder(container_client, cmp_id, connection_id, max_blobs=100):
    """
    Infers the base folder by analyzing blob paths in the container.

    Uses hierarchical delimiter listing when available, then supplements with a
    flat blob sample so multi-segment org prefixes are not missed by the first
    page of unrelated blobs.
    """
    try:
        paths = _collect_blob_path_samples(container_client, max_samples=max_blobs)

        if not paths:
            raise ValueError("No blobs found in the container to infer a base folder.")

        inferred = infer_dataverse_base_folder_from_paths(paths)
        logger.warning(
            "DBX|base_folder_inferred | connection_id=%s | base_folder=%s | sample_count=%s",
            connection_id,
            inferred,
            len(paths),
        )
        return inferred

    except ValueError as ve:
        print(f"ValueError occurred: {ve}")
        message = f"ValueError occurred: {ve}"
        insert_error_log(cmp_id, connection_id, message, "infer_base_folder")
        raise

    except Exception as e:
        print(f"An error occurred: {e}")
        message1 = f"An error occurred: {e}"
        insert_error_log(cmp_id, connection_id, message1, "infer_base_folder")
        raise


def resolve_dataverse_base_folder(
    container_client,
    cmp_id,
    connection_id,
    source_config=None,
    *,
    max_blobs: int = 100,
) -> str:
    """
    Resolve the Dataverse org-level base folder from source config or inference.

    Optional source config keys (first non-empty wins):
      base_folder, folder_name, org_folder, base_folder_path
    Environment override: DYNAMICS_AZURE_BASE_FOLDER
    """
    config = source_config if isinstance(source_config, dict) else {}
    env_override = str(os.environ.get("DYNAMICS_AZURE_BASE_FOLDER") or "").strip().strip("/")
    if env_override:
        logger.warning(
            "DBX|base_folder_resolved | connection_id=%s | source=env | base_folder=%s",
            connection_id,
            env_override,
        )
        return env_override

    for key in _DATAVERSE_BASE_FOLDER_CONFIG_KEYS:
        configured = str(config.get(key) or "").strip().strip("/")
        if configured:
            logger.warning(
                "DBX|base_folder_resolved | connection_id=%s | source=config | key=%s | base_folder=%s",
                connection_id,
                key,
                configured,
            )
            return configured

    return infer_base_folder(
        container_client,
        cmp_id,
        connection_id,
        max_blobs=max_blobs,
    )


def is_directory_greater_than(directory_name, last_checked_date):
    dir_date = datetime.strptime(directory_name, "%Y-%m-%dT%H.%M.%SZ")
    last_checked = datetime.strptime(last_checked_date, "%Y-%m-%dT%H.%M.%SZ")
    return dir_date >= last_checked


def safe_json_load(data):
    """Safely loads a JSON string. Returns an empty list if the string is invalid or empty."""
    try:
        if isinstance(data, (dict, list)):
            return data
        return json.loads(data) if data else []
    except (ValueError, TypeError):
        return []


def update_table_status(
    connection_id,
    table_name,
    status,
    last_checked_dates,
    job_name,
    migration_session_id,
    existing_fields,
    cmp_id,
    table_detail_log=None,
):
    """
    Updates the status of the SourceTableSelection and MigrationRecord entries in PostgreSQL.
    """
    _vprint(
        "update_table_status connection_id=%s table=%s status=%s job=%s session=%s",
        connection_id,
        table_name,
        status,
        job_name,
        migration_session_id,
    )

    last_checked_date = last_checked_dates.get(table_name)
    table_fields = safe_json_load(existing_fields) or []
    table_fields_json = json.dumps(table_fields) if isinstance(table_fields, (list, dict)) else "[]"

    conn = postgres_connection()

    try:
        cursor = conn.cursor()
        sts_rows = None
        mr_rows = None
        row = None

        sql_update_source_table = """
        UPDATE source_table_selection
        SET status = %s, last_synced = %s, table_fields = %s
        WHERE connection_id = %s AND "table" = %s;
        """
        cursor.execute(
            sql_update_source_table,
            (status, last_checked_date, table_fields_json, connection_id, table_name),
        )
        sts_rows = cursor.rowcount
        cursor.execute(
            """
            SELECT id, status, start_time
            FROM migration_record
            WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
            """,
            (job_name, migration_session_id, table_name),
        )

        row = cursor.fetchone()
        _vprint("migration_record before update:", row)

        sql_update_migration_record = """
        UPDATE migration_record
        SET status = %s, updated_at = NOW()
        WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
        """
        cursor.execute(sql_update_migration_record, (status, job_name, migration_session_id, table_name))
        mr_rows = cursor.rowcount

        conn.commit()
        logger.info("Updated table %s status to %s", table_name, status)

        if table_detail_log and table_detail_log.enabled():
            table_detail_log.log(
                f"update_table_status committed table={table_name!r} status={status!r} "
                f"source_table_selection_rows={sts_rows} migration_record_rows={mr_rows} "
                f"migration_record_before={row!r}"
            )

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
        message = f"Error updating table or migration record: {e}"
        insert_error_log(cmp_id, connection_id, message, "update_table_status")

    finally:
        cursor.close()
        conn.close()


def delete_csv_from_bucket(table_name, connection_id, cmp_id):
    """Delete the temp Dynamics CSV from GCS after COPY-to-staging."""
    bucket_name = BUCKET_NAME
    file_name = f"temp_data_dynamics_f&o_{table_name}_{connection_id}.csv"
    logger.warning(
        "DBX|gcs_temp_csv_delete_start | table=%s | connection_id=%s | blob=%s",
        table_name,
        connection_id,
        file_name,
    )
    try:
        ok = delete_blob_from_bucket(bucket_name, file_name, client=storage_client)
        if not ok:
            logger.warning(
                "DBX|gcs_temp_csv_delete_miss | table=%s | connection_id=%s | blob=%s",
                table_name,
                connection_id,
                file_name,
            )
            insert_error_log(
                cmp_id,
                connection_id,
                f"delete_blob_from_bucket returned False for {bucket_name}/{file_name}",
                "delete_csv_from_bucket",
            )
        else:
            logger.warning(
                "DBX|gcs_temp_csv_delete_ok | table=%s | connection_id=%s | blob=%s",
                table_name,
                connection_id,
                file_name,
            )
        return f"File {file_name} deleted successfully from bucket {bucket_name}."
    except Exception as e:
        logger.error(
            "DBX|gcs_temp_csv_delete_failed | table=%s | connection_id=%s | blob=%s | err=%s",
            table_name,
            connection_id,
            file_name,
            e,
        )
        message = f"An error occurred while deleting the file {file_name}: {e}"
        insert_error_log(cmp_id, connection_id, message, "delete_csv_from_bucket")
        return message


def log_with_time(message, start_time=None, end_time=None):
    """Helper function to log messages with start time, end time, and duration."""
    if start_time and end_time:
        duration = (end_time - start_time).total_seconds()
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"{message} - Start: {start_str}, End: {end_str}, Duration: {duration:.3f} seconds")
    else:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"{current_time} - {message}")


def filter_date_directories(date_directories, safety_interval_cutoff):
    """
    Filters the list of date directories to include only those that are less than
    the safety_interval_cutoff.
    """
    if isinstance(safety_interval_cutoff, str):
        safety_interval_cutoff = datetime.strptime(safety_interval_cutoff, "%Y-%m-%dT%H.%M.%SZ")

    filtered_directories = [
        folder_date
        for folder_date in date_directories
        if datetime.strptime(folder_date, "%Y-%m-%dT%H.%M.%SZ") < safety_interval_cutoff
    ]

    return filtered_directories


def align_fields_with_existing(df, attribute_details, existing_fields, table_name):
    """
    Aligns DataFrame columns with the expected schema based on attribute_details
    and existing fields in the table.
    """
    if not isinstance(existing_fields, dict):
        existing_fields = {}
    missing_fields = [field for field in existing_fields if field not in df.columns]
    if missing_fields:
        print(f"Adding missing fields to DataFrame for table '{table_name}': {missing_fields}")
        for field in missing_fields:
            df[field] = None

    extra_fields = [field for field in df.columns if field not in existing_fields]
    _vprint(f"Extra fields for table '{table_name}': {extra_fields}")

    modified_fields = {}

    for field in df.columns:
        if field in existing_fields:
            expected_type = attribute_details.get(field, None)
            if expected_type:
                existing_type = existing_fields[field]
                if expected_type != existing_type:
                    modified_fields[field] = expected_type
                    print(
                        f"Field '{field}' has a type/size mismatch: "
                        f"Expected '{expected_type}', found '{existing_type}'"
                    )

    _vprint(f"Modified fields for table '{table_name}': {modified_fields}")

    return df, modified_fields, extra_fields


def directory_exists_in_date_directory(
    container_client, base_folder, date_dir, table_name, cmp_id, connection_id
):
    """
    Check if the table directory exists inside the given date directory.
    """
    table_dir_prefix = join_azure_blob_path(base_folder, date_dir, table_name) + "/"

    try:
        blobs = container_client.list_blobs(name_starts_with=table_dir_prefix)

        for blob in blobs:
            if blob.name.startswith(table_dir_prefix):
                print(f"Table directory {table_name} found in {date_dir}.")
                return True

        return False

    except Exception as e:
        print(f"Error checking directory {table_dir_prefix} in {date_dir}: {e}")
        message = f"Error checking directory {table_dir_prefix} in {date_dir}: {e}"
        insert_error_log(cmp_id, connection_id, message, "directory_exists_in_date_directory")
        return False


def dynamics_gcs_landing_object_key(
    connection_id: int,
    table_name: str,
    date_dir: str,
    csv_basename: str,
    *,
    is_delete: bool = False,
) -> str:
    """GCS object key under the Dynamics F&O lake prefix."""
    sub = "deletes" if is_delete else "data"
    return f"dynamics_fo/{connection_id}/{table_name}/{date_dir}/{sub}/{csv_basename}"


def dynamics_gcs_landing_prefix(connection_id: int, table_name: str, date_dir: str) -> str:
    """GCS URI prefix for one table/date folder (includes trailing slash)."""
    bucket_name = BUCKET_NAME
    return f"gs://{bucket_name}/dynamics_fo/{connection_id}/{table_name}/{date_dir}/"


def land_azure_csv_to_gcs_prefix(
    container_client,
    azure_blob_name: str,
    gcs_object_key: str,
    *,
    bucket_name: str | None = None,
) -> str:
    """
    Stream-copy one Azure export CSV into GCS without pandas parsing.

    Returns the ``gs://`` URI of the landed object.
    """
    bucket_name = bucket_name or BUCKET_NAME
    if not bucket_name:
        raise ValueError("GCS_BUCKET_NAME is not configured")

    blob_client = container_client.get_blob_client(azure_blob_name)
    gcs_bucket = storage_client.bucket(bucket_name)
    gcs_blob = gcs_bucket.blob(gcs_object_key)
    retry = Retry(initial=1.0, maximum=16.0, multiplier=2.0, deadline=300.0)

    t0 = time.time()
    logger.warning(
        "DBX|spark_land_start | azure=%s | gcs_key=%s",
        azure_blob_name,
        gcs_object_key,
    )
    downloader = blob_client.download_blob()
    # Azure StorageStreamDownloader is not a full file-like object (no tell()),
    # so stream chunks into GCS instead of upload_from_file().
    with gcs_blob.open("wb", content_type="text/csv", retry=retry) as gcs_writer:
        for chunk in downloader.chunks():
            gcs_writer.write(chunk)
    gcs_uri = f"gs://{bucket_name}/{gcs_object_key}"
    logger.warning(
        "DBX|spark_land_ok | azure=%s | gcs_uri=%s | duration_s=%.3f",
        azure_blob_name,
        gcs_uri,
        time.time() - t0,
    )
    return gcs_uri


def get_azure_staging_container_client(*, staging_abfss_url: str | None = None):
    """Return a cached Azure Blob container client for Datasyncher staging writes."""
    global _azure_staging_container_client
    if _azure_staging_container_client is not None:
        return _azure_staging_container_client

    connection_string = str(AZURE_STAGING_CONNECTION_STRING or "").strip()
    if not connection_string:
        raise ValueError("AZURE_STAGING_CONNECTION_STRING is not configured")

    staging_url = str(staging_abfss_url or DATASYNCHER_AZURE_STAGING_URL or "").strip()
    if not staging_url:
        raise ValueError("DATASYNCHER_AZURE_STAGING_URL is not configured")

    loc = parse_abfss_uri(staging_url)
    service_client = BlobServiceClient.from_connection_string(connection_string)
    _azure_staging_container_client = service_client.get_container_client(loc.container_name)
    return _azure_staging_container_client


def dynamics_adls_staging_blob_path(
    connection_id: int,
    table_name: str,
    date_dir: str,
    csv_basename: str,
    *,
    is_delete: bool = False,
    staging_base_prefix: str = "",
) -> str:
    """Blob path under the staging container for one landed CSV."""
    sub = "deletes" if is_delete else "data"
    relative = f"dynamics_fo/{connection_id}/{table_name}/{date_dir}/{sub}/{csv_basename}"
    return join_azure_blob_path(staging_base_prefix, relative)


def dynamics_adls_staging_landing_prefix(
    connection_id: int,
    table_name: str,
    date_dir: str,
    staging_abfss_url: str,
) -> str:
    """``abfss://`` prefix for one table/date folder in the staging container."""
    loc = parse_abfss_uri(staging_abfss_url)
    table_path = join_azure_blob_path(
        loc.blob_prefix,
        f"dynamics_fo/{connection_id}/{table_name}/{date_dir}",
    )
    return build_abfss_uri(loc.container_name, loc.account_name, table_path)


def dynamics_adls_staging_table_landing_prefix(
    connection_id: int,
    table_name: str,
    staging_abfss_url: str,
) -> str:
    """``abfss://`` prefix for all landed date folders of one table (bulk Spark read)."""
    loc = parse_abfss_uri(staging_abfss_url)
    table_path = join_azure_blob_path(
        loc.blob_prefix,
        f"dynamics_fo/{connection_id}/{table_name}",
    )
    return build_abfss_uri(loc.container_name, loc.account_name, table_path)


def land_azure_csv_to_adls_staging(
    source_container_client,
    azure_blob_name: str,
    staging_blob_name: str,
    *,
    staging_container_client=None,
    staging_abfss_url: str | None = None,
    quiet: bool = False,
) -> str:
    """
    Stream-copy one F&O export CSV into the Datasyncher Azure staging container.

    Returns the ``abfss://`` URI of the landed object.
    """
    staging_url = str(staging_abfss_url or DATASYNCHER_AZURE_STAGING_URL or "").strip()
    if not staging_url:
        raise ValueError("DATASYNCHER_AZURE_STAGING_URL is not configured")

    loc = parse_abfss_uri(staging_url)
    staging_client = staging_container_client or get_azure_staging_container_client(
        staging_abfss_url=staging_url
    )

    t0 = time.time()
    if not quiet:
        logger.warning(
            "DBX|spark_staging_land_start | export_blob=%s | staging_blob=%s",
            azure_blob_name,
            staging_blob_name,
        )

    source_blob = source_container_client.get_blob_client(azure_blob_name)
    dest_blob = staging_client.get_blob_client(staging_blob_name)
    try:
        source_size = int(source_blob.get_blob_properties().size or 0)
    except ResourceNotFoundError as exc:
        raise RuntimeError(
            f"Azure export blob not found at {azure_blob_name!r} "
            f"(staging target {staging_blob_name!r})"
        ) from exc
    if source_size <= 0:
        raise RuntimeError(
            f"Refusing to stage zero-byte export blob {azure_blob_name!r}"
        )
    downloader = source_blob.download_blob()
    dest_blob.upload_blob(downloader.chunks(), overwrite=True)
    dest_size = int(dest_blob.get_blob_properties().size or 0)
    if dest_size != source_size:
        raise RuntimeError(
            f"Staging copy size mismatch for {azure_blob_name!r}: "
            f"export={source_size} staged={dest_size} blob={staging_blob_name!r}"
        )

    abfss_uri = build_abfss_uri(loc.container_name, loc.account_name, staging_blob_name)
    if not quiet:
        logger.warning(
            "DBX|spark_staging_land_ok | export_blob=%s | abfss_uri=%s | duration_s=%.3f",
            azure_blob_name,
            abfss_uri,
            time.time() - t0,
        )
    return abfss_uri


def land_table_date_dir_to_adls_staging(
    container_client,
    base_path: str,
    date_dir: str,
    table_name: str,
    connection_id: int,
    *,
    staging_abfss_url: str | None = None,
    staging_container_client=None,
) -> tuple[str, list[dict]]:
    """
    Copy all CSV blobs for one table/date folder from F&O export ADLS to staging ADLS.

    Returns ``(landing_prefix_abfss, manifest_entries)`` where each entry has
    azure_path, staging_blob, abfss_uri, and is_delete.
    """
    staging_url = str(staging_abfss_url or DATASYNCHER_AZURE_STAGING_URL or "").strip()
    if not staging_url:
        raise ValueError("DATASYNCHER_AZURE_STAGING_URL is not configured")

    loc = parse_abfss_uri(staging_url)
    staging_client = staging_container_client or get_azure_staging_container_client(
        staging_abfss_url=staging_url
    )
    _, blob_names = list_table_csv_blobs(container_client, base_path, date_dir, table_name)
    landed: list[dict] = []
    for blob_name in blob_names:
        basename = blob_name.rsplit("/", 1)[-1]
        is_delete = is_dataverse_delete_export_csv(blob_name)
        staging_blob = dynamics_adls_staging_blob_path(
            connection_id,
            table_name,
            date_dir,
            basename,
            is_delete=is_delete,
            staging_base_prefix=loc.blob_prefix,
        )
        abfss_uri = land_azure_csv_to_adls_staging(
            container_client,
            blob_name,
            staging_blob,
            staging_container_client=staging_client,
            staging_abfss_url=staging_url,
        )
        landed.append(
            {
                "azure_path": blob_name,
                "staging_blob": staging_blob,
                "abfss_uri": abfss_uri,
                "is_delete": is_delete,
            }
        )

    landing_prefix = dynamics_adls_staging_landing_prefix(
        connection_id,
        table_name,
        date_dir,
        staging_url,
    )
    logger.warning(
        "DBX|spark_staging_discover | table=%s | date_dir=%s | files=%s | landing_prefix=%s",
        table_name,
        date_dir,
        len(landed),
        landing_prefix,
    )
    return landing_prefix, landed


def resolve_dynamics_bulk_copy_workers(
    *,
    copy_workers: int | None = None,
    active_parallel_tables: int | None = None,
) -> int:
    """
    Bounded parallel blob-copy workers for bulk ADLS staging (streaming copies only).

    When ``active_parallel_tables`` is set (from in-process bulk-copy registration),
    scale per-table workers as ``pod_budget // active`` so a single active table gets
    the full default (8) even on pods with high ``CELERY_WORKER_CONCURRENCY``.

    Falls back to ``CELERY_WORKER_CONCURRENCY`` only when the active count is unknown.
    """
    if copy_workers is not None:
        workers = int(copy_workers)
    else:
        raw = str(os.getenv("DYNAMICS_DBX_BULK_COPY_WORKERS", "8") or "8").strip()
        try:
            workers = int(raw)
        except ValueError:
            workers = 8

    workers = max(1, min(workers, 32))

    if copy_workers is not None:
        return workers

    try:
        pod_budget = int(os.getenv("DYNAMICS_DBX_BULK_COPY_POD_THREAD_BUDGET", "16") or 16)
    except ValueError:
        pod_budget = 16
    pod_budget = max(1, min(pod_budget, 32))

    if active_parallel_tables is not None:
        active = max(1, int(active_parallel_tables))
        per_table_cap = max(2, pod_budget // active)
        return max(2, min(workers, per_table_cap))

    try:
        pod_concurrency = int(os.getenv("CELERY_WORKER_CONCURRENCY", "1") or 1)
    except ValueError:
        pod_concurrency = 1
    if pod_concurrency <= 1:
        return workers

    per_table_cap = max(2, pod_budget // pod_concurrency)
    return max(2, min(workers, per_table_cap))


def _bulk_copy_staging_file(
    container_client,
    *,
    azure_blob_name: str,
    staging_blob: str,
    staging_client,
    staging_url: str,
    date_dir: str,
    is_delete: bool,
) -> dict:
    abfss_uri = land_azure_csv_to_adls_staging(
        container_client,
        azure_blob_name,
        staging_blob,
        staging_container_client=staging_client,
        staging_abfss_url=staging_url,
        quiet=True,
    )
    return {
        "azure_path": azure_blob_name,
        "staging_blob": staging_blob,
        "abfss_uri": abfss_uri,
        "is_delete": is_delete,
        "date_dir": date_dir,
    }


def land_table_bulk_to_adls_staging(
    container_client,
    base_path: str,
    date_dirs: list[str],
    table_name: str,
    connection_id: int,
    *,
    staging_abfss_url: str | None = None,
    staging_container_client=None,
    progress_every: int = 50,
    copy_workers: int | None = None,
    lease_refresh_job_name: str | None = None,
) -> tuple[str, list[dict], int, str | None]:
    """
    Copy all pending date folders for one table from F&O export ADLS to staging ADLS.

    Returns ``(table_landing_prefix_abfss, all_landed_files, folders_with_data, last_date_dir)``.
    """
    staging_url = str(staging_abfss_url or DATASYNCHER_AZURE_STAGING_URL or "").strip()
    if not staging_url:
        raise ValueError("DATASYNCHER_AZURE_STAGING_URL is not configured")

    loc = parse_abfss_uri(staging_url)
    staging_client = staging_container_client or get_azure_staging_container_client(
        staging_abfss_url=staging_url
    )
    last_valid_dir: str | None = None
    total_date_dirs = len(date_dirs)
    global _BULK_COPY_ACTIVE_COUNT

    with _BULK_COPY_ACTIVITY_LOCK:
        _BULK_COPY_ACTIVE_COUNT += 1
        active_parallel_tables = _BULK_COPY_ACTIVE_COUNT

    try:
        workers = resolve_dynamics_bulk_copy_workers(
            copy_workers=copy_workers,
            active_parallel_tables=active_parallel_tables,
        )
        return _land_table_bulk_to_adls_staging_inner(
            container_client,
            base_path,
            date_dirs,
            table_name,
            connection_id,
            staging_url=staging_url,
            loc=loc,
            staging_client=staging_client,
            last_valid_dir=last_valid_dir,
            total_date_dirs=total_date_dirs,
            workers=workers,
            active_parallel_tables=active_parallel_tables,
            progress_every=progress_every,
            lease_refresh_job_name=lease_refresh_job_name,
        )
    finally:
        with _BULK_COPY_ACTIVITY_LOCK:
            _BULK_COPY_ACTIVE_COUNT -= 1


def _bulk_staging_scan_and_copy(
    container_client,
    base_path: str,
    date_dirs: list[str],
    table_name: str,
    connection_id: int,
    *,
    workers: int,
    active_parallel_tables: int,
    progress_every: int,
    build_copy_task,
    run_copy,
    table_landing_prefix: str,
    lease_refresh_job_name: str | None = None,
) -> tuple[str, list[dict], int, str | None]:
    """
    Shared bulk staging core: parallel folder scan + parallel stream-copy.

    ``build_copy_task(date_dir, blob_name, basename, is_delete, files_in_folder)``
    returns a task dict. ``run_copy(task)`` must return a dict with ``date_dir``.
    """
    def _maybe_refresh_fleet_lease() -> None:
        if not lease_refresh_job_name:
            return
        try:
            from utils.pipeline_fleet_lease import refresh_pipeline_flow_fleet_lease

            refresh_pipeline_flow_fleet_lease(job_name=lease_refresh_job_name)
        except Exception:
            pass

    last_valid_dir: str | None = None
    total_date_dirs = len(date_dirs)
    scan_progress_every = max(
        0,
        int(os.getenv("DYNAMICS_DBX_BULK_COPY_SCAN_PROGRESS_EVERY", "250") or 0),
    )
    try:
        scan_workers = int(os.getenv("DYNAMICS_DBX_BULK_SCAN_WORKERS", "16") or 16)
    except ValueError:
        scan_workers = 16
    scan_workers = max(1, min(scan_workers, 32))

    bulk_t0 = time.time()
    folder_plans: list[tuple[str, list[str]]] = []
    copy_tasks: list[dict] = []

    def _scan_one(date_dir: str) -> tuple[str, list[str]]:
        _, blob_names = list_table_csv_blobs(
            container_client, base_path, date_dir, table_name
        )
        return date_dir, blob_names

    scanned = 0
    with ThreadPoolExecutor(max_workers=scan_workers) as scan_executor:
        futures = {
            scan_executor.submit(_scan_one, date_dir): date_dir for date_dir in date_dirs
        }
        for future in as_completed(futures):
            date_dir, blob_names = future.result()
            scanned += 1
            last_valid_dir = date_dir
            if not blob_names:
                if (
                    scan_progress_every > 0
                    and (
                        scanned == 1
                        or scanned % scan_progress_every == 0
                        or scanned == total_date_dirs
                    )
                ):
                    logger.warning(
                        "DBX|spark_staging_bulk_scan | table=%s | date_dirs_scanned=%s/%s | "
                        "folders_planned=%s | files=%s | scan_workers=%s | elapsed_s=%.1f",
                        table_name,
                        scanned,
                        total_date_dirs,
                        len(folder_plans),
                        len(copy_tasks),
                        scan_workers,
                        time.time() - bulk_t0,
                    )
                continue

            folder_plans.append((date_dir, blob_names))
            logger.warning(
                "DBX|spark_staging_bulk_folder_start | table=%s | folder=%s/%s | "
                "date_dir=%s | files_in_folder=%s",
                table_name,
                len(folder_plans),
                total_date_dirs,
                date_dir,
                len(blob_names),
            )
            for blob_name in blob_names:
                basename = blob_name.rsplit("/", 1)[-1]
                is_delete = is_dataverse_delete_export_csv(blob_name)
                copy_tasks.append(
                    build_copy_task(
                        date_dir, blob_name, basename, is_delete, len(blob_names)
                    )
                )
            if (
                scan_progress_every > 0
                and (
                    scanned == 1
                    or scanned % scan_progress_every == 0
                    or scanned == total_date_dirs
                )
            ):
                logger.warning(
                    "DBX|spark_staging_bulk_scan | table=%s | date_dirs_scanned=%s/%s | "
                    "folders_planned=%s | files=%s | scan_workers=%s | elapsed_s=%.1f",
                    table_name,
                    scanned,
                    total_date_dirs,
                    len(folder_plans),
                    len(copy_tasks),
                    scan_workers,
                    time.time() - bulk_t0,
                )

    folders_with_data = len(folder_plans)
    logger.warning(
        "DBX|spark_staging_bulk_start | table=%s | connection_id=%s | "
        "total_date_dirs=%s | folders_with_data=%s | total_files=%s | "
        "copy_workers=%s | scan_workers=%s | active_parallel_tables=%s | progress_every=%s",
        table_name,
        connection_id,
        total_date_dirs,
        folders_with_data,
        len(copy_tasks),
        workers,
        scan_workers,
        active_parallel_tables,
        progress_every,
    )

    all_landed: list[dict] = []
    if not copy_tasks:
        logger.warning(
            "DBX|spark_staging_bulk_done | table=%s | folders_with_data=%s | files=%s | "
            "date_dirs_scanned=%s | landing_prefix=%s | duration_s=%.1f",
            table_name,
            folders_with_data,
            0,
            total_date_dirs,
            table_landing_prefix,
            time.time() - bulk_t0,
        )
        return table_landing_prefix, all_landed, folders_with_data, last_valid_dir

    copy_t0 = time.time()
    folders_copied = 0
    folder_file_counts = {date_dir: len(blobs) for date_dir, blobs in folder_plans}
    folder_remaining = dict(folder_file_counts)
    folder_started_at = {date_dir: copy_t0 for date_dir, _ in folder_plans}
    state_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_copy, task) for task in copy_tasks]
        for future in as_completed(futures):
            landed = future.result()
            date_dir = landed["date_dir"]
            with state_lock:
                all_landed.append(landed)
                folder_remaining[date_dir] -= 1
                if folder_remaining[date_dir] != 0:
                    continue

                folders_copied += 1
                files_in_folder = folder_file_counts[date_dir]
                logger.warning(
                    "DBX|spark_staging_bulk_folder_done | table=%s | folder=%s | date_dir=%s | "
                    "files_in_folder=%s | cumulative_files=%s | folder_duration_s=%.1f | "
                    "elapsed_s=%.1f",
                    table_name,
                    folders_copied,
                    date_dir,
                    files_in_folder,
                    len(all_landed),
                    time.time() - folder_started_at[date_dir],
                    time.time() - bulk_t0,
                )
                if progress_every > 0 and folders_copied % progress_every == 0:
                    batch_num = folders_copied // progress_every
                    _maybe_refresh_fleet_lease()
                    logger.warning(
                        "DBX|spark_staging_bulk_batch_complete | table=%s | batch=%s | "
                        "folders_copied=%s | files=%s | date_dirs_scanned=%s/%s | "
                        "elapsed_s=%.1f",
                        table_name,
                        batch_num,
                        folders_copied,
                        len(all_landed),
                        total_date_dirs,
                        total_date_dirs,
                        time.time() - bulk_t0,
                    )
                    logger.warning(
                        "DBX|spark_staging_bulk_progress | table=%s | folders_copied=%s | "
                        "files=%s | date_dirs_scanned=%s/%s | elapsed_s=%.1f",
                        table_name,
                        folders_copied,
                        len(all_landed),
                        total_date_dirs,
                        total_date_dirs,
                        time.time() - bulk_t0,
                    )

    logger.warning(
        "DBX|spark_staging_bulk_done | table=%s | folders_with_data=%s | files=%s | "
        "date_dirs_scanned=%s | copy_workers=%s | landing_prefix=%s | duration_s=%.1f",
        table_name,
        folders_with_data,
        len(all_landed),
        total_date_dirs,
        workers,
        table_landing_prefix,
        time.time() - bulk_t0,
    )
    return table_landing_prefix, all_landed, folders_with_data, last_valid_dir


def _land_table_bulk_to_adls_staging_inner(
    container_client,
    base_path: str,
    date_dirs: list[str],
    table_name: str,
    connection_id: int,
    *,
    staging_url: str,
    loc,
    staging_client,
    last_valid_dir: str | None,
    total_date_dirs: int,
    workers: int,
    active_parallel_tables: int,
    progress_every: int,
    lease_refresh_job_name: str | None = None,
) -> tuple[str, list[dict], int, str | None]:
    del last_valid_dir, total_date_dirs  # computed inside shared helper

    def _build_copy_task(date_dir, blob_name, basename, is_delete, files_in_folder):
        return {
            "azure_blob_name": blob_name,
            "staging_blob": dynamics_adls_staging_blob_path(
                connection_id,
                table_name,
                date_dir,
                basename,
                is_delete=is_delete,
                staging_base_prefix=loc.blob_prefix,
            ),
            "date_dir": date_dir,
            "is_delete": is_delete,
            "files_in_folder": files_in_folder,
        }

    def _run_copy(task: dict) -> dict:
        return _bulk_copy_staging_file(
            container_client,
            azure_blob_name=task["azure_blob_name"],
            staging_blob=task["staging_blob"],
            staging_client=staging_client,
            staging_url=staging_url,
            date_dir=task["date_dir"],
            is_delete=task["is_delete"],
        )

    return _bulk_staging_scan_and_copy(
        container_client,
        base_path,
        date_dirs,
        table_name,
        connection_id,
        workers=workers,
        active_parallel_tables=active_parallel_tables,
        progress_every=progress_every,
        build_copy_task=_build_copy_task,
        run_copy=_run_copy,
        table_landing_prefix=dynamics_adls_staging_table_landing_prefix(
            connection_id, table_name, staging_url
        ),
        lease_refresh_job_name=lease_refresh_job_name,
    )


def resolve_adls_staging_cleanup_enabled() -> bool:
    """Whether to delete Azure staging CSVs after a successful Spark MERGE."""
    val = str(
        os.environ.get("DYNAMICS_DBX_ADLS_STAGING_CLEANUP_AFTER_MERGE", "1") or "1"
    ).strip().lower()
    return val not in ("0", "false", "no", "off")


def resolve_bulk_staging_reuse_enabled() -> bool:
    """
    Whether bulk Spark may skip re-copy when staging CSVs already exist.

    Default is false: each run clears leftover staging and copies fresh.
    Set ``DYNAMICS_DBX_BULK_REUSE_STAGING=1`` to resume a failed run without
    re-copying (legacy rescue path).
    """
    val = str(
        os.environ.get("DYNAMICS_DBX_BULK_REUSE_STAGING", "") or ""
    ).strip().lower()
    return val in ("1", "true", "yes", "on")


def dynamics_adls_staging_blob_prefix(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
    staging_base_prefix: str = "",
) -> str:
    """Container blob prefix for one table or one table/date folder in staging ADLS."""
    if date_dir:
        relative = f"dynamics_fo/{int(connection_id)}/{table_name}/{date_dir}"
    else:
        relative = f"dynamics_fo/{int(connection_id)}/{table_name}"
    prefix = join_azure_blob_path(staging_base_prefix, relative)
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def count_adls_staging_csv_blobs(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
    staging_abfss_url: str | None = None,
    staging_container_client=None,
    max_list: int | None = None,
) -> tuple[int, str]:
    """
    Count CSV blobs under the Datasyncher Azure staging prefix for one table.

    Returns ``(csv_count, blob_prefix)``.
    """
    staging_url = str(staging_abfss_url or DATASYNCHER_AZURE_STAGING_URL or "").strip()
    if not staging_url:
        raise ValueError("DATASYNCHER_AZURE_STAGING_URL is not configured")

    loc = parse_abfss_uri(staging_url)
    blob_prefix = dynamics_adls_staging_blob_prefix(
        connection_id,
        table_name,
        date_dir=date_dir,
        staging_base_prefix=loc.blob_prefix,
    )
    client = staging_container_client or get_azure_staging_container_client(
        staging_abfss_url=staging_url
    )
    count = 0
    zero_byte: list[str] = []
    for blob in client.list_blobs(name_starts_with=blob_prefix):
        name = str(getattr(blob, "name", "") or "")
        if not name.lower().endswith(".csv"):
            continue
        size = int(getattr(blob, "size", None) or 0)
        if size <= 0:
            zero_byte.append(name)
        count += 1
        if max_list is not None and count >= int(max_list):
            break
    if zero_byte:
        raise RuntimeError(
            f"Staging ADLS has {len(zero_byte)} zero-byte CSV blob(s) under "
            f"'{blob_prefix}' (sample: {zero_byte[:3]!r}). "
            "Delete bad blobs or re-run with DYNAMICS_DBX_BULK_FORCE_RECOPY=1."
        )
    return count, blob_prefix


def verify_adls_staging_has_csvs(
    connection_id: int,
    table_name: str,
    *,
    min_count: int = 1,
    expected_count: int | None = None,
    date_dir: str | None = None,
    staging_abfss_url: str | None = None,
    staging_container_client=None,
) -> int:
    """
    Preflight: ensure staging ADLS actually contains CSVs before Spark submit.

    Raises ``RuntimeError`` when the listed CSV count is below ``min_count`` or
    below ``expected_count`` (when provided — typically ``len(landed_files)``).
    """
    count, blob_prefix = count_adls_staging_csv_blobs(
        connection_id,
        table_name,
        date_dir=date_dir,
        staging_abfss_url=staging_abfss_url,
        staging_container_client=staging_container_client,
    )
    min_required = max(1, int(min_count or 1))
    if expected_count is not None:
        min_required = max(min_required, int(expected_count))

    if count < min_required:
        expected_label = (
            f"expected_at_least={expected_count}"
            if expected_count is not None
            else f"min_count={min_count}"
        )
        raise RuntimeError(
            f"Bulk staging preflight failed for table '{table_name}': "
            f"staging lists {count} CSV blob(s) under '{blob_prefix}' "
            f"({expected_label}). Bulk copy reported files but staging is empty "
            "or incomplete — fix copy/credentials before submitting Spark."
        )

    logger.warning(
        "DBX|spark_staging_preflight_ok | table=%s | connection_id=%s | "
        "csv_count=%s | expected=%s | prefix=%s",
        table_name,
        connection_id,
        count,
        expected_count,
        blob_prefix,
    )
    return count


def _delete_adls_staging_blobs_under_prefix(
    staging_container_client,
    blob_prefix: str,
    *,
    batch_size: int = 256,
) -> int:
    deleted = 0
    batch: list[str] = []
    for blob in staging_container_client.list_blobs(name_starts_with=blob_prefix):
        batch.append(blob.name)
        if len(batch) >= batch_size:
            staging_container_client.delete_blobs(*batch, delete_snapshots="include")
            deleted += len(batch)
            batch.clear()
    if batch:
        staging_container_client.delete_blobs(*batch, delete_snapshots="include")
        deleted += len(batch)
    return deleted


def cleanup_adls_staging_after_spark_merge(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
    staging_abfss_url: str | None = None,
    staging_container_client=None,
) -> int:
    """
    Best-effort delete of landed CSVs under the Datasyncher Azure staging container.

    When ``date_dir`` is omitted, removes the whole table prefix (bulk Spark path).
    Non-fatal: logs and returns 0 on failure after MERGE already succeeded.
    """
    if not resolve_adls_staging_cleanup_enabled():
        logger.warning(
            "DBX|spark_staging_cleanup_skipped | table=%s | connection_id=%s | reason=disabled",
            table_name,
            connection_id,
        )
        return 0

    staging_url = str(staging_abfss_url or DATASYNCHER_AZURE_STAGING_URL or "").strip()
    if not staging_url:
        logger.warning(
            "DBX|spark_staging_cleanup_skipped | table=%s | connection_id=%s | reason=no_staging_url",
            table_name,
            connection_id,
        )
        return 0

    loc = parse_abfss_uri(staging_url)
    blob_prefix = dynamics_adls_staging_blob_prefix(
        connection_id,
        table_name,
        date_dir=date_dir,
        staging_base_prefix=loc.blob_prefix,
    )
    client = staging_container_client or get_azure_staging_container_client(
        staging_abfss_url=staging_url
    )

    t0 = time.time()
    logger.warning(
        "DBX|spark_staging_cleanup_start | table=%s | connection_id=%s | "
        "blob_prefix=%s | scope=%s",
        table_name,
        connection_id,
        blob_prefix,
        date_dir or "table",
    )
    try:
        deleted = _delete_adls_staging_blobs_under_prefix(client, blob_prefix)
        logger.warning(
            "DBX|spark_staging_cleanup_done | table=%s | connection_id=%s | "
            "blobs_deleted=%s | scope=%s | duration_s=%.1f",
            table_name,
            connection_id,
            deleted,
            date_dir or "table",
            time.time() - t0,
        )
        return deleted
    except Exception as exc:
        logger.warning(
            "DBX|spark_staging_cleanup_failed | table=%s | connection_id=%s | "
            "blob_prefix=%s | err=%s",
            table_name,
            connection_id,
            blob_prefix,
            exc,
        )
        return 0


def dynamics_gcs_staging_blob_path(
    connection_id: int,
    table_name: str,
    date_dir: str,
    csv_basename: str,
    *,
    is_delete: bool = False,
) -> str:
    """Object key under the GCS staging bucket for one landed CSV (same layout as ADLS)."""
    return dynamics_gcs_landing_object_key(
        connection_id,
        table_name,
        date_dir,
        csv_basename,
        is_delete=is_delete,
    )


def dynamics_gcs_staging_blob_prefix(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
) -> str:
    """GCS object-key prefix for one table or table/date folder."""
    if date_dir:
        return f"dynamics_fo/{int(connection_id)}/{table_name}/{date_dir}/"
    return f"dynamics_fo/{int(connection_id)}/{table_name}/"


def dynamics_gcs_staging_table_landing_prefix(
    connection_id: int,
    table_name: str,
    *,
    bucket_name: str | None = None,
) -> str:
    """``gs://`` prefix for all landed date folders of one table (bulk Spark read)."""
    bucket = str(bucket_name or BUCKET_NAME or "").strip()
    if not bucket:
        raise ValueError("GCS_BUCKET_NAME is not configured")
    return f"gs://{bucket}/dynamics_fo/{int(connection_id)}/{table_name}"


def resolve_gcs_staging_cleanup_enabled() -> bool:
    val = str(
        os.environ.get("DYNAMICS_DBX_GCS_STAGING_CLEANUP_AFTER_MERGE", "1") or "1"
    ).strip().lower()
    return val not in ("0", "false", "no", "off")


def count_gcs_staging_csv_blobs(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
    bucket_name: str | None = None,
    max_list: int | None = None,
) -> tuple[int, str]:
    """Count CSV objects under the GCS staging prefix for one table."""
    bucket = str(bucket_name or BUCKET_NAME or "").strip()
    if not bucket:
        raise ValueError("GCS_BUCKET_NAME is not configured")
    blob_prefix = dynamics_gcs_staging_blob_prefix(
        connection_id, table_name, date_dir=date_dir
    )
    count = 0
    for blob in storage_client.list_blobs(bucket, prefix=blob_prefix):
        name = str(getattr(blob, "name", "") or "")
        if name.lower().endswith(".csv"):
            count += 1
            if max_list is not None and count >= int(max_list):
                break
    return count, blob_prefix


def verify_gcs_staging_has_csvs(
    connection_id: int,
    table_name: str,
    *,
    min_count: int = 1,
    expected_count: int | None = None,
    date_dir: str | None = None,
    bucket_name: str | None = None,
) -> int:
    """Preflight: ensure GCS staging contains CSVs before Spark submit."""
    count, blob_prefix = count_gcs_staging_csv_blobs(
        connection_id,
        table_name,
        date_dir=date_dir,
        bucket_name=bucket_name,
    )
    min_required = max(1, int(min_count or 1))
    if expected_count is not None:
        min_required = max(min_required, int(expected_count))
    if count < min_required:
        expected_label = (
            f"expected_at_least={expected_count}"
            if expected_count is not None
            else f"min_count={min_count}"
        )
        raise RuntimeError(
            f"Bulk staging preflight failed for table '{table_name}': "
            f"GCS lists {count} CSV object(s) under '{blob_prefix}' "
            f"({expected_label}). Bulk copy reported files but staging is empty "
            "or incomplete — fix copy/credentials before submitting Spark."
        )
    logger.warning(
        "DBX|spark_staging_preflight_ok | table=%s | connection_id=%s | "
        "csv_count=%s | expected=%s | prefix=%s | cloud=gcs",
        table_name,
        connection_id,
        count,
        expected_count,
        blob_prefix,
    )
    return count


def cleanup_gcs_staging_after_spark_merge(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
    bucket_name: str | None = None,
) -> int:
    """Best-effort delete of landed CSVs under the GCS staging bucket."""
    if not resolve_gcs_staging_cleanup_enabled():
        logger.warning(
            "DBX|spark_staging_cleanup_skipped | table=%s | connection_id=%s | "
            "reason=disabled | cloud=gcs",
            table_name,
            connection_id,
        )
        return 0
    bucket = str(bucket_name or BUCKET_NAME or "").strip()
    if not bucket:
        logger.warning(
            "DBX|spark_staging_cleanup_skipped | table=%s | connection_id=%s | "
            "reason=no_bucket | cloud=gcs",
            table_name,
            connection_id,
        )
        return 0
    blob_prefix = dynamics_gcs_staging_blob_prefix(
        connection_id, table_name, date_dir=date_dir
    )
    t0 = time.time()
    logger.warning(
        "DBX|spark_staging_cleanup_start | table=%s | connection_id=%s | "
        "blob_prefix=%s | scope=%s | cloud=gcs",
        table_name,
        connection_id,
        blob_prefix,
        date_dir or "table",
    )
    try:
        deleted = 0
        gcs_bucket = storage_client.bucket(bucket)
        for blob in list(storage_client.list_blobs(bucket, prefix=blob_prefix)):
            blob.delete()
            deleted += 1
        del gcs_bucket
        logger.warning(
            "DBX|spark_staging_cleanup_done | table=%s | connection_id=%s | "
            "blobs_deleted=%s | scope=%s | duration_s=%.1f | cloud=gcs",
            table_name,
            connection_id,
            deleted,
            date_dir or "table",
            time.time() - t0,
        )
        return deleted
    except Exception as exc:
        logger.warning(
            "DBX|spark_staging_cleanup_failed | table=%s | connection_id=%s | "
            "blob_prefix=%s | err=%s | cloud=gcs",
            table_name,
            connection_id,
            blob_prefix,
            exc,
        )
        return 0


def _bulk_register_and_run(
    *,
    copy_workers: int | None,
    land_fn,
) -> tuple[str, list[dict], int, str | None]:
    """Increment active bulk-copy counter, resolve workers, run land_fn, then decrement."""
    global _BULK_COPY_ACTIVE_COUNT
    with _BULK_COPY_ACTIVITY_LOCK:
        _BULK_COPY_ACTIVE_COUNT += 1
        active_parallel_tables = _BULK_COPY_ACTIVE_COUNT
    try:
        workers = resolve_dynamics_bulk_copy_workers(
            copy_workers=copy_workers,
            active_parallel_tables=active_parallel_tables,
        )
        return land_fn(workers=workers, active_parallel_tables=active_parallel_tables)
    finally:
        with _BULK_COPY_ACTIVITY_LOCK:
            _BULK_COPY_ACTIVE_COUNT -= 1


def land_table_bulk_to_gcs_staging(
    container_client,
    base_path: str,
    date_dirs: list[str],
    table_name: str,
    connection_id: int,
    *,
    bucket_name: str | None = None,
    progress_every: int = 50,
    copy_workers: int | None = None,
    lease_refresh_job_name: str | None = None,
) -> tuple[str, list[dict], int, str | None]:
    """
    Copy all pending date folders for one table from F&O export ADLS to GCS staging.

    Returns ``(table_landing_prefix_gs, all_landed_files, folders_with_data, last_date_dir)``.
    """
    bucket = str(bucket_name or BUCKET_NAME or "").strip()
    if not bucket:
        raise ValueError("GCS_BUCKET_NAME is not configured")

    table_landing_prefix = dynamics_gcs_staging_table_landing_prefix(
        connection_id, table_name, bucket_name=bucket
    )

    def _land_inner(*, workers: int, active_parallel_tables: int):
        def _build_copy_task(date_dir, blob_name, basename, is_delete, files_in_folder):
            return {
                "azure_blob_name": blob_name,
                "gcs_key": dynamics_gcs_staging_blob_path(
                    connection_id,
                    table_name,
                    date_dir,
                    basename,
                    is_delete=is_delete,
                ),
                "date_dir": date_dir,
                "is_delete": is_delete,
                "files_in_folder": files_in_folder,
            }

        def _run_copy(task: dict) -> dict:
            gcs_uri = land_azure_csv_to_gcs_prefix(
                container_client,
                task["azure_blob_name"],
                task["gcs_key"],
                bucket_name=bucket,
            )
            return {
                "azure_path": task["azure_blob_name"],
                "gcs_key": task["gcs_key"],
                "gcs_uri": gcs_uri,
                "abfss_uri": gcs_uri,
                "is_delete": task["is_delete"],
                "date_dir": task["date_dir"],
            }

        return _bulk_staging_scan_and_copy(
            container_client,
            base_path,
            date_dirs,
            table_name,
            connection_id,
            workers=workers,
            active_parallel_tables=active_parallel_tables,
            progress_every=progress_every,
            build_copy_task=_build_copy_task,
            run_copy=_run_copy,
            table_landing_prefix=table_landing_prefix,
            lease_refresh_job_name=lease_refresh_job_name,
        )

    return _bulk_register_and_run(copy_workers=copy_workers, land_fn=_land_inner)


def resolve_s3_bucket_name(bucket_name: str | None = None) -> str:
    """Resolve S3 bucket name from arg, S3_BUCKET_NAME, or S3_BUCKET_URL."""
    explicit = str(bucket_name or "").strip()
    if explicit:
        if explicit.startswith("s3://"):
            return explicit[len("s3://") :].split("/", 1)[0]
        return explicit
    env_name = str(os.environ.get("S3_BUCKET_NAME") or S3_BUCKET_NAME or "").strip()
    if env_name:
        return env_name
    url = str(os.environ.get("S3_BUCKET_URL") or S3_BUCKET_URL or "").strip()
    if url.startswith("s3://"):
        return url[len("s3://") :].split("/", 1)[0]
    if url:
        return url.split("/", 1)[0]
    return ""


def _get_s3_client():
    """Return a cached boto3 S3 client (env credentials / IAM role)."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    import boto3

    kwargs = {}
    region = str(os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "").strip()
    if region:
        kwargs["region_name"] = region
    _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def dynamics_s3_staging_blob_path(
    connection_id: int,
    table_name: str,
    date_dir: str,
    csv_basename: str,
    *,
    is_delete: bool = False,
) -> str:
    """S3 object key under the staging bucket for one landed CSV."""
    sub = "deletes" if is_delete else "data"
    return f"dynamics_fo/{int(connection_id)}/{table_name}/{date_dir}/{sub}/{csv_basename}"


def dynamics_s3_staging_blob_prefix(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
) -> str:
    if date_dir:
        return f"dynamics_fo/{int(connection_id)}/{table_name}/{date_dir}/"
    return f"dynamics_fo/{int(connection_id)}/{table_name}/"


def dynamics_s3_staging_table_landing_prefix(
    connection_id: int,
    table_name: str,
    *,
    bucket_name: str | None = None,
) -> str:
    """``s3://`` prefix for all landed date folders of one table (bulk Spark read)."""
    bucket = resolve_s3_bucket_name(bucket_name)
    if not bucket:
        raise ValueError("S3_BUCKET_NAME or S3_BUCKET_URL is not configured")
    return f"s3://{bucket}/dynamics_fo/{int(connection_id)}/{table_name}"


def land_azure_csv_to_s3_prefix(
    container_client,
    azure_blob_name: str,
    s3_object_key: str,
    *,
    bucket_name: str | None = None,
    quiet: bool = False,
) -> str:
    """
    Stream-copy one Azure export CSV into S3 without pandas parsing.

    Returns the ``s3://`` URI of the landed object.
    """
    bucket = resolve_s3_bucket_name(bucket_name)
    if not bucket:
        raise ValueError("S3_BUCKET_NAME or S3_BUCKET_URL is not configured")

    blob_client = container_client.get_blob_client(azure_blob_name)
    s3 = _get_s3_client()
    t0 = time.time()
    if not quiet:
        logger.warning(
            "DBX|spark_land_start | azure=%s | s3_key=%s",
            azure_blob_name,
            s3_object_key,
        )
    downloader = blob_client.download_blob()
    # Multipart upload via upload_fileobj needs a file-like; buffer chunks.
    from io import BytesIO

    buf = BytesIO()
    for chunk in downloader.chunks():
        buf.write(chunk)
    buf.seek(0)
    s3.upload_fileobj(buf, bucket, s3_object_key, ExtraArgs={"ContentType": "text/csv"})
    s3_uri = f"s3://{bucket}/{s3_object_key}"
    if not quiet:
        logger.warning(
            "DBX|spark_land_ok | azure=%s | s3_uri=%s | duration_s=%.3f",
            azure_blob_name,
            s3_uri,
            time.time() - t0,
        )
    return s3_uri


def resolve_s3_staging_cleanup_enabled() -> bool:
    val = str(
        os.environ.get("DYNAMICS_DBX_S3_STAGING_CLEANUP_AFTER_MERGE", "1") or "1"
    ).strip().lower()
    return val not in ("0", "false", "no", "off")


def count_s3_staging_csv_blobs(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
    bucket_name: str | None = None,
    max_list: int | None = None,
) -> tuple[int, str]:
    """Count CSV objects under the S3 staging prefix for one table."""
    bucket = resolve_s3_bucket_name(bucket_name)
    if not bucket:
        raise ValueError("S3_BUCKET_NAME or S3_BUCKET_URL is not configured")
    blob_prefix = dynamics_s3_staging_blob_prefix(
        connection_id, table_name, date_dir=date_dir
    )
    s3 = _get_s3_client()
    count = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=blob_prefix):
        for obj in page.get("Contents") or []:
            key = str(obj.get("Key") or "")
            if key.lower().endswith(".csv"):
                count += 1
                if max_list is not None and count >= int(max_list):
                    return count, blob_prefix
    return count, blob_prefix


def verify_s3_staging_has_csvs(
    connection_id: int,
    table_name: str,
    *,
    min_count: int = 1,
    expected_count: int | None = None,
    date_dir: str | None = None,
    bucket_name: str | None = None,
) -> int:
    """Preflight: ensure S3 staging contains CSVs before Spark submit."""
    count, blob_prefix = count_s3_staging_csv_blobs(
        connection_id,
        table_name,
        date_dir=date_dir,
        bucket_name=bucket_name,
    )
    min_required = max(1, int(min_count or 1))
    if expected_count is not None:
        min_required = max(min_required, int(expected_count))
    if count < min_required:
        expected_label = (
            f"expected_at_least={expected_count}"
            if expected_count is not None
            else f"min_count={min_count}"
        )
        raise RuntimeError(
            f"Bulk staging preflight failed for table '{table_name}': "
            f"S3 lists {count} CSV object(s) under '{blob_prefix}' "
            f"({expected_label}). Bulk copy reported files but staging is empty "
            "or incomplete — fix copy/credentials before submitting Spark."
        )
    logger.warning(
        "DBX|spark_staging_preflight_ok | table=%s | connection_id=%s | "
        "csv_count=%s | expected=%s | prefix=%s | cloud=s3",
        table_name,
        connection_id,
        count,
        expected_count,
        blob_prefix,
    )
    return count


def cleanup_s3_staging_after_spark_merge(
    connection_id: int,
    table_name: str,
    *,
    date_dir: str | None = None,
    bucket_name: str | None = None,
) -> int:
    """Best-effort delete of landed CSVs under the S3 staging bucket."""
    if not resolve_s3_staging_cleanup_enabled():
        logger.warning(
            "DBX|spark_staging_cleanup_skipped | table=%s | connection_id=%s | "
            "reason=disabled | cloud=s3",
            table_name,
            connection_id,
        )
        return 0
    bucket = resolve_s3_bucket_name(bucket_name)
    if not bucket:
        logger.warning(
            "DBX|spark_staging_cleanup_skipped | table=%s | connection_id=%s | "
            "reason=no_bucket | cloud=s3",
            table_name,
            connection_id,
        )
        return 0
    blob_prefix = dynamics_s3_staging_blob_prefix(
        connection_id, table_name, date_dir=date_dir
    )
    t0 = time.time()
    logger.warning(
        "DBX|spark_staging_cleanup_start | table=%s | connection_id=%s | "
        "blob_prefix=%s | scope=%s | cloud=s3",
        table_name,
        connection_id,
        blob_prefix,
        date_dir or "table",
    )
    try:
        s3 = _get_s3_client()
        deleted = 0
        paginator = s3.get_paginator("list_objects_v2")
        to_delete: list[dict] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=blob_prefix):
            for obj in page.get("Contents") or []:
                key = str(obj.get("Key") or "")
                if not key:
                    continue
                to_delete.append({"Key": key})
                if len(to_delete) >= 1000:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                    deleted += len(to_delete)
                    to_delete.clear()
        if to_delete:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
            deleted += len(to_delete)
        logger.warning(
            "DBX|spark_staging_cleanup_done | table=%s | connection_id=%s | "
            "blobs_deleted=%s | scope=%s | duration_s=%.1f | cloud=s3",
            table_name,
            connection_id,
            deleted,
            date_dir or "table",
            time.time() - t0,
        )
        return deleted
    except Exception as exc:
        logger.warning(
            "DBX|spark_staging_cleanup_failed | table=%s | connection_id=%s | "
            "blob_prefix=%s | err=%s | cloud=s3",
            table_name,
            connection_id,
            blob_prefix,
            exc,
        )
        return 0


def land_table_bulk_to_s3_staging(
    container_client,
    base_path: str,
    date_dirs: list[str],
    table_name: str,
    connection_id: int,
    *,
    bucket_name: str | None = None,
    progress_every: int = 50,
    copy_workers: int | None = None,
    lease_refresh_job_name: str | None = None,
) -> tuple[str, list[dict], int, str | None]:
    """
    Copy all pending date folders for one table from F&O export ADLS to S3 staging.

    Returns ``(table_landing_prefix_s3, all_landed_files, folders_with_data, last_date_dir)``.
    """
    bucket = resolve_s3_bucket_name(bucket_name)
    if not bucket:
        raise ValueError("S3_BUCKET_NAME or S3_BUCKET_URL is not configured")

    table_landing_prefix = dynamics_s3_staging_table_landing_prefix(
        connection_id, table_name, bucket_name=bucket
    )

    def _land_inner(*, workers: int, active_parallel_tables: int):
        def _build_copy_task(date_dir, blob_name, basename, is_delete, files_in_folder):
            return {
                "azure_blob_name": blob_name,
                "s3_key": dynamics_s3_staging_blob_path(
                    connection_id,
                    table_name,
                    date_dir,
                    basename,
                    is_delete=is_delete,
                ),
                "date_dir": date_dir,
                "is_delete": is_delete,
                "files_in_folder": files_in_folder,
            }

        def _run_copy(task: dict) -> dict:
            s3_uri = land_azure_csv_to_s3_prefix(
                container_client,
                task["azure_blob_name"],
                task["s3_key"],
                bucket_name=bucket,
                quiet=True,
            )
            return {
                "azure_path": task["azure_blob_name"],
                "s3_key": task["s3_key"],
                "s3_uri": s3_uri,
                "abfss_uri": s3_uri,
                "is_delete": task["is_delete"],
                "date_dir": task["date_dir"],
            }

        return _bulk_staging_scan_and_copy(
            container_client,
            base_path,
            date_dirs,
            table_name,
            connection_id,
            workers=workers,
            active_parallel_tables=active_parallel_tables,
            progress_every=progress_every,
            build_copy_task=_build_copy_task,
            run_copy=_run_copy,
            table_landing_prefix=table_landing_prefix,
            lease_refresh_job_name=lease_refresh_job_name,
        )

    return _bulk_register_and_run(copy_workers=copy_workers, land_fn=_land_inner)


def discover_table_date_dir_for_spark_direct(
    container_client,
    container_name: str,
    connection_string: str,
    base_path: str,
    date_dir: str,
    table_name: str,
) -> tuple[str, list[dict]]:
    """
    Discover F&O export CSVs for Spark direct-read (no byte copy).

    Returns ``(landing_prefix_abfss, manifest_entries)`` where each entry has
    azure_path, abfss_uri, and is_delete.
    """
    from utils.azure_path_utils import (
        build_abfss_uri,
        join_azure_blob_path,
        parse_account_name_from_connection_string,
    )

    account_name = parse_account_name_from_connection_string(connection_string)
    _, blob_names = list_table_csv_blobs(container_client, base_path, date_dir, table_name)
    table_path = join_azure_blob_path(base_path, date_dir, table_name)
    landing_prefix = build_abfss_uri(container_name, account_name, table_path)

    discovered: list[dict] = []
    for blob_name in blob_names:
        is_delete = is_dataverse_delete_export_csv(blob_name)
        discovered.append(
            {
                "azure_path": blob_name,
                "abfss_uri": build_abfss_uri(container_name, account_name, blob_name),
                "is_delete": is_delete,
            }
        )

    logger.warning(
        "DBX|spark_direct_discover | table=%s | date_dir=%s | files=%s | landing_prefix=%s",
        table_name,
        date_dir,
        len(discovered),
        landing_prefix,
    )
    return landing_prefix, discovered


def land_table_date_dir_to_gcs(
    container_client,
    base_path: str,
    date_dir: str,
    table_name: str,
    connection_id: int,
    *,
    bucket_name: str | None = None,
) -> list[dict]:
    """
    Land all CSV blobs for one table/date folder from Azure to GCS.

    Returns a list of manifest entry dicts with azure_path, gcs_uri, is_delete.
    """
    bucket_name = bucket_name or BUCKET_NAME
    _, blob_names = list_table_csv_blobs(container_client, base_path, date_dir, table_name)
    landed: list[dict] = []
    for blob_name in blob_names:
        basename = blob_name.rsplit("/", 1)[-1]
        is_delete = is_dataverse_delete_export_csv(blob_name)
        gcs_key = dynamics_gcs_landing_object_key(
            connection_id,
            table_name,
            date_dir,
            basename,
            is_delete=is_delete,
        )
        gcs_uri = land_azure_csv_to_gcs_prefix(
            container_client,
            blob_name,
            gcs_key,
            bucket_name=bucket_name,
        )
        landed.append(
            {
                "azure_path": blob_name,
                "gcs_uri": gcs_uri,
                "gcs_key": gcs_key,
                "is_delete": is_delete,
            }
        )
    return landed


def land_table_date_dir_to_s3(
    container_client,
    base_path: str,
    date_dir: str,
    table_name: str,
    connection_id: int,
    *,
    bucket_name: str | None = None,
) -> tuple[str, list[dict]]:
    """
    Land all CSV blobs for one table/date folder from Azure to S3.

    Returns ``(landing_prefix_s3, manifest_entries)``.
    """
    bucket = resolve_s3_bucket_name(bucket_name)
    if not bucket:
        raise ValueError("S3_BUCKET_NAME or S3_BUCKET_URL is not configured")
    _, blob_names = list_table_csv_blobs(container_client, base_path, date_dir, table_name)
    landed: list[dict] = []
    for blob_name in blob_names:
        basename = blob_name.rsplit("/", 1)[-1]
        is_delete = is_dataverse_delete_export_csv(blob_name)
        s3_key = dynamics_s3_staging_blob_path(
            connection_id,
            table_name,
            date_dir,
            basename,
            is_delete=is_delete,
        )
        s3_uri = land_azure_csv_to_s3_prefix(
            container_client,
            blob_name,
            s3_key,
            bucket_name=bucket,
        )
        landed.append(
            {
                "azure_path": blob_name,
                "s3_uri": s3_uri,
                "s3_key": s3_key,
                "is_delete": is_delete,
            }
        )
    landing_prefix = (
        f"s3://{bucket}/dynamics_fo/{int(connection_id)}/{table_name}/{date_dir}"
    )
    return landing_prefix, landed


def write_landing_manifest(
    connection_id: int,
    table_name: str,
    date_dir: str,
    files: list[dict],
    *,
    bucket_name: str | None = None,
    extra: dict | None = None,
) -> tuple[str, str]:
    """
    Write ``manifest.json`` for a landed table/date batch.

    Returns ``(manifest_gcs_uri, landing_prefix_gcs_uri)``.
    """
    bucket_name = bucket_name or BUCKET_NAME
    if not bucket_name:
        raise ValueError("GCS_BUCKET_NAME is not configured")

    prefix = dynamics_gcs_landing_prefix(connection_id, table_name, date_dir)
    manifest_key = f"dynamics_fo/{connection_id}/{table_name}/{date_dir}/manifest.json"
    payload = {
        "connection_id": connection_id,
        "table": table_name,
        "date_dir": date_dir,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "landing_prefix": prefix,
        "files": files,
    }
    if extra:
        payload.update(extra)

    body = json.dumps(payload, indent=2)
    retry = Retry(initial=1.0, maximum=16.0, multiplier=2.0, deadline=60.0)
    gcs_bucket = storage_client.bucket(bucket_name)
    gcs_bucket.blob(manifest_key).upload_from_string(
        body,
        content_type="application/json",
        retry=retry,
    )
    manifest_uri = f"gs://{bucket_name}/{manifest_key}"
    logger.warning(
        "DBX|spark_manifest_written | table=%s | date_dir=%s | files=%s | uri=%s",
        table_name,
        date_dir,
        len(files),
        manifest_uri,
    )
    return manifest_uri, prefix


def upload_to_bucket(df: pd.DataFrame, table_name: str, connection_id: int, blob_name: str | None = None) -> str:
    """
    Uploads a DataFrame to Google Cloud Storage as a CSV file with retry support.

    Returns:
        str: GCS path of the uploaded CSV file (gcs://bucket/object).
    """
    bucket_name = BUCKET_NAME
    temp_csv_blob_name = blob_name or f"temp_data_dynamics_f&o_{table_name}_{connection_id}.csv"
    row_count = len(df.index) if df is not None and hasattr(df, "index") else 0
    col_count = len(df.columns) if df is not None and hasattr(df, "columns") else 0

    logger.warning(
        "DBX|gcs_upload_prepare | table=%s | connection_id=%s | rows=%s | cols=%s | blob=%s | bucket=%s",
        table_name,
        connection_id,
        row_count,
        col_count,
        temp_csv_blob_name,
        bucket_name,
    )

    csv_data = df.to_csv(index=False, quoting=csv.QUOTE_NONNUMERIC)
    logger.warning(
        "DBX|gcs_upload_csv_ready | table=%s | connection_id=%s | bytes=%s",
        table_name,
        connection_id,
        len(csv_data.encode("utf-8")),
    )

    t0 = time.time()
    try:
        client = storage_client
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(temp_csv_blob_name)
    except Exception as client_exc:
        logger.error(
            "DBX|gcs_client_init_failed | table=%s | connection_id=%s | duration_s=%.3f | err=%s",
            table_name,
            connection_id,
            time.time() - t0,
            client_exc,
        )
        raise

    retry = Retry(initial=1.0, maximum=16.0, multiplier=2.0, deadline=60.0)

    try:
        blob.upload_from_string(csv_data, content_type="text/csv", retry=retry)
        gcs_uri = f"gcs://{bucket_name}/{temp_csv_blob_name}"
        logger.warning(
            "DBX|gcs_upload_success | table=%s | connection_id=%s | uri=%s | duration_s=%.3f",
            table_name,
            connection_id,
            gcs_uri,
            time.time() - t0,
        )
        return gcs_uri

    except Exception as e:
        logger.error(
            "DBX|gcs_upload_blob_failed | table=%s | connection_id=%s | duration_s=%.3f | err=%s",
            table_name,
            connection_id,
            time.time() - t0,
            e,
        )
        raise


def _normalize_gcs_bucket_url(bucket_url: str) -> str:
    url = str(bucket_url or "").strip()
    if not url:
        return url
    if not url.endswith("/"):
        url = f"{url}/"
    return url


# Re-export path utils commonly needed by orchestrators alongside these helpers.
__all__ = [
    "LISTING_USE_DELIMITER_SCAN",
    "GCS_BUCKET_URL",
    "BUCKET_NAME",
    "PROJECT_ID",
    "S3_BUCKET_URL",
    "S3_BUCKET_NAME",
    "storage_client",
    "_build_gcs_storage_client",
    "_migration_verbose_logs",
    "_vprint",
    "_migration_table_detail_log_dir",
    "_migration_table_log_gcs_enabled",
    "_migration_table_log_gcs_bucket_name",
    "_migration_table_log_gcs_object_name",
    "_upload_table_migration_log_to_gcs",
    "_TableMigrationDetailFileLog",
    "connect_to_blob_storage",
    "postgres_connection",
    "insert_error_log",
    "parse_table_fields",
    "_connection_last_synced_folder_cursor",
    "get_last_checked_date_directories",
    "get_all_date_directories_from_db",
    "insert_or_update_date_directories",
    "get_last_checked_dates_and_fields",
    "fetch_blob_names",
    "fetch_blobs_for_prefix",
    "list_date_directories",
    "is_date_format",
    "add_underscore",
    "normalize_column_name",
    "transformations",
    "is_valid_primary_key_value",
    "filter_null_primary_key_rows",
    "filter_orphan_delete_rows",
    "_mark_dataverse_delete_csv_chunk",
    "read_csv_in_chunks",
    "list_table_csv_blobs",
    "iter_table_csv_chunks",
    "infer_base_folder",
    "resolve_dataverse_base_folder",
    "is_directory_greater_than",
    "safe_json_load",
    "update_table_status",
    "delete_csv_from_bucket",
    "log_with_time",
    "filter_date_directories",
    "align_fields_with_existing",
    "directory_exists_in_date_directory",
    "dynamics_gcs_landing_object_key",
    "dynamics_gcs_landing_prefix",
    "get_azure_staging_container_client",
    "dynamics_adls_staging_blob_path",
    "dynamics_adls_staging_landing_prefix",
    "dynamics_adls_staging_table_landing_prefix",
    "land_azure_csv_to_adls_staging",
    "land_table_date_dir_to_adls_staging",
    "resolve_dynamics_bulk_copy_workers",
    "land_table_bulk_to_adls_staging",
    "resolve_adls_staging_cleanup_enabled",
    "resolve_bulk_staging_reuse_enabled",
    "dynamics_adls_staging_blob_prefix",
    "count_adls_staging_csv_blobs",
    "verify_adls_staging_has_csvs",
    "cleanup_adls_staging_after_spark_merge",
    "dynamics_gcs_staging_blob_path",
    "dynamics_gcs_staging_blob_prefix",
    "dynamics_gcs_staging_table_landing_prefix",
    "resolve_gcs_staging_cleanup_enabled",
    "count_gcs_staging_csv_blobs",
    "verify_gcs_staging_has_csvs",
    "cleanup_gcs_staging_after_spark_merge",
    "land_table_bulk_to_gcs_staging",
    "resolve_s3_bucket_name",
    "dynamics_s3_staging_blob_path",
    "dynamics_s3_staging_blob_prefix",
    "dynamics_s3_staging_table_landing_prefix",
    "land_azure_csv_to_s3_prefix",
    "resolve_s3_staging_cleanup_enabled",
    "count_s3_staging_csv_blobs",
    "verify_s3_staging_has_csvs",
    "cleanup_s3_staging_after_spark_merge",
    "land_table_bulk_to_s3_staging",
    "discover_table_date_dir_for_spark_direct",
    "land_azure_csv_to_gcs_prefix",
    "land_table_date_dir_to_gcs",
    "land_table_date_dir_to_s3",
    "write_landing_manifest",
    "upload_to_bucket",
    "_normalize_gcs_bucket_url",
    # path utils re-exports for convenience
    "join_azure_blob_path",
    "is_dataverse_delete_export_csv",
    "dataverse_csv_processing_order_key",
]
