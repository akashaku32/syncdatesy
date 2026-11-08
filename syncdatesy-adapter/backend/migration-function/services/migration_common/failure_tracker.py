"""Shared migration failure tracking for per-table/object error collection."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


class MigrationFailureTracker:
    """
    Tracks failed tables/objects during migration for consolidated reporting.

    Each failure is stored as a dict with ``object``, ``error``, and ``timestamp``.
    Legacy keys ``table_name``, ``error_message``, and ``failure_time`` are also set
    for compatibility with existing email and summary helpers.
    """

    def __init__(self) -> None:
        self.failed_tables: List[Dict[str, Any]] = []
        self.migration_start_time = datetime.now(timezone.utc)
        self._lock = threading.Lock()

    def add_failure(
        self,
        table_name: str,
        error_message: str,
        service_name: str = "",
        *,
        object_name: Optional[str] = None,
    ) -> None:
        name = str(object_name if object_name is not None else table_name or "").strip()
        if not name:
            return
        failure_time = datetime.now(timezone.utc)
        failure = {
            "object": name,
            "table_name": name,
            "error": str(error_message),
            "error_message": str(error_message),
            "timestamp": failure_time,
            "failure_time": failure_time,
            "service_name": service_name,
        }
        with self._lock:
            for item in self.failed_tables:
                if str(item.get("object", item.get("table_name", ""))).strip().upper() == name.upper():
                    item.update(failure)
                    return
            self.failed_tables.append(failure)
        logger.info("Added failure for object %r to migration tracker", name)

    def has_failures(self) -> bool:
        with self._lock:
            return len(self.failed_tables) > 0

    def get_failure_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_failures": len(self.failed_tables),
                "failed_tables": list(self.failed_tables),
                "migration_start_time": self.migration_start_time,
            }

    @staticmethod
    def failed_table_names(failed_tables: Optional[Iterable[Any]]) -> Set[str]:
        """Extract object/table names from tracker entries (dict or str)."""
        names: Set[str] = set()
        for item in failed_tables or []:
            if isinstance(item, dict):
                name = item.get("object") or item.get("table_name")
            else:
                name = item
            if name:
                names.add(str(name))
        return names
