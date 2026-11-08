# Pipeline scheduler reconcile

Scheduling for **flows (pipelines)** uses two separate persistence layers. After deploys or scheduler pod restarts, they can drift unless jobs are re-synced from the `pipeline` table.

---

## Two places schedule data lives

| Layer | Storage | Meaning |
|-------|---------|---------|
| **Pipeline config** | Django `pipeline` table (`schedule_type`, `schedule_config`, `time_frequency`, `sync_start_date`, `sync_end_date`, `status`, `schedule_paused`, `validated_graph`) | What the Scheduling UI shows; source of truth for *what should run*. |
| **Runnable timers** | APScheduler job store (`apscheduler_jobs` via `SQLAlchemyJobStore`) | What the `apscheduler` pod actually fires; job IDs are `pipeline_<id>`. |

Legacy **per-connection** auto-sync (`ScheduledJob`, `connection.time_frequency`) is separate. Batches assigned to a pipeline are scheduled only through **`pipeline_<id>`** jobs (`_sync_pipeline_scheduler` in `apps/elttool/v1/pipeline_views.py`).

---

## When jobs are registered or removed

`_sync_pipeline_scheduler(pipeline)` registers or removes `pipeline_<id>` when:

| Condition | Scheduler action |
|-----------|------------------|
| Pipeline `status=paused` or `schedule_paused=true` | Remove job |
| No nodes, no graph roots, or no `validated_graph` | Remove job |
| `schedule_type=manual` | Remove job |
| Active pipeline with valid graph and parseable schedule | `add_job` → `run_pipeline_job` |

**Automatic sync triggers today:**

- Pipeline **create** (POST)
- Pipeline **PATCH** (schedule / status changes)
- Pipeline **Validate** (POST) after a successful publish — registers the job without re-saving the schedule
- **`apscheduler` pod startup** — full reconcile (see below)
- **`manage.py reconcile_pipeline_schedulers`** — manual / ops

Django web workers do **not** run the scheduler; they call the scheduler HTTP API (`scheduler-service:5000`).

---

## Startup reconcile (after rollout)

When `apps.elttool.scheduler_service` starts:

1. APScheduler loads existing rows from `apscheduler_jobs`.
2. The HTTP API listens on port `5000`.
3. A background thread waits for `/health`, then runs **`reconcile_all_pipeline_schedulers()`** for every `pipeline` row.

**Logs to confirm success:**

```text
PIPELINE_SCHEDULER_RECONCILE_DONE | total=<N> | synced=<N> | errors=0
PIPELINE_SCHEDULER_RECONCILE_STARTUP | stats={...}
```

Per-pipeline registration may also log:

```text
Job pipeline_<id> added successfully.
```

### Environment variable

| Variable | Default | Purpose |
|----------|---------|---------|
| `PIPELINE_SCHEDULER_RECONCILE_ON_START` | `true` | Set to `false` to skip startup reconcile (emergency only). |

Set in `k8s/statefulsets/apscheduler.yaml` for the batch cluster.

### Code locations

- Reconcile logic: `apps/elttool/services/pipeline_scheduler_reconcile.py`
- Management command: `apps/elttool/management/commands/reconcile_pipeline_schedulers.py`
- Startup hook: `apps/elttool/scheduler_service.py` → `_run_startup_pipeline_reconcile`

---

## Manual reconcile (ops)

From any pod with Django + DB access (e.g. `deployment/django`):

```bash
# All pipelines
kubectl exec -n default deployment/django -- python manage.py reconcile_pipeline_schedulers

# One flow
kubectl exec -n default deployment/django -- python manage.py reconcile_pipeline_schedulers --pipeline-id 59

# Count only
kubectl exec -n default deployment/django -- python manage.py reconcile_pipeline_schedulers --dry-run
```

Non-zero exit if any pipeline failed to sync.

---

## Verify a flow is scheduled

```bash
# Scheduler service (from django pod)
kubectl exec -n default deployment/django -- python manage.py shell -c "
from apps.elttool.scheduler_client import SchedulerClient
print(SchedulerClient.get_job('pipeline_59'))
"

# Or hit apscheduler logs after rollout
kubectl logs -n default apscheduler-0 --tail=100 | grep -E 'RECONCILE|pipeline_59'
```

Expected: job present with `next_run_time` matching the pipeline cron/interval (timezone from `schedule_config.timezone`).

---

## Troubleshooting: “auto schedule did not run”

1. **Confirm the batch is on a pipeline** — connector overview may show `time_frequency=None`; schedule lives on the **flow**, not the connection.
2. **Check `PipelineRun` metadata** — scheduled runs include `'source': 'scheduler'` in `metadata`; manual runs omit it.
3. **Check scheduler job exists** — `GET /jobs/get/pipeline_<id>` or `SchedulerClient.get_job` returns 404 → drift; run reconcile.
4. **Check pipeline gates** — paused flow, schedule paused, unpublished graph, or manual schedule type → no job by design.
5. **After `apscheduler` restart** — rely on startup reconcile; if disabled, run `reconcile_pipeline_schedulers` manually.

### Example (connection 69 → Pipeline 3, `pipeline_id=59`)

- Schedule: weekdays **10:15** `Asia/Calcutta`
- Symptom: no fire at 10:15 IST; `pipeline_59` missing from scheduler after pod restart
- Fix: deploy with startup reconcile, or `manage.py reconcile_pipeline_schedulers --pipeline-id 59`

---

## Deploy (batch cluster)

Pipeline scheduler changes ship in **`datasyncher-django-app`** (same image as `django`, `celery-worker-django`, `celery-beat-django`, **`apscheduler`**).

```powershell
cd Datasyncher-React-Adapter\k8s\batch-cluster
.\rollout-django-batch.ps1 -Build
```

Rollout restarts all four consumers so dispatch, Celery, and scheduled runs stay aligned.

The django rollout **guard** may block while migrations or pipeline runs are active; wait or use documented force procedures in `k8s/README.md` if appropriate.

---

## Related docs

- [SCHEDULER_PRODUCTION_SETUP.md](./SCHEDULER_PRODUCTION_SETUP.md) — APScheduler service, connection-level jobs, event listeners
- [k8s/README.md](../../k8s/README.md) — batch rollouts and guard behavior
