"""
Management command: cleanup_old_logs
====================================
Deletes Log and MigrationRecord rows older than N days (default: 92).

Data_Transfer rows are NEVER deleted — they serve as the permanent source of
truth for billing and usage. After this cleanup:
  - data_transfer.log_id              → SET NULL  (DB constraint, automatic)
  - data_transfer.migration_record_id → SET NULL  (DB constraint, automatic)
  - data_transfer.connection_id       → unchanged ✅ billing works forever

Usage:
  python manage.py cleanup_old_logs              # 92-day retention (production default)
  python manage.py cleanup_old_logs --days 92    # explicit
  python manage.py cleanup_old_logs --dry-run    # preview counts only, no deletion
"""

from datetime import timedelta
from django.utils import timezone
from django.core.management.base import BaseCommand
from apps.elttool.models import Log, MigrationRecord, Data_Transfer


class Command(BaseCommand):
    help = (
        'Delete Log and MigrationRecord entries older than N days (default 92). '
        'Data_Transfer rows are preserved — their connection_id FK is direct and '
        'is never affected by this cleanup.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=92,
            help='Retention period in days (default: 92)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be deleted without making any changes',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=days)

        log_count = Log.objects.filter(time__lt=cutoff).count()
        mr_count = MigrationRecord.objects.filter(created_at__lt=cutoff).count()

        # data_transfer rows with connection_id still intact after cleanup
        dt_total = Data_Transfer.objects.count()
        dt_affected = Data_Transfer.objects.filter(
            start_time__lt=cutoff
        ).count()

        self.stdout.write('=' * 60)
        self.stdout.write(f'Retention policy : {days} days')
        self.stdout.write(f'Cutoff date      : {cutoff.date()} (records older than this)')
        self.stdout.write('=' * 60)
        self.stdout.write(f'Logs to delete              : {log_count}')
        self.stdout.write(f'MigrationRecords to delete  : {mr_count}')
        self.stdout.write(f'Data_Transfer rows (total)  : {dt_total}  ← NOT deleted')
        self.stdout.write(f'Data_Transfer rows (old)    : {dt_affected}  ← FK set to NULL, billing intact')
        self.stdout.write('=' * 60)

        if dry_run:
            self.stdout.write(
                self.style.WARNING('--dry-run: No changes made. Remove --dry-run to execute.')
            )
            return

        # Delete Logs first (child of nothing critical, parent of migration_record fk in Log)
        deleted_logs, _ = Log.objects.filter(time__lt=cutoff).delete()

        # Delete MigrationRecords — data_transfer.migration_record_id SET NULL automatically
        deleted_mr, _ = MigrationRecord.objects.filter(created_at__lt=cutoff).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Deleted {deleted_logs} logs, {deleted_mr} migration records. '
                f'All data_transfer rows preserved with connection_id intact.'
            )
        )
