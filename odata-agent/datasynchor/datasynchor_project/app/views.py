
from rest_framework.exceptions import APIException
# Create your views here.

from rest_framework.views import APIView
from app.grpc_files.dataynchor_client import get_metadata, startdataload, AlldataSource,SourceDetail,test_sap_connection,delta_load
from rest_framework.response import Response
from datetime import datetime, timezone


class Metadata(APIView):

    def get(self,request):
        url = "https://ecxnet.ddns.net:44300/sap/opu/odata/sap/Z_MASTER_SRV/$metadata"
        cust_id = "C013"
        source_type = "sap-odata"
        source_config = {   
            "host" : "ecvsdx.evidentx.com",
            "port" : 44300,
            "username": "VINOD",
            "password": "<example-password>"
        }
        try:
            response = get_metadata(source_type, cust_id, url, source_config)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)

        return Response(response, status=200)

class StartDataLoad(APIView):

    def get(self,request):

        # adls ------------------------------------------------------------------------
        # source_config = {
        #     "host" : "ecvsdx.evidentx.com",
        #     "port" : 44300,
        #     "username": "VINOD",
        #     "password": "<example-password>"
        # }

        # destination_type = "adls"

        # destination_config = {
        #     "TENANT_ID":"b0cf36f9-cb42-463a-ae4c-da75d2ee1bae",
        #     "CLIENT_ID" : "6bf43649-db4f-40f0-af5a-30ad0190d8b6",
        #     "CLIENT_SECRET" : "<set-from-env>",
        #     "CONTAINER_NAME": "vsd-100",
        #     "STORAGE_ACCOUNT_NAME" : "spectraadlsgen2"
        # }

        # sources=[
        #     # {
        #     #     "name":"ZI_ORDER_DELTA_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":None,
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"AttrOfZI_ORDER_DELTA",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta"                 
        #     # },
        #     # {
        #     #     "name":"Z_MASTER01_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":"Z_MASTER01_SRV_EntityOf0FI_AR_30",
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"EntityOf0FI_AR_30",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta"    
        #     # },
        #     # {
        #     #     "name":"Z_MASTER01_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":None,
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"EntityOf0MATERIAL_ATTR",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta"

        #     # },
        #     {
        #         "name":"Z_I_SALESORDERITEM_SRV",
        #         "compress":False,
        #         "compress_type":None,
        #         "folder_name":"SALESORDERTEST",
        #         "partition_data_by_date": True,
        #         "entity_name":"AttrOfISDSLSORDERITEM",
        #         "file_rec_count":10000,
        #         "load_method" : "initial+delta",
        #         "delta_enabled":True,
        #         "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        #     },
        #     # {
        #     #     "name":"Z_I_SALESORDER_SRV",
        #     #     "compress":True,
        #     #     "compress_type":"snappy",
        #     #     "folder_name":"facts_of_33",
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"AttrOfISDSALESORDER",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta",
        #     #     "delta_enabled":True
        #     # },
        #     # {
        #     #     "name":"Z_I_ORDER_DELTA_02_SRV",
        #     #     "compress":True,
        #     #     "compress_type":"snappy",
        #     #     "folder_name":"test3",
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"AttrOfZI_ORDER_DELTA",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta",
        #     #     "delta_enabled":True
        #     # },
        #     # {
        #     #     "name":"Z_I_ORDER_DELTA_1_SRV",
        #     #     "compress":False,
        #     #     "compress_type":"gzip",
        #     #     "folder_name":"test10",
        #     #     "partition_data_by_date": True,
        #     #     "entity_name":"AttrOfZI_ORDER_DELTA",
        #     #     "file_rec_count":100000,
        #     #     "load_method" : "initial+delta",
        #     #     "delta_enabled":True,
        #     #     "pagination_count":100000,
        #     #     "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        #     # }    
        # ]

        # details_for_sts_update = {
        #     "migration_session_id":"1426",
        #     "job_name":"SAPAGENT_manual_1426_AttrOfZI_ORDER_DELTA",
        #     "connection_id":"129",
        # }
        cust_id = "C013"

        # snowflake -------------------------------------------------------------------
        details_for_sts_update = {
            "migration_session_id":1234,
            "connection_id":"129",
            "job_name":"test01",
            "cmp_id":121,
            "user_id":12,
            "session":34,
            "log_id":67
        }


        source_config = {
            "host" : "ecvsdx.evidentx.com",
            "port" : 44300,
            "username": "VINOD",
            "password": "<example-password>"
        }

        destination_type = "snowflake"

    #     destination_config: Dict[str, Any]
    # """Snowflake destination: account, username, password/private_key, warehouse, database, schema, role."""
        destination_config = {
            "username": "<example-user>",
            "account": "<example-account>",
            "password": "<example-password>",
            "warehouse": "SNOWFLAKE_LEARNING_WH",
            "database":"my_database",
            "schema":"teszt",
            "role" : "ACCOUNTADMIN"
        }

        transform_state = {
  "version": 6,
  "mapping_json": {
    "dtp_id": "DTP_00O2TIIO2L73TN9NY6AZRY2PX",
    "source": {
      "fields": [
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 1,
          "reffield": "",
          "fieldname": "BUKRS"
        },
        {
          "length": 7,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 2,
          "reffield": "",
          "fieldname": "FISCPER"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 3,
          "reffield": "",
          "fieldname": "BELNR"
        },
        {
          "length": 3,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 4,
          "reffield": "",
          "fieldname": "BUZEI"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 5,
          "reffield": "",
          "fieldname": "STATUSPS"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 6,
          "reffield": "",
          "fieldname": "KTOPL"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 7,
          "reffield": "",
          "fieldname": "HKONT"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 8,
          "reffield": "",
          "fieldname": "KOART"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 9,
          "reffield": "",
          "fieldname": "UMSKZ"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 10,
          "reffield": "",
          "fieldname": "BLART"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 11,
          "reffield": "",
          "fieldname": "BSCHL"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 12,
          "reffield": "",
          "fieldname": "FISCVAR"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 13,
          "reffield": "",
          "fieldname": "BLDAT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 14,
          "reffield": "",
          "fieldname": "BUDAT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 15,
          "reffield": "",
          "fieldname": "CPUDT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 16,
          "reffield": "",
          "fieldname": "VALUT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 17,
          "reffield": "",
          "fieldname": "AUGDT"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 18,
          "reffield": "",
          "fieldname": "AUGBL"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 19,
          "reffield": "",
          "fieldname": "KOKRS"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 20,
          "reffield": "",
          "fieldname": "KOSTL"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 21,
          "reffield": "",
          "fieldname": "PRCTR"
        },
        {
          "length": 12,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 22,
          "reffield": "",
          "fieldname": "AUFNR"
        },
        {
          "length": 8,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 23,
          "reffield": "",
          "fieldname": "PROJK"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 24,
          "reffield": "",
          "fieldname": "WERKS"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 25,
          "reffield": "",
          "fieldname": "GSBER"
        },
        {
          "length": 16,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 26,
          "reffield": "",
          "fieldname": "FKBER"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 27,
          "reffield": "",
          "fieldname": "PPRCT"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 28,
          "reffield": "",
          "fieldname": "PARGB"
        },
        {
          "length": 6,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 29,
          "reffield": "",
          "fieldname": "VBUND"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 30,
          "reffield": "",
          "fieldname": "LCURR"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 31,
          "reffield": "",
          "fieldname": "DMSOL"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 32,
          "reffield": "",
          "fieldname": "DMHAB"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 33,
          "reffield": "",
          "fieldname": "DMSHB"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 34,
          "reffield": "",
          "fieldname": "WAERS"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 35,
          "reffield": "",
          "fieldname": "WRSOL"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 36,
          "reffield": "",
          "fieldname": "WRHAB"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 37,
          "reffield": "",
          "fieldname": "WRSHB"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 38,
          "reffield": "",
          "fieldname": "CURT2"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 39,
          "reffield": "",
          "fieldname": "HWAE2"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 40,
          "reffield": "",
          "fieldname": "DMSO2"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 41,
          "reffield": "",
          "fieldname": "DMHA2"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 42,
          "reffield": "",
          "fieldname": "DMSH2"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 43,
          "reffield": "",
          "fieldname": "CURT3"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 44,
          "reffield": "",
          "fieldname": "HWAE3"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 45,
          "reffield": "",
          "fieldname": "DMSO3"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 46,
          "reffield": "",
          "fieldname": "DMHA3"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 47,
          "reffield": "",
          "fieldname": "DMSH3"
        },
        {
          "length": 16,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 48,
          "reffield": "",
          "fieldname": "XBLNR"
        },
        {
          "length": 20,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 49,
          "reffield": "",
          "fieldname": "XREF3"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 50,
          "reffield": "",
          "fieldname": "AWREF"
        },
        {
          "length": 4,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 51,
          "reffield": "",
          "fieldname": "AWGJA"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 52,
          "reffield": "",
          "fieldname": "AWBUK"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 53,
          "reffield": "",
          "fieldname": "AWKOK"
        },
        {
          "length": 50,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 54,
          "reffield": "",
          "fieldname": "SGTXT"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 55,
          "reffield": "",
          "fieldname": "XNEGP"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 56,
          "reffield": "",
          "fieldname": "UPDMOD"
        },
        {
          "length": 24,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 57,
          "reffield": "",
          "fieldname": "PS_POSID"
        },
        {
          "length": 18,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 58,
          "reffield": "",
          "fieldname": "ZUONR"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 59,
          "reffield": "",
          "fieldname": "STBLG"
        },
        {
          "length": 4,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 60,
          "reffield": "",
          "fieldname": "STJAH"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 61,
          "reffield": "",
          "fieldname": "REBZG"
        },
        {
          "length": 4,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 62,
          "reffield": "",
          "fieldname": "REBZJ"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 63,
          "reffield": "",
          "fieldname": "REBZT"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 64,
          "reffield": "",
          "fieldname": "BUZID"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 65,
          "reffield": "",
          "fieldname": "MWART"
        },
        {
          "length": 3,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 66,
          "reffield": "",
          "fieldname": "TXGRP"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 67,
          "reffield": "",
          "fieldname": "ZFBDT"
        },
        {
          "length": 3,
          "datatype": "DEC",
          "decimals": 0,
          "key_flag": " ",
          "position": 68,
          "reffield": "",
          "fieldname": "ZBD1T"
        },
        {
          "length": 3,
          "datatype": "DEC",
          "decimals": 0,
          "key_flag": " ",
          "position": 69,
          "reffield": "",
          "fieldname": "ZBD2T"
        },
        {
          "length": 3,
          "datatype": "DEC",
          "decimals": 0,
          "key_flag": " ",
          "position": 70,
          "reffield": "",
          "fieldname": "ZBD3T"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 71,
          "reffield": "",
          "fieldname": "XREVERSAL"
        },
        {
          "length": 5,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 72,
          "reffield": "",
          "fieldname": "AWTYP"
        },
        {
          "length": 20,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 73,
          "reffield": "",
          "fieldname": "AWKEY"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 74,
          "reffield": "",
          "fieldname": "BSTAT"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 75,
          "reffield": "",
          "fieldname": "DMBTR"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 76,
          "reffield": "",
          "fieldname": "DMBE2"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 77,
          "reffield": "",
          "fieldname": "DMBE3"
        },
        {
          "length": 4,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 78,
          "reffield": "",
          "fieldname": "GJAHR"
        },
        {
          "length": 3,
          "datatype": "CLNT",
          "decimals": 0,
          "key_flag": " ",
          "position": 79,
          "reffield": "",
          "fieldname": "MANDT"
        },
        {
          "length": 2,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 80,
          "reffield": "",
          "fieldname": "MONAT"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 81,
          "reffield": "",
          "fieldname": "SHKZG"
        },
        {
          "length": 23,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 82,
          "reffield": "",
          "fieldname": "WRBTR"
        },
        {
          "length": 25,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 83,
          "reffield": "",
          "fieldname": "BKTXT"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 84,
          "reffield": "",
          "fieldname": "GLVOR"
        },
        {
          "length": 8,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 85,
          "reffield": "",
          "fieldname": "PERNR"
        },
        {
          "length": 16,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 86,
          "reffield": "",
          "fieldname": "FISTL"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 87,
          "reffield": "",
          "fieldname": "GEBER"
        },
        {
          "length": 3,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 88,
          "reffield": "",
          "fieldname": "KTOSL"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 89,
          "reffield": "",
          "fieldname": "BUDGET_PD"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 90,
          "reffield": "",
          "fieldname": "MWSKZ"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 91,
          "reffield": "",
          "fieldname": "ODQ_CHANGEMODE"
        },
        {
          "length": 19,
          "datatype": "DEC",
          "decimals": 0,
          "key_flag": " ",
          "position": 92,
          "reffield": "",
          "fieldname": "ODQ_ENTITYCNTR"
        }
      ],
      "objectname": "0FI_GL_4",
      "objecttext": "DataSource 0FI_GL_4 (General Ledger: Line Items) from source system FADCLNT100 (Snowflake: LANDING.0FI_GL_4)",
      "objecttype": "RSDS",
      "source_system": "FADCLNT100"
    },
    "target": {
      "fields": [
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 1,
          "reffield": "",
          "fieldname": "BUKRS"
        },
        {
          "length": 7,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 2,
          "reffield": "",
          "fieldname": "FISCPER"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 3,
          "reffield": "",
          "fieldname": "BELNR"
        },
        {
          "length": 3,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 4,
          "reffield": "",
          "fieldname": "BUZEI"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 5,
          "reffield": "",
          "fieldname": "STATUSPS"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 6,
          "reffield": "",
          "fieldname": "KTOPL"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 7,
          "reffield": "",
          "fieldname": "HKONT"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 8,
          "reffield": "",
          "fieldname": "KOART"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 9,
          "reffield": "",
          "fieldname": "UMSKZ"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 10,
          "reffield": "",
          "fieldname": "DOC_TYPE"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 11,
          "reffield": "",
          "fieldname": "BSCHL"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 12,
          "reffield": "",
          "fieldname": "FISCVAR"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 13,
          "reffield": "",
          "fieldname": "BLDAT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 14,
          "reffield": "",
          "fieldname": "BUDAT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 15,
          "reffield": "",
          "fieldname": "CPUDT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 16,
          "reffield": "",
          "fieldname": "VALUT"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 17,
          "reffield": "",
          "fieldname": "AUGDT"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 18,
          "reffield": "",
          "fieldname": "AUGBL"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 19,
          "reffield": "",
          "fieldname": "KOKRS"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 20,
          "reffield": "",
          "fieldname": "KOSTL"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 21,
          "reffield": "",
          "fieldname": "PROFIT_CENTER"
        },
        {
          "length": 12,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 22,
          "reffield": "",
          "fieldname": "ORDER_NUMBER"
        },
        {
          "length": 8,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 23,
          "reffield": "",
          "fieldname": "WBS_ELEMENT_NO"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 24,
          "reffield": "",
          "fieldname": "WERKS"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 25,
          "reffield": "",
          "fieldname": "GSBER"
        },
        {
          "length": 16,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 26,
          "reffield": "",
          "fieldname": "FKBER"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 27,
          "reffield": "",
          "fieldname": "PPRCT"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 28,
          "reffield": "",
          "fieldname": "PARGB"
        },
        {
          "length": 6,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 29,
          "reffield": "",
          "fieldname": "VBUND"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 30,
          "reffield": "",
          "fieldname": "LCURR"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 31,
          "reffield": "LCURR",
          "fieldname": "LC_DEBIT_AMT"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 32,
          "reffield": "LCURR",
          "fieldname": "DMHAB"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 33,
          "reffield": "LCURR",
          "fieldname": "LC_NET_AMT"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 34,
          "reffield": "",
          "fieldname": "DOC_CURRENCY"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 35,
          "reffield": "DOC_CURRENCY",
          "fieldname": "WRSOL"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 36,
          "reffield": "DOC_CURRENCY",
          "fieldname": "WRHAB"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 37,
          "reffield": "DOC_CURRENCY",
          "fieldname": "WRSHB"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 38,
          "reffield": "",
          "fieldname": "CURR_TYPE_2"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 39,
          "reffield": "",
          "fieldname": "HWAE2"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 40,
          "reffield": "HWAE2",
          "fieldname": "DMSO2"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 41,
          "reffield": "HWAE2",
          "fieldname": "GC_CREDIT_AMT"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 42,
          "reffield": "HWAE2",
          "fieldname": "DMSH2"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 43,
          "reffield": "",
          "fieldname": "CURT3"
        },
        {
          "length": 5,
          "datatype": "CUKY",
          "decimals": 0,
          "key_flag": " ",
          "position": 44,
          "reffield": "",
          "fieldname": "HARD_CURRENCY_3"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 45,
          "reffield": "HARD_CURRENCY_3",
          "fieldname": "DMSO3"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 46,
          "reffield": "HARD_CURRENCY_3",
          "fieldname": "DMHA3"
        },
        {
          "length": 13,
          "datatype": "CURR",
          "decimals": 2,
          "key_flag": " ",
          "position": 47,
          "reffield": "HARD_CURRENCY_3",
          "fieldname": "DMSH3"
        },
        {
          "length": 16,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 48,
          "reffield": "",
          "fieldname": "XBLNR"
        },
        {
          "length": 20,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 49,
          "reffield": "",
          "fieldname": "XREF3"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 50,
          "reffield": "",
          "fieldname": "ORIGIN_REF_DOC"
        },
        {
          "length": 4,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 51,
          "reffield": "",
          "fieldname": "AWGJA"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 52,
          "reffield": "",
          "fieldname": "ORIGIN_COMP_CODE"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 53,
          "reffield": "",
          "fieldname": "AWKOK"
        },
        {
          "length": 50,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 54,
          "reffield": "",
          "fieldname": "ITEM_TEXT"
        },
        {
          "length": 1,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 55,
          "reffield": "",
          "fieldname": "UPDMOD"
        },
        {
          "length": 24,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 56,
          "reffield": "",
          "fieldname": "PS_POSID"
        },
        {
          "length": 18,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 57,
          "reffield": "",
          "fieldname": "ZUONR"
        },
        {
          "length": 25,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 58,
          "reffield": "",
          "fieldname": "BKTXT"
        },
        {
          "length": 4,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 59,
          "reffield": "",
          "fieldname": "GLVOR"
        },
        {
          "length": 8,
          "datatype": "NUMC",
          "decimals": 0,
          "key_flag": " ",
          "position": 60,
          "reffield": "",
          "fieldname": "PERNR"
        },
        {
          "length": 16,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 61,
          "reffield": "",
          "fieldname": "FUNDS_CENTER"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 62,
          "reffield": "",
          "fieldname": "GEBER"
        },
        {
          "length": 3,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 63,
          "reffield": "",
          "fieldname": "KTOSL"
        },
        {
          "length": 10,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 64,
          "reffield": "",
          "fieldname": "BUDGET_PERIOD"
        },
        {
          "length": 8,
          "datatype": "DATS",
          "decimals": 0,
          "key_flag": " ",
          "position": 65,
          "reffield": "",
          "fieldname": "UPD_DATE"
        },
        {
          "length": 2,
          "datatype": "CHAR",
          "decimals": 0,
          "key_flag": " ",
          "position": 66,
          "reffield": "",
          "fieldname": "GN_R3_SSY"
        }
      ],
      "objectname": "/IMO/CMFIGL04",
      "objecttext": "CM FI-GL: Line Items (0FI_GL_4) - Staging (Inbound Queue Only) (Snowflake: LANDING./IMO/CMFIGL04_STRM)",
      "objecttype": "ADSO"
    },
    "row_filter": {
      "present": True,
      "condition": {
        "type": "IN",
        "field": "BUKRS",
        "values": [
          "IN01",
          "1710"
        ]
      },
      "description": "DTP-level filter node (fixedValues): BUKRS EQ 'IN01' OR BUKRS EQ '1710' (two include single-value selections on the same field, combined with OR). The transformation itself has no routine.",
      "implemented_in": "DTP_00O2TIIO2L73TN9NY6AZRY2PX_PYTHON_transformation.py"
    },
    "audit_report": "DTP_00O2TIIO2L73TN9NY6AZRY2PX_audit_report.pdf",
    "field_mapping": [
      {
        "rule_id": [
          1
        ],
        "position": 1,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BUKRS",
        "constant_value": None,
        "src_fieldnames": [
          "BUKRS"
        ]
      },
      {
        "rule_id": [
          2
        ],
        "position": 2,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "FISCPER",
        "constant_value": None,
        "src_fieldnames": [
          "FISCPER"
        ]
      },
      {
        "rule_id": [
          3
        ],
        "position": 3,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BELNR",
        "constant_value": None,
        "src_fieldnames": [
          "BELNR"
        ]
      },
      {
        "rule_id": [
          4
        ],
        "position": 4,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BUZEI",
        "constant_value": None,
        "src_fieldnames": [
          "BUZEI"
        ]
      },
      {
        "rule_id": [
          5
        ],
        "position": 5,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "STATUSPS",
        "constant_value": None,
        "src_fieldnames": [
          "STATUSPS"
        ]
      },
      {
        "rule_id": [
          6
        ],
        "position": 6,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "KTOPL",
        "constant_value": None,
        "src_fieldnames": [
          "KTOPL"
        ]
      },
      {
        "rule_id": [
          7
        ],
        "position": 7,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "HKONT",
        "constant_value": None,
        "src_fieldnames": [
          "HKONT"
        ]
      },
      {
        "rule_id": [
          8
        ],
        "position": 8,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "KOART",
        "constant_value": None,
        "src_fieldnames": [
          "KOART"
        ]
      },
      {
        "rule_id": [
          9
        ],
        "position": 9,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "UMSKZ",
        "constant_value": None,
        "src_fieldnames": [
          "UMSKZ"
        ]
      },
      {
        "rule_id": [
          10
        ],
        "position": 10,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DOC_TYPE",
        "constant_value": None,
        "src_fieldnames": [
          "BLART"
        ]
      },
      {
        "rule_id": [
          11
        ],
        "position": 11,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BSCHL",
        "constant_value": None,
        "src_fieldnames": [
          "BSCHL"
        ]
      },
      {
        "rule_id": [
          12
        ],
        "position": 12,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "FISCVAR",
        "constant_value": None,
        "src_fieldnames": [
          "FISCVAR"
        ]
      },
      {
        "rule_id": [
          13
        ],
        "position": 13,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BLDAT",
        "constant_value": None,
        "src_fieldnames": [
          "BLDAT"
        ]
      },
      {
        "rule_id": [
          14
        ],
        "position": 14,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BUDAT",
        "constant_value": None,
        "src_fieldnames": [
          "BUDAT"
        ]
      },
      {
        "rule_id": [
          15
        ],
        "position": 15,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "CPUDT",
        "constant_value": None,
        "src_fieldnames": [
          "CPUDT"
        ]
      },
      {
        "rule_id": [
          16
        ],
        "position": 16,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "VALUT",
        "constant_value": None,
        "src_fieldnames": [
          "VALUT"
        ]
      },
      {
        "rule_id": [
          17
        ],
        "position": 17,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "AUGDT",
        "constant_value": None,
        "src_fieldnames": [
          "AUGDT"
        ]
      },
      {
        "rule_id": [
          18
        ],
        "position": 18,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "AUGBL",
        "constant_value": None,
        "src_fieldnames": [
          "AUGBL"
        ]
      },
      {
        "rule_id": [
          19
        ],
        "position": 19,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "KOKRS",
        "constant_value": None,
        "src_fieldnames": [
          "KOKRS"
        ]
      },
      {
        "rule_id": [
          20
        ],
        "position": 20,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "KOSTL",
        "constant_value": None,
        "src_fieldnames": [
          "KOSTL"
        ]
      },
      {
        "rule_id": [
          21
        ],
        "position": 21,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "PROFIT_CENTER",
        "constant_value": None,
        "src_fieldnames": [
          "PRCTR"
        ]
      },
      {
        "rule_id": [
          22
        ],
        "position": 22,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "ORDER_NUMBER",
        "constant_value": None,
        "src_fieldnames": [
          "AUFNR"
        ]
      },
      {
        "rule_id": [
          23
        ],
        "position": 23,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "WBS_ELEMENT_NO",
        "constant_value": None,
        "src_fieldnames": [
          "PROJK"
        ]
      },
      {
        "rule_id": [
          24
        ],
        "position": 24,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "WERKS",
        "constant_value": None,
        "src_fieldnames": [
          "WERKS"
        ]
      },
      {
        "rule_id": [
          25
        ],
        "position": 25,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "GSBER",
        "constant_value": None,
        "src_fieldnames": [
          "GSBER"
        ]
      },
      {
        "rule_id": [
          26
        ],
        "position": 26,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "FKBER",
        "constant_value": None,
        "src_fieldnames": [
          "FKBER"
        ]
      },
      {
        "rule_id": [
          27
        ],
        "position": 27,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "PPRCT",
        "constant_value": None,
        "src_fieldnames": [
          "PPRCT"
        ]
      },
      {
        "rule_id": [
          28
        ],
        "position": 28,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "PARGB",
        "constant_value": None,
        "src_fieldnames": [
          "PARGB"
        ]
      },
      {
        "rule_id": [
          29
        ],
        "position": 29,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "VBUND",
        "constant_value": None,
        "src_fieldnames": [
          "VBUND"
        ]
      },
      {
        "rule_id": [
          30
        ],
        "position": 30,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "LCURR",
        "constant_value": None,
        "src_fieldnames": [
          "LCURR"
        ]
      },
      {
        "rule_id": [
          31
        ],
        "position": 31,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "LC_DEBIT_AMT",
        "constant_value": None,
        "src_fieldnames": [
          "DMSOL"
        ]
      },
      {
        "rule_id": [
          32
        ],
        "position": 32,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DMHAB",
        "constant_value": None,
        "src_fieldnames": [
          "DMHAB"
        ]
      },
      {
        "rule_id": [
          33
        ],
        "position": 33,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "LC_NET_AMT",
        "constant_value": None,
        "src_fieldnames": [
          "DMSHB"
        ]
      },
      {
        "rule_id": [
          34
        ],
        "position": 34,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DOC_CURRENCY",
        "constant_value": None,
        "src_fieldnames": [
          "WAERS"
        ]
      },
      {
        "rule_id": [
          35
        ],
        "position": 35,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "WRSOL",
        "constant_value": None,
        "src_fieldnames": [
          "WRSOL"
        ]
      },
      {
        "rule_id": [
          36
        ],
        "position": 36,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "WRHAB",
        "constant_value": None,
        "src_fieldnames": [
          "WRHAB"
        ]
      },
      {
        "rule_id": [
          37
        ],
        "position": 37,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "WRSHB",
        "constant_value": None,
        "src_fieldnames": [
          "WRSHB"
        ]
      },
      {
        "rule_id": [
          38
        ],
        "position": 38,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "CURR_TYPE_2",
        "constant_value": None,
        "src_fieldnames": [
          "CURT2"
        ]
      },
      {
        "rule_id": [
          39
        ],
        "position": 39,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "HWAE2",
        "constant_value": None,
        "src_fieldnames": [
          "HWAE2"
        ]
      },
      {
        "rule_id": [
          40
        ],
        "position": 40,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DMSO2",
        "constant_value": None,
        "src_fieldnames": [
          "DMSO2"
        ]
      },
      {
        "rule_id": [
          41
        ],
        "position": 41,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "GC_CREDIT_AMT",
        "constant_value": None,
        "src_fieldnames": [
          "DMHA2"
        ]
      },
      {
        "rule_id": [
          42
        ],
        "position": 42,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DMSH2",
        "constant_value": None,
        "src_fieldnames": [
          "DMSH2"
        ]
      },
      {
        "rule_id": [
          43
        ],
        "position": 43,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "CURT3",
        "constant_value": None,
        "src_fieldnames": [
          "CURT3"
        ]
      },
      {
        "rule_id": [
          44
        ],
        "position": 44,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "HARD_CURRENCY_3",
        "constant_value": None,
        "src_fieldnames": [
          "HWAE3"
        ]
      },
      {
        "rule_id": [
          45
        ],
        "position": 45,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DMSO3",
        "constant_value": None,
        "src_fieldnames": [
          "DMSO3"
        ]
      },
      {
        "rule_id": [
          46
        ],
        "position": 46,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DMHA3",
        "constant_value": None,
        "src_fieldnames": [
          "DMHA3"
        ]
      },
      {
        "rule_id": [
          47
        ],
        "position": 47,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "DMSH3",
        "constant_value": None,
        "src_fieldnames": [
          "DMSH3"
        ]
      },
      {
        "rule_id": [
          48
        ],
        "position": 48,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "XBLNR",
        "constant_value": None,
        "src_fieldnames": [
          "XBLNR"
        ]
      },
      {
        "rule_id": [
          49
        ],
        "position": 49,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "XREF3",
        "constant_value": None,
        "src_fieldnames": [
          "XREF3"
        ]
      },
      {
        "rule_id": [
          50
        ],
        "position": 50,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "ORIGIN_REF_DOC",
        "constant_value": None,
        "src_fieldnames": [
          "AWREF"
        ]
      },
      {
        "rule_id": [
          51
        ],
        "position": 51,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "AWGJA",
        "constant_value": None,
        "src_fieldnames": [
          "AWGJA"
        ]
      },
      {
        "rule_id": [
          52
        ],
        "position": 52,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "ORIGIN_COMP_CODE",
        "constant_value": None,
        "src_fieldnames": [
          "AWBUK"
        ]
      },
      {
        "rule_id": [
          53
        ],
        "position": 53,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "AWKOK",
        "constant_value": None,
        "src_fieldnames": [
          "AWKOK"
        ]
      },
      {
        "rule_id": [
          54
        ],
        "position": 54,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "ITEM_TEXT",
        "constant_value": None,
        "src_fieldnames": [
          "SGTXT"
        ]
      },
      {
        "rule_id": [
          55
        ],
        "position": 55,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "UPDMOD",
        "constant_value": None,
        "src_fieldnames": [
          "UPDMOD"
        ]
      },
      {
        "rule_id": [
          56
        ],
        "position": 56,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "PS_POSID",
        "constant_value": None,
        "src_fieldnames": [
          "PS_POSID"
        ]
      },
      {
        "rule_id": [
          57
        ],
        "position": 57,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "ZUONR",
        "constant_value": None,
        "src_fieldnames": [
          "ZUONR"
        ]
      },
      {
        "rule_id": [
          58
        ],
        "position": 58,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BKTXT",
        "constant_value": None,
        "src_fieldnames": [
          "BKTXT"
        ]
      },
      {
        "rule_id": [
          59
        ],
        "position": 59,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "GLVOR",
        "constant_value": None,
        "src_fieldnames": [
          "GLVOR"
        ]
      },
      {
        "rule_id": [
          60
        ],
        "position": 60,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "PERNR",
        "constant_value": None,
        "src_fieldnames": [
          "PERNR"
        ]
      },
      {
        "rule_id": [
          61
        ],
        "position": 61,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "FUNDS_CENTER",
        "constant_value": None,
        "src_fieldnames": [
          "FISTL"
        ]
      },
      {
        "rule_id": [
          62
        ],
        "position": 62,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "GEBER",
        "constant_value": None,
        "src_fieldnames": [
          "GEBER"
        ]
      },
      {
        "rule_id": [
          63
        ],
        "position": 63,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "KTOSL",
        "constant_value": None,
        "src_fieldnames": [
          "KTOSL"
        ]
      },
      {
        "rule_id": [
          64
        ],
        "position": 64,
        "mapping_type": "DIRECT",
        "tgt_fieldname": "BUDGET_PERIOD",
        "constant_value": None,
        "src_fieldnames": [
          "BUDGET_PD"
        ]
      },
      {
        "rule_id": [],
        "position": 65,
        "mapping_type": "NO_RULE",
        "tgt_fieldname": "UPD_DATE",
        "constant_value": "00000000",
        "src_fieldnames": []
      },
      {
        "rule_id": [],
        "position": 66,
        "mapping_type": "NO_RULE",
        "tgt_fieldname": "GN_R3_SSY",
        "constant_value": "",
        "src_fieldnames": []
      }
    ],
    "transformation": {
      "file": "DTP_00O2TIIO2L73TN9NY6AZRY2PX_PYTHON_transformation.py",
      "type": "PYTHON",
      "entry_point": "__TRANSFORM_0FI_GL_4_IMOCMFIGL04"
    }
  },
  "mapping_overrides": {
    "mappings": [],
    "targetOrder": [],
    "addedSources": [],
    "removedTargets": [],
    "removedJsonMappings": []
  },
  "script_text": "import pandas as pd\n\ndef transform(__source_df: pd.DataFrame) -> pd.DataFrame:\n    __target_df = __source_df.copy()\n    df = __target_df\n    df[\"HKONT\"] = df[\"HKONT\"].astype(str).str.lstrip(\"0\")\n    df[\"KOKRS\"] = df[\"KOKRS\"].astype(str).str[:2]\n    df[\"LCURR\"] = df[\"LCURR\"].replace(\"USD\", \"EURO\")\n    df[\"DMSH2\"] = pd.to_numeric(df[\"DMSH2\"], errors=\"coerce\") * 1.5\n\n    return __target_df\n",
  "script_sha256": "bb31e486899169caa24aa4a984a15cba376b06d330e93e3c3fcb277a29628c46",
  "entry_point": "transform",
  "run_order": "mapping_script",
  "row_filter": None,
  "service_name": "ZFI_GL_4_1_SRV",
  "entity_name": "EntityOf0FI_GL_4",
  "json_table_name": "/IMO/CMFIGL04",
  "destination_table_name": "IMO_CMFIGL04",
  "destination_name_source": "JSON",
  "previous_destination_table_name": "ENTITY_OF_0_FI_GL_4",
  "destination_rename_mode": "create_new"
        }
        sources = [
            # {
            #     "service_name":"Z_I_SALESORDERITEM_SRV",
            #     "entity_name":"AttrOfISDSLSORDERITEM",
            #     "load_method": "initial",
            #     "initial_completed_flag": False,
            #     "drop_and_reload" : False,
            #     "pagination_count":30,
            #     "filter_expr":None,
            #     "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            #     "delta_name":"DeltaLinksOfAttrOfISDSLSORDERITEM",
            #     "delta_token":None,
            #     "transform_state":None
                
            # }, 
            {
                "service_name":"Z_MASTER01_SRV",
                "entity_name":"EntityOf0FI_AR_30",
                "load_method": "initial",
                "initial_completed_flag": False,
                "drop_and_reload" : False,
                "pagination_count":30,
                "filter_expr":None,
                "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "delta_name":None,
                "delta_token":None,
                "transform_state":None
                
            }, 
            # {
            #     "service_name":"Z_MASTER01_SRV",
            #     "entity_name":"EntityOf0MATERIAL_ATTR",
            #     "load_method": "initial",
            #     "initial_completed_flag": False,
            #     "drop_and_reload" : False,
            #     "pagination_count":30,
            #     "filter_expr":None,
            #     "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            #     "delta_name":"DeltaLinksOfEntityOf0MATERIAL_ATTR",
            #     "delta_token":None,
            #     "transform_state":None
            # }, 
            # {
            #     "service_name":"Z_I_ORDER_DELTA_1_SRV",
            #     "entity_name":"AttrOfZI_ORDER_DELTA",
            #     "load_method": "initial",
            #     "initial_completed_flag": False,
            #     "drop_and_reload" : False,
            #     "pagination_count":100000,
            #     "filter_expr":None,
            #     "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            #     "delta_name":"DeltaLinksOfAttrOfZI_ORDER_DELTA",
            #     "delta_token":None,
            #     "transform_state":None
                
            # }, 
            # # ------------------------------------------------------------------------------------------------
            # {
            #     "service_name":"ZFI_GL_4_SRV",
            #     "entity_name":"EntityOf0FI_GL_4",
            #     "load_method": "initial",
            #     "initial_completed_flag": False,
            #     "drop_and_reload" : False,
            #     "pagination_count":30,
            #     "filter_expr":None,
            #     "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            #     "delta_name":"DeltaLinksOfEntityOf0FI_GL_4",
            #     "delta_token":"D20261019082411_000025000",
            #     "transform_state":transform_state  
            # },                   
        ]

        response = startdataload(cust_id,sources,source_config,destination_config,destination_type,details_for_sts_update)
        return Response(response)

class GetAllDataSource(APIView):
    def get(self,request):
        url="http://ecvsdx.evidentx.com:8000/sap/opu/odata/sap/Z_I_DS_SRVC_LIST_SRV/FactsOfZ_I_DS_SRVC_LIST/?$format=json"
        cust_id = "C013"

        source_config = {
            "host" : "ecvsdx.evidentx.com",
            "port" : 44300,
            "username": "VINOD",
            "password": "<example-password>"
        }
        source_type = "sap-odata"
        try:
            response = AlldataSource(source_type,cust_id,url,source_config)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)

        return Response(response, status=200)
    
class GetSourceDetails(APIView):

    def get(self,request):

        cust_id = "C013"

        source_config = {
            "host" : "ecvsdx.evidentx.com",
            "port" : 44300,
            "username": "VINOD",
            "password": "<example-password>"
        }
        source_type = "sap-odata"
        service_name = "Z_MASTER01_SRV"

        try:
            response = SourceDetail(source_type,cust_id,source_config,service_name)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)

        return Response(response, status=200)

class Test_connection(APIView):
    def get(self,request):
        cust_id = "C013"
        source_type = "sap-odata"
        source_config = {
            "host" : "ecvsdx.evidentx.com",
            "port" : 44300,
            "username": "VINOD",
            "password": "<example-password>"
        }
        try:
            response = test_sap_connection(source_type,cust_id,source_config)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)

        return Response(response, status=200)



class LoadDelta(APIView):
    def get(self,request):

        # source_config = {
        #     "host" : "ecvsdx.evidentx.com",
        #     "port" : 44300,
        #     "username": "VINOD",
        #     "password": "<example-password>"
        # }

        # destination_type = "adls"
        # destination_config = {
        #     "TENANT_ID":"b0cf36f9-cb42-463a-ae4c-da75d2ee1bae",
        #     "CLIENT_ID" : "6bf43649-db4f-40f0-af5a-30ad0190d8b6",
        #     "CLIENT_SECRET" : "<set-from-env>",
        #     "CONTAINER_NAME": "vsd-100",
        #     "STORAGE_ACCOUNT_NAME" : "spectraadlsgen2"
        # }

        # sources=[
        #     # {
        #     #     "name":"ZI_ORDER_DELTA_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":None,
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"AttrOfZI_ORDER_DELTA",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta"                     
        #     # },
        #     # {
        #     #     "name":"Z_MASTER01_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":"Z_MASTER01_SRV_EntityOf0FI_AR_30",
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"EntityOf0FI_AR_30",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta"    
        #     # },
        #     # {
        #     #     "name":"Z_MASTER01_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":None,
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"EntityOf0MATERIAL_ATTR",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta"    
        #     # },
        #     {
        #         "name":"Z_I_SALESORDERITEM_SRV",
        #         "compress":False,
        #         "compress_type":None,
        #         "folder_name":"SALESORDERTEST",
        #         "partition_data_by_date": True,
        #         "entity_name":"AttrOfISDSLSORDERITEM",
        #         "file_rec_count":20,
        #         "load_method" : "initial+delta",
        #         "delta_enabled":True,
        #         "delta_token" : "D20261005111514_000031000", 
        #         "pagination_count":100000, 
        #         "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")   
        #     },
        #     # {
        #     #     "name":"Z_I_SALESORDER_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":None,
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"AttrOfISDSALESORDER",
        #     #     "file_rec_count":5,
        #     #     "load_method" : "initial+delta",
        #     #     "delta_enabled":True,
        #     #     "delta_token" : "D20260717090704_000019000"  
        #     # },
        #     # {
        #     #     "name":"ZVBAK_VBAP_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":"Facts_of_zv",
        #     #     "partition_delta_by_date": True,
        #     #     "entity_name":"FactsOfZVBAK_VBAP_CDS",
        #     #     "file_rec_count":10000,
        #     #     "load_method" : "initial+delta",              
        #     # }
        #     # {
        #     #     "name":"Z_I_ORDER_DELTA_1_SRV",
        #     #     "compress":False,
        #     #     "compress_type":None,
        #     #     "folder_name":"test5",
        #     #     "partition_data_by_date": True,
        #     #     "entity_name":"AttrOfZI_ORDER_DELTA",
        #     #     "file_rec_count":100000,
        #     #     "load_method" : "initial",
        #     #     "delta_enabled":False,
        #     #     "delta_token" : "D20260902072327_000042000",
        #     #     "pagination_count":100000,
        #     #     "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        #     # }, 
        # ]


        # details_for_sts_update = {
        #     "migration_session_id":"1426",
        #     "job_name":"SAPAGENT_manual_1426_AttrOfZI_ORDER_DELTA",
        #     "connection_id":"129",
        # }
        # cust_id = "C013"

        #----------------------------------------------------------------------------------
        cust_id = "C013"
        details_for_sts_update = {
            "connection_id":"129"
            ""
        }


        source_config = {
            "host" : "ecvsdx.evidentx.com",
            "port" : 44300,
            "username": "VINOD",
            "password": "<example-password>"
        }

        destination_type = "snowflake"

    #     destination_config: Dict[str, Any]
    # """Snowflake destination: account, username, password/private_key, warehouse, database, schema, role."""
        destination_config = {
            "username": "<example-user>",
            "account": "<example-account>",
            "password": "<example-password>",
            "warehouse": "SNOWFLAKE_LEARNING_WH",
            "database":"my_database",
            "schema":"teszt",
            "role" : "ACCOUNTADMIN"
        }
        sources = [
            {
                "service_name":"Z_I_SALESORDERITEM_SRV",
                "entity_name":"AttrOfISDSLSORDERITEM",
                "load_method": "delta",
                "initial_completed_flag": False,
                "drop_and_reload" : False,
                "pagination_count":30,
                "filter_expr":None,
                "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "delta_name":"DeltaLinksOfAttrOfISDSLSORDERITEM",
                "delta_token":"D20261019082411_000025000"
                
            },              
        ]
        response = delta_load(cust_id,sources,source_config,destination_config,destination_type,details_for_sts_update)

        return Response(response)

def status_update(detais):
    pass


