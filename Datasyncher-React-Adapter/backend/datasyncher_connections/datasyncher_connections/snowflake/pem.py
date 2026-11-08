"""PEM normalization and private-key loading for Snowflake key-pair auth."""

from __future__ import annotations

import re

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization


def normalize_pem_key(key_str: str) -> str:
    """
    Normalize PEM key string by fixing newlines and ensuring proper formatting.

    Behavioral baseline: migration-function ``snowflake_service.normalize_pem_key``.
    """
    if not key_str:
        raise ValueError("Key string cannot be empty")

    key_str = key_str.strip()

    if "-----BEGIN" not in key_str.upper() or "-----END" not in key_str.upper():
        pass  # preserve legacy warning-only behavior in callers

    key_str = key_str.replace("\\n", "\n")

    if "-----BEGIN" in key_str:
        begin_match = re.search(r"-----BEGIN [^-]+-----", key_str)
        if begin_match:
            begin_end_pos = begin_match.end()
            if begin_end_pos < len(key_str) and key_str[begin_end_pos] != "\n":
                key_str = re.sub(r"(-----BEGIN [^-]+-----)\s*", r"\1\n", key_str, count=1)

    if "-----END" in key_str:
        end_match = re.search(r"-----END [^-]+-----", key_str)
        if end_match:
            end_start_pos = end_match.start()
            if end_start_pos > 0 and key_str[end_start_pos - 1] != "\n":
                key_str = re.sub(r"\s*(-----END [^-]+-----)", r"\n\1", key_str, count=1)

    return key_str


def _passphrase_bytes(passphrase: str | None) -> bytes | None:
    if passphrase and str(passphrase).strip():
        return str(passphrase).encode("utf-8")
    return None


def load_private_key_object(private_key_str: str, passphrase: str | None = None):
    """
    Load PEM private key as a cryptography key object.

    Behavioral baseline: ``SnowflakeService._load_private_key_from_string``.
    """
    if not private_key_str:
        raise ValueError("Private key string cannot be empty")
    if not isinstance(private_key_str, str):
        raise ValueError(f"Private key must be a string, got {type(private_key_str)}")

    key_upper = private_key_str.upper()
    if "-----BEGIN" not in key_upper or "-----END" not in key_upper:
        raise ValueError(
            "Error: Private key must be in PEM format with -----BEGIN and -----END headers."
        )

    password_bytes = _passphrase_bytes(passphrase)

    try:
        return serialization.load_pem_private_key(
            private_key_str.encode(),
            password=password_bytes,
            backend=default_backend(),
        )
    except ValueError as exc:
        if not password_bytes and passphrase and str(passphrase).strip():
            try:
                return serialization.load_pem_private_key(
                    private_key_str.encode(),
                    password=str(passphrase).encode("utf-8"),
                    backend=default_backend(),
                )
            except ValueError as retry_err:
                raise ValueError(
                    "Error: Failed to load private key. Please verify the passphrase is correct "
                    "and the key format is valid PEM."
                ) from retry_err
        if password_bytes:
            try:
                serialization.load_pem_private_key(
                    private_key_str.encode(),
                    password=None,
                    backend=default_backend(),
                )
                raise ValueError(
                    "Error: Unexpected error validating the private key. A passphrase was provided, "
                    "but the private key is not encrypted for the given passphrase."
                )
            except (ValueError, TypeError):
                raise ValueError(
                    "Error: Failed to load private key. The key may be encrypted with a different "
                    "passphrase, corrupted, or in an unsupported format."
                ) from exc
        raise ValueError(
            "Error: Failed to load private key. If the key is encrypted, provide the correct passphrase."
        ) from exc


def load_private_key_der(private_key_str: str, passphrase: str | None = None) -> bytes:
    """
    Load PEM private key and return DER PKCS8 bytes for SQLAlchemy ``connect_args``.

    Behavioral baseline: Dynamics ``_load_private_key_from_string``.
    """
    p_key = load_private_key_object(private_key_str, passphrase)
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
