from datetime import datetime, timezone
from decimal import Decimal
import logging
from zipfile import ZipFile

import pytest
import pyarrow.parquet as pq
from fastapi import HTTPException
from openpyxl import load_workbook

from models.snowflake_sftp_models import SnowflakeSFTPMigrationRequest
from services import snowflake_to_sftp_service
from services.snowflake.as_source.to_sftp import (
    _build_snowflake_select_query,
    _mark_table_failed_with_error,
    _upload_file,
)
from services.source_file_helpers.file_writing_utils import _write_excel, _write_parquet


class _MockCursor:
    def __init__(self, description, row_batches):
        self.description = description
        self._row_batches = list(row_batches)

    def fetchmany(self, fetch_size):
        if not self._row_batches:
            return []
        return self._row_batches.pop(0)


def test_write_parquet_keeps_decimal_schema_stable(tmp_path):
    out_path = tmp_path / "generaljournalaccountentry.parquet"
    description = [
        ("ACCOUNTINGCURRENCYAMOUNT", None, None, None, 15, 6, True),
        ("TEXT", None, None, None, None, None, True),
    ]
    row_batches = [
        [(Decimal("99999999.123456"), "first")],
        [(Decimal("100000000.123456"), "second")],
    ]
    cursor = _MockCursor(description, row_batches)

    total_rows, col_count = _write_parquet(cursor, str(out_path), fetch_size=1)

    assert total_rows == 2
    assert col_count == 2

    schema = pq.read_table(out_path).schema
    amount_field = schema.field("ACCOUNTINGCURRENCYAMOUNT")
    assert amount_field.type.precision == 15
    assert amount_field.type.scale == 6


def test_write_excel_applies_sheet_options_and_conditional_formatting(tmp_path):
    out_path = tmp_path / "customers.xlsx"
    description = [
        ("NAME", None, None, None, None, None, True),
        ("SCORE", None, None, None, None, None, True),
        ("STATUS", None, None, None, None, None, True),
    ]
    row_batches = [
        [("Alice", 10, "Active"), ("Bob", 25, "Failed")],
    ]
    cursor = _MockCursor(description, row_batches)

    total_rows, col_count = _write_excel(
        cursor,
        str(out_path),
        sheet_name="QA Export",
        excel_options={
            "freeze_panes": "A2",
            "auto_filter": True,
            "column_formats": {"SCORE": "0"},
        },
        conditional_formats=[
            {
                "type": "cellIs",
                "column_name": "SCORE",
                "operator": "greaterThanOrEqual",
                "formula": ["20"],
                "style": {"fill_color": "FFC7CE"},
            },
            {
                "type": "formula",
                "range": "A2:C3",
                "formula": ['$C2="Failed"'],
                "style": {"fill_color": "FFF2CC"},
            },
        ],
        fetch_size=100,
    )

    assert total_rows == 2
    assert col_count == 3

    wb = load_workbook(out_path)
    ws = wb["QA Export"]
    assert ws.freeze_panes == "A2"
    assert ws["B2"].number_format == "0"
    assert len(ws.conditional_formatting) == 2


def test_write_excel_leaves_headers_plain_without_explicit_header_style(tmp_path):
    out_path = tmp_path / "plain_headers.xlsx"
    description = [
        ("PLANET_CODE", None, None, None, None, None, True),
        ("SECTOR_NAME", None, None, None, None, None, True),
    ]
    row_batches = [[("PX-101", "Orion")]]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="Plain",
        excel_options={"auto_filter": True},
        conditional_formats=[],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["Plain"]
    assert ws.auto_filter.ref == "A1:B2"
    assert ws["A1"].fill.patternType is None
    assert ws["A1"].font.bold is False


def test_write_excel_conditional_formatting_keeps_dxf_fill_and_font(tmp_path):
    out_path = tmp_path / "library.xlsx"
    description = [
        ("TITLE", None, None, None, None, None, True),
        ("PUBLISHED_YEAR", None, None, None, None, None, True),
    ]
    row_batches = [
        [("Book A", 2021), ("Book B", 2010)],
    ]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="Library",
        excel_options={},
        conditional_formats=[
            {
                "type": "cellIs",
                "column_name": "PUBLISHED_YEAR",
                "operator": "greaterThan",
                "formula": ["2020"],
                "style": {
                    "bold": True,
                    "font_color": "FFFFFF",
                    "background_color": "DB142F",
                },
            },
            {
                "type": "formula",
                "range": "A2:B3",
                "formula": ['$B2>2020'],
                "style": {
                    "fill_color": "FFF2CC",
                },
            },
        ],
        fetch_size=100,
    )

    with ZipFile(out_path) as workbook_zip:
        styles_xml = workbook_zip.read("xl/styles.xml").decode("utf-8")

    assert 'bgColor rgb="FFDB142F"' in styles_xml
    assert 'bgColor rgb="FFFFF2CC"' in styles_xml
    assert '<font>' in styles_xml
    assert '<b/>' in styles_xml
    assert 'rgb="FFFFFFFF"' in styles_xml


def test_write_excel_supports_sheet_header_and_body_styles(tmp_path):
    out_path = tmp_path / "inventory.xlsx"
    description = [
        ("ITEM_NAME", None, None, None, None, None, True),
        ("STOCK_QTY", None, None, None, None, None, True),
    ]
    row_batches = [
        [("Desk", 10), ("Chair", 20)],
    ]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="Warehouse",
        excel_options={
            "sheet_header_style": {
                "fill": "112233",
                "font_color": "FFFFFF",
                "bold": True,
                "horizontal": "center",
            },
            "header_style": {
                "fill": "445566",
                "font_color": "FFFFFF",
                "bold": True,
            },
            "body_style": {
                "fill": "F2F2F2",
                "font_color": "111111",
                "banding_fill": "D9EAF7",
                "banding_font_color": "003366",
                "banding_frequency": 2,
            },
            "freeze_header": True,
        },
        conditional_formats=[],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["Warehouse"]
    assert ws["A1"].value == "Warehouse"
    assert "A1:B1" in {str(rng) for rng in ws.merged_cells.ranges}
    assert ws.freeze_panes == "A3"
    assert ws["A2"].value == "ITEM_NAME"
    assert ws["A3"].fill.fgColor.rgb == "FFF2F2F2"
    assert ws["A3"].font.color.rgb == "FF111111"
    assert ws["A4"].fill.fgColor.rgb == "FFD9EAF7"
    assert ws["A4"].font.color.rgb == "FF003366"


def test_write_excel_uses_sheet_header_option_alias(tmp_path):
    out_path = tmp_path / "warehouse_alias.xlsx"
    description = [
        ("ITEM_NAME", None, None, None, None, None, True),
        ("STOCK_QTY", None, None, None, None, None, True),
    ]
    row_batches = [[("Desk", 10)]]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="sheet one",
        excel_options={
            "sheet_header": "Warehouse Report",
            "sheet_header_style": {
                "fill": "FF0000",
                "font_color": "FFFFFF",
                "font_size": 24,
                "bold": True,
            },
        },
        conditional_formats=[],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["sheet one"]
    assert ws["A1"].value == "Warehouse Report"
    assert "A1:B1" in {str(rng) for rng in ws.merged_cells.ranges}


def test_write_excel_supports_multi_row_sheet_header_merge(tmp_path):
    out_path = tmp_path / "warehouse_multiline.xlsx"
    description = [
        ("ITEM_NAME", None, None, None, None, None, True),
        ("STOCK_QTY", None, None, None, None, None, True),
    ]
    row_batches = [[("Desk", 10)]]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="sheet one",
        excel_options={
            "sheet_header": "Warehouse Report",
            "sheet_header_row_span": 2,
            "sheet_header_style": {
                "fill": "FF0000",
                "font_color": "FFFFFF",
                "font_size": 24,
                "bold": True,
            },
            "freeze_header": True,
        },
        conditional_formats=[],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["sheet one"]
    assert ws["A1"].value == "Warehouse Report"
    assert "A1:B2" in {str(rng) for rng in ws.merged_cells.ranges}
    assert ws["A3"].value == "ITEM_NAME"
    assert ws.freeze_panes == "A4"


def test_write_excel_skips_sheet_header_when_disabled(tmp_path):
    out_path = tmp_path / "warehouse_header_disabled.xlsx"
    description = [
        ("ITEM_NAME", None, None, None, None, None, True),
        ("STOCK_QTY", None, None, None, None, None, True),
    ]
    row_batches = [[("Desk", 10)]]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="sheet one",
        excel_options={
            "sheet_header_enabled": False,
            "sheet_header": "Warehouse Report",
            "sheet_header_row_span": 2,
            "sheet_header_style": {
                "fill": "FF0000",
                "font_color": "FFFFFF",
                "font_size": 24,
                "bold": True,
            },
        },
        conditional_formats=[],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["sheet one"]
    assert ws["A1"].value == "ITEM_NAME"
    assert {str(rng) for rng in ws.merged_cells.ranges} == set()


def test_write_excel_auto_width_favors_header_readability(tmp_path):
    out_path = tmp_path / "galaxy.xlsx"
    description = [
        ("PLANET_CODE", None, None, None, None, None, True),
        ("SECTOR_NAME", None, None, None, None, None, True),
        ("CREATED_AT", None, None, None, None, None, True),
    ]
    row_batches = [
        [("PX-101", "Orion", "2025-01-01 08:00:00")],
    ]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="Galaxy",
        excel_options={"auto_width": True},
        conditional_formats=[],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["Galaxy"]
    assert ws.column_dimensions["A"].width >= 15
    assert ws.column_dimensions["B"].width >= 15
    assert ws.column_dimensions["C"].width >= 16


def test_write_excel_normalizes_timezone_aware_datetimes_and_logs_columns(tmp_path, caplog):
    out_path = tmp_path / "timezone.xlsx"
    description = [
        ("ORDER_ID", None, None, None, None, None, True),
        ("ORDER_TIMESTAMP", None, None, None, None, None, True),
        ("UPDATED_AT", None, None, None, None, None, True),
    ]
    row_batches = [[
        (1, datetime(2026, 6, 4, 12, 30, 0, tzinfo=timezone.utc), datetime(2026, 6, 4, 18, 0, 0)),
    ]]
    cursor = _MockCursor(description, row_batches)

    caplog.set_level(logging.DEBUG, logger="services.source_file_helpers.file_writing_utils")

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="Timezone",
        excel_options={},
        conditional_formats=[],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["Timezone"]
    assert ws["B2"].value.tzinfo is None
    assert ws["B2"].value == datetime(2026, 6, 4, 12, 30, 0)
    assert ws["C2"].value == datetime(2026, 6, 4, 18, 0, 0)
    assert "ORDER_TIMESTAMP" in caplog.text
    assert "tz-aware columns" in caplog.text


def test_write_excel_rejects_unknown_conditional_format_column(tmp_path):
    out_path = tmp_path / "invalid.xlsx"
    description = [("NAME", None, None, None, None, None, True)]
    row_batches = [[("Alice",)]]
    cursor = _MockCursor(description, row_batches)

    with pytest.raises(HTTPException) as exc_info:
        _write_excel(
            cursor,
            str(out_path),
            sheet_name="Sheet1",
            excel_options={},
            conditional_formats=[{"type": "cellIs", "column_name": "MISSING", "formula": ["1"]}],
            fetch_size=100,
        )

    assert exc_info.value.status_code == 422
    assert "column not found" in exc_info.value.detail


def test_write_excel_conditional_formatting_entire_row(tmp_path):
    out_path = tmp_path / "entire_row.xlsx"
    description = [
        ("NAME", None, None, None, None, None, True),
        ("SCORE", None, None, None, None, None, True),
        ("STATUS", None, None, None, None, None, True),
    ]
    row_batches = [
        [("Alice", 10, "Active"), ("Bob", 25, "Failed")],
    ]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="QA Entire Row",
        excel_options={},
        conditional_formats=[
            {
                "type": "cellIs",
                "column_name": "SCORE",
                "operator": "greaterThanOrEqual",
                "formula": ["20"],
                "highlight_scope": "entire_row",
                "style": {"fill_color": "FFC7CE"},
            },
            {
                "type": "containsText",
                "column_name": "STATUS",
                "text": "Failed",
                "highlight_scope": "entire_row",
                "style": {"fill_color": "FFF2CC"},
            }
        ],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["QA Entire Row"]
    
    cf_rules = list(ws.conditional_formatting)
    assert len(cf_rules) == 1
    
    ranges = {str(cf.sqref) for cf in cf_rules}
    assert "A2:C3" in ranges

    found_score_formula = False
    found_status_formula = False

    for cf in cf_rules:
        for rule in cf.rules:
            assert rule.type == "expression"
            formula_str = rule.formula[0]
            if "$B2>=20" in formula_str:
                found_score_formula = True
            elif 'SEARCH("Failed",$C2)' in formula_str:
                found_status_formula = True

    assert found_score_formula is True
    assert found_status_formula is True


def test_write_excel_multiple_conditions_highlights_selected_columns(tmp_path):
    out_path = tmp_path / "multiple_conditions.xlsx"
    description = [
        ("STATUS", None, None, None, None, None, True),
        ("AMOUNT", None, None, None, None, None, True),
        ("OWNER", None, None, None, None, None, True),
    ]
    cursor = _MockCursor(
        description,
        [[("Failed", 125, "Alice"), ("Active", 10, "Bob")]],
    )

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="Multiple Conditions",
        excel_options={},
        conditional_formats=[
            {
                "type": "multipleconditions",
                "target_columns": ["AMOUNT", "OWNER"],
                "column_name": "AMOUNT",
                "highlight_scope": "selected_columns",
                "conditions": [
                    {"column": "STATUS", "operator": "equal", "value": "Failed"},
                    {"column": "AMOUNT", "operator": "greaterThan", "value": "100", "logic": "and"},
                ],
                "formula": ['=AND($A2="Failed",$B2>100)'],
                "style": {"fill_color": "FFC7CE"},
            }
        ],
        fetch_size=100,
    )

    wb = load_workbook(out_path)
    ws = wb["Multiple Conditions"]
    ranges = {str(cf.sqref) for cf in ws.conditional_formatting}
    assert ranges == {"B2:B3", "C2:C3"}
    formulas = [rule.formula[0] for cf in ws.conditional_formatting for rule in cf.rules]
    assert formulas == ['AND($A2="Failed",$B2>100)', 'AND($A2="Failed",$B2>100)']


def test_write_excel_color_scale_xlsxwriter(tmp_path):
    out_path = tmp_path / "colorscale.xlsx"
    description = [
        ("VAL", None, None, None, None, None, True),
    ]
    row_batches = [
        [(1,), (2,), (3,)],
    ]
    cursor = _MockCursor(description, row_batches)

    _write_excel(
        cursor,
        str(out_path),
        sheet_name="ColorScaleTest",
        excel_options={},
        conditional_formats=[
            {
                "type": "colorscale",
                "column_name": "VAL",
                "colors": ["70AD47", "FFC000", "C00000"],
            }
        ],
        fetch_size=100,
    )

    with ZipFile(out_path) as workbook_zip:
        sheet_xml = workbook_zip.read("xl/worksheets/sheet1.xml").decode("utf-8")

    assert "colorScale" in sheet_xml


def test_build_snowflake_select_query_uses_selected_fields_order():
    query = _build_snowflake_select_query(
        "PUBLIC.MY_TABLE",
        ["THIRD_COL", "FIRST_COL", "SECOND_COL"],
    )

    assert query == 'SELECT "THIRD_COL", "FIRST_COL", "SECOND_COL" FROM "PUBLIC"."MY_TABLE"'


def _base_request():
    return SnowflakeSFTPMigrationRequest(
        source_config={
            "account": "acc",
            "user": "user",
            "authentication_type": "password",
            "password": "pw",
            "database": "DB",
            "schema": "PUBLIC",
            "warehouse": "WH",
        },
        destination_config={
            "sftp_host": "example.com",
            "sftp_port": 22,
            "sftp_username": "user",
            "authentication_type": "password",
            "sftp_password": "secret",
            "root_folder": "/upload",
            "file_format": "csv",
        },
        connection_id=33,
        cmp_id=7,
        user_id=11,
        session=2,
        selected_tables=["T1"],
        safety_interval="0",
        job_name="JOB_1",
        migration_session_id=2,
        execution_order="sequential",
        chunk_count=1000,
        logs_creation=False,
    )


def test_mark_table_failed_with_error_updates_table_log(monkeypatch):
    request = _base_request()
    calls = {"status": [], "update_log": []}

    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "update_table_status_snowflake",
        lambda connection_id, table_name, status, job_name, migration_session_id: calls["status"].append(
            (connection_id, table_name, status, job_name, migration_session_id)
        ),
    )

    class _DummyCursor:
        def close(self):
            return None

    class _DummyConn:
        def cursor(self):
            return _DummyCursor()

        def close(self):
            return None

    from utils import database_utils
    monkeypatch.setattr(database_utils, "postgres_connection", lambda: _DummyConn())
    monkeypatch.setattr(database_utils, "get_migration_record_id", lambda *args, **kwargs: 892243)
    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "update_table_migration_status_log",
        lambda **kwargs: calls["update_log"].append(kwargs) or True,
    )

    _mark_table_failed_with_error(
        request=request,
        table_name="GENERALJOURNALACCOUNTENTRY",
        job_name="JOB_1",
        error_message="Table schema does not match schema used to create file",
    )

    assert calls["status"] == [(33, "GENERALJOURNALACCOUNTENTRY", "failed", "JOB_1", 2)]
    assert calls["update_log"] == [
        {
            "migration_record_id": 892243,
            "message": "Table schema does not match schema used to create file",
            "status": "F",
            "session": 2,
            "job_name": "JOB_1",
            "connection_id": 33,
        }
    ]


def test_migrate_snowflake_to_sftp_logs_job_failure_on_early_error(monkeypatch):
    request = _base_request()
    failure_calls = []
    marked_tables = []

    monkeypatch.setattr(snowflake_to_sftp_service, "update_migration_status_log", lambda **kwargs: True)
    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "validate_sftp_authorization",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=422, detail="Bad SFTP config")),
    )
    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "log_migration_failure",
        lambda **kwargs: failure_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "_mark_table_failed_with_error",
        lambda **kwargs: marked_tables.append((kwargs["table_name"], kwargs["error_message"])),
    )

    with pytest.raises(HTTPException) as exc_info:
        snowflake_to_sftp_service.migrate_snowflake_to_sftp(request)

    assert exc_info.value.detail == "Bad SFTP config"
    assert len(failure_calls) == 1
    assert marked_tables == [("T1", "Bad SFTP config")]
    assert failure_calls[0]["job_name"] == "JOB_1"
    assert failure_calls[0]["error_message"] == "Bad SFTP config"
    assert failure_calls[0]["connection_id"] == 33
    assert failure_calls[0]["total_tables"] == 1


def test_migrate_snowflake_to_sftp_marks_unprocessed_tables_failed_on_connectivity_error(monkeypatch):
    request = _base_request().copy(update={"selected_tables": ["T1", "T2"]})
    failure_calls = []
    marked_tables = []

    monkeypatch.setattr(snowflake_to_sftp_service, "update_migration_status_log", lambda **kwargs: True)
    monkeypatch.setattr(snowflake_to_sftp_service, "validate_sftp_authorization", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "test_sftp_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=422,
                detail=(
                    "The configured target folder is not writable: /upload. "
                    "The SFTP connection succeeded, but the configured user does not have permission "
                    "to create files in this folder. Please grant write permission on this folder "
                    "or choose a different destination folder."
                ),
            )
        ),
    )
    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "_mark_table_failed_with_error",
        lambda **kwargs: marked_tables.append((kwargs["table_name"], kwargs["error_message"])),
    )
    monkeypatch.setattr(
        snowflake_to_sftp_service,
        "log_migration_failure",
        lambda **kwargs: failure_calls.append(kwargs) or True,
    )

    with pytest.raises(HTTPException) as exc_info:
        snowflake_to_sftp_service.migrate_snowflake_to_sftp(request)

    assert exc_info.value.detail == (
        "The configured target folder is not writable: /upload. "
        "The SFTP connection succeeded, but the configured user does not have permission "
        "to create files in this folder. Please grant write permission on this folder "
        "or choose a different destination folder."
    )
    assert marked_tables == [
        (
            "T1",
            "The configured target folder is not writable: /upload. "
            "The SFTP connection succeeded, but the configured user does not have permission "
            "to create files in this folder. Please grant write permission on this folder "
            "or choose a different destination folder.",
        ),
        (
            "T2",
            "The configured target folder is not writable: /upload. "
            "The SFTP connection succeeded, but the configured user does not have permission "
            "to create files in this folder. Please grant write permission on this folder "
            "or choose a different destination folder.",
        ),
    ]
    assert len(failure_calls) == 1
    assert failure_calls[0]["error_message"] == (
        "The configured target folder is not writable: /upload. "
        "The SFTP connection succeeded, but the configured user does not have permission "
        "to create files in this folder. Please grant write permission on this folder "
        "or choose a different destination folder."
    )


def test_upload_file_maps_permission_denied_to_target_folder_message(tmp_path):
    local_path = tmp_path / "export.csv"
    local_path.write_text("id,name\n1,test\n", encoding="utf-8")

    class _DenyWriteSFTP:
        def putfo(self, file_obj, remote_path):
            raise PermissionError(13, "Permission denied")

    with pytest.raises(HTTPException) as exc_info:
        _upload_file(_DenyWriteSFTP(), str(local_path), "/upload/export.csv")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "The configured target folder is not writable: /upload. "
        "The SFTP connection succeeded, but the configured user does not have permission "
        "to create files in this folder. Please grant write permission on this folder "
        "or choose a different destination folder."
    )


def test_check_table_completed_status_skips_and_restores(monkeypatch):
    """Verifies that check_table_completed_status skips processing and restores stats."""
    request = _base_request()

    from utils import database_utils
    monkeypatch.setattr(
        database_utils,
        "check_table_completed_status",
        lambda connection_id, table_name, migration_session_id, job_name: {
            "status": "success",
            "rows": 14471,
            "columns": 198,
        }
    )

    result = snowflake_to_sftp_service._process_snowflake_to_sftp_table(
        request=request,
        table_name="T1",
        source_config={},
        dest_config={},
        root_folder_path="/upload",
        file_format="excel",
        default_delimiter=",",
        default_quotechar='"',
        escapechar=None,
        null_sequence="\\N",
        fetch_size=1000,
        table_export_settings={},
        job_name="JOB_1",
    )

    assert result["status"] == "success"
    assert result["rows"] == 14471
    assert result["columns"] == 198
    assert result["remote_path"] == "(skipped: already uploaded)"
    assert result["error"] is None
def test_reconcile_completed_tables(monkeypatch):
    """Verifies that reconcile_completed_tables updates migration_record status when it has diverged from source_table_selection."""
    from utils import database_utils
    from utils.database_utils import reconcile_completed_tables

    queries = []

    class DummyCursor:
        def execute(self, sql, params=None):
            queries.append((sql, params))
            self._last_sql = sql
            
        def fetchone(self):
            if "source_table_selection" in self._last_sql:
                return ("completed",)
            elif "migration_record" in self._last_sql:
                # Mock a diverged status (e.g. 'failed')
                return ("failed",)
            return None

        def close(self):
            pass

    class DummyConn:
        def cursor(self):
            return DummyCursor()
        def commit(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(database_utils, "postgres_connection", lambda: DummyConn())

    reconcile_completed_tables(
        connection_id=33,
        job_name="JOB_1",
        migration_session_id="2",
        selected_tables=["T1"]
    )

    # Verify that the update query was executed
    update_executed = False
    for q, p in queries:
        if "UPDATE migration_record" in q and "SET status" in q:
            update_executed = True
            assert p == ('completed', 'JOB_1', '2', 'T1')

    assert update_executed is True
