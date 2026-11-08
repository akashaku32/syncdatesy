from __future__ import annotations
from typing import Any, Dict, List, Optional
import pandas as pd
from utils.snowflake_mapping_resolver import resolve_effective_mapping
from utils.transform.script_runner import effective_script_text,run_transform
from utils.transform.dataframe_columns import mapping_source_series, resolve_dataframe_column, consolidate_target_columns, align_dataframe_to_names
from utils.transform.one_to_many import replicate_one_to_many_sources

RUN_ORDER_FILTER_SCRIPT_MAPPING = "filter_script_mapping"
RUN_ORDER_MAPPING_SCRIPT = "mapping_script"


def apply_column_mapping(
    df: pd.DataFrame,
    rows: List[Dict[str, Any]],
    script_output_columns: Optional[set] = None,
    source_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Copy each mapped source into its target, then keep only mapped columns.

    A source used by more than one target (FISCPER -> FISCPER and FISCPER_2)
    is copied, not renamed, so every target keeps the source values.
    """
    # Columns produced only by the script (no JSON field_mapping row). Mapped targets
    # are always filled from src_fieldnames even when the script also outputs them.
    script_only_columns = script_output_columns or set()
    extract_df = source_df if source_df is not None else df
    out = df.copy()
    target_cols: List[str] = []
    seen = set()
    for row in rows:
        tgt = row.get("tgt_fieldname")
        if not tgt or tgt in seen:
            continue
        seen.add(tgt)
        target_cols.append(tgt)
        srcs = row.get("src_fieldnames") or []
        if not srcs:
            if tgt in script_only_columns:
                continue
            if tgt not in out.columns:
                out[tgt] = None
            continue
        src = srcs[0]
        series = mapping_source_series(extract_df, df, src)
        if series is not None:
            out[tgt] = series.copy()
        elif resolve_dataframe_column(list(out.columns), tgt) is None:
            out[tgt] = None

    out = replicate_one_to_many_sources(out, rows, extract_df, df)
    out = consolidate_target_columns(out, target_cols)
    out = align_dataframe_to_names(out, target_cols)
    for col in target_cols:
        if col not in out.columns:
            out[col] = None

    ordered = [c for c in target_cols if c in out.columns]
    extra_script = [
        c for c in out.columns if c not in ordered and c in script_only_columns
    ]
    return out[ordered + extra_script] if ordered else out


def execute_transform_chunk(
    df: pd.DataFrame,
    mapping_json: Optional[Dict[str, Any]],
    mapping_overrides: Optional[Dict[str, Any]],
    script_text: Optional[str],
    entry_point: Optional[str],
    run_order: str = RUN_ORDER_FILTER_SCRIPT_MAPPING,
    sap_metadata_fields: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """
    Apply mapping + script to a DataFrame chunk.
    SAP row filter is applied before this call in the pipeline.
    """
    resolved = resolve_effective_mapping(
        mapping_json, mapping_overrides, sap_metadata_fields
    )
    rows = resolved["rows"]
    script_cols = set(resolved.get("script_columns") or [])
    script_text = effective_script_text(script_text)

    if not script_text:
        return apply_column_mapping(df, rows)

    sap_extract = df.copy()
    if run_order == RUN_ORDER_MAPPING_SCRIPT:
        mapped = apply_column_mapping(df, rows)
        result, _ = run_transform(script_text, mapped, entry_point)
        return apply_column_mapping(
            result, rows, script_output_columns=script_cols, source_df=mapped
        )

    # Script first on raw SAP extract, then mapping (re-fill targets from SAP when aligned)
    result, meta = run_transform(script_text, df, entry_point)
    return apply_column_mapping(
        result, rows, script_output_columns=script_cols, source_df=sap_extract
    )

