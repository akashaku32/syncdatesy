"""Celery dispatch for Databricks → Salesforce reverse ETL."""

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def _install_dbx_retl_fakes(monkeypatch, seen):
    import importlib

    from celery_worker import _dispatch_direct_connector

    class FakeMigrationRequest:
        def __init__(self, **kwargs):
            seen["selected_tables"] = kwargs.get("selected_tables")
            for k, v in kwargs.items():
                setattr(self, k, v)

    async def fake_sync(req, environment):
        seen["sync_called"] = True
        seen["environment"] = environment
        seen["connection_id"] = getattr(req, "connection_id", None)
        return {"status": "ok", "total_rows": 0}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "models.salesforce_models":

            class Mod:
                SalesforceMigration = FakeMigrationRequest

            return Mod
        if name == "services.salesforce.as_destination.from_databricks":
            seen["service"] = name

            class Mod:
                sync_databricks_to_salesforce = staticmethod(fake_sync)

            return Mod
        if name == "services.salesforce.as_source.to_databricks":
            raise AssertionError("RETL path must not import Salesforce→Databricks sync")
        if name == "services.salesforce.as_destination.from_snowflake":
            raise AssertionError("Databricks RETL must not import Snowflake RETL sync")
        return real_import_module(name)

    def fake_execute_async(coro):
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    monkeypatch.setattr("celery_worker._execute_async", fake_execute_async)
    return _dispatch_direct_connector


def _payload(selected_tables):
    return {
        "source_config": {"catalog": "main", "schema": "sf"},
        "destination_config": {},
        "connection_id": 25,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "selected_tables": selected_tables,
        "job_name": "dbx_sf_retl",
        "migration_session_id": 1,
        "safety_interval": "120",
        "execution_order": "sequential",
        "chunk_count": 500,
        "logs_creation": False,
        "connection_name": "sf",
        "company_name": "Co",
    }


def test_dispatch_databricks_to_salesforce_production_normalizes_tables(monkeypatch):
    seen = {"service": None, "sync_called": False, "environment": None}
    dispatch = _install_dbx_retl_fakes(monkeypatch, seen)
    result = dispatch("/Salesforce-Databricks-migration-yield", _payload(["Account"]))
    assert result == {"status": "ok", "total_rows": 0}
    assert seen["service"] == "services.salesforce.as_destination.from_databricks"
    assert seen["sync_called"] is True
    assert seen["environment"] == "production"
    assert seen["selected_tables"] == [["Account", "Account"]]


def test_dispatch_databricks_to_salesforce_sandbox(monkeypatch):
    seen = {"service": None, "sync_called": False, "environment": None}
    dispatch = _install_dbx_retl_fakes(monkeypatch, seen)
    result = dispatch(
        "/Salesforce-DatabricksSandbox-migration-yield",
        _payload([["Account", "Account__c"]]),
    )
    assert result["status"] == "ok"
    assert seen["environment"] == "sandbox"
    assert seen["selected_tables"] == [["Account", "Account__c"]]


def test_cloud_function_url_salesforce_databricks_retl_maps_to_migrate_endpoints():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert (
        "'/Salesforce-Databricks-migration-yield': "
        "get_api_url('Salesforce-Databricks-migrate')"
    ) in text
    assert (
        "'/Salesforce-DatabricksSandbox-migration-yield': "
        "get_api_url('Salesforce-DatabricksSandbox-migrate')"
    ) in text
    assert (
        "'/Salesforce-Databricks-migration-yield': 'databricks_to_salesforce'"
    ) in text
    assert (
        "'/Salesforce-DatabricksSandbox-migration-yield': "
        "'databricks_sandbox_to_salesforce'"
    ) in text
    assert (
        "'/Databricks-Salesforce-migration-yield': 'salesforce_to_databricks'"
    ) in text
