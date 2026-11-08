import re
from typing import List, Optional

from apps.elttool.models import MigrationRecord, Data_Transfer, Log

_ERROR_LOG_STATUSES = ('F', 'E')
_GENERIC_TABLES_FAILED_RE = re.compile(
    r"^Tables failed:\s*\[.*\]\s*$",
    re.IGNORECASE | re.DOTALL,
)


def is_generic_table_failure_message(message: str) -> bool:
    """True for aggregate/stub failure text that hides the root cause."""
    text = str(message or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if 'migration in progress' in lowered:
        return True
    if 'retrying:' in lowered and 'tables failed' in lowered:
        return True
    if text.count('Tables failed:') > 1:
        return True
    if _GENERIC_TABLES_FAILED_RE.match(text):
        return True
    return False


def pick_best_table_error_message(
    *,
    table_name: str,
    migration_record_id: Optional[int],
    job_name: Optional[str],
) -> Optional[str]:
    """Prefer actionable per-table errors over final aggregate job summaries."""
    table_name = str(table_name or '').strip()
    candidates: List[str] = []

    if migration_record_id is not None:
        for log in (
            Log.objects.filter(
                migration_record_id=migration_record_id,
                status__in=_ERROR_LOG_STATUSES,
            )
            .order_by('-log_id')[:40]
        ):
            msg = str(log.message or '').strip()
            if msg:
                candidates.append(msg)

    if job_name:
        for log in (
            Log.objects.filter(
                job_name=job_name,
                status__in=_ERROR_LOG_STATUSES,
                migration_record_id__isnull=True,
            )
            .order_by('-log_id')[:20]
        ):
            msg = str(log.message or '').strip()
            if msg:
                candidates.append(msg)

    for msg in candidates:
        if is_generic_table_failure_message(msg):
            continue
        if table_name and table_name.lower() in msg.lower():
            return msg
    for msg in candidates:
        if not is_generic_table_failure_message(msg):
            return msg
    return candidates[0] if candidates else None


def _extract_reason_from_log_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    marker = "Reason:"
    idx = text.find(marker)
    if idx >= 0:
        reason = text[idx + len(marker):].strip()
        return reason[:-1].strip() if reason.endswith(".") else reason
    low = text.lower()
    if "file(s) skipped due schema/type mismatch" in low:
        return text[:-1].strip() if text.endswith(".") else text
    if low.startswith("skipping file '"):
        return text[:-1].strip() if text.endswith(".") else text
    return ""


def build_migration_status(migration_session_id, connection_id=None):
    # -----------------------------
    # Fetch MigrationRecords by Session ID
    # -----------------------------
    migration_records = (
        MigrationRecord.objects
        .filter(migration_session_id=migration_session_id)
        .select_related(
            'connection',
            'connection__src_config',
            'connection__src_config__src',
            'connection__dst_config',
            'connection__dst_config__dst',
        )
    )

    if connection_id is not None:
        migration_records = migration_records.filter(connection_id=connection_id)

    if not migration_records.exists():
        return None, {
            'error': f'No migration records found for session_id: {migration_session_id}'
        }, 404

    # -----------------------------
    # Determine connection_id and job_name from first record
    # -----------------------------
    first_record = migration_records.first()
    resolved_connection_id = (
        first_record.connection.connection_id
        if first_record.connection else None
    )
    # If caller omitted connection_id but this session spans multiple connections,
    # refuse the ambiguous unscoped query (prevents mixing SF + Dynamics tables).
    if connection_id is None and resolved_connection_id is not None:
        other_conn_ids = (
            MigrationRecord.objects
            .filter(migration_session_id=migration_session_id)
            .exclude(connection_id=resolved_connection_id)
            .values_list('connection_id', flat=True)
            .distinct()
        )
        if other_conn_ids.exists():
            return None, {
                'error': (
                    f'Session {migration_session_id} spans multiple connections; '
                    f'pass connection_id to disambiguate'
                ),
                'connection_ids': list(other_conn_ids) + [resolved_connection_id],
            }, 400

    connection_id = resolved_connection_id
    job_name = first_record.job_name

    if not connection_id:
        return None, {
            'error': 'Unable to determine connection_id'
        }, 400

    # Job-level partial warning (status='P') used to surface skip warnings in migration_status API.
    partial_warning_message = None
    latest_job_terminal_status = None
    job_level_message = None
    if job_name:
        partial_logs = (
            Log.objects
            .filter(job_name=job_name, status='P', migration_record_id__isnull=True)
            .order_by('-log_id')[:30]
        )
        for p_log in partial_logs:
            extracted = _extract_reason_from_log_message(p_log.message)
            if extracted:
                partial_warning_message = extracted
                break
        latest_job_terminal_log = (
            Log.objects
            .filter(job_name=job_name, status__in=['S', 'P', 'F'], migration_record_id__isnull=True)
            .order_by('-log_id')
            .first()
        )
        latest_job_terminal_status = latest_job_terminal_log.status if latest_job_terminal_log else None
        if latest_job_terminal_log and latest_job_terminal_log.message:
            from apps.elttool.services.connection_migration_status import (
                resolve_job_level_display_message,
            )

            job_level_message = resolve_job_level_display_message(
                latest_job_terminal_log.message,
                connection_id=connection_id,
                session=migration_session_id,
                log_id=latest_job_terminal_log.log_id,
            )
        if not job_level_message:
            in_progress_job_log = (
                Log.objects.filter(
                    job_name=job_name,
                    migration_record_id__isnull=True,
                    status='I',
                )
                .order_by('-log_id')
                .first()
            )
            if in_progress_job_log and in_progress_job_log.message:
                lowered = in_progress_job_log.message.lower()
                if (
                    'retrying' in lowered
                    or 'failed' in lowered
                    or 'error' in lowered
                    or 'timeout' in lowered
                ) and 'migration in progress' not in lowered:
                    job_level_message = in_progress_job_log.message

    if not job_level_message and connection_id and migration_session_id:
        session_terminal_log = (
            Log.objects.filter(
                connection_id=connection_id,
                migration_record_id__isnull=True,
                session=migration_session_id,
                status__in=['S', 'P', 'F'],
            )
            .exclude(message__icontains='in progress')
            .order_by('-log_id')
            .first()
        )
        if session_terminal_log and session_terminal_log.message:
            from apps.elttool.services.connection_migration_status import (
                resolve_job_level_display_message,
            )

            job_level_message = resolve_job_level_display_message(
                session_terminal_log.message,
                connection_id=connection_id,
                session=migration_session_id,
                log_id=session_terminal_log.log_id,
            )
            if not latest_job_terminal_status:
                latest_job_terminal_status = session_terminal_log.status

    is_amazon_s3 = False
    s3_multi_single_mode = False
    try:
        src_name = (
            first_record.connection.src_config.src.name
            if first_record.connection and first_record.connection.src_config and first_record.connection.src_config.src
            else None
        )
        is_amazon_s3 = str(src_name or "").strip().lower() == "amazons3"
        src_data = first_record.connection.src_config.data if first_record.connection and first_record.connection.src_config else {}
        file_mapping_mode = None
        if isinstance(src_data, dict):
            file_mapping_mode = src_data.get("file_mapping_mode") or src_data.get("file_mapping_method")
        s3_multi_single_mode = str(file_mapping_mode or "").strip() == "multi_files_single_table"
    except Exception:
        pass

    is_databricks_destination = False
    try:
        dst_name = (
            first_record.connection.dst_config.dst.name
            if first_record.connection
            and first_record.connection.dst_config
            and first_record.connection.dst_config.dst
            else None
        )
        is_databricks_destination = 'databricks' in str(dst_name or '').strip().lower()
    except Exception:
        pass

    is_listing_only_session = (
        'listing_tables' in str(job_name or '')
        and not migration_records.exclude(table_name__isnull=True).exclude(table_name='').exists()
    )
    listing_job_record = None
    if is_listing_only_session:
        listing_job_record = migration_records.filter(table_name__isnull=True).first()
    # Per-chunk Databricks merge: staging_records_count = cumulative rows in base (not staging).
    progress_count_source = 'base_merged' if is_databricks_destination else 'staging'
    tables_data = []
    all_completed = True
    any_failed = False
    any_deleted = False
    any_open = False
    table_warning_messages = []

    for record in migration_records:
        if record.table_name is None:
            continue

        latest_transfer = (
            Data_Transfer.objects
            .filter(migration_record=record)
            .order_by('-end_time')
            .first()
        )

        normalized_status = (record.status or '').lower()

        error_message = None
        if normalized_status == 'failed':
            error_message = pick_best_table_error_message(
                table_name=record.table_name or '',
                migration_record_id=record.id,
                job_name=job_name,
            )
        if not error_message and normalized_status == 'in_progress':
            retry_progress_log = (
                Log.objects.filter(migration_record=record, status='I')
                .exclude(message__icontains='migration in progress')
                .order_by('-log_id')
                .first()
            )
            if retry_progress_log and retry_progress_log.message:
                error_message = retry_progress_log.message
        if normalized_status == 'failed' and not error_message and job_level_message:
            lowered = job_level_message.lower()
            if 'migration in progress' not in lowered:
                error_message = job_level_message
        if normalized_status == 'skipped' and not error_message:
            skip_log = (
                Log.objects
                .filter(migration_record=record)
                .order_by('-log_id')
                .first()
            )
            error_message = skip_log.message if skip_log else None
        # MigrationRecord status is authoritative — transient error logs during Celery
        # retry must not flip an in-progress table (or the whole run) to failed.
        effective_failed = normalized_status == 'failed'

        if effective_failed:
            status_icon = 'failed'
            any_failed = True
            all_completed = False
        elif normalized_status == 'skipped':
            status_icon = 'skipped'
        elif normalized_status == 'deleted':
            status_icon = 'deleted'
            any_deleted = True
            all_completed = False
        elif normalized_status == 'completed':
            status_icon = 'completed'
        else:
            status_icon = 'in_progress'
            any_open = True
            all_completed = False

        # S3 multi-file single-table: when job is partial (P) but table is completed,
        # surface skip warning to UI without changing table status.
        latest_partial_table_log = (
            Log.objects
            .filter(migration_record=record, status='P')
            .order_by('-log_id')
            .first()
        )
        table_partial_warning_message = (
            _extract_reason_from_log_message(latest_partial_table_log.message)
            if latest_partial_table_log else None
        )
        effective_warning_message = table_partial_warning_message or partial_warning_message
        is_partial_warning_case = (
            normalized_status == 'completed'
            and bool(effective_warning_message)
            and is_amazon_s3
            and s3_multi_single_mode
        )
        if is_partial_warning_case and not error_message:
            error_message = effective_warning_message
        if is_partial_warning_case and effective_warning_message:
            table_warning_messages.append(effective_warning_message)

        duration = None
        if (
            latest_transfer
            and latest_transfer.start_time
            and latest_transfer.end_time
        ):
            delta = latest_transfer.end_time - latest_transfer.start_time
            total = int(delta.total_seconds())
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            duration = (
                f"{h}h {m}m {s}s" if h else
                f"{m}m {s}s" if m else
                f"{s}s"
            )

        # Overview Records: cumulative merged rows on migration_record (per-chunk merge).
        staging_records_count = getattr(record, 'staging_records_count', None)
        staging_table_name = None
        last_folder_checkpoint = None
        table_status = 'failed' if effective_failed else (normalized_status or record.status)
        status_lower = str(table_status or '').strip().lower()
        table_completed = status_lower in (
            'completed', 'success', 's', 'succeeded',
        )

        transfer_total = (
            int(latest_transfer.total_rec)
            if latest_transfer and latest_transfer.total_rec is not None
            else None
        )
        
        tables_data.append({
            'table_name': record.table_name,
            'status': table_status,
            'status_icon': 'warning' if is_partial_warning_case else status_icon,
            'start_time': record.start_time.isoformat() if record.start_time else None,  # Use MigrationRecord.start_time,
            'end_time': (
                latest_transfer.end_time.isoformat()
                if latest_transfer and latest_transfer.end_time else None
            ),
            'record_count': transfer_total if transfer_total is not None else 0,
            'new_rec': latest_transfer.new_rec if latest_transfer else 0,
            'mod_rec': latest_transfer.mod_rec if latest_transfer else 0,
            'del_rec': latest_transfer.del_rec if latest_transfer else 0,
            'duration': duration,
            'migration_record_id': record.id,
            'error_message': error_message,
            'message': error_message,
            'staging_records_count': staging_records_count,
            'staging_table_name': staging_table_name,
            'last_folder_checkpoint': last_folder_checkpoint,
            'has_warning': bool(is_partial_warning_case),
            'warning_message': effective_warning_message if is_partial_warning_case else None,
        })

    # Sort tables by start_time (ascending). None start_time goes last.
    tables_data.sort(
        key=lambda item: (item.get('start_time') is None, item.get('start_time'))
    )

    # -----------------------------
    # Overall status
    # -----------------------------
    if is_listing_only_session and listing_job_record:
        listing_status = (listing_job_record.status or '').lower()
        if listing_status in ('in_progress', 'i'):
            overall_status = 'in_progress'
            all_completed = False
        elif listing_status in ('failed', 'f', 'error'):
            overall_status = 'failed'
            any_failed = True
            all_completed = False
        elif listing_status in ('completed', 'success', 's'):
            overall_status = 'completed'
        elif listing_status == 'deleted':
            overall_status = 'deleted'
            any_deleted = True
            all_completed = False
        else:
            overall_status = 'in_progress'
            any_open = True
            all_completed = False

        if not job_level_message:
            in_progress_log = (
                Log.objects.filter(
                    job_name=job_name,
                    migration_record_id__isnull=True,
                    status='I',
                )
                .order_by('-log_id')
                .first()
            )
            if in_progress_log and in_progress_log.message:
                job_level_message = in_progress_log.message
    else:
        any_skipped = any(
            str(item.get('status') or '').lower() == 'skipped'
            for item in tables_data
        )
        overall_status = (
            'in_progress' if any_open else
            'failed' if any_failed else
            'deleted' if any_deleted else
            'completed' if all_completed else
            'in_progress'
        )
        overall_warning = (
            overall_status == 'completed'
            and (
                any_skipped
                or (
                    bool(table_warning_messages or partial_warning_message)
                    and is_amazon_s3
                    and s3_multi_single_mode
                )
            )
        )
        skipped_table_names = sorted({
            item.get('table_name')
            for item in tables_data
            if str(item.get('status') or '').lower() == 'skipped'
            and item.get('table_name')
        })
        overall_warning_message = (
            f"{len(skipped_table_names)} table(s) skipped — refresh/reload in progress: "
            f"{', '.join(skipped_table_names)}"
            if any_skipped
            else table_warning_messages[0]
            if table_warning_messages
            else partial_warning_message
        )
        # Job-level logs can be stale (e.g. early crash then table retry succeeds).
        # Table/MigrationRecord status is authoritative once every table succeeded.
        if not any_open and all_completed and not any_failed:
            overall_status = 'completed'
        elif not any_open and (not all_completed or any_failed):
            if latest_job_terminal_status == 'F':
                overall_status = 'failed'
            elif latest_job_terminal_status == 'P' and not (
                bool(table_warning_messages or partial_warning_message)
                and is_amazon_s3
                and s3_multi_single_mode
            ):
                overall_status = 'failed'
        elif latest_job_terminal_status == 'S' and not any_open and not any_failed:
            overall_status = 'completed'

        return {
            'job_name': job_name,
            'migration_session_id': migration_session_id,
            'connection_id': connection_id,
            'overall_status': overall_status,
            'overall_status_icon': 'warning' if overall_warning else overall_status,
            'overall_has_warning': bool(overall_warning),
            'overall_warning_message': overall_warning_message if overall_warning else None,
            'job_level_message': job_level_message,
            'progress_count_source': progress_count_source,
            'tables': tables_data,
            'total_tables': len(tables_data)
        }, None, 200

    any_skipped = False
    overall_warning = False
    overall_warning_message = None

    return {
        'job_name': job_name,
        'migration_session_id': migration_session_id,
        'connection_id': connection_id,
        'overall_status': overall_status,
        'overall_status_icon': 'warning' if overall_warning else overall_status,
        'overall_has_warning': bool(overall_warning),
        'overall_warning_message': overall_warning_message if overall_warning else None,
        'job_level_message': job_level_message,
        'progress_count_source': progress_count_source,
        'tables': tables_data,
        'total_tables': len(tables_data)
    }, None, 200
