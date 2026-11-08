<#
.SYNOPSIS
  makemigrations → build/push :batch images → migrate DB on batch cluster only → rollout workloads on batch cluster only.

.DESCRIPTION
  Safe for datasyncher-cluster (QA): every kubectl call uses --context for datasyncher-batch-cluster.
  Does not apply ingress, static IPs, or ClusterIssuer changes — only images + migrate Job + rollouts.

  Prerequisites: Docker, gcloud auth (gcloud auth login), gcloud auth configure-docker gcr.io,
  kubectl with batch context available.

.EXAMPLE
  .\scripts\deploy-to-batch-cluster.ps1

.EXAMPLE
  Skip DB migrate Job (e.g. schema already applied): .\scripts\deploy-to-batch-cluster.ps1 -SkipMigrateJob
#>
[CmdletBinding()]
param(
    [switch] $SkipMigrateJob,
    [switch] $RelaxDjangoRollout
)

$ErrorActionPreference = "Stop"
$BatchContext = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster"
$BatchGcpProject = "datasyncher-batch"
$ScriptRoot = $PSScriptRoot
$AdapterRoot = Split-Path $ScriptRoot -Parent
$PythonExe = Join-Path $AdapterRoot ".venv\Scripts\python.exe"
$MigrateScript = Join-Path $AdapterRoot "k8s\batch-cluster\run-db-migrate-batch.ps1"

Write-Host "`n=== [1/4] Django makemigrations (local, no DB) ===" -ForegroundColor Cyan
Push-Location $AdapterRoot
try {
    if (Test-Path $PythonExe) {
        & $PythonExe manage.py makemigrations --noinput
        if ($LASTEXITCODE -ne 0) { throw "makemigrations failed (exit $LASTEXITCODE)" }
    }
    else {
        Write-Host "Skipping makemigrations (no .venv at $PythonExe). Ensure migration files are committed before deploy." -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}

Write-Host "`n=== [2/4] Docker build + push gcr.io/.../*:batch (no kubectl) ===" -ForegroundColor Cyan
$buildArgs = @{
    Tag                = "batch"
    Project            = $BatchGcpProject
    IncludeFrontend    = $true
    FrontendViteMode   = "batch"
    SkipRollout        = $true
}
& (Join-Path $ScriptRoot "build-push-k8s.ps1") @buildArgs
if ($LASTEXITCODE -ne 0) { throw "build-push-k8s (build/push) failed (exit $LASTEXITCODE)" }

if (-not $SkipMigrateJob) {
    Write-Host "`n=== [3/4] Batch cluster: Job runs python manage.py migrate --noinput inside Django :batch image ===" -ForegroundColor Cyan
    & $MigrateScript -Context $BatchContext
    if ($LASTEXITCODE -ne 0) { throw "run-db-migrate-batch.ps1 failed (exit $LASTEXITCODE)" }
}
else {
    Write-Host "`n=== [3/4] Skipped (-SkipMigrateJob) ===" -ForegroundColor Yellow
}

Write-Host "`n=== [4/4] Batch cluster only: kubectl set image + rollout status ===" -ForegroundColor Cyan
$rollArgs = @{
    Tag             = "batch"
    Project         = $BatchGcpProject
    SkipBuild       = $true
    SkipPush        = $true
    IncludeFrontend = $true
    KubectlContext  = $BatchContext
}
if ($RelaxDjangoRollout) {
    $rollArgs['RelaxDjangoRollout'] = $true
}
& (Join-Path $ScriptRoot "build-push-k8s.ps1") @rollArgs
if ($LASTEXITCODE -ne 0) { throw "build-push-k8s (rollout) failed (exit $LASTEXITCODE)" }

Write-Host "`nDone. QA cluster / datasyncher-cluster was not targeted by kubectl (only context=$BatchContext)." -ForegroundColor Green
