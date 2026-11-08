"""
Minimal SAP proxy agent for local hub smoke tests.

Registers on the ODATA hub (AgentService.Connect) and answers TestSapConnection
with stub connectivity flags — no SAP/OData/ADLS dependencies.

Usage (PYTHONPATH=backend;backend/migration-function):
  set LOCAL_AGENT_CONFIG=path/to/ODATA/client/config.json
  python backend/sap_proxy_agent/scripts/local_mock_proxy_agent.py

cust_id in config.json agent_id must match proxy_agent / TestSapConnection cust_id.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import grpc
from google.protobuf.struct_pb2 import Struct
from grpc import aio

_SCRIPTS_ROOT = Path(__file__).resolve().parent
_SAP_AGENT_ROOT = _SCRIPTS_ROOT.parent
_BACKEND_ROOT = _SAP_AGENT_ROOT.parent
_MIGRATION_ROOT = _BACKEND_ROOT / "migration-function"
for _p in (_BACKEND_ROOT, _MIGRATION_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sap_proxy_agent.generated import service_pb2, service_pb2_grpc  # noqa: E402


def _load_agent_config() -> dict:
    path = os.environ.get("LOCAL_AGENT_CONFIG", "").strip()
    if not path:
        default = (
            _BACKEND_ROOT.parents[1]
            / "odata-agent"
            / "client"
            / "config.json"
        )
        path = str(default) if default.is_file() else ""
    if not path or not Path(path).is_file():
        raise SystemExit(
            "Set LOCAL_AGENT_CONFIG to ODATA client config.json (agent mTLS + agent_id)."
        )
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


async def _run() -> None:
    cfg = _load_agent_config()
    cust_id = cfg["agent_id"]
    host = cfg.get("server_host", "localhost")
    port = int(cfg.get("server_port") or 50051)
    target = f"{host}:{port}"

    credentials = grpc.ssl_channel_credentials(
        root_certificates=cfg["trusted_ca_cert"].encode("utf-8"),
        private_key=cfg["client_private_key"].encode("utf-8"),
        certificate_chain=cfg["client_cert"].encode("utf-8"),
    )
    send_queue: asyncio.Queue = asyncio.Queue()

    async def request_generator():
        yield service_pb2.AgentMessage(cust_id=cust_id, register=True)
        while True:
            yield await send_queue.get()

    async def handle_server_message(msg: service_pb2.ServerMessage) -> None:
        if msg.test_sap_connection:
            payload = {
                "check_dns": True,
                "check_tcp": True,
                "check_ssl": True,
                "check_credentials": True,
                "mock_agent": True,
            }
            conn_struct = Struct()
            conn_struct.update(payload)
            await send_queue.put(
                service_pb2.AgentMessage(
                    cust_id=cust_id,
                    conn_details=service_pb2.ConnectionDetails(
                        request_id=msg.request_id,
                        conn_details_json=conn_struct,
                    ),
                )
            )
            return
        if msg.request_id:
            await send_queue.put(
                service_pb2.AgentMessage(
                    cust_id=cust_id,
                    request_id=msg.request_id,
                    error_msg="local_mock_proxy_agent: unhandled request type",
                )
            )

    print(f"[mock-agent] connecting to {target} as cust_id={cust_id!r}")
    async with aio.secure_channel(target, credentials) as channel:
        stub = service_pb2_grpc.AgentServiceStub(channel)
        responses = stub.Connect(request_generator())
        async for res in responses:
            await handle_server_message(res)


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
