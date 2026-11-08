import logging
import os
from urllib.parse import urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)


def _django_internal_base_url():
    """
    Base URL for in-cluster HTTP calls to django-svc. Plain HTTP only; https to :8000
    fails with SSL record-layer errors against gunicorn/uvicorn.
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
    """See fast-api/websocket_utils._internal_push_headers."""
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
            timeout=60
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
    Triggers a WebSocket update for all table statuses of a connection.
    Calls the internal Django API.
    """
    if not connection_id:
        return

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/connection-table-status-push/{connection_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=60)
        if response.status_code == 200:
            logger.info(f"✅ Connection WebSocket update triggered | connection_id={connection_id}")
        else:
            logger.warning(
                f"⚠️ Connection WebSocket update failed | connection_id={connection_id} | "
                f"status={response.status_code} | body={response.text}"
            )
    except Exception as e:
        logger.error(f"❌ Error triggering connection WebSocket update | connection_id={connection_id} | error={e}")

def trigger_update_schema_ws_status_update(connection_id):
    """
    Triggers a WebSocket update for schema refresh operations.
    Calls the internal Django API for update-schema status channel.
    """
    if not connection_id:
        return

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/update-schema-status-push/{connection_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=60)
        if response.status_code == 200:
            logger.info(f"✅ Update Schema WebSocket update triggered | connection_id={connection_id}")
        else:
            logger.warning(
                f"⚠️ Update Schema WebSocket update failed | connection_id={connection_id} | "
                f"status={response.status_code} | body={response.text}"
            )
    except Exception as e:
        logger.error(f"❌ Error triggering Update Schema WebSocket update | connection_id={connection_id} | error={e}")


def trigger_connection_activity_log_update(connection_id):
    """
    Triggers a WebSocket update for connection activity logs.
    Calls the internal Django API.
    """
    if not connection_id:
        return

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/connection-activity-log-push/{connection_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=60)
        if response.status_code == 200:
            logger.info(f"✅ Connection activity log WebSocket update triggered | connection_id={connection_id}")
        else:
            logger.warning(
                f"⚠️ Connection activity log WebSocket update failed | connection_id={connection_id} | "
                f"status={response.status_code} | body={response.text}"
            )
    except Exception as e:
        logger.error(f"❌ Error triggering connection activity log WebSocket update | connection_id={connection_id} | error={e}")


def trigger_company_connections_status_update(cmp_id):
    """
    Triggers the company-level WebSocket channel so the home page
    refreshes migration_status for all connections in this company.
    Calls the internal Django API from the migration-function process.
    """
    if not cmp_id:
        return

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/company-connections-status-push/{cmp_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=5)
        if response.status_code == 200:
            logger.info(f"✅ Company connections WS update triggered | cmp_id={cmp_id}")
        else:
            logger.warning(
                f"⚠️ Company connections WS update failed | cmp_id={cmp_id} | "
                f"status={response.status_code}"
            )
    except Exception as e:
        logger.error(f"❌ Error triggering company connections WS update | cmp_id={cmp_id} | error={e}")
