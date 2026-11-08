# Message Filtering Bug Fixes

## Date: 2026-02-10

## Issues Identified and Fixed

### Issue 1: Non-Migration Logs Bypassing Status Filter ✅ FIXED

**Location:** `apps/elttool/v1/views.py` (ConnectionLogsAPIView)

**Problem:**
When filtering activity logs by status (failed/completed), non-migration logs like "Update schema", "Connector paused", etc. were still being displayed because they lack a `job_name` field.

**Root Cause:**
The filter logic only checked:
```python
if status_filter and log.job_name:
    # Check status
```

This meant logs without a `job_name` completely bypassed the status filter.

**Fix Applied:**
Updated the filter logic to explicitly exclude logs without a `job_name` when a status filter is active:

```python
if status_filter:
    # If status filter is applied, only include migration-related logs
    if log.job_name:
        # For logs with job_name, check if the job status matches the filter
        job_status = determine_job_status(log.job_name, connection_id)
        if job_status != status_filter.lower():
            should_include = False
    else:
        # Exclude logs without job_name (non-migration logs) when filtering
        should_include = False
```

**Result:**
- When filtering by "failed" or "completed", only migration-related logs are shown
- System logs like "Connector paused", "Update schema initiated" are correctly excluded

---

### Issue 2: Misleading "Migration Completed Successfully" Messages ✅ FIXED

**Location:** `backend/migration-function/utils/error_utils.py` (log_migration_completion function)

**Problem:**
The "failed" filter was showing messages that said "Migration completed successfully... X tables failed" which was confusing to users. While the filtering logic worked correctly, the message text was misleading.

**Root Cause:**
The `log_migration_completion` function always used the base message "Migration completed successfully" regardless of whether there were failures:

```python
base_message = "Migration completed successfully"
```

**Fix Applied:**
Updated the function to choose an appropriate base message based on the actual outcome:

```python
# Determine base message based on failures
final_failed = (failed_tables or 0) + (skipped_tables or 0)
final_successful = successful_tables or (total_tables - final_failed if total_tables else 0)
final_total = total_tables or (final_successful + final_failed)

# Choose appropriate base message
if final_total == 0 or final_failed == 0:
    base_message = "Migration completed successfully"
elif final_failed == final_total:
    base_message = "Migration failed"
else:
    base_message = "Migration completed"  # Partial success - don't say "successfully"
```

**Result:**
- Full success: "Migration completed successfully in X seconds..."
- Partial success: "Migration completed in X seconds... Tables: Y/Z successful, N failed."
- Complete failure: "Migration failed in X seconds... N table(s) failed."

The messages now accurately reflect the migration outcome.

---

## Testing Recommendations

1. **Test Status Filtering:**
   - Create a connection and run some migrations (some successful, some failed)
   - Add some system logs (pause connector, refresh schema)
   - Filter by "completed" - should only show successful migrations
   - Filter by "failed" - should only show failed/partial migrations
   - Verify system logs don't appear in either filter

2. **Test Message Accuracy:**
   - Run a migration where all tables succeed - verify message says "completed successfully"
   - Run a migration where some tables fail - verify message says "Migration completed" (not "successfully")
   - Run a migration where all tables fail - verify message says "Migration failed"

3. **Test Edge Cases:**
   - Migrations with no tables
   - Migrations with only skipped tables
   - Mixed scenarios with skipped + failed + successful tables

---

## Files Modified

1. `apps/elttool/v1/views.py`
   - Updated ConnectionLogsAPIView.get() method
   - Modified status filter logic to exclude non-migration logs

2. `backend/migration-function/utils/error_utils.py`
   - Updated log_migration_completion() function
   - Changed base message selection based on migration outcome
   - Removed duplicate calculation of table counts
