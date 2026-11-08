"""Compile semantic Excel multiple-condition rules for the final worksheet layout.

The UI stores conditions using database column names and parenthesis markers.
This module deliberately resolves those names only when the Excel writer knows
the final column order and first data row.  That keeps formulas correct when
fields are selected/reordered or a sheet title/header occupies extra rows.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence


class ExcelConditionFormulaError(ValueError):
    """Raised when a semantic multiple-condition rule cannot be compiled."""


_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _excel_column_letter(index: int) -> str:
    """Return the 1-based Excel column letter without importing a writer library."""
    if index < 1:
        raise ExcelConditionFormulaError("Excel column indexes must be positive")
    letters = []
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(65 + remainder))
    return "".join(reversed(letters))


def _format_value(value: Any) -> str:
    """Format a condition value as a safe Excel formula constant."""
    text = "" if value is None else str(value).strip()
    if text.startswith("="):
        text = text[1:].strip()
    if text.upper() in {"TRUE", "FALSE"}:
        return text.upper()
    if _NUMERIC_RE.fullmatch(text):
        # Reject non-finite numeric literals even though Excel would not use
        # them as valid constants.
        try:
            if math.isfinite(float(text)):
                return text
        except ValueError:
            pass
    return '"' + text.replace('"', '""') + '"'


def _cell_reference(column: Any, column_index_map: Mapping[str, int], row: int) -> str:
    name = str(column or "").strip().upper()
    if name not in column_index_map:
        raise ExcelConditionFormulaError(
            f"Excel condition column not found in exported columns: {name}"
        )
    return f"${_excel_column_letter(int(column_index_map[name]))}{row}"


def _condition_formula(condition: Mapping[str, Any], column_index_map: Mapping[str, int], row: int) -> str:
    cell = _cell_reference(condition.get("column"), column_index_map, row)
    operator = str(condition.get("operator") or "equal").strip()
    value = _format_value(condition.get("value", ""))

    comparisons = {
        "equal": "=",
        "notEqual": "<>",
        "greaterThan": ">",
        "lessThan": "<",
        "greaterThanOrEqual": ">=",
        "lessThanOrEqual": "<=",
    }
    if operator in comparisons:
        formula = f"{cell}{comparisons[operator]}{value}"
    elif operator in {"contains", "notContains"}:
        search = f"ISNUMBER(SEARCH({value},{cell}))"
        formula = search if operator == "contains" else f"ISERROR(SEARCH({value},{cell}))"
    elif operator in {"beginsWith", "notBeginsWith"}:
        expression = f"LEFT({cell},LEN({value}))={value}"
        formula = expression if operator == "beginsWith" else f"LEFT({cell},LEN({value}))<>{value}"
    elif operator in {"endsWith", "notEndsWith"}:
        expression = f"RIGHT({cell},LEN({value}))={value}"
        formula = expression if operator == "endsWith" else f"RIGHT({cell},LEN({value}))<>{value}"
    elif operator == "isEmpty":
        formula = f"ISBLANK({cell})"
    elif operator == "isNotEmpty":
        formula = f"NOT(ISBLANK({cell}))"
    elif operator in {"between", "notBetween"}:
        second = _format_value(condition.get("value2", ""))
        if operator == "between":
            formula = f"AND({cell}>={value},{cell}<={second})"
        else:
            formula = f"OR({cell}<{value},{cell}>{second})"
    else:
        raise ExcelConditionFormulaError(f"Unsupported Excel condition operator: {operator}")

    logic = str(condition.get("logic") or "and").strip().lower()
    if logic not in {"and", "or"}:
        raise ExcelConditionFormulaError(f"Unsupported Excel condition logic: {logic}")
    return formula


def _parenthesis_count(value: Any, marker: str) -> int:
    text = str(value or "")
    if any(character not in marker for character in text):
        raise ExcelConditionFormulaError(
            f"Condition parenthesis markers may contain only {marker!r}"
        )
    return len(text)


def compile_multiple_conditions_formula(
    conditions: Sequence[Mapping[str, Any]],
    column_names: Sequence[Any],
    first_data_row: int,
) -> str:
    """Compile conditions into a formula without a leading ``=``.

    Parentheses are emitted exactly around the flat condition sequence.  This
    matches the frontend's serialized tree format and preserves nested groups
    without constructing a large intermediate AST for every exported row.
    """
    if not conditions:
        raise ExcelConditionFormulaError("Multiple conditions must not be empty")
    if int(first_data_row) < 1:
        raise ExcelConditionFormulaError("The first data row must be positive")

    column_index_map = {
        str(name).strip().upper(): index
        for index, name in enumerate(column_names, start=1)
    }
    tokens = []
    depth = 0
    for index, condition in enumerate(conditions):
        if not isinstance(condition, Mapping):
            raise ExcelConditionFormulaError(f"Condition {index} must be an object")
        if index:
            logic = str(condition.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                raise ExcelConditionFormulaError(f"Unsupported Excel condition logic: {logic}")
            tokens.append(logic.upper())

        opening = _parenthesis_count(condition.get("open_paren"), "(")
        closing = _parenthesis_count(condition.get("close_paren"), ")")
        depth += opening
        depth -= closing
        if depth < 0:
            raise ExcelConditionFormulaError("Excel condition parentheses are unbalanced")
        # Closing markers belong after the condition, not before it.  The
        # depth check above still catches a close marker that has no opener.
        tokens.extend("(" for _ in range(opening))
        tokens.append(
            _condition_formula(condition, column_index_map, int(first_data_row))
        )
        tokens.extend(")" for _ in range(closing))

    if depth:
        raise ExcelConditionFormulaError("Excel condition parentheses are unbalanced")
    position = 0

    def parse_primary() -> str:
        nonlocal position
        if position >= len(tokens):
            raise ExcelConditionFormulaError("Excel condition expression is incomplete")
        token = tokens[position]
        if token == "(":
            position += 1
            result = parse_or()
            if position >= len(tokens) or tokens[position] != ")":
                raise ExcelConditionFormulaError("Excel condition parentheses are unbalanced")
            position += 1
            return result
        if token in {"AND", "OR", ")"}:
            raise ExcelConditionFormulaError("Excel condition expression is malformed")
        position += 1
        return token

    def parse_and() -> str:
        nonlocal position
        result = parse_primary()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            result = f"AND({result},{parse_primary()})"
        return result

    def parse_or() -> str:
        nonlocal position
        result = parse_and()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            result = f"OR({result},{parse_and()})"
        return result

    result = parse_or()
    if position != len(tokens):
        raise ExcelConditionFormulaError("Excel condition expression is malformed")
    return result

