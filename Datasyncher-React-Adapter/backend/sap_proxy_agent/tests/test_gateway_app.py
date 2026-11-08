"""Unit tests for HTTPS progress gateway (no Django)."""

import sys
from pathlib import Path

_GATEWAY_ROOT = Path(__file__).resolve().parents[1] / "gateway"
if str(_GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_ROOT))

from app import _parse_agent_uid_from_dn, app  # noqa: E402
from fastapi.testclient import TestClient


def test_parse_cn():
    assert _parse_agent_uid_from_dn("CN=agent-1-abc,O=Datasyncher") == "agent-1-abc"
    assert _parse_agent_uid_from_dn("CN=agent-9-deadbeef") == "agent-9-deadbeef"
    assert _parse_agent_uid_from_dn("") is None


def test_healthz():
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
