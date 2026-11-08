"""
Azure Blob path helpers for Microsoft Dataverse export layouts.

Dataverse stores exports as:
  {org_base_folder}/{YYYY-MM-DDTHH.MM.SSZ}/{table}/...
  {org_base_folder}/{date}/Microsoft.Athena.TrickleFeedService/{table}-model.json

The date directory is NOT always the first path segment (org folder comes first).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, List, Optional

# Dataverse folder timestamp format used across listing + migration
DATAVERSE_DATE_DIR_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.\d{2}Z$"
)

# Long CSV stem shapes (under {date}/{table}/) when inferring sortable calendar dates.
_DATAVERSE_LONG_STEM_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATAVERSE_LONG_STEM_ISO_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True)
class DataverseCsvShardSortRules:
    """
    Tunable rules for ordering CSV shards under a Dataverse date/table folder.

    Override by passing ``rules=`` to ``dataverse_csv_processing_order_key`` (or a copy
    with ``dataclasses.replace``) instead of editing literals inside the sort helpers.
    """

    # Stems with this length or greater are treated as bulk / date-style names first.
    bulk_or_date_stem_min_len: int = 4
    # When interpreting a 6-digit all-numeric stem as YYYYMM, require year in this range.
    yyyy_mm_year_min: int = 1900
    yyyy_mm_year_max: int = 2100
    # For MMDDYY two-digit years: values < pivot map to 2000+yy, else 1900+yy.
    mmddyy_two_digit_year_pivot: int = 70
    # Used by ``is_dataverse_delete_export_csv`` / migration flush logic (not sort order).
    delete_csv_basenames: frozenset[str] = field(default_factory=lambda: frozenset({"2.csv"}))
    final_csv_basename: str = "1.csv"


DEFAULT_DATAVERSE_CSV_SORT_RULES = DataverseCsvShardSortRules()

# Synapse Link / Dataverse export: CSV shards under each date/table folder.
# Stems with length >= bulk_or_date_stem_min_len first (year / month-year / date-style order),
# then stems shorter than that threshold: numeric ascending, then non-numeric lexically.
DATAVERSE_DELETE_CSV_BASENAMES = DEFAULT_DATAVERSE_CSV_SORT_RULES.delete_csv_basenames
DATAVERSE_FINAL_CSV_BASENAME = DEFAULT_DATAVERSE_CSV_SORT_RULES.final_csv_basename
DATAVERSE_BULK_OR_DATE_STEM_MIN_LEN = DEFAULT_DATAVERSE_CSV_SORT_RULES.bulk_or_date_stem_min_len

# Sort tuple tiers (explicit so ordering logic does not rely on unexplained integers).
_TIER_LONG_STEM = 0
_TIER_SHORT_STEM = 1
# Within the long-stem tier: parsed calendar dates, then long digit stems, then lexical.
_LONG_SORT_PARSED_DATE = 0
_LONG_SORT_DIGITS = 1
_LONG_SORT_LEX = 2
# Within the short-stem tier: numeric stems before non-numeric.
_SHORT_SORT_NUMERIC = 0
_SHORT_SORT_NON_NUMERIC = 1


def is_dataverse_date_directory(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    return bool(DATAVERSE_DATE_DIR_RE.match(name.strip()))


def parse_account_name_from_connection_string(connection_string: str) -> str:
    """Extract ``AccountName`` from an Azure storage connection string."""
    if not connection_string or not str(connection_string).strip():
        raise ValueError("connection_string is required")
    for part in str(connection_string).split(";"):
        segment = part.strip()
        if not segment or "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        if key.strip().lower() == "accountname":
            account_name = value.strip()
            if account_name:
                return account_name
    raise ValueError("AccountName not found in connection_string")


@dataclass(frozen=True)
class AbfssLocation:
    """Parsed ``abfss://`` URI components."""

    container_name: str
    account_name: str
    blob_prefix: str = ""


def parse_abfss_uri(abfss_url: str) -> AbfssLocation:
    """
    Parse ``abfss://container@account.dfs.core.windows.net/prefix`` into components.

    ``blob_prefix`` has no leading or trailing slashes.
    """
    raw = str(abfss_url or "").strip()
    if not raw.startswith("abfss://"):
        raise ValueError(f"abfss URL must start with abfss://, got: {raw!r}")
    without_scheme = raw[len("abfss://") :]
    if "@" not in without_scheme:
        raise ValueError(f"Invalid abfss URL (missing @): {raw!r}")
    container_name, rest = without_scheme.split("@", 1)
    container_name = container_name.strip()
    account_host, _, path = rest.partition("/")
    account_host = account_host.strip()
    suffix = ".dfs.core.windows.net"
    if not account_host.endswith(suffix):
        raise ValueError(f"Invalid abfss account host in URL: {raw!r}")
    account_name = account_host[: -len(suffix)].strip()
    if not container_name or not account_name:
        raise ValueError(f"Invalid abfss URL (empty container or account): {raw!r}")
    blob_prefix = path.strip("/")
    return AbfssLocation(
        container_name=container_name,
        account_name=account_name,
        blob_prefix=blob_prefix,
    )


def build_abfss_uri(
    container_name: str,
    account_name: str,
    blob_path: str = "",
) -> str:
    """Build an ``abfss://`` URI for ADLS Gen2 (no trailing slash)."""
    container = str(container_name or "").strip()
    account = str(account_name or "").strip()
    if not container or not account:
        raise ValueError("container_name and account_name are required for abfss URI")
    base = f"abfss://{container}@{account}.dfs.core.windows.net"
    path = join_azure_blob_path(blob_path)
    return f"{base}/{path}" if path else base


def join_azure_blob_path(*parts: Optional[str]) -> str:
    """Join non-empty path segments with single slashes (no leading/trailing slash)."""
    segs: List[str] = []
    for p in parts:
        if p is None:
            continue
        s = str(p).strip().strip("/")
        if s:
            segs.append(s)
    return "/".join(segs)


def first_date_directory_in_blob_path(blob_name: str) -> Optional[str]:
    """Return the first path segment that looks like a Dataverse date folder, if any."""
    if not blob_name:
        return None
    for part in blob_name.split("/"):
        if is_dataverse_date_directory(part):
            return part
    return None


def csv_blob_basename(blob_name: str) -> str:
    """Last path segment lowercased, e.g. '2.csv'."""
    return (blob_name or "").rsplit("/", 1)[-1].lower()


def is_dataverse_delete_export_csv(blob_name: str) -> bool:
    """True for delete-only export shards (e.g. 2.csv)."""
    return csv_blob_basename(blob_name) in DATAVERSE_DELETE_CSV_BASENAMES


def _csv_numeric_stem(blob_name: str) -> Optional[int]:
    """Numeric stem of a .csv blob name, or None if not purely digits."""
    base = csv_blob_basename(blob_name)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    if not stem.isdigit():
        return None
    return int(stem)


def is_single_digit_dataverse_csv_shard(blob_name: str) -> bool:
    """True for 0.csv–9.csv (one-digit stem); False for 1900.csv, 10.csv, etc."""
    base = csv_blob_basename(blob_name)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return len(stem) == 1 and stem.isdigit()


def is_four_digit_dataverse_csv_shard(blob_name: str) -> bool:
    """True for 1900.csv, 2025.csv, etc. (exactly four-digit numeric stem)."""
    base = csv_blob_basename(blob_name)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return len(stem) == 4 and stem.isdigit()


def _csv_stem(blob_name: str) -> str:
    """Lowercased filename stem without extension."""
    base = csv_blob_basename(blob_name)
    return base.rsplit(".", 1)[0] if "." in base else base


def _try_parse_long_stem_as_date(stem: str, rules: DataverseCsvShardSortRules) -> Optional[date]:
    """
    Best-effort calendar date for bulk / export-style filenames under a table folder.

    Supported shapes (examples):
      2026.csv, 1900.csv — year only
      2026-01.csv — year-month
      2026-01-15.csv — ISO date
      202601.csv — YYYYMM when year/month are plausible (bounds from ``rules``)
      20260115.csv — YYYYMMDD
      010226.csv — MMDDYY when YYYYMM does not apply (century pivot from ``rules``)
    """
    s = (stem or "").strip().lower()
    if not s:
        return None

    m = _DATAVERSE_LONG_STEM_ISO_DATE_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    m = _DATAVERSE_LONG_STEM_ISO_YEAR_MONTH_RE.match(s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        try:
            return date(y, mo, 1)
        except ValueError:
            return None

    if s.isdigit():
        if len(s) == 4:
            y = int(s)
            try:
                return date(y, 1, 1)
            except ValueError:
                return None
        if len(s) == 6:
            y_hi, m_hi = int(s[:4]), int(s[4:6])
            if rules.yyyy_mm_year_min <= y_hi <= rules.yyyy_mm_year_max and 1 <= m_hi <= 12:
                try:
                    return date(y_hi, m_hi, 1)
                except ValueError:
                    return None
            mo_lo, dd_lo, yy_lo = int(s[:2]), int(s[2:4]), int(s[4:6])
            if 1 <= mo_lo <= 12 and 1 <= dd_lo <= 31:
                pivot = rules.mmddyy_two_digit_year_pivot
                y_full = 2000 + yy_lo if yy_lo < pivot else 1900 + yy_lo
                try:
                    return date(y_full, mo_lo, dd_lo)
                except ValueError:
                    return None
            return None
        if len(s) == 8:
            y, mo, d = int(s[:4]), int(s[4:6]), int(s[6:8])
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


def _long_stem_sort_key(stem: str, rules: DataverseCsvShardSortRules) -> tuple:
    """Ordering within the first tier (stem length >= ``rules.bulk_or_date_stem_min_len``)."""
    s = (stem or "").strip().lower()
    parsed = _try_parse_long_stem_as_date(s, rules)
    if parsed is not None:
        return (_LONG_SORT_PARSED_DATE, parsed.toordinal(), 0, s)
    if s.isdigit():
        return (_LONG_SORT_DIGITS, int(s), 0, s)
    return (_LONG_SORT_LEX, 0, 0, s)


def dataverse_csv_processing_order_key(
    blob_name: str,
    rules: DataverseCsvShardSortRules = DEFAULT_DATAVERSE_CSV_SORT_RULES,
) -> tuple:
    """
    Sort key for CSV blobs in one date/table folder.

    Two groups (see ``rules.bulk_or_date_stem_min_len``, default 4):

      1. **Stem length ≥ threshold** — year / year-month / full-date style names first:
         chronological when the stem parses as a calendar date, otherwise long
         all-digit stems by integer value, otherwise lexically by stem.
      2. **Stem length < threshold** — sorted: purely numeric stems by value (0, 1, 2, …,
         10, …), then other names lexically by basename.

    Delete vs final shard detection (``is_dataverse_delete_export_csv``, flush logic in
    migration) is unchanged; only **processing order** follows the two groups above.
    """
    base = csv_blob_basename(blob_name)
    stem = _csv_stem(blob_name)
    num = _csv_numeric_stem(blob_name)

    if len(stem) >= rules.bulk_or_date_stem_min_len:
        return (_TIER_LONG_STEM, *_long_stem_sort_key(stem, rules))

    if num is not None:
        return (_TIER_SHORT_STEM, _SHORT_SORT_NUMERIC, num, "")

    return (_TIER_SHORT_STEM, _SHORT_SORT_NON_NUMERIC, 0, base)


def infer_dataverse_base_folder_from_paths(paths: Iterable[str]) -> str:
    """
    Infer org-level base folder (segment(s) before the first date directory).

    Falls back to longest common prefix by *path segments* (not characters).
    """
    paths_list = [p for p in paths if p]
    if not paths_list:
        raise ValueError("No blobs found in the container to infer a base folder.")

    bases = set()
    for path in paths_list:
        parts = [x for x in path.split("/") if x]
        for i, part in enumerate(parts):
            if is_dataverse_date_directory(part):
                bases.add("/".join(parts[:i]) if i > 0 else "")
                break

    if len(bases) == 1:
        return bases.pop()

    # Multiple or no date segments found: use segment-wise longest common prefix
    split_paths = [[x for x in p.split("/") if x] for p in paths_list]
    if not split_paths:
        return ""
    min_len = min(len(p) for p in split_paths)
    common: List[str] = []
    for i in range(min_len):
        seg = split_paths[0][i]
        if not all(len(p) > i and p[i] == seg for p in split_paths):
            break
        common.append(seg)
    return "/".join(common)


def list_dataverse_date_folder_names(
    container_client,
    *,
    base_folder: Optional[str] = None,
    name_prefix: Optional[str] = None,
) -> set:
    """
    List Dataverse export date-folder names using hierarchical delimiter listing.

    Uses ``walk_blobs(..., delimiter='/')`` so Azure returns virtual directory
    prefixes (one per export timestamp) instead of every CSV/blob under them.
    Typically orders of magnitude faster than iterating all blobs.
    """
    if name_prefix:
        list_prefix = join_azure_blob_path(base_folder, name_prefix)
    elif base_folder:
        list_prefix = join_azure_blob_path(base_folder)
    else:
        list_prefix = ""

    if list_prefix and not list_prefix.endswith("/"):
        list_prefix = list_prefix + "/"

    date_directories: set = set()
    walker = container_client.walk_blobs(
        name_starts_with=list_prefix or None,
        delimiter="/",
    )
    for item in walker:
        name = getattr(item, "name", "") or ""
        folder = first_date_directory_in_blob_path(name)
        if folder and is_dataverse_date_directory(folder):
            date_directories.add(folder)
    return date_directories
