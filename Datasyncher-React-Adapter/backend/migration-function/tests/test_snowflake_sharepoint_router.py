"""
Unit tests for Snowflake -> SharePoint migration router.

Keeps this at router level: request validation + happy-path response shape.
Service layer is mocked to avoid real Snowflake/SharePoint/network usage.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from routers.snowflake_sharepoint_router import router


def create_test_app():
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
async def client():
    app = create_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as async_client:
        yield async_client


def _base_payload():
    return {
        "source_config": {
            "account": "acc",
            "user": "user",
            "authentication_type": "password",
            "password": "pw",
            "database": "DB",
            "schema": "PUBLIC",
            "warehouse": "WH",
        },
        "destination_config": {
            "auth_type": "client_secret",
            "tenant_id": "tenant-123",
            "client_id": "client-123",
            "client_secret": "secret-value",
            "sharepoint_site_url": "https://example.sharepoint.com/sites/demo",
            "document_library": "Documents",
            "root_folder": "/exports",
            "file_format": "csv",
            "csv_delimiter": ",",
            "csv_quotechar": '"',
        },
        "connection_id": 1,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "selected_tables": ["MY_VIEW"],
        "safety_interval": "0",
        "job_name": "TEST_JOB",
        "migration_session_id": 123,
        "execution_order": "parallel",
        "chunk_count": 1000,
        "logs_creation": False,
        "connection_name": "Conn",
        "company_name": "Company",
        "drop_and_reload": False,
    }


class TestSnowflakeSharePointMigrate:
    async def test_migrate_success(self, client):
        payload = _base_payload()
        with patch(
            "routers.snowflake_sharepoint_router.migrate_snowflake_to_sharepoint",
            return_value={"status_code": 200, "status": "success"},
        ):
            resp = await client.post("/Snowflake-SharePoint-migrate", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"

    async def test_migrate_validation_error(self, client):
        payload = _base_payload()
        payload.pop("connection_id")
        resp = await client.post("/Snowflake-SharePoint-migrate", json=payload)
        assert resp.status_code == 422

    async def test_migrate_accepts_excel_file_format_payload(self, client):
        payload = _base_payload()
        payload["destination_config"]["file_format"] = "excel"
        payload["table_export_settings"] = {
            "MY_VIEW": {
                "file_format": "excel",
                "excel_sheet_name": "Report",
                "excel_options": {"freeze_panes": "A2"},
                "excel_conditional_formats": [],
            }
        }
        with patch(
            "routers.snowflake_sharepoint_router.migrate_snowflake_to_sharepoint",
            return_value={"status_code": 200, "status": "success"},
        ):
            resp = await client.post("/Snowflake-SharePoint-migrate", json=payload)
        assert resp.status_code == 200
