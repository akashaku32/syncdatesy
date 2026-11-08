"""Persist Salesforce OAuth tokens to Django after in-worker refresh (rotation-safe)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, Optional, TypeVar
from urllib.parse import urlparse, urlunparse

import requests
from redis.lock import Lock

logger = logging.getLogger(__name__)

T = TypeVar("T")

SF_REFRESH_LOCK_PREFIX = "sf_oauth_refresh"
DEFAULT_REFRESH_LOCK_TIMEOUT = 30
DEFAULT_REFRESH_LOCK_BLOCKING_TIMEOUT = 90

SALESFORCE_TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"
SALESFORCE_SANDBOX_TOKEN_URL = "https://test.salesforce.com/services/oauth2/token"


def resolve_salesforce_token_url(
    environment: str,
    custom_domain: str = "",
    instance_url: str = "",
) -> str:
    """OAuth token endpoint aligned with how the org was authorized."""
    env = (environment or "production").strip().lower()
    custom = (custom_domain or "").strip().rstrip("/")
    inst = (instance_url or "").strip().rstrip("/")

    if env == "sandbox":
        if custom:
            if custom.startswith("http://") or custom.startswith("https://"):
                base = custom
            elif ".sandbox.my.salesforce.com" in custom:
                base = f"https://{custom.lstrip('https://').lstrip('http://')}"
            else:
                base = f"https://{custom}.sandbox.my.salesforce.com"
            return f"{base.rstrip('/')}/services/oauth2/token"
        if inst and ".sandbox.my.salesforce.com" in inst:
            return f"{inst}/services/oauth2/token"
        return SALESFORCE_SANDBOX_TOKEN_URL

    if not custom:
        return SALESFORCE_TOKEN_URL
    if custom.startswith("http://") or custom.startswith("https://"):
        base = custom
    elif ".my.salesforce.com" in custom:
        base = f"https://{custom.lstrip('https://').lstrip('http://')}"
    else:
        base = f"https://{custom}.my.salesforce.com"
    return f"{base.rstrip('/')}/services/oauth2/token"


def _django_internal_base_url() -> str:
    default = "http://django-svc:8000"
    raw = (os.getenv("DJANGO_INTERNAL_BASE_URL") or default).strip()
    if not raw:
        return default.rstrip("/")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and (
        host == "django-svc" or host.startswith("django-svc.")
    ):
        parsed = parsed._replace(scheme="http")
    return urlunparse(parsed).rstrip("/")


def _get_redis_client():
    import redis

    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis.from_url(url, decode_responses=True)


def _refresh_lock_key(connection_id: int, role: str) -> str:
    return f"{SF_REFRESH_LOCK_PREFIX}:{int(connection_id)}:{role}"


def run_with_salesforce_refresh_lock(
    connection_id: int,
    role: str,
    operation: Callable[[], T],
    *,
    lock_timeout: int = DEFAULT_REFRESH_LOCK_TIMEOUT,
    blocking_timeout: int = DEFAULT_REFRESH_LOCK_BLOCKING_TIMEOUT,
) -> T:
    """
    Serialize Salesforce OAuth refresh per connection/role across Celery workers.

    With Refresh Token Rotation enabled, only one worker may call Salesforce's
    token endpoint at a time. Waiters block, then re-hydrate inside ``operation``.
    """
    if role not in ("source", "destination"):
        return operation()

    client = _get_redis_client()
    lock = Lock(
        client,
        _refresh_lock_key(connection_id, role),
        timeout=lock_timeout,
        blocking_timeout=blocking_timeout,
    )
    acquired = lock.acquire(blocking=True, blocking_timeout=blocking_timeout)
    if not acquired:
        logger.warning(
            "SF_OAUTH_LOCK_TIMEOUT | connection_id=%s | role=%s | blocking_timeout=%s",
            connection_id,
            role,
            blocking_timeout,
        )
        raise ValueError(
            "Timed out waiting for Salesforce OAuth refresh lock. "
            "Retry the migration shortly."
        )
    try:
        return operation()
    finally:
        try:
            lock.release()
        except Exception:
            logger.debug(
                "SF_OAUTH_LOCK_RELEASE_SKIPPED | connection_id=%s | role=%s",
                connection_id,
                role,
                exc_info=True,
            )


def try_return_post_rotation_tokens(
    connection_id: int,
    role: str,
    previous_refresh_token: Optional[str],
    current_access_token: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """
    Return persisted tokens when another worker already rotated the refresh token.

    Avoids a redundant (and harmful) second call to Salesforce's refresh endpoint.
    """
    if not previous_refresh_token:
        return None

    latest = fetch_salesforce_oauth_tokens(int(connection_id), role)
    if not latest:
        return None

    stored_rt = str(latest.get("refresh_token") or "").strip()
    if not stored_rt or stored_rt == str(previous_refresh_token).strip():
        return None

    access_token = latest.get("access_token")
    instance_url = latest.get("instance_url")
    if not access_token or not instance_url:
        return None

    if current_access_token and str(access_token).strip() == str(
        current_access_token
    ).strip():
        return None

    logger.info(
        "SF_OAUTH_SKIP_REFRESH | connection_id=%s | role=%s | reason=already_rotated",
        connection_id,
        role,
    )
    return {
        "access_token": access_token,
        "instance_url": instance_url,
        "refresh_token": stored_rt,
    }


def wait_for_salesforce_token_rotation(
    connection_id: int,
    role: str,
    previous_refresh_token: str,
    *,
    current_access_token: Optional[str] = None,
    max_wait_seconds: float = 8.0,
    poll_seconds: float = 0.35,
) -> Optional[Dict[str, str]]:
    """Poll Django until a sibling worker persists a rotated refresh token."""
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        rotated = try_return_post_rotation_tokens(
            connection_id,
            role,
            previous_refresh_token,
            current_access_token=current_access_token,
        )
        if rotated:
            return rotated
        time.sleep(poll_seconds)
    return None


def _internal_headers() -> Dict[str, str]:
    headers = {"X-Forwarded-Proto": "https"}
    key = os.getenv("INTERNAL_API_KEY")
    if key:
        headers["X-INTERNAL-KEY"] = key
    return headers


def fetch_salesforce_oauth_tokens(
    connection_id: int, role: str
) -> Optional[Dict[str, Any]]:
    """Load latest Salesforce OAuth tokens from Django (for refresh retry after rotation)."""
    if not connection_id or role not in ("source", "destination"):
        return None

    url = (
        f"{_django_internal_base_url()}/api/v1/internal/"
        f"connection/{int(connection_id)}/salesforce-oauth-tokens/"
    )
    try:
        response = requests.get(
            url,
            params={"role": role},
            headers=_internal_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            logger.warning(
                "SF_OAUTH_FETCH_FAILED | connection_id=%s | role=%s | status=%s | body=%s",
                connection_id,
                role,
                response.status_code,
                (response.text or "")[:200],
            )
            return None
        payload = response.json()
        return payload.get("tokens") if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning(
            "SF_OAUTH_FETCH_ERROR | connection_id=%s | role=%s | error=%s",
            connection_id,
            role,
            exc,
        )
        return None


def persist_salesforce_oauth_tokens(
    connection_id: int,
    role: str,
    token_data: Dict[str, Any],
    *,
    previous_refresh_token: Optional[str] = None,
) -> bool:
    """Write refreshed tokens back to encrypted source/destination config."""
    if not connection_id or role not in ("source", "destination"):
        return False

    access_token = token_data.get("access_token")
    instance_url = token_data.get("instance_url")
    if not access_token or not instance_url:
        return False

    url = (
        f"{_django_internal_base_url()}/api/v1/internal/"
        f"connection/{int(connection_id)}/salesforce-oauth-tokens/"
    )
    body = {
        "role": role,
        "access_token": access_token,
        "instance_url": instance_url,
        "refresh_token": token_data.get("refresh_token"),
        "previous_refresh_token": previous_refresh_token,
    }
    try:
        response = requests.post(
            url,
            json=body,
            headers=_internal_headers(),
            timeout=15,
        )
        if response.status_code == 200:
            logger.info(
                "SF_OAUTH_PERSISTED | connection_id=%s | role=%s | rotated=%s",
                connection_id,
                role,
                bool(
                    token_data.get("refresh_token")
                    and previous_refresh_token
                    and token_data.get("refresh_token") != previous_refresh_token
                ),
            )
            return True

        logger.warning(
            "SF_OAUTH_PERSIST_FAILED | connection_id=%s | role=%s | status=%s | body=%s",
            connection_id,
            role,
            response.status_code,
            (response.text or "")[:200],
        )
        return False
    except Exception as exc:
        logger.warning(
            "SF_OAUTH_PERSIST_ERROR | connection_id=%s | role=%s | error=%s",
            connection_id,
            role,
            exc,
        )
        return False


def hydrate_salesforce_credentials(
    connection_id: int,
    role: str,
    credentials: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Merge latest OAuth tokens from Django into the in-memory job payload.

    Parallel Celery children each receive a snapshot of source_config; when one
    child refreshes and persists a rotated refresh token, siblings must reload
    from DB before their next Salesforce connection attempt.
    """
    out = dict(credentials or {})
    if not connection_id or role not in ("source", "destination"):
        return out

    latest = fetch_salesforce_oauth_tokens(int(connection_id), role)
    if not latest:
        return out

    changed = []
    for key in ("access_token", "instance_url", "refresh_token"):
        new_val = latest.get(key)
        if new_val and out.get(key) != new_val:
            out[key] = new_val
            changed.append(key)

    if changed:
        logger.info(
            "SF_OAUTH_HYDRATED | connection_id=%s | role=%s | fields=%s",
            connection_id,
            role,
            ",".join(changed),
        )
    return out
