"""SAP OData direct HTTP load functions.

Three public functions used by the orchestrator:

- load_initial       -  full snapshot (initial / delete-and-load / initial_delta first run)
- load_delta         -  incremental delta pull (subsequent runs)
- load_initial       -  also handles delta-only subscription initialization

All shared helpers (date transforms, ADLS writing, metadata fetch, etc.) live in
services.sap.source so this file stays focused on SAP HTTP request / response logic.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import pandas as pd
import requests

from utils.timing_utils import log_execution_time
from services.sap.source import (
    SAP_MAX_PAGES,
    SAP_REQUEST_TIMEOUT,
    SAPSourceDetails,
    _apply_row_filter_to_page,
    _extract_delta_link,
    _fetch_delta_link_via_entity_set,
    _flush_buffer_to_local,
    _build_arrow_schema,
    _to_arrow_table,
    delete_adls_directory_contents,
    delete_adls_file,
    _transform_odata_page,
    extract_token_from_delta_link,
    fetch_metadata,
    force_https_url,
    generate_filename_base,
    generate_filename,
    write_adls,
)
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Keep the same local staging layout as the proxy loader.  In particular, a
# delta token directory is the manifest of files that the proxy removes from
# ADLS before refetching that token.
APP_DIR = Path(__file__).resolve().parent
TEMP_DIR = APP_DIR / "temporary_files"
DELTA_DIR = APP_DIR / "delta_files"


def _build_sap_session(server_address: str, source_config: Optional[Any] = None) -> requests.Session:
    """Build a SAP HTTP session using the configured Secret Manager TLS cert."""
    from utils.sap_tls_helper import build_session_from_config

    if isinstance(source_config, dict):
        config = dict(source_config)
        config.setdefault("host", server_address)
    elif source_config is not None:
        config = source_config
    else:
        config = {"host": server_address}
    return build_session_from_config(config)


def _fetch_metadata_and_arrow_schema(
    server_address: str,
    service_name: str,
    username: str,
    password: str,
    source_config: Optional[Any] = None,
) -> Tuple[bytes, Any]:
    """Fetch SAP metadata and build the schema used for Parquet staging."""
    metadata_url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/$metadata"
    schema_metadata = fetch_metadata(
        metadata_url, username, password, source_config_or_data=source_config
    )
    if not schema_metadata:
        raise ValueError("SAP metadata response was empty.")

    metadata_bytes = json.dumps(schema_metadata, indent=4).encode("utf-8")
    return metadata_bytes, _build_arrow_schema(metadata_bytes)


# ---------------------------------------------------------------------------
# SAP error response parser
# ---------------------------------------------------------------------------

def _parse_sap_error(status_code: int, response_text: str) -> str:
    """Return a human-readable message from a SAP Gateway JSON error payload."""
    try:
        response_data = json.loads(response_text)
        error_block   = response_data.get("error", {})
        if not error_block:
            return f"HTTP {status_code} from SAP: {response_text}"

        error_code = error_block.get("code", "")
        msg_obj    = error_block.get("message", {})
        error_msg  = msg_obj.get("value", "") if isinstance(msg_obj, dict) else str(msg_obj)

        inner_error   = error_block.get("innererror", {})
        error_details = []
        if isinstance(inner_error, dict):
            for detail in inner_error.get("errordetails", []):
                detail_msg = detail.get("message", "").strip()
                if detail_msg and detail_msg != error_msg:
                    error_details.append(detail_msg)

        lines = []
        if error_code:
            lines += ["Error Code:", error_code, ""]
        if error_msg:
            lines += ["Message:", error_msg, ""]
        if error_details:
            lines += ["Details:"] + error_details

        return "\n".join(lines).strip()
    except Exception:
        return f"HTTP {status_code} from SAP: {response_text}"


def _report_chunk_progress(
    connection_id: Optional[int],
    migration_session_id: Optional[Any],
    job_name: Optional[str],
    table_name: str,
    rows_written: int,
) -> None:
    """Helper to update staging records count and trigger WebSocket updates per chunk."""
    if connection_id is not None and migration_session_id is not None and job_name:
        try:
            from utils.simple_progress_tracker import SimpleProgressTracker
            SimpleProgressTracker.update_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                batch_rows=rows_written,
            )
        except Exception as tracker_err:
            logger.warning(
                "[%s] Chunk progress tracker update failed (non-fatal): %s",
                table_name, tracker_err,
            )


# ---------------------------------------------------------------------------
# load_initial
# ---------------------------------------------------------------------------

@log_execution_time
def load_initial(
    server_address: str,
    src_details: SAPSourceDetails,
    username: str,
    password: str,
    adls_config: Dict[str, Any],
    connection_id: Optional[int] = None,
    migration_session_id: Optional[Any] = None,
    job_name: Optional[str] = None,
    delta_only: bool = False,
    source_config: Optional[Any] = None,
) -> Tuple[bool, int, Optional[str]]:
    """Full initial snapshot from SAP Gateway written to ADLS ``/initial/``.

    Sends ``Prefer: odata.track-changes`` so SAP ODQ embeds a delta link on the
    final page. That link is returned but not persisted  -  the orchestrator decides
    whether to store it based on the configured load_method.

    Returns:
        (success, total_rows, delta_token)  — delta_token is None on failure.
    """
    service_name        = src_details.name
    entity_name         = src_details.entity_name
    initial_output_dir  = os.path.join(src_details.folder_name, "initial")
    file_record_count   = src_details.file_rec_count
    compression_type    = src_details.compress_type if src_details.compress else None
    page_size = src_details.pagination_count or 50000
    odata_headers       = {"Prefer": f"odata.track-changes,odata.maxpagesize={page_size}"}

    encoded_filter = None
    initial_url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/{entity_name}/?"
    if src_details.filter_expr:
        encoded_filter = quote(src_details.filter_expr, safe="'()")
        initial_url += f"$filter={encoded_filter}&"
    initial_url += "$format=json"
    initial_url = force_https_url(initial_url)

    logger.info(
        "[load_initial] Constructed URL — entity='%s' raw_filter=%r encoded_filter=%r url='%s'",
        entity_name,
        src_details.filter_expr,
        encoded_filter,
        initial_url,
    )
    logger.info(
        "[load_initial] Filter applied — filter=%r",
        src_details.filter_expr,
    )

    # Proxy-compatible delta subscription initialization: make one small
    # request only to create/advance the SAP ODQ subscription.  Do not page
    # through the initial dataset or create local/ADLS files.
    if delta_only:
        init_headers = {"Prefer": "odata.track-changes,odata.maxpagesize=1"}
        subscription_url = force_https_url(
            f"https://{server_address}/sap/opu/odata/sap/{service_name}/{entity_name}/?$format=json"
        )
        with _build_sap_session(server_address, source_config) as session:
            session.auth = (username, password)
            session.headers.update(init_headers)
            response = session.get(subscription_url, timeout=SAP_REQUEST_TIMEOUT)
            if response.status_code != 200:
                error_message = _parse_sap_error(response.status_code, response.text)
                logger.error("[load_initial] Delta subscription initialization failed: %s", error_message)
                return False, 0, None

            response_data = response.json()
            delta_link = _extract_delta_link(response_data)

        if src_details.delta_name:
            resolved_delta_link = _fetch_delta_link_via_entity_set(
                server_address=server_address,
                service_name=service_name,
                delta_name=src_details.delta_name,
                username=username,
                password=password,
                filter_expr=src_details.filter_expr,
                source_config_or_data=source_config,
            )
            if resolved_delta_link:
                delta_link = resolved_delta_link

        return True, 0, extract_token_from_delta_link(delta_link) if delta_link else None

    # Match the proxy's initial-load staging location.  Files remain available
    # until their ADLS upload succeeds, then are removed individually.
    staging_dir = TEMP_DIR / src_details.folder_name / "initial"
    os.makedirs(staging_dir, exist_ok=True)
    staged_files: list[Tuple[str, str]] = []
    metadata_bytes: Optional[bytes] = None
    remote_mutated = False
    logger.info(
        "[load_initial] Created local staging directory='%s' entity='%s'",
        str(staging_dir), entity_name,
    )

    run_timestamp     = src_details.timestamp or datetime.now(timezone.utc)
    delta_link        = None
    page_number       = 0
    total_rows        = 0
    file_number       = 1
    file_name_base    = generate_filename_base(run_timestamp)
    page_buffer: list = []
    buffered_rows     = 0
    writer = None
    single_file_path = None

    try:
        metadata_bytes, arrow_schema = _fetch_metadata_and_arrow_schema(
            server_address,
            service_name,
            username,
            password,
            source_config,
        )

        with _build_sap_session(server_address, source_config) as session:
            session.auth = (username, password)
            session.headers.update(odata_headers)
            current_url = initial_url
            while current_url:
                page_number += 1
                if page_number > SAP_MAX_PAGES:
                    logger.error(
                        "[load_initial] SAP_MAX_PAGES limit (%d) hit for entity '%s'. Aborting.",
                        SAP_MAX_PAGES, entity_name,
                    )
                    return False, total_rows, None

                logger.debug("[load_initial] Fetching page %d from '%s'", page_number, current_url)
                response = session.get(current_url, timeout=SAP_REQUEST_TIMEOUT)

                if response.status_code != 200:
                    error_message = _parse_sap_error(response.status_code, response.text)
                    logger.error("[load_initial] %s", error_message)
                    raise ValueError(error_message)

                response_data = response.json()
                page_records  = response_data.get("d", {}).get("results", [])

                next_page_url = response_data.get("d", {}).get("__next")
                if next_page_url:
                    current_url = force_https_url(next_page_url, server_address, service_name)
                else:
                    delta_link  = _extract_delta_link(response_data)
                    current_url = None

                if not page_records:
                    continue

                page_df = _transform_odata_page(
                    records=page_records,
                    mode="initial",
                    time_now=run_timestamp,
                    delta_pointer="initial",
                )

                if src_details.row_filter_config:
                    page_df = _apply_row_filter_to_page(page_df, src_details.row_filter_config)

                if file_record_count and file_record_count > 0:
                    page_buffer.append(page_df)
                    buffered_rows += len(page_df)
                else:
                    if writer is None:
                        single_file_path = os.path.join(
                            staging_dir, generate_filename(file_name_base, 1)
                        )
                        writer = pq.ParquetWriter(
                            single_file_path,
                            arrow_schema,
                            compression=compression_type,
                        )
                    writer.write_table(_to_arrow_table(page_df, arrow_schema))
                    total_rows += len(page_df)

                while file_record_count and file_record_count > 0 and buffered_rows >= file_record_count:
                    combined_df   = pd.concat(page_buffer, ignore_index=True)
                    chunk_df      = combined_df.iloc[:file_record_count]
                    remaining_df  = combined_df.iloc[file_record_count:].reset_index(drop=True)
                    page_buffer   = [remaining_df] if not remaining_df.empty else []
                    buffered_rows = len(remaining_df)

                    rows_written, local_path = _flush_buffer_to_local(
                        buffer_df=chunk_df,
                        staging_dir=staging_dir,
                        file_name_base=file_name_base,
                        file_num=file_number,
                        compression_type=compression_type,
                        arrow_schema=arrow_schema,
                    )
                    staged_files.append((os.path.basename(local_path), local_path))
                    total_rows += rows_written
                    logger.info(
                        "[load_initial] Uploaded chunk #%d (%d rows) — total: %d",
                        file_number, rows_written, total_rows,
                    )
                    logger.info(
                        "[load_initial] Uploaded chunk #%d (%d rows)  -  total: %d",
                        file_number, rows_written, total_rows,
                    )
                    file_number += 1

        if writer is not None:
            writer.close()
            writer = None
            staged_files.append((os.path.basename(single_file_path), single_file_path))

        if page_buffer:
            remainder_df = pd.concat(page_buffer, ignore_index=True)
            if not remainder_df.empty:
                rows_written, local_path = _flush_buffer_to_local(
                    buffer_df=remainder_df,
                    staging_dir=staging_dir,
                    file_name_base=file_name_base,
                    file_num=file_number,
                    compression_type=compression_type,
                    arrow_schema=arrow_schema,
                )
                staged_files.append((os.path.basename(local_path), local_path))
                total_rows += rows_written
                logger.info(
                    "[load_initial] Uploaded final chunk #%d (%d rows) — grand total: %d",
                    file_number, rows_written, total_rows,
                )
                logger.info(
                    "[load_initial] Uploaded final chunk #%d (%d rows)  -  grand total: %d",
                    file_number, rows_written, total_rows,
                )

        if not delta_only:
            logger.info(
                "[load_initial] Source fetch complete; replacing ADLS output_dir='%s' staged_files=%d",
                initial_output_dir, len(staged_files),
            )
            delete_adls_directory_contents(initial_output_dir, adls_config)
            remote_mutated = True
            write_adls(
                initial_output_dir,
                "sap.partfile.metadata",
                metadata_bytes,
                adls_config=adls_config,
            )
            total_files = len(staged_files)
            for idx, (file_name, local_path) in enumerate(staged_files, start=1):
                file_rows = pq.ParquetFile(local_path).metadata.num_rows
                with open(local_path, "rb") as stream:
                    write_adls(
                        initial_output_dir,
                        file_name,
                        stream.read(),
                        adls_config=adls_config,
                    )
                os.remove(local_path)
                _report_chunk_progress(
                    connection_id, migration_session_id, job_name, entity_name, file_rows
                )
                logger.info(
                    "[load_initial] Uploaded file %d/%d (%d rows)",
                    idx, total_files, file_rows,
                )
            try:
                staging_dir.rmdir()
                staging_dir.parent.rmdir()
            except OSError:
                pass
            write_adls(initial_output_dir, "_SUCCESS", None, adls_config=adls_config)

            # Resolve the next token only after the initial upload succeeds.
            if src_details.delta_name:
                resolved_delta_link = _fetch_delta_link_via_entity_set(
                    server_address=server_address,
                    service_name=service_name,
                    delta_name=src_details.delta_name,
                    username=username,
                    password=password,
                    filter_expr=src_details.filter_expr,
                    source_config_or_data=source_config,
                )
                if resolved_delta_link:
                    delta_link = extract_token_from_delta_link(resolved_delta_link)

        logger.info(
            "[load_initial] Completed  -  entity='%s' pages=%d total_rows=%d",
            entity_name, page_number, total_rows,
        )
        # Return the raw token, matching the proxy loader's migration state.
        if delta_link:
            delta_link = extract_token_from_delta_link(delta_link)
        return True, (0 if delta_only else total_rows), delta_link

    except Exception as exc:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        if remote_mutated:
            try:
                delete_adls_directory_contents(initial_output_dir, adls_config)
            except Exception as cleanup_exc:
                logger.warning("[load_initial] Failed to roll back ADLS output: %s", cleanup_exc)
        for _, local_path in staged_files:
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except OSError:
                pass
        try:
            staging_dir.rmdir()
            staging_dir.parent.rmdir()
        except OSError:
            pass
        logger.exception(
            "[load_initial] Fatal error for entity '%s' after %d pages / %d rows: %s",
            entity_name, page_number, total_rows, exc,
        )
        raise
    finally:
        logger.info(
            "[load_initial] Removed local staging directory='%s' entity='%s'",
            str(staging_dir), entity_name,
        )


# ---------------------------------------------------------------------------
# load_delta
# ---------------------------------------------------------------------------

@log_execution_time
def load_delta(
    delta_token_url: str,
    src_details: SAPSourceDetails,
    username: str,
    password: str,
    adls_config: Dict[str, Any],
    server_address: str = "",
    partition_data_by_date: bool = False,
    filter_expr: Optional[str] = None,
    connection_id: Optional[int] = None,
    migration_session_id: Optional[Any] = None,
    job_name: Optional[str] = None,
    source_config: Optional[Any] = None,
) -> Tuple[bool, int, Optional[str]]:
    """Fetch changed records from SAP ODQ using the stored delta link.

    Follows the delta-link chain until all changed pages are consumed. Extracts
    the next delta link from the last page to persist for the following run.

    Output path:
        partition_data_by_date=False  -> <folder>/delta/
        partition_data_by_date=True   -> <folder>/delta/YYYY-MM-DD/

    Returns:
        (success, total_changed_rows, next_delta_link_url)
    """
    service_name      = src_details.name
    entity_name       = src_details.entity_name
    folder_name       = src_details.folder_name
    compression_type  = src_details.compress_type if src_details.compress else None
    file_record_count = src_details.file_rec_count

    encoded_filter = None
    if (
        delta_token_url.startswith("http://")
        or delta_token_url.startswith("https://")
        or "/" in delta_token_url
        or "?" in delta_token_url
    ):
        delta_start_url = force_https_url(delta_token_url)
    else:
        raw_token = delta_token_url.strip("'\"")
        # Use the same configured filter as the initial load. The argument is
        # only a fallback for older callers that do not populate src_details.
        effective_filter = src_details.filter_expr or filter_expr
        query_parts = []
        if effective_filter:
            encoded_filter = quote(effective_filter, safe="'()")
            query_parts.append(f"$filter={encoded_filter}")
        query_parts.append(f"!deltatoken='{raw_token}'")
        query_prefix = "?" if effective_filter else "?&"
        delta_start_url = (
            f"https://{server_address}/sap/opu/odata/sap/{service_name}"
            f"/{entity_name}{query_prefix}{'&'.join(query_parts)}"
        )
        delta_start_url = force_https_url(delta_start_url)

    logger.info(
        "[load_delta] Constructed URL — entity='%s' raw_filter=%r encoded_filter=%r url='%s'",
        entity_name,
        effective_filter if not delta_token_url.startswith(("http://", "https://")) else filter_expr or src_details.filter_expr,
        encoded_filter,
        delta_start_url,
    )
    delta_filter_for_log = src_details.filter_expr or filter_expr
    logger.info(
        "[load_delta] Filter comparison — initial_filter=%r delta_filter=%r same=%s",
        src_details.filter_expr,
        delta_filter_for_log,
        src_details.filter_expr == delta_filter_for_log,
    )
    logger.info("[load_delta] Starting delta pull — entity='%s'", entity_name)

    page_size = src_details.pagination_count or 50000
    odata_headers = {
        "Accept": "application/json",
        "Prefer": f"odata.track-changes,odata.maxpagesize={page_size}",
    }

    run_timestamp = src_details.timestamp or datetime.now(timezone.utc)
    today_str     = run_timestamp.strftime("%Y-%m-%d")
    delta_pointer = extract_token_from_delta_link(delta_token_url)

    delta_output_dir = (
        os.path.join(folder_name, "delta", today_str)
        if partition_data_by_date
        else os.path.join(folder_name, "delta")
    )

    # Match proxy behavior: the token-specific local directory is retained
    # until each successfully uploaded file has been removed.
    staging_dir = DELTA_DIR / folder_name / str(delta_pointer)
    os.makedirs(staging_dir, exist_ok=True)
    staged_files: list[Tuple[str, str]] = []
    metadata_bytes: Optional[bytes] = None
    logger.info(
        "[load_delta] Created local staging directory='%s' entity='%s'",
        str(staging_dir), entity_name,
    )

    # The proxy uses files left in the token directory as the deletion
    # manifest.  Delete only those files, not the complete delta directory.
    try:
        for existing_name in os.listdir(staging_dir):
            existing_path = staging_dir / existing_name
            if existing_path.is_file():
                logger.info(
                    "[load_delta] Deleting existing ADLS file='%s/%s'",
                    delta_output_dir, existing_name,
                )
                delete_adls_file(delta_output_dir, existing_name, adls_config)
                existing_path.unlink()
    except Exception:
        logger.exception("[load_delta] Failed to remove existing delta files")
        raise

    next_delta_link   = None
    source_fetch_complete = False
    page_number       = 0
    total_rows        = 0
    file_number       = 1
    file_name_base    = generate_filename_base(run_timestamp)
    page_buffer: list = []
    buffered_rows     = 0
    writer = None
    single_file_path = None

    try:
        metadata_bytes, arrow_schema = _fetch_metadata_and_arrow_schema(
            server_address,
            service_name,
            username,
            password,
            source_config,
        )

        with _build_sap_session(server_address, source_config) as session:
            session.auth = (username, password)
            session.headers.update(odata_headers)
            current_url = delta_start_url
            while current_url:
                page_number += 1
                if page_number > SAP_MAX_PAGES:
                    logger.error(
                        "[load_delta] SAP_MAX_PAGES limit (%d) hit for entity '%s'. Aborting.",
                        SAP_MAX_PAGES, entity_name,
                    )
                    return False, total_rows, None

                logger.debug("[load_delta] Fetching page %d from '%s'", page_number, current_url)
                response = session.get(current_url, timeout=SAP_REQUEST_TIMEOUT)

                if response.status_code != 200:
                    error_message = _parse_sap_error(response.status_code, response.text)
                    logger.error("[load_delta] %s", error_message)
                    raise ValueError(error_message)

                response_data = response.json()
                d_block       = response_data.get("d", {})

                page_records  = d_block.get("results", []) if isinstance(d_block, dict) else []
                next_page_url = d_block.get("__next") if isinstance(d_block, dict) else None
                if not next_page_url and not next_delta_link:
                    next_delta_link = _extract_delta_link(response_data)

                if page_records:
                    page_df = _transform_odata_page(
                        records=page_records,
                        mode="delta",
                        time_now=run_timestamp,
                        delta_pointer=delta_pointer,
                    )

                    if file_record_count and file_record_count > 0:
                        page_buffer.append(page_df)
                        buffered_rows += len(page_df)
                    else:
                        if writer is None:
                            single_file_path = os.path.join(
                                staging_dir, generate_filename(file_name_base, 1)
                            )
                            writer = pq.ParquetWriter(
                                single_file_path,
                                arrow_schema,
                                compression=compression_type,
                            )
                        writer.write_table(_to_arrow_table(page_df, arrow_schema))
                        total_rows += len(page_df)

                    while file_record_count and file_record_count > 0 and buffered_rows >= file_record_count:
                        combined_df   = pd.concat(page_buffer, ignore_index=True)
                        chunk_df      = combined_df.iloc[:file_record_count]
                        remaining_df  = combined_df.iloc[file_record_count:].reset_index(drop=True)
                        page_buffer   = [remaining_df] if not remaining_df.empty else []
                        buffered_rows = len(remaining_df)

                        rows_written, local_path = _flush_buffer_to_local(
                            buffer_df=chunk_df,
                            staging_dir=staging_dir,
                            file_name_base=file_name_base,
                            file_num=file_number,
                            compression_type=compression_type,
                            arrow_schema=arrow_schema,
                        )
                        staged_files.append((os.path.basename(local_path), local_path))
                        total_rows += rows_written
                        logger.info(
                            "[load_delta] Uploaded chunk #%d (%d rows) — running total: %d",
                            file_number, rows_written, total_rows,
                        )
                        logger.info(
                            "[load_delta] Uploaded chunk #%d (%d rows)  -  running total: %d",
                            file_number, rows_written, total_rows,
                        )
                        file_number += 1

                current_url = (
                    force_https_url(next_page_url, server_address, service_name)
                    if next_page_url else None
                )

        if writer is not None:
            writer.close()
            writer = None
            staged_files.append((os.path.basename(single_file_path), single_file_path))

        if page_buffer:
            remainder_df = pd.concat(page_buffer, ignore_index=True)
            if not remainder_df.empty:
                rows_written, local_path = _flush_buffer_to_local(
                    buffer_df=remainder_df,
                    staging_dir=staging_dir,
                    file_name_base=file_name_base,
                    file_num=file_number,
                    compression_type=compression_type,
                    arrow_schema=arrow_schema,
                )
                staged_files.append((os.path.basename(local_path), local_path))
                total_rows += rows_written
                logger.info(
                    "[load_delta] Uploaded final chunk #%d (%d rows) — grand total: %d",
                    file_number, rows_written, total_rows,
                )
                logger.info(
                    "[load_delta] Uploaded final chunk #%d (%d rows)  -  grand total: %d",
                    file_number, rows_written, total_rows,
                )

        source_fetch_complete = True

        logger.info(
            "[load_delta] Source fetch complete; replacing ADLS output_dir='%s' staged_files=%d",
            delta_output_dir, len(staged_files),
        )
        if metadata_bytes:
            write_adls(
                delta_output_dir,
                "sap.partfile.metadata",
                metadata_bytes,
                adls_config=adls_config,
            )

        # Upload one file at a time and remove its local manifest entry only
        # after the ADLS write succeeds, as in the proxy implementation.
        total_files = len(staged_files)
        for idx, (file_name, local_path) in enumerate(staged_files, start=1):
            file_rows = pq.ParquetFile(local_path).metadata.num_rows
            with open(local_path, "rb") as stream:
                write_adls(
                    delta_output_dir,
                    file_name,
                    stream.read(),
                    adls_config=adls_config,
                )
            os.remove(local_path)
            _report_chunk_progress(
                connection_id, migration_session_id, job_name, entity_name, file_rows
            )
            logger.info(
                "[load_delta] Uploaded file %d/%d (%d rows)",
                idx, total_files, file_rows,
            )

        # The proxy loader does not create a delta _SUCCESS marker.
        try:
            staging_dir.rmdir()
            staging_dir.parent.rmdir()
        except OSError:
            pass

        # Resolve the next token only after every delta file uploads.
        if src_details.delta_name:
            resolved_delta_link = _fetch_delta_link_via_entity_set(
                server_address=server_address,
                service_name=service_name,
                delta_name=src_details.delta_name,
                username=username,
                password=password,
                filter_expr=src_details.filter_expr,
                source_config_or_data=source_config,
            )
            if resolved_delta_link:
                next_delta_link = extract_token_from_delta_link(resolved_delta_link)

        logger.info(
            "[load_delta] Completed  -  entity='%s' pages=%d total_rows=%d",
            entity_name, page_number, total_rows,
        )
        if next_delta_link:
            next_delta_link = extract_token_from_delta_link(next_delta_link)
        return True, total_rows, next_delta_link

    except Exception as exc:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        # The proxy fetch helper removes the token's local parent directory
        # when SAP fetching fails, including partial parquet files.
        if not source_fetch_complete:
            shutil.rmtree(DELTA_DIR / folder_name, ignore_errors=True)
        logger.exception(
            "[load_delta] Fatal error for entity '%s' after %d pages / %d rows: %s",
            entity_name, page_number, total_rows, exc,
        )
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
        logger.info(
            "[load_delta] Removed local staging directory='%s' entity='%s'",
            staging_dir, entity_name,
        )
