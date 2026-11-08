"""One-off cluster check: in_progress migration jobs vs Celery task states."""
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

sys.path.insert(0, "/app")
from celery_worker import celery_app  # noqa: E402

user = os.environ["DB_USER"]
password = os.environ["DB_PASSWORD"]
host = os.environ["DB_HOST"]
dbname = os.environ["DB_NAME"]
engine = create_engine(f"postgresql://{user}:{password}@{host}/{dbname}")
now = datetime.now(timezone.utc)

with engine.connect() as conn:
    rows = conn.execute(
        text(
            """
            SELECT job_name, task_id, table_name, updated_at, connection_id
            FROM migration_record
            WHERE status = 'in_progress'
            ORDER BY updated_at ASC
            """
        )
    ).fetchall()

print(f"in_progress rows: {len(rows)}")
jobs: dict = {}
stuck_started_old = []

for job_name, task_id, table_name, updated_at, conn_id in rows:
    j = jobs.setdefault(
        job_name,
        {"total": 0, "pending": 0, "started": 0, "success": 0, "failure": 0, "other": {}},
    )
    j["total"] += 1
    state = celery_app.AsyncResult(str(task_id)).state if task_id else "NO_TASK"
    age_m = 0.0
    if updated_at:
        ut = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at
        age_m = (now - ut).total_seconds() / 60.0
    if state == "PENDING":
        j["pending"] += 1
    elif state == "STARTED":
        j["started"] += 1
        if age_m > 180:
            stuck_started_old.append((job_name, table_name, round(age_m), str(task_id)[:8]))
    elif state == "SUCCESS":
        j["success"] += 1
    elif state == "FAILURE":
        j["failure"] += 1
    else:
        j["other"][state] = j["other"].get(state, 0) + 1

print("\n--- Job summary ---")
for jn, m in sorted(jobs.items(), key=lambda x: -x[1]["total"]):
    short = (jn[:65] + "...") if len(jn) > 68 else jn
    stuck = m["pending"] == m["total"] and m["total"] > 0
    progressing = m["started"] > 0 or m["success"] > 0
    flag = "STUCK" if stuck else ("ACTIVE" if progressing else "WAITING")
    print(f"[{flag}] {short}")
    print(
        f"  total={m['total']} pending={m['pending']} started={m['started']} "
        f"success={m['success']} fail={m['failure']}"
    )

stuck_jobs = [jn for jn, m in jobs.items() if m["pending"] == m["total"] and m["total"] > 0]
print(f"\nSTUCK jobs (all children still PENDING): {len(stuck_jobs)}")
for jn in stuck_jobs:
    print(f"  - {jn[:90]}")

print(f"\nOld STARTED tasks (>3h): {len(stuck_started_old)}")
for x in stuck_started_old[:10]:
    print(f"  {x[2]}m table={x[1]} task={x[3]} job={x[0][:50]}...")
