"""Resolve hub mTLS material paths (k8s mount or local dev)."""

from __future__ import annotations

import os
from pathlib import Path


def hub_mtls_dir() -> Path:
    raw = (os.getenv("HUB_MTLS_DIR") or "/etc/sap-proxy-agent-hub/mtls").strip()
    return Path(raw)


def _first_existing(base: Path, names: tuple[str, ...]) -> Path:
    for name in names:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return base / names[0]


def hub_server_cert_path() -> Path:
    base = hub_mtls_dir()
    return _first_existing(base, ("client_svr_server.crt", "server.crt"))


def hub_server_key_path() -> Path:
    base = hub_mtls_dir()
    return _first_existing(base, ("client_svr_server.key", "server.key"))


def hub_ca_cert_path() -> Path:
    return hub_mtls_dir() / "ca.crt"


def hub_ca_key_path() -> Path:
    return hub_mtls_dir() / "ca.key"
