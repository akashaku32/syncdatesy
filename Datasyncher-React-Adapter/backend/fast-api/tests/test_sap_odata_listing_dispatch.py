"""Celery dispatch for SAP OData source listing (Update Schema)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def test_dispatch_sap_odata_listing_tables(monkeypatch):
    import importlib

    seen = {}

    def fake_discover(connection_id, credentials, job_name):
        seen["connection_id"] = connection_id
        seen["credentials"] = credentials
        seen["job_name"] = job_name
        return {
            "status_code": 200,
            "message": "Successfully discovered and registered 3 OData entities.",
        }

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "services.sap.client":

            class Mod:
                discover_and_populate_sap_odata_service = staticmethod(fake_discover)

            return Mod
        return real_import_module(name)

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    from celery_worker import _dispatch_direct_connector

    result = _dispatch_direct_connector(
        "/SAPOData-listing-tables",
        {
            "connection_id": 42,
            "job_name": "sap_listing_tables_42",
            "SAPOData": {"host": "sap.example.com", "username": "user"},
        },
    )
    assert result["status_code"] == 200
    assert seen["connection_id"] == 42
    assert seen["job_name"] == "sap_listing_tables_42"
    assert seen["credentials"]["host"] == "sap.example.com"


def test_cloud_function_url_sap_odata_listing_mapped():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert "'/SAPOData-listing-tables': get_api_url('SAPOData-listing-tables')" in text
    assert "'/SAPOData-listing-tables': 'sap_odata_listing'" in text
