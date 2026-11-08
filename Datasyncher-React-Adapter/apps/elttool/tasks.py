from celery import shared_task
from apps.elttool.billing.services.billing_invoice import generate_monthly_invoices
from apps.elttool.billing.services.autopay import run_autopay


@shared_task
def generate_invoices_task():
    generate_monthly_invoices()

@shared_task
def run_autopay_task():
    run_autopay()

@shared_task
def cleanup_old_logs_task(days=92):
    """
    Deletes Log and MigrationRecord rows older than `days` days.
    Data_Transfer rows are never deleted — connection_id FK keeps billing intact.
    Scheduled: 5th of every month at 01:00 UTC (after invoice on 1st + autopay on 4th).
    """
    from datetime import timedelta
    from django.utils import timezone
    from apps.elttool.models import Log, MigrationRecord

    cutoff = timezone.now() - timedelta(days=days)
    deleted_logs, _ = Log.objects.filter(time__lt=cutoff).delete()
    deleted_mr, _ = MigrationRecord.objects.filter(created_at__lt=cutoff).delete()
    return (
        f'Cleanup complete: {deleted_logs} logs + {deleted_mr} migration records '
        f'deleted (older than {days} days). Data_Transfer rows preserved.'
    )
