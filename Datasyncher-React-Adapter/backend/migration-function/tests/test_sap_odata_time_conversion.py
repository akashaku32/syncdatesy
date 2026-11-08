import pandas as pd

from services.sap.source import convert_odata_time_columns, parse_odata_duration_to_time


def test_parse_odata_duration_to_time():
    assert parse_odata_duration_to_time("PT16H10M56S") == "16:10:56"
    assert parse_odata_duration_to_time("PT0S") == "00:00:00"
    assert parse_odata_duration_to_time("PT1H") == "01:00:00"
    assert parse_odata_duration_to_time("16:10:56") == "16:10:56"
    assert parse_odata_duration_to_time(None) is None


def test_convert_odata_time_columns():
    df = pd.DataFrame({"CREATIONTIME": ["PT16H10M56S", None], "NAME": ["abc", "def"]})
    out = convert_odata_time_columns(df)
    assert out.loc[0, "CREATIONTIME"] == "16:10:56"
    assert out.loc[1, "CREATIONTIME"] is None
    assert out.loc[0, "NAME"] == "abc"
