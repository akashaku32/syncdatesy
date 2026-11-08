"""Check active runs and in-progress migrations."""
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django

django.setup()

from django.db import connection

with connection.cursor() as c:
    c.execute(
        """
        SELECT id, pipeline_id, status, started_at, finished_at
        FROM pipeline_runs
        WHERE status = 'running'
        ORDER BY id DESC
        LIMIT 10
        """
    )
    print("RUNNING RUNS:")
    for r in c.fetchall():
        print(r)

    c.execute(
        """
        SELECT migration_session_id, table_name, status, job_name, updated_at
        FROM migration_records
        WHERE status = 'in_progress'
        ORDER BY updated_at DESC
        LIMIT 20
        """
    )
    print("IN_PROGRESS MRS:")
    for r in c.fetchall():
        print(r)

    c.execute(
        """
        SELECT id, status, metadata->'nodes'
        FROM pipeline_runs
        WHERE id IN (1119, 1120)
        """
    )
    print("RUNS 1119/1120:")
    for r in c.fetchall():
        print("run", r[0], "status", r[1])
