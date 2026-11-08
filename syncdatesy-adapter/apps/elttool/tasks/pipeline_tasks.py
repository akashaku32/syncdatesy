import logging
import os
import re
from datetime import datetime, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Dedicated queue so flow starts are not stuck behind ops-monitoring tasks.
# celery-worker-django listens to this queue on its own worker process.
PIPELINE_RUN_CELERY_QUEUE = "pipeline"
# Wait and retry instead of holding an orchestrator slot when this organisation
# is already at its plan concurrent-flow cap. Other organisations keep their slots.
PIPELINE_RUN_COMPANY_WAIT_SECONDS = 20
# Recheck a deferred failure until its table rows leave in_progress.
PIPELINE_DEFERRED_FAILURE_RETRY_SECONDS = 30
# After this many waits (~10 min at 30s), force-fail leftover in_progress rows
# so execution logs do not keep hourglasses after a terminal node/run failure.
PIPELINE_DEFERRED_FAILURE_MAX_ATTEMPTS = int(
    os.getenv("PIPELINE_DEFERRED_FAILURE_MAX_ATTEMPTS", "20")
)
# A run marked running with no executor is stuck in the worker, not in a plan cap.
PIPELINE_EXECUTORLESS_ALERT_SECONDS = 10 * 60
PIPELINE_EXECUTORLESS_REAP_SECONDS = 15 * 60
# Active table rows left behind after a run already finished.
PIPELINE_ORPHAN_MIGRATION_SECONDS = 10 * 60
# Queued capacity waits retry every 20s; if no broker message for this long, re-dispatch.
PIPELINE_QUEUED_ORPHAN_SECONDS = 90
_ORPHAN_ACTIVE_STATUSES = ("in_progress", "pending", "started", "running", "queued")
_PIPELINE_RUN_JOB = re.compile(r"^pipeline_run_(\d+)")
_TERMINAL_RUN_STATUSES = ("deleted", "failed", "completed")
_PIPELINE_TASK_NAMES = {
    "apps.elttool.tasks.execute_pipeline_run",
    "apps.elttool.tasks.start_pipeline_level",
    "apps.elttool.tasks.poll_pipeline_run",
}


def pipeline_run_executorless_action(metadata, started_at, now=None) -> str:
    """Return ``ok``, ``alert``, or ``reap`` for a running run with no executor."""
    meta = metadata or {}
    if meta.get("executor") or meta.get("failure_pending"):
        return "ok"
    if started_at is None:
        return "ok"
    current = now or timezone.now()
    age = (current - started_at).total_seconds()
    if age >= PIPELINE_EXECUTORLESS_REAP_SECONDS:
        return "reap"
    if age >= PIPELINE_EXECUTORLESS_ALERT_SECONDS:
        return "alert"
    return "ok"


def _run_status(run_id: int):
    from apps.elttool.models import PipelineRun

    try:
        return PipelineRun.objects.values_list("status", flat=True).get(id=run_id)
    except PipelineRun.DoesNotExist:
        return None


def _schedule_pipeline_poll(run_id: int) -> None:
    from apps.elttool.v1.pipeline_views import PIPELINE_POLL_COUNTDOWN_SECONDS

    poll_pipeline_run.apply_async(
        args=[run_id],
        countdown=PIPELINE_POLL_COUNTDOWN_SECONDS,
        queue=PIPELINE_RUN_CELERY_QUEUE,
    )


@shared_task(
    bind=True,
    name="apps.elttool.tasks.execute_pipeline_run",
    queue=PIPELINE_RUN_CELERY_QUEUE,
    max_retries=None,
)
def execute_pipeline_run(self, run_id: int):
    from apps.elttool.models import PipelineRun
    from apps.elttool.v1.pipeline_views import (
        _execute_pipeline_strict,
        _nonblocking_orchestration_enabled,
        begin_nonblocking_pipeline_run,
        try_acquire_company_flow_slot,
    )

    run_status = _run_status(run_id)
    if run_status is None:
        logger.info("PIPELINE_RUN_ABSENT | run=%s", run_id)
        return
    if run_status in _TERMINAL_RUN_STATUSES:
        logger.info("PIPELINE_RUN_TERMINAL | run=%s | status=%s", run_id, run_status)
        return

    if not try_acquire_company_flow_slot(run_id):
        try:
            from apps.elttool.v1.pipeline_views import (
                _mark_pipeline_run_waiting_for_capacity,
            )

            _mark_pipeline_run_waiting_for_capacity(run_id)
        except Exception:
            logger.exception(
                "PIPELINE_RUN_WAIT_MARK_FAILED | run=%s",
                run_id,
            )
        logger.info(
            "PIPELINE_RUN_COMPANY_WAIT | run=%s | retry_in=%ss",
            run_id,
            PIPELINE_RUN_COMPANY_WAIT_SECONDS,
        )
        raise self.retry(countdown=PIPELINE_RUN_COMPANY_WAIT_SECONDS)
    metadata = (
        PipelineRun.objects.filter(id=run_id).values_list("metadata", flat=True).first()
        or {}
    )
    mode = (metadata or {}).get("orchestration_mode")
    use_nonblocking = mode == "nonblocking" or (
        mode != "blocking"
        and not (metadata or {}).get("executor")
        and _nonblocking_orchestration_enabled()
    )
    if use_nonblocking:
        action = begin_nonblocking_pipeline_run(run_id)
        if action == "continue":
            _schedule_pipeline_poll(run_id)
        return
    _execute_pipeline_strict(run_id)


@shared_task(
    bind=True,
    name="apps.elttool.tasks.start_pipeline_level",
    queue=PIPELINE_RUN_CELERY_QUEUE,
    max_retries=None,
)
def start_pipeline_level(self, run_id: int):
    """Dispatch the next ready DAG nodes and return. Polling continues the run."""
    from apps.elttool.v1.pipeline_views import begin_nonblocking_pipeline_run

    run_status = _run_status(run_id)
    if run_status is None or run_status in _TERMINAL_RUN_STATUSES:
        logger.info("PIPELINE_LEVEL_SKIP | run=%s | status=%s", run_id, run_status)
        return
    action = begin_nonblocking_pipeline_run(run_id)
    if action == "continue":
        _schedule_pipeline_poll(run_id)


@shared_task(
    bind=True,
    name="apps.elttool.tasks.poll_pipeline_run",
    queue=PIPELINE_RUN_CELERY_QUEUE,
    max_retries=None,
)
def poll_pipeline_run(self, run_id: int):
    """Check in-flight nodes. Re-queue until the level finishes, then advance."""
    from apps.elttool.v1.pipeline_views import (
        PIPELINE_POLL_COUNTDOWN_SECONDS,
        advance_nonblocking_pipeline_run,
    )

    run_status = _run_status(run_id)
    if run_status is None or run_status in _TERMINAL_RUN_STATUSES:
        logger.info("PIPELINE_POLL_SKIP | run=%s | status=%s", run_id, run_status)
        return
    action = advance_nonblocking_pipeline_run(run_id)
    if action == "continue":
        logger.info(
            "PIPELINE_POLL_WAIT | run=%s | retry_in=%ss",
            run_id,
            PIPELINE_POLL_COUNTDOWN_SECONDS,
        )
        raise self.retry(countdown=PIPELINE_POLL_COUNTDOWN_SECONDS)


@shared_task(
    bind=True,
    name="apps.elttool.tasks.finalize_deferred_pipeline_failure",
    queue=PIPELINE_RUN_CELERY_QUEUE,
    max_retries=None,
)
def finalize_deferred_pipeline_failure(self, run_id: int):
    """Close a run that failed while table rows were still in progress.

    The orchestrator records failure_pending and returns so a sibling load can
    finish. This task marks the run failed once those rows are terminal, and
    retries while they are still active. The run does not hold a flow slot
    during the wait.
    """
    from apps.elttool.models import PipelineRun
    from apps.elttool.v1.pipeline_views import (
        _close_deferred_pipeline_failure,
        _fail_active_pipeline_load_rows,
        _get_pipeline_run_metadata,
        _reconcile_pipeline_run_status,
    )

    try:
        run = PipelineRun.objects.get(id=run_id)
    except PipelineRun.DoesNotExist:
        logger.info("PIPELINE_DEFERRED_FAILURE_ABSENT | run=%s", run_id)
        return
    if run.status != "running" or not (run.metadata or {}).get("failure_pending"):
        return

    _reconcile_pipeline_run_status(run)
    run.refresh_from_db(fields=["status", "metadata"])
    if run.status == "running" and (run.metadata or {}).get("failure_pending"):
        attempts = int(getattr(self.request, "retries", 0) or 0) + 1
        if attempts >= PIPELINE_DEFERRED_FAILURE_MAX_ATTEMPTS:
            meta = _get_pipeline_run_metadata(run)
            reason = meta.get("error") or "Pipeline run failed"
            logger.warning(
                "PIPELINE_DEFERRED_FAILURE_FORCE_CLOSE | run=%s | attempts=%s",
                run_id,
                attempts,
            )
            _fail_active_pipeline_load_rows(run, reason=str(reason))
            _close_deferred_pipeline_failure(run, meta)
            return
        logger.info(
            "PIPELINE_DEFERRED_FAILURE_WAIT | run=%s | retry_in=%ss | attempt=%s",
            run_id,
            PIPELINE_DEFERRED_FAILURE_RETRY_SECONDS,
            attempts,
        )
        raise self.retry(countdown=PIPELINE_DEFERRED_FAILURE_RETRY_SECONDS)


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def _pipeline_run_ids_with_pending_execute_tasks(run_ids: set[int]) -> set[int]:
    """Run ids that already have an execute_pipeline_run message on the broker."""
    if not run_ids:
        return set()
    found: set[int] = set()
    try:
        from datasyncher.celery_app import app

        inspect = app.control.inspect(timeout=3)
        for method in (inspect.active, inspect.reserved, inspect.scheduled):
            try:
                bucket = method() or {}
            except Exception:
                continue
            for tasks in bucket.values():
                for raw in tasks or []:
                    request = raw.get("request", raw) if isinstance(raw, dict) else {}
                    if not isinstance(request, dict):
                        continue
                    if request.get("name") != "apps.elttool.tasks.execute_pipeline_run":
                        continue
                    args = request.get("args") or []
                    try:
                        run_id = int(args[0]) if args else None
                    except (TypeError, ValueError, IndexError):
                        continue
                    if run_id in run_ids:
                        found.add(run_id)
    except Exception:
        logger.exception("PIPELINE_QUEUED_INSPECT_FAILED")
    return found


def redispatch_orphaned_queued_pipeline_runs(now=None):
    """Re-queue capacity waits that lost their Celery retry chain."""
    from apps.elttool.models import PipelineRun
    from apps.elttool.v1.pipeline_views import _dispatch_pipeline_run

    current = now or timezone.now()
    grace_before = current - timedelta(seconds=PIPELINE_QUEUED_ORPHAN_SECONDS)
    waiting_ids: list[int] = []
    for run in PipelineRun.objects.filter(status="queued").only(
        "id", "metadata", "started_at"
    ):
        meta = run.metadata or {}
        if meta.get("executor") or not meta.get("waiting_for_capacity"):
            continue
        since_dt = _parse_iso_datetime(meta.get("waiting_since")) or _parse_iso_datetime(
            meta.get("requested_at")
        )
        if since_dt is None:
            since_dt = run.started_at
        if since_dt is not None and since_dt > grace_before:
            continue
        waiting_ids.append(run.id)
    if not waiting_ids:
        return []
    pending = _pipeline_run_ids_with_pending_execute_tasks(set(waiting_ids))
    redispatched: list[int] = []
    for run_id in waiting_ids:
        if run_id in pending:
            continue
        try:
            _dispatch_pipeline_run(run_id)
            redispatched.append(run_id)
            logger.warning("PIPELINE_QUEUED_ORPHAN_REDISPATCH | run=%s", run_id)
        except Exception:
            logger.exception("PIPELINE_QUEUED_REDISPATCH_FAILED | run=%s", run_id)
    return redispatched


def _pipeline_task_ids_for_runs(run_ids: set[int]) -> list[str]:
    """Task ids for pipeline orchestration messages belonging to these runs."""
    if not run_ids:
        return []
    found = []
    try:
        from datasyncher.celery_app import app

        inspect = app.control.inspect(timeout=3)
        for method in (inspect.active, inspect.reserved, inspect.scheduled):
            try:
                bucket = method() or {}
            except Exception:
                continue
            for tasks in bucket.values():
                for raw in tasks or []:
                    request = raw.get("request", raw) if isinstance(raw, dict) else {}
                    if not isinstance(request, dict):
                        continue
                    if request.get("name") not in _PIPELINE_TASK_NAMES:
                        continue
                    args = request.get("args") or []
                    try:
                        run_id = int(args[0]) if args else None
                    except (TypeError, ValueError, IndexError):
                        continue
                    if run_id in run_ids and request.get("id"):
                        found.append(request["id"])
    except Exception:
        logger.exception("PIPELINE_REAP_INSPECT_FAILED")
    return found


def orphan_migration_groups(rows, terminal_run_ids):
    """Group active migration rows whose pipeline run is already finished."""
    terminal = {int(run_id) for run_id in terminal_run_ids}
    groups = {}
    for row in rows:
        match = _PIPELINE_RUN_JOB.match(getattr(row, "job_name", "") or "")
        if not match:
            continue
        run_id = int(match.group(1))
        if run_id not in terminal:
            continue
        groups.setdefault(run_id, []).append(row)
    return groups


def close_orphaned_terminal_migration_records(now=None):
    """Fail table rows still active long after their pipeline run finished."""
    from apps.elttool.models import MigrationRecord, PipelineRun, SourceTableSelection

    current = now or timezone.now()
    cutoff = current - timedelta(seconds=PIPELINE_ORPHAN_MIGRATION_SECONDS)
    rows = list(
        MigrationRecord.objects.filter(
            status__in=_ORPHAN_ACTIVE_STATUSES,
            updated_at__lte=cutoff,
            job_name__startswith="pipeline_run_",
        ).only("id", "job_name", "connection_id", "table_name", "task_id")
    )
    if not rows:
        return {}
    run_ids = []
    for row in rows:
        match = _PIPELINE_RUN_JOB.match(row.job_name or "")
        if match:
            run_ids.append(int(match.group(1)))
    terminal_ids = PipelineRun.objects.filter(
        id__in=run_ids,
        status__in=_TERMINAL_RUN_STATUSES,
    ).values_list("id", flat=True)
    groups = orphan_migration_groups(rows, terminal_ids)
    closed = {}
    task_ids = []
    for run_id, run_rows in groups.items():
        ids = [row.id for row in run_rows]
        MigrationRecord.objects.filter(id__in=ids).update(
            status="failed",
            updated_at=current,
        )
        for row in run_rows:
            if row.connection_id and row.table_name:
                still_active = MigrationRecord.objects.filter(
                    connection_id=row.connection_id,
                    table_name=row.table_name,
                    status__in=_ORPHAN_ACTIVE_STATUSES,
                ).exists()
                if not still_active:
                    SourceTableSelection.objects.filter(
                        connection_id=row.connection_id,
                        table=row.table_name,
                        status="in_progress",
                    ).update(status=None)
            if row.task_id:
                task_ids.append(str(row.task_id))
        closed[run_id] = len(ids)
        logger.warning(
            "PIPELINE_ORPHAN_MIGRATION_CLOSED | run=%s | count=%s",
            run_id,
            len(ids),
        )
    if task_ids:
        try:
            from apps.elttool.services.celery_inspect_service import (
                get_migration_celery_app,
            )

            get_migration_celery_app().control.revoke(task_ids, terminate=False)
        except Exception:
            logger.exception("PIPELINE_ORPHAN_REVOKE_FAILED")
    return closed


@shared_task(
    name="apps.elttool.tasks.reap_stuck_pipeline_runs",
    queue="ops",
)
def reap_stuck_pipeline_runs():
    """Fail runs that have been running with no executor, and revoke their tasks.

    These runs are stuck in the Django worker. They are not waiting on a plan
    cap, and they do not have a flow pod. Runs that already started keep going.
    """
    from django.core.management import call_command

    from apps.elttool.models import PipelineRun

    now = timezone.now()
    redispatched = redispatch_orphaned_queued_pipeline_runs(now)
    alert_before = now - timedelta(seconds=PIPELINE_EXECUTORLESS_ALERT_SECONDS)
    candidates = PipelineRun.objects.filter(
        status="running",
        started_at__lte=alert_before,
    ).only("id", "started_at", "metadata")
    reap_ids = []
    for run in candidates:
        action = pipeline_run_executorless_action(run.metadata, run.started_at, now)
        if action == "alert":
            logger.warning(
                "PIPELINE_RUN_EXECUTORLESS_ALERT | run=%s | started_at=%s",
                run.id,
                run.started_at.isoformat() if run.started_at else None,
            )
        elif action == "reap":
            reap_ids.append(run.id)
    orphaned = close_orphaned_terminal_migration_records(now)
    if not reap_ids:
        return {
            "alerted": True,
            "reaped": [],
            "orphaned": orphaned,
            "redispatched": redispatched,
        }
    for run_id in reap_ids:
        try:
            call_command(
                "fail_pipeline_run",
                str(run_id),
                reason="Cleared stuck pending run that never started",
                no_revoke=True,
            )
        except Exception:
            logger.exception("PIPELINE_REAP_FAIL_COMMAND | run=%s", run_id)
    task_ids = _pipeline_task_ids_for_runs(set(reap_ids))
    if task_ids:
        try:
            from datasyncher.celery_app import app

            app.control.revoke(task_ids, terminate=False)
        except Exception:
            logger.exception("PIPELINE_REAP_REVOKE_FAILED")
    logger.warning("PIPELINE_RUN_EXECUTORLESS_REAPED | runs=%s", reap_ids)
    return {
        "reaped": reap_ids,
        "revoked": len(task_ids),
        "orphaned": orphaned,
        "redispatched": redispatched,
    }
