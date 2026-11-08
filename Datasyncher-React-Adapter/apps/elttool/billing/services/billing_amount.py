from decimal import Decimal, ROUND_DOWN
from django.utils.timezone import now
from apps.elttool.models import Pricing

def get_dynamic_billing_amount(record_count, reference_date=None):
    """
    Calculate billing amount using progressive tiered pricing structure.
    Each tier is applied to its specific range of records.
    
    Example: For 1,000,000 records (1000.00 units):
    - Tier 1 (1-500): $140 fixed
    - Tier 2 (501-1000): 500.00 units × $0.16 = $80.00
    - Total: $220.00
    """
    if record_count < 1000:
        return Decimal('0.00')

    if not reference_date:
        reference_date = now().date()

    # Convert records to units with 2 decimal places (e.g., 32755 records = 32.75 units)
    total_units = (Decimal(str(record_count)) / Decimal('1000')).quantize(Decimal('0.01'), rounding=ROUND_DOWN)

    pricing_tiers = Pricing.objects.filter(
        effective_start_date__lte=reference_date,
        effective_end_date__gte=reference_date
    ).order_by('unit_volume_range')
    
    if not pricing_tiers.exists():
        return Decimal('0.00')
    
    total_bill = Decimal('0.00')

    for tier in pricing_tiers:
        if not tier.unit_volume_range:
            continue
        
        # Convert tier boundaries to Decimal for precise comparison
        tier_lower = Decimal(str(tier.unit_volume_range.lower)) if tier.unit_volume_range.lower is not None else Decimal('0')
        tier_upper = Decimal(str(tier.unit_volume_range.upper)) if tier.unit_volume_range.upper is not None else None
        
        # Skip if we haven't reached this tier yet
        if total_units < tier_lower:
            break
        
        # Determine if units fall within this tier
        in_tier = False
        if tier_upper is None:
            # Open-ended tier
            in_tier = total_units > tier_lower
        else:
            # Check if units are within this tier's range
            in_tier = tier_lower <= total_units <= tier_upper
        
        if in_tier and tier_upper is not None and total_units <= tier_upper:
            # All our units are in this single tier
            if tier.pricing_type == 'fixed' and tier.fixed_amount is not None:
                total_bill += tier.fixed_amount
            elif tier.pricing_type == 'variable' and tier.price_per_unit is not None:
                # For variable rate, only charge for units in this tier
                units_in_tier = total_units - tier_lower
                tier_cost = units_in_tier * tier.price_per_unit
                total_bill += tier_cost
            break
        elif tier_upper is not None and total_units > tier_upper:
            # We pass through this entire tier
            if tier.pricing_type == 'fixed' and tier.fixed_amount is not None:
                total_bill += tier.fixed_amount
            elif tier.pricing_type == 'variable' and tier.price_per_unit is not None:
                # Calculate units in this tier (as Decimal)
                tier_units = tier_upper - tier_lower
                tier_cost = tier_units * tier.price_per_unit
                total_bill += tier_cost
        elif tier_upper is None:
            # Open-ended tier - calculate remaining units
            if tier.pricing_type == 'variable' and tier.price_per_unit is not None:
                remaining_units = total_units - tier_lower
                tier_cost = remaining_units * tier.price_per_unit
                total_bill += tier_cost
            break

    return total_bill.quantize(Decimal('0.01'), rounding=ROUND_DOWN)

