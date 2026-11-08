# Per-job worker isolation

Complements [RFC-001](../docs/RFC-001-PER-JOB-WORKER-ISOLATION.md) and [README-POOL-STRUCTURE.md](./README-POOL-STRUCTURE.md).

DB-backed plan limits (`plan_resource_config`, `company_resource_override`) are loaded at orchestration time via `celery_resource_config.py` (RFC-001 §5–8). Apply migration `0060` before enabling per-job mode in production.

## Modes

| `CELERY_JOB_ISOLATION_MODE` | Behavior |
|-----------------------------|----------|
| `shared` (default) | Legacy shared `celery-worker` pool + KEDA |
| `per_job` | Ephemeral `celery-worker-job-{hash}` per migration + control plane |

## Queues

| Queue | Consumer | Tasks |
|-------|----------|-------|
| `migration-control` | `celery-worker-control` | orchestrate, finalize, call_function |
| `migration-control-reconcile` | `celery-worker-control-reconcile` | `check_job_status` only |
| `migration:job:{hash}` | `celery-worker-job-{hash}` | `call_function_child` only |
| `migration` | `celery-worker` (shared mode) | All migration tasks |

`hash = sha256(job_name)[:12]`

## Lifecycle

1. `orchestrate_migration` computes `effective_pods = max(1, min(ceil(tables/concurrency), plan_max_pods))`
2. `provision_job_workers` creates/patches Deployment
3. `ensure_job_worker_ready` waits until `readyReplicas >= 1` (120s timeout)
4. Children publish to `migration:job:{hash}`
5. `finalize_migration_orchestration` scales to 0 and deletes Deployment

## Redis keys

| Key | Purpose |
|-----|---------|
| `celery:job-worker:{hash}:meta` | JSON lifecycle metadata |
| `celery:job-worker:active` | SET of active job hashes |

## Rollback

1. Set `CELERY_JOB_ISOLATION_MODE=shared` on all workers and FastAPI
2. Restore KEDA ScaledObject on `celery-worker`
3. Delete stray deployments: `kubectl delete deploy -l datasyncher.com/managed-by=job-pool`
4. Drain per-job queues (broker visibility timeout)

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CELERY_JOB_ISOLATION_MODE` | `shared` | `shared` or `per_job` |
| `CELERY_MIGRATION_CONTROL_QUEUE` | `migration-control` | Orchestrator queue |
| `CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE` | (empty) | Reconcile queue; when set, `check_job_status` never uses orchestrate queue |
| `CELERY_WORKER_ROLE` | (empty) | `control` or `control-reconcile` |
| `CELERY_JOB_WORKER_READY_TIMEOUT` | `120` | Provision readiness gate |
| `CELERY_CLUSTER_MAX_JOB_WORKER_PODS` | `40` | Cluster-wide replica cap |
| `CELERY_PROMETHEUS_METRICS_ENABLED` | `false` | Export RFC §11 Prometheus metrics |
| `CELERY_METRICS_PORT` | `9100` | Control worker `/metrics` port |

## Metrics

Prometheus scrape targets, metric catalog, and GMP/Operator manifests: [monitoring/README-METRICS.md](./monitoring/README-METRICS.md).
