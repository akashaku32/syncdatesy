from datetime import datetime
import requests
import xmltodict
from utils.adls_utils import EDM_TYPE_MAPPING,PROPERTIES,generate_filename_base,generate_filename
import os
from adls_connect import write_adls,delete_adls_directory,delete_adls_file,adls_connection
import pandas as pd
from datetime import datetime, timezone
import json
import socket,ssl
import urllib3
import re,shutil
import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.compute as pc
from pathlib import Path
import sys

def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
TEMP_DIR = APP_DIR / "temporary_files"
DELTA_DIR = APP_DIR / "delta_files"



ODATE_RE = re.compile(r"^/Date\((\d+)\)/$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")
def find_odata_date_columns(df):
    """Detect columns containing date-like strings in either:
      - OData JSON format:  /Date(1717286400000)/
      - ISO datetime format: 2026-06-02T00:00:00 (optional fractional seconds)
    Checks the FULL column, but vectorized — no row-by-row Python loop,
    so it's fast even at millions of rows.

    Returns a dict: {column_name: "odata" | "iso"} instead of a plain list,
    since the two formats need different conversion logic.
    """
    date_cols = {}
    for col in df.columns:
        if df[col].dtype != object:
            continue

        col_str = df[col].astype(str)

        odata_match = col_str.str.match(ODATE_RE, na=False)
        if odata_match.any():
            date_cols[col] = "odata"
            continue  # a column is one format or the other, not both

        iso_match = col_str.str.match(ISO_DATE_RE, na=False)
        if iso_match.any():
            date_cols[col] = "iso"

    return date_cols

def convert_odata_columns(df, date_cols):
    """Vectorized conversion of date columns to yyyymmdd strings.
    date_cols: dict of {column_name: "odata" | "iso"} as returned by
    find_odata_date_columns.
    """
    for col, fmt in date_cols.items():
        s = df[col]

        if fmt == "odata":
            millis = s.str.extract(r"/Date\((\d+)\)/", expand=False)
            millis_numeric = pd.to_numeric(millis, errors="coerce")
            dt = pd.to_datetime(millis_numeric, unit="ms", utc=True, errors="coerce")

        else:  # fmt == "iso"
            dt = pd.to_datetime(s, format="%Y-%m-%dT%H:%M:%S", errors="coerce")
            # fallback pass for values with fractional seconds, if any slipped through
            if dt.isna().any():
                fallback = pd.to_datetime(s, errors="coerce")
                dt = dt.fillna(fallback)

        df[col] = dt.dt.strftime("%Y%m%d")
        # if original had actual NaN/None (not a date string at all), preserve them
        df[col] = df[col].where(s.notna(), s)

    return df


def check_dns(host):
    """
    Check whether the hostname exists.
    """

    try:
        ip = socket.gethostbyname(host)
        return True

    except socket.gaierror:
        return False

def check_tcp(host, port, timeout=10):
    """
    Check whether the server is listening on the given port.
    """

    try:
        with socket.create_connection((host, port), timeout=timeout):
            # print(f"TCP Success: Connected to {host}:{port}")
            return True

    except socket.timeout:
        # print("TCP Failed: Connection timed out")
        return False

    except OSError as e:
        # print(f"TCP Failed: {e}")
        return False


def check_ssl(host, port, timeout=10):
    """
    Verify HTTPS communication.
    """

    context = ssl.create_default_context()

    # Support self-signed certificates
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    try:

        with socket.create_connection((host, port), timeout=timeout) as sock:

            with context.wrap_socket(sock, server_hostname=host) as ssl_sock:
                # print("SSL Handshake Successful")
                # print("Protocol :", ssl_sock.version())
                # print("Cipher   :", ssl_sock.cipher())

                return True

    except ssl.SSLError as e:
        # print("SSL Failed:", e)
        return False

    except Exception as e:
        # print("Connection Failed:", e)
        return False
    


urllib3.disable_warnings()

def check_credentials(host, port, username, password):

    CATALOG_URL = (
        "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/"
        "ServiceCollection?$top=1&$format=json"
    )

    url = f"https://{host}:{port}{CATALOG_URL}"

    try:

        response = requests.get(
            url,
            auth=(username,password),
            headers={"Accept":"application/json"},
            verify=False,
            timeout=10,
            allow_redirects=False
        )

        if response.status_code == 200:
            # print("Credentials are Valid")
            return True

        elif response.status_code in (401,302):
            # print("Invalid Username or Password")
            return False

        elif response.status_code == 403:
            # print("User authenticated but lacks permission")
            return False

        elif response.status_code == 404:
            # print("CATALOGSERVICE is not activated")
            return False

        else:
            # print("Unexpected Status:",response.status_code)
            return False

    except requests.exceptions.Timeout:
        # print("Request Timed Out")
        return False

    except requests.exceptions.SSLError as e:
        # print("SSL Error:",e)
        return False

    except Exception as e:
        # print("Unexpected Error:",e)
        return False


def test_connection(source_config):

    host = source_config.host
    port = str(source_config.port)
    username = source_config.username
    password = source_config.password

    conn_details ={}

    # Step 5
    conn_details["check_dns"] = False if not check_dns(host) else True

    # Step 6
    conn_details["check_tcp"]=False if not check_tcp(host, port) else True

    # Step 7
    conn_details["check_ssl"]=False if not check_ssl(host, port) else True

    # Step 8
    conn_details["check_credentials"]=False if not check_credentials(host, port, username, password) else True

    return conn_details


def fetch_source_details(source_config,service_name):
    try:
        host = source_config.host
        port = str(source_config.port)
        username = source_config.username
        password = source_config.password
        server_address = f"{host}:{port}"
        url=f"https://{server_address}/sap/opu/odata/sap/{service_name}/"
        response=requests.get(url,auth=(username,password),verify=False)
        data = xmltodict.parse(response.text)

        return data
    
    except Exception as e:
        print(e)
        return None

def get_human_readable_error(context, exc):
    """
    Converts a caught exception into a human-readable message for migration
    status reporting, instead of exposing raw system/exception text.

    `context` is a short phrase describing which step failed
    (e.g. "connecting to destination storage (ADLS)").
    """
    print(context,exc,"]]]]]]]]]]]]]]]]]]]")
    # ---- URL / request construction problems (checked before the generic
    # ---- RequestException/ConnectionError branches since these are more specific) ----
    if isinstance(exc, requests.exceptions.MissingSchema):
        reason = "the source system URL is malformed (missing http:// or https://)"
    elif isinstance(exc, requests.exceptions.InvalidSchema):
        reason = "the source system URL uses an unsupported or invalid scheme"
    elif isinstance(exc, requests.exceptions.InvalidURL):
        reason = "the source system URL is invalid (check host, port, or path for typos)"
    elif isinstance(exc, requests.exceptions.URLRequired):
        reason = "no valid URL was provided for the source system"
    elif isinstance(exc, requests.exceptions.InvalidHeader):
        reason = "an invalid request header was sent to the source system"

    # ---- Connection-level problems ----
    elif isinstance(exc, requests.exceptions.SSLError):
        reason = "an SSL/certificate error occurred while connecting to the source system"
    elif isinstance(exc, requests.exceptions.ProxyError):
        reason = "unable to connect to the source system through the configured proxy"
    elif isinstance(exc, requests.exceptions.ConnectTimeout):
        reason = "connection to the source system timed out"
    elif isinstance(exc, requests.exceptions.ConnectionError):
        reason = "unable to connect to the source system (check host, port, or network connectivity)"
    elif isinstance(exc, requests.exceptions.ReadTimeout):
        reason = "the source system did not respond in time"
    elif isinstance(exc, requests.exceptions.Timeout):
        reason = "the request to the source system timed out"
    elif isinstance(exc, requests.exceptions.TooManyRedirects):
        reason = "the request to the source system was redirected too many times"
    elif isinstance(exc, requests.exceptions.ChunkedEncodingError):
        reason = "the response from the source system was incomplete or corrupted"
    elif isinstance(exc, requests.exceptions.HTTPError):
        reason = "the source system returned an error response"
    elif isinstance(exc, requests.exceptions.RequestException):
        reason = "an error occurred while communicating with the source system"

    # ---- Low-level errors that can occasionally escape unwrapped by requests ----
    elif isinstance(exc, UnicodeError):
        reason = "the source system URL contains invalid characters"
    elif isinstance(exc, (json.JSONDecodeError, ValueError)):
        reason = "received an invalid or unexpected response format from the source system"
    elif isinstance(exc, KeyError):
        reason = f"an expected field ({exc}) was missing in the source system response"
    elif isinstance(exc, PermissionError):
        reason = "permission was denied while accessing local files or destination storage"
    elif isinstance(exc, FileNotFoundError):
        reason = "a required file or folder could not be found"
    elif isinstance(exc, IsADirectoryError):
        reason = "expected a file but found a directory"
    elif isinstance(exc, OSError):
        reason = "a file system error occurred while reading or writing local files"
    elif "azure" in type(exc).__module__.lower():
        reason = "an error occurred while communicating with the destination storage (ADLS)"
    else:
        reason = "an unexpected error occurred"

    return f"Failed while {context}: {reason}"


def update_migration_status(migration_session_id,connection_id,job_name,tbl_name,rows_processed,
                            delta_token,status,message):

    datasyncher_url = f"https://qa.datasyncher.com/api/v1/internal/migration-progress-update/{migration_session_id}/"
    payload = {
        "migration_session_id": migration_session_id,
        "connection_id": connection_id,
        "job_name": job_name,
        "table_name": tbl_name,
        "rows_processed": rows_processed,
        "delta_token": delta_token,
        "status": status,
        "message": message,
    }

    print("payload",payload)
    INTERNAL_API_KEY = "datasyncher_internal_ws_2026"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {INTERNAL_API_KEY}"   # if your API requires authentication
    }

    # try:
    #     datasyn_response = requests.post(
    #         datasyncher_url,
    #         json=payload,
    #         headers=headers
    #     )

    # except Exception as e:
    #     print(str(e))

def metadata(url,source_config):

    """
    this functions fetches the ODATA meta data url and sents the response to datasynchor
    """

    # first create metadata
    username = source_config.username
    password = source_config.password
    response = requests.get(url, auth=(username, password),verify=False)
    if response.status_code == 200:
        metadata_dict = xmltodict.parse(response.text)
        return metadata_dict
    else:
        return None


def fetch_metadata(url,source_config):

    """
    this functions fetches the ODATA meta data url and created the contents to be stored inside sap.partfile.metadata
    """

    # first create metadata
    username = source_config.username
    password = source_config.password
    response = requests.get(url, auth=(username, password),verify=False)
    if response.status_code == 200:
        metadata_dict = xmltodict.parse(response.text)
        
        # Find the main entity (skip DeltaLinks entity)
        entity_types = metadata_dict["edmx:Edmx"]["edmx:DataServices"]["Schema"]["EntityType"]
    
        if not isinstance(entity_types, list):
            entity_types = [entity_types]

        for entity in entity_types:
            if not entity["@Name"].startswith("DeltaLinks"):
                main_entity = entity
                break

        # primary keys
        property_refs = main_entity["Key"]["PropertyRef"]
        if isinstance(property_refs,list):
            primary_keys = [key["@Name"]for key in property_refs]
        else:
            primary_keys = property_refs["@Name"]

        columns=[]

        for entity in main_entity["Property"]:
            if entity["@Name"] in ["ODQ_CHANGEMODE","ODQ_ENTITYCNTR"]:
                continue
            column={
                "name":entity["@Name"],
                "dataType":EDM_TYPE_MAPPING.get(entity["@Type"])
            }

            if EDM_TYPE_MAPPING.get(entity["@Type"])=="NVARCHAR":
                column["length"] = 8


            # if maxlength is provided then overwrite column[Length]
            if "@MaxLength" in entity:
                column["length"] = int(entity["@MaxLength"])
                
            if "@Precision" in entity:
                column["precision"]= int(entity["@Precision"])
            
            if "@Nullable" in entity:
                column["nullable"] = str(entity["@Nullable"]).lower() == "true"
                
            if "@Scale" in entity:
                column["scale"]=int(entity["@Scale"])

            if entity["@Name"] in primary_keys:
                column["primaryKey"]= True

            if PROPERTIES.get(column["dataType"]):
                column["properties"]=PROPERTIES.get(column["dataType"])

            columns.append(column)
                
        columns.extend([
            {
                "name": "__operation_type",
                "dataType": "NVARCHAR",
                "length": 1,
                "nullable": True,
                "properties": [
                    {
                        "namespace": "com.sap.vflow",
                        "name": "vtype-template",
                        "value": "string"
                    },
                    {
                        "namespace": "com.sap.rms",
                        "name": "semanticType",
                        "value": "_change_mode"
                    }
                ]
            },
            {
                "name": "__sequence_number",
                "dataType": "DECIMAL",
                "precision": 20,
                "nullable": True,
                "properties": [
                    {
                        "namespace": "com.sap.vflow",
                        "name": "vtype-template",
                        "value": "string"
                    },
                    {
                        "namespace": "com.sap.rms",
                        "name": "semanticType",
                        "value": "_change_mode"
                    }
                ]
            },
            {
                "name": "__timestamp",
                "dataType": "TIMESTAMP",
                "nullable": True,
                "properties": [
                    {
                        "namespace": "com.sap.vflow",
                        "name": "vtype-template",
                        "value": "timestamp"
                    },
                    {
                        "namespace": "com.sap.rms",
                        "name": "semanticType",
                        "value": "_change_mode"
                    }
                ]
            },
            {
                "name": "__delta_pointer",
                "dataType": "NVARCHAR",
                "length": 30,
                "nullable": True,
                "properties": [
                    {
                        "namespace": "com.sap.vflow",
                        "name": "vtype-template",
                        "value": "string"
                    },
                    {
                        "namespace": "com.sap.rms",
                        "name": "semanticType",
                        "value": "_change_mode"
                    }
                ]
            },
            {"name": "_SPECT_DELETED",
             "dataType": "BOOLEAN",
             "nullable": False
            }
        ])


        contents= {
        "version": metadata_dict["edmx:Edmx"].get("@Version"),
        "format": "PARQUET",
        "properties":[
            {
            "namespace": "datasynchor.com",
            "name": "loadType",
            "value": "REPLICATE"
            },
            {
            "namespace": "datasynchor.com",
            "name": "replicationTaskID",
            "value": "3a5ca2a6-a63e-4a28-b3cb-6e0fea66f93b"
            },
            {
            "namespace": "datasynchor.com",
            "name": "groupDeltaFilesBy",
            "value": "NONE"
            }
        ],
        "columns": columns
        }

        return contents # contents to be saved in sap.partfile.metadata 

    else:
        print("Request failed:", response.status_code)
        return None

def load_data_allsrc(data_url,source_config):
    username = source_config.username
    password = source_config.password
    response = requests.get(data_url,auth=(username,password),verify=False)
    data = response.json()
    return data

def _build_arrow_schema(metadata_bytes):
    """Build a pyarrow schema from the metadata JSON (same types/precision/scale)."""
    types = {
        "SHORT": pa.int16(), "INTEGER": pa.int32(), "LONG": pa.int64(),
        "FLOAT": pa.float32(), "DOUBLE": pa.float64(),
        "BOOLEAN": pa.bool_(), "BINARY": pa.binary(),
        "TIMESTAMP": pa.timestamp("us"), "INSTANT": pa.timestamp("us", tz="UTC"),
    }
    def _pa_type(c):
        if c["dataType"] == "DECIMAL":
            return pa.decimal128(c.get("precision", 38), c.get("scale", 0))
        return types.get(c["dataType"], pa.string())  # NVARCHAR, STRING

    return pa.schema([pa.field(c["name"], _pa_type(c))
                      for c in json.loads(metadata_bytes)["columns"]])


def _to_arrow_table(df, schema):
    """Cast every column to the metadata type. Missing/empty columns become typed nulls."""
    arrays = []
    for f in schema:
        if f.name in df.columns:
            arr = pa.Array.from_pandas(df[f.name])
            # empty strings can't be cast to numbers/booleans -> treat as null
            if pa.types.is_string(arr.type) and not pa.types.is_string(f.type):
                arr = pc.if_else(pc.equal(arr, ""), pa.scalar(None, pa.string()), arr)
            if arr.null_count == len(arr):
                arrays.append(pa.nulls(len(df), f.type))   # fully empty -> typed nulls
            else:
                arrays.append(arr.cast(f.type))
        else:
            arrays.append(pa.nulls(len(df), f.type))
    return pa.Table.from_arrays(arrays, schema=schema)

def _fetch_and_write_local_parquet(host, port, username, password,
                                    service_name, entity_name,
                                    output_dir, file_rec_count,
                                    compression_type, timestamp,
                                    file_name_base,pagination_count,metadata_bytes):
    """
    Streams OData pages from SAP and writes local parquet file(s),
    keeping memory bounded to one page (+ a partial buffer) at a time

    - If file_rec_count is given: writes multiple files, each with
      exactly file_rec_count rows (last file may have fewer).
    - If file_rec_count is not given: writes a single file using
      ParquetWriter row-group appends (still one page in memory at a time).

    Returns: list of (file_name, local_path) written successfully.
    Raises on any failure — caller is responsible for cleanup/rollback.
    Guarantees: no partial/incomplete local file is left behind on failure.
    """
    initial_url = f"https://{host}:{port}/sap/opu/odata/sap/{service_name}/{entity_name}/?$format=json"
    headers = {"Prefer": f"odata.track-changes,odata.maxpagesize={pagination_count}"}

    parquet_files = []
    writer = None            # used only in the no-file_rec_count branch
    single_file_path = None
    buffer_df = pd.DataFrame()  # used only in the file_rec_count branch
    file_num = 1

    arrow_schema = _build_arrow_schema(metadata_bytes)
    def _transform(page_records):
        page_df = pd.DataFrame(page_records)
        page_df.drop(columns=["__metadata", "ODQ_CHANGEMODE", "ODQ_ENTITYCNTR"],
                     errors="ignore", inplace=True)
        date_cols = find_odata_date_columns(page_df)
        page_df = convert_odata_columns(page_df, date_cols)
        page_df["__operation_type"] = "L"
        page_df["__sequence_number"] = float("nan")
        page_df["__timestamp"] = timestamp
        page_df["__delta_pointer"] = "initial"

        # Initial load records are never deleted
        page_df["_SPECT_DELETED"] = False
        return page_df

    try:
        with requests.Session() as session:
            session.auth = (username, password)
            session.headers.update(headers)

            while initial_url:
                response = session.get(initial_url, verify=False)
                if response.status_code != 200:
                    raise Exception(f"SAP fetch failed: {response.status_code},{response.text}")

                data = response.json()
                page_df = _transform(data["d"]["results"])

                if file_rec_count and file_rec_count > 0:
                    # ---- CASE 1: fixed record count per file ----
                    buffer_df = pd.concat([buffer_df, page_df], ignore_index=True)

                    while len(buffer_df) >= file_rec_count:
                        chunk = buffer_df.iloc[:file_rec_count]
                        buffer_df = buffer_df.iloc[file_rec_count:].reset_index(drop=True)

                        file_name = generate_filename(file_name_base, file_num)
                        parquet_path = os.path.join(output_dir, file_name)
                        pq.write_table(_to_arrow_table(chunk, arrow_schema), parquet_path, compression=compression_type)
                        parquet_files.append((file_name, parquet_path))
                        file_num += 1

                else:
                    # ---- CASE 2: no file_rec_count -> single file, streamed row groups ----
                    if single_file_path is None:
                        file_name = generate_filename(file_name_base, 1)
                        single_file_path = os.path.join(output_dir, file_name)
                        parquet_files.append((file_name, single_file_path))

                    table = _to_arrow_table(page_df, arrow_schema)
                    if writer is None:
                        writer = pq.ParquetWriter(single_file_path, table.schema,
                                                   compression=compression_type)
                    writer.write_table(table)

                initial_url = data["d"].get("__next")

        # flush any leftover rows in the buffered (file_rec_count) case
        if file_rec_count and file_rec_count > 0 and len(buffer_df) > 0:
            file_name = generate_filename(file_name_base, file_num)
            parquet_path = os.path.join(output_dir, file_name)
            pq.write_table(_to_arrow_table(buffer_df, arrow_schema), parquet_path, compression=compression_type)
            parquet_files.append((file_name, parquet_path))

        if writer is not None:
            writer.close()
            writer = None

        return parquet_files

    except Exception:
        # make sure nothing partial survives locally
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

        temp_parent = os.path.dirname(output_dir)

        if os.path.exists(temp_parent):
            shutil.rmtree(temp_parent)
        raise

def _cleanup_local_dir(local_dir, stop_dir=TEMP_DIR):
    """
    Remove local_dir if empty, then remove empty parent directories,
    but stop before deleting the stop_dir itself.
    """
    current = os.path.abspath(local_dir)
    stop_path = os.path.abspath(stop_dir)

    while (
        current != stop_path
        and os.path.isdir(current)
        and not os.listdir(current)
    ):
        try:
            os.rmdir(current)
        except Exception as e:
            print(f"Warning: failed to remove local folder '{current}': {e}")
            break

        current = os.path.dirname(current)


def load_initial(src_config, destination_type, destination_config, src_details,details_for_status_update):

    service_name = src_details.name
    entity_name = src_details.entity_name

    host = src_config.host
    port = src_config.port
    username = src_config.username
    password = src_config.password

    migration_session_id = details_for_status_update["migration_session_id"]
    job_name = details_for_status_update["job_name"]
    connection_id = details_for_status_update["connection_id"]

    # variables passed to datasyncher 
    message = None
    delta_token = None
    status = None 
    rows_processed  = 0

    try:
        service_client = adls_connection(
            destination_config["TENANT_ID"],
            destination_config["CLIENT_ID"],
            destination_config["CLIENT_SECRET"],
            destination_config["CONTAINER_NAME"],
            destination_config["STORAGE_ACCOUNT_NAME"],
        )

    except Exception as e:

        status = "failed"
        message = get_human_readable_error("connecting to destination storage (ADLS)", e)
        rows_processed = 0

        update_migration_status(
            migration_session_id,
            connection_id,
            job_name,
            entity_name,
            rows_processed,
            delta_token,
            status,
            message
        )
        return False

    if src_details.load_method == "delta_only":
        initial_url = f"https://{host}:{port}/sap/opu/odata/sap/{service_name}/{entity_name}/?$format=json"
        headers = {"Prefer": "odata.track-changes,odata.maxpagesize=1"}
        with requests.Session() as session:
            session.auth = (username, password)
            session.headers.update(headers)
            response = session.get(initial_url, verify=False)
            if response.status_code != 200:
                status = "failed"
                message = f"Failed while initializing delta on the sap system: received status code {response.status_code} from SAP"
                rows_processed = 0

                update_migration_status(
                    migration_session_id,
                    connection_id,
                    job_name,
                    entity_name,
                    rows_processed,
                    delta_token,
                    status,
                    message
                )
                return None
            
    else:

        time = datetime.strptime(src_details.utc_time, "%Y-%m-%d %H:%M:%S")
        file_rec_count = src_details.file_rec_count
        folder_name = src_details.folder_name if src_details.folder_name else f"{service_name}"
        initial_output_dir = os.path.join(folder_name, "initial")
        local_output_dir = TEMP_DIR / folder_name / "initial"
        os.makedirs(local_output_dir, exist_ok=True)
        compression_type = src_details.compress_type if src_details.compress else None
        pagination_count =src_details.pagination_count if src_details.pagination_count else 200000
        file_name_base = generate_filename_base(time)


        # ---- Step 1: fetch metadata ----
        try:
            meta_url = f"https://{host}:{port}/sap/opu/odata/sap/{service_name}/$metadata"
            meta_data = fetch_metadata(meta_url, src_config)
            if meta_data is None:
                raise Exception("metadata request failed")
            meta_file_name = "sap.partfile.metadata"
            metadata_bytes = json.dumps(meta_data, indent=4).encode("utf-8")

        except Exception as e:
            print(f"Failed to fetch metadata: {e}")
            status = "failed"
            message = get_human_readable_error("fetching metadata from the source system", e)
            rows_processed = 0

            update_migration_status(migration_session_id,connection_id,job_name,entity_name,rows_processed,
                                        delta_token,status,message)
            
            return None

        # ---- Step 2: fetch + write locally FIRST ----
        try:
            parquet_files = _fetch_and_write_local_parquet(
                host, port, username, password,
                service_name, entity_name,
                local_output_dir, file_rec_count,
                compression_type, time, file_name_base,pagination_count,metadata_bytes
            )
        except Exception as e:
            print(f"Failed to fetch/write local parquet files: {e}")

            status = "failed"
            message =  get_human_readable_error("fetching data from the source system", e)
            rows_processed = 0

            update_migration_status(
                migration_session_id,
                connection_id,
                job_name,
                entity_name,
                rows_processed,
                delta_token,
                status,
                message
            )
            return None


        # ---- Step 3: only now delete the old ADLS folder, since we know we have ----
        # ----          a complete, valid replacement ready to upload             ----
        try:
            delete_adls_directory(service_client,destination_config["CONTAINER_NAME"],initial_output_dir)
        except Exception as e:
            print(f"Failed to delete existing ADLS directory: {e}")
            for _, path in parquet_files:
                if os.path.exists(path):
                    os.remove(path)
            #removing the folder        
            _cleanup_local_dir(local_output_dir)
            status = "failed"
            message = get_human_readable_error("deleting the existing directory in destination storage (ADLS)", e)
            rows_processed = 0

            update_migration_status(migration_session_id,connection_id,job_name,entity_name,rows_processed,
                                        delta_token,status,message)
            return None

        # ---- Step 4: upload everything; roll back ADLS on any failure ----
        try:
            # write_adls(service_client,destination_config["CONTAINER_NAME"],initial_output_dir, meta_file_name, metadata_bytes)

            total_files = len(parquet_files)
            rows_processed = 0

            for idx, (file_name, parquet_path) in enumerate(parquet_files, start=1):
                # get row count before the file is written/removed
                file_row_count = pq.ParquetFile(parquet_path).metadata.num_rows

                with open(parquet_path, "rb") as f:
                    content = f.read()
                # write_adls(service_client,destination_config["CONTAINER_NAME"],initial_output_dir, file_name, content)
                # os.remove(parquet_path)

                rows_processed += file_row_count
                status = "completed" if idx == total_files else "in_progress"

                # status update after each file is written to ADLS
                update_migration_status(
                    migration_session_id,
                    connection_id,
                    job_name,
                    entity_name,
                    rows_processed,
                    delta_token,
                    status,
                    message
                )

            #removing the folder        
            # _cleanup_local_dir(local_output_dir)

            # only the very last ADLS write — the "commit" marker
            # write_adls(service_client,destination_config["CONTAINER_NAME"],initial_output_dir, "_SUCCESS", b"")

        except Exception as e:
            print(f"Failed to write initial load files to ADLS: {e}")
            for _, parquet_path in parquet_files:
                if os.path.exists(parquet_path):
                    os.remove(parquet_path)
            #removing the folder        
            _cleanup_local_dir(local_output_dir)
            # roll back partial upload so we don't leave a half-written folder
            # (note: old data is already gone at this point, since delete happened
            # in Step 3 — this is an inherent risk of "delete and load" as a strategy)
            try:
                delete_adls_directory(service_client,destination_config["CONTAINER_NAME"],initial_output_dir)
            except Exception as cleanup_err:
                print(f"Warning: failed to roll back ADLS directory: {cleanup_err}")

            status = "failed"
            message = get_human_readable_error("uploading initial load files to destination storage (ADLS)", e)
            rows_processed = 0

            update_migration_status(migration_session_id,connection_id,job_name,entity_name,rows_processed,
                                        delta_token,status,message)    

            return None
        
    # ---- delta token (unchanged) ----
    if src_details.delta_enabled:
        deltalinks_url = f"https://{host}:{port}/sap/opu/odata/sap/{service_name}/DeltaLinksOf{entity_name}/?$format=json"
        deltalinks_url_resp = requests.get(deltalinks_url, auth=(username, password), verify=False)
        delta_linksresp_json = deltalinks_url_resp.json()
        next_delta_url = delta_linksresp_json["d"]["results"][0]["ChangesAfter"]["__deferred"]["uri"]
        delta_token_match = re.search(r"\('([^']+)'\)", next_delta_url)
        delta_token = delta_token_match.group(1) if delta_token_match else None

    update_migration_status(migration_session_id,connection_id,job_name,entity_name,rows_processed,
                            delta_token,status,message)

    return True


def _fetch_and_write_local_delta_parquet(host, port, username, password,
                                    service_name, entity_name,delta_token,
                                    output_dir, file_rec_count,
                                    compression_type, timestamp,
                                    file_name_base,pagination_count,metadata_bytes):
    """
    Streams OData pages from SAP and writes local parquet file(s),
    keeping memory bounded to one page (+ a partial buffer) at a time.

    - If file_rec_count is given: writes multiple files, each with
      exactly file_rec_count rows (last file may have fewer).
    - If file_rec_count is not given: writes a single file using
      ParquetWriter row-group appends (still one page in memory at a time).

    Returns: list of (file_name, local_path) written successfully.
    Raises on any failure — caller is responsible for cleanup/rollback.
    Guarantees: no partial/incomplete local file is left behind on failure.
    """

    # print(file_rec_count,"----------------------------------")
    current_delta_token = delta_token
    url=f"https://{host}:{port}/sap/opu/odata/sap/{service_name}/{entity_name}?&!deltatoken='{current_delta_token}'&$format=json"

    headers = {
        "Prefer": f"odata.maxpagesize={pagination_count}"
    }

    parquet_files = []
    writer = None            # used only in the no-file_rec_count branch
    single_file_path = None
    buffer_df = pd.DataFrame()  # used only in the file_rec_count branch
    file_num = 1

    arrow_schema = _build_arrow_schema(metadata_bytes)
    def _transform(page_records):
        page_df = pd.DataFrame(page_records)
        date_cols = find_odata_date_columns(page_df)
        page_df = convert_odata_columns(page_df, date_cols)
        # Create operation type before dropping ODQ_CHANGEMODE
        if "ODQ_CHANGEMODE" in page_df.columns:
            page_df["__operation_type"] = page_df["ODQ_CHANGEMODE"].eq("U").map({
                True: "A",
                False: "X"
            })
        else:
            page_df["__operation_type"] = "A"

        # X = deleted, everything else = not deleted
        page_df["_SPECT_DELETED"] = page_df["__operation_type"].eq("X")

        page_df.drop(
            columns=["__metadata", "ODQ_CHANGEMODE", "ODQ_ENTITYCNTR"],
            errors="ignore",
            inplace=True
        )
        page_df["__sequence_number"] = float("nan")
        page_df["__timestamp"] = timestamp
        page_df["__delta_pointer"] = delta_token

        return page_df

    try:
        with requests.Session() as session:
            session.auth = (username, password)
            session.headers.update(headers)
            while url:
                print("url fetching started,",datetime.now())
                response = session.get(url, verify=False)
                print("url fetching started,",datetime.now())
                if response.status_code != 200:
                    raise Exception(f"SAP fetch failed: {response.status_code}")

                data = response.json()
                page_df = _transform(data["d"]["results"])

                if file_rec_count and file_rec_count > 0:
                    # ---- CASE 1: fixed record count per file ----
                    buffer_df = pd.concat([buffer_df, page_df], ignore_index=True)

                    while len(buffer_df) >= file_rec_count:
                        chunk = buffer_df.iloc[:file_rec_count]
                        buffer_df = buffer_df.iloc[file_rec_count:].reset_index(drop=True)
    
                        file_name = generate_filename(file_name_base, file_num)
                        parquet_path = os.path.join(output_dir, file_name)
                        pq.write_table(_to_arrow_table(chunk, arrow_schema), parquet_path, compression=compression_type)
                        parquet_files.append((file_name, parquet_path))
                        file_num += 1

                else:
                    # ---- CASE 2: no file_rec_count -> single file, streamed row groups ----
                    if single_file_path is None:
                        file_name = generate_filename(file_name_base, 1)
                        single_file_path = os.path.join(output_dir, file_name)
                        parquet_files.append((file_name, single_file_path))

                    table = _to_arrow_table(page_df, arrow_schema)
                    if writer is None:
                        writer = pq.ParquetWriter(single_file_path, table.schema,
                                                   compression=compression_type)
                    writer.write_table(table)

                url = data["d"].get("__next")

        # flush any leftover rows in the buffered (file_rec_count) case
        if file_rec_count and file_rec_count > 0 and len(buffer_df) > 0:
            file_name = generate_filename(file_name_base, file_num)
            parquet_path = os.path.join(output_dir, file_name)
            pq.write_table(_to_arrow_table(buffer_df, arrow_schema), parquet_path, compression=compression_type)
            parquet_files.append((file_name, parquet_path))

        if writer is not None:
            writer.close()
            writer = None

        return parquet_files
        
    except Exception:
        # make sure nothing partial survives locally
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        temp_parent = os.path.dirname(output_dir)

        if os.path.exists(temp_parent):
            shutil.rmtree(temp_parent)

        raise


def load_delta(src_config, destination_type, destination_config, src_details,details_for_status_update):
    service_name = src_details.name
    entity_name = src_details.entity_name
    host = src_config.host
    port = src_config.port
    username = src_config.username
    password = src_config.password
    
    time = datetime.strptime(src_details.utc_time, "%Y-%m-%d %H:%M:%S")
    file_rec_count = src_details.file_rec_count
    folder_name = src_details.folder_name if src_details.folder_name else f"{service_name}"
    # directory in adls 
    if src_details.partition_data_by_date:
        delta_output_dir = os.path.join(
            folder_name,
            "delta",
            time.strftime("%Y-%m-%d")
        )
    else:
        delta_output_dir = os.path.join(folder_name, "delta")
    # directory for creating local files 
    local_output_dir = DELTA_DIR / folder_name / str(src_details.delta_token)
    os.makedirs(local_output_dir, exist_ok=True)
    compression_type = src_details.compress_type if src_details.compress else None
    pagination_count =src_details.pagination_count if src_details.pagination_count else 200000
    file_name_base = generate_filename_base(time)

    migration_session_id = details_for_status_update["migration_session_id"]
    job_name = details_for_status_update["job_name"]
    connection_id = details_for_status_update["connection_id"]

    # variables passed to datasyncher 
    message = None
    delta_token = None
    status = None 
    rows_processed  = 0

    try:
        service_client = adls_connection(
            destination_config["TENANT_ID"],
            destination_config["CLIENT_ID"],
            destination_config["CLIENT_SECRET"],
            destination_config["CONTAINER_NAME"],
            destination_config["STORAGE_ACCOUNT_NAME"],
        )

    except Exception as e:

        status = "failed"
        message = get_human_readable_error("connecting to destination storage (ADLS)", e)
        rows_processed = 0

        update_migration_status(
            migration_session_id,
            connection_id,
            job_name,
            entity_name,
            rows_processed,
            delta_token,
            status,
            message
        )
        return False # return false because the no need to retry again . 

 # ---------------------------------------------------------
    # Step 1: Remove ADLS files corresponding to existing
    #         local files BEFORE creating new local files
    # ---------------------------------------------------------
    try:

        existing_local_dir = (
            DELTA_DIR /
            folder_name /
            str(src_details.delta_token)
        )

        print(
            f"Checking existing local delta directory: "
            f"{existing_local_dir}"
        )

        if os.path.isdir(existing_local_dir):

            for file_name in os.listdir(existing_local_dir):

                local_file_path = os.path.join(
                    existing_local_dir,
                    file_name
                )

                # Only process files
                if os.path.isfile(local_file_path):

                    print(
                        f"Found existing local file: {file_name}"
                    )

                    print(
                        f"Deleting ADLS file: "
                        f"{delta_output_dir}/{file_name}"
                    )

                    delete_adls_file(
                        service_client,
                        destination_config["CONTAINER_NAME"],
                        delta_output_dir,
                        file_name
                    )

                    os.remove(local_file_path)
            # clean directory after deleting from adls 
            _cleanup_local_dir(local_output_dir,DELTA_DIR)
        else:
            print(
                "No existing local delta folder found. "
                "Nothing to delete from ADLS."
            )

    except Exception as e:

        print(
            f"Failed to remove existing ADLS files: {e}"
        )

        status = "failed"

        message = "removing existing delta files from destination storage (ADLS)",


        update_migration_status(
            migration_session_id,
            connection_id,
            job_name,
            entity_name,
            0,
            delta_token,
            status,
            message
        )

        # IMPORTANT:
        # Do not create/fetch new local files if the old ADLS
        # file cleanup failed.
        return None

    # ---------------------------------------------------------
    # Step 2: Now create the local directory and fetch data
    # ---------------------------------------------------------
    os.makedirs(
        local_output_dir,
        exist_ok=True
    )

    # ---- Step 3: fetch metadata ----
    try:
        meta_url = f"https://{host}:{port}/sap/opu/odata/sap/{service_name}/$metadata"
        meta_data = fetch_metadata(meta_url, src_config)
        if meta_data is None:
            raise Exception("metadata request failed")
        meta_file_name = "sap.partfile.metadata"
        metadata_bytes = json.dumps(meta_data, indent=4).encode("utf-8")
    except Exception as e:
        print(f"Failed to fetch metadata: {e}")
        status = "failed"
        message = get_human_readable_error("fetching metadata from the source system", e)
        rows_processed = 0

        update_migration_status(
            migration_session_id,
            connection_id,
            job_name,
            entity_name,
            rows_processed,
            delta_token,
            status,
            message
        )
        return None

    # ---- Step 4: fetch + write locally FIRST (bounded memory, all-or-nothing) ----
    # Deliberately done before touching ADLS: if SAP fetch fails, the existing
    # good data in ADLS is left untouched instead of being wiped for nothing.
    try:
        print("fetching started ", datetime.now())
        parquet_files = _fetch_and_write_local_delta_parquet(
            host, port, username, password,
            service_name, entity_name,src_details.delta_token,
            local_output_dir, file_rec_count,
            compression_type, time, file_name_base,pagination_count,metadata_bytes
        )
        print("fetching ended ", datetime.now())
    except Exception as e:
        print(f"Failed to fetch/write local parquet files: {e}")
        status = "failed"
        message = get_human_readable_error("fetching delta data from the source system ", e)
        rows_processed = 0

        update_migration_status(
            migration_session_id,
            connection_id,
            job_name,
            entity_name,
            rows_processed,
            delta_token,
            status,
            message
        )
        return None


    # ---- Step 5: upload everything; roll back ADLS on any failure ----
    try:
        print("writing started ", datetime.now())
        # write_adls(service_client,destination_config["CONTAINER_NAME"],delta_output_dir, meta_file_name, metadata_bytes)

        total_files = len(parquet_files)
        rows_processed = 0

        for idx, (file_name, parquet_path) in enumerate(parquet_files, start=1):
            # get row count before the file is written/removed
            file_row_count = pq.ParquetFile(parquet_path).metadata.num_rows

            with open(parquet_path, "rb") as f:
                content = f.read()
            # write_adls(service_client,destination_config["CONTAINER_NAME"],delta_output_dir, file_name, content)
            # os.remove(parquet_path)

            rows_processed += file_row_count
            status = "completed" if idx == total_files else "in_progress"

            print("writing ended ", datetime.now())
            # status update after each file is written to ADLS
            print("status started ", datetime.now())
            update_migration_status(
                migration_session_id,
                connection_id,
                job_name,
                entity_name,
                rows_processed,
                delta_token,
                status,
                message
            )
            print("status ended ", datetime.now())

        #removing the folder        
        # _cleanup_local_dir(local_output_dir,DELTA_DIR)
        
        #fetching the next token
        deltalinks_url = f"https://{host}:{port}/sap/opu/odata/sap/{service_name}/DeltaLinksOf{entity_name}/?$format=json"
        deltalinks_url_resp = requests.get(deltalinks_url, auth=(username, password), verify=False)
        delta_linksresp_json = deltalinks_url_resp.json()
        # Get all results
        tokens_list = delta_linksresp_json.get("d", {}).get("results", [])

        if tokens_list:
            # Find the record with the latest CreatedAt
            latest_item = max(
                tokens_list,
                key=lambda x: int(x["CreatedAt"][6:-2])
            )

            latest_delta_token = latest_item["DeltaToken"]

            print("Latest CreatedAt:", latest_item["CreatedAt"])
            print("Latest DeltaToken:", latest_delta_token)
        else:
            latest_delta_token = None
            print("No delta records found.")
        
        delta_token = latest_delta_token
        status = "completed"

        # final status update, carrying the new delta token
        update_migration_status(
            migration_session_id,
            connection_id,
            job_name,
            entity_name,
            rows_processed,
            delta_token,
            status,
            message
        )
        return True
    except Exception as e:
        print(f"Failed to write delta load files to ADLS: {e}")
        status = "failed"
        message = get_human_readable_error("uploading delta load files to destination storage (ADLS)", e)
        rows_processed = 0

        update_migration_status(
            migration_session_id,
            connection_id,
            job_name,
            entity_name,
            rows_processed,
            delta_token,
            status,
            message
        )
        return None    

    





