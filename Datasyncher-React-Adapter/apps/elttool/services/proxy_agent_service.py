"""
proxy_agent_service.py
----------------------
Service layer for Proxy Agent configuration generation.

Security model:
- bootstrap_token is generated in memory and returned raw to the caller exactly once.
  Only its SHA-256 hex digest is written to the DB (bootstrap_token_hash).
- The client private key is generated in memory, returned to the caller, and NEVER stored.
- Revocation is by certificate expiry (90-day validity by default). There is no active
  revocation flag -- decommission by generating a new agent config.
- agent_uid collisions are astronomically rare (16 hex chars = 18 quintillion combinations)
  but are handled by a retry loop (max MAX_UID_RETRIES attempts) catching IntegrityError.
"""

import logging
import os
import secrets
from datetime import datetime, timezone, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone as dj_timezone

from apps.utils.encryption import encrypt_field

logger = logging.getLogger(__name__)

MAX_UID_RETRIES = 5
DEFAULT_CERT_VALIDITY_DAYS = 90


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def generate_bootstrap_token(cmp_id: int) -> tuple:
    """
    Generate a secure bootstrap token and encrypt it using the app's AES-GCM
    encryption (same mechanism as Company/SourceConfig sensitive fields).

    Returns:
        (raw_token, encrypted_payload)

    raw_token format: "{cmp_id}-{48 hex chars}"
    encrypted_payload: JSON-serialisable list [encrypted_data, nonce, tag, salt,
                       original_type, iterations] produced by encrypt_field().

    Only the encrypted_payload is written to the DB (bootstrap_token JSONField).
    The raw_token must be returned to the user in config.json and then discarded.
    """
    random_part = secrets.token_hex(24)   # 48 hex chars
    raw_token = f"{cmp_id}-{random_part}"
    encrypted_payload = encrypt_field(raw_token, cmp_id)
    return raw_token, encrypted_payload


def generate_agent_uid(cmp_id: int) -> str:
    """
    Generate a unique agent UID: "agent-{cmp_id}-{16 random hex chars}".

    The caller is responsible for retrying on collision (DB unique constraint).
    """
    suffix = secrets.token_hex(8)   # 16 hex chars
    return f"agent-{cmp_id}-{suffix}"


# ---------------------------------------------------------------------------
# CA / Certificate helpers
# ---------------------------------------------------------------------------

def _get_ca_path(setting_name: str) -> str:
    """Resolve and validate a CA file path from Django settings."""
    path = getattr(settings, setting_name, None)
    exists = os.path.exists(path) if path else False
    is_file = os.path.isfile(path) if path else False
    readable = os.access(path, os.R_OK) if path else False

    logger.info(
        "proxy_agent_service [_get_ca_path]: setting=%s path='%s' exists=%s is_file=%s readable=%s",
        setting_name, path, exists, is_file, readable,
    )

    if not path:
        raise ImproperlyConfigured(
            f"Django setting '{setting_name}' is not configured. "
            f"Set it to the absolute path of the CA file on this server."
        )
    if not is_file:
        raise ImproperlyConfigured(
            f"CA file not found at '{path}' (setting: {setting_name}, exists={exists}, readable={readable}). "
            f"Ensure the file exists inside the container and the process user has read permission."
        )
    return path


def load_ca_credentials():
    """
    Load the CA certificate and private key from the configured filesystem paths.

    Settings required:
        PROXY_AGENT_CA_CERT_PATH  -- absolute path to ca.crt
        PROXY_AGENT_CA_KEY_PATH   -- absolute path to ca.key (must be 440, group-readable)

    Raises:
        ImproperlyConfigured -- if paths are missing, files are absent, or files are unreadable.
    """
    cert_path = _get_ca_path("PROXY_AGENT_CA_CERT_PATH")
    key_path = _get_ca_path("PROXY_AGENT_CA_KEY_PATH")

    logger.info("proxy_agent_service [load_ca_credentials]: loading cert_path='%s', key_path='%s'", cert_path, key_path)

    try:
        with open(cert_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read())
    except PermissionError:
        raise ImproperlyConfigured(
            f"Permission denied reading CA cert at '{cert_path}'. "
            f"Ensure the process user has read permission."
        )
    except Exception as exc:
        raise ImproperlyConfigured(f"Failed to load CA cert from '{cert_path}': {exc}") from exc

    try:
        with open(key_path, "rb") as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None)
    except PermissionError:
        raise ImproperlyConfigured(
            f"Permission denied reading CA key at '{key_path}'. "
            f"Ensure the process user has read permission."
        )
    except Exception as exc:
        raise ImproperlyConfigured(f"Failed to load CA key from '{key_path}': {exc}") from exc

    logger.info("proxy_agent_service [load_ca_credentials]: CA credentials loaded successfully")
    return ca_cert, ca_key


def issue_client_certificate(
    agent_uid,
    ca_cert,
    ca_key,
    validity_days=DEFAULT_CERT_VALIDITY_DAYS,
):
    """
    Generate an RSA-2048 client key pair and issue a certificate signed by the CA.

    Certificate properties:
        - Subject CN = agent_uid
        - ExtendedKeyUsage: clientAuth only
        - BasicConstraints: CA=False
        - KeyUsage: digitalSignature
        - Validity: now to now + validity_days

    Returns:
        (client_cert_pem, client_private_key_pem, not_after_datetime)

    IMPORTANT: client_private_key_pem must never be written to the database or any log.
    """
    # Generate a fresh RSA-2048 key pair for this agent
    client_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    now = datetime.now(timezone.utc)
    not_after = now + timedelta(days=validity_days)

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, agent_uid),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    client_cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    client_key_pem = client_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    return client_cert_pem, client_key_pem, not_after


# ---------------------------------------------------------------------------
# Config assembly
# ---------------------------------------------------------------------------

def build_agent_config_json(
    agent_uid,
    client_cert_pem,
    client_key_pem,
    raw_token,
    trusted_ca_pem,
    *,
    progress_signing_secret=None,
):
    """
    Assemble the downloadable config.json dictionary.

    IMPORTANT: raw_token, client_key_pem, and progress_signing_secret must not be
    logged or persisted after this call (except encrypted server-side copies).
    """
    server_host = getattr(settings, "PROXY_AGENT_SERVER_HOST", "qa.datasyncher.com")
    server_port = int(getattr(settings, "PROXY_AGENT_SERVER_PORT", 50051))
    progress_host = getattr(
        settings,
        "PROXY_AGENT_PUBLIC_API_HOST",
        getattr(settings, "PROXY_AGENT_SERVER_HOST", "agent-api.datasyncher.com"),
    )
    progress_api_url = (
        getattr(settings, "PROXY_AGENT_PROGRESS_API_URL", None)
        or f"https://{progress_host}/v1/migration-progress"
    )
    progress_transport = getattr(
        settings,
        "PROXY_AGENT_PROGRESS_TRANSPORT",
        "grpc_with_https_fallback",
    )

    config = {
        "agent_id": agent_uid,
        "server_host": server_host,
        "server_port": server_port,
        "trusted_ca_cert": trusted_ca_pem,
        "client_cert": client_cert_pem,
        "client_private_key": client_key_pem,
        "bootstrap_token": raw_token,
        "progress_api_url": progress_api_url,
        "progress_transport": progress_transport,
    }
    if progress_signing_secret:
        config["progress_signing_secret"] = progress_signing_secret
    return config


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def create_proxy_agent(cmp_id, agent_name, user):
    """
    Orchestrate full Proxy Agent creation for a company:
        1. Unique agent_uid generation.
        2. Generate bootstrap token -> (raw_token, token_hash).
        3. Load CA credentials from filesystem.
        4. Issue client certificate signed by CA.
        5. Persist/update single ProxyAgent record for cmp_id (AES-GCM encrypted cert/token).
        6. Read trusted CA PEM for inclusion in config.json.
        7. Return (agent, config_dict) where config_dict includes private key and raw token.

    NOTE: proxy_agent is resolved dynamically from the company's ProxyAgent record at call time.
    It is NOT stored inside SourceConfig config_data (one agent per company).
    """
    from apps.elttool.models import Company, ProxyAgent, SourceConfig

    company = Company.objects.get(pk=cmp_id)
    if not agent_name or not str(agent_name).strip():
        agent_name = "Datasyncher Agent"

    # --- 1. Generate unique agent_uid with collision retry ---
    agent_uid = None
    for attempt in range(1, MAX_UID_RETRIES + 1):
        candidate = generate_agent_uid(cmp_id)
        if not ProxyAgent.objects.filter(agent_uid=candidate).exists():
            agent_uid = candidate
            break
        logger.warning(
            "proxy_agent_service: agent_uid collision on attempt %d: %s",
            attempt, candidate,
        )
    if agent_uid is None:
        raise RuntimeError(
            f"Could not generate a unique agent_uid for cmp_id={cmp_id} "
            f"after {MAX_UID_RETRIES} attempts."
        )

    # --- 2. Bootstrap token (encrypt with cmp_id key, raw stays in memory) ---
    raw_token, encrypted_token = generate_bootstrap_token(cmp_id)

    # --- 3 & 4. CA credentials + client certificate ---
    ca_cert, ca_key = load_ca_credentials()
    client_cert_pem, client_key_pem, not_after = issue_client_certificate(
        agent_uid, ca_cert, ca_key
    )

    # Encrypt client_cert_pem using cmp_id key before persisting to DB
    encrypted_cert_pem = encrypt_field(client_cert_pem, cmp_id)

    # Progress HMAC secret: raw in config once; hash + encrypted copy in DB.
    raw_progress_secret = secrets.token_hex(32)
    from apps.elttool.services.proxy_agent_migration_progress import (
        hash_progress_signing_secret,
    )

    progress_secret_hash = hash_progress_signing_secret(raw_progress_secret)
    encrypted_progress_secret = encrypt_field(raw_progress_secret, cmp_id)

    # --- 5. Strictly 1 agent per company: delete old agent record if present ---
    ProxyAgent.objects.filter(cmp=company).delete()

    agent = ProxyAgent.objects.create(
        agent_name=agent_name,
        agent_uid=agent_uid,
        cmp=company,
        bootstrap_token=encrypted_token,     # AES-GCM encrypted
        client_cert_pem=encrypted_cert_pem,  # AES-GCM encrypted
        client_cert_not_after=not_after,
        progress_signing_secret_hash=progress_secret_hash,
        progress_signing_secret=encrypted_progress_secret,
        created_by=user,
        config_downloaded_at=dj_timezone.now(),
    )

    logger.info(
        "proxy_agent_service: created agent agent_uid=%s cmp_id=%s created_by=%s",
        agent_uid, cmp_id, getattr(user, "user_id", None),
    )

    # --- 6. Read trusted CA PEM for config.json ---
    cert_path = getattr(settings, "PROXY_AGENT_CA_CERT_PATH", "")
    with open(cert_path, "r") as f:
        trusted_ca_pem = f.read()

    # --- 7. Build config dict ---
    config = build_agent_config_json(
        agent_uid,
        client_cert_pem,
        client_key_pem,
        raw_token,
        trusted_ca_pem,
        progress_signing_secret=raw_progress_secret,
    )

    return agent, config
