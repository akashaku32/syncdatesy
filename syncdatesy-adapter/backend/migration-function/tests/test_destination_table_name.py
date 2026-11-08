from services.transform.destination_table_name import (
    extract_json_target_objectname,
    is_valid_snowflake_destination_name,
    normalize_destination_table_name,
    normalize_objectname_for_compare,
)


def test_imo_sample():
    name, warnings = normalize_destination_table_name("/IMO/CMFIGL04")
    assert name == "IMO_CMFIGL04"
    assert warnings == []


def test_leading_digit():
    name, _ = normalize_destination_table_name("9LEDGER")
    assert name == "T_9LEDGER"


def test_spaces():
    name, _ = normalize_destination_table_name(" IMO  CM ")
    assert name == "IMO_CM"


def test_empty_and_unicode():
    empty, warnings = normalize_destination_table_name("***")
    assert empty == ""
    assert warnings
    name, _ = normalize_destination_table_name("café")
    assert name == "CAF"


def test_truncate_and_reserved():
    name, _ = normalize_destination_table_name("A" * 300)
    assert len(name) == 255
    reserved, warnings = normalize_destination_table_name("TABLE")
    assert reserved == "TABLE_T"
    assert warnings


def test_objectname_o_vs_0():
    assert normalize_objectname_for_compare("0FI_GL_4") == normalize_objectname_for_compare(
        "EntityOf0FI_GL_4"
    )


def test_extract_and_validity():
    assert extract_json_target_objectname({"target": {"objectname": "/IMO/CMFIGL04"}}) == "/IMO/CMFIGL04"
    assert is_valid_snowflake_destination_name("IMO_CMFIGL04")
    assert not is_valid_snowflake_destination_name("SELECT")
