"""Resolve SAP proxy agent gRPC hub host:port for migration-function clients."""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULT_PORT = "50051"


def grpc_server_target() -> str:
    """
    Prefer PROXY_AGENT_SERVER_HOST + PROXY_AGENT_SERVER_PORT (batch / Celery env).
    Fall back to local config.json for dev-only runs.
    """
    host = (os.environ.get("PROXY_AGENT_SERVER_HOST") or "").strip()
    port = (os.environ.get("PROXY_AGENT_SERVER_PORT") or _DEFAULT_PORT).strip()
    if host:
        return f"{host}:{port}"

    config_path = Path(__file__).resolve().parent / "config.json"
    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as handle:
                config = json.load(handle)
            cfg_host = (config.get("server_host") or "").strip()
            cfg_port = str(config.get("server_port") or _DEFAULT_PORT).strip()
            if cfg_host:
                return f"{cfg_host}:{cfg_port}"
        except (OSError, json.JSONDecodeError):
            pass

    legacy = (os.environ.get("PROXY_AGENT_GRPC_TARGET") or "").strip()
    if legacy:
        return legacy

    raise RuntimeError(
        "SAP proxy gRPC hub target is not configured. "
        "Set PROXY_AGENT_SERVER_HOST and PROXY_AGENT_SERVER_PORT."
    )
