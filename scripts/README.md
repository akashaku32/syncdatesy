# Scripts

Operational scripts for the **syncdatesy** workspace. Each subfolder groups scripts by purpose so you can find and run the right tool quickly.

## Layout

```
scripts/
  rollout/     Deploy local code to the batch GKE cluster (build + restart pods)
```

## Prerequisites

Before running anything here:

1. **gcloud** logged in with access to project `datasyncher-batch`
2. **kubectl** context set to the batch cluster:
   ```powershell
   kubectl config current-context
   # expected: gke_datasyncher-batch_us-central1_datasyncher-batch-cluster
   ```
3. Local repos checked out on the branch you want to deploy (usually `core/batch_technique`)

## Folders

| Folder | Purpose | Documentation |
|--------|---------|---------------|
| [rollout/](rollout/README.md) | Build local images and roll out services to GKE | Per-service commands like `rollout_django`, `rollout_fastapi` |

## Quick start

Deploy one service after local changes (from repo root):

```powershell
cd <syncdatesy>\scripts\rollout
.\rollout_django.cmd
```

See [rollout/README.md](rollout/README.md) for all services, flags, and safety notes.

## Related paths

- Low-level rollout logic: `syncdatesy-adapter/k8s/batch-cluster/rollout-*-batch.ps1`
- K8s manifests: `syncdatesy-adapter/k8s/deployments/`
- Detailed Cloud Build steps: `syncdatesy-adapter/k8s/batch-cluster/ROLLOUT-BATCH-CLOUDBUILD.md`
