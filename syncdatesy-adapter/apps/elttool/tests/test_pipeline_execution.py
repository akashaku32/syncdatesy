"""Tests for pipeline execution flow (_execute_pipeline_strict, _wait_for_migration_session)."""
from __future__ import annotations

import threading
import time
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.elttool.v1.pipeline_views import (
    _execute_pipeline_strict,
    _fail_pipeline_run,
    _failed_status_is_confirmed,
    _run_metadata_consumes_flow_slot,
    _wait_for_migration_session,
)


def _make_pipeline_node(node_id: int = 5, tables: tuple[str, ...] = ("account",)):
    batch_tables = MagicMock()
    batch_tables.values_list.return_value = list(tables)
    batch_tables.count.return_value = len(tables)
    batch = SimpleNamespace(
        name="first",
        tables=batch_tables,
        execution_order="parallel",
        time_frequency="15",
    )
    src_cfg = SimpleNamespace(data={"host": "d365.example"}, name="D365_TEST")
    dst_cfg = SimpleNamespace(data={"schema": "PUBLIC"}, name="Snowflake")
    connection = SimpleNamespace(
        connection_id=6,
        cmp_id=1,
        safety_interval="",
        logs_creation=True,
        DELETED="_SPECT_DELETED",
        SYNCED="_SPECT_SYNCED",
        dst_schema="BATCH_SCHEMA",
        src_config=src_cfg,
        dst_config=dst_cfg,
    )
    return SimpleNamespace(
        id=node_id,
        batch=batch,
        connection=connection,
        execution_order="parallel",
        time_frequency="15",
        batch_id=1,
        order_index=0,
        node_label="batch",
        connection_id=connection.connection_id,
    )


def _mock_migration_record_queryset(
    mock_records,
    *,
    exists_return=False,
    exists_side_effect=None,
    values_list_return=(),
):
    qs = MagicMock()
    qs.filter.return_value = qs
    qs.exclude.return_value = qs
    if exists_side_effect is not None:
        qs.exists.side_effect = exists_side_effect
    else:
        qs.exists.return_value = exists_return
    qs.values_list.return_value = list(values_list_return)
    mock_records.filter.return_value = qs
    if hasattr(mock_records, "objects"):
        mock_records.objects.filter.return_value = qs
    return qs


def _make_pipeline_run(run_id: int = 83):
    run = SimpleNamespace(
        id=run_id,
        pipeline_id=1,
        status="running",
        finished_at=None,
        triggered_by_id=1,
        metadata={},
        pipeline=SimpleNamespace(id=1, cmp=None, validated_graph=None),
        save=MagicMock(),
        refresh_from_db=MagicMock(),
    )
    return run


def _with_orm_patches(patches, *extra):
    """Enter every ORM patch (including select_for_update / atomic helpers)."""

    @contextmanager
    def _cm():
        with ExitStack() as stack:
            for item in list(patches) + list(extra):
                stack.enter_context(item)
            yield

    return _cm()


class PeekMigrationSessionScopeTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views.MigrationRecord")
    @patch("apps.elttool.services.migration_status_builder.build_migration_status")
    def test_scoped_peek_ignores_other_runs_in_the_same_session(
        self, mock_build_status, mock_records
    ):
        from apps.elttool.v1.pipeline_views import _peek_migration_session

        mock_build_status.return_value = (
            {"overall_status": "completed", "tables": []},
            None,
            200,
        )
        _mock_migration_record_queryset(
            mock_records,
            exists_return=False,
            values_list_return=["completed", "completed"],
        )

        status, _data = _peek_migration_session(
            18439,
            connection_id=64,
            job_name_prefix="pipeline_run_5124_node_339",
        )
        self.assertEqual(status, "completed")
        mock_build_status.assert_called_once_with(18439, connection_id=64)


class WaitForMigrationSessionTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views.time.sleep")
    @patch("apps.elttool.services.migration_status_builder.build_migration_status")
    def test_returns_completed_when_migration_finishes(
        self, mock_build_status, mock_sleep
    ):
        mock_build_status.return_value = (
            {"overall_status": "completed", "tables": []},
            None,
            200,
        )
        status, data = _wait_for_migration_session(103, poll_interval=0, timeout=1)
        self.assertEqual(status, "completed")
        self.assertEqual(data["overall_status"], "completed")
        mock_sleep.assert_not_called()

    @patch("apps.elttool.v1.pipeline_views.MigrationRecord.objects")
    @patch("apps.elttool.v1.pipeline_views.time.sleep")
    @patch("apps.elttool.services.migration_status_builder.build_migration_status")
    def test_returns_failed_when_migration_fails(
        self, mock_build_status, mock_sleep, mock_records
    ):
        _mock_migration_record_queryset(mock_records, exists_return=False)
        mock_build_status.return_value = (
            {"overall_status": "failed", "tables": []},
            None,
            200,
        )
        status, _data = _wait_for_migration_session(103, poll_interval=0, timeout=1)
        self.assertEqual(status, "failed")

    @patch("apps.elttool.v1.pipeline_views.PIPELINE_FAILED_CONFIRM_SECONDS", 30)
    @patch("apps.elttool.v1.pipeline_views.time.monotonic")
    @patch("apps.elttool.v1.pipeline_views.MigrationRecord.objects")
    @patch("apps.elttool.v1.pipeline_views.time.sleep")
    @patch("apps.elttool.services.migration_status_builder.build_migration_status")
    def test_failed_peek_keeps_waiting_when_a_retry_resets_the_row(
        self, mock_build_status, mock_sleep, mock_records, mock_monotonic
    ):
        mock_monotonic.side_effect = [0, 0, 40, 200]
        _mock_migration_record_queryset(
            mock_records,
            exists_side_effect=[False, True],
        )
        mock_build_status.side_effect = [
            ({"overall_status": "failed", "tables": []}, None, 200),
            ({"overall_status": "in_progress", "tables": []}, None, 200),
        ]
        status, _data = _wait_for_migration_session(103, poll_interval=0, timeout=100)
        self.assertEqual(status, "timeout")
        mock_sleep.assert_any_call(30)

    @patch("apps.elttool.v1.pipeline_views.PIPELINE_FAILED_CONFIRM_SECONDS", 30)
    @patch("apps.elttool.v1.pipeline_views.time.monotonic")
    @patch("apps.elttool.v1.pipeline_views.MigrationRecord.objects")
    @patch("apps.elttool.v1.pipeline_views.time.sleep")
    @patch("apps.elttool.services.migration_status_builder.build_migration_status")
    def test_failed_peek_fails_when_still_failed_after_the_window(
        self, mock_build_status, mock_sleep, mock_records, mock_monotonic
    ):
        mock_monotonic.side_effect = [0, 0, 10, 10]
        _mock_migration_record_queryset(mock_records, exists_return=False)
        mock_build_status.return_value = (
            {"overall_status": "failed", "tables": []},
            None,
            200,
        )
        status, _data = _wait_for_migration_session(103, poll_interval=0, timeout=100)
        self.assertEqual(status, "failed")
        mock_sleep.assert_any_call(30)
        self.assertEqual(mock_build_status.call_count, 2)

    @patch("apps.elttool.v1.pipeline_views.time.monotonic")
    @patch("apps.elttool.v1.pipeline_views.time.sleep")
    @patch("apps.elttool.services.migration_status_builder.build_migration_status")
    @patch("apps.elttool.v1.pipeline_views.MigrationRecord.objects")
    def test_returns_timeout_when_deadline_exceeded(
        self, mock_records, mock_build_status, mock_sleep, mock_monotonic
    ):
        mock_monotonic.side_effect = [0, 0, 100]
        _mock_migration_record_queryset(mock_records, exists_return=True)
        mock_build_status.return_value = (
            {"overall_status": "in_progress"},
            None,
            200,
        )
        status, data = _wait_for_migration_session(
            103, poll_interval=0, timeout=10
        )
        self.assertEqual(status, "timeout")
        self.assertEqual(data["overall_status"], "in_progress")


class ExecutePipelineStrictTests(SimpleTestCase):
    def _patch_orm(self, run, nodes, edges=None):
        if not isinstance(nodes, list):
            nodes = [nodes]
        edges = edges or []

        # Published snapshot so _prepare_pipeline_execution can resolve nodes.
        if getattr(run.pipeline, "validated_graph", None) is None:
            run.pipeline.validated_graph = {
                "nodes": [
                    {"node_id": node.id, "batch_id": getattr(node, "batch_id", None)}
                    for node in nodes
                ],
                "edges": [
                    {
                        "from_node_id": edge.from_node_id,
                        "to_node_id": edge.to_node_id,
                    }
                    for edge in edges
                ],
                "start_node_id": None,
            }

        run_qs = MagicMock()
        run_qs.get.return_value = run
        sfu_qs = MagicMock()
        sfu_qs.get.return_value = run

        node_filter = MagicMock()
        node_filter.order_by.return_value = nodes
        node_qs = MagicMock()
        node_qs.filter.return_value = node_filter

        edge_filter = MagicMock(return_value=edges)
        user_filter = MagicMock()
        user_filter.first.return_value = SimpleNamespace(user_id=1, id=1)

        # Metadata updates use select_for_update + atomic (cross-process safe).
        # Mirror that with a real lock so ThreadPoolExecutor node workers in
        # these unit tests cannot clobber run.metadata on the shared mock.
        _meta_lock = threading.Lock()

        @contextmanager
        def _atomic_locked():
            with _meta_lock:
                yield

        return (
            patch(
                "apps.elttool.v1.pipeline_views.PipelineRun.objects.select_related",
                return_value=run_qs,
            ),
            patch(
                "apps.elttool.v1.pipeline_views.PipelineNode.objects.select_related",
                return_value=node_qs,
            ),
            patch(
                "apps.elttool.v1.pipeline_graph.PipelineNode.objects.select_related",
                return_value=node_qs,
            ),
            patch(
                "apps.elttool.v1.pipeline_views.PipelineEdge.objects.filter",
                edge_filter,
            ),
            patch(
                "apps.elttool.v1.pipeline_views.User.objects.filter",
                return_value=user_filter,
            ),
            patch(
                "apps.elttool.v1.pipeline_views.ensure_start_node",
                return_value=SimpleNamespace(id=9),
            ),
            patch(
                "apps.elttool.v1.pipeline_views.validate_connected_to_start",
            ),
            patch(
                "apps.elttool.v1.pipeline_views.transaction.atomic",
                side_effect=_atomic_locked,
            ),
            patch(
                "apps.elttool.v1.pipeline_views.PipelineRun.objects.select_for_update",
                return_value=sfu_qs,
            ),
            patch(
                "apps.elttool.services.table_operation_guard.get_in_progress_table_names",
                return_value=set(),
            ),
            patch(
                "apps.elttool.v1.pipeline_views.MigrationRecord.objects.filter",
                return_value=MagicMock(exists=MagicMock(return_value=False)),
            ),
            patch(
                "apps.elttool.v1.pipeline_views.send_pipeline_batch_notification",
            ),
            patch(
                "apps.elttool.v1.pipeline_views.send_pipeline_flow_notification",
            ),
        )

    def _with_orm(self, patches, *extra):
        return _with_orm_patches(patches, *extra)

    def _make_edge(self, from_node_id: int, to_node_id: int):
        return SimpleNamespace(from_node_id=from_node_id, to_node_id=to_node_id)

    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._wait_for_migration_session")
    def test_completes_when_task_and_migration_succeed(
        self, mock_wait, mock_start_session, mock_task
    ):
        run = _make_pipeline_run()
        node = _make_pipeline_node()
        mock_start_session.return_value = ("pipeline_job", 103, 3937)
        mock_task.return_value = [{"status": "success", "tables": ["account"]}]
        mock_wait.return_value = ("completed", {"overall_status": "completed"})

        patches = self._patch_orm(run, node)
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.finished_at)
        mock_task.assert_called_once()
        task_kwargs = mock_task.call_args.kwargs
        self.assertEqual(task_kwargs["connection_id"], 6)
        self.assertEqual(task_kwargs["selected_tables"], ["account"])
        self.assertEqual(task_kwargs["selected_folder_path"], {})
        self.assertEqual(task_kwargs["is_deleted"], "_SPECT_DELETED")
        self.assertEqual(task_kwargs["is_updated"], "_SPECT_SYNCED")
        self.assertEqual(task_kwargs["destination_config"].get("schema"), "BATCH_SCHEMA")
        self.assertIsInstance(task_kwargs["source_config"], dict)
        self.assertIsInstance(task_kwargs["destination_config"], dict)
        # Shared run fleet so parallel batches reuse one org job slot.
        self.assertEqual(task_kwargs.get("fleet_key"), f"pipeline_run_{run.id}")

    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._wait_for_migration_session")
    def test_fails_on_immediate_task_failure(
        self, mock_wait, mock_start_session, mock_task
    ):
        run = _make_pipeline_run()
        node = _make_pipeline_node()
        mock_start_session.return_value = ("pipeline_job", 103, 3937)
        mock_task.return_value = [
            {"status": "failed", "message": "FastAPI returned 422"}
        ]

        patches = self._patch_orm(run, node)
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(run.status, "failed")
        self.assertIn("422", run.metadata.get("error", ""))
        mock_wait.assert_not_called()

    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._wait_for_migration_session")
    def test_fails_when_migration_session_ends_failed(
        self, mock_wait, mock_start_session, mock_task
    ):
        run = _make_pipeline_run()
        node = _make_pipeline_node()
        mock_start_session.return_value = ("pipeline_job", 103, 3937)
        mock_task.return_value = [{"status": "success"}]
        mock_wait.return_value = ("failed", {"overall_status": "failed"})

        patches = self._patch_orm(run, node)
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(run.status, "failed")
        self.assertIn("ended with failed", run.metadata.get("error", ""))

    @patch("apps.elttool.v1.views.start_migration_session")
    def test_fails_when_batch_has_no_tables(self, mock_start_session):
        run = _make_pipeline_run()
        node = _make_pipeline_node(tables=())
        mock_start_session.return_value = ("pipeline_job", 103, 3937)

        patches = self._patch_orm(run, node)
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(run.status, "failed")
        self.assertIn("no tables", run.metadata.get("error", "").lower())
        mock_start_session.assert_not_called()

    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._wait_for_migration_session")
    def test_parallel_roots_run_before_child(
        self, mock_wait, mock_start_session, mock_task
    ):
        run = _make_pipeline_run()
        root_a = _make_pipeline_node(node_id=1, tables=("a",))
        root_b = _make_pipeline_node(node_id=2, tables=("b",))
        child = _make_pipeline_node(node_id=3, tables=("c",))
        edges = [
            self._make_edge(1, 3),
            self._make_edge(2, 3),
        ]

        mock_start_session.side_effect = [
            ("pipeline_job", 201, 1),
            ("pipeline_job", 202, 2),
            ("pipeline_job", 203, 3),
        ]
        call_order = []

        def task_side_effect(**kwargs):
            call_order.append(kwargs["job_name"])
            return [{"status": "success"}]

        mock_task.side_effect = task_side_effect
        mock_wait.return_value = ("completed", {"overall_status": "completed"})

        patches = self._patch_orm(run, [root_a, root_b, child], edges)
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(run.status, "completed")
        self.assertEqual(mock_task.call_count, 3)
        self.assertEqual(
            set(call_order[:2]),
            {"pipeline_run_83_node_1", "pipeline_run_83_node_2"},
        )
        self.assertEqual(call_order[2], "pipeline_run_83_node_3")

    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._wait_for_migration_session")
    def test_parallel_roots_receive_distinct_migration_session_ids(
        self, mock_wait, mock_start_session, mock_task
    ):
        run = _make_pipeline_run()
        root_a = _make_pipeline_node(node_id=1, tables=("a",))
        root_b = _make_pipeline_node(node_id=2, tables=("b",))
        edges = []

        mock_start_session.side_effect = [
            ("pipeline_job", 401, 1),
            ("pipeline_job", 402, 2),
        ]
        mock_task.return_value = [{"status": "success"}]
        mock_wait.return_value = ("completed", {"overall_status": "completed"})

        patches = self._patch_orm(run, [root_a, root_b], edges)
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(mock_start_session.call_count, 2)
        self.assertEqual(run.status, "completed")

    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._wait_for_migration_session")
    def test_failed_root_waits_for_sibling_before_failing_pipeline(
        self, mock_wait, mock_start_session, mock_task
    ):
        run = _make_pipeline_run()
        root_a = _make_pipeline_node(node_id=1, tables=("a",))
        root_b = _make_pipeline_node(node_id=2, tables=("b",))
        child = _make_pipeline_node(node_id=3, tables=("c",))
        edges = [
            self._make_edge(1, 3),
            self._make_edge(2, 3),
        ]

        mock_start_session.side_effect = [
            ("pipeline_job", 301, 1),
            ("pipeline_job", 302, 2),
        ]

        def wait_side_effect(session_id, poll_interval=None, timeout=None, **_kwargs):
            if session_id == 301:
                return ("failed", {"overall_status": "failed"})
            return ("completed", {"overall_status": "completed"})

        mock_wait.side_effect = wait_side_effect
        mock_task.return_value = [{"status": "success"}]

        patches = self._patch_orm(run, [root_a, root_b, child], edges)
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(run.status, "failed")
        self.assertEqual(mock_task.call_count, 2)
        self.assertIn("ended with failed", run.metadata.get("error", ""))
        child_calls = [
            c for c in mock_task.call_args_list
            if c.kwargs.get("job_name") == "pipeline_run_83_node_3"
        ]
        self.assertEqual(child_calls, [])

    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._wait_for_migration_session")
    def test_few_starts_when_s_completes_while_cust_still_running(
        self, mock_wait, mock_start_session, mock_task
    ):
        run = _make_pipeline_run()
        batch_f = _make_pipeline_node(node_id=5, tables=("f1",))
        batch_pr = _make_pipeline_node(node_id=7, tables=("pr1",))
        batch_s = _make_pipeline_node(node_id=6, tables=("s1", "s2"))
        batch_cust = _make_pipeline_node(node_id=14, tables=("c1",))
        batch_few = _make_pipeline_node(node_id=13, tables=("few1",))
        edges = [
            self._make_edge(5, 6),
            self._make_edge(7, 6),
            self._make_edge(7, 14),
            self._make_edge(6, 13),
        ]
        session_by_node = {5: 297, 7: 298, 6: 299, 14: 300, 13: 301}
        mock_start_session.side_effect = [
            ("pipeline_job", session_by_node[5], 1),
            ("pipeline_job", session_by_node[7], 2),
            ("pipeline_job", session_by_node[6], 3),
            ("pipeline_job", session_by_node[14], 4),
            ("pipeline_job", session_by_node[13], 5),
        ]

        few_task_called = threading.Event()
        few_started_during_cust_wait = []

        def task_side_effect(**kwargs):
            if kwargs.get("job_name") == "pipeline_run_83_node_13":
                few_task_called.set()
            return [{"status": "success"}]

        def wait_side_effect(session_id, poll_interval=None, timeout=None, **_kwargs):
            if session_id == session_by_node[14]:
                # Row locks under select_for_update can delay sibling starts slightly.
                for _ in range(500):
                    if few_task_called.is_set():
                        few_started_during_cust_wait.append(True)
                        break
                    time.sleep(0.01)
                else:
                    few_started_during_cust_wait.append(False)
                return ("completed", {"overall_status": "completed"})
            return ("completed", {"overall_status": "completed"})

        mock_task.side_effect = task_side_effect
        mock_wait.side_effect = wait_side_effect

        patches = self._patch_orm(
            run,
            [batch_f, batch_pr, batch_s, batch_cust, batch_few],
            edges,
        )
        with self._with_orm(patches):
            _execute_pipeline_strict(run.id)

        self.assertEqual(run.status, "completed")
        self.assertTrue(few_task_called.is_set())
        few_calls = [
            c
            for c in mock_task.call_args_list
            if c.kwargs.get("job_name") == "pipeline_run_83_node_13"
        ]
        cust_calls = [
            c
            for c in mock_task.call_args_list
            if c.kwargs.get("job_name") == "pipeline_run_83_node_14"
        ]
        self.assertEqual(len(few_calls), 1)
        self.assertEqual(len(cust_calls), 1)
        # Overlap is best-effort: row-locked metadata updates can delay
        # dispatch slightly so Few may start just after Cust's wait returns.
        self.assertEqual(len(few_started_during_cust_wait), 1)


class FailPipelineRunTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views._fail_active_pipeline_load_rows", return_value=0)
    @patch("apps.elttool.v1.pipeline_views._schedule_deferred_pipeline_failure_check")
    @patch("apps.elttool.v1.pipeline_views._teardown_pipeline_flow_fleet")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_has_active_migrations")
    @patch("apps.elttool.v1.pipeline_views.send_pipeline_flow_notification")
    def test_fail_pipeline_run_defers_when_migrations_still_active(
        self,
        mock_notify,
        mock_active,
        mock_teardown,
        mock_schedule,
        mock_fail_rows,
    ):
        run = _make_pipeline_run()
        run.status = "running"
        mock_active.return_value = True

        _fail_pipeline_run(run, [], RuntimeError("transient copy timeout"))

        self.assertEqual(run.status, "running")
        self.assertIsNone(run.finished_at)
        self.assertTrue(run.metadata.get("failure_pending"))
        mock_schedule.assert_called_once_with(run.id)
        mock_teardown.assert_not_called()
        mock_notify.assert_not_called()

    @patch("apps.elttool.v1.pipeline_views._fail_active_pipeline_load_rows", return_value=0)
    @patch("apps.elttool.v1.pipeline_views._teardown_pipeline_flow_fleet")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_has_active_migrations")
    @patch("apps.elttool.v1.pipeline_views.send_pipeline_flow_notification")
    def test_fail_pipeline_run_teardowns_when_no_active_migrations(
        self,
        mock_notify,
        mock_active,
        mock_teardown,
        mock_fail_rows,
    ):
        run = _make_pipeline_run()
        run.status = "running"
        mock_active.return_value = False

        _fail_pipeline_run(run, [], RuntimeError("terminal failure"))

        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)
        mock_fail_rows.assert_called()
        mock_teardown.assert_called_once_with(run.id)
        mock_notify.assert_called_once()

    def test_fail_active_rows_marks_mrs_failed_and_closes_progress_logs(self):
        from apps.elttool.v1.pipeline_views import _fail_active_pipeline_load_rows

        run = _make_pipeline_run()
        mr_qs = MagicMock()
        mr_qs.filter.return_value = mr_qs
        mr_qs.values.return_value = [
            {
                "id": 11,
                "connection_id": 68,
                "table_name": "accountingdistribution",
                "task_id": "t1",
            }
        ]
        sts_qs = MagicMock()
        log_qs = MagicMock()
        with patch(
            "apps.elttool.models.MigrationRecord.objects.filter",
            return_value=mr_qs,
        ), patch(
            "apps.elttool.models.SourceTableSelection.objects.filter",
            return_value=sts_qs,
        ), patch(
            "apps.elttool.models.Log.objects.filter",
            return_value=log_qs,
        ), patch(
            "apps.elttool.v1.pipeline_views.transaction.atomic",
        ), patch(
            "apps.elttool.services.celery_inspect_service.get_migration_celery_app",
        ) as mock_celery:
            count = _fail_active_pipeline_load_rows(
                run,
                reason="Invalid access token",
                node_id=341,
                migration_session_id=18681,
            )

        self.assertEqual(count, 1)
        mr_qs.update.assert_called_once()
        self.assertEqual(mr_qs.update.call_args.kwargs["status"], "failed")
        sts_qs.update.assert_called_once_with(status=None)
        log_qs.update.assert_called_once()
        self.assertEqual(log_qs.update.call_args.kwargs["status"], "E")
        self.assertEqual(
            log_qs.update.call_args.kwargs["message"],
            "Invalid access token",
        )
        mock_celery.return_value.control.revoke.assert_called_once()


class ReconcilePipelineRunStatusTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views.send_pipeline_flow_notification")
    @patch("apps.elttool.v1.pipeline_views._set_pipeline_node_metadata")
    @patch("apps.elttool.services.migration_status_builder.build_migration_status")
    @patch("apps.elttool.v1.pipeline_views.PipelineNode.objects.filter")
    def test_reconcile_marks_run_failed_when_migration_failed(
        self,
        mock_node_filter,
        mock_build_status,
        mock_set_node_metadata,
        mock_notify,
    ):
        from apps.elttool.v1.pipeline_views import _reconcile_pipeline_run_status

        run = _make_pipeline_run()
        run.metadata = {
            "nodes": {
                "14": {
                    "status": "running",
                    "migration_session_id": 296,
                    "batch_name": "cust",
                },
            },
            "current_node_ids": [14],
        }
        mock_build_status.return_value = (
            {"overall_status": "failed", "tables": []},
            None,
            200,
        )
        batch_node = SimpleNamespace(id=14, node_kind="batch")

        def _apply_node_metadata(run_obj, node_id, **fields):
            meta = dict(run_obj.metadata or {})
            nodes = dict(meta.get("nodes") or {})
            entry = dict(nodes.get(str(node_id)) or {})
            entry.update(fields)
            nodes[str(node_id)] = entry
            meta["nodes"] = nodes
            run_obj.metadata = meta

        mock_set_node_metadata.side_effect = _apply_node_metadata
        mock_node_filter.return_value = [batch_node]

        with patch(
            "apps.elttool.v1.pipeline_views.is_start_node",
            return_value=False,
        ), patch(
            "apps.elttool.v1.pipeline_views.MigrationRecord.objects.filter"
        ) as mock_mr_filter:
            mock_mr_filter.return_value.exists.return_value = False
            _reconcile_pipeline_run_status(run)

        mock_set_node_metadata.assert_called_once()
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)
        mock_notify.assert_called_once()

    @patch("apps.elttool.v1.pipeline_views._sync_pipeline_run_nodes_from_migrations")
    def test_reconcile_skips_terminal_runs(self, mock_sync):
        from apps.elttool.v1.pipeline_views import _reconcile_pipeline_run_status

        run = _make_pipeline_run()
        run.status = "completed"
        _reconcile_pipeline_run_status(run)
        mock_sync.assert_not_called()


class DeferredPipelineFailureTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views._fail_active_pipeline_load_rows", return_value=0)
    @patch("apps.elttool.v1.pipeline_views._teardown_pipeline_flow_fleet")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_has_active_migrations")
    @patch("apps.elttool.v1.pipeline_views.send_pipeline_flow_notification")
    @patch("apps.elttool.v1.pipeline_views._sync_pipeline_run_nodes_from_migrations")
    def test_reconcile_finalizes_deferred_failure(
        self,
        mock_sync,
        mock_notify,
        mock_active,
        mock_teardown,
        mock_fail_rows,
    ):
        from apps.elttool.v1.pipeline_views import _reconcile_pipeline_run_status

        run = _make_pipeline_run()
        run.status = "running"
        run.metadata = {
            "failure_pending": True,
            "error": "transient copy timeout",
            "nodes": {"14": {"status": "running"}},
        }
        mock_active.return_value = False

        _reconcile_pipeline_run_status(run)

        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)
        self.assertFalse(run.metadata.get("failure_pending"))
        mock_teardown.assert_called_once_with(run.id)
        mock_notify.assert_called_once()

    @patch("apps.elttool.v1.pipeline_views._teardown_pipeline_flow_fleet")
    @patch("apps.elttool.v1.pipeline_views._pipeline_run_has_active_migrations")
    @patch("apps.elttool.v1.pipeline_views.send_pipeline_flow_notification")
    @patch("apps.elttool.v1.pipeline_views._sync_pipeline_run_nodes_from_migrations")
    def test_reconcile_leaves_deferred_failure_while_migrations_active(
        self,
        mock_sync,
        mock_notify,
        mock_active,
        mock_teardown,
    ):
        from apps.elttool.v1.pipeline_views import _reconcile_pipeline_run_status

        run = _make_pipeline_run()
        run.status = "running"
        run.metadata = {
            "failure_pending": True,
            "error": "transient copy timeout",
            "nodes": {"14": {"status": "running"}},
        }
        mock_active.return_value = True

        _reconcile_pipeline_run_status(run)

        self.assertEqual(run.status, "running")
        self.assertTrue(run.metadata.get("failure_pending"))
        mock_teardown.assert_not_called()
        mock_notify.assert_not_called()
        mock_sync.assert_called_once()


class FlowSlotTests(SimpleTestCase):
    def test_deferred_failure_does_not_consume_a_flow_slot(self):
        self.assertFalse(
            _run_metadata_consumes_flow_slot(
                {"executor": "celery", "failure_pending": True}
            )
        )

    def test_running_status_consumes_a_flow_slot_even_before_executor(self):
        self.assertTrue(
            _run_metadata_consumes_flow_slot({"executor": "celery"}, status="running")
        )
        self.assertTrue(_run_metadata_consumes_flow_slot({}, status="running"))
        self.assertFalse(_run_metadata_consumes_flow_slot({}, status="queued"))


class PipelineGhostSessionResumeTests(SimpleTestCase):
    @patch("apps.elttool.v1.pipeline_views._start_pipeline_node_session")
    @patch("apps.elttool.v1.pipeline_views._reconcile_ghost_pipeline_node_session")
    @patch("apps.elttool.v1.pipeline_views._pipeline_node_session_is_ghost")
    @patch("apps.elttool.v1.pipeline_views._build_pipeline_node_execution_context")
    def test_resume_redispatches_when_session_is_ghost(
        self,
        mock_build_ctx,
        mock_is_ghost,
        mock_reconcile,
        mock_start_session,
    ):
        from apps.elttool.v1.pipeline_views import _resume_pipeline_node_session

        run = _make_pipeline_run()
        node = _make_pipeline_node()
        user = SimpleNamespace(user_id=1)
        node_meta = {
            "migration_session_id": 14093,
            "pipeline_job_name": "pipeline_run_83_node_5",
            "status": "running",
        }
        mock_is_ghost.return_value = True
        mock_start_session.return_value = {"migration_session_id": 14099}

        ctx, skip = _resume_pipeline_node_session(run, node, user, node_meta)

        mock_is_ghost.assert_called_once()
        mock_reconcile.assert_called_once()
        mock_start_session.assert_called_once_with(run, node, user)
        mock_build_ctx.assert_not_called()
        self.assertEqual(ctx["migration_session_id"], 14099)
        self.assertFalse(skip)

    @patch("apps.elttool.v1.pipeline_views._start_pipeline_node_session")
    @patch("apps.elttool.v1.pipeline_views._reconcile_ghost_pipeline_node_session")
    @patch("apps.elttool.v1.pipeline_views._pipeline_node_session_is_ghost")
    @patch("apps.elttool.v1.pipeline_views._build_pipeline_node_execution_context")
    def test_resume_keeps_waiting_when_session_still_live(
        self,
        mock_build_ctx,
        mock_is_ghost,
        mock_reconcile,
        mock_start_session,
    ):
        from apps.elttool.v1.pipeline_views import _resume_pipeline_node_session

        run = _make_pipeline_run()
        node = _make_pipeline_node()
        user = SimpleNamespace(user_id=1)
        node_meta = {
            "migration_session_id": 14093,
            "log_id": 999,
            "pipeline_job_name": "pipeline_run_83_node_5",
            "status": "running",
        }
        mock_is_ghost.return_value = False
        mock_build_ctx.return_value = {"selected_tables": ["account"]}

        ctx, skip = _resume_pipeline_node_session(run, node, user, node_meta)

        mock_reconcile.assert_not_called()
        mock_start_session.assert_not_called()
        self.assertEqual(ctx["migration_session_id"], 14093)
        self.assertEqual(ctx["log_id"], 999)
        self.assertTrue(skip)


class NonblockingOrchestrationTests(SimpleTestCase):
    _patch_orm = ExecutePipelineStrictTests._patch_orm
    _make_edge = ExecutePipelineStrictTests._make_edge

    def _with_orm(self, patches, *extra):
        return _with_orm_patches(patches, *extra)

    def _publish(self, run, nodes, edges):
        if not isinstance(nodes, list):
            nodes = [nodes]
        run.pipeline.validated_graph = {
            "nodes": [
                {"node_id": node.id, "batch_id": node.batch_id}
                for node in nodes
            ],
            "edges": [
                {"from_node_id": edge.from_node_id, "to_node_id": edge.to_node_id}
                for edge in edges
            ],
            "start_node_id": None,
        }

    @patch("apps.elttool.v1.pipeline_views.send_pipeline_flow_notification")
    @patch(
        "apps.elttool.services.table_operation_guard.get_in_progress_table_names",
        return_value=set(),
    )
    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._peek_migration_session")
    def test_level_progression_does_not_wait_inside_the_start(
        self, mock_peek, mock_start_session, mock_task, _busy, _notify
    ):
        from apps.elttool.v1.pipeline_views import (
            advance_nonblocking_pipeline_run,
            begin_nonblocking_pipeline_run,
        )

        run = _make_pipeline_run()
        root = _make_pipeline_node(node_id=1, tables=("a",))
        child = _make_pipeline_node(node_id=2, tables=("b",))
        edges = [self._make_edge(1, 2)]
        self._publish(run, [root, child], edges)
        mock_start_session.side_effect = [
            ("pipeline_job", 501, 1),
            ("pipeline_job", 502, 2),
        ]
        mock_task.return_value = [{"status": "success"}]
        mock_peek.return_value = ("running", {"overall_status": "in_progress"})

        patches = self._patch_orm(run, [root, child], edges)
        records = patch("apps.elttool.v1.pipeline_views.MigrationRecord.objects")
        with self._with_orm(patches, records):
            action = begin_nonblocking_pipeline_run(run.id)

        self.assertEqual(action, "continue")
        self.assertEqual(run.status, "running")
        self.assertEqual(mock_task.call_count, 1)
        self.assertEqual(
            mock_task.call_args.kwargs["job_name"],
            "pipeline_run_83_node_1",
        )

        mock_peek.return_value = ("completed", {"overall_status": "completed"})
        with self._with_orm(patches, records):
            second = advance_nonblocking_pipeline_run(run.id)
        self.assertEqual(second, "continue")
        self.assertEqual(mock_task.call_count, 2)

        with self._with_orm(patches, records):
            third = advance_nonblocking_pipeline_run(run.id)
        self.assertEqual(third, "done")
        self.assertEqual(run.status, "completed")

    @patch("apps.elttool.v1.pipeline_views.send_pipeline_flow_notification")
    @patch(
        "apps.elttool.services.table_operation_guard.get_in_progress_table_names",
        return_value=set(),
    )
    @patch("apps.elttool.v1.views.task")
    @patch("apps.elttool.v1.views.start_migration_session")
    @patch("apps.elttool.v1.pipeline_views._peek_migration_session")
    def test_failed_node_skips_dependents_on_the_next_poll(
        self, mock_peek, mock_start_session, mock_task, _busy, _notify
    ):
        from apps.elttool.v1.pipeline_views import (
            advance_nonblocking_pipeline_run,
            begin_nonblocking_pipeline_run,
        )

        run = _make_pipeline_run()
        root = _make_pipeline_node(node_id=1, tables=("a",))
        child = _make_pipeline_node(node_id=2, tables=("b",))
        edges = [self._make_edge(1, 2)]
        self._publish(run, [root, child], edges)
        mock_start_session.return_value = ("pipeline_job", 601, 1)
        mock_task.return_value = [{"status": "success"}]
        mock_peek.return_value = ("failed", {"overall_status": "failed", "tables": []})

        patches = self._patch_orm(run, [root, child], edges)
        records = patch("apps.elttool.v1.pipeline_views.MigrationRecord.objects")
        with self._with_orm(patches), records as mock_records:
            mock_records.filter.return_value.exists.return_value = False
            begin_nonblocking_pipeline_run(run.id)
            waiting = advance_nonblocking_pipeline_run(run.id)
            self.assertEqual(waiting, "continue")
            with patch(
                "apps.elttool.v1.pipeline_views._failed_status_is_confirmed",
                return_value=(True, None),
            ):
                action = advance_nonblocking_pipeline_run(run.id)

        self.assertEqual(action, "done")
        self.assertEqual(run.status, "failed")
        self.assertEqual(mock_task.call_count, 1)
        child_node = (run.metadata.get("nodes") or {}).get("2") or {}
        self.assertEqual(child_node.get("status"), "skipped")

    @patch("apps.elttool.v1.pipeline_views._execute_pipeline_strict")
    @patch("apps.elttool.tasks.pipeline_tasks._schedule_pipeline_poll")
    @patch("apps.elttool.v1.pipeline_views.begin_nonblocking_pipeline_run", return_value="continue")
    @patch("apps.elttool.v1.pipeline_views._nonblocking_orchestration_enabled", return_value=True)
    @patch("apps.elttool.v1.pipeline_views._mark_pipeline_run_slot_acquired")
    @patch("apps.elttool.v1.pipeline_views.try_acquire_company_flow_slot", return_value=True)
    @patch("apps.elttool.tasks.pipeline_tasks._run_status", return_value="running")
    def test_flag_uses_nonblocking_start_for_a_new_run(
        self, _status, _cap, _slot, _flag, mock_begin, mock_poll, mock_strict
    ):
        from apps.elttool.tasks.pipeline_tasks import execute_pipeline_run

        metadata = MagicMock()
        metadata.values_list.return_value.first.return_value = {}
        with patch(
            "apps.elttool.models.PipelineRun.objects.filter",
            return_value=metadata,
        ):
            execute_pipeline_run.run(83)

        mock_begin.assert_called_once_with(83)
        mock_poll.assert_called_once_with(83)
        mock_strict.assert_not_called()

    @patch("apps.elttool.v1.pipeline_views._mark_pipeline_run_waiting_for_capacity")
    @patch("apps.elttool.v1.pipeline_views.try_acquire_company_flow_slot", return_value=False)
    @patch("apps.elttool.tasks.pipeline_tasks._run_status", return_value="queued")
    def test_plan_cap_still_retries_before_a_flow_starts(self, _status, _cap, _mark):
        from celery.exceptions import Retry

        from apps.elttool.tasks.pipeline_tasks import execute_pipeline_run

        with self.assertRaises(Retry):
            execute_pipeline_run.run(83)
        _mark.assert_called_once_with(83)

    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.tasks.pipeline_tasks._pipeline_run_ids_with_pending_execute_tasks")
    @patch("apps.elttool.models.PipelineRun.objects")
    def test_redispatches_orphaned_queued_capacity_waits(
        self, mock_objects, mock_pending, mock_dispatch
    ):
        from datetime import timedelta

        from django.utils import timezone

        from apps.elttool.tasks.pipeline_tasks import redispatch_orphaned_queued_pipeline_runs

        old = timezone.now() - timedelta(hours=2)
        run = SimpleNamespace(
            id=5133,
            started_at=old,
            metadata={
                "waiting_for_capacity": True,
                "waiting_since": old.isoformat(),
            },
        )
        mock_objects.filter.return_value.only.return_value = [run]
        mock_pending.return_value = set()

        redispatched = redispatch_orphaned_queued_pipeline_runs(now=timezone.now())

        self.assertEqual(redispatched, [5133])
        mock_dispatch.assert_called_once_with(5133)

    @patch("apps.elttool.v1.pipeline_views._dispatch_pipeline_run")
    @patch("apps.elttool.tasks.pipeline_tasks._pipeline_run_ids_with_pending_execute_tasks")
    @patch("apps.elttool.models.PipelineRun.objects")
    def test_skips_redispatch_when_execute_task_is_already_pending(
        self, mock_objects, mock_pending, mock_dispatch
    ):
        from datetime import timedelta

        from django.utils import timezone

        from apps.elttool.tasks.pipeline_tasks import redispatch_orphaned_queued_pipeline_runs

        old = timezone.now() - timedelta(hours=2)
        run = SimpleNamespace(
            id=5133,
            started_at=old,
            metadata={"waiting_for_capacity": True, "waiting_since": old.isoformat()},
        )
        mock_objects.filter.return_value.only.return_value = [run]
        mock_pending.return_value = {5133}

        redispatched = redispatch_orphaned_queued_pipeline_runs(now=timezone.now())

        self.assertEqual(redispatched, [])
        mock_dispatch.assert_not_called()

    def test_executorless_run_is_alerted_then_reaped(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.elttool.tasks.pipeline_tasks import pipeline_run_executorless_action

        now = timezone.now()
        self.assertEqual(
            pipeline_run_executorless_action({}, now - timedelta(minutes=11), now),
            "alert",
        )
        self.assertEqual(
            pipeline_run_executorless_action({}, now - timedelta(minutes=16), now),
            "reap",
        )
        self.assertEqual(
            pipeline_run_executorless_action(
                {"executor": "celery"},
                now - timedelta(hours=2),
                now,
            ),
            "ok",
        )

    def test_nonblocking_failure_waits_for_the_confirm_window(self):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        confirmed, seen = _failed_status_is_confirmed(None, now)
        self.assertFalse(confirmed)
        self.assertTrue(seen)
        confirmed, kept = _failed_status_is_confirmed(seen, now + timedelta(seconds=10))
        self.assertFalse(confirmed)
        self.assertEqual(kept, seen)
        confirmed, cleared = _failed_status_is_confirmed(seen, now + timedelta(seconds=31))
        self.assertTrue(confirmed)
        self.assertIsNone(cleared)

    def test_reaper_closes_orphan_rows_only_for_finished_runs(self):
        from apps.elttool.tasks.pipeline_tasks import orphan_migration_groups

        failed_row = SimpleNamespace(
            id=179379,
            job_name="pipeline_run_5067_node_176__table",
        )
        running_row = SimpleNamespace(
            id=2,
            job_name="pipeline_run_9_node_1__table",
        )
        groups = orphan_migration_groups([failed_row, running_row], [5067])
        self.assertEqual(list(groups), [5067])
        self.assertEqual(groups[5067], [failed_row])

    @patch("apps.elttool.services.celery_inspect_service.get_migration_celery_app")
    @patch("apps.elttool.models.SourceTableSelection.objects")
    @patch("apps.elttool.models.PipelineRun.objects")
    @patch("apps.elttool.models.MigrationRecord.objects")
    def test_close_orphaned_records_fails_rows_on_a_finished_run(
        self, mock_records, mock_runs, mock_selections, mock_celery
    ):
        from apps.elttool.tasks.pipeline_tasks import (
            close_orphaned_terminal_migration_records,
            orphan_migration_groups,
            reap_stuck_pipeline_runs,
        )

        row = SimpleNamespace(
            id=179379,
            job_name="pipeline_run_5067_node_176__table",
            connection_id=29,
            table_name="dimensionfocusbalance",
            task_id="f4cea14b",
        )
        listed = MagicMock()
        listed.only.return_value = [row]
        updated = MagicMock()
        still = MagicMock()
        still.exists.return_value = False
        mock_records.filter.side_effect = [listed, updated, still]
        mock_runs.filter.return_value.values_list.return_value = [5067]

        closed = close_orphaned_terminal_migration_records()

        self.assertEqual(
            reap_stuck_pipeline_runs.name,
            "apps.elttool.tasks.reap_stuck_pipeline_runs",
        )
        self.assertFalse(hasattr(orphan_migration_groups, "delay"))
        self.assertEqual(closed, {5067: 1})
        updated.update.assert_called_once()
        self.assertEqual(updated.update.call_args.kwargs["status"], "failed")
        mock_selections.filter.return_value.update.assert_called_once_with(status=None)


class PipelineMetadataLockTests(SimpleTestCase):
    def test_update_metadata_uses_row_lock(self):
        from apps.elttool.v1.pipeline_views import _update_pipeline_run_metadata

        run = _make_pipeline_run()
        locked = MagicMock()
        locked.id = run.id
        locked.metadata = {"executor": "celery"}
        qs = MagicMock()
        qs.get.return_value = locked
        with patch(
            "apps.elttool.v1.pipeline_views.PipelineRun.objects.select_for_update",
            return_value=qs,
        ) as mock_sfu, patch(
            "apps.elttool.v1.pipeline_views.transaction.atomic",
        ):
            _update_pipeline_run_metadata(run, waiting_for_capacity=False)

        mock_sfu.assert_called_once_with()
        qs.get.assert_called_once_with(id=run.id)
        self.assertEqual(locked.metadata.get("waiting_for_capacity"), False)
        locked.save.assert_called_once_with(update_fields=["metadata"])
        self.assertEqual(run.metadata.get("waiting_for_capacity"), False)


class ScheduledDispatchJitterTests(SimpleTestCase):
    def test_jitter_respects_zero_and_positive_bounds(self):
        from apps.elttool.v1 import pipeline_views as pv

        with patch.object(pv, "PIPELINE_SCHEDULE_JITTER_SECONDS", 0):
            self.assertEqual(pv._scheduled_dispatch_jitter_seconds(), 0)

        with patch.object(pv, "PIPELINE_SCHEDULE_JITTER_SECONDS", 300), patch(
            "random.randint", return_value=42
        ) as mock_rand:
            self.assertEqual(pv._scheduled_dispatch_jitter_seconds(), 42)
            mock_rand.assert_called_once_with(0, 300)

    @patch("apps.elttool.tasks.pipeline_tasks.execute_pipeline_run.apply_async")
    def test_dispatch_passes_countdown_when_set(self, mock_apply):
        from apps.elttool.v1.pipeline_views import _dispatch_pipeline_run

        _dispatch_pipeline_run(83, countdown=17)
        mock_apply.assert_called_once()
        self.assertEqual(mock_apply.call_args.kwargs["countdown"], 17)
        self.assertEqual(mock_apply.call_args.kwargs["args"], [83])

        mock_apply.reset_mock()
        _dispatch_pipeline_run(84, countdown=0)
        self.assertNotIn("countdown", mock_apply.call_args.kwargs)
