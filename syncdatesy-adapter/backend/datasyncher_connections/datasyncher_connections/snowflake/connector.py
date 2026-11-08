"""snowflake.connector session builders."""

from __future__ import annotations

import os
import socket
from typing import Any

import snowflake.connector

from datasyncher_connections.snowflake.pem import load_private_key_object, normalize_pem_key


def _derive_host_from_account(account: str | None) -> str:
    if not account:
        return ""
    account_id = str(account).split(".")[0]
    return f"{account_id}.snowflakecomputing.com"


def _preflight_network_test(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    if not host:
        return False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def create_snowflake_connector(
    config: dict,
    *,
    use_database: bool = True,
    preflight: bool = True,
    client_session_keep_alive: bool | None = None,
) -> snowflake.connector.SnowflakeConnection:
    """
    Create a ``snowflake.connector`` connection from a config dict.

  Behavioral baseline: ``amazon_s3_service.sf_connect`` / ``google_reviews_service._sf_connect``
  / ``snowflake_to_snowflake_service._build_conn_params``.
    """
    account = config.get("account")
    user = config.get("user") or config.get("username")
    warehouse = config.get("warehouse")
    database = config.get("database") if use_database else None
    schema = config.get("schema")
    role = config.get("role") or os.getenv("SNOWFLAKE_DEFAULT_ROLE", "ACCOUNTADMIN")
    authentication_type = str(config.get("authentication_type", "password")).lower()
    password = config.get("password")
    private_key = config.get("private_key")
    passphrase = config.get("passphrase")

    if preflight:
        host = _derive_host_from_account(account)
        if host and not _preflight_network_test(host):
            pass  # non-blocking; legacy services log and continue

    ocsp_fail_open_env = str(os.getenv("SNOWFLAKE_OCSP_FAIL_OPEN", "true")).strip().lower()
    ocsp_fail_open = ocsp_fail_open_env in {"1", "true", "yes", "on"}

    connect_params: dict[str, Any] = {
        "account": account,
        "user": user,
        "warehouse": warehouse,
        "role": role,
        "ocsp_fail_open": ocsp_fail_open,
    }
    if database:
        connect_params["database"] = database
    if schema:
        connect_params["schema"] = schema
    if client_session_keep_alive is not None:
        connect_params["client_session_keep_alive"] = client_session_keep_alive
    elif config.get("client_session_keep_alive") is not None:
        connect_params["client_session_keep_alive"] = config["client_session_keep_alive"]

    if authentication_type == "key_pair" and private_key:
        normalized = normalize_pem_key(str(private_key))
        connect_params["private_key"] = load_private_key_object(
            normalized,
            str(passphrase) if passphrase is not None else None,
        )
    elif authentication_type == "password" and password:
        connect_params["password"] = password
    elif (password or "").strip():
        connect_params["password"] = password
    elif (private_key or "").strip():
        normalized = normalize_pem_key(str(private_key))
        connect_params["private_key"] = load_private_key_object(
            normalized,
            str(passphrase) if passphrase is not None else None,
        )
    else:
        raise ValueError(
            f"Authentication failed: authentication_type='{authentication_type}', "
            "but required credentials were not provided."
        )

    return snowflake.connector.connect(**connect_params)
