from celery import Celery, group
from celery.exceptions import Ignore, SoftTimeLimitExceeded
from celery.signals import (
    task_failure,
    before_task_publish,
    task_revoked,
    worker_shutdown,
    worker_shutting_down,
    worker_process_init,
    task_prerun,
    task_postrun,
    worker_ready,
)
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json
import csv
import hashlib
import math
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
import requests
import signal
import os
import sys
import time
import asyncio
import concurrent.futures
import importlib
import re
import tempfile

# Skip google-auth's metadata universe_domain lookup on GKE WI nodes
# that are still booting gke-metadata-server.
os.environ.setdefault("GOOGLE_CLOUD_UNIVERSE_DOMAIN", "googleapis.com")

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import Event, Lock, Thread
from typing import Optional
import redis as redis_sync
from celery.utils.log import get_task_logger
from google.cloud import storage

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from websocket_utils import (
    trigger_ws_status_update,
    trigger_connection_ws_status_update,
    trigger_connection_activity_log_update,
)
from celery_scale_down_protection import (
    detect_and_mark_stale_tasks,
    enter_worker_drain,
    exit_worker_drain,
    floor_desired_replicas_by_busy_pods,
    on_task_postrun as scale_down_on_task_postrun,
    on_task_prerun as scale_down_on_task_prerun,
    protection_enabled as scale_down_protection_enabled,
    reconcile_pod_active_tasks_from_inspect,
    reset_after_fork as reset_scale_down_after_fork,
    sync_deletion_cost_from_redis,
)
import celery_job_metrics as job_metrics
import celery_job_worker_pool as job_pool
import celery_resource_config as resource_cfg

logger = get_task_logger(__name__)

FILE_SOURCE_REFRESH_ROUTES = {
    "/Snowflake-AmazonS3-migration-yield": ("/AmazonS3-listing-tables", "AmazonS3", "s3"),
    "/Snowflake-AzureDataLakeStorage-migration-yield": ("/AzureDataLakeStorage-listing-tables", "AzureDataLakeStorage", "adls"),
    "/Snowflake-ADLS-migration-yield": ("/ADLS-listing-tables", "AzureDataLakeStorage", "adls"),
    "/Snowflake-GoogleDrive-migration-yield": ("/GoogleDrive-listing-tables", "GoogleDrive", "google_drive"),
    "/Snowflake-SFTP-migration-yield": ("/SFTP-listing-tables", "SFTP", "sftp"),
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "3"))


def _parse_unlimited_seconds(env_name: str, default: Optional[int]) -> Optional[int]:
    """Parse seconds from env; 0/none/unlimited disables the limit (returns None)."""
    raw = os.getenv(env_name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("", "0", "none", "unlimited", "-1", "off", "false"):
        return None
    return max(1, int(raw))


# Long-running Dynamics migrations (2–3+ days). Override via env when needed.
MIGRATION_LONG_RUN_SECONDS = int(os.getenv("MIGRATION_LONG_RUN_SECONDS", str(4 * 86_400)))
MIGRATION_TASK_STUCK_FAIL_SECONDS = _parse_unlimited_seconds(
    "MIGRATION_TASK_STUCK_FAIL_SECONDS",
    MIGRATION_LONG_RUN_SECONDS,
)

# Default: no Celery task time limit (long Dynamics tables can run indefinitely).
CELERY_TASK_TIME_LIMIT = _parse_unlimited_seconds("CELERY_TASK_TIME_LIMIT_SECONDS", None)
_soft_default = (
    max(CELERY_TASK_TIME_LIMIT - 1800, 60) if CELERY_TASK_TIME_LIMIT is not None else None
)
CELERY_TASK_SOFT_TIME_LIMIT = _parse_unlimited_seconds(
    "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", _soft_default,
)
MAX_CELERY_CHILD_GROUPS = int(os.getenv("CELERY_MAX_CHILD_GROUPS", "6"))
LISTING_PREFIX_CHILD_GROUPS = int(os.getenv("LISTING_PREFIX_CHILD_GROUPS", str(MAX_CELERY_CHILD_GROUPS)))
CELERY_DIRECT_CONNECTOR_EXECUTION = os.getenv("CELERY_DIRECT_CONNECTOR_EXECUTION", "true").lower() == "true"
CELERY_HTTP_FALLBACK_ENABLED = os.getenv("CELERY_HTTP_FALLBACK_ENABLED", "false").lower() == "true"
CELERY_MARK_COMPLETED_FROM_CELERY = os.getenv("CELERY_MARK_COMPLETED_FROM_CELERY", "true").lower() == "true"
CELERY_KEDA_ACTIVE_LIST_KEY = os.getenv("CELERY_KEDA_ACTIVE_LIST_KEY", "celery:keda:active")
CELERY_ACTIVE_MIGRATION_COUNT_KEY = os.getenv(
    "CELERY_ACTIVE_MIGRATION_COUNT_KEY", "celery:active-migration-count"
)
# KEDA redis list scaler: LLEN of this list = desired replica count (listLength target = 1).
CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY = os.getenv(
    "CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY", "celery:keda:desired-replicas"
)
CELERY_KEDA_PODS_PER_JOB = max(1, int(os.getenv("CELERY_KEDA_PODS_PER_JOB", "2")))
CELERY_KEDA_MAX_REPLICAS = max(1, int(os.getenv("CELERY_KEDA_MAX_REPLICAS", "4")))
# jobs = legacy jobs×2; workload = sum(min(2, ceil(tables/concurrency))) per in_progress job.
CELERY_KEDA_SCALE_MODE = os.getenv("CELERY_KEDA_SCALE_MODE", "jobs").strip().lower()
CELERY_KEDA_MAX_PODS_PER_JOB = max(1, int(os.getenv("CELERY_KEDA_MAX_PODS_PER_JOB", "6")))
CELERY_KEDA_ACTIVE_SYNC_MAX_IDS = max(
    1, int(os.getenv("CELERY_KEDA_ACTIVE_SYNC_MAX_IDS", "32"))
)
CELERY_KEDA_ACTIVE_PRUNE_ON_POSTRUN = os.getenv(
    "CELERY_KEDA_ACTIVE_PRUNE_ON_POSTRUN", "true"
).lower() in ("1", "true", "yes", "on")
# When celery:keda:active grows far beyond real in-flight work, KEDA scaling breaks and
# pending children get requeued until CELERY_MAX_CHILD_REQUEUES — fail as pending_timeout.
CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD = max(
    50, int(os.getenv("CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD", "200"))
)
CELERY_KEDA_ACTIVE_FORCE_PRUNE_LEN = max(
    CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD,
    int(os.getenv("CELERY_KEDA_ACTIVE_FORCE_PRUNE_LEN", "500")),
)
CELERY_KEDA_ACTIVE_PRUNE_BATCH_SIZE = max(
    50, int(os.getenv("CELERY_KEDA_ACTIVE_PRUNE_BATCH_SIZE", "1000"))
)
CELERY_CHECK_JOB_STATUS_INTERVAL = int(os.getenv("CELERY_CHECK_JOB_STATUS_INTERVAL", "20"))
CELERY_CHECK_JOB_STATUS_SCHEDULE_LOCK_KEY = os.getenv(
    "CELERY_CHECK_JOB_STATUS_SCHEDULE_LOCK_KEY", "celery:check_lock"
)
CELERY_CHECK_JOB_STATUS_RUNNING_LOCK_KEY = os.getenv(
    "CELERY_CHECK_JOB_STATUS_RUNNING_LOCK_KEY", "celery:check_job_status:running"
)
# Idle age (migration_record.updated_at) before PENDING+no-payload is treated as dead.
# Must be >= longest healthy Dynamics run; progress updates bump updated_at so age resets
# while work is active. Default matches MIGRATION_LONG_RUN_SECONDS (was 3600 — killed 25h jobs).
CELERY_STALE_NO_PAYLOAD_SECONDS = int(
    os.getenv("CELERY_STALE_NO_PAYLOAD_SECONDS", str(MIGRATION_LONG_RUN_SECONDS))
)
CELERY_RECONCILE_MAX_JOBS = max(1, int(os.getenv("CELERY_RECONCILE_MAX_JOBS", "25")))
CELERY_RECONCILE_MAX_SECONDS = float(os.getenv("CELERY_RECONCILE_MAX_SECONDS", "90")) or None
# Redis broker: 0 = highest priority. Orchestrate must beat background reconcile polls.
CELERY_ORCHESTRATE_TASK_PRIORITY = int(os.getenv("CELERY_ORCHESTRATE_TASK_PRIORITY", "0"))
CELERY_CHECK_JOB_STATUS_PRIORITY = int(os.getenv("CELERY_CHECK_JOB_STATUS_PRIORITY", "9"))
CELERY_CHILD_PAYLOAD_KEY_PREFIX = os.getenv("CELERY_CHILD_PAYLOAD_KEY_PREFIX", "celery:child-payload")
# Child payload + Celery result backend must outlive multi-day Dynamics tables.
# Previous hard-coded 86400s caused PENDING_NO_PAYLOAD after ~24h on healthy workers.
CELERY_LONG_RUN_REDIS_TTL_SECONDS = max(
    86_400,
    int(
        os.getenv(
            "CELERY_LONG_RUN_REDIS_TTL_SECONDS",
            str(MIGRATION_LONG_RUN_SECONDS + 86_400),
        )
    ),
)
CELERY_CHILD_PAYLOAD_TTL_SECONDS = max(
    86_400,
    int(os.getenv("CELERY_CHILD_PAYLOAD_TTL_SECONDS", str(CELERY_LONG_RUN_REDIS_TTL_SECONDS))),
)
CELERY_RESULT_EXPIRES_SECONDS = max(
    86_400,
    int(os.getenv("CELERY_RESULT_EXPIRES_SECONDS", str(CELERY_LONG_RUN_REDIS_TTL_SECONDS))),
)
# When false, check_job_status logs PENDING diagnosis only; use Celery child retries for real errors.
CELERY_PENDING_CHILD_REQUEUE_ENABLED = os.getenv(
    "CELERY_PENDING_CHILD_REQUEUE_ENABLED", "false"
).lower() in ("1", "true", "yes", "on")
CELERY_PENDING_CHILD_REQUEUE_AFTER_SECONDS = int(os.getenv("CELERY_PENDING_CHILD_REQUEUE_AFTER_SECONDS", "90"))
# If false, check_job_status never revokes+requeues STARTED children (wait for Celery result /
# worker death handling). Set true (default) to recover rare stuck STARTED tasks after a long wait.
CELERY_STARTED_CHILD_REQUEUE_ENABLED = os.getenv(
    "CELERY_STARTED_CHILD_REQUEUE_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
# If no task time limit, do not requeue healthy STARTED children by elapsed time.
_started_requeue_default = (
    (CELERY_TASK_TIME_LIMIT + 600) if CELERY_TASK_TIME_LIMIT is not None else None
)
CELERY_STARTED_CHILD_REQUEUE_AFTER_SECONDS = _parse_unlimited_seconds(
    "CELERY_STARTED_CHILD_REQUEUE_AFTER_SECONDS",
    _started_requeue_default,
)
# Safety net: fail a STARTED child that has been stuck longer than this, regardless of the
# requeue flag or a missing task time limit. Prevents pipelines from hanging forever when a
# worker pod dies mid-task and the Celery result is orphaned in STARTED (default 4 days).
CELERY_STARTED_CHILD_HARD_FAIL_AFTER_SECONDS = _parse_unlimited_seconds(
    "CELERY_STARTED_CHILD_HARD_FAIL_AFTER_SECONDS",
    MIGRATION_LONG_RUN_SECONDS,
)
# When true, only hard-fail a stuck STARTED child if its assigned worker pod is confirmed gone
# (heartbeat expired). When false, hard-fail purely on elapsed time past the threshold.
CELERY_STARTED_HARD_FAIL_REQUIRE_DEAD_POD = os.getenv(
    "CELERY_STARTED_HARD_FAIL_REQUIRE_DEAD_POD", "true"
).lower() in ("1", "true", "yes", "on")
# Redis/Kombu default visibility_timeout is 3600s. With task_acks_late=True, messages become
# visible again after that interval and the same child task can run twice (duplicate migrations).
_broker_visibility_default = (
    CELERY_TASK_TIME_LIMIT + 3600 if CELERY_TASK_TIME_LIMIT is not None else 2_592_000
)
CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS = int(
    os.getenv(
        "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS",
        str(_broker_visibility_default),
    )
)
# Worker -> migration-function HTTP wait (must exceed Databricks Spark poll timeout).
_DEFAULT_SPARK_POLL_SECONDS = 36 * 3600
CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS = int(
    os.getenv(
        "CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS",
        str(_DEFAULT_SPARK_POLL_SECONDS + 3600),
    )
)
# Each check_job_status requeue uses a NEW Celery task id (retries reset). Cap lineage
# requeues to avoid unbounded staging tables / revoke loops if the threshold is too low.
CELERY_MAX_CHILD_REQUEUES = int(os.getenv("CELERY_MAX_CHILD_REQUEUES", "15"))
# If a STARTED child was assigned to a pod that no longer heartbeats (OOM/eviction), requeue
# after this many seconds instead of waiting for CELERY_STARTED_CHILD_REQUEUE_AFTER_SECONDS.
CELERY_OOM_ORPHAN_REQUEUE_AFTER_SECONDS = int(
    os.getenv("CELERY_OOM_ORPHAN_REQUEUE_AFTER_SECONDS", "900")
)
CELERY_WORKER_HEARTBEAT_TTL_SECONDS = int(
    os.getenv("CELERY_WORKER_HEARTBEAT_TTL_SECONDS", "180")
)
# Background refresh while ForkPoolWorker runs long migrations (must stay below TTL).
CELERY_WORKER_HEARTBEAT_REFRESH_INTERVAL_SECONDS = int(
    os.getenv("CELERY_WORKER_HEARTBEAT_REFRESH_INTERVAL_SECONDS", "60")
)
CELERY_WORKER_HEARTBEAT_KEY_PREFIX = os.getenv(
    "CELERY_WORKER_HEARTBEAT_KEY_PREFIX", "celery:worker-alive:"
)
# When true (default), stale Redis heartbeat alone does not orphan-requeue; K8s must
# confirm the assigned pod is missing/terminated/OOM. Prevents false requeues on busy pods.
CELERY_ORPHAN_REQUEUE_REQUIRE_K8S_CONFIRMATION = os.getenv(
    "CELERY_ORPHAN_REQUEUE_REQUIRE_K8S_CONFIRMATION", "true"
).lower() in ("1", "true", "yes", "on")
# Auto-requeue STARTED/IN_PROGRESS tasks when the assigned worker pod is confirmed gone
# (finalizer children, stale heartbeats, listing jobs). Requires saved task payload in Redis.
CELERY_ORPHAN_AUTO_REQUEUE_ENABLED = os.getenv(
    "CELERY_ORPHAN_AUTO_REQUEUE_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
CELERY_ORPHAN_REQUEUE_LOCK_TTL_SECONDS = int(
    os.getenv("CELERY_ORPHAN_REQUEUE_LOCK_TTL_SECONDS", "120")
)
# Query in-cluster K8s API when assigned worker pod heartbeats stop (detect OOMKilled vs rollout).
CELERY_K8S_POD_STATUS_ENABLED = os.getenv(
    "CELERY_K8S_POD_STATUS_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
CELERY_K8S_NAMESPACE = os.getenv("CELERY_K8S_NAMESPACE", "")
CELERY_WORKER_DEATH_REDIS_KEY_PREFIX = os.getenv(
    "CELERY_WORKER_DEATH_REDIS_KEY_PREFIX", "celery:worker-death:"
)
CELERY_WORKER_DEATH_REDIS_TTL_SECONDS = int(
    os.getenv("CELERY_WORKER_DEATH_REDIS_TTL_SECONDS", "604800")
)
_K8S_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_K8S_SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
_K8S_API_HOST = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
_K8S_API_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
FAIL_IN_PROGRESS_ON_WORKER_SHUTDOWN = os.getenv("FAIL_IN_PROGRESS_ON_WORKER_SHUTDOWN", "false").lower() == "true"
CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB = int(os.getenv("CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB", "1200000"))
CELERY_WORKER_MAX_TASKS_PER_CHILD = int(os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "50"))
CELERY_MIGRATION_QUEUE = os.getenv("CELERY_MIGRATION_QUEUE", "migration")
CELERY_MIGRATION_CONTROL_QUEUE = os.getenv("CELERY_MIGRATION_CONTROL_QUEUE", "migration-control")
CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE = os.getenv(
    "CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE",
    CELERY_MIGRATION_CONTROL_QUEUE,
)
CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE = os.getenv(
    "CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE", ""
)


def _resolve_check_job_status_queue() -> str:
    """Reconcile ticks must never land on the orchestrate queue (starves FANOUT)."""
    if CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE:
        return CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE
    return CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE


def _resolve_finalize_queue() -> str:
    """Completion work must not share the six orchestrate slots.

    A finalizer that blocks on a database lock or pod teardown used to sit
    unacked for days and leave orchestrate_migration reserved behind it.
    """
    if CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE:
        return CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE
    return CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE


CELERY_CHECK_JOB_STATUS_QUEUE = _resolve_check_job_status_queue()
CELERY_CHECK_JOB_STATUS_SOFT_TIME_LIMIT = int(
    os.getenv("CELERY_CHECK_JOB_STATUS_SOFT_TIME_LIMIT", "90")
)
CELERY_CHECK_JOB_STATUS_TIME_LIMIT = int(
    os.getenv("CELERY_CHECK_JOB_STATUS_TIME_LIMIT", "120")
)
CELERY_ORCHESTRATE_SOFT_TIME_LIMIT = int(
    os.getenv("CELERY_ORCHESTRATE_SOFT_TIME_LIMIT", "300")
)
CELERY_ORCHESTRATE_TIME_LIMIT = int(
    os.getenv("CELERY_ORCHESTRATE_TIME_LIMIT", "360")
)
# Finalizers only write completion state and tear down pods. A hung one must not
# occupy an orchestrate slot for days. 0/unlimited is intentionally not the default.
CELERY_FINALIZER_SOFT_TIME_LIMIT = int(os.getenv("CELERY_FINALIZER_SOFT_TIME_LIMIT_SECONDS", "120"))
CELERY_FINALIZER_TIME_LIMIT = int(os.getenv("CELERY_FINALIZER_TIME_LIMIT_SECONDS", "180"))
CELERY_FINALIZER_MAX_ATTEMPTS = max(1, int(os.getenv("CELERY_FINALIZER_MAX_ATTEMPTS", "3")))
CELERY_FINALIZER_ORPHAN_CHECKS_PER_JOB = max(
    1, int(os.getenv("CELERY_FINALIZER_ORPHAN_CHECKS_PER_JOB", "8"))
)
CELERY_JOB_ISOLATION_MODE = os.getenv("CELERY_JOB_ISOLATION_MODE", "per_job").strip().lower()
CELERY_WORKER_ROLE = os.getenv("CELERY_WORKER_ROLE", "").strip().lower()
CELERY_CHILD_AUDIT_CSV_ENABLED = os.getenv("CELERY_CHILD_AUDIT_CSV_ENABLED", "true").lower() == "true"
CELERY_CHILD_AUDIT_CSV_PATH = os.getenv(
    "CELERY_CHILD_AUDIT_CSV_PATH",
    os.path.join(tempfile.gettempdir(), "celery_child_task_audit.csv"),
)
CELERY_CHILD_AUDIT_GCS_ENABLED = os.getenv("CELERY_CHILD_AUDIT_GCS_ENABLED", "true").lower() == "true"
CELERY_CHILD_AUDIT_GCS_BUCKET = os.getenv("CELERY_CHILD_AUDIT_GCS_BUCKET") or os.getenv("GCS_BUCKET_NAME")
CELERY_CHILD_AUDIT_GCS_PREFIX = os.getenv("CELERY_CHILD_AUDIT_GCS_PREFIX", "celery-child-audit")
CELERY_ONE_TABLE_PER_CHILD = os.getenv("CELERY_ONE_TABLE_PER_CHILD", "true").lower() in (
    "1", "true", "yes", "on",
)
CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB = max(
    1, int(os.getenv("CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB", "6"))
)
CELERY_FANOUT_PENDING_KEY_PREFIX = os.getenv(
    "CELERY_FANOUT_PENDING_KEY_PREFIX", "celery:fanout-pending:"
)
CELERY_FANOUT_INFLIGHT_KEY_PREFIX = os.getenv(
    "CELERY_FANOUT_INFLIGHT_KEY_PREFIX", "celery:fanout-inflight:"
)
CELERY_FANOUT_STAGGER_TTL_SECONDS = int(os.getenv("CELERY_FANOUT_STAGGER_TTL_SECONDS", "86400"))

logger.info(
    "Celery broker=%s backend=%s concurrency=%s task_time_limit=%s visibility_timeout=%ss",
    BROKER_URL,
    RESULT_BACKEND,
    CELERY_WORKER_CONCURRENCY,
    CELERY_TASK_TIME_LIMIT if CELERY_TASK_TIME_LIMIT is not None else "unlimited",
    CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
)

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------
celery_app = Celery("migration_tasks", broker=BROKER_URL, backend=RESULT_BACKEND)
_celery_conf = dict(
    task_default_queue=CELERY_MIGRATION_QUEUE,
    worker_concurrency=CELERY_WORKER_CONCURRENCY,
    worker_pool="prefork",
    worker_prefetch_multiplier=1,       # each process takes exactly 1 task so rest stay in queue for KEDA
    task_acks_late=True,                # re-queue if worker dies mid-task
    broker_transport_options={
        "visibility_timeout": CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
    },
    task_track_started=True,
    result_expires=CELERY_RESULT_EXPIRES_SECONDS,
    worker_send_task_events=True,
    task_send_sent_event=True,
    worker_max_memory_per_child=CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB,
    worker_max_tasks_per_child=CELERY_WORKER_MAX_TASKS_PER_CHILD,
    task_routes={
        "celery_worker.orchestrate_migration": {"queue": CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE},
        "celery_worker.call_function_child": {"queue": CELERY_MIGRATION_QUEUE},
        "celery_worker.finalize_migration_orchestration": {"queue": _resolve_finalize_queue()},
        "celery_worker.teardown_pipeline_flow_fleet": {"queue": _resolve_finalize_queue()},
        "celery_worker.call_function": {"queue": CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE},
        "celery_worker.check_job_status": {"queue": CELERY_CHECK_JOB_STATUS_QUEUE},
    },
)
if CELERY_TASK_TIME_LIMIT is not None:
    _celery_conf["task_time_limit"] = CELERY_TASK_TIME_LIMIT
if CELERY_TASK_SOFT_TIME_LIMIT is not None:
    _celery_conf["task_soft_time_limit"] = CELERY_TASK_SOFT_TIME_LIMIT
celery_app.conf.update(**_celery_conf)
celery_app.conf.timezone = "UTC"
# Reconcile-only watchdog: celery-worker-control-reconcile also bootstraps + reschedules
# check_job_status while work is active. Never enqueue reconcile ticks on migration-control.
_beat_schedule = {}
if CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE:
    _beat_schedule["check-job-status-reconcile-watchdog"] = {
        "task": "celery_worker.check_job_status",
        "schedule": float(CELERY_CHECK_JOB_STATUS_INTERVAL),
        "options": {"queue": CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE},
    }
celery_app.conf.beat_schedule = _beat_schedule

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("FASTAPI_DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL (or FASTAPI_DATABASE_URL) must be set")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_FAILED = "failed"

# ---------------------------------------------------------------------------
# Redis client — per-process singleton (reset after prefork)
# ---------------------------------------------------------------------------
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_sync.from_url(BROKER_URL, decode_responses=True)
    return _redis_client


@worker_process_init.connect
def init_worker(**kwargs):
    """Reset DB pool and Redis client after prefork so each child gets its own connection."""
    engine.dispose()
    global _redis_client, _gcs_client
    _redis_client = None
    _gcs_client = None
    reset_scale_down_after_fork()
    logger.info("WORKER_INIT | pod=%s | pid=%s", os.getenv("HOSTNAME", "unknown"), os.getpid())


# ---------------------------------------------------------------------------
# KEDA active list — tracks in-flight tasks so KEDA keeps pods alive
# ---------------------------------------------------------------------------
_KEDA_TERMINAL_STATES = frozenset({"SUCCESS", "FAILURE", "REVOKED"})


def _keda_task_id_str(task_id) -> Optional[str]:
    """Normalize ids for Redis list ops (decode_responses off on some code paths → bytes)."""
    if task_id is None:
        return None
    if isinstance(task_id, bytes):
        return task_id.decode("utf-8", errors="replace")
    s = str(task_id).strip()
    return s or None


@task_prerun.connect
def on_task_start(sender=None, task_id=None, **kwargs):
    task_name = getattr(sender, "name", "") if sender else ""
    if scale_down_protection_enabled():
        try:
            scale_down_on_task_prerun(_get_redis(), task_id=task_id, task_name=task_name)
        except Exception as exc:
            logger.warning("SCALE_DOWN_PRERUN_FAILED | task_id=%s | err=%s", task_id, exc)
    if task_name in (
        "celery_worker.call_function_child",
        "celery_worker.orchestrate_migration",
    ):
        _touch_worker_heartbeat()
    if task_name == "celery_worker.check_job_status":
        return
    tid = _keda_task_id_str(task_id)
    if not tid:
        return
    try:
        r = _get_redis()
        # Deduplicate any stale/prewarm copies so each in-flight task contributes
        # at most one entry in the active list.
        deduped = r.lrem(CELERY_KEDA_ACTIVE_LIST_KEY, 0, tid)
        length = r.lpush(CELERY_KEDA_ACTIVE_LIST_KEY, tid)
        logger.info("KEDA_ACTIVE_PUSH | task_id=%s | deduped=%s | pod=%s | list_len=%s",
                    tid, deduped, os.getenv("HOSTNAME", "unknown"), length)
    except Exception as e:
        logger.warning("KEDA push failed task_id=%s err=%s", tid, e)


@task_postrun.connect
def on_task_end(sender=None, task_id=None, state=None, args=None, **other_kwargs):
    task_name = getattr(sender, "name", "") if sender else ""
    if scale_down_protection_enabled():
        try:
            scale_down_on_task_postrun(
                _get_redis(), task_id=task_id, task_name=task_name, state=state
            )
        except Exception as exc:
            logger.warning("SCALE_DOWN_POSTRUN_FAILED | task_id=%s | err=%s", task_id, exc)
    if task_name == "celery_worker.check_job_status":
        return
    tid = _keda_task_id_str(task_id)
    if not tid:
        return
    try:
        r = _get_redis()
        # Remove all occurrences: task IDs can be pushed multiple times
        # (fanout prewarm + task_prerun + retries), and leaving duplicates
        # inflates KEDA desired replicas.
        removed = r.lrem(CELERY_KEDA_ACTIVE_LIST_KEY, 0, tid)
        remaining = r.llen(CELERY_KEDA_ACTIVE_LIST_KEY)
        logger.info("KEDA_ACTIVE_REMOVE | task_id=%s | removed=%s | pod=%s | list_len=%s",
                    tid, removed, os.getenv("HOSTNAME", "unknown"), remaining)
        # celery-beat-migration runs celery_worker.conf.beat_schedule; Django beat does not.
        # Still prune here so terminal ids clear without waiting for the next tick (missed postrun / kill).
        if CELERY_KEDA_ACTIVE_PRUNE_ON_POSTRUN and remaining:
            _prune_terminal_ids_from_keda_active_list("KEDA_ACTIVE_TERMINAL_PRUNED_POSTRUN")
        if task_name == "celery_worker.call_function_child" and state in _KEDA_TERMINAL_STATES:
            payload = _load_payload(tid)
            job_name = (payload or {}).get("job_name")
            if not job_name:
                task_args = args or ()
                if len(task_args) >= 4:
                    job_name = task_args[3]
            if job_name and _fanout_stagger_active(job_name):
                _release_fanout_slot(job_name, tid)
    except Exception as e:
        logger.warning("KEDA remove failed task_id=%s err=%s", tid, e)


def _diagnose_pending_child(queue_depth: int, active_depth: int) -> str:
    """Classify why a child is still PENDING for reconcile decisions."""
    if queue_depth > 0:
        if active_depth > CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD:
            return "KEDA_ACTIVE_LIST_INFLATED"
        return "IN_QUEUE_WAITING_FOR_KEDA_SCALE"
    if active_depth > 0:
        return "DEQUEUED_NOT_STARTED_prefetch_hold"
    return "TASK_INVISIBLE_likely_lost"


def _pending_requeue_skip_reason(diagnosis: str) -> Optional[str]:
    """
    Reasons to wait instead of requeue/fail — requeue burns the cap while workers scale.
    """
    if diagnosis == "DEQUEUED_NOT_STARTED_prefetch_hold":
        return "prefetch_hold_wait_for_slot"
    if diagnosis in ("IN_QUEUE_WAITING_FOR_KEDA_SCALE", "KEDA_ACTIVE_LIST_INFLATED"):
        return "wait_for_keda_scale"
    return None


def _prune_terminal_ids_from_keda_active_list(
    log_label: str,
    *,
    max_ids: Optional[int] = None,
) -> int:
    """
    Remove Redis KEDA sentinel ids whose Celery result state is already terminal.

    Needed when migration_record still references a completed task_id (finalizer /
    DB lag) so the reconcile loop must not skip LREM for ids that remain in the list.
    """
    r = _get_redis()
    current = r.lrange(CELERY_KEDA_ACTIVE_LIST_KEY, 0, -1) or []
    if max_ids is not None and max_ids > 0:
        current = current[:max_ids]
    pruned = 0
    for raw_tid in current:
        tid = _keda_task_id_str(raw_tid)
        if not tid:
            continue
        try:
            st = celery_app.AsyncResult(tid).state
        except Exception:
            st = None
        if st in _KEDA_TERMINAL_STATES:
            pruned += int(r.lrem(CELERY_KEDA_ACTIVE_LIST_KEY, 0, tid) or 0)
    if pruned:
        logger.warning(
            "%s | removed=%s | pod=%s | list_len_after=%s",
            log_label,
            pruned,
            os.getenv("HOSTNAME", "unknown"),
            r.llen(CELERY_KEDA_ACTIVE_LIST_KEY),
        )
    return pruned


def _rebuild_keda_active_list(live_ids: set[str], log_label: str) -> int:
    """Replace celery:keda:active with only non-terminal in-flight task ids."""
    r = _get_redis()
    before = int(r.llen(CELERY_KEDA_ACTIVE_LIST_KEY) or 0)
    normalized = sorted({_keda_task_id_str(tid) for tid in (live_ids or set()) if _keda_task_id_str(tid)})
    pipe = r.pipeline()
    pipe.delete(CELERY_KEDA_ACTIVE_LIST_KEY)
    if normalized:
        pipe.lpush(CELERY_KEDA_ACTIVE_LIST_KEY, *normalized)
    pipe.execute()
    after = int(r.llen(CELERY_KEDA_ACTIVE_LIST_KEY) or 0)
    logger.warning(
        "%s | before=%s | after=%s | live_ids=%s | pod=%s",
        log_label,
        before,
        after,
        len(normalized),
        os.getenv("HOSTNAME", "unknown"),
    )
    return max(0, before - after)


def _repair_inflated_keda_active_list(
    live_ids: Optional[set[str]] = None,
) -> int:
    """
    Prune terminal KEDA ids and rebuild the list when it is far larger than live work.
    """
    r = _get_redis()
    length = int(r.llen(CELERY_KEDA_ACTIVE_LIST_KEY) or 0)
    if length <= CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD:
        return 0

    repaired = _prune_terminal_ids_from_keda_active_list(
        "KEDA_ACTIVE_TERMINAL_PRUNED_INFLATED",
        max_ids=CELERY_KEDA_ACTIVE_PRUNE_BATCH_SIZE,
    )
    length = int(r.llen(CELERY_KEDA_ACTIVE_LIST_KEY) or 0)
    if length <= CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD:
        return repaired

    if length >= CELERY_KEDA_ACTIVE_FORCE_PRUNE_LEN and live_ids is not None:
        rebuilt = _rebuild_keda_active_list(live_ids, "KEDA_ACTIVE_LIST_REBUILT")
        return repaired + rebuilt

    logger.warning(
        "KEDA_ACTIVE_LIST_STILL_INFLATED | list_len=%s | threshold=%s | force_prune_len=%s | "
        "live_ids=%s | pod=%s",
        length,
        CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD,
        CELERY_KEDA_ACTIVE_FORCE_PRUNE_LEN,
        len(live_ids or ()),
        os.getenv("HOSTNAME", "unknown"),
    )
    return repaired


def _pods_for_table_count(table_count: int) -> int:
    """Pods for one job from table count (matches --concurrency slots per pod)."""
    n = max(0, int(table_count))
    if n == 0:
        return 0
    return min(
        CELERY_KEDA_MAX_REPLICAS,
        max(1, math.ceil(n / CELERY_WORKER_CONCURRENCY)),
    )


def _desired_pods_from_workload(job_table_counts: dict[str, int]) -> int:
    if not job_table_counts:
        return 0
    return min(
        CELERY_KEDA_MAX_REPLICAS,
        sum(_pods_for_table_count(c) for c in job_table_counts.values()),
    )


def _normalize_company_plan(plan: Optional[str]) -> str:
    return (plan or "").strip().lower()


def _plan_pod_cap(plan: Optional[str], pod_cap: Optional[int] = None) -> int:
    if pod_cap is not None:
        cap = int(pod_cap)
        if cap > 0:
            return max(1, min(cap, CELERY_KEDA_MAX_PODS_PER_JOB))
    normalized = _normalize_company_plan(plan)
    # Fallback when DB plan join is unavailable (legacy string plan on company).
    plan_caps = {
        "free": 2,
        "standard": 2,
        "enterprise": 6,
        "premium": 4,
        "discounted": 2,
    }
    return max(
        1,
        min(
            int(plan_caps.get(normalized, CELERY_KEDA_MAX_PODS_PER_JOB)),
            CELERY_KEDA_MAX_PODS_PER_JOB,
        ),
    )


def _capped_pods_for_job(
    table_count: int,
    plan: Optional[str] = None,
    pod_cap: Optional[int] = None,
    resource_config: Optional[resource_cfg.EffectiveResourceConfig] = None,
) -> int:
    if resource_config is not None:
        return resource_cfg.compute_execution_plan(resource_config, table_count).effective_pods
    return job_pool.compute_effective_pods(table_count, plan, pod_cap=pod_cap)


def _child_task_queue(job_name: str) -> str:
    return job_pool.migration_queue_for_job(job_name, CELERY_MIGRATION_QUEUE)


def _control_task_queue() -> str:
    return job_pool.control_queue_name()


def _reconcile_time_exceeded(deadline: Optional[float]) -> bool:
    return deadline is not None and time.monotonic() > deadline


def _check_job_status_schedule_lock_ttl() -> int:
    return max(5, CELERY_CHECK_JOB_STATUS_INTERVAL - 3)


def _check_job_status_running_lock_ttl() -> int:
    return max(60, CELERY_CHECK_JOB_STATUS_INTERVAL * 3)


def _acquire_check_job_status_running_lock(r) -> bool:
    holder = f"{os.getenv('HOSTNAME', 'unknown')}:{os.getpid()}"
    return bool(
        r.set(
            CELERY_CHECK_JOB_STATUS_RUNNING_LOCK_KEY,
            holder,
            nx=True,
            ex=_check_job_status_running_lock_ttl(),
        )
    )


def _release_check_job_status_running_lock(r) -> None:
    try:
        r.delete(CELERY_CHECK_JOB_STATUS_RUNNING_LOCK_KEY)
    except Exception:
        pass


def _schedule_check_job_status(r, countdown=None) -> bool:
    delay = CELERY_CHECK_JOB_STATUS_INTERVAL if countdown is None else countdown
    try:
        lock = r.set(
            CELERY_CHECK_JOB_STATUS_SCHEDULE_LOCK_KEY,
            "1",
            nx=True,
            ex=_check_job_status_schedule_lock_ttl(),
        )
        if not lock:
            return False
        reconcile_queue = _resolve_check_job_status_queue()
        check_job_status.apply_async(
            countdown=delay,
            queue=reconcile_queue,
            priority=CELERY_CHECK_JOB_STATUS_PRIORITY,
        )
        return True
    except Exception:
        reconcile_queue = _resolve_check_job_status_queue()
        check_job_status.apply_async(
            countdown=delay,
            queue=reconcile_queue,
            priority=CELERY_CHECK_JOB_STATUS_PRIORITY,
        )
        return True


def _should_fail_stale_pending_no_payload(age_secs: float) -> bool:
    """
    Fail only when migration_record has been idle longer than the long-run threshold.

    age_secs is (now - migration_record.updated_at). Progress / checkpoint writers bump
    updated_at so healthy multi-day children never look stale.
    """
    return age_secs > CELERY_STALE_NO_PAYLOAD_SECONDS


def _provision_failure_message(r, job_name: str) -> str:
    """Particular reason a deferred worker never started, for the migration log and UI."""
    reason = ""
    try:
        # Deferred flow fleets store the payload on the run identity
        # (pipeline_run_{id}), not the per-node job name.
        keys = [f"{job_pool.PROVISION_PAYLOAD_PREFIX}{job_name}"]
        identity = job_pool.fleet_key_for_job(job_name)
        if identity and identity != job_name:
            keys.append(f"{job_pool.PROVISION_PAYLOAD_PREFIX}{identity}")
        for key in keys:
            raw = r.get(key)
            if not raw:
                continue
            payload = json.loads(raw)
            reason = str(payload.get("reason") or "").strip()
            if reason:
                break
    except Exception:
        reason = ""
    details = {
        "company_job_cap": "this organization is already at its concurrent job limit",
        "company_pod_cap": "this organization is already at its worker pod limit",
        "company_rate_limit": "this organization hit its worker start rate limit",
        "rate_limit": "the cluster worker start rate limit was hit",
    }
    detail = details.get(reason) or (
        f"provision reason {reason}" if reason else "the job worker did not become ready"
    )
    return f"Migration failed: job worker was not started ({detail})."


def _awaiting_fanout_provision_expired(state: dict) -> bool:
    waiting_since = state.get("waiting_since")
    if not waiting_since:
        return False
    try:
        started = datetime.fromisoformat(str(waiting_since).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started).total_seconds()
        return age > job_pool.CELERY_JOB_WORKER_READY_TIMEOUT
    except Exception:
        return False


def _resume_awaiting_fanouts(r) -> int:
    """Resume fan-out for jobs that were waiting on deferred worker provision (RFC §9.2)."""
    resumed = 0
    for job_name in job_pool.list_awaiting_fanout_jobs(r):
        state = job_pool.load_awaiting_fanout_state(r, job_name)
        if not state:
            job_pool.clear_awaiting_fanout_state(r, job_name)
            continue
        if _awaiting_fanout_provision_expired(state):
            # When another org flow still holds the plan job/pod slot, keep
            # waiting instead of failing tables with company_job_cap.
            defer_reason = ""
            try:
                keys = [f"{job_pool.PROVISION_PAYLOAD_PREFIX}{job_name}"]
                identity = job_pool.fleet_key_for_job(job_name)
                if identity and identity != job_name:
                    keys.append(f"{job_pool.PROVISION_PAYLOAD_PREFIX}{identity}")
                for key in keys:
                    raw = r.get(key)
                    if not raw:
                        continue
                    defer_reason = str(json.loads(raw).get("reason") or "").strip()
                    if defer_reason:
                        break
            except Exception:
                defer_reason = ""
            if defer_reason in job_pool.COMPANY_DEFER_REASONS:
                job_pool.save_awaiting_fanout_state(
                    r,
                    job_name,
                    child_ids=state.get("child_ids") or [],
                    max_inflight=int(
                        state.get("max_inflight") or CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB
                    ),
                    use_staggered=bool(state.get("use_staggered")),
                    connection_id=state.get("connection_id"),
                    refresh_waiting_since=True,
                )
                logger.warning(
                    "FANOUT_PROVISION_WAIT_CAPACITY | job=%s | reason=%s",
                    job_name,
                    defer_reason,
                )
                continue
            connection_id = state.get("connection_id")
            update_job_status(
                job_name,
                STATUS_FAILED,
                connection_id,
                _provision_failure_message(r, job_name),
            )
            if job_pool.is_flow_fleet_job(job_name):
                job_pool.release_flow_fleet_lease(job_name, r=r)
            else:
                job_pool.teardown_job_workers(job_name, fast=True, r=r)
            job_pool.clear_awaiting_fanout_state(r, job_name)
            logger.error(
                "FANOUT_PROVISION_TIMEOUT | job=%s | waiting_since=%s",
                job_name,
                state.get("waiting_since"),
            )
            continue
        if not job_pool.job_worker_ready_now(job_name, r=r):
            continue
        child_ids = state.get("child_ids") or []
        max_inflight = int(state.get("max_inflight") or CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB)
        if state.get("use_staggered"):
            _init_fanout_stagger(job_name, child_ids, max_inflight=max_inflight)
        else:
            for child_id in child_ids:
                _publish_child_task(child_id)
        job_pool.clear_awaiting_fanout_state(r, job_name)
        _schedule_check_job_status(r, countdown=10)
        logger.warning(
            "FANOUT_RESUMED_AFTER_PROVISION | job=%s | children=%s | max_inflight=%s",
            job_name,
            len(child_ids),
            max_inflight,
        )
        resumed += 1
    return resumed


def _cleanup_job_resource_state(job_name: str) -> None:
    try:
        resource_cfg.delete_job_execution_config(_get_redis(), job_name)
    except Exception as exc:
        logger.debug("JOB_RESOURCE_STATE_CLEANUP_FAILED | job=%s | err=%s", job_name, exc)


def _teardown_job_workers_if_isolated(job_name: str) -> None:
    _cleanup_job_resource_state(job_name)
    if not job_pool.job_isolation_enabled():
        return
    try:
        if job_pool.is_flow_fleet_job(job_name):
            job_pool.release_flow_fleet_lease(job_name, r=_get_redis())
            return
        job_pool.teardown_job_workers(job_name, fast=True, r=_get_redis())
    except Exception as exc:
        logger.warning("JOB_WORKER_TEARDOWN_FAILED | job=%s | err=%s", job_name, exc)


def _desired_pods_from_workload_with_plan(
    job_table_counts: dict[str, int],
    job_plans: Optional[dict[str, str]] = None,
    job_pod_caps: Optional[dict[str, int]] = None,
) -> int:
    if not job_table_counts:
        return 0
    plans = job_plans or {}
    caps = job_pod_caps or {}
    desired = 0
    for job_name, table_count in job_table_counts.items():
        desired += _capped_pods_for_job(
            table_count,
            plans.get(job_name),
            caps.get(job_name),
        )
    return min(CELERY_KEDA_MAX_REPLICAS, desired)


def _desired_pods_jobs_mode(job_count: int) -> int:
    return min(max(int(job_count), 0) * CELERY_KEDA_PODS_PER_JOB, CELERY_KEDA_MAX_REPLICAS)


def _count_in_progress_job_workload_and_plan(db) -> dict[str, dict[str, object]]:
    rows = db.execute(
        text(
            """
            SELECT mr.job_name,
                   COUNT(*) AS cnt,
                   COALESCE(MAX(c.plan), '') AS plan,
                   GREATEST(
                       COALESCE(MAX(p.pod_count), 1),
                       CASE
                           WHEN COALESCE(MAX(c.pod_count), 0) > COALESCE(MAX(p.pod_count), 1)
                           THEN COALESCE(MAX(c.pod_count), 0)
                           ELSE 0
                       END
                   ) AS pod_cap
            FROM migration_record mr
            JOIN connection cn ON cn.connection_id = mr.connection_id
            LEFT JOIN company c ON c.cmp_id = cn.cmp_id
            LEFT JOIN plans p ON p.plan_id = c.plan_id
            WHERE mr.status = :s
            GROUP BY mr.job_name
            """
        ),
        {"s": STATUS_IN_PROGRESS},
    ).fetchall()
    return {
        r[0]: {
            "table_count": int(r[1] or 0),
            "plan": str(r[2] or ""),
            "pod_cap": int(r[3] or 1),
        }
        for r in rows
    }


def _count_in_progress_tables_per_job(db) -> dict[str, int]:
    workload = _count_in_progress_job_workload_and_plan(db)
    return {job_name: int(meta.get("table_count") or 0) for job_name, meta in workload.items()}


def _count_in_progress_plans_per_job(db) -> dict[str, str]:
    workload = _count_in_progress_job_workload_and_plan(db)
    return {job_name: str(meta.get("plan") or "") for job_name, meta in workload.items()}


def _compute_desired_migration_pods(
    job_count: int,
    job_table_counts: Optional[dict[str, int]] = None,
    job_plans: Optional[dict[str, str]] = None,
    job_pod_caps: Optional[dict[str, int]] = None,
) -> int:
    jobs_desired = _desired_pods_jobs_mode(job_count)
    if CELERY_KEDA_SCALE_MODE != "workload":
        return jobs_desired
    # Strict workload mode: plan-capped per-job pods only (no jobs×N hybrid floor override).
    workload_desired = _desired_pods_from_workload_with_plan(
        job_table_counts or {},
        job_plans=job_plans or {},
        job_pod_caps=job_pod_caps or {},
    )
    return min(workload_desired, CELERY_KEDA_MAX_REPLICAS)


def _set_active_migration_pods(
    r,
    job_count: int = 0,
    *,
    job_table_counts: Optional[dict[str, int]] = None,
    job_plans: Optional[dict[str, str]] = None,
    job_pod_caps: Optional[dict[str, int]] = None,
) -> int:
    """
    Write desired celery-worker replica count for KEDA.

    Uses a Redis LIST (celery:keda:desired-replicas) with LLEN = desired pods so KEDA's
    list scaler with listLength=1 maps desired count to replica target. Also sets
    celery:active-migration-count for ops visibility.
    """
    counts = job_table_counts or {}
    plans = job_plans or {}
    pod_caps = job_pod_caps or {}
    workload_desired = _compute_desired_migration_pods(
        job_count, counts, job_plans=plans, job_pod_caps=pod_caps
    )
    desired = floor_desired_replicas_by_busy_pods(workload_desired, r)
    per_job_caps = None
    if counts:
        per_job_caps = {
            job_name: {
                "table_count": int(table_count),
                "plan": plans.get(job_name),
                "pod_cap": pod_caps.get(job_name),
                "plan_cap": _plan_pod_cap(
                    plans.get(job_name), pod_cap=pod_caps.get(job_name)
                ),
                "base_pods": _pods_for_table_count(table_count),
                "capped_pods": _capped_pods_for_job(
                    table_count, plans.get(job_name), pod_cap=pod_caps.get(job_name)
                ),
            }
            for job_name, table_count in counts.items()
        }
    r.set(CELERY_ACTIVE_MIGRATION_COUNT_KEY, str(desired))
    r.delete(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY)
    if desired > 0:
        r.lpush(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY, *(["1"] * desired))
    list_len = r.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) or 0
    logger.warning(
        "KEDA_DESIRED_REPLICAS_SET | mode=%s | jobs=%s | tables_by_job=%s | plans_by_job=%s | caps_by_job=%s | "
        "workload_desired=%s | desired_pods=%s | list_key=%s | list_len=%s | pod=%s",
        CELERY_KEDA_SCALE_MODE,
        job_count,
        counts if counts else None,
        plans if plans else None,
        per_job_caps,
        workload_desired,
        desired,
        CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY,
        list_len,
        os.getenv("HOSTNAME", "unknown"),
    )
    return desired


def _count_in_progress_jobs(db) -> int:
    row = db.execute(
        text("SELECT COUNT(DISTINCT job_name) FROM migration_record WHERE status = :s"),
        {"s": STATUS_IN_PROGRESS},
    ).scalar()
    return int(row or 0)


def _reconcile_scale_down_protection(r) -> None:
    """Reconcile per-pod Redis active_tasks + deletion cost with Celery inspect."""
    if not scale_down_protection_enabled():
        return
    try:
        reconcile_pod_active_tasks_from_inspect(r, celery_app)
    except Exception as exc:
        logger.warning("SCALE_DOWN_RECONCILE_FAILED | err=%s", exc)


def _sync_active_migration_count_from_db(r, db) -> int:
    """Refresh KEDA migration counter from DB ground truth."""
    workload = _count_in_progress_job_workload_and_plan(db)
    job_table_counts = {
        job_name: int(meta.get("table_count") or 0)
        for job_name, meta in workload.items()
    }
    job_plans = {
        job_name: str(meta.get("plan") or "")
        for job_name, meta in workload.items()
    }
    job_pod_caps = {
        job_name: int(meta.get("pod_cap") or 1)
        for job_name, meta in workload.items()
    }
    job_count = len(job_table_counts)
    return _set_active_migration_pods(
        r,
        job_count,
        job_table_counts=job_table_counts,
        job_plans=job_plans,
        job_pod_caps=job_pod_caps,
    )


def _keda_bulk_push_candidates(
    live_task_ids: list[str],
    redis_ids: set[str],
    max_ids: Optional[int] = None,
) -> list[str]:
    """Task ids in live_task_ids missing from redis_ids, capped for reconcile LPUSH."""
    missing = sorted(tid for tid in live_task_ids if tid and tid not in redis_ids)
    cap = CELERY_KEDA_ACTIVE_SYNC_MAX_IDS if max_ids is None else max_ids
    if len(missing) > cap:
        logger.warning(
            "KEDA_ACTIVE_SYNC_CAP_HIT | missing=%s | cap=%s | pod=%s",
            len(missing),
            cap,
            os.getenv("HOSTNAME", "unknown"),
        )
        return missing[:cap]
    return missing


# ---------------------------------------------------------------------------
# Child payload store — persists task args for requeue recovery
# ---------------------------------------------------------------------------
def _payload_key(task_id: str) -> str:
    return f"{CELERY_CHILD_PAYLOAD_KEY_PREFIX}:{task_id}"


def _save_payload(task_id: str, payload: dict):
    try:
        _get_redis().set(
            _payload_key(task_id),
            json.dumps(payload),
            ex=CELERY_CHILD_PAYLOAD_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("Failed to save payload task_id=%s err=%s", task_id, e)


def _load_payload(task_id: str):
    try:
        raw = _get_redis().get(_payload_key(task_id))
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning("Failed to load payload task_id=%s err=%s", task_id, e)
        return None


def _delete_payload(task_id: str):
    try:
        _get_redis().delete(_payload_key(task_id))
    except Exception:
        pass


def _touch_payload(task_id: Optional[str]) -> None:
    """Renew child payload TTL so multi-day tasks do not lose requeue metadata."""
    if not task_id:
        return
    try:
        r = _get_redis()
        key = _payload_key(task_id)
        if r.exists(key):
            r.expire(key, CELERY_CHILD_PAYLOAD_TTL_SECONDS)
    except Exception as e:
        logger.debug("PAYLOAD_TOUCH_FAILED | task_id=%s | err=%s", task_id, e)


def _worker_heartbeat_key(pod_name: str) -> str:
    return f"{CELERY_WORKER_HEARTBEAT_KEY_PREFIX}{pod_name}"


def _touch_worker_heartbeat():
    """Register this pod as alive (refreshed by check_job_status, worker_ready, and long child tasks)."""
    pod = os.getenv("HOSTNAME", "unknown")
    if not pod or pod == "unknown":
        return
    try:
        _get_redis().set(
            _worker_heartbeat_key(pod),
            datetime.now(timezone.utc).isoformat(),
            ex=CELERY_WORKER_HEARTBEAT_TTL_SECONDS,
        )
    except Exception as e:
        logger.debug("WORKER_HEARTBEAT_FAILED | pod=%s | err=%s", pod, e)


@contextmanager
def _worker_heartbeat_while_busy(task_id: Optional[str] = None):
    """Keep Redis worker heartbeat (+ child payload TTL) fresh during long connector work."""
    _touch_worker_heartbeat()
    _touch_payload(task_id)
    stop = Event()
    interval = max(
        15,
        min(
            CELERY_WORKER_HEARTBEAT_REFRESH_INTERVAL_SECONDS,
            max(CELERY_WORKER_HEARTBEAT_TTL_SECONDS - 30, 30),
        ),
    )

    def _refresh_loop():
        while not stop.wait(interval):
            _touch_worker_heartbeat()
            _touch_payload(task_id)

    thread = Thread(target=_refresh_loop, name="celery-worker-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.0)
        _touch_worker_heartbeat()
        _touch_payload(task_id)


_CHILD_EXEC_LOCK_PREFIX = "celery:child-exec-lock:"
_CHILD_EXEC_LOCK_COMPLETED = "completed"


def _child_exec_lock_key(task_id: str) -> str:
    return f"{_CHILD_EXEC_LOCK_PREFIX}{task_id}"


def _child_exec_lock_ttl_seconds() -> int:
    """TTL must exceed broker visibility and multi-day Dynamics table runs."""
    return max(
        CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
        MIGRATION_LONG_RUN_SECONDS,
        86_400,
    )


def _try_acquire_child_exec_lock(task_id: str, owner: str) -> bool:
    """
    True when this worker may execute the child task body.

    With task_acks_late=True, Redis can redeliver the same message after ~3600s while the
    first worker is still running. A second delivery must not re-run process_table (it would
    drop the in-use staging table via prechunk_staging_cleanup).
    """
    try:
        r = _get_redis()
        key = _child_exec_lock_key(task_id)
        if r.set(key, owner, nx=True, ex=_child_exec_lock_ttl_seconds()):
            return True
        existing = r.get(key)
        if isinstance(existing, bytes):
            existing = existing.decode("utf-8", errors="replace")
        if existing == _CHILD_EXEC_LOCK_COMPLETED:
            logger.warning(
                "CHILD_EXEC_LOCK_ALREADY_COMPLETED | task_id=%s | owner=%s",
                task_id,
                owner,
            )
        return False
    except Exception as exc:
        logger.warning("CHILD_EXEC_LOCK_ACQUIRE_FAILED | task_id=%s | err=%s", task_id, exc)
        return True


def _get_child_exec_lock_holder(task_id: str) -> Optional[str]:
    try:
        raw = _get_redis().get(_child_exec_lock_key(task_id))
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return raw
    except Exception:
        return None


def _release_child_exec_lock(task_id: str, owner: str) -> None:
    try:
        r = _get_redis()
        key = _child_exec_lock_key(task_id)
        current = r.get(key)
        if isinstance(current, bytes):
            current = current.decode("utf-8", errors="replace")
        if current == owner:
            r.delete(key)
    except Exception as exc:
        logger.debug("CHILD_EXEC_LOCK_RELEASE_FAILED | task_id=%s | err=%s", task_id, exc)


def _mark_child_exec_lock_completed(task_id: str) -> None:
    try:
        _get_redis().set(
            _child_exec_lock_key(task_id),
            _CHILD_EXEC_LOCK_COMPLETED,
            ex=7 * 86_400,
        )
    except Exception as exc:
        logger.debug("CHILD_EXEC_LOCK_COMPLETE_MARK_FAILED | task_id=%s | err=%s", task_id, exc)


def _is_worker_pod_alive(pod_name: str) -> bool:
    if not pod_name or pod_name == "unknown":
        return True
    try:
        return bool(_get_redis().exists(_worker_heartbeat_key(pod_name)))
    except Exception:
        return True


def _k8s_namespace() -> str:
    if CELERY_K8S_NAMESPACE:
        return CELERY_K8S_NAMESPACE
    try:
        with open(_K8S_SA_NAMESPACE_PATH, encoding="utf-8") as fh:
            return fh.read().strip() or "default"
    except OSError:
        return "default"


def _k8s_api_request(path: str, timeout: float = 5.0):
    if not CELERY_K8S_POD_STATUS_ENABLED:
        return None
    if not os.path.isfile(_K8S_SA_TOKEN_PATH):
        return None
    try:
        with open(_K8S_SA_TOKEN_PATH, encoding="utf-8") as fh:
            token = fh.read().strip()
        url = f"https://{_K8S_API_HOST}:{_K8S_API_PORT}{path}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            verify=_K8S_SA_CA_PATH if os.path.isfile(_K8S_SA_CA_PATH) else True,
            timeout=timeout,
        )
        if resp.status_code == 404:
            return {"_http_status": 404}
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("K8S_API_REQUEST_FAILED | path=%s | err=%s", path, exc)
        return None


def _parse_pod_termination_from_k8s_body(body: dict) -> dict:
    """Extract termination reason from a Pod status object (pure, testable)."""
    result = {
        "oom_killed": False,
        "termination_reason": "Unknown",
        "exit_code": None,
        "detail": "",
        "source": "pod_status",
    }
    if not body:
        return result

    pod_status = body.get("status") or {}
    pod_reason = pod_status.get("reason") or ""
    pod_message = pod_status.get("message") or ""
    if pod_reason:
        result["detail"] = f"{pod_reason}: {pod_message}".strip(": ")

    best = None
    for status_key in ("containerStatuses", "initContainerStatuses"):
        for container in pod_status.get(status_key) or []:
            terminated = (container.get("lastState") or {}).get("terminated") or {}
            if not terminated:
                continue
            reason = str(terminated.get("reason") or "")
            exit_code = terminated.get("exitCode")
            candidate = (reason, exit_code, container.get("name") or status_key)
            if best is None or reason in ("OOMKilled", "Error"):
                best = candidate

    if best:
        reason, exit_code, container_name = best
        result["termination_reason"] = reason or "Unknown"
        result["exit_code"] = exit_code
        if reason == "OOMKilled" or exit_code == 137:
            result["oom_killed"] = True
        container_detail = f"container={container_name} reason={reason} exit_code={exit_code}"
        result["detail"] = "; ".join(x for x in (result["detail"], container_detail) if x)
    elif pod_reason == "Evicted":
        result["termination_reason"] = "Evicted"
    elif pod_reason:
        result["termination_reason"] = pod_reason

    return result


def _parse_pod_events_for_oom(events_body: dict) -> dict:
    """Fallback when pod object is gone: scan K8s events for OOMKilling."""
    result = {
        "oom_killed": False,
        "termination_reason": "Unknown",
        "exit_code": 137,
        "detail": "",
        "source": "events",
    }
    items = (events_body or {}).get("items") or []
    for event in reversed(items):
        reason = str(event.get("reason") or "")
        message = str(event.get("message") or "")
        combined = f"{reason} {message}".lower()
        if "oom" in combined or "out of memory" in combined:
            result["oom_killed"] = True
            result["termination_reason"] = reason or "OOMKilling"
            result["detail"] = message or reason
            return result
    return result


def _pod_termination_info_unavailable() -> dict:
    return {
        "oom_killed": False,
        "termination_reason": "Unknown",
        "exit_code": None,
        "detail": "k8s_api_unavailable",
        "source": "unavailable",
    }


def _pod_termination_info_missing(pod_name: str) -> dict:
    gone = _pod_termination_info_unavailable()
    gone["termination_reason"] = "PodNotFound"
    gone["detail"] = f"pod {pod_name} no longer exists (likely deleted after termination)"
    gone["source"] = "pod_missing"
    return gone


def _enrich_termination_info_from_events(namespace: str, pod_name: str, parsed: dict) -> dict:
    if parsed.get("oom_killed"):
        return parsed
    events_body = _k8s_api_request(
        f"/api/v1/namespaces/{namespace}/events"
        f"?fieldSelector=involvedObject.name={pod_name}"
    )
    if events_body:
        event_parsed = _parse_pod_events_for_oom(events_body)
        if event_parsed.get("oom_killed"):
            return event_parsed
    return parsed


def _get_pod_status_snapshot(pod_name: str) -> dict:
    """
    Single K8s pod read for orphan-requeue decisions and death logging.
    Returns exists (False/True/None), phase, termination_info, k8s_available.
    """
    snapshot = {
        "exists": None,
        "phase": None,
        "termination_info": _pod_termination_info_unavailable(),
        "k8s_available": False,
    }
    if not pod_name or pod_name == "unknown":
        return snapshot

    namespace = _k8s_namespace()
    pod_body = _k8s_api_request(f"/api/v1/namespaces/{namespace}/pods/{pod_name}")
    if pod_body is None:
        return snapshot

    snapshot["k8s_available"] = True
    if pod_body.get("_http_status") == 404:
        snapshot["exists"] = False
        snapshot["termination_info"] = _enrich_termination_info_from_events(
            namespace, pod_name, _pod_termination_info_missing(pod_name),
        )
        return snapshot

    snapshot["exists"] = True
    snapshot["phase"] = (pod_body.get("status") or {}).get("phase") or "Unknown"
    parsed = _enrich_termination_info_from_events(
        namespace, pod_name, _parse_pod_termination_from_k8s_body(pod_body),
    )
    snapshot["termination_info"] = parsed
    return snapshot


def _fetch_pod_termination_info(pod_name: str) -> dict:
    """Best-effort: why did this worker pod disappear (OOMKilled, Evicted, etc.)?"""
    if not pod_name or pod_name == "unknown":
        return _pod_termination_info_unavailable()
    return _get_pod_status_snapshot(pod_name)["termination_info"]


def _should_orphan_requeue_assigned_pod(pod_name: str) -> tuple[bool, str, dict]:
    """
    Decide if a STARTED child with a stale heartbeat should be orphan-requeued.

    Returns (should_requeue, skip_reason, termination_info).
    skip_reason is set when should_requeue is False (for ORPHAN_REQUEUE_SKIPPED logs).
    """
    snapshot = _get_pod_status_snapshot(pod_name)
    termination_info = snapshot["termination_info"]
    if not CELERY_ORPHAN_REQUEUE_REQUIRE_K8S_CONFIRMATION:
        return True, "", termination_info

    if not CELERY_K8S_POD_STATUS_ENABLED:
        return False, "k8s_disabled", termination_info

    if not snapshot["k8s_available"]:
        return False, "k8s_api_unavailable", termination_info

    if snapshot["exists"] is False:
        return True, "", termination_info

    if termination_info.get("oom_killed"):
        return True, "", termination_info

    phase = str(snapshot.get("phase") or "").strip()
    if phase in ("Running", "Pending"):
        return False, f"k8s_pod_{phase.lower()}", termination_info

    if phase in ("Failed", "Succeeded"):
        return True, "", termination_info

    reason = str(termination_info.get("termination_reason") or "")
    if reason and reason not in ("Unknown", ""):
        return True, "", termination_info

    if snapshot["exists"]:
        return False, "k8s_pod_exists_not_gone", termination_info

    return False, "k8s_inconclusive", termination_info


def _requeue_reason_for_pod_death(termination_info: dict) -> str:
    if termination_info.get("oom_killed"):
        return "worker_pod_oom_killed"
    reason = str(termination_info.get("termination_reason") or "").lower()
    if reason == "evicted":
        return "worker_pod_evicted"
    return "worker_pod_gone"


def _insert_error_log_for_worker_death(
    connection_id,
    payload: dict,
    message: str,
    function_name: str = "celery_worker_orphan_requeue",
) -> None:
    try:
        from services.dynamics.as_source.to_snowflake import insert_error_log
    except ImportError:
        logger.debug("WORKER_DEATH_ERROR_LOG_SKIP | reason=insert_error_log_import_failed")
        return
    data = payload.get("data") or {}
    cmp_id = data.get("cmp_id")
    if cmp_id is None or connection_id is None:
        return
    try:
        insert_error_log(int(cmp_id), int(connection_id), message, function_name)
    except Exception as exc:
        logger.warning("WORKER_DEATH_ERROR_LOG_FAILED | err=%s", exc)


def _record_worker_pod_death(
    *,
    job_name: str,
    connection_id,
    task_id: str,
    assigned_pod: str,
    payload: dict,
    termination_info: dict,
) -> None:
    """Log + persist worker pod death (especially OOMKilled) for ops and UI error feeds."""
    oom_killed = bool(termination_info.get("oom_killed"))
    reason = termination_info.get("termination_reason") or "Unknown"
    exit_code = termination_info.get("exit_code")
    detail = termination_info.get("detail") or ""
    requeue_count = int(payload.get("requeue_count") or 0)

    death_record = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "job_name": job_name,
        "task_id": task_id,
        "assigned_pod": assigned_pod,
        "oom_killed": oom_killed,
        "termination_reason": reason,
        "exit_code": exit_code,
        "detail": detail,
        "source": termination_info.get("source"),
        "requeue_count": requeue_count,
        "detected_by_pod": os.getenv("HOSTNAME", "unknown"),
    }
    try:
        r = _get_redis()
        r.set(
            f"{CELERY_WORKER_DEATH_REDIS_KEY_PREFIX}{job_name}",
            json.dumps(death_record, ensure_ascii=True),
            ex=CELERY_WORKER_DEATH_REDIS_TTL_SECONDS,
        )
    except Exception as exc:
        logger.debug("WORKER_DEATH_REDIS_FAILED | job=%s | err=%s", job_name, exc)

    human_msg = (
        f"Celery worker pod {assigned_pod} died while migrating {job_name}. "
        f"Reason: {reason}"
        + (f" (exit {exit_code})" if exit_code is not None else "")
        + (f". {detail}" if detail else "")
        + ". Task will be requeued automatically."
    )
    if oom_killed:
        job_metrics.inc_oom(job_pool.job_hash(job_name))
        logger.error(
            "WORKER_POD_OOMKILLED | job=%s | task_id=%s | assigned_pod=%s | "
            "exit_code=%s | detail=%s | requeue_count=%s | pod=%s",
            job_name,
            task_id,
            assigned_pod,
            exit_code,
            detail,
            requeue_count,
            os.getenv("HOSTNAME", "unknown"),
        )
        _insert_error_log_for_worker_death(
            connection_id,
            payload,
            f"OOMKilled: {human_msg}",
            "celery_worker_oom_killed",
        )
    else:
        logger.warning(
            "WORKER_POD_TERMINATED | job=%s | task_id=%s | assigned_pod=%s | "
            "reason=%s | exit_code=%s | detail=%s | requeue_count=%s | pod=%s",
            job_name,
            task_id,
            assigned_pod,
            reason,
            exit_code,
            detail,
            requeue_count,
            os.getenv("HOSTNAME", "unknown"),
        )
        _insert_error_log_for_worker_death(
            connection_id,
            payload,
            human_msg,
            "celery_worker_pod_terminated",
        )

    _write_child_audit_event(
        event_name="worker_pod_oom_killed" if oom_killed else "worker_pod_terminated",
        job_name=job_name,
        connection_id=connection_id,
        child_task_id=task_id,
        status="worker_lost",
        error=human_msg,
        requeue_reason=reason,
    )
    if connection_id:
        try:
            trigger_connection_activity_log_update(connection_id)
        except Exception as exc:
            logger.debug("WORKER_DEATH_ACTIVITY_LOG_TRIGGER_FAILED | err=%s", exc)


def _task_elapsed_seconds(
    payload: Optional[dict],
    fallback_age_secs: float,
    now: datetime,
) -> float:
    dispatched_at_str = (payload or {}).get("dispatched_at")
    if dispatched_at_str:
        try:
            return (now - datetime.fromisoformat(dispatched_at_str)).total_seconds()
        except Exception:
            pass
    return fallback_age_secs


def _assigned_pod_for_task(task_id: str, payload: Optional[dict]) -> Optional[str]:
    pod = (payload or {}).get("assigned_pod")
    if pod and str(pod) not in ("", "unknown"):
        return str(pod)
    try:
        meta = celery_app.AsyncResult(task_id).result
        if isinstance(meta, dict):
            hostname = str(meta.get("hostname") or "")
            if hostname.startswith("celery@"):
                return hostname.split("@", 1)[1]
    except Exception:
        pass
    return None


def _is_monolithic_call_function_payload(payload: dict) -> bool:
    if payload.get("task_kind") == "call_function":
        return True
    return "group_index" not in payload


def _requeue_call_function_task(db, r, now, job_name, old_task_id, payload, reason):
    old_rq = int(payload.get("requeue_count") or 0)
    if old_rq >= CELERY_MAX_CHILD_REQUEUES:
        logger.error(
            "REQUEUE_CAP_EXCEEDED | kind=call_function | job=%s | old_id=%s | "
            "requeue_count=%s | max=%s | would_reason=%s",
            job_name,
            old_task_id,
            old_rq,
            CELERY_MAX_CHILD_REQUEUES,
            reason,
        )
        return None, None
    new_id = str(uuid4())
    new_payload = dict(payload)
    new_payload["requeue_count"] = old_rq + 1
    new_payload["dispatched_at"] = now.isoformat()
    new_payload["requeued_from"] = old_task_id
    new_payload["requeue_reason"] = reason
    new_payload["task_kind"] = "call_function"
    new_payload.pop("assigned_pod", None)
    new_payload.pop("assigned_pid", None)
    new_payload.pop("assigned_at", None)
    _save_payload(new_id, new_payload)
    call_function.apply_async(
        args=[
            new_payload["url"],
            new_payload["data"],
            new_payload["headers"],
            job_name,
            new_payload["connection_id"],
        ],
        task_id=new_id,
        queue=CELERY_MIGRATION_QUEUE,
    )
    logger.warning(
        "CALL_FUNCTION_REQUEUE_DISPATCHED | job=%s | old_id=%s | new_id=%s | reason=%s | "
        "requeue_count=%s | queue=%s",
        job_name,
        old_task_id,
        new_id,
        reason,
        new_payload.get("requeue_count"),
        CELERY_MIGRATION_QUEUE,
    )
    _delete_payload(old_task_id)
    db.execute(
        text("""
            UPDATE migration_record
            SET task_id = :new_id, updated_at = :now
            WHERE job_name = :j AND task_id = :old_id
        """),
        {"new_id": new_id, "now": now, "j": job_name, "old_id": old_task_id},
    )
    return new_id, new_payload


def _maybe_requeue_orphan_started_child(
    *,
    db,
    r,
    now,
    job_name: str,
    task_id: str,
    connection_id,
    payload: Optional[dict] = None,
    age_secs: float = 0,
    source: str = "reconcile",
) -> Optional[str]:
    """
    Requeue STARTED/IN_PROGRESS work when the assigned worker pod is confirmed gone.
    Returns the new Celery task id when a replacement was published.
    """
    if not CELERY_ORPHAN_AUTO_REQUEUE_ENABLED or not CELERY_STARTED_CHILD_REQUEUE_ENABLED:
        return None
    lock_key = f"celery:orphan-requeue-lock:{task_id}"
    if r.exists(lock_key):
        return None

    payload = payload or _load_payload(task_id)
    if not payload:
        return None

    try:
        state = celery_app.AsyncResult(task_id).state
    except Exception as exc:
        logger.warning(
            "ORPHAN_AUTO_REQUEUE_STATE_FAILED | source=%s | job=%s | task_id=%s | err=%s",
            source,
            job_name,
            task_id,
            exc,
        )
        return None
    if state in ("SUCCESS", "FAILURE", "REVOKED"):
        return None
    if state not in ("STARTED", "IN_PROGRESS"):
        return None

    assigned_pod = _assigned_pod_for_task(task_id, payload)
    if not assigned_pod or _is_worker_pod_alive(assigned_pod):
        return None

    elapsed_secs = _task_elapsed_seconds(payload, age_secs, now)
    if elapsed_secs <= CELERY_OOM_ORPHAN_REQUEUE_AFTER_SECONDS:
        return None

    should_requeue, skip_reason, termination_info = _should_orphan_requeue_assigned_pod(
        assigned_pod
    )
    if not should_requeue:
        logger.warning(
            "ORPHAN_AUTO_REQUEUE_SKIPPED | source=%s | job=%s | task_id=%s | assigned_pod=%s | "
            "elapsed_secs=%.0f | skip_reason=%s",
            source,
            job_name,
            task_id,
            assigned_pod,
            elapsed_secs,
            skip_reason,
        )
        return None

    requeue_reason = _requeue_reason_for_pod_death(termination_info)
    _record_worker_pod_death(
        job_name=job_name,
        connection_id=connection_id,
        task_id=task_id,
        assigned_pod=assigned_pod,
        payload=payload,
        termination_info=termination_info,
    )
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception as revoke_err:
        logger.warning(
            "ORPHAN_AUTO_REQUEUE_REVOKE_FAILED | job=%s | task_id=%s | err=%s",
            job_name,
            task_id,
            revoke_err,
        )

    new_id = None
    if _is_monolithic_call_function_payload(payload):
        new_id, _new_payload = _requeue_call_function_task(
            db, r, now, job_name, task_id, payload, requeue_reason
        )
    else:
        new_id, _new_payload = _requeue_child_task(
            db=db,
            r=r,
            now=now,
            job_name=job_name,
            old_task_id=task_id,
            payload=payload,
            reason=requeue_reason,
        )
        if not new_id:
            _fail_child_on_requeue_cap(
                job_name=job_name,
                connection_id=connection_id,
                task_id=task_id,
                payload=payload,
                reason=requeue_reason,
            )
            return None
        logger.warning(
            "STARTED_CHILD_REQUEUED | job=%s | old_id=%s | new_id=%s | "
            "elapsed_since_dispatch_secs=%.0f | threshold=%s | reason=%s | "
            "lost_pod=%s | group=%s | pod=%s | source=%s",
            job_name,
            task_id,
            new_id,
            elapsed_secs,
            CELERY_OOM_ORPHAN_REQUEUE_AFTER_SECONDS,
            requeue_reason,
            payload.get("assigned_pod"),
            _new_payload.get("group_index"),
            os.getenv("HOSTNAME", "unknown"),
            source,
        )

    if not new_id:
        return None

    try:
        from celery_scale_down_protection import task_orphaned_key

        r.delete(task_orphaned_key(task_id))
    except Exception:
        pass
    r.set(lock_key, "1", ex=CELERY_ORPHAN_REQUEUE_LOCK_TTL_SECONDS)
    logger.warning(
        "ORPHAN_AUTO_REQUEUED | source=%s | job=%s | old_id=%s | new_id=%s | "
        "assigned_pod=%s | elapsed_secs=%.0f | reason=%s",
        source,
        job_name,
        task_id,
        new_id,
        assigned_pod,
        elapsed_secs,
        requeue_reason,
    )
    return new_id


def _maybe_hard_fail_stuck_started_child(
    *,
    job_name: str,
    task_id: str,
    connection_id,
    payload: Optional[dict],
    elapsed_secs: float,
) -> bool:
    """
    Last-resort backstop for STARTED children that never terminate.

    Fires regardless of CELERY_STARTED_CHILD_REQUEUE_ENABLED or a missing Celery task time
    limit. Without this, a child whose worker pod dies mid-task (result orphaned in STARTED,
    e.g. after CHILD_DUPLICATE_DELIVERY_SKIPPED) leaves the pipeline running forever.

    Returns True when the job was failed.
    """
    threshold = CELERY_STARTED_CHILD_HARD_FAIL_AFTER_SECONDS
    if threshold is None or elapsed_secs <= threshold:
        return False

    assigned_pod = _assigned_pod_for_task(task_id, payload)
    pod_alive = _is_worker_pod_alive(assigned_pod) if assigned_pod else True
    if CELERY_STARTED_HARD_FAIL_REQUIRE_DEAD_POD and pod_alive:
        logger.warning(
            "STARTED_HARD_FAIL_SKIPPED | job=%s | task_id=%s | elapsed_secs=%.0f | "
            "assigned_pod=%s | reason=pod_still_alive",
            job_name,
            task_id,
            elapsed_secs,
            assigned_pod,
        )
        return False

    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception as revoke_err:
        logger.warning(
            "STARTED_HARD_FAIL_REVOKE_FAILED | job=%s | task_id=%s | err=%s",
            job_name,
            task_id,
            revoke_err,
        )

    tables = _tables_for_child_payload(payload)
    message = (
        f"STARTED task stuck for {elapsed_secs:.0f}s "
        f"(assigned_pod={assigned_pod}, pod_alive={pod_alive})"
    )
    if tables:
        _fail_child_tables(
            job_name,
            connection_id,
            tables,
            message,
            task_id=task_id,
        )
    else:
        # Cannot scope tables — fall back to open-row fail so completed siblings survive.
        _fail_open_job_tables(job_name, connection_id, message)
    logger.warning(
        "STARTED_TASK_HARD_FAILED | job=%s | task_id=%s | elapsed_secs=%.0f | "
        "threshold=%s | assigned_pod=%s | pod_alive=%s | tables=%s",
        job_name,
        task_id,
        elapsed_secs,
        threshold,
        assigned_pod,
        pod_alive,
        tables,
    )
    return True


def _requeue_stale_orphan_tasks(
    db,
    r,
    now,
    orphans: list[dict],
) -> int:
    """Requeue tasks flagged by stale heartbeat scan when worker pod is gone."""
    requeued = 0
    for orphan in orphans:
        task_id = str(orphan.get("task_id") or "").strip()
        if not task_id:
            continue
        payload = _load_payload(task_id)
        if not payload:
            continue
        job_name = payload.get("job_name")
        connection_id = payload.get("connection_id")
        if not job_name:
            continue
        new_id = _maybe_requeue_orphan_started_child(
            db=db,
            r=r,
            now=now,
            job_name=job_name,
            task_id=task_id,
            connection_id=connection_id,
            payload=payload,
            source="stale_heartbeat",
        )
        if new_id:
            requeued += 1
    return requeued


def _requeue_started_child(
    *,
    db,
    r,
    now,
    job_name: str,
    task_id: str,
    connection_id,
    payload: dict,
    elapsed_secs: float,
    reason: str,
    threshold_secs: int,
) -> bool:
    """Revoke stale STARTED child and publish a replacement. Returns True if requeued."""
    if not CELERY_STARTED_CHILD_REQUEUE_ENABLED:
        return False
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception as revoke_err:
        logger.warning(
            "STARTED_REVOKE_FAILED | job=%s | task_id=%s | err=%s | pod=%s",
            job_name, task_id, revoke_err, os.getenv("HOSTNAME", "unknown"),
        )
    new_id, new_payload = _requeue_child_task(
        db=db,
        r=r,
        now=now,
        job_name=job_name,
        old_task_id=task_id,
        payload=payload,
        reason=reason,
    )
    if not new_id:
        _fail_child_on_requeue_cap(
            job_name=job_name,
            connection_id=connection_id,
            task_id=task_id,
            payload=payload,
            reason=reason,
        )
        return False
    logger.warning(
        "STARTED_CHILD_REQUEUED | job=%s | old_id=%s | new_id=%s | "
        "elapsed_since_dispatch_secs=%.0f | threshold=%s | reason=%s | "
        "lost_pod=%s | group=%s | pod=%s",
        job_name,
        task_id,
        new_id,
        elapsed_secs,
        threshold_secs,
        reason,
        payload.get("assigned_pod"),
        new_payload.get("group_index"),
        os.getenv("HOSTNAME", "unknown"),
    )
    return True


def _enrich_parent_ctx_from_db(job_name: str, connection_id, parent_ctx: dict) -> dict:
    """
    Resolve log_id / session / cmp_id / user_id / selected_tables when Redis finalizer
    context was lost (stuck-finalizer recovery) or the HTTP payload omitted log_id.

    Chunk-level Celery job_name differs from the combined job_name on the job-level log
    row, so we join via migration_session_id from migration_record.
    """
    ctx = dict(parent_ctx or {})
    if not job_name or not connection_id:
        return ctx
    if ctx.get("log_id"):
        return ctx

    try:
        with SessionLocal() as db:
            sid_row = db.execute(
                text(
                    """
                    SELECT migration_session_id
                    FROM migration_record
                    WHERE connection_id = :connection_id
                      AND (
                        job_name = :job_name
                        OR job_name LIKE :job_prefix
                      )
                    LIMIT 1
                    """
                ),
                {
                    "job_name": job_name,
                    "job_prefix": f"{job_name}__%",
                    "connection_id": int(connection_id),
                },
            ).fetchone()
            if not sid_row:
                return ctx
            sid = sid_row[0]

            log_row = db.execute(
                text(
                    """
                    SELECT log_id, session, cmp_id, user_id
                    FROM log
                    WHERE connection_id = :connection_id
                      AND migration_record_id IS NULL
                      AND job_name = :job_name
                      AND status = 'I'
                    ORDER BY log_id DESC
                    LIMIT 1
                    """
                ),
                {"connection_id": int(connection_id), "job_name": job_name},
            ).fetchone()
            if not log_row:
                log_row = db.execute(
                    text(
                        """
                        SELECT log_id, session, cmp_id, user_id
                        FROM log
                        WHERE connection_id = :connection_id
                          AND migration_record_id IS NULL
                          AND job_name = :job_name
                        ORDER BY log_id DESC
                        LIMIT 1
                        """
                    ),
                    {"connection_id": int(connection_id), "job_name": job_name},
                ).fetchone()
            if not log_row:
                log_row = db.execute(
                    text(
                        """
                        SELECT log_id, session, cmp_id, user_id
                        FROM log
                        WHERE connection_id = :connection_id
                          AND migration_record_id IS NULL
                          AND status = 'I'
                          AND session = :sid
                        ORDER BY log_id DESC
                        LIMIT 1
                        """
                    ),
                    {"connection_id": int(connection_id), "sid": sid},
                ).fetchone()
            if log_row:
                ctx.setdefault("log_id", log_row[0])
                ctx.setdefault("session", log_row[1])
                ctx.setdefault("cmp_id", log_row[2])
                ctx.setdefault("user_id", log_row[3])

            tbl_rows = db.execute(
                text(
                    """
                    SELECT DISTINCT table_name
                    FROM migration_record
                    WHERE connection_id = :connection_id
                      AND migration_session_id = :sid
                      AND table_name IS NOT NULL
                      AND table_name <> ''
                    """
                ),
                {"connection_id": int(connection_id), "sid": sid},
            ).fetchall()
            if tbl_rows:
                ctx.setdefault("selected_tables", [r[0] for r in tbl_rows])
    except Exception as exc:
        logger.warning(
            "PARENT_CTX_DB_ENRICH_FAILED | job=%s | connection_id=%s | err=%s",
            job_name,
            connection_id,
            exc,
        )
    logger.warning("PARENT_CTX_RESOLVED | job=%s | connection_id=%s | log_id=%s | session=%s", job_name, connection_id, ctx.get("log_id"), ctx.get("session"))
    return ctx


_audit_csv_lock = Lock()
_gcs_client = None
_gcs_client_lock = Lock()
_missing_gcs_bucket_warned = False
_audit_csv_headers = [
    "event_ts_utc",
    "event_name",
    "job_name",
    "connection_id",
    "parent_task_id",
    "child_task_id",
    "group_index",
    "pod",
    "pid",
    "tables",
    "table_count",
    "queue_wait_secs",
    "retry",
    "max_retries",
    "status",
    "api_status",
    "total_rows",
    "successful_tables_count",
    "failed_tables_count",
    "error",
    "requeue_from",
    "requeue_reason",
]


def _is_gce_metadata_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "metadata.google.internal",
            "universe_domain",
            "metadata server unavailable",
            "compute engine metadata",
        )
    )


def _is_non_retryable_oauth_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "invalid_grant",
            "expired access/refresh token",
            "expired access token",
            "invalid_session_id",
            "session expired",
            "token refresh failed",
            "reconnect the source",
            "reconnect salesforce",
        )
    )


def _is_non_retryable_child_error(exc: Exception) -> bool:
    """Code/config bugs that will not succeed on Celery retry."""
    if isinstance(exc, (TypeError, AttributeError, ImportError, ModuleNotFoundError, SyntaxError, NameError)):
        return True
    text = str(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "unexpected keyword argument",
            "has no attribute",
            "no module named",
            "takes no arguments",
            "not defined",
            # Deterministic Databricks / load failures — retry only resets UI to
            # in_progress (hourglass) without fixing schema/config mismatches.
            "copy_into_schema_mismatch",
            "schema mismatch was detected while copying",
            "invalid access token",
            "permission denied",
            "table or view not found",
            "does not exist",
            # Scoped-credential Spark fallback blocked by workspace policy — retry won't help.
            "zero-manual spark staging fallback",
            "secret scope",
            "create job clusters",
            "serverless-only workspaces still need",
            "only the account admin can create",
        )
    )


def _format_task_error_message(err) -> str:
    """Extract a user-facing message from Celery/direct connector exceptions."""
    if err is None:
        return "Migration failed due to task error."
    detail = getattr(err, "detail", None)
    if detail is not None:
        if isinstance(detail, (list, dict)):
            return str(detail)[:2000]
        text = str(detail).strip()
        if text:
            return text[:2000]
    response = getattr(err, "response", None)
    if response is not None:
        try:
            body = response.json()
            if isinstance(body, dict):
                for key in ("detail", "message", "error"):
                    if body.get(key):
                        text = str(body[key]).strip()
                        if text:
                            return text[:2000]
            elif body is not None:
                text = str(body).strip()
                if text:
                    return text[:2000]
        except Exception:
            text = (getattr(response, "text", None) or "").strip()
            if text:
                return text[:2000]
    text = str(err).strip()
    return (text or "Migration failed due to task error.")[:2000]


def _format_child_retry_ui_message(
    message: Optional[str],
    *,
    retry_current: Optional[int] = None,
    retry_max: Optional[int] = None,
    table_name: Optional[str] = None,
) -> str:
    """Human-readable retry message (must not contain 'Migration in progress')."""
    text_msg = (message or "Migration failed").strip()
    if retry_current is not None and retry_max is not None:
        retry_label = f"retrying ({retry_current}/{retry_max})"
    else:
        retry_label = "retrying"
    if table_name:
        return f"Entity '{table_name}' {retry_label}: {text_msg}"
    return f"Migration {retry_label}: {text_msg}"


def _update_job_level_in_progress_log(
    db,
    *,
    job_name: str,
    connection_id,
    message: str,
    migration_session_id=None,
) -> int:
    """Update job-level in-progress activity log text while status stays I."""
    text_msg = (message or "").strip()
    if not text_msg:
        return 0
    params = {
        "message": text_msg,
        "now": datetime.now(timezone.utc),
        "connection_id": int(connection_id),
        "job_name": job_name,
        "job_prefix": f"{job_name}__%",
        "status_i": "I",
    }
    if migration_session_id is not None:
        result = db.execute(
            text(
                """
                UPDATE log
                SET message = :message, time = :now
                WHERE connection_id = :connection_id
                  AND migration_record_id IS NULL
                  AND status = :status_i
                  AND (
                    job_name = :job_name
                    OR job_name LIKE :job_prefix
                    OR session = :session_id
                  )
                """
            ),
            {**params, "session_id": migration_session_id},
        )
    else:
        result = db.execute(
            text(
                """
                UPDATE log
                SET message = :message, time = :now
                WHERE connection_id = :connection_id
                  AND migration_record_id IS NULL
                  AND status = :status_i
                  AND (job_name = :job_name OR job_name LIKE :job_prefix)
                """
            ),
            params,
        )
    return int(getattr(result, "rowcount", 0) or 0)


def _upsert_table_retry_logs(
    db,
    *,
    job_name: str,
    connection_id,
    tables: list[str],
    message: str,
) -> None:
    """Ensure table-level in-progress logs exist and carry the retry error text."""
    text_msg = (message or "").strip()
    tables = [str(t) for t in (tables or []) if t]
    if not text_msg or not tables:
        return

    session_id, cmp_id, user_id = _resolve_failure_log_identity(db, job_name, connection_id)
    rows = db.execute(
        text(
            """
            SELECT id, table_name
            FROM migration_record
            WHERE connection_id = :connection_id
              AND table_name = ANY(:tables)
              AND (job_name = :job_name OR job_name LIKE :job_prefix)
            """
        ),
        {
            **_migration_job_name_filter_params(job_name),
            "connection_id": int(connection_id),
            "tables": tables,
        },
    ).fetchall()
    if not rows:
        return

    now = datetime.now(timezone.utc)
    for migration_record_id, table_name in rows:
        table_message = _format_child_retry_ui_message(
            text_msg,
            table_name=table_name,
        )
        updated = db.execute(
            text(
                """
                UPDATE log
                SET message = :message, time = :now
                WHERE log_id = (
                    SELECT log_id
                    FROM log
                    WHERE migration_record_id = :migration_record_id
                      AND status = 'I'
                    ORDER BY log_id DESC
                    LIMIT 1
                )
                """
            ),
            {
                "message": table_message,
                "now": now,
                "migration_record_id": migration_record_id,
            },
        )
        if int(getattr(updated, "rowcount", 0) or 0) > 0:
            continue
        db.execute(
            text(
                """
                INSERT INTO log (
                    log_id, cmp_id, connection_id, user_id, time, status,
                    message, session, job_name, migration_record_id
                )
                VALUES (
                    nextval('log_log_id_seq'), :cmp_id, :connection_id, :user_id,
                    :now, 'I', :message, :session_id, :job_name, :migration_record_id
                )
                """
            ),
            {
                "cmp_id": cmp_id,
                "connection_id": int(connection_id),
                "user_id": user_id,
                "now": now,
                "message": table_message,
                "session_id": session_id,
                "job_name": job_name,
                "migration_record_id": migration_record_id,
            },
        )


def _surface_child_error_to_ui(
    job_name: str,
    connection_id,
    tables: list[str],
    message: Optional[str],
    *,
    mark_failed: bool = False,
    task_id: Optional[str] = None,
    retry_current: Optional[int] = None,
    retry_max: Optional[int] = None,
) -> None:
    """Write per-table and job-level error messages so the UI can display them."""
    if mark_failed:
        _fail_child_tables(
            job_name,
            connection_id,
            tables,
            message,
            task_id=task_id,
        )
        return

    text_msg = (message or "Migration failed").strip()
    tables = [str(t) for t in (tables or []) if t]
    if not text_msg or not tables:
        return

    job_message = _format_child_retry_ui_message(
        text_msg,
        retry_current=retry_current,
        retry_max=retry_max,
    )

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        try:
            _upsert_table_retry_logs(
                db,
                job_name=job_name,
                connection_id=connection_id,
                tables=tables,
                message=text_msg,
            )
            migration_session_id = db.execute(
                text(
                    "SELECT DISTINCT migration_session_id FROM migration_record WHERE job_name = :job_name"
                ),
                {"job_name": job_name},
            ).scalar()
            _update_job_level_in_progress_log(
                db,
                job_name=job_name,
                connection_id=connection_id,
                message=job_message,
                migration_session_id=migration_session_id,
            )
            db.commit()
            if migration_session_id:
                try:
                    trigger_ws_status_update(migration_session_id)
                    trigger_connection_activity_log_update(connection_id)
                except Exception as ws_err:
                    logger.warning(
                        "WebSocket trigger failed session=%s err=%s",
                        migration_session_id,
                        ws_err,
                    )
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error(
                "CHILD_ERROR_UI_LOG_FAILED | job=%s | tables=%s | err=%s",
                job_name,
                tables,
                exc,
            )


def _wait_for_gce_metadata(timeout_secs: float = 45.0) -> bool:
    """Wait until GKE Workload Identity metadata is reachable (new nodes race this)."""
    import urllib.error
    import urllib.request

    url = "http://metadata.google.internal/computeMetadata/v1/"
    deadline = time.time() + max(1.0, float(timeout_secs))
    last_err = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
            urllib.request.urlopen(req, timeout=2)
            return True
        except Exception as exc:
            last_err = exc
            time.sleep(1.5)
    logger.warning("GCE_METADATA_WAIT_TIMEOUT | timeout=%ss | err=%s", timeout_secs, last_err)
    return False


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is not None:
        return _gcs_client
    with _gcs_client_lock:
        if _gcs_client is None:
            _wait_for_gce_metadata(timeout_secs=30)
            project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
            _gcs_client = storage.Client(project=project_id) if project_id else storage.Client()
    return _gcs_client


def _write_child_audit_event_gcs(event_payload: dict):
    global _missing_gcs_bucket_warned
    if not CELERY_CHILD_AUDIT_GCS_ENABLED:
        return False
    if not CELERY_CHILD_AUDIT_GCS_BUCKET:
        if not _missing_gcs_bucket_warned:
            logger.warning(
                "CHILD_AUDIT_GCS_BUCKET_MISSING | set CELERY_CHILD_AUDIT_GCS_BUCKET or GCS_BUCKET_NAME to enable uploads"
            )
            _missing_gcs_bucket_warned = True
        return False
    try:
        gcs = _get_gcs_client()
        bucket = gcs.bucket(CELERY_CHILD_AUDIT_GCS_BUCKET)
        event_ts = datetime.now(timezone.utc)
        raw_job_name = str(event_payload.get("job_name") or "unknown")
        safe_job_name = raw_job_name.replace("/", "_").replace(" ", "_")
        # GCS object names have a max length (1024 chars). Your job_name can be huge
        # (long -> chains), so we must truncate + hash.
        job_hash = hashlib.sha256(raw_job_name.encode("utf-8")).hexdigest()[:12]
        safe_job_name = safe_job_name[:80]  # keep path segments small

        event_name = str(event_payload.get("event_name") or "event").replace("/", "_")[:40]
        pod_name = str(event_payload.get("pod") or "unknown").replace("/", "_")[:40]
        event_id = str(uuid4())
        object_name = (
            f"{CELERY_CHILD_AUDIT_GCS_PREFIX}/"
            f"{event_ts.strftime('%Y-%m-%d')}/"
            f"job={safe_job_name}-{job_hash}/"
            f"pod={pod_name}/"
            f"{event_ts.strftime('%H%M%S')}_{event_name}_{event_id}.json"
        )
        blob = bucket.blob(object_name)
        blob.upload_from_string(
            json.dumps(event_payload, ensure_ascii=True),
            content_type="application/json",
        )
        return True
    except Exception as e:
        logger.warning("CHILD_AUDIT_GCS_WRITE_FAILED | event=%s | err=%s", event_payload.get("event_name"), e)
        return False


def _write_child_audit_event(
    event_name: str,
    job_name: str,
    connection_id=None,
    parent_task_id=None,
    child_task_id=None,
    group_index=None,
    tables=None,
    queue_wait_secs=None,
    retry=None,
    max_retries=None,
    status=None,
    api_status=None,
    total_rows=None,
    successful_tables_count=None,
    failed_tables_count=None,
    error=None,
    requeue_from=None,
    requeue_reason=None,
):
    event_payload = {
        "event_ts_utc": datetime.now(timezone.utc).isoformat(),
        "event_name": event_name,
        "job_name": job_name,
        "connection_id": connection_id,
        "parent_task_id": parent_task_id,
        "child_task_id": child_task_id,
        "group_index": group_index,
        "pod": os.getenv("HOSTNAME", "unknown"),
        "pid": os.getpid(),
        "tables": tables or [],
        "table_count": len(tables) if isinstance(tables, list) else 0,
        "queue_wait_secs": queue_wait_secs,
        "retry": retry,
        "max_retries": max_retries,
        "status": status,
        "api_status": api_status,
        "total_rows": total_rows,
        "successful_tables_count": successful_tables_count,
        "failed_tables_count": failed_tables_count,
        "error": error,
        "requeue_from": requeue_from,
        "requeue_reason": requeue_reason,
    }
    if _write_child_audit_event_gcs(event_payload):
        return
    if not CELERY_CHILD_AUDIT_CSV_ENABLED:
        return
    try:
        csv_path = CELERY_CHILD_AUDIT_CSV_PATH
        parent_dir = os.path.dirname(csv_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        tables = tables or []
        row = [
            event_payload["event_ts_utc"],
            event_name,
            job_name,
            connection_id,
            parent_task_id,
            child_task_id,
            group_index,
            event_payload["pod"],
            event_payload["pid"],
            json.dumps(tables, ensure_ascii=True),
            len(tables) if isinstance(tables, list) else 0,
            queue_wait_secs,
            retry,
            max_retries,
            status,
            api_status,
            total_rows,
            successful_tables_count,
            failed_tables_count,
            error,
            requeue_from,
            requeue_reason,
        ]
        with _audit_csv_lock:
            file_exists = os.path.exists(csv_path)
            with open(csv_path, "a", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                if not file_exists:
                    writer.writerow(_audit_csv_headers)
                writer.writerow(row)
    except Exception as e:
        logger.warning("CHILD_AUDIT_CSV_WRITE_FAILED | event=%s | err=%s", event_name, e)


def _requeue_child_task(db, r, now, job_name, old_task_id, payload, reason):
    old_rq = int(payload.get("requeue_count") or 0)
    if old_rq >= CELERY_MAX_CHILD_REQUEUES:
        logger.error(
            "REQUEUE_CAP_EXCEEDED | job=%s | old_id=%s | requeue_count=%s | max=%s | would_reason=%s",
            job_name,
            old_task_id,
            old_rq,
            CELERY_MAX_CHILD_REQUEUES,
            reason,
        )
        return None, None
    new_id = str(uuid4())
    new_payload = dict(payload)
    new_payload["requeue_count"] = old_rq + 1
    new_payload["dispatched_at"] = now.isoformat()
    new_payload["requeued_from"] = old_task_id
    new_payload["requeue_reason"] = reason
    _save_payload(new_id, new_payload)
    call_function_child.apply_async(
        args=[
            new_payload["url"], new_payload["data"],
            new_payload["headers"], job_name,
            new_payload["connection_id"], new_payload["group_index"],
        ],
        task_id=new_id,
        queue=_child_task_queue(job_name),
    )
    if _fanout_stagger_active(job_name):
        try:
            inflight_key = _fanout_inflight_key(job_name)
            key_type = _fanout_inflight_key_type(r, job_name)
            if key_type in (b"string", "string"):
                # Slot transfer: old task will DECR on terminal postrun; new task is already live.
                pass
            else:
                r.srem(inflight_key, old_task_id)
                r.sadd(inflight_key, new_id)
                r.expire(inflight_key, CELERY_FANOUT_STAGGER_TTL_SECONDS)
        except Exception as exc:
            logger.warning(
                "FANOUT_REQUEUE_INFLIGHT_UPDATE_FAILED | job=%s | old_id=%s | new_id=%s | err=%s",
                job_name,
                old_task_id,
                new_id,
                exc,
            )
    logger.warning(
        "CHILD_REQUEUE_DISPATCHED | job=%s | old_id=%s | new_id=%s | reason=%s | "
        "requeue_count=%s | group_index=%s | table_names=%s | queue=%s",
        job_name,
        old_task_id,
        new_id,
        reason,
        new_payload.get("requeue_count"),
        new_payload.get("group_index"),
        (new_payload.get("table_names") or new_payload.get("data", {}).get("selected_tables") or [])[:20],
        _child_task_queue(job_name),
    )
    _write_child_audit_event(
        event_name="child_requeued",
        job_name=job_name,
        connection_id=new_payload.get("connection_id"),
        parent_task_id=new_payload.get("data", {}).get("parent_task_id"),
        child_task_id=new_id,
        group_index=new_payload.get("group_index"),
        tables=new_payload.get("table_names"),
        status="requeued",
        requeue_from=old_task_id,
        requeue_reason=reason,
    )

    db.execute(
        text("""
            UPDATE migration_record
            SET task_id = :new_id, updated_at = :now
            WHERE job_name = :j AND task_id = :old_id
        """),
        {"new_id": new_id, "now": now, "j": job_name, "old_id": old_task_id},
    )

    ctx_key = f"celery:finalizer-ctx:{job_name}"
    raw_ctx = r.get(ctx_key)
    if raw_ctx:
        ctx = json.loads(raw_ctx)
        ctx["expected_task_ids"] = [
            new_id if i == old_task_id else i
            for i in ctx.get("expected_task_ids", [])
        ]
        r.set(ctx_key, json.dumps(ctx), ex=CELERY_LONG_RUN_REDIS_TTL_SECONDS)
    # Delete old payload only after MR + finalizer ctx point at the new task id so
    # reconcile never sees PENDING + no payload for a task_id still in migration_record.
    _delete_payload(old_task_id)
    return new_id, new_payload


# ---------------------------------------------------------------------------
# Table group splitter
# ---------------------------------------------------------------------------
# Django yield URLs use {dest}-{source}-migration-yield.
# Warehouse → Salesforce reverse ETL paths — need [[source, dest], ...] mapping pairs.
SNOWFLAKE_TO_SALESFORCE_PATHS = frozenset({
    "/Salesforce-Snowflake-migration-yield",
    "/SalesforceSandbox-Snowflake-migration-yield",
})
DATABRICKS_TO_SALESFORCE_PATHS = frozenset({
    "/Salesforce-Databricks-migration-yield",
    "/Salesforce-DatabricksSandbox-migration-yield",
})
REVERSE_ETL_TO_SALESFORCE_PATHS = SNOWFLAKE_TO_SALESFORCE_PATHS | DATABRICKS_TO_SALESFORCE_PATHS


def _is_snowflake_to_salesforce_path(function_path: str) -> bool:
    return (function_path or "") in SNOWFLAKE_TO_SALESFORCE_PATHS


def _is_reverse_etl_to_salesforce_path(function_path: str) -> bool:
    return (function_path or "") in REVERSE_ETL_TO_SALESFORCE_PATHS


def _normalize_salesforce_mapping_entries(selected_tables):
    """
    Snowflake→Salesforce expects mapping_list as [[source, destination], ...].
    Fan-out may pass ['Account'] or [['Account', 'Account__c']].
    """
    if not selected_tables:
        return []
    normalized = []
    for entry in selected_tables:
        if entry is None:
            continue
        if isinstance(entry, (list, tuple)):
            if not entry:
                continue
            if len(entry) >= 2 and not isinstance(entry[0], (list, tuple)):
                src, dest = entry[0], entry[1]
            else:
                src = entry[0]
                dest = src
            normalized.append([str(src), str(dest)])
        elif isinstance(entry, str):
            normalized.append([entry, entry])
        else:
            normalized.append([str(entry), str(entry)])
    return normalized


def _migration_record_table_names(table_group, function_path=None):
    """Table names for migration_record.task_id mapping (source side only)."""
    if _is_reverse_etl_to_salesforce_path(function_path):
        return [pair[0] for pair in _normalize_salesforce_mapping_entries(table_group)]
    return [str(t) for t in (table_group or []) if t]


def _split_table_groups(selected_tables, max_groups=6):
    """Split tables into at most max_groups balanced groups."""
    tables = [t for t in (selected_tables or []) if t]
    if not tables:
        return []
    group_count = min(max_groups, len(tables))
    groups = [[] for _ in range(group_count)]
    for idx, table in enumerate(tables):
        groups[idx % group_count].append(table)
    return groups


def _build_migration_fanout_groups(selected_tables, is_listing_path=False, function_path=None):
    """Build child table groups for orchestrate_migration fan-out."""
    if is_listing_path:
        return None
    if _is_reverse_etl_to_salesforce_path(function_path):
        entries = _normalize_salesforce_mapping_entries(selected_tables)
        if not entries:
            return []
        if CELERY_ONE_TABLE_PER_CHILD:
            return [[pair] for pair in entries]
        return _split_table_groups(entries, MAX_CELERY_CHILD_GROUPS)

    tables = [t for t in (selected_tables or []) if t]
    if not tables:
        return []
    if CELERY_ONE_TABLE_PER_CHILD:
        return [[table] for table in tables]
    return _split_table_groups(tables, MAX_CELERY_CHILD_GROUPS)


def _fanout_pending_key(job_name: str) -> str:
    return f"{CELERY_FANOUT_PENDING_KEY_PREFIX}{job_name}"


def _fanout_inflight_key(job_name: str) -> str:
    return f"{CELERY_FANOUT_INFLIGHT_KEY_PREFIX}{job_name}"


def _fanout_stagger_active(job_name: str) -> bool:
    try:
        r = _get_redis()
        return bool(
            r.exists(_fanout_pending_key(job_name))
            or r.exists(_fanout_inflight_key(job_name))
        )
    except Exception:
        return False


def _cleanup_fanout_stagger(job_name: str) -> None:
    try:
        r = _get_redis()
        r.delete(_fanout_pending_key(job_name), _fanout_inflight_key(job_name))
    except Exception as exc:
        logger.warning("FANOUT_STAGGER_CLEANUP_FAILED | job=%s | err=%s", job_name, exc)


_FANOUT_RESERVE_AND_PUBLISH_LUA = """
local pending_key = KEYS[1]
local inflight_key = KEYS[2]
local max_inflight = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local reserved = {}
while redis.call('SCARD', inflight_key) < max_inflight do
    local child_id = redis.call('LPOP', pending_key)
    if not child_id then
        break
    end
    redis.call('SADD', inflight_key, child_id)
    redis.call('EXPIRE', inflight_key, ttl)
    table.insert(reserved, child_id)
end
return reserved
"""


def _fanout_inflight_key_type(r, job_name: str):
    try:
        return r.type(_fanout_inflight_key(job_name))
    except Exception:
        return None


def _fanout_inflight_count(job_name: str) -> int:
    try:
        r = _get_redis()
        inflight_key = _fanout_inflight_key(job_name)
        key_type = _fanout_inflight_key_type(r, job_name)
        if key_type not in (None, b"none", "none", b"set", "set", b"string", "string"):
            logger.warning(
                "FANOUT_INFLIGHT_TYPE_MISMATCH | job=%s | type=%s | deleting and resetting",
                job_name, key_type,
            )
            r.delete(inflight_key)
            key_type = None
        if key_type in (b"string", "string"):
            return int(r.get(inflight_key) or 0)
        return int(r.scard(inflight_key) or 0)
    except Exception:
        return 0


def _reserve_fanout_publish_slots(job_name: str, max_inflight: Optional[int] = None) -> list[str]:
    """Atomically LPOP pending ids and track in-flight up to max_inflight."""
    limit = max_inflight if max_inflight is not None else CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB
    pending_key = _fanout_pending_key(job_name)
    inflight_key = _fanout_inflight_key(job_name)
    try:
        r = _get_redis()
        key_type = _fanout_inflight_key_type(r, job_name)
        # If key exists with wrong type (string instead of set or vice versa), delete and start
        # fresh to prevent WRONGTYPE errors. Inflight counter drift is acceptable here — the
        # stagger will simply re-fill from pending up to max_inflight.
        if key_type not in (None, b"none", "none", b"set", "set"):
            logger.warning(
                "FANOUT_INFLIGHT_TYPE_MISMATCH | job=%s | type=%s | deleting and resetting",
                job_name, key_type,
            )
            r.delete(inflight_key)
            key_type = None
        if key_type in (b"string", "string"):
            reserved = []
            while int(r.get(inflight_key) or 0) < limit:
                child_id = r.lpop(pending_key)
                if not child_id:
                    break
                child_id = _keda_task_id_str(child_id)
                if not child_id:
                    continue
                r.incr(inflight_key)
                r.expire(inflight_key, CELERY_FANOUT_STAGGER_TTL_SECONDS)
                reserved.append(child_id)
            return reserved
        raw = r.eval(
            _FANOUT_RESERVE_AND_PUBLISH_LUA,
            2,
            pending_key,
            inflight_key,
            limit,
            CELERY_FANOUT_STAGGER_TTL_SECONDS,
        )
        return [_keda_task_id_str(child_id) for child_id in (raw or []) if _keda_task_id_str(child_id)]
    except Exception as exc:
        logger.warning("FANOUT_RESERVE_FAILED | job=%s | err=%s", job_name, exc)
        return []


def _publish_child_task(child_id: str, parent_task_id=None) -> bool:
    """Publish a single child task from its saved Redis payload."""
    payload = _load_payload(child_id)
    if not payload:
        logger.warning("CHILD_PUBLISH_SKIPPED | child_id=%s | reason=no_payload", child_id)
        return False

    job_name = payload["job_name"]
    connection_id = payload["connection_id"]
    group_index = payload["group_index"]
    child_data = payload["data"]
    headers = payload["headers"]
    url = payload["url"]
    tables = payload.get("table_names") or child_data.get("selected_tables") or []

    payload["dispatched_at"] = datetime.now(timezone.utc).isoformat()
    _save_payload(child_id, payload)

    call_function_child.apply_async(
        args=[url, child_data, headers, job_name, connection_id, group_index],
        task_id=child_id,
        queue=_child_task_queue(job_name),
    )
    logger.warning(
        "CHILD_PUBLISHED | job=%s | child_id=%s | group=%s | table_count=%s | tables=%s | "
        "listing_prefixes=%s | pod=%s | staggered=%s",
        job_name,
        child_id,
        group_index,
        len(tables),
        tables,
        payload.get("listing_prefixes"),
        os.getenv("HOSTNAME", "unknown"),
        _fanout_stagger_active(job_name),
    )
    _write_child_audit_event(
        event_name="child_published",
        job_name=job_name,
        connection_id=connection_id,
        parent_task_id=parent_task_id or child_data.get("parent_task_id"),
        child_task_id=child_id,
        group_index=group_index,
        tables=tables,
        status="published",
    )
    return True


def _refill_fanout_slots(job_name: str, max_inflight: Optional[int] = None) -> int:
    """Publish pending children while in-flight count is below the per-job cap."""
    if not _fanout_stagger_active(job_name):
        return 0
    mem_ratio = resource_cfg.check_runtime_memory_pressure()
    if resource_cfg.memory_pressure_should_defer_refill(mem_ratio):
        logger.warning(
            "FANOUT_REFILL_DEFERRED | job=%s | reason=memory_pressure | ratio=%s",
            job_name,
            mem_ratio,
        )
        return 0
    published = 0
    for child_id in _reserve_fanout_publish_slots(job_name, max_inflight=max_inflight):
        if _publish_child_task(child_id):
            published += 1
    if published:
        try:
            r = _get_redis()
            pending_left = r.llen(_fanout_pending_key(job_name))
            inflight = _fanout_inflight_count(job_name)
        except Exception:
            pending_left = inflight = "unknown"
        logger.warning(
            "FANOUT_REFILL | job=%s | published=%s | pending_left=%s | inflight=%s | max_inflight=%s",
            job_name,
            published,
            pending_left,
            inflight,
            max_inflight if max_inflight is not None else CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB,
        )
    return published


def _release_fanout_slot(job_name: str, child_id: str) -> None:
    """Remove a published child from in-flight tracking and refill pending slots."""
    try:
        r = _get_redis()
        inflight_key = _fanout_inflight_key(job_name)
        key_type = _fanout_inflight_key_type(r, job_name)
        if key_type in (b"string", "string"):
            current = int(r.get(inflight_key) or 0)
            if current > 0:
                r.decr(inflight_key)
            else:
                r.set(inflight_key, 0, ex=CELERY_FANOUT_STAGGER_TTL_SECONDS)
            removed = 1
        else:
            removed = r.srem(inflight_key, child_id)
            if removed:
                r.expire(inflight_key, CELERY_FANOUT_STAGGER_TTL_SECONDS)
        logger.info(
            "FANOUT_SLOT_RELEASE | job=%s | child_id=%s | removed=%s | inflight=%s | key_type=%s",
            job_name,
            child_id,
            removed,
            _fanout_inflight_count(job_name),
            key_type,
        )
    except Exception as exc:
        logger.warning("FANOUT_SLOT_RELEASE_FAILED | job=%s | child_id=%s | err=%s", job_name, child_id, exc)
    _refill_fanout_slots(job_name)


def _init_fanout_stagger(job_name: str, child_ids: list[str], max_inflight: Optional[int] = None) -> int:
    """Seed pending child ids and publish up to max_inflight tasks."""
    limit = max_inflight if max_inflight is not None else CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB
    r = _get_redis()
    pending_key = _fanout_pending_key(job_name)
    inflight_key = _fanout_inflight_key(job_name)
    r.delete(pending_key, inflight_key)
    if child_ids:
        r.rpush(pending_key, *child_ids)
        r.expire(pending_key, CELERY_FANOUT_STAGGER_TTL_SECONDS)
    logger.warning(
        "FANOUT_STAGGER_START | job=%s | total_children=%s | max_inflight=%s | pending=%s",
        job_name,
        len(child_ids),
        limit,
        len(child_ids),
    )
    return _refill_fanout_slots(job_name, max_inflight=limit)


def _publish_all_children(children, parent_task_id=None):
    """Legacy fan-out: publish every child task immediately."""
    for c in children:
        _publish_child_task(c["id"], parent_task_id=parent_task_id)


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------
def _execute_async(coro):
    """Run async connector handlers from Celery (no loop) or FastAPI (uvicorn loop active)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Migration function root
# ---------------------------------------------------------------------------
MIGRATION_FUNCTION_ROOT = os.path.abspath(os.path.join(APP_ROOT, "..", "migration-function"))
if os.path.isdir(MIGRATION_FUNCTION_ROOT) and MIGRATION_FUNCTION_ROOT not in sys.path:
    sys.path.insert(0, MIGRATION_FUNCTION_ROOT)


# ---------------------------------------------------------------------------
# Direct connector dispatch
# ---------------------------------------------------------------------------
SOURCE_HELPER_DIRECT_HANDLERS = {
    "/SFTP-Authorization": (
        "routers.sftp_authorization_router",
        "authorize_sftp",
        "models.sftp_models",
        "SFTPAuthorizationRequest",
    ),
    "/SFTP-listing-files": (
        "routers.sftp_source_router",
        "list_files",
        "models.sftp_source_models",
        "SFTPListingTables",
    ),
    "/SFTP-listing-tables": (
        "routers.sftp_source_router",
        "list_tables",
        "models.sftp_source_models",
        "SFTPListingTables",
    ),
    "/SFTP-preview-pattern": (
        "routers.sftp_source_router",
        "preview_pattern",
        "models.sftp_source_models",
        "SFTPPatternPreviewRequest",
    ),
    "/SFTP-suggest-primary-keys": (
        "routers.sftp_source_router",
        "suggest_primary_keys",
        "models.sftp_source_models",
        "SFTPPKSuggestionRequest",
    ),
    "/AzureDataLakeStorage-listing-files": (
        "routers.adls_source_router",
        "list_files",
        "models.adls_source_models",
        "ADLSListingTables",
    ),
    "/ADLS-listing-files": (
        "routers.adls_source_router",
        "list_files",
        "models.adls_source_models",
        "ADLSListingTables",
    ),
    "/AzureDataLakeStorage-listing-tables": (
        "routers.adls_source_router",
        "list_tables",
        "models.adls_source_models",
        "ADLSListingTables",
    ),
    "/ADLS-listing-tables": (
        "routers.adls_source_router",
        "list_tables",
        "models.adls_source_models",
        "ADLSListingTables",
    ),
    "/AzureDataLakeStorage-preview-pattern": (
        "routers.adls_source_router",
        "preview_pattern",
        "models.adls_source_models",
        "ADLSPatternPreviewRequest",
    ),
    "/ADLS-preview-pattern": (
        "routers.adls_source_router",
        "preview_pattern",
        "models.adls_source_models",
        "ADLSPatternPreviewRequest",
    ),
    "/AzureDataLakeStorage-suggest-primary-keys": (
        "routers.adls_source_router",
        "suggest_primary_keys",
        "models.adls_source_models",
        "ADLSPKSuggestionRequest",
    ),
    "/ADLS-suggest-primary-keys": (
        "routers.adls_source_router",
        "suggest_primary_keys",
        "models.adls_source_models",
        "ADLSPKSuggestionRequest",
    ),
    "/GoogleDrive-listing-files": (
        "routers.google_drive_router",
        "google_drive_listing_files",
        "models.google_drive_models",
        "GoogleDriveListingTables",
    ),
    "/GoogleDrive-listing-tables": (
        "routers.google_drive_router",
        "google_drive_listing_tables",
        "models.google_drive_models",
        "GoogleDriveListingTables",
    ),
    "/GoogleDrive-preview-pattern": (
        "routers.google_drive_router",
        "google_drive_preview_pattern",
        "models.google_drive_models",
        "GoogleDrivePatternPreviewRequest",
    ),
    "/GoogleDrive-suggest-primary-keys": (
        "routers.google_drive_router",
        "google_drive_suggest_primary_keys",
        "models.google_drive_models",
        "GoogleDrivePKSuggestionRequest",
    ),
}


def _dispatch_source_helper(function_path, payload):
    handler_spec = SOURCE_HELPER_DIRECT_HANDLERS.get(function_path)
    if not handler_spec:
        return None
    router_module_name, handler_name, model_module_name, model_name = handler_spec
    request_model = getattr(importlib.import_module(model_module_name), model_name)
    handler = getattr(importlib.import_module(router_module_name), handler_name)
    return _execute_async(handler(request_model(**payload)))


def dispatch_direct_connector(function_path, payload):
    """Public synchronous entry point used by FastAPI source helper routes."""
    return _dispatch_direct_connector(function_path, payload)


def _dispatch_direct_connector(function_path, payload):
    if function_path == "/SFTP-listing-tables":
        SFTPListingTables = getattr(
            importlib.import_module("models.sftp_source_models"), "SFTPListingTables"
        )
        SFTPSourceService = getattr(
            importlib.import_module("services.sftp.source"), "SFTPSourceService"
        )
        request = SFTPListingTables(**payload)
        result = SFTPSourceService.list_tables_with_schema(request)
        SFTPSourceService.refresh_and_update_sftp_tables_in_database(
            connection_id=request.connection_id,
            tables_result=result,
            job_name=getattr(request, "job_name", None),
            migration_session_id=getattr(request, "migration_session_id", None),
        )
        return {"result": result}
    if function_path in (
        "/AzureDataLakeStorage-listing-tables",
        "/ADLS-listing-tables",
    ):
        ADLSListingTables = getattr(
            importlib.import_module("models.adls_source_models"), "ADLSListingTables"
        )
        ADLSSourceService = getattr(
            importlib.import_module("services.adls.source"), "ADLSSourceService"
        )
        request = ADLSListingTables(**payload)
        result = ADLSSourceService.list_tables_with_schema(request)
        ADLSSourceService.refresh_and_update_adls_tables_in_database(
            connection_id=request.connection_id,
            tables_result=result,
            job_name=getattr(request, "job_name", None),
            migration_session_id=getattr(request, "migration_session_id", None),
        )
        return {"result": result}
    if function_path == "/GoogleDrive-listing-tables":
        GoogleDriveListingTables = getattr(
            importlib.import_module("models.google_drive_models"), "GoogleDriveListingTables"
        )
        GoogleDriveSnowflakeService = getattr(
            importlib.import_module("services.google_drive.as_source.to_snowflake"),
            "GoogleDriveSnowflakeService",
        )
        request = GoogleDriveListingTables(**payload)
        result = GoogleDriveSnowflakeService.list_tables_with_schema(request)
        GoogleDriveSnowflakeService.refresh_and_update_google_drive_tables_in_database(
            connection_id=request.connection_id,
            tables_result=result,
            job_name=getattr(request, "job_name", None),
            migration_session_id=getattr(request, "migration_session_id", None),
        )
        return {"result": result}
    if function_path in SOURCE_HELPER_DIRECT_HANDLERS:
        return _dispatch_source_helper(function_path, payload)
    if function_path in (
        "/Snowflake-MicrosoftDynamics-migration-yield",
        "/Snowflake-MicrosoftDynamics365_FO-migration-yield",
    ):
        MigrationRequest = getattr(importlib.import_module("models.microsoft_dynamics_models"), "MigrationRequest")
        migrate = getattr(importlib.import_module("services.dynamics.as_source.to_snowflake"), "migrate")
        return _execute_async(migrate(MigrationRequest(**payload)))
    if function_path == "/Databricks-MicrosoftDynamics365_FO-migration-yield":
        MigrationRequest = getattr(importlib.import_module("models.microsoft_dynamics_models"), "MigrationRequest")
        migrate = getattr(
            importlib.import_module("services.dynamics.as_source.to_databricks"),
            "migrate",
        )
        return _execute_async(migrate(MigrationRequest(**payload)))
    if function_path == "/Databricks-Salesforce-migration-yield":
        SalesforceDatabricksMigration = getattr(
            importlib.import_module("models.salesforce_models"), "SalesforceDatabricksMigration"
        )
        sync_salesforce_to_databricks = getattr(
            importlib.import_module("services.salesforce.as_source.to_databricks"),
            "sync_salesforce_to_databricks",
        )
        return _execute_async(
            sync_salesforce_to_databricks(SalesforceDatabricksMigration(**payload), "production")
        )
    if function_path == "/Databricks-SalesforceSandbox-migration-yield":
        SalesforceDatabricksMigration = getattr(
            importlib.import_module("models.salesforce_models"), "SalesforceDatabricksMigration"
        )
        sync_salesforce_to_databricks = getattr(
            importlib.import_module("services.salesforce.as_source.to_databricks"),
            "sync_salesforce_to_databricks",
        )
        return _execute_async(
            sync_salesforce_to_databricks(SalesforceDatabricksMigration(**payload), "sandbox")
        )
    if function_path == "/Salesforce-Databricks-migration-yield":
        SalesforceMigration = getattr(
            importlib.import_module("models.salesforce_models"), "SalesforceMigration"
        )
        sync_databricks_to_salesforce = getattr(
            importlib.import_module("services.salesforce.as_destination.from_databricks"),
            "sync_databricks_to_salesforce",
        )
        sf_payload = dict(payload)
        sf_payload["selected_tables"] = _normalize_salesforce_mapping_entries(
            sf_payload.get("selected_tables")
        )
        return _execute_async(
            sync_databricks_to_salesforce(SalesforceMigration(**sf_payload), "production")
        )
    if function_path == "/Salesforce-DatabricksSandbox-migration-yield":
        SalesforceMigration = getattr(
            importlib.import_module("models.salesforce_models"), "SalesforceMigration"
        )
        sync_databricks_to_salesforce = getattr(
            importlib.import_module("services.salesforce.as_destination.from_databricks"),
            "sync_databricks_to_salesforce",
        )
        sf_payload = dict(payload)
        sf_payload["selected_tables"] = _normalize_salesforce_mapping_entries(
            sf_payload.get("selected_tables")
        )
        return _execute_async(
            sync_databricks_to_salesforce(SalesforceMigration(**sf_payload), "sandbox")
        )
    if function_path == "/Snowflake-Salesforce-migration-yield":
        SalesforceSnowflakeMigration = getattr(importlib.import_module("models.salesforce_models"), "SalesforceSnowflakeMigration")
        sync_salesforce_to_snowflake = getattr(importlib.import_module("services.salesforce.as_source.to_snowflake"), "sync_salesforce_to_snowflake")
        return _execute_async(sync_salesforce_to_snowflake(SalesforceSnowflakeMigration(**payload), "production"))
    if function_path == "/Salesforce-Snowflake-migration-yield":
        SalesforceMigration = getattr(importlib.import_module("models.salesforce_models"), "SalesforceMigration")
        sync_snowflake_to_salesforce = getattr(importlib.import_module("services.salesforce.as_destination.from_snowflake"), "sync_snowflake_to_salesforce")
        sf_payload = dict(payload)
        sf_payload["selected_tables"] = _normalize_salesforce_mapping_entries(
            sf_payload.get("selected_tables")
        )
        return _execute_async(sync_snowflake_to_salesforce(SalesforceMigration(**sf_payload), "production"))
    if function_path == "/Snowflake-SalesforceSandbox-migration-yield":
        SalesforceSnowflakeMigration = getattr(importlib.import_module("models.salesforce_models"), "SalesforceSnowflakeMigration")
        sync_salesforce_to_snowflake = getattr(importlib.import_module("services.salesforce.as_source.to_snowflake"), "sync_salesforce_to_snowflake")
        return _execute_async(sync_salesforce_to_snowflake(SalesforceSnowflakeMigration(**payload), "sandbox"))
    if function_path == "/SalesforceSandbox-Snowflake-migration-yield":
        SalesforceMigration = getattr(importlib.import_module("models.salesforce_models"), "SalesforceMigration")
        sync_snowflake_to_salesforce = getattr(importlib.import_module("services.salesforce.as_destination.from_snowflake"), "sync_snowflake_to_salesforce")
        sf_payload = dict(payload)
        sf_payload["selected_tables"] = _normalize_salesforce_mapping_entries(
            sf_payload.get("selected_tables")
        )
        return _execute_async(sync_snowflake_to_salesforce(SalesforceMigration(**sf_payload), "sandbox"))
    if function_path == "/Snowflake-Snowflake-migration-yield":
        SnowflakeMigration = getattr(importlib.import_module("models.snowflake_models"), "SnowflakeMigration")
        migrate = getattr(importlib.import_module("services.snowflake.as_source.to_snowflake"), "migrate")
        return migrate(SnowflakeMigration(**payload))
    if function_path == "/Snowflake-GoogleReviews-migration-yield":
        GoogleReviewsMigration = getattr(importlib.import_module("models.google_reviews_models"), "GoogleReviewsMigration")
        sync_google_reviews_to_snowflake = getattr(importlib.import_module("services.google_reviews.as_source.to_snowflake"), "sync_google_reviews_to_snowflake")
        req = GoogleReviewsMigration(**payload)
        return sync_google_reviews_to_snowflake(
            req.source_config, req.destination_config,
            start_date=req.start_date, end_date=req.end_date,
            safety_interval=req.safety_interval, selected_tables=req.selected_tables,
            cmp_id=req.cmp_id, connection_id=req.connection_id, user_id=req.user_id,
            job_name=req.job_name, migration_session_id=req.migration_session_id,
            session=req.session, logs_creation=req.logs_creation,
            connection_name=req.connection_name, company_name=req.company_name,
            drop_and_reload=req.drop_and_reload, log_id=req.log_id,
            celery_child_mode=bool(getattr(req, "celery_child_mode", False)),
        )
    if function_path == "/Snowflake-AmazonS3-migration-yield":
        AmazonS3MigrationRequest = getattr(importlib.import_module("models.amazon_s3_models"), "AmazonS3MigrationRequest")
        AmazonS3Service = getattr(importlib.import_module("services.amazon_s3.service"), "AmazonS3Service")
        return _execute_async(AmazonS3Service.migrate(AmazonS3MigrationRequest(**payload)))
    if function_path == "/AmazonS3-Snowflake-migration-yield":
        SnowflakeS3MigrationRequest = getattr(
            importlib.import_module("models.snowflake_s3_models"), "SnowflakeS3MigrationRequest"
        )
        migrate_snowflake_to_s3 = getattr(
            importlib.import_module("services.snowflake.as_source.to_s3"), "migrate_snowflake_to_s3"
        )
        return migrate_snowflake_to_s3(SnowflakeS3MigrationRequest(**payload))
    if function_path == "/SFTP-Snowflake-migration-yield":
        SnowflakeSFTPMigrationRequest = getattr(
            importlib.import_module("models.snowflake_sftp_models"), "SnowflakeSFTPMigrationRequest"
        )
        migrate_snowflake_to_sftp = getattr(
            importlib.import_module("services.snowflake.as_source.to_sftp"), "migrate_snowflake_to_sftp"
        )
        return migrate_snowflake_to_sftp(SnowflakeSFTPMigrationRequest(**payload))
    if function_path == "/SharePoint-Snowflake-migration-yield":
        SnowflakeSharePointMigrationRequest = getattr(
            importlib.import_module("models.snowflake_sharepoint_models"), "SnowflakeSharePointMigrationRequest"
        )
        migrate_snowflake_to_sharepoint = getattr(
            importlib.import_module("services.snowflake.as_source.to_sharepoint"), "migrate_snowflake_to_sharepoint"
        )
        return migrate_snowflake_to_sharepoint(SnowflakeSharePointMigrationRequest(**payload))
    if function_path == "/AzureDataLakeStorage-Snowflake-migration-yield":
        SnowflakeADLSMigrationRequest = getattr(
            importlib.import_module("models.snowflake_adls_models"), "SnowflakeADLSMigrationRequest"
        )
        migrate_snowflake_to_adls = getattr(
            importlib.import_module("services.snowflake.as_source.to_adls"), "migrate_snowflake_to_adls"
        )
        return migrate_snowflake_to_adls(SnowflakeADLSMigrationRequest(**payload))
    if function_path == "/GoogleDrive-Snowflake-migration-yield":
        GoogleDriveMigrationRequest = getattr(
            importlib.import_module("models.google_drive_models"), "GoogleDriveMigrationRequest"
        )
        sync_snowflake_to_google_drive = getattr(
            importlib.import_module("services.google_drive.as_destination.from_snowflake"), "sync_snowflake_to_google_drive"
        )
        return sync_snowflake_to_google_drive(GoogleDriveMigrationRequest(**payload))

    # --- Source → Snowflake (file sources) ---
    if function_path == "/Snowflake-SFTP-migration-yield":
        SFTPSnowflakeMigrationRequest = getattr(
            importlib.import_module("models.sftp_snowflake_models"), "SFTPSnowflakeMigrationRequest"
        )
        SFTPSnowflakeService = getattr(
            importlib.import_module("services.sftp.as_source.to_snowflake"), "SFTPSnowflakeService"
        )
        return _execute_async(SFTPSnowflakeService.migrate(SFTPSnowflakeMigrationRequest(**payload)))
    if function_path in (
        "/Snowflake-AzureDataLakeStorage-migration-yield",
        "/Snowflake-ADLS-migration-yield",
    ):
        ADLSSnowflakeMigrationRequest = getattr(
            importlib.import_module("models.adls_source_models"), "ADLSSnowflakeMigrationRequest"
        )
        ADLSSnowflakeService = getattr(
            importlib.import_module("services.adls.as_source.to_snowflake"), "ADLSSnowflakeService"
        )
        return _execute_async(ADLSSnowflakeService.migrate(ADLSSnowflakeMigrationRequest(**payload)))
    if function_path == "/Snowflake-GoogleDrive-migration-yield":
        GoogleDriveSnowflakeMigrationRequest = getattr(
            importlib.import_module("models.google_drive_snowflake_models"),
            "GoogleDriveSnowflakeMigrationRequest",
        )
        GoogleDriveSnowflakeService = getattr(
            importlib.import_module("services.google_drive.as_source.to_snowflake"),
            "GoogleDriveSnowflakeService",
        )
        return _execute_async(
            GoogleDriveSnowflakeService.migrate(GoogleDriveSnowflakeMigrationRequest(**payload))
        )
    if function_path == "/Snowflake-SAPOData-migration-yield":
        SAPODataSnowflakeMigrationRequest = getattr(
            importlib.import_module("models.sap_odata_models"),
            "SAPODataSnowflakeMigrationRequest",
        )
        sap_odata_to_snowflake_connector = getattr(
            importlib.import_module("services.sap.as_source.to_snowflake"),
            "sap_odata_to_snowflake_connector",
        )
        return sap_odata_to_snowflake_connector(SAPODataSnowflakeMigrationRequest(**payload))
    if function_path == "/Databricks-SAPOData-migration-yield":
        SAPODataDatabricksMigrationRequest = getattr(
            importlib.import_module("models.sap_odata_models"),
            "SAPODataDatabricksMigrationRequest",
        )
        sap_odata_to_databricks_connector = getattr(
            importlib.import_module("services.sap.as_source.to_databricks"),
            "sap_odata_to_databricks_connector",
        )
        return sap_odata_to_databricks_connector(SAPODataDatabricksMigrationRequest(**payload))
    if function_path == "/AzureDataLakeStorage-SAPOData-migration-yield":
        SAPODataADLSMigrationRequest = getattr(
            importlib.import_module("models.sap_odata_models"),
            "SAPODataADLSMigrationRequest",
        )
        migrate_sap_odata_to_adls = getattr(
            importlib.import_module("services.sap.as_source.to_adls"),
            "migrate_sap_odata_to_adls",
        )
        return migrate_sap_odata_to_adls(SAPODataADLSMigrationRequest(**payload))
    if function_path == "/Snowflake-NetSuite-migration-yield":
        NetSuiteSnowflakeMigrationRequest = getattr(
            importlib.import_module("models.netsuite_models"),
            "NetSuiteSnowflakeMigrationRequest",
        )
        netsuite_to_snowflake_connector = getattr(
            importlib.import_module("services.netsuite.as_source.to_snowflake"),
            "netsuite_to_snowflake_connector",
        )
        return netsuite_to_snowflake_connector(NetSuiteSnowflakeMigrationRequest(**payload))
    if function_path == "/Databricks-NetSuite-migration-yield":
        NetSuiteDatabricksMigrationRequest = getattr(
            importlib.import_module("models.netsuite_models"),
            "NetSuiteDatabricksMigrationRequest",
        )
        netsuite_to_databricks_connector = getattr(
            importlib.import_module("services.netsuite.as_source.to_databricks"),
            "netsuite_to_databricks_connector",
        )
        return netsuite_to_databricks_connector(NetSuiteDatabricksMigrationRequest(**payload))

    # --- File-source auth / list / preview / PK ---

    if function_path == "/MicrosoftDynamics365_FO-listing-tables":
        ListingTables = getattr(importlib.import_module("models.microsoft_dynamics_models"), "ListingTables")
        authenticate_and_list_tables = getattr(importlib.import_module("services.listing_tables.service"), "authenticate_and_list_tables")
        req = ListingTables(**payload)
        source = req.MicrosoftDynamics365_FO or {}
        return authenticate_and_list_tables(
            req.connection_id, source.get("container_name"), source.get("connection_string"),
            req.cmp_id, req.user_id, req.last_checked_date, req.job_name,
            listing_prefixes=payload.get("listing_prefixes"),
        )
    if function_path == "/Snowflake-listing-tables":
        ListingTables = getattr(importlib.import_module("models.snowflake_models"), "ListingTables")
        SnowflakeService = getattr(importlib.import_module("services.snowflake.service"), "SnowflakeService")
        req = ListingTables(**payload)
        return SnowflakeService(req.Snowflake, req.destination_schema).get_tables_and_columns(
            req.connection_id, req.job_name, req.destination_schema
        )
    if function_path == "/Databricks-listing-tables":
        DatabricksService = getattr(
            importlib.import_module("services.databricks.service"),
            "DatabricksService",
        )
        details = payload.get("Databricks") or payload.get("databricks") or {}
        return DatabricksService(
            details,
            destination_schema=details.get("schema"),
            validate_destination_schema=False,
            source_schema=details.get("schema"),
            validate_source_schema=True,
        ).get_tables_and_columns(
            payload.get("connection_id"),
            payload.get("job_name"),
            cmp_id=payload.get("cmp_id"),
            user_id=payload.get("user_id"),
        )
    if function_path == "/SAPOData-listing-tables":
        discover_and_populate_sap_odata_service = getattr(
            importlib.import_module("services.sap.client"),
            "discover_and_populate_sap_odata_service",
        )
        return discover_and_populate_sap_odata_service(
            connection_id=payload.get("connection_id"),
            credentials=payload.get("SAPOData") or {},
            job_name=payload.get("job_name"),
        )
    if function_path == "/NetSuite-listing-tables":
        discover_and_populate_netsuite = getattr(
            importlib.import_module("services.netsuite.listing"),
            "discover_and_populate_netsuite",
        )
        return discover_and_populate_netsuite(
            connection_id=payload.get("connection_id"),
            credentials=payload.get("NetSuite") or payload.get("source_config") or {},
            job_name=payload.get("job_name"),
        )
    if function_path == "/Salesforce-listing-tables":
        from utils.config_helpers import sanitize_listing_payload

        SalesforceListing = getattr(importlib.import_module("models.salesforce_models"), "SalesforceListing")
        get_salesforce_objects_and_columns = getattr(importlib.import_module("services.salesforce.client"), "get_salesforce_objects_and_columns")
        sanitize_listing_payload(payload, ("Salesforce",))
        req = SalesforceListing(**payload)
        sf = req.Salesforce or {}
        return {"objects": get_salesforce_objects_and_columns(
            sf.get("state"), req.connection_id, req.job_name, sf.get("access_token"),
            sf.get("instance_url"), sf.get("refresh_token") or "default-fallback-token",
            sf.get("CLIENT_ID"), sf.get("CLIENT_SECRET"), "production",
            req.is_source or False, sf.get("CUSTOM_DOMAIN"),
            salesforce_credentials=sf,
        )}
    if function_path == "/SalesforceSandbox-listing-tables":
        from utils.config_helpers import sanitize_listing_payload

        SalesforceSandboxListing = getattr(importlib.import_module("models.salesforce_models"), "SalesforceSandboxListing")
        get_salesforce_objects_and_columns = getattr(importlib.import_module("services.salesforce.client"), "get_salesforce_objects_and_columns")
        sanitize_listing_payload(payload, ("SalesforceSandbox",))
        req = SalesforceSandboxListing(**payload)
        sf = req.SalesforceSandbox or {}
        return {"objects": get_salesforce_objects_and_columns(
            sf.get("state"), req.connection_id, req.job_name, sf.get("access_token"),
            sf.get("instance_url"), sf.get("refresh_token"), sf.get("CLIENT_ID"),
            sf.get("CLIENT_SECRET"), "sandbox", req.is_source or False,
            sf.get("CUSTOM_DOMAIN"),
            salesforce_credentials=sf,
        )}
    if function_path == "/GoogleReviews-listing-tables":
        list_google_reviews_tables = getattr(importlib.import_module("services.google_reviews.as_source.to_snowflake"), "list_google_reviews_tables")
        google = payload.get("source_config") or payload.get("GoogleReviews") or {}
        return {"result": list_google_reviews_tables(
            google, connection_id=payload.get("connection_id"), job_name=payload.get("job_name")
        )}
    if function_path == "/AmazonS3-listing-tables":
        AmazonS3ListingTables = getattr(importlib.import_module("models.amazon_s3_models"), "AmazonS3ListingTables")
        AmazonS3Service = getattr(importlib.import_module("services.amazon_s3.service"), "AmazonS3Service")
        req = AmazonS3ListingTables(**payload)
        result = _execute_async(AmazonS3Service.list_tables_with_schema(req))
        db_result = AmazonS3Service.refresh_and_update_s3_tables_in_database(
            connection_id=req.connection_id,
            tables_result=result,
            job_name=getattr(req, "job_name", None),
            migration_session_id=getattr(req, "migration_session_id", None),
        )
        result["tables_written"] = db_result.get("total_tables", 0)
        result["tables_deleted"] = db_result.get("tables_deleted", 0)
        return {"result": result}
    if function_path == "/AmazonS3-listing-files":
        AmazonS3ListingTables = getattr(importlib.import_module("models.amazon_s3_models"), "AmazonS3ListingTables")
        AmazonS3Service = getattr(importlib.import_module("services.amazon_s3.service"), "AmazonS3Service")
        return {"result": _execute_async(AmazonS3Service.list_tables(AmazonS3ListingTables(**payload)))}
    if function_path == "/Snowflake-listing-tables-readonly":
        ListingTables = getattr(importlib.import_module("models.snowflake_models"), "ListingTables")
        SnowflakeService = getattr(importlib.import_module("services.snowflake.service"), "SnowflakeService")
        req = ListingTables(**payload)
        snowflake_details = req.Snowflake or {}
        schema_name = str(snowflake_details.get("schema") or req.destination_schema or "").strip()
        database_name = str(snowflake_details.get("database") or "").strip()
        if not schema_name or not database_name:
            raise RuntimeError("Missing required Snowflake fields: database and schema.")
        service = SnowflakeService(snowflake_details, req.destination_schema)
        conn = service.create_connection()
        cur = conn.cursor()
        try:
            safe_db = '"' + database_name.replace('"', '""') + '"'
            safe_schema = '"' + schema_name.replace('"', '""') + '"'
            safe_schema_like = schema_name.replace("'", "''")
            cur.execute(f"SHOW SCHEMAS LIKE '{safe_schema_like}' IN DATABASE {safe_db}")
            schema_rows = cur.fetchall() or []
            schema_exists = any(
                isinstance(row, (list, tuple)) and len(row) > 1
                and str(row[1]).strip().upper() == schema_name.upper()
                for row in schema_rows
            )
            if not schema_exists:
                return {
                    "status_code": 200, "schema_exists": False,
                    "existing_tables": [], "existing_tables_upper": [],
                    "table_count": 0, "body": "Snowflake schema not found (read-only listing).",
                }
            cur.execute(f"SHOW TABLES IN SCHEMA {safe_db}.{safe_schema}")
            table_rows = cur.fetchall() or []
            table_names = sorted({
                str(row[1]).strip()
                for row in table_rows
                if isinstance(row, (list, tuple)) and len(row) > 1 and str(row[1]).strip()
            })
        finally:
            try: cur.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass
        return {
            "status_code": 200, "schema_exists": True,
            "existing_tables": table_names,
            "existing_tables_upper": [t.upper() for t in table_names],
            "table_count": len(table_names),
            "body": "Read-only Snowflake table listing fetched successfully",
        }
    raise RuntimeError(f"Unsupported direct connector dispatch path: {function_path}")


def _execute_connector(url, payload, headers):
    if CELERY_DIRECT_CONNECTOR_EXECUTION:
        try:
            return _dispatch_direct_connector(payload.get("function_path"), payload), 200
        except Exception:
            if not CELERY_HTTP_FALLBACK_ENABLED:
                raise
    # Ensure migration-function / internal HTTP fallbacks always carry the service key,
    # even if the upstream caller forgot X-INTERNAL-KEY.
    headers = dict(headers or {})
    key = os.getenv("INTERNAL_API_KEY")
    if key and not headers.get("X-INTERNAL-KEY"):
        headers["X-INTERNAL-KEY"] = str(key)
    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS,
        verify=False,
    )
    response.raise_for_status()
    try:
        return response.json(), response.status_code
    except ValueError:
        return {}, response.status_code


# ---------------------------------------------------------------------------
# update_job_status / per-child failure helpers
# ---------------------------------------------------------------------------
def _tables_for_child_payload(payload: Optional[dict]) -> list[str]:
    """Resolve table names assigned to a child task payload."""
    if not isinstance(payload, dict):
        return []
    tables = payload.get("table_names") or []
    if not tables:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        tables = data.get("selected_tables") or data.get("table_names") or []
    return [str(t) for t in tables if t]


def _tables_for_task_from_db(db, job_name: str, task_id: str) -> list[str]:
    rows = db.execute(
        text(
            """
            SELECT DISTINCT table_name
            FROM migration_record
            WHERE job_name = :job_name
              AND task_id = :task_id
              AND table_name IS NOT NULL
              AND table_name <> ''
            """
        ),
        {"job_name": job_name, "task_id": task_id},
    ).scalars().all()
    return [str(t) for t in rows if t]


def _migration_job_name_filter_params(job_name: str) -> dict:
    return {
        "job_name": job_name,
        "job_prefix": f"{job_name}__%",
    }


def _resolve_failure_log_identity(db, job_name: str, connection_id) -> tuple:
    """Return (migration_session_id, cmp_id, user_id) for failure log rows."""
    session_id = db.execute(
        text(
            """
            SELECT migration_session_id
            FROM migration_record
            WHERE connection_id = :connection_id
              AND (job_name = :job_name OR job_name LIKE :job_prefix)
            LIMIT 1
            """
        ),
        {**_migration_job_name_filter_params(job_name), "connection_id": int(connection_id)},
    ).scalar()
    cmp_id = db.execute(
        text("SELECT cmp_id FROM connection WHERE connection_id = :connection_id"),
        {"connection_id": int(connection_id)},
    ).scalar()
    user_id = None
    if session_id is not None:
        user_id = db.execute(
            text(
                """
                SELECT user_id
                FROM log
                WHERE connection_id = :connection_id
                  AND migration_record_id IS NULL
                  AND (
                    job_name = :job_name
                    OR job_name LIKE :job_prefix
                    OR session = :session_id
                  )
                ORDER BY log_id DESC
                LIMIT 1
                """
            ),
            {
                **_migration_job_name_filter_params(job_name),
                "connection_id": int(connection_id),
                "session_id": session_id,
            },
        ).scalar()
    return session_id, cmp_id, user_id


def _insert_table_failure_logs(
    db,
    *,
    job_name: str,
    connection_id,
    tables: list[str],
    message: Optional[str],
) -> None:
    """Persist per-table failed Log rows so migration_status exposes error_message."""
    text_msg = (message or "Migration failed").strip()
    tables = [str(t) for t in (tables or []) if t]
    if not text_msg or not tables:
        return

    session_id, cmp_id, user_id = _resolve_failure_log_identity(db, job_name, connection_id)
    rows = db.execute(
        text(
            """
            SELECT id, table_name
            FROM migration_record
            WHERE connection_id = :connection_id
              AND table_name = ANY(:tables)
              AND (job_name = :job_name OR job_name LIKE :job_prefix)
            """
        ),
        {
            **_migration_job_name_filter_params(job_name),
            "connection_id": int(connection_id),
            "tables": tables,
        },
    ).fetchall()
    if not rows:
        return

    now = datetime.now(timezone.utc)
    for migration_record_id, _table_name in rows:
        db.execute(
            text(
                """
                INSERT INTO log (
                    log_id, cmp_id, connection_id, user_id, time, status,
                    message, session, job_name, migration_record_id
                )
                VALUES (
                    nextval('log_log_id_seq'), :cmp_id, :connection_id, :user_id,
                    :now, 'F', :message, :session_id, :job_name, :migration_record_id
                )
                """
            ),
            {
                "cmp_id": cmp_id,
                "connection_id": int(connection_id),
                "user_id": user_id,
                "now": now,
                "message": text_msg,
                "session_id": session_id,
                "job_name": job_name,
                "migration_record_id": migration_record_id,
            },
        )


_GENERIC_CHILD_FAILURE_RE = re.compile(
    r"^\d+\s+child group\(s\) failed\.?$",
    re.IGNORECASE,
)


def _is_generic_failure_stub(message: Optional[str]) -> bool:
    text = (message or "").strip()
    if not text:
        return True
    if _GENERIC_CHILD_FAILURE_RE.match(text):
        return True
    lowered = text.lower()
    return lowered in {
        "child task failed",
        "task revoked",
        "migration failed",
        "migration failed due to task error.",
    }


def _collect_fanout_failure_reason(
    job_name: str,
    connection_id,
    child_results=None,
) -> str:
    """Build a user-visible failure reason from child payloads and table logs."""
    reasons: list[str] = []
    seen: set[str] = set()

    def _add(raw) -> None:
        text = str(raw or "").strip()
        if not text or _is_generic_failure_stub(text):
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        reasons.append(text[:2000])

    for result in child_results or []:
        if not isinstance(result, dict):
            continue
        for field in ("error", "message"):
            _add(result.get(field))

    try:
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    """
                    SELECT l.message
                    FROM log l
                    INNER JOIN migration_record mr ON mr.id = l.migration_record_id
                    WHERE mr.job_name = :job_name
                      AND mr.connection_id = :connection_id
                      AND l.status IN ('F', 'E')
                      AND l.message IS NOT NULL
                      AND TRIM(l.message) <> ''
                    ORDER BY l.log_id DESC
                    LIMIT 20
                    """
                ),
                {"job_name": job_name, "connection_id": int(connection_id)},
            ).scalars().all()
            for msg in rows:
                if _is_generic_failure_stub(str(msg or "")):
                    continue
                _add(msg)
    except Exception as exc:
        logger.warning(
            "FANOUT_FAILURE_REASON_DB_FAILED | job=%s | err=%s",
            job_name,
            exc,
        )

    if not reasons:
        failed_groups = len(
            [r for r in (child_results or []) if _child_result_is_failed(r)]
        )
        if failed_groups:
            return f"{failed_groups} table group(s) failed during sync."
        return "Migration failed due to task error."

    if len(reasons) == 1:
        return reasons[0]

    # Same root cause repeated per table — show the shared tail once.
    tails = []
    for text in reasons:
        if "processing the table" in text.lower() and ":" in text:
            tails.append(text.split(":", 1)[1].strip())
        else:
            tails.append(text)
    if tails and all(t == tails[0] for t in tails):
        return tails[0][:2000]

    summary = "; ".join(reasons[:2])
    if len(reasons) > 2:
        summary += f"; and {len(reasons) - 2} more error(s)"
    return summary[:2000]


def _format_task_error_message(exception, default="Migration failed due to task error."):
    """Extract a user-visible message from connector/task exceptions."""
    if exception is None:
        return default

    detail = getattr(exception, "detail", None)
    if detail is not None:
        msg = str(detail).strip()
        if msg:
            return msg[:2000]

    response = getattr(exception, "response", None)
    if response is not None:
        try:
            body = response.json()
            if isinstance(body, dict):
                for key in ("detail", "message", "error"):
                    if body.get(key):
                        msg = str(body[key]).strip()
                        if msg:
                            return msg[:2000]
            elif body is not None:
                msg = str(body).strip()
                if msg:
                    return msg[:2000]
        except Exception:
            text = (getattr(response, "text", None) or "").strip()
            if text:
                return text[:2000]

    msg = str(exception).strip()
    if msg:
        return msg[:2000]

    msg = repr(exception).strip()
    if msg and msg not in ("''", '""'):
        return msg[:2000]

    return default


def _format_listing_failure_message(message: Optional[str]) -> str:
    text_msg = (message or "").strip()
    if not text_msg:
        return "Update schema failed."
    lowered = text_msg.lower()
    if lowered.startswith("update schema failed"):
        return text_msg
    return f"Update schema failed. {text_msg}"


def _update_job_level_terminal_log(
    db,
    *,
    job_name: str,
    connection_id,
    message: str,
    log_status: str,
    migration_session_id=None,
) -> None:
    """Flip in-progress job-level activity log to a terminal status (S/F)."""
    text_msg = (message or "").strip()
    if not text_msg:
        return
    params = {
        "message": text_msg,
        "now": datetime.now(timezone.utc),
        "connection_id": int(connection_id),
        "job_name": job_name,
        "job_prefix": f"{job_name}__%",
        "status_i": "I",
        "log_status": log_status,
    }
    if migration_session_id is not None:
        db.execute(
            text(
                """
                UPDATE log
                SET status = :log_status, message = :message, time = :now
                WHERE connection_id = :connection_id
                  AND migration_record_id IS NULL
                  AND (status = :status_i OR (:log_status = 'F' AND status IN ('E', 'F')))
                  AND (
                    job_name = :job_name
                    OR job_name LIKE :job_prefix
                    OR session = :session_id
                  )
                """
            ),
            {**params, "session_id": migration_session_id},
        )
    else:
        db.execute(
            text(
                """
                UPDATE log
                SET status = :log_status, message = :message, time = :now
                WHERE connection_id = :connection_id
                  AND migration_record_id IS NULL
                  AND (status = :status_i OR (:log_status = 'F' AND status IN ('E', 'F')))
                  AND (job_name = :job_name OR job_name LIKE :job_prefix)
                """
            ),
            params,
        )


def _sync_listing_activity_log(
    db,
    *,
    job_name: str,
    connection_id,
    status: str,
    message: Optional[str] = None,
    migration_session_id=None,
) -> None:
    """Keep listing/update-schema activity logs aligned with migration_record status."""
    if "listing_tables" not in (job_name or ""):
        return
    if status == STATUS_FAILED:
        _update_job_level_terminal_log(
            db,
            job_name=job_name,
            connection_id=connection_id,
            message=_format_listing_failure_message(message),
            log_status="F",
            migration_session_id=migration_session_id,
        )
    elif status == STATUS_COMPLETED:
        success_message = (message or "Update schema completed successfully.").strip()
        _update_job_level_terminal_log(
            db,
            job_name=job_name,
            connection_id=connection_id,
            message=success_message,
            log_status="S",
            migration_session_id=migration_session_id,
        )


def _update_job_level_failure_log(
    db,
    *,
    job_name: str,
    connection_id,
    message: Optional[str],
    migration_session_id=None,
) -> None:
    """Flip in-progress job log to failed with a readable message."""
    text_msg = (message or "Migration failed").strip()
    if not text_msg:
        return
    _update_job_level_terminal_log(
        db,
        job_name=job_name,
        connection_id=connection_id,
        message=text_msg,
        log_status="F",
        migration_session_id=migration_session_id,
    )


def _close_terminal_failure_activity(db, job_name: str, connection_id) -> None:
    """
    Migration rows are already failed, but the job-level log is still in progress
    and the Celery children never started. Write the stored error and close that log.
    """
    if not job_name or connection_id is None:
        return
    try:
        migration_session_id = db.execute(
            text(
                "SELECT DISTINCT migration_session_id FROM migration_record WHERE job_name = :job_name"
            ),
            {"job_name": job_name},
        ).scalar()
        existing = db.execute(
            text(
                """
                SELECT message
                FROM log
                WHERE connection_id = :connection_id
                  AND status IN ('F', 'E')
                  AND migration_record_id IS NOT NULL
                  AND (
                    job_name = :job_name
                    OR job_name LIKE :job_prefix
                    OR session = :session_id
                  )
                ORDER BY log_id DESC
                LIMIT 1
                """
            ),
            {
                "connection_id": int(connection_id),
                "job_name": job_name,
                "job_prefix": f"{job_name}__%",
                "session_id": migration_session_id,
            },
        ).scalar()
        message = (existing or "").strip() or "Migration failed before the worker started."
        if not existing:
            tables = [
                str(table_name)
                for table_name in db.execute(
                    text(
                        """
                        SELECT DISTINCT table_name
                        FROM migration_record
                        WHERE job_name = :job_name
                          AND status = :failed
                          AND table_name IS NOT NULL
                          AND table_name <> ''
                        """
                    ),
                    {"job_name": job_name, "failed": STATUS_FAILED},
                ).scalars().all()
                if table_name
            ]
            _insert_table_failure_logs(
                db,
                job_name=job_name,
                connection_id=connection_id,
                tables=tables,
                message=message,
            )
        _update_job_level_failure_log(
            db,
            job_name=job_name,
            connection_id=connection_id,
            message=message,
            migration_session_id=migration_session_id,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(
            "TERMINAL_FAILURE_ACTIVITY_CLOSE_FAILED | job=%s | err=%s",
            job_name,
            exc,
        )


def _reset_child_tables_for_retry(
    job_name: str,
    connection_id,
    tables: list[str],
    *,
    task_id: Optional[str] = None,
) -> bool:
    """Keep MR/STS in_progress while Celery retry countdown is pending."""
    tables = [str(t) for t in (tables or []) if t]
    if not tables:
        return False

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        try:
            migration_session_id = db.execute(
                text(
                    "SELECT DISTINCT migration_session_id FROM migration_record WHERE job_name = :job_name"
                ),
                {"job_name": job_name},
            ).scalar()

            db.execute(
                text(
                    """
                    UPDATE migration_record
                    SET status = :status, updated_at = :now
                    WHERE job_name = :job_name
                      AND table_name = ANY(:tables)
                      AND status <> :completed
                    """
                ),
                {
                    "status": STATUS_IN_PROGRESS,
                    "now": now,
                    "job_name": job_name,
                    "tables": tables,
                    "completed": STATUS_COMPLETED,
                },
            )
            for table_name in tables:
                db.execute(
                    text(
                        """
                        UPDATE source_table_selection
                        SET status = :status
                        WHERE "table" = :table_name
                          AND connection_id = :connection_id
                          AND COALESCE(status, '') <> :completed
                        """
                    ),
                    {
                        "status": STATUS_IN_PROGRESS,
                        "table_name": table_name,
                        "connection_id": connection_id,
                        "completed": STATUS_COMPLETED,
                    },
                )
            db.commit()
            logger.warning(
                "CHILD_TABLES_RETRY_RESET | job=%s | task_id=%s | tables=%s",
                job_name,
                task_id,
                tables,
            )
            if migration_session_id:
                try:
                    trigger_ws_status_update(migration_session_id)
                    trigger_connection_ws_status_update(connection_id)
                    trigger_connection_activity_log_update(connection_id)
                except Exception as ws_err:
                    logger.error(
                        "WebSocket trigger failed session=%s err=%s",
                        migration_session_id,
                        ws_err,
                    )
            return True
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error(
                "CHILD_TABLES_RETRY_RESET_DB_ERROR | job=%s | tables=%s | err=%s",
                job_name,
                tables,
                exc,
            )
            return False


def _fail_child_tables(
    job_name: str,
    connection_id,
    tables: list[str],
    message: Optional[str] = None,
    *,
    task_id: Optional[str] = None,
) -> bool:
    """
    Mark only the given tables failed. Sibling tables (completed or still running)
    are left untouched — bulk update_job_status(FAILED) must not wipe them.
    """
    tables = [str(t) for t in (tables or []) if t]
    if not tables:
        logger.error(
            "CHILD_FAIL_NO_TABLES | job=%s | task_id=%s | message=%s | ACTION=skip_bulk_fail",
            job_name,
            task_id,
            message,
        )
        return False

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        try:
            migration_session_id = db.execute(
                text(
                    "SELECT DISTINCT migration_session_id FROM migration_record WHERE job_name = :job_name"
                ),
                {"job_name": job_name},
            ).scalar()

            db.execute(
                text(
                    """
                    UPDATE migration_record
                    SET status = :status, updated_at = :now
                    WHERE job_name = :job_name
                      AND table_name = ANY(:tables)
                      AND status <> :completed
                    """
                ),
                {
                    "status": STATUS_FAILED,
                    "now": now,
                    "job_name": job_name,
                    "tables": tables,
                    "completed": STATUS_COMPLETED,
                },
            )
            for table_name in tables:
                db.execute(
                    text(
                        """
                        UPDATE source_table_selection
                        SET status = :status
                        WHERE "table" = :table_name
                          AND connection_id = :connection_id
                          AND COALESCE(status, '') <> :completed
                        """
                    ),
                    {
                        "status": STATUS_FAILED,
                        "table_name": table_name,
                        "connection_id": connection_id,
                        "completed": STATUS_COMPLETED,
                    },
                )
            _insert_table_failure_logs(
                db,
                job_name=job_name,
                connection_id=connection_id,
                tables=tables,
                message=message,
            )
            _update_job_level_failure_log(
                db,
                job_name=job_name,
                connection_id=connection_id,
                message=message,
                migration_session_id=migration_session_id,
            )
            db.commit()
            logger.warning(
                "CHILD_TABLES_FAILED | job=%s | task_id=%s | tables=%s | message=%s",
                job_name,
                task_id,
                tables,
                message,
            )
            for notify, identifier in (
                (trigger_ws_status_update, migration_session_id),
                (trigger_connection_ws_status_update, connection_id),
                (trigger_connection_activity_log_update, connection_id),
            ):
                if identifier:
                    try:
                        notify(identifier)
                    except Exception as ws_err:
                        logger.error("Failure status notification failed: %s", ws_err)
            return True
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error(
                "CHILD_TABLES_FAILED_DB_ERROR | job=%s | tables=%s | err=%s",
                job_name,
                tables,
                exc,
            )
            return False


def _fail_open_job_tables(
    job_name: str,
    connection_id,
    message: Optional[str] = None,
    *,
    update_job_level_log: bool = True,
) -> bool:
    """
    On partial job failure, mark only non-completed migration rows failed.
    Preserves tables that already completed successfully.

    When ``update_job_level_log`` is False, migration_record / table rows are
    updated but the job-level activity log is left for ``_log_aggregated_completion``
    (partial runs need status P + table counts, not a generic child-group stub).
    """
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        try:
            migration_session_id = db.execute(
                text(
                    "SELECT DISTINCT migration_session_id FROM migration_record WHERE job_name = :job_name"
                ),
                {"job_name": job_name},
            ).scalar()

            open_tables = db.execute(
                text(
                    """
                    SELECT DISTINCT table_name
                    FROM migration_record
                    WHERE job_name = :job_name
                      AND COALESCE(status, '') <> :completed
                      AND table_name IS NOT NULL
                      AND table_name <> ''
                    """
                ),
                {
                    "job_name": job_name,
                    "completed": STATUS_COMPLETED,
                },
            ).scalars().all()

            db.execute(
                text(
                    """
                    UPDATE migration_record
                    SET status = :status, updated_at = :now
                    WHERE job_name = :job_name
                      AND COALESCE(status, '') <> :completed
                    """
                ),
                {
                    "status": STATUS_FAILED,
                    "now": now,
                    "job_name": job_name,
                    "completed": STATUS_COMPLETED,
                },
            )
            for table_name in open_tables:
                db.execute(
                    text(
                        """
                        UPDATE source_table_selection
                        SET status = :status
                        WHERE "table" = :table_name
                          AND connection_id = :connection_id
                          AND COALESCE(status, '') <> :completed
                        """
                    ),
                    {
                        "status": STATUS_FAILED,
                        "table_name": table_name,
                        "connection_id": connection_id,
                        "completed": STATUS_COMPLETED,
                    },
                )
            failed_tables = list(open_tables) or [
                str(t)
                for t in db.execute(
                    text(
                        """
                        SELECT DISTINCT table_name
                        FROM migration_record
                        WHERE job_name = :job_name
                          AND connection_id = :connection_id
                          AND status = :failed
                          AND table_name IS NOT NULL
                          AND table_name <> ''
                        """
                    ),
                    {
                        "job_name": job_name,
                        "connection_id": int(connection_id),
                        "failed": STATUS_FAILED,
                    },
                ).scalars().all()
                if t
            ]
            resolved_message = (message or "").strip()
            if _is_generic_failure_stub(resolved_message):
                resolved_message = _collect_fanout_failure_reason(
                    job_name,
                    connection_id,
                    None,
                )
            # Finalizer path: per-child handlers already wrote table logs — do not
            # insert another generic stub row for every open table.
            if update_job_level_log:
                _insert_table_failure_logs(
                    db,
                    job_name=job_name,
                    connection_id=connection_id,
                    tables=failed_tables,
                    message=resolved_message,
                )
                _update_job_level_failure_log(
                    db,
                    job_name=job_name,
                    connection_id=connection_id,
                    message=resolved_message,
                    migration_session_id=migration_session_id,
                )
            db.commit()
            logger.warning(
                "OPEN_JOB_TABLES_FAILED | job=%s | open_tables=%s | message=%s | job_log=%s",
                job_name,
                list(open_tables),
                message,
                update_job_level_log,
            )
            for notify, identifier in (
                (trigger_ws_status_update, migration_session_id),
                (trigger_connection_ws_status_update, connection_id),
                (trigger_connection_activity_log_update, connection_id),
            ):
                if identifier:
                    try:
                        notify(identifier)
                    except Exception as ws_err:
                        logger.error("Failure status notification failed: %s", ws_err)
            return True
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("OPEN_JOB_TABLES_FAILED_DB_ERROR | job=%s | err=%s", job_name, exc)
            return False


def _fail_child_on_requeue_cap(
    *,
    job_name: str,
    connection_id,
    task_id: str,
    payload: Optional[dict],
    reason: str,
) -> bool:
    """Fail only this child's tables and revoke the stuck task so finalizer can proceed."""
    tables = _tables_for_child_payload(payload)
    if not tables:
        with SessionLocal() as db:
            tables = _tables_for_task_from_db(db, job_name, task_id)
    message = f"Child requeue cap exceeded ({CELERY_MAX_CHILD_REQUEUES}); {reason}"
    ok = _fail_child_tables(
        job_name,
        connection_id,
        tables,
        message,
        task_id=task_id,
    )
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception as revoke_err:
        logger.warning(
            "REQUEUE_CAP_REVOKE_FAILED | job=%s | task_id=%s | err=%s",
            job_name,
            task_id,
            revoke_err,
        )
    return ok


def update_job_status(job_name, status, connection_id, message=None):
    """
    Bulk-update migration_record (and matching source_table_selection) for a job_name.

    Returns:
        True if the DB transaction committed for this call.
        False if STATUS_COMPLETED was skipped (SKIP_BULK_COMPLETION) or on DB error.
    """
    if status == STATUS_FAILED:
        failure_message = (
            _format_listing_failure_message(message)
            if "listing_tables" in (job_name or "")
            else (message or "").strip() or "Migration failed due to task error."
        )
        # Keep tables that already completed. Only this job's open rows are failed.
        return _fail_open_job_tables(job_name, connection_id, failure_message)
    with SessionLocal() as db:
        try:
            migration_session_id = db.execute(
                text("SELECT DISTINCT migration_session_id FROM migration_record WHERE job_name = :job_name"),
                {"job_name": job_name},
            ).scalar()

            table_names = db.execute(
                text("SELECT DISTINCT table_name FROM migration_record WHERE job_name = :job_name"),
                {"job_name": job_name},
            ).scalars().all()

            real_table_names = [
                str(t).strip()
                for t in (table_names or [])
                if t is not None and str(t).strip()
            ]

            # Guardrail: never bulk-mark an entire job/tables as completed unless
            # table-level statuses already indicate completion.
            # This prevents premature "round-off" completion when some tables are
            # still running/missed in fan-out flows.
            if status == STATUS_COMPLETED and real_table_names:
                completed_count = db.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM source_table_selection
                        WHERE connection_id = :connection_id
                          AND "table" = ANY(:table_names)
                          AND status = :status
                        """
                    ),
                    {
                        "connection_id": connection_id,
                        "table_names": real_table_names,
                        "status": STATUS_COMPLETED,
                    },
                ).scalar() or 0
                total_tables = len(real_table_names)
                if completed_count < total_tables:
                    logger.warning(
                        "SKIP_BULK_COMPLETION | job=%s | completed_tables=%s | total_tables=%s | reason=table_level_not_complete",
                        job_name,
                        completed_count,
                        total_tables,
                    )
                    return False

            db.execute(
                text("UPDATE migration_record SET status = :status, updated_at = :now WHERE job_name = :job_name"),
                {"status": status, "now": datetime.now(timezone.utc), "job_name": job_name},
            )

            for table_name in real_table_names:
                db.execute(
                    text("""
                        UPDATE source_table_selection
                        SET status = :status
                        WHERE "table" = :table_name AND connection_id = :connection_id
                    """),
                    {"status": status, "table_name": table_name, "connection_id": connection_id},
                )

            if status == STATUS_FAILED:
                failure_text = (message or "").strip() or "Migration failed."
                _insert_table_failure_logs(
                    db,
                    job_name=job_name,
                    connection_id=connection_id,
                    tables=real_table_names,
                    message=failure_text,
                )
                _update_job_level_failure_log(
                    db,
                    job_name=job_name,
                    connection_id=connection_id,
                    message=failure_text,
                    migration_session_id=migration_session_id,
                )

            if status in (STATUS_COMPLETED, STATUS_FAILED):
                _sync_listing_activity_log(
                    db,
                    job_name=job_name,
                    connection_id=connection_id,
                    status=status,
                    message=message,
                    migration_session_id=migration_session_id,
                )

            db.commit()

            if migration_session_id:
                try:
                    trigger_ws_status_update(migration_session_id)
                    trigger_connection_ws_status_update(connection_id)
                    trigger_connection_activity_log_update(connection_id)
                except Exception as ws_err:
                    logger.error("WebSocket trigger failed session=%s err=%s", migration_session_id, ws_err)
            return True

        except SQLAlchemyError as e:
            db.rollback()
            logger.error("DB error in update_job_status job=%s err=%s", job_name, e)
            return False


# ---------------------------------------------------------------------------
# call_function — single non-orchestrated job path
# ---------------------------------------------------------------------------
@celery_app.task(
    bind=True,
    name="celery_worker.call_function",
    max_retries=1,
    default_retry_delay=300,
    time_limit=CELERY_TASK_TIME_LIMIT,
    soft_time_limit=CELERY_TASK_SOFT_TIME_LIMIT,
)
def call_function(self, url, data, headers, job_name, connection_id):
    logger.warning("CALL_FUNCTION_START | task_id=%s | job=%s | pod=%s | pid=%s | retry=%s/%s",
                   self.request.id, job_name, os.getenv("HOSTNAME", "unknown"),
                   os.getpid(), self.request.retries, self.max_retries)
    now_iso = datetime.now(timezone.utc).isoformat()
    existing_payload = _load_payload(self.request.id) or {}
    _save_payload(
        self.request.id,
        {
            "url": url,
            "data": data,
            "headers": headers,
            "job_name": job_name,
            "connection_id": connection_id,
            "dispatched_at": existing_payload.get("dispatched_at") or now_iso,
            "requeue_count": int(existing_payload.get("requeue_count") or 0),
            "task_kind": "call_function",
            "assigned_pod": os.getenv("HOSTNAME", "unknown"),
            "assigned_pid": os.getpid(),
            "assigned_at": now_iso,
        },
    )
    try:
        self.update_state(state="IN_PROGRESS")
        current_retry = self.request.retries
        is_final = current_retry >= self.max_retries
        data_with_retry = data.copy()
        data_with_retry.update({
            "retry_attempt": current_retry,
            "max_retries": self.max_retries,
            "is_final_attempt": is_final,
        })

        response_payload, status_code = _execute_connector(url, data_with_retry, headers)
        logger.warning("CALL_FUNCTION_DONE | task_id=%s | job=%s | status=%s",
                       self.request.id, job_name, status_code)

        api_status = str(response_payload.get("status", "")).lower()
        if api_status in ("failed", "error"):
            failure_message = response_payload.get("message") or f"API returned status={api_status}"
            update_job_status(job_name, STATUS_FAILED, connection_id, failure_message)
            return

        if "listing_tables" in (job_name or ""):
            logger.info("LISTING_STATUS_DEFERRED | job=%s", job_name)
        elif CELERY_MARK_COMPLETED_FROM_CELERY:
            update_job_status(job_name, STATUS_COMPLETED, connection_id)

    except Exception as err:
        failure_message = _format_task_error_message(err)
        logger.error("CALL_FUNCTION_ERROR | task_id=%s | job=%s | err=%s",
                     self.request.id, job_name, failure_message)
        if "listing_tables" in (job_name or ""):
            update_job_status(job_name, STATUS_FAILED, connection_id, failure_message)
            return
        if self.request.retries >= self.max_retries:
            update_job_status(job_name, STATUS_FAILED, connection_id, str(err))
        countdown = 300 * (2 ** self.request.retries)
        raise self.retry(exc=err, countdown=countdown)

    except Exception as e:
        logger.error(f"Unexpected error in task '{self.request.id}' for job '{job_name}': {e}")
        update_job_status(job_name, STATUS_FAILED, connection_id, str(e))
        raise self.retry(exc=e)

    finally:
        logger.warning("CALL_FUNCTION_END | task_id=%s | job=%s | pod=%s",
                       self.request.id, job_name, os.getenv("HOSTNAME", "unknown"))


def _maybe_skip_stale_orchestrate(task, job_name: str) -> Optional[dict]:
    """
    Drop orchestrate tasks for jobs with no in_progress migration rows (stale queue
    messages, redelivered tasks after restart, or duplicate enqueue). Frees control
    worker slots instead of reprovisioning workers for dead jobs.
    """
    delivery = getattr(task.request, "delivery_info", None) or {}
    redelivered = bool(delivery.get("redelivered"))
    with SessionLocal() as db:
        counts = db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE status = :in_progress) AS in_progress,
                    COUNT(*) AS total
                FROM migration_record
                WHERE job_name = :job_name
            """),
            {"job_name": job_name, "in_progress": STATUS_IN_PROGRESS},
        ).one()
    in_progress = int(counts.in_progress or 0)
    total = int(counts.total or 0)
    if total == 0 or in_progress == 0:
        logger.warning(
            "ORCHESTRATE_SKIP_STALE | job=%s | redelivered=%s | in_progress=%s | total=%s",
            job_name,
            redelivered,
            in_progress,
            total,
        )
        return {"status": "skipped", "reason": "stale_job"}
    return None


def _cleanup_stale_awaiting_fanouts_on_startup(r) -> int:
    """Clear awaiting-fanout state for jobs that are no longer in_progress."""
    cleared = 0
    for job_name in job_pool.list_awaiting_fanout_jobs(r):
        with SessionLocal() as db:
            active = db.execute(
                text(
                    "SELECT 1 FROM migration_record "
                    "WHERE job_name = :job_name AND status = :status LIMIT 1"
                ),
                {"job_name": job_name, "status": STATUS_IN_PROGRESS},
            ).first()
        if active:
            continue
        try:
            if job_pool.is_flow_fleet_job(job_name):
                job_pool.release_flow_fleet_lease(job_name, r=r)
            else:
                job_pool.teardown_job_workers(job_name, fast=True, r=r)
        except Exception as exc:
            logger.warning(
                "STARTUP_AWAITING_FANOUT_TEARDOWN_FAILED | job=%s | err=%s",
                job_name,
                exc,
            )
        job_pool.clear_awaiting_fanout_state(r, job_name)
        cleared += 1
        logger.warning("STARTUP_AWAITING_FANOUT_CLEARED | job=%s", job_name)
    return cleared


# ---------------------------------------------------------------------------
# orchestrate_migration — fan-out parent
# ---------------------------------------------------------------------------
@celery_app.task(
    bind=True,
    name="celery_worker.orchestrate_migration",
    max_retries=0,
    soft_time_limit=CELERY_ORCHESTRATE_SOFT_TIME_LIMIT,
    time_limit=CELERY_ORCHESTRATE_TIME_LIMIT,
)
def orchestrate_migration(self, url, data, headers, job_name, connection_id, function_path, is_listing_path=False):
    stale_skip = _maybe_skip_stale_orchestrate(self, job_name)
    if stale_skip:
        return stale_skip

    selected_tables = data.get("selected_tables") or []
    if is_listing_path:
        build_listing_prefixes = getattr(
            importlib.import_module("services.listing_tables.service"),
            "build_listing_prefixes",
        )
        listing_prefixes = build_listing_prefixes(data.get("last_checked_date"))
        concrete_prefixes = [p for p in listing_prefixes if p]
        if not concrete_prefixes:
            groups = [[None]]
        else:
            listing_group_count = max(1, min(LISTING_PREFIX_CHILD_GROUPS, len(concrete_prefixes)))
            chunk_size = max(1, (len(concrete_prefixes) + listing_group_count - 1) // listing_group_count)
            groups = [concrete_prefixes[i:i + chunk_size] for i in range(0, len(concrete_prefixes), chunk_size)]
    else:
        groups = _build_migration_fanout_groups(
            selected_tables, is_listing_path=False, function_path=function_path
        )

    if not groups:
        update_job_status(job_name, STATUS_FAILED, connection_id, "No tables to process")
        return {"status": "failed"}

    use_staggered_fanout = (not is_listing_path) and CELERY_ONE_TABLE_PER_CHILD
    logger.warning(
        "FANOUT_START | job=%s | pod=%s | pid=%s | tables=%s | groups=%s | "
        "one_table_per_child=%s | staggered=%s | max_inflight=%s",
        job_name,
        os.getenv("HOSTNAME", "unknown"),
        os.getpid(),
        len(selected_tables),
        len(groups),
        CELERY_ONE_TABLE_PER_CHILD,
        use_staggered_fanout,
        CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB if use_staggered_fanout else "n/a",
    )

    # -----------------------------------------------------------------------
    # Phase 1 — prepare child task IDs and payloads (no DB, no broker yet)
    # -----------------------------------------------------------------------
    children = []
    for i, table_group in enumerate(groups, start=1):
        child_id = str(uuid4())
        child_data = deepcopy(data)
        child_tables = table_group
        if is_listing_path:
            child_tables = ["__listing__"]
            child_data["listing_prefixes"] = table_group
        if _is_reverse_etl_to_salesforce_path(function_path):
            child_data["selected_tables"] = _normalize_salesforce_mapping_entries(child_tables)
        else:
            child_data["selected_tables"] = child_tables
        child_data["parent_task_id"] = self.request.id
        child_data["function_path"] = function_path
        child_data["idempotency_key"] = f"{job_name}:{i}"
        record_tables = _migration_record_table_names(child_tables, function_path)
        children.append({
            "id": child_id,
            "data": child_data,
            "tables": record_tables,
            "listing_prefixes": table_group if is_listing_path else None,
            "group_index": i,
        })

    # -----------------------------------------------------------------------
    # Phase 2 — write all task_id → table mappings to DB in one transaction
    # -----------------------------------------------------------------------
    with SessionLocal() as db:
        for c in children:
            db.execute(
                text("""
                    UPDATE migration_record
                    SET task_id = :task_id, updated_at = :now
                    WHERE job_name = :job_name AND table_name = ANY(:tables)
                """),
                {"task_id": c["id"], "now": datetime.now(timezone.utc),
                 "job_name": job_name, "tables": c["tables"]},
            )
        db.commit()
    logger.warning(
        "FANOUT_DB_MAPPED | job=%s | children=%s | is_listing=%s",
        job_name,
        len(children),
        is_listing_path,
    )

    r = _get_redis()
    plan_for_job = str(data.get("plan") or "")
    pod_cap_for_job = None
    raw_cap = data.get("pod_cap")
    if raw_cap is not None:
        pod_cap_for_job = int(raw_cap)

    max_inflight_for_job = CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB
    execution_plan = None
    resource_config = None
    fanout_awaiting_provision = False
    workers_handed_off = False

    try:
        with SessionLocal() as db:
            resource_config = resource_cfg.resolve_effective_resource_config(
                db,
                connection_id,
                plan_hint=plan_for_job,
                r=r,
                connector_type=data.get("connector_type"),
            )
            company_row = resource_cfg._load_company_context(db, connection_id)
            override = (
                resource_cfg._override_dict_from_company_row(company_row)
                if company_row else {}
            )
            connector = resource_cfg.load_connector_limits(db, connection_id)
            try:
                config = resource_cfg.resolve_full_execution_config(
                    connector, resource_config, override, db=db,
                )
            except resource_cfg.PlanNodeMismatchError as pool_err:
                logger.error(
                    "JOB_PROVISION_FAILED_NODE_MISMATCH | job=%s | error=%s",
                    job_name,
                    pool_err,
                )
                finalize_migration_orchestration.run(
                    [],
                    job_name,
                    connection_id,
                    status="failed",
                    reason=f"Node pool cannot satisfy plan resources: {pool_err}",
                )
                return {"status": "failed", "reason": str(pool_err)}
            tables_n = len(groups)
            resource_config = replace(
                resource_config,
                concurrency=config["concurrency"],
                effective_concurrency=config["concurrency"],
            )
            # Pods from table count / concurrency, capped by plan max_pods and
            # optional pipeline-run budget slice (pod_cap) — stable for flow lifetime.
            flow_extra = resource_config.extra_config or {}
            execution_plan = resource_cfg.compute_execution_plan(
                resource_config,
                tables_n,
                flow_preferred_max_pods=flow_extra.get("flow_preferred_max_pods"),
                pod_cap=pod_cap_for_job,
            )
            # Prefer fleet-wide inflight = pods * concurrency (even assignment stays busy).
            fleet_inflight = (
                execution_plan.effective_pods * execution_plan.effective_concurrency
            )
            max_inflight_for_job = max(
                1,
                min(fleet_inflight, tables_n) if tables_n else fleet_inflight,
            )

            # Even assignment: stable sort + round-robin wave publish order + shard ids.
            sorted_groups = sorted(
                enumerate(groups),
                key=lambda item: (
                    str((item[1][0] if item[1] else "")),
                    item[0],
                ),
            )
            ordered_indices = resource_cfg.order_indices_round_robin(
                len(sorted_groups),
                execution_plan.effective_pods,
            )
            shard_assignment = resource_cfg.assign_even_shards(
                len(sorted_groups),
                execution_plan.effective_pods,
            )

            resource_cfg.store_job_execution_config(
                r,
                job_name,
                execution_plan,
                shard_plan={
                    "effective_pods": execution_plan.effective_pods,
                    "shard_sizes": resource_cfg.partition_even_shard_sizes(
                        tables_n, execution_plan.effective_pods
                    ),
                    "shard_assignment": shard_assignment,
                    "rr_order": ordered_indices,
                    "sorted_group_indices": [orig_i for orig_i, _ in sorted_groups],
                    "max_inflight": max_inflight_for_job,
                    "stable_fleet": True,
                },
                connector_ceiling=config.get("connector_ceiling"),
            )

            resolved_chunk = config["chunk_size"]
            if tables_n == 1:
                resolved_chunk = resource_cfg.apply_single_table_chunk_boost(
                    resolved_chunk,
                    resource_config,
                    job_table_count=tables_n,
                    connector_type=data.get("connector_type"),
                    connector_ceiling=config.get("connector_ceiling"),
                )
            # Apply even shard annotation + RR publish order on already-built children.
            # Map original group index (1-based in children) via sorted_groups / rr_order.
            by_orig = {c["group_index"] - 1: c for c in children}
            reordered = []
            for wave_pos, sorted_pos in enumerate(ordered_indices):
                orig_i = sorted_groups[sorted_pos][0]
                child = by_orig.get(orig_i)
                if child is None:
                    continue
                shard_id = (
                    shard_assignment[sorted_pos]
                    if sorted_pos < len(shard_assignment)
                    else wave_pos % max(1, execution_plan.effective_pods)
                )
                child["data"]["chunk_count"] = resolved_chunk
                child["data"]["job_table_count"] = tables_n
                child["data"]["plan_max_chunk_size"] = resource_config.max_chunk_size
                child["data"]["effective_resource_plan"] = resource_config.plan_name
                child["data"]["shard_id"] = shard_id
                child["data"]["effective_pods"] = execution_plan.effective_pods
                child["shard_id"] = shard_id
                reordered.append(child)
            if len(reordered) == len(children):
                children = reordered
            else:
                for c in children:
                    c["data"]["chunk_count"] = resolved_chunk
                    c["data"]["job_table_count"] = tables_n
                    c["data"]["plan_max_chunk_size"] = resource_config.max_chunk_size
                    c["data"]["effective_resource_plan"] = resource_config.plan_name

            logger.warning(
                "EXECUTION_PLAN | job=%s | plan=%s | source=%s | tables=%s | required_pods=%s | "
                "effective_pods=%s | concurrency=%s | effective_concurrency=%s | max_inflight=%s | "
                "full_dynamic=True | resolved_chunk=%s | even_shards=%s | stable_fleet=True",
                job_name,
                resource_config.plan_name,
                resource_config.source,
                len(groups),
                execution_plan.required_pods,
                execution_plan.effective_pods,
                resource_config.concurrency,
                execution_plan.effective_concurrency,
                max_inflight_for_job,
                resolved_chunk,
                resource_cfg.partition_even_shard_sizes(
                    tables_n, execution_plan.effective_pods
                ),
            )

            prov_replicas = execution_plan.effective_pods
            if job_pool.is_flow_fleet_job(job_name):
                prov_replicas = job_pool.resolve_flow_fleet_provision_replicas(
                    execution_plan.effective_pods,
                    plan=resource_config.plan_name if resource_config else plan_for_job,
                    pod_cap=pod_cap_for_job,
                )
                run_id = job_pool.parse_pipeline_run_id(job_name)
                if run_id is not None:
                    job_pool.refresh_flow_fleet_lease(r, run_id)
            fleet_key = data.get("fleet_key")
            if not fleet_key and job_pool.is_flow_fleet_job(job_name):
                run_id = job_pool.parse_pipeline_run_id(job_name)
                if run_id is not None:
                    fleet_key = f"pipeline_run_{run_id}"
            prov = job_pool.provision_job_workers(
                job_name,
                prov_replicas,
                concurrency=execution_plan.effective_concurrency,
                config=config,
                r=r,
                resource_config=resource_config,
                db=db,
                fleet_key=fleet_key,
            )
            if prov.get("status") == "deferred":
                fanout_awaiting_provision = True
                workers_handed_off = True
            elif prov.get("status") != "ok":
                err = f"Job worker provision failed: {prov.get('reason', prov.get('status'))}"
                update_job_status(job_name, STATUS_FAILED, connection_id, err)
                return {"status": "failed", "reason": prov.get("status")}
            elif job_pool.job_worker_ready_now(job_name, r=r):
                workers_handed_off = True
                logger.warning(
                    "JOB_WORKER_FANOUT_READY | job=%s | effective_pods=%s | groups=%s | pod=%s",
                    job_name,
                    prov_replicas,
                    len(groups),
                    os.getenv("HOSTNAME", "unknown"),
                )
            else:
                fanout_awaiting_provision = True
                workers_handed_off = True
                logger.warning(
                    "FANOUT_DEFERRED_WORKER_READY | job=%s | effective_pods=%s | pod=%s",
                    job_name,
                    prov_replicas,
                    os.getenv("HOSTNAME", "unknown"),
                )
    except SoftTimeLimitExceeded:
        if workers_handed_off or fanout_awaiting_provision:
            job_pool.save_awaiting_fanout_state(
                r,
                job_name,
                child_ids=[c["id"] for c in children],
                max_inflight=max_inflight_for_job,
                use_staggered=use_staggered_fanout,
                connection_id=connection_id,
            )
            _schedule_check_job_status(r, countdown=30)
            logger.warning(
                "ORCHESTRATE_SOFT_LIMIT_AFTER_PROVISION | job=%s | children=%s",
                job_name,
                len(children),
            )
            return {"status": "provision_deferred", "child_groups": len(children)}
        logger.error("ORCHESTRATE_SOFT_LIMIT_BEFORE_PROVISION | job=%s", job_name)
        update_job_status(
            job_name,
            STATUS_FAILED,
            connection_id,
            "Orchestrator timed out before the job worker was started",
        )
        raise
    except Exception as counter_err:
        logger.warning(
            "ACTIVE_MIGRATION_COUNTER_SET_FAILED | job=%s | err=%s | pod=%s",
            job_name,
            counter_err,
            os.getenv("HOSTNAME", "unknown"),
        )

    # -----------------------------------------------------------------------
    # Phase 3 — save requeue payloads, publish to queue
    # -----------------------------------------------------------------------
    now_iso = datetime.now(timezone.utc).isoformat()
    for c in children:
        _save_payload(c["id"], {
            "url": url,
            "data": c["data"],
            "headers": headers,
            "job_name": job_name,
            "connection_id": connection_id,
            "group_index": c["group_index"],
            "table_names": c["tables"],
            "listing_prefixes": c.get("listing_prefixes"),
            "dispatched_at": now_iso,
            "requeue_count": 0,
        })

    parent_ctx = {
        "selected_tables": selected_tables,
        "cmp_id": data.get("cmp_id"),
        "user_id": data.get("user_id"),
        "session": data.get("session"),
        "log_id": data.get("log_id"),
        "migration_session_id": data.get("migration_session_id"),
        "function_path": function_path,
        "connector_type": data.get("connector_type"),
    }
    if function_path in FILE_SOURCE_REFRESH_ROUTES:
        parent_ctx["file_source_config"] = data.get("source_config")
    _store_finalizer_context(
        r,
        job_name=job_name,
        connection_id=connection_id,
        is_listing_path=is_listing_path,
        parent_ctx=parent_ctx,
        children=children,
    )

    if fanout_awaiting_provision:
        job_pool.save_awaiting_fanout_state(
            r,
            job_name,
            child_ids=[c["id"] for c in children],
            max_inflight=max_inflight_for_job,
            use_staggered=use_staggered_fanout,
            connection_id=connection_id,
        )
        _schedule_check_job_status(r, countdown=30)
        logger.warning(
            "FANOUT_AWAITING_PROVISION | job=%s | children=%s | max_inflight=%s",
            job_name,
            len(children),
            max_inflight_for_job,
        )
        return {"status": "provision_deferred", "child_groups": len(children)}

    if use_staggered_fanout:
        published_now = _init_fanout_stagger(
            job_name,
            [c["id"] for c in children],
            max_inflight=max_inflight_for_job,
        )
        try:
            queue_depth = r.llen(CELERY_MIGRATION_QUEUE)
            pending_left = r.llen(_fanout_pending_key(job_name))
            inflight = _fanout_inflight_count(job_name)
        except Exception as e:
            logger.warning("FANOUT_STAGGER_METRICS_FAILED | job=%s | err=%s", job_name, e)
            queue_depth = pending_left = inflight = "unknown"
        logger.warning(
            "FANOUT_DONE | job=%s | children=%s | published_now=%s | pending_left=%s | "
            "inflight=%s | redis_queue_depth=%s | staggered=true",
            job_name,
            len(children),
            published_now,
            pending_left,
            inflight,
            queue_depth,
        )
    else:
        _publish_all_children(children, parent_task_id=self.request.id)
        try:
            queue_depth = r.llen(CELERY_MIGRATION_QUEUE)
            logger.warning(
                "FANOUT_DONE | job=%s | children=%s | redis_queue_depth=%s | prefetch_race=%s",
                job_name,
                len(children),
                queue_depth,
                queue_depth < len(children),
            )
            if queue_depth < len(children):
                logger.warning(
                    "PREFETCH_RACE_WARNING | job=%s | dispatched=%s | visible_in_queue=%s | "
                    "already_consumed=%s | KEDA_may_not_scale_in_time — "
                    "verify worker_prefetch_multiplier=1 and concurrency=3",
                    job_name,
                    len(children),
                    queue_depth,
                    len(children) - queue_depth,
                )
        except Exception as e:
            logger.warning("FANOUT_REDIS_CHECK_FAILED | job=%s | err=%s", job_name, e)

    # Bootstrap reconciliation immediately after fan-out
    _schedule_check_job_status(r, countdown=10)

    return {"status": "queued", "child_groups": len(children)}


# ---------------------------------------------------------------------------
# call_function_child — individual child task
# ---------------------------------------------------------------------------
@celery_app.task(
    bind=True,
    name="celery_worker.call_function_child",
    max_retries=1,
    default_retry_delay=300,
    time_limit=CELERY_TASK_TIME_LIMIT,
    soft_time_limit=CELERY_TASK_SOFT_TIME_LIMIT,
)
def call_function_child(self, url, data, headers, job_name, connection_id, group_index):
    assigned_tables = list(data.get("selected_tables") or [])
    current_retry = self.request.retries

    # Compute how long this task waited in queue before pickup
    payload_meta = _load_payload(self.request.id)
    dispatched_at_str = (payload_meta or {}).get("dispatched_at")
    queue_wait = "unknown"
    if dispatched_at_str:
        try:
            queue_wait = round(
                (datetime.now(timezone.utc) - datetime.fromisoformat(dispatched_at_str)).total_seconds(), 1
            )
        except Exception:
            pass

    logger.warning(
        "CHILD_ASSIGNED | job=%s | child_id=%s | group=%s | pod=%s | pid=%s | "
        "table_count=%s | tables=%s | queue_wait_secs=%s | retry=%s/%s",
        job_name, self.request.id, group_index,
        os.getenv("HOSTNAME", "unknown"), os.getpid(),
        len(assigned_tables), assigned_tables, queue_wait,
        current_retry, self.max_retries,
    )

    connector = data.get("connector_type")
    mem_ratio = resource_cfg.check_runtime_memory_pressure()
    if mem_ratio is not None:
        try:
            import psutil
            job_metrics.observe_memory_rss(
                psutil.Process().memory_info().rss,
                connector=connector,
            )
        except Exception:
            pass
    if mem_ratio is not None and mem_ratio > 0.75:
        logger.warning(
            "FANOUT_REFILL_DEFERRED | job=%s | child_id=%s | reason=memory_pressure | ratio=%.2f",
            job_name,
            self.request.id,
            mem_ratio,
        )

    stored_exec = resource_cfg.load_job_execution_config(_get_redis(), job_name)
    if stored_exec and stored_exec.get("config"):
        cfg = resource_cfg.config_from_dict(stored_exec["config"])
        table_label = assigned_tables[0] if assigned_tables else None
        chunk_mult = resource_cfg.memory_pressure_chunk_multiplier(mem_ratio)
        base_chunk = data.get("chunk_count")
        if chunk_mult < 1.0 and base_chunk:
            base_chunk = max(1, int(int(base_chunk) * chunk_mult))
        job_table_count = int(data.get("job_table_count") or 0)
        if job_table_count == 1:
            connector_ceiling = (
                stored_exec.get("connector_ceiling") if stored_exec else None
            )
            base_chunk = resource_cfg.apply_single_table_chunk_boost(
                int(base_chunk or cfg.max_chunk_size),
                cfg,
                job_table_count=1,
                connector_type=connector,
                connector_ceiling=connector_ceiling,
            )
        clamped_chunk = resource_cfg.clamp_chunk_size(
            base_chunk,
            cfg,
            table_name=str(table_label) if table_label else None,
            connector=connector,
            single_table_job=(job_table_count == 1),
        )
        if clamped_chunk != data.get("chunk_count"):
            data = dict(data)
            data["chunk_count"] = clamped_chunk

    _write_child_audit_event(
        event_name="child_assigned",
        job_name=job_name,
        connection_id=connection_id,
        parent_task_id=data.get("parent_task_id"),
        child_task_id=self.request.id,
        group_index=group_index,
        tables=assigned_tables,
        queue_wait_secs=queue_wait,
        retry=current_retry,
        max_retries=self.max_retries,
        status="assigned",
    )

    lock_owner = f"{os.getenv('HOSTNAME', 'unknown')}:{os.getpid()}"
    lock_acquired = _try_acquire_child_exec_lock(self.request.id, lock_owner)
    lock_finalized = False
    if not lock_acquired:
        holder = _get_child_exec_lock_holder(self.request.id)
        logger.warning(
            "CHILD_DUPLICATE_DELIVERY_SKIPPED | job=%s | child_id=%s | group=%s | holder=%s | "
            "queue_wait_secs=%s | pod=%s | pid=%s",
            job_name,
            self.request.id,
            group_index,
            holder,
            queue_wait,
            os.getenv("HOSTNAME", "unknown"),
            os.getpid(),
        )
        _write_child_audit_event(
            event_name="child_duplicate_skipped",
            job_name=job_name,
            connection_id=connection_id,
            parent_task_id=data.get("parent_task_id"),
            child_task_id=self.request.id,
            group_index=group_index,
            tables=assigned_tables,
            queue_wait_secs=queue_wait,
            retry=current_retry,
            max_retries=self.max_retries,
            status="skipped",
            error=f"duplicate_delivery holder={holder}",
        )
        raise Ignore()

    body = {}
    try:
        payload = deepcopy(data)
        payload.update({
            "retry_attempt": current_retry,
            "max_retries": self.max_retries,
            "is_final_attempt": current_retry >= self.max_retries,
            "celery_child_mode": True,
            "table_group_index": group_index,
        })

        logger.warning(
            "CHILD_EXEC_START | job=%s | child_id=%s | group=%s | pod=%s | pid=%s | table_count=%s",
            job_name, self.request.id, group_index,
            os.getenv("HOSTNAME", "unknown"), os.getpid(), len(assigned_tables),
        )
        _write_child_audit_event(
            event_name="child_exec_start",
            job_name=job_name,
            connection_id=connection_id,
            parent_task_id=data.get("parent_task_id"),
            child_task_id=self.request.id,
            group_index=group_index,
            tables=assigned_tables,
            retry=current_retry,
            max_retries=self.max_retries,
            status="in_progress",
        )
        stored_payload = _load_payload(self.request.id)
        if stored_payload is not None:
            stored_payload["assigned_pod"] = os.getenv("HOSTNAME", "unknown")
            stored_payload["assigned_pid"] = os.getpid()
            stored_payload["assigned_at"] = datetime.now(timezone.utc).isoformat()
            _save_payload(self.request.id, stored_payload)

        with _worker_heartbeat_while_busy(task_id=self.request.id):
            _wait_for_gce_metadata(timeout_secs=45)
            body, _ = _execute_connector(url, payload, headers)
        api_status = str(body.get("status", "completed")).lower()

        logger.warning(
            "CHILD_EXEC_DONE | job=%s | child_id=%s | group=%s | pod=%s | "
            "api_status=%s | rows=%s | ok_tables=%s | failed_tables=%s",
            job_name, self.request.id, group_index, os.getenv("HOSTNAME", "unknown"),
            api_status, body.get("total_rows", 0),
            body.get("successful_tables_count", 0), body.get("failed_tables_count", 0),
        )
        _write_child_audit_event(
            event_name="child_exec_done",
            job_name=job_name,
            connection_id=connection_id,
            parent_task_id=data.get("parent_task_id"),
            child_task_id=self.request.id,
            group_index=group_index,
            tables=assigned_tables,
            retry=current_retry,
            max_retries=self.max_retries,
            status="completed",
            api_status=api_status,
            total_rows=body.get("total_rows", 0),
            successful_tables_count=body.get("successful_tables_count", 0),
            failed_tables_count=body.get("failed_tables_count", 0),
        )

        missing_source_file_failure = (
            bool(body.get("single_mode_missing_files_detected"))
            and bool(body.get("single_mode_missing_files"))
            and int(body.get("failed_tables_count", 0) or 0) > 0
        )
        successful_list = body.get("successful_tables_list")
        failed_list = body.get("failed_tables_list") or []
        failed_count = int(body.get("failed_tables_count", 0) or 0)
        successful_count = int(body.get("successful_tables_count", len(assigned_tables)) or 0)

        # Connector already persisted per-table failed status (e.g. SAP→DBX COPY
        # schema mismatch). Do NOT raise+retry: CHILD_TABLES_RETRY_RESET flips
        # migration_record/source_table_selection back to in_progress and the
        # pipeline UI keeps the hourglass until (or after) the countdown.
        if api_status in ("failed", "error") and not missing_source_file_failure:
            if failed_count <= 0 and not failed_list:
                raise RuntimeError(body.get("message") or f"API returned {api_status}")
            logger.error(
                "CHILD_CONNECTOR_FAILED | job=%s | child_id=%s | group=%s | "
                "failed_tables=%s | failed_count=%s | ok_count=%s | "
                "action=no_retry_connector_already_failed",
                job_name, self.request.id, group_index,
                failed_list, failed_count, successful_count,
            )
            child_status = (
                "failed"
                if successful_count <= 0
                else "partial_failure"
            )
            _delete_payload(self.request.id)
            _mark_child_exec_lock_completed(self.request.id)
            lock_finalized = True
            return {
                "group_index": group_index,
                "status": child_status,
                "tables": assigned_tables,
                "total_rows": int(body.get("total_rows", 0) or 0),
                "total_inserts": int(body.get("total_inserts", 0) or 0),
                "total_updates": int(body.get("total_updates", 0) or 0),
                "total_deleted_recids": int(body.get("total_deleted_recids", 0) or 0),
                "execution_time": float(body.get("execution_time", 0.0) or 0.0),
                "total_tables": int(
                    body.get("total_tables", len(assigned_tables)) or len(assigned_tables)
                ),
                "successful_tables_count": successful_count,
                "failed_tables_count": failed_count or len(failed_list),
                "successful_tables_list": (
                    successful_list if isinstance(successful_list, list) else []
                ),
                "failed_tables_list": failed_list or list(assigned_tables),
                "single_mode_missing_files_detected": bool(
                    body.get("single_mode_missing_files_detected")
                ),
                "single_mode_missing_files": body.get("single_mode_missing_files") or [],
            }

        # Surface partial table failures to the finalizer so it marks the job failed
        if failed_count > 0:
            logger.error(
                "CHILD_PARTIAL_FAILURE | job=%s | child_id=%s | group=%s | "
                "failed_tables=%s | failed_count=%s | ok_count=%s",
                job_name, self.request.id, group_index,
                failed_list, failed_count, successful_count,
            )

        child_status = "partial_failure" if failed_count > 0 else "completed"
        _delete_payload(self.request.id)
        _mark_child_exec_lock_completed(self.request.id)
        lock_finalized = True
        return {
            "group_index": group_index,
            "status": child_status,
            "tables": assigned_tables,
            "total_rows": int(body.get("total_rows", 0) or 0),
            "total_inserts": int(body.get("total_inserts", 0) or 0),
            "total_updates": int(body.get("total_updates", 0) or 0),
            "total_deleted_recids": int(body.get("total_deleted_recids", 0) or 0),
            "execution_time": float(body.get("execution_time", 0.0) or 0.0),
            "total_tables": int(body.get("total_tables", len(assigned_tables)) or len(assigned_tables)),
            "successful_tables_count": successful_count,
            "failed_tables_count": failed_count,
            "successful_tables_list": successful_list if isinstance(successful_list, list) else assigned_tables,
            "failed_tables_list": failed_list,
            "single_mode_missing_files_detected": bool(body.get("single_mode_missing_files_detected")),
            "single_mode_missing_files": body.get("single_mode_missing_files") or [],
        }

    except Exception as exc:
        failure_message = _format_task_error_message(exc)
        non_retryable = _is_non_retryable_oauth_error(exc) or _is_non_retryable_child_error(exc)
        if not non_retryable and current_retry < self.max_retries:
            countdown = 15 if _is_gce_metadata_error(exc) else 300 * (2 ** current_retry)
            logger.warning(
                "CHILD_RETRY | job=%s | child_id=%s | group=%s | pod=%s | "
                "retry=%s/%s | countdown=%ss | error=%s",
                job_name, self.request.id, group_index,
                os.getenv("HOSTNAME", "unknown"),
                current_retry + 1, self.max_retries, countdown, failure_message,
            )
            _write_child_audit_event(
                event_name="child_retry",
                job_name=job_name,
                connection_id=connection_id,
                parent_task_id=data.get("parent_task_id"),
                child_task_id=self.request.id,
                group_index=group_index,
                tables=assigned_tables,
                retry=current_retry + 1,
                max_retries=self.max_retries,
                status="retrying",
                error=failure_message,
            )
            _surface_child_error_to_ui(
                job_name,
                connection_id,
                assigned_tables,
                failure_message,
                mark_failed=False,
                task_id=self.request.id,
                retry_current=current_retry + 1,
                retry_max=self.max_retries,
            )
            _reset_child_tables_for_retry(
                job_name,
                connection_id,
                assigned_tables,
                task_id=self.request.id,
            )
            raise self.retry(exc=exc, countdown=countdown)

        logger.error(
            "CHILD_TERMINAL_FAILURE | job=%s | child_id=%s | group=%s | pod=%s | "
            "tables=%s | table_count=%s | error=%s | ACTION=manual intervention required",
            job_name, self.request.id, group_index,
            os.getenv("HOSTNAME", "unknown"),
            assigned_tables, len(assigned_tables), failure_message,
        )
        _write_child_audit_event(
            event_name="child_terminal_failure",
            job_name=job_name,
            connection_id=connection_id,
            parent_task_id=data.get("parent_task_id"),
            child_task_id=self.request.id,
            group_index=group_index,
            tables=assigned_tables,
            retry=current_retry,
            max_retries=self.max_retries,
            status="failed",
            error=failure_message,
        )
        _surface_child_error_to_ui(
            job_name,
            connection_id,
            assigned_tables,
            failure_message,
            mark_failed=True,
            task_id=self.request.id,
        )
        _delete_payload(self.request.id)
        return {
            "group_index": group_index,
            "status": "failed",
            "error": failure_message,
            "tables": assigned_tables,
            "total_rows": 0,
            "total_inserts": 0,
            "total_updates": 0,
            "total_deleted_recids": 0,
            "execution_time": 0.0,
            "total_tables": len(assigned_tables),
            "successful_tables_count": 0,
            "failed_tables_count": len(assigned_tables),
            "successful_tables_list": [],
            "failed_tables_list": assigned_tables,
            "single_mode_missing_files_detected": bool(body.get("single_mode_missing_files_detected")),
            "single_mode_missing_files": body.get("single_mode_missing_files") or [],
        }

    finally:
        if lock_acquired and not lock_finalized:
            _release_child_exec_lock(self.request.id, lock_owner)
        logger.warning("CHILD_DONE | job=%s | child_id=%s | group=%s | pod=%s",
                       job_name, self.request.id, group_index, os.getenv("HOSTNAME", "unknown"))


# ---------------------------------------------------------------------------
# _log_aggregated_completion
# ---------------------------------------------------------------------------
def _aggregate_row_counts_from_db(job_name: str) -> dict:
    """
    Sum per-table row counts from migration_record/data_transfer for a job.
    Used when Celery child payloads report 0 rows but DB already has counts
    (e.g. finalize runs after migration_record completed but before data_transfer).
    """
    try:
        with SessionLocal() as db:
            row = db.execute(
                text("""
                    SELECT
                        COALESCE(SUM(
                            COALESCE(NULLIF(dt.total_rec, 0), mr.staging_records_count, 0)
                        ), 0) AS total_rows,
                        COALESCE(SUM(COALESCE(dt.new_rec, 0)), 0) AS total_inserts,
                        COALESCE(SUM(COALESCE(dt.mod_rec, 0)), 0) AS total_updates,
                        COALESCE(SUM(COALESCE(dt.del_rec, 0)), 0) AS total_deletes
                    FROM migration_record mr
                    LEFT JOIN data_transfer dt ON dt.migration_record_id = mr.id
                    WHERE mr.job_name = :job_name
                      AND mr.table_name IS NOT NULL
                      AND mr.table_name <> ''
                      AND mr.status = :completed
                """),
                {"job_name": job_name, "completed": STATUS_COMPLETED},
            ).fetchone()
            if not row:
                return {}
            total_rows = int(row[0] or 0)
            if total_rows <= 0:
                return {}
            total_inserts = int(row[1] or 0)
            total_updates = int(row[2] or 0)
            total_deletes = int(row[3] or 0)
            if not total_inserts and not total_updates and not total_deletes:
                total_inserts = total_rows
            return {
                "total_rows": total_rows,
                "total_inserts": total_inserts,
                "total_updates": total_updates,
                "total_deletes": total_deletes,
            }
    except Exception as exc:
        logger.warning("AGG_ROW_DB_FALLBACK_FAILED | job=%s | err=%s", job_name, exc)
        return {}


def _log_aggregated_completion(
    job_name,
    connection_id,
    parent_ctx,
    child_results,
    has_failures,
    relax_table_subset_check=False,
):
    ctx = _enrich_parent_ctx_from_db(job_name, connection_id, dict(parent_ctx or {}))
    if not ctx.get("log_id") and (ctx.get("cmp_id") is None or ctx.get("user_id") is None):
        logger.warning(
            "SKIP_AGG_LOG | job=%s | reason=no_log_identity_after_enrich | keys=%s",
            job_name,
            list(ctx.keys()),
        )
        return

    total_rows = sum(int(r.get("total_rows", 0) or 0) for r in child_results or [])
    total_inserts = sum(int(r.get("total_inserts", 0) or 0) for r in child_results or [])
    total_updates = sum(int(r.get("total_updates", 0) or 0) for r in child_results or [])
    total_deletes = sum(int(r.get("total_deleted_recids", 0) or 0) for r in child_results or [])
    if total_rows <= 0 and child_results and not has_failures:
        db_counts = _aggregate_row_counts_from_db(job_name)
        if db_counts:
            total_rows = db_counts["total_rows"]
            if total_inserts <= 0:
                total_inserts = db_counts["total_inserts"]
            if total_updates <= 0:
                total_updates = db_counts["total_updates"]
            if total_deletes <= 0:
                total_deletes = db_counts["total_deletes"]
            logger.info(
                "AGG_ROW_DB_FALLBACK | job=%s | rows=%s | inserts=%s | updates=%s | deletes=%s",
                job_name,
                total_rows,
                total_inserts,
                total_updates,
                total_deletes,
            )
    execution_time = max(
        (float(r.get("execution_time", 0.0) or 0.0) for r in child_results or []), default=0.0
    )

    successful_tables, failed_tables = [], []
    for r in child_results or []:
        s_list = r.get("successful_tables_list")
        f_list = r.get("failed_tables_list")
        if isinstance(s_list, list):
            successful_tables.extend(s_list)
        if isinstance(f_list, list):
            failed_tables.extend(f_list)
        if not s_list and not f_list:
            assigned = r.get("tables") or []
            (successful_tables if r.get("status") == "completed" else failed_tables).extend(assigned)

    parent_tables = ctx.get("selected_tables") or []
    total_tables = len(parent_tables) if parent_tables else len(successful_tables) + len(failed_tables)

    # When DB bulk completion was not used to gate this call, require every parent-selected
    # table in child payloads before emitting full success (legacy / belt-and-suspenders).
    if not has_failures and parent_tables and not relax_table_subset_check:
        succ_cf = {str(t).casefold() for t in successful_tables}
        missing_tables = [t for t in parent_tables if str(t).casefold() not in succ_cf]
        if missing_tables:
            logger.warning(
                "AGG_LOG_DEFERRED | job=%s | reason=child_payloads_missing_tables | missing=%s",
                job_name,
                missing_tables[:30],
            )
            return

    try:
        log_migration_completion = getattr(
            importlib.import_module("utils.error_utils"), "log_migration_completion"
        )
    except Exception as err:
        logger.error("AGG_LOG_IMPORT_FAILED | job=%s | err=%s", job_name, err)
        return

    try:
        failure_reason = None
        if has_failures:
            failure_reason = _collect_fanout_failure_reason(
                job_name,
                connection_id,
                child_results,
            )
        log_migration_completion(
            execution_time=execution_time,
            total_rows=total_rows,
            cmp_id=ctx.get("cmp_id"),
            connection_id=connection_id,
            user_id=ctx.get("user_id"),
            session=ctx.get("session"),
            inserts=total_inserts,
            updates=total_updates,
            deletes=total_deletes,
            total_tables=total_tables,
            successful_tables=len(successful_tables),
            failed_tables=len(failed_tables),
            job_name=job_name,
            log_id=ctx.get("log_id"),
            include_operation_counts=False,
            failure_reason=failure_reason,
            status_override=(
                "P" if has_failures and successful_tables else
                "F" if has_failures else "S"
            ),
        )
        logger.info("AGG_LOG_WROTE | job=%s | ok=%s | failed=%s | rows=%s",
                    job_name, len(successful_tables), len(failed_tables), total_rows)
    except Exception as err:
        logger.error("AGG_LOG_WRITE_FAILED | job=%s | err=%s", job_name, err)


_FINALIZER_CTX_KEY_PREFIX = "celery:finalizer-ctx:"


def _finalizer_ctx_key(job_name: str) -> str:
    return f"{_FINALIZER_CTX_KEY_PREFIX}{job_name}"


def _delete_finalizer_ctx(job_name: str) -> None:
    try:
        _get_redis().delete(_finalizer_ctx_key(job_name))
    except Exception as exc:
        logger.warning("FINALIZER_CTX_DELETE_FAILED | job=%s | err=%s", job_name, exc)


def _store_finalizer_context(
    r,
    *,
    job_name: str,
    connection_id,
    is_listing_path: bool,
    parent_ctx: dict,
    children: list,
) -> None:
    """Persist finalizer metadata so check_job_status can emit the completion log."""
    r.set(
        _finalizer_ctx_key(job_name),
        json.dumps({
            "connection_id": connection_id,
            "is_listing_path": is_listing_path,
            "parent_ctx": parent_ctx,
            "expected_task_ids": [c["id"] for c in children],
            "total_groups": len(children),
        }),
        ex=CELERY_LONG_RUN_REDIS_TTL_SECONDS,
    )
    logger.info("FINALIZER_CTX_STORED | job=%s | groups=%s", job_name, len(children))


def _recover_stuck_activity_logs(db, r, now) -> None:
    """
    Sessions where migration_record rows are terminal but the job-level activity log
    is still 'Migration in progress' (e.g. deferred fan-out skipped finalizer ctx).
    """
    try:
        stuck_rows = db.execute(
            text("""
                SELECT l.log_id, l.connection_id, l.session, MAX(mr.job_name) AS job_name
                FROM log l
                INNER JOIN migration_record mr
                  ON mr.connection_id = l.connection_id
                 AND mr.migration_session_id = l.session
                WHERE l.migration_record_id IS NULL
                  AND l.status = 'I'
                  AND l.message ILIKE ANY (ARRAY['%migration in progress%', '%update schema in progress%'])
                  AND l.time >= NOW() - INTERVAL '7 days'
                GROUP BY l.log_id, l.connection_id, l.session
                HAVING COUNT(*) FILTER (WHERE mr.status = :in_progress) = 0
                   AND COUNT(*) > 0
                ORDER BY l.log_id DESC
                LIMIT 10
            """),
            {"in_progress": STATUS_IN_PROGRESS},
        ).fetchall()
    except Exception as exc:
        logger.warning("STUCK_ACTIVITY_LOG_SCAN_FAILED | err=%s", exc)
        return

    for log_id, connection_id, session_id, job_name in stuck_rows:
        if not job_name:
            continue
        lock_key = f"celery:stuck-log-recovery:{session_id}"
        if r.exists(lock_key):
            continue
        child_results = _db_derived_child_results(db, job_name)
        if not child_results:
            continue
        logger.warning(
            "STUCK_ACTIVITY_LOG_RECOVERY | log_id=%s | session=%s | job=%s",
            log_id,
            session_id,
            job_name,
        )
        is_listing_job = "listing_tables" in (job_name or "")
        finalize_migration_orchestration.apply_async(
            args=[child_results, job_name, connection_id, is_listing_job, {}],
        )
        r.set(lock_key, now.isoformat(), ex=3600)

    _recover_orphan_activity_logs(db, r, now)


def _recover_orphan_activity_logs(db, r, now) -> None:
    """
    Job-level activity logs stuck on 'Migration in progress' when the session has
    no migration_record rows (aborted start, superseded run, or cleanup).
    """
    try:
        orphan_rows = db.execute(
            text("""
                SELECT l.log_id, l.connection_id, l.session, l.job_name
                FROM log l
                WHERE l.migration_record_id IS NULL
                  AND l.status = 'I'
                  AND l.message ILIKE ANY (
                        ARRAY['%migration in progress%', '%update schema in progress%']
                      )
                  AND l.time >= NOW() - INTERVAL '7 days'
                  AND l.time < NOW() - INTERVAL '30 minutes'
                  AND NOT EXISTS (
                        SELECT 1
                        FROM migration_record mr
                        WHERE mr.connection_id = l.connection_id
                          AND mr.migration_session_id = l.session
                      )
                ORDER BY l.log_id DESC
                LIMIT 10
            """),
        ).fetchall()
    except Exception as exc:
        logger.warning("ORPHAN_ACTIVITY_LOG_SCAN_FAILED | err=%s", exc)
        return

    if not orphan_rows:
        return

    try:
        update_migration_status_log = getattr(
            importlib.import_module("utils.error_utils"),
            "update_migration_status_log",
        )
    except Exception as exc:
        logger.warning("ORPHAN_ACTIVITY_LOG_IMPORT_FAILED | err=%s", exc)
        return

    for log_id, connection_id, session_id, job_name in orphan_rows:
        if not job_name:
            continue
        lock_key = f"celery:orphan-log-recovery:{log_id}"
        if r.exists(lock_key):
            continue
        message = (
            "Migration failed: session ended without starting table migration. "
            "The job may have been superseded by a newer run."
        )
        try:
            updated = update_migration_status_log(
                job_name=job_name,
                message=message,
                status="F",
                session=session_id,
                log_id=log_id,
                connection_id=connection_id,
            )
            if updated:
                logger.warning(
                    "ORPHAN_ACTIVITY_LOG_RECOVERED | log_id=%s | session=%s | job=%s",
                    log_id,
                    session_id,
                    job_name,
                )
                r.set(lock_key, now.isoformat(), ex=3600)
        except Exception as exc:
            logger.warning(
                "ORPHAN_ACTIVITY_LOG_RECOVERY_FAILED | log_id=%s | err=%s",
                log_id,
                exc,
            )


def _resolve_child_result(task_id: str, state: Optional[str] = None) -> Optional[dict]:
    """Build a child result dict from Celery AsyncResult; avoid empty {} on REVOKED."""
    try:
        ar = celery_app.AsyncResult(task_id)
        state = state or ar.state
        if state == "SUCCESS":
            res = ar.result
            if isinstance(res, dict) and res.get("status"):
                return res
            return {"status": "completed", "tables": [], "group_index": 0}
        if state == "FAILURE":
            res = ar.result
            err = str(res) if res else "child task failed"
            return {"status": "failed", "error": err, "tables": []}
        if state == "REVOKED":
            return {"status": "failed", "error": "task revoked", "tables": []}
    except Exception as exc:
        logger.warning("CHILD_RESULT_RESOLVE_FAILED | task_id=%s | err=%s", task_id, exc)
        return {"status": "failed", "error": str(exc), "tables": []}
    return None


def _collect_source_missing_files(child_results: list) -> set:
    missing = set()
    for result in child_results or []:
        if not isinstance(result, dict):
            continue
        if result.get("single_mode_missing_files_detected"):
            missing.update(result.get("single_mode_missing_files") or [])
    return missing


def _maybe_trigger_source_post_missing_file_schema_refresh(
    job_name: str,
    connection_id,
    parent_ctx: dict,
    child_results: list,
) -> bool:
    """
    Run source update-schema once after missing-file failures in single_table_per_file fan-out.
    Child migrate() calls defer refresh when celery_child_mode=True.
    """
    missing_files = _collect_source_missing_files(child_results)
    if not missing_files:
        return False

    ctx = parent_ctx or {}
    function_path = str(ctx.get("function_path") or "")
    route = FILE_SOURCE_REFRESH_ROUTES.get(function_path)
    if not route:
        return False

    listing_path, config_key, source_name = route
    source_config = ctx.get("file_source_config") or ctx.get("s3_source_config")
    cmp_id = ctx.get("cmp_id")
    user_id = ctx.get("user_id")
    if not source_config or cmp_id is None or user_id is None:
        logger.warning(
            "SOURCE_POST_MISSING_REFRESH_SKIP | job=%s | reason=missing_context",
            job_name,
        )
        return False

    refresh_job_name = (
        f"{job_name}_post_missing_file_schema_refresh"
        if job_name
        else f"{source_name}_post_missing_file_schema_refresh_{ctx.get('migration_session_id')}"
    )
    lock_key = f"{source_name}:post_missing_refresh:{job_name}"
    try:
        if not _get_redis().set(lock_key, "1", nx=True, ex=3600):
            logger.info(
                "SOURCE_POST_MISSING_REFRESH_SKIP | job=%s | reason=already_triggered",
                job_name,
            )
            return False
    except Exception as lock_err:
        logger.warning(
            "SOURCE_POST_MISSING_REFRESH_LOCK_FAILED | job=%s | err=%s",
            job_name,
            lock_err,
        )

    try:
        insert_tool_log = getattr(
            importlib.import_module("utils.error_utils"),
            "insert_tool_log",
        )
        insert_tool_log(
            cmp_id,
            connection_id,
            user_id,
            "Update Schema",
            "I",
            "Update schema in progress...",
            ctx.get("migration_session_id") or ctx.get("session"),
            job_name=refresh_job_name,
            migration_record_id=None,
        )
    except Exception as log_err:
        logger.warning(
            "SOURCE_POST_MISSING_REFRESH_START_LOG_FAILED | job=%s | err=%s",
            job_name,
            log_err,
        )

    listing_payload = {
        "connection_id": connection_id,
        config_key: source_config,
        "job_name": refresh_job_name,
        "cmp_id": cmp_id,
        "user_id": user_id,
        "migration_session_id": ctx.get("migration_session_id") or ctx.get("session"),
    }
    try:
        _dispatch_direct_connector(listing_path, listing_payload)
        logger.warning(
            "SOURCE_POST_MISSING_REFRESH_DONE | job=%s | missing_files=%s",
            job_name,
            sorted(missing_files),
        )
        return True
    except Exception as refresh_err:
        logger.error(
            "SOURCE_POST_MISSING_REFRESH_FAILED | job=%s | err=%s",
            job_name,
            refresh_err,
        )
        return False


def _child_result_is_failed(result: dict) -> bool:
    if not isinstance(result, dict):
        return True
    # "partial_failure" means some tables failed within the child; treat as failed
    # so the finalizer marks the overall job failed.
    return str(result.get("status", "")).lower() not in ("completed",)


def _sync_completion_from_child_results(job_name: str, connection_id, child_results: list) -> bool:
    """
    When table-level rows lag behind successful Celery children, sync
    source_table_selection + migration_record so SKIP_BULK_COMPLETION can pass.
    """
    tables = []
    for result in child_results or []:
        if _child_result_is_failed(result):
            continue
        tables.extend(result.get("successful_tables_list") or result.get("tables") or [])
    tables = list({str(t) for t in tables if t})
    if not tables:
        return False
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        try:
            for table_name in tables:
                db.execute(
                    text("""
                        UPDATE source_table_selection
                        SET status = :status
                        WHERE connection_id = :connection_id AND "table" = :table_name
                    """),
                    {"status": STATUS_COMPLETED, "connection_id": connection_id, "table_name": table_name},
                )
                db.execute(
                    text("""
                        UPDATE migration_record
                        SET status = :status, updated_at = :now
                        WHERE job_name = :job_name AND table_name = :table_name
                    """),
                    {"status": STATUS_COMPLETED, "now": now, "job_name": job_name, "table_name": table_name},
                )
            db.commit()
            logger.warning(
                "FINALIZER_SYNC_TABLES | job=%s | synced=%s",
                job_name,
                len(tables),
            )
            return True
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("FINALIZER_SYNC_TABLES_FAILED | job=%s | err=%s", job_name, exc)
            return False


def _job_has_open_migration_rows(db, job_name: str) -> bool:
    count = db.execute(
        text("""
            SELECT COUNT(*)
            FROM migration_record
            WHERE job_name = :job_name
              AND status NOT IN (:completed, :failed)
        """),
        {"job_name": job_name, "completed": STATUS_COMPLETED, "failed": STATUS_FAILED},
    ).scalar() or 0
    return count > 0


def _db_derived_child_results(db, job_name: str) -> list[dict]:
    """Synthesize child payloads from per-table migration_record when Celery lags behind DB."""
    rows = db.execute(
        text("""
            SELECT mr.table_name, mr.status,
                   COALESCE(dt.new_rec, 0) AS new_rec,
                   COALESCE(dt.mod_rec, 0) AS mod_rec,
                   COALESCE(NULLIF(dt.total_rec, 0), mr.staging_records_count, 0) AS total_rec
            FROM migration_record mr
            LEFT JOIN data_transfer dt ON dt.migration_record_id = mr.id
            WHERE mr.job_name = :job_name
              AND mr.table_name IS NOT NULL
              AND mr.table_name <> ''
        """),
        {"job_name": job_name},
    ).fetchall()
    results = []
    for idx, row in enumerate(rows, start=1):
        table_name = row[0]
        status = row[1]
        new_rec = int(row[2] or 0)
        mod_rec = int(row[3] or 0)
        total_rec = int(row[4] or 0)
        if total_rec and not new_rec and not mod_rec:
            new_rec = total_rec
        if status == STATUS_COMPLETED:
            results.append({
                "group_index": idx,
                "status": "completed",
                "tables": [table_name],
                "successful_tables_list": [table_name],
                "failed_tables_list": [],
                "total_rows": total_rec,
                "total_inserts": new_rec,
                "total_updates": mod_rec,
                "execution_time": 0.0,
            })
        elif status == STATUS_FAILED:
            results.append({
                "group_index": idx,
                "status": "failed",
                "tables": [table_name],
                "successful_tables_list": [],
                "failed_tables_list": [table_name],
                "total_rows": total_rec,
                "total_inserts": 0,
                "total_updates": 0,
                "execution_time": 0.0,
            })
    return results


def _dispatch_finalize_from_check_job_status(
    r,
    *,
    job_name: str,
    ctx: dict,
    ctx_key: str,
    child_results: list,
    now,
    reason: str,
) -> None:
    finalize_migration_orchestration.apply_async(
        args=[
            child_results,
            job_name,
            ctx.get("connection_id"),
            bool(ctx.get("is_listing_path")),
            ctx.get("parent_ctx"),
        ]
    )
    logger.warning(
        "FINALIZER_DISPATCHED | job=%s | groups=%s | reason=%s",
        job_name,
        len(child_results),
        reason,
    )
    ctx["_finalizer_dispatched"] = True
    ctx["_dispatched_at"] = now.isoformat()
    r.set(ctx_key, json.dumps(ctx), ex=CELERY_LONG_RUN_REDIS_TTL_SECONDS)


def _clear_finalizer_dispatch_marker(job_name: str) -> None:
    """
    After finalize skipped bulk completion (SKIP_BULK_COMPLETION), clear the soft-dispatch
    flags so check_job_status can dispatch finalize again on the next poll — without
    waiting for FINALIZER_STALE_DISPATCH (120s).
    """
    try:
        r = _get_redis()
        ctx_key = _finalizer_ctx_key(job_name)
        raw = r.get(ctx_key)
        if not raw:
            return
        ctx = json.loads(raw)
        ctx.pop("_finalizer_dispatched", None)
        ctx.pop("_dispatched_at", None)
        r.set(ctx_key, json.dumps(ctx), ex=CELERY_LONG_RUN_REDIS_TTL_SECONDS)
    except Exception as exc:
        logger.warning("FINALIZER_CTX_CLEAR_DISPATCH_FAILED | job=%s | err=%s", job_name, exc)


# ---------------------------------------------------------------------------
# finalize_migration_orchestration
# ---------------------------------------------------------------------------
_FINALIZER_RUN_LOCK_PREFIX = "celery:finalizer-run-lock:"
_FINALIZER_ATTEMPT_PREFIX = "celery:finalizer-attempts:"


def _finalizer_run_lock_key(job_name: str) -> str:
    return f"{_FINALIZER_RUN_LOCK_PREFIX}{job_name}"


def _try_acquire_finalizer_lock(job_name: str, owner: str) -> bool:
    """One in-flight finalizer per job. Fail open if Redis is unavailable."""
    try:
        ttl = max(CELERY_FINALIZER_TIME_LIMIT + 60, 240)
        return bool(_get_redis().set(_finalizer_run_lock_key(job_name), owner, nx=True, ex=ttl))
    except Exception as exc:
        logger.warning("FINALIZER_LOCK_ACQUIRE_FAILED | job=%s | err=%s", job_name, exc)
        return True


def _release_finalizer_lock(job_name: str, owner: str) -> None:
    try:
        r = _get_redis()
        key = _finalizer_run_lock_key(job_name)
        current = r.get(key)
        if isinstance(current, bytes):
            current = current.decode("utf-8", errors="replace")
        if current in (None, owner):
            r.delete(key)
    except Exception as exc:
        logger.warning("FINALIZER_LOCK_RELEASE_FAILED | job=%s | err=%s", job_name, exc)


def _bump_finalizer_attempts(job_name: str) -> int:
    """Count starts, including ones killed by the hard time limit. Fail open."""
    try:
        r = _get_redis()
        key = f"{_FINALIZER_ATTEMPT_PREFIX}{job_name}"
        count = int(r.incr(key))
        r.expire(key, 3600)
        return count
    except Exception as exc:
        logger.warning("FINALIZER_ATTEMPT_COUNT_FAILED | job=%s | err=%s", job_name, exc)
        return 1


def _finalize_migration_orchestration_body(
    child_results,
    job_name,
    connection_id,
    is_listing_path=False,
    parent_ctx=None,
    status=None,
    reason=None,
):
    if status == "failed" and reason:
        logger.error(
            "JOB_PROVISION_FAILED_NODE_MISMATCH | job=%s | error=%s",
            job_name,
            reason,
        )
        update_job_status(job_name, STATUS_FAILED, connection_id, reason)
        _delete_finalizer_ctx(job_name)
        return {"status": "failed", "reason": reason}

    child_results = child_results or []
    failed = [r for r in child_results if _child_result_is_failed(r)]

    if is_listing_path:
        if failed:
            _cleanup_fanout_stagger(job_name)
            _teardown_job_workers_if_isolated(job_name)
            listing_failure = _collect_fanout_failure_reason(
                job_name,
                connection_id,
                failed,
            )
            update_job_status(job_name, STATUS_FAILED, connection_id, listing_failure)
            _delete_finalizer_ctx(job_name)
            return {"status": "failed", "failed_groups": failed}
        update_job_status(job_name, STATUS_COMPLETED, connection_id)
        logger.info("LISTING_STATUS_COMPLETED | job=%s", job_name)
        _delete_finalizer_ctx(job_name)
        return {"status": "completed", "groups": len(child_results)}

    merged_ctx = _enrich_parent_ctx_from_db(job_name, connection_id, dict(parent_ctx or {}))

    if failed:
        failure_message = _collect_fanout_failure_reason(
            job_name,
            connection_id,
            failed,
        )
        if not _fail_open_job_tables(
            job_name,
            connection_id,
            failure_message,
            update_job_level_log=False,
        ):
            _clear_finalizer_dispatch_marker(job_name)
            raise RuntimeError(f"Could not persist failed migration status for {job_name}")
        try:
            _log_aggregated_completion(
                job_name=job_name,
                connection_id=connection_id,
                parent_ctx=merged_ctx,
                child_results=child_results,
                has_failures=True,
            )
        except Exception:
            logger.exception("FAILED_JOB_AGGREGATION_ERROR | job=%s", job_name)
        _maybe_trigger_source_post_missing_file_schema_refresh(
            job_name, connection_id, merged_ctx, child_results
        )
        _cleanup_fanout_stagger(job_name)
        _teardown_job_workers_if_isolated(job_name)
        _delete_finalizer_ctx(job_name)
        return {"status": "failed", "failed_groups": failed}

    # One aggregated "Migration completed …" only after bulk DB completion applies,
    # i.e. every table in source_table_selection is completed (SKIP_BULK_COMPLETION passed).
    if CELERY_MARK_COMPLETED_FROM_CELERY:
        applied = update_job_status(job_name, STATUS_COMPLETED, connection_id)
        if not applied:
            synced = _sync_completion_from_child_results(job_name, connection_id, child_results)
            if synced:
                applied = update_job_status(job_name, STATUS_COMPLETED, connection_id)
        if not applied:
            _clear_finalizer_dispatch_marker(job_name)
            logger.warning(
                "FINALIZER_AGG_LOG_DEFERRED | job=%s | reason=SKIP_BULK_COMPLETION_not_all_tables_completed_yet",
                job_name,
            )
            return {
                "status": "completed",
                "groups": len(child_results),
                "aggregation_log_deferred": True,
            }

    _log_aggregated_completion(
        job_name=job_name,
        connection_id=connection_id,
        parent_ctx=merged_ctx,
        child_results=child_results,
        has_failures=False,
        relax_table_subset_check=True,
    )

    _maybe_trigger_source_post_missing_file_schema_refresh(
        job_name, connection_id, merged_ctx, child_results
    )
    _cleanup_fanout_stagger(job_name)
    _teardown_job_workers_if_isolated(job_name)
    _delete_finalizer_ctx(job_name)
    logger.warning("FINALIZER_COMPLETE | job=%s | groups=%s | status=completed", job_name, len(child_results))
    return {"status": "completed", "groups": len(child_results)}


@celery_app.task(
    bind=True,
    name="celery_worker.finalize_migration_orchestration",
    soft_time_limit=CELERY_FINALIZER_SOFT_TIME_LIMIT,
    time_limit=CELERY_FINALIZER_TIME_LIMIT,
)
def finalize_migration_orchestration(
    self,
    child_results,
    job_name,
    connection_id,
    is_listing_path=False,
    parent_ctx=None,
    status=None,
    reason=None,
):
    """Run completion off the orchestrate pool, once per job, and always return.

    The soft limit acks the message so a blocked database or Kubernetes call
    cannot sit unacked for days. The hard limit kills the child if the soft
    signal cannot interrupt it. After a few starts the job is abandoned so a
    poison finalizer cannot loop forever.
    """
    owner = str(getattr(self.request, "id", None) or f"finalize:{os.getpid()}")
    acquired = _try_acquire_finalizer_lock(job_name, owner)
    if not acquired:
        logger.warning("FINALIZER_SKIPPED_IN_FLIGHT | job=%s | owner=%s", job_name, owner)
        return {"status": "skipped", "reason": "already_running"}
    try:
        attempt = _bump_finalizer_attempts(job_name)
        if attempt > CELERY_FINALIZER_MAX_ATTEMPTS:
            logger.error(
                "FINALIZER_ATTEMPTS_EXHAUSTED | job=%s | attempts=%s",
                job_name,
                attempt,
            )
            _delete_finalizer_ctx(job_name)
            return {"status": "abandoned", "reason": "attempts_exhausted"}
        return _finalize_migration_orchestration_body(
            child_results,
            job_name,
            connection_id,
            is_listing_path=is_listing_path,
            parent_ctx=parent_ctx,
            status=status,
            reason=reason,
        )
    except SoftTimeLimitExceeded:
        logger.error(
            "FINALIZER_TIME_LIMIT | job=%s | soft_limit=%ss",
            job_name,
            CELERY_FINALIZER_SOFT_TIME_LIMIT,
        )
        return {"status": "timed_out"}
    finally:
        _release_finalizer_lock(job_name, owner)


@celery_app.task(
    name="celery_worker.teardown_pipeline_flow_fleet",
    bind=True,
    ignore_result=True,
    soft_time_limit=CELERY_FINALIZER_SOFT_TIME_LIMIT,
    time_limit=CELERY_FINALIZER_TIME_LIMIT,
)
def teardown_pipeline_flow_fleet(self, pipeline_run_id: int):
    """Delete the shared flow fleet for a finished pipeline run."""
    run_id = int(pipeline_run_id)
    logger.warning("FLOW_FLEET_TEARDOWN_REQUESTED | pipeline_run_id=%s", run_id)
    try:
        result = job_pool.teardown_flow_fleet(run_id, fast=True, r=_get_redis())
        logger.warning(
            "FLOW_FLEET_TEARDOWN_DONE | pipeline_run_id=%s | result=%s",
            run_id,
            result.get("status"),
        )
        return result
    except Exception as exc:
        logger.warning(
            "FLOW_FLEET_TEARDOWN_FAILED | pipeline_run_id=%s | err=%s",
            run_id,
            exc,
        )
        return {"status": "failed", "error": str(exc)}


def _redis_finalizer_job_names(r) -> set:
    """
    Job names that still have a Redis orchestration finalizer payload.
    Must be polled even when migration_record has no in_progress rows — otherwise
    once all per-table rows flip to completed, check_job_status would never visit
    the finalizer and the job-level activity log stays stuck on 'Migration in progress'.
    """
    names = set()
    try:
        for key in r.scan_iter(match=f"{_FINALIZER_CTX_KEY_PREFIX}*"):
            if not key or not isinstance(key, str):
                continue
            if key.startswith(_FINALIZER_CTX_KEY_PREFIX):
                names.add(key[len(_FINALIZER_CTX_KEY_PREFIX):])
    except Exception as exc:
        logger.warning("REDIS_FINALIZER_SCAN_FAILED | err=%s", exc)
    return names


# ---------------------------------------------------------------------------
# check_job_status — reconciliation loop
# ---------------------------------------------------------------------------
@celery_app.task(
    soft_time_limit=CELERY_CHECK_JOB_STATUS_SOFT_TIME_LIMIT,
    time_limit=CELERY_CHECK_JOB_STATUS_TIME_LIMIT,
)
def check_job_status():
    if job_pool.is_control_worker() and not job_pool.is_reconcile_worker():
        logger.warning(
            "CHECK_JOB_STATUS_SKIPPED | reason=wrong_worker_role | role=%s | pod=%s",
            CELERY_WORKER_ROLE,
            os.getenv("HOSTNAME", "unknown"),
        )
        return
    r = _get_redis()
    if not _acquire_check_job_status_running_lock(r):
        logger.warning(
            "CHECK_JOB_STATUS_SKIPPED | reason=lock_held | pod=%s | pid=%s",
            os.getenv("HOSTNAME", "unknown"),
            os.getpid(),
        )
        return
    try:
        _check_job_status_impl(r)
    finally:
        _release_check_job_status_running_lock(r)


def _check_job_status_impl(r):
    _touch_worker_heartbeat()
    _reconcile_deadline = (
        time.monotonic() + CELERY_RECONCILE_MAX_SECONDS
        if CELERY_RECONCILE_MAX_SECONDS else None
    )
    if job_pool.job_isolation_enabled():
        try:
            job_metrics.refresh_cluster_gauges(r)
        except Exception as metrics_exc:
            logger.debug("METRICS_REFRESH_SKIPPED | err=%s", metrics_exc)

    with SessionLocal() as db:
        try:
            total_in_progress = db.execute(
                text("SELECT COUNT(*) FROM migration_record WHERE status = :s"),
                {"s": STATUS_IN_PROGRESS},
            ).scalar() or 0
            rows = db.execute(
                text("""
                    SELECT job_name, task_id, connection_id, updated_at
                    FROM migration_record
                    WHERE status = :s
                    ORDER BY updated_at DESC
                    LIMIT :limit
                """),
                {"s": STATUS_IN_PROGRESS, "limit": CELERY_RECONCILE_MAX_JOBS},
            ).fetchall()

            now = datetime.now(timezone.utc)
            _job_task_states = {}
            _job_conn_map = {}
            live_keda_task_ids: set[str] = set()

            if not rows:
                # Do not delete the whole list: tasks can still be PENDING/STARTED while DB
                # briefly has no in_progress rows; only drop terminal Celery ids.
                _repair_inflated_keda_active_list(set())
                _prune_terminal_ids_from_keda_active_list(
                    "KEDA_ACTIVE_TERMINAL_PRUNED_NO_IN_PROGRESS_ROWS",
                    max_ids=CELERY_KEDA_ACTIVE_PRUNE_BATCH_SIZE,
                )
                _reconcile_scale_down_protection(r)
                try:
                    swept = job_pool.sweep_stale_job_workers(db, r)
                    if swept:
                        logger.warning("JOB_WORKER_SWEEP | deleted=%s | jobs=0", swept)
                except Exception as sweep_exc:
                    logger.warning("JOB_WORKER_SWEEP_FAILED | err=%s", sweep_exc)
                logger.debug(
                    "No in-progress migration_record rows | pod=%s | "
                    "KEDA list pruned-of-terminal-only — finalizer sweep uses Redis ctx keys",
                    os.getenv("HOSTNAME", "unknown"),
                )
                _recover_stuck_activity_logs(db, r, now)
            else:
                if total_in_progress > len(rows):
                    logger.warning(
                        "RECONCILE_CAPPED | total=%s | processing=%s | limit=%s",
                        total_in_progress,
                        len(rows),
                        CELERY_RECONCILE_MAX_JOBS,
                    )
                logger.warning(
                    "RECONCILE_START | pod=%s | pid=%s | in_progress_rows=%s | "
                    "queue_depth=%s | keda_active=%s",
                    os.getenv("HOSTNAME", "unknown"), os.getpid(),
                    len(rows), r.llen(CELERY_MIGRATION_QUEUE), r.llen(CELERY_KEDA_ACTIVE_LIST_KEY),
                )
                _repair_inflated_keda_active_list()

                # Deduplicate: one entry per (job, task_id)
                seen = {}
                for job_name, task_id, connection_id, updated_at in rows:
                    if task_id:
                        key = (job_name, task_id, connection_id)
                        if key not in seen:
                            seen[key] = updated_at

                for (job_name, task_id, connection_id), updated_at in seen.items():
                    if _reconcile_time_exceeded(_reconcile_deadline):
                        logger.warning(
                            "RECONCILE_TIME_CAP | deadline_exceeded | processed_so_far=%s | pod=%s",
                            len(_job_task_states), os.getenv("HOSTNAME", "unknown"),
                        )
                        break
                    age_secs = (now - updated_at).total_seconds() if updated_at else 0
                    try:
                        state = celery_app.AsyncResult(task_id).state
                    except Exception as e:
                        logger.error("STATE_CHECK_FAILED | job=%s | task_id=%s | err=%s", job_name, task_id, e)
                        update_job_status(job_name, STATUS_FAILED, connection_id, f"Cannot check task state: {e}")
                        continue

                    # Record state for recovery check
                    _job_task_states.setdefault(job_name, {})[task_id] = state
                    _job_conn_map[job_name] = connection_id
                    tid_norm = _keda_task_id_str(task_id)
                    if tid_norm and state not in _KEDA_TERMINAL_STATES:
                        live_keda_task_ids.add(tid_norm)

                    logger.warning(
                        "RECONCILE_TASK | job=%s | task_id=%s | state=%s | age_secs=%.0f | pod=%s",
                        job_name, task_id, state, age_secs, os.getenv("HOSTNAME", "unknown"),
                    )

                    if state == "SUCCESS":
                        if CELERY_MARK_COMPLETED_FROM_CELERY:
                            # Hard gate: full-job completion is finalizer-only.
                            # check_job_status can reconcile and requeue, but must never mark
                            # the whole job completed from an individual task success.
                            # Recovery is handled below via _job_task_states after the main loop.
                            logger.warning(
                                "CHILD_SUCCESS_FINALIZER_ONLY_DEFER | job=%s | task_id=%s | pod=%s",
                                job_name, task_id, os.getenv("HOSTNAME", "unknown")
                            )

                    elif state in ("FAILURE", "REVOKED"):
                        payload = _load_payload(task_id)
                        tables = _tables_for_child_payload(payload)
                        if not tables:
                            tables = _tables_for_task_from_db(db, job_name, task_id)
                        message = f"Task {state.lower()}"
                        if tables:
                            _fail_child_tables(
                                job_name,
                                connection_id,
                                tables,
                                message,
                                task_id=task_id,
                            )
                        else:
                            _fail_open_job_tables(job_name, connection_id, message)
                        logger.warning(
                            "JOB_CHILD_FAILED | job=%s | task_id=%s | state=%s | tables=%s",
                            job_name,
                            task_id,
                            state,
                            tables,
                        )

                    elif state == "PENDING":
                        payload = _load_payload(task_id)
                        if payload is None:
                            logger.warning(
                                "PENDING_NO_PAYLOAD | job=%s | task_id=%s | age_secs=%.0f | pod=%s",
                                job_name, task_id, age_secs, os.getenv("HOSTNAME", "unknown"),
                            )
                            superseded = db.execute(
                                text(
                                    """
                                    SELECT task_id FROM migration_record
                                    WHERE job_name = :j AND task_id = :tid
                                    LIMIT 1
                                    """
                                ),
                                {"j": job_name, "tid": task_id},
                            ).scalar()
                            if superseded is None:
                                logger.warning(
                                    "PENDING_NO_PAYLOAD_SUPERSEDED | job=%s | task_id=%s | pod=%s",
                                    job_name,
                                    task_id,
                                    os.getenv("HOSTNAME", "unknown"),
                                )
                                continue
                            if _should_fail_stale_pending_no_payload(age_secs):
                                tables = _tables_for_task_from_db(db, job_name, task_id)
                                message = (
                                    f"Stale pending task with no payload (age {age_secs:.0f}s)"
                                )
                                if tables:
                                    _fail_child_tables(
                                        job_name,
                                        connection_id,
                                        tables,
                                        message,
                                        task_id=task_id,
                                    )
                                else:
                                    _fail_open_job_tables(job_name, connection_id, message)
                                logger.warning(
                                    "STALE_NO_PAYLOAD_FAILED | job=%s | task_id=%s | age_secs=%.0f | tables=%s",
                                    job_name,
                                    task_id,
                                    age_secs,
                                    tables,
                                )
                            continue

                        dispatched_at_str = payload.get("dispatched_at")
                        if not dispatched_at_str:
                            continue

                        pending_secs = (now - datetime.fromisoformat(dispatched_at_str)).total_seconds()
                        queue_depth = int(r.llen(CELERY_MIGRATION_QUEUE) or 0)
                        active_depth = int(r.llen(CELERY_KEDA_ACTIVE_LIST_KEY) or 0)
                        diagnosis = _diagnose_pending_child(queue_depth, active_depth)
                        skip_reason = _pending_requeue_skip_reason(diagnosis)

                        logger.warning(
                            "PENDING_DIAGNOSIS | job=%s | task_id=%s | pending_secs=%.0f | "
                            "queue_depth=%s | keda_active=%s | threshold=%s | diagnosis=%s",
                            job_name, task_id, pending_secs,
                            queue_depth, active_depth,
                            CELERY_PENDING_CHILD_REQUEUE_AFTER_SECONDS, diagnosis,
                        )

                        # Prefetch hold / KEDA backlog: wait for workers to scale or slots to
                        # free — requeuing burns the cap and fails tables as pending_timeout.
                        if skip_reason:
                            logger.warning(
                                "PENDING_REQUEUE_SKIPPED | job=%s | task_id=%s | "
                                "pending_secs=%.0f | reason=%s | diagnosis=%s",
                                job_name,
                                task_id,
                                pending_secs,
                                skip_reason,
                                diagnosis,
                            )
                            continue

                        if (
                            CELERY_PENDING_CHILD_REQUEUE_ENABLED
                            and pending_secs > CELERY_PENDING_CHILD_REQUEUE_AFTER_SECONDS
                        ):
                            new_id, new_payload = _requeue_child_task(
                                db=db,
                                r=r,
                                now=now,
                                job_name=job_name,
                                old_task_id=task_id,
                                payload=payload,
                                reason="pending_timeout",
                            )
                            if not new_id:
                                _fail_child_on_requeue_cap(
                                    job_name=job_name,
                                    connection_id=connection_id,
                                    task_id=task_id,
                                    payload=payload,
                                    reason="pending_timeout",
                                )
                                continue

                            logger.warning(
                                "CHILD_REQUEUED | job=%s | old_id=%s | new_id=%s | "
                                "pending_secs=%.0f | group=%s | tables=%s",
                                job_name, task_id, new_id, pending_secs,
                                new_payload.get("group_index"), new_payload.get("table_names"),
                            )

                    elif state == "STARTED":
                        payload = _load_payload(task_id)
                        if not payload:
                            continue
                        elapsed_secs = _task_elapsed_seconds(payload, age_secs, now)
                        if CELERY_STARTED_CHILD_REQUEUE_ENABLED:
                            if _maybe_requeue_orphan_started_child(
                                db=db,
                                r=r,
                                now=now,
                                job_name=job_name,
                                task_id=task_id,
                                connection_id=connection_id,
                                payload=payload,
                                age_secs=age_secs,
                                source="reconcile",
                            ):
                                continue
                            if (
                                CELERY_STARTED_CHILD_REQUEUE_AFTER_SECONDS is not None
                                and elapsed_secs > CELERY_STARTED_CHILD_REQUEUE_AFTER_SECONDS
                            ):
                                _requeue_started_child(
                                    db=db,
                                    r=r,
                                    now=now,
                                    job_name=job_name,
                                    task_id=task_id,
                                    connection_id=connection_id,
                                    payload=payload,
                                    elapsed_secs=elapsed_secs,
                                    reason="started_timeout",
                                    threshold_secs=CELERY_STARTED_CHILD_REQUEUE_AFTER_SECONDS,
                                )
                                continue
                        else:
                            logger.debug(
                                "STARTED_NO_REQUEUE | job=%s | task_id=%s | reason=CELERY_STARTED_CHILD_REQUEUE_ENABLED=false",
                                job_name,
                                task_id,
                            )

                        # Safety net: never let a STARTED child hang forever. Applies even when
                        # requeue is disabled or there is no Celery task time limit.
                        if _maybe_hard_fail_stuck_started_child(
                            job_name=job_name,
                            task_id=task_id,
                            connection_id=connection_id,
                            payload=payload,
                            elapsed_secs=elapsed_secs,
                        ):
                            continue

                    elif state == "IN_PROGRESS" and "listing_tables" in (job_name or ""):
                        payload = _load_payload(task_id)
                        if payload and _maybe_requeue_orphan_started_child(
                            db=db,
                            r=r,
                            now=now,
                            job_name=job_name,
                            task_id=task_id,
                            connection_id=connection_id,
                            payload=payload,
                            age_secs=age_secs,
                            source="listing_reconcile",
                        ):
                            continue

                    elif (
                        MIGRATION_TASK_STUCK_FAIL_SECONDS is not None
                        and age_secs > MIGRATION_TASK_STUCK_FAIL_SECONDS
                    ):
                        update_job_status(job_name, STATUS_FAILED, connection_id,
                                          f"Task stuck for {age_secs:.0f}s")
                        logger.warning("TASK_STUCK_FAILED | job=%s | task_id=%s | age_secs=%.0f",
                                       job_name, task_id, age_secs)

                    else:
                        logger.debug("TASK_RUNNING | job=%s | task_id=%s | state=%s", job_name, task_id, state)

                db.commit()

            # -------------------------------------------------------------------
            # Finalizer check — fire when all expected children are terminal
            # -------------------------------------------------------------------
            # Include Redis ctx keys even when migration_record has no in_progress rows,
            # otherwise the finalizer never runs after the last table completes.
            jobs_for_finalizer = set(row[0] for row in rows) | _redis_finalizer_job_names(r)
            for job_name_fin in jobs_for_finalizer:
                if _reconcile_time_exceeded(_reconcile_deadline):
                    logger.warning(
                        "RECONCILE_TIME_CAP | phase=finalizer | pod=%s",
                        os.getenv("HOSTNAME", "unknown"),
                    )
                    break
                ctx_key = _finalizer_ctx_key(job_name_fin)
                raw = r.get(ctx_key)
                if not raw:
                    continue
                try:
                    ctx = json.loads(raw)
                except Exception:
                    r.delete(ctx_key)
                    continue

                expected_ids = list(ctx.get("expected_task_ids") or [])
                if not expected_ids:
                    r.delete(ctx_key)
                    continue

                states = {tid: celery_app.AsyncResult(tid).state for tid in expected_ids}
                terminal_states = {"SUCCESS", "FAILURE", "REVOKED"}
                all_done = all(s in terminal_states for s in states.values())

                # Drop orphan ctx: job already terminal in DB and children are not running.
                if all_done and not _job_has_open_migration_rows(db, job_name_fin):
                    if all(s == "REVOKED" for s in states.values()):
                        logger.warning(
                            "FINALIZER_CTX_ORPHAN_DELETE | job=%s | reason=all_revoked_and_db_terminal",
                            job_name_fin,
                        )
                        r.delete(ctx_key)
                        continue
                    if all(s == "SUCCESS" for s in states.values()):
                        child_results = [
                            _resolve_child_result(tid, states[tid])
                            for tid in expected_ids
                        ]
                        child_results = [c for c in child_results if c]
                        if child_results and not any(_child_result_is_failed(c) for c in child_results):
                            # DB rows are already terminal. Dispatch at most one
                            # completion finalizer, then drop ctx. Leaving ctx in
                            # place re-enqueues a copy on every reconcile tick and
                            # buries orchestrate_migration behind tens of thousands
                            # of duplicate finalizers on migration-control.
                            late_lock = f"celery:finalizer-late-complete:{job_name_fin}"
                            if not r.set(late_lock, "1", nx=True, ex=3600):
                                logger.warning(
                                    "FINALIZER_CTX_LATE_COMPLETE_SKIP | job=%s | reason=already_queued",
                                    job_name_fin,
                                )
                                r.delete(ctx_key)
                                continue
                            logger.warning(
                                "FINALIZER_CTX_LATE_COMPLETE | job=%s | reason=db_terminal_children_success",
                                job_name_fin,
                            )
                            _dispatch_finalize_from_check_job_status(
                                r,
                                job_name=job_name_fin,
                                ctx=ctx,
                                ctx_key=ctx_key,
                                child_results=child_results,
                                now=now,
                                reason="db_terminal_children_success",
                            )
                            r.delete(ctx_key)
                            continue

                # Skip ctx entries already dispatched (soft-delete marker)
                if ctx.get("_finalizer_dispatched"):
                    dispatched_at_str = ctx.get("_dispatched_at", "")
                    try:
                        dispatched_secs = (
                            now - datetime.fromisoformat(dispatched_at_str)
                        ).total_seconds()
                    except Exception:
                        dispatched_secs = 9999
                    # Wait out the finalizer time limit before queueing another copy.
                    # A second copy used to run beside the first and fill every
                    # control slot while both waited on the same database rows.
                    stale_after = CELERY_FINALIZER_TIME_LIMIT + 30
                    if dispatched_secs < stale_after:
                        logger.warning(
                            "FINALIZER_ALREADY_DISPATCHED | job=%s | dispatched_secs=%.0f | waiting",
                            job_name_fin, dispatched_secs,
                        )
                        continue
                    # > 120 s and job still in_progress — re-dispatch
                    logger.warning(
                        "FINALIZER_STALE_DISPATCH | job=%s | dispatched_secs=%.0f | re-dispatching",
                        job_name_fin, dispatched_secs,
                    )
                    ctx.pop("_finalizer_dispatched", None)
                    ctx.pop("_dispatched_at", None)

                orphan_checks = 0
                for tid in list(expected_ids):
                    if _reconcile_time_exceeded(_reconcile_deadline):
                        break
                    if orphan_checks >= CELERY_FINALIZER_ORPHAN_CHECKS_PER_JOB:
                        break
                    if celery_app.AsyncResult(tid).state != "STARTED":
                        continue
                    orphan_checks += 1
                    new_id = _maybe_requeue_orphan_started_child(
                        db=db,
                        r=r,
                        now=now,
                        job_name=job_name_fin,
                        task_id=tid,
                        connection_id=ctx.get("connection_id"),
                        source="finalizer_poll",
                    )
                    if not new_id:
                        continue
                    expected_ids = [new_id if existing == tid else existing for existing in expected_ids]
                    ctx["expected_task_ids"] = expected_ids
                    r.set(ctx_key, json.dumps(ctx), ex=CELERY_LONG_RUN_REDIS_TTL_SECONDS)

                states = {tid: celery_app.AsyncResult(tid).state for tid in expected_ids}
                all_done = all(s in terminal_states for s in states.values())
                state_counts = {}
                for state_name in states.values():
                    state_counts[state_name] = state_counts.get(state_name, 0) + 1

                logger.warning(
                    "FINALIZER_POLL | job=%s | expected=%s | state_counts=%s | all_done=%s | "
                    "pending=%s | success=%s | failure=%s",
                    job_name_fin, len(expected_ids), state_counts, all_done,
                    state_counts.get("PENDING", 0),
                    state_counts.get("SUCCESS", 0),
                    state_counts.get("FAILURE", 0) + state_counts.get("REVOKED", 0),
                )

                if not all_done:
                    if not _job_has_open_migration_rows(db, job_name_fin):
                        # Never finalize from DB while Celery still has live children.
                        # A prior bulk-fail left MRs terminal while siblings were STARTED
                        # (dimension node 146 / session 4484).
                        celery_active = any(
                            str(s).upper() in (
                                "STARTED",
                                "PENDING",
                                "RETRY",
                                "RECEIVED",
                                "IN_PROGRESS",
                            )
                            for s in states.values()
                        )
                        if celery_active:
                            pending_only = all(
                                str(state_name).upper() in ("PENDING", "RECEIVED", "RETRY")
                                for state_name in states.values()
                            )
                            if pending_only and not any(
                                str(state_name).upper() == "STARTED" for state_name in states.values()
                            ):
                                # Rows are already failed, but the children never started.
                                # Close the activity log with the stored error and drop the
                                # queued tasks so the UI does not stay on "in progress".
                                _close_terminal_failure_activity(
                                    db,
                                    job_name_fin,
                                    ctx.get("connection_id"),
                                )
                                for tid in expected_ids:
                                    try:
                                        celery_app.control.revoke(tid, terminate=False)
                                    except Exception:
                                        pass
                                logger.warning(
                                    "FINALIZER_PENDING_CLOSED | job=%s | state_counts=%s",
                                    job_name_fin,
                                    state_counts,
                                )
                                r.delete(ctx_key)
                                continue
                            logger.warning(
                                "FINALIZER_DB_TERMINAL_SKIPPED | job=%s | reason=celery_still_active | "
                                "state_counts=%s",
                                job_name_fin,
                                state_counts,
                            )
                            continue
                        db_child_results = _db_derived_child_results(db, job_name_fin)
                        if db_child_results:
                            _dispatch_finalize_from_check_job_status(
                                r,
                                job_name=job_name_fin,
                                ctx=ctx,
                                ctx_key=ctx_key,
                                child_results=db_child_results,
                                now=now,
                                reason="db_terminal_celery_lagging",
                            )
                            continue
                    continue

                child_results = [
                    _resolve_child_result(tid, states[tid])
                    for tid in expected_ids
                ]
                child_results = [c for c in child_results if c]

                _dispatch_finalize_from_check_job_status(
                    r,
                    job_name=job_name_fin,
                    ctx=ctx,
                    ctx_key=ctx_key,
                    child_results=child_results,
                    now=now,
                    reason="all_children_terminal",
                )

            # -------------------------------------------------------------------
            # Stuck-finalizer recovery — all children SUCCESS but ctx already gone
            # -------------------------------------------------------------------
            # This handles the case where:
            #   1. All child tasks completed (SUCCESS)
            #   2. Finalizer was dispatched and ctx was cleaned up (hard-deleted,
            #      e.g. by an older version of this code)
            #   3. update_job_status was skipped by SKIP_BULK_COMPLETION guardrail
            #   4. Job is still in_progress with no ctx → permanently stuck
            #
            # Rate-limited to once per 120 s per job via a Redis cooldown key so
            # we don't spam finalize_migration_orchestration on every 20-s tick.
            for jn, task_states in _job_task_states.items():
                if _reconcile_time_exceeded(_reconcile_deadline):
                    break
                if not task_states:
                    continue
                if not all(s == "SUCCESS" for s in task_states.values()):
                    continue
                ctx_key = f"celery:finalizer-ctx:{jn}"
                if r.exists(ctx_key):
                    # Ctx still present — normal finalizer poll above handles it
                    continue
                # Rate-limit: skip if we already fired recovery within the last 120 s
                recovery_lock_key = f"celery:finalizer-recovery-lock:{jn}"
                if r.exists(recovery_lock_key):
                    logger.warning(
                        "STUCK_FINALIZER_RECOVERY_RATELIMITED | job=%s | cooldown_active",
                        jn,
                    )
                    continue
                cid = _job_conn_map.get(jn)
                logger.warning(
                    "STUCK_FINALIZER_RECOVERY | job=%s | all_child_states=SUCCESS | "
                    "ctx_missing | re-dispatching finalizer | pod=%s",
                    jn, os.getenv("HOSTNAME", "unknown"),
                )
                child_results = []
                for tid in task_states:
                    resolved = _resolve_child_result(tid, task_states[tid])
                    if not resolved:
                        continue
                    status = str(resolved.get("status", "")).lower()
                    is_child_payload = "group_index" in resolved
                    if is_child_payload and status in {"completed", "failed"}:
                        child_results.append(resolved)
                if not child_results:
                    logger.warning(
                        "STUCK_FINALIZER_RECOVERY_SKIPPED | job=%s | reason=no_child_results",
                        jn,
                    )
                    continue
                # parent_ctx was lost with Redis ctx; DB enrich inside finalizer resolves log_id.
                is_listing_job = "listing_tables" in (jn or "")
                finalize_migration_orchestration.apply_async(
                    args=[child_results, jn, cid, is_listing_job, {}]
                )
                # Set cooldown so we don't re-dispatch for another 120 s
                r.set(recovery_lock_key, "1", ex=120)
                logger.warning("STUCK_FINALIZER_RE_DISPATCHED | job=%s | task_count=%s",
                               jn, len(task_states))

            # Sync KEDA active list: drop ids whose Celery state is terminal (including ids
            # still listed on migration_record until finalizer updates DB — previously we
            # skipped those and the list stayed inflated until the next postrun).
            _repair_inflated_keda_active_list(live_keda_task_ids)
            _prune_terminal_ids_from_keda_active_list(
                "KEDA_ACTIVE_TERMINAL_PRUNED",
                max_ids=CELERY_KEDA_ACTIVE_PRUNE_BATCH_SIZE,
            )
            active_task_ids = list(live_keda_task_ids)
            after_raw = r.lrange(CELERY_KEDA_ACTIVE_LIST_KEY, 0, -1) or []
            after = {_keda_task_id_str(x) for x in after_raw if _keda_task_id_str(x)}
            missing = _keda_bulk_push_candidates(active_task_ids, after)
            if missing:
                r.lpush(CELERY_KEDA_ACTIVE_LIST_KEY, *missing)

            if scale_down_protection_enabled() and not _reconcile_time_exceeded(_reconcile_deadline):
                try:
                    orphans = detect_and_mark_stale_tasks(r)
                    if orphans:
                        requeued = _requeue_stale_orphan_tasks(db, r, now, orphans)
                        logger.warning(
                            "STALE_TASK_SCAN_DONE | orphans=%s | auto_requeued=%s | pod=%s",
                            len(orphans),
                            requeued,
                            os.getenv("HOSTNAME", "unknown"),
                        )
                except Exception as stale_exc:
                    logger.warning("STALE_TASK_SCAN_FAILED | err=%s", stale_exc)

            # Reconcile Redis busy counters + deletion cost before KEDA floor uses them.
            _reconcile_scale_down_protection(r)

            try:
                swept = job_pool.sweep_stale_job_workers(db, r)
                if swept:
                    logger.warning(
                        "JOB_WORKER_SWEEP | deleted=%s | pod=%s",
                        swept,
                        os.getenv("HOSTNAME", "unknown"),
                    )
            except Exception as sweep_exc:
                logger.warning("JOB_WORKER_SWEEP_FAILED | err=%s", sweep_exc)

            try:
                promoted = job_pool.process_provision_queue(r, db=db)
                if promoted:
                    logger.warning("PROVISION_QUEUE_PROMOTED | count=%s", promoted)
                resumed = _resume_awaiting_fanouts(r)
                if resumed:
                    logger.warning("AWAITING_FANOUT_RESUMED | count=%s", resumed)
            except Exception as prov_exc:
                logger.warning("PROVISION_QUEUE_PROCESS_FAILED | err=%s", prov_exc)

        except SQLAlchemyError as e:
            db.rollback()
            logger.error("DB error in check_job_status: %s", e)
        except Exception as e:
            logger.error("Unexpected error in check_job_status: %s", e)
            raise

    # Reschedule while DB shows in-progress work OR Redis still has migration backlog /
    # KEDA sentinel entries (Django beat does not drive celery_worker.check_job_status).
    with SessionLocal() as db:
        still_active = db.execute(
            text("SELECT 1 FROM migration_record WHERE status = :s LIMIT 1"),
            {"s": STATUS_IN_PROGRESS},
        ).first()
        drain_redis = (r.llen(CELERY_KEDA_ACTIVE_LIST_KEY) or 0) > 0 or (
            r.llen(CELERY_MIGRATION_QUEUE) or 0
        ) > 0
        if still_active or drain_redis:
            _schedule_check_job_status(r)


# ---------------------------------------------------------------------------
# cleanup_orphaned_tasks
# ---------------------------------------------------------------------------
@celery_app.task
def cleanup_orphaned_tasks():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=14)
    with SessionLocal() as db:
        try:
            orphans = db.execute(
                text("""
                    SELECT job_name, connection_id, updated_at
                    FROM migration_record
                    WHERE status = :s AND updated_at < :cutoff
                """),
                {"s": STATUS_IN_PROGRESS, "cutoff": cutoff},
            ).fetchall()

            if orphans:
                logger.warning("ORPHAN_CLEANUP | found=%s orphaned jobs", len(orphans))
                for job_name, connection_id, updated_at in orphans:
                    age = datetime.now(timezone.utc) - updated_at
                    update_job_status(job_name, STATUS_FAILED, connection_id,
                                      f"Task orphaned — no updates for {age}")
                    logger.warning("ORPHAN_FAILED | job=%s | age=%s", job_name, age)

            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("Error in cleanup_orphaned_tasks: %s", e)


def _fail_task_from_signal(task_id, task_name, args, task_kwargs, message):
    """Fail only the job that owns this task. Never use a table name as connection_id."""
    args = args or []
    context = dict(task_kwargs or {})
    if task_name == "celery_worker.finalize_migration_orchestration":
        names = ("child_results", "job_name", "connection_id")
    else:
        names = ("url", "data", "headers", "job_name", "connection_id")
    for name, value in zip(names, args):
        context.setdefault(name, value)
    job_name = context.get("job_name")
    connection_id = context.get("connection_id")
    with SessionLocal() as db:
        if not job_name and task_id:
            row = db.execute(
                text(
                    "SELECT job_name, connection_id FROM migration_record "
                    "WHERE task_id = :task_id LIMIT 1"
                ),
                {"task_id": task_id},
            ).first()
            if row:
                job_name, connection_id = row
        if not job_name:
            logger.warning("TASK_FAILURE_NO_JOB | task_id=%s", task_id)
            return False
        if connection_id is None:
            connection_id = db.execute(
                text(
                    "SELECT connection_id FROM migration_record "
                    "WHERE job_name = :job_name LIMIT 1"
                ),
                {"job_name": job_name},
            ).scalar()
        if connection_id is None:
            logger.warning("TASK_FAILURE_NO_CONNECTION | job=%s", job_name)
            return False
        if task_name == "celery_worker.call_function_child":
            tables = _tables_for_child_payload(context)
            if not tables and task_id:
                tables = _tables_for_task_from_db(db, job_name, task_id)
            if tables:
                return _fail_child_tables(
                    job_name, connection_id, tables, message, task_id=task_id
                )
    return update_job_status(job_name, STATUS_FAILED, connection_id, message)


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------
def _fail_task_from_signal(task_id, task_name, args, task_kwargs, message):
    """Resolve the real connection and fail only the affected task's tables."""
    args = args or []
    context = dict(task_kwargs or {})
    if task_name == "celery_worker.finalize_migration_orchestration":
        names = ("child_results", "job_name", "connection_id")
    else:
        names = ("url", "data", "headers", "job_name", "connection_id")
    for name, value in zip(names, args):
        context.setdefault(name, value)
    job_name = context.get("job_name")
    connection_id = context.get("connection_id")
    if (
        task_name == "celery_worker.orchestrate_migration"
        and job_name
        and _orchestrate_left_fanout_deferred(job_name)
    ):
        return False
    with SessionLocal() as db:
        if not job_name and task_id:
            row = db.execute(
                text("SELECT job_name, connection_id FROM migration_record WHERE task_id = :task_id LIMIT 1"),
                {"task_id": task_id},
            ).first()
            if row:
                job_name, connection_id = row
        if not job_name:
            logger.warning("TASK_FAILURE_NO_JOB | task_id=%s", task_id)
            return False
        if (
            task_name == "celery_worker.orchestrate_migration"
            and _orchestrate_left_fanout_deferred(job_name)
        ):
            return False
        if connection_id is None:
            connection_id = db.execute(
                text("SELECT connection_id FROM migration_record WHERE job_name = :job_name LIMIT 1"),
                {"job_name": job_name},
            ).scalar()
        if connection_id is None:
            logger.warning("TASK_FAILURE_NO_CONNECTION | job=%s", job_name)
            return False
        if task_name == "celery_worker.call_function_child":
            tables = _tables_for_child_payload(context)
            if not tables and task_id:
                tables = _tables_for_task_from_db(db, job_name, task_id)
            return _fail_child_tables(job_name, connection_id, tables, message, task_id=task_id)
    return update_job_status(job_name, STATUS_FAILED, connection_id, message)


def _orchestrate_left_fanout_deferred(job_name: str) -> bool:
    """True when children are waiting on the per-job pods, so a dead orchestrator must not fail the load."""
    if not job_name:
        return False
    try:
        state = job_pool.load_awaiting_fanout_state(_get_redis(), job_name)
    except Exception:
        logger.exception("ORCHESTRATE_DEFER_STATE_CHECK_FAILED | job=%s", job_name)
        return False
    if not state:
        return False
    logger.warning("ORCHESTRATE_DIED_AFTER_DEFER | job=%s", job_name)
    return True


@task_failure.connect
def task_failure_handler(sender=None, exception=None, traceback=None, **kwargs):
    request = getattr(sender, "request", None)
    task_id = kwargs.get("task_id") or getattr(request, "id", None)
    logger.error("TASK_FAILURE_SIGNAL | task_id=%s | exception=%s", task_id, exception)
    try:
        _fail_task_from_signal(
            task_id,
            getattr(sender, "name", None),
            kwargs.get("args") if kwargs.get("args") is not None else getattr(request, "args", None),
            kwargs.get("kwargs") if kwargs.get("kwargs") is not None else getattr(request, "kwargs", None),
            _format_task_error_message(exception),
        )
    except Exception:
        logger.exception("TASK_FAILURE_STATUS_UPDATE_FAILED | task_id=%s", task_id)


@task_revoked.connect
def task_revoked_handler(sender=None, terminated=None, signum=None, expired=None, **kwargs):
    request = kwargs.get("request") or getattr(sender, "request", None)
    task_id = _keda_task_id_str(getattr(request, "id", None))
    reason = "Task revoked"
    if terminated:
        reason += " (terminated)"
    if expired:
        reason += " (expired)"
    if signum:
        reason += f" (signal {signum})"
    logger.warning("TASK_REVOKED | task_id=%s | reason=%s", task_id, reason)
    try:
        if task_id:
            _get_redis().lrem(CELERY_KEDA_ACTIVE_LIST_KEY, 0, task_id)
    except Exception:
        logger.exception("TASK_REVOKED_REDIS_CLEANUP_FAILED | task_id=%s", task_id)
    if not task_id:
        return
    try:
        _fail_task_from_signal(
            task_id,
            getattr(sender, "name", None),
            getattr(request, "args", None),
            getattr(request, "kwargs", None),
            reason,
        )
    except Exception:
        logger.exception("TASK_REVOKED_STATUS_UPDATE_FAILED | task_id=%s", task_id)


@worker_shutdown.connect
def worker_shutdown_handler(sender=None, **kwargs):
    logger.warning("WORKER_SHUTDOWN | pod=%s", os.getenv("HOSTNAME", "unknown"))
    if scale_down_protection_enabled():
        try:
            exit_worker_drain(_get_redis())
        except Exception as exc:
            logger.warning("WORKER_DRAIN_END_FAILED | err=%s", exc)
    try:
        with SessionLocal() as db:
            if FAIL_IN_PROGRESS_ON_WORKER_SHUTDOWN:
                jobs = db.execute(
                    text("SELECT job_name, connection_id FROM migration_record WHERE status = :s"),
                    {"s": STATUS_IN_PROGRESS},
                ).fetchall()
                for job_name, connection_id in jobs:
                    update_job_status(job_name, STATUS_FAILED, connection_id, "Worker shutdown")
    except Exception as e:
        logger.error("Error in worker_shutdown_handler: %s", e)


def _background_startup_reconcile():
    """Run heavy startup work in a daemon thread so on_worker_ready returns fast."""
    import threading

    def _run():
        pod = os.getenv("HOSTNAME", "unknown")
        if job_pool.is_control_worker() and job_pool.job_isolation_enabled():
            try:
                job_pool.startup_orphan_sweep(SessionLocal, _get_redis())
            except Exception as exc:
                logger.warning("STARTUP_ORPHAN_SWEEP_FAILED | err=%s", exc)
            try:
                r = _get_redis()
                pruned = _prune_terminal_ids_from_keda_active_list(
                    "KEDA_ACTIVE_TERMINAL_PRUNED_STARTUP"
                )
                cleared = _cleanup_stale_awaiting_fanouts_on_startup(r)
                if pruned or cleared:
                    logger.warning(
                        "STARTUP_CONTROL_CLEANUP | keda_pruned=%s | awaiting_cleared=%s | pod=%s",
                        pruned,
                        cleared,
                        pod,
                    )
            except Exception as exc:
                logger.warning("STARTUP_CONTROL_CLEANUP_FAILED | err=%s", exc)
        if scale_down_protection_enabled():
            try:
                reconcile_pod_active_tasks_from_inspect(_get_redis(), celery_app)
            except Exception as exc:
                logger.warning("SCALE_DOWN_WORKER_READY_INIT_FAILED | err=%s", exc)
        logger.info("STARTUP_RECONCILE_DONE | pod=%s", pod)

    t = threading.Thread(target=_run, daemon=True, name="startup-reconcile")
    t.start()


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    _touch_worker_heartbeat()
    pod = os.getenv("HOSTNAME", "unknown")
    if job_pool.is_control_worker():
        job_metrics.start_metrics_server(int(os.getenv("CELERY_METRICS_PORT", "9100")))
    # Kick off orphan sweep + scale-down reconcile in background — must not block this
    # signal handler or Celery delays consuming the queue until it returns.
    _background_startup_reconcile()
    if job_pool.is_reconcile_worker():
        try:
            _schedule_check_job_status(_get_redis(), countdown=15)
            logger.info("WORKER_READY | bootstrapped check_job_status | pod=%s", pod)
        except Exception as e:
            logger.warning("Worker ready bootstrap failed: %s", e)


@worker_shutting_down.connect
def on_worker_shutting_down(sender, **kwargs):
    if not scale_down_protection_enabled():
        return
    try:
        enter_worker_drain(
            _get_redis(),
            celery_app=celery_app,
            migration_queue=CELERY_MIGRATION_QUEUE,
        )
    except Exception as exc:
        logger.warning("WORKER_DRAIN_START_FAILED | err=%s", exc)


@before_task_publish.connect
def task_dispatched_handler(headers=None, body=None, **kwargs):
    task_id = headers.get("id")
    task_name = headers.get("task")
    logger.warning("TASK_PUBLISHED | task_name=%s | task_id=%s | pod=%s | pid=%s",
                   task_name, task_id, os.getenv("HOSTNAME", "unknown"), os.getpid())


def signal_handler(signum, frame):
    logger.warning("SIGNAL_RECEIVED | signum=%s | pod=%s", signum, os.getenv("HOSTNAME", "unknown"))
    if scale_down_protection_enabled() and signum in (signal.SIGTERM, signal.SIGINT):
        try:
            enter_worker_drain(
                _get_redis(),
                celery_app=celery_app,
                migration_queue=CELERY_MIGRATION_QUEUE,
            )
        except Exception as exc:
            logger.warning("SIGNAL_DRAIN_FAILED | err=%s", exc)
    try:
        with SessionLocal() as db:
            if FAIL_IN_PROGRESS_ON_WORKER_SHUTDOWN:
                jobs = db.execute(
                    text("SELECT job_name, connection_id FROM migration_record WHERE status = :s"),
                    {"s": STATUS_IN_PROGRESS},
                ).fetchall()
                for job_name, connection_id in jobs:
                    update_job_status(job_name, STATUS_FAILED, connection_id,
                                      f"Process interrupted by signal {signum}")
    except Exception as e:
        logger.error("Error in signal_handler: %s", e)
    # Do not sys.exit() — let Celery handle graceful drain


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
