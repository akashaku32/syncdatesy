-- Unity Catalog: allow Databricks SQL warehouse to COPY INTO batch temp CSVs from GCS.
--
-- Fivetran-style setup (recommended on GCP when Workload Identity Federation is disabled):
--   1. Run the steps below ONCE in Databricks Catalog Explorer (metastore admin).
--   2. Copy the returned GCP service account email from the storage credential.
--   3. Save these values on the Datasyncher destination connection config:
--        gcs_external_location_mode     = manual
--        gcs_bucket_url                 = gs://meta_data_bucket_ds_batch/
--        gcs_storage_credential_name    = datasyncher_batch_gcs_cred
--        gcs_external_location_name     = datasyncher_batch_gcs
--        gcs_service_account_email      = <email from step 2>
--   4. On each migration, Datasyncher verifies UC objects, grants bucket IAM for that SA,
--      uploads temp CSVs to GCS, and runs COPY INTO.
--
-- Auto mode (default): migration code tries REST API credential creation when missing.
-- Requires Databricks GCP Workload Identity Federation. Falls back to INSERT if setup fails.
--
-- Bucket must match gcs_bucket_url / cluster env GCS_BUCKET_URL:
--   gs://meta_data_bucket_ds_batch/

-- ── Manual setup in Databricks Catalog Explorer (preferred) ───────────────────
--
-- A. Storage credential
--    Catalog Explorer → External data → Storage credentials → Create credential
--    Name: datasyncher_batch_gcs_cred
--    Type: GCP service account (Databricks-managed) — NOT a customer JSON key
--    Copy the generated service account email (e.g. databricks-uc-xxxxx@...gserviceaccount.com)
--
-- B. External location
--    Catalog Explorer → External data → External locations → Create location
--    Name: datasyncher_batch_gcs
--    URL:  gs://meta_data_bucket_ds_batch/
--    Storage credential: datasyncher_batch_gcs_cred
--
-- C. Grant READ FILES to warehouse users
GRANT READ FILES ON EXTERNAL LOCATION datasyncher_batch_gcs TO `account users`;
--
-- D. GCP IAM (on the GCS bucket) — also auto-applied by Datasyncher when gcs_service_account_email is set:
--    roles/storage.objectAdmin          → serviceAccount:<databricks-generated-sa>
--    roles/storage.legacyBucketReader   → serviceAccount:<databricks-generated-sa>  (optional, for listing)

-- ── SQL-only external location (after credential exists via UI/API) ───────────
-- NOTE: On GCP you CANNOT create storage credentials via SQL. Use Catalog Explorer or REST API.

CREATE EXTERNAL LOCATION IF NOT EXISTS datasyncher_batch_gcs
  URL 'gs://meta_data_bucket_ds_batch/'
  WITH (STORAGE CREDENTIAL datasyncher_batch_gcs_cred);

GRANT READ FILES ON EXTERNAL LOCATION datasyncher_batch_gcs TO `account users`;

-- Smoke test
SHOW EXTERNAL LOCATIONS LIKE 'datasyncher_batch_gcs';

-- Optional COPY INTO smoke test (after credential + location exist)
-- COPY INTO test.datasyncher_demo.copy_smoke_staging
-- FROM 'gs://meta_data_bucket_ds_batch/temp_data_dynamics_f&o_copy_smoke_demo_0.csv'
-- FILEFORMAT = CSV
-- FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'false');
