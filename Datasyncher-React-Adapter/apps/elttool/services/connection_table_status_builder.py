import logging
from datetime import timedelta, datetime
from django.db.models import Q, Max
from django.utils import timezone

from apps.elttool.models import (
    Connection,
    SourceTableSelection,
    MigrationRecord,
    ScheduledJob,
    Log,
)

logger = logging.getLogger(__name__)


def _job_level_migration_status_for_connection(connection_id: int):
    """
    Same rules as ConnectionListAPIView migration_statuses / job_level_error_messages
    so the connection_table_status WebSocket can drive the overview "syncing" badge
    without a separate refetch of /api/v1/home/.
    """
    latest_log_id = (
        Log.objects.filter(
            connection_id=connection_id,
            migration_record__isnull=True,
            job_name__isnull=False,
        ).aggregate(mid=Max("log_id"))["mid"]
    )

    if MigrationRecord.objects.filter(
        connection_id=connection_id,
        status="in_progress",
    ).exists():
        return "S", None

    if not latest_log_id:
        return None, None

    row = Log.objects.filter(log_id=latest_log_id).only("status", "message").first()
    if not row:
        return None, None

    if row.status == "I":
        return "S", None
    if row.status in ("E", "P", "F"):
        return "E", row.message
    if row.status == "S":
        return "C", None
    return None, None


def build_connection_table_status(connection_id, table_name=None):
    """
    Builds table-level status response for a connection.
    Extends ConnectionTableStatusAPIView with migration_status / error_message
    (parity with ConnectionSerializer + ConnectionListAPIView) for real-time UI.
    """

    try:
        # Optimize query with select_related to avoid N+1 queries
        connection = Connection.objects.select_related(
            'src_config',
            'dst_config'
        ).get(connection_id=connection_id)
    except Connection.DoesNotExist:
        return None, {
            "body": {"message": "Connection with the given ID does not exist"},
            "status": 404,
        }

    # Include selected tables OR tables in progress (parity with Django view)
    table_query = SourceTableSelection.objects.filter(
        Q(connection=connection, selected=True) |
        Q(connection=connection, status='in_progress')
    ).distinct()

    if table_name:
        table_query = table_query.filter(table=table_name)

    table_statuses = [
        {
            'table_name': table.table,
            'status': table.status or ''
        }
        for table in table_query
    ]

    # Schema refresh detection — only listing_tables jobs (not table migrations)
    schema_refresh_in_progress = MigrationRecord.objects.filter(
        connection=connection,
        table_name__isnull=True,
        job_name__contains='listing_tables',
        status='in_progress'
    ).exists()

    # Calculate next sync time (parity with Django view)
    scheduled_jobs = ScheduledJob.objects.filter(
        connection=connection
    ).select_related('connection').order_by('-updated_at')

    if scheduled_jobs.exists():
        recent_job = scheduled_jobs.first()
        last_synced = recent_job.updated_at if recent_job else None
    else:
        recent_job = None
        last_synced = timezone.now()

    sync_frequency_minutes = None
    if recent_job and recent_job.frequency_minutes:
        sync_frequency_minutes = recent_job.frequency_minutes
        logger.info(
            "[next_sync] (table_status) connection_id=%s job_id=%s job.frequency_minutes=%s",
            connection_id,
            recent_job.id,
            sync_frequency_minutes,
        )
    else:
        sync_frequency_minutes = connection.time_frequency
        logger.info(
            "[next_sync] (table_status) connection_id=%s using connection.time_frequency=%s",
            connection_id,
            sync_frequency_minutes,
        )

    next_sync_time = None
    if (
        sync_frequency_minutes is not None
        and sync_frequency_minutes != "None"
        and connection.status == "A"
    ):
        try:
            if isinstance(last_synced, str):
                last_synced = datetime.strptime(
                    last_synced,
                    '%Y-%m-%d %H:%M:%S.%f+00'
                )

            next_sync_time = last_synced + timedelta(
                minutes=int(sync_frequency_minutes)
            )
            logger.info(
                "[next_sync] (table_status) connection_id=%s last_synced=%s next_sync_time=%s",
                connection_id,
                last_synced,
                next_sync_time,
            )
            readable_time_frequency = next_sync_time.strftime(
                '%Y-%m-%d %H:%M:%SZ'
            )
        except Exception:
            readable_time_frequency = "None"
    else:
        readable_time_frequency = "None"

    formatted_next_sync_time = None
    if next_sync_time:
        utc_tz = timezone.get_fixed_timezone(0)
        if timezone.is_naive(next_sync_time):
            next_sync_time = timezone.make_aware(next_sync_time, timezone=utc_tz)
        formatted_next_sync_time = (
            next_sync_time
            .astimezone(utc_tz)
            .isoformat(timespec='microseconds')
            .replace('+00:00', 'Z')
        )

    migration_status, job_level_error_message = _job_level_migration_status_for_connection(
        connection_id
    )

    return {
        'table_statuses': table_statuses,
        'readable_time_frequency': readable_time_frequency,
        'next_sync_time': formatted_next_sync_time,
        'schema_refresh_in_progress': schema_refresh_in_progress,
        'migration_status': migration_status,
        'error_message': job_level_error_message,
        'retrieval_progress': connection.retrieval_progress,
    }, None
