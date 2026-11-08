<#
.SYNOPSIS
  Show per-job / flow-fleet Celery workers for a pipeline (flow) run and tail live logs.

.DESCRIPTION
  Looks up PipelineRun + MigrationRecord rows in Django, prints expected
  celery-worker-flow-* / celery-worker-job-* deployments, lists matching pods,
  then follows their logs live (kubectl logs -f --prefix).

.EXAMPLE
  .\watch-flow-jobs.ps1 -PipelineRunId 1148

.EXAMPLE
  .\watch-flow-jobs.ps1 -PipelineRunId 1148 -NoFollow

.EXAMPLE
  .\watch-flow-jobs.ps1 -SessionId 4170 -Tail 200

.EXAMPLE
  .\watch-flow-jobs.ps1 -ListActive

.EXAMPLE
  .\watch-flow-jobs.ps1 -PipelineId 16 -IncludeControl
#>
[CmdletBinding(DefaultParameterSetName = "ByRun")]
param(
  [Parameter(ParameterSetName = "ByRun", Position = 0)]
  [int] $PipelineRunId = 0,

  [Parameter(ParameterSetName = "ByPipeline")]
  [int] $PipelineId = 0,

  [Parameter(ParameterSetName = "BySession")]
  [int] $SessionId = 0,

  [Parameter(ParameterSetName = "ByJob")]
  [string] $JobName = "",

  [Parameter(ParameterSetName = "ListActive")]
  [switch] $ListActive,

  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $DjangoDeploymentName = "django",

  [int] $Tail = 100,
  [switch] $NoFollow,
  [switch] $IncludeControl
)

$ErrorActionPreference = "Stop"

function Write-Step([string] $msg) {
  Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Invoke-DjangoShell([string] $python) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $output = $python | kubectl --context=$Context exec "deployment/$DjangoDeploymentName" -n $Namespace -i -- python manage.py shell 2>&1
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  $text = ($output | Out-String)
  if ($code -gt 1) {
    Write-Host $text
    throw "Django shell failed with exit $code"
  }
  return $text
}

function Get-TaggedLines([string] $text, [string] $tag) {
  $prefix = "@@$tag="
  $out = @()
  foreach ($line in ($text -split "`r?`n")) {
    if ($line.StartsWith($prefix)) {
      $out += $line.Substring($prefix.Length).Trim()
    }
  }
  return $out
}

# ---------------------------------------------------------------------------
# List active runs
# ---------------------------------------------------------------------------
if ($ListActive) {
  Write-Step "Active pipeline runs + in-progress migrations"
  $python = @'
from collections import Counter
from apps.elttool.models import PipelineRun, MigrationRecord

runs = list(
    PipelineRun.objects.filter(status="running").select_related("pipeline").order_by("-id")[:30]
)
print("ACTIVE_PIPELINE_RUNS", len(runs))
for r in runs:
    name = getattr(r.pipeline, "name", "") if r.pipeline_id else ""
    print(
        f"RUN id={r.id} pipeline_id={r.pipeline_id} name={name!r} "
        f"started={r.started_at} status={r.status}"
    )

mrs = list(
    MigrationRecord.objects.filter(
        status__in=["in_progress", "pending", "queued", "running"]
    ).order_by("-id")[:100]
)
print("ACTIVE_MIGRATION_RECORDS", len(mrs))
by_job = Counter()
for m in mrs:
    jn = (m.job_name or "")[:120]
    by_job[jn] += 1
    print(
        f"MR id={m.id} session={m.migration_session_id} table={m.table_name} "
        f"status={m.status} task_id={m.task_id} job={jn}"
    )
print("JOB_NAME_COUNTS")
for jn, n in by_job.most_common(40):
    print(f"  {n:3d}  {jn}")
'@
  Write-Host (Invoke-DjangoShell $python)
  exit 0
}

if ($PipelineRunId -le 0 -and $PipelineId -le 0 -and $SessionId -le 0 -and -not $JobName) {
  Write-Host "Pass -PipelineRunId, -PipelineId, -SessionId, -JobName, or -ListActive" -ForegroundColor Yellow
  Write-Host "Example: .\watch-flow-jobs.ps1 -PipelineRunId 1148"
  exit 1
}

$jobNamePy = ($JobName -replace "\\", "\\\\" -replace "'", "\\'")

Write-Step "Resolving flow / per-job Celery workers from Django"

$python = @"
import hashlib
import re
from collections import Counter, defaultdict

from apps.elttool.models import PipelineRun, MigrationRecord

PIPELINE_RUN_RE = re.compile(r"^pipeline_run_(\d+)")

run_id = int($PipelineRunId) if int($PipelineRunId) else None
pipeline_id = int($PipelineId) if int($PipelineId) else None
session_id = int($SessionId) if int($SessionId) else None
job_name_arg = '''$jobNamePy'''.strip() or None


def job_hash(name: str) -> str:
    return hashlib.sha256((name or "").encode("utf-8")).hexdigest()[:12]


def fleet_key(name: str) -> str:
    m = PIPELINE_RUN_RE.match(name or "")
    if m:
        return f"pipeline_run_{m.group(1)}"
    return name or ""


def fleet_hash(name: str) -> str:
    return job_hash(fleet_key(name))


def flow_deploy(name: str) -> str:
    return ("celery-worker-flow-" + fleet_hash(name))[:63].rstrip("-")


def job_deploy(name: str) -> str:
    return ("celery-worker-job-" + job_hash(name))[:63].rstrip("-")


run = None
if run_id:
    run = PipelineRun.objects.select_related("pipeline").filter(id=run_id).first()
elif pipeline_id:
    run = (
        PipelineRun.objects.select_related("pipeline")
        .filter(pipeline_id=pipeline_id)
        .order_by("-id")
        .first()
    )
elif job_name_arg:
    m = PIPELINE_RUN_RE.match(job_name_arg)
    if m:
        run = PipelineRun.objects.select_related("pipeline").filter(id=int(m.group(1))).first()
elif session_id:
    mr0 = (
        MigrationRecord.objects.filter(migration_session_id=session_id)
        .exclude(job_name__isnull=True)
        .exclude(job_name="")
        .order_by("-id")
        .first()
    )
    if mr0 and mr0.job_name:
        m = PIPELINE_RUN_RE.match(mr0.job_name)
        if m:
            run = PipelineRun.objects.select_related("pipeline").filter(id=int(m.group(1))).first()

if run is not None:
    run_id = run.id
    print("=== PIPELINE RUN ===")
    print(
        f"id={run.id} pipeline_id={run.pipeline_id} "
        f"name={getattr(run.pipeline, 'name', '')!r}"
    )
    print(f"status={run.status} started_at={run.started_at} finished_at={run.finished_at}")
    meta = run.metadata if isinstance(run.metadata, dict) else {}
    nodes = meta.get("nodes") or meta.get("node_states") or {}
    if isinstance(nodes, dict) and nodes:
        counts = Counter(str((v or {}).get("status") or v) for v in nodes.values())
        print("node_status_counts", dict(counts))
        for nid, nmeta in list(nodes.items())[:50]:
            if not isinstance(nmeta, dict):
                print(f"  node={nid} raw={nmeta!r}")
                continue
            print(
                f"  node={nid} status={nmeta.get('status')} "
                f"session={nmeta.get('migration_session_id') or nmeta.get('session_id')} "
                f"job={str(nmeta.get('pipeline_job_name') or nmeta.get('job_name') or '')[:90]}"
            )
    print(f"@@RUN_ID={run.id}")
    print(f"@@FLOW_DEPLOY={flow_deploy(f'pipeline_run_{run.id}')}")
    print(f"@@FLEET_HASH={fleet_hash(f'pipeline_run_{run.id}')}")
    # Must match celery_job_worker_pool.JOB_WORKER_LABEL_PIPELINE_RUN
    print(f"@@LABEL=datasyncher.com/pipeline-run-id={run.id}")
elif not session_id and not job_name_arg:
    print("ERROR no PipelineRun resolved")
    raise SystemExit(2)

mrs_qs = MigrationRecord.objects.all()
if run_id:
    mrs_qs = mrs_qs.filter(job_name__startswith=f"pipeline_run_{run_id}")
elif session_id:
    mrs_qs = mrs_qs.filter(migration_session_id=session_id)
elif job_name_arg:
    mrs_qs = mrs_qs.filter(job_name__startswith=job_name_arg)

mrs = list(mrs_qs.order_by("job_name", "table_name")[:500])
print("=== MIGRATION RECORDS ===")
print(f"count={len(mrs)}")
print("status_counts", dict(Counter(m.status for m in mrs)))

jobs = sorted({(m.job_name or "") for m in mrs if m.job_name})
if job_name_arg and job_name_arg not in jobs:
    jobs.append(job_name_arg)
if run_id and not jobs:
    jobs = [f"pipeline_run_{run_id}"]

by_job = defaultdict(list)
for m in mrs:
    by_job[m.job_name or ""].append(m)

for jn in jobs:
    rows = by_job.get(jn, [])
    fh = fleet_hash(jn)
    jh = job_hash(jn)
    print(f"--- job={jn} ---")
    print(f"  fleet_key={fleet_key(jn)} fleet_hash={fh}")
    print(f"  flow_deploy={flow_deploy(jn)}  queue=migration:flow:{fh}")
    print(f"  job_deploy={job_deploy(jn)}   queue=migration:job:{jh}")
    print(f"@@DEPLOY={flow_deploy(jn)}")
    print(f"@@DEPLOY={job_deploy(jn)}")
    print(f"@@JOB_HASH={jh}")
    print(f"@@FLEET_HASH={fh}")
    sc = Counter(r.status for r in rows)
    print(f"  mr_status={dict(sc)} tables={len(rows)}")
    for r in rows[:40]:
        print(
            f"    table={r.table_name} status={r.status} session={r.migration_session_id} "
            f"task_id={r.task_id} staging={r.staging_records_count}"
        )
    if len(rows) > 40:
        print(f"    ... {len(rows) - 40} more tables")
"@

$detailOut = Invoke-DjangoShell $python
Write-Host $detailOut

if ($detailOut -match "ERROR no PipelineRun resolved") {
  Write-Host "Could not resolve a pipeline run / jobs from the given filters." -ForegroundColor Red
  exit 2
}

$resolvedRunId = @(Get-TaggedLines $detailOut "RUN_ID") | Select-Object -First 1
$labelSelector = @(Get-TaggedLines $detailOut "LABEL") | Select-Object -First 1
$deployNames = @(Get-TaggedLines $detailOut "DEPLOY") | Select-Object -Unique
$jobHashes = @(Get-TaggedLines $detailOut "JOB_HASH") | Select-Object -Unique
$fleetHashes = @(Get-TaggedLines $detailOut "FLEET_HASH") | Select-Object -Unique

if (-not $resolvedRunId -and $PipelineRunId -gt 0) {
  $resolvedRunId = "$PipelineRunId"
}
if (-not $labelSelector -and $resolvedRunId) {
  $labelSelector = "datasyncher.com/pipeline-run-id=$resolvedRunId"
}

# ---------------------------------------------------------------------------
# Kubernetes view
# ---------------------------------------------------------------------------
Write-Step "Kubernetes worker deployments / pods"

# kubectl writes "No resources found" to stderr; do not treat that as terminating.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"

$podsToFollow = New-Object System.Collections.Generic.HashSet[string]

if ($labelSelector) {
  Write-Host "-- label: $labelSelector" -ForegroundColor DarkCyan
  kubectl --context=$Context get deploy,pods -n $Namespace -l $labelSelector -o wide 2>&1 | Write-Host
  $labeled = kubectl --context=$Context get pods -n $Namespace -l $labelSelector `
    -o jsonpath="{.items[*].metadata.name}" 2>$null
  if ($labeled) {
    foreach ($p in ($labeled -split "\s+")) {
      if ($p) { [void]$podsToFollow.Add($p) }
    }
  }
}

foreach ($dep in $deployNames) {
  if (-not $dep) { continue }
  Write-Host "-- deployment $dep" -ForegroundColor DarkCyan
  $exists = kubectl --context=$Context get deploy $dep -n $Namespace -o name 2>$null
  if ($exists) {
    kubectl --context=$Context get deploy $dep -n $Namespace -o wide 2>&1 | Write-Host
    $podNames = kubectl --context=$Context get pods -n $Namespace `
      -o jsonpath="{.items[*].metadata.name}" 2>$null
    foreach ($p in ($podNames -split "\s+")) {
      if ($p -and $p.StartsWith("$dep-")) {
        Write-Host "  pod: $p"
        [void]$podsToFollow.Add($p)
      }
    }
  } else {
    Write-Host "  (not found yet / already torn down)" -ForegroundColor DarkYellow
  }
}

# Hash-label fallback (per-job workers without pipeline-run label)
foreach ($h in ($jobHashes + $fleetHashes | Select-Object -Unique)) {
  if (-not $h) { continue }
  $sel = "datasyncher.com/job-hash=$h"
  $names = kubectl --context=$Context get pods -n $Namespace -l $sel `
    -o jsonpath="{.items[*].metadata.name}" 2>$null
  if ($names) {
    Write-Host "-- hash label $sel -> $names" -ForegroundColor DarkCyan
    foreach ($p in ($names -split "\s+")) {
      if ($p) { [void]$podsToFollow.Add($p) }
    }
  }
}

Write-Host "-- job-pool overview (managed-by=job-pool)" -ForegroundColor DarkCyan
kubectl --context=$Context get pods -n $Namespace `
  -l "datasyncher.com/managed-by=job-pool" `
  -o custom-columns=NAME:.metadata.name,APP:.metadata.labels.app,HASH:.metadata.labels.datasyncher\.com/job-hash,RUN:.metadata.labels.datasyncher\.com/pipeline-run-id,STATUS:.status.phase `
  --no-headers 2>&1 | Write-Host

Write-Host "-- control plane" -ForegroundColor DarkCyan
kubectl --context=$Context get pods -n $Namespace `
  -l "app in (celery-worker-control,celery-worker-control-reconcile)" `
  -o wide 2>&1 | Write-Host

if ($IncludeControl) {
  $ctrl = kubectl --context=$Context get pods -n $Namespace -l "app=celery-worker-control" `
    -o jsonpath="{.items[*].metadata.name}" 2>$null
  foreach ($p in ($ctrl -split "\s+")) {
    if ($p) { [void]$podsToFollow.Add($p) }
  }
}

$ErrorActionPreference = $prevEap

$podList = @($podsToFollow)
Write-Host "`nMatched worker pods ($($podList.Count)):" -ForegroundColor Green
$podList | ForEach-Object { Write-Host "  $_" }

# ---------------------------------------------------------------------------
# Live logs
# ---------------------------------------------------------------------------
if ($NoFollow) {
  Write-Step "Skipping live logs (-NoFollow). Re-run without it to tail."
  exit 0
}

Write-Step "Live logs (Ctrl+C to stop)  tail=$Tail"
$ErrorActionPreference = "Continue"

if ($labelSelector) {
  $json = kubectl --context=$Context get pods -n $Namespace -l $labelSelector -o json 2>$null
  $hasLabeled = $false
  if ($json) {
    try {
      $obj = $json | ConvertFrom-Json
      $hasLabeled = @($obj.items).Count -gt 0
    } catch {
      $hasLabeled = $false
    }
  }
  if ($hasLabeled) {
    Write-Host "Following pods with label $labelSelector" -ForegroundColor Green
    kubectl --context=$Context logs -n $Namespace -l $labelSelector `
      --all-containers=true --prefix=true --tail=$Tail --follow=true
    exit $LASTEXITCODE
  }
}

if ($podList.Count -eq 1) {
  $pod = $podList[0]
  Write-Host "Following pod $pod" -ForegroundColor Green
  kubectl --context=$Context logs -n $Namespace $pod `
    --all-containers=true --prefix=true --tail=$Tail --follow=true
  exit $LASTEXITCODE
}

if ($podList.Count -gt 1) {
  Write-Host "Following $($podList.Count) pods (merged). Prefer stern if installed for cleaner output." -ForegroundColor Green
  $jobs = @()
  foreach ($pod in $podList) {
    $jobs += Start-Job -ScriptBlock {
      param($ctx, $ns, $podName, $tailLines)
      & kubectl --context=$ctx logs -n $ns $podName --all-containers=true --prefix=true --tail=$tailLines --follow=true 2>&1 |
        ForEach-Object { "$_" }
    } -ArgumentList $Context, $Namespace, $pod, $Tail
  }
  try {
    while ($true) {
      foreach ($j in $jobs) {
        Receive-Job -Job $j -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
      }
      $running = @($jobs | Where-Object { $_.State -eq "Running" })
      if ($running.Count -eq 0) { break }
      Start-Sleep -Milliseconds 250
    }
  } finally {
    $jobs | Stop-Job -ErrorAction SilentlyContinue
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
  }
  exit 0
}

Write-Host "No job/flow worker pods found for this flow yet." -ForegroundColor Yellow
Write-Host "Tailing control-plane logs (orchestrator). Re-run when workers are Ready." -ForegroundColor Yellow
kubectl --context=$Context logs -n $Namespace -l "app=celery-worker-control" `
  --all-containers=true --prefix=true --tail=$Tail --follow=true
exit $LASTEXITCODE
