import logging
import time

from fastapi import APIRouter, HTTPException

from models.snowflake_sharepoint_models import SnowflakeSharePointMigrationRequest
from services.snowflake.as_source.to_sharepoint import migrate_snowflake_to_sharepoint

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/Snowflake-SharePoint-migrate")
async def snowflake_sharepoint_migrate(request: SnowflakeSharePointMigrationRequest):
    """
    Snowflake (source) -> SharePoint (destination) migration endpoint.

    Uses the same status/logging/websocket patterns as the Snowflake->SFTP route.
    """
    start_time = time.time()
    try:
        return migrate_snowflake_to_sharepoint(request)
    except HTTPException:
        raise
    except Exception as e:
        from utils.error_utils import handle_service_error

        handle_service_error(
            error=e,
            context={
                "job_name": getattr(request, "job_name", "Snowflake-SharePoint Migration"),
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
                "service_name": "Snowflake-SharePoint",
            },
        )
        logger.exception("Snowflake-SharePoint migration failed: %s", e)
        raise HTTPException(status_code=422, detail=str(e))
