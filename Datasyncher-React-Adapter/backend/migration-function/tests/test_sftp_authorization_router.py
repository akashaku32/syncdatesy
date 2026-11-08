"""
Unit tests for SFTP Authorization Router endpoint.
Keeps the router thin and verifies request/response behavior.
"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from routers.sftp_authorization_router import router


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


def _valid_password_payload():
    return {
        "SFTP": {
            "sftp_host": "example.com",
            "sftp_port": 22,
            "sftp_username": "user",
            "authentication_type": "password",
            "sftp_password": "secret",
            "root_folder": "/upload",
            "file_format": "csv",
            "csv_delimiter": ",",
            "csv_quotechar": '"',
        }
    }


class TestSFTPAuthorizationEndpoint:
    async def test_authorization_success(self, client):
        payload = _valid_password_payload()
        with patch("routers.sftp_authorization_router.test_sftp_connection", return_value=None):
            resp = await client.post("/SFTP-Authorization", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status_code"] == 200

    async def test_authorization_success_without_file_format_defaults_to_csv(self, client):
        payload = _valid_password_payload()
        payload["SFTP"].pop("file_format", None)
        with patch("routers.sftp_authorization_router.test_sftp_connection", return_value=None):
            resp = await client.post("/SFTP-Authorization", json=payload)
        assert resp.status_code == 200

    async def test_authorization_success_with_excel_file_format(self, client):
        payload = _valid_password_payload()
        payload["SFTP"]["file_format"] = "excel"
        payload["SFTP"].pop("csv_delimiter", None)
        payload["SFTP"].pop("csv_quotechar", None)
        with patch("routers.sftp_authorization_router.test_sftp_connection", return_value=None):
            resp = await client.post("/SFTP-Authorization", json=payload)
        assert resp.status_code == 200

    async def test_authorization_missing_host(self, client):
        payload = _valid_password_payload()
        payload["SFTP"].pop("sftp_host", None)
        with patch("routers.sftp_authorization_router.test_sftp_connection", return_value=None):
            resp = await client.post("/SFTP-Authorization", json=payload)
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "field,value",
        [
            ("sftp_username", ""),
            ("sftp_port", "not-a-port"),
            ("sftp_port", 70000),
            ("authentication_type", "token"),
            ("sftp_password", ""),
            ("root_folder", ""),
            ("file_format", "xml"),
            ("csv_delimiter", "||"),
            ("csv_quotechar", "''"),
        ],
    )
    async def test_authorization_invalid_password_payloads(self, client, field, value):
        payload = _valid_password_payload()
        payload["SFTP"][field] = value

        with patch("routers.sftp_authorization_router.test_sftp_connection", return_value=None):
            resp = await client.post("/SFTP-Authorization", json=payload)

        assert resp.status_code == 422

    async def test_authorization_key_pair_missing_private_key(self, client):
        payload = _valid_password_payload()
        payload["SFTP"].pop("sftp_password", None)
        payload["SFTP"]["authentication_type"] = "key_pair"

        with patch("routers.sftp_authorization_router.test_sftp_connection", return_value=None):
            resp = await client.post("/SFTP-Authorization", json=payload)

        assert resp.status_code == 422
