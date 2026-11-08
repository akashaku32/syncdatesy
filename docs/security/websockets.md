# WebSocket authentication

**Status:** Done for auth helpers and consumer ownership checks. Query-string tokens remain a known policy gap.

## Goals

- Only authenticated users can open app WebSockets.
- A user may only subscribe to resources belonging to their company / connections.

## What was implemented

### Auth helpers

File: `apps/elttool/websocket/auth.py`

- Accept JWT or DRF Token from:
  - `Authorization: Bearer …`, or
  - query parameter `token` (legacy / client convenience)
- Helpers:
  - `user_belongs_to_company`
  - `user_owns_connection`
  - `user_owns_migration_session`

### Consumers

Example: `apps/elttool/websocket/connection_table_status.py` (and related consumers)

- Authenticate on connect.
- Enforce ownership / company membership before streaming.
- Close with **4401** (unauthenticated) or **4403** (forbidden) on failure.

Ingress routes `/ws/` to Django (see batch ingress under `k8s/batch-cluster/ingress/`).

## Client expectations

- Prefer sending the token in the `Authorization` header when the client stack allows it.
- Frontend WS helpers live under the SPA (see `websocket` chunks / hooks such as migration status WS).

## Gaps

- Accepting tokens in the **query string** contradicts the root `AGENTS.md` rule “never put tokens in URLs”. Prefer migrating clients to header-only auth and then rejecting query tokens.
- Ensure every new consumer calls the shared helpers — do not open anonymous WS endpoints.
