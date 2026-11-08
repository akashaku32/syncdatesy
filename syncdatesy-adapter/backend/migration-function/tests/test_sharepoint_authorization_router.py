"""
Unit tests for SharePoint Authorization Router endpoint.
Keeps the router thin and verifies request/response behavior.
"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from routers.sharepoint_authorization_router import router


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


def _valid_client_secret_payload():
    return {
        "SharePoint": {
            "auth_type": "client_secret",
            "tenant_id": "tenant-123",
            "client_id": "client-123",
            "client_secret": "secret-value",
            "sharepoint_site_url": "https://example.sharepoint.com/sites/demo",
            "document_library": "Documents",
            "root_folder": "/exports",
        }
    }


class TestSharePointAuthorizationEndpoint:
    async def test_authorization_success_client_secret(self, client):
        with patch(
            "routers.sharepoint_authorization_router.test_sharepoint_connection",
            return_value={"site_id": "site-1", "drive_id": "drive-1"},
        ):
            resp = await client.post("/SharePoint-Authorization", json=_valid_client_secret_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status_code"] == 200

    async def test_authorization_success_client_certificate(self, client):
        payload = _valid_client_secret_payload()
        payload["SharePoint"]["auth_type"] = "client_certificate"
        payload["SharePoint"].pop("client_secret", None)
        payload["SharePoint"]["client_certificate"] = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----"

        with patch(
            "routers.sharepoint_authorization_router.test_sharepoint_connection",
            return_value={"site_id": "site-1", "drive_id": "drive-1"},
        ):
            resp = await client.post("/SharePoint-Authorization", json=payload)
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "field,value",
        [
            ("tenant_id", ""),
            ("client_id", ""),
            ("client_secret", ""),
            ("sharepoint_site_url", ""),
            ("sharepoint_site_url", "not-a-url"),
            ("document_library", ""),
            ("root_folder", ".."),
            ("root_folder", "folder\\nested"),
            ("auth_type", "oauth"),
        ],
    )
    async def test_authorization_invalid_client_secret_payloads(self, client, field, value):
        payload = _valid_client_secret_payload()
        payload["SharePoint"][field] = value

        with patch(
            "routers.sharepoint_authorization_router.test_sharepoint_connection",
            return_value={"site_id": "site-1", "drive_id": "drive-1"},
        ):
            resp = await client.post("/SharePoint-Authorization", json=payload)
        assert resp.status_code == 422

    async def test_authorization_client_certificate_requires_certificate(self, client):
        payload = _valid_client_secret_payload()
        payload["SharePoint"]["auth_type"] = "client_certificate"
        payload["SharePoint"].pop("client_secret", None)

        with patch(
            "routers.sharepoint_authorization_router.test_sharepoint_connection",
            return_value={"site_id": "site-1", "drive_id": "drive-1"},
        ):
            resp = await client.post("/SharePoint-Authorization", json=payload)
        assert resp.status_code == 422
