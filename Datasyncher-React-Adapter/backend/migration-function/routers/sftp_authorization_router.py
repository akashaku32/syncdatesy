import asyncio

from fastapi import APIRouter

from models.sftp_models import SFTPAuthorizationRequest
from services.sftp.client import test_sftp_connection, validate_sftp_authorization

router = APIRouter()


@router.post("/SFTP-Authorization")
async def authorize_sftp(request: SFTPAuthorizationRequest):
    """
    Minimal authorization endpoint used by Django DestinationConfigSaveAPIView.

    Validates required configuration fields and tests SFTP connectivity.
    """
    validate_sftp_authorization(request)
    await asyncio.to_thread(test_sftp_connection, request)

    return {
        "status_code": 200,
        "body": "SFTP connection validated successfully.",
    }
