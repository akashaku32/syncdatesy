<#
.SYNOPSIS
  Build local Django code and roll it out to the batch GKE cluster.

.EXAMPLE
  .\rollout_django.ps1

.EXAMPLE
  .\rollout_django.ps1 -NoBuild

.EXAMPLE
  .\rollout_django.ps1 -Migrate -Force
#>
[CmdletBinding()]
param(
  [switch] $NoBuild,
  [switch] $Migrate,
  [switch] $Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$args = @{
  Build = -not $NoBuild
}
if ($Migrate) { $args.Migrate = $true }
if ($Force) { $args.Force = $true }

Invoke-BatchRolloutScript -ScriptName "rollout-django-batch.ps1" -Arguments $args
