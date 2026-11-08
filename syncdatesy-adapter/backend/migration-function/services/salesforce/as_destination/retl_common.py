"""Shared Reverse ETL helpers for warehouse → Salesforce.

Source-specific extract (temp table + chunk fetch) lives behind
``SourceExtractAdapter``. Salesforce Bulk upsert, field matching, and the
per-table process loop are reused by Snowflake and Databricks RETL.
"""

from __future__ import annotations

import json
import re
import time
import traceback as tb_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

import pandas as pd
from fastapi import HTTPException

from services.salesforce.client import (
    prepare_salesforce_credentials,
    create_log_paths,
    postgres_connection,
    salesforce_connection,
    update_table_status,
)
from utils.database_utils import data_transfer_logs
from utils.snowflake_utils import normalize_name

# Omit from Bulk API payload — read-only system fields and fields that should not
# be overwritten on update (Id is kept separately as the upsert external-id key).
SALESFORCE_RETL_OMIT_ON_WRITE = frozenset(
    {
        "Name",
        "CreatedDate",
        "LastModifiedDate",
        "CreatedById",
        "LastModifiedById",
        "SystemModstamp",
        "IsDeleted",
    }
)


class SourceExtractAdapter(Protocol):
    def create_temp_index_table(
        self, source_config, source_table, temp_table, fields
    ) -> None: ...

    def fetch_chunks(
        self, source_config, temp_table, fields, chunk_size
    ) -> Iterable[list]: ...

    def drop_temp_table(self, source_config, temp_table) -> None: ...

    def update_staging_count(
        self,
        source_config,
        connection_id,
        migration_session_id,
        job_name,
        table_name,
        temp_table,
    ) -> None: ...


class SharedListFailureTracker:
    """Wrap a multiprocessing shared list with MigrationFailureTracker.add_failure."""

    def __init__(self, shared_list):
        self.shared_list = shared_list
        self.failed_tables = []

    def add_failure(self, table_name, error_message, service_name):
        if self.shared_list is not None and hasattr(self.shared_list, "append"):
            self.shared_list.append((table_name, error_message, service_name))
        self.failed_tables.append(table_name)


def requested_source_tables_from_mapping(mapping_list) -> list[str]:
    requested_source_tables = []
    for mapping in mapping_list or []:
        source_table_name = None
        if isinstance(mapping, (list, tuple)) and mapping:
            source_table_name = mapping[0]
        elif isinstance(mapping, dict):
            source_table_name = (
                mapping.get("source_table")
                or mapping.get("table_name")
                or mapping.get("table")
            )
        else:
            source_table_name = mapping

        if isinstance(source_table_name, (list, tuple)) and source_table_name:
            source_table_name = source_table_name[0]

        if source_table_name is not None:
            requested_source_tables.append(str(source_table_name))
    return requested_source_tables


def mark_unmapped_source_tables(
    *,
    mapping_list,
    matching_fields_list,
    failure_tracker,
    service_name: str,
    connection_id,
    job_name,
    migration_session_id,
    cmp_id,
    user_id,
    session,
    is_final_attempt: bool = True,
) -> list[str]:
    requested_source_tables = requested_source_tables_from_mapping(mapping_list)
    matched_source_tables = {
        str(item.get("source_table"))
        for item in (matching_fields_list or [])
        if isinstance(item, dict) and item.get("source_table")
    }
    unmatched_source_tables = []
    seen_unmatched = set()
    for table_name in requested_source_tables:
        if table_name not in matched_source_tables and table_name not in seen_unmatched:
            seen_unmatched.add(table_name)
            unmatched_source_tables.append(table_name)

    for source_table_name in unmatched_source_tables:
        no_mapping_message = (
            "No valid field mapping found between source and destination tables."
        )
        print(f"⚠️ {no_mapping_message} Marking table as failed: {source_table_name}")
        if failure_tracker:
            failure_tracker.add_failure(
                source_table_name, no_mapping_message, service_name
            )
        try:
            from utils.error_utils import handle_service_error

            handle_service_error(
                error=ValueError(no_mapping_message),
                context={
                    "table_name": source_table_name,
                    "service_name": service_name,
                    "error_type": "TABLE_MAPPING_NOT_FOUND",
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "job_name": job_name,
                    "migration_session_id": migration_session_id,
                    "session": session,
                    "source_table": source_table_name,
                },
                log_files=None,
                send_email=False,
                is_final_attempt=is_final_attempt,
            )
        except Exception as log_error:
            print(
                f"⚠️ Failed to write unmapped-table error log for '{source_table_name}': {log_error}"
            )
        try:
            update_table_status(
                connection_id, source_table_name, "failed", job_name, migration_session_id
            )
        except Exception as status_error:
            print(
                f"⚠️ Failed to set status 'failed' for unmapped table '{source_table_name}': {status_error}"
            )
    return unmatched_source_tables


def match_source_destination_fields(
    source_fields: dict,
    destination_fields: dict,
) -> tuple[list[str], dict[str, str]]:
    """Match warehouse columns to Salesforce API names.

    Forward ETL stores Salesforce fields in the warehouse using ``normalize_name``
    (e.g. ``CreatedDate`` → ``CREATED_DATE``). Reverse ETL must map back using the
    same normalization so names align across both sides.
    """
    destination_norm_lookup: dict[str, str] = {}
    destination_exact_lookup: dict[str, str] = {}
    for field_name in destination_fields.keys():
        field_str = str(field_name)
        destination_exact_lookup[field_str] = field_str
        destination_exact_lookup[field_str.lower()] = field_str
        destination_norm_lookup[normalize_name(field_str)] = field_str

    matching_fields: list[str] = []
    field_map: dict[str, str] = {}

    for source_field in source_fields.keys():
        source_str = str(source_field)
        destination_field = (
            destination_exact_lookup.get(source_str)
            or destination_exact_lookup.get(source_str.lower())
            or destination_norm_lookup.get(source_str.upper())
            or destination_norm_lookup.get(normalize_name(source_str))
        )
        if destination_field is not None:
            matching_fields.append(source_field)
            field_map[source_str] = destination_field

    return matching_fields, field_map


def get_matching_fields(connection_id, mapping_list):
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        matching_fields_list = []

        for mapping in mapping_list or []:
            source_table = None
            destination_table = None
            if isinstance(mapping, dict):
                source_table = (
                    mapping.get("source_table")
                    or mapping.get("table_name")
                    or mapping.get("table")
                )
                destination_table = (
                    mapping.get("destination_table") or mapping.get("destination")
                )
            elif isinstance(mapping, (list, tuple)) and len(mapping) >= 2:
                source_table, destination_table = mapping[0], mapping[1]
            else:
                continue

            if isinstance(source_table, list):
                source_table = source_table[0] if source_table else None
            if isinstance(destination_table, list):
                destination_table = destination_table[0] if destination_table else None

            source_table = (
                str(source_table) if not isinstance(source_table, str) else source_table
            )
            destination_table = (
                str(destination_table)
                if not isinstance(destination_table, str)
                else destination_table
            )
            if not source_table or not destination_table:
                continue
            cursor.execute(
                """
                SELECT table_fields FROM source_table_selection
                WHERE connection_id = %s AND "table" = %s
                """,
                (connection_id, source_table),
            )
            source_fields = cursor.fetchone()

            if source_fields and source_fields[0]:
                source_fields = (
                    source_fields[0]
                    if isinstance(source_fields[0], dict)
                    else json.loads(source_fields[0])
                )
            else:
                source_fields = {}

            cursor.execute(
                """
                SELECT table_fields FROM destination_table_selection
                WHERE connection_id = %s AND "table" = %s
                """,
                (connection_id, destination_table),
            )
            destination_fields = cursor.fetchone()

            if destination_fields and destination_fields[0]:
                destination_fields = (
                    destination_fields[0]
                    if isinstance(destination_fields[0], dict)
                    else json.loads(destination_fields[0])
                )
            else:
                destination_fields = {}

            matching_fields, field_map = match_source_destination_fields(
                source_fields, destination_fields
            )

            if matching_fields:
                matching_fields_list.append(
                    {
                        "source_table": source_table,
                        "destination_table": destination_table,
                        "matching_fields": matching_fields,
                        "field_map": field_map,
                    }
                )

        cursor.close()
        conn.close()
        return matching_fields_list

    except Exception as e:
        print(f"ERROR: Failed to fetch matching fields: {str(e)}")
        raise


def bulk_upsert_to_salesforce(
    data: list,
    salesforce_object: str,
    source_table: str,
    sf,
    connection_id: int,
    connection_name: str,
    logs_creation: bool,
    chunk_index: int,
    external_id_field: str = "Id",
    csv_file=None,
    response_file=None,
    migration_type_label: str = "Snowflake ➜ Salesforce",
    field_map: dict[str, str] | None = None,
) -> dict:
    try:

        def convert_datetime_to_string(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {
                    key: convert_datetime_to_string(value) for key, value in obj.items()
                }
            elif isinstance(obj, list):
                return [convert_datetime_to_string(item) for item in obj]
            return obj

        def convert_date_string_to_iso(date_str):
            try:
                for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        date_obj = datetime.strptime(date_str, fmt)
                        return date_obj.date().isoformat()
                    except ValueError:
                        continue
                return date_str
            except Exception as e:
                print(f"Error converting date: {e}")
                return date_str

        processed_data = []
        insert_records = []
        upsert_records = []
        operation_types = []

        for record in data:
            converted_record = convert_datetime_to_string(record)
            for key, value in converted_record.items():
                if isinstance(value, str) and re.match(r"\d{1,2}/\d{1,2}/\d{4}", value):
                    converted_record[key] = convert_date_string_to_iso(value)
            upsert_id = None
            renamed_record = {}
            for key, value in converted_record.items():
                if key == "__datasyncher_row":
                    continue
                dest_key = field_map.get(str(key), key) if field_map else key
                if dest_key == "Id":
                    if value:
                        upsert_id = value
                    continue
                if dest_key in SALESFORCE_RETL_OMIT_ON_WRITE:
                    continue
                renamed_record[dest_key] = value
            processed_data.append(renamed_record)

            if upsert_id:
                upsert_records.append({"Id": upsert_id, **renamed_record})
                operation_types.append("upsert")
            else:
                insert_records.append(renamed_record)
                operation_types.append("insert")

        if logs_creation and csv_file is not None:
            csv_data = []
            timestamp = datetime.now()
            for idx, record in enumerate(data):
                csv_record = {
                    "batch_no": chunk_index,
                    "batch_index": idx,
                    "connection_id": connection_id,
                    "timestamp": timestamp.isoformat(),
                    "operation": operation_types[idx],
                    **record,
                }
                csv_data.append(csv_record)
            df = pd.DataFrame(csv_data)
            file_exists = Path(csv_file).is_file()
            with open(csv_file, "a", encoding="utf-8") as f:
                if not file_exists:
                    f.write(f"# Connection Name: {connection_name}\n")
                    f.write(f"# Migration Type: {migration_type_label}\n")
                    f.write(
                        f"# Migration Tables: {source_table} ➜ {salesforce_object}\n"
                    )
                    f.write(f"# Timestamp: {timestamp.isoformat()}\n\n")
                    df.to_csv(f, index=False)
                else:
                    df.to_csv(f, index=False, header=False)

        try:
            responses = {}
            bulk_job_ids = {}

            if insert_records:
                print(
                    f"📤 Starting Salesforce INSERT operation for {len(insert_records)} records..."
                )
                insert_response = sf.bulk.__getattr__(salesforce_object).insert(
                    insert_records
                )
                responses["insert"] = insert_response

                if hasattr(insert_response, "job_id"):
                    bulk_job_ids["insert_job_id"] = insert_response.job_id
                    print(f"🆔 INSERT Job ID: {insert_response.job_id}")
                    try:
                        print(
                            f"📝 Registering INSERT job {insert_response.job_id} for monitoring"
                        )
                    except Exception as e:
                        print(f"⚠️ Could not register INSERT job for monitoring: {e}")

            if upsert_records:
                print(
                    f"🔄 Starting Salesforce UPSERT operation for {len(upsert_records)} records..."
                )
                upsert_response = sf.bulk.__getattr__(salesforce_object).upsert(
                    upsert_records, external_id_field=external_id_field
                )
                responses["upsert"] = upsert_response

                if hasattr(upsert_response, "job_id"):
                    bulk_job_ids["upsert_job_id"] = upsert_response.job_id
                    print(f"🆔 UPSERT Job ID: {upsert_response.job_id}")
                    try:
                        print(
                            f"📝 Registering UPSERT job {upsert_response.job_id} for monitoring"
                        )
                    except Exception as e:
                        print(f"⚠️ Could not register UPSERT job for monitoring: {e}")

            insert_success_count = sum(
                1 for record in responses.get("insert", []) if record.get("success")
            )
            insert_failure_count = sum(
                1 for record in responses.get("insert", []) if not record.get("success")
            )
            upsert_success_count = sum(
                1 for record in responses.get("upsert", []) if record.get("success")
            )
            upsert_failure_count = sum(
                1 for record in responses.get("upsert", []) if not record.get("success")
            )

            summary_data = {
                "Total Records": len(data),
                "Insert Attempts": len(insert_records),
                "Insert Success": insert_success_count,
                "Insert Failures": insert_failure_count,
                "Upsert Attempts": len(upsert_records),
                "Upsert Success": upsert_success_count,
                "Upsert Failures": upsert_failure_count,
                "Bulk Job IDs": bulk_job_ids,
            }

            if response_file is not None:
                log_record = {
                    "connection_id": connection_id,
                    "timestamp": datetime.now().isoformat(),
                    "salesforce_object": salesforce_object,
                    "insert_responses": responses.get("insert", []),
                    "upsert_responses": responses.get("upsert", []),
                    "summary": summary_data,
                }
                with open(response_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_record) + "\n")

            return {
                "success": True,
                "summary": summary_data,
                "log_file": str(csv_file) if csv_file else None,
                "response_file": str(response_file) if response_file else None,
                "bulk_job_ids": bulk_job_ids,
            }

        except Exception as e:
            print(f"Error performing bulk upsert: {str(e)}")
            if response_file is not None:
                error_record = {
                    "connection_id": connection_id,
                    "timestamp": datetime.now().isoformat(),
                    "salesforce_object": salesforce_object,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "data_sample": data[:5] if data else [],
                }
                with open(response_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(error_record) + "\n")
            return {
                "success": False,
                "error": str(e),
                "log_file": str(csv_file) if csv_file else None,
                "response_file": str(response_file) if response_file else None,
            }

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise e


def process_mapping_core(
    matching_fields,
    source_config,
    salesforce_details,
    connection_id,
    session,
    job_name,
    migration_session_id,
    result_queue,
    cmp_id,
    user_id,
    environment,
    chunk_count,
    logs_creation,
    connection_name,
    company_name,
    extract_adapter: SourceExtractAdapter,
    failure_tracker=None,
    is_final_attempt=True,
    service_name: str = "Snowflake to Salesforce",
    migration_type_label: str = "Snowflake ➜ Salesforce",
    update_staging: bool = True,
) -> Optional[int]:
    """Per-table RETL: extract chunks from warehouse → Salesforce Bulk upsert."""
    source_table = None
    try:
        print(f"DEBUG: Processing mapping: {matching_fields}")

        if not isinstance(matching_fields, dict):
            raise ValueError(
                f"ERROR: Expected matching_fields to be dict but got {type(matching_fields)}"
            )

        source_table = matching_fields.get("source_table")
        salesforce_object = matching_fields.get("destination_table")
        fields_to_fetch = matching_fields.get("matching_fields")
        field_map = matching_fields.get("field_map") or {}
        temp_table = f"TMP_{source_table}_extract"

        if not isinstance(source_table, str) or not isinstance(salesforce_object, str):
            raise ValueError(
                "ERROR: Source and destination table names must be strings."
            )
        if not isinstance(fields_to_fetch, list):
            raise ValueError("ERROR: Fields to fetch must be a list.")

        base_log_dir = "/home/readvinode/salesforce_upsert_logs"
        log_dir = create_log_paths(base_log_dir, company_name)
        log_dir = Path(log_dir)
        safe_connection_name = re.sub(r"\W+", "_", connection_name)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_file = log_dir / (
            f"{safe_connection_name}_{connection_id}_{salesforce_object}_sf_{timestamp_str}.csv"
        )
        response_file = log_dir / (
            f"sf_response {safe_connection_name}_{connection_id}_{salesforce_object}_{timestamp_str}.jsonl"
        )

        try:
            update_table_status(
                connection_id, source_table, "in_progress", job_name, migration_session_id
            )
        except Exception as e:
            print(f"Warning: Failed to update initial status: {e}")

        sf_creds, sf_oauth_context = prepare_salesforce_credentials(
            connection_id, "destination", salesforce_details
        )
        sf = salesforce_connection(
            sf_creds.get("state"),
            sf_creds.get("access_token"),
            sf_creds.get("instance_url"),
            sf_creds.get("refresh_token"),
            sf_creds.get("CLIENT_ID"),
            sf_creds.get("CLIENT_SECRET"),
            environment,
            sf_creds.get("CUSTOM_DOMAIN"),
            oauth_context=sf_oauth_context,
        )

        total_rows_transmitted = 0
        insert_success = insert_failure = upsert_success = upsert_failure = 0
        had_chunk_errors = False
        start_time = datetime.now(timezone.utc)
        last_status_update = start_time
        chunk_index = 0

        extract_adapter.create_temp_index_table(
            source_config, source_table, temp_table, fields_to_fetch
        )

        if update_staging:
            try:
                extract_adapter.update_staging_count(
                    source_config,
                    connection_id,
                    migration_session_id,
                    job_name,
                    source_table,
                    temp_table,
                )
            except Exception as e:
                print(f"Warning: Failed to update staging count: {e}")

        for chunk in extract_adapter.fetch_chunks(
            source_config, temp_table, fields_to_fetch, chunk_size=chunk_count
        ):
            chunk_index += 1
            print(
                f"DEBUG: Processing chunk {chunk_index}, {len(chunk)} rows from {source_table}"
            )

            if not isinstance(chunk, list):
                print(f"ERROR: Expected chunk to be list but got {type(chunk)}")
                continue

            try:
                result = bulk_upsert_to_salesforce(
                    chunk,
                    salesforce_object,
                    source_table,
                    sf,
                    connection_id,
                    connection_name,
                    logs_creation,
                    chunk_index,
                    csv_file=csv_file,
                    response_file=response_file,
                    migration_type_label=migration_type_label,
                    field_map=field_map,
                )
                if result["success"]:
                    insert_success += result["summary"].get("Insert Success", 0)
                    insert_failure += result["summary"].get("Insert Failures", 0)
                    upsert_success += result["summary"].get("Upsert Success", 0)
                    upsert_failure += result["summary"].get("Upsert Failures", 0)
                    if (
                        result["summary"].get("Insert Failures", 0) > 0
                        or result["summary"].get("Upsert Failures", 0) > 0
                    ):
                        had_chunk_errors = True
                    print(f"DEBUG: Upsert result: {result}")
                else:
                    had_chunk_errors = True
                    print(
                        f"ERROR: Bulk upsert failed for {salesforce_object} - {result['error']}"
                    )

            except Exception as upsert_error:
                had_chunk_errors = True
                print(
                    f"ERROR: Bulk upsert exception for {salesforce_object} - {upsert_error}"
                )
                tb_module.print_exc()

                from utils.error_utils import log_error_to_tool_log

                chunk_error_message = (
                    f"Chunk {chunk_index} processing failed: {str(upsert_error)}"
                )
                if log_error_to_tool_log(
                    cmp_id,
                    connection_id,
                    user_id,
                    source_table,
                    chunk_error_message,
                    session,
                    job_name=job_name,
                ):
                    print(
                        f"✅ Chunk error logged to log for table: {source_table}, chunk: {chunk_index}"
                    )
                else:
                    print("⚠️ Failed to log chunk error to log")

                if is_final_attempt:
                    try:
                        from utils.email_utils import send_error_notification

                        send_error_notification(
                            table_name=f"{source_table} (Chunk {chunk_index})",
                            error_message=(
                                f"Chunk processing error: {str(upsert_error)}\n\n"
                                f"Traceback:\n{tb_module.format_exc()}"
                            ),
                            connection_name=connection_name,
                            service_name=service_name,
                            user_id=user_id,
                            company_id=cmp_id,
                            additional_context={
                                "job_name": job_name,
                                "user_id": user_id,
                                "company_id": cmp_id,
                                "company_name": company_name,
                                "migration_session_id": migration_session_id,
                                "connection_id": connection_id,
                                "session": session,
                                "chunk_index": chunk_index,
                                "chunk_size": len(chunk),
                                "source_table": source_table,
                                "destination_table": salesforce_object,
                            },
                        )
                        print("📧 Chunk failure email sent (final attempt)")
                    except Exception as email_error:
                        print(
                            f"⚠️ Failed to send chunk error notification email: {email_error}"
                        )
                else:
                    print("⏭️ SKIPPING chunk failure email (not final attempt)")

            total_rows_transmitted += len(chunk)

            current_time = datetime.now(timezone.utc)
            if (current_time - last_status_update).total_seconds() > 300:
                print(
                    f"📊 Progress update: {total_rows_transmitted} rows processed for {source_table}"
                )
                try:
                    update_table_status(
                        connection_id,
                        source_table,
                        "in_progress",
                        job_name,
                        migration_session_id,
                    )
                except Exception as e:
                    print(f"Warning: Failed to update progress status: {e}")
                last_status_update = current_time

        end_time = datetime.now(timezone.utc)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                data_transfer_logs(
                    cmp_id,
                    connection_id,
                    user_id,
                    source_table,
                    start_time,
                    end_time,
                    len(fields_to_fetch),
                    session,
                    insert_success,
                    insert_failure,
                    upsert_success,
                    upsert_failure,
                    job_name=job_name,
                )
                break
            except Exception as e:
                print(f"⚠️ Failed to log data transfer (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    print(f"❌ Failed to log data transfer after {max_retries} attempts")

        table_failed = had_chunk_errors or insert_failure > 0 or upsert_failure > 0
        if table_failed and failure_tracker:
            failure_tracker.add_failure(
                source_table,
                (
                    f"{service_name} upsert completed with failures. "
                    f"Insert Failures: {insert_failure}, Upsert Failures: {upsert_failure}"
                ),
                service_name,
            )

        for attempt in range(max_retries):
            try:
                final_status = "failed" if table_failed else "completed"
                update_table_status(
                    connection_id, source_table, final_status, job_name, migration_session_id
                )
                break
            except Exception as e:
                print(f"⚠️ Failed to update completion status (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    print(f"❌ Failed to update status after {max_retries} attempts")
        extract_adapter.drop_temp_table(source_config, temp_table)
        if table_failed:
            print(
                f"❌ Data sync completed with failures for table: {source_table} "
                f"(Insert Failures={insert_failure}, Upsert Failures={upsert_failure})"
            )
        else:
            print(f"✅ Data sync completed successfully for table: {source_table}")

        if result_queue:
            result_queue.put(total_rows_transmitted)
        else:
            print(f"📊 Rows transmitted for {source_table}: {total_rows_transmitted}")

        return total_rows_transmitted

    except Exception as e:
        print(f"ERROR: Unexpected error in processing mapping: {e}")
        tb_module.print_exc()

        from utils.error_utils import handle_service_error

        table_name = source_table or "Unknown Table"
        error_context = {
            "table_name": table_name,
            "service_name": service_name,
            "error_type": "TABLE_MIGRATION_FAILED",
            "cmp_id": cmp_id,
            "connection_id": connection_id,
            "user_id": user_id,
            "job_name": job_name,
            "migration_session_id": migration_session_id,
            "session": session,
            "source_table": source_table,
            "destination_table": (
                matching_fields.get("destination_table")
                if isinstance(matching_fields, dict)
                else "Unknown"
            ),
        }

        error_result = handle_service_error(
            error=e,
            context=error_context,
            log_files=None,
            send_email=False,
        )

        if error_result.get("tool_log_logged", error_result.get("log_logged", False)):
            print(f"✅ Table error logged to tool_log for table: {table_name}")

        error_details = f"Error: {str(e)}\n\nTraceback:\n{tb_module.format_exc()}"
        if failure_tracker:
            failure_tracker.add_failure(table_name, error_details, service_name)
            print(f"📝 Added table failure to migration tracker: {table_name}")
        else:
            print(f"⚠️ No failure tracker provided - table failure not tracked: {table_name}")

        if source_table:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    update_table_status(
                        connection_id, source_table, "failed", job_name, migration_session_id
                    )
                    break
                except Exception as status_error:
                    print(
                        f"⚠️ Failed to update failure status (attempt {attempt + 1}): {status_error}"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(2)
        if result_queue:
            result_queue.put(0)
        else:
            print(
                f"WARNING: result_queue is None. Skipping failure queue update for {source_table}"
            )
        return 0
