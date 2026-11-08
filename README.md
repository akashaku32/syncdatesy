# syncdatesy (DataSyncher monorepo)

Monorepo for **batch GKE** deployments: Django/DRF, FastAPI/Celery, **migration-function** connectors, **SAP proxy agent** hub, React UI, ODATA on-prem agent reference code, and Kubernetes manifests. This folder is the working copy used for **GCP batch** (`datasyncher-batch`) rollouts and local development—not a single deployable artifact by itself; images and secrets live in cluster/GCR.

**Git remote:** [github.com/akashaku32/syncdatesy](https://github.com/akashaku32/syncdatesy.git)  
**Default branch:** `main`

---

## Repository layout

| Path | Role |
|------|------|
| [`Datasyncher-React-Adapter/`](Datasyncher-React-Adapter/) | Backend: Django (`apps/elttool`), FastAPI + Celery (`backend/fast-api`), connectors (`backend/migration-function`), shared connectors lib, **SAP proxy agent** (`backend/sap_proxy_agent`), K8s manifests (`k8s/`), Cloud Build configs (`backend/cloudbuild/`) |
| [`frontend/data-syncher-ui/`](frontend/data-syncher-ui/) | Customer React SPA (Vite)—calls DRF via `queryOptions` + axios |
| [`frontend/`](frontend/) | Docker/nginx wrappers for batch frontend image build |
| [`odata-agent/`](odata-agent/) | Reference / legacy ODATA proxy agent (client + server patterns) |
| [`docs/`](docs/) | Cross-repo docs index and security notes at workspace root |
| [`scripts/`](scripts/) | Rollout helpers, cluster scripts at workspace level |
| [`sap_demo_files/`](sap_demo_files/) | SAP mapping / demo artifacts |
| [`.cursor/`](.cursor/) | Cursor rules (optional; policy is in [`AGENTS.md`](AGENTS.md)) |

**Agent guides (mandatory for code changes):**

- Workspace: [`AGENTS.md`](AGENTS.md)
- Adapter: [`Datasyncher-React-Adapter/AGENTS.md`](Datasyncher-React-Adapter/AGENTS.md)
- UI: [`frontend/data-syncher-ui/AGENTS.md`](frontend/data-syncher-ui/AGENTS.md)

**Kubernetes:** start at [`Datasyncher-React-Adapter/k8s/README.md`](Datasyncher-React-Adapter/k8s/README.md). Batch-specific rollout: [`Datasyncher-React-Adapter/k8s/batch-cluster/`](Datasyncher-React-Adapter/k8s/batch-cluster/).

**SAP proxy agent:** [`Datasyncher-React-Adapter/docs/operations/proxy-agent/ARCHITECTURE.md`](Datasyncher-React-Adapter/docs/operations/proxy-agent/ARCHITECTURE.md), hub ops [`HUB.md`](Datasyncher-React-Adapter/docs/operations/proxy-agent/HUB.md), DNS/TLS [`DNS-AND-TLS.md`](Datasyncher-React-Adapter/docs/operations/proxy-agent/DNS-AND-TLS.md).

---

## Git branches

Long-lived integration:

| Branch | Purpose |
|--------|---------|
| `main` | Full monorepo snapshot (no secrets); integration baseline |

Feature branches (created from `main` for parallel work—they start identical to `main` until you commit on them):

| Branch | Suggested focus |
|--------|-----------------|
| `feature/sap-proxy-agent-hub` | Hub, unary, gateway, gRPC/mTLS, `channel_factory`, network policies |
| `feature/migration-function-connectors` | FastAPI connector routes, SAP OData proxy tests, authorization |
| `feature/frontend-data-syncher-ui` | React SPA, proxy agent UI, connection flows |
| `feature/batch-kubernetes-deploy` | `k8s/`, rollouts (`rollout-*-batch.ps1`), ingress, secrets **examples** |
| `feature/netsuite-connectors` | NetSuite ODBC / migration-function NetSuite routers |
| `feature/odata-agent` | `odata-agent/` tree and parity with `sap_proxy_agent` |
| `feature/databricks-dynamics-load` | Databricks Dynamics load, Spark job client, docs |

```powershell
git fetch origin
git switch feature/sap-proxy-agent-hub
```

---

## What is committed vs excluded

This repo is intended to be **safe to publish** (private GitHub still benefits from not storing credentials in history).

**Included:** application source, k8s **manifests** and **example** secret YAML (`secrets.example.yaml`), Cloud Build YAML, docs, tests, generated gRPC stubs where checked in, UI source.

**Excluded via [`.gitignore`](.gitignore) (do not force-add):**

- `.env`, `*.key`, `*.pem`, live `secrets.yaml`, `k8s/secrets/`, `*_LOCAL_*` secret files
- SAP/ODATA agent runtime configs: `config.json`, `config.batch.hub.json`, `odata-agent/**/keys/config.json`
- `spectra-sf-jwt/`, GCP key JSON, nested git backups (`**/.git.nested-backup/`)
- `node_modules/`, `venv/`, `__pycache__/`, local logs

### Restore secrets locally

1. **Batch cluster:** copy [`Datasyncher-React-Adapter/k8s/batch-cluster/secrets/secrets.example.yaml`](Datasyncher-React-Adapter/k8s/batch-cluster/secrets/secrets.example.yaml) → `secrets.local.yaml` (gitignored), fill from your secret manager, then apply per [`k8s/batch-cluster/`](Datasyncher-React-Adapter/k8s/batch-cluster/) docs.
2. **Django / services:** copy [`Datasyncher-React-Adapter/.env.example`](Datasyncher-React-Adapter/.env.example) → `.env` in the adapter tree.
3. **ODATA reference agent:** see [`odata-agent/README.md`](odata-agent/README.md) (`config.example.json` → `config.json`).
4. **Security overview:** [`docs/security/secrets-and-settings.md`](docs/security/secrets-and-settings.md).

If AWS (or other) credentials were ever stored in a local `secrets.yaml` that left this machine, **rotate or disable those keys in the cloud console**—they must not be assumed confidential.

---

## SAP proxy agent (batch) — platform → hub

High-level flow for **proxy** SAP OData connections:

1. **UI / Django** triggers connection test → **migration-function** (`test_sap_odata_connection` with `connection_method=proxy`).
2. **migration-function** opens mTLS gRPC to **sap-proxy-agent-hub** (`Datasynchor.TestSapConnection`), using env from deployment + mounted `sap-proxy-agent-hub-mtls`.
3. **Hub** forwards to the **on-premises agent** registered by `proxy_agent` id (`cust_id`). If no agent session: gRPC `UNAVAILABLE` with details like *Agent for customer … is not connected*.
4. Friendly errors: [`backend/migration-function/services/sap/proxy.py`](Datasyncher-React-Adapter/backend/migration-function/services/sap/proxy.py) (`format_proxy_error_msg`).
5. TLS peer name: [`backend/sap_proxy_agent/client/channel_factory.py`](Datasyncher-React-Adapter/backend/sap_proxy_agent/client/channel_factory.py) maps in-cluster Service DNS `sap-proxy-agent-hub-svc…` → cert SAN **`sap-proxy-agent-hub`**.

**Batch deployment env (migration-function):**

| Variable | Typical value |
|----------|----------------|
| `PROXY_AGENT_SERVER_HOST` | `sap-proxy-agent-hub-svc.default.svc.cluster.local` |
| `PROXY_AGENT_SERVER_PORT` | `50051` |
| `PROXY_AGENT_HUB_MTLS_DIR` | `/etc/sap-proxy-agent-hub-client/mtls` |
| `PROXY_AGENT_HUB_SSL_TARGET_NAME` | Optional; e.g. `agent-hub.gcp.datasyncher.com` when dialing LB IP |

**Rollout migration-function image (batch):**

```powershell
cd Datasyncher-React-Adapter
.\k8s\batch-cluster\rollout-migration-function-batch.ps1 -Build
```

Context: `gke_datasyncher-batch_us-central1_datasyncher-batch-cluster`  
Image: `gcr.io/datasyncher-batch/datasyncher-migration-function:batch`

---

## Development quick start

### Prerequisites

- Python 3.10+, Node 18+ (UI), Docker (images), `gcloud` + `kubectl` (batch), `gh` (optional)

### Backend (adapter)

```powershell
cd Datasyncher-React-Adapter
# See Datasyncher-React-Adapter/AGENTS.md for tests:
python manage.py test apps.elttool.tests
cd backend/migration-function; pytest tests/ -q
```

### Frontend

```powershell
cd frontend/data-syncher-ui
npm ci
npm run lint
npm run build
```

### Security checks (before PR)

- Adapter: `pre-commit run` from `Datasyncher-React-Adapter`
- UI: `npm run audit:deps`

---

## Batch GKE workloads (reference)

| Deployment | Image tag (batch) | Notes |
|------------|-------------------|--------|
| `django`, `fastapi`, `frontend` | `:batch` / project registry | App pool |
| `migration-function` | `datasyncher-migration-function:batch` | Connector HTTP, SAP proxy gRPC client |
| `sap-proxy-agent-hub` | `datasyncher-sap-proxy-agent-hub:batch` | Agent mTLS `:50051`, keep **replicas: 1** until shared registry |
| `sap-proxy-agent-unary` | `datasyncher-sap-proxy-agent-unary:batch` | Bootstrap token validation |
| `proxy-agent-gateway` | HTTPS progress fallback | See proxy-agent docs |

Network policies: [`Datasyncher-React-Adapter/k8s/network-policies/`](Datasyncher-React-Adapter/k8s/network-policies/) (e.g. `allow-migration-function-to-sap-proxy-agent-hub`).

---

## Contributing / workflow

1. Branch from `main` or an existing `feature/*` branch.
2. Follow [`AGENTS.md`](AGENTS.md)—minimal diffs, no secrets in source, tenant-scoped connections on backend.
3. Run lint/tests for touched trees.
4. Open PR to `main`; batch images via Cloud Build under `backend/cloudbuild/batch/`.

---

## License and ownership

Proprietary DataSyncher / Spectra work product. Do not commit customer credentials or production Secret manifests. For access to GCR and batch cluster credentials, use your team’s secret store—not this repository.
