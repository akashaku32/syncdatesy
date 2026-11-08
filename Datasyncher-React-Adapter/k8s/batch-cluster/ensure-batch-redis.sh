#!/usr/bin/env bash
# Ensure a Memorystore Redis instance exists in the batch GCP project (same VPC as GKE).
# Prints the host IP on stdout (last line). Used by bootstrap and apply-manifests.
#
# Env:
#   GCP_PROJECT   (default: datasyncher-batch)
#   GCP_REGION    (default: us-central1)
#   REDIS_NAME    (default: datasyncher-batch-redis)
set -euo pipefail

PROJECT="${GCP_PROJECT:-datasyncher-batch}"
REGION="${GCP_REGION:-us-central1}"
NAME="${REDIS_NAME:-datasyncher-batch-redis}"
NETWORK="projects/${PROJECT}/global/networks/default"

gcloud services enable redis.googleapis.com --project="${PROJECT}" >/dev/null 2>&1 || true

if ! gcloud redis instances describe "${NAME}" --region="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "==> Creating Memorystore ${NAME} in ${PROJECT} (${REGION})" >&2
  gcloud redis instances create "${NAME}" \
    --size=1 \
    --region="${REGION}" \
    --network="${NETWORK}" \
    --redis-version=redis_7_0 \
    --tier=basic \
    --project="${PROJECT}"
fi

HOST="$(gcloud redis instances describe "${NAME}" \
  --region="${REGION}" \
  --project="${PROJECT}" \
  --format='value(host)')"
if [[ -z "${HOST}" ]]; then
  echo "ERROR: could not read Redis host for ${NAME}" >&2
  exit 1
fi
echo "${HOST}"
