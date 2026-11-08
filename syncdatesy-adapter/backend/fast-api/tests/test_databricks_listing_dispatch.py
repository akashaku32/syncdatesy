"""Celery dispatch for Databricks source listing."""

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def test_dispatch_databricks_listing_tables(monkeypatch):
    import importlib

    seen = {}

    class FakeService:
        def __init__(self, details, **kwargs):
            seen["details"] = details
            seen["kwargs"] = kwargs

        def get_tables_and_columns(self, connection_id, job_name, cmp_id=None, user_id=None):
            seen["connection_id"] = connection_id
            seen["job_name"] = job_name
            return {"status_code": 200, "updated_tables": {"ACCOUNT": "table"}}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "services.databricks.service":

            class Mod:
                DatabricksService = FakeService

            return Mod
        return real_import_module(name)

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    from celery_worker import _dispatch_direct_connector

    result = _dispatch_direct_connector(
        "/Databricks-listing-tables",
        {
            "connection_id": 9,
            "job_name": "dbx_listing",
            "Databricks": {"catalog": "main", "schema": "sf"},
        },
    )
    assert result["status_code"] == 200
    assert seen["details"]["schema"] == "sf"
    assert seen["connection_id"] == 9


def test_cloud_function_url_databricks_listing_mapped():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert "'/Databricks-listing-tables': get_api_url('Databricks-listing-tables')" in text
    assert "'/Databricks-listing-tables': 'databricks_listing'" in text
