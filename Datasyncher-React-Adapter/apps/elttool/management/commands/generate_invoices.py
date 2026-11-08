from django.core.management.base import BaseCommand
from apps.elttool.billing.services.billing_invoice import generate_monthly_invoices

class Command(BaseCommand):
    help = 'Generate monthly invoices'

    def handle(self, *args, **options):
        self.stdout.write('Starting invoice generation...')
        generate_monthly_invoices()
        self.stdout.write(self.style.SUCCESS('Successfully generated invoices'))