# services/sftp_to_snowflake_service.py
"""
SFTP to Snowflake Migration Service - Consolidating all SFTP to Snowflake functionality.
Reuses staging, GCS upload, table creation, and MERGE helpers from amazon_s3_service.py.
"""

from __future__ import annotations
import io
import os
import csv
import json
import time
import socket
import logging
import traceback
import tempfile
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple, Set

import pandas as pd
from fastapi import HTTPException
from google.cloud import storage
import xml.etree.ElementTree as ET

# Import models
from models.sftp_snowflake_models import SFTPSnowflakeMigrationRequest
from models.sftp_source_models import SFTPListingTables

# Import S3 Snowflake helper constants & functions
from services.amazon_s3.service import (
    GCS_BUCKET_NAME,
    resolve_gcs_bucket_name,
    resolve_snowflake_storage_integration,
    sf_connect,
    normalize_and_log_snowflake_identifier,
    ensure_tables,
    upload_chunk_to_gcs,
    copy_into_staging_from_gcs_batch,
    merge_staging_into_final,
    align_chunk_to_table_column_order,
    reorder_chunk_columns_for_snowflake,
    _snowflake_table_exists,
    Q,
    sanitize_identifier,
    staging_name,
    parse_datetime_from_db,
    MigrationFailureTracker,
    normalize_delimiter,
    normalize_quotechar,
    normalize_escapechar,
    normalize_line_separator,
    normalize_skipheaderlines,
    assert_no_fully_empty_rows,
    drop_fully_empty_business_rows,
    drop_rows_with_missing_trailing_columns,
    add_metadata_to_chunk,
    _unwrap_single_key_json_container,
    _validate_json_record_keys,
    _is_valid_parquet_file,
    _is_valid_avro_container,
    _debug_count_bad_csv_lines,
    _first_non_whitespace_char,
    _validate_delimited_text,
    parse_xml_packed,
    parse_xml_unpacked,
    parse_xml_record_packed,
    parse_xml_record_unpacked,
    infer_columns_from_df,
    sanitize_column_name,
    suggest_common_table_name,
    extract_date_from_filename,
)

# Import SFTP source client helpers
from services.sftp.source import (
    _validate_sftp_parquet_signature,
    _open_sftp_client,
    _close_sftp_client,
    _effective_root_folder,
    _sftp_listing_base_folder,
    _sftp_listing_root_folder,
    _walk_sftp_files,
    SFTPSourceService,
)

# Import utilities
from utils.database_utils import (
    postgres_connection,
    update_table_status_snowflake,
    data_transfer_logs,
    get_last_checked_dates_and_fields,
    get_migration_record_id,
)
from utils.error_utils import (
    handle_service_error, 
    log_migration_completion,
    insert_tool_log,
    should_include_operation_counts,
)
from utils.email_templates import create_connection_failure_email
from utils.email_utils import EmailConfiguration, send_email_with_retry
from utils.celery_cleanup_utils import handle_migration_completion
from utils.simple_progress_tracker import SimpleProgressTracker
from utils.s3_utils import log, table_from_file_key, log_prefix
from utils.gcp_utils import (
    gcs_client,
    ensure_gcs_storage_integration,
    get_gcp_service_account,
    ensure_bucket_iam_for_integration,
    assert_allowed_prefix_matches,
)

logger = logging.getLogger(__name__)


def _read_sftp_head_bytes(sftp, key: str, max_bytes: int = 4096) -> bytes:
    """Read a small prefix of an SFTP file for lightweight format checks."""
    if max_bytes <= 0:
        return b""
    try:
        with sftp.open(key, "rb") as f:
            return f.read(max_bytes)
    except Exception as read_err:
        log(f"[SFTP][TYPE_CHECK] Failed to read head bytes for '{key}': {read_err}")
        return b""


def _reset_binary_stream(stream) -> None:
    if hasattr(stream, "seek") and callable(getattr(stream, "seek", None)):
        try:
            stream.seek(0)
        except Exception:
            pass


def _stream_text_prefix(stream, max_bytes: int) -> str:
    _reset_binary_stream(stream)
    data = stream.read(max_bytes)
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="ignore")
    return str(data or "")


def _stream_json_values(stream):
    decoder = json.JSONDecoder()
    buffer = ""
    _reset_binary_stream(stream)
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        if isinstance(chunk, bytes):
            buffer += chunk.decode("utf-8", errors="ignore")
        else:
            buffer += str(chunk)
        while True:
            buffer = buffer.lstrip()
            if not buffer:
                break
            try:
                value, offset = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break
            yield value
            buffer = buffer[offset:]


def _stream_json_array_items(stream):
    decoder = json.JSONDecoder()
    buffer = ""
    started = False
    finished = False
    _reset_binary_stream(stream)
    while not finished:
        chunk = stream.read(65536)
        if not chunk:
            finished = True
        else:
            if isinstance(chunk, bytes):
                buffer += chunk.decode("utf-8", errors="ignore")
            else:
                buffer += str(chunk)

        while True:
            buffer = buffer.lstrip()
            if not buffer:
                break
            if not started:
                if buffer[0] != "[":
                    raise ValueError("JSON content is not an array")
                started = True
                buffer = buffer[1:]
                continue
            if buffer[0] == "]":
                finished = True
                buffer = buffer[1:]
                break
            if buffer[0] == ",":
                buffer = buffer[1:]
                continue
            try:
                value, offset = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break
            yield value
            buffer = buffer[offset:]


def _stream_without_footer(stream, skip_footer_lines: int):
    footer_lines = max(int(skip_footer_lines or 0), 0)
    if footer_lines <= 0:
        return stream, None

    spool = tempfile.SpooledTemporaryFile(mode="w+b", max_size=5 * 1024 * 1024)
    pending = deque()
    _reset_binary_stream(stream)
    while True:
        line = stream.readline()
        if not line:
            break
        pending.append(line)
        if len(pending) > footer_lines:
            spool.write(pending.popleft())
    spool.seek(0)
    return spool, spool


def is_sftp_file_compatible_with_type(
    sftp,
    key: str,
    file_type: str,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    escape_char: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Lightweight content-based compatibility check for SFTP files.
    """
    ft = (file_type or "").strip().lower()
    if not ft:
        return True, None

    if ft == "parquet":
        return _validate_sftp_parquet_signature(sftp, key)

    head_bytes = _read_sftp_head_bytes(sftp, key, max_bytes=4096)
    if not head_bytes:
        return False, "file is empty or head bytes could not be read"

    if ft == "avro":
        if _is_valid_avro_container(head_bytes):
            return True, None
        return False, "Invalid Avro container file header (expected magic bytes 'Obj\\x01')."

    try:
        text = head_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return False, "failed to decode head bytes"

    first_char = _first_non_whitespace_char(text)
    if ft == "json":
        if first_char in ("{", "["):
            return True, None
        return False, "does not look like JSON"
    if ft == "xml":
        if first_char == "<":
            return True, None
        return False, "does not look like XML"
    if ft in ("csv", "tsv"):
        delim = normalize_delimiter(delimiter, ft)
        qchar = normalize_quotechar(quotechar)
        echar = normalize_escapechar(escape_char)
        line_sep = normalize_line_separator(line_separator)
        ok, reason = _validate_delimited_text(
            text,
            delim,
            skipheaderlines,
            qchar,
            echar,
            skip_footer_lines,
            line_sep,
        )
        if ok:
            return True, None
        return False, f"does not look like delimited {ft.upper()} content. {reason}"

    return True, None


def _validate_delimited_row_width(stream, delimiter, quotechar, escapechar, skip, footer, separator):
    """Check every record without pandas' small-chunk extra-field truncation."""
    import codecs

    def check(encoding):
        stream.seek(0)
        text = codecs.getreader(encoding)(stream)

        def lines():
            if not separator or separator == "\n":
                yield from text
                return
            pending = ""
            while True:
                chunk = text.read(65536)
                if not chunk:
                    if pending:
                        yield pending
                    return
                pending += chunk
                while separator in pending:
                    line, pending = pending.split(separator, 1)
                    yield line + "\n"

        def data_lines():
            buffered = deque()
            for number, line in enumerate(lines()):
                if number < skip:
                    continue
                buffered.append(line)
                if len(buffered) > footer:
                    yield buffered.popleft()

        rows = csv.reader(
            data_lines(), delimiter=delimiter, quotechar=quotechar or None,
            quoting=csv.QUOTE_NONE if not quotechar else csv.QUOTE_MINIMAL,
            escapechar=escapechar, strict=True,
        )
        width = None
        for row in rows:
            if not row:
                continue
            if width is None:
                width = len(row)
            elif len(row) != width:
                raise ValueError(f"Invalid row at line {rows.line_num}: expected {width} columns, found {len(row)}.")
    try:
        try:
            check("utf-8")
        except UnicodeDecodeError:
            check("ISO-8859-1")
    finally:
        stream.seek(0)


def read_sftp_file_in_chunks(
    sftp, key: str,
    file_type: str,
    chunk_size: int = 5000,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
    enforce_row_schema_consistency: bool = False,
    enforce_json_key_consistency: bool = False,
    json_expected_keys_state: Optional[Dict[str, Optional[set]]] = None,
):
    """
    Generator function that yields chunks of DataFrames from SFTP file (streaming mode).
    Raises real exceptions (no sentinel yields) so table-level logs show the
    precise failure cause instead of a generic streaming error.
    """
    log(f"[STREAM] Starting chunked read from SFTP: {key}")
    
    try:
        chunk_rows = int(chunk_size) if chunk_size is not None else 5000
    except (TypeError, ValueError):
        chunk_rows = 5000

    skip = normalize_skipheaderlines(skipheaderlines)

    log(f"  [STREAM] File type: {file_type}, chunk_size: {chunk_rows:,} rows")
    file_type = file_type.lower() if file_type else ""
    
    chunk_number = 0
    total_rows_processed = 0
    last_modified = None
    
    file_stream = None
    try:
        # Get file metadata for logging and timestamp tracking
        stat = sftp.stat(key)
        last_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        file_size = stat.st_size
        log(f"  [STREAM] File metadata: size={file_size:,} bytes, last_modified={last_modified}")
        
        # Open SFTP file-like stream
        file_stream = sftp.open(key, "rb")

        # CSV/TSV: Use pandas chunksize (BEST for streaming)
        if file_type in ("csv", "tsv"):
            delim = normalize_delimiter(delimiter, file_type)
            q = normalize_quotechar(quotechar)
            quote_disabled = q == ""
            
            if skip == 0:
                pandas_skiprows = None
                pandas_header = 0
            else:
                pandas_skiprows = list(range(skip))
                pandas_header = 0
            
            escapechar_val = normalize_escapechar(escape_char)
            if quote_disabled and not escapechar_val:
                escapechar_val = "\\"
            
            na_values_list = None
            if null_sequence and null_sequence.strip():
                na_values_list = [null_sequence.strip()]
            
            skipfooter_val = normalize_skipheaderlines(skip_footer_lines) if skip_footer_lines else 0
            lineterminator_val = normalize_line_separator(line_separator)
            
            if enforce_row_schema_consistency:
                _validate_delimited_row_width(
                    file_stream, delim, q, escapechar_val, skip, skipfooter_val,
                    None if skipfooter_val else lineterminator_val,
                )

            # Debug pre-scan to estimate malformed rows
            try:
                head_bytes = _read_sftp_head_bytes(sftp, key, max_bytes=65536)
                if head_bytes:
                    try:
                        head_text = head_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        head_text = head_bytes.decode("ISO-8859-1", errors="replace")
                    debug_stats = _debug_count_bad_csv_lines(
                        head_text,
                        delimiter=delim,
                        quotechar=q,
                        escapechar=escapechar_val,
                        skipheaderlines=skip,
                        skip_footer_lines=skip_footer_lines,
                        max_lines=2000,
                    )
                    log(
                        "[STREAM][DEBUG] CSV pre-scan (prefix) "
                        f"lines={debug_stats['lines_scanned']}, "
                        f"blank={debug_stats['blank_lines']}, "
                        f"data={debug_stats['data_lines']}, "
                        f"bad={debug_stats['bad_lines']}, "
                        f"expected_cols={debug_stats['expected_cols']}"
                    )
                    if debug_stats.get("header_line_num") is not None:
                        log(
                            "[STREAM][DEBUG] CSV header (prefix) "
                            f"line={debug_stats['header_line_num']}, "
                            f"header={debug_stats.get('header_line')}"
                        )
                    if debug_stats.get("first_bad_line_num") is not None:
                        log(
                            "[STREAM][DEBUG] CSV first bad line (prefix) "
                            f"line={debug_stats['first_bad_line_num']}, "
                            f"cols={debug_stats.get('first_bad_cols')}, "
                            f"row={debug_stats.get('first_bad_line')}"
                        )
                    bad_lines_detail = debug_stats.get("bad_lines_detail") or []
                    if bad_lines_detail:
                        log("[STREAM][DEBUG] CSV bad lines (prefix):")
                        for item in bad_lines_detail:
                            log(
                                "  - "
                                f"line={item.get('line_num')}, "
                                f"cols={item.get('cols')}, "
                                f"row={item.get('row')}"
                            )
            except Exception:
                pass
            
            def _iterate_with_metadata(df_iterable):
                nonlocal chunk_number, total_rows_processed
                for chunk_df in df_iterable:
                    chunk_number += 1
                    if chunk_df.empty:
                        continue
                    try:
                        assert_no_fully_empty_rows(chunk_df)
                    except Exception as e:
                        log(f"  [STREAM] Invalid empty row(s) detected: {e}")
                        raise
                    chunk_df = drop_fully_empty_business_rows(chunk_df)
                    if not chunk_df.empty:
                        chunk_df, dropped_rows = drop_rows_with_missing_trailing_columns(chunk_df)
                        if dropped_rows:
                            if enforce_row_schema_consistency:
                                raise ValueError(
                                    f"Invalid row detected: {dropped_rows} row(s) have missing trailing columns. "
                                    "All rows must match the header column count."
                                )
                            log(
                                f"  [STREAM] Dropped {dropped_rows} malformed row(s) with most columns missing "
                                f"(trailing) for '{key}'"
                            )
                    if chunk_df.empty:
                        log(f"  [STREAM] Skipped chunk {chunk_number}: all rows were empty")
                        continue
                    global_row_offset = total_rows_processed
                    chunk_df = add_metadata_to_chunk(
                        chunk_df, key, last_modified, chunk_number, global_row_offset
                    )
                    total_rows_processed += len(chunk_df)
                    yield chunk_df, chunk_number, False, last_modified, total_rows_processed

            try:
                base_read_csv_params = {
                    "delimiter": delim,
                    "skiprows": pandas_skiprows,
                    "header": pandas_header,
                    "encoding": "utf-8",
                    "on_bad_lines": "error" if enforce_row_schema_consistency else "skip"
                }
                if quote_disabled:
                    base_read_csv_params["quoting"] = csv.QUOTE_NONE
                else:
                    base_read_csv_params["quotechar"] = q
                if escapechar_val:
                    base_read_csv_params["escapechar"] = escapechar_val
                if na_values_list:
                    base_read_csv_params["na_values"] = na_values_list
                if lineterminator_val and skipfooter_val == 0:
                    base_read_csv_params["lineterminator"] = lineterminator_val

                if skipfooter_val > 0:
                    footer_stream, footer_spool = _stream_without_footer(file_stream, skipfooter_val)
                    try:
                        chunk_read_csv_params = dict(base_read_csv_params)
                        chunk_read_csv_params["chunksize"] = chunk_rows
                        chunk_read_csv_params["engine"] = "python"
                        chunk_iter = pd.read_csv(footer_stream, **chunk_read_csv_params)
                        yield from _iterate_with_metadata(chunk_iter)
                    finally:
                        if footer_spool is not None:
                            try:
                                footer_spool.close()
                            except Exception:
                                pass
                else:
                    chunk_read_csv_params = dict(base_read_csv_params)
                    chunk_read_csv_params["chunksize"] = chunk_rows
                    chunk_iter = pd.read_csv(file_stream, **chunk_read_csv_params)
                    yield from _iterate_with_metadata(chunk_iter)

                yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed

            except UnicodeDecodeError:
                try:
                    file_stream.close()
                except Exception:
                    pass
                file_stream = sftp.open(key, "rb")

                base_read_csv_params = {
                    "delimiter": delim,
                    "skiprows": pandas_skiprows,
                    "header": pandas_header,
                    "encoding": "ISO-8859-1",
                    "on_bad_lines": "error" if enforce_row_schema_consistency else "skip"
                }
                if quote_disabled:
                    base_read_csv_params["quoting"] = csv.QUOTE_NONE
                else:
                    base_read_csv_params["quotechar"] = q
                if escapechar_val:
                    base_read_csv_params["escapechar"] = escapechar_val
                if na_values_list:
                    base_read_csv_params["na_values"] = na_values_list
                if lineterminator_val and skipfooter_val == 0:
                    base_read_csv_params["lineterminator"] = lineterminator_val

                if skipfooter_val > 0:
                    footer_stream, footer_spool = _stream_without_footer(file_stream, skipfooter_val)
                    try:
                        chunk_read_csv_params = dict(base_read_csv_params)
                        chunk_read_csv_params["chunksize"] = chunk_rows
                        chunk_read_csv_params["engine"] = "python"
                        chunk_iter = pd.read_csv(footer_stream, **chunk_read_csv_params)
                        yield from _iterate_with_metadata(chunk_iter)
                    finally:
                        if footer_spool is not None:
                            try:
                                footer_spool.close()
                            except Exception:
                                pass
                else:
                    chunk_read_csv_params = dict(base_read_csv_params)
                    chunk_read_csv_params["chunksize"] = chunk_rows
                    chunk_iter = pd.read_csv(file_stream, **chunk_read_csv_params)
                    yield from _iterate_with_metadata(chunk_iter)

                yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed
        
        # JSON: Custom chunking (split array into chunks)
        elif file_type == "json":
            mode = (json_mode or "").lower()
            try:
                prefix = _stream_text_prefix(file_stream, 4096).lstrip()
                value_iter = _stream_json_array_items(file_stream) if prefix.startswith("[") else _stream_json_values(file_stream)
                json_records: List[Any] = []
                json_row_index = 0

                def _emit_json_chunk(records: List[Any], start_index: int):
                    nonlocal chunk_number, total_rows_processed
                    if not records:
                        return None
                    chunk_number += 1
                    if mode == "packed":
                        chunk_df = pd.DataFrame([
                            {"_DATA": item, "_ROW": start_index + idx}
                            for idx, item in enumerate(records)
                        ])
                    else:
                        chunk_df = pd.DataFrame(records)
                        try:
                            assert_no_fully_empty_rows(chunk_df)
                        except Exception as e:
                            log(f"   JSON empty-row validation error: {e}")
                            raise
                    chunk_df = add_metadata_to_chunk(chunk_df, key, last_modified, chunk_number, start_index)
                    total_rows_processed += len(chunk_df)
                    return chunk_df

                for value in value_iter:
                    if mode == "packed":
                        if isinstance(value, list):
                            json_records.extend(value)
                        else:
                            json_records.append(value)
                        while len(json_records) >= chunk_rows:
                            chunk_records = json_records[:chunk_rows]
                            json_records = json_records[chunk_rows:]
                            chunk_df = _emit_json_chunk(chunk_records, json_row_index)
                            if chunk_df is not None:
                                yield chunk_df, chunk_number, False, last_modified, total_rows_processed
                            json_row_index += len(chunk_records)
                        continue

                    value = _unwrap_single_key_json_container(value)
                    if isinstance(value, list):
                        json_records.extend(value)
                    elif isinstance(value, dict):
                        json_records.append(value)
                    while len(json_records) >= chunk_rows:
                        chunk_records = json_records[:chunk_rows]
                        json_records = json_records[chunk_rows:]
                        if enforce_json_key_consistency and json_expected_keys_state is not None:
                            json_expected_keys_state["expected"] = _validate_json_record_keys(
                                chunk_records, json_expected_keys_state.get("expected")
                            )
                        chunk_df = _emit_json_chunk(chunk_records, json_row_index)
                        if chunk_df is not None:
                            yield chunk_df, chunk_number, False, last_modified, total_rows_processed
                        json_row_index += len(chunk_records)

                if json_records:
                    if enforce_json_key_consistency and json_expected_keys_state is not None and mode != "packed":
                        json_expected_keys_state["expected"] = _validate_json_record_keys(
                            json_records, json_expected_keys_state.get("expected")
                        )
                    chunk_df = _emit_json_chunk(json_records, json_row_index)
                    if chunk_df is not None:
                        yield chunk_df, chunk_number, False, last_modified, total_rows_processed

                yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed
            except json.JSONDecodeError as e:
                raise ValueError(f"JSON parse error: {e}")
            except Exception as e:
                raise
        
        # Parquet: Read row groups in chunks
        elif file_type == "parquet":
            import pyarrow.parquet as pq
            _reset_binary_stream(file_stream)
            parquet_file = pq.ParquetFile(file_stream)
            num_row_groups = parquet_file.num_row_groups
            for i in range(num_row_groups):
                chunk_number += 1
                table = parquet_file.read_row_group(i)
                chunk_df = table.to_pandas()
                global_row_offset = total_rows_processed
                chunk_df = add_metadata_to_chunk(
                    chunk_df, key, last_modified, chunk_number, global_row_offset
                )
                total_rows_processed += len(chunk_df)
                yield chunk_df, chunk_number, False, last_modified, total_rows_processed
            yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed
            
        # Avro: Read records in chunks
        elif file_type == "avro":
            import fastavro
            _reset_binary_stream(file_stream)
            records = []
            chunk_number = 0
            reader = fastavro.reader(file_stream)
            for record in reader:
                records.append(record)
                if len(records) >= chunk_rows:
                    chunk_number += 1
                    chunk_df = pd.json_normalize(records)
                    global_row_offset = total_rows_processed
                    chunk_df = add_metadata_to_chunk(
                        chunk_df, key, last_modified, chunk_number, global_row_offset
                    )
                    total_rows_processed += len(chunk_df)
                    yield chunk_df, chunk_number, False, last_modified, total_rows_processed
                    records = []
            if records:
                chunk_number += 1
                chunk_df = pd.json_normalize(records)
                global_row_offset = total_rows_processed
                chunk_df = add_metadata_to_chunk(
                    chunk_df, key, last_modified, chunk_number, global_row_offset
                )
                total_rows_processed += len(chunk_df)
                yield chunk_df, chunk_number, False, last_modified, total_rows_processed
            yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed

        # XML
        elif file_type == "xml":
            mode = (xml_mode or "").lower()
            _reset_binary_stream(file_stream)
            context = ET.iterparse(file_stream, events=("start", "end"))
            context_iter = iter(context)
            try:
                event, root = next(context_iter)
            except StopIteration:
                yield pd.DataFrame(), 0, True, last_modified, 0
                return

            current_depth = 1
            record_tag_local = None
            chunk_records: List[Dict[str, Any]] = []
            records_processed = 0

            for event, elem in context_iter:
                elem_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                if event == "start":
                    current_depth += 1
                    continue

                if elem is root:
                    if records_processed == 0 and len(list(root)) == 0:
                        if mode == "unpacked":
                            rec = parse_xml_unpacked(root)
                        else:
                            rec = parse_xml_packed(root)
                        if rec is not None:
                            chunk_number += 1
                            chunk_df = add_metadata_to_chunk(rec.copy(), key, last_modified, chunk_number, 0)
                            total_rows_processed += len(chunk_df)
                            yield chunk_df, chunk_number, False, last_modified, total_rows_processed
                    break

                if current_depth == 2:
                    if record_tag_local is None:
                        record_tag_local = elem_tag
                    if elem_tag == record_tag_local:
                        records_processed += 1
                        if mode == "unpacked":
                            rec = parse_xml_record_unpacked(elem, records_processed - 1)
                        else:
                            rec = parse_xml_record_packed(elem, records_processed - 1)
                        if rec is not None:
                            chunk_records.append(rec)
                        elem.clear()
                        if len(chunk_records) >= chunk_rows:
                            chunk_number += 1
                            chunk_df = pd.DataFrame(chunk_records)
                            if mode == "unpacked" and not _xml_unpacked_has_business_data(chunk_df):
                                raise ValueError("Update schema failed: no business columns in XML")
                            global_row_offset = total_rows_processed
                            chunk_df = add_metadata_to_chunk(
                                chunk_df, key, last_modified, chunk_number, global_row_offset
                            )
                            total_rows_processed += len(chunk_df)
                            yield chunk_df, chunk_number, False, last_modified, total_rows_processed
                            chunk_records = []

                current_depth -= 1

            if chunk_records:
                chunk_number += 1
                chunk_df = pd.DataFrame(chunk_records)
                if mode == "unpacked" and not _xml_unpacked_has_business_data(chunk_df):
                    raise ValueError("Update schema failed: no business columns in XML")
                global_row_offset = total_rows_processed
                chunk_df = add_metadata_to_chunk(
                    chunk_df, key, last_modified, chunk_number, global_row_offset
                )
                total_rows_processed += len(chunk_df)
                yield chunk_df, chunk_number, False, last_modified, total_rows_processed
            yield pd.DataFrame(), chunk_number, True, last_modified, total_rows_processed

    except Exception as e:
        log(f"  [STREAM] Error during chunked SFTP read: {e}")
        raise
    finally:
        if file_stream is not None:
            try:
                file_stream.close()
            except Exception:
                pass


def infer_columns_from_sftp_sample(
    sftp, key: str, file_type: str,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Dict[str, Any]]], Optional[str]]:
    """
    Read a small sample of the SFTP file and infer columns.
    Returns (columns, error_message).
    """
    is_compatible, type_reason = is_sftp_file_compatible_with_type(
        sftp=sftp,
        key=key,
        file_type=file_type,
        delimiter=delimiter,
        quotechar=quotechar,
        skipheaderlines=skipheaderlines,
        escape_char=escape_char,
        skip_footer_lines=skip_footer_lines,
        line_separator=line_separator,
    )
    if not is_compatible:
        return None, (
            f"Update schema failed: File '{key}' could not be parsed. "
            f"Details: {type_reason}"
        )

    df_sample = None
    try:
        for chunk_df, chunk_num, is_last_chunk, last_mod, total_rows_chunk in read_sftp_file_in_chunks(
            sftp, key, file_type,
            chunk_size=100,
            delimiter=delimiter,
            quotechar=quotechar,
            skipheaderlines=skipheaderlines,
            json_mode=json_mode,
            xml_mode=xml_mode,
            escape_char=escape_char,
            null_sequence=null_sequence,
            skip_footer_lines=skip_footer_lines,
            line_separator=line_separator,
        ):
            if chunk_df is not None and not chunk_df.empty:
                df_sample = chunk_df
                break
    except Exception as e:
        return None, f"Error reading file sample: {str(e)}"

    if df_sample is None or df_sample.empty:
        return None, "File is empty or could not be read"

    columns = infer_columns_from_df(df_sample, load_method="upsert_file_row")
    if not isinstance(columns, dict) or not columns:
        return None, "Could not infer columns from sample"
    return columns, None


def compute_common_new_columns_for_multi_files_sftp(
    sftp, keys: List[str], file_type: str,
    expected_columns: Dict[str, Dict[str, Any]],
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> Set[str]:
    """
    For multi-files-single-table mode:
    Only allow new columns if they appear in ALL files in the batch.
    Returns a set of allowed new columns (normalized uppercase).
    """
    if not isinstance(expected_columns, dict) or not expected_columns:
        return set()

    expected_col_names = {sanitize_column_name(k).upper() for k in expected_columns.keys()}
    metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
    expected_col_names = expected_col_names - metadata_cols

    common_cols = None
    for key in keys:
        actual_columns, err = infer_columns_from_sftp_sample(
            sftp, key, file_type,
            delimiter=delimiter,
            quotechar=quotechar,
            skipheaderlines=skipheaderlines,
            json_mode=json_mode,
            xml_mode=xml_mode,
            escape_char=escape_char,
            null_sequence=null_sequence,
            skip_footer_lines=skip_footer_lines,
            line_separator=line_separator,
        )
        if actual_columns is None:
            log(f"[VALIDATE]  Unable to infer columns for '{key}': {err}")
            return set()

        actual_col_names = {sanitize_column_name(k).upper() for k in actual_columns.keys()}
        actual_col_names = actual_col_names - metadata_cols

        common_cols = actual_col_names if common_cols is None else (common_cols & actual_col_names)
        if not common_cols:
            return set()

    if common_cols is None:
        return set()

    new_cols = common_cols - expected_col_names
    if new_cols:
        log(f"[VALIDATE]  New columns present in ALL files: {new_cols}")
    return new_cols


def validate_sftp_file_structure_matches(
    sftp, key: str, file_type: str,
    expected_columns: Dict[str, Dict[str, Any]],
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    escape_char: Optional[str] = None,
    null_sequence: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
    enforce_type_compatibility: bool = False,
    allowed_new_columns: Optional[Set[str]] = None,
) -> Tuple[bool, Dict[str, Dict[str, Any]], Optional[str]]:
    """
    Validate that an SFTP file matches the expected structure.
    """
    log(f"[VALIDATE] Validating file structure for '{key}'")
    actual_columns, err = infer_columns_from_sftp_sample(
        sftp, key, file_type,
        delimiter=delimiter,
        quotechar=quotechar,
        skipheaderlines=skipheaderlines,
        json_mode=json_mode,
        xml_mode=xml_mode,
        escape_char=escape_char,
        null_sequence=null_sequence,
        skip_footer_lines=skip_footer_lines,
        line_separator=line_separator,
    )
    if actual_columns is None:
        return False, {}, err or "Could not infer columns from sample"

    # Normalize column names for comparison (uppercase, sanitized)
    expected_col_names = {sanitize_column_name(k).upper() for k in expected_columns.keys()}
    actual_col_names = {sanitize_column_name(k).upper() for k in actual_columns.keys()}

    # Exclude metadata columns from comparison (they're added automatically)
    metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
    expected_col_names = expected_col_names - metadata_cols
    actual_col_names = actual_col_names - metadata_cols

    # Check if all expected columns exist in actual columns
    missing_cols = expected_col_names - actual_col_names
    if missing_cols:
        return False, actual_columns, f"File '{key}' is missing required columns: {missing_cols}"

    # Check if there are new columns (this is OK, will be handled by schema update)
    new_cols = actual_col_names - expected_col_names
    if new_cols:
        if allowed_new_columns is not None:
            disallowed_new = new_cols - allowed_new_columns
            if disallowed_new:
                return False, actual_columns, f"File '{key}' contains disallowed new columns: {disallowed_new}"

    return True, actual_columns, None



class SFTPSnowflakeService:
    @staticmethod
    async def migrate(request: SFTPSnowflakeMigrationRequest) -> Dict[str, Any]:
        """
        Main migration entry point for SFTP to Snowflake.
        """
        token = log_prefix.set("SFTP")
        try:
            return await SFTPSnowflakeService._sync_sftp_to_snowflake(request)
        finally:
            log_prefix.reset(token)

    @staticmethod
    async def _sync_sftp_to_snowflake(request: SFTPSnowflakeMigrationRequest) -> Dict[str, Any]:
        """
        Internal migration implementation: sync data from SFTP to Snowflake.
        """
        log("=" * 80)
        log("[MIGRATION] Starting SFTP to Snowflake migration")
        log("=" * 80)

        # Extract source config (SFTP)
        source_config = request.source_config
        log("[MIGRATION] Extracting source config (SFTP)...")
        host = str(source_config.get("sftp_host") or source_config.get("host") or "").strip()
        port = source_config.get("sftp_port") if source_config.get("sftp_port") is not None else source_config.get("port")
        username = str(source_config.get("sftp_username") or source_config.get("username") or "").strip()
        sftp_base_prefix = (source_config.get("sftp_base_prefix") or source_config.get("base_folder_path") or "").strip()
        
        log(f"[MIGRATION] SFTP Config: host={host}, port={port or 22}, username={username}, prefix={sftp_base_prefix or '(root)'}")

        # Extract file mapping mode — support both key aliases for compatibility:
        # • "file_mapping_mode"   (canonical key used by migration service and SourceFileMapping overlay)
        # • "file_mapping_method" (key sent by the frontend form and list_sftp_files view)
        file_mapping_mode = (
            source_config.get("file_mapping_mode")
            or source_config.get("file_mapping_method")
            or "single_table_per_file"
        )
        if not isinstance(file_mapping_mode, str):
            file_mapping_mode = "single_table_per_file"
        log(f"[SFTP][MIGRATION] File mapping mode: {file_mapping_mode}")

        # Optional UI-driven mapping: file_key -> logical table name
        single_file_map = source_config.get("single_file_table_mapping") or {}
        if isinstance(single_file_map, str):
            try:
                single_file_map = json.loads(single_file_map)
            except Exception as e:
                log(f"[SFTP][MIGRATION][MAP] Failed to parse single_file_table_mapping: {e}")
                single_file_map = {}
        if not isinstance(single_file_map, dict):
            single_file_map = {}
        log(f"[SFTP][MIGRATION][MAP] Final single_file_map: {single_file_map} ({len(single_file_map)} entries)")

        # Extract destination config (Snowflake)
        dest_config = request.destination_config
        log("[MIGRATION] Extracting destination config (Snowflake)...")
        sf_account = dest_config.get("account")
        sf_user = dest_config.get("user") or dest_config.get("username")
        sf_password = dest_config.get("password")
        sf_warehouse = normalize_and_log_snowflake_identifier(dest_config.get("warehouse"), "warehouse")
        sf_database = normalize_and_log_snowflake_identifier(dest_config.get("database"), "database")
        sf_schema = normalize_and_log_snowflake_identifier(dest_config.get("schema"), "schema")
        sf_role = normalize_and_log_snowflake_identifier(dest_config.get("role"), "role") or "ACCOUNTADMIN"

        log(f"[MIGRATION] Snowflake Config: account={sf_account}, database={sf_database}, schema={sf_schema}")

        # Extract GCS config
        gcs_service_account_json = dest_config.get("gcs_service_account_json") or source_config.get("gcs_service_account_json")
        gcp_project_id = dest_config.get("gcp_project_id") or source_config.get("gcp_project_id")
        gcs_bucket_name = resolve_gcs_bucket_name(dest_config, source_config)
        sf_storage_integration = resolve_snowflake_storage_integration(dest_config, source_config)

        log(f"[MIGRATION] GCS Config: bucket={gcs_bucket_name} (root level uploads)")
        log(f"[MIGRATION] Storage Integration: {sf_storage_integration}")

        # Extract migration parameters
        connection_id = request.connection_id
        cmp_id = request.cmp_id
        user_id = request.user_id
        session = request.session
        selected_tables = request.selected_tables
        mapping_removed_tables = []
        log_id = request.log_id if hasattr(request, 'log_id') else None
        start_time = time.time()

        # Convert table names to SFTP file paths (keys) in single_table_per_file mode
        if selected_tables and file_mapping_mode == "single_table_per_file":
            log(f"[SFTP] [MIGRATION] Converting {len(selected_tables)} table names to SFTP file paths...")
            selected_files = []
            try:
                mapped_table_names_upper = {
                    str(v).strip().upper()
                    for v in (single_file_map or {}).values()
                    if str(v).strip()
                }
                mapped_file_keys = {
                    str(k).strip()
                    for k in (single_file_map or {}).keys()
                    if str(k).strip()
                }

                conn_db = postgres_connection()
                cursor_db = conn_db.cursor()
                
                # Query folder_path for selected tables
                normalized_selected_tables = [
                    sanitize_identifier(str(t).strip()) for t in selected_tables if str(t).strip()
                ]
                if not normalized_selected_tables:
                    raise ValueError("No valid selected table names provided for SFTP migration.")
                placeholders = ','.join(['%s'] * len(normalized_selected_tables))
                sql_get_paths = f"""
                    SELECT "table", folder_path 
                    FROM source_table_selection 
                    WHERE connection_id = %s AND UPPER("table") IN ({placeholders})
                """
                cursor_db.execute(sql_get_paths, (connection_id, *normalized_selected_tables))
                table_to_path = {str(row[0]).strip().upper(): row[1] for row in cursor_db.fetchall()}
                cursor_db.close()
                conn_db.close()
                
                # Convert table names to file paths
                unmapped_tables = []
                for table_name in selected_tables:
                    table_name_str = str(table_name).strip()
                    table_name_upper = table_name_str.upper()

                    # If selected value is a table name, it must exist in current mapping values.
                    if table_name_upper in mapped_table_names_upper:
                        matched_file_key = next(
                            (
                                str(file_key)
                                for file_key, mapped_table in (single_file_map or {}).items()
                                if str(mapped_table).strip().upper() == table_name_upper
                            ),
                            None,
                        )
                        if matched_file_key:
                            selected_files.append(matched_file_key)
                            log(f"[SFTP] [MIGRATION] Mapped table '{table_name_str}' file '{matched_file_key}'")
                            continue

                    # If selected value is a direct file key, it must still exist in current mapping.
                    if table_name_str in mapped_file_keys:
                        selected_files.append(table_name_str)
                        log(f"[SFTP] [MIGRATION] Using selected file key '{table_name_str}'")
                        continue

                    file_path = table_to_path.get(table_name_upper)
                    if file_path:
                        file_path_str = str(file_path).strip()
                        if file_path_str in mapped_file_keys:
                            selected_files.append(file_path_str)
                            log(f"[SFTP] [MIGRATION] Mapped table '{table_name_str}' file '{file_path_str}'")
                        else:
                            unmapped_tables.append(table_name_str)
                            log(
                                f"[SFTP] [MIGRATION] WARNING: Table '{table_name_str}' has stale folder_path "
                                f"'{file_path_str}' not present in current mapping"
                            )
                    else:
                        unmapped_tables.append(table_name_str)
                        log(f"[SFTP] [MIGRATION] WARNING: Mapping missing for table '{table_name_str}'")

                if unmapped_tables:
                    mapping_removed_tables.extend(unmapped_tables)
                    missing_tables = ", ".join(sorted(set(unmapped_tables)))
                    log(
                        "[SFTP] [MIGRATION] Mapping removed for selected table(s): "
                        f"{missing_tables}. These tables will be marked failed and skipped."
                    )
                
                # Replace selected_tables with selected_files (file paths)
                selected_tables = selected_files
                log(f"[MIGRATION] Using {len(selected_tables)} selected files (converted from {len(request.selected_tables)} table names)")
            except Exception as path_error:
                log(f"[SFTP] [MIGRATION] Error converting table names to file paths: {path_error}")
                raise

        # Extract file options
        file_type = source_config.get("file_type", "").lower() if source_config.get("file_type") else ""
        json_mode = source_config.get("json_mode")
        xml_mode = source_config.get("xml_mode")
        delimiter = source_config.get("delimiter")
        quotechar = source_config.get("quotechar")
        skipheaderlines = source_config.get("skipheaderlines")
        
        # CSV/TSV options
        escape_char = source_config.get("escape_char")
        null_sequence = source_config.get("null_sequence")
        skip_footer_lines = source_config.get("skip_footer_lines")
        line_separator = source_config.get("line_separator")
        
        if file_type == "tsv":
            delimiter = source_config.get("tsv_delimiter") or delimiter
            quotechar = source_config.get("tsv_quotechar") or quotechar
            skipheaderlines = source_config.get("tsv_skipheaderlines") if source_config.get("tsv_skipheaderlines") is not None else skipheaderlines
            escape_char = source_config.get("tsv_escape_char") or escape_char
            null_sequence = source_config.get("tsv_null_sequence") or null_sequence
            skip_footer_lines = source_config.get("tsv_skip_footer_lines") if source_config.get("tsv_skip_footer_lines") is not None else skip_footer_lines

        chunk_count = request.chunk_count
        load_method = source_config.get("load_method", "upsert_file_row")
        
        multi_files_table_name = (source_config.get("multi_files_table_name") or "").strip()
        multi_files_prefix = (source_config.get("multi_files_prefix") or "").strip()
        include_subfolders = source_config.get("include_subfolders") in (True, "true", "1", "yes", "on")

        # Load custom primary key mappings
        custom_pk_mapping = {}
        if load_method == "upsert_custom_key":
            custom_pk_mapping = source_config.get("custom_primary_key") or {}
            if isinstance(custom_pk_mapping, str):
                try:
                    custom_pk_mapping = json.loads(custom_pk_mapping)
                except Exception:
                    custom_pk_mapping = {}
            if not isinstance(custom_pk_mapping, dict):
                custom_pk_mapping = {}

        job_name = request.job_name or f"sftp_migration_{request.migration_session_id}"
        migration_session_id = request.migration_session_id
        safety_interval = request.safety_interval
        logs_creation = request.logs_creation
        connection_name = (request.connection_name or f"SFTP_CONNECTION_{connection_id}").strip()
        company_name = (request.company_name or "").strip()
        drop_and_reload = request.drop_and_reload or False

        # SFTP import migration should not defer terminal logs/emails across retries.
        # Force final-attempt behavior for consistent status/log visibility, matching S3.
        is_final_attempt = True
        retry_attempt = 0
        max_retries = 1

        # Initialize failure tracker
        failure_tracker = MigrationFailureTracker()
        table_level_error_email_sent = False
        post_missing_schema_refresh_done = False

        async def _run_post_missing_file_schema_refresh() -> None:
            """Refresh SFTP schema once after missing-file detection so stale mappings are pruned."""
            nonlocal post_missing_schema_refresh_done
            if post_missing_schema_refresh_done:
                return
            post_missing_schema_refresh_done = True

            log(
                "[MIGRATION] Post-migration schema refresh triggered due to missing file(s): "
                f"{sorted(missing_files_single_mode)}"
            )
            refresh_job_name = (
                f"{job_name}_post_missing_file_schema_refresh"
                if job_name
                else f"SFTP_post_missing_file_schema_refresh_{migration_session_id}"
            )
            try:
                insert_tool_log(
                    cmp_id=cmp_id,
                    connection_id=connection_id,
                    usr_id=user_id,
                    table_name="Update Schema",
                    status="I",
                    message="Update schema in progress...",
                    session=migration_session_id,
                    job_name=refresh_job_name,
                    migration_record_id=None,
                )
            except Exception as start_log_error:
                log(f"[MIGRATION] Could not create post-refresh start log (non-fatal): {start_log_error}")

            listing_request = SFTPListingTables(
                connection_id=connection_id,
                SFTP=source_config,
                job_name=refresh_job_name,
                migration_session_id=migration_session_id,
                cmp_id=cmp_id,
                user_id=user_id,
                log_id=log_id,
            )
            try:
                # SFTPSourceService.list_tables_with_schema is synchronous (unlike S3).
                refreshed_tables_result = SFTPSourceService.list_tables_with_schema(listing_request)
                refreshed_tables_result["cmp_id"] = cmp_id
                refreshed_tables_result["user_id"] = user_id
                SFTPSourceService.refresh_and_update_sftp_tables_in_database(
                    connection_id=connection_id,
                    tables_result=refreshed_tables_result,
                    job_name=refresh_job_name,
                    migration_session_id=migration_session_id,
                )
                try:
                    from utils.websocket_utils import (
                        trigger_connection_ws_status_update,
                        trigger_update_schema_ws_status_update,
                    )
                    trigger_update_schema_ws_status_update(connection_id)
                    trigger_connection_ws_status_update(connection_id)
                except Exception as ws_err:
                    log(f"[MIGRATION] Could not trigger websocket after SFTP schema refresh: {ws_err}")
                log(
                    "[MIGRATION] Post-migration schema refresh completed after missing-file detection"
                )
            except Exception as refresh_err:
                log(f"[MIGRATION] Post-migration SFTP schema refresh failed: {refresh_err}")
                try:
                    from utils.error_utils import update_migration_status_log
                    update_migration_status_log(
                        job_name=refresh_job_name,
                        message=f"Update schema failed: {refresh_err}",
                        status="F",
                        session=migration_session_id,
                    )
                    from utils.websocket_utils import trigger_update_schema_ws_status_update
                    trigger_update_schema_ws_status_update(connection_id)
                except Exception as log_err:
                    log(f"[MIGRATION] Could not write post-refresh failure log: {log_err}")
                raise

        def _mark_selected_tables_failed_on_terminal_error() -> None:
            try:
                candidate_names = set()
                for item in selected_tables or []:
                    value = str(item).strip()
                    if not value:
                        continue
                    candidate_names.add(value)
                    mapped_name = single_file_map.get(value) or single_file_map.get(value.split("/")[-1])
                    if mapped_name:
                        candidate_names.add(str(mapped_name).strip())

                if file_mapping_mode == "multi_files_single_table" and multi_files_table_name:
                    candidate_names.add(str(multi_files_table_name).strip())

                if not candidate_names:
                    return

                conn_db = postgres_connection()
                cursor_db = conn_db.cursor()
                cursor_db.execute(
                    """
                    SELECT "table"
                    FROM source_table_selection
                    WHERE connection_id = %s
                    """,
                    (connection_id,),
                )
                existing_table_names = [str(r[0]).strip() for r in (cursor_db.fetchall() or []) if r and r[0]]
                cursor_db.close()
                conn_db.close()

                existing_lookup = {name.upper(): name for name in existing_table_names}
                resolved_tables = []
                for name in candidate_names:
                    resolved = existing_lookup.get(str(name).strip().upper())
                    if resolved:
                        resolved_tables.append(resolved)

                for table_name in sorted(set(resolved_tables)):
                    try:
                        update_table_status_snowflake(
                            connection_id, table_name, "failed", job_name, migration_session_id
                        )
                    except Exception as status_error:
                        log(f"[MIGRATION] Failed to mark table '{table_name}' as failed: {status_error}")
            except Exception as mark_error:
                log(f"[MIGRATION] Failed to mark selected tables as failed: {mark_error}")

        # Validate required configs
        try:
            # Validate SFTP credentials
            if not host or not username:
                error_msg = "Missing required SFTP credentials (host or username)"
                log(f"[MIGRATION] {error_msg}")
                raise ValueError(error_msg)
            
            # Check authentication type and password/private_key.
            # For backwards-compatibility with older SFTP connections that were saved before
            # authentication_type was a required field, auto-detect from the credentials present.
            _explicit_auth = str(source_config.get("authentication_type") or "").strip().lower()
            if _explicit_auth not in ("password", "key_pair"):
                # Auto-detect: prefer password if sftp_password is set, otherwise fall back to key_pair
                if source_config.get("sftp_password") or source_config.get("password"):
                    _explicit_auth = "password"
                    log(f"[MIGRATION] authentication_type auto-detected as 'password' from sftp_password credential")
                elif source_config.get("sftp_private_key") or source_config.get("private_key"):
                    _explicit_auth = "key_pair"
                    log(f"[MIGRATION] authentication_type auto-detected as 'key_pair' from sftp_private_key credential")
                else:
                    _explicit_auth = "password"  # default; will fail below if password is also missing
                    log(f"[MIGRATION] authentication_type not set and no credential found — defaulting to 'password'")
            auth_type = _explicit_auth
            if auth_type == "password":
                sftp_pass = source_config.get("sftp_password") or source_config.get("password")
                if not sftp_pass:
                    error_msg = "Missing required SFTP authentication: password must be provided for password auth"
                    log(f"[MIGRATION] {error_msg}")
                    raise ValueError(error_msg)
            elif auth_type == "key_pair":
                sftp_priv = source_config.get("sftp_private_key") or source_config.get("private_key")
                if not sftp_priv:
                    error_msg = "Missing required SFTP authentication: private_key must be provided for key_pair auth"
                    log(f"[MIGRATION] {error_msg}")
                    raise ValueError(error_msg)

            # Validate required Snowflake fields (account, user, database, schema)
            if not sf_account or not sf_user or not sf_database or not sf_schema:
                error_msg = "Missing required Snowflake credentials (account, user, database, or schema)"
                log(f"[MIGRATION] {error_msg}")
                raise ValueError(error_msg)
            
            # Validate Snowflake authentication: either password OR private_key must be provided
            sf_private_key = dest_config.get("private_key")
            if not sf_password and not sf_private_key:
                error_msg = "Missing required Snowflake authentication: either password or private_key must be provided"
                log(f"[MIGRATION] {error_msg}")
                raise ValueError(error_msg)
        except ValueError as validation_error:
            error_msg = str(validation_error)
            log(f"[MIGRATION] Validation Error: {error_msg}")
            
            error_context = {
                "table_name": "Migration Setup",
                "service_name": "SFTP to Snowflake",
                "error_type": "VALIDATION_ERROR",
                "cmp_id": cmp_id,
                "connection_id": connection_id,
                "user_id": user_id,
                "job_name": job_name,
                "migration_session_id": migration_session_id,
                "company_name": company_name,
                "connection_name": connection_name,
                "session": session
            }
            
            try:
                handle_service_error(
                    error=validation_error,
                    context=error_context,
                    log_files=None,
                    send_email=True,  # Send email for validation errors
                    is_final_attempt=is_final_attempt
                )
            except Exception as email_err:
                log(f" Failed to send validation error email: {email_err}")
            _mark_selected_tables_failed_on_terminal_error()
            raise validation_error

        # Setup Snowflake connection
        try:
            conn = sf_connect(
                sf_account=sf_account,
                sf_user=sf_user,
                sf_password=sf_password,
                sf_warehouse=sf_warehouse,
                sf_database=sf_database,
                sf_schema=sf_schema,
                sf_role=sf_role,
                private_key=dest_config.get("private_key"),
                passphrase=dest_config.get("passphrase"),
                authentication_type=dest_config.get("authentication_type", "password"),
            )
            log(" Snowflake connection successful")
            
            # Create schema if it doesn't exist
            if sf_schema:
                log(f"[MIGRATION] Ensuring schema '{sf_schema}' exists in database '{sf_database}'...")
                try:
                    with conn.cursor() as cur:
                        if sf_database:
                            cur.execute(f"USE DATABASE {Q(sf_database)}")
                        try:
                            cur.execute(f"USE SCHEMA {Q(sf_schema)}")
                            log(f"[MIGRATION] Schema '{sf_schema}' already exists")
                        except Exception:
                            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {Q(sf_schema)}")
                            log(f"[MIGRATION] Schema '{sf_schema}' created successfully")
                            cur.execute(f"USE SCHEMA {Q(sf_schema)}")
                except Exception as schema_error:
                    log(f"[MIGRATION] Warning: Could not ensure schema exists: {str(schema_error)}")
        except Exception as sf_error:
            error_message = str(sf_error)
            error_message_with_trace = f"{error_message}\n\n--- Traceback ---\n{traceback.format_exc()}"
            log(f" SNOWFLAKE CONNECTION FAILED: {error_message}")
            
            if is_final_attempt:
                try:
                    html_content, plain_text = create_connection_failure_email(
                        connection_type="Snowflake",
                        connection_name=connection_name,
                        error_message=error_message_with_trace,
                        context={
                            "job_name": job_name,
                            "company_name": company_name,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "cmp_id": cmp_id
                        }
                    )
                    email_config = EmailConfiguration(user_id=user_id, company_id=cmp_id)
                    if email_config.is_configured():
                        subject = f" Snowflake Connection Failed - {connection_name} - {company_name}"
                        send_email_with_retry(email_config, subject, html_content, plain_text)
                        log(" Snowflake connection failure email sent (final attempt)")
                except Exception as email_error:
                    log(f" Failed to send Snowflake connection failure email: {email_error}")
            _mark_selected_tables_failed_on_terminal_error()
            raise sf_error

        # Setup SFTP client
        try:
            sftp_client, transport, sock = _open_sftp_client(source_config)
            log(f"[MIGRATION] SFTP client authenticated successfully")
            
            # Change directory to the top-level root_folder ONLY (NOT including base_folder_path).
            # File keys stored in the database (e.g. "csv_files/person_one.txt") are relative to
            # root_folder, so chdir must NOT include base_folder_path — that would create a
            # double-nested path like root/csv_files/csv_files/file.txt and cause FileNotFoundError.
            # This mirrors the listing service which also only chdir's to root_folder.
            root_folder = _sftp_listing_root_folder(source_config)
            if root_folder:
                sftp_client.chdir(root_folder)
                log(f"[MIGRATION] SFTP client changed directory to root folder: '{root_folder}'")
        except Exception as sftp_error:
            error_message = str(sftp_error)
            error_message_with_trace = f"{error_message}\n\n--- Traceback ---\n{traceback.format_exc()}"
            log(f" SFTP CONNECTION FAILED: {error_message}")
            
            if is_final_attempt:
                try:
                    html_content, plain_text = create_connection_failure_email(
                        connection_type="SFTP",
                        connection_name=connection_name,
                        error_message=error_message_with_trace,
                        context={
                            "job_name": job_name,
                            "company_name": company_name,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "cmp_id": cmp_id
                        }
                    )
                    email_config = EmailConfiguration(user_id=user_id, company_id=cmp_id)
                    if email_config.is_configured():
                        subject = f" SFTP Connection Failed - {connection_name} - {company_name}"
                        send_email_with_retry(email_config, subject, html_content, plain_text)
                        log(" SFTP connection failure email sent (final attempt)")
                except Exception as email_error:
                    log(f" Failed to send SFTP connection failure email: {email_error}")
            try:
                conn.close()
            except Exception:
                pass
            _mark_selected_tables_failed_on_terminal_error()
            raise sftp_error

        # Setup GCS
        log(f"[MIGRATION] Connecting to GCS...")
        try:
            if gcs_service_account_json:
                gcs = gcs_client(gcs_service_account_json, gcp_project_id)
            else:
                gcs = storage.Client(project=gcp_project_id)
            log(f"[MIGRATION] GCS connected")
        except Exception as e:
            # Fallback to default credentials
            log(f"[MIGRATION] GCS connection with config failed: {e}")
            log(f"[MIGRATION] Attempting fallback to default GCS credentials...")
            try:
                from google.auth import compute_engine
                credentials = compute_engine.Credentials()
                gcs = storage.Client(credentials=credentials, project=gcp_project_id)
                log(f"[MIGRATION] GCS connected using default credentials")
            except Exception as fallback_error:
                try:
                    gcs = storage.Client(project=gcp_project_id)
                    log(f"[MIGRATION] GCS connected using environment/default credentials")
                except Exception as final_error:
                    error_msg = f"GCS connection failed: {e}. Fallback attempts also failed: {fallback_error}, {final_error}"
                    log(f"[MIGRATION] {error_msg}")
                    
                    if is_final_attempt:
                        try:
                            error_context = {
                                "table_name": "GCS Connection",
                                "service_name": "SFTP to Snowflake",
                                "error_type": "GCS_CONNECTION_FAILED",
                                "cmp_id": cmp_id,
                                "connection_id": connection_id,
                                "user_id": user_id,
                                "job_name": job_name,
                                "migration_session_id": migration_session_id,
                                "company_name": company_name,
                                "connection_name": connection_name,
                                "session": session
                            }
                            
                            error_result = handle_service_error(
                                error=Exception(error_msg),
                                context=error_context,
                                log_files=None,
                                send_email=True,
                                is_final_attempt=is_final_attempt
                            )
                            if error_result.get("email_sent"):
                                log(f" GCS connection failure email sent")
                        except Exception as email_error:
                            log(f" Failed to send GCS connection failure email: {email_error}")
                    
                    _close_sftp_client(sftp_client, transport, sock)
                    try:
                        conn.close()
                    except Exception:
                        pass
                    _mark_selected_tables_failed_on_terminal_error()
                    raise Exception(error_msg)

        # Setup and validate GCS Storage Integration
        try:
            gcs_allowed_url_prefix = f"gcs://{gcs_bucket_name}/"
            log(f"[MIGRATION] Ensuring storage integration: {sf_storage_integration} with prefix: {gcs_allowed_url_prefix}")
            existed = ensure_gcs_storage_integration(conn, sf_storage_integration, gcs_allowed_url_prefix)
            try:
                sa_email = get_gcp_service_account(conn, sf_storage_integration)
                ensure_bucket_iam_for_integration(gcs, gcs_bucket_name, sa_email)
                log(f"[MIGRATION] Storage integration IAM configured")
            except Exception as e:
                log(f"[MIGRATION] Storage integration IAM check: {e if not existed else 'continuing...'}")

            assert_allowed_prefix_matches(conn, gcs_allowed_url_prefix, sf_storage_integration)
        except Exception as storage_error:
            log(f"[MIGRATION] Storage integration validation failed: {storage_error}")
            _close_sftp_client(sftp_client, transport, sock)
            try:
                conn.close()
            except Exception:
                pass
            _mark_selected_tables_failed_on_terminal_error()
            raise Exception(f"Storage integration validation failed: {storage_error}")

        try:
            # Get table mappings and last_synced timestamps
            table_names = []
            selected_basename_counts = {}
            key_basename_counts = {}
            if selected_tables:
                selected_basename_counts = {k: 0 for k in selected_tables} # dummy
                for key in selected_tables:
                    if file_mapping_mode == "multi_files_single_table":
                        table_name = sanitize_identifier(str(multi_files_table_name or "multi_files_single_table").strip())
                    else:
                        internal_name = table_from_file_key(key)
                        mapped_name = single_file_map.get(key)
                        table_name = sanitize_identifier(str(mapped_name).strip()) if mapped_name else internal_name
                    if table_name:
                        table_names.append(table_name)
                last_checked_data = get_last_checked_dates_and_fields(connection_id, table_names)
            else:
                last_checked_data = {}

            # Apply safety interval cutoff
            safety_interval_cutoff = None
            raw_safety_interval = (str(safety_interval).strip() if safety_interval is not None else "")
            if raw_safety_interval and raw_safety_interval.lower() != "none":
                try:
                    interval_minutes = int(raw_safety_interval)
                    if interval_minutes > 0:
                        interval_seconds = interval_minutes * 60
                        safety_interval_cutoff = (
                            datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)
                        ).replace(microsecond=0)
                        log(f"[MIGRATION] Safety interval cutoff: {safety_interval_cutoff}")
                except (TypeError, ValueError):
                    pass

            keys = []
            multi_no_files_discovered = False
            if selected_tables:
                if file_mapping_mode == "single_table_per_file":
                    reverse_map = {str(v): str(k) for k, v in single_file_map.items()}
                    for item in selected_tables:
                        selected_item = str(item)
                        if selected_item in single_file_map:
                            keys.append(selected_item)
                        else:
                            file_key = reverse_map.get(selected_item)
                            if file_key:
                                keys.append(file_key)
                else:
                    # multi_files_single_table mode: discover files via walking root
                    # NOTE: sftp_client is already chdir'd to root_folder during SFTP setup above.
                    # Walk from the configured base folder so we only process files under that subtree.
                    try:
                        listing_start = _sftp_listing_base_folder(source_config) or "."
                        all_sftp_files = _walk_sftp_files(
                            sftp_client,
                            listing_start,
                            listing_start,
                            include_subfolders=include_subfolders,
                        )
                        keys = [entry.file_key for entry in all_sftp_files]
                        if not keys:
                            candidate_keys = [str(item).strip() for item in selected_tables if str(item).strip()]
                            has_path_like_keys = any(("/" in item or "." in item) for item in candidate_keys)
                            if candidate_keys and has_path_like_keys:
                                keys = candidate_keys
                            else:
                                multi_no_files_discovered = True
                    except Exception as mapping_error:
                        log(f"[MIGRATION] Error walking SFTP directory: {mapping_error}")
                        raise

            # Sort files chronologically for unified table processing
            if file_mapping_mode == "multi_files_single_table":
                dated_keys = []
                for k in keys:
                    date_obj, score = extract_date_from_filename(k)
                    if date_obj:
                        dated_keys.append((k, date_obj))
                    else:
                        try:
                            st = sftp_client.stat(k)
                            dt = datetime.fromtimestamp(st.st_mtime, timezone.utc).date()
                            dated_keys.append((k, dt))
                        except Exception:
                            from datetime import date
                            dated_keys.append((k, date.min))
                dated_keys.sort(key=lambda x: x[1])
                keys = [x[0] for x in dated_keys]
                log("[MIGRATION] Files sorted chronologically")

            failed_removed_tables = list(set(mapping_removed_tables))
            
            def _mark_removed_mapping_table_failed(table_name_val: str) -> None:
                """Mark removed-mapping table as failed in both table selection and migration record."""
                removed_mapping_message = "SFTP table mapping removed. Kindly update schema or remap before migration."
                try:
                    update_table_status_snowflake(
                        connection_id, table_name_val, "failed", job_name, migration_session_id
                    )
                except Exception as status_error:
                    log(f"[MIGRATION] Failed to update removed-mapping table '{table_name_val}' status: {status_error}")

                try:
                    conn_db = postgres_connection()
                    cursor_db = conn_db.cursor()
                    migration_record_id = None
                    now_utc = datetime.now(timezone.utc)
                    cursor_db.execute(
                        """
                        UPDATE migration_record
                        SET status = %s, updated_at = NOW()
                        WHERE job_name = %s
                          AND migration_session_id = %s
                          AND table_name = %s
                        """,
                        ("failed", job_name, migration_session_id, table_name_val),
                    )
                    exact_update_count = cursor_db.rowcount

                    cursor_db.execute(
                        """
                        SELECT id
                        FROM migration_record
                        WHERE job_name = %s
                          AND migration_session_id = %s
                          AND table_name = %s
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (job_name, migration_session_id, table_name_val),
                    )
                    row = cursor_db.fetchone()
                    if row and row[0]:
                        migration_record_id = row[0]

                    if exact_update_count == 0:
                        cursor_db.execute(
                            """
                            UPDATE migration_record
                            SET status = %s, updated_at = NOW()
                            WHERE job_name = %s
                              AND migration_session_id = %s
                              AND table_name ILIKE %s
                            """,
                            ("failed", job_name, migration_session_id, table_name_val),
                        )
                        cursor_db.execute(
                            """
                            SELECT id
                            FROM migration_record
                            WHERE job_name = %s
                              AND migration_session_id = %s
                              AND table_name ILIKE %s
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (job_name, migration_session_id, table_name_val),
                        )
                        row = cursor_db.fetchone()
                        if row and row[0]:
                            migration_record_id = row[0]

                    if migration_record_id:
                        cursor_db.execute("SELECT nextval('log_log_id_seq')")
                        table_log_id = cursor_db.fetchone()[0]
                        cursor_db.execute(
                            """
                            INSERT INTO log
                                (log_id, cmp_id, connection_id, user_id, time, status, message, session, job_name, migration_record_id)
                            VALUES
                                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                table_log_id,
                                cmp_id,
                                connection_id,
                                user_id,
                                now_utc,
                                "F",
                                f"{table_name_val}: {removed_mapping_message}",
                                session,
                                job_name,
                                migration_record_id,
                            ),
                        )
                        cursor_db.execute(
                            """
                            INSERT INTO data_transfer
                                (migration_record_id, table_name, start_time, end_time, log_id, new_rec, mod_rec, del_rec, total_rec)
                            VALUES
                                (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                migration_record_id,
                                table_name_val,
                                now_utc,
                                now_utc,
                                table_log_id,
                                0,
                                0,
                                0,
                                0,
                            ),
                        )
                    else:
                        log(
                            f"[MIGRATION] Could not find migration_record_id for removed-mapping table "
                            f"'{table_name_val}' (job_name={job_name}, session={migration_session_id})"
                        )

                    conn_db.commit()
                    cursor_db.close()
                    conn_db.close()
                except Exception as record_error:
                    log(f"[MIGRATION] Failed to mark migration_record failed for removed-mapping table '{table_name_val}': {record_error}")

            if not keys:
                if file_mapping_mode == "multi_files_single_table" and multi_no_files_discovered:
                    table_name = sanitize_identifier(str(multi_files_table_name or "multi_files_single_table").strip())
                    error_msg = "No files found in folder for processing."
                    log(f"[MIGRATION] {error_msg}")
                    try:
                        error_context = {
                            "table_name": table_name,
                            "service_name": "SFTP to Snowflake",
                            "error_type": "TABLE_MIGRATION_FAILED",
                            "cmp_id": cmp_id,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "job_name": job_name,
                            "migration_session_id": migration_session_id,
                            "session": session,
                            "source_file": None,
                            "log_id": log_id
                        }
                        handle_service_error(
                            error=FileNotFoundError(error_msg),
                            context=error_context,
                            log_files=None,
                            send_email=False,
                            is_final_attempt=is_final_attempt
                        )
                    except Exception as log_error:
                        log(f"[MIGRATION] Failed to write table-level error for '{table_name}': {log_error}")
                    _mark_selected_tables_failed_on_terminal_error()
                    try:
                        log_migration_completion(
                            execution_time=0.0,
                            total_rows=0,
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            user_id=user_id,
                            session=migration_session_id,
                            total_tables=1,
                            successful_tables=0,
                            failed_tables=1,
                            job_name=job_name,
                            failure_reason=error_msg,
                            status_override="F",
                            include_operation_counts=should_include_operation_counts(load_method),
                            suppress_reason_in_completion=True,
                            celery_child_mode=request.celery_child_mode,
                        )
                    except Exception as completion_log_error:
                        log(f"[MIGRATION] Failed to log migration completion: {completion_log_error}")
                    return {
                        "status": "failed",
                        "message": error_msg,
                        "files_processed": 0,
                        "failed_tables": 1,
                        "processed_tables": [],
                    }
                if failed_removed_tables:
                    for table_name in failed_removed_tables:
                        _mark_removed_mapping_table_failed(table_name)
                    error_msg = f"SFTP table mapping removed for selected table(s): {', '.join(failed_removed_tables)}"
                    return {
                        "status": "failed",
                        "message": error_msg,
                        "files_processed": 0,
                        "failed_tables": len(failed_removed_tables),
                        "processed_tables": [],
                    }
                return {"status": "failed", "message": "No files to process", "files_processed": 0}

            # Drop and reload preparation
            if drop_and_reload:
                try:
                    tables_to_drop = set()
                    for key in keys:
                        if file_mapping_mode == "multi_files_single_table":
                            tables_to_drop.add(sanitize_identifier(multi_files_table_name))
                        else:
                            mapped_name = single_file_map.get(key)
                            table_name = sanitize_identifier(mapped_name) if mapped_name else table_from_file_key(key)
                            if table_name:
                                tables_to_drop.add(table_name)

                    with conn.cursor() as drop_cur:
                        for table_name in tables_to_drop:
                            log(f"[DROP] Dropping Snowflake table {sf_database}.{sf_schema}.{table_name} for drop_and_reload")
                            drop_cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(table_name)}")
                except Exception as drop_error:
                    log(f"Error during drop and reload: {drop_error}")

            # Process files using streaming chunks
            processed = 0
            total_rows = 0
            total_inserts = 0
            total_updates = 0
            total_deletes = 0
            table_operation_counts = {}
            user_pk_map = {}
            migration_start_time = datetime.now(timezone.utc)
            truncated_tables = set()

            def _truncate_before_merge(target_table):
                metadata = last_checked_data.get(target_table, {})
                truncate = str(metadata.get("truncate_data", False)).lower() in {"true", "1", "yes", "on"}
                if truncate and not drop_and_reload and target_table not in truncated_tables:
                    with conn.cursor() as cur:
                        cur.execute(f"TRUNCATE TABLE {Q(sf_database)}.{Q(sf_schema)}.{Q(target_table)}")
                    truncated_tables.add(target_table)

            processed_files_by_table = {}
            missing_files_by_table = {}
            incompatible_files_by_table = {}
            missing_files_single_mode = set()
            available_files_seen = 0
            first_chunk_df = None
            multi_staged_source_rows = 0
            
            single_table = None
            original_table_name = None
            multi_files_schema_override = None
            allowed_new_columns = None
            if file_mapping_mode == "multi_files_single_table":
                log("[MIGRATION] File mapping mode: Multi-file Single table")
                if multi_files_table_name:
                    original_table_name = sanitize_identifier(multi_files_table_name)
                    single_table = sanitize_identifier(multi_files_table_name)
                    log(f"[MIGRATION] Using user-specified unified table name: {single_table} (original: {original_table_name})")
                else:
                    suggested_name = suggest_common_table_name(keys)
                    original_table_name = sanitize_identifier(suggested_name)
                    single_table = sanitize_identifier(suggested_name)
                    log(f"[MIGRATION] Using unified table name from suggestion: {single_table} (original: {original_table_name})")

                table_meta = {}
                if isinstance(last_checked_data, dict) and single_table:
                    table_meta = last_checked_data.get(single_table, {}) or last_checked_data.get(original_table_name, {})
                    if not table_meta:
                        lookup = {str(k).upper(): v for k, v in last_checked_data.items()}
                        table_meta = lookup.get(str(single_table).upper(), {}) if single_table else {}
                expected_columns_for_multi = (
                    table_meta.get("table_fields", {}) if isinstance(table_meta, dict) else {}
                )
                if isinstance(expected_columns_for_multi, dict) and expected_columns_for_multi:
                    allowed_new_columns = compute_common_new_columns_for_multi_files_sftp(
                        sftp_client, keys, file_type,
                        expected_columns_for_multi,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                    )
                    if not allowed_new_columns:
                        log("[VALIDATE] No new columns common to all files; schema additions disabled for this run.")

            # In multi-file single-table mode, infer schema across ALL files before table creation
            # so numeric types (e.g., FLOAT vs NUMBER) are correctly resolved.
            if file_mapping_mode == "multi_files_single_table" and keys:
                def _type_family(t: str) -> str:
                    t = str(t or "").upper()
                    if any(x in t for x in ("FLOAT", "DOUBLE", "REAL", "NUMBER", "DECIMAL", "NUMERIC")):
                        return "numeric"
                    if "TIMESTAMP" in t or t == "DATE":
                        return "datetime"
                    if "BOOLEAN" in t:
                        return "boolean"
                    if "VARIANT" in t:
                        return "variant"
                    return "text"

                def _widen_type(existing: str, incoming: str) -> str:
                    if not existing:
                        return incoming
                    if not incoming:
                        return existing
                    e = str(existing).upper()
                    i = str(incoming).upper()
                    ef = _type_family(e)
                    if ef != _type_family(i):
                        # Keep existing when families differ; schema validation will catch incompatibilities.
                        return existing
                    if ef == "numeric":
                        # Prefer FLOAT if any sample indicates decimals.
                        if "FLOAT" in e or "FLOAT" in i:
                            return "FLOAT"
                    return existing

                merged_schema = {}
                schema_files_attempted = 0
                schema_files_skipped = 0
                log("[MIGRATION][SCHEMA_PRE] Inferring schema across all files for multi-file table creation...")
                for key in keys:
                    schema_files_attempted += 1
                    actual_columns, err = infer_columns_from_sftp_sample(
                        sftp_client, key, file_type,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                    )
                    if actual_columns is None:
                        schema_files_skipped += 1
                        log(f"[MIGRATION][SCHEMA_PRE] Skipping '{key}' (no columns inferred): {err}")
                        continue
                    for col_name, col_meta in actual_columns.items():
                        col_u = sanitize_column_name(col_name).upper()
                        if col_u in {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}:
                            continue
                        incoming_type = None
                        if isinstance(col_meta, dict):
                            incoming_type = col_meta.get("data_type")
                        existing_meta = merged_schema.get(col_u)
                        existing_type = existing_meta.get("data_type") if isinstance(existing_meta, dict) else None
                        chosen_type = _widen_type(existing_type, incoming_type)
                        merged_schema[col_u] = {"data_type": chosen_type}
                if merged_schema:
                    multi_files_schema_override = merged_schema
                    log(
                        f"[MIGRATION][SCHEMA_PRE] Merged schema across {len(keys)} file(s): "
                        f"{len(merged_schema)} column(s) (sampled)."
                    )
                    log(
                        f"[MIGRATION][SCHEMA_PRE] Files scanned for schema: {schema_files_attempted}, "
                        f"skipped: {schema_files_skipped}"
                    )
                else:
                    log("[MIGRATION][SCHEMA_PRE] No schema override derived; falling back to per-file inference.")
                    log(
                        f"[MIGRATION][SCHEMA_PRE] Files scanned for schema: {schema_files_attempted}, "
                        f"skipped: {schema_files_skipped}"
                    )

            multi_staging_table_name = (
                staging_name(single_table.upper())
                if file_mapping_mode == "multi_files_single_table" and single_table
                else None
            )
            tables_in_progress = set()
            processed_tables = set()
            skipped_tables_delta = set()
            skipped_tables_safety = set()
            skipped_tables = set()
            table_exists_cache = {}

            if failed_removed_tables:
                for table_name in failed_removed_tables:
                    _mark_removed_mapping_table_failed(table_name)
                    processed_tables.add(table_name)
                    failure_tracker.add_failure(
                        table_name,
                        "SFTP table mapping removed. Kindly update schema or remap before migration.",
                        "SFTP to Snowflake"
                    )

            if file_mapping_mode == "multi_files_single_table" and original_table_name:
                try:
                    update_table_status_snowflake(
                        connection_id, original_table_name, "in_progress", job_name, migration_session_id
                    )
                except Exception as status_error:
                    log(f"[MIGRATION] Failed to set initial status for multi-file single table: {status_error}")

            staging_tables = {}

            for key in keys:
                log("\n" + "=" * 72)
                log(f"[MIGRATION] Processing file: {key}")
                log("=" * 72)

                if file_mapping_mode == "multi_files_single_table":
                    table = single_table
                else:
                    internal_name = table_from_file_key(key)
                    mapped_name = single_file_map.get(key)
                    table = sanitize_identifier(mapped_name) if mapped_name else internal_name

                current_staging_table = multi_staging_table_name if file_mapping_mode == "multi_files_single_table" else None
                table_metadata = last_checked_data.get(table, {})
                last_synced_timestamp = table_metadata.get("last_checked_date")
                if str(table_metadata.get("truncate_data", False)).lower() in {"true", "1", "yes", "on"}:
                    last_synced_timestamp = None
                
                # Check file compatibility and exists
                file_exists = True
                confirmed_missing_file = False
                root_folder = _sftp_listing_root_folder(source_config)
                log(f"[SFTP][DEBUG] Checking existence of file: '{key}' (Root folder: '{root_folder}')")
                try:
                    head_stat = sftp_client.stat(key)
                    file_last_modified = datetime.fromtimestamp(head_stat.st_mtime, timezone.utc)
                    available_files_seen += 1
                    log(f"[SFTP][DEBUG] File '{key}' exists on SFTP server. Size: {head_stat.st_size} bytes, modified: {file_last_modified}")
                except FileNotFoundError as fnf_err:
                    file_exists = False
                    confirmed_missing_file = True
                    log(f"[FILE] WARNING: File '{key}' not found on SFTP server. (FNF: {fnf_err})")
                    try:
                        parent = key.rsplit("/", 1)[0] if "/" in key else "."
                        log(f"[SFTP][DEBUG] Files in parent directory '{parent}': {sftp_client.listdir(parent)}")
                    except Exception as list_err:
                        log(f"[SFTP][DEBUG] Could not list parent directory '{parent}': {list_err}")
                except Exception as e:
                    file_exists = False
                    log(f"[FILE] WARNING: Could not access file '{key}': {e}")
                    try:
                        parent = key.rsplit("/", 1)[0] if "/" in key else "."
                        log(f"[SFTP][DEBUG] Files in parent directory '{parent}': {sftp_client.listdir(parent)}")
                    except Exception as list_err:
                        log(f"[SFTP][DEBUG] Could not list parent directory '{parent}': {list_err}")

                if not file_exists:
                    if file_mapping_mode == "multi_files_single_table":
                        missing_files_by_table.setdefault(table, []).append(key)
                        log(f"[MIGRATION] Multi-file mode: skipping missing file '{key}' for table '{table}' and continuing with remaining files")
                    else:
                        if confirmed_missing_file:
                            missing_files_single_mode.add(key)
                        failure_message = f"File '{key}' does not exist on SFTP server."
                        failure_tracker.add_failure(table, failure_message, "SFTP to Snowflake")
                        try:
                            error_context = {
                                "table_name": table,
                                "service_name": "SFTP to Snowflake",
                                "error_type": "TABLE_MIGRATION_FAILED",
                                "cmp_id": cmp_id,
                                "connection_id": connection_id,
                                "user_id": user_id,
                                "job_name": job_name,
                                "migration_session_id": migration_session_id,
                                "connection_name": connection_name,
                                "session": session,
                                "source_file": key,
                                "log_id": log_id
                            }
                            error_result = handle_service_error(
                                error=FileNotFoundError(failure_message),
                                context=error_context,
                                log_files=None,
                                # Match S3: record the table-level failure here and
                                # let the consolidated migration failure path own email delivery.
                                send_email=False,
                                is_final_attempt=is_final_attempt
                            )
                            table_level_error_email_sent = table_level_error_email_sent or bool(error_result.get("email_sent"))
                        except Exception as log_error:
                            log(f"[MIGRATION] Failed to write table-level error log for '{table}': {log_error}")
                        try:
                            status_table_name = (original_table_name or table)
                            update_table_status_snowflake(
                                connection_id, status_table_name, "failed", job_name, migration_session_id
                            )
                            if table in tables_in_progress:
                                tables_in_progress.remove(table)
                            processed_tables.add(table)
                            skipped_tables.discard(table)
                        except Exception as status_error:
                            log(f"[MIGRATION] Failed to mark table '{table}' as failed: {status_error}")
                    continue

                # Content-based compatibility check
                is_type_compatible, type_reason = is_sftp_file_compatible_with_type(
                    sftp=sftp_client,
                    key=key,
                    file_type=file_type,
                    delimiter=delimiter,
                    quotechar=quotechar,
                    skipheaderlines=skipheaderlines,
                    escape_char=escape_char,
                    skip_footer_lines=skip_footer_lines,
                    line_separator=line_separator,
                )
                if not is_type_compatible:
                    compatibility_message = (
                        f"Skipping file '{key}': incompatible with selected file_type '{file_type}'."
                    )
                    log(f"[MIGRATION][TYPE_CHECK] {compatibility_message}")
                    incompatible_files_by_table.setdefault(table, []).append(key)
                    if failure_tracker:
                        failure_tracker.add_failure(table, compatibility_message, "SFTP to Snowflake")
                    if file_mapping_mode != "multi_files_single_table":
                        try:
                            # Log exact table-level error message
                            error_context = {
                                "table_name": table,
                                "service_name": "SFTP to Snowflake",
                                "error_type": "FILE_TYPE_MISMATCH",
                                "cmp_id": cmp_id,
                                "connection_id": connection_id,
                                "user_id": user_id,
                                "job_name": job_name,
                                "migration_session_id": migration_session_id,
                                "session": session,
                                "source_file": key,
                                "log_id": log_id
                            }
                            handle_service_error(
                                error=ValueError(compatibility_message),
                                context=error_context,
                                log_files=None,
                                send_email=False,
                                is_final_attempt=is_final_attempt
                            )
                        except Exception as log_error:
                            log(f"[MIGRATION] Failed to write table-level error log for '{table}': {log_error}")
                        try:
                            status_table_name = (original_table_name or table)
                            update_table_status_snowflake(
                                connection_id, status_table_name, "failed", job_name, migration_session_id
                            )
                            if table in tables_in_progress:
                                tables_in_progress.remove(table)
                            processed_tables.add(table)
                            skipped_tables.discard(table)
                        except Exception as status_error:
                            log(f"[MIGRATION] Failed to mark table '{table}' as failed: {status_error}")
                    continue

                expected_columns = table_metadata.get("table_fields", {}) if isinstance(table_metadata, dict) else {}

                # In multi-file/single-table mode, enforce schema compatibility before loading.
                # This avoids noisy Snowflake load errors when users accidentally map heterogeneous files.
                if file_mapping_mode == "multi_files_single_table" and isinstance(expected_columns, dict) and expected_columns:
                    is_compatible, _, validation_error = validate_sftp_file_structure_matches(
                        sftp=sftp_client,
                        key=key,
                        file_type=file_type,
                        expected_columns=expected_columns,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                        enforce_type_compatibility=True,
                        allowed_new_columns=allowed_new_columns,
                    )
                    if not is_compatible:
                        compatibility_message = (
                            f"Skipping file '{key}' for table '{table}': schema/type mismatch. "
                            f"{validation_error}"
                        )
                        log(f"[MIGRATION][MULTI_VALIDATION] {compatibility_message}")
                        incompatible_files_by_table.setdefault(table, []).append(key)
                        if failure_tracker:
                            failure_tracker.add_failure(table, compatibility_message, "SFTP to Snowflake")
                        continue

                # Delta load checks
                should_process = True
                skip_reason = None
                
                if not drop_and_reload:
                    # If Snowflake table doesn't exist, force load
                    if last_synced_timestamp and sf_database and sf_schema:
                        cache_key = str(table).strip().upper()
                        if cache_key not in table_exists_cache:
                            table_exists_cache[cache_key] = _snowflake_table_exists(
                                conn, sf_database, sf_schema, cache_key
                            )
                        if not table_exists_cache[cache_key]:
                            last_synced_timestamp = None

                    # Apply safety interval cutoff
                    if safety_interval_cutoff and file_last_modified > safety_interval_cutoff:
                        should_process = False
                        skip_reason = "safety"
                    # Apply last synced delta check
                    elif last_synced_timestamp:
                        last_synced_dt = parse_datetime_from_db(last_synced_timestamp)
                        if last_synced_dt and file_last_modified <= last_synced_dt:
                            should_process = False
                            skip_reason = "delta"

                if not should_process:
                    log(f"[FILE] Skipping file '{key}' (not modified or too recent)")
                    skip_time_utc = datetime.now(timezone.utc)
                    if file_mapping_mode == "multi_files_single_table":
                        if skip_reason == "safety":
                            skipped_tables_safety.add(table)
                        else:
                            skipped_tables_delta.add(table)
                        skipped_tables.add(table)
                    else:
                        try:
                            if skip_reason == "safety":
                                update_table_status_snowflake(
                                    connection_id, table, "completed", job_name, migration_session_id, sync_end_time=None
                                )
                                skipped_tables_safety.add(table)
                            else:
                                update_table_status_snowflake(
                                    connection_id, table, "completed", job_name, migration_session_id, skip_time_utc
                                )
                                skipped_tables_delta.add(table)
                            skipped_tables.add(table)
                            processed_tables.add(table)
                        except Exception as status_error:
                            log(f"[FILE] Failed to mark skipped table completed: {status_error}")
                    continue

                # Process the file
                file_start_time = datetime.now(timezone.utc)
                if file_mapping_mode != "multi_files_single_table":
                    try:
                        update_table_status_snowflake(
                            connection_id, table, "in_progress", job_name, migration_session_id
                        )
                        tables_in_progress.add(table)
                    except Exception as status_error:
                        log(f"[MIGRATION] Failed to set status to in_progress: {status_error}")

                try:
                    # Enforce row/schema consistency (True/False based on setting or default)
                    enforce_consistency = True
                    json_expected_keys_state = {"expected": None}
                    
                    # Yield chunks of DataFrame from the SFTP stream
                    chunks_generator = read_sftp_file_in_chunks(
                        sftp_client, key,
                        file_type=file_type,
                        chunk_size=chunk_count,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                        enforce_row_schema_consistency=enforce_consistency,
                        enforce_json_key_consistency=True,
                        json_expected_keys_state=json_expected_keys_state,
                    )

                    gcs_parquet_urls = []
                    total_file_rows = 0

                    for chunk_df, chunk_num, is_last_chunk, last_modified_val, total_rows_val in chunks_generator:
                        if chunk_df.empty:
                            if is_last_chunk:
                                break
                            continue
                        total_file_rows = total_rows_val
                        if first_chunk_df is None:
                            first_chunk_df = chunk_df.copy()

                        # Ensure tables exist (only on first chunk)
                        if chunk_num == 1:
                            if file_mapping_mode in ("single_table_per_file", "multi_files_single_table"):
                                try:
                                    target_table_for_schema = original_table_name or table
                                    inferred_columns = infer_columns_from_df(
                                        chunk_df,
                                        load_method,
                                        table_name=target_table_for_schema,
                                        internal_name=table,
                                        custom_primary_key=custom_pk_mapping if load_method == "upsert_custom_key" else None,
                                    )
                                    if isinstance(inferred_columns, dict) and inferred_columns:
                                        # Persist schema in standardized uppercase keys for consistency
                                        normalized_inferred_columns = {}
                                        for col_name, meta in inferred_columns.items():
                                            norm_name = sanitize_column_name(str(col_name)).upper()
                                            if not norm_name:
                                                continue
                                            normalized_inferred_columns[norm_name] = meta

                                        normalized_expected_columns = {}
                                        if isinstance(expected_columns, dict):
                                            for col_name, meta in expected_columns.items():
                                                norm_name = sanitize_column_name(str(col_name)).upper()
                                                if not norm_name:
                                                    continue
                                                normalized_expected_columns[norm_name] = meta

                                        # Detect column rename (remove + add) in single_table_per_file mode.
                                        if (
                                            file_mapping_mode == "single_table_per_file"
                                            and not drop_and_reload
                                            and normalized_expected_columns
                                        ):
                                            metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
                                            inferred_cols = set(normalized_inferred_columns.keys()) - metadata_cols
                                            expected_cols = set(normalized_expected_columns.keys()) - metadata_cols
                                            removed_cols = expected_cols - inferred_cols
                                            added_cols = inferred_cols - expected_cols
                                            if removed_cols and added_cols:
                                                removed_preview = ", ".join(sorted(list(removed_cols))[:3])
                                                added_preview = ", ".join(sorted(list(added_cols))[:3])
                                                raise ValueError(
                                                    "Column name changed detected. "
                                                    f"Removed: {removed_preview}. Added: {added_preview}. "
                                                    "Please correct the file header or run Drop & Reload."
                                                )

                                        # Detect column rename in multi_files_single_table when ALL files share the new columns
                                        if (
                                            file_mapping_mode == "multi_files_single_table"
                                            and not drop_and_reload
                                            and normalized_expected_columns
                                            and allowed_new_columns is not None
                                        ):
                                            metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
                                            inferred_cols = set(normalized_inferred_columns.keys()) - metadata_cols
                                            expected_cols = set(normalized_expected_columns.keys()) - metadata_cols
                                            removed_cols = expected_cols - inferred_cols
                                            added_cols = inferred_cols - expected_cols
                                            if removed_cols and added_cols and added_cols.issubset(allowed_new_columns):
                                                removed_preview = ", ".join(sorted(list(removed_cols))[:3])
                                                added_preview = ", ".join(sorted(list(added_cols))[:3])
                                                raise ValueError(
                                                    "Column name changed detected. "
                                                    f"Removed: {removed_preview}. Added: {added_preview}. "
                                                    "Please correct the file header or run Drop & Reload."
                                                )

                                        if normalized_inferred_columns != normalized_expected_columns:
                                            should_update_schema = True
                                            if file_mapping_mode == "multi_files_single_table" and allowed_new_columns is not None:
                                                metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_SPECT_SYNCED", "_SPECT_DELETED", "_DATA"}
                                                inferred_cols = set(normalized_inferred_columns.keys()) - metadata_cols
                                                expected_cols = set(normalized_expected_columns.keys()) - metadata_cols
                                                new_cols = inferred_cols - expected_cols
                                                disallowed_new = new_cols - allowed_new_columns
                                                if disallowed_new:
                                                    should_update_schema = False
                                                    log(
                                                        f"[MIGRATION][SCHEMA_SYNC] Skipping schema update for '{table}' "
                                                        f"due to unexpected new columns: {disallowed_new}"
                                                    )
                                            if should_update_schema:
                                                conn_db = None
                                                cursor_db = None
                                                try:
                                                    conn_db = postgres_connection()
                                                    cursor_db = conn_db.cursor()
                                                    cursor_db.execute(
                                                        """
                                                        UPDATE source_table_selection
                                                        SET table_fields = %s
                                                        WHERE connection_id = %s
                                                          AND (UPPER("table") = %s OR "table" = %s)
                                                        """,
                                                        (
                                                            json.dumps(normalized_inferred_columns),
                                                            connection_id,
                                                            str(target_table_for_schema).strip().upper(),
                                                            target_table_for_schema,
                                                        ),
                                                    )
                                                    conn_db.commit()
                                                finally:
                                                    if cursor_db:
                                                        cursor_db.close()
                                                    if conn_db:
                                                        conn_db.close()
                                                expected_columns = normalized_inferred_columns
                                                if table in last_checked_data and isinstance(last_checked_data[table], dict):
                                                    last_checked_data[table]["table_fields"] = normalized_inferred_columns
                                                log(
                                                    f"[MIGRATION][SCHEMA_SYNC] Updated source_table_selection schema for "
                                                    f"table '{table}' with {len(normalized_inferred_columns)} column(s)"
                                                )
                                except Exception as schema_sync_error:
                                    log(f"[MIGRATION][SCHEMA_SYNC] Failed to update source_table_selection schema for table '{table}': {schema_sync_error}")
                                    if "Column name changed detected" in str(schema_sync_error):
                                        raise

                            staging_table_name = ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                custom_pk_mapping.get(table.upper(), []),
                                sf_database=sf_database,
                                sf_schema=sf_schema,
                                staging_table_name=multi_staging_table_name if file_mapping_mode == "multi_files_single_table" else None,
                                schema_override=multi_files_schema_override if file_mapping_mode == "multi_files_single_table" else None,
                            )
                            current_staging_table = staging_table_name
                            staging_tables[table] = staging_table_name
                        elif current_staging_table:
                            ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                custom_pk_mapping.get(table.upper(), []),
                                sf_database=sf_database,
                                sf_schema=sf_schema,
                                staging_table_name=current_staging_table,
                                schema_override=multi_files_schema_override if file_mapping_mode == "multi_files_single_table" else None,
                            )

                        # Align and reorder columns
                        chunk_df = align_chunk_to_table_column_order(
                            conn, chunk_df, current_staging_table,
                            sf_database=sf_database,
                            sf_schema=sf_schema,
                        )
                        chunk_df = reorder_chunk_columns_for_snowflake(
                            chunk_df, load_method,
                            custom_pk_mapping.get(table.upper(), []),
                        )

                        # Upload staging chunk to GCS as CSV
                        stage_delimiter = ","
                        stage_quotechar = '"'
                        stage_file_type = "csv"

                        chunk_gcs_path = upload_chunk_to_gcs(
                            gcs, chunk_df, table, chunk_num,
                            gcs_bucket_name,
                            delimiter=stage_delimiter,
                            quotechar=stage_quotechar,
                            file_type=stage_file_type
                        )
                        gcs_full_url = f"gcs://{gcs_bucket_name}/{chunk_gcs_path}"
                        gcs_parquet_urls.append(gcs_full_url)
                        SimpleProgressTracker.accumulate_staging_progress_batch(
                            connection_id=connection_id,
                            migration_session_id=migration_session_id,
                            job_name=job_name,
                            table_name=table,
                            batch_rows=len(chunk_df),
                        )

                        if is_last_chunk:
                            break

                    # Load from GCS into Snowflake staging table
                    if gcs_parquet_urls:
                        copy_skiprows = 1
                        copy_delimiter = ","
                        copy_quotechar = '"'
                        
                        copy_into_staging_from_gcs_batch(
                            conn, gcs_parquet_urls, current_staging_table,
                            sf_database, sf_schema, sf_storage_integration,
                            gcs_bucket_name,
                            delimiter=copy_delimiter, quotechar=copy_quotechar,
                            skipheaderlines=copy_skiprows, file_type="csv",
                            connection_id=connection_id,
                            migration_session_id=str(migration_session_id),
                            job_name=job_name,
                            table_name=table
                        )

                        if file_mapping_mode == "multi_files_single_table":
                            multi_staged_source_rows += int(total_file_rows or 0)
                            processed += 1
                            processed_tables.add(table)
                            skipped_tables.discard(table)
                            skipped_tables_delta.discard(table)
                            processed_files_by_table.setdefault(table, []).append(key)
                            log(f"[MIGRATION] Staged file '{key}' into shared staging table '{current_staging_table}'")
                            continue

                        # Merge staging table into final Snowflake table
                        enable_delete_sync = (not drop_and_reload) and load_method == "upsert_custom_key"
                        _truncate_before_merge(table)
                        merge_result = merge_staging_into_final(
                            conn,
                            table,
                            current_staging_table,
                            load_method,
                            custom_pk_mapping.get(table.upper(), []),
                            sf_database,
                            sf_schema,
                            source_file_key=key,
                            delete_missing_rows=enable_delete_sync,
                        )

                        merge_inserted = merge_result.get("inserted", 0)
                        merge_updated = merge_result.get("updated", 0)
                        merge_deleted = merge_result.get("deleted", 0)

                        table_ops = table_operation_counts.setdefault(
                            table, {"inserts": 0, "updates": 0, "deletes": 0}
                        )
                        table_ops["inserts"] += merge_inserted
                        table_ops["updates"] += merge_updated
                        table_ops["deletes"] += merge_deleted

                        total_inserts += merge_inserted
                        total_updates += merge_updated
                        total_deletes += merge_deleted
                        total_rows += (merge_inserted + merge_updated + merge_deleted)

                        # Update staging progress tracker for real-time WebSocket progress updates
                        cumulative_effective_rows = (
                            table_ops["inserts"] + table_ops["updates"] + table_ops["deletes"]
                        )
                        if all([connection_id, migration_session_id, job_name, table]):
                            try:
                                SimpleProgressTracker.set_staging_count(
                                    connection_id=connection_id,
                                    migration_session_id=str(migration_session_id),
                                    job_name=job_name,
                                    table_name=table,
                                    count_value=cumulative_effective_rows,
                                )
                                log(
                                    f"[MIGRATION] Updated staging_records_count with effective rows for table '{table}': "
                                    f"{cumulative_effective_rows:,} (Inserted+Updated+Deleted)"
                                )
                            except Exception as progress_err:
                                log(
                                    f"[MIGRATION] Failed to set effective staging_records_count for table '{table}': {progress_err}"
                                )

                        # Update status of single table
                        migration_end_time = datetime.now(timezone.utc)
                        update_table_status_snowflake(
                            connection_id, table, "completed", job_name, migration_session_id, migration_end_time
                        )

                        # Write Data Transfer Logs in PostgreSQL
                        if job_name:
                            cols_count = len(first_chunk_df.columns) if first_chunk_df is not None else 0
                            data_transfer_logs(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                user_id=user_id,
                                table_name=table,
                                start_time=file_start_time,
                                end_time=migration_end_time,
                                columns_data=cols_count,
                                session=session,
                                total_rows=(merge_inserted + merge_updated + merge_deleted),
                                inserts=merge_inserted,
                                updates=merge_updated,
                                deletes=merge_deleted,
                                job_name=job_name
                            )

                        # Clean up staging table
                        try:
                            with conn.cursor() as cleanup_cur:
                                cleanup_cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(current_staging_table)}")
                        except Exception as e:
                            log(f"Failed to drop staging table '{current_staging_table}': {e}")

                        processed += 1
                        processed_tables.add(table)
                        skipped_tables.discard(table)
                        skipped_tables_delta.discard(table)

                except Exception as file_error:
                    log(f"[FILE] Error processing file '{key}': {file_error}")
                    log(traceback.format_exc())
                    
                    target_table = original_table_name or table
                    if file_mapping_mode != "multi_files_single_table":
                        update_table_status_snowflake(
                            connection_id, table, "failed", job_name, migration_session_id
                        )
                        processed_tables.add(table)
                    else:
                        update_table_status_snowflake(
                            connection_id, target_table, "failed", job_name, migration_session_id
                        )
                        processed_tables.add(target_table)

                    failure_tracker.add_failure(target_table, str(file_error), "SFTP to Snowflake")

                    # Log failure details to diagnostic tools table
                    try:
                        conn_db = postgres_connection()
                        cursor_db = conn_db.cursor()
                        sftp_record_id = get_migration_record_id(
                            cursor_db,
                            job_name=job_name,
                            table_name=target_table,
                            connection_id=connection_id,
                        )
                        cursor_db.close()
                        conn_db.close()
                        if sftp_record_id:
                            insert_tool_log(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                usr_id=user_id,
                                table_name=target_table,
                                status="F",
                                message=str(file_error),
                                session=migration_session_id,
                                job_name=job_name,
                                migration_record_id=sftp_record_id,
                            )
                    except Exception as tool_log_err:
                        log(f"[MIGRATION] Failed to insert tool log for failed table '{target_table}': {tool_log_err}")

                    try:
                        error_context = {
                            "table_name": target_table,
                            "service_name": "SFTP to Snowflake",
                            "error_type": "TABLE_MIGRATION_FAILED",
                            "cmp_id": cmp_id,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "job_name": job_name,
                            "migration_session_id": migration_session_id,
                            "connection_name": connection_name,
                            "session": session,
                            "source_file": key,
                            "log_id": log_id
                        }
                        error_result = handle_service_error(
                            error=file_error,
                            context=error_context,
                            log_files=None,
                            send_email=True,
                            is_final_attempt=is_final_attempt
                        )
                        table_level_error_email_sent = table_level_error_email_sent or bool(error_result.get("email_sent"))
                    except Exception as log_error:
                        log(f"[MIGRATION] Failed to write table-level error log for '{target_table}': {log_error}")

                    # Cleanup staging
                    if current_staging_table:
                        try:
                            with conn.cursor() as cleanup_cur:
                                cleanup_cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(current_staging_table)}")
                        except Exception:
                            pass

            # Multi-file single table: Perform one final merge at the end
            if (
                file_mapping_mode == "multi_files_single_table"
                and single_table
                and multi_staging_table_name
                and processed_files_by_table.get(single_table)
                and not any(
                    str(item.get("table_name", "")).strip().upper() == str(original_table_name or single_table).strip().upper()
                    and not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                    for item in (failure_tracker.failed_tables or []) if isinstance(item, dict)
                )
            ):
                try:
                    enable_delete_sync = (not drop_and_reload) and load_method == "upsert_custom_key"
                    _truncate_before_merge(single_table)
                    merge_result = merge_staging_into_final(
                        conn,
                        single_table,
                        multi_staging_table_name,
                        load_method,
                        custom_pk_mapping.get(single_table.upper(), []),
                        sf_database,
                        sf_schema,
                        source_file_key=None,
                        source_file_keys=processed_files_by_table.get(single_table, []),
                        delete_missing_rows=enable_delete_sync,
                    )
                    merge_inserted = merge_result.get("inserted", 0)
                    merge_updated = merge_result.get("updated", 0)
                    merge_deleted = merge_result.get("deleted", 0)

                    table_ops = table_operation_counts.setdefault(
                        single_table, {"inserts": 0, "updates": 0, "deletes": 0}
                    )
                    table_ops["inserts"] += merge_inserted
                    table_ops["updates"] += merge_updated
                    table_ops["deletes"] += merge_deleted

                    total_inserts += merge_inserted
                    total_updates += merge_updated
                    total_deletes += merge_deleted
                    total_rows += (merge_inserted + merge_updated + merge_deleted)

                    # Update websocket progress staging count
                    cumulative_rows = table_ops["inserts"] + table_ops["updates"] + table_ops["deletes"]
                    SimpleProgressTracker.set_staging_count(
                        connection_id=connection_id,
                        migration_session_id=str(migration_session_id),
                        job_name=job_name,
                        table_name=original_table_name,
                        count_value=cumulative_rows,
                    )

                    # Cleanup staging table
                    try:
                        with conn.cursor() as cleanup_cur:
                            cleanup_cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(multi_staging_table_name)}")
                    except Exception:
                        pass

                except Exception as final_merge_error:
                    log(f"[MIGRATION] Final multi-file merge failed: {final_merge_error}")
                    update_table_status_snowflake(
                        connection_id, original_table_name, "failed", job_name, migration_session_id
                    )
                    processed_tables.add(original_table_name)
                    failure_tracker.add_failure(original_table_name, str(final_merge_error), "SFTP to Snowflake")
                    try:
                        error_context = {
                            "table_name": original_table_name,
                            "service_name": "SFTP to Snowflake",
                            "error_type": "TABLE_MIGRATION_FAILED",
                            "cmp_id": cmp_id,
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "job_name": job_name,
                            "migration_session_id": migration_session_id,
                            "connection_name": connection_name,
                            "session": session,
                            "source_file": None,
                            "log_id": log_id
                        }
                        error_result = handle_service_error(
                            error=final_merge_error,
                            context=error_context,
                            log_files=None,
                            send_email=True,
                            is_final_attempt=is_final_attempt
                        )
                        table_level_error_email_sent = table_level_error_email_sent or bool(error_result.get("email_sent"))
                    except Exception as log_error:
                        log(f"[MIGRATION] Failed to write final merge error log for '{original_table_name}': {log_error}")
                    # Drop the shared staging table so it does not linger in Snowflake after a merge abort.
                    try:
                        with conn.cursor() as cleanup_cur:
                            cleanup_cur.execute(
                                f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(multi_staging_table_name)}"
                            )
                        log(f"[MIGRATION] Dropped staging table '{multi_staging_table_name}' after merge abort")
                    except Exception as staging_drop_err:
                        log(f"[MIGRATION] Failed to drop staging table '{multi_staging_table_name}' after merge abort: {staging_drop_err}")

            # Finalize table status and data transfer logs for multi-file table operations
            if file_mapping_mode == "multi_files_single_table" and original_table_name:
                normalized_table_name = str(original_table_name).strip().upper()
                failed_entries_for_table = [
                    item
                    for item in (failure_tracker.failed_tables or [])
                    if isinstance(item, dict)
                    and str(item.get("table_name", "")).strip().upper() == normalized_table_name
                ]
                processed_for_multi = len(processed_files_by_table.get(single_table, []))
                has_non_skip_failure_for_multi = any(
                    not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                    for item in failed_entries_for_table
                )
                multi_failed = has_non_skip_failure_for_multi or (
                    len(failed_entries_for_table) > 0 and processed_for_multi == 0
                )
                    
                final_table_status = "failed" if multi_failed else "completed"
                migration_end_time = datetime.now(timezone.utc)
                final_sync_end_time = (
                    migration_end_time if final_table_status == "completed"
                    and single_table not in skipped_tables_safety else None
                )
                    
                update_table_status_snowflake(
                    connection_id,
                    original_table_name,
                    final_table_status,
                    job_name,
                    migration_session_id,
                    final_sync_end_time,
                )
                try:
                    
                    if job_name:
                        table_ops = table_operation_counts.get(single_table) or {"inserts": 0, "updates": 0, "deletes": 0}
                        cumulative_rows = table_ops.get("inserts", 0) + table_ops.get("updates", 0) + table_ops.get("deletes", 0)
                        
                        cols_count = 0
                        if 'first_chunk_df' in locals() and first_chunk_df is not None and hasattr(first_chunk_df, 'columns'):
                            cols_count = len(first_chunk_df.columns)
                            
                        data_transfer_logs(
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            user_id=user_id,
                            table_name=original_table_name,
                            start_time=migration_start_time,
                            end_time=migration_end_time,
                            columns_data=cols_count,
                            session=session,
                            total_rows=cumulative_rows,
                            inserts=table_ops.get("inserts", 0),
                            updates=table_ops.get("updates", 0),
                            deletes=table_ops.get("deletes", 0),
                            job_name=job_name
                        )
                except Exception as transfer_log_error:
                    log(f"[MIGRATION] Failed to log final data transfer for multi-file single table: {transfer_log_error}")

            if file_mapping_mode != "multi_files_single_table":
                # Retry any failed status write before reporting the migration result.
                for failure in failure_tracker.failed_tables:
                    update_table_status_snowflake(
                        connection_id, failure["table_name"], "failed", job_name, migration_session_id,
                    )

            # Finalize status & logs
            removed_mapping_count = len(failed_removed_tables) if file_mapping_mode == "single_table_per_file" else 0
            effective_total_tables = len(keys) + removed_mapping_count
            failure_reason_for_log = None
            log_status_override = None
            warning_detail_for_ui = None

            if failure_tracker and failure_tracker.has_failures():
                for item in (failure_tracker.failed_tables or []):
                    if not isinstance(item, dict):
                        continue
                    candidate_reason = str(item.get("error_message") or "").strip()
                    if candidate_reason:
                        failure_reason_for_log = candidate_reason
                        break

            # Determine operation type for conditional logic
            operation_type = None
            # For multi_files_single_table mode, treat as single table regardless of number of files
            is_single_table_operation = (
                (file_mapping_mode == "multi_files_single_table") or
                (drop_and_reload and effective_total_tables == 1) or
                (effective_total_tables == 1 and not drop_and_reload)
            )
            
            if is_single_table_operation and file_mapping_mode == "multi_files_single_table":
                operation_type = 'multi_files_single_table'
            elif is_single_table_operation and drop_and_reload:
                operation_type = 'drop_and_reload'
            elif is_single_table_operation:
                operation_type = 'single_table_refresh'

            # For single table operations, use standard message format
            if operation_type in ('drop_and_reload', 'single_table_refresh', 'multi_files_single_table'):
                if file_mapping_mode == "multi_files_single_table":
                    # For multi-file single-table mode, keep table summary at table level (1/1),
                    # but use explicit status override for partial (skip-only) outcomes.
                    total_tables_for_log = 1
                    failed_count = 0
                    failed_entries_for_table = [
                        item
                        for item in (failure_tracker.failed_tables or [])
                        if isinstance(item, dict)
                        and str(item.get("table_name", "")).strip().upper() == str(original_table_name).strip().upper()
                    ]
                    has_non_skip_failure_for_multi = any(
                        not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                        for item in failed_entries_for_table
                    )
                    skipped_file_keys = sorted(
                        set(
                            incompatible_files_by_table.get(single_table, [])
                            + missing_files_by_table.get(single_table, [])
                            + missing_files_by_table.get(original_table_name, [])
                        )
                    )
                    skipped_count = len(skipped_file_keys)
                    processed_for_multi = len(processed_files_by_table.get(single_table, []))

                    if has_non_skip_failure_for_multi:
                        failed_count = 1
                        successful_count = 0
                        log_status_override = "F"
                        if not failure_reason_for_log:
                            failure_reason_for_log = (
                                "Multi-file table failed due to non-skip error(s) during processing."
                            )
                        # Surface the exact error in the migration record so the UI shows it.
                        # Without this, the completion message only shows the generic summary
                        # because suppress_reason_in_completion=True strips the reason.
                        warning_detail_for_ui = failure_reason_for_log
                    elif skipped_count > 0 and processed_for_multi == 0:
                        # All files were skipped (schema/type mismatch or missing), so the table did not load at all.
                        # Treat this as a failure and surface a concrete error message.
                        failed_count = 1
                        successful_count = 0
                        log_status_override = "F"
                        latest_skip_detail = next(
                            (
                                str(item.get("error_message", "")).strip()
                                for item in reversed(failed_entries_for_table)
                                if str(item.get("error_message", "")).strip().startswith("Skipping file '")
                            ),
                            "",
                        )
                        sample = ", ".join(skipped_file_keys[:2])
                        more = f" (+{skipped_count - 2} more)" if skipped_count > 2 else ""
                        failure_reason_for_log = (
                            latest_skip_detail
                            or f"All files skipped for table due to schema/type mismatch or missing files: {sample}{more}"
                        )
                        warning_detail_for_ui = failure_reason_for_log
                    elif skipped_count > 0 and processed_for_multi > 0:
                        failed_count = 0
                        successful_count = 1
                        log_status_override = "P"
                        sample = ", ".join(skipped_file_keys[:2])
                        more = f" (+{skipped_count - 2} more)" if skipped_count > 2 else ""
                        latest_skip_detail = next(
                            (
                                str(item.get("error_message", "")).strip()
                                for item in reversed(failed_entries_for_table)
                                if str(item.get("error_message", "")).strip().startswith("Skipping file '")
                            ),
                            "",
                        )
                        failure_reason_for_log = (
                            latest_skip_detail
                            or f"{skipped_count} file(s) skipped due schema/type mismatch: {sample}{more}"
                        )
                        warning_detail_for_ui = failure_reason_for_log
                        if skipped_count > 1 and skipped_file_keys:
                            warning_detail_for_ui = (
                                f"{warning_detail_for_ui} | Skipped files: {', '.join(skipped_file_keys[:5])}"
                            )
                    else:
                        failed_count = 0
                        successful_count = 1
                        log_status_override = "S"
                else:
                    total_tables_for_log = effective_total_tables
                    failed_count = len(failure_tracker.failed_tables) if failure_tracker and failure_tracker.has_failures() else 0
                    successful_count = max(total_tables_for_log - failed_count, 0)
                
                # Log migration completion in error_utils/logs
                try:
                    log_migration_completion(
                        execution_time=(time.time() - start_time),
                        total_rows=total_rows,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        session=migration_session_id,
                        total_tables=total_tables_for_log,
                        successful_tables=successful_count,
                        failed_tables=failed_count,
                        job_name=job_name,
                        inserts=total_inserts,
                        updates=total_updates,
                        deletes=total_deletes,
                        include_operation_counts=should_include_operation_counts(load_method),
                        log_id=log_id,
                        failure_reason=failure_reason_for_log,
                        status_override=log_status_override,
                        suppress_reason_in_completion=True,
                        celery_child_mode=request.celery_child_mode,
                    )
                except Exception as completion_log_error:
                    log(f"[MIGRATION] Failed to log migration completion: {completion_log_error}")

                if (
                    file_mapping_mode == "multi_files_single_table"
                    and log_status_override == "P"
                    and warning_detail_for_ui
                    and all([cmp_id, connection_id, user_id, job_name, migration_session_id])
                ):
                    # Write mismatch warning as TABLE-level log
                    try:
                        warning_table = original_table_name or single_table or "Migration"
                        warn_conn = postgres_connection()
                        warn_cursor = warn_conn.cursor()
                        warning_migration_record_id = get_migration_record_id(
                            warn_cursor,
                            job_name=job_name,
                            table_name=warning_table,
                            connection_id=connection_id,
                        )
                        warn_cursor.close()
                        warn_conn.close()
                        if warning_migration_record_id:
                            insert_tool_log(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                usr_id=user_id,
                                table_name=warning_table,
                                status="P",
                                message=warning_detail_for_ui,
                                session=migration_session_id,
                                job_name=job_name,
                                migration_record_id=warning_migration_record_id,
                            )
                    except Exception as warn_log_err:
                        log(f"[MIGRATION] Failed to write SFTP partial warning log: {warn_log_err}")

                if (
                    file_mapping_mode == "multi_files_single_table"
                    and log_status_override == "F"
                    and warning_detail_for_ui
                    and all([cmp_id, connection_id, user_id, job_name, migration_session_id])
                ):
                    # Write schema mismatch as TABLE-level failure log so UI shows exact error_message.
                    try:
                        from utils.error_utils import update_table_migration_status_log
                        warning_table = original_table_name or single_table or "Migration"
                        warn_conn = postgres_connection()
                        warn_cursor = warn_conn.cursor()
                        warning_migration_record_id = get_migration_record_id(
                            warn_cursor,
                            job_name=job_name,
                            table_name=warning_table,
                            connection_id=connection_id,
                        )
                        warn_cursor.close()
                        warn_conn.close()
                        if warning_migration_record_id:
                            updated = update_table_migration_status_log(
                                migration_record_id=warning_migration_record_id,
                                message=warning_detail_for_ui,
                                status="F",
                                session=migration_session_id,
                                job_name=job_name,
                                connection_id=connection_id,
                            )
                            if not updated:
                                insert_tool_log(
                                    cmp_id=cmp_id,
                                    connection_id=connection_id,
                                    usr_id=user_id,
                                    table_name=warning_table,
                                    status="F",
                                    message=warning_detail_for_ui,
                                    session=migration_session_id,
                                    job_name=job_name,
                                    migration_record_id=warning_migration_record_id,
                                )
                    except Exception as warn_log_err:
                        log(f"[MIGRATION] Failed to write SFTP failure log: {warn_log_err}")
            else:
                failed_count = len(failure_tracker.failed_tables) if failure_tracker.has_failures() else 0
                successful_count = max(effective_total_tables - failed_count, 0)
                try:
                    log_migration_completion(
                        execution_time=(time.time() - start_time),
                        total_rows=total_rows,
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        session=migration_session_id,
                        total_tables=effective_total_tables,
                        successful_tables=successful_count,
                        failed_tables=failed_count,
                        job_name=job_name,
                        inserts=total_inserts,
                        updates=total_updates,
                        deletes=total_deletes,
                        include_operation_counts=should_include_operation_counts(load_method),
                        log_id=log_id,
                        failure_reason=failure_reason_for_log,
                        status_override=log_status_override,
                        suppress_reason_in_completion=True,
                        celery_child_mode=request.celery_child_mode,
                    )
                except Exception as completion_log_error:
                    log(f"[MIGRATION] Failed to log migration completion: {completion_log_error}")

            # Mirror S3 status rules so completion labels stay consistent across sources.
            if file_mapping_mode == "multi_files_single_table":
                normalized_table_name = str(original_table_name or "").strip().upper()
                failed_entries_for_table = [
                    item
                    for item in (failure_tracker.failed_tables or [])
                    if isinstance(item, dict)
                    and str(item.get("table_name", "")).strip().upper() == normalized_table_name
                ]
                processed_for_multi = len(processed_files_by_table.get(single_table, []))
                has_non_skip_failure_for_multi = any(
                    not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                    for item in failed_entries_for_table
                )
                multi_failed = has_non_skip_failure_for_multi or (
                    len(failed_entries_for_table) > 0 and processed_for_multi == 0
                )
                final_status = "failed" if multi_failed else "success"
            elif failed_count > 0 and processed == 0:
                final_status = "failed"
            else:
                final_status = "success"

            # Send consolidated error email only for true failures.
            if failure_tracker.has_failures() and not table_level_error_email_sent:
                should_send_consolidated_failure_email = True
                if file_mapping_mode == "multi_files_single_table" and original_table_name:
                    normalized_table_name = str(original_table_name).strip().upper()
                    failed_entries_for_table = [
                        item
                        for item in (failure_tracker.failed_tables or [])
                        if isinstance(item, dict)
                        and str(item.get("table_name", "")).strip().upper() == normalized_table_name
                    ]
                    processed_for_multi = len(processed_files_by_table.get(single_table, []))
                    has_non_skip_failure_for_multi = any(
                        not str(item.get("error_message", "")).strip().startswith("Skipping file '")
                        for item in failed_entries_for_table
                    )
                    only_skip_warnings = (
                        len(failed_entries_for_table) > 0
                        and not has_non_skip_failure_for_multi
                    )
                    if only_skip_warnings:
                        should_send_consolidated_failure_email = False
                        log(
                            "[MIGRATION] Skip-only file validation warnings detected in multi-file mode; "
                            "suppressing consolidated failure email."
                        )

                if should_send_consolidated_failure_email:
                    try:
                        failure_tracker.send_consolidated_error_email(
                            connection_name=connection_name,
                            service_name="SFTP to Snowflake",
                            user_id=user_id,
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            job_name=job_name,
                            migration_session_id=migration_session_id,
                            company_name=company_name,
                            is_final_attempt=is_final_attempt
                        )
                    except Exception as email_err:
                        log(f"[MIGRATION] Failed to send consolidated failure email: {email_err}")

            # Celery job cleanup
            try:
                handle_migration_completion(
                    connection_name=connection_name,
                    job_name=job_name,
                    completion_message=f"SFTP migration completed: {processed}/{len(keys)} files, {total_rows:,} rows"
                )
            except Exception:
                pass

            # Match S3: refresh mappings after migration completion has been logged.
            # This prunes missing single-file mappings and writes the refresh completion
            # status before returning the migration result.
            if (
                file_mapping_mode == "single_table_per_file"
                and missing_files_single_mode
                and not request.celery_child_mode
            ):
                try:
                    await _run_post_missing_file_schema_refresh()
                except Exception as refresh_err:
                    log(f"[MIGRATION] Post-migration SFTP schema refresh failed (non-fatal): {refresh_err}")

            return {
                "status": final_status,
                "files_processed": processed,
                "total_rows": total_rows,
                "total_inserts": total_inserts,
                "total_updates": total_updates,
                "total_deleted_recids": total_deletes,
                "execution_time": time.time() - start_time,
                "failed_tables": failed_count,
                "successful_tables_count": successful_count,
                "failed_tables_count": failed_count,
                "successful_tables_list": list(processed_tables),
                "failed_tables_list": [
                    str(item.get("table_name"))
                    for item in (failure_tracker.failed_tables or [])
                    if item.get("table_name")
                ],
                "processed_tables": list(processed_tables),
                "single_mode_missing_files_detected": bool(missing_files_single_mode),
                "single_mode_missing_files": sorted(missing_files_single_mode),
            }

        finally:
            _close_sftp_client(sftp_client, transport, sock)
            try:
                conn.close()
            except Exception:
                pass
