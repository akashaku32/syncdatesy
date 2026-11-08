"""
Shared GCS / Snowflake storage settings for migration services.

Production pods must inject these via cluster secrets (see k8s/secrets/secrets-prod.yaml).
No QA/dev bucket defaults — missing env fails at import time (same pattern as Dynamics/Salesforce).
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

EnvSource = Optional[Mapping[str, Any]]


def _require_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    joined = ", ".join(names)
    raise RuntimeError(f"Missing required environment variable (one of): {joined}")


GCP_PROJECT_ID = _require_env("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT")
GCS_BUCKET_NAME = _require_env("GCS_BUCKET_NAME")
GCS_BUCKET_URL = os.environ.get("GCS_BUCKET_URL", "").strip() or f"gcs://{GCS_BUCKET_NAME}/"
SNOWFLAKE_STORAGE_INTEGRATION = _require_env(
    "SNOWFLAKE_STORAGE_INTEGRATION",
    "SF_STORAGE_INTEGRATION",
)

# Backward-compatible alias (legacy name in amazon_s3_service and docs).
SF_STORAGE_INTEGRATION = SNOWFLAKE_STORAGE_INTEGRATION


def resolve_snowflake_storage_integration(
    dest_config: EnvSource = None,
    source_config: EnvSource = None,
) -> str:
    """Per-connection override, else cluster SNOWFLAKE_STORAGE_INTEGRATION."""
    dest_config = dest_config or {}
    source_config = source_config or {}
    explicit = (
        dest_config.get("sf_storage_integration")
        or source_config.get("sf_storage_integration")
    )
    if explicit:
        return str(explicit).strip()
    return SNOWFLAKE_STORAGE_INTEGRATION


def resolve_gcs_bucket_name(
    dest_config: EnvSource = None,
    source_config: EnvSource = None,
) -> str:
    dest_config = dest_config or {}
    source_config = source_config or {}
    explicit = (
        dest_config.get("gcs_bucket_name")
        or source_config.get("gcs_bucket_name")
    )
    if explicit:
        return str(explicit).strip()
    return GCS_BUCKET_NAME


def resolve_gcp_project_id(
    dest_config: EnvSource = None,
    source_config: EnvSource = None,
) -> str:
    dest_config = dest_config or {}
    source_config = source_config or {}
    explicit = (
        dest_config.get("gcp_project_id")
        or source_config.get("gcp_project_id")
    )
    if explicit:
        return str(explicit).strip()
    return GCP_PROJECT_ID
