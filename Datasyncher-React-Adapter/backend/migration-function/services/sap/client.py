# -*- coding: latin-1 -*-
"""
sap_odata_auth.py
-----------------------------------------------------------------------------
Production-grade SAP OData connection validator.

Validation pipeline (fail-fast, ordered):
    Step 0  Field Validation       -  no network, pure input checks
    Step 1  DNS Resolution         -  hostname -> IP
    Step 2  TCP Port Reachability  -  raw socket connect
    Step 3  SSL/TLS Handshake     -  self-signed cert supported
    Step 4  Credential Check      -  IWFND CATALOGSERVICE;v=2/ServiceCollection?$top=1 (Basic Auth probe)

Error identifiers
-----------------
Every raised HTTPException carries a machine-readable `error_code` in its
detail dict so callers can programmatically branch on the failure reason
without parsing human-readable messages.

    SAP_ODATA_CFG_001   Invalid config structure
    SAP_ODATA_CFG_002   Invalid port value
    SAP_ODATA_VAL_001   Missing host
    SAP_ODATA_VAL_002   IP address supplied instead of hostname
    SAP_ODATA_VAL_003   IPv6 address supplied instead of hostname
    SAP_ODATA_VAL_004   Port out of range
    SAP_ODATA_VAL_005   Missing username
    SAP_ODATA_VAL_006   Missing password
    SAP_ODATA_VAL_007   Invalid connection_method
    SAP_ODATA_DNS_001   Hostname not resolvable
    SAP_ODATA_TCP_001   TCP connection timed out
    SAP_ODATA_TCP_002   TCP port unreachable
    SAP_ODATA_SSL_001   SSL handshake failed
    SAP_ODATA_SSL_002   SSL unexpected error
    SAP_ODATA_AUTH_401  Invalid credentials
    SAP_ODATA_AUTH_403  Insufficient SAP permissions
    SAP_ODATA_AUTH_404  Catalog Service not found (wrong path / not activated)
    SAP_ODATA_AUTH_XXX  Unexpected HTTP status from gateway
    SAP_ODATA_AUTH_SSL  SSL error during credential check
    SAP_ODATA_AUTH_TMO  Credential check timed out
    SAP_ODATA_AUTH_ERR  Unexpected error during credential check

Environment variables
---------------------
    SAP_CONNECTION_TIMEOUT   TCP/SSL/HTTP timeout in seconds  (default: 10)
    SAP_MAX_RETRIES          Retry attempts for Steps 1G--3     (default: 2)
    SAP_RETRY_BACKOFF        Back-off multiplier in seconds   (default: 1.0)
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import ssl
import time
from typing import Any, Dict, List, Optional

import requests
import urllib3
from fastapi import HTTPException

from models.sap_odata_models import SAPODataAuthorizationRequest
from services.sap.source import (
    parse_proxy_metadata_entities,
    parse_proxy_source_detail_entities,
)
from utils.timing_utils import (
    log_execution_time,
    print_timing_summary_table,
    reset_timing_records,
)

# -- Silence self-signed cert noise from urllib3 -------------------------------
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -- Module-level logger -------------------------------------------------------
logger = logging.getLogger(__name__)

# -- Safe config and connection helpers ----------------------------------------

def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the dictionary with sensitive fields redacted."""
    sensitive_keys = {"password", "passwd", "pwd", "secret", "token", "key"}
    return {
        k: ("***" if k.lower() in sensitive_keys else v)
        for k, v in d.items()
    }


def _is_ip_address(host: str) -> tuple[bool, bool]:
    """Returns (is_ipv4, is_ipv6) using the authoritative ipaddress module."""
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
        return isinstance(addr, ipaddress.IPv4Address), isinstance(addr, ipaddress.IPv6Address)
    except ValueError:
        return False, False


# -- Explicit blocklist of technical/system services -------------------------
_TECHNICAL_SERVICES = frozenset({"Z_I_DS_SRVC_LIST_SRV", "CATALOGSERVICE"})

# -- Runtime configuration (injectable via env) --------------------------------
_TIMEOUT: int       = int(os.getenv("SAP_CONNECTION_TIMEOUT", "10"))
_MAX_RETRIES: int   = int(os.getenv("SAP_MAX_RETRIES",        "2"))
_RETRY_BACKOFF: float = float(os.getenv("SAP_RETRY_BACKOFF",  "1.0"))

# -- SAP Gateway credential-probe endpoint -------------------------------------
#
# WHY THIS SPECIFIC PATH:
#   /sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection?$top=1&$format=json
#
#   - CATALOGSERVICE is a SAP system service (not user data). It exists on every
#     properly configured SAP Gateway (ECC, S/4HANA on-premise).
#   - Requires Basic Auth -> a 200 proves credentials AND gateway activation.
#   - $top=1 fetches exactly one record -> minimal payload, no large download risk.
#   - If this 404s, the SAP BASIS team has not activated the Gateway correctly.
#     That is their configuration problem, not ours. The error message tells them
#     exactly what transaction to check: /IWFND/MAINT_SERVICE.
#
# WHY NOT A FALLBACK TO S/4HANA CLOUD PATHS:
#   S/4HANA Cloud uses OAuth2, not Basic Auth. Probing it with a username/password
#   makes no architectural sense and would confuse the error signal. Cloud support
#   requires a completely separate authentication flow and connector design.
#
# This matches how Fivetran, AWS AppFlow, and AWS Glue handle SAP OData auth:
# they declare the catalog service a prerequisite and document it  -  they do not
# attempt runtime path discovery.
#
_CATALOG_PROBE = (
    "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection"
    "?$top=1&$format=json"
)
_CUSTOM_LISTING_PATH = (
    "/sap/opu/odata/sap/Z_I_DS_SRVC_LIST_SRV/FactsOfZ_I_DS_SRVC_LIST"
    "?$format=json"
)
_CATALOG_SERVICE_COLLECTION = (
    "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection"
    "?$format=json"
)


# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

def _raise(error_code: str, message: str, status: int = 422) -> None:
    """
    Raise a FastAPI HTTPException with a structured detail payload.

    Detail shape:
        {
            "error_code": "SAP_ODATA_VAL_001",
            "message":    "Missing required field: host"
        }
    """
    logger.error("[%s] %s", error_code, message)
    raise HTTPException(
        status_code=status,
        detail={"error_code": error_code, "message": message},
    )


def _extract_config(request: SAPODataAuthorizationRequest) -> dict[str, Any]:
    """Return the raw config dict; raise structured error on bad type."""
    config = request.SAPOData or {}
    if not isinstance(config, dict):
        _raise("SAP_ODATA_CFG_001", "Invalid SAP OData config; expected a JSON object.")
    return config


def _parse_connection_fields(config: dict[str, Any]) -> tuple[str, int, str, str, str]:
    """
    Extract and normalise the five core fields.

    Returns:
        (host, port_int, username, password, connection_method)
    """
    logger.debug("Config keys: %s", list(config.keys()))  # never log values

    host              = str(config.get("host")              or "").strip()
    port_raw          = config.get("port")
    username          = str(config.get("username")          or "").strip()
    password          = str(config.get("password")          or "").strip()
    connection_method = str(config.get("connection_method") or "direct").strip().lower()

    try:
        port_int = int(port_raw) if port_raw is not None and str(port_raw).strip() != "" else 443
    except (TypeError, ValueError):
        _raise("SAP_ODATA_CFG_002", f"Invalid port value '{port_raw}'; must be an integer.")

    return host, port_int, username, password, connection_method


def _with_retry(fn, *args, step_label: str, **kwargs) -> None:
    """
    Call *fn* with simple linear-backoff retries.
    Only retries on non-HTTP exceptions (e.g. transient network blips).
    HTTPException propagates immediately without retry.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 2):          # +2 -> 1 initial + N retries
        try:
            fn(*args, **kwargs)
            return
        except HTTPException:
            raise                                        # structured error -> no retry
        except Exception as exc:
            last_exc = exc
            if attempt <= _MAX_RETRIES:
                wait = _RETRY_BACKOFF * attempt
                logger.warning(
                    "[%s] attempt %d/%d failed (%s)  -  retrying in %.1fs",
                    step_label, attempt, _MAX_RETRIES + 1, exc, wait,
                )
                time.sleep(wait)

    # All attempts exhausted  -  re-raise the last raw exception so the
    # calling step can wrap it into its own structured HTTPException.
    raise last_exc  # type: ignore[misc]


# -----------------------------------------------------------------------------
# STEP 0  -  FIELD VALIDATION  (no network calls)
# -----------------------------------------------------------------------------

@log_execution_time
def validate_sap_odata_authorization(request: SAPODataAuthorizationRequest) -> None:
    """
    Validate all required form fields before any network activity.

    Rules:
        host             required; DNS hostname only (no IPv4 / IPv6)
        port             required; integer 1G--65535
        username         required
        password         required
        connection_method  "direct" | "proxy"
        proxy_agent      required when connection_method == "proxy" (non-empty agent UUID)
    """
    config = _extract_config(request)
    host, port_int, username, password, connection_method = _parse_connection_fields(config)

    logger.debug("[SAP_ODATA_VALIDATE] Starting field validation for host='%s'", host or "<empty>")

    # -- Host ------------------------------------------------------------------
    if not host:
        _raise("SAP_ODATA_VAL_001", "Missing required field: host.")

    is_ipv4, is_ipv6 = _is_ip_address(host)
    if is_ipv4:
        _raise(
            "SAP_ODATA_VAL_002",
            f"Host '{host}' is an IPv4 address. "
            "Provide a DNS hostname  -  SSL certificates require it.",
        )

    if is_ipv6:
        _raise(
            "SAP_ODATA_VAL_003",
            f"Host '{host}' appears to be an IPv6 address. "
            "Provide a DNS hostname  -  SSL certificates require it.",
        )

    # -- Port ------------------------------------------------------------------
    if not (1 <= port_int <= 65535):
        _raise(
            "SAP_ODATA_VAL_004",
            f"Port {port_int} is out of range. Must be between 1 and 65535.",
        )

    # -- Credentials -----------------------------------------------------------
    if not username:
        _raise("SAP_ODATA_VAL_005", "Missing required field: username.")

    if not password:
        _raise("SAP_ODATA_VAL_006", "Missing required field: password.")

    # -- Connection method -----------------------------------------------------
    if connection_method not in ("direct", "proxy"):
        _raise(
            "SAP_ODATA_VAL_007",
            f"Invalid connection_method '{connection_method}'. Expected 'direct' or 'proxy'.",
        )

    if connection_method == "proxy":
        proxy_agent = str(config.get("proxy_agent") or "").strip()
        if not proxy_agent:
            _raise(
                "SAP_ODATA_VAL_008",
                "connection_method is 'proxy' but proxy_agent is missing. "
                "Generate a proxy agent config from the UI and ensure the agent UUID is present, "
                "then ensure the on-prem proxy agent is running before testing the connection.",
            )

    logger.info(
        "[SAP_ODATA_VALIDATE] Field validation passed  -  host='%s' port=%d method='%s'",
        host, port_int, connection_method,
    )


# -----------------------------------------------------------------------------
# STEP 1  -  DNS RESOLUTION
# -----------------------------------------------------------------------------

@log_execution_time
def _check_dns(host: str) -> None:
    """
    Resolve *host* to an IP address via the system DNS resolver.

    Raises:
        SAP_ODATA_DNS_001  hostname not found in DNS
    """
    logger.debug("[SAP_ODATA_DNS] Resolving hostname '%s'", host)
    try:
        resolved_ip = socket.gethostbyname(host)
        logger.info("[SAP_ODATA_DNS] Step 1 passed  -  '%s' -> %s", host, resolved_ip)
    except socket.gaierror as exc:
        _raise(
            "SAP_ODATA_DNS_001",
            f"Cannot resolve hostname '{host}'. "
            f"Verify the host address is correct and reachable from this network. "
            f"DNS error: {exc}",
        )


# -----------------------------------------------------------------------------
# STEP 2  -  TCP PORT REACHABILITY
# -----------------------------------------------------------------------------

@log_execution_time
def _check_tcp(host: str, port_int: int, timeout: int) -> None:
    """
    Open a raw TCP socket to verify the port is reachable.

    Raises:
        SAP_ODATA_TCP_001  connection timed out
        SAP_ODATA_TCP_002  port unreachable (server down / firewall / wrong port)
    """
    logger.debug("[SAP_ODATA_TCP] Connecting to %s:%d (timeout=%ds)", host, port_int, timeout)
    try:
        with socket.create_connection((host, port_int), timeout=timeout) as sock:
            logger.info("[SAP_ODATA_TCP] Step 2 passed  -  TCP connection to %s:%d succeeded", host, port_int)
    except socket.timeout:
        _raise(
            "SAP_ODATA_TCP_001",
            f"TCP connection to {host}:{port_int} timed out after {timeout}s. "
            "Check host, port, and firewall/network rules.",
        )
    except OSError as exc:
        _raise(
            "SAP_ODATA_TCP_002",
            f"Port {port_int} is unreachable on {host}. "
            f"Ensure the SAP server is running and the port is open. "
            f"OS error: {exc}",
        )


# -----------------------------------------------------------------------------
# STEP 3  -  SSL / TLS HANDSHAKE
# -----------------------------------------------------------------------------

@log_execution_time
def _check_ssl(host: str, port_int: int, timeout: int) -> None:
    """
    Perform an SSL/TLS handshake to confirm HTTPS is functioning.

    Self-signed certificates are explicitly supported:
        check_hostname = False
        verify_mode    = ssl.CERT_NONE
    This mirrors the behaviour of Fivetran and Airbyte SAP connectors for
    on-premise deployments that use SAP's default self-signed certs.

    Raises:
        SAP_ODATA_SSL_001  SSL protocol / certificate error
        SAP_ODATA_SSL_002  unexpected non-SSL error during handshake
    """
    logger.debug("[SAP_ODATA_SSL] Starting TLS handshake with %s:%d", host, port_int)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    raw_sock: socket.socket | None = None
    try:
        raw_sock = socket.create_connection((host, port_int), timeout=timeout)
        with ssl_ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            cipher = tls_sock.cipher()
            proto  = tls_sock.version()
            logger.info(
                "[SAP_ODATA_SSL] Step 3 passed  -  %s:%d | protocol=%s cipher=%s",
                host, port_int, proto, cipher,
            )
    except ssl.SSLError as exc:
        _raise(
            "SAP_ODATA_SSL_001",
            f"SSL handshake failed for {host}:{port_int}. "
            f"Verify the TLS configuration on the SAP server. "
            f"SSL error: {exc}",
        )
    except Exception as exc:
        _raise(
            "SAP_ODATA_SSL_002",
            f"Unexpected error during SSL handshake with {host}:{port_int}. "
            f"Error: {exc}",
        )
    finally:
        # Guard: if wrap_socket raised before entering its own context manager,
        # the raw socket would leak  -  close it explicitly here.
        if raw_sock and raw_sock.fileno() != -1:
            try:
                raw_sock.close()
            except OSError:
                pass


# -----------------------------------------------------------------------------
# STEP 4  -  CREDENTIAL VALIDATION
# -----------------------------------------------------------------------------

@log_execution_time
def _check_credentials(
    host: str,
    port_int: int,
    username: str,
    password: str,
    timeout: int,
    tls_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"https://{host}:{port_int}{_CATALOG_PROBE}"
    headers = {"Accept": "application/json", "User-Agent": "SAPODataConnector/1.0"}

    logger.debug("[SAP_ODATA_AUTH] Probing %s as user '%s'", url, username)

    config = tls_config if tls_config is not None else {"host": host, "port": port_int}

    try:
        try:
            from utils.sap_tls_helper import build_session_from_config
            session = build_session_from_config(config)
        except Exception:
            session = requests.Session()
            session.verify = False

        with session.get(
            url,
            auth=(username, password),
            headers=headers,
            timeout=timeout,
            stream=True,              # Don't download large response bodies
            allow_redirects=False,    # Detect silent login-page redirects explicitly
        ) as resp:
            status = resp.status_code
            logger.info("[SAP_ODATA_AUTH] %s -> HTTP %d", url, status)

            if status == 200:
                logger.info(
                    "[SAP_ODATA_AUTH] Step 4 passed  -  "
                    "credentials valid for user '%s' on %s:%d",
                    username, host, port_int,
                )
                return

            elif status in (401, 302):
                # 302 = gateway redirecting to a login page because Basic Auth failed silently.
                # Both signals mean the same thing: wrong credentials.
                _raise(
                    "SAP_ODATA_AUTH_401",
                    f"Invalid username or password for SAP user '{username}'. "
                    "Please verify your credentials and try again. "
                    f"(Gateway responded with HTTP {status})",
                )

            elif status == 403:
                _raise(
                    "SAP_ODATA_AUTH_403",
                    f"SAP user '{username}' authenticated successfully but lacks "
                    "the authorizations required to access the SAP Gateway. "
                    "Ask your SAP BASIS administrator to assign the roles described in "
                    "SAP Note 2855052 (Authorizations required for ODP Data Replication API 2.0).",
                )

            elif status == 404:
                _raise(
                    "SAP_ODATA_AUTH_404",
                    f"SAP Gateway Catalog Service (CATALOGSERVICE) is not activated on "
                    f"{host}:{port_int}. "
                    "This is a SAP BASIS configuration issue, not a credential problem. "
                    "Ask your SAP administrator to open transaction /IWFND/MAINT_SERVICE "
                    "and verify the CATALOGSERVICE is listed and active.",
                )

            else:
                _raise(
                    "SAP_ODATA_AUTH_XXX",
                    f"SAP Gateway returned an unexpected HTTP {status} response from {url}. "
                    "Check the SAP Gateway server logs (transaction /IWFND/ERROR_LOG) "
                    "for more detail.",
                )

    except requests.exceptions.SSLError as exc:
        logger.error("[SAP_ODATA_AUTH] SSL error on %s: %s", url, exc)
        _raise(
            "SAP_ODATA_AUTH_SSL",
            f"SSL certificate verification failed for SAP Gateway at {host}:{port_int}. "
            f"SSL detail: {exc}",
        )

    except requests.exceptions.Timeout:
        logger.error("[SAP_ODATA_AUTH] Request timed out: %s (timeout=%ds)", url, timeout)
        _raise(
            "SAP_ODATA_AUTH_TMO",
            f"SAP Gateway at {host}:{port_int} did not respond within {timeout}s. "
            "Check server health, ICM workload, and network latency.",
        )

    except HTTPException:
        raise    # Already structured  -  propagate as-is

    except Exception as exc:
        logger.exception("[SAP_ODATA_AUTH] Unexpected error probing %s: %s", url, exc)
        _raise(
            "SAP_ODATA_AUTH_ERR",
            f"Unexpected error while validating SAP credentials against {host}:{port_int}. "
            f"Detail: {exc}",
        )


# -----------------------------------------------------------------------------
# MAIN ORCHESTRATOR
# -----------------------------------------------------------------------------

@log_execution_time
def test_sap_odata_connection(request: SAPODataAuthorizationRequest) -> dict[str, str]:
    reset_timing_records()
    config = _extract_config(request)
    host, port_int, username, password, connection_method = _parse_connection_fields(config)

    logger.info(
        "[SAP_ODATA_CONNECT] Starting connection test  -  host='%s' port=%d method='%s'",
        host, port_int, connection_method,
    )

    t_start = time.monotonic()

    if connection_method == "proxy":
        logger.info(
            "[SAP_ODATA_PROXY] Routing connection test through proxy agent '%s' with host='%s' port='%s' username='%s' password_set=%s",
            config.get('proxy_agent'),
            host,
            port_int,
            username,
            bool(password),
        )
        from sap_proxy_agent.client.dataynchor_client import test_sap_connection
        from sap_proxy_agent.generated import service_pb2

        proxy_agent = str(config.get("proxy_agent") or "").strip()
        src_conf = service_pb2.SrcConnConfig(
            host=host,
            port=port_int,
            username=username,
            password=password,
        )

        try:
            results = test_sap_connection(
                src_type="sap-odata",
                cust_id=proxy_agent,
                source_config=src_conf,
            )
            logger.info(
                "[SAP_ODATA_PROXY] Proxy agent returned result for host='%s' port='%s' keys=%s result=%s",
                host,
                port_int,
                list(results.keys()) if isinstance(results, dict) else type(results).__name__,
                results,
            )
            logger.info(
                "[SAP_ODATA_PROXY] Proxy agent test-connection payload host='%s' port='%s' payload=%s",
                host,
                port_int,
                results,
            )
            if not results:
                logger.error(
                    "[SAP_ODATA_PROXY] Empty proxy agent result host='%s' port='%s' proxy_agent='%s'",
                    host,
                    port_int,
                    proxy_agent,
                )
                _raise("SAP_ODATA_AUTH_ERR", "Proxy agent did not return any connection test results.")

            if not results.get("check_dns"):
                _raise("SAP_ODATA_DNS_001", f"Proxy agent could not resolve hostname '{host}'.")
            if not results.get("check_tcp"):
                _raise("SAP_ODATA_TCP_002", f"Proxy agent could not reach port {port_int} on '{host}'.")
            if not results.get("check_ssl"):
                _raise("SAP_ODATA_SSL_001", f"SSL handshake failed on proxy agent for {host}:{port_int}.")
            if not results.get("check_credentials"):
                _raise("SAP_ODATA_AUTH_401", f"Invalid credentials for user '{username}' on SAP Gateway via proxy.")

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(
                "[SAP_ODATA_PROXY] Connection test via proxy agent failed host='%s' port='%s' username='%s'",
                host,
                port_int,
                username,
            )
            from services.sap.proxy import format_proxy_error_msg
            friendly_msg = format_proxy_error_msg(e, proxy_agent)
            _raise("SAP_ODATA_AUTH_ERR", f"Connection test failed: {friendly_msg}")

        return {
            "status": "ok",
            "message": f"SAP OData connection to {host}:{port_int} verified successfully via proxy agent.",
        }

    # -- Step 1: DNS -----------------------------------------------------------
    try:
        _with_retry(_check_dns, host, step_label="SAP_ODATA_DNS")
    except HTTPException:
        raise
    except Exception as exc:
        _raise("SAP_ODATA_DNS_001", f"DNS resolution failed after {_MAX_RETRIES + 1} attempts: {exc}")

    # -- Step 2: TCP -----------------------------------------------------------
    try:
        _with_retry(_check_tcp, host, port_int, _TIMEOUT, step_label="SAP_ODATA_TCP")
    except HTTPException:
        raise
    except Exception as exc:
        _raise("SAP_ODATA_TCP_002", f"TCP check failed after {_MAX_RETRIES + 1} attempts: {exc}")

    # -- Step 3: SSL -----------------------------------------------------------
    try:
        _with_retry(_check_ssl, host, port_int, _TIMEOUT, step_label="SAP_ODATA_SSL")
    except HTTPException:
        raise
    except Exception as exc:
        _raise("SAP_ODATA_SSL_002", f"SSL handshake failed after {_MAX_RETRIES + 1} attempts: {exc}")

    # -- Step 4: Credentials (no retry  -  auth errors are definitive) -----------
    tls_config: Dict[str, Any] = {"host": host, "port": port_int}
    _check_credentials(host, port_int, username, password, _TIMEOUT, tls_config=tls_config)

    elapsed = time.monotonic() - t_start
    logger.info(
        "[SAP_ODATA_CONNECT] All 4 steps passed for %s:%d  -  completed in %.2fs",
        host, port_int, elapsed,
    )

    res = {
        "status":  "ok",
        "message": f"SAP OData connection to {host}:{port_int} verified successfully.",
        "tls_config": {
            "tls_verification_mode": tls_config.get("tls_verification_mode"),
            "tls_cert_secret_name": tls_config.get("tls_cert_secret_name"),
            "tls_cert_fingerprint": tls_config.get("tls_cert_fingerprint"),
            "tls_cert_not_valid_after": tls_config.get("tls_cert_not_valid_after"),
        }
    }
    print_timing_summary_table("SAP ODATA TEST CONNECTION TIMING SUMMARY")
    return res


import xml.etree.ElementTree as ET


# -----------------------------------------------------------------------------
# PROXY AGENT  -  AlldataSource via gRPC
# -----------------------------------------------------------------------------

@log_execution_time
def _discover_services_via_proxy_agent(
    credentials: dict,
    known_service_names: Optional[List[str]] = None,
) -> list:
    """
    List SAP OData services through the Datasyncher proxy agent via gRPC.

    Calls AlldataSource(cust_id, alldata_url, src_conf) from
        sap_proxy_agent/client/dataynchor_client.py.  The response is expected to be a dict
    with shape compatible with the SAP OData listing response so we can
    extract service names the same way as the direct path.

    Fields used from credentials:
        proxy_agent       -  the gRPC agent identifier / routing key   (cust_id)
        host / port       -  used to construct the listing URL           (alldata_url)
        username/password -  forwarded as SrcConnConfig                 (src_conf)
    """
    from sap_proxy_agent.client.dataynchor_client import AlldataSource
    from sap_proxy_agent.generated import service_pb2

    proxy_agent = str(credentials.get("proxy_agent") or "").strip()
    host        = str(credentials.get("host")        or "").strip()
    username    = str(credentials.get("username")    or "").strip()
    password    = str(credentials.get("password")    or "").strip()

    port = credentials.get("port")
    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 443
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid port; must be an integer")

    custom_listing_url = f"https://{host}:{port_int}{_CUSTOM_LISTING_PATH}"

    # SrcConnConfig proto message fields: host, port (int32), username, password
    src_conf = service_pb2.SrcConnConfig(
        host=host,
        port=port_int,
        username=username,
        password=password,
    )

    def _proxy_fetch_items(alldata_url: str) -> list:
        logger.info(
            "[SAP_ODATA_PROXY] Calling AlldataSource via gRPC proxy agent '%s' -> %s",
            proxy_agent,
            alldata_url,
        )
        try:
            raw = AlldataSource(
                src_type="sap-odata",
                cust_id=proxy_agent,
                data_url=alldata_url,
                source_config=src_conf,
            )
        except Exception as grpc_err:
            logger.error("[SAP_ODATA_PROXY] gRPC AlldataSource call failed: %s", grpc_err)
            from services.sap.proxy import format_proxy_error_msg
            friendly_msg = format_proxy_error_msg(grpc_err, proxy_agent)
            raise HTTPException(status_code=502, detail=friendly_msg) from grpc_err

        alldata_json = raw if isinstance(raw, dict) else {}
        candidate_payload = alldata_json.get("alldataJson", alldata_json)
        if isinstance(candidate_payload, dict) and "alldata_json" in candidate_payload:
            candidate_payload = candidate_payload.get("alldata_json", candidate_payload)
        d = (
            candidate_payload.get("d", candidate_payload)
            if isinstance(candidate_payload, dict)
            else candidate_payload
        )
        if isinstance(d, dict):
            return d.get("results") or []
        if isinstance(d, list):
            return d
        return []

    custom_items = _proxy_fetch_items(custom_listing_url)
    results = _services_from_custom_listing_items(custom_items)
    if results:
        logger.info(
            "[SAP_ODATA_PROXY] Service discovery via proxy completed. Found %d unique services.",
            len(results),
        )
        return results

    registered = _normalize_known_service_names(known_service_names)
    if registered:
        logger.warning(
            "[SAP_ODATA_PROXY] Custom listing returned no services; "
            "probing $metadata for %d registered service(s) (Add Service path).",
            len(registered),
        )
        results = _discover_services_via_metadata_probe_proxy(
            credentials, registered, port_int, src_conf
        )
        logger.info(
            "[SAP_ODATA_PROXY] Registered-service metadata fallback found %d service(s).",
            len(results),
        )
        return results

    logger.warning(
        "[SAP_ODATA_PROXY] Custom listing returned no services and no registered "
        "services to probe; skipping full catalog scan."
    )
    return []


@log_execution_time
def _extract_entity_sets_from_metadata_xml(xml_content: str) -> list:
    """Parse SAP OData $metadata XML into entity-set records."""
    root = ET.fromstring(xml_content.encode("utf-8"))

    entity_types = {}
    for et in root.findall(".//{*}EntityType"):
        et_name = et.get("Name")
        pk_props = []
        key_node = et.find(".//{*}Key")
        if key_node is not None:
            pk_props = [ref.get("Name") for ref in key_node.findall(".//{*}PropertyRef")]

        properties = []
        for prop in et.findall(".//{*}Property"):
            prop_name = prop.get("Name")
            prop_type = prop.get("Type", "Edm.String")
            is_pk = prop_name in pk_props
            precision = prop.get("Precision") or prop.get("MaxLength")

            filterable_val = None
            filter_restriction = None
            display_format = None
            for attr_key, attr_val in prop.attrib.items():
                if attr_key.endswith("filterable"):
                    filterable_val = attr_val
                elif attr_key.endswith("filter-restriction"):
                    filter_restriction = attr_val
                elif attr_key.endswith("display-format"):
                    display_format = attr_val

            is_filterable = filterable_val.lower() != "false" if filterable_val else True

            properties.append({
                "name": prop_name,
                "type": prop_type,
                "is_primary_key": is_pk,
                "precision": precision,
                "filterable": is_filterable,
                "filter_restriction": filter_restriction,
                "display_format": display_format,
            })
        entity_types[et_name] = properties

    entity_sets = []
    all_set_names = set()
    for es in root.findall(".//{*}EntitySet"):
        es_name = es.get("Name")
        if es_name:
            all_set_names.add(es_name)

    for es in root.findall(".//{*}EntitySet"):
        es_name = es.get("Name")
        if not es_name:
            continue

        if es_name.startswith("DeltaOf") or es_name.startswith("DeltaLinksOf"):
            continue

        raw_et_ref = es.get("EntityType", "")
        et_ref = raw_et_ref.split(".")[-1] if raw_et_ref else ""

        properties = entity_types.get(et_ref, [])
        if not properties:
            for et_name, props in entity_types.items():
                if et_name == et_ref or et_name == es_name:
                    properties = props
                    break

        delta_name_opt1 = f"DeltaOf{es_name}"
        delta_name_opt2 = f"DeltaLinksOf{es_name}"

        delta_name = None
        if delta_name_opt1 in all_set_names:
            delta_name = delta_name_opt1
        elif delta_name_opt2 in all_set_names:
            delta_name = delta_name_opt2

        entity_sets.append({
            "entity_name": es_name,
            "is_delta": delta_name is not None,
            "delta_name": delta_name,
            "fields": properties,
        })

    return entity_sets


def _extract_entity_sets_from_metadata_payload(metadata_payload, service_name: str) -> list:
    """Normalize metadata RPC payloads into entity-set records."""
    if not metadata_payload:
        return []

    if isinstance(metadata_payload, list):
        return metadata_payload

    if isinstance(metadata_payload, dict):
        edmx_payload = metadata_payload.get("edmx:Edmx") or metadata_payload.get("edmx") or metadata_payload
        if isinstance(edmx_payload, dict) and (
            "edmx:DataServices" in edmx_payload
            or "Schema" in edmx_payload
            or "EntityContainer" in edmx_payload
        ):
            try:
                return _extract_entity_sets_from_metadata_edmx(edmx_payload)
            except Exception:
                logger.exception(
                    "[SAP_ODATA_PROXY] Failed to parse edmx metadata payload for service '%s'.",
                    service_name,
                )

        for key in ("metadata_json", "metadataJson", "src_details_json", "srcDetailsJson", "d"):
            nested = metadata_payload.get(key)
            if nested is not None:
                extracted = _extract_entity_sets_from_metadata_payload(nested, service_name)
                if extracted:
                    return extracted

        for key in ("entity_sets", "entities", "results"):
            value = metadata_payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                extracted = _extract_entity_sets_from_metadata_payload(value, service_name)
                if extracted:
                    return extracted

        columns = metadata_payload.get("columns")
        if isinstance(columns, list) and columns:
            entity_name = (
                metadata_payload.get("entity_name")
                or metadata_payload.get("entityName")
                or metadata_payload.get("name")
                or service_name
            )
            return [{
                "entity_name": str(entity_name),
                "is_delta": False,
                "delta_name": None,
                "fields": columns,
            }]

        xml_candidate = metadata_payload.get("metadata_xml") or metadata_payload.get("xml")
        if isinstance(xml_candidate, str) and xml_candidate.strip():
            try:
                return _extract_entity_sets_from_metadata_xml(xml_candidate)
            except Exception:
                logger.exception(
                    "[SAP_ODATA_PROXY] Failed to parse XML metadata string for service '%s'.",
                    service_name,
                )

    if isinstance(metadata_payload, str) and metadata_payload.strip():
        try:
            return _extract_entity_sets_from_metadata_xml(metadata_payload)
        except Exception:
            try:
                parsed = json.loads(metadata_payload)
            except Exception:
                parsed = None
            if parsed is not None:
                return _extract_entity_sets_from_metadata_payload(parsed, service_name)

    return []


@log_execution_time
def _extract_entity_sets_from_metadata_edmx(edmx_payload: dict) -> list:
    """Parse xmltodict-style edmx metadata into entity-set records."""
    data_services = edmx_payload.get("edmx:DataServices") or edmx_payload.get("DataServices") or {}
    if not isinstance(data_services, dict):
        return []

    schemas = data_services.get("Schema") or []
    if not isinstance(schemas, list):
        schemas = [schemas]

    parsed_schemas = []
    for schema in schemas:
        if isinstance(schema, dict):
            if schema.get("EntityType"):
                parsed_schemas.append(schema)

    entity_types = {}
    all_set_names = set()

    for schema in parsed_schemas:
        entity_type_items = schema.get("EntityType") or []
        if not isinstance(entity_type_items, list):
            entity_type_items = [entity_type_items]

        for et in entity_type_items:
            if not isinstance(et, dict):
                continue
            et_name = et.get("@Name")
            if not et_name:
                continue

            key_node = et.get("Key") or {}
            property_refs = key_node.get("PropertyRef") if isinstance(key_node, dict) else None
            if property_refs is None:
                pk_props = []
            elif isinstance(property_refs, list):
                pk_props = [ref.get("@Name") for ref in property_refs if isinstance(ref, dict)]
            elif isinstance(property_refs, dict):
                pk_props = [property_refs.get("@Name")]
            else:
                pk_props = []

            raw_properties = et.get("Property") or []
            if not isinstance(raw_properties, list):
                raw_properties = [raw_properties]

            properties = []
            for prop in raw_properties:
                if not isinstance(prop, dict):
                    continue
                prop_name = prop.get("@Name")
                if not prop_name:
                    continue
                prop_type = prop.get("@Type", "Edm.String")
                is_pk = prop_name in pk_props
                precision = prop.get("@Precision") or prop.get("@MaxLength")

                filterable_val = prop.get("@sap:filterable")
                filter_restriction = prop.get("@sap:filter-restriction")
                display_format = prop.get("@sap:display-format")
                is_filterable = str(filterable_val).strip().lower() != "false" if filterable_val is not None else True

                properties.append({
                    "name": prop_name,
                    "type": prop_type,
                    "is_primary_key": is_pk,
                    "precision": precision,
                    "filterable": is_filterable,
                    "filter_restriction": filter_restriction,
                    "display_format": display_format,
                })
            entity_types[et_name] = properties

        entity_container = schema.get("EntityContainer") or {}
        if isinstance(entity_container, list):
            entity_container = next((item for item in entity_container if isinstance(item, dict)), {})
        entity_sets = entity_container.get("EntitySet") if isinstance(entity_container, dict) else []
        if not isinstance(entity_sets, list):
            entity_sets = [entity_sets]

        for es in entity_sets:
            if isinstance(es, dict) and es.get("@Name"):
                all_set_names.add(es["@Name"])

    results = []
    for schema in parsed_schemas:
        entity_container = schema.get("EntityContainer") or {}
        if isinstance(entity_container, list):
            entity_container = next((item for item in entity_container if isinstance(item, dict)), {})
        entity_sets = entity_container.get("EntitySet") if isinstance(entity_container, dict) else []
        if not isinstance(entity_sets, list):
            entity_sets = [entity_sets]

        for es in entity_sets:
            if not isinstance(es, dict):
                continue
            es_name = es.get("@Name")
            if not es_name:
                continue
            if es_name.startswith("DeltaOf") or es_name.startswith("DeltaLinksOf"):
                continue

            raw_et_ref = es.get("@EntityType", "")
            et_ref = raw_et_ref.split(".")[-1] if raw_et_ref else ""
            properties = entity_types.get(et_ref, [])
            if not properties:
                for et_name, props in entity_types.items():
                    if et_name == et_ref or et_name == es_name:
                        properties = props
                        break

            delta_name_opt1 = f"DeltaOf{es_name}"
            delta_name_opt2 = f"DeltaLinksOf{es_name}"
            delta_name = None
            if delta_name_opt1 in all_set_names:
                delta_name = delta_name_opt1
            elif delta_name_opt2 in all_set_names:
                delta_name = delta_name_opt2

            results.append({
                "entity_name": es_name,
                "is_delta": delta_name is not None,
                "delta_name": delta_name,
                "fields": properties,
            })

    return results


def _discover_service_entities_via_proxy_agent(credentials: dict, service_name: str) -> list:
    """Fetch entity metadata through the proxy agent.

    The proxy flow uses two calls:
    1. SourceDetail to discover entity and delta names.
    2. Metadata to fetch the field schema for those entities.

    We then merge the two responses by entity_name so the database write path
    stays identical to the direct connection flow.
    """
    from sap_proxy_agent.client.dataynchor_client import get_metadata, SourceDetail
    from sap_proxy_agent.generated import service_pb2

    proxy_agent = str(credentials.get("proxy_agent") or "").strip()
    host = str(credentials.get("host") or "").strip()
    username = str(credentials.get("username") or "").strip()
    password = str(credentials.get("password") or "").strip()
    port = credentials.get("port")

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 443
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid port; must be an integer")

    src_conf = service_pb2.SrcConnConfig(
        host=host,
        port=port_int,
        username=username,
        password=password,
    )

    meta_url = f"https://{host}:{port_int}/sap/opu/odata/sap/{service_name}/$metadata"
    logger.info(
        "[SAP_ODATA_PROXY] Calling SourceDetail via gRPC proxy agent '%s' for service '%s'",
        proxy_agent or "<missing>",
        service_name,
    )

    source_detail_raw = None
    try:
        source_detail_raw = SourceDetail(
            src_type="sap-odata",
            cust_id=proxy_agent,
            source_config=src_conf,
            service_name=service_name,
        )
    except Exception as grpc_err:
        logger.warning("[SAP_ODATA_PROXY] gRPC SourceDetail call failed: %s", grpc_err)

    logger.info(
        "[SAP_ODATA_PROXY] SourceDetail response top_level_keys=%s raw=%s",
        list(source_detail_raw.keys()) if isinstance(source_detail_raw, dict) else type(source_detail_raw).__name__,
        source_detail_raw,
    )

    source_entities = parse_proxy_source_detail_entities(source_detail_raw)
    logger.info(
        "[SAP_ODATA_PROXY] Parsed %d entities from SourceDetail for service '%s'",
        len(source_entities),
        service_name,
    )

    logger.info(
        "[SAP_ODATA_PROXY] Calling Metadata via gRPC proxy agent '%s' for service '%s'",
        proxy_agent or "<missing>",
        service_name,
    )

    metadata_raw = None
    try:
        metadata_raw = get_metadata(
            source_type="sap-odata",
            cust_id=proxy_agent,
            url=meta_url,
            source_config=src_conf,
        )
    except Exception as grpc_err:
        logger.warning("[SAP_ODATA_PROXY] gRPC Metadata call failed: %s", grpc_err)

    logger.info(
        "[SAP_ODATA_PROXY] Metadata response top_level_keys=%s raw=%s",
        list(metadata_raw.keys()) if isinstance(metadata_raw, dict) else type(metadata_raw).__name__,
        metadata_raw,
    )

    metadata_entities = parse_proxy_metadata_entities(metadata_raw)
    logger.info(
        "[SAP_ODATA_PROXY] Parsed %d metadata entities for service '%s'",
        len(metadata_entities),
        service_name,
    )

    if source_entities and metadata_entities:
        metadata_by_name = {
            str(item.get("entity_name") or "").strip(): item
            for item in metadata_entities
            if str(item.get("entity_name") or "").strip()
        }
        metadata_by_lower = {
            key.lower(): value
            for key, value in metadata_by_name.items()
        }
        merged_entities = []
        for source_item in source_entities:
            entity_name = str(source_item.get("entity_name") or "").strip()
            if not entity_name:
                continue

            merged_item = dict(source_item)
            metadata_item = metadata_by_name.get(entity_name) or metadata_by_lower.get(entity_name.lower())

            if metadata_item:
                merged_item["fields"] = metadata_item.get("fields") or []
                merged_item["is_delta"] = bool(merged_item.get("is_delta") or metadata_item.get("is_delta"))
                merged_item["delta_name"] = merged_item.get("delta_name") or metadata_item.get("delta_name")
            else:
                merged_item["fields"] = merged_item.get("fields") or []

            merged_entities.append(merged_item)

        if merged_entities:
            logger.info(
                "[SAP_ODATA_PROXY] Merged %d proxy entities for service '%s'",
                len(merged_entities),
                service_name,
            )
            return merged_entities

    if metadata_entities:
        return metadata_entities

    if source_entities:
        for entity in source_entities:
            entity.setdefault("fields", [])
        return source_entities

    raise HTTPException(
        status_code=422,
        detail=f"Proxy agent returned no parseable source detail or metadata for service '{service_name}'.",
    )


def _normalize_catalog_service_name(name: str) -> str:
    """
    Map IWFND catalog service identifiers to gateway OData service names.

    Catalog ServiceCollection often exposes IDs like ``ZFI_GL_4_SRV_0001`` while
    metadata/data URLs use ``ZFI_GL_4_SRV`` (version suffix omitted).
    """
    text = str(name or "").strip()
    if not text:
        return text
    if text.endswith("_0001"):
        return text[:-5]
    if re.search(r"_\d{4}$", text):
        stripped = re.sub(r"_\d{4}$", "", text)
        if stripped.endswith(("_SRV", "_CDS")):
            return stripped
    return text


def _service_name_from_gateway_path(raw: str) -> Optional[str]:
    """Extract SAP OData service name from a catalog ID or gateway URL path."""
    text = str(raw or "").strip()
    if not text:
        return None
    lower = text.lower()
    marker = "/sap/opu/odata/sap/"
    if marker in lower:
        idx = lower.index(marker) + len(marker)
        tail = text[idx:].strip("/")
        if not tail:
            return None
        parsed = tail.split("/")[0].split("?")[0].strip() or None
        return _normalize_catalog_service_name(parsed) if parsed else None
    plain = text.strip("/").split("/")[-1].split("?")[0].strip()
    if not plain:
        return None
    return _normalize_catalog_service_name(plain)


def _services_from_custom_listing_items(items: list) -> list:
    """Parse Z_I_DS_SRVC_LIST_SRV rows into {service_name} records."""
    results: list = []
    seen_services: set = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        srv_name = item.get("SERVICE")
        if not srv_name:
            logger.warning(
                "[SAP_ODATA_DISCOVER] Index %d: 'SERVICE' property not found in entry: %s",
                index,
                list(item.keys()),
            )
            continue
        srv_name_str = str(srv_name).strip()
        if srv_name_str in _TECHNICAL_SERVICES:
            continue
        if srv_name_str in seen_services:
            continue
        seen_services.add(srv_name_str)
        results.append({"service_name": srv_name_str})
    return results


def _service_name_from_catalog_item(item: dict) -> Optional[str]:
    # Gateway URL in ID is authoritative for OData calls.
    for key in ("ID", "Id", "id"):
        val = item.get(key)
        if not val:
            continue
        raw = str(val).strip()
        if "/sap/opu/odata/sap/" in raw.lower():
            parsed = _service_name_from_gateway_path(raw)
            if parsed:
                return parsed
    for key in ("TechnicalName", "technicalName", "Name", "name"):
        val = item.get(key)
        if val:
            candidate = _normalize_catalog_service_name(str(val).strip())
            if candidate:
                return candidate
    for key in ("ID", "Id", "id"):
        val = item.get(key)
        if val:
            parsed = _service_name_from_gateway_path(str(val))
            if parsed:
                return parsed
    return None


def _services_from_catalog_items(items: list) -> list:
    """Parse IWFND CATALOGSERVICE ServiceCollection rows into {service_name} records."""
    results: list = []
    seen_services: set = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        srv_name_str = _service_name_from_catalog_item(item)
        if not srv_name_str:
            logger.debug(
                "[SAP_ODATA_DISCOVER] Catalog index %d: could not resolve service name from %s",
                index,
                list(item.keys()),
            )
            continue
        if srv_name_str in _TECHNICAL_SERVICES:
            continue
        if srv_name_str in seen_services:
            continue
        seen_services.add(srv_name_str)
        results.append({"service_name": srv_name_str})
    return results


def _create_sap_gateway_session(host: str, port) -> requests.Session:
    try:
        from utils.sap_tls_helper import build_session_from_config as build_session_for_source
        return build_session_for_source({"host": str(host or "").strip(), "port": port})
    except Exception:
        session = requests.Session()
        session.verify = False
        return session


def _fetch_odata_collection_items(
    session: requests.Session,
    start_url: str,
    username: str,
    password: str,
    timeout: int,
) -> list:
    """Fetch all pages from an OData V2 JSON collection feed."""
    items: list = []
    url: Optional[str] = start_url
    while url:
        response = session.get(
            url,
            auth=(username, password),
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Service listing failed (HTTP {response.status_code})",
            )
        data = response.json()
        page_items: list = []
        next_url: Optional[str] = None
        if isinstance(data, dict):
            d = data.get("d", {})
            if isinstance(d, dict):
                page_items = d.get("results") or []
                next_url = d.get("__next")
            elif isinstance(d, list):
                page_items = d
        items.extend(page_items)
        url = str(next_url).strip() if next_url else None
    return items


def _normalize_known_service_names(service_names: Optional[List[str]]) -> List[str]:
    """Deduplicate registered SAP OData service names, skipping technical services."""
    if not service_names:
        return []
    seen: set = set()
    ordered: List[str] = []
    for raw in service_names:
        name = str(raw or "").strip()
        if not name or name in _TECHNICAL_SERVICES or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _discover_services_via_metadata_probe_direct(
    session: requests.Session,
    host: str,
    port_int: int,
    username: str,
    password: str,
    service_names: List[str],
    timeout: int,
) -> list:
    """
    Resolve services by probing each service's $metadata URL (same SAP call as Add New Service).
    """
    results: list = []
    for service_name in _normalize_known_service_names(service_names):
        url = f"https://{host}:{port_int}/sap/opu/odata/sap/{service_name}/$metadata"
        logger.info(
            "[SAP_ODATA_DISCOVER] Probing registered service metadata: %s",
            service_name,
        )
        try:
            response = session.get(
                url,
                auth=(username, password),
                timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "[SAP_ODATA_DISCOVER] Metadata probe failed for '%s': %s",
                service_name,
                exc,
            )
            continue
        if response.status_code == 200:
            results.append({"service_name": service_name})
        else:
            logger.warning(
                "[SAP_ODATA_DISCOVER] Metadata probe for '%s' returned HTTP %s",
                service_name,
                response.status_code,
            )
    logger.info(
        "[SAP_ODATA_DISCOVER] Registered-service metadata fallback completed. "
        "Found %d reachable service(s).",
        len(results),
    )
    return results


def _discover_services_via_metadata_probe_proxy(
    credentials: dict,
    service_names: List[str],
    port_int: int,
    src_conf,
) -> list:
    """Probe $metadata for registered services through the gRPC proxy agent."""
    from sap_proxy_agent.client.dataynchor_client import get_metadata

    proxy_agent = str(credentials.get("proxy_agent") or "").strip()
    host = str(credentials.get("host") or "").strip()
    results: list = []
    for service_name in _normalize_known_service_names(service_names):
        meta_url = f"https://{host}:{port_int}/sap/opu/odata/sap/{service_name}/$metadata"
        logger.info(
            "[SAP_ODATA_PROXY] Probing registered service metadata: %s",
            service_name,
        )
        try:
            metadata_raw = get_metadata(
                source_type="sap-odata",
                cust_id=proxy_agent,
                url=meta_url,
                source_config=src_conf,
            )
        except Exception as grpc_err:
            logger.warning(
                "[SAP_ODATA_PROXY] Metadata probe failed for '%s': %s",
                service_name,
                grpc_err,
            )
            continue
        metadata_entities = parse_proxy_metadata_entities(metadata_raw)
        if metadata_entities:
            results.append({"service_name": service_name})
        else:
            logger.warning(
                "[SAP_ODATA_PROXY] Metadata probe for '%s' returned no parseable entities",
                service_name,
            )
    return results


def _registered_service_names_for_connection(connection_id: int) -> List[str]:
    """Distinct SAP OData service names already stored for this connection."""
    names: set = set()
    sql = """
        SELECT service_name, "table"
        FROM source_table_selection
        WHERE connection_id = %s
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (connection_id,))
            for service_name, table_name in cursor.fetchall():
                if service_name and str(service_name).strip():
                    names.add(str(service_name).strip())
                    continue
                table_text = str(table_name or "").strip()
                if "/" in table_text:
                    prefix = table_text.split("/", 1)[0].strip()
                    if prefix:
                        names.add(prefix)
    return sorted(names)


def _listing_failure_detail(
    custom_status: int,
    *,
    known_service_names: Optional[List[str]] = None,
    probe_results_empty: bool = False,
) -> str:
    registered = _normalize_known_service_names(known_service_names)
    if not registered:
        return (
            f"Service listing failed: custom listing service returned HTTP {custom_status} "
            "and no SAP OData services are registered on this connection yet. "
            "Use Add New Service to register a service by name, or ask SAP BASIS to activate "
            "Z_I_DS_SRVC_LIST_SRV for this user."
        )
    if probe_results_empty:
        joined = ", ".join(registered)
        return (
            f"Service listing failed: custom listing service returned HTTP {custom_status} "
            f"and $metadata could not be fetched for registered service(s): {joined}."
        )
    return f"Service listing failed (HTTP {custom_status})"


@log_execution_time
def discover_sap_odata_services(
    credentials: dict,
    known_service_names: Optional[List[str]] = None,
) -> list:
    """
    List all SAP OData services for a connection.

    Routing:
        connection_method == 'proxy'  -> calls the Datasyncher proxy agent
                                        via gRPC (AlldataSource)
        connection_method == 'direct' -> connects directly to the SAP gateway
                                        using the custom listing service
                                        Z_I_DS_SRVC_LIST_SRV/FactsOfZ_I_DS_SRVC_LIST
    """
    connection_method = str(credentials.get("connection_method") or "direct").strip().lower()

    # -- Proxy Agent path -----------------------------------------------------
    if connection_method == "proxy":
        logger.info("[SAP_ODATA_DISCOVER] connection_method=proxy -> routing through gRPC proxy agent")
        return _discover_services_via_proxy_agent(
            credentials,
            known_service_names=known_service_names,
        )

    # -- Direct path ----------------------------------------------------------
    host = str(credentials.get("host") or "").strip()
    port = credentials.get("port")
    username = str(credentials.get("username") or "").strip()
    password = str(credentials.get("password") or "").strip()

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 443
    except (TypeError, ValueError):
        logger.error(f"[SAP_ODATA_DISCOVER] Invalid port specified: {port}")
        raise HTTPException(status_code=422, detail="Invalid port; must be an integer")

    url = f"https://{host}:{port_int}{_CUSTOM_LISTING_PATH}"
    connect_timeout = 15

    logger.info(f"[SAP_ODATA_DISCOVER] Fetching service list from custom endpoint: {url}")

    try:
        session = _create_sap_gateway_session(host, port)

        response = session.get(
            url,
            auth=(username, password),
            headers={"Accept": "application/json"},
            timeout=connect_timeout,
        )
        
        logger.info(f"[SAP_ODATA_DISCOVER] Response status code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            items: list = []
            if isinstance(data, dict):
                d = data.get("d", {})
                if isinstance(d, dict):
                    items = d.get("results") or []
                elif isinstance(d, list):
                    items = d
            logger.info(
                "[SAP_ODATA_DISCOVER] Retrieved %d service/entity records from custom listing feed",
                len(items),
            )
            results = _services_from_custom_listing_items(items)
            logger.info(
                "[SAP_ODATA_DISCOVER] Service discovery completed. Found %d unique services to list",
                len(results),
            )
            return results

        if response.status_code in (403, 404):
            logger.warning(
                "[SAP_ODATA_DISCOVER] Custom listing unavailable (HTTP %s): %s",
                response.status_code,
                response.text[:500],
            )
            registered = _normalize_known_service_names(known_service_names)
            if registered:
                logger.info(
                    "[SAP_ODATA_DISCOVER] Falling back to $metadata probe for %d "
                    "registered service(s) (Add Service path).",
                    len(registered),
                )
                results = _discover_services_via_metadata_probe_direct(
                    session,
                    host,
                    port_int,
                    username,
                    password,
                    registered,
                    connect_timeout,
                )
                if results:
                    return results
                raise HTTPException(
                    status_code=response.status_code,
                    detail=_listing_failure_detail(
                        response.status_code,
                        known_service_names=registered,
                        probe_results_empty=True,
                    ),
                )
            raise HTTPException(
                status_code=response.status_code,
                detail=_listing_failure_detail(
                    response.status_code,
                    known_service_names=registered,
                ),
            )

        logger.warning(
            "[SAP_ODATA_DISCOVER] SAP OData service listing returned HTTP %s: %s",
            response.status_code,
            response.text,
        )
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Service listing failed (HTTP {response.status_code})",
        )

    except HTTPException:
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"[SAP_ODATA_DISCOVER] SAP OData service discovery HTTP error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to SAP gateway: {str(e)}"
        )
    except Exception as e:
        logger.error(f"[SAP_ODATA_DISCOVER] SAP OData service discovery failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"SAP service listing failed: {str(e)}"
        )


@log_execution_time
def discover_sap_odata_service_entities(credentials: dict, service_name: str) -> list:
    """
    Connect to SAP OData service, fetch $metadata XML, and extract entities, primary keys, fields, and delta support.
    """
    host = str(credentials.get("host") or "").strip()
    port = credentials.get("port")
    username = str(credentials.get("username") or "").strip()
    password = str(credentials.get("password") or "").strip()

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 443
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid port; must be an integer")

    url = f"https://{host}:{port_int}/sap/opu/odata/sap/{service_name}/$metadata"
    connect_timeout = 15

    connection_method = str(credentials.get("connection_method") or "direct").strip().lower()
    proxy_agent = str(credentials.get("proxy_agent") or "").strip()
    logger.info(
        "[SAP_ODATA_DISCOVER] service='%s' host='%s' port=%d method='%s' proxy_agent_present=%s",
        service_name,
        host,
        port_int,
        connection_method,
        bool(proxy_agent),
    )

    try:
        if connection_method == "proxy":
            logger.info(
                "[SAP_ODATA_PROXY] Fetching service entity details for '%s' via proxy agent '%s'",
                service_name,
                proxy_agent or "<missing>",
            )
            return _discover_service_entities_via_proxy_agent(credentials, service_name)

        try:
            from utils.sap_tls_helper import build_session_from_config as build_session_for_source
            session = build_session_for_source({"host": str(host or "").strip(), "port": port})
        except Exception:
            session = requests.Session()
            session.verify = False

        response = session.get(
            url,
            auth=(username, password),
            timeout=connect_timeout,
        )
        if response.status_code != 200:
            logger.warning(f"SAP OData metadata fetch for {service_name} returned HTTP {response.status_code}")
            msg = f"Metadata fetch failed (HTTP {response.status_code})."
            if response.status_code in [403, 404]:
                msg = f"Service '{service_name}' not found or access forbidden. Please verify the service name."
            raise HTTPException(
                status_code=response.status_code,
                detail=msg
            )

        xml_content = response.text
        logger.info(
            "[SAP_ODATA_DIRECT] Fetched metadata directly from SAP gateway for service '%s'",
            service_name,
        )
        return _extract_entity_sets_from_metadata_xml(response.text)
        root = ET.fromstring(xml_content.encode("utf-8"))

        entity_types = {}
        for et in root.findall(".//{*}EntityType"):
            et_name = et.get("Name")
            pk_props = []
            key_node = et.find(".//{*}Key")
            if key_node is not None:
                pk_props = [ref.get("Name") for ref in key_node.findall(".//{*}PropertyRef")]

            properties = []
            for prop in et.findall(".//{*}Property"):
                prop_name = prop.get("Name")
                prop_type = prop.get("Type", "Edm.String")
                is_pk = prop_name in pk_props
                precision = prop.get("Precision") or prop.get("MaxLength")

                # Scan all attributes once to capture sap:filterable and sap:filter-restriction.
                # Both carry a SAP-namespace prefix (e.g. {http://www.sap.com/Protocols/SAPData})
                # so we match by suffix rather than the full qualified name.
                filterable_val = None
                filter_restriction = None
                display_format = None
                for attr_key, attr_val in prop.attrib.items():
                    if attr_key.endswith("filterable"):
                        filterable_val = attr_val
                    elif attr_key.endswith("filter-restriction"):
                        filter_restriction = attr_val
                    elif attr_key.endswith("display-format"):
                        display_format = attr_val

                is_filterable = filterable_val.lower() != "false" if filterable_val else True

                properties.append({
                    "name": prop_name,
                    "type": prop_type,
                    "is_primary_key": is_pk,
                    "precision": precision,
                    # sap:filterable  -  false means this field MUST NOT appear in $filter at all.
                    "filterable": is_filterable,
                    # sap:filter-restriction  -  one of: None | 'single-value' | 'multi-value' | 'interval-boundaries'
                    # None means no restriction  -  any operator / combination is allowed.
                    "filter_restriction": filter_restriction,
                    "display_format": display_format,
                })
            entity_types[et_name] = properties

        entity_sets = []
        all_set_names = set()
        for es in root.findall(".//{*}EntitySet"):
            es_name = es.get("Name")
            if es_name:
                all_set_names.add(es_name)

        for es in root.findall(".//{*}EntitySet"):
            es_name = es.get("Name")
            if not es_name:
                continue

            # Hide both DeltaOf and DeltaLinksOf entities from being listed as main tables in the UI
            if es_name.startswith("DeltaOf") or es_name.startswith("DeltaLinksOf"):
                continue

            raw_et_ref = es.get("EntityType", "")
            et_ref = raw_et_ref.split(".")[-1] if raw_et_ref else ""

            properties = entity_types.get(et_ref, [])
            if not properties:
                for et_name, props in entity_types.items():
                    if et_name == et_ref or et_name == es_name:
                        properties = props
                        break

            # Check both possible delta naming conventions in SAP OData services
            delta_name_opt1 = f"DeltaOf{es_name}"
            delta_name_opt2 = f"DeltaLinksOf{es_name}"

            delta_name = None
            if delta_name_opt1 in all_set_names:
                delta_name = delta_name_opt1
            elif delta_name_opt2 in all_set_names:
                delta_name = delta_name_opt2

            is_delta = delta_name is not None

            entity_sets.append({
                "entity_name": es_name,
                "is_delta": is_delta,
                "delta_name": delta_name,
                "fields": properties
            })

        return entity_sets

    except HTTPException:
        raise
    except ET.ParseError as e:
        logger.error(f"SAP OData metadata XML parsing failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse SAP OData metadata XML: {str(e)}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"SAP OData metadata fetch HTTP error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch metadata from SAP gateway: {str(e)}"
        )
    except Exception as e:
        logger.error(f"SAP OData service entity discovery failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"SAP entity discovery failed: {str(e)}"
        )


# -- Database updates and list tables implementation ---------------------------

import json
from contextlib import contextmanager
from utils.database_utils import postgres_connection

EDM_TYPE_MAPPING = {
    "Edm.Binary": "Binary",
    "Edm.Boolean": "Boolean",
    "Edm.Byte": "Binary",
    "Edm.DateTime": "Nvarchar",
    "Edm.Decimal": "Decimal",
    "Edm.Double": "Double",
    "Edm.Guid": "String",
    "Edm.Int16": "Short",
    "Edm.Int32": "Integer",
    "Edm.Int64": "Long",
    "Edm.SByte": "Binary",
    "Edm.Single": "Float",
    "Edm.String": "Nvarchar",
    "Edm.Time": "Nvarchar",
    "Edm.DateTimeOffset": "Instant"
}
SAP_ODQ_TECHNICAL_FIELDS = {"ODQ_CHANGEMODE", "ODQ_ENTITYCNTR"}

def map_odata_type(odata_type: str, precision=None) -> str:
    formatted = EDM_TYPE_MAPPING.get(odata_type, "Nvarchar")
    
    # Enforce safe default size of 8 for date/time columns
    if odata_type in ["Edm.DateTime", "Edm.Time", "Edm.DateTimeOffset"]:
        try:
            p_val = int(precision) if precision else 0
        except ValueError:
            p_val = 0
        precision = 8 if p_val <= 0 else p_val

    if precision is not None and str(precision).strip() != "" and str(precision).lower() != "none":
        # Fall back to 8 if string/binary types explicitly resolve to length 0
        if str(precision) == "0" and formatted in ["Nvarchar", "Binary"]:
            precision = 8
        formatted = f"{formatted}({precision})"
    return formatted


@contextmanager
def get_db_connection():
    conn = postgres_connection()
    try:
        yield conn
    finally:
        if conn:
            conn.close()


def _upsert_entities(cursor, connection_id: int, service_name: str, entities: list) -> int:
    """Upsert list of entities to database, returning count of entities processed."""
    entities_processed = 0
    for ent in entities:
        entity_name = ent.get("entity_name")
        if not entity_name:
            continue

        raw_fields = ent.get("fields") or []
        table_fields_dict = {}
        primary_keys_list = []

        for f in raw_fields:
            f_name = f.get("name")
            if not f_name or str(f_name).upper() in SAP_ODQ_TECHNICAL_FIELDS:
                continue
            f_type = f.get("type", "Edm.String")
            is_pk = f.get("is_primary_key", False)
            precision = f.get("precision")

            table_fields_dict[f_name] = {
                "data_type": map_odata_type(f_type, precision),
                "is_primary_key": is_pk,
                # Raw OData EDM type used by the filter builder to select
                # the correct literal format (datetime vs datetimeoffset etc.).
                "edm_type": f_type,
                # sap:filterable - false means field MUST NOT appear in $filter.
                "filterable": f.get("filterable", True),
                # sap:filter-restriction controls which operators are allowed
                "filter_restriction": f.get("filter_restriction"),
                "display_format": f.get("display_format"),
                "precision": precision,
            }
            if is_pk:
                primary_keys_list.append(f_name)

        table_db_name = entity_name
        table_fields_json = json.dumps(table_fields_dict)
        primary_keys_json = json.dumps(primary_keys_list)
        is_delta = ent.get("is_delta", False)
        delta_name = ent.get("delta_name")

        sql_upsert = """
            INSERT INTO source_table_selection (connection_id, "table", table_fields, primary_keys, selected, added_on, is_history, is_delta, delta_name, service_name, status, is_mapping_checked)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, NULL, %s)
            ON CONFLICT (connection_id, "table")
            DO UPDATE SET 
                table_fields = EXCLUDED.table_fields,
                primary_keys = EXCLUDED.primary_keys,
                is_delta = EXCLUDED.is_delta,
                delta_name = EXCLUDED.delta_name,
                service_name = EXCLUDED.service_name,
                status = NULL;
        """
        cursor.execute(
            sql_upsert,
            (
                connection_id,
                table_db_name,
                table_fields_json,
                primary_keys_json,
                False, # selected
                False, # is_history
                is_delta,
                delta_name,
                service_name,
                False # is_mapping_checked
            )
        )
        entities_processed += 1
    return entities_processed

def save_sap_odata_service_entities(
    connection_id: int,
    credentials: dict,
    service_name: str,
    selected_entities: list,
) -> int:
    """Discover a service and persist only the selected entities."""
    entities = discover_sap_odata_service_entities(credentials, service_name)
    selected_names = {str(name) for name in (selected_entities or [])}
    entities_to_save = [
        entity for entity in entities
        if entity.get("entity_name") in selected_names
    ]

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            saved_count = _upsert_entities(
                cursor, connection_id, service_name, entities_to_save
            )
        conn.commit()

    return saved_count


def _mark_migration(conn, job_name: str, status: str) -> None:
    """Update migration record status in a single query."""
    with conn.cursor() as cursor:
        sql_update_record = """
            UPDATE migration_record
            SET status = %s, updated_at = NOW()
            WHERE job_name = %s;
        """
        cursor.execute(sql_update_record, (status, job_name))
    conn.commit()


def _notify(connection_id: int, job_name: str, success: bool, message: str) -> None:
    """Log the status change and trigger WebSocket update."""
    try:
        from utils.error_utils import update_migration_status_log
        update_migration_status_log(
            job_name=job_name,
            message=message,
            status="S" if success else "E"
        )
    except Exception as log_err:
        logger.warning(f"SAP Listing: Log update failed: {log_err}")

    try:
        from utils.websocket_utils import trigger_update_schema_ws_status_update
        trigger_update_schema_ws_status_update(connection_id)
    except Exception as ws_err:
        logger.warning(f"SAP Listing: WebSocket trigger failed: {ws_err}")


@log_execution_time
def discover_and_populate_sap_odata_service(connection_id: int, credentials: dict, job_name: str) -> dict:
    """
    Connect to SAP OData gateway, discover services and entities, and populate them in source_table_selection.
    Then mark the migration record status as completed or failed, log the outcome, and trigger WS update.
    """
    reset_timing_records()
    import time
    local_start_time = time.time()
    try:
        # 1. Fetch available services (or registered services when custom listing is blocked)
        registered_names = _registered_service_names_for_connection(connection_id)
        services = discover_sap_odata_services(
            credentials,
            known_service_names=registered_names,
        )
        logger.info(f"SAP Listing: Found {len(services)} services for connection {connection_id}")

        entities_processed = 0

        # Use connection context manager
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 2. Loop through each service and fetch entities
                for srv in services:
                    service_name = srv.get("service_name")
                    # Skip technical services
                    if not service_name or service_name in _TECHNICAL_SERVICES:
                        logger.info(f"SAP Listing: Skipping catalog service {service_name}")
                        continue

                    try:
                        entities = discover_sap_odata_service_entities(credentials, service_name)
                    except Exception as ent_err:
                        logger.warning(f"SAP Listing: Failed to discover entities for service {service_name}: {ent_err}")
                        continue

                    logger.info(f"SAP Listing: Found {len(entities)} entities for service {service_name}")
                    entities_processed += _upsert_entities(cursor, connection_id, service_name, entities)

                # Update migration record status to completed
                _mark_migration(conn, job_name, 'completed')

        execution_time = time.time() - local_start_time
        completion_message = (
            f"Update schema completed successfully in {execution_time:.2f} seconds. "
            f"Discovered {entities_processed} entities."
        )
        _notify(connection_id, job_name, success=True, message=completion_message)

        return {
            'status_code': 200,
            'message': f"Successfully discovered and registered {entities_processed} OData entities."
        }

    except Exception as e:
        logger.exception(f"SAP Listing: Exception during schema discovery: {e}")

        # Mark migration record status as failed
        try:
            with get_db_connection() as conn_err:
                _mark_migration(conn_err, job_name, 'failed')
        except Exception as record_err:
            logger.warning(f"SAP Listing: Failed to set migration record to FAILED: {record_err}")

        _notify(connection_id, job_name, success=False, message=f"Update schema failed: {str(e)}")

        raise e
    finally:
        print_timing_summary_table("SAP ODATA TABLE DISCOVERY TIMING SUMMARY")


