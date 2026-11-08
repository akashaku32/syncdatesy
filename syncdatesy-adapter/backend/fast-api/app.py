from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from uuid import uuid4
from celery_worker import (
    dispatch_direct_connector,
    call_function,
    orchestrate_migration,
    celery_app,
    CELERY_ORCHESTRATE_TASK_PRIORITY,
)  # Import Celery tasks and app
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from typing import Optional
import logging
import os
import hmac
from datetime import datetime
import requests
# FastAPI app setup
app = FastAPI()


def _internal_headers(extra=None):
    headers = {"Content-Type": "application/json"}
    key = os.getenv("INTERNAL_API_KEY")
    if key:
        headers["X-INTERNAL-KEY"] = str(key)
    if extra:
        headers.update(extra)
    return headers


_PUBLIC_PATHS = {"/healthz", "/health", "/metrics", "/"}


@app.middleware("http")
async def require_internal_api_key(request: Request, call_next):
    if request.url.path in _PUBLIC_PATHS or request.method == "OPTIONS":
        return await call_next(request)
    expected = os.getenv("INTERNAL_API_KEY") or ""
    provided = request.headers.get("X-INTERNAL-KEY") or ""
    if not expected:
        return JSONResponse(
            status_code=503,
            content={"detail": "INTERNAL_API_KEY is not configured"},
        )
    if not provided or not hmac.compare_digest(str(provided), str(expected)):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing internal service key"},
        )
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if os.getenv("CELERY_PROMETHEUS_METRICS_ENABLED", "false").lower() in ("1", "true", "yes", "on"):
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    import celery_job_metrics as job_metrics

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics():
        registry = job_metrics.get_registry()
        if registry is None:
            raise HTTPException(status_code=404, detail="Prometheus metrics not enabled")
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
else:

    @app.get("/metrics", include_in_schema=False)
    def metrics_disabled():
        raise HTTPException(status_code=404, detail="Prometheus metrics not enabled")

# Database setup
DATABASE_URL = os.getenv('FASTAPI_DATABASE_URL')  # Update with your database URL
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
DOMAIN_NAME =  os.getenv('DOMAIN','qa.datasyncher.com')
print("Current Domain : ", DOMAIN_NAME)
 
# Use internal Docker service for container-to-container communication (more secure and faster)
USE_INTERNAL_API = os.getenv('USE_INTERNAL_API', 'true').lower() == 'true'
MIGRATION_FUNCTION_INTERNAL_URL = os.getenv('MIGRATION_FUNCTION_INTERNAL_URL', 'http://migration-function:8001')
CELERY_FANOUT_ENABLED = os.getenv('CELERY_FANOUT_ENABLED', 'false').lower() == 'true'
CELERY_DIRECT_CONNECTOR_EXECUTION = os.getenv('CELERY_DIRECT_CONNECTOR_EXECUTION', 'true').lower() == 'true'
CELERY_HTTP_FALLBACK_ENABLED = os.getenv('CELERY_HTTP_FALLBACK_ENABLED', 'false').lower() == 'true'
CELERY_MIGRATION_QUEUE = os.getenv("CELERY_MIGRATION_QUEUE", "migration")
CELERY_MIGRATION_CONTROL_QUEUE = os.getenv("CELERY_MIGRATION_CONTROL_QUEUE", "migration-control")
CELERY_JOB_ISOLATION_MODE = os.getenv("CELERY_JOB_ISOLATION_MODE", "per_job").strip().lower()


def _orchestrate_enqueue_queue() -> str:
    return CELERY_MIGRATION_CONTROL_QUEUE

if USE_INTERNAL_API:
    # Internal: Direct Docker service-to-service communication (recommended)
    # Note: No /api-new prefix needed for internal calls - that's only for Traefik routing
    API_BASE_URL = MIGRATION_FUNCTION_INTERNAL_URL
    print(f"Using INTERNAL API: {API_BASE_URL}")
else:
    # External: Through Traefik (fallback option)
    # Traefik strips /api-new prefix and forwards to migration-function
    API_BASE_URL = f"https://{DOMAIN_NAME}/api-new"
    print(f"Using EXTERNAL API: {API_BASE_URL}")
 
def get_api_url(endpoint: str) -> str:
    """
    Helper function to construct API URLs for migration-function service.
    Uses internal Docker service name by default for better security and performance.
   
    Args:
        endpoint: The API endpoint (e.g., 'migrate', 'Salesforce-listing-tables')
   
    Returns:
        Complete URL string (internal or external based on USE_INTERNAL_API setting)
    """
    url = f"{API_BASE_URL}/{endpoint}"
    print(f"🔍 DEBUG get_api_url: endpoint='{endpoint}' -> URL='{url}'")
    return url
 
SENSITIVE_LOG_KEYS = {
    "aws_access_key_id",
    "aws_secret_access_key",
    "password",
    "passphrase",
    "private_key",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
}
 
 
def _redact_payload(value):
    """Recursively redact common credential fields for safe request logging."""
    if isinstance(value, dict):
        redacted = {}
        for key, inner in value.items():
            if str(key).lower() in SENSITIVE_LOG_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_payload(inner)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value
 
 
def _log_payload(event: str, payload: dict) -> None:
    logging.info("%s: %s", event, _redact_payload(payload))
 
 
# Cloud function URLs
CLOUD_FUNCTION_URLS = {
    # '/Snowflake-MicrosoftDynamics-migration-yield': 'https://us-central1-elt-project-433103.cloudfunctions.net/function-migration-yield',
    # '/s3_migration': 'https://us-central1-elt-project-433103.cloudfunctions.net/s3_migration',
    '/Snowflake-MicrosoftDynamics-migration-yield': get_api_url('migrate'),
    '/Snowflake-MicrosoftDynamics365_FO-migration-yield': get_api_url('migrate'),
    '/Databricks-MicrosoftDynamics365_FO-migration-yield': get_api_url('Databricks-MicrosoftDynamics365_FO-migrate'),
    '/Databricks-Salesforce-migration-yield': get_api_url('Databricks-Salesforce-migrate'),
    '/Databricks-SalesforceSandbox-migration-yield': get_api_url('Databricks-SalesforceSandbox-migrate'),
    '/Salesforce-Databricks-migration-yield': get_api_url('Salesforce-Databricks-migrate'),
    '/Salesforce-DatabricksSandbox-migration-yield': get_api_url('Salesforce-DatabricksSandbox-migrate'),
    '/MicrosoftDynamics365_FO-listing-tables': get_api_url('MicrosoftDynamics365_FO-listing-tables'),
    '/Snowflake-listing-tables': get_api_url('Snowflake-listing-tables'),
    '/Snowflake-listing-tables-readonly': get_api_url('Snowflake-listing-tables-readonly'),
    '/Databricks-listing-tables': get_api_url('Databricks-listing-tables'),
    '/SAPOData-listing-tables': get_api_url('SAPOData-listing-tables'),
    '/Salesforce-listing-tables': get_api_url('Salesforce-listing-tables'),
    '/Salesforce-Snowflake-migration-yield': get_api_url('Salesforce-migrate'),
    '/SalesforceSandbox-listing-tables': get_api_url('SalesforceSandbox-listing-tables'),
    '/Snowflake-SalesforceSandbox-migration-yield': get_api_url('SalesforceSandbox-Snowflake-migrate'),
    '/Snowflake-Salesforce-migration-yield': get_api_url('Salesforce-Snowflake-migrate'),
    '/SalesforceSandbox-Snowflake-migration-yield': get_api_url('SalesforceSandbox-migrate'),
    '/Snowflake-Snowflake-migration-yield': get_api_url('Snowflake-migrate'),
    '/GoogleReviews-listing-tables': get_api_url('GoogleReviews-listing-tables'),
    '/Snowflake-GoogleReviews-migration-yield': get_api_url('Snowflake-GoogleReviews-migrate'),
    '/AmazonS3-listing-tables': get_api_url('AmazonS3-listing-tables'),
    '/AmazonS3-listing-files': get_api_url('AmazonS3-listing-files'),
    '/AmazonS3-preview-pattern': get_api_url('AmazonS3-preview-pattern'),
    '/AmazonS3-suggest-primary-keys': get_api_url('AmazonS3-suggest-primary-keys'),
    '/Snowflake-AmazonS3-migration-yield': get_api_url('Snowflake-AmazonS3-migrate'),
    '/SFTP-Authorization': get_api_url('SFTP-Authorization'),
    '/SFTP-listing-files': get_api_url('SFTP-listing-files'),
    '/SFTP-listing-tables': get_api_url('SFTP-listing-tables'),
    '/SFTP-preview-pattern': get_api_url('SFTP-preview-pattern'),
    '/SFTP-suggest-primary-keys': get_api_url('SFTP-suggest-primary-keys'),
    # Destination-first naming (matches Django task url construction): SFTP destination + Snowflake source.
    '/SFTP-Snowflake-migration-yield': get_api_url('Snowflake-SFTP-migrate'),
    '/Snowflake-SFTP-migration-yield': get_api_url('SFTP-Snowflake-migrate'),
    '/SharePoint-Snowflake-migration-yield': get_api_url('Snowflake-SharePoint-migrate'),
    '/AmazonS3-Snowflake-migration-yield': get_api_url('Snowflake-S3-migrate'),
    '/AzureDataLakeStorage-listing-files': get_api_url('AzureDataLakeStorage-listing-files'),
    '/AzureDataLakeStorage-listing-tables': get_api_url('AzureDataLakeStorage-listing-tables'),
    '/AzureDataLakeStorage-preview-pattern': get_api_url('AzureDataLakeStorage-preview-pattern'),
    '/AzureDataLakeStorage-Snowflake-migration-yield': get_api_url('Snowflake-ADLS-migrate'),
    '/Snowflake-AzureDataLakeStorage-migration-yield': get_api_url('AzureDataLakeStorage-Snowflake-migrate'),
    '/ADLS-listing-files': get_api_url('ADLS-listing-files'),
    '/ADLS-listing-tables': get_api_url('ADLS-listing-tables'),
    '/ADLS-preview-pattern': get_api_url('ADLS-preview-pattern'),
    '/Snowflake-ADLS-migration-yield': get_api_url('ADLS-Snowflake-migrate'),
    '/GoogleDrive-listing-files': get_api_url('GoogleDrive-listing-files'),
    '/GoogleDrive-listing-tables': get_api_url('GoogleDrive-listing-tables'),
    '/GoogleDrive-preview-pattern': get_api_url('GoogleDrive-preview-pattern'),
    '/Source-GoogleDrive-Authorization': get_api_url('Source-GoogleDrive-Authorization'),
    '/GoogleDrive-Authorization': get_api_url('GoogleDrive-Authorization'),
    '/GoogleDrive-Snowflake-migration-yield': get_api_url('Snowflake-GoogleDrive-migrate'),
    '/Snowflake-GoogleDrive-migration-yield': get_api_url('GoogleDrive-Snowflake-migrate'),
    '/Snowflake-SAPOData-migration-yield': get_api_url('Snowflake-SAPOData-migration-yield'),
    '/AzureDataLakeStorage-SAPOData-migration-yield': get_api_url('AzureDataLakeStorage-SAPOData-migration-yield'),
    '/Databricks-SAPOData-migration-yield': get_api_url('Databricks-SAPOData-migrate'),
    '/NetSuite-Authorization': get_api_url('NetSuite-Authorization'),
    '/NetSuite-listing-tables': get_api_url('NetSuite-listing-tables'),
    '/Snowflake-NetSuite-migration-yield': get_api_url('Snowflake-NetSuite-migration-yield'),
    '/Databricks-NetSuite-migration-yield': get_api_url('Databricks-NetSuite-migration-yield'),
}

MIGRATION_FUNCTION_PATHS = {
    '/Snowflake-MicrosoftDynamics-migration-yield',
    '/Snowflake-MicrosoftDynamics365_FO-migration-yield',
    '/Databricks-MicrosoftDynamics365_FO-migration-yield',
    '/Databricks-Salesforce-migration-yield',
    '/Databricks-SalesforceSandbox-migration-yield',
    '/Salesforce-Databricks-migration-yield',
    '/Salesforce-DatabricksSandbox-migration-yield',
    '/Salesforce-Snowflake-migration-yield',
    '/Snowflake-Salesforce-migration-yield',
    '/SalesforceSandbox-Snowflake-migration-yield',
    '/Snowflake-SalesforceSandbox-migration-yield',
    '/Snowflake-Snowflake-migration-yield',
    '/Snowflake-GoogleReviews-migration-yield',
    '/Snowflake-AmazonS3-migration-yield',
    '/Snowflake-SFTP-migration-yield',
    '/SFTP-Snowflake-migration-yield',
    '/SharePoint-Snowflake-migration-yield',
    '/AmazonS3-Snowflake-migration-yield',
    '/AzureDataLakeStorage-Snowflake-migration-yield',
    '/Snowflake-AzureDataLakeStorage-migration-yield',
    '/Snowflake-ADLS-migration-yield',
    '/GoogleDrive-Snowflake-migration-yield',
    '/Snowflake-GoogleDrive-migration-yield',
    '/Snowflake-SAPOData-migration-yield',
    '/AzureDataLakeStorage-SAPOData-migration-yield',
    '/Databricks-SAPOData-migration-yield',
    '/Snowflake-NetSuite-migration-yield',
    '/Databricks-NetSuite-migration-yield',
}

CONNECTOR_TYPE_BY_PATH = {
    '/Snowflake-MicrosoftDynamics-migration-yield': 'microsoft_dynamics',
    '/Snowflake-MicrosoftDynamics365_FO-migration-yield': 'microsoft_dynamics',
    '/Databricks-MicrosoftDynamics365_FO-migration-yield': 'microsoft_dynamics_databricks',
    '/Databricks-Salesforce-migration-yield': 'salesforce_to_databricks',
    '/Databricks-SalesforceSandbox-migration-yield': 'salesforce_sandbox_to_databricks',
    '/Salesforce-Databricks-migration-yield': 'databricks_to_salesforce',
    '/Salesforce-DatabricksSandbox-migration-yield': 'databricks_sandbox_to_salesforce',
    '/Salesforce-Snowflake-migration-yield': 'snowflake_to_salesforce',
    '/Snowflake-Salesforce-migration-yield': 'salesforce_to_snowflake',
    '/SalesforceSandbox-Snowflake-migration-yield': 'snowflake_to_salesforce_sandbox',
    '/Snowflake-SalesforceSandbox-migration-yield': 'salesforce_sandbox_to_snowflake',
    '/Snowflake-Snowflake-migration-yield': 'snowflake_to_snowflake',
    '/Snowflake-GoogleReviews-migration-yield': 'google_reviews',
    '/Snowflake-AmazonS3-migration-yield': 'amazon_s3',
    '/SFTP-Authorization': 'sftp_authorization',
    '/SFTP-listing-files': 'sftp_listing_files',
    '/SFTP-listing-tables': 'sftp_listing_tables',
    '/SFTP-preview-pattern': 'sftp_preview_pattern',
    '/SFTP-Snowflake-migration-yield': 'snowflake_to_sftp',
    '/Snowflake-SFTP-migration-yield': 'sftp_to_snowflake',
    '/SharePoint-Snowflake-migration-yield': 'snowflake_to_sharepoint',
    '/AmazonS3-Snowflake-migration-yield': 'snowflake_to_amazon_s3',
    '/AmazonS3-preview-pattern': 'amazon_s3_preview_pattern',
    '/AzureDataLakeStorage-listing-files': 'azure_data_lake_storage_listing_files',
    '/AzureDataLakeStorage-listing-tables': 'azure_data_lake_storage_listing_tables',
    '/AzureDataLakeStorage-preview-pattern': 'azure_data_lake_storage_preview_pattern',
    '/AzureDataLakeStorage-Snowflake-migration-yield': 'snowflake_to_adls',
    '/Snowflake-AzureDataLakeStorage-migration-yield': 'adls_to_snowflake',
    '/ADLS-listing-files': 'adls_listing_files',
    '/ADLS-listing-tables': 'adls_listing_tables',
    '/ADLS-preview-pattern': 'adls_preview_pattern',
    '/Snowflake-ADLS-migration-yield': 'adls_to_snowflake',
    '/GoogleDrive-listing-files': 'google_drive_listing_files',
    '/GoogleDrive-listing-tables': 'google_drive_listing_tables',
    '/GoogleDrive-preview-pattern': 'google_drive_preview_pattern',
    '/GoogleDrive-Snowflake-migration-yield': 'snowflake_to_googledrive',
    '/Snowflake-GoogleDrive-migration-yield': 'googledrive_to_snowflake',
    '/Snowflake-SAPOData-migration-yield': 'sap_odata_to_snowflake',
    '/AzureDataLakeStorage-SAPOData-migration-yield': 'sap_odata_to_adls',
    '/Databricks-SAPOData-migration-yield': 'sap_odata_to_databricks',
    '/Snowflake-NetSuite-migration-yield': 'netsuite_to_snowflake',
    '/Databricks-NetSuite-migration-yield': 'netsuite_to_databricks',
    '/NetSuite-Authorization': 'netsuite_authorization',
    '/SFTP-suggest-primary-keys': 'sftp_suggest_primary_keys',
    '/Source-GoogleDrive-Authorization': 'source_google_drive_authorization',
    '/GoogleDrive-Authorization': 'google_drive_authorization',
}

LISTING_FUNCTION_PATHS = {
    '/MicrosoftDynamics365_FO-listing-tables',
    '/Snowflake-listing-tables',
    '/Snowflake-listing-tables-readonly',
    '/Databricks-listing-tables',
    '/SAPOData-listing-tables',
    '/NetSuite-listing-tables',
    '/Salesforce-listing-tables',
    '/SalesforceSandbox-listing-tables',
    '/GoogleReviews-listing-tables',
    '/AmazonS3-listing-tables',
    '/AmazonS3-listing-files',
    '/SFTP-listing-files',
    '/SFTP-listing-tables',
    '/AzureDataLakeStorage-listing-files',
    '/AzureDataLakeStorage-listing-tables',
    '/ADLS-listing-files',
    '/ADLS-listing-tables',
    '/GoogleDrive-listing-files',
    '/GoogleDrive-listing-tables',
}

LISTING_CONNECTOR_TYPE_BY_PATH = {
    '/MicrosoftDynamics365_FO-listing-tables': 'microsoft_dynamics_listing',
    '/Snowflake-listing-tables': 'snowflake_listing',
    '/Snowflake-listing-tables-readonly': 'snowflake_listing_readonly',
    '/Databricks-listing-tables': 'databricks_listing',
    '/SAPOData-listing-tables': 'sap_odata_listing',
    '/NetSuite-listing-tables': 'netsuite_listing',
    '/Salesforce-listing-tables': 'salesforce_listing',
    '/SalesforceSandbox-listing-tables': 'salesforce_sandbox_listing',
    '/GoogleReviews-listing-tables': 'google_reviews_listing',
    '/AmazonS3-listing-tables': 'amazon_s3_listing',
    '/AmazonS3-listing-files': 'amazon_s3_listing_files',
    '/SFTP-listing-files': 'sftp_listing_files',
    '/SFTP-listing-tables': 'sftp_listing_tables',
    '/SFTP-preview-pattern': 'sftp_preview_pattern',
    '/SFTP-suggest-primary-keys': 'sftp_suggest_primary_keys',
    '/AzureDataLakeStorage-listing-files': 'azure_data_lake_storage_listing_files',
    '/AzureDataLakeStorage-listing-tables': 'azure_data_lake_storage_listing_tables',
    '/AzureDataLakeStorage-preview-pattern': 'azure_data_lake_storage_preview_pattern',
    '/ADLS-listing-files': 'adls_listing_files',
    '/ADLS-listing-tables': 'adls_listing_tables',
    '/ADLS-preview-pattern': 'adls_preview_pattern',
    '/GoogleDrive-listing-files': 'google_drive_listing_files',
    '/GoogleDrive-listing-tables': 'google_drive_listing_tables',
    '/GoogleDrive-preview-pattern': 'google_drive_preview_pattern',
}


def _enforce_cutover_contract(payload: dict, function_path: str) -> dict:
    """
    Normalizes payload fields for direct Celery execution.
    """
    normalized = dict(payload or {})
    normalized['function_path'] = function_path
    normalized['connector_type'] = (
        CONNECTOR_TYPE_BY_PATH.get(function_path)
        or LISTING_CONNECTOR_TYPE_BY_PATH.get(function_path)
    )
    normalized['attempt'] = normalized.get('retry_attempt', 0)
    normalized['selected_tables'] = normalized.get('selected_tables') or []
    normalized['chunk_count'] = normalized.get('chunk_count') or 0

    if not normalized.get('idempotency_key'):
        base = normalized.get('job_name', 'job')
        conn = normalized.get('connection_id', 'unknown')
        session = normalized.get('migration_session_id', 'unknown')
        normalized['idempotency_key'] = f"{base}:{conn}:{session}:{function_path}"

    required_fields = [
        'job_name',
        'connection_id',
        'migration_session_id',
        'connector_type',
        'selected_tables',
        'chunk_count',
        'attempt',
        'idempotency_key',
    ]
    missing = [field for field in required_fields if normalized.get(field) is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required contract fields for Celery direct execution: {missing}",
        )
    return normalized
# Define the request model
class MigrationData(BaseModel):
    job_name: str
    source_config: dict
    destination_config: dict
    selected_tables: list
    attribute_dict: dict
    folderpath_dict: dict
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    is_deleted: str
    is_updated: str
    safety_interval: str
    migration_session_id: int
    last_checked_dates: dict
    execution_order:str
    chunk_count:int
    logs_creation:bool
    connection_name: str
    company_name: str
    # ✅ Added trigger_type field to track migration trigger source
    trigger_type: Optional[str] = "manual"
    # ✅ Retry attempt tracking fields (prevent duplicate emails)
    retry_attempt: int = 0  # Default to 0 (first attempt)
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates
    max_retries: int = 2    # Default to 2 (total 3 attempts: 1 initial + 2 retries)
    is_final_attempt: bool = True  # Default to True for backward compatibility
    drop_and_reload: Optional[bool] = False  # Flag for drop and reload operation
    table_export_settings: Optional[dict] = None  # Optional mapping: {table_name: export_settings}
    # Pipeline run-wide pod budget slice (optional). When set, orchestrate must not
    # independently grant the full plan max_pods for this job.
    pod_cap: Optional[int] = None
 
class ListingTables(BaseModel):
    job_name: str
    connection_id: int  # Include connection ID
    migration_session_id: int
    MicrosoftDynamics365_FO: dict
    last_checked_date: Optional[str]
    cmp_id: int
    user_id:int
 
class SnowflakeListingTables(BaseModel):
    job_name: str
    connection_id: int  # Include connection ID
    migration_session_id: int
    Snowflake: dict
    # Optional: required for Snowflake->Snowflake style flows, but not applicable for
    # Snowflake->SFTP "update schema" (we only list source tables).
    destination_schema: Optional[str] = None
    last_checked_date: Optional[str]

class DatabricksListingTables(BaseModel):
    job_name: Optional[str] = None
    connection_id: int
    Databricks: dict
    last_checked_date: Optional[str] = None
    cmp_id: Optional[int] = None
    user_id: Optional[int] = None
    log_id: Optional[int] = None
    is_source: Optional[bool] = True
    migration_session_id: Optional[int] = None

class NetSuiteListingTables(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int = 0
    NetSuite: dict
    cmp_id: int
    user_id: int
    last_checked_date: Optional[str] = None
    log_id: Optional[int] = None


class SAPODataListingTables(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int
    SAPOData: dict
    cmp_id: int
    user_id: int
    last_checked_date: Optional[str] = None
    log_id: Optional[int] = None
    is_source: Optional[bool] = True
 
class SnowflakeReadonlyListingTables(BaseModel):
    connection_id: int = 0
    Snowflake: dict
    destination_schema: str
    job_name: Optional[str] = "s3_snowflake_readonly_check"
    last_checked_date: Optional[str] = None
 
class SalesforceListingTables(BaseModel):
    job_name: str
    connection_id: int  # Include connection ID
    migration_session_id: int
    Salesforce: dict
    cmp_id: int
    user_id:int
    is_source:Optional[bool]
   
class SalesforceSandboxListingTables(BaseModel):
    job_name: str
    connection_id: int  # Include connection ID
    migration_session_id: int
    SalesforceSandbox: dict
    cmp_id: int
    user_id:int
    is_source:Optional[bool]
 
class GoogleReviewsListingTables(BaseModel):
    job_name: str
    connection_id: int  # Include connection ID
    migration_session_id: int
    GoogleReviews: dict
    cmp_id: int
    user_id:int
    is_source:Optional[bool]
 
class AmazonS3ListingTables(BaseModel):
    job_name: str
    connection_id: int  # Include connection ID
    migration_session_id: int
    AmazonS3: dict
    cmp_id: int
    user_id: int
    is_source: Optional[bool]
    last_checked_date: Optional[str]

class AmazonS3PatternPreviewRequest(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int
    AmazonS3: dict
    multi_files_prefix: Optional[str] = None
    max_results: Optional[int] = 50
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = None

class SFTPAuthorizationRequest(BaseModel):
    SFTP: dict
    is_source: Optional[bool] = None

async def _enqueue_listing_tables_request(request: Request, function_path: str, model_cls):
    """Enqueue update-schema listing-tables via Celery (same path as S3/CRM sources)."""
    payload = await request.json()
    _log_payload(f"Received {function_path} payload", payload)
    try:
        data = model_cls(**payload)
    except Exception as exc:
        logging.error("Payload validation failed for %s: %s", function_path, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await call_function_for_migration(data, function_path)


def _dispatch_source_helper_payload(function_path: str, payload: dict):
    """Run synchronous source helpers through CURRENT's direct dispatch table."""
    if CELERY_DIRECT_CONNECTOR_EXECUTION:
        return dispatch_direct_connector(function_path, payload)

    response = requests.post(
        CLOUD_FUNCTION_URLS[function_path],
        json=payload,
        headers=_internal_headers(),
        timeout=120,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"detail": response.text}


async def _handle_source_helper_request(request: Request, function_path: str):
    payload = await request.json()
    _log_payload(f"Received {function_path} payload", payload)
    try:
        return _dispatch_source_helper_payload(function_path, payload)
    except HTTPException:
        raise
    except requests.RequestException as exc:
        logging.error("Source helper forwarding failed path=%s error=%s", function_path, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("Source helper dispatch failed path=%s", function_path)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class SFTPListingTables(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int
    SFTP: dict
    cmp_id: int
    user_id: int
    is_source: Optional[bool] = True
    last_checked_date: Optional[str] = None
    log_id: Optional[int] = None

class SFTPPatternPreviewRequest(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int
    SFTP: dict
    multi_files_prefix: Optional[str] = None
    max_results: Optional[int] = 50
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = None

class ADLSPatternPreviewRequest(BaseModel):
    job_name: Optional[str] = None
    connection_id: int
    migration_session_id: Optional[int] = None
    AzureDataLakeStorage: Optional[dict] = None
    ADLS: Optional[dict] = None
    multi_files_prefix: Optional[str] = None
    max_results: Optional[int] = 50
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = None

def _adls_alias_path(request: Request, suffix: str) -> str:
    if request.url.path.endswith(f'/ADLS-{suffix}'):
        return f'/ADLS-{suffix}'
    return f'/AzureDataLakeStorage-{suffix}'


class ADLSListingTables(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int
    AzureDataLakeStorage: Optional[dict] = None
    ADLS: Optional[dict] = None
    cmp_id: int
    user_id: int
    is_source: Optional[bool] = True
    last_checked_date: Optional[str] = None
    log_id: Optional[int] = None

class GoogleDriveListingTables(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: int
    GoogleDrive: dict
    cmp_id: int
    user_id: int
    is_source: Optional[bool] = True
    last_checked_date: Optional[str] = None
    log_id: Optional[int] = None

class GoogleDrivePatternPreviewRequest(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: Optional[int] = None
    GoogleDrive: dict
    multi_files_prefix: Optional[str] = None
    max_results: Optional[int] = 50
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = None

# Celery task endpoint for listing tables
# @app.post('/MicrosoftDynamics365_FO-listing-tables')
# async def call_function_listing_tables(data: ListingTables):
#     return await call_function_for_migration(data, '/MicrosoftDynamics365_FO-listing-tables')
@app.post('/MicrosoftDynamics365_FO-listing-tables')
async def call_function_listing_tables(request: Request):
    # Log the raw payload
    payload = await request.json()
    logging.info(f"Received payload: {payload}")
 
    try:
        # Parse the payload into the Pydantic model
        data = ListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
 
    # If everything is fine, proceed with processing
    return await call_function_for_migration(data, '/MicrosoftDynamics365_FO-listing-tables')
 
@app.post('/Snowflake-listing-tables')
async def call_function_listing_tables_snowflake(request: Request):
    # Log the raw payload
    payload = await request.json()
    logging.info(f"Received payload: {payload}")
 
    try:
        # Parse the payload into the Pydantic model
        data = SnowflakeListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
 
    # If everything is fine, proceed with processing
    return await call_function_for_migration(data, '/Snowflake-listing-tables')

@app.post('/Databricks-listing-tables')
async def call_function_listing_tables_databricks(request: Request):
    payload = await request.json()
    logging.info(f"Received Databricks listing payload: {payload}")
    try:
        data = DatabricksListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
    return await call_function_for_migration(data, '/Databricks-listing-tables')


@app.post('/SAPOData-listing-tables')
async def call_function_listing_tables_sap_odata(request: Request):
    payload = await request.json()
    _log_payload("Received SAP OData listing payload", payload)
    try:
        data = SAPODataListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
    return await call_function_for_migration(data, '/SAPOData-listing-tables')


async def _forward_json_to_migration_function(request: Request, endpoint: str, *, timeout: int = 120):
    """Proxy a JSON POST from Django to migration-function."""
    payload = await request.json()
    _log_payload(f"Forwarding {endpoint} payload", payload)
    try:
        response = requests.post(
            get_api_url(endpoint),
            json=payload,
            headers=_internal_headers(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logging.error("Forwarding failed for %s: %s", endpoint, exc)
        return JSONResponse(
            status_code=502,
            content={"detail": f"Failed to reach migration-function for {endpoint}: {str(exc)}"},
        )

    try:
        content = response.json()
    except ValueError:
        content = {"detail": response.text}

    return JSONResponse(status_code=response.status_code, content=content)


@app.post('/sap-odata/services')
async def sap_odata_services(request: Request):
    return await _forward_json_to_migration_function(request, 'sap-odata/services')


@app.post('/sap-odata/service-entities')
async def sap_odata_service_entities(request: Request):
    return await _forward_json_to_migration_function(request, 'sap-odata/service-entities')


@app.post('/entity-transform/validate')
async def entity_transform_validate(request: Request):
    return await _forward_json_to_migration_function(
        request, 'entity-transform/validate'
    )


@app.post('/entity-transform/dry-run')
async def entity_transform_dry_run(request: Request):
    return await _forward_json_to_migration_function(
        request, 'entity-transform/dry-run', timeout=300
    )


@app.post('/source-script/validate')
async def source_script_validate(request: Request):
    return await _forward_json_to_migration_function(request, 'source-script/validate')


@app.post('/source-script/snowflake/dry-run')
async def source_script_snowflake_dry_run(request: Request):
    return await _forward_json_to_migration_function(
        request, 'source-script/snowflake/dry-run', timeout=300
    )
 
 
@app.post('/Snowflake-listing-tables-readonly')
async def call_function_listing_tables_snowflake_readonly(request: Request):
    payload = await request.json()
    _log_payload("Received readonly Snowflake payload", payload)
 
    try:
        data = SnowflakeReadonlyListingTables(**payload)
    except Exception as e:
        logging.error(f"Readonly Snowflake payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
 
    return await call_function_for_migration(data, '/Snowflake-listing-tables-readonly')
 
@app.post('/Salesforce-listing-tables')
async def call_function_listing_tables_salesforce(request: Request):
    # Log the raw payload
    payload = await request.json()
    logging.info(f"Received payload: {payload}")
 
    try:
        # Parse the payload into the Pydantic model
        data = SalesforceListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
 
    # If everything is fine, proceed with processing
    return await call_function_for_migration(data, '/Salesforce-listing-tables')
 
@app.post('/SalesforceSandbox-listing-tables')
async def call_function_listing_tables_salesforcesandbox(request: Request):
    # Log the raw payload
    payload = await request.json()
    logging.info(f"Received payload: {payload}")
 
    try:
        # Parse the payload into the Pydantic model
        data = SalesforceSandboxListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
 
    # If everything is fine, proceed with processing
    return await call_function_for_migration(data, '/SalesforceSandbox-listing-tables')
 
@app.post('/GoogleReviews-listing-tables')
async def call_function_listing_tables_googlereviews(request: Request):
    # Log the raw payload
    payload = await request.json()
    logging.info(f"Received payload: {payload}")
 
    try:
        # Parse the payload into the Pydantic model
        data = GoogleReviewsListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
 
    # If everything is fine, proceed with processing
    return await call_function_for_migration(data, '/GoogleReviews-listing-tables')
 
@app.post('/AmazonS3-listing-tables')
async def call_function_listing_tables_amazons3(request: Request):
    # Log the raw payload
    payload = await request.json()
    _log_payload("Received payload", payload)
 
    try:
        # Parse the payload into the Pydantic model
        data = AmazonS3ListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
 
    # If everything is fine, proceed with processing
    return await call_function_for_migration(data, '/AmazonS3-listing-tables')


@app.post('/AmazonS3-listing-files')
async def call_function_listing_files_amazons3(request: Request):
    payload = await request.json()
    _log_payload("Received payload", payload)

    try:
        response = requests.post(
            get_api_url('AmazonS3-listing-files'),
            json=payload,
            headers=_internal_headers(),
            timeout=120,
        )
    except requests.RequestException as exc:
        logging.error(f"AmazonS3 listing-files forwarding failed: {str(exc)}")
        return JSONResponse(
            status_code=502,
            content={"success": False, "message": f"Failed to reach S3 listing service: {str(exc)}"},
        )

    try:
        content = response.json()
    except ValueError:
        content = {"detail": response.text}

    return JSONResponse(status_code=response.status_code, content=content)


@app.post('/AmazonS3-preview-pattern')
async def call_function_preview_pattern_amazons3(request: Request):
    payload = await request.json()
    _log_payload("Received payload", payload)

    try:
        data = AmazonS3PatternPreviewRequest(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}

    return await call_function_for_migration(data, '/AmazonS3-preview-pattern')


@app.post('/AmazonS3-suggest-primary-keys')
async def call_function_suggest_primary_keys_amazons3(request: Request):
    payload = await request.json()
    _log_payload("Received payload", payload)

    try:
        response = requests.post(
            get_api_url('AmazonS3-suggest-primary-keys'),
            json=payload,
            headers=_internal_headers(),
            timeout=120,
        )
    except requests.RequestException as exc:
        logging.error(f"AmazonS3 PK suggestion forwarding failed: {str(exc)}")
        return JSONResponse(
            status_code=502,
            content={"success": False, "message": f"Failed to reach PK suggestion service: {str(exc)}"},
        )

    try:
        content = response.json()
    except ValueError:
        content = {"detail": response.text}

    return JSONResponse(status_code=response.status_code, content=content)


@app.post('/SFTP-Authorization')
async def call_function_authorize_sftp(request: Request):
    return await _handle_source_helper_request(request, '/SFTP-Authorization')

@app.post('/SFTP-listing-files')
async def call_function_listing_files_sftp(request: Request):
    return await _handle_source_helper_request(request, '/SFTP-listing-files')

@app.post('/SFTP-listing-tables')
async def call_function_listing_tables_sftp(request: Request):
    return await _enqueue_listing_tables_request(request, '/SFTP-listing-tables', SFTPListingTables)

@app.post('/SFTP-preview-pattern')
async def call_function_preview_pattern_sftp(request: Request):
    return await _handle_source_helper_request(request, '/SFTP-preview-pattern')


@app.post('/SFTP-suggest-primary-keys')
async def call_function_suggest_primary_keys_sftp(request: Request):
    return await _handle_source_helper_request(
        request, '/SFTP-suggest-primary-keys'
    )


@app.get('/Source-GoogleDrive-Authorization')
@app.get('/GoogleDrive-Authorization')
async def call_function_authorize_google_drive_source(request: Request):
    """Proxy Google Drive OAuth authorize URL generation to migration-function."""
    try:
        response = requests.get(
            get_api_url('Source-GoogleDrive-Authorization'),
            params=dict(request.query_params),
            timeout=60,
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"status_code": response.status_code, "body": response.text}
    except Exception as e:
        logging.error(f"Google Drive source authorization failed: {str(e)}")
        return {"error": "Google Drive source authorization failed", "details": str(e)}


@app.post('/AzureDataLakeStorage-listing-files')
@app.post('/ADLS-listing-files')
async def call_function_listing_files_adls(request: Request):
    return await _handle_source_helper_request(
        request, _adls_alias_path(request, 'listing-files')
    )


@app.post('/AzureDataLakeStorage-listing-tables')
@app.post('/ADLS-listing-tables')
async def call_function_listing_tables_adls(request: Request):
    return await _enqueue_listing_tables_request(
        request, _adls_alias_path(request, 'listing-tables'), ADLSListingTables
    )


@app.post('/AzureDataLakeStorage-preview-pattern')
@app.post('/ADLS-preview-pattern')
async def call_function_preview_pattern_adls(request: Request):
    return await _handle_source_helper_request(
        request, _adls_alias_path(request, 'preview-pattern')
    )


@app.post('/AzureDataLakeStorage-suggest-primary-keys')
@app.post('/ADLS-suggest-primary-keys')
async def call_function_suggest_primary_keys_adls(request: Request):
    return await _handle_source_helper_request(
        request, _adls_alias_path(request, 'suggest-primary-keys')
    )


@app.post('/GoogleDrive-listing-files')
async def call_function_listing_files_googledrive(request: Request):
    return await _handle_source_helper_request(request, '/GoogleDrive-listing-files')


@app.post('/GoogleDrive-listing-tables')
async def call_function_listing_tables_googledrive(request: Request):
    return await _enqueue_listing_tables_request(
        request, '/GoogleDrive-listing-tables', GoogleDriveListingTables
    )


@app.post('/GoogleDrive-preview-pattern')
async def call_function_preview_pattern_googledrive(request: Request):
    return await _handle_source_helper_request(
        request, '/GoogleDrive-preview-pattern'
    )


@app.post('/GoogleDrive-suggest-primary-keys')
async def call_function_suggest_primary_keys_googledrive(request: Request):
    return await _handle_source_helper_request(
        request, '/GoogleDrive-suggest-primary-keys'
    )


# Celery task endpoints for each migration function
@app.post('/Snowflake-MicrosoftDynamics-migration-yield')
async def call_function_migration_yield(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-MicrosoftDynamics-migration-yield')
 
@app.post('/Snowflake-MicrosoftDynamics365_FO-migration-yield')
async def call_function_migration_yield_f(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-MicrosoftDynamics365_FO-migration-yield')

@app.post('/Databricks-MicrosoftDynamics365_FO-migration-yield')
async def call_function_migration_yield_databricks_f(data: MigrationData):
    return await call_function_for_migration(data, '/Databricks-MicrosoftDynamics365_FO-migration-yield')

@app.post('/Databricks-Salesforce-migration-yield')
async def migration_salesforce_to_databricks(data: MigrationData):
    return await call_function_for_migration(data, '/Databricks-Salesforce-migration-yield')

@app.post('/Databricks-SalesforceSandbox-migration-yield')
async def migration_salesforce_sandbox_to_databricks(data: MigrationData):
    return await call_function_for_migration(data, '/Databricks-SalesforceSandbox-migration-yield')

@app.post('/Salesforce-Databricks-migration-yield')
async def migration_databricks_to_salesforce(data: MigrationData):
    return await call_function_for_migration(data, '/Salesforce-Databricks-migration-yield')

@app.post('/Salesforce-DatabricksSandbox-migration-yield')
async def migration_databricks_to_salesforce_sandbox(data: MigrationData):
    return await call_function_for_migration(data, '/Salesforce-DatabricksSandbox-migration-yield')
 
@app.post('/Salesforce-Snowflake-migration-yield')
async def migration_snowflake_to_salesforce(data: MigrationData):
    return await call_function_for_migration(data, '/Salesforce-Snowflake-migration-yield')
 
@app.post('/Snowflake-Salesforce-migration-yield')
async def migration_salesforce_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-Salesforce-migration-yield')
 
@app.post('/SalesforceSandbox-Snowflake-migration-yield')
async def migration_snowflake_to_sandbox(data: MigrationData):
    return await call_function_for_migration(data, '/SalesforceSandbox-Snowflake-migration-yield')
 
@app.post('/Snowflake-SalesforceSandbox-migration-yield')
async def migration_sandbox_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-SalesforceSandbox-migration-yield')
 
@app.post('/Snowflake-Snowflake-migration-yield')
async def migration_snowflake_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-Snowflake-migration-yield')
 
@app.post('/Snowflake-GoogleReviews-migration-yield')
async def migration_snowflake_to_googlereviews(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-GoogleReviews-migration-yield')
 
@app.post('/Snowflake-AmazonS3-migration-yield')
async def migration_snowflake_to_amazons3(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-AmazonS3-migration-yield')
 
@app.post('/SFTP-Snowflake-migration-yield')
async def migration_snowflake_to_sftp(data: MigrationData):
    return await call_function_for_migration(data, '/SFTP-Snowflake-migration-yield')


@app.post('/SharePoint-Snowflake-migration-yield')
async def migration_snowflake_to_sharepoint(data: MigrationData):
    return await call_function_for_migration(data, '/SharePoint-Snowflake-migration-yield')

@app.post('/Snowflake-SFTP-migration-yield')
async def migration_sftp_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-SFTP-migration-yield')


@app.post('/AmazonS3-Snowflake-migration-yield')
async def migration_snowflake_to_s3(data: MigrationData):
    return await call_function_for_migration(data, '/AmazonS3-Snowflake-migration-yield')


@app.post('/AzureDataLakeStorage-Snowflake-migration-yield')
async def migration_snowflake_to_adls(data: MigrationData):
    return await call_function_for_migration(data, '/AzureDataLakeStorage-Snowflake-migration-yield')


@app.post('/GoogleDrive-Snowflake-migration-yield')
async def migration_snowflake_to_google_drive(data: MigrationData):
    return await call_function_for_migration(data, '/GoogleDrive-Snowflake-migration-yield')


@app.post('/Snowflake-AzureDataLakeStorage-migration-yield')
async def migration_azure_data_lake_storage_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-AzureDataLakeStorage-migration-yield')


@app.post('/Snowflake-ADLS-migration-yield')
async def migration_adls_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-ADLS-migration-yield')


@app.post('/Snowflake-GoogleDrive-migration-yield')
async def migration_google_drive_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-GoogleDrive-migration-yield')


@app.post('/Snowflake-SAPOData-migration-yield')
async def migration_sap_odata_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-SAPOData-migration-yield')


@app.post('/AzureDataLakeStorage-SAPOData-migration-yield')
async def migration_sap_odata_to_adls(data: MigrationData):
    return await call_function_for_migration(data, '/AzureDataLakeStorage-SAPOData-migration-yield')


@app.post('/Databricks-SAPOData-migration-yield')
async def migration_sap_odata_to_databricks(data: MigrationData):
    return await call_function_for_migration(data, '/Databricks-SAPOData-migration-yield')


@app.post('/NetSuite-listing-tables')
async def call_function_listing_tables_netsuite(request: Request):
    payload = await request.json()
    _log_payload("Received NetSuite listing payload", payload)
    try:
        data = NetSuiteListingTables(**payload)
    except Exception as e:
        logging.error(f"Payload validation failed: {str(e)}")
        return {"error": "Payload validation failed", "details": str(e)}
    return await call_function_for_migration(data, '/NetSuite-listing-tables')


@app.post('/Snowflake-NetSuite-migration-yield')
async def migration_netsuite_to_snowflake(data: MigrationData):
    return await call_function_for_migration(data, '/Snowflake-NetSuite-migration-yield')


@app.post('/Databricks-NetSuite-migration-yield')
async def migration_netsuite_to_databricks(data: MigrationData):
    return await call_function_for_migration(data, '/Databricks-NetSuite-migration-yield')

async def call_function_for_migration(data: MigrationData, function_path: str):
    request_marker = id(data)
 
    print(
            f"🚪 | call_function_for_migration in fast-api.app.py called | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
            f"connection_id={data.connection_id} | job_name={data.job_name} | "
            f"migration_session_id={data.migration_session_id} | function_path={function_path}| request_marker={request_marker}\n"
        )
 
    # Define headers if required
    headers = _internal_headers()
 
    # Get the URL for this function path
    url = CLOUD_FUNCTION_URLS[function_path]
    #print(f"🔍 DEBUG call_function_for_migration: function_path='{function_path}' -> URL='{url}'")
    #print(f"🔍 DEBUG: API_BASE_URL='{API_BASE_URL}', USE_INTERNAL_API={USE_INTERNAL_API}")
 
    # Call the Celery task asynchronously
    task_id = str(uuid4())
    print(
        f"🆔 | EVENT=CELERY_TASK_ID_GENERATED | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
        f"job_name={data.job_name} | migration_session_id={data.migration_session_id} | "
        f"celery_task_id={task_id}| request_marker={request_marker}\n"
    )
    # print(f"🔍 DEBUG: Queuing Celery task with task_id='{task_id}'")
    # print(f"🔍 DEBUG: Task URL='{url}'")
    # print(f"🔍 DEBUG: Job name='{data.job_name}'")
    
    is_migration_path = function_path in MIGRATION_FUNCTION_PATHS
    is_listing_path = function_path in LISTING_FUNCTION_PATHS
    execution_order = str(getattr(data, "execution_order", "") or "").lower()
    is_parallel = execution_order == "parallel"
    selected_tables = getattr(data, "selected_tables", None)
    selected_tables_count = len(selected_tables) if isinstance(selected_tables, list) else 0
    # Single-table reload/refresh must use orchestrate_migration → call_function_child
    # (not monolithic call_function). Previously required >1 table, which forced reload
    # onto the long-running call_function path.
    can_fanout = (
        CELERY_FANOUT_ENABLED
        and is_migration_path
        and is_parallel
        and isinstance(selected_tables, list)
        and selected_tables_count >= 1
    )
    print(
        f"🧭 | EVENT=CELERY_FANOUT_DECISION | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
        f"job_name={data.job_name} | migration_session_id={data.migration_session_id} | "
        f"execution_order={execution_order or 'missing'} | require_parallel=True | "
            f"selected_tables_count={selected_tables_count if isinstance(selected_tables, list) else 'non_list'} | "
        f"can_fanout={can_fanout} | request_marker={request_marker}\n"
    )
    try:
        # Verify Celery app configuration
        # print(f"🔍 DEBUG: Celery broker: {celery_app.conf.broker_url}")
        # print(f"🔍 DEBUG: Celery backend: {celery_app.conf.result_backend}")
        # print(f"🔍 DEBUG: Registered tasks: {list(celery_app.tasks.keys())}")
        enqueue_task_name = "orchestrate_migration" if can_fanout else "call_function"
        print(
            f"📤 | EVENT=CELERY_ENQUEUE_INTENT | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
            f"task={enqueue_task_name} | "
            f"connection_id={data.connection_id} | job_name={data.job_name} | "
            f"migration_session_id={data.migration_session_id} | celery_task_id={task_id} | url={url}| request_marker={request_marker}\n"
        )

        payload = _enforce_cutover_contract(data.dict(), function_path)
        payload['celery_direct_connector_execution'] = CELERY_DIRECT_CONNECTOR_EXECUTION
        payload['celery_http_fallback_enabled'] = CELERY_HTTP_FALLBACK_ENABLED
        if can_fanout:
            payload['celery_fanout_mode'] = True
            payload['connector_type'] = CONNECTOR_TYPE_BY_PATH.get(function_path, 'unknown')
            task = orchestrate_migration.apply_async(
                args=[url, payload, headers, data.job_name, data.connection_id, function_path, is_listing_path],
                task_id=task_id,
                queue=_orchestrate_enqueue_queue(),
                priority=CELERY_ORCHESTRATE_TASK_PRIORITY,
            )
        else:
            task = call_function.apply_async(
                args=[url, payload, headers, data.job_name, data.connection_id],
                task_id=task_id,
                queue=_orchestrate_enqueue_queue(),
                priority=CELERY_ORCHESTRATE_TASK_PRIORITY,
            )
        print(
            f"✅ | EVENT=CELERY_ENQUEUED | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
            f"task={enqueue_task_name} | "
            f"job_name={data.job_name} | migration_session_id={data.migration_session_id} | "
            f"celery_task_id={task.id} | celery_state={task.state}| request_marker={request_marker}\n"
        )
       
        # print(f"✅ DEBUG: Celery task queued successfully. Task ID: {task.id}")
        # print(f"✅ DEBUG: Task name: {call_function.name}")
        # print(f"✅ DEBUG: Task queue: default")
        # print(f"✅ DEBUG: Task state: {task.state}")
    except Exception as e:
        # print(f"❌ DEBUG: Failed to queue Celery task: {e}")
        # import traceback
        # print(f"❌ DEBUG: Traceback: {traceback.format_exc()}")
        print(
            f"❌ | EVENT=CELERY_ENQUEUE_FAILED | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
            f"job_name={data.job_name} | migration_session_id={data.migration_session_id} | "
            f"error={str(e)} | request_marker={request_marker}\n"
        )
        raise
 
    # Update the database with job_name, task_id, and status as "in_progress"
    with SessionLocal() as db:
        db.execute(
            text("""
                UPDATE migration_record
                SET task_id = :task_id, status = :status
                WHERE job_name = :job_name AND migration_session_id = :migration_session_id
            """),
            {"job_name": data.job_name, "task_id": task.id, "status": "in_progress", "migration_session_id": data.migration_session_id},
        )
        db.commit()
        print(
            f"🧾 | EVENT=DB_UPDATED | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
            f"job_name={data.job_name} | migration_session_id={data.migration_session_id} | "
            f"task_id={task.id} | status=in_progress | request_marker={request_marker}\n"
        )
        # ✅ TRIGGER WebSocket push immediately when status becomes in_progress
        # (session detail + connection overview + company list) so UI shows migration in progress without polling.
        try:
            from websocket_utils import (
                trigger_connection_activity_log_update,
                trigger_connection_ws_status_update,
                trigger_ws_status_update,
                trigger_company_connections_status_update,
            )
            trigger_ws_status_update(data.migration_session_id)
            trigger_connection_ws_status_update(data.connection_id)
            trigger_connection_activity_log_update(data.connection_id)
            if data.cmp_id:
                trigger_company_connections_status_update(data.cmp_id)
            print(
                f"🔔 | EVENT=WS_TRIGGERED | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
                f"connection_id={data.connection_id} | job_name={data.job_name}| request_marker={request_marker}\n"
            )
        except Exception as ws_err:
            # print(f"⚠️ WebSocket trigger failed: {ws_err}")
            print(
                f"⚠️ | EVENT=WS_TRIGGER_FAILED | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
                f"connection_id={data.connection_id} | job_name={data.job_name} | "
                f"error={str(ws_err)}| request_marker={request_marker}\n"
            )
    print(
        f"🚪 | EVENT=FASTAPI_EXIT | pid={os.getpid()} | time={datetime.utcnow().isoformat()} | "
        f"job_name={data.job_name} | migration_session_id={data.migration_session_id} | "
        f"task_id={task.id}| request_marker={request_marker}\n"
    )
    # Return a response with task id and status
    return JSONResponse(content={"task_id": task.id, "status": "Migration started"})
 



