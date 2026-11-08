from datetime import datetime,timezone
from typing import Any, Dict, List
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import Engine
from google.cloud import storage
import re, os , time, requests, xmltodict, grpc
from utils.snowflake_utils import normalize_name, force_https_url , _resolve_edmx_entity_type, _build_metadata_columns,_sf_ident,is_valid_snowflake_destination_name
from typing import Optional
from utils.snowflake_mapping_resolver import resolve_effective_mapping
from utils.transform.snowflake_executer import execute_transform_chunk
from google.protobuf.json_format import MessageToDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import requests, json
from urllib.parse import quote
import io
import json
import time, sys
from pathlib import Path
from urllib.parse import quote
import pandas as pd
from google.cloud import storage
from google.oauth2.credentials import Credentials
from odata_functions import TEMP_DIR
import shutil
import service_pb2
import service_pb2_grpc
from google.protobuf.struct_pb2 import Struct
# from agent import update_migration_status

BUCKET_NAME: str = "meta_data_bucket_ds_development"
GCS_BUCKET_URL: str = f"gcs://{BUCKET_NAME}/" if BUCKET_NAME else ""
DEMO_STORAGE: str = "SNOWFLAKE_STORAGE_INTEGRATION"

# Use the same bucket configured for downscoping on the server.
GCS_BUCKET_NAME =BUCKET_NAME

# Agent config.json, containing server_host and operational mTLS credentials.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.json"
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

agent_id = config["agent_id"]

def update_table_status(status_data: dict):
    """
    Sends table status details to the main Django application
    through tableStatusService unary RPC.
    """
    try:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            private_key=config["client_private_key"].encode("utf-8"),
            certificate_chain=config["client_cert"].encode("utf-8"),
        )

        server_addr = f'{config["server_host"]}:50053'

        # Include agent/customer identification in the payload.
        status_data.setdefault("cust_id", agent_id)

        # Convert dictionary into protobuf Struct.
        status_struct = Struct()
        status_struct.update(status_data)

        with grpc.secure_channel(server_addr, credentials) as channel:

            stub = service_pb2_grpc.TableStatusServiceStub(channel)

            response = stub.TableStatusUpdate(
                service_pb2.StatusUpdateRequest(
                    statusdetails=status_struct
                ),
                timeout=30
            )

        print("[STATUS] table status updated successfully")

        return True

    except grpc.RpcError as e:
        print(
            f"[STATUS] table status RPC failed: "
            f"{e.code()} - {e.details()}"
        )
        return False

    except Exception as e:
        print(f"[STATUS] Unexpected error: {repr(e)}")
        return False


def update_table_etl_status(status_data: dict):
    """
    Sends table_etl status details to the main Django application
    through TableETLStatusService unary RPC.
    """
    try:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            private_key=config["client_private_key"].encode("utf-8"),
            certificate_chain=config["client_cert"].encode("utf-8"),
        )

        server_addr = f'{config["server_host"]}:50053'

        # Include agent/customer identification in the payload.
        status_data.setdefault("cust_id", agent_id)

        # Convert dictionary into protobuf Struct.
        status_struct = Struct()
        status_struct.update(status_data)

        with grpc.secure_channel(server_addr, credentials) as channel:

            stub = service_pb2_grpc.TableETLStatusServiceStub(channel)

            response = stub.TableETLStatusUpdate(
                service_pb2.StatusUpdateRequest(
                    statusdetails=status_struct
                ),
                timeout=30
            )

        print("[STATUS] table_etl status updated successfully")

        return True

    except grpc.RpcError as e:
        print(
            f"[STATUS] table_etl status RPC failed: "
            f"{e.code()} - {e.details()}"
        )
        return False

    except Exception as e:
        print(f"[STATUS] Unexpected error: {repr(e)}")
        return False


def data_transfer_logs(status_data: dict):
    """
    Sends data_transfer_logs status details to the main Django application
    through DataTransferLogsService unary RPC.
    """
    try:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            private_key=config["client_private_key"].encode("utf-8"),
            certificate_chain=config["client_cert"].encode("utf-8"),
        )

        server_addr = f'{config["server_host"]}:50053'

        # Include agent/customer identification in the payload.
        status_data.setdefault("cust_id", agent_id)

        # Convert dictionary into protobuf Struct.
        status_struct = Struct()
        status_struct.update(status_data)

        with grpc.secure_channel(server_addr, credentials) as channel:

            stub = service_pb2_grpc.DataTransferLogsServiceStub(channel)

            response = stub.DataTransferLogsUpdate(
                service_pb2.StatusUpdateRequest(
                    statusdetails=status_struct
                ),
                timeout=30
            )

        print("[STATUS] DataTransferLogs status updated successfully")

        return True

    except grpc.RpcError as e:
        print(
            f"[STATUS] DataTransferLogs status RPC failed: "
            f"{e.code()} - {e.details()}"
        )
        return False

    except Exception as e:
        print(f"[STATUS] Unexpected error: {repr(e)}")
        return False



def update_migration_status_log(status_data: dict):
    """
    Sends Migration Status status details to the main Django application
    through MigrationStatusLogService unary RPC.
    """
    try:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            private_key=config["client_private_key"].encode("utf-8"),
            certificate_chain=config["client_cert"].encode("utf-8"),
        )

        server_addr = f'{config["server_host"]}:50053'

        # Include agent/customer identification in the payload.
        status_data.setdefault("cust_id", agent_id)

        # Convert dictionary into protobuf Struct.
        status_struct = Struct()
        status_struct.update(status_data)

        with grpc.secure_channel(server_addr, credentials) as channel:

            stub = service_pb2_grpc.MigrationStatusLogServiceStub(channel)

            response = stub.MigrationStatusLogUpdate(
                service_pb2.StatusUpdateRequest(
                    statusdetails=status_struct
                ),
                timeout=30
            )

        print("[STATUS] Migration Status status updated successfully")

        return True

    except grpc.RpcError as e:
        print(
            f"[STATUS] Migration Status status RPC failed: "
            f"{e.code()} - {e.details()}"
        )
        return False

    except Exception as e:
        print(f"[STATUS] Unexpected error: {repr(e)}")
        return False

def record_table_failure(status_data: dict):
    """
    Sends record_table_failure details to the main Django application
    through RecordTableFailureService unary RPC.
    """
    try:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            private_key=config["client_private_key"].encode("utf-8"),
            certificate_chain=config["client_cert"].encode("utf-8"),
        )

        server_addr = f'{config["server_host"]}:50053'

        # Include agent/customer identification in the payload.
        status_data.setdefault("cust_id", agent_id)

        # Convert dictionary into protobuf Struct.
        status_struct = Struct()
        status_struct.update(status_data)

        with grpc.secure_channel(server_addr, credentials) as channel:

            stub = service_pb2_grpc.RecordTableFailureServiceStub(channel)

            response = stub.RecordTableFailureUpdate(
                service_pb2.StatusUpdateRequest(
                    statusdetails=status_struct
                ),
                timeout=30
            )

        print("[STATUS] RecordTableFailure Status status updated successfully")

        return True

    except grpc.RpcError as e:
        print(
            f"[STATUS] RecordTableFailure status RPC failed: "
            f"{e.code()} - {e.details()}"
        )
        return False

    except Exception as e:
        print(f"[STATUS] Unexpected error: {repr(e)}")
        return False


def upload_parquet_to_gcs(
    dataframe: pd.DataFrame,
    blob_name: str,
) -> str:
    """
    Serialize one DataFrame to Parquet in memory and upload it to GCS.
    No local Parquet file is created.

    blob_name is the complete desired GCS object name.
    Returns the GCS URI after the upload succeeds.
    """
    from agent import get_gcp_access_token
    token = get_gcp_access_token()

    # Convert the page directly to an in-memory Parquet buffer.
    buffer = io.BytesIO()
    dataframe.to_parquet(buffer, index=False, engine="pyarrow")
    buffer.seek(0)

    credentials = Credentials(token=token)
    client = storage.Client(credentials=credentials)
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(blob_name)

    try:
        blob.upload_from_file(
            buffer,
            rewind=True,
            content_type="application/vnd.apache.parquet",
        )
    finally:
        buffer.close()

    # Reaching this point means the upload succeeded.
    return f"gcs://{GCS_BUCKET_NAME}/{blob_name}"

def _delete_gcs_blob(uploaded_files: List) -> None:

    from agent import get_gcp_access_token
    try:

        print("gcp delete process entered ")
        token = get_gcp_access_token()

        for uri in uploaded_files:
            blob_name = uri.split(f"gcs://{BUCKET_NAME}/", 1)[-1]
            credentials = Credentials(token=token)

            client = storage.Client(credentials=credentials)
            blob = client.bucket(BUCKET_NAME).blob(blob_name)

            if blob.exists():
                blob.delete()
                print("[GCS] Deleted '%s'", blob_name)

    except Exception as e:
        print("[GCS] Delete failed '%s': %s", blob_name, e)

def normalize_pem_key(key_str: str) -> str:
    """
    Normalize PEM key string by fixing newlines and ensuring proper formatting.

    Behavioral baseline: migration-function ``snowflake_service.normalize_pem_key``.
    """
    if not key_str:
        raise ValueError("Key string cannot be empty")

    key_str = key_str.strip()

    if "-----BEGIN" not in key_str.upper() or "-----END" not in key_str.upper():
        pass  # preserve legacy warning-only behavior in callers

    key_str = key_str.replace("\\n", "\n")

    if "-----BEGIN" in key_str:
        begin_match = re.search(r"-----BEGIN [^-]+-----", key_str)
        if begin_match:
            begin_end_pos = begin_match.end()
            if begin_end_pos < len(key_str) and key_str[begin_end_pos] != "\n":
                key_str = re.sub(r"(-----BEGIN [^-]+-----)\s*", r"\1\n", key_str, count=1)

    if "-----END" in key_str:
        end_match = re.search(r"-----END [^-]+-----", key_str)
        if end_match:
            end_start_pos = end_match.start()
            if end_start_pos > 0 and key_str[end_start_pos - 1] != "\n":
                key_str = re.sub(r"\s*(-----END [^-]+-----)", r"\n\1", key_str, count=1)

    return key_str

def ensure_bucket_iam_for_integration(gcs: storage.Client, bucket_name: str, member_sa: str):
    """Grant roles/storage.objectAdmin to the Snowflake integration's service account on the bucket."""
    member = f"serviceAccount:{member_sa}"
    role = "roles/storage.objectAdmin"
    bucket = gcs.bucket(bucket_name)
    policy = bucket.get_iam_policy(requested_policy_version=3)
    for b in policy.bindings:
        if b["role"] == role:
            mems = set(b.get("members", []))
            if member in mems:
                print(f"'{member}' already has '{role}' on '{bucket_name}'.")
                return
            mems.add(member)
            b["members"] = sorted(list(mems))
            bucket.set_iam_policy(policy)
            print(f" IAM updated: added '{member}' to existing '{role}'.")
            return
    policy.bindings.append({"role": role, "members": [member]})
    bucket.set_iam_policy(policy)
    print(f" IAM updated: bound '{member}' → '{role}' on '{bucket_name}'.")


def _ensure_gcs_integration(sf_engine: Engine, connection_id: int) -> None:
    """Ensure Snowflake GCS storage integration exists and bucket IAM is configured."""

    integration_existed = False
    try:
        with sf_engine.connect() as connection:
            result = connection.execute(
                text(f"SHOW STORAGE INTEGRATIONS LIKE '{DEMO_STORAGE}'")
            ).fetchall()
            if result:
                integration_existed = True
                print("[SAP -> SF] Storage integration '%s' already exists", DEMO_STORAGE)
            else:
                connection.execute(text(f"""
                    CREATE STORAGE INTEGRATION {DEMO_STORAGE}
                    TYPE = EXTERNAL_STAGE
                    STORAGE_PROVIDER = 'GCS'
                    ENABLED = TRUE
                    STORAGE_ALLOWED_LOCATIONS = ('{GCS_BUCKET_URL}');
                """))
                connection.commit()
                print("[SAP -> SF] Created storage integration '%s'", DEMO_STORAGE)
    except Exception as exc:
        print(
            "[SAP -> SF] Error creating storage integration for conn=%s: %s",
            connection_id,
            exc,
        )
        raise

    if integration_existed:
        return

    try:
        with sf_engine.connect() as connection:
            rows = connection.execute(text(f"DESC INTEGRATION {DEMO_STORAGE}")).fetchall()
        service_account = next(
            (row[2] for row in rows if row[0] == "STORAGE_GCP_SERVICE_ACCOUNT"),
            None,
        )
        if service_account:
            ensure_bucket_iam_for_integration(storage.Client(), BUCKET_NAME, service_account)
    except Exception as exc:
        print(
            "[SAP -> SF] Error configuring GCS IAM for conn=%s: %s",
            connection_id,
            exc,
        )
        raise


def _get_snowflake_engine(destination_config: Dict[str, Any]) -> Engine:
    """Build a SQLAlchemy engine for Snowflake. Supports password and PEM key auth."""
    account   = destination_config["account"]
    user      = destination_config["username"]
    password  = destination_config["password"] if "password" in destination_config else ""
    warehouse = destination_config["warehouse"]
    database  = destination_config["database"]
    schema_   = destination_config["schema"]
    role      = destination_config["role"]  
    pk_str    = destination_config["private_key"] if "private_key" in destination_config else ""


    engine_url = (
        f"snowflake://{account}/{database}/{schema_}"
        f"?warehouse={warehouse}&role={role}"
    )


    if pk_str:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        pem_str = normalize_pem_key(pk_str)
        passphrase_raw = (
            destination_config.get("passphrase")
            or destination_config.get("private_key_passphrase")
            or ""
        )
        passphrase_bytes = (
            str(passphrase_raw).encode("utf-8")
            if str(passphrase_raw).strip()
            else None
        )
        pk_obj = serialization.load_pem_private_key(
            pem_str.encode(),
            password=passphrase_bytes,
            backend=default_backend(),
        )
        pk_bytes   = pk_obj.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        return create_engine(
            engine_url,
            connect_args={
                "user": user,
                "private_key": pk_bytes,
            },
            future=True
        )

    # ------------------------------------------------------------------
    # Password authentication
    # ------------------------------------------------------------------
    return create_engine(
        engine_url,
        connect_args={
            "user": user,
            "password": password,
        },
        future=True
    )

# ---------------------------------------------------------------------------
# EDM to Snowflake type map
# ---------------------------------------------------------------------------

EDM_TO_SNOWFLAKE: Dict[str, str] = {
    "Edm.String":         "VARCHAR(255)",
    "Edm.Boolean":        "BOOLEAN",
    "Edm.Byte":           "NUMBER(3,0)",
    "Edm.SByte":          "NUMBER(3,0)",
    "Edm.Int16":          "NUMBER(5,0)",
    "Edm.Int32":          "NUMBER(10,0)",
    "Edm.Int64":          "NUMBER(19,0)",
    "Edm.Single":         "FLOAT",
    "Edm.Double":         "FLOAT",
    "Edm.Decimal":        "NUMBER(38,10)",
    "Edm.DateTime":       "TIMESTAMP_NTZ",
    "Edm.DateTimeOffset": "TIMESTAMP_TZ",
    "Edm.Time":           "TIME",
    "Edm.Guid":           "VARCHAR(36)",
    "Edm.Binary":         "BINARY",
}


# dataType fallback for internal columns added by source.py (they have no edmType).
# Keys match the "dataType" field in the metadata dict, not EDM type names.
DATATYPE_TO_SNOWFLAKE: Dict[str, str] = {
    "BOOLEAN": "BOOLEAN",
    "NVARCHAR": "VARCHAR(255)",
    "DECIMAL": "NUMBER(38,10)",  # precision/scale handled separately
    "TIMESTAMP": "TIMESTAMP_TZ",
    "INSTANT": "TIMESTAMP_TZ",
    "STRING": "VARCHAR(255)",
}

def _edm_col_to_sf(
    edm_type: str,
    max_length: Optional[int] = None,
    precision: Optional[int] = None,
    scale: Optional[int] = None,
    data_type: Optional[str] = None,
) -> str:
    """Convert an SAP EDM type to a Snowflake column DDL type string.

    For ``Edm.Decimal`` the SAP metadata ``@Precision`` / ``@Scale`` attributes
    are used when available so that quantity / amount columns get the correct
    number of decimal places (e.g. NUMBER(13,3)) instead of the hardcoded
    fallback NUMBER(38,10) which causes excessive trailing zeros in the UI.

    ``data_type`` is the secondary-source fallback for internal columns added by
    ``source.py`` (e.g. ``_SPECT_DELETED``, ``__timestamp``) that carry a plain
    ``dataType`` field but no ``edmType``.  Without this fallback they would
    resolve to ``Edm.String`` -> ``VARCHAR(255)`` even when they are BOOLEAN.
    """
    if edm_type == "Edm.Decimal":
        p = precision if precision and precision > 0 else 38
        p = min(p, 38)  # Snowflake max precision is 38; SAP may report > 38 (e.g. 45)
        s = scale if scale is not None and scale >= 0 else 10
        s = min(s, p)  # scale must not exceed precision
        return f"NUMBER({p},{s})"

    sf = EDM_TO_SNOWFLAKE.get(edm_type)
    if sf is None and data_type:
        # No EDM type resolved — fall back to the internal dataType mapping.
        sf = DATATYPE_TO_SNOWFLAKE.get((data_type or "").upper())
    if sf is None:
        sf = "VARCHAR(255)"  # last-resort default

    if sf == "VARCHAR(255)" and max_length and max_length > 255:
        sf = f"VARCHAR({max_length})"
    return sf


def _column_sf_type(column: Dict[str, Any]) -> str:
    explicit = column.get("sfType")
    if explicit:
        return str(explicit)
    return _edm_col_to_sf(
        column.get("edmType", ""),
        column.get("maxLength") or column.get("length"),
        column.get("precision"),
        column.get("scale"),
        column.get("dataType"),
    )

def ensure_snowflake_table_exists(
    engine: Engine,
    schema: str,
    table_name: str,
    schema_metadata: Optional[Dict[str, Any]],
    primary_keys: List[str],
    is_updated: str = "_FIVETRAN_SYNCED",
    is_deleted: str = "_FIVETRAN_DELETED",
) -> bool:
    """Create or verify Snowflake target table from SAP EDM metadata.

    Returns True if table already existed, False if newly created.
    """
    norm = _sf_ident(table_name)
    full  = f'"{schema}"."{norm}"'
    cols  = (schema_metadata or {}).get("columns", [])

    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        exists = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=:s AND table_name=:t"),
            {"s": schema.upper(), "t": norm.upper()},
        ).scalar() > 0

        if not exists:
            defs = [
                f'"{c.get("name")}" {_edm_col_to_sf(c.get("edmType", "Edm.String"), c.get("maxLength") or c.get("length"))}'
                for c in cols
            ]
            if is_updated not in [c.get("name") for c in cols]:
                defs.append(f'"{is_updated}" TIMESTAMP_NTZ')
            if is_deleted not in [c.get("name") for c in cols]:
                defs.append(f'"{is_deleted}" BOOLEAN DEFAULT FALSE')
            conn.execute(text(f'CREATE TABLE IF NOT EXISTS {full} ({", ".join(defs)})'))
            # logger.info("[DDL] Created %s", full)
            return False

        existing = {r[0] for r in conn.execute(
            text("SELECT UPPER(column_name) FROM information_schema.columns WHERE table_schema=:s AND table_name=:t"),
            {"s": schema.upper(), "t": norm.upper()},
        ).fetchall()}
        for c in cols:
            if c.get("name", "").upper() not in existing:
                conn.execute(text(
                    f'ALTER TABLE {full} ADD COLUMN IF NOT EXISTS "{c["name"]}" '
                    f'{_column_sf_type(c.get("edmType", "Edm.String"), c.get("maxLength") or c.get("length"))}'
                ))
        for ac, at in [(is_updated, "TIMESTAMP_NTZ"), (is_deleted, "BOOLEAN DEFAULT FALSE")]:
            if ac.upper() not in existing:
                conn.execute(text(f'ALTER TABLE {full} ADD COLUMN IF NOT EXISTS "{ac}" {at}'))
        return True


def _get_snowflake_column_order(
    engine: Engine, schema: str, table_name: str
) -> List[str]:
    """Return Snowflake table columns in ordinal order."""
    norm = _sf_ident(table_name)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name = :table
                ORDER BY ordinal_position
                """
            ),
            {"schema": schema.upper(), "table": norm.upper()},
        ).fetchall()
    return [str(r[0]) for r in rows]


def _align_df_to_snowflake_columns(df: pd.DataFrame, column_order: List[str]) -> pd.DataFrame:
    """Match DataFrame columns to Snowflake ordinal order; NULL-fill missing columns."""
    if not column_order:
        raise ValueError("Cannot align DataFrame without Snowflake column order")

    aligned = df.copy()
    if aligned.columns.duplicated().any():
        dupes = list(aligned.columns[aligned.columns.duplicated()])
        # logger.warning("[SAP -> SF] Dropping duplicate DataFrame columns: %s", dupes[:10])
        aligned = aligned.loc[:, ~aligned.columns.duplicated()]

    extra_cols = [c for c in aligned.columns if c not in column_order]
    if extra_cols:
        # logger.info("[SAP -> SF] Dropping %d DataFrame column(s) not in Snowflake table", len(extra_cols))
        aligned = aligned.drop(columns=extra_cols, errors="ignore")

    missing_cols = [c for c in column_order if c not in aligned.columns]
    # if missing_cols:
        # logger.info("[SAP -> SF] NULL-filling %d Snowflake column(s) missing from extract", len(missing_cols))

    aligned = aligned.reindex(columns=column_order, fill_value=None)
    if len(aligned.columns) != len(column_order):
        raise ValueError(
            f"Column alignment failed: df_cols={len(aligned.columns)} "
            f"snowflake_cols={len(column_order)}"
        )
    return aligned


def _ensure_staging_table(
    engine: Engine, schema: str, target: str, staging: str
) -> None:
    """Recreate staging table so it always mirrors the current target schema."""
    norm_target = _sf_ident(target)
    norm_staging = _sf_ident(staging)
    with engine.begin() as conn:
        conn.execute(
            text(
                f'CREATE OR REPLACE TABLE "{schema}"."{norm_staging}" '
                f'LIKE "{schema}"."{norm_target}"'
            )
        )

def fetch_metadata(
    url: str,
    username: str,
    password: str,
    entity_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch and parse the SAP OData ``$metadata`` endpoint into a column schema dict."""

    url = force_https_url(url)

    session = requests.Session()
    session.verify = False

    response = session.get(
        url,
        auth=(username, password),
        timeout=120,
    )

    if response.status_code != 200:

        # status update -----------------------------------------------------------
        # logger.error(
        #     "Metadata request failed with status: %d",
        #     response.status_code,
        # )
        return None

    metadata_dict = xmltodict.parse(response.text)

    main_entity = _resolve_edmx_entity_type(
        metadata_dict,
        entity_name=entity_name,
    )

    if not main_entity:

        # status update ---------------------------------------------------------------------
        # logger.warning(
        #     "No suitable entity type found in metadata for entity_name=%s",
        #     entity_name,
        # )
        return None

    columns = _build_metadata_columns(main_entity)

    if not columns:
        return None

    # status update ---------------------------------------------------------------------

    # logger.info(
    #     "fetch_metadata: resolved entity_type='%s' entity_set=%s columns=%d",
    #     main_entity.get("@Name"),
    #     entity_name or "(default)",
    #     len(columns),
    # )

    return {
        "version": metadata_dict["edmx:Edmx"].get("@Version"),
        "format": "PARQUET",
        "properties": [
            {
                "namespace": "datasynchor.com",
                "name": "loadType",
                "value": "REPLICATE",
            },
            {
                "namespace": "datasynchor.com",
                "name": "replicationTaskID",
                "value": "3a5ca2a6-a63e-4a28-b3cb-6e0fea66f93b",
            },
            {
                "namespace": "datasynchor.com",
                "name": "groupDeltaFilesBy",
                "value": "NONE",
            },
        ],
        "columns": columns,
    }



# Compiled once at module load for maximum re-use performance.
# SAP OData services surface dates in two common JSON serialisation formats:
#   /Date(1717286400000)/    -  OData V2 JSON millisecond epoch
#   2026-06-02T00:00:00      -  ISO 8601 (optional fractional seconds)
ODATE_RE = re.compile(r"^/Date\((\d+)\)/$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")
# SAP Edm.Time JSON values use ISO-8601 duration, e.g. PT16H10M56S
ODATA_TIME_DURATION_RE = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$",
    re.IGNORECASE,
)
def find_odata_date_columns(df: pd.DataFrame) -> Dict[str, str]:
    """Detect columns containing OData or ISO datetime strings, vectorised.

    Checks the **full** column using pandas vectorised string matching so that
    no row-by-row Python loop is needed  -  fast even at millions of rows.

    Args:
        df: Source DataFrame to inspect.

    Returns:
        Dict mapping column name -> ``"odata"`` or ``"iso"``.  Only columns
        where at least one value matches a date pattern are included.
        A column is categorised by whichever pattern matches first (OData takes
        priority over ISO).
    """
    date_cols: Dict[str, str] = {}
    for col in df.columns:
        if df[col].dtype != object:
            continue
        col_str = df[col].astype(str)
        if col_str.str.match(ODATE_RE, na=False).any():
            date_cols[col] = "odata"
            continue  # OData and ISO are mutually exclusive per column
        if col_str.str.match(ISO_DATE_RE, na=False).any():
            date_cols[col] = "iso"
    return date_cols

def convert_odata_columns(df: pd.DataFrame, date_cols: Dict[str, str]) -> pd.DataFrame:
    """Convert detected date columns to ``yyyymmdd`` strings in-place.

    Handles two formats:
    - ``"odata"``: ``/Date(milliseconds)/`` -> UTC datetime -> ``yyyymmdd``
    - ``"iso"``:   ``YYYY-MM-DDTHH:MM:SS[.fff]`` -> ``yyyymmdd`` (server-local)

    Original ``NaN`` / ``None`` cells are preserved so no fabricated data is
    introduced.

    Args:
        df: Source DataFrame (modified in-place for performance).
        date_cols: Dict returned by :func:`find_odata_date_columns`.

    Returns:
        The same DataFrame with date columns converted to ``yyyymmdd`` strings.
    """
    for col, fmt in date_cols.items():
        s = df[col]
        if fmt == "odata":
            millis = s.str.extract(r"/Date\((\d+)\)/", expand=False)
            millis_numeric = pd.to_numeric(millis, errors="coerce")
            # utc=True required: OData epoch is always UTC
            dt = pd.to_datetime(millis_numeric, unit="ms", utc=True, errors="coerce")
        else:  # iso
            dt = pd.to_datetime(s, format="%Y-%m-%dT%H:%M:%S", errors="coerce")
            # Fallback for values with fractional seconds not matched by strict format
            if dt.isna().any():
                fallback = pd.to_datetime(s, errors="coerce")
                dt = dt.fillna(fallback)
        df[col] = dt.dt.strftime("%Y%m%d")
        # Re-apply original NaN/None so non-date cells are not replaced with NaT strings
        df[col] = df[col].where(s.notna(), s)
    return df

def parse_odata_duration_to_time(value: Any) -> Any:
    """Convert SAP OData Edm.Time duration strings to HH:MM:SS[.ffffff].

    SAP Gateway serialises Edm.Time as ISO-8601 durations (``PT16H10M56S``).
    Snowflake TIME columns expect clock-time strings instead.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return value

    match = ODATA_TIME_DURATION_RE.fullmatch(text)
    if not match:
        return value

    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    sec_whole = int(seconds)
    micro = int(round((seconds - sec_whole) * 1_000_000))
    if micro:
        return f"{hours:02d}:{minutes:02d}:{sec_whole:02d}.{micro:06d}"
    return f"{hours:02d}:{minutes:02d}:{sec_whole:02d}"

def convert_odata_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise OData Edm.Time duration columns to Snowflake-compatible time strings."""
    for col in df.columns:
        if df[col].dtype != object:
            continue
        col_str = df[col].astype(str)
        if not col_str.str.match(r"^PT\d", na=False).any():
            continue
        df[col] = df[col].map(parse_odata_duration_to_time)
    return df



def _snowflake_table_exists(engine: Engine, schema: str, table_name: str) -> bool:
    ident = _sf_ident(table_name)
    try:
        return inspect(engine).has_table(ident, schema=schema)
    except Exception:
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=:s AND table_name=:t"
                ),
                {"s": schema.upper(), "t": ident.upper()},
            ).scalar()
            return bool(count)


def _rename_snowflake_table(
    engine: Engine, schema: str, old_name: str, new_name: str
) -> None:
    old_ident = _sf_ident(old_name)
    new_ident = _sf_ident(new_name)
    if old_ident == new_ident:
        return
    with engine.begin() as conn:
        conn.execute(
            text(f'ALTER TABLE "{schema}"."{old_ident}" RENAME TO "{new_ident}"')
        )

def _abap_type_to_sf(
    datatype: Optional[str],
    length: Optional[int] = None,
    decimals: Optional[int] = None,
) -> str:
    """Map an ABAP / JSON field type onto a Snowflake DDL type."""
    kind = (datatype or "").upper().strip()
    size = int(length or 0)
    scale = int(decimals or 0)
    if kind in {"CURR", "QUAN", "DEC", "FLTP", "DF16", "DF34"}:
        precision = min(size or 38, 38)
        scale = min(max(scale, 0), precision)
        return f"NUMBER({precision},{scale})"
    if kind in {"INT", "INT1", "INT2", "INT4", "INT8", "INTEGER"}:
        return "NUMBER(38,0)"
    if kind in {"DATE"}:
        return "DATE"
    if kind in {"TIMESTAMP", "DATETIME"}:
        return "TIMESTAMP_NTZ"
    if kind == "DATS":
        return "VARCHAR(8)"
    if kind == "TIMS":
        return "VARCHAR(6)"
    if size > 0:
        return f"VARCHAR({min(size, 16777216)})"
    return "VARCHAR(255)"

def build_target_schema_metadata(
    resolved: Dict[str, Any],
    sap_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Snowflake column list from effective target names, not raw SAP names."""
    sap_by_name = {
        str(col.get("name") or "").upper(): col
        for col in (sap_metadata or {}).get("columns") or []
        if col.get("name")
    }
    target_by_name = {
        str(field.get("fieldname") or "").upper(): field
        for field in resolved.get("target_fields") or []
        if field.get("fieldname")
    }
    source_by_name = {
        str(field.get("fieldname") or "").upper(): field
        for field in resolved.get("source_fields") or []
        if field.get("fieldname")
    }
    ordered: List[tuple] = []
    seen: set = set()
    for row in resolved.get("rows") or []:
        target = row.get("tgt_fieldname")
        if not target:
            continue
        key = str(target).upper()
        if key in seen:
            continue
        seen.add(key)
        sources = row.get("src_fieldnames") or []
        ordered.append((str(target), sources[0] if sources else None))
    for name in resolved.get("script_columns") or []:
        key = str(name).upper()
        if key in seen:
            continue
        seen.add(key)
        ordered.append((str(name), None))

    columns: List[Dict[str, Any]] = []
    for target_name, source_name in ordered:
        target_meta = target_by_name.get(target_name.upper()) or {}
        source_meta = (
            source_by_name.get(str(source_name).upper()) if source_name else {}
        ) or {}
        sap_col = sap_by_name.get(str(source_name).upper()) if source_name else None
        datatype = target_meta.get("datatype") or source_meta.get("datatype")
        length = target_meta.get("length") or source_meta.get("length")
        decimals = target_meta.get("decimals")
        if decimals is None:
            decimals = source_meta.get("decimals")
        column: Dict[str, Any] = {"name": target_name}
        if datatype:
            column["dataType"] = str(datatype)
            column["sfType"] = _abap_type_to_sf(str(datatype), length, decimals)
        elif sap_col:
            column["edmType"] = sap_col.get("edmType") or ""
            column["dataType"] = sap_col.get("dataType")
            column["maxLength"] = sap_col.get("maxLength") or sap_col.get("length")
            column["precision"] = sap_col.get("precision")
            column["scale"] = sap_col.get("scale")
        else:
            column["sfType"] = "VARCHAR(255)"
        if sap_col and sap_col.get("primaryKey"):
            column["primaryKey"] = True
        columns.append(column)
    return {"columns": columns}

def _transform_odata_page(
    records: list,
    mode: str,
    time_now: datetime,
    delta_pointer: str,
) -> pd.DataFrame:
    """Transform a single OData page (list of raw dicts) into a clean DataFrame.
    Called once per OData page by both :func:`~services.sap.direct.load_initial`
    and :func:`~services.sap.direct.load_delta` so that the transform logic is DRY
    and memory stays bounded to roughly one page at a time.
    Args:
        records:       Raw record dicts from the OData JSON or Atom XML response.
        mode:          ``"initial"`` for full-load pages, ``"delta"`` for change-set pages.
        time_now:      UTC timestamp stamped on every row in ``__timestamp``.
        delta_pointer: Raw delta token written into ``__delta_pointer``.
    Returns:
        Clean DataFrame ready for parquet serialisation.  Returns an empty
        DataFrame (no columns) when *records* is empty.
    """
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    if mode == "initial":
        # Drop SAP internal bookkeeping columns  -  consumers don't need them.
        # Match the reference loader by removing SAP bookkeeping fields before
        # converting source date columns for snapshot rows.
        df.drop(columns=["__metadata", "ODQ_CHANGEMODE", "ODQ_ENTITYCNTR"], errors="ignore", inplace=True)
        date_cols = find_odata_date_columns(df)
        if date_cols:
            df = convert_odata_columns(df, date_cols)
        df = convert_odata_time_columns(df)
        df["__operation_type"] = "L"   # "L" = full Load (snapshot row)

    else:  # mode == "delta"
        # Derive __operation_type BEFORE dropping ODQ_CHANGEMODE.
        #   "U" (Update) -> "A" (Apply / upsert)
        #   anything else (D=Delete, G--) -> "X" (eXpunge / delete)
        if "ODQ_CHANGEMODE" in df.columns:
            # "U" (Update) -> "A" (Apply/upsert); everything else -> "X" (delete).
            # Using vectorised .eq().map() avoids a Python loop and correctly maps
            # NaN / empty values to "X" (delete) rather than silently treating them
            # as upserts (which the old apply() lambda did).
            df["__operation_type"] = df["ODQ_CHANGEMODE"].eq("U").map({True: "A", False: "X"})
        else:
            df["__operation_type"] = "A"

        df.drop(columns=["__metadata", "ODQ_CHANGEMODE", "ODQ_ENTITYCNTR"], errors="ignore", inplace=True)
        date_cols = find_odata_date_columns(df)
        if date_cols:
            df = convert_odata_columns(df, date_cols)
        df = convert_odata_time_columns(df)

    # Internal audit columns appended on every row regardless of mode.
    df["__delta_pointer"]   = delta_pointer
    df["__sequence_number"] = float("nan")
    df["__timestamp"]       = time_now
    # SAP delta deletes are represented by operation type X.  Snapshot rows,
    # inserts, and updates remain active records.
    df["_SPECT_DELETED"]    = df["__operation_type"].eq("X")

    return df

from decimal import Decimal
import pandas as pd

def enforce_metadata_types(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    df = df.copy()

    for col in meta.get("columns", []):
        name = col["name"]
        if name not in df.columns:
            continue

        dtype = col.get("dataType", "").upper()
        s = df[name]

        if dtype in ("NVARCHAR", "VARCHAR", "STRING"):
            df[name] = s.astype("string")
            length = col.get("length")
            if length and df[name].str.len().gt(length).any():
                raise ValueError(f"{name} contains values longer than {length}")

        elif dtype == "BOOLEAN":
            values = s.map({
                True: True, False: False,
                "true": True, "false": False,
                "True": True, "False": False,
                "X": True, "": False
            })
            df[name] = values.where(s.notna(), None).astype("boolean")

        elif dtype in ("SHORT", "INTEGER", "LONG"):
            pandas_type = {
                "SHORT": "Int16",
                "INTEGER": "Int32",
                "LONG": "Int64"
            }[dtype]
            df[name] = pd.to_numeric(s, errors="raise").astype(pandas_type)

        elif dtype in ("DECIMAL", "NUMBER"):
            precision = col.get("precision", 38)
            scale = col.get("scale", 0)

            def to_decimal(value):
                if pd.isna(value):
                    return None

                value = Decimal(str(value))
                rounded = value.quantize(Decimal(1).scaleb(-scale))

                if rounded != value:
                    raise ValueError(
                        f"{name}: {value} exceeds scale {scale}"
                    )

                required_precision = max(1, max(rounded.adjusted() + 1, 0) + scale)
                if required_precision > precision:
                    raise ValueError(
                        f"{name}: {value} exceeds precision {precision}"
                    )

                return rounded

            df[name] = s.map(to_decimal)

        elif dtype in ("DOUBLE", "FLOAT"):
            df[name] = pd.to_numeric(s, errors="raise").astype("Float64")

        elif dtype == "BINARY":
            df[name] = s.map(
                lambda v: v.encode() if isinstance(v, str) else v
            )

        elif dtype == "INSTANT":
            df[name] = pd.to_datetime(s, errors="raise", utc=True)

    return df

def _odata_url_with_select(
    base_url: str,
    select_columns: Optional[List[str]],
) -> str:
    if not select_columns or "$select=" in base_url:
        return base_url
    from urllib.parse import quote

    encoded = quote(",".join(select_columns), safe=",")
    if "?" in base_url:
        return base_url.replace("?", f"?$select={encoded}&", 1)
    return base_url


def delete_checkpoint_folder(table_name: str) -> bool:
    """
    Delete the entire temporary folder for a table, including checkpoint files.

    Example:
        TEMP_DIR / table_name / "initial" / "checkpoint.json"
        TEMP_DIR / table_name / "delta" / "checkpoint.json"

    Returns:
        True  - folder deleted successfully or did not exist.
        False - deletion failed.
    """
    table_dir = (Path(TEMP_DIR) / table_name).resolve()
    temp_root = Path(TEMP_DIR).resolve()

    # Safety check: only allow deletion inside TEMP_DIR.
    if table_dir == temp_root or temp_root not in table_dir.parents:
        raise ValueError(f"Invalid table directory: {table_dir}")

    try:
        if table_dir.exists():
            shutil.rmtree(table_dir)
            print(f"[CHECKPOINT] Deleted temporary folder: {table_dir}")
        else:
            print(f"[CHECKPOINT] Folder does not exist: {table_dir}")

        return True

    except OSError as exc:
        print(f"[CHECKPOINT] Failed to delete {table_dir}: {exc}")
        return False


def _fetch_initial_parquet(
    server_address: str,
    service_name: str,
    table_name: str,
    username: str,
    password: str,
    chunk_count: int,
    now_utc: datetime,
    blob_name: str,
    snowflake_columns,
    transform_state,
    sap_fields,
    filter_expr: Optional[str] = None,
    delta_name: Optional[str] = None,
    select_columns: Optional[List[str]] = None,
):
    """Fetch all SAP OData initial pages. Stores next_delta_link in df.attrs."""
    # ---------------------------------------------------------
    # Create table directory
    # ---------------------------------------------------------
    rows_extracted = 0
    try:
        def _fetch_detlta_token():
            if not delta_name:
                return None
            try:
                deltalinks_url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/{delta_name}/?$format=json"
                deltalinks_url_resp = requests.get(deltalinks_url, auth=(username, password), verify=False)
                if deltalinks_url_resp.status_code != 200:

                    raise RuntimeError(
                            f"SAP delta-token HTTP {deltalinks_url_resp.status_code}:"
                            f"{deltalinks_url_resp.text[:400]}"
                        )
                delta_linksresp_json = deltalinks_url_resp.json()
                next_delta_url = delta_linksresp_json["d"]["results"][0]["ChangesAfter"]["__deferred"]["uri"]
                delta_token_match = re.search(r"\('([^']+)'\)", next_delta_url)
                delta_token = delta_token_match.group(1) if delta_token_match else None
                return delta_token
            except Exception as exc:

                raise RuntimeError(
                    f"Failed to fetch SAP delta token "
                    f"for table {table_name}: {exc}"
                ) from exc


        table_dir = TEMP_DIR / table_name / "initial"


        table_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        checkpoint_path = table_dir / "checkpoint.json"

        initial_url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/{table_name}/?"
        if filter_expr:
            encoded_filter = quote(filter_expr, safe="'()")
            initial_url += f"$filter={encoded_filter}&"
        initial_url += "$format=json"
        start = force_https_url(_odata_url_with_select(initial_url, select_columns))

        current = start
        page_num = 1
        uploaded_files: List[str] = []

        # Remove the .parquet suffix so the page number can be inserted.
        if blob_name.lower().endswith(".parquet"):
            blob_base = blob_name[:-len(".parquet")]
        else:
            blob_base = blob_name

        # ---------------------------------------------------------
        # Check whether a previous extraction exists
        # ---------------------------------------------------------
        if checkpoint_path.exists():
            try:
                with open(checkpoint_path,"r",encoding="utf-8") as f:
                    checkpoint = json.load(f)
                uploaded_files = checkpoint.get("uploaded_files", [])
                completed = checkpoint.get("completed",False)
                next_url = checkpoint.get("next")
                rows_extracted = checkpoint.get("rows_extracted", 0)

                if completed:
                    if delta_name:
                        delta_token = _fetch_detlta_token()
                    else:
                        delta_token = None

                    return uploaded_files, delta_token, rows_extracted


                if next_url:
                    current = next_url
                    # New checkpoints store the next page number.
                    if checkpoint.get("next_page") is not None:
                        page_num = int(checkpoint["next_page"])
                else:
                    current = start
                    page_num = 1

            except Exception as e:

                raise ValueError(
                    f"Failed to read checkpoint file "
                    f"{checkpoint_path}: {e}"
                )

        else:

            # -----------------------------------------------------
            # First run
            # -----------------------------------------------------

            current = start
            page_num = 1
            # Create initial checkpoint
            checkpoint = {
                "next": current, # next url 
                "uploaded_files": [],  # stores the uploaded files to gcp
                "next_page": 1, # used to create the next file name 
                "rows_extracted": 0, # used to store total number rows extracted from sap 
                "completed": False
            }

            with open(checkpoint_path,"w",encoding="utf-8") as f:
                json.dump(checkpoint,f,indent=4)

        with requests.Session() as sess:
            sess.auth = (username, password)
            sess.headers.update({"Prefer": f"odata.track-changes,odata.maxpagesize={chunk_count}"})
            sess.verify = False
            while current:
                resp = sess.get(current, timeout=120)
                if resp.status_code != 200:
                    raise ValueError(f"SAP initial HTTP {resp.status_code}: {resp.text[:400]}")
                data = resp.json()
                d_block = data.get("d", {})
                records = d_block.get("results", []) if isinstance(d_block, dict) else []
                nxt = d_block.get("__next") if isinstance(d_block, dict) else None
                if nxt:
                    current = force_https_url(nxt, server_address, service_name)

                else:
                    current = None

                if records:
                    page_df = _transform_odata_page(records=records, mode="initial", time_now=now_utc, delta_pointer="initial")
                    if transform_state and not page_df.empty:
                        page_df = execute_transform_chunk(
                            page_df,
                            transform_state.get("mapping_json"),
                            transform_state.get("mapping_overrides"),
                            transform_state.get("script_text"),
                            transform_state.get("entry_point"),
                            transform_state.get("run_order") or "filter_script_mapping",
                            sap_metadata_fields=sap_fields,
                        )
                else:
                    page_df = pd.DataFrame()

                page_df = _align_df_to_snowflake_columns(page_df,snowflake_columns)
                rows_extracted += len(page_df)
                # Example: temp_data_sap_CUSTOMERS_CONN1_RUN1_page_1.parquet
                page_blob_name = f"{blob_base}_page_{page_num}.parquet"

                # Must finish successfully before updating the checkpoint.
                gcs_uri = upload_parquet_to_gcs(
                    dataframe=page_df,
                    blob_name=page_blob_name,
                )
                uploaded_files.append(gcs_uri)
                print(f"Page {page_num} uploaded successfully.")

                if nxt:
                    page_num += 1
                    next_url = force_https_url(nxt, server_address, service_name)
                    checkpoint = {"next": next_url,"uploaded_files": uploaded_files,"next_page": page_num,"rows_extracted": rows_extracted, "completed": False}
                    checkpoint_tmp_path = table_dir / "checkpoint.json.tmp"

                    with open(checkpoint_tmp_path, "w", encoding="utf-8") as f:
                        json.dump(checkpoint, f, indent=4)
                        f.flush()
                        os.fsync(f.fileno())

                    os.replace(checkpoint_tmp_path, checkpoint_path)
                    print(f"Checkpoint updated. Next page: {page_num + 1}")
                    current = next_url
                    
                else:
                    checkpoint = {"next": None,"uploaded_files": uploaded_files,"next_page":None,"rows_extracted": rows_extracted, "completed": True}
                    checkpoint_tmp_path = table_dir / "checkpoint.json.tmp"

                    with open(checkpoint_tmp_path, "w", encoding="utf-8") as f:
                        json.dump(checkpoint, f, indent=4)
                        f.flush()
                        os.fsync(f.fileno())

                    os.replace(checkpoint_tmp_path, checkpoint_path)
                    print("SAP initial extraction completed.")
                    current = None

        if delta_name:
            delta_token=_fetch_detlta_token()
        else:
            delta_token = None

        return uploaded_files,delta_token, rows_extracted
    except Exception as exc:

        raise RuntimeError(
            f"SAP initial extraction failed "
            f"for table {table_name}: {exc}"
        ) from exc


def _fetch_delta_parquet(
    server_address,
    service_name,
    table_name,
    username,
    password,
    chunk_count,
    now_utc,
    blob_name,
    snowflake_columns ,
    filter_expr,
    delta_name   
):
    pass

def _fetch_delta_parquet(
    server_address: str,
    service_name: str,
    table_name: str,
    username: str,
    password: str,
    chunk_count: int,
    delta_token: str,
    now_utc: datetime,
    blob_name: str,
    snowflake_columns,
    transform_state,
    sap_fields,
    filter_expr: Optional[str] = None,
    delta_name: Optional[str] = None,
):
    """Fetch all SAP OData initial pages. Stores next_delta_link in df.attrs."""
    # ---------------------------------------------------------
    # Create table directory
    # ---------------------------------------------------------

    def _fetch_detlta_token():
        deltalinks_url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/{delta_name}/?$format=json"
        deltalinks_url_resp = requests.get(deltalinks_url, auth=(username, password), verify=False)
        delta_linksresp_json = deltalinks_url_resp.json()
        # Get all results
        tokens_list = delta_linksresp_json.get("d", {}).get("results", [])

        if tokens_list:
            # Find the record with the latest CreatedAt
            latest_item = max(
                tokens_list,
                key=lambda x: int(x["CreatedAt"][6:-2])
            )

            latest_delta_token = latest_item["DeltaToken"]

            print("Latest CreatedAt:", latest_item["CreatedAt"])
            print("Latest DeltaToken:", latest_delta_token)
        else:
            latest_delta_token = None
            print("No delta records found.")

        return latest_delta_token

    table_dir = TEMP_DIR / table_name / "delta"

    table_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    checkpoint_path = table_dir / "checkpoint.json"
    
    delta_url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/{delta_name}?"
    if filter_expr:
        encoded_filter = quote(filter_expr, safe="'()")
        delta_url += f"$filter={encoded_filter}"
    delta_url +=f"&!deltatoken='{delta_token}'"
    delta_url += "&$format=json"
    start = force_https_url(_odata_url_with_select(delta_url))

    current = start
    page_num = 1
    uploaded_files: List[str] = []

    # Remove the .parquet suffix so the page number can be inserted.
    if blob_name.lower().endswith(".parquet"):
        blob_base = blob_name[:-len(".parquet")]
    else:
        blob_base = blob_name

    # ---------------------------------------------------------
    # Check whether a previous extraction exists
    # ---------------------------------------------------------
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path,"r",encoding="utf-8") as f:
                checkpoint = json.load(f)
            uploaded_files = checkpoint.get("uploaded_files", [])
            completed = checkpoint.get("completed",False)
            next_url = checkpoint.get("next")

            if completed:
                    if delta_name:
                        delta_token=_fetch_detlta_token()
                        return uploaded_files, delta_token
                    else:
                        return uploaded_files, None
                    
            if next_url:
                current = next_url
                # New checkpoints store the next page number.
                if checkpoint.get("next_page") is not None:
                    page_num = int(checkpoint["next_page"])
            else:
                current = start
                page_num = 1

        except Exception as e:

            raise ValueError(
                f"Failed to read checkpoint file "
                f"{checkpoint_path}: {e}"
            )

    else:

        # -----------------------------------------------------
        # First run
        # -----------------------------------------------------

        current = start
        page_num = 1
        # Create initial checkpoint
        checkpoint = {
            "next": current,
            "uploaded_files": [],
            "next_page": 1,
            "completed": False
        }

        with open(checkpoint_path,"w",encoding="utf-8") as f:
            json.dump(checkpoint,f,indent=4)

    with requests.Session() as sess:
        sess.auth = (username, password)
        sess.headers.update({"Prefer": f"odata.track-changes,odata.maxpagesize={chunk_count}"})
        sess.verify = False
        while current:
            resp = sess.get(current, timeout=120)
            if resp.status_code != 200:
                raise ValueError(f"SAP initial HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json()
            d_block = data.get("d", {})
            records = d_block.get("results", []) if isinstance(d_block, dict) else []
            nxt = d_block.get("__next") if isinstance(d_block, dict) else None
            if nxt:
                current = force_https_url(nxt, server_address, service_name)

            else:
                current = None

            if records:
                page_df = _transform_odata_page(records=records, mode="delta", time_now=now_utc, delta_pointer=delta_token)
                if transform_state and not page_df.empty:
                    page_df = execute_transform_chunk(
                        page_df,
                        transform_state.get("mapping_json"),
                        transform_state.get("mapping_overrides"),
                        transform_state.get("script_text"),
                        transform_state.get("entry_point"),
                        transform_state.get("run_order") or "filter_script_mapping",
                        sap_metadata_fields=sap_fields,
                    )
            else:
                page_df = pd.DataFrame()

            page_df = _align_df_to_snowflake_columns(page_df,snowflake_columns)
            # Example: temp_data_sap_CUSTOMERS_CONN1_RUN1_page_1.parquet
            page_blob_name = f"{blob_base}_page_{page_num}.parquet"

            # Must finish successfully before updating the checkpoint.
            gcs_uri = upload_parquet_to_gcs(
                dataframe=page_df,
                blob_name=page_blob_name,
            )
            uploaded_files.append(gcs_uri)
            print(f"Page {page_num} uploaded successfully.")

            if nxt:
                page_num += 1
                next_url = force_https_url(nxt, server_address, service_name)
                checkpoint = {"next": next_url,"uploaded_files": uploaded_files,"next_page": page_num, "completed": False}
                checkpoint_tmp_path = table_dir / "checkpoint.json.tmp"

                with open(checkpoint_tmp_path, "w", encoding="utf-8") as f:
                    json.dump(checkpoint, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(checkpoint_tmp_path, checkpoint_path)
                print(f"Checkpoint updated. Next page: {page_num + 1}")
                current = next_url
                
            else:
                checkpoint = {"next": None,"uploaded_files": uploaded_files,"next_page":None, "completed": True}
                checkpoint_tmp_path = table_dir / "checkpoint.json.tmp"

                with open(checkpoint_tmp_path, "w", encoding="utf-8") as f:
                    json.dump(checkpoint, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(checkpoint_tmp_path, checkpoint_path)
                print("SAP initial extraction completed.")
                current = None

    if delta_name:
        delta_token=_fetch_detlta_token()
    else:
        delta_token = None

    return uploaded_files,delta_token


def _copy_into_staging(
    engine: Engine,
    schema: str,
    staging: str,
    gcs_uri: str,
) -> int:
    """Load one GCS Parquet file into Snowflake staging."""

    norm_s = _sf_ident(staging)

    if not gcs_uri.startswith("gcs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    copy_sql = f"""
        COPY INTO "{schema}"."{norm_s}"
        FROM '{gcs_uri}'
        FILE_FORMAT = (TYPE = PARQUET COMPRESSION = AUTO)
        MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
        STORAGE_INTEGRATION = {DEMO_STORAGE}
        ON_ERROR = ABORT_STATEMENT
        PURGE = FALSE
    """

    rows_loaded = 0

    with engine.begin() as conn:
        results = conn.execute(text(copy_sql)).fetchall()
        print("results:",results)
        for row in results:
            # COPY result columns:
            # 0=file, 1=status, 2=rows_parsed, 3=rows_loaded
            rows_loaded += int(row[3] or 0)

    print(
        f"[COPY] Loaded {rows_loaded} rows "
        f"from {gcs_uri} into {norm_s}"
    )

    return rows_loaded

def _insert_overwrite(
    engine: Engine,
    schema: str,
    staging: str,
    target: str,
    column_order: List[str],
) -> int:
    ns = _sf_ident(staging)
    nt = _sf_ident(target)
    col_list = ", ".join(f'"{c}"' for c in column_order)
    with engine.begin() as c:
        c.execute(text(f'TRUNCATE TABLE "{schema}"."{nt}"'))
        r = c.execute(text(
            f'INSERT INTO "{schema}"."{nt}" ({col_list}) '
            f'SELECT {col_list} FROM "{schema}"."{ns}"'
        ))
        return r.rowcount


def _merge_into(
    engine: Engine,
    schema: str,
    staging: str,
    target: str,
    pks: List[str],
    all_cols: List[str],
) -> int:
    ns = _sf_ident(staging)
    nt = _sf_ident(target)
    col_list = ", ".join(f'"{c}"' for c in all_cols)
    if not pks:
        with engine.begin() as c:
            r = c.execute(text(
                f'INSERT INTO "{schema}"."{nt}" ({col_list}) '
                f'SELECT {col_list} FROM "{schema}"."{ns}"'
            ))
            return r.rowcount
    join   = " AND ".join(f't."{k}"=s."{k}"' for k in pks)
    non_pk = [c for c in all_cols if c not in pks]
    cl     = ", ".join(f'"{c}"' for c in all_cols)
    vl     = ", ".join(f's."{c}"' for c in all_cols)
    if non_pk:
        upd = ", ".join(f't."{c}"=s."{c}"' for c in non_pk)
        sql = f'MERGE INTO "{schema}"."{nt}" t USING "{schema}"."{ns}" s ON {join} WHEN MATCHED THEN UPDATE SET {upd} WHEN NOT MATCHED THEN INSERT ({cl}) VALUES ({vl})'
    else:
        sql = f'MERGE INTO "{schema}"."{nt}" t USING "{schema}"."{ns}" s ON {join} WHEN NOT MATCHED THEN INSERT ({cl}) VALUES ({vl})'
    with engine.begin() as c:
        return c.execute(text(sql)).rowcount

    
def migrate_table_snowflake(src_details,sf_engine,source_config,destination_config,details_for_sts_update):
    t0 = time.time()
    service_name = src_details.service_name if src_details.service_name else "ZGW_ODATA_SRV"
    table_name = src_details.entity_name
    schema = destination_config["schema"].upper()
    # Row filter settings
    filter_expr = src_details.filter_expr if src_details.filter_expr else None 
    t_start = time.time()
    is_updated  = src_details.is_updated or "_FIVETRAN_SYNCED"
    is_deleted  = src_details.is_deleted or "_FIVETRAN_DELETED"
    chunk_count = src_details.pagination_count if src_details.pagination_count else 200000
    transform_state = src_details.transform_state

    if transform_state is not None:
        transform_state = MessageToDict(
            transform_state,
            preserving_proto_field_name=True
        )
    else:
        transform_state = {}
    dest_from_transform = transform_state.get("destination_table_name")
    if dest_from_transform and is_valid_snowflake_destination_name(dest_from_transform):
        target_table = dest_from_transform.strip().upper()
    else:
        target_table = _sf_ident(table_name)
    staging_table = f"STG_{target_table}"

    username = source_config.username
    password = source_config.password
    connection_id = details_for_sts_update["connection_id"]

    now_utc = src_details.utc_time
    delta_name = src_details.delta_name

    blob_name: str = f"temp_data_sap_{target_table}_{connection_id}.parquet"

    # # Check ETL state (service_name is stored per table during Update Schema)
    # etl_state = _get_table_etl_state(req.connection_id, table_name, existing_conn=db_engine)
    server_addr = f"{source_config.host}:{source_config.port}"

    load_method = src_details.load_method
    load_method = str(load_method).strip().lower()


    # # An initial-only table is intentionally processed once.  A subsequent
    # # execution is a successful no-op, not an error or a new load type.
    # if (
    #     load_method == "initial"
    #     and src_details.initial_completed_flag
    #     and not src_details.drop_and_reload
    # ):
    #     completed_at = datetime.now(timezone.utc)

    #     # status update -------------------------------------------------------------------------
    #     # return {
    #     #     "table_name": table_name,
    #     #     "status": "success",
    #     #     "rows_extracted": 0,
    #     #     "rows_loaded": 0,
    #     #     "duration": round(time.time() - t_start, 2),
    #     #     "message": "Initial load already completed; table was not processed again.",
    #     #     "error": None,
    #     # }

    #     return True
    
        # status update -------------------------------------------------------------------------
    # update_table_status_snowflake(
    #     req.connection_id,
    #     table_name,
    #     "in_progress",
    #     req.job_name,
    #     req.migration_session_id,
    # )

    # status_details ={ 
    #     "migration_session_id":details_for_sts_update["migration_session_id"],
    #     "connection_id":connection_id,
    #     "job_name":details_for_sts_update[""]
    #     "table_name":table_name,
    #     "rows_processed":,
    #     "delta_token":
    #     "status":"completed",
    #     "message":}

    # update_migration_status(status_details)

    status_details ={ 
        "migration_session_id":details_for_sts_update["migration_session_id"],
        "connection_id":connection_id,
        "job_name":details_for_sts_update["job_name"],
        "table_name":table_name,
        "status":"in_progress"
    }
    update_table_status(status_details)

    try:
        if not server_addr:
            raise ValueError(f"Missing SAP host/port in source config for table {table_name}")
        if not service_name:
            raise ValueError(f"Missing SAP service_name for table {table_name}")

        # 1. Fetch metadata & prepare DDL
        
        try:
            meta_url = f"https://{server_addr}/sap/opu/odata/sap/{service_name}/$metadata"
            meta = fetch_metadata(meta_url, username, password, entity_name=table_name)

        except TimeoutError:
            return "Network timeout: SAP server did not respond in time."

        except ConnectionError:
            return "Connection failure: Unable to reach SAP server."

        except ValueError as e:
            if "XML" in str(e):
                return "Invalid XML: Metadata response could not be parsed."
            elif "entity" in str(e).lower():
                return "Entity not found: Entity type could not be resolved."
            return str(e)

        except Exception as e:
            return str(e)
        pks = [c["name"] for c in meta.get("columns", []) if c.get("primaryKey")]
        previous_dest = (transform_state or {}).get("previous_destination_table_name")
        rename_mode = (transform_state or {}).get("destination_rename_mode")
        if (previous_dest and _sf_ident(previous_dest) != target_table and rename_mode):
            # logger.info(
            #     "[SAP -> SF] Destination rename choice=%s | %s -> %s | entity=%s",
            #     rename_mode,
            #     previous_dest,
            #     target_table,
            #     table_name,
            # )
            if rename_mode == "rename_existing":
                if _snowflake_table_exists(sf_engine, schema, previous_dest):
                    _rename_snowflake_table(sf_engine, schema, previous_dest, target_table)
            elif rename_mode == "create_new":
                # Safer default: leave the old table untouched and full-reload
                # into the new destination. Watermarks follow the entity, so
                # reset them only for this full-reload choice.
                update_table_etl_status(
                    {
                    "connection_id":connection_id,
                    "table_name":table_name,
                    "initial_completed_flag":False,
                    "delta_token":None,
                    })
                
                # etl_state["initial_completed_flag"] = False
                load_method = "initial"
                # logger.info(
                #     "[SAP -> SF] Full reload into new table %s; old table %s kept",
                #     target_table,
                #     previous_dest,
                # )

            # _clear_destination_rename_mode(
            #     req.connection_id, table_name, db_engine
            # )

        if src_details.drop_and_reload:
            # Truncate-and-load is allowed only after the user has emptied the
            # existing target table.  Keep the table definition and do not
            # silently drop user-managed Snowflake objects.
            norm_target = _sf_ident(target_table)
            if inspect(sf_engine).has_table(norm_target, schema=schema):
                with sf_engine.connect() as c:
                    target_rows = (
                        c.execute(
                            text(f'SELECT COUNT(*) FROM "{schema}"."{norm_target}"')
                        ).scalar()
                        or 0
                    )
                if target_rows:   
                    raise ValueError(
                        f"Please truncate the Snowflake table '{schema}.{norm_target}' "
                        "before running truncate and load."
                    )

            update_table_etl_status(
                    {
                    "connection_id":connection_id,
                    "table_name":table_name,
                    "initial_completed_flag":False,
                    "delta_token":None
                    })

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
            target_meta = build_target_schema_metadata(resolved_mapping, meta)
            if target_meta.get("columns"):
                ddl_meta = target_meta
                # logger.info(
                #     "[SAP -> SF] Destination %s uses %d mapped target columns",
                #     target_table,
                #     len(target_meta["columns"]),
                # )
        ensure_snowflake_table_exists(sf_engine, schema, target_table, ddl_meta, pks, is_updated, is_deleted)
        _ensure_staging_table(sf_engine, schema, target_table, staging_table)
        snowflake_columns = _get_snowflake_column_order(sf_engine, schema, staging_table)
        if not snowflake_columns:
            raise ValueError(f"No Snowflake columns found for staging table {staging_table}")
        select_columns: Optional[List[str]] = None
        transform_version = None
        if transform_state:
            transform_version = transform_state.get("version")
            resolved = resolved_mapping or resolve_effective_mapping(
                transform_state.get("mapping_json"),
                transform_state.get("mapping_overrides"),
            )
            select_columns = resolved.get("extract_columns") or None
            # batch_cap = max(1, DRY_RUN_SAP_SELECT_BATCH_SIZE)
            batch_cap = max(1, 50)
            if select_columns and len(select_columns) > batch_cap:
                # logger.info(
                #     "[SAP -> SF] %d extract columns exceed OData $select limit (%d); "
                #     "fetching full entity without $select",
                #     len(select_columns),
                #     batch_cap,
                # )
                select_columns = None
            # logger.info(
            #     "[SAP -> SF] Transform v%s active for %s (%d extract columns)",
            #     transform_version,
            #     table_name,
            #     len(resolved.get("extract_columns") or []),
            # )

        if load_method == "initial":
            uploaded_files, next_delta_token, rows_extracted  = _fetch_initial_parquet(
                server_address=server_addr,
                service_name=service_name,
                table_name=table_name,
                username=username,
                password=password,
                chunk_count=chunk_count,
                now_utc=now_utc,
                blob_name=blob_name,
                snowflake_columns = snowflake_columns,
                transform_state = transform_state,
                sap_fields=sap_fields,
                filter_expr=filter_expr,
                delta_name=delta_name,
                select_columns = select_columns,
            )

        elif load_method == "delta":
            uploaded_files, next_delta_token = _fetch_delta_parquet(
                server_address=server_addr,
                service_name=service_name,
                table_name=table_name,
                username=username,
                password=password,
                chunk_count=chunk_count,
                delta_token = src_details.delta_token,
                now_utc=now_utc,
                blob_name=blob_name,
                snowflake_columns = snowflake_columns,
                transform_state = transform_state,
                sap_fields=sap_fields,
                filter_expr=filter_expr,
                delta_name=delta_name,     
            )

        uploaded_files.sort(key=lambda x: int(x.split("_page_")[1].split(".")[0]))

        rows_staged = 0
        if uploaded_files:
            
            #  Clear staging ONCE before loading all the files.
            with sf_engine.begin() as c:
                c.execute(text(f'TRUNCATE TABLE "{schema}"."{_sf_ident(staging_table)}"'))
            for gcs in uploaded_files:
                rows_staged += _copy_into_staging(
                    engine=sf_engine,
                    schema=schema,
                    staging=staging_table,
                    gcs_uri=gcs
                )    
            print(f"Total rows loaded into staging: {rows_staged}")

        rows_loaded= 0
        if load_method == "initial" and rows_staged != 0:
            rows_loaded = _insert_overwrite(
                engine=sf_engine,
                schema=schema,
                staging=staging_table,
                target=target_table,
                column_order=snowflake_columns,
            )
        elif load_method == "delta" and rows_staged != 0:
            rows_loaded = _merge_into(
                sf_engine, schema, staging_table, target_table, pks, snowflake_columns
            )
            print(
                f"delta load completed for {target_table}. "
                f"Rows inserted: {rows_loaded}"
            )
    
        _delete_gcs_blob(uploaded_files)
        delete_checkpoint_folder(table_name)
        update_table_etl_status(
        {
        "connection_id":connection_id,
        "table_name":table_name,
        "initial_completed_flag":True,
        "delta_token":next_delta_token,
        })

        duration = time.time() - t_start
        data_transfer_logs({
            "cmp_id":details_for_sts_update["cmp_id"],
            "connection_id":connection_id,
            "usr_id":details_for_sts_update["user_id"],
            "table_name":table_name,
            "start_time":None,
            "end_time":None,
            "inserts":rows_loaded,
            "columns_data":len(snowflake_columns),
            "session":details_for_sts_update["session"],
            "job_name":details_for_sts_update["job_name"],
            "migration_session_id":details_for_sts_update["migration_session_id"],
        })

        status_details ={ 
            "migration_session_id":details_for_sts_update["migration_session_id"],
            "connection_id":connection_id,
            "job_name":details_for_sts_update["job_name"],
            "table_name":table_name,
            "status":"completed",
            "sync_end_time":None
        }
        update_table_status(status_details)

        return {
            "table_name": table_name,
            "destination_table_name": target_table,
            "status": "success",
            "rows_extracted": rows_extracted,
            "rows_loaded": rows_loaded,
            "duration": round(duration, 2),
            "load_type": load_method,
            "transform_version": transform_version,
            "error": None,
        }

    except Exception as e:
        import traceback
        print("ERROR in sap_odata_to_snowflake_connector:", repr(e))
        traceback.print_exc()
        err_msg = str(e)

        record_table_failure({
            "req":{
                "connection_id":connection_id,
                "job_name":details_for_sts_update["job_name"],
                "migration_session_id":details_for_sts_update["migration_session_id"],
                "cmp_id":details_for_sts_update["cmp_id"],
                "session":details_for_sts_update["session"],
                "user_id":details_for_sts_update["user_id"]
            },
            "table_name":table_name,
            "err_msg":err_msg
        })

        return {
            "table_name": table_name,
            "status": "failed",
            "error": err_msg,
            "rows_loaded": 0,
            "duration": round(time.time() - t_start, 2),
            "load_type": load_method,
        }

    
def sap_odata_to_snowflake_connector(req, table_data):
    start_time = datetime.now()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()

    source_config = req.src_conf
    details_for_sts_update = req.details_for_sts_update

    try:
        sf_engine = _get_snowflake_engine(req.destination_config)

        _ensure_gcs_integration(
            sf_engine,
            details_for_sts_update["connection_id"]
        )

        result= migrate_table_snowflake(
                table_data,
                sf_engine,
                source_config,
                req.destination_config,
                details_for_sts_update,
        )

    except Exception as e:
        import traceback
        print("Pipeline error:", repr(e))
        traceback.print_exc()

    finally:
        end_time = datetime.now()
        wall_time = time.perf_counter() - wall_start
        cpu_time = time.process_time() - cpu_start
        total_duration = end_time - start_time
        print("\n===== PIPELINE PERFORMANCE =====")
        print(f"Total wall time : {wall_time:.2f} seconds")
        print(f"Total CPU time  : {cpu_time:.2f} seconds")
        print(f"Total Duration  : {total_duration}")

        if wall_time > 0:
            print(
                f"Average CPU usage: "
                f"{cpu_time / wall_time * 100:.2f}% of one CPU core"
            )
        return result
