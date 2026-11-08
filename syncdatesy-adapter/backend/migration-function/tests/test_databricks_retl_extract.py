"""SQL builders for Databricks → Salesforce reverse ETL extract."""

from services.salesforce.as_destination.sources.databricks import (
    build_databricks_chunk_sql,
    build_databricks_drop_sql,
    build_databricks_temp_extract_sql,
)


def test_temp_extract_sql_uses_row_number_and_string_casts():
    sql = build_databricks_temp_extract_sql(
        "main",
        "salesforce",
        "ACCOUNT",
        "DS_TMP_ACCOUNT",
        ["Id", "Name"],
    )
    assert "CREATE OR REPLACE TABLE `main`.`salesforce`.`DS_TMP_ACCOUNT`" in sql
    assert "ROW_NUMBER() OVER (ORDER BY (SELECT 1)) AS __datasyncher_row" in sql
    assert "CAST(`Id` AS STRING) AS `Id`" in sql
    assert "CAST(`Name` AS STRING) AS `Name`" in sql
    assert "FROM `main`.`salesforce`.`ACCOUNT`" in sql


def test_chunk_sql_filters_row_index():
    sql = build_databricks_chunk_sql(
        "main",
        "salesforce",
        "DS_TMP_ACCOUNT",
        ["Id", "Name"],
        1,
        500,
    )
    assert "FROM `main`.`salesforce`.`DS_TMP_ACCOUNT`" in sql
    assert "WHERE __datasyncher_row BETWEEN 1 AND 500" in sql
    assert "ORDER BY __datasyncher_row" in sql


def test_drop_sql_is_idempotent():
    sql = build_databricks_drop_sql("main", "salesforce", "DS_TMP_ACCOUNT")
    assert sql == "DROP TABLE IF EXISTS `main`.`salesforce`.`DS_TMP_ACCOUNT`"


def test_temp_extract_sql_without_catalog():
    sql = build_databricks_temp_extract_sql(
        None, "sf", "CONTACT", "TMP", ["Id"]
    )
    assert "CREATE OR REPLACE TABLE `sf`.`TMP`" in sql
    assert "FROM `sf`.`CONTACT`" in sql
