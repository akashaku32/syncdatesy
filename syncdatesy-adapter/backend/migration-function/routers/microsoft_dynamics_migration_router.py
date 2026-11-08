# microsoft_dynamics_migration_router.py
from fastapi import APIRouter
from services.dynamics.as_source import to_snowflake as microsoft_dynamics_migration_service
from models.microsoft_dynamics_models import MigrationRequest
import logging
import traceback
from datetime import datetime
from fastapi import APIRouter, HTTPException
from utils.error_utils import log_migration_failure

router = APIRouter()

@router.post("/migrate")
async def migrate_data(request: MigrationRequest):
    """
    Handles the migration of data based on the provided MigrationRequest.
    """
    # Calls the service to handle the migration
    start_time = datetime.now()
    try:
        result = await microsoft_dynamics_migration_service.migrate(request)
        return result
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(request, 'job_name', 'Microsoft Dynamics Migration'),
                'log_id': getattr(request, 'log_id', None),
                'cmp_id': getattr(request, 'cmp_id', None),
                'connection_id': getattr(request, 'connection_id', None),
                'user_id': getattr(request, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'successful_tables': 0,
                'total_tables': len(request.selected_tables) if request.selected_tables else 0,
                'total_rows': 0,
                'service_name': 'Microsoft Dynamics 365 FO'
            }
        )
        raise e
