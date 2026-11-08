"""Audit recent pipeline/migration errors."""
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django

django.setup()

from django.utils import timezone
from datetime import timedelta

from apps.elttool.models import (
    ErrorLogEntry,
    Log,
    MigrationRecord,
    PipelineRun,
)

now = timezone.now()
since = now - timedelta(hours=48)

print("=== PIPELINE RUNS (last 8) ===")
for r in PipelineRun.objects.order_by("-id")[:8]:
    nodes = (r.metadata or {}).get("nodes") or {}
    counts = {}
    for n in nodes.values():
        st = (n.get("status") or "?")
        counts[st] = counts.get(st, 0) + 1
    err = (r.metadata or {}).get("error") or ""
    print(
        f"run {r.id} pipeline={r.pipeline_id} status={r.status} "
        f"started={r.started_at} finished={r.finished_at} nodes={counts} err={err[:120]}"
    )

print("\n=== FAILED / IN_PROGRESS MIGRATION RECORDS (48h) ===")
mrs = (
    MigrationRecord.objects.filter(updated_at__gte=since, status__in=["failed", "in_progress", "timeout"])
    .order_by("-updated_at")[:40]
)
print("count shown", len(mrs))
for mr in mrs:
    print(
        f"  {mr.status} session={mr.migration_session_id} table={mr.table_name} "
        f"job={str(mr.job_name)[:70]} updated={mr.updated_at} task={mr.task_id}"
    )

print("\n=== FAILED JOB-LEVEL LOGS (48h) ===")
logs = Log.objects.filter(
    time__gte=since,
    status__in=["E", "F", "P"],
    migration_record__isnull=True,
    job_name__isnull=False,
).order_by("-time")[:20]
for l in logs:
    print(
        f"  log={l.log_id} status={l.status} conn={l.connection_id} time={l.time} "
        f"job={str(l.job_name)[:70]} msg={(l.message or '')[:180]}"
    )

print("\n=== ERROR LOG ENTRIES (48h) ===")
try:
    els = ErrorLogEntry.objects.filter(created_at__gte=since).order_by("-id")[:15]
except Exception:
    try:
        els = ErrorLogEntry.objects.order_by("-id")[:15]
    except Exception as exc:
        print("  cannot query ErrorLogEntry:", exc)
        els = []
for e in els:
    fields = {f.name: getattr(e, f.name, None) for f in e._meta.fields}
    msg = str(fields.get("message") or fields.get("error") or fields.get("error_message") or "")[:200]
    print(" ", {k: fields[k] for k in list(fields)[:8]}, "msg=", msg)

print("\n=== ACTIVE RUN NODE ERRORS ===")
for r in PipelineRun.objects.filter(status__in=["running", "failed"]).order_by("-id")[:4]:
    print(f"-- run {r.id} {r.status} --")
    nodes = (r.metadata or {}).get("nodes") or {}
    for nid, n in sorted(nodes.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
        st = n.get("status")
        if st in ("failed", "timeout", "running", "in_progress") or n.get("error"):
            print(
                f"  node {nid} {n.get('batch_name')} status={st} session={n.get('migration_session_id')} "
                f"err={str(n.get('error') or '')[:160]}"
            )
