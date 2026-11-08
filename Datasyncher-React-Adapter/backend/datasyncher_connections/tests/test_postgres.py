from unittest.mock import MagicMock, patch

import pytest

from datasyncher_connections.postgres import create_postgres_connection


def test_missing_env_raises():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="Missing required environment variables"):
            create_postgres_connection()


@patch("datasyncher_connections.postgres.psycopg2.connect")
def test_connect_success(mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    env = {
        "DB_HOST": "localhost",
        "DB_USER": "user",
        "DB_PASSWORD": "pass",
        "DB_NAME": "db",
    }
    with patch.dict("os.environ", env, clear=True):
        conn = create_postgres_connection(retries=1)

    assert conn is mock_conn
    mock_cursor.execute.assert_called_once_with("SELECT 1")
