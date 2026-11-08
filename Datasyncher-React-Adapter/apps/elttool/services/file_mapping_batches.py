"""Keep batch membership aligned with the current file-source mapping."""

import re


def prune_unmapped_batch_tables(connection, mode, single_mapping, multi_table_name):
    from apps.elttool.models import BatchTable

    def normalized(name):
        return re.sub(r"[^A-Z0-9_]", "_", str(name or "").strip().upper())

    if mode == "single_table_per_file":
        active_names = {normalized(name) for name in single_mapping.values() if name}
    elif mode == "multi_files_single_table":
        active_names = {normalized(multi_table_name or "MULTI_FILES_TABLE")}
    else:
        # Unknown modes must not clear existing membership.
        return 0

    rows = BatchTable.objects.filter(connection=connection)
    stale_ids = [
        row_id for row_id, name in rows.values_list("pk", "table_name")
        if normalized(name) not in active_names
    ]
    if not stale_ids:
        return 0
    deleted, _ = rows.filter(pk__in=stale_ids).delete()
    return deleted
