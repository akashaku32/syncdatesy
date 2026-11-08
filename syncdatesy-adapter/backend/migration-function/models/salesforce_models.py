from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Union

from pydantic import BaseModel, validator
class SalesforceConnection(BaseModel):
    """Model for Salesforce connection details."""
    
    access_token: str  # Salesforce requires a security token along with the password
    instance_url: str  # Instance URL for direct connections
    refresh_token: str  # Refresh token for OAuth
    CUSTOM_DOMAIN: Optional[str] = None  # Optional custom domain

class SalesforceValidationRequest(BaseModel):
    """Model for Salesforce connection validation requests."""
    Salesforce: Optional[Any] = None

class SalesforceSandboxValidationRequest(BaseModel):
    """Model for Salesforce Sandbox connection validation requests."""
    SalesforceSandbox: Optional[Any] = None

class SalesforceListing(BaseModel):
    """Model for fetching Salesforce data such as object lists."""
    connection_id: int  # Connection ID, for reference
    job_name: Optional[str] = None  # Optional job name for tracking or logging
    last_checked_date: Optional[str] = None  # Optional date of last check for records
    # OAuth callbacks may persist optional metadata keys as null; listing must accept them.
    Salesforce: Optional[Dict[str, Any]] = None
    cmp_id: int
    user_id:int
    is_source: Optional[bool] = None  
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates

class SalesforceSandboxListing(BaseModel):
    """Model for fetching Salesforce data such as object lists."""
    connection_id: int  # Connection ID, for reference
    job_name: Optional[str] = None  # Optional job name for tracking or logging
    last_checked_date: Optional[str] = None  # Optional date of last check for records
    SalesforceSandbox: Optional[Dict[str, Any]] = None
    cmp_id: int
    user_id:int
    is_source: Optional[bool] = None 
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates
class SalesforceMigration(BaseModel):
    """Model for fetching Salesforce data such as object lists."""
    source_config: Dict[str, Optional[str]]
    destination_config: Dict[str, Optional[str]]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"
    safety_interval: str
    selected_tables: List[List[str]]
    job_name: Optional[str]
    migration_session_id: int
    execution_order:str
    chunk_count: int
    logs_creation : bool
    connection_name: str
    company_name:str
    # ✅ Retry attempt tracking fields (prevent duplicate emails)
    retry_attempt: int = 0  # Default to 0 (first attempt)
    max_retries: int = 2    # Default to 2 (total 3 attempts: 1 initial + 2 retries)
    is_final_attempt: bool = True  # Default to True for backward compatibility
    drop_and_reload: Optional[bool] = False  # Flag for drop and reload operation
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates
    celery_child_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    @validator("selected_tables", pre=True)
    def normalize_selected_tables(cls, value):
        """
        Accept UI / fan-out shapes:
          - [['Account', 'Account__c'], ...]  (canonical)
          - ['Account', 'Contact', ...]       (flat table list → source=dest)
          - [['Account']] per child          (single table in nested list)
        """
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("selected_tables must be a list")

        normalized: List[List[str]] = []
        for entry in value:
            if entry is None:
                continue
            if isinstance(entry, (list, tuple)):
                if not entry:
                    continue
                if len(entry) >= 2 and not isinstance(entry[0], (list, tuple)):
                    src, dest = entry[0], entry[1]
                else:
                    src = entry[0]
                    dest = src
                normalized.append([str(src), str(dest)])
            elif isinstance(entry, str):
                normalized.append([entry, entry])
            else:
                normalized.append([str(entry), str(entry)])
        return normalized
    
    class Config:
        extra = "ignore"  # Ignore extra fields without validation errors

class SalesforceSnowflakeMigration(BaseModel):
    source_config: Dict[str, Optional[str]]
    destination_config: Dict[str, Optional[str]]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"
    safety_interval: str
    selected_tables: List[str]
    job_name: Optional[str]
    migration_session_id: int
    execution_order:str
    chunk_count: int
    logs_creation : bool
    connection_name: str
    company_name:str
    last_synced: Optional[str] = None
    # ✅ Retry attempt tracking fields (prevent duplicate emails)
    retry_attempt: int = 0  # Default to 0 (first attempt)
    max_retries: int = 2    # Default to 2 (total 3 attempts: 1 initial + 2 retries)
    is_final_attempt: bool = True  # Default to True for backward compatibility
    drop_and_reload: Optional[bool] = False  # Flag for drop and reload operation
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates
    celery_child_mode: Optional[bool] = False
    celery_fanout_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    class Config:
        extra = "ignore"  # Ignore extra fields without validation errors


class SalesforceDatabricksMigration(BaseModel):
    """Salesforce (prod/sandbox) → Databricks payload; mirrors Snowflake SF model."""

    source_config: Dict[str, Optional[str]]
    destination_config: Dict[str, Optional[str]]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"
    safety_interval: str
    selected_tables: List[str]
    job_name: Optional[str]
    migration_session_id: int
    execution_order: str
    chunk_count: int
    logs_creation: bool
    connection_name: str
    company_name: str
    last_synced: Optional[str] = None
    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    drop_and_reload: Optional[bool] = False
    log_id: Optional[int] = None
    celery_child_mode: Optional[bool] = False
    celery_fanout_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    class Config:
        extra = "ignore"

