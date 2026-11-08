# Roll out already-pushed gcr.io/datasyncher-kubernetes/datasyncher-fast-api:qa
# Requires: gcloud auth login as admin@datasyncher.com (cluster admin)

$ErrorActionPreference = "Stop"
gcloud config set account admin@datasyncher.com | Out-Null
gcloud container clusters get-credentials datasyncher-cluster --region us-central1 --project datasyncher-kubernetes
kubectl rollout restart deployment/fastapi deployment/celery-worker deployment/celery-beat-migration -n default
kubectl rollout status deployment/fastapi -n default --timeout=300s
kubectl rollout status deployment/celery-worker -n default --timeout=300s
Write-Host "Rollout complete." -ForegroundColor Green
