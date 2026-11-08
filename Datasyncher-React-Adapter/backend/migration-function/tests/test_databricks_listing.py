from services.databricks.listing import (
    build_databricks_list_columns_sql,
    build_databricks_list_primary_keys_sql,
    build_databricks_list_tables_sql,
    is_internal_listing_table,
)


def test_list_tables_sql_uses_catalog_information_schema():
    sql = build_databricks_list_tables_sql("main", "salesforce")
    assert "FROM `main`.information_schema.tables" in sql
    assert "WHERE upper(table_schema) = 'SALESFORCE'" in sql


def test_list_columns_and_pk_sql():
    cols = build_databricks_list_columns_sql("main", "sf")
    pks = build_databricks_list_primary_keys_sql("main", "sf")
    assert "information_schema.columns" in cols
    assert "PRIMARY KEY" in pks
    assert "key_column_usage" in pks


def test_internal_listing_table_filters_staging_and_row_index():
    assert is_internal_listing_table("DS_ACCOUNT_STAGING_20260903_120000")
    assert is_internal_listing_table("tmp__datasyncher_row")
    assert not is_internal_listing_table("ACCOUNT")


def test_columns_attach_to_tables_with_case_insensitive_match():
    """Columns must attach even when information_schema casing differs."""
    all_objects = {"Account": "table"}
    table_lookup = {name.lower(): name for name in all_objects}
    columns_info = {}
    col_rows = [
        ("account", "Id", "STRING", 1),
        ("ACCOUNT", "Name", "STRING", 2),
    ]
    for table_name, column_name, data_type, ordinal in col_rows:
        table_name = str(table_name or "").strip()
        canonical_table = table_lookup.get(table_name.lower())
        if not canonical_table:
            continue
        columns_info.setdefault(canonical_table, {})
        columns_info[canonical_table][str(column_name)] = {
            "data_type": str(data_type or "STRING"),
            "is_primary_key": False,
            "ordinal_position": int(ordinal or 0) or None,
        }

    assert set(columns_info["Account"].keys()) == {"Id", "Name"}
