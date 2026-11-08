# Security overview

DataSyncher is a multi-tenant SPA + DRF platform. Security is applied in layers so a single bug does not expose every tenant or every internal service.

```text
Browser (SPA)
  │  HTTPS + CSP / HSTS (nginx)
  │  Bearer token (token-store → axios)
  ▼
Ingress (nginx)
  ├─ /            → frontend
  ├─ /api/v1/     → Django / DRF
  ├─ /api/        → FastAPI orchestration
  └─ /ws/         → Django Channels
        │
        ├─ Internal X-INTERNAL-KEY (HMAC) → FastAPI / migration-function
        └─ Tenant + role checks on Connection / company
```

## Layers (what each one protects)

| Layer | Protects against | Primary docs |
|-------|------------------|--------------|
| Secrets / settings | Keys in git, silent insecure defaults | [secrets-and-settings.md](./secrets-and-settings.md) |
| Service auth | Unauthenticated internal APIs | [service-auth.md](./service-auth.md) |
| WebSockets | Hijacked live status / cross-tenant WS | [websockets.md](./websockets.md) |
| Tenant isolation | Cross-company Connection access | [tenant-isolation.md](./tenant-isolation.md) |
| Field encryption | Credential leakage at rest | [field-encryption.md](./field-encryption.md) |
| Frontend | XSS framing, token sprawl, rogue API calls | [frontend.md](./frontend.md) |
| Kubernetes | Privilege escalation, lateral pod traffic | [kubernetes.md](./kubernetes.md) |
| Migration registry | Divergent credential/TLS handling in loaders | [migration-services.md](./migration-services.md) |
| Agent rules | AI/tooling regenerating insecure patterns | Root [`AGENTS.md`](../../AGENTS.md) |

## Design principles (enforced in AGENTS.md)

1. **Fail closed** — missing `INTERNAL_API_KEY` / `SECRET_KEY` must not fall back to a default.
2. **Constant-time compare** — shared keys via `hmac.compare_digest`.
3. **No secrets in source** — env / secret store only; never commit `.env`, `*.key`, or live Secret YAML values.
4. **Tenant scope** — load `Connection` for the authenticated user’s company, not by PK alone.
5. **No tokens in URLs or logs** — prefer `Authorization` headers (WS query-token is a known gap).
6. **UI API surface** — frontend HTTP only through `src/queryOptions/**` + `lib/axios`.

## Implementation map (quick)

| Area | Status | Key paths |
|------|--------|-----------|
| Fail-closed Django / FastAPI keys | Done | `datasyncher/settings/base.py`, `backend/fast-api/app.py` |
| `IsInternalService` | Done | `apps/elttool/v1/permissions.py` |
| WS auth helpers | Done | `apps/elttool/websocket/auth.py` |
| Shared `tenant_scope` module | Missing | Expected at `apps/elttool/services/tenant_scope.py` |
| Field encryption helpers + mig `0123` | Done (inactive without key) | `apps/utils/encryption.py` |
| nginx security headers | Done | `frontend/**/nginx.conf` |
| Pod securityContext (django/fastapi/migration) | Done | `k8s/deployments/` |
| Cluster `FIELD_ENCRYPTION_KEY` | Missing | Not in k8s secret templates |

See [gaps-and-roadmap.md](./gaps-and-roadmap.md) for the full backlog.
