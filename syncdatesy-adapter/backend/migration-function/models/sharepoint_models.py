from typing import Any, Dict, Optional

from pydantic import BaseModel


class SharePointAuthorizationRequest(BaseModel):
    """
    Supports multiple payload shapes sent by Django/clients.

    Examples:
    - {"SharePoint": {...}}
    - {"destination_config": {...}}
    - {"config": {...}}
    """

    SharePoint: Optional[Dict[str, Any]] = None
    destination_config: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None

    class Config:
        extra = "ignore"
