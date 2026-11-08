# Log Filtering - What Shows Where

## Your Current Logs (From Output)

Here's how your logs will be categorized:

### Logs That Will Show in Main Activity View (LEFT side)

| Message | Session | Why It Shows |
|---------|---------|--------------|
| Migration completed successfully in 415.11 seconds. Total rows: 34... | 399 | ✅ Summary - Shows overall migration result |
| Migration session 399 started - Rahul_Conn to Dest_Rahul. Tables: custgroup. Total tables: 1 | 399 | ✅ Summary - Session start |
| Warning: Another migration is already in progress | 0 | ✅ Summary - Warning message |
| Migration is already in progress. Please wait. | 399 | ✅ Summary - Warning message |
| Tables listed and updated successfully in 2239.24s | null | ✅ Summary - Schema operation |
| Update schema in progress | 398 | ✅ Summary - Schema operation |

### Logs That Will Be HIDDEN from Main Activity View

| Message | Session | Table | Why It's Hidden |
|---------|---------|-------|-----------------|
| Successfully migrated/updated 'custgroup' table, containing data with 34 rows and 34 columns. | 399 | custgroup | ❌ Table-specific detail - Hidden from main view |
| All tables migrated successfully. | 0 | null | ❌ Old completion log - Filtered out |

## How It Works

### Main Activity API Response

```
GET /api/v1/connection/12/activity/
```

**Response:**
```json
{
  "logs": [
    {
      "message": "Migration completed successfully in 415.11 seconds. Total rows: 34, Inserts: 34, Deletes: 0.",
      "user": "Akash CC",
      "timestamp": "2025-10-07 09:51:31",
      "status": "S",
      "session_id": 399,
      "table": "Migration",
      "is_session_related": true,
      "log_type": "migration_summary"  // ← Shows this is a summary
    },
    {
      "message": "Migration session 399 started - Rahul_Conn to Dest_Rahul. Tables: custgroup. Total tables: 1",
      "user": "Akash CC",
      "timestamp": "2025-10-07 09:44:36",
      "status": "S",
      "session_id": 399,
      "table": null,
      "is_session_related": true,
      "log_type": "migration_summary"
    },
    {
      "message": "Warning: Another migration is already in progress",
      "user": "Akash CC",
      "timestamp": "2025-10-07 09:40:44",
      "status": "W",
      "session_id": 0,
      "table": null,
      "is_session_related": true,
      "log_type": "migration_summary"
    }
    // ✅ "All tables migrated successfully" is REMOVED
    // ✅ "Successfully migrated/updated 'custgroup' table..." is NOT included here
  ]
}
```

### Session Detail View (When Clicking on a Log)

```
GET /api/v1/connection/12/session/399/
```

**Response (Simplified):**
```json
{
  "session_id": 399,
  "start_time": "2025-10-07 09:44:36",
  "end_time": "2025-10-07 09:51:31",
  "duration": "0:06:55",
  "total_tables": 1,
  "completed_tables": 1,
  "failed_tables": 0,
  "tables": [
    {
      "table_name": "custgroup",
      "status": "completed",
      "started_at": "2025-10-07 09:44:36",
      "completed_at": "2025-10-07 09:51:31"
    }
  ]
}
```

## Filtering Logic

### What Makes a Log "Summary" (Shown in Main View)?

```python
summary_keywords = [
    'session',                      # "Migration session X started"
    'migration completed successfully in',  # From services like MS Dynamics
    'migration in progress',        # Initial message  
    'update schema',               # Schema operations
    'all tables migrated',          # Old-style completion
    'tables listed and updated',    # Table listing
    'warning: another migration',   # Warnings
]
```

### What Makes a Log "Table Detail" (Hidden from Main View)?

```python
table_detail_patterns = [
    'successfully migrated/updated',  # "Successfully migrated/updated 'X' table..."
    'migration: success -',           # "accounts migration: Success - 1250"
    'migration: failure -',
    ': success -',
    ': failure -',
    'containing data with',           # "containing data with 34 rows"
    'rows and',
    'columns.'
]
```

**If a log has:**
- A table name in the `table` field (e.g., `table="custgroup"`) AND
- Matches any table detail pattern

**Then it's HIDDEN** from main activity view.

## UI Behavior

### Main Activity Screen (LEFT Side)

```
┌─────────────────────────────────────────┐
│         Connection Activity             │
├─────────────────────────────────────────┤
│                                         │
│ ✓ Migration completed successfully     │  ← SHOWS
│   in 415.11 seconds                     │
│   Session: 399                          │
│   2025-10-07 09:51:31                   │
│   [Click to view details] ───────────┐  │
│                                       │  │
│ ▶ Migration session 399 started      │  │  ← SHOWS
│   2025-10-07 09:44:36                 │  │
│                                       │  │
│ ⚠ All tables migrated successfully    │  │  ← SHOWS (old log)
│   2025-10-07 09:44:36                 │  │
│                                       │  │
│ 🗑 Connection paused                   │  │  ← Connection activity
│   2025-10-07 08:30:00                 │  │
│                                       │  │
└───────────────────────────────────────┘  │
                                           │
    // ❌ "Successfully migrated/updated    │
    //     'custgroup' table..." NOT shown  │
```

### Session Details (RIGHT Side - After Click)

```
                                           │
   ┌───────────────────────────────────────┘
   │
   └─→ ┌──────────────────────────────────┐
       │   Session 399 Details            │
       ├──────────────────────────────────┤
       │                                  │
       │ • Started: 09:44:36              │
       │ • Completed: 09:51:31            │
       │ • Duration: 6m 55s               │
       │ • Tables: 1/1 completed          │
       │                                  │
       │ Tables:                          │
       │ ✓ custgroup                      │
       │   Status: completed              │
       │   Started: 09:44:36              │
       │   Completed: 09:51:31            │
       │                                  │
       └──────────────────────────────────┘
```

## About Session IDs

### Current Behavior

Your logs show:
- Session 399: Active migration session
- Session 0: Old logs or non-session logs
- Session 398: Previous schema refresh
- Session null: General operations

### Why Some Logs Have session_id = 0

The log "All tables migrated successfully" with `session_id = 0` was created **before** my changes were deployed. 

**Going forward:**
- New migration completion logs will have correct session IDs
- Message will be: "Migration completed successfully in X seconds..."
- Session ID will match the "Migration session X started" log

### Migration Flow with Correct Session IDs

```
Time    Session    Log
09:44:36  399      Migration session 399 started ✓
09:44:36  399      Migration is already in progress (warning from another attempt)
09:51:31  399      Successfully migrated 'custgroup' (hidden)
09:51:31  399      Migration completed successfully ✓
```

All logs in the same migration have **session_id = 399** ✅

## Summary

### Main Activity View Shows:
✅ Migration session started  
✅ Migration completed successfully in X seconds  
✅ Connection paused/activated  
✅ Configuration changes  
✅ Update schema operations  
✅ Warnings and errors  

### Main Activity View Hides:
❌ Individual table migration logs  
❌ "Successfully migrated/updated 'X' table..." logs  
❌ "X migration: Success - Y, Failure - Z" logs  
❌ "All tables migrated successfully" (old logs with session_id=0)  

### Session Detail View (Simplified):
✅ Session start/end time and duration  
✅ Table counts (total, completed, failed)  
✅ **Simple table list** with status and timestamps  
❌ No complex nested structure  
❌ Just the essential table information  

## Testing Your Changes

After deploying, you should see:

1. **Start a migration** → "Migration session X started" appears in activity
2. **Migration runs** → Individual table logs created but NOT shown in activity
3. **Migration completes** → "Migration completed successfully in Y seconds" appears in activity with SAME session ID
4. **Click on completion log** → Right side shows simple table list with status

### Changes Made:
✅ **Removed** "All tables migrated successfully" log (session_id=0)  
✅ **Simplified** session detail response - just table info, no complex nesting  
✅ **Clean** main activity view - only session summaries and connection activities

