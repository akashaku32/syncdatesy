"""HTTP migrate entrypoints for Salesforce → Databricks (Celery HTTP fallback)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from models.salesforce_models import SalesforceDatabricksMigration
from services.salesforce.as_source import to_databricks as salesforce_to_databricks_migration_service
from utils.error_utils import handle_service_error

router = APIRouter()


@router.post("/Databricks-Salesforce-migrate")
async def migrate_salesforce_to_databricks(request: SalesforceDatabricksMigration):
    try:
        return await salesforce_to_databricks_migration_service.sync_salesforce_to_databricks(
            request, "production"
        )
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(request, "job_name", "Salesforce to Databricks Migration"),
                "log_id": getattr(request, "log_id", None),
                "cmp_id": getattr(request, "cmp_id", None),
                "connection_id": getattr(request, "connection_id", None),
                "user_id": getattr(request, "user_id", None),
                "operation": "MIGRATION",
                "is_terminal_failure": True,
                "successful_tables": 0,
                "total_tables": len(request.selected_tables) if request.selected_tables else 0,
                "total_rows": 0,
                "service_name": "Salesforce to Databricks",
                "started_at": datetime.utcnow().isoformat(),
            },
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/Databricks-SalesforceSandbox-migrate")
async def migrate_salesforce_sandbox_to_databricks(request: SalesforceDatabricksMigration):
    try:
        return await salesforce_to_databricks_migration_service.sync_salesforce_to_databricks(
            request, "sandbox"
        )
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(
                    request, "job_name", "Salesforce Sandbox to Databricks Migration"
                ),
                "log_id": getattr(request, "log_id", None),
                "cmp_id": getattr(request, "cmp_id", None),
                "connection_id": getattr(request, "connection_id", None),
                "user_id": getattr(request, "user_id", None),
                "operation": "MIGRATION",
                "is_terminal_failure": True,
                "successful_tables": 0,
                "total_tables": len(request.selected_tables) if request.selected_tables else 0,
                "total_rows": 0,
                "service_name": "Salesforce Sandbox to Databricks",
                "started_at": datetime.utcnow().isoformat(),
            },
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
