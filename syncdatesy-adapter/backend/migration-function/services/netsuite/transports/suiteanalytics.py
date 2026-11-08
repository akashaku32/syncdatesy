"""SuiteAnalytics Connect via ODBC (NetSuite driver required in runtime image)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.netsuite.config import (
    AUTH_CONNECT_EMAIL,
    AUTH_CONNECT_OAUTH2,
    AUTH_CONNECT_TBA,
    connect_account_id,
    suiteanalytics_host,
)
from services.netsuite.oauth_m2m import DEFAULT_CONNECT_SCOPE, request_m2m_access_token
from services.netsuite.tba_token_password import build_connect_tba_token_password

logger = logging.getLogger(__name__)

CONNECT_PORT = 1708
DATASOURCE = "NetSuite2.com"
FETCH_SIZE = 1000
TBA_LOGON_ID = "TBA"


class SuiteAnalyticsTransport:
    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._conn = None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                logger.warning("Failed to close NetSuite ODBC connection", exc_info=True)
            self._conn = None

    def _resolve_logon_password(self) -> tuple[str, str, Optional[str]]:
        """Return (UID, PWD, optional OAuth2Token for CustomProperties)."""
        auth_type = str(self._config.get("connect_auth_type") or AUTH_CONNECT_TBA).strip().lower()
        if auth_type == AUTH_CONNECT_EMAIL:
            email = str(self._config.get("logon_email") or "").strip()
            password = str(self._config.get("connect_password") or "").strip()
            if not email or not password:
                raise HTTPException(
                    status_code=422,
                    detail="logon_email and connect_password are required for SuiteAnalytics email login.",
                )
            return email, password, None
        if auth_type == AUTH_CONNECT_OAUTH2:
            oauth_config = {
                **self._config,
                "client_id": self._config.get("connect_client_id"),
                "certificate_id": self._config.get("connect_certificate_id"),
                "private_key": self._config.get("connect_private_key"),
            }
            token = request_m2m_access_token(oauth_config, scope=DEFAULT_CONNECT_SCOPE)
            return "login", "password", token
        consumer_key = str(self._config.get("consumer_key") or "").strip()
        consumer_secret = str(self._config.get("consumer_secret") or "").strip()
        token_id = str(self._config.get("token_id") or "").strip()
        token_secret = str(self._config.get("token_secret") or "").strip()
        if not all([consumer_key, consumer_secret, token_id, token_secret]):
            raise HTTPException(
                status_code=422,
                detail="consumer_key, consumer_secret, token_id, and token_secret are required for Connect TBA.",
            )
        token_password = build_connect_tba_token_password(
            self._config["account_id"],
            consumer_key,
            token_id,
            consumer_secret,
            token_secret,
        )
        return TBA_LOGON_ID, token_password, None

    def _connect(self):
        if self._conn is not None:
            return self._conn
        try:
            import pyodbc
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="pyodbc is not available; SuiteAnalytics Connect cannot run in this environment.",
            ) from exc

        account = connect_account_id(self._config["account_id"])
        role_id = str(self._config["role_id"]).strip()
        host = suiteanalytics_host(self._config["account_id"])
        logon_id, password, oauth2_token = self._resolve_logon_password()

        driver = os.environ.get("NETSUITE_ODBC_DRIVER", "{NetSuite ODBC Drivers}")
        truststore = os.environ.get("NETSUITE_ODBC_TRUSTSTORE", "system")

        custom_props = f"AccountID={account};RoleID={role_id};Uppercase=1;StaticSchema=1"
        if oauth2_token:
            custom_props = f"{custom_props};OAuth2Token={oauth2_token}"

        conn_str = (
            f"DRIVER={driver};"
            f"Host={host};"
            f"Port={CONNECT_PORT};"
            f"Encrypted=1;"
            f"AllowSinglePacketLogout=1;"
            f"Truststore={truststore};"
            f"ServerDataSource={DATASOURCE};"
            f"UID={logon_id};"
            f"PWD={password};"
            f"CustomProperties={custom_props};"
        )
        try:
            self._conn = pyodbc.connect(conn_str, autocommit=True, timeout=60)
        except pyodbc.Error as exc:
            raise HTTPException(
                status_code=401,
                detail=(
                    "SuiteAnalytics Connect failed. Ensure the NetSuite ODBC driver is installed "
                    f"and credentials match the selected Connect auth type. ({exc})"
                ),
            ) from exc
        return self._conn

    def run_query(
        self,
        sql: str,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query = sql.strip().rstrip(";")
        if limit is not None and " fetch " not in query.lower():
            query = f"{query} FETCH NEXT {int(limit)} ROWS ONLY"
        if offset is not None and int(offset) > 0:
            raise HTTPException(
                status_code=422,
                detail="SuiteAnalytics transport uses keyset paging; offset is not supported.",
            )
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(query)
            columns = [col[0] for col in cursor.description or []]
            rows: List[Dict[str, Any]] = []
            while True:
                batch = cursor.fetchmany(FETCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    rows.append({columns[i]: row[i] for i in range(len(columns))})
                if limit is not None and len(rows) >= limit:
                    return rows[: int(limit)]
            return rows
        finally:
            cursor.close()
