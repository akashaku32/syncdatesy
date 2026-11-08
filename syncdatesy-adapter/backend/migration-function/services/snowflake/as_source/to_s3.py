from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError, ParamValidationError
from fastapi import HTTPException

from models.snowflake_s3_models import SnowflakeS3MigrationRequest
from services.source_file_helpers.file_writing_utils import (
    _decode_csv_char,
    _write_csv,
    _write_excel,
    _write_json,
    _write_parquet,
    build_utc_filename_suffix,
    check_excel_limits_preflight,
    lookup_table_export_settings,
    resolve_relative_target_folder,
)
from services.snowflake.service import SnowflakeService
from services.transform.source_script_transform import (
    TransformingCursor,
    get_active_source_script,
)
from services.snowflake.as_source.to_sftp import (
    MigrationFailureTracker,
    _build_snowflake_select_query,
    _extract_error_message,
    _extract_snowflake_schema,
    _log_stage_error,
    _map_snowflake_export_error,
    _mark_table_failed_with_error,
    _normalize_selected_fields,
    _trigger_ws_all,
)
from utils.database_utils import data_transfer_logs, update_table_status_snowflake
from utils.error_utils import log_migration_completion, log_migration_failure, update_migration_status_log
from utils.s3_utils import s3_client, validate_s3_bucket_name
from utils.simple_progress_tracker import SimpleProgressTracker

logger = logging.getLogger(__name__)


def _normalize_s3_prefix(prefix: Any) -> str:
    cleaned = str(prefix or "").strip().replace("\\", "/")
    return cleaned.strip("/")


def _join_s3_key(*parts: Any) -> str:
    segments = []
    for part in parts:
        value = str(part or "").strip().replace("\\", "/").strip("/")
        if value:
            segments.append(value)
    return "/".join(segments)


def _extract_s3_base_prefix(s3_config: Dict[str, Any]) -> str:
    return _normalize_s3_prefix(
        s3_config.get("base_folder_path")
        or s3_config.get("base_folder")
        or s3_config.get("s3_base_prefix")
    )


def _sanitize_s3_filename(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return "export"
    return name.replace("/", "_").replace("\\", "_")


def _map_s3_client_error(error: ClientError, *, bucket: str, key: Optional[str] = None) -> HTTPException:
    code = str(error.response.get("Error", {}).get("Code") or "")
    message_map = {
        "AccessDenied": "S3 access denied. Check AWS credentials and bucket permissions.",
        "403": "S3 access denied. Check AWS credentials and bucket permissions.",
        "InvalidAccessKeyId": "Invalid AWS access key ID.",
        "SignatureDoesNotMatch": "Invalid AWS secret access key.",
        "InvalidClientTokenId": "Invalid AWS credentials.",
        "NoSuchBucket": f"S3 bucket not found: {bucket}.",
        "NotFound": f"S3 bucket not found or not accessible: {bucket}.",
        "404": f"S3 bucket not found or not accessible: {bucket}.",
        "InvalidFolder": (
            f"S3 base_folder_path not found in bucket: s3://{bucket}/{key}/."
            if key
            else f"S3 base_folder_path not found in bucket: s3://{bucket}."
        ),
    }
    detail = message_map.get(code)
    if detail is None:
        target = f"s3://{bucket}/{key}" if key else f"s3://{bucket}"
        raw_message = error.response.get("Error", {}).get("Message") or str(error)
        detail = f"S3 operation failed for {target}: {code or raw_message}"
    return HTTPException(status_code=422, detail=detail)


def _s3_base_folder_not_found_error(bucket: str, base_prefix: str) -> HTTPException:
    return _map_s3_client_error(
        ClientError(
            {
                "Error": {
                    "Code": "InvalidFolder",
                    "Message": "Base folder does not exist in the bucket.",
                }
            },
            "ListObjectsV2",
        ),
        bucket=bucket,
        key=base_prefix,
    )


def _s3_prefix_exists(s3: Any, *, bucket: str, base_prefix: str) -> bool:
    prefix = _normalize_s3_prefix(base_prefix)
    if not prefix:
        return True

    response = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/", MaxKeys=1)
    return (
        int(response.get("KeyCount") or 0) > 0
        or bool(response.get("Contents"))
        or bool(response.get("CommonPrefixes"))
    )


def _build_s3_client(s3_config: Dict[str, Any]) -> Any:
    bucket = str(s3_config.get("s3_bucket") or "").strip()
    access_key = str(s3_config.get("aws_access_key_id") or "").strip()
    secret_key = str(s3_config.get("aws_secret_access_key") or "").strip()
    region = str(s3_config.get("aws_region") or "").strip() or None

    missing = [
        field_name
        for field_name, value in {
            "s3_bucket": bucket,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }.items()
        if not value
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required S3 field(s): {', '.join(missing)}")

    bucket_validation_error = validate_s3_bucket_name(bucket)
    if bucket_validation_error:
        raise HTTPException(status_code=422, detail=bucket_validation_error)

    try:
        return s3_client(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_region=region,
            s3_bucket=bucket,
            auto_detect_region=True,
        )
    except ParamValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid S3 configuration: {exc}") from exc
    except ClientError as exc:
        raise _map_s3_client_error(exc, bucket=bucket) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to initialize S3 client: {exc}") from exc


def validate_s3_base_folder_exists(
    aws_access_key_id: str,
    aws_secret_access_key: str,
    s3_bucket: str,
    base_prefix: str,
    region: str = "us-east-1",
) -> tuple:
    """Validate S3 bucket access and, when provided, list permission for a prefix."""
    config = {
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region": region,
        "s3_bucket": s3_bucket,
    }
    try:
        client = _build_s3_client(config)
        client.head_bucket(Bucket=s3_bucket)
        prefix = _normalize_s3_prefix(base_prefix)
        if prefix and not _s3_prefix_exists(client, bucket=s3_bucket, base_prefix=prefix):
            return False, _s3_base_folder_not_found_error(s3_bucket, prefix).detail
        return True, None
    except HTTPException as exc:
        return False, str(exc.detail)
    except ClientError as exc:
        return False, _map_s3_client_error(exc, bucket=s3_bucket).detail
    except Exception as exc:
        return False, str(exc)


def _validate_s3_destination_access(s3: Any, *, bucket: str, base_prefix: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        prefix = _normalize_s3_prefix(base_prefix)
        if prefix and not _s3_prefix_exists(s3, bucket=bucket, base_prefix=prefix):
            raise _s3_base_folder_not_found_error(bucket, prefix)
    except ClientError as exc:
        raise _map_s3_client_error(exc, bucket=bucket) from exc


def _upload_file_to_s3(s3: Any, *, bucket: str, key: str, local_path: str) -> int:
    file_size = os.path.getsize(local_path)
    logger.info(
        "Starting S3 upload | bucket=%s key=%s local_path=%s file_size_bytes=%s",
        bucket,
        key,
        local_path,
        file_size,
    )
    try:
        s3.upload_file(local_path, bucket, key)
    except ClientError as exc:
        raise _map_s3_client_error(exc, bucket=bucket, key=key) from exc
    logger.info("Completed S3 upload | bucket=%s key=%s file_size_bytes=%s", bucket, key, file_size)
    return file_size


def _mark_unprocessed_tables_failed(
    *,
    request: SnowflakeS3MigrationRequest,
    job_name: str,
    error_message: str,
    processed_tables: Optional[List[str]] = None,
) -> None:
    processed = {str(name or "").strip() for name in (processed_tables or []) if str(name or "").strip()}
    selected = [str(name or "").strip() for name in (request.selected_tables or []) if str(name or "").strip()]

    for table_name in selected:
        if table_name in processed:
            continue
        try:
            _mark_table_failed_with_error(
                request=request,
                table_name=table_name,
                job_name=job_name,
                error_message=error_message,
            )
        except Exception as table_err:
            logger.warning("Failed to mark unprocessed S3 table %s as failed: %s", table_name, table_err)


def _process_snowflake_to_s3_table(
    *,
    request: SnowflakeS3MigrationRequest,
    table_name: str,
    source_config: Dict[str, Any],
    s3: Any,
    bucket: str,
    base_prefix: str,
    file_format: str,
    default_delimiter: str,
    default_quotechar: str,
    escapechar: Optional[str],
    null_sequence: str,
    fetch_size: int,
    table_export_settings: Dict[str, Any],
    job_name: str,
    failure_tracker: Optional[MigrationFailureTracker] = None,
) -> Dict[str, Any]:
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    table_started = datetime.now(timezone.utc)
    result = {
        "table_name": table_name,
        "status": "failed",
        "rows": 0,
        "columns": 0,
        "remote_path": None,
        "bytes_uploaded": 0,
        "error": None,
    }
    sf_conn = None

    try:
        update_table_status_snowflake(connection_id, table_name, "in_progress", job_name, migration_session_id)
        try:
            SimpleProgressTracker.reset_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
            )
        except Exception as progress_reset_err:
            logger.warning("Failed to reset live staging count for S3 table %s: %s", table_name, progress_reset_err)

        sf_service = SnowflakeService(source_config, destination_schema=None, validate_destination_schema=False)
        sf_conn = sf_service.create_connection()
        schema_name = _extract_snowflake_schema(source_config)
        if schema_name:
            with sf_conn.cursor() as schema_cur:
                schema_cur.execute(f"USE SCHEMA {schema_name}")

        table_settings = lookup_table_export_settings(table_export_settings, table_name)
        if not isinstance(table_settings, dict):
            table_settings = {}

        selected_fields = _normalize_selected_fields(table_settings.get("selected_fields"))
        sort_columns = table_settings.get("sort_columns") or (table_settings.get("excel_options") or {}).get("sort_columns")
        ts = build_utc_filename_suffix(table_settings.get("filename_date_format"))

        table_file_format = str(table_settings.get("file_format") or file_format or "csv").strip().lower()
        if table_file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        query = _build_snowflake_select_query(table_name, selected_fields,sort_columns)
        if table_file_format == "excel":
            check_excel_limits_preflight(sf_conn, query, table_name)

        with sf_conn.cursor() as cur:
            try:
                cur.execute(query)
            except Exception as snowflake_read_err:
                raise _map_snowflake_export_error(table_name, snowflake_read_err) from snowflake_read_err

            # Apply the active generic source script before the shared file
            # writers. The original Snowflake cursor remains unchanged when
            # no script is published for this table.
            active_transform = get_active_source_script(connection_id, table_name)
            script_text = (active_transform or {}).get("script_text")
            if script_text and str(script_text).strip():
                cur = TransformingCursor(
                    cur,
                    fetch_size=fetch_size,
                    script_text=script_text,
                    entry_point=(active_transform or {}).get("entry_point") or "",
                )

            table_target_folder = resolve_relative_target_folder(
                base_prefix,
                table_settings.get("target_folder"),
            )
            folder_prefix = _join_s3_key(base_prefix, table_target_folder)
            base_name = _sanitize_s3_filename(table_settings.get("output_file_name") or table_name)
            add_ts = table_settings.get("add_utc_timestamp")
            add_ts = True if add_ts is None else bool(add_ts)

            ext = {
                "csv": "csv",
                "json": "json",
                "parquet": "parquet",
                "excel": "xlsx",
            }[table_file_format]
            filename = f"{base_name}_{ts}.{ext}" if add_ts else f"{base_name}.{ext}"
            s3_key = _join_s3_key(folder_prefix, filename)

            with tempfile.TemporaryDirectory(prefix="sf_s3_") as tmpdir:
                local_path = os.path.join(tmpdir, filename)
                progress_label = f"S3 table={table_name} file={filename}"

                def _progress_callback(_rows_in_chunk: int, total_rows_written: int) -> None:
                    try:
                        SimpleProgressTracker.set_staging_count(
                            connection_id=connection_id,
                            migration_session_id=migration_session_id,
                            job_name=job_name,
                            table_name=table_name,
                            count_value=total_rows_written,
                        )
                    except Exception as progress_err:
                        logger.warning(
                            "Failed to update live staging count for S3 table %s at total_rows=%s: %s",
                            table_name,
                            total_rows_written,
                            progress_err,
                        )

                logger.info(
                    "Starting Snowflake export for S3 | table=%s fetch_size=%s format=%s local_path=%s s3_key=%s",
                    table_name,
                    fetch_size,
                    table_file_format,
                    local_path,
                    s3_key,
                )

                if table_file_format == "csv":
                    delimiter = _decode_csv_char(table_settings.get("csv_delimiter") or default_delimiter) or ","
                    quotechar = _decode_csv_char(table_settings.get("csv_quote_char") or default_quotechar) or '"'
                    total_rows, col_count = _write_csv(
                        cur,
                        local_path,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        escapechar=escapechar,
                        null_sequence=null_sequence,
                        fetch_size=fetch_size,
                        progress_label=progress_label,
                        progress_callback=_progress_callback,
                    )
                elif table_file_format == "json":
                    ns = null_sequence if null_sequence != "" else None
                    total_rows, col_count = _write_json(
                        cur,
                        local_path,
                        null_sequence=ns,
                        fetch_size=fetch_size,
                        progress_label=progress_label,
                        progress_callback=_progress_callback,
                    )
                elif table_file_format == "parquet":
                    total_rows, col_count = _write_parquet(
                        cur,
                        local_path,
                        fetch_size=fetch_size,
                        progress_label=progress_label,
                        progress_callback=_progress_callback,
                    )
                else:
                    total_rows, col_count = _write_excel(
                        cur,
                        local_path,
                        sheet_name=table_settings.get("excel_sheet_name"),
                        excel_options=table_settings.get("excel_options"),
                        conditional_formats=table_settings.get("excel_conditional_formats"),
                        fetch_size=fetch_size,
                        progress_label=progress_label,
                        progress_callback=_progress_callback,
                    )

                logger.info(
                    "Completed Snowflake export for S3 | table=%s total_rows=%s columns=%s local_path=%s",
                    table_name,
                    total_rows,
                    col_count,
                    local_path,
                )
                bytes_uploaded = _upload_file_to_s3(s3, bucket=bucket, key=s3_key, local_path=local_path)

        remote_path = f"s3://{bucket}/{s3_key}"
        table_completed_at = datetime.now(timezone.utc)
        result.update(
            {
                "status": "success",
                "rows": total_rows,
                "columns": col_count,
                "remote_path": remote_path,
                "bytes_uploaded": bytes_uploaded,
                "table_completed_at": table_completed_at,
            }
        )

        try:
            SimpleProgressTracker.set_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                count_value=total_rows,
            )
            update_table_status_snowflake(
                connection_id,
                table_name,
                "completed",
                job_name,
                migration_session_id,
                sync_end_time=table_completed_at,
            )
            data_transfer_logs(
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                usr_id=request.user_id,
                table_name=table_name,
                start_time=table_started,
                end_time=table_completed_at,
                inserts=total_rows,
                columns_data=col_count,
                session=migration_session_id,
                job_name=job_name,
                migration_session_id=migration_session_id,
            )
        except Exception as log_err:
            _log_stage_error(
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                function_name="_process_snowflake_to_s3_table.logging",
                error=log_err,
                extra_message=f"table={table_name}",
            )

        return result
    except HTTPException as exc:
        result["error"] = _extract_error_message(exc)
        if failure_tracker is not None:
            failure_tracker.add_failure(table_name, result["error"], "Snowflake to S3")
        _mark_table_failed_with_error(
            request=request,
            table_name=table_name,
            job_name=job_name,
            error_message=result["error"],
        )
        _log_stage_error(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            function_name="_process_snowflake_to_s3_table.http",
            error=exc,
            extra_message=f"table={table_name}",
        )
        return result
    except Exception as exc:
        result["error"] = _extract_error_message(exc)
        if failure_tracker is not None:
            failure_tracker.add_failure(table_name, result["error"], "Snowflake to S3")
        _mark_table_failed_with_error(
            request=request,
            table_name=table_name,
            job_name=job_name,
            error_message=result["error"],
        )
        _log_stage_error(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            function_name="_process_snowflake_to_s3_table",
            error=exc,
            extra_message=f"table={table_name}",
        )
        return result
    finally:
        try:
            if sf_conn is not None:
                sf_conn.close()
        except Exception:
            pass


def migrate_snowflake_to_s3(request: SnowflakeS3MigrationRequest) -> Dict[str, Any]:
    logger.info(
        "Entering migrate_snowflake_to_s3 | connection_id=%s | tables=%s",
        request.connection_id,
        len(request.selected_tables or []),
    )
    started_at = datetime.now(timezone.utc)
    connection_id = request.connection_id
    migration_session_id = request.migration_session_id
    job_name = request.job_name or f"Snowflake-S3 Migration {connection_id}"
    failure_tracker = MigrationFailureTracker()
    results = {
        "status": "success",
        "status_code": 200,
        "job_name": job_name,
        "connection_id": connection_id,
        "migration_session_id": migration_session_id,
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "tables": {},
        "total_tables": len(request.selected_tables or []),
        "successful_tables": 0,
        "failed_tables": 0,
        "total_rows": 0,
        "service_name": "Snowflake-S3",
    }

    try:
        update_migration_status_log(
            job_name=job_name,
            message="Migration in progress",
            status="I",
            session=migration_session_id,
        )

        source_config = request.source_config or {}
        s3_config = dict(request.destination_config or {})
        bucket = str(s3_config.get("s3_bucket") or "").strip()
        base_prefix = _extract_s3_base_prefix(s3_config)

        s3 = _build_s3_client(s3_config)
        _validate_s3_destination_access(s3, bucket=bucket, base_prefix=base_prefix)

        file_format = str(s3_config.get("file_format") or "csv").strip().lower()
        if file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        default_delimiter = _decode_csv_char(s3_config.get("csv_delimiter") or ",")
        default_quotechar = _decode_csv_char(s3_config.get("csv_quote_char") or s3_config.get("csv_quotechar") or '"')
        escapechar = None
        null_sequence = s3_config.get("null_sequence")
        null_sequence = "" if null_sequence is None else str(null_sequence)

        fetch_size = int(request.chunk_count) if request.chunk_count else 5000
        if fetch_size <= 0:
            fetch_size = 5000

        table_export_settings = request.table_export_settings or {}
        selected_tables = [str(table_name or "").strip() for table_name in (request.selected_tables or []) if str(table_name or "").strip()]
        execution_order = str(request.execution_order or "sequential").strip().lower()
        logger.info(
            "Snowflake->S3 execution mode=%s | selected_tables=%s | bucket=%s | base_prefix=%s",
            execution_order,
            len(selected_tables),
            bucket,
            base_prefix,
        )

        table_results: List[Dict[str, Any]] = []
        if execution_order == "parallel" and len(selected_tables) > 1:
            max_workers = min(3, len(selected_tables))
            logger.info("Executing Snowflake->S3 in parallel mode | max_workers=%s", max_workers)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _process_snowflake_to_s3_table,
                        request=request,
                        table_name=table_name,
                        source_config=source_config,
                        s3=s3,
                        bucket=bucket,
                        base_prefix=base_prefix,
                        file_format=file_format,
                        default_delimiter=default_delimiter,
                        default_quotechar=default_quotechar,
                        escapechar=escapechar,
                        null_sequence=null_sequence,
                        fetch_size=fetch_size,
                        table_export_settings=table_export_settings,
                        job_name=job_name,
                        failure_tracker=failure_tracker,
                    ): table_name
                    for table_name in selected_tables
                }
                for future in as_completed(futures):
                    table_results.append(future.result())
        else:
            logger.info("Executing Snowflake->S3 in sequential mode")
            for table_name in selected_tables:
                table_results.append(
                    _process_snowflake_to_s3_table(
                        request=request,
                        table_name=table_name,
                        source_config=source_config,
                        s3=s3,
                        bucket=bucket,
                        base_prefix=base_prefix,
                        file_format=file_format,
                        default_delimiter=default_delimiter,
                        default_quotechar=default_quotechar,
                        escapechar=escapechar,
                        null_sequence=null_sequence,
                        fetch_size=fetch_size,
                        table_export_settings=table_export_settings,
                        job_name=job_name,
                        failure_tracker=failure_tracker,
                    )
                )

        for table_result in table_results:
            table_name = table_result["table_name"]
            results["tables"][table_name] = {
                "status": table_result["status"],
                "rows": table_result["rows"],
                "columns": table_result["columns"],
                "remote_path": table_result["remote_path"],
                "bytes_uploaded": table_result["bytes_uploaded"],
                "error": table_result["error"],
            }
            if table_result["status"] == "success":
                results["total_rows"] += table_result["rows"]
                results["successful_tables"] += 1
            else:
                results["failed_tables"] += 1

            _trigger_ws_all(
                migration_session_id=migration_session_id,
                connection_id=connection_id,
                cmp_id=request.cmp_id,
            )

        if results["failed_tables"] == 0:
            results["status"] = "success"
        elif results["successful_tables"] == 0:
            results["status"] = "failed"
        else:
            results["status"] = "partial"

        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["status_code"] = 200 if results["failed_tables"] == 0 else 206

        # Supply the child-result contract used by the Celery finalizer.
        results["execution_time"] = time.time() - started_at.timestamp()
        results["successful_tables_count"] = results["successful_tables"]
        results["failed_tables_count"] = results["failed_tables"]
        results["successful_tables_list"] = [
            table for table, result in results["tables"].items() if result["status"] == "success"
        ]
        results["failed_tables_list"] = [
            table for table, result in results["tables"].items() if result["status"] != "success"
        ]

        # Require failed table status to be saved before returning the API result.
        for failed_table in results["failed_tables_list"]:
            update_table_status_snowflake(
                connection_id, failed_table, "failed", job_name, migration_session_id
            )

        # Child tasks report results; only the parent finalizes the activity log.
        if not request.celery_child_mode:
            log_migration_completion(
                execution_time=results["execution_time"],
                total_rows=results["total_rows"],
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                user_id=request.user_id,
                session=migration_session_id,
                total_tables=results["total_tables"],
                successful_tables=results["successful_tables"],
                failed_tables=results["failed_tables"],
                job_name=job_name,
                log_id=request.log_id,
            )

        failure_tracker.send_consolidated_error_email(
            connection_name=request.connection_name or f"S3 {connection_id}",
            service_name="Snowflake to S3",
            user_id=request.user_id,
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            company_name=request.company_name or "Unknown Company",
            is_final_attempt=True,
        )
        return results
    except Exception as exc:
        error_message = _extract_error_message(exc)
        _mark_unprocessed_tables_failed(
            request=request,
            job_name=job_name,
            error_message=error_message,
            processed_tables=list(results.get("tables", {}).keys()),
        )
        _log_stage_error(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            function_name="migrate_snowflake_to_s3",
            error=exc,
            extra_message=f"job_name={job_name}",
        )
        try:
            log_migration_failure(
                job_name=job_name,
                error_message=error_message,
                session=migration_session_id,
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                user_id=request.user_id,
                execution_time=time.time() - started_at.timestamp(),
                total_rows=results.get("total_rows", 0),
                successful_tables=results.get("successful_tables", 0),
                total_tables=results.get("total_tables", 0),
            )
        except Exception as failure_log_err:
            logger.warning("Failed to write migration failure log for %s: %s", job_name, failure_log_err)
        raise
