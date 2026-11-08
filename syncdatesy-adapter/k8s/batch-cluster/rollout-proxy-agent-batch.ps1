<#
.SYNOPSIS
  Roll out SAP proxy agent stack on batch (MF client, Django progress ingest, hub, gateway).

.DESCRIPTION
  Order: migration-function → django (progress API) → sap-proxy-agent-hub → proxy-agent-gateway

.EXAMPLE
  .\rollout-proxy-agent-batch.ps1 -Build -ApplyManifests -IncludeDjango -Force
#>
[CmdletBinding()]
param(
  [switch] $Build,
  [switch] $ApplyManifests,
  [switch] $IncludeDjango,
  [switch] $Force,
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $Project = "datasyncher-batch",
  [string] $Registry = "gcr.io/datasyncher-batch",
  [string] $Tag = "batch"
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

$common = @{
  Context   = $Context
  Namespace = $Namespace
  Project   = $Project
  Registry  = $Registry
  Tag       = $Tag
}

Write-Host "=== migration-function (sap_proxy_agent gRPC client) ===" -ForegroundColor Cyan
& (Join-Path $here "rollout-migration-function-batch.ps1") @common -Build:$Build

if ($IncludeDjango) {
  Write-Host "`n=== django (proxy progress ingest + legacy unary ORM) ===" -ForegroundColor Cyan
  & (Join-Path $here "rollout-django-batch.ps1") @common -Build:$Build -Force:$Force
}

Write-Host "`n=== sap-proxy-agent-unary (:50053 bootstrap / legacy) ===" -ForegroundColor Cyan
& (Join-Path $here "rollout-sap-proxy-agent-unary-batch.ps1") @common -Build:$Build -ApplyManifests:$ApplyManifests

Write-Host "`n=== sap-proxy-agent-hub ===" -ForegroundColor Cyan
& (Join-Path $here "rollout-sap-proxy-agent-hub-batch.ps1") @common -Build:$Build -ApplyManifests:$ApplyManifests

Write-Host "`n=== proxy-agent-gateway ===" -ForegroundColor Cyan
& (Join-Path $here "rollout-proxy-agent-gateway-batch.ps1") @common -Build:$Build -ApplyManifests:$ApplyManifests

Write-Host "`nAll proxy-agent batch rollouts finished." -ForegroundColor Green
