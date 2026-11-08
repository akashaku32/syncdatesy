# Batch cluster rollout (Cloud Build)

Use this to rebuild `:batch` images in **datasyncher-batch** and restart the matching GKE deployments.

- **Cluster context:** `gke_datasyncher-batch_us-central1_datasyncher-batch-cluster`
- **Namespace:** `default`
- **GCP project:** `datasyncher-batch`
- **UI:** https://gcp.datasyncher.com

Cloud Build **only pushes the image**. Pods keep the old digest until you `set image` + `rollout restart`.

Run these from **PowerShell**. Set once:

```powershell
$CTX  = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster"
$NS   = "default"
$PROJ = "datasyncher-batch"

$ADAPTER  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ROOT     = (Resolve-Path (Join-Path $ADAPTER "..")).Path
$FRONTEND = Join-Path $ROOT "frontend\data-syncher-ui"
```

Optional: skip the active-migration guard by using the helper scripts with `-Force` (see end of this doc).

---

## Step 0 — Confirm kubectl context

```powershell
kubectl config current-context
kubectl --context=$CTX get ns
```

Expected context: `gke_datasyncher-batch_us-central1_datasyncher-batch-cluster`.

---

## Step 1 — Frontend (UI)

**Image:** `gcr.io/datasyncher-batch/datasyncher-frontend:batch`  
**When:** Scheduling canvas, node outline, pipeline UI.

### 1a. Cloud Build

```powershell
gcloud builds submit $FRONTEND `
  --config="$FRONTEND\cloudbuild-batch.yaml" `
  --project=$PROJ
```

Wait until status is **SUCCESS**.

### 1b. Roll out

```powershell
kubectl --context=$CTX set image deployment/frontend frontend=gcr.io/datasyncher-batch/datasyncher-frontend:batch -n $NS
kubectl --context=$CTX rollout restart deployment/frontend -n $NS
kubectl --context=$CTX rollout status deployment/frontend -n $NS --timeout=5m
```

### 1c. Verify

```powershell
kubectl --context=$CTX get pods -l app=frontend -n $NS
```

Hard-refresh the browser (Ctrl+Shift+R) on gcp.datasyncher.com.

---

## Step 2 — Migration-function (connector HTTP / Salesforce→Databricks)

**Image:** `gcr.io/datasyncher-batch/datasyncher-migration-function:batch`  
**When:** Salesforce DATE mapping, volume auto-create, connector listing HTTP fallback.

### 2a. Cloud Build

```powershell
gcloud builds submit "$ADAPTER\backend" `
  --config="$ADAPTER\backend\cloudbuild\batch\migration-function.yaml" `
  --project=$PROJ
```

### 2b. Roll out

```powershell
kubectl --context=$CTX set image deployment/migration-function migration-function=gcr.io/datasyncher-batch/datasyncher-migration-function:batch -n $NS
kubectl --context=$CTX rollout restart deployment/migration-function -n $NS
kubectl --context=$CTX rollout status deployment/migration-function -n $NS --timeout=5m
```

### 2c. Verify

```powershell
kubectl --context=$CTX get pods -l app=migration-function -n $NS
```

---

## Step 3 — FastAPI + Celery control (orchestration / listing workers)

**Image:** `gcr.io/datasyncher-batch/datasyncher-fast-api:batch`  
**When:** Celery orchestration, listing sanitize, job-worker image, REQUEUE / finalizer.

This image is used by:

- `fastapi`
- `celery-worker-control`
- `celery-worker-control-reconcile`
- `celery-beat-migration`
- per-job flow workers (`CELERY_JOB_WORKER_IMAGE`)

### 3a. Cloud Build

```powershell
gcloud builds submit "$ADAPTER\backend" `
  --config="$ADAPTER\backend\cloudbuild\batch\fastapi.yaml" `
  --project=$PROJ
```

### 3b. Roll out

```powershell
$IMG = "gcr.io/datasyncher-batch/datasyncher-fast-api:batch"

kubectl --context=$CTX set image deployment/fastapi fastapi=$IMG -n $NS
kubectl --context=$CTX set image deployment/celery-worker-control celery=$IMG -n $NS
kubectl --context=$CTX set image deployment/celery-worker-control-reconcile celery=$IMG -n $NS
kubectl --context=$CTX set image deployment/celery-beat-migration beat=$IMG -n $NS

kubectl --context=$CTX set env deployment/celery-worker-control -n $NS "CELERY_JOB_WORKER_IMAGE=$IMG"
kubectl --context=$CTX set env deployment/celery-worker-control-reconcile -n $NS "CELERY_JOB_WORKER_IMAGE=$IMG"

kubectl --context=$CTX rollout restart deployment/fastapi -n $NS
kubectl --context=$CTX rollout restart deployment/celery-worker-control -n $NS
kubectl --context=$CTX rollout restart deployment/celery-worker-control-reconcile -n $NS
kubectl --context=$CTX rollout restart deployment/celery-beat-migration -n $NS

kubectl --context=$CTX rollout status deployment/fastapi -n $NS --timeout=5m
kubectl --context=$CTX rollout status deployment/celery-worker-control -n $NS --timeout=5m
kubectl --context=$CTX rollout status deployment/celery-worker-control-reconcile -n $NS --timeout=5m
kubectl --context=$CTX rollout status deployment/celery-beat-migration -n $NS --timeout=5m
```

### 3c. Verify

```powershell
kubectl --context=$CTX get deploy fastapi,celery-worker-control,celery-worker-control-reconcile,celery-beat-migration -n $NS
```

---

## Step 4 — Django + pipeline Celery (pipeline DAG / resume)

**Image:** `gcr.io/datasyncher-batch/datasyncher-django-app:batch`  
**When:** Pipeline executor, resume of stuck runs, Django APIs.

This image is used by:

- `django`
- `celery-worker-django`
- `celery-beat-django`
- `apscheduler`

### 4a. Cloud Build

```powershell
gcloud builds submit $ADAPTER `
  --config="$ADAPTER\deploy\docker\cloudbuild-batch-django.yaml" `
  --project=$PROJ
```

### 4b. Roll out

```powershell
$IMG = "gcr.io/datasyncher-batch/datasyncher-django-app:batch"

kubectl --context=$CTX set image deployment/django django=$IMG -n $NS
kubectl --context=$CTX set image deployment/celery-worker-django celery-worker-django=$IMG -n $NS
kubectl --context=$CTX set image statefulset/celery-beat-django beat=$IMG -n $NS
kubectl --context=$CTX set image statefulset/apscheduler apscheduler=$IMG -n $NS

kubectl --context=$CTX rollout restart deployment/django -n $NS
kubectl --context=$CTX rollout restart deployment/celery-worker-django -n $NS
kubectl --context=$CTX rollout restart statefulset/celery-beat-django -n $NS
kubectl --context=$CTX rollout restart statefulset/apscheduler -n $NS

kubectl --context=$CTX rollout status deployment/django -n $NS --timeout=5m
kubectl --context=$CTX rollout status deployment/celery-worker-django -n $NS --timeout=5m
kubectl --context=$CTX rollout status statefulset/celery-beat-django -n $NS --timeout=5m
kubectl --context=$CTX rollout status statefulset/apscheduler -n $NS --timeout=5m
```

### 4c. Optional migrate

```powershell
kubectl --context=$CTX exec deployment/django -n $NS -- python manage.py migrate --noinput
```

### 4d. Verify

```powershell
kubectl --context=$CTX get deploy django,celery-worker-django -n $NS
kubectl --context=$CTX get sts celery-beat-django,apscheduler -n $NS
```

---

## Step 5 — SAP proxy agent (hub + gateway + MF client)

**When:** `backend/sap_proxy_agent/**`, SAP proxy gRPC client in migration-function, Django progress ingest.

**Images:**

- `gcr.io/datasyncher-batch/datasyncher-migration-function:batch` (includes `sap_proxy_agent/` in image)
- `gcr.io/datasyncher-batch/datasyncher-django-app:batch` (progress API / legacy unary ORM)
- `gcr.io/datasyncher-batch/datasyncher-sap-proxy-agent-hub:batch`
- `gcr.io/datasyncher-batch/datasyncher-proxy-agent-gateway:batch`

One-shot from `$ADAPTER\k8s\batch-cluster`:

```powershell
cd $ADAPTER\k8s\batch-cluster
.\rollout-proxy-agent-batch.ps1 -Build -ApplyManifests -IncludeDjango -Force
```

First deploy (creates Deployments/Services/netpol):

```powershell
.\rollout-proxy-agent-batch.ps1 -Build -ApplyManifests -IncludeDjango -Force
```

Hub only:

```powershell
.\rollout-sap-proxy-agent-hub-batch.ps1 -Build -ApplyManifests
```

Gateway only:

```powershell
.\rollout-proxy-agent-gateway-batch.ps1 -Build -ApplyManifests
```

Verify:

```powershell
kubectl --context=$CTX get deploy migration-function,sap-proxy-agent-hub,proxy-agent-gateway,django -n $NS
kubectl --context=$CTX get pods -l app=sap-proxy-agent-hub -n $NS
kubectl --context=$CTX logs deploy/sap-proxy-agent-hub -n $NS --tail=30
```

---

## Suggested order

Safest first (UI, then connectors, then workers, then Django):

1. Frontend  
2. Migration-function  
3. **SAP proxy agent** (MF + Django + hub + gateway) when agent work changed  
4. FastAPI  
5. Django (non-proxy Django-only changes)  

Do **not** restart Django/FastAPI control while a large pipeline is mid-fanout unless you intend to resume it afterward.

---

## Helper scripts (same as above)

From `$ADAPTER\k8s\batch-cluster`:

```powershell
cd $ADAPTER\k8s\batch-cluster

.\rollout-frontend-batch.ps1
.\rollout-migration-function-batch.ps1 -Build
.\rollout-fastapi-batch.ps1 -Build -Force
.\rollout-django-batch.ps1 -Build -Force

# or all four
.\rollout-selective-batch.ps1 -Groups frontend,migration-function,fastapi,django -Build -Force
```

`-Build` runs Cloud Build. Without `-Build`, the script only restarts pods onto the existing `:batch` tag.

---

## Check the Cloud Build job

List recent builds:

```powershell
gcloud builds list --project=$PROJ --limit=10
```

Watch the latest build until it finishes:

```powershell
gcloud builds list --project=$PROJ --limit=1
# copy ID, then:
gcloud builds describe BUILD_ID --project=$PROJ
```

Stream logs for a running/finished build:

```powershell
gcloud builds log BUILD_ID --project=$PROJ --stream
```

Only running builds:

```powershell
gcloud builds list --project=$PROJ --ongoing --limit=10
```

Filter by image (example: frontend):

```powershell
gcloud builds list --project=$PROJ --filter="images:gcr.io/datasyncher-batch/datasyncher-frontend:batch" --limit=5
```

Open the console URL printed by `gcloud builds submit`, or:

```powershell
# https://console.cloud.google.com/cloud-build/builds?project=datasyncher-batch
```

Status values: `WORKING`, `SUCCESS`, `FAILURE`, `TIMEOUT`, `CANCELLED`.

---

## Check the GKE rollout after the build

```powershell
kubectl --context=$CTX rollout status deployment/frontend -n $NS
kubectl --context=$CTX rollout status deployment/migration-function -n $NS
kubectl --context=$CTX rollout status deployment/fastapi -n $NS
kubectl --context=$CTX rollout status deployment/django -n $NS
```

```powershell
kubectl --context=$CTX get deploy frontend,migration-function,fastapi,django,celery-worker-control,celery-worker-django -n $NS
kubectl --context=$CTX get pods -n $NS
```

Check a specific pod’s image:

```powershell
kubectl --context=$CTX get pods -l app=frontend -n $NS -o jsonpath="{.items[0].status.containerStatuses[0].imageID}{'\n'}"
```

---

## Check pipeline / Celery jobs (optional)

```powershell
kubectl --context=$CTX get pods -l app=celery-worker-flow -n $NS
kubectl --context=$CTX logs deploy/celery-worker-django -n $NS --since=10m
kubectl --context=$CTX logs deploy/celery-worker-control -n $NS --since=10m
```
