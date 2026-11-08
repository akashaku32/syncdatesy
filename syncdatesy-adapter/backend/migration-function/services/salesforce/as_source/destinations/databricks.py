"""
Salesforce → Databricks load helpers.

Maps Salesforce describe types to Delta DDL, transforms Bulk API chunks to
DataFrames, and provides GCS/volume blob naming that does not collide with
Dynamics staging objects.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from dateutil import parser as date_parser

from utils.snowflake_utils import normalize_name

logger = logging.getLogger(__name__)

SALESFORCE_TO_DATABRICKS_TYPE: Dict[str, str] = {
    "id": "STRING",
    "string": "STRING",
    "textarea": "STRING",
    "longtextarea": "STRING",
    "richtextarea": "STRING",
    "email": "STRING",
    "url": "STRING",
    "phone": "STRING",
    "combopicklist": "STRING",
    "combobox": "STRING",
    "picklist": "STRING",
    "multipicklist": "STRING",
    "reference": "STRING",
    "masterrecord": "STRING",
    "datacategorygroupreference": "STRING",
    "boolean": "BOOLEAN",
    "byte": "BIGINT",
    "int": "BIGINT",
    "integer": "BIGINT",
    "double": "DOUBLE",
    "currency": "DECIMAL(38,6)",
    "percent": "DOUBLE",
    "calculated": "STRING",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "time": "STRING",
    "base64": "STRING",
    "encryptedstring": "STRING",
    "location": "STRING",
    "address": "STRING",
    "complexvalue": "STRING",
    "anytype": "STRING",
    "junctionidlist": "STRING",
}

# Unix-epoch day integers (Databricks DATE_FROM_UNIX_DATE) — ~1970-01-01 .. ~2106-10-02.
_UNIX_DAY_MIN = 0
_UNIX_DAY_MAX = 50_000


def salesforce_staging_blob_name(
    table_name: str, connection_id: int, *, parquet: bool = False
) -> str:
    """GCS/volume object name isolated from Dynamics Dynamics F&O blobs."""
    ext = "parquet" if parquet else "csv"
    return f"temp_data_salesforce_{table_name}_{connection_id}.{ext}"


def map_salesforce_type_to_databricks(sf_type: str) -> str:
    key = str(sf_type or "string").strip().lower()
    return SALESFORCE_TO_DATABRICKS_TYPE.get(key, "STRING")


def map_salesforce_field_to_databricks(meta: dict) -> str:
    """Map one Salesforce describe field to a Databricks SQL type."""
    sf_type = str((meta or {}).get("type", "string")).strip().lower()
    base = map_salesforce_type_to_databricks(sf_type)
    precision = (meta or {}).get("precision")
    scale = (meta or {}).get("scale")
    if base.startswith("DECIMAL") and precision is not None and scale is not None:
        try:
            return f"DECIMAL({int(precision)},{int(scale)})"
        except (TypeError, ValueError):
            return base
    if base == "DOUBLE" and precision is not None and scale is not None:
        try:
            if int(scale) > 0:
                return f"DECIMAL({int(precision)},{int(scale)})"
        except (TypeError, ValueError):
            pass
    return base


def _unix_day_to_iso(day: int) -> Optional[str]:
    if not (_UNIX_DAY_MIN <= day <= _UNIX_DAY_MAX):
        return None
    try:
        return (date(1970, 1, 1) + timedelta(days=day)).strftime("%Y-%m-%d")
    except (OverflowError, ValueError):
        return None


def _coerce_salesforce_date_value(value: Any) -> Optional[str]:
    """Normalize Salesforce date values to YYYY-MM-DD strings for staging."""
    if value is None or value == "":
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _unix_day_to_iso(int(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            iso = _unix_day_to_iso(int(s))
            if iso:
                return iso
        try:
            return date_parser.parse(s).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OverflowError):
            return None
    return None


def _coerce_salesforce_timestamp_value(value: Any) -> Optional[str]:
    """Normalize Salesforce datetime values to UTC TIMESTAMP strings."""
    if value is None or value == "":
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        iv = int(value)
        if iv > 10_000_000_000:  # epoch millis
            dt = datetime.fromtimestamp(iv / 1000.0, tz=timezone.utc)
        elif iv > 1_000_000_000:  # epoch seconds
            dt = datetime.fromtimestamp(iv, tz=timezone.utc)
        else:
            iso = _unix_day_to_iso(iv)
            if not iso:
                return None
            dt = datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            parsed = date_parser.parse(s)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        except (ValueError, TypeError, OverflowError):
            return None
    return None


def _coerce_salesforce_boolean_value(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(int(value))
    return str(value).strip().lower() in ("true", "1", "yes", "t")


def _coerce_salesforce_numeric_value(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_salesforce_string_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def build_attributes_details_field(
    describe_fields: List[dict],
    fields: List[str],
    *,
    is_updated: str = "_FIVETRAN_SYNCED",
    is_deleted: str = "_FIVETRAN_DELETED",
) -> Dict[str, str]:
    """Build {NORMALIZED_COL: DatabricksType} for copy_function / base DDL."""
    wanted = {normalize_name(f) for f in fields}
    meta_by_norm = {
        normalize_name(f["name"]): f for f in (describe_fields or []) if f.get("name")
    }
    out: Dict[str, str] = {}
    for col in wanted:
        meta = meta_by_norm.get(col, {})
        out[col] = map_salesforce_field_to_databricks(meta)
    updated = normalize_name(is_updated)
    deleted = normalize_name(is_deleted)
    out.setdefault(updated, "TIMESTAMP")
    out.setdefault(deleted, "BOOLEAN")
    out.setdefault("ID", "STRING")
    return out


def ensure_databricks_table_from_salesforce_describe(
    destination_engine,
    table_name: str,
    schema: str,
    describe_fields: List[dict],
    fields: List[str],
    connection_id: int,
    cmp_id: int,
    *,
    is_updated: str = "_FIVETRAN_SYNCED",
    is_deleted: str = "_FIVETRAN_DELETED",
    catalog: Optional[str] = None,
) -> Dict[str, str]:
    """Ensure Delta base table exists from Salesforce describe metadata."""
    from services.databricks.load_helpers import ensure_databricks_base_table_exists

    attributes = build_attributes_details_field(
        describe_fields,
        fields,
        is_updated=is_updated,
        is_deleted=is_deleted,
    )
    empty = pd.DataFrame(columns=list(attributes.keys()))
    ensure_databricks_base_table_exists(
        destination_engine,
        normalize_name(table_name),
        schema,
        empty,
        attributes,
        connection_id,
        cmp_id,
        destination_connection={"schema": schema, "catalog": catalog},
    )
    return attributes


def _filter_is_deleted_rows(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("IS_DELETED", "ISDELETED"):
        if col in df.columns:
            mask = df[col].astype(str).str.lower().isin(["true", "1", "yes"])
            dropped = int(mask.sum())
            if dropped:
                logger.info("Initial load: excluded %s IsDeleted=true row(s)", dropped)
            return df.loc[~mask].copy()
    return df


def transform_salesforce_chunk_to_dataframe(
    chunk: List[Dict[str, Any]],
    fields: List[str],
    column_types: Dict[str, str],
    *,
    is_updated: str = "_FIVETRAN_SYNCED",
    is_deleted: str = "_FIVETRAN_DELETED",
    exclude_deleted_rows: bool = False,
) -> pd.DataFrame:
    """Convert a Bulk API result chunk into an uppercase-column DataFrame for COPY INTO."""
    if not chunk:
        return pd.DataFrame()

    df = pd.DataFrame(chunk)
    df.columns = [normalize_name(c) for c in df.columns]

    if exclude_deleted_rows:
        df = _filter_is_deleted_rows(df)
        if df.empty:
            return df

    updated_col = normalize_name(is_updated)
    deleted_col = normalize_name(is_deleted)
    df[updated_col] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if "IS_DELETED" in df.columns:
        df[deleted_col] = (
            df["IS_DELETED"].astype(str).str.lower().isin(["true", "1", "yes"])
        )
    else:
        df[deleted_col] = False

    normalized_fields = [normalize_name(f) for f in fields]
    for col in (updated_col, deleted_col, "ID"):
        if col not in normalized_fields:
            normalized_fields.append(col)
    df = df[[c for c in normalized_fields if c in df.columns]]

    type_map = {normalize_name(k): str(v).upper() for k, v in (column_types or {}).items()}
    for col in list(df.columns):
        dtype = type_map.get(col, "STRING")
        try:
            if dtype == "DATE":
                df[col] = df[col].map(_coerce_salesforce_date_value)
            elif dtype.startswith("TIMESTAMP"):
                df[col] = df[col].map(_coerce_salesforce_timestamp_value)
            elif dtype == "BOOLEAN":
                df[col] = df[col].map(_coerce_salesforce_boolean_value)
            elif dtype in ("BIGINT", "INT", "INTEGER"):
                df[col] = df[col].map(_coerce_salesforce_numeric_value)
            elif dtype == "DOUBLE" or dtype.startswith("DECIMAL"):
                df[col] = df[col].map(_coerce_salesforce_numeric_value)
            else:
                df[col] = df[col].map(_coerce_salesforce_string_value)
        except Exception as exc:
            logger.warning("SF→DBX transform col=%s dtype=%s err=%s", col, dtype, exc)
            df[col] = df[col].map(_coerce_salesforce_string_value)

    df = df.where(pd.notnull(df), None)
    return df


def prepare_dataframe_for_databricks_parquet(
    df: pd.DataFrame,
    column_types: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    Normalize dtypes before Parquet upload so Spark/Databricks COPY INTO does not
    see ambiguous INT/DATE32 columns for Salesforce date fields.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()

    out = df.copy()
    type_map = {normalize_name(k): str(v).upper() for k, v in (column_types or {}).items()}
    for col in out.columns:
        key = normalize_name(col)
        dtype = type_map.get(key, "STRING")
        if dtype == "DATE":
            out[col] = out[col].map(_coerce_salesforce_date_value)
            out[col] = out[col].astype("object")
        elif dtype.startswith("TIMESTAMP"):
            out[col] = out[col].map(_coerce_salesforce_timestamp_value)
            out[col] = out[col].astype("object")
        elif dtype == "BOOLEAN":
            out[col] = out[col].map(_coerce_salesforce_boolean_value)
        elif dtype in ("BIGINT", "INT", "INTEGER", "DOUBLE") or dtype.startswith("DECIMAL"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = out[col].map(_coerce_salesforce_string_value)
            out[col] = out[col].astype("object")
    return out


def upload_salesforce_dataframe_to_bucket(
    df: pd.DataFrame,
    table_name: str,
    connection_id: int,
    *,
    blob_name: Optional[str] = None,
    max_retries: int = 5,
) -> str:
    """Upload CSV to GCS using Salesforce-specific blob naming."""
    from google.api_core.exceptions import ServiceUnavailable
    from google.cloud import storage
    from services.dynamics.source_common import BUCKET_NAME

    bucket_name = BUCKET_NAME
    blob = blob_name or salesforce_staging_blob_name(table_name, connection_id, parquet=False)
    csv_data = df.to_csv(index=False, encoding="utf-8", quoting=csv.QUOTE_ALL, escapechar="\\")
    client = storage.Client()
    gcs_blob = client.bucket(bucket_name).blob(blob)
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            logger.warning(
                "SF_DBX|gcs_upload | table=%s | connection_id=%s | blob=%s | attempt=%s",
                table_name,
                connection_id,
                blob,
                attempt + 1,
            )
            gcs_blob.upload_from_string(csv_data, content_type="text/csv")
            return f"gcs://{bucket_name}/{blob}"
        except ServiceUnavailable as exc:
            last_err = exc
            time.sleep(2 ** attempt)
        except Exception as exc:
            last_err = exc
            break
    raise RuntimeError(f"Salesforce GCS upload failed for {table_name}: {last_err}")
