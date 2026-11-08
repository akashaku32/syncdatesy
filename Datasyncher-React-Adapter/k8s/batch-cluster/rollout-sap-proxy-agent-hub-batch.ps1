<#
.SYNOPSIS
  Build (optional), apply manifests, and roll out sap-proxy-agent-hub:batch.

.EXAMPLE
  .\rollout-sap-proxy-agent-hub-batch.ps1 -Build -ApplyManifests
#>
[CmdletBinding()]
param(
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $Project = "datasyncher-batch",
  [string] $Registry = "gcr.io/datasyncher-batch",
  [string] $Tag = "batch",
  [switch] $Build,
  [switch] $ApplyManifests,
  [string] $RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
  $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$img = "${Registry}/datasyncher-sap-proxy-agent-hub:${Tag}"

if ($Build) {
  Write-Host "==> gcloud builds submit (sap-proxy-agent-hub :batch)" -ForegroundColor Cyan
  Push-Location (Join-Path $RepoRoot "backend")
  try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    gcloud builds submit --project=$Project --config=cloudbuild/batch/sap-proxy-agent-hub.yaml .
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($buildExit -ne 0) { throw "Cloud Build failed (exit $buildExit)" }
  }
  finally {
    Pop-Location
  }
}

if ($ApplyManifests) {
  Write-Host "==> kubectl apply hub Service + Deployment + netpol" -ForegroundColor Cyan
  $k8s = Join-Path $RepoRoot "k8s"
  kubectl --context=$Context apply -f (Join-Path $k8s "deployments/sap-proxy-agent-hub.yaml") -n $Namespace
  kubectl --context=$Context apply -f (Join-Path $k8s "services/sap-proxy-agent-hub-svc.yaml") -n $Namespace
  kubectl --context=$Context apply -f (Join-Path $k8s "network-policies/allow-hub-to-django.yaml") -n $Namespace
}

$dep = kubectl --context=$Context get deployment sap-proxy-agent-hub -n $Namespace -o name 2>$null
if (-not $dep) {
  throw "deployment/sap-proxy-agent-hub not found. Re-run with -ApplyManifests."
}

Write-Host "==> set image -> $img" -ForegroundColor Cyan
kubectl --context=$Context set image deployment/sap-proxy-agent-hub "sap-proxy-agent-hub=$img" -n $Namespace

Write-Host "==> rollout restart sap-proxy-agent-hub" -ForegroundColor Cyan
kubectl --context=$Context rollout restart deployment/sap-proxy-agent-hub -n $Namespace

Write-Host "==> waiting for rollout" -ForegroundColor Cyan
kubectl --context=$Context rollout status deployment/sap-proxy-agent-hub -n $Namespace --timeout=5m

Write-Host ""
Write-Host "Done. sap-proxy-agent-hub rolled out." -ForegroundColor Green
