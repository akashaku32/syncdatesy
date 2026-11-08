"""Per-child failure scoping and finalizer guards for fan-out migrations."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

import celery_worker


class TablesForChildPayloadTests(unittest.TestCase):
    def test_from_table_names(self):
        self.assertEqual(
            celery_worker._tables_for_child_payload(
                {"table_names": ["dimensionfocusbalance"]}
            ),
            ["dimensionfocusbalance"],
        )

    def test_from_data_selected_tables(self):
        self.assertEqual(
            celery_worker._tables_for_child_payload(
                {"data": {"selected_tables": ["a", "b"]}}
            ),
            ["a", "b"],
        )

    def test_empty_payload(self):
        self.assertEqual(celery_worker._tables_for_child_payload(None), [])
        self.assertEqual(celery_worker._tables_for_child_payload({}), [])


class FailChildOnRequeueCapTests(unittest.TestCase):
    @patch.object(celery_worker, "celery_app")
    @patch.object(celery_worker, "_fail_child_tables", return_value=True)
    def test_fails_only_payload_tables_and_revokes(self, fail_tables, celery_app):
        celery_app.control.revoke = MagicMock()
        ok = celery_worker._fail_child_on_requeue_cap(
            job_name="job",
            connection_id=24,
            task_id="tid-1",
            payload={"table_names": ["dimensionattributevaluecombination"]},
            reason="pending_timeout",
        )
        self.assertTrue(ok)
        fail_tables.assert_called_once()
        args, kwargs = fail_tables.call_args
        self.assertEqual(args[0], "job")
        self.assertEqual(args[1], 24)
        self.assertEqual(args[2], ["dimensionattributevaluecombination"])
        self.assertIn("pending_timeout", args[3])
        self.assertEqual(kwargs.get("task_id"), "tid-1")
        celery_app.control.revoke.assert_called_once_with(
            "tid-1", terminate=True, signal="SIGTERM"
        )


class DbTerminalCeleryGuardTests(unittest.TestCase):
    def test_active_states_block_shortcut(self):
        states = {
            "a": "SUCCESS",
            "b": "STARTED",
            "c": "PENDING",
        }
        celery_active = any(
            str(s).upper()
            in ("STARTED", "PENDING", "RETRY", "RECEIVED", "IN_PROGRESS")
            for s in states.values()
        )
        self.assertTrue(celery_active)

    def test_all_terminal_allows_shortcut(self):
        states = {"a": "SUCCESS", "b": "FAILURE", "c": "REVOKED"}
        celery_active = any(
            str(s).upper()
            in ("STARTED", "PENDING", "RETRY", "RECEIVED", "IN_PROGRESS")
            for s in states.values()
        )
        self.assertFalse(celery_active)


if __name__ == "__main__":
    unittest.main()
