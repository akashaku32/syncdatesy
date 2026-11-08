"""
Opt-in **integration** checks for a single Dynamics/Dataverse row ID (GUID).

Use case: ID appears in Azure CSV trace (`dataverse_export_window_inspector`) but you need
an automated cross-check against Snowflake and a repeatable Azure scan.

Enable **only** when secrets and network are available (local or CI vault):

    set DYNAMICS_ID_E2E=1
    set DYNAMICS_ID_E2E_SOURCE_JSON=C:\\path\\local-source.json
    set DYNAMICS_ID_E2E_SF_JSON=C:\\path\\snowflake.json
    set DYNAMICS_ID_E2E_TABLE=LEDGERJOURNALTABLE
    set DYNAMICS_ID_E2E_SCHEMA=PUBLIC
    set DYNAMICS_ID_E2E_GUID=000058e2-0000-0000-4620-005001000000
    set DYNAMICS_ID_E2E_CONNECTION_ID=3

Optional:
    set DYNAMICS_ID_E2E_ASSERT_AZURE_HITS=1     # default: fail if inspector finds 0 rows
    set DYNAMICS_ID_E2E_ASSERT_SNOWFLAKE_ROW=1  # default: fail if COUNT(*) == 0
    set DYNAMICS_ID_E2E_LOOKUP_CALENDAR_DAY=2026-01-14   # UTC day only (with --date-dirs-from-db); use your year
    set DYNAMICS_ID_E2E_LOOKUP_MAX_DATE_DIRS=120   # newest N dirs (default 120 if unset); 0 = all; with calendar-day unset max → no cap
    set DYNAMICS_ID_E2E_INSPECTOR_TIMEOUT_SEC=0   # 0 = no subprocess timeout (default 3600)

Snowflake JSON (``DYNAMICS_ID_E2E_SF_JSON``) supports either:

- **password**: ``account``, ``user``/``username``, ``password``, ``warehouse``, ``database``, optional ``role``
- **key_pair**: ``authentication_type``: ``key_pair``, same as above but ``private_key`` (PEM string),
  optional ``passphrase``; omit ``password``

If ``schema`` is present in the JSON it is used when ``DYNAMICS_ID_E2E_SCHEMA`` is not set.

Run from ``migration-function``:

    python -m pytest tests/test_dynamics_id_e2e_crosscheck.py -v -m integration -p no:django

See ``scripts/README-dynamics-id-full-pipeline-trace.md`` (whole production vs diagnostic flow, symptom table)
and for manual deep-dives (COPY, merge, delete filter in ``snowflake_migration``).
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

pytestmark = pytest.mark.integration

MIGRATION_ROOT = Path(__file__).resolve().parent.parent


def _require_e2e() -> None:
    if os.environ.get("DYNAMICS_ID_E2E", "").strip() != "1":
        pytest.skip("Set DYNAMICS_ID_E2E=1 and required env vars (see module docstring).")


def _truthy(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


@pytest.fixture(scope="module")
def e2e_guid() -> str:
    return os.environ.get(
        "DYNAMICS_ID_E2E_GUID", "000058e2-0000-0000-4620-005001000000"
    ).strip()


@pytest.fixture(scope="module")
def e2e_table() -> str:
    t = os.environ.get("DYNAMICS_ID_E2E_TABLE", "LEDGERJOURNALTABLE").strip()
    if not t:
        pytest.fail("DYNAMICS_ID_E2E_TABLE is required when DYNAMICS_ID_E2E=1")
    return t


@pytest.fixture(scope="module")
def e2e_schema() -> str:
    return os.environ.get("DYNAMICS_ID_E2E_SCHEMA", "").strip()


def _pem_looks_encrypted(pem_str: str) -> bool:
    """True if PEM is PKCS#8 encrypted or legacy OpenSSL encrypted PEM."""
    s = (pem_str or "").strip()
    if not s:
        return False
    head = s[:400].upper()
    if "BEGIN ENCRYPTED PRIVATE KEY" in head:
        return True
    if "PROC-TYPE: 4,ENCRYPTED" in s.upper():
        return True
    return False


def _snowflake_schema_for_config(cfg: dict, env_schema: str) -> str:
    """Prefer explicit env schema; else ``schema`` from JSON; else PUBLIC."""
    es = (env_schema or "").strip()
    if es:
        return es
    cfg_schema = (cfg.get("schema") or cfg.get("SCHEMA") or "").strip()
    return cfg_schema or "PUBLIC"


def _open_snowflake_connection(cfg: dict, schema: str):
    """Password or key_pair (matches common migration ``destination_config`` shapes)."""
    snowflake = pytest.importorskip("snowflake.connector")
    user = cfg.get("user") or cfg.get("username")
    account = cfg.get("account")
    if not user or not account:
        pytest.fail("Snowflake JSON must include account and user (or username)")

    connect_kwargs: dict = {
        "user": user,
        "account": account,
        "warehouse": cfg.get("warehouse"),
        "database": cfg.get("database"),
        "schema": schema,
        "role": cfg.get("role") or None,
    }

    auth = (cfg.get("authentication_type") or cfg.get("authenticationType") or "password")
    auth = str(auth).strip().lower()

    if auth == "key_pair":
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        pk_str = cfg.get("private_key") or cfg.get("privateKey") or ""
        if not pk_str or not str(pk_str).strip():
            pytest.fail("key_pair auth requires private_key in Snowflake JSON")
        try:
            from services.snowflake.service import normalize_pem_key

            pk_str = normalize_pem_key(str(pk_str).strip())
        except Exception:
            pk_str = str(pk_str).strip().replace("\\n", "\n")
        passphrase = cfg.get("passphrase") or cfg.get("passPhrase") or ""
        pass_bytes: Optional[bytes] = None
        if _pem_looks_encrypted(pk_str) and str(passphrase).strip():
            pass_bytes = str(passphrase).encode("utf-8")
        pem = str(pk_str).strip().encode("utf-8")
        private_key = serialization.load_pem_private_key(
            pem, password=pass_bytes, backend=default_backend()
        )
        connect_kwargs["private_key"] = private_key
    else:
        password = cfg.get("password")
        if not password:
            pytest.fail("password auth requires password in Snowflake JSON (or set authentication_type to key_pair)")
        connect_kwargs["password"] = password

    return snowflake.connect(**connect_kwargs)


def test_azure_inspector_finds_guid_rows(tmp_path, e2e_guid, e2e_table):
    """
    Subprocess: ``dataverse_export_window_inspector.py`` (same code path as manual CLI).
    """
    _require_e2e()
    src = os.environ.get("DYNAMICS_ID_E2E_SOURCE_JSON", "").strip()
    if not src or not Path(src).is_file():
        pytest.skip("Set DYNAMICS_ID_E2E_SOURCE_JSON to a readable JSON file")

    out_csv = tmp_path / "id_trace_e2e.csv"
    connection_id = os.environ.get("DYNAMICS_ID_E2E_CONNECTION_ID", "3")
    cmd = [
        sys.executable,
        str(MIGRATION_ROOT / "scripts" / "dataverse_export_window_inspector.py"),
        "--source-json",
        src,
        "--connection-id",
        str(connection_id),
        "--date-dirs-from-db",
        "--table",
        e2e_table.lower(),
        "--lookup-id",
        e2e_guid,
        "--output-csv",
        str(out_csv),
    ]
    cal = os.environ.get("DYNAMICS_ID_E2E_LOOKUP_CALENDAR_DAY", "").strip()
    if cal:
        cmd.extend(["--calendar-day", cal])

    max_dirs_n = 120
    _raw = os.environ.get("DYNAMICS_ID_E2E_LOOKUP_MAX_DATE_DIRS")
    if cal and _raw is None:
        max_dirs_n = 0
    elif _raw is not None:
        s = str(_raw).strip().lower()
        if s in ("", "0", "all", "none"):
            max_dirs_n = 0
        else:
            try:
                max_dirs_n = int(s)
            except ValueError:
                pytest.fail(
                    "DYNAMICS_ID_E2E_LOOKUP_MAX_DATE_DIRS must be an integer, 0/all for full scan"
                )
    if max_dirs_n > 0:
        cmd.extend(["--lookup-max-date-dirs", str(max_dirs_n)])

    child_env = os.environ.copy()
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    child_env.setdefault("PYTHONUTF8", "1")

    timeout_sec: Optional[float] = 3600.0
    to_raw = os.environ.get("DYNAMICS_ID_E2E_INSPECTOR_TIMEOUT_SEC", "").strip()
    if to_raw:
        try:
            v = float(to_raw)
            timeout_sec = None if v <= 0 else v
        except ValueError:
            pass

    proc = subprocess.run(
        cmd,
        cwd=str(MIGRATION_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        timeout=timeout_sec,
    )
    assert proc.returncode == 0, f"inspector stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"

    if not out_csv.is_file():
        pytest.fail("Inspector did not write output CSV")

    with out_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if _truthy("DYNAMICS_ID_E2E_ASSERT_AZURE_HITS", "1"):
        assert len(rows) > 0, (
            "No Azure CSV rows for this ID in DB-listed date dirs — widen date range or "
            "fix table/connection before investigating Snowflake."
        )


def test_snowflake_row_exists_for_guid(e2e_guid, e2e_table, e2e_schema):
    """Direct Snowflake check for the physical table row."""
    _require_e2e()
    sf_path = os.environ.get("DYNAMICS_ID_E2E_SF_JSON", "").strip()
    if not sf_path or not Path(sf_path).is_file():
        pytest.skip("Set DYNAMICS_ID_E2E_SF_JSON to Snowflake credentials JSON")

    with open(sf_path, encoding="utf-8") as f:
        cfg = json.load(f)

    schema = _snowflake_schema_for_config(cfg, e2e_schema)
    conn = _open_snowflake_connection(cfg, schema)
    try:
        cur = conn.cursor()
        database = (cfg.get("database") or "").strip()
        if database:
            fq = f'"{database}"."{schema.upper()}"."{e2e_table.upper()}"'
        else:
            fq = f'"{schema.upper()}"."{e2e_table.upper()}"'
        cur.execute(f"SELECT COUNT(*) FROM {fq} WHERE \"ID\" = %s", (e2e_guid,))
        n = int(cur.fetchone()[0])
    finally:
        conn.close()

    if _truthy("DYNAMICS_ID_E2E_ASSERT_SNOWFLAKE_ROW", "1"):
        assert n >= 1, (
            "Snowflake has no row for this ID. With Azure hits confirmed, inspect worker logs "
            "for [COPY_DEBUG], [MERGE_DEBUG], and snow_migration delete filter / dedupe "
            "(see scripts/README-dynamics-id-full-pipeline-trace.md)."
        )
