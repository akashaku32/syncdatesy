from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
import json
import urllib.parse

from models.google_drive_models import (
    GoogleDriveListingTables,
    GoogleDriveMigrationRequest,
    GoogleDrivePKSuggestionRequest,
    GoogleDrivePatternPreviewRequest,
    GoogleDriveValidationRequest,
)
from models.google_drive_snowflake_models import GoogleDriveSnowflakeMigrationRequest
from services.google_drive.client import (
    get_google_drive_authorize_url,
    get_access_token,
    validate_google_drive_connection,
)
from services.google_drive.as_destination.from_snowflake import sync_snowflake_to_google_drive
from services.google_drive.as_source.to_snowflake import GoogleDriveSnowflakeService
from utils.deployment_config import get_google_drive_oauth_return_url
from utils.error_utils import handle_service_error, handle_update_schema_listing_error

router = APIRouter()


@router.get("/Source-GoogleDrive-Authorization")
@router.get("/GoogleDrive-Authorization")
def authorize_google_drive(state: str, data: str, redirect_uri: str):
    try:
        decoded_data = urllib.parse.unquote(data)
        parsed = json.loads(decoded_data)
        auth_url = get_google_drive_authorize_url(
            state,
            parsed,
            redirect_uri=redirect_uri,
        )
        return {"auth_url": auth_url}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")


@router.get("/google-drive/callback")
def google_drive_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    encoded_data = request.query_params.get("data_new")

    try:
        data = json.loads(urllib.parse.unquote(encoded_data)) if encoded_data else {}
        redirect_uri = data.get("redirect_uri")
        token = get_access_token(code, state, data, redirect_uri=redirect_uri)
        folder_id = data.get("folder_id")
        folder_name = data.get("folder_name")
        create_folder_if_missing = data.get("create_folder_if_missing")
        if folder_id or folder_name:
            validation_payload = {
                "GoogleDrive": {
                    **data,
                    **token,
                    "folder_id": folder_id,
                    "folder_name": folder_name,
                    "create_folder_if_missing": create_folder_if_missing,
                }
            }
            validated = validate_google_drive_connection(validation_payload)
            token["access_token"] = validated.get("access_token") or token.get("access_token")
            token["refresh_token"] = validated.get("refresh_token") or token.get("refresh_token")
            token["token_expires_at"] = validated.get("token_expires_at") or token.get("token_expires_at")
            token["folder_id"] = validated.get("folder_id")
            token["folder_name"] = validated.get("folder_name")
        return token
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")


@router.get("/source/googledrive/callback")
def google_drive_source_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    encoded_data = request.query_params.get("data_new")

    try:
        data = json.loads(urllib.parse.unquote(encoded_data)) if encoded_data else {}
        redirect_uri = data.get("redirect_uri")
        token = get_access_token(code, state, data, redirect_uri=redirect_uri)
        folder_id = data.get("folder_id")
        folder_name = data.get("folder_name")
        create_folder_if_missing = data.get("create_folder_if_missing")
        if folder_id or folder_name:
            validation_payload = {
                "GoogleDrive": {
                    **data,
                    **token,
                    "folder_id": folder_id,
                    "folder_name": folder_name,
                    "create_folder_if_missing": create_folder_if_missing,
                }
            }
            validated = validate_google_drive_connection(validation_payload)
            token["access_token"] = validated.get("access_token") or token.get(
                "access_token"
            )
            token["refresh_token"] = validated.get("refresh_token") or token.get(
                "refresh_token"
            )
            token["token_expires_at"] = validated.get(
                "token_expires_at"
            ) or token.get("token_expires_at")
            token["folder_id"] = validated.get("folder_id")
            token["folder_name"] = validated.get("folder_name")

        params = {key: value for key, value in token.items() if value is not None}
        query_string = urllib.parse.urlencode(params)
        return RedirectResponse(url=get_google_drive_oauth_return_url(query_string))

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")


@router.post("/GoogleDrive-Authorization")
async def validate_google_drive(data: GoogleDriveValidationRequest):
    try:
        raw_details = data.GoogleDrive
        if isinstance(raw_details, str):
            try:
                raw_details = json.loads(raw_details)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="GoogleDrive must be a valid JSON object")

        payload = {"GoogleDrive": raw_details if raw_details is not None else {}}
        result = validate_google_drive_connection(payload)
        return result
    except HTTPException:
        raise
    except ValidationError as validation_error:
        raise HTTPException(status_code=422, detail=validation_error.errors())
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(data, "job_name", None) or "Google Drive Connection Validation",
                "service_name": "Google Drive",
                "is_terminal_failure": False,
            },
        )
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/GoogleDrive-listing-files")
async def google_drive_listing_files(data: GoogleDriveListingTables):
    try:
        return {"result": GoogleDriveSnowflakeService.list_files(data)}
    except HTTPException:
        raise
    except ValidationError as validation_error:
        raise HTTPException(status_code=422, detail=validation_error.errors())
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(data, "job_name", None)
                or "Google Drive File Listing",
                "log_id": getattr(data, "log_id", None),
                "cmp_id": getattr(data, "cmp_id", None),
                "connection_id": getattr(data, "connection_id", None),
                "user_id": getattr(data, "user_id", None),
                "operation": "LIST_FILES",
                "is_terminal_failure": False,
                "service_name": "Google Drive",
            },
        )
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/GoogleDrive-listing-tables")
async def google_drive_listing_tables(data: GoogleDriveListingTables):
    try:
        result = GoogleDriveSnowflakeService.list_tables_with_schema(data)
        try:
            db_result = GoogleDriveSnowflakeService.refresh_and_update_google_drive_tables_in_database(
                connection_id=data.connection_id,
                tables_result=result,
                job_name=getattr(data, "job_name", None),
                migration_session_id=getattr(data, "migration_session_id", None),
            )
            result["tables_written"] = db_result.get("tables_written", 0)
            result["tables_deleted"] = db_result.get("tables_deleted", 0)
        except Exception as db_exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Update schema failed while writing Google Drive tables "
                    f"to database: {db_exc}"
                ),
            )
        return {"result": result}
    except HTTPException as http_error:
        handle_update_schema_listing_error(
            http_error,
            data,
            service_name="Google Drive",
        )
        raise http_error
    except ValidationError as validation_error:
        raise HTTPException(status_code=422, detail=validation_error.errors())
    except Exception as e:
        handle_update_schema_listing_error(
            e,
            data,
            service_name="Google Drive",
        )
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/GoogleDrive-preview-pattern")
async def google_drive_preview_pattern(data: GoogleDrivePatternPreviewRequest):
    try:
        return GoogleDriveSnowflakeService.preview_pattern(data)
    except HTTPException:
        raise
    except ValidationError as validation_error:
        raise HTTPException(status_code=422, detail=validation_error.errors())
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(data, "job_name", None)
                or "Google Drive Pattern Preview",
                "log_id": getattr(data, "log_id", None),
                "cmp_id": getattr(data, "cmp_id", None),
                "connection_id": getattr(data, "connection_id", None),
                "user_id": getattr(data, "user_id", None),
                "operation": "PREVIEW_PATTERN",
                "is_terminal_failure": False,
                "service_name": "Google Drive",
            },
        )
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/GoogleDrive-suggest-primary-keys")
async def google_drive_suggest_primary_keys(data: GoogleDrivePKSuggestionRequest):
    try:
        return await GoogleDriveSnowflakeService.suggest_primary_keys(data)
    except HTTPException:
        raise
    except ValidationError as validation_error:
        raise HTTPException(status_code=422, detail=validation_error.errors())
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(data, "job_name", None)
                or "Google Drive PK Suggestion",
                "log_id": None,
                "cmp_id": getattr(data, "cmp_id", None),
                "connection_id": getattr(data, "connection_id", None),
                "user_id": getattr(data, "user_id", None),
                "operation": "SUGGEST_PRIMARY_KEYS",
                "is_terminal_failure": False,
                "service_name": "Google Drive",
            },
        )
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/Snowflake-GoogleDrive-migrate")
async def snowflake_google_drive_migrate(request: Request):
    migration_request = None
    try:
        body = await request.json()
        migration_request = GoogleDriveMigrationRequest(**body)
        result = sync_snowflake_to_google_drive(migration_request)
        return result
    except HTTPException:
        raise
    except ValidationError as validation_error:
        raise HTTPException(status_code=422, detail=validation_error.errors())
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(migration_request, "job_name", None) or "Google Drive Migration",
                "log_id": getattr(migration_request, "log_id", None),
                "cmp_id": getattr(migration_request, "cmp_id", None),
                "connection_id": getattr(migration_request, "connection_id", None),
                "user_id": getattr(migration_request, "user_id", None),
                "operation": "MIGRATION",
                "is_terminal_failure": True,
                "service_name": "Google Drive",
            },
        )
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/GoogleDrive-Snowflake-migrate")
async def google_drive_snowflake_migrate(
    request: GoogleDriveSnowflakeMigrationRequest,
):
    """Migrate files from Google Drive to Snowflake."""
    try:
        return await GoogleDriveSnowflakeService.migrate(request)
    except HTTPException as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(request, "job_name", None)
                or "Google Drive to Snowflake Migration",
                "log_id": getattr(request, "log_id", None),
                "cmp_id": getattr(request, "cmp_id", None),
                "connection_id": getattr(request, "connection_id", None),
                "user_id": getattr(request, "user_id", None),
                "operation": "MIGRATION",
                "is_terminal_failure": True,
                "successful_tables": 0,
                "total_tables": len(request.selected_tables)
                if request.selected_tables
                else 0,
                "total_rows": 0,
                "session": getattr(request, "migration_session_id", None),
                "migration_session_id": getattr(
                    request, "migration_session_id", None
                ),
                "service_name": "Google Drive to Snowflake",
            },
        )
        raise
    except ValidationError as validation_error:
        raise HTTPException(status_code=422, detail=validation_error.errors())
    except Exception as e:
        handle_service_error(
            error=e,
            context={
                "job_name": getattr(request, "job_name", None)
                or "Google Drive to Snowflake Migration",
                "log_id": getattr(request, "log_id", None),
                "cmp_id": getattr(request, "cmp_id", None),
                "connection_id": getattr(request, "connection_id", None),
                "user_id": getattr(request, "user_id", None),
                "operation": "MIGRATION",
                "is_terminal_failure": True,
                "successful_tables": 0,
                "total_tables": len(request.selected_tables)
                if request.selected_tables
                else 0,
                "total_rows": 0,
                "service_name": "Google Drive to Snowflake",
            },
        )
        raise HTTPException(status_code=422, detail=str(e))
