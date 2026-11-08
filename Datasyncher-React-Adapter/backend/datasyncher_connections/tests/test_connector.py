from unittest.mock import MagicMock, patch

import pytest

from datasyncher_connections.snowflake.connector import create_snowflake_connector


@patch("datasyncher_connections.snowflake.connector.snowflake.connector.connect")
def test_password_auth(mock_connect, unencrypted_pem_key):
    mock_connect.return_value = MagicMock()
    config = {
        "account": "acct",
        "user": "user",
        "password": "pass",
        "warehouse": "WH",
        "database": "DB",
        "schema": "SCHEMA",
        "authentication_type": "password",
    }
    create_snowflake_connector(config, preflight=False)
    params = mock_connect.call_args[1]
    assert params["password"] == "pass"


@patch("datasyncher_connections.snowflake.connector.snowflake.connector.connect")
def test_keypair_auth(mock_connect, unencrypted_pem_key):
    mock_connect.return_value = MagicMock()
    config = {
        "account": "acct",
        "user": "user",
        "warehouse": "WH",
        "database": "DB",
        "schema": "SCHEMA",
        "authentication_type": "key_pair",
        "private_key": unencrypted_pem_key,
    }
    create_snowflake_connector(config, preflight=False)
    params = mock_connect.call_args[1]
    assert "private_key" in params
    assert params["private_key"] is not None


def test_missing_credentials_raises():
    with pytest.raises(ValueError, match="Authentication failed"):
        create_snowflake_connector(
            {"account": "a", "user": "u", "authentication_type": "password"},
            preflight=False,
        )
