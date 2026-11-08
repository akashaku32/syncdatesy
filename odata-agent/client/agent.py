from grpc import aio
import grpc
import service_pb2
import service_pb2_grpc
from odata_functions import load_data_allsrc,fetch_source_details,metadata,test_connection
from table_scheduler import TableScheduler
import asyncio,shutil,os
from datetime import datetime, timezone
from google.protobuf.struct_pb2 import Struct
import json, time, threading
from cert_manager import (
    get_cert_expiry,
    needs_renewal,
    generate_key_and_csr,
    atomic_write_json,
)
import random
from cryptography import x509
import sys
from pathlib import Path
import ctypes
from google.protobuf.empty_pb2 import Empty

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.json"
TEMP_DIR = BASE_DIR / "temporary_files"

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

agent_id = config["agent_id"]
host = config["server_host"]

# --- Cert rotation tuning ------------------------------------------------
CERT_CHECK_INTERVAL_SECONDS = 24 * 60 * 60      # check every 24 hours
CERT_RENEWAL_THRESHOLD_DAYS = 7                # renew when <= 7 days left
CERT_RENEWAL_RETRY_SECONDS = 5 * 60            # backoff if a renewal attempt fails
BOOTSTRAP_ADDR = f"{host}:50052"

def cleanup_temp_files():
    """
    Clears all previous agent temporary data.
    Keeps the temporary_files directory itself.
    """

    if not TEMP_DIR:
        return
    
    temp_path = os.path.abspath(TEMP_DIR)
    delta_path = os.path.join(BASE_DIR, "delta_files")

    try:
        # Create delta_files separately
        os.makedirs(delta_path, exist_ok=True)
        # Create folder if it doesn't exist
        if not os.path.exists(temp_path):
            os.makedirs(temp_path)
            return

        # Remove everything inside temporary_files
        for item in os.listdir(temp_path):
            item_path = os.path.join(temp_path, item)

            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)

            except Exception as e:
                print(f"Warning: failed to remove temp item {item_path}: {e}")


    except Exception as e:
        print(f"Temporary cleanup failed: {e}")

def cert_is_usable(cert_pem: str) -> bool:
    """
    True only if cert_pem parses AND is not already expired.
    False for missing/empty/malformed/expired certs -- all of which
    mean the normal mTLS path is unusable and bootstrap is required.
    """
    if not cert_pem:
        return False
    try:
        return get_cert_expiry(cert_pem) > datetime.now(timezone.utc)
    except Exception as e:
        print(f"[cert_check] could not parse client_cert: {repr(e)}")
        return False


# Shared token cache for all threads in this Python process.
_gcp_token_cache = None

def get_gcp_access_token():
    """
    Reuse the cached downscoped token while valid.
    Request a new token from GCPTokenBrokerService when necessary.
    """

    global _gcp_token_cache

    # Reuse the shared token if it is still valid.
    cache = _gcp_token_cache

    if cache is not None and time.monotonic() < cache["valid_until"]:
        return cache["token"]

    credentials = grpc.ssl_channel_credentials(
        root_certificates=config["trusted_ca_cert"].encode("utf-8"),
        private_key=config["client_private_key"].encode("utf-8"),
        certificate_chain=config["client_cert"].encode("utf-8"),
    )

    server_addr = f'{config["server_host"]}:50053'

    with grpc.secure_channel(server_addr, credentials) as channel:
        stub = service_pb2_grpc.GCPTokenBrokerServiceStub(channel)

        response = stub.GetDownscopedToken(
            Empty(),
            timeout=120,
        )

    if response.error_message:
        raise RuntimeError(
            f"Downscoped token request failed: {response.error_message}"
        )

    if not response.access_token or response.expires_in <= 0:
        raise RuntimeError("Token server returned an invalid token response")

    refresh_margin = min(60, response.expires_in * 0.1)

    # Create a single cache entry and replace it atomically.
    new_cache = {
        "token": response.access_token,
        "valid_until": (
            time.monotonic()
            + response.expires_in
            - refresh_margin
        ),
    }

    _gcp_token_cache = new_cache

    print(
        "[GCP] Shared token obtained; "
        f"expires in {response.expires_in} seconds"
    )

    return new_cache["token"]


max_workers = 4
class AgentClient():

    def __init__(self,server_addr, cust_id):
        self.server_addr = server_addr
        self.cust_id = cust_id
        self.channel = None
        self.stub = None 
        self.send_queue = asyncio.Queue()
        self.cert_rotation_in_progress = False
        self.gcp_access_token = None
        self.gcp_token_expiry = None
        self.table_scheduler = TableScheduler(max_workers=max_workers)

    def setup_channel(self):
        credentials = grpc.ssl_channel_credentials(
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            private_key=config["client_private_key"].encode("utf-8"),
            certificate_chain=config["client_cert"].encode("utf-8"),
        )

        options = [
            ('grpc.keepalive_time_ms', 50000),
            ('grpc.keepalive_timeout_ms', 10000),
            ('grpc.keepalive_permit_without_calls', 1),
        ]

        self.channel = aio.secure_channel(self.server_addr, 
                                          credentials,
                                          options=options)
        self.stub = service_pb2_grpc.AgentServiceStub(self.channel)

    # Generator (send messages)
    async def request_generator(self):
        print("Sending register...")

        # Send only register once
        yield service_pb2.AgentMessage(
            cust_id=self.cust_id,
            register=True
        )

        # Keep generator alive (no more messages for now)
        while True:
            msg = await self.send_queue.get()
            yield msg 

    async def receive_loop(self, responses):
            
            async for res in responses:
                # src_type is not sent with all request.
                # src_type check is included only in requests for the initial steps of a source.
                # some request doesnt wait for the acknowlegdement (for example StartDataLoad,TriggerDelta). so for these request dont include src_type check
                if res.src_type and res.src_type != "sap-odata":
                    await self.send_queue.put(
                        service_pb2.AgentMessage(
                            request_id=res.request_id,
                            cust_id=self.cust_id,
                            error_msg=f"Unsupported source type: {res.src_type}"
                        )
                    )
                else:
                    asyncio.create_task(
                        self.process_odata_message(res)
                    )

    async def process_odata_message(self, res):

        if res.metadata_url and res.request_id: 

            response = await asyncio.to_thread(
                metadata,
                res.metadata_url,
                res.src_conf
            )
            
            metadata_struct = Struct()
            metadata_struct.update(response)
            await self.send_queue.put(
                service_pb2.AgentMessage(
                    cust_id=self.cust_id,
                    metadata_json=
                    service_pb2.MetadataPayload(
                        request_id=res.request_id,
                        metadata_json=metadata_struct
                    )
                )
            )
        elif res.load_delta == True and res.destination_type == "adls":
            await self.table_scheduler.add_message({
                "response": res,
                "tables": res.sources,
            })          
 
        elif res.sources and res.destination_type == "adls":
            await self.table_scheduler.add_message({
                "response": res,
                "tables": res.sources,
            })

        elif res.destination_type == "snowflake":
            await self.table_scheduler.add_message({
                "response": res,
                "tables": res.snowflake_sources,
            })
        elif res.alldata_url:
            
            response = await asyncio.to_thread(
                load_data_allsrc,
                res.alldata_url,
                res.src_conf
            )

            all_data_struct = Struct()
            all_data_struct.update(response)

            await self.send_queue.put(
                service_pb2.AgentMessage(
                    cust_id=self.cust_id,
                    alldata=service_pb2.AllDataLoad(
                        request_id=res.request_id,
                        alldata=all_data_struct
                    )
                )
            )
        elif res.service_name:
            response = await asyncio.to_thread(
                fetch_source_details,
                res.src_conf,
                res.service_name
            )

            src_details_struct = Struct()
            src_details_struct.update(response)

            await self.send_queue.put(
                service_pb2.AgentMessage(
                    cust_id=self.cust_id,
                    src_json=service_pb2.SourceDetail(
                        request_id=res.request_id,
                        src_details_json=src_details_struct
                    )
                )
            )       
            # ---------------------------------------------------------------

        elif res.test_sap_connection:            
            response = await asyncio.to_thread(
                test_connection,
                res.src_conf,
            )
            conn_details_struct = Struct()
            conn_details_struct.update(response)

            await self.send_queue.put(
                service_pb2.AgentMessage(
                    cust_id=self.cust_id,
                    conn_details=service_pb2.ConnectionDetails(
                        request_id=res.request_id,
                        conn_details_json=conn_details_struct
                    )
                )
            )      



    async def run(self):
        while True:
            try:
                print("Connecting to server...")

                responses = self.stub.Connect(self.request_generator())

                await self.receive_loop(responses)
            except asyncio.CancelledError:
                if self.cert_rotation_in_progress:
                    print("Channel closed for certificate renewal.")
                    self.cert_rotation_in_progress = False
                    self.setup_channel()
                    continue
                raise
            
            except Exception as e:
                if e.code() == grpc.StatusCode.ALREADY_EXISTS:
                    print(
                        f"Connection rejected: {e.details()}"
                    )

                    # Stop agent execution
                    return
                print("Reconnecting in 5 seconds...", repr(e))
                import traceback; traceback.print_exc()

                await asyncio.sleep(5)
                self.setup_channel()

    async def cert_watch_loop(self):
        """
        Runs forever alongside run(). Independently checks certificate
        expiry and renews when close to expiring. Never blocks message
        processing: it only touches the channel at the very end, and even
        then it does so by closing it, which the existing reconnect logic
        in run() already handles gracefully (queued messages are preserved
        because self.send_queue is a persistent instance attribute).
        """
        # small random offset so multiple agents don't all hit the server
        # for renewal at the exact same moment
        await asyncio.sleep(random.uniform(0, 60))

        while True:
            try:
                with open(CONFIG_PATH, "r") as f:
                    cfg = json.load(f)

                cert_pem = cfg["client_cert"]

                if needs_renewal(cert_pem, CERT_RENEWAL_THRESHOLD_DAYS):
                    expiry = get_cert_expiry(cert_pem)
                    print(f"[cert_watch] Cert expires {expiry.isoformat()} - renewing now")
                    ok = await self.renew_certificate()
                    if not ok:
                        # renewal failed (server unreachable, rejected CSR, etc.)
                        # back off and retry sooner than the normal interval,
                        # since we're presumably getting close to expiry.
                        await asyncio.sleep(CERT_RENEWAL_RETRY_SECONDS)
                        continue
                else:
                    expiry = get_cert_expiry(cert_pem)
                    print(f"[cert_watch] Cert OK, expires {expiry.isoformat()}")

            except Exception as e:
                print(f"[cert_watch] check failed: {repr(e)}")

            await asyncio.sleep(CERT_CHECK_INTERVAL_SECONDS)

    async def renew_certificate(self) -> bool:
        """
        Generates a fresh keypair + CSR locally, asks the server to sign it,
        writes the new key/cert to config.json atomically, then triggers a
        graceful reconnect so the running stream picks up the new identity.

        Returns True on success, False on failure (caller decides retry timing).
        """
        try:
            # CPU-bound key generation off the event loop
            private_key_pem, csr_pem = await asyncio.to_thread(
                generate_key_and_csr, self.cust_id
            )

            # Unary RPC - uses the CURRENT (still valid) channel/stub,
            # since renewal happens well before actual expiry.
            response = await self.stub.RenewCertificate(
                service_pb2.CertRenewalRequest(
                    cust_id=self.cust_id,
                    csr_pem=csr_pem,
                ),
                timeout=30,
            )

            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)

            cfg["client_private_key"] = private_key_pem
            cfg["client_cert"] = response.cert_pem
            if response.ca_cert_pem:
                cfg["trusted_ca_cert"] = response.ca_cert_pem

            # Persist atomically - never leaves a half-written config on disk
            await asyncio.to_thread(atomic_write_json, str(CONFIG_PATH), cfg)

            # Update the in-memory copy too, for anything else that reads it
            global config
            config = cfg

            print("[cert_watch] New certificate written to config.json")

            # Force a reconnect so the live stream picks up the new identity.
            # Closing the channel makes the current Connect() stream raise,
            # which run()'s existing except-block already handles: it calls
            # setup_channel() (re-reading config.json) and reconnects.
            # self.send_queue is untouched, so nothing queued is lost.
            if self.channel is not None:
                self.cert_rotation_in_progress = True
                await self.channel.close()

            return True

        except grpc.aio.AioRpcError as e:
            print(f"[cert_watch] RenewCertificate RPC failed: {e.code()} {e.details()}")
            return False
        except Exception as e:
            print(f"[cert_watch] renewal failed: {repr(e)}")
            return False

    async def bootstrap_enroll(self) -> bool:
        """
        Used when the operational cert is already expired/invalid, so the
        normal mTLS channel can't be established at all. Connects on the
        separate no-client-auth port using the long-lived bootstrap token
        instead, and obtains a fresh operational cert.
        """
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)

            bootstrap_token = cfg.get("bootstrap_token")
            if not bootstrap_token:
                print("[bootstrap] no bootstrap_token in config.json -- cannot recover")
                return False

            creds = grpc.ssl_channel_credentials(
                root_certificates=cfg["trusted_ca_cert"].encode("utf-8")
            )

            async with aio.secure_channel(BOOTSTRAP_ADDR, creds) as channel:
                stub = service_pb2_grpc.BootstrapServiceStub(channel)
                private_key_pem, csr_pem = await asyncio.to_thread(
                    generate_key_and_csr, self.cust_id
                )

                try:
                    response = await stub.Enroll(
                        service_pb2.EnrollRequest(
                            cust_id=self.cust_id,
                            csr_pem=csr_pem,
                            bootstrap_token=bootstrap_token,
                        ),
                        timeout=30,
                    )
                except grpc.aio.AioRpcError as e:
                    print(f"[bootstrap] Enroll failed: {e.code()} {e.details()}")
                    return False

                # Make sure the server actually returned a certificate.
                if not response.cert_pem:
                    print("[bootstrap] server returned an empty certificate")
                    return False

                # Make sure the server returned the rotated bootstrap token.
                if not response.new_bootstrap_token:
                    print(
                        "[bootstrap] server did not return "
                        "new_bootstrap_token"
                    )
                    return False

                # Update config with the new operational identity.
                cfg["client_private_key"] = private_key_pem
                cfg["client_cert"] = response.cert_pem

                # IMPORTANT:
                # Replace the old bootstrap token with the newly rotated one.
                cfg["bootstrap_token"] = response.new_bootstrap_token

                # Persist everything atomically.
                await asyncio.to_thread(
                    atomic_write_json,
                    str(CONFIG_PATH),
                    cfg,
                )

                # Update the in-memory configuration too.
                global config
                config = cfg

                print(
                    "[bootstrap] obtained new operational certificate "
                    "and rotated bootstrap token"
                )
                return True

        except Exception as e:
            print(f"[bootstrap] unexpected error: {repr(e)}")
            return False

async def main():

    #cleaning up the temporary_files 
    # cleanup_temp_files()
    client = AgentClient(f"{host}:50051", agent_id)
    # client = AgentClient("173.255.114.5:50051", "C013")

    if not cert_is_usable(config.get("client_cert", "")):
        print("Operational cert missing or expired at startup -- bootstrapping")
        ok = await client.bootstrap_enroll()
        if not ok:
            print("Bootstrap failed -- cannot start")
            return

    client.setup_channel()

    # Runs concurrently with client.run(); never blocks message processing.
    asyncio.create_task(client.cert_watch_loop())

    await client.run()

import os
import sys
import platform

# this below code is to ensure that the agent is not started directly and it is supposed to run only as background task
def is_running_as_windows_service():
    """Return True if running in Windows Session 0."""
    if os.name != "nt":
        return False

    try:
        session_id = ctypes.c_ulong()

        result = ctypes.windll.kernel32.ProcessIdToSessionId(
            os.getpid(),
            ctypes.byref(session_id)
        )

        if result == 0:
            return False

        return session_id.value == 0

    except Exception:
        return False


def is_running_under_systemd():
    """Return True if launched by systemd."""
    if platform.system() != "Linux":
        return False

    return bool(os.environ.get("INVOCATION_ID"))


def ensure_running_as_service():

    system = platform.system()

    # -------------------------
    # Windows
    # -------------------------
    if system == "Windows":

        if not is_running_as_windows_service():
            ctypes.windll.user32.MessageBoxW(
                0,
                "MyAgent runs only as a Windows service.\n\n"
                "Use Services.msc to control it.",
                "MyAgent",
                0x40
            )
            sys.exit(0)

        return

    # -------------------------
    # Linux
    # -------------------------
    if system == "Linux":

        if not is_running_under_systemd():
            print(
                "This program is a background agent and must "
                "be run by systemd."
            )
            sys.exit(1)

        return

    # -------------------------
    # Unsupported OS
    # -------------------------
    print(f"Unsupported operating system: {system}")
    sys.exit(1)

if __name__ == "__main__":
    # This function is used to prevent the agent from being started directly, ensuring that it is started as a Windows service on Windows or as a systemd service on Linux.
    # ensure_running_as_service()
    asyncio.run(main())
 




