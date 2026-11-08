
from django.templatetags.static import static
from django.template.loader import render_to_string
from django.utils import timezone
import logging
logger = logging.getLogger(__name__)
# from django.contrib.staticfiles import finders
from weasyprint import HTML
# logo_path = finders.find('billing/image001.png')
# print("logo path",logo_path)
from datetime import date, timedelta, datetime
def generate_invoice_pdf(invoice):
    # Example: external_base_url = "http://198.51.100.23" (or your IP/domain, **no trailing slash**)
    external_base_url = "https://qa.datasyncher.com"
    # Use absolute logo url (no `request`)
    logo_url = external_base_url + static('billing/company-logo.png')

    company_address1 = "50 Hampshire Dr, "
    company_address2 = "Plainsboro, NJ 08536"
    company_name = "Datasyncher"

    company_display_name = invoice.cmp.data or "Company"
    billed_to = invoice.cmp.get_decrypted_field("billing_address") or {}
    ship_to = invoice.cmp.get_decrypted_field("shipping_address") or billed_to
    billed_to = dict(billed_to)
    ship_to = dict(ship_to)
    billed_to["name"] = company_display_name
    ship_to["name"] = company_display_name

    due_date = invoice.created_at + timedelta(days=3)
    context = {
        'invoice': invoice,
        'due_date': due_date,
        'company_name': company_name,
        'company_address1': company_address1,
        'company_address2': company_address2,
        'logo_url': logo_url,
        'payment_link': None,
        'current_year': timezone.now().year,
        'billed_to': billed_to,
        'ship_to': ship_to,
    }

    try:
        html_string = render_to_string('documents/invoice.html', context)
        logger.info("HTML rendered successfully from template")

        base_url = external_base_url  # Used for static assets/CSS resolution
        pdf_file = HTML(string=html_string, base_url=base_url).write_pdf()
        logger.info("PDF generated successfully")
        return pdf_file
    except Exception as render_error:
        logger.error(f"Error during PDF rendering: {render_error}")
        raise
