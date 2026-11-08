# SAP proxy agent unary RPC (`unary_rpc.py`, port `:50053`)

ODATA reference: `odata-agent/.../grpc_files/unary_rpc.py`.  
Monorepo implementation:

| Path | Role |
|------|------|
| `backend/sap_proxy_agent/unary/unary_rpc.py` | gRPC server (bootstrap + GCP + legacy status) |
| `backend/sap_proxy_agent/unary/get_gcp_access_token.py` | Downscoped GCS token for agents |
| `apps/elttool/services/proxy_agent_bootstrap.py` | `ValidateBootstrapToken` → `ProxyAgent` DB |
| `apps/elttool/services/proxy_agent_legacy_status.py` | Legacy `TableStatus*` → `apply_progress_event` |

**Not in Django as a file** — unary runs as its **own process/pod** with `django.setup()` for ORM access (same pattern as ODATA, but uses `apps.elttool`).

## Services

| gRPC | Monorepo behavior |
|------|-------------------|
| `TokenValidationService.ValidateBootstrapToken` | Decrypt/compare bootstrap token; rotate via `generate_bootstrap_token` |
| `GCPTokenBrokerService.GetDownscopedToken` | Env: `PROXY_AGENT_GCP_PROJECT`, `PROXY_AGENT_GCS_BUCKET`, `PROXY_AGENT_GCS_OBJECT_PREFIX` |
| `TableStatusService.*` (legacy) | Maps Struct → `table_started` / `table_completed` / `table_failed` → Django ORM (same as `ReportProgress`) |

Hub **Enroll** (`server.py` reference) calls **`localhost:50053`** — in batch, hub env **`TOKEN_VALIDATION_GRPC_TARGET=sap-proxy-agent-unary-svc:50053`**.

## Local run

```powershell
cd syncdatesy-adapter
$env:DJANGO_SETTINGS_MODULE = "datasyncher.settings.qa"
$env:UNARY_RPC_MTLS_CONFIG = "path\to\ODATA\...\grpc_files\config.json"
python backend/sap_proxy_agent/unary/unary_rpc.py
```

## mTLS

Same material as ODATA `grpc_files/config.json` (`client_svr_key` / `client_svr_cert` as server creds) or env:

- `UNARY_RPC_MTLS_DIR` — mount dir with `client_svr_server.crt/key` and `ca.crt` (batch: same secret as hub)
- `UNARY_RPC_SERVER_KEY_PEM` / `UNARY_RPC_SERVER_CERT_PEM` / `UNARY_RPC_TRUSTED_CA_PEM`

## Deploy

Build from adapter root:

```bash
docker build -f backend/sap_proxy_agent/unary/Dockerfile -t gcr.io/datasyncher-batch/datasyncher-sap-proxy-agent-unary:batch .
```

Batch rollout:

```powershell
.\k8s\batch-cluster\rollout-sap-proxy-agent-unary-batch.ps1 -Build -ApplyManifests
```

Primary progress path remains **`ReportProgress` → hub → Django**; legacy `:50053` status RPCs exist for older agent builds.
