"""Salesforce listing model accepts OAuth configs with null optional fields."""

from models.salesforce_models import SalesforceListing, SalesforceSandboxListing
from utils.config_helpers import drop_none_config_values, sanitize_listing_payload


def test_salesforce_listing_accepts_null_optional_oauth_fields():
    payload = {
        "connection_id": 25,
        "job_name": "salesforce_databricks_con_listing_tables_4766",
        "last_checked_date": None,
        "Salesforce": {
            "CLIENT_ID": "client-id",
            "CLIENT_SECRET": "client-secret",
            "CUSTOM_DOMAIN": "",
            "access_token": "token",
            "refresh_token": "refresh",
            "instance_url": "https://example.my.salesforce.com",
            "expires_in": None,
            "token_expires_at": None,
            "token_type": None,
            "folder_id": None,
            "folder_name": None,
            "state": "oauth-state",
        },
        "cmp_id": 66,
        "user_id": 61,
        "is_source": True,
        "log_id": 1,
    }
    req = SalesforceListing(**payload)
    assert req.Salesforce["access_token"] == "token"
    assert req.Salesforce["expires_in"] is None


def test_salesforce_sandbox_listing_accepts_null_optional_oauth_fields():
    payload = {
        "connection_id": 25,
        "job_name": "sandbox_listing",
        "SalesforceSandbox": {
            "access_token": "token",
            "refresh_token": "refresh",
            "instance_url": "https://example.my.salesforce.com",
            "folder_id": None,
        },
        "cmp_id": 66,
        "user_id": 61,
        "is_source": True,
    }
    req = SalesforceSandboxListing(**payload)
    assert req.SalesforceSandbox["folder_id"] is None


def test_drop_none_config_values_removes_null_keys():
    assert drop_none_config_values(
        {"access_token": "a", "folder_id": None, "state": "s"}
    ) == {"access_token": "a", "state": "s"}


def test_sanitize_listing_payload_strips_nulls_from_nested_config():
    payload = {
        "Salesforce": {"access_token": "a", "folder_name": None},
        "connection_id": 1,
    }
    sanitize_listing_payload(payload, ("Salesforce",))
    assert payload["Salesforce"] == {"access_token": "a"}
