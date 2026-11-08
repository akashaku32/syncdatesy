"""Convert DTP row_filter JSON to Datasyncher RowFilterConfig."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def dtp_row_filter_to_row_filter_config(row_filter: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row_filter or not row_filter.get("present"):
        return None

    condition = row_filter.get("condition") or {}
    field = condition.get("field")
    if not field:
        return None

    filter_type = str(condition.get("type", "")).upper()
    if filter_type == "IN":
        values = condition.get("values") or []
        return {
            "logic": "and",
            "conditions": [
                {
                    "column": field,
                    "operator": "in",
                    "value": list(values),
                }
            ],
        }

    if filter_type in ("EQ", "="):
        return {
            "logic": "and",
            "conditions": [
                {
                    "column": field,
                    "operator": "eq",
                    "value": condition.get("value"),
                }
            ],
        }

    return None


def row_filter_warnings(
    row_filter: Optional[Dict[str, Any]],
    primary_keys: List[str],
) -> List[str]:
    warnings: List[str] = []
    if not row_filter or not row_filter.get("present"):
        return warnings

    condition = row_filter.get("condition") or {}
    field = condition.get("field")
    if field and primary_keys and field not in primary_keys:
        warnings.append(
            f"Filter field '{field}' is not a primary key column. "
            "Only primary key columns can be used in entity filters for delta load methods. "
            "Keep this filter in the script instead."
        )
    return warnings
