"""One SAP source field mapped to multiple target columns (any field, not special-cased)."""

from __future__ import annotations
import logging
from collections import defaultdict
from typing import Any, Dict, List
import pandas as pd
from utils.transform.dataframe_columns import mapping_source_series
logger = logging.getLogger(__name__)

def targets_by_source(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Map each source fieldname to ordered target fieldnames that use it."""
    by_source: Dict[str, List[str]] = defaultdict(list)
    seen: set = set()
    for row in rows:
        tgt = row.get("tgt_fieldname")
        srcs = row.get("src_fieldnames") or []
        if not tgt or not srcs:
            continue
        src = str(srcs[0])
        key = (src, tgt)
        if key in seen:
            continue
        seen.add(key)
        if tgt not in by_source[src]:
            by_source[src].append(tgt)
    return dict(by_source)


def one_to_many_groups(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Sources that feed more than one target column."""
    return {
        src: targets for src, targets in targets_by_source(rows).items() if len(targets) > 1
    }


def replicate_one_to_many_sources(
    out: pd.DataFrame,
    rows: List[Dict[str, Any]],
    extract_df: pd.DataFrame,
    result_df: pd.DataFrame,
) -> pd.DataFrame:
    """Copy each shared source into every mapped target (identity + suffix targets)."""
    frame = out.copy()
    for src, targets in one_to_many_groups(rows).items():
        series = mapping_source_series(extract_df, result_df, src)
        if series is None:
            continue
        for tgt in targets:
            frame[tgt] = series.copy()
    return frame


def log_one_to_many_column_fill(
    df: pd.DataFrame,
    rows: List[Dict[str, Any]],
    log_prefix: str = "[SAP -> SF]",
) -> None:
    """Log non-null counts for every one-to-many target (diagnostics only)."""
    if df.empty:
        return
    for src, targets in one_to_many_groups(rows).items():
        for tgt in targets:
            if tgt not in df.columns:
                logger.warning(
                    "%s one-to-many %s -> %s: target column missing after transform",
                    log_prefix,
                    src,
                    tgt,
                )
                continue
            filled = int(df[tgt].notna().sum())
            logger.info(
                "%s one-to-many %s -> %s non-null %d/%d",
                log_prefix,
                src,
                tgt,
                filled,
                len(df),
            )
