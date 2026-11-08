# Backend Cloud Build configs

Submit from **`backend/`** (build context is the backend tree unless noted).

```powershell
cd backend
gcloud builds submit --config=cloudbuild/batch/migration-function.yaml .
```

## Layout

| Path | Image (example) | Service |
|------|-----------------|---------|
| `batch/migration-function.yaml` | `datasyncher-migration-function:batch` | migration-function |
| `batch/fastapi.yaml` | `datasyncher-fast-api:batch` | FastAPI + Celery control |
| `batch/sap-proxy-agent-hub.yaml` | `datasyncher-sap-proxy-agent-hub:batch` | SAP proxy gRPC hub |
| `batch/proxy-agent-gateway.yaml` | `datasyncher-proxy-agent-gateway:batch` | HTTPS progress gateway |
| `qa/migration-function.yaml` | `datasyncher-migration-function:qa` | QA migration-function |
| `qa/fastapi.yaml` | `datasyncher-fast-api:qa` | QA FastAPI |

SAP proxy **code** lives under [`../sap_proxy_agent/`](../sap_proxy_agent/README.md) (hub, unary, gateway, client).

Legacy paths at `backend/cloudbuild-*.yaml` were removed — update scripts to use `cloudbuild/<env>/...`.
