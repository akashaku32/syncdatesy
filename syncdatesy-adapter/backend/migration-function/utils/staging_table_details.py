"""
Deprecated: staging_table_details removed. Re-export checkpoint helpers for tests.
"""

from utils.load_checkpoint import (
    FOLDER_FMT,
    SF_MODSTAMP_CHECKPOINT_PREFIX,
    finalize_migration_record,
    get_last_synced_for_table,
    get_migration_record_info,
    get_migration_record_staging_count,
    last_synced_from_checkpoint,
    mark_migration_failed,
    normalize_load_checkpoint,
    parse_sync_datetime,
    safe_folder_checkpoint,
    safe_salesforce_modstamp_checkpoint,
    should_resume_migration,
    update_load_checkpoint,
)

# Backward-compatible aliases for tests migrating off staging_table_details.
should_resume_staging = should_resume_migration
mark_staging_failed = mark_migration_failed
clear_staging_table_details = finalize_migration_record

__all__ = [
    "FOLDER_FMT",
    "SF_MODSTAMP_CHECKPOINT_PREFIX",
    "clear_staging_table_details",
    "finalize_migration_record",
    "get_last_synced_for_table",
    "get_migration_record_info",
    "get_migration_record_staging_count",
    "last_synced_from_checkpoint",
    "mark_migration_failed",
    "mark_staging_failed",
    "normalize_load_checkpoint",
    "parse_sync_datetime",
    "safe_folder_checkpoint",
    "safe_salesforce_modstamp_checkpoint",
    "should_resume_migration",
    "should_resume_staging",
    "update_load_checkpoint",
]
