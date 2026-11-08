# datasynchor_project/grpc_token_server.py
import secrets
import hashlib
import hmac, json
from datetime import timedelta
from concurrent import futures
import grpc
import sys
import types
import importlib
from pathlib import Path
from get_gcp_access_token import generate_downscoped_token
from datetime import datetime, timezone
from google.protobuf.json_format import MessageToDict
from google.protobuf.empty_pb2 import Empty
import os
import sys
from pathlib import Path

# Get Django project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Add project root to Python path
sys.path.insert(0, str(PROJECT_ROOT))

# Initialize Django
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "datasynchor_project.settings"
)

import django
django.setup()

# Import Django function
from app.views import status_update
# ------------------------------------------------------------------
# Load generated protobuf files as a package.
#
# service_pb2_grpc.py contains:
#     from . import service_pb2 as service__pb2
#
# Since this file is being executed directly, we create a small
# package context so that relative import works.
# ------------------------------------------------------------------

GRPC_PACKAGE = "_local_grpc_files"
GRPC_DIR = Path(__file__).resolve().parent

grpc_package = types.ModuleType(GRPC_PACKAGE)
grpc_package.__path__ = [str(GRPC_DIR)]
grpc_package.__package__ = GRPC_PACKAGE

sys.modules[GRPC_PACKAGE] = grpc_package

service_pb2 = importlib.import_module(
    f"{GRPC_PACKAGE}.service_pb2"
)

service_pb2_grpc = importlib.import_module(
    f"{GRPC_PACKAGE}.service_pb2_grpc"
)
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

credentials = grpc.ssl_server_credentials(
    private_key_certificate_chain_pairs=[
        (
            config["client_svr_key"].encode("utf-8"),
            config["client_svr_cert"].encode("utf-8"),
        )
    ],
    root_certificates=config["trusted_ca_cert"].encode("utf-8"),
    require_client_auth=True,
)

# GRPC_SERVER = "localhost:50051"  # change if needed


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


bootstrap_tokens: dict[str, dict] = {
    "agent-84-cd5833b0923f48a7": {
        "token_hash": hash_token(
            "84-8c6333be8b797b2574e183a533c2450762dcc5e6f80f8fba"
        ),
    }
}

class TokenValidationService(service_pb2_grpc.TokenValidationServiceServicer):

    def ValidateBootstrapToken(self, request, context):

        cust_id = request.cust_id
        presented_token = request.bootstrap_token

        if not cust_id or not presented_token:
            return service_pb2.TokenValidationResponse(
                cust_id=cust_id,
                token_validated=False,
            )

        # ----------------------------------------------------------
        # Find bootstrap token record
        # ----------------------------------------------------------

        record = bootstrap_tokens.get(cust_id)

        if record is None:
            print(
                f"[TokenValidation] unknown cust_id {cust_id}"
            )

            return service_pb2.TokenValidationResponse(
                cust_id=cust_id,
                token_validated=False,
            )

        # ----------------------------------------------------------
        # Validate supplied token
        # ----------------------------------------------------------

        presented_token_hash = hash_token(presented_token)

        if not hmac.compare_digest(
            presented_token_hash,
            record["token_hash"],
        ):
            print(
                f"[TokenValidation] REJECTED for {cust_id}"
            )

            return service_pb2.TokenValidationResponse(
                cust_id=cust_id,
                token_validated=False,
            )

        # ----------------------------------------------------------
        # Generate a new bootstrap token
        # ----------------------------------------------------------

        new_token = secrets.token_urlsafe(32)

        # Store only the hash of the new token.
        record["token_hash"] = hash_token(new_token)

        # ----------------------------------------------------------
        # IMPORTANT:
        # This is the plaintext token that Datasynchor will receive.
        # ----------------------------------------------------------

        print(
            f"[TokenValidation] validated for {cust_id}"
        )

        print(
            f"[TokenValidation] NEW bootstrap token for "
            f"{cust_id}: {new_token}"
        )

        return service_pb2.TokenValidationResponse(
            cust_id=cust_id,
            token_validated=True,
            new_bootstrap_token=new_token,
        )


class GCPTokenBrokerService(
    service_pb2_grpc.GCPTokenBrokerServiceServicer
):
    def GetDownscopedToken(self, request, context):
        try:
            # The request is Empty, so no cust_id or bucket name
            # needs to be passed by the agent.
            #
            # The server uses its configured GCP bucket and prefix.
            token, expiry = generate_downscoped_token()

            if not token or not expiry:
                context.abort(
                    grpc.StatusCode.INTERNAL,
                    "GCP token generation returned an invalid result",
                )

            # Calculate the remaining token lifetime.
            now = datetime.now(timezone.utc)

            # Handle expiry values that may be timezone-naive.
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            expires_in = int((expiry - now).total_seconds())

            if expires_in <= 0:
                context.abort(
                    grpc.StatusCode.INTERNAL,
                    "GCP returned an expired token",
                )

            print(
                "[TokenBroker] Downscoped token generated successfully"
            )

            return service_pb2.DownscopedTokenResponse(
                access_token=token,
                expires_in=expires_in,
                error_message="",
            )

        except Exception:
            import logging

            logging.exception(
                "[TokenBroker] Failed to generate downscoped token"
            )

            context.abort(
                grpc.StatusCode.INTERNAL,
                "Unable to generate GCP downscoped token",
            )

class TableStatusService(
    service_pb2_grpc.TableStatusServiceServicer
):

    def TableStatusUpdate(self, request, context):

        try:
            # Convert protobuf Struct into Python dictionary
            status_data = MessageToDict(
                request.statusdetails,
                preserving_proto_field_name=True
            )

            print(
                "[table Status] Received status update:",
                status_data
            )

            # Call Django business logic
            status_update(status_data)

            print(
                "[table Status] Status updated successfully"
            )

            return Empty()

        except Exception as e:

            context.abort(
                grpc.StatusCode.INTERNAL,
                "Failed to update table  status"
            )

class TableETLStatusService(
    service_pb2_grpc.TableETLStatusServiceServicer
):

    def TableETLStatusUpdate(self, request, context):

        try:
            # Convert protobuf Struct into Python dictionary
            status_data = MessageToDict(
                request.statusdetails,
                preserving_proto_field_name=True
            )

            print(
                "[table_etl_Status] Received status update:",
                status_data
            )

            # Call Django business logic
            status_update(status_data)

            print(
                "[table_etl_Status] Status updated successfully"
            )

            return Empty()

        except Exception as e:

            context.abort(
                grpc.StatusCode.INTERNAL,
                "Failed to update table status"
            )

class DataTransferLogsService(
    service_pb2_grpc.DataTransferLogsServiceServicer
):

    def DataTransferLogsUpdate(self, request, context):

        try:
            # Convert protobuf Struct into Python dictionary
            status_data = MessageToDict(
                request.statusdetails,
                preserving_proto_field_name=True
            )

            print(
                "[DataTransferLogs] Received status update:",
                status_data
            )

            # Call Django business logic
            status_update(status_data)

            print(
                "[DataTransferLogs] Status updated successfully"
            )

            return Empty()

        except Exception as e:

            context.abort(
                grpc.StatusCode.INTERNAL,
                "Failed to update DataTransferLogs status"
            )

class MigrationStatusLogService(
    service_pb2_grpc.MigrationStatusLogServiceServicer
):

    def MigrationStatusLogUpdate(self, request, context):

        try:
            # Convert protobuf Struct into Python dictionary
            status_data = MessageToDict(
                request.statusdetails,
                preserving_proto_field_name=True
            )

            print(
                "[MigrationStatusLog] Received status update:",
                status_data
            )

            # Call Django business logic
            status_update(status_data)

            print(
                "[MigrationStatusLog] Status updated successfully"
            )

            return Empty()

        except Exception as e:

            context.abort(
                grpc.StatusCode.INTERNAL,
                "Failed to update MigrationStatusLog status"
            )


class RecordTableFailureService(
    service_pb2_grpc.RecordTableFailureServiceServicer
):

    def RecordTableFailureUpdate(self, request, context):

        try:
            # Convert protobuf Struct into Python dictionary
            status_data = MessageToDict(
                request.statusdetails,
                preserving_proto_field_name=True
            )

            print(
                "[RecordTableFailureLog] Received status update:",
                status_data
            )

            # Call Django business logic
            status_update(status_data)

            print(
                "[RecordTableFailure] Status updated successfully"
            )

            return Empty()

        except Exception as e:

            context.abort(
                grpc.StatusCode.INTERNAL,
                "Failed to update RecordTableFailure status"
            )

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    service_pb2_grpc.add_TokenValidationServiceServicer_to_server(
        TokenValidationService(), server
    )
    # This is an internal, service-to-service port — not exposed publicly.
    # Restrict at the network layer (firewall/security group) to only the
    # agent-server's host, same principle as the DB access discussion.


    # New GCP downscoped-token service.
    service_pb2_grpc.add_GCPTokenBrokerServiceServicer_to_server(
        GCPTokenBrokerService(), server
    )

    # table status update service
    service_pb2_grpc.add_TableStatusServiceServicer_to_server(
        TableStatusService(), server
    )
    # table etl status update service
    service_pb2_grpc.add_TableETLStatusServiceServicer_to_server(
        TableETLStatusService(), server
    )
    #  status update service
    service_pb2_grpc.add_DataTransferLogsServiceServicer_to_server(
        DataTransferLogsService(), server
    )

    #  migration status update service
    service_pb2_grpc.add_MigrationStatusLogServiceServicer_to_server(
        MigrationStatusLogService(), server
    )

    #  migration status update service
    service_pb2_grpc.add_RecordTableFailureServiceServicer_to_server(
        RecordTableFailureService(), server
    )

    server.add_secure_port(
        "[::]:50053",
        credentials
    )
    server.start()
    print("Token validation gRPC server running on port 50053")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()