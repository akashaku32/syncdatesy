import time

from fastapi import APIRouter, HTTPException

from models.netsuite_models import NetSuiteListingTablesRequest
from services.netsuite.listing import discover_and_populate_netsuite

router = APIRouter()


@router.post("/NetSuite-listing-tables")
@router.post("/Source-NetSuite-listing-tables")
@router.post("/api-new/NetSuite-listing-tables")
def netsuite_listing_tables(req: NetSuiteListingTablesRequest):
    start_time = time.time()
    try:
        return discover_and_populate_netsuite(
            connection_id=req.connection_id,
            credentials=req.NetSuite,
            job_name=req.job_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        from utils.error_utils import handle_service_error

        handle_service_error(
            error=exc,
            context={
                "job_name": req.job_name,
                "cmp_id": req.cmp_id,
                "connection_id": req.connection_id,
                "user_id": req.user_id,
                "operation": "UPDATE_SCHEMA",
                "is_terminal_failure": True,
                "execution_time": time.time() - start_time,
                "total_tables": 0,
                "successful_tables": 0,
                "total_rows": 0,
                "service_name": "NetSuite",
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
