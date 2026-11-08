"""Parse and validate NetSuite source configuration."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from fastapi import HTTPException

METHOD_SUITEQL = "suiteql_rest"
METHOD_SUITEANALYTICS = "suiteanalytics"
VALID_METHODS = frozenset({METHOD_SUITEQL, METHOD_SUITEANALYTICS})

AUTH_CONNECT_TBA = "tba"
AUTH_CONNECT_OAUTH2 = "oauth2_m2m"
AUTH_CONNECT_EMAIL = "email_password"
VALID_CONNECT_AUTH = frozenset({AUTH_CONNECT_TBA, AUTH_CONNECT_OAUTH2, AUTH_CONNECT_EMAIL})


def normalize_config(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    if not raw:
        raise HTTPException(status_code=422, detail="NetSuite configuration is required.")
    config = dict(raw)
    method = str(config.get("connection_method") or METHOD_SUITEQL).strip().lower()
    if method not in VALID_METHODS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid connection_method '{method}'. Expected suiteql_rest or suiteanalytics.",
        )
    config["connection_method"] = method
    account_id = str(config.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=422, detail="account_id is required.")
    config["account_id"] = account_id
    return config


def validate_for_method(config: Dict[str, Any]) -> None:
    method = config["connection_method"]
    if method == METHOD_SUITEQL:
        for field in ("client_id", "certificate_id", "private_key"):
            if not str(config.get(field) or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} is required for SuiteQL (REST) connections.",
                )
        return

    if not str(config.get("role_id") or "").strip():
        raise HTTPException(status_code=422, detail="role_id is required for SuiteAnalytics Connect.")

    auth_type = str(config.get("connect_auth_type") or AUTH_CONNECT_TBA).strip().lower()
    if auth_type not in VALID_CONNECT_AUTH:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid connect_auth_type '{auth_type}'. Expected tba, oauth2_m2m, or email_password.",
        )
    config["connect_auth_type"] = auth_type

    if auth_type == AUTH_CONNECT_EMAIL:
        for field in ("logon_email", "connect_password"):
            if not str(config.get(field) or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} is required for SuiteAnalytics email/password login.",
                )
    elif auth_type == AUTH_CONNECT_OAUTH2:
        for field in ("connect_client_id", "connect_certificate_id", "connect_private_key"):
            if not str(config.get(field) or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} is required for SuiteAnalytics OAuth 2.0 M2M.",
                )
    else:
        for field in ("consumer_key", "consumer_secret", "token_id", "token_secret"):
            if not str(config.get(field) or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} is required for SuiteAnalytics Connect TBA.",
                )


def suiteanalytics_host(account_id: str) -> str:
    realm = account_id.replace("_", "-").lower()
    return f"{realm}.connect.api.netsuite.com"


def suitetalk_base_url(account_id: str) -> str:
    realm = account_id.replace("_", "-").lower()
    return f"https://{realm}.suitetalk.api.netsuite.com"


def connect_account_id(account_id: str) -> str:
    """Account id for SuiteAnalytics Connect (underscores, uppercase)."""
    return account_id.strip().upper()


def method_label(config: Dict[str, Any]) -> Tuple[str, str]:
    method = config["connection_method"]
    if method == METHOD_SUITEQL:
        return method, "SuiteQL REST"
    return method, "SuiteAnalytics Connect"
