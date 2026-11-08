"""Compare inferred business columns without treating type widths as mismatches."""

import re


METADATA_COLUMNS = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED"}


def type_family(data_type):
    name = str(data_type or "").upper().split("(", 1)[0].strip()
    if name in {"NUMBER", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "INT", "INTEGER", "BIGINT"}:
        return "numeric"
    if name.startswith("TIMESTAMP") or name == "DATE":
        return "datetime"
    if name in {"VARCHAR", "TEXT", "STRING", "CHAR"}:
        return "text"
    return name.lower()


def schema_mismatch(expected, actual, *, enforce_types=True, allowed_new_columns=None):
    """Return a readable mismatch, or None. None allows additional columns."""
    def column_name(name):
        cleaned = re.sub(r"[^A-Za-z0-9_]", "", str(name).strip().replace(" ", "_"))
        if cleaned and not cleaned[0].isalpha() and cleaned[0] != "_":
            cleaned = "_" + cleaned
        return cleaned.upper()

    def normalize(columns):
        return {
            column_name(name): meta
            for name, meta in columns.items()
            if str(name).upper() not in METADATA_COLUMNS
        }

    expected, actual = normalize(expected), normalize(actual)
    missing = set(expected) - set(actual)
    if missing:
        return "missing required columns: " + ", ".join(sorted(missing))
    extra = set(actual) - set(expected)
    if allowed_new_columns is not None:
        extra -= set(allowed_new_columns)
        if extra:
            return "unexpected columns: " + ", ".join(sorted(extra))
    if enforce_types:
        for name in sorted(set(expected) & set(actual)):
            # Custom keys are stored as VARCHAR regardless of their source type.
            if expected[name].get("is_primary_key"):
                continue
            expected_type = expected[name].get("data_type")
            actual_type = actual[name].get("data_type")
            if expected_type and actual_type and type_family(expected_type) != type_family(actual_type):
                return f"column '{name}' has type {actual_type}; expected {expected_type}"
    return None
