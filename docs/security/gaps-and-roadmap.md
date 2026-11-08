# Gaps and roadmap

Honest backlog relative to the hardening plan and root `AGENTS.md`. Use this for prioritization — not as a claim that production is fully locked down.

## Highest impact

| # | Gap | Risk | Suggested next step |
|---|-----|------|---------------------|
| 1 | Shared `apps/elttool/services/tenant_scope.py` missing | Cross-tenant PK access if a view forgets local filters | Implement module; migrate batch/pipeline/transform views onto it |
| 2 | Migration-function `utils.internal_auth` missing | Internal migration API may be unauthenticated or fail to import | Restore `verify_internal_token` + HMAC check matching FastAPI |
| 3 | `FIELD_ENCRYPTION_KEY` not in cluster secret templates | Credentials stay on legacy encryption path | Add secret, roll Django, run mig `0123` |
| 4 | WS tokens still accepted in query string | Token leakage via logs/referrers | Header-only auth; then reject query `token` |
| 5 | Frontend tokens in localStorage | XSS can steal session | Phase 4b httpOnly refresh + in-memory access |

## Medium

| Gap | Notes |
|-----|-------|
| Proxy agent public DNS / TLS (`agent-api.gcp.datasyncher.com`) | Batch gateway + CA ready; A record + cert-manager still required for public mTLS edge — [DNS-AND-TLS.md](../../Datasyncher-React-Adapter/docs/operations/proxy-agent/DNS-AND-TLS.md) |
| `scripts/security_checks.ps1` referenced but absent | Wire ruff S / gitleaks / pip-audit as documented in `AGENTS.md` |
| `apps.utils.http_client` referenced in rules but absent | Shared TLS-verifying requests session (`verify=True`) |
| Legacy `verify=False` / bare `AllowAny` | Inventory and remove or comment-justify |
| Celery (and other) pods without full securityContext | Match django/fastapi/migration profile |
| Image digest pinning | Reduce tag-mutable supply-chain risk |

## Already in good shape (keep maintaining)

- Fail-closed `SECRET_KEY` / `INTERNAL_API_KEY` in Django settings
- FastAPI internal-key middleware
- DRF `IsInternalService` + role permissions
- WS ownership helpers
- Field encryption library + mig `0123` (code ready)
- Frontend nginx CSP/HSTS suite
- NetworkPolicies + PDB + selected pod hardening
- Agent rules (`AGENTS.md`, Cursor/Claude/Copilot adapters)

## Suggested sequencing

1. Ship `tenant_scope` + migration-function `internal_auth` (correctness of boundaries).
2. Enable `FIELD_ENCRYPTION_KEY` in batch (ops + migrate).
3. Frontend Phase 4b token storage.
4. Remove query-string WS tokens; purge `verify=False`.
5. Mechanical CI: `security_checks.ps1` + strengthen pre-commit.
