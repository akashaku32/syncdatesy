from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# def push_migration_status_update(migration_session_id, payload):
#     """
#     Push migration status update to WebSocket listeners.

#     payload MUST match MigrationStatusAPIView response structure.
#     """
#     channel_layer = get_channel_layer()

#     async_to_sync(channel_layer.group_send)(
#         f"migration_status_{migration_session_id}",
#         {
#             "type": "migration_status_update",
#             "payload": payload,
#         }
#     )

import hashlib
import json
import os
from urllib.parse import urlparse, urlunparse

from django.core.cache import cache


def _django_internal_base_url():
    """
    Base URL for in-cluster HTTP calls to django-svc. The Service speaks plain HTTP;
    mis-set env (https://django-svc… or K8s FQDN with https) causes SSL record-layer errors.
    """
    import os

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
    headers = {"X-Forwarded-Proto": "https"}
    key = os.getenv("INTERNAL_API_KEY")
    if key:
        headers["X-INTERNAL-KEY"] = key
    return headers

def push_migration_status_update(migration_session_id, payload):
    # Send the update immediately without aggressive deduplication
    # since migration status updates are critical and time-sensitive
    
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"migration_status_{migration_session_id}",
        {
            "type": "migration_status_update",
            "payload": payload,
        }
    )

def push_connection_table_status_update(connection_id, payload):
    # Send connection table status updates immediately
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"connection_table_status_{connection_id}",
        {
            "type": "connection_table_status_update",
            "payload": payload,
        }
    )


def push_update_schema_status(connection_id, payload):
    # Send update schema status updates immediately
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"update_schema_status_{connection_id}",
        {
            "type": "update_schema_status_update",
            "payload": payload,
        }
    )

def trigger_connection_ws_status_update(connection_id):
    """
    Triggers a WebSocket update for all table statuses of a connection.
    Calls the internal Django API.
    """
    if not connection_id:
        return

    import requests
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    
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
    Triggers a WebSocket update for connection activity logs.
    Calls the internal Django API.
    """
    if not connection_id:
        return

    import requests
    import logging
    
    logger = logging.getLogger(__name__)
    
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
        logger.error(f"❌ Error triggering connection activity log WebSocket update | connection_id={connection_id} | error={e}")

def push_connection_activity_log_update(connection_id, payload):
    """
    Push connection activity log update to WebSocket listeners.
    """
    # Send activity log updates immediately
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"connection_activity_log_{connection_id}",
        {
            "type": "connection_activity_log_update",
            "payload": payload,
        }
    )


def push_admin_ops_snapshot(payload):
    """Broadcast full ops snapshot to admin_ops_platform WebSocket group."""
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "admin_ops_platform",
        {
            "type": "admin_ops_update",
            "payload": payload,
        },
    )


def push_company_connections_status_update(cmp_id, payload):
    """
    Push a company-level ping to all home-page listeners for this company.
    Frontend receives this and re-fetches /api/v1/home/ to refresh migration_status.
    """
    # Normalize payload contract so all emitters trigger the same frontend path.
    # Older callers sometimes send only {"connection_id": ..., "error_message": ...}.
    normalized_payload = dict(payload or {})
    normalized_payload.setdefault("event", "migration_status_changed")
    normalized_payload.setdefault("cmp_id", cmp_id)

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"company_connections_status_{cmp_id}",
        {
            "type": "company_connections_status_update",
            "payload": normalized_payload,
        }
    )


def trigger_company_connections_status_update(cmp_id):
    """
    Relay: called by migration-function (separate process) to trigger the
    company_connections_status WS channel via the internal Django HTTP API.
    """
    if not cmp_id:
        return

    import requests
    import os
    import logging

    _logger = logging.getLogger(__name__)

    try:
        url = f"{_django_internal_base_url()}/api/v1/internal/company-connections-status-push/{cmp_id}/"
        response = requests.post(url, headers=_internal_push_headers(), timeout=5)
        if response.status_code == 200:
            _logger.info(f"✅ Company connections WS update triggered | cmp_id={cmp_id}")
        else:
            _logger.warning(
                f"⚠️ Company connections WS update failed | cmp_id={cmp_id} | "
                f"status={response.status_code}"
            )
    except Exception as e:
        _logger.error(f"❌ Error triggering company connections WS update | cmp_id={cmp_id} | error={e}")