"""
Batch CRUD + scheduler endpoints for the Schema Page Batch Groups UI.

URL patterns (registered in urls.py):
  GET  POST   /connection/<id>/batches/
  GET  PATCH  DELETE  /connection/<id>/batches/<bid>/
  POST        /connection/<id>/batches/<bid>/tables/
  DELETE      /connection/<id>/batches/<bid>/tables/<tname>/
  POST        /connection/<id>/batches/<bid>/run/
  POST        /connection/<id>/batches/<bid>/toggle/
"""

import logging
from datetime import datetime

import pytz
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.elttool.models import (
    BatchTable,
    Connection,
    MigrationBatch,
    PipelineNode,
    ScheduledJob,
    SourceTableSelection,
)
from apps.elttool.services.pipeline_batch_guards import (
    BATCH_IN_PIPELINE_DELETE_MSG,
    batch_is_in_pipeline,
    build_batch_pipeline_usage_map,
)
from apps.elttool.v1.permissions import IsAdministrator, IsAnalystOrAdmin

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #

class BatchTableSerializer(serializers.ModelSerializer):
    last_synced = serializers.SerializerMethodField()

    class Meta:
        model = BatchTable
        fields = ['id', 'table_name', 'sequence', 'last_synced']

    def get_last_synced(self, obj):
        try:
            sts = SourceTableSelection.objects.filter(
                connection=obj.connection, table=obj.table_name
            ).only('last_synced').first()
            return sts.last_synced if sts else None
        except Exception:
            return None


class MigrationBatchSerializer(serializers.ModelSerializer):
    tables = BatchTableSerializer(many=True, read_only=True)
    table_count = serializers.SerializerMethodField()
    in_pipeline = serializers.SerializerMethodField()
    pipeline_id = serializers.SerializerMethodField()
    pipeline_name = serializers.SerializerMethodField()

    class Meta:
        model = MigrationBatch
        fields = [
            'id',
            'name',
            'time_frequency',
            'execution_order',
            'schedule_type',
            'schedule_config',
            'sync_start_date',
            'status',
            'created_at',
            'updated_at',
            'tables',
            'table_count',
            'in_pipeline',
            'pipeline_id',
            'pipeline_name',
        ]
        read_only_fields = [
            'id',
            'created_at',
            'updated_at',
            'tables',
            'table_count',
            'in_pipeline',
            'pipeline_id',
            'pipeline_name',
        ]

    def _pipeline_usage(self, obj):
        usage_map = self.context.get('pipeline_usage') or {}
        return usage_map.get(obj.id)

    def get_table_count(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'tables' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['tables'])
        return obj.tables.count()

    def get_in_pipeline(self, obj):
        return bool(self._pipeline_usage(obj))

    def get_pipeline_id(self, obj):
        usage = self._pipeline_usage(obj)
        return usage.get('pipeline_id') if usage else None

    def get_pipeline_name(self, obj):
        usage = self._pipeline_usage(obj)
        return usage.get('pipeline_name') if usage else None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _get_batch_job_id(connection_id: int, batch_id: int) -> str:
    """Stable scheduler job-id for a batch. Never includes the batch name."""
    return f"conn_{connection_id}_batch_{batch_id}"


PIPELINE_REQUIRED_MSG = (
    "Add this batch to a pipeline on Scheduling to run or schedule it."
)


def _schedule_batch(batch: MigrationBatch, request) -> str:
    """
    Per-batch APScheduler is disabled — scheduling is pipeline-only (Snowflake-style).
    Removes any legacy batch job if present.
    """
    _remove_batch_job(batch)
    return f"Batch {batch.id}: scheduling is configured on the Scheduling pipeline."


def _remove_batch_job(batch: MigrationBatch) -> None:
    """Remove the APScheduler job and ScheduledJob DB row for a batch."""
    from apps.elttool.scheduler_client import SchedulerClient

    job_id = _get_batch_job_id(batch.connection_id, batch.id)
    try:
        SchedulerClient.remove_job(job_id)
    except Exception as e:
        logger.warning("batch_views: could not remove scheduler job %s: %s", job_id, e)

    ScheduledJob.objects.filter(batch=batch).delete()


def _next_batch_name(connection: Connection) -> str:
    """Return 'Batch N' where N is the next available integer."""
    existing = set(
        MigrationBatch.objects.filter(connection=connection).values_list('name', flat=True)
    )
    n = 1
    while f"Batch {n}" in existing:
        n += 1
    return f"Batch {n}"


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

def _get_connection_for_user(request, connection_id):
    """404 if connection missing or belongs to another company."""
    return get_object_or_404(
        Connection.objects.filter(cmp=request.user.cmp),
        connection_id=connection_id,
    )


class BatchListCreateView(APIView):
    """
    GET  /connection/<connection_id>/batches/
         Returns all batches for the connection plus a list of unassigned tables.

    POST /connection/<connection_id>/batches/
         Creates a new batch.  Body: { name, time_frequency, execution_order,
         schedule_type?, schedule_config?, sync_start_date? }
    """
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def get(self, request, connection_id):
        connection = _get_connection_for_user(request, connection_id)

        # Real schedule batches only — the synthetic "Unassigned" bucket (used
        # by legacy update-selection) is folded into unassigned_tables below.
        batches = (
            MigrationBatch.objects
            .filter(connection=connection)
            .exclude(name='Unassigned')
            .prefetch_related('tables')
            .order_by('created_at')
        )
        batches_list = list(batches)
        pipeline_usage = build_batch_pipeline_usage_map(batches_list)
        serializer = MigrationBatchSerializer(
            batches_list,
            many=True,
            context={'pipeline_usage': pipeline_usage},
        )

        assigned_table_names = set(
            BatchTable.objects.filter(connection=connection).values_list('table_name', flat=True)
        )
        unassigned = list(
            SourceTableSelection.objects
            .filter(connection=connection, selected=True)
            .exclude(table__in=assigned_table_names)
            .order_by('sequence')
            .values('table', 'last_synced', 'sequence')
        )

        # Tables stored in the paused "Unassigned" batch (backward-compat path)
        unassigned_batch = MigrationBatch.objects.filter(
            connection=connection, name='Unassigned'
        ).prefetch_related('tables').first()
        if unassigned_batch:
            for bt in unassigned_batch.tables.all():
                sts = SourceTableSelection.objects.filter(
                    connection=connection, table=bt.table_name
                ).only('last_synced').first()
                unassigned.append({
                    'table': bt.table_name,
                    'last_synced': sts.last_synced if sts else None,
                    'sequence': bt.sequence,
                })

        return Response({
            'batches': serializer.data,
            'unassigned_tables': [
                {'table_name': r['table'], 'last_synced': r['last_synced'], 'sequence': r['sequence']}
                for r in unassigned
            ],
        })

    def post(self, request, connection_id):
        connection = _get_connection_for_user(request, connection_id)

        default_name = _next_batch_name(connection)
        data = request.data.copy()
        data.setdefault('name', default_name)
        data.setdefault('time_frequency', connection.time_frequency or '15')
        data.setdefault('execution_order', connection.execution_order or 'parallel')
        data.setdefault('schedule_type', 'interval')
        data.setdefault('schedule_config', {})

        serializer = MigrationBatchSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        try:
            batch = serializer.save(connection=connection)
        except IntegrityError:
            return Response(
                {'error': f"A batch named '{data['name']}' already exists for this connection."},
                status=status.HTTP_409_CONFLICT,
            )

        # Pipeline-only scheduling: clear any legacy per-batch scheduler job.
        try:
            _remove_batch_job(batch)
        except Exception as e:
            logger.warning("batch_views: could not clear legacy job on create: %s", e)

        return Response(MigrationBatchSerializer(
            batch,
            context={'pipeline_usage': build_batch_pipeline_usage_map([batch])},
        ).data, status=status.HTTP_201_CREATED)


class BatchDetailView(APIView):
    """
    GET    /connection/<connection_id>/batches/<batch_id>/
    PATCH  /connection/<connection_id>/batches/<batch_id>/
           Body: { name?, time_frequency?, execution_order?, sync_start_date? }
           Renaming does NOT change the scheduler job-id.
    DELETE /connection/<connection_id>/batches/<batch_id>/
           Removes the batch; all its BatchTable rows are deleted (tables become
           Unassigned — SourceTableSelection.selected stays True).
    """
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def _get_batch(self, request, connection_id, batch_id):
        connection = _get_connection_for_user(request, connection_id)
        batch = get_object_or_404(MigrationBatch, id=batch_id, connection=connection)
        return connection, batch

    def get(self, request, connection_id, batch_id):
        _, batch = self._get_batch(request, connection_id, batch_id)
        return Response(MigrationBatchSerializer(
            batch,
            context={'pipeline_usage': build_batch_pipeline_usage_map([batch])},
        ).data)

    def patch(self, request, connection_id, batch_id):
        connection, batch = self._get_batch(request, connection_id, batch_id)

        serializer = MigrationBatchSerializer(batch, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            batch = serializer.save()
        except IntegrityError:
            return Response(
                {'error': f"A batch named '{request.data.get('name')}' already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        if 'name' in request.data and request.data.get('name'):
            PipelineNode.objects.filter(
                batch=batch,
                node_kind='batch',
            ).update(node_label=request.data.get('name'))

        # Re-schedule if frequency changed
        schedule_keys = {
            'time_frequency', 'execution_order', 'schedule_type',
            'schedule_config', 'sync_start_date', 'status',
        }
        if schedule_keys.intersection(request.data.keys()):
            try:
                _remove_batch_job(batch)
            except Exception as e:
                logger.warning("batch_views: could not clear legacy job on patch: %s", e)

        return Response(MigrationBatchSerializer(
            batch,
            context={'pipeline_usage': build_batch_pipeline_usage_map([batch])},
        ).data)

    def delete(self, request, connection_id, batch_id):
        connection, batch = self._get_batch(request, connection_id, batch_id)

        if batch_is_in_pipeline(batch):
            return Response({'error': BATCH_IN_PIPELINE_DELETE_MSG}, status=status.HTTP_400_BAD_REQUEST)

        table_names = list(batch.tables.values_list('table_name', flat=True))

        with transaction.atomic():
            _remove_batch_job(batch)
            batch.delete()  # CASCADE removes BatchTable rows

        return Response(
            {
                'message': f"Batch deleted. {len(table_names)} table(s) returned to Unassigned.",
                'returned_tables': table_names,
            },
            status=status.HTTP_200_OK,
        )


class BatchTablesView(APIView):
    """
    POST /connection/<connection_id>/batches/<batch_id>/tables/
    Body: { "tables": ["table_a", "table_b"] }

    Moves the listed tables into this batch.  If a table already belongs to
    another batch on this connection, a 409 is returned with a clear list of
    conflicts before any changes are made.
    """
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def post(self, request, connection_id, batch_id):
        connection = _get_connection_for_user(request, connection_id)
        batch = get_object_or_404(MigrationBatch, id=batch_id, connection=connection)

        table_names = request.data.get('tables', [])
        if not isinstance(table_names, list) or not table_names:
            return Response({'error': "'tables' must be a non-empty list."}, status=400)

        # Tables locked to a *different* real batch (not Unassigned) — block
        # with 409 so the UI can prompt.  Moving from "Unassigned" is always allowed.
        locked = BatchTable.objects.filter(
            connection=connection,
            table_name__in=table_names,
        ).exclude(batch=batch).exclude(batch__name='Unassigned')

        conflicts = [
            {'table_name': row['table_name'], 'batch_name': row['batch__name']}
            for row in locked.values('table_name', 'batch__name')
        ]
        if conflicts:
            return Response(
                {
                    'error': 'Some tables are already assigned to another batch.',
                    'conflicts': conflicts,
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Move: drop rows on other batches (including Unassigned), then attach here.
        with transaction.atomic():
            BatchTable.objects.filter(
                connection=connection,
                table_name__in=table_names,
            ).exclude(batch=batch).delete()

            existing = set(
                BatchTable.objects.filter(batch=batch, table_name__in=table_names)
                .values_list('table_name', flat=True)
            )
            new_tables = [
                BatchTable(
                    batch=batch,
                    connection=connection,
                    table_name=t,
                    sequence=i,
                )
                for i, t in enumerate(table_names)
                if t not in existing
            ]
            if new_tables:
                BatchTable.objects.bulk_create(new_tables)

        batch.refresh_from_db()
        return Response(MigrationBatchSerializer(batch).data, status=status.HTTP_200_OK)


class BatchTableDetailView(APIView):
    """
    DELETE /connection/<connection_id>/batches/<batch_id>/tables/<table_name>/
    Removes a table from the batch (it becomes Unassigned).
    """
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def delete(self, request, connection_id, batch_id, table_name):
        connection = _get_connection_for_user(request, connection_id)
        batch = get_object_or_404(MigrationBatch, id=batch_id, connection=connection)

        deleted_count, _ = BatchTable.objects.filter(
            batch=batch, connection=connection, table_name=table_name
        ).delete()

        if deleted_count == 0:
            return Response(
                {'error': f"Table '{table_name}' not found in this batch."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {'message': f"Table '{table_name}' removed from batch and returned to Unassigned."},
            status=status.HTTP_200_OK,
        )


class BatchRunNowView(APIView):
    """
    POST /connection/<connection_id>/batches/<batch_id>/run/
    Triggers an immediate (manual) migration run for this batch's tables only.
    """
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]

    def post(self, request, connection_id, batch_id):
        connection = get_object_or_404(
            Connection.objects.filter(cmp=request.user.cmp),
            connection_id=connection_id,
        )
        batch = get_object_or_404(MigrationBatch, id=batch_id, connection=connection)

        return Response(
            {'error': PIPELINE_REQUIRED_MSG},
            status=status.HTTP_400_BAD_REQUEST,
        )


class BatchToggleView(APIView):
    """
    POST /connection/<connection_id>/batches/<batch_id>/toggle/
    Flips the batch status active ↔ paused.
    - Pausing stops the APScheduler job (does not cancel in-flight runs).
    - Resuming re-registers the job.
    """
    permission_classes = [IsAuthenticated, IsAdministrator]

    def post(self, request, connection_id, batch_id):
        connection = _get_connection_for_user(request, connection_id)
        get_object_or_404(MigrationBatch, id=batch_id, connection=connection)

        return Response(
            {'error': PIPELINE_REQUIRED_MSG},
            status=status.HTTP_400_BAD_REQUEST,
        )
