<#
.SYNOPSIS
  Delete prior db-migrate-batch Job (if any), apply it, wait for success, print logs.

.EXAMPLE
  .\run-db-migrate-batch.ps1
.EXAMPLE
  .\run-db-migrate-batch.ps1 -Context gke_project_region_other-cluster
#>
param(
    [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
    [string] $Namespace = "default",
    [string] $JobName = "db-migrate-batch",
    [int] $WaitSeconds = 600
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifest = Join-Path $here "jobs\db-migrate-batch.yaml"

Write-Host "kubectl context: $Context (pass-through --context)" -ForegroundColor Cyan
kubectl --context=$Context cluster-info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "kubectl cannot reach the cluster (exit $LASTEXITCODE). Run: gcloud auth login"
}

Write-Host "`nDelete existing Job (if any) - Jobs are immutable for image/command changes." -ForegroundColor Yellow
kubectl --context=$Context delete job $JobName -n $Namespace --ignore-not-found
if ($LASTEXITCODE -ne 0) { throw "kubectl delete job failed (exit $LASTEXITCODE)" }

Write-Host "`nApply $manifest" -ForegroundColor Yellow
kubectl --context=$Context apply -f $manifest
if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed (exit $LASTEXITCODE)" }

Write-Host "`nWait up to ${WaitSeconds}s for completion..." -ForegroundColor Yellow
kubectl --context=$Context wait --for=condition=complete job/${JobName} -n $Namespace --timeout="${WaitSeconds}s"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Job did not complete in time or failed. Recent pod events:" -ForegroundColor Red
    kubectl --context=$Context describe job $JobName -n $Namespace
    throw "kubectl wait failed (exit $LASTEXITCODE)"
}

Write-Host "`nLogs:" -ForegroundColor Green
kubectl --context=$Context logs job/${JobName} -n $Namespace
