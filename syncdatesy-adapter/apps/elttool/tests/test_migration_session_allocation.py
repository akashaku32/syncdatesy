"""Tests for migration session ID allocation under concurrency."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class AllocateMigrationSessionIdTests(SimpleTestCase):
    @patch("apps.elttool.v1.views.Log")
    @patch("apps.elttool.v1.views.MigrationRecord")
    @patch("apps.elttool.v1.views.db_connection")
    def test_uses_max_of_migration_record_and_log_sessions(
        self, mock_db_connection, mock_migration_record, mock_log
    ):
        mock_db_connection.vendor = "sqlite"
        mock_migration_record.objects.aggregate.return_value = {
            "migration_session_id__max": 140,
        }
        mock_log.objects.aggregate.return_value = {"session__max": 145}

        from apps.elttool.v1.views import _allocate_migration_session_id

        self.assertEqual(_allocate_migration_session_id(), 146)

    @patch("apps.elttool.v1.views.Log")
    @patch("apps.elttool.v1.views.MigrationRecord")
    @patch("apps.elttool.v1.views.db_connection")
    def test_postgres_uses_transaction_scoped_advisory_lock(
        self, mock_db_connection, mock_migration_record, mock_log
    ):
        mock_db_connection.vendor = "postgresql"
        cursor = MagicMock()
        mock_db_connection.cursor.return_value.__enter__.return_value = cursor
        mock_migration_record.objects.aggregate.return_value = {
            "migration_session_id__max": 10,
        }
        mock_log.objects.aggregate.return_value = {"session__max": 10}

        from apps.elttool.v1.views import _allocate_migration_session_id

        self.assertEqual(_allocate_migration_session_id(), 11)
        cursor.execute.assert_called_once()
        self.assertIn("pg_advisory_xact_lock", cursor.execute.call_args[0][0])

    @patch("apps.elttool.v1.views.Log")
    @patch("apps.elttool.v1.views.MigrationRecord")
    @patch("apps.elttool.v1.views.db_connection")
    def test_concurrent_allocations_return_distinct_ids(
        self, mock_db_connection, mock_migration_record, mock_log
    ):
        mock_db_connection.vendor = "sqlite"
        state = {"mr_max": 100, "log_max": 100}
        state_lock = threading.Lock()

        def aggregate_mr(*_args, **_kwargs):
            with state_lock:
                return {"migration_session_id__max": state["mr_max"]}

        def aggregate_log(*_args, **_kwargs):
            with state_lock:
                return {"session__max": state["log_max"]}

        def reserve_log_session(session):
            with state_lock:
                state["log_max"] = max(state["log_max"], session or 0)

        mock_migration_record.objects.aggregate.side_effect = aggregate_mr
        mock_log.objects.aggregate.side_effect = aggregate_log

        from apps.elttool.v1.views import _allocate_migration_session_id

        def allocate_and_reserve():
            next_id = _allocate_migration_session_id()
            reserve_log_session(next_id)
            return next_id

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: allocate_and_reserve(), range(16)))

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(ids), list(range(101, 117)))
