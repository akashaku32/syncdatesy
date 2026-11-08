"""Salesforce connection validation tests (OAuth + JWT Bearer)."""

from unittest.mock import MagicMock, patch

from services.salesforce.validation import validate_salesforce_connection_payload


def test_jwt_validation_uses_jwt_branch_without_connection_id():
    """Create-time validation must not fall through to OAuth (URL_NOT_RESET)."""
    payload = {
        "Salesforce": {
            "authentication_type": "jwt_bearer",
            "CLIENT_ID": "consumer-key",
            "integration_username": "user@example.com",
            "private_key": "private-key",
        }
    }
    mock_sf = MagicMock()
    mock_sf.session_id = "access-token"
    mock_sf.sf_instance = "example.my.salesforce.com"

    with patch(
        "services.salesforce.validation.salesforce_connection",
        return_value=mock_sf,
    ) as connect_mock:
        result = validate_salesforce_connection_payload(payload, environment="production")

    assert result["status_code"] == 200
    assert "JWT" in result["body"]
    connect_mock.assert_called_once()
    oauth_context = connect_mock.call_args.kwargs["oauth_context"]
    assert oauth_context["credentials"]["authentication_type"] == "jwt_bearer"
    assert oauth_context["connection_id"] is None


def test_jwt_validation_returns_401_on_connect_failure():
    payload = {
        "authentication_type": "jwt_bearer",
        "CLIENT_ID": "consumer-key",
        "integration_username": "user@example.com",
        "private_key": "private-key",
    }

    with patch(
        "services.salesforce.validation.salesforce_connection",
        side_effect=ValueError("Salesforce JWT token exchange failed: invalid_client"),
    ):
        result = validate_salesforce_connection_payload(payload, environment="production")

    assert result["status_code"] == 401
    assert "JWT token exchange failed" in result["body"]
