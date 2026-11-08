import io
import json
from unittest.mock import MagicMock, patch

import pytest

from datasyncher_connections.databricks.connector import (
    DatabricksWarehouseUnavailable,
    _DatabricksResult,
    assert_databricks_sql_endpoint_available,
    create_databricks_sqlalchemy_like_engine,
    describe_databricks_http_path_issue,
    ensure_cluster_compute_running,
    execute_sql_statement_bounded,
    is_databricks_cluster_compute_http_path,
    is_databricks_sql_warehouse_http_path,
    normalize_databricks_config,
    validate_databricks_api_connection,
    validate_databricks_sql_warehouse,
    warehouse_cold_start_seconds,
)


def _mock_urlopen(responses):
    def _open(req, timeout=20):
        url = req.full_url
        status, payload = responses[url]
        if status >= 400:
            import urllib.error

            body = json.dumps(payload).encode() if payload is not None else b""
            raise urllib.error.HTTPError(
                url, status, "error", hdrs=None, fp=io.BytesIO(body)
            )
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    return _open


@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_validate_databricks_api_connection_success(mock_urlopen):
    host = "adb-test.azuredatabricks.net"
    mock_urlopen.side_effect = _mock_urlopen(
        {
            f"https://{host}/api/2.0/clusters/list": (200, {"clusters": [{"cluster_id": "x"}]}),
            f"https://{host}/api/2.1/unity-catalog/catalogs/prod": (200, {"name": "prod"}),
        }
    )
    result = validate_databricks_api_connection(
        {
            "server_hostname": host,
            "access_token": "dapi-test",
            "catalog": "prod",
        }
    )
    assert result["status"] == "success"
    assert result["mode"] == "api_only"
    assert result["cluster_count"] == 1
    assert result["catalog_ok"] is True


@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_validate_databricks_api_connection_missing_catalog(mock_urlopen):
    host = "adb-test.azuredatabricks.net"
    mock_urlopen.side_effect = _mock_urlopen(
        {
            f"https://{host}/api/2.0/clusters/list": (200, {"clusters": []}),
            f"https://{host}/api/2.1/unity-catalog/catalogs/prod": (404, {}),
        }
    )
    with pytest.raises(ValueError, match="catalog 'prod' was not found"):
        validate_databricks_api_connection(
            {
                "server_hostname": host,
                "access_token": "dapi-test",
                "catalog": "prod",
            }
        )


def test_normalize_databricks_config_adds_leading_slash_to_http_path():
    cfg = normalize_databricks_config({"http_path": "sql/1.0/warehouses/abc"})
    assert cfg["http_path"] == "/sql/1.0/warehouses/abc"


def test_describe_databricks_http_path_issue_for_cluster_path():
    path = "/sql/protocolv1/o/7405607423243625/1014-055536-2my5dndu"
    assert describe_databricks_http_path_issue(path) is None
    assert is_databricks_cluster_compute_http_path(path)
    assert not is_databricks_sql_warehouse_http_path(path)


def test_warehouse_cold_start_seconds_defaults_to_fifteen_minutes(monkeypatch):
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_COLD_START_SECONDS", raising=False)
    assert warehouse_cold_start_seconds() == 900


@patch("datasyncher_connections.databricks.connector.create_databricks_connection")
def test_validate_databricks_sql_warehouse_success(mock_connect):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [(1,), ("cat", "sch", "user@example.com")]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = mock_conn

    result = validate_databricks_sql_warehouse(
        {
            "server_hostname": "adb-test.azuredatabricks.net",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "dapi-test",
            "catalog": "test",
        }
    )
    assert result["status"] == "success"
    assert result["mode"] == "sql_warehouse"


@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_validate_databricks_api_connection_invalid_token(mock_urlopen):
    host = "adb-test.azuredatabricks.net"
    mock_urlopen.side_effect = _mock_urlopen(
        {
            f"https://{host}/api/2.0/clusters/list": (
                403,
                {"message": "Invalid access token."},
            ),
        }
    )
    with pytest.raises(ValueError, match="Invalid access token"):
        validate_databricks_api_connection(
            {
                "server_hostname": host,
                "access_token": "dapi-bad",
                "catalog": "prod",
            }
        )


def _sequential_urlopen(steps):
    index = {"n": 0}

    def _open(req, timeout=20):
        status, payload = steps[index["n"]]
        index["n"] += 1
        if status >= 400:
            import urllib.error

            body = json.dumps(payload).encode() if payload is not None else b""
            raise urllib.error.HTTPError(req.full_url, status, "error", hdrs=None, fp=io.BytesIO(body))
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    return _open


_DEST = {
    "server_hostname": "adb-test.azuredatabricks.net",
    "http_path": "/sql/1.0/warehouses/wh-1",
    "access_token": "dapi-test",
    "catalog": "test",
    "schema": "sql_datasyncher",
}


@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_create_engine_without_schema_when_ensure_schema_false():
    dest = {
        "server_hostname": "adb-test.azuredatabricks.net",
        "http_path": "/sql/1.0/warehouses/abc",
        "access_token": "token",
        "catalog": "test",
    }
    engine, schema = create_databricks_sqlalchemy_like_engine(
        dest,
        ensure_schema=False,
    )
    assert schema == ""
    assert engine.config["catalog"] == "test"


def test_schema_ensure_fails_immediately_when_warehouse_health_failed(mock_urlopen):
    mock_urlopen.side_effect = _sequential_urlopen(
        [
            (
                200,
                {
                    "name": "test_wh_sql",
                    "state": "STARTING",
                    "health": {
                        "status": "FAILED",
                        "message": "RESOURCE_EXHAUSTED: Cannot create the resource, please try again later.",
                    },
                },
            )
        ]
    )
    with pytest.raises(DatabricksWarehouseUnavailable, match="RESOURCE_EXHAUSTED"):
        create_databricks_sqlalchemy_like_engine(_DEST, ensure_schema=True)
    assert mock_urlopen.call_count == 1


@patch("datasyncher_connections.databricks.connector.time.sleep")
@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_schema_statement_fails_when_warehouse_stays_stopped(mock_urlopen, _sleep):
    mock_urlopen.side_effect = _sequential_urlopen(
        [
            (200, {"name": "test_wh_sql", "state": "STOPPED", "health": {}}),
            (
                200,
                {"statement_id": "stmt-1", "status": {"state": "PENDING"}},
            ),
            (200, {"name": "test_wh_sql", "state": "STOPPED", "health": {}}),
            (200, {}),
        ]
    )
    with pytest.raises(DatabricksWarehouseUnavailable, match="STOPPED and did not start"):
        execute_sql_statement_bounded(
            _DEST,
            "CREATE SCHEMA IF NOT EXISTS `test`.`sql_datasyncher`",
            stopped_grace_seconds=0,
            budget_seconds=180,
        )
    assert mock_urlopen.call_count == 4


@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_schema_ensure_succeeds_when_warehouse_is_running(mock_urlopen):
    mock_urlopen.side_effect = _sequential_urlopen(
        [
            (200, {"name": "test_wh_sql", "state": "RUNNING", "health": {}}),
            (
                200,
                {"statement_id": "stmt-1", "status": {"state": "SUCCEEDED"}},
            ),
        ]
    )
    engine, schema = create_databricks_sqlalchemy_like_engine(_DEST, ensure_schema=True)
    assert schema == "sql_datasyncher"
    assert engine.config["catalog"] == "test"


def test_databricks_result_keys_from_description():
    result = _DatabricksResult(
        rows=[("a", 1)],
        description=[("ID",), ("COUNT",)],
    )
    assert result.keys() == ["ID", "COUNT"]
    assert result.fetchone() == ("a", 1)


_CLUSTER_DEST = {
    "server_hostname": "adb-test.azuredatabricks.net",
    "http_path": "/sql/protocolv1/o/7405607423243625/1014-055536-2my5dndu",
    "access_token": "dapi-test",
    "catalog": "test",
}


@patch("datasyncher_connections.databricks.connector.time.sleep")
@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_ensure_cluster_compute_running_auto_starts_terminated(mock_urlopen, _sleep):
    mock_urlopen.side_effect = _sequential_urlopen(
        [
            (
                200,
                {
                    "cluster_id": "1014-055536-2my5dndu",
                    "cluster_name": "test_compute",
                    "state": "TERMINATED",
                },
            ),
            (200, {}),  # clusters/start
            (
                200,
                {
                    "cluster_id": "1014-055536-2my5dndu",
                    "cluster_name": "test_compute",
                    "state": "PENDING",
                },
            ),
            (
                200,
                {
                    "cluster_id": "1014-055536-2my5dndu",
                    "cluster_name": "test_compute",
                    "state": "RUNNING",
                },
            ),
        ]
    )
    cluster = ensure_cluster_compute_running(_CLUSTER_DEST, budget_seconds=60, poll_seconds=1)
    assert cluster["state"] == "RUNNING"
    assert mock_urlopen.call_count == 4


@patch("datasyncher_connections.databricks.connector.urllib.request.urlopen")
def test_assert_databricks_sql_endpoint_available_running_cluster(mock_urlopen):
    mock_urlopen.side_effect = _sequential_urlopen(
        [
            (
                200,
                {
                    "cluster_id": "1014-055536-2my5dndu",
                    "cluster_name": "test_compute",
                    "state": "RUNNING",
                },
            ),
        ]
    )
    cluster = assert_databricks_sql_endpoint_available(_CLUSTER_DEST)
    assert cluster["state"] == "RUNNING"
