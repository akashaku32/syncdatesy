"""
Fail a pipeline run and release table locks so a fresh run does not skip tables.

Usage:
  python manage.py fail_pipeline_run 3503
  python manage.py fail_pipeline_run 3503 --reason "Manual reset before re-run"
  python manage.py fail_pipeline_run 3503 --dry-run
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.elttool.models import MigrationRecord, PipelineRun, SourceTableSelection
from apps.elttool.v1.pipeline_views import (
    _ACTIVE_LOAD_STATUSES,
    _teardown_pipeline_flow_fleet,
)

ACTIVE_STATUSES = tuple(_ACTIVE_LOAD_STATUSES)


class Command(BaseCommand):
    help = (
        "Mark a pipeline run failed, fail its migration records, clear in-progress "
        "table locks, revoke Celery tasks, and tear down flow workers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "run_id",
            type=int,
            help="Pipeline run id (execution id), e.g. 3503",
        )
        parser.add_argument(
            "--reason",
            default="Manually failed for clean pipeline restart",
            help="Error message stored on the run and its nodes",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without updating the database",
        )
        parser.add_argument(
            "--no-teardown",
            action="store_true",
            help="Skip enqueueing flow-fleet worker teardown",
        )
        parser.add_argument(
            "--no-revoke",
            action="store_true",
            help="Skip revoking in-flight Celery task ids",
        )

    def handle(self, *args, **options):
        run_id = int(options["run_id"])
        reason = str(options["reason"] or "").strip() or "Manually failed"
        dry_run = bool(options["dry_run"])
        teardown = not bool(options["no_teardown"])
        revoke = not bool(options["no_revoke"])

        try:
            run = PipelineRun.objects.select_related("pipeline").get(id=run_id)
        except PipelineRun.DoesNotExist:
            raise CommandError(f"PipelineRun id={run_id} not found")

        job_prefix = f"pipeline_run_{run_id}"
        run_mrs = list(
            MigrationRecord.objects.filter(job_name__startswith=job_prefix).values(
                "id",
                "connection_id",
                "table_name",
                "status",
                "task_id",
                "migration_session_id",
                "job_name",
            )
        )

        meta = dict(run.metadata or {})
        nodes = dict(meta.get("nodes") or {})
        session_ids = sorted(
            {
                int(entry.get("migration_session_id"))
                for entry in nodes.values()
                if isinstance(entry, dict) and entry.get("migration_session_id")
            }
        )
        connection_ids = sorted(
            {
                int(entry.get("connection_id"))
                for entry in nodes.values()
                if isinstance(entry, dict) and entry.get("connection_id")
            }
        )

        session_mrs = []
        if session_ids:
            session_mrs = list(
                MigrationRecord.objects.filter(
                    migration_session_id__in=session_ids,
                    status__in=ACTIVE_STATUSES,
                ).values(
                    "id",
                    "connection_id",
                    "table_name",
                    "status",
                    "task_id",
                    "migration_session_id",
                    "job_name",
                )
            )

        mr_by_id: dict[int, dict] = {}
        for row in run_mrs + session_mrs:
            mr_by_id[row["id"]] = row
        mr_rows = list(mr_by_id.values())

        connection_tables: dict[int, set[str]] = {}
        for row in mr_rows:
            cid = row.get("connection_id")
            table = row.get("table_name")
            if cid and table:
                connection_tables.setdefault(int(cid), set()).add(str(table))

        task_ids = sorted(
            {
                str(row["task_id"]).strip()
                for row in mr_rows
                if row.get("task_id")
            }
        )
        active_mr_ids = [
            row["id"] for row in mr_rows if row.get("status") in ACTIVE_STATUSES
        ]

        sts_candidates = 0
        for connection_id, tables in connection_tables.items():
            sts_candidates += SourceTableSelection.objects.filter(
                connection_id=connection_id,
                table__in=list(tables),
                status="in_progress",
            ).count()

        self.stdout.write("=" * 72)
        self.stdout.write(
            f"Pipeline run {run_id} | pipeline={run.pipeline_id} "
            f"name={run.pipeline.name!r} | current_status={run.status}"
        )
        self.stdout.write(f"Reason: {reason}")
        self.stdout.write(f"Migration sessions: {session_ids or '(none in metadata)'}")
        self.stdout.write(f"Connections: {connection_ids or '(derived from MRs)'}")
        self.stdout.write(f"Migration records (run + active session rows): {len(mr_rows)}")
        self.stdout.write(f"Active migration records to fail: {len(active_mr_ids)}")
        self.stdout.write(f"SourceTableSelection in_progress to clear: {sts_candidates}")
        self.stdout.write(f"Celery tasks to revoke: {len(task_ids)}")
        self.stdout.write(f"Nodes in metadata: {len(nodes)}")
        self.stdout.write("-" * 72)
        for row in mr_rows[:40]:
            self.stdout.write(
                f"  mr={row['id']} table={row['table_name']} status={row['status']} "
                f"session={row['migration_session_id']} job={str(row['job_name'])[:70]}"
            )
        if len(mr_rows) > 40:
            self.stdout.write(f"  ... and {len(mr_rows) - 40} more")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("--dry-run: no changes made. Remove --dry-run to apply.")
            )
            return

        now = timezone.now()
        with transaction.atomic():
            if active_mr_ids:
                MigrationRecord.objects.filter(id__in=active_mr_ids).update(
                    status="failed",
                    updated_at=now,
                )

            sts_cleared = 0
            for connection_id, tables in connection_tables.items():
                sts_cleared += SourceTableSelection.objects.filter(
                    connection_id=connection_id,
                    table__in=list(tables),
                    status="in_progress",
                ).update(status=None)

            for node_id, entry in list(nodes.items()):
                if not isinstance(entry, dict):
                    continue
                entry = dict(entry)
                entry["status"] = "failed"
                entry["error"] = reason
                entry["finished_at"] = now.isoformat()
                nodes[str(node_id)] = entry

            meta["nodes"] = nodes
            meta["error"] = reason
            meta["failure_pending"] = False
            meta["current_node_id"] = None
            meta["current_node_ids"] = []

            run.status = "failed"
            run.finished_at = now
            run.metadata = meta
            run.save(update_fields=["status", "finished_at", "metadata"])

        if revoke and task_ids:
            try:
                from apps.elttool.services.celery_inspect_service import (
                    get_migration_celery_app,
                )

                get_migration_celery_app().control.revoke(
                    task_ids,
                    terminate=True,
                    signal="SIGTERM",
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(f"Celery revoke failed (non-fatal): {exc}")
                )

        if teardown:
            _teardown_pipeline_flow_fleet(run_id)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. run={run_id} status=failed | mrs_failed={len(active_mr_ids)} "
                f"| sts_cleared={sts_cleared} | tasks_revoked={len(task_ids)}"
            )
        )
