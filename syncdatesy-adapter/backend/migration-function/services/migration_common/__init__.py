"""Shared migration utilities: failures, retry, registry, protocols, logging."""

from services.migration_common.bootstrap import ensure_registered
from services.migration_common.failure_tracker import MigrationFailureTracker
from services.migration_common.protocols import DestinationLoader, SourceExtractor
from services.migration_common.registry import get, get_by_function_path, register, run
from services.migration_common.retry import retry_with_exponential_backoff
from services.migration_common.table_detail_log import TableMigrationDetailFileLog

__all__ = [
    "DestinationLoader",
    "MigrationFailureTracker",
    "SourceExtractor",
    "TableMigrationDetailFileLog",
    "ensure_registered",
    "get",
    "get_by_function_path",
    "register",
    "retry_with_exponential_backoff",
    "run",
]
