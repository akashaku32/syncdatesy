"""Compare DB scheduled rows vs live scheduler jobs."""
from __future__ import annotations

import json
import os
import urllib.request

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datasyncher.settings")
django.setup()

from django.db import connection

from apps.elttool.models import Pipeline

with urllib.request.urlopen("http://scheduler-service:5000/jobs/list", timeout=30) as resp:
    raw = json.loads(resp.read().decode())
jobs = raw.get("jobs", raw if isinstance(raw, list) else [])
live_ids = {str(j.get("id")) for j in jobs if isinstance(j, dict)}

with connection.cursor() as c:
    c.execute("SELECT id FROM apscheduler_jobs")
    db_ids = {str(row[0]) for row in c.fetchall()}

pipelines_scheduled = Pipeline.objects.filter(schedule_paused=False).exclude(
    schedule_type__in=["manual", None, ""]
).count()
pipelines_with_job = sum(
    1
    for p in Pipeline.objects.filter(schedule_paused=False).exclude(
        schedule_type__in=["manual", None, ""]
    )
    if f"pipeline_{p.id}" in live_ids
)

legacy_in_db = [i for i in db_ids if "_batch_" in i]
legacy_in_live = [i for i in live_ids if "_batch_" in i]
conn_in_live = [i for i in live_ids if i.startswith("conn_") and "_batch_" not in i]

print(f"LIVE_SCHEDULER_JOBS={len(live_ids)}")
print(f"DB_APSCHEDULER_ROWS={len(db_ids)}")
print(f"PIPELINES_SCHEDULE_ENABLED={pipelines_scheduled}")
print(f"PIPELINES_WITH_LIVE_JOB={pipelines_with_job}")
print(f"LEGACY_BATCH_IN_DB={len(legacy_in_db)}")
print(f"LEGACY_BATCH_IN_LIVE={len(legacy_in_live)}")
print(f"CONN_SYNC_JOBS_LIVE={len(conn_in_live)}")
orphan_db = sorted(db_ids - live_ids)
orphan_live = sorted(live_ids - db_ids)
print(f"DB_NOT_IN_LIVE={len(orphan_db)}")
print(f"LIVE_NOT_IN_DB={len(orphan_live)}")
if orphan_db[:10]:
    print("DB_ORPHAN_SAMPLE=" + json.dumps(orphan_db[:10]))
if orphan_live[:10]:
    print("LIVE_ORPHAN_SAMPLE=" + json.dumps(orphan_live[:10]))

missing = []
for p in Pipeline.objects.filter(schedule_paused=False).exclude(
    schedule_type__in=["manual", None, ""]
):
    if f"pipeline_{p.id}" not in live_ids:
        missing.append(
            {
                "id": p.id,
                "name": p.name,
                "schedule_type": p.schedule_type,
                "status": p.status,
            }
        )
print(f"PIPELINES_MISSING_LIVE_JOB={len(missing)}")
if missing:
    print("MISSING_SAMPLE=" + json.dumps(missing[:10]))
