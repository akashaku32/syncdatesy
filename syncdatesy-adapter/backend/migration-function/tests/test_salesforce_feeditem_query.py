"""Salesforce FeedItem / feed-object SOQL filter tests."""

import sys
from unittest.mock import MagicMock

if "google.cloud.storage" not in sys.modules:
    sys.modules["google.cloud.storage"] = MagicMock()
    sys.modules["google.cloud"] = MagicMock()

from services.salesforce.client import (
    build_salesforce_extract_where_clause,
    resolve_salesforce_watermark_field,
)


def test_feeditem_uses_created_date_not_system_modstamp():
    field = resolve_salesforce_watermark_field(
        "FeedItem",
        is_history=False,
        has_system_modstamp=True,
        has_last_modified_date=True,
        has_last_modified=True,
        has_login_time=False,
        has_created_date=True,
    )
    assert field == "CreatedDate"


def test_feeditem_full_load_where_includes_id_and_created_date():
    clause = build_salesforce_extract_where_clause(
        "FeedItem",
        is_incremental_sync=False,
        is_history=False,
        upper_ts="2026-09-22T07:03:17+0000",
        has_system_modstamp=True,
        has_created_date=True,
    )
    assert "Id >" in clause
    assert "CreatedDate <=" in clause
    assert "SystemModstamp" not in clause
    assert clause.endswith("ORDER BY Id")


def test_account_still_uses_system_modstamp():
    clause = build_salesforce_extract_where_clause(
        "Account",
        is_incremental_sync=False,
        is_history=False,
        upper_ts="2026-09-22T07:03:17+0000",
        has_system_modstamp=True,
        has_created_date=True,
    )
    assert clause == " WHERE SystemModstamp <= 2026-09-22T07:03:17+0000"
    assert "Id >" not in clause


def test_feeditem_incremental_uses_created_date_window():
    clause = build_salesforce_extract_where_clause(
        "FeedItem",
        is_incremental_sync=True,
        is_history=False,
        lower_ts="2026-09-21T00:00:00+0000",
        upper_ts="2026-09-22T07:03:17+0000",
        has_system_modstamp=True,
        has_created_date=True,
    )
    assert "Id >" in clause
    assert "CreatedDate >" in clause
    assert "CreatedDate <=" in clause
    assert "SystemModstamp" not in clause
