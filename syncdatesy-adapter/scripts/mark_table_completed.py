"""Mark ledgerjournaltrans completed across migration_record and table selections."""
from django.db import connection as db_conn
from django.utils import timezone

TABLE = "ledgerjournaltrans"
JOB_PREFIX = "D365_PROD_TEST_manual_102"
CONNECTION_ID = 5

print(f"=== Current state for {TABLE} (conn {CONNECTION_ID}) ===")
with db_conn.cursor() as c:
    c.execute(
        """
        SELECT id, job_name, status, updated_at
        FROM migration_record
        WHERE table_name = %s
          AND (job_name LIKE %s OR connection_id = %s)
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        [TABLE, f"{JOB_PREFIX}%", CONNECTION_ID],
    )
    print("migration_record:")
    for row in c.fetchall():
        print(" ", row)

    c.execute(
        """
        SELECT tbl_id, "table", status, last_synced
        FROM source_table_selection
        WHERE connection_id = %s AND LOWER("table") = LOWER(%s)
        """,
        [CONNECTION_ID, TABLE],
    )
    print("source_table_selection:")
    for row in c.fetchall():
        print(" ", row)

    c.execute(
        """
        SELECT tbl_id, "table", status
        FROM destination_table_selection
        WHERE connection_id = %s AND LOWER("table") = LOWER(%s)
        """,
        [CONNECTION_ID, TABLE],
    )
    print("destination_table_selection:")
    for row in c.fetchall():
        print(" ", row)

now = timezone.now()
with db_conn.cursor() as c:
    c.execute(
        """
        UPDATE migration_record
        SET status = 'completed', updated_at = %s
        WHERE table_name = %s
          AND status IN ('in_progress', 'failed', 'pending')
          AND (job_name LIKE %s OR connection_id = %s)
        """,
        [now, TABLE, f"{JOB_PREFIX}%", CONNECTION_ID],
    )
    print(f"\nmigration_record updated: {c.rowcount}")

    c.execute(
        """
        UPDATE source_table_selection
        SET status = 'completed', last_synced = %s
        WHERE connection_id = %s AND LOWER("table") = LOWER(%s)
        """,
        [now.isoformat(), CONNECTION_ID, TABLE],
    )
    print(f"source_table_selection updated: {c.rowcount}")

    c.execute(
        """
        UPDATE destination_table_selection
        SET status = 'completed'
        WHERE connection_id = %s AND LOWER("table") = LOWER(%s)
        """,
        [CONNECTION_ID, TABLE],
    )
    print(f"destination_table_selection updated: {c.rowcount}")

print("\n=== After update ===")
with db_conn.cursor() as c:
    c.execute(
        """
        SELECT id, job_name, status FROM migration_record
        WHERE table_name = %s AND connection_id = %s
        ORDER BY updated_at DESC LIMIT 5
        """,
        [TABLE, CONNECTION_ID],
    )
    for row in c.fetchall():
        print("migration_record:", row)
    c.execute(
        """
        SELECT tbl_id, "table", status FROM source_table_selection
        WHERE connection_id = %s AND LOWER("table") = LOWER(%s)
        """,
        [CONNECTION_ID, TABLE],
    )
    for row in c.fetchall():
        print("source_table_selection:", row)

print("Done.")
