import logging
import os
from urllib.parse import urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)


def _django_internal_base_url():
    """
    Base URL for in-cluster HTTP calls to django-svc. Plain HTTP only on port 8000.
    """
    default = "http://django-svc:8000"
    raw = (os.getenv("DJANGO_INTERNAL_BASE_URL") or default).strip()
    if not raw:
        return default.rstrip("/")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and (host == "django-svc" or host.startswith("django-svc.")):
        parsed = parsed._replace(scheme="http")
    return urlunparse(parsed).rstrip("/")


def _internal_push_headers():
    """
    Headers for in-cluster HTTP calls to django-svc.

    SECURE_SSL_REDIRECT would 301 http→https on :8000; requests then follow
    with TLS against plain HTTP and fail (SSL WRONG_VERSION_NUMBER). Sending
    X-Forwarded-Proto=https matches SECURE_PROXY_SSL_HEADER so Django treats
    the request as already secure and serves it over HTTP.
    """
    headers = {"X-Forwarded-Proto": "https"}
    key = os.getenv("INTERNAL_API_KEY")
    if key:
        headers["X-INTERNAL-KEY"] = key
    return headers


def trigger_ws_status_update(migration_session_id):
    """
    Trigger a WebSocket migration status update by calling the internal Django API.
    """
    if not migration_session_id:
        return

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/migration-status-push/{migration_session_id}/"

        response = requests.post(
            url,
            headers=_internal_push_headers(),
            timeout=5
        )

        if response.status_code == 200:
            logger.info(
                f"✅ WebSocket status update triggered | session={migration_session_id}"
            )
        elif response.status_code == 404:
            # Session might already be cleaned up – not fatal
            logger.warning(
                f"⚠️ WS push skipped (not found) | session={migration_session_id}"
            )
        else:
            logger.warning(
                f"⚠️ WS push failed | session={migration_session_id} | "
                f"status={response.status_code} | body={response.text}"
            )

    except Exception as e:
        logger.error(
            f"❌ Failed to trigger WS update | session={migration_session_id} | error={e}"
        )

def trigger_connection_ws_status_update(connection_id):
    """
    Triggers a WebSocket update for all table statuses of a connection by calling the internal Django API.
    """
    if not connection_id:
        return

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/connection-table-status-push/{connection_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=5)
        if response.status_code == 200:
            logger.info(f"✅ Connection WebSocket update triggered | connection_id={connection_id}")
        else:
            logger.warning(
                f"⚠️ Connection WebSocket update failed | connection_id={connection_id} | "
                f"status={response.status_code} | body={response.text}"
            )
    except Exception as e: 
        logger.error(f"❌ Error triggering connection WebSocket update | connection_id={connection_id} | error={e}")


def trigger_connection_activity_log_update(connection_id):
    """
    Trigger a WebSocket update for the connection activity log panel.
    """
    if not connection_id:
        return

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/connection-activity-log-push/{connection_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=5)
        if response.status_code == 200:
            logger.info(f"✅ Connection activity log WebSocket update triggered | connection_id={connection_id}")
        else:
            logger.warning(
                f"⚠️ Connection activity log WebSocket update failed | connection_id={connection_id} | "
                f"status={response.status_code} | body={response.text}"
            )
    except Exception as e:
        logger.error(
            f"❌ Error triggering connection activity log WebSocket update | connection_id={connection_id} | error={e}"
        )


def trigger_company_connections_status_update(cmp_id):
    """Ping company-level WS so home / connection lists refetch migration_status."""
    if not cmp_id:
        return
    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/company-connections-status-push/{cmp_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=5)
        if response.status_code != 200:
            logger.warning(
                "⚠️ Company connections WS update failed | cmp_id=%s | status=%s",
                cmp_id,
                response.status_code,
            )
    except Exception as e:
        logger.error(
            "❌ Error triggering company connections WS update | cmp_id=%s | error=%s",
            cmp_id,
            e,
        )
