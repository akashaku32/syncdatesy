"""Tests for bulk connection migration status computation."""
from unittest.mock import MagicMock, patch

from apps.elttool.services.connection_migration_status import (
    _resolve_connector_error_message,
    bulk_connection_migration_status,
    resolve_job_level_display_message,
)


def _log(connection_id, log_id, status):
    log = MagicMock()
    log.connection_id = connection_id
    log.log_id = log_id
    log.status = status
    log.message = "err" if status in ("E", "P", "F") else None
    return log


def _annotate_result(rows):
    return MagicMock(
        values=MagicMock(
            return_value=MagicMock(annotate=MagicMock(return_value=rows))
        )
    )


def _configure_status_queries(mock_log, mock_mr, latest, terminal=None, active=()):
    # filter().exclude().values().annotate() for latest + terminal id maps,
    # then filter(log_id__in=...) twice for the concrete Log rows.
    latest.job_name = "SAP_TO_DB_manual_1"
    latest.message = latest.message or ("err" if latest.status in ("E", "P", "F") else "ok")
    if terminal is not None:
        terminal.job_name = "SAP_TO_DB_manual_1"
        terminal.message = terminal.message or (
            "err" if terminal.status in ("E", "P", "F") else "ok"
        )

    latest_map = _annotate_result(
        [{"connection_id": 8, "latest_log_id": latest.log_id}]
    )
    terminal_map = _annotate_result(
        [{"connection_id": 8, "latest_log_id": terminal.log_id}] if terminal else []
    )
    mock_log.objects.filter.side_effect = [
        MagicMock(exclude=MagicMock(return_value=latest_map)),
        [latest],
        MagicMock(exclude=MagicMock(return_value=terminal_map)),
        [terminal] if terminal else [],
    ]
    mock_mr.objects.filter.return_value.values_list.return_value.distinct.return_value = list(active)


@patch("apps.elttool.services.connection_migration_status.MigrationRecord")
@patch("apps.elttool.services.connection_migration_status.Log")
def test_historical_i_log_does_not_override_newer_failure(mock_log, mock_mr):
    latest = _log(8, 200, "F")
    _configure_status_queries(mock_log, mock_mr, latest, latest)
    result = bulk_connection_migration_status([8])
    assert result[8]["migration_status"] == "E"
    assert result[8]["error_message"] == "err"
    assert "status" not in mock_log.objects.filter.call_args_list[0].kwargs


@patch("apps.elttool.services.connection_migration_status.MigrationRecord")
@patch("apps.elttool.services.connection_migration_status.Log")
def test_in_progress_when_latest_log_is_i(mock_log, mock_mr):
    _configure_status_queries(mock_log, mock_mr, _log(8, 201, "I"), _log(8, 200, "F"))
    assert bulk_connection_migration_status([8])[8]["migration_status"] == "S"


@patch("apps.elttool.services.connection_migration_status.MigrationRecord")
@patch("apps.elttool.services.connection_migration_status.Log")
def test_in_progress_when_mr_active_despite_newer_success_log(mock_log, mock_mr):
    latest = _log(8, 200, "S")
    _configure_status_queries(mock_log, mock_mr, latest, latest, active=[8])
    assert bulk_connection_migration_status([8])[8]["migration_status"] == "S"


@patch("apps.elttool.services.connection_migration_status.MigrationRecord")
@patch("apps.elttool.services.connection_migration_status.Log")
def test_completed_when_latest_success_and_no_active_mr(mock_log, mock_mr):
    latest = _log(8, 200, "S")
    _configure_status_queries(mock_log, mock_mr, latest, latest)
    result = bulk_connection_migration_status([8])
    assert result[8]["migration_status"] == "C"
    assert result[8]["error_message"] is None


def test_resolve_job_level_display_message_from_table_shaped_stub():
    msg = (
        "An error occurred while processing the table 'budgettransactionline': "
        "Databricks SQL warehouse request failed: Invalid access token."
    )
    with patch(
        "apps.elttool.services.connection_migration_status._lookup_table_failure_root_cause",
        return_value="Databricks SQL warehouse request failed: Invalid access token.",
    ), patch(
        "apps.elttool.services.connection_migration_status._find_session_job_summary_message",
        return_value=None,
    ):
        display = resolve_job_level_display_message(
            msg,
            connection_id=68,
            session=19425,
        )
    assert display.startswith("Migration failed.")
    assert "Invalid access token" in display
    assert "budgettransactionline" not in display


def test_transform_publish_audit_is_not_rewritten_as_migration_failed():
    msg = (
        "Entity transform published for table 'EntityOf0FI_GL_4' "
        "(script sha256=7aa6b8716a36…)"
    )
    with patch(
        "apps.elttool.services.connection_migration_status._lookup_table_failure_root_cause",
        return_value="some old table error",
    ):
        display = resolve_job_level_display_message(
            msg,
            connection_id=84,
            session=None,
        )
    assert display == msg
    assert "Migration failed" not in display


def test_resolve_connector_error_ignores_transform_audit():
    msg = "Entity transform published for table 'EntityOf0FI_GL_4'"
    assert _resolve_connector_error_message(84, msg) is None


@patch(
    "apps.elttool.services.connection_migration_status._lookup_table_failure_root_cause",
    return_value="Databricks SQL warehouse request failed: Invalid access token.",
)
def test_resolve_generic_child_group_stub_from_table_log(_mock_lookup):
    resolved = _resolve_connector_error_message(68, "4 child group(s) failed")
    assert "Invalid access token" in resolved


@patch("apps.elttool.services.connection_migration_status.MigrationRecord")
@patch("apps.elttool.services.connection_migration_status.Log")
def test_error_from_latest_terminal_failure(mock_log, mock_mr):
    latest = _log(8, 200, "E")
    _configure_status_queries(mock_log, mock_mr, latest, latest)
    result = bulk_connection_migration_status([8])
    assert result[8]["migration_status"] == "E"
    assert result[8]["error_message"] == "err"
