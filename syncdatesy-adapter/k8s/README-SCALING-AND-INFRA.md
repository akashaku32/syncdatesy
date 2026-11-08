# Celery worker scaling, live updates, and node (VM) sizing

> **Current architecture (KEDA job-based scaling, staggered fan-out, diagrams):**  
> See **[README-CELERY-MIGRATION-SCALING.md](./README-CELERY-MIGRATION-SCALING.md)** — that is the canonical doc for how pods, jobs, and table children are assigned.  
> Sections below about scaling on the `migration` queue and `celery:keda:active` list describe the **legacy** trigger set and may not match `k8s/keda/celery-scaler.yaml` in this branch.

This document describes how **`celery-worker`** pod autoscaling works in this repo, how to change settings **while the system is running**, and how that differs from **GKE worker node (VM) sizing**.

Primary manifests:

- `k8s/keda/celery-scaler.yaml` — KEDA `ScaledObject` (replica count)
- `k8s/deployments/celery-worker.yaml` — Deployment (container image, **CPU/RAM**, Celery flags)

---

## 1. Who controls how many pods run?

The **`celery-worker-scaler`** `ScaledObject` drives the **`celery-worker`** Deployment replica count via KEDA and an underlying Horizontal Pod Autoscaler (HPA).

The Deployment file may show `spec.replicas: 0`; **KEDA still sets the live desired replicas** when the `ScaledObject` is applied and healthy. Treat **`minReplicaCount` / `maxReplicaCount`** in the `ScaledObject` as the operational bounds.

Current defaults (see `celery-scaler.yaml`; adjust if your file differs):

| Setting | Typical value | Role |
|--------|-----------------|------|
| `minReplicaCount` | `1` | Floor: at least this many worker pods. |
| `maxReplicaCount` | `12` | Ceiling: never more than this many worker pods. |
| `pollingInterval` | `5` | Seconds between KEDA metric evaluations. |
| `cooldownPeriod` | `600` | Scale-related cooldown (see KEDA docs; pairs with HPA behavior). |

**Per-pod Celery concurrency** is set on the Deployment (`--concurrency=3`). Rough upper bound for **concurrent task executions** is approximately:

`maxReplicaCount × worker concurrency` → e.g. `12 × 3 = 36` slots (tasks still share DB, Redis, and connector capacity).

---

## 2. What metrics drive scaling?

KEDA uses **several triggers** and takes the **maximum** across them (documented in comments in `celery-scaler.yaml`). Whichever trigger asks for the **most** replicas tends to win, then the count is clamped to **`min`…`max`**.

1. **Redis list `migration`** — Celery broker queue for migration tasks.  
2. **Redis list `celery:keda:active`** — Sentinel list: one entry per in-flight task (except `check_job_status`), so workers are not scaled to zero while work is running even if the queue is briefly empty.  
3. **Memory utilization %** — Relative to pod **requests** (requires `resources.requests` on the pod).  
4. **CPU utilization %** — Same.

### `listLength` (Redis triggers)

`listLength` is **not** “tasks per pod” and **not** tied 1:1 to Celery `--concurrency`. It is a **KEDA scaling target**: roughly, how much **Redis list backlog per replica** the autoscaler aims for before adding another pod. **Lower** `listLength` → **more** pods for the same list length. **Higher** → **fewer** pods.

Current value in repo: **`"3"`** on both `migration` and `celery:keda:active`.

### Stale `celery:keda:active` entries

If workers are **SIGKILL**’d or otherwise die without `task_postrun`, IDs can remain on `celery:keda:active`. That can inflate KEDA metrics and keep **too many** pods. The scaler comments note deleting the Redis key as a break-glass fix after incidents. **`celery inspect active`** is a better signal for *actually running* tasks than raw `LLEN` when you suspect staleness.

---

## 3. Updating scaling settings “in between” (no full outage)

You normally apply changes with:

```bash
kubectl apply -f k8s/keda/celery-scaler.yaml
```

### `minReplicaCount` / `maxReplicaCount`

| Change | Effect on existing pods |
|--------|-------------------------|
| **Raise `maxReplicaCount`** | Only raises the ceiling. **No pods removed.** |
| **Lower `maxReplicaCount`** below **current** replicas | Cluster **scales in**: extra pods are **terminated** (graceful drain within `terminationGracePeriodSeconds` and your `preStop`). |
| **Raise `minReplicaCount`** above current replicas | **Adds** pods to satisfy the new floor. |
| **Lower `minReplicaCount`** | Does not by itself kill pods; allows the scaler to scale **down** further when metrics allow. |

So you **can** edit min/max **while workloads run**; you are not required to “take Celery down.” **Lowering max below current count** is an intentional scale-in and **will** end some pods.

### `listLength`, `pollingInterval`, CPU/memory trigger `value`, HPA `behavior`

These adjust **how fast** and **how aggressively** replicas are computed. Applying them **does not restart pods by itself**; replicas may change over the next evaluation windows. Large drops in desired replicas still respect **scale-down stabilization** (e.g. `stabilizationWindowSeconds: 600` in this file).

### KEDA CPU/memory trigger thresholds vs pod `resources`

- Editing **only** the `ScaledObject` thresholds (`75` → `80`, etc.) updates HPA rules; **pods are not recreated** for that change alone.  
- Editing **`resources.requests` / `limits`** on the Deployment **does** roll pods (new ReplicaSet), because container resources are immutable on a running pod.

---

## 4. Pod CPU and memory (worker “size” per container)

Defined under `celery-worker` → `containers[0].resources` in `celery-worker.yaml` (current example):

- **Requests:** `cpu: 1000m`, `memory: 3Gi` — used for **scheduling** and for **utilization %** in the memory/CPU triggers.  
- **Limits:** `cpu: 4000m`, `memory: 8Gi` — hard cap per container; OOMKill happens if usage exceeds **memory limit**.

Apply after edit:

```bash
kubectl apply -f k8s/deployments/celery-worker.yaml
```

Expect a **rolling restart** of worker pods as the Deployment updates.

---

## 5. Worker node (VM) size on GKE — different from pod scaling

**Pods do not define VM size.** Worker **nodes** are GKE **node pool** settings (machine type, disk, labels such as `pool: worker`, taints, autoscaling min/max **nodes**).

### Can you “resize the VM” while pods run?

- GKE does **not** offer a simple “increase RAM on this exact same node object” for standard node pools. You typically **add a new node pool** with the desired machine type (or recreate a pool), then **cordon and drain** old nodes so workloads reschedule.  
- Pods on **unchanged** nodes keep running until those nodes are drained or replaced.  
- Pods on **drained** nodes are evicted and **rescheduled** elsewhere (use PodDisruptionBudgets and careful draining for sensitive workloads).

### Relationship to pod `requests`

Every pod must fit on a node: the **sum of CPU/memory requests** for all pods on a node must be ≤ node **allocatable** resources (after system daemons). If pods request too much per node, you see **`Pending`** (often “Insufficient cpu/memory”). Fix by **larger machine type**, **more nodes**, or **smaller pod requests**.

---

## 6. Useful commands

```bash
# Worker pods and restarts (OOM often shows as OOMKilled in last state)
kubectl get pods -n default -l app=celery-worker -o wide

# Deployment desired/ready (KEDA may control desired)
kubectl get deploy celery-worker -n default

# KEDA object and HPA name
kubectl get scaledobject -n default
kubectl get hpa -n default

# Live Celery logs (one pod behind the Deployment service)
kubectl logs -n default deploy/celery-worker -c celery -f --tail=100

# Active tasks across workers (requires working broker from exec pod)
kubectl exec -n default deploy/celery-worker -c celery -- \
  celery -A celery_worker.celery_app inspect active --timeout=15
```

**Redis address** in `celery-scaler.yaml` (`metadata.address`) must match your cluster’s Redis Service or IP for the **namespace/environment** you apply to.

### Cleaning stale `celery:keda:active`

The list is **not** a history of completed jobs: each ID should be removed in `task_postrun`. If workers die without cleanup, **`LLEN`** stays high and KEDA keeps **max replicas**.

1. **Automatic (recommended):** `check_job_status` prunes entries that are **not** tied to current DB in-progress `task_id` rows and whose Celery `AsyncResult` state is already **SUCCESS / FAILURE / REVOKED** (see `celery_worker.py` after the finalizer section). Deploy a worker image containing that logic.

2. **Manual — maintenance window:** If `migration` queue is empty and `celery inspect active` shows no real work, you can clear the key (KEDA may scale down quickly):

   ```bash
   kubectl exec -n default deploy/celery-worker -c celery -- python -c "
   import os, redis
   u = os.environ.get('CELERY_BROKER_URL') or os.environ['REDIS_URL']
   r = redis.from_url(u, decode_responses=True)
   r.delete('celery:keda:active')
   print('deleted celery:keda:active')
   "
   ```

   Only do this when you accept a short window where the active-list trigger reads **0**; do **not** delete mid-flight on a busy system without coordination.

---

## 7. Quick decision table

| Goal | Where to change |
|------|------------------|
| More/fewer **pods** for same queue | `listLength`, `maxReplicaCount`, Redis triggers, HPA `behavior` in `celery-scaler.yaml` |
| Hard cap on pod count | `maxReplicaCount` |
| Always-on workers | `minReplicaCount` > 0 |
| More **parallel tasks per pod** | `--concurrency` on Deployment (and ensure CPU/memory limits support it) |
| More **RAM/CPU per task** | Deployment `resources` |
| Bigger **machines** or more **nodes** | GKE **node pool** (not these YAML files unless you add Terraform / cluster config here) |

---

## 8. Related application code

- `backend/fast-api/celery_worker.py` — `CELERY_KEDA_ACTIVE_LIST_KEY`, `task_prerun` / `task_postrun`, queue name `migration`, task routes.

For production changes, apply manifests to the correct cluster and namespace, watch `kubectl get pods` / `hpa` during rollout, and confirm Redis and Postgres capacity alongside worker scale.
