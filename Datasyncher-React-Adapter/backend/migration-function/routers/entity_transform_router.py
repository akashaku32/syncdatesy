"""FastAPI router for entity transform validate and dry-run."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config.transform_limits import (
    DEFAULT_DRY_RUN_ROWS,
    DRY_RUN_SAP_SELECT_BATCH_SIZE,
    DRY_RUN_SAP_TIMEOUT_SECONDS,
    MAX_DRY_RUN_ROWS,
)
from services.transform.dry_run import run_dry_run
from services.transform.mapping_resolver import resolve_effective_mapping
from services.transform.mapping_status import build_mapping_table
from services.transform.mapping_validator import validate_mapping_json
from services.transform.row_filter_bridge import dtp_row_filter_to_row_filter_config, row_filter_warnings
from services.transform.script_validator import script_has_filter_pattern, validate_script_ast

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/entity-transform", tags=["entity-transform"])


class ValidateRequest(BaseModel):
    connection_id: int
    table_name: str
    service_name: Optional[str] = None
    mapping_json: Optional[Any] = None
    mapping_overrides: Dict[str, Any] = Field(default_factory=dict)
    script_text: str = ""
    entry_point: str = ""
    table_fields: Dict[str, Any] = Field(default_factory=dict)
    primary_keys: List[str] = Field(default_factory=list)


class DryRunRequest(ValidateRequest):
    run_order: str = "filter_script_mapping"
    sample_size: Optional[int] = None
    source_credentials: Dict[str, Any] = Field(default_factory=dict)


def _normalize_mapping_json(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        _, errors = validate_mapping_json(raw)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
        return json.loads(raw)
    raise HTTPException(status_code=400, detail="mapping_json must be object or string")


def _sap_fields_from_table_fields(table_fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    fields = []
    for name, meta in (table_fields or {}).items():
        if isinstance(meta, dict):
            fields.append(
                {
                    "fieldname": name,
                    "datatype": meta.get("type") or meta.get("datatype") or "CHAR",
                    "length": meta.get("length") or meta.get("maxLength") or 0,
                    "decimals": meta.get("scale") or meta.get("decimals") or 0,
                }
            )
        else:
            fields.append({"fieldname": name, "datatype": str(meta)})
    return fields


def _normalize_sap_credentials(creds: Dict[str, Any]) -> Dict[str, Any]:
    """Align dry-run creds with migration (host/server, embedded host:port)."""
    normalized = dict(creds or {})
    host = str(
        normalized.get("host")
        or normalized.get("server")
        or normalized.get("sap_server")
        or ""
    ).strip()
    if host and ":" in host and not host.startswith("["):
        host_part, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            host = host_part
            if normalized.get("port") in (None, ""):
                normalized["port"] = int(maybe_port)
    normalized["host"] = host
    return normalized


def _sap_host_port(creds: Dict[str, Any]) -> Tuple[str, int]:
    host = str(creds.get("host") or "").strip()
    port_raw = creds.get("port")
    try:
        port = int(port_raw) if port_raw not in (None, "") else 443
    except (TypeError, ValueError):
        port = 443
    return host, port


def _odata_records_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """Parse OData entity rows from direct JSON or proxy AlldataSource payloads."""
    if not isinstance(payload, dict):
        return []
    candidate = payload.get("alldataJson") or payload.get("alldata_json") or payload
    if isinstance(candidate, dict) and "alldata_json" in candidate:
        candidate = candidate.get("alldata_json", candidate)
    if not isinstance(candidate, dict):
        return []
    d_block = candidate.get("d", candidate)
    if isinstance(d_block, dict):
        results = d_block.get("results")
        if isinstance(results, list):
            return results
    value = candidate.get("value")
    if isinstance(value, list):
        return value
    return []


def _build_entity_sample_url(
    host: str,
    port: int,
    service: str,
    entity: str,
    extract_columns: List[str],
    top_n: int,
) -> str:
    from services.sap.source import force_https_url

    select_part = ""
    if extract_columns:
        select_part = f"$select={','.join(extract_columns)}&"
    server_address = f"{host}:{port}" if port != 443 else host
    path = (
        f"https://{server_address}/sap/opu/odata/sap/{service}/{entity}/"
        f"?{select_part}$format=json&$top={top_n}"
    )
    return force_https_url(path, server_address=server_address, service_name=service)


def _merge_sample_frames(frames: List[pd.DataFrame]) -> pd.DataFrame:
    """Horizontally merge OData sample batches (same entity, same row order)."""
    if not frames:
        return pd.DataFrame()
    merged = frames[0].copy()
    for extra in frames[1:]:
        for col in extra.columns:
            if col in merged.columns:
                continue
            if len(extra) == len(merged):
                merged[col] = extra[col].values
            else:
                merged[col] = extra[col].reindex(merged.index)
    return merged


def _fetch_sap_sample_once(
    req: DryRunRequest,
    creds: Dict[str, Any],
    host: str,
    port: int,
    service: str,
    entity: str,
    extract_columns: List[str],
    top_n: int,
) -> Tuple[pd.DataFrame, Optional[str]]:
    """Single OData sample request (one $select batch)."""
    url = _build_entity_sample_url(host, port, service, entity, extract_columns, top_n)

    from services.sap.proxy import is_proxy_connection

    if is_proxy_connection(creds):
        from sap_proxy_agent.client.dataynchor_client import AlldataSource
        from services.sap.proxy import _get_proxy_src_conn, _get_proxy_credentials

        proxy_agent, _, _, _, _ = _get_proxy_credentials(creds)
        raw = AlldataSource(
            src_type="sap-odata",
            cust_id=proxy_agent,
            data_url=url,
            source_config=_get_proxy_src_conn(creds),
        )
        records = _odata_records_from_payload(raw)
        if not records:
            return pd.DataFrame(), (
                "SAP returned no rows for the sample request (via proxy). "
                "Check entity name, selected columns, and SAP data."
            )
        return pd.DataFrame(records), None

    from utils.sap_tls_helper import build_session_from_config

    session = build_session_from_config(creds)
    auth = (
        creds.get("username") or creds.get("user") or "",
        creds.get("password") or "",
    )
    resp = session.get(url, auth=auth, timeout=DRY_RUN_SAP_TIMEOUT_SECONDS)
    if resp.status_code != 200:
        logger.warning(
            "SAP sample fetch failed: %s %s",
            resp.status_code,
            (resp.text or "")[:300],
        )
        return pd.DataFrame(), (
            f"SAP sample request failed (HTTP {resp.status_code}). "
            "Dry run continued without live rows."
        )
    records = _odata_records_from_payload(resp.json())
    if not records:
        return pd.DataFrame(), (
            "SAP returned an empty result set for the sample request."
        )
    return pd.DataFrame(records), None


def _fetch_sap_sample(
    req: DryRunRequest, extract_columns: List[str]
) -> Tuple[pd.DataFrame, Optional[str]]:
    """Fetch a small OData sample for dry-run (direct HTTP or proxy agent)."""
    creds = _normalize_sap_credentials(req.source_credentials or {})
    host, port = _sap_host_port(creds)
    service = req.service_name or creds.get("service_name") or ""
    entity = req.table_name
    if service and entity.startswith(f"{service}_"):
        entity = entity[len(service) + 1 :]

    if not host or not service:
        return pd.DataFrame(), (
            "Could not load a SAP sample: missing host or OData service on the connection."
        )

    top_n = min(req.sample_size or DEFAULT_DRY_RUN_ROWS, MAX_DRY_RUN_ROWS)
    batch_size = max(1, DRY_RUN_SAP_SELECT_BATCH_SIZE)
    unique_cols = list(dict.fromkeys(c for c in extract_columns if c))
    batches: List[List[str]] = (
        [unique_cols[i : i + batch_size] for i in range(0, len(unique_cols), batch_size)]
        if unique_cols
        else [[]]
    )

    try:
        frames: List[pd.DataFrame] = []
        last_warning: Optional[str] = None
        for batch in batches:
            frame, warning = _fetch_sap_sample_once(
                req, creds, host, port, service, entity, batch, top_n
            )
            if warning:
                last_warning = warning
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(), last_warning
        if len(batches) > 1:
            logger.info(
                "SAP dry-run sample merged %d $select batches (%d columns)",
                len(batches),
                len(unique_cols),
            )
        return _merge_sample_frames(frames), None
    except Exception as exc:
        logger.warning("SAP sample fetch error: %s", exc)
        method = str(creds.get("connection_method") or "direct").lower()
        hint = (
            " Ensure the Datasyncher proxy agent is online."
            if method == "proxy"
            else (
                " The batch cluster cannot reach this SAP host directly; "
                "use a proxy-agent connection if migrations run through the agent."
            )
        )
        return pd.DataFrame(), f"Could not fetch SAP sample data: {exc}.{hint}"


@router.post("/validate")
def validate_transform(req: ValidateRequest):
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    checklist: List[Dict[str, Any]] = []
    entry_points: List[str] = []

    mapping = _normalize_mapping_json(req.mapping_json)
    data_dest = None
    if mapping is not None:
        if isinstance(req.mapping_json, str):
            _, struct_errors = validate_mapping_json(req.mapping_json)
        else:
            from services.transform.mapping_validator import validate_mapping_structure

            struct_errors = validate_mapping_structure(mapping)
        errors.extend(struct_errors)

        obj_name = (mapping.get("source") or {}).get("objectname")
        entity = req.table_name
        if req.service_name and entity.startswith(f"{req.service_name}_"):
            entity = entity[len(req.service_name) + 1 :]
        from services.transform.destination_table_name import (
            extract_json_target_objectname,
            normalize_destination_table_name,
            normalize_objectname_for_compare,
        )

        if obj_name and normalize_objectname_for_compare(obj_name) != normalize_objectname_for_compare(
            entity
        ):
            warnings.append(
                {
                    "type": "objectname_mismatch",
                    "message": f"JSON source.objectname '{obj_name}' does not match entity '{entity}'",
                }
            )
        json_table = extract_json_target_objectname(mapping)
        if json_table:
            normalised, name_warnings = normalize_destination_table_name(json_table)
            for msg in name_warnings:
                warnings.append({"type": "destination_table_name", "message": msg})
            if normalised:
                data_dest = {
                    "json_table_name": json_table,
                    "destination_table_name": normalised,
                    "destination_name_source": "JSON",
                }
            else:
                data_dest = {
                    "json_table_name": json_table,
                    "destination_table_name": None,
                    "destination_name_source": "DEFAULT",
                }
        else:
            data_dest = None

        pk_warnings = row_filter_warnings(mapping.get("row_filter"), req.primary_keys)
        for w in pk_warnings:
            warnings.append({"type": "row_filter_pk", "message": w})

    if req.script_text:
        checklist, entry_points = validate_script_ast(req.script_text)
        failed = [c for c in checklist if not c.get("passed")]
        for f in failed:
            errors.append(
                {
                    "path": f"script:{f.get('line', 0)}",
                    "message": f.get("message") or f.get("label"),
                }
            )

        if mapping:
            trans = mapping.get("transformation") or {}
            if str(trans.get("type", "")).upper() == "NONE":
                warnings.append(
                    {
                        "type": "transformation_none",
                        "message": "JSON says no transformation, script found",
                    }
                )
            rf = mapping.get("row_filter") or {}
            if rf.get("present"):
                field = (rf.get("condition") or {}).get("field")
                if field and script_has_filter_pattern(req.script_text, field):
                    warnings.append(
                        {
                            "type": "duplicate_filter",
                            "message": f"Filter on '{field}' exists in JSON and script",
                        }
                    )

    sap_live: Set[str] = set((req.table_fields or {}).keys())
    sap_meta = _sap_fields_from_table_fields(req.table_fields)
    if mapping:
        source_fields = mapping.get("source", {}).get("fields") or sap_meta
    else:
        source_fields = sap_meta

    resolved = resolve_effective_mapping(mapping, req.mapping_overrides, sap_meta)
    if mapping:
        resolved["mapping_json"] = mapping
    table = build_mapping_table(
        resolved["rows"],
        source_fields,
        resolved.get("target_fields") or [],
        resolved.get("script_columns") or [],
        sap_live,
        resolved.get("not_selected") or [],
    )

    row_filter_config = dtp_row_filter_to_row_filter_config(
        mapping.get("row_filter") if mapping else None
    )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "checklist": checklist,
        "entry_points": entry_points or (
            [req.entry_point] if req.entry_point else []
        ),
        "counts": {
            "mapped": len(resolved["rows"]),
            "not_selected": len(resolved.get("not_selected") or []),
            "from_script": len(resolved.get("script_columns") or []),
        },
        "mapping_table": table,
        "row_filter_config": row_filter_config,
        "destination": data_dest,
    }


@router.post("/dry-run")
def dry_run_transform(req: DryRunRequest):
    mapping = _normalize_mapping_json(req.mapping_json)
    sap_meta = _sap_fields_from_table_fields(req.table_fields)
    resolved = resolve_effective_mapping(mapping, req.mapping_overrides, sap_meta)
    extract_cols = resolved.get("extract_columns") or []

    sample_df, sample_warning = _fetch_sap_sample(req, extract_cols)
    if sample_df.empty and mapping and mapping.get("source", {}).get("fields"):
        # Synthetic empty frame with source columns for schema-only dry run
        cols = [f["fieldname"] for f in mapping["source"]["fields"][:20]]
        sample_df = pd.DataFrame(columns=cols)

    result = run_dry_run(
        mapping_json=mapping,
        mapping_overrides=req.mapping_overrides,
        script_text=req.script_text or None,
        entry_point=req.entry_point or None,
        run_order=req.run_order,
        sample_df=sample_df,
        sap_live_fields=set(req.table_fields.keys()),
        primary_keys=req.primary_keys,
        sample_size=req.sample_size or DEFAULT_DRY_RUN_ROWS,
        sap_metadata_fields=sap_meta,
    )
    result["row_filter_config"] = dtp_row_filter_to_row_filter_config(
        mapping.get("row_filter") if mapping else None
    )
    if sample_warning:
        result["warnings"] = [
            {"type": "sap_sample", "message": sample_warning},
            *(result.get("warnings") or []),
        ]
    return result
