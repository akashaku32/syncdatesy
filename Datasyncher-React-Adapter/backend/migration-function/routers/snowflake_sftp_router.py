import logging
import time

from fastapi import APIRouter, HTTPException

from models.snowflake_sftp_models import SnowflakeSFTPMigrationRequest
from services.snowflake.as_source.to_sftp import migrate_snowflake_to_sftp

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/Snowflake-SFTP-migrate")
async def snowflake_sftp_migrate(request: SnowflakeSFTPMigrationRequest):
    """
    Snowflake (source) -> SFTP (destination) migration endpoint.

    Uses the same status/logging/websocket patterns as other migration-function routes:
    - Per-table status updates via utils.database_utils.update_table_status_snowflake
    - DataTransfer/Log writes via utils.database_utils.data_transfer_logs
    - UI websocket pushes via utils.websocket_utils triggers
    """
    start_time = time.time()
    try:
        return migrate_snowflake_to_sftp(request)
    except HTTPException:
        raise
    except Exception as e:
        # Keep consistent error handling with other routers.
        from utils.error_utils import handle_service_error

        handle_service_error(
            error=e,
            context={
                "job_name": getattr(request, "job_name", "Snowflake-SFTP Migration"),
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
                "service_name": "Snowflake-SFTP",
            },
        )
        logger.exception("Snowflake-SFTP migration failed: %s", e)
        raise HTTPException(status_code=422, detail=str(e))

