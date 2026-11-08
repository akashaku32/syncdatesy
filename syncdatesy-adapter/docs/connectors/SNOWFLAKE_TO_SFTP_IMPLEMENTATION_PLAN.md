# Snowflake -> SFTP (Reverse ETL Export) Implementation Plan

This document is a step-by-step checklist to add a new connection type to this app:

- **Source:** Snowflake (tables/views)
- **Destination:** SFTP
- **Behavior:** Extract data from Snowflake and export to files (`CSV`, `TSV`, `JSON`, `AVRO`), then upload those files to an SFTP server.

The plan is organized so you can complete items **one by one**. Each step includes what to implement, where it typically lives in this repo, and what to verify.

---

## 0) Define Scope + Decisions (Do This First)

Before coding, lock these decisions so backend + UI align:

1. **JSON format**
   - Recommended: `NDJSON` (one JSON object per line) for large exports.
   - Alternative: `JSON array` (single array) is memory-heavy and easy to break on big tables.
2. **File naming**
   - Decide default pattern. Recommended:
     - `{connection_name}_{connection_id}_{table}_{migration_session_id}_{timestamp}.{ext}`
3. **Overwrite vs. append**
   - Recommended default: do **not** overwrite existing files; always write a new file with timestamp/session id.
4. **Avro library**
   - Recommended: `fastavro`.
5. **SFTP auth types**
   - Minimum: `username + password`.
   - Strongly recommended: also support **keypair** (private key + optional passphrase).
6. **SSH tunnel support**
   - Decide if tunnel is required for v1 or optional. This document includes it as optional but supported.
7. **Export boundaries**
   - Decide if the export is:
     - **One file per selected table/view** (recommended for v1), or
     - Multiple tables merged into one file (not recommended for v1).

Deliverable for this step:
- A short note in the team ticket / PR description listing these decisions.

---

## 1) Connection Setup Form (UI + Attributes) - Required Fields

### 1.1 Add new Destination: `SFTP`

Create a new destination entry so UI can create a `DestinationConfig` for it.

Where this usually belongs:
- Django DB tables: `DestinationModel`, `DestinationAttribute`, `DestinationConfig`.

Checklist:
- Add destination record: `DestinationModel(name="SFTP")`.
- Add destination attributes (fields) for the SFTP form (see below).
- Ensure `DestinationConfig` encryption behavior is used (already exists in `apps/elttool/models.py`).

### 1.2 SFTP Destination Form Fields (Must Match Your Requirement)

In the connection setup form, collect:

**SFTP Server**
- `sftp_host` (SFTP Server Host Address) (string)
- `sftp_port` (SFTP Server Port) (int, default 22)
- `sftp_username` (SFTP Server Username) (string)
- `sftp_password` (SFTP Server Password) (string, optional if keypair is used)

**Authentication Mode (Needed for Your Requirement)**
- `auth_mode` (enum): `password` | `keypair`
- If `auth_mode = keypair`:
  - `sftp_private_key` (string, PEM)
  - `sftp_passphrase` (string, optional)

**Remote Pathing (Needed for Your Requirement)**
- `remote_directory` (string)
  - This is the folder where the exported files will be uploaded.

**Optional: Base Folder Path (As Requested)**
- `base_folder_path` (string, optional)
  - Meaning in our app:
    - The lowest common folder prefix we will treat as the target root.
    - For v1, we can interpret it as an additional path prefix under `remote_directory`, or we can treat it as the primary remote root and derive subfolders below it.
  - Example:
    - If uploads should land under:
      - `files/exports/customers/...`
      - `files/exports/products/...`
    - Set `base_folder_path = files/exports`

**Recommended Additional SFTP Fields (Mentioned Because They Matter In Production)**
- `connect_timeout_seconds` (int, default 30)
- `strict_host_key_check` (bool, default false for v1, but see security note below)
- `known_hosts` (string, optional; if strict checking is enabled)
- `remote_file_mode` (string, optional; e.g. `0644`)

Security note:
- For v1 you can ship with `strict_host_key_check=false` (easier onboarding), but plan to add host key pinning quickly.

### 1.3 SSH Tunnel Fields (If Connection Method = "Connect via an SSH tunnel")

Add a `connection_method` field:
- `direct`
- `ssh_tunnel`

If `ssh_tunnel`, collect:

**SSH Tunnel**
- `ssh_tunnel_host` (SSH Tunnel Host Address) (string)
- `ssh_tunnel_port` (SSH Tunnel Port) (int, default 22)
- `ssh_tunnel_username` (SSH Tunnel Username) (string)

Recommended additional tunnel fields (practical needs):
- `ssh_tunnel_auth_mode`: `password` | `keypair`
- If `password`:
  - `ssh_tunnel_password`
- If `keypair`:
  - `ssh_tunnel_private_key`
  - `ssh_tunnel_passphrase` (optional)

Important DNS/IP note (verbatim requirement):
- If you enter **DNS instead of IP** for `sftp_host`, you must have **IP-to-DNS mapping** for the SFTP server in the tunnel host’s `/etc/hosts`, OR the name must resolve to IP using an internal DNS server.

---

## 2) Data Export Settings (Destination Options)

These are required to meet the “convert view data to file formats” part.

Add destination config fields:

1. `file_format` (enum): `csv` | `tsv` | `json` | `avro` | `parquet`
2. `include_header` (bool, default true) (CSV/TSV only)
3. `json_mode` (enum): `ndjson` | `array` (default `ndjson`)
4. `compression` (enum): `none` | `gzip` (optional for v1; recommended)
5. `file_name_template` (string, optional)
6. `subfolder_template` (string, optional)
   - Example: `{table}/dt={yyyy}-{mm}-{dd}/`

### 2.1 CSV-specific options (Show Only When `file_format=csv`)

When the user selects **CSV**, expose additional CSV writer settings. This solves the scenario where Snowflake values like `rahul,tr` must be written safely without breaking columns.

Add CSV config fields:

1. `csv_delimiter` (string, default `,`)
2. `csv_quotechar` (string, default `"`)
3. `csv_quoting` (enum, default `minimal`)
   - Values aligned to Python `csv` module: `minimal`, `all`, `nonnumeric`, `none`
4. `csv_escapechar` (string, optional)
   - Used when `csv_quoting=none` or when you need explicit escaping.
5. `csv_doublequote` (bool, default true)
6. `csv_lineterminator` (string, optional, default `\n`)
7. `csv_encoding` (string, default `utf-8`)
8. `csv_null_string` (string, optional)
   - Example: empty string, or `NULL`

UI behavior:
- Render these fields only when `file_format` is `csv`.
- Show a short helper note near delimiter/quotechar explaining that commas inside values are handled by quoting, not by changing the delimiter (unless the user explicitly needs a different delimiter).

Validation rules:
- `csv_delimiter` must be exactly 1 character.
- `csv_quotechar` must be exactly 1 character.
- If `csv_quoting=none`, strongly recommend `csv_escapechar` is set (or enforce it).

### 2.2 Parquet options (Show Only When `file_format=parquet`)

Parquet support typically requires `pyarrow` (recommended) or `fastparquet`.

Add Parquet config fields (minimal v1):
1. `parquet_compression` (enum, default `snappy`): `snappy` | `gzip` | `zstd` | `none`
2. `parquet_row_group_size` (int, optional)
3. `parquet_coerce_timestamps` (bool, optional; if needed for Snowflake timestamp handling)

Implementation note:
- Export pipeline for Parquet usually becomes: Snowflake rows -> Arrow Table -> Parquet write.

Verification for this step:
- UI shows these options when destination = `SFTP`.
- Saving the connection persists these settings in `DestinationConfig`.
- Sensitive fields are not shown in logs and are encrypted at rest (same as other destinations).

---

## 3) Backend Contract (Django -> FastAPI -> migration-function)

This repo uses:
- Django (`apps/elttool`) for orchestration + UI API
- FastAPI gateway (`backend/fast-api/app.py`) for forwarding to the migration backend
- migration-function (`backend/migration-function`) for connector execution

We will follow the same pattern used by existing Snowflake migrations and by `Snowflake-AmazonS3-migration-yield`.

### 3.1 Add a new migration-yield path in FastAPI gateway

File:
- `backend/fast-api/app.py`

Checklist:
- Add `CLOUD_FUNCTION_URLS` mapping for:
  - `/Snowflake-SFTP-migration-yield` -> `get_api_url('Snowflake-SFTP-migrate')`
- Add endpoint:
  - `POST /Snowflake-SFTP-migration-yield`
  - It should call `call_function_for_migration(...)` exactly like other migration-yield endpoints.

Verification:
- Local request to FastAPI gateway returns a forwarded response (even if mocked).

### 3.2 Add a Django routing decision for Snowflake->SFTP

Where:
- Usually in `apps/elttool/v1/views.py` where migrations are triggered.

Checklist:
- When `source.name == "Snowflake"` AND `destination.name == "SFTP"`:
  - Construct payload with:
    - `source_config` (Snowflake)
    - `destination_config` (SFTP + export settings)
    - `selected_tables` (selected Snowflake tables/views)
    - `attribute_dict`, `folderpath_dict` if your contract requires them
    - `connection_id`, `cmp_id`, `user_id`, `session`, `migration_session_id`, `chunk_count`, `safety_interval`, `logs_creation`, etc.
  - Send to FastAPI gateway endpoint:
    - `/Snowflake-SFTP-migration-yield`

Verification:
- A connection with Snowflake source + SFTP destination triggers the new route.

---

## 4) migration-function: Add New Endpoint `/Snowflake-SFTP-migrate`

### 4.1 Create request model

Where:
- `backend/migration-function/models/`

Checklist:
- Add a Pydantic model similar to existing migration requests (see Snowflake and S3 models).
- Ensure request supports:
  - `source_config` (Snowflake credentials + db/schema + warehouse/role)
  - `destination_config` (SFTP fields + export settings + tunnel fields)
  - `selected_tables` list (tables/views)
  - Context ids: `connection_id`, `cmp_id`, `user_id`, `migration_session_id`, `session`
  - Logging controls: `logs_creation`

### 4.2 Create router

Where:
- `backend/migration-function/routers/`

Checklist:
- Add a new router file (example): `sftp_router.py`.
- Implement:
  - `POST /Snowflake-SFTP-migrate`
  - Validate request and call service method.
- Add router registration in `backend/migration-function/app.py` (where other routers are included).

Verification:
- `POST /Snowflake-SFTP-migrate` returns 200 with a stub result (before full implementation).

---

## 5) migration-function: Implement SFTP Client

### 5.1 Add dependency

File:
- `backend/migration-function/requirements.txt`

Checklist:
- Add `paramiko` (SFTP/SSH).
- Add Avro library if needed:
  - `fastavro`

Verification:
- migration-function container / env installs requirements successfully.

### 5.2 Implement `SFTPService`

Where:
- `backend/migration-function/services/sftp_service.py` (new)

Checklist:
- Connect in two modes:
  - Direct SFTP connection to `sftp_host:sftp_port`
  - Through SSH tunnel:
    - Connect to tunnel host
    - Open channel/forwarding to the SFTP server host+port
    - Create SFTP client over that channel
- Auth support:
  - Password
  - Keypair (private key + optional passphrase)
- Safety:
  - Upload via temp filename (e.g. `.partial`) then rename to final name.
  - Ensure remote directories exist (recursive mkdir).
  - Redact secrets in errors/logs.

Recommended output:
- `upload_file(local_path, remote_path) -> {bytes_uploaded, remote_path}`

Verification:
- Unit tests with paramiko mocked confirm:
  - correct connection selection (direct vs tunnel)
  - upload uses temp then rename
  - directory creation is attempted

---

## 6) migration-function: Implement Snowflake -> File Export

### 6.1 Snowflake read strategy

Checklist:
- Read from Snowflake tables or views with `SELECT * FROM <object>`.
- Use chunked fetching to avoid memory blowups.
- Respect `chunk_count` if it exists in your request contract.

Verification:
- A large result set does not build a full in-memory list.

### 6.2 File writers (CSV/TSV/JSON/AVRO)

Where:
- `backend/migration-function/services/` (new module recommended)
  - Example: `export_writers.py`

Checklist per format:
- CSV:
  - Python `csv` module
  - configurable delimiter `,`
  - `include_header` support
  - configurable `quotechar`, `escapechar`, and quoting mode
- TSV:
  - Same as CSV, delimiter `\t`
- JSON:
  - Default `ndjson`: one record per line
  - Optional `array` mode if enabled
- AVRO:
  - Use `fastavro`
  - Derive schema from Snowflake cursor metadata
  - Map Snowflake types to Avro types (with nullability)
- Parquet:
  - Use `pyarrow` (recommended)
  - Convert row batches to Arrow and write parquet with selected compression

Verification:
- For each format:
  - file is created locally
  - row count matches exported rows
  - file can be opened/parsed by a standard reader

---

## 7) migration-function: Implement `Snowflake -> SFTP` Orchestration Service

Where:
- `backend/migration-function/services/snowflake_to_sftp_service.py` (new)

Checklist:
1. Validate destination config (SFTP + export options).
2. Create Snowflake connection using existing Snowflake service patterns.
3. For each selected table/view:
   - Export to local temp file in chosen format
   - Upload to SFTP under:
     - `remote_directory` + optional `base_folder_path` + optional `subfolder_template`
   - Emit per-table success/failure result
4. Clean up local temp files (always, even on failure).

Verification:
- A single selected view produces exactly one file on SFTP.
- Multiple tables produce multiple files.
- Failures do not leak secrets.

---

## 8) Logging, Status Updates, and Websocket Progress

This app already has patterns where migration-function triggers updates back to Django and websockets.

Checklist:
- For each table/view:
  - status transitions: queued -> running -> completed/failed
  - write a useful log message at start and end
  - include row count + remote path in success message (no secrets)
- On terminal error:
  - write a clear failure log with a redacted error message

Verification:
- Connection activity and table statuses update in UI in near real-time.

---

## 9) Django: Ensure Connection Wizard Can Select Views (Snowflake Source)

Your requirement says “Snowflake as View” specifically. Snowflake listing already exists.

Checklist:
- Confirm listing-tables API includes views (or add an option to include views).
- Ensure selecting a “view” is stored in `SourceTableSelection` same as tables.
- Ensure migration request uses the selected list from DB.

Verification:
- A Snowflake view appears in listing and can be selected and migrated.

---

## 10) Tests (Minimum Safe Set)

### 10.1 migration-function tests

Where:
- `backend/migration-function/tests/`

Checklist:
- Endpoint test:
  - POST `/Snowflake-SFTP-migrate` with mocked Snowflake cursor + mocked SFTP client
  - assert upload called with correct remote path and file extension
- Writer tests:
  - CSV/TSV/NDJSON writers produce expected output for a small dataset
- Tunnel connection tests:
  - ensure tunnel mode uses the correct connection path (mocked paramiko)

### 10.2 Django tests (if test suite exists for these modules)

Checklist:
- Destination config validation:
  - direct mode requires SFTP host/port/user + either password or keypair
  - tunnel mode requires tunnel fields too
- Routing:
  - Snowflake + SFTP chooses `/Snowflake-SFTP-migration-yield`

---

## 11) Rollout + Backward Compatibility

Checklist:
- Feature flag (optional but recommended):
  - Allow enabling SFTP destination per environment.
- Avoid breaking existing connectors:
  - Keep new fields isolated to SFTP destination.
- Observability:
  - Ensure errors are actionable and redacted.
  - Add clear per-table success logs.

Verification:
- Existing migrations still work.
- New SFTP connector is visible and functional when enabled.

---

## Appendix A: Final Field List Summary (SFTP Destination)

Required for v1:
- `sftp_host`
- `sftp_port`
- `sftp_username`
- `auth_mode` (`password` or `keypair`)
- `sftp_password` (required if `auth_mode=password`)
- `sftp_private_key` (required if `auth_mode=keypair`)
- `sftp_passphrase` (optional)
- `remote_directory`
- `file_format` (`csv|tsv|json|avro|parquet`)

If `file_format=csv`:
- `csv_delimiter`
- `csv_quotechar`
- `csv_quoting`
- `csv_escapechar` (optional but recommended depending on quoting)
- `csv_doublequote`
- `csv_lineterminator`
- `csv_encoding`
- `csv_null_string` (optional)

If `file_format=parquet`:
- `parquet_compression`
- `parquet_row_group_size` (optional)
- `parquet_coerce_timestamps` (optional)

If `connection_method=ssh_tunnel`:
- `ssh_tunnel_host`
- `ssh_tunnel_port`
- `ssh_tunnel_username`
- `ssh_tunnel_auth_mode` (`password|keypair`)
- `ssh_tunnel_password` (if tunnel auth mode is password)
- `ssh_tunnel_private_key` (if tunnel auth mode is keypair)
- `ssh_tunnel_passphrase` (optional)

Optional but recommended:
- `base_folder_path`
- `include_header` (CSV/TSV)
- `json_mode` (`ndjson|array`)
- `compression` (`none|gzip`)
- `file_name_template`
- `subfolder_template`
- `connect_timeout_seconds`
- `strict_host_key_check`
- `known_hosts`
