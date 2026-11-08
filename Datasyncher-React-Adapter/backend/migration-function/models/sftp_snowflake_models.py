# models/sftp_snowflake_models.py
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, List, Any

class SFTPSnowflakeMigrationRequest(BaseModel):
    """Model for SFTP to Snowflake migration request.
    
    This model matches the structure of other sources (Salesforce, MicrosoftDynamics, S3).
    All SFTP-specific configuration (file_type, file_mapping_mode, load_method, etc.)
    is stored in source_config dictionary.
    """
    source_config: Dict[str, Any]  # Contains SFTP config: file_type, file_mapping_mode, load_method, delimiter, etc.
    destination_config: Dict[str, Any]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    selected_tables: List[str]  # List of file keys/paths
    safety_interval: str
    job_name: Optional[str] = None
    migration_session_id: int
    execution_order: str
    chunk_count: int
    logs_creation: bool = False
    connection_name: Optional[str] = None
    company_name: Optional[str] = None
    drop_and_reload: Optional[bool] = False
    
    # Common fields for consistency
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"
    
    # Retry attempt tracking fields
    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    log_id: Optional[int] = None
    
    celery_child_mode: bool = False

    model_config = ConfigDict(extra="ignore")

