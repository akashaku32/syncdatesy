# Build, push, and roll out datasyncher-fast-api:qa (fast-api + celery-worker share this image).
# Build: Cloud Build (admin@datasyncher.com) OR local Docker + gcr-key.json service account.
# Rollout: kubectl as admin@datasyncher.com (not the gcr-key SA — that breaks cluster RBAC).

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
$RepoRoot = Split-Path -Parent $BackendRoot
$Image = "gcr.io/datasyncher-kubernetes/datasyncher-fast-api:qa"
$GcrKey = Join-Path $RepoRoot "gcr-key.json"
Set-Location $BackendRoot

function Push-ImageLocal {
    if (-not (Test-Path $GcrKey)) { throw "Missing $GcrKey for local push fallback" }
    Write-Host "=== Local Docker build/push: $Image ===" -ForegroundColor Cyan
    gcloud auth activate-service-account --key-file=$GcrKey --project=datasyncher-kubernetes | Out-Null
    docker build -t $Image -f fast-api/Dockerfile .
    $token = gcloud auth print-access-token
    $token | docker login -u oauth2accesstoken --password-stdin https://gcr.io | Out-Null
    docker push $Image
}

Write-Host "=== Build/push $Image ===" -ForegroundColor Cyan
$buildOk = $false
try {
    gcloud builds submit --config=cloudbuild/qa/fastapi.yaml --project=datasyncher-kubernetes .
    $buildOk = $true
} catch {
    Write-Host "Cloud Build failed; trying local Docker push..." -ForegroundColor Yellow
    Push-ImageLocal
    $buildOk = $true
}
if (-not $buildOk) { Push-ImageLocal }

# Use human admin for kubectl (revoke SA from active gcloud if needed)
gcloud config set account admin@datasyncher.com | Out-Null
gcloud container clusters get-credentials datasyncher-cluster --region us-central1 --project=datasyncher-kubernetes

Write-Host "=== Apply control-plane manifests ===" -ForegroundColor Cyan
kubectl apply -f (Join-Path $RepoRoot "k8s/deployments/celery-worker-control.yaml") -n default
kubectl apply -f (Join-Path $RepoRoot "k8s/deployments/celery-worker-control-reconcile.yaml") -n default

Write-Host "=== Rollout restart (fast-api, celery-worker, celery-beat-migration, control workers) ===" -ForegroundColor Cyan
kubectl rollout restart deployment/fastapi deployment/celery-worker deployment/celery-beat-migration deployment/celery-worker-control deployment/celery-worker-control-reconcile -n default
kubectl rollout status deployment/fastapi -n default --timeout=300s
kubectl rollout status deployment/celery-worker -n default --timeout=300s
kubectl rollout status deployment/celery-worker-control -n default --timeout=300s
kubectl rollout status deployment/celery-worker-control-reconcile -n default --timeout=300s

Write-Host "=== Verify single-table fan-out gate (CELERY_ENQUEUED orchestrate_migration on reload) ===" -ForegroundColor Cyan
Write-Host "After reload, logs should show: task=orchestrate_migration (not call_function)"
