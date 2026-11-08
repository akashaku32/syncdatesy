import csv
import faulthandler
import json
import logging
import os
import re
import time as time_module
from datetime import date, datetime, time as datetime_time, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from services.source_file_helpers.excel_condition_formula import (
    ExcelConditionFormulaError,
    compile_multiple_conditions_formula,
)

# ── NEW at top of file ────────────────────────────────────────────────────────

def _build_column_format_cache(
    col_names: List[str],
    column_formats_by_index: List[Optional[str]],
    default_date_fmt: str,
    default_dt_fmt: str,
    default_time_fmt: str,
) -> List[Optional[str]]:
    """
    Resolve number formats for every column using the first non-null value
    seen in the sample. Returns a list indexed by column position.
    This is called once after the first chunk, never per-row.
    """
    # Start with explicit overrides; None means "sniff from data".
    return list(column_formats_by_index)  # filled in during first-chunk sniff


def _sniff_column_formats(
    sample_row: tuple,
    explicit_formats: List[Optional[str]],
    default_date_fmt: str,
    default_dt_fmt: str,
    default_time_fmt: str,
) -> List[Optional[str]]:
    """One-time pass over first data row to lock in per-column formats."""
    result = []
    for idx, value in enumerate(sample_row):
        if idx < len(explicit_formats) and explicit_formats[idx]:
            result.append(explicit_formats[idx])
            continue
        if isinstance(value, datetime):
            result.append(default_dt_fmt)
        elif isinstance(value, date):
            result.append(default_date_fmt)
        elif isinstance(value, datetime_time):
            result.append(default_time_fmt)
        else:
            result.append(None)
    return result


logger = logging.getLogger(__name__)


FILENAME_DATE_FORMATS = {
    "yyyy_mm_dd": "%Y_%m_%d",
    "dd_mm_yyyy": "%d_%m_%Y",
    "mm_dd_yyyy": "%m_%d_%Y",
}
DEFAULT_UTC_FILENAME_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_LEGACY_UTC_FILENAME_SUFFIX_RE = re.compile(r"_\d{8}T\d{6}Z$", re.IGNORECASE)
_YYYY_MM_DD_FILENAME_SUFFIX_RE = re.compile(r"_\d{4}_\d{2}_\d{2}$")
_DD_MM_YYYY_FILENAME_SUFFIX_RE = re.compile(r"_\d{2}_\d{2}_\d{4}$")


def strip_export_filename_suffix(stem: str) -> str:
    """Remove Snowflake export timestamp/date suffixes from a filename stem."""
    value = str(stem or "").strip()
    if not value:
        return value
    for pattern in (
        _LEGACY_UTC_FILENAME_SUFFIX_RE,
        _YYYY_MM_DD_FILENAME_SUFFIX_RE,
        _DD_MM_YYYY_FILENAME_SUFFIX_RE,
    ):
        candidate = pattern.sub("", value)
        if candidate != value:
            return candidate
    return value


def lookup_table_export_settings(
    table_export_settings: Optional[dict],
    table_name: str,
) -> dict:
    """Resolve per-table export settings using case-insensitive table-name matching."""
    settings = table_export_settings or {}
    if not isinstance(settings, dict):
        return {}
    if table_name in settings and isinstance(settings[table_name], dict):
        return settings[table_name]
    target = str(table_name or "").strip()
    if not target:
        return {}
    target_lower = target.lower()
    for key, value in settings.items():
        if str(key).strip().lower() == target_lower and isinstance(value, dict):
            return value
    return {}


def resolve_relative_target_folder(base_folder: Any, target_folder: Any) -> str:
    """Return the table folder relative to the configured destination folder.

    The UI may display and submit the destination folder as the default table
    target.  In that case it represents the already-selected root, not a child
    folder with the same name.
    """
    normalized_base = str(base_folder or "").strip().replace("\\", "/").strip("/")
    normalized_target = str(target_folder or "").strip().replace("\\", "/").strip("/")

    if normalized_target == normalized_base:
        return ""
    return normalized_target


def build_utc_filename_suffix(
    filename_date_format: Optional[str] = None,
    current_time: Optional[datetime] = None,
) -> str:
    """Build the UTC suffix used by Snowflake file destinations.

    A missing format preserves the legacy timestamp suffix. When a supported
    date format is selected, only that formatted UTC date is shown in the
    filename; migration timestamps stored in the database are unaffected.
    """
    utc_time = current_time or datetime.now(timezone.utc)
    if utc_time.tzinfo is None or utc_time.utcoffset() is None:
        utc_time = utc_time.replace(tzinfo=timezone.utc)
    else:
        utc_time = utc_time.astimezone(timezone.utc)

    selected_format = str(filename_date_format or "").strip().lower()
    if not selected_format:
        return utc_time.strftime(DEFAULT_UTC_FILENAME_TIMESTAMP_FORMAT)

    strftime_format = FILENAME_DATE_FORMATS.get(selected_format)
    if strftime_format is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid filename_date_format. Expected one of: "
                + ", ".join(sorted(FILENAME_DATE_FORMATS))
            ),
        )
    return utc_time.strftime(strftime_format)

# Canonical lowercase rule type tokens shared by request validation and Excel writing.
EXCEL_RULE_TYPE_ABOVE_AVERAGE = "aboveaverage"
EXCEL_RULE_TYPE_BEGINS_WITH = "beginswith"
EXCEL_RULE_TYPE_CELL_IS = "cellis"
EXCEL_RULE_TYPE_CONTAINS_TEXT = "containstext"
EXCEL_RULE_TYPE_COLOR_SCALE = "colorscale"
EXCEL_RULE_TYPE_DATA_BAR = "databar"
EXCEL_RULE_TYPE_DUPLICATE_RECORDS = "duplicaterecords"
EXCEL_RULE_TYPE_ENDS_WITH = "endswith"
EXCEL_RULE_TYPE_EXPRESSION = "expression"
EXCEL_RULE_TYPE_FORMULA = "formula"
EXCEL_RULE_TYPE_ICON_SET = "iconset"
EXCEL_RULE_TYPE_NOT_CONTAINS_TEXT = "notcontainstext"
EXCEL_RULE_TYPE_TIME_PERIOD = "timeperiod"
EXCEL_RULE_TYPE_TOP_10 = "top10"
EXCEL_RULE_TYPE_UNIQUE_RECORDS = "uniquerecords"
EXCEL_RULE_TYPE_MULTIPLE_CONDITIONS = "multipleconditions"

EXCEL_TEXT_RULE_OPERATOR_MAP = {
    EXCEL_RULE_TYPE_CONTAINS_TEXT: "containsText",
    EXCEL_RULE_TYPE_NOT_CONTAINS_TEXT: "notContains",
    EXCEL_RULE_TYPE_BEGINS_WITH: "beginsWith",
    EXCEL_RULE_TYPE_ENDS_WITH: "endsWith",
}


def _excel_option_enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _get_cursor_description_value(desc: Any, index: int, *attr_names: str) -> Any:
    logger.info("Entering _get_cursor_description_value")
    for attr_name in attr_names:
        value = getattr(desc, attr_name, None)
        if value is not None:
            return value
    if isinstance(desc, (list, tuple)) and len(desc) > index:
        return desc[index]
    return None


def _build_parquet_schema(pa, description: List[Any], sample_rows: List[Tuple[Any, ...]]) -> Any:
    logger.info("Entering _build_parquet_schema")
    col_names = [d[0] for d in (description or [])]
    columns = list(zip(*sample_rows)) if sample_rows else [[] for _ in col_names]
    fields = []

    for idx, name in enumerate(col_names):
        col_vals = list(columns[idx]) if columns else []
        inferred_type = pa.array(col_vals).type
        precision = _get_cursor_description_value(description[idx], 4, "precision")
        scale = _get_cursor_description_value(description[idx], 5, "scale")
        has_decimal_values = any(isinstance(val, Decimal) for val in col_vals if val is not None)
        should_use_decimal_schema = pa.types.is_decimal(inferred_type) or has_decimal_values

        if precision is None and has_decimal_values:
            inferred_scale = getattr(inferred_type, "scale", 0) if pa.types.is_decimal(inferred_type) else 0
            precision = 38
            scale = inferred_scale

        if should_use_decimal_schema and precision is not None and scale is not None:
            normalized_precision = max(1, min(int(precision), 38))
            normalized_scale = max(0, min(int(scale), normalized_precision))
            field_type = pa.decimal128(normalized_precision, normalized_scale)
        else:
            field_type = inferred_type

        fields.append(pa.field(name, field_type))

    return pa.schema(fields)


def _iter_rows_in_chunks(cursor, fetch_size: int) -> Iterable[List[Tuple[Any, ...]]]:
    logger.info("Entering _iter_rows_in_chunks | fetch_size=%s", fetch_size)
    chunk_index = 0
    try:
        while True:
            fetch_started_at = time_module.perf_counter()
            logger.info(
                "_iter_rows_in_chunks fetching next chunk | chunk_index=%s fetch_size=%s",
                chunk_index + 1,
                fetch_size,
            )
            rows = cursor.fetchmany(fetch_size)
            logger.info(
                "_iter_rows_in_chunks fetch completed | chunk_index=%s rows_in_chunk=%s elapsed_seconds=%.3f",
                chunk_index + 1,
                len(rows) if rows else 0,
                time_module.perf_counter() - fetch_started_at,
            )
            if not rows:
                break
            chunk_index += 1
            logger.info(
                "_iter_rows_in_chunks yielded chunk | chunk_index=%s rows_in_chunk=%s",
                chunk_index,
                len(rows),
            )
            yield rows
    finally:
        logger.info("Exiting _iter_rows_in_chunks | chunks=%s", chunk_index)


def _decode_csv_char(val: Any) -> str:
    logger.info("Entering _decode_csv_char")
    s = str(val or "")
    if s in ("\\t", "\\\\t"):
        return "\t"
    if s in ("\\r", "\\\\r"):
        return "\r"
    if s in ("\\n", "\\\\n"):
        return "\n"
    if s in ("\\s", "\\\\s"):
        return " "
    return s


def _excel_safe_sheet_name(name: Optional[str]) -> str:
    cleaned = str(name or "").strip() or "Sheet1"
    invalid_chars = '\\/*?:[]'
    for char in invalid_chars:
        cleaned = cleaned.replace(char, "_")
    return cleaned[:31] or "Sheet1"


def _excel_color(value: Optional[str], default: Optional[str] = None) -> Optional[str]:
    """
    Normalize a CSS-style hex color to an 8-char ARGB string (e.g. 'FF1F4E78').
    Returns None when no valid color can be resolved, which tells openpyxl
    to leave the property unset rather than writing a corrupt color attribute.
    """
    raw = str(value or default or "").strip().lstrip("#")
    if not raw or raw.upper() in ("NONE", "TRANSPARENT", ""):
        return None
    # Strip alpha prefix if the caller accidentally passed a full ARGB already
    if len(raw) == 8 and all(c in "0123456789ABCDEFabcdef" for c in raw):
        return raw.upper()
    if len(raw) == 6 and all(c in "0123456789ABCDEFabcdef" for c in raw):
        return f"FF{raw.upper()}"
    if len(raw) == 3 and all(c in "0123456789ABCDEFabcdef" for c in raw):
        # Expand CSS shorthand: #abc → #aabbcc
        expanded = "".join(c * 2 for c in raw)
        return f"FF{expanded.upper()}"
    logger.info("_excel_color received unrecognised color value | raw=%r default=%r", value, default)
    return None

def _excel_pattern_fill(style_dict: dict) -> Optional[Any]:
    """
    Build a solid PatternFill from a style dict.
    Keys checked (first non-None wins): fill, fill_color, background_color.
    Returns None when no valid color is available so callers can skip assignment.
    """
    try:
        from openpyxl.styles import PatternFill
    except ImportError:
        logger.error("openpyxl is not installed; PatternFill unavailable")
        return None

    fill_color = _excel_color(
        style_dict.get("fill")
        or style_dict.get("fill_color")
        or style_dict.get("background_color")
    )
    if not fill_color:
        return None

    logger.info("_excel_pattern_fill | argb=%s", fill_color)
    # Setting both fgColor and bgColor is required for conditional-format DXFs
    # to render reliably across Excel versions (fgColor alone is ignored by
    # some readers when the fill_type is 'solid').
    return PatternFill(fill_type="solid", fgColor=fill_color, bgColor=fill_color)


def _excel_font(
    style_dict: dict,
    *,
    default_name: Optional[str] = None,
    default_size: Optional[Any] = None,
    default_bold: Optional[bool] = None,
    default_italic: Optional[bool] = None,
    default_color: Optional[str] = None,
) -> Optional[Any]:
    """
    Build an openpyxl Font from a style dict plus caller-supplied defaults.
    Returns None when no attributes would be set, avoiding a no-op Font object.
    """
    try:
        from openpyxl.styles import Font
    except ImportError:
        return None

    kwargs: dict = {}

    color = _excel_color(style_dict.get("font_color") or style_dict.get("color"), default_color)
    if color:
        kwargs["color"] = color

    bold_raw = style_dict.get("bold")
    if bold_raw is not None:
        kwargs["bold"] = bool(bold_raw)
    elif default_bold is not None:
        kwargs["bold"] = bool(default_bold)

    italic_raw = style_dict.get("italic")
    if italic_raw is not None:
        kwargs["italic"] = bool(italic_raw)
    elif default_italic is not None:
        kwargs["italic"] = bool(default_italic)

    name = style_dict.get("font_name") or default_name
    if name:
        kwargs["name"] = str(name)

    size_raw = style_dict.get("font_size") if "font_size" in style_dict else default_size
    if size_raw is not None:
        try:
            kwargs["size"] = float(size_raw)
        except (TypeError, ValueError):
            logger.info("_excel_font ignoring non-numeric font_size | value=%r", size_raw)

    underline = style_dict.get("underline")
    if underline:
        kwargs["underline"] = str(underline)  # "single", "double", etc.

    strike = style_dict.get("strikethrough")
    if strike is not None:
        kwargs["strike"] = bool(strike)

    if not kwargs:
        return None

    return Font(**kwargs)

_VALID_HORIZONTAL = {"general", "left", "center", "right", "fill", "justify", "centerContinuous", "distributed"}
_VALID_VERTICAL   = {"top", "center", "bottom", "justify", "distributed"}

def _excel_alignment(
    style_dict: dict,
    *,
    default_horizontal: Optional[str] = None,
    default_wrap_text: Optional[bool] = None,
) -> Optional[Any]:
    try:
        from openpyxl.styles import Alignment
    except ImportError:
        return None

    kwargs: dict = {}

    h = str(style_dict.get("horizontal") or default_horizontal or "").strip()
    if h:
        if h not in _VALID_HORIZONTAL:
            logger.info("_excel_alignment ignoring invalid horizontal=%r", h)
        else:
            kwargs["horizontal"] = h

    v = str(style_dict.get("vertical") or "").strip()
    if v:
        if v not in _VALID_VERTICAL:
            logger.info("_excel_alignment ignoring invalid vertical=%r", v)
        else:
            kwargs["vertical"] = v

    wrap_raw = style_dict.get("wrap_text")
    if wrap_raw is not None:
        kwargs["wrap_text"] = bool(wrap_raw)
    elif default_wrap_text is not None:
        kwargs["wrap_text"] = bool(default_wrap_text)

    indent = style_dict.get("indent")
    if indent is not None:
        try:
            kwargs["indent"] = int(indent)
        except (TypeError, ValueError):
            pass

    shrink = style_dict.get("shrink_to_fit")
    if shrink is not None:
        kwargs["shrinkToFit"] = bool(shrink)

    if not kwargs:
        return None

    return Alignment(**kwargs)

def _excel_differential_style(style_dict: Optional[dict]) -> Optional[Any]:
    """
    Build a DifferentialStyle (DXF) used by conditional-formatting rules.
    Returns None when the dict contains nothing actionable, so the Rule is
    written without a DXF rather than with an empty/broken one.
    """
    if not isinstance(style_dict, dict) or not style_dict:
        return None
    try:
        from openpyxl.styles.differential import DifferentialStyle
    except ImportError:
        logger.error("openpyxl DifferentialStyle unavailable; conditional format styling skipped")
        return None

    try:
        fill      = _excel_pattern_fill(style_dict)
        font      = _excel_font(style_dict)
        alignment = _excel_alignment(style_dict)

        if not any((fill, font, alignment)):
            logger.info("_excel_differential_style: no components resolved | style=%s", style_dict)
            return None

        logger.info(
            "_excel_differential_style built | fill=%s font=%s alignment=%s",
            bool(fill), bool(font), bool(alignment),
        )
        return DifferentialStyle(fill=fill, font=font, alignment=alignment)
    except Exception:
        logger.exception("_excel_differential_style failed | style=%s", style_dict)
        return None

def _apply_style_to_row(
    ws: Any,
    row_idx: int,
    style_dict: Optional[dict],
    *,
    default_fill_color: Optional[str] = None,
    default_font_name: Optional[str] = None,
    default_font_size: Optional[Any] = None,
    default_font_color: Optional[str] = None,
    default_bold: Optional[bool] = None,
    default_italic: Optional[bool] = None,
    default_horizontal: Optional[str] = None,
    default_wrap_text: Optional[bool] = None,
) -> None:
    """Apply fill/font/alignment to every cell in *row_idx* of *ws*."""
    style_dict = dict(style_dict or {})

    # Merge default fill into the dict so _excel_pattern_fill sees it
    if not (style_dict.get("fill") or style_dict.get("fill_color") or style_dict.get("background_color")):
        style_dict["fill"] = default_fill_color

    fill      = _excel_pattern_fill(style_dict)
    font      = _excel_font(
        style_dict,
        default_name=default_font_name,
        default_size=default_font_size,
        default_bold=default_bold,
        default_italic=default_italic,
        default_color=default_font_color,
    )
    alignment = _excel_alignment(
        style_dict,
        default_horizontal=default_horizontal,
        default_wrap_text=default_wrap_text,
    )

    if fill is None and font is None and alignment is None:
        return  # nothing to apply — avoid iterating cells needlessly

    try:
        cells = ws[row_idx]
    except (TypeError, KeyError):
        # write-only worksheets don't support row indexing after append
        logger.info("_apply_style_to_row: skipping non-readable row %s", row_idx)
        return

    for cell in cells:
        if fill is not None:
            cell.fill = fill
        if font is not None:
            cell.font = font
        if alignment is not None:
            cell.alignment = alignment

def _excel_display_value(value: Any) -> Any:
    """
    Convert a Python value to something openpyxl can write into a cell without error.

    Rules:
    - Decimal  → float (openpyxl has no Decimal writer)
    - Decimal NaN/Inf → None  (Excel has no equivalent; write blank)
    - timezone-aware datetime → UTC naive datetime
    - Everything else passes through unchanged
    """
    if isinstance(value, Decimal):
        try:
            f = float(value)
        except (OverflowError, ValueError):
            return None  # Inf / NaN → blank cell
        import math
        return None if (math.isnan(f) or math.isinf(f)) else f

    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    return value

def _excel_is_timezone_aware_datetime(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _excel_should_trace_row(row_number: int, chunk_row_count: int) -> bool:
    return row_number <= 5 or row_number % 1000 == 0 or row_number == chunk_row_count


def _excel_process_memory_mb() -> Optional[float]:
    try:
        import psutil  # type: ignore

        return float(psutil.Process(os.getpid()).memory_info().rss) / (1024.0 * 1024.0)
    except Exception:
        pass

    if os.name != "nt":
        return None

    try:
        import ctypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        process_handle = ctypes.windll.kernel32.GetCurrentProcess()
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(process_handle, ctypes.byref(counters), counters.cb):
            return None
        return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
    except Exception:
        logger.info("Excel memory snapshot unavailable", exc_info=True)
        return None


def _excel_log_memory(label: str, *, prefix: str) -> None:
    memory_mb = _excel_process_memory_mb()
    if memory_mb is None:
        logger.info("%s | label=%s memory_mb=unavailable pid=%s", prefix, label, os.getpid())
        return
    logger.info("%s | label=%s memory_mb=%.2f pid=%s", prefix, label, memory_mb, os.getpid())


def _estimate_excel_text_width(value: Any, *, is_header: bool = False) -> float:
    text = "" if value is None else str(value)
    if not text:
        return 0.0

    width = 0.0
    for char in text:
        if char in {"W", "M", "@", "#", "%", "&"}:
            width += 1.45
        elif char in {"_", "-", "/", "\\", ":"}:
            width += 1.2
        elif char == " ":
            width += 0.65
        elif char.isupper():
            width += 1.15
        elif char.isdigit():
            width += 1.0
        else:
            width += 0.95

    if is_header:
        width += 3.5
    else:
        width += 1.75

    return min(width, 80.0)


def _excel_first_cell(cell_range: str) -> str:
    return str(cell_range).split(":", 1)[0]


def _excel_column_index_map(col_names: List[str]) -> dict:
    return {str(name).strip().upper(): idx + 1 for idx, name in enumerate(col_names)}


def _resolve_excel_rule_range(rule: dict, column_index_map: dict, first_data_row: int, last_data_row: int) -> str:
    """Resolves the Excel range to apply the conditional format rule to.

    Args:
        rule: The conditional formatting rule dictionary.
        column_index_map: Mapping of column names (uppercase) to 1-based index.
        first_data_row: 1-based index of the first row of data.
        last_data_row: 1-based index of the last row of data.

    Returns:
        A string representing the Excel range (e.g. "A2:C100" or "C2:C100").
    """
    target_range = str(rule.get("range") or "").strip()
    if target_range:
        logger.info("Excel conditional formatting using explicit range | range=%s rule_type=%s", target_range, rule.get("type"))
        return target_range

    column_name = str(rule.get("column_name") or "").strip().upper()
    if not column_name:
        raise HTTPException(status_code=422, detail="Excel conditional formatting rule requires range or column_name")
    if column_name not in column_index_map:
        raise HTTPException(status_code=422, detail=f"Excel conditional formatting column not found: {column_name}")

    from openpyxl.utils import get_column_letter

    highlight_scope = str(rule.get("highlight_scope") or "cell").strip().lower()
    if highlight_scope == "entire_row":
        max_col_idx = max(column_index_map.values())
        last_col_letter = get_column_letter(max_col_idx)
        resolved_range = f"A{first_data_row}:{last_col_letter}{last_data_row}"
    elif highlight_scope in {"cell", "entire_column", "selected_columns"}:
        col_letter = get_column_letter(column_index_map[column_name])
        resolved_range = f"{col_letter}{first_data_row}:{col_letter}{last_data_row}"
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported Excel highlight_scope: {highlight_scope}")

    logger.info(
        "Excel conditional formatting resolved range | column_name=%s highlight_scope=%s first_data_row=%s last_data_row=%s resolved_range=%s",
        column_name,
        highlight_scope,
        first_data_row,
        last_data_row,
        resolved_range,
    )
    return resolved_range


def _compiled_multiple_conditions_formula(
    rule: dict,
    col_names: List[str],
    first_data_row: int,
) -> Optional[str]:
    """Compile semantic conditions against the writer's final sheet layout.

    Older saved rules may contain only ``formula``.  In that case this helper
    returns ``None`` and the existing formula remains the compatibility path.
    """
    conditions = rule.get("conditions")
    if not conditions:
        return None
    try:
        return compile_multiple_conditions_formula(
            conditions, col_names, first_data_row
        )
    except ExcelConditionFormulaError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid multipleconditions rule: {exc}",
        ) from exc


def _build_entire_row_formula(rule: dict, col_letter: str, row_num: int) -> Optional[str]:
    """Translates a conditional formatting rule to a row-level Excel formula.

    This function maps cell-specific or text-based criteria into formula
    expressions that use absolute column references (e.g., $B2) so that
    Excel evaluates the condition based on the key column for all cells in the row.

    Args:
        rule: The conditional formatting rule dictionary.
        col_letter: Letter of the key column containing the condition value.
        row_num: 1-based index of the target row to evaluate.

    Returns:
        The generated Excel formula string without a leading '=', or None if invalid.
    """
    rule_type = str(rule.get("type") or "").strip().lower()
    cell_ref = f"${col_letter}{row_num}"

    def _fmt_val(val: Any) -> str:
        """Helper to format python values as Excel formula constants."""
        val_str = str(val).strip()
        # Strip a leading '=' so callers can pass "=TRUE" or "=1.5" naturally
        if val_str.startswith("="):
            val_str = val_str[1:].strip()
        try:
            # If numeric, keep as raw unquoted digits
            float(val_str)
            return val_str
        except ValueError:
            pass
        if val_str.lower() in ("true", "false"):
            return val_str.upper()
        # Escaping quotes for Excel formula string format
        escaped_val = val_str.replace('"', '""')
        return f'"{escaped_val}"'

    if rule_type == "cellis":
        operator = str(rule.get("operator") or "equal").strip().lower().replace(" ", "")
        formula_vals = rule.get("formula") or []
        if isinstance(formula_vals, str):
            formula_vals = [formula_vals]
        # Clean any empty strings from list
        formula_vals = [str(f) for f in formula_vals if str(f).strip()]
        if not formula_vals:
            logger.warning("_build_entire_row_formula: cellis rule missing formula values")
            return None
        val0 = _fmt_val(formula_vals[0])

        if operator in ("equal", "equalto"):
            return f"{cell_ref}={val0}"
        elif operator in ("notequal", "notequalto"):
            return f"{cell_ref}<>{val0}"
        elif operator in ("greaterthan", "greaterthan"):
            return f"{cell_ref}>{val0}"
        elif operator in ("lessthan", "lessthan"):
            return f"{cell_ref}<{val0}"
        elif operator in ("greaterthanorequal", "greaterthanorequalto"):
            return f"{cell_ref}>={val0}"
        elif operator in ("lessthanorequal", "lessthanorequalto"):
            return f"{cell_ref}<={val0}"
        elif operator == "between":
            if len(formula_vals) < 2:
                logger.warning("_build_entire_row_formula: between operator requires 2 values")
                return None
            return f"AND({cell_ref}>={val0},{cell_ref}<={_fmt_val(formula_vals[1])})"
        elif operator == "notbetween":
            if len(formula_vals) < 2:
                logger.warning("_build_entire_row_formula: notbetween operator requires 2 values")
                return None
            return f"OR({cell_ref}<{val0},{cell_ref}>{_fmt_val(formula_vals[1])})"
        else:
            logger.warning("_build_entire_row_formula: unsupported operator %s, defaulting to equal", operator)
            return f"{cell_ref}={val0}"

    elif rule_type == "containstext":
        text = str(rule.get("text") or "")
        if not text:
            return None
        return _build_contains_text_formula(cell_ref, text)

    elif rule_type == "notcontainstext":
        text = str(rule.get("text") or "")
        if not text:
            return None
        return f"NOT({_build_contains_text_formula(cell_ref, text)})"

    elif rule_type == "beginswith":
        text = str(rule.get("text") or "")
        if not text:
            return None
        return _build_begins_with_formula(cell_ref, text)

    elif rule_type == "endswith":
        text = str(rule.get("text") or "")
        if not text:
            return None
        return _build_ends_with_formula(cell_ref, text)

    elif rule_type in ("formula", "expression"):
        formula_vals = rule.get("formula") or []
        if isinstance(formula_vals, str):
            formula_vals = [formula_vals]
        if formula_vals:
            formula_str = str(formula_vals[0]).strip()
            # Excel formula rule expressions written directly should have '=' removed if needed,
            # though usually it doesn't matter. Let's strip the leading '=' if present.
            if formula_str.startswith("="):
                formula_str = formula_str[1:]
            return formula_str

    logger.warning("_build_entire_row_formula: unsupported rule type for entire row: %s", rule_type)
    return None


def _build_contains_text_formula(cell_ref: str, text_value: str) -> str:
    escaped_text = str(text_value).replace('"', '""')
    return f'NOT(ISERROR(SEARCH("{escaped_text}",{cell_ref})))'


def _build_begins_with_formula(cell_ref: str, text_value: str) -> str:
    escaped_text = str(text_value).replace('"', '""')
    return f'LEFT({cell_ref},{len(str(text_value))})="{escaped_text}"'


def _build_ends_with_formula(cell_ref: str, text_value: str) -> str:
    escaped_text = str(text_value).replace('"', '""')
    return f'RIGHT({cell_ref},{len(str(text_value))})="{escaped_text}"'

def _apply_excel_conditional_formatting(
    ws: Any,
    rules: List[dict],
    col_names: List[str],
    first_data_row: int,
    last_data_row: int,
) -> None:
    """
    Apply a list of conditional-formatting rule dicts to *ws*.
    Each rule must have 'type' and either 'range' or 'column_name'.
    Invalid rules raise HTTPException(422); internal openpyxl errors are
    logged and skipped so the rest of the workbook is not lost.
    """
    if not rules or last_data_row < first_data_row:
        return

    from openpyxl.formatting.rule import (
        ColorScaleRule,
        DataBarRule,
        IconSetRule,
        Rule,
    )

    column_index_map = _excel_column_index_map(col_names)
    logger.info(
        "_apply_excel_conditional_formatting | sheet=%s rules=%s rows=%s-%s",
        getattr(ws, "title", None), len(rules), first_data_row, last_data_row,
    )

    expanded_rules = []
    for raw_rule in rules:
        targets = (raw_rule or {}).get("target_columns") if isinstance(raw_rule, dict) else None
        if isinstance(targets, list) and targets:
            for target in targets:
                expanded_rule = dict(raw_rule)
                expanded_rule["column_name"] = target
                expanded_rule.pop("target_columns", None)
                expanded_rules.append(expanded_rule)
        else:
            expanded_rules.append(raw_rule)

    for index, raw_rule in enumerate(expanded_rules):
        rule = dict(raw_rule or {})
        rule_type = str(rule.get("type") or "").strip().lower()

        try:
            target_range = _resolve_excel_rule_range(
                rule, column_index_map, first_data_row, last_data_row
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Excel conditional format rule[{index}]: cannot resolve range — {exc}",
            ) from exc

        formula = rule.get("formula") or []
        if isinstance(formula, str):
            formula = [formula]
        formula = [str(f) for f in formula if str(f).strip()]  # drop blank entries
        if rule_type == EXCEL_RULE_TYPE_MULTIPLE_CONDITIONS:
            compiled_formula = _compiled_multiple_conditions_formula(
                rule, col_names, first_data_row
            )
            if compiled_formula:
                # Semantic conditions are authoritative.  A frontend formula
                # may contain stale column letters or a hard-coded row number.
                formula = [compiled_formula]

        dxf = _excel_differential_style(rule.get("style"))

        logger.info(
            "CF rule[%s] | type=%s range=%s operator=%s has_dxf=%s",
            index, rule_type, target_range, rule.get("operator"), bool(dxf),
        )

        try:
            highlight_scope = str(rule.get("highlight_scope") or "cell").strip().lower()
            condition_column = str(rule.get("condition_column_name") or rule.get("column_name") or "").strip().upper()
            if condition_column and condition_column in column_index_map and rule_type not in (EXCEL_RULE_TYPE_COLOR_SCALE, EXCEL_RULE_TYPE_DATA_BAR, EXCEL_RULE_TYPE_ICON_SET) and (highlight_scope in {"entire_row", "entire_column"} or rule.get("condition_column_name")):
                column_name = condition_column
                if column_name in column_index_map:
                    from openpyxl.utils import get_column_letter
                    col_letter = get_column_letter(column_index_map[column_name])
                    formula_str = _build_entire_row_formula(rule, col_letter, first_data_row)
                    if formula_str:
                        logger.info(
                            "CF rule[%s] applying formula rule in openpyxl | condition_column=%s range=%s formula=%s",
                            index, column_name, target_range, formula_str
                        )
                        ws.conditional_formatting.add(
                            target_range,
                            Rule(type="expression", formula=[formula_str], stopIfTrue=bool(rule.get("stop_if_true", False)), dxf=dxf),
                        )
                        continue

            # ── formula / expression ──────────────────────────────────────
            if rule_type in {EXCEL_RULE_TYPE_FORMULA, EXCEL_RULE_TYPE_EXPRESSION, EXCEL_RULE_TYPE_MULTIPLE_CONDITIONS}:
                if not formula:
                    raise HTTPException(status_code=422, detail=f"CF rule[{index}] {rule_type} requires formula")
                ws.conditional_formatting.add(
                    target_range,
                    Rule(type="expression", formula=formula, stopIfTrue=bool(rule.get("stop_if_true", False)), dxf=dxf),
                )

            # ── cellIs ────────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_CELL_IS:
                if not formula:
                    raise HTTPException(status_code=422, detail=f"CF rule[{index}] cellIs requires formula")
                operator = str(rule.get("operator") or "equal").strip()
                ws.conditional_formatting.add(
                    target_range,
                    Rule(type="cellIs", operator=operator, formula=formula,
                         stopIfTrue=bool(rule.get("stop_if_true", False)), dxf=dxf),
                )

            # ── text-match rules ──────────────────────────────────────────
            elif rule_type in EXCEL_TEXT_RULE_OPERATOR_MAP:
                text_value = rule.get("text")
                if text_value in (None, ""):
                    raise HTTPException(status_code=422, detail=f"CF rule[{index}] {rule_type} requires text")
                first_cell = _excel_first_cell(target_range)
                operator   = EXCEL_TEXT_RULE_OPERATOR_MAP[rule_type]
                text_str   = str(text_value)
                formula_text = {
                    EXCEL_RULE_TYPE_CONTAINS_TEXT:     _build_contains_text_formula(first_cell, text_str),
                    EXCEL_RULE_TYPE_NOT_CONTAINS_TEXT: f"NOT({_build_contains_text_formula(first_cell, text_str)})",
                    EXCEL_RULE_TYPE_BEGINS_WITH:        _build_begins_with_formula(first_cell, text_str),
                    EXCEL_RULE_TYPE_ENDS_WITH:          _build_ends_with_formula(first_cell, text_str),
                }[rule_type]
                ws.conditional_formatting.add(
                    target_range,
                    Rule(type="containsText", operator=operator, text=text_str,
                         formula=[formula_text], dxf=dxf),
                )

            # ── duplicate / unique ────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_DUPLICATE_RECORDS:
                ws.conditional_formatting.add(target_range, Rule(type="duplicateValues", dxf=dxf))

            elif rule_type == EXCEL_RULE_TYPE_UNIQUE_RECORDS:
                ws.conditional_formatting.add(target_range, Rule(type="uniqueValues", dxf=dxf))

            # ── top10 ─────────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_TOP_10:
                try:
                    rank = max(1, min(int(rule.get("rank") or 10), 1000))
                except (TypeError, ValueError):
                    rank = 10
                ws.conditional_formatting.add(
                    target_range,
                    Rule(type="top10", rank=rank,
                         percent=bool(rule.get("percent", False)),
                         bottom=bool(rule.get("bottom", False)), dxf=dxf),
                )

            # ── aboveAverage ──────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_ABOVE_AVERAGE:
                above = rule.get("above_average")
                if above is None:
                    above = not bool(rule.get("below_average", False))
                ws.conditional_formatting.add(
                    target_range,
                    Rule(type="aboveAverage", aboveAverage=bool(above),
                         equalAverage=bool(rule.get("equal_average", False)), dxf=dxf),
                )

            # ── timePeriod ────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_TIME_PERIOD:
                time_period = str(rule.get("time_period") or rule.get("period") or "").strip()
                if not time_period:
                    raise HTTPException(status_code=422, detail=f"CF rule[{index}] timePeriod requires time_period")
                ws.conditional_formatting.add(
                    target_range,
                    Rule(type="timePeriod", timePeriod=time_period, dxf=dxf),
                )

            # ── colorScale ────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_COLOR_SCALE:
                colors = list(rule.get("colors") or [])
                if len(colors) == 2:
                    ws.conditional_formatting.add(
                        target_range,
                        ColorScaleRule(
                            start_type="min",  start_color=_excel_color(colors[0], "F8696B"),
                            end_type="max",    end_color=_excel_color(colors[1], "63BE7B"),
                        ),
                    )
                elif len(colors) >= 3:
                    ws.conditional_formatting.add(
                        target_range,
                        ColorScaleRule(
                            start_type="min",        start_color=_excel_color(colors[0], "F8696B"),
                            mid_type="percentile",   mid_value=50,
                            mid_color=_excel_color(colors[1], "FFEB84"),
                            end_type="max",          end_color=_excel_color(colors[2], "63BE7B"),
                        ),
                    )
                else:
                    raise HTTPException(status_code=422, detail=f"CF rule[{index}] colorScale requires 2 or 3 colors")

            # ── dataBar ───────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_DATA_BAR:
                # openpyxl DataBarRule requires start/end values as strings when type != "min"/"max"
                start_type  = str(rule.get("start_type") or "min")
                end_type    = str(rule.get("end_type")   or "max")
                start_value = None if start_type in ("min", "autoMin") else str(rule.get("start_value") or "0")
                end_value   = None if end_type   in ("max", "autoMax") else str(rule.get("end_value")   or "100")
                ws.conditional_formatting.add(
                    target_range,
                    DataBarRule(
                        start_type=start_type, start_value=start_value,
                        end_type=end_type,     end_value=end_value,
                        color=_excel_color(rule.get("color"), "638EC6"),
                        showValue=bool(rule.get("show_value", True)),
                    ),
                )

            # ── iconSet ───────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_ICON_SET:
                icon_style = str(rule.get("icon_style") or "3TrafficLights1")
                thresholds = list(rule.get("values") or [0, 33, 67])
                icon_kwargs: dict = dict(
                    icon_style=icon_style,
                    type=str(rule.get("value_type") or "percent"),
                    values=thresholds,
                )
                # Only pass optional booleans when explicitly provided to avoid
                # openpyxl writing 'None' literally into the XML attribute.
                if rule.get("show_value") is not None:
                    icon_kwargs["showValue"] = rule["show_value"]
                if rule.get("percent") is not None:
                    icon_kwargs["percent"] = rule["percent"]
                if rule.get("reverse") is not None:
                    icon_kwargs["reverse"] = rule["reverse"]
                ws.conditional_formatting.add(target_range, IconSetRule(**icon_kwargs))

            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported Excel conditional formatting rule type: {rule_type!r}",
                )

        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "_apply_excel_conditional_formatting failed | rule_index=%s type=%s range=%s",
                index, rule_type, target_range,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Excel conditional format rule[{index}] (type={rule_type!r}) failed during application.",
            )

def _write_csv(
    cursor,
    out_path: str,
    *,
    delimiter: str,
    quotechar: str,
    escapechar: Optional[str],
    null_sequence: str,
    fetch_size: int,
    progress_label: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    started_at = time_module.perf_counter()
    logger.info("Entering _write_csv | label=%s out_path=%s", progress_label or out_path, out_path)
    col_names = [d[0] for d in (cursor.description or [])]
    total_rows = 0
    chunk_index = 0
    effective_escapechar = escapechar or "\\"

    logger.info(
        "Starting CSV export | label=%s out_path=%s fetch_size=%s columns=%s",
        progress_label or out_path,
        out_path,
        fetch_size,
        len(col_names),
    )

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f,
            delimiter=delimiter,
            quotechar=quotechar,
            quoting=csv.QUOTE_MINIMAL,
            escapechar=effective_escapechar,
            lineterminator="\n",
        )
        writer.writerow(col_names)
        for rows in _iter_rows_in_chunks(cursor, fetch_size):
            chunk_index += 1
            for row in rows:
                total_rows += 1
                writer.writerow([null_sequence if v is None else v for v in row])
            logger.info(
                "CSV export chunk written | label=%s chunk_index=%s rows_in_chunk=%s total_rows=%s",
                progress_label or out_path,
                chunk_index,
                len(rows),
                total_rows,
            )
            if progress_callback is not None:
                progress_callback(len(rows), total_rows)

    return total_rows, len(col_names)


def _write_json(
    cursor,
    out_path: str,
    *,
    null_sequence: Optional[str],
    fetch_size: int,
    progress_label: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    started_at = time_module.perf_counter()
    logger.info("Entering _write_json | label=%s out_path=%s", progress_label or out_path, out_path)
    col_names = [d[0] for d in (cursor.description or [])]
    total_rows = 0
    chunk_index = 0

    logger.info(
        "Starting JSON export | label=%s out_path=%s fetch_size=%s columns=%s",
        progress_label or out_path,
        out_path,
        fetch_size,
        len(col_names),
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("[")
        first_row = True
        for rows in _iter_rows_in_chunks(cursor, fetch_size):
            chunk_index += 1
            for row in rows:
                total_rows += 1
                obj = {}
                for idx, col in enumerate(col_names):
                    val = row[idx]
                    if val is None and null_sequence is not None:
                        val = null_sequence
                    obj[col] = val
                if not first_row:
                    f.write(",")
                f.write(json.dumps(obj, default=str))
                first_row = False
            logger.info(
                "JSON export chunk written | label=%s chunk_index=%s rows_in_chunk=%s total_rows=%s",
                progress_label or out_path,
                chunk_index,
                len(rows),
                total_rows,
            )
            if progress_callback is not None:
                progress_callback(len(rows), total_rows)
        f.write("]")

    return total_rows, len(col_names)

def _write_parquet(
    cursor,
    out_path: str,
    *,
    fetch_size: int,
    progress_label: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    started_at = time_module.perf_counter()
    logger.info("Entering _write_parquet | label=%s out_path=%s", progress_label or out_path, out_path)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception:
        logger.exception("Entering _write_parquet dependency failure")
        raise HTTPException(status_code=500, detail="Parquet dependency missing: pyarrow is not installed")

    col_names = [d[0] for d in (cursor.description or [])]
    total_rows = 0
    chunk_index = 0
    writer = None
    target_schema = None
    logger.info(
        "Starting Parquet export | label=%s out_path=%s fetch_size=%s columns=%s",
        progress_label or out_path,
        out_path,
        fetch_size,
        len(col_names),
    )
    try:
        for rows in _iter_rows_in_chunks(cursor, fetch_size):
            chunk_index += 1
            total_rows += len(rows)
            columns = list(zip(*rows)) if rows else []
            if target_schema is None:
                target_schema = _build_parquet_schema(pa, list(cursor.description or []), list(rows))

            arrays = []
            for idx, _ in enumerate(col_names):
                col_vals = list(columns[idx]) if columns else []
                arrays.append(pa.array(col_vals, type=target_schema.field(idx).type))
            table = pa.Table.from_arrays(arrays, schema=target_schema)
            if writer is None:
                writer = pq.ParquetWriter(out_path, target_schema)
            writer.write_table(table)
            logger.info(
                "Parquet export chunk written | label=%s chunk_index=%s rows_in_chunk=%s total_rows=%s",
                progress_label or out_path,
                chunk_index,
                len(rows),
                total_rows,
            )
            if progress_callback is not None:
                progress_callback(len(rows), total_rows)
    finally:
        if writer is not None:
            writer.close()

    return total_rows, len(col_names)

def _write_excel(
    cursor,
    out_path: str,
    *,
    sheet_name: Optional[str],
    excel_options: Optional[dict],
    conditional_formats: Optional[List[dict]],
    fetch_size: int,
    progress_label: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """Writes Excel files using xlsxwriter in constant memory mode exclusively.

    This function delegates the writing process directly to _write_excel_xlsxwriter,
    ensuring low memory consumption (streaming writing) and consistent styling/formatting.
    """
    label = progress_label or out_path
    logger.info("_write_excel starting with xlsxwriter exclusively | label=%s out_path=%s", label, out_path)
    print(f"_write_excel starting with xlsxwriter exclusively | label={label} out_path={out_path}")

    return _write_excel_xlsxwriter(
        cursor,
        out_path,
        sheet_name=sheet_name,
        excel_options=excel_options,
        conditional_formats=conditional_formats,
        fetch_size=fetch_size,
        progress_label=progress_label,
        progress_callback=progress_callback,
    )


def _write_excel_openpyxl(
    cursor,
    out_path: str,
    *,
    sheet_name: Optional[str],
    excel_options: Optional[dict],
    conditional_formats: Optional[List[dict]],
    fetch_size: int,
    progress_label: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    label = progress_label or out_path
    logger.info("_write_excel_openpyxl starting | label=%s out_path=%s", label, out_path)
    print("_write_excel_openpyxl starting | label=%s out_path=%s", label, out_path)

    col_names = [d[0] for d in (cursor.description or [])]
    n_cols    = len(col_names)

    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
        from openpyxl.cell import WriteOnlyCell
    except ImportError:
        raise HTTPException(status_code=500, detail="Excel dependency missing: openpyxl is not installed")

    try:
        faulthandler.enable(all_threads=True)
    except Exception:
        pass

    options   = dict(excel_options or {})

    # ── column limit: checked once before any workbook work ───────────────────
    if n_cols > 16384:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Excel column limit exceeded for export '{label}': "
                f"{n_cols:,} columns in source table, Excel supports at most 16,384. "
                f"Use CSV, JSON, or Parquet format for wide tables."
            ),
        )

    # ── resolve options ───────────────────────────────────────────────────────
    use_write_only     = False
    auto_width         = bool(options.get("auto_width", True))
    auto_filter        = bool(options.get("auto_filter", True))
    width_sample_limit = max(0, int(options.get("width_sample_rows") or 200))
    default_date_fmt   = str(options.get("date_format")     or "yyyy-mm-dd")
    default_dt_fmt     = str(options.get("datetime_format") or "yyyy-mm-dd hh:mm:ss")
    default_time_fmt   = str(options.get("time_format")     or "hh:mm:ss")

    sheet_header_style = options.get("sheet_header_style") or {}
    header_style       = options.get("header_style")       or {}
    body_style         = options.get("body_style")         or {}

    sheet_header_text    = str(options.get("sheet_header_title") or options.get("sheet_header") or options.get("title") or "").strip()
    sheet_header_enabled = options.get("sheet_header_enabled")
    has_sheet_header_cfg = bool(sheet_header_text or sheet_header_style or options.get("sheet_header_row_span") or options.get("sheet_header_merge_rows"))
    render_sheet_header  = (_excel_option_enabled(sheet_header_enabled) if sheet_header_enabled is not None else has_sheet_header_cfg)

    # ── explicit column format overrides (per column name) ────────────────────
    explicit_col_formats: List[Optional[str]] = [
        options.get("column_formats", {}).get(str(col).strip())
        or options.get("column_formats", {}).get(str(col).strip().upper())
        for col in col_names
    ]
    # col_formats is finalised after first-chunk type sniff; None until then.
    col_formats: Optional[List[Optional[str]]] = None

    normalized_col_names = [str(c).strip().upper() for c in col_names]

    # ── banding / body style objects — built ONCE ─────────────────────────────
    banding_fill_color = _excel_color(body_style.get("banding_fill"))
    banding_font_color = _excel_color(body_style.get("banding_font_color"))
    try:
        banding_frequency = max(1, int(body_style.get("banding_frequency") or 2))
    except (TypeError, ValueError):
        banding_frequency = 2

    body_fill    = _excel_pattern_fill({**body_style, "fill": body_style.get("fill") or body_style.get("fill_color")})
    banding_fill = _excel_pattern_fill({"fill": banding_fill_color}) if banding_fill_color else None
    body_font    = _excel_font(body_style, default_name="Calibri", default_size=11, default_bold=False, default_italic=False)
    banding_font = (
        _excel_font({**body_style, "font_color": banding_font_color}, default_name="Calibri", default_size=11, default_bold=False, default_italic=False)
        if banding_font_color else body_font
    )
    body_alignment = _excel_alignment(body_style)

    # ── width tracking ────────────────────────────────────────────────────────
    widths: List[float] = [_estimate_excel_text_width(name, is_header=True) for name in col_names]

    # ── workbook / sheet ──────────────────────────────────────────────────────
    safe_title = _excel_safe_sheet_name(sheet_name or options.get("sheet_name"))
    wb = Workbook(write_only=use_write_only)
    ws = wb.create_sheet(title=safe_title) if use_write_only else wb.active
    if not use_write_only:
        ws.title = safe_title

    sheet_header_row_idx  = None
    column_header_row_idx = 1
    first_data_row        = 2

    # ── header rows ───────────────────────────────────────────────────────────
    if use_write_only:
        sh_fill  = _excel_pattern_fill({**sheet_header_style, "fill": sheet_header_style.get("fill") or "D9EAF7"})
        sh_font  = _excel_font(sheet_header_style, default_name="Calibri", default_size=16, default_color="000000", default_bold=True, default_italic=False)
        sh_align = _excel_alignment(sheet_header_style, default_horizontal="center", default_wrap_text=False)
        ch_fill  = _excel_pattern_fill({**header_style, "fill": header_style.get("fill") or "1F4E78"})
        ch_font  = _excel_font(header_style, default_name="Calibri", default_size=11, default_color="FFFFFF", default_bold=True, default_italic=False)
        ch_align = _excel_alignment(header_style, default_horizontal="center", default_wrap_text=True)

        def _hcell(value, *, fill=None, font=None, align=None, number_format=None):
            c = WriteOnlyCell(ws, value=value)
            if fill:            c.fill          = fill
            if font:            c.font          = font
            if align:           c.alignment     = align
            if number_format:   c.number_format = number_format
            return c

        if render_sheet_header:
            try:
                row_span = max(1, int(options.get("sheet_header_row_span") or options.get("sheet_header_merge_rows") or 1))
            except (TypeError, ValueError):
                row_span = 1
            header_title = sheet_header_text or safe_title
            ws.append([_hcell(header_title if i == 0 else "", fill=sh_fill, font=sh_font, align=sh_align) for i in range(max(n_cols, 1))])
            for _ in range(row_span - 1):
                ws.append([_hcell("", fill=sh_fill, font=sh_font, align=sh_align) for _ in range(max(n_cols, 1))])
            sheet_header_row_idx  = 1
            column_header_row_idx = row_span + 1
            first_data_row        = column_header_row_idx + 1

        ws.append([_hcell(col, fill=ch_fill, font=ch_font, align=ch_align) for col in col_names] if header_style else list(col_names))
        if bool(options.get("freeze_header", True)):
            ws.freeze_panes = f"A{first_data_row}"
        if options.get("freeze_panes"):
            ws.freeze_panes = options["freeze_panes"]

    else:
        if render_sheet_header:
            try:
                row_span = max(1, int(options.get("sheet_header_row_span") or options.get("sheet_header_merge_rows") or 1))
            except (TypeError, ValueError):
                row_span = 1
            header_title = sheet_header_text or safe_title
            ws.append([header_title] + [""] * max(n_cols - 1, 0))
            for _ in range(row_span - 1):
                ws.append([""] * n_cols)
            sheet_header_row_idx  = 1
            column_header_row_idx = row_span + 1
            first_data_row        = column_header_row_idx + 1
            for r in range(sheet_header_row_idx, column_header_row_idx):
                _apply_style_to_row(ws, r, sheet_header_style, default_fill_color="D9EAF7", default_font_name="Calibri",
                                    default_font_size=16, default_font_color="000000", default_bold=True, default_italic=False,
                                    default_horizontal="center", default_wrap_text=False)
            if n_cols > 0:
                ws.merge_cells(start_row=sheet_header_row_idx, start_column=1, end_row=column_header_row_idx - 1, end_column=n_cols)

        ws.append(col_names)
        if header_style:
            _apply_style_to_row(ws, column_header_row_idx, header_style, default_fill_color="1F4E78", default_font_name="Calibri",
                                 default_font_size=11, default_font_color="FFFFFF", default_bold=True, default_italic=False,
                                 default_horizontal="center", default_wrap_text=True)

    # ── row iteration ─────────────────────────────────────────────────────────
    total_rows  = 0
    chunk_index = 0
    timezone_aware_columns: dict = {}
    timezone_aware_samples: dict  = {}
    export_started_at = time_module.perf_counter()
    _excel_log_memory(label, prefix="MEMORY BEFORE EXCEL EXPORT")

    # Pre-resolve style objects so the hot loop never rebuilds them.
    _body_fill    = body_fill
    _band_fill    = banding_fill
    _body_font    = body_font
    _band_font    = banding_font
    _body_align   = body_alignment
    _use_banding  = bool(banding_fill_color)
    _band_freq    = banding_frequency

    # ── row limit computed ONCE here, used as an early-exit guard inside the loop ──
    # first_data_row is finalised above after header layout is resolved, so this
    # is the correct place: after headers, before any data fetch.
    max_data_rows = 1048576 - first_data_row + 1

    for rows in _iter_rows_in_chunks(cursor, fetch_size):
        chunk_index += 1
        chunk_started_at = time_module.perf_counter()
        chunk_len = len(rows)

        # ── one-time column format sniff from first row of first chunk ────────
        if col_formats is None and rows:
            col_formats = _sniff_column_formats(
                rows[0], explicit_col_formats,
                default_date_fmt, default_dt_fmt, default_time_fmt,
            )
            logger.info(
                "_write_excel column formats resolved | label=%s formats=%s",
                label,
                [f for f in col_formats if f],
            )
            print(
                "_write_excel column formats resolved | label=%s formats=%s",
                label,
                [f for f in col_formats if f],
            )

        for row in rows:
            # ── row limit guard: raise BEFORE writing the offending row ───────
            # Checked here so we never buffer an oversized workbook in memory.
            # total_rows reflects rows *already written*; if it already equals
            # max_data_rows the very next append would overflow the sheet.
            if total_rows >= max_data_rows:
                logger.error(
                    "_write_excel row limit exceeded | label=%s total_rows=%s max_data_rows=%s first_data_row=%s",
                    label, total_rows, max_data_rows, first_data_row,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Excel row limit exceeded for export '{label}': "
                        f"source table has more than {max_data_rows:,} data rows "
                        f"(Excel worksheet limit is 1,048,576 total rows, "
                        f"minus {first_data_row - 1} header row(s) = {max_data_rows:,} data rows). "
                        f"Use CSV, JSON, or Parquet format for large tables."
                    ),
                )

            total_rows += 1
            is_band = _use_banding and ((total_rows - 1) % _band_freq != 0)

            if total_rows % 5000 == 0:
                _excel_log_memory(label, prefix=f"MEMORY row={total_rows} chunk={chunk_index}")

            # ── normalise values (avoid repeated isinstance in hot path) ──────
            display_row: List[Any] = []
            for idx, value in enumerate(row):
                if value is not None and not isinstance(value, (int, float, str, bool)):
                    if _excel_is_timezone_aware_datetime(value):
                        col_key = col_names[idx] if idx < n_cols else f"COL_{idx + 1}"
                        timezone_aware_columns[col_key] = timezone_aware_columns.get(col_key, 0) + 1
                        if col_key not in timezone_aware_samples:
                            timezone_aware_samples[col_key] = value.isoformat()
                    display_row.append(_excel_display_value(value))
                else:
                    display_row.append(value)

            # ── width sampling (first N rows only) ────────────────────────────
            if auto_width and total_rows <= width_sample_limit:
                for idx, val in enumerate(display_row):
                    w = _estimate_excel_text_width("" if val is None else str(val))
                    if w > widths[idx]:
                        widths[idx] = w

            # ── append row ────────────────────────────────────────────────────
            if use_write_only:
                row_fill  = _band_fill  if is_band else _body_fill
                row_font  = _band_font  if is_band else _body_font
                row_cells = []
                for i, val in enumerate(display_row):
                    c = WriteOnlyCell(ws, value=val)
                    if row_font    is not None: c.font          = row_font
                    if _body_align is not None: c.alignment     = _body_align
                    if row_fill    is not None: c.fill          = row_fill
                    nf = col_formats[i] if col_formats and i < len(col_formats) else None
                    if nf: c.number_format = nf
                    row_cells.append(c)
                ws.append(row_cells)

            else:
                ws.append(display_row)
                current_row = first_data_row + total_rows - 1
                row_fill = _band_fill if is_band else _body_fill
                row_font = _band_font if is_band else _body_font
                for idx, val in enumerate(display_row, start=1):
                    cell = ws.cell(row=current_row, column=idx)
                    if row_font    is not None: cell.font      = row_font
                    if _body_align is not None: cell.alignment = _body_align
                    if row_fill    is not None: cell.fill      = row_fill
                    nf = col_formats[idx - 1] if col_formats and (idx - 1) < len(col_formats) else None
                    if nf: cell.number_format = nf

        chunk_elapsed = time_module.perf_counter() - chunk_started_at
        rows_per_sec  = chunk_len / max(chunk_elapsed, 0.001)
        logger.info(
            "_write_excel chunk done | label=%s chunk=%s rows_in_chunk=%s total=%s elapsed=%.3fs rows_per_sec=%.0f",
            label, chunk_index, chunk_len, total_rows, chunk_elapsed, rows_per_sec,
        )
        print(
            "_write_excel chunk done | label=%s chunk=%s rows_in_chunk=%s total=%s elapsed=%.3fs rows_per_sec=%.0f",
            label, chunk_index, chunk_len, total_rows, chunk_elapsed, rows_per_sec,
        )
        if progress_callback is not None:
            progress_callback(chunk_len, total_rows)

    # ── post-write sheet config ───────────────────────────────────────────────
    if auto_width:
        for idx, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = min(max(w, 10), 60)

    for hidden_col in options.get("hidden_columns") or []:
        col_upper = str(hidden_col or "").strip().upper()
        if not col_upper:
            continue
        for idx, name in enumerate(normalized_col_names, start=1):
            if name == col_upper:
                ws.column_dimensions[get_column_letter(idx)].hidden = True
                break

    if not use_write_only:
        if auto_filter and col_names:
            last_col = get_column_letter(n_cols)
            last_row = max(ws.max_row, column_header_row_idx)
            ws.auto_filter.ref = f"A{column_header_row_idx}:{last_col}{last_row}"
        freeze = options.get("freeze_panes")
        if freeze:
            ws.freeze_panes = freeze
        elif bool(options.get("freeze_header", True)):
            ws.freeze_panes = f"A{first_data_row}"

    _apply_excel_conditional_formatting(
        ws, list(conditional_formats or []), col_names,
        first_data_row=first_data_row,
        last_data_row=first_data_row + total_rows - 1,
    )

    if timezone_aware_columns:
        logger.info("_write_excel tz-aware columns | label=%s columns=%s samples=%s", label, timezone_aware_columns, timezone_aware_samples)
        print("_write_excel tz-aware columns | label=%s columns=%s samples=%s", label, timezone_aware_columns, timezone_aware_samples)

    # ── save + size log ───────────────────────────────────────────────────────
    write_elapsed = time_module.perf_counter() - export_started_at
    _excel_log_memory(label, prefix="MEMORY BEFORE SAVE")
    logger.info("_write_excel saving | label=%s rows=%s cols=%s write_elapsed=%.3fs", label, total_rows, n_cols, write_elapsed)
    print("_write_excel saving | label=%s rows=%s cols=%s write_elapsed=%.3fs", label, total_rows, n_cols, write_elapsed)

    try:
        wb.save(out_path)
    except Exception:
        logger.exception("_write_excel save failed | label=%s rows=%s", label, total_rows)
        raise

    try:
        file_size_bytes = os.path.getsize(out_path)
        file_size_mb    = file_size_bytes / (1024 * 1024)
    except OSError:
        file_size_bytes = 0
        file_size_mb    = 0.0

    total_elapsed = time_module.perf_counter() - export_started_at
    logger.info(
        "_write_excel complete | label=%s rows=%s cols=%s "
        "file_size_mb=%.2f write_elapsed=%.3fs total_elapsed=%.3fs rows_per_sec=%.0f",
        label, total_rows, n_cols, file_size_mb,
        write_elapsed, total_elapsed,
        total_rows / max(write_elapsed, 0.001),
    )
    print(
        "_write_excel complete | label=%s rows=%s cols=%s "
        "file_size_mb=%.2f write_elapsed=%.3fs total_elapsed=%.3fs rows_per_sec=%.0f",
        label, total_rows, n_cols, file_size_mb,
        write_elapsed, total_elapsed,
        total_rows / max(write_elapsed, 0.001),
    )
    if file_size_mb > 50:
        logger.warning(
            "_write_excel large file warning | label=%s file_size_mb=%.2f — upload may be slow or throttled by Graph API",
            label, file_size_mb,
        )

    import gc
    gc.collect()

    return total_rows, n_cols


# ==============================================================================
# XlsxWriter memory optimized writing path
# ==============================================================================

XLSXWRITER_OPERATOR_MAP = {
    "equal": "equal to",
    "notequal": "not equal to",
    "greaterthan": "greater than",
    "lessthan": "less than",
    "greaterthanorequal": "greater than or equal to",
    "lessthanorequal": "less than or equal to",
    "between": "between",
    "notbetween": "not between",
}

def _xlsxwriter_format_dict(style_dict: Optional[dict]) -> dict:
    """Converts a CSS-style formatting dictionary to xlsxwriter keys.

    Args:
        style_dict: Dictionary containing visual styling parameters.

    Returns:
        A dictionary containing native xlsxwriter cell format keys.
    """
    if not isinstance(style_dict, dict) or not style_dict:
        return {}
    fmt = {}
    
    # Fill/Background Color
    fill_color = style_dict.get("fill") or style_dict.get("fill_color") or style_dict.get("background_color")
    if fill_color:
        fmt['bg_color'] = _normalize_color_for_xlsxwriter(fill_color, fill_color)
        
    # Font properties
    font_color = style_dict.get("font_color") or style_dict.get("color")
    if font_color:
        fmt['font_color'] = _normalize_color_for_xlsxwriter(font_color, font_color)
        
    if style_dict.get("bold"):
        fmt['bold'] = True
    if style_dict.get("italic"):
        fmt['italic'] = True
    if style_dict.get("underline"):
        fmt['underline'] = True
    if style_dict.get("strikethrough"):
        fmt['font_strikeout'] = True
        
    # Alignment
    h = style_dict.get("horizontal")
    if h:
        fmt['align'] = h
    v = style_dict.get("vertical")
    if v:
        v_str = str(v).strip().lower()
        if v_str == "center":
            v_str = "vcenter"
        fmt['valign'] = v_str
        
    wrap = style_dict.get("wrap_text")
    if wrap is not None:
        fmt['text_wrap'] = bool(wrap)
        
    return fmt

def _normalize_color_for_xlsxwriter(val: Any, default: str) -> str:
    """Normalizes color value to standard hex string with # prefix.

    Args:
        val: Input color value.
        default: Default color to fallback to.

    Returns:
        Cleaned 6-character hex color string starting with #.
    """
    color = _excel_color(val, default)
    if not color:
        # Strip default to 6 characters
        def_clean = str(default).lstrip('#')
        if len(def_clean) == 8:
            def_clean = def_clean[2:]
        return '#' + def_clean

    # xlsxwriter requires 6-character RGB hex codes. Strip the alpha channel (leading 'FF') if present.
    if len(color) == 8:
        color = color[2:]
    return '#' + color

def _normalize_icon_style(style: str) -> str:
    """Translates openpyxl icon style names to xlsxwriter equivalents.

    Args:
        style: openpyxl-style icon set identifier.

    Returns:
        Matching xlsxwriter icon style identifier string.
    """
    s = str(style).strip()
    if s == '3TrafficLights1': return '3_traffic_lights'
    if s == '3TrafficLights2': return '3_traffic_lights_rimmed'
    if s == '3Arrows': return '3_arrows'
    if s == '3ArrowsGray': return '3_arrows_gray'
    if s == '3Flags': return '3_flags'
    if s == '3Signs': return '3_signs'
    if s == '3Symbols': return '3_symbols'
    if s == '3Symbols2': return '3_symbols_2'
    if s == '4Arrows': return '4_arrows'
    if s == '4ArrowsGray': return '4_arrows_gray'
    if s == '4Rating': return '4_ratings'
    if s == '4RedToBlack': return '4_red_to_black'
    if s == '4TrafficLights': return '4_traffic_lights'
    if s == '5Arrows': return '5_arrows'
    if s == '5ArrowsGray': return '5_arrows_gray'
    if s == '5Rating': return '5_ratings'
    if s == '5Quarters': return '5_quarters'
    return '3_traffic_lights'

def _create_xlsxwriter_format(
    workbook,
    style_dict: dict,
    *,
    is_header: bool = False,
    default_bg: Optional[str] = None,
    default_fg: Optional[str] = None,
    default_bold: bool = False,
    default_valign: Optional[str] = None
) -> Any:
    """Builds an xlsxwriter format object from style settings and default values.

    Args:
        workbook: The active xlsxwriter Workbook.
        style_dict: Dictionary with styling parameters.
        is_header: True if setting up sheet headers, False otherwise.
        default_bg: Fallback background color.
        default_fg: Fallback font color.
        default_bold: Fallback bold flag.

    Returns:
        Created Format object.
    """
    fmt_dict = {}
    
    # Background fill
    fill = style_dict.get("fill") or style_dict.get("fill_color") or style_dict.get("background_color") or default_bg
    if fill:
        fmt_dict['bg_color'] = _normalize_color_for_xlsxwriter(fill, fill)
        
    # Font properties
    font_color = style_dict.get("font_color") or style_dict.get("color") or default_fg
    if font_color:
        fmt_dict['font_color'] = _normalize_color_for_xlsxwriter(font_color, font_color)
        
    font_name = style_dict.get("font_name") or "Calibri"
    fmt_dict['font_name'] = font_name
    
    font_size = style_dict.get("font_size") or (16 if is_header else 11)
    fmt_dict['font_size'] = int(float(font_size))
    
    bold = style_dict.get("bold")
    if bold is not None:
        fmt_dict['bold'] = bool(bold)
    elif default_bold:
        fmt_dict['bold'] = True
        
    italic = style_dict.get("italic")
    if italic is not None:
        fmt_dict['italic'] = bool(italic)
        
    underline = style_dict.get("underline")
    if underline:
        fmt_dict['underline'] = True
        
    strike = style_dict.get("strikethrough")
    if strike:
        fmt_dict['font_strikeout'] = True
        
    # Alignment
    h = style_dict.get("horizontal")
    if h:
        fmt_dict['align'] = h
    v = style_dict.get("vertical") or default_valign
    if v:
        v_str = str(v).strip().lower()
        if v_str == "center":
            v_str = "vcenter"
        fmt_dict['valign'] = v_str
        
    wrap = style_dict.get("wrap_text")
    if wrap is not None:
        fmt_dict['text_wrap'] = bool(wrap)
        
    return workbook.add_format(fmt_dict)

def _apply_xlsxwriter_conditional_formatting(
    workbook,
    worksheet,
    rules: List[dict],
    col_names: List[str],
    first_data_row: int,
    last_data_row: int,
) -> None:
    """Applies conditional formatting rules to the xlsxwriter worksheet.

    Args:
        workbook: Active xlsxwriter Workbook.
        worksheet: The target Worksheet object.
        rules: List of dictionaries defining conditional formatting rules.
        col_names: List of column names in the sheet.
        first_data_row: 1-based index of the first row of data.
        last_data_row: 1-based index of the last row of data.
    """
    if not rules or last_data_row < first_data_row:
        logger.info("_apply_xlsxwriter_conditional_formatting skipped | rules count: %s", len(rules or []))
        return

    column_index_map = _excel_column_index_map(col_names)
    logger.info(
        "Applying xlsxwriter conditional formatting | rules_count=%s row_range=%s-%s",
        len(rules), first_data_row, last_data_row,
    )

    expanded_rules = []
    for raw_rule in rules:
        targets = (raw_rule or {}).get("target_columns") if isinstance(raw_rule, dict) else None
        if isinstance(targets, list) and targets:
            for target in targets:
                expanded_rule = dict(raw_rule)
                expanded_rule["column_name"] = target
                expanded_rule.pop("target_columns", None)
                expanded_rules.append(expanded_rule)
        else:
            expanded_rules.append(raw_rule)

    for index, raw_rule in enumerate(expanded_rules):
        rule = dict(raw_rule or {})
        rule_type = str(rule.get("type") or "").strip().lower()

        try:
            target_range = _resolve_excel_rule_range(
                rule, column_index_map, first_data_row, last_data_row
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("CF rule range resolve failed for rule index %s: %s", index, exc)
            continue

        dxf_dict = _xlsxwriter_format_dict(rule.get("style"))
        fmt = workbook.add_format(dxf_dict) if dxf_dict else None

        try:
            opt: dict = {}
            if fmt:
                opt['format'] = fmt

            highlight_scope = str(rule.get("highlight_scope") or "cell").strip().lower()
            condition_column = str(rule.get("condition_column_name") or rule.get("column_name") or "").strip().upper()
            if condition_column and condition_column in column_index_map and rule_type not in (EXCEL_RULE_TYPE_COLOR_SCALE, EXCEL_RULE_TYPE_DATA_BAR, EXCEL_RULE_TYPE_ICON_SET) and (highlight_scope in {"entire_row", "entire_column"} or rule.get("condition_column_name")):
                column_name = condition_column
                if column_name in column_index_map:
                    from openpyxl.utils import get_column_letter
                    col_letter = get_column_letter(column_index_map[column_name])
                    formula_str = _build_entire_row_formula(rule, col_letter, first_data_row)
                    if formula_str:
                        opt['type'] = 'formula'
                        opt['criteria'] = formula_str
                        logger.info(
                            "CF rule[%s] applying formula rule | condition_column=%s range=%s formula=%s",
                            index, column_name, target_range, formula_str
                        )
                        worksheet.conditional_format(target_range, opt)
                        continue

            # ── formula / expression ──────────────────────────────────────
            if rule_type in {EXCEL_RULE_TYPE_FORMULA, EXCEL_RULE_TYPE_EXPRESSION, EXCEL_RULE_TYPE_MULTIPLE_CONDITIONS}:
                formula = rule.get("formula") or []
                if isinstance(formula, str):
                    formula = [formula]
                formula_str = formula[0] if formula else ""
                if rule_type == EXCEL_RULE_TYPE_MULTIPLE_CONDITIONS:
                    compiled_formula = _compiled_multiple_conditions_formula(
                        rule, col_names, first_data_row
                    )
                    if compiled_formula:
                        formula_str = compiled_formula
                if not formula_str:
                    continue
                opt['type'] = 'formula'
                opt['criteria'] = formula_str
                worksheet.conditional_format(target_range, opt)

            # ── cellIs ────────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_CELL_IS:
                formula = rule.get("formula") or []
                if isinstance(formula, str):
                    formula = [formula]
                formula_str = formula[0] if formula else ""
                if not formula_str:
                    continue
                
                raw_op = str(rule.get("operator") or "equal").strip().lower().replace(" ", "")
                op = XLSXWRITER_OPERATOR_MAP.get(raw_op, "equal to")
                
                opt['type'] = 'cell'
                opt['criteria'] = op
                if len(formula) >= 2:
                    opt['minimum'] = formula[0]
                    opt['maximum'] = formula[1]
                else:
                    opt['value'] = formula_str
                worksheet.conditional_format(target_range, opt)

            # ── text-match rules ──────────────────────────────────────────
            elif rule_type in EXCEL_TEXT_RULE_OPERATOR_MAP:
                text_value = str(rule.get("text") or "")
                if not text_value:
                    continue
                
                opt['type'] = 'text'
                if rule_type == EXCEL_RULE_TYPE_CONTAINS_TEXT:
                    opt['criteria'] = 'containing'
                elif rule_type == EXCEL_RULE_TYPE_NOT_CONTAINS_TEXT:
                    opt['criteria'] = 'not containing'
                elif rule_type == EXCEL_RULE_TYPE_BEGINS_WITH:
                    opt['criteria'] = 'begins with'
                elif rule_type == EXCEL_RULE_TYPE_ENDS_WITH:
                    opt['criteria'] = 'ends with'
                
                opt['value'] = text_value
                worksheet.conditional_format(target_range, opt)

            # ── duplicate / unique ────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_DUPLICATE_RECORDS:
                opt['type'] = 'duplicate'
                worksheet.conditional_format(target_range, opt)

            elif rule_type == EXCEL_RULE_TYPE_UNIQUE_RECORDS:
                opt['type'] = 'unique'
                worksheet.conditional_format(target_range, opt)

            # ── top10 ─────────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_TOP_10:
                try:
                    rank = max(1, min(int(rule.get("rank") or 10), 1000))
                except (TypeError, ValueError):
                    rank = 10
                opt['type'] = 'bottom' if rule.get("bottom") else 'top'
                opt['value'] = rank
                if rule.get("percent"):
                    opt['criteria'] = '%'
                worksheet.conditional_format(target_range, opt)

            # ── aboveAverage ──────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_ABOVE_AVERAGE:
                opt['type'] = 'average'
                above = rule.get("above_average")
                if above is None:
                    above = not bool(rule.get("below_average", False))
                opt['criteria'] = 'above' if above else 'below'
                worksheet.conditional_format(target_range, opt)

            # ── timePeriod ────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_TIME_PERIOD:
                time_period = str(rule.get("time_period") or rule.get("period") or "").strip()
                if not time_period:
                    continue
                opt['type'] = 'time_period'
                opt['criteria'] = time_period
                worksheet.conditional_format(target_range, opt)

            # ── colorScale ────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_COLOR_SCALE:
                colors = list(rule.get("colors") or [])
                if len(colors) == 2:
                    opt['type'] = '2_color_scale'
                    opt['min_color'] = _normalize_color_for_xlsxwriter(colors[0], "F8696B")
                    opt['max_color'] = _normalize_color_for_xlsxwriter(colors[1], "63BE7B")
                elif len(colors) >= 3:
                    opt['type'] = '3_color_scale'
                    opt['min_color'] = _normalize_color_for_xlsxwriter(colors[0], "F8696B")
                    opt['mid_color'] = _normalize_color_for_xlsxwriter(colors[1], "FFEB84")
                    opt['max_color'] = _normalize_color_for_xlsxwriter(colors[2], "63BE7B")
                else:
                    raise HTTPException(status_code=422, detail=f"CF rule[{index}] colorScale requires 2 or 3 colors")
                worksheet.conditional_format(target_range, opt)

            # ── dataBar ───────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_DATA_BAR:
                opt['type'] = 'data_bar'
                color = rule.get("color")
                if color:
                    opt['bar_color'] = _normalize_color_for_xlsxwriter(color, "638EC6")
                worksheet.conditional_format(target_range, opt)

            # ── iconSet ───────────────────────────────────────────────────
            elif rule_type == EXCEL_RULE_TYPE_ICON_SET:
                icon_style = str(rule.get("icon_style") or "3TrafficLights1")
                opt['type'] = 'icon_set'
                opt['icon_style'] = _normalize_icon_style(icon_style)
                worksheet.conditional_format(target_range, opt)

        except Exception as e:
            logger.warning("Failed to apply xlsxwriter conditional formatting rule %s: %s", index, e)

def _write_excel_xlsxwriter(
    cursor,
    out_path: str,
    *,
    sheet_name: Optional[str],
    excel_options: Optional[dict],
    conditional_formats: Optional[List[dict]],
    fetch_size: int,
    progress_label: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """Writes Excel using xlsxwriter with constant_memory=True to optimize RAM for wide tables.

    Args:
        cursor: Database cursor loaded with rows to write.
        out_path: File system target path where the Excel file is written.
        sheet_name: Friendly worksheet title.
        excel_options: Layout and formatting flags.
        conditional_formats: Formatting rules matching database criteria.
        fetch_size: Chunk size for reading rows.
        progress_label: Custom tag identifying the source in logger statements.
        progress_callback: Action invoked per chunk to notify staging.

    Returns:
        Tuple containing total_rows and n_cols.
    """
    label = progress_label or out_path
    logger.info("_write_excel_xlsxwriter starting | label=%s out_path=%s", label, out_path)

    try:
        import xlsxwriter
    except ImportError:
        raise HTTPException(status_code=500, detail="Excel dependency missing: xlsxwriter is not installed")

    options   = dict(excel_options or {})
    col_names = [d[0] for d in (cursor.description or [])]
    n_cols    = len(col_names)

    # ── resolve options ───────────────────────────────────────────────────────
    auto_width         = bool(options.get("auto_width", True))
    auto_filter        = bool(options.get("auto_filter", True))
    width_sample_limit = max(0, int(options.get("width_sample_rows") or 200))
    default_date_fmt   = str(options.get("date_format")     or "yyyy-mm-dd")
    default_dt_fmt     = str(options.get("datetime_format") or "yyyy-mm-dd hh:mm:ss")
    default_time_fmt   = str(options.get("time_format")     or "hh:mm:ss")

    sheet_header_style = options.get("sheet_header_style") or {}
    header_style       = options.get("header_style")       or {}
    body_style         = options.get("body_style")         or {}

    sheet_header_text    = str(options.get("sheet_header_title") or options.get("sheet_header") or options.get("title") or "").strip()
    sheet_header_enabled = options.get("sheet_header_enabled")
    has_sheet_header_cfg = bool(sheet_header_text or sheet_header_style or options.get("sheet_header_row_span") or options.get("sheet_header_merge_rows"))
    render_sheet_header  = (_excel_option_enabled(sheet_header_enabled) if sheet_header_enabled is not None else has_sheet_header_cfg)

    # ── explicit column format overrides ────────────────────
    explicit_col_formats: List[Optional[str]] = [
        options.get("column_formats", {}).get(str(col).strip())
        or options.get("column_formats", {}).get(str(col).strip().upper())
        for col in col_names
    ]
    col_formats: Optional[List[Optional[str]]] = None

    # ── banding options ─────────────────────────────
    banding_fill_color = _excel_color(body_style.get("banding_fill"))
    banding_font_color = _excel_color(body_style.get("banding_font_color"))
    try:
        banding_frequency = max(1, int(body_style.get("banding_frequency") or 2))
    except (TypeError, ValueError):
        banding_frequency = 2

    # ── width tracking ────────────────────────────────────────────────────────
    widths: List[float] = [_estimate_excel_text_width(name, is_header=True) for name in col_names]

    # ── workbook / sheet ──────────────────────────────────────────────────────
    workbook = xlsxwriter.Workbook(out_path, {'constant_memory': True})
    safe_title = _excel_safe_sheet_name(sheet_name or options.get("sheet_name"))
    ws = workbook.add_worksheet(safe_title)

    # ── build style formats ──────────────────────────────────────────────────
    sh_format = _create_xlsxwriter_format(
        workbook, sheet_header_style, is_header=True,
        default_bg="D9EAF7", default_fg="000000", default_bold=True,
        default_valign="vcenter"
    )
    
    has_header_style = "header_style" in options and options.get("header_style") is not None
    if has_header_style:
        ch_format = _create_xlsxwriter_format(
            workbook, header_style, is_header=False,
            default_bg="1F4E78", default_fg="FFFFFF", default_bold=True,
            default_valign="vcenter"
        )
    else:
        ch_format = None

    sheet_header_row_idx  = None
    column_header_row_idx = 0
    first_data_row        = 1

    # ── write sheet header ───────────────────────────────────────────────────
    if render_sheet_header:
        try:
            row_span = max(1, int(options.get("sheet_header_row_span") or options.get("sheet_header_merge_rows") or 1))
        except (TypeError, ValueError):
            row_span = 1
        header_title = sheet_header_text or safe_title
        
        if n_cols > 1:
            ws.merge_range(0, 0, row_span - 1, n_cols - 1, header_title, sh_format)
        else:
            ws.write(0, 0, header_title, sh_format)
            
        sheet_header_row_idx  = 0
        column_header_row_idx = row_span
        first_data_row        = column_header_row_idx + 1

    # ── write column headers ─────────────────────────────────────────────────
    for col_idx, col_name in enumerate(col_names):
        ws.write(column_header_row_idx, col_idx, col_name, ch_format)

    # Freeze panes
    freeze_header = bool(options.get("freeze_header", True))
    freeze_panes_cfg = options.get("freeze_panes")
    if freeze_panes_cfg:
        import re
        m = re.match(r"([A-Z]+)(\d+)", str(freeze_panes_cfg).upper().strip())
        if m:
            col_letter, row_num = m.groups()
            col_idx = 0
            for char in col_letter:
                col_idx = col_idx * 26 + (ord(char) - ord('A') + 1)
            ws.freeze_panes(int(row_num) - 1, col_idx - 1)
    elif freeze_header:
        ws.freeze_panes(first_data_row, 0)

    # ── row iteration ─────────────────────────────────────────────────────────
    total_rows  = 0
    chunk_index = 0
    timezone_aware_columns: dict = {}
    timezone_aware_samples: dict = {}
    export_started_at = time_module.perf_counter()
    _excel_log_memory(label, prefix="MEMORY BEFORE XLSXWRITER EXPORT")

    max_data_rows = 1048576 - first_data_row

    col_formats_regular = []
    col_formats_banded = []

    for rows in _iter_rows_in_chunks(cursor, fetch_size):
        chunk_index += 1
        chunk_started_at = time_module.perf_counter()
        chunk_len = len(rows)

        # Sniff formats from first row
        if col_formats is None and rows:
            col_formats = _sniff_column_formats(
                rows[0], explicit_col_formats,
                default_date_fmt, default_dt_fmt, default_time_fmt,
            )
            for idx, nf in enumerate(col_formats):
                reg_dict = _xlsxwriter_format_dict(body_style)
                if nf:
                    reg_dict['num_format'] = nf
                col_formats_regular.append(workbook.add_format(reg_dict))
                
                band_dict = _xlsxwriter_format_dict(body_style)
                if banding_fill_color:
                    band_dict['bg_color'] = _normalize_color_for_xlsxwriter(banding_fill_color, banding_fill_color)
                if banding_font_color:
                    band_dict['font_color'] = _normalize_color_for_xlsxwriter(banding_font_color, banding_font_color)
                if nf:
                    band_dict['num_format'] = nf
                col_formats_banded.append(workbook.add_format(band_dict))

        for row in rows:
            if total_rows >= max_data_rows:
                logger.error(
                    "_write_excel_xlsxwriter row limit exceeded | label=%s total_rows=%s max_data_rows=%s",
                    label, total_rows, max_data_rows,
                )
                workbook.close()
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Excel row limit exceeded for export '{label}': "
                        f"source table has more than {max_data_rows:,} data rows. "
                        f"Use CSV, JSON, or Parquet format for large tables."
                    ),
                )

            total_rows += 1
            is_band = bool(banding_fill_color) and ((total_rows - 1) % banding_frequency != 0)

            if total_rows % 5000 == 0:
                _excel_log_memory(label, prefix=f"MEMORY row={total_rows} chunk={chunk_index}")

            current_row = first_data_row + total_rows - 1
            formats_list = col_formats_banded if is_band else col_formats_regular

            for col_idx, value in enumerate(row):
                if value is not None and not isinstance(value, (int, float, str, bool)):
                    if _excel_is_timezone_aware_datetime(value):
                        col_key = col_names[col_idx] if col_idx < n_cols else f"COL_{col_idx + 1}"
                        timezone_aware_columns[col_key] = timezone_aware_columns.get(col_key, 0) + 1
                        if col_key not in timezone_aware_samples:
                            timezone_aware_samples[col_key] = value.isoformat()

                val = _excel_display_value(value)
                
                fmt = formats_list[col_idx] if col_idx < len(formats_list) else None
                
                if val is None:
                    ws.write_blank(current_row, col_idx, "", fmt)
                elif isinstance(val, (datetime, date)):
                    ws.write_datetime(current_row, col_idx, val, fmt)
                else:
                    ws.write(current_row, col_idx, val, fmt)

                if auto_width and total_rows <= width_sample_limit:
                    w = _estimate_excel_text_width("" if val is None else str(val))
                    if w > widths[col_idx]:
                        widths[col_idx] = w

        chunk_elapsed = time_module.perf_counter() - chunk_started_at
        rows_per_sec  = chunk_len / max(chunk_elapsed, 0.001)
        logger.info(
            "_write_excel_xlsxwriter chunk done | label=%s chunk=%s rows_in_chunk=%s total=%s elapsed=%.3fs rows_per_sec=%.0f",
            label, chunk_index, chunk_len, total_rows, chunk_elapsed, rows_per_sec,
        )
        if progress_callback is not None:
            progress_callback(chunk_len, total_rows)

    # ── post-write configuration ─────────────────────────────────────────────
    if auto_width:
        for idx, w in enumerate(widths):
            ws.set_column(idx, idx, min(max(w, 10), 60))

    for hidden_col in options.get("hidden_columns") or []:
        col_upper = str(hidden_col or "").strip().upper()
        if not col_upper:
            continue
        for idx, name in enumerate(col_names):
            if name.strip().upper() == col_upper:
                ws.set_column(idx, idx, None, None, {'hidden': True})
                break

    if auto_filter and col_names:
        ws.autofilter(column_header_row_idx, 0, first_data_row + total_rows - 1, n_cols - 1)

    _apply_xlsxwriter_conditional_formatting(
        workbook, ws, list(conditional_formats or []), col_names,
        first_data_row=first_data_row + 1,
        last_data_row=first_data_row + total_rows,
    )

    write_elapsed = time_module.perf_counter() - export_started_at
    _excel_log_memory(label, prefix="MEMORY BEFORE SAVE")
    logger.info("_write_excel_xlsxwriter saving | label=%s rows=%s cols=%s write_elapsed=%.3fs", label, total_rows, n_cols, write_elapsed)

    try:
        workbook.close()
    except Exception:
        logger.exception("_write_excel_xlsxwriter save failed | label=%s rows=%s", label, total_rows)
        raise

    try:
        file_size_bytes = os.path.getsize(out_path)
        file_size_mb    = file_size_bytes / (1024 * 1024)
    except OSError:
        file_size_bytes = 0
        file_size_mb    = 0.0

    total_elapsed = time_module.perf_counter() - export_started_at
    logger.info(
        "_write_excel_xlsxwriter complete | label=%s rows=%s cols=%s "
        "file_size_mb=%.2f write_elapsed=%.3fs total_elapsed=%.3fs rows_per_sec=%.0f",
        label, total_rows, n_cols, file_size_mb,
        write_elapsed, total_elapsed,
        total_rows / max(write_elapsed, 0.001),
    )

    if timezone_aware_columns:
        logger.info("_write_excel tz-aware columns | label=%s columns=%s samples=%s", label, timezone_aware_columns, timezone_aware_samples)
        print(f"_write_excel tz-aware columns | label={label} columns={timezone_aware_columns} samples={timezone_aware_samples}")

    import gc
    gc.collect()

    return total_rows, n_cols


def check_excel_limits_preflight(
    sf_conn,
    query: str,
    table_name: str,
) -> None:
    """Pre-flight check to fail early if Excel row or column count is exceeded.

    Queries Snowflake cursor metadata and row count to prevent OOM/file errors.
    Raises HTTPException (422) if limits are exceeded.
    """
    # ── column check (free: LIMIT 0 populates description instantly) ──
    with sf_conn.cursor() as col_cur:
        try:
            col_cur.execute(f"SELECT * FROM ({query}) LIMIT 0")
            col_count_preflight = len(col_cur.description) if col_cur.description else 0
        except Exception as e:
            logger.warning(
                "Excel column pre-flight query failed (skipping check) | table=%s error=%s",
                table_name, e,
            )
            col_count_preflight = 0

    if col_count_preflight > 16384:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Table {table_name!r} has {col_count_preflight:,} columns, "
                f"exceeding the Excel limit of 16,384. "
                "Use CSV, JSON, or Parquet format instead."
            ),
        )

    # ── row check (one COUNT(*) query, no chunk fetching) ─────────
    with sf_conn.cursor() as count_cur:
        try:
            count_cur.execute(f"SELECT COUNT(*) FROM ({query})")
            row_count_preflight = count_cur.fetchone()[0]
        except Exception as e:
            logger.warning(
                "Excel row count pre-flight query failed (skipping check) | table=%s error=%s",
                table_name, e,
            )
            row_count_preflight = None

    if row_count_preflight is not None and row_count_preflight > 1048575:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Table {table_name!r} has {row_count_preflight:,} rows, "
                f"exceeding the Excel data row limit of 1,048,575. "
                "Use CSV, JSON, or Parquet format instead."
            ),
        )

    logger.info(
        "Excel pre-flight passed | table=%s rows=%s cols=%s",
        table_name,
        row_count_preflight if row_count_preflight is not None else "unchecked",
        col_count_preflight or "unchecked",
    )
