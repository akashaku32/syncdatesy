import pandas as pd

from services.transform.executor import apply_column_mapping
from services.transform.one_to_many import one_to_many_groups, replicate_one_to_many_sources


def test_one_to_many_groups_any_source():
    rows = [
        {"src_fieldnames": ["FISCPER"], "tgt_fieldname": "FISCPER"},
        {"src_fieldnames": ["FISCPER"], "tgt_fieldname": "FISCPER_2"},
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS"},
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS_2"},
        {"src_fieldnames": ["BELNR"], "tgt_fieldname": "BELNR"},
    ]
    groups = one_to_many_groups(rows)
    assert groups["FISCPER"] == ["FISCPER", "FISCPER_2"]
    assert groups["BUKRS"] == ["BUKRS", "BUKRS_2"]
    assert "BELNR" not in groups


def test_replicate_fills_identity_target_when_script_nulled_homonym():
    rows = [
        {"src_fieldnames": ["DMBTR"], "tgt_fieldname": "DMBTR"},
        {"src_fieldnames": ["DMBTR"], "tgt_fieldname": "DMBTR_COPY"},
    ]
    extract = pd.DataFrame({"dmbtr": [10.0, 20.0]})
    result = pd.DataFrame({"dmbtr": [10.0, 20.0], "DMBTR": [None, None]})
    out = apply_column_mapping(result, rows, source_df=extract)
    assert out["DMBTR"].tolist() == [10.0, 20.0]
    assert out["DMBTR_COPY"].tolist() == [10.0, 20.0]


def test_replicate_one_to_many_pass_standalone():
    rows = [
        {"src_fieldnames": ["X"], "tgt_fieldname": "A"},
        {"src_fieldnames": ["X"], "tgt_fieldname": "B"},
    ]
    extract = pd.DataFrame({"x": [1, 2]})
    result = pd.DataFrame({"A": [None, None]})
    out = replicate_one_to_many_sources(result, rows, extract, result)
    assert out["A"].tolist() == [1, 2]
    assert out["B"].tolist() == [1, 2]
