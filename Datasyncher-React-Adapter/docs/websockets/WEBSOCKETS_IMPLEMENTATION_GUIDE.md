# DataSyncher – Live Table Status via Django Channels

This guide documents the full implementation of WebSocket live updates for table statuses, the exact code/files changed, how it works end‑to‑end, what the React frontend should do, and key deployment notes.

## 1) What we built

- Real‑time push of table status updates per connection using Django Channels and Redis
- Message format matches `TableStatusAPIView` so the frontend can render REST/WS consistently
- Auto‑push on model changes via Django signals, plus a helper for manual triggers (bulk SQL)

## 2) Files changed/created

- `datasyncher/consumers.py` (new consumer)
- `datasyncher/routing.py` (WebSocket URL pattern)
- `datasyncher/asgi.py` (ASGI app; uses env DJANGO_SETTINGS_MODULE; Channels router)
- `datasyncher/settings/base.py` (env‑driven CHANNEL_LAYERS)
- `apps/elttool/websocket_utils.py` (helper: build payload + send to group)
- `apps/elttool/signals.py` (pre_save/post_save signal handlers)
- `docker-compose.yml` (run Django with Daphne; route `/ws` via Traefik)
- `datasyncher/requirements.txt` (+ `channels-redis`, `daphne`)

## 3) How it works

1. Frontend opens a WebSocket to a connection‑specific endpoint:
   - `ws(s)://<backend-domain>/ws/table-status/{connection_id}/`
2. The consumer joins group `table_status_{connection_id}`.
3. When a `SourceTableSelection.status` changes (via Django ORM), signals fire:
   - `pre_save` stores old values → `post_save` compares → if changed, calls `send_table_status_update(connection_id)`
4. `send_table_status_update` builds a payload identical to `TableStatusAPIView`:
   - `table_statuses: [{ table_name, status }, ...]`
   - `readable_time_frequency` (next sync time string)
   - `schema_refresh_in_progress` (bool)
5. Message is sent to the group; all clients connected to that connection_id receive the JSON.
6. For bulk SQL updates (signals won’t fire), any service can call the helper directly.

## 4) WebSocket endpoints

- WebSocket URL (per connection):
  - `ws(s)://<backend-domain>/ws/table-status/{connection_id}/`
- Group name on server: `table_status_{connection_id}`

## 5) Message format (matches REST)

```json
{
  "table_statuses": [
    { "table_name": "Account", "status": "in_progress" }
  ],
  "readable_time_frequency": "2025-10-31 16:54:00Z",
  "schema_refresh_in_progress": false
}
```

## 6) Frontend (React) – what to do

- Initial snapshot: GET `/api/v1/get_table_status/?connection_id={id}`
- Open WebSocket to `ws(s)://<backend-domain>/ws/table-status/{id}/`
- On message: parse JSON and update UI using the same shape as the REST response
- Use `wss` in production, `ws` in local dev
- Add reconnect with exponential backoff; close on unmount
- Auth: currently not required for WS. If later enabling `AuthMiddlewareStack`, ensure cookies/JWT are present and CORS/session settings allow WS

Minimal hook example:
```ts
const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
const url = `${proto}://${backendHost}/ws/table-status/${connectionId}/`;
const ws = new WebSocket(url);
ws.onmessage = (e) => setData(JSON.parse(e.data));
```

## 7) Deployment specifics

- We use Daphne (ASGI) for WebSockets — not Gunicorn/WSGI:
  - `daphne datasyncher.asgi:application --bind 0.0.0.0 --port 8000`
- Traefik routing (docker-compose): include `/ws` in the Django router rule
- Redis required for Channels: set via env variables
  - `REDIS_URL=redis://redis:6379/0` (Docker) or your managed Redis URL
- Settings module via env:
  - `DJANGO_SETTINGS_MODULE=datasyncher.settings.production` (prod)

## 8) Environment‑driven channels config

`datasyncher/settings/base.py`:
```python
# Prefers REDIS_URL, else REDIS_HOST/REDIS_PORT with defaults
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("REDIS_URL") or (os.getenv("REDIS_HOST", "redis"), int(os.getenv("REDIS_PORT", "6379")))],
        },
    },
}
```

## 9) Docker compose updates

`web` now runs Daphne and Traefik routes `/ws`:
```yaml
web:
  command: daphne datasyncher.asgi:application --bind 0.0.0.0 --port 8000
  labels:
    - "traefik.http.routers.django.rule=Host(`qa.datasyncher.com`) && (PathPrefix(`/api`) || PathPrefix(`/admin`) || PathPrefix(`/static`) || PathPrefix(`/media`) || PathPrefix(`/ws`))"
```

## 10) Logs and troubleshooting

- Added contextual logs with `[WS]` prefixes in:
  - `apps/elttool/signals.py` (pre/post save)
  - `apps/elttool/websocket_utils.py` (payload send)
  - `datasyncher/consumers.py` (connect/disconnect/send)
- Tail logs:
  - Local: Django/Daphne console
  - Docker: `docker-compose logs -f web | grep WS`
- If messages don’t arrive:
  - Confirm Redis and `REDIS_URL`/`REDIS_HOST`/`REDIS_PORT`
  - Ensure using Daphne (ASGI)
  - Verify Traefik rule includes `/ws`
  - Check that you’re connected to the right `connection_id`
  - For bulk updates, call `send_table_status_update(connection_id)`

## 11) Code references

- Consumer: `datasyncher/consumers.py`
- Routing: `datasyncher/routing.py`
- ASGI app: `datasyncher/asgi.py`
- Settings: `datasyncher/settings/base.py`
- Signals: `apps/elttool/signals.py`
- Helper: `apps/elttool/websocket_utils.py`
- Compose: `docker-compose.yml`
- Requirements: `datasyncher/requirements.txt`

## 12) Quick verification steps

1. Start Redis and Django with Daphne
2. Connect WS: `wscat -c ws://localhost:8000/ws/table-status/18/`
3. In Django shell, change a selected table status for connection 18 and `save()`
4. Observe live JSON in the WS client
5. In Docker, ensure `/ws` routes via Traefik

That’s the complete implementation and how to use it end‑to‑end.
