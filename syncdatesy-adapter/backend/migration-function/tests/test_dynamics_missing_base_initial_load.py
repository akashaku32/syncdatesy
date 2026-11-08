"""Missing Databricks base table must force initial load (ignore Postgres watermarks)."""

from unittest.mock import MagicMock, patch

from services.dynamics.as_source.to_databricks import (
    _force_full_folder_scan_for_missing_base_tables,
)


@patch("services.dynamics.as_source.to_databricks.destination_connection_function")
@patch("services.dynamics.as_source.to_databricks.check_table_exists_databricks")
def test_missing_base_clears_watermark_even_with_last_synced(
    mock_exists,
    mock_dest_connect,
):
    mock_dest_connect.return_value = (MagicMock(), "dynamics_schema")
    mock_exists.return_value = False

    last_checked = {"custgroup": "2026-10-29T05.59.09Z"}
    folder_cursor = "2026-10-29T06.59.10Z"

    new_cursor, missing = _force_full_folder_scan_for_missing_base_tables(
        destination_connection={"schema": "dynamics_schema"},
        connection_id=87,
        cmp_id=66,
        selected_tables=["custgroup"],
        last_checked_date=last_checked,
        last_checked_datedirectory=folder_cursor,
    )

    assert missing == ["custgroup"]
    assert last_checked["custgroup"] is None
    assert new_cursor is None


@patch("services.dynamics.as_source.to_databricks.destination_connection_function")
@patch("services.dynamics.as_source.to_databricks.check_table_exists_databricks")
def test_existing_base_keeps_watermark(mock_exists, mock_dest_connect):
    mock_dest_connect.return_value = (MagicMock(), "dynamics_schema")
    mock_exists.return_value = True

    last_checked = {"custgroup": "2026-10-29T05.59.09Z"}
    folder_cursor = "2026-10-29T06.59.10Z"

    new_cursor, missing = _force_full_folder_scan_for_missing_base_tables(
        destination_connection={"schema": "dynamics_schema"},
        connection_id=87,
        cmp_id=66,
        selected_tables=["custgroup"],
        last_checked_date=last_checked,
        last_checked_datedirectory=folder_cursor,
    )

    assert missing == []
    assert last_checked["custgroup"] == "2026-10-29T05.59.09Z"
    assert new_cursor == folder_cursor
