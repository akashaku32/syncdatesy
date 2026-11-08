"""Shared Snowflake and Postgres connection helpers."""

from datasyncher_connections.postgres import create_postgres_connection
from datasyncher_connections.snowflake.connector import create_snowflake_connector
from datasyncher_connections.snowflake.pem import (
    load_private_key_der,
    load_private_key_object,
    normalize_pem_key,
)
from datasyncher_connections.snowflake.sqlalchemy_engine import create_snowflake_sqlalchemy_engine

__all__ = [
    "create_postgres_connection",
    "create_snowflake_connector",
    "create_snowflake_sqlalchemy_engine",
    "load_private_key_der",
    "load_private_key_object",
    "normalize_pem_key",
]
