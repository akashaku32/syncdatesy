# Multi-File to Single Table Mapping: New File Discovery & Schema Update Implementation

## Overview

This document explains how the system handles automatic discovery of new files and schema updates for **multi-file to single table** mapping in S3 to Snowflake migrations.

## Key Requirements

1. **User maps files only once** during connection creation
2. **New matching files are automatically discovered** and added to the mapping
3. **File structure validation** ensures only files with matching columns and file type are added
4. **Schema updates** when new columns are detected in new files
5. **All files must have the same structure** (same columns, same file type)

## Implementation Architecture

### 1. Mapping Immutability (Connection Creation)

**Location:** `apps/elttool/v1/views.py` - `SourceConnectionCreateUpdateAPIView`

**How it works:**
- When a user creates/updates a connection with `multi_files_single_table` mode:
  - The mapping is saved to `S3Mapping` table
  - Table names are **immutable** after initial sync (cannot be changed)
  - File list can be **extended** with new matching files (but not modified)

**Code Reference:**
```python
# If mapping exists and has data transfers, merge new files for multi-file mode
if existing_s3_mapping and has_data_transfers_flag and incoming_file_mapping_mode == "multi_files_single_table":
    existing_multi_map = {}
    # Merge: Keep existing files, add new files from incoming mapping
    merged_multi_map = {}
    for table_name, existing_files in existing_multi_map.items():
        incoming_files = incoming_multi_map.get(table_name, [])
        merged_files = list(set(existing_files + incoming_files))
        merged_multi_map[table_name] = merged_files
```

### 2. File Structure Validation

**Location:** `backend/migration-function/services/amazon_s3_service.py` - `validate_file_structure_matches()`

**How it works:**
1. Reads a sample (first 100 rows) from the candidate file
2. Infers column structure using `infer_columns_from_df()`
3. Compares actual columns with expected columns:
   - **Required:** All expected columns must exist in the file
   - **Optional:** New columns are allowed (will be added to schema)
   - **Metadata columns** (`_FILE`, `_ROW`, `_MODIFIED`, etc.) are excluded from comparison

**Validation Logic:**
```python
# Normalize column names for comparison
expected_col_names = {sanitize_column_name(k).upper() for k in expected_columns.keys()}
actual_col_names = {sanitize_column_name(k).upper() for k in actual_columns.keys()}

# Exclude metadata columns
metadata_cols = {"_FILE", "_ROW", "_MODIFIED", "_DS_SYNCED", "_DATA"}
expected_col_names = expected_col_names - metadata_cols
actual_col_names = actual_col_names - metadata_cols

# Check if all expected columns exist
missing_cols = expected_col_names - actual_col_names
if missing_cols:
    return False, actual_columns, f"Missing required columns: {missing_cols}"
```

### 3. Existing File Validation

**Location:** `backend/migration-function/services/amazon_s3_service.py` - `validate_existing_files_for_schema_changes()`

**How it works:**
1. Validates ALL existing files in the mapping for schema changes
2. Detects if any existing file has been updated with new columns
3. Merges new columns found in existing files into the schema
4. Logs which files have schema changes

**Why this is important:**
- If an old file gets updated with new columns, we need to detect it
- Schema must be updated before processing to ensure all files can be processed correctly
- All files must maintain the same structure (all required columns present)

### 4. New File Discovery

**Location:** `backend/migration-function/services/amazon_s3_service.py` - `discover_new_matching_files()`

**How it works:**
1. Lists all files in S3 matching the file type and prefix
2. Filters out already-mapped files
3. Validates each candidate file's structure
4. Collects matching files and merges any new columns found

**Discovery Process:**
```python
# List all files
all_keys = list_files_recursive(s3, s3_bucket, prefix)
filtered_keys = filter_by_type(all_keys, file_type)

# Filter out existing files
existing_set = set(existing_file_keys)
candidate_keys = [k for k in filtered_keys if k not in existing_set]

# Validate each candidate
for file_key in candidate_keys:
    is_valid, actual_columns, error_msg = validate_file_structure_matches(...)
    if is_valid:
        new_file_keys.append(file_key)
        updated_schema = merge_schema_columns(updated_schema, actual_columns)
```

### 5. Schema Merging

**Location:** `backend/migration-function/services/amazon_s3_service.py` - `merge_schema_columns()`

**How it works:**
- When a new file has additional columns:
  1. New columns are added to the schema
  2. Existing column definitions are preserved (to maintain consistency)
  3. If a column exists but types differ, existing type is kept (with warning)

**Schema Update Logic:**
```python
for col_name, col_meta in new_columns.items():
    col_name_upper = sanitize_column_name(col_name).upper()
    
    if col_name_upper not in merged:
        merged[col_name_upper] = col_meta  # Add new column
    else:
        # Preserve existing definition
        existing_type = merged[col_name_upper].get("data_type", "")
        new_type = col_meta.get("data_type", "")
        if existing_type != new_type:
            log(f"Type mismatch: keeping existing type")
```

### 6. Integration into Migration Flow

**Location:** `backend/migration-function/services/amazon_s3_service.py` - `_sync_amazon_s3_to_snowflake()`

**Migration Process:**
1. **Get existing mapping** from `S3Mapping` table
2. **Get expected schema** from `SourceTableSelection.table_fields`
3. **Validate existing files** for schema changes using `validate_existing_files_for_schema_changes()`
4. **Discover new matching files** using `discover_new_matching_files()` (using updated schema if changed)
5. **Update schema** in `SourceTableSelection` if any changes found (from existing or new files)
6. **Add new files to keys** to process
7. **Process all files** (existing + new) - Snowflake table schema updated automatically via `ensure_tables()`
8. **Update mapping** with newly processed files

**Integration Code:**
```python
# Get existing mapping
existing_mapping = get_s3_mapping(connection_id)
resolved_files_by_table = {}

# Get expected schema from SourceTableSelection
last_checked_data = get_last_checked_dates_and_fields(connection_id, table_names)
table_metadata = last_checked_data.get(table_name, {})
expected_columns = table_metadata.get("table_fields", {})

# Discover new matching files
if file_mapping_mode == "multi_files_single_table" and expected_columns:
    existing_files = resolved_files_by_table.get(table_name, [])
    new_files, updated_schema = discover_new_matching_files(
        s3, s3_bucket, s3_base_prefix, file_type,
        existing_files, expected_columns, file_mapping_mode,
        delimiter=delimiter, quotechar=quotechar, ...
    )
    
    # Add new files to keys to process
    keys.extend(new_files)
    
    # Update schema if new columns found
    if updated_schema != expected_columns:
        update_table_fields_in_database(connection_id, table_name, updated_schema)
```

### 6. Schema Update in Database

**Location:** `backend/migration-function/services/amazon_s3_service.py` - `refresh_and_update_s3_tables_in_database()`

**How it works:**
- Uses PostgreSQL `ON CONFLICT` to update existing table schema
- Updates `table_fields` in `SourceTableSelection` with merged schema
- Preserves existing column definitions when merging

**Database Update:**
```python
sql_upsert = """
    INSERT INTO source_table_selection 
        (connection_id, "table", table_fields, primary_keys, ...)
    VALUES (%s, %s, %s, %s, ...)
    ON CONFLICT (connection_id, "table")
    DO UPDATE SET 
        table_fields = EXCLUDED.table_fields,  -- Updates with merged schema
        ...
"""
```

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Connection Creation (User maps files once)              │
│    - User selects files and creates mapping                │
│    - Mapping saved to S3Mapping table                       │
│    - Schema inferred and saved to SourceTableSelection     │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Migration Starts                                         │
│    - Get existing mapping from S3Mapping                    │
│    - Get expected schema from SourceTableSelection          │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Discover New Files                                       │
│    - List all files in S3 (matching file type)             │
│    - Filter out already-mapped files                       │
│    - Validate each candidate file structure                │
│    - Collect matching files                                │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Schema Update (if new columns found)                     │
│    - Merge new columns into existing schema                │
│    - Update SourceTableSelection.table_fields              │
│    - Update Snowflake table schema (ALTER TABLE)           │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Process All Files                                        │
│    - Process existing files (delta sync)                   │
│    - Process new files (full load)                         │
│    - All files merged into single table                    │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Update Mapping                                           │
│    - Add newly processed files to S3Mapping                 │
│    - Update last_synced timestamp                          │
└─────────────────────────────────────────────────────────────┘
```

## Key Features

### ✅ Automatic Discovery
- New files are automatically discovered during migration
- No manual intervention required after initial mapping

### ✅ Structure Validation
- Files must have all expected columns (structure must match)
- Files with missing columns are rejected
- Files with additional columns are accepted (schema updated)

### ✅ Schema Evolution
- New columns are automatically added to schema
- Existing column definitions are preserved
- Snowflake table schema is updated automatically

### ✅ File Type Consistency
- Only files matching the configured file type are considered
- CSV files won't be mixed with JSON files, etc.

### ✅ Immutability Protection
- Table names cannot be changed after initial sync
- Mapping structure is preserved
- Only new files can be added (not removed or modified)

## Example Scenarios

### Scenario 1: New File with Same Structure
- **Initial mapping:** `file1.csv`, `file2.csv` → `table1`
- **New file:** `file3.csv` (same columns as file1/file2)
- **Result:** `file3.csv` automatically added to mapping, processed in next sync

### Scenario 2: New File with Additional Column
- **Initial mapping:** `file1.csv` (columns: A, B, C) → `table1`
- **New file:** `file2.csv` (columns: A, B, C, D)
- **Result:** 
  - `file2.csv` validated (has all required columns A, B, C)
  - Column D added to schema
  - `file2.csv` added to mapping
  - Schema updated in SourceTableSelection and Snowflake

### Scenario 3: New File with Missing Column
- **Initial mapping:** `file1.csv` (columns: A, B, C) → `table1`
- **New file:** `file2.csv` (columns: A, B) - missing C
- **Result:** `file2.csv` rejected, not added to mapping

### Scenario 4: Different File Type
- **Initial mapping:** `file1.csv`, `file2.csv` → `table1` (file_type: csv)
- **New file:** `file3.json`
- **Result:** `file3.json` filtered out (doesn't match file_type)

### Scenario 5: Existing File Updated with New Column
- **Initial mapping:** `file1.csv` (columns: A, B, C) → `table1`
- **Existing file updated:** `file1.csv` now has columns: A, B, C, D
- **Result:**
  - `file1.csv` validated during migration
  - Column D detected and added to schema
  - Schema updated in SourceTableSelection and Snowflake
  - All subsequent files (existing and new) will use updated schema

## Debug Logging

All operations are logged with prefixes:
- `[VALIDATE]` - File structure validation
- `[DISCOVER]` - New file discovery
- `[SCHEMA_MERGE]` - Schema merging operations
- `[MIGRATION]` - Migration flow

## Benefits

1. **Zero Manual Intervention:** New files are automatically discovered and processed
2. **Data Integrity:** Only files with matching structure are added
3. **Schema Evolution:** System handles new columns automatically
4. **Consistency:** All files in a mapping have the same structure
5. **Efficiency:** Only validates new files, not all files every time

