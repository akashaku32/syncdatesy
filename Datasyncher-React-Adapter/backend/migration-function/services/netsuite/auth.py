"""NetSuite connection validation."""

from __future__ import annotations

from typing import Any, Dict

from models.netsuite_models import NetSuiteAuthorizationRequest
from services.netsuite.config import normalize_config, validate_for_method
from services.netsuite.transports.factory import build_transport


def validate_netsuite_authorization(request: NetSuiteAuthorizationRequest) -> Dict[str, Any]:
    raw = request.NetSuite or {}
    config = normalize_config(raw)
    validate_for_method(config)
    return config


def test_netsuite_connection(config: Dict[str, Any]) -> Dict[str, str]:
    transport = build_transport(config)
    try:
        rows = transport.run_query("SELECT 1 AS ok", limit=1)
        if not rows:
            return {"probe": "empty"}
        return {"probe": "ok", "method": config["connection_method"]}
    finally:
        transport.close()
