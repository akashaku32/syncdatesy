import logging
import sys
from pathlib import Path

# Resolve sap_proxy_agent: /app/sap_proxy_agent (image) or backend/sap_proxy_agent (local tree).
_mf_root = Path(__file__).resolve().parent
for _candidate in (_mf_root, _mf_root.parent):
    if (_candidate / "sap_proxy_agent").is_dir():
        _path = str(_candidate)
        if _path not in sys.path:
            sys.path.insert(0, _path)
        break

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from fastapi import Depends, FastAPI

from utils.internal_auth import verify_internal_token

from routers.microsoft_dynamics_migration_router import router as migration_router

from routers.listing_tables_router import router as listing_tables_router

from routers.microsoft_dynamics_source_connection_router import router as dynamics_router

# from routers.snowflake_router import router as snowflake_router

from routers.salesforce_sandbox_router import  router as  salesforcesandbox_router

from routers.snowflake_listing_router import router as snowflake_listing_router

from routers.salesforce_router import router as salesforce_router

from routers.google_reviews_router import router as google_reviews_router

from routers.snowflake_authorization_router import router as snowflake_auth_router

from routers.snowflake_router import router as snowflake_migration_router

from routers.amazon_s3_routers import router as amazon_s3_router
from routers.s3_snowflake_validation_router import router as s3_snowflake_validation_router
from routers.sftp_authorization_router import router as sftp_auth_router
from routers.snowflake_sftp_router import router as snowflake_sftp_router
from routers.sharepoint_authorization_router import router as sharepoint_auth_router
from routers.snowflake_sharepoint_router import router as snowflake_sharepoint_router
from routers.source_script_router import router as source_script_router
from routers.snowflake_s3_router import router as snowflake_s3_router
from routers.snowflake_adls_router import router as snowflake_adls_router
from routers.sftp_source_router import router as sftp_source_router
from routers.sftp_snowflake_router import router as sftp_snowflake_router
from routers.adls_source_router import router as adls_source_router
from routers.google_drive_router import router as google_drive_router
from routers.databricks_router import router as databricks_router
from routers.databricks_dynamics_migration_router import router as databricks_dynamics_migration_router
from routers.databricks_salesforce_migration_router import router as databricks_salesforce_migration_router

# SAP OData routers
from routers.sap_odata_authorization_router import router as sap_auth_router
from routers.sap_odata_service_discovery_router import router as sap_service_discovery_router
from routers.sap_odata_adls_router import router as sap_adls_router
from routers.sap_odata_snowflake_router import router as sap_snowflake_router
from routers.sap_odata_databricks_router import router as sap_databricks_router
from routers.entity_transform_router import router as entity_transform_router

import os

from fastapi import Request

from fastapi.exceptions import RequestValidationError

from fastapi.responses import JSONResponse

from fastapi.encoders import jsonable_encoder

# Load environment variables from .env file

try:

    from dotenv import load_dotenv

    # Load .env file from the migration-function directory

    env_path = os.path.join(os.path.dirname(__file__), '.env')

    if os.path.exists(env_path):

        load_dotenv(env_path)

        print(f"✅ Loaded environment variables from: {env_path}")

    else:

        print(f"⚠️ .env file not found at: {env_path}. Using system environment variables only.")

        load_dotenv()  # Try loading from current directory or parent

except ImportError:

    print("⚠️ python-dotenv not installed. Using system environment variables only.")

except Exception as e:

    print(f"⚠️ Could not load .env file: {e}. Using system environment variables only.")

# Access environment variables

DB_HOST = os.getenv("DB_HOST")

DB_USER = os.getenv("DB_USER")

DB_PASSWORD = os.getenv("DB_PASSWORD")

DB_NAME = os.getenv("DB_NAME")

app = FastAPI()

_secured = [Depends(verify_internal_token)]

_app_logger = logging.getLogger(__name__)


@app.on_event("startup")
async def _log_sap_proxy_hub_grpc_client_config() -> None:
    """One-line readiness for SAP OData proxy (migration-function → hub gRPC mTLS)."""
    import os
    from pathlib import Path

    mtls_dir = (os.environ.get("PROXY_AGENT_HUB_MTLS_DIR") or "").strip()
    if mtls_dir:
        base = Path(mtls_dir)
        names = ("ca.crt", "client_svr_server.crt", "client_svr_server.key")
        missing = [n for n in names if not (base / n).is_file()]
        if not missing:
            _app_logger.info(
                "SAP hub gRPC client: mTLS files present under PROXY_AGENT_HUB_MTLS_DIR=%s",
                mtls_dir,
            )
        else:
            _app_logger.warning(
                "SAP hub gRPC client: PROXY_AGENT_HUB_MTLS_DIR=%s missing %s "
                "(SAP OData proxy mode will fail until Secret sap-proxy-agent-hub-mtls is mounted)",
                mtls_dir,
                missing,
            )
        return
    if (
        (os.environ.get("PROXY_AGENT_HUB_TRUSTED_CA_PEM") or "").strip()
        and (os.environ.get("PROXY_AGENT_HUB_CLIENT_KEY_PEM") or "").strip()
        and (os.environ.get("PROXY_AGENT_HUB_CLIENT_CERT_PEM") or "").strip()
    ):
        _app_logger.info("SAP hub gRPC client: using PROXY_AGENT_HUB_*_PEM env vars")
        return
    grpc_cfg = (os.environ.get("SAP_PROXY_AGENT_GRPC_CONFIG") or "").strip()
    if grpc_cfg and Path(grpc_cfg).is_file():
        _app_logger.info("SAP hub gRPC client: using SAP_PROXY_AGENT_GRPC_CONFIG=%s", grpc_cfg)
        return
    _app_logger.warning(
        "SAP hub gRPC client: no hub mTLS configured (proxy SAP OData will return SAP_ODATA_HUB_MTLS)."
    )


# Include routers (internal token required)

app.include_router(migration_router, dependencies=_secured)

app.include_router(listing_tables_router, dependencies=_secured)

app.include_router(dynamics_router, dependencies=_secured)

app.include_router(snowflake_listing_router, dependencies=_secured)

app.include_router(snowflake_migration_router, dependencies=_secured)

app.include_router(salesforce_router, dependencies=_secured)

app.include_router(salesforcesandbox_router, dependencies=_secured)

app.include_router(snowflake_auth_router, dependencies=_secured)

app.include_router(google_reviews_router, dependencies=_secured)

app.include_router(amazon_s3_router, dependencies=_secured)
app.include_router(s3_snowflake_validation_router, dependencies=_secured)
app.include_router(sftp_auth_router, dependencies=_secured)
app.include_router(snowflake_sftp_router, dependencies=_secured)
app.include_router(sharepoint_auth_router, dependencies=_secured)
app.include_router(snowflake_sharepoint_router, dependencies=_secured)
app.include_router(source_script_router, dependencies=_secured)
app.include_router(snowflake_s3_router, dependencies=_secured)
app.include_router(snowflake_adls_router, dependencies=_secured)
app.include_router(sftp_source_router, dependencies=_secured)
app.include_router(sftp_snowflake_router, dependencies=_secured)
app.include_router(adls_source_router, dependencies=_secured)
app.include_router(google_drive_router, dependencies=_secured)
app.include_router(databricks_router, dependencies=_secured)
app.include_router(databricks_dynamics_migration_router, dependencies=_secured)
app.include_router(databricks_salesforce_migration_router, dependencies=_secured)

# SAP OData
app.include_router(sap_auth_router, dependencies=_secured)
app.include_router(sap_service_discovery_router, dependencies=_secured)
app.include_router(sap_adls_router, dependencies=_secured)
app.include_router(sap_snowflake_router, dependencies=_secured)
app.include_router(sap_databricks_router, dependencies=_secured)
app.include_router(entity_transform_router, dependencies=_secured)

# 🔻 Add the exception handler **after** creating the `app` instance

@app.exception_handler(RequestValidationError)

async def validation_exception_handler(request: Request, exc: RequestValidationError):

    logging.getLogger(__name__).warning("Request validation error on %s", request.url.path)

    return JSONResponse(

        status_code=422,

        content={"detail": jsonable_encoder(exc.errors())}

    )

# Example to print the loaded environment variable for database configuration

@app.get("/healthz")
def healthz():
    """Lightweight probe target; must stay fast under load."""
    return {"status": "ok"}


@app.get("/")

def read_root():

    return {"message": "FastAPI App is Running", "db_host": DB_HOST}

# Run the FastAPI server locally

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
