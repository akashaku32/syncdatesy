from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class GoogleReviewsAuthDetails(BaseModel):
    state: str
    CLIENT_ID: Optional[str] = None
    CLIENT_SECRET: Optional[str] = None
    # REDIRECT_URI and SCOPE are now constants in the service


class GoogleReviewsValidationRequest(BaseModel):
    GoogleReviews: Optional[Any] = Field(default=None) 
    connection_id: Optional[int] = None
    job_name: Optional[str] = None
    is_source: Optional[bool] = False
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates
    

class GoogleReviewsMigration(BaseModel):
    source_config: Dict[str, Any] = Field(default_factory=dict)
    destination_config: Dict[str, Any] = Field(default_factory=dict)
    connection_id: Optional[int] = None
    job_name: Optional[str] = None
    migration_session_id: Optional[int] = None
    cmp_id: Optional[int] = None
    user_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    safety_interval: Optional[str] = "120"  # Default 2 minutes 
    selected_tables: Optional[List[str]] = Field(default_factory=list)
    attribute_dict: Optional[Dict[str, Any]] = Field(default_factory=dict)
    folderpath_dict: Optional[Dict[str, Any]] = Field(default_factory=dict)
    session: Optional[int] = None
    is_deleted: Optional[str] = None
    is_updated: Optional[str] = None
    last_checked_dates: Optional[Dict[str, Any]] = Field(default_factory=dict)
    execution_order: Optional[str] = None
    chunk_count: Optional[int] = None
    logs_creation: Optional[bool] = False
    connection_name: Optional[str] = None
    company_name: Optional[str] = None
    drop_and_reload: Optional[bool] = False  # Flag for drop and reload operation 
    log_id: Optional[int] = None  # Database ID of the log entry
    
    class Config:
        extra = "ignore"  # Ignore extra fields without validation errors



