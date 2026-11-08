# Django Signals Explained: WebSocket Table Status Updates

## 📚 Why Separate Signals File?

### **Best Practice: `signals.py` vs `models.py`**

**❌ Putting signals in `models.py`:**
```python
# models.py
class SourceTableSelection(models.Model):
    # ... model definition ...

# Signals at the end of models.py
@receiver(post_save, sender=SourceTableSelection)
def my_signal(sender, instance, **kwargs):
    # ...
```

**Problems:**
1. **Circular Import Risk**: Models might import other modules that import models
2. **Code Organization**: Mixes model definitions with business logic
3. **Signal Registration**: Signals might not fire if models.py isn't imported correctly
4. **Testing**: Harder to test signals in isolation
5. **Maintainability**: Large models.py files become hard to read

**✅ Separate `signals.py` file:**
```python
# signals.py
@receiver(post_save, sender=SourceTableSelection)
def my_signal(sender, instance, **kwargs):
    # ...

# apps.py
def ready(self):
    import apps.elttool.signals  # Register signals
```

**Benefits:**
1. **Clear Separation**: Models define structure, signals define behavior
2. **Explicit Registration**: Signals are registered in `apps.py.ready()`
3. **No Circular Imports**: Signals import models, not vice versa
4. **Easy Testing**: Can test signals independently
5. **Better Organization**: Follows Django best practices

---

## 🔄 How Django Signals Work

### **Signal Flow Diagram**

```
┌─────────────────────────────────────────────────────────────┐
│  Django Model Save Operation                                 │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  pre_save Signal Fires                                       │
│  ─────────────────────────                                   │
│  • Fires BEFORE save() commits to database                   │
│  • Instance has new values but not yet saved                 │
│  • Perfect time to capture OLD values                        │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  capture_old_status() Signal Handler                         │
│  ────────────────────────────────                            │
│  • Fetches current DB state                                  │
│  • Stores old_status in instance._old_status                 │
│  • Stores old_connection_id in instance._old_connection_id   │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Database Save Operation                                     │
│  ────────────────────────                                    │
│  • INSERT or UPDATE executed                                 │
│  • Changes committed to database                             │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  post_save Signal Fires                                      │
│  ─────────────────────────                                   │
│  • Fires AFTER save() commits to database                    │
│  • Instance has new values saved to DB                       │
│  • Perfect time to react to changes                          │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  send_table_status_websocket_update() Signal Handler         │
│  ───────────────────────────────────────────────             │
│  1. Retrieves old values from instance._old_status           │
│  2. Compares with current values                             │
│  3. If changed: sends WebSocket update                       │
└─────────────────────────────────────────────────────────────┘
```

### **Signal Registration Process**

```python
# 1. Django starts
python manage.py runserver

# 2. Django loads INSTALLED_APPS
INSTALLED_APPS = [
    'apps.elttool',  # ← Your app
    # ...
]

# 3. Django finds ElttoolConfig in apps.py
class ElttoolConfig(AppConfig):
    name = 'apps.elttool'
    
    def ready(self):  # ← Called when app is ready
        # 4. Import signals.py (this registers the @receiver decorators)
        import apps.elttool.signals
        
        # 5. @receiver decorators connect signal handlers
        # Now pre_save and post_save will fire for SourceTableSelection
```

### **Signal Handler Execution**

```python
# When you do this:
table = SourceTableSelection.objects.get(pk=1)
table.status = 'completed'
table.save()  # ← Signals fire here!

# What happens:
# 1. pre_save signal fires
#    → capture_old_status() runs
#    → Fetches old status from DB: 'in_progress'
#    → Stores in instance._old_status = 'in_progress'

# 2. Database UPDATE executes
#    → status = 'completed' saved to DB

# 3. post_save signal fires
#    → send_table_status_websocket_update() runs
#    → Compares: old_status='in_progress' != current_status='completed'
#    → Status changed! → Sends WebSocket update
```

---

## 📝 Code Breakdown

### **1. pre_save Signal Handler**

```python
@receiver(pre_save, sender=SourceTableSelection)
def capture_old_status(sender, instance, **kwargs):
    """
    Purpose: Capture database state BEFORE save
    
    Why pre_save?
    - After save, old values are gone
    - We need to compare old vs new
    - Store old values temporarily on instance
    """
    if instance.pk:  # Existing record (update, not create)
        old_instance = SourceTableSelection.objects.get(pk=instance.pk)
        instance._old_status = old_instance.status  # Store old value
```

**Key Points:**
- `instance.pk` exists → It's an UPDATE, not a CREATE
- We fetch current DB state before it's overwritten
- Store old values in `instance._old_*` (temporary attributes)
- These attributes survive until `post_save` runs

### **2. post_save Signal Handler**

```python
@receiver(post_save, sender=SourceTableSelection)
def send_table_status_websocket_update(sender, instance, created, **kwargs):
    """
    Purpose: React to changes AFTER save
    
    Why post_save?
    - Database is updated
    - We can safely send notifications
    - Compare old (from pre_save) with new (current)
    """
    old_status = getattr(instance, '_old_status', None)
    current_status = instance.status
    
    if old_status != current_status:  # Change detected!
        send_table_status_update(connection_id)
```

**Key Points:**
- `created=True` → New instance (no old values to compare)
- `created=False` → Updated instance (compare old vs new)
- Only send update if values actually changed
- Use `getattr()` with default to safely access `_old_*` attributes

---

## 🧪 Testing Locally

### **Prerequisites**

1. **Redis Running** (for Channels)
```bash
# Check if Redis is running
redis-cli ping
# Should return: PONG

# If not running, start it:
redis-server
```

2. **Django Server Running** (with ASGI support)
```bash
# Make sure you're using an ASGI server
# Options:
# 1. Django development server (for testing)
python manage.py runserver

# 2. Daphne (production ASGI server)
pip install daphne
daphne datasyncher.asgi:application

# 3. Uvicorn (if using ASGI)
uvicorn datasyncher.asgi:application
```

### **Test 1: Verify Signals Are Registered**

```bash
# Start Django shell
python manage.py shell
```

```python
from django.db.models.signals import post_save
from apps.elttool.models import SourceTableSelection

# Check if signal is connected
print(post_save.has_listeners(SourceTableSelection))
# Should print: True

# List all connected receivers
receivers = post_save._live_receivers(SourceTableSelection)
print(f"Connected receivers: {len(receivers)}")
# Should show at least 1 receiver
```

### **Test 2: Test Signal Firing (Manual Update)**

```python
# In Django shell
from apps.elttool.models import SourceTableSelection, Connection
import logging

# Enable debug logging to see signal activity
logging.basicConfig(level=logging.DEBUG)

# Get a table instance
table = SourceTableSelection.objects.first()
print(f"Current status: {table.status}")
print(f"Connection ID: {table.connection_id}")

# Update status (this will trigger signals)
table.status = 'in_progress'
table.save()  # ← Signals fire here!

# Check logs for:
# - "WebSocket update sent for connection X"
# - Any error messages
```

### **Test 3: Test WebSocket Connection**

**Terminal 1: Start Django Server**
```bash
python manage.py runserver
```

**Terminal 2: Connect with WebSocket Client**

Install `wscat` (WebSocket client):
```bash
npm install -g wscat
```

Connect to WebSocket:
```bash
# Replace 123 with your actual connection_id
wscat -c ws://localhost:8000/ws/table-status/123/
```

**Terminal 3: Update Table Status**
```python
# In Django shell
from apps.elttool.models import SourceTableSelection

table = SourceTableSelection.objects.filter(connection_id=123).first()
if table:
    table.status = 'completed'
    table.save()  # ← Should trigger WebSocket message
```

**Expected Result:**
- Terminal 2 (wscat) should receive a JSON message:
```json
{
  "table_statuses": [...],
  "readable_time_frequency": "...",
  "schema_refresh_in_progress": false
}
```

### **Test 4: Test with Multiple Clients**

**Terminal 1: Client 1**
```bash
wscat -c ws://localhost:8000/ws/table-status/123/
```

**Terminal 2: Client 2**
```bash
wscat -c ws://localhost:8000/ws/table-status/123/
```

**Terminal 3: Update Status**
```python
# In Django shell
table.status = 'failed'
table.save()
```

**Expected Result:**
- Both Terminal 1 and Terminal 2 receive the same update
- This proves the group broadcasting works!

### **Test 5: Test Signal Not Firing (No Change)**

```python
# In Django shell
table = SourceTableSelection.objects.first()

# Get current status
original_status = table.status
print(f"Original: {original_status}")

# Set to same value (no change)
table.status = original_status
table.save()  # ← Signal fires but no update sent (no change detected)

# Check logs - should see no WebSocket message sent
```

### **Test 6: Integration Test Script**

Create `test_websocket_signals.py`:

```python
"""
Test script for WebSocket signals
Run: python manage.py shell < test_websocket_signals.py
"""
from apps.elttool.models import SourceTableSelection, Connection
from apps.elttool.websocket_utils import send_table_status_update
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get a connection
connection = Connection.objects.first()
if not connection:
    print("❌ No connections found. Create one first.")
    exit()

print(f"✅ Testing with connection_id: {connection.connection_id}")

# Get a table
table = SourceTableSelection.objects.filter(connection=connection).first()
if not table:
    print("❌ No tables found for this connection.")
    exit()

print(f"✅ Testing with table: {table.table}")
print(f"   Current status: {table.status}")

# Test 1: Update status
print("\n📝 Test 1: Updating status...")
old_status = table.status
table.status = 'in_progress' if old_status != 'in_progress' else 'completed'
table.save()
print(f"   Status changed: {old_status} → {table.status}")
print("   ✅ Check WebSocket client for message")

# Test 2: Direct function call (for raw SQL updates)
print("\n📝 Test 2: Direct WebSocket update function...")
send_table_status_update(connection.connection_id)
print("   ✅ Check WebSocket client for message")

print("\n✅ Tests complete!")
```

Run it:
```bash
python manage.py shell < test_websocket_signals.py
```

---

## 🔍 Debugging Signals

### **Enable Signal Logging**

Add to `settings/base.py`:
```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'apps.elttool.signals': {
            'handlers': ['console'],
            'level': 'DEBUG',  # Show all signal activity
        },
    },
}
```

### **Check Signal Registration**

```python
# In Django shell
from django.db.models.signals import pre_save, post_save
from apps.elttool.models import SourceTableSelection

# List receivers
pre_receivers = pre_save._live_receivers(SourceTableSelection)
post_receivers = post_save._live_receivers(SourceTableSelection)

print(f"pre_save receivers: {len(pre_receivers)}")
print(f"post_save receivers: {len(post_receivers)}")

# Should show at least 1 for each
```

### **Common Issues**

**Issue: Signals not firing**
- ✅ Check signals are imported in `apps.py.ready()`
- ✅ Restart Django server (signals register on startup)
- ✅ Verify `@receiver` decorator is correct
- ✅ Check sender model matches

**Issue: WebSocket not receiving messages**
- ✅ Check Redis is running
- ✅ Verify `CHANNEL_LAYERS` config
- ✅ Check WebSocket URL matches routing
- ✅ Verify connection_id is correct
- ✅ Check browser console for errors

**Issue: Signal fires but no update sent**
- ✅ Check if status actually changed (old != new)
- ✅ Verify connection_id exists
- ✅ Check logs for errors
- ✅ Test `send_table_status_update()` directly

---

## 📊 Signal Performance

### **Optimization Tips**

1. **Avoid Heavy Operations in Signals**
   ```python
   # ❌ Bad: Heavy operation blocks save()
   @receiver(post_save, sender=MyModel)
   def heavy_operation(sender, instance, **kwargs):
       time.sleep(10)  # Blocks database save!
   
   # ✅ Good: Use Celery for heavy tasks
   @receiver(post_save, sender=MyModel)
   def trigger_async_task(sender, instance, **kwargs):
       my_celery_task.delay(instance.id)  # Non-blocking
   ```

2. **Check for Changes Before Processing**
   ```python
   # ✅ Good: Only process if changed
   if old_status != current_status:
       send_update()
   ```

3. **Handle Errors Gracefully**
   ```python
   # ✅ Good: Don't break save() if WebSocket fails
   try:
       send_update()
   except Exception as e:
       logger.error(f"Update failed: {e}")  # Log but continue
   ```

---

## 🎯 Summary

**Why separate `signals.py`?**
- ✅ Better code organization
- ✅ Explicit signal registration
- ✅ Avoids circular imports
- ✅ Easier to test
- ✅ Django best practice

**How signals work:**
1. `pre_save` → Capture old values
2. Database save → Commit changes
3. `post_save` → Compare and react

**Testing:**
- Use Django shell to test manually
- Use `wscat` to test WebSocket connections
- Enable debug logging to see signal activity
- Test with multiple clients to verify group broadcasting

**Key Takeaway:**
Signals provide a clean way to react to model changes without modifying the model class or the code that saves it. This is perfect for sending WebSocket updates automatically whenever table statuses change!

