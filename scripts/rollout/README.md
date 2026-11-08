# Rollout scripts

Simple one-command deploys for the **datasyncher-batch** GKE cluster. Each script builds your **local code** with Cloud Build, pushes the `:batch` image, and restarts the matching Kubernetes workloads.

**Target cluster:** `gke_datasyncher-batch_us-central1_datasyncher-batch-cluster`  
**Namespace:** `default`  
**UI:** https://gcp.datasyncher.com

## Why this folder exists

Rolling out code to the batch cluster used to mean running long `gcloud builds submit` and `kubectl` commands from memory, or digging into `k8s/batch-cluster/`. These wrappers give you a **single command per service** from your local workspace — no need to remember image names, deployment lists, or build config paths.

They delegate to the battle-tested scripts in `Datasyncher-React-Adapter/k8s/batch-cluster/` (guards, multi-deployment restarts, env updates).

## Scripts

| Script | What it deploys | Local source |
|--------|-----------------|--------------|
| `rollout_django` | django, celery-worker-django, celery-beat-django, apscheduler | `Datasyncher-React-Adapter/` (Django app) |
| `rollout_fastapi` | fastapi, celery-worker-control, celery-worker-control-reconcile, celery-beat-migration | `Datasyncher-React-Adapter/backend/fast-api/` |
| `rollout_migration_function` | migration-function | `Datasyncher-React-Adapter/backend/migration-function/` |
| `rollout_frontend` | frontend | `frontend/data-syncher-ui/` |
| `rollout_all` | All four groups, in safe order | Entire workspace |

Each script has a `.ps1` and a `.cmd` twin. Use whichever you prefer.

## How to run

```powershell
# From repo root (syncdatesy):
$RolloutDir = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "scripts\rollout"
# Or: cd <syncdatesy>\scripts\rollout

# Build local code + deploy (default)
.\rollout_django.cmd
.\rollout_fastapi.cmd
.\rollout_frontend.cmd
.\rollout_migration_function.cmd

# Deploy everything (frontend → migration-function → fastapi → django)
.\rollout_all.cmd
```

From any directory (full path to your clone):

```powershell
& "<syncdatesy>\scripts\rollout\rollout_fastapi.cmd"
```

## Flags

| Flag | Applies to | Effect |
|------|------------|--------|
| `-NoBuild` | All | Skip Cloud Build; only restart pods onto the existing `:batch` tag |
| `-Force` | django, fastapi, rollout_all | Bypass active-migration / pipeline guard |
| `-Migrate` | django, rollout_all | Run `python manage.py migrate` after django rollout |

Examples:

```powershell
.\rollout_django.cmd -Migrate          # build + deploy + run migrations
.\rollout_fastapi.cmd -NoBuild         # restart only, no new image
.\rollout_django.cmd -Force            # deploy even if pipelines are running (use carefully)
.\rollout_all.cmd -Force -Migrate      # full stack deploy with overrides
```

## Recommended order

When deploying multiple services manually, use this order (safest first):

1. **frontend** — UI changes  
2. **migration_function** — connector HTTP / listing  
3. **fastapi** — orchestration, Celery control, job workers  
4. **django** — pipeline DAG, Django APIs, scheduler  

`rollout_all` follows this order automatically.

## Safety guards

**Django** and **FastAPI** rollouts run `assert-no-active-work.ps1` before restarting pods. If migrations or pipelines are in progress, the script stops unless you pass `-Force`.

Do not force-restart Django or FastAPI during a large pipeline fanout unless you plan to resume runs afterward.

**Migration-function** and **frontend** have no guard — they are usually safe to roll anytime.

## What happens under the hood

1. **Build** (unless `-NoBuild`): `gcloud builds submit` using the batch Cloud Build config for that service  
2. **Set image**: `kubectl set image` on all deployments/statefulsets in the group  
3. **Restart**: `kubectl rollout restart` + wait for `rollout status`  
4. **Extra** (django): optional migrate; **Extra** (fastapi): updates `CELERY_JOB_WORKER_IMAGE` for new job pods  

## Verify after rollout

```powershell
$CTX = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster"
kubectl --context=$CTX get deploy django,fastapi,migration-function,frontend -n default
kubectl --context=$CTX rollout status deployment/django -n default
```

Hard-refresh the browser (Ctrl+Shift+R) after a frontend rollout.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `kubectl cannot reach the cluster` | `gcloud auth login` and fetch credentials for the batch cluster |
| Guard blocks rollout | Wait for pipelines to finish, or use `-Force` if intentional |
| Cloud Build fails | Check `gcloud builds list --project=datasyncher-batch --limit=5` |
| UI unchanged after frontend rollout | Hard refresh; confirm pod image with `kubectl get pods -l app=frontend -o wide` |

## Files in this folder

| File | Role |
|------|------|
| `_common.ps1` | Shared paths and helpers (not run directly) |
| `rollout_*.ps1` / `rollout_*.cmd` | Per-service entry points |
