#!/usr/bin/env bash
# Ensure a GCS bucket + celery-gcs-uploader SA + Workload Identity for the batch cluster.
# Prints the bucket name on stdout (last line).
#
# Env:
#   GCP_PROJECT          (default: datasyncher-batch)
#   GCP_REGION           (default: us-central1)
#   GCS_BUCKET_NAME      (default: meta_data_bucket_ds_batch)
#   GCS_SA_NAME          (default: celery-gcs-uploader)
set -euo pipefail

PROJECT="${GCP_PROJECT:-datasyncher-batch}"
REGION="${GCP_REGION:-us-central1}"
BUCKET="${GCS_BUCKET_NAME:-meta_data_bucket_ds_batch}"
SA_NAME="${GCS_SA_NAME:-celery-gcs-uploader}"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
KSA="default/celery-gcs"

gcloud services enable storage.googleapis.com iam.googleapis.com --project="${PROJECT}" >/dev/null 2>&1 || true

if ! gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "==> Creating GCS bucket gs://${BUCKET} in ${PROJECT} (${REGION})" >&2
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
fi

if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "==> Creating service account ${SA_EMAIL}" >&2
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Celery GCS uploader (batch)" \
    --project="${PROJECT}"
fi

echo "==> Grant ${SA_EMAIL} storage.admin on gs://${BUCKET}" >&2
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.admin" \
  --project="${PROJECT}" >/dev/null

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
WI_MEMBER="serviceAccount:${PROJECT}.svc.id.goog[${KSA}]"
echo "==> Workload Identity: ${KSA} → ${SA_EMAIL} (member=${WI_MEMBER})" >&2
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${WI_MEMBER}" >/dev/null 2>&1 || true

# Snowflake COPY INTO reads the same bucket — grant the integration GCP SA automatically.
SNOWFLAKE_SCRIPT="${BASH_SOURCE%/*}/ensure-batch-snowflake-storage-integration.sh"
if [[ -x "${SNOWFLAKE_SCRIPT}" ]]; then
  echo "==> Snowflake storage integration IAM on gs://${BUCKET}" >&2
  GCS_BUCKET_NAME="${BUCKET}" GCP_PROJECT="${PROJECT}" bash "${SNOWFLAKE_SCRIPT}"
fi

echo "${BUCKET}"
