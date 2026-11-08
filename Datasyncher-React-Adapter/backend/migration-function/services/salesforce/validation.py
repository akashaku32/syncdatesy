"""Shared Salesforce connection validation for FastAPI routers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from services.salesforce.client import salesforce_connection
from utils.salesforce_jwt_auth import is_salesforce_jwt_auth

logger = logging.getLogger(__name__)


def _unwrap_salesforce_payload(salesforce_data: Any) -> Dict[str, Any]:
    if salesforce_data is None:
        raise ValueError("Salesforce connection data is required")

    if isinstance(salesforce_data, str):
        salesforce_data = json.loads(salesforce_data)

    if not isinstance(salesforce_data, dict):
        raise ValueError(f"Invalid Salesforce data type: {type(salesforce_data)}")

    if len(salesforce_data) == 1 and isinstance(next(iter(salesforce_data.values())), dict):
        return next(iter(salesforce_data.values()))
    return salesforce_data


def validate_salesforce_connection_payload(
    salesforce_data: Any,
    *,
    environment: str,
) -> Dict[str, Any]:
    """
    Validate Salesforce source/destination config.

    Returns dict with status_code and body; JWT mode also returns token fields in body.
    """
    try:
        inner_dict = _unwrap_salesforce_payload(salesforce_data)
    except json.JSONDecodeError as exc:
        return {"status_code": 400, "body": f"Invalid JSON in Salesforce field: {exc}"}
    except ValueError as exc:
        return {"status_code": 400, "body": str(exc)}

    custom_domain = inner_dict.get("CUSTOM_DOMAIN", "")

    if is_salesforce_jwt_auth(inner_dict):
        client_id = inner_dict.get("CLIENT_ID") or inner_dict.get("client_id")
        username = inner_dict.get("integration_username") or inner_dict.get("username")
        private_key = inner_dict.get("private_key")
        passphrase = inner_dict.get("passphrase")

        if not client_id:
            return {"status_code": 400, "body": "CLIENT_ID is required for JWT Bearer auth"}
        if not username:
            return {
                "status_code": 400,
                "body": "integration_username is required for JWT Bearer auth",
            }
        if not private_key:
            return {"status_code": 400, "body": "private_key is required for JWT Bearer auth"}

        # connection_id is absent during create/validate; still pass credentials so
        # salesforce_connection selects the JWT branch (build_salesforce_oauth_context
        # returns None when connection_id is missing).
        oauth_context = {
            "connection_id": None,
            "role": "source",
            "credentials": inner_dict,
        }
        try:
            sf_connection = salesforce_connection(
                state="test",
                access_token="",
                instance_url=inner_dict.get("instance_url") or "https://login.salesforce.com",
                refresh_token="",
                CLIENT_ID=client_id,
                CLIENT_SECRET="",
                environment=environment,
                custom_domain=custom_domain,
                oauth_context=oauth_context,
            )
        except Exception as exc:
            return {"status_code": 401, "body": str(exc)}

        if not sf_connection:
            return {
                "status_code": 500,
                "body": "Failed to establish Salesforce JWT connection",
            }

        instance = getattr(sf_connection, "sf_instance", None) or inner_dict.get("instance_url")
        if instance and not str(instance).startswith("https://"):
            instance = f"https://{instance}"

        return {
            "status_code": 200,
            "body": "Salesforce JWT Bearer connection established successfully",
            "access_token": getattr(sf_connection, "session_id", ""),
            "instance_url": instance,
            "authentication_type": "jwt_bearer",
        }

    access_token = inner_dict.get("access_token", "")
    instance_url = inner_dict.get("instance_url", "")
    refresh_token = inner_dict.get("refresh_token", "")
    client_id = inner_dict.get("CLIENT_ID", "")
    client_secret = inner_dict.get("CLIENT_SECRET", "")

    if not access_token:
        return {"status_code": 400, "body": "Access token is required"}
    if not instance_url:
        return {"status_code": 400, "body": "Instance URL is required"}
    if not refresh_token:
        return {"status_code": 400, "body": "Refresh token is required"}
    if not client_id:
        return {"status_code": 400, "body": "CLIENT_ID is required for token refresh"}
    if not client_secret:
        return {"status_code": 400, "body": "CLIENT_SECRET is required for token refresh"}
    if not instance_url.startswith("https://") or ".salesforce.com" not in instance_url:
        return {"status_code": 400, "body": "Invalid Salesforce instance URL format"}

    try:
        sf_connection = salesforce_connection(
            state="test",
            access_token=access_token,
            instance_url=instance_url,
            refresh_token=refresh_token,
            CLIENT_ID=client_id,
            CLIENT_SECRET=client_secret,
            environment=environment,
            custom_domain=custom_domain,
        )
    except Exception as exc:
        return {"status_code": 401, "body": str(exc)}

    if sf_connection:
        logger.info("Salesforce OAuth connection established successfully")
        return {
            "status_code": 200,
            "body": "Salesforce connection established successfully",
        }

    return {
        "status_code": 500,
        "body": "Failed to establish Salesforce connection - no connection object returned",
    }
