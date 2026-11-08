# DataSyncher monorepo — rules for ALL AI agents

These instructions apply to **every** coding agent (Cursor, Claude Code, Copilot, Codex, Windsurf, Gemini CLI, custom bots, etc.). Tool-specific folders (for example `.cursor/rules/`) only add extra detail; they do **not** replace this file.

## Repos in this workspace

| Path | Role | Agent guide |
|------|------|-------------|
| [`Datasyncher-React-Adapter/`](Datasyncher-React-Adapter/) | Django/DRF, FastAPI, migration-function, k8s | [`Datasyncher-React-Adapter/AGENTS.md`](Datasyncher-React-Adapter/AGENTS.md) |
| [`frontend/data-syncher-ui/`](frontend/data-syncher-ui/) | React SPA (Vite) | [`frontend/data-syncher-ui/AGENTS.md`](frontend/data-syncher-ui/AGENTS.md) |

Read the matching repo `AGENTS.md` before editing that tree. If the task spans both, implement API contract in the adapter first, then wire UI hooks in `queryOptions/`.

## Mandatory standards (always)

1. **Reuse first** — search for an existing helper/service/hook before writing a new one. No speculative wrappers, unused parameters, or dead code.
2. **Minimal diffs** — change only what the task requires. Do not refactor unrelated files.
3. **No secrets in source** — env / secret store only; fail closed when required secrets are missing. Never commit `.env`, `*.key`, or real k8s Secret values.
4. **Security fail-closed** — `hmac.compare_digest` for shared keys; no `verify=False`; no bare `AllowAny` without a justifying comment; never put tokens in URLs or logs.
5. **Tenant isolation (backend)** — load `Connection` via `apps.elttool.services.tenant_scope.get_connection_for_user`, not PK-only lookups.
6. **API calls (frontend)** — only through `src/queryOptions/**` + `lib/axios`; never import axios inside `components/`.
7. **Migrations** — new source→dest pairs go through `services.migration_common` registry + shared Snowflake/Databricks loaders; never copy a whole `to_*.py`.
8. **No `print`** — use `logging` (backend) / avoid `console.log` in production UI paths.
9. **Size discipline** — prefer files under ~800 lines (backend) / ~400 lines (React components); split instead of growing god modules.
10. **This stack is SPA + DRF** — do not introduce Django templates for customer UI.

## Security documentation

Human-readable security notes (by layer, with gaps): [`docs/security/`](docs/security/).  
Docs index: [`docs/README.md`](docs/README.md).  
Proxy agent progress (mTLS + HMAC + dedicated host): [`docs/security/proxy-agent-progress.md`](docs/security/proxy-agent-progress.md).

## Mechanical enforcement (not optional)

Rules alone do not stop bad code. Always run the relevant checks before finishing:

- Backend: `pre-commit run` / `scripts/security_checks.ps1` (ruff S, gitleaks, pip-audit)
- Frontend: `npm run lint`, `npm run build`, `npm run audit:deps`

## Tool adapters (same rules, different filenames)

| Tool | Entry file (points at this guide) |
|------|-----------------------------------|
| Any / AGENTS.md consumers | This file + per-repo `AGENTS.md` |
| Cursor | `.cursor/rules/` + this file |
| Claude Code | `CLAUDE.md` |
| GitHub Copilot | `.github/copilot-instructions.md` |

If you are an agent that only loads one of those files, treat its contents as **binding**.
