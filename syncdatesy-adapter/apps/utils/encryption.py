"""AES-GCM field encryption with legacy and master-key versions."""
from __future__ import annotations

import ast
import base64
import json
import logging
import os
from typing import Any, Optional, Union

from cryptography.exceptions import InvalidKey, InvalidTag
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)
KeyMaterial = Union[int, str]


def _django_field_encryption_key() -> str:
    try:
        from django.conf import settings

        return (getattr(settings, "FIELD_ENCRYPTION_KEY", None) or "").strip()
    except Exception:
        return (os.getenv("FIELD_ENCRYPTION_KEY") or "").strip()


def _django_field_encryption_version() -> int:
    try:
        from django.conf import settings

        return int(getattr(settings, "FIELD_ENCRYPTION_KEY_VERSION", 1) or 1)
    except Exception:
        try:
            return int(os.getenv("FIELD_ENCRYPTION_KEY_VERSION", "1"))
        except ValueError:
            return 1


def derive_key(key_material: KeyMaterial, salt: bytes, iterations: int = 100000) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
        backend=default_backend(),
    )
    return kdf.derive(str(key_material).encode())


def encrypt_field(data, key_material: KeyMaterial, iterations: int = 100000, key_version: Optional[int] = None):
    original_type = type(data).__name__
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = derive_key(key_material, salt, iterations)
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(str(data).encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
    result = [
        base64.b64encode(encrypted_data).decode(),
        base64.b64encode(nonce).decode(),
        base64.b64encode(encryptor.tag).decode(),
        base64.b64encode(salt).decode(),
        original_type,
        iterations,
    ]
    if key_version is not None:
        result.append(int(key_version))
    return result


def decrypt_field(
    encrypted_data: str,
    key_material: KeyMaterial,
    nonce: str,
    tag: str,
    salt: str,
    original_type: str,
    iterations: int = 100000,
):
    try:
        encrypted_data_b = base64.b64decode(encrypted_data)
        nonce_b = base64.b64decode(nonce)
        tag_b = base64.b64decode(tag)
        salt_b = base64.b64decode(salt)
        key = derive_key(key_material, salt_b, iterations)
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce_b, tag_b), backend=default_backend())
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(encrypted_data_b) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        data = unpadder.update(padded_data) + unpadder.finalize()
        if original_type == "str":
            return data.decode("utf-8")
        if original_type == "int":
            return int(data.decode("utf-8"))
        if original_type == "float":
            return float(data.decode("utf-8"))
        if original_type == "dict":
            data_str = data.decode("utf-8")
            try:
                return ast.literal_eval(data_str)
            except (ValueError, SyntaxError):
                try:
                    return json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning("Failed to decode encrypted dict payload")
                    return None
        return data
    except InvalidTag as e:
        raise ValueError("Decryption failed: Authentication tag mismatch or tampered data.") from e
    except InvalidKey as e:
        raise ValueError("Decryption failed: Invalid key.") from e
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}") from e


def parse_encrypted_payload(raw: Any) -> Optional[list]:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if isinstance(raw, (list, tuple)) and len(raw) in (6, 7):
        return list(raw)
    return None


def encrypt_sensitive(data, legacy_key_material: KeyMaterial) -> list:
    master = _django_field_encryption_key()
    if master:
        return encrypt_field(data, master, key_version=_django_field_encryption_version())
    return encrypt_field(data, legacy_key_material)


def decrypt_sensitive(payload, legacy_key_material: KeyMaterial, legacy_fallback: Optional[KeyMaterial] = None):
    parsed = parse_encrypted_payload(payload)
    if parsed is None:
        return payload
    if len(parsed) == 7:
        encrypted_data, nonce, tag, salt, original_type, iterations, _version = parsed
        master = _django_field_encryption_key()
        if not master:
            raise ValueError("FIELD_ENCRYPTION_KEY required to decrypt versioned payload")
        return decrypt_field(encrypted_data, master, nonce, tag, salt, original_type, iterations)
    encrypted_data, nonce, tag, salt, original_type, iterations = parsed
    try:
        return decrypt_field(
            encrypted_data, legacy_key_material, nonce, tag, salt, original_type, iterations
        )
    except Exception:
        if legacy_fallback is not None:
            return decrypt_field(
                encrypted_data, legacy_fallback, nonce, tag, salt, original_type, iterations
            )
        raise
