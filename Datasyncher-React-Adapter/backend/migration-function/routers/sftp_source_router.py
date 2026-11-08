"""FastAPI routes for SFTP source listing and schema discovery."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from models.sftp_source_models import SFTPListingTables, SFTPPatternPreviewRequest, SFTPPKSuggestionRequest
from services.sftp.source import SFTPSourceService
from models.amazon_s3_models import LoadMethod
from utils.error_utils import handle_update_schema_listing_error

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/SFTP-listing-files")
async def list_files(data: SFTPListingTables):
    """List SFTP files under the configured source root."""
    try:
        return {"result": SFTPSourceService.list_files(data)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("SFTP file listing failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/SFTP-listing-tables")
async def list_tables(data: SFTPListingTables):
    """List SFTP files and infer a schema sample for each table."""
    try:
        result = SFTPSourceService.list_tables_with_schema(data)
        try:
            db_result = SFTPSourceService.refresh_and_update_sftp_tables_in_database(
                connection_id=data.connection_id,
                tables_result=result,
                job_name=getattr(data, "job_name", None),
                migration_session_id=getattr(data, "migration_session_id", None),
            )
            result["tables_written"] = db_result.get("tables_written", 0)
            result["tables_deleted"] = db_result.get("tables_deleted", 0)
        except Exception as db_exc:
            logger.exception("SFTP table persistence failed for connection_id=%s: %s", data.connection_id, db_exc)
            handle_update_schema_listing_error(
                db_exc,
                data,
                service_name="SFTP",
                total_tables=len(result.get("tables", [])),
            )
            raise HTTPException(status_code=422, detail=f"Update schema failed while writing SFTP tables to database: {db_exc}")
        return {"result": result}
    except HTTPException as http_error:
        handle_update_schema_listing_error(
            http_error,
            data,
            service_name="SFTP",
        )
        raise http_error
    except Exception as exc:
        logger.exception("SFTP table listing failed: %s", exc)
        handle_update_schema_listing_error(
            exc,
            data,
            service_name="SFTP",
        )
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/SFTP-preview-pattern")
async def preview_pattern(data: SFTPPatternPreviewRequest):
    """Preview which SFTP files match the configured multi-file pattern."""
    try:
        return SFTPSourceService.preview_pattern(data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("SFTP pattern preview failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/SFTP-suggest-primary-keys")
async def suggest_primary_keys(data: SFTPPKSuggestionRequest):
    """
    Analyze SFTP file columns and return column statistics
    so the user can select custom primary keys.
    """
    try:
        if data.sftp.load_method != LoadMethod.upsert_custom_key:
            raise HTTPException(
                status_code=422,
                detail="Primary key suggestion is only supported for load_method = upsert_custom_key"
            )
        result = await SFTPSourceService.suggest_primary_keys(data)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("SFTP PK suggestion failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


