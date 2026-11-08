-- Batch cluster: allow Snowflake storage integration to read the batch GCS metadata bucket.
-- Run in Snowflake as ACCOUNTADMIN (or role with ALTER INTEGRATION on DATASYNCHERBATCH).
--
-- GCP IAM (automatic on bootstrap / apply-manifests):
--   bash k8s/batch-cluster/ensure-batch-snowflake-storage-integration.sh
--
-- QA/kubernetes uses integration DATASYNCHERKUB + meta_data_bucket_ds_kubernetes (separate).

DESC INTEGRATION DATASYNCHERBATCH;

-- Merge batch bucket into allowed locations (add to existing list if DESC shows more paths).
ALTER STORAGE INTEGRATION DATASYNCHERBATCH
  SET STORAGE_ALLOWED_LOCATIONS = (
    'gcs://meta_data_bucket_ds_batch/'
  );

DESC INTEGRATION DATASYNCHERBATCH;
