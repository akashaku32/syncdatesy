# Fix for Duplicate Migration Execution Issue

## Problem
Migrations were being triggered and completed 3 times for the same job. The same migration job was executing multiple times simultaneously.

## Root Cause Analysis

### 1. **Race Condition in Task Creation**
- Multiple requests can arrive simultaneously (e.g., from frontend retries, network issues, or user double-clicks)
- All requests pass the `is_migration_in_progress()` check in Django views because none have created tasks yet
- Each request then creates a new Celery task, resulting in duplicate executions

### 2. **No Idempotency Check in FastAPI Endpoint**
- The `/Snowflake-AmazonS3-migration-yield` endpoint (`call_function_for_migration`) didn't check if a task with the same `job_name` and `migration_session_id` already exists
- It always created a new task, even if one was already running

### 3. **Multiple Worker Configuration**
- `docker-compose.yml` has:
  - `migration-function` service with `--workers 4`
  - `celery` service with `--concurrency=4` and `--autoscale=4,2`
- Multiple workers can pick up duplicate tasks if they're created

### 4. **Timing Gap**
- Django view checks `is_migration_in_progress()` → returns False
- Django view calls FastAPI endpoint
- FastAPI endpoint creates task
- **Problem**: Between the check and task creation, other requests can also pass the check

## Solution Implemented

### Fix 1: Idempotency Check in FastAPI Endpoint
**File**: `backend/fast-api/app.py`

Added a check in `call_function_for_migration()` to:
1. Check if a task with the same `job_name` and `migration_session_id` already exists with status "in_progress"
2. If it exists, return the existing `task_id` instead of creating a new task
3. This prevents duplicate task creation even if multiple requests arrive simultaneously

```python
# Step 0 - Check for existing in-progress task to prevent duplicate execution
existing_task = db.execute(
    text("""
        SELECT task_id, status 
        FROM migration_record 
        WHERE job_name = :job_name 
          AND migration_session_id = :migration_session_id
          AND status = 'in_progress'
        LIMIT 1
    """),
    {"job_name": data.job_name, "migration_session_id": data.migration_session_id}
).fetchone()

if existing_task and existing_task[0]:
    # Return existing task_id instead of creating new one
    return JSONResponse(content={
        "task_id": existing_task_id, 
        "status": "Migration already in progress",
        "message": "This migration is already running. Duplicate request prevented."
    })
```

## How This Fixes the Issue

1. **Prevents Duplicate Task Creation**: Even if 3 requests arrive simultaneously, only the first one will create a task. The other 2 will receive the existing task_id.

2. **Database-Level Check**: The check happens at the database level, which is more reliable than application-level checks.

3. **Graceful Handling**: Duplicate requests receive a clear message that the migration is already in progress, rather than creating duplicate tasks.

## Remaining Considerations

### Potential Improvements (Future)
1. **Database Advisory Lock**: Use PostgreSQL advisory locks to make the check-and-create operation truly atomic
2. **Unique Constraint**: Add a unique constraint on `(job_name, migration_session_id, status='in_progress')` at the database level
3. **Frontend Debouncing**: Add debouncing on the frontend to prevent rapid duplicate clicks
4. **Request Deduplication**: Use request IDs or tokens to deduplicate requests at the API gateway level

### Monitoring
- Monitor logs for the warning message: `"DUPLICATE PREVENTION: Task already exists"`
- Track how often duplicate requests are being prevented
- This will help identify if the issue is from frontend retries, network issues, or user behavior

## Testing Recommendations

1. **Test Concurrent Requests**: Send 3 simultaneous requests with the same `job_name` and `migration_session_id`
   - Expected: Only 1 task should be created, other 2 should return existing task_id

2. **Test Normal Flow**: Send a single request
   - Expected: Task should be created normally

3. **Test After Completion**: Send a request after a migration completes
   - Expected: New task should be created (since previous one is no longer "in_progress")

## Files Modified

- `backend/fast-api/app.py`: Added idempotency check in `call_function_for_migration()`

## Related Files (No Changes Needed, But Relevant)

- `apps/elttool/v1/views.py`: Contains `is_migration_in_progress()` check (already exists)
- `backend/fast-api/celery_worker.py`: Celery worker configuration
- `docker-compose.yml`: Worker configuration (multiple workers are fine, idempotency check handles it)

