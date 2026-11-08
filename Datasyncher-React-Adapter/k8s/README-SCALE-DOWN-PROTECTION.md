# Celery worker scale-down protection

Complements [README-CELERY-MIGRATION-SCALING.md](./README-CELERY-MIGRATION-SCALING.md). **Does not change** KEDA `desired_pods` / workload formulas.

## Problem addressed

When HPA scales in, Kubernetes may terminate **any** pod. This module makes the scheduler **prefer** pods with `active_tasks == 0` via `controller.kubernetes.io/pod-deletion-cost`.

## Redis keys

| Key | Purpose |
|-----|---------|
| `celery:pod:{pod}:active_tasks` | Integer count of tracked in-flight tasks on this pod |
| `celery:task-heartbeat:{task_id}` | JSON `{task_id, pod_name, updated_at}` refreshed every 60s |
| `celery:task-orphaned:{task_id}` | Stale/orphan metadata (no auto-requeue yet) |
| `celery:pod:{pod}:drain` | Set while worker is draining on SIGTERM/shutdown |

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `CELERY_SCALE_DOWN_PROTECTION_ENABLED` | `true` | Master switch |
| `CELERY_POD_DELETION_COST_ENABLED` | `true` | Patch pod annotation |
| `CELERY_POD_DELETION_COST_BUSY` | `100` | Cost when `active_tasks > 0` |
| `CELERY_POD_DELETION_COST_IDLE` | `0` | Cost when idle |
| `CELERY_POD_ACTIVE_TASKS_RECONCILE_ENABLED` | `true` | Align Redis + deletion cost with `celery inspect active/reserved` each `check_job_status` tick |
| `CELERY_POD_ACTIVE_TASKS_RECONCILE_TIMEOUT` | `10` | Inspect timeout (seconds) |
| `CELERY_TASK_HEARTBEAT_INTERVAL_SECONDS` | `60` | Heartbeat refresh |
| `CELERY_TASK_HEARTBEAT_STALE_SECONDS` | `1200` | Orphan detection threshold (20 min) |

## RBAC

`k8s/rbac/celery-worker-pod-read.yaml` — add **`patch`** on `pods` for the worker ServiceAccount.

```bash
kubectl apply -f k8s/rbac/celery-worker-pod-read.yaml
```

## Rollout (zero downtime)

1. Apply RBAC patch.
2. Build/push `datasyncher-fast-api` image with `celery_scale_down_protection.py`.
3. Rolling update `celery-worker` Deployment (surge allows new pods before old terminate).
4. Verify logs: `POD_DELETION_COST_UPDATED`, `SCALE_DOWN_TASK_START`, `SCALE_DOWN_TASK_FINISH`.
5. Optional: `kubectl get pod -l app=celery-worker -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.controller\.kubernetes\.io/pod-deletion-cost}{"\n"}{end}'`

## Behaviour by scenario

### Idle scale-down

- `active_tasks = 0` → deletion-cost `0` → kube-scheduler **prefers** removing this pod.
- KEDA still drives replica count from `celery:keda:desired-replicas` only.
- `check_job_status` runs **inspect reconciliation** so Redis `active_tasks` and deletion cost match live Celery `active` + `reserved` tasks (fixes drift where busy pods were mislabeled cost `0`).

### Busy pod scale-down

- `active_tasks > 0` → deletion-cost `100` → scheduler **avoids** this pod when other idle pods exist.
- If **all** pods are busy and HPA must remove one, a busy pod can still be chosen (limitation).

### Worker restart / rollout

- `worker_ready` resets `active_tasks` for this pod name and sets cost `0`.
- In-flight tasks on **other** pods unaffected.

### Node eviction

- Same as SIGTERM: `WORKER_DRAIN_START`, cancel consumer for this worker, existing `worker-death` + orphan requeue paths still apply.
- 12h task cannot finish in 120s grace — rely on **existing** `CELERY_OOM_ORPHAN_REQUEUE` + payload requeue (separate from this module).

### Long 12-hour migration

- While child runs: `active_tasks >= 1`, cost `100`, heartbeats every 60s.
- Protects against **preferential** scale-in when idle replicas exist.
- Does **not** extend `terminationGracePeriodSeconds`; pin `minReplicaCount=maxReplicaCount` for guaranteed capacity if needed.

## What this does NOT fix alone

| Issue | Needs |
|-------|--------|
| Finalizer stuck on wrong task id | Finalizer ctx / orphan requeue for all child ids |
| `in_progress` forever with SUCCESS child | `source_table_selection` completion + finalizer |
| Forced kill with no idle pods | Pin replicas or longer grace + requeue |
| Node scale-down with all pods busy | PDB / pinned replicas |

## Risks

- **All pods busy**: HPA still removes a pod; cost annotation only helps when idle pods exist.
- **Fork pool**: `active_tasks` is shared via Redis per pod hostname (correct).
- **RBAC missing**: patch fails gracefully; protection degrades to logs only.
- **Stale heartbeat false positive**: 20 min threshold; tune `CELERY_TASK_HEARTBEAT_STALE_SECONDS`.

## Code

- `backend/fast-api/celery_scale_down_protection.py`
- Wired in `celery_worker.py` (`task_prerun` / `task_postrun` / `worker_ready` / `worker_shutting_down` / `check_job_status`)
- Tests: `backend/fast-api/tests/test_celery_scale_down_protection.py`
