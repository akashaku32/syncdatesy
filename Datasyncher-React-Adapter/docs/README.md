# Datasyncher-React-Adapter documentation

Hierarchical index. Cross-cutting **security** lives in the monorepo [`docs/security/`](../../docs/security/).

```text
docs/
├── README.md                 ← you are here
├── security/                 ← pointer to monorepo security docs
├── operations/               ← runbooks, scheduler, Celery, proxy-agent ops
│   └── proxy-agent/          ← gateway deploy + DNS/TLS
├── api/                      ← HTTP API specs
├── connectors/               ← S3/Snowflake/Salesforce/SAP transform
├── databricks/               ← Dynamics F&O → Databricks
├── websockets/               ← Channels / table status
├── rbac/                     ← product roles
├── implementation-notes/     ← one-off fixes / patterns
├── archive/                  ← historical
└── deployment/               ← deploy configs
```

## By task

| Task | Go to |
|------|--------|
| Whole-app HLD / LLD and Fivetran comparison | [monorepo architecture/hld-lld](../../docs/architecture/hld-lld.md) |
| Proxy agent progress security (mTLS / HMAC) | [monorepo proxy-agent-progress](../../docs/security/proxy-agent-progress.md) |
| Proxy agent deploy / DNS | [operations/proxy-agent/](./operations/proxy-agent/) |
| Secret rotation | [operations/SECRET_ROTATION.md](./operations/SECRET_ROTATION.md) |
| Full API list | [api/](./api/) |
| Databricks staging | [databricks/](./databricks/) |
| Connector guides | [connectors/](./connectors/) |
| NetSuite SuiteAnalytics ODBC | [operations/connectors/netsuite-suiteanalytics-odbc.md](./operations/connectors/netsuite-suiteanalytics-odbc.md) |
| WebSockets | [websockets/](./websockets/) |
| RBAC | [rbac/](./rbac/) |
| Security overview | [security/README.md](./security/README.md) → monorepo |

## Folder map

| Folder | Contents |
|--------|----------|
| **[security/](./security/)** | Pointers to monorepo threat model, mTLS/HMAC, secrets, k8s |
| **[operations/](./operations/)** | Celery, Prometheus, scheduler, k8s QA, **proxy-agent/** |
| **[api/](./api/)** | Full API specs, source/destination APIs |
| **[connectors/](./connectors/)** | S3/Snowflake/Salesforce/SAP entity transform |
| **[databricks/](./databricks/)** | Dynamics F&O → Databricks, staging, permissions |
| **[websockets/](./websockets/)** | WebSocket implementation and local testing |
| **[rbac/](./rbac/)** | Role-based access control |
| **[implementation-notes/](./implementation-notes/)** | One-off fixes, logging, UI patterns |
| **[archive/](./archive/)** | Historical notes |
| **[deployment/](./deployment/)** | Deployment configs |

## Quick links — Dynamics → Databricks

1. [databricks/datasyncher-platform-staging-and-permissions.md](./databricks/datasyncher-platform-staging-and-permissions.md)
2. [databricks/databricks-dynamics-load-scenarios.md](./databricks/databricks-dynamics-load-scenarios.md)
3. [databricks/databricks-managed-volume-staging-setup.md](./databricks/databricks-managed-volume-staging-setup.md)
4. [databricks/databricks-client-cloud-staging-setup.md](./databricks/databricks-client-cloud-staging-setup.md)
