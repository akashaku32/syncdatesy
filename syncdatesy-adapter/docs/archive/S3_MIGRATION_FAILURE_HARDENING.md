# S3 Migration Failure Hardening Plan

Date: 2026-03-16
Owner: DataSyncher Migration
Scope: S3 ? Snowflake (backend/migration-function/services/amazon_s3_service.py)

## Goals
Ensure that **any failure** during S3 migration results in:
- `source_table_selection` status = `failed`
- `migration_record` status = `failed`
- a **table-level error log** entry in `log`
- a **job-level log** where the existing “Migration in progress …” message is **rewritten to failed**
- no table or job left in `in_progress`

This applies to all operations:
- Full load
- Delta refresh
- Drop & reload
- Schema refresh
- Single-table (per-file)
- Multi-file single table

---

## Current Behavior (Confirmed)

### A) Cases already handled (mark failed properly)
1. **Removed/stale mapping for selected tables**
   - Path: `_mark_removed_mapping_table_failed()`
   - Effects:
     - `source_table_selection` updated to failed
     - `migration_record` updated to failed
     - `log` and `data_transfer` rows created
   - Result: Table-level failure recorded correctly

2. **Multi-file single-table: no files discovered**
   - Path: `multi_no_files_discovered` branch
   - Effects:
     - `handle_service_error` for table-level log
     - `_mark_selected_tables_failed_on_terminal_error()`
     - `log_migration_completion(... status_override='F')` (job log update)
   - Result: Proper table failure + job failure

3. **Top-level terminal exceptions**
   - Path: outer `except` with `_mark_selected_tables_failed_on_terminal_error()`
   - Effects:
     - table statuses failed
     - job-level failure log via `log_migration_completion`
   - Result: Proper job failure when exception bubbles up

4. **Per-table processing failures**
   - Path: inner table try/except
   - Effects:
     - `update_table_status_snowflake(... failed)`
     - `handle_service_error` logs
   - Result: Table-level failure recorded

---

## Current Gaps / Missing Edge Cases

### 1) Empty mapping or no files (single-table-per-file)
- Symptoms:
  - `mapping` empty or selected tables resolve to zero keys
  - job still shows “Migration in progress”
- Why it happens:
  - early return without forcing job-level failure log
  - table-level status may be updated, but job log not rewritten

### 2) No files to process (generic fall-through)
- Symptoms:
  - `No files to process` error
  - job log remains “Migration in progress”
- Why it happens:
  - early return without job-level failure log update

### 3) Delta refresh initiated / Drop & reload initiated
- Symptoms:
  - UI shows “delta refresh initiated” / “drop & reload initiated”
  - table status is failed, but log remains initiated/in-progress
- Why it happens:
  - table-level log message created but not updated on failure
  - `update_table_migration_status_log` not used consistently

### 4) Schema refresh initiated
- Symptoms:
  - “Update schema in progress…” remains after failure
- Why it happens:
  - schema refresh job log not updated on failure in some early returns

### 5) Partial failure where some tables fail before job is marked
- Symptoms:
  - failed table correctly marked
  - job log remains “in progress” instead of “partial/failed”
- Why it happens:
  - job-level completion log not always called on early returns

### 6) Errors inside mapping validation / parsing
- Symptoms:
  - mapping JSON invalid / parse errors
  - table failed but job log not updated
- Why it happens:
  - non-terminal error path doesn’t enforce job-level failure log

---

## Required Changes (Production-safe)

### A) Centralize failure finalization
Add a **single helper** for job-level failure finalization:

```
_finalize_job_failure(job_name, session, cmp_id, connection_id, user_id, reason, total_tables, failed_tables)
```

Responsibilities:
- Update job-level log using `log_migration_completion(... status_override='F')`
- Always rewrite existing “Migration in progress” log
- Trigger websocket updates

Use this helper in ALL early-return failure branches.

---

### B) Enforce table-level log updates on failure
Add **table-level log update** on any failure path:

- Use `update_table_migration_status_log(migration_record_id, message, status='F')`
- Ensure message reflects operation:
  - “Table X delta refresh failed”
  - “Table X drop & reload failed”
  - “Table X schema refresh failed”
  - “Table X migration failed”

---

### C) Ensure `migration_record` status set to failed for every failed table
Currently inconsistent in early-return paths. Add explicit status updates in:
- empty mapping
- no files
- schema validation failure
- mapping parse errors

---

### D) Prevent leaving any table in `in_progress`
Any `return {status: failed}` path must:
- call `_mark_selected_tables_failed_on_terminal_error()`
- or explicitly update `source_table_selection` + migration_record

---

## Safe Implementation Steps

1. **Add helpers**
   - `_finalize_job_failure(...)`
   - `_finalize_table_failure(...)`

2. **Wire into all early-return paths**
   - mapping empty
   - no files
   - multi-file discovery failure
   - mapping parse failure

3. **Upgrade all failure paths to update table logs**
   - call `update_table_migration_status_log` where missing

4. **Add tests or debug traces**
   - unit tests not present ? add runtime logs for now
   - log entry check for job log rewrite

---

## Validation Checklist

For each failure scenario, verify in DB:
- `source_table_selection.status = failed`
- `migration_record.status = failed`
- `log` contains latest entry with status `F`
- job log is **not** “Migration in progress”

---

## Rollout Safety
- Deploy behind a feature flag if possible (e.g., `S3_FORCE_FAIL_LOGGING=true`)
- Monitor:
  - job logs for duplicate “failed” entries
  - migration_record updates per job

---

## Notes / Observed Patterns
- `log_migration_completion` updates existing job-level log **only when** `migration_record_id` is `None`.
- Table-level logs require `migration_record_id` — missing IDs prevent updates.
- Some early-return branches exit before `log_migration_completion` is called.

---

## File References
- `backend/migration-function/services/amazon_s3_service.py`
- `backend/migration-function/utils/error_utils.py`
- `backend/migration-function/utils/database_utils.py`
