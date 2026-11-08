#!/usr/bin/env bash
# Enable Workload Identity on the batch GKE cluster and set GKE_METADATA on node pools
# so celery-gcs pods (and per-job workers) can upload to GCS via celery-gcs-uploader SA.
#
# Env:
#   GCP_PROJECT   (default: datasyncher-batch)
#   GCP_REGION    (default: us-central1)
#   CLUSTER       (default: datasyncher-batch-cluster)
set -euo pipefail

PROJECT="${GCP_PROJECT:-datasyncher-batch}"
REGION="${GCP_REGION:-us-central1}"
CLUSTER="${CLUSTER:-datasyncher-batch-cluster}"
WORKLOAD_POOL="${PROJECT}.svc.id.goog"

echo "==> Enable Workload Identity on ${CLUSTER} (pool=${WORKLOAD_POOL})" >&2
gcloud container clusters update "${CLUSTER}" \
  --region="${REGION}" \
  --project="${PROJECT}" \
  --workload-pool="${WORKLOAD_POOL}" \
  --quiet

POOLS="$(gcloud container node-pools list \
  --cluster="${CLUSTER}" \
  --region="${REGION}" \
  --project="${PROJECT}" \
  --format='value(name)' | tr -d '\r')"

for pool in ${POOLS}; do
  pool="$(echo "${pool}" | tr -d '\r')"
  echo "==> Set workload-metadata=GKE_METADATA on node pool ${pool}" >&2
  gcloud container node-pools update "${pool}" \
    --cluster="${CLUSTER}" \
    --region="${REGION}" \
    --project="${PROJECT}" \
    --workload-metadata=GKE_METADATA \
    --quiet
done

echo "==> Workload Identity enabled for ${CLUSTER}" >&2
