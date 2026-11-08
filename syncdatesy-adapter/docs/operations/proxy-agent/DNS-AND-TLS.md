# Proxy agent DNS and TLS (batch)

Batch uses **`gcp.datasyncher.com`** for the **UI/API (HTTPS 443)** — not for agent gRPC loads.

| Host | IP (batch) | Port | Role |
|------|------------|------|------|
| **`gcp.datasyncher.com`** | `34.16.119.133` | **443** | Web app, Django API (generate config in UI) |
| **`agent-api.gcp.datasyncher.com`** | `34.16.119.133` | **443** | HTTPS progress fallback (`progress_api_url`) |
| **`agent-hub.gcp.datasyncher.com`** (recommended) | **`34.42.60.198`** | **50051** / **50052** | SAP proxy **gRPC hub** (`server_host` in `config.json`) |

Do **not** set `PROXY_AGENT_SERVER_HOST` or `server_host` to `gcp.datasyncher.com` or `qa.datasyncher.com` — those hosts do **not** expose **TCP 50051** (QA → `173.255.114.5`, GCP UI → ingress only).

Set Django env **`PROXY_AGENT_SERVER_HOST`** to the **hub** hostname (or hub LB IP until DNS exists). Regenerate agent `config.json` after changing it.

## Hub DNS (gRPC `server_host`)

| Field | Value |
|-------|--------|
| Type | `A` |
| Name | `agent-hub.gcp` |
| Value | `34.42.60.198` (`kubectl get svc sap-proxy-agent-hub-svc -o wide`) |
| TTL | 600 or default |

Creates **`agent-hub.gcp.datasyncher.com`** → hub LoadBalancer. Verify:

```powershell
Test-NetConnection agent-hub.gcp.datasyncher.com -Port 50051
```

## Why DNS is required (HTTPS path)

Agents call a **hostname** from `config.json`:

`https://agent-api.gcp.datasyncher.com/v1/migration-progress`

Without a public A record:

1. Agents cannot resolve the host.
2. cert-manager cannot complete HTTP-01 for `agent-api-tls`.
3. Public HTTPS + mTLS at the ingress never becomes healthy.

In-cluster gateway pods can still be healthy; only the **public edge** is blocked.

## Record to add (GoDaddy)

`datasyncher.com` nameservers: `ns11/ns12.domaincontrol.com` (GoDaddy).

| Field | Value |
|-------|--------|
| Type | `A` |
| Name | `agent-api.gcp` |
| Value | `34.16.119.133` (same as `gcp.datasyncher.com`) |
| TTL | 600 or default |

Creates: `agent-api.gcp.datasyncher.com` → batch ingress LB.

## Verify

```powershell
nslookup agent-api.gcp.datasyncher.com 8.8.8.8
```

Expect `34.16.119.133`. Then on batch:

```powershell
kubectl --context=gke_datasyncher-batch_us-central1_datasyncher-batch-cluster \
  get certificate agent-api-tls
kubectl --context=gke_datasyncher-batch_us-central1_datasyncher-batch-cluster \
  get ingress agent-api-ingress
```

`certificate` should become `READY=True` within ~5–15 minutes after DNS propagates.

## Why not only `gcp.datasyncher.com`?

mTLS on the shared UI host would require client certs for browsers or complex path-scoped auth that is easy to misconfigure. Dedicated host is the preferred isolation model — see [proxy-agent-progress security](../../../../docs/security/proxy-agent-progress.md).

## Related manifests

- Ingress: `k8s/batch-cluster/ingress/agent-api-ingress.yaml`
- Env host: `PROXY_AGENT_PUBLIC_API_HOST=agent-api.gcp.datasyncher.com`
