# Frontend security

**Status:** Done for nginx headers, central HTTP client rules, and route permission gates. HttpOnly refresh (Phase 4b) is not done.

## Goals

- Harden the SPA against common browser threats (clickjacking, MIME sniffing, loose CSP).
- Keep secrets out of `VITE_*` and source.
- Force API traffic through one authenticated client path.

## nginx security headers

Files:

- `frontend/nginx.conf`
- `frontend/data-syncher-ui/nginx.conf` (batch Cloud Build image)

Headers (also duplicated on locations that set their own `Cache-Control` / `add_header`):

| Header | Intent |
|--------|--------|
| `Content-Security-Policy` | Restrict script/style/img/connect / `frame-ancestors 'none'` |
| `Strict-Transport-Security` | Force HTTPS |
| `X-Frame-Options: SAMEORIGIN` | Clickjacking mitigation |
| `X-Content-Type-Options: nosniff` | MIME sniffing |
| `Referrer-Policy` | Limit referrer leakage |
| `Permissions-Policy` | Disable camera/mic/geo/payment/usb |

Hashed `/assets/` are cached long-term; `index.html` is `no-store` so deploys do not strand browsers on deleted chunk names.

## Application layer

| Control | Where | Notes |
|---------|-------|-------|
| Axios API client | `src/lib/axios/api-client.ts` | Bearer from token store; refresh flow |
| Token store | `src/lib/auth/token-store.ts` | Centralized; still localStorage + js-cookie |
| Route guards | `ProtectedRoute` + `AppRoutes` | Permission keys (connectors, billing, users, …) |
| Query layer | `src/queryOptions/**` | Only place components should trigger HTTP |
| ESLint | `eslint.config.js` | Warns on axios imports from `components/` |
| Dependency audit | `npm run audit:deps` | `npm audit --omit=dev` |

Agent rules: `frontend/data-syncher-ui/AGENTS.md` and `.cursor/rules/security-frontend.mdc`.

## Gaps

- **Phase 4b:** move refresh token to httpOnly cookie; keep access token in memory — not implemented.
- Some legacy components may still type/import axios directly; new code must not.
- Never put API keys or internal service keys in `VITE_*` env vars (they are public in the bundle).
