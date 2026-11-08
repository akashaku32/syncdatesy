"""
Map legacy ODATA :50053 StatusUpdate Struct payloads to proxy progress events (Django ORM).
Used by backend/sap_proxy_agent/unary/unary_rpc.py — not for public HTTP.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.elttool.models import ProxyAgent
from apps.elttool.services.proxy_agent_migration_progress import (
    ProgressValidationError,
    apply_progress_event,
)

logger = logging.getLogger(__name__)


def _event_from_legacy_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in ("completed", "success", "s"):
        return "table_completed"
    if normalized in ("failed", "error", "e", "f"):
        return "table_failed"
    if normalized in ("in_progress", "running", "i"):
        return "table_started"
    return "table_progress"


def apply_legacy_statusdetails(status_data: dict[str, Any]) -> dict[str, Any]:
    """
    Convert legacy agent TableStatus* / ETL / transfer log payloads into apply_progress_event.
    """
    if not isinstance(status_data, dict):
        raise ProgressValidationError("statusdetails must be a JSON object.")

    agent_uid = (
        str(status_data.get("cust_id") or status_data.get("agent_id") or "")
    ).strip()
    if not agent_uid:
        raise ProgressValidationError("cust_id is required in legacy status payload.")

    agent = ProxyAgent.objects.filter(agent_uid=agent_uid).select_related("cmp").first()
    if agent is None:
        raise ProgressValidationError(f"Unknown agent_uid={agent_uid!r}.")

    try:
        migration_session_id = int(status_data.get("migration_session_id"))
        connection_id = int(status_data.get("connection_id"))
    except (TypeError, ValueError) as exc:
        raise ProgressValidationError(
            "migration_session_id and connection_id must be integers."
        ) from exc

    job_name = str(status_data.get("job_name") or "").strip()
    if not job_name:
        raise ProgressValidationError("job_name is required.")

    table_name = str(status_data.get("table_name") or "").strip()
    event = _event_from_legacy_status(str(status_data.get("status") or ""))

    body: dict[str, Any] = {
        "event": event,
        "migration_session_id": migration_session_id,
        "job_name": job_name,
        "connection_id": connection_id,
    }
    if table_name:
        body["table_name"] = table_name

    payload = {k: v for k, v in status_data.items() if k not in body}
    if payload:
        body["payload"] = payload

    for key in (
        "rows",
        "rows_processed",
        "inserts",
        "error_message",
        "message",
        "delta_token",
        "sync_end_time",
        "start_time",
        "log_id",
    ):
        if key in status_data and key not in body:
            body[key] = status_data[key]
    if "rows_processed" in body and "rows" not in body:
        body["rows"] = body.pop("rows_processed", None)

    logger.info(
        "legacy_status: agent_uid=%s event=%s table=%s session=%s",
        agent_uid,
        event,
        table_name,
        migration_session_id,
    )
    return apply_progress_event(agent=agent, body=body)
