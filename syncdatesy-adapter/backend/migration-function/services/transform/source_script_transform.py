"""Generic source-side Python transforms.

This module contains no SAP mapping or destination-specific logic. It reuses
the existing script validator/runner and adapts a DB cursor to the interface
expected by the existing file writers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from services.transform.script_runner import (
    ScriptRuntimeError,
    effective_script_text,
    run_transform,
)
from services.transform.script_validator import validate_script_ast

logger = logging.getLogger(__name__)


def get_active_source_script(connection_id: int, table_name: str) -> Optional[Dict[str, Any]]:
    """Load only script fields from the existing transform table.

    The SAP state loader also returns mapping/destination fields and prints its
    full record for diagnostics. Source-only execution needs neither behavior,
    so this narrow loader avoids coupling the new path to SAP state handling or
    emitting user script text to logs.
    """
    from utils.database_utils import postgres_connection

    connection = None
    try:
        connection = postgres_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT version, script_text, entry_point, script_sha256
                FROM entity_transform
                WHERE connection_id = %s
                  AND table_name = %s
                  AND is_active = TRUE
                ORDER BY version DESC
                LIMIT 1
                """,
                (connection_id, table_name),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "version": row[0],
            "script_text": row[1],
            "entry_point": row[2] or "",
            "script_sha256": row[3],
        }
    except Exception as exc:
        # A missing transform table or unavailable state database means no
        # source script; the existing export path must remain usable.
        logger.warning(
            "Unable to load active source script state | connection_id=%s table=%s error=%s",
            connection_id,
            table_name,
            exc,
        )
        return None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.warning(
                    "Unable to close source script state connection | connection_id=%s table=%s",
                    connection_id,
                    table_name,
                    exc_info=True,
                )


def validate_source_script(script_text: str, entry_point: str = "") -> Dict[str, Any]:
    """Validate syntax, imports, banned calls, size, and entry point only."""
    text = str(script_text or "")
    checklist, detected = validate_script_ast(text)
    errors = [
        {
            "path": f"script:{item.get('line', 0)}",
            "message": item.get("message") or item.get("label"),
        }
        for item in checklist
        if not item.get("passed")
    ]
    if entry_point and not errors and entry_point not in detected:
        errors.append(
            {
                "path": "entry_point",
                "message": f"Entry point '{entry_point}' was not detected.",
            }
        )
    warnings = []
    if text.strip():
        warnings.append(
            {
                "type": "source_dtypes",
                "message": (
                    "Source values are passed to the script with object dtype "
                    "to preserve NULLs. Cast explicitly with pd.to_numeric(), "
                    ".astype(), or the appropriate pandas accessor when needed."
                ),
            }
        )
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checklist": checklist,
        "entry_points": detected or ([entry_point] if entry_point else []),
    }


def run_source_script(
    frame: pd.DataFrame,
    script_text: Optional[str],
    entry_point: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Run one source batch and require a pandas DataFrame result."""
    effective = effective_script_text(script_text)
    if not effective:
        return frame, {"entry_point": None, "script_applied": False}
    result, metadata = run_transform(effective, frame, entry_point or None)
    metadata["script_applied"] = True
    return result, metadata


def dataframe_from_rows(
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
) -> pd.DataFrame:
    """Build a frame without pandas coercing nullable source values.

    The script boundary should not silently turn a Snowflake integer plus NULL
    into floating-point values. Object dtype preserves the DB driver's Python
    values; scripts can explicitly cast when numeric pandas dtypes are needed.
    """
    return pd.DataFrame(
        [tuple(row) for row in rows],
        columns=list(columns),
        dtype=object,
    )


class TransformingCursor:
    """Expose transformed DataFrame batches through a DB-API-like cursor.

    Existing CSV, JSON, Parquet, and Excel writers consume ``description`` and
    ``fetchmany``. Keeping that contract lets the script path reuse them while
    leaving the no-script path untouched.
    """

    def __init__(
        self,
        cursor: Any,
        *,
        fetch_size: int,
        script_text: str,
        entry_point: str = "",
    ) -> None:
        self._cursor = cursor
        self._fetch_size = max(1, int(fetch_size))
        self._script_text = script_text
        self._entry_point = entry_point or None
        self._pending: List[Tuple[Any, ...]] = []
        self._finished = False
        self._output_columns: Optional[List[str]] = None
        self._metadata: Dict[str, Any] = {
            "entry_point": None,
            "script_applied": False,
        }
        # Writers read ``description`` immediately after construction. Prime
        # until the first transformed frame establishes the output schema;
        # fetchmany() continues through later empty batches when necessary.
        while self._output_columns is None and not self._finished:
            self._fill_pending()

    @property
    def description(self) -> Sequence[Tuple[str, ...]]:
        columns = self._output_columns
        if columns is None:
            columns = [item[0] for item in (self._cursor.description or [])]
        return [(column,) for column in columns]

    def _fill_pending(self) -> None:
        if self._finished or self._pending:
            return
        rows = self._cursor.fetchmany(self._fetch_size)
        if not rows:
            # There is no data from which to establish a script output schema.
            # Run once on an empty frame so header-only exports still reflect
            # renames, drops, and added columns made by the script. A script
            # that requires an actual row will raise a useful runtime error.
            if self._output_columns is None:
                source_columns = [
                    item[0] for item in (self._cursor.description or [])
                ]
                empty_frame = pd.DataFrame(columns=source_columns, dtype=object)
                transformed, self._metadata = run_source_script(
                    empty_frame,
                    self._script_text,
                    self._entry_point,
                )
                self._output_columns = [
                    str(column) for column in transformed.columns
                ]
            self._finished = True
            return

        source_columns = [item[0] for item in (self._cursor.description or [])]
        frame = dataframe_from_rows(rows, source_columns)
        transformed, self._metadata = run_source_script(
            frame, self._script_text, self._entry_point
        )
        output_columns = [str(column) for column in transformed.columns]
        if self._output_columns is None:
            self._output_columns = output_columns
        elif output_columns != self._output_columns:
            raise ScriptRuntimeError(
                "Script output columns changed between batches; "
                "return a consistent DataFrame schema."
            )
        self._pending = list(transformed.itertuples(index=False, name=None))

    @property
    def metadata(self) -> Dict[str, Any]:
        """Return metadata from the most recently executed script batch."""
        return dict(self._metadata)

    def fetchmany(self, size: Optional[int] = None) -> List[Tuple[Any, ...]]:
        requested = max(1, int(size or self._fetch_size))
        # A script may filter every row from a source batch. Keep fetching until
        # a transformed batch has rows or the source cursor is exhausted; an
        # empty transformed batch is not the same as end-of-file.
        while not self._pending and not self._finished:
            self._fill_pending()
        result = self._pending[:requested]
        del self._pending[:requested]
        return result

    def close(self) -> None:
        self._cursor.close()
