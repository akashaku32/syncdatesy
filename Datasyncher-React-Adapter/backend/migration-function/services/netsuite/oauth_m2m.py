"""OAuth 2.0 M2M token exchange for NetSuite (REST / SuiteAnalytics)."""

from __future__ import annotations

import time
from typing import Any, Dict

import jwt
import requests
from fastapi import HTTPException

from services.netsuite.config import suitetalk_base_url
from services.snowflake.service import normalize_pem_key

TOKEN_PATH = "/services/rest/auth/oauth2/v1/token"
DEFAULT_SUITEQL_SCOPE = "rest_webservices"
DEFAULT_CONNECT_SCOPE = "suite_analytics"


def request_m2m_access_token(config: Dict[str, Any], *, scope: str | None = None) -> str:
    """Exchange JWT client assertion for an access token."""
    client_id = str(config.get("client_id") or config.get("connect_client_id") or "").strip()
    cert_id = str(config.get("certificate_id") or config.get("connect_certificate_id") or "").strip()
    pem_raw = config.get("private_key") or config.get("connect_private_key")
    pem = normalize_pem_key(str(pem_raw or ""))
    if not client_id or not cert_id or not pem:
        raise HTTPException(status_code=422, detail="OAuth M2M requires client_id, certificate_id, and private_key.")

    account_id = str(config["account_id"]).strip()
    token_url = f"{suitetalk_base_url(account_id)}{TOKEN_PATH}"
    scope_value = (scope or config.get("oauth_scope") or DEFAULT_SUITEQL_SCOPE).strip()
    now = int(time.time())
    payload = {
        "iss": client_id,
        "scope": scope_value,
        "aud": token_url,
        "iat": now,
        "exp": now + 3600,
    }
    assertion = jwt.encode(
        payload,
        pem,
        algorithm="RS256",
        headers={"kid": cert_id},
    )
    try:
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"NetSuite token request failed: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"NetSuite token exchange failed: {response.text[:500]}",
        )
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="NetSuite token response missing access_token.")
    return str(token)
