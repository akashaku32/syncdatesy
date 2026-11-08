"""Unit tests for migration_common registry and helpers."""

import sys
from pathlib import Path

import pytest

MIGRATION_ROOT = Path(__file__).resolve().parents[1]
if str(MIGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(MIGRATION_ROOT))

from services.migration_common.failure_tracker import MigrationFailureTracker
from services.migration_common.registry import (
    _FUNCTION_PATH_MAP,
    _REGISTRY,
    get,
    get_by_function_path,
    register,
    run,
)
from services.migration_common.retry import retry_with_exponential_backoff


@pytest.fixture(autouse=True)
def _clear_registry():
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def test_get_by_function_path_returns_tuple():
    pair = get_by_function_path("/Databricks-MicrosoftDynamics365_FO-migration-yield")
    assert pair is not None
    assert isinstance(pair, tuple)
    assert len(pair) == 2
    source, dest = pair
    assert source == "microsoftdynamics365_fo"
    assert dest == "databricks"
    assert get_by_function_path("/unknown-path") is None


def test_register_and_run_callable():
    seen = {}

    def handler(payload):
        seen["payload"] = payload
        return {"ok": True}

    register("salesforce", "snowflake", handler)
    assert get("salesforce", "snowflake") is handler
    result = run("salesforce", "snowflake", {"connection_id": 1})
    assert result == {"ok": True}
    assert seen["payload"]["connection_id"] == 1


def test_run_object_with_run_method():
    class Handler:
        def run(self, payload):
            return payload.get("x", 0) + 1

    register("a", "b", Handler())
    assert run("a", "b", {"x": 41}) == 42


def test_failure_tracker_add_failure():
    tracker = MigrationFailureTracker()
    tracker.add_failure("Account", "boom", service_name="test")
    assert tracker.has_failures()
    entry = tracker.failed_tables[0]
    assert entry["object"] == "Account"
    assert entry["error"] == "boom"
    assert entry["timestamp"] == entry["failure_time"]
    names = MigrationFailureTracker.failed_table_names(tracker.failed_tables)
    assert names == {"Account"}


def test_retry_with_exponential_backoff_succeeds_after_transient_failure(monkeypatch):
    sleeps = []
    monkeypatch.setattr("services.migration_common.retry.time.sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise ValueError("transient")
        return "done"

    result = retry_with_exponential_backoff(fn, retries=3, base_delay=1.0, exceptions=(ValueError,))
    assert result == "done"
    assert attempts["n"] == 2
    assert len(sleeps) == 1


def test_function_path_map_includes_netsuite_pairs():
    assert _FUNCTION_PATH_MAP["/Snowflake-NetSuite-migration-yield"] == (
        "netsuite",
        "snowflake",
    )
    assert _FUNCTION_PATH_MAP["/Databricks-NetSuite-migration-yield"] == (
        "netsuite",
        "databricks",
    )


def test_function_path_map_includes_salesforce_snowflake_pairs():
    assert _FUNCTION_PATH_MAP["/Salesforce-Snowflake-migration-yield"] == (
        "snowflake",
        "salesforce",
    )
    assert _FUNCTION_PATH_MAP["/Snowflake-Salesforce-migration-yield"] == (
        "salesforce",
        "snowflake",
    )
