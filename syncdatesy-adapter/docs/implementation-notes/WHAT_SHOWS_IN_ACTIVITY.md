# What Shows in Activity View - Complete List

## All Log Types That Will Show

### Migration Logs (Has session_id > 1)
```
✓ Migration completed successfully in X seconds. Total rows: Y...
  Session: 403
  
▶ Migration session started
  Session: 403
  
⚠ Warning: Another migration is already in progress
  Session: 403
  
⚠ Migration is already in progress. Please wait.
  Session: 403
```

### Connection Management Logs (session_id = null)
```
⏸ Connection Paused
  (No session)
  
▶ Connection Activated
  (No session)
  
⚙ Configuration updated
  (No session)
  
✓ Test connection successful
  (No session)
  
✗ Connection test failed
  (No session)
  
⚙ Source configuration updated
  (No session)
  
⚙ Destination configuration updated
  (No session)
  
⚙ Sync frequency changed
  (No session)
```

### Schema Operations (Has session_id if related to migration)
```
▶ Update schema started
  Session: 398
  
✓ Tables listed and updated successfully in X seconds
  (No session or has session)
```

## What Will NOT Show (Hidden from Activity)

### Individual Table Migration Logs
```
❌ Successfully migrated/updated 'custgroup' table, containing data with 34 rows and 34 columns.
   (Hidden - shown only in session detail)

❌ accounts migration: Success - 1250, Failure - 0, Columns - 15.
   (Hidden - shown only in session detail)
```

### Old Format Logs
```
❌ All tables migrated successfully.
   (Filtered out if session_id = 0)
```

## Filtering Logic Explained

The code checks two conditions:

```python
if Log._is_summary_log(log) or not Log._is_session_log(log):
    # Show this log
```

**Condition 1: `_is_summary_log(log)`**
Returns True if log contains keywords like:
- "migration session started"
- "migration completed successfully in"
- "migration in progress"
- "update schema"
- "warning: another migration"

**Condition 2: `not _is_session_log(log)`**
Returns True if:
- `log.session is None` (connection activities)
- `log.session <= 1` (general activities)

**Result:**
- ✅ Migration summaries: `_is_summary_log` = True → **Shows**
- ✅ Connection activities: `session=null` → `not _is_session_log` = True → **Shows**
- ❌ Table details: `session>1` but not summary → **Hidden**

## Sample Activity Response

```json
{
  "logs": [
    {
      "message": "Migration completed successfully in 1434.13 seconds. Total rows: 530712, Inserts: 530712, Deletes: 0.",
      "user": "Akash CC",
      "timestamp": "2025-10-07 10:53:44",
      "status": "S",
      "session_id": 403,
      "table": "Migration",
      "is_session_related": true,
      "log_type": "migration_summary"
    },
    {
      "message": "Migration session started",
      "user": "Akash CC",
      "timestamp": "2025-10-07 10:29:49",
      "status": "S",
      "session_id": 403,
      "table": null,
      "is_session_related": true,
      "log_type": "migration_summary"
    },
    {
      "message": "Connection Paused",
      "user": "Akash CC",
      "timestamp": "2025-10-07 09:30:00",
      "status": "S",
      "session_id": null,
      "table": null,
      "is_session_related": false,
      "log_type": "connection_activity"
    },
    {
      "message": "Connection Activated",
      "user": "Akash CC",
      "timestamp": "2025-10-07 08:00:00",
      "status": "S",
      "session_id": null,
      "table": null,
      "is_session_related": false,
      "log_type": "connection_activity"
    },
    {
      "message": "Source configuration updated",
      "user": "Jane Smith",
      "timestamp": "2025-10-07 07:15:00",
      "status": "S",
      "session_id": null,
      "table": null,
      "is_session_related": false,
      "log_type": "connection_activity"
    }
  ]
}
```

## What Each User Action Creates

### User Pauses Connection
```
→ Log created with session_id = null
→ Shows in activity as "Connection Paused"
```

### User Activates Connection
```
→ Log created with session_id = null
→ Shows in activity as "Connection Activated"
```

### User Updates Configuration
```
→ Log created with session_id = null
→ Shows in activity as "Configuration updated"
```

### User Starts Migration
```
→ Log created with session_id = 403 (unique)
→ Shows in activity as "Migration session started"
```

### User's Migration Completes
```
→ Log created with session_id = 403 (same as start)
→ Shows in activity as "Migration completed successfully in X seconds..."
```

### During Migration (Table Processing)
```
→ Logs created with session_id = 403
→ Table logs are HIDDEN from activity
→ Only visible in session detail view
```

## Summary

✅ **All connection activities show** - Paused, activated, config updates, etc.  
✅ **All migration summaries show** - Started, completed  
✅ **Session IDs only for migrations** - Connection activities have session=null  
✅ **Table details hidden** - Only in session detail view  

Every message type will show in the activity view! The filtering only hides individual table migration logs.

