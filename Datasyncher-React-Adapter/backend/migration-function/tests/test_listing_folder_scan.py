"""Tests for listing date-folder sync helpers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services import listing_tables_service as lts


def test_build_listing_prefixes_single_day():
    prefixes = lts.build_listing_prefixes("2026-06-17T10.00.00Z")
    assert prefixes == ["2026-06-17"]


def test_build_listing_prefixes_spans_multiple_days():
    start = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")
    with patch.object(lts, "LISTING_INCREMENTAL_MONTH_PREFIX_MIN_DAYS", 9999):
        prefixes = lts.build_listing_prefixes(f"{start}T00.00.00Z")
    assert prefixes[0] == start
    assert prefixes[-1] == datetime.utcnow().strftime("%Y-%m-%d")
    assert len(prefixes) >= 3


def test_resolve_folder_scan_mode_full_when_no_cache():
    with patch.object(lts, "has_cached_date_directories", return_value=False):
        mode, cursor = lts.resolve_folder_scan_mode(12, 1)
    assert mode == "full"
    assert cursor is None


def test_resolve_folder_scan_mode_incremental_when_cache_exists():
    with patch.object(lts, "has_cached_date_directories", return_value=True), patch.object(
        lts, "get_last_checked_date_directories", return_value="2026-06-10T01.00.00Z"
    ):
        mode, cursor = lts.resolve_folder_scan_mode(1, 1)
    assert mode == "incremental"
    assert cursor == "2026-06-10T01.00.00Z"


def test_resolve_folder_scan_mode_full_when_cache_row_empty():
    with patch.object(lts, "has_cached_date_directories", return_value=True), patch.object(
        lts, "get_last_checked_date_directories", return_value=None
    ):
        mode, cursor = lts.resolve_folder_scan_mode(12, 1)
    assert mode == "full"
    assert cursor is None


def test_format_connection_last_synced_datetime():
    dt = datetime(2026, 6, 16, 13, 22, 26, tzinfo=timezone.utc)
    assert lts._format_connection_last_synced(dt) == "2026-06-16T13.22.26Z"
