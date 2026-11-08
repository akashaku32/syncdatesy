#!/usr/bin/env bash
# Build the frontend Docker image for the batch cluster (Vite mode "batch" → gcp.datasyncher.com).
# Tags :batch by default so you never overwrite :qa used elsewhere.
#
# Usage (from syncdatesy repo root):
#   chmod +x frontend/build-batch-image.sh
#   ./frontend/build-batch-image.sh
#
# Optional:
#   GCR_REGISTRY=gcr.io/datasyncher-batch IMAGE_TAG=batch ./frontend/build-batch-image.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${GCR_REGISTRY:=gcr.io/datasyncher-batch}"
: "${IMAGE_NAME:=datasyncher-frontend}"
: "${IMAGE_TAG:=batch}"

FULL_IMAGE="${GCR_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
echo "==> docker build ${FULL_IMAGE} (VITE_BUILD_MODE=batch)"
docker build \
  --build-arg VITE_BUILD_MODE=batch \
  -t "${FULL_IMAGE}" \
  "${ROOT}"

echo ""
echo "Built: ${FULL_IMAGE}"
echo "Push (does not change any cluster until you roll out):"
echo "  docker push ${FULL_IMAGE}"
echo "On batch context only, roll frontend:"
echo "  kubectl config use-context gke_datasyncher-batch_us-central1_datasyncher-batch-cluster"
echo "  kubectl set image deployment/frontend frontend=${FULL_IMAGE} -n default"
echo "Or re-apply with:"
echo "  BATCH_SET_FRONTEND_IMAGE=1 BATCH_FRONTEND_IMAGE=${FULL_IMAGE} ./Datasyncher-React-Adapter/k8s/batch-cluster/apply-manifests.sh"
