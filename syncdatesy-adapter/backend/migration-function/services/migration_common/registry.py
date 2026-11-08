"""Registry of (source, destination) migration handlers."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

Handler = Callable[..., Any]

_REGISTRY: Dict[Tuple[str, str], Handler] = {}

# FastAPI function_path -> (source, destination) using dest-source path naming.
_FUNCTION_PATH_MAP: Dict[str, Tuple[str, str]] = {
    "/Databricks-Salesforce-migration-yield": ("salesforce", "databricks"),
    "/Databricks-SalesforceSandbox-migration-yield": ("salesforce", "databricks"),
    "/Databricks-MicrosoftDynamics365_FO-migration-yield": (
        "microsoftdynamics365_fo",
        "databricks",
    ),
    "/Snowflake-MicrosoftDynamics365_FO-migration-yield": (
        "microsoftdynamics365_fo",
        "snowflake",
    ),
    "/Snowflake-MicrosoftDynamics-migration-yield": ("microsoftdynamics", "snowflake"),
    "/Snowflake-Salesforce-migration-yield": ("salesforce", "snowflake"),
    "/Snowflake-SalesforceSandbox-migration-yield": ("salesforce", "snowflake"),
    "/Snowflake-NetSuite-migration-yield": ("netsuite", "snowflake"),
    "/Databricks-NetSuite-migration-yield": ("netsuite", "databricks"),
    "/Salesforce-Snowflake-migration-yield": ("snowflake", "salesforce"),
    "/SalesforceSandbox-Snowflake-migration-yield": ("snowflake", "salesforce"),
    "/Salesforce-Databricks-migration-yield": ("databricks", "salesforce"),
    "/Salesforce-DatabricksSandbox-migration-yield": ("databricks", "salesforce"),
}


def register(source: str, dest: str, handler: Handler) -> None:
    _REGISTRY[(source.lower(), dest.lower())] = handler


def get(source: str, dest: str) -> Optional[Handler]:
    return _REGISTRY.get((source.lower(), dest.lower()))


def get_by_function_path(function_path: str) -> Optional[Tuple[str, str]]:
    if not function_path:
        return None
    return _FUNCTION_PATH_MAP.get(function_path)


def run(source: str, dest: str, payload: Any) -> Any:
    handler = get(source, dest)
    if handler is None:
        raise KeyError(f"No migration handler registered for {source!r} -> {dest!r}")
    if hasattr(handler, "run"):
        return handler.run(payload)
    return handler(payload)
