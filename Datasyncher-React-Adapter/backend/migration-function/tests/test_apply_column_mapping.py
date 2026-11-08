"""Column mapping copies a source into every target that uses it."""

import pandas as pd

from services.transform.dry_run import run_dry_run
from services.transform.executor import apply_column_mapping, execute_transform_chunk


def test_script_output_does_not_skip_mapped_bukrs_identity():
    mapping = {
        "field_mapping": [
            {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS"},
            {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS_2"},
        ],
        "source": {"fields": [{"fieldname": "BUKRS"}]},
        "target": {
            "fields": [
                {"fieldname": "BUKRS"},
                {"fieldname": "BUKRS_2"},
            ]
        },
    }
    frame = pd.DataFrame({"BUKRS": ["IN01", "1710"], "EXTRA": [1, 2]})
    script = """
import pandas as pd
def transform(__source_df: pd.DataFrame) -> pd.DataFrame:
    __target_df = __source_df.copy()
    __target_df["BUKRS"] = None
    return __target_df
"""
    out = execute_transform_chunk(
        frame,
        mapping,
        None,
        script,
        "transform",
        "filter_script_mapping",
    )
    assert out["BUKRS"].tolist() == ["IN01", "1710"]
    assert out["BUKRS_2"].tolist() == ["IN01", "1710"]


def test_bukrs_one_to_many_identity_and_suffix():
    frame = pd.DataFrame({"BUKRS": ["1000", "2000"]})
    rows = [
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS"},
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS_2"},
    ]
    out = apply_column_mapping(frame, rows)
    assert out["BUKRS"].tolist() == ["1000", "2000"]
    assert out["BUKRS_2"].tolist() == ["1000", "2000"]


def test_source_column_case_insensitive():
    frame = pd.DataFrame({"bukrs": ["1000"]})
    rows = [
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "COMPANY"},
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "COMPANY_2"},
    ]
    out = apply_column_mapping(frame, rows)
    assert out["COMPANY"].tolist() == ["1000"]
    assert out["COMPANY_2"].tolist() == ["1000"]


def test_one_source_fills_every_target():
    frame = pd.DataFrame({"FISCPER": ["2024001", "2024002"], "BUKRS": ["1000", "2000"]})
    rows = [
        {"src_fieldnames": ["FISCPER"], "tgt_fieldname": "FISCPER"},
        {"src_fieldnames": ["FISCPER"], "tgt_fieldname": "FISCPER_2"},
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "COMPANY"},
    ]
    out = apply_column_mapping(frame, rows)
    assert list(out.columns) == ["FISCPER", "FISCPER_2", "COMPANY"]
    assert out["FISCPER"].tolist() == ["2024001", "2024002"]
    assert out["FISCPER_2"].tolist() == ["2024001", "2024002"]
    assert out["COMPANY"].tolist() == ["1000", "2000"]


def test_dry_run_preview_keeps_mapped_columns_only():
    mapping = {
        "field_mapping": [
            {"src_fieldnames": ["FISCPER"], "tgt_fieldname": "FISCPER"},
            {"src_fieldnames": ["FISCPER"], "tgt_fieldname": "FISCPER_2"},
        ],
        "source": {
            "fields": [
                {"fieldname": "FISCPER"},
                {"fieldname": "UNSELECTED"},
            ]
        },
        "target": {
            "fields": [
                {"fieldname": "FISCPER"},
                {"fieldname": "FISCPER_2"},
                {"fieldname": "UNUSED_TARGET"},
            ]
        },
    }
    sample = pd.DataFrame(
        {
            "FISCPER": ["2024001"],
            "UNSELECTED": ["hide-me"],
            "__metadata": ["ignore"],
        }
    )
    result = run_dry_run(
        mapping_json=mapping,
        mapping_overrides=None,
        script_text="",
        entry_point=None,
        run_order="filter_script_mapping",
        sample_df=sample,
        sample_size=10,
    )
    assert result["errors"] == []
    assert list(result["input_preview"][0]) == ["FISCPER"]
    assert list(result["output_preview"][0]) == ["FISCPER", "FISCPER_2"]
    assert result["output_preview"][0]["FISCPER"] == "2024001"
    assert result["output_preview"][0]["FISCPER_2"] == "2024001"
    assert result["expected_columns"] == ["FISCPER", "FISCPER_2"]
    assert "UNSELECTED" not in result["input_preview"][0]
    assert "UNUSED_TARGET" not in result["output_preview"][0]


def test_dry_run_without_mapping_json_uses_sap_identity_columns():
    sap_meta = [{"fieldname": "BUKRS"}, {"fieldname": "DMBTR"}]
    sample = pd.DataFrame({"BUKRS": ["1000"], "DMBTR": [1.0]})
    script = """
import pandas as pd
def transform(__source_df: pd.DataFrame) -> pd.DataFrame:
    return __source_df.copy()
"""
    result = run_dry_run(
        mapping_json=None,
        mapping_overrides=None,
        script_text=script,
        entry_point="transform",
        run_order="filter_script_mapping",
        sample_df=sample,
        sap_metadata_fields=sap_meta,
    )
    assert result["errors"] == []
    assert result["expected_columns"] == ["BUKRS", "DMBTR"]
