"""
Management command: reconcile_stale_migrations
==============================================
Fail MigrationRecord rows stuck in_progress beyond a configurable age.

Use when Celery control-worker reconcile loops are starved by ghost jobs
(PENDING_NO_PAYLOAD in Redis, orphaned STARTED children, etc.).

Usage:
  python manage.py reconcile_stale_migrations --dry-run
  python manage.py reconcile_stale_migrations --hours 6
  python manage.py reconcile_stale_migrations --hours 1 --session-id 282
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.elttool.models import Log, MigrationRecord, SourceTableSelection


class Command(BaseCommand):
    help = (
        "Mark stale in_progress MigrationRecord rows (and matching table selections) as failed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=float,
            default=6.0,
            help="Fail rows with updated_at older than this many hours (default: 6)",
        )
        parser.add_argument(
            "--minutes",
            type=float,
            default=None,
            help="Fail rows older than this many minutes (overrides --hours when set)",
        )
        parser.add_argument(
            "--session-id",
            type=int,
            default=None,
            help="Optional migration_session_id to limit cleanup to one session",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview rows that would be failed without updating the database",
        )

    def handle(self, *args, **options):
        hours = float(options["hours"])
        minutes = options.get("minutes")
        session_id = options.get("session_id")
        dry_run = options["dry_run"]
        if minutes is not None:
            cutoff = timezone.now() - timezone.timedelta(minutes=float(minutes))
        else:
            cutoff = timezone.now() - timezone.timedelta(hours=hours)

        qs = MigrationRecord.objects.filter(status="in_progress")
        if session_id is not None:
            qs = qs.filter(migration_session_id=session_id)
        else:
            qs = qs.filter(updated_at__lt=cutoff)

        rows = list(
            qs.values(
                "id",
                "job_name",
                "migration_session_id",
                "connection_id",
                "table_name",
                "updated_at",
            )
        )

        self.stdout.write("=" * 60)
        self.stdout.write(f"Cutoff (updated_at before): {cutoff.isoformat()}")
        self.stdout.write(f"Stale in_progress rows     : {len(rows)}")
        self.stdout.write("=" * 60)

        for row in rows[:50]:
            self.stdout.write(
                f"  session={row['migration_session_id']} | job={row['job_name']} | "
                f"table={row['table_name']} | updated={row['updated_at']}"
            )
        if len(rows) > 50:
            self.stdout.write(f"  ... and {len(rows) - 50} more")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("--dry-run: no changes made. Remove --dry-run to apply.")
            )
            return

        if not rows:
            self.stdout.write(self.style.SUCCESS("Nothing to reconcile."))
            return

        connection_tables: dict[int, set[str]] = {}
        job_names: set[str] = set()
        record_ids: list[int] = []

        for row in rows:
            record_ids.append(row["id"])
            job_names.add(row["job_name"])
            cid = row["connection_id"]
            if cid and row["table_name"]:
                connection_tables.setdefault(cid, set()).add(row["table_name"])

        now = timezone.now()
        with transaction.atomic():
            updated_mr = MigrationRecord.objects.filter(id__in=record_ids).update(
                status="failed",
                updated_at=now,
            )
            updated_sts = 0
            for connection_id, tables in connection_tables.items():
                updated_sts += SourceTableSelection.objects.filter(
                    connection_id=connection_id,
                    table__in=list(tables),
                    status="in_progress",
                ).update(status="failed")
            if session_id is not None:
                connection_ids = {row["connection_id"] for row in rows if row["connection_id"]}
                for connection_id in connection_ids:
                    updated_sts += SourceTableSelection.objects.filter(
                        connection_id=connection_id,
                        status="in_progress",
                    ).update(status="failed")
                updated_logs = Log.objects.filter(
                    session=session_id,
                    status="I",
                ).update(
                    status="E",
                    message="Migration failed: stale session reconciled (never completed).",
                )
            else:
                updated_logs = 0

        self.stdout.write(
            self.style.SUCCESS(
                f"Failed {updated_mr} MigrationRecord row(s) across "
                f"{len(job_names)} job(s); updated {updated_sts} table selection(s)"
                + (f"; closed {updated_logs} in-progress log(s)." if session_id is not None else ".")
            )
        )
