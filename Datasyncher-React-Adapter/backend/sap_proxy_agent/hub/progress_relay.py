"""
Relay SAP proxy agent progress to Django internal ingest (same contract as HTTPS gateway).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)


def _django_base_url() -> str:
    default = "http://django-svc:8000"
    raw = (os.getenv("DJANGO_INTERNAL_BASE_URL") or default).strip()
    if not raw:
        return default.rstrip("/")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and (
        host == "django-svc" or host.startswith("django-svc.")
    ):
        parsed = parsed._replace(scheme="http")
    return urlunparse(parsed).rstrip("/")


def _internal_key() -> str:
    return (os.getenv("INTERNAL_API_KEY") or "").strip()


def relay_progress_to_django(
    *,
    agent_uid: str,
    body: dict[str, Any],
    raw_body: bytes,
    timestamp_header: str = "",
    signature_header: str = "",
    idempotency_key: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """
    POST progress JSON to Django. Returns (http_status, response_json).
    HMAC headers are forwarded when present (HTTPS fallback parity).
    """
    key = _internal_key()
    if not key:
        logger.error("INTERNAL_API_KEY is not configured on hub")
        return 503, {"accepted": False, "error": "Hub misconfigured."}

    agent_uid = (agent_uid or "").strip()
    if not agent_uid:
        return 401, {"accepted": False, "error": "Agent identity required."}

    forward_headers = {
        "Content-Type": "application/json",
        "X-INTERNAL-KEY": key,
        "X-Agent-UID": agent_uid,
        "X-Forwarded-Proto": "https",
    }
    if timestamp_header:
        forward_headers["X-Agent-Timestamp"] = timestamp_header
    if signature_header:
        forward_headers["X-Agent-Signature"] = signature_header
    if idempotency_key:
        forward_headers["Idempotency-Key"] = idempotency_key

    url = f"{_django_base_url()}/api/v1/internal/proxy-agent/migration-progress/"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, content=raw_body, headers=forward_headers)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            data = {"accepted": False, "error": resp.text[:500]}
        return resp.status_code, data
    except httpx.RequestError as exc:
        logger.error("Django progress relay failed agent_uid=%s error=%s", agent_uid, exc)
        return 502, {"accepted": False, "error": "Upstream unavailable."}
