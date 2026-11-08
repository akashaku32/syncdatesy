"""Unit tests for DatabricksVolumeStagingClient (mocked Files API)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, mock_open, patch

import pandas as pd
import pytest

from services.databricks.volume_staging_client import (
    DatabricksVolumeStagingClient,
    DatabricksVolumeStagingError,
    VolumeNotFoundError,
    VolumePermissionError,
    VolumeUploadTimeoutError,
    classify_volume_http_error,
    dataframe_for_spark_parquet,
    volume_chunk_blob_name,
    volume_path,
)
from utils.retry_utils import RetryableHTTPError, retry_with_exponential_backoff


def test_volume_path_and_blob_naming():
    assert volume_path("main", "demo", "stage") == "/Volumes/main/demo/stage"
    assert (
        volume_path("main", "demo", "stage", "a/b.csv")
        == "/Volumes/main/demo/stage/a/b.csv"
    )
    name = volume_chunk_blob_name("CUSTTABLE", 99)
    assert name.startswith("temp_data_dynamics_fo_CUSTTABLE_99_")
    assert name.endswith(".parquet")
    assert "&" not in name
    # Unique per call — concurrent workers must not share one object name.
    assert volume_chunk_blob_name("CUSTTABLE", 99) != volume_chunk_blob_name(
        "CUSTTABLE", 99
    )
    assert volume_chunk_blob_name("T", 1, blob_name="custom.csv") == "custom.parquet"


def test_classify_volume_http_errors():
    not_found = classify_volume_http_error(
        404, "VOLUME_DOES_NOT_EXIST", path="/Volumes/a/b/c"
    )
    assert isinstance(not_found, VolumeNotFoundError)
    assert not_found.failure_mode == "volume_not_found"

    denied = classify_volume_http_error(
        403, "Permission denied: WRITE VOLUME", path="/Volumes/a/b/c"
    )
    assert isinstance(denied, VolumePermissionError)
    assert denied.failure_mode == "volume_insufficient_privileges"

    timed = classify_volume_http_error(504, "timeout waiting", path="/Volumes/a/b/c")
    assert isinstance(timed, VolumeUploadTimeoutError)


def test_retry_decorator_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr("utils.retry_utils.time.sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    @retry_with_exponential_backoff(max_retries=4, initial_wait=0.5, max_wait=10)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableHTTPError(503, "busy")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]


def _client(**kwargs):
    defaults = dict(
        server_hostname="dbc-test.cloud.databricks.com",
        access_token="dapi-test-token",
        catalog="main",
        schema="demo",
        volume_name="datasyncher_stage",
        max_retries=2,
        multipart_threshold_bytes=1024,
    )
    defaults.update(kwargs)
    return DatabricksVolumeStagingClient(**defaults)


def test_ensure_volume_exists_ok():
    client = _client()
    with patch.object(client, "_json_request", return_value={"name": "datasyncher_stage"}) as req:
        client.ensure_volume_exists()
        req.assert_called_once()
        assert "unity-catalog/volumes/" in req.call_args[0][1]


def test_ensure_volume_exists_raises_not_found():
    client = _client()
    with patch.object(
        client,
        "_json_request",
        side_effect=VolumeNotFoundError("missing"),
    ):
        with pytest.raises(VolumeNotFoundError):
            client.ensure_volume_exists(create_if_missing=False)


def test_ensure_volume_exists_creates_when_missing():
    client = _client()
    calls = []

    def fake_json(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and "unity-catalog/volumes/" in path:
            if len([c for c in calls if c[0] == "GET" and "volumes" in c[1]]) == 1:
                raise VolumeNotFoundError("missing")
            return {"name": client.volume_name}
        if method == "GET" and "unity-catalog/schemas/" in path:
            return {"name": client.schema}
        if method == "POST" and path == "/api/2.1/unity-catalog/volumes":
            return {"name": client.volume_name}
        return {}

    with patch.object(client, "_json_request", side_effect=fake_json):
        client.ensure_volume_exists(create_if_missing=True)

    post_volume = [
        c for c in calls if c[0] == "POST" and c[1] == "/api/2.1/unity-catalog/volumes"
    ]
    assert post_volume
    assert post_volume[0][2] == {
        "catalog_name": "main",
        "schema_name": "demo",
        "name": "datasyncher_stage",
        "volume_type": "MANAGED",
    }


def test_ensure_volume_exists_creates_schema_when_missing():
    client = _client()
    calls = []

    def fake_json(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and "unity-catalog/volumes/" in path:
            if len([c for c in calls if c[0] == "GET" and "volumes" in c[1]]) == 1:
                raise VolumeNotFoundError("missing volume")
            return {"name": client.volume_name}
        if method == "GET" and "unity-catalog/schemas/" in path:
            raise VolumeNotFoundError("missing schema")
        if method == "POST" and path == "/api/2.1/unity-catalog/schemas":
            return {"name": client.schema}
        if method == "POST" and path == "/api/2.1/unity-catalog/volumes":
            return {"name": client.volume_name}
        return {}

    with patch.object(client, "_json_request", side_effect=fake_json):
        client.ensure_volume_exists(create_if_missing=True)

    assert any(
        c[0] == "POST" and c[1] == "/api/2.1/unity-catalog/schemas"
        for c in calls
    )


def test_ensure_volume_exists_tolerates_create_race():
    client = _client()
    state = {"gets": 0}

    def fake_json(method, path, payload=None):
        if method == "GET" and "unity-catalog/volumes/" in path:
            state["gets"] += 1
            if state["gets"] == 1:
                raise VolumeNotFoundError("missing")
            return {"name": client.volume_name}
        if method == "GET" and "unity-catalog/schemas/" in path:
            return {"name": client.schema}
        if method == "POST" and path == "/api/2.1/unity-catalog/volumes":
            raise DatabricksVolumeStagingError(
                "Volume already exists",
                failure_mode="volume_staging_error",
            )
        return {}

    with patch.object(client, "_json_request", side_effect=fake_json):
        client.ensure_volume_exists(create_if_missing=True)


def test_upload_file_uses_put_under_threshold(tmp_path):
    local = tmp_path / "chunk.csv"
    local.write_text("ID,NAME\n1,A\n", encoding="utf-8")
    client = _client(multipart_threshold_bytes=10_000_000)

    with patch.object(client, "_raw_request", return_value=b"") as raw:
        path = client.upload_file(str(local), "chunk.csv", overwrite=True)

    assert path == "/Volumes/main/demo/datasyncher_stage/chunk.csv"
    method, api_path = raw.call_args[0][:2]
    assert method == "PUT"
    assert api_path.startswith(
        "/api/2.0/fs/files/Volumes/main/demo/datasyncher_stage/chunk.csv"
    )
    assert "overwrite=true" in api_path
    assert raw.call_args[1]["content_type"] == "application/octet-stream"


def test_upload_encodes_ampersand_in_custom_blob_name(tmp_path):
    local = tmp_path / "chunk.parquet"
    local.write_bytes(b"parquet")
    client = _client(multipart_threshold_bytes=10_000_000)
    blob = "temp_data_dynamics_f&o_legacy.parquet"
    with patch.object(client, "_raw_request", return_value=b"") as raw:
        client.upload_file(str(local), blob, overwrite=True)
    api_path = raw.call_args[0][1]
    assert "f%26o" in api_path
    assert "f&o" not in api_path.split("?", 1)[0]
    assert "overwrite=true" in api_path


def test_upload_file_multipart_when_over_threshold(tmp_path, monkeypatch):
    import sys

    local = tmp_path / "big.csv"
    local.write_bytes(b"x" * 2048)
    client = _client(multipart_threshold_bytes=1024)

    mock_w = MagicMock()
    mock_files = MagicMock()
    mock_w.files = mock_files
    mock_sdk = MagicMock()
    mock_sdk.WorkspaceClient = MagicMock(return_value=mock_w)

    monkeypatch.setitem(sys.modules, "databricks", MagicMock())
    monkeypatch.setitem(sys.modules, "databricks.sdk", mock_sdk)

    path = client.upload_file(str(local), "big.csv")

    assert path.endswith("/big.csv")
    mock_files.upload.assert_called_once()
    args, kwargs = mock_files.upload.call_args
    assert args[0] == "/Volumes/main/demo/datasyncher_stage/big.csv"
    assert kwargs.get("overwrite") is True


def test_upload_dataframe_writes_standard_blob_name(tmp_path, monkeypatch):
    client = _client(multipart_threshold_bytes=10_000_000)
    df = pd.DataFrame({"ID": [1], "NAME": ["A"]})
    uploaded = {}

    def fake_upload(local_path, relative_path, overwrite=True):
        uploaded["local"] = local_path
        uploaded["rel"] = relative_path
        uploaded["exists_at_call"] = os.path.exists(local_path)
        return client.absolute_path(relative_path)

    monkeypatch.setattr(client, "upload_file", fake_upload)
    out = client.upload_dataframe(df, "CUSTTABLE", 42)
    assert uploaded["rel"].startswith("temp_data_dynamics_fo_CUSTTABLE_42_")
    assert uploaded["rel"].endswith(".parquet")
    assert "&" not in uploaded["rel"]
    assert uploaded["exists_at_call"] is True
    assert str(uploaded["local"]).endswith(".parquet")
    assert out.endswith(uploaded["rel"])
    # temp file cleaned up after return
    assert not os.path.exists(uploaded["local"])


def test_dataframe_for_spark_parquet_coerces_ns_timestamps():
    df = pd.DataFrame(
        {
            "ID": [1],
            "SINK_MODIFIED_ON": pd.to_datetime(["2026-01-01T12:34:56.789123456Z"]),
        }
    )
    assert "ns" in str(df["SINK_MODIFIED_ON"].dtype)
    out = dataframe_for_spark_parquet(df)
    assert "us" in str(out["SINK_MODIFIED_ON"].dtype)


def test_upload_dataframe_parquet_has_no_timestamp_nanos(tmp_path, monkeypatch):
    """Databricks COPY INTO rejects INT64 TIMESTAMP(NANOS) from default pyarrow."""
    import pyarrow.parquet as pq

    client = _client(multipart_threshold_bytes=10_000_000)
    df = pd.DataFrame(
        {
            "ID": [1],
            "TS": pd.to_datetime(["2026-08-21T14:57:06.123456789Z"]),
        }
    )
    captured = {}

    def fake_upload(local_path, relative_path, overwrite=True):
        captured["schema"] = pq.read_schema(local_path)
        return client.absolute_path(relative_path)

    monkeypatch.setattr(client, "upload_file", fake_upload)
    client.upload_dataframe(df, "CUSTTRANS", 24)
    schema = captured["schema"]
    ts_field = schema.field("TS")
    type_str = str(ts_field.type).lower()
    assert "ns" not in type_str
    assert "us" in type_str or "micro" in type_str or "timestamp" in type_str


def test_delete_file_calls_files_api():
    client = _client()
    with patch.object(client, "_raw_request", return_value=b"") as raw:
        client.delete_absolute(
            "/Volumes/main/demo/datasyncher_stage/temp_data_dynamics_fo_CUSTTABLE_7_abc.parquet"
        )
    method, api_path = raw.call_args[0][:2]
    assert method == "DELETE"
    assert api_path.startswith("/api/2.0/fs/files/Volumes/main/demo/datasyncher_stage/")
    assert "temp_data_dynamics_fo_CUSTTABLE_7_abc.parquet" in api_path
    assert "&" not in api_path.split("?", 1)[0]


def test_put_maps_403_to_permission_error(tmp_path):
    local = tmp_path / "chunk.csv"
    local.write_text("a,b\n1,2\n", encoding="utf-8")
    client = _client(max_retries=1, multipart_threshold_bytes=10_000_000)

    from urllib.error import HTTPError
    from io import BytesIO

    err = HTTPError(
        url="https://x",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=BytesIO(b"Permission denied: WRITE VOLUME"),
    )
    with patch(
        "services.databricks.volume_staging_client.urlopen",
        side_effect=err,
    ):
        with pytest.raises(VolumePermissionError):
            client.upload_file(str(local), "chunk.csv")
