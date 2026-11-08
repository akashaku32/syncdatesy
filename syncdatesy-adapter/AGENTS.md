# DataSyncher backend — rules for ALL AI agents

Binding for Cursor, Claude Code, Copilot, Codex, Windsurf, and any other agent editing this repo.  
Workspace overview: [`../AGENTS.md`](../AGENTS.md).  
Cursor extras (examples/globs): `.cursor/rules/` — same standards, not a different policy.

## Architecture

| Area | Path |
|------|------|
| Django / DRF / Channels | `apps/elttool`, `apps/support_admin`, `datasyncher/` |
| Orchestration FastAPI + Celery | `backend/fast-api/` |
| Connector migrations | `backend/migration-function/` |
| Shared DB connectors | `backend/datasyncher_connections/` |
| K8s | `k8s/` |

This is a **SPA + DRF API** backend (not Django templates for customer UI).

## Mandatory coding rules

1. Search for an existing helper/service before writing a new one. No speculative wrappers or unused parameters.
2. Secrets only from env / secret store; fail closed when `SECRET_KEY` / `INTERNAL_API_KEY` (etc.) are missing.
3. Compare internal keys with `hmac.compare_digest`. Never `verify=False`, bare `AllowAny` without a comment, `eval`/`exec`/`pickle`/`shell=True`.
4. Never put tokens in URLs or logs. Use `apps.utils.http_client` for outbound HTTP.
5. Tenant-scope Connections: `apps.elttool.services.tenant_scope.get_connection_for_user` — never PK-only `Connection.objects.get(pk=...)` in request handlers.
6. New migration pairs: `services.migration_common` registry + shared Snowflake/Databricks loaders. Never copy a whole `to_*.py`.
7. FastAPI/migration-function routes require `X-INTERNAL-KEY` (except `/healthz`).
8. No `print` — use `logging`. Prefer files under ~800 lines; do not grow `views.py` with new endpoints (use a feature module).
9. K8s: `securityContext`, probes, pinned tags; never commit real Secret values.
10. Minimal diffs; no new dependencies without a stated reason.

## Commands

```bash
python manage.py test apps.elttool.tests
python manage.py migrate
cd backend/migration-function && pytest tests/ -q
pre-commit install
pre-commit run --all-files
# or: scripts/security_checks.ps1
```

## Before finishing

Reuse helpers → fail closed on secrets → tenant scope → lint/tests for the touched area.
