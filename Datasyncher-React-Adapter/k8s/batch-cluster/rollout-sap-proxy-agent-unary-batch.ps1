<#
.SYNOPSIS
  Build (optional), apply manifests, and roll out sap-proxy-agent-unary:batch.

.EXAMPLE
  .\rollout-sap-proxy-agent-unary-batch.ps1 -Build -ApplyManifests
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

$img = "${Registry}/datasyncher-sap-proxy-agent-unary:${Tag}"

if ($Build) {
  Write-Host "==> gcloud builds submit (sap-proxy-agent-unary :batch)" -ForegroundColor Cyan
  Push-Location $RepoRoot
  try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    gcloud builds submit --project=$Project --config=backend/cloudbuild/batch/sap-proxy-agent-unary.yaml .
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($buildExit -ne 0) { throw "Cloud Build failed (exit $buildExit)" }
  }
  finally {
    Pop-Location
  }
}

if ($ApplyManifests) {
  Write-Host "==> kubectl apply unary Deployment + Service + netpol" -ForegroundColor Cyan
  $k8s = Join-Path $RepoRoot "k8s"
  kubectl --context=$Context apply -f (Join-Path $k8s "deployments/sap-proxy-agent-unary.yaml") -n $Namespace
  kubectl --context=$Context apply -f (Join-Path $k8s "services/sap-proxy-agent-unary-svc.yaml") -n $Namespace
  kubectl --context=$Context apply -f (Join-Path $k8s "network-policies/allow-hub-to-unary.yaml") -n $Namespace
}

$dep = kubectl --context=$Context get deployment sap-proxy-agent-unary -n $Namespace -o name 2>$null
if (-not $dep) {
  throw "deployment/sap-proxy-agent-unary not found. Re-run with -ApplyManifests."
}

Write-Host "==> set image -> $img" -ForegroundColor Cyan
kubectl --context=$Context set image deployment/sap-proxy-agent-unary "sap-proxy-agent-unary=$img" -n $Namespace

Write-Host "==> rollout restart sap-proxy-agent-unary" -ForegroundColor Cyan
kubectl --context=$Context rollout restart deployment/sap-proxy-agent-unary -n $Namespace

Write-Host "==> waiting for rollout" -ForegroundColor Cyan
kubectl --context=$Context rollout status deployment/sap-proxy-agent-unary -n $Namespace --timeout=10m

Write-Host ""
Write-Host "Done. sap-proxy-agent-unary rolled out." -ForegroundColor Green
