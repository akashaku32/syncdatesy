"""Salesforce JWT Bearer authentication tests."""

import sys
import time
from unittest.mock import MagicMock, patch

if "google.cloud.storage" not in sys.modules:
    sys.modules["google.cloud.storage"] = MagicMock()
    sys.modules["google.cloud"] = MagicMock()

from utils.salesforce_jwt_auth import (
    build_jwt_assertion,
    exchange_jwt_for_access_token,
    format_salesforce_jwt_exchange_error,
    get_salesforce_jwt_access_token,
    is_salesforce_jwt_auth,
)


def test_is_salesforce_jwt_auth():
    assert is_salesforce_jwt_auth({"authentication_type": "jwt_bearer"}) is True
    assert is_salesforce_jwt_auth({"authentication_type": "oauth"}) is False
    assert is_salesforce_jwt_auth(None) is False


def test_build_jwt_assertion_claims():
    with patch(
        "utils.salesforce_jwt_auth._load_signing_key",
        return_value="signing-key",
    ), patch("utils.salesforce_jwt_auth.jwt.encode", return_value="signed-jwt") as encode_mock:
        token = build_jwt_assertion(
            "consumer-key",
            "integration@example.com",
            "private-key",
            "https://test.salesforce.com/services/oauth2/token",
        )

    assert token == "signed-jwt"
    args, kwargs = encode_mock.call_args
    payload = args[0]
    assert payload["iss"] == "consumer-key"
    assert payload["sub"] == "integration@example.com"
    assert payload["aud"] == "https://test.salesforce.com/services/oauth2/token"
    assert payload["exp"] > int(time.time())
    assert kwargs["algorithm"] == "RS256"


def test_format_jwt_exchange_error_preauthorize_hint():
    msg = format_salesforce_jwt_exchange_error(
        "refresh_token scope is required and the connected app should be installed and preauthorized."
    )
    assert "App Policies" in msg
    assert "refresh_token" in msg


def test_exchange_jwt_for_access_token_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "access-123",
        "instance_url": "https://example.sandbox.my.salesforce.com",
    }

    with patch(
        "utils.salesforce_jwt_auth.build_jwt_assertion",
        return_value="assertion",
    ), patch("utils.salesforce_jwt_auth.requests.post", return_value=mock_response):
        tokens = exchange_jwt_for_access_token(
            "consumer-key",
            "integration@example.com",
            "private-key",
            "sandbox",
            custom_domain="ati",
        )

    assert tokens["access_token"] == "access-123"
    assert "sandbox.my.salesforce.com" in tokens["instance_url"]


def test_get_salesforce_jwt_access_token_uses_cache():
    credentials = {
        "authentication_type": "jwt_bearer",
        "CLIENT_ID": "consumer-key",
        "integration_username": "integration@example.com",
        "private_key": "private-key",
    }
    cached = '{"access_token":"cached","instance_url":"https://cached.salesforce.com"}'

    with patch("utils.salesforce_jwt_auth._get_redis_client") as redis_mock:
        redis_mock.return_value.get.return_value = cached
        tokens = get_salesforce_jwt_access_token(
            credentials,
            connection_id=26,
            role="source",
            environment="sandbox",
        )

    assert tokens["access_token"] == "cached"
    redis_mock.return_value.get.assert_called_once()


def test_format_salesforce_migration_auth_error_jwt_passthrough():
    from services.salesforce.client import format_salesforce_migration_auth_error

    creds = {"authentication_type": "jwt_bearer"}
    msg = "Salesforce JWT token exchange failed: refresh_token scope is required"
    assert format_salesforce_migration_auth_error(
        msg, environment="production", credentials=creds
    ) == msg


def test_format_salesforce_migration_auth_error_oauth_sandbox():
    from services.salesforce.client import format_salesforce_migration_auth_error

    msg = format_salesforce_migration_auth_error(
        "invalid_grant: expired access/refresh token",
        environment="sandbox",
        credentials={"authentication_type": "oauth"},
    )
    assert "Salesforce Sandbox" in msg
    assert "Reconnect" in msg


def test_salesforce_connection_uses_jwt_branch():
    from services.salesforce.client import salesforce_connection

    credentials = {
        "authentication_type": "jwt_bearer",
        "CLIENT_ID": "consumer-key",
        "integration_username": "integration@example.com",
        "private_key": "private-key",
    }
    oauth_context = {"connection_id": 26, "role": "source", "credentials": credentials}
    mock_sf = MagicMock()

    with patch(
        "services.salesforce.client._salesforce_connection_jwt",
        return_value=mock_sf,
    ) as jwt_connect:
        result = salesforce_connection(
            "state",
            "",
            "",
            "",
            "consumer-key",
            "",
            "sandbox",
            oauth_context=oauth_context,
        )

    assert result is mock_sf
    jwt_connect.assert_called_once()
