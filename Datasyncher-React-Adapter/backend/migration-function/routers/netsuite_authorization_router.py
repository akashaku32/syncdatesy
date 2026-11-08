import asyncio

from fastapi import APIRouter

from models.netsuite_models import NetSuiteAuthorizationRequest
from services.netsuite.auth import test_netsuite_connection, validate_netsuite_authorization

router = APIRouter()


@router.post("/NetSuite-Authorization")
@router.post("/Source-NetSuite-Authorization")
@router.post("/api-new/NetSuite-Authorization")
@router.post("/api-new/Source-NetSuite-Authorization")
async def authorize_netsuite(request: NetSuiteAuthorizationRequest):
    config = validate_netsuite_authorization(request)
    details = await asyncio.to_thread(test_netsuite_connection, config)
    return {
        "status_code": 200,
        "body": "NetSuite connection validated successfully.",
        "details": details,
    }
