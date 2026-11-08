from asyncio.log import logger
import re
from typing import List,Any, Dict, Optional


_SNOWFLAKE_RESERVED = frozenset(
    {
        "ACCOUNT",
        "ALL",
        "ALTER",
        "AND",
        "ANY",
        "AS",
        "BETWEEN",
        "BY",
        "CASE",
        "CAST",
        "CHECK",
        "COLUMN",
        "CONNECT",
        "CONNECTION",
        "CONSTRAINT",
        "CREATE",
        "CROSS",
        "CURRENT",
        "CURRENT_DATE",
        "CURRENT_TIME",
        "CURRENT_TIMESTAMP",
        "CURRENT_USER",
        "DATABASE",
        "DELETE",
        "DISTINCT",
        "DROP",
        "ELSE",
        "EXISTS",
        "FALSE",
        "FOLLOWING",
        "FOR",
        "FROM",
        "FULL",
        "GRANT",
        "GROUP",
        "GSCLUSTER",
        "HAVING",
        "ILIKE",
        "IN",
        "INCREMENT",
        "INNER",
        "INSERT",
        "INTERSECT",
        "INTO",
        "IS",
        "ISSUE",
        "JOIN",
        "LATERAL",
        "LEFT",
        "LIKE",
        "LOCALTIME",
        "LOCALTIMESTAMP",
        "MINUS",
        "NATURAL",
        "NOT",
        "NULL",
        "OF",
        "ON",
        "OR",
        "ORDER",
        "ORGANIZATION",
        "QUALIFY",
        "REGEXP",
        "REVOKE",
        "RIGHT",
        "RLIKE",
        "ROW",
        "ROWS",
        "SAMPLE",
        "SCHEMA",
        "SELECT",
        "SET",
        "SOME",
        "START",
        "TABLE",
        "TABLESAMPLE",
        "THEN",
        "TO",
        "TRIGGER",
        "TRUE",
        "TRY_CAST",
        "UNION",
        "UNIQUE",
        "UPDATE",
        "USING",
        "VALUES",
        "VIEW",
        "WHEN",
        "WHENEVER",
        "WHERE",
        "WITH",
    }
)

_INVALID_CHAR_RE = re.compile(r"[^A-Za-z0-9_$]")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_IDENT_RE = re.compile(r"^[A-Z_][A-Z0-9_$]*$")

def normalize_name(name: str) -> str:
    """
    Normalizes a name for Snowflake by replacing Salesforce-style __c with _C,
    handling multiple underscores, and converting to uppercase.
    """
    # Replace Salesforce-style __c with _C first
    name = name.replace("__c", "_C")

    # Replace multiple underscores with a single one
    name = re.sub(r"__+", "_", name)

    # Insert underscore before uppercase letters preceded by lowercase letters (e.g., shownMoon → shown_Moon)
    name = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", name)

    # Insert underscore before digits (e.g., Field2 → Field_2)
    name = re.sub(r"(?<=[A-Za-z])(\d)", r"_\1", name)

    # Insert underscore after digits if followed by a letter (e.g., 2Field → 2_Field)
    name = re.sub(r"(\d)(?=[A-Za-z])", r"\1_", name)

    return name.upper()


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

    # logger.warning(
    #     "fetch_metadata: entity '%s' not found in metadata; falling back to first non-DeltaLinks EntityType",
    #     entity_name,
    # )
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

def _sf_ident(name: str) -> str:
    """Quote-ready Snowflake identifier. Do not re-split digits on already-valid names."""
    if is_valid_snowflake_destination_name(name):
        return str(name).strip().upper()
    return normalize_name(name)

def is_valid_snowflake_destination_name(name: Optional[str]) -> bool:
    if not name or not str(name).strip():
        return False
    candidate = str(name).strip().upper()
    if len(candidate) > 255 or candidate in _SNOWFLAKE_RESERVED:
        return False
    return bool(_IDENT_RE.match(candidate))


