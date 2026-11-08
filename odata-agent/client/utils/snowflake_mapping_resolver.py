"""Resolve effective column mappings from JSON + user overrides."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Set


def _field_index(fields: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {f["fieldname"]: f for f in fields if isinstance(f, dict) and f.get("fieldname")}


def build_identity_mapping(sap_fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for idx, field in enumerate(sap_fields, start=1):
        name = field.get("fieldname")
        if not name:
            continue
        rows.append(
            {
                "position": idx,
                "src_fieldnames": [name],
                "tgt_fieldname": name,
                "mapping_type": "DIRECT",
                "constant_value": None,
                "added_by_user": False,
            }
        )
    return rows


def _apply_ui_overrides(
    base_rows: List[Dict[str, Any]],
    overrides: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Apply the Mapping and transform UI override document.

    Shape: ``addedSources``, ``removedTargets``, and ``mappings`` entries of
    kind ``repoint``, ``rename``, or ``added``.
    """
    removed_ids = {
        str(item) for item in (overrides.get("removedJsonMappings") or [])
    }

    def _kept(row: Dict[str, Any]) -> bool:
        target = row.get("tgt_fieldname")
        if not target:
            return False
        return f"json:{target}" not in removed_ids and f"script:{target}" not in removed_ids

    rows = deepcopy(base_rows)
    removed: Set[str] = {str(name) for name in (overrides.get("removedTargets") or [])}
    rows = [row for row in rows if row.get("tgt_fieldname") not in removed and _kept(row)]
    by_tgt = {row["tgt_fieldname"]: row for row in rows}

    for item in overrides.get("mappings") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        source = item.get("source")
        target = item.get("target")
        json_target = item.get("jsonTarget") or target
        if kind == "repoint" and json_target in by_tgt:
            if source:
                by_tgt[json_target]["src_fieldnames"] = [source]
            else:
                by_tgt[json_target]["src_fieldnames"] = []
            by_tgt[json_target]["user_edit"] = True
        elif kind == "rename" and json_target in by_tgt and target and not by_tgt[json_target].get(
            "from_json_locked"
        ):
            row = by_tgt.pop(json_target)
            row["tgt_fieldname"] = target
            if source:
                row["src_fieldnames"] = [source]
            row["added_by_user"] = True
            by_tgt[target] = row
        elif kind == "added" and source and target and target not in by_tgt:
            row = {
                "position": len(rows) + 1,
                "src_fieldnames": [source],
                "tgt_fieldname": target,
                "mapping_type": "DIRECT",
                "constant_value": None,
                "added_by_user": True,
            }
            rows.append(row)
            by_tgt[target] = row

    present_sources: Set[str] = set()
    for row in by_tgt.values():
        present_sources.update(row.get("src_fieldnames") or [])
    for source in overrides.get("addedSources") or []:
        if not source or source in present_sources or source in by_tgt:
            continue
        row = {
            "position": len(rows) + 1,
            "src_fieldnames": [source],
            "tgt_fieldname": source,
            "mapping_type": "DIRECT",
            "constant_value": None,
            "added_by_user": True,
        }
        rows.append(row)
        by_tgt[source] = row
        present_sources.add(source)

    rows = [row for row in rows if row.get("tgt_fieldname") not in removed and _kept(row)]
    order = [str(name) for name in (overrides.get("targetOrder") or []) if name]
    if not order:
        return rows
    rank = {name.upper(): index for index, name in enumerate(order)}
    return sorted(
        rows,
        key=lambda row: rank.get(str(row.get("tgt_fieldname") or "").upper(), len(rank)),
    )


def apply_overrides(
    base_rows: List[Dict[str, Any]],
    overrides: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not overrides:
        return deepcopy(base_rows)
    if any(key in overrides for key in ("mappings", "addedSources", "removedTargets", "removedJsonMappings")):
        return _apply_ui_overrides(base_rows, overrides)

    rows = deepcopy(base_rows)
    by_tgt = {r["tgt_fieldname"]: r for r in rows}
    removed: Set[str] = set(overrides.get("removed_columns") or [])
    rows = [r for r in rows if r["tgt_fieldname"] not in removed]

    for rename in overrides.get("renames") or []:
        old_tgt = rename.get("from")
        new_tgt = rename.get("to")
        if old_tgt in by_tgt and new_tgt:
            by_tgt[old_tgt]["tgt_fieldname"] = new_tgt

    for added in overrides.get("added_columns") or []:
        src = added.get("src_fieldname") or added.get("sap_column")
        tgt = added.get("tgt_fieldname") or src
        if not src or not tgt:
            continue
        if any(r["tgt_fieldname"] == tgt for r in rows):
            continue
        rows.append(
            {
                "position": len(rows) + 1,
                "src_fieldnames": [src],
                "tgt_fieldname": tgt,
                "mapping_type": "DIRECT",
                "constant_value": None,
                "added_by_user": True,
            }
        )

    for edit in overrides.get("edits") or []:
        tgt = edit.get("tgt_fieldname")
        new_src = edit.get("src_fieldnames")
        new_tgt = edit.get("new_tgt_fieldname")
        for row in rows:
            if row["tgt_fieldname"] == tgt:
                if new_src:
                    row["src_fieldnames"] = new_src
                if new_tgt:
                    row["tgt_fieldname"] = new_tgt
                row["added_by_user"] = row.get("added_by_user", False) or bool(
                    edit.get("user_edit")
                )

    return rows


def effective_source_columns(rows: List[Dict[str, Any]]) -> List[str]:
    cols: List[str] = []
    seen: Set[str] = set()
    for row in rows:
        for src in row.get("src_fieldnames") or []:
            if src and src not in seen:
                seen.add(src)
                cols.append(src)
    return cols


def resolve_effective_mapping(
    mapping_json: Optional[Dict[str, Any]],
    mapping_overrides: Optional[Dict[str, Any]],
    sap_metadata_fields: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return resolved mapping rows and extraction column list."""
    if mapping_json and mapping_json.get("field_mapping"):
        base_rows = deepcopy(mapping_json["field_mapping"])
    elif sap_metadata_fields:
        base_rows = build_identity_mapping(sap_metadata_fields)
    else:
        base_rows = []

    rows = apply_overrides(base_rows, mapping_overrides)
    extract_cols = effective_source_columns(rows)

    target_fields = []
    if mapping_json and mapping_json.get("target", {}).get("fields"):
        target_fields = mapping_json["target"]["fields"]

    removed_ids = {
        str(item)
        for item in ((mapping_overrides or {}).get("removedJsonMappings") or [])
    }
    mapped_tgt = {r["tgt_fieldname"] for r in rows}
    script_cols = []
    if target_fields:
        for tf in target_fields:
            fn = tf.get("fieldname")
            if (
                fn
                and fn not in mapped_tgt
                and f"json:{fn}" not in removed_ids
                and f"script:{fn}" not in removed_ids
            ):
                script_cols.append(fn)

    source_fields = []
    if mapping_json and mapping_json.get("source", {}).get("fields"):
        source_fields = mapping_json["source"]["fields"]
    elif sap_metadata_fields:
        source_fields = sap_metadata_fields

    mapped_src: Set[str] = set()
    for r in rows:
        mapped_src.update(r.get("src_fieldnames") or [])

    not_selected = [
        f["fieldname"]
        for f in source_fields
        if isinstance(f, dict) and f.get("fieldname") and f["fieldname"] not in mapped_src
    ]

    return {
        "rows": rows,
        "extract_columns": extract_cols,
        "not_selected": not_selected,
        "script_columns": script_cols,
        "target_fields": target_fields,
        "source_fields": source_fields,
    }
