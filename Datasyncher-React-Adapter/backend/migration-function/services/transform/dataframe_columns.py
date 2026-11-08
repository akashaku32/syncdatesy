"""Helpers for matching SAP OData / pandas column names to mapping names."""

from __future__ import annotations

from typing import List, Optional, Sequence

import pandas as pd


def resolve_dataframe_column(
    columns: Sequence[str], name: Optional[str]
) -> Optional[str]:
    """Return the actual DataFrame column name for a mapping source (case-insensitive)."""
    if not name:
        return None
    if name in columns:
        return name
    upper = str(name).upper()
    for col in columns:
        if str(col).upper() == upper:
            return col
    return None


def resolve_source_column(df: pd.DataFrame, source_name: Optional[str]) -> Optional[str]:
    """Pick the frame column that holds SAP values for *source_name*.

    When a script adds an empty homonym (e.g. null ``FIELD``) while the OData
    field still lives under another case variant, prefer the column that has data.
    """
    if not source_name or df is None or df.empty:
        return resolve_dataframe_column(list(df.columns) if df is not None else [], source_name)
    columns = list(df.columns)
    matches = [c for c in columns if str(c).upper() == str(source_name).upper()]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    for col in matches:
        try:
            if df[col].notna().any():
                return col
        except (KeyError, TypeError, ValueError):
            continue
    return matches[0]


def mapping_source_series(
    source_df: pd.DataFrame,
    result_df: pd.DataFrame,
    source_name: str,
) -> Optional[pd.Series]:
    """Return SAP values for *source_name* aligned to *result_df* rows."""
    for frame in (result_df, source_df):
        col = resolve_source_column(frame, source_name)
        if col is None:
            continue
        series = frame[col]
        if frame is result_df or len(source_df) == len(result_df):
            if series.notna().any():
                return series
        common = result_df.index.intersection(source_df.index)
        if len(common) == len(result_df) and len(result_df) > 0:
            src_col = resolve_source_column(source_df, source_name)
            if src_col is not None:
                aligned = source_df.loc[result_df.index, src_col]
                if aligned.notna().any():
                    return aligned
    col = resolve_source_column(result_df, source_name)
    if col is not None:
        return result_df[col]
    return None


def consolidate_target_columns(
    df: pd.DataFrame, target_names: List[str]
) -> pd.DataFrame:
    """One canonical column per target; drop case-only duplicates."""
    if df.empty or not target_names:
        return df
    out = df.copy()
    for target in target_names:
        matches = [c for c in out.columns if str(c).upper() == str(target).upper()]
        if not matches:
            continue
        best = resolve_source_column(out, target)
        if best is None:
            continue
        out[target] = out[best].copy()
        for col in matches:
            if col != target:
                out.drop(columns=[col], errors="ignore", inplace=True)
    return out


def align_dataframe_to_names(
    df: pd.DataFrame, target_names: List[str]
) -> pd.DataFrame:
    """Rename DataFrame columns to match Snowflake / target names when casing differs."""
    if df.empty or not target_names:
        return df
    by_upper = {str(c).upper(): c for c in df.columns}
    rename = {}
    for target in target_names:
        if target in df.columns:
            continue
        actual = by_upper.get(str(target).upper())
        if actual is not None and actual != target:
            rename[actual] = target
    if rename:
        return df.rename(columns=rename)
    return df
