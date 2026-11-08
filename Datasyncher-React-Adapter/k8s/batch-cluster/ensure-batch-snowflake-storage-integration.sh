#!/usr/bin/env bash
# Grant Snowflake's storage-integration GCP SA access to the batch metadata bucket.
# Snowflake side (once per account): run ensure-batch-snowflake-storage-integration.sql as ACCOUNTADMIN.
#
# Env (optional overrides):
#   GCP_PROJECT                  (default: datasyncher-batch)
#   GCS_BUCKET_NAME              (default: meta_data_bucket_ds_batch)
#   SNOWFLAKE_INTEGRATION        (default: from batch-snowflake.defaults.env → DATASYNCHERBATCH)
#   SNOWFLAKE_GCS_SA             (default: from batch-snowflake.defaults.env)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/batch-snowflake.defaults.env" ]]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/batch-snowflake.defaults.env"
fi

PROJECT="${GCP_PROJECT:-datasyncher-batch}"
BUCKET="${GCS_BUCKET_NAME:-meta_data_bucket_ds_batch}"
INTEGRATION="${SNOWFLAKE_INTEGRATION:-DATASYNCHERBATCH}"
SA="${SNOWFLAKE_GCS_SA:-}"

if [[ -z "${SA}" ]]; then
  echo "ERROR: SNOWFLAKE_GCS_SA is not set." >&2
  echo "  Set it in ${SCRIPT_DIR}/batch-snowflake.defaults.env or export before running." >&2
  echo "  Value from Snowflake: DESC INTEGRATION ${INTEGRATION};" >&2
  exit 1
fi

echo "==> Grant Snowflake integration SA on gs://${BUCKET}" >&2
echo "    integration=${INTEGRATION}" >&2
echo "    member=serviceAccount:${SA}" >&2

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --project="${PROJECT}" \
  --member="serviceAccount:${SA}" \
  --role="roles/storage.objectAdmin" >/dev/null

echo "==> Done. Snowflake COPY INTO from gcs://${BUCKET}/ is authorized for ${INTEGRATION}." >&2
