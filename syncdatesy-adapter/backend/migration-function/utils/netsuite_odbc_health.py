"""Runtime checks for NetSuite SuiteAnalytics ODBC (optional image feature)."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List


def netsuite_odbc_health_payload() -> Dict[str, Any]:
    installed_flag = str(os.environ.get("NETSUITE_ODBC_INSTALLED", "0")).strip() == "1"
    marker_exists = os.path.isfile("/etc/netsuite-odbc-installed")

    payload: Dict[str, Any] = {
        "netsuite_odbc_installed": installed_flag or marker_exists,
        "pyodbc": None,
        "odbc_drivers": [],
        "driver_env": os.environ.get("NETSUITE_ODBC_DRIVER"),
        "truststore_env": os.environ.get("NETSUITE_ODBC_TRUSTSTORE"),
    }

    try:
        import pyodbc  # noqa: F401

        payload["pyodbc"] = "ok"
    except ImportError as exc:
        payload["pyodbc"] = f"missing: {exc}"
        return payload

    try:
        completed = subprocess.run(
            ["odbcinst", "-q", "-d"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        drivers: List[str] = [
            line.strip("[]").strip()
            for line in (completed.stdout or "").splitlines()
            if line.strip()
        ]
        payload["odbc_drivers"] = drivers
        if completed.returncode != 0 and completed.stderr:
            payload["odbcinst_stderr"] = completed.stderr.strip()[:500]
    except FileNotFoundError:
        payload["odbcinst"] = "odbcinst not found"
    except subprocess.TimeoutExpired:
        payload["odbcinst"] = "timeout"

    return payload


def netsuite_odbc_ready() -> bool:
    if str(os.environ.get("NETSUITE_ODBC_INSTALLED", "0")).strip() != "1":
        return True
    data = netsuite_odbc_health_payload()
    if data.get("pyodbc") != "ok":
        return False
    drivers = data.get("odbc_drivers") or []
    if not drivers:
        return False
    expected = (os.environ.get("NETSUITE_ODBC_DRIVER") or "").strip("{}")
    if expected:
        return any(expected.lower() in d.lower() for d in drivers)
    return True
