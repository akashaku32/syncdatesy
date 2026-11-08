# Shared paths and helpers for workspace rollout scripts.
$Script:WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Script:AdapterRoot = Join-Path $WorkspaceRoot "syncdatesy-adapter"
$Script:FrontendRoot = Join-Path $WorkspaceRoot "frontend\data-syncher-ui"
$Script:BatchClusterDir = Join-Path $AdapterRoot "k8s\batch-cluster"

function Invoke-BatchRolloutScript {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [string] $ScriptName,
    [hashtable] $Arguments = @{}
  )

  $target = Join-Path $BatchClusterDir $ScriptName
  if (-not (Test-Path $target)) {
    throw "Rollout script not found: $target"
  }

  & $target @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "$ScriptName failed (exit $LASTEXITCODE)"
  }
}

function Build-FrontendBatch {
  [CmdletBinding()]
  param(
    [string] $Project = "datasyncher-batch"
  )

  if (-not (Test-Path $FrontendRoot)) {
    throw "Frontend root not found: $FrontendRoot"
  }

  Write-Host "==> gcloud builds submit (frontend :batch)" -ForegroundColor Cyan
  Push-Location $FrontendRoot
  try {
    gcloud builds submit --config=cloudbuild-batch.yaml --project=$Project .
    if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed (exit $LASTEXITCODE)" }
  }
  finally {
    Pop-Location
  }
}
