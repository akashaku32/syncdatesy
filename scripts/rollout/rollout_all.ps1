<#
.SYNOPSIS
  Build and roll out all batch services in the safest order.

.EXAMPLE
  .\rollout_all.ps1

.EXAMPLE
  .\rollout_all.ps1 -NoBuild -Force
#>
[CmdletBinding()]
param(
  [switch] $NoBuild,
  [switch] $Migrate,
  [switch] $Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$buildArgs = @{}
if ($NoBuild) { $buildArgs.NoBuild = $true }

$workerArgs = @{}
if ($NoBuild) { $workerArgs.NoBuild = $true }
if ($Force) { $workerArgs.Force = $true }

$djangoArgs = @{}
if ($NoBuild) { $djangoArgs.NoBuild = $true }
if ($Force) { $djangoArgs.Force = $true }
if ($Migrate) { $djangoArgs.Migrate = $true }

& (Join-Path $PSScriptRoot "rollout_frontend.ps1") @buildArgs
& (Join-Path $PSScriptRoot "rollout_migration_function.ps1") @buildArgs
& (Join-Path $PSScriptRoot "rollout_fastapi.ps1") @workerArgs
& (Join-Path $PSScriptRoot "rollout_django.ps1") @djangoArgs

Write-Host ""
Write-Host "Done. All services rolled out." -ForegroundColor Green
