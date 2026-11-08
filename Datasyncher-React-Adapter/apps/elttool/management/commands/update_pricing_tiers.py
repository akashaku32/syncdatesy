from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
from datetime import date, timedelta
from apps.elttool.models import Pricing
from psycopg2.extras import NumericRange


class Command(BaseCommand):
    help = 'Update pricing tiers with the new progressive tiered billing structure'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing pricing data before adding new tiers',
        )
        parser.add_argument(
            '--start-date',
            type=str,
            default=None,
            help='Start date for pricing tiers (YYYY-MM-DD format)',
        )
        parser.add_argument(
            '--end-date',
            type=str,
            default=None,
            help='End date for pricing tiers (YYYY-MM-DD format)',
        )

    def handle(self, *args, **options):
        # Set default dates if not provided
        start_date = options.get('start_date')
        end_date = options.get('end_date')
        
        if not start_date:
            # Set to January 1st of current year
            current_year = date.today().year
            start_date = date(current_year, 1, 1)
        else:
            start_date = date.fromisoformat(start_date)
            
        if not end_date:
            # Set to December 31st of next year
            next_year = date.today().year + 1
            end_date = date(next_year, 12, 31)
        else:
            end_date = date.fromisoformat(end_date)

        # Clear existing data if requested
        if options['clear']:
            self.stdout.write('Clearing existing pricing data...')
            Pricing.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Existing pricing data cleared.'))

        # Define the new pricing tier structure based on your table
        # Note: Tiers should not overlap. Use [lower, upper) format except last tier
        pricing_tiers = [
            {
                'range_str': '[1,500]',  # 0-500K records (0-500 units)
                'lower': 1,
                'upper': 500,
                'bounds': '[]',
                'pricing_type': 'fixed',
                'fixed_amount': Decimal('140.00'),
                'price_per_unit': None
            },
            {
                'range_str': '(500,2000]',  # 500K-2M records (501-2000 units)
                'lower': 500,
                'upper': 2000,
                'bounds': '(]',
                'pricing_type': 'variable',
                'fixed_amount': None,
                'price_per_unit': Decimal('0.16')
            },
            {
                'range_str': '(2000,5000]',  # 2M-5M records (2001-5000 units)
                'lower': 2000,
                'upper': 5000,
                'bounds': '(]',
                'pricing_type': 'variable',
                'fixed_amount': None,
                'price_per_unit': Decimal('0.13')
            },
            {
                'range_str': '(5000,10000]',  # 5M-10M records (5001-10000 units)
                'lower': 5000,
                'upper': 10000,
                'bounds': '(]',
                'pricing_type': 'variable',
                'fixed_amount': None,
                'price_per_unit': Decimal('0.11')
            },
            {
                'range_str': '(10000,25000]',  # 10M-25M records (10001-25000 units)
                'lower': 10000,
                'upper': 25000,
                'bounds': '(]',
                'pricing_type': 'variable',
                'fixed_amount': None,
                'price_per_unit': Decimal('0.09')
            },
            {
                'range_str': '(25000,50000]',  # 25M-50M records (25001-50000 units)
                'lower': 25000,
                'upper': 50000,
                'bounds': '(]',
                'pricing_type': 'variable',
                'fixed_amount': None,
                'price_per_unit': Decimal('0.07')
            },
            {
                'range_str': '(50000,)',  # 50M+ records (open-ended)
                'lower': 50000,
                'upper': None,
                'bounds': '()',
                'pricing_type': 'variable',
                'fixed_amount': None,
                'price_per_unit': Decimal('0.07')
            }
        ]

        # Create pricing tiers
        created_count = 0
        updated_count = 0

        with transaction.atomic():
            for i, tier_data in enumerate(pricing_tiers, 1):
                # Create NumericRange for IntegerRangeField
                unit_range = NumericRange(
                    tier_data['lower'],
                    tier_data['upper'],
                    bounds=tier_data['bounds']
                )
                
                # Check if pricing tier already exists with same range and dates
                existing_tier = Pricing.objects.filter(
                    effective_start_date=start_date,
                    effective_end_date=end_date
                ).filter(
                    unit_volume_range=unit_range
                ).first()

                if existing_tier:
                    # Update existing tier
                    existing_tier.pricing_type = tier_data['pricing_type']
                    existing_tier.fixed_amount = tier_data['fixed_amount']
                    existing_tier.price_per_unit = tier_data['price_per_unit']
                    existing_tier.save()
                    updated_count += 1
                    self.stdout.write(self.style.WARNING(f'Updated tier {i}: {tier_data["range_str"]}'))
                else:
                    # Create new tier
                    Pricing.objects.create(
                        effective_start_date=start_date,
                        effective_end_date=end_date,
                        unit_volume_range=unit_range,
                        pricing_type=tier_data['pricing_type'],
                        fixed_amount=tier_data['fixed_amount'],
                        price_per_unit=tier_data['price_per_unit']
                    )
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f'Created tier {i}: {tier_data["range_str"]}'))

        # Summary
        self.stdout.write('')
        self.stdout.write('='*50)
        self.stdout.write(
            self.style.SUCCESS(
                f'Pricing tiers update complete!\n'
                f'Created: {created_count} | Updated: {updated_count}\n'
                f'Effective: {start_date} to {end_date}'
            )
        )
        self.stdout.write('='*50)

