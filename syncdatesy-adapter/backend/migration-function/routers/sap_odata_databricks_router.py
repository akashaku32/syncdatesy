"""Router exposing endpoints for SAP OData to Databricks migration."""

import logging
import time
from fastapi import APIRouter, HTTPException

from models.sap_odata_models import SAPODataDatabricksMigrationRequest
from services.sap.as_source.to_databricks import sap_odata_to_databricks_connector

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/Databricks-SAPOData-migrate")
@router.post("/Databricks-SAPOData-migration-yield")
@router.post("/api-new/Databricks-SAPOData-migrate")
@router.post("/api-new/Databricks-SAPOData-migration-yield")
async def sap_odata_databricks_migrate(request: SAPODataDatabricksMigrationRequest):
    """Replicate OData collections from SAP Gateway to Databricks via staging COPY/MERGE."""
    start_time = time.time()
    try:
        logger.info(
            "SAP OData -> Databricks migration started - connection_id=%s, "
            "session_id=%s, tables=%s",
            request.connection_id,
            request.migration_session_id,
            len(request.selected_tables or []),
        )
        return sap_odata_to_databricks_connector(request)
    except HTTPException:
        raise
    except Exception as exc:
        from utils.error_utils import handle_service_error, format_exception_message

        clean_error_msg = format_exception_message(exc)
        handle_service_error(
            error=ValueError(clean_error_msg),
            context={
                "job_name": getattr(request, "job_name", "SAPOData-Databricks Migration"),
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
                "service_name": "SAPOData-Databricks",
            },
        )
        logger.error("SAP OData->Databricks migration failed: %s", clean_error_msg)
        raise HTTPException(status_code=422, detail=clean_error_msg)
