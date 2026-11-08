"""Unit tests for Dynamics → Databricks load helpers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from services.databricks import load_helpers as helpers


@pytest.fixture(autouse=True)
def _clear_watermarks():
    helpers._WATERMARK_BY_TABLE.clear()
    yield
    helpers._WATERMARK_BY_TABLE.clear()


def test_get_databricks_type_known_and_unknown():
    assert helpers.get_databricks_type("guid") == "STRING"
    assert helpers.get_databricks_type("int64") == "BIGINT"
    assert helpers.get_databricks_type("dateTime") == "TIMESTAMP"
    assert helpers.get_databricks_type("DateTime") == "TIMESTAMP"
    assert helpers.get_databricks_type("DATETIME") == "TIMESTAMP"
    assert helpers.get_databricks_type("date") == "DATE"
    assert helpers.get_databricks_type("int32") == "INT"
    assert helpers.get_databricks_type("double") == "DOUBLE"
    # Unknown CDM alias → STRING (Snowflake-style safe default), does not raise
    assert helpers.get_databricks_type("unknown_xyz") == "STRING"
    assert helpers.get_snowflake_type("decimal") == "DECIMAL(38,6)"


def test_normalize_dynamics_raw_types_to_databricks():
    assert helpers._normalize_to_databricks_type("dateTime") == "TIMESTAMP"
    assert helpers._normalize_to_databricks_type("guid") == "STRING"
    assert helpers._normalize_to_databricks_type("TIMESTAMP_TZ(9)") == "TIMESTAMP"
    assert helpers._normalize_to_databricks_type("DATE") == "DATE"


def test_normalize_databricks_server_hostname():
    assert (
        helpers.normalize_databricks_server_hostname(
            "https://adb-123.4.azuredatabricks.net/"
        )
        == "adb-123.4.azuredatabricks.net"
    )
    assert (
        helpers.normalize_databricks_server_hostname("dbc-abc.gcp.databricks.com")
        == "dbc-abc.gcp.databricks.com"
    )
    assert helpers.normalize_databricks_server_hostname("") == ""


def test_camelcase_attr_keys_map_datetime_to_timestamp_ddl():
    """Dynamics model.json uses CamelCase; Delta columns are SNAKE_CASE."""
    attrs = {
        "Id": "STRING",
        "CreatedDateTime": "TIMESTAMP",
        "SinkModifiedOn": "TIMESTAMP",
        "IsDelete": "BOOLEAN",
    }
    ddl = helpers._ddl_columns_from_attributes(
        attrs,
        ["ID", "CREATED_DATE_TIME", "SINK_MODIFIED_ON", "IS_DELETE"],
    )
    joined = " | ".join(ddl)
    assert "`CREATED_DATE_TIME` TIMESTAMP" in joined
    assert "`SINK_MODIFIED_ON` TIMESTAMP" in joined
    assert "`IS_DELETE` BOOLEAN" in joined
    assert "`ID` STRING" in joined


def test_dtype_for_column_camelcase_and_string_override():
    attrs = {"CreatedDateTime": "TIMESTAMP", "Name": "STRING"}
    assert (
        helpers._dtype_for_column(attrs, "CREATED_DATE_TIME") == "TIMESTAMP"
    )
    # Stale STRING column + attribute TIMESTAMP → prefer TIMESTAMP
    assert (
        helpers._dtype_for_column(
            attrs,
            "CREATED_DATE_TIME",
            existing_column_types={"CREATED_DATE_TIME": "STRING"},
        )
        == "TIMESTAMP"
    )


def test_remember_and_apply_watermark():
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "SINK_MODIFIED_ON": [
                "2026-01-10T12:00:00Z",
                "2026-01-15T18:30:00Z",
            ],
        }
    )
    helpers.remember_chunk_watermark("ledgerjournaltable", df)
    peeked = helpers.peek_watermark("ledgerjournaltable")
    assert peeked == "2026-01-15T18.30.00Z"

    out = helpers.apply_data_max_watermark({"ledgerjournaltable": "old"}, "ledgerjournaltable")
    assert out["ledgerjournaltable"] == "2026-01-15T18.30.00Z"


def test_remember_watermark_keeps_max_across_chunks():
    helpers.remember_chunk_watermark(
        "t1",
        pd.DataFrame({"SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z"]}),
    )
    helpers.remember_chunk_watermark(
        "t1",
        pd.DataFrame({"SINK_MODIFIED_ON": ["2026-02-01T00:00:00Z"]}),
    )
    helpers.remember_chunk_watermark(
        "t1",
        pd.DataFrame({"SINK_MODIFIED_ON": ["2026-01-15T00:00:00Z"]}),
    )
    assert helpers.peek_watermark("t1") == "2026-02-01T00.00.00Z"


def test_copy_into_sql_shape():
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    helpers._copy_into_staging(
        engine,
        "`main`.`myschema`.`DS_T_STAGING_20260101_120000`",
        "gs://bucket/path/file.csv",
        columns=["APPROVED", "NAME"],
        attributes_details_field={"APPROVED": "BIGINT", "NAME": "STRING"},
    )

    sql = conn.execute.call_args[0][0]
    assert "COPY INTO `main`.`myschema`.`DS_T_STAGING_20260101_120000`" in sql
    assert "SELECT" in sql
    assert "gs://bucket/path/file.csv" in sql
    assert "CAST(NULLIF(TRIM(`APPROVED`), '') AS BIGINT) AS `APPROVED`" in sql
    assert "`NAME` AS `NAME`" in sql
    assert "FILEFORMAT = CSV" in sql
    assert "'mergeSchema' = 'false'" in sql
    assert "'inferSchema' = 'false'" in sql
    # Direct FROM path without SELECT must not be used when columns are provided
    assert "FROM 'gs://bucket/path/file.csv'\n    FILEFORMAT" not in sql


def test_ensure_staging_table_maps_attribute_types(monkeypatch):
    """First call creates staging (no DROP); typed columns from Dynamics attributes."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False
    df = pd.DataFrame({"APPROVED": [1], "ISLOCATIONOWNER": [0], "NAME": ["a"]})

    # DESCRIBE fails => table missing => CREATE once (no DROP).
    monkeypatch.setattr(
        helpers,
        "_list_table_column_names",
        MagicMock(side_effect=Exception("TABLE_OR_VIEW_NOT_FOUND")),
    )
    monkeypatch.setattr(helpers, "_sync_base_table_columns", MagicMock())
    qualified = helpers._ensure_staging_table(
        engine,
        "test",
        "LANDING",
        "DS_T_STAGING",
        df,
        {"APPROVED": "BIGINT", "ISLOCATIONOWNER": "BOOLEAN", "NAME": "STRING"},
    )

    assert qualified == "`test`.`LANDING`.`DS_T_STAGING`"
    assert conn.execute.call_count == 1
    create_sql = conn.execute.call_args_list[0][0][0]
    assert "CREATE TABLE IF NOT EXISTS `test`.`LANDING`.`DS_T_STAGING`" in create_sql
    assert "DROP TABLE" not in create_sql
    assert "`APPROVED` BIGINT" in create_sql
    assert "`ISLOCATIONOWNER` BOOLEAN" in create_sql
    assert "`NAME` STRING" in create_sql
    # Staging stays plain Delta — Fivetran-aligned props are base-table only.
    assert "TBLPROPERTIES" not in create_sql
    assert "enableDeletionVectors" not in create_sql


def test_ensure_base_table_create_has_fivetran_aligned_tblproperties(monkeypatch):
    """Durable base tables get DV/zstd/checkpoint props; CREATE IF NOT EXISTS."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False
    monkeypatch.setattr(helpers, "check_table_exists_databricks", MagicMock(return_value=False))
    monkeypatch.setattr(helpers, "_catalog_from_config", MagicMock(return_value="main"))

    ok = helpers.ensure_databricks_base_table_exists(
        engine,
        "custtrans",
        "landing",
        pd.DataFrame(columns=["ID", "SINK_MODIFIED_ON", "IS_DELETE"]),
        {
            "ID": "STRING",
            "SINK_MODIFIED_ON": "TIMESTAMP",
            "IS_DELETE": "BOOLEAN",
        },
        connection_id=24,
        cmp_id=1,
        destination_connection={"schema": "landing", "catalog": "main"},
    )

    assert ok is True
    create_sql = conn.execute.call_args[0][0]
    assert "CREATE TABLE IF NOT EXISTS `main`.`landing`.`custtrans`" in create_sql
    assert "USING DELTA" in create_sql
    assert "'delta.enableDeletionVectors' = 'true'" in create_sql
    assert "'delta.checkpoint.writeStatsAsJson' = 'false'" in create_sql
    assert "'delta.checkpoint.writeStatsAsStruct' = 'true'" in create_sql
    assert "'parquet.compression.codec' = 'zstd'" in create_sql
    assert "enableRowTracking" not in create_sql


def test_ensure_staging_table_reuses_without_drop(monkeypatch):
    """Later chunks must not DROP staging — only sync missing columns."""
    engine = MagicMock()
    monkeypatch.setattr(
        helpers,
        "_list_table_column_names",
        MagicMock(return_value={"ID", "NAME"}),
    )
    sync = MagicMock()
    monkeypatch.setattr(helpers, "_sync_base_table_columns", sync)

    df = pd.DataFrame({"ID": [1], "NAME": ["a"], "EXTRA": ["x"]})
    qualified = helpers._ensure_staging_table(
        engine,
        "test",
        "LANDING",
        "DS_T_STAGING",
        df,
        {"ID": "BIGINT", "NAME": "STRING", "EXTRA": "STRING"},
    )

    assert qualified == "`test`.`LANDING`.`DS_T_STAGING`"
    sync.assert_called_once()
    engine.connect.assert_not_called()


def test_copy_into_cast_select_maps_types():
    select = helpers._copy_into_cast_select(
        ["APPROVED", "ISLOCATIONOWNER", "AMT", "NOTE"],
        {
            "APPROVED": "BIGINT",
            "ISLOCATIONOWNER": "BOOLEAN",
            "AMT": "DECIMAL(38,6)",
            "NOTE": "STRING",
        },
    )
    assert "CAST(NULLIF(TRIM(`APPROVED`), '') AS BIGINT) AS `APPROVED`" in select
    assert "CAST(NULLIF(TRIM(`ISLOCATIONOWNER`), '') AS BOOLEAN) AS `ISLOCATIONOWNER`" in select
    assert "CAST(NULLIF(TRIM(`AMT`), '') AS DECIMAL(38,6)) AS `AMT`" in select
    assert "`NOTE` AS `NOTE`" in select
    assert "STRING" not in select.split("`NOTE`")[0]  # NOTE stays uncast STRING alias


def test_copy_into_cast_prefers_existing_staging_types():
    """Resumed staging: CAST to live Delta types to avoid DELTA_FAILED_TO_MERGE_FIELDS."""
    select = helpers._copy_into_cast_select(
        ["ATI_DTUNAPPLIEDCUSTTRANSRECID", "APPROVED"],
        {
            "ATI_DTUNAPPLIEDCUSTTRANSRECID": "BIGINT",
            "APPROVED": "BIGINT",
        },
        existing_column_types={
            "ATI_DTUNAPPLIEDCUSTTRANSRECID": "STRING",
            "APPROVED": "BIGINT",
        },
    )
    # Existing STRING wins over attribute BIGINT — no CAST clash on resume.
    assert "`ATI_DTUNAPPLIEDCUSTTRANSRECID` AS `ATI_DTUNAPPLIEDCUSTTRANSRECID`" in select
    assert "CAST(NULLIF(TRIM(`ATI_DTUNAPPLIEDCUSTTRANSRECID`)" not in select
    assert "CAST(NULLIF(TRIM(`APPROVED`), '') AS BIGINT) AS `APPROVED`" in select


def test_copy_into_cast_select_parquet_skips_csv_trim():
    select = helpers._copy_into_cast_select(
        ["APPROVED", "NOTE"],
        {"APPROVED": "BIGINT", "NOTE": "STRING"},
        file_format="PARQUET",
    )
    assert "CAST(`APPROVED` AS BIGINT) AS `APPROVED`" in select
    assert "NULLIF" not in select
    assert "TRIM" not in select
    assert "CAST(`NOTE` AS STRING) AS `NOTE`" in select


def test_copy_into_cast_select_parquet_date_handles_int_and_string():
    select = helpers._copy_into_cast_select(
        ["DATE_C"],
        {"DATE_C": "DATE"},
        file_format="PARQUET",
    )
    assert "DATE_FROM_UNIX_DATE(TRY_CAST(`DATE_C` AS INT))" in select
    assert "TRY_CAST(CAST(`DATE_C` AS STRING) AS DATE)" in select


def test_copy_into_cast_select_parquet_timestamp_handles_epoch():
    select = helpers._copy_into_cast_select(
        ["CREATED_DATE"],
        {"CREATED_DATE": "TIMESTAMP"},
        file_format="PARQUET",
    )
    assert "TRY_CAST(`CREATED_DATE` AS TIMESTAMP)" in select
    assert "TIMESTAMP_MILLIS(TRY_CAST(`CREATED_DATE` AS BIGINT))" in select


def test_copy_into_accepts_managed_volume_path():
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    helpers._copy_into_staging(
        engine,
        "`main`.`myschema`.`staging`",
        "/Volumes/main/myschema/stage/temp_data_dynamics_f&o_CUSTTABLE_1.parquet",
    )
    sql = conn.execute.call_args[0][0]
    assert (
        "FROM '/Volumes/main/myschema/stage/temp_data_dynamics_f&o_CUSTTABLE_1.parquet'"
        in sql
    )
    assert "FILEFORMAT = PARQUET" in sql
    assert "FILEFORMAT = CSV" not in sql
    assert "'header' = 'true'" not in sql


def test_copy_into_volume_error_is_actionable():
    from services.databricks.volume_staging_client import VolumeCopyIntoError

    conn = MagicMock()
    conn.execute.side_effect = Exception(
        "Permission denied: VOLUME insufficient privileges"
    )
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    with pytest.raises(VolumeCopyIntoError, match="managed volume staging file"):
        helpers._copy_into_staging(
            engine,
            "`test`.`demo`.`staging`",
            "/Volumes/test/demo/stage/file.parquet",
        )


def test_copy_into_external_location_error_is_actionable():
    conn = MagicMock()
    conn.execute.side_effect = Exception("Permission denied: external location not found")
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    with pytest.raises(ValueError, match="Unity Catalog cannot read the GCS temp CSV"):
        helpers._copy_into_staging(
            engine,
            "`test`.`demo`.`staging`",
            "gs://bucket/path/file.csv",
        )


def test_resolve_staging_method_defaults_and_explicit():
    assert (
        helpers.resolve_databricks_staging_method({})
        == helpers.STAGING_METHOD_MANAGED_VOLUME
    )
    assert (
        helpers.resolve_databricks_staging_method({"volume_name": "v1"})
        == helpers.STAGING_METHOD_MANAGED_VOLUME
    )
    assert (
        helpers.resolve_databricks_staging_method(
            {"staging_method": "managed_volume"}
        )
        == helpers.STAGING_METHOD_MANAGED_VOLUME
    )
    assert (
        helpers.resolve_databricks_staging_method(
            {"staging_method": "external_location", "volume_name": "v1"}
        )
        == helpers.STAGING_METHOD_EXTERNAL_LOCATION
    )


def test_resolve_staging_settings_defaults_managed_volume_name():
    settings = helpers.resolve_databricks_staging_settings(
        {"catalog": "main", "schema": "demo"}
    )
    assert settings["staging_method"] == helpers.STAGING_METHOD_MANAGED_VOLUME
    assert settings["volume_name"] == helpers.DEFAULT_DATABRICKS_VOLUME_NAME


def test_copy_function_uses_volume_client_when_managed(monkeypatch):
    from unittest.mock import patch

    df = pd.DataFrame(
        {
            "ID": [1],
            "NAME": ["A"],
            "IS_DELETE": [False],
            "SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z"],
        }
    )
    engine = MagicMock()
    engine.config = {
        "catalog": "test",
        "staging_method": "managed_volume",
        "volume_name": "stage",
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }

    monkeypatch.setattr(helpers, "ensure_databricks_base_table_exists", MagicMock())
    monkeypatch.setattr(
        helpers, "_ensure_staging_table", MagicMock(return_value="`test`.`demo`.`staging`")
    )
    monkeypatch.setattr(helpers, "_copy_into_staging", MagicMock())
    monkeypatch.setattr(helpers, "_insert_dataframe", MagicMock())
    monkeypatch.setattr(helpers, "_merge_staging_to_base", MagicMock())
    monkeypatch.setattr(helpers, "_sync_base_table_columns", MagicMock())
    monkeypatch.setattr(helpers, "_count_qualified_table_rows", MagicMock(return_value=1))

    volume_client = MagicMock()
    volume_client.volume_root = "/Volumes/test/demo/stage"
    volume_client.upload_dataframe.return_value = (
        "/Volumes/test/demo/stage/temp_data_dynamics_f&o_CUSTTABLE_99.parquet"
    )
    monkeypatch.setattr(helpers, "_volume_client_from_engine", MagicMock(return_value=volume_client))

    with patch("services.dynamics.source_common.upload_to_bucket") as gcs_upload, patch(
        "utils.load_checkpoint.update_load_checkpoint",
        MagicMock(),
    ):
        helpers.copy_function(
            df=df,
            table_name="CUSTTABLE",
            flag=True,
            destination_engine=engine,
            snowflake_schema="demo",
            snowflake_table_formatted="CUSTTABLE",
            snowflake_table_formatted_original="CUSTTABLE",
            is_integration_checked=False,
            extra_field=None,
            table_exist=True,
            modified_fields=[],
            existing_fields=[],
            attributes_details_field={"ID": "guid", "NAME": "string"},
            connection_id=99,
            primary_key="ID",
            snowflake_database="test",
            cmp_id=66,
            staging_table_name="DS_CUSTTABLE_STAGING_1",
        )

    gcs_upload.assert_not_called()
    volume_client.upload_dataframe.assert_called_once()
    volume_client.delete_absolute.assert_called_once_with(
        "/Volumes/test/demo/stage/temp_data_dynamics_f&o_CUSTTABLE_99.parquet"
    )
    volume_client.delete_chunk.assert_not_called()
    helpers._copy_into_staging.assert_called_once()
    helpers._merge_staging_to_base.assert_called_once()
    staged_url = helpers._copy_into_staging.call_args[0][2]
    assert staged_url.startswith("/Volumes/")
    assert staged_url.endswith(".parquet")


def test_copy_function_updates_progress_after_per_chunk_merge(monkeypatch):
    """Overview binds to cumulative merged_records_count after per-chunk MERGE."""
    from unittest.mock import patch

    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "NAME": ["A", "B"],
            "IS_DELETE": [False, False],
            "SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"],
        }
    )
    engine = MagicMock()
    engine.config = {
        "catalog": "test",
        "schema": "demo",
        "staging_method": "managed_volume",
        "volume_name": "stage",
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }

    monkeypatch.setattr(helpers, "ensure_databricks_base_table_exists", MagicMock())
    monkeypatch.setattr(
        helpers, "_ensure_staging_table", MagicMock(return_value="`test`.`demo`.`staging`")
    )
    monkeypatch.setattr(helpers, "_delete_staging_ids_in_batches", MagicMock(return_value=0))
    monkeypatch.setattr(helpers, "_copy_into_staging", MagicMock())
    merge_mock = MagicMock()
    monkeypatch.setattr(helpers, "_merge_staging_to_base", merge_mock)
    monkeypatch.setattr(helpers, "_sync_base_table_columns", MagicMock())
    monkeypatch.setattr(helpers, "_count_qualified_table_rows", MagicMock(return_value=5))

    volume_client = MagicMock()
    volume_client.volume_root = "/Volumes/test/demo/stage"
    volume_client.upload_dataframe.return_value = (
        "/Volumes/test/demo/stage/temp_data_dynamics_f&o_CUSTTABLE_99.parquet"
    )
    monkeypatch.setattr(helpers, "_volume_client_from_engine", MagicMock(return_value=volume_client))

    progress = MagicMock()
    checkpoint = MagicMock()
    with patch(
        "utils.simple_progress_tracker.SimpleProgressTracker.set_staging_count",
        progress,
    ), patch(
        "utils.load_checkpoint.update_load_checkpoint",
        checkpoint,
    ), patch(
        "utils.load_checkpoint.get_migration_record_staging_count",
        MagicMock(return_value=3),
    ):
        helpers.copy_function(
            df=df,
            table_name="CUSTTABLE",
            flag=True,
            destination_engine=engine,
            snowflake_schema="demo",
            snowflake_table_formatted="CUSTTABLE",
            snowflake_table_formatted_original="CUSTTABLE",
            is_integration_checked=False,
            extra_field=None,
            table_exist=True,
            modified_fields=[],
            existing_fields=[],
            attributes_details_field={"ID": "guid", "NAME": "string"},
            connection_id=99,
            primary_key="ID",
            snowflake_database="test",
            cmp_id=66,
            migration_session_id=4165,
            job_name="pipeline_run_1146_node_141__job",
            staging_table_name="DS_CUSTTABLE_STAGING_1",
            folder_checkpoint="2026-08-01T12.00.00Z",
            safety_interval_minutes=30,
        )

    # Per-chunk merge: COPY then MERGE.
    merge_mock.assert_called_once()
    progress.assert_called_once()
    kwargs = progress.call_args.kwargs
    assert kwargs["connection_id"] == 99
    assert kwargs["migration_session_id"] == 4165
    assert kwargs["job_name"] == "pipeline_run_1146_node_141__job"
    assert kwargs["table_name"] == "CUSTTABLE"
    assert kwargs["count_value"] == 5
    checkpoint.assert_called_once()
    assert checkpoint.call_args.kwargs["merged_records_count"] == 5
    assert checkpoint.call_args.kwargs["folder_checkpoint"] == "2026-08-01T12.00.00Z"


def test_copy_function_progress_uses_source_table_name_for_migration_record(monkeypatch):
    """Salesforce stores migration_record.table_name as FeedItem, not FEED_ITEM."""
    from unittest.mock import patch

    df = pd.DataFrame(
        {
            "ID": ["001"],
            "NAME": ["A"],
            "IS_DELETE": [False],
            "SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z"],
        }
    )
    engine = MagicMock()
    engine.config = {
        "catalog": "test",
        "schema": "demo",
        "staging_method": "managed_volume",
        "volume_name": "stage",
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }

    monkeypatch.setattr(helpers, "ensure_databricks_base_table_exists", MagicMock())
    monkeypatch.setattr(
        helpers, "_ensure_staging_table", MagicMock(return_value="`test`.`demo`.`staging`")
    )
    monkeypatch.setattr(helpers, "_delete_staging_ids_in_batches", MagicMock(return_value=0))
    monkeypatch.setattr(helpers, "_copy_into_staging", MagicMock())
    monkeypatch.setattr(helpers, "_merge_staging_to_base", MagicMock())
    monkeypatch.setattr(helpers, "_sync_base_table_columns", MagicMock())
    monkeypatch.setattr(helpers, "_count_qualified_table_rows", MagicMock(return_value=1))

    volume_client = MagicMock()
    volume_client.volume_root = "/Volumes/test/demo/stage"
    volume_client.upload_dataframe.return_value = (
        "/Volumes/test/demo/stage/temp_data_salesforce_FEED_ITEM_26.parquet"
    )
    monkeypatch.setattr(helpers, "_volume_client_from_engine", MagicMock(return_value=volume_client))

    progress = MagicMock()
    with patch(
        "utils.simple_progress_tracker.SimpleProgressTracker.set_staging_count",
        progress,
    ), patch(
        "utils.load_checkpoint.update_load_checkpoint",
        MagicMock(),
    ), patch(
        "utils.load_checkpoint.get_migration_record_staging_count",
        MagicMock(return_value=0),
    ):
        helpers.copy_function(
            df=df,
            table_name="FEED_ITEM",
            flag=False,
            destination_engine=engine,
            snowflake_schema="demo",
            snowflake_table_formatted="FEED_ITEM",
            snowflake_table_formatted_original="FeedItem",
            is_integration_checked=True,
            extra_field=[],
            table_exist=True,
            modified_fields={},
            existing_fields={},
            attributes_details_field={"ID": "id", "NAME": "string"},
            connection_id=26,
            primary_key="ID",
            snowflake_database="test",
            cmp_id=1,
            migration_session_id=12398,
            job_name="sf_databricks_ati_sandbox_manual_12398_FeedItem",
            staging_table_name="DS_FEED_ITEM_STAGING_1",
        )

    progress.assert_called_once()
    assert progress.call_args.kwargs["table_name"] == "FeedItem"
    assert progress.call_args.kwargs["count_value"] == 1


def test_copy_function_uses_prior_merged_count_when_session_present(monkeypatch):
    """Cumulative progress uses migration_record count + chunk rows."""
    from unittest.mock import patch

    df = pd.DataFrame(
        {
            "ID": [1],
            "NAME": ["A"],
            "IS_DELETE": [False],
            "SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z"],
        }
    )
    engine = MagicMock()
    engine.config = {
        "catalog": "test",
        "schema": "demo",
        "staging_method": "managed_volume",
        "volume_name": "stage",
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }

    monkeypatch.setattr(helpers, "ensure_databricks_base_table_exists", MagicMock())
    monkeypatch.setattr(
        helpers, "_ensure_staging_table", MagicMock(return_value="`test`.`demo`.`staging`")
    )
    monkeypatch.setattr(helpers, "_copy_into_staging", MagicMock())
    monkeypatch.setattr(helpers, "_merge_staging_to_base", MagicMock())

    volume_client = MagicMock()
    volume_client.volume_root = "/Volumes/test/demo/stage"
    volume_client.upload_dataframe.return_value = (
        "/Volumes/test/demo/stage/temp_data_dynamics_f&o_CUSTTABLE_99.parquet"
    )
    monkeypatch.setattr(helpers, "_volume_client_from_engine", MagicMock(return_value=volume_client))

    progress = MagicMock()
    checkpoint = MagicMock()
    with patch(
        "utils.simple_progress_tracker.SimpleProgressTracker.set_staging_count",
        progress,
    ), patch(
        "utils.load_checkpoint.update_load_checkpoint",
        checkpoint,
    ), patch(
        "utils.load_checkpoint.get_migration_record_staging_count",
        MagicMock(return_value=10),
    ):
        helpers.copy_function(
            df=df,
            table_name="CUSTTABLE",
            flag=True,
            destination_engine=engine,
            snowflake_schema="demo",
            snowflake_table_formatted="CUSTTABLE",
            snowflake_table_formatted_original="CUSTTABLE",
            is_integration_checked=False,
            extra_field=None,
            table_exist=True,
            modified_fields=[],
            existing_fields=[],
            attributes_details_field={"ID": "guid", "NAME": "string"},
            connection_id=99,
            primary_key="ID",
            snowflake_database="test",
            cmp_id=66,
            migration_session_id=1,
            job_name="job",
            staging_table_name="DS_CUSTTABLE_STAGING_1",
        )

    progress.assert_called_once()
    assert progress.call_args.kwargs["count_value"] == 11
    checkpoint.assert_called_once()
    assert checkpoint.call_args.kwargs["merged_records_count"] == 11


def test_validate_schema_evolution_rejects_type_mismatch():
    with pytest.raises(ValueError, match="Schema type mismatch"):
        helpers._validate_schema_evolution(
            "custtrans",
            {"APPROVED": "BOOLEAN"},
            [],
            {"APPROVED": "BIGINT"},
        )


def test_validate_schema_evolution_rejects_extra_without_metadata():
    with pytest.raises(ValueError, match="missing from the Dynamics model.json"):
        helpers._validate_schema_evolution(
            "custtrans",
            {},
            ["NEWFIELD"],
            {"ID": "BIGINT"},
        )


def test_validate_schema_evolution_allows_new_columns_with_metadata():
    helpers._validate_schema_evolution(
        "custtrans",
        {},
        ["NEWFIELD"],
        {"ID": "BIGINT", "NEWFIELD": "STRING"},
    )


def test_validate_schema_evolution_allows_raw_names_against_normalized_metadata():
    """
    copy_function normalizes model.json keys to SNAKE_CASE before validation,
    while extra_fields keep raw Dynamics names — must still resolve as present.
    """
    helpers._validate_schema_evolution(
        "custtrans",
        {},
        [
            "ati_dtunappliedcustpaymcreated",
            "ati_dtunappliedcustpaymissettled",
            "ati_dtunappliedcusttransrecid",
        ],
        {
            "ID": "STRING",
            "ATI_DTUNAPPLIEDCUSTPAYMCREATED": "BOOLEAN",
            "ATI_DTUNAPPLIEDCUSTPAYMISSETTLED": "BOOLEAN",
            "ATI_DTUNAPPLIEDCUSTTRANSRECID": "BIGINT",
        },
    )


def test_copy_function_raises_on_copy_into_failure(monkeypatch):
    from unittest.mock import patch

    df = pd.DataFrame(
        {
            "ID": [1],
            "NAME": ["A"],
            "IS_DELETE": [False],
            "SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z"],
        }
    )
    engine = MagicMock()
    engine.config = {"catalog": "test", "staging_method": "external_location"}

    monkeypatch.setattr(helpers, "ensure_databricks_base_table_exists", MagicMock())
    monkeypatch.setattr(helpers, "_ensure_staging_table", MagicMock(return_value="`test`.`demo`.`staging`"))
    monkeypatch.setattr(helpers, "_resolve_stage_url", MagicMock(return_value="gs://bucket/"))
    monkeypatch.setattr(
        helpers,
        "_copy_into_staging",
        MagicMock(side_effect=Exception("external location not found")),
    )
    insert_mock = MagicMock()
    monkeypatch.setattr(helpers, "_insert_dataframe", insert_mock)
    merge_mock = MagicMock()
    monkeypatch.setattr(helpers, "_merge_staging_to_base", merge_mock)

    upload_mock = MagicMock(return_value="gs://bucket/path/file.csv")
    delete_mock = MagicMock()
    with patch(
        "services.dynamics.source_common.upload_to_bucket",
        upload_mock,
    ), patch(
        "services.dynamics.source_common.delete_csv_from_bucket",
        delete_mock,
    ):
        with pytest.raises(Exception, match="external location not found"):
            helpers.copy_function(
                df=df,
                table_name="CUSTTABLE",
                flag=True,
                destination_engine=engine,
                snowflake_schema="demo",
                snowflake_table_formatted="CUSTTABLE",
                snowflake_table_formatted_original="CUSTTABLE",
                is_integration_checked=False,
                extra_field=[],
                table_exist=True,
                modified_fields={},
                existing_fields=[],
                attributes_details_field={"ID": "guid", "NAME": "string"},
                connection_id=99,
                primary_key="ID",
                snowflake_database="test",
                cmp_id=66,
                staging_table_name="DS_CUSTTABLE_STAGING_1",
            )

    insert_mock.assert_not_called()
    merge_mock.assert_not_called()


def test_merge_into_databricks_runs_end_of_job_merge(monkeypatch):
    """End-of-table merge_into_snowflake (alias merge_into_databricks) performs MERGE once."""
    from unittest.mock import patch

    merge_mock = MagicMock()
    monkeypatch.setattr(helpers, "_merge_staging_to_base", merge_mock)
    monkeypatch.setattr(helpers, "_sync_base_table_columns", MagicMock())
    monkeypatch.setattr(
        helpers,
        "_list_table_column_names",
        MagicMock(return_value={"ID", "NAME", "IS_DELETE", "SINK_MODIFIED_ON"}),
    )
    clear_mock = MagicMock()
    with patch("utils.load_checkpoint.finalize_migration_record", clear_mock):
        with patch(
            "utils.simple_progress_tracker.SimpleProgressTracker.set_staging_count",
            MagicMock(),
        ) as set_count:
            helpers.merge_into_snowflake(
                destination_engine=MagicMock(config={"catalog": "main"}),
                snowflake_schema="demo",
                table_name="CUSTTABLE",
                primary_key="ID",
                df=pd.DataFrame(columns=["ID", "NAME", "IS_DELETE", "SINK_MODIFIED_ON"]),
                updated_at="SINK_MODIFIED_ON",
                deleted_at="IS_DELETE",
                cmp_id=1,
                connection_id=23,
                staging_table_name="DS_CUSTTABLE_STAGING_1",
                migration_session_id=99,
                job_name="job1",
                final_records_count=12345,
            )

    merge_mock.assert_called_once()
    clear_mock.assert_called_once()
    assert clear_mock.call_args.kwargs["table_name"] == "CUSTTABLE"
    assert clear_mock.call_args.kwargs["connection_id"] == 23
    assert clear_mock.call_args.kwargs["final_records_count"] == 12345
    set_count.assert_called_once()
    assert set_count.call_args.kwargs["count_value"] == 12345


def test_safe_folder_checkpoint_subtracts_safety_interval():
    from utils.load_checkpoint import safe_folder_checkpoint, should_resume_migration

    assert safe_folder_checkpoint("2026-08-01T12.00.00Z", 30) == "2026-08-01T11.30.00Z"
    assert safe_folder_checkpoint("2026-08-01T12.00.00Z", None) == "2026-08-01T11.30.00Z"
    assert safe_folder_checkpoint("", 30) is None
    assert should_resume_migration("in_progress", "2026-08-01T11.30.00Z")
    assert should_resume_migration("failed", "2026-08-01T11.30.00Z")
    assert not should_resume_migration("failed", None)
    assert not should_resume_migration("completed", "2026-08-01T11.30.00Z")
    assert not should_resume_migration(None, None)


def test_merge_sql_shape_and_truncate():
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    # Avoid information_schema probes changing execute call count.
    from unittest.mock import patch

    with patch.object(helpers, "_list_table_column_names", return_value=set()):
        helpers._merge_staging_to_base(
            engine,
            "main",
            "myschema",
            "CUSTTABLE",
            "DS_CUSTTABLE_STAGING_1",
            ["ID", "NAME", "IS_DELETE", "SINK_MODIFIED_ON"],
            "ID",
            updated_at="SINK_MODIFIED_ON",
            deleted_at="IS_DELETE",
        )

    assert conn.execute.call_count == 2
    merge_sql = conn.execute.call_args_list[0][0][0]
    truncate_sql = conn.execute.call_args_list[1][0][0]
    assert "MERGE INTO `main`.`myschema`.`CUSTTABLE` AS target" in merge_sql
    assert "USING (" in merge_sql
    assert "FROM `main`.`myschema`.`DS_CUSTTABLE_STAGING_1`" in merge_sql
    assert "QUALIFY ROW_NUMBER() OVER (" in merge_sql
    assert "PARTITION BY `ID`" in merge_sql
    assert "ORDER BY `SINK_MODIFIED_ON` DESC NULLS LAST" in merge_sql
    assert "WHEN MATCHED THEN UPDATE SET" in merge_sql
    assert merge_sql.count("target.`IS_DELETE` = source.`IS_DELETE`") == 1
    assert merge_sql.count("target.`SINK_MODIFIED_ON` = source.`SINK_MODIFIED_ON`") == 1
    assert "WHEN NOT MATCHED AND NOT (" in merge_sql
    assert "THEN INSERT" in merge_sql
    assert truncate_sql == "TRUNCATE TABLE `main`.`myschema`.`DS_CUSTTABLE_STAGING_1`"


def test_delete_staging_ids_in_batches():
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    deleted = helpers._delete_staging_ids_in_batches(
        engine,
        "`main`.`myschema`.`DS_T_STAGING`",
        ["a", "b", "a", None],
        pk_column="ID",
        batch_size=2,
    )
    assert deleted == 2
    assert conn.execute.call_count == 1
    sql = conn.execute.call_args[0][0]
    assert "DELETE FROM `main`.`myschema`.`DS_T_STAGING`" in sql
    assert "WHERE `ID` IN (" in sql
    assert "'a'" in sql
    assert "'b'" in sql


def test_merge_staging_source_sql_prefers_sink_modified_on():
    sql = helpers._merge_staging_source_sql(
        "`main`.`s`.`STG`",
        ["ID", "NAME", "SINK_MODIFIED_ON"],
        "ID",
        updated_at="UPDATEDON",
    )
    assert "QUALIFY ROW_NUMBER() OVER (" in sql
    assert "PARTITION BY `ID`" in sql
    assert "ORDER BY `SINK_MODIFIED_ON` DESC NULLS LAST" in sql
    assert "`ID`, `NAME`, `SINK_MODIFIED_ON`" in sql


def test_merge_staging_source_sql_composite_primary_keys():
    sql = helpers._merge_staging_source_sql(
        "`main`.`s`.`STG`",
        ["BUKRS", "BELNR", "BUZEI", "GJAHR", "SINK_MODIFIED_ON"],
        ["BUKRS", "BELNR", "BUZEI", "GJAHR"],
        updated_at="SINK_MODIFIED_ON",
    )
    assert "PARTITION BY `BUKRS`, `BELNR`, `BUZEI`, `GJAHR`" in sql
    assert "ORDER BY `SINK_MODIFIED_ON` DESC NULLS LAST" in sql


def test_merge_sql_composite_on_clause():
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    from unittest.mock import patch

    with patch.object(helpers, "_list_table_column_names", return_value=set()):
        with patch.object(helpers, "_sync_base_table_columns"):
            helpers._merge_staging_to_base(
                engine,
                "main",
                "myschema",
                "ENTITYOF0FI_GL_4",
                "DS_ENTITYOF0FI_GL_4_STAGING_1",
                ["BUKRS", "BELNR", "BUZEI", "GJAHR", "DMBTR", "SINK_MODIFIED_ON"],
                ["BUKRS", "BELNR", "BUZEI", "GJAHR"],
                updated_at="SINK_MODIFIED_ON",
                deleted_at="_FIVETRAN_DELETED",
            )

    executed = [c[0][0] for c in conn.execute.call_args_list]
    merge_sql = next(s for s in executed if isinstance(s, str) and "MERGE INTO" in s)
    assert "PARTITION BY `BUKRS`, `BELNR`, `BUZEI`, `GJAHR`" in merge_sql
    assert (
        "ON target.`BUKRS` = source.`BUKRS` AND target.`BELNR` = source.`BELNR` "
        "AND target.`BUZEI` = source.`BUZEI` AND target.`GJAHR` = source.`GJAHR`"
    ) in merge_sql


def test_drop_databricks_table():
    conn = MagicMock()
    engine = MagicMock()
    engine.config = {"catalog": "main"}
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    result = helpers.drop_databricks_table(engine, "CUSTTABLE", schema="myschema")
    assert result["success"] is True
    sql = conn.execute.call_args[0][0]
    assert sql == "DROP TABLE IF EXISTS `main`.`myschema`.`CUSTTABLE`"


def test_format_watermark_naive_assumes_utc():
    dt = datetime(2026, 3, 1, 4, 5, 6)
    assert helpers.format_watermark(dt) == "2026-03-01T04.05.06Z"
    assert helpers.format_watermark(datetime(2026, 3, 1, 4, 5, 6, tzinfo=timezone.utc)) == (
        "2026-03-01T04.05.06Z"
    )


def test_resolve_databricks_gcs_settings_uses_env_bucket_not_connection(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET_URL", "gs://our-staging-bucket/")
    monkeypatch.delenv("DATABRICKS_GCS_EXTERNAL_LOCATION_MODE", raising=False)
    settings = helpers.resolve_databricks_gcs_settings(
        {
            "gcs_storage_credential_name": "client_cred",
            "gcs_external_location_name": "client_loc",
            "gcs_service_account_email": "dbx-sa@test.iam.gserviceaccount.com",
        }
    )
    assert settings["mode"] == "manual"
    assert settings["gcs_bucket_url"] == "gs://our-staging-bucket/"
    assert settings["credential_name"] == "client_cred"
    assert settings["location_name"] == "client_loc"
    assert settings["service_account_email"] == "dbx-sa@test.iam.gserviceaccount.com"
    assert settings["client_uc_configured"] is True


def test_manual_mode_skips_credential_creation(monkeypatch):
    engine = MagicMock()
    engine.config = {
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }
    engine.connect.return_value.__enter__.return_value = MagicMock(
        execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    )
    engine.connect.return_value.__exit__.return_value = False

    monkeypatch.setattr(
        helpers,
        "_databricks_api_request",
        MagicMock(
            return_value={"storage_credentials": []},
        ),
    )

    result = helpers.ensure_databricks_gcs_external_location(
        engine,
        "gs://bucket/",
        create_credential=False,
    )
    assert "manual_mode" in (result.get("skipped_reason") or "")
    assert result["credential_existed"] is False
    helpers._databricks_api_request.assert_called_once()


def test_find_covering_adls_external_location_picks_narrowest():
    locations = [
        (
            "datasyncher_batch_adls",
            "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
        ),
        (
            "datasyncher_fo_staging_31",
            "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/31/",
        ),
    ]
    target = "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/31/generaljournalaccountentry/"
    assert (
        helpers._find_covering_adls_external_location(locations, target)
        == "datasyncher_fo_staging_31"
    )


def test_ensure_adls_external_location_reuses_covering_location(monkeypatch):
    engine = MagicMock()
    engine.config = {
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        (
            "datasyncher_batch_adls",
            "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
            "datasyncher_batch_cred",
        ),
    ]
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    monkeypatch.setattr(
        helpers,
        "_databricks_api_request",
        MagicMock(
            return_value={
                "storage_credentials": [{"name": "datasyncher_fo_staging_cred"}],
            },
        ),
    )

    result = helpers.ensure_databricks_adls_external_location(
        engine,
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/31/",
        credential_name="datasyncher_fo_staging_cred",
        location_name="datasyncher_fo_staging_31",
        create_credential=True,
        azure_service_principal={
            "directory_id": "tenant",
            "application_id": "app",
            "client_secret": "secret",
        },
    )

    assert result["overlap_reused"] is True
    assert result["resolved_location_name"] == "datasyncher_batch_adls"
    assert result["location_existed"] is True
    assert result["grant_applied"] is True
    assert result.get("skipped_reason") is None
    create_calls = [
        str(call.args[0])
        for call in conn.execute.call_args_list
        if call.args and "CREATE EXTERNAL LOCATION" in str(call.args[0])
    ]
    assert create_calls == []
    grant_sql = str(conn.execute.call_args_list[-1].args[0])
    assert "datasyncher_batch_adls" in grant_sql


def test_ensure_adls_external_location_reuses_conflict_from_create_error(monkeypatch):
    engine = MagicMock()
    engine.config = {
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []

    def _execute(sql):
        if "CREATE EXTERNAL LOCATION" in str(sql):
            raise Exception(
                "LOCATION_OVERLAP: Input path overlaps with existing external location: "
                "datasyncher_batch_adls. Conflicting location: datasyncher_batch_adls"
            )
        return MagicMock()

    conn.execute.side_effect = _execute
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    monkeypatch.setattr(
        helpers,
        "_databricks_api_request",
        MagicMock(
            return_value={
                "storage_credentials": [{"name": "datasyncher_fo_staging_cred"}],
            },
        ),
    )

    result = helpers.ensure_databricks_adls_external_location(
        engine,
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/31/",
        credential_name="datasyncher_fo_staging_cred",
        location_name="datasyncher_fo_staging_31",
        create_credential=True,
        azure_service_principal={
            "directory_id": "tenant",
            "application_id": "app",
            "client_secret": "secret",
        },
    )

    assert result["overlap_reused"] is True
    assert result["resolved_location_name"] == "datasyncher_batch_adls"
    assert result["grant_applied"] is True
    assert result.get("skipped_reason") is None


def test_ensure_adls_external_location_uses_rest_when_sql_connect_fails(monkeypatch):
    engine = MagicMock()
    engine.config = {
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }
    engine.connect.side_effect = RuntimeError("all-purpose cluster TERMINATED")

    def _api(server_hostname, access_token, method, path, payload=None, **kwargs):
        if path.endswith("/storage-credentials"):
            return {"storage_credentials": [{"name": "datasyncher_fo_staging_cred"}]}
        if method == "GET" and path.endswith("/external-locations"):
            return {
                "external_locations": [
                    {
                        "name": "datasyncher_batch_adls",
                        "url": "abfss://datasyncher-stage@test4.dfs.core.windows.net/",
                    }
                ]
            }
        raise AssertionError(f"unexpected API {method} {path}")

    monkeypatch.setattr(helpers, "_databricks_api_request", _api)

    result = helpers.ensure_databricks_adls_external_location(
        engine,
        "abfss://datasyncher-stage@test4.dfs.core.windows.net/dynamics_fo/31/",
        credential_name="datasyncher_fo_staging_cred",
        location_name="datasyncher_fo_staging_31",
        create_credential=True,
        azure_service_principal={
            "directory_id": "tenant",
            "application_id": "app",
            "client_secret": "secret",
        },
    )

    assert result.get("via_rest") is True
    assert result["overlap_reused"] is True
    assert result["resolved_location_name"] == "datasyncher_batch_adls"
    assert result.get("skipped_reason") is None


def test_find_covering_s3_external_location_picks_narrowest():
    locations = [
        ("datasyncher_batch_s3", "s3://datasyncher-stg/dynamics_fo/"),
        ("datasyncher_batch_s3_73", "s3://datasyncher-stg/dynamics_fo/73/"),
    ]
    target = "s3://datasyncher-stg/dynamics_fo/73/generaljournalaccountentry/"
    assert (
        helpers._find_covering_s3_external_location(locations, target)
        == "datasyncher_batch_s3_73"
    )


def test_ensure_s3_external_location_reuses_covering_location(monkeypatch):
    engine = MagicMock()
    engine.config = {
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        (
            "datasyncher_batch_s3",
            "s3://datasyncher-stg/dynamics_fo/",
            "datasyncher_batch_s3_cred",
        ),
    ]
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    monkeypatch.setattr(
        helpers,
        "_databricks_api_request",
        MagicMock(
            return_value={
                "storage_credentials": [{"name": "datasyncher_batch_s3_cred"}],
            },
        ),
    )
    monkeypatch.setattr(
        helpers,
        "_fetch_databricks_storage_credential_aws",
        MagicMock(return_value={}),
    )

    result = helpers.ensure_databricks_s3_external_location(
        engine,
        "s3://datasyncher-stg/dynamics_fo/73",
        credential_name="datasyncher_batch_s3_cred",
        location_name="datasyncher_batch_s3_73",
        create_credential=True,
        grant_role_iam=False,
    )

    assert result["overlap_reused"] is True
    assert result["resolved_location_name"] == "datasyncher_batch_s3"
    assert result["location_existed"] is True
    assert result["grant_applied"] is True
    assert result.get("skipped_reason") is None
    create_calls = [
        str(call.args[0])
        for call in conn.execute.call_args_list
        if call.args and "CREATE EXTERNAL LOCATION" in str(call.args[0])
    ]
    assert create_calls == []
    grant_sql = str(conn.execute.call_args_list[-1].args[0])
    assert "`datasyncher_batch_s3`" in grant_sql


def test_ensure_s3_external_location_reuses_conflict_from_create_error(monkeypatch):
    engine = MagicMock()
    engine.config = {
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []

    def _execute(sql):
        if "CREATE EXTERNAL LOCATION" in str(sql):
            raise Exception(
                "[RequestId=23bb8aa8-4d33-486d-a662-15f3c2fb91b0 "
                "ErrorClass=INVALID_PARAMETER_VALUE.LOCATION_OVERLAP] "
                "Input path url 's3://datasyncher-stg/dynamics_fo/73' overlaps with an "
                "existing external location within 'CreateExternalLocation' call. "
                "Conflicting location: datasyncher_batch_s3_73"
            )
        return MagicMock()

    conn.execute.side_effect = _execute
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False

    monkeypatch.setattr(
        helpers,
        "_databricks_api_request",
        MagicMock(
            return_value={
                "storage_credentials": [{"name": "datasyncher_batch_s3_cred"}],
            },
        ),
    )
    monkeypatch.setattr(
        helpers,
        "_fetch_databricks_storage_credential_aws",
        MagicMock(return_value={}),
    )

    result = helpers.ensure_databricks_s3_external_location(
        engine,
        "s3://datasyncher-stg/dynamics_fo/73",
        credential_name="datasyncher_batch_s3_cred",
        location_name="datasyncher_batch_s3_73",
        create_credential=True,
        grant_role_iam=False,
    )

    assert result["overlap_reused"] is True
    assert result["resolved_location_name"] == "datasyncher_batch_s3_73"
    assert result["grant_applied"] is True
    assert result.get("skipped_reason") is None
