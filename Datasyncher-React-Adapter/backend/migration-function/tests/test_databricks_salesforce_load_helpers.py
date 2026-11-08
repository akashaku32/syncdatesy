"""Unit tests for Salesforce → Databricks load helpers."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from services.salesforce.as_source.destinations import databricks as helpers


def test_salesforce_staging_blob_name_csv_and_parquet():
    assert (
        helpers.salesforce_staging_blob_name("ACCOUNT", 9)
        == "temp_data_salesforce_ACCOUNT_9.csv"
    )
    assert (
        helpers.salesforce_staging_blob_name("ACCOUNT", 9, parquet=True)
        == "temp_data_salesforce_ACCOUNT_9.parquet"
    )


def test_map_salesforce_type_to_databricks_known_and_fallback():
    assert helpers.map_salesforce_type_to_databricks("id") == "STRING"
    assert helpers.map_salesforce_type_to_databricks("boolean") == "BOOLEAN"
    assert helpers.map_salesforce_type_to_databricks("currency") == "DECIMAL(38,6)"
    assert helpers.map_salesforce_type_to_databricks("datetime") == "TIMESTAMP"
    assert helpers.map_salesforce_type_to_databricks("double") == "DOUBLE"
    assert helpers.map_salesforce_type_to_databricks("not_a_real_type") == "STRING"


def test_build_attributes_details_field_includes_meta_and_id():
    describe = [
        {"name": "Id", "type": "id"},
        {"name": "Name", "type": "string"},
        {"name": "Amount", "type": "currency"},
        {"name": "IsActive", "type": "boolean"},
    ]
    attrs = helpers.build_attributes_details_field(
        describe,
        ["Id", "Name", "Amount", "IsActive"],
        is_updated="_FIVETRAN_SYNCED",
        is_deleted="_FIVETRAN_DELETED",
    )
    assert attrs["ID"] == "STRING"
    assert attrs["NAME"] == "STRING"
    assert attrs["AMOUNT"] == "DECIMAL(38,6)"
    assert attrs["IS_ACTIVE"] == "BOOLEAN"
    assert attrs["_FIVETRAN_SYNCED"] == "TIMESTAMP"
    assert attrs["_FIVETRAN_DELETED"] == "BOOLEAN"


def test_transform_salesforce_chunk_to_dataframe_basic():
    chunk = [
        {"Id": "001A", "Name": "Acme", "IsDeleted": False},
        {"Id": "001B", "Name": "Beta", "IsDeleted": False},
    ]
    column_types = {
        "ID": "STRING",
        "NAME": "STRING",
        "_FIVETRAN_SYNCED": "TIMESTAMP",
        "_FIVETRAN_DELETED": "BOOLEAN",
    }
    df = helpers.transform_salesforce_chunk_to_dataframe(
        chunk,
        ["Id", "Name"],
        column_types,
    )
    assert list(df["ID"]) == ["001A", "001B"]
    assert "_FIVETRAN_SYNCED" in df.columns
    assert "_FIVETRAN_DELETED" in df.columns
    assert bool(df["_FIVETRAN_DELETED"].iloc[0]) is False


def test_transform_excludes_deleted_rows_on_initial_load():
    chunk = [
        {"Id": "001A", "Name": "Keep", "IsDeleted": False},
        {"Id": "001B", "Name": "Drop", "IsDeleted": True},
    ]
    df = helpers.transform_salesforce_chunk_to_dataframe(
        chunk,
        ["Id", "Name", "IsDeleted"],
        {"ID": "STRING", "NAME": "STRING", "IS_DELETED": "BOOLEAN"},
        exclude_deleted_rows=True,
    )
    assert list(df["ID"]) == ["001A"]


def test_transform_empty_chunk_returns_empty_frame():
    df = helpers.transform_salesforce_chunk_to_dataframe([], ["Id"], {})
    assert df.empty


def test_transform_salesforce_date_iso_and_unix_day():
    chunk = [{"Id": "001", "Date_C": "2024-06-15"}, {"Id": "002", "Date_C": 0}]
    column_types = {"ID": "STRING", "DATE_C": "DATE"}
    df = helpers.transform_salesforce_chunk_to_dataframe(chunk, ["Id", "Date_C"], column_types)
    assert df.loc[df["ID"] == "001", "DATE_C"].iloc[0] == "2024-06-15"
    assert df.loc[df["ID"] == "002", "DATE_C"].iloc[0] == "1970-01-01"


def test_transform_salesforce_datetime_iso_and_epoch_millis():
    chunk = [
        {"Id": "001", "CreatedDate": "2024-06-15T10:30:00.000Z"},
        {"Id": "002", "CreatedDate": 1_718_445_000_000},
    ]
    column_types = {"ID": "STRING", "CREATED_DATE": "TIMESTAMP"}
    df = helpers.transform_salesforce_chunk_to_dataframe(
        chunk, ["Id", "CreatedDate"], column_types
    )
    assert df.loc[df["ID"] == "001", "CREATED_DATE"].iloc[0].startswith("2024-06-15")
    assert df.loc[df["ID"] == "002", "CREATED_DATE"].iloc[0].startswith("2024-")


def test_transform_salesforce_complex_and_address_as_json_string():
    chunk = [{"Id": "001", "BillingAddress": {"city": "SF", "state": "CA"}}]
    column_types = {"ID": "STRING", "BILLING_ADDRESS": "STRING"}
    df = helpers.transform_salesforce_chunk_to_dataframe(
        chunk, ["Id", "BillingAddress"], column_types
    )
    assert '"city": "SF"' in df["BILLING_ADDRESS"].iloc[0]


def test_map_salesforce_field_to_databricks_uses_precision_scale():
    meta = {"type": "currency", "precision": 16, "scale": 4}
    assert helpers.map_salesforce_field_to_databricks(meta) == "DECIMAL(16,4)"


def test_prepare_dataframe_for_databricks_parquet_keeps_dates_as_strings():
    df = pd.DataFrame({"DATE_C": [0, "2024-01-02"]})
    out = helpers.prepare_dataframe_for_databricks_parquet(df, {"DATE_C": "DATE"})
    assert out["DATE_C"].dtype == object
    assert out["DATE_C"].iloc[0] == "1970-01-01"
    assert out["DATE_C"].iloc[1] == "2024-01-02"


def test_ensure_databricks_table_from_salesforce_describe_delegates(monkeypatch):
    called = {}

    def fake_ensure(
        engine,
        table,
        schema,
        empty_df,
        attributes,
        connection_id,
        cmp_id,
        destination_connection=None,
    ):
        called["table"] = table
        called["schema"] = schema
        called["attrs"] = attributes
        called["catalog"] = (destination_connection or {}).get("catalog")
        return None

    monkeypatch.setattr(
        "services.databricks.load_helpers.ensure_databricks_base_table_exists",
        fake_ensure,
    )
    describe = [{"name": "Id", "type": "id"}, {"name": "Name", "type": "string"}]
    attrs = helpers.ensure_databricks_table_from_salesforce_describe(
        MagicMock(),
        "Account",
        "bronze",
        describe,
        ["Id", "Name"],
        connection_id=1,
        cmp_id=2,
        catalog="main",
    )
    assert called["table"] == "ACCOUNT"
    assert called["schema"] == "bronze"
    assert called["catalog"] == "main"
    assert attrs["ID"] == "STRING"
    assert attrs["NAME"] == "STRING"
