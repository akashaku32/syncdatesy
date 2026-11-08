import os
from sqlalchemy import create_engine, text

e = create_engine(
    f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}/{os.environ['DB_NAME']}"
)
with e.connect() as c:
    rows = c.execute(
        text(
            """
            SELECT job_name, status, COUNT(*) cnt
            FROM migration_record
            WHERE updated_at > NOW() - INTERVAL '24 hours'
            GROUP BY job_name, status
            ORDER BY job_name, status
            """
        )
    ).fetchall()

jobs = {}
for jn, st, cnt in rows:
    jobs.setdefault(jn, {})[st] = cnt

print("Jobs in last 24h by status:")
for jn in sorted(jobs):
    m = jobs[jn]
    ip = m.get("in_progress", 0)
    comp = m.get("completed", 0) + m.get("success", 0)
    fail = m.get("failed", 0) + m.get("failure", 0)
    print(f"  {jn[:75]}")
    print(f"    {dict(m)}")
