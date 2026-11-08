import logging
import time

from fastapi import APIRouter, HTTPException

from models.netsuite_models import NetSuiteDatabricksMigrationRequest
from services.netsuite.as_source.to_databricks import netsuite_to_databricks_connector

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/Databricks-NetSuite-migration-yield")
@router.post("/api-new/Databricks-NetSuite-migration-yield")
async def netsuite_databricks_migrate(request: NetSuiteDatabricksMigrationRequest):
    start_time = time.time()
    try:
        logger.info(
            "NetSuite -> Databricks migration started connection_id=%s tables=%s",
            request.connection_id,
            len(request.selected_tables or []),
        )
        return netsuite_to_databricks_connector(request)
    except HTTPException:
        raise
    except Exception as exc:
        from utils.error_utils import handle_service_error, format_exception_message

        clean_error_msg = format_exception_message(exc)
        handle_service_error(
            error=ValueError(clean_error_msg),
            context={
                "job_name": getattr(request, "job_name", "NetSuite-Databricks Migration"),
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
                "service_name": "NetSuite-Databricks",
            },
        )
        raise HTTPException(status_code=422, detail=clean_error_msg) from exc
