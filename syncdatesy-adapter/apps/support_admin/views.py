from apps.elttool.v1.utils import internal_service_headers
import json
import jwt
import calendar
import logging
import re
from urllib.parse import quote_plus
from datetime import date, datetime, timedelta, timezone as dt_timezone

import requests

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Prefetch
from django.db.models import Sum, F, Max
from django.db.models.functions import Coalesce, TruncMonth, ExtractDay
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.html import escape
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from dateutil.relativedelta import relativedelta

from apps.elttool.billing.services.billing_amount import get_dynamic_billing_amount
from apps.elttool.models import (
    Company,
    Connection,
    Data_Transfer,
    DestinationConfig,
    Invoice,
    Log,
    MigrationRecord,
    PlanResourceConfig,
    RegistrationRequest,
    Role,
    ScheduledJob,
    SourceTableSelection,
    TicketSupport,
    TicketReply,
)
from .models import AdminUser
from .auth import (
    generate_admin_tokens,
    generate_admin_reset_token,
    decode_admin_token,
    ADMIN_JWT_SECRET,
    ADMIN_JWT_ALGORITHM,
)
from .authentication import AdminJWTAuthentication
from .serializers import (
    AdminRegisterSerializer,
    AdminLoginSerializer,
    AdminForgotPasswordSerializer,
    AdminResetPasswordSerializer,
    AdminUserSerializer,
    AdminRegistrationRequestSerializer,
    AdminRegistrationRequestActionSerializer,
    AdminRegistrationRequestExtendEndDateSerializer,
    AdminCompanyListSerializer,
    AdminCompanyBillingSerializer,
    AdminInvoiceSerializer,
    AdminCompanyConnectionSerializer,
    AdminConnectionSchemaSerializer,
    AdminDestinationDetailSerializer,
    AdminDestinationListSerializer,
    AdminTicketListSerializer,
    AdminTicketDetailSerializer,
    AdminTicketReplySerializer,
    AdminTicketStatusSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def as_date(val):
    if hasattr(val, 'date'):
        return val.date()
    return val


def _standardize_destination_error_message(dest_name, error_msg):
    """Return backend error text as-is (no message substitution)."""
    return str(error_msg or "").strip()


def _coerce_json_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return {}

    if isinstance(value, dict):
        return value

    return {}


def _extract_backend_response_error(response, fallback_prefix):
    """
    Mirror the connection-test behavior from the main app:
    treat a JSON body with status_code != 200 as failure even if HTTP is 200.
    """
    try:
        response_data = response.json() if getattr(response, "content", None) else {}
    except Exception:
        response_data = {}

    if isinstance(response_data, dict) and response_data.get("status_code") == 200:
        return False, None, response_data

    if getattr(response, "status_code", None) == 200 and isinstance(response_data, dict):
        error_detail = (
            response_data.get("body")
            or response_data.get("message")
            or response_data.get("detail")
            or response.text
            or "No error message provided"
        )
        if response_data:
            return True, f"{fallback_prefix}: {error_detail}", response_data

    if getattr(response, "status_code", None) != 200:
        error_detail = None
        try:
            error_detail = response.json()
        except Exception:
            error_detail = None

        if isinstance(error_detail, dict):
            error_text = (
                error_detail.get("detail")
                or error_detail.get("message")
                or error_detail.get("body")
                or response.text
                or "No error message provided"
            )
        else:
            error_text = response.text[:200] if getattr(response, "text", None) else "No error message provided"

        return True, f"{fallback_prefix}: {error_text}", response_data

    return True, f"{fallback_prefix}: No error message provided", response_data


def _parse_int_list(value):
    """
    Parse a comma-separated string, a list, or a single value into a list of ints.
    """
    if value is None or value == "":
        return []

    if isinstance(value, str):
        raw_values = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]

    parsed = []
    for item in raw_values:
        try:
            parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _build_selected_tables_payload(connections):
    payload = []
    total_tables = 0

    for connection in connections:
        selected_tables = getattr(connection, "prefetched_selected_schema_rows", None)
        if selected_tables is None:
            selected_tables = SourceTableSelection.objects.filter(
                connection=connection,
                selected=True,
            ).order_by("sequence", "tbl_id")

        selected_tables_data = AdminConnectionSchemaSerializer(selected_tables, many=True).data
        total_tables += len(selected_tables_data)

        payload.append(
            {
                "connection_id": connection.connection_id,
                "company_name": connection.cmp.data if connection.cmp else None,
                "source_name": connection.src_config.src.name if connection.src_config and connection.src_config.src else None,
                "destination_name": connection.dst_config.dst.name if connection.dst_config and connection.dst_config.dst else None,
                "source_title": connection.src_config.name if connection.src_config else None,
                "destination_title": connection.dst_config.name if connection.dst_config else None,
                "selected_tables": selected_tables_data,
                "selected_table_count": len(selected_tables_data),
            }
        )

    return payload, total_tables


def _parse_timestamp(value):
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        return value
    try:
        raw_value = str(value).strip()
        if raw_value.endswith("Z"):
            raw_value = raw_value[:-1] + "+00:00"
        raw_value = re.sub(r"(\d{2})\.(\d{2})\.(\d{2})(?=(?:[+\-]\d{2}:\d{2})?$)", r"\1:\2:\3", raw_value)
        parsed = datetime.fromisoformat(raw_value)
    except Exception:
        return None
    if getattr(parsed, "tzinfo", None) is None:
        parsed = timezone.make_aware(parsed, timezone=dt_timezone.utc)
    return parsed.astimezone(dt_timezone.utc)


def _format_timestamp(value):
    if not value:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = timezone.make_aware(value, timezone=dt_timezone.utc)
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H.%M.%SZ")


def _parse_offset_duration(value):
    if value is None or value == "":
        return timedelta(0)

    if isinstance(value, (int, float)):
        return timedelta(hours=float(value))

    raw_value = str(value).strip()
    if not raw_value:
        return timedelta(0)

    if ":" in raw_value:
        parts = raw_value.split(":")
        if len(parts) != 3:
            raise ValueError("offset must be in HH:MM:SS format")
        hours, minutes, seconds = (int(part) for part in parts)
        return timedelta(hours=hours, minutes=minutes, seconds=seconds)

    return timedelta(hours=float(raw_value))


def _is_selected_tables_update_blocked(connection):
    if Log.objects.filter(connection=connection, status='I').exists():
        return True
    if MigrationRecord.objects.filter(connection=connection, status='in_progress').exists():
        return True
    if SourceTableSelection.objects.filter(connection=connection, status='in_progress').exists():
        return True
    return False


def _build_connection_list_context(connections):
    connection_ids = [connection.connection_id for connection in connections]
    if not connection_ids:
        return {}

    in_progress_ids = set(
        Log.objects.filter(
            connection_id__in=connection_ids,
            status='I',
            migration_record__isnull=True,
            job_name__isnull=False,
        ).values_list('connection_id', flat=True).distinct()
    )

    latest_log_ids = (
        Log.objects.filter(
            connection_id__in=connection_ids,
            status__in=('S', 'E', 'P', 'F'),
            migration_record__isnull=True,
            job_name__isnull=False,
        )
        .values('connection_id')
        .annotate(latest_log_id=Max('log_id'))
    )
    latest_log_id_map = {row['connection_id']: row['latest_log_id'] for row in latest_log_ids}
    latest_job_logs = {
        log.connection_id: log
        for log in Log.objects.filter(log_id__in=latest_log_id_map.values())
    }

    migration_statuses = {}
    for connection_id in connection_ids:
        if connection_id in in_progress_ids:
            migration_statuses[connection_id] = 'S'
        else:
            latest_log = latest_job_logs.get(connection_id)
            if latest_log and latest_log.status in ('E', 'P', 'F'):
                migration_statuses[connection_id] = 'E'
            elif latest_log and latest_log.status == 'S':
                migration_statuses[connection_id] = 'C'
            else:
                migration_statuses[connection_id] = None

    return {
        'migration_statuses': migration_statuses,
    }


def _test_destination_connection(dest_name, config_data):
    formatted_data = {dest_name: config_data}
    if dest_name == "Snowflake":
        formatted_data["validate_destination_schema"] = False

    destination_api_url = f'https://{settings.DOMAIN}/api-new/{dest_name}-Authorization'

    try:
        response = requests.post(
            destination_api_url,
            data=json.dumps(formatted_data),
            headers=internal_service_headers(),
            verify=False,
            timeout=30,
        )

        if response.status_code != 200:
            response_data = None
            try:
                response_data = response.json()
                error_detail = response_data.get("detail") or response_data.get("message") or response_data.get("body") or response.text
            except Exception:
                error_detail = response.text[:200]
                response_data = {
                    "body": error_detail,
                }
            if isinstance(response_data, dict):
                response_data.setdefault("http_status", response.status_code)
                response_data.setdefault("body", error_detail)
            error_msg = _standardize_destination_error_message(dest_name, f"Authorization service returned HTTP {response.status_code}: {error_detail}")
            return False, f"Destination configuration test failed: {error_msg}", response_data

        try:
            response_data = response.json() if response.content else {}
        except json.JSONDecodeError:
            response_data = {}

        if response_data.get("status_code") == 200:
            return True, "Destination connection test successful!", response_data

        error_msg = (
            response_data.get("body") or
            response_data.get("message") or
            response.text or
            "No error message provided"
        )
        error_msg = _standardize_destination_error_message(dest_name, error_msg)
        if isinstance(error_msg, str) and error_msg.strip().lower().startswith("error: "):
            return False, error_msg.strip(), response_data
        return False, f"Destination configuration test failed: {error_msg}", response_data

    except requests.exceptions.Timeout:
        return False, "Connection test timed out. Please check your network connection.", None
    except requests.exceptions.RequestException as exc:
        return False, f"Failed to connect to authorization service: {str(exc)}", None
    except Exception as exc:
        return False, f"Unexpected error during connection test: {str(exc)}", None


class AdminRegisterView(APIView):
    """
    POST /api/v1/support-admin/auth/register/
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AdminRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        admin = AdminUser(email=serializer.validated_data['email'], full_name=serializer.validated_data['full_name'])
        admin.set_password(serializer.validated_data['password'])
        admin.save()

        return Response(AdminUserSerializer(admin).data, status=status.HTTP_201_CREATED)


class AdminLoginView(APIView):
    """
    POST /api/v1/support-admin/auth/login/
    Completely separate login for support admin panel.
    Uses its own AdminUser table and its own JWT secret.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        try:
            admin = AdminUser.objects.get(email=email, is_active=True)
        except AdminUser.DoesNotExist:
            return Response({'message': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not admin.check_password(password):
            return Response({'message': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        access_token, refresh_token = generate_admin_tokens(admin)

        return Response({
            'access_token': access_token,
            'refresh_token': refresh_token,
            'admin': AdminUserSerializer(admin).data,
        }, status=status.HTTP_200_OK)


class AdminForgotPasswordView(APIView):
    """
    POST /api/v1/support-admin/auth/forgot-password/
    Generate a password reset token for a support admin.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AdminForgotPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        try:
            admin = AdminUser.objects.get(email=email, is_active=True)
        except AdminUser.DoesNotExist:
            return Response(
                {'message': 'If a matching user exists, a password reset email has been sent.'},
                status=status.HTTP_200_OK,
            )

        reset_token = generate_admin_reset_token(admin)
        reset_url_base = getattr(
            settings,
            'SUPPORT_ADMIN_RESET_URL',
            'https://qa.datasyncher.com/support-admin/reset-password',
        )
        reset_url = f"{reset_url_base}?token={quote_plus(reset_token)}"

        try:
            send_support_admin_reset_email(admin.email, reset_url)
        except Exception as exc:
            logger.exception('Failed to send support admin password reset email: %s', exc)

        return Response(
            {'message': 'If a matching user exists, a password reset email has been sent.'},
            status=status.HTTP_200_OK,
        )


class AdminResetPasswordView(APIView):
    """
    POST /api/v1/support-admin/auth/reset-password/
    Reset an admin password using a valid reset token.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AdminResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token = serializer.validated_data['token']
        try:
            payload = decode_admin_token(token)
        except jwt.ExpiredSignatureError:
            return Response({'message': 'Reset token has expired.'}, status=status.HTTP_401_UNAUTHORIZED)
        except jwt.InvalidTokenError:
            return Response({'message': 'Invalid reset token.'}, status=status.HTTP_400_BAD_REQUEST)

        if payload.get('type') != 'reset':
            return Response({'message': 'Invalid reset token type.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            admin = AdminUser.objects.get(admin_id=payload['admin_id'], email=payload.get('email'), is_active=True)
        except AdminUser.DoesNotExist:
            return Response({'message': 'Admin user not found or inactive.'}, status=status.HTTP_404_NOT_FOUND)

        admin.set_password(serializer.validated_data['password'])
        admin.save(update_fields=['password', 'updated_at'])

        return Response({'message': 'Password has been reset successfully.'}, status=status.HTTP_200_OK)


def send_admin_reply_email(user_email, subject, connection_name, reply_message, ticket_id, source_type=None):
    from_email = settings.DEFAULT_FROM_EMAIL or 'no-reply@datasyncher.com'
    email_subject = f'Support Update: {subject}'
    from html import escape

    parts = [p for p in [source_type, connection_name] if p]
    connection_info = f' ({" - ".join(parts)})' if parts else ''
    reply_message_html = escape(reply_message or "").replace('\n', '<br>')
    reply_message_html = f"{reply_message_html}<br><br><span style=\"display:block; text-align:left; font-size:13px; line-height:1.5;\">Thanks.<br>Datasyncher support team</span>"

    text_content = f"""Hello,

The support team has replied to your ticket #{ticket_id}.

Ticket: {subject}{connection_info}

Reply:
{reply_message}

Log in to view the full conversation.

Reply from Datasyncher Support Admin

Thanks.
Datasyncher support team
"""
    html_content = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#ffffff;font-family:Arial,sans-serif;color:#111;">
<table width="100%" cellspacing="0" cellpadding="0" style="padding:24px;">
<tr><td align="center">
<table width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#ffffff;border:1px solid #E5E7EB;border-radius:10px;padding:24px;">
<tr><td style="font-size:18px;font-weight:700;">{ticket_id}&nbsp;&nbsp;{subject}&nbsp;&nbsp;<span style="font-size:14px;font-weight:400;color:#6B7280;">{connection_info}</span></td></tr>
<tr><td height="16"></td></tr>
<tr><td style="font-size:14px;line-height:1.7;color:#374151;background:#F9FAFB;padding:16px;border-radius:8px;white-space:pre-wrap;word-break:break-word;">{reply_message_html}</td></tr>
<tr><td height="24"></td></tr>
<tr><td style="font-size:12px;color:#9CA3AF;text-align:center;border-top:1px solid #E5E7EB;padding-top:16px;">Reply from Datasyncher Support Admin</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
    email = EmailMultiAlternatives(
        subject=email_subject,
        body=text_content,
        from_email=from_email,
        to=[user_email],
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)


def send_support_admin_reset_email(recipient_email, reset_url):
    from_email = settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER or 'no-reply@datasyncher.com'
    subject = 'Datasyncher support admin password reset'
    text_content = f"""
Hello,

A password reset request was received for your support admin account.

Use the link below to set a new password:
{reset_url}

If you did not request this, you can safely ignore this email.

Thanks,
Datasyncher Support Team
"""
    html_content = f"""
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#ffffff; font-family:Arial, sans-serif; color:#111111;">
<table width="100%" cellspacing="0" cellpadding="0" style="padding:24px;">
<tr><td align="center">
<table width="100%" cellspacing="0" cellpadding="0" style="max-width:600px; background:#ffffff; border:1px solid #E5E7EB; border-radius:10px; padding:24px;">
<tr><td style="font-size:20px; font-weight:700; text-align:center;">Reset your support admin password</td></tr>
<tr><td height="18"></td></tr>
<tr><td style="font-size:14px; line-height:1.7; color:#374151;">We received a request to reset the password for your Datasyncher support admin account.</td></tr>
<tr><td height="22"></td></tr>
<tr><td align="center"><a href="{reset_url}" style="background:#2563EB; color:#ffffff; padding:14px 24px; text-decoration:none; border-radius:8px; display:inline-block; font-weight:600;">Reset Password</a></td></tr>
<tr><td height="22"></td></tr>
<tr><td style="font-size:13px; line-height:1.7; color:#6B7280;">This link expires in 60 minutes and can only be used once.<br><br>If you did not request this password reset, please ignore this message.</td></tr>
<tr><td height="24"></td></tr>
<tr><td style="font-size:12px; color:#9CA3AF; text-align:center; border-top:1px solid #E5E7EB; padding-top:16px;">Thanks,<br>Datasyncher Support Team</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=[recipient_email],
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)


def send_new_user_access_email(
    recipient_email,
    username,
    temporary_password,
    login_url,
    trial_end_date=None,
):
    """Send the one-time credentials for an admin-created account."""
    from_email = (
        settings.DEFAULT_FROM_EMAIL
        or settings.EMAIL_HOST_USER
        or 'no-reply@datasyncher.com'
    )
    subject = 'Your Datasyncher account is ready'
    username_html = escape(username)
    temporary_password_html = escape(temporary_password)
    login_url_html = escape(login_url)
    trial_expiry = (
        _format_email_datetime(trial_end_date)
        if trial_end_date
        else '14 days after your account was created'
    )
    trial_expiry_html = escape(trial_expiry)
    text_content = f"""
Hello,

Your Datasyncher account has been created.

Login URL: {login_url}
Username: {username}
Temporary password: {temporary_password}

You will be required to choose a new password when you first log in.
Do not share these credentials with anyone.

Your free trial expires on {trial_expiry}.
To continue using Datasyncher after the trial, choose a subscription plan before this date.

Thanks,
Datasyncher Support Team
support1@datasyncher.com
"""
    html_content = f"""
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#ffffff; font-family:'Plus Jakarta Sans', Arial, sans-serif; color:#09090B;">
<table width="100%" cellspacing="0" cellpadding="0" style="padding:24px;">
<tr><td align="center">
<table width="480" cellspacing="0" cellpadding="0" style="background:#ffffff; border:1px solid #E4E4E7; border-radius:10px; padding:28px;">
<tr><td style="font-size:18px; font-weight:600; text-align:center;">Welcome to Datasyncher</td></tr>
<tr><td height="14"></td></tr>
<tr><td style="font-size:14px; line-height:1.6;">Your Datasyncher account has been created. Use the temporary credentials below to log in.</td></tr>
<tr><td height="18"></td></tr>
<tr><td style="font-size:14px; line-height:1.9; color:#09090B; background:#F4F4F5; padding:16px; border-radius:6px;"><strong>Username:</strong> {username_html}<br><strong>Temporary password:</strong> {temporary_password_html}</td></tr>
<tr><td height="22"></td></tr>
<tr><td align="center"><a href="{login_url_html}" style="background:#2563EB; color:#ffffff; padding:12px 22px; text-decoration:none; border-radius:6px; display:inline-block; font-size:14px; font-weight:600;">Log in to Datasyncher</a></td></tr>
<tr><td height="22"></td></tr>
<tr><td style="font-size:13px; line-height:1.6; color:#52525B;">You will be required to choose a new password when you first log in.<br><br>For security reasons, do not share these credentials with anyone.</td></tr>
<tr><td height="18"></td></tr>
<tr><td style="font-size:13px; line-height:1.6; color:#3F3F46; background:#EFF6FF; border-left:3px solid #2563EB; padding:14px;"><strong>Your free trial expires on {trial_expiry_html}.</strong><br>To continue using Datasyncher after the trial, choose a subscription plan before this date.</td></tr>
<tr><td height="26"></td></tr>
<tr><td style="font-size:12px; color:#71717A; text-align:center; border-top:1px solid #E4E4E7; padding-top:14px;">Datasyncher Support Team<br>support1@datasyncher.com</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=[recipient_email],
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)


def _send_new_user_access_email_safely(
    recipient_email,
    username,
    temporary_password,
    login_url,
    trial_end_date,
):
    try:
        send_new_user_access_email(
            recipient_email,
            username,
            temporary_password,
            login_url,
            trial_end_date,
        )
    except Exception:
        logger.exception(
            'Failed to send new user access email to %s',
            recipient_email,
        )


def _format_email_datetime(value):
    """Return a human-readable expiration timestamp in the configured timezone."""
    if timezone.is_aware(value):
        value = timezone.localtime(value)

    time_text = value.strftime('%I:%M %p').lstrip('0')
    timezone_name = value.tzname()
    if timezone_name:
        time_text = f'{time_text} {timezone_name}'

    return f'{value.strftime("%B")} {value.day}, {value.year} at {time_text}'


def send_trial_extension_email(
    recipient_email,
    days_added,
    trial_end_date,
    login_url,
):
    """Notify a registered user that their Datasyncher trial was extended."""
    from_email = (
        settings.DEFAULT_FROM_EMAIL
        or settings.EMAIL_HOST_USER
        or 'no-reply@datasyncher.com'
    )
    subject = 'Your Datasyncher trial has been extended'
    day_label = 'day' if days_added == 1 else 'days'
    trial_expiry = _format_email_datetime(trial_end_date)
    trial_expiry_html = escape(trial_expiry)
    login_url_html = escape(login_url)
    text_content = f"""
Hello,

Good news! Your Datasyncher trial has been extended by {days_added} {day_label}.

Your new trial expiration date is {trial_expiry}.

Log in to continue using Datasyncher:
{login_url}

Thanks,
Datasyncher Support Team
support1@datasyncher.com
"""
    html_content = f"""
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#ffffff; font-family:'Plus Jakarta Sans', Arial, sans-serif; color:#09090B;">
<table width="100%" cellspacing="0" cellpadding="0" style="padding:24px;">
<tr><td align="center">
<table width="480" cellspacing="0" cellpadding="0" style="background:#ffffff; border:1px solid #E4E4E7; border-radius:10px; padding:28px;">
<tr><td style="font-size:18px; font-weight:600; text-align:center;">Your Trial Has Been Extended</td></tr>
<tr><td height="14"></td></tr>
<tr><td style="font-size:14px; line-height:1.6;">Good news! We added <strong>{days_added} {day_label}</strong> to your Datasyncher trial.</td></tr>
<tr><td height="18"></td></tr>
<tr><td style="font-size:13px; line-height:1.6; color:#3F3F46; background:#EFF6FF; border-left:3px solid #2563EB; padding:14px;"><strong>New trial expiration date</strong><br>{trial_expiry_html}</td></tr>
<tr><td height="22"></td></tr>
<tr><td align="center"><a href="{login_url_html}" style="background:#2563EB; color:#ffffff; padding:12px 22px; text-decoration:none; border-radius:6px; display:inline-block; font-size:14px; font-weight:600;">Log in to Datasyncher</a></td></tr>
<tr><td height="22"></td></tr>
<tr><td style="font-size:13px; line-height:1.6; color:#52525B;">You can continue using Datasyncher through the new expiration date shown above.</td></tr>
<tr><td height="26"></td></tr>
<tr><td style="font-size:12px; color:#71717A; text-align:center; border-top:1px solid #E4E4E7; padding-top:14px;">Datasyncher Support Team<br>support1@datasyncher.com</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=[recipient_email],
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)


def _send_trial_extension_email_safely(
    recipient_email,
    days_added,
    trial_end_date,
    login_url,
):
    try:
        send_trial_extension_email(
            recipient_email,
            days_added,
            trial_end_date,
            login_url,
        )
    except Exception:
        logger.exception(
            'Failed to send trial extension email to %s',
            recipient_email,
        )


class AdminTokenRefreshView(APIView):
    """
    POST /api/v1/support-admin/auth/refresh/
    Refresh the admin access token using a valid refresh token.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_token = request.data.get('refresh_token')
        if not refresh_token:
            return Response({'message': 'refresh_token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = decode_admin_token(refresh_token)
        except jwt.ExpiredSignatureError:
            return Response({'message': 'Refresh token has expired. Please log in again.'}, status=status.HTTP_401_UNAUTHORIZED)
        except jwt.InvalidTokenError:
            return Response({'message': 'Invalid refresh token.'}, status=status.HTTP_401_UNAUTHORIZED)

        if payload.get('type') != 'refresh':
            return Response({'message': 'Invalid token type.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            admin = AdminUser.objects.get(admin_id=payload['admin_id'], is_active=True)
        except AdminUser.DoesNotExist:
            return Response({'message': 'Admin user not found or inactive.'}, status=status.HTTP_401_UNAUTHORIZED)

        access_token, new_refresh_token = generate_admin_tokens(admin)

        return Response({
            'access_token': access_token,
            'refresh_token': new_refresh_token,
        }, status=status.HTTP_200_OK)


class AdminMeView(APIView):
    """
    GET /api/v1/support-admin/auth/me/
    Returns the currently logged-in admin's profile.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request):
        return Response(AdminUserSerializer(request.user).data, status=status.HTTP_200_OK)


class AdminRegistrationRequestListView(APIView):
    """
    GET /api/v1/support-admin/registration-requests/
    Return registration requests to authenticated support admins.
    """
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        registration_requests = RegistrationRequest.objects.select_related(
            'company',
        )
        serializer = AdminRegistrationRequestSerializer(
            registration_requests,
            many=True,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminRegistrationRequestActionView(APIView):
    """Approve or reject a pending registration request."""
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def patch(self, request, registration_request_id):
        action_serializer = AdminRegistrationRequestActionSerializer(
            data=request.data,
        )
        action_serializer.is_valid(raise_exception=True)
        action = action_serializer.validated_data['action']

        with transaction.atomic():
            registration_request = get_object_or_404(
                RegistrationRequest.objects.select_for_update(),
                registration_request_id=registration_request_id,
            )

            if registration_request.status is not None:
                return Response(
                    {'error': 'This registration request has already been processed.'},
                    status=status.HTTP_409_CONFLICT,
                )

            if action == 'reject_access':
                registration_request.status = (
                    RegistrationRequest.Status.ACCESS_REJECTED
                )
                registration_request.save(
                    update_fields=['status'],
                )

                return Response(
                    {
                        'message': 'Access request rejected.',
                        'registration_request': AdminRegistrationRequestSerializer(
                            registration_request,
                        ).data,
                    },
                    status=status.HTTP_200_OK,
                )

            email = registration_request.email.strip().lower()
            password = action_serializer.validated_data['password']

            if User.objects.filter(company_email__iexact=email).exists():
                return Response(
                    {'email': 'A user with this email already exists.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            administrator_role = Role.objects.filter(
                role_name='Administrator',
            ).first()
            if administrator_role is None:
                return Response(
                    {'error': 'Administrator role does not exist.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            password_user = User(
                first_name=registration_request.first_name,
                last_name=registration_request.last_name,
                email=email,
                company_email=email,
            )
            try:
                validate_password(password, password_user)
            except DjangoValidationError as error:
                return Response(
                    {'password': error.messages},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            start_date = timezone.now()
            company = Company(
                start_date=start_date,
                end_date=start_date + timedelta(days=14),
                is_trial=True,
            )
            company.data = registration_request.company_name
            company.save()

            user = User(
                first_name=registration_request.first_name,
                last_name=registration_request.last_name,
                email=email,
                company_email=email,
                company_name=registration_request.company_name,
                phone=registration_request.phone_number,
                cmp=company,
                roles=administrator_role,
                is_active=True,
                is_superuser=True,
                password_reset_required=True,
            )
            user.set_password(password)
            user.save()

            company.created_by = user
            company.updated_by = user
            company.save(update_fields=['created_by', 'updated_by'])

            registration_request.status = (
                RegistrationRequest.Status.ACCESS_PROVIDED
            )
            registration_request.company = company
            registration_request.access_provided_at = timezone.now()
            registration_request.save(
                update_fields=['status', 'company', 'access_provided_at'],
            )

            login_url = (
                f"{getattr(settings, 'FRONTEND_URL', 'https://datasyncher.com').rstrip('/')}"
                '/auth/login'
            )
            transaction.on_commit(
                lambda: _send_new_user_access_email_safely(
                    recipient_email=user.email,
                    username=user.company_email,
                    temporary_password=password,
                    login_url=login_url,
                    trial_end_date=company.end_date,
                )
            )

        return Response(
            {
                'message': 'Access provided successfully.',
                'user_id': user.user_id,
                'username': user.company_email,
                'registration_request': AdminRegistrationRequestSerializer(
                    registration_request,
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminRegistrationRequestExtendEndDateView(APIView):
    """
    POST /api/v1/support-admin/registration-requests/<registration_request_id>/extend-end-date/

    Extends the linked Company's end_date by N days.
    - If Expired (is_trial=True and company.end_date <= now): new end_date = now + days
    - If Access Provided / active: new end_date = company.end_date + days
    - Emails the registered address with the updated trial expiration date
    """
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, registration_request_id):
        registration_request = RegistrationRequest.objects.select_related('company').filter(
            registration_request_id=registration_request_id
        ).first()
        if not registration_request:
            return Response({'message': 'Registration request not found.'}, status=status.HTTP_404_NOT_FOUND)

        company = registration_request.company
        if not company:
            return Response(
                {'message': 'No company linked to this registration request.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only Access Provided / Expired rows show an Extend button — guard against
        # Pending / Access Rejected being hit directly via API.
        if registration_request.status != RegistrationRequest.Status.ACCESS_PROVIDED:
            return Response(
                {'message': 'Only requests with access provided can have their end date extended.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AdminRegistrationRequestExtendEndDateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        days = serializer.validated_data['days']

        now = timezone.now()
        is_expired = bool(company.is_trial and company.end_date and company.end_date <= now)

        if is_expired:
            new_end_date = now + timedelta(days=days)
        else:
            new_end_date = (company.end_date or now) + timedelta(days=days)

        company.end_date = new_end_date
        company.save(update_fields=['end_date', 'updated_at'])

        login_url = (
            f"{getattr(settings, 'FRONTEND_URL', 'https://datasyncher.com').rstrip('/')}"
            '/auth/login'
        )
        transaction.on_commit(
            lambda: _send_trial_extension_email_safely(
                recipient_email=registration_request.email,
                days_added=days,
                trial_end_date=new_end_date,
                login_url=login_url,
            )
        )

        return Response(
            {
                'message': 'End date extended successfully.',
                'registration_request_id': registration_request.registration_request_id,
                'cmp_id': company.cmp_id,
                'was_expired': is_expired,
                'days_added': days,
                'end_date': company.end_date,
                'registration_request': AdminRegistrationRequestSerializer(registration_request).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminCompanyListView(APIView):
    """
    GET /api/v1/support-admin/companies/
    Return all companies for support admins.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request):
        qs = Company.objects.all().order_by('-created_at')
        serializer = AdminCompanyListSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminCompanyDestinationListView(APIView):
    """
    GET /api/v1/support-admin/companies/<cmp_id>/destinations/
    Return all destination configs for a company.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request, cmp_id):
        company = Company.objects.filter(cmp_id=cmp_id).first()
        if not company:
            return Response({'message': 'Company not found.'}, status=status.HTTP_404_NOT_FOUND)

        destinations = (
            DestinationConfig.objects.filter(cmp=company)
            .select_related('dst', 'cmp', 'cmp__created_by', 'cmp__updated_by', 'created_by', 'updated_by')
            .order_by('-updated_at')
        )
        content = AdminDestinationListSerializer(destinations, many=True).data
        return Response(
            {
                'content': content,
                'totalElements': len(content),
                'size': len(content),
                'cmp_id': company.cmp_id,
                'company_name': company.data,
            },
            status=status.HTTP_200_OK,
        )


class AdminCompanyDestinationDetailView(APIView):
    """
    GET /api/v1/support-admin/companies/<cmp_id>/destinations/<dst_config_id>/
    Return one destination config with fields.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request, cmp_id, dst_config_id):
        destination = DestinationConfig.objects.select_related(
            'dst',
            'cmp',
            'cmp__created_by',
            'cmp__updated_by',
            'created_by',
            'updated_by',
        ).filter(
            cmp_id=cmp_id,
            dst_config_id=dst_config_id,
        ).first()
        if not destination:
            return Response({'message': 'Destination not found.'}, status=status.HTTP_404_NOT_FOUND)

        return Response(AdminDestinationDetailSerializer(destination).data, status=status.HTTP_200_OK)


class AdminCompanyDestinationTestView(APIView):
    """
    POST /api/v1/support-admin/companies/<cmp_id>/destinations/<dst_config_id>/test/
    Test a destination configuration using saved config or request payload.
    """
    authentication_classes = [AdminJWTAuthentication]

    def post(self, request, cmp_id, dst_config_id):
        destination = DestinationConfig.objects.select_related('dst', 'cmp').filter(
            cmp_id=cmp_id,
            dst_config_id=dst_config_id,
        ).first()
        if not destination:
            return Response({'message': 'Destination not found.'}, status=status.HTTP_404_NOT_FOUND)

        config_data = request.data.get('config_data')
        if not config_data:
            # Support flat form payloads from the frontend as well as nested config_data payloads.
            excluded_keys = {
                'config_data',
                'cmp_id',
                'dst_config_id',
                'destination_name',
                'validate_destination_schema',
            }
            config_data = {
                key: value
                for key, value in request.data.items()
                if key not in excluded_keys
            }
            if not config_data:
                config_data = destination.data

        if isinstance(config_data, str):
            try:
                config_data = json.loads(config_data)
            except Exception:
                config_data = {}

        if not isinstance(config_data, dict) or not config_data:
            return Response({'message': 'config_data must be a non-empty dictionary.'}, status=status.HTTP_400_BAD_REQUEST)

        dest_name = destination.dst.name if destination.dst else None
        if not dest_name:
            return Response({'message': 'Destination type not found.'}, status=status.HTTP_400_BAD_REQUEST)

        if dest_name == 'Snowflake':
            config_data = dict(config_data)
            config_data.setdefault('validate_destination_schema', False)

        success, message, response_data = _test_destination_connection(dest_name, config_data)
        status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
        payload = {
            'success': success,
            'message': message,
            'reason': message,
            'destination': destination.name,
            'destination_type': dest_name,
        }
        if response_data is not None:
            payload['response'] = response_data
        return Response(payload, status=status_code)


class AdminCompanyConnectionTestView(APIView):
    """
    POST /api/v1/support-admin/companies/<cmp_id>/connections/<connection_id>/test/
    Test the saved source and destination configs for a connection.
    """
    authentication_classes = [AdminJWTAuthentication]

    def post(self, request, cmp_id, connection_id):
        company = Company.objects.filter(cmp_id=cmp_id).first()
        if not company:
            return Response({'message': 'Company not found.'}, status=status.HTTP_404_NOT_FOUND)

        connection = (
            Connection.objects.filter(cmp_id=cmp_id, connection_id=connection_id)
            .exclude(status='D')
            .select_related('cmp', 'src_config__src', 'dst_config__dst')
            .first()
        )
        if not connection:
            return Response({'message': 'Connection not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not connection.src_config or not connection.dst_config:
            return Response(
                {'success': False, 'message': 'Configuration not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source_config = _coerce_json_dict(connection.src_config.data)
        destination_config = _coerce_json_dict(connection.dst_config.data)
        source_name = connection.src_config.src.name if connection.src_config.src else None
        dest_name = connection.dst_config.dst.name if connection.dst_config.dst else None

        if not source_config or not destination_config:
            return Response(
                {'success': False, 'message': 'Configuration not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not source_name or not dest_name:
            return Response(
                {'success': False, 'message': 'Source or destination type not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source_api_url = f'https://{settings.DOMAIN}/api-new/{source_name}-Authorization'
        source_payload = {source_name: source_config}
        try:
            source_response = requests.post(
                source_api_url,
                data=json.dumps(source_payload),
                headers=internal_service_headers(),
                verify=False,
                timeout=30,
            )
        except requests.exceptions.Timeout:
            return Response(
                {'success': False, 'message': 'Source connection test timed out. Please check your network connection.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except requests.exceptions.RequestException as exc:
            return Response(
                {'success': False, 'message': f'Failed to connect to authorization service: {str(exc)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source_failed, source_error, _ = _extract_backend_response_error(
            source_response,
            fallback_prefix='Source configuration test failed',
        )
        if source_failed:
            error_message = source_error or 'Source configuration test failed'
            if source_name == 'Snowflake':
                error_message = _standardize_destination_error_message(source_name, error_message)
            return Response(
                {
                    'success': False,
                    'message': error_message,
                    'source': source_name,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        destination_payload = {dest_name: destination_config}
        if dest_name == 'Snowflake':
            dst_schema = str(connection.dst_schema or '').strip()
            if dst_schema:
                destination_payload['destination_schema'] = dst_schema

        try:
            destination_response = requests.post(
                f'https://{settings.DOMAIN}/api-new/{dest_name}-Authorization',
                data=json.dumps(destination_payload),
                headers=internal_service_headers(),
                verify=False,
                timeout=30,
            )
        except requests.exceptions.Timeout:
            return Response(
                {'success': False, 'message': 'Destination connection test timed out. Please check your network connection.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except requests.exceptions.RequestException as exc:
            return Response(
                {'success': False, 'message': f'Failed to connect to authorization service: {str(exc)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        destination_failed, destination_error, destination_response_data = _extract_backend_response_error(
            destination_response,
            fallback_prefix='Destination configuration test failed',
        )
        if destination_failed:
            error_message = destination_error or 'Destination configuration test failed'
            if dest_name == 'Snowflake':
                error_message = _standardize_destination_error_message(dest_name, error_message)
            return Response(
                {
                    'success': False,
                    'message': error_message,
                    'source': source_name,
                    'destination': dest_name,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if dest_name == 'Snowflake' and isinstance(destination_response_data, dict):
            normalized_dst_schema = destination_response_data.get('normalized_destination_schema')
            existing_dst_schema = str(connection.dst_schema or '').strip()
            if normalized_dst_schema and normalized_dst_schema != existing_dst_schema:
                connection.dst_schema = normalized_dst_schema
                connection.save(update_fields=['dst_schema'])

        return Response(
            {
                'success': True,
                'message': 'Both source and destination configurations are valid.',
                'source': source_name,
                'destination': dest_name,
            },
            status=status.HTTP_200_OK,
        )


class AdminCompanyConnectionsView(APIView):
    """
    GET /api/v1/support-admin/companies/<cmp_id>/connections/
    Return all connections for a company, including schema rows.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request, cmp_id):
        company = Company.objects.filter(cmp_id=cmp_id).first()
        if not company:
            return Response({'message': 'Company not found.'}, status=status.HTTP_404_NOT_FOUND)

        qs = list(
            Connection.objects.filter(cmp_id=cmp_id)
            .exclude(status='D')
            .select_related(
                'cmp',
                'cmp__created_by',
                'cmp__updated_by',
                'src_config__src',
                'dst_config__dst',
                'dst_config__created_by',
                'dst_config__updated_by',
                'created_by',
                'updated_by',
            )
            .order_by('-created_at')
        )
        connection_context = _build_connection_list_context(qs)
        serializer = AdminCompanyConnectionSerializer(
            qs,
            many=True,
            context={
                'migration_statuses': connection_context.get('migration_statuses', {}),
            },
        )
        data = serializer.data
        return Response(
            {
                'cmp_id': company.cmp_id,
                'company_name': company.data,
                'connections': data,
                'content': data,
                'totalElements': len(data),
                'size': len(data),
            },
            status=status.HTTP_200_OK,
        )


class AdminSelectedTablesView(APIView):
    """
    GET /api/v1/support-admin/companies/<cmp_id>/connections/<connection_id>/selected-tables/
    Return only selected table names and their last synced timestamps.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request, cmp_id, connection_id):
        company = Company.objects.filter(cmp_id=cmp_id).first()
        if not company:
            return Response({'message': 'Company not found.'}, status=status.HTTP_404_NOT_FOUND)

        connection = (
            Connection.objects.filter(cmp_id=cmp_id, connection_id=connection_id)
            .exclude(status='D')
            .select_related('cmp', 'src_config__src', 'dst_config__dst')
            .first()
        )
        if not connection:
            return Response({'message': 'Connection not found.'}, status=status.HTTP_404_NOT_FOUND)

        selected_tables = list(
            SourceTableSelection.objects.filter(connection=connection, selected=True)
            .order_by('sequence', 'tbl_id')
        )
        serializer = AdminConnectionSchemaSerializer(selected_tables, many=True)
        return Response(
            {
                'connector_name': connection.src_config.name if connection.src_config else None,
                'destination_name': connection.dst_config.name if connection.dst_config else None,
                'selected_tables': serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, cmp_id, connection_id):
        company = Company.objects.filter(cmp_id=cmp_id).first()
        if not company:
            return Response({'message': 'Company not found.'}, status=status.HTTP_404_NOT_FOUND)

        connection = (
            Connection.objects.filter(cmp_id=cmp_id, connection_id=connection_id)
            .exclude(status='D')
            .select_related('cmp', 'src_config__src', 'dst_config__dst')
            .first()
        )
        if not connection:
            return Response({'message': 'Connection not found.'}, status=status.HTTP_404_NOT_FOUND)

        if _is_selected_tables_update_blocked(connection):
            return Response(
                {
                    'message': 'Selected tables timestamps cannot be updated while loading is in progress.',
                    'error_type': 'loading_in_progress',
                },
                status=status.HTTP_409_CONFLICT,
            )

        offset_value = request.data.get('offset_hours', 0)
        try:
            offset_delta = _parse_offset_duration(offset_value)
        except (TypeError, ValueError):
            return Response(
                {'message': 'offset_hours must be a number or HH:MM:SS duration.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        direction = str(request.data.get('direction', 'subtract')).strip().lower()
        if direction not in ('subtract', 'add'):
            return Response(
                {'message': "direction must be 'subtract' or 'add'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        selected_qs = SourceTableSelection.objects.filter(connection=connection, selected=True).order_by('sequence', 'tbl_id')
        selected_tables = list(selected_qs)
        if not selected_tables:
            return Response(
                {
                    'connector_name': connection.src_config.name if connection.src_config else None,
                    'destination_name': connection.dst_config.name if connection.dst_config else None,
                    'selected_tables': [],
                },
                status=status.HTTP_200_OK,
            )

        table_id = request.data.get('table_id')
        table_ids = _parse_int_list(request.data.get('table_ids'))

        if table_id not in (None, ""):
            try:
                target_table_ids = [int(table_id)]
            except (TypeError, ValueError):
                return Response({'message': 'table_id must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
        elif table_ids:
            target_table_ids = table_ids
        else:
            target_table_ids = None

        if target_table_ids is None:
            return Response(
                {
                    'message': 'table_id or table_ids is required.',
                    'error_type': 'table_ids_required',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_tables = [table_obj for table_obj in selected_tables if table_obj.tbl_id in set(target_table_ids)]
        missing_ids = sorted(set(target_table_ids) - {table_obj.tbl_id for table_obj in target_tables})
        if missing_ids:
            return Response(
                {
                    'message': (
                        'Selected table(s) not found for this connection. '
                        'Please refresh the selected tables and try again.'
                    ),
                    'error_type': 'selected_table_not_found',
                    'missing_table_ids': missing_ids,
                },
                    status=status.HTTP_404_NOT_FOUND,
                )

        confirm_raw = request.data.get('confirm', False)
        if isinstance(confirm_raw, str):
            confirm = confirm_raw.strip().lower() in ('1', 'true', 'yes', 'y', 'on')
        else:
            confirm = bool(confirm_raw)

        response_field_name = 'last_synced' if confirm else 'updated_sync_time'
        preview_rows = []
        for table_obj in target_tables:
            parsed_last_synced = _parse_timestamp(table_obj.last_synced)
            if not parsed_last_synced:
                preview_rows.append(
                    {
                        'tbl_id': table_obj.tbl_id,
                        'table': table_obj.table,
                        response_field_name: table_obj.last_synced,
                    }
                )
                continue

            shifted_last_synced = (parsed_last_synced + offset_delta) if direction == 'add' else (parsed_last_synced - offset_delta)
            preview_rows.append(
                {
                    'tbl_id': table_obj.tbl_id,
                    'table': table_obj.table,
                    response_field_name: _format_timestamp(shifted_last_synced),
                }
            )

        if confirm:
            for table_obj in target_tables:
                parsed_last_synced = _parse_timestamp(table_obj.last_synced)
                if not parsed_last_synced:
                    continue
                shifted_last_synced = (parsed_last_synced + offset_delta) if direction == 'add' else (parsed_last_synced - offset_delta)
                table_obj.last_synced = _format_timestamp(shifted_last_synced)
                table_obj.account_type = 'admin'
                table_obj.updated_by_id = getattr(request.user, 'admin_id', None)
                table_obj.save(update_fields=['last_synced', 'account_type', 'updated_by_id'])

        return Response(
            {
                'connector_name': connection.src_config.name if connection.src_config else None,
                'destination_name': connection.dst_config.name if connection.dst_config else None,
                'selected_tables': preview_rows,
                'selected_table_field': response_field_name,
                'offset_hours': offset_value,
                'direction': direction,
                'updated_count': len(preview_rows),
                'update_scope': 'subset' if len(target_tables) > 1 else 'one',
                'confirmed': confirm,
                'preview_only': not confirm,
            },
            status=status.HTTP_200_OK,
        )


class AdminAddOffsetHoursView(AdminSelectedTablesView):
    """
    POST /api/v1/support-admin/companies/<cmp_id>/connections/<connection_id>/add-offset-hours/
    Adds offset hours to last_synced. Equivalent to AdminSelectedTablesView POST with direction='add'.
    """

    def post(self, request, cmp_id, connection_id):
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        data['direction'] = 'add'
        request._full_data = data
        return super().post(request, cmp_id, connection_id)


class AdminSubtractOffsetHoursView(AdminSelectedTablesView):
    """
    POST /api/v1/support-admin/companies/<cmp_id>/connections/<connection_id>/subtract-offset-hours/
    Subtracts offset hours from last_synced. Equivalent to AdminSelectedTablesView POST with direction='subtract'.
    """

    def post(self, request, cmp_id, connection_id):
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        data['direction'] = 'subtract'
        request._full_data = data
        return super().post(request, cmp_id, connection_id)


class AdminCompanyBillingView(APIView):
    """
    GET /api/v1/support-admin/companies/<cmp_id>/billing/
    Return billing details for a company.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request, cmp_id):
        company = Company.objects.filter(cmp_id=cmp_id).first()
        if not company:
            return Response({'message': 'Company not found.'}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                'cmp_id': company.cmp_id,
                'company_name': company.data,
                'billing_details': AdminCompanyBillingSerializer(company).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminCompanyInvoicesView(APIView):
    """
    GET /api/v1/support-admin/companies/<cmp_id>/invoices/
    Return invoice details for a company.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request, cmp_id):
        company = Company.objects.filter(cmp_id=cmp_id).first()
        if not company:
            return Response({'message': 'Company not found.'}, status=status.HTTP_404_NOT_FOUND)

        invoices = Invoice.objects.filter(cmp=company).order_by('-created_at')
        return Response(
            {
                'cmp_id': company.cmp_id,
                'company_name': company.data,
                'invoice_details': AdminInvoiceSerializer(invoices, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminCompanyBillingSummaryView(APIView):
    """
    GET /api/v1/support-admin/companies/<cmp_id>/billing-summary/
    Return billing usage rows and invoices for a company.
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request, cmp_id):
        company = get_object_or_404(Company, cmp_id=cmp_id)
        today = date.today()
        current_month = today.month
        current_year = today.year

        num_days = calendar.monthrange(current_year, current_month)[1]
        labels = [str(day) for day in range(1, num_days + 1)]
        current_month_billing = [0] * num_days

        qs = Data_Transfer.objects.filter(
            start_time__year=current_year,
            start_time__month=current_month,
            connection__cmp_id=cmp_id,
        ).annotate(
            day=ExtractDay('start_time'),
            total_records=Coalesce(F('new_rec'), 0) + Coalesce(F('mod_rec'), 0),
        ).values('day').annotate(
            total_records_sum=Sum('total_records')
        )

        for record in qs:
            day_index = record['day'] - 1
            records = record['total_records_sum'] or 0
            ref_date = date(current_year, current_month, record['day'])
            current_month_billing[day_index] = float(get_dynamic_billing_amount(records, reference_date=ref_date))

        invoices = Invoice.objects.filter(cmp=company).order_by('-created_at')
        billing_details = []
        monthly_details = []
        connection_usage_qs = (
            Connection.objects.filter(cmp_id=cmp_id)
            .select_related('src_config__src', 'dst_config__dst')
            .annotate(total_usage=Coalesce(Sum('connection_transfers__total_rec'), 0))
            .order_by('-created_at')
        )
        connection_usage = [
            {
                'connection_id': connection.connection_id,
                'source_name': connection.src_config.src.name if connection.src_config and connection.src_config.src else None,
                'destination_name': connection.dst_config.dst.name if connection.dst_config and connection.dst_config.dst else None,
                'source_title': connection.src_config.name if connection.src_config else None,
                'destination_title': connection.dst_config.name if connection.dst_config else None,
                'total_usage': connection.total_usage or 0,
            }
            for connection in connection_usage_qs
        ]

        start_date = None
        end_date = (today.replace(day=1) - relativedelta(days=1)).replace(day=1)

        if invoices.exists():
            invoice_billing_end = as_date(invoices.first().billing_end_date)
            start_date = invoice_billing_end + relativedelta(days=1) if invoice_billing_end else None
        elif company.billing_start_date:
            company_billing_start = as_date(company.billing_start_date)
            start_date = company_billing_start.replace(day=1)
            if company.billing_end_date:
                company_billing_end = as_date(company.billing_end_date)
                end_date = company_billing_end.replace(day=1)

        start_date = as_date(start_date)
        end_date = as_date(end_date)

        if start_date and end_date and start_date < end_date:
            current_pointer = start_date.replace(day=1)
            while current_pointer <= end_date:
                next_month = current_pointer + relativedelta(months=1)
                monthly_records = Data_Transfer.objects.filter(
                    connection__cmp_id=cmp_id,
                    start_time__gte=current_pointer,
                    start_time__lt=next_month,
                ).annotate(
                    total_records=Coalesce(F('new_rec'), 0) + Coalesce(F('mod_rec'), 0)
                ).aggregate(total_usage=Sum('total_records'))

                usage = monthly_records['total_usage'] or 0
                if usage >= 1000:
                    amount = get_dynamic_billing_amount(usage, reference_date=current_pointer)
                    billing_details.append({
                        'period': current_pointer.strftime('%B %Y'),
                        'month': current_pointer.month,
                        'year': current_pointer.year,
                        'total_usage': round(usage, 2),
                        'total_unit': round(float(usage) / 1000, 2),
                        'amount': round(float(amount), 2),
                    })

                current_pointer = next_month
        else:
            billing_details_query = (
                Data_Transfer.objects
                .filter(connection__cmp_id=cmp_id)
                .annotate(
                    month=TruncMonth('start_time'),
                    total_records=Coalesce(F('new_rec'), 0) + Coalesce(F('mod_rec'), 0),
                )
                .values('month')
                .annotate(total_usage=Sum('total_records'))
                .order_by('-month')
            )

            for detail in billing_details_query:
                usage = detail['total_usage'] or 0
                if usage < 1000:
                    continue
                ref_date = as_date(detail['month'])
                amount = get_dynamic_billing_amount(usage, reference_date=ref_date)
                billing_details.append({
                    'period': detail['month'].strftime('%B %Y'),
                    'month': detail['month'].month,
                    'year': detail['month'].year,
                    'total_usage': round(usage, 2),
                    'total_unit': round(float(usage) / 1000, 2),
                    'amount': round(float(amount), 2),
                })

        monthly_usage_query = (
            Data_Transfer.objects
            .filter(connection__cmp_id=cmp_id)
            .annotate(
                month=TruncMonth('start_time'),
                total_records=Coalesce(F('new_rec'), 0) + Coalesce(F('mod_rec'), 0),
            )
            .values('month')
            .annotate(total_usage=Sum('total_records'))
            .order_by('-month')
        )

        for detail in monthly_usage_query:
            usage = detail['total_usage'] or 0
            month_date = as_date(detail['month'])
            amount = get_dynamic_billing_amount(usage, reference_date=month_date) if month_date else 0
            monthly_details.append({
                'month': month_date.month if month_date else None,
                'year': month_date.year if month_date else None,
                'period': month_date.strftime('%B %Y') if month_date else None,
                'total_usage': round(usage, 2),
                'total_unit': round(float(usage) / 1000, 2),
                'amount': round(float(amount), 2),
            })

        return Response({
            'cmp_id': cmp_id,
            'company_name': company.data,
            'current_month_labels': labels,
            'current_month_billing': current_month_billing,
            'billing_period': 'current-month',
            'invoices': AdminInvoiceSerializer(invoices, many=True).data,
            'billing_details': billing_details,
            'monthly_details': monthly_details,
            'connections': connection_usage,
            'connection_usage': connection_usage,
        }, status=status.HTTP_200_OK)
# ---------------------------------------------------------------------------
# Ticket Views
# ---------------------------------------------------------------------------

class AdminTicketListView(APIView):
    """
    GET /api/v1/support-admin/tickets/
    Query params: status, company_id, search
    """
    authentication_classes = [AdminJWTAuthentication]

    def get(self, request):
        qs = TicketSupport.objects.select_related(
            'cmp', 'category', 'issue_type', 'created_by'
        ).prefetch_related('replies').order_by('-created_at')

        ticket_status = request.query_params.get('status')
        company_id = request.query_params.get('company_id')
        search = request.query_params.get('search')

        if ticket_status:
            qs = qs.filter(status=ticket_status)
        if company_id:
            qs = qs.filter(cmp_id=company_id)
        if search:
            qs = qs.filter(subject__icontains=search)

        serializer = AdminTicketListSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminTicketDetailView(APIView):
    """
    GET   /api/v1/support-admin/tickets/<ticket_id>/  — view ticket detail
    PATCH /api/v1/support-admin/tickets/<ticket_id>/  — update ticket status
    """
    authentication_classes = [AdminJWTAuthentication]

    def _get_ticket(self, ticket_id):
        try:
            return TicketSupport.objects.select_related(
                'cmp', 'category', 'issue_type', 'created_by'
            ).get(ticket_id=ticket_id)
        except TicketSupport.DoesNotExist:
            return None

    def get(self, request, ticket_id):
        ticket = self._get_ticket(ticket_id)
        if not ticket:
            return Response({'message': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)
        data = AdminTicketDetailSerializer(ticket).data
        if ticket.has_user_reply:
            ticket.has_user_reply = False
            ticket.save(update_fields=['has_user_reply'])
        return Response(data, status=status.HTTP_200_OK)

    def patch(self, request, ticket_id):
        ticket = self._get_ticket(ticket_id)
        if not ticket:
            return Response({'message': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = AdminTicketStatusSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        status_value = serializer.validated_data['status']
        previous_status = ticket.status
        ticket.status = status_value
        update_fields = ['status', 'updated_at']

        if status_value == 'Closed' and previous_status != 'Closed':
            ticket.closed_at = timezone.now()
            update_fields.append('closed_at')

        ticket.save(update_fields=update_fields)
        return Response(AdminTicketDetailSerializer(ticket).data, status=status.HTTP_200_OK)


class AdminTicketRepliesView(APIView):
    """
    GET  /api/v1/support-admin/tickets/<ticket_id>/replies/  — list replies
    POST /api/v1/support-admin/tickets/<ticket_id>/replies/  — send support reply
    """
    authentication_classes = [AdminJWTAuthentication]

    def _get_ticket(self, ticket_id):
        try:
            return TicketSupport.objects.get(ticket_id=ticket_id)
        except TicketSupport.DoesNotExist:
            return None

    def get(self, request, ticket_id):
        ticket = self._get_ticket(ticket_id)
        if not ticket:
            return Response({'message': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)

        replies = TicketReply.objects.filter(ticket=ticket).select_related('created_by')
        return Response(AdminTicketReplySerializer(replies, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, ticket_id):
        ticket = self._get_ticket(ticket_id)
        if not ticket:
            return Response({'message': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)

        if ticket.status == 'Closed':
            return Response({'message': 'Cannot reply to a closed ticket.'}, status=status.HTTP_400_BAD_REQUEST)

        message = request.data.get('message', '').strip()
        if not message:
            return Response({'message': 'message is required.'}, status=status.HTTP_400_BAD_REQUEST)

        reply = TicketReply.objects.create(
            ticket=ticket,
            cmp=ticket.cmp,
            message=message,
            created_by=None,
            is_support_team=True,
        )

        # notify user that admin has replied
        ticket.has_new_response = True
        ticket.save(update_fields=['has_new_response', 'updated_at'])

        user_email = getattr(ticket.created_by, 'email', None)
        if user_email:
            try:
                send_admin_reply_email(
                    user_email=user_email,
                    subject=ticket.subject,
                    connection_name=ticket.connection_name,
                    reply_message=message,
                    ticket_id=ticket.ticket_id,
                    source_type=ticket.source_type,
                )
            except Exception as exc:
                logger.exception('Failed to send reply notification email: %s', exc)

        return Response(AdminTicketReplySerializer(reply).data, status=status.HTTP_201_CREATED)
