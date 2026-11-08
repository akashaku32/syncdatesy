from apps.elttool.models import Connection, PipelineRun, MigrationRecord, Log
from apps.elttool.services.migration_status_builder import build_migration_status
from apps.elttool.services.activity_log_builder import serialize_connection_activity_logs

conn_id = 46
run_id = 2299

print("=== CONNECTION 46 (aws_2nd_flow) ===")
conn = Connection.objects.select_related("src_config", "dst_config").get(connection_id=conn_id)
print(
    f"src={conn.src_config.name} dst={conn.dst_config.name} status={conn.status}"
)

print("\n=== PIPELINE RUN 2299 ===")
run = PipelineRun.objects.select_related("pipeline").get(id=run_id)
print(
    f"pipeline={run.pipeline.name} status={run.status} "
    f"started={run.started_at} finished={getattr(run, 'finished_at', None)}"
)

print("\n=== JOB-LEVEL LOGS (last 15) ===")
logs = list(
    Log.objects.filter(connection_id=conn_id, migration_record_id__isnull=True)
    .order_by("-time")[:15]
)
for log in logs:
    msg = (log.message or "")[:140]
    print(
        f"log_id={log.log_id} status={log.status} time={log.time} "
        f"session={log.session} msg={msg}"
    )

print("\n=== RECONCILED ACTIVITY LOGS (stuck check) ===")
serialized = serialize_connection_activity_logs(logs[:10], persist_reconciliation=False)
for item in serialized[:10]:
    print(
        f"log_id={item['log_id']} status={item['status']} "
        f"msg={(item['message'] or '')[:140]}"
    )

print("\n=== IN-PROGRESS MIGRATION RECORDS ===")
open_mrs = MigrationRecord.objects.filter(
    connection_id=conn_id,
    status__in=["in_progress", "pending", "queued", "running"],
).order_by("-id")
print(f"count={open_mrs.count()}")
for record in open_mrs[:12]:
    print(
        f"  id={record.id} session={record.migration_session_id} "
        f"table={record.table_name} status={record.status} "
        f"job={str(record.job_name)[:70]}"
    )

print("\n=== RECENT SESSIONS ===")
sessions = (
    MigrationRecord.objects.filter(connection_id=conn_id)
    .values_list("migration_session_id", flat=True)
    .distinct()
    .order_by("-migration_session_id")[:10]
)
for session_id in sessions:
    records = (
        MigrationRecord.objects.filter(
            connection_id=conn_id,
            migration_session_id=session_id,
        )
        .exclude(table_name__isnull=True)
        .exclude(table_name="")
    )
    statuses = list(records.values_list("table_name", "status"))
    open_count = sum(
        1 for _, status in statuses if status in ("in_progress", "pending", "queued", "running")
    )
    print(
        f"session={session_id} tables={len(statuses)} open={open_count} "
        f"sample={statuses[:4]}"
    )
    if open_count == 0 and statuses:
        data, err, code = build_migration_status(session_id, connection_id=conn_id)
        if data:
            msg = (data.get("job_level_message") or "")[:180]
            print(f"  -> overall={data['overall_status']} msg={msg}")
