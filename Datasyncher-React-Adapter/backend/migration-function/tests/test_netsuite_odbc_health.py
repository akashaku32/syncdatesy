"""NetSuite ODBC health helper (no driver required in CI)."""

import sys
from pathlib import Path

MIGRATION_ROOT = Path(__file__).resolve().parents[1]
if str(MIGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(MIGRATION_ROOT))

from utils.netsuite_odbc_health import netsuite_odbc_health_payload, netsuite_odbc_ready


def test_odbc_health_when_not_installed(monkeypatch):
    monkeypatch.delenv("NETSUITE_ODBC_INSTALLED", raising=False)
    assert netsuite_odbc_ready() is True
    payload = netsuite_odbc_health_payload()
    assert "pyodbc" in payload
