<#
.SYNOPSIS
  Build local migration-function code and roll it out to the batch GKE cluster.

.EXAMPLE
  .\rollout_migration_function.ps1

.EXAMPLE
  .\rollout_migration_function.ps1 -NoBuild
#>
[CmdletBinding()]
param(
  [switch] $NoBuild
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Invoke-BatchRolloutScript -ScriptName "rollout-migration-function-batch.ps1" -Arguments @{
  Build = -not $NoBuild
}
