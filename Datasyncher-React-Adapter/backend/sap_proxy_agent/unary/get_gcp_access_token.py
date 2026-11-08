"""
GCP downscoped access token for on-prem agents (ADLS / GCS objectUser on a bucket prefix).

Configure via environment (no secrets in source):
  PROXY_AGENT_GCP_PROJECT
  PROXY_AGENT_GCS_BUCKET
  PROXY_AGENT_GCS_OBJECT_PREFIX  (optional, default "")
"""

from __future__ import annotations

import logging
import os

import google.auth
from google.auth import downscoped
from google.auth.credentials import with_scopes_if_required
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

CLOUD_PLATFORM_SCOPE = ("https://www.googleapis.com/auth/cloud-platform",)


def generate_downscoped_token() -> tuple[str, object]:
    project_id = (os.environ.get("PROXY_AGENT_GCP_PROJECT") or "").strip()
    bucket_name = (os.environ.get("PROXY_AGENT_GCS_BUCKET") or "").strip()
    object_prefix = (os.environ.get("PROXY_AGENT_GCS_OBJECT_PREFIX") or "").strip()

    if not project_id or not bucket_name:
        raise RuntimeError(
            "PROXY_AGENT_GCP_PROJECT and PROXY_AGENT_GCS_BUCKET must be set."
        )

    source_credentials, _ = google.auth.default(quota_project_id=project_id)
    source_credentials = with_scopes_if_required(
        source_credentials,
        list(CLOUD_PLATFORM_SCOPE),
    )

    available_resource = f"//storage.googleapis.com/projects/_/buckets/{bucket_name}"
    available_permissions = ["inRole:roles/storage.objectUser"]
    expression = (
        "resource.name.startsWith("
        f"'projects/_/buckets/{bucket_name}/objects/{object_prefix}'"
        ")"
    )
    condition = downscoped.AvailabilityCondition(expression=expression)
    rule = downscoped.AccessBoundaryRule(
        available_resource=available_resource,
        available_permissions=available_permissions,
        availability_condition=condition,
    )
    access_boundary = downscoped.CredentialAccessBoundary(rules=[rule])
    credentials = downscoped.Credentials(
        source_credentials=source_credentials,
        credential_access_boundary=access_boundary,
    )
    credentials.refresh(Request())
    logger.info(
        "Issued downscoped GCP token for bucket=%s prefix=%r",
        bucket_name,
        object_prefix,
    )
    return credentials.token, credentials.expiry
