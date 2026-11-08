# routers/amazon_s3_router.py
"""
Amazon S3 Router - FastAPI endpoints for S3 source operations
Handles: Connection validation, table listing, and migration to Snowflake
"""
from fastapi import APIRouter, HTTPException, Request

from pydantic import ValidationError
from datetime import datetime
import logging
import traceback
from services.amazon_s3.service import AmazonS3Service
from utils.error_utils import log_migration_failure
from models.amazon_s3_models import (
    AmazonS3ValidationRequest,
    AmazonS3ListingTables,
    AmazonS3MigrationRequest,
    AmazonS3PatternPreviewRequest,
    AmazonS3PKSuggestionRequest,
    LoadMethod,
    FileType,
    FileMappingMethod,
    JSONMode

)

# Set up logger for structured logging
logger = logging.getLogger(__name__)

router = APIRouter()


def _structured_print(*args, **kwargs):
    """Compatibility wrapper: route legacy print calls into structured logs."""
    sep = kwargs.get("sep", " ")
    msg = sep.join(str(arg) for arg in args) if args else ""
    logger.info(msg)


print = _structured_print

def _reraise_as_422(http_error: HTTPException) -> None:
    """Keep frontend-compatible behavior by normalizing service HTTP errors to 422."""
    raise HTTPException(status_code=422, detail=http_error.detail)


def _safe_s3_detail(
    error: Exception,
    default_detail: str,
    *,
    allow_raw_for_custom_pk: bool = False,
) -> str:
    """Return user-safe S3 error details without leaking internal exception strings."""
    raw = str(error or "")
    error_str = raw.lower()

    if "aws_access_key_id" in error_str:
        return "Invalid `aws_access_key_id`. Please verify AWS Access Key ID."
    if "aws_secret_access_key" in error_str:
        return "Invalid `aws_secret_access_key`. Please verify AWS Secret Access Key."
    if "s3_bucket" in error_str and "missing" in error_str:
        return "Missing required `s3_bucket`."
    if "s3_base_prefix" in error_str or "folder path" in error_str:
        return "Invalid `s3_base_prefix` (folder path). Verify folder path in the bucket."
    if "credentials" in error_str or "access denied" in error_str:
        return "S3 access denied. Check AWS credentials and permissions."
    if "bucket" in error_str and "not found" in error_str:
        return "S3 bucket not found or not accessible."
    if "file" in error_str and "not found" in error_str:
        return "One or more selected files were not found in S3."
    if "parse" in error_str:
        return "Failed to parse file. Check file type and format options."
    if allow_raw_for_custom_pk and "custom_primary_key" in error_str:
        return raw or default_detail
    return default_detail

@router.post("/AmazonS3-Authorization")
async def validate_connection(data: AmazonS3ValidationRequest):
    """
    Validate Amazon S3 source connection.
    
    Tests bucket access and returns sample files as proof of connection.
    
    Args:
        data: AmazonS3ValidationRequest with S3 credentials
        
    Returns:
        dict: Connection validation result with status_code and body
    """
    print("=" * 80)
    print("[ROUTER] POST /AmazonS3-Authorization")
    print("=" * 80)
    print("[ROUTER] Stage 1: Received connection validation request")
    
    try:
        # Stage 2: Call service layer for connection validation
        print("[ROUTER] Stage 2: Calling AmazonS3Source.validate_connection...")
        logger.info(f"S3 connection validation started for connection_id: {getattr(data, 'connection_id', 'N/A')}")
        
        result = await AmazonS3Service.authenticate(data)
        
        # Stage 3: Success - Return validation result
        status_code = result.get('status_code', 200)
        if status_code != 200:
            detail = result.get("body") or "S3 validation failed"
            print(f"[ROUTER] Stage 3: ✗ Validation failed - status_code={status_code} detail={detail}")
            logger.warning(f"S3 connection validation failed: status_code={status_code}, detail={detail}")
            raise HTTPException(status_code=422, detail=detail)

        print(f"[ROUTER] Stage 3: ✓ Validation successful - status_code={status_code}")
        logger.info(f"S3 connection validation completed: status_code={status_code}")
        return result
        
    except HTTPException as http_error:
        # Stage 3 (Error): HTTPException from service layer - re-raise to preserve status code
        print(f"[ROUTER] Stage 3 (Error): HTTPException from service - status={http_error.status_code}")
        print(f"[ROUTER] Error detail: {http_error.detail}")
        logger.error(f"S3 connection validation HTTPException: status={http_error.status_code}, detail={http_error.detail}")
        _reraise_as_422(http_error)
        
    except ValidationError as validation_error:
        # Stage 3 (Error): Pydantic validation error - return 422
        print(f"[ROUTER] Stage 3 (Error): Validation error - invalid request data")
        print(f"[ROUTER] Validation errors: {validation_error.errors()}")
        logger.error(f"S3 connection validation - ValidationError: {validation_error.errors()}")
        raise HTTPException(status_code=422, detail=validation_error.errors())
        
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': 'S3 Connection Validation',
                'service_name': 'Amazon S3',
                'is_terminal_failure': False
            }
        )
        raise HTTPException(
            status_code=422,
            detail=_safe_s3_detail(e, "S3 connection validation failed.")
        )

@router.post("/AmazonS3-listing-files")
async def list_files(data: AmazonS3ListingTables):
    """
    List available files from S3 bucket.
    
    Returns file listing with metadata (table name, file_key, last_modified).
    This endpoint lists files present in the bucket without schema inference.
    
    Args:
        data: AmazonS3ListingTables with connection details
        
    Returns:
        dict: {"result": {"tables": [...], "files_count": N}}
    """
    print("=" * 80)
    print("[ROUTER] POST /AmazonS3-listing-files")
    print("=" * 80)
    print(f"[ROUTER] Stage 1: Received file listing request - connection_id={data.connection_id}")
    print(f"[ROUTER]   - Bucket: {data.AmazonS3.get('s3_bucket', 'N/A')}")
    print(f"[ROUTER]   - Base prefix: {data.AmazonS3.get('s3_base_prefix', 'N/A')}")
    print(f"[ROUTER]   - File type filter: {data.AmazonS3.get('file_type', 'N/A')}")
    
    try:
        # Stage 2: Call service layer to list files
        print("[ROUTER] Stage 2: Calling AmazonS3Service.list_tables...")
        logger.info(f"S3 file listing started for connection_id: {data.connection_id}")
        
        result = await AmazonS3Service.list_tables(data)
        
        # Stage 3: Success - Process and return results
        tables_count = len(result.get('tables', []))
        files_count = result.get('files_count', 0)
        print(f"[ROUTER] Stage 3: ✓ File listing successful")
        print(f"[ROUTER]   - Unique tables found: {tables_count}")
        print(f"[ROUTER]   - Total files found: {files_count}")
        logger.info(f"S3 file listing completed: {tables_count} tables, {files_count} files")
        
        return {"result": result}
        
    except HTTPException as http_error:
        # Stage 3 (Error): HTTPException from service layer - re-raise to preserve status code
        print(f"[ROUTER] Stage 3 (Error): HTTPException from service - status={http_error.status_code}")
        print(f"[ROUTER] Error detail: {http_error.detail}")
        logger.error(f"S3 file listing HTTPException: status={http_error.status_code}, detail={http_error.detail}")
        _reraise_as_422(http_error)
        
    except ValidationError as validation_error:
        # Stage 3 (Error): Pydantic validation error - return 422
        print(f"[ROUTER] Stage 3 (Error): Validation error - invalid request data")
        print(f"[ROUTER] Validation errors: {validation_error.errors()}")
        logger.error(f"S3 file listing - ValidationError: {validation_error.errors()}")
        raise HTTPException(status_code=422, detail=validation_error.errors())
        
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': 'S3 Object Listing',
                'service_name': 'Amazon S3',
                'is_terminal_failure': False
            }
        )
        raw_detail = str(e)
        error_str = raw_detail.lower()
        detail = raw_detail or "Failed to list S3 files."
        if "aws_access_key_id" in error_str:
            detail = "Invalid `aws_access_key_id`. Please verify AWS Access Key ID."
        elif "aws_secret_access_key" in error_str:
            detail = "Invalid `aws_secret_access_key`. Please verify AWS Secret Access Key."
        elif "s3_bucket" in error_str and "missing" in error_str:
            detail = "Missing required `s3_bucket`."
        elif "s3_base_prefix" in error_str or "folder path" in error_str:
            detail = raw_detail
        elif "credentials" in error_str or "access denied" in error_str:
            detail = "S3 access denied. Check AWS credentials and permissions."
        elif "bucket" in error_str and "not found" in error_str:
            detail = "S3 bucket not found or not accessible."
        raise HTTPException(status_code=422, detail=detail)

@router.post("/AmazonS3-listing-tables")
async def list_tables(data: AmazonS3ListingTables):
    """
    List S3 logical tables with inferred schema (columns).
    
    Returns tables with column details for writing to SourceTableSelection.
    Used by Django's fetch_tables_from_api for schema refresh.
    This endpoint reads sample data from files to infer column structure.
    
    Args:
        data: AmazonS3ListingTables with connection details
        
    Returns:
        dict: {"result": {"tables": [{"table": "...", "columns": {...}}], "files_count": N}}
    """
    import time
    start_time = time.time()
    print("=" * 80)
    print("[ROUTER] POST /AmazonS3-listing-tables")
    print("=" * 80)
    print(f"[ROUTER] Stage 1: Received table listing request (with schema) - connection_id={data.connection_id}")
    print(f"[ROUTER]   - Bucket: {data.AmazonS3.get('s3_bucket', 'N/A')}")
    print(f"[ROUTER]   - Base prefix: {data.AmazonS3.get('s3_base_prefix', 'N/A')}")
    print(f"[ROUTER]   - File type filter: {data.AmazonS3.get('file_type', 'N/A')}")
    print(f"[ROUTER]   - Load method: {data.AmazonS3.get('load_method', 'N/A')}")
    print(f"[ROUTER] 🔍 DEBUG: custom_primary_key in AmazonS3: {data.AmazonS3.get('custom_primary_key', 'NOT_FOUND')}")
    print(f"[ROUTER]   → This endpoint will infer column schema from file samples")
    
    try:
        # Stage 2: Call service layer to list tables WITH schema
        print("[ROUTER] Stage 2: Calling AmazonS3Service.list_tables_with_schema...")
        print("[ROUTER]   → Service will read sample data from files to infer columns")
        logger.info(f"S3 table listing (with schema) started for connection_id: {data.connection_id}")
        
        result = await AmazonS3Service.list_tables_with_schema(data)
        
        # Stage 3: Success - Process and return results
        tables_count = len(result.get('tables', []))
        files_count = result.get('files_count', 0)
        
        # Debug: Check if columns are present
        tables_with_columns = sum(1 for t in result.get('tables', []) if t.get('columns'))
        tables_without_columns = tables_count - tables_with_columns
        
        print(f"[ROUTER] Stage 3: ✓ Table listing (with schema) successful")
        print(f"[ROUTER]   - Unique tables found: {tables_count}")
        print(f"[ROUTER]   - Total files found: {files_count}")
        print(f"[ROUTER]   - Tables with columns: {tables_with_columns}")
        if tables_without_columns > 0:
            print(f"[ROUTER]   - ⚠ Tables without columns: {tables_without_columns} (empty files or parsing errors)")
        logger.info(f"S3 table listing (with schema) completed: {tables_count} tables, {files_count} files, {tables_with_columns} with columns")
        
        # ========================================================================
        # Stage 4: Write tables to SourceTableSelection database
        # ========================================================================
        print(f"[ROUTER] Stage 4: Writing tables to SourceTableSelection database...")
        try:
            # Extract job_name and migration_session_id from data
            job_name = getattr(data, 'job_name', None)
            migration_session_id = getattr(data, 'migration_session_id', None)
            
            # Write tables to database
            db_result = AmazonS3Service.refresh_and_update_s3_tables_in_database(
                connection_id=data.connection_id,
                tables_result=result,
                job_name=job_name,
                migration_session_id=migration_session_id
            )
            
            print(f"[ROUTER] Stage 4: ✓ Database write successful")
            print(f"[ROUTER]   - Tables inserted: {db_result.get('tables_inserted', 0)}")
            print(f"[ROUTER]   - Tables updated: {db_result.get('tables_updated', 0)}")
            logger.info(f"S3 tables written to database: {db_result.get('total_tables', 0)} tables")
            
        except Exception as db_error:
            error_msg = str(db_error)
            print(f"[ROUTER] Stage 4 (Error): ✗ Database write failed: {error_msg}")
            logger.error(f"S3 database write failed for connection_id={data.connection_id}: {error_msg}")

            from utils.error_utils import handle_service_error
            handle_service_error(
                error=db_error,
                context={
                    'job_name': data.job_name,
                    'log_id': data.log_id,
                    'cmp_id': data.cmp_id,
                    'connection_id': data.connection_id,
                    'user_id': data.user_id,
                    'operation': 'UPDATE_SCHEMA',
                    'is_terminal_failure': True,
                    'execution_time': time.time() - start_time,
                    'total_tables': len(result.get('tables', [])),
                    'successful_tables': 0,
                    'total_rows': 0,
                    'service_name': 'Amazon S3'
                }
            )
            raise HTTPException(
                status_code=422,
                detail=f"Update schema failed while writing tables to database: {error_msg}"
            )

        return {"result": result}

    except HTTPException as http_error:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=http_error,
            context={
                'job_name': data.job_name,
                'log_id': data.log_id,
                'cmp_id': data.cmp_id,
                'connection_id': data.connection_id,
                'user_id': data.user_id,
                'operation': 'UPDATE_SCHEMA',
                'is_terminal_failure': True,
                'execution_time': time.time() - start_time,
                'total_tables': 0,
                'successful_tables': 0,
                'total_rows': 0,
                'service_name': 'Amazon S3'
            }
        )
        _reraise_as_422(http_error)

    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': data.job_name,
                'log_id': data.log_id,
                'cmp_id': data.cmp_id,
                'connection_id': data.connection_id,
                'user_id': data.user_id,
                'operation': 'UPDATE_SCHEMA',
                'is_terminal_failure': True,
                'execution_time': time.time() - start_time,
                'total_tables': 0,
                'successful_tables': 0,
                'total_rows': 0,
                'service_name': 'Amazon S3'
            }
        )
        detail = _safe_s3_detail(
            e,
            "Update schema failed.",
            allow_raw_for_custom_pk=True,
        )
        raise HTTPException(status_code=422, detail=detail)

@router.post("/Snowflake-AmazonS3-migrate")
async def migrate(request: AmazonS3MigrationRequest):
    """
    Migrate data from Amazon S3 to Snowflake.
    
    Processes selected files using streaming chunks, uploads to GCS,
    and loads into Snowflake with merge logic.
    
    Args:
        request: AmazonS3MigrationRequest with migration parameters
        
    Returns:
        dict: Migration results with status and row counts
    """
    print("=" * 80)
    print("[ROUTER] POST /Snowflake-AmazonS3-migrate")
    print("=" * 80)
    
    # Stage 1: Request received and validated by FastAPI
    print("[ROUTER] Stage 1: Request received and validated")
    print(f"[ROUTER]   - Connection ID: {request.connection_id}")
    print(f"[ROUTER]   - Migration Session ID: {request.migration_session_id}")
    print(f"[ROUTER]   - Selected files: {len(request.selected_tables)}")
    
    start_time = datetime.now()
    
    # ✅ REFACTORED: Read S3-specific fields from source_config (matching other sources)
    source_config = request.source_config
    file_type = source_config.get("file_type", "unknown")
    file_mapping_mode = source_config.get("file_mapping_mode", "single_table_per_file")
    load_method = source_config.get("load_method", "upsert_file_row")
    
    print(f"[ROUTER]   - File type: {file_type}")
    print(f"[ROUTER]   - File mapping mode: {file_mapping_mode}")
    print(f"[ROUTER]     → single_table_per_file: One table per file")
    print(f"[ROUTER]     → multi_files_single_table: Multiple files → Single table")
    print(f"[ROUTER]   - Load method: {load_method}")
    print(f"[ROUTER]     → upsert_file_row: UPSERT using _FILE and _ROW")
    print(f"[ROUTER]     → append_file_modified: APPEND using _FILE, _ROW, _MODIFIED")
    print(f"[ROUTER]     → upsert_custom_key: UPSERT using custom primary keys")
    print(f"[ROUTER]   - Chunk size: {request.chunk_count:,} rows")
    print(f"[ROUTER]   - Safety interval: {request.safety_interval} minutes")
    print(f"[ROUTER]   - Drop and reload: {request.drop_and_reload}")
    print(f"[ROUTER]   - Logs creation: {request.logs_creation}")
    
    logger.info(
        f"S3 migration started - connection_id={request.connection_id}, "
        f"session_id={request.migration_session_id}, "
        f"files={len(request.selected_tables)}, "
        f"file_type={file_type}, "
        f"load_method={load_method}"
    )
    
    try:
        # Stage 2: Call migration service
        print("[ROUTER] Stage 2: Calling sync_amazon_s3_to_snowflake service...")
        print("[ROUTER]   - Service will handle: Connection testing, file processing, GCS upload, Snowflake loading")
        
        result = await AmazonS3Service.migrate(request)
        
        # Stage 3: Success - Process and return results
        status = result.get('status', 'unknown')
        files_processed = result.get('files_processed', 0)
        total_rows = result.get('total_rows', 0)
        failed_tables = result.get('failed_tables', 0)
        duration = result.get('duration_seconds', 0)
        
        print("[ROUTER] Stage 3: ✓ Migration service completed")
        print(f"[ROUTER]   - Status: {status}")
        print(f"[ROUTER]   - Files processed: {files_processed}")
        print(f"[ROUTER]   - Total rows: {total_rows:,}")
        print(f"[ROUTER]   - Failed tables: {failed_tables}")
        print(f"[ROUTER]   - Duration: {duration:.2f} seconds")
        
        logger.info(
            f"S3 migration completed - status={status}, "
            f"files_processed={files_processed}, "
            f"total_rows={total_rows}, "
            f"failed_tables={failed_tables}, "
            f"duration={duration:.2f}s"
        )
        
        return result
        
    except ValidationError as validation_error:
        # Stage 3 (Error): Pydantic validation error - return 422
        print("[ROUTER] Stage 3 (Error): Validation error - invalid request data")
        print(f"[ROUTER] Validation errors: {validation_error.errors()}")
        logger.error(f"S3 migration - ValidationError: {validation_error.errors()}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=422, detail=validation_error.errors())
        
    except HTTPException as http_error:
        # Stage 3 (Error): HTTPException from service layer - re-raise to preserve status code
        print(f"[ROUTER] Stage 3 (Error): HTTPException from service - status={http_error.status_code}")
        print(f"[ROUTER] Error detail: {http_error.detail}")
        logger.error(f"S3 migration HTTPException: status={http_error.status_code}, detail={http_error.detail}")
        _reraise_as_422(http_error)
        
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(request, 'job_name', 'Amazon S3 Migration'),
                'log_id': getattr(request, 'log_id', None),
                'cmp_id': getattr(request, 'cmp_id', None),
                'connection_id': getattr(request, 'connection_id', None),
                'user_id': getattr(request, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'successful_tables': 0,
                'total_tables': len(request.selected_tables) if request.selected_tables else 0,
                'total_rows': 0,
                'service_name': 'Amazon S3'
            }
        )
        raise HTTPException(
            status_code=422,
            detail=_safe_s3_detail(e, "S3 migration failed.")
        )


@router.post("/AmazonS3-preview-pattern")
async def preview_pattern(data: AmazonS3PatternPreviewRequest):
    """
    Preview which S3 files match a regex pattern.
    
    Used for Multi Files → Single Table mode to validate patterns before migration.
    Returns matched files, non-matched samples, and pattern validation.
    
    Args:
        data: AmazonS3PatternPreviewRequest with S3 credentials and pattern
        
    Returns:
        dict: {
            "status": "success",
            "pattern": str,
            "total_files_in_folder": int,
            "total_files_by_type": int,
            "matched_files_count": int,
            "matched_files": List[dict],
            "sample_non_matched_files": List[dict],
            "pattern_validation": {"is_valid_regex": bool, "error_message": str},
            "suggested_table_name": str
        }
    """
    print("=" * 80)
    print("[ROUTER] POST /AmazonS3-preview-pattern")
    print("=" * 80)
    print(f"[ROUTER] Stage 1: Received pattern preview request")
    print(f"[ROUTER]   - Connection ID: {data.connection_id}")
    print(f"[ROUTER]   - Pattern: {data.multi_files_prefix}")
    print(f"[ROUTER]   - Max Results: {data.max_results}")
    
    try:
        # Stage 2: Call service layer for pattern preview
        print("[ROUTER] Stage 2: Calling AmazonS3Service.preview_pattern...")
        logger.info(f"S3 pattern preview started - connection_id: {data.connection_id}, pattern: {data.multi_files_prefix}")
        
        result = await AmazonS3Service.preview_pattern(data)
        
        # Stage 3: Success - Return preview result
        print(f"[ROUTER] Stage 3: ✓ Pattern preview successful")
        print(f"[ROUTER]   - Matched files: {result.get('matched_files_count', 0)}")
        print(f"[ROUTER]   - Total files: {result.get('total_files_in_folder', 0)}")
        logger.info(f"S3 pattern preview completed - matched: {result.get('matched_files_count', 0)} files")
        print("=" * 80)
        
        return result
        
    except ValidationError as ve:
        # Stage 4a: Validation error - Invalid request data
        error_message = f"Request validation failed: {str(ve)}"
        print(f"[ROUTER] Stage 4a: ✗ Validation error - {error_message}")
        logger.error(f"S3 pattern preview validation error: {error_message}")
        print("=" * 80)
        raise HTTPException(status_code=422, detail=error_message)
        
    except HTTPException as http_error:
        # Stage 4b: Re-raise HTTP exceptions from service layer
        print("[ROUTER] Stage 4b: ✗ HTTP exception from service layer")
        print("=" * 80)
        _reraise_as_422(http_error)
        
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': 'S3 Pattern Preview',
                'service_name': 'Amazon S3',
                'is_terminal_failure': False
            }
        )
        raise HTTPException(
            status_code=422,
            detail=_safe_s3_detail(e, "S3 pattern preview failed.")
        )

@router.post("/AmazonS3-suggest-primary-keys")
async def suggest_primary_keys(
    data: AmazonS3PKSuggestionRequest
):
    """
    Analyze S3 file columns and return column statistics
    so the user can select custom primary keys.

    ONLY supported when:
    load_method = upsert_custom_key
    """

    print("=" * 80)
    print("[ROUTER] POST /AmazonS3-suggest-primary-keys")
    print("=" * 80)

    print("[ROUTER] Stage 1: Request received")
    print(f"[ROUTER]   - Sample rows: {data.sample_rows}")

    s3 = data.s3

    try:
        # ------------------------------------------------------------------
        # Stage 1a: Business rule validation
        # ------------------------------------------------------------------

        if s3.load_method != LoadMethod.upsert_custom_key:
            raise HTTPException(
                status_code=422,
                detail="Primary key suggestion is only supported for load_method = upsert_custom_key"
            )

        if s3.file_type == FileType.xml and ((s3.xml_mode.value if s3.xml_mode else "packed") == "packed"):
            raise HTTPException(
                status_code=422,
                detail="Primary key suggestion is not supported for XML (packed format)"
            )

        if s3.file_type == FileType.json and s3.json_mode == JSONMode.packed:
            raise HTTPException(
                status_code=422,
                detail="Primary key suggestion is not supported for packed JSON"
            )

        print("[ROUTER] Stage 1a: Business rules validated")

        # ------------------------------------------------------------------
        # Stage 1b: Resolve mapping intent (logging only)
        # ------------------------------------------------------------------

        if s3.file_mapping_method == FileMappingMethod.single_table_per_file:
            file_count = len(s3.single_file_table_mapping)
            mapping_mode = "single_table_per_file"
        else:
            file_count = 0
            mapping_mode = "multi_files_single_table"

        print(f"[ROUTER] Stage 1b: Mapping intent resolved")
        print(f"[ROUTER]   - Mapping mode: {mapping_mode}")
        print(f"[ROUTER]   - Total files involved: {file_count}")

        # ------------------------------------------------------------------
        # Stage 2: Call service layer
        # ------------------------------------------------------------------

        print("[ROUTER] Stage 2: Calling AmazonS3Service.suggest_primary_keys...")
        logger.info(
            f"S3 PK suggestion started - connection_id: {data.connection_id}, "
            f"mapping_mode: {mapping_mode}, sample_rows: {data.sample_rows}"
        )

        result = await AmazonS3Service.suggest_primary_keys(data)

        # ------------------------------------------------------------------
        # Stage 3: Success - Log results and return JSON
        # ------------------------------------------------------------------

        print(f"[ROUTER] Stage 3: ✓ PK suggestion successful")
        tables = result.get('tables', [])
        print(f"[ROUTER]   - Analyzed {len(tables)} tables:")
        for table in tables:
            print(
                f"[ROUTER]   - {table.get('table_name')}: "
                f"{len(table.get('columns', []))} columns"
            )

        print("=" * 80)
        return result

    # ------------------------------------------------------------------
    # Stage 4: Error handling
    # ------------------------------------------------------------------

    except HTTPException as http_error:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=http_error,
            context={
                'job_name': 'S3 PK Suggestion',
                'service_name': 'Amazon S3',
                'is_terminal_failure': False
            }
        )
        print("[ROUTER] ✗ HTTP exception raised")
        _reraise_as_422(http_error)

    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': 'S3 PK Suggestion',
                'service_name': 'Amazon S3',
                'is_terminal_failure': False
            }
        )
        print("[ROUTER] ✗ Unexpected error")
        logger.exception("[ROUTER] Unexpected error in /AmazonS3-suggest-primary-keys")

        detail = _safe_s3_detail(e, "Primary key suggestion failed.")
        raise HTTPException(status_code=422, detail=detail)


