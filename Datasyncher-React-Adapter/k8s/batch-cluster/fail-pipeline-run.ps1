<#
.SYNOPSIS
  Fail a pipeline run and clear table locks so the next execution does not skip tables.

.DESCRIPTION
  Marks the pipeline run and all nodes as failed, fails active MigrationRecord rows,
  clears SourceTableSelection in_progress locks, revokes Celery tasks, and tears down
  the per-run flow worker fleet.

  Uses ``manage.py fail_pipeline_run`` when deployed; otherwise runs fail-pipeline-run.inline.py
  via Django shell (works without a new django image).

.EXAMPLE
  .\fail-pipeline-run.ps1 -PipelineRunId 3503

.EXAMPLE
  .\fail-pipeline-run.ps1 -PipelineRunId 3503 -Reason "Reset before manual re-run"

.EXAMPLE
  .\fail-pipeline-run.ps1 -PipelineRunId 3503 -DryRun
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [int] $PipelineRunId,

  [string] $Reason = "Manually failed for clean pipeline restart",
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $DjangoDeploymentName = "django",

  [switch] $DryRun,
  [switch] $NoTeardown,
  [switch] $NoRevoke
)

$ErrorActionPreference = "Stop"

function Invoke-DjangoShell([string] $python) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $output = $python | kubectl --context=$Context exec "deployment/$DjangoDeploymentName" -n $Namespace -i -- python manage.py shell 2>&1
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  $text = ($output | Out-String)
  if ($code -ne 0) {
    Write-Host $text
    throw "Django shell failed with exit $code"
  }
  return $text
}

function Invoke-ManageCommand([string[]] $manageArgs) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $output = kubectl --context=$Context exec "deployment/$DjangoDeploymentName" -n $Namespace -i -- python manage.py @manageArgs 2>&1
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  return @{
    Text = ($output | Out-String)
    Code = $code
  }
}

Write-Host "==> Failing pipeline run $PipelineRunId on $Context" -ForegroundColor Cyan

$manageArgs = @("fail_pipeline_run", "$PipelineRunId", "--reason", $Reason)
if ($DryRun) { $manageArgs += "--dry-run" }
if ($NoTeardown) { $manageArgs += "--no-teardown" }
if ($NoRevoke) { $manageArgs += "--no-revoke" }

$result = Invoke-ManageCommand $manageArgs
if ($result.Code -eq 0) {
  Write-Host $result.Text
  exit 0
}

if ($result.Text -notmatch "Unknown command: 'fail_pipeline_run'") {
  Write-Host $result.Text
  throw "fail_pipeline_run exited with code $($result.Code)"
}

Write-Host "    (management command not deployed; using inline Django shell)" -ForegroundColor DarkYellow

$inlineTemplate = Get-Content -Raw (Join-Path $PSScriptRoot "fail-pipeline-run.inline.py")
$reasonJson = ($Reason | ConvertTo-Json -Compress)
$inlinePy = $inlineTemplate `
  -replace "__RUN_ID__", "$PipelineRunId" `
  -replace "__REASON__", $reasonJson `
  -replace "__DRY_RUN__", ($(if ($DryRun) { "True" } else { "False" })) `
  -replace "__TEARDOWN__", ($(if ($NoTeardown) { "False" } else { "True" })) `
  -replace "__REVOKE__", ($(if ($NoRevoke) { "False" } else { "True" }))

$text = Invoke-DjangoShell $inlinePy
Write-Host $text
if ($text -notmatch '@@DONE=1' -and $text -notmatch '@@DRY_RUN=1') {
  throw "Inline fail did not complete successfully"
}
