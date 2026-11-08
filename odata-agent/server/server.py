import grpc
import service_pb2
import service_pb2_grpc
import asyncio,uuid, hmac, hashlib
from datetime import datetime, timedelta
from cert_authority import CertificateAuthority, InvalidCSRError, get_peer_common_name
import secrets
# Loaded once at startup - never re-read per request.
ca_authority: CertificateAuthority | None = None
 
pending_requests = {} 

class StreamRegistry():
    def __init__(self):
        self.streams={}

    def register(self,cust_id,channel):
        if cust_id in self.streams:
            return False

        self.streams[cust_id] = channel
        return True
        
    def unregister(self,cust_id):
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
        print("Client connected")

        channel_queue = asyncio.Queue()
        cust_id =None

        async def reader():
            nonlocal cust_id
            try:
                # Read messages from client
                async for msg in request_iterator:
                    if msg.register:
                        print(msg)
                        registered = registry.register(msg.cust_id, channel_queue)

                        if not registered:
                            await context.abort(
                                grpc.StatusCode.ALREADY_EXISTS,
                                f"Another agent with customer id '{msg.cust_id}' is already connected."
                            )

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
                print("reader error:",e)
                
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
                registry.unregister(cust_id)

            reader_task.cancel()
            await reader_task
                

        # If client disconnects, stream ends here
        print("Client disconnected")

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
            print(f"[RenewCertificate] unexpected signing error: {repr(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Certificate signing failed")
            return service_pb2.CertRenewalResponse()
 
        print(f"[RenewCertificate] issued new certificate for {cust_id}")
 
        return service_pb2.CertRenewalResponse(
            cert_pem=new_cert_pem,
            # Only needed if you rotate the CA itself; omit otherwise so
            # agents keep trusting the CA they already have.
            # ca_cert_pem=ca_authority.ca_cert_pem,
        )


TOKEN_VALIDATION_ADDR = "localhost:50053"  # datasynchor's internal grpc port

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

        try:
            validation_response = await token_validation_stub.ValidateBootstrapToken(
                service_pb2.TokenValidationRequest(
                    cust_id=cust_id,
                    bootstrap_token=token,
                ),
                timeout=10,
            )
        except grpc.aio.AioRpcError as e:
            print(f"[Enroll] token validation RPC failed: {e.code()} {e.details()}")
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Could not reach token validation service")
            return service_pb2.EnrollResponse()

        if not validation_response.token_validated:
            print(f"[Enroll] REJECTED bootstrap attempt for {cust_id}")
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
            print(f"[Enroll] unexpected signing error: {repr(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Certificate signing failed")
            return service_pb2.EnrollResponse()

        print(f"[Enroll] issued cert via bootstrap for {cust_id}")
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
async def serve():

    global ca_authority, token_validation_channel, token_validation_stub


    server = grpc.aio.server(
        options=[
            ('grpc.max_send_message_length', 50 * 1024 * 1024),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),
            ('grpc.http2.min_ping_interval_without_data_ms', 40000), 
            ('grpc.http2.max_pings_without_data', 0),
            ('grpc.http2.max_ping_strikes', 0),
        ]
    )
    service_pb2_grpc.add_AgentServiceServicer_to_server(
        AgentService(),
        server
    )

    service_pb2_grpc.add_DatasynchorServicer_to_server(
        Datasynchorservice(),
        server
    )

    service_pb2_grpc.add_BootstrapServiceServicer_to_server(
        BootstrapService(),
        server
    )
    # server.add_insecure_port("[::]:50051")
    with open("mtls/client_svr_server.crt", "rb") as f:
        server_cert = f.read()

    with open("mtls/client_svr_server.key", "rb") as f:
        server_key = f.read()

    with open("mtls/ca.crt", "rb") as f:
        ca_cert = f.read()

    server_credentials = grpc.ssl_server_credentials(
        private_key_certificate_chain_pairs=[
            (server_key, server_cert)
        ],
        root_certificates=ca_cert,
        require_client_auth=True
    )

    token_validation_credentials = grpc.ssl_channel_credentials(
        root_certificates=ca_cert,
        private_key=server_key,
        certificate_chain=server_cert,
    )

    token_validation_channel = grpc.aio.secure_channel(
        TOKEN_VALIDATION_ADDR,
        token_validation_credentials,
    )

    token_validation_stub = service_pb2_grpc.TokenValidationServiceStub(
        token_validation_channel
    )

    server.add_secure_port(
        "[::]:50051",
        server_credentials
    )
    
    # Bootstrap port: server-side TLS only, no client cert required.
    # Only BootstrapService is exposed here — AgentService/Datasynchor are
    # NOT registered on this port, so an unauthenticated caller can only
    # ever reach Enroll(), gated by the bootstrap token check inside it.
    bootstrap_credentials = grpc.ssl_server_credentials(
        private_key_certificate_chain_pairs=[
            (server_key, server_cert)
        ],
        root_certificates=ca_cert,
        require_client_auth=False
    )

    server.add_secure_port(
        "[::]:50052",
        bootstrap_credentials
    )
    ca_authority = CertificateAuthority(
        ca_key_path="mtls/ca.key",
        ca_cert_path="mtls/ca.crt",
        validity_days=90,
    )

    await server.start()

    print(" Server running on port 50051")
    await server.wait_for_termination()

if __name__ == "__main__":
    asyncio.run(serve())
