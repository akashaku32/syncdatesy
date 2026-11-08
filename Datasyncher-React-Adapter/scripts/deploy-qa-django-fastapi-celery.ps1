# Build and roll out QA backend images (excludes frontend).
# django image: django, celery-beat-django, apscheduler, celery-worker-django
# fast-api image: fastapi, celery-worker-control, celery-worker, celery-beat-migration
# migration-function image: migration-function

$ErrorActionPreference = "Stop"
$AdapterRoot = Split-Path $PSScriptRoot -Parent
$BackendRoot = Join-Path $AdapterRoot "backend"
$Namespace = "default"
$Project = "datasyncher-kubernetes"
$Tag = "qa"

$DjangoImage = "gcr.io/$Project/datasyncher-django-app:$Tag"
$FastApiImage = "gcr.io/$Project/datasyncher-fast-api:$Tag"
$MigImage = "gcr.io/$Project/datasyncher-migration-function:$Tag"

Write-Host "=== gcloud / cluster ===" -ForegroundColor Cyan
gcloud config set account admin@datasyncher.com
gcloud config set project $Project
gcloud container clusters get-credentials datasyncher-cluster --region us-central1 --project $Project

Write-Host "`n=== Cloud Build: django ===" -ForegroundColor Cyan
Set-Location $AdapterRoot
gcloud builds submit --config=deploy/docker/cloudbuild-django-qa.yaml --project=$Project .

Write-Host "`n=== Cloud Build: fast-api ===" -ForegroundColor Cyan
Set-Location $BackendRoot
gcloud builds submit --config=cloudbuild/qa/fastapi.yaml --project=$Project .

Write-Host "`n=== Cloud Build: migration-function ===" -ForegroundColor Cyan
gcloud builds submit --config=cloudbuild/qa/migration-function.yaml --project=$Project .

Write-Host "`n=== Rollout django-based workloads ===" -ForegroundColor Cyan
kubectl set image deployment/django django=$DjangoImage -n $Namespace
kubectl rollout restart deployment/django -n $Namespace
if (kubectl get deployment celery-worker-django -n $Namespace 2>$null) {
    kubectl set image deployment/celery-worker-django celery-worker-django=$DjangoImage -n $Namespace 2>$null
    if ($LASTEXITCODE -ne 0) {
        kubectl set image deployment/celery-worker-django celery=$DjangoImage -n $Namespace
    }
    kubectl rollout restart deployment/celery-worker-django -n $Namespace
}
if (kubectl get statefulset celery-beat-django -n $Namespace 2>$null) {
    kubectl set image statefulset/celery-beat-django beat=$DjangoImage -n $Namespace
    kubectl rollout restart statefulset/celery-beat-django -n $Namespace
}
if (kubectl get statefulset apscheduler -n $Namespace 2>$null) {
    kubectl set image statefulset/apscheduler apscheduler=$DjangoImage -n $Namespace 2>$null
    if ($LASTEXITCODE -ne 0) {
        kubectl set image statefulset/apscheduler beat=$DjangoImage -n $Namespace 2>$null
        if ($LASTEXITCODE -ne 0) {
            kubectl set image statefulset/apscheduler scheduler=$DjangoImage -n $Namespace
        }
    }
    kubectl rollout restart statefulset/apscheduler -n $Namespace
}
kubectl rollout status deployment/django -n $Namespace --timeout=600s

Write-Host "`n=== Rollout fast-api + celery ===" -ForegroundColor Cyan
kubectl set image deployment/fastapi fastapi=$FastApiImage -n $Namespace
kubectl set image deployment/celery-worker-control celery=$FastApiImage -n $Namespace
if (kubectl get deployment celery-worker -n $Namespace 2>$null) {
    kubectl set image deployment/celery-worker celery=$FastApiImage -n $Namespace
}
if (kubectl get deployment celery-beat-migration -n $Namespace 2>$null) {
    kubectl set image deployment/celery-beat-migration beat=$FastApiImage -n $Namespace
}
kubectl rollout restart deployment/fastapi deployment/celery-worker-control -n $Namespace
if (kubectl get deployment celery-worker -n $Namespace 2>$null) {
    kubectl rollout restart deployment/celery-worker -n $Namespace
}
if (kubectl get deployment celery-beat-migration -n $Namespace 2>$null) {
    kubectl rollout restart deployment/celery-beat-migration -n $Namespace
}
kubectl rollout status deployment/fastapi -n $Namespace --timeout=600s
kubectl rollout status deployment/celery-worker-control -n $Namespace --timeout=600s

Write-Host "`n=== Rollout migration-function ===" -ForegroundColor Cyan
kubectl set image deployment/migration-function migration-function=$MigImage -n $Namespace
kubectl rollout restart deployment/migration-function -n $Namespace
kubectl rollout status deployment/migration-function -n $Namespace --timeout=600s

Write-Host "`n=== Migrate django ===" -ForegroundColor Cyan
kubectl exec -n $Namespace deploy/django -- python manage.py migrate --noinput

Write-Host "`n=== Verify backend pods (frontend excluded) ===" -ForegroundColor Cyan
kubectl get pods -n $Namespace -l 'app in (django,fastapi,celery-worker-control,celery-worker,celery-worker-django,migration-function,celery-beat-django,apscheduler)' -o wide
kubectl get deploy,statefulset -n $Namespace -o custom-columns=KIND:.kind,NAME:.metadata.name,READY:.status.readyReplicas,IMAGE:.spec.template.spec.containers[0].image | Select-String -NotMatch "frontend|ingress"

Write-Host "`nDone (frontend not rolled out)." -ForegroundColor Green
