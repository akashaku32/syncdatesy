"""
Tests for calendar-window helpers used when replaying Dynamics exports (e.g. ledgerjournaltable).

These stay out of ``microsoft_dynamics_migration_service`` so production ``migrate()`` is unchanged.
"""
from datetime import date

import pytest

from utils.dataverse_migration_date_utils import (
    dataverse_folder_calendar_day,
    filter_date_directories_calendar_inclusive,
    last_checked_sentinel_before_calendar_start,
    parse_dataverse_calendar_day,
)


def test_parse_dataverse_calendar_day_iso():
    assert parse_dataverse_calendar_day("2026-01-13") == date(2026, 1, 13)
    assert parse_dataverse_calendar_day("  2026/01/13 ") == date(2026, 1, 13)


def test_parse_dataverse_calendar_day_none():
    assert parse_dataverse_calendar_day(None) is None
    assert parse_dataverse_calendar_day("") is None


def test_parse_dataverse_calendar_day_invalid():
    with pytest.raises(ValueError):
        parse_dataverse_calendar_day("not-a-date")


def test_dataverse_folder_calendar_day():
    assert dataverse_folder_calendar_day("2026-01-13T16.59.09Z") == date(2026, 1, 13)


def test_filter_date_directories_calendar_inclusive_ledger_window_example():
    """Example: keep only folders from 2026-01-13 through 2026-05-01 (inclusive)."""
    dirs = [
        "2026-01-12T16.59.09Z",
        "2026-01-13T16.59.09Z",
        "2026-04-09T17.59.09Z",
        "2026-05-01T03.59.09Z",
        "2026-05-02T16.59.09Z",
    ]
    start = parse_dataverse_calendar_day("2026-01-13")
    end = parse_dataverse_calendar_day("2026-05-01")
    got = filter_date_directories_calendar_inclusive(dirs, start, end)
    assert got == [
        "2026-01-13T16.59.09Z",
        "2026-04-09T17.59.09Z",
        "2026-05-01T03.59.09Z",
    ]


def test_filter_date_directories_calendar_inclusive_no_bounds_returns_copy():
    dirs = ["2026-01-13T16.59.09Z"]
    assert filter_date_directories_calendar_inclusive(dirs, None, None) == dirs


def test_last_checked_sentinel_before_calendar_start():
    s = last_checked_sentinel_before_calendar_start(date(2026, 1, 13))
    assert s == "2026-01-12T23.59.59Z"
