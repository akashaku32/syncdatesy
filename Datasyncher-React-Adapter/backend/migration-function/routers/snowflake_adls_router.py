import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException

from models.snowflake_adls_models import ADLSAuthorizationRequest, SnowflakeADLSMigrationRequest
from services.snowflake.as_source.to_adls import (
    migrate_snowflake_to_adls,
    test_adls_connection,
    validate_adls_authorization,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _format_adls_error_body(detail):
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if isinstance(item, dict):
                loc = item.get("loc") or []
                field = ".".join(str(part) for part in loc if part not in ("body", "__root__"))
                msg = item.get("msg") or item.get("message") or str(item)
                messages.append(f"{field}: {msg}" if field else str(msg))
            else:
                messages.append(str(item))
        return "; ".join(messages)

    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)

    return str(detail or "ADLS destination configuration validation failed.")


async def _authorize_adls_destination(request: ADLSAuthorizationRequest):
    """
    Validate ADLS destination creation/update payloads from Django.
    """
    try:
        validate_adls_authorization(request)
        details = await asyncio.to_thread(test_adls_connection, request)
    except HTTPException as exc:
        error_body = _format_adls_error_body(exc.detail)
        logger.warning(
            "ADLS auth router: validation failed | status=%s detail=%s",
            exc.status_code,
            error_body,
        )
        return {
            "status_code": exc.status_code or 422,
            "body": error_body,
        }
    except Exception as exc:
        error_body = _format_adls_error_body(exc)
        logger.exception("ADLS auth router: unexpected validation failure | detail=%s", error_body)
        return {
            "status_code": 422,
            "body": error_body,
        }

    logger.info(
        "ADLS auth router: validation succeeded | account=%s container=%s",
        details.get("account_name"),
        details.get("container_name"),
    )

    return {
        "status_code": 200,
        "body": "ADLS destination configuration validated successfully.",
        "details": details,
    }


@router.post("/AzureDataLakeStorage-Authorization")
@router.post("/api-new/AzureDataLakeStorage-Authorization")
async def authorize_azure_data_lake_storage(request: ADLSAuthorizationRequest):
    return await _authorize_adls_destination(request)


@router.post("/ADLS-Authorization")
@router.post("/api-new/ADLS-Authorization")
async def authorize_adls(request: ADLSAuthorizationRequest):
    return await _authorize_adls_destination(request)


@router.post("/Snowflake-ADLS-migrate")
@router.post("/api-new/Snowflake-ADLS-migrate")
async def snowflake_adls_migrate(request: SnowflakeADLSMigrationRequest):
    """Snowflake source to ADLS Gen2 destination migration endpoint."""
    start_time = time.time()
    try:
        logger.info(
            "Starting Snowflake->ADLS migration | connection_id=%s | tables=%s",
            request.connection_id,
            len(request.selected_tables or []),
        )
        return migrate_snowflake_to_adls(request)
    except HTTPException:
        raise
    except Exception as exc:
        from utils.error_utils import handle_service_error

        handle_service_error(
            error=exc,
            context={
                "job_name": getattr(request, "job_name", "Snowflake-ADLS Migration"),
                "log_id": getattr(request, "log_id", None),
                "cmp_id": getattr(request, "cmp_id", None),
                "connection_id": getattr(request, "connection_id", None),
                "user_id": getattr(request, "user_id", None),
                "operation": "MIGRATION",
                "is_terminal_failure": True,
                "execution_time": time.time() - start_time,
                "total_tables": len(getattr(request, "selected_tables", []) or []),
                "successful_tables": 0,
                "total_rows": 0,
                "service_name": "Snowflake-ADLS",
            },
        )
        logger.exception("Snowflake->ADLS migration failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
