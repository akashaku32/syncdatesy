# DataSyncher documentation

Monorepo docs root. Prefer this tree for **security and cross-cutting** topics; product/ops detail for the Django adapter lives under [`Datasyncher-React-Adapter/docs/`](../Datasyncher-React-Adapter/docs/).

```text
docs/
├── README.md                 ← you are here
├── architecture/
│   └── hld-lld.md            ← control plane, data plane, one pipeline run
└── security/                 ← threat model, auth, encryption, k8s, proxy agent
    ├── README.md
    ├── overview.md
    ├── secrets-and-settings.md
    ├── service-auth.md
    ├── proxy-agent-progress.md   ← mTLS + HMAC + dedicated host
    ├── websockets.md
    ├── tenant-isolation.md
    ├── field-encryption.md
    ├── frontend.md
    ├── kubernetes.md
    ├── migration-services.md
    └── gaps-and-roadmap.md
```

## Quick start by role

| Role | Start here |
|------|------------|
| Architecture (HLD / LLD) | [architecture/hld-lld.md](./architecture/hld-lld.md) |
| Security / platform | [security/README.md](./security/README.md) |
| Proxy agent progress (mTLS/HMAC) | [security/proxy-agent-progress.md](./security/proxy-agent-progress.md) |
| Backend feature work | [Datasyncher-React-Adapter/docs/README.md](../Datasyncher-React-Adapter/docs/README.md) |
| Frontend SPA | [frontend/data-syncher-ui/](../frontend/data-syncher-ui/) + [security/frontend.md](./security/frontend.md) |
| Agent coding rules | [AGENTS.md](../AGENTS.md) |

## Related trees (not duplicated here)

| Tree | Purpose |
|------|---------|
| [`Datasyncher-React-Adapter/docs/`](../Datasyncher-React-Adapter/docs/) | API, operations, connectors, RBAC, websockets, proxy-agent ops |
| [`Datasyncher-React-Adapter/k8s/`](../Datasyncher-React-Adapter/k8s/) | Manifests; batch cluster under `k8s/batch-cluster/` |
