<#
.SYNOPSIS
  Build Docker images locally, push to GCR, and update GKE workloads (no git).

.DESCRIPTION
  Builds:
    - datasyncher-django-app  (deploy/docker/Dockerfile, repo root)
    - datasyncher-fast-api    (backend/fast-api/Dockerfile, context backend/)
    - datasyncher-migration-function (backend/migration-function/Dockerfile)
    - datasyncher-frontend    (optional: sibling ..\frontend\Dockerfile; use -IncludeFrontend; use -FrontendViteMode batch for gcp.datasyncher.com bundle)

  Requires: Docker Desktop, gcloud auth (run: gcloud auth login), kubectl context for the cluster.

  Mutating the same tag (e.g. :qa) in GCR does not restart pods unless the Deployment changes or you
  run `kubectl rollout restart`; this script runs rollout restart on frontend when -IncludeFrontend is used.

.EXAMPLE
  .\scripts\build-push-k8s.ps1 -Tag qa

.EXAMPLE
  .\scripts\build-push-k8s.ps1 -Tag qa -IncludeFrontend

.EXAMPLE
  .\scripts\build-push-k8s.ps1 -Tag qa -SkipRollout

.EXAMPLE
  .\scripts\build-push-k8s.ps1 -Tag qa -RelaxDjangoRollout

.EXAMPLE
  # Build only for batch cluster (:batch tags); does not push or touch kubectl — safe for other clusters.
  .\scripts\build-push-k8s.ps1 -Tag batch -SkipPush -SkipRollout -IncludeFrontend -FrontendViteMode batch

.EXAMPLE
  # After docker push :batch: set workloads only on the batch cluster (never touches default kubectl context target).
  .\scripts\build-push-k8s.ps1 -Tag batch -SkipBuild -SkipPush -IncludeFrontend -KubectlContext gke_datasyncher-kubernetes_us-central1_datasyncher-batch-cluster
#>

[CmdletBinding()]
param(
    [string] $Project = "datasyncher-kubernetes",
    [string] $Tag = "qa",
    [string] $Namespace = "default",
    [switch] $SkipBuild,
    [switch] $SkipPush,
    [switch] $SkipRollout,
    [switch] $RelaxDjangoRollout,
    # Build/push sibling repo frontend (..\frontend) and rollout deployment/frontend + rollout restart for mutable :qa tag.
    [switch] $IncludeFrontend,
    # Passed as Docker build-arg VITE_BUILD_MODE (e.g. "batch" uses data-syncher-ui/.env.batch → gcp.datasyncher.com).
    [string] $FrontendViteMode = "production",
    [string] $RolloutTimeout = "10m",
    # When set (e.g. gke_PROJECT_REGION_datasyncher-batch-cluster), every kubectl command uses --context=...
    # so you never rely on the default cluster — safe when your default context is datasyncher-cluster (QA).
    [string] $KubectlContext = ""
)

$ErrorActionPreference = "Stop"
$Registry = "gcr.io/$Project"

function Invoke-KubectlCli {
    param([Parameter(Mandatory)][string[]] $Arguments)
    if ($KubectlContext) {
        & kubectl --context=$KubectlContext @Arguments
    }
    else {
        & kubectl @Arguments
    }
}
$AdapterRoot = Split-Path $PSScriptRoot -Parent
$BackendRoot = Join-Path $AdapterRoot "backend"
$WorkspaceRoot = Split-Path $AdapterRoot -Parent
$FrontendRoot = Join-Path $WorkspaceRoot "frontend"

$DjangoImage = "$Registry/datasyncher-django-app`:$Tag"
$FastApiImage = "$Registry/datasyncher-fast-api`:$Tag"
$MigrationImage = "$Registry/datasyncher-migration-function`:$Tag"
$FrontendImage = "$Registry/datasyncher-frontend`:$Tag"

Write-Host "Registry: $Registry" -ForegroundColor Cyan
Write-Host "Tag:      $Tag" -ForegroundColor Cyan

if (-not $SkipBuild) {
    Write-Host "`n[build] Django..." -ForegroundColor Yellow
    docker build -f (Join-Path $AdapterRoot "deploy\docker\Dockerfile") -t $DjangoImage $AdapterRoot

    Write-Host "`n[build] FastAPI + Celery worker..." -ForegroundColor Yellow
    docker build -f (Join-Path $BackendRoot "fast-api\Dockerfile") -t $FastApiImage $BackendRoot

    Write-Host "`n[build] migration-function..." -ForegroundColor Yellow
    docker build -f (Join-Path $BackendRoot "migration-function\Dockerfile") -t $MigrationImage $BackendRoot

    if ($IncludeFrontend) {
        $frontendDockerfile = Join-Path $FrontendRoot "Dockerfile"
        if (-not (Test-Path $frontendDockerfile)) {
            Write-Warning "IncludeFrontend set but Dockerfile not found at $frontendDockerfile - skipping frontend build."
        }
        else {
            Write-Host "`n[build] frontend (nginx + React dist, VITE_BUILD_MODE=$FrontendViteMode)..." -ForegroundColor Yellow
            docker build -f $frontendDockerfile --build-arg "VITE_BUILD_MODE=$FrontendViteMode" -t $FrontendImage $FrontendRoot
            if ($LASTEXITCODE -ne 0) {
                throw "docker build failed for frontend (exit $LASTEXITCODE)"
            }
        }
    }
}

if (-not $SkipPush) {
    Write-Host "`n[push] Configure GCR auth if needed: gcloud auth configure-docker gcr.io" -ForegroundColor DarkGray
    docker push $DjangoImage
    docker push $FastApiImage
    docker push $MigrationImage
    if ($IncludeFrontend -and (Test-Path (Join-Path $FrontendRoot "Dockerfile"))) {
        docker push $FrontendImage
    }
}

if ($SkipRollout) {
    Write-Host "`nDone (rollout skipped). Set workloads manually or re-run without -SkipRollout." -ForegroundColor Green
    exit 0
}

$ErrorActionPreference = "Continue"

if ($KubectlContext) {
    Write-Host "`n[k8s] cluster context: $KubectlContext (datasyncher-cluster is untouched)" -ForegroundColor Green
}
Write-Host "`n[k8s] set images (namespace=$Namespace)..." -ForegroundColor Yellow
Invoke-KubectlCli @('set', 'image', 'deployment/django', "django=$DjangoImage", '-n', $Namespace)
Invoke-KubectlCli @('set', 'image', 'deployment/fastapi', "fastapi=$FastApiImage", '-n', $Namespace)
Invoke-KubectlCli @('set', 'image', 'deployment/celery-worker-control', "celery=$FastApiImage", '-n', $Namespace)
if ((Invoke-KubectlCli @('get', 'deployment', 'celery-worker-control-reconcile', '-n', $Namespace, '--ignore-not-found', '-o', 'name') 2>$null)) {
    Invoke-KubectlCli @('set', 'image', 'deployment/celery-worker-control-reconcile', "celery=$FastApiImage", '-n', $Namespace)
}
Invoke-KubectlCli @('set', 'image', 'deployment/celery-worker', "celery=$FastApiImage", '-n', $Namespace)
Invoke-KubectlCli @('set', 'image', 'deployment/celery-beat-migration', "beat=$FastApiImage", '-n', $Namespace)
Invoke-KubectlCli @('set', 'image', 'deployment/celery-worker-django', "celery-worker-django=$DjangoImage", '-n', $Namespace)
Invoke-KubectlCli @('set', 'image', 'deployment/migration-function', "migration-function=$MigrationImage", '-n', $Namespace)
Invoke-KubectlCli @('set', 'image', 'statefulset/celery-beat-django', "beat=$DjangoImage", '-n', $Namespace)
Invoke-KubectlCli @('set', 'image', 'statefulset/apscheduler', "apscheduler=$DjangoImage", '-n', $Namespace)

if ($IncludeFrontend -and (Test-Path (Join-Path $FrontendRoot "Dockerfile"))) {
    Write-Host "`n[k8s] deployment/frontend image..." -ForegroundColor Yellow
    Invoke-KubectlCli @('set', 'image', 'deployment/frontend', "frontend=$FrontendImage", '-n', $Namespace)
    # Same tag string often does not trigger a rollout — force new pods so imagePullPolicy: Always pulls fresh layers.
    Invoke-KubectlCli @('rollout', 'restart', 'deployment/frontend', '-n', $Namespace)
}

$PatchRelax = Join-Path $PSScriptRoot "k8s-patch-django-rollout-relax.json"
$PatchRestore = Join-Path $PSScriptRoot "k8s-patch-django-rollout-restore.json"

if ($RelaxDjangoRollout -and (Test-Path $PatchRelax)) {
    Write-Host "`n[django] temporary rolling strategy (relax)..." -ForegroundColor DarkYellow
    Invoke-KubectlCli @('patch', 'deployment', 'django', '-n', $Namespace, '--type=merge', '--patch-file', $PatchRelax)
}

$rollouts = @(
    "deployment/django",
    "deployment/fastapi",
    "deployment/celery-worker-control",
    "deployment/celery-worker-control-reconcile",
    "deployment/celery-worker",
    "deployment/celery-beat-migration",
    "deployment/celery-worker-django",
    "deployment/migration-function",
    "statefulset/celery-beat-django",
    "statefulset/apscheduler"
)
if ($IncludeFrontend -and (Test-Path (Join-Path $FrontendRoot "Dockerfile"))) {
    $rollouts = @(
        "deployment/django",
        "deployment/fastapi",
        "deployment/celery-worker-control",
        "deployment/celery-worker-control-reconcile",
        "deployment/celery-worker",
        "deployment/celery-beat-migration",
        "deployment/celery-worker-django",
        "deployment/migration-function",
        "deployment/frontend",
        "statefulset/celery-beat-django",
        "statefulset/apscheduler"
    )
}
foreach ($r in $rollouts) {
    Write-Host "`n[k8s] rollout status $r ..." -ForegroundColor Cyan
    $timeout = if ([string]::IsNullOrWhiteSpace($RolloutTimeout)) { "10m" } else { $RolloutTimeout }
    Invoke-KubectlCli @('rollout', 'status', $r, '-n', $Namespace, "--timeout=$timeout")
}

if ($RelaxDjangoRollout -and (Test-Path $PatchRestore)) {
    Write-Host "`n[django] restore rolling strategy..." -ForegroundColor DarkYellow
    Invoke-KubectlCli @('patch', 'deployment', 'django', '-n', $Namespace, '--type=merge', '--patch-file', $PatchRestore)
}

Write-Host "`nDone. Optional: kubectl exec -n $Namespace deploy/django -- python manage.py migrate --noinput" -ForegroundColor Green
