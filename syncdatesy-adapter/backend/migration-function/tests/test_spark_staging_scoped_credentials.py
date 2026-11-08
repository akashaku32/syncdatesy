"""Tests for zero-manual Spark staging access resolver and scoped credentials."""

from unittest.mock import MagicMock, patch

import pytest

from services.databricks.load_helpers import (
    SPARK_LANDING_CLOUD_AZURE,
    SPARK_STAGING_ACCESS_EXTERNAL_LOCATION,
    SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL,
    is_spark_staging_access_permission_failure,
    resolve_spark_staging_access,
    spark_scoped_fallback_enabled,
)
from services.databricks.spark_job_client import _job_settings_payload
from services.databricks.staging_scoped_credentials import (
    build_scoped_credential_spark_conf,
    format_scoped_credential_blocker_message,
    is_scoped_credential_workspace_blocker,
    mint_s3_temporary_credentials,
    secret_key_for_connection,
)


def test_permission_failure_detects_account_admin_403():
    assert is_spark_staging_access_permission_failure(
        "Only the account admin can create or update a storage credential "
        "with type AzureServicePrincipal"
    )
    assert is_spark_staging_access_permission_failure(
        "manual_mode: storage credential 'x' not found in Unity Catalog"
    )
    assert not is_spark_staging_access_permission_failure("LIST returned 0 entries")


def test_spark_scoped_fallback_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_SCOPED_FALLBACK", raising=False)
    assert spark_scoped_fallback_enabled() is False


def test_resolve_spark_staging_access_destination_override(monkeypatch):
    monkeypatch.setenv("DYNAMICS_DBX_SPARK_SCOPED_FALLBACK", "1")
    engine = MagicMock()
    mode = resolve_spark_staging_access(
        {"spark_staging_access": "scoped_credential"},
        engine,
        connection_id=82,
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert mode == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL


def test_resolve_spark_staging_access_ignores_scoped_override_when_fallback_off(
    monkeypatch,
):
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_SCOPED_FALLBACK", raising=False)
    engine = MagicMock()
    mode = resolve_spark_staging_access(
        {"spark_staging_access": "scoped_credential"},
        engine,
        connection_id=82,
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert mode == SPARK_STAGING_ACCESS_EXTERNAL_LOCATION


@patch("services.databricks.load_helpers._cache_set_spark_staging_access")
@patch("services.databricks.load_helpers._cache_get_spark_staging_access", return_value=None)
@patch("services.databricks.load_helpers._probe_spark_staging_external_location")
def test_resolve_spark_staging_access_probe_permission_picks_scoped(
    mock_probe, _mock_get, mock_set, monkeypatch
):
    monkeypatch.setenv("DYNAMICS_DBX_SPARK_SCOPED_FALLBACK", "1")
    mock_probe.return_value = (
        False,
        "Only the account admin can create or update a storage credential",
    )
    engine = MagicMock()
    engine.config = {"server_hostname": "adb-1.azuredatabricks.net", "access_token": "t"}
    mode = resolve_spark_staging_access(
        {"catalog": "main"},
        engine,
        connection_id=82,
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert mode == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL
    mock_set.assert_called_once()
    assert mock_set.call_args[0][1] == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL


@patch("services.databricks.load_helpers._cache_set_spark_staging_access")
@patch("services.databricks.load_helpers._cache_get_spark_staging_access", return_value=None)
@patch("services.databricks.load_helpers._probe_spark_staging_external_location")
def test_resolve_spark_staging_access_probe_fail_stays_external_when_fallback_off(
    mock_probe, _mock_get, mock_set, monkeypatch
):
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_SCOPED_FALLBACK", raising=False)
    mock_probe.return_value = (
        False,
        "Only the account admin can create or update a storage credential",
    )
    engine = MagicMock()
    engine.config = {"server_hostname": "adb-1.azuredatabricks.net", "access_token": "t"}
    mode = resolve_spark_staging_access(
        {"catalog": "main"},
        engine,
        connection_id=82,
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert mode == SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
    mock_set.assert_not_called()


@patch("services.databricks.load_helpers._cache_set_spark_staging_access")
@patch(
    "services.databricks.load_helpers._cache_get_spark_staging_access",
    return_value=SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL,
)
@patch("services.databricks.load_helpers._probe_spark_staging_external_location")
def test_resolve_spark_staging_access_ignores_cached_scoped_when_fallback_off(
    mock_probe, _mock_get, mock_set, monkeypatch
):
    monkeypatch.delenv("DYNAMICS_DBX_SPARK_SCOPED_FALLBACK", raising=False)
    mock_probe.return_value = (True, "adls_external_location_ok")
    engine = MagicMock()
    engine.config = {"server_hostname": "adb-1.azuredatabricks.net", "access_token": "t"}
    mode = resolve_spark_staging_access(
        {"catalog": "main"},
        engine,
        connection_id=82,
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert mode == SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
    mock_probe.assert_called_once()


@patch("services.databricks.load_helpers._cache_set_spark_staging_access")
@patch(
    "services.databricks.load_helpers._cache_get_spark_staging_access",
    return_value=SPARK_STAGING_ACCESS_EXTERNAL_LOCATION,
)
@patch("services.databricks.load_helpers._probe_spark_staging_external_location")
def test_resolve_spark_staging_access_honours_redis_cache(mock_probe, _mock_get, _mock_set):
    engine = MagicMock()
    engine.config = {"server_hostname": "adb-1.azuredatabricks.net", "access_token": "t"}
    mode = resolve_spark_staging_access(
        {"catalog": "main"},
        engine,
        connection_id=82,
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert mode == SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
    mock_probe.assert_not_called()


@patch("services.databricks.load_helpers._cache_set_spark_staging_access")
@patch("services.databricks.load_helpers._cache_get_spark_staging_access", return_value=None)
@patch("services.databricks.load_helpers._probe_spark_staging_external_location")
def test_resolve_spark_staging_access_probe_ok_picks_external(
    mock_probe, _mock_get, mock_set
):
    mock_probe.return_value = (True, "adls_external_location_ok")
    engine = MagicMock()
    engine.config = {"server_hostname": "adb-1.azuredatabricks.net", "access_token": "t"}
    mode = resolve_spark_staging_access(
        {"catalog": "main"},
        engine,
        connection_id=82,
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
    )
    assert mode == SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
    assert mock_set.call_args[0][1] == SPARK_STAGING_ACCESS_EXTERNAL_LOCATION


def test_job_settings_payload_scoped_single_user_uses_secret_refs():
    spark_conf = build_scoped_credential_spark_conf(
        "azure",
        connection_id=82,
        minted={
            "cloud": "azure",
            "account_name": "test4datasyncher",
            "container_name": "datasyncher-stage",
        },
        secret_keys={"sas_token": "conn_82"},
    )
    payload = _job_settings_payload(
        "/Shared/datasyncher/dynamics_fo_adls_merge",
        "datasyncher_dynamics_fo_adls_merge_scoped",
        compute_mode="classic",
        landing_cloud=SPARK_LANDING_CLOUD_AZURE,
        spark_conf=spark_conf,
        data_security_mode="SINGLE_USER",
        node_type_id="Standard_DS3_v2",
    )
    cluster = payload["job_clusters"][0]["new_cluster"]
    assert cluster["data_security_mode"] == "SINGLE_USER"
    assert cluster["node_type_id"] == "Standard_DS3_v2"
    conf = cluster["spark_conf"]
    host = "test4datasyncher.dfs.core.windows.net"
    assert conf[f"fs.azure.account.auth.type.{host}"] == "SAS"
    token_conf = conf[f"fs.azure.sas.fixed.token.datasyncher-stage.{host}"]
    assert token_conf == "{{secrets/datasyncher_staging/conn_82}}"
    assert "sv=" not in token_conf
    assert "sig=" not in token_conf


def test_build_s3_spark_conf_uses_temporary_provider_and_secret_refs():
    conf = build_scoped_credential_spark_conf(
        "s3",
        connection_id=9,
        minted={"cloud": "s3", "bucket_name": "datasyncher-stg"},
        secret_keys={
            "access_key_id": "conn_9_ak",
            "secret_access_key": "conn_9_sk",
            "session_token": "conn_9_token",
        },
    )
    assert (
        conf["fs.s3a.aws.credentials.provider"]
        == "org.apache.hadoop.fs.s3a.TemporaryAWSCredentialsProvider"
    )
    assert conf["fs.s3a.access.key"] == "{{secrets/datasyncher_staging/conn_9_ak}}"
    assert conf["fs.s3a.secret.key"] == "{{secrets/datasyncher_staging/conn_9_sk}}"
    assert conf["fs.s3a.session.token"] == "{{secrets/datasyncher_staging/conn_9_token}}"


@patch("boto3.client")
def test_mint_s3_temporary_credentials_assumes_role_with_prefix_policy(mock_boto_client):
    sts = MagicMock()
    sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "AKIATEST",
            "SecretAccessKey": "SECRET",
            "SessionToken": "TOKEN",
        }
    }
    mock_boto_client.return_value = sts

    with patch.dict(
        "os.environ",
        {
            "S3_BUCKET_NAME": "datasyncher-stg",
            "DATABRICKS_S3_SCOPED_ASSUME_ROLE_ARN": "arn:aws:iam::123:role/scoped",
            "AWS_REGION": "us-east-1",
        },
        clear=False,
    ):
        minted = mint_s3_temporary_credentials(connection_id=82)

    assert minted["access_key_id"] == "AKIATEST"
    assert minted["bucket_name"] == "datasyncher-stg"
    assert minted["prefix"] == "dynamics_fo/82/"
    kwargs = sts.assume_role.call_args.kwargs
    assert "dynamics_fo/82/" in kwargs["Policy"]
    assert "s3:GetObject" in kwargs["Policy"]
    assert "s3:PutObject" not in kwargs["Policy"]


def test_secret_key_for_connection_shapes():
    assert secret_key_for_connection(82) == "conn_82"
    assert secret_key_for_connection(82, "ak") == "conn_82_ak"


def test_scoped_blocker_message_is_actionable():
    assert is_scoped_credential_workspace_blocker("cannot create secret scopes")
    msg = format_scoped_credential_blocker_message("cluster policy denied")
    assert "datasyncher_staging" in msg
    assert "job cluster" in msg.lower() or "job clusters" in msg.lower()
