"""FastAPI routes for ADLS source preview and schema helpers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from models.adls_source_models import (
    ADLSListingTables,
    ADLSPKSuggestionRequest,
    ADLSPatternPreviewRequest,
    ADLSSnowflakeMigrationRequest,
)
from models.amazon_s3_models import LoadMethod
from services.adls.source import ADLSSourceService
from services.adls.as_source.to_snowflake import ADLSSnowflakeService
from utils.error_utils import handle_update_schema_listing_error

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/AzureDataLakeStorage-listing-files")
@router.post("/ADLS-listing-files")
async def list_files(data: ADLSListingTables):
    """List ADLS files under the configured source root."""
    try:
        return {"result": ADLSSourceService.list_files(data)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ADLS file listing failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/AzureDataLakeStorage-listing-tables")
@router.post("/ADLS-listing-tables")
async def list_tables(data: ADLSListingTables):
    """List ADLS files and infer a schema sample for each table."""
    try:
        result = ADLSSourceService.list_tables_with_schema(data)
        try:
            db_result = ADLSSourceService.refresh_and_update_adls_tables_in_database(
                connection_id=data.connection_id,
                tables_result=result,
                job_name=getattr(data, "job_name", None),
                migration_session_id=getattr(data, "migration_session_id", None),
            )
            result["tables_written"] = db_result.get("tables_written", 0)
            result["tables_deleted"] = db_result.get("tables_deleted", 0)
        except Exception as db_exc:
            logger.exception("ADLS table persistence failed for connection_id=%s: %s", data.connection_id, db_exc)
            handle_update_schema_listing_error(
                db_exc,
                data,
                service_name="Azure Data Lake Storage",
                total_tables=len(result.get("tables", [])),
            )
            raise HTTPException(status_code=422, detail=f"Update schema failed while writing ADLS tables to database: {db_exc}")
        return {"result": result}
    except HTTPException as http_error:
        handle_update_schema_listing_error(
            http_error,
            data,
            service_name="Azure Data Lake Storage",
        )
        raise http_error
    except Exception as exc:
        logger.exception("ADLS table listing failed: %s", exc)
        handle_update_schema_listing_error(
            exc,
            data,
            service_name="Azure Data Lake Storage",
        )
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/AzureDataLakeStorage-preview-pattern")
@router.post("/ADLS-preview-pattern")
async def preview_pattern(data: ADLSPatternPreviewRequest):
    """Preview ADLS files in the configured folder scope."""
    try:
        return ADLSSourceService.preview_pattern(data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ADLS pattern preview failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/AzureDataLakeStorage-suggest-primary-keys")
@router.post("/ADLS-suggest-primary-keys")
async def suggest_primary_keys(data: ADLSPKSuggestionRequest):
    """Analyze ADLS file columns and return primary-key suggestion metrics."""
    try:
        if data.source is None:
            raise HTTPException(status_code=422, detail="ADLS config is required")
        if data.source.load_method != LoadMethod.upsert_custom_key:
            raise HTTPException(
                status_code=422,
                detail="Primary key suggestion is only supported for load_method = upsert_custom_key",
            )
        return await ADLSSourceService.suggest_primary_keys(data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ADLS PK suggestion failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/AzureDataLakeStorage-Snowflake-migrate")
@router.post("/ADLS-Snowflake-migrate")
async def migrate_adls_to_snowflake(request: ADLSSnowflakeMigrationRequest):
    """Migrate files from ADLS source to Snowflake."""
    try:
        payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
        source_config = dict(payload.get("source_config") or {})
        account_name = source_config.get("account_name") or source_config.get("storage_account_name")
        container_name = (
            source_config.get("container_name")
            or source_config.get("file_system")
            or source_config.get("filesystem")
        )
        source_config.update(
            {
                "_file_source_type": "adls",
                "account_name": account_name,
                "storage_account_name": account_name,
                "container_name": container_name,
                "file_system": container_name,
                "folder_name": (
                    source_config.get("folder_name")
                    or source_config.get("root_folder")
                    or source_config.get("folder_path")
                    or source_config.get("base_folder_path")
                    or source_config.get("adls_base_prefix")
                    or ""
                ),
            }
        )
        for legacy_path_key in ("root_folder", "folder_path", "base_folder_path", "adls_base_prefix"):
            source_config.pop(legacy_path_key, None)
        payload["source_config"] = source_config
        migration_request = ADLSSnowflakeMigrationRequest(**payload)
        return await ADLSSnowflakeService.migrate(migration_request)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ADLS to Snowflake migration failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"ADLS to Snowflake migration failed: {exc}")

