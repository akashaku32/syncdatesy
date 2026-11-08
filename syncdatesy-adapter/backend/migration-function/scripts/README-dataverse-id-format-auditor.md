# Dataverse ID format auditor

Script: `dataverse_id_format_auditor.py`

Scans **multiple** Dataverse export tables in Azure Blob storage and finds rows where the primary **ID** column is **not** a normal GUID.

Reports go to **`scripts/dataverse_id_audit_reports/conn<N>_<timestamp>/`** (one subfolder per table).

- **Per-table row cap:** old default was `--max-rows-per-table 100000`. **New default `0` = unlimited** (full scan).
- **Parallel:** `--parallel 10` audits 10 tables at once.
- **Anomaly rows** include `DATE_DIRECTORY`, `CSV_FILE_NAME`, `ID_VALUE`, and `SAMPLE_ROW_JSON`.

## Expected vs odd IDs

| Kind | Example |
|------|---------|
| Valid (hyphen GUID) | `000058e2-0000-0000-4620-005001000000` |
| Valid (32 hex, no hyphens) | `000058e2000000004620005001000000` |
| Odd (flagged) | Long text, empty, spaces, non-hex characters, wrong length |

Anomaly types written to CSV: `empty`, `too_long`, `whitespace`, `non_hex_chars`, `not_guid`.

## Setup

Same as the window inspector:

```bash
cd syncdatesy-adapter\backend\migration-function
pip install azure-storage-blob pandas python-dotenv
```

Use `--source-json` with `connection_string`, `container_name`, optional `base_folder` (or infer from blobs).

### Postgres (for `--tables-from-db` / `--date-dirs-from-db`)

Set **`DATABASE_URL`** (same value as `DATABASE_URL` in `k8s/secrets/secrets.yaml`):

```powershell
$env:DATABASE_URL = "postgresql://postgres:YOUR_PASSWORD@HOST:5432/datasyncher_kuberntest_ati_30_04"
```

Or `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, or `--database-url postgresql://...`.

## Quick start

**One table** (newest export folder only — fast):

```powershell
python scripts\dataverse_id_format_auditor.py `
  --source-json .\local-source.json `
  --connection-id 3 `
  --date-dirs-from-db `
  --tables ledgerjournaltable `
  --output-summary-csv .\id_audit_summary.csv `
  --output-samples-csv .\id_anomalies.csv
```

**Several tables**:

```powershell
python scripts\dataverse_id_format_auditor.py `
  --source-json .\local-source.json `
  --connection-id 3 `
  --date-dirs-from-db `
  --tables ledgerjournaltable,ledgerjournaltrans,custtable,vendtable `
  --output-summary-csv .\id_audit_summary.csv `
  --output-samples-csv .\id_anomalies.csv
```

**All selected tables for connection 3, every export folder from January 2026 onward (10 parallel, full scan):**

```powershell
python scripts\dataverse_id_format_auditor.py `
  --source-json .\local-source.json `
  --connection-id 3 `
  --date-dirs-from-db `
  --tables-from-db `
  --start "2026-01-01T00.00.00Z" `
  --parallel 10
```

Output example: `scripts\dataverse_id_audit_reports\conn3_20260525_120000\`

- `OVERALL_REPORT.txt` — which tables have bad IDs  
- `overall_summary.csv` / `overall_anomalies.csv`  
- `tables\ledgerjournaltable\anomalies.csv` — **DATE_DIRECTORY**, **CSV_FILE_NAME**, **ID_VALUE**, sample row JSON  
- `tables\ledgerjournaltable\report.txt` — human-readable summary  

With `--start` / `--end`, the script scans **all** date directories in that window (not only the latest folder). Use `--max-date-dirs 1` if you only want the newest folder inside the window.

**All tables registered in Postgres** for the connection (latest folder only):

```powershell
python scripts\dataverse_id_format_auditor.py `
  --source-json .\local-source.json `
  --connection-id 3 `
  --date-dirs-from-db `
  --tables-from-db `
  --max-date-dirs 1 `
  --output-summary-csv .\id_audit_summary.csv `
  --output-samples-csv .\id_anomalies.csv
```

Limit DB list to specific names:

```powershell
python scripts\dataverse_id_format_auditor.py ... `
  --tables-from-db `
  --tables ledgerjournaltable,ledgerjournaltrans
```

## Performance knobs

| Flag | Default | Meaning |
|------|---------|---------|
| `--max-date-dirs` | all dirs if `--start`/`--end`, else `1` | Cap to newest N folders |
| `--max-rows-per-table` | **`0` (unlimited)** | Row cap; `100000` = old quick sample mode |
| `--parallel` | **`10`** | Tables run at the same time |
| `--first-csv-only` | off | Set to scan only first CSV per date folder (faster) |
| `--max-rows-per-table` | `100000` | Row cap per table |
| `--max-samples-per-table` | `50` | How many bad-ID examples to save |
| `--scan-all-csvs` | off | Scan every CSV shard (not just the first) |
| `--all-date-dirs` | off | Scan every folder from DB (slow) |

## Outputs

**Summary CSV** (`--output-summary-csv`): one row per table — rows scanned, valid GUID count, anomaly count, breakdown by type.

**Samples CSV** (`--output-samples-csv`): example bad rows with `TABLE`, `ID_VALUE`, `ID_LENGTH`, `ANOMALY_TYPE`, source blob/folder.

Use `--only-anomalies` to exit with code `1` when any table has anomalies (useful in CI).

## Related script

`dataverse_export_window_inspector.py` — trace **one known GUID** across all folders/CSVs. Use the auditor when you do not know the ID and want to discover bad formats across tables.
