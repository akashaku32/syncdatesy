"""SAP OData shared helpers  -  constants, type maps, data-class, and utilities.

This module contains everything that is imported by BOTH the direct HTTP loader
(``services.sap.direct``) and the proxy gRPC loader (``services.sap.proxy``).  It has
**no** imports from either of those two files so that the dependency graph remains
strictly one-way and circular imports are impossible.

Import hierarchy (-> means "imports from"):
    services.sap.source   (this file)  - no internal service deps
    services.sap.direct   -> services.sap.source
    services.sap.proxy    -> services.sap.source
    services.sap.as_source.to_adls  -> services.sap.source + services.sap.direct + services.sap.proxy
"""

from __future__ import annotations

import io
import json
import logging
import os
import random
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
import xmltodict

from services.snowflake_to_adls_service import upload_file_to_adls
from utils.timing_utils import log_execution_time

# -- Module-level logger -------------------------------------------------------
# Do NOT call setLevel() here  -  let the application root logger control levels.
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SAP HTTP configuration
# ---------------------------------------------------------------------------

# Timeout (seconds) applied to every SAP Gateway HTTP request.
# Override via environment variable SAP_REQUEST_TIMEOUT_SECONDS.
SAP_REQUEST_TIMEOUT: int = int(os.environ.get("SAP_REQUEST_TIMEOUT_SECONDS", "120"))

# Safety cap on OData pages fetched per entity per run.
# Guards against infinite loops caused by circular __next links.
SAP_MAX_PAGES: int = int(os.environ.get("SAP_MAX_PAGES", "10000"))

# ---------------------------------------------------------------------------
# EDM -> internal type mapping
# ---------------------------------------------------------------------------

#: Maps OData Edm.* primitive types to internal Datasynchor column type strings.
EDM_TYPE_MAPPING: Dict[str, str] = {
    "Edm.Binary":          "BINARY",
    "Edm.Boolean":         "BOOLEAN",
    "Edm.Byte":            "BINARY",
    "Edm.DateTime":        "NVARCHAR",
    "Edm.Decimal":         "DECIMAL",
    "Edm.Double":          "DOUBLE",
    "Edm.Guid":            "STRING",
    "Edm.Int16":           "SHORT",
    "Edm.Int32":           "INTEGER",
    "Edm.Int64":           "LONG",
    "Edm.SByte":           "BINARY",
    "Edm.Single":          "FLOAT",
    "Edm.String":          "NVARCHAR",
    "Edm.Time":            "NVARCHAR",
    "Edm.DateTimeOffset":  "INSTANT",
}

#: Additional vtype-template properties attached to specific column data types.
PROPERTIES: Dict[str, List[Dict[str, str]]] = {
    "NVARCHAR": [
        {"namespace": "com.datasynchor.vflow", "name": "vtype-template", "value": "string"}
    ],
    "DECIMAL": [
        {"namespace": "com.datasynchor.vflow", "name": "vtype-template", "value": "decfloat34"}
    ],
}

#: Edm types that represent numeric values (used in row-filter coercion).
NUMERIC_EDM_TYPES: frozenset = frozenset({
    "Edm.Byte", "Edm.SByte", "Edm.Int16", "Edm.Int32", "Edm.Int64",
    "Edm.Decimal", "Edm.Double", "Edm.Single",
})

#: Edm types that represent datetime/time values (used in row-filter coercion).
DATETIME_EDM_TYPES: frozenset = frozenset({
    "Edm.DateTime", "Edm.DateTimeOffset", "Edm.Time",
})

# ---------------------------------------------------------------------------
# OData date detection patterns
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Data class: SAPSourceDetails
# ---------------------------------------------------------------------------

class SAPSourceDetails:
    """Immutable value object holding OData source configuration for a single entity.

    Passed through load_initial / load_delta so that every
    helper receives a typed bundle rather than a loose dict of keyword args.

    Attributes:
        name: OData service name (e.g. ``ZORDERS_SRV``).
        entity_name: Entity set name within that service (e.g. ``SalesOrderSet``).
        file_rec_count: Target rows per parquet chunk written to ADLS.
        folder_name: ADLS base folder path for this entity.
        compress: Whether to compress parquet output.
        compress_type: Parquet compression codec (``snappy``, ``gzip``, G--) or None.
        delta_name: Optional delta-links entity set name used as a fallback to
            obtain the delta token when SAP does not embed it in the last page.
        filter_expr: Optional OData ``$filter`` expression (server-side filtering).
        row_filter_config: Optional parsed RowFilterConfig for client-side post-filter.
    """

    def __init__(
        self,
        name: str,
        entity_name: str,
        file_rec_count: int,
        folder_name: str,
        compress: bool,
        compress_type: Optional[str],
        delta_name: Optional[str] = None,
        filter_expr: Optional[str] = None,
        row_filter_config: Optional[Any] = None,
        # The agent/reference implementation stamps every row with the
        # caller's run timestamp.  Keep this optional for older callers.
        timestamp: Optional[datetime] = None,
        pagination_count: Optional[int] = None,
    ) -> None:
        self.name = name
        self.entity_name = entity_name
        self.file_rec_count = file_rec_count
        self.folder_name = folder_name
        self.compress = compress
        self.compress_type = compress_type
        self.delta_name = delta_name
        self.filter_expr = filter_expr
        self.row_filter_config = row_filter_config
        self.timestamp = timestamp
        self.pagination_count = pagination_count


# ---------------------------------------------------------------------------
# ADLS write helper
# ---------------------------------------------------------------------------

def write_adls(
    directory: str,
    filename: str,
    content: Optional[bytes] = None,
    adls_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Write binary content to Azure Data Lake Storage Gen2.

    Delegates to the workspace-level ``upload_file_to_adls`` helper which
    handles chunked uploads and retry logic.  Callers must always supply
    ``adls_config``; there is no credential fallback so that thread-pool
    workers cannot accidentally share state.

    Args:
        directory: Target virtual folder path on ADLS (forward-slash separated).
        filename: Target blob/file name within ``directory``.
        content: Raw bytes to upload.  An empty bytes object (``b""``) is
            written when this is ``None`` (useful for marker files like ``_SUCCESS``).
        adls_config: Required ADLS connection parameter map.

    Raises:
        ValueError: If ``adls_config`` is not provided.
    """
    if not adls_config:
        raise ValueError("ADLS credentials not provided to write_adls.")

    # Normalise path separator so the upload helper receives a clean POSIX path.
    remote_path = os.path.join(directory, filename).replace("\\", "/")
    logger.info("Writing to ADLS: %s", remote_path)

    data_payload = content if content is not None else b""
    upload_file_to_adls(remote_path, data_payload, adls_config)


# ---------------------------------------------------------------------------
# Filename generation helpers
# ---------------------------------------------------------------------------

def rand_hex(n: int) -> str:
    """Return a random lowercase hexadecimal string of exactly *n* characters.

    Args:
        n: Desired string length.

    Returns:
        Random hex string of length ``n``.
    """
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def generate_filename_base(time: datetime) -> str:
    """Generate a part-file base name following the Datasynchor naming convention.

    The format mirrors the reference ``utils.py`` implementation so that files
    produced by this service are indistinguishable from files produced by any
    other Datasynchor loader.  The timestamp is supplied by the caller so all
    files from one load share the same run identifier.

    Returns:
        String of the form ``part-<YYYYMMDD_HHMMSS>-<8hex>-<4hex>-<4hex>-<4hex>-<12hex>``.
    """
    a = time.strftime("%Y%m%d_%H%M%S")
    b = rand_hex(8)
    c = rand_hex(4)
    d = rand_hex(4)
    e = rand_hex(4)
    f = rand_hex(12)
    return f"part-{a}-{b}-{c}-{d}-{e}-{f}"


def generate_filename(base_name: str, part_no: int = 1) -> str:
    """Construct a parquet chunk filename from a base name and a part number.

    Args:
        base_name: Value returned by :func:`generate_filename_base`.
        part_no: Sequential chunk index (1-based).

    Returns:
        Filename string, e.g. ``part-...-01.parquet``.
    """
    return f"{base_name}-{part_no:02d}.parquet"


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

@log_execution_time
def force_https_url(
    url: str,
    server_address: str = None,
    service_name: str = None,
) -> str:
    """Ensure *url* uses HTTPS, is absolute, and has a single ``?`` separator.

    SAP OData services occasionally return HTTP next/delta links or relative
    URLs.  This function normalises all of them to a clean HTTPS URL before
    any further request is made.

    Args:
        url: The OData endpoint URL to normalise.
        server_address: Trusted SAP host:port used to reconstruct relative URLs.
        service_name: OData service name used to reconstruct relative URLs.

    Returns:
        Normalised HTTPS URL string.
    """
    if not url:
        return url

    # Reconstruct relative URLs using the provided server_address / service_name.
    if not url.startswith("http://") and not url.startswith("https://") and server_address and service_name:
        url_clean = url.lstrip("/")
        base_prefix = f"sap/opu/odata/sap/{service_name}/"
        if url_clean.startswith(base_prefix):
            url = f"https://{server_address}/{url_clean}"
        elif url_clean.startswith("sap/opu/odata/"):
            url = f"https://{server_address}/{url_clean}"
        else:
            url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/{url_clean}"

    if url.startswith("http://"):
        url = url.replace("http://", "https://", 1)
    elif not url.startswith("https://") and not server_address:
        url = f"https://{url.lstrip('/')}"
    elif not url.startswith("https://") and server_address:
        url = f"https://{server_address}/{url.lstrip('/')}"

    # Collapse multiple ``?`` separators (e.g. from malformed SAP next links).
    if url.count("?") > 1:
        parts = url.split("?")
        url = parts[0] + "?" + "&".join(parts[1:])

    return url


@log_execution_time
def extract_token_from_delta_link(link: str) -> str:
    """Extract the raw delta token from a full delta URL, relative URI, or XML link.

    Handles all common SAP OData token surface formats:
    - ``?!deltatoken='D20260630_002'``
    - ``?$deltatoken=D20260630_002``
    - ``DeltaLinksOfSalesOrder('D20260630_002')``
    - Raw token strings passed directly

    Args:
        link: Full delta URL, relative path, XML link tag, or raw token.

    Returns:
        Extracted raw token string, or the original *link* value unchanged if
        no recognisable pattern is found.
    """
    if not link:
        return ""
    link = str(link).strip()

    # Unwrap an XML ``<link rel="delta" href="..."/>`` tag if present.
    if link.startswith("<") and "href=" in link:
        m_href = re.search(r'href="([^"]+)"', link)
        if not m_href:
            m_href = re.search(r"href='([^']+)'", link)
        if m_href:
            link = m_href.group(1)

    # 1. Query-parameter form: ``[!$]?deltatoken='VALUE'`` or ``deltatoken=VALUE``.
    # 1. Query-parameter form: ``[!$]?deltatoken\s*=\s*['\"]?([^'\"&>]+)['\"]?``.
    match = re.search(r"[!$]?deltatoken\s*=\s*['\"]?([^'\"&>]+)['\"]?", link, re.IGNORECASE)
    if match:
        return match.group(1)

    # 2. Parenthesis key form: ``EntitySet('VALUE')``.
    # Match both single-quoted and unquoted token values inside parentheses.
    match_paren = re.findall(r"\(\"?'?([^'\"()]+)'?\"?\)", link)
    if match_paren:
        return match_paren[-1]

    return link


def build_sap_delta_start_url(
    delta_token_url: str,
    *,
    server_address: str,
    service_name: str,
    entity_name: str,
    filter_expr: Optional[str] = None,
) -> str:
    """Build the SAP ODQ delta URL using Gateway-compatible ``!deltatoken`` syntax."""
    token = str(delta_token_url or "").strip()
    if not token:
        raise ValueError("Missing SAP delta token")

    if (
        token.startswith("http://")
        or token.startswith("https://")
        or "/" in token
        or "?" in token
    ):
        url = force_https_url(token, server_address, service_name)
    else:
        raw_token = token.strip("'\"")
        query_parts: List[str] = []
        if filter_expr:
            encoded_filter = quote(filter_expr, safe="'()")
            query_parts.append(f"$filter={encoded_filter}")
        query_parts.append(f"!deltatoken='{raw_token}'")
        query_prefix = "?" if filter_expr else "?&"
        url = (
            f"https://{server_address}/sap/opu/odata/sap/{service_name}"
            f"/{entity_name}{query_prefix}{'&'.join(query_parts)}"
        )
        url = force_https_url(url, server_address, service_name)

    if "$format=json" not in url.lower() and "$format=xml" not in url.lower():
        url += "&$format=json" if "?" in url else "?$format=json"
    return url


# ---------------------------------------------------------------------------
# OData date detection and conversion
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SAP $metadata fetch
# ---------------------------------------------------------------------------

def _metadata_as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _resolve_edmx_entity_type(
    metadata_dict: Dict[str, Any],
    entity_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve the EntityType block for an OData entity set name."""
    try:
        data_services = metadata_dict["edmx:Edmx"]["edmx:DataServices"]
        schema_items = _metadata_as_list(data_services.get("Schema"))
    except (KeyError, TypeError):
        return None

    entity_types: Dict[str, Dict[str, Any]] = {}
    for schema_item in schema_items:
        if not isinstance(schema_item, dict):
            continue
        for entity_type in _metadata_as_list(schema_item.get("EntityType")):
            if isinstance(entity_type, dict) and entity_type.get("@Name"):
                entity_types[entity_type["@Name"]] = entity_type

    if not entity_types:
        return None

    if not entity_name:
        return next(
            (et for et in entity_types.values() if not str(et.get("@Name", "")).startswith("DeltaLinks")),
            None,
        )

    entity_name = str(entity_name).strip()
    for schema_item in schema_items:
        if not isinstance(schema_item, dict):
            continue
        entity_container = schema_item.get("EntityContainer") or {}
        if isinstance(entity_container, list):
            entity_container = next((item for item in entity_container if isinstance(item, dict)), {})
        for entity_set in _metadata_as_list(entity_container.get("EntitySet") if isinstance(entity_container, dict) else None):
            if not isinstance(entity_set, dict):
                continue
            if entity_set.get("@Name") != entity_name:
                continue
            entity_type_ref = str(entity_set.get("@EntityType") or "").split(".")[-1]
            if entity_type_ref and entity_type_ref in entity_types:
                return entity_types[entity_type_ref]

    if entity_name in entity_types:
        return entity_types[entity_name]

    for type_name, entity_type in entity_types.items():
        if type_name == entity_name:
            return entity_type

    logger.warning(
        "fetch_metadata: entity '%s' not found in metadata; falling back to first non-DeltaLinks EntityType",
        entity_name,
    )
    return next(
        (et for et in entity_types.values() if not str(et.get("@Name", "")).startswith("DeltaLinks")),
        None,
    )


def _build_metadata_columns(main_entity: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build Datasynchor column schema from a resolved EntityType block."""
    try:
        property_refs = main_entity["Key"]["PropertyRef"]
    except (KeyError, TypeError) as exc:
        logger.error(
            "fetch_metadata: missing or malformed Key/PropertyRef in entity '%s': %s",
            main_entity.get("@Name", "?"),
            exc,
        )
        return []

    if isinstance(property_refs, list):
        primary_keys = [k["@Name"] for k in property_refs if isinstance(k, dict) and k.get("@Name")]
    else:
        primary_keys = [property_refs["@Name"]] if isinstance(property_refs, dict) and property_refs.get("@Name") else []

    raw_properties = main_entity.get("Property")
    if raw_properties is None:
        logger.error(
            "fetch_metadata: no Property elements found in entity '%s'.",
            main_entity.get("@Name", "?"),
        )
        return []
    if not isinstance(raw_properties, list):
        raw_properties = [raw_properties]

    columns: List[Dict[str, Any]] = []
    for prop in raw_properties:
        if not isinstance(prop, dict):
            continue
        if prop.get("@Name") in ("ODQ_CHANGEMODE", "ODQ_ENTITYCNTR"):
            continue

        e_type = prop.get("@Type", "Edm.String")
        data_type = EDM_TYPE_MAPPING.get(e_type, "NVARCHAR")
        column: Dict[str, Any] = {
            "name": prop["@Name"],
            "dataType": data_type,
            "edmType": e_type,
        }

        if data_type == "NVARCHAR":
            column["length"] = 8

        if "@MaxLength" in prop:
            try:
                val = int(prop["@MaxLength"])
            except ValueError:
                val = 0
            column["length"] = val if val > 0 else 8

        if e_type in ("Edm.DateTime", "Edm.Time", "Edm.DateTimeOffset"):
            column["length"] = max(column.get("length", 0), 30)

        if "@Precision" in prop:
            try:
                p_val = int(prop["@Precision"])
            except ValueError:
                p_val = 0
            if p_val > 0:
                column["precision"] = p_val

        if "@Nullable" in prop:
            column["nullable"] = str(prop["@Nullable"]).strip().lower() == "true"

        if "@Scale" in prop:
            try:
                s_val = int(prop["@Scale"])
                if s_val >= 0:
                    column["scale"] = s_val
            except ValueError:
                logger.warning(
                    "Non-numeric @Scale '%s' for property '%s'  -  skipping scale.",
                    prop["@Scale"],
                    prop.get("@Name"),
                )

        if prop.get("@Name") in primary_keys:
            column["primaryKey"] = True

        if PROPERTIES.get(data_type):
            column["properties"] = PROPERTIES[data_type]

        if "@sap:filter-restriction" in prop:
            column["filterRestriction"] = prop["@sap:filter-restriction"]

        if "@sap:display-format" in prop:
            column["displayFormat"] = prop["@sap:display-format"]
            column["display_format"] = prop["@sap:display-format"]

        if "@sap:filterable" in prop:
            column["filterable"] = str(prop["@sap:filterable"]).strip().lower() != "false"
        else:
            column["filterable"] = True

        columns.append(column)

    _change_mode_props = [
        {"namespace": "com.sap.vflow", "name": "vtype-template", "value": "string"},
        {"namespace": "com.sap.rms", "name": "semanticType", "value": "_change_mode"},
    ]
    columns.extend([
        {"name": "__operation_type", "dataType": "NVARCHAR", "length": 1, "nullable": True, "properties": _change_mode_props},
        {"name": "__sequence_number", "dataType": "DECIMAL", "precision": 20, "nullable": True, "properties": _change_mode_props},
        {"name": "__timestamp", "dataType": "TIMESTAMP", "nullable": True,
         "properties": [
             {"namespace": "com.sap.vflow", "name": "vtype-template", "value": "timestamp"},
             {"namespace": "com.sap.rms", "name": "semanticType", "value": "_change_mode"},
         ]},
        {"name": "__delta_pointer", "dataType": "NVARCHAR", "length": 30, "nullable": True, "properties": _change_mode_props},
        {"name": "_SPECT_DELETED",    "dataType": "BOOLEAN",  "nullable": False},
    ])
    return columns


@log_execution_time
def fetch_metadata(
    url: str,
    username: str,
    password: str,
    source_config_or_data: Optional[Any] = None,
    entity_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch and parse the SAP OData ``$metadata`` endpoint into a column schema dict.

    Translates the EDMX XML response into the Datasynchor RMS parquet schema
    format, including primary-key flags, nullable annotations, precision/scale,
    and the four internal audit columns (``__operation_type``, ``__delta_pointer``,
    ``__sequence_number``, ``__timestamp``).

    Args:
        url: Full HTTPS URL of the ``$metadata`` endpoint.
        username: SAP Basic Auth username.
        password: SAP Basic Auth password.
        source_config_or_data: Optional SourceConfig model or dict with TLS verification settings.
        entity_name: Optional OData entity set name (e.g. ``EntityOf0FI_AR_30``).

    Returns:
        Schema metadata dictionary (``{"version", "format", "properties", "columns"}``)
        or ``None`` if the request failed or the response could not be parsed.
    """
    url = force_https_url(url)
    logger.info("Fetching SAP OData metadata from %s", url)
    try:
        from utils.sap_tls_helper import build_session_from_config as build_session_for_source
        from urllib.parse import urlparse
        parsed = urlparse(url)
        session_target = source_config_or_data or {"host": parsed.hostname or url, "port": parsed.port or 443}
        session = build_session_for_source(session_target)
    except Exception:
        # Self-signed SAP Gateway certs are common; match sap_tls_helper fallback.
        session = requests.Session()
        session.verify = False

    response = session.get(url, auth=(username, password), timeout=SAP_REQUEST_TIMEOUT)
    if response.status_code != 200:
        logger.error("Metadata request failed with status: %d", response.status_code)
        return None

    metadata_dict = xmltodict.parse(response.text)

    main_entity = _resolve_edmx_entity_type(metadata_dict, entity_name=entity_name)
    if not main_entity:
        logger.warning("No suitable entity type found in metadata for entity_name=%s", entity_name)
        return None

    columns = _build_metadata_columns(main_entity)
    if not columns:
        return None

    logger.info(
        "fetch_metadata: resolved entity_type='%s' entity_set=%s columns=%d",
        main_entity.get("@Name"),
        entity_name or "(default)",
        len(columns),
    )

    return {
        "version":    metadata_dict["edmx:Edmx"].get("@Version"),
        "format":     "PARQUET",
        "properties": [
            {"namespace": "datasynchor.com", "name": "loadType",             "value": "REPLICATE"},
            {"namespace": "datasynchor.com", "name": "replicationTaskID",    "value": "3a5ca2a6-a63e-4a28-b3cb-6e0fea66f93b"},
            {"namespace": "datasynchor.com", "name": "groupDeltaFilesBy",    "value": "NONE"},
        ],
        "columns": columns,
    }


# ---------------------------------------------------------------------------
# Proxy response normalization
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_proxy_source_detail_entities(payload: Any) -> List[Dict[str, Any]]:
    """Normalize ``GetSourceDetail``/``SourceDetail`` proxy payloads.

    Expected shapes include nested ``app:service`` documents with
    ``app:workspace.app:collection`` entries. Each collection item can expose
    the entity name through ``sap:member-title``, ``atom:title.#text``, or
    ``@href``. Delta-link entities are kept as metadata and not returned as
    standalone rows.
    """
    if not payload:
        return []

    if isinstance(payload, dict):
        for wrapper_key in ("src_details_json", "srcDetailsJson", "src_json", "srcJson"):
            wrapped = payload.get(wrapper_key)
            if wrapped is not None:
                return parse_proxy_source_detail_entities(wrapped)

        service = payload.get("app:service") or payload.get("service") or payload
        if not isinstance(service, dict):
            return []

        workspace = service.get("app:workspace") or service.get("workspace") or {}
        if not isinstance(workspace, dict):
            return []

        collections = _as_list(workspace.get("app:collection") or workspace.get("collection"))
        names: List[str] = []
        for item in collections:
            if not isinstance(item, dict):
                continue

            entity_name = (
                item.get("sap:member-title")
                or item.get("@sap:member-title")
                or item.get("@href")
            )
            if not entity_name:
                atom_title = item.get("atom:title")
                if isinstance(atom_title, dict):
                    entity_name = atom_title.get("#text") or atom_title.get("@value")
                elif isinstance(atom_title, str):
                    entity_name = atom_title

            if entity_name:
                entity_name = str(entity_name).strip()
                if entity_name:
                    names.append(entity_name)

        seen = set()
        all_names = set(names)
        entities: List[Dict[str, Any]] = []
        for entity_name in names:
            if entity_name in seen:
                continue
            seen.add(entity_name)
            if entity_name.startswith("DeltaOf") or entity_name.startswith("DeltaLinksOf"):
                continue

            delta_name = None
            for candidate in (f"DeltaLinksOf{entity_name}", f"DeltaOf{entity_name}"):
                if candidate in all_names:
                    delta_name = candidate
                    break

            entities.append({
                "entity_name": entity_name,
                "is_delta": delta_name is not None,
                "delta_name": delta_name,
            })

        return entities

    if isinstance(payload, list):
        entities: List[Dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict) and item.get("entity_name"):
                entities.append({
                    "entity_name": str(item["entity_name"]).strip(),
                    "is_delta": bool(item.get("is_delta")),
                    "delta_name": item.get("delta_name"),
                })
        return entities

    return []


@log_execution_time
def parse_proxy_metadata_entities(payload: Any) -> List[Dict[str, Any]]:
    """Normalize proxy ``Metadata`` responses into entity/field records.

    This reuses the same field-shape the direct path already stores in
    ``source_table_selection`` so proxy and direct behave identically.
    """
    if not payload:
        return []

    if isinstance(payload, dict):
        for wrapper_key in ("metadata_json", "metadataJson"):
            wrapped = payload.get(wrapper_key)
            if wrapped is not None:
                return parse_proxy_metadata_entities(wrapped)

        edmx_root = payload.get("edmx:Edmx") or payload.get("edmx")
        if not isinstance(edmx_root, dict):
            return []

        data_services = edmx_root.get("edmx:DataServices") or edmx_root.get("DataServices")
        if not isinstance(data_services, dict):
            return []

        schema = data_services.get("Schema")
        schemas = schema if isinstance(schema, list) else [schema]

        entity_types: Dict[str, List[Dict[str, Any]]] = {}
        all_set_names = set()

        for schema_item in schemas:
            if not isinstance(schema_item, dict):
                continue

            entity_type_items = schema_item.get("EntityType") or []
            if not isinstance(entity_type_items, list):
                entity_type_items = [entity_type_items]

            for entity_type in entity_type_items:
                if not isinstance(entity_type, dict):
                    continue
                et_name = entity_type.get("@Name")
                if not et_name:
                    continue

                key_node = entity_type.get("Key") or {}
                property_refs = key_node.get("PropertyRef") if isinstance(key_node, dict) else None
                if property_refs is None:
                    primary_keys: List[str] = []
                elif isinstance(property_refs, list):
                    primary_keys = [ref.get("@Name") for ref in property_refs if isinstance(ref, dict) and ref.get("@Name")]
                elif isinstance(property_refs, dict):
                    primary_keys = [property_refs.get("@Name")] if property_refs.get("@Name") else []
                else:
                    primary_keys = []

                raw_properties = entity_type.get("Property") or []
                if not isinstance(raw_properties, list):
                    raw_properties = [raw_properties]

                fields: List[Dict[str, Any]] = []
                for prop in raw_properties:
                    if not isinstance(prop, dict):
                        continue
                    prop_name = prop.get("@Name")
                    if not prop_name:
                        continue

                    fields.append({
                        "name": prop_name,
                        "type": prop.get("@Type", "Edm.String"),
                        "is_primary_key": prop_name in primary_keys,
                        "precision": prop.get("@Precision") or prop.get("@MaxLength"),
                        "filterable": str(prop.get("@sap:filterable", "true")).strip().lower() != "false",
                        "filter_restriction": prop.get("@sap:filter-restriction"),
                        "display_format": prop.get("@sap:display-format"),
                    })


                entity_types[et_name] = fields

            entity_container = schema_item.get("EntityContainer") or {}
            if isinstance(entity_container, list):
                entity_container = next((item for item in entity_container if isinstance(item, dict)), {})

            entity_sets = entity_container.get("EntitySet") if isinstance(entity_container, dict) else []
            if not isinstance(entity_sets, list):
                entity_sets = [entity_sets]

            for entity_set in entity_sets:
                if isinstance(entity_set, dict) and entity_set.get("@Name"):
                    all_set_names.add(entity_set["@Name"])

        results: List[Dict[str, Any]] = []
        for schema_item in schemas:
            if not isinstance(schema_item, dict):
                continue

            entity_container = schema_item.get("EntityContainer") or {}
            if isinstance(entity_container, list):
                entity_container = next((item for item in entity_container if isinstance(item, dict)), {})

            entity_sets = entity_container.get("EntitySet") if isinstance(entity_container, dict) else []
            if not isinstance(entity_sets, list):
                entity_sets = [entity_sets]

            for entity_set in entity_sets:
                if not isinstance(entity_set, dict):
                    continue

                entity_name = entity_set.get("@Name")
                if not entity_name:
                    continue
                if entity_name.startswith("DeltaOf") or entity_name.startswith("DeltaLinksOf"):
                    continue

                raw_entity_type = entity_set.get("@EntityType", "")
                entity_type_name = raw_entity_type.split(".")[-1] if raw_entity_type else ""
                fields = entity_types.get(entity_type_name, [])
                if not fields:
                    for candidate_name, candidate_fields in entity_types.items():
                        if candidate_name == entity_type_name or candidate_name == entity_name:
                            fields = candidate_fields
                            break

                delta_name = None
                for candidate in (f"DeltaOf{entity_name}", f"DeltaLinksOf{entity_name}"):
                    if candidate in all_set_names:
                        delta_name = candidate
                        break

                results.append({
                    "entity_name": entity_name,
                    "is_delta": delta_name is not None,
                    "delta_name": delta_name,
                    "fields": fields,
                })

        return results

    if isinstance(payload, str):
        try:
            metadata_dict = xmltodict.parse(payload)
        except Exception:
            return []
        return parse_proxy_metadata_entities(metadata_dict)

    return []


# ---------------------------------------------------------------------------
# Delta-link extraction helpers
# ---------------------------------------------------------------------------

@log_execution_time
def _extract_delta_link(data: Dict[str, Any]) -> Optional[str]:
    """Extract the OData delta link from a JSON response body.

    Checks all standard and common variations of key names and nesting
    structures returned by SAP OData V2 services.

    Args:
        data: Parsed JSON response dict from SAP.

    Returns:
        Delta link string, or ``None`` if none is found.
    """
    logger.info("[_extract_delta_link] Scanning JSON response payload for delta links.")
    try:
        logger.debug("[_extract_delta_link] Full response payload: %s", json.dumps(data))
    except Exception:
        logger.debug("[_extract_delta_link] Full response payload (str): %s", str(data))

    if not isinstance(data, dict):
        logger.warning("[_extract_delta_link] Provided data is not a dictionary.")
        return None

    d_obj = data.get("d")
    if isinstance(d_obj, dict):
        for key in ("__delta", "@odata.deltaLink", "deltaLink"):
            link = d_obj.get(key)
            if link:
                logger.info("[_extract_delta_link] Found delta link in d.%s: %s", key, link)
                return str(link)

    for key in ("__delta", "@odata.deltaLink", "deltaLink"):
        link = data.get(key)
        if link:
            logger.info("[_extract_delta_link] Found delta link in root.%s: %s", key, link)
            return str(link)

    logger.warning(
        "[_extract_delta_link] No delta link found. Root keys: %s. 'd' keys: %s",
        list(data.keys()),
        list(d_obj.keys()) if isinstance(d_obj, dict) else "N/A",
    )
    return None


@log_execution_time
def _fetch_delta_link_via_entity_set(
    server_address: str,
    service_name: str,
    delta_name: str,
    username: str,
    password: str,
    filter_expr: Optional[str] = None,
    source_config_or_data: Optional[Union[Any, Dict[str, Any]]] = None,
) -> Optional[str]:
    """Retrieve the delta token by querying the dedicated delta-links entity set.

    Used as a fallback when SAP does not embed the delta link in the final page
    of the initial load or subscription-init response.

    Args:
        server_address: SAP host:port (e.g. ``myhost.example.com:443``).
        service_name: OData service name (e.g. ``ZORDERS_SRV``).
        delta_name: Delta-links entity set name (e.g. ``DeltaLinksOfSalesOrder``).
        username: SAP Basic Auth username.
        password: SAP Basic Auth password.
        filter_expr: Optional OData ``$filter`` expression to scope the query.
        source_config_or_data: Optional SourceConfig model instance or dict.

    Returns:
        Reconstructed HTTPS delta link URL, or ``None`` if the token could not
        be resolved.
    """
    try:
        query = "$format=json"
        # We MUST NOT append filter_expr to the DeltaLinks query. The DeltaLinks 
        # entity set only contains CreatedAt and DeltaToken, not the data columns 
        # (like TOTALNETAMOUNT), so passing a data filter here causes a 400 Bad Request.
        url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/{delta_name}?{query}"
        url = force_https_url(url)
        logger.info("[DELTA_FETCH] Querying delta entity set: %s", url)

        try:
            from utils.sap_tls_helper import build_session_from_config as build_session_for_source
            from urllib.parse import urlparse
            parsed_url = urlparse(url)
            host = parsed_url.hostname or server_address.split(":")[0]
            port = parsed_url.port or (int(server_address.split(":")[1]) if ":" in server_address else 443)
            session_target = source_config_or_data or {"host": host, "port": port}
            session = build_session_for_source(session_target)
        except Exception:
            session = requests.Session()
            session.verify = False

        resp = session.get(url, auth=(username, password), timeout=SAP_REQUEST_TIMEOUT)
        logger.info("[DELTA_FETCH] HTTP %d from delta entity set.", resp.status_code)
        if resp.status_code != 200:
            logger.warning("[DELTA_FETCH] Failed with status %d: %s", resp.status_code, resp.text)
            return None

        data = resp.json()
        try:
            logger.debug("[DELTA_FETCH] Payload JSON: %s", json.dumps(data))
        except Exception:
            logger.debug("[DELTA_FETCH] Payload (str): %s", str(data))

        results = data.get("d", {}).get("results", [])
        if not results:
            logger.warning("[DELTA_FETCH] No results in delta entity set response.")
            return None

        if results:
            try:
                # Find the record with the latest CreatedAt
                latest_item = max(
                    results,
                    key=lambda x: int(x["CreatedAt"][6:-2]) if "CreatedAt" in x and isinstance(x["CreatedAt"], str) and len(x["CreatedAt"]) > 8 else 0
                )
                
                if latest_item.get("DeltaToken"):
                    delta_token = str(latest_item["DeltaToken"])
                    logger.info("[DELTA_FETCH] Extracted DeltaToken from latest CreatedAt: %s", delta_token)
            except Exception as sort_err:
                logger.warning("[DELTA_FETCH] Failed to sort tokens by CreatedAt: %s", sort_err)
                # Fallback to taking the last item if sorting fails
                if results[-1].get("DeltaToken"):
                    delta_token = str(results[-1]["DeltaToken"])
                    logger.info("[DELTA_FETCH] Extracted fallback DeltaToken from last record: %s", delta_token)

        if delta_token:
            delta_link = (
                f"https://{server_address}/sap/opu/odata/sap/{service_name}"
                f"/{delta_name}('{delta_token}')"
            )
            logger.info("[DELTA_FETCH] Reconstructed delta link: %s", delta_link)
            return force_https_url(delta_link)

        logger.warning("[DELTA_FETCH] Could not resolve delta token from results. Found %d records.", len(results))
    except Exception as exc:
        logger.exception("[DELTA_FETCH] Error fetching delta token via entity set: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Row-filter helpers (client-side post-filter applied to each page)
# ---------------------------------------------------------------------------

def _coerce_filter_series(series: pd.Series, edm_type: Optional[str]) -> pd.Series:
    """Coerce a pandas Series to the appropriate type for filter comparison.

    Args:
        series: Column values from the current page DataFrame.
        edm_type: OData Edm type string, e.g. ``Edm.Int32``.

    Returns:
        Coerced Series ready for comparison operators.
    """
    if edm_type in NUMERIC_EDM_TYPES:
        return pd.to_numeric(series, errors="coerce")
    if edm_type in DATETIME_EDM_TYPES:
        return pd.to_datetime(series, errors="coerce")
    return series.astype("string")


def _coerce_filter_scalar(value: Any, edm_type: Optional[str]) -> Any:
    """Coerce a scalar filter value to the appropriate type for comparison.

    Args:
        value: The right-hand side value from the filter condition.
        edm_type: OData Edm type string, e.g. ``Edm.Decimal``.

    Returns:
        Coerced Python scalar.
    """
    if edm_type in NUMERIC_EDM_TYPES:
        return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if edm_type in DATETIME_EDM_TYPES:
        return pd.to_datetime(value, errors="coerce")
    return str(value)


@log_execution_time
def _apply_row_filter_to_page(page_df: pd.DataFrame, row_filter_config: Any) -> pd.DataFrame:
    """Apply a validated RowFilterConfig to a single page of OData records.

    Evaluates each filter condition against the page DataFrame and returns
    only the rows that satisfy all conditions (logical AND).  Applied
    **before** the ODQ_CHANGEMODE column is dropped so that column may be
    referenced in a condition.

    Args:
        page_df: DataFrame for the current OData page.
        row_filter_config: Parsed RowFilterConfig model with ``.conditions`` list.

    Returns:
        Filtered copy of *page_df*.

    Raises:
        ValueError: If a filter column is absent from the page or an unsupported
            operator is used.
    """
    conditions = (
        row_filter_config.get("conditions")
        if isinstance(row_filter_config, dict)
        else getattr(row_filter_config, "conditions", None)
    )
    if page_df.empty or not row_filter_config or not conditions:
        return page_df

    # RowFilterConfig.conditions is recursive JSON by design, so Pydantic
    # leaves its members as dicts.  Normalize at this runtime boundary too;
    # validation/building may have normalized only their local references.
    from models.sap_row_filter import RowFilterCondition, RowFilterConfig, RowFilterNot

    def normalize_node(node: Any) -> Any:
        if isinstance(node, (RowFilterCondition, RowFilterConfig, RowFilterNot)):
            return node
        if isinstance(node, dict):
            if "not" in node:
                return RowFilterNot(**node)
            if "conditions" in node:
                return RowFilterConfig(**node)
            return RowFilterCondition(**node)
        return node

    def evaluate(node: Any) -> pd.Series:
        node = normalize_node(node)

        if isinstance(node, RowFilterNot):
            return ~evaluate(node.not_).fillna(False)

        if isinstance(node, RowFilterConfig):
            children = [evaluate(child) for child in node.conditions]
            if not children:
                return pd.Series(True, index=page_df.index)
            result = children[0].fillna(False)
            for child_mask in children[1:]:
                if node.logic == "or":
                    result |= child_mask.fillna(False)
                else:
                    result &= child_mask.fillna(False)
            return result

        if not isinstance(node, RowFilterCondition):
            raise ValueError("Each row-filter node must be a condition, group, or not node.")

        cond = node
        if cond.column not in page_df.columns:
            raise ValueError(
                f"Row filter column '{cond.column}' was not present in the SAP delta payload."
            )

        series   = page_df[cond.column]
        edm_type = getattr(cond, "edm_type", None)
        op       = cond.operator

        if op == "is_null":
            cond_mask = series.isna()
        elif op == "is_not_null":
            cond_mask = series.notna()
        elif op in ("contains", "startswith", "endswith"):
            string_series = series.astype("string")
            needle = str(cond.value)
            if op == "contains":
                cond_mask = string_series.str.contains(needle, regex=False, na=False)
            elif op == "startswith":
                cond_mask = string_series.str.startswith(needle, na=False)
            else:
                cond_mask = string_series.str.endswith(needle, na=False)
        elif op == "in":
            values = cond.value or []
            if edm_type in NUMERIC_EDM_TYPES:
                coerced = [pd.to_numeric(pd.Series([v]), errors="coerce").iloc[0] for v in values]
                cond_mask = _coerce_filter_series(series, edm_type).isin(coerced)
            elif edm_type in DATETIME_EDM_TYPES:
                coerced = [pd.to_datetime(v, errors="coerce") for v in values]
                cond_mask = _coerce_filter_series(series, edm_type).isin(coerced)
            else:
                cond_mask = series.astype("string").isin([str(v) for v in values])
        elif op == "between":
            lo = _coerce_filter_scalar(cond.value["min"], edm_type)
            hi = _coerce_filter_scalar(cond.value["max"], edm_type)
            cs = _coerce_filter_series(series, edm_type)
            cond_mask = cs.ge(lo) & cs.le(hi)
        else:
            cs  = _coerce_filter_series(series, edm_type)
            csv = _coerce_filter_scalar(cond.value, edm_type)
            op_map = {
                "eq": cs == csv, "ne": cs != csv, "gt": cs > csv,
                "ge": cs >= csv, "lt": cs < csv,  "le": cs <= csv,
            }
            if op not in op_map:
                raise ValueError(f"Unsupported row filter operator '{op}'.")
            cond_mask = op_map[op]

        return cond_mask.fillna(False)

    mask = evaluate(row_filter_config)
    return page_df.loc[mask].copy()


# ---------------------------------------------------------------------------
# Shared page-transform and streaming-flush helpers
# ---------------------------------------------------------------------------

@log_execution_time
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


def _build_arrow_schema(metadata_bytes: bytes) -> pa.Schema:
    """Build a PyArrow schema from the SAP metadata column types."""
    types = {
        "SHORT": pa.int16(), "INTEGER": pa.int32(), "LONG": pa.int64(),
        "FLOAT": pa.float32(), "DOUBLE": pa.float64(),
        "BOOLEAN": pa.bool_(), "BINARY": pa.binary(),
        "TIMESTAMP": pa.timestamp("us"), "INSTANT": pa.timestamp("us", tz="UTC"),
    }

    def _pa_type(column):
        if column["dataType"] == "DECIMAL":
            return pa.decimal128(column.get("precision", 38), column.get("scale", 0))
        return types.get(column["dataType"], pa.string())

    return pa.schema([
        pa.field(column["name"], _pa_type(column))
        for column in json.loads(metadata_bytes)["columns"]
    ])


def _to_arrow_table(df: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    """Cast every column to the metadata type before writing Parquet."""
    arrays = []
    for field in schema:
        if field.name in df.columns:
            array = pa.Array.from_pandas(df[field.name])
            if pa.types.is_string(array.type) and not pa.types.is_string(field.type):
                array = pc.if_else(
                    pc.equal(array, ""),
                    pa.scalar(None, pa.string()),
                    array,
                )
            if array.null_count == len(array):
                arrays.append(pa.nulls(len(df), field.type))
            else:
                arrays.append(array.cast(field.type))
        else:
            arrays.append(pa.nulls(len(df), field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


@log_execution_time
def _flush_buffer_to_adls(
    buffer_df: pd.DataFrame,
    output_dir: str,
    file_name_base: str,
    file_num: int,
    compression_type: Optional[str],
    adls_config: Dict[str, Any],
) -> int:
    """Serialise *buffer_df* to an in-memory parquet buffer and upload to ADLS.

    Using ``io.BytesIO`` (no temp file on disk) means the parquet bytes are
    released from RAM as soon as ``write_adls`` returns, keeping the process
    memory footprint as low as possible.

    Args:
        buffer_df:        DataFrame chunk to write (may be smaller than
                          ``file_rec_count`` for the final remainder chunk).
        output_dir:       ADLS virtual folder path (e.g. ``ENTITY/initial/``).
        file_name_base:   Base name from :func:`generate_filename_base`.
        file_num:         Sequential 1-based chunk number used in the filename.
        compression_type: Parquet compression codec (``"snappy"``, ``"gzip"``),
                          or ``None`` / ``"none"`` for uncompressed.
        adls_config:      ADLS connection parameters.

    Returns:
        Number of rows written (``len(buffer_df)``).

    Raises:
        Any exception from :func:`write_adls` propagates to the caller so the
        load function can catch it and return a failure result cleanly.
    """
    file_name = generate_filename(file_name_base, file_num)
    buf = io.BytesIO()
    table = pa.Table.from_pandas(buffer_df, preserve_index=False)
    pq.write_table(
        table,
        buf,
        compression=compression_type if compression_type else "none",
        coerce_timestamps="us",
        allow_truncated_timestamps=True,
    )
    buf.seek(0)
    write_adls(output_dir, file_name, buf.read(), adls_config=adls_config)
    rows = len(buffer_df)
    logger.debug(
        "[ADLS] Flushed chunk #%d (%d rows, file='%s') to '%s'",
        file_num, rows, file_name, output_dir,
    )
    return rows


@log_execution_time
def _flush_buffer_to_local(
    buffer_df: pd.DataFrame,
    staging_dir: str,
    file_name_base: str,
    file_num: int,
    compression_type: Optional[str],
    arrow_schema: Optional[pa.Schema] = None,
) -> Tuple[int, str]:
    """Write one parquet chunk to a local staging directory."""
    os.makedirs(staging_dir, exist_ok=True)
    file_name = generate_filename(file_name_base, file_num)
    local_path = os.path.join(staging_dir, file_name)
    if arrow_schema is None:
        buffer_df.to_parquet(
            local_path,
            index=False,
            compression=compression_type if compression_type else "none",
            coerce_timestamps="us",
            allow_truncated_timestamps=True,
        )
    else:
        pq.write_table(
            _to_arrow_table(buffer_df, arrow_schema),
            local_path,
            compression=compression_type if compression_type else "none",
        )
    logger.info(
        "[LOCAL_STAGE] Wrote parquet file='%s' rows=%d staging_dir='%s'",
        file_name, len(buffer_df), staging_dir,
    )
    return len(buffer_df), local_path

# ---------------------------------------------------------------------------
# Arrow schema + table helpers (SAP internal types -> PyArrow)
# ---------------------------------------------------------------------------

_SAP_ARROW_TYPES: Dict[str, pa.DataType] = {
    "SHORT":     pa.int16(),
    "INTEGER":   pa.int32(),
    "LONG":      pa.int64(),
    "FLOAT":     pa.float32(),
    "DOUBLE":    pa.float64(),
    "BOOLEAN":   pa.bool_(),
    "BINARY":    pa.binary(),
    "TIMESTAMP": pa.timestamp("us"),
    "INSTANT":   pa.timestamp("us", tz="UTC"),
}


def _build_arrow_schema(metadata_bytes: bytes) -> pa.Schema:
    """Build a PyArrow schema from the metadata JSON.

    Reads the ``columns`` list emitted by :func:`fetch_metadata` /
    ``_build_metadata_columns`` and converts each internal type string
    (e.g. ``"INTEGER"``, ``"DECIMAL"``, ``"INSTANT"``) to the matching
    PyArrow type, preserving ``precision`` / ``scale`` for DECIMAL columns.
    NVARCHAR / STRING (and any unknown type) fall back to ``pa.string()``.
    """

    def _pa_type(col: Dict[str, Any]) -> pa.DataType:
        if col["dataType"] == "DECIMAL":
            return pa.decimal128(col.get("precision", 38), col.get("scale", 0))
        return _SAP_ARROW_TYPES.get(col["dataType"], pa.string())  # NVARCHAR / STRING

    columns = json.loads(metadata_bytes)["columns"]
    return pa.schema([pa.field(c["name"], _pa_type(c)) for c in columns])


def _to_arrow_table(df: "pd.DataFrame", schema: pa.Schema) -> pa.Table:
    """Cast every DataFrame column to its metadata-declared PyArrow type.

    Handles two edge-cases that arise with SAP OData data:

    * **Empty strings on non-string columns** – SAP sometimes sends ``""``
      where ``NULL`` is expected for numeric / boolean fields.  These are
      replaced with ``None`` *before* the cast so that PyArrow does not
      raise an ``ArrowInvalid`` error.
    * **Fully-null columns** – columns present in the DataFrame but
      containing only nulls are written as typed null arrays instead of
      attempting a cast, which also avoids ``ArrowInvalid``.
    * **Missing columns** – columns declared in the schema but absent from
      the DataFrame are filled with typed nulls.
    """
    arrays: List[pa.Array] = []
    for f in schema:
        if f.name in df.columns:
            arr = pa.Array.from_pandas(df[f.name])
            # Empty strings cannot be cast to numbers/booleans -> treat as null.
            if pa.types.is_string(arr.type) and not pa.types.is_string(f.type):
                arr = pc.if_else(
                    pc.equal(arr, ""),
                    pa.scalar(None, pa.string()),
                    arr,
                )
            if arr.null_count == len(arr):
                arrays.append(pa.nulls(len(df), f.type))   # fully empty -> typed nulls
            else:
                arrays.append(arr.cast(f.type))
        else:
            arrays.append(pa.nulls(len(df), f.type))
    return pa.Table.from_arrays(arrays, schema=schema)


@log_execution_time
def delete_adls_directory_contents(directory: str, adls_config: Dict[str, Any]) -> None:
    """Delete all files under an ADLS directory before replacement upload."""
    from services.snowflake_to_adls_service import _build_adls_service_client

    container = str(adls_config.get("container_name") or adls_config.get("file_system") or "").strip()
    if not container:
        raise ValueError("ADLS container_name is required for cleanup.")

    service_client = _build_adls_service_client(adls_config)
    fs_client = service_client.get_file_system_client(container)
    remote_dir = directory.replace("\\", "/").strip("/")

    try:
        paths = list(fs_client.get_paths(path=remote_dir))
    except Exception as exc:
        if any(marker in str(exc).lower() for marker in ("pathnotfound", "does not exist", "not found")):
            return
        raise

    for path in paths:
        if getattr(path, "is_directory", False):
            continue
        fs_client.get_file_client(path.name).delete_file()

    try:
        fs_client.get_directory_client(remote_dir).delete_directory()
    except Exception as exc:
        if not any(marker in str(exc).lower() for marker in ("pathnotfound", "does not exist", "not found")):
            raise


@log_execution_time
def delete_adls_file(directory: str, file_name: str, adls_config: Dict[str, Any]) -> None:
    """Delete one ADLS file, matching the proxy loader's selective cleanup."""
    from services.snowflake_to_adls_service import _build_adls_service_client

    container = str(adls_config.get("container_name") or adls_config.get("file_system") or "").strip()
    if not container:
        raise ValueError("ADLS container_name is required for cleanup.")

    service_client = _build_adls_service_client(adls_config)
    remote_path = "/".join(
        part.strip("/") for part in (directory, file_name) if part and part.strip("/")
    )
    try:
        service_client.get_file_system_client(container).get_file_client(remote_path).delete_file()
    except Exception as exc:
        if not any(marker in str(exc).lower() for marker in ("pathnotfound", "does not exist", "not found")):
            raise
