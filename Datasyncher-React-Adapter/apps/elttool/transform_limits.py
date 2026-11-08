"""Configurable size limits for entity mapping JSON and transform scripts."""

import os

MAX_MAPPING_JSON_MB = int(os.getenv("MAX_MAPPING_JSON_MB", "10"))
MAX_MAPPED_COLUMNS = int(os.getenv("MAX_MAPPED_COLUMNS", "2000"))
MAX_SCRIPT_KB = int(os.getenv("MAX_SCRIPT_KB", "1024"))
MAX_SCRIPT_LINES = int(os.getenv("MAX_SCRIPT_LINES", "20000"))
MAX_DRY_RUN_ROWS = int(os.getenv("MAX_DRY_RUN_ROWS", "1000"))
DEFAULT_DRY_RUN_ROWS = int(os.getenv("DEFAULT_DRY_RUN_ROWS", "100"))

MAX_MAPPING_JSON_BYTES = MAX_MAPPING_JSON_MB * 1024 * 1024
MAX_SCRIPT_BYTES = MAX_SCRIPT_KB * 1024
