from django.core.management.base import BaseCommand, CommandParser  # Django management command base classes
from django.db import connection as dj_connection                 # Low-level DB cursor (reuse same Postgres connection)
from typing import List, Optional
import json
from apps.elttool.models import Connection, SourceTableSelection


def fetch_delta_fields_for_source(source_id: int) -> List[str]:
    """
    Fetch the configured list of delta field names for a given source_id
    from the table `sourcedeltatable`.

    The column `delta_fields` is stored as JSON/JSONB; we normalize it to
    a Python list[str]. If nothing is configured, we return an empty list.
    """
    if not source_id:
        return []
    try:
        with dj_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT delta_fields
                FROM sourcedeltatable
                WHERE source_id = %s
                """,
                [source_id],
            )
            # Grab the first (and only) row for this source_id
            row = cursor.fetchone()
            if not row:
                return []
            raw_val = row[0]
            if isinstance(raw_val, list):
                return [str(x) for x in raw_val]
            try:
                parsed = json.loads(raw_val)
                return [str(x) for x in parsed] if isinstance(parsed, list) else []
            except Exception:
                return []
    except Exception:
        return []


def has_any_delta_field(table_fields, delta_fields: List[str]) -> bool:
    """
    Decide whether the table (described by `table_fields`) should be marked
    as delta-enabled. We simply check whether ANY of the configured delta
    field names appears as a key in the table's field map.

    `table_fields` is a JSONField in the DB; it may already be a dict, or a
    JSON-encoded string. We handle both cases safely.
    """
    if not table_fields or not delta_fields:
        return False
    if isinstance(table_fields, str):
        try:
            table_fields = json.loads(table_fields)
        except Exception:
            return False
    if not isinstance(table_fields, dict):
        return False
    lookup = set(df for df in delta_fields if isinstance(df, str))
    return any(k in table_fields for k in lookup)


class Command(BaseCommand):
    # Shown by `manage.py help`
    help = "Set SourceTableSelection.is_delta for MicrosoftDynamics365_FO, Salesforce, and GoogleReviews connections."

    def add_arguments(self, parser: CommandParser) -> None:
        # Optional: restrict updates to a single connection
        parser.add_argument("--connection-id", type=int, dest="connection_id", help="Restrict to a single connection_id")
        # Optional: dry-run mode to preview changes without writing to DB
        parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Report only, make no changes")

    def handle(self, *args, **options):
        connection_id: Optional[int] = options.get("connection_id")
        dry_run: bool = options.get("dry_run", False)

        # Only these sources participate (exact names as stored in tool_sourcemodel.name).
        # Requirements: MicrosoftDynamics365_FO, Salesforce, GoogleReviews
        allowed_sources = {"MicrosoftDynamics365_FO", "Salesforce", "GoogleReviews"}

        # Build the base queryset: bring source model via select_related for efficiency
        qs = Connection.objects.select_related("src_config", "src_config__src")
        if connection_id:
            qs = qs.filter(connection_id=connection_id)

        processed = 0
        changed = 0

        for conn in qs.iterator():
            # Resolve the source model for this connection
            src = getattr(conn.src_config, "src", None) if conn.src_config else None
            # Compare with exact source names to avoid ambiguity/casing issues
            src_name = (getattr(src, "name", "") or "").strip()
            if src_name not in allowed_sources:
                continue

            # Load configured delta fields for this source (may be empty)
            source_id = getattr(src, "src_id", None)
            delta_fields = fetch_delta_fields_for_source(source_id)

            # All source tables belonging to this connection
            sts_qs = SourceTableSelection.objects.filter(connection=conn)
            if not sts_qs.exists():
                continue

            # If the source has no configured delta fields: mark all tables as non-delta
            if not delta_fields:
                count = sts_qs.exclude(is_delta=False).count()
                if dry_run:
                    self.stdout.write(f"[DRY] Conn {conn.connection_id} ({src_name}): set is_delta=False on {count} row(s)")
                else:
                    changed += sts_qs.exclude(is_delta=False).update(is_delta=False)
                processed += 1
                continue

            # Otherwise: compute is_delta per-table by checking for any delta field presence
            to_update = []
            for sts in sts_qs.iterator():
                desired = has_any_delta_field(sts.table_fields, delta_fields)
                if bool(sts.is_delta) != bool(desired):
                    sts.is_delta = desired
                    to_update.append(sts)

            if dry_run:
                self.stdout.write(f"[DRY] Conn {conn.connection_id} ({src_name}): will update {len(to_update)} row(s)")
            else:
                if to_update:
                    # Efficiently update only rows with a change
                    SourceTableSelection.objects.bulk_update(to_update, ["is_delta"])
                    changed += len(to_update)
            processed += 1

        if dry_run:
            # Summary for preview mode
            self.stdout.write(self.style.WARNING(f"Dry run complete. Processed {processed} connection(s). No changes written."))
        else:
            # Summary for actual update
            self.stdout.write(self.style.SUCCESS(f"Done. Processed {processed} connection(s). Updated {changed} row(s)."))


