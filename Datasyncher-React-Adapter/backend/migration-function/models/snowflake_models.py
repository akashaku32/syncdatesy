from pydantic import BaseModel
from typing import Optional,Dict,List

class SnowflakeDetails(BaseModel):
    user: str
    password: Optional[str] = None  # Optional for key-pair authentication
    account: str
    database: Optional[str] = None  # Optional field
    warehouse: Optional[str] = None  # Optional field
    snowflake_schema: Optional[str] = None
    authentication_type: Optional[str] = "password"  # Default to password
    private_key: Optional[str] = None  # Required for key-pair authentication
    passphrase: Optional[str] = None  # Optional passphrase for private key
class EngineRequest(BaseModel):
    Snowflake: SnowflakeDetails
    destination_schema: Optional[str] = None

class ListingTables(BaseModel):
    connection_id: int  # Changed to int
    Snowflake: Dict[str, str]
    job_name: Optional[str]  # Adjust the type as needed based on the connection data structure
    last_checked_date: Optional[str] = None
    destination_schema: Optional[str] = None
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates

class SnowflakeMigration(BaseModel):
    """Model for Snowflake to Snowflake migration"""
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
    drop_and_reload: Optional[bool] = False  # Flag for drop and reload operation
    trigger_type: Optional[str] = "manual"  # Added trigger_type field with default "manual"