<#
.SYNOPSIS
  Build (optional), apply manifests, and roll out proxy-agent-gateway:batch.

.EXAMPLE
  .\rollout-proxy-agent-gateway-batch.ps1 -Build -ApplyManifests
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

$img = "${Registry}/datasyncher-proxy-agent-gateway:${Tag}"

if ($Build) {
  Write-Host "==> gcloud builds submit (proxy-agent-gateway :batch)" -ForegroundColor Cyan
  Push-Location (Join-Path $RepoRoot "backend")
  try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    gcloud builds submit --project=$Project --config=cloudbuild/batch/proxy-agent-gateway.yaml .
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($buildExit -ne 0) { throw "Cloud Build failed (exit $buildExit)" }
  }
  finally {
    Pop-Location
  }
}

if ($ApplyManifests) {
  Write-Host "==> kubectl apply gateway Service + Deployment" -ForegroundColor Cyan
  $k8s = Join-Path $RepoRoot "k8s"
  kubectl --context=$Context apply -f (Join-Path $k8s "deployments/proxy-agent-gateway.yaml") -n $Namespace
  kubectl --context=$Context apply -f (Join-Path $k8s "services/proxy-agent-gateway-svc.yaml") -n $Namespace
}

$dep = kubectl --context=$Context get deployment proxy-agent-gateway -n $Namespace -o name 2>$null
if (-not $dep) {
  throw "deployment/proxy-agent-gateway not found. Re-run with -ApplyManifests."
}

Write-Host "==> set image -> $img" -ForegroundColor Cyan
kubectl --context=$Context set image deployment/proxy-agent-gateway "proxy-agent-gateway=$img" -n $Namespace

Write-Host "==> rollout restart proxy-agent-gateway" -ForegroundColor Cyan
kubectl --context=$Context rollout restart deployment/proxy-agent-gateway -n $Namespace

Write-Host "==> waiting for rollout" -ForegroundColor Cyan
kubectl --context=$Context rollout status deployment/proxy-agent-gateway -n $Namespace --timeout=5m

Write-Host ""
Write-Host "Done. proxy-agent-gateway rolled out." -ForegroundColor Green
