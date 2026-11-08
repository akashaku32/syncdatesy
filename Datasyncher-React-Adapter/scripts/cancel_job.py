"""Cancel a migration job: mark DB failed, revoke Celery tasks, clear Redis orchestration keys."""
import json
import os

import django
import redis

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datasyncher.settings.developer")
django.setup()

from django.db import connection

JOB = "D365_PROD_TEST_manual_102_ledgerjournaltrans"
TASK_IDS = [
    "401dc230-c0c2-48a6-98c3-45c1b9bbd010",
    "c79b6809-c07f-4aa2-bbd0-6a32f122be0b",
]

r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

print(f"Cancelling job: {JOB}")
with connection.cursor() as c:
    c.execute(
        """
        UPDATE migration_record
        SET status = 'failed', updated_at = NOW()
        WHERE job_name = %s AND status = 'in_progress'
        """,
        [JOB],
    )
    print(f"  migration_record rows updated: {c.rowcount}")

for tid in TASK_IDS:
    for key in [
        f"celery-task-meta-{tid}",
        f"celery:task-heartbeat:{tid}",
        f"celery:child-payload:{tid}",
    ]:
        if r.delete(key):
            print(f"  deleted {key}")

prefixes = [
    f"celery:finalizer-ctx:{JOB}",
    f"celery:fanout-inflight:{JOB}",
    f"celery:fanout-pending:{JOB}",
    f"celery:job-execution-config:{JOB}",
]
for key in prefixes:
    t = r.type(key)
    if t == "none":
        continue
    if t == "hash":
        r.delete(key)
    elif t == "string":
        r.delete(key)
    elif t == "set":
        r.delete(key)
    elif t == "list":
        r.delete(key)
    print(f"  cleared {key} ({t})")

r.set("celery:active-migration-count", "0")
r.delete("celery:keda:desired-replicas")
print("  reset celery:active-migration-count and celery:keda:desired-replicas")
print("Done.")
