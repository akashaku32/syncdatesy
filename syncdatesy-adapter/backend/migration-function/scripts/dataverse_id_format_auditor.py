#!/usr/bin/env python3
"""
Scan Dataverse Azure Blob CSV exports for rows whose primary ID column is not a normal GUID.

Expected ID shape (same as healthy ledger exports):
  000058e2-0000-0000-4620-005001000000  (36 chars, 8-4-4-4-12 hex with hyphens)

Writes per-table reports under scripts/dataverse_id_audit_reports/ by default.
Runs up to --parallel tables at once (default 10). No row cap unless --max-rows-per-table is set.

Examples:
  python scripts/dataverse_id_format_auditor.py --source-json scripts/source.json \\
    --connection-id 3 --date-dirs-from-db --tables-from-db \\
    --start "2026-01-01T00.00.00Z" --parallel 10

  # Latest export folder only (much faster):
  python scripts/dataverse_id_format_auditor.py ... --max-date-dirs 1
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
from urllib.parse import unquote, urlparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from azure.storage.blob import BlobServiceClient

_SCRIPTS_DIR = Path(__file__).resolve().parent
_MIGRATION_ROOT = _SCRIPTS_DIR.parent
_DEFAULT_OUTPUT_ROOT = _SCRIPTS_DIR / "dataverse_id_audit_reports"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_MIGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_MIGRATION_ROOT))

import dataverse_export_window_inspector as dvi  # noqa: E402

GUID_HYPHEN_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
GUID_BARE_RE = re.compile(r"^[0-9a-fA-F]{32}$")

_PRINT_LOCK = threading.Lock()

# Extra columns copied into anomaly samples when present (migration-style names).
_PREVIEW_COLS = (
    "RECID",
    "DATAAREAID",
    "JOURNALNUM",
    "JOURNALNAME",
    "SINK_CREATED_ON",
    "SINK_MODIFIED_ON",
    "IS_DELETE",
    "ISDELETE",
    "NAME",
    "ACCOUNTNUM",
    "VENDORACCOUNT",
    "CUSTACCOUNT",
)


@dataclass(frozen=True)
class IdClassification:
    is_valid: bool
    anomaly_type: str


def classify_id_value(
    raw: Any,
    *,
    max_valid_len: int = 36,
    max_scan_len: int = 4096,
) -> IdClassification:
    if raw is None:
        return IdClassification(False, "empty")
    text = str(raw).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return IdClassification(False, "empty")
    if len(text) > max_scan_len:
        return IdClassification(False, "too_long")
    if text != str(raw).strip() or any(c.isspace() for c in text):
        return IdClassification(False, "whitespace")
    if GUID_HYPHEN_RE.match(text) or GUID_BARE_RE.match(text):
        return IdClassification(True, "valid_guid")
    if len(text) > max_valid_len:
        return IdClassification(False, "too_long")
    if re.search(r"[^0-9a-fA-F\-]", text):
        return IdClassification(False, "non_hex_chars")
    return IdClassification(False, "not_guid")


def _log(msg: str) -> None:
    with _PRINT_LOCK:
        print(msg, flush=True)


def _apply_database_url_to_env(database_url: str) -> None:
    """Map postgresql://user:pass@host:port/dbname into DB_* env vars."""
    parsed = urlparse((database_url or "").strip())
    if parsed.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"Unsupported database URL scheme: {parsed.scheme!r}")
    if parsed.hostname:
        os.environ.setdefault("DB_HOST", parsed.hostname)
    if parsed.port:
        os.environ.setdefault("DB_PORT", str(parsed.port))
    if parsed.username:
        os.environ.setdefault("DB_USER", unquote(parsed.username))
    if parsed.password is not None:
        os.environ.setdefault("DB_PASSWORD", unquote(parsed.password))
    dbname = (parsed.path or "").lstrip("/")
    if dbname:
        os.environ.setdefault("DB_NAME", dbname)


def _ensure_postgres_env(database_url: Optional[str] = None) -> None:
    """
    postgres_connection() needs DB_HOST, DB_USER, DB_PASSWORD, DB_NAME.
    Also accept DATABASE_URL / FASTAPI_DATABASE_URL (same as k8s secrets).
    """
    if all(os.environ.get(k) for k in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")):
        return
    url = (
        (database_url or "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
        or os.environ.get("FASTAPI_DATABASE_URL", "").strip()
    )
    if url:
        _apply_database_url_to_env(url)
    if all(os.environ.get(k) for k in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")):
        return
    raise ValueError(
        "Postgres credentials missing. Set either:\n"
        "  DB_HOST, DB_USER, DB_PASSWORD, DB_NAME\n"
        "or:\n"
        "  DATABASE_URL=postgresql://user:pass@host:5432/dbname\n"
        "or pass --database-url (same as k8s DATABASE_URL in secrets.yaml).\n"
        "PowerShell example:\n"
        '  $env:DATABASE_URL = "postgresql://USER:PASS@HOST:5432/DBNAME"'
    )


def _fetch_tables_from_db(connection_id: int, selected_only: bool = True) -> List[str]:
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cursor = conn.cursor()
        if selected_only:
            cursor.execute(
                '''
                SELECT "table" FROM source_table_selection
                WHERE connection_id = %s AND COALESCE(selected, true) = true
                ORDER BY "table"
                ''',
                (connection_id,),
            )
        else:
            cursor.execute(
                '''
                SELECT "table" FROM source_table_selection
                WHERE connection_id = %s
                ORDER BY "table"
                ''',
                (connection_id,),
            )
        return [str(row[0]).strip() for row in cursor.fetchall() if row and row[0]]
    finally:
        conn.close()


def _parse_tables_arg(tables_csv: Optional[str], tables_list: List[str]) -> List[str]:
    names: List[str] = []
    if tables_csv:
        names.extend(t.strip() for part in tables_csv.split(",") for t in part.split() if t.strip())
    for t in tables_list:
        t = (t or "").strip()
        if t:
            names.append(t)
    seen: Set[str] = set()
    out: List[str] = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _resolve_tables(args, cfg: Optional[Dict[str, Any]]) -> Tuple[List[str], int]:
    tables = _parse_tables_arg(getattr(args, "tables", None), getattr(args, "table", []) or [])
    if getattr(args, "tables_from_db", False):
        connection_id = dvi._resolve_connection_id(args, cfg)
        if connection_id is None:
            print(
                "--tables-from-db requires --connection-id "
                "(or connection_id in --source-json / DATASYNCHER_CONNECTION_ID)",
                file=sys.stderr,
            )
            return [], 2
        db_tables = _fetch_tables_from_db(connection_id, selected_only=not getattr(args, "all_db_tables", False))
        if tables:
            allowed = {t.lower() for t in tables}
            db_tables = [t for t in db_tables if t.lower() in allowed]
        tables = db_tables
    if not tables:
        print("Provide --tables (comma-separated) and/or --tables-from-db.", file=sys.stderr)
        return [], 2
    return tables, 0


def _pick_date_dirs(date_dirs: List[str], args) -> List[str]:
    if not date_dirs:
        return []
    if getattr(args, "all_date_dirs", False):
        return list(date_dirs)
    max_dirs = getattr(args, "max_date_dirs", None)
    if max_dirs is None:
        if getattr(args, "start", None) or getattr(args, "end", None):
            return list(date_dirs)
        max_dirs = 1
    max_dirs = max(1, int(max_dirs))
    return list(date_dirs[-max_dirs:])


def _row_preview_dict(row, id_col: str, max_cols: int = 6) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for col in _PREVIEW_COLS:
        if col == id_col or col not in row.index:
            continue
        val = row[col]
        if val is None or (isinstance(val, float) and str(val) == "nan"):
            continue
        text = str(val).strip()
        if not text:
            continue
        out[col] = text[:200] if len(text) <= 200 else text[:197] + "..."
        if len(out) >= max_cols:
            break
    if not out:
        for col in row.index:
            if col == id_col or str(col).startswith("_"):
                continue
            val = row[col]
            if val is None:
                continue
            text = str(val).strip()
            if text and text.lower() not in ("nan", "none"):
                out[str(col)] = text[:120]
            if len(out) >= max_cols:
                break
    return out


def _scan_csv_for_id_anomalies(
    container,
    blob_name: str,
    attribute_names: List[str],
    *,
    chunk_size: int,
    max_rows: Optional[int],
    max_samples: Optional[int],
    meta: Dict[str, Any],
    table: str,
    seen_values: Set[str],
    distinct_id_values: Set[str],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    import pandas as pd

    unlimited_rows = max_rows is None or max_rows <= 0
    unlimited_samples = max_samples is None or max_samples <= 0

    stats: Dict[str, Any] = {
        "rows_scanned": 0,
        "valid_guid_rows": 0,
        "anomaly_rows": 0,
        "anomaly_types": Counter(),
        "id_column": None,
    }
    samples: List[Dict[str, Any]] = []

    column_converters = {col: str for col in attribute_names}
    bc = container.get_blob_client(blob_name)
    data = bc.download_blob().readall()
    col_names = dvi._migration_style_column_names(attribute_names)
    id_col = dvi._resolve_id_column(col_names, attribute_names)
    stats["id_column"] = id_col

    try:
        for encoding in (None, "ISO-8859-1"):
            try:
                bio = io.BytesIO(data)
                kwargs: Dict[str, Any] = {
                    "header": None,
                    "names": attribute_names,
                    "chunksize": chunk_size,
                    "low_memory": True,
                    "converters": column_converters,
                }
                if encoding:
                    kwargs["encoding"] = encoding
                for chunk in pd.read_csv(bio, **kwargs):
                    if chunk.empty:
                        continue
                    chunk.columns = col_names
                    if not unlimited_rows and stats["rows_scanned"] >= max_rows:
                        break
                    if not unlimited_rows:
                        remaining = max_rows - stats["rows_scanned"]
                        if len(chunk.index) > remaining:
                            chunk = chunk.head(remaining)

                    id_series = chunk[id_col].astype(str)
                    for idx, raw in id_series.items():
                        stats["rows_scanned"] += 1
                        classification = classify_id_value(raw)
                        norm = str(raw).strip() if raw is not None else ""
                        if norm and norm.lower() not in ("nan", "none", "null"):
                            distinct_id_values.add(norm)
                        if classification.is_valid:
                            stats["valid_guid_rows"] += 1
                            continue
                        stats["anomaly_rows"] += 1
                        stats["anomaly_types"][classification.anomaly_type] += 1
                        if not unlimited_samples and len(samples) >= max_samples:
                            continue
                        value_key = norm[:200]
                        if value_key in seen_values and classification.anomaly_type != "empty":
                            continue
                        seen_values.add(value_key)
                        preview = norm if len(norm) <= 500 else norm[:497] + "..."
                        row_data = chunk.loc[idx]
                        preview_fields = _row_preview_dict(row_data, id_col)
                        samples.append(
                            {
                                "TABLE": table,
                                "DATE_DIRECTORY": meta["date_dir"],
                                "CSV_FILE_NAME": meta["basename"],
                                "BLOB_PATH": blob_name,
                                "ID_COLUMN": id_col,
                                "ID_VALUE": preview,
                                "ID_LENGTH": len(norm),
                                "ANOMALY_TYPE": classification.anomaly_type,
                                "ROW_INDEX": int(idx) if isinstance(idx, (int, float)) else str(idx),
                                "SAMPLE_ROW_JSON": json.dumps(preview_fields, ensure_ascii=False),
                            }
                        )
                    if not unlimited_rows and stats["rows_scanned"] >= max_rows:
                        break
                break
            except UnicodeDecodeError:
                if encoding is None:
                    continue
                raise
    finally:
        del data

    return stats, samples


def audit_table_ids(
    container,
    base_folder: str,
    table: str,
    date_dirs: List[str],
    audit_opts: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    chunk_size = int(audit_opts["chunk_size"])
    max_rows = audit_opts.get("max_rows_per_table")
    max_samples = audit_opts.get("max_samples_per_table")
    scan_all_csvs = bool(audit_opts.get("scan_all_csvs", True))
    include_delete_shards = bool(audit_opts.get("include_delete_csvs", False))
    no_reuse_model = bool(audit_opts.get("no_reuse_model_schema", False))

    unlimited_rows = max_rows is None or int(max_rows) <= 0

    fallback_names: Optional[List[str]] = None
    fallback_from: Optional[str] = None
    if not no_reuse_model and date_dirs:
        fallback_names, fallback_from = dvi._find_fallback_model_attribute_names(
            container, base_folder, table, date_dirs
        )

    combined_stats: Dict[str, Any] = {
        "TABLE": table,
        "date_dirs_scanned": 0,
        "csv_files_scanned": 0,
        "rows_scanned": 0,
        "distinct_ids": 0,
        "valid_guid_rows": 0,
        "anomaly_rows": 0,
        "anomaly_types": Counter(),
        "id_column": None,
        "fallback_model_from": fallback_from,
        "status": "ok",
        "error": None,
        "has_issues": False,
    }
    all_samples: List[Dict[str, Any]] = []
    seen_values: Set[str] = set()
    distinct_id_values: Set[str] = set()

    for date_dir in date_dirs:
        try:
            attribute_names = dvi._load_model_attribute_names(container, base_folder, date_dir, table)
        except Exception:
            if fallback_names:
                attribute_names = fallback_names
            else:
                if combined_stats["status"] == "ok":
                    combined_stats["status"] = "no_model"
                    combined_stats["error"] = f"no model.json for {date_dir}"
                continue

        csv_blobs = dvi._list_csv_blobs_migration_order(container, base_folder, date_dir, table)
        if not csv_blobs:
            continue
        combined_stats["date_dirs_scanned"] += 1

        to_scan = csv_blobs
        if not include_delete_shards:
            non_del = [b for b in csv_blobs if not b.get("is_delete_csv")]
            if non_del:
                to_scan = non_del
        if not scan_all_csvs:
            to_scan = to_scan[:1]

        for blob_info in to_scan:
            if not unlimited_rows and combined_stats["rows_scanned"] >= int(max_rows):
                break
            rows_left = None if unlimited_rows else int(max_rows) - combined_stats["rows_scanned"]
            if rows_left is not None and rows_left <= 0:
                break
            meta = {"date_dir": date_dir, "basename": blob_info["basename"]}
            try:
                stats, samples = _scan_csv_for_id_anomalies(
                    container,
                    blob_info["name"],
                    attribute_names,
                    chunk_size=chunk_size,
                    max_rows=rows_left,
                    max_samples=(
                        None
                        if max_samples is None or int(max_samples) <= 0
                        else int(max_samples) - len(all_samples)
                    ),
                    meta=meta,
                    table=table,
                    seen_values=seen_values,
                    distinct_id_values=distinct_id_values,
                )
            except Exception as e:
                combined_stats["status"] = "read_error"
                combined_stats["error"] = str(e)
                _log(f"  [{table}] error {blob_info['basename']}: {e}")
                continue

            combined_stats["csv_files_scanned"] += 1
            combined_stats["rows_scanned"] += stats["rows_scanned"]
            combined_stats["valid_guid_rows"] += stats["valid_guid_rows"]
            combined_stats["anomaly_rows"] += stats["anomaly_rows"]
            combined_stats["anomaly_types"].update(stats["anomaly_types"])
            if stats.get("id_column") and not combined_stats["id_column"]:
                combined_stats["id_column"] = stats["id_column"]
            all_samples.extend(samples)

        if not unlimited_rows and combined_stats["rows_scanned"] >= int(max_rows):
            break

    combined_stats["distinct_ids"] = len(distinct_id_values)
    combined_stats["has_issues"] = combined_stats["anomaly_rows"] > 0
    return combined_stats, all_samples


def _summary_to_row(summary: Dict[str, Any]) -> Dict[str, Any]:
    types = summary.get("anomaly_types") or Counter()
    return {
        "TABLE": summary["TABLE"],
        "STATUS": summary.get("status"),
        "HAS_ISSUES": summary.get("has_issues", False),
        "ERROR": summary.get("error"),
        "DATE_DIRS_SCANNED": summary.get("date_dirs_scanned"),
        "CSV_FILES_SCANNED": summary.get("csv_files_scanned"),
        "ROWS_SCANNED": summary.get("rows_scanned"),
        "DISTINCT_IDS": summary.get("distinct_ids"),
        "VALID_GUID_ROWS": summary.get("valid_guid_rows"),
        "ANOMALY_ROWS": summary.get("anomaly_rows"),
        "ID_COLUMN": summary.get("id_column"),
        "FALLBACK_MODEL_FROM": summary.get("fallback_model_from"),
        "ANOMALY_TYPES": ";".join(f"{k}:{v}" for k, v in types.most_common()),
    }


def _write_table_outputs(
    table_dir: Path,
    summary: Dict[str, Any],
    samples: List[Dict[str, Any]],
) -> None:
    import pandas as pd

    table_dir.mkdir(parents=True, exist_ok=True)
    types = summary.get("anomaly_types") or Counter()
    summary_export = dict(_summary_to_row(summary))
    summary_export["ANOMALY_TYPES_DICT"] = dict(types)

    (table_dir / "summary.json").write_text(
        json.dumps(summary_export, indent=2, default=str),
        encoding="utf-8",
    )
    pd.DataFrame([_summary_to_row(summary)]).to_csv(table_dir / "summary.csv", index=False)

    lines = [
        f"Table: {summary['TABLE']}",
        f"Status: {summary.get('status')}",
        f"Has non-GUID IDs: {summary.get('has_issues')}",
        f"Rows scanned: {summary.get('rows_scanned')}",
        f"Valid GUID rows: {summary.get('valid_guid_rows')}",
        f"Anomaly rows: {summary.get('anomaly_rows')}",
        f"Distinct IDs: {summary.get('distinct_ids')}",
        f"ID column: {summary.get('id_column')}",
        f"Date dirs scanned: {summary.get('date_dirs_scanned')}",
        f"CSV files scanned: {summary.get('csv_files_scanned')}",
        "",
    ]
    if types:
        lines.append("Anomaly breakdown:")
        for k, v in types.most_common():
            lines.append(f"  {k}: {v}")
        lines.append("")
    if samples:
        lines.append("Sample non-GUID rows (date directory | csv file | id value | type):")
        for s in samples[:30]:
            lines.append(
                f"  {s['DATE_DIRECTORY']} | {s['CSV_FILE_NAME']} | "
                f"{s['ANOMALY_TYPE']} | len={s['ID_LENGTH']} | {s['ID_VALUE']!r}"
            )
            if s.get("SAMPLE_ROW_JSON"):
                lines.append(f"    row: {s['SAMPLE_ROW_JSON']}")
        if len(samples) > 30:
            lines.append(f"  ... {len(samples) - 30} more in anomalies.csv")
    elif summary.get("anomaly_rows", 0) == 0:
        lines.append("No non-GUID ID values found in scanned data.")
    (table_dir / "report.txt").write_text("\n".join(lines), encoding="utf-8")

    if samples:
        pd.DataFrame(samples).to_csv(table_dir / "anomalies.csv", index=False)


def _audit_table_worker(
    table: str,
    connection_string: str,
    container_name: str,
    base_folder: str,
    scan_dirs: List[str],
    audit_opts: Dict[str, Any],
    output_dir: Path,
) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    svc = BlobServiceClient.from_connection_string(connection_string)
    container = svc.get_container_client(container_name)
    _log(f"[START] {table} ({len(scan_dirs)} date dir(s))")
    try:
        summary, samples = audit_table_ids(
            container, base_folder or "", table, scan_dirs, audit_opts
        )
        table_dir = output_dir / "tables" / table
        _write_table_outputs(table_dir, summary, samples)
        issue = "ISSUES" if summary.get("has_issues") else "OK"
        _log(
            f"[{issue}] {table} rows={summary.get('rows_scanned')} "
            f"anomalies={summary.get('anomaly_rows')}"
        )
        return table, summary, samples
    except Exception as e:
        _log(f"[FAIL] {table}: {e}")
        summary = {
            "TABLE": table,
            "status": "failed",
            "error": str(e),
            "has_issues": False,
            "rows_scanned": 0,
            "anomaly_rows": 0,
            "anomaly_types": Counter(),
        }
        table_dir = output_dir / "tables" / table
        table_dir.mkdir(parents=True, exist_ok=True)
        (table_dir / "report.txt").write_text(f"FAILED: {e}\n", encoding="utf-8")
        return table, summary, []


def _make_run_output_dir(args, connection_id: Optional[int]) -> Path:
    if args.output_dir:
        return Path(args.output_dir).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cid = connection_id if connection_id is not None else "unknown"
    return (_DEFAULT_OUTPUT_ROOT / f"conn{cid}_{stamp}").resolve()


def _write_overall_report(
    output_dir: Path,
    *,
    connection_id: Optional[int],
    container_name: str,
    base_folder: str,
    dir_source: str,
    scan_dirs: List[str],
    tables: List[str],
    all_summaries: List[Dict[str, Any]],
    all_samples: List[Dict[str, Any]],
    audit_opts: Dict[str, Any],
) -> None:
    import pandas as pd

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [_summary_to_row(s) for s in all_summaries]
    pd.DataFrame(rows).to_csv(output_dir / "overall_summary.csv", index=False)
    if all_samples:
        pd.DataFrame(all_samples).to_csv(output_dir / "overall_anomalies.csv", index=False)

    issues = [s["TABLE"] for s in all_summaries if s.get("has_issues")]
    ok_tables = [s["TABLE"] for s in all_summaries if s.get("status") == "ok" and not s.get("has_issues")]
    failed = [s["TABLE"] for s in all_summaries if s.get("status") not in (None, "ok")]

    lines = [
        "Dataverse ID format audit — overall report",
        f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"Connection ID: {connection_id}",
        f"Container: {container_name}",
        f"Base folder: {base_folder!r}",
        f"Date dir source: {dir_source}",
        f"Date directories scanned: {len(scan_dirs)}",
        f"Tables: {len(tables)}",
        f"Parallel workers: {audit_opts.get('parallel')}",
        f"Max rows per table: {audit_opts.get('max_rows_per_table') or 'unlimited'}",
        f"Scan all CSV shards per folder: {audit_opts.get('scan_all_csvs')}",
        "",
        f"Tables with non-GUID IDs ({len(issues)}):",
    ]
    if issues:
        for t in issues:
            s = next(x for x in all_summaries if x["TABLE"] == t)
            types = s.get("anomaly_types") or Counter()
            lines.append(
                f"  - {t}: {s.get('anomaly_rows')} anomaly row(s) "
                f"({'; '.join(f'{k}={v}' for k, v in types.most_common(5))})"
            )
            lines.append(f"    see: tables/{t}/anomalies.csv and report.txt")
    else:
        lines.append("  (none)")
    lines.extend(["", f"Tables OK ({len(ok_tables)}): {', '.join(ok_tables) or '(none)'}"])
    if failed:
        lines.extend(["", f"Tables failed ({len(failed)}): {', '.join(failed)}"])
    lines.extend(
        [
            "",
            "Per-table outputs: tables/<table_name>/summary.json, report.txt, anomalies.csv (if issues)",
        ]
    )
    (output_dir / "OVERALL_REPORT.txt").write_text("\n".join(lines), encoding="utf-8")

    meta = {
        "connection_id": connection_id,
        "container": container_name,
        "base_folder": base_folder,
        "dir_source": dir_source,
        "date_dirs_count": len(scan_dirs),
        "tables_count": len(tables),
        "tables_with_issues": issues,
        "audit_opts": audit_opts,
    }
    (output_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Find non-GUID IDs in Dataverse exports (parallel, per-table report folder)."
    )
    p.add_argument("--source-json", default=None, metavar="PATH")
    p.add_argument("--tables", default=None, metavar="T1,T2")
    p.add_argument("--table", action="append", default=[], metavar="NAME")
    p.add_argument("--tables-from-db", action="store_true")
    p.add_argument("--all-db-tables", action="store_true")
    p.add_argument("--connection-id", type=int, default=None)
    p.add_argument(
        "--database-url",
        default=None,
        help="Postgres URL (postgresql://user:pass@host:port/db). "
        "Or set env DATABASE_URL / FASTAPI_DATABASE_URL (k8s secrets).",
    )
    p.add_argument("--cmp-id", type=int, default=None)
    p.add_argument("--date-dirs-from-db", action="store_true")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--safety-interval-minutes", type=int, default=None)
    p.add_argument("--max-date-dirs", type=int, default=None, metavar="N")
    p.add_argument("--all-date-dirs", action="store_true")
    p.add_argument(
        "--max-rows-per-table",
        type=int,
        default=0,
        help="Row cap per table (0 = unlimited, scan all rows in chosen CSVs). Default: 0.",
    )
    p.add_argument(
        "--max-samples-per-table",
        type=int,
        default=0,
        help="Max anomaly samples per table (0 = keep all found). Default: 0.",
    )
    p.add_argument("--chunk-size", type=int, default=50_000)
    p.add_argument(
        "--first-csv-only",
        action="store_true",
        help="Only scan the first non-delete CSV per date folder (default: scan ALL csv shards).",
    )
    p.add_argument("--include-delete-csvs", action="store_true")
    p.add_argument("--no-reuse-model-schema", action="store_true")
    p.add_argument(
        "--output-dir",
        default=None,
        help=f"Report root (default: {_DEFAULT_OUTPUT_ROOT}/conn<N>_<timestamp>/).",
    )
    p.add_argument("--output-summary-csv", default=None, help="Also write overall_summary path.")
    p.add_argument("--output-samples-csv", default=None, help="Also write overall_anomalies path.")
    p.add_argument("--parallel", type=int, default=10, help="Tables audited in parallel (default: 10).")
    p.add_argument("--only-anomalies", action="store_true")
    p.add_argument("--list-plan", action="store_true")
    args = p.parse_args()
    args.scan_all_csvs = not args.first_csv_only

    if not args.date_dirs_from_db and not args.start:
        print("Use --date-dirs-from-db --connection-id N or --start/--end.", file=sys.stderr)
        return 2

    cfg: Optional[Dict[str, Any]] = None
    if args.source_json:
        try:
            raw = dvi._load_source_json(args.source_json)
            cfg = dvi._resolve_source_config(raw)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Failed to read --source-json: {e}", file=sys.stderr)
            return 2

    db_url = getattr(args, "database_url", None)
    if cfg:
        db_url = db_url or cfg.get("database_url") or cfg.get("DATABASE_URL")
    try:
        _ensure_postgres_env(db_url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    tables, code = _resolve_tables(args, cfg)
    if code != 0:
        return code

    connection_id = dvi._resolve_connection_id(args, cfg)

    if args.list_plan:
        if not args.date_dirs_from_db:
            print("--list-plan requires --date-dirs-from-db", file=sys.stderr)
            return 2
        try:
            date_dirs, dir_source = dvi._resolve_date_directories(
                args, container=None, base_folder=None, cfg=cfg
            )
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        scan_dirs = _pick_date_dirs(date_dirs, args)
        print(f"connection_id: {connection_id}")
        print(f"tables ({len(tables)}):")
        for t in tables:
            print(f"  - {t}")
        print(f"date dirs ({dir_source}): {len(date_dirs)} total, will scan {len(scan_dirs)}")
        print(f"parallel: {args.parallel}")
        print(f"max_rows_per_table: {args.max_rows_per_table or 'unlimited'}")
        print(f"output root (default): {_DEFAULT_OUTPUT_ROOT}")
        for d in scan_dirs[:3]:
            print(f"  - {d}")
        if len(scan_dirs) > 3:
            print(f"  ... {len(scan_dirs) - 3} more ...")
        return 0

    conn, container_name, base_folder, cfg_from_creds, code = dvi._resolve_credentials(args)
    if code != 0:
        return code
    if cfg_from_creds:
        cfg = cfg_from_creds
    connection_id = dvi._resolve_connection_id(args, cfg)

    svc = BlobServiceClient.from_connection_string(conn)
    container = svc.get_container_client(container_name)
    base_folder, code = dvi._ensure_base_folder(container, base_folder)
    if code != 0:
        return code

    try:
        date_dirs, dir_source = dvi._resolve_date_directories(args, container, base_folder, cfg)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    scan_dirs = _pick_date_dirs(date_dirs, args)
    if not scan_dirs:
        print("No date directories to scan.", file=sys.stderr)
        return 1

    output_dir = _make_run_output_dir(args, connection_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    audit_opts = {
        "chunk_size": args.chunk_size,
        "max_rows_per_table": args.max_rows_per_table,
        "max_samples_per_table": args.max_samples_per_table,
        "scan_all_csvs": args.scan_all_csvs,
        "include_delete_csvs": args.include_delete_csvs,
        "no_reuse_model_schema": args.no_reuse_model_schema,
        "parallel": args.parallel,
    }

    _log(f"Output directory: {output_dir}")
    _log(f"Container: {container_name} | base_folder: {base_folder!r}")
    _log(f"Date dirs: {len(scan_dirs)} of {len(date_dirs)} ({dir_source})")
    _log(f"Tables: {len(tables)} | parallel: {args.parallel}")
    _log(
        f"Per-table row cap: {args.max_rows_per_table or 'NONE (full scan)'} | "
        f"scan_all_csvs: {args.scan_all_csvs}"
    )

    all_summaries: List[Dict[str, Any]] = []
    all_samples: List[Dict[str, Any]] = []
    workers = max(1, int(args.parallel))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _audit_table_worker,
                table,
                conn,
                container_name,
                base_folder or "",
                scan_dirs,
                audit_opts,
                output_dir,
            ): table
            for table in tables
        }
        for fut in as_completed(futures):
            _table, summary, samples = fut.result()
            all_summaries.append(summary)
            all_samples.extend(samples)

    all_summaries.sort(key=lambda s: s.get("TABLE", ""))
    _write_overall_report(
        output_dir,
        connection_id=connection_id,
        container_name=container_name,
        base_folder=base_folder or "",
        dir_source=dir_source,
        scan_dirs=scan_dirs,
        tables=tables,
        all_summaries=all_summaries,
        all_samples=all_samples,
        audit_opts=audit_opts,
    )

    import pandas as pd

    summary_path = Path(args.output_summary_csv) if args.output_summary_csv else output_dir / "overall_summary.csv"
    pd.DataFrame([_summary_to_row(s) for s in all_summaries]).to_csv(summary_path, index=False)
    if all_samples:
        samples_path = (
            Path(args.output_samples_csv) if args.output_samples_csv else output_dir / "overall_anomalies.csv"
        )
        pd.DataFrame(all_samples).to_csv(samples_path, index=False)

    issues = [s["TABLE"] for s in all_summaries if s.get("has_issues")]
    _log(f"\nDone. Reports: {output_dir}")
    _log(f"Tables with non-GUID IDs ({len(issues)}): {issues or '(none)'}")

    if args.only_anomalies and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
