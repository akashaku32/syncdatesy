"""Shared Salesforce client: auth, Bulk API, field helpers, status/watermarks."""

import requests
import traceback as tb_module
import hashlib
import base64
import secrets
import redis
import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from simple_salesforce import Salesforce
import requests
import psycopg2
import os

import time
import psycopg2
from psycopg2 import OperationalError
from services.snowflake.service import normalize_pem_key
from datetime import datetime

# Import cleanup utility
from utils.celery_cleanup_utils import handle_migration_completion
from utils.deployment_config import get_public_domain
# Import unified data_transfer_logs function
from utils.database_utils import data_transfer_logs, parse_table_fields
from utils.snowflake_utils import drop_staging_table, normalize_name
from utils.simple_progress_tracker import SimpleProgressTracker

# =============================================================================
# MIGRATION FAILURE TRACKER CLASS
# =============================================================================


# --- consolidated imports from legacy (deduped extras) ---
import requests
import traceback as tb_module
import hashlib
import base64
import secrets
import redis
import json
from datetime import datetime, timedelta
from fastapi import HTTPException
from simple_salesforce import Salesforce
import psycopg2
import os
import time
from psycopg2 import OperationalError
from services.snowflake.service import normalize_pem_key
from datetime import datetime
from utils.celery_cleanup_utils import handle_migration_completion
from utils.database_utils import data_transfer_logs
from utils.snowflake_utils import drop_staging_table, normalize_name
from utils.simple_progress_tracker import SimpleProgressTracker
import snowflake.connector
from urllib.parse import quote
from typing import List
from models.salesforce_models import SalesforceMigration,SalesforceSnowflakeMigration
from datetime import datetime, timezone
import multiprocessing
from pathlib import Path
import re
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import pandas as pd
import asyncio
import csv
import io
from typing import List, Dict, Any, Optional
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
import ast
import math
from decimal import Decimal
from datetime import datetime, date, timezone
from typing import Dict, List, Any, Optional
import logging
from sqlalchemy import text
from sqlalchemy.sql import text, bindparam
from sqlalchemy.types import JSON as SQLAlchemyJSON
from dateutil import parser
import numpy as np
from typing import List, Dict, Any, Set
from google.cloud import storage
from google.api_core.exceptions import NotFound
from google.cloud import storage, firestore
from google.auth import compute_engine
import gc
from google.api_core.exceptions import ServiceUnavailable
from sqlalchemy import insert, Table, MetaData
from typing import List, Dict, Any
import pytz
from pandas.errors import OutOfBoundsDatetime
from typing import List, Dict
from datetime import timedelta
from utils.salesforce_dynamic_checker import (
    is_salesforce_object_queryable,
    initialize_dynamic_checker,
    batch_check_object_queryability
)
from typing import Optional, Dict



class MigrationFailureTracker:
    """
    Tracks failed tables during migration process for consolidated error reporting.
    Collects table failures and sends one consolidated email at the end of migration.
    """
    def __init__(self):
        self.failed_tables = []
        self.migration_start_time = datetime.now(timezone.utc)

    def add_failure(self, table_name: str, error_message: str, service_name: str = ""):
        """
        Add a failed table to the tracker.

        Args:
            table_name: Name of the failed table
            error_message: Error message/details
            service_name: Service name (e.g., "Snowflake to Salesforce")
        """
        failure_time = datetime.now(timezone.utc)
        failure = {
            "table_name": table_name,
            "error_message": error_message,
            "failure_time": failure_time,
            "service_name": service_name
        }
        self.failed_tables.append(failure)
        print(f"📝 Added failure for table '{table_name}' to migration tracker")

    def has_failures(self):
        """Check if there are any failed tables."""
        return len(self.failed_tables) > 0

    def get_failure_summary(self):
        """Get a summary of all failed tables."""
        return {
            "total_failures": len(self.failed_tables),
            "failed_tables": self.failed_tables,
            "migration_start_time": self.migration_start_time
        }

    @staticmethod
    def failed_table_names(failed_tables) -> set:
        """Extract table names from tracker entries (dict or str)."""
        names = set()
        for item in failed_tables or []:
            if isinstance(item, dict):
                name = item.get("table_name")
            else:
                name = item
            if name:
                names.add(str(name))
        return names

    def send_consolidated_error_email(self, connection_name: str, service_name: str, user_id: int, cmp_id: int, connection_id: int, job_name: str, migration_session_id: int, company_name: str, is_final_attempt: bool = True):
        """
        Send one consolidated email with all failed tables.
        ✅ ONLY sends on final retry attempt to avoid duplicate emails.
        """
        if not self.has_failures():
            print("✅ No failures to report - skipping consolidated error email")
            return
        
        # ✅ CHECK: Only send email on final retry attempt
        if not is_final_attempt:
            print(f"⏭️ SKIPPING consolidated error email (not final attempt) - {len(self.failed_tables)} failures tracked")
            return

        try:
            from utils.email_utils import send_error_notification

            # Create consolidated error message
            failure_summary = self.get_failure_summary()

            # Build detailed error message for each failed table
            error_details = []
            for failure in failure_summary["failed_tables"]:
                timestamp = failure["failure_time"].strftime("%Y-%m-%d %H:%M:%S UTC")
                error_details.append(f"""
🔴 Table: {failure['table_name']}
   🕒 Failed at: {timestamp}
   📋 Error: {failure['error_message']}
   ──────────────────────────────────""")

            consolidated_error_message = f"""Migration completed with {failure_summary['total_failures']} failed table(s).

FAILED TABLES SUMMARY:
{''.join(error_details)}

Migration started: {failure_summary['migration_start_time'].strftime('%Y-%m-%d %H:%M:%S UTC')}
Total tables processed: {failure_summary['total_failures']} failed"""

            # Send consolidated email
            send_error_notification(
                table_name="Migration Summary",  # Will be used in subject
                error_message=consolidated_error_message,
                connection_name=connection_name,
                service_name=service_name,
                user_id=user_id,
                company_id=cmp_id,  # Include company_id for CommunicationSupport emails
                is_consolidated=True,  # This will create the simplified subject
                additional_context={
                    "cmp_id": cmp_id,
                    "company_id": cmp_id,
                    "connection_id": connection_id,
                    "job_name": job_name,
                    "migration_session_id": migration_session_id,
                    "company_name": company_name,
                    "total_failed_tables": failure_summary["total_failures"],
                    "failed_tables_list": [f["table_name"] for f in failure_summary["failed_tables"]]
                }
            )

            print(f"📧 Sent consolidated error email for {failure_summary['total_failures']} failed tables")

        except Exception as email_error:
            print(f"⚠️ Failed to send consolidated error notification email: {email_error}")
            tb_module.print_exc()


DEMO_STORAGE = os.environ["SNOWFLAKE_STORAGE_INTEGRATION"]


GCS_BUCKET_URL = os.environ["GCS_BUCKET_URL"]


BUCKET_NAME = os.environ["GCS_BUCKET_NAME"]


PROJECT_ID = os.environ["GCP_PROJECT_ID"]


CUSTOM_DOMAIN = get_public_domain()


def postgres_connection():
    """
    Create a PostgreSQL connection with retry logic and .env file support
    """
    from datasyncher_connections.postgres import create_postgres_connection

    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("📄 Loaded environment variables from .env file")
    except ImportError:
        print("⚠️ python-dotenv not installed, using system environment variables only")
    except Exception as e:
        print(f"⚠️ Could not load .env file: {e}")

    connection = create_postgres_connection(retries=3, retry_delay=2, validate=True)
    print("✅ Database connection established")
    return connection


SALESFORCE_AUTH_URL = "https://login.salesforce.com/services/oauth2/authorize"


SALESFORCE_TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"


SALESFORCE_SANDBOX_AUTH_URL= "https://test.salesforce.com/services/oauth2/authorize"


SALESFORCE_SANDBOX_TOKEN_URL = "https://test.salesforce.com/services/oauth2/token"


REDIRECT_URI = f"https://{CUSTOM_DOMAIN}/api/v1/salesforce/callback"


SANDBOX_REDIRECT_URI = f"https://{CUSTOM_DOMAIN}/api/v1/salesforcesandbox/callback"


SOURCE_REDIRECT_URI = f"https://{CUSTOM_DOMAIN}/api/v1/source/salesforce/callback"


SOURCE_SANDBOX_REDIRECT_URI = f"https://{CUSTOM_DOMAIN}/api/v1/source/salesforcesandbox/callback"


def _get_redis_client():
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis.from_url(redis_url, decode_responses=True)


redis_client = _get_redis_client()


def generate_pkce_pair():
    """Generate PKCE code_verifier and code_challenge."""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    return code_verifier, code_challenge


def get_salesforce_authorize_url(state: str, data, environment: str, is_source: bool) -> str:
    """Generate Salesforce authorization URL."""
    print("Type of data:", type(data))
    print("Data content:", data)

    # Convert data to a dictionary if it's a string
    if isinstance(data, str):
        try:
            data = json.loads(data)  # Parse JSON string to dictionary
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON string provided for 'data'")

    # Ensure data is a dictionary after conversion
    if not isinstance(data, dict):
        raise ValueError("'data' must be a dictionary or a JSON string representing a dictionary")

    # Generate PKCE pair
    code_verifier, code_challenge = generate_pkce_pair()

    # Store PKCE verifier in Redis (5-minute expiration)
    redis_client.setex(f"pkce:{state}", 300, code_verifier)

    # Extract client details
    CLIENT_ID = data.get("CLIENT_ID")
    CLIENT_SECRET = data.get("CLIENT_SECRET")
    custom_domain = data.get("CUSTOM_DOMAIN")

    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("Missing 'CLIENT_ID' or 'CLIENT_SECRET' in data")
    if environment=="sandbox":
        if is_source:
            return f"{SALESFORCE_SANDBOX_AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={SOURCE_SANDBOX_REDIRECT_URI}&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"
        else:

            return f"{SALESFORCE_SANDBOX_AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={SANDBOX_REDIRECT_URI}&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"
    else:
        if custom_domain=='':
            global SALESFORCE_AUTH_URL
            if is_source:
    # Construct the Salesforce authorization URL
                return f"{SALESFORCE_AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={SOURCE_REDIRECT_URI}&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"
            else:
                return f"{SALESFORCE_AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"
        else:
            if is_source:
    # Construct the Salesforce authorization URL
                return f"{SALESFORCE_AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={SOURCE_REDIRECT_URI}&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"
            else:
                return f"{SALESFORCE_AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"


def get_access_token(code: str, state: str,data:dict,environment:str,is_source:bool):
    """Exchange authorization code for access and refresh tokens."""
    
    print("Checking Redis for state:", state)
    code_verifier = redis_client.get(f"pkce:{state}")
    if isinstance(data, str):
        try:
            data = json.loads(data)  # Parse JSON string to dictionary
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON string provided for 'data'")
    CLIENT_ID=data.get('CLIENT_ID')
    CLIENT_SECRET=data.get('CLIENT_SECRET')
    custom_domain=data.get('CUSTOM_DOMAIN')
    if not code_verifier:
        raise ValueError("Invalid or expired state: " + str(state))
    
    redis_client.delete(f"pkce:{state}")  # Remove after use
    if environment=="production":
        if custom_domain=='':
            global SALESFORCE_TOKEN_URL
            if is_source:
                response = requests.post(SALESFORCE_TOKEN_URL, data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "redirect_uri": SOURCE_REDIRECT_URI,
                    "code": code,
                    "code_verifier": code_verifier,
                })
            else:
                response = requests.post(SALESFORCE_TOKEN_URL, data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "redirect_uri": REDIRECT_URI,
                    "code": code,
                    "code_verifier": code_verifier,
                })
        else:
            SALESFORCE_TOKEN_URL = f"https://{custom_domain}.my.salesforce.com/services/oauth2/token"
            if is_source:
                response = requests.post(SALESFORCE_TOKEN_URL, data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": SOURCE_REDIRECT_URI,
                "code": code,
                "code_verifier": code_verifier,
                })
            else:
                response = requests.post(SALESFORCE_TOKEN_URL, data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "redirect_uri": REDIRECT_URI,
                    "code": code,
                    "code_verifier": code_verifier,
                })

    else:
        if is_source:
            response = requests.post(SALESFORCE_SANDBOX_TOKEN_URL, data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": SOURCE_SANDBOX_REDIRECT_URI,
                "code": code,
                "code_verifier": code_verifier,
            })
        else:
            response = requests.post(SALESFORCE_SANDBOX_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": SANDBOX_REDIRECT_URI,
            "code": code,
            "code_verifier": code_verifier,
        })
    
    if response.status_code != 200:
        raise ValueError(f"Token exchange failed: {response.text}")
    
    token_data = response.json()
    print("token_data",token_data)
    token = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", "default_refresh_token"),

        "instance_url": token_data["instance_url"],
        "expires_at": (datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))).isoformat()
    }
    print("tokens",token)
    redis_client.setex(f"salesforce_token:{state}", 3600, json.dumps(token))  # Store token for 1 hour
    return token


NON_RETRYABLE_SALESFORCE_AUTH_MARKERS = (
    "invalid_grant",
    "expired access/refresh token",
    "expired access token",
    "invalid_session_id",
    "session expired",
    "token refresh failed",
    "authentication failed. refresh token expired",
    "please reconnect",
    "jwt token exchange failed",
    "invalid client credentials",
    "user hasn't approved",
    "invalid assertion",
    "invalid_grant: user hasn't approved",
    "refresh_token scope is required",
    "preauthorized",
    "pre-authorized",
)


def format_salesforce_migration_auth_error(
    message: str,
    *,
    environment: str = "production",
    credentials: Optional[dict] = None,
) -> str:
    """Return a migration-safe auth error (JWT config vs OAuth reconnect)."""
    from utils.salesforce_jwt_auth import is_salesforce_jwt_auth

    msg = str(message or "").strip()
    msg_low = msg.lower()
    if is_salesforce_jwt_auth(credentials) or "jwt token exchange failed" in msg_low:
        return msg

    org_label = (
        "Salesforce Sandbox"
        if str(environment or "").strip().lower() == "sandbox"
        else "Salesforce"
    )
    oauth_expired_markers = (
        "invalid_grant",
        "expired access/refresh token",
        "expired access token",
        "invalid_session_id",
        "session expired",
        "token refresh failed",
    )
    if any(marker in msg_low for marker in oauth_expired_markers):
        return (
            f"{org_label} refresh/access token has expired. "
            "Reconnect the source in connector Settings, then retry the migration."
        )
    return msg


def is_non_retryable_salesforce_auth_error(message: str) -> bool:
    """True when Salesforce OAuth is expired/revoked and Celery retries will not help."""
    msg_low = (message or "").lower()
    return any(marker in msg_low for marker in NON_RETRYABLE_SALESFORCE_AUTH_MARKERS)


def refresh_salesforce_token(
    refresh_token,
    CLIENT_ID,
    CLIENT_SECRET,
    environment,
    custom_domain="",
    instance_url="",
):
    """Refresh Salesforce access token using the provided refresh token."""
    if not refresh_token:
        raise ValueError("Refresh token is required to refresh the access token.")
    
    if not CLIENT_ID:
        raise ValueError("CLIENT_ID is required to refresh the access token.")
    
    if not CLIENT_SECRET:
        raise ValueError("CLIENT_SECRET is required to refresh the access token.")
    
    from utils.salesforce_oauth_persist import resolve_salesforce_token_url

    token_url = resolve_salesforce_token_url(environment, custom_domain, instance_url)

    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    try:
        response = requests.post(token_url, data=payload)
        if response.status_code != 200:
            error_detail = response.text
            print(f"❌ Token refresh failed with status {response.status_code}: {error_detail}")
            
            # Check for specific error cases
            if response.status_code == 400:
                try:
                    error_data = response.json()
                    if 'error' in error_data:
                            raise ValueError(error_data.get('error_description', error_data['error']))
                except:
                    pass
            
            raise ValueError(f"Token refresh failed with status {response.status_code}: {error_detail}")

        new_data = response.json()
        new_access_token = new_data.get("access_token")
        new_instance_url = new_data.get("instance_url")
        
        if not new_access_token:
            raise ValueError("No access token received in refresh response")
        
        if not new_instance_url:
            raise ValueError("No instance URL received in refresh response")
        
        print(f"✅ Token refresh successful. New instance URL: {new_instance_url}")
        return {
            "access_token": new_access_token,
            "instance_url": new_instance_url,
            "refresh_token": new_data.get("refresh_token") or refresh_token,
        }
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Network error during token refresh: {str(e)}")
    except Exception as e:
        raise ValueError(f"Unexpected error during token refresh: {str(e)}")


def build_salesforce_oauth_context(
    connection_id,
    role: str,
    credentials: Optional[dict] = None,
):
    """Build oauth_context for salesforce_connection token persistence."""
    if not connection_id:
        return None
    if role not in ("source", "destination"):
        return None
    return {
        "connection_id": int(connection_id),
        "role": role,
        "credentials": credentials,
    }


def prepare_salesforce_credentials(
    connection_id,
    role: str,
    credentials: Optional[dict] = None,
):
    """Hydrate OAuth tokens from Django; JWT Bearer configs pass through unchanged."""
    from utils.salesforce_jwt_auth import is_salesforce_jwt_auth

    creds = dict(credentials or {})
    oauth_context = build_salesforce_oauth_context(connection_id, role, creds)
    if not is_salesforce_jwt_auth(creds):
        from utils.salesforce_oauth_persist import hydrate_salesforce_credentials

        creds = hydrate_salesforce_credentials(connection_id, role, creds)
        if isinstance(oauth_context, dict):
            oauth_context["credentials"] = creds
    elif isinstance(oauth_context, dict):
        oauth_context["credentials"] = creds
    return creds, oauth_context


def _apply_refreshed_salesforce_tokens(
    oauth_context,
    token_data: dict,
    *,
    previous_refresh_token: Optional[str] = None,
) -> bool:
    """Update in-memory credentials and persist rotated tokens to Django."""
    if not oauth_context or not token_data:
        return False

    creds = oauth_context.get("credentials")
    if isinstance(creds, dict):
        creds["access_token"] = token_data.get("access_token")
        creds["instance_url"] = token_data.get("instance_url")
        if token_data.get("refresh_token"):
            creds["refresh_token"] = token_data.get("refresh_token")

    connection_id = oauth_context.get("connection_id")
    role = oauth_context.get("role")
    if not connection_id or not role:
        return False

    try:
        from utils.salesforce_oauth_persist import persist_salesforce_oauth_tokens

        return bool(
            persist_salesforce_oauth_tokens(
                int(connection_id),
                role,
                token_data,
                previous_refresh_token=previous_refresh_token,
            )
        )
    except Exception as exc:
        logger.warning(
            "SF OAuth token persist failed | connection_id=%s | role=%s | error=%s",
            connection_id,
            role,
            exc,
        )
        return False


def _refresh_salesforce_token_with_persist(
    refresh_token,
    CLIENT_ID,
    CLIENT_SECRET,
    environment,
    custom_domain="",
    oauth_context=None,
    instance_url="",
    current_access_token="",
):
    """Refresh access token under a distributed lock (rotation-safe for parallel workers)."""
    from utils.salesforce_oauth_persist import (
        fetch_salesforce_oauth_tokens,
        run_with_salesforce_refresh_lock,
        try_return_post_rotation_tokens,
        wait_for_salesforce_token_rotation,
    )

    def _attempt(rt):
        return refresh_salesforce_token(
            rt,
            CLIENT_ID,
            CLIENT_SECRET,
            environment,
            custom_domain,
            instance_url,
        )

    def _do_refresh_locked():
        previous_refresh_token = refresh_token
        active_refresh_token = refresh_token
        connection_id = (oauth_context or {}).get("connection_id")
        role = (oauth_context or {}).get("role")

        stale_access = (
            current_access_token
            or (oauth_context or {}).get("credentials", {}).get("access_token")
            or ""
        )

        if connection_id and role:
            rotated = try_return_post_rotation_tokens(
                int(connection_id),
                role,
                previous_refresh_token,
                current_access_token=stale_access or None,
            )
            if rotated:
                return rotated

            latest = fetch_salesforce_oauth_tokens(int(connection_id), role)
            latest_rt = (latest or {}).get("refresh_token")
            if latest_rt:
                active_refresh_token = latest_rt

        try:
            token_data = _attempt(active_refresh_token)
        except ValueError as exc:
            msg = str(exc).lower()
            if oauth_context and connection_id and role and "invalid_grant" in msg:
                token_data = wait_for_salesforce_token_rotation(
                    int(connection_id),
                    role,
                    previous_refresh_token,
                    current_access_token=stale_access or None,
                )
                if token_data is None:
                    latest = fetch_salesforce_oauth_tokens(int(connection_id), role)
                    latest_rt = (latest or {}).get("refresh_token")
                    if latest_rt and latest_rt != active_refresh_token:
                        token_data = _attempt(latest_rt)
                        active_refresh_token = latest_rt
                    else:
                        raise exc
            else:
                raise
        else:
            if token_data.get("refresh_token"):
                active_refresh_token = token_data.get("refresh_token")

        persisted = _apply_refreshed_salesforce_tokens(
            oauth_context,
            token_data,
            previous_refresh_token=previous_refresh_token,
        )
        if oauth_context and not persisted:
            logger.warning(
                "SF_OAUTH_PERSIST_MISSED | connection_id=%s | role=%s | "
                "refresh succeeded but Django persist failed — parallel workers may see invalid_grant",
                oauth_context.get("connection_id"),
                oauth_context.get("role"),
            )
        return token_data

    if (
        oauth_context
        and oauth_context.get("connection_id")
        and oauth_context.get("role") in ("source", "destination")
    ):
        return run_with_salesforce_refresh_lock(
            int(oauth_context["connection_id"]),
            oauth_context["role"],
            _do_refresh_locked,
        )
    return _do_refresh_locked()


def _salesforce_connection_jwt(
    credentials,
    environment,
    custom_domain="",
    oauth_context=None,
):
    """Connect using JWT Bearer (no refresh tokens)."""
    from utils.salesforce_jwt_auth import (
        get_salesforce_jwt_access_token,
        invalidate_salesforce_jwt_cache,
        is_salesforce_jwt_auth,
    )

    if not is_salesforce_jwt_auth(credentials):
        raise ValueError("JWT credentials are required for JWT Bearer connection.")

    connection_id = (oauth_context or {}).get("connection_id")
    role = (oauth_context or {}).get("role")
    creds = dict(credentials or {})
    if isinstance(oauth_context, dict) and isinstance(oauth_context.get("credentials"), dict):
        creds.update(oauth_context["credentials"])

    limit_keywords = [
        "request_limit_exceeded",
        "totalrequests limit exceeded",
        "request limit",
        "rate limit",
        "too many requests",
        "429",
        "503",
        "service unavailable",
    ]

    for attempt in range(2):
        token_data = get_salesforce_jwt_access_token(
            creds,
            connection_id=connection_id,
            role=role,
            environment=environment,
            custom_domain=custom_domain or creds.get("CUSTOM_DOMAIN") or "",
            use_cache=attempt == 0,
        )
        access_token = token_data["access_token"]
        instance_url = token_data["instance_url"]
        latest_version = get_latest_salesforce_version(instance_url, access_token, logger)
        try:
            sf = Salesforce(
                instance_url=instance_url,
                session_id=access_token,
                version=latest_version,
            )
            sf.sf_version = latest_version
            sf.describe()
            print("✅ Connected to Salesforce via JWT Bearer successfully!")
            return sf
        except Exception as exc:
            error_message = str(exc).lower()
            if any(keyword in error_message for keyword in limit_keywords):
                raise HTTPException(
                    status_code=429,
                    detail="Salesforce API limit reached. Please try again after some time.",
                )
            if attempt == 0 and connection_id and role:
                invalidate_salesforce_jwt_cache(int(connection_id), role)
                continue
            raise


def salesforce_connection(
    state,
    access_token,
    instance_url,
    refresh_token,
    CLIENT_ID,
    CLIENT_SECRET,
    environment,
    custom_domain="",
    oauth_context=None,
):
    """
    Connects to Salesforce with retry logic and connection validation
    """
    from utils.salesforce_jwt_auth import is_salesforce_jwt_auth

    jwt_credentials = None
    if isinstance(oauth_context, dict) and isinstance(oauth_context.get("credentials"), dict):
        jwt_credentials = oauth_context["credentials"]

    if is_salesforce_jwt_auth(jwt_credentials):
        return _salesforce_connection_jwt(
            jwt_credentials,
            environment,
            custom_domain=custom_domain,
            oauth_context=oauth_context,
        )

    def _hydrate_from_django():
        nonlocal access_token, instance_url, refresh_token
        if not oauth_context:
            return
        from utils.salesforce_oauth_persist import hydrate_salesforce_credentials

        hydrated = hydrate_salesforce_credentials(
            oauth_context.get("connection_id"),
            oauth_context.get("role"),
            oauth_context.get("credentials"),
        )
        if isinstance(hydrated, dict):
            oauth_context["credentials"] = hydrated
            access_token = hydrated.get("access_token") or access_token
            instance_url = hydrated.get("instance_url") or instance_url
            refresh_token = hydrated.get("refresh_token") or refresh_token

    _hydrate_from_django()

    max_retries = 3
    retry_count = 0
    latest_version = get_latest_salesforce_version(instance_url, access_token, logger)
    limit_keywords = [
        "request_limit_exceeded",
        "totalrequests limit exceeded",
        "request limit",
        "rate limit",
        "too many requests",
        "429",
        "503",
        "service unavailable",
    ]

    while retry_count < max_retries:
        _hydrate_from_django()
        try:
            print(f"🔹 Attempting Salesforce connection (attempt {retry_count + 1})...")
            
            # Try to connect with the existing access token
            sf = Salesforce(instance_url=instance_url, session_id=access_token, version=latest_version)
            sf.sf_version = latest_version
            # Test if session is valid immediately
            try:
                sf.describe()  # If this fails, the session is invalid
                print("✅ Connected to Salesforce successfully!")
                return sf
                
            except Exception as e:
                error_message = str(e).lower()
                print(f"❌ Initial token test failed: {error_message}")

                # API limit errors should be surfaced as-is, not converted to auth expiry.
                if any(keyword in error_message for keyword in limit_keywords):
                    raise HTTPException(
                        status_code=429,
                        detail="Salesforce API limit reached. Please try again after some time."
                    )

                # If session is invalid, refresh the token
                if 'invalid_session_id' in error_message or 'session expired' in error_message:
                    print("🔄 Access token expired, attempting refresh...")

                    if not CLIENT_ID or not CLIENT_SECRET:
                        raise ValueError("Missing Salesforce OAuth credentials. Please check your connection configuration.")

                    # Refresh token and get new access_token + instance_url
                    token_data = _refresh_salesforce_token_with_persist(
                        refresh_token,
                        CLIENT_ID,
                        CLIENT_SECRET,
                        environment,
                        custom_domain,
                        oauth_context=oauth_context,
                        instance_url=instance_url,
                        current_access_token=access_token,
                    )
                    new_access_token = token_data["access_token"]
                    new_instance_url = token_data["instance_url"]
                    new_refresh_token = token_data.get("refresh_token") or refresh_token
                    access_token = new_access_token
                    instance_url = new_instance_url
                    refresh_token = new_refresh_token
                    print(f"🔑 Token refreshed successfully! New Instance URL: {new_instance_url}")

                    # Retry Salesforce connection with updated credentials
                    sf = Salesforce(instance_url=new_instance_url, session_id=new_access_token, version=latest_version)
                    sf.describe()  # Validate the connection
                    print("✅ Reconnected to Salesforce successfully!")
                    return sf
                else:
                    raise  # Re-raise if not a session error

        except Exception as e:
            retry_count += 1

            # ✅ CRITICAL FIX: If e is already an HTTPException with a detail, re-raise it directly.
            # str(HTTPException) returns EMPTY string, which destroys the error message.
            if isinstance(e, HTTPException):
                if hasattr(e, 'detail') and e.detail:
                    print(f"❌ Salesforce connection failed (attempt {retry_count}): {e.detail}")
                    raise e  # Preserve original HTTPException with its detail intact
            
            # For non-HTTPException: extract message safely
            error_message = str(e).strip() if str(e).strip() else repr(e)
            print(f"❌ Salesforce connection failed (attempt {retry_count}): {error_message}")
            
            if retry_count >= max_retries:
                # Capture the raw error for the classifier
                raise e
            
            time.sleep(2 ** retry_count)  # Exponential backoff


def history_object_name(obj_name: str) -> str:
    """
    Derive history object name:
      - 'Budget__c' → 'Budget__History'
      - 'Account'   → 'AccountHistory'
    """
    if obj_name.endswith("__c"):
        return obj_name[:-3] + "__History"
    return obj_name + "History"


def get_salesforce_objects_and_columns(
    state, connection_id, job_name,
    access_token, instance_url, refresh_token,
    client_id, client_secret, environment,
    is_source, custom_domain='',
    salesforce_credentials=None,
):
    """
    Fetch Salesforce objects + metadata, and auto-detect & include history tables
    """
    role = "source" if is_source else "destination"
    creds, oauth_context = prepare_salesforce_credentials(
        connection_id,
        role,
        dict(salesforce_credentials or {}),
    )
    sf = salesforce_connection(
        state,
        creds.get("access_token") or access_token,
        creds.get("instance_url") or instance_url,
        creds.get("refresh_token") or refresh_token,
        creds.get("CLIENT_ID") or client_id,
        creds.get("CLIENT_SECRET") or client_secret,
        environment,
        creds.get("CUSTOM_DOMAIN") or custom_domain,
        oauth_context=oauth_context,
    )

    resolved_instance_url = (
        getattr(sf, "base_url", None)
        or (
            f"https://{sf.sf_instance}"
            if getattr(sf, "sf_instance", None)
            else None
        )
        or creds.get("instance_url")
        or instance_url
    )
    latest_version = get_latest_salesforce_version(
        resolved_instance_url, sf.session_id, logger
    )

    if not sf:
        raise ValueError("Failed to connect to Salesforce")

    sf_desc = sf.describe()
    objects = sf_desc.get("sobjects", [])

    updated_tables = {}
    columns = {}
    history_tables = {}

    # -----------------------------------------------------
    # 🔁 Loop through all objects and collect metadata
    # -----------------------------------------------------
    for obj in objects:
        obj_name = obj.get("name")
        if not obj_name:
            continue

        updated_tables[obj_name] = "table"

        # 📦 Extract field metadata
        try:
            desc = getattr(sf, obj_name).describe()
            cols = {}
            for f in desc.get("fields", []):
                fname = f.get("name")
                if not fname:
                    continue
                is_pk = f.get("idLookup") or fname == "Id" or f.get("unique", False)
                cols[fname] = {
                    "data_type": f.get("type", "UNKNOWN").upper(),
                    "is_primary_key": is_pk
                }
            columns[obj_name] = cols
        except Exception as e:
            logger.warning(f"⚠️ Failed to describe {obj_name}: {e}")
            columns[obj_name] = {}

        # 🧠 Detect history tracking per-object
        if is_source:
            if check_object_has_history_tracking(obj_name, sf.session_id, instance_url, latest_version):
                hist_name = history_object_name(obj_name)
                history_tables[hist_name] = "history_table"
                updated_tables[hist_name] = "table"

    logger.info(f"📌 Total history tables detected: {len(history_tables)}")

    # 💾 Persist metadata to the database
    refresh_and_update_tables_in_database(
        connection_id, updated_tables, columns, job_name, is_source, history_tables
    )

    return {
        'status_code': 200,
        "updated_tables": updated_tables,
        "history_tables": history_tables,
        "columns": columns,
        'body': "✅ Tables fetched successfully"
    }


def check_object_has_history_tracking(obj_name, access_token, instance_url, api_version):
    tooling_url = f"{instance_url}/services/data/v{api_version}/tooling/query"
    headers = {"Authorization": f"Bearer {access_token}"}
    query = (
        f"SELECT QualifiedApiName FROM FieldDefinition "
        f"WHERE EntityDefinition.QualifiedApiName = '{obj_name}' "
        f"AND IsFieldHistoryTracked = true LIMIT 1"
    )

    try:
        resp = requests.get(tooling_url, headers=headers, params={"q": query})
        print("object",obj_name)
        print("response",resp)
        if resp.ok and resp.json().get("records"):
            return True
    except Exception as e:
        logger.warning(f"⚠️ Tooling API failed for {obj_name}: {e}")
    return False


def refresh_and_update_tables_in_database(
    connection_id, new_tables, attributes_dict, job_name,
    is_source, history_tables
):
    """
    Insert/update table metadata and flag history tables via `is_history` and delta tables via `is_delta`.
    """
    table_name = "source_table_selection" if is_source else "destination_table_selection"
    max_retries = 3
    local_start_time = time.time()

    # Get source_id and delta fields for this connection (only for source tables)
    source_id = None
    delta_fields = []
    if is_source:
        source_id = get_source_id_from_connection(connection_id)
        if source_id:
            delta_fields = get_delta_fields_for_source(source_id)
            print(f"🔍 Found {len(delta_fields)} delta fields for source_id {source_id}: {delta_fields}")

    for attempt in range(max_retries):
        try:
            conn = postgres_connection()
            cursor = conn.cursor()
            cursor.execute("SET statement_timeout = '60s'")

            for table in new_tables.keys():
                # ✅ Fix: Always provide non-null table_fields
                table_data = attributes_dict.get(table)
                table_type = new_tables.get(table, None)
                if not isinstance(table_data, dict):
                    table_data = {}
                primary_keys = [
                    col_name
                    for col_name, meta in table_data.items()
                    if isinstance(meta, dict) and meta.get("is_primary_key")
                ]
                table_fields = json.dumps(table_data)
                primary_keys_json = json.dumps(primary_keys)

                is_hist = table in history_tables if is_source else False
                
                # 🔍 Check if table contains delta fields (only for source tables with delta fields configured)
                is_delta = False
                if is_source and delta_fields:
                    is_delta = check_table_has_delta_fields(table_data, delta_fields)
                    if is_delta:
                        print(f"✅ Table '{table}' marked as delta table - contains delta fields: {[field for field in delta_fields if field in table_data]}")

                if is_source:
                    sql = f"""
                        INSERT INTO {table_name}
                        (connection_id, "table", table_fields, primary_keys, selected, folder_path, json_path, added_on, is_history, is_delta, is_mapping_checked)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)
                        ON CONFLICT (connection_id, "table")
                        DO UPDATE SET
                            table_fields = EXCLUDED.table_fields,
                            primary_keys = EXCLUDED.primary_keys,
                            folder_path = EXCLUDED.folder_path,
                            json_path = EXCLUDED.json_path,
                            added_on = NOW(),
                            is_history = EXCLUDED.is_history,
                            is_delta = EXCLUDED.is_delta;
                    """
                    cursor.execute(sql, (
                        connection_id,
                        table,
                        table_fields,
                        primary_keys_json,
                        False,  # Set default 'selected' value only on first insert
                        None,
                        None,
                        is_hist,
                        is_delta,
                        False
                    ))

                else:
                    # Insert or update the table
                    sql_upsert = """
                        INSERT INTO destination_table_selection (connection_id, "table", table_type, table_fields, selected, added_on)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (connection_id, "table")
                        DO UPDATE SET 
                            table_fields = EXCLUDED.table_fields,
                            selected = EXCLUDED.selected,
                            added_on = NOW();
                    """
                    cursor.execute(sql_upsert, (connection_id, table, table_type, table_fields, False))
            # ✅ FIX: Check if this is reverse ETL and if source listing is also complete
            # For reverse ETL (Snowflake -> Salesforce), both source and destination listing must complete
            # We check if source tables were recently updated (within last 5 minutes)
            cursor.execute("""
                SELECT COUNT(*) 
                FROM connection tc
                JOIN source_config sc ON tc.src_config_id = sc.src_config_id
                JOIN source_model sm ON sc.src_id = sm.src_id
                WHERE tc.connection_id = %s 
                AND sm.is_rtl = TRUE;
            """, (connection_id,))
            is_reverse_etl = cursor.fetchone()[0] > 0
            
            if is_reverse_etl and not is_source:
                # For reverse ETL destination listing, check if source listing has also completed
                # We check if source tables were recently updated (within last 5 minutes)
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM source_table_selection 
                    WHERE connection_id = %s 
                    AND added_on > NOW() - INTERVAL '5 minutes';
                """, (connection_id,))
                source_recently_updated = cursor.fetchone()[0] > 0
                
                if source_recently_updated:
                    # Both source and destination listing are complete
                    cursor.execute("""
                        UPDATE migration_record
                        SET status = %s, updated_at = NOW()
                        WHERE job_name = %s AND table_name IS NULL;
                    """, ('COMPLETED', job_name))
                    print(f"✅ All listing jobs completed for reverse ETL job_name: {job_name}")

                    # ✅ Update the log message to indicate schema refresh completion
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
                            f"Updated {len(new_tables)} tables."
                        )
                        update_migration_status_log(
                            job_name=job_name,
                            message=completion_message,
                            status="S"  # Success status
                        )
                    except Exception as log_error:
                        print(f"⚠️ Update schema completion log update failed (non-fatal): {log_error}")

                    # ✅ Trigger WebSocket update for schema refresh completion (reverse ETL)
                    try:
                        from utils.websocket_utils import trigger_update_schema_ws_status_update
                        trigger_update_schema_ws_status_update(connection_id)
                    except Exception as ws_error:
                        print(f"⚠️ WebSocket trigger failed (non-fatal): {ws_error}")
                else:
                    # Source listing not complete yet, don't mark as COMPLETED
                    print(f"⏳ Source listing not complete yet for reverse ETL job_name: {job_name}, not marking as COMPLETED yet")
            else:
                # Regular ETL or source listing, mark as COMPLETED immediately
                cursor.execute("""
                    UPDATE migration_record
                    SET status = %s, updated_at = NOW()
                    WHERE job_name = %s AND table_name IS NULL;
                """, ('COMPLETED', job_name))
                print(f"✅ Listing job completed for job_name: {job_name}")

                # ✅ Update the log message to indicate schema refresh completion
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
                        f"Updated {len(new_tables)} tables."
                    )
                    update_migration_status_log(
                        job_name=job_name,
                        message=completion_message,
                        status="S"  # Success status
                    )
                except Exception as log_error:
                    print(f"⚠️ Update schema completion log update failed (non-fatal): {log_error}")

                # ✅ Trigger WebSocket update for schema refresh completion
                try:
                    from utils.websocket_utils import trigger_update_schema_ws_status_update
                    trigger_update_schema_ws_status_update(connection_id)
                except Exception as ws_error:
                    print(f"⚠️ WebSocket trigger failed (non-fatal): {ws_error}")

            conn.commit()
            print(f"✅ {table_name} updated ({len(new_tables)} tables).")
            break

        except psycopg2.OperationalError as e:
            print(f"⚠️ DB connection error attempt {attempt+1}: {e}")
            conn.rollback()
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise

        except Exception as e:
            print(f"Error updating database: {e}")
            # Rollback the transaction and update the migration record to FAILED
            if conn:
                conn.rollback()
            try:
                with conn.cursor() as cursor:
                    sql_update_migration_record_error = """
                    UPDATE migration_record
                    SET status = %s, updated_at = NOW()
                    WHERE job_name = %s;
                    """
                    cursor.execute(sql_update_migration_record_error, ('FAILED', job_name))
                    conn.commit()

                    # ✅ Update the log message to indicate schema refresh failure
                    try:
                        from utils.error_utils import update_migration_status_log
                        failure_message = f"Update schema failed: {str(e)}"
                        update_migration_status_log(
                            job_name=job_name,
                            message=failure_message,
                            status="E"  # Error status
                        )
                    except Exception as log_error:
                        print(f"⚠️ Update schema failure log update failed (non-fatal): {log_error}")

                    # ✅ Trigger WebSocket update for schema refresh failure
                    try:
                        from utils.websocket_utils import trigger_update_schema_ws_status_update
                        trigger_update_schema_ws_status_update(connection_id)
                    except Exception as ws_error:
                        print(f"⚠️ WebSocket trigger failed (non-fatal): {ws_error}")
            except Exception as err:
                print(f"❌ Error marking migration FAILED: {err}")
            raise e

        finally:
            cursor.close()
            conn.close()


def get_delta_fields_for_source(source_id):
    """
    Fetch delta fields from source_delta_table for a given source_id.
    
    Args:
        source_id (int): The source ID (e.g., 3 for Salesforce)
    
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


def drop_temp_table(snowflake_details, temp_table):
    """
    Drops the given temporary table from the database using SQLAlchemy engine.
    schema: Optional schema name, if not default.
    """
    engine=destination_connection_function(snowflake_details)
    snowflake_schema = snowflake_details.get('schema', '').upper()
    if not temp_table:
        print("WARNING: Temp table name not supplied. Skipping drop.")
        return
    try:
        qualified_table = f'"{temp_table}"'
        if snowflake_schema:
            qualified_table = f'"{snowflake_schema}".{qualified_table}'
        drop_sql = f"DROP TABLE IF EXISTS {qualified_table}"
        with engine.connect() as conn:
            conn.execute(text(drop_sql))
        print(f"INFO: Temp table {qualified_table} dropped.")
    except Exception as e:
        print(f"WARNING: Failed to drop temp table {temp_table}: {e}")


def create_temp_index_table(snowflake_details, source_table, temp_table, fields):
    """
    Creates a temporary table with an auto-incremented `datasyncher_row` column
    and inserts selected fields from the source table.

    :param snowflake_details: dict with Snowflake connection details (including schema)
    :param source_table: name of the source table (can include schema)
    :param temp_table: name of the temporary table to create
    :param fields: list of field names to include in the temp table
    """
    engine = destination_connection_function(snowflake_details)
    schema = snowflake_details.get('schema', '').upper()
    full_temp_table = f"{schema}.{temp_table}"

    # Define columns
    column_defs = ['__datasyncher_row INTEGER AUTOINCREMENT START 1 INCREMENT 1 ORDER']
    for field in fields:
        column_defs.append(f'"{field}" VARCHAR')  # Adjust type as needed

    column_defs_str = ",\n  ".join(column_defs)

    # Create table SQL
    create_sql = f"""
        CREATE OR REPLACE TABLE {full_temp_table} (
          {column_defs_str}
        );
    """

    # Insert SQL (exclude datasyncher_row)
    quoted_fields = [f'"{f}"' for f in fields]
    field_list_str = ", ".join(quoted_fields)
    insert_sql = f"""
        INSERT INTO {full_temp_table} ({field_list_str})
        SELECT {field_list_str}
        FROM {source_table};
    """

    print("Creating and populating temporary table...")
    print("Create SQL:")
    print(create_sql)
    print("Insert SQL:")
    print(insert_sql)

    with engine.connect() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS {full_temp_table}'))
        conn.execute(text(create_sql))
        conn.execute(text(insert_sql))


def create_log_paths(base_dir, company_name):
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_dir = Path(base_dir) / company_name / today_str
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


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
        (authentication_type == "key_pair" and not private_key_str)
        or (authentication_type == "password" and not password)
    ):
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
            flavor="salesforce",
            ensure_schema=True,
        )
        print("✅ Connection established successfully.")
        return engine
    except SQLAlchemyError as e:
        if isinstance(e, HTTPException):
            raise e
        raise e
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise e


def update_table_status(connection_id, table_name, status, job_name, migration_session_id):
    """
    Updates the status with retry logic and fresh connections.
    Also sets start_time when status changes to 'in_progress'.
    """
    conn = None
    cursor = None
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Create a fresh connection for each update
            conn = postgres_connection()
            cursor = conn.cursor()
            
            # Set a statement timeout to prevent hanging
            cursor.execute("SET statement_timeout = '30s'")
            
            # Update source_table_selection
            sql_update_source_table = """
            UPDATE source_table_selection
            SET status = %s
            WHERE connection_id = %s AND "table" = %s;
            """
            cursor.execute(sql_update_source_table, (status, connection_id, table_name))
                    
             # Update migration_record - set start_time when status is 'in_progress'
            # NOTE: start_time will be updated later in data_transfer_logs with actual transfer start time
            if status == 'in_progress':
                sql_update_migration_record = """
                UPDATE migration_record
                SET status = %s, updated_at = NOW()
                WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
                """
            else:
                sql_update_migration_record = """
                UPDATE migration_record
                SET status = %s, updated_at = NOW()
                WHERE job_name = %s AND migration_session_id = %s AND table_name = %s;
                """
            cursor.execute(sql_update_migration_record, (status, job_name, migration_session_id, table_name))
            
            # Commit the changes
            conn.commit()
            print(f"✅ Successfully updated status for table '{table_name}' to '{status}'")

            # ✅ Trigger WebSocket status update for real-time UI tracking
            try:
                from utils.websocket_utils import (
                    trigger_ws_status_update,
                    trigger_connection_ws_status_update,
                    trigger_company_connections_status_update,
                )
                trigger_ws_status_update(migration_session_id)
                trigger_connection_ws_status_update(connection_id)
                if status == 'in_progress':
                    cursor.execute(
                        "SELECT cmp_id FROM connection WHERE connection_id = %s",
                        (connection_id,)
                    )
                    cmp_row = cursor.fetchone()
                    if cmp_row and cmp_row[0]:
                        trigger_company_connections_status_update(cmp_row[0])
            except Exception as ws_err:
                print(f"⚠️ WebSocket trigger failed: {ws_err}")

            break  # Success, exit the retry loop
            
        except psycopg2.OperationalError as e:
            retry_count += 1
            print(f"⚠️ Database connection error (attempt {retry_count}/{max_retries}): {e}")
            if retry_count >= max_retries:
                print(f"❌ Failed to update status after {max_retries} attempts")
                raise
            time.sleep(2)  # Wait before retry
            
        except Exception as e:
            print(f"❌ Error updating table status: {e}")
            if conn:
                conn.rollback()
            raise
            
        finally:
            # Always close connections
            if cursor:
                cursor.close()
            if conn:
                conn.close()


def update_table_status_timestamp_sf_to_snowflake(args, table_name, status, message):
    """
    Update timestamp for Salesforce to Snowflake table processing (using updated_at field).
    """
    try:
        # Extract connection details from args
        connection_id = args[3]  # connection_id is 4th argument
        job_name = args[4]       # job_name is 5th argument
        migration_session_id = args[5]  # migration_session_id is 6th argument

        conn = postgres_connection()
        cursor = conn.cursor()

        # Update timestamp (using updated_at field)
        cursor.execute("""
            UPDATE migration_record
            SET updated_at = %s, status = %s
            WHERE job_name = %s AND table_name = %s
        """, (datetime.utcnow(), status, job_name, table_name))

        conn.commit()

        # Trigger WebSocket status update for real-time UI tracking
        try:
            from utils.websocket_utils import (
                trigger_ws_status_update,
                trigger_connection_ws_status_update,
                trigger_company_connections_status_update,
            )
            trigger_ws_status_update(migration_session_id)
            trigger_connection_ws_status_update(connection_id)
            if status == 'in_progress':
                cursor.execute(
                    "SELECT cmp_id FROM connection WHERE connection_id = %s",
                    (connection_id,)
                )
                cmp_row = cursor.fetchone()
                if cmp_row and cmp_row[0]:
                    trigger_company_connections_status_update(cmp_row[0])
        except Exception as ws_err:
            print(f"WebSocket trigger failed: {ws_err}")
        print(f"🔄 TIMESTAMP: Table '{table_name}' - {status} - {message}")

    except Exception as e:
        print(f"❌ Failed to update timestamp for table '{table_name}': {e}")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


def update_table_status_sf_to_snowflake(connection_id, table_name, status, job_name, migration_session_id, sync_end_time=None):
    """
    Update table status in BOTH source_table_selection AND migration_record
    for Salesforce to Snowflake migration.
    Also sets start_time when status changes to 'in_progress'.
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()

        # ✅ UPDATE 1: source_table_selection
        if sync_end_time:
            cursor.execute("""
                UPDATE source_table_selection
                SET status = %s, last_synced = %s
                WHERE connection_id = %s AND "table" = %s;
            """, (status, sync_end_time, connection_id, table_name))
        else:
            cursor.execute("""
                UPDATE source_table_selection
                SET status = %s
                WHERE connection_id = %s AND "table" = %s;
            """, (status, connection_id, table_name))
        
        rows_updated_source = cursor.rowcount
        
        # ✅ UPDATE 2: migration_record - update status only (start_time will be set in data_transfer_logs with actual transfer time)
        if status == 'in_progress':
            cursor.execute("""
                UPDATE migration_record
                SET status = %s, updated_at = %s
                WHERE job_name = %s AND table_name = %s AND migration_session_id = %s
            """, (status, datetime.utcnow(), job_name, table_name, migration_session_id))
        else:
            cursor.execute("""
                UPDATE migration_record
                SET status = %s, updated_at = %s
                WHERE job_name = %s AND table_name = %s AND migration_session_id = %s
            """, (status, datetime.utcnow(), job_name, table_name, migration_session_id))
        
        
        rows_updated_migration = cursor.rowcount

        conn.commit()
        print(f"✅ Updated table '{table_name}' status to '{status}' (SourceTable: {rows_updated_source}, MigrationRecord: {rows_updated_migration})")

        try:
            from utils.websocket_utils import trigger_ws_status_update, trigger_connection_ws_status_update
            trigger_ws_status_update(migration_session_id)
            trigger_connection_ws_status_update(connection_id)
        except Exception as ws_err:
            print(f"WebSocket trigger failed: {ws_err}")

    except Exception as e:
        print(f"❌ Failed to update status for table '{table_name}': {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


logger = logging.getLogger(__name__)


logger.setLevel(logging.INFO)


console_handler = logging.StreamHandler()


console_handler.setLevel(logging.INFO)


formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')


console_handler.setFormatter(formatter)


logger.addHandler(console_handler)


class BulkAPI2Client:
    """Salesforce Bulk API 2.0 client with fallback to 1.0 and smart data cleanup."""

    def __init__(self, sf_connection, field_type_map=None):
        self.sf = sf_connection
        self.instance_url = self.sf.base_url.rstrip('/')
        self.session_id = self.sf.session_id
        self.headers = {
            'Authorization': f'Bearer {self.session_id}',
            'Content-Type': 'application/json'
        }
        self.bulk_api_url = f"{self.instance_url}/services/data/v62.0/jobs/query"
        self._fallback_results = None
        self.FIELD_TYPE_MAP = field_type_map or {}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def create_query_job(self, object_name: str, query: str, chunk_size: int, delete_transfer_needed: bool) -> str:
        # Always queryAll; soft-deleted rows are stripped on initial load in insert_into_staging.
        operation = "queryAll"
        response = requests.post(
            self.bulk_api_url,
            headers=self.headers,
            json={
                "operation": operation,
                "query": query,
                "contentType": "CSV",
                "columnDelimiter": "COMMA",
                "lineEnding": "LF"
            }
        )
        if response.status_code == 201:
            job_id = response.json()['id']
            self.logger.info(f"✅ Created Bulk API 2.0 job: {job_id} (operation={operation})")
            await self.wait_for_job_completion(job_id)
            return job_id
        else:
            self.logger.warning("⚠️ Bulk API 2.0 failed. Fallback to Bulk API 1.0")
            return await self._fallback_to_buy_bulk1(query, chunk_size, delete_transfer_needed, object_name)

    async def wait_for_job_completion(self, job_id: str):
        """
        Wait for job completion. Handles both Bulk API 2.0 jobs and progress-based fallback operations.
        """
        # Check if this is a progress-based fallback operation (not a real Salesforce job)
        if job_id.startswith("fallback_bulk1_"):
            self.logger.info("✅ Using Bulk API 1.0 fallback with progress monitoring - no job to wait for")
            # The progress monitoring is handled within the chunk generator itself
            return
        
        # Handle legacy fallback_bulk_1 ID
        if job_id == "fallback_bulk_1":
            self.logger.info("✅ Using Bulk API 1.0 fallback - streaming query directly (no job monitoring)")
            return
        
        # Normal Bulk API 2.0 job monitoring
        job_url = f"{self.bulk_api_url}/{job_id}"
        while True:
            resp = requests.get(job_url, headers=self.headers)
            if resp.status_code != 200:
                raise Exception(f"Job status fetch failed: {resp.text}")
            state = resp.json().get('state')
            self.logger.info(f"⏳ Job {job_id} state: {state}")
            if state == 'JobComplete':
                return
            if state in ['Failed', 'Aborted']:
                raise Exception(f"❌ Job ended with state: {state}")
            await asyncio.sleep(5)

    async def get_job_results(self, job_id: str):
        """
        Get job results. Handles both Bulk API 2.0 jobs and progress-based fallback operations.
        """
        # Handle progress-based fallback operations
        if job_id.startswith("fallback_bulk1_") or job_id == "fallback_bulk_1":
            if hasattr(self, "_fallback_chunk_generator"):
                return self._fallback_chunk_generator  # ✅ return the streaming generator
            else:
                raise Exception("❌ Fallback generator not initialized.")
        
        # Bulk API 2.0 result fetch (CSV)
        resp = requests.get(
            f"{self.bulk_api_url}/{job_id}/results",
            headers={
                'Authorization': f'Bearer {self.session_id}',
                'Accept': 'text/csv'
            }
        )
        if resp.status_code != 200:
            raise Exception(f"Results fetch failed: {resp.text}")
        
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        return self._cleanup_bool(rows)


    async def _fallback_to_buy_bulk1(self, query: str, chunk_size: int = 9000, delete_transfer_needed: bool = False, object_name: str = "unknown") -> str:
        """
        Bulk API 1.0 fallback with enhanced progress monitoring.
        
        This method now includes:
        - Adaptive timeout calculation based on estimated dataset size
        - Progress-based stuck detection (not just time-based)
        - Heartbeat logging for long-running operations
        - Smart differentiation between stuck vs. legitimately slow operations
        """
        self.logger.info("🔁 Fallback to Bulk API 1.0: Streaming records using query_all_iter()")
        
        # Step 1: Estimate record count for adaptive timeout calculation
        estimated_records = await self._estimate_record_count_for_fallback(query, object_name)
        
        # Step 2: Create progress monitor with adaptive timeouts
        try:
            from utils.salesforce_progress_monitor import create_progress_monitor, cleanup_progress_monitor
            import time as time_module
            
            operation_id = f"fallback_bulk1_{object_name}_{int(time_module.time())}"
            progress_monitor = create_progress_monitor(operation_id, estimated_records, object_name)
            
            # Store for external monitoring
            self._current_operation_id = operation_id
            self._progress_monitor = progress_monitor
            
        except Exception as e:
            self.logger.warning(f"⚠️ Could not initialize progress monitor: {e}, continuing without it")
            progress_monitor = None
            operation_id = "fallback_bulk_1"
        
        # Step 3: Start query with progress tracking
        # Always queryAll so restricted/hidden rows are included; initial load filters
        # IsDeleted=true in insert_into_staging before COPY (exclude_deleted_rows).
        include_deleted = True
        self.logger.info(f"🔍 DEBUG Bulk API 1.0 [{object_name}]: delete_transfer_needed={delete_transfer_needed} → include_deleted={include_deleted}")
        self.logger.info(f"🔍 DEBUG Bulk API 1.0 [{object_name}]: drop_and_reload={delete_transfer_needed} (will be passed from parent function)")
        self.logger.info(f"🔁 Bulk API 1.0 query_all_iter(include_deleted={include_deleted})")
        generator = self.sf.query_all_iter(query, include_deleted=include_deleted)
        
        async def chunk_generator():
            chunk = []
            total = 0
            last_progress_update = time_module.time()
            last_heartbeat = time_module.time()
            graceful_shutdown = False
            shutdown_metadata = None
            
            try:
                for record in generator:
                    current_time = time_module.time()
                    
                    # Process record
                    try:
                        cleaned = self._cleanup_row(record)
                        chunk.append(cleaned)
                        total += 1
                    except Exception as e:
                        self.logger.error(f"❌ Cleaning error: {e}")
                        continue
                    
                    # Update progress monitor (if available)
                    if progress_monitor:
                        # Update every 1000 records or every 60 seconds
                        if total % 1000 == 0 or (current_time - last_progress_update) > 60:
                            progress_info = progress_monitor.update_progress(total)
                            last_progress_update = current_time
                            
                            # Check if operation should continue (now returns metadata)
                            should_continue, reason, metadata = progress_monitor.should_continue()
                            
                            if not should_continue:
                                termination_type = metadata.get('termination_type')
                                
                                if termination_type == 'stuck':
                                    # Hard stop - truly stuck operation, don't trust buffered data
                                    error_msg = f"❌ OPERATION TERMINATED (STUCK): {reason}"
                                    self.logger.error(error_msg)
                                    self.logger.error(f"   Abandoning {len(chunk)} buffered records due to stuck detection")
                                    raise Exception(error_msg)
                                
                                elif termination_type == 'total_timeout':
                                    # Graceful shutdown - flush current chunk and exit cleanly
                                    progress_pct = metadata.get('progress_percent', 0)
                                    self.logger.warning(f"⚠️ GRACEFUL SHUTDOWN INITIATED: {reason}")
                                    self.logger.warning(
                                        f"   📊 Progress at timeout: {metadata['records_migrated']:,}/{metadata['estimated_total']:,} "
                                        f"records ({progress_pct:.1f}% complete)"
                                    )
                                    self.logger.warning(f"   📦 Flushing buffer with {len(chunk):,} records before termination")
                                    graceful_shutdown = True
                                    shutdown_metadata = metadata
                                    break  # Exit loop cleanly, will yield final chunk
                            
                            # Log grace period warnings
                            elif metadata.get('in_grace_period'):
                                self.logger.warning(f"⏰ {reason}")
                            
                            # Log heartbeat if interval passed
                            elif progress_monitor.should_log_heartbeat():
                                self.logger.info(progress_monitor.get_progress_summary())
                    
                    # Yield chunks
                    if len(chunk) >= chunk_size:
                        yield chunk
                        chunk = []
                
                # Yield final chunk (includes buffered records if graceful shutdown)
                if chunk:
                    if graceful_shutdown:
                        self.logger.warning(f"📦 Yielding final partial chunk: {len(chunk):,} records")
                    yield chunk
                
                # Final summary
                if progress_monitor:
                    final_progress = progress_monitor.update_progress(total)
                    
                    if graceful_shutdown:
                        # Raise special exception with metadata for partial success tracking
                        from utils.salesforce_progress_monitor import PartialMigrationTimeoutException
                        
                        self.logger.warning(
                            f"⚠️ PARTIAL MIGRATION COMPLETED:\n"
                            f"   Records migrated: {total:,}/{final_progress['estimated_records']:,}\n"
                            f"   Progress: {final_progress['progress_percent']:.1f}%\n"
                            f"   Elapsed time: {final_progress['elapsed_seconds']}s\n"
                            f"   Average rate: {final_progress['overall_rate']:.1f} records/sec"
                        )
                        
                        raise PartialMigrationTimeoutException(
                            f"Partial migration due to timeout: {total:,}/{final_progress['estimated_records']:,} records "
                            f"({final_progress['progress_percent']:.1f}%) migrated successfully",
                            records_migrated=total,
                            total_expected=final_progress['estimated_records'],
                            elapsed_seconds=final_progress['elapsed_seconds']
                        )
                    else:
                        # Normal completion
                        self.logger.info(
                            f"✅ Fallback completed successfully: {total:,} records in {final_progress['elapsed_seconds']}s "
                            f"(avg: {final_progress['overall_rate']:.1f}/sec)"
                        )
                else:
                    if graceful_shutdown:
                        self.logger.warning(f"⚠️ Partial migration: {total:,} records processed before timeout")
                    else:
                        self.logger.info(f"✅ Fallback completed: {total:,} records processed")
                    
            finally:
                # Cleanup progress monitor
                if progress_monitor:
                    try:
                        cleanup_progress_monitor(operation_id)
                    except:
                        pass

        self._fallback_chunk_generator = chunk_generator()
        return operation_id
    
    async def _estimate_record_count_for_fallback(self, query: str, object_name: str) -> int:
        """
        Estimate record count for adaptive timeout calculation.
        Uses Salesforce COUNT() query when possible, defaults to conservative estimate.
        """
        try:
            import re
            
            # Extract table name from query if object_name is unknown
            if object_name == "unknown":
                table_match = re.search(r'\bFROM\s+(\w+)', query, re.IGNORECASE)
                if table_match:
                    object_name = table_match.group(1)
            
            # Try to get count from Salesforce
            count_query = f"SELECT COUNT() FROM {object_name}"
            
            # Add WHERE clause if exists in original query
            where_match = re.search(r'\bWHERE\s+(.+?)(?:\s+ORDER\s+BY|\s+LIMIT|\s+GROUP\s+BY|$)', query, re.IGNORECASE | re.DOTALL)
            if where_match:
                where_clause = where_match.group(1).strip()
                # Remove LIMIT clause from WHERE if present
                where_clause = re.sub(r'\s+LIMIT\s+\d+', '', where_clause, flags=re.IGNORECASE)
                count_query += f" WHERE {where_clause}"
            
            self.logger.info(f"🔍 Estimating count with: {count_query}")
            
            # Execute count query with timeout
            count_result = self.sf.query(count_query)
            estimated_count = count_result.get('totalSize', 1_000_000)
            
            # Add 10% buffer for estimates
            estimated_count = int(estimated_count * 1.1)
            
            self.logger.info(f"📊 Estimated records: {estimated_count:,}")
            return estimated_count
            
        except Exception as e:
            self.logger.warning(f"⚠️ Count estimation failed: {e}, using default estimate of 1M records")
            return 1_000_000  # Conservative default


    def _cleanup_bool(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned = []
        for i, row in enumerate(rows):
            try:
                cleaned_row = self._cleanup_row(row)
                cleaned.append(cleaned_row)
            except Exception as e:
                self.logger.error(
                    f"\n❌ Failed to clean row {i}:\n"
                    f"Data: {row}\n"
                    f"Types: { {k: type(v).__name__ for k, v in row.items()} }\n"
                    f"Reason: {str(e)}"
                )
                continue
        return cleaned

    def _is_nan(self, value):
        """Check if a value is NaN in various forms"""
        if value is None:
            return False
        
        # Check for float nan
        if isinstance(value, float):
            return math.isnan(value)
        
        
        
        # Check for pandas NaN
        try:
            if pd.isna(value):
                return True
        except:
            pass
        
        # Check for string 'nan'
        if isinstance(value, str) and value.lower() == 'nan':
            return True
            
        return False
    

    def _is_nan(self, v):
        return isinstance(v, float) and math.isnan(v)
    
    def _cleanup_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        cleaned_row = {}
        for k, v in row.items():
            if k == 'attributes':
                continue

            field_type = self.FIELD_TYPE_MAP.get(k, "string")  # default fallback

            try:
                # Handle None and NaN values first
                if v is None or self._is_nan(v):
                    cleaned_row[k] = None
                    continue

                # Handle empty string by field type   
                if v == "" :
                    if field_type == "float":
                        cleaned_row[k] = None  # Use None instead of 0.0 for empty floats
                    elif field_type == "int":
                        cleaned_row[k] = None  # Use None instead of 0 for empty ints
                    elif field_type == "boolean":
                        cleaned_row[k] = False
                    elif field_type in ("datetime", "date"):
                        cleaned_row[k] = None
                    else:
                        cleaned_row[k] = None
                    continue

                # Handle boolean values
                if field_type == "boolean":
                    if v is None:
                        cleaned_row[k] = False  # Treat null as False
                    elif isinstance(v, bool):
                        cleaned_row[k] = v
                    elif isinstance(v, str):
                        v_lower = v.strip().lower()
                        if v_lower in ("true", "1", "yes", "y"):
                            cleaned_row[k] = True
                        elif v_lower in ("false", "0", "no", ""):
                            cleaned_row[k] = False
                        else:
                            cleaned_row[k] = False
                    else:
                        cleaned_row[k] = bool(v)


                # Handle numeric types
                elif field_type in ("float", "int"):
                    if isinstance(v, (int, float)) and not self._is_nan(v):
                        if field_type == "int":
                            cleaned_row[k] = int(v)
                        else:
                            cleaned_row[k] = float(v)
                    elif isinstance(v, str):
                        v_strip = v.strip().replace(",", "")
                        if v_strip == "":
                            cleaned_row[k] = None
                        else:
                            try:
                                if field_type == "int":
                                    cleaned_row[k] = int(float(v_strip))
                                else:
                                    cleaned_row[k] = float(v_strip)
                            except ValueError:
                                cleaned_row[k] = None
                    elif isinstance(v, Decimal):
                        if field_type == "int":
                            cleaned_row[k] = int(v)
                        else:
                            cleaned_row[k] = float(v)
                    else:
                        cleaned_row[k] = None

                # Handle datetime types
                elif field_type in ("datetime", "date"):
                    cleaned_row[k] = v  # No transformation



                # Handle all other types (including strings)
                else:
                    if isinstance(v, str):
                        cleaned_row[k] = v if v else ""
                    elif isinstance(v, (list, dict)):
                        cleaned_row[k] = json.dumps(v)
                    elif isinstance(v, bool):
                        cleaned_row[k] = v
                    elif isinstance(v, (int, float)) and not self._is_nan(v):
                        cleaned_row[k] = v
                    elif isinstance(v, Decimal):
                        cleaned_row[k] = float(v)
                    elif isinstance(v, (datetime, date)):
                        cleaned_row[k] = v.isoformat()
                    else:
                        cleaned_row[k] = str(v) if v is not None else ""

            except Exception as e:
                self.logger.warning(f"⚠️ Cleaning error for key={k}, value={v}, type={type(v)}: {e}")
                cleaned_row[k] = None

        # Don't skip rows with all None values - let them be inserted
        return cleaned_row


logger = logging.getLogger(__name__)


def get_last_checked_dates_and_fields(connection_id, selected_tables=None, include_history=False):
    """
    Fetches last checked dates and fields for tables associated with a connection.
    If include_history=True, includes history tables as well.
    """
    try:
        conn = postgres_connection()
        cursor = conn.cursor()

        base_query = """
        SELECT "table", last_synced, table_fields, is_history
        FROM source_table_selection
        WHERE connection_id = %s
        """
        params = [connection_id]

        if selected_tables:
            placeholders = ', '.join(['%s'] * len(selected_tables))
            base_query += f" AND \"table\" IN ({placeholders})"
            params += selected_tables

        if not include_history:
            base_query += " AND (is_history = FALSE OR is_history IS NULL)"

        cursor.execute(base_query, tuple(params))
        rows = cursor.fetchall()

        last_checked_data = {}
        for table_name, last_synced, table_fields, is_history in rows:
            parsed_fields = parse_table_fields(table_fields)
            last_checked_data[table_name] = {
                "last_checked_date": last_synced,
                "table_fields": parsed_fields,
                "is_history": bool(is_history),
            }

        return last_checked_data

    except Exception as e:
        logger.error(f"❌ Error fetching last checked data: {e}")
        return {}


credentials = compute_engine.Credentials()


storage_client = storage.Client(project=PROJECT_ID,credentials=credentials)


logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)


def check_field_exists(sf_connection, object_name: str, field_name: str) -> bool:
    """Check if a field exists in a Salesforce object"""
    try:
        describe = sf_connection.__getattr__(object_name).describe()
        field_names = [f['name'] for f in describe['fields']]
        return field_name in field_names
    except Exception as e:
        logger.error(f"Error checking field existence: {e}")
        return False


def process_salesforce_results_in_chunks(results: List[Dict], chunk_size: int):
    """Yield chunks of results"""
    for i in range(0, len(results), chunk_size):
        yield results[i:i + chunk_size]


def get_latest_salesforce_version(instance_url: str, access_token: str, logger=None) -> str:
    """
    Get the latest available Salesforce API version.
    """
    try:
        url = f"{instance_url}/services/data/"
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        versions = resp.json()
        latest_version = versions[-1]["version"]
        if logger:
            logger.info(f"🔍 Latest Salesforce API version: v{latest_version}")
        return latest_version
    except Exception as e:
        if logger:
            logger.warning(f"⚠️ Failed to fetch latest Salesforce API version. Defaulting to v59.0: {e}")
        return "62.0"  # fallback to a stable known version


def get_all_salesforce_fields(sf: Salesforce, salesforce_object: str, logger=None) -> dict:
    """Retrieve all queryable fields using describe() and enrich with Tooling API if needed."""
    try:
        describe = sf.__getattr__(salesforce_object).describe()
        primary_key = "Id"

        # ✅ Only include queryable fields to avoid SOQL errors
        describe_fields = {
            f['name']: f for f in describe['fields']
            if f.get('queryable', True)
        }

        all_fields_info = {
            name: {
                "type": f.get("type", "string").upper(),
                "is_virtual": f.get("calculated", False),
                "formula": f.get("calculatedFormula", None),
                "deprecated_and_hidden": f.get("deprecatedAndHidden", False)
            }
            for name, f in describe_fields.items()
        }

        # --- Tooling API Setup ---
        instance_url = sf.base_url.split("/services")[0]
        access_token = sf.session_id
        latest_version = get_latest_salesforce_version(instance_url, access_token, logger)
        tooling_base_url = f"{instance_url}/services/data/v{latest_version}/tooling/query"
        headers = {"Authorization": f"Bearer {access_token}"}

        tooling_query = f"""
            SELECT QualifiedApiName
            FROM FieldDefinition
            WHERE EntityDefinition.QualifiedApiName = '{salesforce_object}'
        """
        tooling_resp = requests.get(tooling_base_url, headers=headers, params={"q": tooling_query.strip()})
        tooling_resp.raise_for_status()

        tooling_records = tooling_resp.json().get("records", [])
        for rec in tooling_records:
            field_name = rec["QualifiedApiName"]
            if field_name not in all_fields_info:
                # Skip unknown/unqueryable fields
                continue

            # Optional: enrich field info from Tooling API (if required)
            meta_query = f"""
                SELECT QualifiedApiName, DataType, IsCalculated, Metadata
                FROM FieldDefinition
                WHERE EntityDefinition.QualifiedApiName = '{salesforce_object}'
                AND QualifiedApiName = '{field_name}'
            """
            meta_resp = requests.get(tooling_base_url, headers=headers, params={"q": meta_query.strip()})
            if meta_resp.status_code != 200:
                if logger:
                    logger.warning(f"⚠️ Tooling metadata fetch failed for {field_name}: {meta_resp.text}")
                continue

            meta_record = meta_resp.json().get("records", [])[0]
            all_fields_info[field_name]["type"] = meta_record.get("DataType", "string").upper()
            all_fields_info[field_name]["is_virtual"] = meta_record.get("IsCalculated", False)

            metadata_raw = meta_record.get("Metadata")
            try:
                metadata_json = metadata_raw if isinstance(metadata_raw, dict) else json.loads(metadata_raw)
                if "CustomField" in metadata_json:
                    all_fields_info[field_name]["formula"] = metadata_json["CustomField"].get("formula")
                    all_fields_info[field_name]["deprecated_and_hidden"] = metadata_json["CustomField"].get("deprecatedAndHidden", False)
                elif "StandardField" in metadata_json:
                    all_fields_info[field_name]["deprecated_and_hidden"] = metadata_json["StandardField"].get("deprecatedAndHidden", False)
            except Exception as parse_err:
                if logger:
                    logger.warning(f"⚠️ Metadata parse failed for {field_name}: {parse_err}")
            time.sleep(0.02)

        # Ensure primary key is present
        if primary_key not in all_fields_info:
            all_fields_info[primary_key] = {
                "type": "ID",
                "is_virtual": False,
                "formula": None,
                "deprecated_and_hidden": False
            }

        if logger:
            logger.info(f"✅ Total queryable fields collected for {salesforce_object}: {len(all_fields_info)}")
        return all_fields_info

    except Exception as e:
        if logger:
            logger.warning(f"⚠️ Failed to retrieve full Tooling API fields: {e}")
        # Fallback: safe describe fields only
        try:
            describe = sf.__getattr__(salesforce_object).describe()
            fallback_fields = {
                f['name']: f for f in describe['fields']
                if f.get('queryable', True)
            }
            fallback_info = {
                name: {
                    "type": f.get("type", "string").upper(),
                    "is_virtual": f.get("calculated", False),
                    "formula": f.get("calculatedFormula", None),
                    "deprecated_and_hidden": f.get("deprecatedAndHidden", False)
                }
                for name, f in fallback_fields.items()
            }
            if primary_key not in fallback_info:
                fallback_info[primary_key] = {
                    "type": "ID",
                    "is_virtual": False,
                    "formula": None,
                    "deprecated_and_hidden": False
                }
            return fallback_info
        except Exception as fallback_e:
            if logger:
                logger.error(f"❌ Fallback failed for {salesforce_object}: {fallback_e}")
            raise


# Chatter feed objects reject SystemModstamp in SOQL and require an Id predicate.
SALESFORCE_NO_SYSTEM_MODSTAMP_OBJECTS = frozenset({"FeedItem", "FeedComment"})
SALESFORCE_REQUIRES_ID_FILTER_OBJECTS = frozenset({"FeedItem", "FeedComment"})
SALESFORCE_FEED_ID_KEYSET_FLOOR = "000000000000000AAA"


def resolve_salesforce_watermark_field(
    object_name: str,
    *,
    is_history: bool,
    has_system_modstamp: bool,
    has_last_modified_date: bool,
    has_last_modified: bool,
    has_login_time: bool,
    has_created_date: bool = True,
) -> Optional[str]:
    """Choose SOQL watermark column; feed objects must not use SystemModstamp."""
    if is_history:
        return "CreatedDate" if has_created_date else None
    if object_name in SALESFORCE_NO_SYSTEM_MODSTAMP_OBJECTS:
        return "CreatedDate" if has_created_date else None
    if has_system_modstamp:
        return "SystemModstamp"
    if has_last_modified_date:
        return "LastModifiedDate"
    if has_last_modified:
        return "LastModified"
    if has_login_time:
        return "LoginTime"
    if has_created_date:
        return "CreatedDate"
    return None


def build_salesforce_extract_where_clause(
    object_name: str,
    *,
    is_incremental_sync: bool,
    is_history: bool,
    lower_ts: Optional[str] = None,
    upper_ts: Optional[str] = None,
    has_system_modstamp: bool = False,
    has_last_modified_date: bool = False,
    has_last_modified: bool = False,
    has_login_time: bool = False,
    has_created_date: bool = True,
    last_id: Optional[str] = None,
) -> str:
    """
    Build WHERE (and optional ORDER BY) for Salesforce bulk extract.

    FeedItem/FeedComment cannot use SystemModstamp and require ``Id`` in the filter.
    """
    watermark_field = resolve_salesforce_watermark_field(
        object_name,
        is_history=is_history,
        has_system_modstamp=has_system_modstamp,
        has_last_modified_date=has_last_modified_date,
        has_last_modified=has_last_modified,
        has_login_time=has_login_time,
        has_created_date=has_created_date,
    )
    clauses = []

    if object_name in SALESFORCE_REQUIRES_ID_FILTER_OBJECTS:
        id_floor = (last_id or SALESFORCE_FEED_ID_KEYSET_FLOOR).replace("'", "")
        clauses.append(f"Id > '{id_floor}'")

    if is_incremental_sync and lower_ts and upper_ts and watermark_field:
        if watermark_field == "CreatedDate":
            clauses.append(f"CreatedDate > {lower_ts} AND CreatedDate <= {upper_ts}")
        elif watermark_field == "SystemModstamp":
            clauses.append(
                f"SystemModstamp > {lower_ts} AND SystemModstamp <= {upper_ts}"
            )
        elif watermark_field == "LastModifiedDate":
            clauses.append(
                f"LastModifiedDate > {lower_ts} AND LastModifiedDate <= {upper_ts}"
            )
        elif watermark_field == "LastModified":
            clauses.append(
                f"LastModified > {lower_ts} AND LastModified <= {upper_ts}"
            )
        elif watermark_field == "LoginTime":
            clauses.append(f"LoginTime > {lower_ts} AND LoginTime <= {upper_ts}")
    elif not is_incremental_sync and upper_ts and watermark_field:
        if watermark_field == "CreatedDate":
            clauses.append(f"CreatedDate <= {upper_ts}")
        elif watermark_field == "SystemModstamp":
            clauses.append(f"SystemModstamp <= {upper_ts}")

    if not clauses:
        return ""
    suffix = " ORDER BY Id" if object_name in SALESFORCE_REQUIRES_ID_FILTER_OBJECTS else ""
    return f" WHERE {' AND '.join(clauses)}{suffix}"


def is_feeditem_id_filter_error(message: str) -> bool:
    msg = str(message or "").lower()
    return "feeditem requires a filter by id" in msg or (
        "requires a filter by id" in msg and "feeditem" in msg
    )


def is_unsupported_salesforce_object(object_name: str, sf_client=None) -> bool:
    """
    Check if a Salesforce object is unsupported for query operations.
    
    🚀 NEW: This function now uses DYNAMIC Salesforce API calls to check if objects 
    are queryable in real-time, eliminating the need for any hardcoded lists!
    
    The function uses the Salesforce Describe API to determine:
    - Whether the object exists in the org
    - Whether it's queryable 
    - Whether it's retrieveable
    - Whether it's deprecated
    
    Results are cached for performance (60 minutes by default).
    
    Args:
        object_name (str): The Salesforce object name to check
        sf_client: Optional Salesforce client (uses global if available)
        
    Returns:
        bool: True if the object is unsupported/not queryable, False if queryable
        
    Example:
        >>> is_unsupported_salesforce_object('Account')
        False  # Account is queryable
        >>> is_unsupported_salesforce_object('Account__ChangeEvent') 
        True   # ChangeEvent objects are not queryable
    """
    # Invert the result since we're checking for "unsupported" 
    # but the dynamic checker returns "queryable"
    is_queryable = is_salesforce_object_queryable(object_name, sf_client)
    return not is_queryable  # Unsupported = NOT queryable


def update_last_synced_date(connection_id: int, table_name: str, sync_time: datetime):
    """Update the last synced date for a table"""
    try:
        conn = postgres_connection()
        cursor = conn.cursor()
        
        update_query = """
        UPDATE source_table_selection 
        SET last_synced = %s 
        WHERE connection_id = %s AND "table" = %s
        """
        
        cursor.execute(update_query, (sync_time.isoformat(), connection_id, table_name))
        conn.commit()

        if cursor.rowcount == 0:
            logger.warning(
                "No source_table_selection row updated for %s (connection_id=%s)",
                table_name,
                connection_id,
            )
        else:
            logger.info(f"✅ Updated last_synced date for {table_name} to {sync_time}")
        
    except Exception as e:
        logger.error(f"❌ Error updating last_synced date: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


logger = logging.getLogger(__name__)


def create_log_files(logs_creation: bool, company_name: str, connection_name: str,
                     connection_id: int, salesforce_object: str) -> Optional[Dict[str, Path]]:
    """Create log file paths for Salesforce extraction logging"""

    if not logs_creation:
        logger.info("🛑 Log creation disabled. Skipping log file setup.")
        return None

    try:
        base_log_dir = "/home/readvinode/salesforce_to_snowflake_extract_logs"
        log_dir = create_log_paths(base_log_dir, company_name)
        log_dir = Path(log_dir)

        # ✅ Ensure the directory exists
        os.makedirs(log_dir, exist_ok=True)

        if os.path.isdir(log_dir):
            logger.info(f"📁 Log directory created or already exists: {log_dir}")
        else:
            logger.warning(f"⚠️ Expected log directory not found after creation: {log_dir}")

        safe_connection_name = re.sub(r'\W+', '_', connection_name)
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')

        paths = {
            'csv_file': log_dir / f"{safe_connection_name}_{connection_id}_{salesforce_object}_extract_{timestamp_str}.csv",
            'response_file': log_dir / f"sf_extract_response_{safe_connection_name}_{connection_id}_{salesforce_object}_{timestamp_str}.jsonl",
            'copy_error_file': log_dir / f"{safe_connection_name}_{connection_id}_copy_errors_log.csv"
        }

        logger.info(f"📝 Log file paths created: {paths}")
        return paths

    except Exception as e:
        logger.error(f"❌ Failed to create log files: {e}", exc_info=True)
        return None


def log_chunk_data_with_operation(csv_file: Path, chunk: List[Dict], source_table: str,
                                 destination_table: str, connection_name: str, 
                                 chunk_index: int, merge_result: Dict):
    """Log chunk data to CSV file with operation details"""
    df = pd.DataFrame(chunk)
    
    # Add metadata columns
    df['chunk_index'] = chunk_index
    df['operation_timestamp'] = datetime.now().isoformat()
    df['records_inserted'] = merge_result['inserted']
    df['records_updated'] = merge_result['updated']
    
    file_exists = csv_file.is_file()
    with open(csv_file, 'a', encoding='utf-8') as f:
        if not file_exists:
            f.write(f"# Connection Name: {connection_name}\n")
            f.write("# Migration Type: Salesforce ➜ Snowflake\n")
            f.write(f"# Migration Tables: {source_table} ➜ {destination_table}\n")
            f.write(f"# Timestamp: {datetime.now().isoformat()}\n")
            f.write("# Operation: MERGE (INSERT/UPDATE)\n\n")
            df.to_csv(f, index=False)
        else:
            df.to_csv(f, index=False, header=False)


def log_error_to_response_file(response_file: Path, error: Exception, chunk_index: int):
    """Log error details to response file"""
    error_record = {
        "timestamp": datetime.now().isoformat(),
        "chunk_index": chunk_index,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": tb_module.format_exc()
    }
    
    with open(response_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(error_record) + "\n")


update_table_status_snowflake = update_table_status_sf_to_snowflake
