"""
Internal Django ingest for SAP proxy agent migration progress.

Public path: proxy-agent-gateway POST /v1/migration-progress
Cluster path: POST /api/v1/internal/proxy-agent/migration-progress/
"""

from __future__ import annotations

import json
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.elttool.services.proxy_agent_migration_progress import (
    ProgressAuthError,
    ProgressValidationError,
    ingest_proxy_agent_progress,
)
from apps.elttool.v1.permissions import IsInternalProxyAgentGateway

logger = logging.getLogger(__name__)

ALLOWED_EVENTS = frozenset(
    {
        "table_started",
        "table_progress",
        "table_completed",
        "table_failed",
        "activity_log",
        "batch_completed",
    }
)
_MAX_BODY_BYTES = 64 * 1024


class InternalProxyAgentMigrationProgressAPIView(APIView):
    """
    Cluster-only ingest. Gateway must send X-INTERNAL-KEY + X-Agent-UID
    (CN from mTLS) plus agent HMAC headers.
    """

    permission_classes = [IsInternalProxyAgentGateway]
    authentication_classes = []

    def post(self, request):
        raw_body = request.body or b""
        if len(raw_body) > _MAX_BODY_BYTES:
            return Response(
                {"accepted": False, "error": "Request body too large."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            body = json.loads(raw_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response(
                {"accepted": False, "error": "Invalid JSON body."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(body, dict):
            return Response(
                {"accepted": False, "error": "JSON object required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event = str(body.get("event") or "").strip()
        if event not in ALLOWED_EVENTS:
            return Response(
                {"accepted": False, "error": f"Unsupported event={event!r}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agent_uid = getattr(request, "proxy_agent_uid", None) or (
            request.headers.get("X-Agent-UID") or ""
        ).strip()
        idempotency_key = (
            request.headers.get("Idempotency-Key")
            or body.get("idempotency_key")
            or ""
        )

        try:
            result = ingest_proxy_agent_progress(
                agent_uid=agent_uid,
                body=body,
                raw_body=raw_body,
                timestamp_header=request.headers.get("X-Agent-Timestamp") or "",
                signature_header=request.headers.get("X-Agent-Signature") or "",
                idempotency_key=str(idempotency_key).strip() or None,
            )
        except ProgressAuthError as exc:
            logger.warning(
                "proxy_agent_progress auth failed agent_uid=%s event=%s",
                agent_uid,
                event,
            )
            return Response(
                {"accepted": False, "error": str(exc)},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except ProgressValidationError as exc:
            logger.warning(
                "proxy_agent_progress validation failed agent_uid=%s event=%s err=%s",
                agent_uid,
                event,
                exc,
            )
            return Response(
                {"accepted": False, "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception(
                "proxy_agent_progress unexpected error agent_uid=%s event=%s",
                agent_uid,
                event,
            )
            return Response(
                {"accepted": False, "error": "Internal error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "proxy_agent_progress accepted agent_uid=%s event=%s session=%s job=%s overall=%s",
            agent_uid,
            event,
            body.get("migration_session_id"),
            body.get("job_name"),
            result.get("overall_status"),
        )
        return Response(result, status=status.HTTP_200_OK)
