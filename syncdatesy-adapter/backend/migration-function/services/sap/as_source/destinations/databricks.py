"""
SAP OData → Databricks type / DDL helpers.

Maps Edm / internal Datasynchor types to Delta SQL types and builds the
``attributes_details_field`` map consumed by ``load_helpers.copy_function``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.snowflake_utils import normalize_name

logger = logging.getLogger(__name__)

# Edm.* → Databricks SQL
EDM_TO_DATABRICKS: Dict[str, str] = {
    "Edm.String": "STRING",
    "Edm.Boolean": "BOOLEAN",
    "Edm.Byte": "SMALLINT",
    "Edm.SByte": "SMALLINT",
    "Edm.Int16": "SMALLINT",
    "Edm.Int32": "INT",
    "Edm.Int64": "BIGINT",
    "Edm.Single": "FLOAT",
    "Edm.Double": "DOUBLE",
    "Edm.Decimal": "DECIMAL(38,10)",
    "Edm.DateTime": "TIMESTAMP",
    "Edm.DateTimeOffset": "TIMESTAMP",
    "Edm.Time": "STRING",
    "Edm.Guid": "STRING",
    "Edm.Binary": "BINARY",
    "Edm.Date": "DATE",
}

# Internal dataType strings from services.sap.source.EDM_TYPE_MAPPING
DATATYPE_TO_DATABRICKS: Dict[str, str] = {
    "BOOLEAN": "BOOLEAN",
    "NVARCHAR": "STRING",
    "DECIMAL": "DECIMAL(38,10)",
    "TIMESTAMP": "TIMESTAMP",
    "INSTANT": "TIMESTAMP",
    "STRING": "STRING",
    "BINARY": "BINARY",
    "DOUBLE": "DOUBLE",
    "FLOAT": "FLOAT",
    "SHORT": "SMALLINT",
    "INTEGER": "INT",
    "LONG": "BIGINT",
}

# ABAP / DTP JSON field datatype → Databricks
ABAP_TO_DATABRICKS: Dict[str, str] = {
    "CURR": "DECIMAL",
    "QUAN": "DECIMAL",
    "DEC": "DECIMAL",
    "FLTP": "DOUBLE",
    "DF16": "DECIMAL",
    "DF34": "DECIMAL",
    "INT": "BIGINT",
    "INT1": "SMALLINT",
    "INT2": "SMALLINT",
    "INT4": "INT",
    "INT8": "BIGINT",
    "INTEGER": "BIGINT",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "DATETIME": "TIMESTAMP",
    "DATS": "STRING",
    "TIMS": "STRING",
    "CHAR": "STRING",
    "NUMC": "STRING",
    "STRING": "STRING",
    "RAW": "BINARY",
}


def sap_staging_blob_name(
    table_name: str, connection_id: int, *, parquet: bool = False
) -> str:
    """GCS/volume object name isolated from Salesforce / Dynamics staging blobs."""
    ext = "parquet" if parquet else "csv"
    return f"temp_data_sap_{normalize_name(table_name)}_{connection_id}.{ext}"


def map_edm_type_to_databricks(
    edm_type: Optional[str] = None,
    *,
    max_length: Optional[int] = None,
    precision: Optional[int] = None,
    scale: Optional[int] = None,
    data_type: Optional[str] = None,
) -> str:
    """Convert an SAP EDM / internal type to a Databricks SQL type string."""
    edm = (edm_type or "").strip()
    if edm == "Edm.Decimal":
        p = precision if precision and precision > 0 else 38
        p = min(int(p), 38)
        s = scale if scale is not None and scale >= 0 else 10
        s = min(int(s), p)
        return f"DECIMAL({p},{s})"

    mapped = EDM_TO_DATABRICKS.get(edm)
    if mapped is None and data_type:
        mapped = DATATYPE_TO_DATABRICKS.get(str(data_type).upper().strip())
    if mapped is None:
        mapped = "STRING"
    return mapped


def map_abap_type_to_databricks(
    datatype: Optional[str],
    length: Optional[int] = None,
    decimals: Optional[int] = None,
) -> str:
    """Map an ABAP / JSON field type onto a Databricks DDL type."""
    kind = (datatype or "").upper().strip()
    size = int(length or 0)
    scale = int(decimals or 0)
    base = ABAP_TO_DATABRICKS.get(kind)
    if base == "DECIMAL":
        precision = min(size or 38, 38)
        scale = min(max(scale, 0), precision)
        return f"DECIMAL({precision},{scale})"
    if base:
        return base
    return "STRING"


def _column_databricks_type(column: Dict[str, Any]) -> str:
    explicit = column.get("dbxType") or column.get("sfType")
    if explicit:
        from services.databricks.load_helpers import _normalize_to_databricks_type

        return _normalize_to_databricks_type(str(explicit))
    datatype = column.get("dataType")
    if datatype and not column.get("edmType"):
        # Prefer ABAP mapping when only datatype is present (transform targets)
        abap = map_abap_type_to_databricks(
            str(datatype),
            column.get("maxLength") or column.get("length"),
            column.get("scale") if column.get("scale") is not None else column.get("decimals"),
        )
        if abap != "STRING" or str(datatype).upper() in ABAP_TO_DATABRICKS:
            return abap
    return map_edm_type_to_databricks(
        column.get("edmType", ""),
        max_length=column.get("maxLength") or column.get("length"),
        precision=column.get("precision"),
        scale=column.get("scale"),
        data_type=column.get("dataType"),
    )


def normalize_sap_column_for_databricks(name: Optional[str]) -> str:
    """Match Databricks load_helpers column keys (letter/digit underscore split).

    ``copy_function`` rewrites attribute keys via ``add_underscore``
    (``HWAE2`` → ``HWAE_2``). SAP OData headers are often glued alphanumerics;
    DataFrame columns must use the same spelling or COPY INTO fails with
    ``COPY_INTO_SCHEMA_MISMATCH_WITH_TARGET_TABLE``.
    """
    from services.dynamics.source_common import add_underscore

    return add_underscore(str(name or "").strip())


def build_attributes_details_from_sap_metadata(
    schema_metadata: Optional[Dict[str, Any]],
    *,
    is_updated: str = "_FIVETRAN_SYNCED",
    is_deleted: str = "_FIVETRAN_DELETED",
) -> Dict[str, str]:
    """Build {COL_NAME: DatabricksType} for copy_function / base DDL."""
    out: Dict[str, str] = {}
    for col in (schema_metadata or {}).get("columns") or []:
        name = col.get("name")
        if not name:
            continue
        key = normalize_sap_column_for_databricks(name)
        if not key:
            continue
        out[key] = _column_databricks_type(col)
    # Meta columns keep configured spelling (already underscored).
    updated = str(is_updated or "_FIVETRAN_SYNCED").strip().upper()
    deleted = str(is_deleted or "_FIVETRAN_DELETED").strip().upper()
    out.setdefault(updated, "TIMESTAMP")
    out.setdefault(deleted, "BOOLEAN")
    return out


def build_target_schema_metadata_databricks(
    resolved: Dict[str, Any],
    sap_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Column list from effective target names with Databricks type hints."""
    from services.sap.as_source.to_snowflake import build_target_schema_metadata

    # Reuse Snowflake target ordering / PK flags, then rewrite types for Delta.
    base = build_target_schema_metadata(resolved, sap_metadata)
    columns: List[Dict[str, Any]] = []
    for col in base.get("columns") or []:
        rewritten = dict(col)
        rewritten["dbxType"] = _column_databricks_type(rewritten)
        rewritten.pop("sfType", None)
        columns.append(rewritten)
    return {"columns": columns}


def ensure_databricks_table_from_sap_metadata(
    destination_engine,
    table_name: str,
    schema: str,
    schema_metadata: Optional[Dict[str, Any]],
    connection_id: int,
    cmp_id: int,
    *,
    is_updated: str = "_FIVETRAN_SYNCED",
    is_deleted: str = "_FIVETRAN_DELETED",
    catalog: Optional[str] = None,
) -> Dict[str, str]:
    """Ensure Delta base table exists from SAP EDM / transform metadata."""
    from services.databricks.load_helpers import ensure_databricks_base_table_exists

    attributes = build_attributes_details_from_sap_metadata(
        schema_metadata,
        is_updated=is_updated,
        is_deleted=is_deleted,
    )
    empty = pd.DataFrame(columns=list(attributes.keys()))
    ensure_databricks_base_table_exists(
        destination_engine,
        normalize_name(table_name) if not str(table_name).isupper() else str(table_name).strip().upper(),
        schema,
        empty,
        attributes,
        connection_id,
        cmp_id,
        destination_connection={"schema": schema, "catalog": catalog},
    )
    return attributes
