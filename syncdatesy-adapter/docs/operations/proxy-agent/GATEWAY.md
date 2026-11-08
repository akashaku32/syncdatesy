# Proxy agent progress gateway (ops)

**Fallback** HTTPS path for on-prem SAP proxy agents to report per-table and job progress when gRPC `ReportProgress` is unavailable. **Primary** transport is gRPC on the existing hub (see [README-AGENT.md](./README-AGENT.md)). Django updates `migration_record`, `source_table_selection`, `data_transfer`, activity logs, and job finalization via the internal ingest; pipeline orchestration still polls DB (`poll_pipeline_run` / `_peek_migration_session`).

**Security model (mTLS, HMAC, dedicated host):**  
[`docs/security/proxy-agent-progress.md`](../../../../docs/security/proxy-agent-progress.md) (monorepo)

**DNS / TLS:** [DNS-AND-TLS.md](./DNS-AND-TLS.md)

## Architecture

| Component | Role |
|-----------|------|
| On-prem agent | Runs SAP loads; POSTs progress events |
| `proxy-agent-gateway` | Small FastAPI pod; rate limit; forwards to Django |
| Django `internal/proxy-agent/migration-progress/` | HMAC verify, tenant/job bind, ORM updates, WS push |
| gRPC hub `:50051` | **Loads only** (`StartDataLoad` / `TriggerDelta`) in v1 |
| `celery-worker-django` | Pipeline DAG poll — unchanged |

```text
Agent --mTLS HTTPS--> agent-api ingress --> proxy-agent-gateway
                                              |
                                              | X-INTERNAL-KEY + X-Agent-UID
                                              v
                                         django-svc internal ingest --> PostgreSQL + WS
```

## Agent events

`POST /v1/migration-progress` JSON:

- Required: `migration_session_id`, `job_name`, `connection_id`, `event`
- Table events also need `table_name`
- Recommended: `idempotency_key`
- Events: `table_started`, `table_progress`, `table_completed`, `table_failed`, `activity_log`, `batch_completed`

Headers:

- Client cert (ingress mTLS); CN = `agent_uid`
- `X-Agent-Timestamp` — Unix seconds (±300s)
- `X-Agent-Signature` — `HMAC-SHA256(progress_signing_secret, timestamp + "." + raw_body)` hex
- `Idempotency-Key` — optional

Correlation IDs come from gRPC `details_for_sts_update` on load accept (`migration_session_id`, `job_name`, `connection_id`, `cmp_id`, `user_id`, `log_id`).

## Deploy (batch)

```bash
# Build (context = backend/)
gcloud builds submit --project=datasyncher-batch \
  --config=backend/cloudbuild/batch/proxy-agent-gateway.yaml backend/

kubectl --context=gke_datasyncher-batch_us-central1_datasyncher-batch-cluster apply \
  -f k8s/deployments/proxy-agent-gateway.yaml \
  -f k8s/services/proxy-agent-gateway-svc.yaml \
  -f k8s/batch-cluster/ingress/agent-api-ingress.yaml
```

Secrets (batch):

| Secret | Contents |
|--------|----------|
| `proxy-agent-ca` | `ca.crt` + `ca.key` (Django issuance mount) |
| `proxy-agent-client-ca` | `ca.crt` only (ingress `auth-tls-secret`) |
| `datasyncher-secrets` | `INTERNAL_API_KEY`, `PROXY_AGENT_*` paths/hosts |

Env (via `datasyncher-secrets` / Django settings):

| Variable | Purpose |
|----------|---------|
| `INTERNAL_API_KEY` | Gateway → Django |
| `DJANGO_INTERNAL_BASE_URL` | Default `http://django-svc:8000` |
| `PROXY_AGENT_PUBLIC_API_HOST` | Host written into agent `progress_api_url` |
| `PROXY_AGENT_CA_CERT_PATH` / `PROXY_AGENT_CA_KEY_PATH` | Cert issuance (`/etc/proxy-agent-ca/...`) |
| `PROXY_AGENT_PROGRESS_TRANSPORT` | `grpc_with_https_fallback` (default), `grpc`, or `https` |

Django CA mount patch: `k8s/batch-cluster/patches/django-proxy-agent-ca.yaml`.

## Agent config (`POST /api/v1/proxy-agent/generate/`)

Fields in `config.json`:

- `progress_api_url` — e.g. `https://agent-api.gcp.datasyncher.com/v1/migration-progress`
- `progress_signing_secret` — one-time HMAC secret
- `progress_transport` — usually `grpc_with_https_fallback`
- client cert / key / bootstrap / trusted CA (existing)

## Primary — gRPC `ReportProgress`

1. Proto: [`service.proto`](../../../backend/sap_proxy_agent/proto/service.proto)
2. Agent calls on same mTLS channel as loads; **hub pod** relays to Django ingest ([HUB.md](./HUB.md)).
3. Try gRPC first when transport is `grpc` or `grpc_with_https_fallback`.

## This gateway (HTTPS fallback)

Agents POST here when gRPC progress fails or transport is `https`. Keep deployed for fallback.

## Pipeline verification

1. Deploy gateway; `GET /healthz`.
2. Generate agent config; run SAP proxy batch in a pipeline with a dependent node.
3. Agent posts `table_completed` per table → UI progress updates; job log flips I→S/F.
4. When session terminal, `poll_pipeline_run` starts the next batch node.

## Pod sizing

| Item | Value |
|------|--------|
| Deployment | `proxy-agent-gateway` |
| Pool | `app` |
| Replicas | 1–2 |
| Requests | 100m CPU / 128Mi |
| Limits | 500m CPU / 512Mi |
| Port | 8080 |
