"""Unit tests for SAP OData → Databricks type mapping helpers."""

from services.sap.as_source.destinations.databricks import (
    build_attributes_details_from_sap_metadata,
    map_abap_type_to_databricks,
    map_edm_type_to_databricks,
    normalize_sap_column_for_databricks,
    sap_staging_blob_name,
)
from services.sap.as_source.to_databricks import _dbx_ident, _primary_key_for_merge


def test_map_edm_decimal_uses_precision_scale():
    assert map_edm_type_to_databricks(
        "Edm.Decimal", precision=13, scale=3
    ) == "DECIMAL(13,3)"


def test_map_edm_string_and_boolean():
    assert map_edm_type_to_databricks("Edm.String") == "STRING"
    assert map_edm_type_to_databricks("Edm.Boolean") == "BOOLEAN"
    assert map_edm_type_to_databricks("Edm.Int64") == "BIGINT"
    assert map_edm_type_to_databricks("Edm.DateTimeOffset") == "TIMESTAMP"


def test_map_edm_falls_back_to_internal_datatype():
    assert map_edm_type_to_databricks("", data_type="BOOLEAN") == "BOOLEAN"
    assert map_edm_type_to_databricks(None, data_type="INSTANT") == "TIMESTAMP"


def test_map_abap_decimal_and_int():
    assert map_abap_type_to_databricks("CURR", length=13, decimals=2) == "DECIMAL(13,2)"
    assert map_abap_type_to_databricks("INT4") == "INT"
    assert map_abap_type_to_databricks("DATS") == "STRING"


def test_normalize_sap_column_splits_letter_digit_for_copy_into():
    # Matches Databricks load_helpers / add_underscore used by staging DDL.
    assert normalize_sap_column_for_databricks("HWAE2") == "HWAE_2"
    assert normalize_sap_column_for_databricks("DMSH3") == "DMSH_3"
    assert normalize_sap_column_for_databricks("GN_R3_SSY") == "GN_R_3_SSY"
    assert normalize_sap_column_for_databricks("HWAE_2") == "HWAE_2"


def test_build_attributes_includes_audit_columns():
    meta = {
        "columns": [
            {"name": "OrderId", "edmType": "Edm.String", "primaryKey": True},
            {"name": "Amount", "edmType": "Edm.Decimal", "precision": 15, "scale": 2},
            {"name": "HWAE2", "edmType": "Edm.String"},
            {"name": "_SPECT_DELETED", "dataType": "BOOLEAN"},
        ]
    }
    attrs = build_attributes_details_from_sap_metadata(meta)
    assert attrs["ORDER_ID"] == "STRING"
    assert attrs["AMOUNT"] == "DECIMAL(15,2)"
    assert attrs["HWAE_2"] == "STRING"
    assert "HWAE2" not in attrs
    assert attrs["_SPECT_DELETED"] == "BOOLEAN"
    assert attrs["_FIVETRAN_SYNCED"] == "TIMESTAMP"
    assert attrs["_FIVETRAN_DELETED"] == "BOOLEAN"


def test_sap_staging_blob_name_isolated():
    name = sap_staging_blob_name("SalesOrderSet", 99)
    assert name.startswith("temp_data_sap_")
    assert name.endswith("_99.csv")
    assert "salesforce" not in name


def test_dbx_ident_preserves_valid_transform_names():
    assert _dbx_ident("IMO_CMFIGL04") == "IMO_CMFIGL04"
    # Valid alphanumeric names are uppercased in place (same as Snowflake path).
    assert _dbx_ident("SalesOrderSet") == "SALESORDERSET"


def test_primary_key_for_merge_keeps_composite_keys():
    assert _primary_key_for_merge(["OrderId", "Item"], ["ORDER_ID", "ITEM"]) == [
        "ORDER_ID",
        "ITEM",
    ]
    assert _primary_key_for_merge([], ["_FIVETRAN_SYNCED", "COL_A"]) == ["COL_A"]
