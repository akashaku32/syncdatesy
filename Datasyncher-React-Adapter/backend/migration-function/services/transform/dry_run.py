"""Dry-run transform against a SAP OData sample."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import pandas as pd

from config.transform_limits import (
    DEFAULT_DRY_RUN_ROWS,
    DRY_RUN_PREVIEW_ROWS,
    MAX_DRY_RUN_ROWS,
)
from services.transform.executor import (
    apply_column_mapping,
    check_snowflake_value_warnings,
    execute_transform_chunk,
    validate_output_schema,
)
from services.transform.mapping_resolver import resolve_effective_mapping
from services.transform.mapping_status import build_mapping_table, compute_row_status
from services.transform.mapping_validator import validate_mapping_json
from services.transform.row_filter_bridge import dtp_row_filter_to_row_filter_config, row_filter_warnings
from services.transform.script_runner import (
    effective_script_text,
    run_transform,
    ScriptRuntimeError,
)
from services.transform.script_validator import script_has_filter_pattern, validate_script_ast


def run_dry_run(
    mapping_json: Optional[Dict[str, Any]],
    mapping_overrides: Optional[Dict[str, Any]],
    script_text: Optional[str],
    entry_point: Optional[str],
    run_order: str,
    sample_df: pd.DataFrame,
    sap_live_fields: Optional[Set[str]] = None,
    primary_keys: Optional[List[str]] = None,
    sample_size: int = DEFAULT_DRY_RUN_ROWS,
    sap_metadata_fields: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    sample_size = min(max(1, sample_size), MAX_DRY_RUN_ROWS)
    input_df = sample_df.head(sample_size).copy()
    script_text = effective_script_text(script_text)

    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    resolved = resolve_effective_mapping(
        mapping_json, mapping_overrides, sap_metadata_fields
    )
    target_fields = resolved.get("target_fields") or []
    expected_cols = [
        row["tgt_fieldname"] for row in resolved["rows"] if row.get("tgt_fieldname")
    ]
    if script_text:
        for name in resolved.get("script_columns") or []:
            if name not in expected_cols:
                expected_cols.append(name)

    if mapping_json and mapping_json.get("row_filter", {}).get("present"):
        pk_warnings = row_filter_warnings(mapping_json.get("row_filter"), primary_keys or [])
        for w in pk_warnings:
            warnings.append({"type": "row_filter_pk", "message": w})

    if script_text and mapping_json:
        rf = mapping_json.get("row_filter") or {}
        if rf.get("present"):
            field = (rf.get("condition") or {}).get("field")
            if field and script_has_filter_pattern(script_text, field):
                warnings.append(
                    {
                        "type": "duplicate_filter",
                        "message": f"Row filter on '{field}' exists in both JSON and script. Keep it in one place.",
                    }
                )

        trans = mapping_json.get("transformation") or {}
        if str(trans.get("type", "")).upper() == "NONE" and script_text.strip():
            warnings.append(
                {
                    "type": "transformation_none",
                    "message": "JSON says no transformation, but a script was provided.",
                }
            )

    rows_in = len(input_df)
    output_df: Optional[pd.DataFrame] = None

    try:
        if script_text:
            output_df = execute_transform_chunk(
                input_df,
                mapping_json,
                mapping_overrides,
                script_text,
                entry_point,
                run_order,
                sap_metadata_fields=sap_metadata_fields,
            )
        elif mapping_json:
            output_df = apply_column_mapping(input_df, resolved["rows"])
        else:
            output_df = input_df.copy()
    except ScriptRuntimeError as exc:
        errors.append(
            {
                "type": "script_error",
                "message": str(exc),
                "line": exc.line,
                "traceback": exc.traceback_text,
            }
        )
        return _result_payload(
            rows_in,
            0,
            input_df,
            None,
            warnings,
            errors,
            resolved,
            sap_live_fields,
            preview_rows=min(sample_size, DRY_RUN_PREVIEW_ROWS, MAX_DRY_RUN_ROWS),
            expected_columns=expected_cols,
        )

    rows_out = len(output_df) if output_df is not None else 0
    schema_issues = validate_output_schema(output_df, expected_cols) if output_df is not None else []
    mapped_targets = set(expected_cols)
    warning_fields = [
        field
        for field in target_fields
        if isinstance(field, dict) and field.get("fieldname") in mapped_targets
    ]
    value_warnings = (
        check_snowflake_value_warnings(output_df, warning_fields) if output_df is not None else []
    )
    warnings.extend(value_warnings)
    warnings.extend({"type": "schema", "message": i["message"]} for i in schema_issues)

    preview_rows = min(sample_size, DRY_RUN_PREVIEW_ROWS, MAX_DRY_RUN_ROWS)
    return _result_payload(
        rows_in,
        rows_out,
        input_df,
        output_df,
        warnings,
        errors,
        resolved,
        sap_live_fields,
        preview_rows,
        expected_cols,
    )


def _preview_frame(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """Keep mapped columns only, in mapping order."""
    if df is None or df.empty and not columns:
        return df
    keep = [name for name in columns if name in df.columns]
    if not keep:
        return df.iloc[:, 0:0]
    return df.loc[:, keep]


def _result_payload(
    rows_in: int,
    rows_out: int,
    input_df: pd.DataFrame,
    output_df: Optional[pd.DataFrame],
    warnings: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    resolved: Dict[str, Any],
    sap_live_fields: Optional[Set[str]],
    preview_rows: int = DRY_RUN_PREVIEW_ROWS,
    expected_columns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    preview_rows = max(1, min(preview_rows, MAX_DRY_RUN_ROWS))
    source_fields = resolved.get("source_fields") or []
    target_fields = resolved.get("target_fields") or []
    mapped_sources = list(resolved.get("extract_columns") or [])
    mapped_targets = expected_columns or [
        row["tgt_fieldname"] for row in resolved["rows"] if row.get("tgt_fieldname")
    ]
    input_preview_df = _preview_frame(input_df, mapped_sources)
    output_preview_df = (
        _preview_frame(output_df, mapped_targets) if output_df is not None else None
    )
    table = build_mapping_table(
        resolved["rows"],
        source_fields,
        target_fields,
        resolved.get("script_columns") or [],
        sap_live_fields,
        resolved.get("not_selected") or [],
    )

    status_counts: Dict[str, int] = {}
    for row in table:
        st = row["status"]
        status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "columns_out": list(output_preview_df.columns) if output_preview_df is not None else [],
        "expected_columns": mapped_targets,
        "warnings": warnings,
        "errors": errors,
        "schema_check": {
            "passed": not any(w.get("type") == "schema" for w in warnings),
            "column_count": len(output_preview_df.columns) if output_preview_df is not None else 0,
        },
        "counts": {
            "mapped": len(resolved["rows"]),
            "not_selected": len(resolved.get("not_selected") or []),
            "from_script": len(resolved.get("script_columns") or []),
            "status": status_counts,
        },
        "mapping_table": table,
        "input_preview": input_preview_df.head(preview_rows).to_dict(orient="records")
        if len(input_preview_df)
        else [],
        "output_preview": output_preview_df.head(preview_rows).to_dict(orient="records")
        if output_preview_df is not None and len(output_preview_df)
        else [],
        "preview_rows": preview_rows,
        "row_filter_config": None,
    }
