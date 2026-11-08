#!/usr/bin/env bash
# Build all GKE app images with tag :batch (Django, FastAPI/Celery worker, migration-function, optional frontend).
# Does not push and does not run kubectl — safe for other clusters using :qa / pinned digests in git.
#
# From Datasyncher-React-Adapter:
#   chmod +x scripts/build-batch-images.sh
#   ./scripts/build-batch-images.sh
#
# Optional:
#   TAG=my-batch-1 INCLUDE_FRONTEND=0 ./scripts/build-batch-images.sh

set -euo pipefail
ADAPTER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="${ADAPTER}/backend"
WORKSPACE="$(cd "${ADAPTER}/.." && pwd)"
FRONTEND="${WORKSPACE}/frontend"

: "${PROJECT:=datasyncher-batch}"
: "${TAG:=batch}"
REG="gcr.io/${PROJECT}"

DJ="${REG}/datasyncher-django-app:${TAG}"
FA="${REG}/datasyncher-fast-api:${TAG}"
MF="${REG}/datasyncher-migration-function:${TAG}"
FE="${REG}/datasyncher-frontend:${TAG}"

echo "==> Django -> ${DJ}"
docker build -f "${ADAPTER}/deploy/docker/Dockerfile" -t "${DJ}" "${ADAPTER}"

echo "==> FastAPI (Celery worker image) -> ${FA}"
docker build -f "${BACKEND}/fast-api/Dockerfile" -t "${FA}" "${BACKEND}"

echo "==> migration-function -> ${MF}"
docker build -f "${BACKEND}/migration-function/Dockerfile" -t "${MF}" "${BACKEND}/migration-function"

if [[ "${INCLUDE_FRONTEND:-1}" == "1" ]]; then
  echo "==> Frontend (Vite mode=batch) -> ${FE}"
  docker build --build-arg VITE_BUILD_MODE=batch -f "${FRONTEND}/Dockerfile" -t "${FE}" "${FRONTEND}"
fi

echo ""
echo "Built. Push when ready:"
echo "  gcloud auth configure-docker gcr.io"
echo "  docker push ${DJ} && docker push ${FA} && docker push ${MF}"
if [[ "${INCLUDE_FRONTEND:-1}" == "1" ]]; then
  echo "  docker push ${FE}"
fi
echo "Batch cluster (kubectl context = batch), roll live refs without changing git:"
echo "  BATCH_SET_ALL_WORKLOAD_IMAGES=1 BATCH_IMAGE_TAG=${TAG} ./k8s/batch-cluster/apply-manifests.sh"
echo "Or PowerShell: .\\scripts\\build-push-k8s.ps1 -Tag ${TAG} -SkipBuild -SkipPush -RelaxDjangoRollout"
