import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from apps.elttool.models import User


logger = logging.getLogger(__name__)


def _get_invoice_recipients(company):
    users = User.objects.filter(
        cmp=company,
        roles__role_name__in=["Administrator", "Billing"],
    ).only("company_email", "email")
    recipients = []
    for user in users:
        if user.company_email:
            recipients.append(user.company_email)
        elif getattr(user, "email", None):
            recipients.append(user.email)
    return sorted(set(recipients))


def _build_subject(invoice):
    return f"Receipt from Datasyncher - {invoice.invoice_number}"


def _build_text_body(invoice, company_name, period_text, payment_method, receipt_url):
    paid_at = invoice.payment_date
    paid_at_text = timezone.localtime(paid_at).strftime("%b %d, %Y, %I:%M %p") if paid_at else "N/A"
    payment_line = f"Payment method: {payment_method}\n" if payment_method else ""
    return (
        f"Receipt from Datasyncher\n"
        f"Receipt #{invoice.invoice_number}\n\n"
        f"Amount paid: ${invoice.total_amount}\n"
        f"Date paid: {paid_at_text}\n"
        f"{payment_line}\n"
        f"Summary:\n"
        f"- Payment to Datasyncher: ${invoice.total_amount}\n"
        f"- Amount paid: ${invoice.total_amount}\n"
    )


def _build_html_body(invoice, company_name, period_text, payment_method, receipt_url):
    paid_at = invoice.payment_date
    paid_at_text = timezone.localtime(paid_at).strftime("%b %d, %Y, %I:%M %p") if paid_at else "N/A"
    logo_base = getattr(settings, "EMAIL_ASSET_BASE_URL", "").rstrip("/")
    if not logo_base:
        logo_base = "https://qa.datasyncher.com"
    header_path = "/static/billing/receipt-header.png"
    header_url = f"{logo_base}{header_path}"

    return f"""
<!DOCTYPE html>
<html>
<body style=\"margin:0;padding:0;background:#F4F6FA;font-family:'Helvetica Neue', Arial, sans-serif;color:#0F172A;\">
  <table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"padding:24px;\">
    <tr>
      <td align=\"center\">
        <table width=\"560\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#ffffff;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;\">
          <tr>
            <td style=\"padding:0;background:#7C3AED;\">
              <img src=\"{header_url}\" alt=\"Datasyncher receipt header\" width=\"560\" style=\"display:block;border:0;max-width:100%;\" />
            </td>
          </tr>
          <tr><td height=\"12\"></td></tr>
          <tr>
            <td style=\"font-size:20px;font-weight:600;text-align:center;color:#0F172A;\">
              Receipt from Datasyncher
            </td>
          </tr>
          <tr><td height=\"6\"></td></tr>
          <tr>
            <td style=\"font-size:12px;color:#64748B;text-align:center;\">
              Receipt #{invoice.invoice_number}
            </td>
          </tr>
          <tr><td height=\"22\"></td></tr>
          <tr>
            <td style=\"padding:0 24px;\">
              <table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">
                <tr>
                  <td style=\"font-size:11px;color:#94A3B8;text-transform:uppercase;letter-spacing:0.04em;\">Amount paid</td>
                  <td style=\"font-size:11px;color:#94A3B8;text-transform:uppercase;letter-spacing:0.04em;\">Date paid</td>
                  <td style=\"font-size:11px;color:#94A3B8;text-transform:uppercase;letter-spacing:0.04em;\">Payment method</td>
                </tr>
                <tr><td height=\"6\"></td></tr>
                <tr>
                  <td style=\"font-size:14px;font-weight:700;color:#0F172A;\">${invoice.total_amount}</td>
                  <td style=\"font-size:14px;font-weight:700;color:#0F172A;\">{paid_at_text}</td>
                  <td style=\"font-size:14px;font-weight:700;color:#0F172A;\">{payment_method or "N/A"}</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr><td height=\"24\"></td></tr>
          <tr>
            <td style=\"padding:0 24px;font-size:12px;color:#94A3B8;text-transform:uppercase;letter-spacing:0.04em;\">
              Summary
            </td>
          </tr>
          <tr><td height=\"12\"></td></tr>
          <tr>
            <td style=\"padding:0 24px;\">
              <table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;\">
                <tr><td height=\"16\"></td></tr>
                <tr>
                  <td style=\"padding:0 18px;\">
                    <table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">
                      <tr>
                        <td style=\"font-size:14px;color:#334155;\">Payment to Datasyncher</td>
                        <td align=\"right\" style=\"font-size:14px;color:#334155;\">${invoice.total_amount}</td>
                      </tr>
                      <tr><td height=\"10\"></td></tr>
                      <tr>
                        <td style=\"font-size:14px;font-weight:700;color:#0F172A;\">Amount paid</td>
                        <td align=\"right\" style=\"font-size:14px;font-weight:700;color:#0F172A;\">${invoice.total_amount}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr><td height=\"16\"></td></tr>
              </table>
            </td>
          </tr>
          <tr>
            <td height=\"48\" style=\"font-size:1px;line-height:1px;\">&nbsp;</td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _billing_portal_url():
    domain = getattr(settings, "DOMAIN", "qa.datasyncher.com")
    return f"https://{domain}/billing"


def _build_reset_style_html(title, intro, body_lines, footer_line, cta_text=None, cta_url=None):
    cta_html = ""
    if cta_text and cta_url:
        cta_html = f"""
<tr>
<td align="center">
<a href="{cta_url}"
   style="
       background:#2563EB;
       color:#ffffff;
       padding:12px 22px;
       border-radius:6px;
       text-decoration:none;
       font-size:14px;
       font-weight:600;
       display:inline-block;
   ">
{cta_text}
</a>
</td>
</tr>

<tr><td height="22"></td></tr>
"""

    body_html = "<br><br>".join(body_lines) if body_lines else ""

    return f"""
<!DOCTYPE html>
<html>
<body style="
    margin:0;
    padding:0;
    background:#ffffff;
    font-family:'Plus Jakarta Sans', Arial, sans-serif;
    color:#09090B;
">

<table width="100%" cellspacing="0" cellpadding="0" style="padding:24px;">
<tr>
<td align="center">

<table width="480" cellspacing="0" cellpadding="0"
       style="
           background:#ffffff;
           border:1px solid #E4E4E7;
           border-radius:10px;
           padding:28px;
       ">

<tr>
<td style="font-size:18px; font-weight:600; text-align:center;">
{title}
</td>
</tr>

<tr><td height="14"></td></tr>

<tr>
<td style="font-size:14px; line-height:1.6;">
{intro}
</td>
</tr>

<tr><td height="22"></td></tr>

{cta_html}

<tr>
<td style="font-size:13px; color:#52525B; line-height:1.6;">
{body_html}
</td>
</tr>

<tr><td height="26"></td></tr>

<tr>
<td style="
    font-size:12px;
    color:#71717A;
    text-align:center;
    border-top:1px solid #E4E4E7;
    padding-top:14px;
">
{footer_line}
</td>
</tr>

</table>
</td>
</tr>
</table>

</body>
</html>
"""


def _format_failure_reason(reason):
    if not reason:
        return "Unknown reason"
    if isinstance(reason, str):
        return reason.strip() or "Unknown reason"
    if isinstance(reason, dict):
        message = reason.get("message")
        decline = reason.get("decline_code")
        code = reason.get("code")
        error_type = reason.get("type")
        parts = [p for p in [message, decline, code, error_type] if p]
        return " | ".join(str(p) for p in parts) if parts else "Unknown reason"
    return str(reason)


def send_invoice_payment_failed_email(invoice, failure_reason):
    company = invoice.cmp
    if not company:
        logger.warning("[BILLING_EMAIL] Failed invoice %s has no company; skipping email.", invoice.id)
        return

    recipients = _get_invoice_recipients(company)
    if not recipients:
        logger.info("[BILLING_EMAIL] No billing/admin recipients for company %s; skipping.", company.cmp_id)
        return

    brand = invoice.get_decrypted_field("payment_method_brand")
    last4 = invoice.get_decrypted_field("payment_method_last4")
    if not brand:
        brand = company.get_decrypted_field("stripe_card_brand")
    if not last4:
        last4 = company.get_decrypted_field("stripe_card_last4")
    payment_method = None
    if brand and last4:
        payment_method = f"{str(brand).upper()} - {last4}"
    elif brand:
        payment_method = str(brand).upper()
    elif last4:
        payment_method = f"**** {last4}"

    reason_text = _format_failure_reason(failure_reason)
    subject = f"Payment failed for invoice {invoice.invoice_number}"

    text_body = (
        "Hello,\n\n"
        f"We were unable to process the automatic payment for invoice {invoice.invoice_number}.\n"
        f"Failure reason: {reason_text}\n"
        f"Payment method: {payment_method or 'N/A'}\n\n"
        "Please update your billing details so we can retry payment.\n"
        f"Billing portal: {_billing_portal_url()}\n\n"
        "Datasyncher Billing Team\n"
        "support1@datasyncher.com\n"
    )

    html_body = _build_reset_style_html(
        title="Payment Failed",
        intro=(
            f"We were unable to process the automatic payment for invoice "
            f"<strong>{invoice.invoice_number}</strong>."
        ),
        body_lines=[
            f"<strong>Failure reason:</strong> {reason_text}",
            f"<strong>Payment method:</strong> {payment_method or 'N/A'}",
            "Please update your billing details so we can retry payment.",
        ],
        footer_line="Datasyncher Billing Team<br>support1@datasyncher.com",
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    email.attach_alternative(html_body, "text/html")
    email.send()
    logger.info(
        "[BILLING_EMAIL] Sent payment failure email for invoice %s to %s",
        invoice.invoice_number,
        ", ".join(recipients),
    )


def send_card_expiring_email(company):
    if not company:
        return
    recipients = _get_invoice_recipients(company)
    if not recipients:
        return

    brand = company.get_decrypted_field("stripe_card_brand")
    last4 = company.get_decrypted_field("stripe_card_last4")
    exp_month = company.get_decrypted_field("stripe_card_exp_month")
    exp_year = company.get_decrypted_field("stripe_card_exp_year")

    payment_method = None
    if brand and last4:
        payment_method = f"{str(brand).upper()} - {last4}"
    elif brand:
        payment_method = str(brand).upper()
    elif last4:
        payment_method = f"**** {last4}"

    exp_text = None
    if exp_month and exp_year:
        exp_text = f"{int(exp_month):02d}/{exp_year}"

    subject = "Your card is expiring soon"
    text_body = (
        "Hello,\n\n"
        "Your saved card for Datasyncher billing is expiring next month.\n"
        f"Payment method: {payment_method or 'N/A'}\n"
        f"Expiration: {exp_text or 'N/A'}\n\n"
        "Please update your billing details to avoid any payment interruptions.\n"
        f"Billing portal: {_billing_portal_url()}\n\n"
        "Datasyncher Billing Team\n"
        "support1@datasyncher.com\n"
    )

    html_body = _build_reset_style_html(
        title="Card Expiring Soon",
        intro="Your saved card for Datasyncher billing is expiring next month.",
        body_lines=[
            f"<strong>Payment method:</strong> {payment_method or 'N/A'}",
            f"<strong>Expiration:</strong> {exp_text or 'N/A'}",
            "Please update your billing details to avoid any payment interruptions.",
        ],
        footer_line="Datasyncher Billing Team<br>support1@datasyncher.com",
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    email.attach_alternative(html_body, "text/html")
    email.send()
    logger.info(
        "[BILLING_EMAIL] Sent card expiring email to %s",
        ", ".join(recipients),
    )


def send_invoice_paid_email(invoice):
    company = invoice.cmp
    if not company:
        logger.warning("[BILLING_EMAIL] Invoice %s has no company; skipping email.", invoice.id)
        return

    recipients = _get_invoice_recipients(company)
    if not recipients:
        logger.info("[BILLING_EMAIL] No billing/admin recipients for company %s; skipping.", company.cmp_id)
        return

    company_name = company.data or "your company"
    period_text = "N/A"
    if invoice.billing_start_date and invoice.billing_end_date:
        period_text = f"{invoice.billing_start_date.date()} to {invoice.billing_end_date.date()}"

    receipt_url = invoice.get_decrypted_field("receipt_url")
    brand = invoice.get_decrypted_field("payment_method_brand")
    last4 = invoice.get_decrypted_field("payment_method_last4")
    payment_method = None
    if brand and last4:
        payment_method = f"{str(brand).upper()} - {last4}"
    elif brand:
        payment_method = str(brand).upper()
    elif last4:
        payment_method = f"**** {last4}"

    subject = _build_subject(invoice)
    text_body = _build_text_body(invoice, company_name, period_text, payment_method, receipt_url)
    html_body = _build_html_body(invoice, company_name, period_text, payment_method, receipt_url)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    email.attach_alternative(html_body, "text/html")

    if invoice.pdf_content:
        filename = f"invoice_{invoice.invoice_number}.pdf"
        email.attach(filename, invoice.pdf_content, "application/pdf")
    else:
        logger.info("[BILLING_EMAIL] Invoice %s has no PDF content; sending without attachment.", invoice.id)

    email.send()
    logger.info(
        "[BILLING_EMAIL] Sent payment success email for invoice %s to %s",
        invoice.invoice_number,
        ", ".join(recipients),
    )
