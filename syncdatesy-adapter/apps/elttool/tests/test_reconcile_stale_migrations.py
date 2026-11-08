"""Tests for reconcile_stale_migrations management command."""
from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase
from django.utils import timezone


def _rows():
    stale_time = timezone.now() - timedelta(hours=12)
    return [
        {
            "id": 1,
            "job_name": "job_a",
            "migration_session_id": 10,
            "connection_id": 5,
            "table_name": "custtable",
            "updated_at": stale_time,
        },
        {
            "id": 2,
            "job_name": "job_a",
            "migration_session_id": 10,
            "connection_id": 5,
            "table_name": "vendtable",
            "updated_at": stale_time,
        },
    ]


class ReconcileStaleMigrationsCommandTests(SimpleTestCase):
    def _main_queryset(self, rows):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.values.return_value = rows
        return qs

    @patch("apps.elttool.management.commands.reconcile_stale_migrations.SourceTableSelection")
    @patch("apps.elttool.management.commands.reconcile_stale_migrations.MigrationRecord")
    def test_dry_run_does_not_update(self, mock_mr, mock_sts):
        mock_mr.objects.filter.return_value = self._main_queryset(_rows())

        out = StringIO()
        call_command("reconcile_stale_migrations", "--dry-run", "--hours=6", stdout=out)

        self.assertEqual(mock_mr.objects.filter.return_value.update.call_count, 0)
        mock_sts.objects.filter.assert_not_called()
        self.assertIn("Stale in_progress rows", out.getvalue())
        self.assertIn("--dry-run", out.getvalue())

    @patch("apps.elttool.management.commands.reconcile_stale_migrations.transaction")
    @patch("apps.elttool.management.commands.reconcile_stale_migrations.SourceTableSelection")
    @patch("apps.elttool.management.commands.reconcile_stale_migrations.MigrationRecord")
    def test_apply_fails_stale_rows(self, mock_mr, mock_sts, mock_txn):
        rows = _rows()
        main_qs = self._main_queryset(rows)
        update_qs = MagicMock()
        update_qs.update.return_value = len(rows)
        mock_mr.objects.filter.side_effect = [main_qs, update_qs]

        sts_qs = MagicMock()
        sts_qs.update.return_value = 2
        mock_sts.objects.filter.return_value = sts_qs
        mock_txn.atomic.return_value.__enter__ = MagicMock(return_value=None)
        mock_txn.atomic.return_value.__exit__ = MagicMock(return_value=False)

        out = StringIO()
        call_command("reconcile_stale_migrations", "--hours=6", stdout=out)

        update_qs.update.assert_called_once()
        self.assertEqual(update_qs.update.call_args.kwargs["status"], "failed")
        mock_sts.objects.filter.assert_called_once()
        self.assertIn("Failed 2 MigrationRecord", out.getvalue())

    @patch("apps.elttool.management.commands.reconcile_stale_migrations.MigrationRecord")
    def test_session_id_filter_passed_to_queryset(self, mock_mr):
        qs = self._main_queryset([])
        mock_mr.objects.filter.return_value = qs

        call_command("reconcile_stale_migrations", "--dry-run", "--session-id=282", stdout=StringIO())

        qs.filter.assert_called_once_with(migration_session_id=282)

    @patch("apps.elttool.management.commands.reconcile_stale_migrations.MigrationRecord")
    def test_nothing_to_reconcile(self, mock_mr):
        mock_mr.objects.filter.return_value = self._main_queryset([])

        out = StringIO()
        call_command("reconcile_stale_migrations", "--hours=6", stdout=out)

        self.assertIn("Nothing to reconcile", out.getvalue())
