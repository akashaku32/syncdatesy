<#
.SYNOPSIS
  Build local frontend code and roll it out to the batch GKE cluster.

.EXAMPLE
  .\rollout_frontend.ps1

.EXAMPLE
  .\rollout_frontend.ps1 -NoBuild
#>
[CmdletBinding()]
param(
  [switch] $NoBuild
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

if (-not $NoBuild) {
  Build-FrontendBatch
}

Invoke-BatchRolloutScript -ScriptName "rollout-frontend-batch.ps1"
