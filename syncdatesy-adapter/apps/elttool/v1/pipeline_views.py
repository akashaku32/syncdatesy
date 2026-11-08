import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from typing import Any, Optional

import pytz
from django.db import IntegrityError, close_old_connections, connection as db_connection, transaction
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.elttool.models import (
    Connection,
    MigrationBatch,
    MigrationRecord,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PipelineRun,
    User,
)
from apps.elttool.v1.batch_views import _remove_batch_job
from apps.elttool.v1.pipeline_run_helpers import (
    DagCycleError,
    allocate_flow_pod_caps,
    build_batch_dependency_graph,
    compute_node_weights,
    dag_execution_levels,
    node_children_count_violations,
    pick_ready_nodes_weighted,
    plan_concurrency,
    plan_parallel_slots,
    plan_run_pod_cap,
    ready_batch_node_ids,
    pipeline_run_failure_message as _pipeline_run_failure_message,
    rollup_pipeline_overall as _rollup_pipeline_overall,
    task_had_immediate_failure as _task_had_immediate_failure,
    transitive_dependents,
)
from apps.elttool.v1.pipeline_graph import (
    ValidatedGraphResolutionError,
    batch_nodes,
    bridge_edges_on_delete,
    build_validated_graph_snapshot,
    disconnected_batch_node_ids,
    ensure_start_node,
    entry_node_ids,
    execution_node_ids_and_edges,
    get_start_node,
    is_batch_node,
    is_start_node,
    load_validated_graph_for_execution,
    pipeline_graph_fingerprint,
    restore_canvas_to_published_graph,
    validate_connected_to_start,
)
from apps.elttool.services.pipeline_notifications import (
    normalize_notification_emails,
    send_pipeline_batch_notification,
    send_pipeline_flow_notification,
)
from apps.elttool.v1.views import (
    _normalize_notification_email_group_ids,
    _validate_company_email_group_ids,
)
from apps.elttool.v1.permissions import IsAnalystOrAdmin

logger = logging.getLogger(__name__)


def _resolve_pipeline_run_user(run: PipelineRun) -> Optional[User]:
    """
    Resolve a User for pipeline log/task context.

    Scheduler / shell-triggered runs may have ``triggered_by_id=None``. The ``log``
    table requires ``user_id`` NOT NULL, so fall back to pipeline / connection /
    company owner before returning None.
    """
    if run is None:
        return None
    if run.triggered_by_id:
        user = User.objects.filter(user_id=run.triggered_by_id).first()
        if user is not None:
            return user

    pipeline = getattr(run, 'pipeline', None)
    if pipeline is not None:
        created_by_id = getattr(pipeline, 'created_by_id', None)
        if created_by_id:
            user = User.objects.filter(user_id=created_by_id).first()
            if user is not None:
                return user
        cmp_id = getattr(pipeline, 'cmp_id', None)
        if cmp_id:
            user = (
                User.objects.filter(cmp_id=cmp_id)
                .order_by('user_id')
                .first()
            )
            if user is not None:
                return user
    return None


PIPELINE_NODE_TIMEOUT_SECONDS = int(
    os.getenv('PIPELINE_NODE_TIMEOUT_SECONDS', str(4 * 86_400))
)
PIPELINE_NODE_POLL_INTERVAL_SECONDS = int(os.getenv('PIPELINE_NODE_POLL_INTERVAL_SECONDS', '5'))
# A child task can mark a table failed and then reset it to in_progress when it
# retries. Wait this long and peek again before treating that failure as final.
PIPELINE_FAILED_CONFIRM_SECONDS = int(os.getenv('PIPELINE_FAILED_CONFIRM_SECONDS', '30'))
# After orchestrator/worker loss, in_progress rows with PENDING Celery tasks older
# than this are treated as ghost sessions and re-dispatched on resume.
PIPELINE_GHOST_SESSION_MINUTES = int(os.getenv('PIPELINE_GHOST_SESSION_MINUTES', '3'))
PIPELINE_MAX_PARALLEL_NODES = int(os.getenv('PIPELINE_MAX_PARALLEL_NODES', '2'))
# Max direct children per node (out-degree). Not a per-DAG-level width cap.
PIPELINE_MAX_CHILDREN_PER_NODE = int(
    os.getenv(
        'PIPELINE_MAX_CHILDREN_PER_NODE',
        os.getenv('PIPELINE_MAX_PARALLEL_NODES_PER_LEVEL', '2'),
    )
)
# Back-compat alias — prefer PIPELINE_MAX_CHILDREN_PER_NODE.
PIPELINE_MAX_PARALLEL_NODES_PER_LEVEL = PIPELINE_MAX_CHILDREN_PER_NODE
# When true: weighted ready-node picker + run-wide plan pod budget shared across in-flight nodes.
PIPELINE_FLOW_POD_BUDGET_ENABLED = os.getenv(
    'PIPELINE_FLOW_POD_BUDGET_ENABLED', 'false'
).lower() in ('1', 'true', 'yes', 'on')
# When true: one worker fleet per pipeline run (plan max pods), reused across nodes.
# Default on so mid-run batches do not re-compete for company_job_cap.
PIPELINE_FLOW_FLEET_REUSE_ENABLED = os.getenv(
    'PIPELINE_FLOW_FLEET_REUSE_ENABLED', 'true'
).lower() in ('1', 'true', 'yes', 'on')
FLOW_FLEET_LEASE_TTL_SECONDS = int(
    os.getenv('FLOW_FLEET_LEASE_TTL_SECONDS', str(4 * 86_400))
)
# When true, a flow hands off one DAG step and returns. A short poll task
# continues it, so one worker process is not held for the whole load.
# Default on so many organisations can orchestrate without saturating
# celery-worker-django processes.
PIPELINE_NONBLOCKING_ORCHESTRATION = os.getenv(
    'PIPELINE_NONBLOCKING_ORCHESTRATION', 'true'
).lower() in ('1', 'true', 'yes', 'on')
PIPELINE_POLL_COUNTDOWN_SECONDS = int(os.getenv('PIPELINE_POLL_COUNTDOWN_SECONDS', '15'))
# Spread scheduled starts so many accounts do not hit the provision rate at once.
PIPELINE_SCHEDULE_JITTER_SECONDS = int(
    os.getenv('PIPELINE_SCHEDULE_JITTER_SECONDS', '300')
)


def _nonblocking_orchestration_enabled() -> bool:
    return bool(PIPELINE_NONBLOCKING_ORCHESTRATION) or os.getenv(
        'PIPELINE_NONBLOCKING_ORCHESTRATION', 'true'
    ).lower() in ('1', 'true', 'yes', 'on')
FLOW_FLEET_LEASE_PREFIX = 'celery:flow-fleet:run:'


def _flow_fleet_reuse_enabled() -> bool:
    return bool(PIPELINE_FLOW_FLEET_REUSE_ENABLED)


def _flow_pod_budget_enabled_for_run(meta: Optional[dict] = None) -> bool:
    if _flow_fleet_reuse_enabled():
        return True
    if PIPELINE_FLOW_POD_BUDGET_ENABLED:
        return True
    if meta and bool(meta.get('flow_pod_budget_enabled')):
        return True
    if meta and bool(meta.get('flow_fleet_reuse_enabled')):
        return True
    return False


def _refresh_pipeline_flow_fleet_lease(run_id: int) -> None:
    if not _flow_fleet_reuse_enabled():
        return
    try:
        from django.conf import settings
        import redis

        url = getattr(settings, 'CELERY_BROKER_URL', None) or getattr(
            settings, 'REDIS_URL', 'redis://redis:6379/0'
        )
        client = redis.from_url(url)
        client.set(
            f'{FLOW_FLEET_LEASE_PREFIX}{int(run_id)}',
            '1',
            ex=FLOW_FLEET_LEASE_TTL_SECONDS,
        )
    except Exception:
        logger.exception('FLOW_FLEET_LEASE_REFRESH_FAILED | run=%s', run_id)


def _clear_pipeline_flow_fleet_lease(run_id: int) -> None:
    try:
        from django.conf import settings
        import redis

        url = getattr(settings, 'CELERY_BROKER_URL', None) or getattr(
            settings, 'REDIS_URL', 'redis://redis:6379/0'
        )
        client = redis.from_url(url)
        client.delete(f'{FLOW_FLEET_LEASE_PREFIX}{int(run_id)}')
    except Exception:
        logger.exception('FLOW_FLEET_LEASE_CLEAR_FAILED | run=%s', run_id)


def _maybe_early_teardown_flow_fleet(
    run: PipelineRun,
    exec_node_ids: list[int],
    *,
    fleet_reuse: bool,
    node_status: str,
) -> None:
    """Release a shared flow fleet after a single-node run succeeds.

    Do not teardown on failure — child table tasks may still be running on the
    flow worker while the orchestrator is in FANOUT_AWAITING_PROVISION.
    """
    if not fleet_reuse or len(exec_node_ids) != 1:
        return
    if (node_status or '').lower() != 'completed':
        return
    _teardown_pipeline_flow_fleet(run.id)
    logger.info(
        'FLOW_FLEET_EARLY_TEARDOWN | run=%s | nodes=%s',
        run.id,
        exec_node_ids,
    )


def _teardown_pipeline_flow_fleet(run_id: int) -> None:
    """Ask FastAPI control workers to delete the shared run fleet.

    Always enqueue — even when ``PIPELINE_FLOW_FLEET_REUSE_ENABLED`` is off.
    Fleets created while reuse was on (or by control workers that still have the
    flag enabled) would otherwise stay in Redis/K8s forever and falsely trip
    ``company_job_cap`` on later runs. Teardown is idempotent when no fleet exists.
    """
    _clear_pipeline_flow_fleet_lease(run_id)
    try:
        from apps.elttool.services.celery_inspect_service import get_migration_celery_app

        queue = os.getenv('CELERY_MIGRATION_CONTROL_QUEUE', 'migration-control')
        get_migration_celery_app().send_task(
            'celery_worker.teardown_pipeline_flow_fleet',
            args=[int(run_id)],
            queue=queue,
        )
        logger.info('FLOW_FLEET_TEARDOWN_ENQUEUED | run=%s | queue=%s', run_id, queue)
    except Exception:
        logger.exception('FLOW_FLEET_TEARDOWN_ENQUEUE_FAILED | run=%s', run_id)


_TERMINAL_PIPELINE_RUN_STATUSES = frozenset({
    'completed',
    'failed',
    'cancelled',
    'canceled',
})


def _drop_redis_fleet_registration(job_name: str) -> None:
    """Remove a fleet from the Redis active set immediately (K8s teardown can follow)."""
    if not job_name:
        return
    try:
        from apps.elttool.services.ops_monitoring_service import (
            REDIS_ACTIVE_SET_KEY,
            _meta_key_from_hash,
            get_ops_redis,
            job_hash,
        )

        r = get_ops_redis()
        h = job_hash(job_name)
        r.srem(REDIS_ACTIVE_SET_KEY, h)
        r.delete(_meta_key_from_hash(h))
    except Exception:
        logger.exception('FLOW_FLEET_REDIS_DROP_FAILED | job=%s', job_name)


def _reclaim_orphan_flow_fleets_for_company(company) -> int:
    """Tear down Redis/K8s fleets whose PipelineRun is already terminal.

    Run Now / schedule only count Postgres flow slots. Ghost fleets from finished
    runs still occupy the Redis ``company_job_cap``, so provision fails mid-run
    with no early warning. Clear Redis immediately, then enqueue K8s teardown.
    """
    if company is None:
        return 0
    try:
        from apps.elttool.services.ops_monitoring_service import (
            get_ops_redis,
            list_job_workers_from_redis,
        )
    except Exception:
        logger.exception(
            'FLOW_FLEET_ORPHAN_RECLAIM_IMPORT_FAILED | company=%s',
            getattr(company, 'cmp_id', None),
        )
        return 0

    try:
        workers = list_job_workers_from_redis(get_ops_redis())
    except Exception:
        logger.exception(
            'FLOW_FLEET_ORPHAN_RECLAIM_LIST_FAILED | company=%s',
            getattr(company, 'cmp_id', None),
        )
        return 0

    company_id = int(company.cmp_id)
    reclaimed = 0
    seen_run_ids: set[int] = set()
    for meta in workers:
        raw_company = meta.get('company_id')
        if raw_company is None:
            continue
        try:
            if int(raw_company) != company_id:
                continue
        except (TypeError, ValueError):
            continue

        job_name = str(meta.get('job_name') or '')
        run_id = meta.get('pipeline_run_id')
        if run_id is None:
            match = re.match(r'^pipeline_run_(\d+)', job_name)
            if match:
                run_id = match.group(1)
        if run_id is None:
            continue
        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            continue
        if run_id in seen_run_ids:
            continue
        seen_run_ids.add(run_id)

        status = (
            PipelineRun.objects.filter(id=run_id)
            .values_list('status', flat=True)
            .first()
        )
        if status is not None and status not in _TERMINAL_PIPELINE_RUN_STATUSES:
            continue

        # Free the plan cap before the async K8s teardown lands.
        fleet_key = f'pipeline_run_{run_id}'
        _drop_redis_fleet_registration(fleet_key)
        if job_name and job_name != fleet_key:
            _drop_redis_fleet_registration(job_name)
        _teardown_pipeline_flow_fleet(run_id)
        reclaimed += 1
        logger.warning(
            'FLOW_FLEET_ORPHAN_RECLAIM | run=%s | status=%s | company=%s',
            run_id,
            status,
            company_id,
        )
    return reclaimed


def _company_redis_fleet_count(company) -> int:
    """Redis fleets for this org that still belong to a non-terminal PipelineRun.

    Ghosts for finished runs are ignored here (and reclaimed separately) so they
    do not block Run Now after Postgres already shows free capacity.
    """
    if company is None:
        return 0
    try:
        from apps.elttool.services.ops_monitoring_service import (
            get_ops_redis,
            list_job_workers_from_redis,
        )

        company_id = int(company.cmp_id)
        active_run_ids: set[int] = set()
        count = 0
        for meta in list_job_workers_from_redis(get_ops_redis()):
            raw_company = meta.get('company_id')
            if raw_company is None:
                continue
            try:
                if int(raw_company) != company_id:
                    continue
            except (TypeError, ValueError):
                continue

            run_id = meta.get('pipeline_run_id')
            if run_id is None:
                match = re.match(r'^pipeline_run_(\d+)', str(meta.get('job_name') or ''))
                if match:
                    run_id = match.group(1)
            if run_id is None:
                # Unknown fleet identity — count it so we still warn early.
                count += 1
                continue
            try:
                run_id = int(run_id)
            except (TypeError, ValueError):
                count += 1
                continue
            if run_id not in active_run_ids:
                status = (
                    PipelineRun.objects.filter(id=run_id)
                    .values_list('status', flat=True)
                    .first()
                )
                if status is None or status in _TERMINAL_PIPELINE_RUN_STATUSES:
                    continue
                active_run_ids.add(run_id)
            count += 1
        return count
    except Exception:
        logger.exception(
            'COMPANY_REDIS_FLEET_COUNT_FAILED | company=%s',
            getattr(company, 'cmp_id', None),
        )
        return 0


def _get_connection_for_user(request, connection_id):
    return get_object_or_404(
        Connection.objects.filter(cmp=request.user.cmp),
        connection_id=connection_id,
    )


def _get_pipeline_for_user(request, pipeline_id):
    return get_object_or_404(
        Pipeline.objects.select_related('cmp').filter(cmp=request.user.cmp),
        id=pipeline_id,
    )


class PipelineNodeSerializer(serializers.ModelSerializer):
    batch_name = serializers.SerializerMethodField()

    class Meta:
        model = PipelineNode
        fields = [
            'id',
            'pipeline_id',
            'node_kind',
            'connection_id',
            'batch_id',
            'batch_name',
            'node_label',
            'x',
            'y',
            'order_index',
            'execution_order',
            'schedule_type',
            'time_frequency',
            'schedule_config',
            'sync_start_date',
            'notify_on_completion',
        ]

    def get_batch_name(self, obj):
        return obj.batch.name if obj.batch_id else ''


class PipelineEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineEdge
        fields = ['id', 'pipeline_id', 'from_node_id', 'to_node_id']


def _run_waiting_for_flow_slot(metadata) -> bool:
    """True when a run is blocked on the organisation plan concurrent-flow cap."""
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get('waiting_for_capacity'))


def _display_latest_run_status(
    raw_status: Optional[str],
    metadata=None,
) -> Optional[str]:
    """Normalize DB run state for pipeline list / picker badges."""
    if not raw_status:
        return None
    status_l = str(raw_status).lower()
    if status_l == 'queued':
        return 'queued'
    if status_l in ('running', 'in_progress'):
        if _run_waiting_for_flow_slot(metadata):
            return 'queued'
        return 'running'
    return raw_status


def _attach_pipeline_latest_run_statuses(pipelines) -> None:
    """Set ``latest_run_status`` on each pipeline for list serialization."""
    pipeline_list = list(pipelines)
    if not pipeline_list:
        return
    ids = [p.id for p in pipeline_list]
    active_by_pipeline: dict[int, PipelineRun] = {}
    for run in (
        PipelineRun.objects.filter(
            pipeline_id__in=ids,
            status__in=('running', 'queued'),
        )
        .order_by('pipeline_id', '-id')
        .iterator()
    ):
        if run.pipeline_id not in active_by_pipeline:
            active_by_pipeline[run.pipeline_id] = run

    fallback_by_pipeline: dict[int, PipelineRun] = {}
    missing_ids = [pid for pid in ids if pid not in active_by_pipeline]
    if missing_ids:
        for run in (
            PipelineRun.objects.filter(pipeline_id__in=missing_ids)
            .order_by('pipeline_id', '-id')
            .iterator()
        ):
            if run.pipeline_id not in fallback_by_pipeline:
                fallback_by_pipeline[run.pipeline_id] = run

    for pipeline in pipeline_list:
        run = active_by_pipeline.get(pipeline.id) or fallback_by_pipeline.get(
            pipeline.id
        )
        if not run:
            pipeline.latest_run_status = None
            continue
        pipeline.latest_run_status = _display_latest_run_status(
            run.status,
            run.metadata,
        )


class PipelineSerializer(serializers.ModelSerializer):
    nodes = PipelineNodeSerializer(many=True, read_only=True)
    edges = PipelineEdgeSerializer(many=True, read_only=True)
    root_node_ids = serializers.SerializerMethodField()
    start_node_id = serializers.SerializerMethodField()
    readable_schedule = serializers.SerializerMethodField()
    next_run_at = serializers.SerializerMethodField()
    validated_at = serializers.DateTimeField(read_only=True)
    validated_graph_fingerprint = serializers.SerializerMethodField()
    draft_node_ids = serializers.SerializerMethodField()
    canvas_changed_since_publish = serializers.SerializerMethodField()
    has_published_graph = serializers.SerializerMethodField()
    published_graph = serializers.SerializerMethodField()
    latest_run_status = serializers.SerializerMethodField()

    class Meta:
        model = Pipeline
        fields = [
            'id',
            'name',
            'status',
            'schedule_paused',
            'latest_run_status',
            'schedule_type',
            'time_frequency',
            'schedule_config',
            'sync_start_date',
            'sync_end_date',
            'notify_on_flow_finish',
            'notification_email_group_ids',
            'notification_emails',
            'created_at',
            'updated_at',
            'nodes',
            'edges',
            'root_node_ids',
            'start_node_id',
            'readable_schedule',
            'next_run_at',
            'validated_at',
            'validated_graph_fingerprint',
            'draft_node_ids',
            'canvas_changed_since_publish',
            'has_published_graph',
            'published_graph',
        ]

    def get_root_node_ids(self, obj):
        nodes = list(obj.nodes.all())
        edges = list(obj.edges.all())
        return entry_node_ids(nodes, edges)

    def get_start_node_id(self, obj):
        start = get_start_node(obj.nodes.all())
        return start.id if start else None

    def get_readable_schedule(self, obj):
        return _readable_pipeline_schedule(obj)

    def get_next_run_at(self, obj):
        if (
            obj.status == 'paused'
            or obj.schedule_paused
            or obj.schedule_type == 'manual'
        ):
            return None
        try:
            from apps.elttool.scheduler_client import SchedulerClient

            job = SchedulerClient.get_job(f'pipeline_{obj.id}')
            if not job:
                return None
            return job.get('next_run_time')
        except Exception:
            logger.debug(
                'Failed to fetch next_run_at for pipeline %s',
                obj.id,
                exc_info=True,
            )
            return None

    def get_validated_graph_fingerprint(self, obj):
        snapshot = obj.validated_graph or {}
        return snapshot.get('fingerprint')

    def get_draft_node_ids(self, obj):
        nodes = list(obj.nodes.all())
        edges = list(obj.edges.all())
        return disconnected_batch_node_ids(nodes, edges)

    def get_canvas_changed_since_publish(self, obj):
        if not obj.validated_graph:
            return False
        nodes = list(obj.nodes.all())
        edges = list(obj.edges.all())
        current = pipeline_graph_fingerprint(nodes, edges)
        published = (obj.validated_graph or {}).get('fingerprint')
        return bool(published and current != published)

    def get_has_published_graph(self, obj):
        snapshot = obj.validated_graph
        if not snapshot or not isinstance(snapshot, dict):
            return False
        nodes = snapshot.get('nodes') or []
        return bool(nodes)

    def get_published_graph(self, obj):
        return _published_graph_summary(obj)

    def get_latest_run_status(self, obj):
        annotated = getattr(obj, 'latest_run_status', None)
        if annotated is not None:
            return annotated
        run = (
            obj.runs.filter(status__in=('running', 'queued'))
            .order_by('-id')
            .first()
        )
        if run is None:
            run = obj.runs.order_by('-id').first()
        if run is None:
            return None
        return _display_latest_run_status(run.status, run.metadata)


def _published_graph_summary(pipeline: Optional[Pipeline]) -> Optional[dict]:
    """Compact published-topology payload for the Draft / Published canvas switch."""
    if pipeline is None:
        return None
    snapshot = pipeline.validated_graph
    if not snapshot or not isinstance(snapshot, dict):
        return None

    node_ids = []
    for item in snapshot.get('nodes') or []:
        if not isinstance(item, dict):
            continue
        node_id = item.get('node_id')
        if node_id is None:
            continue
        try:
            node_ids.append(int(node_id))
        except (TypeError, ValueError):
            continue

    edges = []
    for item in snapshot.get('edges') or []:
        if not isinstance(item, dict):
            continue
        try:
            edges.append({
                'from_node_id': int(item['from_node_id']),
                'to_node_id': int(item['to_node_id']),
            })
        except (KeyError, TypeError, ValueError):
            continue

    start_node_id = snapshot.get('start_node_id')
    try:
        start_node_id = int(start_node_id) if start_node_id is not None else None
    except (TypeError, ValueError):
        start_node_id = None

    return {
        'fingerprint': snapshot.get('fingerprint'),
        'validated_at': (
            pipeline.validated_at.isoformat() if pipeline.validated_at else None
        ),
        'start_node_id': start_node_id,
        'node_ids': node_ids,
        'edges': edges,
    }


def _validate_dag(nodes, edges):
    node_ids = {n.id for n in nodes}
    indegree = {nid: 0 for nid in node_ids}
    graph = defaultdict(list)

    for e in edges:
        if e.from_node_id not in node_ids or e.to_node_id not in node_ids:
            raise serializers.ValidationError("Invalid dependency edge.")
        if e.from_node_id == e.to_node_id:
            raise serializers.ValidationError("Self dependency is not allowed.")
        graph[e.from_node_id].append(e.to_node_id)
        indegree[e.to_node_id] += 1

    queue = deque([nid for nid, deg in indegree.items() if deg == 0])
    seen = 0
    topo = []
    while queue:
        cur = queue.popleft()
        topo.append(cur)
        seen += 1
        for nxt in graph[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if seen != len(node_ids):
        raise serializers.ValidationError("Dependencies contain a cycle.")
    return topo


def _pipeline_root_node_ids(nodes, edges):
    """Deprecated alias — entry nodes after Start."""
    return entry_node_ids(nodes, edges)


def _readable_pipeline_schedule(pipeline: Pipeline) -> str:
    schedule_type = pipeline.schedule_type or 'interval'
    end_suffix = ''
    if getattr(pipeline, 'sync_end_date', None):
        end_suffix = f" until {pipeline.sync_end_date.strftime('%d %b %Y')}"
    if schedule_type == 'manual':
        return 'Manual only'
    if schedule_type == 'cron':
        cfg = pipeline.schedule_config or {}
        cron_type = cfg.get('cron_type', 'weekdays')
        hour = int(cfg.get('hour', 0))
        minute = int(cfg.get('minute', 0))
        tz = cfg.get('timezone') or 'UTC'
        at = f"{hour:02d}:{minute:02d}"
        if cron_type == 'weekly':
            days = cfg.get('days_of_week')
            if isinstance(days, list) and days:
                day = ', '.join(days)
            else:
                day = cfg.get('day_of_week', 'mon')
            return f"Weekly ({day}) {at} {tz}{end_suffix}"
        if cron_type == 'monthly':
            return f"{_readable_monthly_label(cfg)} {at} {tz}{end_suffix}"
        if cron_type == 'daily':
            return f"Daily {at} {tz}{end_suffix}"
        return f"Weekdays {at} {tz}{end_suffix}"
    try:
        freq = int(pipeline.time_frequency or '15')
    except (TypeError, ValueError):
        freq = 15
    cfg = pipeline.schedule_config or {}
    interval_unit = cfg.get('interval_unit')
    interval_value = cfg.get('interval_value')
    if interval_unit == 'week' and interval_value:
        try:
            weeks = int(interval_value)
            label = '1 week' if weeks == 1 else f'Every {weeks} weeks'
            return f'{label}{end_suffix}'
        except (TypeError, ValueError):
            pass
    if freq % 1440 == 0:
        days = freq // 1440
        base = 'Daily' if days == 1 else f'Every {days} days'
        return f'{base}{end_suffix}'
    if freq % 60 == 0:
        hours = freq // 60
        base = '1 hour' if hours == 1 else f'Every {hours} hours'
        return f'{base}{end_suffix}'
    return f'Every {freq} min{end_suffix}'


PIPELINE_NOTIFICATION_KEYS = {
    'notify_on_flow_finish',
    'notification_email_group_ids',
    'notification_emails',
}


def _apply_pipeline_notification_patch(pipeline: Pipeline, data: dict) -> None:
    if 'notify_on_flow_finish' in data:
        pipeline.notify_on_flow_finish = bool(data.get('notify_on_flow_finish'))
    if 'notification_email_group_ids' in data:
        try:
            group_ids = _normalize_notification_email_group_ids(
                data.get('notification_email_group_ids'),
            )
            pipeline.notification_email_group_ids = _validate_company_email_group_ids(
                pipeline.cmp,
                group_ids,
            )
        except ValueError as exc:
            raise serializers.ValidationError({'notification_email_group_ids': str(exc)}) from exc
    if 'notification_emails' in data:
        try:
            pipeline.notification_emails = normalize_notification_emails(
                data.get('notification_emails'),
            )
        except ValueError as exc:
            raise serializers.ValidationError({'notification_emails': str(exc)}) from exc


def _parse_schedule(node: PipelineNode):
    schedule_type = node.schedule_type or 'interval'
    if schedule_type == 'cron':
        cfg = node.schedule_config or {}
        cron_type = cfg.get('cron_type', 'weekdays')
        hour = int(cfg.get('hour', 0))
        minute = int(cfg.get('minute', 0))
        timezone_name = cfg.get('timezone') or 'UTC'
        trigger_type = 'cron'
        trigger_args = {
            'hour': hour,
            'minute': minute,
            'timezone': timezone_name,
        }
        if cron_type == 'weekly':
            trigger_args['day_of_week'] = cfg.get('day_of_week', 'mon')
        elif cron_type == 'monthly':
            trigger_args['day'] = _monthly_cron_day(cfg)
        elif cron_type == 'weekdays':
            trigger_args['day_of_week'] = 'mon-fri'
        return trigger_type, trigger_args
    freq = int(node.time_frequency or '15')
    return 'interval', {'minutes': freq}


def _coerce_pipeline_datetime(value, timezone_name: str):
    """Store schedule bounds as aware datetimes in the schedule timezone."""
    if value in (None, ''):
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(_normalize_timezone_name(timezone_name))
    if isinstance(value, str):
        raw = value.strip().replace('Z', '+00:00')
        if not raw or raw.lower() in {'none', 'null'}:
            return None
        if ' ' in raw and 'T' not in raw:
            raw = raw.replace(' ', 'T', 1)
        parsed = datetime.fromisoformat(raw)
    elif isinstance(value, datetime):
        parsed = value
    else:
        return value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed


def _pipeline_timezone_name(pipeline: Pipeline) -> str:
    cfg = pipeline.schedule_config or {}
    return _normalize_timezone_name(cfg.get('timezone') or 'UTC')


def _normalize_timezone_name(timezone_name: str) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    tz = (timezone_name or 'UTC').strip() or 'UTC'
    try:
        ZoneInfo(tz)
        return tz
    except ZoneInfoNotFoundError:
        logger.warning('Invalid timezone %r, falling back to UTC', timezone_name)
        return 'UTC'


def _weekly_day_of_week(cfg: dict) -> str:
    days = cfg.get('days_of_week')
    if isinstance(days, list) and days:
        return ','.join(str(d) for d in days if d)
    return str(cfg.get('day_of_week', 'mon'))


def _monthly_cron_day(cfg: dict):
    monthly_mode = cfg.get('monthly_mode')
    monthly_day_cron = cfg.get('monthly_day_cron')
    if monthly_mode == 'relative' or isinstance(monthly_day_cron, str):
        if isinstance(monthly_day_cron, str) and monthly_day_cron.strip():
            return monthly_day_cron.strip().lower()
        week = str(cfg.get('monthly_week') or 'first').lower()
        weekday = str(cfg.get('monthly_weekday') or cfg.get('day_of_week') or 'mon').lower()
        week_map = {
            'first': '1st',
            'second': '2nd',
            'third': '3rd',
            'fourth': '4th',
            'last': 'last',
        }
        prefix = week_map.get(week, week)
        if prefix == 'last':
            return f'last {weekday}'
        return f'{prefix} {weekday}'
    day = cfg.get('day', 1)
    if isinstance(day, str):
        parsed = day.strip().lower()
        if parsed and not parsed.isdigit():
            return parsed
    return int(day or 1)


def _readable_monthly_label(cfg: dict) -> str:
    monthly_mode = cfg.get('monthly_mode')
    monthly_day_cron = cfg.get('monthly_day_cron')
    if monthly_mode == 'relative' or isinstance(monthly_day_cron, str):
        label = monthly_day_cron or _monthly_cron_day(cfg)
        return f'Monthly ({label})'
    day = int(cfg.get('day', 1))
    return f'Monthly (day {day})'


def _parse_pipeline_schedule(pipeline: Pipeline):
    schedule_type = pipeline.schedule_type or 'interval'
    if schedule_type == 'manual':
        return None, None
    if schedule_type == 'cron':
        cfg = pipeline.schedule_config or {}
        cron_type = cfg.get('cron_type', 'weekdays')
        hour = int(cfg.get('hour', 0))
        minute = int(cfg.get('minute', 0))
        timezone_name = _normalize_timezone_name(cfg.get('timezone') or 'UTC')
        args = {
            'hour': hour,
            'minute': minute,
            'timezone': timezone_name,
        }
        if cron_type == 'weekly':
            args['day_of_week'] = _weekly_day_of_week(cfg)
        elif cron_type == 'monthly':
            args['day'] = _monthly_cron_day(cfg)
        elif cron_type == 'weekdays':
            args['day_of_week'] = 'mon-fri'
        elif cron_type == 'daily':
            pass
        return 'cron', args
    cfg = pipeline.schedule_config or {}
    if cfg.get('interval_unit') == 'week':
        try:
            weeks = max(1, int(cfg.get('interval_value', 1)))
        except (TypeError, ValueError):
            weeks = 1
        return 'interval', {'weeks': weeks}
    freq = int(pipeline.time_frequency or '15')
    return 'interval', {'minutes': freq}


def _pipeline_node_meta_key(node_id: int) -> str:
    return str(node_id)


def _get_pipeline_run_metadata(run: PipelineRun) -> dict:
    return dict(run.metadata or {})


def _lock_pipeline_run_for_metadata(run: PipelineRun) -> PipelineRun:
    """Row-lock a run so concurrent orchestrator processes cannot clobber metadata."""
    return PipelineRun.objects.select_for_update().get(id=run.id)


def _update_pipeline_run_metadata(run: PipelineRun, **updates) -> None:
    with transaction.atomic():
        locked = _lock_pipeline_run_for_metadata(run)
        meta = _get_pipeline_run_metadata(locked)
        meta.update(updates)
        if 'current_node_ids' in updates:
            node_ids = updates.get('current_node_ids') or []
            meta['current_node_id'] = node_ids[0] if node_ids else None
        locked.metadata = meta
        locked.save(update_fields=['metadata'])
        run.metadata = meta


def _set_pipeline_node_metadata(run: PipelineRun, node_id: int, **fields) -> None:
    with transaction.atomic():
        locked = _lock_pipeline_run_for_metadata(run)
        meta = _get_pipeline_run_metadata(locked)
        nodes = dict(meta.get('nodes') or {})
        key = _pipeline_node_meta_key(node_id)
        entry = dict(nodes.get(key) or {})
        entry.update(fields)
        nodes[key] = entry
        meta['nodes'] = nodes
        locked.metadata = meta
        locked.save(update_fields=['metadata'])
        run.metadata = meta


class _PipelineRunStopped(Exception):
    """This flow was deleted. Stop its loads without failing any other flow."""


_ACTIVE_LOAD_STATUSES = ('in_progress', 'pending', 'started', 'running', 'queued')
_FLOW_DELETED_MESSAGE = 'Flow deleted'


def _mark_pipeline_session_deleted(session_ctx: Optional[dict]) -> None:
    """Mark one session's open load deleted. Leave other sessions alone."""
    if not session_ctx:
        return
    from apps.elttool.models import Log, MigrationRecord

    session_id = session_ctx.get('migration_session_id')
    if not session_id:
        return
    MigrationRecord.objects.filter(
        migration_session_id=session_id,
        status__in=_ACTIVE_LOAD_STATUSES,
    ).update(status='deleted')
    Log.objects.filter(session=session_id, status='I').update(
        status='D',
        message=_FLOW_DELETED_MESSAGE,
    )


def _pipeline_run_was_deleted(run: PipelineRun) -> bool:
    try:
        run.refresh_from_db(fields=['status'])
    except PipelineRun.DoesNotExist:
        return True
    return (run.status or '') == 'deleted'


def _migration_record_scope_qs(
    migration_session_id: int,
    *,
    connection_id: Optional[int] = None,
    job_name_prefix: Optional[str] = None,
):
    qs = MigrationRecord.objects.filter(migration_session_id=migration_session_id)
    if connection_id is not None:
        qs = qs.filter(connection_id=connection_id)
    if job_name_prefix:
        qs = qs.filter(job_name__startswith=job_name_prefix)
    return qs


def _resolve_migration_peek_scope(
    run: Optional[PipelineRun],
    node_id: Optional[int],
    connection_id: Optional[int],
    job_name_prefix: Optional[str],
) -> tuple[Optional[int], Optional[str]]:
    if connection_id is not None or job_name_prefix:
        if job_name_prefix is None and run is not None and node_id is not None:
            job_name_prefix = f"pipeline_run_{run.id}_node_{node_id}"
        return connection_id, job_name_prefix
    if run is None or node_id is None:
        return None, None
    entry = (_get_pipeline_run_metadata(run).get('nodes') or {}).get(str(node_id)) or {}
    resolved_connection = entry.get('connection_id')
    resolved_prefix = entry.get('pipeline_job_name') or (
        f"pipeline_run_{run.id}_node_{node_id}"
    )
    return resolved_connection, resolved_prefix


def _peek_migration_session(
    migration_session_id: int,
    *,
    connection_id: Optional[int] = None,
    job_name_prefix: Optional[str] = None,
    run: Optional[PipelineRun] = None,
    node_id: Optional[int] = None,
):
    """One non-blocking status check. ``running`` means poll again later."""
    from apps.elttool.services.migration_status_builder import build_migration_status

    connection_id, job_name_prefix = _resolve_migration_peek_scope(
        run,
        node_id,
        connection_id,
        job_name_prefix,
    )
    scoped = connection_id is not None or job_name_prefix
    record_qs = (
        _migration_record_scope_qs(
            migration_session_id,
            connection_id=connection_id,
            job_name_prefix=job_name_prefix,
        )
        if scoped
        else MigrationRecord.objects.filter(migration_session_id=migration_session_id)
    )

    data, error, _http_status = build_migration_status(
        migration_session_id,
        connection_id=connection_id,
    )
    if data and (data.get('overall_status') or '').lower() == 'completed':
        return 'completed', data

    still_running = record_qs.filter(status__in=_ACTIVE_LOAD_STATUSES).exists()
    terminal_statuses = set(record_qs.values_list('status', flat=True))
    if not still_running:
        if 'deleted' in terminal_statuses:
            return 'deleted', data
    if data:
        overall = (data.get('overall_status') or '').lower()
        if overall == 'completed':
            return overall, data
        if overall in ('failed', 'deleted') and not still_running:
            return overall, data
        if not still_running and terminal_statuses:
            if any(str(status).lower() == 'failed' for status in terminal_statuses):
                return 'failed', data
            if all(
                str(status).lower() in ('completed', 'skipped')
                for status in terminal_statuses
            ):
                return 'completed', data
        return 'running', data
    if error:
        if not record_qs.exists():
            return 'failed', data
        if not still_running:
            if record_qs.filter(status='failed').exists():
                return 'failed', data
            if record_qs.exists() and not record_qs.exclude(
                status__in=('completed', 'skipped')
            ).exists():
                return 'completed', data
    if not still_running and record_qs.exists():
        if record_qs.filter(status='failed').exists():
            return 'failed', data
        return 'completed', data
    return 'running', data


def _wait_for_migration_session(
    migration_session_id: int,
    poll_interval: Optional[int] = None,
    timeout: Optional[int] = None,
    run: Optional[PipelineRun] = None,
    *,
    connection_id: Optional[int] = None,
    job_name_prefix: Optional[str] = None,
    node_id: Optional[int] = None,
):
    interval = poll_interval or PIPELINE_NODE_POLL_INTERVAL_SECONDS
    deadline = time.monotonic() + (timeout or PIPELINE_NODE_TIMEOUT_SECONDS)
    last_data = None

    while time.monotonic() < deadline:
        if run is not None and _pipeline_run_was_deleted(run):
            return 'deleted', last_data
        status, data = _peek_migration_session(
            migration_session_id,
            connection_id=connection_id,
            job_name_prefix=job_name_prefix,
            run=run,
            node_id=node_id,
        )
        if data:
            last_data = data
        if status == 'failed':
            remaining = max(0.0, deadline - time.monotonic())
            time.sleep(min(PIPELINE_FAILED_CONFIRM_SECONDS, remaining))
            if run is not None and _pipeline_run_was_deleted(run):
                return 'deleted', last_data
            status, data = _peek_migration_session(
                migration_session_id,
                connection_id=connection_id,
                job_name_prefix=job_name_prefix,
                run=run,
                node_id=node_id,
            )
            if data:
                last_data = data
            if status == 'failed':
                return status, data or last_data
        if status != 'running':
            return status, data or last_data
        _touch_pipeline_orchestrator(run)
        time.sleep(interval)

    return 'timeout', last_data


def _migration_failure_detail(mig_data, final_status: str) -> str:
    """Use the table or job error text. Do not replace a real failure with a generic status."""
    from apps.elttool.services.migration_status_builder import (
        is_generic_table_failure_message,
    )

    tables = (mig_data or {}).get('tables') or []
    messages = []
    for table in tables:
        if str(table.get('status') or '').lower() != 'failed':
            continue
        message = str(table.get('error_message') or table.get('message') or '').strip()
        lowered = message.lower()
        if (
            not message
            or 'migration in progress' in lowered
            or is_generic_table_failure_message(message)
            or message in messages
        ):
            continue
        name = str(table.get('table_name') or '').strip()
        if name and name.lower() not in message.lower():
            message = f"{name}: {message}"
        messages.append(message)
    if messages:
        return '; '.join(messages[:3])
    for key in ('job_level_message', 'overall_warning_message'):
        message = str((mig_data or {}).get(key) or '').strip()
        if message and 'migration in progress' not in message.lower():
            return message
    return f'Migration ended with status: {final_status}'


def _sync_pipeline_run_nodes_from_migrations(run: PipelineRun) -> None:
    """Update node metadata when the migration session finished but the executor did not."""
    from apps.elttool.services.migration_status_builder import build_migration_status

    meta = _get_pipeline_run_metadata(run)
    nodes_meta = meta.get('nodes') or {}
    for key, entry in nodes_meta.items():
        node_status = entry.get('status')
        if node_status not in ('running', 'in_progress', 'failed'):
            continue
        session_id = entry.get('migration_session_id')
        if not session_id:
            continue
        data, _, _ = build_migration_status(
            session_id,
            connection_id=entry.get('connection_id'),
        )
        if not data:
            continue
        overall = (data.get('overall_status') or '').lower()
        if overall == 'completed':
            _set_pipeline_node_metadata(
                run,
                int(key),
                status='completed',
                finished_at=timezone.now().isoformat(),
                error=None,
            )
        elif node_status in ('running', 'in_progress') and overall in ('failed', 'timeout'):
            _set_pipeline_node_metadata(
                run,
                int(key),
                status='failed',
                finished_at=timezone.now().isoformat(),
                error=(
                    entry.get('error')
                    or _migration_failure_detail(data, overall)
                ),
            )


def _pipeline_run_participating_node_ids(
    meta: dict,
    pipeline: Pipeline,
    live_nodes: list,
    run_status: Optional[str] = None,
) -> list[int]:
    """
    Node IDs that belonged to this run.

    Prefer a frozen participating_node_ids list (seeded when the run starts) so
    progress totals stay correct and later canvas edits do not appear as pending
    on historical / in-progress runs.

    Finished runs must NEVER expand onto the current published/live canvas —
    batches added after the run must not show up in historic Flow / logs.
    """
    ids: set[int] = set()
    for nid in meta.get('participating_node_ids') or []:
        try:
            ids.add(int(nid))
        except (TypeError, ValueError):
            continue
    if ids:
        return sorted(ids)

    nodes_meta = meta.get('nodes') or {}
    from_meta: set[int] = set()
    for key in nodes_meta.keys():
        try:
            from_meta.add(int(key))
        except (TypeError, ValueError):
            continue
    for nid in meta.get('executed_node_ids') or []:
        try:
            from_meta.add(int(nid))
        except (TypeError, ValueError):
            continue
    for nid in meta.get('current_node_ids') or []:
        try:
            from_meta.add(int(nid))
        except (TypeError, ValueError):
            continue
    current = meta.get('current_node_id')
    if current is not None:
        try:
            from_meta.add(int(current))
        except (TypeError, ValueError):
            pass

    # Prefer nodes observed for this run. Do not expand historical runs onto a
    # newer published graph (batches added after the run would incorrectly appear).
    if from_meta:
        return sorted(from_meta)

    snap = meta.get('graph_snapshot')
    if isinstance(snap, dict):
        snap_ids: set[int] = set()
        for nid in snap.get('node_ids') or []:
            try:
                snap_ids.add(int(nid))
            except (TypeError, ValueError):
                continue
        if snap_ids:
            return sorted(snap_ids)

    status = (run_status or '').lower()
    # Completed/failed history: never invent newer canvas nodes.
    if status in ('completed', 'failed'):
        return []

    snapshot = pipeline.validated_graph if isinstance(pipeline.validated_graph, dict) else None
    published: set[int] = set()
    if snapshot:
        for item in snapshot.get('nodes') or []:
            if not isinstance(item, dict):
                continue
            nid = item.get('node_id')
            if nid is None:
                continue
            try:
                published.add(int(nid))
            except (TypeError, ValueError):
                continue

    # Run just started / no node entries yet — fall back to current published DAG.
    if meta.get('graph_source') == 'validated' and published:
        return sorted(published)

    if published:
        return sorted(published)

    return sorted(n.id for n in live_nodes if is_batch_node(n))


def _seed_pipeline_run_node_entries(nodes) -> tuple[list[int], dict]:
    """Pending metadata for every batch node in the graph about to execute."""
    participating: list[int] = []
    nodes_meta: dict = {}
    for node in batch_nodes(nodes):
        participating.append(node.id)
        table_count = 0
        batch_name = node.node_label or f'Node {node.id}'
        if node.batch_id and getattr(node, 'batch', None) is not None:
            batch_name = node.batch.name or batch_name
            try:
                table_count = node.batch.tables.count()
            except Exception:
                table_count = 0
        nodes_meta[_pipeline_node_meta_key(node.id)] = {
            'status': 'pending',
            'batch_id': node.batch_id,
            'batch_name': batch_name,
            'connection_id': node.connection_id,
            'node_label': node.node_label or batch_name,
            'execution_order': node.execution_order or (
                node.batch.execution_order if node.batch_id and getattr(node, 'batch', None) else None
            ) or 'parallel',
            'table_count': table_count,
            'migration_session_id': None,
            'started_at': None,
            'finished_at': None,
            'error': None,
        }
    return participating, nodes_meta


def _reconcile_stale_terminal_pipeline_run(run: PipelineRun) -> None:
    """Fix runs marked failed when a node's migration session later completed."""
    if run.status not in ('failed', 'completed'):
        return

    _sync_pipeline_run_nodes_from_migrations(run)
    run.refresh_from_db(fields=['status', 'metadata'])
    if run.status != 'failed':
        return

    meta = _get_pipeline_run_metadata(run)
    nodes_meta = meta.get('nodes') or {}
    live_nodes = list(PipelineNode.objects.filter(pipeline_id=run.pipeline_id))
    participating_ids = _pipeline_run_participating_node_ids(
        meta, run.pipeline, live_nodes, run_status=run.status
    )
    nodes_payload = []
    for node_id in participating_ids:
        entry = nodes_meta.get(str(node_id)) or {}
        nodes_payload.append({'status': entry.get('status') or 'pending'})

    if not nodes_payload:
        return

    overall = _rollup_pipeline_overall(nodes_payload, 'failed')
    if overall['status'] != 'completed':
        return

    terminal_meta = _mark_unstarted_pipeline_nodes_skipped({
        **meta,
        'current_node_id': None,
        'current_node_ids': [],
        'error': None,
    })
    run.status = 'completed'
    run.metadata = terminal_meta
    run.save(update_fields=['status', 'metadata'])


def _close_deferred_pipeline_failure(run: PipelineRun, meta: dict) -> None:
    """Finish a run that already failed once its table rows are terminal."""
    error = meta.get('error') or 'Pipeline run failed'
    # Force-close any leftover in_progress rows (stuck retries / dead workers).
    _fail_active_pipeline_load_rows(run, reason=str(error))
    now_iso = timezone.now().isoformat()
    nodes = {}
    for key, entry in (meta.get('nodes') or {}).items():
        entry = dict(entry or {})
        node_status = (entry.get('status') or '').lower()
        if node_status in ('', 'running', 'in_progress', 'pending', 'queued'):
            entry['status'] = 'skipped'
            entry['finished_at'] = entry.get('finished_at') or now_iso
            entry['error'] = (
                entry.get('error')
                or 'Not started — pipeline stopped due to an upstream failure.'
            )
        nodes[key] = entry
    terminal_meta = _mark_unstarted_pipeline_nodes_skipped({
        **meta,
        'nodes': nodes,
        'failure_pending': False,
        'error': _pipeline_run_failure_message({**meta, 'nodes': nodes}, error),
        'current_node_id': None,
        'current_node_ids': [],
    })
    run.status = 'failed'
    run.finished_at = timezone.now()
    run.metadata = terminal_meta
    run.save(update_fields=['status', 'finished_at', 'metadata'])
    try:
        send_pipeline_flow_notification(run)
    except Exception:
        logger.exception(
            'Pipeline flow notification hook failed for deferred failure run %s',
            run.id,
        )
    _teardown_pipeline_flow_fleet(run.id)


def _reconcile_pipeline_run_status(run: PipelineRun) -> None:
    """Finalize runs left as running after worker interruption or uncaught node failure."""
    if run.status != 'running':
        return

    _sync_pipeline_run_nodes_from_migrations(run)
    run.refresh_from_db(fields=['status', 'metadata', 'finished_at'])
    if run.status != 'running':
        return

    meta = _get_pipeline_run_metadata(run)
    if meta.get('failure_pending'):
        if _pipeline_run_has_active_migrations(run):
            return
        _close_deferred_pipeline_failure(run, meta)
        return

    nodes_meta = meta.get('nodes') or {}
    live_nodes = list(PipelineNode.objects.filter(pipeline_id=run.pipeline_id))
    participating_ids = _pipeline_run_participating_node_ids(
        meta, run.pipeline, live_nodes, run_status=run.status
    )
    nodes_payload = []
    first_error = None
    for node_id in participating_ids:
        entry = nodes_meta.get(str(node_id)) or {}
        node_status = entry.get('status') or 'pending'
        if node_status == 'failed' and not first_error:
            first_error = entry.get('error')
        nodes_payload.append({'status': node_status})

    if not nodes_payload:
        return

    overall = _rollup_pipeline_overall(nodes_payload, 'running')
    if overall['nodes_running'] > 0:
        return

    terminal_meta = _mark_unstarted_pipeline_nodes_skipped({
        **meta,
        'current_node_id': None,
        'current_node_ids': [],
    })
    run.finished_at = timezone.now()

    if overall['status'] == 'failed':
        run.status = 'failed'
        terminal_meta['error'] = _pipeline_run_failure_message(
            terminal_meta,
            first_error or 'Pipeline run failed',
        )
        run.metadata = terminal_meta
        run.save(update_fields=['status', 'finished_at', 'metadata'])
        try:
            send_pipeline_flow_notification(run)
        except Exception:
            logger.exception(
                'Pipeline flow notification hook failed for reconciled run %s',
                run.id,
            )
        _teardown_pipeline_flow_fleet(run.id)
        return

    if overall['status'] == 'completed':
        run.status = 'completed'
        run.metadata = terminal_meta
        run.save(update_fields=['status', 'finished_at', 'metadata'])
        try:
            send_pipeline_flow_notification(run)
        except Exception:
            logger.exception(
                'Pipeline flow notification hook failed for reconciled run %s',
                run.id,
            )
        _teardown_pipeline_flow_fleet(run.id)


def _build_pipeline_run_summary(
    run: PipelineRun,
    *,
    reconcile_running: bool = False,
) -> dict:
    """Lightweight run row for list API — metadata only, no full graph rebuild."""
    if reconcile_running and run.status == 'running':
        _reconcile_pipeline_run_status(run)
        run.refresh_from_db(fields=['status', 'metadata', 'finished_at', 'started_at'])

    meta = _get_pipeline_run_metadata(run)
    nodes_meta = meta.get('nodes') or {}
    nodes_payload = [
        {
            'status': entry.get('status') or 'pending',
            'table_count': int(entry.get('table_count') or 0),
        }
        for entry in nodes_meta.values()
    ]
    overall = _rollup_pipeline_overall(nodes_payload, run.status)
    if run.status == 'queued' or (
        run.status == 'running' and _run_waiting_for_flow_slot(meta)
    ):
        status = 'queued'
    elif run.status == 'running':
        status = overall['status']
    else:
        status = run.status

    return {
        'pipeline_run_id': run.id,
        'run_mode': _pipeline_run_mode(meta),
        'status': status,
        'waiting_for_flow_slot': run.status == 'queued'
        or _run_waiting_for_flow_slot(meta),
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'overall': overall,
        'error': meta.get('error'),
    }


def _build_pipeline_run_detail(run: PipelineRun, include_migration_status: bool = True) -> dict:
    if run.status == 'running':
        _reconcile_pipeline_run_status(run)
        run.refresh_from_db()
    elif run.status == 'failed':
        _reconcile_stale_terminal_pipeline_run(run)
        run.refresh_from_db()
    from apps.elttool.services.migration_status_builder import build_migration_status

    pipeline = run.pipeline
    nodes = list(
        PipelineNode.objects.filter(pipeline=pipeline)
        .select_related('batch', 'connection')
        .order_by('order_index')
    )
    edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
    try:
        topo = _validate_dag(nodes, edges)
    except serializers.ValidationError:
        topo = [n.id for n in nodes]

    meta = _get_pipeline_run_metadata(run)
    nodes_meta = meta.get('nodes') or {}
    current_node_ids = meta.get('current_node_ids') or []
    current_node_id = meta.get('current_node_id')
    if current_node_id is None and current_node_ids:
        current_node_id = current_node_ids[0]

    participating_ids = set(
        _pipeline_run_participating_node_ids(
            meta, pipeline, nodes, run_status=run.status
        )
    )
    nodes_payload = []
    node_map = {n.id: n for n in nodes}

    # Preserve DAG order for live nodes; append any deleted-but-recorded nodes after.
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for node_id in topo:
        if node_id in participating_ids and node_id not in seen:
            ordered_ids.append(node_id)
            seen.add(node_id)
    for node_id in sorted(participating_ids):
        if node_id not in seen:
            ordered_ids.append(node_id)
            seen.add(node_id)

    for node_id in ordered_ids:
        node = node_map.get(node_id)
        if node and is_start_node(node):
            continue
        key = _pipeline_node_meta_key(node_id)
        entry = nodes_meta.get(key) or {}
        node_status = entry.get('status') or 'pending'
        migration_session_id = entry.get('migration_session_id')
        migration_status = None

        node_connection_id = (
            node.connection_id if node else entry.get('connection_id')
        )
        if include_migration_status and migration_session_id:
            data, error, _ = build_migration_status(
                migration_session_id,
                connection_id=node_connection_id,
            )
            if data:
                migration_status = data
                mig_overall = (data.get('overall_status') or '').lower()
                if node_status in ('running', 'pending', 'in_progress', 'failed') and mig_overall:
                    node_status = mig_overall
            elif error and node_status in ('running', 'in_progress', 'pending'):
                node_status = 'failed'

        # Older runs may have stored "skipped" for nodes that never started.
        # Keep showing their prior state (pending) instead of a synthetic skipped badge.
        if node_status == 'skipped':
            node_status = 'pending'
            err = entry.get('error') or ''
            if isinstance(err, str) and err.startswith('Skipped'):
                entry = {**entry, 'error': None}

        table_count = int(entry.get('table_count') or 0)
        if node and node.batch_id:
            table_count = node.batch.tables.count()

        nodes_payload.append({
            'node_id': node_id,
            'batch_id': (node.batch_id if node else entry.get('batch_id')),
            'batch_name': (
                node.batch.name if node and node.batch_id
                else entry.get('batch_name', '')
            ),
            'connection_id': (node.connection_id if node else entry.get('connection_id')),
            'node_label': (node.node_label if node else entry.get('node_label', '')),
            'execution_order': (
                (node.execution_order if node else None)
                or (node.batch.execution_order if node and node.batch_id else None)
                or entry.get('execution_order')
                or 'parallel'
            ),
            'status': node_status,
            'migration_session_id': migration_session_id,
            'started_at': entry.get('started_at'),
            'finished_at': entry.get('finished_at'),
            'error': entry.get('error'),
            'skipped_tables': entry.get('skipped_tables') or [],
            'table_count': table_count,
            'migration_status': migration_status,
        })

    overall = _rollup_pipeline_overall(nodes_payload, run.status)

    start = get_start_node(nodes)
    start_id = start.id if start else None

    # Rebuild sparse/missing historic topology and persist it so Flow layout
    # keeps exact edges on later views.
    meta, run_edges, backfilled = _backfill_run_graph_snapshot(
        run,
        meta,
        pipeline,
        participating_ids,
        edges,
        start_node_id=start_id,
        live_nodes=nodes,
    )
    edges_frozen = bool(
        meta.get('edge_pairs')
        or (
            isinstance(meta.get('graph_snapshot'), dict)
            and (meta.get('graph_snapshot') or {}).get('edges')
        )
    )
    if not run_edges:
        run_edges, edges_frozen = _pipeline_run_edges_for_detail(
            meta,
            pipeline,
            participating_ids,
            edges,
            start_node_id=start_id,
            live_nodes=nodes,
        )

    # Historic Flow must match the connected DAG from Start. Drop participating
    # orphans that are not reachable via frozen/backfilled edges (often later
    # draft batches incorrectly listed on old runs).
    if run_edges:
        reachable = _reachable_node_ids_from_start(start_id, run_edges)
        before = len(nodes_payload)
        nodes_payload = [
            n for n in nodes_payload if int(n['node_id']) in reachable
        ]
        if len(nodes_payload) != before:
            overall = _rollup_pipeline_overall(nodes_payload, run.status)

    if run.status == 'queued' or (
        run.status == 'running' and _run_waiting_for_flow_slot(meta)
    ):
        run_status = 'queued'
    elif run.status == 'running':
        run_status = overall['status']
    else:
        run_status = run.status

    return {
        'pipeline_run_id': run.id,
        'pipeline_id': pipeline.id,
        'run_mode': _pipeline_run_mode(meta),
        'status': run_status,
        'waiting_for_flow_slot': run.status == 'queued'
        or _run_waiting_for_flow_slot(meta),
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'current_node_id': current_node_id,
        'current_node_ids': current_node_ids,
        'overall': overall,
        'nodes': nodes_payload,
        'edges': run_edges,
        'edges_frozen': edges_frozen,
        'edges_backfilled': bool(meta.get('edges_backfilled') or backfilled),
        'start_node_id': start_id,
        'error': meta.get('error'),
    }


def _pipeline_run_mode(meta: dict) -> str:
    """Published runs execute the validated graph; anything else is draft."""
    source = (meta.get('graph_source') or '').lower()
    return 'published' if source == 'validated' else 'draft'


def _reachable_node_ids_from_start(
    start_node_id: Optional[int],
    edges: list[dict],
) -> set[int]:
    """Node IDs reachable from Start (inclusive) via directed run edges."""
    adj: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        try:
            adj[int(e['from_node_id'])].append(int(e['to_node_id']))
        except (KeyError, TypeError, ValueError):
            continue
    out: set[int] = set()
    if start_node_id is None:
        for e in edges:
            try:
                out.add(int(e['from_node_id']))
                out.add(int(e['to_node_id']))
            except (KeyError, TypeError, ValueError):
                continue
        return out
    start = int(start_node_id)
    queue = [start]
    seen = {start}
    out.add(start)
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            out.add(nxt)
            queue.append(nxt)
    return out


def _normalize_run_edge_pairs(raw) -> list[dict]:
    """Normalize frozen edge_pairs / edges from run metadata."""
    pairs: list[dict] = []
    if not raw:
        return pairs
    for item in raw:
        try:
            if isinstance(item, dict):
                frm = int(item['from_node_id'])
                to = int(item['to_node_id'])
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                frm = int(item[0])
                to = int(item[1])
            else:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        pairs.append({'from_node_id': frm, 'to_node_id': to})
    return pairs


def _filter_edges_to_allowed(
    pairs: list[dict],
    allowed: set[int],
) -> list[dict]:
    return [
        e for e in pairs
        if e['from_node_id'] in allowed and e['to_node_id'] in allowed
    ]


def _score_run_edges(pairs: list[dict], participating_ids: set[int], start_node_id: Optional[int]) -> tuple[int, int]:
    """Higher edge count and fewer orphan batches is better. Returns (edges, -orphans)."""
    if not pairs:
        return (0, 0)
    adj: dict[int, list[int]] = defaultdict(list)
    for e in pairs:
        adj[e['from_node_id']].append(e['to_node_id'])
    start = start_node_id
    reachable: set[int] = set()
    if start is not None:
        queue = [int(start)]
        seen = {int(start)}
        while queue:
            cur = queue.pop(0)
            for nxt in adj.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    if nxt in participating_ids:
                        reachable.add(nxt)
                    queue.append(nxt)
    orphans = len(participating_ids - reachable)
    return (len(pairs), -orphans)


def _infer_legacy_run_edges(
    meta: dict,
    pipeline: Pipeline,
    participating_ids: set[int],
    live_edges: list,
    start_node_id: Optional[int] = None,
    live_nodes: Optional[list] = None,
) -> list[dict]:
    """Best-effort topology for runs that never froze a full canvas snapshot.

    When canvas nodes were deleted/re-added, node IDs change but batch_ids stay.
    Remap current published/live edges onto this run's participating node IDs via
    batch_id so historic Flow layout keeps real connections.
    """
    allowed = set(participating_ids)
    if start_node_id is not None:
        allowed.add(int(start_node_id))

    live_nodes = list(live_nodes or [])
    nodes_meta = meta.get('nodes') or {}

    run_node_to_batch: dict[int, int] = {}
    for key, entry in nodes_meta.items():
        if not isinstance(entry, dict):
            continue
        try:
            nid = int(key)
        except (TypeError, ValueError):
            continue
        bid = entry.get('batch_id')
        if bid is None:
            continue
        try:
            run_node_to_batch[nid] = int(bid)
        except (TypeError, ValueError):
            continue

    live_batch_to_node: dict[int, int] = {}
    live_name_to_node: dict[str, int] = {}
    live_start_id = None
    for node in live_nodes:
        if is_start_node(node):
            live_start_id = node.id
            continue
        if not is_batch_node(node):
            continue
        if node.batch_id is not None:
            try:
                live_batch_to_node[int(node.batch_id)] = int(node.id)
            except (TypeError, ValueError):
                continue
            # Identity mapping when the run node is still on the canvas.
            run_node_to_batch.setdefault(int(node.id), int(node.batch_id))
        batch_obj = getattr(node, 'batch', None)
        batch_name = (getattr(batch_obj, 'name', None) or getattr(node, 'node_label', None) or '')
        if isinstance(batch_name, str) and batch_name.strip():
            live_name_to_node[batch_name.strip().lower()] = int(node.id)

    # Fill missing run batch_ids from names stored on the run.
    for key, entry in nodes_meta.items():
        if not isinstance(entry, dict):
            continue
        try:
            nid = int(key)
        except (TypeError, ValueError):
            continue
        if nid in run_node_to_batch:
            continue
        name = entry.get('batch_name') or entry.get('node_label') or ''
        if not isinstance(name, str) or not name.strip():
            continue
        live_nid = live_name_to_node.get(name.strip().lower())
        if live_nid is None:
            continue
        # Reverse: find batch_id for that live node
        for bid, lid in live_batch_to_node.items():
            if lid == live_nid:
                run_node_to_batch[nid] = bid
                break

    # live/published node id -> this run's participating node id
    source_to_run: dict[int, int] = {}
    if start_node_id is not None and live_start_id is not None:
        source_to_run[int(live_start_id)] = int(start_node_id)
        source_to_run[int(start_node_id)] = int(start_node_id)

    for run_nid in participating_ids:
        source_to_run[int(run_nid)] = int(run_nid)
        bid = run_node_to_batch.get(int(run_nid))
        if bid is not None:
            live_nid = live_batch_to_node.get(bid)
            if live_nid is not None:
                source_to_run[int(live_nid)] = int(run_nid)
        entry = nodes_meta.get(str(run_nid)) or nodes_meta.get(run_nid) or {}
        if isinstance(entry, dict):
            name = entry.get('batch_name') or entry.get('node_label') or ''
            if isinstance(name, str) and name.strip():
                live_nid = live_name_to_node.get(name.strip().lower())
                if live_nid is not None:
                    source_to_run[int(live_nid)] = int(run_nid)

    def _project(pairs: list[dict]) -> list[dict]:
        projected: list[dict] = []
        seen: set[tuple[int, int]] = set()
        for e in pairs:
            frm = source_to_run.get(e['from_node_id'], e['from_node_id'])
            to = source_to_run.get(e['to_node_id'], e['to_node_id'])
            if frm not in allowed or to not in allowed:
                continue
            key = (frm, to)
            if key in seen:
                continue
            seen.add(key)
            projected.append({'from_node_id': frm, 'to_node_id': to})
        return projected

    candidates: list[list[dict]] = []
    snapshot = pipeline.validated_graph if isinstance(pipeline.validated_graph, dict) else None
    if snapshot:
        published = _project(
            _normalize_run_edge_pairs(snapshot.get('edges') or [])
        )
        if published:
            candidates.append(published)

    live_pairs = []
    for e in live_edges:
        try:
            live_pairs.append({
                'from_node_id': int(e.from_node_id),
                'to_node_id': int(e.to_node_id),
            })
        except (TypeError, ValueError, AttributeError):
            continue
    live_projected = _project(live_pairs)
    if live_projected:
        candidates.append(live_projected)

    if not candidates:
        return []
    return max(
        candidates,
        key=lambda pairs: _score_run_edges(pairs, participating_ids, start_node_id),
    )


def _frozen_run_graph_is_sparse(
    pairs: list[dict],
    participating_ids: set[int],
    start_node_id: Optional[int],
) -> bool:
    """True when frozen edges leave most participating batches unreachable from Start."""
    if not pairs:
        return True
    if not participating_ids:
        return False
    _edge_count, neg_orphans = _score_run_edges(
        pairs, participating_ids, start_node_id
    )
    orphans = -neg_orphans
    # Screenshot failure mode: only Start→roots linked, rest stacked as orphans.
    return orphans > max(1, len(participating_ids) // 2)


def _backfill_run_graph_snapshot(
    run: PipelineRun,
    meta: dict,
    pipeline: Pipeline,
    participating_ids: set[int],
    live_edges: list,
    start_node_id: Optional[int] = None,
    live_nodes: Optional[list] = None,
    *,
    force: bool = False,
) -> tuple[dict, list[dict], bool]:
    """
    Persist a full canvas edge snapshot onto run.metadata when missing/sparse.

    Returns (updated_meta, edges, wrote).
    """
    allowed = set(participating_ids)
    if start_node_id is not None:
        allowed.add(int(start_node_id))

    existing_raw = meta.get('edge_pairs')
    if not existing_raw and isinstance(meta.get('graph_snapshot'), dict):
        existing_raw = (meta.get('graph_snapshot') or {}).get('edges')
    existing = _filter_edges_to_allowed(
        _normalize_run_edge_pairs(existing_raw),
        allowed,
    )

    needs = force or _frozen_run_graph_is_sparse(
        existing, participating_ids, start_node_id
    )
    if not needs and existing:
        return meta, existing, False

    inferred = _infer_legacy_run_edges(
        meta,
        pipeline,
        participating_ids,
        live_edges,
        start_node_id,
        live_nodes=live_nodes,
    )
    if not inferred:
        return meta, existing, False

    # Only replace when inferred is strictly better (more edges / fewer orphans).
    if existing and not force:
        if _score_run_edges(inferred, participating_ids, start_node_id) <= _score_run_edges(
            existing, participating_ids, start_node_id
        ):
            return meta, existing, False

    graph_snapshot = {
        'start_node_id': start_node_id,
        'node_ids': sorted(int(n) for n in participating_ids),
        'edges': inferred,
    }
    new_meta = {
        **meta,
        'edge_pairs': inferred,
        'graph_snapshot': graph_snapshot,
        'edges_backfilled': True,
    }
    run.metadata = new_meta
    run.save(update_fields=['metadata'])
    return new_meta, inferred, True


def _pipeline_run_edges_for_detail(
    meta: dict,
    pipeline: Pipeline,
    participating_ids: set[int],
    live_edges: list,
    start_node_id: Optional[int] = None,
    live_nodes: Optional[list] = None,
) -> tuple[list[dict], bool]:
    """
    Topology for the run canvas / history view.

    Returns (edges, edges_frozen).
    Frozen edge_pairs from run start always win when they form a connected graph.
    Otherwise infer + (caller may backfill) from published/live draft.
    """
    allowed = set(participating_ids)
    if start_node_id is not None:
        allowed.add(int(start_node_id))

    frozen_raw = meta.get('edge_pairs')
    if not frozen_raw:
        snap = meta.get('graph_snapshot')
        if isinstance(snap, dict):
            frozen_raw = snap.get('edges')
    frozen = _filter_edges_to_allowed(
        _normalize_run_edge_pairs(frozen_raw),
        allowed,
    )
    if (
        meta.get('edge_pairs')
        or (
            isinstance(meta.get('graph_snapshot'), dict)
            and (meta.get('graph_snapshot') or {}).get('edges')
        )
    ) and not _frozen_run_graph_is_sparse(
        frozen, participating_ids, start_node_id
    ):
        return frozen, True

    inferred = _infer_legacy_run_edges(
        meta,
        pipeline,
        participating_ids,
        live_edges,
        start_node_id,
        live_nodes=live_nodes,
    )
    return inferred, False


def _record_pipeline_skipped_tables(
    connection,
    migration_session_id: int,
    pipeline_job_name: str,
    skipped_table_names: list[str],
    user,
    *,
    reason: Optional[str] = None,
) -> None:
    """Create terminal migration records + logs for tables skipped during a pipeline run."""
    from apps.elttool.models import Log
    from apps.elttool.services.table_operation_guard import PIPELINE_TABLE_SKIP_REASON

    if not skipped_table_names:
        return

    skip_reason = reason or PIPELINE_TABLE_SKIP_REASON
    now = timezone.now()
    for table_name in skipped_table_names:
        record = MigrationRecord.objects.create(
            connection=connection,
            migration_session_id=migration_session_id,
            table_name=table_name,
            job_name=pipeline_job_name,
            status='skipped',
            start_time=now,
        )
        Log.objects.create(
            message=skip_reason,
            job_name=pipeline_job_name,
            migration_record=record,
            cmp=connection.cmp,
            user=user,
            connection_id=connection.connection_id,
            status='P',
            session=migration_session_id,
        )


def _build_pipeline_node_execution_context(node) -> dict[str, Any]:
    """Build migration configs for a pipeline node (no session allocation)."""
    from apps.elttool.models import TableMapping
    from apps.elttool.services.table_operation_guard import filter_tables_for_migration
    from apps.elttool.v1.views import _is_file_based_destination, ensure_config_is_dict

    batch = node.batch
    connection = node.connection
    batch_table_names = list(batch.tables.values_list('table_name', flat=True))
    if not batch_table_names:
        raise RuntimeError(f"Batch '{batch.name}' has no tables.")

    # Reverse ETL (Snowflake → Salesforce/etc.) needs [source, destination] pairs.
    # Batch rows only store source table names; resolve destinations from table_mapping_retl.
    # File-based RETL destinations keep plain source names (no TableMapping pairs).
    dest_platform = getattr(getattr(connection.dst_config, 'dst', None), 'name', None)
    src_model = getattr(getattr(connection.src_config, 'src', None), 'is_rtl', False)
    is_reverse_etl = bool(connection.src_config and src_model)
    if is_reverse_etl and not _is_file_based_destination(dest_platform):
        batch_name_lower = {str(name).lower(): str(name) for name in batch_table_names}
        selected_tables = []
        mapped_sources_lower = set()
        mappings = (
            TableMapping.objects.select_related('source_table', 'destination_table')
            .filter(connection_id=connection.connection_id)
        )
        for mapping in mappings:
            source_name = mapping.source_table.table
            dest_name = mapping.destination_table.table
            key = str(source_name).lower()
            if key in batch_name_lower:
                selected_tables.append([str(source_name), str(dest_name)])
                mapped_sources_lower.add(key)
        missing = [
            batch_name_lower[k]
            for k in batch_name_lower
            if k not in mapped_sources_lower
        ]
        if missing:
            raise RuntimeError(
                f"Batch '{batch.name}' has tables without a source→destination mapping: "
                f"{', '.join(missing)}. Map them on the Schema tab first."
            )
        if not selected_tables:
            raise RuntimeError(
                f"Batch '{batch.name}' has no reverse-ETL mappings to run."
            )
    else:
        selected_tables = batch_table_names

    selected_tables, skipped_tables = filter_tables_for_migration(
        connection,
        selected_tables,
        is_reverse_etl=is_reverse_etl,
    )

    execution_order = node.execution_order or batch.execution_order or 'parallel'
    src_cfg = connection.src_config
    dst_cfg = connection.dst_config

    source_config = (
        ensure_config_is_dict(src_cfg.data, 'source_config')
        if src_cfg and src_cfg.data
        else {}
    )
    destination_config = (
        ensure_config_is_dict(dst_cfg.data, 'destination_config')
        if dst_cfg and dst_cfg.data
        else {}
    )
    dst_schema = str(connection.dst_schema or '').strip()
    if dst_schema:
        destination_config = dict(destination_config)
        destination_config['schema'] = dst_schema

    return {
        'selected_tables': selected_tables,
        'skipped_tables': skipped_tables,
        'batch_table_count': len(batch_table_names),
        'source_config': source_config,
        'destination_config': destination_config,
        'execution_order': execution_order,
        'connection': connection,
        'batch': batch,
        'src_cfg': src_cfg,
    }


def _start_pipeline_node_session(run: PipelineRun, node, user) -> dict[str, Any]:
    """Pre-allocate a migration session for a pipeline node (call sequentially per DAG level)."""
    from apps.elttool.v1.views import start_migration_session

    ctx = _build_pipeline_node_execution_context(node)
    execution_order = ctx['execution_order']
    if str(execution_order).lower() == 'sequential':
        logger.warning(
            "Pipeline node %s uses sequential execution_order; per-job fan-out scaling is disabled.",
            node.id,
        )

    connection = ctx['connection']
    selected_tables = ctx['selected_tables']
    skipped_tables = ctx.get('skipped_tables') or []
    batch_table_count = int(ctx.get('batch_table_count') or 0)
    pipeline_job_name = f"pipeline_run_{run.id}_node_{node.id}"

    if not selected_tables and not skipped_tables:
        raise RuntimeError(f"Batch '{ctx['batch'].name}' has no tables.")

    _combined_job_name, migration_session_id, log_id = start_migration_session(
        connection,
        'manual',
        selected_tables,
        connection.connection_id,
        user,
        None,
        create_migration_records=False,
        create_log=bool(selected_tables),
    )

    if skipped_tables:
        _record_pipeline_skipped_tables(
            connection,
            migration_session_id,
            pipeline_job_name,
            skipped_tables,
            user,
        )

    return {
        **ctx,
        'migration_session_id': migration_session_id,
        'log_id': log_id,
        'pipeline_job_name': pipeline_job_name,
        'batch_table_count': batch_table_count,
    }


def _pipeline_node_job_name(run: PipelineRun, node, node_meta: dict[str, Any]) -> str:
    return (
        node_meta.get('pipeline_job_name')
        or f"pipeline_run_{run.id}_node_{node.id}"
    )


def _celery_task_ids_queued(task_ids: set[str]) -> set[str]:
    """Return task ids still present on known Celery broker queues."""
    if not task_ids:
        return set()
    import json

    import redis
    from django.conf import settings

    try:
        broker = redis.from_url(settings.CELERY_BROKER_URL or settings.REDIS_URL)
    except Exception as exc:
        logger.warning('PIPELINE_QUEUE_SCAN_FAILED | err=%s', exc)
        return set()

    found: set[str] = set()
    for queue_name in (
        'migration-control',
        'migration',
        'pipeline',
        'celery',
        'default',
    ):
        try:
            items = broker.lrange(queue_name, 0, -1)
        except Exception:
            continue
        for raw in items:
            try:
                msg = json.loads(raw)
                headers = msg.get('headers') or {}
                task_id = headers.get('id')
                if task_id in task_ids:
                    found.add(task_id)
            except Exception:
                continue
    return found


def _migration_celery_task_state(task_id: Optional[str]) -> str:
    if not task_id:
        return 'MISSING'
    from apps.elttool.services.celery_inspect_service import get_migration_celery_app

    try:
        return str(get_migration_celery_app().AsyncResult(task_id).state or 'PENDING')
    except Exception as exc:
        logger.warning('PIPELINE_TASK_STATE_FAILED | task_id=%s | err=%s', task_id, exc)
        return 'UNKNOWN'


def _pipeline_node_session_is_ghost(
    run: PipelineRun,
    node,
    migration_session_id: int,
    pipeline_job_name: str,
    node_meta: dict[str, Any],
) -> bool:
    """
    True when a resumed node has in_progress DB rows but the Celery handoff is dead
    (typical after celery-worker-django restart / node scale-down).
    """
    node_status = (node_meta.get('status') or '').lower()
    if node_status not in ('running', 'in_progress'):
        return False

    in_progress_qs = MigrationRecord.objects.filter(
        migration_session_id=migration_session_id,
        job_name__startswith=pipeline_job_name,
        status='in_progress',
    )
    if not in_progress_qs.exists():
        return False

    cutoff = timezone.now() - timezone.timedelta(minutes=PIPELINE_GHOST_SESSION_MINUTES)
    rows = list(
        in_progress_qs.values(
            'id',
            'task_id',
            'table_name',
            'updated_at',
            'staging_records_count',
        )
    )
    if any(int(row.get('staging_records_count') or 0) > 0 for row in rows):
        return False
    if any(row.get('updated_at') and row['updated_at'] >= cutoff for row in rows):
        return False

    task_ids = {str(row['task_id']) for row in rows if row.get('task_id')}
    if task_ids & _celery_task_ids_queued(task_ids):
        return False

    from apps.elttool.services.celery_inspect_service import tasks_for_job_name

    live = tasks_for_job_name(pipeline_job_name, timeout=1.5)
    if live:
        return False

    live_states = {_migration_celery_task_state(tid) for tid in task_ids}
    if live_states - {'PENDING', 'MISSING', 'UNKNOWN'}:
        return False

    logger.warning(
        'PIPELINE_GHOST_SESSION | run=%s | node=%s | session=%s | job=%s | tasks=%s',
        run.id,
        node.id,
        migration_session_id,
        pipeline_job_name,
        sorted(task_ids),
    )
    return True


def _reconcile_ghost_pipeline_node_session(
    run: PipelineRun,
    node,
    migration_session_id: int,
    pipeline_job_name: str,
) -> None:
    """Fail stale in_progress rows for a lost pipeline node session."""
    from apps.elttool.models import Log, SourceTableSelection

    in_progress_qs = MigrationRecord.objects.filter(
        migration_session_id=migration_session_id,
        job_name__startswith=pipeline_job_name,
        status='in_progress',
    )
    rows = list(
        in_progress_qs.values('connection_id', 'table_name')
    )
    if not rows:
        return

    connection_id = rows[0]['connection_id']
    table_names = sorted(
        {str(row['table_name']) for row in rows if row.get('table_name')}
    )
    now = timezone.now()
    fail_message = (
        'Migration failed: pipeline orchestrator restarted while this batch was '
        'in progress; session reconciled and will be re-dispatched.'
    )

    with transaction.atomic():
        updated_mr = in_progress_qs.update(status='failed', updated_at=now)
        updated_sts = 0
        if connection_id and table_names:
            updated_sts = SourceTableSelection.objects.filter(
                connection_id=connection_id,
                table__in=table_names,
                status='in_progress',
            ).update(status='failed')
        updated_logs = Log.objects.filter(
            session=migration_session_id,
            status='I',
        ).update(status='E', message=fail_message)

    logger.warning(
        'PIPELINE_GHOST_SESSION_RECONCILED | run=%s | node=%s | session=%s | '
        'mr_failed=%s | sts_failed=%s | logs_closed=%s',
        run.id,
        node.id,
        migration_session_id,
        updated_mr,
        updated_sts,
        updated_logs,
    )


def _resume_pipeline_node_session(
    run: PipelineRun,
    node,
    user,
    node_meta: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Return session context and whether task dispatch should be skipped."""
    existing_session_id = node_meta.get('migration_session_id')
    if existing_session_id:
        pipeline_job_name = _pipeline_node_job_name(run, node, node_meta)
        if _pipeline_node_session_is_ghost(
            run,
            node,
            int(existing_session_id),
            pipeline_job_name,
            node_meta,
        ):
            _reconcile_ghost_pipeline_node_session(
                run,
                node,
                int(existing_session_id),
                pipeline_job_name,
            )
            return _start_pipeline_node_session(run, node, user), False

        ctx = _build_pipeline_node_execution_context(node)
        return (
            {
                **ctx,
                'migration_session_id': existing_session_id,
                'log_id': node_meta.get('log_id'),
                'pipeline_job_name': pipeline_job_name,
            },
            True,
        )
    return _start_pipeline_node_session(run, node, user), False


def _apply_pipeline_node_outcome(
    run: PipelineRun,
    node,
    final_status: str,
    mig_data,
) -> None:
    """Record a terminal migration session and raise when the node failed."""
    node_id = node.id
    if final_status == 'deleted' or _pipeline_run_was_deleted(run):
        try:
            _set_pipeline_node_metadata(
                run,
                node_id,
                status='deleted',
                finished_at=timezone.now().isoformat(),
                error=_FLOW_DELETED_MESSAGE,
            )
        except PipelineRun.DoesNotExist:
            pass
        raise _PipelineRunStopped()
    node_terminal_status = 'completed' if final_status == 'completed' else 'failed'
    node_error = None
    if node_terminal_status != 'completed':
        node_error = _migration_failure_detail(mig_data, final_status)
    _set_pipeline_node_metadata(
        run,
        node_id,
        status=node_terminal_status,
        finished_at=timezone.now().isoformat(),
        error=node_error,
    )
    if node_terminal_status != 'completed':
        # Do not leave tables as in_progress after the node is failed — retries
        # are over (or were non-retryable). Execution logs should show failed +
        # the last error only.
        node_meta = (
            (_get_pipeline_run_metadata(run).get('nodes') or {}).get(str(node_id))
            or {}
        )
        sid = node_meta.get('migration_session_id')
        _fail_active_pipeline_load_rows(
            run,
            reason=node_error
            or f'Pipeline node {node_id} migration session ended with {final_status}',
            node_id=node_id,
            migration_session_id=int(sid) if sid else None,
        )
    if node_terminal_status == 'completed':
        try:
            send_pipeline_batch_notification(run, node)
        except Exception:
            logger.exception(
                'Pipeline batch notification hook failed for run %s node %s',
                run.id,
                node_id,
            )
    if final_status != 'completed':
        raise RuntimeError(
            node_error
            or f"Pipeline node {node_id} migration session ended with {final_status}"
        )


def _execute_pipeline_node(
    run: PipelineRun,
    node,
    user,
    session_ctx: Optional[dict[str, Any]] = None,
    *,
    skip_task_dispatch: bool = False,
    pod_cap: Optional[int] = None,
    wait_for_session: bool = True,
) -> None:
    from apps.elttool.v1.views import task

    close_old_connections()
    if _pipeline_run_was_deleted(run):
        _mark_pipeline_session_deleted(session_ctx)
        raise _PipelineRunStopped()

    node_id = node.id
    if session_ctx is None:
        session_ctx = _start_pipeline_node_session(run, node, user)

    batch = session_ctx['batch']
    connection = session_ctx['connection']
    src_cfg = session_ctx['src_cfg']
    migration_session_id = session_ctx['migration_session_id']
    log_id = session_ctx['log_id']
    pipeline_job_name = session_ctx['pipeline_job_name']
    selected_tables = session_ctx['selected_tables']
    skipped_tables = session_ctx.get('skipped_tables') or []
    batch_table_count = int(
        session_ctx.get('batch_table_count')
        or len(selected_tables) + len(skipped_tables)
    )
    source_config = session_ctx['source_config']
    destination_config = session_ctx['destination_config']
    execution_order = session_ctx['execution_order']

    # Sequential batches cannot fan out into multiple pods for a *dedicated* fleet.
    # Shared flow fleets stay at plan max so later parallel nodes can reuse them.
    effective_pod_cap = pod_cap
    if (
        str(execution_order or '').lower() != 'parallel'
        and not _flow_fleet_reuse_enabled()
    ):
        effective_pod_cap = 1
        if pod_cap and pod_cap > 1:
            logger.warning(
                "PIPELINE_SEQUENTIAL_POD_CAP | run=%s | node=%s | requested=%s | forced=1",
                run.id,
                node_id,
                pod_cap,
            )

    _set_pipeline_node_metadata(
        run,
        node_id,
        connection_id=connection.connection_id,
        batch_name=batch.name,
        migration_session_id=migration_session_id,
        pipeline_job_name=pipeline_job_name,
        status='running',
        started_at=timezone.now().isoformat(),
        finished_at=None,
        error=None,
        execution_order=execution_order,
        table_count=batch_table_count,
        skipped_tables=skipped_tables,
        pod_cap=effective_pod_cap,
    )

    if not skip_task_dispatch and selected_tables:
        task_kwargs = dict(
            source_config=source_config,
            destination_config=destination_config,
            selected_tables=selected_tables,
            selected_attributes_dict={},
            selected_folder_path={},
            connection_id=connection.connection_id,
            time_frequency=str(node.time_frequency or batch.time_frequency or '15'),
            session=None,
            user_id=(
                run.triggered_by_id
                or getattr(user, 'user_id', None)
                or getattr(run.pipeline, 'created_by_id', None)
            ),
            cmp_id=connection.cmp_id,
            connection_name=src_cfg.name,
            is_deleted=connection.DELETED,
            is_updated=connection.SYNCED,
            safety_interval=str(connection.safety_interval or ''),
            last_checked_dates={},
            migration_type='manual',
            executionorder=execution_order,
            log_creation=bool(connection.logs_creation),
            job_name=pipeline_job_name,
            migration_session_id=migration_session_id,
            log_id=log_id,
        )
        if effective_pod_cap is not None:
            task_kwargs['pod_cap'] = int(effective_pod_cap)
        # Shared run fleet so Batch 2 reuses Batch 1's org job slot / pod budget.
        run_meta = _get_pipeline_run_metadata(run)
        flow_state = run_meta.get('flow_pod_state') or {}
        fleet_key = flow_state.get('fleet_key')
        if not fleet_key and (
            run_meta.get('flow_fleet_reuse_enabled') or _flow_fleet_reuse_enabled()
        ):
            fleet_key = f'pipeline_run_{run.id}'
        if fleet_key:
            task_kwargs['fleet_key'] = fleet_key
        if _pipeline_run_was_deleted(run):
            _mark_pipeline_session_deleted(session_ctx)
            raise _PipelineRunStopped()
        task_results = task(**task_kwargs)

        if _task_had_immediate_failure(task_results):
            err_msg = None
            for res in task_results or []:
                if not isinstance(res, dict):
                    continue
                err_msg = res.get('message') or res.get('error') or res.get('response')
                if err_msg:
                    break
            err_msg = err_msg or 'Migration failed to start for pipeline node.'
            _set_pipeline_node_metadata(
                run,
                node_id,
                status='failed',
                finished_at=timezone.now().isoformat(),
                error=err_msg,
            )
            raise RuntimeError(err_msg)

    if not wait_for_session:
        return

    final_status, mig_data = _wait_for_migration_session(
        migration_session_id,
        run=run,
        connection_id=connection.connection_id,
        job_name_prefix=pipeline_job_name,
        node_id=node_id,
    )
    _apply_pipeline_node_outcome(run, node, final_status, mig_data)


def _mark_unstarted_pipeline_nodes_skipped(
    meta: dict,
    *,
    reason: str = 'Skipped — upstream failed or run ended before this batch started.',
) -> dict:
    """Leave unstarted nodes as pending/waiting when a run terminates.

    Previously this flipped leftover nodes to ``skipped``. Downstream batches that
    never ran because an upstream node failed should keep their prior status.
    ``reason`` is accepted for call-site compatibility and ignored.
    """
    del reason  # intentionally unused — preserve prior pending/waiting status
    return meta


def _pipeline_run_has_active_migrations(run: PipelineRun) -> bool:
    """True while any table in this run is still migrating (including Celery retry / Spark poll)."""
    from apps.elttool.models import MigrationRecord

    active_statuses = _ACTIVE_LOAD_STATUSES
    meta = _get_pipeline_run_metadata(run)
    session_ids = []
    for entry in (meta.get('nodes') or {}).values():
        sid = entry.get('migration_session_id')
        if sid:
            session_ids.append(int(sid))
    prefix = f'pipeline_run_{run.id}'
    if session_ids:
        return MigrationRecord.objects.filter(
            migration_session_id__in=session_ids,
            status__in=active_statuses,
            job_name__startswith=prefix,
        ).exists()
    return MigrationRecord.objects.filter(
        job_name__startswith=prefix,
        status__in=active_statuses,
    ).exists()


def _fail_active_pipeline_load_rows(
    run: PipelineRun,
    *,
    reason: str,
    node_id: Optional[int] = None,
    migration_session_id: Optional[int] = None,
) -> int:
    """Mark stuck in-progress table rows failed and clear table locks.

    Retries leave MigrationRecord as in_progress until the last attempt. When the
    node or run is already failed, those rows must not keep showing hourglasses
    in execution logs — only the final error should remain.
    """
    from apps.elttool.models import Log, MigrationRecord, SourceTableSelection

    prefix = f'pipeline_run_{run.id}'
    if node_id is not None:
        prefix = f'pipeline_run_{run.id}_node_{int(node_id)}'
    qs = MigrationRecord.objects.filter(
        job_name__startswith=prefix,
        status__in=_ACTIVE_LOAD_STATUSES,
    )
    if migration_session_id is not None:
        qs = qs.filter(migration_session_id=int(migration_session_id))
    rows = list(qs.values('id', 'connection_id', 'table_name', 'task_id'))
    if not rows:
        return 0

    now = timezone.now()
    fail_message = (reason or 'Migration failed').strip() or 'Migration failed'
    mr_ids = [row['id'] for row in rows]
    connection_tables: dict[int, set[str]] = {}
    task_ids = []
    for row in rows:
        cid = row.get('connection_id')
        table = row.get('table_name')
        if cid and table:
            connection_tables.setdefault(int(cid), set()).add(str(table))
        if row.get('task_id'):
            task_ids.append(str(row['task_id']))

    with transaction.atomic():
        MigrationRecord.objects.filter(id__in=mr_ids).update(
            status='failed',
            updated_at=now,
        )
        for connection_id, tables in connection_tables.items():
            SourceTableSelection.objects.filter(
                connection_id=connection_id,
                table__in=list(tables),
                status='in_progress',
            ).update(status=None)
        # Close open progress logs so the UI keeps the final error, not a stuck "I".
        Log.objects.filter(
            migration_record_id__in=mr_ids,
            status='I',
        ).update(status='E', message=fail_message)

    if task_ids:
        try:
            from apps.elttool.services.celery_inspect_service import (
                get_migration_celery_app,
            )

            get_migration_celery_app().control.revoke(
                task_ids, terminate=False
            )
        except Exception:
            logger.exception(
                'PIPELINE_FAIL_ROWS_REVOKE_FAILED | run=%s | node=%s',
                run.id,
                node_id,
            )

    logger.warning(
        'PIPELINE_FAIL_ACTIVE_ROWS | run=%s | node=%s | session=%s | count=%s | reason=%s',
        run.id,
        node_id,
        migration_session_id,
        len(mr_ids),
        fail_message[:200],
    )
    return len(mr_ids)


def _fail_pipeline_run(run: PipelineRun, executed: list, error: Exception) -> None:
    logger.exception("Pipeline run %s failed", run.id)
    error_text = str(error).strip()
    # Always clear rows for nodes that already failed. Sibling loads can keep
    # running under failure_pending; their rows stay until they finish or we close.
    meta_now = _get_pipeline_run_metadata(run)
    for key, entry in (meta_now.get('nodes') or {}).items():
        entry = entry or {}
        if str(entry.get('status') or '').lower() != 'failed':
            continue
        try:
            node_id = int(key)
        except (TypeError, ValueError):
            continue
        sid = entry.get('migration_session_id')
        _fail_active_pipeline_load_rows(
            run,
            reason=str(entry.get('error') or error_text),
            node_id=node_id,
            migration_session_id=int(sid) if sid else None,
        )

    if _pipeline_run_has_active_migrations(run):
        logger.warning(
            'PIPELINE_FAIL_DEFERRED | run=%s | reason=active_migration_records | error=%s',
            run.id,
            error,
        )
        meta = {
            **_get_pipeline_run_metadata(run),
            'executed_node_ids': executed,
            'error': _pipeline_run_failure_message(
                _get_pipeline_run_metadata(run),
                error_text,
            ),
            'failure_pending': True,
        }
        run.metadata = meta
        run.save(update_fields=['metadata'])
        try:
            _schedule_deferred_pipeline_failure_check(run.id)
        except Exception:
            logger.exception(
                'PIPELINE_FAIL_DEFERRED_SCHEDULE_FAILED | run=%s',
                run.id,
            )
        return

    _fail_active_pipeline_load_rows(run, reason=error_text)
    meta = _mark_unstarted_pipeline_nodes_skipped(
        {
            **_get_pipeline_run_metadata(run),
            'executed_node_ids': executed,
            'error': error_text,
            'current_node_id': None,
            'current_node_ids': [],
            'failure_pending': False,
        },
        reason=f'Skipped — pipeline stopped: {error}',
    )
    meta['error'] = _pipeline_run_failure_message(meta, error_text)
    run.status = 'failed'
    run.finished_at = timezone.now()
    run.metadata = meta
    run.save(update_fields=['status', 'finished_at', 'metadata'])
    try:
        send_pipeline_flow_notification(run)
    except Exception:
        logger.exception('Pipeline flow notification hook failed for run %s', run.id)
    _teardown_pipeline_flow_fleet(run.id)


def _company_plan_name(company) -> str:
    if company is None:
        return ''
    return str(getattr(company, 'plan', '') or '').strip()


_PLAN_MAX_CONCURRENT_FLOWS = {
    "free": 1,
    "standard": 2,
    "discounted": 1,
    "premium": 2,
    "enterprise": 3,
}


def _company_max_concurrent_flows(company) -> int:
    """How many pipeline runs this organisation may orchestrate at once.

    This is the plan's max_concurrent_jobs, not a cluster-wide slot count.
    Another organisation at its own cap does not consume this allowance.
    """
    plan_name = _company_plan_name(company).lower()
    if company is not None:
        try:
            from apps.elttool.models import PlanResourceConfig

            row = PlanResourceConfig.objects.filter(
                plan_name__iexact=plan_name
            ).values_list("max_concurrent_jobs", flat=True).first()
            if row is not None:
                return max(1, int(row))
        except Exception:
            logger.exception(
                "PIPELINE_RUN_COMPANY_LIMIT_LOOKUP_FAILED | plan=%s", plan_name
            )
    return _PLAN_MAX_CONCURRENT_FLOWS.get(plan_name, 1)


_COMPANY_FLOW_SLOT_LOCK_BASE = 8675311


def _company_flow_slot_xact_lock(company_id: int) -> None:
    if db_connection.vendor != "postgresql":
        return
    with db_connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [_COMPANY_FLOW_SLOT_LOCK_BASE + int(company_id)],
        )


def _run_holds_company_flow_slot(run) -> bool:
    """True when this run occupies one plan concurrent-flow slot."""
    if (run.status or "") != "running":
        return False
    if (run.metadata or {}).get("failure_pending"):
        return False
    return True


def _run_metadata_consumes_flow_slot(metadata, *, status: str = "running") -> bool:
    """True when this run state should hold a plan slot (see ``_run_holds_company_flow_slot``)."""
    if (status or "") != "running":
        return False
    meta = metadata or {}
    if meta.get("failure_pending"):
        return False
    return True


_deferred_release_depth = threading.local()


def _release_terminal_deferred_pipeline_failures(company) -> None:
    """Close deferred failures whose table rows are already terminal."""
    if company is None:
        return
    if getattr(_deferred_release_depth, "depth", 0):
        return
    _deferred_release_depth.depth = 1
    try:
        pending_runs = [
            run
            for run in PipelineRun.objects.filter(
                pipeline__cmp_id=company.cmp_id,
                status="running",
            ).only("id", "status", "metadata", "finished_at", "pipeline_id")
            if (run.metadata or {}).get("failure_pending")
        ]
        for run in pending_runs:
            try:
                _reconcile_pipeline_run_status(run)
            except Exception:
                logger.exception(
                    "PIPELINE_DEFERRED_FAILURE_RECONCILE_FAILED | run=%s",
                    run.id,
                )
    finally:
        _deferred_release_depth.depth = 0


def _schedule_deferred_pipeline_failure_check(run_id: int) -> None:
    from apps.elttool.tasks.pipeline_tasks import (
        PIPELINE_DEFERRED_FAILURE_RETRY_SECONDS,
        PIPELINE_RUN_CELERY_QUEUE,
        finalize_deferred_pipeline_failure,
    )

    finalize_deferred_pipeline_failure.apply_async(
        args=[run_id],
        countdown=PIPELINE_DEFERRED_FAILURE_RETRY_SECONDS,
        queue=PIPELINE_RUN_CELERY_QUEUE,
    )


def _company_active_flow_count(company, *, exclude_run_id: Optional[int] = None) -> int:
    """Running pipeline runs holding a plan flow slot for this organisation."""
    if company is None:
        return 0
    _release_terminal_deferred_pipeline_failures(company)
    qs = PipelineRun.objects.filter(
        pipeline__cmp_id=company.cmp_id, status="running"
    ).only("id", "status", "metadata")
    if exclude_run_id is not None:
        qs = qs.exclude(id=exclude_run_id)
    return sum(1 for other in qs if _run_holds_company_flow_slot(other))


def try_acquire_company_flow_slot(run_id: int) -> bool:
    """Atomically claim a plan flow slot for this run."""
    with transaction.atomic():
        run = (
            PipelineRun.objects.select_for_update()
            .select_related("pipeline", "pipeline__cmp")
            .get(id=run_id)
        )
        company = getattr(run.pipeline, "cmp", None)
        if company is not None:
            _company_flow_slot_xact_lock(company.cmp_id)
            limit = _company_max_concurrent_flows(company)
            active = _company_active_flow_count(company, exclude_run_id=run.id)
            if active >= limit:
                return False
        if run.status == "queued":
            meta = dict(_get_pipeline_run_metadata(run))
            meta["waiting_for_capacity"] = False
            run.status = "running"
            run.metadata = meta
            run.save(update_fields=["status", "metadata"])
        return True


def company_has_flow_capacity(company) -> bool:
    """True when this organisation still has a free concurrent-flow slot."""
    if company is None:
        return True
    return _company_active_flow_count(company) < _company_max_concurrent_flows(company)


def company_flow_capacity_available(run_id: int) -> bool:
    """True when this organisation still has a free concurrent-flow slot."""
    run = PipelineRun.objects.select_related("pipeline", "pipeline__cmp").get(id=run_id)
    company = getattr(run.pipeline, "cmp", None)
    if company is None:
        return True
    limit = _company_max_concurrent_flows(company)
    active = _company_active_flow_count(company, exclude_run_id=run.id)
    if active >= limit:
        logger.info(
            "PIPELINE_RUN_COMPANY_AT_LIMIT | run=%s | company=%s | active=%s | limit=%s",
            run.id,
            company.cmp_id,
            active,
            limit,
        )
        return False
    return True


def _mark_pipeline_run_waiting_for_capacity(run_id: int) -> None:
    """Remember that this run is queued for an organisation flow slot.

    Does not set ``executor``, so the run does not consume a concurrent-flow slot
    while the orchestrator retries.
    """
    try:
        run = PipelineRun.objects.get(id=run_id)
    except PipelineRun.DoesNotExist:
        return
    if run.status not in ("running", "queued"):
        return
    meta = _get_pipeline_run_metadata(run)
    if meta.get("executor"):
        return
    if run.status != "queued":
        run.status = "queued"
        run.save(update_fields=["status"])
    _update_pipeline_run_metadata(
        run,
        waiting_for_capacity=True,
        waiting_since=meta.get("waiting_since") or timezone.now().isoformat(),
    )


def _mark_pipeline_run_slot_acquired(run_id: int) -> None:
    """Move a capacity wait into running once this organisation has a free slot."""
    updated = PipelineRun.objects.filter(id=run_id, status="queued").update(status="running")
    if not updated:
        return
    run = PipelineRun.objects.get(id=run_id)
    _update_pipeline_run_metadata(run, waiting_for_capacity=False)


def _resolve_pipeline_run_pod_budget(company) -> tuple[str, int, int, int]:
    """
    Return (plan_name, flow_budget, parallel_slots, concurrency) for a pipeline run.

    Prefer PlanResourceConfig.max_pods / company effective pod count when available.
    """
    plan_name = _company_plan_name(company)
    override = None
    if company is not None:
        try:
            override = int(company.get_effective_pod_count())
        except Exception:
            override = None
        try:
            from apps.elttool.models import PlanResourceConfig

            row = PlanResourceConfig.objects.filter(
                plan_name__iexact=plan_name
            ).values_list('max_pods', flat=True).first()
            if row is not None:
                db_cap = int(row)
                override = max(int(override or 0), db_cap) if override else db_cap
        except Exception:
            pass

    flow_budget = plan_run_pod_cap(plan_name, override=override)
    slots = plan_parallel_slots(plan_name) if plan_name else PIPELINE_MAX_PARALLEL_NODES
    conc = plan_concurrency(plan_name)
    return plan_name, flow_budget, slots, conc


def _node_table_counts_from_seed(nodes_seed: dict, node_ids: list[int]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for node_id in node_ids:
        entry = nodes_seed.get(_pipeline_node_meta_key(node_id)) or {}
        counts[node_id] = max(0, int(entry.get('table_count') or 0))
    return counts


def _release_flow_pod_allocation(run: PipelineRun, node_id: int) -> None:
    with transaction.atomic():
        locked = _lock_pipeline_run_for_metadata(run)
        meta = _get_pipeline_run_metadata(locked)
        state = dict(meta.get('flow_pod_state') or {})
        allocations = dict(state.get('allocations') or {})
        job_key = f"pipeline_run_{run.id}_node_{node_id}"
        allocations.pop(job_key, None)
        allocations.pop(str(node_id), None)
        state['allocations'] = allocations
        meta['flow_pod_state'] = state
        locked.metadata = meta
        locked.save(update_fields=['metadata'])
        run.metadata = meta


def _store_flow_pod_allocations(
    run: PipelineRun,
    new_allocations: dict[int, int],
    *,
    plan_name: str,
    flow_budget: int,
) -> None:
    with transaction.atomic():
        locked = _lock_pipeline_run_for_metadata(run)
        meta = _get_pipeline_run_metadata(locked)
        state = dict(meta.get('flow_pod_state') or {})
        allocations = dict(state.get('allocations') or {})
        for node_id, cap in new_allocations.items():
            allocations[f"pipeline_run_{run.id}_node_{node_id}"] = int(cap)
            allocations[str(node_id)] = int(cap)
        state.update({
            'plan_name': plan_name,
            'plan_max_pods': flow_budget,
            'flow_budget': flow_budget,
            'allocations': allocations,
        })
        meta['flow_pod_state'] = state
        locked.metadata = meta
        locked.save(update_fields=['metadata'])
        run.metadata = meta


def _execute_pipeline_dependency_dag(
    run: PipelineRun,
    node_map: dict[int, Any],
    user,
    exec_node_ids: list[int],
    edge_pairs: list[tuple[int, int]],
    executed: list,
    *,
    initial_completed: Optional[set[int]] = None,
    resolve_session=None,
) -> None:
    """Run batch nodes when their direct dependencies complete (not level barriers).

    Independent branches keep running when another branch fails. Dependents of a
    failed node are skipped. Light ready nodes gain priority after waiting so they
    are not starved by heavier siblings under parallel_slots caps.
    """
    parents, children = build_batch_dependency_graph(exec_node_ids, edge_pairs)
    completed = set(initial_completed or ())
    failed: set[int] = set()
    skipped: set[int] = set()
    in_flight: dict[Any, int] = {}
    ready_wait: dict[int, int] = {}
    first_failure: Optional[Exception] = None

    meta = _get_pipeline_run_metadata(run)
    node_weights = meta.get('node_weights') or {}
    fleet_reuse = _flow_fleet_reuse_enabled() or bool(meta.get('flow_fleet_reuse_enabled'))
    flow_enabled = _flow_pod_budget_enabled_for_run(meta)
    plan_name = (meta.get('flow_pod_state') or {}).get('plan_name') or ''
    flow_budget = int((meta.get('flow_pod_state') or {}).get('flow_budget') or 0)
    max_parallel = int(
        (meta.get('flow_pod_state') or {}).get('parallel_slots')
        or PIPELINE_MAX_PARALLEL_NODES
    )
    concurrency = int(
        (meta.get('flow_pod_state') or {}).get('concurrency')
        or plan_concurrency(plan_name)
    )

    # When budget is enabled, cannot run more concurrent nodes than available pods.
    if flow_enabled and flow_budget > 0:
        max_parallel = min(max_parallel, flow_budget)

    table_counts: dict[int, int] = {}
    for nid in exec_node_ids:
        w = node_weights.get(str(nid)) or {}
        table_counts[nid] = int(w.get('tables') or 0)
        if table_counts[nid] <= 0:
            entry = (meta.get('nodes') or {}).get(str(nid)) or {}
            table_counts[nid] = int(entry.get('table_count') or 0)

    terminal = completed | failed | skipped

    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        while len(terminal) < len(exec_node_ids):
            if _pipeline_run_was_deleted(run):
                raise _PipelineRunStopped()
            ready = [
                nid
                for nid in ready_batch_node_ids(
                    exec_node_ids,
                    parents,
                    completed,
                    set(in_flight.values()) | failed | skipped,
                )
                if nid not in failed and nid not in skipped
            ]

            # Track how long each ready node has waited without being started.
            ready_set = set(ready)
            for nid in list(ready_wait.keys()):
                if nid not in ready_set:
                    ready_wait.pop(nid, None)
            for nid in ready:
                ready_wait[nid] = int(ready_wait.get(nid, 0)) + 1

            slots = max_parallel - len(in_flight)
            if flow_enabled and flow_budget > 0 and not fleet_reuse:
                state = (_get_pipeline_run_metadata(run).get('flow_pod_state') or {})
                if not in_flight:
                    # No active fleets — clear stale allocations so budget is fully available.
                    if state.get('allocations'):
                        _update_pipeline_run_metadata(
                            run,
                            flow_pod_state={
                                **state,
                                'allocations': {},
                            },
                        )
                    remaining_budget = flow_budget
                else:
                    used = 0
                    allocs = state.get('allocations') or {}
                    for nid in in_flight.values():
                        used += int(allocs.get(str(nid)) or allocs.get(
                            f"pipeline_run_{run.id}_node_{nid}"
                        ) or 0)
                    remaining_budget = max(0, flow_budget - used)
                # Each new node needs at least 1 pod; limit how many we can start.
                slots = min(slots, remaining_budget)

            if slots > 0 and ready:
                if node_weights:
                    picked = pick_ready_nodes_weighted(
                        ready, node_weights, slots, wait_rounds=ready_wait
                    )
                else:
                    # Prefer longest-waiting when no weights.
                    picked = sorted(
                        ready,
                        key=lambda nid: (-int(ready_wait.get(nid, 0)), nid),
                    )[:slots]

                new_caps: dict[int, int] = {}
                if fleet_reuse and flow_budget > 0 and picked:
                    new_caps = allocate_flow_pod_caps(
                        {nid: table_counts.get(nid, 0) for nid in picked},
                        flow_budget,
                        concurrency=concurrency,
                    )
                    picked = [
                        nid for nid in picked if int(new_caps.get(nid) or 0) > 0
                    ]
                    if picked:
                        _store_flow_pod_allocations(
                            run,
                            {nid: new_caps[nid] for nid in picked},
                            plan_name=plan_name,
                            flow_budget=flow_budget,
                        )
                        logger.info(
                            "PIPELINE_FLOW_FLEET_REUSE | run=%s | budget=%s | caps=%s | nodes=%s",
                            run.id,
                            flow_budget,
                            {nid: new_caps[nid] for nid in picked},
                            picked,
                        )
                elif flow_enabled and flow_budget > 0 and picked:
                    # Allocate only from remaining budget for newly started nodes;
                    # already-running fleets stay fixed (stable fleet).
                    state = (_get_pipeline_run_metadata(run).get('flow_pod_state') or {})
                    used = 0
                    for nid in in_flight.values():
                        allocs = state.get('allocations') or {}
                        used += int(allocs.get(str(nid)) or allocs.get(
                            f"pipeline_run_{run.id}_node_{nid}"
                        ) or 0)
                    remaining_budget = max(0, flow_budget - used)
                    new_caps = allocate_flow_pod_caps(
                        {nid: table_counts.get(nid, 0) for nid in picked},
                        remaining_budget,
                        concurrency=concurrency,
                    )
                    # Drop nodes that got 0 pods (budget exhausted).
                    picked = [nid for nid in picked if int(new_caps.get(nid) or 0) > 0]
                    if picked:
                        _store_flow_pod_allocations(
                            run,
                            {nid: new_caps[nid] for nid in picked},
                            plan_name=plan_name,
                            flow_budget=flow_budget,
                        )
                        logger.info(
                            "PIPELINE_FLOW_POD_ALLOC | run=%s | budget=%s | remaining=%s | caps=%s",
                            run.id,
                            flow_budget,
                            remaining_budget,
                            {nid: new_caps[nid] for nid in picked},
                        )

                if _pipeline_run_was_deleted(run):
                    raise _PipelineRunStopped()
                for node_id in picked:
                    ready_wait.pop(node_id, None)
                    if resolve_session is not None:
                        session_ctx, skip_task = resolve_session(
                            run, node_map[node_id], user, node_id
                        )
                    else:
                        session_ctx = _start_pipeline_node_session(
                            run, node_map[node_id], user
                        )
                        skip_task = False
                    node_pod_cap = new_caps.get(node_id) if (flow_enabled or fleet_reuse) else None
                    _refresh_pipeline_flow_fleet_lease(run.id)
                    future = pool.submit(
                        _execute_pipeline_node,
                        run,
                        node_map[node_id],
                        user,
                        session_ctx,
                        skip_task_dispatch=skip_task,
                        pod_cap=node_pod_cap,
                    )
                    in_flight[future] = node_id

            if in_flight:
                _update_pipeline_run_metadata(
                    run,
                    current_node_ids=sorted(in_flight.values()),
                )
                done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    node_id = in_flight.pop(future)
                    try:
                        future.result()
                    except _PipelineRunStopped:
                        raise
                    except Exception as exc:
                        if flow_enabled:
                            _release_flow_pod_allocation(run, node_id)
                        failed.add(node_id)
                        if first_failure is None:
                            first_failure = exc
                        # Skip only this branch's dependents — other branches continue.
                        newly_skipped = transitive_dependents({node_id}, children)
                        newly_skipped -= completed | failed | set(in_flight.values())
                        for sid in newly_skipped:
                            if sid in skipped:
                                continue
                            skipped.add(sid)
                            _set_pipeline_node_metadata(
                                run,
                                sid,
                                status='skipped',
                                error=(
                                    f'Skipped — upstream node {node_id} failed: {exc}'
                                ),
                                finished_at=timezone.now().isoformat(),
                            )
                        logger.exception(
                            "PIPELINE_NODE_FAILED | run=%s | node=%s | "
                            "continuing independent branches | skipped=%s",
                            run.id,
                            node_id,
                            sorted(newly_skipped),
                        )
                        terminal = completed | failed | skipped
                        continue
                    completed.add(node_id)
                    if node_id not in executed:
                        executed.append(node_id)
                    if flow_enabled:
                        _release_flow_pod_allocation(run, node_id)
                    _maybe_early_teardown_flow_fleet(
                        run,
                        exec_node_ids,
                        fleet_reuse=fleet_reuse,
                        node_status='completed',
                    )
                _update_pipeline_run_metadata(
                    run,
                    executed_node_ids=executed,
                    current_node_ids=sorted(in_flight.values()),
                )
                terminal = completed | failed | skipped
                continue

            if len(terminal) < len(exec_node_ids):
                # Nothing running and nothing ready — remaining must be blocked.
                remaining = [
                    nid for nid in exec_node_ids if nid not in terminal
                ]
                for sid in remaining:
                    skipped.add(sid)
                    _set_pipeline_node_metadata(
                        run,
                        sid,
                        status='skipped',
                        error='Skipped — blocked by upstream failure or unmet dependency',
                        finished_at=timezone.now().isoformat(),
                    )
                terminal = completed | failed | skipped
                if first_failure is not None:
                    break
                raise RuntimeError(
                    "Pipeline execution stalled: remaining nodes are blocked by dependencies."
                )
            break

        _update_pipeline_run_metadata(
            run,
            executed_node_ids=executed,
            current_node_ids=[],
            current_node_id=None,
        )

    if first_failure is not None:
        raise first_failure


def _pipeline_run_is_resumable(run: PipelineRun) -> bool:
    """True when orchestrator died but run metadata still tracks in-flight/completed nodes."""
    if run.status != 'running':
        return False
    meta = _get_pipeline_run_metadata(run)
    return bool(meta.get('executor') and meta.get('nodes'))


def _resume_pipeline_strict(run_id: int) -> None:
    """Continue a running pipeline after celery-worker-django restart or orchestrator loss."""
    run = PipelineRun.objects.select_related('pipeline', 'pipeline__cmp').get(id=run_id)
    pipeline = run.pipeline

    _sync_pipeline_run_nodes_from_migrations(run)
    run.refresh_from_db(fields=['status', 'metadata'])
    meta = _get_pipeline_run_metadata(run)
    nodes_meta = meta.get('nodes') or {}

    initial_completed: set[int] = set()
    for key, entry in nodes_meta.items():
        try:
            node_id = int(key)
        except (TypeError, ValueError):
            continue
        if (entry.get('status') or '').lower() == 'completed':
            initial_completed.add(node_id)

    executed: list[int] = []
    for nid in meta.get('executed_node_ids') or []:
        try:
            executed.append(int(nid))
        except (TypeError, ValueError):
            continue
    for node_id in sorted(initial_completed):
        if node_id not in executed:
            executed.append(node_id)

    try:
        nodes, edges = load_validated_graph_for_execution(pipeline)
        exec_node_ids, edge_pairs = execution_node_ids_and_edges(nodes, edges)
        node_map = {n.id: n for n in nodes if is_batch_node(n)}

        user = _resolve_pipeline_run_user(run)

        def resolve_session(run, node, user, node_id):
            fresh = _get_pipeline_run_metadata(run)
            entry = (fresh.get('nodes') or {}).get(str(node_id)) or {}
            return _resume_pipeline_node_session(run, node, user, entry)

        logger.info(
            "PIPELINE_RUN_RESUME | run=%s | completed_nodes=%s | executed=%s",
            run.id,
            sorted(initial_completed),
            executed,
        )

        if meta.get('flow_fleet_reuse_enabled') or _flow_fleet_reuse_enabled():
            _refresh_pipeline_flow_fleet_lease(run.id)

        _execute_pipeline_dependency_dag(
            run,
            node_map,
            user,
            exec_node_ids,
            edge_pairs,
            executed,
            initial_completed=initial_completed,
            resolve_session=resolve_session,
        )

        run.status = 'completed'
        run.finished_at = timezone.now()
        run.metadata = _mark_unstarted_pipeline_nodes_skipped({
            **_get_pipeline_run_metadata(run),
            'executed_node_ids': executed,
            'current_node_id': None,
            'current_node_ids': [],
        })
        run.save(update_fields=['status', 'finished_at', 'metadata'])
        try:
            send_pipeline_flow_notification(run)
        except Exception:
            logger.exception('Pipeline flow notification hook failed for run %s', run.id)
        _teardown_pipeline_flow_fleet(run.id)
    except _PipelineRunStopped:
        logger.info('PIPELINE_RUN_STOPPED_DELETED | run=%s', run_id)
        return
    except Exception as exc:
        if _pipeline_run_was_deleted(run):
            logger.info('PIPELINE_RUN_STOPPED_DELETED | run=%s', run_id)
            return
        _fail_pipeline_run(run, executed, exc)


def _prepare_pipeline_execution(run: PipelineRun):
    """Seed run metadata and return the execution context, or None if it failed."""
    pipeline = run.pipeline
    try:
        nodes, edges = load_validated_graph_for_execution(pipeline)
        exec_node_ids, edge_pairs = execution_node_ids_and_edges(nodes, edges)
        try:
            levels = dag_execution_levels(exec_node_ids, edge_pairs)
        except (DagCycleError, ValueError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        node_map = {n.id: n for n in nodes if is_batch_node(n)}

        user = _resolve_pipeline_run_user(run)

        # Only reachable execution nodes — never seed disconnected draft orphans
        # into participating_node_ids (they show as floating nodes on historic Flow).
        exec_id_set = set(exec_node_ids)
        participating_ids, nodes_seed = _seed_pipeline_run_node_entries(
            [n for n in nodes if is_batch_node(n) and n.id in exec_id_set]
        )
        table_counts = _node_table_counts_from_seed(nodes_seed, exec_node_ids)
        node_weights = compute_node_weights(
            exec_node_ids,
            edge_pairs,
            table_counts,
            levels=levels,
        )
        plan_name, flow_budget, parallel_slots, concurrency = _resolve_pipeline_run_pod_budget(
            getattr(pipeline, 'cmp', None)
        )
        flow_budget_enabled = _flow_pod_budget_enabled_for_run()
        fleet_reuse = _flow_fleet_reuse_enabled()
        flow_pod_state = {
            'plan_name': plan_name,
            'plan_max_pods': flow_budget,
            'flow_budget': flow_budget,
            'parallel_slots': parallel_slots,
            'concurrency': concurrency,
            'allocations': {},
        }
        if fleet_reuse:
            flow_pod_state['fleet_key'] = f'pipeline_run_{run.id}'
        # Freeze the FULL canvas topology (including Start→batch edges), not the
        # batch-only execution pairs — history UI needs exact layout edges.
        start = get_start_node(nodes)
        canvas_edge_pairs = []
        for edge in edges:
            try:
                canvas_edge_pairs.append({
                    'from_node_id': int(edge.from_node_id),
                    'to_node_id': int(edge.to_node_id),
                })
            except (TypeError, ValueError, AttributeError):
                continue
        graph_snapshot = {
            'start_node_id': start.id if start else None,
            'node_ids': list(participating_ids),
            'edges': canvas_edge_pairs,
        }
        _update_pipeline_run_metadata(
            run,
            nodes=nodes_seed,
            participating_node_ids=participating_ids,
            edge_pairs=canvas_edge_pairs,
            graph_snapshot=graph_snapshot,
            executed_node_ids=[],
            pipeline_id=pipeline.id,
            current_node_ids=[],
            current_node_id=None,
            executor='celery',
            started_at=timezone.now().isoformat(),
            graph_source='validated',
            node_weights=node_weights,
            flow_pod_state=flow_pod_state,
            flow_pod_budget_enabled=flow_budget_enabled,
            flow_fleet_reuse_enabled=fleet_reuse,
        )
        if fleet_reuse:
            _refresh_pipeline_flow_fleet_lease(run.id)
        logger.info(
            "PIPELINE_RUN_BUDGET | run=%s | plan=%s | budget=%s | slots=%s | "
            "flow_budget_enabled=%s | fleet_reuse=%s | nodes=%s",
            run.id,
            plan_name,
            flow_budget,
            parallel_slots,
            flow_budget_enabled,
            fleet_reuse,
            len(exec_node_ids),
        )
        return {
            'node_map': node_map,
            'user': user,
            'exec_node_ids': exec_node_ids,
            'edge_pairs': edge_pairs,
        }
    except Exception as exc:
        _fail_pipeline_run(run, [], exc)
        return None


def _complete_nonblocking_pipeline_run(run: PipelineRun, executed: list) -> None:
    run.status = 'completed'
    run.finished_at = timezone.now()
    run.metadata = _mark_unstarted_pipeline_nodes_skipped({
        **_get_pipeline_run_metadata(run),
        'executed_node_ids': executed,
        'current_node_id': None,
        'current_node_ids': [],
    })
    run.save(update_fields=['status', 'finished_at', 'metadata'])
    try:
        send_pipeline_flow_notification(run)
    except Exception:
        logger.exception('Pipeline flow notification hook failed for run %s', run.id)
    _teardown_pipeline_flow_fleet(run.id)


def _orchestrator_sets(meta: dict):
    state = dict(meta.get('orchestrator') or {})

    def _ints(values):
        parsed = []
        for value in values or []:
            try:
                parsed.append(int(value))
            except (TypeError, ValueError):
                continue
        return parsed

    pairs = []
    for pair in state.get('edge_pairs') or []:
        try:
            pairs.append((int(pair[0]), int(pair[1])))
        except (TypeError, ValueError, IndexError):
            continue
    ready_wait = {}
    for key, value in (state.get('ready_wait') or {}).items():
        try:
            ready_wait[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return {
        'exec_node_ids': _ints(state.get('exec_node_ids')),
        'edge_pairs': pairs,
        'completed': set(_ints(state.get('completed'))),
        'failed': set(_ints(state.get('failed'))),
        'skipped': set(_ints(state.get('skipped'))),
        'in_flight': _ints(state.get('in_flight')),
        'ready_wait': ready_wait,
        'executed': _ints(state.get('executed')),
        'first_failure': state.get('first_failure'),
        'failed_seen_at': {
            str(key): str(value)
            for key, value in (state.get('failed_seen_at') or {}).items()
            if value
        },
    }


def _save_orchestrator(run: PipelineRun, state: dict) -> None:
    _update_pipeline_run_metadata(
        run,
        orchestrator={
            'exec_node_ids': list(state['exec_node_ids']),
            'edge_pairs': [[a, b] for a, b in state['edge_pairs']],
            'completed': sorted(state['completed']),
            'failed': sorted(state['failed']),
            'skipped': sorted(state['skipped']),
            'in_flight': list(state['in_flight']),
            'ready_wait': {str(k): v for k, v in state['ready_wait'].items()},
            'executed': list(state['executed']),
            'first_failure': state['first_failure'],
            'failed_seen_at': dict(state.get('failed_seen_at') or {}),
        },
        executed_node_ids=list(state['executed']),
        current_node_ids=list(state['in_flight']),
    )


def _failed_status_is_confirmed(seen_at, now=None):
    """Return (confirmed, seen_at_to_keep). First sighting starts the window."""
    now = now or timezone.now()
    if not seen_at:
        return False, now.isoformat()
    try:
        seen_dt = datetime.fromisoformat(str(seen_at))
    except (TypeError, ValueError):
        return False, now.isoformat()
    if timezone.is_naive(seen_dt):
        seen_dt = timezone.make_aware(seen_dt, timezone.utc)
    if (now - seen_dt).total_seconds() >= PIPELINE_FAILED_CONFIRM_SECONDS:
        return True, None
    return False, str(seen_at)


def advance_nonblocking_pipeline_run(run_id: int) -> str:
    """Dispatch newly ready nodes or close the run. Returns ``continue`` or ``done``.

    Does not sleep. The caller re-queues a poll task while nodes are in flight.
    """
    run = PipelineRun.objects.select_related('pipeline', 'pipeline__cmp').get(id=run_id)
    if _pipeline_run_was_deleted(run) or run.status in ('failed', 'completed', 'deleted'):
        return 'done'
    meta = _get_pipeline_run_metadata(run)
    state = _orchestrator_sets(meta)
    exec_node_ids = state['exec_node_ids']
    if not exec_node_ids:
        _fail_pipeline_run(run, state['executed'], RuntimeError('Pipeline orchestration state is missing.'))
        return 'done'

    nodes, _edges = load_validated_graph_for_execution(run.pipeline)
    node_map = {n.id: n for n in nodes if is_batch_node(n)}
    user = _resolve_pipeline_run_user(run)

    parents, children = build_batch_dependency_graph(exec_node_ids, state['edge_pairs'])
    completed = state['completed']
    failed = state['failed']
    skipped = state['skipped']
    in_flight = list(state['in_flight'])
    executed = list(state['executed'])
    first_failure = state['first_failure']
    ready_wait = state['ready_wait']

    flow_enabled = _flow_pod_budget_enabled_for_run(meta)
    fleet_reuse = _flow_fleet_reuse_enabled() or bool(meta.get('flow_fleet_reuse_enabled'))
    plan_name = (meta.get('flow_pod_state') or {}).get('plan_name') or ''
    flow_budget = int((meta.get('flow_pod_state') or {}).get('flow_budget') or 0)
    max_parallel = int(
        (meta.get('flow_pod_state') or {}).get('parallel_slots')
        or PIPELINE_MAX_PARALLEL_NODES
    )
    concurrency = int(
        (meta.get('flow_pod_state') or {}).get('concurrency')
        or plan_concurrency(plan_name)
    )
    if flow_enabled and flow_budget > 0:
        max_parallel = min(max_parallel, flow_budget)
    node_weights = meta.get('node_weights') or {}
    table_counts = {}
    for nid in exec_node_ids:
        weight = node_weights.get(str(nid)) or {}
        table_counts[nid] = int(weight.get('tables') or 0)
        if table_counts[nid] <= 0:
            entry = (meta.get('nodes') or {}).get(str(nid)) or {}
            table_counts[nid] = int(entry.get('table_count') or 0)

    failed_seen = dict(state.get('failed_seen_at') or {})
    still_running = []
    for node_id in in_flight:
        entry = (meta.get('nodes') or {}).get(str(node_id)) or {}
        session_id = entry.get('migration_session_id')
        status = 'running'
        data = None
        if session_id:
            started_at = entry.get('started_at')
            timed_out = False
            if started_at:
                try:
                    started_dt = datetime.fromisoformat(str(started_at))
                    if timezone.is_naive(started_dt):
                        started_dt = timezone.make_aware(started_dt, timezone.utc)
                    timed_out = (
                        timezone.now() - started_dt
                    ).total_seconds() > PIPELINE_NODE_TIMEOUT_SECONDS
                except (TypeError, ValueError):
                    timed_out = False
            if timed_out:
                status, data = 'timeout', None
            else:
                status, data = _peek_migration_session(
                    int(session_id),
                    run=run,
                    node_id=node_id,
                )
        else:
            status = 'failed'
        if status == 'failed':
            confirmed, keep = _failed_status_is_confirmed(failed_seen.get(str(node_id)))
            if keep:
                failed_seen[str(node_id)] = keep
            else:
                failed_seen.pop(str(node_id), None)
            if not confirmed:
                still_running.append(node_id)
                continue
        else:
            failed_seen.pop(str(node_id), None)
        if status == 'running':
            still_running.append(node_id)
            continue
        node = node_map.get(node_id)
        try:
            if node is None:
                raise RuntimeError(f'Pipeline node {node_id} is missing from the published graph.')
            _apply_pipeline_node_outcome(run, node, status, data)
        except _PipelineRunStopped:
            return 'done'
        except Exception as exc:
            if flow_enabled:
                _release_flow_pod_allocation(run, node_id)
            failed.add(node_id)
            if first_failure is None:
                first_failure = str(exc)
            newly_skipped = transitive_dependents({node_id}, children)
            newly_skipped -= completed | failed | set(still_running)
            for sid in newly_skipped:
                if sid in skipped:
                    continue
                skipped.add(sid)
                _set_pipeline_node_metadata(
                    run,
                    sid,
                    status='skipped',
                    error=f'Skipped — upstream node {node_id} failed: {exc}',
                    finished_at=timezone.now().isoformat(),
                )
            logger.exception(
                "PIPELINE_NODE_FAILED | run=%s | node=%s | continuing independent branches",
                run.id,
                node_id,
            )
            continue
        completed.add(node_id)
        if node_id not in executed:
            executed.append(node_id)
        if flow_enabled:
            _release_flow_pod_allocation(run, node_id)
        _maybe_early_teardown_flow_fleet(
            run,
            exec_node_ids,
            fleet_reuse=fleet_reuse,
            node_status='completed',
        )
    in_flight = still_running

    terminal = completed | failed | skipped
    ready = [
        nid
        for nid in ready_batch_node_ids(
            exec_node_ids,
            parents,
            completed,
            set(in_flight) | failed | skipped,
        )
        if nid not in failed and nid not in skipped
    ]
    ready_set = set(ready)
    for nid in list(ready_wait.keys()):
        if nid not in ready_set:
            ready_wait.pop(nid, None)
    for nid in ready:
        ready_wait[nid] = int(ready_wait.get(nid, 0)) + 1

    slots = max_parallel - len(in_flight)
    if flow_enabled and flow_budget > 0 and not fleet_reuse:
        pod_state = (_get_pipeline_run_metadata(run).get('flow_pod_state') or {})
        if not in_flight and pod_state.get('allocations'):
            _update_pipeline_run_metadata(
                run,
                flow_pod_state={**pod_state, 'allocations': {}},
            )
            remaining_budget = flow_budget
        else:
            used = 0
            allocs = pod_state.get('allocations') or {}
            for nid in in_flight:
                used += int(allocs.get(str(nid)) or 0)
            remaining_budget = max(0, flow_budget - used)
        slots = min(slots, remaining_budget)

    picked = []
    if slots > 0 and ready:
        if node_weights:
            picked = pick_ready_nodes_weighted(ready, node_weights, slots, wait_rounds=ready_wait)
        else:
            picked = sorted(ready, key=lambda nid: (-int(ready_wait.get(nid, 0)), nid))[:slots]
        new_caps = {}
        if fleet_reuse and flow_budget > 0 and picked:
            new_caps = allocate_flow_pod_caps(
                {nid: table_counts.get(nid, 0) for nid in picked},
                flow_budget,
                concurrency=concurrency,
            )
            picked = [nid for nid in picked if int(new_caps.get(nid) or 0) > 0]
        elif flow_enabled and flow_budget > 0 and picked:
            pod_state = (_get_pipeline_run_metadata(run).get('flow_pod_state') or {})
            used = 0
            allocs = pod_state.get('allocations') or {}
            for nid in in_flight:
                used += int(allocs.get(str(nid)) or 0)
            remaining_budget = max(0, flow_budget - used)
            new_caps = allocate_flow_pod_caps(
                {nid: table_counts.get(nid, 0) for nid in picked},
                remaining_budget,
                concurrency=concurrency,
            )
            picked = [nid for nid in picked if int(new_caps.get(nid) or 0) > 0]
        if picked and (fleet_reuse or flow_enabled) and flow_budget > 0:
            _store_flow_pod_allocations(
                run,
                {nid: new_caps[nid] for nid in picked},
                plan_name=plan_name,
                flow_budget=flow_budget,
            )
        for node_id in picked:
            ready_wait.pop(node_id, None)
            node = node_map.get(node_id)
            if node is None:
                failed.add(node_id)
                if first_failure is None:
                    first_failure = f'Pipeline node {node_id} is missing from the published graph.'
                continue
            try:
                _execute_pipeline_node(
                    run,
                    node,
                    user,
                    wait_for_session=False,
                    pod_cap=new_caps.get(node_id) if (flow_enabled or fleet_reuse) else None,
                )
            except _PipelineRunStopped:
                return 'done'
            except Exception as exc:
                failed.add(node_id)
                if first_failure is None:
                    first_failure = str(exc)
                newly_skipped = transitive_dependents({node_id}, children)
                newly_skipped -= completed | failed | set(in_flight)
                for sid in newly_skipped:
                    if sid in skipped:
                        continue
                    skipped.add(sid)
                    _set_pipeline_node_metadata(
                        run,
                        sid,
                        status='skipped',
                        error=f'Skipped — upstream node {node_id} failed: {exc}',
                        finished_at=timezone.now().isoformat(),
                    )
                continue
            in_flight.append(node_id)
            _refresh_pipeline_flow_fleet_lease(run.id)

    state.update({
        'completed': completed,
        'failed': failed,
        'skipped': skipped,
        'in_flight': in_flight,
        'ready_wait': ready_wait,
        'executed': executed,
        'first_failure': first_failure,
        'failed_seen_at': failed_seen,
    })
    terminal = completed | failed | skipped
    if len(terminal) >= len(exec_node_ids) and not in_flight:
        _save_orchestrator(run, state)
        if first_failure:
            _fail_pipeline_run(run, executed, RuntimeError(first_failure))
        else:
            _complete_nonblocking_pipeline_run(run, executed)
        return 'done'
    if not in_flight and len(terminal) < len(exec_node_ids):
        remaining = [nid for nid in exec_node_ids if nid not in terminal]
        for sid in remaining:
            skipped.add(sid)
            _set_pipeline_node_metadata(
                run,
                sid,
                status='skipped',
                error='Skipped — blocked by upstream failure or unmet dependency',
                finished_at=timezone.now().isoformat(),
            )
        state['skipped'] = skipped
        _save_orchestrator(run, state)
        if first_failure:
            _fail_pipeline_run(run, executed, RuntimeError(first_failure))
        else:
            _fail_pipeline_run(
                run,
                executed,
                RuntimeError('Pipeline execution stalled: remaining nodes are blocked by dependencies.'),
            )
        return 'done'
    _save_orchestrator(run, state)
    return 'continue'


def begin_nonblocking_pipeline_run(run_id: int) -> str:
    """Seed a run and dispatch its first ready nodes without waiting for them."""
    try:
        run = PipelineRun.objects.select_related('pipeline', 'pipeline__cmp').get(id=run_id)
    except PipelineRun.DoesNotExist:
        logger.info('PIPELINE_RUN_ABSENT | run=%s', run_id)
        return 'done'
    if _pipeline_run_was_deleted(run):
        return 'done'
    if _get_pipeline_run_metadata(run).get('failure_pending'):
        _reconcile_pipeline_run_status(run)
        return 'done'
    meta = _get_pipeline_run_metadata(run)
    if meta.get('orchestration_mode') != 'nonblocking':
        if _pipeline_run_is_resumable(run):
            _resume_pipeline_strict(run_id)
            return 'done'
        ctx = _prepare_pipeline_execution(run)
        if ctx is None:
            return 'done'
        _update_pipeline_run_metadata(
            run,
            orchestration_mode='nonblocking',
            orchestrator={
                'exec_node_ids': list(ctx['exec_node_ids']),
                'edge_pairs': [[a, b] for a, b in ctx['edge_pairs']],
                'completed': [],
                'failed': [],
                'skipped': [],
                'in_flight': [],
                'ready_wait': {},
                'executed': [],
                'first_failure': None,
            },
        )
    return advance_nonblocking_pipeline_run(run_id)


def _execute_pipeline_strict(run_id: int):
    try:
        run = PipelineRun.objects.select_related('pipeline', 'pipeline__cmp').get(id=run_id)
    except PipelineRun.DoesNotExist:
        logger.info('PIPELINE_RUN_ABSENT | run=%s', run_id)
        return
    if _pipeline_run_was_deleted(run):
        logger.info('PIPELINE_RUN_DELETED | run=%s', run_id)
        return
    if _get_pipeline_run_metadata(run).get('failure_pending'):
        _reconcile_pipeline_run_status(run)
        return
    if _pipeline_run_is_resumable(run):
        return _resume_pipeline_strict(run_id)

    executed: list = []
    ctx = _prepare_pipeline_execution(run)
    if ctx is None:
        return
    node_map = ctx['node_map']
    user = ctx['user']
    exec_node_ids = ctx['exec_node_ids']
    edge_pairs = ctx['edge_pairs']

    try:
        _execute_pipeline_dependency_dag(
            run,
            node_map,
            user,
            exec_node_ids,
            edge_pairs,
            executed,
        )
        if _pipeline_run_was_deleted(run):
            return

        run.status = 'completed'
        run.finished_at = timezone.now()
        run.metadata = _mark_unstarted_pipeline_nodes_skipped({
            **_get_pipeline_run_metadata(run),
            'executed_node_ids': executed,
            'current_node_id': None,
            'current_node_ids': [],
        })
        run.save(update_fields=['status', 'finished_at', 'metadata'])
        try:
            send_pipeline_flow_notification(run)
        except Exception:
            logger.exception('Pipeline flow notification hook failed for run %s', run.id)
        _teardown_pipeline_flow_fleet(run.id)
    except _PipelineRunStopped:
        logger.info('PIPELINE_RUN_STOPPED_DELETED | run=%s', run_id)
        return
    except Exception as exc:
        if _pipeline_run_was_deleted(run):
            logger.info('PIPELINE_RUN_STOPPED_DELETED | run=%s', run_id)
            return
        _fail_pipeline_run(run, executed, exc)


def _scheduled_dispatch_jitter_seconds() -> int:
    """Random delay (0..N seconds) so many accounts do not start in the same minute."""
    max_seconds = max(0, int(PIPELINE_SCHEDULE_JITTER_SECONDS))
    if max_seconds <= 0:
        return 0
    import random

    return random.randint(0, max_seconds)


def _dispatch_pipeline_run(run_id: int, *, countdown: Optional[int] = None) -> None:
    from apps.elttool.tasks.pipeline_tasks import (
        PIPELINE_RUN_CELERY_QUEUE,
        execute_pipeline_run,
    )

    kwargs = {
        'args': [run_id],
        'queue': PIPELINE_RUN_CELERY_QUEUE,
    }
    if countdown and int(countdown) > 0:
        kwargs['countdown'] = int(countdown)
    execute_pipeline_run.apply_async(**kwargs)


def _pipeline_run_in_progress(pipeline: Pipeline) -> bool:
    return PipelineRun.objects.filter(
        pipeline=pipeline,
        status__in=('running', 'queued'),
    ).exists()


def _validation_error_message(exc: serializers.ValidationError) -> str:
    detail = exc.detail
    message = detail[0] if isinstance(detail, list) else detail
    if isinstance(message, dict):
        message = next(iter(message.values()), str(detail))
    return str(message)


def _build_pipeline_run_validation(
    nodes,
    edges,
    *,
    pipeline: Optional[Pipeline] = None,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    level_summaries: list[dict] = []
    draft_node_ids = disconnected_batch_node_ids(nodes, edges)
    has_published_graph = bool(pipeline and pipeline.validated_graph)
    published_at = (
        pipeline.validated_at.isoformat() if pipeline and pipeline.validated_at else None
    )
    canvas_changed_since_publish = False
    if pipeline and pipeline.validated_graph:
        current_fp = pipeline_graph_fingerprint(nodes, edges)
        published_fp = (pipeline.validated_graph or {}).get('fingerprint')
        canvas_changed_since_publish = bool(published_fp and current_fp != published_fp)

    if not batch_nodes(nodes):
        errors.append('Pipeline has no batch nodes.')
        return {
            'valid': False,
            'errors': errors,
            'warnings': warnings,
            'levels': level_summaries,
            'max_children_per_node': PIPELINE_MAX_CHILDREN_PER_NODE,
            'max_nodes_per_level': PIPELINE_MAX_CHILDREN_PER_NODE,
            'draft_node_ids': draft_node_ids,
            'has_published_graph': has_published_graph,
            'published_at': published_at,
            'canvas_changed_since_publish': canvas_changed_since_publish,
        }

    connected_ok = True
    try:
        validate_connected_to_start(nodes, edges)
    except serializers.ValidationError as exc:
        connected_ok = False
        errors.append(_validation_error_message(exc))

    try:
        _validate_dag(nodes, edges)
    except serializers.ValidationError as exc:
        errors.append(_validation_error_message(exc))

    levels: list[list[int]] = []
    try:
        exec_ids, edge_pairs = execution_node_ids_and_edges(nodes, edges)
        levels = dag_execution_levels(exec_ids, edge_pairs)
    except (DagCycleError, ValueError) as exc:
        errors.append(str(exc))

    if levels:
        node_map = {n.id: n for n in nodes if is_batch_node(n)}
        for level_idx, level_nodes in enumerate(levels):
            names = []
            for node_id in level_nodes:
                node = node_map.get(node_id)
                if not node:
                    continue
                names.append(
                    node.batch.name if node.batch_id else (node.node_label or f'Node {node_id}')
                )
            level_summaries.append({
                'level': level_idx + 1,
                'node_count': len(level_nodes),
                'node_ids': level_nodes,
                'batch_names': names,
            })

    # Cap out-degree per node (max children), not DAG level width. A wide
    # level is fine when each parent fans out to at most N children.
    if connected_ok:
        node_map_all = {n.id: n for n in nodes}
        edge_pairs_all = [
            (e.from_node_id, e.to_node_id)
            for e in edges
            if e.from_node_id in node_map_all and e.to_node_id in node_map_all
        ]
        for node_id, count in node_children_count_violations(
            edge_pairs_all,
            PIPELINE_MAX_CHILDREN_PER_NODE,
            node_ids=set(node_map_all),
        ):
            node = node_map_all.get(node_id)
            if node and getattr(node, 'batch_id', None):
                label = (
                    node.batch.name
                    if getattr(node, 'batch', None) is not None
                    else (node.node_label or f'Node {node_id}')
                )
            elif node and is_start_node(node):
                label = node.node_label or 'Start'
            else:
                label = (getattr(node, 'node_label', None) if node else None) or f'Node {node_id}'
            errors.append(
                f'"{label}" has {count} outgoing connections '
                f'(max {PIPELINE_MAX_CHILDREN_PER_NODE} children per node).'
            )

    if pipeline and _pipeline_run_in_progress(pipeline):
        warnings.append(
            'A pipeline run is already queued or in progress. Wait for it to finish before starting another.'
        )

    if not errors and not has_published_graph:
        warnings.append(
            'No published flow yet. The next scheduled tick will auto-validate and publish '
            'this draft before running.'
        )

    if not errors and has_published_graph and canvas_changed_since_publish:
        warnings.append(
            'Canvas has unpublished changes. Schedule will keep running the last published flow '
            'until you validate again.'
        )

    if errors and has_published_graph:
        warnings.append(
            'Schedule will keep running the last published flow until you fix and validate again.'
        )

    if errors and not has_published_graph:
        warnings.append(
            'Schedule cannot run until validation passes. The next tick will record a failed '
            'run with these errors if they are still present.'
        )

    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'levels': level_summaries,
        'max_children_per_node': PIPELINE_MAX_CHILDREN_PER_NODE,
        # Back-compat for older clients; value is max children per node.
        'max_nodes_per_level': PIPELINE_MAX_CHILDREN_PER_NODE,
        'draft_node_ids': draft_node_ids,
        'has_published_graph': has_published_graph,
        'published_at': published_at,
        'canvas_changed_since_publish': canvas_changed_since_publish,
    }


def _publish_validated_graph(pipeline: Pipeline, nodes, edges, user: Optional[User] = None) -> None:
    pipeline.validated_graph = build_validated_graph_snapshot(nodes, edges)
    pipeline.validated_at = timezone.now()
    pipeline.validated_by = user
    pipeline.save(
        update_fields=['validated_graph', 'validated_at', 'validated_by', 'updated_at'],
    )


def _resolve_scheduled_run_user_id(
    pipeline: Pipeline,
    user_id: Optional[int] = None,
) -> Optional[int]:
    """Pick a user id for scheduled ticks (scheduler/shell may omit user_id)."""
    resolved_user_id = user_id or getattr(pipeline, 'created_by_id', None)
    if resolved_user_id is None and getattr(pipeline, 'cmp_id', None):
        fallback = (
            User.objects.filter(cmp_id=pipeline.cmp_id)
            .order_by('user_id')
            .values_list('user_id', flat=True)
            .first()
        )
        resolved_user_id = fallback
    return resolved_user_id


def _record_scheduled_validation_failure(
    pipeline: Pipeline,
    *,
    resolved_user_id: Optional[int],
    errors: list[str],
) -> PipelineRun:
    """Create a failed run so validation errors from a schedule tick are visible in the UI."""
    now = timezone.now()
    error_text = '; '.join(errors) if errors else 'Flow validation failed.'
    run = PipelineRun.objects.create(
        pipeline=pipeline,
        status='failed',
        started_at=now,
        finished_at=now,
        triggered_by_id=resolved_user_id,
        metadata={
            'requested_at': now.isoformat(),
            'source': 'scheduler',
            'graph_source': 'draft_auto_validate',
            'error': error_text,
            'validation_errors': list(errors),
        },
    )
    logger.warning(
        'PIPELINE_SCHEDULE_VALIDATION_FAILED | pipeline=%s | run=%s | errors=%s',
        pipeline.id,
        run.id,
        error_text,
    )
    return run


_SCHEDULE_PATCH_KEYS = frozenset({
    'schedule_type',
    'time_frequency',
    'schedule_config',
    'sync_start_date',
    'sync_end_date',
})


def _patch_touches_schedule(request_data) -> bool:
    if _SCHEDULE_PATCH_KEYS.intersection(request_data.keys()):
        return True
    if request_data.get('schedule_paused') is False:
        return True
    return False


def _validate_and_publish_draft_canvas(
    pipeline: Pipeline,
    *,
    user: Optional[User] = None,
) -> dict:
    """Validate the draft canvas; publish when valid. Returns validation payload."""
    ensure_start_node(pipeline)
    nodes = list(
        PipelineNode.objects.filter(pipeline=pipeline).select_related('batch')
    )
    edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
    payload = _build_pipeline_run_validation(nodes, edges, pipeline=pipeline)
    if payload['valid']:
        _publish_validated_graph(pipeline, nodes, edges, user=user)
        payload['has_published_graph'] = True
        payload['published_at'] = (
            pipeline.validated_at.isoformat() if pipeline.validated_at else None
        )
        payload['canvas_changed_since_publish'] = False
    return payload


def _auto_publish_draft_for_schedule(
    pipeline: Pipeline,
    *,
    resolved_user_id: Optional[int],
) -> bool:
    """
    Validate the draft canvas and publish it so a scheduled tick can run.

    Returns True when the pipeline has a usable published graph afterwards.
    On validation failure, records a failed PipelineRun (visible in the UI) and
    returns False.
    """
    user = None
    if resolved_user_id is not None:
        user = User.objects.filter(user_id=resolved_user_id).first()
    payload = _validate_and_publish_draft_canvas(pipeline, user=user)
    if not payload['valid']:
        _record_scheduled_validation_failure(
            pipeline,
            resolved_user_id=resolved_user_id,
            errors=payload.get('errors') or [],
        )
        return False

    logger.info(
        'PIPELINE_SCHEDULE_AUTO_PUBLISHED | pipeline=%s | user=%s',
        pipeline.id,
        resolved_user_id,
    )
    return True


_ORCHESTRATOR_HEARTBEAT_SECONDS = 20
_ORCHESTRATOR_STALE_SECONDS = 180
_RESUME_CLAIM_SECONDS = 90


def _touch_pipeline_orchestrator(run: Optional[PipelineRun]) -> None:
    """Refresh the heartbeat while a flow is actually being orchestrated."""
    if run is None:
        return
    from django.utils.dateparse import parse_datetime

    now = timezone.now()
    meta = _get_pipeline_run_metadata(run)
    previous = meta.get('orchestrator_heartbeat')
    parsed = parse_datetime(previous) if isinstance(previous, str) else None
    if parsed is not None:
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.utc)
        if (now - parsed).total_seconds() < _ORCHESTRATOR_HEARTBEAT_SECONDS:
            return
    _update_pipeline_run_metadata(run, orchestrator_heartbeat=now.isoformat())


def _pipeline_orchestrator_is_stale(run: PipelineRun) -> bool:
    """True when no live orchestrator has heartbeated this run recently."""
    from django.utils.dateparse import parse_datetime

    meta = _get_pipeline_run_metadata(run)
    raw = meta.get('orchestrator_heartbeat')
    parsed = parse_datetime(raw) if isinstance(raw, str) else None
    if parsed is None:
        parsed = run.started_at
    if parsed is None:
        return True
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return timezone.now() - parsed > timedelta(seconds=_ORCHESTRATOR_STALE_SECONDS)


def _claim_pipeline_resume(run_id: int) -> bool:
    """One resume at a time. A second caller inside the claim window waits."""
    from django.utils.dateparse import parse_datetime

    with transaction.atomic():
        try:
            run = PipelineRun.objects.select_for_update().get(id=run_id)
        except PipelineRun.DoesNotExist:
            return False
        if not _pipeline_run_is_resumable(run):
            return False
        meta = dict(run.metadata or {})
        claimed_raw = meta.get('orchestrator_claimed_at')
        claimed_at = parse_datetime(claimed_raw) if isinstance(claimed_raw, str) else None
        if claimed_at is not None:
            if timezone.is_naive(claimed_at):
                claimed_at = timezone.make_aware(claimed_at, timezone.utc)
            if timezone.now() - claimed_at < timedelta(seconds=_RESUME_CLAIM_SECONDS):
                return False
        meta['orchestrator_claimed_at'] = timezone.now().isoformat()
        run.metadata = meta
        run.save(update_fields=['metadata'])
        return True


def resume_stale_pipeline_runs(pipeline_id: Optional[int] = None) -> int:
    """Redispatch flows whose orchestrator died, so the next tick is not skipped forever."""
    filters = {'status': 'running'}
    if pipeline_id is not None:
        filters['pipeline_id'] = pipeline_id
    qs = PipelineRun.objects.filter(**filters).order_by('id')
    resumed = 0
    for run in qs:
        if not _pipeline_run_is_resumable(run):
            continue
        if not _pipeline_orchestrator_is_stale(run):
            continue
        if not _claim_pipeline_resume(run.id):
            continue
        logger.warning(
            "PIPELINE_RUN_RESUME_STALE | pipeline=%s | run=%s",
            run.pipeline_id,
            run.id,
        )
        _dispatch_pipeline_run(run.id)
        resumed += 1
    return resumed


def run_pipeline_job(pipeline_id: int, user_id: Optional[int] = None):
    # APScheduler threads have no Django request cycle, so CONN_HEALTH_CHECKS
    # does not re-run on its own. Recycle before the first ORM touch or a
    # server-side idle close surfaces as InterfaceError: connection already closed.
    close_old_connections()
    try:
        pipeline = Pipeline.objects.select_related('cmp', 'created_by').get(id=pipeline_id)
    except Pipeline.DoesNotExist:
        logger.info('PIPELINE_SCHEDULE_SKIPPED_DELETED | pipeline=%s', pipeline_id)
        return
    if _pipeline_run_in_progress(pipeline):
        resumed = resume_stale_pipeline_runs(pipeline_id=pipeline.id)
        if resumed:
            logger.warning(
                "PIPELINE_SCHEDULE_RESUMED_STALE | pipeline=%s | resumed=%s",
                pipeline_id,
                resumed,
            )
        else:
            logger.warning(
                "Skipping pipeline %s run - a run is already in progress.",
                pipeline_id,
            )
        return

    # log.user_id is NOT NULL — scheduler/shell callers may omit user_id.
    resolved_user_id = _resolve_scheduled_run_user_id(pipeline, user_id)

    # Unpublished draft: validate + publish on the tick so schedules still fire.
    # Validation failures become a failed PipelineRun (shown in the UI).
    if not pipeline.validated_graph:
        if not _auto_publish_draft_for_schedule(
            pipeline,
            resolved_user_id=resolved_user_id,
        ):
            return
        pipeline.refresh_from_db(
            fields=['validated_graph', 'validated_at', 'validated_by']
        )

    try:
        load_validated_graph_for_execution(pipeline)
    except ValidatedGraphResolutionError as exc:
        logger.warning(
            "Scheduled pipeline %s published graph invalid (%s); "
            "retrying with draft auto-validate.",
            pipeline_id,
            exc,
        )
        if not _auto_publish_draft_for_schedule(
            pipeline,
            resolved_user_id=resolved_user_id,
        ):
            return
        pipeline.refresh_from_db(
            fields=['validated_graph', 'validated_at', 'validated_by']
        )
        try:
            load_validated_graph_for_execution(pipeline)
        except ValidatedGraphResolutionError as retry_exc:
            _record_scheduled_validation_failure(
                pipeline,
                resolved_user_id=resolved_user_id,
                errors=[str(retry_exc)],
            )
            return

    company = getattr(pipeline, 'cmp', None)
    if company is not None:
        _reclaim_orphan_flow_fleets_for_company(company)
    requested_at = timezone.now().isoformat()
    metadata = {
        'requested_at': requested_at,
        'source': 'scheduler',
        'graph_source': 'validated',
    }
    with transaction.atomic():
        if company is not None:
            _company_flow_slot_xact_lock(company.cmp_id)
        at_cap = company is not None and (
            not company_has_flow_capacity(company)
            or _company_redis_fleet_count(company)
            >= _company_max_concurrent_flows(company)
        )
        if at_cap:
            metadata['waiting_for_capacity'] = True
            metadata['waiting_since'] = requested_at
            logger.info(
                "PIPELINE_RUN_SCHEDULED_QUEUED | pipeline=%s | company=%s | "
                "active=%s | limit=%s",
                pipeline_id,
                company.cmp_id,
                _company_active_flow_count(company),
                _company_max_concurrent_flows(company),
            )
        run = PipelineRun.objects.create(
            pipeline=pipeline,
            status='queued' if at_cap else 'running',
            triggered_by_id=resolved_user_id,
            metadata=metadata,
        )
    try:
        jitter = _scheduled_dispatch_jitter_seconds()
        if jitter:
            logger.info(
                "PIPELINE_SCHEDULE_JITTER | pipeline=%s | run=%s | countdown=%ss",
                pipeline_id,
                run.id,
                jitter,
            )
        _dispatch_pipeline_run(run.id, countdown=jitter or None)
    except Exception as exc:
        logger.exception("Failed to dispatch scheduled pipeline run %s", run.id)
        _fail_pipeline_run(run, [], exc)


def _sync_pipeline_scheduler(pipeline: Pipeline):
    from apps.elttool.scheduler_client import SchedulerClient

    job_id = f"pipeline_{pipeline.id}"
    if pipeline.status == 'paused' or pipeline.schedule_paused:
        try:
            SchedulerClient.remove_job(job_id)
        except Exception:
            pass
        return

    nodes = list(PipelineNode.objects.filter(pipeline=pipeline))
    edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
    if not nodes:
        try:
            SchedulerClient.remove_job(job_id)
        except Exception:
            pass
        return

    roots = _pipeline_root_node_ids(nodes, edges)
    if not roots:
        try:
            SchedulerClient.remove_job(job_id)
        except Exception:
            pass
        return

    # Allow schedules without a published graph: the next tick auto-validates
    # the draft (or records a failed run with validation errors).

    trigger_type, trigger_args = _parse_pipeline_schedule(pipeline)
    if trigger_type is None:
        try:
            SchedulerClient.remove_job(job_id)
        except Exception:
            pass
        return

    tz_name = _pipeline_timezone_name(pipeline)
    if pipeline.sync_start_date:
        trigger_args['start_date'] = _coerce_pipeline_datetime(
            pipeline.sync_start_date, tz_name
        ).isoformat()
    if getattr(pipeline, 'sync_end_date', None):
        trigger_args['end_date'] = _coerce_pipeline_datetime(
            pipeline.sync_end_date, tz_name
        ).isoformat()
    SchedulerClient.add_job(
        job_id=job_id,
        func_path='apps.elttool.v1.pipeline_views.run_pipeline_job',
        trigger_type=trigger_type,
        trigger_args=trigger_args,
        args=(pipeline.id,),
        kwargs={'user_id': pipeline.created_by_id},
        replace_existing=True,
        max_instances=1,
    )


class PipelineConnectionsView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def get(self, request):
        connections = (
            Connection.objects.filter(cmp=request.user.cmp)
            .exclude(status='D')
            .select_related('src_config')
            .order_by('-updated_at')
        )
        from apps.elttool.services.pipeline_batch_guards import (
            build_batch_pipeline_usage_map,
        )

        payload = []
        for conn in connections:
            batches = list(
                MigrationBatch.objects.filter(connection=conn)
                .exclude(name='Unassigned')
                .annotate(table_count=Count('tables'))
                .order_by('created_at')
            )
            usage_map = build_batch_pipeline_usage_map(batches)
            connection_name = (
                conn.src_config.name
                if conn.src_config_id and conn.src_config
                else f'Connection {conn.connection_id}'
            )
            payload.append({
                'connection_id': conn.connection_id,
                'connection_name': connection_name,
                'batches': [
                    {
                        'id': b.id,
                        'name': b.name,
                        'table_count': b.table_count,
                        'execution_order': b.execution_order,
                        'time_frequency': b.time_frequency,
                        'in_pipeline': b.id in usage_map,
                        'pipeline_id': (usage_map.get(b.id) or {}).get('pipeline_id'),
                        'pipeline_name': (usage_map.get(b.id) or {}).get('pipeline_name'),
                    }
                    for b in batches
                ],
            })
        return Response({'connections': payload})


class PipelineListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def get(self, request):
        pipelines = (
            Pipeline.objects.filter(cmp=request.user.cmp)
            .prefetch_related(
                Prefetch(
                    'nodes',
                    queryset=PipelineNode.objects.select_related('batch'),
                ),
                'edges',
            )
            .order_by('-updated_at')
        )
        pipeline_list = list(pipelines)
        for pipeline in pipeline_list:
            if not get_start_node(pipeline.nodes.all()):
                ensure_start_node(pipeline)
        _attach_pipeline_latest_run_statuses(pipeline_list)
        return Response({'pipelines': PipelineSerializer(pipeline_list, many=True).data})

    def post(self, request):
        name = (request.data.get('name') or '').strip() or f"Pipeline {int(datetime.now().timestamp())}"
        schedule_config = request.data.get('schedule_config') or {}
        # New flows do not auto-enable recurring runs; user configures schedule then resumes it.
        if 'schedule_paused' in request.data:
            schedule_paused = bool(request.data.get('schedule_paused'))
        else:
            schedule_paused = True
        schedule_type = request.data.get('schedule_type') or 'manual'
        tz_name = _normalize_timezone_name(schedule_config.get('timezone') or 'UTC')
        try:
            sync_start_date = _coerce_pipeline_datetime(
                request.data.get('sync_start_date'), tz_name
            )
            sync_end_date = _coerce_pipeline_datetime(
                request.data.get('sync_end_date'), tz_name
            )
        except ValueError as exc:
            return Response(
                {'error': f'Invalid schedule date: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            pipeline = Pipeline.objects.create(
                cmp=request.user.cmp,
                name=name,
                schedule_paused=schedule_paused,
                schedule_type=schedule_type,
                time_frequency=str(request.data.get('time_frequency') or '15'),
                schedule_config=schedule_config,
                sync_start_date=sync_start_date,
                sync_end_date=sync_end_date,
                created_by=request.user,
            )
        except IntegrityError:
            return Response(
                {'error': f"A pipeline named '{name}' already exists."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            _sync_pipeline_scheduler(pipeline)
        except Exception:
            logger.exception(
                "PIPELINE_SCHEDULER_SYNC_FAILED | pipeline=%s",
                pipeline.id,
            )
        ensure_start_node(pipeline)
        pipeline.refresh_from_db()
        return Response(PipelineSerializer(pipeline).data, status=status.HTTP_201_CREATED)


def _stop_loads_for_deleted_pipeline(pipeline: Pipeline) -> None:
    """Stop this flow only. Other flows and organisations keep their loads."""
    from django.db.models import Q
    from django.utils import timezone as dj_timezone

    from apps.elttool.models import Log, MigrationRecord, SourceTableSelection

    runs = list(PipelineRun.objects.filter(pipeline=pipeline))
    run_ids = [run.id for run in runs]
    session_ids: list[int] = []
    for run in runs:
        for entry in ((run.metadata or {}).get('nodes') or {}).values():
            if not isinstance(entry, dict):
                continue
            sid = entry.get('migration_session_id')
            if sid:
                session_ids.append(int(sid))

    job_filter = Q()
    for run_id in run_ids:
        job_filter |= Q(job_name__startswith=f'pipeline_run_{run_id}')
    if session_ids:
        job_filter |= Q(migration_session_id__in=session_ids)

    active_rows = []
    if job_filter:
        active_rows = list(
            MigrationRecord.objects.filter(
                job_filter,
                status__in=_ACTIVE_LOAD_STATUSES,
            ).only('id', 'connection_id', 'table_name', 'task_id')
        )
    if active_rows:
        MigrationRecord.objects.filter(
            id__in=[row.id for row in active_rows],
        ).update(status='deleted', updated_at=dj_timezone.now())

    log_filter = Q()
    for run_id in run_ids:
        log_filter |= Q(job_name__startswith=f'pipeline_run_{run_id}')
    if session_ids:
        log_filter |= Q(session__in=session_ids)
    if log_filter:
        Log.objects.filter(log_filter, status='I').update(
            status='D',
            message=_FLOW_DELETED_MESSAGE,
        )

    touched = {
        (row.connection_id, row.table_name)
        for row in active_rows
        if row.table_name
    }
    for connection_id, table_name in touched:
        still_active = MigrationRecord.objects.filter(
            connection_id=connection_id,
            table_name=table_name,
            status__in=_ACTIVE_LOAD_STATUSES,
        ).exists()
        if still_active:
            continue
        SourceTableSelection.objects.filter(
            connection_id=connection_id,
            table=table_name,
            status__in=_ACTIVE_LOAD_STATUSES,
        ).update(status=None)

    task_ids = sorted({row.task_id for row in active_rows if row.task_id})
    if task_ids:
        try:
            from apps.elttool.services.celery_inspect_service import get_migration_celery_app

            get_migration_celery_app().control.revoke(
                task_ids,
                terminate=True,
                signal='SIGTERM',
            )
        except Exception:
            logger.exception('PIPELINE_DELETE_REVOKE_FAILED | pipeline=%s', pipeline.id)

    for run in runs:
        run.status = 'deleted'
        run.finished_at = dj_timezone.now()
        run.save(update_fields=['status', 'finished_at'])
        _teardown_pipeline_flow_fleet(run.id)
    logger.info(
        'PIPELINE_DELETE_LOADS_STOPPED | pipeline=%s | runs=%s | records=%s',
        pipeline.id,
        run_ids,
        len(active_rows),
    )


class PipelineDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def patch(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        if _patch_touches_schedule(request.data):
            payload = _validate_and_publish_draft_canvas(
                pipeline,
                user=request.user,
            )
            if not payload['valid']:
                return Response(payload, status=status.HTTP_400_BAD_REQUEST)
        if 'name' in request.data:
            pipeline.name = request.data.get('name') or pipeline.name
        if 'status' in request.data and request.data.get('status') in {'active', 'paused'}:
            pipeline.status = request.data.get('status')
        if 'schedule_paused' in request.data:
            pipeline.schedule_paused = bool(request.data.get('schedule_paused'))
        if 'schedule_type' in request.data:
            pipeline.schedule_type = request.data.get('schedule_type') or pipeline.schedule_type
        if 'time_frequency' in request.data:
            pipeline.time_frequency = str(request.data.get('time_frequency') or pipeline.time_frequency)
        if 'schedule_config' in request.data:
            pipeline.schedule_config = request.data.get('schedule_config') or {}
        tz_name = _pipeline_timezone_name(pipeline)
        try:
            if 'sync_start_date' in request.data:
                pipeline.sync_start_date = _coerce_pipeline_datetime(
                    request.data.get('sync_start_date'), tz_name
                )
            if 'sync_end_date' in request.data:
                pipeline.sync_end_date = _coerce_pipeline_datetime(
                    request.data.get('sync_end_date'), tz_name
                )
        except ValueError as exc:
            return Response(
                {'error': f'Invalid schedule date: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            _apply_pipeline_notification_patch(pipeline, request.data)
        except serializers.ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        pipeline.save(
            update_fields=[
                'name', 'status', 'schedule_paused', 'schedule_type', 'time_frequency',
                'schedule_config', 'sync_start_date', 'sync_end_date', 'notify_on_flow_finish',
                'notification_email_group_ids', 'notification_emails', 'updated_at',
            ],
        )
        try:
            _sync_pipeline_scheduler(pipeline)
        except Exception:
            logger.exception(
                "PIPELINE_SCHEDULER_SYNC_FAILED | pipeline=%s",
                pipeline.id,
            )
        return Response(PipelineSerializer(pipeline).data)

    def delete(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        try:
            from apps.elttool.scheduler_client import SchedulerClient
            SchedulerClient.remove_job(f"pipeline_{pipeline.id}")
        except Exception:
            pass
        _stop_loads_for_deleted_pipeline(pipeline)
        pipeline.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PipelineNodesView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def post(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        ensure_start_node(pipeline)
        connection = _get_connection_for_user(request, request.data.get('connection_id'))
        batch = get_object_or_404(MigrationBatch, id=request.data.get('batch_id'), connection=connection)
        existing = (
            PipelineNode.objects.filter(batch=batch, node_kind='batch')
            .select_related('pipeline')
            .first()
        )
        if existing:
            if existing.pipeline_id == pipeline.id:
                return Response(
                    {'error': 'This batch is already in the pipeline.'},
                    status=status.HTTP_409_CONFLICT,
                )
            other = existing.pipeline
            other_name = (other.name if other else None) or f'Pipeline {existing.pipeline_id}'
            return Response(
                {
                    'error': (
                        f'This batch is already used in pipeline "{other_name}". '
                        'Remove it from that flow before adding it here.'
                    ),
                    'pipeline_id': existing.pipeline_id,
                    'pipeline_name': other_name,
                },
                status=status.HTTP_409_CONFLICT,
            )
        order_index = PipelineNode.objects.filter(pipeline=pipeline, node_kind='batch').count() + 1
        insert_between = request.data.get('insert_between') or {}
        from_node_id = insert_between.get('from_node_id')
        to_node_id = insert_between.get('to_node_id')
        try:
            node = PipelineNode.objects.create(
                pipeline=pipeline,
                node_kind='batch',
                connection=connection,
                batch=batch,
                node_label=batch.name,
                x=int(request.data.get('x') or 0),
                y=int(request.data.get('y') or 0),
                order_index=order_index,
                execution_order=batch.execution_order,
            )
        except IntegrityError:
            return Response(
                {
                    'error': (
                        'This batch is already used in another pipeline. '
                        'Remove it from that flow before adding it here.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        if from_node_id and to_node_id:
            removed = PipelineEdge.objects.filter(
                pipeline=pipeline,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
            ).delete()
            if removed[0]:
                PipelineEdge.objects.get_or_create(
                    pipeline=pipeline,
                    from_node_id=from_node_id,
                    to_node=node,
                )
                PipelineEdge.objects.get_or_create(
                    pipeline=pipeline,
                    from_node=node,
                    to_node_id=to_node_id,
                )
            else:
                start = get_start_node(PipelineNode.objects.filter(pipeline=pipeline))
                if start:
                    PipelineEdge.objects.get_or_create(
                        pipeline=pipeline,
                        from_node=start,
                        to_node=node,
                    )

        try:
            edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
            _validate_dag(list(PipelineNode.objects.filter(pipeline=pipeline)), edges)
        except serializers.ValidationError as exc:
            node.delete()
            return Response({'error': str(exc.detail if hasattr(exc, 'detail') else exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            _remove_batch_job(batch)
        except Exception:
            pass

        return Response(PipelineNodeSerializer(node).data, status=status.HTTP_201_CREATED)


class PipelineNodeDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def patch(self, request, pipeline_id, node_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        node = get_object_or_404(PipelineNode, id=node_id, pipeline=pipeline)

        schedule_keys = {
            'schedule_type', 'time_frequency', 'schedule_config', 'sync_start_date',
        }
        if schedule_keys.intersection(request.data.keys()):
            return Response(
                {
                    'error': (
                        'Pipeline schedule is configured on the Start node only. '
                        'Select Start in the canvas to set timing.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if PIPELINE_NOTIFICATION_KEYS.intersection(request.data.keys()):
            return Response(
                {
                    'error': (
                        'Pipeline notification recipients are configured on the Start node only. '
                        'Select Start in the canvas to set email alerts.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if 'notify_on_completion' in request.data:
            if is_start_node(node):
                return Response(
                    {'error': 'The Start node cannot enable batch completion notifications.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            node.notify_on_completion = bool(request.data.get('notify_on_completion'))

        for key in ['x', 'y', 'order_index', 'node_label', 'execution_order']:
            if key in request.data:
                setattr(node, key, request.data.get(key))
        node.save()
        return Response(PipelineNodeSerializer(node).data)

    def delete(self, request, pipeline_id, node_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        node = get_object_or_404(PipelineNode, id=node_id, pipeline=pipeline)
        if is_start_node(node):
            return Response({'error': 'The Start node cannot be deleted.'}, status=status.HTTP_400_BAD_REQUEST)
        batch = node.batch
        try:
            bridge_edges_on_delete(
                pipeline, node, max_children_per_node=PIPELINE_MAX_CHILDREN_PER_NODE
            )
        except serializers.ValidationError as exc:
            return Response({'error': str(exc.detail if hasattr(exc, 'detail') else exc)}, status=status.HTTP_400_BAD_REQUEST)
        PipelineEdge.objects.filter(pipeline=pipeline, from_node=node).delete()
        PipelineEdge.objects.filter(pipeline=pipeline, to_node=node).delete()
        node.delete()
        if batch and not PipelineNode.objects.filter(batch=batch).exists():
            try:
                _remove_batch_job(batch)
            except Exception:
                pass
        try:
            edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
            _validate_dag(list(PipelineNode.objects.filter(pipeline=pipeline)), edges)
        except serializers.ValidationError:
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)


class PipelineEdgesView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def post(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        from_node = get_object_or_404(PipelineNode, id=request.data.get('from_node_id'), pipeline=pipeline)
        to_node = get_object_or_404(PipelineNode, id=request.data.get('to_node_id'), pipeline=pipeline)
        if from_node.id == to_node.id:
            return Response({'error': 'Self dependency is not allowed.'}, status=status.HTTP_400_BAD_REQUEST)
        existing = PipelineEdge.objects.filter(
            pipeline=pipeline,
            from_node=from_node,
            to_node=to_node,
        ).first()
        if existing is None:
            outgoing_count = PipelineEdge.objects.filter(
                pipeline=pipeline, from_node=from_node
            ).count()
            if outgoing_count >= PIPELINE_MAX_CHILDREN_PER_NODE:
                return Response(
                    {
                        'error': (
                            f'Each node can have at most {PIPELINE_MAX_CHILDREN_PER_NODE} '
                            'outgoing connections.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        edge, _ = PipelineEdge.objects.get_or_create(
            pipeline=pipeline,
            from_node=from_node,
            to_node=to_node,
        )
        edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
        try:
            _validate_dag(list(PipelineNode.objects.filter(pipeline=pipeline)), edges)
        except serializers.ValidationError as exc:
            edge.delete()
            return Response({'error': str(exc.detail[0] if isinstance(exc.detail, list) else exc.detail)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PipelineEdgeSerializer(edge).data, status=status.HTTP_201_CREATED)


class PipelineEdgeDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def delete(self, request, pipeline_id, edge_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        edge = get_object_or_404(PipelineEdge, id=edge_id, pipeline=pipeline)
        edge.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PipelineRunListView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def get(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        runs = (
            PipelineRun.objects.filter(pipeline=pipeline)
            .order_by('-started_at')[:25]
        )
        payload = []
        for index, run in enumerate(runs):
            payload.append(
                _build_pipeline_run_summary(
                    run,
                    reconcile_running=(index == 0 and run.status == 'running'),
                )
            )
        return Response({'runs': payload})


class PipelineRunDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def get(self, request, pipeline_id, run_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        run = get_object_or_404(PipelineRun, id=run_id, pipeline=pipeline)
        return Response(_build_pipeline_run_detail(run, include_migration_status=True))


class PipelineValidateView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def post(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        ensure_start_node(pipeline)
        nodes = list(PipelineNode.objects.filter(pipeline=pipeline).select_related('batch'))
        edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
        payload = _build_pipeline_run_validation(nodes, edges, pipeline=pipeline)
        if payload['valid']:
            _publish_validated_graph(pipeline, nodes, edges, user=request.user)
            try:
                _sync_pipeline_scheduler(pipeline)
            except Exception:
                logger.exception(
                    'Failed to sync scheduler after pipeline validate | pipeline=%s',
                    pipeline.id,
                )
            payload['has_published_graph'] = True
            payload['published_at'] = pipeline.validated_at.isoformat()
            payload['canvas_changed_since_publish'] = False
        status_code = status.HTTP_200_OK if payload['valid'] else status.HTTP_400_BAD_REQUEST
        return Response(payload, status=status_code)


class PipelineResetView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def post(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        if not pipeline.validated_graph:
            return Response(
                {'error': 'No published flow to restore. Validate the pipeline first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                restore_canvas_to_published_graph(pipeline)
        except serializers.ValidationError as exc:
            detail = exc.detail
            message = detail[0] if isinstance(detail, list) else str(detail)
            return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)

        ensure_start_node(pipeline)
        nodes = list(PipelineNode.objects.filter(pipeline=pipeline).select_related('batch'))
        edges = list(PipelineEdge.objects.filter(pipeline=pipeline))
        payload = _build_pipeline_run_validation(nodes, edges, pipeline=pipeline)
        payload['canvas_changed_since_publish'] = False
        return Response(payload, status=status.HTTP_200_OK)


class PipelineRunNowView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def post(self, request, pipeline_id):
        pipeline = _get_pipeline_for_user(request, pipeline_id)
        ensure_start_node(pipeline)

        if not pipeline.validated_graph:
            return Response(
                {
                    'error': (
                        'No published pipeline flow. Validate the pipeline before running.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            load_validated_graph_for_execution(pipeline)
        except ValidatedGraphResolutionError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if _pipeline_run_in_progress(pipeline):
            return Response(
                {
                    'error': (
                        'A pipeline run is already queued or in progress. '
                        'Please wait for it to complete before starting another.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        company = getattr(pipeline, 'cmp', None)
        # Drop ghost fleets from finished runs before the plan-cap check so a
        # false Redis company_job_cap does not appear mid-run after Run Now succeeds.
        if company is not None:
            _reclaim_orphan_flow_fleets_for_company(company)
        with transaction.atomic():
            if company is not None:
                _company_flow_slot_xact_lock(company.cmp_id)
                limit = _company_max_concurrent_flows(company)
                active = _company_active_flow_count(company)
                redis_fleets = _company_redis_fleet_count(company)
                blocked_by = None
                if active >= limit:
                    blocked_by = 'flow_slot'
                elif redis_fleets >= limit:
                    # Real concurrent worker fleets still held (e.g. another
                    # org run's pods). Surface the same warning at trigger time
                    # instead of failing tables later with company_job_cap.
                    blocked_by = 'worker_fleet'
                    active = max(active, redis_fleets)
                if blocked_by is not None:
                    plan_label = _company_plan_name(company) or 'current'
                    message = (
                        f'Your {plan_label} plan allows {limit} concurrent '
                        f'flow{"s" if limit != 1 else ""}. '
                        f'{active} {"are" if active != 1 else "is"} currently running. '
                        f'Wait for one to finish before starting another.'
                    )
                    return Response(
                        {
                            'warning': message,
                            'code': 'plan_flow_cap',
                            'limit': limit,
                            'active': active,
                            'blocked_by': blocked_by,
                            # Keep error for older clients that only read this field.
                            'error': message,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

            run = PipelineRun.objects.create(
                pipeline=pipeline,
                status='running',
                triggered_by=request.user,
                metadata={
                    'requested_at': timezone.now().isoformat(),
                    'graph_source': 'validated',
                },
            )
        _dispatch_pipeline_run(run.id)
        return Response(
            {
                'message': 'Pipeline execution started.',
                'pipeline_run_id': run.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )
