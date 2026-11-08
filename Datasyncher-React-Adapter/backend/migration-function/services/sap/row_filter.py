"""
SAP OData $filter builder.

Supported operator surface (operator-based UI):

  - String fields (SAP ODP-safe):
        eq, ne, gt, ge, lt, le, startswith, in

  - Numeric / Decimal fields (unrestricted):
        eq, ne, gt, ge, lt, le, in

  - Boolean fields:
        eq

  - Time fields (unrestricted):
        eq, ne, gt, ge, lt, le, in

  - DateTime fields (unrestricted):
        eq, ne, gt, ge, lt, le, in

  - Interval-restricted fields (sap:filter-restriction="interval"):
        ge, gt, le, lt (eq, ne, in are not allowed)

Date / DateTime Precision & Range Generation:
  - Year Precision (e.g. "2026"):
        eq -> (FIELD ge datetime'2026-01-01T00:00:00' and FIELD le datetime'2026-12-31T23:59:59')
        ge -> FIELD ge datetime'2026-01-01T00:00:00'
        gt -> FIELD gt datetime'2026-12-31T23:59:59'
        le -> FIELD le datetime'2026-12-31T23:59:59'
        lt -> FIELD lt datetime'2026-01-01T00:00:00'

  - Year & Month Precision (e.g. "2026-03" or "2026 March"):
        eq -> (FIELD ge datetime'2026-03-01T00:00:00' and FIELD le datetime'2026-03-31T23:59:59')
        ge -> FIELD ge datetime'2026-03-01T00:00:00'
        gt -> FIELD gt datetime'2026-03-31T23:59:59'
        le -> FIELD le datetime'2026-03-31T23:59:59'
        lt -> FIELD lt datetime'2026-03-01T00:00:00'

  - Multiple values / buckets on the SAME field:
        Joined with 'OR', e.g.:
        ((FIELD ge Feb_Start and FIELD le Feb_End) or (FIELD ge Mar_Start and FIELD le Mar_End))

  - Conditions across DIFFERENT fields:
        Joined with 'AND', e.g.:
        (Period Range OR Period Range) and (SALESORG eq '1000')

Parenthesizing Rules:
  - Any multi-clause range group or OR group is wrapped in parentheses.
"""

import calendar
import logging
import re
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from models.sap_row_filter import RowFilterConfig, RowFilterCondition, RowFilterNot
from utils.timing_utils import log_execution_time

logger = logging.getLogger(__name__)

MAX_ROW_FILTER_CONDITIONS = 20

# --------------------------------------------------------------------------
# Type mapping
# --------------------------------------------------------------------------

FILTER_TYPE_BY_EDM_TYPE = {
    "Edm.String": "string",
    "Edm.Guid": "string",
    "Edm.Binary": "string",
    "Edm.Byte": "numeric",
    "Edm.SByte": "numeric",
    "Edm.Int16": "numeric",
    "Edm.Int32": "numeric",
    "Edm.Int64": "numeric",
    "Edm.Decimal": "numeric",
    "Edm.Double": "numeric",
    "Edm.Single": "numeric",
    "Edm.Boolean": "boolean",
    "Edm.DateTime": "datetime",
    "Edm.DateTimeOffset": "datetime",
    "Edm.Time": "time",
}

INTERVAL_CAPABLE_FILTER_TYPES = {"numeric", "datetime", "time"}

UNRESTRICTED_OPERATORS_BY_FILTER_TYPE = {
    # Verified SAP ODP surface: comparison operators work on string fields
    # lexicographically, and startswith must be emitted as a bare function.
    # substringof/endswith are intentionally excluded because this entity
    # either errors or silently ignores them server-side.
    "string": {"eq", "ne", "gt", "ge", "lt", "le", "startswith", "in"},
    "numeric": {"eq", "ne", "gt", "ge", "lt", "le", "in"},
    "boolean": {"eq"},
    "time": {"eq", "ne", "gt", "ge", "lt", "le", "in"},
    "datetime": {"eq", "ne", "gt", "ge", "lt", "le", "in"},
}

INTERVAL_OPERATORS = {"ge", "gt", "le", "lt"}

MONTH_NAME_TO_INT = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _escape_str(value: str) -> str:
    """Escapes single quotes to prevent OData literal injection."""
    return str(value).replace("'", "''")


# --------------------------------------------------------------------------
# Date/DateTime Value Parsing & Renderers
# --------------------------------------------------------------------------

def _parse_year_or_month(v: Any) -> Optional[Tuple[str, int, Optional[int]]]:
    """
    Inspects string/int value to check if it represents:
    - Year precision ("2026" or 2026) -> ("year", 2026, None)
    - Year & Month precision ("2026-03", "2026 March", "March 2026") -> ("month", 2026, 3)
    Returns None if value is a full date/datetime.
    """
    if v is None or isinstance(v, (list, dict)):
        return None
    s = str(v).strip()

    # Pure 4-digit year: 2026
    if re.fullmatch(r"\d{4}", s):
        return ("year", int(s), None)

    # Year-Month: 2026-03 or 2026/03
    m = re.fullmatch(r"(\d{4})[-/](0?[1-9]|1[0-2])", s)
    if m:
        return ("month", int(m.group(1)), int(m.group(2)))

    # Month name and year: "2026 March", "March 2026", "2026-March", etc.
    m1 = re.fullmatch(r"(\d{4})[\s\-_,]+([A-Za-z]+)", s)
    if m1 and m1.group(2).lower() in MONTH_NAME_TO_INT:
        return ("month", int(m1.group(1)), MONTH_NAME_TO_INT[m1.group(2).lower()])

    m2 = re.fullmatch(r"([A-Za-z]+)[\s\-_,]+(\d{4})", s)
    if m2 and m2.group(1).lower() in MONTH_NAME_TO_INT:
        return ("month", int(m2.group(2)), MONTH_NAME_TO_INT[m1.group(1).lower()])

    return None


def _format_datetime_literal(v: Any) -> str:
    """Formats string/datetime into standard OData literal string YYYY-MM-DDTHH:MM:SS."""
    s = str(v).strip()
    if s.startswith("datetime'") and s.endswith("'"):
        s = s[9:-1]

    if "T" in s:
        parts = s.split("T")
        date_p = parts[0]
        time_p = parts[1].split(".")[0].split("+")[0].split("-")[0].replace("Z", "")
        if len(time_p) == 5:  # HH:MM
            time_p += ":00"
        elif not time_p:
            time_p = "00:00:00"
        return f"{date_p}T{time_p}"

    if len(s) == 10 and s.count("-") == 2:
        return f"{s}T00:00:00"

    return s


def _render_datetime_clause(field: str, op: str, val: Any) -> str:
    """Renders single condition or range expression for a datetime field using ge + le for range bounds."""
    if op == "in":
        val_list = val if isinstance(val, list) else [val]
        clauses = [_render_datetime_clause(field, "eq", x) for x in val_list]
        return "(" + " or ".join(clauses) + ")"

    precision_match = _parse_year_or_month(val)
    if precision_match:
        p_type, year, month = precision_match
        if p_type == "year":
            start_dt = f"{year:04d}-01-01T00:00:00"
            end_dt = f"{year:04d}-12-31T23:59:59"
        else:
            _, last_day = calendar.monthrange(year, month)
            start_dt = f"{year:04d}-{month:02d}-01T00:00:00"
            end_dt = f"{year:04d}-{month:02d}-{last_day:02d}T23:59:59"

        if op == "eq":
            return f"({field} ge datetime'{start_dt}' and {field} le datetime'{end_dt}')"
        elif op == "ne":
            return f"({field} lt datetime'{start_dt}' or {field} gt datetime'{end_dt}')"
        elif op == "ge":
            return f"{field} ge datetime'{start_dt}'"
        elif op == "gt":
            return f"{field} gt datetime'{end_dt}'"
        elif op == "le":
            return f"{field} le datetime'{end_dt}'"
        elif op == "lt":
            return f"{field} lt datetime'{start_dt}'"

    dt_str = _format_datetime_literal(val)
    return f"{field} {op} datetime'{dt_str}'"


STRING_OPS = {
    "eq": lambda f, v: f"{f} eq null" if v is None else f"{f} eq '{_escape_str(v)}'",
    "ne": lambda f, v: f"{f} ne null" if v is None else f"{f} ne '{_escape_str(v)}'",
    "gt": lambda f, v: f"{f} gt '{_escape_str(v)}'",
    "ge": lambda f, v: f"{f} ge '{_escape_str(v)}'",
    "lt": lambda f, v: f"{f} lt '{_escape_str(v)}'",
    "le": lambda f, v: f"{f} le '{_escape_str(v)}'",
    "startswith": lambda f, v: f"startswith({f}, '{_escape_str(v)}')",
    "in": lambda f, v: "(" + " or ".join(f"{f} eq '{_escape_str(x)}'" for x in v) + ")",
}


def _numeric_literal(value: Any) -> str:
    """Return a safe OData numeric literal or reject it."""
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        raise ValueError("Numeric filter values must be scalar numbers.")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid numeric filter value: {value!r}.") from exc
    if not number.is_finite():
        raise ValueError(f"Invalid numeric filter value: {value!r}.")
    return format(number, "f")


def _boolean_literal(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    normalized = str(value).strip().lower()
    if normalized in {"true", "false"}:
        return normalized
    raise ValueError(f"Invalid boolean filter value: {value!r}.")


def _validate_renderable_condition(cond: Any, filter_type: str) -> None:
    """Defensive validation for callers that build without metadata validation."""
    if cond.operator not in UNRESTRICTED_OPERATORS_BY_FILTER_TYPE.get(filter_type, set()) and not (
        cond.operator in INTERVAL_OPERATORS and filter_type in INTERVAL_CAPABLE_FILTER_TYPES
    ):
        raise ValueError(
            f"Operator '{cond.operator}' is not supported for '{filter_type}' SAP OData filters."
        )
    if cond.value is None and cond.operator in {"eq", "ne"}:
        return
    if cond.value is None:
        raise ValueError(f"Operator '{cond.operator}' requires a value.")
    values = cond.value if cond.operator == "in" else [cond.value]
    if cond.operator == "in" and (not isinstance(values, list) or not values):
        raise ValueError("Operator 'in' requires a non-empty list of values.")
    if filter_type == "numeric":
        for value in values:
            _numeric_literal(value)
    elif filter_type == "boolean":
        if cond.operator != "eq":
            raise ValueError("Boolean SAP OData filters support only eq.")
        _boolean_literal(values[0])
NUMERIC_OPS = {
    "eq": lambda f, v: f"{f} eq {v}",
    "ne": lambda f, v: f"{f} ne {v}",
    "gt": lambda f, v: f"{f} gt {v}",
    "ge": lambda f, v: f"{f} ge {v}",
    "lt": lambda f, v: f"{f} lt {v}",
    "le": lambda f, v: f"{f} le {v}",
    "in": lambda f, v: "(" + " or ".join(f"{f} eq {x}" for x in v) + ")",
}
BOOLEAN_OPS = {
    "eq": lambda f, v: f"{f} eq {str(v).lower()}",
}
TIME_OPS = {
    "eq": lambda f, v: f"{f} eq time'{v}'",
    "ne": lambda f, v: f"{f} ne time'{v}'",
    "gt": lambda f, v: f"{f} gt time'{v}'",
    "ge": lambda f, v: f"{f} ge time'{v}'",
    "lt": lambda f, v: f"{f} lt time'{v}'",
    "le": lambda f, v: f"{f} le time'{v}'",
    "in": lambda f, v: "(" + " or ".join(f"{f} eq time'{x}'" for x in v) + ")",
}

OP_MAP_BY_FILTER_TYPE = {
    "string": STRING_OPS,
    "numeric": NUMERIC_OPS,
    "datetime": None,  # handled by _render_datetime_clause
    "boolean": BOOLEAN_OPS,
    "time": TIME_OPS,
}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _resolve_column(cond, columns_by_name: Dict[str, Any]):
    col = columns_by_name.get(cond.column) or columns_by_name.get(cond.column.lower())
    if not col:
        raise ValueError(f"Column '{cond.column}' does not exist on this entity.")
    cond.column = col["name"]  # normalize casing to metadata
    return col


def _as_node(value: Any) -> Any:
    """Normalize JSON nodes while accepting already-parsed Pydantic objects."""
    if isinstance(value, (RowFilterCondition, RowFilterNot, RowFilterConfig)):
        return value
    if isinstance(value, dict):
        if "not" in value:
            return RowFilterNot(**value)
        if "conditions" in value:
            return RowFilterConfig(**value)
        return RowFilterCondition(**value)
    return value


def _is_interval_restricted(col: Dict[str, Any]) -> bool:
    return (col.get("filterRestriction") or col.get("filter_restriction") or "").lower() == "interval"


def _allowed_operators_for(col: Dict[str, Any], filter_type: str) -> set:
    """Single source of truth: which operators a column may use, given
    its EDM-derived filter_type and its sap:filter-restriction."""
    if _is_interval_restricted(col):
        if filter_type not in INTERVAL_CAPABLE_FILTER_TYPES:
            raise ValueError(
                f"Interval restriction is not supported for filter type '{filter_type}'."
            )
        return INTERVAL_OPERATORS

    unrestricted_ops = UNRESTRICTED_OPERATORS_BY_FILTER_TYPE.get(filter_type)
    if unrestricted_ops is None:
        raise ValueError(
            f"Column of type '{filter_type}' is not filterable."
        )
    return unrestricted_ops


@log_execution_time
def validate_row_filter_config(
    config: RowFilterConfig,
    columns_metadata: List[Dict[str, Any]],
) -> None:
    """
    Validate row filter configuration against entity column metadata.

    Raises:
        ValueError: If any validation rule is violated.
    """
    logger.info("Validating row filter configuration...")

    if len(config.conditions) > MAX_ROW_FILTER_CONDITIONS:
        raise ValueError(
            f"Too many filter conditions (max {MAX_ROW_FILTER_CONDITIONS})."
        )

    columns_by_name: Dict[str, Any] = {}
    for c in columns_metadata:
        columns_by_name[c["name"]] = c
        columns_by_name[c["name"].lower()] = c

    ops_seen_by_column: "OrderedDict[str, set]" = OrderedDict()

    for raw_cond in config.conditions:
        node = _as_node(raw_cond)
        if isinstance(node, RowFilterNot):
            child = _as_node(node.not_)
            if isinstance(child, RowFilterConfig):
                validate_row_filter_config(child, columns_metadata)
            else:
                validate_row_filter_config(RowFilterConfig(conditions=[child]), columns_metadata)
            continue
        if isinstance(node, RowFilterConfig):
            validate_row_filter_config(node, columns_metadata)
            continue
        cond = node
        if not isinstance(cond, RowFilterCondition):
            raise ValueError("Each row-filter node must be a condition, group, or not node.")
        logger.info(f"Validating condition: column='{cond.column}', operator='{cond.operator}', value='{cond.value}'")
        col = _resolve_column(cond, columns_by_name)

        if not col.get("filterable", True):
            raise ValueError(f"Column '{cond.column}' is not filterable on this SAP entity.")

        if not cond.edm_type:
            cond.edm_type = col.get("edmType") or col.get("edm_type") or col.get("type")
        if col.get("edmType") and cond.edm_type != col.get("edmType"):
            raise ValueError(f"edm_type mismatch for '{cond.column}' -- metadata may be stale.")

        filter_type = FILTER_TYPE_BY_EDM_TYPE.get(cond.edm_type)
        if filter_type is None:
            raise ValueError(f"Column '{cond.column}' (type {cond.edm_type}) is not filterable.")

        logger.info(f"Column '{cond.column}' mapped to filter_type '{filter_type}'. Checking allowed operators...")

        allowed_ops = _allowed_operators_for(col, filter_type)
        if cond.operator not in allowed_ops:
            raise ValueError(
                f"Operator '{cond.operator}' not valid for '{cond.column}' ({filter_type})."
            )

        if cond.value is None and cond.operator not in {"eq", "ne"}:
            raise ValueError(f"Operator '{cond.operator}' requires a value.")

        if cond.operator == "in":
            if not isinstance(cond.value, list) or not cond.value:
                raise ValueError(f"Operator 'in' for '{cond.column}' requires a non-empty list of values.")
            for x in cond.value:
                if x is None or str(x).strip() == "":
                    raise ValueError(f"Operator 'in' for '{cond.column}' cannot contain null or empty values.")

        if filter_type == "numeric":
            for value in (cond.value if cond.operator == "in" else [cond.value]):
                _numeric_literal(value)
        elif filter_type == "boolean":
            _boolean_literal(cond.value)

        ops_seen_by_column.setdefault(cond.column, set()).add(cond.operator)

    logger.info("Row filter configuration successfully validated.")


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------

@log_execution_time
def build_row_filter_expression(config: Optional[RowFilterConfig]) -> Optional[str]:
    """
    Compile a validated RowFilterConfig into an OData $filter expression.

    - Multiple condition buckets for the SAME column (e.g. Feb range OR Mar range) are joined with 'OR'.
    - Conditions across DIFFERENT columns are joined with 'AND' (or config.logic).
    """
    if not config or not config.conditions:
        return None

    def render_leaf_group(nodes: list[Any], logic: str) -> str:
        """Render flat conditions, preserving same-column OR/range behavior."""
        grouped: "OrderedDict[str, list]" = OrderedDict()
        for raw in nodes:
            cond = _as_node(raw)
            if not isinstance(cond, RowFilterCondition):
                raise ValueError("Nested logical nodes must be rendered outside a flat condition group.")
            grouped.setdefault(cond.column, []).append(cond)

        clauses = []
        for column, conds in grouped.items():
            filter_type = FILTER_TYPE_BY_EDM_TYPE.get(conds[0].edm_type)
            if filter_type is None:
                raise ValueError(f"Unsupported or missing EDM type for '{column}'.")
            for cond in conds:
                _validate_renderable_condition(cond, filter_type)

            range_ops = {"ge", "le"}, {"gt", "le"}, {"ge", "lt"}, {"gt", "lt"}
            if len(conds) == 2 and {c.operator for c in conds} in range_ops:
                rendered = []
                for cond in conds:
                    rendered.append(_render_leaf(cond, column, filter_type))
                clauses.append("(" + " and ".join(rendered) + ")")
            else:
                rendered = [_render_leaf(cond, column, filter_type) for cond in conds]
                clauses.append("(" + " or ".join(rendered) + ")" if len(rendered) > 1 else rendered[0])
        return f" {logic} ".join(clauses)

    def _render_leaf(cond: RowFilterCondition, column: str, filter_type: str) -> str:
        if cond.value is None:
            return f"{column} {cond.operator} null"
        if filter_type == "datetime":
            return _render_datetime_clause(column, cond.operator, cond.value)
        op_map = OP_MAP_BY_FILTER_TYPE.get(filter_type)
        if op_map is None or cond.operator not in op_map:
            raise ValueError(f"Operator '{cond.operator}' cannot be rendered for '{filter_type}'.")
        value = cond.value
        if filter_type == "numeric":
            value = [_numeric_literal(x) for x in value] if cond.operator == "in" else _numeric_literal(value)
        elif filter_type == "boolean":
            value = _boolean_literal(value)
        return op_map[cond.operator](column, value)

    def render_node(raw: Any) -> str:
        node = _as_node(raw)
        if isinstance(node, RowFilterNot):
            return f"not ({render_node(node.not_)})"
        if isinstance(node, RowFilterConfig):
            if not node.conditions:
                raise ValueError("A logical group must contain at least one condition.")
            normalized = [_as_node(item) for item in node.conditions]
            if all(isinstance(item, RowFilterCondition) for item in normalized):
                return render_leaf_group(normalized, node.logic)
            return f" {node.logic} ".join(render_node(item) for item in normalized)
        if isinstance(node, RowFilterCondition):
            filter_type = FILTER_TYPE_BY_EDM_TYPE.get(node.edm_type)
            if filter_type is None:
                raise ValueError(f"Unsupported or missing EDM type for '{node.column}'.")
            _validate_renderable_condition(node, filter_type)
            return _render_leaf(node, node.column, filter_type)
        raise ValueError("Each row-filter node must be a condition, group, or not node.")

    normalized_top = [_as_node(item) for item in config.conditions]
    if all(isinstance(item, RowFilterCondition) for item in normalized_top):
        expression = render_leaf_group(normalized_top, config.logic)
    else:
        expression = f" {config.logic} ".join(render_node(item) for item in normalized_top)
    logger.info(f"Compiled filter expression: {expression}")
    return expression
