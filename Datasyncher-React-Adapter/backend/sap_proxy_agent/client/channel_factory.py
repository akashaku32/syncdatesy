"""mTLS gRPC channel to the SAP proxy agent hub."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import grpc

from sap_proxy_agent.client.hub_target import grpc_server_target

logger = logging.getLogger(__name__)


def _host_from_grpc_target(target: str) -> str:
    """Return host part of ``host:port`` (supports IPv6 ``[addr]:port``)."""
    t = (target or "").strip()
    if not t:
        return ""
    if t.startswith("["):
        close = t.find("]:")
        if close != -1:
            return t[1:close]
        if t.endswith("]"):
            return t[1:-1]
        return t
    if ":" in t:
        host, port = t.rsplit(":", 1)
        if port.isdigit():
            return host
    return t


def _map_hub_connect_host_to_tls_peer_name(host: str) -> str:
    """
    Map the gRPC dial host to the name we verify on the hub leaf certificate.

    The hub Service DNS name is ``sap-proxy-agent-hub-svc....`` but batch hub TLS
    leaf certs are issued for ``sap-proxy-agent-hub`` (and optional public SAN
    like ``agent-hub.gcp.datasyncher.com``) — not for the Service FQDN. Dialing
    the ClusterIP with ``ssl_target_name_override`` set to the Service name
    therefore still fails hostname verification unless we use a matching SAN.
    """
    h = (host or "").strip()
    if not h:
        return h
    lower = h.lower()
    if lower == "sap-proxy-agent-hub-svc" or lower.startswith(
        "sap-proxy-agent-hub-svc."
    ):
        return "sap-proxy-agent-hub"
    return h


def _tls_peer_server_name() -> str:
    """
    Name used for server certificate verification (SNI / hostname check).

    gRPC resolves Kubernetes DNS to a ClusterIP; the leaf cert often has a DNS SAN
    for the Service name only, not the IP, which causes
    "Hostname Verification Check failed" unless we set
    ``grpc.ssl_target_name_override`` to that DNS name.

    Override explicitly with PROXY_AGENT_HUB_SSL_TARGET_NAME when connecting via
    a bare IP or LB address whose cert uses a different hostname (e.g.
    agent-hub.gcp.datasyncher.com).

    When ``PROXY_AGENT_SERVER_HOST`` is the in-cluster Service name
    ``sap-proxy-agent-hub-svc...``, we verify as ``sap-proxy-agent-hub`` to match
    hub leaf SANs (see ``_map_hub_connect_host_to_tls_peer_name``).
    """
    override = (os.environ.get("PROXY_AGENT_HUB_SSL_TARGET_NAME") or "").strip()
    if override:
        return override
    env_host = (os.environ.get("PROXY_AGENT_SERVER_HOST") or "").strip()
    if env_host:
        return _map_hub_connect_host_to_tls_peer_name(env_host)
    return _map_hub_connect_host_to_tls_peer_name(_host_from_grpc_target(grpc_server_target()))


def _config_json_path() -> Path:
    override = (os.environ.get("SAP_PROXY_AGENT_GRPC_CONFIG") or "").strip()
    if override:
        return Path(override)
    # Dev: sap_proxy_agent/dev/config.json (gitignored; copy from config.json.example)
    return Path(__file__).resolve().parents[1] / "dev" / "config.json"


def _load_mtls_material() -> tuple[bytes, bytes, bytes]:
    """
    Load client mTLS material for the platform → hub gRPC connection.

    Resolution order:
      1. Directory mount (batch/K8s): PROXY_AGENT_HUB_MTLS_DIR with ca.crt +
         client_svr_server.crt/key (same layout as sap-proxy-agent-hub-mtls Secret).
      2. Inline PEM env vars: PROXY_AGENT_HUB_TRUSTED_CA_PEM, *_CLIENT_KEY_PEM, *_CLIENT_CERT_PEM.
      3. JSON file: SAP_PROXY_AGENT_GRPC_CONFIG or dev/config.json
         (keys: trusted_ca_cert, client_svr_cert, client_svr_key).
    """
    mtls_dir = (os.environ.get("PROXY_AGENT_HUB_MTLS_DIR") or "").strip()
    if mtls_dir:
        base = Path(mtls_dir)
        ca_path = base / "ca.crt"
        # Same filenames as hub / unary mounts (see k8s/deployments/sap-proxy-agent-hub.yaml).
        key_path = base / "client_svr_server.key"
        if not key_path.is_file():
            key_path = base / "server.key"
        cert_path = base / "client_svr_server.crt"
        if not cert_path.is_file():
            cert_path = base / "server.crt"
        for path, label in (
            (ca_path, "ca.crt"),
            (key_path, "client key"),
            (cert_path, "client cert"),
        ):
            if not path.is_file():
                raise RuntimeError(
                    "SAP proxy hub mTLS credentials missing. "
                    f"Expected {label} at {path} under PROXY_AGENT_HUB_MTLS_DIR={mtls_dir!r}. "
                    "Mount Secret sap-proxy-agent-hub-mtls (or set PROXY_AGENT_HUB_*_PEM)."
                )
        return (
            ca_path.read_bytes(),
            key_path.read_bytes(),
            cert_path.read_bytes(),
        )

    ca = (os.environ.get("PROXY_AGENT_HUB_TRUSTED_CA_PEM") or "").strip()
    key = (os.environ.get("PROXY_AGENT_HUB_CLIENT_KEY_PEM") or "").strip()
    cert = (os.environ.get("PROXY_AGENT_HUB_CLIENT_CERT_PEM") or "").strip()
    if ca and key and cert:
        return ca.encode("utf-8"), key.encode("utf-8"), cert.encode("utf-8")

    config_path = _config_json_path()
    if not config_path.is_file():
        raise RuntimeError(
            "SAP proxy hub mTLS credentials missing. "
            "Mount hub mTLS (PROXY_AGENT_HUB_MTLS_DIR), set PROXY_AGENT_HUB_*_PEM env vars, "
            "or provide SAP_PROXY_AGENT_GRPC_CONFIG / dev/config.json."
        )
    with open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    return (
        config["trusted_ca_cert"].encode("utf-8"),
        config["client_svr_key"].encode("utf-8"),
        config["client_svr_cert"].encode("utf-8"),
    )


def get_channel() -> grpc.Channel:
    target = grpc_server_target()
    root, private_key, cert_chain = _load_mtls_material()
    credentials = grpc.ssl_channel_credentials(
        root_certificates=root,
        private_key=private_key,
        certificate_chain=cert_chain,
    )
    peer_name = _tls_peer_server_name()
    channel_options = [
        ("grpc.max_send_message_length", 50 * 1024 * 1024),
        ("grpc.max_receive_message_length", 50 * 1024 * 1024),
    ]
    if peer_name:
        channel_options.append(("grpc.ssl_target_name_override", peer_name))

    logger.debug(
        "Opening gRPC channel to SAP proxy hub at %s (tls peer name=%r)",
        target,
        peer_name or "(default from target)",
    )
    return grpc.secure_channel(
        target,
        credentials,
        options=channel_options,
    )
