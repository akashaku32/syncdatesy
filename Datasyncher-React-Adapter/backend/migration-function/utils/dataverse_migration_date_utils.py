"""
Pure helpers for Dynamics / Dataverse **replay** and inspection scenarios.

Used by tests (and optional scripts) to narrow `date_directory` folder lists by calendar day
without wiring replay logic into ``microsoft_dynamics_migration_service.main()``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional


def parse_dataverse_calendar_day(value: Optional[str]) -> Optional[date]:
    """
    Parse a calendar day used to filter Dataverse export folder names.

    Accepts ISO ``YYYY-MM-DD``, ``YYYY/MM/DD``, or ``M/D/YYYY`` / ``MM/DD/YYYY``.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"Unrecognized calendar day {value!r}; use YYYY-MM-DD or MM/DD/YYYY"
    )


def dataverse_folder_calendar_day(folder_name: str) -> date:
    """UTC calendar day embedded in a Dataverse export directory name."""
    dt = datetime.strptime(folder_name, "%Y-%m-%dT%H.%M.%SZ")
    return dt.date()


def filter_date_directories_calendar_inclusive(
    date_directories: List[str],
    start_day: Optional[date],
    end_day: Optional[date],
) -> List[str]:
    """Keep folders whose UTC calendar day lies within [start_day, end_day] when each bound is set."""
    if not start_day and not end_day:
        return list(date_directories)
    out: List[str] = []
    for folder in date_directories:
        try:
            d = dataverse_folder_calendar_day(folder)
        except ValueError:
            continue
        if start_day and d < start_day:
            continue
        if end_day and d > end_day:
            continue
        out.append(folder)
    return out


def last_checked_sentinel_before_calendar_start(start_day: date) -> str:
    """
    Synthetic ``last_synced`` / last_checked directory string strictly before ``start_day``,
    so ``is_directory_greater_than`` (dir >= last_checked) includes every export folder on
    ``start_day`` onward when replaying from Postgres cursors.
    """
    prev = start_day - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}-{prev.day:02d}T23.59.59Z"
