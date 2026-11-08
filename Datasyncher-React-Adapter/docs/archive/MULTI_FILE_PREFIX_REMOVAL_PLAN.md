# Multi-File Prefix Removal Plan

Date: 2026-03-10
Owner: Backend (S3 migration function)
Scope: S3 -> Snowflake pipeline; remove reliance on `multi_files_prefix` and extension-based filtering

## Goal
Stop using `multi_files_prefix` and file extensions for filtering. Always list all files under the selected prefix scope, then validate content against the user-selected file type and schema before processing.

## Non-Goals
- No change to S3 connection/authentication flow.
- No change to existing Snowflake load logic (COPY/MERGE semantics remain as-is).
- No UI redesign required beyond removing/ignoring `multi_files_prefix` inputs.

## Current Behavior (Problem)
- `multi_files_prefix` is auto-set to `*.{filetype}` and used to filter file keys.
- `filter_by_type()` filters by file extension.
- Files without extension or with different extensions are skipped even if content matches.

## Target Behavior (Required)
- Always list all keys in scope.
- Optional pattern filtering is removed.
- Content-based validation decides compatibility:
  - Binary formats: magic bytes (Parquet, Avro).
  - Text formats: minimal parse + structure checks (CSV/TSV/JSON/XML).
  - Schema validation remains the final gate.

## Step-by-Step Plan

### Step 1: Deprecate `multi_files_prefix`
- Backend:
  - Ignore `multi_files_prefix` in all flows.
  - Remove defaulting to `*.{filetype}`.
  - Keep field accepted in requests for backward compatibility but log a warning if set.
- Frontend:
  - Remove hard-coded pattern setting.
  - Stop sending `multi_files_prefix`.

### Step 2: Remove extension-based filtering
- Replace all `filter_by_type(all_keys, file_type)` usage with `all_keys`.
- Locations in `backend/migration-function/services/amazon_s3_service.py`:
  - `discover_new_matching_files`
  - `preview_pattern` (remove or return deprecated response)
  - `auto_refresh_multi_file_mapping`
  - PK suggestion flow
  - `list_files_only`
  - `list_tables_with_schema`
  - migration selection paths for `multi_files_single_table`
- Leave `filter_by_type` in `backend/migration-function/utils/s3_utils.py` for now, but mark as deprecated.

### Step 3: Add content-based type validation helper
- Add helper in `backend/migration-function/services/amazon_s3_service.py`:
  - `is_file_compatible_with_type(s3, bucket, key, file_type, options) -> (bool, reason)`
- Implementation rules:
  - Parquet: read first 4 bytes, expect `PAR1`.
  - Avro: read first 3 bytes, expect `Obj`.
  - JSON: first non-whitespace char is `{` or `[`.
  - XML: first non-whitespace char is `<`.
  - CSV/TSV: read N lines, check consistent column count with delimiter.
- Use minimal reads to avoid large payloads.

### Step 4: Apply validation in flows
- Listing/preview flows:
  - Return all files but include `compatible` flag + `reason` for incompatibility.
- Discovery/migration flows:
  - Only process files where `compatible == True`.
  - Log skipped files with reasons.
- Schema validation remains final gate using `validate_file_structure_matches`.

### Step 5: Update logs and response payloads
- Replace "files by type" counters with:
  - `total_files_scanned`
  - `compatible_files_count`
  - `skipped_files_count`
- If `multi_files_prefix` is provided, log: "ignored (deprecated)".

### Step 6: Tests
- Update or remove extension-based tests in:
  - `backend/migration-function/tests/test_s3_utils.py`
  - `backend/migration-function/tests/test_amazon_s3_service.py`
- Add new tests:
  - CSV content without `.csv` extension is accepted.
  - TSV content in `.txt` is accepted when file_type=tsv.
  - `.csv` extension with non-CSV content is rejected.
  - Parquet/Avro magic bytes recognized.

### Step 7: Documentation
- Update docs to explain:
  - `multi_files_prefix` is deprecated/ignored.
  - File type is validated by content, not extension.
  - User-selected format options are mandatory for correct parsing.

### Step 8: Rollout
- Optional feature flag (if needed): `S3_CONTENT_BASED_TYPE_SELECTION`.
- Enable in dev/test; validate logs; then enable in prod.

## Risks and Mitigations
- Risk: More files scanned -> higher S3 listing cost.
  - Mitigation: keep prefix scoping and optional subfolder control.
- Risk: False positives for text format detection.
  - Mitigation: schema validation remains the final gate.

## Acceptance Criteria
- Files without extension are discovered and processed if content matches.
- Files with wrong content are skipped with a clear reason.
- No reliance on `multi_files_prefix` or `filter_by_type`.

