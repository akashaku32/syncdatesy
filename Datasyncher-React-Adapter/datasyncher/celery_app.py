import logging
import os
import threading

from celery import Celery
from celery.signals import (
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
)

logger = logging.getLogger(__name__)

# Set default settings for celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'datasyncher.settings.developer')

app = Celery('datasyncher')

# Load settings from Django
app.config_from_object('django.conf:settings', namespace='CELERY')

# This next line is only needed IF you want to hard override -- otherwise, let config_from_object do its job
app.conf.broker_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
# Auto-discover tasks in your Django app
app.autodiscover_tasks()


@worker_process_init.connect
def _reset_locks_after_fork(**kwargs):
    """Give each prefork child its own locks and database connections.

    A lock held in the parent at fork time stays locked in the child forever.
    That froze pipeline worker processes and left other organisations waiting.
    Pipeline run metadata now uses select_for_update, so only the migration
    session lock still needs a post-fork reset.
    """
    from django.db import connections

    connections.close_all()

    import apps.elttool.v1.views as elt_views

    elt_views._migration_session_id_lock = threading.Lock()


@task_prerun.connect
def _close_old_db_connections_before_task(*args, **kwargs):
    """Drop stale connections before each Celery task (CONN_MAX_AGE recycle)."""
    from django.db import close_old_connections

    close_old_connections()


@task_postrun.connect
def _close_old_db_connections_after_task(*args, **kwargs):
    """Release DB sockets after each task so idle workers do not pin Postgres."""
    from django.db import close_old_connections

    close_old_connections()


@worker_ready.connect
def _resume_pipeline_runs_after_worker_start(sender=None, **kwargs):
    """After a pipeline-worker restart, continue flows whose orchestrator died.

    A killed pod does not redeliver an in-flight task until the broker
    visibility timeout, so the schedule would skip every later tick.
    """
    hostname = str(getattr(sender, "hostname", "") or "")
    if not hostname.startswith("pipeline@"):
        return
    try:
        from apps.elttool.v1.pipeline_views import resume_stale_pipeline_runs

        resume_stale_pipeline_runs()
    except Exception:
        logger.exception("PIPELINE_STARTUP_RESUME_FAILED")