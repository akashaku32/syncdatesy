"""Internal service-key auth for migration-function HTTP routes."""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException


async def verify_internal_token(
    x_internal_key: Optional[str] = Header(default=None, alias="X-INTERNAL-KEY"),
) -> None:
    expected = os.getenv("INTERNAL_API_KEY") or ""
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="INTERNAL_API_KEY is not configured",
        )
    provided = x_internal_key or ""
    if not provided or not hmac.compare_digest(str(provided), str(expected)):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing internal service key",
        )
