<#
.SYNOPSIS
  Emergency: manually resize worker-standard when GKE autoscaling is stuck.

.DESCRIPTION
  Normal operation: worker-standard stays at 0 nodes until celery-worker-control
  provisions celery-worker-job-* deployments for a migration; GKE then autoscales
  the pool to schedule those pods.

  Use this script only when jobs are Pending with no nodes and autoscaler has not
  added capacity after several minutes (see: kubectl get pods -l app=celery-worker-job).

.EXAMPLE
  .\ensure-worker-standard-node.ps1

.EXAMPLE
  .\ensure-worker-standard-node.ps1 -NumNodes 2
#>
[CmdletBinding()]
param(
    [string] $Project = "datasyncher-batch",
    [string] $Region = "us-central1",
    [string] $Cluster = "datasyncher-batch-cluster",
    [int] $NumNodes = 1
)

$ErrorActionPreference = "Stop"

Write-Host "`n==> Emergency resize worker-standard to $NumNodes node(s)" -ForegroundColor Cyan
Write-Host "    (Skip in normal operation — pool should autoscale when job workers are provisioned.)" -ForegroundColor DarkYellow
gcloud container clusters resize $Cluster `
    --node-pool=worker-standard `
    --num-nodes=$NumNodes `
    --region=$Region `
    --project=$Project `
    --quiet

Write-Host "`nDone. Verify:" -ForegroundColor Green
Write-Host "  gcloud container node-pools describe worker-standard --cluster=$Cluster --region=$Region --project=$Project"
Write-Host "  kubectl get nodes -l pool=worker-standard"
