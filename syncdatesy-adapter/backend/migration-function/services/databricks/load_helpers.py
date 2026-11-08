"""
Databricks load helpers for D365 F&O → Databricks (and Salesforce → Databricks).

Per-chunk flow: stage CSV → COPY INTO staging → MERGE into base → TRUNCATE staging.
Checkpoints: source_table_selection.last_synced + migration_record.staging_records_count.
End-of-table: drop staging Delta table; safety-flush any orphan staging on resume.

Load engine (runtime, not destination form):

  Salesforce → always sql_warehouse.
  Dynamics (initial and incremental) → spark_job (bulk staging + one Spark MERGE).
  Override with destination_config.load_engine or DYNAMICS_DBX_LOAD_ENGINE=sql_warehouse.

Staging methods (auto-default managed_volume + ``datasyncher_stage`` volume):

  managed_volume (default for new connections)
    Upload chunk CSVs to a Unity Catalog managed volume via Files API
    (PAT/OAuth only — works on AWS / Azure / GCP Databricks). Connection needs
    catalog + schema; volume defaults to ``datasyncher_stage``.

  external_location (legacy GCP path — unchanged)
    Upload to Datasyncher-managed GCS, then COPY INTO via UC external location.
    Client enters gcs_storage_credential_name / gcs_external_location_name /
    gcs_service_account_email. See docs/databricks/databricks-gcp-unity-catalog-setup.md.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Sequence

import pandas as pd

logger = logging.getLogger(__name__)


def _normalize_merge_primary_keys(primary_key: Any) -> list[str]:
    """Accept a single PK string or a list/tuple of composite keys for MERGE/QUALIFY."""
    if primary_key is None:
        return ["ID"]
    if isinstance(primary_key, (list, tuple)):
        keys = [str(k).strip() for k in primary_key if k and str(k).strip()]
        return keys or ["ID"]
    key = str(primary_key).strip()
    return [key] if key else ["ID"]

STAGING_METHOD_MANAGED_VOLUME = "managed_volume"
STAGING_METHOD_EXTERNAL_LOCATION = "external_location"

# Spark staging access to Datasyncher cloud landing (independent of SQL staging_method).
SPARK_STAGING_ACCESS_EXTERNAL_LOCATION = "external_location"
SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL = "scoped_credential"

LOAD_ENGINE_SQL_WAREHOUSE = "sql_warehouse"
LOAD_ENGINE_SPARK_JOB = "spark_job"
# Default poll wait for Databricks Spark MERGE (bulk and per-folder submit_and_wait).
DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS = 36 * 3600

_SPARK_STAGING_ACCESS_CACHE_TTL_SECONDS = int(
    os.environ.get("DYNAMICS_DBX_SPARK_STAGING_ACCESS_CACHE_TTL_SECONDS", str(7 * 24 * 3600))
)
_SPARK_STAGING_ACCESS_PERMISSION_MARKERS = (
    "403",
    "permission_denied",
    "permission denied",
    "permissions",
    "unauthorized",
    "access denied",
    "forbidden",
    "account admin",
    "only the account admin",
    "uc_storage_credential",
    "uc_storage_credential_permission_denied",
    "does not have",
    "not authorized",
    "manual_mode",
    "storage credential",
    "external location",
    "create or update a storage credential",
)

SOURCE_TYPE_DYNAMICS = "dynamics"
SOURCE_TYPE_SALESFORCE = "salesforce"

DEFAULT_DATABRICKS_VOLUME_NAME = "datasyncher_stage"

SPARK_LANDING_CLOUD_AZURE = "azure"
SPARK_LANDING_CLOUD_GCS = "gcs"
SPARK_LANDING_CLOUD_S3 = "s3"

AZURE_SPARK_READ_MODE_DIRECT = "direct"
AZURE_SPARK_READ_MODE_STAGING = "staging"

AZURE_SPARK_LOAD_STRATEGY_BULK = "bulk"
AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER = "per_folder"

# table_name -> max SINK_MODIFIED_ON seen in successfully merged chunks
_WATERMARK_BY_TABLE: dict[str, datetime] = {}

# Serialize Unity Catalog setup per connection across parallel Celery workers/pods.
_EXTERNAL_LOCATION_ENSURED: dict[int, dict] = {}
_ADLS_EXTERNAL_LOCATION_ENSURED: dict[tuple[int, str], dict] = {}
_EXTERNAL_LOCATION_ENSURE_LOCK = threading.Lock()
_EXTERNAL_LOCATION_PG_LOCK_BASE = 880023
_ADLS_EXTERNAL_LOCATION_PG_LOCK_BASE = 880024

# Fivetran-aligned Delta props for durable base tables (not ephemeral staging).
# Deletion vectors help MERGE/soft-delete; zstd + checkpoint stats match common
# Databricks destination defaults. Row tracking intentionally omitted.
DELTA_BASE_TBLPROPERTIES_SQL = (
    "TBLPROPERTIES ("
    "'delta.enableDeletionVectors' = 'true', "
    "'delta.checkpoint.writeStatsAsJson' = 'false', "
    "'delta.checkpoint.writeStatsAsStruct' = 'true', "
    "'parquet.compression.codec' = 'zstd'"
    ")"
)

# Dynamics F&O / Synapse Link model.json ``dataType`` → Databricks Delta SQL type.
# Covers CDM lake export types plus common aliases (case-insensitive lookup).
# Unknown types default to STRING (same safe fallback as Dynamics→Snowflake).
field_dtype_conversion = {
    # Identity / text
    "guid": "STRING",
    "uuid": "STRING",
    "string": "STRING",
    "char": "STRING",
    "varchar": "STRING",
    "text": "STRING",
    "memo": "STRING",
    "json": "STRING",
    "xml": "STRING",
    "binary": "STRING",
    "varbinary": "STRING",
    "entity": "STRING",
    "reference": "STRING",
    # Temporal
    "datetime": "TIMESTAMP",
    "dateTime": "TIMESTAMP",
    "datetimeoffset": "TIMESTAMP",
    "dateTimeOffset": "TIMESTAMP",
    "timestamp": "TIMESTAMP",
    "date": "DATE",
    "time": "STRING",
    # Numeric
    "int64": "BIGINT",
    "long": "BIGINT",
    "bigint": "BIGINT",
    "int32": "INT",
    "int16": "INT",
    "int": "INT",
    "integer": "INT",
    "byte": "INT",
    "tinyint": "INT",
    "smallint": "INT",
    "decimal": "DECIMAL(38,6)",
    "numeric": "DECIMAL(38,6)",
    "money": "DECIMAL(38,6)",
    "currency": "DECIMAL(38,6)",
    "float": "DOUBLE",
    "double": "DOUBLE",
    "real": "DOUBLE",
    # Logical
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "bit": "BOOLEAN",
}


def get_databricks_type(field_type, max_length=None):
    """
    Map a Dynamics/CDM ``dataType`` string to a Databricks SQL type.

    All known lake-export types are covered. Unknown values fall back to
    STRING (with a warning) so a new CDM alias never hard-fails a load —
    matching Dynamics→Snowflake behavior.
    """
    key = str(field_type or "").strip()
    if not key:
        return "STRING"

    # Exact key first (preserves camelCase entries like dateTime)
    if key in field_dtype_conversion:
        return field_dtype_conversion[key]

    lower = key.lower()
    # Case-insensitive alias table (datetime, DATETIME, DateTime, …)
    lower_map = {str(k).lower(): v for k, v in field_dtype_conversion.items()}
    if lower in lower_map:
        return lower_map[lower]

    logger.warning(
        "DBX|unknown_dynamics_datatype | dataType=%r | defaulting=STRING",
        key,
    )
    return "STRING"


# Alias used by copied Dynamics service call sites
get_snowflake_type = get_databricks_type


def _normalize_attr_column_key(name: str) -> str:
    """
    Align Dynamics model.json attribute names with post-transform Delta columns.

    Transformations rename CamelCase → SNAKE_CASE (e.g. CreatedDateTime →
    CREATED_DATE_TIME). Attribute type maps used CamelCase keys, so naive
    UPPER() lookup missed and every unknown column defaulted to STRING —
    including dateTime fields that must be TIMESTAMP.
    """
    from services.dynamics.source_common import add_underscore

    return add_underscore(str(name or ""))


def _attr_type_lookup(attributes_details_field: dict | None) -> tuple[dict[str, str], dict[str, str]]:
    """Return (by_normalized_name, by_alnum_compact) type maps."""
    by_norm: dict[str, str] = {}
    by_compact: dict[str, str] = {}
    for k, v in (attributes_details_field or {}).items():
        nk = _normalize_attr_column_key(k)
        by_norm[nk] = v
        by_compact[re.sub(r"[^A-Z0-9]", "", nk)] = v
    return by_norm, by_compact


def _lookup_attr_dtype(
    attributes_details_field: dict | None,
    col: str,
    *,
    default: str = "STRING",
) -> str:
    by_norm, by_compact = _attr_type_lookup(attributes_details_field)
    key = _normalize_attr_column_key(col)
    if key in by_norm:
        return by_norm[key]
    compact = re.sub(r"[^A-Z0-9]", "", key)
    return by_compact.get(compact, default)


def _attr_key_in_metadata(field: str, attributes_details_field: dict | None) -> bool:
    """
    True when ``field`` resolves to an entry in model.json attribute metadata.

    ``copy_function`` normalizes attribute keys to SNAKE_CASE before validation,
    while ``extra_fields`` still use raw Dynamics names (e.g. camelCase / lowercase).
    Exact string match therefore false-positives "missing from model.json".
    """
    attrs = attributes_details_field or {}
    raw = str(field)
    if raw in attrs:
        return True
    by_norm, by_compact = _attr_type_lookup(attrs)
    key = _normalize_attr_column_key(raw)
    if key in by_norm or key in attrs:
        return True
    compact = re.sub(r"[^A-Z0-9]", "", key)
    return bool(compact) and compact in by_compact


def _validate_schema_evolution(
    table_name: str,
    modified_fields: dict | None,
    extra_fields: list | None,
    attributes_details_field: dict | None,
) -> None:
    """
    Fail fast on schema changes Databricks cannot apply automatically.

    New columns (extra_fields) are OK when present in the date-folder model.json
    metadata — they are added via ALTER TABLE ADD COLUMNS. Type changes on
    existing columns are not supported and must surface as errors.
    """
    if modified_fields:
        details = ", ".join(
            f"{field}→{expected!r}" for field, expected in modified_fields.items()
        )
        raise ValueError(
            f"Schema type mismatch for table '{table_name}': {details}. "
            "Databricks does not alter existing column types automatically; "
            "fix the destination table or reset with drop_and_reload."
        )

    missing_meta = [
        str(field)
        for field in (extra_fields or [])
        if not _attr_key_in_metadata(field, attributes_details_field)
    ]
    if missing_meta:
        raise ValueError(
            f"New column(s) {missing_meta} appeared for table '{table_name}' but "
            "are missing from the Dynamics model.json metadata for this date folder. "
            "Re-run listing or wait for the export schema file to include the new field."
        )


def remember_chunk_watermark(table_name: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    col = None
    for candidate in ("SINK_MODIFIED_ON", "SinkModifiedOn", "sinkmodifiedon"):
        if candidate in df.columns:
            col = candidate
            break
    if not col:
        return
    series = pd.to_datetime(df[col], utc=True, errors="coerce").dropna()
    if series.empty:
        return
    chunk_max = series.max().to_pydatetime()
    if chunk_max.tzinfo is None:
        chunk_max = chunk_max.replace(tzinfo=timezone.utc)
    prev = _WATERMARK_BY_TABLE.get(table_name)
    if prev is None or chunk_max > prev:
        _WATERMARK_BY_TABLE[table_name] = chunk_max


def format_watermark(dt: datetime) -> str:
    """Format watermark like Dynamics folder cursors: YYYY-MM-DDTHH.MM.SSZ"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H.%M.%SZ")


def pop_watermark(table_name: str) -> str | None:
    dt = _WATERMARK_BY_TABLE.pop(table_name, None)
    if dt is None:
        return None
    return format_watermark(dt)


def peek_watermark(table_name: str) -> str | None:
    dt = _WATERMARK_BY_TABLE.get(table_name)
    if dt is None:
        return None
    return format_watermark(dt)


def _log_dest_error(cmp_id, connection_id, message, function_name):
    _log_databricks_issue(
        cmp_id,
        connection_id,
        message,
        function_name,
        tool_log_status="E",
    )


def _log_databricks_issue(
    cmp_id,
    connection_id,
    message,
    function_name,
    *,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
    tool_log_status="E",
):
    """Write Databricks setup/load issues to error_log_entry + log (like other connectors)."""
    text = str(message)[:2000]
    try:
        from utils.error_utils import insert_error_log_entry, insert_tool_log

        insert_error_log_entry(cmp_id, connection_id, text, function_name)
        log_session = session if session is not None else migration_session_id
        if log_session is not None:
            insert_tool_log(
                cmp_id,
                connection_id,
                usr_id,
                "Migration",
                tool_log_status,
                text,
                log_session,
                job_name=job_name,
            )
    except Exception:
        logger.exception("Failed to write Databricks issue to DB | fn=%s", function_name)


def _external_location_lock_id(connection_id: int) -> int:
    return _EXTERNAL_LOCATION_PG_LOCK_BASE + int(connection_id)


def _adls_external_location_lock_id(connection_id: int, location_name: str = "") -> int:
    lock_id = _ADLS_EXTERNAL_LOCATION_PG_LOCK_BASE + int(connection_id)
    if location_name:
        lock_id += abs(hash(str(location_name).lower())) % 10000
    return lock_id


def _pick_dest_value(dest: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        val = dest.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def _infer_spark_landing_cloud_from_host(host: str) -> str:
    normalized = str(host or "").strip().lower()
    if normalized.endswith(".azuredatabricks.net"):
        return SPARK_LANDING_CLOUD_AZURE
    if normalized.endswith(".gcp.databricks.com"):
        return SPARK_LANDING_CLOUD_GCS
    if ".cloud.databricks.com" in normalized:
        return SPARK_LANDING_CLOUD_S3
    return SPARK_LANDING_CLOUD_GCS


def resolve_spark_landing_cloud(
    destination_connection: dict | None = None,
    source_connection: dict | None = None,
) -> str:
    """
    Resolve where Dynamics Spark loads read staged/export data.

    ``destination_config.spark_landing_cloud`` wins (azure | gcs | s3).
    Otherwise infer from Databricks workspace hostname; default gcs for unknown hosts
    (preserves existing GCS spark path on non-Azure workspaces).
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    explicit = _pick_dest_value(dest, "spark_landing_cloud").lower()
    if explicit in (
        SPARK_LANDING_CLOUD_AZURE,
        SPARK_LANDING_CLOUD_GCS,
        SPARK_LANDING_CLOUD_S3,
    ):
        return explicit

    env_override = str(os.environ.get("DYNAMICS_DBX_SPARK_LANDING_CLOUD") or "").strip().lower()
    if env_override in (
        SPARK_LANDING_CLOUD_AZURE,
        SPARK_LANDING_CLOUD_GCS,
        SPARK_LANDING_CLOUD_S3,
    ):
        return env_override

    host = _pick_dest_value(dest, "server_hostname", "host")
    return _infer_spark_landing_cloud_from_host(host)


def resolve_platform_azure_staging_sp(
    destination_connection: dict | None = None,
) -> dict[str, str] | None:
    """
    Datasyncher-managed Azure SP for UC read on the shared staging container.

    Any customer Databricks workspace can register this SP as a storage credential
    without client-side Azure setup — the platform SP already has read access to
    ``DATASYNCHER_AZURE_STAGING_URL``.

    Env: ``DATABRICKS_AZURE_STAGING_SP_TENANT_ID``, ``..._CLIENT_ID``, ``..._CLIENT_SECRET``.
    Optional destination overrides: ``adls_staging_sp_tenant_id``, etc.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    directory_id = _pick_dest_value(
        dest,
        "adls_staging_sp_tenant_id",
        "azure_staging_sp_tenant_id",
        default=os.environ.get("DATABRICKS_AZURE_STAGING_SP_TENANT_ID", ""),
    )
    application_id = _pick_dest_value(
        dest,
        "adls_staging_sp_client_id",
        "azure_staging_sp_client_id",
        default=os.environ.get("DATABRICKS_AZURE_STAGING_SP_CLIENT_ID", ""),
    )
    client_secret = _pick_dest_value(
        dest,
        "adls_staging_sp_client_secret",
        "azure_staging_sp_client_secret",
        default=os.environ.get("DATABRICKS_AZURE_STAGING_SP_CLIENT_SECRET", ""),
    )
    if not (directory_id and application_id and client_secret):
        return None
    return {
        "directory_id": directory_id,
        "application_id": application_id,
        "client_secret": client_secret,
    }


def resolve_databricks_adls_staging_settings(
    destination_connection: dict | None = None,
) -> dict[str, str | bool]:
    """
    Resolve Azure ADLS **staging container** settings for Dynamics Spark (Method A).

    Client fields: adls_staging_storage_credential_name, adls_staging_external_location_name,
    adls_staging_url. Env fallbacks: DATASYNCHER_AZURE_STAGING_URL,
    DATABRICKS_ADLS_STAGING_CREDENTIAL_NAME, DATABRICKS_ADLS_STAGING_LOCATION_NAME.

    When platform SP env vars are set (``DATABRICKS_AZURE_STAGING_SP_*``), mode defaults
    to ``auto`` so UC storage credential + external location are provisioned at runtime.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}

    credential_name = _pick_dest_value(
        dest,
        "adls_staging_storage_credential_name",
        default=os.environ.get(
            "DATABRICKS_ADLS_STAGING_CREDENTIAL_NAME",
            "datasyncher_fo_staging_cred",
        ),
    )
    location_name = _pick_dest_value(
        dest,
        "adls_staging_external_location_name",
        default=os.environ.get(
            "DATABRICKS_ADLS_STAGING_LOCATION_NAME",
            "datasyncher_fo_staging",
        ),
    )
    staging_url = _pick_dest_value(
        dest,
        "adls_staging_url",
        default=os.environ.get("DATASYNCHER_AZURE_STAGING_URL", ""),
    )
    platform_sp = resolve_platform_azure_staging_sp(dest)

    explicit_mode = (
        _pick_dest_value(dest, "adls_staging_external_location_mode")
        or os.environ.get("DATABRICKS_ADLS_STAGING_EXTERNAL_LOCATION_MODE", "")
    ).strip().lower()
    if explicit_mode in ("auto", "manual"):
        mode = explicit_mode
    elif platform_sp:
        mode = "auto"
    else:
        mode = "manual"

    return {
        "mode": mode,
        "credential_name": credential_name,
        "location_name": location_name,
        "staging_url": staging_url,
        "grant_principal": _pick_dest_value(
            dest,
            "adls_staging_grant_principal",
            default=os.environ.get("DATABRICKS_ADLS_STAGING_GRANT_PRINCIPAL", "account users"),
        ),
        "platform_sp": platform_sp,
        "platform_sp_configured": bool(platform_sp),
    }


def resolve_databricks_adls_staging_location_name(
    connection_id: int,
    destination_connection: dict | None = None,
) -> str:
    """Per-connection UC external location name under the shared staging credential."""
    settings = resolve_databricks_adls_staging_settings(destination_connection)
    base = str(settings.get("location_name") or "datasyncher_fo_staging").strip()
    return f"{base}_{int(connection_id)}"


def resolve_spark_staging_read_mode(
    landing_cloud: str | None = None,
    destination_connection: dict | None = None,
) -> str:
    """
    Resolve staging vs direct Spark read for Dynamics initial loads.

    ``staging``: copy F&O CSVs to same-cloud Datasyncher landing (ADLS/GCS/S3),
    UC external location on landing prefix, Spark reads landed files.

    ``direct`` (Azure only): UC on F&O export container, Spark reads export in place.

    For GCS/S3, staging is the only supported mode when a platform bucket is configured.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    cloud = str(landing_cloud or "").strip().lower() or resolve_spark_landing_cloud(dest)

    if cloud == SPARK_LANDING_CLOUD_AZURE:
        explicit = (
            _pick_dest_value(dest, "spark_azure_read_mode", "azure_spark_read_mode").lower()
        )
        if explicit in ("staging", "method_a", "a"):
            return AZURE_SPARK_READ_MODE_STAGING
        if explicit in ("direct", "export", "method_b", "b"):
            return AZURE_SPARK_READ_MODE_DIRECT

        env_override = str(
            os.environ.get("DYNAMICS_DBX_AZURE_SPARK_READ_MODE") or ""
        ).strip().lower()
        if env_override in ("staging", "method_a", "a"):
            return AZURE_SPARK_READ_MODE_STAGING
        if env_override in ("direct", "export", "method_b", "b"):
            return AZURE_SPARK_READ_MODE_DIRECT

        staging_settings = resolve_databricks_adls_staging_settings(dest)
        if str(staging_settings.get("staging_url") or "").strip():
            return AZURE_SPARK_READ_MODE_STAGING
        return AZURE_SPARK_READ_MODE_DIRECT

    # GCS / S3: staging when platform bucket is configured.
    shared = _pick_dest_value(dest, "spark_staging_read_mode", "spark_read_mode").lower()
    if shared in ("staging", "method_a", "a"):
        return AZURE_SPARK_READ_MODE_STAGING
    if shared in ("direct", "export", "method_b", "b"):
        return AZURE_SPARK_READ_MODE_DIRECT

    env_shared = str(os.environ.get("DYNAMICS_DBX_SPARK_STAGING_READ_MODE") or "").strip().lower()
    if env_shared in ("staging", "method_a", "a"):
        return AZURE_SPARK_READ_MODE_STAGING
    if env_shared in ("direct", "export", "method_b", "b"):
        return AZURE_SPARK_READ_MODE_DIRECT

    if cloud == SPARK_LANDING_CLOUD_GCS:
        gcs = resolve_databricks_gcs_settings(dest)
        bucket_url = str(gcs.get("gcs_bucket_url") or os.environ.get("GCS_BUCKET_NAME") or "").strip()
        if bucket_url or str(os.environ.get("GCS_BUCKET_NAME") or "").strip():
            return AZURE_SPARK_READ_MODE_STAGING
        return AZURE_SPARK_READ_MODE_DIRECT

    if cloud == SPARK_LANDING_CLOUD_S3:
        s3 = resolve_databricks_s3_settings(dest)
        if str(s3.get("s3_bucket_url") or s3.get("bucket_name") or "").strip():
            return AZURE_SPARK_READ_MODE_STAGING
        return AZURE_SPARK_READ_MODE_DIRECT

    return AZURE_SPARK_READ_MODE_STAGING


def resolve_spark_staging_load_strategy(
    landing_cloud: str | None = None,
    destination_connection: dict | None = None,
) -> str:
    """
    Choose bulk vs per-folder Spark staging execution.

    ``bulk`` (default): copy all pending date folders to staging, then one Spark job.
    ``per_folder``: legacy loop — copy one folder, Spark, repeat.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    cloud = str(landing_cloud or "").strip().lower() or resolve_spark_landing_cloud(dest)

    cloud_keys: tuple[str, ...] = ("spark_staging_load_strategy", "spark_load_strategy")
    if cloud == SPARK_LANDING_CLOUD_AZURE:
        cloud_keys = (
            "spark_azure_load_strategy",
            "azure_spark_load_strategy",
            *cloud_keys,
        )
    elif cloud == SPARK_LANDING_CLOUD_GCS:
        cloud_keys = ("spark_gcs_load_strategy", *cloud_keys)
    elif cloud == SPARK_LANDING_CLOUD_S3:
        cloud_keys = ("spark_s3_load_strategy", *cloud_keys)

    explicit = _pick_dest_value(dest, *cloud_keys).lower()
    if explicit in (AZURE_SPARK_LOAD_STRATEGY_BULK, "bulk_copy"):
        return AZURE_SPARK_LOAD_STRATEGY_BULK
    if explicit in (AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER, "per_folder", "folder"):
        return AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER

    env_candidates = [
        "DYNAMICS_DBX_SPARK_STAGING_LOAD_STRATEGY",
        "DYNAMICS_DBX_AZURE_SPARK_LOAD_STRATEGY",
    ]
    if cloud == SPARK_LANDING_CLOUD_GCS:
        env_candidates.insert(0, "DYNAMICS_DBX_GCS_SPARK_LOAD_STRATEGY")
    elif cloud == SPARK_LANDING_CLOUD_S3:
        env_candidates.insert(0, "DYNAMICS_DBX_S3_SPARK_LOAD_STRATEGY")

    for env_name in env_candidates:
        env_override = str(os.environ.get(env_name) or "").strip().lower()
        if env_override in (AZURE_SPARK_LOAD_STRATEGY_BULK, "bulk_copy", "bulk"):
            return AZURE_SPARK_LOAD_STRATEGY_BULK
        if env_override in (AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER, "per_folder", "folder"):
            return AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER

    return AZURE_SPARK_LOAD_STRATEGY_BULK


def resolve_azure_spark_read_mode(
    destination_connection: dict | None = None,
) -> str:
    """Backward-compatible wrapper around ``resolve_spark_staging_read_mode`` for Azure."""
    return resolve_spark_staging_read_mode(
        SPARK_LANDING_CLOUD_AZURE, destination_connection
    )


def resolve_azure_spark_load_strategy(
    destination_connection: dict | None = None,
) -> str:
    """Backward-compatible wrapper around ``resolve_spark_staging_load_strategy`` for Azure."""
    return resolve_spark_staging_load_strategy(
        SPARK_LANDING_CLOUD_AZURE, destination_connection
    )


def _spark_staging_access_redis_client():
    try:
        import redis

        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        return redis.from_url(url, decode_responses=True)
    except Exception as exc:
        logger.warning("DBX|spark_staging_access | redis_unavailable | err=%s", exc)
        return None


def _spark_staging_access_cache_key(
    *,
    server_hostname: str,
    catalog: str,
    landing_cloud: str,
) -> str:
    host = str(server_hostname or "").strip().lower()
    cat = str(catalog or "").strip().lower() or "_"
    cloud = str(landing_cloud or "").strip().lower() or "_"
    return f"dbx:spark_staging_access:{host}:{cat}:{cloud}"


def spark_scoped_fallback_enabled() -> bool:
    """Classic scoped-credential job-cluster fallback.

    Off by default so all-purpose (and warehouse) destinations stay on the
    Unity Catalog + serverless Spark path instead of spinning a new 4-core VM.
    Re-enable with ``DYNAMICS_DBX_SPARK_SCOPED_FALLBACK=1``.
    """
    raw = str(os.environ.get("DYNAMICS_DBX_SPARK_SCOPED_FALLBACK") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_spark_staging_access_permission_failure(message: str | None) -> bool:
    """True when an ensure/LIST error means UC storage access is blocked."""
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in _SPARK_STAGING_ACCESS_PERMISSION_MARKERS)


def _cache_get_spark_staging_access(cache_key: str) -> str | None:
    client = _spark_staging_access_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(cache_key)
    except Exception as exc:
        logger.warning(
            "DBX|spark_staging_access | cache_get_failed | key=%s | err=%s",
            cache_key,
            exc,
        )
        return None
    value = str(raw or "").strip().lower()
    if value in (
        SPARK_STAGING_ACCESS_EXTERNAL_LOCATION,
        SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL,
    ):
        return value
    return None


def _cache_set_spark_staging_access(cache_key: str, mode: str) -> None:
    client = _spark_staging_access_redis_client()
    if client is None:
        return
    try:
        client.setex(cache_key, _SPARK_STAGING_ACCESS_CACHE_TTL_SECONDS, mode)
    except Exception as exc:
        logger.warning(
            "DBX|spark_staging_access | cache_set_failed | key=%s | err=%s",
            cache_key,
            exc,
        )


def _probe_spark_staging_external_location(
    landing_cloud: str,
    engine,
    *,
    connection_id: int,
    destination_connection: dict | None = None,
    cmp_id=None,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
) -> tuple[bool, str]:
    """
    Try UC external-location ensure + soft LIST.

    Returns ``(ok, reason)``. ``ok`` means Spark can use the external_location path.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    cloud = str(landing_cloud or "").strip().lower()

    try:
        if cloud == SPARK_LANDING_CLOUD_AZURE:
            staging_settings = resolve_databricks_adls_staging_settings(dest)
            if not str(staging_settings.get("staging_url") or "").strip():
                return False, "azure_staging_url_missing"
            adls_url = resolve_dynamics_adls_staging_url(dest, connection_id=connection_id)
            platform_sp = staging_settings.get("platform_sp")
            ext_result = ensure_databricks_adls_external_location_once(
                connection_id,
                engine,
                adls_url,
                cmp_id=cmp_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                usr_id=usr_id,
                session=session,
                credential_name=staging_settings.get(
                    "credential_name", "datasyncher_fo_staging_cred"
                ),
                location_name=resolve_databricks_adls_staging_location_name(
                    connection_id, dest
                ),
                grant_principal=staging_settings.get("grant_principal", "account users"),
                create_credential=staging_settings.get("mode") != "manual",
                azure_service_principal=platform_sp if isinstance(platform_sp, dict) else None,
            )
            skipped = str(ext_result.get("skipped_reason") or "").strip()
            if skipped and is_spark_staging_access_permission_failure(skipped):
                return False, skipped
            if skipped and "manual_mode" in skipped.lower():
                return False, skipped
            list_check = verify_databricks_adls_prefix_readable(engine, adls_url)
            if list_check.get("ok") or list_check.get("skipped_reason"):
                return True, "adls_external_location_ok"
            err = str(list_check.get("error") or "LIST failed")
            if is_spark_staging_access_permission_failure(err):
                return False, err
            # Soft non-permission LIST failures (empty prefix, transient) still OK for ensure-time.
            return True, f"adls_list_soft_fail:{err[:120]}"

        if cloud == SPARK_LANDING_CLOUD_S3:
            s3_settings = resolve_databricks_s3_settings(dest)
            bucket_url = resolve_dynamics_s3_staging_url(dest, connection_id=connection_id)
            ext_result = ensure_databricks_s3_external_location_once(
                connection_id,
                engine,
                bucket_url,
                cmp_id=cmp_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                usr_id=usr_id,
                session=session,
                credential_name=s3_settings.get(
                    "credential_name", "datasyncher_batch_s3_cred"
                ),
                location_name=resolve_databricks_s3_connection_location_name(
                    connection_id, dest
                ),
                grant_principal=s3_settings.get("grant_principal", "account users"),
                create_credential=s3_settings.get("mode") == "auto",
                aws_iam_role_arn=s3_settings.get("iam_role_arn") or None,
            )
            skipped = str(ext_result.get("skipped_reason") or "").strip()
            if skipped and is_spark_staging_access_permission_failure(skipped):
                return False, skipped
            if skipped and "manual_mode" in skipped.lower():
                return False, skipped
            list_check = verify_databricks_s3_prefix_readable(engine, bucket_url)
            if list_check.get("ok") or list_check.get("skipped_reason"):
                return True, "s3_external_location_ok"
            err = str(list_check.get("error") or "LIST failed")
            if is_spark_staging_access_permission_failure(err):
                return False, err
            return True, f"s3_list_soft_fail:{err[:120]}"

        # GCS default
        gcs_settings = resolve_databricks_gcs_settings(dest)
        try:
            bucket_url = resolve_dynamics_gcs_staging_url(dest, connection_id=connection_id)
        except ValueError as exc:
            return False, str(exc)
        ext_result = ensure_databricks_gcs_external_location_once(
            connection_id,
            engine,
            bucket_url,
            cmp_id=cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
            credential_name=gcs_settings.get(
                "credential_name", "datasyncher_batch_gcs_cred"
            ),
            location_name=resolve_databricks_gcs_connection_location_name(
                connection_id, dest
            ),
            grant_principal=gcs_settings.get("grant_principal", "account users"),
            create_credential=gcs_settings.get("mode") != "manual",
            known_service_account=gcs_settings.get("service_account_email"),
            grant_bucket_iam=gcs_settings.get("mode") != "manual",
        )
        skipped = str(ext_result.get("skipped_reason") or "").strip()
        if skipped and is_spark_staging_access_permission_failure(skipped):
            return False, skipped
        if skipped and "manual_mode" in skipped.lower():
            return False, skipped
        list_check = verify_databricks_gcs_prefix_readable(engine, bucket_url)
        if list_check.get("ok") or list_check.get("skipped_reason"):
            return True, "gcs_external_location_ok"
        err = str(list_check.get("error") or "LIST failed")
        if is_spark_staging_access_permission_failure(err):
            return False, err
        return True, f"gcs_list_soft_fail:{err[:120]}"
    except Exception as exc:
        msg = str(exc)
        if is_spark_staging_access_permission_failure(msg):
            return False, msg
        logger.warning(
            "DBX|spark_staging_access | probe_exception | cloud=%s | err=%s",
            cloud,
            msg[:400],
        )
        return False, msg


def resolve_spark_staging_access(
    destination_connection: dict | None,
    engine,
    *,
    connection_id: int,
    landing_cloud: str | None = None,
    cmp_id=None,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
) -> str:
    """
    Decide how Dynamics Spark reads Datasyncher staging for this workspace.

    Returns ``external_location`` (UC + serverless-capable) or ``scoped_credential``
    (classic single-user job cluster with short-lived platform credentials).

    Order: destination ``spark_staging_access`` override → Redis cache → live probe.
    Scoped fallback is off unless ``DYNAMICS_DBX_SPARK_SCOPED_FALLBACK=1``.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    cloud = str(landing_cloud or "").strip().lower() or resolve_spark_landing_cloud(dest)
    fallback_ok = spark_scoped_fallback_enabled()

    explicit = _pick_dest_value(dest, "spark_staging_access", "spark_staging_access_mode").lower()
    if explicit in (
        SPARK_STAGING_ACCESS_EXTERNAL_LOCATION,
        SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL,
        "scoped",
        "uc",
    ):
        if explicit in ("scoped",):
            mode = SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL
        elif explicit in ("uc",):
            mode = SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
        else:
            mode = explicit
        if mode == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL and not fallback_ok:
            logger.warning(
                "DBX|spark_staging_access | mode=%s | reason=scoped_fallback_disabled "
                "override_ignored | cloud=%s",
                SPARK_STAGING_ACCESS_EXTERNAL_LOCATION,
                cloud,
            )
            return SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
        logger.warning(
            "DBX|spark_staging_access | mode=%s | reason=destination_override | cloud=%s",
            mode,
            cloud,
        )
        return mode

    cfg = getattr(engine, "config", {}) or {}
    server_hostname = str(cfg.get("server_hostname") or cfg.get("host") or "").strip()
    catalog = _pick_dest_value(dest, "catalog", "database")
    cache_key = _spark_staging_access_cache_key(
        server_hostname=server_hostname,
        catalog=catalog,
        landing_cloud=cloud,
    )
    cached = _cache_get_spark_staging_access(cache_key)
    if cached:
        if cached == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL and not fallback_ok:
            logger.warning(
                "DBX|spark_staging_access | ignoring cached scoped_credential | "
                "reason=scoped_fallback_disabled | cloud=%s | key=%s",
                cloud,
                cache_key,
            )
        else:
            logger.warning(
                "DBX|spark_staging_access | mode=%s | reason=redis_cache | cloud=%s | key=%s",
                cached,
                cloud,
                cache_key,
            )
            return cached

    ok, reason = _probe_spark_staging_external_location(
        cloud,
        engine,
        connection_id=connection_id,
        destination_connection=dest,
        cmp_id=cmp_id,
        migration_session_id=migration_session_id,
        job_name=job_name,
        usr_id=usr_id,
        session=session,
    )
    if ok:
        mode = SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
    elif fallback_ok:
        mode = SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL
    else:
        mode = SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
        logger.warning(
            "DBX|spark_staging_access | mode=%s | reason=scoped_fallback_disabled "
            "probe_failed=%s | cloud=%s | connection_id=%s",
            mode,
            (reason or "")[:300],
            cloud,
            connection_id,
        )
        return mode
    _cache_set_spark_staging_access(cache_key, mode)
    logger.warning(
        "DBX|spark_staging_access | mode=%s | reason=%s | cloud=%s | connection_id=%s",
        mode,
        (reason or "")[:300],
        cloud,
        connection_id,
    )
    return mode


def resolve_spark_bulk_job_timeout_seconds(
    destination_connection: dict | None = None,
) -> int:
    """Longer timeout for bulk Spark MERGE over many staged date folders."""
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    explicit = _pick_dest_value(dest, "spark_job_timeout_seconds", "spark_bulk_job_timeout_seconds")
    if explicit:
        try:
            return max(300, int(explicit))
        except ValueError:
            pass
    env_val = str(os.environ.get("DYNAMICS_DBX_SPARK_BULK_JOB_TIMEOUT_SECONDS") or "").strip()
    if env_val:
        try:
            return max(300, int(env_val))
        except ValueError:
            pass
    return int(
        os.environ.get("DYNAMICS_DBX_SPARK_BULK_JOB_TIMEOUT_SECONDS")
        or os.environ.get(
            "DYNAMICS_DBX_SPARK_JOB_TIMEOUT_SECONDS",
            str(DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS),
        )
    )


def resolve_dynamics_adls_staging_url(
    destination_connection: dict | None,
    *,
    connection_id: int,
) -> str:
    """Resolve UC external-location URL for one connection's Azure staging prefix."""
    from utils.azure_path_utils import AbfssLocation, build_abfss_uri, join_azure_blob_path, parse_abfss_uri

    settings = resolve_databricks_adls_staging_settings(destination_connection)
    staging_url = str(settings.get("staging_url") or "").strip()
    if not staging_url:
        raise ValueError(
            "adls_staging_url or DATASYNCHER_AZURE_STAGING_URL is required for Azure Spark staging reads"
        )

    loc: AbfssLocation = parse_abfss_uri(staging_url)
    conn_prefix = join_azure_blob_path(loc.blob_prefix, f"dynamics_fo/{int(connection_id)}")
    return _normalize_abfss_url(
        build_abfss_uri(loc.container_name, loc.account_name, conn_prefix)
    )


def resolve_databricks_adls_settings(destination_connection: dict | None = None) -> dict[str, str | bool]:
    """
    Resolve ADLS export UC settings for Dynamics Spark direct-read on Azure Databricks.

    Client fields: adls_storage_credential_name, adls_external_location_name,
    adls_export_url (optional override), adls_external_location_mode.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}

    credential_name = _pick_dest_value(
        dest,
        "adls_storage_credential_name",
        default=os.environ.get("DATABRICKS_ADLS_CREDENTIAL_NAME", "datasyncher_fo_export_cred"),
    )
    location_name = _pick_dest_value(
        dest,
        "adls_external_location_name",
        default=os.environ.get("DATABRICKS_ADLS_LOCATION_NAME", "datasyncher_fo_export"),
    )
    export_url = _pick_dest_value(dest, "adls_export_url")

    explicit_mode = (
        _pick_dest_value(dest, "adls_external_location_mode")
        or os.environ.get("DATABRICKS_ADLS_EXTERNAL_LOCATION_MODE", "")
    ).strip().lower()
    client_uc_configured = bool(
        _pick_dest_value(dest, "adls_storage_credential_name")
        or _pick_dest_value(dest, "adls_external_location_name")
        or export_url
    )
    if explicit_mode in ("auto", "manual"):
        mode = explicit_mode
    elif client_uc_configured:
        mode = "manual"
    else:
        mode = "manual"

    return {
        "mode": mode,
        "credential_name": credential_name,
        "location_name": location_name,
        "export_url": export_url,
        "grant_principal": _pick_dest_value(
            dest,
            "adls_grant_principal",
            default=os.environ.get("DATABRICKS_ADLS_GRANT_PRINCIPAL", "account users"),
        ),
        "client_uc_configured": client_uc_configured,
    }


def resolve_dynamics_adls_export_url(
    destination_connection: dict | None,
    *,
    container_name: str,
    connection_string: str,
    base_folder: str,
) -> str:
    """Resolve the UC external-location URL covering the F&O export prefix."""
    from utils.azure_path_utils import (
        build_abfss_uri,
        join_azure_blob_path,
        parse_account_name_from_connection_string,
    )

    settings = resolve_databricks_adls_settings(destination_connection)
    override = str(settings.get("export_url") or "").strip()
    if override:
        return _normalize_abfss_url(override)

    account_name = parse_account_name_from_connection_string(connection_string)
    export_prefix = join_azure_blob_path(base_folder)
    return _normalize_abfss_url(
        build_abfss_uri(container_name, account_name, export_prefix)
    )


def _normalize_abfss_url(adls_url: str) -> str:
    raw_url = str(adls_url or "").strip()
    if not raw_url.startswith("abfss://"):
        raise ValueError(f"adls_export_url must start with abfss://, got: {raw_url!r}")
    if not raw_url.endswith("/"):
        raw_url += "/"
    return raw_url


def _parse_adls_external_location_rows(rows) -> list[tuple[str, str]]:
    """Parse SHOW EXTERNAL LOCATIONS rows into (name, url) pairs."""
    locations: list[tuple[str, str]] = []
    for row in rows or []:
        if not row:
            continue
        name = str(row[0]).strip().strip("`")
        url = ""
        if len(row) > 1 and row[1]:
            raw = str(row[1]).strip().strip("'\"")
            if raw.startswith("abfss://"):
                try:
                    url = _normalize_abfss_url(raw)
                except ValueError:
                    url = raw
            else:
                url = raw
        locations.append((name, url))
    return locations


def _find_covering_adls_external_location(
    locations: list[tuple[str, str]],
    target_url: str,
) -> str | None:
    """Return the narrowest existing UC external location whose URL covers target_url."""
    normalized_target = _normalize_abfss_url(target_url)
    best_name: str | None = None
    best_len = -1
    for name, url in locations:
        if not url or not str(url).startswith("abfss://"):
            continue
        try:
            normalized_existing = _normalize_abfss_url(url)
        except ValueError:
            continue
        if normalized_target.startswith(normalized_existing) and len(normalized_existing) > best_len:
            best_name = name
            best_len = len(normalized_existing)
    return best_name


def _list_uc_external_locations_via_rest(
    server_hostname: str,
    access_token: str,
) -> list[tuple[str, str]]:
    """List Unity Catalog external locations through the REST API (no SQL warehouse)."""
    resp = _databricks_api_request(
        server_hostname,
        access_token,
        "GET",
        "/api/2.1/unity-catalog/external-locations",
    )
    locations: list[tuple[str, str]] = []
    for item in resp.get("external_locations") or []:
        name = str((item or {}).get("name") or "").strip()
        raw_url = str((item or {}).get("url") or "").strip()
        if not name:
            continue
        url = raw_url
        if raw_url.startswith("abfss://"):
            try:
                url = _normalize_abfss_url(raw_url)
            except ValueError:
                url = raw_url
        locations.append((name, url))
    return locations


def _ensure_adls_external_location_via_rest(
    server_hostname: str,
    access_token: str,
    *,
    adls_export_url: str,
    credential_name: str,
    location_name: str,
    create_location: bool,
) -> dict:
    """Create or reuse an ADLS external location without SQL on the destination cluster."""
    raw_url = _normalize_abfss_url(adls_export_url)
    result: dict = {
        "location_existed": False,
        "resolved_location_name": location_name,
        "overlap_reused": False,
        "grant_applied": False,
        "skipped_reason": None,
        "via_rest": True,
    }
    locations = _list_uc_external_locations_via_rest(server_hostname, access_token)
    location_existed = any(name.lower() == location_name.lower() for name, _ in locations)
    resolved_name = location_name
    if not location_existed:
        covering = _find_covering_adls_external_location(locations, raw_url)
        if covering:
            resolved_name = covering
            location_existed = True
            result["overlap_reused"] = True
            logger.warning(
                "DBX|ensure_adls_external_location | rest reusing covering location "
                "'%s' for url '%s'",
                covering,
                raw_url,
            )
        elif not create_location:
            result["skipped_reason"] = (
                f"manual_mode: external location '{location_name}' not found in Unity Catalog. "
                f"Create it with URL '{raw_url}' and storage credential '{credential_name}'."
            )
            return result
        else:
            try:
                _databricks_api_request(
                    server_hostname,
                    access_token,
                    "POST",
                    "/api/2.1/unity-catalog/external-locations",
                    {
                        "name": location_name,
                        "url": raw_url,
                        "credential_name": credential_name,
                    },
                )
                location_existed = True
                resolved_name = location_name
                logger.warning(
                    "DBX|ensure_adls_external_location | rest created external location "
                    "'%s' -> '%s'",
                    location_name,
                    raw_url,
                )
            except Exception as create_exc:
                conflict = _parse_location_overlap_conflict(create_exc)
                if conflict:
                    resolved_name = conflict
                    location_existed = True
                    result["overlap_reused"] = True
                    logger.warning(
                        "DBX|ensure_adls_external_location | rest LOCATION_OVERLAP — "
                        "reusing '%s' for url '%s'",
                        conflict,
                        raw_url,
                    )
                else:
                    raise
    result["location_existed"] = location_existed
    result["resolved_location_name"] = resolved_name
    return result


def _parse_location_overlap_conflict(exc: Exception | str) -> str | None:
    """Extract conflicting UC external location name from LOCATION_OVERLAP errors."""
    text = str(exc)
    for pattern in (
        r"Conflicting location:\s*[`']?([A-Za-z0-9_.-]+)[`']?",
        r"existing external location:\s*[`']?([A-Za-z0-9_.-]+)[`']?",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip("`")
    return None


def resolve_databricks_load_engine(
    *,
    source_type: str = "",
    is_initial_load: bool = False,
    destination_connection: dict | None = None,
) -> str:
    """
    Resolve Databricks load engine from source type and load phase at runtime.

    Salesforce → always ``sql_warehouse``.
    Dynamics → ``spark_job`` for initial and incremental syncs (bulk staging copy,
    then one Spark MERGE per table run). Watermark filtering limits which date
    folders are copied on subsequent runs.
    Other / unknown sources → ``sql_warehouse``.

    Explicit ``destination_config.load_engine`` or ``DYNAMICS_DBX_LOAD_ENGINE`` can
    force ``sql_warehouse`` (legacy pandas chunks) when needed.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    normalized_source = str(source_type or "").strip().lower()

    if normalized_source == SOURCE_TYPE_SALESFORCE:
        return LOAD_ENGINE_SQL_WAREHOUSE

    if normalized_source == SOURCE_TYPE_DYNAMICS:
        explicit = str(dest.get("load_engine") or "").strip().lower()
        if explicit in (LOAD_ENGINE_SQL_WAREHOUSE, LOAD_ENGINE_SPARK_JOB):
            return explicit
        env_override = str(os.environ.get("DYNAMICS_DBX_LOAD_ENGINE") or "").strip().lower()
        if env_override in (LOAD_ENGINE_SQL_WAREHOUSE, LOAD_ENGINE_SPARK_JOB):
            return env_override
        return LOAD_ENGINE_SPARK_JOB

    return LOAD_ENGINE_SQL_WAREHOUSE


def resolve_databricks_staging_method(destination_connection: dict | None = None) -> str:
    """
    Resolve staging method for a Databricks destination.

    Explicit ``staging_method`` wins. When unset:
      - ``volume_name`` present → managed_volume
      - legacy GCS external-location fields present → external_location
      - otherwise → managed_volume (default for new core-only destination form)
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    explicit = str(dest.get("staging_method") or "").strip().lower()
    if explicit in (STAGING_METHOD_MANAGED_VOLUME, STAGING_METHOD_EXTERNAL_LOCATION):
        return explicit
    if str(dest.get("volume_name") or "").strip():
        return STAGING_METHOD_MANAGED_VOLUME
    gcs = resolve_databricks_gcs_settings(dest)
    if any(
        str(gcs.get(key) or "").strip()
        for key in (
            "gcs_storage_credential_name",
            "gcs_external_location_name",
            "gcs_service_account_email",
        )
    ):
        return STAGING_METHOD_EXTERNAL_LOCATION
    return STAGING_METHOD_MANAGED_VOLUME


def resolve_databricks_staging_settings(destination_connection: dict | None = None) -> dict:
    """
    Resolve staging settings for either managed_volume or external_location.

    Always includes ``staging_method``. GCS fields are populated for the
    external_location path; volume fields for managed_volume.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    method = resolve_databricks_staging_method(dest)
    gcs = resolve_databricks_gcs_settings(dest)

    def _pick(*keys: str, default: str = "") -> str:
        for key in keys:
            val = dest.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return default

    catalog = _pick("catalog", "database")
    schema = _pick("schema", "dst_schema")
    volume_name = _pick("volume_name")
    if method == STAGING_METHOD_MANAGED_VOLUME and not volume_name:
        volume_name = DEFAULT_DATABRICKS_VOLUME_NAME

    return {
        "staging_method": method,
        "catalog": catalog,
        "schema": schema,
        "volume_name": volume_name,
        **gcs,
    }


def resolve_databricks_gcs_settings(destination_connection: dict | None = None) -> dict[str, str]:
    """
    Resolve GCS staging (our infra) + client Unity Catalog settings.

    GCS staging prefix always comes from env GCS_BUCKET_URL — not from the connection form.
    Client connection fields (Databricks-side only):
      gcs_storage_credential_name, gcs_external_location_name, gcs_service_account_email
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}

    def _pick(*keys: str, default: str = "") -> str:
        for key in keys:
            val = dest.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return default

    credential_name = _pick(
        "gcs_storage_credential_name",
        default=os.environ.get("DATABRICKS_GCS_CREDENTIAL_NAME", "datasyncher_batch_gcs_cred"),
    )
    location_name = _pick(
        "gcs_external_location_name",
        default=os.environ.get("DATABRICKS_GCS_LOCATION_NAME", "datasyncher_batch_gcs"),
    )
    service_account_email = _pick(
        "gcs_service_account_email",
        "databricks_gcs_service_account",
    )

    explicit_mode = (
        _pick("gcs_external_location_mode")
        or os.environ.get("DATABRICKS_GCS_EXTERNAL_LOCATION_MODE", "")
    ).strip().lower()
    client_uc_configured = bool(
        _pick("gcs_storage_credential_name")
        or _pick("gcs_external_location_name")
        or service_account_email
    )
    if explicit_mode in ("auto", "manual"):
        mode = explicit_mode
    elif client_uc_configured:
        mode = "manual"
    else:
        mode = "auto"

    return {
        "mode": mode,
        "gcs_bucket_url": os.environ.get("GCS_BUCKET_URL", "").strip(),
        "credential_name": credential_name,
        "location_name": location_name,
        "grant_principal": _pick(
            "gcs_grant_principal",
            default=os.environ.get("DATABRICKS_GCS_GRANT_PRINCIPAL", "account users"),
        ),
        "service_account_email": service_account_email,
        "client_uc_configured": client_uc_configured,
    }


def resolve_databricks_s3_settings(destination_connection: dict | None = None) -> dict[str, str]:
    """
    Resolve S3 staging (platform bucket) + client Unity Catalog settings for AWS Databricks.

    Bucket comes from destination ``s3_staging_bucket_url`` or env ``S3_BUCKET_URL`` /
    ``S3_BUCKET_NAME``. Client UC fields: ``s3_storage_credential_name``,
    ``s3_external_location_name``, ``s3_iam_role_arn``, ``s3_external_location_mode``.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}

    def _pick(*keys: str, default: str = "") -> str:
        for key in keys:
            val = dest.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return default

    bucket_url = _pick(
        "s3_staging_bucket_url",
        "s3_bucket_url",
        default=os.environ.get("S3_BUCKET_URL", ""),
    )
    bucket_name = _pick(
        "s3_bucket_name",
        default=os.environ.get("S3_BUCKET_NAME", ""),
    )
    if not bucket_url and bucket_name:
        bucket_url = f"s3://{bucket_name}/"
    if bucket_url and not bucket_url.startswith("s3://"):
        bucket_url = f"s3://{bucket_url.lstrip('/')}"
    if bucket_url and not bucket_url.endswith("/"):
        bucket_url = f"{bucket_url}/"
    if not bucket_name and bucket_url.startswith("s3://"):
        bucket_name = bucket_url[len("s3://") :].split("/", 1)[0]

    credential_name = _pick(
        "s3_storage_credential_name",
        default=os.environ.get("DATABRICKS_S3_CREDENTIAL_NAME", "datasyncher_batch_s3_cred"),
    )
    location_name = _pick(
        "s3_external_location_name",
        default=os.environ.get("DATABRICKS_S3_LOCATION_NAME", "datasyncher_batch_s3"),
    )
    iam_role_arn = _pick(
        "s3_iam_role_arn",
        "aws_iam_role_arn",
        default=os.environ.get("DATABRICKS_S3_IAM_ROLE_ARN", ""),
    )
    explicit_mode = (
        _pick("s3_external_location_mode")
        or os.environ.get("DATABRICKS_S3_EXTERNAL_LOCATION_MODE", "")
    ).strip().lower()
    client_uc_configured = bool(
        _pick("s3_storage_credential_name")
        or _pick("s3_external_location_name")
        or iam_role_arn
    )
    if explicit_mode in ("auto", "manual"):
        mode = explicit_mode
    elif client_uc_configured:
        mode = "manual"
    elif iam_role_arn:
        mode = "auto"
    else:
        mode = "manual"

    return {
        "mode": mode,
        "s3_bucket_url": bucket_url,
        "bucket_name": bucket_name,
        "credential_name": credential_name,
        "location_name": location_name,
        "iam_role_arn": iam_role_arn,
        "grant_principal": _pick(
            "s3_grant_principal",
            default=os.environ.get("DATABRICKS_S3_GRANT_PRINCIPAL", "account users"),
        ),
        "client_uc_configured": client_uc_configured,
    }


def resolve_databricks_s3_connection_location_name(
    connection_id: int,
    destination_connection: dict | None = None,
) -> str:
    """Per-connection UC external location name under the shared S3 credential."""
    settings = resolve_databricks_s3_settings(destination_connection)
    base = str(settings.get("location_name") or "datasyncher_batch_s3").strip()
    return f"{base}_{int(connection_id)}"


def resolve_dynamics_s3_staging_url(
    destination_connection: dict | None = None,
    *,
    connection_id: int | None = None,
) -> str:
    """``s3://`` URL for UC external location (bucket root or connection prefix)."""
    settings = resolve_databricks_s3_settings(destination_connection)
    base = str(settings.get("s3_bucket_url") or "").strip()
    if not base:
        raise ValueError("S3_BUCKET_URL / s3_staging_bucket_url is not configured")
    if not base.endswith("/"):
        base = f"{base}/"
    if connection_id is None:
        return base
    return f"{base}dynamics_fo/{int(connection_id)}/"


def resolve_dynamics_s3_staging_parent_url(
    destination_connection: dict | None = None,
) -> str:
    """``s3://`` URL for the shared ``dynamics_fo/`` staging parent prefix."""
    bucket_root = resolve_dynamics_s3_staging_url(destination_connection)
    return f"{bucket_root.rstrip('/')}/dynamics_fo/"


def resolve_databricks_s3_destination_location_name(
    destination_connection: dict | None = None,
) -> str:
    """Workspace-level UC external location name (parent ``dynamics_fo/`` prefix)."""
    settings = resolve_databricks_s3_settings(destination_connection)
    return str(settings.get("location_name") or "datasyncher_batch_s3").strip()


def resolve_databricks_gcs_connection_location_name(
    connection_id: int,
    destination_connection: dict | None = None,
) -> str:
    """Per-connection UC external location name under the shared GCS credential."""
    settings = resolve_databricks_gcs_settings(destination_connection)
    base = str(settings.get("location_name") or "datasyncher_batch_gcs").strip()
    return f"{base}_{int(connection_id)}"


def resolve_dynamics_gcs_staging_url(
    destination_connection: dict | None = None,
    *,
    connection_id: int | None = None,
) -> str:
    """``gs://`` URL for UC external location (bucket root or connection prefix)."""
    settings = resolve_databricks_gcs_settings(destination_connection)
    base = str(settings.get("gcs_bucket_url") or "").strip()
    if not base:
        bucket = str(os.environ.get("GCS_BUCKET_NAME") or "").strip()
        if bucket:
            base = f"gs://{bucket}/"
    if not base:
        raise ValueError("GCS_BUCKET_URL / GCS_BUCKET_NAME is not configured")
    if base.startswith("gcs://"):
        base = "gs://" + base[len("gcs://") :]
    if not base.startswith("gs://"):
        base = f"gs://{base.lstrip('/')}"
    if not base.endswith("/"):
        base = f"{base}/"
    if connection_id is None:
        return base
    return f"{base}dynamics_fo/{int(connection_id)}/"


def _normalize_gcs_bucket_url(gcs_bucket_url: str) -> str:
    raw_url = str(gcs_bucket_url or "").strip()
    if raw_url.startswith("gcs://"):
        raw_url = "gs://" + raw_url[len("gcs://") :]
    if not raw_url.startswith("gs://"):
        raise ValueError(f"gcs_bucket_url must start with gs:// or gcs://, got: {raw_url!r}")
    if not raw_url.endswith("/"):
        raw_url += "/"
    return raw_url


def _fetch_databricks_storage_credential_aws(
    server_hostname: str,
    access_token: str,
    credential_name: str,
) -> dict[str, str]:
    """Return AWS IAM role details for an existing UC storage credential."""
    try:
        resp = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            f"/api/2.1/unity-catalog/storage-credentials/{credential_name}",
        )
    except Exception as exc:
        logger.warning(
            "DBX|fetch_storage_credential_aws | credential=%s | err=%s",
            credential_name,
            exc,
        )
        return {}
    aws_block = resp.get("aws_iam_role") or {}
    return {
        "role_arn": str(aws_block.get("role_arn") or "").strip(),
        "external_id": str(aws_block.get("external_id") or "").strip(),
        "unity_catalog_iam_arn": str(
            aws_block.get("unity_catalog_iam_arn") or ""
        ).strip(),
    }


def _fetch_databricks_storage_credential_sa(
    server_hostname: str,
    access_token: str,
    credential_name: str,
) -> str | None:
    """Return the Databricks-generated GCP SA email for an existing storage credential."""
    try:
        resp = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            f"/api/2.1/unity-catalog/storage-credentials/{credential_name}",
        )
    except Exception as exc:
        logger.warning(
            "DBX|fetch_storage_credential_sa | credential=%s | err=%s",
            credential_name,
            exc,
        )
        return None
    sa_block = resp.get("databricks_gcp_service_account") or {}
    email = str(sa_block.get("email") or "").strip()
    return email or None


def normalize_databricks_server_hostname(host: str) -> str:
    """Workspace host only (no scheme/path/port) for https://{host}/api/... URLs."""
    raw = str(host or "").strip()
    for prefix in ("https://", "http://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
    raw = raw.split("/")[0].strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]
    elif ":" in raw and not raw.endswith(".net"):
        raw = raw.rsplit(":", 1)[0]
    return raw.strip().rstrip("/")


def _transient_databricks_api_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    if "no address associated with hostname" in text:
        return True
    if "timed out" in text or "timeout" in text:
        return True
    if "temporary failure in name resolution" in text:
        return True
    if "connection reset" in text or "connection refused" in text:
        return True
    errno_val = getattr(exc, "errno", None)
    if errno_val in {-2, -3, -5}:
        return True
    reason = getattr(exc, "reason", None)
    if reason is not None and reason is not exc:
        return _transient_databricks_api_error(reason)
    return False


def _databricks_api_request(
    server_hostname: str,
    access_token: str,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    max_retries: int = 6,
) -> dict:
    """Make a Databricks REST API call, return parsed JSON response."""
    import json as _json
    import urllib.request as _urllib_request

    host = normalize_databricks_server_hostname(server_hostname)
    if not host:
        raise RuntimeError(
            f"Databricks API {method} {path} failed: missing server_hostname"
        )
    url = f"https://{host}{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    data = _json.dumps(payload).encode() if payload else None
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        req = _urllib_request.Request(url, data=data, headers=headers, method=method)
        try:
            with _urllib_request.urlopen(req, timeout=30) as resp:
                return _json.loads(resp.read().decode())
        except Exception as exc:
            body = ""
            if hasattr(exc, "read"):
                try:
                    body = exc.read().decode()
                except Exception:
                    pass
            last_exc = RuntimeError(
                f"Databricks API {method} {path} failed: {exc} body={body}"
            )
            retryable = (
                "429" in body
                or "REQUEST_LIMIT_EXCEEDED" in body
                or _transient_databricks_api_error(exc)
            )
            if retryable and attempt < max_retries - 1:
                sleep_s = min(30, 2 ** attempt)
                logger.warning(
                    "DBX|databricks_api_retry | host=%s method=%s path=%s attempt=%s/%s sleep_s=%s err=%s",
                    host,
                    method,
                    path,
                    attempt + 1,
                    max_retries,
                    sleep_s,
                    exc,
                )
                time.sleep(sleep_s)
                continue
            raise last_exc from exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Databricks API {method} {path} failed with no response")


def ensure_databricks_gcs_external_location(
    engine,
    gcs_bucket_url: str,
    *,
    credential_name: str = "datasyncher_batch_gcs_cred",
    location_name: str = "datasyncher_batch_gcs",
    grant_principal: str = "account users",
    gcp_sa_json: str | None = None,  # kept for API compat but unused on GCP
    create_credential: bool = True,
    known_service_account: str | None = None,
    grant_bucket_iam: bool = True,
) -> dict:
    """
    Idempotently ensure a Unity Catalog storage credential + external location
    exist so Databricks can COPY INTO from the batch GCS bucket.

    Manual mode (create_credential=False, grant_bucket_iam=False): verify-only.
    The client creates UC objects in their Databricks workspace and grants GCP IAM;
    Datasyncher does not auto-create credentials or bucket policies.

    Steps performed:
      1. REST GET /storage-credentials — skip creation if credential already exists.
      2. REST POST /storage-credentials (auto mode only) if missing.
      3. Resolve GCP SA email (from POST response, GET credential, or known_service_account).
      4. Auto-grant GCS IAM roles on the batch bucket for that SA.
      5. SQL SHOW / CREATE EXTERNAL LOCATION + GRANT READ FILES.

    Returns:
        dict with keys: credential_existed, location_existed, grant_applied,
                        iam_granted, skipped_reason, generated_gcp_sa, mode.
    """
    raw_url = _normalize_gcs_bucket_url(gcs_bucket_url)

    result: dict = {
        "credential_existed": False,
        "location_existed": False,
        "grant_applied": False,
        "iam_granted": False,
        "skipped_reason": None,
        "generated_gcp_sa": None,
        "mode": "manual" if not create_credential else "auto",
    }

    cfg = getattr(engine, "config", {}) or {}
    server_hostname = str(cfg.get("server_hostname") or cfg.get("host") or "").strip()
    access_token = str(cfg.get("access_token") or cfg.get("token") or "").strip()
    if not server_hostname or not access_token:
        result["skipped_reason"] = "engine_config_missing_host_or_token"
        logger.warning("DBX|ensure_external_location | %s", result["skipped_reason"])
        return result

    def _grant_bucket_iam(sa_email: str) -> None:
        if not sa_email:
            return
        bucket_name = raw_url.replace("gs://", "").rstrip("/").split("/")[0]
        try:
            from utils.gcp_utils import ensure_bucket_iam_for_integration
            from services.dynamics.source_common import storage_client

            ensure_bucket_iam_for_integration(storage_client, bucket_name, sa_email)
            result["iam_granted"] = True
            result["generated_gcp_sa"] = sa_email
            logger.warning(
                "DBX|ensure_external_location | IAM granted | sa=%s | bucket=%s",
                sa_email,
                bucket_name,
            )
        except Exception as iam_exc:
            logger.error(
                "DBX|ensure_external_location | IAM grant failed (non-fatal) | "
                "sa=%s | bucket=%s | err=%s",
                sa_email,
                bucket_name,
                iam_exc,
            )

    try:
        creds_resp = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            "/api/2.1/unity-catalog/storage-credentials",
        )
        all_creds = creds_resp.get("storage_credentials") or []
        credential_existed = any(
            str(c.get("name", "")).lower() == credential_name.lower()
            for c in all_creds
        )
        result["credential_existed"] = credential_existed

        resolved_sa = str(known_service_account or "").strip() or None

        if not credential_existed:
            if not create_credential:
                result["skipped_reason"] = (
                    f"manual_mode: storage credential '{credential_name}' not found in Unity Catalog. "
                    "Create it once in Databricks Catalog Explorer, then save gcs_service_account_email "
                    "on the destination connection."
                )
                logger.warning("DBX|ensure_external_location | %s", result["skipped_reason"])
                return result

            create_resp = _databricks_api_request(
                server_hostname,
                access_token,
                "POST",
                "/api/2.1/unity-catalog/storage-credentials",
                {"name": credential_name, "databricks_gcp_service_account": {}},
            )
            resolved_sa = (
                (create_resp.get("databricks_gcp_service_account") or {}).get("email") or ""
            ).strip() or resolved_sa
            logger.warning(
                "DBX|ensure_external_location | created storage credential '%s' | generated_gcp_sa=%s",
                credential_name,
                resolved_sa,
            )
        else:
            logger.warning(
                "DBX|ensure_external_location | storage credential '%s' already exists",
                credential_name,
            )
            if not resolved_sa:
                resolved_sa = _fetch_databricks_storage_credential_sa(
                    server_hostname, access_token, credential_name
                )

        if resolved_sa and grant_bucket_iam:
            _grant_bucket_iam(resolved_sa)
        elif resolved_sa and not grant_bucket_iam:
            result["generated_gcp_sa"] = resolved_sa
            logger.warning(
                "DBX|ensure_external_location | manual mode — skipping bucket IAM "
                "(client grants GCP access for sa=%s)",
                resolved_sa,
            )
        elif credential_existed:
            logger.warning(
                "DBX|ensure_external_location | credential exists but GCP SA email unknown; "
                "set gcs_service_account_email on the destination connection after manual setup"
            )

        with engine.connect() as conn:
            all_locs = conn.execute("SHOW EXTERNAL LOCATIONS").fetchall()
            location_existed = any(
                str(row[0]).lower() == location_name.lower()
                for row in all_locs
                if row
            )
            result["location_existed"] = location_existed

            if not location_existed:
                if not create_credential and not credential_existed:
                    return result
                conn.execute(
                    f"CREATE EXTERNAL LOCATION IF NOT EXISTS `{location_name}` "
                    f"URL '{raw_url}' "
                    f"WITH (STORAGE CREDENTIAL `{credential_name}`)"
                )
                logger.warning(
                    "DBX|ensure_external_location | created external location '%s' -> '%s'",
                    location_name,
                    raw_url,
                )
            else:
                logger.warning(
                    "DBX|ensure_external_location | external location '%s' already exists",
                    location_name,
                )

            try:
                conn.execute(
                    f"GRANT READ FILES ON EXTERNAL LOCATION `{location_name}` "
                    f"TO `{grant_principal}`"
                )
                result["grant_applied"] = True
                logger.warning(
                    "DBX|ensure_external_location | granted READ FILES on '%s' to `%s`",
                    location_name,
                    grant_principal,
                )
            except Exception as grant_exc:
                logger.warning(
                    "DBX|ensure_external_location | GRANT READ FILES skipped (non-fatal): %s",
                    grant_exc,
                )

    except Exception as exc:
        logger.error(
            "DBX|ensure_external_location | failed (non-fatal, will fall back to INSERT): %s",
            exc,
        )
        result["skipped_reason"] = str(exc)

    return result


def ensure_databricks_gcs_external_location_once(
    connection_id: int,
    engine,
    gcs_bucket_url: str,
    *,
    cmp_id=None,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
    credential_name: str = "datasyncher_batch_gcs_cred",
    location_name: str = "datasyncher_batch_gcs",
    grant_principal: str = "account users",
    gcp_sa_json: str | None = None,
    create_credential: bool = True,
    known_service_account: str | None = None,
    grant_bucket_iam: bool = True,
) -> dict:
    """
    Run ensure_databricks_gcs_external_location at most once per connection at a time
    across parallel workers (PostgreSQL advisory lock + in-process cache).
    """
    with _EXTERNAL_LOCATION_ENSURE_LOCK:
        cached = _EXTERNAL_LOCATION_ENSURED.get(connection_id)
        if cached is not None:
            return dict(cached)

    lock_id = _external_location_lock_id(connection_id)
    conn = None
    cur = None
    try:
        from utils.database_utils import postgres_connection

        conn = postgres_connection()
        if conn is None:
            raise RuntimeError("postgres_connection returned None for external location lock")
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        conn.commit()

        with _EXTERNAL_LOCATION_ENSURE_LOCK:
            cached = _EXTERNAL_LOCATION_ENSURED.get(connection_id)
            if cached is not None:
                return dict(cached)

        result = ensure_databricks_gcs_external_location(
            engine,
            gcs_bucket_url,
            credential_name=credential_name,
            location_name=location_name,
            grant_principal=grant_principal,
            gcp_sa_json=gcp_sa_json,
            create_credential=create_credential,
            known_service_account=known_service_account,
            grant_bucket_iam=grant_bucket_iam,
        )

        with _EXTERNAL_LOCATION_ENSURE_LOCK:
            _EXTERNAL_LOCATION_ENSURED[connection_id] = dict(result)

        if result.get("skipped_reason"):
            _log_databricks_issue(
                cmp_id,
                connection_id,
                (
                    "Databricks external location setup failed; COPY INTO will fall back to INSERT. "
                    f"Reason: {result['skipped_reason']}"
                ),
                "ensure_databricks_gcs_external_location",
                migration_session_id=migration_session_id,
                job_name=job_name,
                usr_id=usr_id,
                session=session,
                tool_log_status="E",
            )

        return dict(result)
    finally:
        if conn is not None and cur is not None:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
                conn.commit()
            except Exception:
                logger.exception(
                    "DBX|ensure_external_location | advisory unlock failed | connection_id=%s",
                    connection_id,
                )
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def _normalize_s3_bucket_url(s3_bucket_url: str) -> str:
    raw_url = str(s3_bucket_url or "").strip()
    if not raw_url.startswith("s3://"):
        raise ValueError(f"s3_bucket_url must start with s3://, got: {raw_url!r}")
    if not raw_url.endswith("/"):
        raw_url += "/"
    return raw_url


def _parse_s3_external_location_rows(rows) -> list[tuple[str, str]]:
    """Parse SHOW EXTERNAL LOCATIONS rows into (name, url) pairs."""
    locations: list[tuple[str, str]] = []
    for row in rows or []:
        if not row:
            continue
        name = str(row[0]).strip().strip("`")
        url = ""
        if len(row) > 1 and row[1]:
            raw = str(row[1]).strip().strip("'\"")
            if raw.startswith("s3://"):
                try:
                    url = _normalize_s3_bucket_url(raw)
                except ValueError:
                    url = raw
            else:
                url = raw
        locations.append((name, url))
    return locations


def _find_covering_s3_external_location(
    locations: list[tuple[str, str]],
    target_url: str,
) -> str | None:
    """Return the narrowest existing UC external location whose URL covers target_url."""
    normalized_target = _normalize_s3_bucket_url(target_url)
    best_name: str | None = None
    best_len = -1
    for name, url in locations:
        if not url or not str(url).startswith("s3://"):
            continue
        try:
            normalized_existing = _normalize_s3_bucket_url(url)
        except ValueError:
            continue
        if normalized_target.startswith(normalized_existing) and len(normalized_existing) > best_len:
            best_name = name
            best_len = len(normalized_existing)
    return best_name


def _s3_bucket_and_prefix(s3_bucket_url: str) -> tuple[str, str]:
    """Return ``(bucket_name, key_prefix)`` from an ``s3://`` URL."""
    normalized = _normalize_s3_bucket_url(s3_bucket_url)
    without_scheme = normalized[len("s3://") :]
    bucket_name, remainder = (without_scheme.split("/", 1) + [""])[:2]
    prefix = remainder.strip("/")
    return bucket_name, prefix


def ensure_databricks_s3_external_location(
    engine,
    s3_bucket_url: str,
    *,
    credential_name: str = "datasyncher_batch_s3_cred",
    location_name: str = "datasyncher_batch_s3",
    grant_principal: str = "account users",
    create_credential: bool = False,
    aws_iam_role_arn: str | None = None,
    grant_role_iam: bool = True,
) -> dict:
    """
    Verify (manual mode) or ensure a Unity Catalog external location on S3.

    Auto mode with ``aws_iam_role_arn`` registers an AWS IAM role storage credential,
    fetches the Databricks-generated external ID, updates the IAM role trust/policy
    via AWS APIs, then creates the external location. Manual mode expects the client
    to create UC objects in Catalog Explorer first.
    """
    raw_url = _normalize_s3_bucket_url(s3_bucket_url)
    bucket_name, key_prefix = _s3_bucket_and_prefix(raw_url)
    result: dict = {
        "credential_existed": False,
        "location_existed": False,
        "grant_applied": False,
        "iam_granted": False,
        "trust_updated": False,
        "policy_updated": False,
        "skipped_reason": None,
        "mode": "manual" if not create_credential else "auto",
        "resolved_location_name": location_name,
        "overlap_reused": False,
    }

    cfg = getattr(engine, "config", {}) or {}
    server_hostname = str(cfg.get("server_hostname") or cfg.get("host") or "").strip()
    access_token = str(cfg.get("access_token") or cfg.get("token") or "").strip()
    if not server_hostname or not access_token:
        result["skipped_reason"] = "engine_config_missing_host_or_token"
        logger.warning("DBX|ensure_s3_external_location | %s", result["skipped_reason"])
        return result

    try:
        creds_resp = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            "/api/2.1/unity-catalog/storage-credentials",
        )
        all_creds = creds_resp.get("storage_credentials") or []
        credential_existed = any(
            str(c.get("name", "")).lower() == credential_name.lower()
            for c in all_creds
        )
        result["credential_existed"] = credential_existed

        if not credential_existed:
            if not create_credential:
                result["skipped_reason"] = (
                    f"manual_mode: storage credential '{credential_name}' not found in Unity Catalog. "
                    "Create it once in Databricks Catalog Explorer on the S3 staging path, then "
                    "save s3_storage_credential_name on the destination connection."
                )
                logger.warning("DBX|ensure_s3_external_location | %s", result["skipped_reason"])
                return result

            role_arn = str(aws_iam_role_arn or "").strip()
            if not role_arn:
                result["skipped_reason"] = (
                    "auto_mode: S3 credential auto-provisioning requires "
                    "DATABRICKS_S3_IAM_ROLE_ARN or s3_iam_role_arn on destination"
                )
                logger.warning("DBX|ensure_s3_external_location | %s", result["skipped_reason"])
                return result

            _databricks_api_request(
                server_hostname,
                access_token,
                "POST",
                "/api/2.1/unity-catalog/storage-credentials",
                {
                    "name": credential_name,
                    "aws_iam_role": {"role_arn": role_arn},
                },
            )
            result["credential_existed"] = True
            logger.warning(
                "DBX|ensure_s3_external_location | created storage credential '%s' via IAM role",
                credential_name,
            )
        else:
            logger.warning(
                "DBX|ensure_s3_external_location | storage credential '%s' already exists",
                credential_name,
            )

        aws_details = _fetch_databricks_storage_credential_aws(
            server_hostname,
            access_token,
            credential_name,
        )
        resolved_role_arn = str(
            aws_details.get("role_arn") or aws_iam_role_arn or ""
        ).strip()
        external_id = str(aws_details.get("external_id") or "").strip()
        if create_credential and grant_role_iam and resolved_role_arn and external_id:
            from utils.aws_iam_utils import ensure_iam_role_for_databricks_s3

            iam_prefix = key_prefix.split("/", 1)[0] if key_prefix else ""
            iam_result = ensure_iam_role_for_databricks_s3(
                resolved_role_arn,
                external_id,
                bucket_name,
                key_prefix=iam_prefix,
                grant_iam=True,
            )
            result["iam_granted"] = bool(iam_result.get("iam_granted"))
            result["trust_updated"] = bool(iam_result.get("trust_updated"))
            result["policy_updated"] = bool(iam_result.get("policy_updated"))
            if iam_result.get("skipped_reason"):
                result["skipped_reason"] = (
                    "Databricks S3 IAM auto-provisioning failed: "
                    f"{iam_result['skipped_reason']}"
                )
                logger.error(
                    "DBX|ensure_s3_external_location | %s",
                    result["skipped_reason"],
                )
                return result
            merged_ids = iam_result.get("external_ids") or []
            logger.warning(
                "DBX|ensure_s3_external_location | IAM ready | role=%s | external_ids=%s",
                resolved_role_arn,
                merged_ids,
            )
        elif create_credential and grant_role_iam and not external_id:
            result["skipped_reason"] = (
                f"storage credential '{credential_name}' is missing aws_iam_role.external_id "
                "from Databricks; cannot auto-configure IAM trust policy"
            )
            logger.error("DBX|ensure_s3_external_location | %s", result["skipped_reason"])
            return result

        with engine.connect() as conn:
            all_locs = conn.execute("SHOW EXTERNAL LOCATIONS").fetchall()
            locations = _parse_s3_external_location_rows(all_locs)
            resolved_location_name = location_name
            location_existed = any(
                name.lower() == location_name.lower()
                for name, _ in locations
            )

            if not location_existed:
                covering = _find_covering_s3_external_location(locations, raw_url)
                if covering:
                    resolved_location_name = covering
                    location_existed = True
                    result["overlap_reused"] = True
                    logger.warning(
                        "DBX|ensure_s3_external_location | reusing covering external location "
                        "'%s' for url '%s' (skip create '%s')",
                        covering,
                        raw_url,
                        location_name,
                    )
                elif not create_credential and not credential_existed:
                    result["resolved_location_name"] = resolved_location_name
                    return result
                else:
                    try:
                        conn.execute(
                            f"CREATE EXTERNAL LOCATION IF NOT EXISTS `{location_name}` "
                            f"URL '{raw_url}' "
                            f"WITH (STORAGE CREDENTIAL `{credential_name}`)"
                        )
                        logger.warning(
                            "DBX|ensure_s3_external_location | created external location '%s' -> '%s'",
                            location_name,
                            raw_url,
                        )
                        location_existed = True
                        resolved_location_name = location_name
                    except Exception as create_exc:
                        conflict = _parse_location_overlap_conflict(create_exc)
                        if conflict:
                            resolved_location_name = conflict
                            location_existed = True
                            result["overlap_reused"] = True
                            logger.warning(
                                "DBX|ensure_s3_external_location | LOCATION_OVERLAP — reusing "
                                "existing external location '%s' for url '%s'",
                                conflict,
                                raw_url,
                            )
                        else:
                            raise
            else:
                logger.warning(
                    "DBX|ensure_s3_external_location | external location '%s' already exists",
                    location_name,
                )

            result["location_existed"] = location_existed
            result["resolved_location_name"] = resolved_location_name

            try:
                conn.execute(
                    f"GRANT READ FILES ON EXTERNAL LOCATION `{resolved_location_name}` "
                    f"TO `{grant_principal}`"
                )
                result["grant_applied"] = True
                logger.warning(
                    "DBX|ensure_s3_external_location | granted READ FILES on '%s' to `%s`",
                    resolved_location_name,
                    grant_principal,
                )
            except Exception as grant_exc:
                logger.warning(
                    "DBX|ensure_s3_external_location | GRANT READ FILES skipped (non-fatal): %s",
                    grant_exc,
                )

    except Exception as exc:
        logger.error(
            "DBX|ensure_s3_external_location | failed (non-fatal): %s",
            exc,
        )
        result["skipped_reason"] = str(exc)

    return result


def ensure_databricks_s3_external_location_once(
    connection_id: int,
    engine,
    s3_bucket_url: str,
    *,
    cmp_id=None,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
    credential_name: str = "datasyncher_batch_s3_cred",
    location_name: str = "datasyncher_batch_s3",
    grant_principal: str = "account users",
    create_credential: bool = False,
    aws_iam_role_arn: str | None = None,
    grant_role_iam: bool = True,
) -> dict:
    """Run ensure_databricks_s3_external_location at most once per connection/location."""
    cache_key = (int(connection_id), str(location_name or "").strip().lower())
    with _EXTERNAL_LOCATION_ENSURE_LOCK:
        cached = _ADLS_EXTERNAL_LOCATION_ENSURED.get(cache_key)
        if cached is not None:
            return dict(cached)

    lock_id = _adls_external_location_lock_id(connection_id, location_name)
    conn = None
    cur = None
    try:
        from utils.database_utils import postgres_connection

        conn = postgres_connection()
        if conn is None:
            raise RuntimeError("postgres_connection returned None for S3 external location lock")
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        conn.commit()

        with _EXTERNAL_LOCATION_ENSURE_LOCK:
            cached = _ADLS_EXTERNAL_LOCATION_ENSURED.get(cache_key)
            if cached is not None:
                return dict(cached)

        result = ensure_databricks_s3_external_location(
            engine,
            s3_bucket_url,
            credential_name=credential_name,
            location_name=location_name,
            grant_principal=grant_principal,
            create_credential=create_credential,
            aws_iam_role_arn=aws_iam_role_arn,
            grant_role_iam=grant_role_iam,
        )

        with _EXTERNAL_LOCATION_ENSURE_LOCK:
            if not result.get("skipped_reason"):
                _ADLS_EXTERNAL_LOCATION_ENSURED[cache_key] = dict(result)

        if result.get("skipped_reason"):
            _log_databricks_issue(
                cmp_id,
                connection_id,
                (
                    "Databricks S3 external location setup failed. "
                    f"Reason: {result['skipped_reason']}"
                ),
                "ensure_databricks_s3_external_location",
                migration_session_id=migration_session_id,
                job_name=job_name,
                usr_id=usr_id,
                session=session,
                tool_log_status="E",
            )

        return dict(result)
    finally:
        if conn is not None and cur is not None:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
                conn.commit()
            except Exception:
                logger.exception(
                    "DBX|ensure_s3_external_location | advisory unlock failed | connection_id=%s",
                    connection_id,
                )
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def _destination_config_flag_true(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def should_provision_dedicated_dynamics_spark_job_at_destination(
    destination_connection: dict | None = None,
) -> bool:
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    return _destination_config_flag_true(dest.get("create_dedicated_dbx_serverless_job"))


def provision_dedicated_dynamics_spark_job_for_destination(
    dst_config_id: int,
    destination_connection: dict,
) -> dict[str, Any]:
    """
    Create or update a dedicated serverless Lakeflow Job on the Databricks destination.

    Job metadata is stored in destination ``config_data`` (``spark_job_id``, ``spark_job_name``).
    Staging access is finalized on first Dynamics migration; this pass creates the job definition.
    """
    from datasyncher_connections.databricks.connector import (
        create_databricks_sqlalchemy_like_engine,
        normalize_databricks_config,
    )
    from services.databricks.spark_job_client import ensure_dynamics_spark_job

    result: dict[str, Any] = {
        "provisioned": False,
        "skipped": True,
        "skipped_reason": None,
        "job_id": None,
        "job_name": None,
        "compute_mode": None,
        "spark_staging_access": None,
    }
    dest = dict(destination_connection or {})
    if not should_provision_dedicated_dynamics_spark_job_at_destination(dest):
        result["skipped_reason"] = "dedicated_serverless_job_not_enabled"
        return result
    dest["create_dedicated_dbx_serverless_job"] = "true"
    dest["spark_compute_mode"] = "serverless"
    dest["dst_config_id"] = str(int(dst_config_id))
    dest["_job_definition_only"] = "true"
    if not str(dest.get("server_hostname") or dest.get("host") or "").strip():
        result["skipped_reason"] = "missing_databricks_hostname"
        return result
    if not str(dest.get("access_token") or dest.get("token") or "").strip():
        result["skipped_reason"] = "missing_databricks_token"
        return result

    result["skipped"] = False
    details = normalize_databricks_config(dest)
    engine, _schema = create_databricks_sqlalchemy_like_engine(
        details,
        ensure_schema=False,
    )
    try:
        job_info = ensure_dynamics_spark_job(
            dest,
            engine,
            connection_id=0,
        )
        job_id = job_info.get("job_id")
        result["provisioned"] = job_id is not None
        result["job_id"] = job_id
        result["job_name"] = job_info.get("job_name")
        result["compute_mode"] = job_info.get("spark_compute_mode")
        result["spark_staging_access"] = job_info.get("spark_staging_access")
        return result
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


def should_provision_databricks_s3_staging_at_connection(
    destination_connection: dict | None = None,
) -> bool:
    """True when Dynamics AWS Spark staging auto-provision applies."""
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    if resolve_spark_landing_cloud(dest) != SPARK_LANDING_CLOUD_S3:
        return False
    s3_settings = resolve_databricks_s3_settings(dest)
    return str(s3_settings.get("mode") or "").strip().lower() == "auto"


def should_provision_databricks_s3_staging_at_destination(
    destination_connection: dict | None = None,
) -> bool:
    """True when UC credential + IAM trust should be pre-provisioned at destination save."""
    return should_provision_databricks_s3_staging_at_connection(destination_connection)


def wait_for_databricks_s3_prefix_readable(
    engine,
    s3_prefix: str,
    *,
    max_wait_seconds: float = 90.0,
    poll_interval_seconds: float = 5.0,
) -> dict:
    """
    Poll until Databricks can LIST the S3 staging prefix or the wait budget expires.

    Used after IAM trust updates so the first migration does not fail on propagation lag.
    """
    started = time.monotonic()
    deadline = started + max(0.0, float(max_wait_seconds))
    interval = max(0.5, float(poll_interval_seconds))
    attempt = 0
    last_check: dict = {"ok": False, "error": None, "skipped_reason": None}
    while True:
        attempt += 1
        last_check = verify_databricks_s3_prefix_readable(engine, s3_prefix)
        if last_check.get("ok") or last_check.get("skipped_reason"):
            last_check["attempts"] = attempt
            last_check["waited_seconds"] = time.monotonic() - started
            return last_check
        if time.monotonic() >= deadline:
            break
        logger.warning(
            "DBX|s3_prefix_readable | waiting for IAM propagation | prefix=%s | attempt=%s | error=%s",
            s3_prefix,
            attempt,
            str(last_check.get("error") or "")[:200],
        )
        time.sleep(interval)
    last_check["attempts"] = attempt
    last_check["waited_seconds"] = time.monotonic() - started
    last_check["timed_out"] = True
    return last_check


def provision_databricks_s3_staging_for_destination(
    dst_config_id: int,
    destination_connection: dict,
    *,
    wait_for_iam_propagation: bool = True,
    max_wait_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> dict:
    """
    Pre-provision UC storage credential, IAM trust, and parent ``dynamics_fo/`` location.

    Run at Databricks destination save so IAM propagation completes before any
    Dynamics connection is created. Per-connection prefixes are provisioned later.
    """
    from datasyncher_connections.databricks.connector import (
        create_databricks_sqlalchemy_like_engine,
        normalize_databricks_config,
    )

    result: dict[str, Any] = {
        "provisioned": False,
        "skipped": True,
        "skipped_reason": None,
        "iam_granted": False,
        "trust_updated": False,
        "policy_updated": False,
        "s3_prefix_readable": False,
        "iam_propagation_wait": None,
        "external_location": None,
        "s3_prefix": None,
        "scope": "destination",
        "dst_config_id": int(dst_config_id),
    }
    dest = dict(destination_connection or {})
    if not should_provision_databricks_s3_staging_at_destination(dest):
        result["skipped_reason"] = "s3_auto_provision_not_applicable"
        return result

    s3_settings = resolve_databricks_s3_settings(dest)
    try:
        bucket_url = resolve_dynamics_s3_staging_parent_url(dest)
    except ValueError as exc:
        result["skipped_reason"] = str(exc)
        return result

    location_name = resolve_databricks_s3_destination_location_name(dest)
    scope_id = -abs(int(dst_config_id))
    result["s3_prefix"] = bucket_url
    result["external_location"] = location_name
    result["skipped"] = False

    details = normalize_databricks_config(dest)
    engine, _schema = create_databricks_sqlalchemy_like_engine(
        details,
        ensure_schema=False,
    )
    try:
        ext_result = ensure_databricks_s3_external_location_once(
            scope_id,
            engine,
            bucket_url,
            credential_name=s3_settings.get("credential_name", "datasyncher_batch_s3_cred"),
            location_name=location_name,
            grant_principal=s3_settings.get("grant_principal", "account users"),
            create_credential=s3_settings.get("mode") == "auto",
            aws_iam_role_arn=s3_settings.get("iam_role_arn") or None,
        )
        result["iam_granted"] = bool(ext_result.get("iam_granted"))
        result["trust_updated"] = bool(ext_result.get("trust_updated"))
        result["policy_updated"] = bool(ext_result.get("policy_updated"))
        if ext_result.get("skipped_reason"):
            result["skipped_reason"] = ext_result.get("skipped_reason")
            return result

        result["provisioned"] = True
        wait_seconds = max_wait_seconds
        if wait_seconds is None:
            wait_seconds = float(
                os.environ.get("DATABRICKS_S3_IAM_PROPAGATION_MAX_WAIT_S", "90")
            )
        poll_seconds = poll_interval_seconds
        if poll_seconds is None:
            poll_seconds = float(
                os.environ.get("DATABRICKS_S3_IAM_PROPAGATION_POLL_S", "5")
            )
        if wait_for_iam_propagation and (
            result["trust_updated"] or result["policy_updated"]
        ):
            wait_result = wait_for_databricks_s3_prefix_readable(
                engine,
                bucket_url,
                max_wait_seconds=wait_seconds,
                poll_interval_seconds=poll_seconds,
            )
        else:
            wait_result = verify_databricks_s3_prefix_readable(engine, bucket_url)
        result["iam_propagation_wait"] = wait_result
        result["s3_prefix_readable"] = bool(wait_result.get("ok"))
        if not result["s3_prefix_readable"] and not wait_result.get("skipped_reason"):
            result["skipped_reason"] = (
                wait_result.get("error")
                or "Databricks cannot LIST S3 staging parent prefix after IAM setup"
            )
        return result
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


def provision_databricks_s3_staging_for_connection(
    connection_id: int,
    destination_connection: dict,
    *,
    wait_for_iam_propagation: bool = True,
    max_wait_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> dict:
    """
    Pre-provision per-connection UC S3 external location + IAM trust.

    Idempotent: safe on every Dynamics → AWS Databricks connection save. Workspace
    credential/IAM should already exist from destination save when possible.
    """
    from datasyncher_connections.databricks.connector import (
        create_databricks_sqlalchemy_like_engine,
        normalize_databricks_config,
    )

    result: dict[str, Any] = {
        "provisioned": False,
        "skipped": True,
        "skipped_reason": None,
        "iam_granted": False,
        "trust_updated": False,
        "policy_updated": False,
        "s3_prefix_readable": False,
        "iam_propagation_wait": None,
        "external_location": None,
        "s3_prefix": None,
    }
    dest = dict(destination_connection or {})
    if not should_provision_databricks_s3_staging_at_connection(dest):
        result["skipped_reason"] = "s3_auto_provision_not_applicable"
        return result

    s3_settings = resolve_databricks_s3_settings(dest)
    try:
        bucket_url = resolve_dynamics_s3_staging_url(dest, connection_id=int(connection_id))
    except ValueError as exc:
        result["skipped_reason"] = str(exc)
        return result

    location_name = resolve_databricks_s3_connection_location_name(int(connection_id), dest)
    result["s3_prefix"] = bucket_url
    result["external_location"] = location_name
    result["skipped"] = False

    details = normalize_databricks_config(dest)
    engine, _schema = create_databricks_sqlalchemy_like_engine(
        details,
        ensure_schema=False,
    )
    try:
        ext_result = ensure_databricks_s3_external_location_once(
            int(connection_id),
            engine,
            bucket_url,
            credential_name=s3_settings.get("credential_name", "datasyncher_batch_s3_cred"),
            location_name=location_name,
            grant_principal=s3_settings.get("grant_principal", "account users"),
            create_credential=s3_settings.get("mode") == "auto",
            aws_iam_role_arn=s3_settings.get("iam_role_arn") or None,
        )
        result["iam_granted"] = bool(ext_result.get("iam_granted"))
        result["trust_updated"] = bool(ext_result.get("trust_updated"))
        result["policy_updated"] = bool(ext_result.get("policy_updated"))
        if ext_result.get("skipped_reason"):
            result["skipped_reason"] = ext_result.get("skipped_reason")
            return result

        result["provisioned"] = True
        wait_seconds = max_wait_seconds
        if wait_seconds is None:
            wait_seconds = float(
                os.environ.get("DATABRICKS_S3_IAM_PROPAGATION_MAX_WAIT_S", "90")
            )
        poll_seconds = poll_interval_seconds
        if poll_seconds is None:
            poll_seconds = float(
                os.environ.get("DATABRICKS_S3_IAM_PROPAGATION_POLL_S", "5")
            )
        if wait_for_iam_propagation and (
            result["trust_updated"] or result["policy_updated"]
        ):
            wait_result = wait_for_databricks_s3_prefix_readable(
                engine,
                bucket_url,
                max_wait_seconds=wait_seconds,
                poll_interval_seconds=poll_seconds,
            )
        else:
            wait_result = verify_databricks_s3_prefix_readable(engine, bucket_url)
        result["iam_propagation_wait"] = wait_result
        result["s3_prefix_readable"] = bool(wait_result.get("ok"))
        if not result["s3_prefix_readable"] and not wait_result.get("skipped_reason"):
            result["skipped_reason"] = (
                wait_result.get("error")
                or "Databricks cannot LIST S3 staging prefix after IAM setup"
            )
        return result
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


def ensure_databricks_adls_external_location(
    engine,
    adls_export_url: str,
    *,
    credential_name: str = "datasyncher_fo_export_cred",
    location_name: str = "datasyncher_fo_export",
    grant_principal: str = "account users",
    create_credential: bool = False,
    azure_service_principal: dict[str, str] | None = None,
) -> dict:
    """
    Verify (manual mode) or ensure a Unity Catalog external location on Azure ADLS.

    Auto mode with ``azure_service_principal`` (platform ``DATABRICKS_AZURE_STAGING_SP_*``)
    registers the Datasyncher-managed SP as a UC storage credential, then creates the
    external location — no client-side Catalog Explorer setup for staging reads.
    """
    raw_url = _normalize_abfss_url(adls_export_url)

    result: dict = {
        "credential_existed": False,
        "location_existed": False,
        "grant_applied": False,
        "skipped_reason": None,
        "mode": "manual" if not create_credential else "auto",
        "resolved_location_name": location_name,
        "overlap_reused": False,
    }

    cfg = getattr(engine, "config", {}) or {}
    server_hostname = str(cfg.get("server_hostname") or cfg.get("host") or "").strip()
    access_token = str(cfg.get("access_token") or cfg.get("token") or "").strip()
    if not server_hostname or not access_token:
        result["skipped_reason"] = "engine_config_missing_host_or_token"
        logger.warning("DBX|ensure_adls_external_location | %s", result["skipped_reason"])
        return result

    try:
        creds_resp = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            "/api/2.1/unity-catalog/storage-credentials",
        )
        all_creds = creds_resp.get("storage_credentials") or []
        credential_existed = any(
            str(c.get("name", "")).lower() == credential_name.lower()
            for c in all_creds
        )
        result["credential_existed"] = credential_existed

        if not credential_existed:
            if not create_credential:
                result["skipped_reason"] = (
                    f"manual_mode: storage credential '{credential_name}' not found in Unity Catalog. "
                    "Create it once in Databricks Catalog Explorer on the F&O export path, then "
                    "save adls_storage_credential_name on the destination connection."
                )
                logger.warning("DBX|ensure_adls_external_location | %s", result["skipped_reason"])
                return result

            if azure_service_principal:
                sp_payload = {
                    "directory_id": str(azure_service_principal.get("directory_id") or "").strip(),
                    "application_id": str(azure_service_principal.get("application_id") or "").strip(),
                    "client_secret": str(azure_service_principal.get("client_secret") or "").strip(),
                }
                if not all(sp_payload.values()):
                    result["skipped_reason"] = (
                        "auto_mode: incomplete platform Azure SP config for staging credential"
                    )
                    logger.warning("DBX|ensure_adls_external_location | %s", result["skipped_reason"])
                    return result
                _databricks_api_request(
                    server_hostname,
                    access_token,
                    "POST",
                    "/api/2.1/unity-catalog/storage-credentials",
                    {
                        "name": credential_name,
                        "azure_service_principal": sp_payload,
                    },
                )
                result["credential_existed"] = True
                logger.warning(
                    "DBX|ensure_adls_external_location | created storage credential '%s' "
                    "via platform Azure SP",
                    credential_name,
                )
            else:
                result["skipped_reason"] = (
                    "auto_mode: Azure ADLS credential auto-provisioning requires "
                    "DATABRICKS_AZURE_STAGING_SP_* env vars or adls_staging_sp_* on destination"
                )
                logger.warning("DBX|ensure_adls_external_location | %s", result["skipped_reason"])
                return result

        with engine.connect() as conn:
            all_locs = conn.execute("SHOW EXTERNAL LOCATIONS").fetchall()
            locations = _parse_adls_external_location_rows(all_locs)
            resolved_location_name = location_name
            location_existed = any(
                name.lower() == location_name.lower()
                for name, _ in locations
            )

            if not location_existed:
                covering = _find_covering_adls_external_location(locations, raw_url)
                if covering:
                    resolved_location_name = covering
                    location_existed = True
                    result["overlap_reused"] = True
                    logger.warning(
                        "DBX|ensure_adls_external_location | reusing covering external location "
                        "'%s' for url '%s' (skip create '%s')",
                        covering,
                        raw_url,
                        location_name,
                    )
                elif not create_credential:
                    result["skipped_reason"] = (
                        f"manual_mode: external location '{location_name}' not found in Unity Catalog. "
                        f"Create it with URL '{raw_url}' and storage credential '{credential_name}'."
                    )
                    logger.warning("DBX|ensure_adls_external_location | %s", result["skipped_reason"])
                    return result
                else:
                    try:
                        conn.execute(
                            f"CREATE EXTERNAL LOCATION IF NOT EXISTS `{location_name}` "
                            f"URL '{raw_url}' "
                            f"WITH (STORAGE CREDENTIAL `{credential_name}`)"
                        )
                        logger.warning(
                            "DBX|ensure_adls_external_location | created external location '%s' -> '%s'",
                            location_name,
                            raw_url,
                        )
                        location_existed = True
                        resolved_location_name = location_name
                    except Exception as create_exc:
                        conflict = _parse_location_overlap_conflict(create_exc)
                        if conflict:
                            resolved_location_name = conflict
                            location_existed = True
                            result["overlap_reused"] = True
                            logger.warning(
                                "DBX|ensure_adls_external_location | LOCATION_OVERLAP — reusing "
                                "existing external location '%s' for url '%s'",
                                conflict,
                                raw_url,
                            )
                        else:
                            raise
            else:
                logger.warning(
                    "DBX|ensure_adls_external_location | external location '%s' already exists",
                    location_name,
                )

            result["location_existed"] = location_existed
            result["resolved_location_name"] = resolved_location_name

            try:
                conn.execute(
                    f"GRANT READ FILES ON EXTERNAL LOCATION `{resolved_location_name}` "
                    f"TO `{grant_principal}`"
                )
                result["grant_applied"] = True
                logger.warning(
                    "DBX|ensure_adls_external_location | granted READ FILES on '%s' to `%s`",
                    resolved_location_name,
                    grant_principal,
                )
            except Exception as grant_exc:
                logger.warning(
                    "DBX|ensure_adls_external_location | GRANT READ FILES skipped (non-fatal): %s",
                    grant_exc,
                )

    except Exception as exc:
        logger.warning(
            "DBX|ensure_adls_external_location | sql_path_failed | falling_back_to_rest | err=%s",
            exc,
        )
        try:
            rest_result = _ensure_adls_external_location_via_rest(
                server_hostname,
                access_token,
                adls_export_url=raw_url,
                credential_name=credential_name,
                location_name=location_name,
                create_location=bool(create_credential),
            )
            result["location_existed"] = rest_result.get("location_existed", False)
            result["resolved_location_name"] = rest_result.get(
                "resolved_location_name", location_name
            )
            result["overlap_reused"] = rest_result.get("overlap_reused", False)
            result["grant_applied"] = rest_result.get("grant_applied", False)
            result["skipped_reason"] = rest_result.get("skipped_reason")
            result["via_rest"] = True
            if result.get("skipped_reason"):
                logger.warning(
                    "DBX|ensure_adls_external_location | rest_fallback_incomplete | %s",
                    result["skipped_reason"],
                )
            else:
                logger.warning(
                    "DBX|ensure_adls_external_location | rest_fallback_ok | location=%s",
                    result.get("resolved_location_name"),
                )
        except Exception as rest_exc:
            logger.error(
                "DBX|ensure_adls_external_location | failed (non-fatal): sql=%s rest=%s",
                exc,
                rest_exc,
            )
            result["skipped_reason"] = str(rest_exc)

    return result


def ensure_databricks_adls_external_location_once(
    connection_id: int,
    engine,
    adls_export_url: str,
    *,
    cmp_id=None,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
    credential_name: str = "datasyncher_fo_export_cred",
    location_name: str = "datasyncher_fo_export",
    grant_principal: str = "account users",
    create_credential: bool = False,
    azure_service_principal: dict[str, str] | None = None,
) -> dict:
    """Run ensure_databricks_adls_external_location at most once per connection + location."""
    cache_key = (int(connection_id), str(location_name or "").lower())
    with _EXTERNAL_LOCATION_ENSURE_LOCK:
        cached = _ADLS_EXTERNAL_LOCATION_ENSURED.get(cache_key)
        if cached is not None:
            return dict(cached)

    lock_id = _adls_external_location_lock_id(connection_id, location_name)
    conn = None
    cur = None
    try:
        from utils.database_utils import postgres_connection

        conn = postgres_connection()
        if conn is None:
            raise RuntimeError("postgres_connection returned None for ADLS external location lock")
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        conn.commit()

        with _EXTERNAL_LOCATION_ENSURE_LOCK:
            cached = _ADLS_EXTERNAL_LOCATION_ENSURED.get(cache_key)
            if cached is not None:
                return dict(cached)

        result = ensure_databricks_adls_external_location(
            engine,
            adls_export_url,
            credential_name=credential_name,
            location_name=location_name,
            grant_principal=grant_principal,
            create_credential=create_credential,
            azure_service_principal=azure_service_principal,
        )

        with _EXTERNAL_LOCATION_ENSURE_LOCK:
            _ADLS_EXTERNAL_LOCATION_ENSURED[cache_key] = dict(result)

        if result.get("skipped_reason"):
            _log_databricks_issue(
                cmp_id,
                connection_id,
                (
                    "Databricks ADLS external location setup incomplete; Spark read may fail. "
                    f"Reason: {result['skipped_reason']}"
                ),
                "ensure_databricks_adls_external_location",
                migration_session_id=migration_session_id,
                job_name=job_name,
                usr_id=usr_id,
                session=session,
                tool_log_status="E",
            )

        return dict(result)
    finally:
        if conn is not None and cur is not None:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
                conn.commit()
            except Exception:
                logger.exception(
                    "DBX|ensure_adls_external_location | advisory unlock failed | connection_id=%s",
                    connection_id,
                )
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def verify_databricks_adls_prefix_readable(
    engine,
    abfss_prefix: str,
    *,
    require_entries: bool = False,
) -> dict:
    """
    Best-effort Unity Catalog read check: ``LIST`` the ADLS prefix via the SQL warehouse.

    Returns ``{ok, entry_count, error, skipped_reason}``. When ``ok`` is False and the
    error looks like a permissions/UC issue, callers should abort before Spark submit.
    """
    return _verify_databricks_cloud_prefix_readable(
        engine,
        abfss_prefix,
        scheme="abfss://",
        log_label="adls_prefix_readable",
        require_entries=require_entries,
    )


def verify_databricks_gcs_prefix_readable(
    engine,
    gs_prefix: str,
    *,
    require_entries: bool = False,
) -> dict:
    """Best-effort UC read check: ``LIST`` a ``gs://`` prefix via the SQL warehouse."""
    return _verify_databricks_cloud_prefix_readable(
        engine,
        gs_prefix,
        scheme="gs://",
        log_label="gcs_prefix_readable",
        require_entries=require_entries,
    )


def verify_databricks_s3_prefix_readable(
    engine,
    s3_prefix: str,
    *,
    require_entries: bool = False,
) -> dict:
    """Best-effort UC read check: ``LIST`` an ``s3://`` prefix via the SQL warehouse."""
    return _verify_databricks_cloud_prefix_readable(
        engine,
        s3_prefix,
        scheme="s3://",
        log_label="s3_prefix_readable",
        require_entries=require_entries,
    )


def _verify_databricks_cloud_prefix_readable(
    engine,
    prefix: str,
    *,
    scheme: str,
    log_label: str,
    require_entries: bool = False,
) -> dict:
    result: dict = {
        "ok": False,
        "entry_count": 0,
        "error": None,
        "skipped_reason": None,
    }
    normalized = str(prefix or "").strip().rstrip("/")
    if not normalized.lower().startswith(scheme.lower()):
        result["skipped_reason"] = f"not_{scheme.rstrip(':/')}_prefix"
        return result

    list_sql = f"LIST '{normalized}/'"
    try:
        if hasattr(engine, "connect"):
            with engine.connect() as conn:
                cursor = conn.execute(list_sql)
                rows = _materialize_list_rows(cursor)
        elif hasattr(engine, "execute"):
            cursor = engine.execute(list_sql)
            rows = _materialize_list_rows(cursor)
        else:
            result["skipped_reason"] = "engine_has_no_execute"
            logger.warning(
                "DBX|%s | skipped | reason=%s | prefix=%s",
                log_label,
                result["skipped_reason"],
                normalized,
            )
            return result

        result["entry_count"] = rows if isinstance(rows, int) else len(rows or [])
        if require_entries and result["entry_count"] == 0:
            result["error"] = f"LIST returned 0 entries under {normalized}/"
            logger.warning(
                "DBX|%s | empty | prefix=%s",
                log_label,
                normalized,
            )
            return result

        result["ok"] = True
        logger.warning(
            "DBX|%s | ok | prefix=%s | sample_entries=%s",
            log_label,
            normalized,
            result["entry_count"],
        )
        return result
    except Exception as exc:
        msg = str(exc)
        result["error"] = msg
        logger.warning(
            "DBX|%s | failed | prefix=%s | error=%s",
            log_label,
            normalized,
            msg[:400],
        )
        return result


def _materialize_list_rows(cursor):
    """
    Convert a Databricks SQL LIST cursor/result into a short row list.

    Some warehouse proxies (``_DatabricksResult``) are not iterable and raise
    TypeError on ``list(cursor)`` / ``fetchmany`` — treat that as LIST-ok with
    unknown count (-1) so we do not block Spark on a client quirk.
    """
    if cursor is None:
        return []
    try:
        if hasattr(cursor, "fetchmany"):
            rows = cursor.fetchmany(10)
            return list(rows or [])
    except Exception:
        pass
    try:
        if hasattr(cursor, "fetchall"):
            rows = cursor.fetchall()
            return list(rows or [])[:10]
    except Exception:
        pass
    try:
        return list(cursor)[:10]
    except TypeError:
        return -1
    except Exception:
        return -1


def destination_connection_function(
    destination_connection,
    connection_id,
    cmp_id,
    *,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
    skip_external_location_ensure=False,
):
    from datasyncher_connections.databricks.connector import (
        create_databricks_sqlalchemy_like_engine,
    )

    logger.warning(
        "DBX|destination_connect_start | connection_id=%s | cmp_id=%s",
        connection_id,
        cmp_id,
    )
    try:
        engine, schema = create_databricks_sqlalchemy_like_engine(
            destination_connection,
            ensure_schema=True,
        )
        catalog = None
        if hasattr(engine, "config"):
            catalog = (engine.config.get("catalog") or "").strip() or None
        logger.warning(
            "DBX|destination_connect_ok | connection_id=%s | schema=%s | catalog=%s | engine=%s",
            connection_id,
            schema,
            catalog,
            type(engine).__name__,
        )

        # Stage config on engine for per-chunk copy_function.
        staging = resolve_databricks_staging_settings(destination_connection)
        if hasattr(engine, "config") and isinstance(engine.config, dict):
            engine.config["staging_method"] = staging["staging_method"]
            engine.config["volume_name"] = staging.get("volume_name") or ""
            if staging.get("catalog"):
                engine.config.setdefault("catalog", staging["catalog"])
            if staging.get("schema"):
                engine.config.setdefault("schema", staging["schema"])

        if staging["staging_method"] == STAGING_METHOD_MANAGED_VOLUME:
            volume_name = staging.get("volume_name") or ""
            catalog_name = (
                staging.get("catalog")
                or (engine.config.get("catalog") if hasattr(engine, "config") else "")
                or ""
            )
            schema_name = schema or staging.get("schema") or ""
            if not volume_name:
                raise ValueError(
                    "Databricks staging_method=managed_volume requires volume_name "
                    "on the destination connection."
                )
            cfg = getattr(engine, "config", {}) or {}
            from services.databricks.volume_staging_client import (
                DatabricksVolumeStagingClient,
                DatabricksVolumeStagingError,
            )

            try:
                volume_client = DatabricksVolumeStagingClient(
                    server_hostname=str(
                        cfg.get("server_hostname") or cfg.get("host") or ""
                    ),
                    access_token=str(cfg.get("access_token") or cfg.get("token") or ""),
                    catalog=str(catalog_name),
                    schema=str(schema_name),
                    volume_name=str(volume_name),
                )
                volume_client.ensure_volume_exists()
                if isinstance(engine.config, dict):
                    engine.config["volume_staging_path"] = volume_client.volume_root
            except DatabricksVolumeStagingError as vol_exc:
                _log_databricks_issue(
                    cmp_id,
                    connection_id,
                    str(vol_exc),
                    "ensure_databricks_managed_volume",
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    usr_id=usr_id,
                    session=session,
                    tool_log_status="E",
                )
                raise
            logger.warning(
                "DBX|managed_volume_ready | connection_id=%s | volume=%s | path=%s",
                connection_id,
                volume_client.full_name(),
                volume_client.volume_root,
            )
            return engine, schema

        # Azure Databricks / Dynamics Spark uses datasyncher-stage ADLS + UC ADLS
        # external location (datasyncher_batch_adls). Do NOT require or create the
        # legacy GCS storage credential — that path is only for GCP landing.
        landing_cloud = resolve_spark_landing_cloud(destination_connection)
        if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
            logger.warning(
                "DBX|gcs_external_location_skipped | connection_id=%s | "
                "reason=azure_spark_landing (ADLS datasyncher-stage; "
                "datasyncher_batch_gcs_cred not required)",
                connection_id,
            )
            return engine, schema

        # external_location (GCS) path — unchanged.
        gcs_settings = staging
        gcs_bucket_url = gcs_settings["gcs_bucket_url"]
        if hasattr(engine, "config") and isinstance(engine.config, dict) and gcs_bucket_url:
            engine.config["gcs_staging_url"] = gcs_bucket_url
        if gcs_bucket_url and not skip_external_location_ensure:
            gcp_sa_json = os.environ.get("DATABRICKS_GCS_SA_JSON", "").strip() or None
            create_credential = gcs_settings["mode"] != "manual"
            # GCS bucket is ours — grant the client's Databricks SA read access on our bucket.
            grant_bucket_iam = bool(gcs_settings.get("service_account_email"))
            el_result = ensure_databricks_gcs_external_location_once(
                connection_id,
                engine,
                gcs_bucket_url,
                cmp_id=cmp_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                usr_id=usr_id,
                session=session,
                credential_name=gcs_settings["credential_name"],
                location_name=gcs_settings["location_name"],
                grant_principal=gcs_settings["grant_principal"],
                gcp_sa_json=gcp_sa_json,
                create_credential=create_credential,
                known_service_account=gcs_settings["service_account_email"] or None,
                grant_bucket_iam=grant_bucket_iam,
            )
            logger.warning(
                "DBX|external_location_ensure_result | connection_id=%s | mode=%s | %s",
                connection_id,
                gcs_settings["mode"],
                el_result,
            )
            discovered_sa = str(el_result.get("generated_gcp_sa") or "").strip()
            if discovered_sa and not gcs_settings["service_account_email"]:
                logger.warning(
                    "DBX|save_gcs_service_account | connection_id=%s | "
                    "Save this on the destination connection as gcs_service_account_email: %s",
                    connection_id,
                    discovered_sa,
                )
        elif not gcs_bucket_url:
            logger.warning(
                "DBX|external_location_skipped | connection_id=%s | "
                "reason=gcs_bucket_url_not_set (set gcs_bucket_url on destination or GCS_BUCKET_URL env)",
                connection_id,
            )

        return engine, schema
    except Exception as e:
        logger.error(
            "DBX|destination_connect_failed | connection_id=%s | err=%s",
            connection_id,
            e,
        )
        _log_dest_error(cmp_id, connection_id, str(e), "destination_connection_function")
        raise


def _q_ident(name: str) -> str:
    return "`" + str(name).replace("`", "") + "`"


def _qualified(catalog: str | None, schema: str, table: str) -> str:
    parts = []
    if catalog:
        parts.append(_q_ident(catalog))
    parts.append(_q_ident(schema))
    parts.append(_q_ident(table))
    return ".".join(parts)


def _catalog_from_config(destination_connection: dict | None) -> str | None:
    if not destination_connection:
        return None
    return (destination_connection.get("catalog") or "").strip() or None


def check_table_exists_databricks(
    destination_engine, table, destination_connection, connection_id, cmp_id
):
    schema = (destination_connection or {}).get("schema")
    catalog = _catalog_from_config(destination_connection)
    if not schema:
        return False
    table_u = str(table).upper()
    schema_u = str(schema).upper()
    try:
        with destination_engine.connect() as conn:
            if catalog:
                sql = f"""
                    SELECT COUNT(*) FROM `{catalog}`.information_schema.tables
                    WHERE upper(table_schema) = '{schema_u}'
                      AND upper(table_name) = '{table_u}'
                """
            else:
                sql = f"""
                    SELECT COUNT(*) FROM information_schema.tables
                    WHERE upper(table_schema) = '{schema_u}'
                      AND upper(table_name) = '{table_u}'
                """
            row = conn.execute(sql).fetchone()
            return bool(row and row[0] > 0)
    except Exception as e:
        _log_dest_error(cmp_id, connection_id, str(e), "check_table_exists_databricks")
        return False


def check_staging_table_exists(
    destination_engine, table, destination_connection, cmp_id, connection_id, staging_table_name=None
):
    if not staging_table_name:
        raise ValueError(
            f"CRITICAL ERROR: staging_table_name is missing in check_staging_table_exists for table {table}"
        )
    return check_table_exists_databricks(
        destination_engine, staging_table_name, destination_connection, connection_id, cmp_id
    )


def _ddl_columns_from_attributes(attributes_details_field: dict, columns: list[str]) -> list[str]:
    ddl = []
    for col in columns:
        name = _normalize_attr_column_key(col)
        dtype = _lookup_attr_dtype(attributes_details_field, col, default="STRING")
        # Map leftover Snowflake-ish types if any slipped through
        dtype = _normalize_to_databricks_type(dtype)
        ddl.append(f"{_q_ident(name)} {dtype}")
    return ddl


def _normalize_to_databricks_type(dtype: str) -> str:
    raw = str(dtype or "STRING").strip()
    if not raw:
        return "STRING"
    upper = raw.upper()
    if upper.startswith("VARCHAR") or upper.startswith("CHAR"):
        return "STRING"
    if upper.startswith("TIMESTAMP_TZ") or upper.startswith("TIMESTAMP_LTZ") or upper.startswith("TIMESTAMP_NTZ"):
        return "TIMESTAMP"
    if upper.startswith("NUMBER") or upper.startswith("NUMERIC"):
        # NUMBER(38,0) -> BIGINT, else DECIMAL
        m = re.match(r"NUMBER\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", upper)
        if m and int(m.group(2)) == 0:
            return "BIGINT"
        if m:
            return f"DECIMAL({m.group(1)},{m.group(2)})"
        return "DECIMAL(38,6)"
    if upper == "FLOAT":
        return "DOUBLE"
    if upper == "LONG":
        return "BIGINT"
    known_sql = {
        "STRING",
        "BOOLEAN",
        "BIGINT",
        "DOUBLE",
        "TIMESTAMP",
        "DATE",
        "INT",
        "INTEGER",
    }
    if upper in known_sql:
        return "INT" if upper == "INTEGER" else upper
    if upper.startswith("DECIMAL"):
        return raw
    # Dynamics/CDM type names that slipped through (guid, dateTime, …)
    lower_map = {str(k).lower(): v for k, v in field_dtype_conversion.items()}
    if raw.lower() in lower_map:
        return lower_map[raw.lower()]
    return "STRING"


def _coerce_attr_metadata_dict(value) -> dict:
    """Ensure attribute metadata is a mapping (not a DB execute result)."""
    return value if isinstance(value, dict) else {}


def _list_table_column_names(destination_engine, qualified: str) -> set[str]:
    return set(_list_table_column_types(destination_engine, qualified).keys())


def _list_table_column_types(destination_engine, qualified: str) -> dict[str, str]:
    """
    Return {COLUMN_NAME_UPPER: databricks_sql_type} from DESCRIBE TABLE.
    Stops at partition/detail section markers (# ...).
    """
    with destination_engine.connect() as conn:
        rows = conn.execute(f"DESCRIBE TABLE {qualified}").fetchall()
    types: dict[str, str] = {}
    for row in rows:
        if not row or not row[0]:
            continue
        name = str(row[0]).strip()
        if name.startswith("#"):
            break
        dtype = str(row[1]).strip() if len(row) > 1 and row[1] is not None else "STRING"
        types[name.upper()] = _normalize_to_databricks_type(dtype)
    return types


def _sync_base_table_columns(
    destination_engine,
    qualified: str,
    attributes_details_field: dict,
    columns: list[str],
) -> None:
    """Add any missing columns when Dynamics schema evolves across export folders."""
    existing = _list_table_column_names(destination_engine, qualified)
    col_defs = _ddl_columns_from_attributes(attributes_details_field or {}, columns)
    missing = []
    for col_def in col_defs:
        parts = col_def.split(maxsplit=1)
        name = parts[0].strip("`").upper()
        dtype = parts[1] if len(parts) > 1 else "STRING"
        if name not in existing:
            # Databricks ADD COLUMNS expects unquoted identifiers in this clause.
            missing.append(f"{name} {dtype}")
    if not missing:
        return
    alter_sql = f"ALTER TABLE {qualified} ADD COLUMNS ({', '.join(missing)})"
    with destination_engine.connect() as conn:
        conn.execute(alter_sql)
    logger.warning("[ENSURE_BASE] Added columns to %s: %s", qualified, missing)


def ensure_databricks_base_table_exists(
    destination_engine,
    table_name,
    snowflake_schema,
    df,
    attributes_details_field,
    connection_id,
    cmp_id,
    destination_connection=None,
):
    schema = snowflake_schema
    catalog = _catalog_from_config(destination_connection)
    if check_table_exists_databricks(
        destination_engine, table_name, {"schema": schema, "catalog": catalog}, connection_id, cmp_id
    ):
        col_source = (
            list(df.columns)
            if df is not None and hasattr(df, "columns") and len(df.columns) > 0
            else list(_coerce_attr_metadata_dict(attributes_details_field).keys())
        )
        if col_source:
            qualified = _qualified(catalog, schema, table_name)
            _sync_base_table_columns(
                destination_engine,
                qualified,
                attributes_details_field,
                col_source,
            )
        return True
    if not attributes_details_field and (df is None or getattr(df, "empty", True)):
        return False

    col_source = (
        list(df.columns)
        if df is not None and hasattr(df, "columns") and len(df.columns) > 0
        else list(_coerce_attr_metadata_dict(attributes_details_field).keys())
    )
    col_defs = _ddl_columns_from_attributes(attributes_details_field or {}, col_source)
    if not col_defs:
        return False

    qualified = _qualified(catalog, schema, table_name)
    create_sql = (
        f"CREATE TABLE IF NOT EXISTS {qualified} ({', '.join(col_defs)}) "
        f"USING DELTA {DELTA_BASE_TBLPROPERTIES_SQL}"
    )
    with destination_engine.connect() as conn:
        conn.execute(create_sql)
    logger.info("[ENSURE_BASE] Created Databricks table %s", qualified)
    return True


def _dtype_for_column(
    attributes_details_field: dict | None,
    col: str,
    existing_column_types: dict[str, str] | None = None,
) -> str:
    """
    Resolve Databricks SQL type for a column.

    Prefer the existing staging/base column type when present so resumed/
    deferred-merge COPY INTO CASTs match Delta and avoid
    DELTA_FAILED_TO_MERGE_FIELDS. Fall back to Dynamics attribute metadata
    (CamelCase keys are normalized to SNAKE_CASE for lookup).
    """
    key = _normalize_attr_column_key(col)
    attr_raw = _lookup_attr_dtype(attributes_details_field, col, default="")
    attr_dtype = _normalize_to_databricks_type(attr_raw) if attr_raw else ""

    if existing_column_types:
        existing = (
            existing_column_types.get(key)
            or existing_column_types.get(str(col).upper())
            or existing_column_types.get(str(col).upper().replace(" ", "_"))
        )
        if existing:
            existing_norm = _normalize_to_databricks_type(existing)
            # Stale tables created before CamelCase→SNAKE key fix often have
            # dateTime columns as STRING. Prefer attribute TIMESTAMP so new
            # staging CASTs are correct (base may still need drop_and_reload).
            if (
                existing_norm == "STRING"
                and attr_dtype == "TIMESTAMP"
            ):
                logger.warning(
                    "DBX|timestamp_was_string | col=%s | existing=STRING | "
                    "using attribute TIMESTAMP (recreate base if MERGE type-mismatch)",
                    key,
                )
                return "TIMESTAMP"
            if (
                existing_norm in ("INT", "INTEGER", "BIGINT")
                and attr_dtype == "DATE"
            ):
                logger.warning(
                    "DBX|date_was_int | col=%s | existing=%s | "
                    "using attribute DATE for COPY cast",
                    key,
                    existing_norm,
                )
                return "DATE"
            return existing_norm

    if attr_dtype:
        return attr_dtype
    return "STRING"


def _parquet_copy_cast_expr(quoted_name: str, dtype: str) -> str:
    """
    Build a Parquet-safe CAST expression for COPY INTO.

    Parquet files may store Salesforce DATE columns as INT (epoch days) or STRING
    (ISO dates). TIMESTAMP columns may be STRING, epoch millis, or epoch seconds.
    """
    upper = _normalize_to_databricks_type(dtype).upper()
    if upper == "DATE":
        # Staging columns may already be INT (epoch days); TRY_CAST(INT AS DATE) fails in Databricks.
        return (
            f"CASE WHEN {quoted_name} IS NULL THEN NULL "
            f"ELSE COALESCE("
            f"DATE_FROM_UNIX_DATE(TRY_CAST({quoted_name} AS INT)), "
            f"TRY_CAST(CAST({quoted_name} AS STRING) AS DATE)"
            f") END AS {quoted_name}"
        )
    if upper.startswith("TIMESTAMP"):
        return (
            f"CASE WHEN {quoted_name} IS NULL THEN NULL "
            f"ELSE COALESCE("
            f"TRY_CAST({quoted_name} AS TIMESTAMP), "
            f"TIMESTAMP_MILLIS(TRY_CAST({quoted_name} AS BIGINT)), "
            f"TIMESTAMP_SECONDS(TRY_CAST({quoted_name} AS BIGINT))"
            f") END AS {quoted_name}"
        )
    if upper == "BOOLEAN":
        return (
            f"CASE WHEN {quoted_name} IS NULL THEN NULL "
            f"WHEN LOWER(CAST({quoted_name} AS STRING)) IN ('true', '1', 't', 'yes') THEN true "
            f"WHEN LOWER(CAST({quoted_name} AS STRING)) IN ('false', '0', 'f', 'no') THEN false "
            f"ELSE CAST({quoted_name} AS BOOLEAN) END AS {quoted_name}"
        )
    return f"CAST({quoted_name} AS {dtype}) AS {quoted_name}"


def _copy_into_cast_select(
    columns: list[str],
    attributes_details_field: dict | None,
    existing_column_types: dict[str, str] | None = None,
    *,
    file_format: str = "CSV",
) -> str:
    """
    Build SELECT list that casts source columns to staging types.

    CSV (inferSchema=false): everything is STRING — use NULLIF/TRIM then CAST.
    Parquet: columns are already typed — CAST directly (no TRIM) to match staging.
    Prefer ``existing_column_types`` when staging is reused across chunks/resumes.
    """
    exprs: list[str] = []
    fmt = str(file_format or "CSV").strip().upper()
    for col in columns:
        name = _q_ident(str(col).upper())
        dtype = _dtype_for_column(
            attributes_details_field, col, existing_column_types=existing_column_types
        )
        upper = dtype.upper()
        if upper == "STRING" or upper.startswith("VARCHAR") or upper.startswith("CHAR"):
            if fmt == "CSV":
                exprs.append(f"{name} AS {name}")
            else:
                # Parquet string/null → keep as-is for STRING staging cols.
                exprs.append(f"CAST({name} AS STRING) AS {name}")
            continue
        if fmt == "PARQUET":
            exprs.append(_parquet_copy_cast_expr(name, dtype))
        else:
            # Empty CSV cells become NULL so CAST does not fail on ''.
            nullif = f"NULLIF(TRIM({name}), '')"
            exprs.append(f"CAST({nullif} AS {dtype}) AS {name}")
    return ",\n      ".join(exprs)


def _ensure_staging_table(
    destination_engine,
    catalog,
    schema,
    staging_table,
    df,
    attributes_details_field,
):
    """
    Ensure the per-chunk staging Delta table exists with typed columns.

    Creates the table once when missing. On later chunks, only adds missing
    columns — never DROP/TRUNCATE here (truncated after each chunk MERGE).
    """
    col_source = list(df.columns) if df is not None and hasattr(df, "columns") else []
    if not col_source:
        col_source = list((attributes_details_field or {}).keys())
    col_defs = _ddl_columns_from_attributes(attributes_details_field or {}, col_source)
    if not col_defs:
        raise ValueError(f"Cannot create staging table {staging_table}: no columns")
    qualified = _qualified(catalog, schema, staging_table)

    # Reuse existing staging across chunks (truncated after each MERGE). Avoid existence probe
    # that requires cmp_id by DESCRIBE-ing; missing table raises.
    try:
        _list_table_column_names(destination_engine, qualified)
        table_exists = True
    except Exception:
        table_exists = False

    if table_exists:
        _sync_base_table_columns(
            destination_engine,
            qualified,
            attributes_details_field,
            col_source,
        )
        logger.warning(
            "DBX|ensure_staging_reuse | staging=%s | cols=%s",
            qualified,
            len(col_source),
        )
        return qualified

    create_sql = f"CREATE TABLE IF NOT EXISTS {qualified} ({', '.join(col_defs)}) USING DELTA"
    try:
        with destination_engine.connect() as conn:
            conn.execute(create_sql)
    except Exception as create_exc:
        # Parallel workers can race past the DESCRIBE miss; IF NOT EXISTS should
        # cover it, but treat ALREADY_EXISTS as reuse for older warehouses.
        err = str(create_exc).lower()
        if "already_exists" not in err and "already exists" not in err:
            raise
        logger.warning(
            "DBX|ensure_staging_create_race | staging=%s | err=%s",
            qualified,
            create_exc,
        )
    _sync_base_table_columns(
        destination_engine,
        qualified,
        attributes_details_field,
        col_source,
    )
    logger.warning(
        "DBX|ensure_staging_created | staging=%s | cols=%s",
        qualified,
        len(col_defs),
    )
    return qualified


def _sql_literal(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NULL"
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return "NULL"
        return f"TIMESTAMP '{value.isoformat()}'"
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat()}'"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return "NULL"
        return str(value)
    text_val = str(value).replace("'", "''")
    return f"'{text_val}'"


def _normalize_stage_base_url(url: str) -> str:
    """Normalize stage prefix (gs://.../ or /Volumes/.../) for Databricks COPY INTO."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("gcs://"):
        raw = "gs://" + raw[len("gcs://") :]
    if not raw.endswith("/"):
        raw = raw + "/"
    return raw


def _resolve_stage_url(destination_engine) -> str:
    """Resolve GCS prefix for COPY INTO from connection config or env GCS_BUCKET_URL."""
    from_config = ""
    if hasattr(destination_engine, "config") and isinstance(destination_engine.config, dict):
        from_config = str(destination_engine.config.get("gcs_staging_url") or "").strip()
    stage = _normalize_stage_base_url(
        from_config or str(os.environ.get("GCS_BUCKET_URL") or "").strip()
    )
    if not stage:
        raise ValueError(
            "Databricks COPY INTO requires env GCS_BUCKET_URL "
            "(GCS prefix readable via a Unity Catalog external location)."
        )
    if not stage.startswith("gs://"):
        raise ValueError(
            f"Databricks GCS_BUCKET_URL must be a gs:// (or gcs://) prefix, got: {stage!r}"
        )
    return stage


def _staging_method_from_engine(destination_engine) -> str:
    if hasattr(destination_engine, "config") and isinstance(destination_engine.config, dict):
        method = str(destination_engine.config.get("staging_method") or "").strip().lower()
        if method in (STAGING_METHOD_MANAGED_VOLUME, STAGING_METHOD_EXTERNAL_LOCATION):
            return method
        if str(destination_engine.config.get("volume_name") or "").strip():
            return STAGING_METHOD_MANAGED_VOLUME
    return STAGING_METHOD_EXTERNAL_LOCATION


def _volume_client_from_engine(destination_engine, schema: str):
    """Build DatabricksVolumeStagingClient from engine.config + schema."""
    from services.databricks.volume_staging_client import DatabricksVolumeStagingClient

    cfg = getattr(destination_engine, "config", {}) or {}
    return DatabricksVolumeStagingClient(
        server_hostname=str(cfg.get("server_hostname") or cfg.get("host") or ""),
        access_token=str(cfg.get("access_token") or cfg.get("token") or ""),
        catalog=str(cfg.get("catalog") or "").strip(),
        schema=str(schema or cfg.get("schema") or "").strip(),
        volume_name=str(cfg.get("volume_name") or "").strip(),
    )


def _temp_csv_blob_name(table_name: str, connection_id: int) -> str:
    return f"temp_data_dynamics_f&o_{table_name}_{connection_id}.csv"


def _normalize_copy_into_source(file_url: str) -> str:
    """
    Normalize COPY INTO FROM source.

    Accepts:
      - gs:// / gcs:// object URLs (external_location)
      - /Volumes/{catalog}/{schema}/{volume}/... (managed_volume)
      - dbfs:/Volumes/... (normalized to /Volumes/...)
    """
    raw = str(file_url or "").strip()
    if not raw:
        raise ValueError("COPY INTO source URL/path is empty")
    if raw.startswith("gcs://"):
        return "gs://" + raw[len("gcs://") :]
    if raw.startswith("gs://"):
        return raw
    if raw.startswith("dbfs:/Volumes/"):
        return raw[len("dbfs:") :]
    if raw.startswith("/Volumes/"):
        return raw
    raise ValueError(
        "COPY INTO source must be gs://, gcs://, or /Volumes/... path, "
        f"got: {raw!r}"
    )


def _copy_into_staging(
    destination_engine,
    staging_qualified: str,
    file_url: str,
    columns: list[str] | None = None,
    attributes_details_field: dict | None = None,
    existing_column_types: dict[str, str] | None = None,
) -> None:
    """
    Load one staged file into the staging Delta table via Databricks COPY INTO.

    Source may be a GCS URI (external location, typically CSV) or a UC managed
    volume path (Parquet). When ``columns`` is provided, fields are CASTed to the
    typed staging schema in a FROM subquery (avoids DELTA_FAILED_TO_MERGE_FIELDS).
    Prefer ``existing_column_types`` (or DESCRIBE of staging) so resumed staging
    type mismatches do not force the slow INSERT fallback.
    """
    from services.databricks.volume_staging_client import VolumeCopyIntoError

    normalized = _normalize_copy_into_source(file_url)
    is_volume = normalized.startswith("/Volumes/")
    file_format = (
        "PARQUET"
        if str(normalized).lower().endswith(".parquet")
        else "CSV"
    )

    if file_format == "PARQUET":
        format_opts = """COPY_OPTIONS (
      'force' = 'true',
      'mergeSchema' = 'false'
    )"""
    else:
        format_opts = """FORMAT_OPTIONS (
      'header' = 'true',
      'inferSchema' = 'false',
      'delimiter' = ',',
      'quote' = '"',
      'escape' = '"'
    )
    COPY_OPTIONS (
      'force' = 'true',
      'mergeSchema' = 'false'
    )"""

    cast_types = existing_column_types
    if cast_types is None and columns:
        try:
            cast_types = _list_table_column_types(destination_engine, staging_qualified)
        except Exception as type_exc:
            logger.warning(
                "DBX|staging_types_lookup_failed | staging=%s | err=%s",
                staging_qualified,
                type_exc,
            )
            cast_types = None

    col_list = [str(c) for c in (columns or []) if str(c).strip()]
    if col_list:
        select_list = _copy_into_cast_select(
            col_list,
            attributes_details_field,
            existing_column_types=cast_types,
            file_format=file_format,
        )
        from_clause = f"""(
      SELECT
      {select_list}
      FROM '{normalized}'
    )"""
    else:
        from_clause = f"'{normalized}'"

    copy_sql = f"""
    COPY INTO {staging_qualified}
    FROM {from_clause}
    FILEFORMAT = {file_format}
    {format_opts}
    """
    # Typed staging + CAST in SELECT maps source columns → staging types before
    # Delta write. mergeSchema=false keeps COPY INTO from evolving the table.
    logger.info(
        "[COPY INTO] staging=%s from=%s format=%s",
        staging_qualified,
        normalized,
        file_format,
    )
    t0 = time.time()
    try:
        with destination_engine.connect() as conn:
            conn.execute(copy_sql)
    except Exception as exc:
        err_text = str(exc)
        lower = err_text.lower()
        if is_volume and any(
            token in lower
            for token in (
                "does not exist",
                "not found",
                "no such file",
                "path does not exist",
            )
        ):
            raise VolumeCopyIntoError(
                "Databricks COPY INTO could not find the staged volume file "
                f"(upload may have been overwritten/deleted by a concurrent worker, "
                f"or the object was cleaned up early). Source={normalized!r}. "
                f"Original error: {err_text}",
                failure_mode="volume_copy_source_missing",
            ) from exc
        if is_volume and any(
            token in lower
            for token in (
                "volume",
                "permission denied",
                "access denied",
                "cannot access",
                "not authorized",
                "insufficient privileges",
            )
        ):
            raise VolumeCopyIntoError(
                "Databricks COPY INTO failed reading the managed volume staging file. "
                "Confirm the volume exists and the warehouse principal has READ VOLUME. "
                f"Source={normalized!r}. Original error: {err_text}"
            ) from exc
        if any(
            token in lower
            for token in (
                "external location",
                "storage credential",
                "permission denied",
                "access denied",
                "cannot access",
                "not authorized",
            )
        ):
            raise ValueError(
                "Databricks COPY INTO failed: Unity Catalog cannot read the GCS temp CSV. "
                "Create a STORAGE CREDENTIAL + EXTERNAL LOCATION for env GCS_BUCKET_URL "
                "(see k8s/batch-cluster/ensure-batch-databricks-external-location.sql) "
                f"and GRANT READ FILES to the warehouse principal. Original error: {err_text}"
            ) from exc
        raise
    logger.warning(
        "DBX|copy_into_done | staging=%s | from=%s | duration_s=%.3f",
        staging_qualified,
        normalized,
        time.time() - t0,
    )


def _insert_dataframe(destination_engine, qualified_table: str, df: pd.DataFrame, batch_size: int = 500):
    """Debug-only path; production Dynamics→Databricks load uses COPY INTO."""
    if df is None or df.empty:
        return
    columns = [str(c) for c in df.columns]
    col_sql = ", ".join(_q_ident(str(c).upper()) for c in columns)

    with destination_engine.connect() as conn:
        for start in range(0, len(df.index), batch_size):
            chunk = df.iloc[start : start + batch_size]
            values_sql = []
            for _, row in chunk.iterrows():
                literals = [_sql_literal(row[c]) for c in columns]
                values_sql.append(f"({', '.join(literals)})")
            insert_sql = f"INSERT INTO {qualified_table} ({col_sql}) VALUES {', '.join(values_sql)}"
            conn.execute(insert_sql)


# Keep IN (...) batches under warehouse expression-size limits (Snowflake uses 10k).
DATABRICKS_IN_LIST_BATCH_SIZE = 10000


def _iter_id_batches(ids, batch_size: int = DATABRICKS_IN_LIST_BATCH_SIZE):
    unique_ids = list(dict.fromkeys(ids or []))
    size = max(1, int(batch_size or DATABRICKS_IN_LIST_BATCH_SIZE))
    for offset in range(0, len(unique_ids), size):
        yield unique_ids[offset : offset + size]


def _delete_staging_ids_in_batches(
    destination_engine,
    staging_qualified: str,
    delete_ids,
    pk_column: str = "ID",
    batch_size: int = DATABRICKS_IN_LIST_BATCH_SIZE,
) -> int:
    """
    Snowflake parity: before COPY INTO, remove existing staging rows for chunk IDs.

    Keeps staging unique per PK across chunks and safety-interval re-reads so the
    final MERGE never sees multiple source rows for one target key.
    """
    unique_ids = [i for i in dict.fromkeys(delete_ids or []) if i is not None]
    if not unique_ids:
        return 0
    pk = str(pk_column or "ID")
    batches = 0
    with destination_engine.connect() as conn:
        for batch in _iter_id_batches(unique_ids, batch_size=batch_size):
            in_clause = ", ".join(_sql_literal(v) for v in batch)
            delete_sql = f"""
            DELETE FROM {staging_qualified}
            WHERE {_q_ident(pk)} IN ({in_clause})
            """
            conn.execute(delete_sql)
            batches += 1
    logger.warning(
        "DBX|staging_id_replace | staging=%s | pk=%s | ids=%s | batches=%s",
        staging_qualified,
        pk,
        len(unique_ids),
        batches,
    )
    return len(unique_ids)


def _merge_staging_source_sql(
    staging_qualified: str,
    cols: list[str],
    primary_key: str | Sequence[str],
    updated_at: str | None = None,
) -> str:
    """
    Build a MERGE source that is unique on primary key (single or composite).

    Cross-chunk / safety-interval re-reads can leave multiple rows per ID in
    staging; Databricks MERGE rejects that. Keep the latest SINK_MODIFIED_ON
    (same rule as per-chunk pandas dedupe / Dynamics→Snowflake).
    """
    pks = _normalize_merge_primary_keys(primary_key)
    select_cols = ", ".join(_q_ident(c) for c in cols)
    order_col = None
    if any(str(c).upper() == "SINK_MODIFIED_ON" for c in cols):
        order_col = "SINK_MODIFIED_ON"
    elif updated_at and any(str(c).lower() == str(updated_at).lower() for c in cols):
        order_col = str(updated_at)
    if order_col:
        order_expr = f"{_q_ident(order_col)} DESC NULLS LAST"
    else:
        order_expr = ", ".join(_q_ident(pk) for pk in pks)
    partition_by = ", ".join(_q_ident(pk) for pk in pks)
    return f"""(
      SELECT {select_cols}
      FROM {staging_qualified}
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY {partition_by}
        ORDER BY {order_expr}
      ) = 1
    )"""


def _merge_staging_to_base(
    destination_engine,
    catalog,
    schema,
    base_table,
    staging_table,
    columns,
    primary_key,
    updated_at,
    deleted_at,
):
    target = _qualified(catalog, schema, base_table)
    source = _qualified(catalog, schema, staging_table)
    pks = _normalize_merge_primary_keys(primary_key)
    pk_upper = {pk.upper() for pk in pks}
    cols = [str(c) for c in columns]
    staging_cols = _list_table_column_names(destination_engine, source)
    base_cols = _list_table_column_names(destination_engine, target)
    if not cols and staging_cols:
        cols = sorted(staging_cols)
    elif not cols and base_cols:
        cols = sorted(base_cols)
    if staging_cols:
        cols = [c for c in cols if str(c).upper() in staging_cols]
    if base_cols:
        cols = [c for c in cols if str(c).upper() in base_cols]
    if not cols:
        raise ValueError(
            f"No overlapping columns between staging and base table for merge into {target}"
        )

    from services.dynamics.source_common import (
        canonical_meta_column,
        meta_column_name_variants,
    )

    sync_col = canonical_meta_column(updated_at, "_FIVETRAN_SYNCED")
    del_meta_col = canonical_meta_column(deleted_at, "_FIVETRAN_DELETED")
    meta_upper = meta_column_name_variants(updated_at, deleted_at)
    cols = [c for c in cols if str(c).upper() not in meta_upper or str(c).upper() in {sync_col, del_meta_col}]

    _sync_base_table_columns(
        destination_engine,
        target,
        {
            sync_col: "TIMESTAMP",
            del_meta_col: "BOOLEAN",
        },
        [sync_col, del_meta_col],
    )

    update_cols = [
        c
        for c in cols
        if c.upper() not in pk_upper
        and c.upper() not in meta_upper
        and c.upper() not in {"IS_DELETE", "IS_DELETED", "ISDELETED"}
        and c.upper() != "SINK_MODIFIED_ON"
    ]

    set_parts = []
    seen_set_cols: set[str] = set()

    def _append_set(col_name: str, expr: str) -> None:
        key = str(col_name).upper()
        if key in seen_set_cols:
            return
        seen_set_cols.add(key)
        set_parts.append(expr)

    upper_cols = {str(c).upper(): str(c) for c in cols}
    resolved_deleted = None
    for candidate in ("IS_DELETE", "IS_DELETED", "ISDELETED"):
        if candidate in upper_cols:
            resolved_deleted = upper_cols[candidate]
            break

    if resolved_deleted:
        delete_predicate = (
            f"COALESCE(CAST(source.{_q_ident(resolved_deleted)} AS BOOLEAN), false)"
        )
    else:
        delete_predicate = "false"

    for col in update_cols:
        _append_set(
            col,
            f"target.{_q_ident(col)} = CASE WHEN {delete_predicate} THEN target.{_q_ident(col)} ELSE source.{_q_ident(col)} END",
        )
    if resolved_deleted:
        _append_set(
            resolved_deleted,
            f"target.{_q_ident(resolved_deleted)} = source.{_q_ident(resolved_deleted)}",
        )
    if any(c.upper() == "SINK_MODIFIED_ON" for c in cols):
        _append_set(
            "SINK_MODIFIED_ON",
            f"target.{_q_ident('SINK_MODIFIED_ON')} = source.{_q_ident('SINK_MODIFIED_ON')}",
        )

    _append_set(sync_col, f"target.{_q_ident(sync_col)} = current_timestamp()")
    if resolved_deleted:
        _append_set(
            del_meta_col,
            f"target.{_q_ident(del_meta_col)} = COALESCE(CAST(source.{_q_ident(resolved_deleted)} AS BOOLEAN), false)",
        )
    else:
        _append_set(del_meta_col, f"target.{_q_ident(del_meta_col)} = false")

    business_cols = [c for c in cols if str(c).upper() not in meta_upper]
    insert_cols = ", ".join(
        _q_ident(c) for c in business_cols + [sync_col, del_meta_col]
    )
    insert_vals = ", ".join(
        [f"source.{_q_ident(c)}" for c in business_cols]
        + [
            "current_timestamp()",
            (
                f"COALESCE(CAST(source.{_q_ident(resolved_deleted)} AS BOOLEAN), false)"
                if resolved_deleted
                else "false"
            ),
        ]
    )
    source_sql = _merge_staging_source_sql(
        source, cols, pks, updated_at=updated_at
    )
    on_clause = " AND ".join(
        f"target.{_q_ident(pk)} = source.{_q_ident(pk)}" for pk in pks
    )

    merge_sql = f"""
    MERGE INTO {target} AS target
    USING {source_sql} AS source
    ON {on_clause}
    WHEN MATCHED THEN UPDATE SET {', '.join(set_parts)}
    WHEN NOT MATCHED AND NOT ({delete_predicate}) THEN INSERT ({insert_cols}) VALUES ({insert_vals})
    """
    logger.warning(
        "DBX|merge_start | base=%s | staging=%s | pk=%s | cols=%s | dedupe=qualify",
        target,
        source,
        ",".join(pks),
        len(cols),
    )
    t0 = time.time()
    with destination_engine.connect() as conn:
        conn.execute(merge_sql)
        conn.execute(f"TRUNCATE TABLE {source}")
    logger.warning(
        "DBX|merge_done | base=%s | staging=%s | duration_s=%.3f",
        target,
        source,
        time.time() - t0,
    )


def _ds_staging_table_pattern(table_name: str) -> re.Pattern[str]:
    tbl_u = str(table_name or "").strip().upper()
    return re.compile(rf"^DS_{re.escape(tbl_u)}_STAGING_[0-9]{{8}}_[0-9]{{6}}$", re.I)


def list_ds_staging_table_names(
    engine,
    schema: str,
    table_name: str,
    catalog: str | None = None,
) -> list[str]:
    """List Databricks staging table names for a source table (DS_{TABLE}_STAGING_*)."""
    if not engine or not schema or not table_name:
        return []
    cat = catalog
    if cat is None and hasattr(engine, "config"):
        cat = (engine.config.get("catalog") or "").strip() or None
    schema_u = schema.strip().upper()
    pattern = _ds_staging_table_pattern(table_name)
    names: list[str] = []
    try:
        with engine.connect() as con:
            if cat:
                rows = con.execute(
                    f"""
                    SELECT table_name FROM `{cat}`.information_schema.tables
                    WHERE upper(table_schema) = '{schema_u}'
                    """
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT table_name FROM information_schema.tables
                    WHERE upper(table_schema) = '{schema_u}'
                    """
                ).fetchall()
            for (tname,) in rows:
                if tname and pattern.match(str(tname)):
                    names.append(str(tname))
    except Exception as exc:
        logger.warning(
            "DBX|list_staging_failed | table=%s schema=%s err=%s",
            table_name,
            schema,
            exc,
        )
    return sorted(names)


def flush_orphan_staging_tables(
    destination_engine,
    schema: str,
    base_table_name: str,
    primary_key: str,
    columns: list[str],
    updated_at: str | None,
    deleted_at: str | None,
    keep_staging_name: str | None = None,
) -> int:
    """
    Safety flush: MERGE any non-empty DS_* staging tables into base, then DROP them.

    Used on resume after failure and for legacy bloated staging cleanup.
    Returns number of staging tables dropped.
    """
    catalog = None
    if hasattr(destination_engine, "config"):
        catalog = (destination_engine.config.get("catalog") or "").strip() or None
    keep_norm = (keep_staging_name or "").strip().upper()
    dropped = 0
    for staging_name in list_ds_staging_table_names(
        destination_engine, schema, base_table_name, catalog=catalog
    ):
        if keep_norm and staging_name.upper() == keep_norm:
            continue
        staging_q = _qualified(catalog, schema, staging_name)
        row_count = _count_qualified_table_rows(destination_engine, staging_q)
        if row_count and row_count > 0:
            logger.warning(
                "DBX|orphan_staging_flush | base=%s | staging=%s | rows=%s",
                base_table_name,
                staging_name,
                row_count,
            )
            try:
                _merge_staging_to_base(
                    destination_engine,
                    catalog,
                    schema,
                    base_table_name,
                    staging_name,
                    columns,
                    primary_key or "ID",
                    updated_at,
                    deleted_at,
                )
            except Exception as merge_exc:
                logger.warning(
                    "DBX|orphan_staging_flush_failed | staging=%s | err=%s",
                    staging_name,
                    merge_exc,
                )
                raise
        try:
            drop_staging_table(destination_engine, staging_name, schema, catalog=catalog)
            dropped += 1
        except Exception as drop_exc:
            logger.warning(
                "DBX|orphan_staging_drop_failed | staging=%s | err=%s",
                staging_name,
                drop_exc,
            )
    return dropped


def _resolve_merge_timestamp_columns(
    columns: list[str],
    updated_at: str | None,
    deleted_at: str | None,
) -> tuple[str | None, str | None]:
    upper_cols = {str(c).upper(): str(c) for c in columns}
    deleted_col = None
    if deleted_at and str(deleted_at).upper() in upper_cols:
        deleted_col = upper_cols[str(deleted_at).upper()]
    else:
        for candidate in ("_FIVETRAN_DELETED", "IS_DELETED", "IS_DELETE", "ISDELETED"):
            if candidate in upper_cols:
                deleted_col = upper_cols[candidate]
                break
    if not deleted_col:
        deleted_col = deleted_at or "_FIVETRAN_DELETED"
    from services.dynamics.source_common import canonical_meta_column

    sync_col = canonical_meta_column(updated_at, "_FIVETRAN_SYNCED")
    if sync_col in upper_cols:
        updated_col = upper_cols[sync_col]
    else:
        updated_col = next(
            (
                upper_cols[k]
                for k in upper_cols
                if k.endswith("UPDATED_AT") or k == "UPDATED_AT"
            ),
            upper_cols.get("SINK_MODIFIED_ON", "SINK_MODIFIED_ON"),
        )
    return updated_col, deleted_col


def copy_function(
    df,
    table_name,
    flag,
    destination_engine,
    snowflake_schema,
    snowflake_table_formatted,
    snowflake_table_formatted_original,
    is_integration_checked,
    extra_field,
    table_exist,
    modified_fields,
    existing_fields,
    attributes_details_field,
    connection_id,
    primary_key,
    snowflake_database,
    cmp_id,
    migration_session_id=None,
    job_name=None,
    staging_table_name=None,
    usr_id=None,
    session=None,
    folder_checkpoint=None,
    safety_interval_minutes=None,
    safety_interval_seconds=None,
    staging_blob_name=None,
    updated_at=None,
    deleted_at=None,
    merged_rows_so_far: int | None = None,
):
    """
    Load one chunk: upload CSV → COPY INTO staging → MERGE into base → TRUNCATE staging.

    Checkpoint (last_synced + migration_record.staging_records_count) is written only
    after successful MERGE+TRUNCATE, not after COPY alone.

    ``staging_blob_name`` optionally overrides the GCS/volume object name so
    Salesforce and Dynamics jobs do not overwrite each other.
    """
    # migration_record / source_table_selection use the connector object name
    # (e.g. FeedItem), not the normalized Databricks table id (FEED_ITEM).
    migration_table_name = (
        str(snowflake_table_formatted_original).strip()
        if snowflake_table_formatted_original
        else table_name
    )
    row_count = len(df.index) if df is not None and hasattr(df, "index") else 0
    staging_method = _staging_method_from_engine(destination_engine)
    logger.warning(
        "DBX|copy_function_start | table=%s | connection_id=%s | rows=%s | "
        "staging=%s | staging_method=%s | table_exist=%s | session=%s | job=%s | blob=%s",
        table_name,
        connection_id,
        row_count,
        staging_table_name,
        staging_method,
        table_exist,
        migration_session_id,
        (job_name or "")[:120],
        staging_blob_name,
    )

    if not staging_table_name:
        raise ValueError(f"CRITICAL ERROR: staging_table_name is missing in copy_function for {table_name}")

    schema = snowflake_schema
    catalog = None
    if hasattr(destination_engine, "config"):
        catalog = (destination_engine.config.get("catalog") or "").strip() or None

    destination_connection = {"schema": schema, "catalog": catalog}
    if attributes_details_field:
        # Normalize CamelCase Dynamics keys → SNAKE_CASE so DDL/CAST lookups
        # match transformed DataFrame columns (avoids dateTime → STRING).
        attributes_details_field = {
            _normalize_attr_column_key(k): _normalize_to_databricks_type(v)
            for k, v in _coerce_attr_metadata_dict(attributes_details_field).items()
        }

    extra_fields_list = extra_field if isinstance(extra_field, list) else []
    _validate_schema_evolution(
        table_name,
        modified_fields,
        extra_fields_list,
        attributes_details_field,
    )

    ensure_databricks_base_table_exists(
        destination_engine,
        table_name,
        schema,
        df,
        attributes_details_field,
        connection_id,
        cmp_id,
        destination_connection=destination_connection,
    )
    logger.warning(
        "DBX|copy_function_base_table_ready | table=%s | schema=%s | catalog=%s",
        table_name,
        schema,
        catalog,
    )

    if df is not None and not df.empty:
        df = df.copy()
        df.columns = [str(c).upper() for c in df.columns]

    staging_qualified = _ensure_staging_table(
        destination_engine,
        catalog,
        schema,
        staging_table_name,
        df,
        attributes_details_field,
    )
    logger.warning(
        "DBX|copy_function_staging_ready | staging=%s | rows=%s",
        staging_qualified,
        row_count,
    )

    if df is not None and not df.empty:
        use_volume = staging_method == STAGING_METHOD_MANAGED_VOLUME
        staged_file_url = None
        volume_client = None
        delete_gcs = None

        if use_volume:
            volume_client = _volume_client_from_engine(destination_engine, schema)
            logger.warning(
                "DBX|volume_upload_start | table=%s | connection_id=%s | rows=%s | volume=%s",
                table_name,
                connection_id,
                row_count,
                volume_client.volume_root,
            )
            t_upload = time.time()
            try:
                staged_file_url = volume_client.upload_dataframe(
                    df,
                    table_name,
                    connection_id,
                    overwrite=True,
                    blob_name=staging_blob_name,
                    column_types=attributes_details_field,
                )
            except Exception as upload_exc:
                logger.error(
                    "DBX|volume_upload_failed | table=%s | connection_id=%s | rows=%s | "
                    "duration_s=%.3f | failure_mode=%s | err=%s",
                    table_name,
                    connection_id,
                    row_count,
                    time.time() - t_upload,
                    getattr(upload_exc, "failure_mode", "volume_upload_failed"),
                    upload_exc,
                )
                _log_databricks_issue(
                    cmp_id,
                    connection_id,
                    str(upload_exc),
                    "volume_upload_failed",
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    usr_id=usr_id,
                    session=session,
                    tool_log_status="E",
                )
                raise
            logger.warning(
                "DBX|volume_upload_ok | table=%s | connection_id=%s | url=%s | duration_s=%.3f",
                table_name,
                connection_id,
                staged_file_url,
                time.time() - t_upload,
            )
        else:
            # Fail fast if GCS_BUCKET_URL is not configured for COPY INTO.
            stage_url = _resolve_stage_url(destination_engine)
            logger.warning(
                "DBX|gcs_upload_start | table=%s | connection_id=%s | rows=%s | stage_url=%s",
                table_name,
                connection_id,
                row_count,
                stage_url,
            )

            from services.dynamics.source_common import (
                delete_csv_from_bucket,
                upload_to_bucket,
            )

            delete_gcs = delete_csv_from_bucket
            t_upload = time.time()
            try:
                staged_file_url = upload_to_bucket(
                    df, table_name, connection_id, blob_name=staging_blob_name
                )
            except Exception as upload_exc:
                logger.error(
                    "DBX|gcs_upload_failed | table=%s | connection_id=%s | rows=%s | "
                    "duration_s=%.3f | err=%s",
                    table_name,
                    connection_id,
                    row_count,
                    time.time() - t_upload,
                    upload_exc,
                )
                raise
            logger.warning(
                "DBX|gcs_upload_ok | table=%s | connection_id=%s | url=%s | duration_s=%.3f",
                table_name,
                connection_id,
                staged_file_url,
                time.time() - t_upload,
            )

        try:
            t_copy = time.time()
            try:
                _copy_into_staging(
                    destination_engine,
                    staging_qualified,
                    staged_file_url,
                    columns=list(df.columns),
                    attributes_details_field=attributes_details_field,
                )
                logger.warning(
                    "DBX|copy_into_ok | table=%s | staging=%s | duration_s=%.3f",
                    table_name,
                    staging_qualified,
                    time.time() - t_copy,
                )
            except Exception as copy_exc:
                _log_databricks_issue(
                    cmp_id,
                    connection_id,
                    (
                        f"COPY INTO failed for table '{table_name}' ({row_count} rows). "
                        f"Error: {copy_exc}"
                    ),
                    "copy_into_failed",
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    usr_id=usr_id,
                    session=session,
                    tool_log_status="E",
                )
                raise

            updated_col, deleted_col = _resolve_merge_timestamp_columns(
                list(df.columns), updated_at, deleted_at
            )
            t_merge = time.time()
            try:
                _merge_staging_to_base(
                    destination_engine,
                    catalog,
                    schema,
                    table_name,
                    staging_table_name,
                    list(df.columns),
                    primary_key or "ID",
                    updated_col,
                    deleted_col,
                )
            except Exception as merge_exc:
                _log_databricks_issue(
                    cmp_id,
                    connection_id,
                    (
                        f"Per-chunk MERGE failed for table '{table_name}' "
                        f"({row_count} rows). Error: {merge_exc}"
                    ),
                    "per_chunk_merge_failed",
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    usr_id=usr_id,
                    session=session,
                    tool_log_status="E",
                )
                raise
            logger.warning(
                "DBX|per_chunk_merge_ok | table=%s | staging=%s | duration_s=%.3f",
                table_name,
                staging_qualified,
                time.time() - t_merge,
            )

            remember_chunk_watermark(table_name, df)
            if merged_rows_so_far is None and all(
                [migration_session_id, job_name, migration_table_name, connection_id]
            ):
                from utils.load_checkpoint import get_migration_record_staging_count

                merged_rows_so_far = get_migration_record_staging_count(
                    connection_id=connection_id,
                    table_name=migration_table_name,
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                )
            cumulative_merged = int(merged_rows_so_far or 0) + int(row_count or 0)

            logger.warning(
                "DBX|copy_function_chunk_done | table=%s | migration_table=%s | "
                "connection_id=%s | chunk_rows=%s | cumulative_merged=%s | merge=per_chunk",
                table_name,
                migration_table_name,
                connection_id,
                row_count,
                cumulative_merged,
            )
            if all([migration_session_id, job_name, migration_table_name, connection_id]):
                try:
                    from utils.simple_progress_tracker import SimpleProgressTracker

                    SimpleProgressTracker.set_staging_count(
                        connection_id=connection_id,
                        migration_session_id=migration_session_id,
                        job_name=job_name,
                        table_name=migration_table_name,
                        count_value=int(cumulative_merged),
                    )
                except Exception as progress_exc:
                    logger.warning(
                        "DBX|progress_update_failed | table=%s | session=%s | rows=%s | err=%s",
                        migration_table_name,
                        migration_session_id,
                        cumulative_merged,
                        progress_exc,
                    )
                try:
                    from utils.load_checkpoint import update_load_checkpoint

                    update_load_checkpoint(
                        connection_id=connection_id,
                        table_name=migration_table_name,
                        folder_checkpoint=folder_checkpoint,
                        merged_records_count=cumulative_merged,
                        migration_session_id=migration_session_id,
                        job_name=job_name,
                        safety_interval_minutes=safety_interval_minutes,
                        safety_interval_seconds=safety_interval_seconds,
                    )
                except Exception as checkpoint_exc:
                    logger.warning(
                        "DBX|load_checkpoint_update_failed | table=%s | session=%s | err=%s",
                        table_name,
                        migration_session_id,
                        checkpoint_exc,
                    )
        except Exception as load_exc:
            logger.error(
                "DBX|databricks_load_failed | table=%s | staging=%s | staged_url=%s | "
                "staging_method=%s | failure_mode=%s | err=%s",
                table_name,
                staging_qualified,
                staged_file_url,
                staging_method,
                getattr(load_exc, "failure_mode", "databricks_load_failed"),
                load_exc,
            )
            raise
        finally:
            try:
                if use_volume and volume_client is not None and staged_file_url:
                    # Delete the exact unique object this chunk uploaded (not a
                    # shared per-table name — that raced across workers).
                    volume_client.delete_absolute(staged_file_url)
                elif delete_gcs is not None:
                    delete_gcs(table_name, connection_id, cmp_id)
            except Exception as cleanup_exc:
                logger.warning(
                    "DBX|staging_temp_csv_cleanup_failed | table=%s | connection_id=%s | "
                    "staging_method=%s | err=%s",
                    table_name,
                    connection_id,
                    staging_method,
                    cleanup_exc,
                )
    else:
        logger.warning(
            "DBX|copy_function_skip_empty | table=%s | connection_id=%s | reason=no_rows",
            table_name,
            connection_id,
        )

    return staging_table_name, df


def _count_qualified_table_rows(destination_engine, qualified: str) -> int | None:
    """Return COUNT(*) for a fully qualified Databricks table, or None on failure."""
    if not destination_engine or not qualified:
        return None
    try:
        with destination_engine.connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {qualified}").fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning(
            "DBX|staging_count_failed | table=%s | err=%s",
            qualified,
            exc,
        )
        return None


def merge_into_snowflake(
    destination_engine,
    snowflake_schema,
    table_name,
    primary_key,
    df,
    updated_at,
    deleted_at,
    cmp_id,
    connection_id,
    staging_table_name=None,
    table_detail_log=None,
    migration_session_id=None,
    job_name=None,
    final_records_count=None,
):
    """
    Safety / legacy end-of-table MERGE when staging still has rows (orphan flush).

    Normal loads use per-chunk MERGE in copy_function. This remains for edge cases
    and tests that call merge_into_databricks directly.
    """
    if not staging_table_name:
        raise ValueError(
            f"CRITICAL ERROR: staging_table_name is missing in merge_into_databricks for table {table_name}"
        )

    catalog = None
    if hasattr(destination_engine, "config"):
        catalog = (destination_engine.config.get("catalog") or "").strip() or None
    schema = snowflake_schema

    cols = []
    if df is not None and hasattr(df, "columns") and len(df.columns) > 0:
        cols = [str(c) for c in df.columns]
    else:
        staging_q = _qualified(catalog, schema, staging_table_name)
        cols = sorted(_list_table_column_names(destination_engine, staging_q) or [])

    if not cols:
        if table_detail_log:
            try:
                table_detail_log.log(
                    f"merge_into_databricks skipped reason=no_columns staging={staging_table_name!r}"
                )
            except Exception:
                pass
        logger.warning(
            "[MERGE] end-of-job merge skipped table=%s reason=no_columns staging=%s",
            table_name,
            staging_table_name,
        )
        return

    upper_cols = {c.upper(): c for c in cols}
    deleted_col = None
    if deleted_at and str(deleted_at).upper() in upper_cols:
        deleted_col = upper_cols[str(deleted_at).upper()]
    else:
        for candidate in ("_FIVETRAN_DELETED", "IS_DELETED", "IS_DELETE", "ISDELETED"):
            if candidate in upper_cols:
                deleted_col = upper_cols[candidate]
                break
    if not deleted_col:
        deleted_col = deleted_at or "_FIVETRAN_DELETED"
    updated_col = next(
        (upper_cols[k] for k in upper_cols if k.endswith("UPDATED_AT") or k == "UPDATED_AT"),
        upper_cols.get("SINK_MODIFIED_ON", "SINK_MODIFIED_ON"),
    )
    if updated_at and str(updated_at).upper() in upper_cols:
        updated_col = upper_cols[str(updated_at).upper()]

    base_qualified = _qualified(catalog, schema, table_name)
    _sync_base_table_columns(
        destination_engine,
        base_qualified,
        None,
        cols,
    )
    if table_detail_log:
        try:
            table_detail_log.log(
                f"merge_into_databricks start staging={staging_table_name!r} cols={len(cols)}"
            )
        except Exception:
            pass

    _merge_staging_to_base(
        destination_engine,
        catalog,
        schema,
        table_name,
        staging_table_name,
        cols,
        primary_key or "ID",
        updated_at=updated_col,
        deleted_at=deleted_col,
    )
    logger.warning(
        "[MERGE] end-of-job merge done table=%s staging=%s",
        table_name,
        staging_table_name,
    )
    if table_detail_log:
        try:
            table_detail_log.log(
                f"merge_into_databricks done staging={staging_table_name!r}"
            )
        except Exception:
            pass

    try:
        from utils.load_checkpoint import finalize_migration_record

        finalize_migration_record(
            connection_id=connection_id,
            table_name=table_name,
            migration_session_id=migration_session_id,
            job_name=job_name,
            final_records_count=(
                int(final_records_count)
                if final_records_count is not None
                else None
            ),
        )
    except Exception as finalize_exc:
        logger.warning(
            "DBX|finalize_migration_record_failed | table=%s | err=%s",
            table_name,
            finalize_exc,
        )

    # Keep Overview Records in sync with other connectors: final migrated count,
    # not 0 (WS clients listen to staging_records_count).
    if all([migration_session_id, job_name, table_name, connection_id]):
        try:
            from utils.simple_progress_tracker import SimpleProgressTracker

            SimpleProgressTracker.set_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                count_value=int(final_records_count or 0),
            )
        except Exception as progress_exc:
            logger.warning(
                "DBX|set_final_count_after_merge_failed | table=%s | err=%s",
                table_name,
                progress_exc,
            )


def apply_data_max_watermark(last_checked_dates: dict, table_name: str) -> dict:
    """Replace folder-based cursor with max(SINK_MODIFIED_ON) when available."""
    out = dict(last_checked_dates or {})
    wm = peek_watermark(table_name)
    if wm:
        out[table_name] = wm
    return out


def check_primary_key_exists(destination_engine, table_name):
    """Databricks/Unity Catalog may not expose PK the same way; treat as absent."""
    try:
        query = f"""
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE upper(table_name) = '{str(table_name).upper()}'
          AND constraint_type = 'PRIMARY KEY'
        """
        with destination_engine.connect() as conn:
            result = conn.execute(query).fetchone()
        return result is not None
    except Exception:
        return False


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
        raise ValueError(
            f"CRITICAL ERROR: staging_table_name is missing in retrieve_snowflake_recids for table {table_name}"
        )
    if not ids_to_check:
        return []

    catalog = None
    if hasattr(engine, "config"):
        catalog = (engine.config.get("catalog") or "").strip() or None
    qualified = _qualified(catalog, schema, staging_table_name)
    found = []
    unique_ids = list(dict.fromkeys(ids_to_check))
    batch_size = 1000
    with engine.connect() as conn:
        for offset in range(0, len(unique_ids), batch_size):
            batch = unique_ids[offset : offset + batch_size]
            in_clause = ", ".join("'" + str(i).replace("'", "''") + "'" for i in batch)
            sql = f"SELECT {_q_ident('ID')} FROM {qualified} WHERE {_q_ident('ID')} IN ({in_clause})"
            rows = conn.execute(sql).fetchall()
            found.extend(row[0] for row in rows)
    return found


def retrieve_snowflake_ids_delete(engine, table_name, schema, ids_to_check, connection_id, cmp_id):
    if not ids_to_check:
        return []
    catalog = None
    if hasattr(engine, "config"):
        catalog = (engine.config.get("catalog") or "").strip() or None
    qualified = _qualified(catalog, schema, table_name)
    found = []
    unique_ids = list(dict.fromkeys(ids_to_check))
    batch_size = 1000
    try:
        with engine.connect() as conn:
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                in_clause = ", ".join("'" + str(i).replace("'", "''") + "'" for i in batch)
                sql = f"SELECT {_q_ident('ID')} FROM {qualified} WHERE {_q_ident('ID')} IN ({in_clause})"
                rows = conn.execute(sql).fetchall()
                found.extend(row[0] for row in rows)
    except Exception as e:
        _log_dest_error(cmp_id, connection_id, str(e), "retrieve_snowflake_ids_delete")
        return []
    return found


def drop_staging_table(engine, staging_table: str, schema: str = "default", catalog: str | None = None):
    """Drop a Databricks staging table (backtick-qualified)."""
    cat = catalog
    if cat is None and hasattr(engine, "config"):
        cat = (engine.config.get("catalog") or "").strip() or None
    qualified = _qualified(cat, schema, staging_table)
    try:
        with engine.connect() as con:
            con.execute(f"DROP TABLE IF EXISTS {qualified}")
            logger.info("Dropped Databricks staging table: %s", qualified)
    except Exception as e:
        logger.error("Failed to drop staging table %s: %s", qualified, e)


def drop_databricks_table(
    engine,
    table_name: str,
    schema: str = "default",
    catalog: str | None = None,
) -> dict:
    """Drop a Databricks base table (used by drop-and-reload)."""
    cat = catalog
    if cat is None and hasattr(engine, "config"):
        cat = (engine.config.get("catalog") or "").strip() or None
    qualified = _qualified(cat, schema, table_name)
    try:
        with engine.connect() as con:
            con.execute(f"DROP TABLE IF EXISTS {qualified}")
        logger.info("Dropped Databricks table: %s", qualified)
        return {"success": True, "message": f"Dropped {qualified}"}
    except Exception as e:
        logger.error("Failed to drop Databricks table %s: %s", qualified, e)
        return {"success": False, "message": f"Failed to drop {qualified}: {e}"}


# Preferred name for orchestrators (merge_into_snowflake kept for call-site compat).
merge_into_databricks = merge_into_snowflake


def drop_existing_ds_staging_tables_for_table(
    engine,
    schema: str,
    table_name: str,
    keep_staging_name=None,
    migration_session_id=None,
) -> int:
    """Best-effort cleanup of prior DS_*_STAGING_* tables in Databricks."""
    from utils.snowflake_utils import (
        collect_protected_staging_names_for_table,
        lookup_session_staging_name,
        normalize_name,
    )

    if not engine or not schema or not table_name:
        return 0
    catalog = None
    if hasattr(engine, "config"):
        catalog = (engine.config.get("catalog") or "").strip() or None
    schema_u = schema.strip().upper()
    tbl_u = table_name.strip().upper()
    pattern = re.compile(rf"^DS_{re.escape(tbl_u)}_STAGING_[0-9]{{8}}_[0-9]{{6}}$", re.I)

    protected = collect_protected_staging_names_for_table(table_name)
    if keep_staging_name:
        protected.add(normalize_name(keep_staging_name))
    remembered = lookup_session_staging_name(migration_session_id, table_name)
    if remembered:
        protected.add(normalize_name(remembered))

    dropped = 0
    skipped_protected = 0
    try:
        with engine.connect() as con:
            if catalog:
                rows = con.execute(
                    f"""
                    SELECT table_name FROM `{catalog}`.information_schema.tables
                    WHERE upper(table_schema) = '{schema_u}'
                    """
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT table_name FROM information_schema.tables
                    WHERE upper(table_schema) = '{schema_u}'
                    """
                ).fetchall()
            for (tname,) in rows:
                if not tname or not pattern.match(str(tname)):
                    continue
                norm = normalize_name(tname)
                if norm in protected:
                    skipped_protected += 1
                    continue
                qualified = _qualified(catalog, schema, tname)
                con.execute(f"DROP TABLE IF EXISTS {qualified}")
                dropped += 1
        if dropped or skipped_protected:
            logger.warning(
                "Databricks staging cleanup | dropped=%s skipped_protected=%s | "
                "table=%s schema=%s session=%s",
                dropped,
                skipped_protected,
                table_name,
                schema_u,
                migration_session_id,
            )
    except Exception as e:
        logger.error(
            "Databricks staging cleanup failed table=%s schema=%s err=%s",
            table_name,
            schema,
            e,
        )
    return dropped
