from apps.elttool.models import Company,Data_Transfer,Invoice,Connection
import logging,calendar
logger = logging.getLogger(__name__)
from django.db.models import Sum
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from datetime import  timedelta
from decimal import Decimal, ROUND_DOWN
from apps.elttool.billing.services.billing_amount import get_dynamic_billing_amount   # calculate billing amount
from apps.elttool.billing.services.billing_pdf import generate_invoice_pdf  # generate invoice PDF
def as_date(value):
    """Convert datetime to date if needed"""
    if hasattr(value, 'date'):
        return value.date()
    return value
def generate_monthly_invoices():
    logger.info("Starting monthly invoice generation")

    current_date = timezone.now()
    last_full_month = (current_date.replace(day=1) - relativedelta(months=1)).date()
    companies = Company.objects.all()
    logger.info(f"Found {companies.count()} companies")

    for company in companies:
        try:
            logger.info(f"\n============\nProcessing company: {company.data}")

            has_start = bool(company.billing_start_date)
            has_end = bool(company.billing_end_date)
            logger.debug(f"{company.data} billing_start_date: {company.billing_start_date}, billing_end_date: {company.billing_end_date}")
            logger.info(f"{company.data} - has_start={has_start}, has_end={has_end}")

            total_invoices_generated = 0
            last_invoice = (
                Invoice.objects.filter(cmp=company)
                .order_by('-billing_end_date')
                .first()
            )

            # --- CUSTOM RANGE ONE-TIME INVOICE (first invoice w/ start and end dates) ---
            if not last_invoice and has_start and has_end:
                start_date = as_date(company.billing_start_date)
                end_date = as_date(company.billing_end_date)

                logger.info(
                    f"[DEBUG] {company.data} is triggering CUSTOM RANGE invoicing: {start_date} to {end_date} (no previous invoice exists)."
                )

                existing_invoice = Invoice.objects.filter(
                    cmp=company,
                    billing_start_date=start_date,
                    billing_end_date=end_date
                ).first()

                if existing_invoice:
                    logger.info(f"Invoice already exists for {company.data} for {start_date} to {end_date}, skipping.")
                else:
                    connections = Connection.objects.filter(cmp=company)


                    big_connections = []
                    small_connections = []

                    for connection in connections:
                        conn_data = Data_Transfer.objects.filter(
                            connection=connection,
                            start_time__gte=start_date,
                            start_time__lte=end_date
                        ).aggregate(
                            new=Sum('new_rec', default=0),
                            mod=Sum('mod_rec', default=0)
                        )
                        conn_records = (conn_data['new'] or 0) + (conn_data['mod'] or 0)
                        if conn_records >= 500_000:
                            big_connections.append({'connection': connection, 'records': conn_records})
                        elif conn_records >= 1000:
                            small_connections.append({'connection': connection, 'records': conn_records})
                        # < 1000: not billed

                    big_total_records = sum(item['records'] for item in big_connections)
                    big_bill_amount = Decimal('0.00')
                    if big_total_records > 0:
                        big_bill_amount = get_dynamic_billing_amount(big_total_records, reference_date=start_date)

                    small_bill_lines = []
                    for item in small_connections:
                        amt = get_dynamic_billing_amount(item['records'], reference_date=start_date)
                        small_bill_lines.append({
                            'connection': item['connection'],
                            'records': item['records'],
                            'amount': amt
                        })

                    total_amount = big_bill_amount + sum(line['amount'] for line in small_bill_lines)
                    total_records = big_total_records + sum(line['records'] for line in small_bill_lines)
                    units = (Decimal(total_records) / Decimal(1000)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                    invoice_number = Invoice.generate_invoice_number(company, start_date)

                    if total_records == 0:
                        logger.warning(f"No billable records for {company.data} in custom range {start_date} to {end_date}. No invoice.")
                    elif total_records < 1000:
                        logger.warning(f"Only {total_records} records (below minimum) for {company.data} in {start_date} to {end_date}. No invoice.")
                    else:
                        try:
                            invoice = Invoice.objects.create(
                                cmp=company,
                                invoice_number=invoice_number,
                                billing_start_date=start_date,
                                billing_end_date=end_date,
                                total_amount=round(total_amount, 2),
                                total_records=total_records,
                                units=units,
                                status='GENERATED'
                            )
                            try:
                                pdf_content = generate_invoice_pdf(
                                    invoice
                                )
                                invoice.pdf_content = pdf_content
                                invoice.is_generated = True
                                invoice.save()
                                total_invoices_generated += 1
                                logger.info(f"✅ [Custom-range] SINGLE INVOICE: {invoice_number} for {company.data} ({start_date} to {end_date}) amount: ${invoice.total_amount}")
                            except Exception as pdf_error:
                                logger.error(f"❌ PDF generation failed for {invoice_number}: {pdf_error}")
                        except Exception as create_error:
                            logger.error(f"❌ DB create error for {company.data} {start_date} to {end_date}: {create_error}")

                next_month = (end_date + timedelta(days=1)).replace(day=1)
                if as_date(next_month) <= last_full_month:
                    start_month = next_month
                else:
                    start_month = None

            # --- NORMAL MONTHLY LOGIC ---
            else:
                if last_invoice:
                    start_month = (as_date(last_invoice.billing_end_date) + timedelta(days=1)).replace(day=1)
                    logger.info(f"[DEBUG] {company.data} - Continuing monthly invoices after last invoice end: {as_date(last_invoice.billing_end_date)}")
                elif has_start:
                    start_month = as_date(company.billing_start_date).replace(day=1)
                else:
                    first_transfer = (
                        Data_Transfer.objects.filter(connection__cmp=company)
                        .order_by('start_time')
                        .first()
                    )
                    if not first_transfer:
                        logger.warning(f"No data transfers for {company.data}, skipping.")
                        start_month = None
                    else:
                        start_month = as_date(first_transfer.start_time).replace(day=1)

            if start_month and as_date(start_month) <= last_full_month:
                end_month = last_full_month
                logger.info(f"Monthly invoicing for {company.data} from {start_month.strftime('%B %Y')} to {end_month.strftime('%B %Y')}")
                current_month = as_date(start_month)
                while current_month <= end_month:
                    logger.debug(f"[DEBUG] {company.data} - In monthly invoice loop for {current_month.strftime('%Y-%m-%d')}")
                    _, last_day = calendar.monthrange(current_month.year, current_month.month)
                    billing_start = current_month.replace(day=1)
                    billing_end = current_month.replace(day=last_day)

                    existing_invoice = Invoice.objects.filter(
                        cmp=company,
                        billing_start_date__year=current_month.year,
                        billing_start_date__month=current_month.month
                    ).first()
                    if existing_invoice:
                        logger.info(f"Invoice {existing_invoice.invoice_number} already exists for {company.data} {current_month.strftime('%B %Y')}, skipping.")
                        current_month += relativedelta(months=1)
                        continue

                    connections = Connection.objects.filter(cmp=company)

                    big_connections = []
                    small_connections = []

                    for connection in connections:
                        conn_data = Data_Transfer.objects.filter(
                            connection=connection,
                            start_time__gte=billing_start,
                            start_time__lte=billing_end
                        ).aggregate(
                            new=Sum('new_rec', default=0),
                            mod=Sum('mod_rec', default=0)
                        )
                        conn_records = (conn_data['new'] or 0) + (conn_data['mod'] or 0)
                        if conn_records >= 500_000:
                            big_connections.append({'connection': connection, 'records': conn_records})
                        elif conn_records >= 1000:
                            small_connections.append({'connection': connection, 'records': conn_records})
                        # <1000: not billed

                    big_total_records = sum(item['records'] for item in big_connections)
                    big_bill_amount = Decimal('0.00')
                    if big_total_records > 0:
                        big_bill_amount = get_dynamic_billing_amount(big_total_records, reference_date=billing_start)

                    small_bill_lines = []
                    for item in small_connections:
                        amt = get_dynamic_billing_amount(item['records'], reference_date=billing_start)
                        small_bill_lines.append({
                            'connection': item['connection'],
                            'records': item['records'],
                            'amount': amt
                        })

                    total_amount = big_bill_amount + sum(line['amount'] for line in small_bill_lines)
                    total_records = big_total_records + sum(line['records'] for line in small_bill_lines)
                    units = (Decimal(total_records) / Decimal(1000)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                    invoice_number = Invoice.generate_invoice_number(company, current_month)

                    if total_records == 0:
                        logger.warning(f"No billable records for {company.data} {current_month.strftime('%B %Y')}. No invoice.")
                    elif total_records < 1000:
                        logger.warning(f"Only {total_records} records for {company.data} {current_month.strftime('%B %Y')}, below minimum. No invoice.")
                    else:
                        try:
                            invoice = Invoice.objects.create(
                                cmp=company,
                                invoice_number=invoice_number,
                                billing_start_date=billing_start,
                                billing_end_date=billing_end,
                                total_amount=round(total_amount, 2),
                                total_records=total_records,
                                units=units,
                                status='GENERATED'
                            )
                            try:
                                pdf_content = generate_invoice_pdf(
                                    invoice
                                )
                                invoice.pdf_content = pdf_content
                                invoice.is_generated = True
                                invoice.save()
                                total_invoices_generated += 1
                                logger.info(f"✅ [Monthly] Invoice {invoice_number} for {company.data} | {current_month.strftime('%B %Y')} | ${invoice.total_amount}")
                            except Exception as pdf_error:
                                logger.error(f"❌ PDF generation failed for {invoice_number}: {pdf_error}")
                        except Exception as create_error:
                            logger.error(f"❌ DB create error for {company.data} {current_month.strftime('%B %Y')}: {create_error}")

                    current_month += relativedelta(months=1)

            if total_invoices_generated > 0:
                logger.info(f"✅ Total {total_invoices_generated} invoice(s) generated for {company.data}")
            else:
                logger.info(f"ℹ️ No new invoices generated for {company.data}. See logs for details.")

        except Exception as e:
            logger.error(f"❌ Exception for {company.data}: {e}")

    logger.info("Monthly invoice generation completed")
