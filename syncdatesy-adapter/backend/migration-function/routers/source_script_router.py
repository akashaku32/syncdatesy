"""Generic source-script validation and Snowflake dry-run endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from services.snowflake.as_source.to_sftp import (
    _build_snowflake_select_query,
    _extract_snowflake_schema,
)
from services.snowflake.service import SnowflakeService
from services.transform.script_runner import ScriptRuntimeError
from services.transform.source_script_transform import (
    TransformingCursor,
    dataframe_from_rows,
    validate_source_script,
)

router = APIRouter(prefix="/source-script", tags=["source-script"])
logger = logging.getLogger(__name__)


class SampleCursor:
    """Small DB-API-compatible cursor used to dry-run fetched sample rows."""

    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]):
        self.description = [(column,) for column in columns]
        self._rows = list(rows)

    def fetchmany(self, size: Optional[int] = None):
        requested = len(self._rows) if size is None else max(1, int(size))
        result, self._rows = self._rows[:requested], self._rows[requested:]
        return result

    def close(self):
        return None


class SourceScriptValidateRequest(BaseModel):
    script_text: str = ""
    entry_point: str = ""


class SnowflakeScriptDryRunRequest(SourceScriptValidateRequest):
    source_config: Dict[str, Any] = Field(default_factory=dict)
    table_name: str
    selected_fields: Optional[list[str]] = None
    sort_columns: Optional[list[Any]] = None
    sample_size: int = 100


@router.post("/validate")
def validate_source_script_route(request: SourceScriptValidateRequest):
    """Validate only; this endpoint never connects to a source or executes code."""
    return validate_source_script(request.script_text, request.entry_point)


@router.post("/snowflake/dry-run")
def dry_run_snowflake_script(request: SnowflakeScriptDryRunRequest):
    """Execute the script against a bounded Snowflake sample without uploading."""
    validation = validate_source_script(request.script_text, request.entry_point)
    if not validation["valid"]:
        return {
            **validation,
            "rows_in": 0,
            "rows_out": 0,
            "input_columns": [],
            "output_columns": [],
            "preview_rows": [],
        }

    limit = max(1, min(int(request.sample_size or 100), 1000))
    service = SnowflakeService(
        request.source_config,
        destination_schema=None,
        validate_destination_schema=False,
    )
    connection = None
    try:
        connection = service.create_connection()
        schema_name = _extract_snowflake_schema(request.source_config)
        if schema_name:
            # create_connection selects the database, but not the source schema.
            # The migration export path does the same before issuing its table
            # query; mirror it here so unqualified table names resolve against
            # the configured Snowflake source schema.
            with connection.cursor() as schema_cursor:
                schema_cursor.execute(f"USE SCHEMA {schema_name}")
        base_query = _build_snowflake_select_query(
            request.table_name,
            request.selected_fields or [],
            request.sort_columns,
        )
        # The query builder produces a trusted internal SELECT. Appending LIMIT
        # after ORDER BY preserves the same top-N ordering as the real export
        # while keeping dry-run bounded at the database.
        query = f"{base_query.rstrip(';')} LIMIT {limit}"
        with connection.cursor() as cursor:
            cursor.execute(query)
            columns = [item[0] for item in (cursor.description or [])]
            rows = cursor.fetchmany(limit)
        # Run the sample through the same cursor adapter used by migration so
        # empty batches and changing output schemas are exercised in dry-run.
        # Use multiple batches whenever the sample has more than one row so
        # dry-run can detect scripts that change their output schema by batch.
        sample_batch_size = max(1, min(100, (limit // 2) or 1))
        transformed_cursor = TransformingCursor(
            SampleCursor(columns, rows),
            fetch_size=sample_batch_size,
            script_text=request.script_text,
            entry_point=request.entry_point,
        )
        output_columns = [item[0] for item in transformed_cursor.description]
        output_rows = []
        while True:
            batch = transformed_cursor.fetchmany(sample_batch_size)
            if not batch:
                break
            output_rows.extend(batch)
        output_frame = dataframe_from_rows(output_rows, output_columns)
        preview_rows = output_frame.head(20).where(
            pd.notna(output_frame), None
        ).to_dict("records")
        return {
            **validation,
            "rows_in": len(rows),
            "rows_out": len(output_frame),
            "input_columns": list(columns),
            # Match the cursor adapter and file-writer header representation.
            "output_columns": [str(column) for column in output_frame.columns],
            "preview_rows": jsonable_encoder(preview_rows),
            "metadata": transformed_cursor.metadata,
        }
    except ScriptRuntimeError as exc:
        logger.warning("Source script dry-run failed | table=%s error=%s", request.table_name, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Snowflake source script dry-run failed | table=%s",
            request.table_name,
        )
        raise HTTPException(
            status_code=422,
            detail="Snowflake script dry-run failed. Check the server logs for details.",
        ) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.warning(
                    "Unable to close Snowflake dry-run connection | table=%s",
                    request.table_name,
                    exc_info=True,
                )
