from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class NetSuiteAuthorizationRequest(BaseModel):
    """Validate NetSuite source credentials (SuiteQL REST or SuiteAnalytics Connect)."""

    NetSuite: Optional[Dict[str, Any]] = None

    class Config:
        extra = "ignore"


class NetSuiteListingTablesRequest(BaseModel):
    job_name: str
    connection_id: int
    migration_session_id: Optional[int] = None
    NetSuite: dict
    cmp_id: int
    user_id: int
    last_checked_date: Optional[str] = None
    log_id: Optional[int] = None

    class Config:
        extra = "ignore"


class NetSuiteSnowflakeMigrationRequest(BaseModel):
    source_config: Dict[str, Any]
    destination_config: Dict[str, Any]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    migration_session_id: int
    selected_tables: List[str]
    safety_interval: str = "120"
    job_name: Optional[str] = None
    connection_name: Optional[str] = None
    execution_order: str = "sequential"
    chunk_count: int = 1000
    drop_and_reload: Optional[bool] = False
    logs_creation: bool = False
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"
    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    log_id: Optional[int] = None
    celery_child_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    class Config:
        extra = "ignore"


class NetSuiteDatabricksMigrationRequest(NetSuiteSnowflakeMigrationRequest):
    """Same payload shape as Snowflake migration; destination_config holds Databricks fields."""

    class Config:
        extra = "ignore"
