# Worker node pools (right-sized)

One GKE node pool per plan tier. **Node machine size matches plan CPU/RAM** (not 2× oversized).

| Pool name           | Machine type    | Plans                    | Pod memory (req / limit) |
|---------------------|-----------------|--------------------------|--------------------------|
| worker-standard     | e2-standard-2   | Free, Standard, Discounted | 4Gi / 6Gi              |
| worker-premium      | e2-standard-4   | Premium                  | 10Gi / 12Gi              |
| worker-enterprise   | e2-standard-8   | Enterprise               | 22Gi / 24Gi              |

`celery-worker-control` runs on **control-pool** (orchestration only). Job pods use the worker pools above.

### App pool (web tier)

| Pool | Machine | Autoscale | Label | Workloads |
|------|---------|-----------|-------|-----------|
| app-pool-std2 | e2-standard-2 (8 GB) | 2–6 | `pool=app` | django, fastapi, frontend, migration-function, beats |

Resize: `bash k8s/node-pools/apply-app-pool-resize.sh`

### Control pool (Celery orchestration)

| Pool | Machine | Autoscale | Label | Workloads |
|------|---------|-----------|-------|-----------|
| control-pool | e2-standard-2 (8 GB) | 1–2 | `pool=control` | celery-worker-control, celery-worker-control-reconcile |

Create: `bash k8s/node-pools/apply-control-pool.sh`

Each pool must have:
- Label: `pool={pool-name}`
- Taint: `pool={pool-name}:NoSchedule` (premium + enterprise only; worker-standard has no taint)
- Autoscaling: **min-nodes=0** (scale to zero when idle)

## DB + YAML

Apply SQL (updates `worker_node_pool_config` + `plan_resource_config`):

```bash
kubectl exec -i -n default deployment/celery-worker-control -- python - <<'PY'
import os
from sqlalchemy import create_engine, text
from pathlib import Path
sql = Path("/dev/stdin").read_text()  # or pipe from file
# Prefer: cat k8s/node-pools/rightsize-worker-pools.sql | kubectl exec -i ...
PY
```

Or pipe the SQL file:

```bash
cat k8s/node-pools/rightsize-worker-pools.sql | kubectl exec -i -n default deployment/celery-worker-control -- python -c "
import os, sys
from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'])
with e.begin() as c:
    c.execute(text(sys.stdin.read()))
print('OK')
"
```

## GKE pool recreation (machine type cannot be changed in-place)

Run `apply-worker-pool-rightsize.sh` after control is on control-pool.

```bash
# --max-nodes is per zone on a regional cluster (3 zones => ~3x total).
# Sized for ~100 accounts / CELERY_CLUSTER_MAX_JOB_WORKER_PODS=200.

# worker-standard (GKE pool name: worker-pool)
gcloud container node-pools create worker-pool \
  --cluster=datasyncher-cluster --region=us-central1 \
  --project=datasyncher-kubernetes \
  --machine-type=e2-standard-2 \
  --enable-autoscaling --min-nodes=0 --max-nodes=40 \
  --node-labels=pool=worker-standard \
  --disk-size=100

# worker-premium
gcloud container node-pools create worker-premium \
  --cluster=datasyncher-cluster --region=us-central1 \
  --project=datasyncher-kubernetes \
  --machine-type=e2-standard-4 \
  --enable-autoscaling --min-nodes=0 --max-nodes=20 \
  --node-labels=pool=worker-premium \
  --node-taints=pool=worker-premium:NoSchedule \
  --disk-size=100

# worker-enterprise
gcloud container node-pools create worker-enterprise \
  --cluster=datasyncher-cluster --region=us-central1 \
  --project=datasyncher-kubernetes \
  --machine-type=e2-standard-8 \
  --enable-autoscaling --min-nodes=0 --max-nodes=20 \
  --node-labels=pool=worker-enterprise \
  --node-taints=pool=worker-enterprise:NoSchedule \
  --disk-size=100
```


Delete the old pool **after** workloads are off the node and the replacement pool exists.
