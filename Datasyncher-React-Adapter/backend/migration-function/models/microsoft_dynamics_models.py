# models/microsoft_dynamics_models.py
from pydantic import BaseModel
from typing import Optional, Dict,List

class MicrosoftDynamicsSourceConnection(BaseModel):
    MicrosoftDynamics365_FO: Dict[str, str] 

class ListingTables(BaseModel):
    connection_id: int  # Changed to int
    MicrosoftDynamics365_FO: Dict[str, str]
    job_name: Optional[str]  # Adjust the type as needed based on the connection data structure
    last_checked_date: Optional[str] = None  # Can be None if not provided
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates

class MigrationRequest(BaseModel):
    """
    destination_config for Dynamics → Databricks needs core connection fields only
    (server_hostname, http_path, access_token, catalog). Load engine is chosen at
    runtime: Spark Job for initial bulk load, SQL warehouse for incremental syncs.

    Legacy optional keys still honored when present on saved destinations:
      load_engine: force "spark_job" or "sql_warehouse" for all Dynamics loads
      spark_job_id / spark_job_name: override auto-provisioned Databricks Job
    """
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
    connection_name: Optional[str] = None  # Connection name for cleanup and logging
    drop_and_reload: Optional[bool] = False  # Flag for drop and reload operation
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates
    celery_child_mode: Optional[bool] = False
    table_group_index: Optional[int] = None
    parent_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    class Config:
        extra = "ignore"  # Ignore extra fields instead of raising validation error