"""Dispatch + HTTP fallback wiring for Dynamics → Databricks migrate."""

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def test_dispatch_direct_connector_databricks_dynamics_imports_new_migrate(monkeypatch):
    import importlib

    from celery_worker import _dispatch_direct_connector

    seen = {"service": None, "migrate_called": False}

    class FakeMigrationRequest:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    async def fake_migrate(req):
        seen["migrate_called"] = True
        return {"status": "ok", "connection_id": getattr(req, "connection_id", None)}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "models.microsoft_dynamics_models":
            class Mod:
                MigrationRequest = FakeMigrationRequest

            return Mod
        if name == "services.dynamics.as_source.to_databricks":
            seen["service"] = name

            class Mod:
                migrate = fake_migrate

            return Mod
        if name == "services.dynamics.as_source.to_snowflake":
            raise AssertionError("Databricks path must not import Snowflake Dynamics migrate")
        return real_import_module(name)

    def fake_execute_async(coro):
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    monkeypatch.setattr("celery_worker._execute_async", fake_execute_async)

    payload = {
        "source_config": {},
        "destination_config": {},
        "connection_id": 23,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "selected_tables": ["custtable"],
        "job_name": "dbx_dynamics_test",
        "migration_session_id": 1,
    }
    result = _dispatch_direct_connector(
        "/Databricks-MicrosoftDynamics365_FO-migration-yield",
        payload,
    )
    assert result == {"status": "ok", "connection_id": 23}
    assert seen["service"] == "services.dynamics.as_source.to_databricks"
    assert seen["migrate_called"] is True


def test_cloud_function_url_databricks_dynamics_not_snowflake_migrate():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert (
        "'/Databricks-MicrosoftDynamics365_FO-migration-yield': "
        "get_api_url('Databricks-MicrosoftDynamics365_FO-migrate')"
    ) in text
    assert (
        "'/Databricks-MicrosoftDynamics365_FO-migration-yield': get_api_url('migrate')"
    ) not in text


def test_snowflake_dynamics_url_still_uses_migrate():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert (
        "'/Snowflake-MicrosoftDynamics365_FO-migration-yield': get_api_url('migrate')"
    ) in text
