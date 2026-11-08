# Datasyncher platform staging — buckets, containers, S3, and zero-manual setup

This guide answers: **what Datasyncher owns**, **what cloud permissions are required**, and **what (if anything) your Databricks admin must create manually** so users can connect their org’s Databricks workspace.

**Related docs**

| Topic | Doc |
|--------|-----|
| Load engine routing (Spark vs SQL) | [databricks-dynamics-load-scenarios.md](./databricks-dynamics-load-scenarios.md) |
| SQL incremental / Salesforce (simplest user path) | [databricks-managed-volume-staging-setup.md](./databricks-managed-volume-staging-setup.md) |
| Client-owned S3 / ADLS (data residency) | [databricks-client-cloud-staging-setup.md](./databricks-client-cloud-staging-setup.md) |
| Legacy GCP external location | [databricks-gcp-unity-catalog-setup.md](./databricks-gcp-unity-catalog-setup.md) |

---

## What the user enters (destination form)

For **new connections**, the Databricks destination form only needs:

| Field | Example |
|-------|---------|
| Server Hostname | `adb-1234567890.12.azuredatabricks.net` |
| HTTP Path | `/sql/1.0/warehouses/abc123...` |
| Access Token | PAT or OAuth token |
| Catalog | `main` |

Plus the connector **destination schema** (`dst_schema`). Everything else — staging bucket, load engine, UC names — is chosen automatically at runtime.

**No bucket URLs, IAM roles, or external location names appear on the form** for the default managed-volume path.

---

## What Datasyncher platform owns (not the customer)

Datasyncher runs shared **staging infrastructure** per environment. Celery workers read/write these using platform credentials configured in Kubernetes secrets (`k8s/batch-cluster/secrets/`).

| Cloud | Platform resource | Env vars | Per-connection prefix |
|-------|-------------------|----------|------------------------|
| **GCP** | GCS bucket | `GCS_BUCKET_URL`, `GCS_BUCKET_NAME` | `gs://<bucket>/dynamics_fo/<connection_id>/` |
| **Azure** | ADLS container | `DATASYNCHER_AZURE_STAGING_URL` | `<staging_url>/dynamics_fo/<connection_id>/...` |
| **AWS** | S3 bucket | `S3_BUCKET_URL`, `S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | `s3://<bucket>/dynamics_fo/<connection_id>/` |

**Batch environment examples** (replace with your deployment values):

| Resource | Example value |
|----------|---------------|
| GCS bucket | `gs://meta_data_bucket_ds_batch/` |
| Azure staging container | `abfss://datasyncher-stage@<storage-account>.dfs.core.windows.net/` |
| S3 bucket | `s3://datasyncher-stg/` |

**Who grants what on Datasyncher-owned storage**

| Party | Permission on platform bucket/container |
|-------|----------------------------------------|
| **Datasyncher workers** | **Write** — copy F&O export CSVs into staging prefix (`PutObject` / Blob Data Contributor) |
| **Databricks** (via UC external location) | **Read** — Spark Job or COPY INTO reads landed files (`READ FILES` on external location) |
| **Customer F&O export** | **Read-only** for Datasyncher — export files are never modified |

Datasyncher ops provision worker credentials once per environment. **Customers do not create or pay for these buckets** in the default model.

---

## Minimum manual work by scenario

| Scenario | User / Databricks admin creates manually? | Datasyncher auto at runtime |
|----------|-------------------------------------------|-----------------------------|
| **Salesforce → Databricks** | **Almost nothing** — PAT with warehouse + UC access | Managed volume `datasyncher_stage` (auto-created), COPY/MERGE |
| **Dynamics incremental (SQL)** | Same as above | Managed volume staging |
| **Dynamics initial (Spark) — Azure** | **None** (token needs job-cluster + secret-scope create) | Prefer UC external location; on account-admin 403, fall back to scoped SAS on a dedicated job cluster |
| **Dynamics initial (Spark) — GCP** | **None** if Databricks WIF allows auto credential | UC credential + external location; if denied, scoped SA/token on a dedicated job cluster |
| **Dynamics initial (Spark) — AWS** | **None** when platform STS/IAM is configured (optional metastore trust for UC path) | UC external location when permitted; else temporary STS credentials on a dedicated job cluster |
| **Client-owned staging** | Client bucket + UC + write IAM for Datasyncher | COPY/MERGE only — see [client-cloud staging](./databricks-client-cloud-staging-setup.md) |

### Closest to “connect without creating anything manually”

Use the **managed-volume default** for SQL paths:

1. User saves destination with hostname, HTTP path, PAT, catalog, schema.
2. On first sync, Datasyncher **auto-creates** the UC managed volume `datasyncher_stage` (when `DATABRICKS_VOLUME_AUTO_CREATE=1`, default).
3. Datasyncher uploads chunk files via Files API → COPY INTO → MERGE.

**PAT must allow (SQL / managed volume):** `USE CATALOG`, `USE SCHEMA`, `CREATE VOLUME` (or pre-existing volume), `CREATE TABLE`, `MODIFY` on target schema, and SQL warehouse access.

**PAT must allow (Dynamics Spark):** the SQL rights above, plus **create/run Jobs**, and for the zero-manual fallback: **create classic/single-user job clusters** and **create/manage the `datasyncher_staging` secret scope**. Serverless-only workspaces cannot use the fallback and still need a UC external location.

**Dynamics initial loads still use Spark.** Datasyncher lands CSVs in platform staging, then either:
1. **UC external location** (preferred — serverless Spark), or
2. **Scoped credential** (automatic fallback) — mint a short-lived read-only credential for `dynamics_fo/<connection_id>/`, store it in `datasyncher_staging`, and run a dedicated classic job cluster with `spark_conf` secret references.

After the first successful Spark load, incremental runs use managed-volume SQL — no user action.

---

## Auto-provisioning at runtime (what the backend does)

On each migration, `load_helpers.py` and `spark_job_client.py` call **ensure** functions idempotently:

| Function | When | Creates in customer Databricks |
|----------|------|-------------------------------|
| `ensure_volume_exists()` | SQL staging (default) | Managed volume + schema if missing |
| `resolve_spark_staging_access()` | Dynamics Spark staging | Chooses `external_location` vs `scoped_credential` (override / Redis cache / live probe) |
| `ensure_databricks_adls_external_location_once()` | Azure Spark (UC path) | Storage credential (if auto mode) + external location + `GRANT READ FILES` |
| `ensure_databricks_gcs_external_location_once()` | GCP Spark / legacy SQL GCS | Storage credential (if auto mode) + external location + `GRANT READ FILES` |
| `ensure_databricks_s3_external_location_once()` | AWS Spark (UC path) | Storage credential (if auto + IAM role) + external location + `GRANT READ FILES` |
| `mint_*` + secret scope put | Spark `scoped_credential` path | Secret scope `datasyncher_staging` + per-connection keys; classic `SINGLE_USER` job cluster |

**Auto vs manual mode**

| Cloud | Auto mode when | Manual mode when |
|-------|----------------|------------------|
| **Azure ADLS** | Platform SP env vars set (`DATABRICKS_AZURE_STAGING_SP_*`) | No platform SP → admin creates UC in Catalog Explorer |
| **GCS** | Default auto unless client UC fields saved on connection | Client saved credential/location names → verify-only |
| **S3** | **Default manual**; auto only with `DATABRICKS_S3_IAM_ROLE_ARN` + `s3_external_location_mode=auto` | Typical AWS setup |

Log patterns: `DBX|managed_volume_ready`, `DBX|spark_staging_access`, `DBX|scoped_cred`, `DBX|ensure_adls_external_location`, `DBX|ensure_s3_external_location`, `manual_mode: storage credential ... not found`.

**Platform ops notes for scoped fallback**

| Cloud | Datasyncher-side requirement |
|-------|------------------------------|
| Azure | Platform SP needs **Storage Blob Delegator** (+ read) on the staging account so workers can mint user-delegation SAS |
| AWS | Worker credentials that can `sts:AssumeRole` / `GetFederationToken` (optional `DATABRICKS_S3_SCOPED_ASSUME_ROLE_ARN`) |
| GCP | `DATABRICKS_GCS_SA_JSON` for downscoped token or SA key delivery into the secret scope |

---

## AWS Databricks — S3 bucket permissions (detail)

### Datasyncher-side (platform ops, one-time)

**S3 bucket** (e.g. `datasyncher-stg`):

| IAM principal | Permissions on `s3://datasyncher-stg/dynamics_fo/*` |
|---------------|-----------------------------------------------------|
| Datasyncher worker IAM user/role | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucket` |

Configured via cluster secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_URL`.

**Landing path per connection:** `s3://datasyncher-stg/dynamics_fo/<connection_id>/<table>/...`

### Customer Databricks-side (for Spark initial load)

Databricks must read the same prefix via Unity Catalog:

**1. IAM role for UC storage credential** (customer AWS account)

Create an IAM role Databricks can assume, with S3 read on the staging prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": [
        "arn:aws:s3:::datasyncher-stg",
        "arn:aws:s3:::datasyncher-stg/dynamics_fo/*"
      ]
    }
  ]
}
```

Trust policy must allow the Databricks UC master role for your workspace (see [Databricks AWS storage credentials](https://docs.databricks.com/aws/en/connect/unity-catalog/cloud-storage/storage-credentials)).

**2. Platform env (Datasyncher ops sets once per environment)**

| Env var | Purpose |
|---------|---------|
| `DATABRICKS_S3_IAM_ROLE_ARN` | IAM role ARN for auto UC credential creation |
| `DATABRICKS_S3_CREDENTIAL_NAME` | Default `datasyncher_batch_s3_cred` |
| `DATABRICKS_S3_LOCATION_NAME` | Base name; per-connection suffix `_<connection_id>` |
| `DATABRICKS_S3_EXTERNAL_LOCATION_MODE` | Set `auto` to enable runtime provisioning |
| `DATABRICKS_S3_GRANT_PRINCIPAL` | Default `account users` |

When auto mode succeeds, Datasyncher creates in the **customer workspace**:

- Storage credential → IAM role
- Fetches `external_id` from Databricks API and updates the IAM role trust policy + S3 read inline policy via AWS APIs (same pattern as GCP bucket IAM grants)
- External location → `s3://datasyncher-stg/dynamics_fo/<connection_id>/`
- `GRANT READ FILES` to warehouse principal

**Platform ops (one-time):** the Datasyncher worker AWS user (`AWS_ACCESS_KEY_ID`) must be allowed to `iam:GetRole`, `iam:UpdateAssumeRolePolicy`, `iam:GetRolePolicy`, and `iam:PutRolePolicy` on `DATABRICKS_S3_IAM_ROLE_ARN`. No External ID in the customer form or K8s secrets.

**3. SQL incremental (after initial Spark)** — no S3 involved; uses managed volume instead.

---

## Azure Databricks — container permissions (detail)

### Datasyncher-side (platform ops, one-time)

**ADLS container** (e.g. `abfss://datasyncher-stage@<account>.dfs.core.windows.net/`):

| Principal | Permission |
|-----------|------------|
| Datasyncher platform service principal | **Storage Blob Data Contributor** on staging container |
| Datasyncher workers | Use platform SP or connection string to **write** landed CSVs |

Env: `DATASYNCHER_AZURE_STAGING_URL`

**Platform SP for UC auto-provision** (enables zero manual UC in customer workspace):

| Env var | Purpose |
|---------|---------|
| `DATABRICKS_AZURE_STAGING_SP_TENANT_ID` | Entra tenant |
| `DATABRICKS_AZURE_STAGING_SP_CLIENT_ID` | App registration |
| `DATABRICKS_AZURE_STAGING_SP_CLIENT_SECRET` | SP secret |
| `DATABRICKS_ADLS_STAGING_CREDENTIAL_NAME` | Default `datasyncher_fo_staging_cred` |
| `DATABRICKS_ADLS_STAGING_LOCATION_NAME` | Per-connection: `datasyncher_fo_staging_<connection_id>` |

When platform SP is configured, Datasyncher registers it as a UC storage credential in the **customer workspace** and creates the external location on the staging prefix — **customer admin does not open Catalog Explorer**.

**Storage account:** must grant the platform SP **Storage Blob Data Reader** (Databricks read) on the staging container. Datasyncher ops configure this on the Datasyncher-owned container.

### Customer source (Dynamics F&O export)

Separate from staging — Datasyncher needs **read/list** on the customer Dataverse export container (`connection_string` on source). Export is never modified.

---

## GCP Databricks — GCS bucket permissions (detail)

### Datasyncher-side (platform ops)

| Resource | Setup script | Worker SA |
|----------|--------------|-----------|
| `gs://meta_data_bucket_ds_batch/` | `k8s/batch-cluster/ensure-batch-gcs-bucket.sh` | `celery-gcs-uploader@<project>.iam.gserviceaccount.com` |

Worker SA: `roles/storage.admin` on the bucket (write temp CSVs).

### Customer Databricks-side

**Auto mode (default for new connections without legacy GCS fields):**

1. Datasyncher POSTs UC storage credential → Databricks generates GCP SA email.
2. Datasyncher grants that SA `roles/storage.objectAdmin` on **Datasyncher’s** bucket.
3. Datasyncher CREATE EXTERNAL LOCATION + GRANT READ FILES.

Requires Databricks GCP Workload Identity Federation. If auto fails, see [databricks-gcp-unity-catalog-setup.md](./databricks-gcp-unity-catalog-setup.md) for one-time manual UC setup.

**Per-connection Spark prefix:** `gs://meta_data_bucket_ds_batch/dynamics_fo/<connection_id>/`

---

## End-to-end: Dynamics → Databricks (default platform model)

```text
┌─────────────────────────────────────────────────────────────────┐
│ CUSTOMER (one-time)                                              │
│  • Databricks destination: hostname, HTTP path, PAT, catalog     │
│  • Dynamics source: export container connection string (read)    │
│  • PAT scopes: warehouse + UC create/use on target schema       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ DATASYNCHER PLATFORM (ops-provisioned, shared)                   │
│  • GCS / ADLS / S3 staging bucket or container                   │
│  • Worker credentials (AWS keys, Azure SP, GCS SA)               │
│  • Optional: DATABRICKS_*_SP_* / S3_IAM_ROLE for UC auto         │
└─────────────────────────────────────────────────────────────────┘
                              │
          Initial load (Spark)│                    Incremental (SQL)
                              ▼                              ▼
              Copy CSVs to platform staging          Managed volume
              UC external location on prefix         datasyncher_stage
              Spark Job MERGE                        COPY INTO → MERGE
                              │                              │
                              └──────────┬───────────────────┘
                                         ▼
                              Target Delta tables in UC
```

---

## Databricks PAT — minimum privileges (all clouds)

| Privilege | Used for |
|-----------|----------|
| SQL warehouse `CAN USE` | COPY INTO, MERGE, DDL |
| `USE CATALOG` / `USE SCHEMA` on target | Tables + volume |
| `CREATE VOLUME`, `READ VOLUME`, `WRITE VOLUME` | Managed volume staging (auto-create) |
| `CREATE TABLE`, `MODIFY` on target schema | Base + staging Delta tables |
| Metastore admin **not** required on customer side when auto-provision succeeds | UC credential/location created via PAT + REST API |

If auto-provision fails (common on S3 without IAM role), a metastore admin creates UC objects once — see [databricks-dynamics-load-scenarios.md § Permissions](./databricks-dynamics-load-scenarios.md#permissions-by-scenario).

---

## Platform ops checklist (Datasyncher team)

| Task | AWS | Azure | GCP |
|------|-----|-------|-----|
| Create staging bucket/container | S3 bucket + worker IAM | ADLS container + SP write | GCS bucket + uploader SA |
| Set worker env in K8s secrets | `S3_*`, `AWS_*` | `DATASYNCHER_AZURE_STAGING_URL` | `GCS_BUCKET_*` |
| Enable UC auto in customer workspaces | `DATABRICKS_S3_IAM_ROLE_ARN` + trust | `DATABRICKS_AZURE_STAGING_SP_*` | WIF (auto GCS credential) |
| Grant Databricks read on staging | Via UC IAM role | SP Reader on container | Auto IAM grant to Databricks SA |
| SQL reference script | — | — | `k8s/batch-cluster/ensure-batch-databricks-external-location.sql` |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `manual_mode: storage credential ... not found` | UC not auto-created (S3 manual default, Azure SP missing) | Configure platform env vars or one-time Catalog Explorer setup |
| `S3_BUCKET_URL is not configured` | Platform S3 not set for AWS Spark | Ops: set `S3_BUCKET_URL` in worker secrets |
| `Azure ADLS credential auto-provisioning requires DATABRICKS_AZURE_STAGING_SP_*` | Platform Azure SP not configured | Ops: set SP env vars |
| `insufficient privileges` / `WRITE VOLUME` | PAT lacks volume grants | Grant READ/WRITE VOLUME or disable auto-create and pre-create volume |
| Databricks cannot LIST S3/ADLS prefix | External location URL mismatch or READ FILES missing | Align URL with `dynamics_fo/<connection_id>/` prefix |
| Cross-cloud error | Azure DBX pointed at GCS | Use same-cloud staging — see [load scenarios](./databricks-dynamics-load-scenarios.md) |

---

## Code references

| Component | File |
|-----------|------|
| Staging / S3 / ADLS / GCS resolvers | `backend/migration-function/services/databricks/load_helpers.py` |
| Volume auto-create | `backend/migration-function/services/databricks/volume_staging_client.py` |
| Spark UC ensure | `backend/migration-function/services/databricks/spark_job_client.py` |
| K8s secret template | `k8s/batch-cluster/secrets/secrets.example.yaml` |
| GCS bucket bootstrap | `k8s/batch-cluster/ensure-batch-gcs-bucket.sh` |
