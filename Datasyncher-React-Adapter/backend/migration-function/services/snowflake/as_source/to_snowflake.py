import asyncio
import gc
import os
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from itertools import combinations

import pandas as pd
import snowflake.connector

from models.snowflake_models import SnowflakeMigration

# Import cleanup utility
from utils.celery_cleanup_utils import handle_migration_completion
from services.snowflake.service import SnowflakeService, normalize_pem_key
from utils.database_utils import (
    update_table_status_snowflake,
    get_last_checked_dates_and_fields,
    data_transfer_logs,
    update_combination_formula
)
from utils.error_utils import insert_tool_log, log_migration_completion
import psycopg2
import time

from google.auth import compute_engine
from google.cloud import storage

credentials = compute_engine.Credentials()
storage_client = storage.Client(credentials=credentials)

# =============================================================================
# GLOBAL CONSTANTS - Snowflake to Snowflake Service
# =============================================================================

# Snowflake Configuration Constants
SNOWFLAKE_STORAGE_INTEGRATION = 'DATASYNCHERSDEV'
GCS_BUCKET_URL = 'gcs://meta_data_bucket_ds_development/'
BUCKET_NAME = "meta_data_bucket_ds_development"
PROJECT_ID = "datasyncher-react-qa-dev"



# 1. Standard staging table name
def get_staging_table_name(base_table: str) -> str:
    return f"{base_table}__STAGING"

# 2. Build Snowflake connection params from a config dict
def _build_conn_params(config):
    print("DEBUG: Building Snowflake connection parameters")
    print(f"DEBUG: Raw config received -> {config}")

    conn_params = {
        "account": config["account"],
        "user": config.get("user") or config.get("username"),
        "warehouse": config.get("warehouse", "COMPUTE_WH"),
        "role": config.get("role"),
        "database": config.get("database"),
        "schema": config.get("schema"),
        "client_session_keep_alive": True
    }
    print(f"DEBUG: Base connection params (without auth) -> {conn_params}")
    print(f"DEBUG: Connection will use schema: {conn_params.get('schema')} in database: {conn_params.get('database')}")

    authentication_type = config.get("authentication_type", "password").lower()
    print(f"DEBUG: Authentication type detected -> {authentication_type}")

    if authentication_type == "password":
        conn_params["password"] = config.get("password")
        print(f"DEBUG: Using password authentication, password length -> {len(config.get('password', ''))}")

    elif authentication_type == "key_pair":
        private_key_str = config.get("private_key")
        private_key_passphrase = config.get("passphrase")
        print("DEBUG: Using key pair authentication")
        print(f"DEBUG: Passphrase provided -> {'YES' if private_key_passphrase else 'NO'}")

        if not private_key_str:
            raise Exception("Private key string is required for key-pair authentication.")

        print(f"DEBUG: Raw private key string length -> {len(private_key_str)}")

        private_key_strip = normalize_pem_key(private_key_str)
        print(f"DEBUG: Normalized private key string length -> {len(private_key_strip)}")

        try:
            from datasyncher_connections.snowflake.pem import load_private_key_object

            private_key = load_private_key_object(private_key_strip, private_key_passphrase)
            print(f"DEBUG: Private key object loaded successfully -> {private_key}")
        except Exception as e:
            print(f"ERROR: Failed to load private key: {e}")
            raise

        conn_params["private_key"] = private_key

    else:
        raise Exception(f"Unsupported authentication type: {authentication_type}")

    print("DEBUG: Final connection parameters ready")
    return conn_params

def postgres_connection():
    """
    Create a PostgreSQL connection with retry logic
    """
    from datasyncher_connections.postgres import create_postgres_connection

    connection = create_postgres_connection(retries=3, retry_delay=2, validate=True)
    print("✅ Database connection established")
    return connection

# insert_tool_log is now imported from utils.error_utils (centralized utility with race condition protection)

# 3. Get current DB and schema
def _get_current_db_schema(conn, schema):
    with conn.cursor() as cursor:
        if schema:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
        cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
        return cursor.fetchone()

# 4. Generate CREATE TABLE DDL for tables WITHOUT ROW_HASH and GUID renamed
def get_create_table_ddl_for_table(src_cursor, src_db, src_schema, table_name, dest_db, dest_schema, dest_table,is_updated,is_deleted):
    src_cursor.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
        FROM {src_db}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{src_schema}' AND TABLE_NAME = '{table_name}'
        ORDER BY ORDINAL_POSITION
    """)
    columns = src_cursor.fetchall()
    col_defs = []
    for col_name, data_type, char_len, num_precision, num_scale, is_nullable in columns:
        dt = data_type.upper()
        if dt in ("VARCHAR", "CHAR", "CHARACTER VARYING", "TEXT"):
            if char_len and char_len > 0:
                dt = f"VARCHAR({char_len})"
            else:
                dt = "VARCHAR"
        elif dt in ("NUMBER", "NUMERIC", "DECIMAL"):
            if num_precision is not None and num_scale is not None:
                dt = f"NUMBER({num_precision},{num_scale})"
            elif num_precision is not None:
                dt = f"NUMBER({num_precision})"
            else:
                dt = "NUMBER"
        elif dt in ("TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"):
            pass
        nullable_str = " NOT NULL" if is_nullable == "NO" else ""
        col_defs.append(f'"{col_name}" {dt}{nullable_str}')

    # Add renamed GUID column as per new logic
    col_defs += [
        '_DATASYNCHER_ID VARCHAR(512)',
        f'{is_updated} TIMESTAMP_NTZ',
        f'{is_deleted} BOOLEAN',
    ]
    cols_str = ', '.join(col_defs)
    ddl = f"CREATE TABLE IF NOT EXISTS {dest_db}.{dest_schema}.{dest_table} ({cols_str})"
    return ddl

# 5. Generate CREATE TABLE DDL for views similar to tables
def get_create_table_ddl_for_view(src_cursor, src_db, src_schema, view_name, dest_db, dest_schema, dest_table,is_updated,is_deleted):
    src_cursor.execute(f"SELECT * FROM {src_db}.{src_schema}.{view_name} LIMIT 0")
    cols = [desc[0] for desc in src_cursor.description]
    col_defs = []
    for col in cols:
        src_cursor.execute(f"""
            SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
            FROM {src_db}.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{src_schema}' AND TABLE_NAME = '{view_name}' AND COLUMN_NAME = '{col}'
        """)
        dt_row = src_cursor.fetchone()
        if not dt_row:
            dt = "VARCHAR"
            nullable_str = ""
        else:
            data_type, char_len, num_precision, num_scale, is_nullable = dt_row
            dt = data_type.upper()
            if dt in ("VARCHAR", "CHAR", "CHARACTER VARYING", "TEXT"):
                if char_len and char_len > 0:
                    dt = f"VARCHAR({char_len})"
                else:
                    dt = "VARCHAR"
            elif dt in ("NUMBER", "NUMERIC", "DECIMAL"):
                if num_precision is not None and num_scale is not None:
                    dt = f"NUMBER({num_precision},{num_scale})"
                elif num_precision is not None:
                    dt = f"NUMBER({num_precision})"
                else:
                    dt = "NUMBER"
            elif dt in ("TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"):
                pass
            
        col_defs.append(f'"{col}" {dt}')

    col_defs += [
        '_DATASYNCHER_ID VARCHAR(512)',
        f'{is_updated} TIMESTAMP_NTZ',
        f'{is_deleted} BOOLEAN',
    ]
    ddl = f"CREATE TABLE IF NOT EXISTS {dest_db}.{dest_schema}.{dest_table} ({', '.join(col_defs)})"
    return ddl

def sanitize_filename_component(s: str) -> str:
    """Sanitize components of filenames by replacing unsafe characters with underscores."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', s)
# 7. Candidate characteristic columns remain the same
def get_candidate_group_columns(cur, db, schema, table):
    cur.execute(f"""
        SELECT COLUMN_NAME
        FROM {db}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='{schema}'
          AND TABLE_NAME='{table}'
          AND UPPER(DATA_TYPE) IN ('VARCHAR', 'CHAR', 'STRING', 'TEXT', 'DATE')
        ORDER BY ORDINAL_POSITION
    """)
    return [row[0] for row in cur.fetchall()]


# 8. Distinct counts for candidates - unchanged
def get_cardinality_counts(cur, db, schema, table, columns):
    if not columns:
        return {}
    exprs = ','.join([f"COUNT(DISTINCT \"{col}\") AS \"{col}_count\"" for col in columns])
    sql = f"SELECT {exprs} FROM {db}.{schema}.{table}"
    cur.execute(sql)
    row = cur.fetchone()
    print("colums", row)
    return dict(zip(columns, row))


# 9. Check if all groups within threshold - unchanged
def group_within_threshold(cur, db, schema, table, cols, threshold):
    group_cols = ','.join(f'"{c}"' for c in cols)
    cur.execute(f"SELECT {group_cols}, COUNT(*) FROM {db}.{schema}.{table} GROUP BY {group_cols} HAVING COUNT(*) > {threshold} LIMIT 1")
    result = cur.fetchone()
    print(result)
    return result is None


# 10. Find best grouping (singles, combos) - unchanged
def find_optimal_grouping(cur, db, schema, table, cols, threshold=50000):
    card_map = get_cardinality_counts(cur, db, schema, table, cols)
    sorted_cols = sorted(cols, key=lambda c: card_map.get(c, 1e10))
    print(sorted_cols)
    for width in range(1, len(sorted_cols) + 1):
        for combo in combinations(sorted_cols, width):
            combo_card = 1
            for c in combo:
                combo_card *= card_map.get(c, threshold + 1)
            if combo_card > threshold * 10:
                continue
            if group_within_threshold(cur, db, schema, table, list(combo), threshold):
                return list(combo)
    return []

# def upload_parquet_files_and_load(conn, parquet_file_paths, snowflake_stage, target_table, dest_db, dest_schema):
#     with conn.cursor() as cur:
#         stage_name = snowflake_stage[1:] if snowflake_stage.startswith('@') else snowflake_stage
#         cur.execute(f"CREATE STAGE IF NOT EXISTS {stage_name}")
        
#         for file_path in parquet_file_paths:
#             filename = os.path.basename(file_path)
#             put_cmd = f"PUT file://{file_path} @{stage_name} OVERWRITE=TRUE"
#             print(f"[INFO] Uploading file: {filename} to stage {stage_name}")
#             cur.execute(put_cmd)

#             copy_cmd = f"""
#                 COPY INTO {dest_db}.{dest_schema}.{target_table}
#                 FROM @{stage_name}/{filename}
#                 FILE_FORMAT = (TYPE = 'PARQUET')
#                 MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
#                 PURGE = TRUE
#             """

#             print(f"[INFO] Loading {filename} into table {target_table}")
#             cur.execute(copy_cmd)

#         conn.commit()
#         print(f"[INFO] Loaded {len(parquet_file_paths)} Parquet files into {target_table}")
# from google.api_core.exceptions import NotFound  # Unused

def ensure_gcs_storage_integration(connection, integration_name, bucket_url, connection_id, cmp_id):
    
    check_integration_sql = f"""
    SHOW STORAGE INTEGRATIONS LIKE '{integration_name}';
    """
    with connection.cursor() as cursor:
        cursor.execute(check_integration_sql)
        result = cursor.fetchall()
        if result:
            print(f"✅ Storage integration '{integration_name}' already exists.")
            return result
        print(f"🚧 Creating storage integration '{integration_name}'...")
        create_integration_sql = f"""
        CREATE STORAGE INTEGRATION {integration_name}
        TYPE = EXTERNAL_STAGE
        STORAGE_PROVIDER = 'GCS'
        ENABLED = TRUE
        STORAGE_ALLOWED_LOCATIONS = ('{bucket_url}');
        """
        try:
            cursor.execute(create_integration_sql)
            print(f"✅ Storage integration '{integration_name}' created successfully.")
            return None
        except Exception as e:
            # Handle race condition: if another parallel process created it, treat as success
            error_msg = str(e)
            if "already exists" in error_msg or "42710" in error_msg or "002002" in error_msg:
                print(f"✅ Storage integration '{integration_name}' already exists (created by another process).")
                # Re-check to get the integration details
                cursor.execute(check_integration_sql)
                result = cursor.fetchall()
                return result if result else None
            else:
                # Re-raise if it's a different error
                raise
    


def get_gcp_service_account(connection, integration_name, cmp_id, connection_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DESC INTEGRATION {integration_name}")
            result = cursor.fetchall()

            for row in result:
                c_property, c_type, c_value, c_default = row
                if c_property == "STORAGE_GCP_SERVICE_ACCOUNT":
                    print(f"✅ GCP Service Account found: {c_value}")
                    return c_value

        raise ValueError(f"❌ STORAGE_GCP_SERVICE_ACCOUNT not found in integration '{integration_name}'.")

    except Exception as e:
        raise RuntimeError(f"❌ Error fetching GCP Service Account: {str(e)}")


def ensure_service_account_and_permissions(connection, integration_name, bucket_name, project_id, cmp_id, connection_id):
    try:
        service_account_email = get_gcp_service_account(connection, integration_name, cmp_id, connection_id)
        member = f"serviceAccount:{service_account_email}"
        role = "roles/storage.objectAdmin"

        storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)

        # Fetch IAM policy
        policy = bucket.get_iam_policy(requested_policy_version=3)

        role_found = False
        for binding in policy.bindings:
            if binding["role"] == role:
                if member in binding["members"]:
                    print(f"✅ '{member}' already has role '{role}' on bucket '{bucket_name}'.")
                    return
                else:
                    print(f"🔄 Adding '{member}' to existing role '{role}'.")
                    # bindings.members is a list for google-cloud-storage policy
                    members = list(binding["members"]) if isinstance(binding["members"], set) else binding["members"]
                    if member not in members:
                        members.append(member)
                    binding["members"] = members
                    role_found = True
                    break

        if not role_found:
            print(f"➕ Adding new binding: '{member}' → '{role}'")
            policy.bindings.append({
                "role": role,
                "members": [member],
            })

        bucket.set_iam_policy(policy)
        print(f"✅ IAM policy updated: '{member}' granted '{role}' on bucket '{bucket_name}'.")

    except Exception as e:
        raise RuntimeError(f"❌ Error setting IAM permissions: {str(e)}")


def upload_parquet_files_and_load(
    conn,
    gcs_parquet_urls,
    target_table,
    dest_db,
    dest_schema,
    cmp_id,
    connection_id,
    is_integration_checked=False,
):
    """
    Loads Parquet files from GCS into Snowflake table using STORAGE_INTEGRATION.

    Args:
        conn: Snowflake connection object
        gcs_parquet_urls: List of full GCS URIs (e.g., gs://bucket/path/file.parquet)
        target_table: Destination table in Snowflake
        dest_db: Target database
        dest_schema: Target schema
        storage_integration: Name of the Snowflake GCS integration (e.g., DEMO_STORAGE)
        gcs_bucket_url: Full GCS path prefix (e.g., gs://my-bucket/prefix/)
        bucket_name: Name of the GCS bucket (e.g., my-bucket)
        project_id: GCP project ID
        cmp_id: Company ID or metadata for logging
        connection_id: Connection ID for tracking
        destination_engine: SQLAlchemy engine for metadata queries
        is_integration_checked: Whether the storage integration and permissions are already validated
    """
    if not gcs_parquet_urls:
        print("[WARN] No Parquet files provided to load.")
        return
    storage_integration = SNOWFLAKE_STORAGE_INTEGRATION
    gcs_bucket_url_sf = GCS_BUCKET_URL
    bucket_name = BUCKET_NAME
    project_id = PROJECT_ID
    # ✅ Ensure storage integration and IAM is applied only once
    if not is_integration_checked:
        res = ensure_gcs_storage_integration(conn, storage_integration, gcs_bucket_url_sf, connection_id, cmp_id)
        if not res:
            ensure_service_account_and_permissions(conn, storage_integration, bucket_name, project_id, cmp_id, connection_id)
        is_integration_checked = True

    loaded_count = 0
    with conn.cursor() as cur:
        for gcs_url in gcs_parquet_urls:
            try:
                gcs_url = gcs_url.strip()
                

                print(f"[INFO] ⬇️ Loading {gcs_url} into {dest_db}.{dest_schema}.{target_table}")

                copy_cmd = f"""
                    COPY INTO "{dest_db}"."{dest_schema}"."{target_table}"
                    FROM '{gcs_url}'
                    STORAGE_INTEGRATION = {storage_integration}
                    FILE_FORMAT = (
                        TYPE = 'PARQUET'
                        NULL_IF = ('None','<NA>')
                    )

                    MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
                """

                cur.execute(copy_cmd)
                loaded_count += 1

            except Exception as e:
                print(f"[ERROR] ❌ Failed to load {gcs_url}: {e}")

        conn.commit()
        print(f"[INFO] ✅ Loaded {loaded_count}/{len(gcs_parquet_urls)} Parquet files into {dest_db}.{dest_schema}.{target_table}")

def sync_table_columns(src_cur, dest_cur, src_db, src_schema, src_table, dest_db, dest_schema, dest_table):
    src_cur.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
               NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
        FROM {src_db}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{src_schema}' AND TABLE_NAME = '{src_table}'
        ORDER BY ORDINAL_POSITION
    """)
    src_cols = src_cur.fetchall()
    src_cols_map = {col[0].upper(): col for col in src_cols}

    dest_cur.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
               NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
        FROM {dest_db}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{dest_schema}' AND TABLE_NAME = '{dest_table}'
        ORDER BY ORDINAL_POSITION
    """)
    dest_cols = dest_cur.fetchall()
    dest_cols_map = {col[0].upper(): col for col in dest_cols}

    missing_cols = [src_cols_map[col] for col in src_cols_map if col not in dest_cols_map]

    if missing_cols:
        print(f"Altering {dest_db}.{dest_schema}.{dest_table} to add {len(missing_cols)} missing columns:")
        for col in missing_cols:
            col_name, data_type, char_len, num_prec, num_scale, is_nullable = col
            dt = data_type.upper()
            if dt in ("VARCHAR", "CHAR", "CHARACTER VARYING", "TEXT"):
                if char_len and char_len > 0:
                    dt = f"VARCHAR({char_len})"
                else:
                    dt = "VARCHAR"
            elif dt in ("NUMBER", "NUMERIC", "DECIMAL"):
                if num_prec is not None and num_scale is not None:
                    dt = f"NUMBER({num_prec},{num_scale})"
                elif num_prec is not None:
                    dt = f"NUMBER({num_prec})"
                else:
                    dt = "NUMBER"
            elif dt in ("TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"):
                pass 
            nullable_str = " NOT NULL" if is_nullable == "NO" else ""
            alter_sql = f'ALTER TABLE {dest_db}.{dest_schema}.{dest_table} ADD COLUMN "{col_name}" {dt}{nullable_str}'
            print(f"Executing: {alter_sql}")
            dest_cur.execute(alter_sql)
    else:
        print(f"No new columns to add to {dest_db}.{dest_schema}.{dest_table}")

def sanitize_filename(filename: str) -> str:
    sanitized = re.sub(r'[^a-zA-Z0-9\-_\.]+', '_', filename)
    return sanitized.strip('._')

def get_staging_table_ddl(src_cursor, src_db, src_schema, base_table, dest_db, dest_schema,updated_at,deleted_at):
    src_cursor.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
        FROM {src_db}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{src_schema}' AND TABLE_NAME = '{base_table}'
        ORDER BY ORDINAL_POSITION
    """)
    columns = src_cursor.fetchall()
    col_defs = []
    for col_name, data_type, char_len, num_precision, num_scale, is_nullable in columns:
        dt = data_type.upper()
        if dt in ("VARCHAR", "CHAR", "CHARACTER VARYING", "TEXT"):
            if char_len and char_len > 0:
                dt = f"VARCHAR({char_len})"
            else:
                dt = "VARCHAR"
        elif dt in ("NUMBER", "NUMERIC", "DECIMAL"):
            if num_precision is not None and num_scale is not None:
                dt = f"NUMBER({num_precision},{num_scale})"
            elif num_precision is not None:
                dt = f"NUMBER({num_precision})"
            else:
                dt = "NUMBER"
        elif dt in ("TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"):
            pass
        
        col_defs.append(f'"{col_name}" {dt}')
    # Add system columns
    col_defs += [
        '_DATASYNCHER_ID VARCHAR(512)',
        f'{updated_at} TIMESTAMP_NTZ',
        f'{deleted_at} BOOLEAN'
    ]
    staging_table = get_staging_table_name(base_table)
    ddl = f"CREATE TABLE IF NOT EXISTS {dest_db}.{dest_schema}.{staging_table} ({', '.join(col_defs)})"
    return ddl

def sync_staging_to_target(conn, staging_table, target_table, is_updated, is_deleted):
    with conn.cursor() as cur:
        # Get columns in staging table
        cur.execute(f"""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = '{staging_table.upper()}'
              AND TABLE_SCHEMA = CURRENT_SCHEMA()
              AND TABLE_CATALOG = CURRENT_DATABASE()
            ORDER BY ORDINAL_POSITION
        """)
        columns = [row[0] for row in cur.fetchall()]
        if not columns:
            raise RuntimeError(f"No columns found for staging table {staging_table}")

        insert_columns = ", ".join(f'"{col}"' for col in columns)
        update_columns = [col for col in columns if col.upper() != "_DATASYNCHER_ID"]
        update_set = ", ".join(f'tgt."{col}" = stg."{col}"' for col in update_columns)

        # Count rows matched for update
        cur.execute(f"""
            SELECT COUNT(*)
            FROM {target_table} tgt
            INNER JOIN {staging_table} stg ON tgt._DATASYNCHER_ID = stg._DATASYNCHER_ID
        """)
        matched_rows = cur.fetchone()[0]
        print(f"🔄 Rows matched for update: {matched_rows}")

        # Count rows to insert
        cur.execute(f"""
            SELECT COUNT(*)
            FROM {staging_table} stg
            LEFT JOIN {target_table} tgt ON tgt._DATASYNCHER_ID = stg._DATASYNCHER_ID
            WHERE tgt._DATASYNCHER_ID IS NULL
        """)
        inserted_rows = cur.fetchone()[0]
        print(f"➕ Rows to insert: {inserted_rows}")

        print("Running MERGE...")
        merge_sql = f"""
            MERGE INTO {target_table} tgt
            USING {staging_table} stg
            ON tgt._DATASYNCHER_ID = stg._DATASYNCHER_ID
            WHEN MATCHED THEN
              UPDATE SET {update_set}
            WHEN NOT MATCHED THEN
              INSERT ({insert_columns})
              VALUES ({', '.join(f'stg."{col}"' for col in columns)})
        """
        cur.execute(merge_sql)
        print("✅ Merge complete.")

        # Count rows to soft delete
        cur.execute(f"""
            SELECT COUNT(*)
            FROM {target_table} tgt
            LEFT JOIN {staging_table} stg ON tgt._DATASYNCHER_ID = stg._DATASYNCHER_ID
            WHERE stg._DATASYNCHER_ID IS NULL AND tgt.{is_deleted} = FALSE
        """)
        deleted_rows = cur.fetchone()[0]
        print(f"🗑️ Rows to be soft deleted: {deleted_rows}")

        print("Running soft delete...")
        delete_sql = f"""
            UPDATE {target_table}
            SET {is_deleted} = TRUE
            WHERE _DATASYNCHER_ID IN (
                SELECT tgt._DATASYNCHER_ID
                FROM {target_table} tgt
                LEFT JOIN {staging_table} stg ON tgt._DATASYNCHER_ID = stg._DATASYNCHER_ID
                WHERE stg._DATASYNCHER_ID IS NULL AND tgt.{is_deleted} = FALSE
            )
        """
        cur.execute(delete_sql)
        print("✅ Soft delete complete.")

        print("🧹 Dropping staging table...")
        cur.execute(f"DROP TABLE {staging_table}")
        conn.commit()
        print("📝 All changes committed.")

        return {
            "updated": 0,
            "inserted": inserted_rows,
            "deleted": deleted_rows,
            "columns_data": len(columns)
        }



from google.cloud import storage  # Make sure this is installed


# Upload a file to Google Cloud Storage
def upload_to_gcs(local_file_path, bucket_name, gcs_path):
    client = storage_client if 'storage_client' in globals() else storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(local_file_path)
    print(f"✅ Uploaded to gs://{bucket_name}/{gcs_path}")


# Delete files from a specific GCS folder (prefix)
def cleanup_gcs_folder(bucket_name, prefix):
    client = storage_client if 'storage_client' in globals() else storage.Client()
    bucket = client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))
    for blob in blobs:
        blob.delete()
    print(f"🧹 Cleaned up {len(blobs)} files from gs://{bucket_name}/{prefix}")




def get_effective_group_columns(
    src_cur,
    source_db,
    source_schema,
    table_name,
    formula_combination,
    batch_threshold,
    connection_id  # Add connection_id as parameter
):
    if formula_combination is None:
        # Compute group columns from scratch
        candidates = get_candidate_group_columns(src_cur, source_db, source_schema, table_name)
        computed_group_cols = find_optimal_grouping(
            src_cur, source_db, source_schema, table_name, candidates, batch_threshold
        )
        # Save computed formula
        update_combination_formula(connection_id, table_name, computed_group_cols)
        return computed_group_cols

    # If formula_combination is provided, check its distinct combination count
    columns_expr = ', '.join(f'"{col}"' for col in formula_combination)
    src_cur.execute(f"""
        SELECT COUNT(DISTINCT {columns_expr})
        FROM {source_db}.{source_schema}.{table_name}
    """)
    distinct_count = src_cur.fetchone()[0]

    if distinct_count > batch_threshold:
        print(f"[INFO] Distinct combinations {distinct_count} exceed threshold {batch_threshold}, recomputing...")
        candidates = get_candidate_group_columns(src_cur, source_db, source_schema, table_name)
        computed_group_cols = find_optimal_grouping(
            src_cur, source_db, source_schema, table_name, candidates, batch_threshold
        )
        # Update recomputed formula
        update_combination_formula(connection_id, table_name, computed_group_cols)
        return computed_group_cols

    print(f"[INFO] Using provided formula_combination (distinct: {distinct_count})")
    return formula_combination


# from decimal import Decimal
# import pandas as pd
# import pyarrow as pa
from typing import Tuple

# def convert_numeric_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, pa.Schema]:
#     """
#     Convert numeric columns in a DataFrame to Decimal with dynamic precision/scale
#     and return the updated DataFrame + Arrow schema.
#     """
#     schema_fields = []

#     for col in df.columns:
#         if pd.api.types.is_integer_dtype(df[col]) or pd.api.types.is_float_dtype(df[col]):
#             # Calculate precision & scale dynamically
#             max_precision, max_scale = 1, 0
#             for val in df[col].dropna():
#                 dec = Decimal(str(val))
#                 precision = len(dec.as_tuple().digits)
#                 scale = -dec.as_tuple().exponent if dec.as_tuple().exponent < 0 else 0
#                 max_precision = max(max_precision, precision)
#                 max_scale = max(max_scale, scale)

#             max_precision = min(38, max_precision)  # Snowflake max precision
#             max_scale = min(max_precision, max_scale)

#             # Apply conversion to Decimal
#             df[col] = df[col].apply(lambda x: Decimal(str(x)) if pd.notna(x) else None)

#             schema_fields.append(pa.field(col, pa.decimal128(max_precision, max_scale)))
#         else:
#             schema_fields.append(pa.field(col, pa.string()))

#     return df, pa.schema(schema_fields)

import pyarrow as pa
import pandas as pd
from decimal import Decimal, ROUND_HALF_UP

def build_snowflake_schema(df: pd.DataFrame, snowflake_columns: list) -> Tuple[pd.DataFrame, pa.Schema]:
    fields = []

    for col, sf_type in snowflake_columns:
        sf_type = sf_type.upper()

        if col not in df.columns:
            df[col] = pd.NA
            fields.append(pa.field(col, pa.null()))
            continue

        if sf_type.startswith("NUMBER"):
            df[col] = df[col].apply(lambda v: str(v) if pd.notna(v) else None)
            fields.append(pa.field(col, pa.string()))


        elif sf_type.startswith(("VARCHAR", "CHAR", "TEXT")):
            df[col] = df[col].astype("string[pyarrow]")
            fields.append(pa.field(col, pa.string()))

        elif sf_type.startswith("BOOLEAN"):
            df[col] = df[col].astype("boolean")
            fields.append(pa.field(col, pa.bool_()))

        elif sf_type.startswith(("FLOAT", "DOUBLE", "REAL")):
            df[col] = pd.to_numeric(df[col], errors="coerce")
            fields.append(pa.field(col, pa.float64()))

        elif sf_type.startswith("DATE"):
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.normalize()
            df[col] = df[col].astype("datetime64[us]")
            fields.append(pa.field(col, pa.date32()))

        elif sf_type.startswith("TIMESTAMP"):
            # If formatted as string already
            df[col] = df[col].astype("string[pyarrow]")
            fields.append(pa.field(col, pa.string()))

        else:
            df[col] = df[col].astype("string[pyarrow]")
            fields.append(pa.field(col, pa.string()))

    return df, pa.schema(fields)





import numpy as np

import pandas as pd

def clean_dataframe_for_snowflake(df: pd.DataFrame, snowflake_columns: list) -> pd.DataFrame:
    """
    Ensure DataFrame is Snowflake-compatible:
    - Normalize null values to pd.NA
    - Cast pandas dtypes to nullable versions
    - Reorder columns to match Snowflake table schema
    """

    # Normalize all representations of nulls
    df = df.replace({"None": pd.NA, "nan": pd.NA, "NaN": pd.NA})

    # Convert to pandas nullable dtypes (Int64, Float64, string, boolean, datetime64)
    df = df.convert_dtypes(dtype_backend="numpy_nullable")

    # Enforce Snowflake column ordering (drop extras if any)
    sf_cols = [col for col, _ in snowflake_columns]
    df = df[[c for c in sf_cols if c in df.columns]]

    return df

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import gcsfs

def _migrate_table(source_conn, dest_conn, config, table_name,
                   batch_threshold, start_time, safety_interval, last_checked_data):

    formula_combination = last_checked_data.get("formula_combination", None)
    delta_timestamp_field = last_checked_data.get("delta_timestamp_field", None)
    last_synced_timestamp = last_checked_data.get("last_checked_date", None)
    truncate_table = last_checked_data.get("truncate_data", False)
    src_config=config.source_config
    dst_config=config.destination_config
    source_schema=src_config.get('schema')
    source_db=src_config.get('database')
    dest_schema=dst_config.get('schema')
    dest_db=dst_config.get('database')
    print(f"[DEBUG] Source: {source_db}.{source_schema}.{table_name} (for reading)")
    print(f"[DEBUG] Destination: {dest_db}.{dest_schema}.{table_name} (for writing)")
    full_src_name = f"{source_db}.{source_schema}.{table_name}"
    is_updated = config.is_updated
    is_deleted = config.is_deleted

    print(f"[INFO] Starting migration for {full_src_name}")
    update_table_status_snowflake(
        config.connection_id, table_name, "in_progress",
        config.job_name, config.migration_session_id
    )

    inserted = updated = deleted = 0
    columns_data = 0
    gcs_parquet_urls = []

    try:
        with source_conn.cursor() as src_cur, dest_conn.cursor() as dest_cur:
            
                

            # Detect object type
            src_cur.execute(f"""
                SELECT TABLE_TYPE 
                FROM {source_db}.INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = '{source_schema.upper()}'
                AND TABLE_NAME = '{table_name.upper()}'
            """)
            row = src_cur.fetchone()
            print("row as", row)

            if not row:
                # Fallback: use SHOW OBJECTS
                src_cur.execute(f"SHOW OBJECTS LIKE '{table_name}' IN SCHEMA {source_db}.{source_schema}")
                row = src_cur.fetchone()
                print("row from SHOW OBJECTS:", row)

                if not row:
                    raise ValueError(f"Source object {table_name} not found in {source_db}.{source_schema}")

                # SHOW OBJECTS: 5th column is KIND (TABLE, VIEW, etc.)
                table_type = row[4].upper()
            else:
                # INFORMATION_SCHEMA returns TABLE_TYPE directly (BASE TABLE, VIEW, etc.)
                table_type = row[0].upper()

            print(f"Resolved {table_name} as type: {table_type}")


            # Create schema and table
            dest_cur.execute(f"CREATE SCHEMA IF NOT EXISTS {dest_schema}")
            dest_conn.commit()

            if truncate_table:
                try:
                    print(f"🧹 Truncating target table: {dest_schema}.{table_name}")
                    dest_cur.execute(f"TRUNCATE TABLE IF EXISTS {dest_schema}.{table_name}")
                    dest_conn.commit()
                except Exception as e:
                    print(f"❌ Failed to truncate {dest_schema}.{table_name}: {e}")
                    dest_conn.rollback()


            if table_type in ("BASE TABLE", "TABLE", "MATERIALIZED VIEW"):
                ddl = get_create_table_ddl_for_table(
                    src_cur, source_db, source_schema, table_name,
                    dest_db, dest_schema, table_name, is_updated, is_deleted
                )
            elif table_type in ("VIEW", "SECURE VIEW"):
                ddl = get_create_table_ddl_for_view(
                    src_cur, source_db, source_schema, table_name,
                    dest_db, dest_schema, table_name, is_updated, is_deleted
                )
            else:
                raise ValueError(f"Unsupported source table type: {table_type}")

            dest_cur.execute(ddl)
            dest_conn.commit()

            sync_table_columns(src_cur, dest_cur, source_db, source_schema,
                               table_name, dest_db, dest_schema, table_name)
            dest_conn.commit()

            # Prepare staging
            staging_table = get_staging_table_name(table_name)
            staging_ddl = get_staging_table_ddl(
                src_cur, source_db, source_schema, table_name,
                dest_db, dest_schema, is_updated, is_deleted
            )
            dest_cur.execute(staging_ddl)
            dest_conn.commit()

            group_cols = get_effective_group_columns(
                src_cur,
                source_db,
                source_schema,
                table_name,
                formula_combination,
                batch_threshold,
                config.connection_id
            )

            # Collect columns
            src_cur.execute(f"""
                SELECT COLUMN_NAME FROM {source_db}.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = '{source_schema}' AND TABLE_NAME = '{table_name}'
                ORDER BY ORDINAL_POSITION
            """)
            all_source_columns = [row[0] for row in src_cur.fetchall()]
            base_columns = [c for c in all_source_columns if c.upper() not in ("_DATASYNCHER_ID", is_updated, is_deleted)]

            dest_cur.execute(f"""
                SELECT COLUMN_NAME, DATA_TYPE
                FROM {dest_db}.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = '{dest_schema}' AND TABLE_NAME = '{staging_table.upper()}'
                ORDER BY ORDINAL_POSITION
            """)
            snowflake_columns = [(row[0], row[1]) for row in dest_cur.fetchall()]
            staging_columns = [col for col, _ in snowflake_columns]

            print(snowflake_columns)
            # Build base WHERE clause if delta is present
            where_clause = ""
            if delta_timestamp_field and last_synced_timestamp:
                safe_interval = safety_interval if safety_interval is not None else 2
                cutoff_time = pd.to_datetime(last_synced_timestamp) - pd.Timedelta(minutes=safe_interval)
                upper_bound_time = pd.to_datetime(start_time)

                where_clause = f"""WHERE "{delta_timestamp_field}" > TO_TIMESTAMP('{cutoff_time.isoformat()}')
                                   AND "{delta_timestamp_field}" <= TO_TIMESTAMP('{upper_bound_time.isoformat()}')"""

            # Determine groups
            if group_cols:
                group_expr = ', '.join(f'"{c}"' for c in group_cols)
                src_cur.execute(f"""
                    SELECT {group_expr} FROM {source_db}.{source_schema}.{table_name}
                    {where_clause}
                    GROUP BY {group_expr}
                """)
                groups = src_cur.fetchall()
            else:
                groups = [()]

            bucket_name = BUCKET_NAME
            stage_prefix = f"{config.job_name}/{config.migration_session_id}/{table_name}"
            file_counter = 0
            fs = gcsfs.GCSFileSystem(project=PROJECT_ID)

            for batch_vals in groups:
                where_clauses = []

                if group_cols:
                    for col, val in zip(group_cols, batch_vals):
                        if val is None:
                            where_clauses.append(f'"{col}" IS NULL')
                        elif isinstance(val, str):
                            safe_val = val.replace("'", "''")
                            where_clauses.append(f'"{col}" = \'{safe_val}\'')
                        else:
                            where_clauses.append(f'"{col}" = {val}')

                if delta_timestamp_field and last_synced_timestamp:
                    where_clauses.append(
                        f'"{delta_timestamp_field}" > TO_TIMESTAMP(\'{cutoff_time.isoformat()}\')')
                    where_clauses.append(
                        f'"{delta_timestamp_field}" <= TO_TIMESTAMP(\'{upper_bound_time.isoformat()}\')')

                where_stmt = ' AND '.join(where_clauses)

                hash_expr = " || '|' || ".join([f"COALESCE(TO_VARCHAR(\"{col}\"), '')" for col in base_columns])

                quoted_cols = ', '.join(f'"{col}"' for col in base_columns)

                sql = f"""
                SELECT
                    {quoted_cols},
                    SHA2({hash_expr}, 256) AS "_DATASYNCHER_ID",
                    CURRENT_TIMESTAMP() AS "{is_updated}",
                    FALSE AS "{is_deleted}"
                FROM {source_db}.{source_schema}.{table_name}
                {f"WHERE {where_stmt}" if where_stmt else ""}
                """

                print(f"[DEBUG] Running query:\n{sql}")

                df = pd.read_sql(sql, source_conn)


                if df.empty:
                    continue
                
                # Debug: Show first 5 _DATASYNCHER_ID values directly after reading
                if "_DATASYNCHER_ID" in df.columns:
                    print("[DEBUG] First 5 _DATASYNCHER_ID values from Snowflake:", df["_DATASYNCHER_ID"].head().to_list())
                else:
                    print("[DEBUG] _DATASYNCHER_ID column not found in query result!")

            
                # Ensure all staging columns are present
                for col in staging_columns:
                    if col not in df.columns:
                        df[col] = None
                df = df[staging_columns]

                # Format datetime columns for Parquet
                for col in df.columns:
                    if pd.api.types.is_datetime64_any_dtype(df[col]):
                        df[col] = df[col].dt.tz_localize(None)
                        df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')
                # for col in df.columns:
                #     if pd.api.types.is_datetime64_any_dtype(df[col]):
                #         df[col] = pd.to_datetime(df[col], errors="coerce").dt.tz_localize(None)


                timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
                filename = f"batch_{file_counter}_{timestamp}.parquet"
                gcs_path = f"{stage_prefix}/{filename}"
                full_path = f"{bucket_name}/{gcs_path}"

                with fs.open(full_path, 'wb') as f:
                    # Use best dtypes
                    df = df.convert_dtypes()

                    # Replace all known null markers with proper pd.NA
                    df = df.replace(["<NA>", "None", None], pd.NA)

                    # Force numeric columns to numeric dtype → NaN becomes NULL in Parquet
                    for col, sf_type in snowflake_columns:
                        if col in df.columns and sf_type.startswith("NUMBER"):
                            df[col] = pd.to_numeric(df[col], errors="coerce")

                    # Ensure string columns don't contain "<NA>" literal
                    for col in df.select_dtypes(include=["string", "object"]).columns:
                        df[col] = df[col].replace("<NA>", pd.NA).astype("string[pyarrow]")

                    # Build schema + write Parquet
                    df, schema = build_snowflake_schema(df, snowflake_columns)
                    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
                    pq.write_table(table, f)



                print(f"[INFO] Uploaded batch to gs://{full_path}")
                gcs_parquet_urls.append(f"gcs://{full_path}")
                file_counter += 1

            if gcs_parquet_urls:
                upload_parquet_files_and_load(dest_conn, gcs_parquet_urls,
                                               staging_table,
                                               dest_db, dest_schema,
                                               config.cmp_id, config.connection_id)

                print("[INFO] Synchronizing staging to target table...")
                result = sync_staging_to_target(dest_conn, staging_table, table_name, is_updated, is_deleted)
                inserted = result.get("inserted", 0)
                updated = result.get("updated", 0)
                deleted = result.get("deleted", 0)
                columns_data = result.get("columns_data", 0)
                print("[SUCCESS] Migration completed successfully.")
                
                # Add migration completion message and log
                execution_time = time.time() - start_time
                log_migration_completion(
                    execution_time=execution_time,
                    total_rows=inserted + updated + deleted,
                    cmp_id=config.cmp_id,
                    connection_id=config.connection_id,
                    user_id=config.user_id,
                    session=config.migration_session_id,
                    inserts=inserted,
                    updates=updated,
                    deletes=deleted,
                    include_operation_counts=False  # Exclude operation counts to match standardized format
                )
            else:
                print("[WARN] No files uploaded, skipping load & merge.")

            update_table_status_snowflake(config.connection_id, table_name, "completed",
                                          config.job_name, config.migration_session_id)

            return "completed", inserted, updated, deleted, columns_data

    except Exception as e:
        print(f"[ERROR] Migration failed for {full_src_name}: {e}")
        update_table_status_snowflake(config.connection_id, table_name, "failed",
                                      config.job_name, config.migration_session_id)
        return "failure", inserted, updated, deleted, columns_data

def detect_inserts_and_deletes(dest_cur, staging_ids_table, dest_db, dest_schema, table_name, is_deleted_col):
    """
    Detect insert and delete IDs between staging_ids_table and target table.
    Performs delete marking directly in Snowflake for performance.
    Returns: (insert_ids, delete_count)
    """

    # Find IDs to insert (still fetched to Python, since we'll insert later)
    dest_cur.execute(f"""
        SELECT stg._DATASYNCHER_ID
        FROM {staging_ids_table} stg
        LEFT JOIN {dest_db}.{dest_schema}.{table_name} tgt
        ON tgt._DATASYNCHER_ID = stg._DATASYNCHER_ID
        WHERE tgt._DATASYNCHER_ID IS NULL
    """)
    insert_ids = [row[0] for row in dest_cur.fetchall()]

    # Directly mark deletes using UPDATE with LEFT JOIN and capture affected row count
    dest_cur.execute(f"""
        UPDATE {dest_db}.{dest_schema}.{table_name} tgt
        SET {is_deleted_col} = TRUE
        FROM (
            SELECT tgt._DATASYNCHER_ID
            FROM {dest_db}.{dest_schema}.{table_name} tgt
            LEFT JOIN {staging_ids_table} stg
            ON tgt._DATASYNCHER_ID = stg._DATASYNCHER_ID
            WHERE stg._DATASYNCHER_ID IS NULL
              AND tgt.{is_deleted_col} = FALSE
        ) del_ids
        WHERE tgt._DATASYNCHER_ID = del_ids._DATASYNCHER_ID
    """)
    delete_count = dest_cur.rowcount  # Directly get how many rows were updated

    print(f"[DIFF] Insert IDs: {len(insert_ids)}, Delete Count: {delete_count}")
    return insert_ids, delete_count



def migrate_table_worker(config_dict, table_name):
    

    config = SnowflakeMigration(**config_dict)
    source_conn = dest_conn = None
    start_time = time.time()  # For execution time calculation (float)
    start_datetime = datetime.utcnow()  # For database timestamps
    get_last_checked_data=get_last_checked_dates_and_fields(config.connection_id,config.selected_tables)
    try:
        source_conn = snowflake.connector.connect(**_build_conn_params(config.source_config))
        print("after source conn",source_conn)
        dest_conn = snowflake.connector.connect(**_build_conn_params(config.destination_config))
        print("dest con",dest_conn)
        
        table_status,inserted, updated, deleted, columns_data = _migrate_table(
            source_conn, dest_conn, config, table_name, config.chunk_count,start_time,config.safety_interval,last_checked_data=get_last_checked_data.get(table_name)  
        )
        print("updated",updated)
        print("inserted",inserted)
        end_time = time.time()  # For execution time calculation (float)
        end_datetime = datetime.utcnow()  # For database timestamps

        update_table_status_snowflake(
            connection_id=config.connection_id,
            table_name=table_name,
            status=table_status,
            job_name=config.job_name,
            migration_session_id=config.migration_session_id,
            sync_end_time=start_datetime,  # Use datetime for database
        )

        data_transfer_logs(
            cmp_id=config.cmp_id,
            connection_id=config.connection_id,
            usr_id=config.user_id,
            table_name=table_name,
            start_time=start_datetime,  # Use datetime for database
            end_time=end_datetime,  # Use datetime for database
            columns_data=columns_data,
            session=config.migration_session_id,
            insert_success=inserted,
            insert_failure=0,
            upsert_success=updated,
            upsert_failure=0,
        )

        return {
            "table": table_name,
            "status": "completed",
            "inserted": inserted,
            "updated": updated,
            "deleted": deleted,
            
        }

    except Exception as e:
        update_table_status_snowflake(
            connection_id=config.connection_id,
            table_name=table_name,
            status="failed",
            job_name=config.job_name,
            migration_session_id=config.migration_session_id,
        )
        return {
            "table": table_name,
            "status": "failed",
            "error": str(e)
        }

    finally:
        if source_conn:
            source_conn.close()
        if dest_conn:
            dest_conn.close()



def parallel_migration(config: SnowflakeMigration):
    print("🚀 Starting parallel migration")
    start_time = time.time()
    config_dict = config.model_dump() if hasattr(config, "model_dump") else config.dict()
    max_procs = min(len(config.selected_tables), os.cpu_count() or 2)
    
    # Extract drop_and_reload flag from config
    drop_and_reload = config.drop_and_reload
    
    # DEBUG: Log the drop_and_reload flag
    print("=" * 80)
    print(f"🔍 DEBUG SNOWFLAKE TO SNOWFLAKE (PARALLEL): drop_and_reload flag received: {drop_and_reload}")
    print(f"🔍 DEBUG SNOWFLAKE TO SNOWFLAKE (PARALLEL): Type: {type(drop_and_reload)}")
    print(f"🔍 DEBUG SNOWFLAKE TO SNOWFLAKE (PARALLEL): Selected tables: {config.selected_tables}")
    print("=" * 80)
    
    # ═══════════════════════════════════════════════════════════════
    # DROP TABLES IF drop_and_reload FLAG IS TRUE
    # This ensures a clean slate before reloading data from source Snowflake
    # ═══════════════════════════════════════════════════════════════
    if drop_and_reload:
        print("=" * 80)
        print("🗑️  DROP AND RELOAD MODE ENABLED (SNOWFLAKE TO SNOWFLAKE - PARALLEL)")
        print(f"   Dropping {len(config.selected_tables)} table(s) before reload...")
        print("=" * 80)
        
        # Create temporary destination connection for drop operations
        try:
            dest_conn = snowflake.connector.connect(**_build_conn_params(config.destination_config))
            dest_cursor = dest_conn.cursor()
            dest_db = config.destination_config.get('database')
            dest_schema = config.destination_config.get('schema')
            
            # Drop each selected table in destination Snowflake
            for table_name in config.selected_tables:
                try:
                    # Normalize table name to Snowflake conventions (UPPER_SNAKE_CASE)
                    # Convert camelCase/PascalCase to UPPER_SNAKE_CASE
                    normalized_table = re.sub(r'(?<=[a-z])([A-Z])', r'_\1', table_name).upper()
                    
                    # Build fully qualified table name
                    qualified_table = f'"{dest_db}"."{dest_schema}"."{normalized_table}"'
                    
                    print(f"🗑️  Dropping table: {qualified_table}")
                    
                    # Execute DROP TABLE IF EXISTS statement
                    drop_sql = f"DROP TABLE IF EXISTS {qualified_table}"
                    dest_cursor.execute(drop_sql)
                    
                    print(f"✅ Successfully dropped table: {normalized_table}")
                    
                except Exception as drop_error:
                    print(f"⚠️  Warning: Could not drop table {table_name}: {drop_error}")
                    # Continue even if drop fails (table might not exist)
                    # This is expected behavior for first-time table loads
                    pass
            
            dest_cursor.close()
            dest_conn.close()
            print("=" * 80)
            print("✅ DROP PHASE COMPLETED")
            print("   All destination tables have been dropped")
            print("   Ready to reload data from source Snowflake in parallel")
            print("=" * 80)
            
        except Exception as e:
            print(f"❌ Error during drop phase: {e}")
            # Log the error but continue with migration
            insert_tool_log(
                config.cmp_id, 
                config.connection_id, 
                config.user_id, 
                "Drop Phase", 
                "E", 
                f"Error during drop phase: {str(e)}", 
                config.migration_session_id
            )

    with ProcessPoolExecutor(max_workers=max_procs) as executor:
        futures = [
            executor.submit(migrate_table_worker, config_dict, table_name)
            for table_name in config.selected_tables
        ]
        results = [f.result() for f in futures]

    print("✅ Parallel migration completed")
    
    # Add overall migration completion message and log (after all tables are processed)
    execution_time = time.time() - start_time
    total_tables = len(config.selected_tables)
    
    # Count actual successful and failed tables from results
    successful_count = sum(1 for r in results if r.get("status") == "completed")
    failed_count = sum(1 for r in results if r.get("status") == "failed")
    
    # Extract trigger_type from config
    trigger_type = getattr(config, 'trigger_type', 'manual')
    
    log_migration_completion(
        execution_time=execution_time,
        total_rows=0,  # Not tracked in parallel migration
        cmp_id=config.cmp_id,
        connection_id=config.connection_id,
        user_id=config.user_id,
        session=config.migration_session_id,
        total_tables=total_tables,
        successful_tables=successful_count,
        failed_tables=failed_count,
        job_name=config.job_name,
        trigger_type=trigger_type,
        log_id=getattr(config, 'log_id', None)  # ✅ Pass log_id for stable updates
    )
    
    # Create completion message for cleanup
    completion_message = (
        f"Migration completed successfully in {execution_time:.2f} seconds, "
        f"Total tables migrated: {total_tables}."
    )
    
    # Clean up celery jobs to prevent stuck job scenarios
    try:
        # Use connection_name from config (passed from frontend)
        connection_name = config.connection_name if config.connection_name else f"SNOWFLAKE_CONNECTION_{config.connection_id}"
        
        cleanup_result = handle_migration_completion(
            connection_name=connection_name,
            job_name=config.job_name,
            completion_message=completion_message
        )
        
        if cleanup_result['status'] == 'success' and cleanup_result['cleaned_jobs']:
            print(f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} stuck celery jobs for {connection_name}")
        elif cleanup_result['status'] == 'error':
            print(f"⚠️ Cleanup warning for {connection_name}: {cleanup_result['message']}")
            
    except Exception as cleanup_error:
        # Don't fail the migration if cleanup fails - just log the warning
        print(f"⚠️ Failed to cleanup celery jobs: {cleanup_error}")
        import traceback
        traceback.print_exc()
    
    # Extract trigger_type from config to include in response
    trigger_type = getattr(config, 'trigger_type', 'manual')
    
    return {
        "status": "completed",
        "tables_migrated": results,
        "trigger_type": trigger_type  # Include trigger type in response
    }



def sequential_migration(config: SnowflakeMigration):
    
    print("🚶 Starting sequential migration")
    start_time = time.time()
    source_conn = snowflake.connector.connect(**_build_conn_params(config.source_config))

    dest_conn = snowflake.connector.connect(**_build_conn_params(config.destination_config))
    get_last_checked_data=get_last_checked_dates_and_fields(config.connection_id,config.selected_tables)
    results = []
    
    # Extract drop_and_reload flag from config
    drop_and_reload = config.drop_and_reload
    
    # DEBUG: Log the drop_and_reload flag
    print("=" * 80)
    print(f"🔍 DEBUG SNOWFLAKE TO SNOWFLAKE: drop_and_reload flag received: {drop_and_reload}")
    print(f"🔍 DEBUG SNOWFLAKE TO SNOWFLAKE: Type: {type(drop_and_reload)}")
    print(f"🔍 DEBUG SNOWFLAKE TO SNOWFLAKE: Selected tables: {config.selected_tables}")
    print("=" * 80)
    
    # ═══════════════════════════════════════════════════════════════
    # DROP TABLES IF drop_and_reload FLAG IS TRUE
    # This ensures a clean slate before reloading data from source Snowflake
    # ═══════════════════════════════════════════════════════════════
    if drop_and_reload:
        print("=" * 80)
        print("🗑️  DROP AND RELOAD MODE ENABLED (SNOWFLAKE TO SNOWFLAKE)")
        print(f"   Dropping {len(config.selected_tables)} table(s) before reload...")
        print("=" * 80)
        
        # Drop tables using raw Snowflake cursor
        try:
            dest_cursor = dest_conn.cursor()
            dest_db = config.destination_config.get('database')
            dest_schema = config.destination_config.get('schema')
            
            # Drop each selected table in destination Snowflake
            for table_name in config.selected_tables:
                try:
                    # Normalize table name to Snowflake conventions (UPPER_SNAKE_CASE)
                    # Convert camelCase/PascalCase to UPPER_SNAKE_CASE
                    normalized_table = re.sub(r'(?<=[a-z])([A-Z])', r'_\1', table_name).upper()
                    
                    # Build fully qualified table name
                    qualified_table = f'"{dest_db}"."{dest_schema}"."{normalized_table}"'
                    
                    print(f"🗑️  Dropping table: {qualified_table}")
                    
                    # Execute DROP TABLE IF EXISTS statement
                    drop_sql = f"DROP TABLE IF EXISTS {qualified_table}"
                    dest_cursor.execute(drop_sql)
                    
                    print(f"✅ Successfully dropped table: {normalized_table}")
                    
                except Exception as drop_error:
                    print(f"⚠️  Warning: Could not drop table {table_name}: {drop_error}")
                    # Continue even if drop fails (table might not exist)
                    # This is expected behavior for first-time table loads
                    pass
            
            dest_cursor.close()
            print("=" * 80)
            print("✅ DROP PHASE COMPLETED")
            print("   All destination tables have been dropped")
            print("   Ready to reload data from source Snowflake")
            print("=" * 80)
            
        except Exception as e:
            print(f"❌ Error during drop phase: {e}")
            # Log the error but continue with migration
            insert_tool_log(
                config.cmp_id, 
                config.connection_id, 
                config.user_id, 
                "Drop Phase", 
                "E", 
                f"Error during drop phase: {str(e)}", 
                config.migration_session_id
            )

    try:
        for table_name in config.selected_tables:
            start_time = time.time()  # For execution time calculation (float)
            start_datetime = datetime.utcnow()  # For database timestamps
            try:
                table_status,inserted, updated, deleted, columns_data = _migrate_table(
                    source_conn, dest_conn, config, table_name, config.chunk_count,start_time,config.safety_interval,last_checked_data=get_last_checked_data.get(table_name)  
                )
                end_time = time.time()  # For execution time calculation (float)
                end_datetime = datetime.utcnow()  # For database timestamps

                update_table_status_snowflake(
                    connection_id=config.connection_id,
                    table_name=table_name,
                    status=table_status,
                    job_name=config.job_name,
                    migration_session_id=config.migration_session_id,
                    sync_end_time=end_datetime,  # Use datetime for database
                )

                data_transfer_logs(
                    cmp_id=config.cmp_id,
                    connection_id=config.connection_id,
                    usr_id=config.user_id,
                    table_name=table_name,
                    start_time=start_datetime,  # Use datetime for database
                    end_time=end_datetime,  # Use datetime for database
                    columns_data=columns_data,
                    session=config.migration_session_id,
                    insert_success=inserted,
                    insert_failure=0,
                    upsert_success=updated,
                    upsert_failure=0,
                )

                results.append({
                    "table": table_name,
                    "status": "completed",
                    "inserted": inserted,
                    "updated": updated,
                    "deleted": deleted,
                    
                })

            except Exception as e:
                
                results.append({
                    "table": table_name,
                    "status": "failed",
                    "error": str(e)
                })

    finally:
        source_conn.close()
        dest_conn.close()

    print("✅ Sequential migration completed")
    
    # Add overall migration completion message and log (after all tables are processed)
    execution_time = time.time() - start_time
    total_tables = len(config.selected_tables)
    
    # Count actual successful and failed tables from results
    successful_count = sum(1 for r in results if r.get("status") == "completed")
    failed_count = sum(1 for r in results if r.get("status") == "failed")
    
    # Extract trigger_type from config
    trigger_type = getattr(config, 'trigger_type', 'manual')
    
    log_migration_completion(
            execution_time=execution_time,
            total_rows=0,  # Not tracked in sequential migration
            cmp_id=config.cmp_id,
            connection_id=config.connection_id,
            user_id=config.user_id,
            session=config.migration_session_id,
            total_tables=total_tables,
            successful_tables=successful_count,
            failed_tables=failed_count,
            job_name=config.job_name,
            trigger_type=trigger_type,
            log_id=getattr(config, 'log_id', None)  # ✅ Pass log_id for stable updates
        )
        
    # Create completion message for cleanup
    completion_message = (
        f"Migration completed successfully in {execution_time:.2f} seconds, "
        f"Total tables migrated: {total_tables}."
    )
        
        
    # Clean up celery jobs to prevent stuck job scenarios
    try:
        # Use connection_name from config (passed from frontend)
        connection_name = config.connection_name if config.connection_name else f"SNOWFLAKE_CONNECTION_{config.connection_id}"
        
        cleanup_result = handle_migration_completion(
            connection_name=connection_name,
            job_name=config.job_name,
            completion_message=completion_message
        )
        
        if cleanup_result['status'] == 'success' and cleanup_result['cleaned_jobs']:
            print(f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} stuck celery jobs for {connection_name}")
        elif cleanup_result['status'] == 'error':
            print(f"⚠️ Cleanup warning for {connection_name}: {cleanup_result['message']}")
            
    except Exception as cleanup_error:
        # Don't fail the migration if cleanup fails - just log the warning
        print(f"⚠️ Failed to cleanup celery jobs: {cleanup_error}")
        import traceback
        traceback.print_exc()
    
    # Extract trigger_type from config to include in response
    trigger_type = getattr(config, 'trigger_type', 'manual')
    
    return {
        "status": "completed",
        "tables_migrated": results,
        "trigger_type": trigger_type  # Include trigger type in response
    }

def migrate(config: SnowflakeMigration):
    print("📦 Migration started")
    if config.execution_order == "parallel":
        result = parallel_migration(config)
    else:
        result = sequential_migration(config)
    print("🏁 Migration finished")
    return result
