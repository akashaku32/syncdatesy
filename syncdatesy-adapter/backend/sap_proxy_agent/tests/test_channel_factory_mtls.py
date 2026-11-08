"""Tests for platform → hub gRPC mTLS loading (PROXY_AGENT_HUB_MTLS_DIR)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sap_proxy_agent.client import channel_factory


def test_load_mtls_from_hub_mtls_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ca.crt").write_bytes(b"CA-PEM\n")
    (tmp_path / "client_svr_server.crt").write_bytes(b"CERT-PEM\n")
    (tmp_path / "client_svr_server.key").write_bytes(b"KEY-PEM\n")

    monkeypatch.setenv("PROXY_AGENT_HUB_MTLS_DIR", str(tmp_path))
    monkeypatch.delenv("PROXY_AGENT_HUB_TRUSTED_CA_PEM", raising=False)
    monkeypatch.delenv("PROXY_AGENT_HUB_CLIENT_KEY_PEM", raising=False)
    monkeypatch.delenv("PROXY_AGENT_HUB_CLIENT_CERT_PEM", raising=False)
    monkeypatch.delenv("SAP_PROXY_AGENT_GRPC_CONFIG", raising=False)

    ca, key, cert = channel_factory._load_mtls_material()
    assert ca == b"CA-PEM\n"
    assert key == b"KEY-PEM\n"
    assert cert == b"CERT-PEM\n"


def test_map_service_fqdn_to_hub_leaf_name() -> None:
    assert (
        channel_factory._map_hub_connect_host_to_tls_peer_name(
            "sap-proxy-agent-hub-svc.default.svc.cluster.local"
        )
        == "sap-proxy-agent-hub"
    )
    assert channel_factory._map_hub_connect_host_to_tls_peer_name("sap-proxy-agent-hub-svc") == (
        "sap-proxy-agent-hub"
    )
    assert (
        channel_factory._map_hub_connect_host_to_tls_peer_name("agent-hub.gcp.datasyncher.com")
        == "agent-hub.gcp.datasyncher.com"
    )


def test_tls_peer_prefers_ssl_target_name_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROXY_AGENT_HUB_SSL_TARGET_NAME", "custom.example.com")
    monkeypatch.delenv("PROXY_AGENT_SERVER_HOST", raising=False)
    # PROXY_AGENT_SERVER_HOST would normally map svc FQDN → sap-proxy-agent-hub
    assert channel_factory._tls_peer_server_name() == "custom.example.com"


def test_tls_peer_maps_k8s_service_fqdn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROXY_AGENT_HUB_SSL_TARGET_NAME", raising=False)
    monkeypatch.setenv(
        "PROXY_AGENT_SERVER_HOST",
        "sap-proxy-agent-hub-svc.default.svc.cluster.local",
    )
    assert channel_factory._tls_peer_server_name() == "sap-proxy-agent-hub"
