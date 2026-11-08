"""
cert_manager.py

Handles the mTLS certificate lifecycle for the agent:
- Parsing expiry (notAfter) from a PEM certificate
- Deciding whether a cert is close enough to expiry to renew
- Generating a NEW keypair + CSR locally (private key never leaves this process)
- Atomically persisting new credentials to config.json
"""

import os
import json
import tempfile
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pathlib import Path
import sys
def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
TEMP_DIR = APP_DIR / "temporary_files"


def get_cert_expiry(cert_pem: str) -> datetime:
    """Return the timezone-aware notAfter datetime of a PEM certificate."""
    cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"), default_backend())
    # cryptography >= 42 exposes the tz-aware accessor directly; fall back otherwise.
    expiry = getattr(cert, "not_valid_after_utc", None)
    if expiry is None:
        expiry = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return expiry


def seconds_until_expiry(cert_pem: str) -> float:
    return (get_cert_expiry(cert_pem) - datetime.now(timezone.utc)).total_seconds()


def needs_renewal(cert_pem: str, threshold_days: int = 7) -> bool:
    """True if the cert expires within `threshold_days` (or already has)."""
    return seconds_until_expiry(cert_pem) <= threshold_days * 86400


def generate_key_and_csr(common_name: str) -> tuple[str, str]:
    """
    Generate a brand-new private key + CSR locally.
    IMPORTANT: the private key is returned only to the in-process caller.
    It must never be sent to the server -- only the CSR is transmitted.

    Returns: (private_key_pem, csr_pem)
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(private_key, hashes.SHA256(), default_backend())
    )

    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")

    return private_key_pem, csr_pem


def atomic_write_json(path: str, data: dict) -> None:
    """
    Write JSON atomically: write to a temp file in the same directory,
    fsync it, then os.replace() over the target. This guarantees that if the
    process crashes mid-write, the existing (still-valid) config on disk is
    never left half-written or corrupted.
    """
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".config_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise