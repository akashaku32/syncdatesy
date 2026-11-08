"""Show KEDA pod allocation and live worker assignment for a job prefix."""
import json
import os
import re
import subprocess
import sys

import redis
from sqlalchemy import create_engine, text

JOB_PREFIX = sys.argv[1] if len(sys.argv) > 1 else "D365_PROD_TEST"

r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
list_key = os.environ.get("CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY", "celery:keda:desired-replicas")
entries = r.lrange(list_key, 0, -1)
job_entries = []
for raw in entries:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        continue
    jn = str(obj.get("job_name") or "")
    if JOB_PREFIX in jn:
        job_entries.append(obj)

print(f"=== KEDA desired-replicas for '{JOB_PREFIX}' ===")
print(f"List entries matching job: {len(job_entries)}")
for e in job_entries[:5]:
    print(f"  job={str(e.get('job_name',''))[:70]}")
    print(f"    plan_cap={e.get('plan_cap')} per_job_pods={e.get('per_job_pods')} tables={e.get('table_count')}")
if len(job_entries) > 5:
    print(f"  ... and {len(job_entries)-5} more")

# Connection plan from DB
e = create_engine(
    f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}/{os.environ['DB_NAME']}"
)
with e.connect() as c:
    row = c.execute(
        text(
            """
            SELECT c.connection_name, p.plan_name
            FROM connection c
            LEFT JOIN subscription s ON s.id = c.subscription_id
            LEFT JOIN plan p ON p.id = s.plan_id
            WHERE c.connection_name ILIKE :pat
            LIMIT 3
            """
        ),
        {"pat": f"%{JOB_PREFIX.split('_manual')[0].replace('D365_', 'D365_')}%"},
    ).fetchall()
print("\n=== Connection / plan (DB) ===")
for conn_name, plan in row:
    print(f"  {conn_name}: plan={plan}")

# Per-pod active tasks from celery inspect
print("\n=== Pods actively running this job (celery inspect active) ===")
out = subprocess.check_output(
    [
        "celery",
        "-A",
        "celery_worker.celery_app",
        "inspect",
        "active",
        "--timeout",
        "10",
    ],
    text=True,
    stderr=subprocess.STDOUT,
)
pods_for_job: dict[str, int] = {}
for block in re.split(r"celery@(\S+)", out):
    pass
# parse worker blocks
parts = re.findall(
    r"celery@(\S+).*?'job_name': '([^']*"
    + re.escape(JOB_PREFIX)
    + r"[^']*)'",
    out,
    re.DOTALL,
)
for pod, _jn in parts:
    pods_for_job[pod] = pods_for_job.get(pod, 0) + 1

if not pods_for_job:
  # fallback: count job_name occurrences per worker section
  for m in re.finditer(r"celery@(\S+)\s*\n(.*?)(?=celery@|\Z)", out, re.DOTALL):
    pod = m.group(1)
    section = m.group(2)
    cnt = section.count(JOB_PREFIX)
    if cnt:
      pods_for_job[pod] = cnt

print(f"Pods with active tasks: {len(pods_for_job)}")
for pod, cnt in sorted(pods_for_job.items()):
    print(f"  {pod}: {cnt} active task(s)")
