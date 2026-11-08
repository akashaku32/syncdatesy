<#
.SYNOPSIS
  After `docker push gcr.io/.../datasyncher-frontend:batch`, point the batch cluster at that tag and restart pods.
  (Re-tagging :batch in GCR does not restart pods by itself; this script does.)

.EXAMPLE
  .\rollout-frontend-batch.ps1
#>
[CmdletBinding()]
param(
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $Registry = "gcr.io/datasyncher-batch",
  [string] $Tag = "batch"
)

$ErrorActionPreference = "Stop"
$img = "${Registry}/datasyncher-frontend:${Tag}"
Write-Host "==> set image deployment/frontend -> $img" -ForegroundColor Cyan
kubectl --context=$Context set image deployment/frontend "frontend=$img" -n $Namespace
Write-Host "==> rollout restart deployment/frontend" -ForegroundColor Cyan
kubectl --context=$Context rollout restart deployment/frontend -n $Namespace
kubectl --context=$Context rollout status deployment/frontend -n $Namespace --timeout=5m
Write-Host "==> running pod imageID:" -ForegroundColor Green
$imageId = kubectl --context=$Context get pods -n $Namespace -l app=frontend -o "jsonpath={.items[0].status.containerStatuses[0].imageID}"
Write-Host $imageId
