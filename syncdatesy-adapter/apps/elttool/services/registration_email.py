from typing import Any, Mapping

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape


REGISTRATION_NOTIFICATION_EMAIL = 'annmarybenny985@gmail.com'
REGISTRATION_EMAIL_SUBJECT = 'New Datasyncher registration request'


def _display_value(value, fallback='Not provided'):
    value = str(value or '').strip()
    return value or fallback


def create_registration_request_email_content(
    registration_data: Mapping[str, Any],
) -> tuple[str, str]:
    """Build the HTML and plain-text registration notification bodies."""
    first_name = _display_value(registration_data.get('first_name'))
    last_name = _display_value(registration_data.get('last_name'))
    email = _display_value(registration_data.get('email'))
    phone_number = _display_value(registration_data.get('phone_number'))
    company_name = _display_value(registration_data.get('company_name'))

    plain_text = f"""A new registration request was submitted.

First name: {first_name}
Last name: {last_name}
Email: {email}
Phone number: {phone_number}
Company name: {company_name}
"""

    html_content = f"""\
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{REGISTRATION_EMAIL_SUBJECT}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:Arial,sans-serif;color:#18181b;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:32px 16px;background:#f4f4f5;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#fff;border:1px solid #e4e4e7;border-radius:10px;">
                    <tr>
                        <td style="padding:28px 32px 12px;font-size:22px;font-weight:700;color:#6e2fd5;">
                            New registration request
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:0 32px 24px;font-size:14px;line-height:1.6;color:#52525b;">
                            A new Datasyncher registration request was submitted with the following details.
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:0 32px 32px;">
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:14px;">
                                <tr><td style="width:35%;padding:12px;border-top:1px solid #e4e4e7;color:#71717a;">First name</td><td style="padding:12px;border-top:1px solid #e4e4e7;font-weight:600;">{escape(first_name)}</td></tr>
                                <tr><td style="padding:12px;border-top:1px solid #e4e4e7;color:#71717a;">Last name</td><td style="padding:12px;border-top:1px solid #e4e4e7;font-weight:600;">{escape(last_name)}</td></tr>
                                <tr><td style="padding:12px;border-top:1px solid #e4e4e7;color:#71717a;">Email</td><td style="padding:12px;border-top:1px solid #e4e4e7;font-weight:600;">{escape(email)}</td></tr>
                                <tr><td style="padding:12px;border-top:1px solid #e4e4e7;color:#71717a;">Phone number</td><td style="padding:12px;border-top:1px solid #e4e4e7;font-weight:600;">{escape(phone_number)}</td></tr>
                                <tr><td style="padding:12px;border-top:1px solid #e4e4e7;border-bottom:1px solid #e4e4e7;color:#71717a;">Company name</td><td style="padding:12px;border-top:1px solid #e4e4e7;border-bottom:1px solid #e4e4e7;font-weight:600;">{escape(company_name)}</td></tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

    return html_content, plain_text


def send_registration_request_email(registration_data: Mapping[str, Any]) -> None:
    """Send a registration request notification to the configured recipient."""
    html_content, plain_text = create_registration_request_email_content(
        registration_data,
    )
    email = EmailMultiAlternatives(
        subject=REGISTRATION_EMAIL_SUBJECT,
        body=plain_text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[REGISTRATION_NOTIFICATION_EMAIL],
    )
    email.attach_alternative(html_content, 'text/html')

    if email.send(fail_silently=False) != 1:
        raise RuntimeError('Registration request email was not sent.')
