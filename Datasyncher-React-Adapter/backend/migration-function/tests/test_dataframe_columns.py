import pandas as pd

from services.sap.as_source.to_snowflake import _align_df_to_snowflake_columns
from services.transform.dataframe_columns import align_dataframe_to_names


def test_align_dataframe_renames_case_only():
    df = pd.DataFrame({"bukrs": [1], "bukrs_2": [1]})
    aligned = align_dataframe_to_names(df, ["BUKRS", "BUKRS_2"])
    assert list(aligned.columns) == ["BUKRS", "BUKRS_2"]


def test_align_to_snowflake_fills_after_case_rename():
    df = pd.DataFrame({"BUKRS": ["x"], "BUKRS_2": ["x"]})
    order = ["BUKRS", "BUKRS_2", "_FIVETRAN_SYNCED"]
    aligned = _align_df_to_snowflake_columns(df, order)
    assert aligned["BUKRS"].iloc[0] == "x"
    assert aligned["BUKRS_2"].iloc[0] == "x"
    assert aligned["_FIVETRAN_SYNCED"].iloc[0] is None
