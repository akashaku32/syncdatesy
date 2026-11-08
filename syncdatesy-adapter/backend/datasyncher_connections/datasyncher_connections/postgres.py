"""PostgreSQL connection helper (platform metadata DB)."""

from __future__ import annotations

import os
import time
from typing import Any

import psycopg2
from psycopg2 import OperationalError


def create_postgres_connection(
    *,
    retries: int = 3,
    retry_delay: int = 2,
    validate: bool = True,
    env_overrides: dict[str, str] | None = None,
) -> psycopg2.extensions.connection:
    """
    Create a PostgreSQL connection with retry logic.

    Behavioral baseline: ``migration-function/utils/database_utils.postgres_connection``.
    """
    env = {**os.environ, **(env_overrides or {})}
    db_host = env.get("DB_HOST")
    db_user = env.get("DB_USER")
    db_password = env.get("DB_PASSWORD")
    db_name = env.get("DB_NAME")

    if not all([db_host, db_user, db_password, db_name]):
        missing_vars = [
            name
            for name, val in [
                ("DB_HOST", db_host),
                ("DB_USER", db_user),
                ("DB_PASSWORD", db_password),
                ("DB_NAME", db_name),
            ]
            if not val
        ]
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            connection = psycopg2.connect(
                host=db_host,
                user=db_user,
                password=db_password,
                dbname=db_name,
                connect_timeout=30,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
            if validate:
                cursor = connection.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
            return connection
        except (OperationalError, psycopg2.Error, Exception) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(retry_delay)
            else:
                raise last_error from exc

    raise RuntimeError("Unreachable: postgres connection retry loop exhausted")
