"""
gRPC ReportProgress servicer — relays to Django via progress_relay.
Register on the SAP proxy hub alongside load RPCs (ODATA server.py).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent import futures

import grpc
from google.protobuf import json_format

logger = logging.getLogger(__name__)

_CN_RE = re.compile(r"CN=([^,/]+)")


def _agent_uid_from_context(context: grpc.ServicerContext) -> str | None:
    for key, value in context.invocation_metadata():
        if key.lower() in ("x-forwarded-client-cert-cn", "x-client-cert-cn"):
            match = _CN_RE.search(value or "")
            if match:
                return match.group(1).strip()
    peer = context.peer()
    if peer:
        return None
    return None


def _progress_body_from_request(request) -> dict:
    body = {
        "event": request.event,
        "migration_session_id": int(request.migration_session_id),
        "job_name": request.job_name,
        "connection_id": int(request.connection_id),
    }
    if request.table_name:
        body["table_name"] = request.table_name
    if request.idempotency_key:
        body["idempotency_key"] = request.idempotency_key
    if request.payload:
        payload = json_format.MessageToDict(request.payload)
        if isinstance(payload, dict):
            body["payload"] = payload
    return body


def build_progress_servicer(service_pb2, service_pb2_grpc, relay_fn):
    """Factory so hub image can import generated protos from its own path."""

    class DatasynchorProgressServicer(service_pb2_grpc.DatasynchorProgressServicer):
        def ReportProgress(self, request, context):
            from sap_proxy_agent.hub.progress_relay import relay_progress_to_django

            agent_uid = (request.cust_id or "").strip() or _agent_uid_from_context(context)
            body = _progress_body_from_request(request)
            raw_body = json.dumps(body, separators=(",", ":")).encode("utf-8")

            status_code, data = relay_progress_to_django(
                agent_uid=agent_uid or request.cust_id,
                body=body,
                raw_body=raw_body,
                idempotency_key=request.idempotency_key or None,
            )

            if status_code >= 400 or not data.get("accepted"):
                msg = str(data.get("error") or data.get("message") or "rejected")
                return service_pb2.ProgressAck(
                    accepted=False,
                    message=msg,
                )

            return service_pb2.ProgressAck(
                accepted=True,
                overall_status=str(data.get("overall_status") or ""),
                tables_remaining=int(data.get("tables_remaining") or 0),
                message=str(data.get("message") or ""),
            )

    return DatasynchorProgressServicer()


def serve_progress_only(*, port: int, mtls_credentials, grpc_package_path: str) -> None:
    """
    Standalone progress server (dev/smoke). Production hub should register the same
    servicer on the load server port 50051.
    """
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, grpc_package_path)
    service_pb2 = importlib.import_module("service_pb2")
    service_pb2_grpc = importlib.import_module("service_pb2_grpc")

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    servicer = build_progress_servicer(service_pb2, service_pb2_grpc, None)
    service_pb2_grpc.add_DatasynchorProgressServicer_to_server(servicer, server)
    server.add_secure_port(f"[::]:{port}", mtls_credentials)
    server.start()
    logger.info("ReportProgress gRPC listening on %s", port)
    server.wait_for_termination()
