<#
.SYNOPSIS
  Build (optional) and roll out datasyncher-fast-api:batch to fastapi + migration-control consumers.

.DESCRIPTION
  Rollout group (image=datasyncher-fast-api:batch):
    - deployment/fastapi
    - deployment/celery-worker-control
    - deployment/celery-worker-control-reconcile
    - deployment/celery-beat-migration

  Before restarting, runs assert-no-active-work guard for group 'fastapi' by default.
  Use -Force to bypass the guard (not recommended for active migrations).

.EXAMPLE
  .\rollout-fastapi-batch.ps1 -Build

.EXAMPLE
  .\rollout-fastapi-batch.ps1 -Force
#>
[CmdletBinding()]
param(
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $Project = "datasyncher-batch",
  [string] $Registry = "gcr.io/datasyncher-batch",
  [string] $Tag = "batch",
  [switch] $Build,
  [switch] $Force,
  [string] $RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
  $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$guardScript = Join-Path $here "assert-no-active-work.ps1"

$img = "${Registry}/datasyncher-fast-api:${Tag}"

if ($Build) {
  Write-Host "==> gcloud builds submit (fastapi :batch)" -ForegroundColor Cyan
  Push-Location $RepoRoot
  try {
    gcloud builds submit --project=$Project --config=backend/cloudbuild/batch/fastapi.yaml backend/
    if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed (exit $LASTEXITCODE)" }
  }
  finally {
    Pop-Location
  }
}

Write-Host "==> guard: block fastapi rollout if migrations/pipelines are active" -ForegroundColor Cyan
if ($Force) {
  & $guardScript -Context $Context -Namespace $Namespace -DjangoDeploymentName "django" -Group "fastapi" -Force
} else {
  & $guardScript -Context $Context -Namespace $Namespace -DjangoDeploymentName "django" -Group "fastapi"
}

Write-Host "==> set image -> $img" -ForegroundColor Cyan
kubectl --context=$Context set image deployment/fastapi "fastapi=$img" -n $Namespace
kubectl --context=$Context set image deployment/celery-worker-control "celery=$img" -n $Namespace
kubectl --context=$Context set image deployment/celery-worker-control-reconcile "celery=$img" -n $Namespace
kubectl --context=$Context set image deployment/celery-beat-migration "beat=$img" -n $Namespace

# Ensure newly provisioned per-job celery-worker-job-* pods use the new worker image.
kubectl --context=$Context set env deployment/celery-worker-control -n $Namespace "CELERY_JOB_WORKER_IMAGE=$img"
kubectl --context=$Context set env deployment/celery-worker-control-reconcile -n $Namespace "CELERY_JOB_WORKER_IMAGE=$img"

Write-Host "==> rollout restart fastapi + migration control group" -ForegroundColor Cyan
kubectl --context=$Context rollout restart deployment/fastapi -n $Namespace
kubectl --context=$Context rollout restart deployment/celery-worker-control -n $Namespace
kubectl --context=$Context rollout restart deployment/celery-worker-control-reconcile -n $Namespace
kubectl --context=$Context rollout restart deployment/celery-beat-migration -n $Namespace

Write-Host "==> waiting for rollouts" -ForegroundColor Cyan
kubectl --context=$Context rollout status deployment/fastapi -n $Namespace --timeout=5m
kubectl --context=$Context rollout status deployment/celery-worker-control -n $Namespace --timeout=5m
kubectl --context=$Context rollout status deployment/celery-worker-control-reconcile -n $Namespace --timeout=5m
kubectl --context=$Context rollout status deployment/celery-beat-migration -n $Namespace --timeout=5m

Write-Host ""
Write-Host "Done. FastAPI image rolled out to fastapi + celery-worker-control/reconcile + celery-beat-migration." -ForegroundColor Green

