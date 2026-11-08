"""SAP Snowflake engine reads destination passphrase field."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

from services.sap.as_source.to_snowflake import _get_snowflake_engine


def _encrypted_pem(passphrase: str) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode()),
    ).decode()


def test_get_snowflake_engine_uses_passphrase_field(monkeypatch):
    pem = _encrypted_pem("secret-pass")
    captured = {}

    def fake_create_engine(url, connect_args=None):
        captured["url"] = url
        captured["connect_args"] = connect_args
        return object()

    monkeypatch.setattr("services.sap.as_source.to_snowflake.create_engine", fake_create_engine)

    _get_snowflake_engine(
        {
            "account": "xy12345",
            "username": "loader",
            "warehouse": "WH",
            "database": "DB",
            "schema": "SAP",
            "role": "LOADER",
            "private_key": pem,
            "passphrase": "secret-pass",
        }
    )

    assert "private_key" in captured["connect_args"]


def test_get_snowflake_engine_falls_back_to_private_key_passphrase(monkeypatch):
    pem = _encrypted_pem("legacy-pass")

    def fake_create_engine(url, connect_args=None):
        assert connect_args and "private_key" in connect_args
        return object()

    monkeypatch.setattr("services.sap.as_source.to_snowflake.create_engine", fake_create_engine)

    _get_snowflake_engine(
        {
            "account": "xy12345",
            "username": "loader",
            "warehouse": "WH",
            "database": "DB",
            "schema": "SAP",
            "role": "LOADER",
            "private_key": pem,
            "private_key_passphrase": "legacy-pass",
        }
    )
