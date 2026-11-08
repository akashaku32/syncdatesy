"""Celery tasks for platform admin ops monitoring."""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.elttool.tasks.ops_monitoring_tasks.push_admin_ops_snapshot",
    queue="ops",
)
def push_admin_ops_snapshot():
    """Build and broadcast ops snapshot to admin_ops_platform WebSocket group."""
    try:
        from apps.elttool.services.ops_monitoring_service import build_ops_snapshot
        from apps.elttool.websocket.ws_utils import push_admin_ops_snapshot as _push

        _push(build_ops_snapshot())
    except Exception:
        logger.exception("push_admin_ops_snapshot task failed")
