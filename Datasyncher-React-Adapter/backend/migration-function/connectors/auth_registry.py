"""
Registry of connector authorization HTTP paths.

Routers remain the HTTP entry points; this module centralizes connector_name → path
lookup for dynamic dispatch, tests, and documentation.
"""

from __future__ import annotations

CONNECTOR_AUTH_REGISTRY: dict[str, str] = {
    "Snowflake": "/Snowflake-Authorization",
    "Databricks": "/Databricks-Authorization",
    "Salesforce": "/Salesforce-Authorization",
    "SalesforceSandbox": "/SalesforceSandbox-Authorization",
    "MicrosoftDynamics365_FO": "/MicrosoftDynamics365_FO-Authorization",
    "AmazonS3": "/AmazonS3-Authorization",
    "GoogleReviews": "/GoogleReviews-Authorization",
}

CONNECTOR_SOURCE_OAUTH_REGISTRY: dict[str, str] = {
    "Salesforce": "/Source-Salesforce-Authorization",
    "SalesforceSandbox": "/Source-SalesforceSandbox-Authorization",
    "GoogleReviews": "/Source-GoogleReviews-Authorization",
}


def get_auth_path(connector_name: str, *, source_oauth: bool = False) -> str | None:
    """Return Authorization HTTP path for a connector, if registered."""
    if source_oauth:
        return CONNECTOR_SOURCE_OAUTH_REGISTRY.get(connector_name)
    return CONNECTOR_AUTH_REGISTRY.get(connector_name)
