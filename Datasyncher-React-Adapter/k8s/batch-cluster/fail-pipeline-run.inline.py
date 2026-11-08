# Piped into: python manage.py shell
# Placeholders replaced by fail-pipeline-run.ps1:
#   __RUN_ID__, __REASON__, __DRY_RUN__, __TEARDOWN__, __REVOKE__

from django.db import transaction
from django.utils import timezone

from apps.elttool.models import MigrationRecord, PipelineRun, SourceTableSelection
from apps.elttool.v1.pipeline_views import _ACTIVE_LOAD_STATUSES, _teardown_pipeline_flow_fleet

run_id = __RUN_ID__
reason = __REASON__
dry_run = __DRY_RUN__
teardown = __TEARDOWN__
revoke = __REVOKE__
ACTIVE = tuple(_ACTIVE_LOAD_STATUSES)

run = PipelineRun.objects.select_related("pipeline").get(id=run_id)
job_prefix = f"pipeline_run_{run_id}"
run_mrs = list(
    MigrationRecord.objects.filter(job_name__startswith=job_prefix).values(
        "id",
        "connection_id",
        "table_name",
        "status",
        "task_id",
        "migration_session_id",
        "job_name",
    )
)
meta = dict(run.metadata or {})
nodes = dict(meta.get("nodes") or {})
session_ids = sorted(
    {
        int(entry["migration_session_id"])
        for entry in nodes.values()
        if isinstance(entry, dict) and entry.get("migration_session_id")
    }
)
session_mrs = []
if session_ids:
    session_mrs = list(
        MigrationRecord.objects.filter(
            migration_session_id__in=session_ids,
            status__in=ACTIVE,
        ).values(
            "id",
            "connection_id",
            "table_name",
            "status",
            "task_id",
            "migration_session_id",
            "job_name",
        )
    )

mr_by_id = {}
for row in run_mrs + session_mrs:
    mr_by_id[row["id"]] = row
mr_rows = list(mr_by_id.values())

connection_tables = {}
for row in mr_rows:
    cid = row.get("connection_id")
    table = row.get("table_name")
    if cid and table:
        connection_tables.setdefault(int(cid), set()).add(str(table))

task_ids = sorted({str(row["task_id"]).strip() for row in mr_rows if row.get("task_id")})
active_mr_ids = [row["id"] for row in mr_rows if row.get("status") in ACTIVE]
sts_candidates = sum(
    SourceTableSelection.objects.filter(
        connection_id=connection_id,
        table__in=list(tables),
        status="in_progress",
    ).count()
    for connection_id, tables in connection_tables.items()
)

print(f"@@RUN_ID={run_id}")
print(f"@@PIPELINE_ID={run.pipeline_id}")
print(f"@@CURRENT_STATUS={run.status}")
print(f"@@MR_COUNT={len(mr_rows)}")
print(f"@@MR_ACTIVE={len(active_mr_ids)}")
print(f"@@STS_IN_PROGRESS={sts_candidates}")
print(f"@@TASKS={len(task_ids)}")

if dry_run:
    print("@@DRY_RUN=1")
else:
    now = timezone.now()
    with transaction.atomic():
        if active_mr_ids:
            MigrationRecord.objects.filter(id__in=active_mr_ids).update(
                status="failed",
                updated_at=now,
            )
        sts_cleared = 0
        for connection_id, tables in connection_tables.items():
            sts_cleared += SourceTableSelection.objects.filter(
                connection_id=connection_id,
                table__in=list(tables),
                status="in_progress",
            ).update(status=None)
        for node_id, entry in list(nodes.items()):
            if isinstance(entry, dict):
                entry = dict(entry)
                entry["status"] = "failed"
                entry["error"] = reason
                entry["finished_at"] = now.isoformat()
                nodes[str(node_id)] = entry
        meta["nodes"] = nodes
        meta["error"] = reason
        meta["failure_pending"] = False
        meta["current_node_id"] = None
        meta["current_node_ids"] = []
        run.status = "failed"
        run.finished_at = now
        run.metadata = meta
        run.save(update_fields=["status", "finished_at", "metadata"])

    if revoke and task_ids:
        try:
            from apps.elttool.services.celery_inspect_service import get_migration_celery_app

            get_migration_celery_app().control.revoke(
                task_ids,
                terminate=True,
                signal="SIGTERM",
            )
        except Exception as exc:
            print(f"@@REVOKE_ERR={exc}")

    if teardown:
        _teardown_pipeline_flow_fleet(run_id)

    print(
        f"@@DONE=1 mrs_failed={len(active_mr_ids)} "
        f"sts_cleared={sts_cleared} tasks_revoked={len(task_ids)}"
    )
