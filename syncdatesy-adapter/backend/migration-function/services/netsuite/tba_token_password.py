"""Build SuiteAnalytics Connect TBA token password (Oracle NetSuite spec)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time


def build_connect_tba_token_password(
    account_id: str,
    consumer_key: str,
    token_id: str,
    consumer_secret: str,
    token_secret: str,
) -> str:
    """
    One-time Connect login password from integration TBA credentials.
    Username for ODBC must be ``TBA``.
    """
    nonce = secrets.token_urlsafe(24)[:32]
    timestamp = str(int(time.time()))
    base = "&".join(
        [
            account_id.strip(),
            consumer_key.strip(),
            token_id.strip(),
            nonce,
            timestamp,
        ]
    )
    key = f"{consumer_secret.strip()}&{token_secret.strip()}"
    signature = base64.b64encode(
        hmac.new(key.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return f"{base}&{signature}&HMAC-SHA256"
