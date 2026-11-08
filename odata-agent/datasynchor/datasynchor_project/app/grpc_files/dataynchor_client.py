import grpc
from . import service_pb2
from . import service_pb2_grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct
import json

from pathlib import Path
import json

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

GRPC_SERVER = "localhost:50051"  # change if needed


credentials = grpc.ssl_channel_credentials(
            root_certificates=config["trusted_ca_cert"].encode("utf-8"),
            private_key=config["client_svr_key"].encode("utf-8"),
            certificate_chain=config["client_svr_cert"].encode("utf-8"),
        )

def get_channel():
    return grpc.secure_channel(
        GRPC_SERVER,
        credentials,
        options=[
            ('grpc.max_send_message_length', 50 * 1024 * 1024),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),
        ]
    )

def get_metadata(source_type,cust_id,url,source_config):
    channel = get_channel()
    stub = service_pb2_grpc.DatasynchorStub(channel)

    request = service_pb2.MetadataRequest(
        cust_id=cust_id,
        metadata_url=url,
        src_conf=source_config,
        src_type = source_type
    )

    response = stub.Metadata(request)

    if response.error_msg:
        raise ValueError(response.error_msg)

    return MessageToDict(response.metadata_json)


def startdataload(cust_id,sources,source_config,destination_config,destination_type,details_for_sts_update):
    channel=get_channel()
    stub = service_pb2_grpc.DatasynchorStub(channel)

    destination_config_struct = Struct()
    destination_config_struct.update(destination_config)

    details_for_sts_update_struct = Struct()
    details_for_sts_update_struct.update(details_for_sts_update)

    if destination_type == "adls":
        request = service_pb2.DataLoadRequest(
            cust_id=cust_id,
            sources=sources,
            src_conf=source_config,
            destination_type=destination_type,
            destination_config=destination_config_struct,
            details_for_sts_update=details_for_sts_update_struct,
        )

    elif destination_type == "snowflake":
        request = service_pb2.DataLoadRequest(
            cust_id=cust_id,
            snowflake_sources=sources,
            src_conf=source_config,
            destination_type=destination_type,
            destination_config=destination_config_struct,
            details_for_sts_update=details_for_sts_update_struct,
        )
    response = stub.StartDataLoad(request)
    return response.accepted

def AlldataSource(src_type,cust_id,data_url,source_config):
    channel=get_channel()
    stub = service_pb2_grpc.DatasynchorStub(channel)

    request = service_pb2.AllDataRequest(
        cust_id=cust_id,
        alldata_url=data_url,
        src_conf=source_config,
        src_type=src_type,
    )

    response = stub.AllDataSource(request)
    if response.error_msg:
        raise ValueError(response.error_msg)

    return MessageToDict(response)

def SourceDetail(src_type,cust_id,source_config,service_name):
    channel=get_channel()
    stub = service_pb2_grpc.DatasynchorStub(channel)

    request = service_pb2.SourceDetailRequest(
        cust_id=cust_id,
        service_name=service_name,
        src_conf=source_config,
        src_type = src_type
    )

    response = stub.GetSourceDetail(request)
    if response.error_msg:
        raise ValueError(response.error_msg)

    return MessageToDict(response.src_details_json)


def test_sap_connection(src_type,cust_id,source_config):
    channel=get_channel()
    stub = service_pb2_grpc.DatasynchorStub(channel)

    request = service_pb2.ConnectionRequest(
        cust_id=cust_id,
        src_conf=source_config,
        src_type = src_type
    )

    response = stub.TestSapConnection(request)
    if response.error_msg:
        raise ValueError(response.error_msg)

    return MessageToDict(response.conn_details_json)
    

def delta_load(cust_id,sources,source_config,destination_config,destination_type,details_for_sts_update):
    channel=get_channel()
    stub = service_pb2_grpc.DatasynchorStub(channel)
    print(sources,"django")
    destination_config_struct = Struct()
    destination_config_struct.update(destination_config)

    details_for_sts_update_struct = Struct()
    details_for_sts_update_struct.update(details_for_sts_update)

    request = service_pb2.TriggerDeltaRequest(
        cust_id=cust_id,
        sources=sources,
        src_conf=source_config,
        destination_type=destination_type,
        destination_config=destination_config_struct,
        details_for_sts_update = details_for_sts_update_struct
    )

    response = stub.TriggerDelta(request)
    return response.accepted