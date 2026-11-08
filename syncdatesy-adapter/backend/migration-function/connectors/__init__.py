"""Connector dispatch helpers."""

from connectors.auth_registry import (
    CONNECTOR_AUTH_REGISTRY,
    CONNECTOR_SOURCE_OAUTH_REGISTRY,
    get_auth_path,
)

__all__ = [
    "CONNECTOR_AUTH_REGISTRY",
    "CONNECTOR_SOURCE_OAUTH_REGISTRY",
    "get_auth_path",
]
