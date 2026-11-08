# services/listing_tables_service.py
import json
import time
from azure.storage.blob import BlobServiceClient
from datetime import datetime, timedelta, timezone
import os
import psycopg2

# Import celery cleanup utility
try:
    from utils.celery_cleanup_utils import handle_migration_completion
except ImportError:
    # Fallback if utils are not available
    def handle_migration_completion(connection_name, job_name, completion_message):
        return {"status": "success", "cleaned_jobs": []}

# Import centralized tool_log utility
from utils.error_utils import insert_tool_log
from utils.azure_path_utils import (
    infer_dataverse_base_folder_from_paths,
    join_azure_blob_path,
    first_date_directory_in_blob_path,
    list_dataverse_date_folder_names,
)
from utils.connection_progress_utils import (
    clear_connection_retrieval_progress,
    update_connection_retrieval_progress,
)





def postgres_connection():
    from datasyncher_connections.postgres import create_postgres_connection

    connection = create_postgres_connection()
    print("connection", connection)
    return connection


import psycopg2
from datetime import datetime

# insert_tool_log is now imported from utils.error_utils (centralized utility with race condition protection)


import json
import psycopg2
import json
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

# Helper function to get delta fields for a specific source
def get_delta_fields_for_source(source_id):
    """
    Fetch delta fields from source_delta_table for a given source_id.
    
    Args:
        source_id (int): The source ID (e.g., for Microsoft Dynamics 365 FO)
    
    Returns:
        list: List of delta field names for the source
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT delta_fields 
            FROM source_delta_table 
            WHERE source_id = %s
        """, (source_id,))
        
        result = cursor.fetchone()
        if result and result[0]:
            # Parse the delta_fields JSON if it's stored as JSON
            delta_fields = result[0] if isinstance(result[0], list) else json.loads(result[0])
            return delta_fields
        return []
        
    except Exception as e:
        print(f"Error fetching delta fields for source {source_id}: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()

def get_source_id_from_connection(connection_id):
    """
    Get src_id from connection_id by following:
    connection -> source_config -> source_model
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT sm.src_id
            FROM connection tc
            JOIN source_config sc ON tc.src_config_id = sc.src_config_id
            JOIN source_model sm ON sc.src_id = sm.src_id
            WHERE tc.connection_id = %s
        """, (connection_id,))
        
        result = cursor.fetchone()
        return result[0] if result else None

    except Exception as e:
        print(f"Error fetching src_id for connection {connection_id}: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def check_table_has_delta_fields(table_fields, delta_fields):
    """
    Check if a table contains any of the specified delta fields.
    
    Args:
        table_fields (dict): Dictionary of table field names and their types
        delta_fields (list): List of delta field names to check for
    
    Returns:
        bool: True if table contains any delta fields, False otherwise
    """
    if not table_fields or not delta_fields:
        return False
    
    # Convert table_fields to dict if it's a JSON string
    if isinstance(table_fields, str):
        try:
            table_fields = json.loads(table_fields)
        except (json.JSONDecodeError, ValueError):
            return False
    
    # Check if any delta field exists in the table fields
    return any(delta_field in table_fields for delta_field in delta_fields)


# ---------------------------------------------------------------------------
# Listing performance knobs (safe defaults preserve behavior)
# ---------------------------------------------------------------------------
LISTING_BLOB_PROGRESS_EVERY = max(1, int(os.getenv("LISTING_BLOB_PROGRESS_EVERY", "2000")))
LISTING_VERBOSE_FOLDER_LOG = os.getenv("LISTING_VERBOSE_FOLDER_LOG", "false").lower() == "true"
LISTING_MAX_PREFIX_WORKERS = max(1, int(os.getenv("LISTING_MAX_PREFIX_WORKERS", "4")))
LISTING_MAX_CHUNK_WORKERS = max(1, int(os.getenv("LISTING_MAX_CHUNK_WORKERS", "4")))
LISTING_USE_DELIMITER_SCAN = os.getenv("LISTING_USE_DELIMITER_SCAN", "true").strip().lower() in (
    "1", "true", "yes", "on", "enabled",
)
LISTING_INCREMENTAL_MONTH_PREFIX_MIN_DAYS = max(
    1, int(os.getenv("LISTING_INCREMENTAL_MONTH_PREFIX_MIN_DAYS", "62"))
)


def _format_connection_last_synced(value) -> str:
    """
    Normalize a cursor for connection.last_synced (VARCHAR).

    The column stores string timestamps (Dataverse folder format), not timestamptz.
    Passing a Python datetime into COALESCE/GREATEST causes a Postgres type error.
    """
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H.%M.%SZ")
    if value is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H.%M.%SZ")
    text = str(value).strip().strip('"')
    if not text or text.lower() == "null":
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H.%M.%SZ")
    return text


def refresh_and_update_tables_in_database(connection_id, new_tables, attributes_dict, last_checked_date_dir, job_name):
    """
    Refreshes the tables and their attributes in the PostgreSQL database.
    Inserts new tables and updates existing ones, while handling migration records and delta field detection.
    """
    print("job name", job_name)
    local_start_time = time.time()
    last_checked_date_dir = _format_connection_last_synced(last_checked_date_dir)
    if new_tables is None:
        new_tables = set()

    # Get source_id and delta fields for this connection (Microsoft Dynamics 365 FO)
    source_id = get_source_id_from_connection(connection_id)
    delta_fields = []
    if source_id:
        delta_fields = get_delta_fields_for_source(source_id)
        print(f"🔍 Found {len(delta_fields)} delta fields for Microsoft Dynamics source_id {source_id}: {delta_fields}")
    
    try:
        # Establish PostgreSQL connection
        conn = postgres_connection()  # Ensure this function returns a valid connection
        with conn.cursor() as cursor:

            # Fetch existing tables for the given connection_id
            sql_fetch_existing_tables = """
            SELECT "table" FROM source_table_selection WHERE connection_id = %s;
            """
            cursor.execute(sql_fetch_existing_tables, (connection_id,))
            existing_tables = {row[0] for row in cursor.fetchall()}

            # Upsert every table returned from Azure listing — not only names missing from
            # source_table_selection. Previously we skipped existing rows, so table_fields /
            # primary_keys / is_delta never refreshed on "Update schema" (only net-new names
            # were written). ON CONFLICT must not overwrite user `selected` / is_mapping_checked.
            tables_discovered = sorted(new_tables) if isinstance(new_tables, (set, frozenset)) else list(new_tables)
            newly_discovered = [t for t in tables_discovered if t not in existing_tables]

            for table in tables_discovered:
                table_fields = attributes_dict.get(table, None)
                primary_keys = []
                if isinstance(table_fields, dict):
                    primary_keys = [
                        col_name
                        for col_name, meta in table_fields.items()
                        if isinstance(meta, dict) and meta.get("is_primary_key")
                    ]

                # Convert table_fields to a JSON string if it's not None
                if table_fields is not None:
                    table_fields_json = json.dumps(table_fields)
                else:
                    table_fields_json = "{}"
                primary_keys_json = json.dumps(primary_keys)

                # 🔍 Check if table contains delta fields (for Microsoft Dynamics 365 FO)
                is_delta = False
                if delta_fields and table_fields:
                    is_delta = check_table_has_delta_fields(table_fields, delta_fields)
                    if is_delta:
                        print(f"✅ Microsoft Dynamics table '{table}' marked as delta table - contains delta fields: {[field for field in delta_fields if field in table_fields]}")

                # Insert or update the table with added_on, is_history, and is_delta included
                sql_upsert = """
                            INSERT INTO source_table_selection (connection_id, "table", table_fields, primary_keys, selected, added_on, is_history, is_delta, is_mapping_checked)
                            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s)
                            ON CONFLICT (connection_id, "table")
                            DO UPDATE SET
                                table_fields = EXCLUDED.table_fields,
                                primary_keys = EXCLUDED.primary_keys,
                                is_history = EXCLUDED.is_history,
                                is_delta = EXCLUDED.is_delta;
                        """

                cursor.execute(sql_upsert, (connection_id, table, table_fields_json, primary_keys_json, False, False, is_delta, False))

            # Update the last_synced field in the connection table
            sql_update_connection = """
            UPDATE connection
            SET last_synced = GREATEST(COALESCE(last_synced, %s), %s)
            WHERE connection_id = %s;
            """
            cursor.execute(sql_update_connection, (last_checked_date_dir, last_checked_date_dir, connection_id))

            # Update the migration_record table with the status for the given job_name
            sql_update_migration_record = """
            UPDATE migration_record
            SET status = %s, updated_at = NOW()
            WHERE job_name = %s;
            """
            cursor.execute(sql_update_migration_record, ('completed', job_name))

            # Commit the transaction
            conn.commit()
            clear_connection_retrieval_progress(connection_id)
            print(
                f"Successfully upserted {len(tables_discovered)} table row(s) from listing "
                f"({len(newly_discovered)} new names, {len(tables_discovered) - len(newly_discovered)} schema refresh); "
                f"last_synced + migration_record updated."
            )

            # ✅ Update the log message to indicate schema refresh completion
            try:
                from utils.error_utils import update_migration_status_log
                execution_time = None
                try:
                    if job_name:
                        conn_et = postgres_connection()
                        cursor_et = conn_et.cursor()
                        cursor_et.execute(
                            """
                            SELECT start_time
                            FROM migration_record
                            WHERE job_name = %s
                            ORDER BY created_at DESC
                            LIMIT 1;
                            """,
                            (job_name,),
                        )
                        row = cursor_et.fetchone()
                        start_time_dt = row[0] if row and row[0] else None
                        if isinstance(start_time_dt, datetime):
                            if start_time_dt.tzinfo is None:
                                execution_time = (datetime.utcnow() - start_time_dt).total_seconds()
                            else:
                                execution_time = (datetime.now(timezone.utc) - start_time_dt).total_seconds()
                except Exception:
                    execution_time = None
                finally:
                    try:
                        cursor_et.close()
                    except Exception:
                        pass
                    try:
                        conn_et.close()
                    except Exception:
                        pass

                if execution_time is None:
                    execution_time = 0.0

                completion_message = (
                    f"Update schema completed successfully in {execution_time:.2f} seconds. "
                    f"Upserted {len(tables_discovered)} table definition(s) "
                    f"({len(newly_discovered)} newly added to catalog)."
                )
                update_migration_status_log(
                    job_name=job_name,
                    message=completion_message,
                    status="S"  # Success status
                )
            except Exception as log_error:
                print(f"⚠️ Update schema completion log update failed (non-fatal): {log_error}")

            # ✅ Trigger WebSocket update for schema refresh completion
            try:
                from utils.websocket_utils import trigger_update_schema_ws_status_update
                trigger_update_schema_ws_status_update(connection_id)
            except Exception as ws_error:
                print(f"⚠️ WebSocket trigger failed (non-fatal): {ws_error}")

    except Exception as e:
        print(f"Error updating database: {e}")

        # Rollback the transaction and update the migration record to FAILED
        if conn:
            conn.rollback()
        try:
            with conn.cursor() as cursor:
                sql_update_migration_record_error = """
                UPDATE migration_record
                SET status = %s, updated_at = NOW()
                WHERE job_name = %s;
                """
                cursor.execute(sql_update_migration_record_error, ('failed', job_name))
                conn.commit()

                # ✅ Update the log message to indicate schema refresh failure
                try:
                    from utils.error_utils import update_migration_status_log
                    failure_message = f"Update schema failed: {str(e)}"
                    update_migration_status_log(
                        job_name=job_name,
                        message=failure_message,
                        status="E"  # Error status
                    )
                except Exception as log_error:
                    print(f"⚠️ Update schema failure log update failed (non-fatal): {log_error}")

                # ✅ Trigger WebSocket update for schema refresh failure
                try:
                    from utils.websocket_utils import trigger_update_schema_ws_status_update
                    trigger_update_schema_ws_status_update(connection_id)
                except Exception as ws_error:
                    print(f"⚠️ WebSocket trigger failed (non-fatal): {ws_error}")
        except Exception as error_update:
            print(f"Error updating migration record to FAILED: {error_update}")

        raise e

    finally:
        if conn:
            conn.close()

    return {
        'status_code': 200,
        'updated_tables': tables_discovered,
        'newly_discovered_tables': newly_discovered,
        'last_synced': last_checked_date_dir,
    }

def get_last_checked_date_directories(connection_id,cmp_id):
    """
    Fetches the last checked date directories for a given connection ID using raw SQL.

    Args:
    - connection_id (int): The ID of the connection.

    Returns:
    - The maximum (latest) date directory, or None if no directories exist.
    """
    try:
        print(f"[DEBUG] Fetching directories for connection_id: {connection_id}")

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
        print(f"[DEBUG] Fetched row: {row}")  # Debugging fetched data

        # If no row is found, return None
        if not row:
            print("[DEBUG] No directories found for this connection.")
            cursor.close()
            conn.close()
            return None

        # Extract directories and check its type
        directories_data = row[0]
        print(f"[DEBUG] Type of directories_data: {type(directories_data)}")

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

        print(f"[DEBUG] Extracted directories before sorting: {date_directories}")

        # Sort the directories (convert string to datetime for sorting)
        date_directories.sort(key=lambda x: datetime.strptime(x, "%Y-%m-%dT%H.%M.%SZ"))

        # Get the latest date if available
        max_date = date_directories[-1] if date_directories else None
        print(f"[DEBUG] Max date found: {max_date}")

        # Close connection
        cursor.close()
        conn.close()

        return max_date  # Return latest date

    except Exception as e:
        print(f"[ERROR] Error fetching date directories: {str(e)}")
        message=f"[ERROR] Error fetching date directories: {str(e)}"
        insert_error_log(cmp_id, connection_id,message, "get_last_checked_date_directories")
        return None  # Return None on error



from azure.core.exceptions import AzureError


def discover_schema_from_root_model_json(container_client, cmp_id=None, connection_id=None):
    """
    Discover all D365 FO table schemas from the single root model.json file.

    The file lives at the container root (blob path = "model.json"), sitting
    directly alongside the timestamped date directories — confirmed from the
    Azure Portal view of the Dataverse export container.

    Downloads exactly ONE blob and parses every entity definition from it,
    replacing the old flow that scanned hundreds of timestamped directories
    and downloaded a separate [TableName]-model.json per table.

    Args:
        container_client : Azure BlobServiceClient container client
        cmp_id           : Company ID (used for error logging only)
        connection_id    : Connection ID (used for error logging only)

    Returns:
        tuple: (new_tables: set[str], attributes_dict: dict[str, dict])
            new_tables      – set of table name strings
            attributes_dict – {table_name: {col_name: {"data_type": str, "is_primary_key": bool}}}

    Raises:
        ValueError  : if model.json has an unexpected top-level structure.
        Exception   : re-raises any Azure SDK / network errors after logging.
    """
    dtype_conversion = {
        'guid':           'VARCHAR(512)',
        'dateTime':       'TIMESTAMP_TZ(9)',
        'dateTimeOffset': 'TIMESTAMP_TZ(9)',
        'int64':          'NUMBER(38,0)',
        'decimal':        'FLOAT',
        'float':          'FLOAT',
        'string':         'VARCHAR',
        'boolean':        'BOOLEAN',
    }

    # model.json lives at the container root — no base-folder prefix required.
    blob_path = "model.json"
    print(f"Start: Downloading root model.json from blob path '{blob_path}'")

    try:
        blob_client = container_client.get_blob_client(blob_path)

        # ── Log blob metadata BEFORE downloading ─────────────────────────────
        # This is critical for support: when a user asks "why didn't my new
        # table appear?", the last_modified timestamp tells us whether the
        # file was updated after their D365 export.
        try:
            props = blob_client.get_blob_properties()
            print(
                f"model.json metadata — size={props.size:,} bytes, "
                f"last_modified={props.last_modified}"
            )
        except Exception as meta_err:
            print(f"⚠️  Could not fetch model.json blob properties (non-fatal): {meta_err}")
        # ─────────────────────────────────────────────────────────────────────

        raw = blob_client.download_blob().readall()
        json_data = json.loads(raw)
        print(f"Completed: Root model.json downloaded ({len(raw):,} bytes)")

    except Exception as e:
        msg = f"Failed to download root model.json from '{blob_path}': {e}"
        print(f"[ERROR] {msg}")
        if cmp_id and connection_id:
            insert_error_log(cmp_id, connection_id, msg, "discover_schema_from_root_model_json")
        raise

    # ── Structure validation ──────────────────────────────────────────────────
    # Guards against silent failures if Microsoft changes the model.json schema.
    if not isinstance(json_data, dict):
        raise ValueError(
            f"model.json root must be a JSON object; got {type(json_data).__name__}"
        )
    if "entities" not in json_data:
        raise ValueError(
            "model.json is missing required top-level 'entities' array. "
            f"Keys present: {list(json_data.keys())}"
        )
    if not isinstance(json_data["entities"], list):
        raise ValueError(
            f"model.json 'entities' must be a list; got {type(json_data['entities']).__name__}"
        )
    # ─────────────────────────────────────────────────────────────────────────

    entities = json_data["entities"]
    print(f"Start: Parsing {len(entities)} entity definitions from root model.json")

    new_tables = set()
    attributes_dict = {}
    duplicate_count = 0

    for entity in entities:
        table_name = entity.get("name")
        if not table_name:
            continue

        # ── Duplicate entity guard ────────────────────────────────────────────
        # Since one file now controls the entire catalog, duplicates must be
        # surfaced immediately rather than silently overwriting earlier entries.
        if table_name in attributes_dict:
            duplicate_count += 1
            print(
                f"⚠️  Duplicate entity in model.json: '{table_name}' — "
                f"keeping first occurrence, skipping duplicate."
            )
            continue
        # ─────────────────────────────────────────────────────────────────────

        attributes = {}
        for attr in entity.get("attributes", []):
            name = attr.get("name")
            data_type = attr.get("dataType")
            max_length = attr.get("maxLength", -1)

            if not name:
                continue

            # Case-insensitive PK detection — guards against metadata casing
            # variations (e.g. "id", "ID") across D365 export versions.
            is_pk = name.lower() == "id"

            # VARCHAR length — round up to nearest multiple of 512 (mirrors old logic).
            if data_type == 'string' and max_length and max_length > 0:
                length = ((max_length - 1) // 512 + 1) * 512
                snowflake_type = f"VARCHAR({length})"
            else:
                snowflake_type = dtype_conversion.get(data_type, 'VARCHAR(512)')

            attributes[name] = {
                "data_type": snowflake_type,
                "is_primary_key": is_pk,
            }

        attributes_dict[table_name] = attributes
        new_tables.add(table_name)

    if duplicate_count:
        print(
            f"⚠️  {duplicate_count} duplicate entity name(s) found and skipped in model.json. "
            f"Review the root model.json for unexpected repeated entity definitions."
        )

    print(f"Completed: Extracted schemas for {len(new_tables)} tables from root model.json")
    return new_tables, attributes_dict


def has_cached_date_directories(connection_id):
    """True when date_directory exists with at least one cached folder name."""
    conn = None
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT directories FROM date_directory WHERE connection_id = %s;",
            (connection_id,),
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return False
        directories_data = row[0]
        if isinstance(directories_data, str):
            if not directories_data.strip():
                return False
            try:
                date_directories = json.loads(directories_data)
            except json.JSONDecodeError:
                return False
        elif isinstance(directories_data, list):
            date_directories = directories_data
        else:
            return False
        return bool(date_directories)
    except Exception as exc:
        print(f"[WARN] Could not read date_directory cache state: {exc}")
        return False
    finally:
        if conn:
            conn.close()


def resolve_folder_scan_mode(connection_id, cmp_id):
    """
    Decide how to discover export date folders during listing.

    - No cached directories → full scan (all folders under inferred base_folder).
    - Cached directories exist → incremental scan from the latest cached folder.
    """
    if not has_cached_date_directories(connection_id):
        print("[folder_scan] No cached directories; scanning all date folders")
        return "full", None

    cached_max = get_last_checked_date_directories(connection_id, cmp_id)
    if cached_max:
        print(f"[folder_scan] Incremental scan from cached max folder: {cached_max}")
        return "incremental", cached_max

    print("[folder_scan] Empty date_directory row; scanning all date folders")
    return "full", None


def sync_date_directories_after_listing(
    container_client,
    connection_id,
    cmp_id,
    listing_prefixes=None,
):
    """
    Incrementally scan Azure export folders and persist them to date_directory.

    Runs synchronously at the end of listing (after model.json schema discovery)
    so migrations can read cached folders instead of full container list_blobs().
    """
    scan_started = time.time()
    print("Start: Syncing date directories (incremental folder scan)")

    base_folder = listing_infer_base_folder(container_client, cmp_id, connection_id)
    print(f"[folder_scan] Inferred base_folder={base_folder!r}")

    existing_count = 0
    conn = None
    cursor = None
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT directories FROM date_directory WHERE connection_id = %s;",
            (connection_id,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            dirs = row[0]
            if isinstance(dirs, str):
                dirs = json.loads(dirs) if dirs.strip() else []
            existing_count = len(dirs) if isinstance(dirs, list) else 0
    except Exception as exc:
        print(f"[WARN] Could not read existing date_directory count: {exc}")
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    if listing_prefixes is not None:
        prefixes_override = listing_prefixes if listing_prefixes else [None]
        scan_mode = "override"
        scan_cursor = None
    else:
        scan_mode, scan_cursor = resolve_folder_scan_mode(connection_id, cmp_id)
        prefixes_override = [None] if scan_mode == "full" else None

    if listing_prefixes is not None:
        total_prefixes = len(prefixes_override)
    elif scan_mode == "full":
        total_prefixes = 1
    else:
        total_prefixes = len(build_listing_prefixes(scan_cursor))

    update_connection_retrieval_progress(
        connection_id, 0, max(1, total_prefixes), notify_ws=True
    )

    if listing_prefixes is not None:
        discovered = list_date_directories(
            container_client,
            cmp_id,
            connection_id,
            base_folder=base_folder,
            prefixes_override=prefixes_override,
            progress_connection_id=connection_id,
        )
    elif scan_mode == "full":
        discovered = list_date_directories(
            container_client,
            cmp_id,
            connection_id,
            base_folder=base_folder,
            prefixes_override=[None],
            progress_connection_id=connection_id,
        )
    else:
        discovered = list_date_directories(
            container_client,
            cmp_id,
            connection_id,
            prefix=scan_cursor,
            base_folder=base_folder,
            progress_connection_id=connection_id,
        )

    merged = insert_or_update_date_directories(connection_id, discovered)
    new_folder_count = max(0, len(merged) - existing_count)
    duration = time.time() - scan_started
    print(
        f"Completed: Date directory sync in {duration:.2f}s — "
        f"discovered={len(discovered)} total_cached={len(merged)} new={new_folder_count}"
    )
    update_connection_retrieval_progress(
        connection_id, max(1, total_prefixes), max(1, total_prefixes), notify_ws=True
    )
    return {
        "discovered_count": len(discovered),
        "total_cached_count": len(merged),
        "new_folder_count": new_folder_count,
        "duration_seconds": duration,
    }


def authenticate_and_list_tables(
    connection_id,
    container_name,
    connection_string,
    cmp_id,
    user_id,
    last_checked_date=None,
    job_name=None,
    listing_prefixes=None,
):
    """
    Authenticate with Azure Blob Storage and list new tables from date directories.

    Args:
        container_name: The name of the Azure Blob Storage container.
        connection_string: The connection string for Azure Blob Storage.
        cmp_id: Company ID (must be valid FK)
        last_checked_date: (Optional) The last checked date to filter new tables.
        job_name: (Optional) The name of the job.

    Returns:
        A dictionary with the response status, last checked date, and the new tables found.
    """
    start_time = time.time()

    try:
        print("Start: Authenticating with Azure Blob Storage")
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        container_client = blob_service_client.get_container_client(container_name)
        print("Completed: Authentication with Azure Blob Storage")

        # ── New fast path ────────────────────────────────────────────────────
        # Read the single root model.json that lives at the container root
        # (confirmed blob path: "model.json"). This replaces the old flow that
        # scanned every timestamped date directory and downloaded hundreds of
        # individual [TableName]-model.json files.
        print("Start: Discovering schemas from root model.json")
        new_tables, attributes_dict = discover_schema_from_root_model_json(
            container_client,
            cmp_id=cmp_id,
            connection_id=connection_id,
        )
        # No date-directory timestamp is determined here; use current UTC time
        # as the fallback so refresh_and_update_tables_in_database updates
        # last_synced correctly.
        #
        # Use UTC "now" when no blob directory cursor exists (root model.json path).
        # refresh_and_update_tables_in_database stringifies this for connection.last_synced (VARCHAR).
        last_checked_date_dir = last_checked_date or datetime.now(timezone.utc)
        # ────────────────────────────────────────────────────────────────────

        refresh_and_update_tables_in_database(connection_id, new_tables, attributes_dict, last_checked_date_dir, job_name)

        folder_sync = sync_date_directories_after_listing(
            container_client,
            connection_id,
            cmp_id,
            listing_prefixes=listing_prefixes,
        )

        execution_time = None
        try:
            if job_name:
                conn_et = postgres_connection()
                cursor_et = conn_et.cursor()
                cursor_et.execute(
                    """
                    SELECT start_time
                    FROM migration_record
                    WHERE job_name = %s
                    ORDER BY created_at DESC
                    LIMIT 1;
                    """,
                    (job_name,),
                )
                row = cursor_et.fetchone()
                start_time_dt = row[0] if row and row[0] else None
                if isinstance(start_time_dt, datetime):
                    if start_time_dt.tzinfo is None:
                        execution_time = (datetime.utcnow() - start_time_dt).total_seconds()
                    else:
                        execution_time = (datetime.now(timezone.utc) - start_time_dt).total_seconds()
        except Exception:
            execution_time = None
        finally:
            try:
                cursor_et.close()
            except Exception:
                pass
            try:
                conn_et.close()
            except Exception:
                pass

        if execution_time is None:
            execution_time = 0.0

        # Clean up celery jobs to prevent stuck job scenarios
        try:
            # Construct connection_name for cleanup (similar to other migration services)
            connection_name = f"DYNAMICS_LISTING_{connection_id}"
            
            cleanup_result = handle_migration_completion(
                connection_name=connection_name,
                job_name=job_name,
                completion_message=(
                    f"Update schema completed successfully in {execution_time:.2f} seconds. "
                    f"Updated {len(new_tables)} tables. "
                    f"Synced {folder_sync['total_cached_count']} date folders "
                    f"({folder_sync['new_folder_count']} new)."
                )
            )
            
            if cleanup_result['status'] == 'success' and cleanup_result['cleaned_jobs']:
                print(f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} stuck celery jobs for {connection_name}")
            elif cleanup_result['status'] == 'error':
                print(f"⚠️ Cleanup warning for {connection_name}: {cleanup_result['message']}")
                
        except Exception as cleanup_error:
            # Don't fail the listing if cleanup fails - just log the warning
            print(f"⚠️ Failed to cleanup celery jobs: {cleanup_error}")
            import traceback
            traceback.print_exc()

        clear_connection_retrieval_progress(connection_id)
        return {
            'status_code': 200,
            'message': (
                f'Update schema completed successfully in {execution_time:.2f} seconds. '
                f'Updated {len(new_tables)} tables. '
                f'Synced {folder_sync["total_cached_count"]} date folders '
                f'({folder_sync["new_folder_count"]} new).'
            ),
            'execution_time': execution_time,
            'folder_sync': folder_sync,
        }

    # -------------------- AZURE ERROR --------------------
    except AzureError as azure_error:
        error_message = f"Azure Storage error: {azure_error}"
        print(error_message)
        clear_connection_retrieval_progress(connection_id)

        try:
            insert_tool_log(
                cmp_id,
                connection_id,
                user_id,
                None,
                'E',
                error_message,
                job_name,
                job_name=job_name
            )
        except Exception as log_err:
            print(f"⚠️ insert_tool_log failed (azure error): {log_err}")

        raise azure_error

    # -------------------- GENERAL ERROR --------------------
    except Exception as general_error:
        error_message = f"General error: {general_error}"
        print(error_message)
        clear_connection_retrieval_progress(connection_id)

        try:
            insert_tool_log(
                cmp_id,
                connection_id,
                user_id,
                None,
                'E',
                error_message,
                job_name,
                job_name=job_name
            )
        except Exception as log_err:
            print(f"⚠️ insert_tool_log failed (general error): {log_err}")

        raise general_error

    finally:
        print("Execution completed.")

    

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
    print(f"[DEBUG] Connecting to PostgreSQL for connection_id: {connection_id}")
    # Connect to PostgreSQL
    conn = postgres_connection()
    cursor = conn.cursor()
    print("[DEBUG] Connected to database successfully")
    # Fetch existing directories
    cursor.execute("""
        SELECT directories FROM date_directory WHERE connection_id = %s;
    """, (connection_id,))
    
    row = cursor.fetchone()
    print(f"[DEBUG] Retrieved row: {row}")  # Debug fetched data
    if row:
        print(f"[DEBUG] Type of row[0]: {type(row[0])}")  # Check data type before json.loads()
    
    existing_directories = row[0] if row and row[0] else []
    if isinstance(existing_directories, str):
        try:
            existing_directories = json.loads(existing_directories) if existing_directories.strip() else []
        except json.JSONDecodeError:
            print(f"[ERROR] Could not parse directories JSON, resetting merge base: {existing_directories[:200]!r}")
            existing_directories = []
    if not isinstance(existing_directories, list):
        print(f"[ERROR] existing_directories has unexpected type {type(existing_directories)}; using empty list for merge.")
        existing_directories = []
    print(f"[DEBUG] Existing directories: {existing_directories}")
    # Ensure new_directories is a list
    if not isinstance(new_directories, list):
        print("[ERROR] new_directories is not a list!")
        return "Error: new_directories must be a list."
    # Merge existing and new directories (avoiding duplicates)
    updated_directories = list(set(existing_directories + new_directories))
    print(f"[DEBUG] Merged directories before saving: {updated_directories}")
    # Convert updated list to JSON format
    updated_json = json.dumps(updated_directories)  # ✅ Ensure JSON conversion is correct
    print(f"[DEBUG] JSON to be stored: {updated_json}")
    # Insert or update the record
    if row:
        cursor.execute("""
            UPDATE date_directory 
            SET directories = %s, updated_at = %s 
            WHERE connection_id = %s;
        """, (updated_json, datetime.utcnow(), connection_id))
        print(f"[DEBUG] Updated record for connection_id: {connection_id}")
    else:
        cursor.execute("""
            INSERT INTO date_directory (connection_id, directories, created_at, updated_at)
            VALUES (%s, %s, %s, %s);
        """, (connection_id, updated_json, datetime.utcnow(), datetime.utcnow()))
        print(f"[DEBUG] Inserted new record for connection_id: {connection_id}")
    # Commit changes and close the connection
    conn.commit()
    cursor.close()
    conn.close()
    print(f"[DEBUG] Final date directories: {updated_directories}")
    return sorted(updated_directories)
import re
import os
from datetime import datetime, timedelta
from azure.storage.blob import ContainerClient

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
def fetch_blobs_for_prefix(container_client, prefix, cmp_id, connection_id, base_folder=None):
    """
    Discover date directories for a day/month prefix (or full tree when prefix is None).

    Prefers Azure hierarchical delimiter listing (folder prefixes only). Falls back
    to per-blob iteration when delimiter listing is disabled or unavailable.
    """
    list_label = prefix if prefix else "ALL"
    print(
        f"[Process {os.getpid()}] Fetching date folders prefix={list_label} "
        f"base_folder={base_folder!r} delimiter={LISTING_USE_DELIMITER_SCAN}"
    )

    if LISTING_USE_DELIMITER_SCAN:
        try:
            date_directories = list_dataverse_date_folder_names(
                container_client,
                base_folder=base_folder,
                name_prefix=prefix,
            )
            print(
                f"[Process {os.getpid()}] Delimiter listing: unique_dirs={len(date_directories)} "
                f"prefix={list_label}"
            )
            return date_directories
        except Exception as exc:
            print(
                f"[WARN] Delimiter date-folder listing failed for prefix={list_label}: {exc}; "
                "falling back to blob iteration"
            )

    date_directories = set()
    if prefix:
        list_prefix = join_azure_blob_path(base_folder, prefix)
    elif base_folder:
        list_prefix = join_azure_blob_path(base_folder)
    else:
        list_prefix = None

    blob_iter = container_client.list_blobs(name_starts_with=list_prefix) if list_prefix else container_client.list_blobs()
    scanned_count = 0
    matched_count = 0

    for blob in blob_iter:
        scanned_count += 1
        if scanned_count % LISTING_BLOB_PROGRESS_EVERY == 0:
            print(
                f"[Process {os.getpid()}] Blob scan progress: scanned={scanned_count} "
                f"matched={matched_count} prefix={list_prefix if list_prefix else 'ALL'}"
            )
        folder = first_date_directory_in_blob_path(blob.name)
        if not folder:
            continue

        if is_valid_date(folder):
            date_directories.add(folder)
            matched_count += 1
            if LISTING_VERBOSE_FOLDER_LOG:
                print(f"[Process {os.getpid()}] Added folder: {folder}")

    print(
        f"[Process {os.getpid()}] Completed blob scan: scanned={scanned_count} "
        f"matched={matched_count} unique_dirs={len(date_directories)} "
        f"prefix={list_prefix if list_prefix else 'ALL'}"
    )

    return date_directories

def build_monthly_listing_prefixes(start_date_str, current_date_str):
    """YYYY-MM prefixes from start month through current month (inclusive)."""
    start_year, start_month = int(start_date_str[:4]), int(start_date_str[5:7])
    end_year, end_month = int(current_date_str[:4]), int(current_date_str[5:7])
    prefixes = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        prefixes.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return prefixes


def build_listing_prefixes(last_checked_date):
    """Build blob prefixes from last_checked_date to current UTC date (day or month)."""
    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    if not last_checked_date:
        return [None]

    try:
        prefix_date = str(last_checked_date).split("T")[0]
        datetime.strptime(prefix_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Prefix must be a valid ISO 8601 datetime string")

    start_dt = datetime.strptime(prefix_date, "%Y-%m-%d")
    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    if (end_dt - start_dt).days >= LISTING_INCREMENTAL_MONTH_PREFIX_MIN_DAYS:
        return build_monthly_listing_prefixes(prefix_date, current_date)

    prefixes = [prefix_date]
    while prefix_date < current_date:
        next_day = datetime.strptime(prefix_date, "%Y-%m-%d") + timedelta(days=1)
        prefix_date = next_day.strftime("%Y-%m-%d")
        prefixes.append(prefix_date)
    return prefixes


def list_date_directories(container_client,cmp_id,connection_id, prefix=None, base_folder=None, prefixes_override=None, progress_connection_id=None):
    """
    Lists all date directories that are:
    - Greater than the prefix (if provided).
    - Less than safety_interval_cutoff.
    - Optimized using multiprocessing for faster execution.

    Args:
        container_client: Azure Blob container client.
        safety_interval_cutoff: The maximum date limit ('YYYY-MM-DDTHH:MM:SSZ').
        prefix: Optional prefix to optimize API calls (e.g., 'YYYY-MM-DDTHH:MM:SSZ').

    Returns:
        Sorted list of date directories matching the criteria.
    """
    date_directories = set()
    if prefixes_override is not None:
        prefixes = prefixes_override if prefixes_override else [None]
    elif prefix:
        prefixes = build_listing_prefixes(prefix)
    else:
        prefixes = [None]

    print(f"[Main Process] Prefixes to process: {prefixes}")

    # Celery-prefork workers cannot spawn child processes, but thread pools are safe for
    # these Azure I/O bound list calls and provide bounded parallelism.
    worker_count = min(LISTING_MAX_PREFIX_WORKERS, max(1, len(prefixes)))
    results = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_prefix = {
            executor.submit(fetch_blobs_for_prefix, container_client, p, cmp_id, connection_id, base_folder): p
            for p in prefixes
        }
        completed = 0
        for future in as_completed(future_to_prefix):
            p = future_to_prefix[future]
            completed += 1
            print(
                f"[Main Process] Prefix progress: {completed}/{len(prefixes)} "
                f"(prefix={p if p else 'ALL'})"
            )
            if progress_connection_id:
                update_connection_retrieval_progress(
                    progress_connection_id,
                    completed,
                    len(prefixes),
                    notify_ws=True,
                )
            results.append(future.result())

    # Combine results from parallel processes
    for res in results:
        date_directories.update(res)

    print(f"[Main Process] Final sorted date directories: {sorted(date_directories)}")
    return sorted(date_directories)
def filter_directories_greater_than(directories, last_checked_date):
    if last_checked_date:
        last_checked = datetime.strptime(last_checked_date, "%Y-%m-%dT%H.%M.%SZ")
    filtered_directories = []

    for directory_name in directories:
        try:
            dir_date = datetime.strptime(directory_name, "%Y-%m-%dT%H.%M.%SZ")
            if dir_date > last_checked:
                filtered_directories.append(directory_name)
        except ValueError:
            # Handle the case where the directory name is not in the expected format
            print(f"Skipping directory with invalid format: {directory_name}")
    sorted_directories = sorted(filtered_directories, key=lambda x: datetime.strptime(x, "%Y-%m-%dT%H.%M.%SZ"))
    return sorted_directories
from datetime import datetime

def list_new_tables_in_date_directories(
    container_client,
    base_folder,
    last_checked_date,
    connection_id,
    cmp_id,
    prefixes_override=None,
):
    new_tables = set()
    most_recent_date_dir = None
    attributes_dict = {}

    date_directories_save = list_date_directories(
        container_client,
        cmp_id,
        connection_id,
        last_checked_date,
        base_folder=base_folder,
        prefixes_override=prefixes_override,
    )
    all_directories = insert_or_update_date_directories(connection_id, date_directories_save)
    if last_checked_date:
        directories = filter_directories_greater_than(all_directories, last_checked_date)
    else:
        directories = all_directories

    print("Completed: Listing blobs in container")
    print(f"Start: Filtering date directories from {len(directories)} blobs")
    date_directories = sorted(
        [d for d in directories if is_valid_date(d)],
        key=lambda x: datetime.strptime(x, "%Y-%m-%dT%H.%M.%SZ")
    )
    print(f"Completed: Filtering date directories. Found {len(date_directories)} valid date directories")

    if not date_directories:
        print("No valid date directories found.")
        clear_connection_retrieval_progress(connection_id)
        return set(), {}, last_checked_date

    total_dirs = len(date_directories)
    update_connection_retrieval_progress(connection_id, 0, total_dirs)

    # Ensure chunk_size is at least 1
    num_chunks = 5
    chunk_size = max(1, len(date_directories) // num_chunks)

    # Split the date_directories into chunks for parallel processing
    chunks = [date_directories[i:i + chunk_size] for i in range(0, len(date_directories), chunk_size)]

    print(f"Start: Processing {len(chunks)} chunks with thread pool")
    # Celery-safe parallelism: thread pool for I/O-bound blob + JSON fetch.
    results = []
    worker_count = min(LISTING_MAX_CHUNK_WORKERS, max(1, len(chunks)))
    chunk_offsets = []
    offset = 0
    for chunk in chunks:
        chunk_offsets.append(offset)
        offset += len(chunk)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(
                process_date_directories_chunk,
                container_client,
                base_folder,
                last_checked_date,
                chunk,
                connection_id,
                chunk_offsets[idx - 1],
                total_dirs,
            ): idx
            for idx, chunk in enumerate(chunks, start=1)
        }
        completed = 0
        for future in as_completed(future_to_index):
            completed += 1
            chunk_idx = future_to_index[future]
            print(
                f"[Main Process] Chunk progress: {completed}/{len(chunks)} "
                f"(completed_chunk={chunk_idx})"
            )
            results.append(future.result())

    print("Completed: Processing chunks in parallel")
    # Merge the results from all chunks
    for chunk_new_tables, chunk_attributes_dict, chunk_most_recent_date_dir in results:
        new_tables.update(chunk_new_tables)
        attributes_dict.update(chunk_attributes_dict)
        if chunk_most_recent_date_dir:
            most_recent_date_dir = chunk_most_recent_date_dir

    if not new_tables:
        clear_connection_retrieval_progress(connection_id)
        return set(), {}, last_checked_date

    update_connection_retrieval_progress(connection_id, total_dirs, total_dirs)
    return new_tables, attributes_dict, most_recent_date_dir

def process_date_directories_chunk(
    container_client,
    base_folder,
    last_checked_date,
    date_chunk,
    connection_id=None,
    chunk_start=0,
    total_dirs=0,
):
    new_tables = set()
    attributes_dict = {}
    most_recent_date_dir = None
    dtype_conversion = {
            'guid': 'VARCHAR(512)',         # GUID/UUID mapped to VARCHAR(512)
            'dateTime': 'TIMESTAMP_TZ(9)',  # Both dateTime and dateTimeOffset use TIMESTAMP_TZ(9)
            'dateTimeOffset': 'TIMESTAMP_TZ(9)',
            'int64': 'NUMBER(38,0)',        # Large integer
            'decimal': 'FLOAT',             # Decimal mapped to FLOAT for compatibility
            'float': 'FLOAT',               # Float type mapped to Snowflake FLOAT
            'string': 'VARCHAR',            # String with a defined max length
            'boolean': 'BOOLEAN'            # Logical type
        }

    print(f"Start: Processing chunk of {len(date_chunk)} date directories")
    start_processing = last_checked_date is None

    for idx, date_dir in enumerate(date_chunk, start=1):
        if connection_id and total_dirs > 0 and (
            idx == 1 or idx % 10 == 0 or idx == len(date_chunk)
        ):
            global_idx = chunk_start + idx
            update_connection_retrieval_progress(
                connection_id, global_idx, total_dirs, notify_ws=True
            )
        if idx % 50 == 0:
            print(
                f"Chunk directory progress: {idx}/{len(date_chunk)} "
                f"(global={chunk_start + idx}/{total_dirs}, latest={date_dir})"
            )
        if start_processing:
            # Construct the full path for the date directory
            full_date_path = f"{base_folder}/{date_dir}"

            # Look for the 'Microsoft.Athena.TrickleFeedService' directory
            trickle_feed_path = f"{full_date_path}/Microsoft.Athena.TrickleFeedService"

            # List all files under the 'Microsoft.Athena.TrickleFeedService' folder
            trickle_feed_files = [
                blob.name[len(trickle_feed_path):].strip('/')
                for blob in container_client.list_blobs(name_starts_with=trickle_feed_path + '/')
            ]

            # Filter files that match the pattern '*-model.json'
            table_files = {file for file in trickle_feed_files if file.endswith('-model.json')}

            for file_name in table_files:
                table_name = file_name.split('-model.json')[0]

                # Skip if attributes for the table are already extracted
                if table_name in attributes_dict:
                    print(f"Skipping attributes extraction for table: {table_name}, already processed.")
                    continue

                table_json_path = f"{trickle_feed_path}/{file_name}"

                # Download and parse the JSON file
                blob_client = container_client.get_blob_client(table_json_path)
                json_data = json.loads(blob_client.download_blob().readall())

                # Extract attributes and store in a dictionary with field name and data type
                attributes = {}
                for attribute in json_data.get("entities", [{}])[0].get("attributes", []):
                    name = attribute.get("name")
                    data_type = attribute.get("dataType")
                    max_length = attribute.get("maxLength", -1)
                    
                    # Check for primary key (similar to Salesforce logic)
                    # Microsoft Dynamics doesn't provide idLookup, so we check name == "Id"
                    is_pk = name == "Id"

                    # Determine Snowflake type with length adjustments for VARCHAR
                    if data_type == 'string' and max_length > 0:
                        # Adjust VARCHAR length to nearest multiple of 512
                        length = ((max_length - 1) // 512 + 1) * 512
                        snowflake_type = f"VARCHAR({length})"
                    else:
                        snowflake_type = dtype_conversion.get(data_type, 'VARCHAR(512)')  # Default to VARCHAR(512) if unknown

                    attributes[name] = {
                        "data_type": snowflake_type,
                        "is_primary_key": is_pk
                    }

                # # Add custom fields with default types
                # attributes["_SPECT_SYNCED"] = 'TIMESTAMP_TZ(9)'
                # attributes["_SPECT_DELETED"] = 'BOOLEAN'

                # Add to the global dictionary
                attributes_dict[table_name] = attributes

                # Add table to the set of new tables
                new_tables.add(table_name)

        elif listing_is_directory_greater_than(date_dir, last_checked_date):
            start_processing = True

        most_recent_date_dir = date_dir

    print(f"Completed: Processing chunk of {len(date_chunk)} date directories")
    return new_tables, attributes_dict, most_recent_date_dir

def listing_infer_base_folder(container_client,cmp_id,connection_id, max_blobs=10):
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

        # Find the common prefix at the directory level
        base_folder = "/".join(os.path.commonprefix(paths).split("/")[:-1])

        return base_folder

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


def is_valid_date(directory_name):
    try:
        datetime.strptime(directory_name, "%Y-%m-%dT%H.%M.%SZ")
        return True
    except ValueError:
        return False

def listing_is_directory_greater_than(directory_name, last_checked_date):
    if last_checked_date and last_checked_date.lower() == "null":
        last_checked_date = None

    if last_checked_date is None:
        return True

    last_checked_date = last_checked_date.strip('"')
    dir_date = datetime.strptime(directory_name, "%Y-%m-%dT%H.%M.%SZ")
    last_checked = datetime.strptime(last_checked_date, "%Y-%m-%dT%H.%M.%SZ")
    return dir_date > last_checked