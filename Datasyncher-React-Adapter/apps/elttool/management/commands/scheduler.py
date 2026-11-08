from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, CrontabSchedule
import json

class Command(BaseCommand):
    help = "Setup periodic celery tasks"

    def handle(self, *args, **kwargs):
        # Run at 00:00 UTC on the 1st day of every month
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='0', hour='0', day_of_month='1', month_of_year='*', day_of_week='*'
        )
        PeriodicTask.objects.update_or_create(
            name='Generate Invoices Monthly',
            defaults={
                'crontab': schedule,
                'task': 'apps.elttool.tasks.generate_invoices_task',
                'args': json.dumps([]),
                'enabled': True,
            }
        )

        # Run on the 4th day of every month at 00:30 UTC for autopay attempts
        autopay_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='30', hour='0', day_of_month='4', month_of_year='*', day_of_week='*'
        )
        PeriodicTask.objects.update_or_create(
            name='stripe autopay run',
            defaults={
                'crontab': autopay_schedule,
                'task': 'apps.elttool.tasks.run_autopay_task',
                'args': json.dumps([]),
                'enabled': True,
            }
        )

        # Run on the 5th of every month at 01:00 UTC
        # After invoice generation (1st) and autopay (4th) are safely complete
        cleanup_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='0', hour='1', day_of_month='5', month_of_year='*', day_of_week='*'
        )
        PeriodicTask.objects.update_or_create(
            name='Cleanup Old Logs Monthly',
            defaults={
                'crontab': cleanup_schedule,
                'task': 'apps.elttool.tasks.cleanup_old_logs_task',
                'args': json.dumps([]),  # uses default days=92
                'enabled': True,
            }
        )

        self.stdout.write(self.style.SUCCESS('Scheduled Task created/updated.'))

