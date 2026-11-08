<#
.SYNOPSIS
  Build local FastAPI code and roll it out to the batch GKE cluster.

.EXAMPLE
  .\rollout_fastapi.ps1

.EXAMPLE
  .\rollout_fastapi.ps1 -NoBuild

.EXAMPLE
  .\rollout_fastapi.ps1 -Force
#>
[CmdletBinding()]
param(
  [switch] $NoBuild,
  [switch] $Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$args = @{
  Build = -not $NoBuild
}
if ($Force) { $args.Force = $true }

Invoke-BatchRolloutScript -ScriptName "rollout-fastapi-batch.ps1" -Arguments $args
