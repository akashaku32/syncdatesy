# DataSyncher security documentation

Canonical security docs for the monorepo (Django/DRF, FastAPI, migration-function, React SPA, Kubernetes, agent rules).

## How to read this

| Doc | Audience | Contents |
|-----|----------|----------|
| [overview.md](./overview.md) | Everyone | Threat model summary, layers, status legend |
| [secrets-and-settings.md](./secrets-and-settings.md) | Backend / ops | Env secrets, fail-closed settings, rotation |
| [service-auth.md](./service-auth.md) | Backend | `X-INTERNAL-KEY`, HMAC, DRF permissions, FastAPI middleware |
| [proxy-agent-progress.md](./proxy-agent-progress.md) | Backend / ops | Agent progress API: dedicated host, **mTLS**, **HMAC**, gateway, DNS |
| [websockets.md](./websockets.md) | Backend | WS JWT/token auth and ownership checks |
| [tenant-isolation.md](./tenant-isolation.md) | Backend | Company-scoped `Connection` access |
| [field-encryption.md](./field-encryption.md) | Backend / ops | AES-GCM credentials, migration `0123` |
| [frontend.md](./frontend.md) | Frontend | CSP/nginx headers, axios/queryOptions, tokens |
| [kubernetes.md](./kubernetes.md) | Ops / platform | Pod securityContext, NetworkPolicies, secrets hygiene |
| [migration-services.md](./migration-services.md) | Backend | Shared migration registry (reduces credential/TLS drift) |
| [gaps-and-roadmap.md](./gaps-and-roadmap.md) | Leads | Known gaps and next hardening steps |

## Related docs (elsewhere)

| Topic | Location |
|-------|----------|
| Secret rotation checklist | [`Datasyncher-React-Adapter/docs/operations/SECRET_ROTATION.md`](../../Datasyncher-React-Adapter/docs/operations/SECRET_ROTATION.md) |
| Proxy agent gateway ops / DNS | [`Datasyncher-React-Adapter/docs/operations/proxy-agent/`](../../Datasyncher-React-Adapter/docs/operations/proxy-agent/) |
| RBAC (product roles) | [`Datasyncher-React-Adapter/docs/rbac/`](../../Datasyncher-React-Adapter/docs/rbac/) |
| K8s secrets hygiene | [`Datasyncher-React-Adapter/k8s/secrets/README.md`](../../Datasyncher-React-Adapter/k8s/secrets/README.md) |
| Pod hardening notes | [`Datasyncher-React-Adapter/k8s/deployments/README-HARDENING.md`](../../Datasyncher-React-Adapter/k8s/deployments/README-HARDENING.md) |
| Agent coding rules | [`AGENTS.md`](../../AGENTS.md) (root) + per-repo `AGENTS.md` |

## Status legend

- **Done** — implemented in code and expected in deployed images when rolled out
- **Partial** — some call sites / envs only
- **Missing** — documented or imported but not present / not configured
