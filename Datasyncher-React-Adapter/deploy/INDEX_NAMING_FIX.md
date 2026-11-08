# Database Index Naming Fix

## Problem Description

After removing the `elttool_` prefix from database tables and indexes, a naming mismatch occurred:

- **Tables** were renamed to use underscore naming: `source_model`, `source_attribute`, etc.
- **Indexes** were renamed to remove the prefix but kept the old camelCase naming: `sourcemodel_pkey`, `sourceattribute_name_key`, etc.

### Example of the Issue

```
Table Name:     source_model
Index Names:    sourcemodel_pkey          ❌ Mismatch!
                sourcemodel_name_key      ❌ Mismatch!
                sourcemodel_name_6eee4f7a_like  ❌ Mismatch!
```

**Expected:**
```
Table Name:     source_model
Index Names:    source_model_pkey         ✅ Matches!
                source_model_name_key     ✅ Matches!
                source_model_name_6eee4f7a_like  ✅ Matches!
```

## Why This Matters

### 1. **Query Performance**
- PostgreSQL indexes **still work functionally** even with mismatched names
- However, naming consistency helps:
  - Database administrators quickly identify which indexes belong to which tables
  - Query planners can more easily optimize when naming is predictable
  - Debugging query performance issues becomes easier

### 2. **Django ORM Compatibility**
- Django expects indexes to follow naming conventions based on table names
- Future migrations might try to recreate indexes if they don't match expected patterns
- `makemigrations` might generate unnecessary index creation/dropping operations

### 3. **Maintenance & Debugging**
- Consistent naming makes it easier to:
  - Identify indexes in query plans (`EXPLAIN ANALYZE`)
  - Write maintenance scripts
  - Document database schema
  - Onboard new developers

## Solution

A new migration (`0027_align_index_names_with_tables.py`) has been created to:

1. **Detect mismatched indexes**: Finds indexes that don't match their table naming convention
2. **Rename indexes**: Updates index names to match table names with underscores
3. **Handle all affected tables**: Covers all tables that were renamed during the prefix removal

### Affected Tables

The migration handles these table/index name mappings:

| Old Index Prefix | New Table Name | Example |
|-----------------|----------------|---------|
| `sourcemodel_` | `source_model` | `sourcemodel_pkey` → `source_model_pkey` |
| `sourceattribute_` | `source_attribute` | `sourceattribute_src_id_abc123` → `source_attribute_src_id_abc123` |
| `sourceconfig_` | `source_config` | `sourceconfig_cmp_id_9ba6ef_idx` → `source_config_cmp_id_9ba6ef_idx` |
| `sourcetableselection_` | `source_table_selection` | `sourcetableselection_connect_54b940_idx` → `source_table_selection_connect_54b940_idx` |
| `sourcedeltatable_` | `source_delta_table` | - |
| `communicationsupport_` | `communication_support` | - |
| `datedirectory_` | `date_directory` | - |
| `errorlogentry_` | `error_log_entry` | - |
| `tablemapping_` | `table_mapping` | - |
| `migrationrecord_` | `migration_record` | - |
| `scheduledjob_` | `scheduled_job` | - |
| `roleaccess_` | `role_access` | - |
| `destinationmodel_` | `destination_model` | - |
| `destinationattribute_` | `destination_attribute` | - |
| `destinationconfig_` | `destination_config` | - |
| `destinationtableselection_` | `destination_table_selection` | - |

## How Indexes Work in PostgreSQL

### What is an Index?

An index is a data structure that improves query performance by allowing the database to quickly locate rows without scanning the entire table.

### Index Types Used

1. **Primary Key Index (pkey)**: Automatically created for primary keys
   - Example: `source_model_pkey` on `src_id`
   - Ensures uniqueness and provides fast lookups

2. **Unique Index**: Ensures column values are unique
   - Example: `source_model_name_key` on `name`
   - Used for unique constraints

3. **B-tree Index**: Default index type for most queries
   - Example: `source_model_name_6eee4f7a_like` on `name`
   - Supports equality, range, and pattern matching queries

### How PostgreSQL Uses Indexes

1. **Query Planning**: PostgreSQL's query planner analyzes queries and decides whether to use an index
2. **Index Scan**: When an index is used, PostgreSQL:
   - Searches the index structure (much faster than table scan)
   - Retrieves row pointers
   - Fetches actual rows from the table

3. **Index Matching**: PostgreSQL matches indexes based on:
   - Column names (not index names)
   - Query predicates (WHERE clauses)
   - Join conditions

**Important**: Index names don't affect functionality, but consistent naming improves maintainability.

## Pros and Cons

### Pros of Matching Index Names to Table Names

✅ **Consistency**: Easier to identify which indexes belong to which tables  
✅ **Maintainability**: Clearer database schema documentation  
✅ **Django Compatibility**: Prevents migration conflicts  
✅ **Debugging**: Easier to read query plans and identify performance issues  
✅ **Team Collaboration**: New developers understand schema faster  

### Cons

❌ **Migration Time**: Renaming indexes requires a brief lock on each index (usually milliseconds)  
❌ **Temporary Downtime**: Very brief locks during rename operations  
❌ **Complexity**: Migration script needs to handle all edge cases  

**Note**: The cons are minimal - index renames are very fast operations in PostgreSQL.

## Running the Migration

```bash
# Apply the migration
python manage.py migrate elttool

# Verify the changes
python manage.py dbshell
```

Then in PostgreSQL:
```sql
-- Check index names for source_model table
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE schemaname = 'public' 
  AND tablename = 'source_model'
ORDER BY indexname;
```

Expected output:
```
indexname                          | indexdef
-----------------------------------+------------------------------------------
source_model_pkey                  | CREATE UNIQUE INDEX source_model_pkey ...
source_model_name_key              | CREATE UNIQUE INDEX source_model_name_key ...
source_model_name_6eee4f7a_like   | CREATE INDEX source_model_name_6eee4f7a_like ...
```

## Verification Query

Run this query to check for any remaining mismatches:

```sql
SELECT 
    pi.tablename,
    pi.indexname,
    CASE 
        WHEN pi.indexname LIKE pi.tablename || '_%' THEN '✅ Match'
        ELSE '❌ Mismatch'
    END as status
FROM pg_indexes pi
WHERE pi.schemaname = 'public'
  AND pi.indexname NOT LIKE 'pg_%'
ORDER BY pi.tablename, pi.indexname;
```

All rows should show "✅ Match" after running the migration.

## Rollback

If needed, you can rollback the migration:

```bash
python manage.py migrate elttool 0026_strip_elttool_prefix_from_indexes
```

This will revert index names back to the old camelCase format.

## Best Practices Going Forward

1. **Consistent Naming**: Always use the same naming convention for tables and their indexes
2. **Django Conventions**: Let Django generate index names when possible (it follows table naming)
3. **Manual Indexes**: When creating custom indexes, use the pattern: `{table_name}_{purpose}_{suffix}`
4. **Documentation**: Document any custom index naming conventions in your project

## Performance Impact

**Before Fix**: Indexes work but naming is inconsistent  
**After Fix**: Indexes work with consistent naming (no performance change)  
**Migration Impact**: Minimal - index renames are very fast (typically < 1ms per index)

The actual query performance remains the same because PostgreSQL uses index structure and column information, not index names, to optimize queries.

