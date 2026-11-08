"""
Async gRPC hub (ODATA server.py): agent Connect stream + Datasynchor loads + bootstrap Enroll.

Registers DatasynchorProgress (ReportProgress → Django relay) on the same :50051 server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

import grpc

from sap_proxy_agent.generated import service_pb2, service_pb2_grpc
from sap_proxy_agent.hub.cert_authority import (
    CertificateAuthority,
    InvalidCSRError,
    get_peer_common_name,
)
from sap_proxy_agent.hub.grpc_progress import build_progress_servicer
from sap_proxy_agent.hub.mtls_paths import (
    hub_ca_cert_path,
    hub_ca_key_path,
    hub_server_cert_path,
    hub_server_key_path,
)

logger = logging.getLogger("sap_proxy_agent_hub.grpc")

# Loaded once at startup - never re-read per request.
ca_authority: CertificateAuthority | None = None
 
pending_requests = {} 

class StreamRegistry():
    """One active Connect stream per cust_id. Replace on reconnect; unregister only for that queue."""

    def __init__(self):
        self.streams = {}

    def register(self, cust_id, channel):
        """Take over custody_id if a previous stream died without cleanup (network drop)."""
        prev = self.streams.get(cust_id)
        self.streams[cust_id] = channel
        if prev is not None and prev is not channel:
            logger.info(
                "Replacing stale Connect for cust_id=%s (reconnect after disconnect)",
                cust_id,
            )
        return True

    def unregister(self, cust_id, channel):
        if self.streams.get(cust_id) is channel:
            self.streams.pop(cust_id, None)
    
    async def send(self, cust_id, msg):
        channel = self.streams.get(cust_id)
        if channel:
            await channel.put(msg)
            return True
        return False

registry = StreamRegistry()

class AgentService(service_pb2_grpc.AgentServiceServicer):

    async def Connect(self, request_iterator, context):
        logger.info("Agent Connect stream opened")

        channel_queue = asyncio.Queue()
        cust_id =None

        async def reader():
            nonlocal cust_id
            try:
                # Read messages from client
                async for msg in request_iterator:
                    if msg.register:
                        logger.info("Agent registered cust_id=%s", msg.cust_id)
                        registry.register(msg.cust_id, channel_queue)

                        cust_id = msg.cust_id
                        # optional ACK
                    # handle response from agent
                    if msg.HasField("error_msg"):
                        request_id = msg.request_id
                        future = pending_requests.pop(request_id, None)
                        if future and not future.done():
                            future.set_exception(Exception(msg.error_msg))
                    elif msg.HasField("metadata_json"):
                        request_id = msg.metadata_json.request_id
                        future = pending_requests.pop(request_id, None)
                        if future and not future.done():
                            future.set_result(msg.metadata_json.metadata_json)
                    elif msg.HasField("alldata"):
                        request_id = msg.alldata.request_id

                        future = pending_requests.pop(request_id, None)
                        if future and not future.done():
                            future.set_result(msg.alldata.alldata)

                    elif msg.HasField("src_json"):
                        request_id = msg.src_json.request_id

                        future = pending_requests.pop(request_id, None)
                        if future and not future.done():
                            future.set_result(msg.src_json.src_details_json)
                    elif msg.HasField("conn_details"):
                        request_id = msg.conn_details.request_id

                        future = pending_requests.pop(request_id, None)
                        if future and not future.done():
                            future.set_result(msg.conn_details.conn_details_json)

            except Exception as e:
                # context.abort() raises AbortError in the reader; do not log as ERROR.
                if e.__class__.__name__ == "AbortError":
                    logger.debug("Connect stream aborted (duplicate cust_id or server reject): %s", e)
                    return
                logger.exception("Connect reader error: %s", e)
                
        reader_task = asyncio.create_task(reader())


        # Writer (outgoing messages)
        try:
            while True:
                msg = await channel_queue.get()
                yield msg

        except asyncio.CancelledError:
            pass

        finally:
            if cust_id:
                registry.unregister(cust_id, channel_queue)

            reader_task.cancel()
            await reader_task
                

        # If client disconnects, stream ends here
        logger.info("Agent Connect stream closed cust_id=%s", cust_id)

    async def RenewCertificate(self, request, context):
        """
        Signs a CSR submitted by an agent whose current mTLS certificate is
        nearing expiry. This RPC is unary and independent of the Connect()
        stream - grpc.aio schedules it as its own coroutine, so it runs
        concurrently with every other agent's active stream.
 
        The one thing that could block the whole server is the actual RSA
        signing (synchronous CPU work) - that's why it's offloaded to
        asyncio.to_thread() below rather than called inline.
        """
        cust_id = request.cust_id
        csr_pem = request.csr_pem
 
        # Confirm the caller is who they claim to be, using the identity
        # already established by the current (still-valid) mTLS handshake -
        # NOT the cust_id field alone, which is just client-supplied data.
        peer_cn = get_peer_common_name(context)
        if peer_cn is None or peer_cn != cust_id:
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(
                "Authenticated certificate identity does not match requested cust_id"
            )
            return service_pb2.CertRenewalResponse()
 
        try:
            # CPU-bound signing off the event loop - keeps all other
            # connected agents' Connect() streams responsive meanwhile.
            new_cert_pem = await asyncio.to_thread(
                ca_authority.sign_csr, csr_pem, cust_id
            )
        except InvalidCSRError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
            return service_pb2.CertRenewalResponse()
        except Exception as e:
            logger.exception("[RenewCertificate] unexpected signing error")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Certificate signing failed")
            return service_pb2.CertRenewalResponse()
 
        logger.info("[RenewCertificate] issued new certificate for %s", cust_id)
 
        return service_pb2.CertRenewalResponse(
            cert_pem=new_cert_pem,
            # Only needed if you rotate the CA itself; omit otherwise so
            # agents keep trusting the CA they already have.
            # ca_cert_pem=ca_authority.ca_cert_pem,
        )


token_validation_channel = None
token_validation_stub = None

class BootstrapService(service_pb2_grpc.BootstrapServiceServicer):
    """
    Runs on a separate, no-client-auth port. Used only when an agent's
    operational mTLS certificate has already expired (or was never issued),
    so the normal Connect()/RenewCertificate path is unreachable. Auth here
    comes entirely from the long-lived bootstrap_token, not from a client
    certificate.
    """


    async def Enroll(self, request, context):
        cust_id = request.cust_id
        token = request.bootstrap_token

        if token_validation_stub is None:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(
                "Bootstrap token validation is not configured (TOKEN_VALIDATION_GRPC_TARGET)."
            )
            return service_pb2.EnrollResponse()

        try:
            validation_response = await token_validation_stub.ValidateBootstrapToken(
                service_pb2.TokenValidationRequest(
                    cust_id=cust_id,
                    bootstrap_token=token,
                ),
                timeout=10,
            )
        except grpc.aio.AioRpcError as e:
            logger.error(
                "[Enroll] token validation RPC failed: %s %s", e.code(), e.details()
            )
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Could not reach token validation service")
            return service_pb2.EnrollResponse()

        if not validation_response.token_validated:
            logger.warning("[Enroll] REJECTED bootstrap attempt for %s", cust_id)
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details("Invalid or expired bootstrap credential")
            return service_pb2.EnrollResponse()

        try:
            new_cert_pem = await asyncio.to_thread(
                ca_authority.sign_csr, request.csr_pem, cust_id
            )
        except InvalidCSRError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
            return service_pb2.EnrollResponse()
        except Exception as e:
            logger.exception("[Enroll] unexpected signing error")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Certificate signing failed")
            return service_pb2.EnrollResponse()

        logger.info("[Enroll] issued cert via bootstrap for %s", cust_id)
        return service_pb2.EnrollResponse(
            cert_pem=new_cert_pem,
            new_bootstrap_token=validation_response.new_bootstrap_token,
        )


class Datasynchorservice(service_pb2_grpc.DatasynchorServicer):

    async def Metadata(self, request, context):

        """
        this function is for fetching the metadata from the agent 
        """
        cust_id = request.cust_id
        url = request.metadata_url
        src_conf= request.src_conf
        src_type = request.src_type

        # 1. generate request_id
        request_id = str(uuid.uuid4())

        # 2. create future
        future = asyncio.Future()
        pending_requests[request_id] = future

        # 3. send to agent
        server_msg = service_pb2.ServerMessage(
            request_id=request_id,
            metadata_url=url,
            src_conf=src_conf,
            src_type = src_type,
        )

        sent = await registry.send(cust_id, server_msg)
        if not sent:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"Agent for customer {cust_id} is not connected")
            return service_pb2.Metadata_Response()
         
        try:
            result = await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            context.set_details("Timed out waiting for agent response")
            return service_pb2.Metadata_Response()
        except Exception as e:
            # Don't fail the RPC — return a normal OK response with error_msg set
            return service_pb2.Metadata_Response(error_msg=str(e))

        return service_pb2.Metadata_Response(metadata_json=result)
    
    
    async def StartDataLoad(self, request, context):

        cust_id = request.cust_id
        src_conn_config = request.src_conf
        destination_type = request.destination_type
        destination_config = request.destination_config
        details_for_sts_update = request.details_for_sts_update

        if destination_type == "adls":
            server_msg = service_pb2.ServerMessage(
                sources = request.sources,
                src_conf = src_conn_config,
                destination_type = destination_type,
                destination_config = destination_config,
                details_for_sts_update = details_for_sts_update,
            )
        elif destination_type == "snowflake":
            server_msg = service_pb2.ServerMessage(
                snowflake_sources = request.snowflake_sources,
                src_conf = src_conn_config,
                destination_type = destination_type,
                destination_config = destination_config,
                details_for_sts_update = details_for_sts_update,
            )
        else:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"Unsupported destination_type={destination_type!r}")
            return service_pb2.DataLoadResponse(accepted=False)

        sent = await registry.send(cust_id, server_msg)
        if not sent:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"Agent for customer {cust_id} is not connected")
            return service_pb2.DataLoadResponse(accepted=False)

        return service_pb2.DataLoadResponse(accepted=True)
        

    async def AllDataSource(self, request, context):

        cust_id = request.cust_id
        data_url = request.alldata_url
        src_conn_config = request.src_conf
        src_type = request.src_type

        # 1. generate request_id
        request_id = str(uuid.uuid4())

        server_msg = service_pb2.ServerMessage(
            request_id=request_id,
            alldata_url=data_url,
            src_conf = src_conn_config,
            src_type=src_type
        )

        # 2. create future
        future = asyncio.Future()
        pending_requests[request_id] = future

        sent = await registry.send(cust_id, server_msg)
        if not sent:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"Agent for customer {cust_id} is not connected")
            return service_pb2.AllDataResponse()
 
        # 4. wait for agent response
        try:
            result = await asyncio.wait_for(future, timeout=30)
 
        except asyncio.TimeoutError:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            context.set_details("Timed out waiting for agent response")
            return service_pb2.AllDataResponse()
        except Exception as e:
            return service_pb2.AllDataResponse(error_msg=str(e))
 
        return service_pb2.AllDataResponse(
            alldata_json=result
        )

    async def GetSourceDetail(self,request,context):

        cust_id = request.cust_id
        service_name = request.service_name
        src_conn_config = request.src_conf
        src_type = request.src_type

        # 1. generate request_id
        request_id = str(uuid.uuid4())

        server_msg = service_pb2.ServerMessage(
            request_id=request_id,
            service_name = service_name,
            src_conf = src_conn_config,
            src_type = src_type
        )

        # 2. create future
        future = asyncio.Future()
        pending_requests[request_id] = future

        sent = await registry.send(cust_id, server_msg)
        if not sent:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"Agent for customer {cust_id} is not connected")
            return service_pb2.SourceDetailResponse(
                error_msg="agent not connected"
            )
 
        # 4. wait for agent response
        try:
            result = await asyncio.wait_for(future, timeout=30)
 
        except asyncio.TimeoutError:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            return service_pb2.SourceDetailResponse(
                error_msg="timeout error"
            )
        except Exception as e:
            return service_pb2.SourceDetailResponse(error_msg=str(e))
 
        return service_pb2.SourceDetailResponse(
            src_details_json=result
        )

    async def TestSapConnection(self, request, context):

        cust_id = request.cust_id
        src_conn_config = request.src_conf
        src_type = request.src_type

        # 1. generate request_id
        request_id = str(uuid.uuid4())

        server_msg = service_pb2.ServerMessage(
            request_id=request_id,
            src_conf = src_conn_config,
            test_sap_connection = True,
            src_type = src_type
        )

 
        # 2. create future
        future = asyncio.Future()
        pending_requests[request_id] = future

        sent = await registry.send(cust_id, server_msg)
        if not sent:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"Agent for customer {cust_id} is not connected")
            return service_pb2.ConnectionResponse()

        # 4. wait for agent response
        try:
            result = await asyncio.wait_for(future, timeout=30)
 
        except asyncio.TimeoutError:
            pending_requests.pop(request_id, None)
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            context.set_details("Timed out waiting for agent response")
            return service_pb2.ConnectionResponse()
        except Exception as e:
            return service_pb2.ConnectionResponse(error_msg=str(e))
 
        return service_pb2.ConnectionResponse(
            conn_details_json=result
        )


    async def TriggerDelta(self, request, context):

        cust_id = request.cust_id
        sources = request.sources
        src_conn_config = request.src_conf
        destination_type = request.destination_type
        destination_config = request.destination_config
        details_for_sts_update = request.details_for_sts_update

        server_msg = service_pb2.ServerMessage(
            sources = sources,
            src_conf = src_conn_config,
            destination_type = destination_type,
            destination_config = destination_config,
            load_delta = True,
            details_for_sts_update=details_for_sts_update
        )

        sent = await registry.send(cust_id, server_msg)
        if not sent:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"Agent for customer {cust_id} is not connected")
            return service_pb2.TriggerDeltaResponse(accepted=False)
 
        return service_pb2.TriggerDeltaResponse(accepted=True)
# Server startup
# ---------------------------
async def serve() -> None:
    global ca_authority, token_validation_channel, token_validation_stub

    grpc_port = int(os.getenv("GRPC_HUB_PORT", "50051"))
    bootstrap_port = int(os.getenv("GRPC_BOOTSTRAP_PORT", "50052"))

    server_cert_path = hub_server_cert_path()
    server_key_path = hub_server_key_path()
    ca_cert_path = hub_ca_cert_path()
    ca_key_path = hub_ca_key_path()

    for path in (server_cert_path, server_key_path, ca_cert_path, ca_key_path):
        if not path.is_file():
            raise FileNotFoundError(
                f"Hub mTLS file missing: {path}. Mount secret at HUB_MTLS_DIR "
                f"({hub_ca_cert_path().parent})."
            )

    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
            ("grpc.http2.min_ping_interval_without_data_ms", 40000),
            ("grpc.http2.max_pings_without_data", 0),
            ("grpc.http2.max_ping_strikes", 0),
        ]
    )
    service_pb2_grpc.add_AgentServiceServicer_to_server(AgentService(), server)
    service_pb2_grpc.add_DatasynchorServicer_to_server(Datasynchorservice(), server)
    service_pb2_grpc.add_BootstrapServiceServicer_to_server(BootstrapService(), server)

    progress_servicer = build_progress_servicer(service_pb2, service_pb2_grpc, None)
    service_pb2_grpc.add_DatasynchorProgressServicer_to_server(
        progress_servicer,
        server,
    )

    server_cert = server_cert_path.read_bytes()
    server_key = server_key_path.read_bytes()
    ca_cert = ca_cert_path.read_bytes()

    server_credentials = grpc.ssl_server_credentials(
        private_key_certificate_chain_pairs=[(server_key, server_cert)],
        root_certificates=ca_cert,
        require_client_auth=True,
    )

    token_validation_target = (
        os.getenv("TOKEN_VALIDATION_GRPC_TARGET") or ""
    ).strip()
    if token_validation_target:
        token_validation_credentials = grpc.ssl_channel_credentials(
            root_certificates=ca_cert,
            private_key=server_key,
            certificate_chain=server_cert,
        )
        token_validation_channel = grpc.aio.secure_channel(
            token_validation_target,
            token_validation_credentials,
        )
        token_validation_stub = service_pb2_grpc.TokenValidationServiceStub(
            token_validation_channel
        )
        logger.info("Bootstrap validation via gRPC at %s", token_validation_target)
    else:
        logger.warning(
            "TOKEN_VALIDATION_GRPC_TARGET unset; Enroll will reject until unary is wired."
        )

    server.add_secure_port(f"[::]:{grpc_port}", server_credentials)

    bootstrap_credentials = grpc.ssl_server_credentials(
        private_key_certificate_chain_pairs=[(server_key, server_cert)],
        root_certificates=ca_cert,
        require_client_auth=False,
    )
    server.add_secure_port(f"[::]:{bootstrap_port}", bootstrap_credentials)

    ca_authority = CertificateAuthority(
        ca_key_path=str(ca_key_path),
        ca_cert_path=str(ca_cert_path),
        validity_days=int(os.getenv("HUB_CLIENT_CERT_VALIDITY_DAYS", "90")),
    )

    await server.start()
    logger.info(
        "SAP proxy hub gRPC listening on %s (bootstrap %s)",
        grpc_port,
        bootstrap_port,
    )
    await server.wait_for_termination()
