from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class SAPODataAuthorizationRequest(BaseModel):
    """
    Pydantic request model for SAP OData authorization and connectivity tests.
    
    Supports payload format:
    {
        "SAPOData": {
            "host": "sap.company.com",
            "port": 443,
            "username": "SAPUSER",
            "password": "password",
            "connection_method": "direct",
            "proxy_agent": null
        }
    }
    """

    SAPOData: Optional[Dict[str, Any]] = None

    class Config:
        extra = "ignore"


class SAPODataADLSMigrationRequest(BaseModel):
    """
    Pydantic request model for SAP OData to ADLS Gen2 migration.
    """
    source_config: Dict[str, Any]
    destination_config: Dict[str, Any]

    connection_id: int
    cmp_id: int
    user_id: int
    session: int

    selected_tables: List[str]
    table_export_settings: Optional[Dict[str, Any]] = None
    safety_interval: str
    job_name: Optional[str] = None
    migration_session_id: int

    execution_order: str
    chunk_count: int
    logs_creation: bool = False
    connection_name: Optional[str] = None
    company_name: Optional[str] = None
    drop_and_reload: Optional[bool] = False

    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    log_id: Optional[int] = None
    celery_child_mode: Optional[bool] = False
    celery_fanout_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    class Config:
        extra = "ignore"


class SAPODataSnowflakeMigrationRequest(BaseModel):
    """
    Pydantic request model for SAP OData to Snowflake migration.

    Follows the same structure as SalesforceSnowflakeMigration and
    SAPODataADLSMigrationRequest.  Staging uses GCS + Snowflake
    STORAGE_INTEGRATION (matching Salesforce / D365 pattern).
    """
    source_config: Dict[str, Any]
    """SAP Gateway connection: host, port, username, password, connection_method, proxy_agent."""

    destination_config: Dict[str, Any]
    """Snowflake destination: account, username, password/private_key, warehouse, database, schema, role."""

    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    migration_session_id: int

    selected_tables: List[str]
    """Entity-set names to replicate, e.g. ['SalesOrderSet', 'BusinessPartnerSet']."""

    table_export_settings: Optional[Dict[str, Any]] = None
    """Per-table overrides: load_method, filter_expr, chunk_count, etc."""

    safety_interval: str = "120"
    job_name: Optional[str] = None
    connection_name: Optional[str] = None
    company_name: Optional[str] = None

    execution_order: str = "sequential"
    """'sequential' or 'parallel'."""

    chunk_count: int = 50000
    """OData page / GCS batch size in rows."""

    drop_and_reload: Optional[bool] = False
    logs_creation: bool = False

    # System audit column name aliases (Fivetran-compatible defaults)
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"

    # Retry / idempotency tracking
    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    log_id: Optional[int] = None
    celery_child_mode: Optional[bool] = False
    celery_fanout_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    class Config:
        extra = "ignore"


class SAPODataDatabricksMigrationRequest(BaseModel):
    """
    Pydantic request model for SAP OData to Databricks migration.

    Same payload shape as SAPODataSnowflakeMigrationRequest; destination_config
    holds Databricks SQL warehouse credentials (server_hostname, http_path,
    access_token, catalog, schema / Unity Catalog schema).
    """
    source_config: Dict[str, Any]
    """SAP Gateway connection: host, port, username, password, connection_method, proxy_agent."""

    destination_config: Dict[str, Any]
    """Databricks destination: server_hostname, http_path, access_token, catalog, schema."""

    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    migration_session_id: int

    selected_tables: List[str]
    """Entity-set names to replicate, e.g. ['SalesOrderSet', 'BusinessPartnerSet']."""

    table_export_settings: Optional[Dict[str, Any]] = None
    """Per-table overrides: load_method, filter_expr, chunk_count, etc."""

    safety_interval: str = "120"
    job_name: Optional[str] = None
    connection_name: Optional[str] = None
    company_name: Optional[str] = None

    execution_order: str = "sequential"
    """'sequential' or 'parallel'."""

    chunk_count: int = 50000
    """OData page / staging batch size in rows."""

    drop_and_reload: Optional[bool] = False
    logs_creation: bool = False

    # System audit column name aliases (Fivetran-compatible defaults)
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"

    # Retry / idempotency tracking
    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    log_id: Optional[int] = None
    celery_child_mode: Optional[bool] = False
    celery_fanout_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    class Config:
        extra = "ignore"

