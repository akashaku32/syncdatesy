# Requirements Verification: Multi-File to Single Table Mapping

## ✅ Requirement 1: User Maps Files Only Once During Connection Creation

**Status:** ✅ **IMPLEMENTED CORRECTLY**

**Implementation:**
- Location: `apps/elttool/v1/views.py` (lines 3562-3584)
- Mapping is saved to `S3Mapping` table during connection creation
- Table names are **immutable** after initial sync (cannot be changed)
- File list can be **extended** with new matching files (but not modified)
- Validation: `_validate_and_persist_s3_mapping()` enforces immutability

**Code Evidence:**
```python
# Multi-file mode is prefix-driven and resolves files dynamically.
```

---

## ✅ Requirement 2: New Matching Files Automatically Picked Up

**Status:** ✅ **IMPLEMENTED CORRECTLY**

**Implementation:**
- Location: `backend/migration-function/services/amazon_s3_service.py`
- Function: `discover_new_matching_files()` (lines 1360-1455)
- Automatically runs during migration (lines 3089-3102)
- Lists all files in S3, filters out existing files, validates structure
- Adds matching files to processing queue

**Code Evidence:**
```python
# Discover new matching files
new_files, final_schema = discover_new_matching_files(...)
if new_files:
    discovered_new_files.extend(new_files)
    # Add to processing queue
    selected_tables = list(selected_set | new_files_set)
```

---

## ✅ Requirement 3: Files Match Structure (Same Columns and File Type)

**Status:** ✅ **IMPLEMENTED CORRECTLY**

**Implementation:**
- Location: `validate_file_structure_matches()` (lines 1136-1222)
- Validates:
  - ✅ **File type** must match (filtered by `filter_by_type()`)
  - ✅ **All expected columns** must exist in file
  - ✅ **Additional columns** are allowed (will be added to schema)
  - ✅ **Missing columns** cause rejection

**Code Evidence:**
```python
# Check if all expected columns exist
missing_cols = expected_col_names - actual_col_names
if missing_cols:
    return False, actual_columns, f"Missing required columns: {missing_cols}"

# New columns are OK
new_cols = actual_col_names - expected_col_names
if new_cols:
    log(f"File has new columns: {new_cols} (will be added to schema)")
```

---

## ✅ Requirement 4: If New Column Added, Update Schema in SourceTableSelection

**Status:** ✅ **IMPLEMENTED CORRECTLY**

**Implementation:**
- Location: Lines 3108-3131
- Schema is updated in `SourceTableSelection.table_fields` when new columns are detected
- Updates happen for:
  - ✅ New columns found in **existing files** (via `validate_existing_files_for_schema_changes()`)
  - ✅ New columns found in **new files** (via `discover_new_matching_files()`)

**Code Evidence:**
```python
# Update schema in SourceTableSelection
if final_schema != table_metadata.get("table_fields", {}):
    update_sql = """
        UPDATE sourcetableselection
        SET table_fields = %s
        WHERE connection_id = %s AND "table" = %s
    """
    cursor_db.execute(update_sql, (table_fields_json, connection_id, table_name))
```

---

## ✅ Requirement 5: All Files Should Have That Column and Process Correctly

**Status:** ✅ **IMPLEMENTED CORRECTLY**

**Implementation:**
- **Schema Update Flow:**
  1. ✅ Existing files validated → new columns detected → schema updated
  2. ✅ New files discovered → new columns detected → schema merged
  3. ✅ Schema updated in `SourceTableSelection` **BEFORE** processing
  4. ✅ Snowflake table schema updated via `ensure_tables()` during processing (lines 1564-1572)

**Code Evidence:**
```python
# ensure_tables() adds new columns to Snowflake
for col_u, col_type in desired_cols.items():
    if col_u not in existing_main:
        sql = f'ALTER TABLE {main_table} ADD COLUMN "{col_u}" {col_type}'
        cur.execute(sql)
```

**Processing Flow:**
- Files are processed with updated schema
- `ensure_tables()` ensures Snowflake table has all columns
- All files (existing + new) use the same unified schema

---

## ✅ Requirement 6: Handle Updates to Existing Files

**Status:** ✅ **IMPLEMENTED CORRECTLY** (Added based on your question)

**Implementation:**
- Location: `validate_existing_files_for_schema_changes()` (lines 1265-1357)
- Validates **ALL existing files** for schema changes
- Detects new columns in existing files
- Merges new columns into schema before processing

**Code Evidence:**
```python
# STEP 1: Validate existing files for schema changes
updated_schema, files_with_changes = validate_existing_files_for_schema_changes(
    s3, s3_bucket, file_type, existing_files, expected_columns, ...
)

# Update schema if changed
if updated_schema != expected_columns:
    expected_columns = updated_schema
    schema_updated = True
```

---

## Complete Flow Verification

### ✅ Initial Connection Creation
1. User maps files → Saved to `S3Mapping`
2. Schema inferred → Saved to `SourceTableSelection.table_fields`
3. Mapping is immutable (table names cannot change)

### ✅ Subsequent Migrations
1. **Get existing mapping** from `S3Mapping`
2. **Get expected schema** from `SourceTableSelection`
3. **Validate existing files** for schema changes
4. **Discover new matching files**
5. **Update schema** in `SourceTableSelection` if changes found
6. **Process all files** (existing + new)
7. **Update Snowflake schema** via `ensure_tables()` during processing
8. **Update mapping** with newly processed files

---

## Summary

| Requirement | Status | Implementation |
|------------|--------|----------------|
| User maps once | ✅ | Immutability enforced in views.py |
| Auto-discover new files | ✅ | `discover_new_matching_files()` |
| Structure validation | ✅ | `validate_file_structure_matches()` |
| Schema update in DB | ✅ | Updates `SourceTableSelection.table_fields` |
| All files process correctly | ✅ | Schema updated before processing, `ensure_tables()` handles Snowflake |
| Handle existing file updates | ✅ | `validate_existing_files_for_schema_changes()` |

**All requirements are correctly implemented! ✅**

