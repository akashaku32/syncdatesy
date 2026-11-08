"""Read/update encrypted Salesforce OAuth tokens on Connection source/destination configs."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from django.db import transaction

from apps.elttool.models import Connection, DestinationConfig, SourceConfig

VALID_ROLES = frozenset({"source", "destination"})


def _config_for_role(connection: Connection, role: str):
    if role == "source":
        return connection.src_config
    if role == "destination":
        return connection.dst_config
    return None


def _normalize_config_data(raw: Any) -> Optional[Dict[str, Any]]:
    """Decryptable config fields may be stored as dict or JSON string."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def get_salesforce_oauth_tokens(connection_id: int, role: str) -> Optional[Dict[str, Any]]:
    """Return OAuth token fields for the connection's Salesforce source or destination."""
    if role not in VALID_ROLES:
        return None

    try:
        connection = Connection.objects.select_related("src_config", "dst_config").get(
            connection_id=connection_id
        )
    except Connection.DoesNotExist:
        return None

    config = _config_for_role(connection, role)
    if not config:
        return None

    data = _normalize_config_data(config.data)
    if not data:
        return None

    return {
        "access_token": data.get("access_token"),
        "instance_url": data.get("instance_url"),
        "refresh_token": data.get("refresh_token"),
        "CLIENT_ID": data.get("CLIENT_ID"),
        "CLIENT_SECRET": data.get("CLIENT_SECRET"),
        "CUSTOM_DOMAIN": data.get("CUSTOM_DOMAIN"),
        "state": data.get("state"),
    }


def update_salesforce_oauth_tokens(
    connection_id: int,
    role: str,
    *,
    access_token: str,
    instance_url: str,
    refresh_token: Optional[str] = None,
    previous_refresh_token: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Persist refreshed Salesforce OAuth tokens under row lock.

    When previous_refresh_token is supplied, skip the write if another worker already
    rotated the stored refresh token (avoids clobbering a newer token).
    """
    if role not in VALID_ROLES:
        return False, "invalid role"
    if not access_token or not instance_url:
        return False, "access_token and instance_url are required"

    try:
        with transaction.atomic():
            connection = Connection.objects.select_related(
                "src_config", "dst_config"
            ).get(connection_id=connection_id)

            if role == "source":
                if not connection.src_config_id:
                    return False, "config not found"
                config = SourceConfig.objects.select_for_update().get(
                    pk=connection.src_config_id
                )
            else:
                if not connection.dst_config_id:
                    return False, "config not found"
                config = DestinationConfig.objects.select_for_update().get(
                    pk=connection.dst_config_id
                )

            data = _normalize_config_data(config.data)
            if not data:
                return False, "invalid config data"

            stored_refresh = data.get("refresh_token")
            if (
                previous_refresh_token
                and stored_refresh
                and stored_refresh != previous_refresh_token
            ):
                return True, "skipped_stale_refresh_token"

            updated = dict(data)
            updated["access_token"] = access_token
            updated["instance_url"] = instance_url
            if refresh_token:
                updated["refresh_token"] = refresh_token

            config.data = updated
            config.save()

        return True, "updated"
    except Connection.DoesNotExist:
        return False, "connection not found"
    except Exception as exc:
        return False, str(exc)
