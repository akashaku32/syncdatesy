"""Celery dispatch for SAP OData migration endpoints."""

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def test_dispatch_sap_odata_to_snowflake_migration(monkeypatch):
    import importlib

    seen = {}

    def fake_migrate(req):
        seen["connection_id"] = req.connection_id
        seen["tables"] = list(req.selected_tables)
        return {"status": "SUCCESS", "total_tables": len(req.selected_tables)}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "services.sap.as_source.to_snowflake":

            class Mod:
                sap_odata_to_snowflake_connector = staticmethod(fake_migrate)

            return Mod
        if name == "models.sap_odata_models":

            class Mod:
                class SAPODataSnowflakeMigrationRequest:
                    def __init__(self, **kwargs):
                        self.connection_id = kwargs.get("connection_id")
                        self.selected_tables = kwargs.get("selected_tables") or []

            return Mod
        return real_import_module(name)

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    from celery_worker import _dispatch_direct_connector

    result = _dispatch_direct_connector(
        "/Snowflake-SAPOData-migration-yield",
        {
            "connection_id": 32,
            "selected_tables": ["AttrOfISDSLSORDERITEM", "EntityOf0FI_AR_30"],
            "source_config": {"host": "sap.example.com"},
            "destination_config": {"schema": "SAP"},
            "migration_session_id": 13100,
            "cmp_id": 1,
            "user_id": 1,
            "session": 1,
        },
    )
    assert result["status"] == "SUCCESS"
    assert seen["connection_id"] == 32
    assert len(seen["tables"]) == 2


def test_dispatch_sap_odata_to_adls_migration(monkeypatch):
    import importlib

    seen = {}

    def fake_migrate(req):
        seen["connection_id"] = req.connection_id
        seen["tables"] = list(req.selected_tables)
        return {"status": "success", "status_code": 200}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "services.sap.as_source.to_adls":

            class Mod:
                migrate_sap_odata_to_adls = staticmethod(fake_migrate)

            return Mod
        if name == "models.sap_odata_models":

            class Mod:
                class SAPODataADLSMigrationRequest:
                    def __init__(self, **kwargs):
                        self.connection_id = kwargs.get("connection_id")
                        self.selected_tables = kwargs.get("selected_tables") or []

            return Mod
        return real_import_module(name)

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    from celery_worker import _dispatch_direct_connector

    result = _dispatch_direct_connector(
        "/AzureDataLakeStorage-SAPOData-migration-yield",
        {
            "connection_id": 32,
            "selected_tables": ["EntityA"],
            "source_config": {"host": "sap.example.com"},
            "destination_config": {"container": "data"},
            "migration_session_id": 13101,
            "cmp_id": 1,
            "user_id": 1,
            "session": 1,
            "execution_order": "sequential",
            "chunk_count": 50000,
            "safety_interval": "120",
        },
    )
    assert result["status_code"] == 200
    assert seen["connection_id"] == 32


def test_dispatch_sap_odata_to_databricks_migration(monkeypatch):
    import importlib

    seen = {}

    def fake_migrate(req):
        seen["connection_id"] = req.connection_id
        seen["tables"] = list(req.selected_tables)
        return {"status": "SUCCESS", "total_tables": len(req.selected_tables)}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "services.sap.as_source.to_databricks":

            class Mod:
                sap_odata_to_databricks_connector = staticmethod(fake_migrate)

            return Mod
        if name == "models.sap_odata_models":

            class Mod:
                class SAPODataDatabricksMigrationRequest:
                    def __init__(self, **kwargs):
                        self.connection_id = kwargs.get("connection_id")
                        self.selected_tables = kwargs.get("selected_tables") or []

            return Mod
        return real_import_module(name)

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    from celery_worker import _dispatch_direct_connector

    result = _dispatch_direct_connector(
        "/Databricks-SAPOData-migration-yield",
        {
            "connection_id": 42,
            "selected_tables": ["SalesOrderSet", "BusinessPartnerSet"],
            "source_config": {"host": "sap.example.com"},
            "destination_config": {
                "server_hostname": "dbc.example.com",
                "http_path": "/sql/1.0/warehouses/abc",
                "access_token": "token",
                "catalog": "main",
                "schema": "sap",
            },
            "migration_session_id": 14000,
            "cmp_id": 1,
            "user_id": 1,
            "session": 1,
        },
    )
    assert result["status"] == "SUCCESS"
    assert seen["connection_id"] == 42
    assert len(seen["tables"]) == 2


def test_cloud_function_url_sap_odata_migration_mapped():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert "'/Snowflake-SAPOData-migration-yield': get_api_url('Snowflake-SAPOData-migration-yield')" in text
    assert "'/AzureDataLakeStorage-SAPOData-migration-yield': get_api_url('AzureDataLakeStorage-SAPOData-migration-yield')" in text
    assert "'/Databricks-SAPOData-migration-yield': get_api_url('Databricks-SAPOData-migrate')" in text
    assert "'/Snowflake-SAPOData-migration-yield': 'sap_odata_to_snowflake'" in text
    assert "'/AzureDataLakeStorage-SAPOData-migration-yield': 'sap_odata_to_adls'" in text
    assert "'/Databricks-SAPOData-migration-yield': 'sap_odata_to_databricks'" in text
