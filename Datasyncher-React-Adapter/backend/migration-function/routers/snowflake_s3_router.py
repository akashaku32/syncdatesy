import logging
import time

from fastapi import APIRouter, HTTPException

from models.snowflake_s3_models import SnowflakeS3MigrationRequest
from services.snowflake.as_source.to_s3 import migrate_snowflake_to_s3

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/Snowflake-S3-migrate")
async def snowflake_s3_migrate(request: SnowflakeS3MigrationRequest):
    """Snowflake source to Amazon S3 destination migration endpoint."""
    start_time = time.time()
    try:
        logger.info(
            "Starting Snowflake->S3 migration | connection_id=%s | tables=%s",
            request.connection_id,
            len(request.selected_tables or []),
        )
        return migrate_snowflake_to_s3(request)
    except HTTPException:
        raise
    except Exception as exc:
        from utils.error_utils import handle_service_error

        handle_service_error(
            error=exc,
            context={
                "job_name": getattr(request, "job_name", "Snowflake-S3 Migration"),
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
                "service_name": "Snowflake-S3",
            },
        )
        logger.exception("Snowflake->S3 migration failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
