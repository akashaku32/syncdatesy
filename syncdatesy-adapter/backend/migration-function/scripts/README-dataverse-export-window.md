# Dataverse export window inspector

Script: `dataverse_export_window_inspector.py`

Lists Azure Blob paths under your Dataverse export layout **between two date-directory timestamps** (inclusive), optionally per table, optionally peeks CSV rows using the table’s `-model.json` column order (same as the migration worker), or **traces one record ID** across all CSV shards into a single combined DataFrame.

## Setup

From `migration-function` (or any env where `azure-storage-blob` and `pandas` are installed):

```bash
pip install azure-storage-blob pandas
# optional:
pip install python-dotenv
```

Run from `migration-function` so `utils.azure_path_utils` is importable for **base folder inference** when `base_folder` is omitted.

## Credentials (same as Dynamics `migrate()` source)

**Preferred:** JSON file with the same keys as `request.source_config` in `microsoft_dynamics_migration_service.py`:

- `connection_string` (Azure Storage connection string)
- `container_name`
- `base_folder` (optional org prefix; if omitted, inferred like `infer_base_folder()` from a sample of blob paths)

You can also wrap the same object under `source_config` to match a full API body shape.

Copy `source-config.example.json` to a **gitignored** file (e.g. `local-source.json`) and fill in real values. Do **not** commit secrets.

**Fallback:** environment variables `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_CONTAINER_NAME`, optional `AZURE_BASE_FOLDER`.

## Default table

`--table` defaults to **`ledgerjournaltrans`** (Ledger journal trans). Override with `--table someothertable` if needed.

## Examples

Using JSON (recommended):

```powershell
cd syncdatesy-adapter\backend\migration-function

python scripts\dataverse_export_window_inspector.py `
  --source-json .\local-source.json `
  --start "2026-05-19T22.59.08Z" `
  --end "2026-05-20T03.59.09Z" `
  --peek-rows 5000
```

Peek uses the default table `ledgerjournaltrans`. Another table:

```powershell
python scripts\dataverse_export_window_inspector.py `
  --source-json .\local-source.json `
  --start "2026-05-19T22.59.08Z" `
  --end "2026-05-20T03.59.09Z" `
  --table vendtrans `
  --peek-rows 5000
```

Download all blobs for the table under those directories:

```powershell
python scripts\dataverse_export_window_inspector.py `
  --source-json .\local-source.json `
  --start "2026-05-19T22.59.08Z" `
  --end "2026-05-20T03.59.09Z" `
  --download-dir .\export_window_dump
```

### Date directories from Postgres (same as Dynamics migration)

The migration worker stores export folders in **`date_directory.directories`** (`get_all_date_directories_from_db` / `insert_or_update_date_directories` in `microsoft_dynamics_migration_service.py`).

Requires DB env (same as migration worker): `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.

```powershell
python scripts\dataverse_export_window_inspector.py `
  --source-json .\local-source.json `
  --connection-id 3 `
  --date-dirs-from-db `
  --table ledgerjournaltrans `
  --lookup-id "000058e2-0000-0000-4620-005001000000" `
  --output-csv .\id_trace.csv
```

Optional: narrow the DB list with `--start` / `--end`, or apply migration safety cutoff with `--safety-interval-minutes 120`.

Per-table cursor only (one folder from `source_table_selection.last_synced`):

```powershell
python scripts\dataverse_export_window_inspector.py `
  --source-json .\local-source.json `
  --connection-id 3 `
  --table-last-checked-from-db `
  --table ledgerjournaltrans `
  --lookup-id "000058e2-0000-0000-4620-005001000000" `
  --output-csv .\id_trace.csv
```

### Trace one ID across CSV exports (migration-aligned)

Find every row for a GUID in `ledgerjournaltrans` (or any `--table`), across all CSV shards in each date folder, in **migration processing order** (`1900.csv` → … → `2.csv` delete → `1.csv` final). Output is one combined DataFrame; optional CSV export.

Narrow time window (recommended):

```powershell
python scripts\dataverse_export_window_inspector.py `
  --source-json .\local-source.json `
  --table ledgerjournaltrans `
  --lookup-id "000058e2-0000-0000-4620-005001000000" `
  --start "2025-11-26T20.59.09Z" `
  --end "2025-11-26T20.59.09Z" `
  --output-csv .\id_trace.csv
```

Scan all discovered date directories (can be slow on large containers):

```powershell
python scripts\dataverse_export_window_inspector.py `
  --source-json .\local-source.json `
  --table ledgerjournaltrans `
  --lookup-id "000058e2-0000-0000-4620-005001000000" `
  --output-csv .\id_trace.csv
```

Extra columns on each hit row:

| Column | Meaning |
|--------|---------|
| `_SOURCE_DATE_DIR` | Dataverse date folder |
| `_SOURCE_BLOB` | Full blob path |
| `_SOURCE_CSV_BASENAME` | e.g. `1900.csv`, `2.csv` |
| `_MIGRATION_PROCESSING_ORDER` | Sort index used by migration |
| `_IS_DELETE_CSV_SHARD` | `true` for `2.csv` |
| `_CSV_IS_DELETE_PARSED` | `IsDelete` value parsed from CSV |
| `_MIGRATION_FORCED_IS_DELETE` | Migration forces delete on `2.csv` shards |

For **end-to-end** “Azure hit vs Snowflake row” checks and pipeline drop points, see
`scripts/README-dynamics-id-full-pipeline-trace.md` and pytest `tests/test_dynamics_id_e2e_crosscheck.py`.

## JSON shapes

Flat:

```json
{
  "connection_string": "...",
  "container_name": "...",
  "base_folder": "dataverse-org-..."
}
```

Wrapped (like API body):

```json
{
  "source_config": {
    "connection_string": "...",
    "container_name": "..."
  }
}
```

## Timestamp format

Date folders in blobs use **dots** in the clock part, e.g. `2026-05-20T02.59.09Z`. You may type either `22:59` or `22.59`; the script normalizes to the dotted form.

## Performance note

Discovering directories scans blob names under `base_folder/` up to `--max-list-ops` (default 200000). Very large containers may need a narrower time window or a higher cap.
