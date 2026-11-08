from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class SnowflakeSharePointMigrationRequest(BaseModel):
    """
    Snowflake (source) -> SharePoint (destination) migration request.

    Matches the common migration-function request shape used by other connectors:
    - source_config / destination_config dictionaries
    - connection/session identifiers for DB logging + websocket push
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
    celery_child_mode: bool = False

    model_config = ConfigDict(extra="ignore")
