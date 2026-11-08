<#
.SYNOPSIS
  Implements checklist from "Why things may not work as planned": repo batch artifacts, cluster image IDs, migrate, batch URL probe.

.DESCRIPTION
  -RepoOnly: verify workspace contains batch rollback migration 0057 and no batch_views.py (no kubectl).
  Default: also queries kubectl for django/frontend/celery-worker-django images and runs migrate in deploy/django.
  Requires: gcloud auth for GKE (gke-gcloud-auth-plugin).

.EXAMPLE
  .\scripts\verify-plan-rollout.ps1 -RepoOnly
  .\scripts\verify-plan-rollout.ps1 -Namespace default
#>
[CmdletBinding()]
param(
    [string] $Namespace = "default",
    [switch] $RepoOnly,
    [switch] $SkipMigrate,
    [string] $ApiBaseUrl = "",
    [string] $ApiToken = "",
    [int] $ConnectionId = 0
)

$ErrorActionPreference = "Stop"
# This file lives in Datasyncher-React-Adapter/scripts/
$Adapter = Split-Path $PSScriptRoot -Parent
$Need = @(
    (Join-Path $Adapter "apps\elttool\migrations\0055_migrationbatch_batchtable.py"),
    (Join-Path $Adapter "apps\elttool\migrations\0056_migrate_connection_tables_to_batch1.py"),
    (Join-Path $Adapter "apps\elttool\migrations\0057_remove_migration_batch_models.py")
)
$MustNotExist = @(
    (Join-Path $Adapter "apps\elttool\v1\batch_views.py")
)

Write-Host "=== Repo migration / batch cleanup ===" -ForegroundColor Cyan
$missing = @()
foreach ($p in $Need) {
    if (-not (Test-Path -LiteralPath $p)) { $missing += $p }
    else { Write-Host "  OK: $p" -ForegroundColor Green }
}
foreach ($p in $MustNotExist) {
    if (Test-Path -LiteralPath $p) {
        Write-Host "UNEXPECTED FILE (batch feature removed): $p" -ForegroundColor Red
        exit 1
    }
    else { Write-Host "  OK (absent): $p" -ForegroundColor Green }
}
if ($missing.Count -gt 0) {
    Write-Host "MISSING:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  $_" }
    exit 1
}

if ($RepoOnly) {
    Write-Host "`nRepo-only check passed. Run without -RepoOnly after: gcloud auth login" -ForegroundColor Green
    exit 0
}

# kubectl writes credential errors to stderr; do not treat as terminating
$ErrorActionPreference = "Continue"
$clusterOk = $true
Write-Host "`n=== Cluster images (deployment spec) ===" -ForegroundColor Cyan
$deploys = @("django", "frontend", "celery-worker-django")
foreach ($d in $deploys) {
    $img = & kubectl get deploy $d -n $Namespace -o jsonpath="{.spec.template.spec.containers[0].image}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  $d : kubectl failed (run: gcloud auth login)" -ForegroundColor Red
        $clusterOk = $false
        break
    }
    if (-not $img) {
        Write-Host "  $d : (not found)" -ForegroundColor Yellow
        $clusterOk = $false
    } else {
        Write-Host "  $d : $img" -ForegroundColor Green
    }
}

if ($clusterOk) {
    Write-Host "`n=== Running pods imageID (first pod per label) ===" -ForegroundColor Cyan
    $labels = @{ "django" = "app=django"; "frontend" = "app=frontend"; "celery" = "app=celery-worker-django" }
    foreach ($k in $labels.Keys) {
        $sel = $labels[$k]
        $id = & kubectl get pod -n $Namespace -l $sel -o jsonpath="{.items[0].status.containerStatuses[0].imageID}" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  $k : (error)" -ForegroundColor Yellow
        } else {
            Write-Host "  $k : $id" -ForegroundColor Green
        }
    }
}

if (-not $SkipMigrate -and $clusterOk) {
    Write-Host "`n=== migrate (django) ===" -ForegroundColor Cyan
    & kubectl exec -n $Namespace deploy/django -- python manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) {
        Write-Host "migrate exited with failure." -ForegroundColor Red
        $clusterOk = $false
    }
} elseif (-not $SkipMigrate -and -not $clusterOk) {
    Write-Host "`n=== migrate (skipped: cluster not reachable) ===" -ForegroundColor Yellow
}

if ($ApiBaseUrl -and $ApiToken -and $ConnectionId -gt 0) {
    Write-Host "`n=== API smoke GET .../home/ (batch routes removed; use home list) ===" -ForegroundColor Cyan
    $uri = "$ApiBaseUrl/api/v1/home/"
    $headers = @{ Authorization = "Token $ApiToken" }
    try {
        $resp = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get
        Write-Host "OK: home list response received." -ForegroundColor Green
        $resp | ConvertTo-Json -Depth 3 | Write-Host
    } catch {
        Write-Host "Request failed (check token and URL): $_" -ForegroundColor Red
        exit 1
    }
} elseif ($ApiBaseUrl -and $ApiToken) {
    Write-Host "`n=== API smoke: pass -ConnectionId <n> (optional; probes /home/) ===" -ForegroundColor Cyan
    Write-Host "Example: -ApiBaseUrl https://qa.datasyncher.com -ApiToken <token> -ConnectionId 1" -ForegroundColor DarkGray
}

if (-not $clusterOk) {
    Write-Host "`nCluster verification incomplete. Fix kubectl/gcloud auth and re-run without -RepoOnly." -ForegroundColor Yellow
    exit 1
}

Write-Host "`nDone. Compare image digests to the images you built from this repo commit." -ForegroundColor Green
exit 0
