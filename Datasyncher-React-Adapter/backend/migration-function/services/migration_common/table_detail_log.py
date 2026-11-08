"""Minimal JSON-lines table migration detail log."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class TableMigrationDetailFileLog:
    """Append JSON lines to a file path for per-table migration diagnostics."""

    __slots__ = ("_path", "_lock", "_fp")

    def __init__(self, path: Optional[str] = None, **context: Any) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._fp = None
        if path:
            try:
                directory = os.path.dirname(os.path.abspath(path))
                if directory:
                    os.makedirs(directory, exist_ok=True)
                self._fp = open(path, "a", encoding="utf-8", buffering=1)
                if context:
                    self.append("init", context)
            except OSError:
                self._path = None
                self._fp = None

    def enabled(self) -> bool:
        return self._fp is not None

    def append(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        if not self._fp:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **(data or {}),
        }
        line = json.dumps(record, default=str) + "\n"
        with self._lock:
            self._fp.write(line)
            self._fp.flush()

    def log(self, message: str, **extra: Any) -> None:
        self.append("log", {"message": message, **extra})

    def close(self) -> None:
        with self._lock:
            if self._fp:
                try:
                    self._fp.close()
                finally:
                    self._fp = None
