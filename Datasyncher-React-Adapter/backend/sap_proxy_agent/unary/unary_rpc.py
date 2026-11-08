"""
SAP proxy agent unary gRPC services (ODATA :50053 equivalent).

- TokenValidationService / ValidateBootstrapToken  → Django ProxyAgent bootstrap (rotate on success)
- GCPTokenBrokerService / GetDownscopedToken      → get_gcp_access_token.py
- Legacy TableStatus* RPCs                        → proxy_agent_legacy_status (ReportProgress parity)

Run from repo root with Django configured:
  set DJANGO_SETTINGS_MODULE=datasyncher.settings.qa
  python backend/sap_proxy_agent/unary/unary_rpc.py

Internal mTLS on UNARY_RPC_PORT (default 50053). Hub Enroll calls this service.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from concurrent import futures
from datetime import datetime, timezone
from pathlib import Path

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.json_format import MessageToDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("unary_rpc")

UNARY_ROOT = Path(__file__).resolve().parent
SAP_AGENT_ROOT = UNARY_ROOT.parent
REPO_ROOT = UNARY_ROOT.parents[2]

for path in (str(SAP_AGENT_ROOT.parent), str(REPO_ROOT), str(SAP_AGENT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.environ.get("DJANGO_SETTINGS_MODULE", "datasyncher.settings.kubernetes"),
)

import django

django.setup()

from apps.elttool.services.proxy_agent_bootstrap import (  # noqa: E402
    validate_and_rotate_bootstrap_token,
)
from apps.elttool.services.proxy_agent_legacy_status import (  # noqa: E402
    apply_legacy_statusdetails,
)

from sap_proxy_agent.generated import service_pb2, service_pb2_grpc  # noqa: E402
from sap_proxy_agent.unary.get_gcp_access_token import generate_downscoped_token  # noqa: E402


def _load_mtls_from_dir(mtls_dir: str) -> grpc.ServerCredentials:
    base = Path(mtls_dir)
    key_path = base / "client_svr_server.key"
    if not key_path.is_file():
        key_path = base / "server.key"
    cert_path = base / "client_svr_server.crt"
    if not cert_path.is_file():
        cert_path = base / "server.crt"
    ca_path = base / "ca.crt"
    for path in (key_path, cert_path, ca_path):
        if not path.is_file():
            raise RuntimeError(
                f"Unary RPC mTLS file missing: {path}. Mount secret at UNARY_RPC_MTLS_DIR."
            )
    return grpc.ssl_server_credentials(
        private_key_certificate_chain_pairs=[
            (key_path.read_bytes(), cert_path.read_bytes())
        ],
        root_certificates=ca_path.read_bytes(),
        require_client_auth=True,
    )


def _load_mtls_credentials() -> grpc.ServerCredentials:
    mtls_dir = os.environ.get("UNARY_RPC_MTLS_DIR", "").strip()
    if mtls_dir:
        return _load_mtls_from_dir(mtls_dir)

    config_path = os.environ.get("UNARY_RPC_MTLS_CONFIG", "").strip()
    if config_path and Path(config_path).is_file():
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        return grpc.ssl_server_credentials(
            private_key_certificate_chain_pairs=[
                (
                    config["client_svr_key"].encode("utf-8"),
                    config["client_svr_cert"].encode("utf-8"),
                )
            ],
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            require_client_auth=True,
        )

    key = (os.environ.get("UNARY_RPC_SERVER_KEY_PEM") or "").strip()
    cert = (os.environ.get("UNARY_RPC_SERVER_CERT_PEM") or "").strip()
    ca = (os.environ.get("UNARY_RPC_TRUSTED_CA_PEM") or "").strip()
    if key and cert and ca:
        return grpc.ssl_server_credentials(
            private_key_certificate_chain_pairs=[(key.encode("utf-8"), cert.encode("utf-8"))],
            root_certificates=ca.encode("utf-8"),
            require_client_auth=True,
        )
    raise RuntimeError(
        "Unary RPC mTLS not configured. Set UNARY_RPC_MTLS_CONFIG or UNARY_RPC_*_PEM env vars."
    )


class TokenValidationService(service_pb2_grpc.TokenValidationServiceServicer):
    def ValidateBootstrapToken(self, request, context):
        cust_id = request.cust_id
        presented = request.bootstrap_token
        validated, new_token = validate_and_rotate_bootstrap_token(cust_id, presented)
        if not validated:
            return service_pb2.TokenValidationResponse(
                cust_id=cust_id,
                token_validated=False,
            )
        return service_pb2.TokenValidationResponse(
            cust_id=cust_id,
            token_validated=True,
            new_bootstrap_token=new_token or "",
        )


class GCPTokenBrokerService(service_pb2_grpc.GCPTokenBrokerServiceServicer):
    def GetDownscopedToken(self, request, context):
        try:
            token, expiry = generate_downscoped_token()
            if not token or not expiry:
                context.abort(
                    grpc.StatusCode.INTERNAL,
                    "GCP token generation returned an invalid result",
                )
            now = datetime.now(timezone.utc)
            if getattr(expiry, "tzinfo", None) is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            expires_in = int((expiry - now).total_seconds())
            if expires_in <= 0:
                context.abort(grpc.StatusCode.INTERNAL, "GCP returned an expired token")
            return service_pb2.DownscopedTokenResponse(
                access_token=token,
                expires_in=expires_in,
                error_message="",
            )
        except Exception:
            logger.exception("GetDownscopedToken failed")
            context.abort(
                grpc.StatusCode.INTERNAL,
                "Unable to generate GCP downscoped token",
            )


def _legacy_status_handler(status_data: dict, context) -> Empty:
    try:
        apply_legacy_statusdetails(status_data)
        return Empty()
    except Exception as exc:
        logger.exception("legacy status update failed: %s", exc)
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))


class TableStatusService(service_pb2_grpc.TableStatusServiceServicer):
    def TableStatusUpdate(self, request, context):
        status_data = MessageToDict(
            request.statusdetails,
            preserving_proto_field_name=True,
        )
        return _legacy_status_handler(status_data, context)


class TableETLStatusService(service_pb2_grpc.TableETLStatusServiceServicer):
    def TableETLStatusUpdate(self, request, context):
        status_data = MessageToDict(
            request.statusdetails,
            preserving_proto_field_name=True,
        )
        return _legacy_status_handler(status_data, context)


class DataTransferLogsService(service_pb2_grpc.DataTransferLogsServiceServicer):
    def DataTransferLogsUpdate(self, request, context):
        status_data = MessageToDict(
            request.statusdetails,
            preserving_proto_field_name=True,
        )
        status_data.setdefault("status", "completed")
        return _legacy_status_handler(status_data, context)


class MigrationStatusLogService(service_pb2_grpc.MigrationStatusLogServiceServicer):
    def MigrationStatusLogUpdate(self, request, context):
        status_data = MessageToDict(
            request.statusdetails,
            preserving_proto_field_name=True,
        )
        status_data.setdefault("status", status_data.get("status") or "completed")
        return _legacy_status_handler(status_data, context)


class RecordTableFailureService(service_pb2_grpc.RecordTableFailureServiceServicer):
    def RecordTableFailureUpdate(self, request, context):
        status_data = MessageToDict(
            request.statusdetails,
            preserving_proto_field_name=True,
        )
        status_data["status"] = "failed"
        return _legacy_status_handler(status_data, context)


def serve() -> None:
    port = int(os.environ.get("UNARY_RPC_PORT", "50053"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    service_pb2_grpc.add_TokenValidationServiceServicer_to_server(
        TokenValidationService(), server
    )
    service_pb2_grpc.add_GCPTokenBrokerServiceServicer_to_server(
        GCPTokenBrokerService(), server
    )
    service_pb2_grpc.add_TableStatusServiceServicer_to_server(
        TableStatusService(), server
    )
    service_pb2_grpc.add_TableETLStatusServiceServicer_to_server(
        TableETLStatusService(), server
    )
    service_pb2_grpc.add_DataTransferLogsServiceServicer_to_server(
        DataTransferLogsService(), server
    )
    service_pb2_grpc.add_MigrationStatusLogServiceServicer_to_server(
        MigrationStatusLogService(), server
    )
    service_pb2_grpc.add_RecordTableFailureServiceServicer_to_server(
        RecordTableFailureService(), server
    )

    credentials = _load_mtls_credentials()
    server.add_secure_port(f"[::]:{port}", credentials)
    server.start()
    logger.info("Unary RPC (bootstrap + GCP + legacy status) listening on :%s", port)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
