from typing import Any, Dict, Optional

from pydantic import BaseModel


class SFTPAuthorizationRequest(BaseModel):
    """
    Supports multiple payload shapes sent by Django/clients.

    Examples:
    - {"SFTP": {...}}
    - {"destination_config": {...}}
    - {"config": {...}}
    """

    SFTP: Optional[Dict[str, Any]] = None
    destination_config: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None
    is_source: Optional[bool] = None

    class Config:
        extra = "ignore"

