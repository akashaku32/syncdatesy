# Build, migrate (elttool 0060), and roll out per-job isolation + Prometheus metrics on QA.
# Requires: gcloud auth as admin@datasyncher.com, kubectl access to datasyncher-cluster.

$ErrorActionPreference = "Stop"
$AdapterRoot = Split-Path $PSScriptRoot -Parent
$BackendRoot = Join-Path $AdapterRoot "backend"
$MigRoot = Join-Path $BackendRoot "migration-function"
$Namespace = "default"
$Project = "datasyncher-kubernetes"
$Tag = "qa"

Write-Host "=== gcloud / cluster ===" -ForegroundColor Cyan
gcloud config set account admin@datasyncher.com
gcloud config set project $Project
gcloud container clusters get-credentials datasyncher-cluster --region us-central1 --project $Project

Write-Host "`n=== Cloud Build: django (includes migration 0060) ===" -ForegroundColor Cyan
Set-Location $AdapterRoot
gcloud builds submit --config=deploy/docker/cloudbuild-django-qa.yaml --project=$Project .

Write-Host "`n=== Rollout django (migration file must be in image) ===" -ForegroundColor Cyan
kubectl rollout restart deployment/django -n $Namespace
kubectl rollout status deployment/django -n $Namespace --timeout=600s

Write-Host "`n=== Django migrate elttool 0060 ===" -ForegroundColor Cyan
kubectl exec -n $Namespace deploy/django -- python manage.py migrate elttool 0060 --noinput
kubectl exec -n $Namespace deploy/django -- python manage.py showmigrations elttool | Select-String "0060"

Write-Host "`n=== Cloud Build: fast-api (celery + metrics) ===" -ForegroundColor Cyan
Set-Location $BackendRoot
gcloud builds submit --config=cloudbuild/qa/fastapi.yaml --project=$Project .

Write-Host "`n=== Apply RBAC + control worker + monitoring ===" -ForegroundColor Cyan
Set-Location $AdapterRoot
kubectl apply -f k8s/rbac/celery-worker-k8s.yaml
kubectl apply -f k8s/deployments/celery-worker-control.yaml
kubectl apply -f k8s/monitoring/celery-control-metrics-service.yaml
kubectl apply -f k8s/monitoring/celery-control-podmonitoring.yaml
kubectl apply -f k8s/monitoring/fastapi-podmonitoring.yaml

Write-Host "`n=== Rollout fast-api workloads ===" -ForegroundColor Cyan
$FastApiImage = "gcr.io/$Project/datasyncher-fast-api:$Tag"
kubectl set image deployment/fastapi "fastapi=$FastApiImage" -n $Namespace
kubectl set image deployment/celery-worker "celery=$FastApiImage" -n $Namespace
kubectl set image deployment/celery-worker-control "celery=$FastApiImage" -n $Namespace

kubectl rollout restart deployment/fastapi deployment/celery-worker deployment/celery-worker-control -n $Namespace
kubectl rollout status deployment/fastapi -n $Namespace --timeout=600s
kubectl rollout status deployment/celery-worker -n $Namespace --timeout=600s
kubectl rollout status deployment/celery-worker-control -n $Namespace --timeout=600s

Write-Host "`n=== Verify ===" -ForegroundColor Cyan
kubectl get pods -n $Namespace -l 'app in (django,fastapi,celery-worker,celery-worker-control)'
kubectl exec -n $Namespace deploy/django -- python manage.py showmigrations elttool | Select-String "0060"

Write-Host "`nDone. Per-job mode: CELERY_JOB_ISOLATION_MODE=per_job is set on celery-worker-control only." -ForegroundColor Green
Write-Host "FastAPI/shared celery-worker remain shared until you flip the flag. Metrics enabled on control + fastapi manifests." -ForegroundColor Green
