from unittest.mock import MagicMock, patch

import pytest

from datasyncher_connections.snowflake.sqlalchemy_engine import create_snowflake_sqlalchemy_engine


@pytest.fixture
def snowflake_config(unencrypted_pem_key):
    return {
        "user": "test_user",
        "account": "xy12345",
        "database": "TEST_DB",
        "warehouse": "TEST_WH",
        "schema": "test_schema",
        "role": "TEST_ROLE",
        "authentication_type": "password",
        "password": "secret",
    }


@patch("datasyncher_connections.snowflake.sqlalchemy_engine.create_engine")
def test_salesforce_flavor_password_url(mock_create_engine, snowflake_config):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_create_engine.return_value = mock_engine

    engine, schema = create_snowflake_sqlalchemy_engine(
        snowflake_config,
        flavor="salesforce",
        validate_version=False,
    )

    assert schema == "TEST_SCHEMA"
    assert engine is mock_engine
    url = mock_create_engine.call_args[0][0]
    assert "test_schema" in url.lower() or "TEST_SCHEMA" in url
    assert "TEST_ROLE" in url


@patch("datasyncher_connections.snowflake.sqlalchemy_engine.create_engine")
def test_dynamics_flavor_password_url(mock_create_engine, snowflake_config):
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = MagicMock()
    mock_create_engine.return_value = mock_engine

    create_snowflake_sqlalchemy_engine(snowflake_config, flavor="dynamics")

    url = mock_create_engine.call_args[0][0]
    assert "warehouse=TEST_WH" in url
    assert "/TEST_DB?" in url or "/TEST_DB" in url


@patch("datasyncher_connections.snowflake.sqlalchemy_engine.create_engine")
def test_dynamics_keypair_uses_der_connect_args(mock_create_engine, snowflake_config, unencrypted_pem_key):
    snowflake_config.update(
        {
            "authentication_type": "key_pair",
            "private_key": unencrypted_pem_key,
            "password": None,
        }
    )
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = MagicMock()
    mock_create_engine.return_value = mock_engine

    create_snowflake_sqlalchemy_engine(snowflake_config, flavor="dynamics")

    connect_args = mock_create_engine.call_args[1]["connect_args"]
    assert isinstance(connect_args["private_key"], bytes)


@patch("datasyncher_connections.snowflake.sqlalchemy_engine.create_engine")
def test_salesforce_keypair_uses_key_object(mock_create_engine, snowflake_config, unencrypted_pem_key):
    snowflake_config.update(
        {
            "authentication_type": "key_pair",
            "private_key": unencrypted_pem_key,
            "password": None,
        }
    )
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = MagicMock()
    mock_create_engine.return_value = mock_engine

    create_snowflake_sqlalchemy_engine(snowflake_config, flavor="salesforce")

    connect_args = mock_create_engine.call_args[1]["connect_args"]
    assert not isinstance(connect_args["private_key"], (bytes, bytearray))


@patch("datasyncher_connections.snowflake.sqlalchemy_engine.create_engine")
def test_on_error_callback_invoked(mock_create_engine, snowflake_config):
    mock_create_engine.side_effect = RuntimeError("boom")
    errors = []

    with pytest.raises(RuntimeError):
        create_snowflake_sqlalchemy_engine(
            snowflake_config,
            on_error=lambda exc: errors.append(exc),
        )

    assert len(errors) == 1
