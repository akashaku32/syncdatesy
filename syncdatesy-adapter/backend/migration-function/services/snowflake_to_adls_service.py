"""Compatibility shim for legacy imports.

SAP OData and older call sites import ``services.snowflake_to_adls_service``.
ADLS helpers now live under ``services.snowflake.as_source.to_adls`` (with
``MigrationFailureTracker`` from the Snowflake SFTP export helpers).
"""

from services.snowflake.as_source.to_adls import (  # noqa: F401
    _build_adls_service_client,
    _upload_file_to_adls,
    _validate_adls_destination_access,
    upload_file_to_adls,
)
from services.snowflake.as_source.to_sftp import MigrationFailureTracker  # noqa: F401

__all__ = [
    "MigrationFailureTracker",
    "_build_adls_service_client",
    "_upload_file_to_adls",
    "_validate_adls_destination_access",
    "upload_file_to_adls",
]
