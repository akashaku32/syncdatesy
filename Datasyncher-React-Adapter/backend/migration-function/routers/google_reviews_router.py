from fastapi import APIRouter, HTTPException, Request
import urllib.parse
import json
from datetime import datetime

from services.google_reviews.as_source.to_snowflake import (
    get_google_reviews_authorize_url,
    get_access_token,
    sync_google_reviews_to_snowflake,
    list_google_reviews_tables,
)
from utils.database_utils import get_last_checked_dates_and_fields, update_table_status_snowflake
from models.google_reviews_models import (
    GoogleReviewsMigration,
    GoogleReviewsValidationRequest,
)

router = APIRouter()


@router.get("/Source-GoogleReviews-Authorization")
def authorize_google_reviews(state: str, data: str):
    try:
        decoded_data = urllib.parse.unquote(data)
        parsed = json.loads(decoded_data)
        auth_url = get_google_reviews_authorize_url(state, parsed, is_source=True)
        return {"auth_url": auth_url}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")


@router.get("/source/googlereviews/callback")
def google_reviews_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    encoded_data = request.query_params.get("data_new")
    try:
        data = json.loads(urllib.parse.unquote(encoded_data)) if encoded_data else {}
        token = get_access_token(code, state, data)
        return token
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format in 'data'.")


@router.post("/GoogleReviews-Authorization")
async def validate_google_reviews_connection(data: GoogleReviewsValidationRequest):

    print(type(data.GoogleReviews), data.GoogleReviews)  # remove after stabilization

    raw_details = data.GoogleReviews

    # Defensive guard (rarely triggered with strict model, but safe)
    if isinstance(raw_details, str):
        try:
            raw_details = json.loads(raw_details)
            print("INFO: Parsed stringified GoogleReviews JSON")
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="GoogleReviews must be a valid JSON object"
            )

    if raw_details is None:
        details = {}
    elif isinstance(raw_details, dict):
        details = raw_details
    else:
        raise HTTPException(
            status_code=400,
            detail=f"GoogleReviews must be an object, got {type(raw_details).__name__}"
        )

    if not details.get("access_token"):
        raise HTTPException(status_code=400, detail="access_token is required")

    try:
        from services.google_reviews.as_source.to_snowflake import list_accounts_with_refresh
        _ = list_accounts_with_refresh(details)

        # Check if token was refreshed during validation
        original_token = raw_details.get("access_token") if isinstance(raw_details, dict) else (json.loads(raw_details).get("access_token") if isinstance(raw_details, str) else None)
        current_token = details.get("access_token")
        
        response_body = "Google Reviews connection validated"
        updated_token = None
        
        if current_token and original_token and current_token != original_token:
            print("INFO: Token was refreshed during validation")
            updated_token = current_token
            response_body = "Google Reviews connection validated and token refreshed"

        return {
            "status_code": 200,
            "body": response_body,
            "updated_access_token": updated_token
        }

    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': 'Google Reviews Connection Validation',
                'service_name': 'Google Reviews',
                'is_terminal_failure': False
            }
        )
        raise e


@router.post("/GoogleReviews-listing-tables")
async def google_reviews_listing_tables(request: Request):
    """List available tables/entities from Google Reviews API"""
    import time
    start_time = time.time()
    print("DEBUG: Starting Google Reviews table listing")
    body = await request.json()
    print(f"DEBUG: Table listing request body: {json.dumps(body, indent=2)}")
    
    # Extract Google config from the request body structure (handle both old and new formats)
    google = body.get('source_config', {}) or body.get('GoogleReviews', {})
    print(f"DEBUG: Google config keys: {list(google.keys()) if google else 'None'}")
    
    # Extract connection_id for delta field detection 
    connection_id = body.get('connection_id')
    print(f"DEBUG: connection_id: {connection_id}")
    
    # Check for access_token specifically
    if 'access_token' not in google or not google.get('access_token'):
        print("DEBUG: ERROR - No access_token found in Google configuration for table listing!")
        raise HTTPException(status_code=401, detail="access_token is required for table listing")
    
    print("DEBUG: Access token found - authentication appears valid for table listing")
    
    try:
        # Extract job_name from the request body to identify this as a listing_tables operation
        job_name = body.get('job_name')
        result = list_google_reviews_tables(google, connection_id=connection_id, job_name=job_name)
        print(f"DEBUG: Table listing result: {json.dumps(result, indent=2)}")
        return {"result": result}
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': body.get('job_name'),
                'log_id': body.get('log_id'),
                'cmp_id': body.get('cmp_id'),
                'connection_id': connection_id,
                'user_id': body.get('user_id'),
                'operation': 'UPDATE_SCHEMA',
                'is_terminal_failure': True,
                'execution_time': time.time() - start_time,
                'total_tables': 0,
                'successful_tables': 0,
                'total_rows': 0,
                'service_name': 'Google Reviews'
            }
        )
        raise e


@router.post("/Snowflake-GoogleReviews-migrate")
async def google_reviews_to_snowflake(request: Request):
    """Migrate data from Google Reviews to Snowflake"""
    print("DEBUG: Starting google_reviews_to_snowflake migration")
    body = await request.json()
    print(f"DEBUG: Request body received: {json.dumps(body, indent=2)}")
    
    start_time = datetime.now()
    
    migration = GoogleReviewsMigration(**body)
    google = migration.source_config
    sf = migration.destination_config
    
    print(f"DEBUG: Google config keys: {list(google.keys()) if google else 'None'}")
    print(f"DEBUG: Snowflake config keys: {list(sf.keys()) if sf else 'None'}")
    print(f"DEBUG: Google config values (excluding sensitive data): {({k: v for k, v in google.items() if k not in ['access_token', 'refresh_token', 'CLIENT_SECRET', 'client_secret']}) if google else 'None'}")
    
    # Check for access_token specifically
    if 'access_token' not in google or not google.get('access_token'):
        print("DEBUG: WARNING - No access_token found in Google configuration!")
        print("DEBUG: Available Google config keys:", list(google.keys()) if google else "None")
        print("DEBUG: This suggests the Google Reviews connection hasn't been properly authenticated")
        print("DEBUG: Expected Google config should include: access_token, refresh_token, CLIENT_ID, CLIENT_SECRET")
        print("DEBUG: Please complete the Google Reviews authorization flow first:")
        print("DEBUG: 1. Call /Source-GoogleReviews-Authorization to get auth URL")
        print("DEBUG: 2. Complete OAuth flow and get callback with code")
        print("DEBUG: 3. Use /source/googlereviews/callback to exchange code for tokens")
        print("DEBUG: 4. Then retry the migration with the access_token")
    else:
        print("DEBUG: Access token found in source_config - authentication appears valid")
    
    try:
        # Extract migration parameters from migration object
        start_date = migration.start_date
        end_date = migration.end_date 
        safety_interval = migration.safety_interval or "120"
        connection_id = migration.connection_id
        job_name = migration.job_name
        migration_session_id = migration.migration_session_id
        drop_and_reload = migration.drop_and_reload  # Flag for drop and reload operation 
        log_id = migration.log_id
        
        print(f"DEBUG: Migration parameters - start_date: {start_date}, end_date: {end_date}, safety_interval: {safety_interval}")
        print(f"DEBUG: drop_and_reload flag: {drop_and_reload}")
        
        # CRITICAL: If drop_and_reload is True, do NOT auto-fill start_date
        # This ensures full reload, not incremental sync
        if drop_and_reload:
            print("DEBUG: drop_and_reload=True - will NOT auto-fill start_date (forcing full reload)")
            start_date = None  # Explicitly set to None to ensure full reload
        elif not start_date and connection_id:
            print(f"DEBUG: No start_date provided, checking last_checked for connection_id: {connection_id}")
            last_checked_map = get_last_checked_dates_and_fields(connection_id)
            print(f"DEBUG: Last checked map: {last_checked_map}")
            # Choose the most recent last_checked_date across tables (or any available)
            last_dates = [v.get("last_checked_date") for v in last_checked_map.values() if isinstance(v, dict)]
            # Convert to isoformat string if datetime
            if last_dates:
                last_dates = [d for d in last_dates if d]
                if last_dates:
                    # pick the max last synced date
                    d = max(last_dates)
                    try:
                        start_date = d.isoformat() if hasattr(d, 'isoformat') else str(d)
                        print(f"DEBUG: Using last_checked_date as start_date: {start_date}")
                    except Exception:
                        start_date = str(d)
                        print(f"DEBUG: Using last_checked_date as start_date (fallback): {start_date}")
        # Extract selected tables and drop_and_reload flag
        selected_tables = migration.selected_tables or []
        drop_and_reload = migration.drop_and_reload  # Extract drop_and_reload flag
        cmp_id = migration.cmp_id
        
        print(f"DEBUG: Calling sync_google_reviews_to_snowflake with final parameters")
        print(f"DEBUG: - start_date: {start_date}")
        print(f"DEBUG: - end_date: {end_date}")
        print(f"DEBUG: - safety_interval: {safety_interval}")
        print(f"DEBUG: - selected_tables: {selected_tables}")
        print(f"DEBUG: - drop_and_reload: {drop_and_reload}")
        print(f"DEBUG: - connection_id: {connection_id}")
        print(f"DEBUG: - cmp_id: {cmp_id}")
        
        result = sync_google_reviews_to_snowflake(
            google,
            sf,
            start_date=start_date,
            end_date=end_date,
            safety_interval=safety_interval,
            selected_tables=selected_tables,
            # Error logging parameters
            cmp_id=migration.cmp_id,
            connection_id=connection_id,
            user_id=migration.user_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            session=migration.session,
            logs_creation=migration.logs_creation,
            connection_name=migration.connection_name,
            company_name=migration.company_name,
            drop_and_reload=drop_and_reload,  # Pass drop_and_reload flag 
            log_id=log_id
        )
        
        print(f"DEBUG: Sync result received: {json.dumps(result, indent=2)}")
        
        # Persist updated access token if refreshed
        if result.get("updated_access_token"):
            print("DEBUG: Access token was refreshed during sync")
            google["access_token"] = result["updated_access_token"]
        # Update last_synced watermark for this source/connection
        job_name = migration.job_name
        migration_session_id = migration.migration_session_id
        
        if connection_id and job_name and migration_session_id:
            print(f"DEBUG: Updating table status for connection_id: {connection_id}")
            from utils.websocket_utils import (
                trigger_ws_status_update,
                trigger_connection_ws_status_update,
            )
            # Use end_date if provided, otherwise now
            sync_end_time = None
            try:
                if end_date:
                    # Ensure proper datetime
                    sync_end_time = datetime.fromisoformat(end_date)
                    print(f"DEBUG: Using end_date as sync_end_time: {sync_end_time}")
            except Exception:
                sync_end_time = None
                print("DEBUG: Failed to parse end_date, will use current time")
            if sync_end_time is None:
                sync_end_time = datetime.utcnow()
                print(f"DEBUG: Using current time as sync_end_time: {sync_end_time}")
            
            # Update status for all selected tables
            for table_name in selected_tables:
                print(f"DEBUG: Updating status for table: {table_name}")
                try:
                    update_table_status_snowflake(
                        connection_id,
                        table_name,
                        "completed",
                        job_name,
                        migration_session_id,
                        sync_end_time=sync_end_time,
                    )
                    # Explicitly push WS after each table status change so UI updates immediately.
                    trigger_ws_status_update(migration_session_id)
                    trigger_connection_ws_status_update(connection_id)
                    print(f"DEBUG: Successfully updated status for {table_name}")
                except Exception as e:
                    print(f"DEBUG: ERROR updating status for {table_name}: {str(e)}")
                    # Continue with other tables even if one fails
            # One final sync push after all table updates.
            trigger_ws_status_update(migration_session_id)
            trigger_connection_ws_status_update(connection_id)
            print("DEBUG: All table status updates completed")
        else:
            print("DEBUG: Skipping table status update - missing connection_id, job_name, or migration_session_id")
        
        print("DEBUG: Migration completed successfully")
        return {"result": result}
    except Exception as e:
        print(f"DEBUG: Error in google_reviews_to_snowflake: {str(e)}")
        print(f"DEBUG: Exception type: {type(e).__name__}")
        import traceback
        print(f"DEBUG: Full traceback: {traceback.format_exc()}")
        
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(migration, 'job_name', 'Google Reviews Migration'),
                'log_id': getattr(migration, 'log_id', None),
                'cmp_id': getattr(migration, 'cmp_id', None),
                'connection_id': connection_id,
                'user_id': getattr(migration, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'successful_tables': 0,
                'total_tables': len(migration.selected_tables) if hasattr(migration, 'selected_tables') and migration.selected_tables else 0,
                'total_rows': 0,
                'service_name': 'Google Reviews'
            }
        )
        raise e


