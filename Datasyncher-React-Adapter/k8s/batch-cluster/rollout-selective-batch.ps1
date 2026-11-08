<#
.SYNOPSIS
  Selective rollout orchestrator for the batch GKE cluster with migration safety guards.

.DESCRIPTION
  Rolls out only the relevant image groups:
    - frontend
    - migration-function
    - fastapi
    - django
    - proxy-agent (migration-function + django + hub + gateway)

  Group order (safest first):
    frontend → migration-function → proxy-agent → fastapi → django

  Autodetection:
    - If -Groups is provided, it is used directly.
    - Otherwise, autodetects from git diff within the Datasyncher-React-Adapter git repo
      (so frontend changes require explicit -Groups frontend).

.EXAMPLE
  .\rollout-selective-batch.ps1 -Groups django -Build -Migrate

.EXAMPLE
  .\rollout-selective-batch.ps1 -Groups frontend,fastapi -Build

.EXAMPLE
  .\rollout-selective-batch.ps1 -Build -BaseRef HEAD~1
#>
[CmdletBinding()]
param(
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $Project = "datasyncher-batch",
  [string] $Registry = "gcr.io/datasyncher-batch",
  [string] $Tag = "batch",
  [string[]] $Groups,
  [switch] $Build,
  [switch] $Migrate,
  [switch] $Force,
  [string] $BaseRef = "HEAD~1",
  [string] $RepoRoot = ""
)

$ErrorActionPreference = "Stop"

$adapterRoot = if ($RepoRoot) { $RepoRoot } else { (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$k8sBatch = Join-Path $adapterRoot "k8s\batch-cluster"

$frontendRoot = Join-Path (Split-Path $adapterRoot -Parent) "frontend\data-syncher-ui"

function NormalizePath([string] $s) {
  return ($s -replace '\\','/').ToLowerInvariant()
}

function DetectGroupsFromGitDiff {
  # This only works inside the adapter git repo; frontend folder is not tracked here.
  Push-Location $adapterRoot
  try {
    $cmd = "git diff --name-only $BaseRef..HEAD"
    $files = & cmd /c $cmd 2>$null
    if (-not $files) { return @() }
    $groupsDetected = New-Object System.Collections.Generic.HashSet[string]

    foreach ($f in $files) {
      $p = NormalizePath $f
      if ($p -like "apps/*" -or $p -like "datasyncher/*" -or $p -like "deploy/docker/*") {
        $groupsDetected.Add("django") | Out-Null
      }
      if ($p -like "backend/fast-api/*") {
        $groupsDetected.Add("fastapi") | Out-Null
      }
      if ($p -like "backend/migration-function/*") {
        $groupsDetected.Add("migration-function") | Out-Null
      }
      if ($p -like "backend/sap_proxy_agent/*") {
        $groupsDetected.Add("proxy-agent") | Out-Null
      }
      if ($p -like "apps/elttool/services/proxy_agent*" -or $p -like "apps/elttool/v1/proxy_agent*") {
        $groupsDetected.Add("proxy-agent") | Out-Null
        $groupsDetected.Add("django") | Out-Null
      }
    }

    return @($groupsDetected)
  }
  finally {
    Pop-Location
  }
}

if (-not $Groups -or $Groups.Count -eq 0) {
  $detected = DetectGroupsFromGitDiff
  if (-not $detected -or $detected.Count -eq 0) {
    Write-Host "No groups detected from git diff inside Datasyncher-React-Adapter repo." -ForegroundColor Yellow
    Write-Host "For frontend-only changes, pass -Groups frontend explicitly." -ForegroundColor Yellow
    throw "Aborting selective rollout: groups not specified and autodetection returned empty."
  }
  $Groups = $detected
}

$groupSet = New-Object System.Collections.Generic.HashSet[string]
foreach ($g in $Groups) { $groupSet.Add((NormalizePath $g)) | Out-Null }

$order = @("frontend", "migration-function", "proxy-agent", "fastapi", "django")
Write-Host "==> selective rollout groups: $(@($groupSet) -join ', ')" -ForegroundColor Cyan
Write-Host "==> rollout order: $($order -join ' -> ')" -ForegroundColor Cyan

function BuildFrontendIfNeeded {
  if (-not $Build) { return }
  if (-not (Test-Path $frontendRoot)) { throw "frontend root not found at $frontendRoot" }

  Write-Host "==> gcloud builds submit (frontend :batch)" -ForegroundColor Cyan
  Push-Location $frontendRoot
  try {
    # cloudbuild config + Dockerfile live under frontend/
    gcloud builds submit --config=cloudbuild-batch.yaml --project=$Project .
  }
  finally { Pop-Location }
}

foreach ($g in $order) {
  if (-not $groupSet.Contains($g)) { continue }

  if ($g -eq "frontend") {
    BuildFrontendIfNeeded
    Write-Host "==> rollout group: frontend" -ForegroundColor Cyan
    & (Join-Path $k8sBatch "rollout-frontend-batch.ps1") `
      -Context $Context -Namespace $Namespace -Registry $Registry -Tag $Tag
  }

  if ($g -eq "migration-function") {
    Write-Host "==> rollout group: migration-function" -ForegroundColor Cyan
    & (Join-Path $k8sBatch "rollout-migration-function-batch.ps1") `
      -Context $Context -Namespace $Namespace -Project $Project -Registry $Registry -Tag $Tag -Build:$Build
  }

  if ($g -eq "proxy-agent") {
    Write-Host "==> rollout group: proxy-agent (MF + django + hub + gateway)" -ForegroundColor Cyan
    & (Join-Path $k8sBatch "rollout-proxy-agent-batch.ps1") `
      -Context $Context -Namespace $Namespace -Project $Project -Registry $Registry -Tag $Tag `
      -Build:$Build -ApplyManifests -IncludeDjango -Force:$Force
  }

  if ($g -eq "fastapi") {
    Write-Host "==> rollout group: fastapi" -ForegroundColor Cyan
    & (Join-Path $k8sBatch "rollout-fastapi-batch.ps1") `
      -Context $Context -Namespace $Namespace -Project $Project -Registry $Registry -Tag $Tag -Build:$Build -Force:$Force
  }

  if ($g -eq "django") {
    Write-Host "==> rollout group: django" -ForegroundColor Cyan
    & (Join-Path $k8sBatch "rollout-django-batch.ps1") `
      -Context $Context -Namespace $Namespace -Project $Project -Registry $Registry -Tag $Tag -Build:$Build -Migrate:$Migrate -Force:$Force
  }
}

Write-Host ""
Write-Host "Done. Selective rollout finished." -ForegroundColor Green

