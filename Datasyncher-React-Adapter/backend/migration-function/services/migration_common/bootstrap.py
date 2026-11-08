"""Lazy registration of migration handlers (import from celery_worker)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
from typing import Any, Callable, Optional

from services.migration_common.registry import register

_REGISTERED = False


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def _import_module(name: str):
    """Prefer celery_worker.importlib when available (test patches)."""
    try:
        import celery_worker

        return celery_worker.importlib.import_module(name)
    except ImportError:
        return importlib.import_module(name)


def _netsuite_to_snowflake_handler(payload: dict) -> Any:
    NetSuiteSnowflakeMigrationRequest = getattr(
        _import_module("models.netsuite_models"),
        "NetSuiteSnowflakeMigrationRequest",
    )
    netsuite = _import_module("services.netsuite.as_source.to_snowflake")
    connector = getattr(netsuite, "netsuite_to_snowflake_connector")
    return connector(NetSuiteSnowflakeMigrationRequest(**payload))


def _netsuite_to_databricks_handler(payload: dict) -> Any:
    NetSuiteDatabricksMigrationRequest = getattr(
        _import_module("models.netsuite_models"),
        "NetSuiteDatabricksMigrationRequest",
    )
    netsuite = _import_module("services.netsuite.as_source.to_databricks")
    connector = getattr(netsuite, "netsuite_to_databricks_connector")
    return connector(NetSuiteDatabricksMigrationRequest(**payload))


def _dynamics_to_databricks_handler(payload: dict) -> Any:
    MigrationRequest = getattr(
        _import_module("models.microsoft_dynamics_models"),
        "MigrationRequest",
    )
    dynamics = _import_module("services.dynamics.as_source.to_databricks")
    migrate = getattr(dynamics, "migrate")
    return _run_async(migrate(MigrationRequest(**payload)))


def ensure_registered(execute_async: Optional[Callable[[Any], Any]] = None) -> None:
    """Register built-in handlers once. Optional ``execute_async`` overrides async runner."""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    if execute_async is not None:

        def _handler(payload: dict) -> Any:
            MigrationRequest = getattr(
                _import_module("models.microsoft_dynamics_models"),
                "MigrationRequest",
            )
            dynamics = _import_module("services.dynamics.as_source.to_databricks")
            migrate = getattr(dynamics, "migrate")
            return execute_async(migrate(MigrationRequest(**payload)))

        register("microsoftdynamics365_fo", "databricks", _handler)
    else:
        register("microsoftdynamics365_fo", "databricks", _dynamics_to_databricks_handler)

    register("netsuite", "snowflake", _netsuite_to_snowflake_handler)
    register("netsuite", "databricks", _netsuite_to_databricks_handler)
