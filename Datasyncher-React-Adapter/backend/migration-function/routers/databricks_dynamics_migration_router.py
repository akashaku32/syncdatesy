"""HTTP migrate entrypoint for Dynamics F&O → Databricks (Celery HTTP fallback)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from models.microsoft_dynamics_models import MigrationRequest
from services.dynamics.as_source import to_databricks as microsoft_dynamics_to_databricks_migration_service
from utils.error_utils import handle_service_error

router = APIRouter()


@router.post("/Databricks-MicrosoftDynamics365_FO-migrate")
async def migrate_dynamics_to_databricks(request: MigrationRequest):
    try:
        return await microsoft_dynamics_to_databricks_migration_service.migrate(request)
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(request, "job_name", "Dynamics to Databricks Migration"),
                "log_id": getattr(request, "log_id", None),
                "cmp_id": getattr(request, "cmp_id", None),
                "connection_id": getattr(request, "connection_id", None),
                "user_id": getattr(request, "user_id", None),
                "operation": "MIGRATION",
                "is_terminal_failure": True,
                "successful_tables": 0,
                "total_tables": len(request.selected_tables) if request.selected_tables else 0,
                "total_rows": 0,
                "service_name": "Microsoft Dynamics 365 FO to Databricks",
                "started_at": datetime.utcnow().isoformat(),
            },
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
