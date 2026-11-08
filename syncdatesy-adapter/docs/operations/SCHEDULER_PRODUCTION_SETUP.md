# Production Scheduler Setup

## 🚀 **Overview**

The application now uses a production-ready APScheduler setup with:
- ✅ **PostgreSQL jobstore** - Jobs persist across restarts
- ✅ **Redis lock** - Prevents duplicate schedulers in multi-worker environments
- ✅ **Event listeners** - Comprehensive logging and monitoring
- ✅ **Thread pool** - 10 concurrent threads for parallel job execution
- ✅ **Auto-start** - Initializes when Django starts

**Pipeline flows:** schedule config is in the `pipeline` table; runnable jobs are `pipeline_<id>` in APScheduler. After deploy or scheduler restart, use startup reconcile or `manage.py reconcile_pipeline_schedulers`. See **[PIPELINE_SCHEDULER_RECONCILE.md](./PIPELINE_SCHEDULER_RECONCILE.md)**.

---

## 📁 **Architecture**

### **apps/elttool/apps.py** (Scheduler Configuration)
- Defines the global `scheduler` instance
- Configures PostgreSQL jobstore for persistence
- Sets up Redis lock for multi-worker coordination
- Adds event listeners for debugging
- Starts scheduler in `ready()` method

### **apps/elttool/v1/views.py** (Scheduler Usage)
- Imports `scheduler` from `apps.py`
- Uses scheduler in:
  - `background_task()` - Add/update jobs
  - `update_sync_frequency()` - Remove jobs when paused
  - `ConnectionToggleAPIView` - Pause/resume connections
  - `UpdateTableSelectionView` - Reschedule on table changes

---

## 🔧 **Configuration**

### **Required Settings (settings.py)**

```python
# Redis URL for scheduler lock
REDIS_URL = 'redis://localhost:6379/0'

# PostgreSQL database (already configured)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'your_database',
        'USER': 'your_user',
        'PASSWORD': 'your_password',
        'HOST': 'your_host',
        'PORT': '5432',
    }
}
```

### **APScheduler Tables**

APScheduler automatically creates these tables in PostgreSQL:
- `apscheduler_jobs` - Stores job definitions and state

You don't need to create migrations for these tables.

---

## 🎯 **How It Works**

### **1. Django Startup**
```
1. Django loads apps
   ↓
2. ElttoolConfig.ready() runs
   ↓
3. Check if scheduler already running (Redis lock)
   ↓
4. If not running:
   - Set Redis lock (key: "scheduler_running", expiry: 3 min)
   - Add event listeners
   - Start scheduler ✅
   ↓
5. Scheduler begins processing jobs from PostgreSQL
```

### **2. Job Creation (Connection Activated)**
```
User activates connection
   ↓
ConnectionToggleAPIView.post()
   ↓
update_sync_frequency(frequency=60)  # 60 minutes
   ↓
migration_function(frequency=60)
   ↓
threading.Thread(background_task)
   ↓
scheduler.add_job(
   task,
   'interval',
   minutes=60,
   id='9_123_SALESFORCE_CONN',  # cmp_id_connection_id_name
   replace_existing=True
)
   ↓
Job saved to PostgreSQL ✅
```

### **3. Job Execution (Every 60 Minutes)**
```
Scheduler timer triggers
   ↓
Executes task() function
   ↓
Calls FastAPI migration endpoint
   ↓
Syncs data from source to destination
   ↓
Updates SourceTableSelection.last_synced
   ↓
Updates SourceTableSelection.status
   ↓
Logs to log
   ↓
EVENT_JOB_EXECUTED listener logs success ✅
```

### **4. Job Pause (Connection Paused)**
```
User pauses connection
   ↓
ConnectionToggleAPIView.post()
   ↓
update_sync_frequency(frequency="None")
   ↓
scheduler.remove_job(job_id)
   ↓
Job removed from scheduler ✅
   (but remains in ScheduledJob DB table for reference)
```

---

## 📊 **Event Listeners**

### **Job Executed**
```python
✅ Job 9_123_SALESFORCE_CONN executed successfully.
```

### **Job Error**
```python
❌ Job 9_123_SALESFORCE_CONN failed with exception: Connection timeout
Traceback: ... (full stack trace)
```

### **Job Missed**
```python
⚠️  Job 9_123_SALESFORCE_CONN missed its scheduled run.
```
(Happens if previous execution is still running when next one is due)

### **Scheduler Started**
```python
🚀 Scheduler started.
```

### **Scheduler Shutdown**
```python
🛑 Scheduler shut down.
```

---

## 🔒 **Redis Lock Mechanism**

### **Purpose**
Prevents multiple schedulers running when using Gunicorn with multiple workers.

### **How It Works**
```python
# Worker 1 starts
is_scheduler_running()  # Check Redis key "scheduler_running"
# → Returns False
set_scheduler_running()  # Set key with 3-min expiry
scheduler.start()  # ✅ Start scheduler

# Worker 2 starts 1 second later
is_scheduler_running()  # Check Redis key "scheduler_running"
# → Returns True (Worker 1 set it)
# ⏭️  Skip start (log: "Scheduler already running")
```

### **Lock Expiry**
- **Duration:** 3 minutes (180 seconds)
- **Reason:** If process crashes, lock auto-expires so new worker can start scheduler
- **Refresh:** Lock is set once at startup, not renewed

### **Fallback**
If Redis is unavailable:
- Logs warning: `"Redis check failed: ... Assuming scheduler not running."`
- Proceeds to start scheduler anyway (safe for single-worker setups)

---

## 🗄️ **Database Tables**

### **APScheduler Tables (Auto-created)**
```sql
-- Stores job definitions
CREATE TABLE apscheduler_jobs (
    id VARCHAR(191) PRIMARY KEY,      -- Job ID (e.g., "9_123_SALESFORCE_CONN")
    next_run_time FLOAT,               -- Unix timestamp of next execution
    job_state BYTEA                    -- Pickled job state
);
```

### **Django Tables (Your Models)**
```sql
-- Tracks scheduled jobs
CREATE TABLE scheduledjob (
    id SERIAL PRIMARY KEY,
    connection_id INTEGER,
    job_name VARCHAR(255),
    frequency_minutes INTEGER,
    status VARCHAR(20),
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Tracks table sync status
CREATE TABLE sourcetableselection (
    -- ...
    status VARCHAR(20),          -- 'pending', 'in_progress', 'completed', 'failed'
    last_synced TIMESTAMP        -- Updated after each successful sync
);
```

---

## 🧪 **Testing**

### **Test 1: Verify Scheduler Started**
```bash
# Start Django
python manage.py runserver

# Look for log output:
# 🚀 Scheduler started.
# ✅ Scheduler started successfully.
```

### **Test 2: Create a Scheduled Job**
```bash
# In Django shell or via API
# 1. Create a connection
# 2. Set time_frequency = 5 (5 minutes)
# 3. Activate connection

# Check logs:
# ✅ Job 9_123_CONN created successfully
```

### **Test 3: Job Execution**
```bash
# Wait 5 minutes
# Check logs:
# ✅ Job 9_123_CONN executed successfully.

# Check database:
SELECT * FROM sourcetableselection 
WHERE connection_id = 123;
# last_synced should be updated
```

### **Test 4: Multi-Worker**
```bash
# Start Gunicorn with 4 workers
gunicorn datasyncher.wsgi:application --workers 4

# Check logs - should only see ONE scheduler start:
# Worker 1: 🚀 Scheduler started.
# Worker 2: ⏭️  Scheduler already running (Redis lock active)
# Worker 3: ⏭️  Scheduler already running (Redis lock active)
# Worker 4: ⏭️  Scheduler already running (Redis lock active)
```

---

## 🐛 **Troubleshooting**

### **Problem: "Scheduler not starting"**
**Symptoms:**
- No log message: `"🚀 Scheduler started."`
- Jobs not executing

**Solutions:**
1. Check if running during migrations:
   ```bash
   # Scheduler skips start during these commands:
   python manage.py migrate  # ← Scheduler won't start
   python manage.py runserver  # ← Scheduler WILL start
   ```

2. Check for errors in logs:
   ```bash
   grep "Failed to start the scheduler" logs/*
   ```

3. Verify database connection:
   ```python
   # In Django shell
   from apps.elttool.apps import get_database_url
   print(get_database_url())
   ```

---

### **Problem: "Jobs added but not executing"**
**Symptoms:**
- `scheduler.add_job()` succeeds
- No `EVENT_JOB_EXECUTED` logs
- Jobs visible in `apscheduler_jobs` table

**Solutions:**
1. Check scheduler is running:
   ```python
   from apps.elttool.apps import scheduler
   print(scheduler.running)  # Should be True
   print(scheduler.get_jobs())  # List all jobs
   ```

2. Check job next_run_time:
   ```sql
   SELECT id, next_run_time FROM apscheduler_jobs;
   -- next_run_time is Unix timestamp
   -- Compare with current time
   ```

3. Check for job errors:
   ```bash
   # Look for EVENT_JOB_ERROR logs
   grep "Job.*failed" logs/*
   ```

---

### **Problem: "Duplicate schedulers running"**
**Symptoms:**
- Jobs execute twice
- Duplicate log entries

**Solutions:**
1. Check Redis connection:
   ```python
   import redis
   from django.conf import settings
   r = redis.StrictRedis.from_url(settings.REDIS_URL)
   print(r.get("scheduler_running"))  # Should be "1" or None
   ```

2. Verify Redis lock timeout:
   ```python
   r.ttl("scheduler_running")  # Should be < 180 seconds
   ```

3. Manually release lock:
   ```python
   from apps.elttool.apps import release_scheduler_lock
   release_scheduler_lock()
   ```

---

### **Problem: "Job missed its scheduled run"**
**Symptoms:**
- Log: `"⚠️  Job X missed its scheduled run."`

**Causes:**
- Previous execution still running when next one is due
- System time jumped (server restart, time zone change)

**Solutions:**
1. Increase job frequency (e.g., 5 min → 15 min)
2. Check long-running migrations
3. Add `max_instances=1` to prevent overlapping (already set ✅)

---

## 📝 **Key Features**

### **1. Persistence**
- Jobs survive Django restarts
- Stored in PostgreSQL `apscheduler_jobs` table
- Automatically resume after server restart

### **2. Multi-Worker Safe**
- Redis lock prevents duplicate schedulers
- Only one worker runs scheduler
- Other workers add jobs via shared PostgreSQL store

### **3. Monitoring**
- Event listeners log all job execution
- Detailed error messages with stack traces
- Missed run detection

### **4. Graceful Shutdown**
- `EVENT_SCHEDULER_SHUTDOWN` triggered on exit
- Jobs saved to database
- Can be resumed later

---

## 🚀 **Production Deployment**

### **Gunicorn Configuration**
```bash
# Run with multiple workers
gunicorn datasyncher.wsgi:application \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile -
```

### **Docker Configuration**
```dockerfile
# Ensure Redis is available
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  web:
    build: .
    depends_on:
      - redis
      - postgres
    environment:
      - REDIS_URL=redis://redis:6379/0
```

### **Environment Variables**
```bash
REDIS_URL=redis://your-redis-host:6379/0
DATABASE_URL=postgresql://user:pass@host:5432/db
```

---

## 📚 **References**

- **APScheduler Docs:** https://apscheduler.readthedocs.io/
- **Redis Lock Pattern:** https://redis.io/docs/manual/patterns/distributed-locks/
- **Django AppConfig:** https://docs.djangoproject.com/en/4.2/ref/applications/

---

## ✅ **Summary**

Your scheduler setup is now **production-ready** with:
1. ✅ PostgreSQL persistence
2. ✅ Redis-based multi-worker coordination
3. ✅ Comprehensive event logging
4. ✅ Auto-start on Django initialization
5. ✅ Graceful error handling
6. ✅ Migration command detection

**Jobs will now execute reliably on schedule!** 🎉


