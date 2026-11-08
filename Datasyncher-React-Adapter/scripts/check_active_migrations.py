from django.db import connection

print("=== migration_record last 24h by status ===")
with connection.cursor() as c:
    c.execute(
        """
        SELECT status, COUNT(*) FROM migration_record
        WHERE updated_at > NOW() - INTERVAL '24 hours'
        GROUP BY status ORDER BY COUNT(*) DESC
        """
    )
    for row in c.fetchall():
        print(row)

print("\n=== in_progress / pending jobs ===")
with connection.cursor() as c:
    c.execute(
        """
        SELECT DISTINCT job_name, status, COUNT(*) as cnt
        FROM migration_record
        WHERE LOWER(status) IN ('in_progress', 'pending', 'running', 'started')
        GROUP BY job_name, status
        ORDER BY cnt DESC LIMIT 15
        """
    )
    rows = c.fetchall()
    if not rows:
        print("(none)")
    for row in rows:
        print(row)

print("\n=== recent tool_log (last 5) ===")
with connection.cursor() as c:
    c.execute(
        """
        SELECT time, status, LEFT(message, 80), job_name
        FROM tool_log
        ORDER BY time DESC LIMIT 5
        """
    )
    for row in c.fetchall():
        print(row)
