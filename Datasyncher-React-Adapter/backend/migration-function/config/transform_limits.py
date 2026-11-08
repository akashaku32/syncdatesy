"""Configurable size limits for entity mapping JSON and transform scripts."""

import os

MAX_MAPPING_JSON_MB = int(os.environ.get("MAX_MAPPING_JSON_MB", "10"))
MAX_MAPPED_COLUMNS = int(os.environ.get("MAX_MAPPED_COLUMNS", "2000"))
MAX_SCRIPT_KB = int(os.environ.get("MAX_SCRIPT_KB", "1024"))
MAX_SCRIPT_LINES = int(os.environ.get("MAX_SCRIPT_LINES", "20000"))
MAX_DRY_RUN_ROWS = int(os.environ.get("MAX_DRY_RUN_ROWS", "1000"))
DEFAULT_DRY_RUN_ROWS = int(os.environ.get("DEFAULT_DRY_RUN_ROWS", "100"))

MAX_MAPPING_JSON_BYTES = MAX_MAPPING_JSON_MB * 1024 * 1024
MAX_SCRIPT_BYTES = MAX_SCRIPT_KB * 1024

SANDBOX_TIMEOUT_SECONDS = int(os.environ.get("TRANSFORM_SANDBOX_TIMEOUT_SECONDS", "120"))
SANDBOX_MEMORY_MB = int(os.environ.get("TRANSFORM_SANDBOX_MEMORY_MB", "512"))
DRY_RUN_SAP_TIMEOUT_SECONDS = int(os.environ.get("DRY_RUN_SAP_TIMEOUT_SECONDS", "60"))
# Max OData $select fields per SAP sample request (dry-run batches above this).
DRY_RUN_SAP_SELECT_BATCH_SIZE = int(os.environ.get("DRY_RUN_SAP_SELECT_BATCH_SIZE", "50"))
# Preview tables in dry-run API responses (defaults to match DEFAULT_DRY_RUN_ROWS).
DRY_RUN_PREVIEW_ROWS = int(
    os.environ.get("DRY_RUN_PREVIEW_ROWS", str(DEFAULT_DRY_RUN_ROWS))
)
