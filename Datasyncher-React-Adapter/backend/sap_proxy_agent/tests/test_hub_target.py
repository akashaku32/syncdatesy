import pytest

from sap_proxy_agent.client.hub_target import grpc_server_target


def test_grpc_server_target_from_env(monkeypatch):
    monkeypatch.setenv("PROXY_AGENT_SERVER_HOST", "hub.example.com")
    monkeypatch.setenv("PROXY_AGENT_SERVER_PORT", "50051")
    assert grpc_server_target() == "hub.example.com:50051"


def test_grpc_server_target_missing_raises(monkeypatch):
    monkeypatch.delenv("PROXY_AGENT_SERVER_HOST", raising=False)
    monkeypatch.delenv("PROXY_AGENT_SERVER_PORT", raising=False)
    monkeypatch.delenv("PROXY_AGENT_GRPC_TARGET", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        grpc_server_target()
