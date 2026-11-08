# Testing WebSocket Table Status Updates Locally

## 🚀 Quick Start Guide

### Step 1: Start Required Services

**Terminal 1: Start Redis**
```bash
redis-server

# Or if using Docker:
docker run -d -p 6379:6379 redis:alpine

# Verify it's running:
redis-cli ping
# Should return: PONG
```

**Terminal 2: Start Django Server**
```bash
# Make sure you're in the project root
cd /path/to/syncdatesy-adapter

# Start Django (with ASGI support)
python manage.py runserver

# Or use Daphne (recommended for WebSocket testing):
pip install daphne
daphne datasyncher.asgi:application --bind 0.0.0.0 --port 8000
```

**Terminal 3: WebSocket Client**
```bash
# Install wscat (WebSocket client)
npm install -g wscat

# Connect to WebSocket (replace 123 with your connection_id)
wscat -c ws://localhost:8000/ws/table-status/123/

# You should see: Connected (press CTRL+C to quit)
```

### Step 2: Test Signal Triggering

**Terminal 4: Django Shell**
```bash
python manage.py shell
```

```python
# Import models
from apps.elttool.models import SourceTableSelection, Connection

# Get a connection (replace with your actual connection_id)
connection = Connection.objects.first()
print(f"Using connection_id: {connection.connection_id}")

# Get a table
table = SourceTableSelection.objects.filter(connection=connection).first()
print(f"Testing with table: {table.table}")
print(f"Current status: {table.status}")

# Update status - this will trigger the signal!
table.status = 'in_progress'
table.save()  # ← Signal fires here!

# Check Terminal 3 (wscat) - you should see a JSON message!
```

### Step 3: Verify Results

**In Terminal 3 (wscat), you should see:**
```json
{
  "table_statuses": [
    {
      "table_name": "Account",
      "status": "in_progress"
    }
  ],
  "readable_time_frequency": "2024-01-15 10:30:00Z",
  "schema_refresh_in_progress": false
}
```

---

## 🧪 Complete Test Suite

### Test 1: Verify Signals Are Registered

```bash
python manage.py shell
```

```python
from django.db.models.signals import pre_save, post_save
from apps.elttool.models import SourceTableSelection

# Check if signals are connected
print("pre_save connected:", pre_save.has_listeners(SourceTableSelection))
print("post_save connected:", post_save.has_listeners(SourceTableSelection))

# Should both print: True
```

### Test 2: Test Signal Execution

```python
# In Django shell
import logging
logging.basicConfig(level=logging.DEBUG)

from apps.elttool.models import SourceTableSelection

table = SourceTableSelection.objects.first()
print(f"Before: {table.status}")

# Update status
table.status = 'completed'
table.save()

# Check server logs for:
# "WebSocket update sent for connection X"
```

### Test 3: Test Multiple Clients

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

**Expected:** Both Terminal 1 and Terminal 2 receive the same message!

### Test 4: Test No Change Detection

```python
# In Django shell
table = SourceTableSelection.objects.first()
original = table.status

# Set to same value (no change)
table.status = original
table.save()

# Check logs - should NOT send WebSocket update
# (because status didn't actually change)
```

### Test 5: Test Direct Function Call

```python
# In Django shell
from apps.elttool.websocket_utils import send_table_status_update

# Direct call (for raw SQL updates)
send_table_status_update(123)  # Replace with your connection_id

# Check WebSocket client - should receive update
```

---

## 📋 Using the Test Script

I've created a test script for you:

```bash
# Option 1: Run in Django shell
python manage.py shell < test_websocket_signals.py

# Option 2: Copy and paste into Django shell
python manage.py shell
# Then paste the contents of test_websocket_signals.py
```

---

## 🐛 Troubleshooting

### Issue: Signals not firing

**Check:**
1. ✅ Signals are imported in `apps.py.ready()`
2. ✅ Django server restarted after adding signals
3. ✅ Check server logs for: "✅ WebSocket table status signals registered"

**Solution:**
```python
# In Django shell, verify signals are registered
from django.db.models.signals import post_save
from apps.elttool.models import SourceTableSelection
print(post_save.has_listeners(SourceTableSelection))  # Should be True
```

### Issue: WebSocket not connecting

**Check:**
1. ✅ Redis is running: `redis-cli ping`
2. ✅ `CHANNEL_LAYERS` config in settings
3. ✅ Using ASGI server (not just WSGI)
4. ✅ WebSocket URL is correct: `ws://localhost:8000/ws/table-status/123/`

**Solution:**
```bash
# Use Daphne instead of runserver
pip install daphne
daphne datasyncher.asgi:application
```

### Issue: WebSocket connects but no messages

**Check:**
1. ✅ Table status actually changed (old != new)
2. ✅ Connection ID matches in WebSocket URL
3. ✅ Check server logs for errors
4. ✅ Test direct function call:

```python
from apps.elttool.websocket_utils import send_table_status_update
send_table_status_update(123)  # Your connection_id
```

### Issue: "Connection refused" error

**Check:**
1. ✅ Redis is running on correct port
2. ✅ `CHANNEL_LAYERS` config points to correct Redis host
3. ✅ For Docker: Redis container is running

**Solution:**
```bash
# Check Redis connection
redis-cli ping

# Update settings if Redis is on different host
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [("localhost", 6379)]},  # Change host if needed
    },
}
```

---

## 📊 Debug Mode

### Enable Detailed Logging

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
        'apps.elttool.websocket_utils': {
            'handlers': ['console'],
            'level': 'DEBUG',  # Show WebSocket activity
        },
        'channels': {
            'handlers': ['console'],
            'level': 'DEBUG',  # Show Channels activity
        },
    },
}
```

### Watch Logs in Real-Time

```bash
# Terminal 1: Start server with logs
python manage.py runserver

# Terminal 2: Watch for signal activity
# Updates will show in server logs when you save models
```

---

## ✅ Verification Checklist

- [ ] Redis is running
- [ ] Django server is running with ASGI support
- [ ] Signals are registered (check server logs)
- [ ] WebSocket client connects successfully
- [ ] Updating table status triggers WebSocket message
- [ ] Multiple clients receive same update
- [ ] No update sent when status doesn't change
- [ ] Direct function call works

---

## 🎯 Next Steps

Once testing is successful:
1. Integrate WebSocket client into your frontend
2. Handle reconnection logic
3. Update UI when messages are received
4. Test with real migration updates

---

## 📚 Additional Resources

- [Django Channels Documentation](https://channels.readthedocs.io/)
- [Django Signals Documentation](https://docs.djangoproject.com/en/stable/topics/signals/)
- [WebSocket Testing Guide](./WEBSOCKET_TABLE_STATUS_UPDATES.md)
- [Signals Explained](docs/DJANGO_SIGNALS_EXPLAINED.md)

