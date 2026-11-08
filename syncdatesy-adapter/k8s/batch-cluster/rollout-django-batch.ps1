<#
.SYNOPSIS
  Build (optional) and roll out datasyncher-django-app:batch to all Django-code consumers.

.DESCRIPTION
  django, celery-worker-django, and apscheduler share the same image tag. Re-pushing :batch
  does not restart pods — this script sets the image and restarts all three together so
  pipeline dispatch, Celery tasks, and scheduled runs stay in sync.

.EXAMPLE
  .\rollout-django-batch.ps1

.EXAMPLE
  .\rollout-django-batch.ps1 -Build

.EXAMPLE
  .\rollout-django-batch.ps1 -Migrate
#>
[CmdletBinding()]
param(
  [string] $Context = "gke_datasyncher-batch_us-central1_datasyncher-batch-cluster",
  [string] $Namespace = "default",
  [string] $Project = "datasyncher-batch",
  [string] $Registry = "gcr.io/datasyncher-batch",
  [string] $Tag = "batch",
  [switch] $Build,
  [switch] $Migrate,
  [switch] $Force,
  [string] $RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
  $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$guardScript = Join-Path $here "assert-no-active-work.ps1"

$img = "${Registry}/datasyncher-django-app:${Tag}"

if ($Build) {
  Write-Host "==> gcloud builds submit (django :batch)" -ForegroundColor Cyan
  Push-Location $RepoRoot
  try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    gcloud builds submit --project=$Project --config=deploy/docker/cloudbuild-batch-django.yaml .
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($buildExit -ne 0) { throw "Cloud Build failed (exit $buildExit)" }
  }
  finally {
    Pop-Location
  }
}

Write-Host "==> guard: block django rollout if migrations/pipelines are active" -ForegroundColor Cyan
if ($Force) {
  & $guardScript -Context $Context -Namespace $Namespace -DjangoDeploymentName "django" -Group "django" -Force
} else {
  & $guardScript -Context $Context -Namespace $Namespace -DjangoDeploymentName "django" -Group "django"
}

Write-Host "==> set image -> $img" -ForegroundColor Cyan
kubectl --context=$Context set image deployment/django "django=$img" -n $Namespace
kubectl --context=$Context set image deployment/celery-worker-django "celery-worker-django=$img" -n $Namespace
kubectl --context=$Context set image deployment/celery-worker-django-ops "celery-worker-django-ops=$img" -n $Namespace 2>$null
kubectl --context=$Context set image statefulset/celery-beat-django "beat=$img" -n $Namespace
kubectl --context=$Context set image statefulset/apscheduler "apscheduler=$img" -n $Namespace

Write-Host "==> rollout restart django, celery-worker-django, celery-worker-django-ops, celery-beat-django, apscheduler" -ForegroundColor Cyan
kubectl --context=$Context rollout restart deployment/django -n $Namespace
kubectl --context=$Context rollout restart deployment/celery-worker-django -n $Namespace
kubectl --context=$Context rollout restart deployment/celery-worker-django-ops -n $Namespace 2>$null
kubectl --context=$Context rollout restart statefulset/celery-beat-django -n $Namespace
kubectl --context=$Context rollout restart statefulset/apscheduler -n $Namespace

Write-Host "==> waiting for rollouts" -ForegroundColor Cyan
kubectl --context=$Context rollout status deployment/django -n $Namespace --timeout=5m
kubectl --context=$Context rollout status deployment/celery-worker-django -n $Namespace --timeout=5m
kubectl --context=$Context rollout status deployment/celery-worker-django-ops -n $Namespace --timeout=5m 2>$null
kubectl --context=$Context rollout status statefulset/celery-beat-django -n $Namespace --timeout=5m
kubectl --context=$Context rollout status statefulset/apscheduler -n $Namespace --timeout=5m

if ($Migrate) {
  Write-Host "==> migrate elttool" -ForegroundColor Cyan
  kubectl --context=$Context exec deployment/django -n $Namespace -- python manage.py migrate --noinput
}

Write-Host "==> verify pipeline_tasks import on each consumer" -ForegroundColor Green
foreach ($target in @(
  @{ kind = "deployment"; name = "django" },
  @{ kind = "deployment"; name = "celery-worker-django" },
  @{ kind = "pod"; name = "celery-beat-django-0" },
  @{ kind = "pod"; name = "apscheduler-0" }
)) {
  if ($target.kind -eq "deployment") {
    $check = kubectl --context=$Context exec "deployment/$($target.name)" -n $Namespace -- grep -c "pipeline_tasks" apps/elttool/v1/pipeline_views.py 2>$null
  } else {
    $check = kubectl --context=$Context exec $target.name -n $Namespace -- grep -c "pipeline_tasks" apps/elttool/v1/pipeline_views.py 2>$null
  }
  if ([int]$check -ge 1) {
    Write-Host "    OK $($target.name)" -ForegroundColor Green
  } else {
    Write-Host "    WARN $($target.name) - pipeline_tasks import not found" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "Done. Django image rolled out to django, celery-worker-django, celery-beat-django, and apscheduler." -ForegroundColor Green
