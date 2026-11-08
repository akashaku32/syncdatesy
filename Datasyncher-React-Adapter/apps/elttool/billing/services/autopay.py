import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.elttool.models import Invoice
from apps.elttool.billing.services.invoice_email import (
    send_card_expiring_email,
    send_invoice_payment_failed_email,
)


logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY


def run_autopay():
    today = timezone.now().date()
    logger.info("[AUTOPAY] Starting run at %s", timezone.now().isoformat())
    expiring_notice_sent = set()

    invoices = (
        Invoice.objects.select_related("cmp")
        .filter(payment_status="UNPAID")
        .exclude(billing_end_date=None)
        .filter(billing_end_date__date__lte=today)
        .order_by("billing_end_date")
    )

    if not invoices.exists():
        logger.info("[AUTOPAY] No unpaid invoices due for autopay.")
        return

    for invoice in invoices:
        company = invoice.cmp
        if not company:
            logger.warning("[AUTOPAY] Invoice %s has no company; skipping.", invoice.id)
            continue
        _maybe_send_card_expiring_notice(company, today, expiring_notice_sent)
        stripe_customer_id = company.get_decrypted_field("stripe_customer_id")
        stripe_payment_method_id = company.get_decrypted_field("stripe_default_payment_method_id")
        if not stripe_customer_id or not stripe_payment_method_id:
            logger.info(
                "[AUTOPAY] Missing Stripe customer/payment method for company %s; skipping invoice %s.",
                company.cmp_id,
                invoice.id,
            )
            continue

        # Avoid duplicate in-flight intents unless a failure reason exists.
        if invoice.get_decrypted_field("payment_reference") and not invoice.failure_reason:
            logger.info(
                "[AUTOPAY] Invoice %s already has intent %s; skipping.",
                invoice.id,
                invoice.get_decrypted_field("payment_reference"),
            )
            continue

        with transaction.atomic():
            invoice_locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
            if invoice_locked.payment_status == "PAID":
                logger.info(
                    "[AUTOPAY] Invoice %s already PAID before intent creation; skipping.",
                    invoice.id,
                )
                continue
            attempt_number = (invoice_locked.autopay_attempt or 0) + 1

        amount_cents = int(invoice.total_amount * 100)
        logger.info(
            "[AUTOPAY] Creating off-session PaymentIntent | invoice_id=%s amount_cents=%s attempt=%s",
            invoice.id,
            amount_cents,
            attempt_number,
        )

        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency="usd",
                customer=stripe_customer_id,
                payment_method=stripe_payment_method_id,
                off_session=True,
                confirm=True,
                metadata={"invoice_id": str(invoice.id)},
                idempotency_key=f"autopay-invoice-{invoice.id}-attempt-{attempt_number}",
            )
        except Exception as stripe_error:
            logger.error(
                "[AUTOPAY] Failed to create PaymentIntent for invoice %s: %s",
                invoice.id,
                stripe_error,
            )
            previous_reason = invoice.get_decrypted_field("failure_reason")
            failure_reason = str(stripe_error)
            with transaction.atomic():
                invoice.failure_reason = failure_reason
                invoice.autopay_attempt = attempt_number
                invoice.save(update_fields=["failure_reason", "autopay_attempt"])
            if failure_reason and failure_reason != (previous_reason or ""):
                try:
                    send_invoice_payment_failed_email(invoice, failure_reason)
                except Exception as email_error:
                    logger.warning(
                        "[AUTOPAY] Failed to send payment failure email for invoice %s: %s",
                        invoice.id,
                        email_error,
                    )
            continue

        with transaction.atomic():
            Invoice.objects.filter(
                pk=invoice.pk,
                autopay_attempt__lt=attempt_number,
            ).update(autopay_attempt=attempt_number)
        logger.info(
            "[AUTOPAY] Recorded attempt number %s for invoice %s",
            attempt_number,
            invoice.id,
        )

        with transaction.atomic():
            invoice.refresh_from_db()
            if invoice.payment_status == "PAID":
                logger.info(
                    "[AUTOPAY] Invoice %s already PAID after intent creation; leaving as-is.",
                    invoice.id,
                )
                continue
            invoice.payment_reference = payment_intent.get("id")
            invoice.payment_status = "PROCESSING"
            invoice.failure_reason = None
            invoice.save(update_fields=["payment_reference", "payment_status", "failure_reason"])

        logger.info(
            "[AUTOPAY] PaymentIntent created | invoice_id=%s intent_id=%s status=%s",
            invoice.id,
            payment_intent.get("id"),
            payment_intent.get("status"),
        )


def _parse_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _is_expiring_next_month(exp_month, exp_year, today):
    month = _parse_int(exp_month)
    year = _parse_int(exp_year)
    if not month or not year:
        return False
    next_month = 1 if today.month == 12 else today.month + 1
    next_year = today.year + (1 if today.month == 12 else 0)
    return month == next_month and year == next_year


def _maybe_send_card_expiring_notice(company, today, sent_registry):
    if not company or not company.cmp_id:
        return
    if company.cmp_id in sent_registry:
        return
    last_sent = getattr(company, "card_expiry_email_sent_at", None)
    if last_sent and last_sent.year == today.year and last_sent.month == today.month:
        return
    exp_month = company.get_decrypted_field("stripe_card_exp_month")
    exp_year = company.get_decrypted_field("stripe_card_exp_year")
    if not _is_expiring_next_month(exp_month, exp_year, today):
        return
    try:
        send_card_expiring_email(company)
        company.card_expiry_email_sent_at = timezone.now()
        company.save(update_fields=["card_expiry_email_sent_at"])
        sent_registry.add(company.cmp_id)
    except Exception as email_error:
        logger.warning(
            "[AUTOPAY] Failed to send card expiring email for company %s: %s",
            company.cmp_id,
            email_error,
        )
