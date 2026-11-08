# Prometheus endpoints — Datasyncher QA / Kubernetes

Complete reference for **Prometheus scrape targets**, **Django ops APIs** that consume metrics, and **Celery monitoring endpoints** on the QA cluster.

| | |
|---|---|
| **QA base URL** | `https://qa-kubernetes.datasyncher.com` |
| **Cluster** | `datasyncher-cluster` (`us-central1`, project `datasyncher-kubernetes`) |
| **Metrics implementation** | [`backend/fast-api/celery_job_metrics.py`](../backend/fast-api/celery_job_metrics.py) |
| **Short ops cheat sheet** | [`k8s/monitoring/README-METRICS.md`](../k8s/monitoring/README-METRICS.md) |

---

## Table of contents

1. [Endpoint summary](#endpoint-summary)
2. [Authentication](#authentication)
3. [Raw Prometheus scrape endpoints](#raw-prometheus-scrape-endpoints)
4. [Django ops REST endpoints](#django-ops-rest-endpoints)
5. [Celery connectivity endpoint](#celery-connectivity-endpoint)
6. [WebSocket ops feed](#websocket-ops-feed)
7. [Metric series catalog](#metric-series-catalog)
8. [Configuration](#configuration)
9. [Kubernetes scrape manifests](#kubernetes-scrape-manifests)
10. [Alert rules](#alert-rules)
11. [QA verification checklist](#qa-verification-checklist)
12. [PromQL examples](#promql-examples)

---

## Endpoint summary

| # | Endpoint | Method | Auth | Exposes Prometheus? | Purpose |
|---|----------|--------|------|---------------------|---------|
| 1 | `/api/metrics` | GET | None | **Yes** (text/plain) | FastAPI public metrics scrape |
| 2 | `http://celery-worker-control-metrics:9100/metrics` | GET | None (in-cluster) | **Yes** | Primary scrape target — cluster gauges from Redis |
| 3 | `http://fastapi-svc:9090/metrics` | GET | None (in-cluster) | **Yes** | FastAPI in-cluster scrape |
| 4 | `/api/v1/admin/ops/overview/` | GET | Superuser JWT | **Reads** metrics | Ops dashboard + `prometheus_gauges` JSON |
| 5 | `/api/v1/admin/ops/workers/` | GET | Superuser JWT | No (Celery inspect) | Worker pods, queues, Celery state |
| 6 | `/api/v1/admin/ops/jobs/` | GET | Superuser JWT | No | Active migration jobs list |
| 7 | `/api/v1/admin/ops/jobs/<job_name>/` | GET | Superuser JWT | No | Single job detail + child tasks |
| 8 | `/api/v1/test-celery-connection/` | GET | Any user JWT | No | Django → Redis → Celery health check |
| 9 | `/api/v1/internal/admin-ops-push/` | POST | None (internal) | **Reads** metrics | Push ops snapshot to WebSocket |
| 10 | `wss://…/ws/admin_ops/?token=<JWT>` | WS | Superuser JWT | **Reads** metrics | Live ops dashboard stream |

**Not scraped:** `celery-worker` (KEDA), `celery-worker-job-*` (ephemeral), `django`, `migration-function`, `celery-worker-django`.

---

## Authentication

### QA platform admin (for ops endpoints)

| Field | Value |
|-------|--------|
| Email | `qa-platform-admin@datasyncher.com` |
| Password | `QaK8sAdmin!2026` |
| Requirements | `is_superuser=true` |

Create or reset with:

```powershell
.\scripts\create-qa-platform-admin.ps1
```

### Obtain JWT

```http
POST /api/v1/authentication/
Content-Type: application/json

{
  "username": "qa-platform-admin@datasyncher.com",
  "password": "QaK8sAdmin!2026"
}
```

**Response (200):**

```json
{
  "message": "Login successful",
  "user": { "id": 8, "role": null, "account_id": null },
  "access_token": "<JWT>",
  "refresh_token": "<JWT>",
  "redirect_to": "destination-home"
}
```

Use on all protected calls:

```http
Authorization: Bearer <access_token>
```

> **Note:** Raw `/metrics` endpoints do **not** require auth. Ops endpoints require a **platform superuser** token.

---

## Raw Prometheus scrape endpoints

### 1. FastAPI — public (via ingress)

| | |
|---|---|
| **URL** | `https://qa-kubernetes.datasyncher.com/api/metrics` |
| **Method** | `GET` |
| **Auth** | None |
| **Content-Type** | `text/plain; version=1.0.0` |
| **Pod** | `deployment/fastapi` |
| **Port** | `9090` (named `metrics` in manifest) |

**When disabled** (`CELERY_PROMETHEUS_METRICS_ENABLED=false`):

```json
HTTP 404
{ "detail": "Prometheus metrics not enabled" }
```

**Example:**

```bash
curl -s https://qa-kubernetes.datasyncher.com/api/metrics | grep '^datasyncher_'
```

**QA verified output (idle cluster):**

```
datasyncher_cluster_worker_pods_used 0.0
datasyncher_provision_queue_depth 0.0
datasyncher_startup_orphan_sweep_deleted_total 0.0
```

---

### 2. Control worker — primary scrape target

| | |
|---|---|
| **In-cluster Service** | `http://celery-worker-control-metrics:9100/metrics` |
| **Pod localhost** | `http://127.0.0.1:9100/metrics` |
| **Method** | `GET` |
| **Auth** | None (cluster-internal only) |
| **Pod** | `deployment/celery-worker-control` |
| **Port** | `9100` (`CELERY_METRICS_PORT`) |

**Why primary:** Control worker runs `refresh_cluster_gauges()` from Redis on each `check_job_status` cycle. Cluster-wide gauges are most accurate here.

**Port-forward (local dev):**

```bash
kubectl port-forward -n default deploy/celery-worker-control 9100:9100
curl -s http://localhost:9100/metrics | grep '^datasyncher_'
```

**From django pod (same as `OPS_METRICS_URL`):**

```bash
kubectl exec -n default deploy/django -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://celery-worker-control-metrics:9100/metrics').read().decode())"
```

**Startup log confirmation:**

```bash
kubectl logs -n default deploy/celery-worker-control | grep PROMETHEUS_METRICS_SERVER_STARTED
# PROMETHEUS_METRICS_SERVER_STARTED | port=9100
```

---

### 3. FastAPI — in-cluster

| | |
|---|---|
| **URL** | `http://fastapi-svc:9090/metrics` |
| **Method** | `GET` |
| **Auth** | None (in-cluster) |
| **GMP scrape** | [`k8s/monitoring/fastapi-podmonitoring.yaml`](../k8s/monitoring/fastapi-podmonitoring.yaml) |

Same metric series as public `/api/metrics`, but separate process — gauges may lag control worker.

---

## Django ops REST endpoints

Django does **not** serve `/metrics`. It **scrapes** `OPS_METRICS_URL` and embeds selected gauges in JSON responses.

**Base path:** `https://qa-kubernetes.datasyncher.com/api/v1/`  
**Auth:** `Authorization: Bearer <superuser JWT>`

---

### 4. `GET /api/v1/admin/ops/overview/`

Platform ops summary including live Prometheus gauges.

| | |
|---|---|
| **Permission** | `IsPlatformSuperuser` |
| **Prometheus** | Scrapes `OPS_METRICS_URL`; returns `prometheus_gauges` object |

**Example:**

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://qa-kubernetes.datasyncher.com/api/v1/admin/ops/overview/ | jq .
```

**Response fields (selected):**

```json
{
  "active_sessions": 0,
  "active_jobs": 0,
  "worker_pods_used": 0,
  "worker_pods_cap": 40,
  "provision_queue_depth": 0,
  "control_queue_depth": 0,
  "celery_workers": {
    "workers_available": true,
    "total_workers": 1,
    "total_active_tasks": 0,
    "error": null
  },
  "isolation_mode": "per_job",
  "prometheus_gauges": {
    "datasyncher_cluster_worker_pods_used": 0.0,
    "datasyncher_provision_queue_depth": 0.0,
    "datasyncher_startup_orphan_sweep_deleted_total": 0.0
  }
}
```

**Query params:** none.

---

### 5. `GET /api/v1/admin/ops/workers/`

Celery worker inspect + Redis per-job worker metadata.

| | |
|---|---|
| **Permission** | `IsPlatformSuperuser` |
| **Prometheus** | Does not return raw metrics; complements overview with live Celery inspect |

**Example:**

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://qa-kubernetes.datasyncher.com/api/v1/admin/ops/workers/ | jq .
```

**Response structure:**

```json
{
  "control_worker": {
    "celery@celery-worker-control-…": {
      "active_tasks": {},
      "registered_tasks": ["…"],
      "pool": { "max-concurrency": 3 }
    }
  },
  "job_worker_hosts": {},
  "job_workers": [],
  "celery_inspect": {
    "workers_available": true,
    "total_workers": 1,
    "total_active_tasks": 0
  }
}
```

---

### 6. `GET /api/v1/admin/ops/jobs/`

List active/recent migration jobs.

| | |
|---|---|
| **Permission** | `IsPlatformSuperuser` |
| **Query params** | `company_id` (int), `plan` (string), `limit` (1–200, default 50) |

**Example:**

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://qa-kubernetes.datasyncher.com/api/v1/admin/ops/jobs/?limit=5"
```

---

### 7. `GET /api/v1/admin/ops/jobs/<job_name>/`

Single job detail with per-table status and child Celery tasks.

**Example:**

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://qa-kubernetes.datasyncher.com/api/v1/admin/ops/jobs/my-connection-job-name/"
```

**404** if job not found.

---

### 8. `POST /api/v1/internal/admin-ops-push/`

Cluster-internal hook to broadcast ops snapshot (includes `prometheus_gauges`) to WebSocket clients.

| | |
|---|---|
| **Auth** | `AllowAny` (intended for in-cluster callers only) |
| **Method** | `POST` |

**Response (200):**

```json
{ "message": "Admin ops WebSocket update pushed successfully" }
```

---

## Celery connectivity endpoint

### 9. `GET /api/v1/test-celery-connection/`

Verifies Django → Redis → Celery worker path. Useful alongside Prometheus ops checks.

| | |
|---|---|
| **Permission** | `IsAuthenticated` (any logged-in user) |
| **Query params** | `connection_name` (optional — tests specific connection migration state) |

**Example:**

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://qa-kubernetes.datasyncher.com/api/v1/test-celery-connection/ | jq .
```

**Response (selected):**

```json
{
  "worker_status": {
    "workers_available": true,
    "total_workers": 1,
    "total_active_tasks": 0
  },
  "connection_test": {
    "message": "Provide ?connection_name=X to test specific connection"
  },
  "timestamp": "2026-06-19T…",
  "redis_host": "…",
  "celery_config": { "…": "…" }
}
```

---

## WebSocket ops feed

### 10. `wss://qa-kubernetes.datasyncher.com/ws/admin_ops/`

Live ops dashboard stream. Payload built by `build_ops_snapshot()` — includes `overview.prometheus_gauges`.

| | |
|---|---|
| **Auth** | `?token=<superuser JWT>` query parameter |
| **Consumer** | [`apps/elttool/websocket/consumers/admin_ops.py`](../apps/elttool/websocket/consumers/admin_ops.py) |

**Connect:**

```
wss://qa-kubernetes.datasyncher.com/ws/admin_ops/?token=<access_token>
```

**Snapshot event shape:**

```json
{
  "event": "ops_snapshot",
  "timestamp": "2026-06-19T12:00:00+00:00",
  "overview": {
    "prometheus_gauges": { "…": "…" },
    "worker_pods_used": 0,
    "provision_queue_depth": 0
  },
  "jobs": [ "…" ],
  "workers": { "…": "…" }
}
```

---

## Metric series catalog

All custom series use prefix `datasyncher_`. Implementation: [`celery_job_metrics.py`](../backend/fast-api/celery_job_metrics.py).

### Gauges

| Metric | Labels | Description |
|--------|--------|-------------|
| `datasyncher_job_worker_replicas` | `company_id`, `plan`, `job_hash` | Active replicas per per-job Deployment |
| `datasyncher_migration_worker_memory_bytes` | `pod`, `connector` | Worker process RSS |
| `datasyncher_cluster_worker_pods_used` | — | Total active per-job worker pods |
| `datasyncher_provision_queue_depth` | — | Deferred provision queue depth |

### Counters

| Metric | Labels | Description |
|--------|--------|-------------|
| `datasyncher_job_worker_ready_timeout_total` | `plan` | Provision readiness timeouts |
| `datasyncher_job_teardown_total` | `status` | Deployment teardown (`success` / `fail`) |
| `datasyncher_chunk_reduced_total` | `connector`, `reason` | Chunk size clamp events |
| `datasyncher_oom_killed_total` | `job_hash` | OOMKilled worker pods |
| `datasyncher_provision_deferred_total` | `reason` | Deferred provisions |
| `datasyncher_startup_orphan_sweep_deleted_total` | — | Orphan Deployments deleted on startup |
| `datasyncher_concurrency_reduced_total` | `plan` | Memory-based concurrency reductions |

### Histograms

| Metric | Labels | Buckets (s) | Description |
|--------|--------|-------------|-------------|
| `datasyncher_job_provision_latency_seconds` | `plan` | 5, 15, 30, 60, 90, 120, 180, 300 | Provision → readyReplicas≥1 |

### Label cardinality rules

Use `job_hash` (12 chars), `company_id`, `plan`, `connector`, `reason`, `status`, `pod`.  
Do **not** use raw `table_name` or full `job_name` as labels.

---

## Configuration

| Variable | Default | Component | Description |
|----------|---------|-----------|-------------|
| `CELERY_PROMETHEUS_METRICS_ENABLED` | `false` | fastapi, celery-worker-control | Master enable switch |
| `CELERY_METRICS_PORT` | `9100` | celery-worker-control | Standalone `/metrics` HTTP port |
| `CELERY_CLUSTER_MAX_JOB_WORKER_PODS` | `40` | control worker | Pod cap for alerts + ops overview |
| `OPS_METRICS_URL` | `http://celery-worker-control-metrics:9100/metrics` | django | Django scrape URL for ops API |

**QA manifests** (`CELERY_PROMETHEUS_METRICS_ENABLED=true`):

- [`k8s/deployments/celery-worker-control.yaml`](../k8s/deployments/celery-worker-control.yaml)
- [`k8s/deployments/fastapi.yaml`](../k8s/deployments/fastapi.yaml)

**Django setting:** [`datasyncher/settings/base.py`](../datasyncher/settings/base.py) → `OPS_METRICS_URL`

---

## Kubernetes scrape manifests

| File | Purpose |
|------|---------|
| [`celery-control-metrics-service.yaml`](../k8s/monitoring/celery-control-metrics-service.yaml) | ClusterIP Service `celery-worker-control-metrics:9100` |
| [`celery-control-podmonitoring.yaml`](../k8s/monitoring/celery-control-podmonitoring.yaml) | GMP scrape — control worker |
| [`fastapi-podmonitoring.yaml`](../k8s/monitoring/fastapi-podmonitoring.yaml) | GMP scrape — fastapi |
| [`celery-control-servicemonitor.yaml`](../k8s/monitoring/celery-control-servicemonitor.yaml) | Prometheus Operator ServiceMonitor |
| [`prometheus-rules-job-isolation.yaml`](../k8s/monitoring/prometheus-rules-job-isolation.yaml) | Example alert rules |

**Apply (GMP):**

```bash
kubectl apply -f k8s/monitoring/celery-control-metrics-service.yaml
kubectl apply -f k8s/monitoring/celery-control-podmonitoring.yaml
kubectl apply -f k8s/monitoring/fastapi-podmonitoring.yaml
```

---

## Alert rules

From [`prometheus-rules-job-isolation.yaml`](../k8s/monitoring/prometheus-rules-job-isolation.yaml):

| Alert | Severity | Trigger |
|-------|----------|---------|
| `JobWorkerReadyTimeout` | critical | `increase(datasyncher_job_worker_ready_timeout_total[5m]) > 0` |
| `JobWorkerTeardownFailed` | warning | `increase(datasyncher_job_teardown_total{status="fail"}[5m]) > 0` |
| `ProvisionQueueBacklog` | warning | `datasyncher_provision_queue_depth > 10` for 30m |
| `ClusterWorkerPodsNearCap` | warning | `cluster_worker_pods_used / cap > 0.9` for 5m |
| `MigrationWorkerMemoryHigh` | warning | RSS > 85% of pod limit for 5m |
| `JobWorkerOOMKilled` | critical | `increase(datasyncher_oom_killed_total[5m]) > 0` |

---

## QA verification checklist

Run after deploy or metrics config change.

### Step 1 — Login

```bash
export BASE=https://qa-kubernetes.datasyncher.com
export TOKEN=$(curl -sS -X POST "$BASE/api/v1/authentication/" \
  -H "Content-Type: application/json" \
  -d '{"username":"qa-platform-admin@datasyncher.com","password":"QaK8sAdmin!2026"}' \
  | jq -r .access_token)
echo "token length: ${#TOKEN}"
```

### Step 2 — Raw metrics (no auth)

```bash
curl -sS -o /dev/null -w "%{http_code}\n" "$BASE/api/metrics"          # expect 200
curl -sS "$BASE/api/metrics" | grep -c '^datasyncher_'                  # expect >= 3
```

### Step 3 — Ops APIs (superuser JWT)

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/admin/ops/overview/"                                      # expect 200

curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/admin/ops/overview/" | jq .prometheus_gauges            # expect object

curl -sS -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/admin/ops/workers/"                                       # expect 200

curl -sS -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/admin/ops/jobs/?limit=5"                                  # expect 200

curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/test-celery-connection/" | jq .worker_status.workers_available  # expect true
```

### Step 4 — In-cluster scrape

```bash
kubectl exec -n default deploy/django -- python -c \
  "import urllib.request; b=urllib.request.urlopen('http://celery-worker-control-metrics:9100/metrics').read().decode(); print(len([l for l in b.splitlines() if l.startswith('datasyncher_')]))"
# expect >= 3
```

### QA status (last verified)

| Endpoint | HTTP | Result |
|----------|------|--------|
| `/api/metrics` | 200 | 4 `datasyncher_*` series |
| `/api/v1/admin/ops/overview/` | 200 | `prometheus_gauges` populated |
| `/api/v1/admin/ops/workers/` | 200 | `workers_available=true` |
| `/api/v1/admin/ops/jobs/` | 200 | Returns job list |
| `/api/v1/test-celery-connection/` | 200 | `workers_available=true` |
| `celery-worker-control-metrics:9100/metrics` | 200 | 4 series from django pod |

---

## PromQL examples

```promql
# Cluster pod utilization
datasyncher_cluster_worker_pods_used / 40

# Provision backlog
datasyncher_provision_queue_depth

# P95 provision latency by plan
histogram_quantile(0.95,
  sum by (plan, le) (rate(datasyncher_job_provision_latency_seconds_bucket[5m]))
)

# OOM events
increase(datasyncher_oom_killed_total[15m])

# Top memory consumers
topk(5, datasyncher_migration_worker_memory_bytes)

# Per-job replica count
datasyncher_job_worker_replicas
```

---

## Related documentation

| Document | Topic |
|----------|-------|
| [QA_KUBERNETES_API_ENDPOINTS.md](./QA_KUBERNETES_API_ENDPOINTS.md) | Full Django / FastAPI / migration-function API |
| [k8s/README.md](../k8s/README.md) | Cluster architecture and ingress routing |
| [k8s/README-JOB-ISOLATION.md](../k8s/README-JOB-ISOLATION.md) | Per-job Celery worker isolation |
| [RFC-001-PER-JOB-WORKER-ISOLATION.md](./RFC-001-PER-JOB-WORKER-ISOLATION.md) | RFC §11 monitoring specification |
| [test_celery_job_metrics.py](../backend/fast-api/tests/test_celery_job_metrics.py) | Unit tests for metric series |
