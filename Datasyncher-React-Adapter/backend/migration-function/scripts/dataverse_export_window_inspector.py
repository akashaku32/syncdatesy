#!/usr/bin/env python3
"""
Inspect Microsoft Dataverse Azure Blob exports between two date-directory timestamps,
or locate every row for a given record ID across CSV shards (migration-aligned).

Layout (same as migration service):
  {BASE_FOLDER}/{YYYY-MM-DDTHH.MM.SSZ}/{table_name}/...
  {BASE_FOLDER}/{date}/Microsoft.Athena.TrickleFeedService/{table_name}-model.json

Credentials: use --source-json (same keys as Dynamics source_config) or env vars.
  AZURE_STORAGE_CONNECTION_STRING, AZURE_CONTAINER_NAME (optional AZURE_BASE_FOLDER if not inferring).

Optional: pip install python-dotenv. Default --table: ledgerjournaltrans.

Examples:
  python scripts/dataverse_export_window_inspector.py --source-json ./local-source.json --start "2026-05-19T22.59.08Z" --end "2026-05-20T03.59.09Z" --peek-rows 5000

  python scripts/dataverse_export_window_inspector.py --source-json ./local-source.json --table ledgerjournaltrans --lookup-id "000058e2-0000-0000-4620-005001000000" --output-csv ./id_trace.csv

  # Date dirs from Postgres (same tables as Dynamics migration), optional window filter:
  python scripts/dataverse_export_window_inspector.py --source-json ./local-source.json --connection-id 3 --date-dirs-from-db --table ledgerjournaltrans --lookup-id "000058e2-0000-0000-4620-005001000000" --output-csv ./id_trace.csv

  # Only date-directory folders on one UTC calendar day (with --date-dirs-from-db):
  python scripts/dataverse_export_window_inspector.py --source-json ./local-source.json --connection-id 3 --date-dirs-from-db --calendar-day 2026-01-14 --table ledgerjournaltable --lookup-id "..." --output-csv ./id_trace.csv

  # Single folder from source_table_selection.last_synced for one table:
  python scripts/dataverse_export_window_inspector.py --source-json ./local-source.json --connection-id 3 --table-last-checked-from-db --table ledgerjournaltrans --lookup-id "000058e2-0000-0000-4620-005001000000" --output-csv ./id_trace.csv
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from azure.storage.blob import BlobServiceClient

# Import same base-folder inference as migration-function
_MIGRATION_ROOT = Path(__file__).resolve().parent.parent
if str(_MIGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_MIGRATION_ROOT))
from utils.azure_path_utils import (  # noqa: E402
    DATAVERSE_DATE_DIR_RE,
    csv_blob_basename,
    dataverse_csv_processing_order_key,
    first_date_directory_in_blob_path,
    infer_dataverse_base_folder_from_paths,
    is_dataverse_delete_export_csv,
    join_azure_blob_path,
)


def _load_source_json(path: str) -> Dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_source_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize to keys: connection_string, container_name, base_folder (optional), connection_id."""
    merged = dict(raw)
    if "source_config" in merged and isinstance(merged["source_config"], dict):
        nested = dict(merged["source_config"])
        for key, value in nested.items():
            merged.setdefault(key, value)
    conn = (merged.get("connection_string") or merged.get("connectionString") or "").strip()
    container = (merged.get("container_name") or merged.get("containerName") or "").strip()
    base = merged.get("base_folder") or merged.get("baseFolder")
    if base is not None and str(base).strip():
        merged["base_folder"] = str(base).strip()
    else:
        merged["base_folder"] = None
    merged["connection_string"] = conn
    merged["container_name"] = container
    cid = merged.get("connection_id") or merged.get("connectionId")
    if cid is not None:
        try:
            merged["connection_id"] = int(cid)
        except (TypeError, ValueError):
            merged["connection_id"] = None
    return merged


def _parse_directories_field(directories_data: Any) -> List[str]:
    """Parse date_directory.directories (JSON string or list) into sorted folder names."""
    if directories_data is None:
        return []
    if isinstance(directories_data, str):
        directories_data = json.loads(directories_data)
    if not isinstance(directories_data, list):
        raise ValueError(f"Unexpected directories type: {type(directories_data)!r}")
    out: List[str] = []
    for name in directories_data:
        if not name:
            continue
        normalized = _normalize_user_timestamp(str(name).strip())
        if DATAVERSE_DATE_DIR_RE.match(normalized):
            out.append(normalized)
    out.sort(key=lambda n: _dir_to_utc_datetime(n))
    return out


def _fetch_date_directories_from_db(connection_id: int, cmp_id: int = 0) -> List[str]:
    """
    Full sorted list from date_directory.directories — same SQL as
    get_all_date_directories_from_db() in microsoft_dynamics_migration_service.
    Uses database_utils only (no heavy migration-service import).
    """
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT directories FROM date_directory WHERE connection_id = %s",
            (connection_id,),
        )
        row = cursor.fetchone()
        if not row:
            return []
        return _parse_directories_field(row[0])
    finally:
        conn.close()


def _fetch_table_last_synced_from_db(connection_id: int, table: str) -> Optional[str]:
    """
    Per-table export cursor from source_table_selection.last_synced
    (migration last_checked_date for that table).
    """
    from utils.database_utils import postgres_connection

    conn = postgres_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT last_synced
            FROM source_table_selection
            WHERE connection_id = %s AND "table" = %s
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
            """,
            (connection_id, table),
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return None
        raw = row[0]
        if hasattr(raw, "isoformat"):
            # timestamptz → approximate folder (migration stores folder names, not always DB timestamps)
            text = raw.strftime("%Y-%m-%dT%H.%M.%SZ")
            return _normalize_user_timestamp(text)
        return _normalize_user_timestamp(str(raw).strip())
    finally:
        conn.close()


def _filter_date_dirs_by_window(
    date_dirs: List[str],
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
) -> List[str]:
    if not start_dt and not end_dt:
        return list(date_dirs)
    filtered: List[str] = []
    for name in date_dirs:
        try:
            dt = _dir_to_utc_datetime(name)
        except ValueError:
            continue
        if start_dt and dt < start_dt:
            continue
        if end_dt and dt > end_dt:
            continue
        filtered.append(name)
    return filtered


def _resolve_connection_id(args, cfg: Optional[Dict[str, Any]]) -> Optional[int]:
    if getattr(args, "connection_id", None) is not None:
        return int(args.connection_id)
    if cfg and cfg.get("connection_id") is not None:
        return int(cfg["connection_id"])
    env_cid = os.environ.get("DATASYNCHER_CONNECTION_ID", "").strip()
    if env_cid:
        return int(env_cid)
    return None


def _apply_migration_safety_filter(date_dirs: List[str], safety_interval_minutes: Optional[int]) -> List[str]:
    """Same cutoff rule as migrate(): filter_date_directories(safety_interval_cutoff)."""
    if not safety_interval_minutes or safety_interval_minutes <= 0:
        return list(date_dirs)
    cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=int(safety_interval_minutes))
    filtered: List[str] = []
    for folder_date in date_dirs:
        try:
            if _dir_to_utc_datetime(folder_date) < cutoff_dt:
                filtered.append(folder_date)
        except ValueError:
            continue
    return filtered


def _resolve_date_directories(
    args,
    container,
    base_folder: Optional[str],
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], str]:
    """
    Return (date_dirs, source_description).
    Priority: --table-last-checked-from-db > --date-dirs-from-db > blob scan.
    """
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    if args.start:
        start_dt = _dir_to_utc_datetime(_normalize_user_timestamp(args.start))
    if args.end:
        end_dt = _dir_to_utc_datetime(_normalize_user_timestamp(args.end))
    if start_dt and end_dt and end_dt < start_dt:
        raise ValueError("--end must be >= --start")

    cmp_id = int(getattr(args, "cmp_id", None) or (cfg or {}).get("cmp_id") or 0)

    if getattr(args, "table_last_checked_from_db", False):
        connection_id = _resolve_connection_id(args, cfg)
        if connection_id is None:
            raise ValueError(
                "--table-last-checked-from-db requires --connection-id "
                "(or connection_id in --source-json / DATASYNCHER_CONNECTION_ID)"
            )
        table = (args.table or "").strip()
        if not table:
            raise ValueError("--table is required with --table-last-checked-from-db")
        folder = _fetch_table_last_synced_from_db(connection_id, table)
        if not folder:
            raise ValueError(
                f"No last_synced in source_table_selection for connection_id={connection_id} table={table!r}"
            )
        dirs = _filter_date_dirs_by_window([folder], start_dt, end_dt)
        if not dirs:
            raise ValueError(f"Table last_synced folder {folder!r} is outside --start/--end window")
        return dirs, f"source_table_selection.last_synced (connection_id={connection_id}, table={table})"

    if getattr(args, "date_dirs_from_db", False):
        connection_id = _resolve_connection_id(args, cfg)
        if connection_id is None:
            raise ValueError(
                "--date-dirs-from-db requires --connection-id "
                "(or connection_id in --source-json / DATASYNCHER_CONNECTION_ID)"
            )
        dirs = _fetch_date_directories_from_db(connection_id, cmp_id=cmp_id)
        if not dirs:
            raise ValueError(f"No rows in date_directory for connection_id={connection_id}")
        max_dir = dirs[-1] if dirs else None
        safety_min = getattr(args, "safety_interval_minutes", None)
        if safety_min is not None:
            dirs = _apply_migration_safety_filter(dirs, safety_min)
        dirs = _filter_date_dirs_by_window(dirs, start_dt, end_dt)
        if not dirs:
            raise ValueError("No date_directory folders remain after filters")
        return (
            dirs,
            f"date_directory.directories (connection_id={connection_id}, count={len(dirs)}, max_in_db={max_dir!r})",
        )

    # Blob listing (legacy / --blob-scan-date-dirs)
    if start_dt is not None and end_dt is not None:
        dirs = _discover_date_directories(container, base_folder, args.max_list_ops, start_dt, end_dt)
        return dirs, f"azure blob scan {args.start} .. {args.end}"

    dirs = _discover_date_directories(container, base_folder, args.max_list_ops, None, None)
    return dirs, "azure blob scan (all discovered date dirs)"


def _apply_calendar_day_to_args(args: argparse.Namespace) -> None:
    """
    Set args.start / args.end to the full UTC calendar day when --calendar-day is given.
    Mutually exclusive with explicit --start / --end.
    """
    raw = getattr(args, "calendar_day", None)
    if not raw or not str(raw).strip():
        return
    if getattr(args, "start", None) or getattr(args, "end", None):
        raise SystemExit("error: --calendar-day cannot be used together with --start or --end")
    d = str(raw).strip()
    parts = d.split("-", 2)
    if len(parts) != 3:
        raise SystemExit("error: --calendar-day must be YYYY-MM-DD (UTC)")
    try:
        y, m, dd = int(parts[0]), int(parts[1]), int(parts[2])
        date_type(y, m, dd)
    except ValueError as e:
        raise SystemExit(f"error: invalid --calendar-day {d!r}: {e}")
    args.start = f"{y:04d}-{m:02d}-{dd:02d}T00.00.00Z"
    args.end = f"{y:04d}-{m:02d}-{dd:02d}T23.59.59Z"


def _infer_base_folder(container, max_blobs: int = 10) -> str:
    """Same as microsoft_dynamics_migration_service.infer_base_folder (first page, max_blobs)."""
    blob_iterator = container.list_blobs(results_per_page=max_blobs).by_page()
    first_page = next(blob_iterator)
    paths = [blob.name for blob in first_page]
    if not paths:
        raise ValueError("No blobs in container; cannot infer base_folder.")
    return infer_dataverse_base_folder_from_paths(paths)


def _normalize_user_timestamp(s: str) -> str:
    """
    Accept '2026-05-20T02:59:09Z' or '2026-05-20T02.59.09Z' (dots in time) and return
    canonical Dataverse folder form with dots: 2026-05-20T02.59.09Z
    """
    raw = (s or "").strip()
    if not raw:
        raise ValueError("empty timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1]
    raw = raw.replace("Z", "")
    # Replace colons in time portion with dots (Dataverse blob folder convention)
    if "T" in raw:
        date_part, time_part = raw.split("T", 1)
        time_part = time_part.replace(":", ".")
        raw = f"{date_part}T{time_part}"
    if not raw.endswith("Z"):
        raw = raw + "Z"
    if not DATAVERSE_DATE_DIR_RE.match(raw):
        raise ValueError(
            f"Timestamp must look like YYYY-MM-DDTHH.MM.SSZ (dots in clock), got: {s!r} -> {raw!r}"
        )
    return raw


def _dir_to_utc_datetime(name: str) -> datetime:
    """Parse Dataverse date folder name to aware UTC datetime."""
    if not DATAVERSE_DATE_DIR_RE.match(name):
        raise ValueError(f"not a dataverse date directory: {name!r}")
    # 2026-05-20T02.59.09Z
    d, t = name.split("T", 1)
    t = t.rstrip("Z")
    hh, mm, ss = t.split(".")
    return datetime(
        int(d[:4]),
        int(d[5:7]),
        int(d[8:10]),
        int(hh),
        int(mm),
        int(ss),
        tzinfo=timezone.utc,
    )


def _list_blobs_prefix(container, base_folder: Optional[str]):
    """Mirror fetch_blobs_for_prefix list_prefix: org prefix or whole container."""
    if base_folder:
        list_prefix = join_azure_blob_path(base_folder)
        return container.list_blobs(name_starts_with=list_prefix)
    return container.list_blobs()


def _list_table_blobs(
    container,
    base_folder: str,
    date_dir: str,
    table: str,
) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    prefix = join_azure_blob_path(base_folder, date_dir, table) + "/"
    for blob in container.list_blobs(name_starts_with=prefix):
        size = int(getattr(blob, "size", 0) or 0)
        out.append((blob.name, size))
    return sorted(out)


def _load_model_attribute_names(
    container,
    base_folder: str,
    date_dir: str,
    table: str,
) -> List[str]:
    """Return ordered attribute names from Microsoft.Athena.TrickleFeedService {table}-model.json."""
    json_path = join_azure_blob_path(
        base_folder,
        date_dir,
        "Microsoft.Athena.TrickleFeedService",
        f"{table}-model.json",
    )
    bc = container.get_blob_client(json_path)
    if not bc.exists():
        raise FileNotFoundError(f"model json not found: {json_path}")
    raw = bc.download_blob().readall()
    data = json.loads(raw.decode("utf-8"))
    entities = data.get("entities") or []
    if not entities:
        raise ValueError("model json has no entities[]")
    attrs = entities[0].get("attributes") or []
    names = [a["name"] for a in attrs if isinstance(a, dict) and a.get("name")]
    if not names:
        raise ValueError("model json entities[0].attributes has no names")
    return names


def _peek_csv(
    container,
    blob_name: str,
    attribute_names: List[str],
    max_rows: int,
) -> Dict[str, Any]:
    import pandas as pd

    bc = container.get_blob_client(blob_name)
    data = bc.download_blob().readall()
    # Headerless CSV; same as migration read_csv_in_chunks
    df = pd.read_csv(
        io.BytesIO(data),
        header=None,
        names=attribute_names,
        nrows=max_rows,
        low_memory=False,
        dtype=str,
    )
    # Match transformations: column cleanup to find IsDelete -> IS_DELETE
    cols_upper = [c.upper().replace(" ", "_") for c in df.columns]
    df.columns = cols_upper
    isdel_col = None
    for cand in ("ISDELETE", "IS_DELETE"):
        if cand in df.columns:
            isdel_col = cand
            break
    summary: Dict[str, Any] = {
        "blob": blob_name,
        "rows_peeked": len(df.index),
        "columns": len(df.columns),
    }
    if isdel_col:
        ser = df[isdel_col].str.lower().isin(("true", "1", "yes")) | (df[isdel_col] == "True")
        summary["is_delete_column"] = isdel_col
        summary["is_delete_true_rows_in_peek"] = int(ser.sum())
    else:
        summary["is_delete_column"] = None
    if "ID" in df.columns:
        summary["distinct_id_in_peek"] = int(df["ID"].nunique(dropna=False))
    return summary


def _discover_date_directories(
    container,
    base_folder: Optional[str],
    max_list_ops: int,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
) -> List[str]:
    """Discover date dirs under base_folder; optional inclusive UTC window."""
    found: Dict[str, datetime] = {}
    ops = 0
    for blob in _list_blobs_prefix(container, base_folder):
        ops += 1
        if ops > max_list_ops:
            print(
                f"WARNING: stopped listing after {max_list_ops} blob metadata rows "
                f"(raise --max-list-ops if your container is large).",
                file=sys.stderr,
            )
            break
        folder = first_date_directory_in_blob_path(blob.name)
        if not folder:
            continue
        try:
            dt = _dir_to_utc_datetime(folder)
        except ValueError:
            continue
        if start_dt is not None and dt < start_dt:
            continue
        if end_dt is not None and dt > end_dt:
            continue
        found.setdefault(folder, dt)
    return sorted(found.keys(), key=lambda n: found[n])


def _migration_style_column_names(attribute_names: List[str]) -> List[str]:
    """Uppercase / underscore cleanup aligned with transformations() in migration service."""
    import re

    def add_underscore(column_name: str) -> str:
        column_name = re.sub(r"(?<=[a-zA-Z])(?=[0-9])", "_", column_name)
        column_name = re.sub(r"(?<=[0-9])(?=[a-zA-Z])", "_", column_name)
        column_name = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", column_name)
        return column_name.upper()

    cols = [add_underscore(c) for c in attribute_names]
    cols = [c.lstrip("_").replace("__", "_") for c in cols]
    return [c.upper().strip() for c in cols]


def _resolve_id_column(column_names: List[str], attribute_names: List[str]) -> str:
    """Prefer Id/ID from model; fall back to first CSV column (user: ID is column 1)."""
    upper = list(column_names)
    if "ID" in upper:
        return "ID"
    if attribute_names:
        first_model = _migration_style_column_names([attribute_names[0]])[0]
        if first_model in upper:
            return first_model
    if upper:
        return upper[0]
    raise ValueError("no columns available to resolve ID field")


def _parse_is_delete_series(df, id_col: str) -> "Any":
    import pandas as pd

    for cand in ("IS_DELETE", "ISDELETE"):
        if cand not in df.columns:
            continue
        ser = df[cand]
        if ser.dtype == bool:
            return ser.fillna(False)
        lowered = ser.astype(str).str.strip().str.lower()
        return lowered.isin(("true", "1", "yes"))
    return pd.Series([False] * len(df.index), index=df.index)


def _list_csv_blobs_migration_order(
    container,
    base_folder: str,
    date_dir: str,
    table: str,
) -> List[Dict[str, Any]]:
    """CSV blobs under one date/table folder, sorted like process_file_in_chunks."""
    prefix = join_azure_blob_path(base_folder, date_dir, table) + "/"
    rows: List[Dict[str, Any]] = []
    for blob in container.list_blobs(name_starts_with=prefix):
        if not blob.name.lower().endswith(".csv"):
            continue
        lm = getattr(blob, "last_modified", None)
        rows.append(
            {
                "name": blob.name,
                "size": int(getattr(blob, "size", 0) or 0),
                "last_modified": lm,
                "basename": csv_blob_basename(blob.name),
                "is_delete_csv": is_dataverse_delete_export_csv(blob.name),
            }
        )
    rows.sort(key=lambda r: dataverse_csv_processing_order_key(r["name"]))
    for i, r in enumerate(rows):
        r["migration_processing_order"] = i
    return rows


def _search_csv_for_id(
    container,
    blob_name: str,
    attribute_names: List[str],
    lookup_id: str,
    chunk_size: int,
    meta: Dict[str, Any],
) -> "Any":
    """
    Read CSV headerless with model column order (migration read_csv_in_chunks),
    return matching rows with source metadata columns.
    """
    import pandas as pd

    lookup_id = (lookup_id or "").strip()
    if not lookup_id:
        return pd.DataFrame()

    column_converters = {col: str for col in attribute_names}
    bc = container.get_blob_client(blob_name)
    data = bc.download_blob().readall()
    col_names = _migration_style_column_names(attribute_names)
    id_col = _resolve_id_column(col_names, attribute_names)

    matches: List[pd.DataFrame] = []
    chunk_no = 0
    for encoding in (None, "ISO-8859-1"):
        try:
            bio = io.BytesIO(data)
            kwargs: Dict[str, Any] = {
                "header": None,
                "names": attribute_names,
                "chunksize": chunk_size,
                "low_memory": False,
                "converters": column_converters,
            }
            if encoding:
                kwargs["encoding"] = encoding
            for chunk in pd.read_csv(bio, **kwargs):
                chunk_no += 1
                if chunk.empty:
                    continue
                chunk.columns = col_names
                id_series = chunk[id_col].astype(str).str.strip()
                hit = id_series == lookup_id
                if not hit.any():
                    continue
                part = chunk.loc[hit].copy()
                part["_SOURCE_DATE_DIR"] = meta["date_dir"]
                part["_SOURCE_BLOB"] = blob_name
                part["_SOURCE_CSV_BASENAME"] = meta["basename"]
                part["_BLOB_LAST_MODIFIED_UTC"] = meta.get("last_modified")
                part["_MIGRATION_PROCESSING_ORDER"] = meta.get("migration_processing_order")
                part["_IS_DELETE_CSV_SHARD"] = meta.get("is_delete_csv", False)
                part["_MIGRATION_FORCED_IS_DELETE"] = meta.get("is_delete_csv", False)
                part["_CSV_CHUNK_NUMBER"] = chunk_no
                part["_ID_COLUMN_USED"] = id_col
                is_del = _parse_is_delete_series(part, id_col)
                part["_CSV_IS_DELETE_PARSED"] = is_del.values
                matches.append(part)
            break
        except UnicodeDecodeError:
            if encoding is None:
                continue
            raise

    if not matches:
        return pd.DataFrame()
    return pd.concat(matches, ignore_index=True)


def _find_fallback_model_attribute_names(
    container,
    base_folder: str,
    table: str,
    date_dirs: List[str],
) -> Tuple[Optional[List[str]], Optional[str]]:
    """Newest-first search for any date_dir that has {table}-model.json."""
    for date_dir in reversed(date_dirs):
        try:
            names = _load_model_attribute_names(container, base_folder, date_dir, table)
            return names, date_dir
        except Exception:
            continue
    return None, None


def lookup_id_across_exports(
    container,
    base_folder: str,
    table: str,
    lookup_id: str,
    date_dirs: List[str],
    chunk_size: int,
    *,
    quiet: bool = False,
    reuse_model_schema: bool = True,
    progress_every: int = 250,
    log_each_dir: bool = False,
) -> "Any":
    """Scan all CSV shards in each date dir; return one combined DataFrame."""
    import pandas as pd

    all_parts: List[pd.DataFrame] = []
    files_with_hits: List[str] = []
    dirs_with_hits: List[str] = []
    skipped_no_csv = 0
    scanned_dirs = 0

    fallback_names: Optional[List[str]] = None
    fallback_from: Optional[str] = None
    if reuse_model_schema and date_dirs:
        fallback_names, fallback_from = _find_fallback_model_attribute_names(
            container, base_folder, table, date_dirs
        )
        if fallback_names and not quiet:
            print(
                f"Reusing model schema from {fallback_from!r} when per-folder model.json is missing.",
                file=sys.stderr,
            )

    total = len(date_dirs)
    for idx, date_dir in enumerate(date_dirs, start=1):
        if progress_every > 0 and not log_each_dir and (
            idx == 1 or idx % progress_every == 0 or idx == total
        ):
            print(
                f"Progress: {idx}/{total} date dirs "
                f"(scanned_with_csv={scanned_dirs}, hits={len(files_with_hits)})",
                file=sys.stderr,
            )

        attribute_names: Optional[List[str]] = None
        try:
            attribute_names = _load_model_attribute_names(container, base_folder, date_dir, table)
        except Exception:
            if reuse_model_schema and fallback_names:
                attribute_names = fallback_names
            elif not quiet:
                print(f"  skip {date_dir}: model json unavailable", file=sys.stderr)
            if not attribute_names:
                if log_each_dir:
                    print(
                        f"[{idx}/{total}] {date_dir} skip (no model.json)",
                        file=sys.stderr,
                        flush=True,
                    )
                continue

        csv_blobs = _list_csv_blobs_migration_order(container, base_folder, date_dir, table)
        if not csv_blobs:
            skipped_no_csv += 1
            if log_each_dir:
                print(
                    f"[{idx}/{total}] {date_dir} done (no {table} csvs)",
                    file=sys.stderr,
                    flush=True,
                )
            continue

        scanned_dirs += 1
        dir_had_hit = False
        dir_hit_files = 0
        for blob_info in csv_blobs:
            meta = {
                "date_dir": date_dir,
                "basename": blob_info["basename"],
                "last_modified": (
                    blob_info["last_modified"].isoformat()
                    if blob_info.get("last_modified")
                    else None
                ),
                "migration_processing_order": blob_info["migration_processing_order"],
                "is_delete_csv": blob_info["is_delete_csv"],
            }
            try:
                part = _search_csv_for_id(
                    container,
                    blob_info["name"],
                    attribute_names,
                    lookup_id,
                    chunk_size,
                    meta,
                )
            except Exception as e:
                print(f"  error reading {blob_info['name']}: {e}", file=sys.stderr)
                continue
            if part is None or part.empty:
                continue
            all_parts.append(part)
            files_with_hits.append(blob_info["name"])
            dir_had_hit = True
            dir_hit_files += 1
            print(
                f"  hit: {date_dir} / {blob_info['basename']} "
                f"rows={len(part.index)} order={blob_info['migration_processing_order']}"
                f"{' [delete shard]' if blob_info['is_delete_csv'] else ''}",
                flush=True,
            )
        if dir_had_hit:
            dirs_with_hits.append(date_dir)
        if log_each_dir:
            print(
                f"[{idx}/{total}] {date_dir} done "
                f"({len(csv_blobs)} csv(s), {dir_hit_files} hit file(s))",
                file=sys.stderr,
                flush=True,
            )

    print(f"\nLookup summary for ID {lookup_id!r}:")
    print(f"  date directories listed: {len(date_dirs)}")
    print(f"  date directories with csv data: {scanned_dirs}")
    print(f"  date directories without table csvs: {skipped_no_csv}")
    if fallback_from:
        print(f"  fallback model schema from: {fallback_from}")
    print(f"  date directories with hits: {len(dirs_with_hits)}")
    print(f"  csv files with hits: {len(files_with_hits)}")
    if dirs_with_hits:
        print("  folders:")
        for d in dirs_with_hits:
            print(f"    - {join_azure_blob_path(base_folder, d, table)}/")

    if not all_parts:
        return pd.DataFrame()

    combined = pd.concat(all_parts, ignore_index=True)
    combined["_LOOKUP_ID"] = lookup_id.strip()
    combined["_LOOKUP_TABLE"] = table
    return combined


def _resolve_credentials(args) -> Tuple[str, str, Optional[str], Optional[Dict[str, Any]], int]:
    """Return connection_string, container_name, base_folder, cfg dict, exit_code."""
    conn = ""
    container_name = ""
    base_folder: Optional[str] = None
    cfg: Optional[Dict[str, Any]] = None

    if args.source_json:
        try:
            raw = _load_source_json(args.source_json)
            cfg = _resolve_source_config(raw)
        except OSError as e:
            print(f"Failed to read --source-json: {e}", file=sys.stderr)
            return "", "", None, None, 2
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}", file=sys.stderr)
            return "", "", None, None, 2
        conn = cfg.get("connection_string") or ""
        container_name = cfg.get("container_name") or ""
        base_folder = cfg.get("base_folder")
    else:
        conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        container_name = os.environ.get("AZURE_CONTAINER_NAME", "").strip()
        bf = os.environ.get("AZURE_BASE_FOLDER", "").strip()
        base_folder = bf or None

    if not conn or not container_name:
        print(
            "Provide --source-json PATH with connection_string and container_name "
            "(same as Dynamics migrate source_config), or set "
            "AZURE_STORAGE_CONNECTION_STRING and AZURE_CONTAINER_NAME.",
            file=sys.stderr,
        )
        return "", "", None, None, 2
    return conn, container_name, base_folder, cfg, 0


def _ensure_base_folder(container, base_folder: Optional[str]) -> Tuple[Optional[str], int]:
    if base_folder:
        return str(base_folder).strip() or None, 0
    print(
        "base_folder not in JSON / env; inferring like infer_base_folder() "
        "(first page of blobs, max 10)..."
    )
    try:
        inferred = _infer_base_folder(container)
    except Exception as e:
        print(f"Could not infer base_folder: {e}", file=sys.stderr)
        return None, 2
    print(f"Inferred base_folder: {inferred!r}")
    return inferred, 0


def _force_utf8_stdio() -> None:
    """Avoid Windows cp1252/charmap errors when printing CSV preview rows with Unicode."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                if hasattr(stream, "reconfigure"):
                    stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def run_id_lookup(args) -> int:
    import pandas as pd

    _force_utf8_stdio()

    conn, container_name, base_folder, cfg, code = _resolve_credentials(args)
    if code != 0:
        return code

    lookup_id = (args.lookup_id or "").strip()
    if not lookup_id:
        print("--lookup-id must be non-empty", file=sys.stderr)
        return 2

    table = (args.table or "").strip()
    if not table:
        print("--table is required with --lookup-id", file=sys.stderr)
        return 2

    if not (
        getattr(args, "date_dirs_from_db", False)
        or getattr(args, "table_last_checked_from_db", False)
    ):
        print(
            "Hint: use --date-dirs-from-db --connection-id N to load folders from "
            "Postgres date_directory (same as Dynamics migration).",
            file=sys.stderr,
        )

    svc = BlobServiceClient.from_connection_string(conn)
    container = svc.get_container_client(container_name)
    base_folder, code = _ensure_base_folder(container, base_folder)
    if code != 0:
        return code

    print(f"Container: {container_name}")
    print(f"Base folder: {base_folder!r}")
    print(f"Table: {table}")
    print(f"Lookup ID: {lookup_id}")

    try:
        date_dirs, dir_source = _resolve_date_directories(args, container, base_folder, cfg)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    max_lookup_dirs = int(getattr(args, "lookup_max_date_dirs", 0) or 0)
    if max_lookup_dirs > 0 and len(date_dirs) > max_lookup_dirs:
        before = len(date_dirs)
        date_dirs = date_dirs[-max_lookup_dirs:]
        print(
            f"Using newest {max_lookup_dirs} of {before} date directories (--lookup-max-date-dirs).",
            file=sys.stderr,
        )

    print(f"Date directories: {dir_source}")
    if len(date_dirs) <= 30:
        for d in date_dirs:
            print(f"  - {d}")
    else:
        for d in date_dirs[:3]:
            print(f"  - {d}")
        print(f"  ... ({len(date_dirs) - 6} more) ...")
        for d in date_dirs[-3:]:
            print(f"  - {d}")
    if not date_dirs:
        print("No date directories to scan.", file=sys.stderr)
        return 1

    print(f"Scanning {len(date_dirs)} date director(ies) for matching rows...")
    combined = lookup_id_across_exports(
        container,
        base_folder or "",
        table,
        lookup_id,
        date_dirs,
        args.chunk_size,
        quiet=getattr(args, "quiet", False),
        reuse_model_schema=not getattr(args, "no_reuse_model_schema", False),
        progress_every=(
            0
            if getattr(args, "log_each_dir", False)
            else getattr(args, "progress_every", 250)
        ),
        log_each_dir=getattr(args, "log_each_dir", False),
    )

    if combined.empty:
        print("No rows found for this ID in the scanned window.")
        return 1

    print(f"\nTotal matching rows: {len(combined.index)}")
    show_cols = [
        c
        for c in (
            "_SOURCE_DATE_DIR",
            "_SOURCE_CSV_BASENAME",
            "_MIGRATION_PROCESSING_ORDER",
            "_IS_DELETE_CSV_SHARD",
            "_CSV_IS_DELETE_PARSED",
            "ID",
            "SINK_MODIFIED_ON",
            "IS_DELETE",
            "ISDELETE",
        )
        if c in combined.columns
    ]
    if show_cols:
        print(combined[show_cols].to_string(index=False))

    if args.output_csv:
        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        export_df = combined
        if getattr(args, "slim_csv", False):
            slim_cols = [
                c
                for c in (
                    "ID",
                    "RECID",
                    "JOURNALNUM",
                    "JOURNALNAME",
                    "SINK_CREATED_ON",
                    "SINK_MODIFIED_ON",
                    "IS_DELETE",
                    "ISDELETE",
                    "POSTED",
                    "DATAAREAID",
                    "_SOURCE_DATE_DIR",
                    "_SOURCE_CSV_BASENAME",
                    "_BLOB_LAST_MODIFIED_UTC",
                    "_MIGRATION_PROCESSING_ORDER",
                    "_IS_DELETE_CSV_SHARD",
                    "_MIGRATION_FORCED_IS_DELETE",
                    "_CSV_IS_DELETE_PARSED",
                    "_LOOKUP_ID",
                    "_LOOKUP_TABLE",
                )
                if c in export_df.columns
            ]
            export_df = export_df[slim_cols]
        exclude = getattr(args, "exclude_columns", None) or []
        if exclude:
            drop = [c for c in exclude if c in export_df.columns]
            if drop:
                export_df = export_df.drop(columns=drop)
        export_df.to_csv(out_path, index=False)
        print(f"\nWrote combined DataFrame ({len(export_df.index)} rows) to {out_path}")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="List Dataverse blob paths or trace one record ID across CSV exports."
    )
    p.add_argument(
        "--start",
        default=None,
        help="Start boundary (inclusive). Required for listing mode; optional for --lookup-id.",
    )
    p.add_argument(
        "--end",
        default=None,
        help="End boundary (inclusive). Required for listing mode; optional for --lookup-id.",
    )
    p.add_argument(
        "--calendar-day",
        default=None,
        metavar="YYYY-MM-DD",
        help="UTC calendar day only: sets --start/--end to that day's 00:00:00..23:59:59. "
        "Cannot be combined with --start/--end. With --date-dirs-from-db, only folders whose "
        "timestamp falls on that UTC date are scanned.",
    )
    p.add_argument(
        "--lookup-id",
        default=None,
        metavar="GUID",
        help="Find all CSV rows with this ID (first column / model Id field), merge into one DataFrame.",
    )
    p.add_argument(
        "--slim-csv",
        action="store_true",
        help="With --lookup-id, omit huge text columns (e.g. LOG) from --output-csv.",
    )
    p.add_argument(
        "--exclude-columns",
        action="append",
        default=[],
        metavar="COL",
        help="Drop column(s) from --output-csv (repeatable).",
    )
    p.add_argument(
        "--output-csv",
        default=None,
        metavar="PATH",
        help="With --lookup-id, write the combined matching rows to this CSV file.",
    )
    p.add_argument(
        "--chunk-size",
        type=int,
        default=50_000,
        help="CSV read chunk size for --lookup-id (default: 50000, same as migration).",
    )
    p.add_argument(
        "--lookup-max-date-dirs",
        type=int,
        default=0,
        metavar="N",
        help="With --lookup-id, scan at most the N chronologically newest date dirs (0 = no limit).",
    )
    p.add_argument(
        "--source-json",
        default=None,
        metavar="PATH",
        help="Path to JSON with Dynamics source_config keys (connection_string, container_name, "
        "optional base_folder). Use '-' to read JSON from stdin.",
    )
    p.add_argument(
        "--table",
        default="ledgerjournaltrans",
        help="Table folder name under each date dir (default: ledgerjournaltrans).",
    )
    p.add_argument(
        "--peek-rows",
        type=int,
        default=0,
        help="If >0, read first N rows of the first CSV in each dir (uses model.json column order).",
    )
    p.add_argument(
        "--max-list-ops",
        type=int,
        default=200_000,
        help="Safety cap: max blob list iterations when discovering date dirs under base folder.",
    )
    p.add_argument(
        "--download-dir",
        default=None,
        help="If set, download all blobs for --table in window into this local directory (preserves blob path).",
    )
    p.add_argument(
        "--connection-id",
        type=int,
        default=None,
        help="Dynamics connection_id for Postgres date_directory / source_table_selection (or set in --source-json).",
    )
    p.add_argument(
        "--cmp-id",
        type=int,
        default=None,
        help="Company id for migration DB error logging (optional, default 0).",
    )
    p.add_argument(
        "--date-dirs-from-db",
        action="store_true",
        help="Load date folders from Postgres date_directory.directories (get_all_date_directories_from_db).",
    )
    p.add_argument(
        "--table-last-checked-from-db",
        action="store_true",
        help="Use only source_table_selection.last_synced for --table (one folder).",
    )
    p.add_argument(
        "--safety-interval-minutes",
        type=int,
        default=None,
        help="With --date-dirs-from-db, apply migration filter_date_directories cutoff (minutes).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Less stderr noise (skip messages, progress only).",
    )
    p.add_argument(
        "--no-reuse-model-schema",
        action="store_true",
        help="Require model.json in every date folder (do not reuse schema from newest folder).",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=250,
        help="Print progress every N date directories (0=off). Default 250.",
    )
    p.add_argument(
        "--log-each-dir",
        action="store_true",
        help="Log one line to stderr when each date directory check completes (disables --progress-every).",
    )
    args = p.parse_args()
    _apply_calendar_day_to_args(args)

    if args.lookup_id:
        return run_id_lookup(args)

    if not args.start or not args.end:
        print("--start and --end are required unless using --lookup-id.", file=sys.stderr)
        return 2

    conn, container_name, base_folder, cfg, code = _resolve_credentials(args)
    if code != 0:
        return code

    svc = BlobServiceClient.from_connection_string(conn)
    container = svc.get_container_client(container_name)

    base_folder, code = _ensure_base_folder(container, base_folder)
    if code != 0:
        return code

    if not args.start or not args.end:
        if not getattr(args, "date_dirs_from_db", False):
            print("--start and --end are required unless --date-dirs-from-db.", file=sys.stderr)
            return 2
    else:
        start_name = _normalize_user_timestamp(args.start)
        end_name = _normalize_user_timestamp(args.end)
        if _dir_to_utc_datetime(end_name) < _dir_to_utc_datetime(start_name):
            print("--end must be >= --start", file=sys.stderr)
            return 2

    print(f"Container: {container_name}")
    print(f"Base folder: {base_folder!r}" + (" (empty = container root)" if base_folder == "" else ""))

    try:
        date_dirs, dir_source = _resolve_date_directories(args, container, base_folder, cfg)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if not date_dirs:
        print("No date directories found.", file=sys.stderr)
        return 1

    print(f"Found {len(date_dirs)} date director(ies) ({dir_source}):")
    for d in date_dirs:
        print(f"  - {d}")

    table = (args.table or "").strip()
    if not table:
        print("\nDone (--table empty). Pass --table to list blobs / peek / download.")
        return 0
    total_bytes = 0
    for d in date_dirs:
        blobs = _list_table_blobs(container, base_folder, d, table)
        dir_bytes = sum(sz for _, sz in blobs)
        total_bytes += dir_bytes
        print(f"\n=== {d} / {table} ===  blobs={len(blobs)} bytes={dir_bytes}")
        ordered = sorted((n for n, _ in blobs), key=dataverse_csv_processing_order_key)
        if ordered:
            order_labels = [x.rsplit("/", 1)[-1] for x in ordered]
            print(f"  processing_order (migration): {' -> '.join(order_labels)}")
        for name, sz in blobs[:50]:
            tag = " [delete]" if is_dataverse_delete_export_csv(name) else ""
            print(f"  {sz:>12}  {name}{tag}")
        if len(blobs) > 50:
            print(f"  ... {len(blobs) - 50} more")

        if args.peek_rows > 0 and blobs:
            csv_blobs = [n for n, _ in blobs if n.lower().endswith(".csv")]
            if not csv_blobs:
                print("  (no .csv under this prefix; skip peek)")
                continue
            try:
                attrs = _load_model_attribute_names(container, base_folder, d, table)
            except Exception as e:
                print(f"  peek skipped (model json): {e}")
                continue
            first_csv = sorted(csv_blobs)[0]
            try:
                info = _peek_csv(container, first_csv, attrs, args.peek_rows)
                print(f"  peek: {info}")
            except Exception as e:
                print(f"  peek failed: {e}")

        if args.download_dir:
            import pathlib

            root = pathlib.Path(args.download_dir)
            for name, _ in blobs:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                bc = container.get_blob_client(name)
                dest.write_bytes(bc.download_blob().readall())
            print(f"  downloaded to {args.download_dir}")

    print(f"\nTotal bytes for table {table!r} across listed dirs: {total_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
