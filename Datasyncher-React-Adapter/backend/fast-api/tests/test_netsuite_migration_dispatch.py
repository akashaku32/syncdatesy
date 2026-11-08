"""Celery dispatch for NetSuite migration endpoints."""

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def test_dispatch_netsuite_to_snowflake_migration(monkeypatch):
    import importlib

    seen = {}

    def fake_migrate(req):
        seen["connection_id"] = req.connection_id
        seen["tables"] = list(req.selected_tables)
        return {"status": "success", "total_tables": len(req.selected_tables)}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "services.netsuite.as_source.to_snowflake":

            class Mod:
                netsuite_to_snowflake_connector = staticmethod(fake_migrate)

            return Mod
        if name == "models.netsuite_models":

            class Mod:
                class NetSuiteSnowflakeMigrationRequest:
                    def __init__(self, **kwargs):
                        self.connection_id = kwargs.get("connection_id")
                        self.selected_tables = kwargs.get("selected_tables") or []

            return Mod
        return real_import_module(name)

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    from celery_worker import _dispatch_direct_connector

    result = _dispatch_direct_connector(
        "/Snowflake-NetSuite-migration-yield",
        {
            "connection_id": 42,
            "selected_tables": ["customer", "transaction"],
            "source_config": {"account_id": "123", "connection_method": "suiteql_rest"},
            "destination_config": {"schema": "NETSUITE"},
            "migration_session_id": 99,
            "cmp_id": 1,
            "user_id": 1,
            "session": 1,
        },
    )
    assert result["status"] == "success"
    assert seen["connection_id"] == 42
    assert seen["tables"] == ["customer", "transaction"]


def test_app_maps_netsuite_migration_paths():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    text = app_path.read_text(encoding="utf-8")
    assert "'/Snowflake-NetSuite-migration-yield': get_api_url('Snowflake-NetSuite-migration-yield')" in text
    assert "'/Snowflake-NetSuite-migration-yield': 'netsuite_to_snowflake'" in text
    assert "'/NetSuite-listing-tables': 'netsuite_listing'" in text
