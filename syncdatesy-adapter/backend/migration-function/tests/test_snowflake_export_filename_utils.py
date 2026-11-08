from datetime import datetime, timezone

import pytest

from services.source_file_helpers.file_writing_utils import (
    build_utc_filename_suffix,
    lookup_table_export_settings,
    resolve_relative_target_folder,
    strip_export_filename_suffix,
)


def test_strip_export_filename_suffix():
    assert strip_export_filename_suffix("EMPLOYEES_2026_09_21") == "EMPLOYEES"
    assert strip_export_filename_suffix("CUSTOMER_09_21_2026") == "CUSTOMER"
    assert strip_export_filename_suffix("EMPLOYEE_20260921T084052Z") == "EMPLOYEE"
    assert strip_export_filename_suffix("courses") == "courses"


def test_build_utc_filename_suffix_formats():
    current_time = datetime(2026, 9, 21, 8, 40, 52, tzinfo=timezone.utc)
    assert build_utc_filename_suffix("yyyy_mm_dd", current_time) == "2026_09_21"
    assert build_utc_filename_suffix("dd_mm_yyyy", current_time) == "21_09_2026"
    assert build_utc_filename_suffix("mm_dd_yyyy", current_time) == "09_21_2026"
    assert build_utc_filename_suffix(None, current_time) == "20260921T084052Z"


def test_resolve_relative_target_folder():
    assert resolve_relative_target_folder("SNOWFLAKE-S3", "SNOWFLAKE-S3") == ""
    assert resolve_relative_target_folder("SNOWFLAKE-S3", "CSV") == "CSV"
    assert resolve_relative_target_folder("SNOWFLAKE-S3/csv", "SNOWFLAKE-S3/csv") == ""
    assert resolve_relative_target_folder("SNOWFLAKE-S3", "SNOWFLAKE-S3/csv") == "csv"
    assert resolve_relative_target_folder("SNOWFLAKE-S3/csv", "CSV") == "CSV"


def test_lookup_table_export_settings_is_case_insensitive():
    settings = {
        "EMPLOYEES": {"output_file_name": "EMPLOYEES", "filename_date_format": "yyyy_mm_dd"},
    }
    resolved = lookup_table_export_settings(settings, "employees")
    assert resolved["output_file_name"] == "EMPLOYEES"
    assert resolved["filename_date_format"] == "yyyy_mm_dd"


def test_build_utc_filename_suffix_rejects_invalid_format():
    with pytest.raises(Exception):
        build_utc_filename_suffix("invalid-format")
