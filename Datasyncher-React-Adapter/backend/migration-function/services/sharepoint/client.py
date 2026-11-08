import logging
import os
import tempfile
import base64
from typing import Optional, Tuple
from urllib.parse import quote, urlparse

import requests
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CertificateCredential, ClientSecretCredential
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from fastapi import HTTPException

from models.sharepoint_models import SharePointAuthorizationRequest

logger = logging.getLogger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_TIMEOUT_SECONDS = 600


def _extract_config(request: SharePointAuthorizationRequest) -> dict:
    config = request.SharePoint or request.destination_config or request.config or {}
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Invalid SharePoint config; expected object.")
    return config


def _redact_value(key: str, value):
    if str(key).lower() in {
        "client_secret",
        "client_certificate",
        "access_token",
        "client_certificate_content",
        "certificate_password",
    }:
        return "***REDACTED***"
    if isinstance(value, dict):
        return {inner_key: _redact_value(inner_key, inner_value) for inner_key, inner_value in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    return value


def _redact_config(config: dict) -> dict:
    return {key: _redact_value(key, value) for key, value in config.items()}


def _require_non_empty(config: dict, field_name: str) -> str:
    value = str(config.get(field_name) or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail=f"Missing required field: {field_name}")
    return value


def _validate_sharepoint_site_url(site_url: str) -> None:
    parsed = urlparse(site_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=422,
            detail="Invalid sharepoint_site_url; expected a full http(s) URL",
        )


def _validate_root_folder(root_folder: Optional[str]) -> None:
    """
    Validate root_folder for unsafe values.
    Rejects backslashes, dot-only segments, and any path that contains
    traversal sequences (.. between slashes).
    """
    normalized = str(root_folder or "").strip()
    if not normalized:
        return

    if "\\" in normalized:
        raise HTTPException(
            status_code=422,
            detail="Invalid root_folder; use SharePoint-style '/' path separators",
        )

    segments = [s for s in normalized.split("/") if s]
    for segment in segments:
        if segment in {".", ".."}:
            raise HTTPException(
                status_code=422,
                detail="Invalid root_folder; relative traversal paths ('.' or '..') are not allowed",
            )

    # Reject encoded traversal sequences that slip past the split check
    if "%2e%2e" in normalized.lower() or "%2f" in normalized.lower():
        raise HTTPException(
            status_code=422,
            detail="Invalid root_folder; URL-encoded path traversal sequences are not allowed",
        )

def validate_sharepoint_authorization(request: SharePointAuthorizationRequest) -> None:
    """
    Validate SharePoint destination config fields before attempting any remote calls.
    """
    config = _extract_config(request)

    auth_type = str(config.get("auth_type") or "").strip().lower()
    if auth_type not in ("client_secret", "client_certificate"):
        raise HTTPException(
            status_code=422,
            detail="Invalid auth_type; expected client_secret or client_certificate",
        )

    _require_non_empty(config, "tenant_id")
    _require_non_empty(config, "client_id")

    if auth_type == "client_secret":
        _require_non_empty(config, "client_secret")
    else:
        if not any(
            str(config.get(field_name) or "").strip()
            for field_name in (
                "client_certificate_content",
                "certificate_filename",
                "client_certificate",
            )
        ):
            raise HTTPException(
                status_code=422,
                detail="Missing required field: client_certificate_file",
            )

    sharepoint_site_url = _require_non_empty(config, "sharepoint_site_url")
    _validate_sharepoint_site_url(sharepoint_site_url)

    _require_non_empty(config, "document_library")
    _validate_root_folder(config.get("root_folder"))


def _parse_site_url(site_url: str) -> Tuple[str, str]:
    parsed = urlparse(site_url)
    hostname = parsed.netloc.strip()
    relative_path = parsed.path.strip("/")
    return hostname, relative_path


def _normalize_root_folder(root_folder: Optional[str]) -> str:
    value = str(root_folder or "").strip().strip("/")
    return value


def _encode_graph_path(path_value: str) -> str:
    return "/".join(quote(segment, safe="") for segment in path_value.split("/") if segment)


def _decode_base64_field(field_name: str, encoded_value: str) -> bytes:
    try:
        return base64.b64decode(encoded_value)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field_name}; file content could not be decoded.",
        ) from exc


def _extract_uploaded_certificate_material(config: dict) -> Tuple[Optional[bytes], str]:
    certificate_content = str(config.get("client_certificate_content") or "").strip()
    certificate_format = str(config.get("certificate_format") or "").strip().lower()

    certificate_bytes = _decode_base64_field("client_certificate_file", certificate_content) if certificate_content else None

    if not certificate_bytes:
        legacy_certificate = str(config.get("client_certificate") or "")
        if legacy_certificate.strip():
            certificate_bytes = legacy_certificate.encode("utf-8")
            certificate_format = certificate_format or "pem"

    return certificate_bytes, certificate_format


def _serialize_private_key_to_pem(private_key) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _serialize_certificate_to_pem(certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.PEM)


def _build_pem_bundle_from_uploaded_material(config: dict) -> str:
    """
    Return a PEM bundle (private key + certificate chain) from uploaded material.

    Supports:
    - PFX / P12 (binary, optionally password-protected)
    - PEM files containing both a private key block and a certificate block
    """
    certificate_bytes, certificate_format = _extract_uploaded_certificate_material(config)
    certificate_password = str(config.get("certificate_password") or "")
    password_bytes = certificate_password.encode("utf-8") if certificate_password else None

    if not certificate_bytes:
        raise HTTPException(
            status_code=422,
            detail="Missing required field: client_certificate_file",
        )

    # ── PFX / P12 ─────────────────────────────────────────────────────────
    if certificate_format in {"pfx", "p12"}:
        try:
            private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
                certificate_bytes,
                password_bytes,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Invalid client_certificate_file; unable to read the PFX/P12 file. "
                    "Verify the file is not corrupted and the password is correct."
                ),
            ) from exc

        if private_key is None or certificate is None:
            raise HTTPException(
                status_code=422,
                detail="Invalid client_certificate_file; the PFX/P12 file must contain both a certificate and a private key.",
            )

        # Verify key ↔ certificate match by comparing public keys
        try:
            cert_pub  = certificate.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            key_pub   = private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            if cert_pub != key_pub:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid client_certificate_file; the private key does not match the certificate.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("PFX key/certificate match check failed: %s", exc)

        bundle_parts = [
            _serialize_private_key_to_pem(private_key),
            _serialize_certificate_to_pem(certificate),
        ]
        for extra_cert in (additional_certificates or []):
            bundle_parts.append(_serialize_certificate_to_pem(extra_cert))
        return b"".join(bundle_parts).decode("utf-8")

    # ── PEM ───────────────────────────────────────────────────────────────
    if certificate_format not in {"pem", ""}:
        raise HTTPException(
            status_code=422,
            detail="Unsupported client certificate format. Allowed formats are .pfx, .p12, and .pem.",
        )

    cert_text = certificate_bytes.decode("utf-8", errors="ignore")
    has_private_key = any(
        marker in cert_text
        for marker in ("BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN EC PRIVATE KEY", "BEGIN ENCRYPTED PRIVATE KEY")
    )
    has_certificate = "BEGIN CERTIFICATE" in cert_text

    if not has_private_key or not has_certificate:
        raise HTTPException(
            status_code=422,
            detail="The PEM file must contain both the private key block and the certificate block.",
        )

    pem_bytes = cert_text.encode("utf-8")

    # Validate private key — respect the password if provided
    try:
        private_key = serialization.load_pem_private_key(
            pem_bytes,
            password=password_bytes,
            backend=default_backend(),
        )
    except TypeError:
        raise HTTPException(
            status_code=422,
            detail="The PEM private key is encrypted; please supply the certificate_password.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid SharePoint private key; the key block could not be parsed.",
        ) from exc

    # Validate certificate block
    try:
        cert_blocks = cert_text.split("-----END CERTIFICATE-----")
        cert_candidates = [
            (block + "-----END CERTIFICATE-----").encode("utf-8")
            for block in cert_blocks
            if "-----BEGIN CERTIFICATE-----" in block
        ]
        if not cert_candidates:
            raise ValueError("No certificate block found")
        parsed_cert = x509.load_pem_x509_certificate(cert_candidates[0], default_backend())
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid SharePoint certificate; the certificate block could not be parsed.",
        ) from exc

    # Verify key ↔ certificate match
    try:
        cert_pub = parsed_cert.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_pub = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if cert_pub != key_pub:
            raise HTTPException(
                status_code=422,
                detail="Invalid client_certificate_file; the private key does not match the certificate.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("PEM key/certificate match check failed: %s", exc)

    return cert_text

import contextlib

@contextlib.contextmanager
def _temp_pem_file(pem_text: str):
    """
    Write *pem_text* to a NamedTemporaryFile and yield the path.
    The file is always removed on exit, even if the caller raises.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".pem", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(pem_text)
        tmp.flush()
        tmp.close()
        yield tmp.name
    finally:
        _cleanup_temp_certificate_file(tmp.name)


def _build_credential(config: dict):
    """
    Build an Azure credential from config.

    Returns:
        (credential, temp_cert_path | None)

    The caller is responsible for cleaning up temp_cert_path when it is not None.
    For certificate auth, prefer using _temp_pem_file() as a context manager
    instead of calling this function directly.
    """
    auth_type = str(config.get("auth_type") or "").strip().lower()
    tenant_id = str(config.get("tenant_id") or "").strip()
    client_id = str(config.get("client_id") or "").strip()

    if auth_type == "client_secret":
        client_secret = str(config.get("client_secret") or "").strip()
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        ), None

    certificate_pem = _build_pem_bundle_from_uploaded_material(config)
    # Use NamedTemporaryFile manually so we can return the path.
    # _acquire_graph_token (and the context manager above) handle cleanup.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".pem", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(certificate_pem)
        tmp.flush()
    finally:
        tmp.close()   # close before passing path to CertificateCredential

    credential = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate_path=tmp.name,
    )
    return credential, tmp.name
def _cleanup_temp_certificate_file(temp_path: Optional[str]) -> None:
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            logger.warning("Failed to remove temporary SharePoint certificate file | path=%s", temp_path)


def _map_credential_error(error: Exception) -> HTTPException:
    message = str(error or "").strip()
    lowered = message.lower()

    if "tenant" in lowered and ("not found" in lowered or "invalid" in lowered):
        return HTTPException(status_code=422, detail="SharePoint tenant_id is invalid or not accessible.")
    if "application" in lowered and ("not found" in lowered or "client_id" in lowered):
        return HTTPException(status_code=422, detail="SharePoint client_id is invalid for the specified tenant.")
    if "secret" in lowered and ("invalid" in lowered or "expired" in lowered):
        return HTTPException(status_code=422, detail="SharePoint client_secret is invalid or expired.")
    if "certificate" in lowered and ("invalid" in lowered or "thumbprint" in lowered or "private key" in lowered):
        return HTTPException(status_code=422, detail="SharePoint client_certificate is invalid or does not match the app registration.")
    if "consent" in lowered or "permission" in lowered or "unauthorized_client" in lowered:
        return HTTPException(
            status_code=422,
            detail="SharePoint app permissions are missing or admin consent has not been granted.",
        )
    return HTTPException(
        status_code=422,
        detail="Failed to authenticate to Microsoft Entra. Please verify tenant_id, client_id, and credentials.",
    )


def _acquire_graph_token(config: dict) -> str:
    """
    Acquire a Microsoft Graph access token from the configured credential.

    Both client-secret and certificate auth types are supported.
    Temp certificate files are always removed, even when token acquisition fails.
    """
    logger.info(
        "_acquire_graph_token | auth_type=%s tenant_id=%s client_id=%s",
        config.get("auth_type"),
        config.get("tenant_id"),
        config.get("client_id"),
    )
    temp_cert_path: Optional[str] = None
    try:
        credential, temp_cert_path = _build_credential(config)
        token = credential.get_token(GRAPH_SCOPE)
        logger.info("_acquire_graph_token succeeded | auth_type=%s", config.get("auth_type"))
        return token.token
    except ClientAuthenticationError as exc:
        logger.warning("_acquire_graph_token: authentication failed | detail=%s", str(exc))
        raise _map_credential_error(exc)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("_acquire_graph_token: unexpected failure")
        raise _map_credential_error(exc)
    finally:
        _cleanup_temp_certificate_file(temp_cert_path)

def _extract_graph_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    return payload.get("message") or response.text[:300] or f"HTTP {response.status_code}"

def _graph_request(
    method: str,
    url: str,
    access_token: str,
    *,
    json_body: Optional[dict] = None,
    data: Optional[bytes] = None,
    extra_headers: Optional[dict] = None,
    suppress_not_found_log: bool = False,
) -> dict:
    """
    Execute a Microsoft Graph HTTP request and return the parsed JSON body.

    Args:
        method: HTTP verb (GET, POST, PUT, DELETE, PATCH).
        url: Fully-qualified Graph endpoint URL.
        access_token: Bearer token for the Authorization header.
        json_body: Optional JSON payload (sets Content-Type automatically).
        data: Optional raw request body bytes (e.g. file content).
        extra_headers: Optional dict merged into the default headers.
        suppress_not_found_log: When True, 404 responses are logged at DEBUG
            rather than WARNING (useful for existence probes).

    Returns:
        Parsed JSON dict, or {} for no-content (204) responses.

    Raises:
        HTTPException: On network failure or HTTP 4xx/5xx responses.
    """
    headers: dict = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    logger.info("_graph_request | method=%s url=%s", method, url)
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            data=data,
            timeout=GRAPH_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("_graph_request network failure | url=%s error=%s", url, exc)
        raise HTTPException(
            status_code=422,
            detail="Failed to reach Microsoft Graph. Please verify network access and try again.",
        ) from exc

    if response.status_code >= 400:
        detail = _extract_graph_error(response)
        if response.status_code == 404 and suppress_not_found_log:
            logger.info("_graph_request 404 (expected probe) | url=%s detail=%s", url, detail)
        else:
            logger.warning(
                "_graph_request error | status=%s url=%s detail=%s",
                response.status_code, url, detail,
            )
        raise HTTPException(status_code=response.status_code, detail=detail)

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError as exc:
        logger.warning("_graph_request: non-JSON response | url=%s", url)
        raise HTTPException(
            status_code=422,
            detail="Microsoft Graph returned an invalid response.",
        ) from exc

def _resolve_sharepoint_site(access_token: str, sharepoint_site_url: str) -> dict:
    hostname, relative_path = _parse_site_url(sharepoint_site_url)
    if relative_path:
        encoded_path = _encode_graph_path(relative_path)
        url = f"{GRAPH_BASE_URL}/sites/{hostname}:/{encoded_path}"
    else:
        url = f"{GRAPH_BASE_URL}/sites/root"

    try:
        return _graph_request("GET", url, access_token)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=422,
                detail=f"SharePoint site not found or not accessible: {sharepoint_site_url}",
            ) from exc
        if exc.status_code in (401, 403):
            raise HTTPException(
                status_code=422,
                detail="The SharePoint app does not have permission to access the requested site.",
            ) from exc
        raise


def _resolve_document_library(access_token: str, site_id: str, document_library: str) -> dict:
    """
    Find a document library drive by name within a SharePoint site.

    Raises HTTPException(422) when the library is not found or the app lacks
    permission to list drives.  Logs a warning when multiple libraries share
    the same name (case-insensitive) so operators can investigate.
    """
    url = f"{GRAPH_BASE_URL}/sites/{quote(site_id, safe='')}/drives"
    try:
        payload = _graph_request("GET", url, access_token)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            raise HTTPException(
                status_code=422,
                detail="The SharePoint app does not have permission to list document libraries for the site.",
            ) from exc
        raise

    drives: list = payload.get("value") or []
    target = document_library.strip().lower()
    matches = [d for d in drives if str(d.get("name") or "").strip().lower() == target]

    if not matches:
        available = [str(d.get("name") or "") for d in drives]
        logger.warning(
            "_resolve_document_library: library not found | requested=%s available=%s",
            document_library, available,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"SharePoint document library not found: {document_library!r}. "
                "Please verify the library name or update the connection. "
                f"Available libraries: {', '.join(available) or 'none'}."
            ),
        )

    if len(matches) > 1:
        ids = [str(d.get("id") or "") for d in matches]
        logger.warning(
            "_resolve_document_library: ambiguous library name | name=%s matching_ids=%s — using first",
            document_library, ids,
        )

    return matches[0]

def _validate_root_folder_access(access_token: str, drive_id: str, root_folder: str) -> Optional[dict]:
    normalized_root = _normalize_root_folder(root_folder)
    if not normalized_root:
        url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root"
        return _graph_request("GET", url, access_token)

    encoded_path = _encode_graph_path(normalized_root)
    url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root:/{encoded_path}"

    try:
        payload = _graph_request("GET", url, access_token)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=422,
                detail=f"Root folder not found in document library: {root_folder}",
            ) from exc
        if exc.status_code in (401, 403):
            raise HTTPException(
                status_code=422,
                detail=f"Root folder is not accessible in document library: {root_folder}",
            ) from exc
        raise

    if normalized_root and "folder" not in payload:
        raise HTTPException(
            status_code=422,
            detail=f"Root folder path is not a folder in the document library: {root_folder}",
        )
    return payload


def test_sharepoint_connection(request: SharePointAuthorizationRequest) -> dict:
    """
    Perform a live validation against Microsoft Entra and Microsoft Graph.

    This validates:
    - app authentication (client secret or certificate)
    - SharePoint site accessibility
    - document library existence
    - optional root folder accessibility
    """
    validate_sharepoint_authorization(request)
    config = _extract_config(request)
    logger.info("SharePoint auth: starting destination validation | config=%s", _redact_config(config))

    access_token = _acquire_graph_token(config)
    site = _resolve_sharepoint_site(access_token, str(config.get("sharepoint_site_url") or ""))
    drive = _resolve_document_library(access_token, str(site.get("id") or ""), str(config.get("document_library") or ""))
    folder = _validate_root_folder_access(access_token, str(drive.get("id") or ""), str(config.get("root_folder") or ""))

    logger.info(
        "SharePoint auth: validation succeeded | site_id=%s drive_id=%s folder_id=%s",
        site.get("id"),
        drive.get("id"),
        (folder or {}).get("id"),
    )

    return {
        "site_id": site.get("id"),
        "site_name": site.get("displayName") or site.get("name"),
        "site_url": site.get("webUrl"),
        "drive_id": drive.get("id"),
        "drive_name": drive.get("name"),
        "root_folder": str(config.get("root_folder") or "").strip() or "/",
        "root_folder_id": (folder or {}).get("id"),
    }


test_sharepoint_connection.__test__ = False
