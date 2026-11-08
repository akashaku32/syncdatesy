import asyncio
import logging

from fastapi import APIRouter

from models.sharepoint_models import SharePointAuthorizationRequest
from services.sharepoint.client import test_sharepoint_connection, validate_sharepoint_authorization

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/SharePoint-Authorization")
async def authorize_sharepoint(request: SharePointAuthorizationRequest):
    """
    Minimal authorization endpoint used by Django DestinationConfigSaveAPIView.

    Validates required configuration fields for SharePoint destination creation/test
    and performs a live Microsoft Entra + Graph connectivity check.
    """
    validate_sharepoint_authorization(request)
    details = await asyncio.to_thread(test_sharepoint_connection, request)
    logger.info(
        "SharePoint auth router: validation succeeded | site_id=%s drive_id=%s",
        details.get("site_id"),
        details.get("drive_id"),
    )

    return {
        "status_code": 200,
        "body": "SharePoint destination configuration validated successfully.",
        "details": details,
    }
