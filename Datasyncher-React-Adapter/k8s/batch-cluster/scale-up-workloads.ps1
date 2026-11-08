<#
.SYNOPSIS
  Scale batch-cluster app workloads back up (default namespace).

.DESCRIPTION
  After scaling down for cost savings: restores KEDA celery min replicas, sets Deployments
  and StatefulSets to 1 replica. Optionally points all app containers at :batch images in GCR.

  Requires: kubectl context for datasyncher-batch-cluster, valid gcloud auth
  (run: gcloud auth login && gcloud auth application-default login if needed).

.EXAMPLE
  .\scale-up-workloads.ps1

.EXAMPLE
  .\scale-up-workloads.ps1 -UseBatchImages
#>
[CmdletBinding()]
param(
    [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
    [string] $Namespace = "default",
    [string] $Project = "datasyncher-batch",
    [string] $Region = "us-central1",
    [string] $Cluster = "datasyncher-batch-cluster",
    [switch] $UseBatchImages,
    [string] $Registry = "gcr.io/datasyncher-batch",
    [string] $Tag = "batch"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

# worker-standard autoscales 0→N when celery-worker-job-* pods are scheduled (per-job migrations).
# Do not pin min nodes here; use -ScaleWorkerPool only if the autoscaler is stuck.

Write-Step "Patch KEDA ScaledObject celery-worker-scaler → minReplicaCount 1 (allows celery-worker to stay scaled)"
$patchFile = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($patchFile, '{"spec":{"minReplicaCount":1}}')
    kubectl --context=$Context patch scaledobject celery-worker-scaler -n $Namespace --type=merge --patch-file $patchFile
}
finally {
    Remove-Item -Force $patchFile -ErrorAction SilentlyContinue
}

Write-Step "Scale deployments → 1 replica"
$deploys = @(
    "django",
    "fastapi",
    "frontend",
    "migration-function",
    "celery-worker",
    "celery-worker-control",
    "celery-worker-control-reconcile",
    "celery-beat-migration",
    "celery-worker-django"
)
foreach ($d in $deploys) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    kubectl --context=$Context scale "deployment/$d" -n $Namespace --replicas=1 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    skipped $d (not found)" -ForegroundColor DarkYellow
    }
    $ErrorActionPreference = $prev
}

Write-Step "Scale statefulsets → 1 replica"
kubectl --context=$Context scale statefulset/celery-beat-django -n $Namespace --replicas=1
kubectl --context=$Context scale statefulset/apscheduler -n $Namespace --replicas=1

if ($UseBatchImages) {
    Write-Step "Set container images → ${Registry}/*:${Tag}"
    kubectl --context=$Context set image deployment/django "django=${Registry}/datasyncher-django-app:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/fastapi "fastapi=${Registry}/datasyncher-fast-api:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/celery-worker-control "celery=${Registry}/datasyncher-fast-api:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/celery-worker-control-reconcile "celery=${Registry}/datasyncher-fast-api:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/celery-worker "celery=${Registry}/datasyncher-fast-api:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/celery-beat-migration "beat=${Registry}/datasyncher-fast-api:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/celery-worker-django "celery-worker-django=${Registry}/datasyncher-django-app:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/migration-function "migration-function=${Registry}/datasyncher-migration-function:${Tag}" -n $Namespace
    kubectl --context=$Context set image deployment/frontend "frontend=${Registry}/datasyncher-frontend:${Tag}" -n $Namespace
    kubectl --context=$Context set image statefulset/celery-beat-django "beat=${Registry}/datasyncher-django-app:${Tag}" -n $Namespace
    kubectl --context=$Context set image statefulset/apscheduler "apscheduler=${Registry}/datasyncher-django-app:${Tag}" -n $Namespace
}

Write-Step "Rollout status (timeout 10m)"
$rollouts = @(
    "deployment/django",
    "deployment/fastapi",
    "deployment/celery-worker-control",
    "deployment/celery-worker-control-reconcile",
    "deployment/celery-beat-migration",
    "deployment/celery-worker",
    "deployment/celery-worker-django",
    "deployment/migration-function",
    "deployment/frontend",
    "statefulset/celery-beat-django",
    "statefulset/apscheduler"
)
foreach ($r in $rollouts) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    kubectl --context=$Context rollout status $r -n $Namespace --timeout=10m 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    skipped rollout $r (not found or timeout)" -ForegroundColor DarkYellow
    }
    $ErrorActionPreference = $prev
}

Write-Host "`nDone. kubectl get pods -n $Namespace --context=$Context" -ForegroundColor Green
