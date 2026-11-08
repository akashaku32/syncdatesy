"""One-shot: dispatch pending run 1181 and resume dead orchestrators for 1172/1174."""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datasyncher.settings")
django.setup()

from apps.elttool.models import PipelineRun  # noqa: E402
from apps.elttool.models import User  # noqa: E402
from apps.elttool.tasks.pipeline_tasks import execute_pipeline_run  # noqa: E402
from apps.elttool.v1.pipeline_views import (  # noqa: E402
    _execute_pipeline_dependency_dag,
    _get_pipeline_run_metadata,
    _refresh_pipeline_flow_fleet_lease,
    _resume_pipeline_node_session,
    _sync_pipeline_run_nodes_from_migrations,
    execution_node_ids_and_edges,
    is_batch_node,
    load_validated_graph_for_execution,
)


def resume_run(run_id: int) -> None:
    run = PipelineRun.objects.select_related("pipeline", "pipeline__cmp").get(id=run_id)
    if run.status != "running":
        run.status = "running"
        run.save(update_fields=["status"])

    _sync_pipeline_run_nodes_from_migrations(run)
    run.refresh_from_db(fields=["status", "metadata"])
    meta = _get_pipeline_run_metadata(run)
    nodes_meta = meta.get("nodes") or {}

    initial_completed = set()
    for key, entry in nodes_meta.items():
        try:
            node_id = int(key)
        except (TypeError, ValueError):
            continue
        if (entry.get("status") or "").lower() == "completed":
            initial_completed.add(node_id)

    executed = []
    for nid in meta.get("executed_node_ids") or []:
        try:
            executed.append(int(nid))
        except (TypeError, ValueError):
            continue
    for node_id in sorted(initial_completed):
        if node_id not in executed:
            executed.append(node_id)

    pipeline = run.pipeline
    nodes, edges = load_validated_graph_for_execution(pipeline)
    exec_node_ids, edge_pairs = execution_node_ids_and_edges(nodes, edges)
    node_map = {n.id: n for n in nodes if is_batch_node(n)}

    user = None
    if run.triggered_by_id:
        user = User.objects.filter(user_id=run.triggered_by_id).first()

    def resolve_session(run, node, user, node_id):
        fresh = _get_pipeline_run_metadata(run)
        entry = (fresh.get("nodes") or {}).get(str(node_id)) or {}
        return _resume_pipeline_node_session(run, node, user, entry)

    if meta.get("flow_fleet_reuse_enabled"):
        _refresh_pipeline_flow_fleet_lease(run.id)

    print(f"RESUME run={run_id} completed_nodes={sorted(initial_completed)}", flush=True)
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
    print(f"RESUME run={run_id} DAG finished", flush=True)


def dispatch_pending(run_id: int) -> None:
    run = PipelineRun.objects.get(id=run_id)
    if run.status == "pending":
        run.status = "running"
        run.save(update_fields=["status"])
    task = execute_pipeline_run.apply_async(args=[run_id], queue="celery")
    print(f"DISPATCH run={run_id} task_id={task.id}", flush=True)


def main() -> int:
    actions = sys.argv[1:] or ["1181:dispatch", "1172:resume", "1174:resume"]
    for action in actions:
        run_id_str, mode = action.split(":", 1)
        run_id = int(run_id_str)
        if mode == "dispatch":
            dispatch_pending(run_id)
        elif mode == "resume":
            resume_run(run_id)
        else:
            raise SystemExit(f"unknown mode {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
