"""
Unit tests for SharePoint service validation and live connectivity checks.
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from models.sharepoint_models import SharePointAuthorizationRequest
from services.sharepoint.client import (
    test_sharepoint_connection,
    validate_sharepoint_authorization,
)


def _valid_config(**overrides):
    config = {
        "auth_type": "client_secret",
        "tenant_id": "tenant-123",
        "client_id": "client-123",
        "client_secret": "secret-value",
        "sharepoint_site_url": "https://example.sharepoint.com/sites/demo",
        "document_library": "Documents",
        "root_folder": "/exports",
    }
    config.update(overrides)
    return config


def _request(config):
    return SharePointAuthorizationRequest(SharePoint=config)


def _assert_validation_error(config, expected_detail):
    with pytest.raises(HTTPException) as exc:
        validate_sharepoint_authorization(_request(config))

    assert exc.value.status_code == 422
    assert exc.value.detail == expected_detail


def test_validate_sharepoint_authorization_accepts_client_secret_payload():
    validate_sharepoint_authorization(_request(_valid_config()))


@patch("services.sharepoint.client.serialization.load_pem_private_key", return_value=object())
@patch("services.sharepoint.client.x509.load_pem_x509_certificate", return_value=object())
def test_validate_sharepoint_authorization_accepts_client_certificate_payload(_mock_cert, _mock_key):
    validate_sharepoint_authorization(
        _request(
            _valid_config(
                auth_type="client_certificate",
                client_secret=None,
                client_certificate=(
                    "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
                    "-----BEGIN PRIVATE KEY-----\nxyz\n-----END PRIVATE KEY-----"
                ),
            )
        )
    )


def test_validate_sharepoint_authorization_accepts_empty_root_folder():
    validate_sharepoint_authorization(_request(_valid_config(root_folder="")))


@pytest.mark.parametrize(
    "overrides,expected_detail",
    [
        ({"auth_type": ""}, "Invalid auth_type; expected client_secret or client_certificate"),
        ({"auth_type": "oauth"}, "Invalid auth_type; expected client_secret or client_certificate"),
        ({"tenant_id": ""}, "Missing required field: tenant_id"),
        ({"client_id": ""}, "Missing required field: client_id"),
        ({"client_secret": ""}, "Missing required field: client_secret"),
        (
            {"auth_type": "client_certificate", "client_secret": None},
            "Missing required field: client_certificate_file",
        ),
        ({"sharepoint_site_url": ""}, "Missing required field: sharepoint_site_url"),
        (
            {"sharepoint_site_url": "not-a-url"},
            "Invalid sharepoint_site_url; expected a full http(s) URL",
        ),
        ({"document_library": ""}, "Missing required field: document_library"),
        (
            {"root_folder": "folder\\nested"},
            "Invalid root_folder; use SharePoint-style '/' path separators",
        ),
        (
            {"root_folder": ".."},
            "Invalid root_folder; relative traversal paths are not allowed",
        ),
    ],
)
def test_validate_sharepoint_authorization_rejects_invalid_payloads(overrides, expected_detail):
    _assert_validation_error(_valid_config(**overrides), expected_detail)


@patch("services.sharepoint.client.requests.request")
@patch("services.sharepoint.client.ClientSecretCredential")
def test_sharepoint_connection_success_client_secret(mock_credential_cls, mock_request):
    mock_credential = mock_credential_cls.return_value
    mock_credential.get_token.return_value.token = "graph-token"

    mock_request.side_effect = [
        _response(
            200,
            {
                "id": "site-1",
                "displayName": "Demo Site",
                "webUrl": "https://example.sharepoint.com/sites/demo",
            },
        ),
        _response(
            200,
            {
                "value": [
                    {"id": "drive-1", "name": "Documents"},
                ]
            },
        ),
        _response(
            200,
            {
                "id": "folder-1",
                "name": "exports",
                "folder": {},
            },
        ),
    ]

    result = test_sharepoint_connection(_request(_valid_config()))

    assert result["site_id"] == "site-1"
    assert result["drive_id"] == "drive-1"
    assert result["root_folder_id"] == "folder-1"


@patch("services.sharepoint.client.requests.request")
@patch("services.sharepoint.client.ClientSecretCredential")
def test_sharepoint_connection_root_folder_optional_uses_drive_root(mock_credential_cls, mock_request):
    mock_credential = mock_credential_cls.return_value
    mock_credential.get_token.return_value.token = "graph-token"

    mock_request.side_effect = [
        _response(200, {"id": "site-1", "displayName": "Demo Site", "webUrl": "https://example.sharepoint.com/sites/demo"}),
        _response(200, {"value": [{"id": "drive-1", "name": "Documents"}]}),
        _response(200, {"id": "root-folder", "folder": {}}),
    ]

    result = test_sharepoint_connection(_request(_valid_config(root_folder="")))

    assert result["root_folder"] == "/"
    assert result["root_folder_id"] == "root-folder"


@patch("services.sharepoint.client.ClientSecretCredential")
def test_sharepoint_connection_maps_invalid_client_secret(mock_credential_cls):
    mock_credential = mock_credential_cls.return_value
    mock_credential.get_token.side_effect = Exception("AADSTS7000215: Invalid client secret is provided.")

    with pytest.raises(HTTPException) as exc:
        test_sharepoint_connection(_request(_valid_config()))

    assert exc.value.status_code == 422
    assert exc.value.detail == "SharePoint client_secret is invalid or expired."


@patch("services.sharepoint.client.requests.request")
@patch("services.sharepoint.client.ClientSecretCredential")
def test_sharepoint_connection_maps_site_not_found(mock_credential_cls, mock_request):
    mock_credential = mock_credential_cls.return_value
    mock_credential.get_token.return_value.token = "graph-token"
    mock_request.side_effect = [
        _response(404, {"error": {"message": "Item not found"}}),
    ]

    with pytest.raises(HTTPException) as exc:
        test_sharepoint_connection(_request(_valid_config()))

    assert exc.value.status_code == 422
    assert exc.value.detail == "SharePoint site not found or not accessible: https://example.sharepoint.com/sites/demo"


@patch("services.sharepoint.client.requests.request")
@patch("services.sharepoint.client.ClientSecretCredential")
def test_sharepoint_connection_maps_document_library_not_found(mock_credential_cls, mock_request):
    mock_credential = mock_credential_cls.return_value
    mock_credential.get_token.return_value.token = "graph-token"
    mock_request.side_effect = [
        _response(200, {"id": "site-1", "displayName": "Demo Site", "webUrl": "https://example.sharepoint.com/sites/demo"}),
        _response(200, {"value": [{"id": "drive-1", "name": "General"}]}),
    ]

    with pytest.raises(HTTPException) as exc:
        test_sharepoint_connection(_request(_valid_config(document_library="Documents")))

    assert exc.value.status_code == 422
    assert exc.value.detail == "Document library not found on the SharePoint site: Documents"


@patch("services.sharepoint.client.requests.request")
@patch("services.sharepoint.client.ClientSecretCredential")
def test_sharepoint_connection_maps_root_folder_not_found(mock_credential_cls, mock_request):
    mock_credential = mock_credential_cls.return_value
    mock_credential.get_token.return_value.token = "graph-token"
    mock_request.side_effect = [
        _response(200, {"id": "site-1", "displayName": "Demo Site", "webUrl": "https://example.sharepoint.com/sites/demo"}),
        _response(200, {"value": [{"id": "drive-1", "name": "Documents"}]}),
        _response(404, {"error": {"message": "Item not found"}}),
    ]

    with pytest.raises(HTTPException) as exc:
        test_sharepoint_connection(_request(_valid_config(root_folder="/exports")))

    assert exc.value.status_code == 422
    assert exc.value.detail == "Root folder not found in document library: /exports"


class _MockResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"
        self.text = str(payload)

    def json(self):
        return self._payload


def _response(status_code, payload):
    return _MockResponse(status_code, payload)
