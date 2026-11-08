from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from services.sap.client import (
    discover_sap_odata_services,
    discover_sap_odata_service_entities,
    discover_and_populate_sap_odata_service,
    save_sap_odata_service_entities
)

router = APIRouter()


class SAPODataServicesRequest(BaseModel):
    credentials: Dict[str, Any]


class SAPODataServiceEntitiesRequest(BaseModel):
    credentials: Dict[str, Any]
    service_name: str
    connection_id: Optional[int] = None
    entities: Optional[List[str]] = None


class SAPODataListingTablesRequest(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int
    SAPOData: dict
    cmp_id: int
    user_id: int
    last_checked_date: Optional[str] = None


@router.post("/sap-odata/services")
def get_sap_odata_services(req: SAPODataServicesRequest) -> List[Dict[str, Any]]:
    """
    Endpoint to discover available SAP OData services.
    Called by Django with decrypted connection credentials.
    """
    return discover_sap_odata_services(req.credentials)


@router.post("/sap-odata/service-entities")
def get_sap_odata_service_entities(req: SAPODataServiceEntitiesRequest) -> Any:
    """
    Endpoint to fetch metadata and list entities of a specific SAP OData service.
    Called by Django with credentials and the target service name.
    """
    if req.connection_id is not None or req.entities is not None:
        if req.connection_id is None or req.entities is None:
            raise HTTPException(
                status_code=422,
                detail="connection_id and entities are required when saving SAP entities.",
            )
        saved_count = save_sap_odata_service_entities(
            connection_id=req.connection_id,
            credentials=req.credentials,
            service_name=req.service_name,
            selected_entities=req.entities,
        )
        return {"success": True, "saved_count": saved_count}

    return discover_sap_odata_service_entities(req.credentials, req.service_name)


@router.post("/sap-odata/listing-tables")
@router.post("/SAPOData-listing-tables")
@router.post("/Source-SAPOData-listing-tables")
@router.post("/api-new/sap-odata/listing-tables")
@router.post("/api-new/SAPOData-listing-tables")
def sap_odata_listing_tables(req: SAPODataListingTablesRequest):
    """
    Endpoint to discover and register all SAP OData services & entities.
    Updates the database table `source_table_selection` and marks migration record status.
    """
    import time
    start_time = time.time()
    try:
        result = discover_and_populate_sap_odata_service(
            connection_id=req.connection_id,
            credentials=req.SAPOData,
            job_name=req.job_name
        )
        return result
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': req.job_name,
                'cmp_id': req.cmp_id,
                'connection_id': req.connection_id,
                'user_id': req.user_id,
                'operation': 'UPDATE_SCHEMA',
                'is_terminal_failure': True,
                'execution_time': time.time() - start_time,
                'total_tables': 0,
                'successful_tables': 0,
                'total_rows': 0,
                'service_name': 'SAPOData'
            }
        )
        raise HTTPException(status_code=500, detail=str(e))



