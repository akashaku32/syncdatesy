"""NetSuite connector configuration validation."""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

MIGRATION_ROOT = Path(__file__).resolve().parents[1]
if str(MIGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(MIGRATION_ROOT))

from services.netsuite.config import (
    METHOD_SUITEQL,
    normalize_config,
    suiteanalytics_host,
    suitetalk_base_url,
    validate_for_method,
)


def test_normalize_suiteql_requires_account():
    with pytest.raises(HTTPException):
        normalize_config({"connection_method": METHOD_SUITEQL})


def test_validate_suiteql_fields():
    config = normalize_config(
        {
            "connection_method": "suiteql_rest",
            "account_id": "1234567_SB1",
            "client_id": "abc",
            "certificate_id": "cert1",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----",
        }
    )
    validate_for_method(config)
    assert "1234567-sb1" in suitetalk_base_url(config["account_id"])


def test_suiteanalytics_host_from_account():
    assert suiteanalytics_host("1234567_SB1") == "1234567-sb1.connect.api.netsuite.com"


def test_validate_suiteanalytics_tba_fields():
    config = normalize_config(
        {
            "connection_method": "suiteanalytics",
            "account_id": "1234567",
            "connect_auth_type": "tba",
            "role_id": "3",
            "consumer_key": "ck",
            "consumer_secret": "cs",
            "token_id": "ti",
            "token_secret": "ts",
        }
    )
    validate_for_method(config)


def test_validate_suiteanalytics_oauth2_fields():
    config = normalize_config(
        {
            "connection_method": "suiteanalytics",
            "account_id": "1234567",
            "connect_auth_type": "oauth2_m2m",
            "role_id": "3",
            "connect_client_id": "cid",
            "connect_certificate_id": "cert",
            "connect_private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        }
    )
    validate_for_method(config)
