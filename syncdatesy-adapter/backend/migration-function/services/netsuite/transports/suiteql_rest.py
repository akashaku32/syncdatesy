"""SuiteQL over NetSuite REST with OAuth 2.0 certificate (M2M) authentication."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests
from fastapi import HTTPException

from services.netsuite.config import suitetalk_base_url
from services.netsuite.oauth_m2m import request_m2m_access_token

logger = logging.getLogger(__name__)

SUITEQL_PATH = "/services/rest/query/v1/suiteql"


class SuiteQLRestTransport:
    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._account_id = config["account_id"]
        self._base = suitetalk_base_url(self._account_id)
        self._suiteql_url = f"{self._base}{SUITEQL_PATH}"
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0

    def close(self) -> None:
        return None

    def _refresh_token(self) -> None:
        self._access_token = request_m2m_access_token(self._config)
        self._token_expires_at = time.time() + 3540

    def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        self._refresh_token()
        if not self._access_token:
            raise HTTPException(status_code=502, detail="Failed to obtain NetSuite access token.")
        return self._access_token

    def run_query(
        self,
        sql: str,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        token = self._ensure_token()
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "prefer": "transient",
        }
        attempt = 0
        while attempt < 4:
            attempt += 1
            try:
                response = requests.post(
                    self._suiteql_url,
                    headers=headers,
                    json={"q": sql},
                    params=params or None,
                    timeout=120,
                )
            except requests.RequestException as exc:
                raise HTTPException(status_code=502, detail=f"SuiteQL request failed: {exc}") from exc
            if response.status_code == 429:
                time.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code == 401:
                self._refresh_token()
                headers["Authorization"] = f"Bearer {self._access_token}"
                continue
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"SuiteQL failed: {response.text[:500]}",
                )
            data = response.json()
            items = data.get("items") or []
            if not isinstance(items, list):
                return []
            return [dict(row) if isinstance(row, dict) else {"value": row} for row in items]
        raise HTTPException(status_code=429, detail="NetSuite SuiteQL rate limit exceeded.")
