"""
Live integration test for Unity Catalog managed volume staging.

Skipped unless DATABRICKS_VOLUME_INTEGRATION=1 and the following env vars are set:

  DATABRICKS_HOST          workspace hostname (no https://)
  DATABRICKS_TOKEN         PAT / OAuth token with WRITE VOLUME + SQL warehouse access
  DATABRICKS_HTTP_PATH     SQL warehouse HTTP path
  DATABRICKS_CATALOG
  DATABRICKS_SCHEMA
  DATABRICKS_VOLUME        existing managed volume name (or leave empty to create)

Run:
  DATABRICKS_VOLUME_INTEGRATION=1 pytest -m integration \\
    tests/test_databricks_volume_staging_integration.py
"""

from __future__ import annotations

import os
import uuid

import pandas as pd
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("DATABRICKS_VOLUME_INTEGRATION") != "1",
        reason="Set DATABRICKS_VOLUME_INTEGRATION=1 with live Databricks credentials",
    ),
]


def _require_env(*keys: str) -> dict[str, str]:
    missing = [k for k in keys if not str(os.getenv(k) or "").strip()]
    if missing:
        pytest.skip(f"Missing required env for live test: {', '.join(missing)}")
    return {k: os.environ[k].strip() for k in keys}


def test_managed_volume_upload_copy_into_row_count():
    from datasyncher_connections.databricks.connector import (
        create_databricks_sqlalchemy_like_engine,
    )
    from services.databricks.load_helpers import _copy_into_staging
    from services.databricks.volume_staging_client import DatabricksVolumeStagingClient

    env = _require_env(
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_HTTP_PATH",
        "DATABRICKS_CATALOG",
        "DATABRICKS_SCHEMA",
    )
    volume = (os.getenv("DATABRICKS_VOLUME") or "datasyncher_integration_stage").strip()
    catalog = env["DATABRICKS_CATALOG"]
    schema = env["DATABRICKS_SCHEMA"]
    suffix = uuid.uuid4().hex[:8]
    scratch_table = f"`{catalog}`.`{schema}`.`ds_vol_it_{suffix}`"
    relative = f"integration/chunk_{suffix}.csv"

    client = DatabricksVolumeStagingClient(
        server_hostname=env["DATABRICKS_HOST"],
        access_token=env["DATABRICKS_TOKEN"],
        catalog=catalog,
        schema=schema,
        volume_name=volume,
    )

    # Create volume if missing (best-effort; skip create errors when it already exists).
    try:
        client.ensure_volume_exists()
    except Exception:
        engine_boot, _ = create_databricks_sqlalchemy_like_engine(
            {
                "server_hostname": env["DATABRICKS_HOST"],
                "http_path": env["DATABRICKS_HTTP_PATH"],
                "access_token": env["DATABRICKS_TOKEN"],
                "catalog": catalog,
                "schema": schema,
            },
            ensure_schema=True,
        )
        with engine_boot.connect() as conn:
            conn.execute(
                f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{schema}`.`{volume}`"
            )
        client.ensure_volume_exists()

    df = pd.DataFrame({"ID": [1, 2, 3], "NAME": ["a", "b", "c"]})
    import csv
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
    ) as tmp:
        df.to_csv(tmp, index=False, quoting=csv.QUOTE_NONNUMERIC)
        local_path = tmp.name

    try:
        volume_file = client.upload_file(local_path, relative, overwrite=True)
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass

    engine, _ = create_databricks_sqlalchemy_like_engine(
        {
            "server_hostname": env["DATABRICKS_HOST"],
            "http_path": env["DATABRICKS_HTTP_PATH"],
            "access_token": env["DATABRICKS_TOKEN"],
            "catalog": catalog,
            "schema": schema,
        },
        ensure_schema=True,
    )

    try:
        with engine.connect() as conn:
            conn.execute(
                f"CREATE OR REPLACE TABLE {scratch_table} "
                "(ID BIGINT, NAME STRING) USING DELTA"
            )
        _copy_into_staging(engine, scratch_table, volume_file)
        with engine.connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {scratch_table}").fetchone()
        count = int(row[0] if not isinstance(row, dict) else row["c"])
        assert count == 3
    finally:
        try:
            client.delete_file(relative)
        except Exception:
            pass
        try:
            with engine.connect() as conn:
                conn.execute(f"DROP TABLE IF EXISTS {scratch_table}")
        except Exception:
            pass
