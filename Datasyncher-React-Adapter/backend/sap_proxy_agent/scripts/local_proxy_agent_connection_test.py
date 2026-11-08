"""
Call TestSapConnection against a local ODATA hub (platform mTLS client).

Requires:
  - ODATA server/server.py on PROXY_AGENT_SERVER_HOST:PORT (default localhost:50051)
  - Agent or sap_proxy_agent/scripts/local_mock_proxy_agent.py connected with matching cust_id

Optional env:
  LOCAL_HUB_MTLS_CONFIG — grpc_files config.json (client_svr_* for platform caller)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent
_BACKEND_ROOT = _SCRIPTS_ROOT.parent.parent
_MIGRATION_ROOT = _BACKEND_ROOT / "migration-function"
for _p in (_BACKEND_ROOT, _MIGRATION_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _apply_hub_mtls_from_config(config_path: Path) -> None:
    with open(config_path, encoding="utf-8") as handle:
        cfg = json.load(handle)
    os.environ.setdefault("PROXY_AGENT_HUB_TRUSTED_CA_PEM", cfg["trusted_ca_cert"])
    os.environ.setdefault("PROXY_AGENT_HUB_CLIENT_KEY_PEM", cfg["client_svr_key"])
    os.environ.setdefault("PROXY_AGENT_HUB_CLIENT_CERT_PEM", cfg["client_svr_cert"])


def main() -> int:
    os.environ.setdefault("PROXY_AGENT_SERVER_HOST", "localhost")
    os.environ.setdefault("PROXY_AGENT_SERVER_PORT", "50051")

    mtls_path = os.environ.get("LOCAL_HUB_MTLS_CONFIG", "").strip()
    if not mtls_path:
        default = (
            _BACKEND_ROOT.parents[1]
            / "odata-agent"
            / "datasynchor"
            / "datasynchor_project"
            / "app"
            / "grpc_files"
            / "config.json"
        )
        mtls_path = str(default) if default.is_file() else ""
    if mtls_path and Path(mtls_path).is_file():
        _apply_hub_mtls_from_config(Path(mtls_path))

    cust_id = os.environ.get("LOCAL_PROXY_AGENT_ID", "C013")
    from sap_proxy_agent.generated import service_pb2
    from sap_proxy_agent.client.dataynchor_client import test_sap_connection

    src = service_pb2.SrcConnConfig(
        host=os.environ.get("LOCAL_SAP_HOST", "example.com"),
        port=int(os.environ.get("LOCAL_SAP_PORT", "443")),
        username=os.environ.get("LOCAL_SAP_USER", "user"),
        password=os.environ.get("LOCAL_SAP_PASSWORD", "pass"),
    )
    try:
        result = test_sap_connection("sap-odata", cust_id, src)
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    print("OK:", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
