"""Databricks SQL warehouse connection helpers."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any, Iterator

_WAREHOUSE_POLL_SECONDS = 5
_DEFAULT_WAREHOUSE_COLD_START_SECONDS = 900  # 15 minutes


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def warehouse_cold_start_seconds() -> int:
    """
    Max wait for a SQL warehouse to accept queries (cold start / resume).

    Override with ``DATABRICKS_WAREHOUSE_COLD_START_SECONDS`` (default 900).
    """
    return _env_int(
        "DATABRICKS_WAREHOUSE_COLD_START_SECONDS",
        _DEFAULT_WAREHOUSE_COLD_START_SECONDS,
        minimum=60,
    )


def warehouse_stopped_grace_seconds() -> int:
    """
    How long a warehouse may stay ``STOPPED`` while a statement is pending.

    Defaults to the cold-start budget so slow launches are not cut off at 45s.
    Override with ``DATABRICKS_WAREHOUSE_STOPPED_GRACE_SECONDS``.
    """
    return _env_int(
        "DATABRICKS_WAREHOUSE_STOPPED_GRACE_SECONDS",
        warehouse_cold_start_seconds(),
        minimum=60,
    )


class DatabricksWarehouseUnavailable(RuntimeError):
    """SQL warehouse cannot run the statement. Callers should fail the migration."""


def normalize_databricks_config(config: dict | None) -> dict:
    """Normalize destination config keys used by Dynamics → Databricks."""
    if not isinstance(config, dict):
        raise ValueError("Databricks config must be a dictionary")

    normalized = dict(config)
    if "host" in normalized and "server_hostname" not in normalized:
        normalized["server_hostname"] = normalized["host"]
    if "token" in normalized and "access_token" not in normalized:
        normalized["access_token"] = normalized["token"]
    if "schema" in normalized and normalized.get("schema"):
        normalized["schema"] = str(normalized["schema"]).strip()
    if "catalog" in normalized and normalized.get("catalog"):
        normalized["catalog"] = str(normalized["catalog"]).strip()
    http_path = str(normalized.get("http_path") or "").strip()
    if http_path:
        if not http_path.startswith("/"):
            http_path = f"/{http_path.lstrip('/')}"
        normalized["http_path"] = http_path
    return normalized


def is_databricks_sql_warehouse_http_path(http_path: str) -> bool:
    path = str(http_path or "").strip().lower()
    return "/sql/1.0/warehouses/" in path or "/sql/2.0/warehouses/" in path


def is_databricks_cluster_compute_http_path(http_path: str) -> bool:
    """All-purpose / job cluster SQL endpoint (JDBC/ODBC HTTP path)."""
    path = str(http_path or "").strip().lower()
    return "protocolv1" in path or "/sql/protocolv1/" in path


def describe_databricks_http_path_issue(http_path: str) -> str | None:
    """
    Return a user-facing hint when http_path is not a supported Databricks SQL endpoint.
    """
    path = str(http_path or "").strip().lower()
    if not path:
        return None
    if is_databricks_sql_warehouse_http_path(path) or is_databricks_cluster_compute_http_path(path):
        return None
    return (
        "http_path must be a Databricks SQL connection HTTP path: SQL warehouse "
        "(/sql/1.0/warehouses/<warehouse-id>) or all-purpose compute cluster "
        "(/sql/protocolv1/o/<workspace>/<cluster-id>) from Connection details."
    )


def _required(config: dict, *keys: str) -> str:
    for key in keys:
        value = config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise ValueError(f"Missing required Databricks config field(s): {', '.join(keys)}")


def validate_databricks_api_connection(config: dict) -> dict[str, Any]:
    """
    Validate workspace reachability and token auth via REST API.

    Does not require a running SQL warehouse. Use for destination-config
    create/update/test before migrations need SQL execution.
    """
    cfg = normalize_databricks_config(config)
    server_hostname = _required(cfg, "server_hostname", "host")
    access_token = _required(cfg, "access_token", "token")
    catalog = str(cfg.get("catalog") or "").strip()

    headers = {"Authorization": f"Bearer {access_token}"}
    base = f"https://{server_hostname}"

    def _http_error_message(exc: urllib.error.HTTPError, *, context: str) -> str:
        body = ""
        try:
            raw = exc.read()
            if raw:
                payload = json.loads(raw.decode())
                body = str(
                    payload.get("message")
                    or payload.get("error_code")
                    or payload.get("detail")
                    or ""
                ).strip()
        except Exception:
            body = ""
        if exc.code in (401, 403):
            return (
                body
                or "Invalid Databricks access token. Generate a new personal access token and try again."
            )
        if body:
            return f"{context} failed ({exc.code}): {body}"
        return f"{context} failed with HTTP {exc.code}"

    try:
        req = urllib.request.Request(f"{base}/api/2.0/clusters/list", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise ValueError(_http_error_message(exc, context="Databricks authentication")) from exc
    except urllib.error.URLError as exc:
        raise ValueError(
            f"Unable to reach Databricks host '{server_hostname}': {exc.reason}"
        ) from exc

    cluster_count = len(payload.get("clusters") or [])

    catalog_ok = None
    if catalog:
        cat_req = urllib.request.Request(
            f"{base}/api/2.1/unity-catalog/catalogs/{catalog}",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(cat_req, timeout=20) as resp:
                cat_payload = json.loads(resp.read().decode())
            catalog_ok = cat_payload.get("name") == catalog
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                catalog_ok = False
            elif exc.code in (401, 403):
                raise ValueError(
                    _http_error_message(exc, context="Databricks catalog access")
                ) from exc
            else:
                raise ValueError(
                    _http_error_message(exc, context=f"Databricks catalog '{catalog}' lookup")
                ) from exc

    if catalog and catalog_ok is False:
        raise ValueError(f"Databricks catalog '{catalog}' was not found")

    return {
        "status": "success",
        "mode": "api_only",
        "cluster_count": cluster_count,
        "catalog": catalog or None,
        "catalog_ok": catalog_ok,
    }


def validate_databricks_sql_warehouse(
    config: dict,
    *,
    socket_timeout: int | None = None,
) -> dict[str, Any]:
    """
    Verify SQL warehouse reachability (SELECT 1). Does not require destination schema.

    Use after API auth when http_path is configured (destination save/test) or for
    full migration connectivity checks.
    """
    cfg = normalize_databricks_config(config)
    server_hostname = _required(cfg, "server_hostname", "host")
    http_path = _required(cfg, "http_path")
    path_hint = describe_databricks_http_path_issue(http_path)
    if path_hint:
        raise ValueError(path_hint)
    access_token = _required(cfg, "access_token", "token")

    timeout = socket_timeout if socket_timeout is not None else warehouse_cold_start_seconds()
    connect_kwargs: dict[str, Any] = {
        "server_hostname": server_hostname,
        "http_path": http_path,
        "access_token": access_token,
        "_socket_timeout": timeout,
        "_retry_timeout": timeout,
        "_retry_stop_after_attempts": 3,
    }
    catalog = str(cfg.get("catalog") or "").strip()
    schema = str(cfg.get("schema") or "").strip()
    if catalog:
        connect_kwargs["catalog"] = catalog
    if schema:
        connect_kwargs["schema"] = schema

    with create_databricks_connection(connect_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            ping = cur.fetchone()
            if not ping or ping[0] != 1:
                raise ValueError("Databricks SQL endpoint ping failed (SELECT 1)")
            cur.execute("SELECT current_catalog(), current_schema(), current_user()")
            ctx = cur.fetchone()

    sql_mode = (
        "sql_cluster_compute"
        if is_databricks_cluster_compute_http_path(http_path)
        else "sql_warehouse"
    )
    return {
        "status": "success",
        "mode": sql_mode,
        "select_1": ping,
        "context": ctx,
        "catalog": catalog or None,
        "schema": schema or None,
    }


def create_databricks_connection(config: dict):
    """Open a raw databricks-sql-connector connection."""
    from databricks import sql

    cfg = normalize_databricks_config(config)
    server_hostname = _required(cfg, "server_hostname", "host")
    http_path = _required(cfg, "http_path")
    access_token = _required(cfg, "access_token", "token")

    connect_kwargs: dict[str, Any] = {
        "server_hostname": server_hostname,
        "http_path": http_path,
        "access_token": access_token,
    }
    catalog = cfg.get("catalog")
    schema = cfg.get("schema")
    if catalog:
        connect_kwargs["catalog"] = catalog
    if schema:
        connect_kwargs["schema"] = schema
    timeout = cfg.get("_socket_timeout")
    if timeout is None:
        timeout = warehouse_cold_start_seconds()
    else:
        timeout = int(timeout)
    connect_kwargs["_socket_timeout"] = timeout
    connect_kwargs["_retry_timeout"] = cfg.get("_retry_timeout", timeout)
    connect_kwargs["_retry_stop_after_attempts"] = int(
        cfg.get("_retry_stop_after_attempts") or 3
    )
    return sql.connect(**connect_kwargs)


class _DatabricksResult:
    def __init__(self, rows: list, description=None):
        self._rows = list(rows or [])
        self._idx = 0
        self.description = description

    def keys(self):
        """SQLAlchemy CursorResult compatibility for column names."""
        if not self.description:
            return []
        names: list[str] = []
        for col in self.description:
            if isinstance(col, (list, tuple)) and col:
                names.append(str(col[0]))
            elif hasattr(col, "name"):
                names.append(str(col.name))
            else:
                names.append(str(col))
        return names

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self):
        remaining = self._rows[self._idx :]
        self._idx = len(self._rows)
        return remaining

    def scalar(self):
        row = self.fetchone()
        if row is None:
            return None
        return row[0]


class _DatabricksConnectionProxy:
    """Minimal SQLAlchemy-like connection proxy over databricks-sql-connector."""

    def __init__(self, raw_conn):
        self._conn = raw_conn

    def execute(self, statement, *args, **kwargs):
        sql = getattr(statement, "text", None) or str(statement)
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql)
            rows = []
            if cursor.description:
                rows = cursor.fetchall()
            return _DatabricksResult(rows, description=cursor.description)
        finally:
            cursor.close()

    def commit(self):
        # Databricks SQL sessions auto-commit DDL/DML in warehouse mode.
        return None

    def close(self):
        self._conn.close()


class DatabricksSQLEngine:
    """Thin engine facade so Dynamics load code can use engine.connect()."""

    def __init__(self, config: dict):
        self.config = normalize_databricks_config(config)

    @contextmanager
    def connect(self) -> Iterator[_DatabricksConnectionProxy]:
        # Fail before sql.connect(). The connector polls QUEUED statements forever
        # when the warehouse health is FAILED (for example RESOURCE_EXHAUSTED).
        assert_databricks_sql_endpoint_available(self.config)
        raw = create_databricks_connection(self.config)
        proxy = _DatabricksConnectionProxy(raw)
        try:
            yield proxy
        finally:
            proxy.close()

    @contextmanager
    def begin(self) -> Iterator[_DatabricksConnectionProxy]:
        with self.connect() as conn:
            yield conn


def create_databricks_sqlalchemy_like_engine(
    config: dict,
    *,
    ensure_schema: bool = True,
) -> tuple[DatabricksSQLEngine, str]:
    """
    Build a Databricks engine-like object and return (engine, schema).

    Schema comes from destination_config['schema'] (injected from Connection.dst_schema).
    """
    cfg = normalize_databricks_config(config)
    schema = str(cfg.get("schema") or "").strip()
    if not schema and ensure_schema:
        raise ValueError("Databricks schema is required (destination_schema / Connection.dst_schema)")

    catalog = str(cfg.get("catalog") or "").strip()
    if not catalog:
        raise ValueError("Databricks catalog is required")

    engine = DatabricksSQLEngine(cfg)

    if ensure_schema and schema:
        execute_sql_statement_bounded(
            cfg,
            f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`",
        )

    return engine, schema


def _cluster_id_from_http_path(http_path: str) -> str:
    path = str(http_path or "").rstrip("/")
    cluster_id = path.split("/")[-1].strip()
    if not cluster_id:
        raise ValueError("Databricks cluster http_path is missing a cluster id")
    return cluster_id


def _warehouse_id(config: dict) -> str:
    http_path = _required(config, "http_path")
    warehouse_id = http_path.rstrip("/").split("/")[-1].strip()
    if not warehouse_id:
        raise ValueError("Databricks http_path is missing a SQL warehouse id")
    return warehouse_id


def _api_json(config: dict, method: str, path: str, body: dict | None = None, timeout: int = 20) -> dict:
    cfg = normalize_databricks_config(config)
    server_hostname = _required(cfg, "server_hostname", "host")
    access_token = _required(cfg, "access_token", "token")
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"https://{server_hostname}{path}",
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return {}
            parsed = json.loads(raw.decode())
            return parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read()
            if raw:
                parsed = json.loads(raw.decode())
                detail = str(parsed.get("message") or parsed.get("error_code") or "").strip()
        except Exception:
            detail = ""
        message = detail or f"HTTP {exc.code}"
        raise DatabricksWarehouseUnavailable(
            f"Databricks SQL warehouse request failed: {message}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DatabricksWarehouseUnavailable(
            f"Unable to reach Databricks SQL warehouse: {exc.reason}"
        ) from exc


def fetch_sql_warehouse(config: dict) -> dict:
    """Return the SQL warehouse resource for this destination http_path."""
    cfg = normalize_databricks_config(config)
    return _api_json(cfg, "GET", f"/api/2.0/sql/warehouses/{_warehouse_id(cfg)}")


def warehouse_unavailable_reason(warehouse: dict) -> str | None:
    """Return an error string when the warehouse cannot accept queries."""
    health = warehouse.get("health") or {}
    status = str(health.get("status") or "").strip().upper()
    message = str(health.get("message") or "").strip()
    state = str(warehouse.get("state") or "").strip().upper()
    name = str(warehouse.get("name") or "").strip() or "SQL warehouse"
    combined = f"{status} {message}".upper()
    if (
        "QUOTAEXCEEDED" in combined
        or "QUOTA EXCEEDED" in combined
        or ("QUOTA" in combined and "CORE" in combined)
    ):
        return (
            f"Databricks SQL warehouse '{name}' cannot start: Azure regional vCPU quota "
            f"is exceeded ({message or status}). Stop other VMs/warehouses in the same "
            f"Azure region or request a quota increase, then start the warehouse in "
            f"Databricks and try again."
        )
    if status == "FAILED" or "RESOURCE_EXHAUSTED" in combined:
        detail = message or status or state or "unavailable"
        return (
            f"Databricks SQL warehouse '{name}' cannot run queries "
            f"({state or 'UNKNOWN'}): {detail}"
        )
    if state in ("DELETED", "DELETING"):
        return f"Databricks SQL warehouse '{name}' is {state}."
    return None


def assert_sql_warehouse_available(config: dict) -> dict:
    """Raise DatabricksWarehouseUnavailable when warehouse health is FAILED."""
    warehouse = fetch_sql_warehouse(config)
    reason = warehouse_unavailable_reason(warehouse)
    if reason:
        raise DatabricksWarehouseUnavailable(reason)
    return warehouse


def fetch_cluster_compute(config: dict) -> dict:
    """Return the all-purpose compute cluster for this destination http_path."""
    cfg = normalize_databricks_config(config)
    cluster_id = _cluster_id_from_http_path(_required(cfg, "http_path"))
    return _api_json(
        cfg,
        "POST",
        "/api/2.0/clusters/get",
        {"cluster_id": cluster_id},
    )


def cluster_compute_unavailable_reason(cluster: dict) -> str | None:
    """Return a hard-failure reason, or None when the cluster can (still) start/run."""
    state = str(cluster.get("state") or "").strip().upper()
    name = str(cluster.get("cluster_name") or cluster.get("cluster_id") or "").strip() or "cluster"
    if state in ("ERROR", "UNKNOWN"):
        state_message = str(cluster.get("state_message") or "").strip()
        detail = f" ({state_message})" if state_message else ""
        return (
            f"Databricks all-purpose compute cluster '{name}' is {state}{detail}. "
            f"Fix the cluster in Databricks (Compute) and try again."
        )
    return None


def ensure_cluster_compute_running(
    config: dict,
    *,
    budget_seconds: int | None = None,
    poll_seconds: int = _WAREHOUSE_POLL_SECONDS,
) -> dict:
    """
    Ensure the destination all-purpose cluster is RUNNING.

    Auto-starts TERMINATED clusters (same idea as SQL warehouse cold start) so
    customers do not have to click Start in Databricks before every migration.
    """
    cfg = normalize_databricks_config(config)
    cluster_id = _cluster_id_from_http_path(_required(cfg, "http_path"))
    budget = int(budget_seconds if budget_seconds is not None else warehouse_cold_start_seconds())
    poll = max(2, int(poll_seconds))
    started = time.monotonic()
    start_requested = False

    while True:
        cluster = fetch_cluster_compute(cfg)
        state = str(cluster.get("state") or "").strip().upper()
        name = (
            str(cluster.get("cluster_name") or cluster.get("cluster_id") or "").strip()
            or cluster_id
        )
        hard_fail = cluster_compute_unavailable_reason(cluster)
        if hard_fail and state != "TERMINATED":
            raise DatabricksWarehouseUnavailable(hard_fail)
        if state == "RUNNING":
            return cluster
        if state in ("PENDING", "RESIZING", "RESTARTING", "STARTING"):
            if time.monotonic() - started >= budget:
                raise DatabricksWarehouseUnavailable(
                    f"Databricks all-purpose compute cluster '{name}' stayed {state} "
                    f"for more than {budget}s. Start it in Databricks (Compute) or "
                    f"increase DATABRICKS_WAREHOUSE_COLD_START_SECONDS."
                )
            time.sleep(poll)
            continue
        if state == "TERMINATED":
            if not start_requested:
                try:
                    _api_json(
                        cfg,
                        "POST",
                        "/api/2.0/clusters/start",
                        {"cluster_id": cluster_id},
                    )
                except Exception as exc:
                    raise DatabricksWarehouseUnavailable(
                        f"Databricks all-purpose compute cluster '{name}' is TERMINATED "
                        f"and could not be started automatically. Start it in Databricks "
                        f"(Compute) and try again. Detail: {exc}"
                    ) from exc
                start_requested = True
            if time.monotonic() - started >= budget:
                raise DatabricksWarehouseUnavailable(
                    f"Databricks all-purpose compute cluster '{name}' did not become "
                    f"RUNNING within {budget}s after auto-start."
                )
            time.sleep(poll)
            continue
        raise DatabricksWarehouseUnavailable(
            f"Databricks all-purpose compute cluster '{name}' is in unsupported "
            f"state {state or 'UNKNOWN'}."
        )


def assert_databricks_sql_endpoint_available(config: dict) -> dict:
    """Pre-flight SQL warehouse or all-purpose cluster before sql.connect()."""
    cfg = normalize_databricks_config(config)
    http_path = str(cfg.get("http_path") or "")
    if is_databricks_cluster_compute_http_path(http_path):
        return ensure_cluster_compute_running(cfg)
    return assert_sql_warehouse_available(cfg)


def _execute_sql_via_connector(config: dict, statement: str) -> None:
    with create_databricks_connection(config) as conn:
        with conn.cursor() as cur:
            cur.execute(statement)


def _statement_state(payload: dict) -> str:
    status = payload.get("status") or {}
    return str(status.get("state") or "").strip().upper()


def _statement_error_message(payload: dict, warehouse_name: str) -> str:
    status = payload.get("status") or {}
    error = status.get("error") or {}
    message = str(error.get("message") or status.get("error_message") or "").strip()
    state = _statement_state(payload) or "FAILED"
    if message:
        return f"Databricks SQL warehouse '{warehouse_name}' statement {state}: {message}"
    return f"Databricks SQL warehouse '{warehouse_name}' statement {state}."


def _cancel_sql_statement(config: dict, statement_id: str) -> None:
    if not statement_id:
        return
    try:
        _api_json(config, "POST", f"/api/2.0/sql/statements/{statement_id}/cancel", {})
    except Exception:
        return


def execute_sql_statement_bounded(
    config: dict,
    statement: str,
    *,
    stopped_grace_seconds: int | None = None,
    budget_seconds: int | None = None,
    poll_seconds: int = _WAREHOUSE_POLL_SECONDS,
) -> None:
    """
    Run one SQL statement and raise if the warehouse cannot execute it.

    The databricks-sql-connector polls statement status on HTTP 200 forever while
    a warehouse stays QUEUED. This path checks warehouse health and stops when
    the warehouse is FAILED, stays STOPPED, or never leaves STARTING.
    """
    if stopped_grace_seconds is None:
        stopped_grace_seconds = warehouse_stopped_grace_seconds()
    if budget_seconds is None:
        budget_seconds = warehouse_cold_start_seconds()

    cfg = normalize_databricks_config(config)
    http_path = str(cfg.get("http_path") or "")
    if is_databricks_cluster_compute_http_path(http_path):
        assert_databricks_sql_endpoint_available(cfg)
        _execute_sql_via_connector(cfg, statement)
        return

    warehouse = assert_sql_warehouse_available(cfg)
    warehouse_id = _warehouse_id(cfg)
    warehouse_name = str(warehouse.get("name") or warehouse_id)
    submitted = _api_json(
        cfg,
        "POST",
        "/api/2.0/sql/statements",
        {
            "warehouse_id": warehouse_id,
            "statement": statement,
            "wait_timeout": "5s",
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
        },
    )
    statement_id = str(submitted.get("statement_id") or "").strip()
    if not statement_id:
        raise DatabricksWarehouseUnavailable(
            f"Databricks SQL warehouse '{warehouse_name}' did not accept the statement."
        )

    started = time.monotonic()
    payload = submitted
    while True:
        state = _statement_state(payload)
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELED", "CLOSED"):
            _cancel_sql_statement(cfg, statement_id)
            raise DatabricksWarehouseUnavailable(
                _statement_error_message(payload, warehouse_name)
            )

        current = fetch_sql_warehouse(cfg)
        reason = warehouse_unavailable_reason(current)
        if reason:
            _cancel_sql_statement(cfg, statement_id)
            raise DatabricksWarehouseUnavailable(reason)

        elapsed = time.monotonic() - started
        warehouse_state = str(current.get("state") or "").strip().upper()
        warehouse_name = str(current.get("name") or warehouse_name)
        if warehouse_state == "STOPPED" and elapsed >= stopped_grace_seconds:
            _cancel_sql_statement(cfg, statement_id)
            raise DatabricksWarehouseUnavailable(
                f"Databricks SQL warehouse '{warehouse_name}' is STOPPED and did not start. "
                f"Statement stayed {state or 'PENDING'}."
            )
        if elapsed >= budget_seconds:
            _cancel_sql_statement(cfg, statement_id)
            raise DatabricksWarehouseUnavailable(
                f"Databricks SQL warehouse '{warehouse_name}' is {warehouse_state or 'unavailable'} "
                f"and the statement stayed {state or 'PENDING'}."
            )
        time.sleep(poll_seconds)
        payload = _api_json(cfg, "GET", f"/api/2.0/sql/statements/{statement_id}")
