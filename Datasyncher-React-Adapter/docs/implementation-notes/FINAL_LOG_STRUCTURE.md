# Final Log Structure - What You'll See

## Activity View (LEFT Side)

### What Shows:
```
✓ Migration completed successfully  ← Shows AFTER migration finishes
  Session: 400
  2025-10-07 10:30:00

▶ Migration session started  ← Shows WHEN migration starts
  Session: 400
  2025-10-07 10:25:00

⏸ Connection paused
  2025-10-07 09:30:00

▶ Connection activated
  2025-10-07 08:00:00
```

### What's Hidden:
- ❌ "All tables migrated successfully" - Old format, removed
- ❌ "Successfully migrated/updated 'custgroup' table..." - Hidden from activity
- ❌ Any table-specific logs - Hidden from activity (shown in session details)

## Session Details (RIGHT Side - When Clicking)

When user clicks "Migration session started":

```
GET /api/v1/connection/123/session/400/
```

**Response:**
```json
{
  "session_id": 400,
  "logs": [
    {
      "message": "Successfully migrated/updated 'custgroup' table, containing data with 34 rows and 34 columns.",
      "timestamp": "2025-10-07 10:29:50",
      "status": "S",
      "table": "custgroup"
    },
    {
      "message": "Successfully migrated/updated 'accounts' table, containing data with 120 rows and 25 columns.",
      "timestamp": "2025-10-07 10:28:15",
      "status": "S",
      "table": "accounts"
    }
  ]
}
```

## Log Flow

```
User starts migration
  ↓
"Migration session started" log created ✅
Shows in activity view immediately
  ↓
Migration runs (tables being migrated)
  ↓
Table logs created but HIDDEN from activity view ✅
(Only visible in session details)
  ↓
Migration completes
  ↓
"Migration completed successfully" log created ✅
Shows in activity view AFTER completion
```

## What Logs Are Created

### During Migration Session:

1. **Start Log** (Shows in activity immediately)
   ```
   Message: "Migration session started"
   Session: 400
   Table: null
   Status: S
   Time: 2025-10-07 10:25:00
   ```

2. **Table Logs** (Hidden from activity, shown in session details only)
   ```
   Message: "Successfully migrated/updated 'custgroup' table, containing data with 34 rows and 34 columns."
   Session: 400
   Table: custgroup
   Status: S
   Time: 2025-10-07 10:29:50
   ```

3. **Completion Log** (Created by migration service, shows in activity AFTER migration finishes)
   ```
   Message: "Migration completed successfully in 1434.13 seconds. Total rows: 530712, Inserts: 530712, Deletes: 0."
   Session: 400
   Table: Migration
   Status: S
   Time: 2025-10-07 10:30:00
   ```
   
   **Note:** Only ONE completion log is created (by the service), not two!

## Connection Activity Logs (Always Show)

These are NOT migration logs, they always show:

```
⏸ Connection paused
▶ Connection activated
⚙ Configuration updated
✓ Test connection successful
⚠ Warning: Another migration is already in progress
```

## API Responses

### Activity API
```
GET /api/v1/connection/123/activity/
```

**Response:**
```json
{
  "logs": [
    {
      "message": "Migration completed successfully",
      "user": "Akash CC",
      "timestamp": "2025-10-07 10:30:00",
      "status": "S",
      "session_id": 400,
      "table": null,
      "is_session_related": true,
      "log_type": "migration_summary"
    },
    {
      "message": "Migration session started",
      "user": "Akash CC",
      "timestamp": "2025-10-07 10:25:00",
      "status": "S",
      "session_id": 400,
      "table": null,
      "is_session_related": true,
      "log_type": "migration_summary"
    },
    {
      "message": "Connection paused",
      "user": "Akash CC",
      "timestamp": "2025-10-07 09:30:00",
      "status": "S",
      "session_id": 1,
      "table": null,
      "is_session_related": false,
      "log_type": "connection_activity"
    }
  ]
}
```

**Shows:**
- Migration session started
- Migration completed successfully
- Connection activities (paused, activated, etc.)
- Warnings
- Schema operations

**Does NOT show:**
- Individual table logs ❌
- Migration records ❌ (removed to avoid confusion)

### Session Detail API
```
GET /api/v1/connection/123/session/400/
```

**Shows:**
- Just table log messages
- Nothing else

## Summary

### Activity View
✅ Shows: "Migration session started" (when started)  
✅ Shows: "Migration completed successfully" (AFTER completion)  
✅ Shows: Connection activities (paused, activated, etc.)  
❌ Hides: Individual table logs  

### Session Details
✅ Shows: Table log messages only  
❌ No extra structure  

**Clean and simple!** Start log when it starts, completion log when it finishes. 🎯

