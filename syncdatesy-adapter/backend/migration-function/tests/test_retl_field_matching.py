"""Tests for reverse ETL source/destination field name matching."""

from services.salesforce.as_destination.retl_common import (
    match_source_destination_fields,
)


def test_match_normalized_databricks_columns_to_salesforce_api_names():
    source_fields = {
        "ID": {},
        "NAME": {},
        "CREATED_DATE": {},
        "IS_DELETED": {},
        "PERCENT_OF_COMPLETION_C": {},
    }
    destination_fields = {
        "Id": {},
        "Name": {},
        "CreatedDate": {},
        "IsDeleted": {},
        "Percent_of_Completion__c": {},
    }

    matching_fields, field_map = match_source_destination_fields(
        source_fields, destination_fields
    )

    assert matching_fields == [
        "ID",
        "NAME",
        "CREATED_DATE",
        "IS_DELETED",
        "PERCENT_OF_COMPLETION_C",
    ]
    assert field_map == {
        "ID": "Id",
        "NAME": "Name",
        "CREATED_DATE": "CreatedDate",
        "IS_DELETED": "IsDeleted",
        "PERCENT_OF_COMPLETION_C": "Percent_of_Completion__c",
    }


def test_match_exact_salesforce_names_when_source_already_normalized():
    source_fields = {"Id": {}, "Name": {}}
    destination_fields = {"Id": {}, "Name": {}}

    matching_fields, field_map = match_source_destination_fields(
        source_fields, destination_fields
    )

    assert matching_fields == ["Id", "Name"]
    assert field_map == {"Id": "Id", "Name": "Name"}
