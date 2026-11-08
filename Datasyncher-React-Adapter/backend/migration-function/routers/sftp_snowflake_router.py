# routers/sftp_snowflake_router.py
from fastapi import APIRouter, HTTPException
from models.sftp_snowflake_models import SFTPSnowflakeMigrationRequest
from services.sftp.as_source.to_snowflake import SFTPSnowflakeService
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/SFTP-Snowflake-migrate")
async def migrate(request: SFTPSnowflakeMigrationRequest):
    """
    Migrate data from SFTP to Snowflake.
    """
    logger.info(
        f"SFTP migration started - connection_id={request.connection_id}, "
        f"session_id={request.migration_session_id}, "
        f"files={len(request.selected_tables)}"
    )
    try:
        result = await SFTPSnowflakeService.migrate(request)
        return result
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(request, 'job_name', 'SFTP Migration'),
                'log_id': getattr(request, 'log_id', None),
                'cmp_id': getattr(request, 'cmp_id', None),
                'connection_id': getattr(request, 'connection_id', None),
                'user_id': getattr(request, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'successful_tables': 0,
                'total_tables': len(request.selected_tables) if request.selected_tables else 0,
                'total_rows': 0,
                'service_name': 'SFTP'
            }
        )
        raise HTTPException(
            status_code=422,
            detail=f"SFTP migration failed: {str(e)}"
        )


