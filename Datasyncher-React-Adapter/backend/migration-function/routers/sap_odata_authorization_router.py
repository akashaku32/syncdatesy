import asyncio
import logging

from fastapi import APIRouter, HTTPException

from models.sap_odata_models import SAPODataAuthorizationRequest
from services.sap.client import test_sap_odata_connection, validate_sap_odata_authorization

router = APIRouter()
logger = logging.getLogger(__name__)


def _http_exception_from_sap_auth_failure(exc: BaseException) -> HTTPException:
    """
    Map unexpected errors (e.g. missing gRPC hub mTLS env) to structured HTTPException
    so Django does not surface only 'Internal Server Error'.
    """
    msg = str(exc).strip()
    if isinstance(exc, RuntimeError) and "SAP proxy hub mTLS" in msg:
        return HTTPException(
            status_code=503,
            detail={
                "error_code": "SAP_ODATA_HUB_MTLS",
                "message": (
                    "SAP proxy hub gRPC client is not configured on migration-function. "
                    "Mount Secret sap-proxy-agent-hub-mtls at PROXY_AGENT_HUB_MTLS_DIR "
                    "(recommended for Kubernetes), or set PROXY_AGENT_HUB_TRUSTED_CA_PEM, "
                    "PROXY_AGENT_HUB_CLIENT_KEY_PEM, PROXY_AGENT_HUB_CLIENT_CERT_PEM, "
                    "or SAP_PROXY_AGENT_GRPC_CONFIG with trusted_ca_cert / client_svr_* keys. "
                    "Use in-cluster hub DNS (e.g. sap-proxy-agent-hub-svc:50051) via "
                    "PROXY_AGENT_SERVER_HOST when hub runs in the cluster."
                ),
            },
        )
    logger.exception("SAP OData authorization failed with unexpected error")
    return HTTPException(
        status_code=500,
        detail={
            "error_code": "SAP_ODATA_AUTH_UNEXPECTED",
            "message": msg or "Unexpected error while validating SAP OData connection.",
        },
    )


@router.post("/SAPOData-Authorization")
@router.post("/Source-SAPOData-Authorization")
@router.post("/api-new/SAPOData-Authorization")
@router.post("/api-new/Source-SAPOData-Authorization")
async def authorize_sap_odata(request: SAPODataAuthorizationRequest):
    """
    POST endpoint to authorize and test SAP OData source connectivity.
    Stops at the first failure using the 5-step validation check.
    """
    try:
        validate_sap_odata_authorization(request)
        details = await asyncio.to_thread(test_sap_odata_connection, request)
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_exception_from_sap_auth_failure(exc) from exc

    return {
        "status_code": 200,
        "body": "SAP OData connection validated successfully.",
        "details": details,
    }


