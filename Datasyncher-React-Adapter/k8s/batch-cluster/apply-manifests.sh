#!/usr/bin/env bash
# Apply shared workloads from k8s/* plus batch-only ingress and Django probe patch.
# Use with kubectl context = datasyncher-batch-cluster (not the QA cluster).
#
# Optional env (live cluster only; does not modify tracked YAML):
#   BATCH_SET_FRONTEND_IMAGE=1          — set only deployment/frontend (BATCH_FRONTEND_IMAGE or .../frontend:batch)
#   BATCH_SET_ALL_WORKLOAD_IMAGES=1     — set django, fastapi, celery-worker, celery-worker-django, migration-function,
#                                         frontend, celery-beat-django, apscheduler to BATCH_IMAGE_REGISTRY/*:BATCH_IMAGE_TAG
#   BATCH_IMAGE_REGISTRY=gcr.io/datasyncher-kubernetes  BATCH_IMAGE_TAG=batch
set -euo pipefail

ADAPTER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
K8S="${ADAPTER_ROOT}/k8s"
BATCH="${K8S}/batch-cluster"

apply_dir() {
  local d="$1"
  if [[ -d "$d" ]]; then
    echo "==> kubectl apply -f $d/"
    # Skip strategic-merge patch fragments (not valid standalone manifests).
    find "$d" -maxdepth 1 \( -name '*.yaml' -o -name '*.yml' \) -print0 \
      | xargs -0 -r kubectl apply -f
  fi
}

echo "==> Service accounts"
apply_dir "${K8S}/serviceaccounts"
if [[ -f "${BATCH}/serviceaccounts/celery-gcs.yaml" ]]; then
  echo "==> Batch celery-gcs Workload Identity override"
  kubectl apply -f "${BATCH}/serviceaccounts/celery-gcs.yaml"
fi

if [[ -z "${BATCH_GCS_BUCKET:-}" ]]; then
  if [[ -x "${BATCH}/ensure-batch-gcs-bucket.sh" ]]; then
    echo "==> Ensure batch GCS bucket + celery-gcs-uploader SA"
    BATCH_GCS_BUCKET="$(bash "${BATCH}/ensure-batch-gcs-bucket.sh")"
    echo "    Bucket: ${BATCH_GCS_BUCKET}"
  fi
fi

# Snowflake GCS IAM is applied inside ensure-batch-gcs-bucket.sh (idempotent).
# One-time Snowflake console step if STORAGE_ALLOWED_LOCATIONS lacks the batch bucket:
#   ${BATCH}/ensure-batch-snowflake-storage-integration.sql

echo "==> RBAC (per-job celery worker provisioning)"
apply_dir "${K8S}/rbac"

if [[ -f "${BATCH}/secrets/secrets.local.yaml" ]]; then
  echo "==> Secrets (batch: secrets.local.yaml)"
  kubectl apply -f "${BATCH}/secrets/secrets.local.yaml"
else
  echo "WARN: ${BATCH}/secrets/secrets.local.yaml missing — copy secrets.example.yaml → secrets.local.yaml, fill values, re-run."
fi

echo "==> Services"
apply_dir "${K8S}/services"

echo "==> Deployments"
apply_dir "${K8S}/deployments"

echo "==> StatefulSets"
apply_dir "${K8S}/statefulsets"

echo "==> Jobs"
apply_dir "${K8S}/jobs"

echo "==> Batch-only Jobs (db-migrate-batch — delete Job before re-run if name unchanged)"
apply_dir "${BATCH}/jobs"

echo "==> HPA"
apply_dir "${K8S}/hpa"

echo "==> KEDA"
apply_dir "${K8S}/keda"

if [[ -n "${BATCH_REDIS_IP:-}" ]]; then
  echo "==> Patch KEDA celery-worker-scaler Redis → ${BATCH_REDIS_IP}:6379 (batch Memorystore; not QA)"
  kubectl patch scaledobject celery-worker-scaler -n default --type=json \
    -p="[{\"op\":\"replace\",\"path\":\"/spec/triggers/0/metadata/address\",\"value\":\"${BATCH_REDIS_IP}:6379\"}]" \
    2>/dev/null || echo "WARN: celery-worker-scaler patch skipped (ScaledObject missing or per_job mode)"
fi

if [[ -x "${BATCH}/ensure-batch-workload-identity.sh" ]] && [[ "${BATCH_ENSURE_WORKLOAD_IDENTITY:-}" == "1" ]]; then
  echo "==> Ensure Workload Identity (BATCH_ENSURE_WORKLOAD_IDENTITY=1)"
  bash "${BATCH}/ensure-batch-workload-identity.sh"
fi

echo "==> ClusterIssuer (LetsEncrypt)"
kubectl apply -f "${K8S}/ingress/cluster-issuer-letsencrypt-prod.yaml"

echo "==> Batch ingress (gcp.datasyncher.com)"
kubectl apply -f "${BATCH}/ingress/"

echo "==> Patch django probes Host header for gcp.datasyncher.com"
kubectl patch deployment django --namespace default --type strategic --patch-file "${BATCH}/patches/django-probes-host.yaml"

# Live-only image overrides (git YAML unchanged — QA/other clusters keep :qa / pinned digests on next apply from repo).
REG="${BATCH_IMAGE_REGISTRY:-gcr.io/datasyncher-batch}"
TAG="${BATCH_IMAGE_TAG:-batch}"
if [[ "${BATCH_SET_ALL_WORKLOAD_IMAGES:-}" == "1" ]]; then
  echo "==> Set all app images → ${REG}/*:${TAG}"
  kubectl set image deployment/django "django=${REG}/datasyncher-django-app:${TAG}" -n default
  kubectl set image deployment/fastapi "fastapi=${REG}/datasyncher-fast-api:${TAG}" -n default
  kubectl set image deployment/celery-worker-control "celery=${REG}/datasyncher-fast-api:${TAG}" -n default
  kubectl set image deployment/celery-worker-control-reconcile "celery=${REG}/datasyncher-fast-api:${TAG}" -n default
  kubectl set env deployment/celery-worker-control -n default "CELERY_JOB_WORKER_IMAGE=${REG}/datasyncher-fast-api:${TAG}"
  kubectl set env deployment/celery-worker-control-reconcile -n default "CELERY_JOB_WORKER_IMAGE=${REG}/datasyncher-fast-api:${TAG}"
  kubectl set image deployment/celery-worker "celery=${REG}/datasyncher-fast-api:${TAG}" -n default
  kubectl set image deployment/celery-beat-migration "beat=${REG}/datasyncher-fast-api:${TAG}" -n default
  kubectl set image deployment/celery-worker-django "celery-worker-django=${REG}/datasyncher-django-app:${TAG}" -n default
  kubectl set image deployment/migration-function "migration-function=${REG}/datasyncher-migration-function:${TAG}" -n default
  kubectl set image deployment/frontend "frontend=${REG}/datasyncher-frontend:${TAG}" -n default
  kubectl set image statefulset/celery-beat-django "beat=${REG}/datasyncher-django-app:${TAG}" -n default
  kubectl set image statefulset/apscheduler "apscheduler=${REG}/datasyncher-django-app:${TAG}" -n default
elif [[ "${BATCH_SET_FRONTEND_IMAGE:-}" == "1" ]]; then
  : "${BATCH_FRONTEND_IMAGE:=${REG}/datasyncher-frontend:${TAG}}"
  echo "==> Set frontend container image → ${BATCH_FRONTEND_IMAGE}"
  kubectl set image deployment/frontend "frontend=${BATCH_FRONTEND_IMAGE}" -n default
fi

echo "Done. Verify: kubectl get ingress; kubectl -n default get deploy django -o yaml | grep -A2 httpHeaders"
