"""Acceptance tests using sap_demo_files fixtures."""

import json
from pathlib import Path

import pandas as pd
import pytest

from services.transform.dry_run import run_dry_run
from services.transform.mapping_resolver import resolve_effective_mapping
from services.transform.mapping_status import build_mapping_table
from services.transform.mapping_validator import validate_mapping_json
from services.transform.script_runner import (
    effective_script_text,
    load_transform_module,
    resolve_entry_point,
)
from services.transform.script_validator import detect_entry_points, validate_script_ast

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "sap_demo_files"
MAPPING_JSON = FIXTURE_DIR / "DTP_00O2TIIO2L73TN9NY6AZRY2PX_source2target_fieldmappings.json"
SCRIPT_PY = FIXTURE_DIR / "DTP_00O2TIIO2L73TN9NY6AZRY2PX_PYTHON_transformation(panda).py"


@pytest.fixture
def mapping_data():
    assert MAPPING_JSON.exists(), f"Missing fixture {MAPPING_JSON}"
    with open(MAPPING_JSON, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def script_text():
    assert SCRIPT_PY.exists(), f"Missing fixture {SCRIPT_PY}"
    return SCRIPT_PY.read_text(encoding="utf-8")


def test_mapping_json_structure_valid(mapping_data):
    _, errors = validate_mapping_json(json.dumps(mapping_data))
    assert errors == []
    assert len(mapping_data["field_mapping"]) == 64
    assert len(mapping_data["source"]["fields"]) == 92
    assert len(mapping_data["target"]["fields"]) == 66


def test_resolved_mapping_counts(mapping_data):
    resolved = resolve_effective_mapping(mapping_data, None)
    assert len(resolved["rows"]) == 64
    assert len(resolved["not_selected"]) == 28
    assert "XNEGP" in resolved["not_selected"]
    assert set(resolved["script_columns"]) == {"UPD_DATE", "GN_R3_SSY"}


def test_multi_source_row_joins_sap_columns():
    rows = [
        {
            "src_fieldnames": ["BUKRS", "GJAHR"],
            "tgt_fieldname": "COMP_YEAR",
            "constant_value": None,
        }
    ]
    source_fields = [
        {"fieldname": "BUKRS", "datatype": "CHAR", "length": 4, "decimals": 0},
        {"fieldname": "GJAHR", "datatype": "NUMC", "length": 4, "decimals": 0},
    ]
    target_fields = [
        {"fieldname": "COMP_YEAR", "datatype": "CHAR", "length": 8, "decimals": 0},
    ]
    table = build_mapping_table(rows, source_fields, target_fields, [])
    assert table[0]["sap_column"] == "BUKRS, GJAHR"
    assert table[0]["target_column"] == "COMP_YEAR"
    assert table[0]["renamed"] is True


def test_mapping_status_narrowed_and_from_script(mapping_data):
    resolved = resolve_effective_mapping(mapping_data, None)
    table = build_mapping_table(
        resolved["rows"],
        mapping_data["source"]["fields"],
        mapping_data["target"]["fields"],
        resolved["script_columns"],
        not_selected=resolved["not_selected"],
    )
    statuses = {r["status"] for r in table}
    assert "Narrowed" in statuses
    assert "From script" in statuses
    assert "Dropped" in statuses
    narrowed = [r for r in table if r["status"] == "Narrowed"]
    assert len(narrowed) == 12
    dropped = [r for r in table if r["status"] == "Dropped"]
    assert any(r["sap_columns"] == ["XNEGP"] for r in dropped)
    script_rows = [r for r in table if r["status"] == "From script"]
    assert {r["target_column"] for r in script_rows} >= {"UPD_DATE", "GN_R3_SSY"}


def test_target_schema_uses_mapped_names():
    from services.sap.as_source.to_snowflake import build_target_schema_metadata

    resolved = {
        "rows": [
            {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "COMPANY"},
            {"src_fieldnames": ["DMBTR"], "tgt_fieldname": "AMOUNT"},
        ],
        "target_fields": [
            {"fieldname": "COMPANY", "datatype": "CHAR", "length": 4},
            {"fieldname": "AMOUNT", "datatype": "CURR", "length": 23, "decimals": 2},
        ],
        "source_fields": [],
        "script_columns": ["UPD_DATE"],
    }
    meta = build_target_schema_metadata(resolved, {"columns": []})
    by_name = {col["name"]: col for col in meta["columns"]}
    assert list(by_name) == ["COMPANY", "AMOUNT", "UPD_DATE"]
    assert by_name["COMPANY"]["sfType"] == "VARCHAR(4)"
    assert by_name["AMOUNT"]["sfType"] == "NUMBER(23,2)"
    assert by_name["UPD_DATE"]["sfType"] == "VARCHAR(255)"


def test_shell_only_script_is_skipped():
    shell = (
        "import pandas as pd\n\n"
        "def transform(__source_df: pd.DataFrame) -> pd.DataFrame:\n"
        "    __target_df = __source_df.copy()\n"
        "    df = __target_df\n"
        "    return __target_df\n"
    )
    assert effective_script_text("") is None
    assert effective_script_text(shell) is None
    assert effective_script_text(shell + "    __target_df['A'] = 1\n") is not None


def test_script_ast_and_entry_point(script_text):
    checklist, entry_points = validate_script_ast(script_text)
    assert all(c["passed"] for c in checklist if c["id"] != "entry_point")
    assert "transform" in detect_entry_points(script_text)


def test_empty_entry_point_prefers_transform():
    module = load_transform_module(
        "import pandas as pd\n\n"
        "def other(df):\n"
        "    return df\n\n"
        "def transform(df):\n"
        "    return df\n"
    )
    assert resolve_entry_point(module, None).__name__ == "transform"
    assert resolve_entry_point(module, "").__name__ == "transform"


def test_script_rejects_os_import():
    bad = "import os\n\ndef f(df):\n    return df\n"
    checklist, _ = validate_script_ast(bad)
    imports = next(c for c in checklist if c["id"] == "imports")
    assert not imports["passed"]


def test_dry_run_with_synthetic_sample(mapping_data, script_text):
    mapped_src = []
    for row in mapping_data["field_mapping"]:
        mapped_src.extend(row["src_fieldnames"])
    sample = pd.DataFrame(
        [{c: "IN01" if c == "BUKRS" else "1" for c in mapped_src}]
    )
    result = run_dry_run(
        mapping_json=mapping_data,
        mapping_overrides=None,
        script_text=script_text,
        entry_point="transform",
        run_order="filter_script_mapping",
        sample_df=sample,
        primary_keys=["BUKRS"],
        sample_size=10,
    )
    assert result["errors"] == []
    assert result["counts"]["mapped"] == 64
    assert result["counts"]["from_script"] == 2
