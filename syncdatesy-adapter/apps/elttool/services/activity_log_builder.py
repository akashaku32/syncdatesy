"""Serialize connection activity logs for REST + WebSocket payloads."""

from __future__ import annotations

import re
from typing import Any, Optional

from apps.elttool.models import BatchTable, Log, MigrationRecord, PipelineNode

_PIPELINE_JOB_RE = re.compile(
    r"^pipeline_run_(?P<run_id>\d+)_node_(?P<node_id>\d+)",
    re.IGNORECASE,
)


def extract_migration_id_from_log(log: Log) -> Optional[int]:
    """
    Extract migration_session_id from a log entry for UI click navigation.
    """
    connection_id = getattr(log, "connection_id", None)
    job_name = getattr(log, "job_name", None)
    migration_record_id = getattr(log, "migration_record_id", None)
    if migration_record_id and getattr(log, "migration_record", None):
        return log.migration_record.migration_session_id

    if job_name:
        try:
            job_name_candidates = [job_name]
            normalized_job_name = job_name.split("->", 1)[0].strip()
            if normalized_job_name and normalized_job_name not in job_name_candidates:
                job_name_candidates.append(normalized_job_name)

            migration_record = (
                MigrationRecord.objects.filter(
                    connection_id=connection_id,
                    job_name__in=job_name_candidates,
                )
                .order_by("-id")
                .first()
            )
            if migration_record:
                return migration_record.migration_session_id
        except Exception:
            pass

        try:
            session_match = re.search(
                r"_(manual|scheduled|auto|delta|reload|full|incremental|listing_tables)_(\d+)(?:_|$)",
                job_name,
                flags=re.IGNORECASE,
            )
            if session_match:
                parsed_session_id = int(session_match.group(2))
                exists = MigrationRecord.objects.filter(
                    connection_id=connection_id,
                    migration_session_id=parsed_session_id,
                ).exists()
                if exists:
                    return parsed_session_id
        except Exception:
            pass
    return None


def resolve_activity_log_batch_context(log: Log) -> dict[str, Any]:
    """Resolve batch / pipeline labels for a high-level activity log row."""
    job_name = (getattr(log, "job_name", None) or "").strip()
    connection_id = getattr(log, "connection_id", None)
    if not job_name:
        return {}

    pipeline_ctx = _pipeline_context_from_job_name(job_name)
    if pipeline_ctx.get("pipeline_name") or pipeline_ctx.get("batch_name"):
        return pipeline_ctx

    if not connection_id:
        return pipeline_ctx

    migration_session_id = None
    session_match = re.search(
        r"_(manual|scheduled|auto|delta|reload|full|incremental|listing_tables)_(\d+)(?:_|$)",
        job_name,
        flags=re.IGNORECASE,
    )
    if session_match:
        migration_session_id = int(session_match.group(2))

    if migration_session_id is None and getattr(log, "session", None):
        try:
            migration_session_id = int(log.session)
        except (TypeError, ValueError):
            migration_session_id = None

    if migration_session_id is None:
        return pipeline_ctx

    # Summary logs often drop the pipeline_run_* prefix; recover it from MigrationRecord.
    record_job_name = (
        MigrationRecord.objects.filter(
            connection_id=connection_id,
            migration_session_id=migration_session_id,
        )
        .exclude(job_name__isnull=True)
        .exclude(job_name="")
        .order_by("-id")
        .values_list("job_name", flat=True)
        .first()
    )
    if record_job_name:
        from_record = _pipeline_context_from_job_name(record_job_name)
        if from_record.get("pipeline_name") or from_record.get("batch_name"):
            return from_record

    table_names = list(
        MigrationRecord.objects.filter(
            connection_id=connection_id,
            migration_session_id=migration_session_id,
        )
        .exclude(table_name__isnull=True)
        .exclude(table_name="")
        .values_list("table_name", flat=True)
        .distinct()
    )
    if not table_names:
        return pipeline_ctx

    batch_rows = (
        BatchTable.objects.filter(
            connection_id=connection_id,
            table_name__in=table_names,
        )
        .select_related("batch")
        .values_list("batch_id", "batch__name")
        .distinct()
    )
    batch_map = {batch_id: name for batch_id, name in batch_rows if batch_id}
    if not batch_map:
        return pipeline_ctx

    names = sorted({name for name in batch_map.values() if name})
    if len(names) == 1:
        batch_id = next(iter(batch_map))
        return {
            **pipeline_ctx,
            "batch_id": batch_id,
            "batch_name": names[0],
        }

    return {
        **pipeline_ctx,
        "batch_name": ", ".join(names),
        "batch_names": names,
    }


def _pipeline_context_from_job_name(job_name: str) -> dict[str, Any]:
    if not job_name:
        return {}
    pipeline_prefix = job_name.split("__", 1)[0].split("->", 1)[0].strip()
    pipeline_match = _PIPELINE_JOB_RE.match(pipeline_prefix)
    if not pipeline_match:
        return {}

    node_id = int(pipeline_match.group("node_id"))
    run_id = int(pipeline_match.group("run_id"))
    try:
        node = PipelineNode.objects.select_related("batch", "pipeline").get(
            id=node_id,
        )
        batch_name = (
            node.batch.name
            if node.batch_id
            else (node.node_label or f"Node {node.id}")
        )
        pipeline_name = node.pipeline.name if node.pipeline_id else None
        return {
            "batch_id": node.batch_id,
            "batch_name": batch_name,
            "pipeline_id": node.pipeline_id,
            "pipeline_run_id": run_id,
            "pipeline_name": pipeline_name,
        }
    except PipelineNode.DoesNotExist:
        return {
            "pipeline_run_id": run_id,
            "pipeline_node_id": node_id,
        }


def _pipeline_run_id_from_job_name(job_name: str) -> Optional[int]:
    if not job_name:
        return None
    candidates = [job_name.strip()]
    if "__" in job_name:
        candidates.append(job_name.split("__", 1)[0].strip())
    for candidate in candidates:
        match = _PIPELINE_JOB_RE.match(candidate.split("->", 1)[0].strip())
        if match:
            return int(match.group("run_id"))
    return None


def _pipeline_run_id_for_log(log: Log) -> Optional[int]:
    job_name = getattr(log, "job_name", None) or ""
    run_id = _pipeline_run_id_from_job_name(job_name)
    if run_id is not None:
        return run_id

    session_id = getattr(log, "session", None)
    connection_id = getattr(log, "connection_id", None)
    if not session_id or not connection_id:
        return None

    record_job_name = (
        MigrationRecord.objects.filter(
            connection_id=connection_id,
            migration_session_id=session_id,
        )
        .exclude(job_name__isnull=True)
        .exclude(job_name="")
        .order_by("-id")
        .values_list("job_name", flat=True)
        .first()
    )
    if record_job_name:
        return _pipeline_run_id_from_job_name(record_job_name)
    return None


def _trigger_type_for_pipeline_run(run_id: int) -> str:
    from apps.elttool.models import PipelineRun

    try:
        run = PipelineRun.objects.only("metadata").get(id=run_id)
    except PipelineRun.DoesNotExist:
        return "Manual"
    source = (run.metadata or {}).get("source")
    if source == "scheduler":
        return "Auto/Scheduled"
    return "Manual"


def _resolve_trigger_type(log: Log) -> str:
    message_lower = (log.message or "").lower()
    if log.message and "trigger:" in message_lower:
        trigger_match = re.search(r"\(trigger:\s*([^)]+)\)", message_lower)
        if trigger_match:
            return trigger_match.group(1).strip().title()

    job_name_lower = (getattr(log, "job_name", "") or "").lower()
    if "_auto_sync_" in job_name_lower:
        return "Auto/Scheduled"
    if "_scheduled_" in job_name_lower:
        return "Auto/Scheduled"

    pipeline_run_id = _pipeline_run_id_for_log(log)
    if pipeline_run_id is not None:
        return _trigger_type_for_pipeline_run(pipeline_run_id)

    return "Manual"


def _is_clickable_activity_log(log: Log, *, migration_session_id: Optional[int] = None) -> bool:
    """Whether the Overview log row can open Migration Details."""
    if migration_session_id is not None:
        return True
    if not log.message:
        return False
    msg_lower = log.message.lower()
    if any(
        phrase in msg_lower
        for phrase in (
            "migration in progress",
            "migration completed",
            "migration failed",
            "update schema in progress",
            "update schema completed",
            "update schema failed",
            "drop and reload initiated",
            "delta refresh initiated",
            "full initial load initiated",
            "table selection updated",
            "child group(s) failed",
        )
    ):
        return True
    status = (getattr(log, "status", None) or "").upper()
    return status in ("S", "F", "E", "P") and bool(getattr(log, "session", None))


def serialize_connection_activity_log(
    log: Log,
    *,
    migration_id: Optional[int] = None,
    timestamp_format: str = "iso",
) -> dict[str, Any]:
    """Build one activity-log dict for API / WebSocket consumers."""
    resolved_migration_id = migration_id
    if resolved_migration_id is None:
        resolved_migration_id = extract_migration_id_from_log(log)
    if resolved_migration_id is None and getattr(log, "session", None):
        try:
            resolved_migration_id = int(log.session)
        except (TypeError, ValueError):
            pass
    is_clickable = _is_clickable_activity_log(
        log,
        migration_session_id=resolved_migration_id,
    )

    user_name = log.user.first_name if log.user else "Unknown"
    if timestamp_format == "iso":
        timestamp = log.time.isoformat() if log.time else None
    else:
        timestamp = log.time

    connection_id = getattr(log, "connection_id", None)
    display_message = log.message
    job_name = getattr(log, "job_name", None) or ""
    # Transform IDE audits are job-level Logs but must keep their success text.
    skip_migration_rewrite = str(job_name).lower().startswith("transform_") or (
        (log.message or "").strip().lower().startswith("entity transform ")
    )
    if (
        connection_id is not None
        and getattr(log, "migration_record_id", None) is None
        and not skip_migration_rewrite
    ):
        from apps.elttool.services.connection_migration_status import (
            resolve_job_level_display_message,
        )

        display_message = resolve_job_level_display_message(
            log.message,
            connection_id=connection_id,
            session=getattr(log, "session", None),
            log_id=getattr(log, "log_id", None),
        )

    payload: dict[str, Any] = {
        "log_id": log.log_id,
        "message": display_message,
        "user_name": user_name,
        "timestamp": timestamp,
        "status": log.status,
        "session_id": log.session,
        "is_clickable": is_clickable,
        "migration_id": resolved_migration_id,
        "log_type": "connection_activity",
        "trigger_type": _resolve_trigger_type(log),
    }
    payload.update(resolve_activity_log_batch_context(log))
    return payload
