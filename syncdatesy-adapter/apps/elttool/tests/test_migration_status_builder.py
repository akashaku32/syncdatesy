"""Tests for migration status overall-status roll-up edge cases."""
import unittest

from apps.elttool.services.migration_status_builder import (
    is_generic_table_failure_message,
)


def resolve_overall_status(
    *,
    any_failed,
    all_completed,
    any_open=False,
    latest_job_terminal_status,
    table_warning_messages=None,
    partial_warning_message=None,
    is_amazon_s3=False,
    s3_multi_single_mode=False,
):
    """Mirror migration_status_builder overall-status logic (pure helper)."""
    overall_status = (
        'in_progress' if any_open else
        'failed' if any_failed else
        'completed' if all_completed else
        'in_progress'
    )
    if not any_open and all_completed and not any_failed:
        overall_status = 'completed'
    elif not any_open and (not all_completed or any_failed):
        if latest_job_terminal_status == 'F':
            overall_status = 'failed'
        elif latest_job_terminal_status == 'P' and not (
            bool(table_warning_messages or partial_warning_message)
            and is_amazon_s3
            and s3_multi_single_mode
        ):
            overall_status = 'failed'
    elif latest_job_terminal_status == 'S' and not any_open and not any_failed:
        overall_status = 'completed'
    return overall_status


class TableFailureMessageTests(unittest.TestCase):
    def test_generic_tables_failed_stub(self):
        self.assertTrue(
            is_generic_table_failure_message("Tables failed: ['accountingdistribution']")
        )

    def test_real_databricks_error_is_not_generic(self):
        msg = (
            "An error occurred while processing the table 'accountingdistribution': "
            "Databricks SQL warehouse request failed: Invalid access token."
        )
        self.assertFalse(is_generic_table_failure_message(msg))

    def test_aggregate_multi_table_summary_is_generic(self):
        msg = (
            "Tables failed: ['a']; Tables failed: ['b']; "
            "Tables failed: ['c']; Tables failed: ['d']"
        )
        self.assertTrue(is_generic_table_failure_message(msg))


class MigrationStatusOverallTests(unittest.TestCase):
    def test_stale_job_failure_log_does_not_override_completed_tables(self):
        status = resolve_overall_status(
            any_failed=False,
            all_completed=True,
            latest_job_terminal_status='F',
        )
        self.assertEqual(status, 'completed')

    def test_job_failure_still_marks_in_progress_run_failed(self):
        status = resolve_overall_status(
            any_failed=False,
            all_completed=False,
            any_open=False,
            latest_job_terminal_status='F',
        )
        self.assertEqual(status, 'failed')

    def test_stale_job_failure_does_not_override_open_tables(self):
        status = resolve_overall_status(
            any_failed=False,
            all_completed=False,
            any_open=True,
            latest_job_terminal_status='F',
        )
        self.assertEqual(status, 'in_progress')

    def test_table_failure_still_failed(self):
        status = resolve_overall_status(
            any_failed=True,
            all_completed=False,
            latest_job_terminal_status='S',
        )
        self.assertEqual(status, 'failed')


if __name__ == "__main__":
    unittest.main()
