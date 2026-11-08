"""
Apply SAP proxy agent migration progress events to Django ORM.

HTTPS (v1) and phase-2 gRPC ReportProgress share this handler after gateway/hub
forward with X-INTERNAL-KEY + X-Agent-UID.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone as dt_timezone
from typing import Any, Optional

from django.core.cache import cache
from django.db import transaction
from django.db.models import Max, Min, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.elttool.models import (
    Connection,
    Data_Transfer,
    Log,
    MigrationRecord,
    ProxyAgent,
    SourceTableSelection,
    User,
)

logger = logging.getLogger(__name__)

TABLE_EVENTS = frozenset(
    {
        "table_started",
        "table_progress",
        "table_completed",
        "table_failed",
    }
)
ALLOWED_EVENTS = TABLE_EVENTS | frozenset({"activity_log", "batch_completed"})
_ACTIVE = ("in_progress",)
_TERMINAL = ("completed", "failed", "skipped", "deleted")
_HMAC_SKEW_SECONDS = 300
_IDEMPOTENCY_TTL = 86400
_IDEMPOTENCY_PREFIX = "proxy_agent_progress:idem:"


class ProgressAuthError(Exception):
    """HMAC / agent identity failure."""


class ProgressValidationError(Exception):
    """Bad payload or tenant/job binding failure."""


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = parse_datetime(str(value).strip())
        if dt is None:
            return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt


def hash_progress_signing_secret(raw_secret: str) -> str:
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


def verify_agent_progress_signature(
    agent: ProxyAgent,
    *,
    timestamp_header: str,
    signature_header: str,
    raw_body: bytes,
    raw_secret: Optional[str] = None,
) -> None:
    """
    Verify request HMAC.

    Prefer decrypting encrypted secret from agent.progress_signing_secret when set;
    otherwise ``raw_secret`` may be supplied by tests. Fail closed if neither works.
    """
    from apps.utils.encryption import decrypt_sensitive

    ts = (timestamp_header or "").strip()
    sig = (signature_header or "").strip()
    if not ts or not sig:
        raise ProgressAuthError("Missing X-Agent-Timestamp or X-Agent-Signature.")
    try:
        ts_int = int(ts)
    except ValueError as exc:
        raise ProgressAuthError("Invalid X-Agent-Timestamp.") from exc
    now = int(time.time())
    if abs(now - ts_int) > _HMAC_SKEW_SECONDS:
        raise ProgressAuthError("X-Agent-Timestamp outside allowed skew.")

    secret = raw_secret
    encrypted = getattr(agent, "progress_signing_secret", None)
    if not secret and encrypted:
        try:
            secret = decrypt_sensitive(encrypted, agent.cmp_id)
        except Exception as exc:
            logger.warning(
                "proxy_agent_progress: decrypt signing secret failed agent_uid=%s",
                agent.agent_uid,
            )
            raise ProgressAuthError("Invalid agent signing material.") from exc
    if not secret:
        raise ProgressAuthError("Agent progress signing secret is not configured.")

    if agent.progress_signing_secret_hash:
        expected_hash = hash_progress_signing_secret(str(secret))
        if not hmac.compare_digest(expected_hash, str(agent.progress_signing_secret_hash)):
            raise ProgressAuthError("Signing secret fingerprint mismatch.")

    message = f"{ts}.".encode("utf-8") + raw_body
    expected_sig = hmac.new(
        str(secret).encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig.lower()):
        raise ProgressAuthError("Invalid X-Agent-Signature.")


def _scoped_records(migration_session_id: int, job_name: str, connection_id: int):
    return MigrationRecord.objects.filter(
        migration_session_id=migration_session_id,
        job_name=job_name,
        connection_id=connection_id,
    )


def _overall_status(qs) -> tuple[str, int]:
    statuses = list(qs.values_list("status", flat=True))
    if not statuses:
        return "failed", 0
    remaining = sum(1 for s in statuses if str(s).lower() in _ACTIVE)
    if remaining:
        return "in_progress", remaining
    if any(str(s).lower() == "failed" for s in statuses):
        if all(str(s).lower() == "failed" for s in statuses):
            return "failed", 0
        return "failed", 0
    return "completed", 0


def _trigger_ws(migration_session_id: int, connection_id: int, *, activity: bool = False) -> None:
    try:
        from apps.elttool.services.migration_status_builder import build_migration_status
        from apps.elttool.websocket.ws_utils import (
            push_connection_activity_log_update,
            push_migration_status_update,
            trigger_connection_activity_log_update,
            trigger_connection_ws_status_update,
        )

        data, error, _ = build_migration_status(
            migration_session_id,
            connection_id=connection_id,
        )
        if data and not error:
            push_migration_status_update(migration_session_id, data)
        trigger_connection_ws_status_update(connection_id)
        if activity:
            trigger_connection_activity_log_update(connection_id)
    except Exception:
        logger.exception(
            "proxy_agent_progress: WS push failed session=%s connection=%s",
            migration_session_id,
            connection_id,
        )


def _update_table_status(
    *,
    connection_id: int,
    table_name: str,
    status: str,
    job_name: str,
    migration_session_id: int,
    sync_end_time: Optional[datetime] = None,
    start_time: Optional[datetime] = None,
    staging_records_count: Optional[int] = None,
) -> MigrationRecord:
    record = MigrationRecord.objects.filter(
        migration_session_id=migration_session_id,
        job_name=job_name,
        connection_id=connection_id,
        table_name=table_name,
    ).first()
    if record is None:
        raise ProgressValidationError(
            f"No migration_record for table={table_name!r} job={job_name!r}."
        )

    current = (record.status or "").lower()
    if current in ("completed", "failed", "skipped", "deleted") and status == "in_progress":
        # Idempotent: do not reopen terminal rows.
        return record

    record.status = status
    if status == "in_progress" and start_time and not record.start_time:
        record.start_time = start_time
    elif status == "in_progress" and not record.start_time:
        record.start_time = timezone.now()
    if staging_records_count is not None:
        record.staging_records_count = max(
            int(record.staging_records_count or 0),
            int(staging_records_count),
        )
    record.save(
        update_fields=[
            "status",
            "start_time",
            "staging_records_count",
            "updated_at",
        ]
    )

    sts = SourceTableSelection.objects.filter(
        connection_id=connection_id,
        table=table_name,
    ).first()
    if sts is not None:
        sts.status = status
        update_fields = ["status"]
        if sync_end_time is not None:
            # SourceTableSelection.last_synced is a CharField in this schema.
            sts.last_synced = sync_end_time.isoformat()
            update_fields.append("last_synced")
        sts.save(update_fields=update_fields)
    return record


def _apply_etl_fields(connection_id: int, table_name: str, payload: dict) -> None:
    sts = SourceTableSelection.objects.filter(
        connection_id=connection_id,
        table=table_name,
    ).first()
    if sts is None:
        return
    update_fields = []
    if "delta_token" in payload and payload.get("delta_token") is not None:
        sts.delta_token = payload.get("delta_token")
        update_fields.append("delta_token")
    if "initial_completed_flag" in payload and payload.get("initial_completed_flag") is not None:
        sts.initial_completed_flag = bool(payload.get("initial_completed_flag"))
        update_fields.append("initial_completed_flag")
    if "load_method_locked" in payload and payload.get("load_method_locked") is not None:
        sts.load_method_locked = bool(payload.get("load_method_locked"))
        update_fields.append("load_method_locked")
    if "load_method" in payload and payload.get("load_method"):
        sts.load_method = str(payload.get("load_method"))
        update_fields.append("load_method")
    if payload.get("initial_completed_flag"):
        if not sts.first_sync_timestamp:
            sts.first_sync_timestamp = timezone.now()
            update_fields.append("first_sync_timestamp")
        sts.last_delta_run_timestamp = timezone.now()
        update_fields.append("last_delta_run_timestamp")
    if update_fields:
        sts.save(update_fields=list(dict.fromkeys(update_fields)))


def _resolve_user(connection: Connection, payload: dict) -> User:
    user_id = payload.get("user_id")
    if user_id:
        user = User.objects.filter(pk=user_id).first()
        if user:
            return user
    if connection.created_by_id:
        user = User.objects.filter(pk=connection.created_by_id).first()
        if user:
            return user
    user = User.objects.filter(cmp_id=connection.cmp_id).order_by("user_id").first()
    if user is None:
        raise ProgressValidationError("Unable to resolve user for activity log.")
    return user


def _write_table_log(
    *,
    connection: Connection,
    user: User,
    migration_session_id: int,
    job_name: str,
    record: MigrationRecord,
    message: str,
    status: str,
) -> None:
    Log.objects.create(
        message=message,
        cmp_id=connection.cmp_id,
        connection=connection,
        user=user,
        status=status,
        session=migration_session_id,
        job_name=job_name,
        migration_record=record,
    )


def build_job_completion_message(
    *,
    execution_time: float,
    total_rows: int,
    successful_tables: int,
    failed_tables: int,
    total_tables: int,
) -> tuple[str, str]:
    """Mirror migration-function log_migration_completion wording (S/P/F)."""
    final_failed = failed_tables
    final_successful = successful_tables
    final_total = total_tables or (final_successful + final_failed)
    if final_total == 0 or final_failed == 0:
        base = "Migration completed successfully"
        status = "S"
    elif final_failed == final_total:
        base = "Migration failed"
        status = "F"
    else:
        base = "Migration completed"
        status = "P"
    table_info = f"Tables: {final_successful}/{final_total} successful"
    if final_failed > 0 and final_failed == final_total:
        table_info += " (all tables failed)"
    elif final_failed > 0:
        table_info += f", {final_failed} failed"
    sentences = [
        f"{base} in {execution_time:.2f} seconds.",
        f"Total rows: {total_rows:,}.",
        table_info + ".",
    ]
    return " ".join(sentences), status


def maybe_finalize_job(
    *,
    connection: Connection,
    user: User,
    migration_session_id: int,
    job_name: str,
    log_id: Optional[int] = None,
    summary: Optional[dict] = None,
) -> Optional[str]:
    """
    When no scoped migration_record rows remain in_progress, flip job-level Log I→S/F/P.
    Returns overall_status or None if still running.
    """
    qs = _scoped_records(migration_session_id, job_name, connection.connection_id)
    if qs.filter(status__in=_ACTIVE).exists():
        return None

    statuses = list(qs.values_list("status", flat=True))
    successful = sum(1 for s in statuses if str(s).lower() in ("completed", "skipped"))
    failed = sum(1 for s in statuses if str(s).lower() == "failed")
    total = len(statuses)

    summary = summary or {}
    total_rows = int(summary.get("total_rows") or 0)
    if total_rows <= 0:
        agg = Data_Transfer.objects.filter(
            migration_record__migration_session_id=migration_session_id,
            migration_record__job_name=job_name,
            connection_id=connection.connection_id,
        ).aggregate(total=Sum("total_rec"))
        total_rows = int(agg.get("total") or 0)

    execution_time = float(summary.get("execution_time") or 0.0)
    if execution_time <= 0:
        times = qs.aggregate(start=Min("start_time"), end=Max("updated_at"))
        if times.get("start") and times.get("end"):
            execution_time = max(
                0.0,
                (times["end"] - times["start"]).total_seconds(),
            )

    message, log_status = build_job_completion_message(
        execution_time=execution_time,
        total_rows=total_rows,
        successful_tables=successful,
        failed_tables=failed,
        total_tables=total,
    )

    now = timezone.now()
    updated = 0
    if log_id:
        updated = Log.objects.filter(
            log_id=log_id,
            migration_record_id__isnull=True,
        ).update(status=log_status, message=message, time=now)
    if not updated:
        updated = Log.objects.filter(
            connection_id=connection.connection_id,
            job_name=job_name,
            migration_record_id__isnull=True,
            status="I",
        ).update(status=log_status, message=message, time=now)
    if not updated:
        Log.objects.filter(
            connection_id=connection.connection_id,
            session=migration_session_id,
            migration_record_id__isnull=True,
            status="I",
        ).update(status=log_status, message=message, time=now)
    if not updated:
        Log.objects.create(
            message=message,
            cmp_id=connection.cmp_id,
            connection=connection,
            user=user,
            status=log_status,
            session=migration_session_id,
            job_name=job_name,
            migration_record=None,
        )

    overall, _ = _overall_status(qs)
    return overall


@transaction.atomic
def apply_progress_event(
    *,
    agent: ProxyAgent,
    body: dict[str, Any],
) -> dict[str, Any]:
    event = str(body.get("event") or "").strip()
    if event not in ALLOWED_EVENTS:
        raise ProgressValidationError(f"Unsupported event={event!r}.")

    try:
        migration_session_id = int(body.get("migration_session_id"))
        connection_id = int(body.get("connection_id"))
    except (TypeError, ValueError) as exc:
        raise ProgressValidationError(
            "migration_session_id and connection_id must be integers."
        ) from exc
    job_name = str(body.get("job_name") or "").strip()
    if not job_name:
        raise ProgressValidationError("job_name is required.")

    connection = (
        Connection.objects.select_related("cmp")
        .filter(pk=connection_id)
        .first()
    )
    if connection is None:
        raise ProgressValidationError("Connection not found.")
    if connection.cmp_id != agent.cmp_id:
        raise ProgressValidationError("Connection does not belong to this agent company.")

    scoped = _scoped_records(migration_session_id, job_name, connection_id)
    if not scoped.exists():
        raise ProgressValidationError(
            "No migration_record rows for session/job/connection."
        )

    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    # Flatten optional top-level fields into payload for convenience.
    for key in (
        "rows",
        "inserts",
        "columns",
        "remote_path",
        "sync_end_time",
        "start_time",
        "delta_token",
        "load_method",
        "initial_completed_flag",
        "load_method_locked",
        "error_message",
        "message",
        "status",
        "staging_records_count",
        "user_id",
        "cmp_id",
        "log_id",
        "total_rows",
        "execution_time",
        "successful_tables",
        "failed_tables",
        "total_tables",
    ):
        if key in body and key not in payload:
            payload[key] = body[key]

    user = _resolve_user(connection, payload)
    table_name = str(body.get("table_name") or payload.get("table_name") or "").strip()

    if event in TABLE_EVENTS and not table_name:
        raise ProgressValidationError("table_name is required for table events.")

    if event == "table_started":
        start_time = _parse_dt(payload.get("start_time")) or timezone.now()
        _update_table_status(
            connection_id=connection_id,
            table_name=table_name,
            status="in_progress",
            job_name=job_name,
            migration_session_id=migration_session_id,
            start_time=start_time,
        )
    elif event == "table_progress":
        count = payload.get("staging_records_count", payload.get("rows"))
        _update_table_status(
            connection_id=connection_id,
            table_name=table_name,
            status="in_progress",
            job_name=job_name,
            migration_session_id=migration_session_id,
            staging_records_count=int(count) if count is not None else None,
        )
    elif event == "table_completed":
        end_time = _parse_dt(payload.get("sync_end_time")) or timezone.now()
        start_time = _parse_dt(payload.get("start_time"))
        record = _update_table_status(
            connection_id=connection_id,
            table_name=table_name,
            status="completed",
            job_name=job_name,
            migration_session_id=migration_session_id,
            sync_end_time=end_time,
            start_time=start_time,
            staging_records_count=(
                int(payload["rows"])
                if payload.get("rows") is not None
                else (
                    int(payload["inserts"])
                    if payload.get("inserts") is not None
                    else None
                )
            ),
        )
        _apply_etl_fields(connection_id, table_name, payload)
        rows = int(payload.get("rows") or payload.get("inserts") or 0)
        Data_Transfer.objects.create(
            connection=connection,
            migration_record=record,
            table_name=table_name,
            start_time=start_time or record.start_time or end_time,
            end_time=end_time,
            new_rec=rows,
            total_rec=rows,
            mod_rec=0,
            del_rec=0,
        )
        _write_table_log(
            connection=connection,
            user=user,
            migration_session_id=migration_session_id,
            job_name=job_name,
            record=record,
            message=(
                f"Successfully migrated '{table_name}' via proxy agent "
                f"({rows} rows)."
            ),
            status="S",
        )
    elif event == "table_failed":
        record = _update_table_status(
            connection_id=connection_id,
            table_name=table_name,
            status="failed",
            job_name=job_name,
            migration_session_id=migration_session_id,
        )
        err = str(payload.get("error_message") or payload.get("message") or "Table failed")
        _write_table_log(
            connection=connection,
            user=user,
            migration_session_id=migration_session_id,
            job_name=job_name,
            record=record,
            message=f"Entity '{table_name}' migration failed: {err}",
            status="E",
        )
    elif event == "activity_log":
        status_code = str(payload.get("status") or "I").upper()[:1]
        if status_code not in ("S", "E", "I", "P"):
            status_code = "I"
        msg = str(payload.get("message") or payload.get("error_message") or "").strip()
        if not msg:
            raise ProgressValidationError("activity_log requires message.")
        Log.objects.create(
            message=msg,
            cmp_id=connection.cmp_id,
            connection=connection,
            user=user,
            status=status_code,
            session=migration_session_id,
            job_name=job_name,
            migration_record=None,
        )
    elif event == "batch_completed":
        maybe_finalize_job(
            connection=connection,
            user=user,
            migration_session_id=migration_session_id,
            job_name=job_name,
            log_id=int(payload["log_id"]) if payload.get("log_id") else None,
            summary=payload,
        )

    if event in ("table_completed", "table_failed"):
        maybe_finalize_job(
            connection=connection,
            user=user,
            migration_session_id=migration_session_id,
            job_name=job_name,
            log_id=int(payload["log_id"]) if payload.get("log_id") else None,
            summary=payload if event == "table_completed" else None,
        )

    agent.last_progress_at = timezone.now()
    agent.save(update_fields=["last_progress_at", "updated_at"])

    overall, remaining = _overall_status(scoped)
    activity = event in ("activity_log", "table_completed", "table_failed", "batch_completed")
    transaction.on_commit(
        lambda: _trigger_ws(
            migration_session_id,
            connection_id,
            activity=activity,
        )
    )
    return {
        "accepted": True,
        "overall_status": overall,
        "tables_remaining": remaining,
    }


def ingest_proxy_agent_progress(
    *,
    agent_uid: str,
    body: dict[str, Any],
    raw_body: bytes,
    timestamp_header: str,
    signature_header: str,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    agent = ProxyAgent.objects.filter(agent_uid=agent_uid).select_related("cmp").first()
    if agent is None:
        raise ProgressAuthError("Unknown agent.")

    verify_agent_progress_signature(
        agent,
        timestamp_header=timestamp_header,
        signature_header=signature_header,
        raw_body=raw_body,
    )

    idem = (idempotency_key or body.get("idempotency_key") or "").strip()
    if idem:
        cache_key = f"{_IDEMPOTENCY_PREFIX}{agent_uid}:{idem}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    result = apply_progress_event(agent=agent, body=body)
    if idem:
        cache.set(cache_key, result, timeout=_IDEMPOTENCY_TTL)
    return result
