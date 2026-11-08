"""Helpers for detecting in-progress table operations (refresh/reload) and filtering migrations."""
from __future__ import annotations

import logging
from typing import Any, Iterable

from apps.elttool.models import MigrationRecord, SourceTableSelection

logger = logging.getLogger(__name__)

PIPELINE_TABLE_SKIP_REASON = (
    "Skipped — table refresh/reload is in progress"
)


def extract_source_table_name(table_entry: Any) -> str:
    """Normalize table representation to source table name."""
    if isinstance(table_entry, (list, tuple)) and table_entry:
        return str(table_entry[0])
    return str(table_entry)


def get_in_progress_table_names(
    connection,
    *,
    include_source_table_selection: bool = True,
) -> set[str]:
    """Return source table names currently under refresh/reload or other in-progress work."""
    in_progress = set(
        MigrationRecord.objects.filter(
            connection=connection,
            status='in_progress',
            table_name__isnull=False,
        ).values_list('table_name', flat=True)
    )

    if include_source_table_selection:
        src_model = getattr(getattr(connection, 'src_config', None), 'src', None)
        is_reverse_etl = bool(getattr(src_model, 'is_rtl', False))
        if not is_reverse_etl:
            in_progress.update(
                SourceTableSelection.objects.filter(
                    connection=connection,
                    status='in_progress',
                ).values_list('table', flat=True)
            )

    return in_progress


def filter_tables_for_migration(
    connection,
    table_entries: Iterable[Any],
    *,
    is_reverse_etl: bool = False,
) -> tuple[list[Any], list[str]]:
    """
    Split table entries into runnable vs skipped (busy with refresh/reload).

    Returns (runnable_entries, skipped_source_table_names).
    """
    entries = list(table_entries)
    if not entries:
        return [], []

    in_progress = get_in_progress_table_names(
        connection,
        include_source_table_selection=not is_reverse_etl,
    )
    if not in_progress:
        return entries, []

    runnable: list[Any] = []
    skipped: list[str] = []
    for entry in entries:
        source_name = extract_source_table_name(entry)
        if source_name in in_progress:
            skipped.append(source_name)
        else:
            runnable.append(entry)

    skipped = sorted(set(skipped))
    if skipped:
        logger.info(
            "Skipping in-progress tables for connection_id=%s: %s",
            getattr(connection, 'connection_id', None),
            skipped,
        )
    return runnable, skipped
