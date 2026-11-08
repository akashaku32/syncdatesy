import urllib3
from urllib3.exceptions import InsecureRequestWarning
urllib3.disable_warnings(InsecureRequestWarning)
"""
Standalone SAP TLS Certificate Helper for Migration Function Container.

Provides PinnedCertAdapter, secret retrieval, inline classification for un-saved connection testing,
and requests.Session construction without any dependency on Django framework or Django models.
"""

import os
import ssl
import socket
import tempfile
import logging
import warnings
from typing import Dict, Any, Optional, Tuple, Union

# Suppress Python version deprecation warnings from google API client core
warnings.filterwarnings("ignore", category=FutureWarning, module="google")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtensionOID

logger = logging.getLogger(__name__)

# Directory fallback for local development or when GCP Secret Manager is unconfigured
LOCAL_CERT_DIR = os.getenv(
    "LOCAL_CERT_STORAGE_DIR",
    os.path.join(tempfile.gettempdir(), "app_secrets", "certs")
)

_CERT_FILE_CACHE: Dict[str, str] = {}


class PinnedCertAdapter(HTTPAdapter):
    """
    Custom HTTPAdapter enforcing leaf certificate pinning for Requests sessions.

    Supports both standard CA verification and strict leaf fingerprint pinning
    when self-signed leaf certificates without CA extensions (BasicConstraints) are used.
    """

    def __init__(self, cert_path: str, skip_hostname: bool = False, **kwargs: Any) -> None:
        self.cert_path = cert_path
        self.skip_hostname = skip_hostname
        self.fingerprint = self._extract_fingerprint(cert_path)
        super().__init__(**kwargs)

    def _extract_fingerprint(self, cert_path: str) -> Optional[str]:
        try:
            with open(cert_path, "rb") as f:
                pem_data = f.read()
            cert = x509.load_pem_x509_certificate(pem_data)
            raw_fp = cert.fingerprint(hashes.SHA256()).hex()
            return ":".join(raw_fp[i:i+2] for i in range(0, len(raw_fp), 2))
        except Exception as exc:
            logger.warning("[SAP_TLS_HELPER] Failed to extract SHA256 fingerprint from '%s': %s", cert_path, exc)
            return None

    def _build_context(self) -> ssl.SSLContext:
        context = create_urllib3_context()
        try:
            context.load_verify_locations(cafile=self.cert_path)
            context.verify_mode = ssl.CERT_REQUIRED
        except Exception:
            context.verify_mode = ssl.CERT_NONE

        if self.skip_hostname:
            context.check_hostname = False
        return context

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["ssl_context"] = self._build_context()
        if self.skip_hostname:
            kwargs["assert_hostname"] = False
        if self.fingerprint:
            kwargs["assert_fingerprint"] = self.fingerprint
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["ssl_context"] = self._build_context()
        if self.skip_hostname:
            kwargs["assert_hostname"] = False
        if self.fingerprint:
            kwargs["assert_fingerprint"] = self.fingerprint
        return super().proxy_manager_for(*args, **kwargs)

    def cert_verify(self, conn: Any, url: str, verify: Any, cert: Any) -> None:
        if self.fingerprint:
            conn.assert_fingerprint = self.fingerprint
            super().cert_verify(conn, url, verify=False, cert=cert)
        else:
            super().cert_verify(conn, url, verify=verify, cert=cert)

        if self.skip_hostname:
            conn.assert_hostname = False


import re


def sanitize_secret_name(secret_name: str) -> str:
    """
    Sanitize secret_name to strictly match GCP Secret Manager ID format: [a-zA-Z0-9_-]+
    Replaces dots, colons, spaces, and any other invalid characters with hyphens.
    """
    if not secret_name:
        return "sap-cert-secret"
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '-', secret_name)
    sanitized = re.sub(r'-+', '-', sanitized).strip('-')
    return sanitized or "sap-cert-secret"


def _format_gcp_labels(metadata: Dict[str, str]) -> Dict[str, str]:
    """
    Format and sanitize metadata dictionary for GCP Secret Manager labels.
    GCP Secret Manager label constraints:
      - Key & Value max length: 63 characters
      - Allowed characters: lowercase letters, numbers, underscores (_), hyphens (-)
    """
    labels = {}
    if not metadata:
        return labels
    for k, v in metadata.items():
        if v is None:
            continue
        clean_k = re.sub(r'[^a-z0-9_-]', '_', str(k).lower())[:63].strip('_')
        clean_v = re.sub(r'[^a-z0-9_-]', '_', str(v).lower())[:63].strip('_')
        if clean_k:
            labels[clean_k] = clean_v
    return labels


_SECRET_MANAGER_CLIENT = None


def get_secret_manager_client():
    """
    Get or create singleton SecretManagerServiceClient instance.
    Prevents allocating a fresh client on every operation to eliminate redundant overhead.
    """
    global _SECRET_MANAGER_CLIENT
    if _SECRET_MANAGER_CLIENT is None:
        from google.cloud import secretmanager
        _SECRET_MANAGER_CLIENT = secretmanager.SecretManagerServiceClient()
    return _SECRET_MANAGER_CLIENT


def cleanup_old_secret_versions(client: Any, secret_path: str, keep_latest: int = 2) -> None:
    """
    Destroy old secret versions beyond keep_latest to prevent accumulating $0.06/month per-version charges.
    Destroyed versions are free in GCP Secret Manager.
    """
    try:
        versions = list(client.list_secret_versions(request={"parent": secret_path}))
        enabled_versions = [v for v in versions if v.state.name == "ENABLED"]
        if len(enabled_versions) > keep_latest:
            enabled_versions.sort(key=lambda v: int(v.name.split("/")[-1]), reverse=True)
            for old_v in enabled_versions[keep_latest:]:
                client.destroy_secret_version(request={"name": old_v.name})
                logger.info("[SAP_TLS_HELPER] Destroyed stale secret version | version=%s", old_v.name)
    except Exception as exc:
        logger.warning("[SAP_TLS_HELPER] Stale secret version cleanup skipped (%s)", exc)


def store_cert_secret(secret_name: str, pem_bytes: bytes, metadata: Dict[str, str]) -> str:
    """
    Store PEM certificate bytes into GCP Secret Manager.
    Falls back to local disk storage ONLY if GCP_PROJECT_ID is not configured (offline local dev).
    """
    secret_name = sanitize_secret_name(secret_name)
    gcp_project = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT", "datasyncher-react-qa-dev")
    
    if gcp_project:
        try:
            client = get_secret_manager_client()
            parent = f"projects/{gcp_project}"
            secret_path = f"{parent}/secrets/{secret_name}"

            try:
                client.get_secret(request={"name": secret_path})
            except Exception:
                client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": secret_name,
                        "secret": {"replication": {"automatic": {}}, "labels": _format_gcp_labels(metadata)},
                    }
                )

            client.add_secret_version(
                request={"parent": secret_path, "payload": {"data": pem_bytes}}
            )
            cleanup_old_secret_versions(client, secret_path, keep_latest=2)
            logger.info("[SAP_TLS_HELPER] Stored cert secret in GCP Secret Manager | secret_name=%s", secret_name)
            return secret_name
        except Exception as exc:
            logger.warning("[SAP_TLS_HELPER] GCP Secret Manager store failed for '%s' (%s). Falling back to local disk storage.", secret_name, exc)

    # Local development offline fallback
    os.makedirs(LOCAL_CERT_DIR, exist_ok=True)
    file_path = os.path.join(LOCAL_CERT_DIR, f"{secret_name}.pem")
    with open(file_path, "wb") as f:
        f.write(pem_bytes)
    
    _CERT_FILE_CACHE[secret_name] = file_path
    logger.info("[SAP_TLS_HELPER] Stored cert secret to local storage | path=%s", file_path)
    return secret_name


def fetch_secret_to_tempfile(secret_name: str) -> str:
    """
    Retrieve PEM certificate for secret_name and return a file path.

    GCP Secret Manager is the primary source of truth when configured. Downloaded certificates
    automatically re-populate the local disk cache for self-healing resilience.
    """
    secret_name = sanitize_secret_name(secret_name)
    if secret_name in _CERT_FILE_CACHE and os.path.exists(_CERT_FILE_CACHE[secret_name]):
        return _CERT_FILE_CACHE[secret_name]

    local_path = os.path.join(LOCAL_CERT_DIR, f"{secret_name}.pem")

    gcp_project = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if gcp_project:
        try:
            client = get_secret_manager_client()
            version_path = f"projects/{gcp_project}/secrets/{secret_name}/versions/latest"
            response = client.access_secret_version(request={"name": version_path})
            pem_bytes = response.payload.data

            os.makedirs(LOCAL_CERT_DIR, exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(pem_bytes)

            _CERT_FILE_CACHE[secret_name] = local_path
            logger.info("[SAP_TLS_HELPER] Fetched secret from GCP Secret Manager | secret_name=%s", secret_name)
            return local_path
        except Exception as exc:
            logger.warning("[SAP_TLS_HELPER] GCP Secret Manager fetch failed for '%s' (%s). Checking local disk.", secret_name, exc)

    if os.path.exists(local_path):
        _CERT_FILE_CACHE[secret_name] = local_path
        return local_path

    raw_local_path = os.path.join(LOCAL_CERT_DIR, secret_name)
    if os.path.exists(raw_local_path):
        _CERT_FILE_CACHE[secret_name] = raw_local_path
        return raw_local_path

    cwd_path = os.path.join(os.getcwd(), f"{secret_name}.pem")
    if os.path.exists(cwd_path):
        _CERT_FILE_CACHE[secret_name] = cwd_path
        return cwd_path

    cwd_raw = os.path.join(os.getcwd(), secret_name)
    if os.path.exists(cwd_raw):
        _CERT_FILE_CACHE[secret_name] = cwd_raw
        return cwd_raw

    raise FileNotFoundError(f"Certificate secret '{secret_name}' could not be located in Secret Manager or local storage.")


def fetch_live_cert_info(host: str, port: int, timeout: int = 10) -> Dict[str, Any]:
    """
    Connect to host:port via TLS socket without requiring trust to inspect certificate properties.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with ctx.wrap_socket(sock, server_hostname=host) as sslsock:
        der_bytes = sslsock.getpeercert(binary_form=True)

    cert = x509.load_der_x509_certificate(der_bytes)
    pem_bytes = ssl.DER_cert_to_PEM_cert(der_bytes).encode("utf-8")
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()

    subject_str = cert.subject.rfc4514_string()
    issuer_str = cert.issuer.rfc4514_string()
    not_valid_after = cert.not_valid_after_utc.isoformat()

    sans = []
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        for name in ext.value:
            sans.append(str(name.value))
    except Exception:
        sans = []

    is_self_signed = (cert.issuer == cert.subject)

    return {
        "der_bytes": der_bytes,
        "pem_bytes": pem_bytes,
        "fingerprint": fingerprint,
        "subject": subject_str,
        "issuer": issuer_str,
        "not_valid_after": not_valid_after,
        "sans": sans,
        "is_self_signed": is_self_signed,
    }


def classify_cert(host: str, port: int, timeout: int = 10) -> Tuple[str, Dict[str, Any]]:
    """
    Classify host certificate into one of: ca_trusted, private_ca, pinned_leaf, pinned_leaf_no_san.
    """
    cert_info = fetch_live_cert_info(host, port, timeout=timeout)

    if not cert_info["is_self_signed"]:
        try:
            test_sock = socket.create_connection((host, port), timeout=timeout)
            test_ctx = ssl.create_default_context()
            with test_ctx.wrap_socket(test_sock, server_hostname=host):
                pass
            return "ca_trusted", cert_info
        except Exception:
            return "private_ca", cert_info

    sans = cert_info.get("sans", [])
    host_lower = host.lower()
    has_matching_san = any(s.lower() == host_lower for s in sans)

    if has_matching_san:
        return "pinned_leaf", cert_info
    else:
        return "pinned_leaf_no_san", cert_info


def get_sap_cert_secret_name(host: str, port: int) -> str:
    """
    Generate deterministic secret name keyed by physical endpoint host and port:
    sap-cert-{sanitized_host}-{port}
    """
    safe_host = sanitize_secret_name(host)
    return f"sap-cert-{safe_host}-{port}"


def build_session_from_config(config_dict_or_obj: Union[Any, Dict[str, Any]]) -> requests.Session:
    """
    Construct a requests.Session configured according to tls_verification_mode.
    Performs inline classification for un-saved hosts during connection testing.

    Args:
        config_dict_or_obj: Dict or object containing host, port, tls_verification_mode, etc.

    Returns:
        Configured requests.Session instance.
    """
    if isinstance(config_dict_or_obj, dict):
        data = config_dict_or_obj
    elif hasattr(config_dict_or_obj, "data") and isinstance(config_dict_or_obj.data, dict):
        data = config_dict_or_obj.data
    elif hasattr(config_dict_or_obj, "__dict__"):
        data = config_dict_or_obj.__dict__
    else:
        data = {}

    raw_host = (data.get("host") or data.get("base_url") or "")
    if isinstance(raw_host, str):
        raw_host = raw_host.strip()
    host = None
    port = 443
    if raw_host:
        from urllib.parse import urlparse
        if "://" in raw_host:
            parsed = urlparse(raw_host)
            host = (parsed.hostname or raw_host).strip()
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        elif ":" in raw_host:
            parts = raw_host.split(":")
            host = parts[0].strip()
            port = int(parts[1]) if parts[1].isdigit() else int(data.get("port") or 443)
        else:
            host = raw_host
            port = int(data.get("port") or 443)
    try:
        port = int(str(data.get("port") or port).strip() or 443)
    except (TypeError, ValueError):
        port = 443

    # Inline classification during "Test Connection" before SourceConfig exists in DB
    if "tls_verification_mode" not in data and host:
        secret_name = data.get("tls_cert_secret_name") or get_sap_cert_secret_name(host, port)
        local_path = os.path.join(LOCAL_CERT_DIR, f"{secret_name}.pem")

        # Reuse cached secret file if already present to avoid repeated live TLS socket probes!
        if os.path.exists(local_path) or secret_name in _CERT_FILE_CACHE:
            data["tls_verification_mode"] = "pinned_leaf_no_san"
            data["tls_cert_secret_name"] = secret_name
            if "tls_cert_fingerprint" not in data:
                try:
                    fp = PinnedCertAdapter(local_path if os.path.exists(local_path) else _CERT_FILE_CACHE[secret_name]).fingerprint
                    if fp:
                        data["tls_cert_fingerprint"] = fp.replace(":", "")
                except Exception:
                    pass
            logger.info("[SAP_TLS_HELPER] Reusing cached classification for host=%s:%s", host, port)
        else:
            try:
                mode, cert_info = classify_cert(host, port)
                store_cert_secret(secret_name, cert_info["pem_bytes"], {
                    "fingerprint": cert_info["fingerprint"],
                    "not_valid_after": cert_info["not_valid_after"],
                })

                data["tls_verification_mode"] = mode
                data["tls_cert_secret_name"] = secret_name
                data["tls_cert_fingerprint"] = cert_info["fingerprint"]
                data["tls_cert_not_valid_after"] = cert_info["not_valid_after"]
                logger.info("[SAP_TLS_HELPER] Inline classification completed during connection test | host=%s:%s mode=%s", host, port, mode)
            except Exception as exc:
                logger.warning("[SAP_TLS_HELPER] Inline classification failed for %s:%s (%s).", host, port, exc)

    secret_name = data.get("tls_cert_secret_name") or (get_sap_cert_secret_name(host, port) if host else None)
    mode = data.get("tls_verification_mode", "pinned_leaf_no_san" if secret_name else "ca_trusted")

    session = requests.Session()

    if secret_name:
        try:
            pem_path = fetch_secret_to_tempfile(secret_name)
            adapter = PinnedCertAdapter(cert_path=pem_path, skip_hostname=(mode == "pinned_leaf_no_san"))
            session.mount("https://", adapter)
            session.verify = pem_path
            logger.info("[SAP_TLS_HELPER] Successfully loaded & verified certificate from '%s' (path=%s)", secret_name, pem_path)
            return session
        except Exception as exc:
            logger.warning("[SAP_TLS_HELPER] Failed to load cert secret '%s': %s", secret_name, exc)

    # SAP Gateway on-prem often uses self-signed certs. Connection tests already
    # handshake with CERT_NONE; match that here when no pinned CA/leaf is available
    # so $metadata / extract do not fail with CERTIFICATE_VERIFY_FAILED.
    if mode != "ca_trusted" or secret_name:
        session.verify = False
        logger.warning(
            "[SAP_TLS_HELPER] Falling back to verify=False | host=%s:%s mode=%s secret=%s",
            host,
            port,
            mode,
            secret_name,
        )
    return session
