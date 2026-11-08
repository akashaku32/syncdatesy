"""Resume a pipeline run whose executor was interrupted (e.g. deploy restart)."""

import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datasyncher.settings.developer")
if not django.apps.apps.ready:
    django.setup()

from django.utils import timezone

from apps.elttool.models import PipelineEdge, PipelineNode, PipelineRun, User
from apps.elttool.services.migration_status_builder import build_migration_status
from apps.elttool.services.pipeline_notifications import (
    send_pipeline_batch_notification,
    send_pipeline_flow_notification,
)
from apps.elttool.v1.pipeline_graph import (
    ensure_start_node,
    execution_node_ids_and_edges,
    is_batch_node,
)
from apps.elttool.v1.pipeline_run_helpers import DagCycleError, dag_execution_levels
from apps.elttool.v1.pipeline_views import (
    _execute_pipeline_dependency_dag,
    _get_pipeline_run_metadata,
    _resume_pipeline_node_session,
    _set_pipeline_node_metadata,
)


def _sync_completed_nodes(run: PipelineRun) -> None:
    meta = _get_pipeline_run_metadata(run)
    nodes_meta = meta.get("nodes") or {}
    for key, entry in nodes_meta.items():
        if entry.get("status") not in ("running", "in_progress"):
            continue
        session_id = entry.get("migration_session_id")
        if not session_id:
            continue
        data, _, _ = build_migration_status(session_id)
        if not data:
            continue
        overall = (data.get("overall_status") or "").lower()
        if overall == "completed":
            _set_pipeline_node_metadata(
                run,
                int(key),
                status="completed",
                finished_at=timezone.now().isoformat(),
                error=None,
            )
            try:
                node = PipelineNode.objects.select_related("batch").get(
                    id=int(key),
                    pipeline_id=run.pipeline_id,
                )
                send_pipeline_batch_notification(run, node)
            except PipelineNode.DoesNotExist:
                pass
            except Exception:
                pass
        elif overall in ("failed", "timeout"):
            _set_pipeline_node_metadata(
                run,
                int(key),
                status="failed",
                finished_at=timezone.now().isoformat(),
                error=f"Migration ended with status: {overall}",
            )


def _completed_node_ids(run: PipelineRun, exec_node_ids: list[int]) -> set[int]:
    nodes_meta = (_get_pipeline_run_metadata(run).get("nodes") or {})
    completed = set()
    for node_id in exec_node_ids:
        entry = nodes_meta.get(str(node_id)) or {}
        if entry.get("status") == "completed":
            completed.add(node_id)
    return completed


def resume_pipeline_run(run_id: int) -> None:
    run = PipelineRun.objects.select_related("pipeline").get(id=run_id)
    if run.status not in ("running", "failed"):
        print(f"Run {run_id} status={run.status}; nothing to resume.")
        return

    pipeline = run.pipeline
    ensure_start_node(pipeline)
    nodes = list(
        PipelineNode.objects.select_related(
            "batch", "connection", "connection__src_config", "connection__dst_config"
        ).filter(pipeline=pipeline).order_by("order_index")
    )
    edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
    exec_node_ids, edge_pairs = execution_node_ids_and_edges(nodes, edges)
    try:
        dag_execution_levels(exec_node_ids, edge_pairs)
    except (DagCycleError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc

    node_map = {n.id: n for n in nodes if is_batch_node(n)}
    user = None
    if run.triggered_by_id:
        user = User.objects.filter(user_id=run.triggered_by_id).first()

    run.status = "running"
    run.finished_at = None
    run.save(update_fields=["status", "finished_at"])

    _sync_completed_nodes(run)
    executed = list((_get_pipeline_run_metadata(run).get("executed_node_ids") or []))
    initial_completed = _completed_node_ids(run, exec_node_ids)
    for node_id in sorted(initial_completed):
        if node_id not in executed:
            executed.append(node_id)

    def resolve_session(run_obj, node, user_obj, node_id):
        node_meta = (_get_pipeline_run_metadata(run_obj).get("nodes") or {}).get(
            str(node_id)
        ) or {}
        return _resume_pipeline_node_session(run_obj, node, user_obj, node_meta)

    try:
        print(
            "Resuming run %s from completed nodes: %s"
            % (run_id, sorted(initial_completed))
        )
        _execute_pipeline_dependency_dag(
            run,
            node_map,
            user,
            exec_node_ids,
            edge_pairs,
            executed,
            initial_completed=initial_completed,
            resolve_session=resolve_session,
        )

        run.status = "completed"
        run.finished_at = timezone.now()
        run.metadata = {
            **_get_pipeline_run_metadata(run),
            "executed_node_ids": executed,
            "current_node_id": None,
            "current_node_ids": [],
        }
        run.save(update_fields=["status", "finished_at", "metadata"])
        try:
            send_pipeline_flow_notification(run)
        except Exception:
            pass
        print(f"Run {run_id} completed.")
    except Exception as exc:
        run.status = "failed"
        run.finished_at = timezone.now()
        run.metadata = {
            **_get_pipeline_run_metadata(run),
            "executed_node_ids": executed,
            "error": str(exc),
            "current_node_id": None,
            "current_node_ids": [],
        }
        run.save(update_fields=["status", "finished_at", "metadata"])
        try:
            send_pipeline_flow_notification(run)
        except Exception:
            pass
        print(f"Run {run_id} failed: {exc}")
        raise


if __name__ == "__main__":
    run_id = int(sys.argv[1]) if len(sys.argv) > 1 else 109
    resume_pipeline_run(run_id)
