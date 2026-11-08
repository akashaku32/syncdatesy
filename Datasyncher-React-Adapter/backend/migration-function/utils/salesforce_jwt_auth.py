"""Salesforce JWT Bearer flow for unattended server-to-server authentication.

Customer setup (Salesforce admin):
1. Create an integration user with API access and required object permissions.
2. Connected App: enable "Use digital signatures" and upload the X.509 certificate
   that matches the private key stored in Datasyncher.
3. Pre-authorize the integration user for the Connected App.
4. In Datasyncher: authentication_type=jwt_bearer, Consumer Key (CLIENT_ID),
   integration_username, and private_key PEM.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import jwt
import requests

from utils.salesforce_oauth_persist import resolve_salesforce_token_url

logger = logging.getLogger(__name__)

JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
JWT_ASSERTION_TTL_SECONDS = 180
JWT_ACCESS_CACHE_TTL_SECONDS = 5400  # 90 min (SF tokens last ~2h)
JWT_CACHE_PREFIX = "sf_jwt_access"


def _jwt_exchange_error_hint(detail: str) -> Optional[str]:
    """Return an admin-facing hint for common Salesforce JWT exchange failures."""
    detail_low = str(detail or "").lower()
    if "invalid assertion" in detail_low:
        return (
            "Check: (1) Connected App Consumer Key matches CLIENT_ID, "
            "(2) private key matches uploaded certificate, "
            "(3) integration_username is exact SF username and pre-authorized, "
            "(4) production vs sandbox connector matches org "
            "(prod aud=login.salesforce.com, sandbox aud=test.salesforce.com)."
        )
    if (
        "refresh_token scope is required" in detail_low
        or "preauthorized" in detail_low
        or "pre-authorized" in detail_low
        or "pre authorized" in detail_low
    ):
        return (
            "In Salesforce Connected App / External Client App: "
            "(1) add OAuth scopes 'Perform requests at any time (refresh_token, offline_access)' "
            "and 'Manage user data via APIs (api)', "
            "(2) set Permitted Users to 'Admin approved users are pre-authorized', "
            "(3) assign the integration user's Profile or Permission Set under App Policies."
        )
    if "invalid_client" in detail_low or "invalid client" in detail_low:
        return "Verify CLIENT_ID matches the Connected App Consumer Key in this org."
    if "user hasn't approved" in detail_low or "user has not approved" in detail_low:
        return (
            "Pre-authorize the integration user for this Connected App, or complete a one-time "
            "OAuth authorization for that user with refresh_token scope enabled."
        )
    return None


def format_salesforce_jwt_exchange_error(detail: str) -> str:
    """Build a user-facing JWT exchange failure message with optional admin hint."""
    detail_text = str(detail or "").strip()
    hint = _jwt_exchange_error_hint(detail_text)
    base = f"Salesforce JWT token exchange failed: {detail_text}"
    return f"{base}. {hint}" if hint else base


def is_salesforce_jwt_auth(credentials: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(credentials, dict):
        return False
    auth_type = str(credentials.get("authentication_type") or "oauth").strip().lower()
    return auth_type in ("jwt_bearer", "jwt", "jwt-bearer")


def resolve_jwt_audience(
    environment: str,
    custom_domain: str = "",
    instance_url: str = "",
) -> str:
    """Token endpoint URL used as JWT ``aud`` claim."""
    return resolve_salesforce_token_url(environment, custom_domain, instance_url)


def _jwt_cache_key(connection_id: int, role: str) -> str:
    return f"{JWT_CACHE_PREFIX}:{int(connection_id)}:{role}"


def _get_redis_client():
    import redis

    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def invalidate_salesforce_jwt_cache(connection_id: int, role: str) -> None:
    if not connection_id or role not in ("source", "destination"):
        return
    try:
        _get_redis_client().delete(_jwt_cache_key(connection_id, role))
    except Exception:
        logger.debug(
            "SF_JWT_CACHE_INVALIDATE_SKIPPED | connection_id=%s | role=%s",
            connection_id,
            role,
            exc_info=True,
        )


def _load_signing_key(private_key: str, passphrase: Optional[str] = None):
    from datasyncher_connections.snowflake.pem import (
        load_private_key_object,
        normalize_pem_key,
    )

    normalized = normalize_pem_key(private_key)
    return load_private_key_object(normalized, passphrase)


def build_jwt_assertion(
    client_id: str,
    username: str,
    private_key: str,
    audience: str,
    passphrase: Optional[str] = None,
) -> str:
    """Build RS256-signed JWT assertion for Salesforce token exchange."""
    if not client_id:
        raise ValueError("CLIENT_ID (Consumer Key) is required for JWT Bearer auth.")
    if not username:
        raise ValueError("integration_username is required for JWT Bearer auth.")
    if not private_key:
        raise ValueError("private_key is required for JWT Bearer auth.")
    if not audience:
        raise ValueError("JWT audience (token URL) is required.")

    signing_key = _load_signing_key(private_key, passphrase)
    now = int(time.time())
    payload = {
        "iss": client_id,
        "sub": username,
        "aud": audience,
        "exp": now + JWT_ASSERTION_TTL_SECONDS,
    }
    return jwt.encode(payload, signing_key, algorithm="RS256")


def exchange_jwt_for_access_token(
    client_id: str,
    username: str,
    private_key: str,
    environment: str,
    custom_domain: str = "",
    instance_url: str = "",
    passphrase: Optional[str] = None,
) -> Dict[str, str]:
    """Exchange a signed JWT assertion for a Salesforce access token."""
    audience = resolve_jwt_audience(environment, custom_domain, instance_url)
    assertion = build_jwt_assertion(
        client_id, username, private_key, audience, passphrase=passphrase
    )
    response = requests.post(
        audience,
        data={
            "grant_type": JWT_BEARER_GRANT,
            "assertion": assertion,
        },
        timeout=30,
    )
    if response.status_code != 200:
        detail = response.text
        try:
            err = response.json()
            detail = err.get("error_description") or err.get("error") or detail
        except Exception:
            pass
        logger.warning(
            "SF_JWT_EXCHANGE_FAILED | aud=%s | env=%s | custom_domain=%s | user=%s | detail=%s",
            audience,
            environment,
            custom_domain or "",
            username,
            detail,
        )
        raise ValueError(format_salesforce_jwt_exchange_error(detail))

    data = response.json()
    access_token = data.get("access_token")
    instance = data.get("instance_url") or instance_url
    if not access_token:
        raise ValueError("Salesforce JWT token exchange returned no access_token.")
    if not instance:
        raise ValueError("Salesforce JWT token exchange returned no instance_url.")

    return {
        "access_token": access_token,
        "instance_url": instance,
    }


def get_salesforce_jwt_access_token(
    credentials: Dict[str, Any],
    connection_id: Optional[int] = None,
    role: Optional[str] = None,
    *,
    environment: str = "production",
    custom_domain: str = "",
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Return cached or freshly exchanged JWT access token bundle.

    credentials must include CLIENT_ID, integration_username, private_key.
    """
    client_id = credentials.get("CLIENT_ID") or credentials.get("client_id")
    username = (
        credentials.get("integration_username")
        or credentials.get("username")
        or credentials.get("USER")
    )
    private_key = credentials.get("private_key")
    passphrase = credentials.get("passphrase") or None
    instance_url = credentials.get("instance_url") or ""

    if use_cache and connection_id and role in ("source", "destination"):
        try:
            cached = _get_redis_client().get(_jwt_cache_key(connection_id, role))
            if cached:
                parsed = json.loads(cached)
                if parsed.get("access_token") and parsed.get("instance_url"):
                    logger.info(
                        "SF_JWT_CACHE_HIT | connection_id=%s | role=%s",
                        connection_id,
                        role,
                    )
                    return parsed
        except Exception:
            logger.debug(
                "SF_JWT_CACHE_READ_SKIPPED | connection_id=%s | role=%s",
                connection_id,
                role,
                exc_info=True,
            )

    tokens = exchange_jwt_for_access_token(
        client_id=str(client_id or "").strip(),
        username=str(username or "").strip(),
        private_key=str(private_key or "").strip(),
        environment=environment,
        custom_domain=custom_domain or credentials.get("CUSTOM_DOMAIN") or "",
        instance_url=instance_url,
        passphrase=passphrase,
    )

    if use_cache and connection_id and role in ("source", "destination"):
        try:
            _get_redis_client().setex(
                _jwt_cache_key(connection_id, role),
                JWT_ACCESS_CACHE_TTL_SECONDS,
                json.dumps(tokens),
            )
        except Exception:
            logger.debug(
                "SF_JWT_CACHE_WRITE_SKIPPED | connection_id=%s | role=%s",
                connection_id,
                role,
                exc_info=True,
            )

    return tokens
