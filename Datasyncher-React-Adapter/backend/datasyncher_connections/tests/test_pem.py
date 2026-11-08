import pytest

from datasyncher_connections.snowflake.pem import (
    load_private_key_der,
    load_private_key_object,
    normalize_pem_key,
)


def test_normalize_pem_key_fixes_escaped_newlines(unencrypted_pem_key):
    escaped = unencrypted_pem_key.replace("\n", "\\n")
    normalized = normalize_pem_key(escaped)
    assert "\n" in normalized
    assert "\\n" not in normalized.replace("\\n", "") or "-----BEGIN" in normalized


def test_normalize_pem_key_empty_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_pem_key("")


def test_load_private_key_object_unencrypted(unencrypted_pem_key):
    key = load_private_key_object(unencrypted_pem_key)
    assert key is not None


def test_load_private_key_object_encrypted(encrypted_pem_key):
    pem, passphrase = encrypted_pem_key
    key = load_private_key_object(pem, passphrase)
    assert key is not None


def test_load_private_key_object_wrong_passphrase(encrypted_pem_key):
    pem, _ = encrypted_pem_key
    with pytest.raises(ValueError):
        load_private_key_object(pem, "wrong-pass")


def test_load_private_key_der_returns_bytes(unencrypted_pem_key):
    der = load_private_key_der(unencrypted_pem_key)
    assert isinstance(der, bytes)
    assert len(der) > 0


def test_load_private_key_object_invalid_pem():
    with pytest.raises(ValueError, match="PEM format"):
        load_private_key_object("not-a-pem-key")
