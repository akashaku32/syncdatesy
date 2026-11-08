"""Refresh pipeline flow-fleet Redis lease during long-running migration work."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def parse_pipeline_run_id(job_name: str | None) -> int | None:
    if not job_name:
        return None
    match = re.search(r"pipeline_run_(\d+)", str(job_name))
    return int(match.group(1)) if match else None


def refresh_pipeline_flow_fleet_lease(
    *,
    job_name: str | None = None,
    pipeline_run_id: int | None = None,
) -> None:
    """Best-effort lease refresh; no-op outside Celery worker runtime."""
    run_id = pipeline_run_id if pipeline_run_id is not None else parse_pipeline_run_id(job_name)
    if not run_id:
        return
    try:
        from celery_worker import _get_redis
        import celery_job_worker_pool as job_pool

        job_pool.refresh_flow_fleet_lease(_get_redis(), int(run_id))
    except Exception as exc:
        logger.debug(
            "FLOW_FLEET_LEASE_REFRESH_SKIPPED | run=%s | err=%s",
            run_id,
            exc,
        )
