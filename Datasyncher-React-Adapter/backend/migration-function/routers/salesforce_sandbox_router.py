from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
import urllib.parse
import json
import logging
from pydantic import ValidationError

# Set up logger for the router
logger = logging.getLogger(__name__)
from services.salesforce.client import (
    get_salesforce_authorize_url,
    get_access_token,
    refresh_salesforce_token,
    get_salesforce_objects_and_columns,
    salesforce_connection,
)
from services.salesforce.as_source.to_snowflake import sync_salesforce_to_snowflake
from services.salesforce.as_destination.from_snowflake import sync_snowflake_to_salesforce
from services.salesforce.as_destination.from_databricks import sync_databricks_to_salesforce
from services.salesforce.validation import validate_salesforce_connection_payload
from models.salesforce_models import SalesforceSandboxListing, SalesforceMigration, SalesforceSnowflakeMigration, SalesforceSandboxValidationRequest

router = APIRouter()


def _friendly_salesforce_auth_error(message: str) -> tuple[int, str]:
    msg = str(message or "")
    msg_low = msg.lower()
    limit_keywords = [
        "request_limit_exceeded",
        "totalrequests limit exceeded",
        "request limit",
        "rate limit",
        "too many requests",
        "429",
        "503",
        "service unavailable",
    ]
    if any(keyword in msg_low for keyword in limit_keywords):
        return 429, "Salesforce API limit reached. Please try again after some time."
    return 500, msg

@router.get("/SalesforceSandbox-Authorization")
def authorize_salesforce_sandbox(state: str, data: str):
    return process_authorization(state, data, environment="sandbox")

@router.post("/SalesforceSandbox-Authorization")
async def validate_salesforce_sandbox_connection(data: SalesforceSandboxValidationRequest):
    """Validate Salesforce Sandbox OAuth or JWT Bearer connection."""
    try:
        return validate_salesforce_connection_payload(
            data.SalesforceSandbox,
            environment="sandbox",
        )
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"message": e.detail})
    except Exception as e:
        status_code, friendly_message = _friendly_salesforce_auth_error(str(e))
        return JSONResponse(status_code=status_code, content={"message": friendly_message})

def process_authorization(state: str, data: str, environment: str):
    try:
        decoded_data = urllib.parse.unquote(data)
        data = json.loads(decoded_data)

        auth_url = get_salesforce_authorize_url(state, data, environment,is_source=False)
        return {"auth_url": auth_url}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")


@router.get("/Source-SalesforceSandbox-Authorization")
def authorize_salesforcesandbox_source(state: str, data: str):
    return process_authorization_source(state, data, environment="sandbox")

def process_authorization_source(state: str, data: str, environment: str):
    try:
        decoded_data = urllib.parse.unquote(data)
        data = json.loads(decoded_data)

        auth_url = get_salesforce_authorize_url(state, data, environment,is_source=True)
        return {"auth_url": auth_url}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")
    
@router.get("/salesforcesandbox/callback")
def salesforce_sandbox_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    encoded_data = request.query_params.get("data_new")

    try:
        data = json.loads(urllib.parse.unquote(encoded_data)) if encoded_data else {}
        token = get_access_token(code, state, data, environment="sandbox",is_source=False)
        return token
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")
    
@router.get("/source/salesforcesandbox/callback")
def source_salesforce_sandbox_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    encoded_data = request.query_params.get("data_new")

    try:
        data = json.loads(urllib.parse.unquote(encoded_data)) if encoded_data else {}
        token = get_access_token(code, state, data, environment="sandbox",is_source=True)
        return token
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")


@router.post("/salesforcesandbox/refresh")
def refresh_token_salesforce_sandbox(state: str):
    try:
        token = refresh_salesforce_token(state, environment="sandbox")
        return token.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/SalesforceSandbox-listing-tables")
def salesforce_listing_tables_sandbox(data: SalesforceSandboxListing):
    return process_salesforce_listing(data, environment="sandbox")

def process_salesforce_listing(data: SalesforceSandboxListing, environment: str):
    import time
    start_time = time.time()
    try:
        salesforce_details = data.SalesforceSandbox
        is_source = data.is_source if data.is_source is not None else False
        print("inside salesforce listing")
        object_list = get_salesforce_objects_and_columns(
            salesforce_details.get("state"),
            data.connection_id,
            data.job_name,
            salesforce_details.get("access_token"),
            salesforce_details.get("instance_url"),
            salesforce_details.get("refresh_token"),
            salesforce_details.get("CLIENT_ID"),
            salesforce_details.get("CLIENT_SECRET"),
            environment,
            is_source,
            salesforce_credentials=salesforce_details,
        )

        return {"objects": object_list}
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': data.job_name,
                'log_id': data.log_id,
                'cmp_id': data.cmp_id,
                'connection_id': data.connection_id,
                'user_id': data.user_id,
                'operation': 'UPDATE_SCHEMA',
                'is_terminal_failure': True,
                'execution_time': time.time() - start_time,
                'total_tables': 0,
                'successful_tables': 0,
                'total_rows': 0,
                'service_name': 'Salesforce Sandbox'
            }
        )
        raise e

@router.post("/SalesforceSandbox-migrate")
async def migrate_data_sandbox(request: Request):
    return await process_salesforce_migration(request, environment="sandbox")

async def process_salesforce_migration(request: Request, environment: str):
    try:
        body = await request.json()
        migration_request = SalesforceMigration(**body)
        result = await sync_snowflake_to_salesforce(migration_request, environment)
        return {"environment": environment, "result": result}
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(migration_request, 'job_name', 'Salesforce Sandbox Migration'),
                'log_id': getattr(migration_request, 'log_id', None),
                'cmp_id': getattr(migration_request, 'cmp_id', None),
                'connection_id': getattr(migration_request, 'connection_id', None),
                'user_id': getattr(migration_request, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'service_name': 'Salesforce Sandbox'
            }
        )
        raise e

@router.post("/SalesforceSandbox-Snowflake-migrate")
async def salesforce_snowflake_migrate_data(request: Request):
    return await process_snowflake_salesforce_migration(request, environment="sandbox")

async def process_snowflake_salesforce_migration(request: Request, environment: str):
    try:
        body = await request.json()
        migration_request = SalesforceSnowflakeMigration(**body)

        result = await sync_salesforce_to_snowflake(migration_request, environment)

        return {"environment": environment, "result": result}

    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(migration_request, 'job_name', 'Snowflake-Salesforce Sandbox Migration'),
                'log_id': getattr(migration_request, 'log_id', None),
                'cmp_id': getattr(migration_request, 'cmp_id', None),
                'connection_id': getattr(migration_request, 'connection_id', None),
                'user_id': getattr(migration_request, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'service_name': 'Salesforce Sandbox (Snowflake)'
            }
        )
        raise e


@router.post("/Salesforce-DatabricksSandbox-migrate")
async def migrate_databricks_to_salesforce_sandbox(request: Request):
    return await process_databricks_salesforce_sandbox_migration(request, environment="sandbox")


async def process_databricks_salesforce_sandbox_migration(request: Request, environment: str):
    try:
        body = await request.json()
        migration_request = SalesforceMigration(**body)
        result = await sync_databricks_to_salesforce(migration_request, environment)
        return {"environment": environment, "result": result}
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(migration_request, 'job_name', 'Databricks-Salesforce Sandbox RETL'),
                'log_id': getattr(migration_request, 'log_id', None),
                'cmp_id': getattr(migration_request, 'cmp_id', None),
                'connection_id': getattr(migration_request, 'connection_id', None),
                'user_id': getattr(migration_request, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'service_name': 'Databricks to Salesforce Sandbox'
            }
        )
        raise e

