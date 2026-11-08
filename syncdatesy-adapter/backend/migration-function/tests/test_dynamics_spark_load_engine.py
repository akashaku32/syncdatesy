"""Tests for Dynamics → Databricks Spark bulk-load path helpers."""

import json
from unittest.mock import MagicMock, patch

import pytest

from services.databricks.load_helpers import (
    AZURE_SPARK_LOAD_STRATEGY_BULK,
    AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER,
    AZURE_SPARK_READ_MODE_DIRECT,
    AZURE_SPARK_READ_MODE_STAGING,
    LOAD_ENGINE_SPARK_JOB,
    LOAD_ENGINE_SQL_WAREHOUSE,
    SOURCE_TYPE_DYNAMICS,
    SOURCE_TYPE_SALESFORCE,
    SPARK_LANDING_CLOUD_AZURE,
    SPARK_LANDING_CLOUD_GCS,
    resolve_azure_spark_load_strategy,
    resolve_azure_spark_read_mode,
    resolve_spark_bulk_job_timeout_seconds,
    resolve_databricks_adls_settings,
    resolve_databricks_adls_staging_location_name,
    resolve_databricks_adls_staging_settings,
    resolve_platform_azure_staging_sp,
    resolve_databricks_load_engine,
    resolve_dynamics_adls_export_url,
    resolve_dynamics_adls_staging_url,
    resolve_spark_landing_cloud,
    verify_databricks_adls_prefix_readable,
)
from utils.azure_path_utils import (
    build_abfss_uri,
    parse_abfss_uri,
    parse_account_name_from_connection_string,
)
from services.databricks.spark_job_client import (
    _destination_wants_dedicated_serverless_job,
    _ensure_workspace_parent_dirs,
    _fetch_run_failure_details,
    _job_settings_payload,
    _resolve_spark_compute_mode,
    _workspace_parent_path,
    dedicated_dynamics_spark_job_name,
    dedicated_dynamics_spark_job_name_for_destination,
    _extract_row_count_from_output,
    extract_notebook_result_value,
    resolve_spark_run_row_count,
)
from services.dynamics import source_common
from services.dynamics.source_common import (
    cleanup_adls_staging_after_spark_merge,
    count_adls_staging_csv_blobs,
    discover_table_date_dir_for_spark_direct,
    dynamics_adls_staging_blob_path,
    dynamics_adls_staging_blob_prefix,
    dynamics_adls_staging_landing_prefix,
    dynamics_adls_staging_table_landing_prefix,
    land_table_bulk_to_adls_staging,
    dynamics_gcs_landing_object_key,
    dynamics_gcs_landing_prefix,
    land_table_date_dir_to_adls_staging,
    resolve_adls_staging_cleanup_enabled,
    resolve_bulk_staging_reuse_enabled,
    verify_adls_staging_has_csvs,
    write_landing_manifest,
)


@pytest.fixture(autouse=True)
def _reset_bulk_copy_active_count():
    source_common._BULK_COPY_ACTIVE_COUNT = 0
    yield
    source_common._BULK_COPY_ACTIVE_COUNT = 0


def test_resolve_load_engine_dynamics_initial_defaults_to_spark_job():
    assert (
        resolve_databricks_load_engine(
            source_type=SOURCE_TYPE_DYNAMICS,
            is_initial_load=True,
        )
        == LOAD_ENGINE_SPARK_JOB
    )


def test_resolve_load_engine_dynamics_incremental_defaults_to_spark_job():
    assert (
        resolve_databricks_load_engine(
            source_type=SOURCE_TYPE_DYNAMICS,
            is_initial_load=False,
        )
        == LOAD_ENGINE_SPARK_JOB
    )


def test_resolve_load_engine_salesforce_always_sql_warehouse():
    assert (
        resolve_databricks_load_engine(
            source_type=SOURCE_TYPE_SALESFORCE,
            is_initial_load=True,
            destination_connection={"load_engine": "spark_job"},
        )
        == LOAD_ENGINE_SQL_WAREHOUSE
    )


def test_resolve_load_engine_unknown_source_defaults_to_sql_warehouse():
    assert (
        resolve_databricks_load_engine(
            source_type="snowflake",
            is_initial_load=True,
        )
        == LOAD_ENGINE_SQL_WAREHOUSE
    )


def test_resolve_load_engine_legacy_explicit_sql_warehouse():
    assert (
        resolve_databricks_load_engine(
            source_type=SOURCE_TYPE_DYNAMICS,
            is_initial_load=True,
            destination_connection={"load_engine": "sql_warehouse"},
        )
        == LOAD_ENGINE_SQL_WAREHOUSE
    )


def test_resolve_load_engine_legacy_explicit_spark_job_all_phases():
    for is_initial in (True, False):
        assert (
            resolve_databricks_load_engine(
                source_type=SOURCE_TYPE_DYNAMICS,
                is_initial_load=is_initial,
                destination_connection={"load_engine": "spark_job"},
            )
            == LOAD_ENGINE_SPARK_JOB
        )


def test_resolve_load_engine_env_override(monkeypatch):
    monkeypatch.setenv("DYNAMICS_DBX_LOAD_ENGINE", "sql_warehouse")
    for is_initial in (True, False):
        assert (
            resolve_databricks_load_engine(
                source_type=SOURCE_TYPE_DYNAMICS,
                is_initial_load=is_initial,
            )
            == LOAD_ENGINE_SQL_WAREHOUSE
        )


def test_resolve_spark_landing_cloud_explicit_azure():
    assert (
        resolve_spark_landing_cloud({"spark_landing_cloud": "azure"})
        == SPARK_LANDING_CLOUD_AZURE
    )


def test_resolve_spark_landing_cloud_infers_from_azure_host():
    assert (
        resolve_spark_landing_cloud(
            {"server_hostname": "adb-123.17.azuredatabricks.net"}
        )
        == SPARK_LANDING_CLOUD_AZURE
    )


def test_resolve_spark_landing_cloud_infers_gcs_from_gcp_host():
    assert (
        resolve_spark_landing_cloud(
            {"server_hostname": "dbc-abc123.gcp.databricks.com"}
        )
        == SPARK_LANDING_CLOUD_GCS
    )


def test_parse_account_name_from_connection_string():
    conn = "DefaultEndpointsProtocol=https;AccountName=myacct;AccountKey=abc;"
    assert parse_account_name_from_connection_string(conn) == "myacct"


def test_build_abfss_uri():
    uri = build_abfss_uri("export", "myacct", "deltalake/org/2026/table")
    assert (
        uri
        == "abfss://export@myacct.dfs.core.windows.net/deltalake/org/2026/table"
    )


def test_resolve_dynamics_adls_export_url_from_source():
    conn = "DefaultEndpointsProtocol=https;AccountName=myacct;AccountKey=abc;"
    url = resolve_dynamics_adls_export_url(
        {},
        container_name="export",
        connection_string=conn,
        base_folder="deltalake/org",
    )
    assert url == "abfss://export@myacct.dfs.core.windows.net/deltalake/org/"


def test_resolve_databricks_adls_settings_defaults_manual():
    settings = resolve_databricks_adls_settings({})
    assert settings["mode"] == "manual"
    assert settings["credential_name"] == "datasyncher_fo_export_cred"


def test_parse_abfss_uri():
    loc = parse_abfss_uri(
        "abfss://datasyncher-stage@test4datasyncher.dfs.core.windows.net/landing/"
    )
    assert loc.container_name == "datasyncher-stage"
    assert loc.account_name == "test4datasyncher"
    assert loc.blob_prefix == "landing"


def test_resolve_azure_spark_read_mode_defaults_to_staging_when_url_configured(monkeypatch):
    monkeypatch.setenv(
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
    )
    assert resolve_azure_spark_read_mode({}) == AZURE_SPARK_READ_MODE_STAGING


def test_resolve_platform_azure_staging_sp_from_env(monkeypatch):
    monkeypatch.setenv("DATABRICKS_AZURE_STAGING_SP_TENANT_ID", "tenant-1")
    monkeypatch.setenv("DATABRICKS_AZURE_STAGING_SP_CLIENT_ID", "app-1")
    monkeypatch.setenv("DATABRICKS_AZURE_STAGING_SP_CLIENT_SECRET", "secret-1")
    sp = resolve_platform_azure_staging_sp({})
    assert sp == {
        "directory_id": "tenant-1",
        "application_id": "app-1",
        "client_secret": "secret-1",
    }


def test_resolve_databricks_adls_staging_settings_auto_when_platform_sp(monkeypatch):
    monkeypatch.setenv("DATABRICKS_AZURE_STAGING_SP_TENANT_ID", "tenant-1")
    monkeypatch.setenv("DATABRICKS_AZURE_STAGING_SP_CLIENT_ID", "app-1")
    monkeypatch.setenv("DATABRICKS_AZURE_STAGING_SP_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv(
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
    )
    settings = resolve_databricks_adls_staging_settings({})
    assert settings["mode"] == "auto"
    assert settings["platform_sp_configured"] is True


def test_resolve_databricks_adls_staging_location_name_per_connection():
    name = resolve_databricks_adls_staging_location_name(
        31,
        {"adls_staging_external_location_name": "datasyncher_fo_staging"},
    )
    assert name == "datasyncher_fo_staging_31"


def test_resolve_azure_spark_load_strategy_defaults_bulk():
    assert resolve_azure_spark_load_strategy({}) == AZURE_SPARK_LOAD_STRATEGY_BULK


def test_resolve_azure_spark_load_strategy_explicit_per_folder():
    assert (
        resolve_azure_spark_load_strategy({"spark_azure_load_strategy": "per_folder"})
        == AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER
    )


def test_resolve_spark_bulk_job_timeout_defaults():
    from services.databricks.load_helpers import (
        DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS,
    )

    assert (
        resolve_spark_bulk_job_timeout_seconds({})
        == DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS
    )


def test_dynamics_adls_staging_table_landing_prefix():
    prefix = dynamics_adls_staging_table_landing_prefix(
        31,
        "generaljournalaccountentry",
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/landing",
    )
    assert prefix.endswith("/generaljournalaccountentry")
    assert "dynamics_fo/31" in prefix


def test_resolve_azure_spark_read_mode_explicit_direct(monkeypatch):
    monkeypatch.setenv(
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
    )
    assert (
        resolve_azure_spark_read_mode({"spark_azure_read_mode": "direct"})
        == AZURE_SPARK_READ_MODE_DIRECT
    )


def test_resolve_dynamics_adls_staging_url():
    url = resolve_dynamics_adls_staging_url(
        {"adls_staging_url": "abfss://datasyncher-stage@test4.dfs.core.windows.net/"},
        connection_id=31,
    )
    assert (
        url
        == "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/31/"
    )


def test_dynamics_adls_staging_paths():
    blob = dynamics_adls_staging_blob_path(
        31,
        "generaljournalaccountentry",
        "2025-01-07T19.59.09Z",
        "53.csv",
        staging_base_prefix="landing",
    )
    assert blob.endswith("/data/53.csv")
    prefix = dynamics_adls_staging_landing_prefix(
        31,
        "generaljournalaccountentry",
        "2025-01-07T19.59.09Z",
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/landing",
    )
    assert "generaljournalaccountentry/2025-01-07T19.59.09Z" in prefix


@patch("services.dynamics.source_common.land_azure_csv_to_adls_staging")
def test_land_table_date_dir_to_adls_staging(mock_land):
    mock_land.return_value = (
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/"
        "landing/dynamics_fo/31/MyTable/2026-02-12T12.59.09Z/data/53.csv"
    )
    mock_container = MagicMock()
    mock_blob = MagicMock()
    mock_blob.name = "deltalake/org/2026-02-12T12.59.09Z/MyTable/53.csv"
    mock_container.list_blobs.return_value = [mock_blob]

    landing_prefix, files = land_table_date_dir_to_adls_staging(
        mock_container,
        "deltalake/org",
        "2026-02-12T12.59.09Z",
        "MyTable",
        31,
        staging_abfss_url="abfss://datasyncher-stage@test4.dfs.core.windows.net/landing",
        staging_container_client=MagicMock(),
    )

    assert mock_land.call_count == 1
    assert len(files) == 1
    assert landing_prefix.endswith("/MyTable/2026-02-12T12.59.09Z")


def test_discover_table_date_dir_for_spark_direct():
    mock_container = MagicMock()
    mock_blob = MagicMock()
    mock_blob.name = "deltalake/org/2026-02-12T12.59.09Z/MyTable/53.csv"
    mock_container.list_blobs.return_value = [mock_blob]
    conn = "DefaultEndpointsProtocol=https;AccountName=myacct;AccountKey=abc;"

    landing_prefix, files = discover_table_date_dir_for_spark_direct(
        mock_container,
        "export",
        conn,
        "deltalake/org",
        "2026-02-12T12.59.09Z",
        "MyTable",
    )

    assert landing_prefix.endswith("/MyTable")
    assert "abfss://export@myacct.dfs.core.windows.net/" in landing_prefix
    assert len(files) == 1
    assert files[0]["is_delete"] is False
    assert files[0]["abfss_uri"].endswith("53.csv")


def test_dynamics_gcs_landing_paths():
    key = dynamics_gcs_landing_object_key(24, "dimensionfocusbalance", "2026-02-12T12.59.09Z", "53.csv")
    assert key == "dynamics_fo/24/dimensionfocusbalance/2026-02-12T12.59.09Z/data/53.csv"
    delete_key = dynamics_gcs_landing_object_key(
        24, "dimensionfocusbalance", "2026-02-12T12.59.09Z", "del.csv", is_delete=True
    )
    assert "/deletes/" in delete_key


def test_dynamics_gcs_landing_prefix_uses_bucket(monkeypatch):
    monkeypatch.setattr(
        "services.dynamics.source_common.BUCKET_NAME",
        "datasyncher-batch-bucket",
    )
    prefix = dynamics_gcs_landing_prefix(24, "dimensionfocusbalance", "2026-02-12T12.59.09Z")
    assert prefix.startswith("gs://datasyncher-batch-bucket/dynamics_fo/24/")


def test_write_landing_manifest_uploads_json(monkeypatch):
    monkeypatch.setattr("services.dynamics.source_common.BUCKET_NAME", "test-bucket")
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    monkeypatch.setattr("services.dynamics.source_common.storage_client", mock_client)

    files = [{"azure_path": "a.csv", "gcs_uri": "gs://test-bucket/a.csv", "is_delete": False}]
    manifest_uri, prefix = write_landing_manifest(24, "MyTable", "2026-02-12T12.59.09Z", files)

    assert manifest_uri.endswith("/manifest.json")
    assert prefix.endswith("/")
    mock_blob.upload_from_string.assert_called_once()
    payload = json.loads(mock_blob.upload_from_string.call_args[0][0])
    assert payload["table"] == "MyTable"
    assert payload["files"] == files


def test_manual_validation_checklist_documented():
    """
    Manual staging validation (ops):
      1. Dynamics → Databricks initial load routes to bulk Spark automatically.
      2. Dynamics incremental loads route to bulk Spark automatically (watermark folders only).
      3. Run migration for dimensionfocusbalance (connection_id with large history).
      4. Grep pod logs: DBX|spark_land_ok / DBX|spark_direct_discover, DBX|spark_job|run_success.
      5. Salesforce → Databricks always uses SQL warehouse.
    """
    assert True


def test_workspace_parent_path():
    assert (
        _workspace_parent_path("/Shared/datasyncher/dynamics_fo_gcs_merge")
        == "/Shared/datasyncher"
    )


@patch("services.databricks.spark_job_client._databricks_api_request")
def test_ensure_workspace_parent_dirs_calls_mkdirs(mock_api):
    parent = _ensure_workspace_parent_dirs(
        "adb-example.azuredatabricks.net",
        "token",
        "/Shared/datasyncher/dynamics_fo_gcs_merge",
    )
    assert parent == "/Shared/datasyncher"
    mock_api.assert_called_once_with(
        "adb-example.azuredatabricks.net",
        "token",
        "POST",
        "/api/2.0/workspace/mkdirs",
        {"path": "/Shared/datasyncher"},
    )


def test_job_settings_payload_serverless_omits_job_clusters():
    payload = _job_settings_payload(
        "/Shared/datasyncher/dynamics_fo_gcs_merge",
        "datasyncher_dynamics_fo_gcs_merge",
        compute_mode="serverless",
        landing_cloud=SPARK_LANDING_CLOUD_GCS,
    )
    assert "job_clusters" not in payload
    assert "job_cluster_key" not in payload["tasks"][0]
    assert payload["tasks"][0]["task_key"] == "dynamics_fo_gcs_merge"


def test_job_settings_payload_adls_task_key():
    payload = _job_settings_payload(
        "/Shared/datasyncher/dynamics_fo_adls_merge",
        "datasyncher_dynamics_fo_adls_merge",
        compute_mode="serverless",
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert payload["tasks"][0]["task_key"] == "dynamics_fo_adls_merge"


def test_job_settings_payload_classic_includes_job_clusters():
    payload = _job_settings_payload(
        "/Shared/datasyncher/dynamics_fo_gcs_merge",
        "datasyncher_dynamics_fo_gcs_merge",
        compute_mode="classic",
    )
    assert payload["job_clusters"]
    assert payload["tasks"][0]["job_cluster_key"]


def test_resolve_spark_compute_mode_defaults_serverless(monkeypatch):
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_COMPUTE_MODE", raising=False)
    monkeypatch.setenv("DYNAMICS_DBX_SPARK_USE_ALL_PURPOSE_CLUSTER", "0")
    assert _resolve_spark_compute_mode({}) == "serverless"


def test_resolve_spark_compute_mode_all_purpose_auto(monkeypatch):
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_COMPUTE_MODE", raising=False)
    monkeypatch.setenv("DYNAMICS_DBX_SPARK_USE_ALL_PURPOSE_CLUSTER", "1")
    dest = {
        "http_path": "/sql/protocolv1/o/7405605411607057/1028-060334-1b14ow4i",
    }
    assert _resolve_spark_compute_mode(dest) == "existing_cluster"


def test_dedicated_dynamics_spark_job_name_includes_connection_id():
    assert dedicated_dynamics_spark_job_name(42) == "datasyncher_dynamics_fo_conn_42"


def test_dedicated_dynamics_spark_job_name_for_destination():
    assert (
        dedicated_dynamics_spark_job_name_for_destination(99)
        == "datasyncher_dynamics_fo_dst_99"
    )


def test_destination_wants_dedicated_serverless_job():
    assert _destination_wants_dedicated_serverless_job(
        {"create_dedicated_dbx_serverless_job": "true"}
    )
    assert not _destination_wants_dedicated_serverless_job({})
    assert _destination_wants_dedicated_serverless_job({"dedicated_serverless_job": "yes"})


def test_resolve_spark_compute_mode_explicit_serverless_overrides_all_purpose(monkeypatch):
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_COMPUTE_MODE", raising=False)
    monkeypatch.setenv("DYNAMICS_DBX_SPARK_USE_ALL_PURPOSE_CLUSTER", "1")
    dest = {
        "http_path": "/sql/protocolv1/o/7405605411607057/1028-060334-1b14ow4i",
        "spark_compute_mode": "serverless",
    }
    assert _resolve_spark_compute_mode(dest) == "serverless"


def test_job_settings_payload_existing_cluster_uses_destination_cluster():
    payload = _job_settings_payload(
        "/Shared/datasyncher/dynamics_fo_adls_merge",
        "datasyncher_dynamics_fo_adls_merge",
        compute_mode="existing_cluster",
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
        existing_cluster_id="1028-060334-1b14ow4i",
    )
    assert "job_clusters" not in payload
    assert payload["tasks"][0]["existing_cluster_id"] == "1028-060334-1b14ow4i"
    assert "job_cluster_key" not in payload["tasks"][0]


def test_extract_notebook_result_value():
    run = {
        "tasks": [
            {"notebook_output": {"result": "12345"}},
        ]
    }
    assert extract_notebook_result_value(run) == 12345
    assert extract_notebook_result_value({}) == 0


def test_extract_row_count_from_output_prefers_notebook_result():
    output = {"notebook_output": {"result": "8015959"}}
    assert _extract_row_count_from_output(output) == 8015959


def test_extract_row_count_from_output_parses_merge_log_line():
    output = {
        "notebook_output": {"result": ""},
        "logs": "Loaded CSVs recursively from abfss://...\nMerged staging rows=8015959 into `test`.`sql`.`generaljournalaccountentry`",
    }
    assert _extract_row_count_from_output(output) == 8015959


def test_extract_row_count_from_output_no_files():
    output = {"notebook_output": {"result": "no_files"}}
    assert _extract_row_count_from_output(output) == 0


def test_resolve_spark_run_row_count_uses_task_run_output(monkeypatch):
    calls: list[int] = []

    def fake_api_request(hostname, token, method, path, payload=None):
        run_id = int(path.rsplit("=", 1)[-1])
        calls.append(run_id)
        if run_id == 1001:
            return {
                "notebook_output": {"result": ""},
                "logs": "Merged staging rows=42 into `c`.`s`.`t`",
            }
        return {"notebook_output": {"result": ""}, "logs": ""}

    monkeypatch.setattr(
        "services.databricks.spark_job_client._databricks_api_request",
        fake_api_request,
    )
    run = {"run_id": 999, "tasks": [{"run_id": 1001, "task_key": "dynamics_fo_adls_merge"}]}
    assert resolve_spark_run_row_count("dbc.example.net", "token", run) == 42
    assert calls == [1001]


def test_fetch_run_failure_details_uses_task_run_output(monkeypatch):
    calls: list[int] = []

    def fake_api_request(hostname, token, method, path, payload=None):
        assert method == "GET"
        run_id = int(path.rsplit("=", 1)[-1])
        calls.append(run_id)
        if run_id == 999:
            raise RuntimeError("parent run output unsupported")
        return {
            "error": "[PARSE_SYNTAX_ERROR] Syntax error at or near 'false'",
            "notebook_output": {"error": "", "result": ""},
        }

    monkeypatch.setattr(
        "services.databricks.spark_job_client._databricks_api_request",
        fake_api_request,
    )
    details = _fetch_run_failure_details(
        "dbc.example.net",
        "token",
        999,
        run={
            "tasks": [
                {"task_key": "dynamics_fo_adls_merge", "run_id": 1001},
            ]
        },
    )
    assert calls == [1001]
    assert "PARSE_SYNTAX_ERROR" in details
    assert "dynamics_fo_adls_merge" in details


def test_process_table_routes_to_spark_job_on_initial_load(monkeypatch):
    """Smoke test: initial load delegates to _process_table_spark_job."""
    from services.dynamics.as_source import to_databricks as mod

    sentinel = ("spark", 1, 0, 0, 0, {})
    monkeypatch.setattr(mod, "_process_table_spark_job", lambda **kwargs: sentinel)
    monkeypatch.setattr(
        mod,
        "destination_connection_function",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("SQL path should not run")),
    )

    result = mod.process_table(
        table_name="dimensionfocusbalance",
        connection_string="",
        container_name="c",
        base_folder="base",
        date_directories=["2026-02-12T12.59.09Z"],
        container_client=MagicMock(),
        connection_id=24,
        cmp_id=1,
        usr_id=1,
        session=1,
        updated_at="_FIVETRAN_SYNCED",
        deleted_at="_FIVETRAN_DELETED",
        destination_connection={"catalog": "test", "schema": "LANDING"},
        last_checked_date={},
        job_name="job",
        migration_session_id=12014,
        table_fields={},
        is_integration_checked=False,
        chunk_count=100000,
    )
    assert result == sentinel


def test_process_table_routes_to_spark_job_on_incremental_load(monkeypatch):
    """Subsequent syncs with a watermark still delegate to _process_table_spark_job."""
    from services.dynamics.as_source import to_databricks as mod

    sentinel = ("spark", 1, 0, 0, 0, {})
    monkeypatch.setattr(mod, "_process_table_spark_job", lambda **kwargs: sentinel)
    monkeypatch.setattr(
        mod,
        "destination_connection_function",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("SQL path should not run")),
    )

    result = mod.process_table(
        table_name="custtrans",
        connection_string="",
        container_name="c",
        base_folder="base",
        date_directories=["2026-02-12T12.59.09Z", "2026-02-13T12.59.09Z"],
        container_client=MagicMock(),
        connection_id=40,
        cmp_id=66,
        usr_id=1,
        session=1,
        updated_at="_FIVETRAN_SYNCED",
        deleted_at="_FIVETRAN_DELETED",
        destination_connection={"catalog": "test", "schema": "LANDING"},
        last_checked_date={"custtrans": "2026-02-12T12.59.09Z"},
        job_name="job",
        migration_session_id=13649,
        table_fields={},
        is_integration_checked=False,
        chunk_count=100000,
    )
    assert result == sentinel


@patch("services.dynamics.as_source.to_databricks.submit_and_wait")
@patch("services.dynamics.as_source.to_databricks.ensure_dynamics_spark_job")
@patch("services.dynamics.as_source.to_databricks.destination_connection_function")
@patch("services.dynamics.as_source.to_databricks.discover_table_date_dir_for_spark_direct")
def test_process_table_spark_job_uses_adls_direct_read(
    mock_discover,
    mock_dest_conn,
    mock_ensure_job,
    mock_submit,
    monkeypatch,
):
    monkeypatch.setenv("DYNAMICS_DBX_AZURE_SPARK_READ_MODE", "direct")
    from services.dynamics.as_source import to_databricks as mod

    mock_dest_conn.return_value = (MagicMock(), "LANDING")
    mock_ensure_job.return_value = {"job_id": 99, "landing_cloud": "azure"}
    mock_discover.return_value = (
        "abfss://export@acct.dfs.core.windows.net/base/date/table",
        [{"azure_path": "base/date/table/1.csv", "abfss_uri": "abfss://x", "is_delete": False}],
    )
    mock_submit.return_value = {"run_id": 1, "row_count": 1}

    monkeypatch.setattr(mod, "check_table_exists_databricks", lambda *a, **k: True)
    monkeypatch.setattr(mod, "update_table_status", lambda *a, **k: None)
    monkeypatch.setattr(mod, "directory_exists_in_date_directory", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_fetch_model_schema_for_table",
        lambda *a, **k: ({}, {}, ["ID"]),
    )
    monkeypatch.setattr(mod, "data_transfer_logs", lambda *a, **k: None)
    monkeypatch.setattr(
        "utils.load_checkpoint.finalize_migration_record",
        lambda **k: None,
    )

    detail_log = MagicMock()
    result = mod._process_table_spark_job(
        table_name="MyTable",
        connection_string="AccountName=acct;AccountKey=x;",
        container_name="export",
        base_folder="base",
        date_directories=["2026-02-12T12.59.09Z"],
        container_client=MagicMock(),
        connection_id=24,
        cmp_id=1,
        usr_id=1,
        session=1,
        updated_at="_FIVETRAN_SYNCED",
        deleted_at="_FIVETRAN_DELETED",
        destination_connection={
            "load_engine": "spark_job",
            "spark_landing_cloud": "azure",
            "server_hostname": "adb-1.azuredatabricks.net",
            "catalog": "test",
            "schema": "LANDING",
        },
        last_checked_date={},
        job_name="job",
        migration_session_id=12014,
        table_fields={},
        safety_interval_minutes=0,
        detail_log=detail_log,
    )

    mock_discover.assert_called_once()
    mock_submit.assert_called_once()
    notebook_params = mock_submit.call_args[0][2]
    assert notebook_params["landing_prefix"].startswith("abfss://")
    assert result is not None


@patch("services.dynamics.source_common.list_table_csv_blobs")
@patch("services.dynamics.source_common.land_azure_csv_to_adls_staging")
def test_land_table_bulk_to_adls_staging(mock_land, mock_list_blobs):
    mock_list_blobs.side_effect = [
        (None, ["d1/t/1.csv"]),
        (None, []),
    ]
    mock_land.return_value = "abfss://datasyncher-stage@test4.dfs.core.windows.net/x"

    prefix, files, folders_with_data, last_dir = land_table_bulk_to_adls_staging(
        MagicMock(),
        "base",
        ["2026-02-12T12.59.09Z", "2026-02-13T12.59.09Z"],
        "MyTable",
        31,
        staging_abfss_url="abfss://datasyncher-stage@test4.dfs.core.windows.net/landing",
        staging_container_client=MagicMock(),
    )

    assert mock_land.call_count == 1
    assert len(files) == 1
    assert folders_with_data == 1
    assert last_dir == "2026-02-13T12.59.09Z"
    assert "MyTable" in prefix


@patch("services.dynamics.source_common.list_table_csv_blobs")
@patch("services.dynamics.source_common.land_azure_csv_to_adls_staging")
def test_land_table_bulk_to_adls_staging_logs_batch_complete(mock_land, mock_list_blobs, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="services.dynamics.source_common")
    mock_list_blobs.side_effect = [(None, [f"d1/t/{i}.csv"]) for i in range(50)]
    mock_land.return_value = "abfss://datasyncher-stage@test4.dfs.core.windows.net/x"

    land_table_bulk_to_adls_staging(
        MagicMock(),
        "base",
        [f"2026-02-12T12.59.{i:02d}Z" for i in range(50)],
        "MyTable",
        31,
        staging_abfss_url="abfss://datasyncher-stage@test4.dfs.core.windows.net/landing",
        staging_container_client=MagicMock(),
        progress_every=50,
    )

    messages = [rec.message for rec in caplog.records]
    assert any("spark_staging_bulk_folder_done" in msg for msg in messages)
    assert any("spark_staging_bulk_batch_complete" in msg and "batch=1" in msg for msg in messages)
    assert any("spark_staging_bulk_done" in msg for msg in messages)


def test_resolve_dynamics_bulk_copy_workers_defaults_and_caps(monkeypatch):
    from services.dynamics.source_common import resolve_dynamics_bulk_copy_workers

    monkeypatch.delenv("DYNAMICS_DBX_BULK_COPY_WORKERS", raising=False)
    monkeypatch.delenv("CELERY_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("DYNAMICS_DBX_BULK_COPY_POD_THREAD_BUDGET", raising=False)
    assert resolve_dynamics_bulk_copy_workers() == 8
    assert resolve_dynamics_bulk_copy_workers(copy_workers=16) == 16
    assert resolve_dynamics_bulk_copy_workers(copy_workers=64) == 32
    monkeypatch.setenv("DYNAMICS_DBX_BULK_COPY_WORKERS", "12")
    assert resolve_dynamics_bulk_copy_workers() == 12


def test_resolve_dynamics_bulk_copy_workers_scales_by_active_tables(monkeypatch):
    from services.dynamics.source_common import resolve_dynamics_bulk_copy_workers

    monkeypatch.setenv("DYNAMICS_DBX_BULK_COPY_WORKERS", "8")
    monkeypatch.setenv("DYNAMICS_DBX_BULK_COPY_POD_THREAD_BUDGET", "16")
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "4")

    assert resolve_dynamics_bulk_copy_workers(active_parallel_tables=1) == 8
    assert resolve_dynamics_bulk_copy_workers(active_parallel_tables=2) == 8
    assert resolve_dynamics_bulk_copy_workers(active_parallel_tables=4) == 4
    assert resolve_dynamics_bulk_copy_workers(active_parallel_tables=8) == 2


def test_resolve_dynamics_bulk_copy_workers_falls_back_to_celery_concurrency(monkeypatch):
    from services.dynamics.source_common import resolve_dynamics_bulk_copy_workers

    monkeypatch.setenv("DYNAMICS_DBX_BULK_COPY_WORKERS", "8")
    monkeypatch.setenv("DYNAMICS_DBX_BULK_COPY_POD_THREAD_BUDGET", "16")
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "4")
    assert resolve_dynamics_bulk_copy_workers() == 4

    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "1")
    assert resolve_dynamics_bulk_copy_workers() == 8


@patch("services.dynamics.source_common.list_table_csv_blobs")
@patch("services.dynamics.source_common.land_azure_csv_to_adls_staging")
def test_land_table_bulk_to_adls_staging_uses_parallel_copy(mock_land, mock_list_blobs):
    import time

    mock_list_blobs.side_effect = [(None, [f"d1/t/{i}.csv"]) for i in range(8)]

    def slow_copy(*args, **kwargs):
        time.sleep(0.05)
        return "abfss://datasyncher-stage@test4.dfs.core.windows.net/x"

    mock_land.side_effect = slow_copy
    started = time.time()
    _, files, folders_with_data, _ = land_table_bulk_to_adls_staging(
        MagicMock(),
        "base",
        [f"2026-02-12T12.59.{i:02d}Z" for i in range(8)],
        "MyTable",
        31,
        staging_abfss_url="abfss://datasyncher-stage@test4.dfs.core.windows.net/landing",
        staging_container_client=MagicMock(),
        copy_workers=4,
    )
    elapsed = time.time() - started
    assert len(files) == 8
    assert folders_with_data == 8
    assert mock_land.call_count == 8
    assert elapsed < 0.25


@patch("services.dynamics.as_source.to_databricks.submit_and_wait")
@patch("services.dynamics.as_source.to_databricks.ensure_dynamics_spark_job")
@patch("services.dynamics.as_source.to_databricks.destination_connection_function")
@patch("services.dynamics.as_source.to_databricks.land_table_bulk_to_adls_staging")
@patch("services.dynamics.as_source.to_databricks.verify_adls_staging_has_csvs")
@patch("services.dynamics.as_source.to_databricks.verify_databricks_adls_prefix_readable")
@patch("services.dynamics.as_source.to_databricks.count_adls_staging_csv_blobs")
@patch("services.dynamics.as_source.to_databricks.cleanup_adls_staging_after_spark_merge")
def test_process_table_spark_job_uses_adls_bulk_staging_copy(
    mock_cleanup,
    mock_count,
    mock_uc_list,
    mock_verify,
    mock_land,
    mock_dest_conn,
    mock_ensure_job,
    mock_submit,
    monkeypatch,
):
    from services.dynamics.as_source import to_databricks as mod

    monkeypatch.setenv(
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
    )
    mock_count.return_value = (0, "dynamics_fo/24/MyTable/")
    mock_verify.return_value = 1
    mock_uc_list.return_value = {"ok": True, "entry_count": 1}
    mock_cleanup.return_value = 1
    mock_dest_conn.return_value = (MagicMock(), "LANDING")
    mock_ensure_job.return_value = {"job_id": 99, "landing_cloud": "azure"}
    mock_land.return_value = (
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/24/MyTable",
        [
            {
                "azure_path": "base/date/table/1.csv",
                "abfss_uri": "abfss://x",
                "is_delete": False,
                "date_dir": "2026-02-12T12.59.09Z",
            }
        ],
        1,
        "2026-02-12T12.59.09Z",
    )
    mock_submit.return_value = {"run_id": 1, "row_count": 100}

    monkeypatch.setattr(mod, "check_table_exists_databricks", lambda *a, **k: True)
    monkeypatch.setattr(mod, "update_table_status", lambda *a, **k: None)
    monkeypatch.setattr(mod, "directory_exists_in_date_directory", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_resolve_model_schema_for_table",
        lambda *a, **k: ("2026-02-12T12.59.09Z", {}, {}, ["ID"]),
    )
    monkeypatch.setattr(mod, "data_transfer_logs", lambda *a, **k: None)
    monkeypatch.setattr(
        "utils.load_checkpoint.finalize_migration_record",
        lambda **k: None,
    )

    detail_log = MagicMock()
    mod._process_table_spark_job(
        table_name="MyTable",
        connection_string="AccountName=acct;AccountKey=x;",
        container_name="export",
        base_folder="base",
        date_directories=["2026-02-12T12.59.09Z"],
        container_client=MagicMock(),
        connection_id=24,
        cmp_id=1,
        usr_id=1,
        session=1,
        updated_at="_FIVETRAN_SYNCED",
        deleted_at="_FIVETRAN_DELETED",
        destination_connection={
            "load_engine": "spark_job",
            "spark_landing_cloud": "azure",
            "server_hostname": "adb-1.azuredatabricks.net",
            "catalog": "test",
            "schema": "LANDING",
        },
        last_checked_date={},
        job_name="job",
        migration_session_id=12014,
        table_fields={},
        safety_interval_minutes=0,
        detail_log=detail_log,
    )

    mock_land.assert_called_once()
    mock_submit.assert_called_once()
    notebook_params = mock_submit.call_args[0][2]
    assert "datasyncher-stage" in notebook_params["landing_prefix"]
    assert notebook_params["schema_columns"] == "ID"
    assert notebook_params["is_initial_load"] == "true"
    from services.databricks.load_helpers import (
        DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS,
    )

    assert (
        mock_submit.call_args[1]["timeout_seconds"]
        >= DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS
    )


@patch("services.dynamics.as_source.to_databricks.submit_and_wait")
@patch("services.dynamics.as_source.to_databricks.ensure_dynamics_spark_job")
@patch("services.dynamics.as_source.to_databricks.destination_connection_function")
@patch("services.dynamics.as_source.to_databricks.land_table_date_dir_to_adls_staging")
def test_process_table_spark_job_uses_adls_per_folder_staging_copy(
    mock_land,
    mock_dest_conn,
    mock_ensure_job,
    mock_submit,
    monkeypatch,
):
    from services.dynamics.as_source import to_databricks as mod

    monkeypatch.setenv(
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
    )
    monkeypatch.setenv("DYNAMICS_DBX_AZURE_SPARK_LOAD_STRATEGY", "per_folder")
    mock_dest_conn.return_value = (MagicMock(), "LANDING")
    mock_ensure_job.return_value = {"job_id": 99, "landing_cloud": "azure"}
    mock_land.return_value = (
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/24/MyTable/2026-02-12T12.59.09Z",
        [{"azure_path": "base/date/table/1.csv", "abfss_uri": "abfss://x", "is_delete": False}],
    )
    mock_submit.return_value = {"run_id": 1, "row_count": 1}

    monkeypatch.setattr(mod, "check_table_exists_databricks", lambda *a, **k: True)
    monkeypatch.setattr(mod, "update_table_status", lambda *a, **k: None)
    monkeypatch.setattr(mod, "directory_exists_in_date_directory", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_fetch_model_schema_for_table",
        lambda *a, **k: ({}, {}, ["ID"]),
    )
    monkeypatch.setattr(mod, "data_transfer_logs", lambda *a, **k: None)
    monkeypatch.setattr(
        "utils.load_checkpoint.finalize_migration_record",
        lambda **k: None,
    )

    detail_log = MagicMock()
    mod._process_table_spark_job(
        table_name="MyTable",
        connection_string="AccountName=acct;AccountKey=x;",
        container_name="export",
        base_folder="base",
        date_directories=["2026-02-12T12.59.09Z"],
        container_client=MagicMock(),
        connection_id=24,
        cmp_id=1,
        usr_id=1,
        session=1,
        updated_at="_FIVETRAN_SYNCED",
        deleted_at="_FIVETRAN_DELETED",
        destination_connection={
            "load_engine": "spark_job",
            "spark_landing_cloud": "azure",
            "server_hostname": "adb-1.azuredatabricks.net",
            "catalog": "test",
            "schema": "LANDING",
        },
        last_checked_date={},
        job_name="job",
        migration_session_id=12014,
        table_fields={},
        safety_interval_minutes=0,
        detail_log=detail_log,
    )

    mock_land.assert_called_once()
    mock_submit.assert_called_once()
    notebook_params = mock_submit.call_args[0][2]
    # Per-folder path never uses base-table COUNT mode.
    assert notebook_params["is_initial_load"] == "false"


@patch("services.dynamics.source_common.storage_client")
def test_land_azure_csv_to_gcs_prefix_streams(mock_storage_client):
    from services.dynamics.source_common import land_azure_csv_to_gcs_prefix

    mock_container = MagicMock()
    mock_blob_client = MagicMock()
    mock_container.get_blob_client.return_value = mock_blob_client
    mock_downloader = MagicMock()
    mock_downloader.chunks.return_value = [b"col1,col2\n", b"1,2\n"]
    mock_blob_client.download_blob.return_value = mock_downloader

    mock_gcs_writer = MagicMock()
    mock_gcs_blob = MagicMock()
    mock_gcs_blob.open.return_value.__enter__.return_value = mock_gcs_writer
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_gcs_blob
    mock_storage_client.bucket.return_value = mock_bucket

    uri = land_azure_csv_to_gcs_prefix(
        mock_container,
        "container/path/file.csv",
        "dynamics_fo/1/table/date/data/file.csv",
        bucket_name="test-bucket",
    )
    assert uri == "gs://test-bucket/dynamics_fo/1/table/date/data/file.csv"
    mock_gcs_blob.open.assert_called_once()
    assert mock_gcs_writer.write.call_count == 2


def test_dynamics_adls_staging_blob_prefix_table_and_date():
    assert dynamics_adls_staging_blob_prefix(31, "MyTable") == "dynamics_fo/31/MyTable/"
    assert (
        dynamics_adls_staging_blob_prefix(
            31,
            "MyTable",
            date_dir="2025-01-07T19.59.09Z",
            staging_base_prefix="staging-root",
        )
        == "staging-root/dynamics_fo/31/MyTable/2025-01-07T19.59.09Z/"
    )


def test_resolve_adls_staging_cleanup_enabled(monkeypatch):
    monkeypatch.delenv("DYNAMICS_DBX_ADLS_STAGING_CLEANUP_AFTER_MERGE", raising=False)
    assert resolve_adls_staging_cleanup_enabled() is True
    monkeypatch.setenv("DYNAMICS_DBX_ADLS_STAGING_CLEANUP_AFTER_MERGE", "0")
    assert resolve_adls_staging_cleanup_enabled() is False


def test_resolve_bulk_staging_reuse_enabled(monkeypatch):
    monkeypatch.delenv("DYNAMICS_DBX_BULK_REUSE_STAGING", raising=False)
    assert resolve_bulk_staging_reuse_enabled() is False
    monkeypatch.setenv("DYNAMICS_DBX_BULK_REUSE_STAGING", "1")
    assert resolve_bulk_staging_reuse_enabled() is True


def test_cleanup_adls_staging_after_spark_merge_deletes_table_prefix(monkeypatch):
    monkeypatch.setenv("DYNAMICS_DBX_ADLS_STAGING_CLEANUP_AFTER_MERGE", "1")
    staging_client = MagicMock()
    blob_a = MagicMock()
    blob_a.name = "dynamics_fo/31/tbl/2025-01-07T19.59.09Z/data/1.csv"
    blob_b = MagicMock()
    blob_b.name = "dynamics_fo/31/tbl/2025-01-07T19.59.09Z/deletes/2.csv"
    staging_client.list_blobs.return_value = [blob_a, blob_b]

    deleted = cleanup_adls_staging_after_spark_merge(
        31,
        "tbl",
        staging_abfss_url="abfss://datasyncher-stage@acct.dfs.core.windows.net/",
        staging_container_client=staging_client,
    )

    assert deleted == 2
    staging_client.list_blobs.assert_called_once_with(
        name_starts_with="dynamics_fo/31/tbl/"
    )
    staging_client.delete_blobs.assert_called_once_with(
        blob_a.name,
        blob_b.name,
        delete_snapshots="include",
    )


def test_cleanup_adls_staging_after_spark_merge_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("DYNAMICS_DBX_ADLS_STAGING_CLEANUP_AFTER_MERGE", "off")
    staging_client = MagicMock()
    deleted = cleanup_adls_staging_after_spark_merge(
        31,
        "tbl",
        staging_abfss_url="abfss://datasyncher-stage@acct.dfs.core.windows.net/",
        staging_container_client=staging_client,
    )
    assert deleted == 0
    staging_client.list_blobs.assert_not_called()


def test_count_and_verify_adls_staging_has_csvs(monkeypatch):
    monkeypatch.setattr(
        source_common,
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@acct.dfs.core.windows.net/",
    )
    staging_client = MagicMock()
    csv_a = MagicMock()
    csv_a.name = "dynamics_fo/31/tbl/2025-01-01/data/1.csv"
    other = MagicMock()
    other.name = "dynamics_fo/31/tbl/2025-01-01/data/readme.txt"
    staging_client.list_blobs.return_value = [csv_a, other]

    count, prefix = count_adls_staging_csv_blobs(
        31,
        "tbl",
        staging_container_client=staging_client,
    )
    assert count == 1
    assert prefix == "dynamics_fo/31/tbl/"

    assert (
        verify_adls_staging_has_csvs(
            31,
            "tbl",
            expected_count=1,
            staging_container_client=staging_client,
        )
        == 1
    )

    with pytest.raises(RuntimeError, match="staging lists 1 CSV"):
        verify_adls_staging_has_csvs(
            31,
            "tbl",
            expected_count=5,
            staging_container_client=staging_client,
        )


def test_verify_adls_staging_has_csvs_blocks_empty(monkeypatch):
    monkeypatch.setattr(
        source_common,
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@acct.dfs.core.windows.net/",
    )
    staging_client = MagicMock()
    staging_client.list_blobs.return_value = []
    with pytest.raises(RuntimeError, match="preflight failed"):
        verify_adls_staging_has_csvs(
            31,
            "tbl",
            expected_count=3,
            staging_container_client=staging_client,
        )


def test_verify_databricks_adls_prefix_readable_ok():
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    cursor = MagicMock()
    cursor.fetchmany.return_value = [("a.csv",), ("b.csv",)]
    conn.execute.return_value = cursor

    result = verify_databricks_adls_prefix_readable(
        engine,
        "abfss://datasyncher-stage@acct.dfs.core.windows.net/dynamics_fo/31/tbl",
        require_entries=True,
    )
    assert result["ok"] is True
    assert result["entry_count"] == 2
    conn.execute.assert_called_once()
    assert "LIST" in str(conn.execute.call_args[0][0])


def test_verify_databricks_adls_prefix_readable_non_iterable_result():
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    class _NonIterableResult:
        pass

    conn.execute.return_value = _NonIterableResult()

    result = verify_databricks_adls_prefix_readable(
        engine,
        "abfss://datasyncher-stage@acct.dfs.core.windows.net/dynamics_fo/31/tbl",
        require_entries=True,
    )
    assert result["ok"] is True
    assert result["entry_count"] == -1


def test_verify_databricks_adls_prefix_readable_permission_error():
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.side_effect = RuntimeError(
        "User does not have permission on EXTERNAL LOCATION"
    )
    result = verify_databricks_adls_prefix_readable(
        engine,
        "abfss://datasyncher-stage@acct.dfs.core.windows.net/dynamics_fo/31/",
    )
    assert result["ok"] is False
    assert "permission" in (result["error"] or "").lower()


def test_land_table_bulk_parallel_scan_lists_each_date_dir(monkeypatch):
    monkeypatch.setattr(
        source_common,
        "DATASYNCHER_AZURE_STAGING_URL",
        "abfss://datasyncher-stage@acct.dfs.core.windows.net/",
    )
    monkeypatch.setenv("DYNAMICS_DBX_BULK_SCAN_WORKERS", "4")
    monkeypatch.setattr(
        source_common,
        "AZURE_STAGING_CONNECTION_STRING",
        "AccountName=acct;AccountKey=key",
    )

    source_client = MagicMock()
    staging_client = MagicMock()

    def _fake_list(_container, _base, date_dir, _table):
        if date_dir.endswith("empty"):
            return f"prefix/{date_dir}/", []
        return f"prefix/{date_dir}/", [f"prefix/{date_dir}/tbl/1.csv"]

    monkeypatch.setattr(source_common, "list_table_csv_blobs", _fake_list)
    monkeypatch.setattr(
        source_common,
        "get_azure_staging_container_client",
        lambda **kwargs: staging_client,
    )
    monkeypatch.setattr(
        source_common,
        "land_azure_csv_to_adls_staging",
        lambda *a, **k: "abfss://x/y.csv",
    )

    landing, landed, folders_with_data, _last = land_table_bulk_to_adls_staging(
        source_client,
        "base",
        ["2025-01-01Tempty", "2025-01-02Tdata", "2025-01-03Tdata"],
        "tbl",
        31,
        staging_container_client=staging_client,
        copy_workers=2,
    )
    assert folders_with_data == 2
    assert len(landed) == 2
    assert "dynamics_fo/31/tbl" in landing


def test_add_underscore_camelcase_sink_modified_on():
    """Worker and Spark notebooks must agree: SinkModifiedOn → SINK_MODIFIED_ON."""
    assert source_common.add_underscore("SinkModifiedOn") == "SINK_MODIFIED_ON"
    assert source_common.add_underscore("ISDELETE") == "ISDELETE"


def test_notebook_to_snake_upper_camelcase_parity():
    """Mirror dynamics_fo_*_merge._to_snake_upper without importing the notebook."""
    import re

    def _to_snake_upper(name: str) -> str:
        s = str(name or "")
        s = re.sub(r"(?<=[a-zA-Z])(?=[0-9])", "_", s)
        s = re.sub(r"(?<=[0-9])(?=[a-zA-Z])", "_", s)
        s = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", s)
        s = re.sub(r"\W+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s.upper()

    def _normalize_col(name: str) -> str:
        return re.sub(r"\W+", "", str(name)).lower().replace("_", "")

    def _resolve_named_column(cols, *candidates):
        upper_map = {str(c).upper(): c for c in cols}
        compact_map = {_normalize_col(c): c for c in cols}
        for candidate in candidates:
            key = str(candidate or "").strip()
            if not key:
                continue
            if key.upper() in upper_map:
                return upper_map[key.upper()]
            compact = _normalize_col(key)
            if compact in compact_map:
                return compact_map[compact]
        return None

    assert _to_snake_upper("SinkModifiedOn") == "SINK_MODIFIED_ON"
    assert _to_snake_upper("SinkModifiedOn") == source_common.add_underscore("SinkModifiedOn")
    assert _resolve_named_column(
        ["ID", "SINKMODIFIEDON", "IS_DELETE"], "SINK_MODIFIED_ON"
    ) == "SINKMODIFIEDON"


def test_dynamics_locale_timestamp_format_patterns():
    """Formats used by notebook _coerce_timestamp_column / try_to_timestamp ORDER BY."""
    from datetime import datetime

    sample = "1/7/2025 8:32:16 PM"
    parsed = datetime.strptime(sample, "%m/%d/%Y %I:%M:%S %p")
    assert parsed.year == 2025
    assert parsed.month == 1
    assert parsed.day == 7
    assert parsed.hour == 20


def test_resolve_spark_staging_load_strategy_gcs_and_s3_defaults_bulk():
    from services.databricks.load_helpers import (
        SPARK_LANDING_CLOUD_S3,
        resolve_spark_staging_load_strategy,
    )

    assert (
        resolve_spark_staging_load_strategy(SPARK_LANDING_CLOUD_GCS, {})
        == AZURE_SPARK_LOAD_STRATEGY_BULK
    )
    assert (
        resolve_spark_staging_load_strategy(SPARK_LANDING_CLOUD_S3, {})
        == AZURE_SPARK_LOAD_STRATEGY_BULK
    )
    assert (
        resolve_spark_staging_load_strategy(
            SPARK_LANDING_CLOUD_GCS,
            {"spark_gcs_load_strategy": "per_folder"},
        )
        == AZURE_SPARK_LOAD_STRATEGY_PER_FOLDER
    )


def test_resolve_spark_staging_read_mode_gcs_when_bucket_configured(monkeypatch):
    from services.databricks.load_helpers import resolve_spark_staging_read_mode

    monkeypatch.setenv("GCS_BUCKET_NAME", "datasyncher-batch")
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_STAGING_READ_MODE", raising=False)
    assert (
        resolve_spark_staging_read_mode(SPARK_LANDING_CLOUD_GCS, {})
        == AZURE_SPARK_READ_MODE_STAGING
    )


def test_resolve_spark_staging_read_mode_s3_when_bucket_configured(monkeypatch):
    from services.databricks.load_helpers import (
        SPARK_LANDING_CLOUD_S3,
        resolve_spark_staging_read_mode,
    )

    monkeypatch.setenv("S3_BUCKET_NAME", "datasyncher-batch-s3")
    monkeypatch.delenv("S3_BUCKET_URL", raising=False)
    assert (
        resolve_spark_staging_read_mode(SPARK_LANDING_CLOUD_S3, {})
        == AZURE_SPARK_READ_MODE_STAGING
    )


def test_dynamics_gcs_s3_staging_table_landing_prefixes(monkeypatch):
    monkeypatch.setattr(source_common, "BUCKET_NAME", "gcs-bucket")
    monkeypatch.setenv("S3_BUCKET_NAME", "s3-bucket")
    assert (
        source_common.dynamics_gcs_staging_table_landing_prefix(31, "MyTable")
        == "gs://gcs-bucket/dynamics_fo/31/MyTable"
    )
    assert (
        source_common.dynamics_s3_staging_table_landing_prefix(31, "MyTable")
        == "s3://s3-bucket/dynamics_fo/31/MyTable"
    )


@patch("services.dynamics.source_common.list_table_csv_blobs")
@patch("services.dynamics.source_common.land_azure_csv_to_gcs_prefix")
def test_land_table_bulk_to_gcs_staging(mock_land, mock_list_blobs, monkeypatch):
    monkeypatch.setattr(source_common, "BUCKET_NAME", "gcs-bucket")
    mock_list_blobs.side_effect = [
        (None, ["d1/t/1.csv"]),
        (None, []),
    ]
    mock_land.return_value = "gs://gcs-bucket/dynamics_fo/31/MyTable/d1/data/1.csv"

    prefix, files, folders_with_data, last_dir = source_common.land_table_bulk_to_gcs_staging(
        MagicMock(),
        "base",
        ["2026-02-12T12.59.09Z", "2026-02-13T12.59.09Z"],
        "MyTable",
        31,
    )

    assert mock_land.call_count == 1
    assert len(files) == 1
    assert folders_with_data == 1
    assert last_dir == "2026-02-13T12.59.09Z"
    assert prefix == "gs://gcs-bucket/dynamics_fo/31/MyTable"


@patch("services.dynamics.source_common.list_table_csv_blobs")
@patch("services.dynamics.source_common.land_azure_csv_to_s3_prefix")
def test_land_table_bulk_to_s3_staging(mock_land, mock_list_blobs, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "s3-bucket")
    mock_list_blobs.side_effect = [
        (None, ["d1/t/1.csv"]),
        (None, []),
    ]
    mock_land.return_value = "s3://s3-bucket/dynamics_fo/31/MyTable/d1/data/1.csv"

    prefix, files, folders_with_data, last_dir = source_common.land_table_bulk_to_s3_staging(
        MagicMock(),
        "base",
        ["2026-02-12T12.59.09Z", "2026-02-13T12.59.09Z"],
        "MyTable",
        31,
    )

    assert mock_land.call_count == 1
    assert len(files) == 1
    assert folders_with_data == 1
    assert prefix == "s3://s3-bucket/dynamics_fo/31/MyTable"


@patch("services.dynamics.as_source.to_databricks.submit_and_wait")
@patch("services.dynamics.as_source.to_databricks.ensure_dynamics_spark_job")
@patch("services.dynamics.as_source.to_databricks.destination_connection_function")
@patch("services.dynamics.as_source.to_databricks.land_table_bulk_to_gcs_staging")
@patch("services.dynamics.as_source.to_databricks.verify_gcs_staging_has_csvs")
@patch("services.dynamics.as_source.to_databricks.verify_databricks_gcs_prefix_readable")
@patch("services.dynamics.as_source.to_databricks.count_gcs_staging_csv_blobs")
@patch("services.dynamics.as_source.to_databricks.cleanup_gcs_staging_after_spark_merge")
def test_process_table_spark_job_uses_gcs_bulk_staging_copy(
    mock_cleanup,
    mock_count,
    mock_uc_list,
    mock_verify,
    mock_land,
    mock_dest_conn,
    mock_ensure_job,
    mock_submit,
    monkeypatch,
):
    from services.dynamics.as_source import to_databricks as mod

    monkeypatch.setenv("GCS_BUCKET_NAME", "gcs-bucket")
    mock_count.return_value = (0, "dynamics_fo/24/MyTable/")
    mock_verify.return_value = 1
    mock_uc_list.return_value = {"ok": True, "entry_count": 1}
    mock_cleanup.return_value = 1
    mock_dest_conn.return_value = (MagicMock(), "LANDING")
    mock_ensure_job.return_value = {"job_id": 99, "landing_cloud": "gcs"}
    mock_land.return_value = (
        "gs://gcs-bucket/dynamics_fo/24/MyTable",
        [
            {
                "azure_path": "base/date/table/1.csv",
                "gcs_uri": "gs://x",
                "is_delete": False,
                "date_dir": "2026-02-12T12.59.09Z",
            }
        ],
        1,
        "2026-02-12T12.59.09Z",
    )
    mock_submit.return_value = {"run_id": 1, "row_count": 100}

    monkeypatch.setattr(mod, "check_table_exists_databricks", lambda *a, **k: True)
    monkeypatch.setattr(mod, "update_table_status", lambda *a, **k: None)
    monkeypatch.setattr(
        mod,
        "_resolve_model_schema_for_table",
        lambda *a, **k: ("2026-02-12T12.59.09Z", {}, {}, ["ID", "AMOUNT"]),
    )
    monkeypatch.setattr(mod, "data_transfer_logs", lambda *a, **k: None)
    monkeypatch.setattr(
        "utils.load_checkpoint.finalize_migration_record",
        lambda **k: None,
    )

    detail_log = MagicMock()
    mod._process_table_spark_job(
        table_name="MyTable",
        connection_string="AccountName=acct;AccountKey=x;",
        container_name="export",
        base_folder="base",
        date_directories=["2026-02-12T12.59.09Z"],
        container_client=MagicMock(),
        connection_id=24,
        cmp_id=1,
        usr_id=1,
        session=1,
        updated_at="_FIVETRAN_SYNCED",
        deleted_at="_FIVETRAN_DELETED",
        destination_connection={
            "load_engine": "spark_job",
            "spark_landing_cloud": "gcs",
            "server_hostname": "123.gcp.databricks.com",
            "catalog": "test",
            "schema": "LANDING",
        },
        last_checked_date={},
        job_name="job",
        migration_session_id=12014,
        table_fields={},
        safety_interval_minutes=0,
        detail_log=detail_log,
    )

    mock_land.assert_called_once()
    mock_submit.assert_called_once()
    notebook_params = mock_submit.call_args[0][2]
    assert notebook_params["landing_prefix"] == "gs://gcs-bucket/dynamics_fo/24/MyTable"
    assert notebook_params["schema_columns"] == "ID,AMOUNT"
    assert notebook_params["is_initial_load"] == "true"
    # Pre-copy cleanup (reuse disabled) + post-merge cleanup.
    assert mock_cleanup.call_count == 2
    mock_cleanup.assert_any_call(24, "MyTable")


def test_spark_job_client_s3_artifact_registered():
    from services.databricks import spark_job_client as sjc
    from services.databricks.load_helpers import SPARK_LANDING_CLOUD_S3

    assert SPARK_LANDING_CLOUD_S3 in sjc._SPARK_ARTIFACTS
    assert sjc._SPARK_ARTIFACTS[SPARK_LANDING_CLOUD_S3].name == "dynamics_fo_s3_merge.py"
    assert sjc._default_job_name(SPARK_LANDING_CLOUD_S3) == sjc.DEFAULT_S3_JOB_NAME
    payload = sjc._job_settings_payload(
        "/Shared/datasyncher/dynamics_fo_s3_merge",
        "job",
        landing_cloud=SPARK_LANDING_CLOUD_S3,
    )
    assert payload["tasks"][0]["task_key"] == "dynamics_fo_s3_merge"


def test_resolve_databricks_s3_settings_from_env(monkeypatch):
    from services.databricks.load_helpers import resolve_databricks_s3_settings

    monkeypatch.setenv("S3_BUCKET_NAME", "my-bucket")
    monkeypatch.delenv("S3_BUCKET_URL", raising=False)
    settings = resolve_databricks_s3_settings({})
    assert settings["bucket_name"] == "my-bucket"
    assert settings["s3_bucket_url"] == "s3://my-bucket/"


def test_verify_databricks_gcs_and_s3_prefix_readable_scheme_guard():
    from services.databricks.load_helpers import (
        verify_databricks_gcs_prefix_readable,
        verify_databricks_s3_prefix_readable,
    )

    gcs = verify_databricks_gcs_prefix_readable(MagicMock(), "abfss://x")
    assert gcs["skipped_reason"] == "not_gs_prefix"
    s3 = verify_databricks_s3_prefix_readable(MagicMock(), "gs://x")
    assert s3["skipped_reason"] == "not_s3_prefix"
