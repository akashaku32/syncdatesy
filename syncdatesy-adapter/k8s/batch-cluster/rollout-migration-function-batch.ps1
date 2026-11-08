<#
.SYNOPSIS
  Build (optional) and roll out datasyncher-migration-function:batch to migration-function only.

.DESCRIPTION
  Rollout group:
    - deployment/migration-function

  This rollout is typically safe because connector table work runs in per-job celery-worker pods
  (celery-worker-job-*). migration-function is mainly used for auth/listing and HTTP fallback.

.EXAMPLE
  .\rollout-migration-function-batch.ps1 -Build
#>
[CmdletBinding()]
param(
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $Project = "datasyncher-batch",
  [string] $Registry = "gcr.io/datasyncher-batch",
  [string] $Tag = "batch",
  [switch] $Build,
  [string] $RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
  $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$img = "${Registry}/datasyncher-migration-function:${Tag}"

if ($Build) {
  Write-Host "==> gcloud builds submit (migration-function :batch)" -ForegroundColor Cyan
  Push-Location $RepoRoot
  try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    gcloud builds submit --project=$Project --config=backend/cloudbuild/batch/migration-function.yaml backend/
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($buildExit -ne 0) { throw "Cloud Build failed (exit $buildExit)" }
  }
  finally {
    Pop-Location
  }
}

Write-Host "==> set image -> $img" -ForegroundColor Cyan
kubectl --context=$Context set image deployment/migration-function "migration-function=$img" -n $Namespace

Write-Host "==> rollout restart migration-function" -ForegroundColor Cyan
kubectl --context=$Context rollout restart deployment/migration-function -n $Namespace

Write-Host "==> waiting for rollout" -ForegroundColor Cyan
kubectl --context=$Context rollout status deployment/migration-function -n $Namespace --timeout=5m

Write-Host ""
Write-Host "Done. migration-function image rolled out." -ForegroundColor Green

