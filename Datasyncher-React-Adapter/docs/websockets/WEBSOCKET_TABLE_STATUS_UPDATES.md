# WebSocket Live Table Status Updates

## Overview

This document explains how the Django Channels WebSocket implementation works to send real-time table status updates to the frontend, without modifying the existing DRF API (`TableStatusAPIView`).

## Architecture

### Components

1. **WebSocket Consumer** (`datasyncher/consumers.py`)
   - Handles WebSocket connections
   - Manages connection-specific groups
   - Sends updates to connected clients

2. **Routing** (`datasyncher/routing.py`)
   - Defines WebSocket URL patterns
   - Routes connections to the consumer

3. **Helper Function** (`apps/elttool/websocket_utils.py`)
   - Formats data exactly like `TableStatusAPIView`
   - Sends messages via Channels layer

4. **Django Signals** (`apps/elttool/models.py`)
   - Automatically detects when `SourceTableSelection.status` changes
   - Triggers WebSocket updates

## How It Works

### 1. Frontend Connection

The frontend connects to the WebSocket endpoint:
```
ws://your-domain/ws/table-status/{connection_id}/
```

Example:
```
ws://localhost:8000/ws/table-status/123/
```

### 2. Connection Group

When a client connects:
- The consumer extracts `connection_id` from the URL
- Creates/joins a group: `table_status_{connection_id}`
- All clients watching the same connection receive the same updates

### 3. Automatic Updates

When `SourceTableSelection.status` changes (via Django ORM):
1. `pre_save` signal captures the old status
2. `post_save` signal compares old vs new status
3. If status changed, `send_table_status_update()` is called
4. Data is formatted (same as `TableStatusAPIView`)
5. Message sent to the connection's group
6. All connected clients receive the update

### 4. Data Format

The WebSocket message format matches `TableStatusAPIView` exactly:

```json
{
  "table_statuses": [
    {
      "table_name": "Account",
      "status": "completed"
    },
    {
      "table_name": "Contact",
      "status": "in_progress"
    }
  ],
  "readable_time_frequency": "2024-01-15 10:30:00Z",
  "schema_refresh_in_progress": false
}
```

## Frontend Integration Example

```javascript
// Connect to WebSocket
const connectionId = 123;
const ws = new WebSocket(`ws://localhost:8000/ws/table-status/${connectionId}/`);

// Handle incoming messages
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Table status update:', data);
  
  // Update your UI with the new status
  updateTableStatuses(data.table_statuses);
  updateNextSyncTime(data.readable_time_frequency);
  updateSchemaRefreshStatus(data.schema_refresh_in_progress);
};

// Handle connection open
ws.onopen = () => {
  console.log('WebSocket connected');
};

// Handle connection close
ws.onclose = () => {
  console.log('WebSocket disconnected');
  // Optionally reconnect
};
```

## Important Notes

### Django ORM Updates Only

**⚠️ Limitation**: The Django signals only fire when using Django ORM to save/update models. If your backend services update the database directly via raw SQL (which some migration services do), the signals won't fire automatically.

### Solutions for Raw SQL Updates

If your backend services use raw SQL, you have two options:

#### Option 1: Call Helper Function Directly

From your backend service (after SQL update), make an HTTP request to trigger the update:

```python
# In your backend service after updating via SQL
import requests

# Trigger WebSocket update
requests.post(
    'http://django-app/api/v1/trigger-table-status-update/',
    json={'connection_id': connection_id},
    headers={'Authorization': 'Bearer <token>'}
)
```

#### Option 2: Create a Minimal Endpoint (Recommended)

Add a simple endpoint that backend services can call:

```python
# In apps/elttool/v1/views.py
from apps.elttool.websocket_utils import send_table_status_update

class TriggerTableStatusUpdateAPIView(APIView):
    def post(self, request):
        connection_id = request.data.get('connection_id')
        if connection_id:
            send_table_status_update(connection_id)
            return Response({'status': 'update sent'}, status=200)
        return Response({'error': 'connection_id required'}, status=400)
```

Then add to URLs:
```python
path('trigger-table-status-update/', TriggerTableStatusUpdateAPIView.as_view()),
```

## Testing

### Test WebSocket Connection

1. Start your Django server with Channels support
2. Connect using a WebSocket client (e.g., `wscat`):

```bash
wscat -c ws://localhost:8000/ws/table-status/123/
```

3. Update a table status via Django admin or ORM
4. You should see the update message in the WebSocket client

### Test Signal Triggering

```python
# In Django shell
from apps.elttool.models import SourceTableSelection

# Get a table
table = SourceTableSelection.objects.first()

# Update status (this will trigger WebSocket update)
table.status = 'in_progress'
table.save()  # Signal fires here!
```

## Configuration

### Settings Required

Ensure `CHANNEL_LAYERS` is configured in `settings/base.py`:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [("redis", 6379)]},
    },
}
```

### ASGI Application

The `datasyncher/asgi.py` should route WebSocket connections:

```python
application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            datasyncher.routing.websocket_urlpatterns
        )
    ),
})
```

## Benefits

1. **No DRF API Changes**: `TableStatusAPIView` remains unchanged
2. **Automatic Updates**: Signals handle everything automatically
3. **Consistent Data**: Same format as REST API
4. **Connection-Specific**: Only relevant clients receive updates
5. **Scalable**: Works with multiple clients per connection

## Troubleshooting

### Updates Not Received

1. Check Redis is running and accessible
2. Verify `CHANNEL_LAYERS` configuration
3. Check Django signals are firing (add logging)
4. Ensure WebSocket URL includes correct `connection_id`
5. Check browser console for WebSocket errors

### Signal Not Firing

- Signals only fire for Django ORM saves
- Raw SQL updates won't trigger signals
- Use the helper endpoint if updating via SQL

### Connection Issues

- Verify ASGI server is running (not just WSGI)
- Check WebSocket URL pattern matches routing
- Ensure Channels middleware is configured

