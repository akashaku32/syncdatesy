from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class ADLSAuthorizationRequest(BaseModel):
    """
    Supports ADLS destination creation/update payloads sent by Django.

    Examples:
    - {"AzureDataLakeStorage": {...}}
    - {"ADLS": {...}}
    - {"destination_config": {...}}
    - {"config": {...}}
    """

    AzureDataLakeStorage: Optional[Dict[str, Any]] = None
    ADLS: Optional[Dict[str, Any]] = None
    destination_config: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")

    def resolved_config(self) -> Dict[str, Any]:
        for candidate in (
            self.destination_config,
            self.AzureDataLakeStorage,
            self.ADLS,
            self.config,
        ):
            if isinstance(candidate, dict):
                return candidate
        return {}


class SnowflakeADLSMigrationRequest(BaseModel):
    """
    Snowflake (source) -> ADLS Gen2 (destination) migration request.

    Matches the common migration-function request shape used by other
    file-based connectors.
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
