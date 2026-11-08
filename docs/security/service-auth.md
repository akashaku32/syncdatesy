# Service-to-service authentication

**Status:** Done for Django internal DRF APIs and FastAPI orchestration middleware. Migration-function wiring is incomplete (missing auth module).

## Goals

- Internal HTTP APIs must not be callable without a shared secret.
- Compare secrets in constant time (`hmac.compare_digest`).
- Fail closed if the shared key is unset.

## Header contract

| Header | Purpose |
|--------|---------|
| `X-INTERNAL-KEY` | Shared secret between Django, FastAPI, and migration-function |

Outbound Django helpers attach this via `internal_service_headers()` in `apps/elttool/v1/utils.py`.

## Django / DRF

### `IsInternalService`

- File: `apps/elttool/v1/permissions.py`
- Reads `X-INTERNAL-KEY`, compares to `settings.INTERNAL_API_KEY` with `hmac.compare_digest`.
- Denies if the expected key is unset or the header does not match.

Used on internal routes under `/api/v1/internal/...` (for example WS push helpers and Salesforce OAuth token helpers in `views.py` / `ops_views.py`).

### Role permissions (product RBAC)

Same module defines role gates such as:

- `IsAdministrator`
- `IsAnalystOrAdmin`
- `IsBillingOrAdmin`
- `IsPlatformSuperuser`

These protect human-facing APIs (orthogonal to the internal service key). Broader product RBAC docs live under `Datasyncher-React-Adapter/docs/rbac/`.

## FastAPI orchestration

- File: `backend/fast-api/app.py`
- Middleware:
  - Public: `/healthz`, `/health`, `/metrics`, `/`
  - Everything else: require `INTERNAL_API_KEY`
    - **503** if key not configured
    - **401** if header missing/wrong (`hmac.compare_digest`)
- In-cluster Django callbacks: `backend/fast-api/websocket_utils.py` sends `X-INTERNAL-KEY` and `X-Forwarded-Proto: https` (avoids TLS redirect mismatches).

## Migration-function

- `backend/migration-function/app.py` intends `Depends(verify_internal_token)` on routers (except health).
- **Gap:** `utils.internal_auth` (`verify_internal_token`) is imported but the module is **missing** from the tree — treat migration-function auth as incomplete until that module is restored/implemented.

## Proxy agent progress

- **Primary:** gRPC `ReportProgress` on hub mTLS (hub relays to Django ingest).
- **Fallback:** `proxy-agent-gateway` on `agent-api.<domain>` with client mTLS + HMAC → same Django ingest.
- Django verifies HMAC on HTTPS path; tenant/job binding on both paths.
- **`INTERNAL_API_KEY` must never appear in agent `config.json`.**
- [`proxy-agent-progress.md`](./proxy-agent-progress.md) · [README-AGENT.md](../../Datasyncher-React-Adapter/docs/operations/proxy-agent/README-AGENT.md)

## Gaps / policy conflicts

- Legacy `AllowAny` and `verify=False` still exist in places; new code must not add them without a justifying comment (`AGENTS.md`).
- Keep internal keys out of browser traffic — only server-to-server.
