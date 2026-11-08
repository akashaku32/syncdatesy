"""Tests for pipeline fleet lease + retryable migration error helpers."""
from unittest.mock import patch

import pytest

from services.dynamics.as_source.to_databricks import _is_retryable_migration_error
from utils.pipeline_fleet_lease import parse_pipeline_run_id


def test_parse_pipeline_run_id_from_job_name():
    assert parse_pipeline_run_id(
        "pipeline_run_1644_node_182__spark_prod_general_databrivks_con_manual_13414_generaljournalaccountentry"
    ) == 1644
    assert parse_pipeline_run_id(None) is None


def test_is_retryable_migration_error_detects_adls_write_timeout():
    err = ("Connection aborted.", TimeoutError("The write operation timed out"))
    assert _is_retryable_migration_error(err) is True


def test_is_retryable_migration_error_rejects_auth_errors():
    assert _is_retryable_migration_error(ValueError("invalid_grant")) is False


def test_warehouse_capacity_error_is_terminal():
    class DatabricksWarehouseUnavailable(RuntimeError):
        pass

    err = DatabricksWarehouseUnavailable(
        "Databricks SQL warehouse 'test_wh_sql' cannot run queries (STARTING): "
        "RESOURCE_EXHAUSTED: Cannot create the resource, please try again later."
    )
    assert _is_retryable_migration_error(err) is False


@patch("celery_job_worker_pool.refresh_flow_fleet_lease")
@patch("celery_worker._get_redis")
def test_refresh_pipeline_flow_fleet_lease_calls_job_pool(mock_redis, mock_refresh):
    from utils.pipeline_fleet_lease import refresh_pipeline_flow_fleet_lease

    refresh_pipeline_flow_fleet_lease(job_name="pipeline_run_99_node_1")
    mock_refresh.assert_called_once_with(mock_redis.return_value, 99)
