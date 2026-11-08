# apps/elttool/v1/utils.py
from django.utils import timezone
from datetime import timedelta
import json
import os
from typing import Optional

import requests
from django.conf import settings

DEFAULT_MIGRATION_API_TIMEOUT_SECONDS = 30
DEFAULT_SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS = 120
DEFAULT_DATABRICKS_WAREHOUSE_COLD_START_SECONDS = 900
SALESFORCE_CONNECTOR_NAMES = frozenset({"Salesforce", "SalesforceSandbox"})


def databricks_warehouse_cold_start_seconds() -> int:
    raw = os.getenv("DATABRICKS_WAREHOUSE_COLD_START_SECONDS", "").strip()
    if not raw:
        return DEFAULT_DATABRICKS_WAREHOUSE_COLD_START_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_DATABRICKS_WAREHOUSE_COLD_START_SECONDS


def _migration_api_timeout_for_connector(connector_name: str) -> int:
    """HTTP timeout for one connector's authorization / connection-test call."""
    name = (connector_name or "").strip()
    if not name:
        return DEFAULT_MIGRATION_API_TIMEOUT_SECONDS
    if name == "Databricks":
        raw = os.getenv("DATABRICKS_AUTH_REQUEST_TIMEOUT_SECONDS", "").strip()
        if raw:
            try:
                return max(60, int(raw))
            except ValueError:
                pass
        return databricks_warehouse_cold_start_seconds() + 60
    if name in SALESFORCE_CONNECTOR_NAMES:
        raw = os.getenv("SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS", "").strip()
        if raw:
            try:
                return max(60, int(raw))
            except ValueError:
                pass
        return DEFAULT_SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS
    return DEFAULT_MIGRATION_API_TIMEOUT_SECONDS


def migration_api_timeout_seconds(*connector_names: Optional[str]) -> int:
    """
    HTTP timeout for Django -> migration-function authorization calls.

    When multiple connectors participate in the same save (source + destination),
    pass every connector name and the longest applicable timeout is used — e.g.
    Salesforce source + Databricks destination waits for warehouse/cluster cold
    start (up to DATABRICKS_AUTH_REQUEST_TIMEOUT_SECONDS), not the 30s default.
    """
    names = [(name or "").strip() for name in connector_names if (name or "").strip()]
    if not names:
        return DEFAULT_MIGRATION_API_TIMEOUT_SECONDS
    return max(_migration_api_timeout_for_connector(name) for name in names)


def get_migration_api_url(endpoint: str) -> str:
    """
    Build migration-function URL for server-to-server calls from Django.

    Uses the in-cluster service when configured so authorization does not depend
    on ingress/nginx (avoids transient 503s when migration-function restarts).
    """
    endpoint = (endpoint or "").lstrip("/")
    use_internal = getattr(settings, "USE_INTERNAL_MIGRATION_API", True)
    internal_base = (
        getattr(settings, "MIGRATION_FUNCTION_INTERNAL_URL", None) or ""
    ).strip().rstrip("/")
    if use_internal and internal_base:
        return f"{internal_base}/{endpoint}"
    public_domain = getattr(settings, "DOMAIN", "qa.datasyncher.com")
    return f"https://{public_domain}/api-new/{endpoint}"


def internal_service_headers(extra: Optional[dict] = None) -> dict:
    """Headers for Django → FastAPI / migration-function calls (X-INTERNAL-KEY)."""
    headers = {"Content-Type": "application/json"}
    key = getattr(settings, "INTERNAL_API_KEY", None) or os.getenv("INTERNAL_API_KEY")
    if key:
        headers["X-INTERNAL-KEY"] = str(key)
    if extra:
        headers.update(extra)
    return headers


def get_trial_status(company):
    """
    Get comprehensive trial status information for a company.
    
    Args:
        company: Company model instance
    
    Returns:
        dict: Trial status information including:
            - subscription_exists: bool
            - trial_pending: bool
            - trial_expired: bool
            - days_remaining: int
            - trial_start_date: datetime
            - trial_end_date: datetime
            - is_trial: bool
            - warning_message: str or None
    """
    print("=" * 80)
    print("🔍 [get_trial_status] Calculating trial status")
    
    if not company:
        print("   ❌ Company is None - returning None")
        print("=" * 80)
        return None
    
    print(f"   Company ID: {company.cmp_id}")
    print(f"   Is Trial: {company.is_trial}")
    print(f"   Plan: {company.plan}")
    print(f"   Start Date: {company.start_date}")
    print(f"   End Date: {company.end_date}")
    print(f"   Current Time: {timezone.now()}")
    
    # Calculate subscription status
    subscription_exists = not company.is_trial
    trial_pending = company.is_trial and company.end_date > timezone.now()
    trial_expired = company.is_trial and company.end_date <= timezone.now()
    
    print(f"   Subscription Exists: {subscription_exists}")
    print(f"   Trial Pending: {trial_pending}")
    print(f"   Trial Expired: {trial_expired}")
    
    # Calculate days remaining
    days_remaining = 0
    if trial_pending:
        days_remaining = max(0, (company.end_date - timezone.now()).days)
        print(f"   Days Remaining: {days_remaining}")
    elif trial_expired:
        days_overdue = (timezone.now() - company.end_date).days
        print(f"   Days Overdue: {days_overdue}")
    
    # Generate warning message if trial expired
    warning_message = None
    if trial_expired and not subscription_exists:
        warning_message = 'Your trial period has ended. Subscribe now to enjoy the full benefits of datasyncher.com.'
        print(f"   ⚠️  Warning Message: {warning_message}")
    
    result = {
        'subscription_exists': subscription_exists,
        'trial_pending': trial_pending,
        'trial_expired': trial_expired,
        'days_remaining': days_remaining,
        'trial_start_date': company.start_date,
        'trial_end_date': company.end_date,
        'is_trial': company.is_trial,
        'warning_message': warning_message,
        'plan': company.plan,
    }
    
    print("   ✅ Trial status calculated successfully")
    print("=" * 80)
    return result


def get_field_read_only(attribute, has_data_transfers):
    """
    Get read_only value for a field based on DataTransfer records.
    
    Args:
        attribute: DestinationAttribute or SourceAttribute instance
        has_data_transfers: bool - whether DataTransfer records exist
    
    Returns:
        bool: read_only value (False = editable, True = read-only)
    """
    if has_data_transfers:
        # Use DB value when DataTransfer exists
        return attribute.read_only
    else:
        # All fields editable when no DataTransfer
        return False


def refresh_salesforce_token(refresh_token, client_id, client_secret, environment, custom_domain=''):
    """
    Refresh Salesforce access token using the provided refresh token.
    Reuses the same logic from backend/migration-function/services/salesforce_service.py
    
    Args:
        refresh_token: The refresh token to use
        client_id: Salesforce CLIENT_ID
        client_secret: Salesforce CLIENT_SECRET
        environment: "production" or "sandbox"
        custom_domain: Optional custom Salesforce domain
    
    Returns:
        dict: {
            'access_token': str,
            'instance_url': str,
            'refresh_token': str (new one if provided, otherwise original)
        }
    
    Raises:
        ValueError: If refresh token is invalid/expired or other errors occur
    """
    if not refresh_token:
        raise ValueError("Refresh token is required to refresh the access token.")
    
    if not client_id:
        raise ValueError("CLIENT_ID is required to refresh the access token.")
    
    if not client_secret:
        raise ValueError("CLIENT_SECRET is required to refresh the access token.")
    
    # Build token URL (same logic as service file)
    if environment == "sandbox":
        token_url = "https://test.salesforce.com/services/oauth2/token"
    else:
        if custom_domain == '':
            token_url = "https://login.salesforce.com/services/oauth2/token"
        else:
            token_url = f"https://{custom_domain}.my.salesforce.com/services/oauth2/token"
    
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    
    try:
        response = requests.post(token_url, data=payload, verify=False)
        if response.status_code != 200:
            error_detail = response.text
            print(f"❌ Token refresh failed with status {response.status_code}: {error_detail}")
            
            # Check for specific error cases
            if response.status_code == 400:
                try:
                    error_data = response.json()
                    if 'error' in error_data:
                        if error_data['error'] == 'invalid_grant':
                            raise ValueError("Refresh token has expired or is invalid. Please re-authenticate.")
                        elif error_data['error'] == 'invalid_client':
                            raise ValueError("Invalid CLIENT_ID or CLIENT_SECRET. Please check your OAuth credentials.")
                        else:
                            raise ValueError(f"OAuth error: {error_data.get('error_description', error_data['error'])}")
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
        
        # Return dict with tokens (refresh_token only if Salesforce provides new one)
        result = {
            'access_token': new_access_token,
            'instance_url': new_instance_url,
            'refresh_token': new_data.get('refresh_token') or refresh_token,  # Use new if provided, otherwise keep original
        }
        
        return result
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Network error during token refresh: {str(e)}")
    except Exception as e:
        raise ValueError(f"Unexpected error during token refresh: {str(e)}")


def ensure_destination_schema(destination_config, destination_schema, domain=None):
    """
    Ensure destination schema exists by calling destination authorization.
    
    This function triggers schema creation for destinations that support it
    (e.g., Snowflake, BigQuery, Redshift) by calling the destination authorization API.
    
    Args:
        destination_config: DestinationConfig instance
        destination_schema: Schema name to create
        domain: API domain (defaults to settings.DOMAIN)
    
    Returns:
        bool: True if schema creation succeeded or was skipped, False on critical error
    """
    if not destination_schema:
        return True
    
    if not domain:
        domain = getattr(settings, 'DOMAIN', 'qa.datasyncher.com')
    
    try:
        schema_payload = {
            destination_config.dst.name: destination_config.data,
            "destination_schema": destination_schema
        }
        
        dest_api_url = get_migration_api_url(
            f"{destination_config.dst.name}-Authorization"
        )
        
        print(f"🔧 Ensuring destination schema: {destination_schema}")
        print(f"   Destination: {destination_config.dst.name}")
        print(f"   API URL: {dest_api_url}")
        
        response = requests.post(
            dest_api_url,
            data=json.dumps(schema_payload),
            headers=internal_service_headers(),
            verify=False,
            timeout=migration_api_timeout_seconds(destination_config.dst.name),
        )
        
        if response.status_code == 200:
            try:
                result = response.json()
                if result.get("status_code") == 200:
                    print(f"✅ Schema '{destination_schema}' ensured successfully")
                    return True
            except (json.JSONDecodeError, ValueError) as json_err:
                print(f"⚠️ Response is not valid JSON: {json_err}")
                print(f"   Response text: {response.text[:200]}")
        
        print(f"⚠️ Schema creation returned non-200 (may already exist)")
        return True
        
    except requests.exceptions.RequestException as req_err:
        print(f"⚠️ Schema creation network error (non-blocking): {str(req_err)}")
        return True
    except Exception as e:
        print(f"⚠️ Schema creation error (non-blocking): {str(e)}")
        return True


def validate_sap_row_filter_dict(value: dict, table_fields: dict = None) -> dict:
    """
    Validate SAP row filter dictionary structure cleanly within Django layer.
    Eliminates cross-container sys.path imports from backend/migration-function.
    """
    if not value or not isinstance(value, dict):
        return {}

    # ``logic`` is the canonical key.  ``operator`` is accepted only as a
    # backwards-compatible alias used by older clients.
    logic = str(value.get("logic") or value.get("operator") or "AND").strip().lower()
    if logic not in ("and", "or"):
        raise ValueError(f"Invalid logical operator '{logic}'. Must be 'and' or 'or'.")

    conditions = value.get("conditions", [])
    if not isinstance(conditions, list):
        raise ValueError("Filter conditions must be a list.")

    schema_map = table_fields if isinstance(table_fields, dict) else {}

    operator_aliases = {
        "=": "eq", "==": "eq", "!=": "ne", "<>": "ne",
        ">": "gt", ">=": "ge", "<": "lt", "<=": "le",
    }
    allowed_by_type = {
        "string": {"eq", "ne", "gt", "ge", "lt", "le", "startswith", "in"},
        "numeric": {"eq", "ne", "gt", "ge", "lt", "le", "in"},
        "datetime": {"eq", "ne", "gt", "ge", "lt", "le", "in"},
        "time": {"eq", "ne", "gt", "ge", "lt", "le", "in"},
        "boolean": {"eq"},
    }
    edm_to_type = {
        "edm.string": "string", "edm.guid": "string", "edm.binary": "string",
        "edm.byte": "numeric", "edm.sbyte": "numeric", "edm.int16": "numeric",
        "edm.int32": "numeric", "edm.int64": "numeric", "edm.decimal": "numeric",
        "edm.double": "numeric", "edm.single": "numeric",
        "edm.boolean": "boolean", "edm.datetime": "datetime",
        "edm.datetimeoffset": "datetime", "edm.time": "time",
    }

    def normalize_node(cond):
        if not isinstance(cond, dict):
            raise ValueError("Each row-filter node must be an object.")

        if "not" in cond:
            if cond.get("not") in (None, ""):
                raise ValueError("'not' requires one child condition or group.")
            return {"not": normalize_node(cond["not"])}

        if "conditions" in cond:
            child_logic = str(cond.get("logic") or cond.get("operator") or "and").strip().lower()
            if child_logic not in ("and", "or"):
                raise ValueError("Nested logical groups must use 'and' or 'or'.")
            child_conditions = cond.get("conditions")
            if not isinstance(child_conditions, list) or not child_conditions:
                raise ValueError("Nested logical groups require a non-empty conditions list.")
            return {
                "logic": child_logic,
                "conditions": [normalize_node(child) for child in child_conditions],
            }

        # Accept both "column" (frontend key) and "field" (canonical internal key)
        field = cond.get("column") or cond.get("field")
        cond_op = str(cond.get("operator") or "eq").strip().lower()
        cond_op = operator_aliases.get(cond_op, cond_op)
        val = cond.get("value")
        edm_type = cond.get("edm_type") or cond.get("edmType")

        if not field:
            raise ValueError("Each row-filter condition requires a column/field.")

        meta = schema_map.get(field) if schema_map else None
        if meta is None and schema_map:
            matched_key = next(
                (key for key in schema_map if str(key).lower() == str(field).lower()),
                None,
            )
            if matched_key is not None:
                field = matched_key
                meta = schema_map[matched_key]
        if meta is not None:
            if isinstance(meta, dict) and meta.get("filterable") is False:
                raise ValueError(f"Field '{field}' is marked as non-filterable in SAP schema.")
            if isinstance(meta, dict):
                edm_type = edm_type or meta.get("edm_type") or meta.get("edmType") or meta.get("type")

        filter_type = edm_to_type.get(str(edm_type or "").strip().lower())
        if filter_type is None:
            raise ValueError(f"Field '{field}' has an unsupported or missing SAP EDM type.")
        if cond_op not in allowed_by_type[filter_type]:
            raise ValueError(
                f"Operator '{cond_op}' is not supported for SAP {filter_type} field '{field}'."
            )
        if cond_op == "in":
            if not isinstance(val, list) or not val:
                raise ValueError(f"Operator 'in' for '{field}' requires a non-empty list.")
        elif val is None and cond_op not in {"eq", "ne"}:
            raise ValueError(f"Operator '{cond_op}' for '{field}' requires a value.")
        elif isinstance(val, str) and not val.strip() and cond_op not in {"eq", "ne"}:
            raise ValueError(f"Operator '{cond_op}' for '{field}' requires a value.")
        if cond_op == "startswith" and isinstance(val, (list, dict)):
            raise ValueError(f"Operator 'startswith' for '{field}' requires one scalar prefix.")

        return {
            "column": str(field).strip(),
            "operator": str(cond_op or "eq").strip().lower(),
            "value": val,
            "edm_type": edm_type,
        }

    return {
        "logic": logic,
        "conditions": [normalize_node(cond) for cond in conditions],
    }

