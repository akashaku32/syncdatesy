"""SAP OData to Snowflake orchestrator.

Architecture mirrors the Salesforce and D365 connectors:
 1. Extract OData pages from SAP Gateway (Direct HTTP or Proxy)
 2. Transform to Pandas DataFrame, add audit columns
 3. Upload CSV to GCS bucket
 4. Run COPY INTO with VALIDATION_MODE = RETURN_ERRORS, expand VARCHAR cols
 5. Execute final COPY INTO ON_ERROR = ABORT_STATEMENT
 6. Initial load: INSERT OVERWRITE; Delta load: MERGE INTO on primary keys
 7. Persist next delta token to PostgreSQL source_table_selection
 8. Clean up GCS blob and truncate staging table

PostgreSQL: postgresql://postgres:DSQa_2026@136.115.40.171:5432/datasyncher_batch
Snowflake: user-supplied destination_config credentials
"""

from __future__ import annotations

import csv
import logging
import os
import re
import time
import traceback as tb_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import HTTPException
from google.api_core.exceptions import ServiceUnavailable
from google.cloud import storage
from models.sap_odata_models import SAPODataSnowflakeMigrationRequest
from services.sap.as_source.to_adls import (
    MigrationFailureTracker,
    _get_table_etl_state,
    _update_table_etl_state,
)
from services.sap.proxy import is_proxy_connection, run_proxy_batch_load
from services.sap.row_filter import build_row_filter_expression
from services.sap.source import (
    SAP_MAX_PAGES,
    SAP_REQUEST_TIMEOUT,
    _apply_row_filter_to_page,
    _extract_delta_link,
    _fetch_delta_link_via_entity_set,
    _transform_odata_page,
    build_sap_delta_start_url,
    extract_token_from_delta_link,
    fetch_metadata,
    force_https_url,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
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
from services.transform.db_state import get_active_entity_transform
from services.transform.destination_table_name import is_valid_snowflake_destination_name
from config.transform_limits import DRY_RUN_SAP_SELECT_BATCH_SIZE
from services.transform.dataframe_columns import (
    align_dataframe_to_names,
    consolidate_target_columns,
)
from services.transform.executor import execute_transform_chunk
from services.transform.one_to_many import log_one_to_many_column_fill
from services.transform.mapping_resolver import resolve_effective_mapping

logger = logging.getLogger(__name__)


def _sf_ident(name: str) -> str:
    """Quote-ready Snowflake identifier. Do not re-split digits on already-valid names."""
    if is_valid_snowflake_destination_name(name):
        return str(name).strip().upper()
    return normalize_name(name)


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
    logger.info(
        "[SAP -> SF] Renamed Snowflake table %s.%s -> %s (user choice)",
        schema,
        old_ident,
        new_ident,
    )


def _clear_destination_rename_mode(
    connection_id: int, table_name: str, existing_conn: Any
) -> None:
    try:
        cursor = existing_conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE entity_transform
                SET destination_rename_mode = NULL
                WHERE connection_id = %s
                  AND table_name = %s
                  AND is_active = TRUE
                """,
                (connection_id, table_name),
            )
            existing_conn.commit()
        finally:
            cursor.close()
    except Exception as exc:
        logger.warning(
            "[SAP -> SF] Failed to clear destination_rename_mode for %s: %s",
            table_name,
            exc,
        )


def _resolve_celery_child_mode(request) -> bool:
    """True when Celery fan-out assigned this table group to a child worker."""
    return bool(getattr(request, "celery_child_mode", False)) or bool(
        getattr(request, "celery_fanout_mode", False)
    )


# ---------------------------------------------------------------------------
# Environment constants (same as Salesforce and D365 connectors)
# ---------------------------------------------------------------------------

BUCKET_NAME: str = os.environ.get("GCS_BUCKET_NAME", "")
GCS_BUCKET_URL: str = f"gcs://{BUCKET_NAME}/" if BUCKET_NAME else ""
DEMO_STORAGE: str = os.environ.get("SNOWFLAKE_STORAGE_INTEGRATION", "")

# ---------------------------------------------------------------------------
# EDM to Snowflake type map
# ---------------------------------------------------------------------------

EDM_TO_SNOWFLAKE: Dict[str, str] = {
    "Edm.String": "VARCHAR(255)",
    "Edm.Boolean": "BOOLEAN",
    "Edm.Byte": "NUMBER(3,0)",
    "Edm.SByte": "NUMBER(3,0)",
    "Edm.Int16": "NUMBER(5,0)",
    "Edm.Int32": "NUMBER(10,0)",
    "Edm.Int64": "NUMBER(19,0)",
    "Edm.Single": "FLOAT",
    "Edm.Double": "FLOAT",
    # Edm.Decimal uses precision/scale from SAP metadata; NUMBER(38,10) is the
    # last-resort fallback when neither attribute is present in the metadata.
    "Edm.Decimal": "NUMBER(38,10)",
    "Edm.DateTime": "TIMESTAMP_NTZ",
    "Edm.DateTimeOffset": "TIMESTAMP_TZ",
    "Edm.Time": "TIME",
    "Edm.Guid": "VARCHAR(36)",
    "Edm.Binary": "BINARY",
}

# ---------------------------------------------------------------------------
# Snowflake connection helpers
# ---------------------------------------------------------------------------


def _get_snowflake_engine(destination_config: Dict[str, Any]) -> Engine:
    """Build a SQLAlchemy engine for Snowflake. Supports password and PEM key auth."""
    account = destination_config.get("account", "")
    user = destination_config.get("username") or destination_config.get("user", "")
    password = destination_config.get("password", "")
    warehouse = destination_config.get("warehouse", "")
    database = destination_config.get("database", "")
    schema_ = destination_config.get("schema", "")
    role = destination_config.get("role", "")
    pk_str = destination_config.get("private_key") or destination_config.get(
        "privateKey"
    )

    if pk_str:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        from services.snowflake.service import normalize_pem_key

        pem_str = normalize_pem_key(pk_str)
        passphrase_raw = (
            destination_config.get("passphrase")
            or destination_config.get("private_key_passphrase")
            or ""
        )
        passphrase_bytes = (
            str(passphrase_raw).encode("utf-8") if str(passphrase_raw).strip() else None
        )
        pk_obj = serialization.load_pem_private_key(
            pem_str.encode(),
            password=passphrase_bytes,
            backend=default_backend(),
        )
        pk_bytes = pk_obj.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return create_engine(
            f"snowflake://{user}@{account}/{database}/{schema_}?warehouse={warehouse}&role={role}",
            connect_args={"private_key": pk_bytes},
        )

    return create_engine(
        f"snowflake://{user}:{password}@{account}/{database}/{schema_}?warehouse={warehouse}&role={role}"
    )


# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------


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
    full = f'"{schema}"."{norm}"'
    cols = (schema_metadata or {}).get("columns", [])

    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        exists = (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=:s AND table_name=:t"
                ),
                {"s": schema.upper(), "t": norm.upper()},
            ).scalar()
            > 0
        )

        if not exists:
            defs = [
                f'"{c.get("name")}" {_column_sf_type(c)}'
                for c in cols
            ]
            if is_updated not in [c.get("name") for c in cols]:
                defs.append(f'"{is_updated}" TIMESTAMP_TZ')
            if is_deleted not in [c.get("name") for c in cols]:
                defs.append(f'"{is_deleted}" BOOLEAN DEFAULT FALSE')
            conn.execute(text(f'CREATE TABLE IF NOT EXISTS {full} ({", ".join(defs)})'))
            logger.info("[DDL] Created %s", full)
            return False

        existing = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT UPPER(column_name) FROM information_schema.columns WHERE table_schema=:s AND table_name=:t"
                ),
                {"s": schema.upper(), "t": norm.upper()},
            ).fetchall()
        }
        for c in cols:
            if c.get("name", "").upper() not in existing:
                conn.execute(
                    text(
                        f'ALTER TABLE {full} ADD COLUMN IF NOT EXISTS "{c["name"]}" '
                        f'{_column_sf_type(c)}'
                    )
                )
        for ac, at in [
            (is_updated, "TIMESTAMP_TZ"),
            (is_deleted, "BOOLEAN DEFAULT FALSE"),
        ]:
            if ac.upper() not in existing:
                conn.execute(
                    text(f'ALTER TABLE {full} ADD COLUMN IF NOT EXISTS "{ac}" {at}')
                )
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


def _align_df_to_snowflake_columns(
    df: pd.DataFrame, column_order: List[str]
) -> pd.DataFrame:
    """Match DataFrame columns to Snowflake ordinal order; NULL-fill missing columns."""
    if not column_order:
        raise ValueError("Cannot align DataFrame without Snowflake column order")

    aligned = consolidate_target_columns(df, column_order)
    aligned = align_dataframe_to_names(aligned, column_order)
    if aligned.columns.duplicated().any():
        dupes = list(aligned.columns[aligned.columns.duplicated()])
        logger.warning(
            "[SAP -> SF] Dropping duplicate DataFrame columns: %s", dupes[:10]
        )
        aligned = aligned.loc[:, ~aligned.columns.duplicated()]

    extra_cols = [c for c in aligned.columns if c not in column_order]
    if extra_cols:
        logger.info(
            "[SAP -> SF] Dropping %d DataFrame column(s) not in Snowflake table",
            len(extra_cols),
        )
        aligned = aligned.drop(columns=extra_cols, errors="ignore")

    missing_cols = [c for c in column_order if c not in aligned.columns]
    if missing_cols:
        logger.info(
            "[SAP -> SF] NULL-filling %d Snowflake column(s) missing from extract",
            len(missing_cols),
        )

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


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------


def _upload_df_to_gcs(
    df: pd.DataFrame,
    table_name: str,
    connection_id: int,
    run_id: str,
    max_retries: int = 5,
) -> Optional[str]:
    """Upload DataFrame as UTF-8 CSV to GCS. Returns gcs:// URI or None."""
    blob_name = f"temp_data_sap_{table_name}_{connection_id}_{run_id}.csv"
    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(blob_name)
        csv_data = df.to_csv(
            index=False, encoding="utf-8", quoting=csv.QUOTE_ALL, escapechar="\\"
        )
        for attempt in range(max_retries):
            try:
                blob.upload_from_string(csv_data, content_type="text/csv")
                logger.info("[GCS] Uploaded '%s' (attempt %d)", blob_name, attempt + 1)
                return f"gcs://{BUCKET_NAME}/{blob_name}"
            except ServiceUnavailable as e:
                logger.warning("[GCS] 503 attempt %d: %s", attempt + 1, e)
                time.sleep(2**attempt)
            except Exception as e:
                logger.error("[GCS] Upload failed attempt %d: %s", attempt + 1, e)
                break
    except Exception as e:
        logger.error("[GCS] Fatal error: %s", e)
    return None


def _delete_gcs_blob(blob_name: str) -> None:
    try:
        blob = storage.Client().bucket(BUCKET_NAME).blob(blob_name)
        if blob.exists():
            blob.delete()
            logger.info("[GCS] Deleted '%s'", blob_name)
    except Exception as e:
        logger.warning("[GCS] Delete failed '%s': %s", blob_name, e)


# ---------------------------------------------------------------------------
# Snowflake COPY INTO helpers
# ---------------------------------------------------------------------------

_FILE_FMT = (
    "TYPE = CSV "
    "FIELD_OPTIONALLY_ENCLOSED_BY = '\"' "
    "SKIP_HEADER = 1 "
    r"ESCAPE = '\\' "
    r"ESCAPE_UNENCLOSED_FIELD = '\\' "
    "FIELD_DELIMITER = ',' "
    r"RECORD_DELIMITER = '\n' "
    "TIMESTAMP_FORMAT = 'AUTO' "
    "DATE_FORMAT = 'AUTO' "
    "NULL_IF = ('', 'NULL', 'None', 'null') "
    "ENCODING = 'UTF8'"
)


def _resolve_server_address(source_config: Dict[str, Any]) -> str:
    """Build SAP host:port from source config (matches SAP ADLS connector)."""
    sc = source_config or {}
    server_address = (sc.get("server_address") or "").strip()
    if server_address:
        return server_address

    host = (sc.get("host") or "").strip()
    port = sc.get("port")
    if host and port:
        return f"{host}:{port}"
    return host


def _ensure_gcs_integration(sf_engine: Engine, connection_id: int, cmp_id: int) -> None:
    """Ensure Snowflake GCS storage integration exists and bucket IAM is configured."""
    from utils.gcp_utils import ensure_bucket_iam_for_integration

    integration_existed = False
    try:
        with sf_engine.connect() as connection:
            result = connection.execute(
                text(f"SHOW STORAGE INTEGRATIONS LIKE '{DEMO_STORAGE}'")
            ).fetchall()
            if result:
                integration_existed = True
                logger.info(
                    "[SAP -> SF] Storage integration '%s' already exists", DEMO_STORAGE
                )
            else:
                connection.execute(
                    text(
                        f"""
                    CREATE STORAGE INTEGRATION {DEMO_STORAGE}
                    TYPE = EXTERNAL_STAGE
                    STORAGE_PROVIDER = 'GCS'
                    ENABLED = TRUE
                    STORAGE_ALLOWED_LOCATIONS = ('{GCS_BUCKET_URL}');
                """
                    )
                )
                connection.commit()
                logger.info(
                    "[SAP -> SF] Created storage integration '%s'", DEMO_STORAGE
                )
    except Exception as exc:
        logger.error(
            "[SAP -> SF] Error creating storage integration for conn=%s: %s",
            connection_id,
            exc,
        )
        raise

    if integration_existed:
        return

    try:
        with sf_engine.connect() as connection:
            rows = connection.execute(
                text(f"DESC INTEGRATION {DEMO_STORAGE}")
            ).fetchall()
        service_account = next(
            (row[2] for row in rows if row[0] == "STORAGE_GCP_SERVICE_ACCOUNT"),
            None,
        )
        if service_account:
            ensure_bucket_iam_for_integration(
                storage.Client(), BUCKET_NAME, service_account
            )
    except Exception as exc:
        logger.error(
            "[SAP -> SF] Error configuring GCS IAM for conn=%s: %s",
            connection_id,
            exc,
        )
        raise


def _handle_copy_errors(
    engine: Engine, schema: str, staging: str, target: str, errors: List[Any]
) -> None:
    norm_s = _sf_ident(staging)
    norm_t = _sf_ident(target)
    seen: set = set()
    for err in errors:
        s = str(err)
        if "character length limit" not in s.lower() and "too long" not in s.lower():
            continue
        m = re.search(r'\["([^"]+)":\d+\]', s)
        if not m:
            continue
        col = m.group(1)
        if col in seen:
            continue
        seen.add(col)
        for tbl in [norm_s, norm_t]:
            try:
                with engine.connect() as c:
                    row = c.execute(
                        text(
                            "SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.columns "
                            "WHERE table_schema=:s AND table_name=:t AND UPPER(column_name)=UPPER(:c)"
                        ),
                        {"s": schema.upper(), "t": tbl.upper(), "c": col},
                    ).fetchone()
                    cur = (row[0] or 255) if row else 255
                    new = ((cur // 512) + 1) * 512
                with engine.begin() as c2:
                    c2.execute(
                        text(
                            f'ALTER TABLE "{schema}"."{tbl}" MODIFY COLUMN "{col}" VARCHAR({new})'
                        )
                    )
                logger.info("[COPY] Expanded %s.%s -> VARCHAR(%d)", tbl, col, new)
            except Exception as e:
                logger.warning("[COPY] Expand failed %s.%s: %s", tbl, col, e)


def _copy_into_staging(
    engine: Engine,
    schema: str,
    staging: str,
    target: str,
    gcs_uri: str,
    column_order: List[str],
) -> int:
    """Two-phase COPY INTO with error validation and column auto-expansion."""
    norm_s = _sf_ident(staging)
    col_list = ", ".join(f'"{c}"' for c in column_order)
    val_sql = (
        f'COPY INTO "{schema}"."{norm_s}" ({col_list}) '
        f"FROM '{gcs_uri}' FILE_FORMAT = ({_FILE_FMT}) "
        f"STORAGE_INTEGRATION = {DEMO_STORAGE} VALIDATION_MODE = RETURN_ERRORS"
    )
    fin_sql = (
        f'COPY INTO "{schema}"."{norm_s}" ({col_list}) '
        f"FROM '{gcs_uri}' FILE_FORMAT = ({_FILE_FMT}) "
        f"STORAGE_INTEGRATION = {DEMO_STORAGE} ON_ERROR = ABORT_STATEMENT PURGE = FALSE"
    )
    with engine.connect() as c:
        try:
            errs = c.execute(text(val_sql)).fetchall()
            if errs:
                _handle_copy_errors(engine, schema, staging, target, errs)
        except Exception as e:
            logger.warning("[COPY] Validation phase error (non-fatal): %s", e)
    rows = 0
    with engine.begin() as c:
        for row in c.execute(text(fin_sql)).fetchall():
            try:
                rows += int(row[1])
            except Exception:
                pass
    logger.info("[COPY] Loaded %d rows into '%s'", rows, norm_s)
    return rows


# ---------------------------------------------------------------------------
# Target table helpers
# ---------------------------------------------------------------------------


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
        r = c.execute(
            text(
                f'INSERT INTO "{schema}"."{nt}" ({col_list}) '
                f'SELECT {col_list} FROM "{schema}"."{ns}"'
            )
        )
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
            r = c.execute(
                text(
                    f'INSERT INTO "{schema}"."{nt}" ({col_list}) '
                    f'SELECT {col_list} FROM "{schema}"."{ns}"'
                )
            )
            return r.rowcount
    join = " AND ".join(f't."{k}"=s."{k}"' for k in pks)
    audit_cols = {"_FIVETRAN_SYNCED", "_FIVETRAN_DELETED", "_SPECT_DELETED"}
    upd_cols = [c for c in all_cols if c not in audit_cols]
    cl = ", ".join(f'"{c}"' for c in all_cols)
    vl = ", ".join(f's."{c}"' for c in all_cols)
    if upd_cols:
        upd = ", ".join(f't."{c}"=s."{c}"' for c in upd_cols)
        sql = f'MERGE INTO "{schema}"."{nt}" t USING "{schema}"."{ns}" s ON {join} WHEN MATCHED THEN UPDATE SET {upd} WHEN NOT MATCHED THEN INSERT ({cl}) VALUES ({vl})'
    else:
        sql = f'MERGE INTO "{schema}"."{nt}" t USING "{schema}"."{ns}" s ON {join} WHEN NOT MATCHED THEN INSERT ({cl}) VALUES ({vl})'
    with engine.begin() as c:
        return c.execute(text(sql)).rowcount


# ---------------------------------------------------------------------------
# DataFrame preparation
# ---------------------------------------------------------------------------


def _prep_df(
    df: pd.DataFrame,
    is_updated: str,
    is_deleted: str,
    ts: datetime,
    is_initial: bool = False,
) -> pd.DataFrame:
    df = df.copy()
    # Use the same TIMESTAMP_TZ(9) value format as the D365 connector:
    #   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%fZ")
    # Trailing "Z" is Snowflake's accepted UTC marker for TIMESTAMP_TZ.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)  # treat naïve ts as UTC
    df[is_updated] = ts.strftime("%Y-%m-%d %H:%M:%S.%fZ")

    # _SPECT_DELETED logic:
    # - Initial load: ALL rows come from a snapshot; none are deleted. Set to False.
    # - Delta load: _transform_odata_page() sets _SPECT_DELETED=True when
    #   ODQ_CHANGEMODE != "U" (i.e. __operation_type == "X"). Preserve that signal.
    _SPECT_DELETED_COL = "_SPECT_DELETED"
    if is_initial:
        df[is_deleted] = False
        if _SPECT_DELETED_COL in df.columns:
            df[_SPECT_DELETED_COL] = False
    elif _SPECT_DELETED_COL in df.columns:
        val = df[_SPECT_DELETED_COL].fillna(False).astype(bool)
        df[_SPECT_DELETED_COL] = val
        df[is_deleted] = val
    else:
        df[is_deleted] = False

    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].where(df[col].notna(), None)
    return df


# ---------------------------------------------------------------------------
# Delta page fetcher
# ---------------------------------------------------------------------------


def _record_table_failure(
    req: SAPODataSnowflakeMigrationRequest,
    table_name: str,
    error_msg: str,
    db_conn: Any,
    function_name: str = "sap_odata_to_snowflake_connector",
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
            "[SAP -> SF] Failed to persist error log for %s: %s", table_name, log_err
        )


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


def _fetch_initial_df(
    server_address: str,
    service_name: str,
    table_name: str,
    username: str,
    password: str,
    chunk_count: int,
    row_filter_config: Optional[Any],
    now_utc: datetime,
    filter_expr: Optional[str] = None,
    delta_name: Optional[str] = None,
    source_config: Optional[Dict[str, Any]] = None,
    select_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Fetch all SAP OData initial pages. Stores next_delta_link in df.attrs."""
    from urllib.parse import quote

    import requests

    initial_url = (
        f"https://{server_address}/sap/opu/odata/sap/{service_name}/{table_name}/?"
    )
    if filter_expr:
        encoded_filter = quote(filter_expr, safe="'()")
        initial_url += f"$filter={encoded_filter}&"
    initial_url += "$format=json"
    start = force_https_url(_odata_url_with_select(initial_url, select_columns))

    page_dfs: List[pd.DataFrame] = []
    next_link: Optional[str] = None
    with requests.Session() as sess:
        sess.auth = (username, password)
        sess.headers.update(
            {"Prefer": f"odata.track-changes,odata.maxpagesize={chunk_count}"}
        )
        sess.verify = False
        current = start
        page_num = 0
        while current:
            page_num += 1
            if page_num > SAP_MAX_PAGES:
                break
            resp = sess.get(current, timeout=120)
            if resp.status_code != 200:
                raise ValueError(
                    f"SAP initial HTTP {resp.status_code}: {resp.text[:400]}"
                )
            data = resp.json()
            d_block = data.get("d", {})
            records = d_block.get("results", []) if isinstance(d_block, dict) else []
            nxt = d_block.get("__next") if isinstance(d_block, dict) else None
            if nxt:
                current = force_https_url(nxt, server_address, service_name)
            else:
                next_link = _extract_delta_link(data)
                current = None
            if records:
                page_df = _transform_odata_page(
                    records=records,
                    mode="initial",
                    time_now=now_utc,
                    delta_pointer="initial",
                )
                if row_filter_config:
                    page_df = _apply_row_filter_to_page(page_df, row_filter_config)
                page_dfs.append(page_df)

    if not next_link and delta_name:
        resolved = _fetch_delta_link_via_entity_set(
            server_address=server_address,
            service_name=service_name,
            delta_name=delta_name,
            username=username,
            password=password,
            source_config_or_data=source_config,
        )
        if resolved:
            next_link = resolved
            logger.info(
                "[SAP -> SF] Resolved delta token via %s for %s",
                delta_name,
                table_name,
            )
        else:
            logger.warning(
                "[SAP -> SF] No delta token from response or %s for %s",
                delta_name,
                table_name,
            )

    if not page_dfs:
        empty = pd.DataFrame()
        empty.attrs["next_delta_link"] = next_link
        return empty
    combined = pd.concat(page_dfs, ignore_index=True)
    combined.attrs["next_delta_link"] = next_link
    return combined


def _fetch_delta_df(
    server_address: str,
    service_name: str,
    table_name: str,
    delta_token_url: str,
    username: str,
    password: str,
    chunk_count: int,
    row_filter_config: Optional[Any],
    now_utc: datetime,
    filter_expr: Optional[str] = None,
    select_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Fetch all SAP ODQ delta pages. Stores next_delta_link in df.attrs."""
    import requests

    pointer = extract_token_from_delta_link(delta_token_url)
    start = _odata_url_with_select(
        build_sap_delta_start_url(
            delta_token_url,
            server_address=server_address,
            service_name=service_name,
            entity_name=table_name,
            filter_expr=filter_expr,
        ),
        select_columns,
    )
    logger.info("[SAP -> SF] Delta start URL for %s: %s", table_name, start)

    page_dfs: List[pd.DataFrame] = []
    next_link: Optional[str] = None
    with requests.Session() as sess:
        sess.auth = (username, password)
        sess.headers.update(
            {
                "Accept": "application/json",
                "Prefer": f"odata.track-changes,odata.maxpagesize={chunk_count}",
            }
        )
        sess.verify = False
        current = start
        pn = 0
        while current:
            pn += 1
            if pn > SAP_MAX_PAGES:
                break
            resp = sess.get(current, timeout=SAP_REQUEST_TIMEOUT)
            if resp.status_code != 200:
                raise ValueError(
                    f"SAP delta HTTP {resp.status_code}: {resp.text[:400]}"
                )
            data = resp.json()
            d_block = data.get("d", {})
            records: List[Any] = []
            next_page_url: Optional[str] = None

            if isinstance(d_block, dict) and "ChangesAfter" in d_block:
                deferred_uri = d_block["ChangesAfter"].get("__deferred", {}).get("uri")
                if deferred_uri:
                    next_page_url = force_https_url(
                        deferred_uri, server_address, service_name
                    )
                    if "$format=json" not in next_page_url.lower():
                        next_page_url += (
                            "&$format=json" if "?" in next_page_url else "?$format=json"
                        )
            else:
                records = (
                    d_block.get("results", []) if isinstance(d_block, dict) else []
                )
                next_page_url = (
                    d_block.get("__next") if isinstance(d_block, dict) else None
                )
                if not next_page_url and not next_link:
                    next_link = _extract_delta_link(data)

            if records:
                pf = _transform_odata_page(
                    records=records,
                    mode="delta",
                    time_now=now_utc,
                    delta_pointer=pointer,
                )
                if row_filter_config:
                    pf = _apply_row_filter_to_page(pf, row_filter_config)
                page_dfs.append(pf)

            current = (
                force_https_url(next_page_url, server_address, service_name)
                if next_page_url
                else None
            )

    if not page_dfs:
        empty = pd.DataFrame()
        empty.attrs["next_delta_link"] = next_link
        return empty
    combined = pd.concat(page_dfs, ignore_index=True)
    combined.attrs["next_delta_link"] = next_link
    return combined


# ---------------------------------------------------------------------------
# Single table migration worker
# ---------------------------------------------------------------------------


def _migrate_single_table_snowflake(
    req: SAPODataSnowflakeMigrationRequest,
    table_name: str,
    db_engine: Engine,
    sf_engine: Engine,
    run_id: str,
    failure_tracker: MigrationFailureTracker,
) -> Dict[str, Any]:
    """Extract one SAP entity-set, load into Snowflake via GCS COPY INTO."""
    t_start = time.time()
    now_utc = datetime.now(timezone.utc)
    schema = (req.destination_config.get("schema") or "PUBLIC").upper()
    transform_state = get_active_entity_transform(
        req.connection_id, table_name, existing_conn=db_engine
    )
    dest_from_transform = (transform_state or {}).get("destination_table_name")
    if dest_from_transform and is_valid_snowflake_destination_name(dest_from_transform):
        target_table = dest_from_transform.strip().upper()
    else:
        target_table = _sf_ident(table_name)
    staging_table = f"STG_{target_table}"

    # Row filter settings
    settings = (req.table_export_settings or {}).get(table_name, {})
    rf_config = build_row_filter_expression(settings.get("row_filter"))
    is_updated = req.is_updated or "_FIVETRAN_SYNCED"
    is_deleted = req.is_deleted or "_FIVETRAN_DELETED"

    # Source connection details
    sc = req.source_config or {}
    username = sc.get("username", "")
    password = sc.get("password", "")
    use_proxy = is_proxy_connection(sc)

    gcs_uri: Optional[str] = None
    blob_name: str = f"temp_data_sap_{target_table}_{req.connection_id}_{run_id}.csv"

    # Check ETL state (service_name is stored per table during Update Schema)
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

    # An initial-only table is intentionally processed once.  A subsequent
    # execution is a successful no-op, not an error or a new load type.
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
        "[SAP -> SF] Table %s | Destination: %s | Mode: %s | Service: %s | Host: %s",
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

    try:
        if not server_addr:
            raise ValueError(
                f"Missing SAP host/port in source config for table {table_name}"
            )
        if not service_name:
            raise ValueError(f"Missing SAP service_name for table {table_name}")

        # 1. Fetch metadata & prepare DDL
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
            and _sf_ident(previous_dest) != target_table
            and rename_mode
        ):
            logger.info(
                "[SAP -> SF] Destination rename choice=%s | %s -> %s | entity=%s",
                rename_mode,
                previous_dest,
                target_table,
                table_name,
            )
            if rename_mode == "rename_existing":
                if _snowflake_table_exists(sf_engine, schema, previous_dest):
                    _rename_snowflake_table(
                        sf_engine, schema, previous_dest, target_table
                    )
            elif rename_mode == "create_new":
                # Safer default: leave the old table untouched and full-reload
                # into the new destination. Watermarks follow the entity, so
                # reset them only for this full-reload choice.
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
                    "[SAP -> SF] Full reload into new table %s; old table %s kept",
                    target_table,
                    previous_dest,
                )
            _clear_destination_rename_mode(
                req.connection_id, table_name, db_engine
            )

        if req.drop_and_reload:
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

            _update_table_etl_state(
                connection_id=req.connection_id,
                table_name=table_name,
                initial_completed_flag=False,
                delta_token=None,
                existing_conn=db_engine,
            )
            etl_state["initial_completed_flag"] = False
            delta_url = None
            logger.info(
                "[SAP -> SF] Target is truncated; resetting initial state for %s",
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
            target_meta = build_target_schema_metadata(resolved_mapping, meta)
            if target_meta.get("columns"):
                ddl_meta = target_meta
                logger.info(
                    "[SAP -> SF] Destination %s uses %d mapped target columns",
                    target_table,
                    len(target_meta["columns"]),
                )
        ensure_snowflake_table_exists(
            sf_engine, schema, target_table, ddl_meta, pks, is_updated, is_deleted
        )
        _ensure_staging_table(sf_engine, schema, target_table, staging_table)
        snowflake_columns = _get_snowflake_column_order(
            sf_engine, schema, staging_table
        )
        if not snowflake_columns:
            raise ValueError(
                f"No Snowflake columns found for staging table {staging_table}"
            )

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
                    "[SAP -> SF] %d extract columns exceed OData $select limit (%d); "
                    "fetching full entity without $select",
                    len(select_columns),
                    batch_cap,
                )
                select_columns = None
            logger.info(
                "[SAP -> SF] Transform v%s active for %s (%d extract columns)",
                transform_version,
                table_name,
                len(resolved.get("extract_columns") or []),
            )

        if use_proxy:
            raise ValueError(
                "SAP OData proxy mode is not yet supported for Snowflake destination"
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
                        "[SAP -> SF] Delta pull failed for %s, falling back to initial load: %s",
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
        logger.info("[SAP -> SF] Extracted %d rows for %s", rows_extracted, table_name)

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
            # 2. Add audit metadata columns and align to Snowflake staging schema
            df = _prep_df(df, is_updated, is_deleted, now_utc, is_initial=is_initial)
            df = _align_df_to_snowflake_columns(df, snowflake_columns)
            logger.info(
                "[SAP -> SF] Aligned %s to %d Snowflake columns (extract had %d populated cols)",
                table_name,
                len(snowflake_columns),
                df.notna().any(axis=0).sum(),
            )

            # 3. Upload CSV to GCS
            gcs_uri = _upload_df_to_gcs(df, target_table, req.connection_id, run_id)
            if not gcs_uri:
                raise RuntimeError(
                    f"Failed to upload DataFrame for {table_name} to GCS"
                )

            # 4. Truncate staging & COPY INTO staging table
            with sf_engine.begin() as c:
                c.execute(
                    text(f'TRUNCATE TABLE "{schema}"."{_sf_ident(staging_table)}"')
                )
            _copy_into_staging(
                sf_engine,
                schema,
                staging_table,
                target_table,
                gcs_uri,
                snowflake_columns,
            )

            # 5. Insert overwrite (initial) or Merge (delta)
            if is_initial:
                rows_loaded = _insert_overwrite(
                    sf_engine, schema, staging_table, target_table, snowflake_columns
                )
            else:
                rows_loaded = _merge_into(
                    sf_engine,
                    schema,
                    staging_table,
                    target_table,
                    pks,
                    snowflake_columns,
                )

        # 6. Cleanup GCS and drop staging table
        # DROP (not just TRUNCATE) so the STG_* table does not remain visible
        # in Snowflake after migration completes.  The next run recreates it
        # via CREATE OR REPLACE ... LIKE, so the schema is always fresh.
        _delete_gcs_blob(blob_name)
        with sf_engine.begin() as c:
            c.execute(
                text(
                    f'DROP TABLE IF EXISTS "{schema}"."{_sf_ident(staging_table)}"'
                )
            )

        # 7. Persist ETL state (delta token)
        if next_delta_link or is_initial:
            _update_table_etl_state(
                connection_id=req.connection_id,
                table_name=table_name,
                initial_completed_flag=True,
                delta_token=next_delta_link or delta_url,
                existing_conn=db_engine,
            )

        duration = time.time() - t_start

        # 8. Record transfer log in Postgres
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
                    "[SAP -> SF] Failed to record transform_version for %s: %s",
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
        _delete_gcs_blob(blob_name)
        # Best-effort drop of staging table so it doesn't linger after a failure.
        try:
            with sf_engine.begin() as c:
                c.execute(
                    text(
                        f'DROP TABLE IF EXISTS "{schema}"."{_sf_ident(staging_table)}"'
                    )
                )
        except Exception as drop_err:
            logger.warning(
                "[SAP -> SF] Failed to drop staging table %s after error: %s",
                staging_table,
                drop_err,
            )
        err_msg = str(
            getattr(exc, "detail", exc) if isinstance(exc, HTTPException) else exc
        )
        logger.error(
            "[SAP -> SF] Table %s failed: %s\n%s",
            table_name,
            err_msg,
            tb_module.format_exc(),
        )
        failure_tracker.add_failure(table_name, err_msg, "SAP OData to Snowflake")
        _record_table_failure(req, table_name, err_msg, db_engine)
        return {
            "table_name": table_name,
            "status": "failed",
            "error": err_msg,
            "rows_loaded": 0,
            "duration": round(time.time() - t_start, 2),
            "load_type": load_type,
        }


# ---------------------------------------------------------------------------
# Proxy batch (agent-side SAP + Snowflake load)
# ---------------------------------------------------------------------------


def _sap_snowflake_proxy_batch_load(req: SAPODataSnowflakeMigrationRequest) -> Dict[str, Any]:
    """Fire StartDataLoad on the hub for all tables; pipeline polls Django progress."""
    started_at = datetime.now(timezone.utc)
    connection_id = req.connection_id
    migration_session_id = req.migration_session_id
    job_name = req.job_name or f"SAPOData-Snowflake Migration {connection_id}"
    celery_child_mode = _resolve_celery_child_mode(req)

    source_config = dict(req.source_config or {})
    source_config["destination_type"] = "snowflake"
    destination_config = dict(req.destination_config or {})

    results: Dict[str, Any] = {
        "status": "in_progress",
        "status_code": 200,
        "job_name": job_name,
        "connection_id": connection_id,
        "migration_session_id": migration_session_id,
        "started_at": started_at.isoformat(),
        "completed_at": started_at.isoformat(),
        "tables": {},
        "total_tables": len(req.selected_tables or []),
        "successful_tables": 0,
        "failed_tables": 0,
        "total_rows": 0,
        "service_name": "SAPOData-Snowflake",
        "message": "Proxy agent load accepted; awaiting progress events.",
    }

    if not celery_child_mode:
        update_migration_status_log(
            job_name=job_name,
            message="Migration in progress",
            status="I",
            session=migration_session_id,
            log_id=req.log_id,
        )

    table_payloads: List[Dict[str, Any]] = []
    pagination_default = int(req.chunk_count or 30)

    for tbl in req.selected_tables or []:
        tbl_state = _get_table_etl_state(connection_id, tbl)
        tbl_settings = (req.table_export_settings or {}).get(tbl) or {}

        tbl_load_method = (
            tbl_state["load_method"]
            if tbl_state["load_method_locked"]
            else (tbl_settings.get("load_method") or tbl_state["load_method"] or "initial")
        )
        tbl_load_method = str(tbl_load_method).strip().lower()
        stored_token = tbl_state.get("delta_token")

        if tbl_load_method in ("delta", "initial_delta"):
            is_first_run = (
                not tbl_state["initial_completed_flag"]
                or tbl_state.get("delete_and_load")
                or req.drop_and_reload
                or not stored_token
            )
        else:
            is_first_run = (
                not tbl_state["initial_completed_flag"]
                or tbl_state.get("delete_and_load")
                or req.drop_and_reload
            )

        if tbl_load_method == "initial" and not is_first_run:
            logger.info(
                "[SAP_SF_PROXY] table=%s initial load already completed; skipping.",
                tbl,
            )
            continue

        effective_load_method = tbl_load_method if is_first_run else "delta"
        filter_expr = tbl_settings.get("filter_expr") or tbl_state.get("row_filter_config")
        if isinstance(filter_expr, dict):
            filter_expr = None

        transform_state = get_active_entity_transform(connection_id, tbl)

        table_payloads.append(
            {
                "snowflake_fields": {
                    "service_name": tbl_state.get("service_name")
                    or source_config.get("service_name")
                    or "ZGW_ODATA_SRV",
                    "entity_name": tbl,
                    "load_method": effective_load_method,
                    "initial_completed_flag": bool(tbl_state.get("initial_completed_flag")),
                    "drop_and_reload": bool(req.drop_and_reload or tbl_state.get("delete_and_load")),
                    "pagination_count": pagination_default,
                    "filter_expr": filter_expr,
                    "delta_name": tbl_state.get("delta_name"),
                    "delta_token": stored_token,
                    "is_updated": req.is_updated or "_FIVETRAN_SYNCED",
                    "is_deleted": req.is_deleted or "_FIVETRAN_DELETED",
                    "transform_state": transform_state,
                }
            }
        )

    if table_payloads:
        logger.info(
            "[SAP_SF_PROXY] Sending %d table(s) via StartDataLoad (Snowflake).",
            len(table_payloads),
        )
        run_proxy_batch_load(
            source_config=source_config,
            destination_config=destination_config,
            table_payloads=table_payloads,
            use_delta_rpc=False,
            job_name=job_name,
            migration_session_id=migration_session_id,
            connection_id=connection_id,
            cmp_id=req.cmp_id,
            user_id=req.user_id,
            log_id=req.log_id,
        )

    return results


# ---------------------------------------------------------------------------
# Master Orchestrator
# ---------------------------------------------------------------------------


def _build_connector_response(
    req: SAPODataSnowflakeMigrationRequest,
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
        "[SAP -> SF] Completed in %ss. ok=%d failed=%d total_rows=%d",
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


def sap_odata_to_snowflake_connector(
    req: SAPODataSnowflakeMigrationRequest,
) -> Dict[str, Any]:
    """Master orchestrator for SAP OData to Snowflake replication."""
    reset_timing_records()
    if is_proxy_connection(req.source_config or {}):
        return _sap_snowflake_proxy_batch_load(req)

    t0 = time.time()
    db_engine = postgres_connection()
    failure_tracker = MigrationFailureTracker()
    results: List[Dict[str, Any]] = []
    celery_child_mode = _resolve_celery_child_mode(req)
    execution_order = req.execution_order
    if celery_child_mode and execution_order == "parallel":
        execution_order = "sequential"

    try:
        sf_engine = _get_snowflake_engine(req.destination_config)
        _ensure_gcs_integration(sf_engine, req.connection_id, req.cmp_id)

        max_workers = (
            min(len(req.selected_tables), 4) if execution_order == "parallel" else 1
        )
        logger.info(
            "[SAP -> SF] Starting migration of %d tables (workers=%d, celery_child=%s)",
            len(req.selected_tables),
            max_workers,
            celery_child_mode,
        )

        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _migrate_single_table_snowflake,
                        req,
                        tbl,
                        db_engine,
                        sf_engine,
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
                    _migrate_single_table_snowflake(
                        req,
                        tbl,
                        db_engine,
                        sf_engine,
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
            "[SAP -> SF] Orchestrator failed: %s\n%s", err_msg, tb_module.format_exc()
        )
        for tbl in req.selected_tables:
            if not any(r.get("table_name") == tbl for r in results):
                failure_tracker.add_failure(tbl, err_msg, "SAP OData to Snowflake")
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
