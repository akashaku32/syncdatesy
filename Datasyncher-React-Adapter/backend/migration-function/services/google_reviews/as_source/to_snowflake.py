import os
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
import base64
import time
import random
import traceback
from pathlib import Path

import pandas as pd
import requests
import snowflake.connector
import psycopg2
import csv
from google.cloud import storage
from google.api_core.exceptions import ServiceUnavailable
from google.api_core.retry import Retry
from sqlalchemy import create_engine, text
from urllib.parse import quote

# Import error handling utilities
from utils.error_utils import (
    log_error_to_tool_log,
    log_error_to_files,
    handle_service_error,
    create_error_log_entry,
    log_migration_completion,
    log_migration_failure
)

# Import database utilities
from utils.database_utils import postgres_connection, data_transfer_logs
from utils.error_utils import insert_tool_log

# Import cleanup utility
from utils.celery_cleanup_utils import handle_migration_completion
# Import progress tracking utility
from utils.simple_progress_tracker import SimpleProgressTracker

# Reuse Snowflake key-pair utilities (including encrypted key handling)
from services.snowflake.service import SnowflakeService, normalize_pem_key

# Import for SQLAlchemy errors and key-pair authentication
from sqlalchemy.exc import SQLAlchemyError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from fastapi import HTTPException

from utils.deployment_config import get_api_redirect_uri

# Google Reviews API Constants
GOOGLE_REVIEWS_CALLBACK_PATH = "/api/v1/source/googlereviews/callback/"
GOOGLE_REVIEWS_SCOPE = 'https://www.googleapis.com/auth/business.manage'


def _google_reviews_redirect_uri() -> str:
    return get_api_redirect_uri(GOOGLE_REVIEWS_CALLBACK_PATH)

# GCS and Snowflake Integration Constants 
DEMO_STORAGE = 'DATASYNCHERDEV'
GCS_BUCKET_URL = 'gcs://meta_data_bucket_ds_development/'
BUCKET_NAME = "meta_data_bucket_ds_development"
PROJECT_ID = "datasyncher-react-qa-dev"

# Google Reviews Table Field Structures (used for delta field detection)
# Field names match database delta field configuration (mixed case)
# Note: Actual Snowflake columns will be UPPERCASE after normalization
GOOGLE_REVIEWS_TABLE_FIELDS = {
    'GOOGLE_ACCOUNTS': {
        'name': 'VARCHAR(255)',
        'accountName': 'VARCHAR(255)',
        'type': 'VARCHAR(100)',
        'verificationState': 'VARCHAR(50)',
        'vettedState': 'VARCHAR(50)',
        'role': 'VARCHAR(50)',
        'accountNumber': 'VARCHAR(50)',
        'permissionLevel': 'VARCHAR(50)'
    },
    'GOOGLE_LOCATIONS': {
        'name': 'VARCHAR(255)',
        'title': 'VARCHAR(255)',
        'labels': 'TEXT',
        'account_id': 'VARCHAR(255)',
        'openInfo.status': 'VARCHAR(50)',
        'metadata.place_id': 'VARCHAR(255)',
        'phone_numbers.primary_phone': 'VARCHAR(50)',
        'storefront_address.locality': 'VARCHAR(100)',
        'storefront_address.postal_code': 'VARCHAR(20)',
        'storefront_address.region_code': 'VARCHAR(10)',
        'storefront_address.administrative_area': 'VARCHAR(100)'
    },
    'GOOGLE_REVIEWS': {
        'name': 'VARCHAR(255)',
        'comment': 'TEXT',
        'reviewId': 'VARCHAR(255)',
        'reviewer': 'TEXT',
        'createTime': 'TIMESTAMP_NTZ',
        'starRating': 'VARCHAR(10)',
        'updateTime': 'TIMESTAMP_NTZ',
        'RatingValue': 'INTEGER',
        'reviewReply': 'TEXT',
        'locationName': 'VARCHAR(255)'
    }
}


# ============================================================================
# DELTA FIELD DETECTION FUNCTIONS 
# ============================================================================

def get_delta_fields_for_source(source_id):
    """
    Fetch delta fields from source_delta_table for a given source_id.
    
    Args:
        source_id (int): The source ID (e.g., for Google Reviews)
    
    Returns:
        list: List of delta field names for the source
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT delta_fields 
            FROM source_delta_table 
            WHERE source_id = %s
        """, (source_id,))
        
        result = cursor.fetchone()
        if result and result[0]:
            # Parse the delta_fields JSON if it's stored as JSON
            delta_fields = result[0] if isinstance(result[0], list) else json.loads(result[0])
            return delta_fields
        return []
        
    except Exception as e:
        print(f"Error fetching delta fields for source {source_id}: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_source_id_from_connection(connection_id):
    """
    Get src_id from connection_id by following:
    connection -> source_config -> source_model
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT sm.src_id
            FROM connection tc
            JOIN source_config sc ON tc.src_config_id = sc.src_config_id
            JOIN source_model sm ON sc.src_id = sm.src_id
            WHERE tc.connection_id = %s
        """, (connection_id,))
        
        result = cursor.fetchone()
        return result[0] if result else None

    except Exception as e:
        print(f"Error fetching src_id for connection {connection_id}: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def check_table_has_delta_fields(table_fields, delta_fields):
    """
    Check if a table contains any of the specified delta fields.
    
    Args:
        table_fields (dict): Dictionary of table field names and their types
        delta_fields (list): List of delta field names to check for
    
    Returns:
        bool: True if table contains any delta fields, False otherwise
    """
    if not table_fields or not delta_fields:
        return False
    
    # Convert table_fields to dict if it's a JSON string
    if isinstance(table_fields, str):
        try:
            table_fields = json.loads(table_fields)
        except (json.JSONDecodeError, ValueError):
            return False
    
    # Check if any delta field exists in the table fields
    return any(delta_field in table_fields for delta_field in delta_fields)


# ============================================================================
# SNOWFLAKE CONNECTION FUNCTION
# ============================================================================

def destination_connection_function(destination_connection):
    """
    Establishes a connection to Snowflake using SQLAlchemy and returns the engine.
    Creates the schema if it doesn't exist.
    """
    from datasyncher_connections.snowflake.sqlalchemy_engine import create_snowflake_sqlalchemy_engine

    print("DEBUG: Destination connection config ->", destination_connection)
    authentication_type = destination_connection.get("authentication_type", "password").lower()
    password = destination_connection.get("password")
    private_key_str = destination_connection.get("private_key")

    if authentication_type == "key_pair" and not private_key_str:
        raise HTTPException(status_code=400, detail="Private key is required for key-pair authentication.")
    if authentication_type == "password" and not password:
        raise HTTPException(status_code=400, detail="Password is required for password authentication.")
    if authentication_type not in ("password", "key_pair") or (
        authentication_type == "key_pair" and not private_key_str
    ) or (authentication_type == "password" and not password):
        raise HTTPException(
            status_code=400,
            detail=(
                "Either password or private key must be provided for authentication. "
                f"Authentication type: {authentication_type}"
            ),
        )

    try:
        engine, _schema = create_snowflake_sqlalchemy_engine(
            destination_connection,
            flavor="google_reviews",
            ensure_schema=True,
        )
        print("✅ Connection established successfully.")
        return engine
    except SQLAlchemyError as e:
        print(f"❌ SQLAlchemy error: {str(e) or repr(e)}")
        raise
    except Exception as e:
        print(f"❌ Unexpected error: {str(e) or repr(e)}")
        raise


# ============================================================================
# GOOGLE REVIEWS OAUTH AND API FUNCTIONS
# ============================================================================

def get_google_reviews_authorize_url(state: str, data: Dict[str, Any], is_source: bool = True) -> str:
    client_id = data.get('CLIENT_ID')
    # Use constants instead of user input
    redirect_uri = _google_reviews_redirect_uri()
    scope = GOOGLE_REVIEWS_SCOPE

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': scope,
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def get_access_token(code: str, state: str, data: Dict[str, Any]) -> Dict[str, Any]:
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        'code': code,
        'client_id': data.get('CLIENT_ID'),
        'client_secret': data.get('CLIENT_SECRET'),
        'redirect_uri': _google_reviews_redirect_uri(),
        'grant_type': 'authorization_code'
    }
    resp = requests.post(token_url, data=payload)
    resp.raise_for_status()
    tokens = resp.json()
    
    # Calculate token expiration time
    expires_in = tokens.get('expires_in', 3600)  # Default to 1 hour
    token_expires_at = int(time.time()) + expires_in
    
    return {
        'access_token': tokens.get('access_token'),
        'refresh_token': tokens.get('refresh_token'),
        'expires_in': expires_in,
        'token_expires_at': token_expires_at,
        'token_type': tokens.get('token_type'),
        'state': state
    }


def refresh_token(refresh_token: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token'
    }
    
    print(f"DEBUG: Refresh token request to {token_url}")
    print(f"DEBUG: Payload keys: {list(payload.keys())}")
    print(f"DEBUG: Client ID: {client_id[:10]}..." if client_id else "DEBUG: Client ID: None")
    print(f"DEBUG: Refresh token: {refresh_token[:10]}..." if refresh_token else "DEBUG: Refresh token: None")
    
    resp = requests.post(token_url, data=payload)
    
    print(f"DEBUG: Refresh token response status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"DEBUG: Refresh token response headers: {dict(resp.headers)}")
        print(f"DEBUG: Refresh token response body: {resp.text}")
        
        # Try to parse error details
        try:
            error_data = resp.json()
            print(f"DEBUG: Parsed error response: {error_data}")
            if 'error' in error_data:
                error_type = error_data.get('error')
                error_desc = error_data.get('error_description', 'No description provided')
                print(f"DEBUG: OAuth error type: {error_type}")
                print(f"DEBUG: OAuth error description: {error_desc}")
                
                # Raise specific exception for invalid_grant (expired/revoked refresh token)
                if error_type == 'invalid_grant':
                    raise ValueError(
                        f"Refresh token has expired or been revoked. "
                        f"Please re-authenticate your Google account through the OAuth flow. "
                        f"Error: {error_desc}"
                    )
        except ValueError:
            # Re-raise ValueError (invalid_grant) as-is
            raise
        except Exception as e:
            print(f"DEBUG: Could not parse error response as JSON: {e}")
    
    resp.raise_for_status()
    tokens = resp.json()
    
    # Calculate token expiration time
    expires_in = tokens.get('expires_in', 3600)  # Default to 1 hour
    tokens['token_expires_at'] = int(time.time()) + expires_in
    
    return tokens


def _print_reauth_instructions():
    """Print detailed re-authentication instructions"""
    print("\n" + "="*80)
    print("🔧 GOOGLE OAUTH RE-AUTHENTICATION REQUIRED")
    print("="*80)
    print("Your Google OAuth tokens have expired. Follow these steps to fix:")
    print()
    print("1. 📋 PREPARE YOUR CREDENTIALS:")
    print("   - Get your CLIENT_ID and CLIENT_SECRET from Google Cloud Console")
    print("   - Go to: https://console.cloud.google.com/apis/credentials")
    print()
    print("2. 🔗 GET AUTHORIZATION URL:")
    print("   - Call: GET /Source-GoogleReviews-Authorization")
    print("   - Include your CLIENT_ID in the request")
    print()
    print("3. 🌐 COMPLETE OAUTH FLOW:")
    print("   - Visit the authorization URL in your browser")
    print("   - Grant permissions to your application")
    print("   - You'll be redirected with an authorization code")
    print()
    print("4. 🔑 EXCHANGE CODE FOR TOKENS:")
    print("   - Call: GET /source/googlereviews/callback")
    print("   - Include the authorization code from step 3")
    print()
    print("5. ✅ UPDATE YOUR CONFIGURATION:")
    print("   - Use the new access_token and refresh_token")
    print("   - Retry your migration")
    print()
    print("📚 REQUIRED GOOGLE APIS:")
    print("   - Google My Business Account Management API")
    print("   - Google My Business Business Information API") 
    print("   - Google My Business API (for reviews)")
    print()
    print("🔍 REQUIRED OAUTH SCOPES:")
    print("   - https://www.googleapis.com/auth/business.manage")
    print("="*80 + "\n")


def _validate_oauth_config(google: Dict[str, Any]) -> List[str]:
    """
    Validate OAuth configuration and return list of issues found.
    
    Returns:
        List of validation error messages, empty if no issues found
    """
    issues = []
    
    # Check required fields
    access_token = google.get('access_token')
    refresh_token = google.get('refresh_token')
    client_id = google.get('CLIENT_ID') or google.get('client_id')
    client_secret = google.get('CLIENT_SECRET') or google.get('client_secret')
    
    if not access_token:
        issues.append("Missing access_token - complete OAuth authorization flow")
    elif not access_token.startswith('ya29.'):
        issues.append("Invalid access_token format - should start with 'ya29.'")
    
    if not refresh_token:
        issues.append("Missing refresh_token - ensure OAuth flow includes offline access")
    elif len(refresh_token) < 20:
        issues.append("Refresh token appears too short - may be invalid")
    
    if not client_id:
        issues.append("Missing CLIENT_ID - check OAuth app configuration")
    elif not client_id.endswith('.apps.googleusercontent.com'):
        issues.append("Invalid CLIENT_ID format - should end with '.apps.googleusercontent.com'")
    
    if not client_secret:
        issues.append("Missing CLIENT_SECRET - check OAuth app configuration")
    elif len(client_secret) < 20:
        issues.append("CLIENT_SECRET appears too short - may be invalid")
    
    # Check token expiration if available
    token_expires_at = google.get('token_expires_at')
    if token_expires_at:
        current_time = int(time.time())
        if current_time > token_expires_at:
            issues.append(f"Access token expired at {datetime.fromtimestamp(token_expires_at).isoformat()}")
    
    return issues


def _is_token_expired(google: Dict[str, Any], buffer_seconds: int = 300) -> bool:
    """
    Check if the access token is expired or will expire soon.
    
    Args:
        google: Dictionary containing token information
        buffer_seconds: Number of seconds before expiration to consider token as expired (default: 5 minutes)
    
    Returns:
        True if token is expired or will expire soon, False otherwise
    """
    token_expires_at = google.get('token_expires_at')
    if not token_expires_at:
        # If we don't have expiration info, assume it might be expired
        print("DEBUG: No token expiration info available, assuming token might be expired")
        return True
    
    current_time = int(time.time())
    expires_soon = current_time >= (token_expires_at - buffer_seconds)
    
    if expires_soon:
        expires_at_readable = datetime.fromtimestamp(token_expires_at).isoformat()
        print(f"DEBUG: Token expires at {expires_at_readable}, current time: {datetime.fromtimestamp(current_time).isoformat()}")
        print("DEBUG: Token is expired or will expire soon")
    else:
        expires_at_readable = datetime.fromtimestamp(token_expires_at).isoformat()
        print(f"DEBUG: Token is valid until {expires_at_readable}")
    
    return expires_soon


def _extract_google_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return resp.text
    if isinstance(payload, dict):
        # V2 style: { error: { code, message, status, details: [...] } }
        if 'error' in payload and isinstance(payload['error'], dict):
            err = payload['error']
            msg = err.get('message') or ''
            status = err.get('status') or ''
            code = err.get('code') or ''
            return f"Google API error (code={code}, status={status}): {msg}"
        # V1 style fields
        if 'message' in payload:
            return str(payload.get('message'))
    return resp.text


def _maybe_refresh_access_token(google: Dict[str, Any]) -> Optional[str]:
    refresh_tok = google.get('refresh_token')
    client_id = google.get('CLIENT_ID') or google.get('client_id')
    client_secret = google.get('CLIENT_SECRET') or google.get('client_secret')
    
    print(f"DEBUG: Checking refresh token credentials...")
    print(f"DEBUG: - refresh_token present: {'Yes' if refresh_tok else 'No'}")
    print(f"DEBUG: - client_id present: {'Yes' if client_id else 'No'}")
    print(f"DEBUG: - client_secret present: {'Yes' if client_secret else 'No'}")
    print(f"DEBUG: - Available google config keys: {list(google.keys())}")
    
    if not (refresh_tok and client_id and client_secret):
        print("DEBUG: Cannot refresh token - missing refresh_token, client_id, or client_secret")
        if not refresh_tok:
            print("DEBUG: Missing refresh_token - this is required for token refresh")
        if not client_id:
            print("DEBUG: Missing client_id - check CLIENT_ID or client_id in config")
        if not client_secret:
            print("DEBUG: Missing client_secret - check CLIENT_SECRET or client_secret in config")
        return None
    
    try:
        print("DEBUG: Attempting to refresh access token...")
        tokens = refresh_token(refresh_tok, client_id, client_secret)
        new_access = tokens.get('access_token')
        if new_access:
            google['access_token'] = new_access
            # Update expiration time if available
            if 'token_expires_at' in tokens:
                google['token_expires_at'] = tokens['token_expires_at']
            # Persist refreshed credentials if a callback is provided
            try:
                persist_cb = google.get('on_token_refresh') or google.get('persist_token')
                if callable(persist_cb):
                    persist_payload = {
                        'access_token': new_access,
                        'refresh_token': tokens.get('refresh_token') or google.get('refresh_token'),
                        'token_expires_at': tokens.get('token_expires_at') or google.get('token_expires_at'),
                    }
                    persist_cb(persist_payload)
                    print("DEBUG: Persisted refreshed Google tokens via provided callback")
            except Exception as e:
                print(f"DEBUG: Failed to persist refreshed tokens: {str(e)}")
            print("DEBUG: Access token refreshed successfully")
            return new_access
        else:
            print("DEBUG: Token refresh response did not contain access_token")
            return None
    except ValueError as e:
        # Re-raise ValueError (invalid_grant) to propagate the clear error message
        print(f"DEBUG: Token refresh failed with invalid_grant: {str(e)}")
        raise
    except Exception as e:
        print(f"DEBUG: Token refresh failed: {str(e)}")
        # Check if it's a 400 error which might indicate invalid refresh token
        if "400" in str(e) and "Bad Request" in str(e):
            print("DEBUG: 400 Bad Request during token refresh - refresh token may be expired or invalid")
            print("DEBUG: User needs to re-authenticate through the OAuth flow")
        return None


def _api_call_with_refresh(method: str, url: str, google: Dict[str, Any], *,
                           headers: Optional[Dict[str, str]] = None,
                           params: Optional[Dict[str, Any]] = None,
                           json_body: Optional[Dict[str, Any]] = None) -> requests.Response:
    access_token = google.get('access_token')
    if not access_token:
        raise ValueError("access_token is required for API calls")
    
    # Proactively check if token is expired and refresh if needed
    if _is_token_expired(google):
        print("DEBUG: Token is expired or will expire soon, attempting proactive refresh...")
        try:
            new_access = _maybe_refresh_access_token(google)
            if new_access:
                print("DEBUG: Proactive token refresh successful")
                access_token = new_access
            else:
                print("DEBUG: Proactive token refresh failed, will try API call anyway")
        except ValueError as e:
            # Refresh token is invalid (expired/revoked) - raise clear error immediately
            print(f"DEBUG: Proactive token refresh failed - refresh token is invalid: {str(e)}")
            raise RuntimeError(str(e))
    
    if headers is None:
        headers = {}
    # Ensure Authorization header present
    local_headers = {**headers, 'Authorization': f'Bearer {access_token}'}

    def _request() -> requests.Response:
        if method.upper() == 'GET':
            return requests.get(url, headers=local_headers, params=params)
        if method.upper() == 'POST':
            return requests.post(url, headers=local_headers, params=params, json=json_body)
        raise ValueError('Unsupported HTTP method')

    resp = _request()
    if resp.status_code == 401:
        print("DEBUG: Received 401 Unauthorized - attempting token refresh...")
        # Try refresh once
        try:
            new_access = _maybe_refresh_access_token(google)
            if new_access:
                print("DEBUG: Token refresh successful, retrying API call...")
                local_headers['Authorization'] = f'Bearer {new_access}'
                resp = _request()
                if resp.status_code == 401:
                    print("DEBUG: Still getting 401 after token refresh - authentication failed")
            else:
                print("DEBUG: Token refresh failed - cannot retry API call")
                # Add helpful error message to response for better debugging
                if resp.status_code == 401:
                    error_msg = "Authentication failed: Access token expired and refresh token is invalid. Please re-authenticate through the OAuth flow."
                    print(f"DEBUG: {error_msg}")
        except ValueError as e:
            # Refresh token is invalid (expired/revoked) - raise clear error
            print(f"DEBUG: Refresh token is invalid: {str(e)}")
            raise RuntimeError(str(e))
    return resp


def _fetch_all_pages(
    url: str,
    google: Dict[str, Any],
    resource_key: str,
    params: Optional[Dict[str, Any]] = None,
    method: str = 'GET'
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Generic helper to fetch all pages from a Google Business API GET endpoint with retries.
    
    Args:
        url: The API endpoint URL
        google: Google configuration with tokens
        resource_key: The key in the JSON response containing the list of items (e.g., 'accounts', 'locations')
        params: Optional query parameters
        method: HTTP method (default: 'GET')
        
    Returns:
        (all_items, full_fetch_success)
    """
    all_items = []
    page_token = None
    full_fetch_success = True
    page_count = 0
    
    _MAX_PAGE_RETRIES = 3
    _RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
    _FAIL_FAST_STATUSES = {400, 401, 403, 404}

    while True:
        page_count += 1
        current_params = {**(params or {})}
        if page_token:
            current_params['pageToken'] = page_token

        page_succeeded = False
        last_error = None
        
        for attempt in range(_MAX_PAGE_RETRIES + 1):
            try:
                r = _api_call_with_refresh(
                    method, url, google,
                    params=current_params if method == 'GET' else None,
                    json_body=current_params if method == 'POST' else None
                )
                
                if r.status_code == 200:
                    page_succeeded = True
                    response_json = r.json()
                    items = response_json.get(resource_key, [])
                    all_items.extend(items)
                    page_token = response_json.get('nextPageToken')
                    break
                
                if r.status_code in _FAIL_FAST_STATUSES or r.status_code not in _RETRYABLE_STATUSES:
                    print(f"DEBUG: Permanent error {r.status_code} fetching {resource_key} at {url}")
                    full_fetch_success = False
                    break
                
                # Retryable error
                if attempt < _MAX_PAGE_RETRIES:
                    wait = (2 ** (attempt + 1)) + random.uniform(0, 1)
                    print(f"DEBUG: Retryable error {r.status_code} fetching {resource_key} (page {page_count}). Retrying in {wait:.1f}s...")
                    time.sleep(wait)
                else:
                    print(f"DEBUG: Max retries reached for {resource_key} (page {page_count}) at {url}")
                    full_fetch_success = False
            except Exception as e:
                last_error = str(e)
                if attempt < _MAX_PAGE_RETRIES:
                    wait = (2 ** (attempt + 1)) + random.uniform(0, 1)
                    time.sleep(wait)
                else:
                    print(f"DEBUG: Exception fetching {resource_key}: {last_error}")
                    full_fetch_success = False

        if not page_succeeded:
            full_fetch_success = False
            break
            
        if not page_token:
            break
            
    return all_items, full_fetch_success


def list_accounts(access_token: str) -> pd.DataFrame:
    """List Google Business accounts (Basic version for validation, limited to first page)"""
    url = 'https://mybusinessaccountmanagement.googleapis.com/v1/accounts'
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        msg = _extract_google_error(resp)
        raise RuntimeError(msg)
    return pd.json_normalize(resp.json().get('accounts', []))


def list_accounts_with_refresh(google: Dict[str, Any]) -> pd.DataFrame:
    """List ALL Google Business accounts with pagination and retries"""
    url = 'https://mybusinessaccountmanagement.googleapis.com/v1/accounts'
    accounts, success = _fetch_all_pages(url, google, 'accounts')
    if not success:
         print("DEBUG: Warning: Some pages of accounts might have failed to fetch")
    return pd.json_normalize(accounts) if accounts else pd.DataFrame()


def list_locations_with_refresh(accounts_df: pd.DataFrame, google: Dict[str, Any]) -> pd.DataFrame:
    """List ALL locations for all accounts with pagination and retries"""
    all_locations_list: List[Dict[str, Any]] = []
    overall_success = True
    
    for account_id in accounts_df['name'] if not accounts_df.empty else []:
        url = f'https://mybusinessbusinessinformation.googleapis.com/v1/{account_id}/locations'
        params = {
            'readMask': 'name,title,labels,phone_numbers.primary_phone,storefront_address.region_code,'
                        'storefront_address.administrative_area,storefront_address.locality,storefront_address.postal_code,'
                        'openInfo.status,metadata.place_id',
            'orderBy': 'title'  # 'title' is a valid orderBy field for Business Information API v1 —
                                # sorts alphabetically by location display name, giving deterministic
                                # page boundaries so multi-page fetches have no gaps or duplicates
        }
        
        locations, success = _fetch_all_pages(url, google, 'locations', params=params)
        if not success:
            overall_success = False
            print(f"DEBUG: Failed to fetch some locations for account {account_id}")
            
        for loc in locations:
            loc['account_id'] = account_id
            all_locations_list.append(loc)
            
    return pd.json_normalize(all_locations_list) if all_locations_list else pd.DataFrame()


def list_reviews_with_refresh(
    locations_df: pd.DataFrame,
    google: Dict[str, Any],
) -> tuple:  # returns (DataFrame, full_fetch_success: bool)
    """
    Fetch ALL reviews for given locations with stable pagination.

    Returns:
        (df, full_fetch_success) where full_fetch_success=True only when
        every account and every page was fetched without error.

    Features:
    - Deterministic ordering: orderBy='updateTime desc' stabilises page boundaries
    - Full pagination: fetches every page, never stops early on date
    - Error handling: continues with other accounts if one fails; sets
      full_fetch_success=False so caller can skip deletion detection
    """
    all_reviews: List[Dict[str, Any]] = []
    total_api_calls = 0
    failed_accounts: List[str] = []  # track any account that errored

    if locations_df.empty:
        print("DEBUG: No locations to fetch reviews for")
        return pd.DataFrame(), True  # vacuously successful

    print(f"DEBUG: Starting review fetch for {len(locations_df)} locations")

    for account_id, group in locations_df.groupby('account_id'):
        location_names = [f"{account_id}/{loc_name}" for loc_name in group['name']]
        print(f"DEBUG: Fetching reviews for account {account_id} with {len(location_names)} locations")

        page_token = None
        page_count = 0
        account_reviews_count = 0
        account_failed = False
        location_counts: Dict[str, int] = {}
        skipped_in_account = 0

        while True:
            page_count += 1
            url = f"https://mybusiness.googleapis.com/v4/{account_id}/locations:batchGetReviews"
            body = {
                'locationNames': location_names,
                'pageSize': 50,
                'orderBy': 'updateTime desc',  # Stable deterministic ordering
            }

            if page_token:
                body['pageToken'] = page_token
                print(f"DEBUG: Fetching page {page_count} for account {account_id} (pageToken: {page_token[:20]}...)")
            else:
                print(f"DEBUG: Fetching page {page_count} for account {account_id} (initial page)")

            # --- Exponential backoff retry for transient errors (e.g. 503) ---
            # Only 503 (Service Unavailable) is retried; permanent errors (4xx)
            # fail fast exactly as before. Max 3 retries: waits 2s → 4s → 8s.
            _MAX_PAGE_RETRIES = 3
            _RETRYABLE_STATUSES = {429, 500, 502, 503, 504}  # all transient Google API errors
            _FAIL_FAST_STATUSES = {400, 401, 403, 404}

            try:
                page_succeeded = False
                for _attempt in range(_MAX_PAGE_RETRIES + 1):
                    r = _api_call_with_refresh(
                        'POST', url, google,
                        json_body=body,
                        headers={'Content-Type': 'application/json'}
                    )
                    total_api_calls += 1

                    if r.status_code == 200:
                        page_succeeded = True
                        if _attempt > 0:
                            print(f"DEBUG: RECOVERED after retry={_attempt}, account={account_id}, page={page_count}")
                        break  # success — exit retry loop

                    if r.status_code in _FAIL_FAST_STATUSES or r.status_code not in _RETRYABLE_STATUSES:
                        # Permanent error — no point retrying
                        print(f"DEBUG: ERROR - API call failed with status {r.status_code} for account {account_id} (non-retryable)")
                        account_failed = True
                        break  # exits retry loop; outer while-True will see account_failed

                    # Retryable status (e.g. 503)
                    if _attempt < _MAX_PAGE_RETRIES:
                        _wait = (2 ** (_attempt + 1)) + random.uniform(0, 1)  # 2-3s, 4-5s, 8-9s (jitter prevents retry storms)
                        print(f"DEBUG: Got {r.status_code} on page {page_count} for account {account_id} — "
                              f"retrying in {_wait}s (attempt {_attempt + 1}/{_MAX_PAGE_RETRIES})...")
                        time.sleep(_wait)
                    else:
                        # All retries exhausted
                        print(f"DEBUG: ERROR - API call failed with status {r.status_code} for account {account_id} "
                              f"after {_MAX_PAGE_RETRIES} retries — giving up on this account")
                        account_failed = True
                        break  # exits retry loop

                if account_failed:
                    break  # exits the while-True page loop for this account

                if not page_succeeded:
                    # Should not reach here, but guard against logic gaps
                    account_failed = True
                    break

                response_json = r.json()
                location_reviews = response_json.get('locationReviews', [])

                page_reviews_count = 0
                for entry in location_reviews:
                    location_full_name = entry.get('name', 'unknown')
                    review = entry.get('review')
                    if review:
                        review['locationName'] = location_full_name
                        all_reviews.append(review)
                        page_reviews_count += 1
                        location_counts[location_full_name] = location_counts.get(location_full_name, 0) + 1
                    else:
                        skipped_in_account += 1
                        print(f"DEBUG: [diagnostic] Review body missing for entry in {location_full_name}")

                account_reviews_count += page_reviews_count
                print(f"DEBUG: Page {page_count} yielded {page_reviews_count} reviews (raw, unfiltered)")

                page_token = response_json.get('nextPageToken')
                if not page_token:
                    print(f"DEBUG: No more pages for account {account_id} — "
                          f"fetched {account_reviews_count} total reviews "
                          f"({skipped_in_account} skipped due to missing review body)")
                    # Log a summary of reviews per location for this account
                    for loc, count in sorted(location_counts.items()):
                        print(f"DEBUG: [diagnostic] Location {loc}: {count} reviews")
                    break

            except Exception as e:
                print(f"DEBUG: ERROR fetching reviews for account {account_id} on page {page_count}: {str(e)}")
                account_failed = True
                break

        if account_failed:
            failed_accounts.append(str(account_id))

    full_fetch_success = len(failed_accounts) == 0
    if not full_fetch_success:
        print(f"WARNING [fetch] {len(failed_accounts)} account(s) failed — deletion detection will be skipped: {failed_accounts}")

    print(f"DEBUG: Review fetch complete — Total raw reviews: {len(all_reviews)}, Total API calls: {total_api_calls}, full_fetch_success={full_fetch_success}")
    df = pd.json_normalize(all_reviews) if all_reviews else pd.DataFrame()
    return df, full_fetch_success


# ============================================================================
# REVIEW SCOPE FILTER & PERSISTENCE HELPERS
# ============================================================================

def _filter_reviews_by_scope(
    reviews_df: pd.DataFrame,
    connector_start_date: Optional[str],
    last_max_update_time: Optional[str],
    safety_cutoff: datetime,
    overlap_seconds: int = 300,
    is_delta: bool = False,
) -> pd.DataFrame:
    """
    Single authoritative filter for all three sync modes.

    Invariant enforced:
        GOOGLE_REVIEWS = reviews WHERE updateTime >= connector_start_date

    Modes
    -----
    Initial load / Drop & Reload:
        updateTime >= connector_start_date   (no upper cutoff — avoids excluding recent reviews)

    Delta:
        updateTime in [max(connector_start_date, last_max - overlap), safety_cutoff)

    Also performs a global sort + dedup on reviewId BEFORE filtering so that
    unstable API pagination order never causes skipped or duplicated records.
    """
    if reviews_df.empty:
        return reviews_df

    df = reviews_df.copy()

    # Parse updateTime to UTC-aware timestamps
    df['updateTime'] = pd.to_datetime(df['updateTime'], utc=True, errors='coerce')
    nat_count = df['updateTime'].isna().sum()
    if nat_count > 0:
        print(f"DEBUG [filter] WARNING: {nat_count} review(s) have unparseable updateTime — they will be excluded")

    # ── Global sort + dedup (must happen before any filtering) ────────────────
    df.sort_values('updateTime', ascending=False, inplace=True)
    df.drop_duplicates(subset=['reviewId'], keep='first', inplace=True)
    df.reset_index(drop=True, inplace=True)
    print(f"DEBUG [filter] After sort+dedup: {len(df)} reviews")

    # Parse connector_start_date once
    start_dt: Optional[datetime] = None
    if connector_start_date:
        try:
            start_dt = pd.to_datetime(connector_start_date, utc=True).to_pydatetime()
        except Exception as exc:
            print(f"⚠️ Could not parse connector_start_date '{connector_start_date}': {exc}")

    if is_delta:
        # ── Delta mode: [last_max - overlap, safety_cutoff) ──────────────────
        # last_max_update_time may be None if not yet persisted; in that case
        # the caller should have set it to connector_start_date (effective_last_max).
        if last_max_update_time:
            try:
                last_max_dt = pd.to_datetime(last_max_update_time, utc=True).to_pydatetime()
                lower_bound = last_max_dt - timedelta(seconds=overlap_seconds)
                # Clamp to connector_start_date to prevent pre-scope records
                if start_dt:
                    lower_bound = max(lower_bound, start_dt)
                mask = (df['updateTime'] >= lower_bound) & (df['updateTime'] < safety_cutoff)
                print(f"DEBUG [filter] DELTA mode: {lower_bound} <= updateTime < {safety_cutoff}")
            except Exception as exc:
                print(f"\u26a0\ufe0f Could not parse last_max_update_time '{last_max_update_time}': {exc} — falling back to start_date scope")
                mask = (df['updateTime'] >= start_dt) & (df['updateTime'] < safety_cutoff) if start_dt else (df['updateTime'] < safety_cutoff)
        else:
            # No lower bound available — include everything up to safety_cutoff
            mask = (df['updateTime'] >= start_dt) & (df['updateTime'] < safety_cutoff) if start_dt else (df['updateTime'] < safety_cutoff)
            print(f"DEBUG [filter] DELTA mode (no last_max): updateTime in [{start_dt}, {safety_cutoff})")
    else:
        # ── Initial load / Drop & Reload: updateTime >= connector_start_date ──
        # No upper safety cutoff — avoids excluding very recent reviews during rebuild.
        if start_dt:
            mask = df['updateTime'] >= start_dt
            print(f"DEBUG [filter] FULL mode: updateTime >= {start_dt}")
        else:
            mask = df['updateTime'].notna()
            print("DEBUG [filter] FULL mode: no start_date — including all reviews with valid updateTime")

    filtered = df[mask].reset_index(drop=True)
    print(f"DEBUG [filter] Input: {len(df)}, Output: {len(filtered)} reviews")
    return filtered


def _persist_last_max_update_time(connection_id: int, max_update_time) -> bool:
    """
    Persist max(updateTime) from the last successful sync to Postgres.
    Written to source_table_selection.last_synced for GOOGLE_REVIEWS, which is
    the same column the router reads back as last_checked_date on the next run.

    Note: update_table_status_snowflake() also updates last_synced at job
    completion (using sync_end_time). This function is an in-process belt-and-
    suspenders update so that the value reflects the max review updateTime seen,
    not just the wall-clock job end time.

    Non-fatal: logs on failure but does not raise.
    """
    try:
        max_str = (
            max_update_time.isoformat()
            if hasattr(max_update_time, 'isoformat')
            else str(max_update_time)
        )
        conn = postgres_connection()
        cursor = conn.cursor()
        # Write to source_table_selection.last_synced — the field the router reads
        # back as last_checked_date (used as start_date / delta lower bound).
        cursor.execute("""
            UPDATE source_table_selection
            SET last_synced = %s
            WHERE connection_id = %s AND "table" = 'GOOGLE_REVIEWS'
        """, (max_str, connection_id))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"✅ Persisted last_max_update_time={max_str} for connection {connection_id}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to persist last_max_update_time for connection {connection_id}: {e}")
        return False



# ============================================================================
# MISSING-STREAK SOFT DELETION
# ============================================================================

def _apply_missing_streak_deletion(
    cursor,
    all_api_review_ids: set,
    target_table: str = 'GOOGLE_REVIEWS',
    missing_streak_n: int = 3,
    mass_deletion_threshold: float = 0.20,
    small_dataset_threshold: int = 50,
) -> dict:
    """
    Frequency-agnostic soft deletion using a consecutive-miss counter.

    Algorithm (guard evaluates BEFORE increment to prevent streak accumulation
    during API outage runs):
    ---------
    1.  Load current API reviewIds into a temp table.
    2.  Count total active rows + count candidate rows (would be incremented).
    3.  Mass-deletion guard (only for datasets > small_dataset_threshold):
        if candidate_count / total_active_rows > mass_deletion_threshold,
        skip BOTH increment and soft-delete. Streaks are NOT advanced.
    4.  If guard passes: increment MISSING_COUNT for absent rows.
    5.  Soft-delete rows whose MISSING_COUNT reached missing_streak_n.

    Guard fires BEFORE increment so that outage runs don't silently advance
    streaks toward the N=3 threshold. A 3-run outage with guard threshold=20%
    will leave MISSING_COUNT unchanged; once the API recovers, streaks reset
    to 0 via MERGE without any deletions.

    Small-dataset bypass (total_active_rows <= small_dataset_threshold=50):
    The spike guard is skipped for small datasets because a business with
    few reviews legitimately deleting all of them would cause 100% ratio,
    permanently blocking deletion. These tenants don't need the guard.

    Returns a dict with counts for observability logging.
    """
    tmp_table = "TMP_GOOGLE_REVIEWS_SEEN_IDS"

    # ── Step 1: Create / refresh temp table of seen IDs ──────────────────────
    cursor.execute(f"""
        CREATE TEMP TABLE IF NOT EXISTS {tmp_table} (
            REVIEWID STRING
        )
    """)
    cursor.execute(f"TRUNCATE TABLE {tmp_table}")

    # Bulk-insert seen IDs in batches to avoid single-statement limits
    review_id_list = list(all_api_review_ids)
    batch_size = 500
    total_inserted = 0
    for i in range(0, len(review_id_list), batch_size):
        batch = review_id_list[i:i + batch_size]
        placeholders = ", ".join(["(%s)"] * len(batch))
        cursor.execute(f"INSERT INTO {tmp_table} (REVIEWID) VALUES {placeholders}", batch)
        total_inserted += len(batch)
    print(f"[DELETION] Loaded {total_inserted} seen reviewIds into {tmp_table}")

    # ── Step 2: Pre-increment counts (guard must see these BEFORE any UPDATE) ─
    cursor.execute(f'SELECT COUNT(*) FROM {target_table} WHERE "_SPECT_DELETED" = FALSE')
    total_active_rows = (cursor.fetchone() or [0])[0]

    # Count how many rows WOULD be incremented — used to evaluate spike ratio
    # before committing any changes.
    cursor.execute(f"""
        SELECT COUNT(*)
        FROM {target_table} t
        WHERE t."_SPECT_DELETED" = FALSE
          AND NOT EXISTS (
              SELECT 1 FROM {tmp_table} s WHERE s.REVIEWID = t."REVIEWID"
          )
    """)
    candidate_count = (cursor.fetchone() or [0])[0]

    print(
        f"[DELETION] total_active={total_active_rows} | "
        f"candidate_missing={candidate_count} | seen={len(all_api_review_ids)}"
    )

    # ── Step 3: Mass-deletion guard (pre-increment) ───────────────────────────
    # Only apply for datasets large enough that a spike is meaningful.
    # Small datasets (≤ small_dataset_threshold) bypass the guard so a
    # business deleting all their reviews is handled correctly.
    mass_deletion_blocked = False
    use_spike_guard = total_active_rows > small_dataset_threshold

    if use_spike_guard and total_active_rows > 0:
        deletion_ratio = candidate_count / total_active_rows
        if deletion_ratio > mass_deletion_threshold:
            print(
                f"[DELETION] ⚠️ BLOCKED — abnormal spike detected: "
                f"{candidate_count}/{total_active_rows} rows absent "
                f"({deletion_ratio:.1%} > {mass_deletion_threshold:.0%} threshold). "
                f"Increment AND soft-delete both skipped — streaks NOT advanced. "
                f"Investigate: API outage, permission loss, or OAuth scope change."
            )
            mass_deletion_blocked = True
    elif not use_spike_guard:
        print(
            f"[DELETION] Spike guard bypassed — small dataset "
            f"({total_active_rows} rows ≤ {small_dataset_threshold} threshold)"
        )

    # ── Step 4: Increment MISSING_COUNT (skipped if guard fired) ─────────────
    rows_incremented = 0
    if not mass_deletion_blocked:
        cursor.execute(f"""
            UPDATE {target_table} t
            SET "MISSING_COUNT" = COALESCE("MISSING_COUNT", 0) + 1
            WHERE t."_SPECT_DELETED" = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM {tmp_table} s WHERE s.REVIEWID = t."REVIEWID"
              )
        """)
        rows_incremented = cursor.rowcount

    # ── Step 5: Soft-delete once streak is reached ────────────────────────────
    rows_soft_deleted = 0
    if not mass_deletion_blocked:
        cursor.execute(f"""
            UPDATE {target_table}
            SET "_SPECT_DELETED" = TRUE
            WHERE "_SPECT_DELETED" = FALSE
              AND "MISSING_COUNT" >= {missing_streak_n}
        """)
        rows_soft_deleted = cursor.rowcount

    # ── Structured observability log ─────────────────────────────────────────
    print(
        f"[DELETION] Run complete — "
        f"seen={len(all_api_review_ids)} | "
        f"total_active={total_active_rows} | "
        f"candidate_missing={candidate_count} | "
        f"rows_incremented={rows_incremented} | "
        f"soft_deleted_now={rows_soft_deleted} | "
        f"mass_deletion_blocked={mass_deletion_blocked} | "
        f"spike_guard_active={use_spike_guard} | "
        f"streak_threshold={missing_streak_n}"
    )

    return {
        "seen": len(all_api_review_ids),
        "total_active_rows": total_active_rows,
        "candidate_missing": candidate_count,
        "rows_incremented": rows_incremented,
        "soft_deleted_now": rows_soft_deleted,
        "mass_deletion_blocked": mass_deletion_blocked,
        "spike_guard_active": use_spike_guard,
        "streak_threshold": missing_streak_n,
    }


def _sf_connect(cfg: Dict[str, Any]):
    from datasyncher_connections.snowflake.connector import create_snowflake_connector

    print("DEBUG: Setting up Snowflake connection...")
    print(
        "DEBUG: Authentication method - password: "
        f"{'present' if cfg.get('password') else 'empty'}, "
        f"key_pair: {'present' if cfg.get('private_key') else 'empty'}"
    )
    conn = create_snowflake_connector(cfg, preflight=False)
    print("DEBUG: Snowflake connection parameters configured successfully")
    return conn


def _check_table_exists(cursor, table_name: str, schema_name: str = None) -> bool:
    """Check if a table exists in the current schema"""
    try:
        # Get current schema if not provided
        if not schema_name:
            cursor.execute("SELECT CURRENT_SCHEMA()")
            schema_result = cursor.fetchone()
            schema_name = schema_result[0] if schema_result else None
            print(f"DEBUG: Checking table existence in schema: {schema_name}")
        
        # Method 1: Try to query the table directly (most reliable)
        try:
            cursor.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
            cursor.fetchone()
            print(f"DEBUG: Table {table_name} exists in schema {schema_name}: True (direct query)")
            return True
        except Exception as direct_error:
            error_msg = str(direct_error).lower()
            if "does not exist" in error_msg or "object does not exist" in error_msg:
                print(f"DEBUG: Table {table_name} does not exist in schema {schema_name}: False (direct query)")
                return False
            else:
                # Some other error, fall back to information_schema check
                print(f"DEBUG: Direct query failed with unexpected error: {direct_error}, trying information_schema")
        
        # Method 2: Fallback to information_schema (can have caching issues)
        query = f"""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = '{schema_name.upper()}' 
            AND table_name = '{table_name.upper()}'
        """
        cursor.execute(query)
        result = cursor.fetchone()
        exists = result[0] > 0 if result else False
        print(f"DEBUG: Table {table_name} exists in schema {schema_name}: {exists} (information_schema)")
        return exists
    except Exception as e:
        print(f"DEBUG: Error checking table existence for {table_name}: {str(e)}")
        return False


def _log_table_migration(cmp_id: int, connection_id: int, user_id: int, table_name: str, 
                        rows_inserted: int, columns_count: int, session: str,job_name: Optional[str] = None):
    """
    Log individual table migration to log.
    
    Args:
        cmp_id: Company ID
        connection_id: Connection ID
        user_id: User ID
        table_name: Name of the table migrated
        rows_inserted: Number of rows inserted/migrated
        columns_count: Number of columns in the table
        session: Session identifier
        job_name: Job name (optional, for migration-related logs
    """
    try:

        
        # Create migration success message
        message = f"Successfully migrated/updated '{table_name}' table, containing data with {rows_inserted} rows and {columns_count} columns."
        print(f"📊 {message}")
        
        # Get migration_record_id from job_name and table_name (if available)
        migration_record_id = None
        if job_name:
            try:
                from utils.database_utils import postgres_connection
                conn = postgres_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT id FROM migration_record 
                        WHERE job_name = %s AND table_name = %s AND connection_id = %s
                    ''', (job_name, table_name, connection_id))
                    result = cursor.fetchone()
                    if result:
                        migration_record_id = result[0]
                    cursor.close()
                    conn.close()
            except Exception as e:
                print(f"⚠️ Warning: Could not fetch migration_record_id: {e}")
        
        # Use centralized insert_tool_log utility (with race condition protection)
        insert_tool_log(
            cmp_id, connection_id, user_id, table_name, "S", message, session,
            job_name=job_name, migration_record_id=migration_record_id
        )
        print(f"✅ Table migration logged successfully for {table_name}")
        
    except Exception as e:
        print(f"⚠️ Failed to log table migration for {table_name}: {e}")


def _create_staging_table(cursor, table_name: str, df: pd.DataFrame, wide_text: bool = False) -> str:
    """
    Create a staging table for the given table name.
    
    Args:
        cursor: Snowflake cursor
        table_name: Base table name (e.g., 'GOOGLE_REVIEWS')
        df: DataFrame with data (used to infer schema)
        wide_text: If True, use TEXT for all string columns
    
    Returns:
        staging_table_name: Name of the created staging table
    """
    if df.empty:
        raise ValueError(f"Cannot create staging table for empty DataFrame: {table_name}")
    
    # Normalize column names (replace dots with underscores, convert to UPPERCASE)
    original_columns = list(df.columns)
    
    # Step 1: Replace dots with underscores
    if any('.' in c for c in df.columns):
        df.columns = [c.replace('.', '_') for c in df.columns]
        print(f"DEBUG: Replaced dots in column names: {original_columns} → {list(df.columns)}")
    
    # Step 2: Convert to UPPERCASE (Snowflake standard)
    df.columns = [c.upper() for c in df.columns]
    print(f"DEBUG: Converted column names to UPPERCASE: {list(df.columns)}")
    
    staging_table_name = f"DS_{table_name}_STAGING_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    cols = list(df.columns)
    print(f"DEBUG: Staging table {staging_table_name} will have columns: {cols}")
    
    def infer(col: str) -> str:
        col_u = col.upper()
        if col_u == "_SPECT_SYNCED":
            return f'"{col}" TIMESTAMP_TZ'
        if col_u == "_SPECT_DELETED":
            return f'"{col}" BOOLEAN'
        if col_u == "LAST_SEEN_IN_API":
            return f'"{col}" TIMESTAMP_NTZ'
        if col_u == "MISSING_COUNT":
            return f'"{col}" NUMBER(10,0)'
        if wide_text:
            return f'"{col}" TEXT'
        if 'time' in col.lower():
            return f'"{col}" TIMESTAMP_NTZ'
        if 'comment' in col.lower():
            return f'"{col}" TEXT'
        return f'"{col}" VARCHAR(512)'
    
    column_defs = ",\n  ".join([infer(c) for c in cols])
    
    # Drop staging table if exists (clean slate)
    print(f"DEBUG: Dropping staging table {staging_table_name} if exists...")
    cursor.execute(f"DROP TABLE IF EXISTS {staging_table_name}")
    
    # Create fresh staging table
    print(f"DEBUG: Creating staging table {staging_table_name}...")
    cursor.execute(f"CREATE TABLE {staging_table_name} (\n  {column_defs}\n)")
    print(f"DEBUG: Staging table {staging_table_name} created successfully")
    
    return staging_table_name


def _insert_into_staging_copy(cursor, df: pd.DataFrame, staging_table: str, table_name: str, 
                               connection_id: int, cmp_id: int, is_integration_checked: bool = False,
                               migration_session_id: str = None, job_name: str = None,
                               snowflake_engine=None) -> bool:
    """
    Insert data into staging table using COPY INTO.
    This is 20-100x faster than row-by-row INSERT for large datasets.
    
    Args:
        cursor: Snowflake cursor
        df: DataFrame with data
        staging_table: Name of staging table
        table_name: Base table name (for GCS blob naming)
        connection_id: Connection ID
        cmp_id: Company ID
        is_integration_checked: Whether GCS integration was already verified
    
    Returns:
        True if GCS integration is verified
    """
    if df.empty:
        print(f"DEBUG: No data to insert into {staging_table}")
        return is_integration_checked
    
    # Normalize column names
    if any('.' in c for c in df.columns):
        df.columns = [c.replace('.', '_') for c in df.columns]
    
    print(f"DEBUG: Inserting {len(df)} rows into staging table {staging_table} via COPY INTO...")
    
    # Step 1: Ensure GCS storage integration exists (one-time check per connection)
    if not is_integration_checked:
        print("🔐 Checking GCS storage integration...")
        integration_exists = ensure_gcs_storage_integration(cursor, DEMO_STORAGE, GCS_BUCKET_URL, connection_id, cmp_id)
        if not integration_exists:
            print("⚠️ WARNING: Storage integration not found. COPY INTO may fail.")
            print("💡 Falling back to row-by-row insert as safety measure...")
            # Fallback to old method if integration doesn't exist
            return _insert_into_staging_fallback(
                cursor, df, staging_table,
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                snowflake_engine=snowflake_engine
            )
        is_integration_checked = True
    
    # Step 2: Clean data (fill NULLs)
    print("🧹 Cleaning data for CSV upload...")
    df = df.fillna("")  # Replace NaN with empty string for CSV compatibility
    
    # Step 3: Upload DataFrame to GCS
    print("📤 Uploading DataFrame to GCS...")
    gcs_path = upload_to_bucket(df, table_name, connection_id, force=False)
    
    if not gcs_path:
        print("❌ GCS upload failed, cannot proceed with COPY INTO")
        raise RuntimeError("Failed to upload data to GCS")
    
    print(f"✅ Data uploaded to: {gcs_path}")
    
    # Step 4: COPY INTO staging table from GCS
    file_path = f"{GCS_BUCKET_URL}temp_data_google_reviews_{table_name}_{connection_id}.csv"
    
    copy_query = f"""
        COPY INTO {staging_table}
        FROM '{file_path}'
        FILE_FORMAT = (
            TYPE = CSV
            FIELD_OPTIONALLY_ENCLOSED_BY = '"'
            SKIP_HEADER = 1
            ESCAPE_UNENCLOSED_FIELD = '\\\\'
            NULL_IF = ('', 'None', 'null', 'NULL')
            TIMESTAMP_FORMAT = 'AUTO'
            DATE_FORMAT = 'AUTO'
        )
        STORAGE_INTEGRATION = {DEMO_STORAGE}
        ON_ERROR = CONTINUE;
    """
    
    print("📥 Executing COPY INTO from GCS to staging table...")
    print(f"   Source: {file_path}")
    print(f"   Target: {staging_table}")
    
    try:
        cursor.execute(copy_query)
        copy_results = cursor.fetchall()
        
        if copy_results:
            rows_loaded = 0
            errors_seen = 0
            for row in copy_results:
                # COPY INTO returns: file, status, rows_parsed, rows_loaded, error_limit, errors_seen, first_error, ...
                if len(row) >= 4:
                    rows_loaded += row[3] if row[3] else 0
                    if len(row) >= 6:
                        errors_seen += row[5] if row[5] else 0
            
            print(f"✅ COPY INTO completed:")
            print(f"   Rows loaded: {rows_loaded}")
            print(f"   Errors seen: {errors_seen}")
            
            if errors_seen > 0:
                print(f"⚠️ WARNING: {errors_seen} rows had errors and were skipped")
        else:
            print("✅ COPY INTO completed (no result rows returned)")
        
        print(f"DEBUG: Successfully loaded data into {staging_table} via COPY INTO")
        
        # Update progress tracking
        if migration_session_id and job_name and snowflake_engine:
            try:
                # Get schema from cursor connection
                snowflake_schema = cursor.connection.schema if hasattr(cursor, 'connection') else 'PUBLIC'
                
                SimpleProgressTracker.update_staging_count(
                    engine=snowflake_engine,  # Use passed SQLAlchemy engine
                        connection_id=connection_id,
                        migration_session_id=migration_session_id,
                        job_name=job_name,
                        table_name=table_name,
                    staging_table=staging_table,
                    snowflake_schema=snowflake_schema
                )
            except Exception as e:
                print(f"⚠️ Failed to update progress tracking: {e}")
        
        return is_integration_checked
    
    except Exception as e:
        print(f"❌ COPY INTO failed: {e}")
        traceback.print_exc()
        raise


def _insert_into_staging_fallback(cursor, df: pd.DataFrame, staging_table: str,
                                  connection_id: int = None, migration_session_id: str = None, 
                                  job_name: str = None, table_name: str = None,
                                  snowflake_engine=None) -> bool:
    """
    Fallback method: Row-by-row INSERT (old method).
    Used only if COPY INTO is not available.
    
    Args:
        cursor: Snowflake cursor
        df: DataFrame with data
        staging_table: Name of staging table
    
    Returns:
        False (integration not checked in fallback mode)
    """
    if df.empty:
        print(f"DEBUG: No data to insert into {staging_table}")
        return False
    
    # Normalize column names
    if any('.' in c for c in df.columns):
        df.columns = [c.replace('.', '_') for c in df.columns]
    
    cols = list(df.columns)
    col_names = ", ".join([f'"{c}"' for c in cols])
    placeholders = ", ".join([f"%({c})s" for c in cols])
    insert_sql = f'INSERT INTO {staging_table} ({col_names}) VALUES ({placeholders})'
    
    print(f"DEBUG: Inserting {len(df)} rows into staging table {staging_table} (FALLBACK: row-by-row)...")
    rows_inserted = 0
    
    for _, row in df.iterrows():
        row_dict = {}
        for k, v in row.items():
            if pd.isna(v):
                row_dict[k] = None
            elif isinstance(v, pd.Timestamp):
                row_dict[k] = v.isoformat()
            else:
                row_dict[k] = str(v)
        cursor.execute(insert_sql, row_dict)
        rows_inserted += 1
    
    print(f"DEBUG: Successfully inserted {rows_inserted} rows into {staging_table} (FALLBACK)")
    
    # Update progress tracking for fallback
    if all([connection_id, migration_session_id, job_name, table_name]) and snowflake_engine:
        try:
            # Get schema from cursor connection
            snowflake_schema = cursor.connection.schema if hasattr(cursor.connection, 'schema') else 'PUBLIC'
            
            SimpleProgressTracker.update_staging_count(
                engine=snowflake_engine,
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                staging_table=staging_table,
                snowflake_schema=snowflake_schema
            )
        except Exception as e:
            print(f"⚠️ Failed to update progress tracking in fallback: {e}")
    
    return False


def _add_spect_metadata_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add standard metadata columns used across connectors.

    For Google Reviews the two extra streak-deletion fields are also set here
    so every MERGE-ed row resets its own counters upon being seen in the API:
      LAST_SEEN_IN_API  — timestamp of this sync run
      MISSING_COUNT     — reset to 0 (row is present in current API response)
    """
    if df is None or df.empty:
        return df

    now = datetime.utcnow()
    df["_SPECT_SYNCED"]      = now
    df["_SPECT_DELETED"]     = False
    df["LAST_SEEN_IN_API"]   = now        # streak reset: row seen this run
    df["MISSING_COUNT"]      = 0          # streak reset: row seen this run

    return df


def _merge_staging_to_target(cursor, staging_table: str, target_table: str, primary_key: str = None):
    """
    Merge data from staging table to target table using MERGE statement.
    For delta tables with primary key (like GOOGLE_REVIEWS with reviewId).
    
    Args:
        cursor: Snowflake cursor
        staging_table: Name of staging table
        target_table: Name of target table
        primary_key: Primary key field for matching (e.g., 'reviewId')
    """
    print(f"DEBUG: Starting MERGE from {staging_table} to {target_table}...")
    
    # Get columns from staging table
    cursor.execute(f"""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{staging_table.upper()}'
          AND TABLE_SCHEMA = CURRENT_SCHEMA()
          AND TABLE_CATALOG = CURRENT_DATABASE()
        ORDER BY ORDINAL_POSITION
    """)
    staging_columns = [row[0] for row in cursor.fetchall()]
    
    if not staging_columns:
        raise RuntimeError(f"No columns found for staging table {staging_table}")
    
    print(f"DEBUG: Found {len(staging_columns)} columns in {staging_table}")

    # Ensure required metadata columns exist in target table for merge compatibility.
    # Older tables may predate these columns and then MERGE fails with an invalid identifier.
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "_SPECT_SYNCED" TIMESTAMP_TZ')
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "_SPECT_DELETED" BOOLEAN')
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "LAST_SEEN_IN_API" TIMESTAMP_NTZ')
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "MISSING_COUNT" NUMBER(10,0)')
    print(f'DEBUG: Ensured metadata columns exist in {target_table} (_SPECT_SYNCED, _SPECT_DELETED, LAST_SEEN_IN_API, MISSING_COUNT)')

    # Fetch target table columns so we only reference columns that actually exist there.
    # Prevents "invalid identifier" when staging has new columns (e.g. REVIEWMEDIAITEMS)
    # that an older target table doesn't have yet.
    cursor.execute(f"""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{target_table.upper()}'
          AND TABLE_SCHEMA = CURRENT_SCHEMA()
          AND TABLE_CATALOG = CURRENT_DATABASE()
        ORDER BY ORDINAL_POSITION
    """)
    target_columns_upper = {row[0].upper() for row in cursor.fetchall()}
    print(f"DEBUG: Target table {target_table} has {len(target_columns_upper)} columns")

    # Intersection: build MERGE only from columns present in BOTH staging and target.
    merge_columns = [c for c in staging_columns if c.upper() in target_columns_upper]
    skipped = [c for c in staging_columns if c.upper() not in target_columns_upper]
    if skipped:
        print(f"DEBUG: Skipping {len(skipped)} staging column(s) absent from target (schema mismatch): {skipped}")
    print(f"DEBUG: MERGE will use {len(merge_columns)} column(s)")

    # Prepare MERGE statement
    if primary_key:
        # Delta table with primary key - use MERGE (INSERT or UPDATE)
        print(f"DEBUG: Using MERGE with primary key: {primary_key}")
        
        # Normalize primary key
        primary_key_upper = primary_key.upper()
        if primary_key_upper not in [c.upper() for c in merge_columns]:
            raise ValueError(f"Primary key '{primary_key}' not found in common staging+target columns")
        
        # Find actual case of primary key in merge_columns
        pk_actual = next(c for c in merge_columns if c.upper() == primary_key_upper)
        
        # Update SET clause (all common columns except primary key)
        update_columns = [c for c in merge_columns if c.upper() != primary_key_upper]
        update_set = ", ".join([f't."{c}" = s."{c}"' for c in update_columns])
        
        # Insert columns and values (intersection only)
        insert_columns = ", ".join([f'"{c}"' for c in merge_columns])
        insert_values = ", ".join([f's."{c}"' for c in merge_columns])
        
        merge_sql = f"""
            MERGE INTO {target_table} t
            USING {staging_table} s
            ON t."{pk_actual}" = s."{pk_actual}"
            WHEN MATCHED THEN
                UPDATE SET {update_set}
            WHEN NOT MATCHED THEN
                INSERT ({insert_columns})
                VALUES ({insert_values})
        """
    else:
        # No primary key - this shouldn't happen for delta tables, but handle gracefully
        print(f"WARNING: No primary key specified for MERGE operation")
        raise ValueError(f"Primary key required for MERGE operation on {target_table}")
    
    print(f"DEBUG: Executing MERGE statement...")
    cursor.execute(merge_sql)
    print(f"DEBUG: MERGE completed successfully from {staging_table} to {target_table}")


def _merge_from_staging_full_load(cursor, staging_table: str, target_table: str, primary_key: str, drop_table: bool = False):
    """
    Merge data from staging table to target table for full load tables.
    Uses MERGE instead of TRUNCATE+INSERT.
    
    For tables without delta capability (GOOGLE_ACCOUNTS, GOOGLE_LOCATIONS).
    
    Args:
        cursor: Snowflake cursor
        staging_table: Name of staging table
        target_table: Name of target table
        primary_key: Primary key field for matching (e.g., 'name')
        drop_table: If True, drops and recreates table (for drop_and_reload mode).
                   If False, preserves existing table and uses MERGE.
    """
    print(f"DEBUG: Starting MERGE from {staging_table} to {target_table} (FULL LOAD)...")
    print(f"DEBUG: Drop table mode: {drop_table}")
    
    # Get columns from staging table
    cursor.execute(f"""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{staging_table.upper()}'
          AND TABLE_SCHEMA = CURRENT_SCHEMA()
          AND TABLE_CATALOG = CURRENT_DATABASE()
        ORDER BY ORDINAL_POSITION
    """)
    columns = [row[0] for row in cursor.fetchall()]
    
    if not columns:
        raise RuntimeError(f"No columns found for staging table {staging_table}")
    
    print(f"DEBUG: Found {len(columns)} columns in {staging_table}")
    print(f"DEBUG: Column names: {columns}")
    
    # Normalize primary key
    primary_key_upper = primary_key.upper()
    if primary_key_upper not in [c.upper() for c in columns]:
        raise ValueError(f"Primary key '{primary_key}' not found in staging table columns: {columns}")
    
    # Find actual case of primary key in columns
    pk_actual = next(c for c in columns if c.upper() == primary_key_upper)
    
    # Update SET clause (all columns except primary key)
    update_columns = [c for c in columns if c.upper() != primary_key_upper]
    update_set = ", ".join([f't."{c}" = s."{c}"' for c in update_columns])
    
    # Insert columns and values
    insert_columns = ", ".join([f'"{c}"' for c in columns])
    insert_values = ", ".join([f's."{c}"' for c in columns])
    
    # Handle table creation based on drop_table flag
    if drop_table:
        # Drop and recreate mode (drop_and_reload=True)
        print(f"DEBUG: DROP mode - Dropping target table {target_table} to ensure schema match...")
        cursor.execute(f"DROP TABLE IF EXISTS {target_table}")
        print(f"DEBUG: Creating target table {target_table} from staging table schema...")
        cursor.execute(f"CREATE TABLE {target_table} AS SELECT * FROM {staging_table} WHERE 1=0")
        print(f"✅ Target table {target_table} dropped and recreated")
    else:
        # Preserve table mode (delta sync or regular sync)
        print(f"DEBUG: PRESERVE mode - Checking if target table {target_table} exists...")
        cursor.execute(f"""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_NAME = '{target_table.upper()}'
              AND TABLE_SCHEMA = CURRENT_SCHEMA()
              AND TABLE_CATALOG = CURRENT_DATABASE()
        """)
        table_exists = cursor.fetchone()[0] > 0
        
        if not table_exists:
            print(f"DEBUG: Target table {target_table} does not exist - creating from staging schema...")
            cursor.execute(f"CREATE TABLE {target_table} AS SELECT * FROM {staging_table} WHERE 1=0")
            print(f"✅ Target table {target_table} created")
        else:
            print(f"✅ Target table {target_table} exists - preserving existing data")
    
    # Verify columns (for debugging)
    cursor.execute(f"""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{target_table.upper()}'
          AND TABLE_SCHEMA = CURRENT_SCHEMA()
          AND TABLE_CATALOG = CURRENT_DATABASE()
        ORDER BY ORDINAL_POSITION
    """)
    target_columns = [row[0] for row in cursor.fetchall()]
    print(f"DEBUG: Target table {target_table} has columns: {target_columns}")
    print(f"DEBUG: Staging table columns: {columns}")

    # Ensure required metadata columns exist in target table for merge compatibility.
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "_SPECT_SYNCED" TIMESTAMP_TZ')
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "_SPECT_DELETED" BOOLEAN')
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "LAST_SEEN_IN_API" TIMESTAMP_NTZ')
    cursor.execute(f'ALTER TABLE {target_table} ADD COLUMN IF NOT EXISTS "MISSING_COUNT" NUMBER(10,0)')
    print(f'DEBUG: Ensured metadata columns exist in {target_table} ("_SPECT_SYNCED", "_SPECT_DELETED", "LAST_SEEN_IN_API", "MISSING_COUNT")')
    
    # All tables are created with UPPERCASE columns
    merge_sql = f"""
        MERGE INTO {target_table} t
        USING {staging_table} s
        ON t."{pk_actual}" = s."{pk_actual}"
        WHEN MATCHED THEN
            UPDATE SET {update_set}
        WHEN NOT MATCHED THEN
            INSERT ({insert_columns})
            VALUES ({insert_values})
    """
    
    print(f"DEBUG: Executing MERGE statement (primary key: {pk_actual})...")
    print(f"DEBUG: MERGE SQL:\n{merge_sql}")
    cursor.execute(merge_sql)
    print(f"DEBUG: MERGE completed successfully from {staging_table} to {target_table}")


def _cleanup_staging_table(cursor, staging_table: str):
    """
    Drop the staging table after successful merge/load.
    
    Args:
        cursor: Snowflake cursor
        staging_table: Name of staging table to drop
    """
    print(f"DEBUG: Dropping staging table {staging_table}...")
    cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")
    print(f"DEBUG: Staging table {staging_table} dropped successfully")


def upload_to_bucket(df: pd.DataFrame, table_name: str, connection_id: int, force: bool = False, max_retries: int = 5) -> str:
    """
    Upload DataFrame to Google Cloud Storage as CSV.
    
    Args:
        df: DataFrame to upload
        table_name: Name of the table (for blob naming)
        connection_id: Connection ID (for blob naming)
        force: If True, delete existing blob before upload
        max_retries: Maximum retry attempts on 503 errors
    
    Returns:
        GCS path (gcs://bucket/blob) or None on failure
    """
    bucket_name = BUCKET_NAME
    blob_name = f"temp_data_google_reviews_{table_name}_{connection_id}.csv"
    
    try:
        # Initialize GCS client and bucket
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        # Optional: Delete existing blob if force=True
        if force and blob.exists():
            print(f"🗑️ Deleting existing blob: {blob_name}")
            blob.delete()
        
        # Convert DataFrame to CSV with proper escaping
        csv_data = df.to_csv(index=False, encoding='utf-8', quoting=csv.QUOTE_ALL, escapechar='\\')
        
        # Retry upload on 503 errors (service unavailable)
        for attempt in range(max_retries):
            try:
                print(f"⬆️ Uploading to GCS (attempt {attempt+1}/{max_retries}): {blob_name}")
                blob.upload_from_string(csv_data, content_type='text/csv', retry=Retry(
                    initial=1.0,
                    maximum=16.0,
                    multiplier=2.0,
                    deadline=60.0
                ))
                gcs_path = f"gcs://{bucket_name}/{blob_name}"
                print(f"✅ Upload successful: {gcs_path}")
                return gcs_path
            except ServiceUnavailable as e:
                print(f"⚠️ GCS service unavailable (503) on attempt {attempt+1}: {e}")
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"⏳ Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ Max retries ({max_retries}) reached")
                    raise
            except Exception as other_error:
                print(f"❌ Upload failed on attempt {attempt+1}: {other_error}")
                traceback.print_exc()
                raise
    
    except Exception as final_error:
        print(f"❌ Fatal error uploading '{table_name}' to GCS: {final_error}")
        traceback.print_exc()
        return None


def ensure_gcs_storage_integration(cursor, integration_name: str, bucket_url: str, connection_id: int, cmp_id: int) -> bool:
    """
    Ensure GCS storage integration exists in Snowflake.
    
    Args:
        cursor: Snowflake cursor
        integration_name: Name of storage integration (e.g., 'DATASYNCHER7')
        bucket_url: GCS bucket URL
        connection_id: Connection ID for logging
        cmp_id: Company ID for logging
    
    Returns:
        True if integration exists or was created, False otherwise
    """
    try:
        print(f"🔐 Checking if storage integration '{integration_name}' exists...")
        cursor.execute(f"SHOW INTEGRATIONS LIKE '{integration_name}'")
        result = cursor.fetchall()
        
        if result:
            print(f"✅ Storage integration '{integration_name}' already exists")
            return True
        else:
            print(f"⚠️ Storage integration '{integration_name}' does not exist")
            print(f"💡 Please create it manually or contact administrator")
            return False
    
    except Exception as e:
        print(f"❌ Error checking storage integration: {e}")
        return False


def sync_google_reviews_to_snowflake(
    google: Dict[str, Any],
    snowflake_cfg: Dict[str, Any],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    safety_interval: Optional[str] = "120",
    selected_tables: Optional[List[str]] = None,
    drop_and_reload: bool = False,
    connection_id: Optional[int] = None,
    cmp_id: Optional[int] = None,
    user_id: Optional[int] = None,
    job_name: Optional[str] = None,
    migration_session_id: Optional[str] = None,
    session: Optional[str] = None,
    logs_creation: Optional[bool] = None,
    connection_name: Optional[str] = None,
    company_name: Optional[str] = None,
    log_id: Optional[int] = None
) -> Dict[str, Any]:
    start_time = time.time()  # Track execution time for logging
    print("DEBUG: Starting sync_google_reviews_to_snowflake")
    print(f"DEBUG: Input parameters - start_date: {start_date}, end_date: {end_date}, safety_interval: {safety_interval}")
    print(f"DEBUG: Selected tables: {selected_tables}")
    
    
    # Extract drop_and_reload flag
    print("=" * 80)
    print(f"🔍 DEBUG GOOGLE REVIEWS: drop_and_reload flag received: {drop_and_reload}")
    print(f"🔍 DEBUG GOOGLE REVIEWS: Type: {type(drop_and_reload)}")
    print(f"🔍 DEBUG GOOGLE REVIEWS: Selected tables: {selected_tables}")
    print("=" * 80)
    
    # Default to all tables if none selected
    if not selected_tables:
        selected_tables = ['GOOGLE_ACCOUNTS', 'GOOGLE_LOCATIONS', 'GOOGLE_REVIEWS']
        print("DEBUG: No tables selected, defaulting to all tables")
    print("=" * 10,selected_tables)
    
    # ═══════════════════════════════════════════════════════════════
    # ✅ TEST GOOGLE REVIEWS CONNECTION AT START (Like Salesforce)
    # This automatically refreshes token if expired, without needing token_expires_at
    # ═══════════════════════════════════════════════════════════════
    print("🔍 Testing Google Reviews connection before starting migration...")
    try:
        # Test connection by making a simple API call
        # If token is expired, _api_call_with_refresh will automatically refresh it
        test_url = 'https://mybusinessaccountmanagement.googleapis.com/v1/accounts'
        test_response = _api_call_with_refresh('GET', test_url, google)
        
        if test_response.status_code == 200:
            print("✅ Google Reviews connection test successful")
            # Token is now refreshed in google dict if it was expired
        else:
            print(f"⚠️ Google Reviews connection test returned status {test_response.status_code}")
            # _api_call_with_refresh already tried to refresh, so if still failing, it's a real error
            if test_response.status_code == 401:
                error_msg = _extract_google_error(test_response)
                raise RuntimeError(f"Google Reviews authentication failed: {error_msg}")
            else:
                error_msg = _extract_google_error(test_response)
                raise RuntimeError(f"Google Reviews connection test failed: {error_msg}")
    except RuntimeError:
        # Re-raise RuntimeError as-is (these are our intentional errors with clear messages)
        raise
    except ValueError as e:
        # Refresh token is invalid - convert to RuntimeError with clear message
        raise RuntimeError(str(e))
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "UNAUTHENTICATED" in error_msg or "invalid authentication" in error_msg.lower():
            print(f"❌ Google Reviews connection test failed: {error_msg}")
            raise RuntimeError(f"Google Reviews authentication failed. Please re-authenticate through the OAuth flow.")
        # For other errors, re-raise as-is
        raise
    
    # ═══════════════════════════════════════════════════════════════
    # DROP TABLES IF drop_and_reload FLAG IS TRUE
    # This ensures a clean slate before reloading data from Google Reviews
    # ═══════════════════════════════════════════════════════════════
    if drop_and_reload:
        print("=" * 80)
        print("🗑️  DROP AND RELOAD MODE ENABLED (GOOGLE REVIEWS)")
        print(f"   Dropping {len(selected_tables)} table(s) before reload...")
        print("=" * 80)
        
        # Import required modules for drop operations
        from utils.table_utils import drop_snowflake_table
        from sqlalchemy import create_engine, text
        from urllib.parse import quote
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        
        # Create Snowflake engine for drop operations
        try:
            # Extract Snowflake connection parameters
            account = snowflake_cfg.get('account')
            user = snowflake_cfg.get('user') or snowflake_cfg.get('username')
            password = snowflake_cfg.get('password')
            database = snowflake_cfg.get('database')
            schema = snowflake_cfg.get('schema')
            if not schema or schema == 'None':
                raise ValueError("Schema is required for Google Reviews operations. Please ensure your connection has a valid schema configured.")
            print(f"✅ Using schema: {schema}")
            warehouse = snowflake_cfg.get('warehouse', 'COMPUTE_WH')
            role = snowflake_cfg.get('role')
            private_key_str = snowflake_cfg.get('private_key')
            passphrase = snowflake_cfg.get('passphrase')
            
            print(f"🔗 Creating Snowflake engine for drop operations...")
            print(f"   Account: {account}")
            print(f"   Database: {database}")
            print(f"   Schema: {schema}")
            print(f"   Warehouse: {warehouse}")
            
            # Detect authentication type and build engine accordingly
            if private_key_str:
                # --- Key pair authentication ---
                print("   Authentication: Key pair")
                
                # Normalize and load private key
                from services.snowflake.service import normalize_pem_key
                normalized_private_key = normalize_pem_key(private_key_str)
                private_key_bytes = normalized_private_key.encode()
                
                # Use passphrase if provided for encrypted keys
                passphrase_bytes = None
                if passphrase and passphrase.strip():
                    passphrase_bytes = passphrase.encode("utf-8")
                    print(f"   Using passphrase for encrypted private key")
                else:
                    print("   No passphrase provided, attempting to load unencrypted key")
                
                private_key = serialization.load_pem_private_key(
                    private_key_bytes,
                    password=passphrase_bytes,
                    backend=default_backend()
                )
                
                # Build SQLAlchemy engine with private key
                connection_string_sf = f"snowflake://{user}@{account}/{database}/{schema}?warehouse={warehouse}"
                if role:
                    connection_string_sf += f"&role={role}"
                
                destination_engine = create_engine(
                    connection_string_sf,
                    connect_args={"private_key": private_key}
                )
                
            elif password:
                # --- Password authentication ---
                print("   Authentication: Password")
                
                user_quoted = quote(user)
                password_quoted = quote(password)
                
                connection_string_sf = f"snowflake://{user_quoted}:{password_quoted}@{account}/{database}/{schema}?warehouse={warehouse}"
                if role:
                    connection_string_sf += f"&role={role}"
                
                destination_engine = create_engine(connection_string_sf)
                
            else:
                raise ValueError("Either password or private_key must be provided for Snowflake authentication")
            
            # Set role and schema if specified
            with destination_engine.connect() as conn:
                if role:
                    conn.execute(text(f"USE ROLE {role}"))
                    print(f"✅ Role set to: {role}")
                conn.execute(text(f"USE SCHEMA {schema}"))
                print(f"✅ Schema set to: {schema}")
            
            # Drop each selected table
            for table_name in selected_tables:
                print(f"\n🗑️  Dropping table: {table_name}")
                drop_result = drop_snowflake_table(
                    destination_engine=destination_engine,
                    table_name=table_name,
                    schema=schema,
                    connection_id=connection_id,
                    cmp_id=cmp_id
                )
                
                if drop_result['success']:
                    print(f"✅ {drop_result['message']}")
                else:
                    print(f"⚠️  {drop_result['message']}")
            
            # Dispose engine after drop operations
            destination_engine.dispose()
            print(f"\n✅ All tables dropped successfully")
            print("=" * 80)
            
        except Exception as e:
            print(f"❌ Error during drop phase: {e}")
            import traceback
            print(f"   Traceback: {traceback.format_exc()}")
            # Log the error but continue with migration
            print(f"⚠️  Continuing with migration despite drop errors...")
            print("=" * 80)
    # ============================================================================
    # 🔄 DELTA FIELD DETECTION 
    # ============================================================================
    # Get delta fields from database for this connection
    delta_fields = []
    source_id = None
    if connection_id:
        source_id = get_source_id_from_connection(connection_id)
        if source_id:
            delta_fields = get_delta_fields_for_source(source_id)
            print(f"🔍 Found {len(delta_fields)} delta fields for Google Reviews source_id {source_id}: {delta_fields}")
        else:
            print(f"⚠️ Could not find source_id for connection_id {connection_id}")
    else:
        print("DEBUG: No connection_id provided - delta field detection skipped")
    
    # Use shared table field structures (defined at module level)
    reviews_fields = GOOGLE_REVIEWS_TABLE_FIELDS['GOOGLE_REVIEWS']
    
    # Determine if GOOGLE_REVIEWS table is delta-capable and which field to use
    reviews_is_delta = check_table_has_delta_fields(reviews_fields, delta_fields) if delta_fields else False
    reviews_delta_field = None
    if reviews_is_delta and delta_fields:
        # Find which delta field exists in reviews table (case-insensitive match)
        for field in delta_fields:
            # Check if field exists in reviews_fields (case-insensitive)
            if field.upper() in [k.upper() for k in reviews_fields.keys()]:
                # Use the UPPERCASE version for actual column operations
                reviews_delta_field = field.upper()
                print(f"✅ GOOGLE_REVIEWS marked as delta table - using field: {reviews_delta_field} (from database field: {field})")
                break
    
    if not reviews_is_delta:
        print("⚠️ GOOGLE_REVIEWS not marked as delta table - will use full reload only")
    # ============================================================================
    
    # Create log files if logging is enabled
    log_files = None
    if logs_creation and connection_id and migration_session_id:
        try:
            log_dir = Path(f"/tmp/google_reviews_logs/{connection_id}/{migration_session_id}")
            log_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_file = log_dir / f"google_reviews_migration_{timestamp}.csv"
            response_file = log_dir / f"google_reviews_responses_{timestamp}.jsonl"
            
            # Create CSV with headers
            with open(csv_file, 'w', encoding='utf-8') as f:
                f.write("chunk_no,batch_index,connection_id,timestamp,operation,table_name,error_message,error_type\n")
            
            log_files = {
                'csv_file': csv_file,
                'response_file': response_file
            }
            print(f"📄 Log files created: CSV={csv_file}, JSONL={response_file}")
        except Exception as e:
            print(f"⚠️ Failed to create log files: {e}")
            log_files = None
    
    # Validate OAuth configuration
    print("DEBUG: Validating OAuth configuration...")
    config_issues = _validate_oauth_config(google)
    if config_issues:
        print("DEBUG: OAuth configuration issues found:")
        for issue in config_issues:
            print(f"DEBUG: - {issue}")
        print("DEBUG: Please fix these issues and retry the migration")
    
    access_token = google.get('access_token')
    if not access_token:
        print("DEBUG: ERROR - access_token is required but not provided")
        print("DEBUG: Google configuration received:")
        print(f"DEBUG: - Keys: {list(google.keys()) if google else 'Empty config'}")
        print("DEBUG: - Expected keys: access_token, refresh_token, CLIENT_ID, CLIENT_SECRET")
        print("DEBUG: - Missing required key: access_token")
        print("DEBUG: This indicates the Google Reviews OAuth flow hasn't been completed")
        raise ValueError('access_token is required')

    print(f"DEBUG: Access token present: {access_token[:10]}..." if access_token else "DEBUG: No access token")
    
    # Error tracking
    errors: List[str] = []
    failed_tables: List[str] = []
    successful_tables: List[str] = []
    
    # Data containers
    accounts = pd.DataFrame()
    locations = pd.DataFrame()
    reviews = pd.DataFrame()
    
    # ============================================================================
    # 🔄 GOOGLE REVIEWS HIERARCHICAL FETCH LOGIC
    # ============================================================================
    # Google Reviews API has a dependency chain:
    # Accounts → Locations → Reviews
    # 
    # Strategy:
    # - ALWAYS fetch prerequisite data (accounts/locations) if child tables are selected
    # - Only SAVE selected tables to Snowflake
    # ============================================================================
    
    # Determine what needs to be FETCHED vs what needs to be SAVED
    need_accounts = 'GOOGLE_ACCOUNTS' in selected_tables or 'GOOGLE_LOCATIONS' in selected_tables or 'GOOGLE_REVIEWS' in selected_tables
    need_locations = 'GOOGLE_LOCATIONS' in selected_tables or 'GOOGLE_REVIEWS' in selected_tables
    need_reviews = 'GOOGLE_REVIEWS' in selected_tables
    
    # Fetch accounts if ANY table is selected (they're the root of the hierarchy)
    if need_accounts:
        try:
            fetch_reason = "required for hierarchy" if 'GOOGLE_ACCOUNTS' not in selected_tables else "selected for migration"
            print(f"DEBUG: Fetching Google accounts ({fetch_reason})...")
            accounts = list_accounts_with_refresh(google)
            print(f"DEBUG: Retrieved {len(accounts)} accounts")
            if not accounts.empty:
                print(f"DEBUG: Accounts DataFrame columns: {list(accounts.columns)}")
                print(f"DEBUG: Accounts DataFrame shape: {accounts.shape}")
            else:
                print("DEBUG: Accounts DataFrame is empty")
            # Only mark as successful if it was actually selected for saving
            if 'GOOGLE_ACCOUNTS' in selected_tables:
                successful_tables.append('GOOGLE_ACCOUNTS')
        except Exception as e:
            print(f"DEBUG: ERROR fetching accounts: {str(e)}")
            failed_tables.append('GOOGLE_ACCOUNTS')
            
            # Provide more specific error message for authentication issues
            if "400" in str(e) and "Bad Request" in str(e):
                error_msg = "Authentication failed: Google OAuth tokens are invalid or expired. Please re-authenticate through the OAuth flow."
                errors.append(f"accounts: {error_msg}")
                _print_reauth_instructions()
            elif "401" in str(e):
                error_msg = "Authentication failed: Access token is invalid. Please re-authenticate through the OAuth flow."
                errors.append(f"accounts: {error_msg}")
                _print_reauth_instructions()
            else:
                error_msg = str(e)
                errors.append(f"accounts: {error_msg}")
            
            # Log error to log and files
            if connection_id and cmp_id:
                error_context = {
                    "table_name": "GOOGLE_ACCOUNTS",
                    "service_name": "Google Reviews to Snowflake",
                    "error_type": "TABLE_FETCH_FAILED",
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "job_name": job_name,
                    "migration_session_id": migration_session_id,
                    "session": session,
                    "connection_name": connection_name,
                    "company_name": company_name,
                    "log_id": log_id
                }
                
                error_result = handle_service_error(
                    error=e,
                    context=error_context,
                    log_files=log_files,
                    send_email=False  # Don't send individual emails
                )
                
                if error_result["log_logged"]:
                    print(f"✅ Error logged to log for GOOGLE_ACCOUNTS")
                if error_result["files_logged"]:
                    print(f"📄 Error logged to files for GOOGLE_ACCOUNTS")
            
            accounts = pd.DataFrame()
    else:
        print("DEBUG: No tables selected that require accounts - skipping account fetch")
    
    # Fetch locations if GOOGLE_LOCATIONS or GOOGLE_REVIEWS is selected
    if need_locations:
        try:
            fetch_reason = "required for reviews" if 'GOOGLE_LOCATIONS' not in selected_tables else "selected for migration"
            print(f"DEBUG: Fetching Google locations ({fetch_reason})...")
            locations = list_locations_with_refresh(accounts, google)
            print(f"DEBUG: Retrieved {len(locations)} locations")
            # Only mark as successful if it was actually selected for saving
            if 'GOOGLE_LOCATIONS' in selected_tables:
                successful_tables.append('GOOGLE_LOCATIONS')
        except Exception as e:
            print(f"DEBUG: ERROR fetching locations: {str(e)}")
            failed_tables.append('GOOGLE_LOCATIONS')
            errors.append(f"locations: {str(e)}")
            
            # Log error
            if connection_id and cmp_id:
                error_context = {
                    "table_name": "GOOGLE_LOCATIONS",
                    "service_name": "Google Reviews to Snowflake",
                    "error_type": "TABLE_FETCH_FAILED",
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "job_name": job_name,
                    "migration_session_id": migration_session_id,
                    "session": session,
                    "log_id": log_id
                }
                handle_service_error(error=e, context=error_context, log_files=log_files, send_email=False)
            
            locations = pd.DataFrame()
    else:
        print("DEBUG: No tables selected that require locations - skipping location fetch")
    
    # Fetch reviews if GOOGLE_REVIEWS is selected
    if need_reviews:
        try:
            print("DEBUG: Fetching Google reviews (selected for migration)...")
            # Fetch ALL reviews unfiltered — filtering applied once via _filter_reviews_by_scope()
            reviews, full_fetch_success = list_reviews_with_refresh(locations, google)
            print(f"DEBUG: Retrieved {len(reviews)} raw reviews (pre-filter), full_fetch_success={full_fetch_success}")
            # Only mark as successful if it was actually selected for saving
            if 'GOOGLE_REVIEWS' in selected_tables:
                successful_tables.append('GOOGLE_REVIEWS')
        except Exception as e:
            print(f"DEBUG: ERROR fetching reviews: {str(e)}")
            failed_tables.append('GOOGLE_REVIEWS')
            errors.append(f"reviews: {str(e)}")
            
            # Log error
            if connection_id and cmp_id:
                error_context = {
                    "table_name": "GOOGLE_REVIEWS",
                    "service_name": "Google Reviews to Snowflake",
                    "error_type": "TABLE_FETCH_FAILED",
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "job_name": job_name,
                    "migration_session_id": migration_session_id,
                    "session": session,
                    "log_id": log_id
                }
                handle_service_error(error=e, context=error_context, log_files=log_files, send_email=False)
            
            reviews = pd.DataFrame()
            full_fetch_success = False   # fetch failed; skip deletion detection
    else:
        print("DEBUG: Skipping Google reviews - not selected")
        full_fetch_success = False       # no fetch attempted; skip deletion detection

    if not reviews.empty:
        print("DEBUG: Processing reviews data...")
        rating_mapping = {'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4, 'FIVE': 5, 'SIX': 6, 'SEVEN': 7, 'EIGHT': 8, 'NINE': 9, 'TEN': 10}
        if 'starRating' in reviews.columns:
            reviews['RatingValue'] = reviews['starRating'].map(rating_mapping)
            print("DEBUG: Added RatingValue column mapping")
        
        # For now, we'll determine the filtering strategy after we check table existence in Snowflake
        # Store the original reviews count for logging
        original_reviews_count = len(reviews)
        print(f"DEBUG: Total reviews fetched from API: {original_reviews_count}")
        
        # We'll apply filtering later after checking table existence
    else:
        print("DEBUG: No reviews to process")

    print("DEBUG: Connecting to Snowflake...")
    conn = _sf_connect(snowflake_cfg)
    cur = conn.cursor()
    
    # Create SQLAlchemy engine for progress tracking
    print("DEBUG: Creating SQLAlchemy engine for progress tracking...")
    snowflake_engine = destination_connection_function(snowflake_cfg)
    
    # Track if GCS storage integration has been checked (one-time check)
    is_integration_checked = False
    
    try:
        print(f"DEBUG: Using database: {snowflake_cfg['database']}")
        cur.execute(f"USE DATABASE {snowflake_cfg['database']}")
        
        # Create schema if it doesn't exist
        schema_name = snowflake_cfg.get('schema')
        if not schema_name or schema_name == 'None':
            raise ValueError("Schema is required for Google Reviews operations. Please ensure your connection has a valid schema configured.")
        print(f"DEBUG: Checking if schema '{schema_name}' exists...")
        try:
            cur.execute(f"USE SCHEMA {schema_name}")
            print(f"DEBUG: Schema '{schema_name}' exists, using it")
        except Exception as e:
            print(f"DEBUG: Schema '{schema_name}' does not exist, creating it...")
            try:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
                print(f"DEBUG: Schema '{schema_name}' created successfully")
                cur.execute(f"USE SCHEMA {schema_name}")
                print(f"DEBUG: Now using schema: {schema_name}")
            except Exception as create_error:
                print(f"DEBUG: ERROR creating schema '{schema_name}': {str(create_error)}")
                raise create_error
        
        # Use role only if it's provided
        if snowflake_cfg.get('role'):
            print(f"DEBUG: Using role: {snowflake_cfg['role']}")
            cur.execute(f"USE ROLE {snowflake_cfg['role']}")
        else:
            print("DEBUG: No role specified, using default role")
        
        # ============================================================================
        # GOOGLE_ACCOUNTS: Full load table (no delta) - use staging + merge
        # ============================================================================
        if 'GOOGLE_ACCOUNTS' in selected_tables:
            print("=" * 80)
            print("DEBUG: Processing GOOGLE_ACCOUNTS table (FULL LOAD - no delta)")
            print("=" * 80)
            
            if not accounts.empty:
                table_start_time = datetime.utcnow()
                # Normalize column names (replace dots with underscores, convert to UPPERCASE)
                if any('.' in c for c in accounts.columns):
                    accounts.columns = [c.replace('.', '_') for c in accounts.columns]
                    print(f"DEBUG: Replaced dots in accounts column names")
                
                # Convert to UPPERCASE (Snowflake standard)
                accounts.columns = [c.upper() for c in accounts.columns]
                accounts = _add_spect_metadata_columns(accounts)
                print(f"DEBUG: Accounts column names (UPPERCASE): {list(accounts.columns)}")
                
                # Step 1: Create staging table
                print(f"DEBUG: Creating staging table with accounts DataFrame columns: {list(accounts.columns)}")
                staging_table = _create_staging_table(cur, 'GOOGLE_ACCOUNTS', accounts, wide_text=False)
                
                # Step 2: Insert data into staging table via COPY INTO
                is_integration_checked = _insert_into_staging_copy(
                    cur, accounts, staging_table, 'GOOGLE_ACCOUNTS', 
                    connection_id, cmp_id, is_integration_checked,
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    snowflake_engine=snowflake_engine
                )
                
                # Step 3: MERGE from staging to target
                # Primary key: 'name' (Google account identifier)
                # Only drops table if drop_and_reload=True, otherwise preserves existing data
                _merge_from_staging_full_load(cur, staging_table, 'GOOGLE_ACCOUNTS', primary_key='name', drop_table=drop_and_reload)
                
                # Step 4: Cleanup staging table
                _cleanup_staging_table(cur, staging_table)
                
                rows_inserted = len(accounts)
                columns_count = len(accounts.columns)
                print(f"DEBUG: Successfully loaded {rows_inserted} accounts via staging table")
                
                 # Log data transfer
                if cmp_id and connection_id and user_id and session:
                    table_end_time = datetime.utcnow()
                    try:
                        data_transfer_logs(
                            cmp_id, connection_id, user_id, 'GOOGLE_ACCOUNTS',
                            table_start_time, table_end_time,
                            columns_count, session,
                            rows_inserted, 0, 0, 0,
                            job_name=job_name
                        )
                    except Exception as log_err:
                        print(f"⚠️ Failed to log data transfer for GOOGLE_ACCOUNTS: {log_err}")
            else:
                print("DEBUG: No accounts data to insert")
        else:
            print("DEBUG: Skipping GOOGLE_ACCOUNTS table - not selected")
        
        # ============================================================================
        # GOOGLE_LOCATIONS: Full load table (no delta) - use staging + merge
        # ============================================================================
        if 'GOOGLE_LOCATIONS' in selected_tables:
            print("=" * 80)
            print("DEBUG: Processing GOOGLE_LOCATIONS table (FULL LOAD - no delta)")
            print("=" * 80)
            
            if not locations.empty:
                table_start_time = datetime.utcnow()
                # Normalize column names (replace dots with underscores, convert to UPPERCASE)
                if any('.' in c for c in locations.columns):
                    locations.columns = [c.replace('.', '_') for c in locations.columns]
                    print(f"DEBUG: Replaced dots in locations column names")
                
                # Convert to UPPERCASE (Snowflake standard)
                locations.columns = [c.upper() for c in locations.columns]
                locations = _add_spect_metadata_columns(locations)
                print(f"DEBUG: Locations column names (UPPERCASE): {list(locations.columns)}")
                
                # Step 1: Create staging table
                staging_table = _create_staging_table(cur, 'GOOGLE_LOCATIONS', locations, wide_text=False)
                
                # Step 2: Insert data into staging table via COPY INTO
                is_integration_checked = _insert_into_staging_copy(
                    cur, locations, staging_table, 'GOOGLE_LOCATIONS', 
                    connection_id, cmp_id, is_integration_checked,
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    snowflake_engine=snowflake_engine
                )
                
                # Step 3: MERGE from staging to target
                # Primary key: 'name' (Google location identifier)
                # Only drops table if drop_and_reload=True, otherwise preserves existing data
                _merge_from_staging_full_load(cur, staging_table, 'GOOGLE_LOCATIONS', primary_key='name', drop_table=drop_and_reload)
                
                # Step 4: Cleanup staging table
                _cleanup_staging_table(cur, staging_table)
                
                rows_inserted = len(locations)
                columns_count = len(locations.columns)
                print(f"DEBUG: Successfully loaded {rows_inserted} locations via staging table")
                
                 # Log data transfer
                if cmp_id and connection_id and user_id and session:
                    table_end_time = datetime.utcnow()
                    try:
                        data_transfer_logs(
                            cmp_id, connection_id, user_id, 'GOOGLE_LOCATIONS',
                            table_start_time, table_end_time,
                            columns_count, session,
                            rows_inserted, 0, 0, 0,
                            job_name=job_name
                        )
                    except Exception as log_err:
                        print(f"⚠️ Failed to log data transfer for GOOGLE_LOCATIONS: {log_err}")
            else:
                print("DEBUG: No locations data to insert")
        else:
            print("DEBUG: Skipping GOOGLE_LOCATIONS table - not selected")
        
        if 'GOOGLE_REVIEWS' in selected_tables:
            print("DEBUG: Processing GOOGLE_REVIEWS table...")
            # Check if Reviews table exists and determine sync strategy
            reviews_table_exists = _check_table_exists(cur, 'GOOGLE_REVIEWS')
            
            # Store original reviews count for logging
            original_reviews_count = len(reviews) if not reviews.empty else 0

            # ============================================================================
            # 🔄 UNIFIED SYNC FILTER
            # ============================================================================
            # Single authoritative filter via _filter_reviews_by_scope().
            # Replaces the old double-filter (pre-fetch + post-fetch with different fields).
            # updateTime is hardcoded as the sole change-detection field.
            # ============================================================================

            # Safety cutoff (used only for delta; not applied to full reload / rebuild)
            try:
                interval_seconds = max(int(safety_interval or 120), 120)
            except (TypeError, ValueError):
                interval_seconds = 120
            safety_cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)
            ).replace(microsecond=0)

            # Resolve connector scope parameters.
            #
            # connector_start_date — the static origin date from the source config
            #   (e.g. '2020-01-01'). This is the MINIMUM lower bound and is used for
            #   full loads and as a floor for delta clamps.
            #
            # last_max_update_time — the last-sync timestamp, used as the delta lower
            #   bound.  The router passes the last sync time as the `start_date`
            #   function parameter (derived from source_table_selection.last_checked_date).
            #   We use that in preference to google.get('last_max_update_time') which
            #   would require a separate persistence mechanism that is not yet reliable.
            connector_start_date = google.get('start_date')   # static origin, e.g. '2020-01-01'
            last_max_update_time = (
                start_date                          # ← router passes last sync time here
                if start_date and start_date != connector_start_date
                else google.get('last_max_update_time')  # fallback (legacy / manual overrides)
            )

            # ── Capture full live reviewId set BEFORE scope filter ────────────
            # This represents every reviewId Google currently returns and is
            # used by _apply_missing_streak_deletion to detect absent reviews.
            all_api_review_ids: set = set()
            if not reviews.empty and 'reviewId' in reviews.columns:
                all_api_review_ids = set(reviews['reviewId'].dropna().tolist())
            print(f"DEBUG: Pre-filter reviewId set size: {len(all_api_review_ids)}")

            # Delta capability:
            # GOOGLE_REVIEWS always supports delta via updateTime field.
            # Delta sync activates when the table already exists AND we are not
            # doing a forced Drop & Reload. On initial load or any forced rebuild,
            # is_delta_sync=False and the full scope (start_date → now) is used.
            is_delta_sync = (
                reviews_table_exists       # table must exist from a prior load
                and not drop_and_reload    # not a forced rebuild
            )

            # When is_delta_sync but last_max_update_time is not persisted yet,
            # treat connector_start_date as the effective lower bound by setting
            # last_max_update_time to it. This prevents a full re-load on the
            # first delta run after initial setup.
            effective_last_max = last_max_update_time
            if is_delta_sync and effective_last_max is None and connector_start_date:
                effective_last_max = connector_start_date
                print(f"DEBUG: No last_max_update_time persisted — using "
                      f"connector_start_date as delta lower bound: {effective_last_max}")

            print(f"DEBUG: Sync mode — drop_and_reload={drop_and_reload}, "
                  f"table_exists={reviews_table_exists}, is_delta={is_delta_sync}")
            print(f"DEBUG: connector_start_date={connector_start_date}, "
                  f"last_max_update_time={last_max_update_time}, "
                  f"effective_last_max={effective_last_max}")

            # Always initialise table_start_time here so it is defined for both
            # the success path and the empty-result logging path below.
            table_start_time = datetime.utcnow()

            if not reviews.empty:
                # Apply the single authoritative scope filter
                reviews = _filter_reviews_by_scope(
                    reviews,
                    connector_start_date=connector_start_date,
                    last_max_update_time=effective_last_max,
                    safety_cutoff=safety_cutoff,
                    overlap_seconds=300,
                    is_delta=is_delta_sync,
                )
                print(f"DEBUG: Reviews after scope filter: {len(reviews)}")

            # Warn when filter emptied results for a table that does not yet exist
            if not reviews_table_exists and reviews.empty and original_reviews_count > 0:
                print(f"DEBUG: WARNING — Table doesn't exist but filter returned 0 rows "
                      f"from {original_reviews_count} total reviews. "
                      f"Consider running a full sync without a start_date.")
            # ============================================================================
            
            # ============================================================================
            # 🔄 STAGING TABLE LOGIC 
            # ============================================================================
            # Determine sync strategy:
            # 1. If table is delta-capable AND start_date provided → USE MERGE (delta sync)
            # ============================================================================
            
            print("=" * 80)
            print("DEBUG: Processing GOOGLE_REVIEWS table")
            print("=" * 80)
            
            if not reviews.empty:
                # Normalize column names (replace dots with underscores, convert to UPPERCASE)
                if any('.' in c for c in reviews.columns):
                    reviews.columns = [c.replace('.', '_') for c in reviews.columns]
                    print(f"DEBUG: Replaced dots in reviews column names")
                
                # Convert to UPPERCASE (Snowflake standard)
                reviews.columns = [c.upper() for c in reviews.columns]
                reviews = _add_spect_metadata_columns(reviews)
                print(f"DEBUG: Reviews column names (UPPERCASE): {list(reviews.columns)}")
                
                # Step 1: Create target table if it doesn't exist
                cols = list(reviews.columns)
                def infer_review_col(col: str) -> str:
                    col_u = col.upper()
                    if col_u == "_SPECT_SYNCED":
                        return f'"{col}" TIMESTAMP_TZ'
                    if col_u == "_SPECT_DELETED":
                        return f'"{col}" BOOLEAN'
                    if col_u == "LAST_SEEN_IN_API":
                        return f'"{col}" TIMESTAMP_NTZ'
                    if col_u == "MISSING_COUNT":
                        return f'"{col}" NUMBER(10,0)'
                    if 'time' in col.lower():
                        return f'"{col}" TIMESTAMP_NTZ'
                    if 'comment' in col.lower():
                        return f'"{col}" TEXT'
                    return f'"{col}" VARCHAR(512)'
                
                column_defs = ",\n  ".join([infer_review_col(c) for c in cols])
                cur.execute(f"CREATE TABLE IF NOT EXISTS GOOGLE_REVIEWS (\n  {column_defs}\n)")
                print("DEBUG: Target table GOOGLE_REVIEWS created/verified")
                
                # Step 2: Create staging table
                staging_table = _create_staging_table(cur, 'GOOGLE_REVIEWS', reviews, wide_text=True)
                
                # Step 3: Insert data into staging table via COPY INTO 
                is_integration_checked = _insert_into_staging_copy(
                    cur, reviews, staging_table, 'GOOGLE_REVIEWS', 
                    connection_id, cmp_id, is_integration_checked,
                    migration_session_id=migration_session_id,
                    job_name=job_name,
                    snowflake_engine=snowflake_engine
                )
                
                # Step 4: MERGE for all scenarios (upsert handles both delta and full reload)
                # is_delta_sync resolved in the filter block above
                if is_delta_sync:
                    print("=" * 80)
                    print("DEBUG: Using MERGE strategy (DELTA SYNC — updateTime field, hardcoded)")
                    print(f"DEBUG: Primary key: reviewId")
                    print(f"DEBUG: Operation: UPDATE existing + INSERT new reviews")
                    print("=" * 80)
                else:
                    reload_reason = []
                    if drop_and_reload:
                        reload_reason.append("drop_and_reload=True")
                    if not reviews_is_delta:
                        reload_reason.append("table not delta-capable")
                    if not reviews_table_exists:
                        reload_reason.append("table doesn't exist yet")
                    if last_max_update_time is None:
                        reload_reason.append("no last_max_update_time persisted")
                    print("=" * 80)
                    print("DEBUG: Using MERGE strategy (FULL RELOAD)")
                    print(f"DEBUG: Reason: {', '.join(reload_reason) or 'unspecified'}")
                    print(f"DEBUG: Primary key: reviewId")
                    print("=" * 80)

                _merge_staging_to_target(cur, staging_table, 'GOOGLE_REVIEWS', primary_key='reviewId')

                # ── Missing-streak soft deletion ──────────────────────────────
                # Runs after MERGE so seen rows already have MISSING_COUNT=0.
                # Guard: only execute when the full API fetch succeeded.
                # Skipped on Drop & Reload (full rebuild resets all counts naturally).
                if full_fetch_success and all_api_review_ids and not drop_and_reload and reviews_table_exists:
                    try:
                        deletion_stats = _apply_missing_streak_deletion(
                            cur,
                            all_api_review_ids=all_api_review_ids,
                            target_table='GOOGLE_REVIEWS',
                            missing_streak_n=3,
                        )
                        print(
                            f"[DELETION] Run complete — "
                            f"seen={deletion_stats['seen']} | "
                            f"total_active={deletion_stats['total_active_rows']} | "
                            f"candidate_missing={deletion_stats['candidate_missing']} | "
                            f"rows_incremented={deletion_stats['rows_incremented']} | "
                            f"soft_deleted_now={deletion_stats['soft_deleted_now']} | "
                            f"mass_deletion_blocked={deletion_stats['mass_deletion_blocked']} | "
                            f"streak_threshold={deletion_stats['streak_threshold']}"
                        )
                    except Exception as del_err:
                        print(f"\u26a0\ufe0f Deletion streak update failed (non-fatal): {del_err}")
                elif not full_fetch_success:
                    print("[DELETION] Skipped — incomplete API fetch (partial account failure detected)")
                elif drop_and_reload:
                    print("[DELETION] Skipped — Drop & Reload rebuilds all rows with MISSING_COUNT=0 naturally")
                elif not reviews_table_exists:
                    print("[DELETION] Skipped — table did not exist before this run (initial load)")

                # ── Persist max(updateTime) for next delta run ──────────────────────────
                # Only persists when reviews were actually processed; skips on empty result
                # to prevent boundary regression.
                if connection_id and not reviews.empty:
                    ut_col = next((c for c in reviews.columns if c.upper() == 'UPDATETIME'), None)
                    if ut_col:
                        max_ut = reviews[ut_col].max()
                        if pd.notna(max_ut):
                            _persist_last_max_update_time(connection_id, max_ut)
                        else:
                            print("DEBUG: max(updateTime) is NaT — skipping persist to avoid boundary regression")
                    else:
                        print("DEBUG: UPDATETIME column not found in reviews — skipping last_max_update_time persist")
                else:
                    print("DEBUG: Empty reviews after filter — last_max_update_time NOT updated (preserves prior boundary)")
                
                # Step 5: Cleanup staging table
                _cleanup_staging_table(cur, staging_table)
                
                rows_inserted = len(reviews)
                columns_count = len(reviews.columns)
                print(f"DEBUG: Successfully processed {rows_inserted} reviews via staging table")
                
                # Log data transfer
                if cmp_id and connection_id and user_id and session:
                    table_end_time = datetime.utcnow()
                    try:
                        data_transfer_logs(
                            cmp_id, connection_id, user_id, 'GOOGLE_REVIEWS',
                            table_start_time, table_end_time,
                            columns_count, session,
                            rows_inserted, 0, 0, 0,
                            job_name=job_name
                        )
                    except Exception as log_err:
                        print(f"⚠️ Failed to log data transfer for GOOGLE_REVIEWS: {log_err}")
            else:
                print("DEBUG: No reviews data to insert")
                # Log even when 0 records are migrated during delta sync
                if cmp_id and connection_id and user_id and session:
                    # FIX: Call data_transfer_logs even with 0 records to ensure Data_Transfer record is created
                    table_end_time = datetime.utcnow()
                    columns_count = len(GOOGLE_REVIEWS_TABLE_FIELDS['GOOGLE_REVIEWS'])  # 10 columns
                    try:
                        data_transfer_logs(
                            cmp_id, connection_id, user_id, 'GOOGLE_REVIEWS',
                            table_start_time, table_end_time,
                            columns_count, session,
                            0, 0, 0, 0,  # 0 records inserted/updated/deleted/failed
                            job_name=job_name
                        )
                        print("✅ Data_Transfer record created for GOOGLE_REVIEWS (0 records)")
                    except Exception as log_err:
                        print(f"⚠️ Failed to log data transfer for GOOGLE_REVIEWS: {log_err}")
        else:
            print("DEBUG: Skipping GOOGLE_REVIEWS table - not selected")
        
    finally:
        print("DEBUG: Closing Snowflake connection...")
        cur.close()
        conn.close()

    result: Dict[str, Any] = {
        'accounts_rows': 0 if accounts.empty else len(accounts),
        'locations_rows': 0 if locations.empty else len(locations),
        'reviews_rows': 0 if reviews.empty else len(reviews),
        'successful_tables': successful_tables,
        'failed_tables': failed_tables
    }
    
    print(f"DEBUG: Final result summary:")
    print(f"DEBUG: - Accounts: {result['accounts_rows']} rows")
    print(f"DEBUG: - Locations: {result['locations_rows']} rows")
    print(f"DEBUG: - Reviews: {result['reviews_rows']} rows")
    print(f"DEBUG: - Successful tables: {successful_tables}")
    print(f"DEBUG: - Failed tables: {failed_tables}")
    
    # If we refreshed token during flow, include updated access_token so caller can persist it
    if google.get('access_token') != access_token:
        print("DEBUG: Access token was refreshed during the process")
        result['updated_access_token'] = google.get('access_token')
    
    if errors:
        print(f"DEBUG: Errors encountered: {errors}")
        result['errors'] = errors
    else:
        print("DEBUG: No errors encountered")
    
    # ============================================================================
    # 🎯 MIGRATION COMPLETION LOGGING 
    # ============================================================================
    # Log migration summary and completion message to log
    # ============================================================================
    if connection_id and cmp_id:
        total_tables = len(selected_tables)
        successful_count = len(successful_tables)
        failed_count = len(failed_tables)
        
        # Only count rows for SELECTED tables (not hierarchy dependencies)
        total_rows = 0
        if 'GOOGLE_ACCOUNTS' in selected_tables:
            total_rows += result['accounts_rows']
        if 'GOOGLE_LOCATIONS' in selected_tables:
            total_rows += result['locations_rows']
        if 'GOOGLE_REVIEWS' in selected_tables:
            total_rows += result['reviews_rows']
        
        # Calculate execution time 
        execution_time = time.time() - start_time
        
        # Create completion message for cleanup (reuse the standardized format)
        completion_message = (
            f"Migration completed successfully in {execution_time:.2f} seconds, "
            f"Total rows: {total_rows:,}, "
            f"Tables: {successful_count}/{total_tables} successful"
        )
        if failed_count > 0:
            completion_message += f", {failed_count} failed"
        completion_message += "."
        
        # Clean up celery jobs to prevent stuck job scenarios
        try:
            from utils.celery_cleanup_utils import handle_migration_completion
            cleanup_result = handle_migration_completion(
                connection_name=connection_name or f"GOOGLE_REVIEWS_{connection_id}",
                job_name=job_name,
                completion_message=completion_message
            )
            
            if cleanup_result['status'] == 'success' and cleanup_result['cleaned_jobs']:
                print(f"🧹 Cleaned up {len(cleanup_result['cleaned_jobs'])} stuck celery jobs for Google Reviews migration")
            elif cleanup_result['status'] == 'error':
                print(f"⚠️ Cleanup warning for Google Reviews: {cleanup_result['message']}")
                
        except Exception as cleanup_error:
            # Don't fail the migration if cleanup fails - just log the warning
            print(f"⚠️ Failed to cleanup celery jobs: {cleanup_error}")
            import traceback
            traceback.print_exc()            

        # Update MigrationRecord status to completed
        # This removes the "Migration in progress" notification from UI
        if migration_session_id and job_name:
            try:
                from utils.database_utils import postgres_connection
                conn = postgres_connection()
                cursor = conn.cursor()
                
                # Update migration record status using normalized lowercase value.
                sql_update_migration_record = """
                    UPDATE migration_record
                    SET status = %s, updated_at = NOW()
                    WHERE job_name = %s
                """
                cursor.execute(sql_update_migration_record, ('completed', job_name))
                conn.commit()
                cursor.close()
                conn.close()
                print(f"✅ Migration record updated to completed for job_name: {job_name}")
            except Exception as e:
                print(f"⚠️ Failed to update migration record: {e}")

        # --- CONSOLIDATED WEB SOCKET TRIGGERS ---
        # Triggered for BOTH the main flow and the fallback scenario to ensure UI consistency
        try:
            from utils.websocket_utils import trigger_ws_status_update, trigger_connection_ws_status_update
            
            # 1. Log completion to Activity Feed (Triggers Activity Log WebSocket update)
            log_migration_completion(
                execution_time=execution_time,
                total_rows=total_rows,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                session=session,
                total_tables=total_tables,
                successful_tables=successful_count,
                failed_tables=failed_count,
                job_name=job_name,
                migration_record_id=None,
                log_id=log_id
            )
            
            # 2. Refresh UI Status banner/spinner (if session exists)
            if migration_session_id:
                trigger_ws_status_update(migration_session_id)
                
            # 3. Refresh individual table statuses (if connection exists)
            if connection_id:
                trigger_connection_ws_status_update(connection_id)
                
            print(f"✅ All migration completion WebSockets triggered for connection {connection_id}")
            
        except Exception as ws_error:
            print(f"⚠️ WebSocket triggers failed (non-fatal): {ws_error}")
    # ============================================================================
    
    print("DEBUG: sync_google_reviews_to_snowflake completed successfully")
    return result


def list_google_reviews_tables(google: Dict[str, Any], connection_id: Optional[int] = None, job_name: Optional[str] = None) -> Dict[str, Any]:
    """
    List available tables/entities from Google Reviews API.
    Returns static information about the 3 hardcoded tables that can be migrated.
    
    🔄 Uses database-driven delta field detection:
    - Fetches delta fields from source_delta_table based on source_id
    - Dynamically determines which tables support incremental sync
    """
    print("DEBUG: Starting list_google_reviews_tables")
    
    # Get delta fields from database if connection_id is provided 
    delta_fields = []
    source_id = None
    if connection_id:
        source_id = get_source_id_from_connection(connection_id)
        if source_id:
            delta_fields = get_delta_fields_for_source(source_id)
            print(f"🔍 Found {len(delta_fields)} delta fields for Google Reviews source_id {source_id}: {delta_fields}")
        else:
            print(f"⚠️ Could not find source_id for connection_id {connection_id}")
    else:
        print("DEBUG: No connection_id provided - delta field detection skipped")
    
    # Use shared table field structures (defined at module level)
    accounts_fields = GOOGLE_REVIEWS_TABLE_FIELDS['GOOGLE_ACCOUNTS']
    locations_fields = GOOGLE_REVIEWS_TABLE_FIELDS['GOOGLE_LOCATIONS']
    reviews_fields = GOOGLE_REVIEWS_TABLE_FIELDS['GOOGLE_REVIEWS']
    
    # Check which tables have delta fields 
    accounts_is_delta = check_table_has_delta_fields(accounts_fields, delta_fields) if delta_fields else False
    locations_is_delta = check_table_has_delta_fields(locations_fields, delta_fields) if delta_fields else False
    reviews_is_delta = check_table_has_delta_fields(reviews_fields, delta_fields) if delta_fields else False
    
    # Determine delta timestamp field for reviews if it's delta-capable
    reviews_delta_field = None
    if reviews_is_delta and delta_fields:
        # Find which delta field exists in reviews table
        for field in delta_fields:
            if field in reviews_fields:
                reviews_delta_field = field
                print(f"✅ GOOGLE_REVIEWS marked as delta table - using field: {reviews_delta_field}")
                break
    
    if accounts_is_delta:
        print(f"✅ GOOGLE_ACCOUNTS marked as delta table")
    if locations_is_delta:
        print(f"✅ GOOGLE_LOCATIONS marked as delta table")
    
    # Build result structure
    result = {
        'tables': [
            {
                'table_name': 'GOOGLE_ACCOUNTS',
                'description': 'Google Business accounts',
                'row_count': 0,  # Will be populated during actual migration
                'columns': list(accounts_fields.keys()),
                'is_delta': accounts_is_delta,  # Dynamically determined from database
                'delta_timestamp_field': None  # Accounts are always full reload
            },
            {
                'table_name': 'GOOGLE_LOCATIONS',
                'description': 'Business locations under Google accounts',
                'row_count': 0,  # Will be populated during actual migration
                'columns': list(locations_fields.keys()),
                'is_delta': locations_is_delta,  # Dynamically determined from database
                'delta_timestamp_field': None  # Locations are always full reload
            },
            {
                'table_name': 'GOOGLE_REVIEWS',
                'description': 'Customer reviews for business locations',
                'row_count': 0,  # Will be populated during actual migration
                'columns': list(reviews_fields.keys()),
                'is_delta': reviews_is_delta,  # ✅ Dynamically determined from database 
                'delta_timestamp_field': reviews_delta_field  # Field used for delta/incremental sync
            }
        ],
        'accounts_count': 0,
        'locations_count': 0,
        'reviews_count': 0,
        'errors': []
    }

    # Optional: Do a lightweight connection validation if access_token is provided
    access_token = google.get('access_token')
    if access_token:
        try:
            # Just validate the token with a simple API call
            accounts = list_accounts(access_token)
            result['accounts_count'] = len(accounts) if not accounts.empty else 0
            print(f"DEBUG: Connection validation successful - found {result['accounts_count']} accounts")
        except Exception as e:
            print(f"DEBUG: Connection validation failed: {str(e)}")
            # Don't add to errors since this is just validation
            # The actual migration will handle authentication properly

    # ✅ Update the log message to indicate schema refresh completion
    # Only trigger if this is actually a schema refresh operation (indicated by job_name containing 'listing_tables')
    if job_name and 'listing_tables' in job_name and connection_id:
        try:
            from utils.error_utils import update_migration_status_log
            execution_time = None
            try:
                if job_name:
                    conn_et = postgres_connection()
                    cursor_et = conn_et.cursor()
                    cursor_et.execute(
                        """
                        SELECT start_time
                        FROM migration_record
                        WHERE job_name = %s
                        ORDER BY created_at DESC
                        LIMIT 1;
                        """,
                        (job_name,),
                    )
                    row = cursor_et.fetchone()
                    start_time_dt = row[0] if row and row[0] else None
                    if isinstance(start_time_dt, datetime):
                        if start_time_dt.tzinfo is None:
                            execution_time = (datetime.utcnow() - start_time_dt).total_seconds()
                        else:
                            execution_time = (datetime.now(timezone.utc) - start_time_dt).total_seconds()
            except Exception:
                execution_time = None
            finally:
                try:
                    cursor_et.close()
                except Exception:
                    pass
                try:
                    conn_et.close()
                except Exception:
                    pass

            if execution_time is None:
                execution_time = 0.0

            completion_message = (
                f"Update schema completed successfully in {execution_time:.2f} seconds. "
                f"Updated {len(result['tables'])} tables."
            )
            update_migration_status_log(
                job_name=job_name,
                message=completion_message,
                status="S"  # Success status
            )
            try:
                from utils.websocket_utils import trigger_connection_activity_log_update
                trigger_connection_activity_log_update(connection_id)
            except Exception as activity_ws_error:
                print(f"Connection activity log WebSocket trigger failed (non-fatal): {activity_ws_error}")
        except Exception as log_error:
            print(f"⚠️ Update schema completion log update failed (non-fatal): {log_error}")

        # ✅ Trigger WebSocket update for schema refresh completion
        try:
            from utils.websocket_utils import trigger_update_schema_ws_status_update
            trigger_update_schema_ws_status_update(connection_id)
        except Exception as ws_error:
            print(f"⚠️ WebSocket trigger failed (non-fatal): {ws_error}")

    return result


