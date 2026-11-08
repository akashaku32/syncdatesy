"""Bulk connection migration status (Connectors list + table-status WebSocket)."""
from __future__ import annotations

from typing import Dict, Optional

from django.db.models import Max

import re

from apps.elttool.models import Log, MigrationRecord

_GENERIC_CHILD_FAILURE_RE = re.compile(
    r"^\d+\s+child group\(s\) failed\.?$",
    re.IGNORECASE,
)


def _extract_table_error_root_cause(message: Optional[str]) -> Optional[str]:
    text = (message or "").strip()
    if not text:
        return None
    if "processing the table" in text.lower() and ":" in text:
        tail = text.split(":", 1)[1].strip()
        return tail or text
    return text


def _is_job_level_summary_message(message: Optional[str]) -> bool:
    lower = (message or "").strip().lower()
    if not lower:
        return False
    return lower.startswith(
        (
            "migration failed",
            "migration completed",
            "migration in progress",
            "update schema",
            "drop and reload",
            "delta refresh",
            "full initial load",
            "table selection updated",
        )
    )


def _is_transform_audit_message(message: Optional[str]) -> bool:
    """IDE Validate/Publish audits are not migration outcomes."""
    lower = (message or "").strip().lower()
    return lower.startswith("entity transform ")


def _is_transform_audit_job_name(job_name: Optional[str]) -> bool:
    name = (job_name or "").strip().lower()
    return name.startswith("transform_")


def _looks_like_migration_failure_message(message: Optional[str]) -> bool:
    """Only failure-shaped job logs should be rewritten as Migration failed."""
    lower = (message or "").strip().lower()
    if not lower or _is_transform_audit_message(lower):
        return False
    if _is_job_level_summary_message(lower):
        return lower.startswith(
            ("migration failed", "update schema failed")
        ) or "failed" in lower
    if "processing the table" in lower:
        return True
    if _GENERIC_CHILD_FAILURE_RE.match((message or "").strip()):
        return True
    if lower in {"child task failed", "task revoked"}:
        return True
    if "error" in lower or "failed" in lower or "exception" in lower:
        return True
    return False


def _lookup_table_failure_root_cause(connection_id: int) -> Optional[str]:
    rows = (
        Log.objects.filter(
            connection_id=connection_id,
            migration_record_id__isnull=False,
            status__in=("F", "E"),
        )
        .exclude(message__isnull=True)
        .exclude(message="")
        .order_by("-log_id")
        .values_list("message", flat=True)[:25]
    )
    for row in rows:
        candidate = str(row).strip()
        if not candidate or _GENERIC_CHILD_FAILURE_RE.match(candidate):
            continue
        if candidate.lower() in {"child task failed", "task revoked"}:
            continue
        if _is_transform_audit_message(candidate):
            continue
        return _extract_table_error_root_cause(candidate)
    return None


def _find_session_job_summary_message(
    connection_id: int,
    session,
    *,
    exclude_log_id: Optional[int] = None,
) -> Optional[str]:
    if connection_id is None or session is None:
        return None
    for log in (
        Log.objects.filter(
            connection_id=connection_id,
            session=session,
            migration_record_id__isnull=True,
            job_name__isnull=False,
            status__in=("S", "P", "F"),
        )
        .order_by("-log_id")[:20]
    ):
        if exclude_log_id is not None and log.log_id == exclude_log_id:
            continue
        msg = (log.message or "").strip()
        if _is_job_level_summary_message(msg):
            return msg
    return None


def resolve_job_level_display_message(
    message: Optional[str],
    *,
    connection_id: Optional[int] = None,
    session=None,
    log_id: Optional[int] = None,
) -> str:
    """Activity log left panel: finalizer-style summary, not per-table errors."""
    raw = (message or "").strip()
    # Validate/Publish audits must never be rewritten as migration failures.
    if _is_transform_audit_message(raw):
        return raw
    if _is_job_level_summary_message(raw):
        return raw

    if connection_id is not None and session is not None:
        sibling = _find_session_job_summary_message(
            int(connection_id),
            session,
            exclude_log_id=log_id,
        )
        if sibling:
            root = _lookup_table_failure_root_cause(int(connection_id))
            if root and "reason:" in sibling.lower():
                stub_reasons = (
                    "child group",
                    "table group(s) failed during sync",
                    "child task failed",
                )
                reason_tail = sibling.split("Reason:", 1)[-1].strip().lower()
                if any(stub in reason_tail for stub in stub_reasons):
                    prefix = sibling.split("Reason:", 1)[0].rstrip()
                    return f"{prefix} Reason: {root}."
            return sibling

    # Success / audit / unrelated job-level rows keep their original text.
    if not _looks_like_migration_failure_message(raw):
        return raw

    root = None
    if connection_id is not None:
        root = _lookup_table_failure_root_cause(int(connection_id))
    if not root and raw:
        root = _extract_table_error_root_cause(raw)

    if root:
        return f"Migration failed. Reason: {root}"
    if _GENERIC_CHILD_FAILURE_RE.match(raw) or "processing the table" in raw.lower():
        return "Migration failed."
    return raw or "Migration failed."


def _resolve_connector_error_message(connection_id: int, message: Optional[str]) -> Optional[str]:
    """Connector list badge: short root cause, not per-table wrapper text."""
    text = (message or "").strip()
    if not text:
        return message
    if _is_transform_audit_message(text):
        return None
    if _is_job_level_summary_message(text):
        if "reason:" in text.lower():
            return text.split("Reason:", 1)[-1].strip() or text
        return text
    if _GENERIC_CHILD_FAILURE_RE.match(text) or "processing the table" in text.lower():
        root = _lookup_table_failure_root_cause(connection_id)
        return root or "Migration failed."
    return message

# UI codes: S = syncing, E = error, C = completed, None = never synced
MigrationStatusCode = Optional[str]


def bulk_connection_migration_status(
    connection_ids: list[int],
) -> Dict[int, Dict[str, MigrationStatusCode]]:
    """
    Compute migration_status and job-level error_message for connections.

    In progress when:
      - the latest job-level log is still ``I``, or
      - any ``MigrationRecord`` for the connection is ``in_progress``.

    Older open logs do not override a later terminal outcome. Actual running
    migration records still keep parallel batch/pipeline jobs visibly active.

    Terminal outcome uses the latest completed job-level log (S/E/P/F).
    """
    if not connection_ids:
        return {}

    ids = list(connection_ids)

    # Exclude IDE transform audits (job_name transform_*) — Validate/Publish must
    # not drive connector migration_status or activity "Migration failed" text.
    latest_job_log_ids = (
        Log.objects.filter(
            connection_id__in=ids,
            migration_record__isnull=True,
            job_name__isnull=False,
        )
        .exclude(job_name__istartswith="transform_")
        .values("connection_id")
        .annotate(latest_log_id=Max("log_id"))
    )
    latest_job_logs = [
        log
        for log in Log.objects.filter(
            log_id__in=[row["latest_log_id"] for row in latest_job_log_ids]
        )
        if not _is_transform_audit_job_name(getattr(log, "job_name", None))
        and not _is_transform_audit_message(getattr(log, "message", None))
    ]
    open_job_log_ids = {
        log.connection_id for log in latest_job_logs if log.status == "I"
    }

    mr_in_progress_ids = set(
        MigrationRecord.objects.filter(
            connection_id__in=ids,
            status="in_progress",
        )
        .values_list("connection_id", flat=True)
        .distinct()
    )

    in_progress_ids = open_job_log_ids | mr_in_progress_ids

    latest_terminal_log_id_map = {
        row["connection_id"]: row["latest_log_id"]
        for row in Log.objects.filter(
            connection_id__in=ids,
            status__in=("S", "E", "P", "F"),
            migration_record__isnull=True,
            job_name__isnull=False,
        )
        .exclude(job_name__istartswith="transform_")
        .values("connection_id")
        .annotate(latest_log_id=Max("log_id"))
    }

    latest_terminal_logs = {
        log.connection_id: log
        for log in Log.objects.filter(log_id__in=latest_terminal_log_id_map.values())
        if not _is_transform_audit_job_name(getattr(log, "job_name", None))
        and not _is_transform_audit_message(getattr(log, "message", None))
    }

    out: Dict[int, Dict[str, MigrationStatusCode]] = {}
    for cid in ids:
        if cid in in_progress_ids:
            out[cid] = {"migration_status": "S", "error_message": None}
            continue

        log = latest_terminal_logs.get(cid)
        if not log:
            out[cid] = {"migration_status": None, "error_message": None}
            continue

        if log.status in ("E", "P", "F"):
            out[cid] = {
                "migration_status": "E",
                "error_message": _resolve_connector_error_message(cid, log.message),
            }
        elif log.status == "S":
            out[cid] = {"migration_status": "C", "error_message": None}
        else:
            out[cid] = {"migration_status": None, "error_message": None}

    return out


def connection_migration_status(connection_id: int) -> tuple[MigrationStatusCode, Optional[str]]:
    """Single-connection helper for WebSocket payloads."""
    row = bulk_connection_migration_status([connection_id]).get(
        connection_id,
        {"migration_status": None, "error_message": None},
    )
    return row["migration_status"], row["error_message"]
