"""Tests for surfacing child migration retry errors in activity logs."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from celery_worker import (  # noqa: E402
    _format_child_retry_ui_message,
    _format_task_error_message,
)


class _FakeHTTPException(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def test_format_child_retry_ui_message_job_level():
    msg = _format_child_retry_ui_message(
        "SFTP connection timed out while reaching 173.255.114.5:22.",
        retry_current=1,
        retry_max=2,
    )
    assert msg == (
        "Migration retrying (1/2): SFTP connection timed out while reaching 173.255.114.5:22."
    )
    assert "migration in progress" not in msg.lower()


def test_format_child_retry_ui_message_table_level():
    msg = _format_child_retry_ui_message(
        "Connection refused",
        retry_current=2,
        retry_max=2,
        table_name="MULTI",
    )
    assert msg == "Entity 'MULTI' retrying (2/2): Connection refused"


def test_format_task_error_message_from_http_exception_detail():
    err = _FakeHTTPException(
        "SFTP connection timed out while reaching 173.255.114.5:22."
    )
    assert _format_task_error_message(err) == (
        "SFTP connection timed out while reaching 173.255.114.5:22."
    )
