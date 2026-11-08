"""Dispatch + HTTP fallback wiring for Salesforce → Databricks migrate."""

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def _install_sf_dbx_fakes(monkeypatch, seen, environment_expected):
    import importlib

    from celery_worker import _dispatch_direct_connector

    class FakeMigrationRequest:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    async def fake_sync(req, environment):
        seen["sync_called"] = True
        seen["environment"] = environment
        seen["connection_id"] = getattr(req, "connection_id", None)
        return {"status": "ok", "connection_id": getattr(req, "connection_id", None)}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "models.salesforce_models":

            class Mod:
                SalesforceDatabricksMigration = FakeMigrationRequest

            return Mod
        if name == "services.salesforce.as_source.to_databricks":
            seen["service"] = name

            class Mod:
                sync_salesforce_to_databricks = staticmethod(fake_sync)

            return Mod
        if name == "services.salesforce.as_source.to_snowflake":
            raise AssertionError("Databricks SF path must not import Snowflake SF sync")
        if name == "services.salesforce.as_destination.from_snowflake":
            raise AssertionError("Databricks SF path must not import Snowflake SF sync")
        if name == "services.salesforce.client":
            raise AssertionError("Databricks SF path must not import SF client for sync")
        return real_import_module(name)

    def fake_execute_async(coro):
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    monkeypatch.setattr("celery_worker._execute_async", fake_execute_async)
    return _dispatch_direct_connector


def test_dispatch_direct_connector_databricks_salesforce_production(monkeypatch):
    seen = {"service": None, "sync_called": False, "environment": None}
    dispatch = _install_sf_dbx_fakes(monkeypatch, seen, "production")

    payload = {
        "source_config": {},
        "destination_config": {},
        "connection_id": 42,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "selected_tables": ["Account"],
        "job_name": "dbx_sf_test",
        "migration_session_id": 1,
        "safety_interval": "120",
        "execution_order": "sequential",
        "chunk_count": 50000,
        "logs_creation": False,
        "connection_name": "sf",
        "company_name": "Co",
    }
    result = dispatch("/Databricks-Salesforce-migration-yield", payload)
    assert result == {"status": "ok", "connection_id": 42}
    assert seen["service"] == "services.salesforce.as_source.to_databricks"
    assert seen["sync_called"] is True
    assert seen["environment"] == "production"


def test_dispatch_direct_connector_databricks_salesforce_sandbox(monkeypatch):
    seen = {"service": None, "sync_called": False, "environment": None}
    dispatch = _install_sf_dbx_fakes(monkeypatch, seen, "sandbox")

    payload = {
        "source_config": {},
        "destination_config": {},
        "connection_id": 7,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "selected_tables": ["Contact"],
        "job_name": "dbx_sf_sbx",
        "migration_session_id": 2,
        "safety_interval": "120",
        "execution_order": "sequential",
        "chunk_count": 50000,
        "logs_creation": False,
        "connection_name": "sf_sbx",
        "company_name": "Co",
    }
    result = dispatch("/Databricks-SalesforceSandbox-migration-yield", payload)
    assert result == {"status": "ok", "connection_id": 7}
    assert seen["sync_called"] is True
    assert seen["environment"] == "sandbox"


def test_cloud_function_url_databricks_salesforce_maps_to_migrate_endpoints():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert (
        "'/Databricks-Salesforce-migration-yield': "
        "get_api_url('Databricks-Salesforce-migrate')"
    ) in text
    assert (
        "'/Databricks-SalesforceSandbox-migration-yield': "
        "get_api_url('Databricks-SalesforceSandbox-migrate')"
    ) in text
    assert (
        "'/Databricks-Salesforce-migration-yield': 'salesforce_to_databricks'"
    ) in text
    assert (
        "'/Databricks-SalesforceSandbox-migration-yield': "
        "'salesforce_sandbox_to_databricks'"
    ) in text
