"""Router-level tests for Google Drive authorization and source proxies."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from routers.google_drive_router import router


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


def _oauth_data():
    return {
        "CLIENT_ID": "drive-client",
        "CLIENT_SECRET": "drive-secret",
        "redirect_uri": "https://example.test/source/googledrive/callback/",
    }


def _listing_payload():
    return {
        "connection_id": 123,
        "cmp_id": 1,
        "user_id": 2,
        "GoogleDrive": {
            **_oauth_data(),
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "folder_id": "folder-1",
            "folder_name": "Imports",
            "file_type": "csv",
        },
    }


class TestGoogleDriveAuthorizationRouter:
    async def test_authorization_success(self, client):
        with patch(
            "routers.google_drive_router.get_google_drive_authorize_url",
            return_value="https://accounts.google.test/oauth",
        ) as mock_authorize:
            response = await client.get(
                "/Source-GoogleDrive-Authorization",
                params={
                    "state": "csrf-state",
                    "redirect_uri": _oauth_data()["redirect_uri"],
                    "data": json.dumps(_oauth_data()),
                },
            )

        assert response.status_code == 200
        assert response.json() == {"auth_url": "https://accounts.google.test/oauth"}
        mock_authorize.assert_called_once()

    async def test_authorization_rejects_invalid_json(self, client):
        response = await client.get(
            "/Source-GoogleDrive-Authorization",
            params={
                "state": "csrf-state",
                "redirect_uri": _oauth_data()["redirect_uri"],
                "data": "{invalid-json",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid JSON format in 'data'."

    async def test_callback_exchanges_code(self, client):
        token = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "token_expires_at": 2_000_000_000,
            "state": "csrf-state",
        }
        with patch(
            "routers.google_drive_router.get_access_token",
            return_value=token,
        ) as mock_exchange:
            response = await client.get(
                "/google-drive/callback",
                params={
                    "code": "authorization-code",
                    "state": "csrf-state",
                    "data_new": json.dumps(_oauth_data()),
                },
            )

        assert response.status_code == 200
        assert response.json() == token
        mock_exchange.assert_called_once()

    async def test_validation_proxy_success(self, client):
        expected = {"status_code": 200, "body": "validated"}
        with patch(
            "routers.google_drive_router.validate_google_drive_connection",
            return_value=expected,
        ):
            response = await client.post(
                "/GoogleDrive-Authorization",
                json={"GoogleDrive": _listing_payload()["GoogleDrive"]},
            )

        assert response.status_code == 200
        assert response.json() == expected

    async def test_listing_proxy_success(self, client):
        result = {"tables": [{"table": "LIBRARY"}], "files_count": 1}
        with patch(
            "routers.google_drive_router.GoogleDriveSnowflakeService.list_files",
            return_value=result,
        ):
            response = await client.post(
                "/GoogleDrive-listing-files",
                json=_listing_payload(),
            )

        assert response.status_code == 200
        assert response.json() == {"result": result}

    async def test_preview_proxy_success(self, client):
        payload = {
            **_listing_payload(),
            "multi_files_prefix": "library_*.csv",
            "max_results": 10,
        }
        expected = {"status": "success", "matching_files": ["library_1.csv"]}
        with patch(
            "routers.google_drive_router.GoogleDriveSnowflakeService.preview_pattern",
            return_value=expected,
        ):
            response = await client.post("/GoogleDrive-preview-pattern", json=payload)

        assert response.status_code == 200
        assert response.json() == expected

    async def test_pk_suggestion_proxy_success(self, client):
        payload = {
            "sample_rows": 10,
            "connection_id": 123,
            "cmp_id": 1,
            "user_id": 2,
            "GoogleDrive": {
                **_listing_payload()["GoogleDrive"],
                "file_mapping_method": "single_table_per_file",
                "single_file_table_mapping": {"library.csv": "LIBRARY"},
                "load_method": "upsert_custom_key",
            },
        }
        expected = {"status": "success", "tables": []}
        with patch(
            "routers.google_drive_router.GoogleDriveSnowflakeService.suggest_primary_keys",
            new=AsyncMock(return_value=expected),
        ):
            response = await client.post(
                "/GoogleDrive-suggest-primary-keys",
                json=payload,
            )

        assert response.status_code == 200
        assert response.json() == expected

