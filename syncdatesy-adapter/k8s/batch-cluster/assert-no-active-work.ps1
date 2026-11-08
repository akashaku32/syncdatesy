<# 
.SYNOPSIS
  Guard: detect active pipeline/migration work and optionally block rollouts.

.DESCRIPTION
  Queries the batch cluster DB for:
    - PipelineRun.status == 'running'
    - MigrationRecord.status in_progress / pending / queued / running
    - recent Log rows with status == 'I' (in progress) within a configurable window

  Default behavior:
    - For groups 'django' and 'fastapi': exits non-zero if any active work is found.
    - For groups 'frontend' and 'migration-function': prints a warning but does not block.

.EXAMPLE
  .\assert-no-active-work.ps1 -Group django

.EXAMPLE
  .\assert-no-active-work.ps1 -Group django -Force
#>
[CmdletBinding()]
param(
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $DjangoDeploymentName = "django",
  [string] $Group = "django",
  [int] $LogWindowMinutes = 30,
  [switch] $Force
)

$ErrorActionPreference = "Stop"

function Write-Step([string] $msg) {
  Write-Host "`n==> $msg" -ForegroundColor Cyan
}

Write-Step "Active-work guard: group=$Group (Force=$Force, LogWindowMinutes=$LogWindowMinutes)"

if ($null -eq $Group) {
  $groupLower = ""
} else {
  $groupLower = $Group.ToString().ToLowerInvariant()
}
$blockOnActive = @("django", "fastapi") -contains $groupLower

$python = @"
from datetime import timedelta
import sys
from django.utils import timezone

from apps.elttool.models import PipelineRun, MigrationRecord, Log

log_window_minutes = int($LogWindowMinutes)
cutoff = timezone.now() - timedelta(minutes=log_window_minutes)

active_pipelines = PipelineRun.objects.filter(status='running').order_by('-id')[:50]

active_mrs = MigrationRecord.objects.filter(
    status__in=['in_progress', 'pending', 'queued', 'running']
).order_by('-id')[:100]

active_logs = Log.objects.filter(
    status='I',
    time__gte=cutoff,
).order_by('-time')[:50]

def fmt_dt(v):
    try:
        return v.isoformat()
    except Exception:
        return str(v)

print('ACTIVE_PIPELINES_COUNT', active_pipelines.count())
for r in active_pipelines:
    print('PIPELINE_RUN', r.id, 'pipeline_id=', getattr(r,'pipeline_id',None), 'started_at=', fmt_dt(getattr(r,'started_at',None)))

print('ACTIVE_MIGRATIONRECORDS_COUNT', active_mrs.count())
for m in active_mrs:
    print('MIGRATION_RECORD', m.id, 'session=', m.migration_session_id, 'table=', m.table_name, 'status=', m.status, 'task_id=', m.task_id, 'job=', (m.job_name or '')[:80])

print('ACTIVE_LOGS_COUNT', active_logs.count())
for l in active_logs:
    print('LOG_IN_PROGRESS', l.log_id, 'conn_id=', getattr(l,'connection_id',None), 'session=', l.session, 'time=', fmt_dt(l.time), 'job=', (l.job_name or '')[:80] if hasattr(l,'job_name') else '')

has_active = active_pipelines.exists() or active_mrs.exists() or active_logs.exists()
sys.exit(1 if has_active else 0)
"@

try {
  # Django prints INFO lines to stderr; do not treat those as PowerShell errors.
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $scriptResult = $python | kubectl --context=$Context exec "deployment/$DjangoDeploymentName" -n $Namespace -i -- python manage.py shell 2>&1
  $exitCode = $LASTEXITCODE
  $ErrorActionPreference = $prevEap

  # We rely on python exit code:
  #  - 0 => no active work
  #  - 1 => active work found
  #  - else => real error
  Write-Host ($scriptResult | Out-String)

  # Filter out benign Django startup noise when deciding success.
  $hasPythonActiveExit = ($exitCode -eq 1)
  $kubectlFailedHard = ($exitCode -gt 1)

  if ($kubectlFailedHard) {
    Write-Host "ERROR: Guard kubectl/python failed with exit $exitCode" -ForegroundColor Red
    exit $exitCode
  }

  if ($hasPythonActiveExit) {
    if ($Force -or -not $blockOnActive) {
      Write-Host "WARN: Active work detected, but proceeding because Force=true or group is non-blocking." -ForegroundColor Yellow
      exit 0
    }

    Write-Host "ERROR: Active work detected. Blocking rollout for group='$Group'." -ForegroundColor Red
    exit 1
  }

  Write-Host "OK: No active work detected for guard group '$Group'." -ForegroundColor Green
  exit 0
}
catch {
  Write-Host "ERROR: Guard failed unexpectedly: $($_.Exception.Message)" -ForegroundColor Red
  throw
}

