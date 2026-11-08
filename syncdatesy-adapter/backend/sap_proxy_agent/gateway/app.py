"""
proxy-agent-gateway — thin HTTPS edge for SAP proxy agent progress callbacks.

Public: POST /v1/migration-progress (mTLS at ingress)
Forwards to Django internal ingest with X-INTERNAL-KEY + X-Agent-UID from cert CN.
No Postgres access.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("proxy_agent_gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="Datasyncher Proxy Agent Gateway", version="1.0.0")

_MAX_BODY = 64 * 1024
_RATE_LIMIT = int(os.getenv("PROXY_AGENT_GATEWAY_RATE_LIMIT", "300"))
_RATE_WINDOW = 60.0
_rate_buckets: Dict[str, Deque[float]] = defaultdict(deque)

_CERT_CN_HEADERS = (
    "x-client-cert-cn",
    "x-ssl-client-s-dn",
    "ssl-client-subject-dn",
    "x-forwarded-client-cert-cn",
)


def _agent_uid_from_client_cert_pem(header_value: str) -> Optional[str]:
    """Parse CN from nginx ssl-client-cert (URL-encoded PEM) when snippets are disabled."""
    raw = (header_value or "").strip()
    if not raw:
        return None
    try:
        from urllib.parse import unquote

        from cryptography import x509
        from cryptography.x509.oid import NameOID

        pem = unquote(raw).replace(" ", "\n").replace("\t", "\n")
        if "-----BEGIN" not in pem:
            return None
        # Normalize accidental newlines inside BEGIN/END lines
        pem = pem.replace(
            "-----BEGIN\nCERTIFICATE-----", "-----BEGIN CERTIFICATE-----"
        ).replace("-----END\nCERTIFICATE-----", "-----END CERTIFICATE-----")
        cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
        attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if attrs:
            return str(attrs[0].value).strip() or None
    except Exception:
        logger.exception("failed to parse ssl-client-cert for agent uid")
    return None


def _django_base_url() -> str:
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


def _internal_key() -> str:
    return (os.getenv("INTERNAL_API_KEY") or "").strip()


def _allow_hmac_dev() -> bool:
    return (os.getenv("PROXY_AGENT_ALLOW_HMAC_DEV") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _parse_agent_uid_from_dn(value: str) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    # Common forms: "CN=agent-1-abc", "CN=agent-1-abc,O=..."
    parts = [p.strip() for p in text.replace("/", ",").split(",") if p.strip()]
    for part in parts:
        if part.upper().startswith("CN="):
            return part.split("=", 1)[1].strip()
    if text.startswith("agent-"):
        return text
    return None


def _agent_uid_from_request(request: Request) -> Optional[str]:
    for header in _CERT_CN_HEADERS:
        raw = request.headers.get(header)
        uid = _parse_agent_uid_from_dn(raw or "")
        if uid:
            return uid
    # Prefer PEM from auth-tls-pass-certificate-to-upstream (no snippet required).
    uid = _agent_uid_from_client_cert_pem(request.headers.get("ssl-client-cert") or "")
    if uid:
        return uid
    verify = (request.headers.get("x-ssl-client-verify") or "").strip().upper()
    if verify and verify not in ("SUCCESS", "SUCCESSFUL", "OK", "1"):
        return None
    if _allow_hmac_dev():
        # Dev-only: allow explicit header when mTLS is not terminated locally.
        return (request.headers.get("x-agent-uid-dev") or "").strip() or None
    return None


def _rate_limit(agent_uid: str) -> None:
    now = time.time()
    bucket = _rate_buckets[agent_uid]
    while bucket and now - bucket[0] > _RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    bucket.append(now)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/migration-progress")
async def migration_progress(request: Request):
    key = _internal_key()
    if not key:
        logger.error("INTERNAL_API_KEY is not configured")
        raise HTTPException(status_code=503, detail="Gateway misconfigured.")

    agent_uid = _agent_uid_from_request(request)
    if not agent_uid:
        raise HTTPException(
            status_code=401,
            detail="Client certificate identity required.",
        )

    body = await request.body()
    if len(body) > _MAX_BODY:
        raise HTTPException(status_code=413, detail="Request body too large.")

    _rate_limit(agent_uid)

    timestamp = request.headers.get("x-agent-timestamp") or ""
    signature = request.headers.get("x-agent-signature") or ""
    idem = request.headers.get("idempotency-key") or ""

    forward_headers = {
        "Content-Type": "application/json",
        "X-INTERNAL-KEY": key,
        "X-Agent-UID": agent_uid,
        "X-Forwarded-Proto": "https",
    }
    if timestamp:
        forward_headers["X-Agent-Timestamp"] = timestamp
    if signature:
        forward_headers["X-Agent-Signature"] = signature
    if idem:
        forward_headers["Idempotency-Key"] = idem

    url = f"{_django_base_url()}/api/v1/internal/proxy-agent/migration-progress/"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, content=body, headers=forward_headers)
    except httpx.RequestError as exc:
        logger.error("django forward failed agent_uid=%s error=%s", agent_uid, exc)
        raise HTTPException(status_code=502, detail="Upstream unavailable.") from exc

    content_type = resp.headers.get("content-type", "application/json")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type,
    )


@app.exception_handler(HTTPException)
async def http_exc_handler(_request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"accepted": False, "error": exc.detail},
    )
