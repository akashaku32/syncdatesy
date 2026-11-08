from django.db import connection

job = "D365_PROD_TEST_manual_102_ledgerjournaltrans"
print(f"=== migration_record for {job} ===")
with connection.cursor() as c:
    c.execute(
        """
        SELECT id, table_name, status, task_id, start_time, updated_at, staging_records_count
        FROM migration_record
        WHERE job_name = %s
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        [job],
    )
    for row in c.fetchall():
        print(row)
