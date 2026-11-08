"""Re-run 0116 database ops for environments that faked 0116 after DuplicateColumn.

0116 already updated Django state. This only touches the database, skipping
columns/indexes that already exist.
"""

from django.db import migrations


_INDEX_RENAMES = (
    ("entity_trans_conn_tbl_active_idx", "entity_tran_connect_2c1de9_idx"),
    ("entity_trans_conn_svc_ent_idx", "entity_tran_connect_5d84ed_idx"),
)

_SOURCE_TABLE_COLUMNS = (
    "compression_method",
    "delete_and_load",
    "delta_name",
    "delta_token",
    "first_sync_timestamp",
    "initial_completed_flag",
    "last_delta_run_timestamp",
    "load_method",
    "load_method_locked",
    "locked_row_filter_config",
    "partition_delta_by_date",
    "row_filter_config",
    "service_name",
)


def _table_columns(schema_editor, table):
    with schema_editor.connection.cursor() as cursor:
        try:
            return {
                col.name
                for col in schema_editor.connection.introspection.get_table_description(
                    cursor, table
                )
            }
        except Exception:
            return set()


def _index_names(schema_editor, table):
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(
            cursor, table
        )
    return {
        name
        for name, info in constraints.items()
        if info.get("index") or info.get("unique")
    }


def rename_entity_transform_indexes(apps, schema_editor):
    existing = _index_names(schema_editor, "entity_transform")
    quote = schema_editor.quote_name
    for old, new in _INDEX_RENAMES:
        if old in existing and new not in existing:
            schema_editor.execute(
                "ALTER INDEX %s RENAME TO %s" % (quote(old), quote(new))
            )


def add_missing_source_table_columns(apps, schema_editor):
    SourceTableSelection = apps.get_model("elttool", "SourceTableSelection")
    existing = _table_columns(schema_editor, SourceTableSelection._meta.db_table)
    for name in _SOURCE_TABLE_COLUMNS:
        if name in existing:
            continue
        field = SourceTableSelection._meta.get_field(name)
        schema_editor.add_field(SourceTableSelection, field)


class Migration(migrations.Migration):

    dependencies = [
        (
            "elttool",
            "0116_rename_entity_trans_conn_tbl_active_idx_entity_tran_connect_2c1de9_idx_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(
            rename_entity_transform_indexes,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            add_missing_source_table_columns,
            migrations.RunPython.noop,
        ),
    ]
