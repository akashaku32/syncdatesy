# Scale checklist: ~100 accounts (no redesign)

This runbook matches the config and code defaults for handling about 100
customer accounts on the existing architecture (non-blocking orchestration,
per-job worker pods, plan caps).

## What changed

| Area | Change |
|---|---|
| Orchestration | `PIPELINE_NONBLOCKING_ORCHESTRATION=true` (default) |
| Django pipeline workers | `celery-worker-django` replicas=2, 4Gi memory, pipeline queue only |
| Ops workers | New `celery-worker-django-ops` (ops/default queues) |
| Metadata locks | `select_for_update` instead of in-process thread locks |
| Cluster pod caps | 200 total; standard 100, premium 50, enterprise 50 |
| Provision rate | 60 pods/minute cluster-wide (6 per company) |
| Schedule jitter | `PIPELINE_SCHEDULE_JITTER_SECONDS=300` (0–5 min) |
| Fleet reuse | **On** (`PIPELINE_FLOW_FLEET_REUSE_ENABLED=true`) — one fleet per flow |

## Deploy

```bash
kubectl apply -f k8s/deployments/celery-worker-django.yaml
kubectl apply -f k8s/deployments/celery-worker-django-ops.yaml
kubectl apply -f k8s/deployments/celery-worker-control.yaml
kubectl apply -f k8s/deployments/celery-worker-control-reconcile.yaml
```

Raise GKE node-pool autoscaler maximums (per zone on a regional cluster) so
Pending pods do not pile up. See `k8s/node-pools/README-NODE-POOLS.md` and
`apply-worker-pool-rightsize.sh` (standard max 40, premium/enterprise max 20).

Update live secrets to match `k8s/batch-cluster/secrets/secrets.example.yaml`
for `PIPELINE_NONBLOCKING_ORCHESTRATION` and `PIPELINE_SCHEDULE_JITTER_SECONDS`.

## Database and Redis headroom

More worker pods mean more Postgres and Redis connections.

### Postgres

Budget roughly:

```
connections ≈ (django_replicas × orchestrator_concurrency)
            + (ops_replicas × ops_concurrency)
            + (job_worker_pods × CELERY_WORKER_CONCURRENCY)
            + control/reconcile/beat/API
```

With non-blocking orchestration at 2×8 and ~100 job pods at concurrency 3:

```
≈ 16 + 2 + 300 + ~20 ≈ 340 connections
```

Check Cloud SQL / Postgres `max_connections` is well above that (leave headroom
for admin and spikes). Prefer **PgBouncer in transaction mode** in front of
Postgres when approaching the limit. Keep Django `CONN_MAX_AGE` modest so
idle workers do not pin connections forever.

### Redis

- Confirm Memorystore / Redis `maxmemory` and connection limits.
- Broker visibility for Django orchestrator is 1 hour (short polls). Control
  workers that run long table loads keep a multi-day visibility timeout.
- Consider a dedicated Redis DB index (or instance) for the Celery broker if
  cache eviction pressure appears under load.

## Load test (100 accounts)

1. Create 100 test companies (mostly standard plan) with a small published
   pipeline (1–2 tables per node).
2. Trigger them together (API Run Now or force scheduler ticks). Scheduled
   starts should spread over ~5 minutes via jitter.
3. Watch:
   - `celery-worker-django` CPU / queue depth for `pipeline`
   - Pending `celery-worker-job-*` pods (node capacity)
   - Provision deferrals (`company_pod_cap`, rate limit) in control logs
   - Postgres active connections
   - Per-run queue wait (`waiting_for_capacity`, `waiting_since`)
4. Tune caps if needed:
   - Raise pool caps / node max only after Pending pods appear
   - Raise `CELERY_PROVISION_RATE_PER_MINUTE` if creates are deferred at start
   - Confirm Batch 2 of an in-flight flow does **not** defer with `company_job_cap`

## Fleet reuse (default on)

`PIPELINE_FLOW_FLEET_REUSE_ENABLED=true` so all nodes in a pipeline run share
one Redis/K8s fleet (`pipeline_run_<id>`). That matches plan flow slots: one
accepted flow holds one job slot until teardown.

Verify after deploy:

- Replica count never shrinks mid-run
- Template PATCH does not restart pods still running another node
- Parallel nodes sharing one queue do not starve each other
- Lease TTL covers runs longer than 24 hours
- Mid-run batches do not fail with "already at its concurrent job limit"
