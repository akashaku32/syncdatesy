from django.core.management.base import BaseCommand
from apps.elttool.billing.services.autopay import run_autopay


class Command(BaseCommand):
    help = "Attempt automatic payments for due unpaid invoices."

    def handle(self, *args, **options):
        run_autopay()
        self.stdout.write(self.style.SUCCESS("[AUTOPAY] Run completed."))
