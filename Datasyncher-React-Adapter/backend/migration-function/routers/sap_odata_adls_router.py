"""Router exposing endpoints for SAP OData to ADLS Gen2 migration."""

import logging
import time
from fastapi import APIRouter, HTTPException

from models.sap_odata_models import SAPODataADLSMigrationRequest
from services.sap.as_source.to_adls import migrate_sap_odata_to_adls

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/AzureDataLakeStorage-SAPOData-migration-yield")
@router.post("/api-new/AzureDataLakeStorage-SAPOData-migration-yield")
async def sap_odata_adls_migrate(request: SAPODataADLSMigrationRequest):
    """Replicate OData collections from SAP Gateway to ADLS Gen2 destination."""
    start_time = time.time()
    try:
        logger.info(
            f"SAP OData migration started - connection_id={request.connection_id}, "
            f"session_id={request.migration_session_id}, "
            f"tables={len(request.selected_tables or [])}"
        )
        return migrate_sap_odata_to_adls(request)
    except HTTPException:
        raise
    except Exception as exc:
        from utils.error_utils import handle_service_error, format_exception_message

        clean_error_msg = format_exception_message(exc)
        handle_service_error(
            error=ValueError(clean_error_msg),
            context={
                "job_name": getattr(request, "job_name", "SAPOData-ADLS Migration"),
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
                "service_name": "SAPOData-ADLS",
            },
        )
        logger.error("SAP OData->ADLS migration failed: %s", clean_error_msg)
        raise HTTPException(status_code=422, detail=clean_error_msg)


