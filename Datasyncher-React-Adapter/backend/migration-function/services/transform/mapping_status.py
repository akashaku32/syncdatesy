"""Compute per-row mapping status for UI and validation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


def _field_meta(fields: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for f in fields:
        if f.get("fieldname") == name:
            return f
    return None


def _sap_type_key(meta: Optional[Dict[str, Any]]) -> tuple:
    if not meta:
        return ("", 0, 0)
    return (
        str(meta.get("datatype") or ""),
        int(meta.get("length") or 0),
        int(meta.get("decimals") or 0),
    )


def format_type_label(meta: Optional[Dict[str, Any]]) -> str:
    if not meta:
        return ""
    datatype = str(meta.get("datatype") or "")
    length = int(meta.get("length") or 0)
    decimals = int(meta.get("decimals") or 0)
    if datatype == "CURR":
        return "CURR"
    if datatype == "DATS":
        return "DATS"
    if length:
        return f"{datatype} {length}"
    return datatype


def format_column_label(name: Optional[str], meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not name:
        return None
    if not meta:
        return name
    datatype = str(meta.get("datatype") or "")
    length = int(meta.get("length") or 0)
    decimals = int(meta.get("decimals") or 0)
    if datatype == "CURR" and length:
        return f"{name} ({length},{decimals})"
    return name


def compute_row_status(
    row: Dict[str, Any],
    source_fields: List[Dict[str, Any]],
    target_fields: List[Dict[str, Any]],
    sap_live_fields: Optional[Set[str]] = None,
) -> str:
    tgt = row.get("tgt_fieldname", "")
    src_names = row.get("src_fieldnames") or []
    constant = row.get("constant_value")

    if constant is not None and constant != "":
        return "Constant"

    if not src_names:
        return "From script"

    src_name = src_names[0]
    if sap_live_fields is not None and src_name not in sap_live_fields:
        return "Not in SAP"

    src_meta = _field_meta(source_fields, src_name)
    tgt_meta = _field_meta(target_fields, tgt)

    if row.get("added_by_user"):
        return "Added by user"

    src_type = _sap_type_key(src_meta)
    tgt_type = _sap_type_key(tgt_meta)

    if src_type[0] == "DATS" and tgt_type[0] in ("DATS", "DATE", ""):
        return "Date check"

    if src_type[0] == "CURR" and tgt_type[0] == "CURR":
        if (src_type[1], src_type[2]) != (tgt_type[1], tgt_type[2]):
            if tgt_type[1] < src_type[1] or tgt_type[2] < src_type[2]:
                return "Narrowed"

    if src_name != tgt:
        return "Direct"

    return "Direct"


def build_mapping_table(
    rows: List[Dict[str, Any]],
    source_fields: List[Dict[str, Any]],
    target_fields: List[Dict[str, Any]],
    script_columns: List[str],
    sap_live_fields: Optional[Set[str]] = None,
    not_selected: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    result = []
    for row in rows:
        src_names = [n for n in (row.get("src_fieldnames") or []) if n]
        tgt = row.get("tgt_fieldname", "")
        status = compute_row_status(row, source_fields, target_fields, sap_live_fields)
        src_meta = _field_meta(source_fields, src_names[0]) if src_names else None
        tgt_meta = _field_meta(target_fields, tgt)
        sap_labels = [format_column_label(n, _field_meta(source_fields, n)) for n in src_names]
        result.append(
            {
                "sap_column": ", ".join([label for label in sap_labels if label]) or None,
                "sap_columns": src_names,
                "target_column": format_column_label(tgt, tgt_meta) or tgt,
                "renamed": bool(src_names) and not any(n == tgt for n in src_names),
                "status": status,
                "type_label": format_type_label(tgt_meta or src_meta),
                "source_type": src_meta,
                "target_type": tgt_meta,
                "constant_value": row.get("constant_value"),
                "added_by_user": row.get("added_by_user", False),
            }
        )

    mapped_tgt = {r["tgt_fieldname"] for r in rows}
    for col in script_columns:
        if col in mapped_tgt:
            continue
        tgt_meta = _field_meta(target_fields, col)
        result.append(
            {
                "sap_column": None,
                "sap_columns": [],
                "target_column": format_column_label(col, tgt_meta) or col,
                "renamed": False,
                "status": "From script",
                "type_label": format_type_label(tgt_meta),
                "source_type": None,
                "target_type": tgt_meta,
                "constant_value": None,
                "added_by_user": False,
            }
        )

    for src_name in not_selected or []:
        src_meta = _field_meta(source_fields, src_name)
        result.append(
            {
                "sap_column": format_column_label(src_name, src_meta) or src_name,
                "sap_columns": [src_name],
                "target_column": None,
                "renamed": False,
                "status": "Dropped",
                "type_label": format_type_label(src_meta),
                "source_type": src_meta,
                "target_type": None,
                "constant_value": None,
                "added_by_user": False,
            }
        )

    return result
