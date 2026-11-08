"""Celery broker inspect helpers for Django (migration job visibility)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from celery import Celery
from django.conf import settings

logger = logging.getLogger(__name__)

_celery_app: Optional[Celery] = None


def get_migration_celery_app() -> Celery:
    global _celery_app
    if _celery_app is None:
        broker = getattr(settings, "CELERY_BROKER_URL", None) or getattr(
            settings, "REDIS_URL", "redis://redis:6379/0"
        )
        _celery_app = Celery("migration_tasks")
        _celery_app.conf.update(
            broker_url=broker,
            result_backend=broker,
            task_default_queue="default",
            broker_connection_retry_on_startup=True,
            broker_connection_timeout=10,
            broker_connection_retry=True,
            broker_connection_max_retries=3,
        )
    return _celery_app


def safe_inspect_call(method_name: str, timeout: float = 2) -> dict:
    try:
        insp = get_migration_celery_app().control.inspect(timeout=timeout)
        method = getattr(insp, method_name)
        return method() or {}
    except Exception as exc:
        logger.warning("CELERY_INSPECT_FAILED | method=%s | err=%s", method_name, exc)
        return {}


def _extract_job_name_from_task(task: dict) -> Optional[str]:
    args = task.get("args") or []
    kwargs = task.get("kwargs") or {}
    if len(args) >= 4:
        return args[3]
    return kwargs.get("job_name")


def iter_inspected_tasks(*, timeout: float = 3) -> list[dict]:
    """Flatten active, reserved, and scheduled tasks with worker + state."""
    items: list[dict] = []
    for state, method in (
        ("active", "active"),
        ("reserved", "reserved"),
        ("scheduled", "scheduled"),
    ):
        bucket = safe_inspect_call(method, timeout=timeout)
        for worker, tasks in bucket.items():
            for raw in tasks or []:
                task = raw.get("request", raw) if state == "scheduled" else raw
                if not isinstance(task, dict):
                    continue
                items.append(
                    {
                        "task": task,
                        "worker": worker,
                        "state": state,
                        "job_name": _extract_job_name_from_task(task),
                        "task_id": task.get("id"),
                        "task_name": task.get("name"),
                    }
                )
    return items


def tasks_for_job_name(job_name: str, *, timeout: float = 3) -> list[dict]:
    matches = []
    for item in iter_inspected_tasks(timeout=timeout):
        jn = item.get("job_name")
        if jn and (jn == job_name or str(jn).startswith(job_name)):
            matches.append(
                {
                    "task_id": item.get("task_id"),
                    "task_name": item.get("task_name"),
                    "worker": item.get("worker"),
                    "state": item.get("state"),
                    "job_name": jn,
                }
            )
    return matches


def is_connection_migration_active_celery(connection_name: str) -> dict:
    try:
        safe_connection_name = connection_name.replace(" ", "_").replace("-", "_")
        active_jobs = []
        for item in iter_inspected_tasks():
            job_name = item.get("job_name")
            if job_name and str(job_name).startswith(safe_connection_name):
                active_jobs.append(
                    {
                        "job_name": job_name,
                        "task_id": item.get("task_id"),
                        "worker": item.get("worker"),
                        "state": item.get("state"),
                    }
                )
        return {
            "is_active": bool(active_jobs),
            "active_jobs": active_jobs,
            "error": None,
        }
    except Exception as exc:
        return {
            "is_active": False,
            "active_jobs": [],
            "error": f"CELERY CHECK FAILED: {exc}",
        }


def get_celery_worker_status() -> dict:
    try:
        inspect = get_migration_celery_app().control.inspect()
        inspect.timeout = 3.0
        stats = inspect.stats() or {}
        active = inspect.active() or {}
        if not stats:
            return {
                "workers_available": False,
                "error": "No Celery workers found or Redis connection failed",
            }
        worker_info: dict[str, Any] = {}
        total_active_tasks = 0
        for worker_name, worker_stats in stats.items():
            active_tasks = len(active.get(worker_name, []))
            total_active_tasks += active_tasks
            worker_info[worker_name] = {
                "status": "online",
                "active_tasks": active_tasks,
                "pool_processes": worker_stats.get("pool", {}).get("processes", 0),
            }
        return {
            "workers_available": True,
            "total_workers": len(worker_info),
            "total_active_tasks": total_active_tasks,
            "workers": worker_info,
            "error": None,
        }
    except Exception as exc:
        return {
            "workers_available": False,
            "error": f"Failed to connect to Celery: {exc}",
        }


def check_connection_migration_status(connection_name: str) -> dict:
    try:
        celery_result = is_connection_migration_active_celery(connection_name)
        return {
            "can_start_migration": not celery_result["is_active"],
            "active_migrations": celery_result["active_jobs"],
            "error": celery_result["error"],
        }
    except Exception as exc:
        return {
            "can_start_migration": True,
            "active_migrations": [],
            "error": str(exc),
        }
