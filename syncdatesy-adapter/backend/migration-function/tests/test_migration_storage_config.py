import os

import pytest

from utils import migration_storage_config as cfg


def test_resolve_snowflake_storage_integration_prefers_connection_config():
    got = cfg.resolve_snowflake_storage_integration(
        dest_config={"sf_storage_integration": "CUSTOM_INT"},
        source_config={"sf_storage_integration": "OTHER"},
    )
    assert got == "CUSTOM_INT"


def test_resolve_snowflake_storage_integration_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_STORAGE_INTEGRATION", "DATASYNCHERKUB_PROD")
    assert cfg.resolve_snowflake_storage_integration() == "DATASYNCHERKUB_PROD"


def test_resolve_gcs_bucket_name_prefers_connection_config():
    got = cfg.resolve_gcs_bucket_name(
        dest_config={"gcs_bucket_name": "conn-bucket"},
    )
    assert got == "conn-bucket"


def test_require_env_raises_when_all_missing(monkeypatch):
    for key in (
        "SNOWFLAKE_STORAGE_INTEGRATION",
        "SF_STORAGE_INTEGRATION",
        "GCS_BUCKET_NAME",
        "GCP_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="GCS_BUCKET_NAME"):
        cfg._require_env("GCS_BUCKET_NAME")
