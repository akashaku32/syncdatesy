"""SQLAlchemy Snowflake engine builders with per-service flavor preservation."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from datasyncher_connections.snowflake.pem import load_private_key_der, load_private_key_object, normalize_pem_key

Flavors = ("dynamics", "salesforce", "google_reviews")


def _config_value(config: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = config.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return default


def _build_password_url(
    config: dict,
    *,
    flavor: str,
    username: str,
    account: str,
    database: str,
    warehouse: str,
    schema: str,
    role: str,
) -> str:
    password = quote(config.get("password", "") or "")
    user = quote(username)

    if flavor == "dynamics":
        return (
            f"snowflake://{user}:{password}@{account}/"
            f"{database}?warehouse={warehouse}"
        )

    schema_part = f"/{schema}" if schema else ""
    role_part = f"&role={role}" if role else ""
    return (
        f"snowflake://{user}:{password}@{account}/{database}{schema_part}?"
        f"warehouse={warehouse}{role_part}"
    )


def _build_keypair_engine(
    config: dict,
    *,
    flavor: str,
    username: str,
    account: str,
    database: str,
    warehouse: str,
    schema: str,
    role: str,
) -> Engine:
    private_key_str = config.get("private_key")
    passphrase = config.get("passphrase")
    if not private_key_str:
        raise ValueError("Private key string is required for key-pair authentication.")

    normalized = normalize_pem_key(str(private_key_str))

    if flavor == "dynamics":
        private_key_material = load_private_key_der(normalized, str(passphrase) if passphrase else None)
        connection_string = (
            f"snowflake://{quote(username)}@{account}/"
            f"{database}?warehouse={warehouse}"
        )
        return create_engine(
            connection_string,
            connect_args={
                "private_key": private_key_material,
                "role": role,
                "schema": schema,
            },
        )

    key_object = load_private_key_object(normalized, str(passphrase) if passphrase else None)
    schema_upper = schema.upper() if schema else ""
    connection_string = (
        f"snowflake://{username}@{account}/{database}/{schema_upper}?"
        f"warehouse={warehouse}&role={role}"
    )
    return create_engine(connection_string, connect_args={"private_key": key_object})


def create_snowflake_sqlalchemy_engine(
    config: dict,
    *,
    flavor: str = "salesforce",
    ensure_schema: bool = True,
    validate_version: bool = False,
    on_error: Callable[..., Any] | None = None,
) -> tuple[Engine, str]:
    """
    Create a SQLAlchemy engine for Snowflake destination connections.

    Args:
        config: Snowflake connection dict (user/username, account, database, etc.)
        flavor: ``dynamics`` (DER bytes + separate schema setup) or
            ``salesforce`` / ``google_reviews`` (cryptography key object in URL path)
        ensure_schema: Run CREATE SCHEMA IF NOT EXISTS + USE SCHEMA
        validate_version: Run SELECT CURRENT_VERSION() (Dynamics behavior)
        on_error: Optional callback invoked as ``on_error(exc)`` before re-raise

    Returns:
        ``(engine, schema_upper)``
    """
    if flavor not in Flavors:
        raise ValueError(f"Unsupported flavor: {flavor}. Expected one of {Flavors}")

    username = _config_value(config, "user", "username")
    account = _config_value(config, "account")
    database = _config_value(config, "database")
    warehouse = _config_value(config, "warehouse")
    schema = _config_value(config, "schema").upper()
    role = _config_value(config, "role")
    authentication_type = _config_value(config, "authentication_type", default="password").lower()

    try:
        if authentication_type == "password":
            connection_string = _build_password_url(
                config,
                flavor=flavor,
                username=username,
                account=account,
                database=database,
                warehouse=warehouse,
                schema=schema,
                role=role,
            )
            engine = create_engine(connection_string)
        elif authentication_type == "key_pair":
            engine = _build_keypair_engine(
                config,
                flavor=flavor,
                username=username,
                account=account,
                database=database,
                warehouse=warehouse,
                schema=schema,
                role=role,
            )
        else:
            raise ValueError(f"Unsupported authentication type: {authentication_type}")

        if ensure_schema and schema:
            with engine.connect() as conn:
                if role and authentication_type != "key_pair" and flavor == "dynamics":
                    conn.execute(text(f"USE ROLE {role}"))
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
                conn.execute(text(f"USE SCHEMA {schema}"))
                if validate_version:
                    result = conn.execute(text("SELECT CURRENT_VERSION()"))
                    row = result.fetchone()
                    if row:
                        print(f"Snowflake version: {row[0]}")

        return engine, schema
    except Exception as exc:
        if on_error:
            on_error(exc)
        raise
