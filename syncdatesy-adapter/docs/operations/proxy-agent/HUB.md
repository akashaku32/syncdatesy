# SAP proxy agent gRPC hub

Dedicated pod for agent mTLS on **`:50051`** (`StartDataLoad`, metadata, **`ReportProgress`**).  
Progress relay target: Django `POST /api/v1/internal/proxy-agent/migration-progress/` (same as [GATEWAY.md](./GATEWAY.md)).

## Deploy (batch)

```powershell
# First time (creates Deployment/Service/netpol) + build :batch + rollout:
.\k8s\batch-cluster\rollout-sap-proxy-agent-hub-batch.ps1 -Build -ApplyManifests

# migration-function gRPC client + hub together:
.\k8s\batch-cluster\rollout-proxy-agent-batch.ps1 -Build -ApplyHubManifests
```

Manual apply:

```bash
kubectl apply -f k8s/deployments/sap-proxy-agent-hub.yaml \
  -f k8s/services/sap-proxy-agent-hub-svc.yaml \
  -f k8s/network-policies/allow-hub-to-django.yaml
```

Set `PROXY_AGENT_SERVER_HOST` in agent `config.json` to the hub LB hostname (not `django-svc`).

**Replicas (batch):** keep **`replicas: 1`** on [`sap-proxy-agent-hub`](../../../k8s/deployments/sap-proxy-agent-hub.yaml) while the Connect stream registry is **in-memory per pod**. With multiple hub pods behind the LoadBalancer, the agent may register on pod A while migration-function’s gRPC call lands on pod B → “Agent for customer … is not connected.” Re-enable `replicas: 2` only after shared registry (e.g. Redis) or **session affinity** on the hub Service.

## Image

- Entrypoint: `python -m sap_proxy_agent.hub` → HTTP `:8081` health + gRPC `:50051` loads + `:50052` bootstrap Enroll.
- Load + agent stream: [`hub_grpc_server.py`](../../../backend/sap_proxy_agent/hub/hub_grpc_server.py) (ODATA `server.py` parity) + `DatasynchorProgress` from [`grpc_progress.py`](../../../backend/sap_proxy_agent/hub/grpc_progress.py).
- mTLS mount: `HUB_MTLS_DIR` (`client_svr_server.crt/key`, `ca.crt`, `ca.key`). Enroll needs `TOKEN_VALIDATION_GRPC_TARGET` → unary `:50053`.
- Env: `DJANGO_INTERNAL_BASE_URL`, `INTERNAL_API_KEY` (from `datasyncher-secrets`), mTLS under `/etc/sap-proxy-agent-hub/mtls`.

## migration-function (platform gRPC client to hub)

SAP OData **proxy** flows call the hub from [`channel_factory.py`](../../../backend/sap_proxy_agent/client/channel_factory.py) using the same CA + **platform client** cert/key as agents use in dev `config.json` (keys `trusted_ca_cert`, `client_svr_cert`, `client_svr_key`).

**Kubernetes (recommended):**

| Item | Value |
|------|--------|
| Hub target | Default: `PROXY_AGENT_SERVER_HOST=sap-proxy-agent-hub-svc.default.svc.cluster.local`, `PROXY_AGENT_SERVER_PORT=50051` (see [`migration-function.yaml`](../../../k8s/deployments/migration-function.yaml)). |
| TLS hostname | gRPC resolves the Service name to a **ClusterIP**; hostname checks use `grpc.ssl_target_name_override`. In batch, the hub leaf cert SAN is **`sap-proxy-agent-hub`** (and optional public name), not `sap-proxy-agent-hub-svc…`, so the client maps **Service FQDN → `sap-proxy-agent-hub`**. For LB IP dials, set `PROXY_AGENT_HUB_SSL_TARGET_NAME=agent-hub.gcp.datasyncher.com` (or whichever name is in the cert). |
| mTLS | Mount Secret `sap-proxy-agent-hub-mtls` (same as hub/unary) at `PROXY_AGENT_HUB_MTLS_DIR=/etc/sap-proxy-agent-hub-client/mtls` with `ca.crt`, `client_svr_server.crt`, `client_svr_server.key`. |
| Network | [`allow-migration-function-to-sap-proxy-agent-hub.yaml`](../../../k8s/network-policies/allow-migration-function-to-sap-proxy-agent-hub.yaml) allows `app: migration-function` → hub `:50051`. |

**Alternative:** PEM env vars `PROXY_AGENT_HUB_TRUSTED_CA_PEM`, `PROXY_AGENT_HUB_CLIENT_CERT_PEM`, `PROXY_AGENT_HUB_CLIENT_KEY_PEM`, or `SAP_PROXY_AGENT_GRPC_CONFIG` → JSON file path.

If hub is reached only via external LB/DNS, set `PROXY_AGENT_SERVER_HOST` / `PROXY_AGENT_SERVER_PORT` in `datasyncher-secrets` and keep the mount or PEMs.

## ReportProgress

Proto: [`service.proto`](../../../backend/sap_proxy_agent/proto/service.proto) (`DatasynchorProgress.ReportProgress`).

Hub must set `X-Agent-UID` from mTLS CN when relaying (same as gateway).

## Local smoke (ODATA hub + platform client)

**One command (no full ODATA agent):**

```powershell
.\syncdatesy-adapter\backend\sap_proxy_agent\scripts\local_proxy_agent_smoke.ps1
```

Starts the ODATA hub if `:50051` is free, runs `local_mock_proxy_agent.py`, calls `TestSapConnection`, then stops processes it started. Use `-SkipHubStart` if the hub is already up; `-LeaveRunning` to keep hub/mock running after PASS.

Manual steps:

1. **Hub** — from `odata-agent/server`: `python server.py` (listens on `:50051`, mTLS under `server/mtls/`).
2. **Mock agent** (or real `client/agent.py` on Python 3.9 + `client/requirements.txt`):
   - `python backend/sap_proxy_agent/scripts/local_mock_proxy_agent.py` — `LOCAL_AGENT_CONFIG` → ODATA `client/config.json` (`agent_id` = `proxy_agent` / `cust_id`, e.g. `C013`). Set `PYTHONPATH=backend;backend/migration-function`.
3. **Connection test** — `python backend/sap_proxy_agent/scripts/local_proxy_agent_connection_test.py` (platform `client_svr_*` from `grpc_files/config.json`).

Expected: `OK: { check_dns, check_tcp, ... }` when the agent stream is registered; `Agent for customer … is not connected` if step 2 is missing or `cust_id` mismatches.
