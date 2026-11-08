# Databricks — Client-cloud staging setup (Hybrid model)

Use this guide when the client's **Databricks workspace and staging storage should live in the same cloud** (Azure Databricks + ADLS, AWS Databricks + S3, or GCP Databricks + client-owned GCS).

This avoids cross-cloud reads (e.g. Azure Databricks reading Datasyncher's GCS bucket), which is the main limitation of the default staging model.

**Related docs**

| Model | When to use | Doc |
|--------|-------------|-----|
| **Unity Catalog managed volume** (recommended) | Any cloud; PAT/OAuth only | [databricks-managed-volume-staging-setup.md](./databricks-managed-volume-staging-setup.md) |
| **Datasyncher-managed GCS staging** | Existing GCP Databricks + external location | [databricks-gcp-unity-catalog-setup.md](./databricks-gcp-unity-catalog-setup.md) |
| **Client-cloud staging** (this doc) | Azure/AWS Databricks, data residency, same-cloud COPY INTO | This file |

> **Implementation status:** **Managed volume staging is implemented** (see
> [databricks-managed-volume-staging-setup.md](./databricks-managed-volume-staging-setup.md)).
> Client-owned S3/ADLS/GCS **external-location** staging described below remains the
> **documented target** for hybrid object-storage setups — confirm with your Datasyncher
> team before enabling on production connections.

---

## Overview

**Load engine selection is automatic:** Dynamics initial loads use Spark; incremental loads and Salesforce always use SQL warehouse. The destination form only requires hostname, HTTP path, access token, and catalog.

Datasyncher loads data into Databricks using:

1. **Temporary CSV files** in **client-owned** object storage (GCS, ADLS, or S3)
2. **`COPY INTO`** from that path into per-table staging Delta tables
3. **`MERGE`** into target tables in the client's catalog/schema

```text
Sources → Datasyncher → client staging bucket → COPY INTO → Delta (UC) → MERGE → target tables
                              ↑                        ↑
                    client grants WRITE          UC external location
                    to Datasyncher               grants READ to warehouse
```

This matches the **Fivetran Hybrid** pattern: stage in the **customer's cloud**, ingest with **`COPY INTO`**, keep final tables in Unity Catalog.

---

## Who owns what

| Party | Owns |
|--------|------|
| **Client** | Staging bucket/container, cloud IAM for read (Databricks) and write (Datasyncher), UC storage credential + external location |
| **Datasyncher** | Extract/transform, upload temp CSVs to client staging, run COPY INTO + MERGE via SQL warehouse |
| **Connection form** | Staging URL, UC names, cloud write credentials (see [Connection fields](#connection-fields-datasyncher-destination)) |

Datasyncher does **not** host client staging data on its own bucket in this model.

---

## When to use which model

| Client Databricks | Recommended staging | Why |
|-------------------|---------------------|-----|
| **GCP** | Datasyncher GCS *or* client GCS | Both work; client GCS if data must stay in client GCP project |
| **Azure** | **Client ADLS / Blob** | Avoids cross-cloud GCS read; native UC + COPY |
| **AWS** | **Client S3** | Same as Azure — staging aligned with workspace cloud |

---

## Prerequisites (all clouds)

- Unity Catalog enabled on the Databricks workspace
- Metastore admin (or equivalent) for storage credentials and external locations
- SQL warehouse configured for Datasyncher (hostname, HTTP path, PAT, catalog, schema)
- Dedicated **staging prefix** for temp CSVs only (not shared with production datasets without a clear subpath)
- External location URL **must exactly match** the staging prefix Datasyncher will write to (including trailing `/`)

---

## Request for client administrator (copy and send)

### Context

Datasyncher will upload temporary CSV files to **your** cloud storage, then run **`COPY INTO`** in your Databricks SQL warehouse. Your team must:

1. Create staging storage in **the same cloud as Databricks**
2. Register a Unity Catalog **storage credential** and **external location** on that prefix
3. Grant the SQL warehouse **`READ FILES`** on the external location
4. Grant **Datasyncher write access** to upload temp files into that prefix

---

## Setup by cloud

### Option A — GCP Databricks + client GCS

**Staging URL example:** `gs://your-company-datasyncher-staging/dynamics/`

#### Client tasks

**1. Create GCS bucket/prefix**

- Bucket in the client's GCP project (or a dedicated staging bucket)
- Prefix reserved for Datasyncher temp files, e.g. `gs://your-company-datasyncher-staging/dynamics/`

**2. Unity Catalog — storage credential**

- **Catalog Explorer** → **External data** → **Storage credentials** → **Create**
- **Type:** GCP service account (**Databricks-managed**)
- **Name:** e.g. `datasyncher_client_staging_cred`
- Record the **GCP service account email** Databricks generates

**3. Unity Catalog — external location**

- **Name:** e.g. `datasyncher_client_staging`
- **URL:** `gs://your-company-datasyncher-staging/dynamics/` *(must match upload prefix)*
- **Storage credential:** from step 2

**4. Grant READ FILES**

```sql
GRANT READ FILES ON EXTERNAL LOCATION `datasyncher_client_staging` TO `account users`;
```

*(Use your warehouse group or service principal if not `account users`.)*

**5. GCS bucket IAM**

| Principal | Role | Purpose |
|-----------|------|---------|
| Databricks UC service account (from step 2) | `roles/storage.objectViewer` (or `objectAdmin` on prefix via conditions) | COPY INTO reads temp CSVs |
| **Datasyncher GCP service account** *(provided by Datasyncher)* | `roles/storage.objectCreator` (+ `objectViewer` if needed for overwrite/delete) | Upload temp CSVs |

**6. Return to Datasyncher**

- Staging URL
- Storage credential name
- External location name
- Databricks GCP service account email
- Confirmation that Datasyncher writer SA has been granted on the bucket

---

### Option B — Azure Databricks + client ADLS / Blob

**Staging URL examples:**

- ADLS Gen2: `abfss://staging@yourstorageaccount.dfs.core.windows.net/datasyncher/`
- Blob: `wasbs://staging@yourstorageaccount.blob.core.windows.net/datasyncher/`

#### Client tasks

**1. Create storage account + container**

- Dedicated container or subpath for Datasyncher temp files
- Note the full URL used in UC (Gen2 `abfss://` is recommended)

**2. Unity Catalog — storage credential**

Follow [Databricks — Manage storage credentials (Azure)](https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/manage-storage-credentials):

- **Type:** Azure managed identity or Microsoft Entra ID service principal
- **Name:** e.g. `datasyncher_client_staging_cred`

**3. Unity Catalog — external location**

- **Name:** e.g. `datasyncher_client_staging`
- **URL:** same as staging prefix above
- **Storage credential:** from step 2

**4. Grant READ FILES**

```sql
GRANT READ FILES ON EXTERNAL LOCATION `datasyncher_client_staging` TO `account users`;
```

**5. Grant Datasyncher write access** (choose one)

| Method | Client provides to Datasyncher |
|--------|--------------------------------|
| **Service principal** (recommended) | Tenant ID, client ID, client secret; SP has **Storage Blob Data Contributor** on container/prefix |
| **SAS token** | Container-level SAS with Read, Write, List |
| **Account key** | Storage account name + key *(less ideal)* |

**Network:** If the storage account restricts public access, allow Datasyncher egress IPs or use Private Link per your security review.

**6. Return to Datasyncher**

- Staging URL (`abfss://` or `wasbs://`)
- External location name (+ storage credential name)
- Azure write credentials (SP or SAS)
- Warehouse hostname, HTTP path, PAT, catalog, schema

---

### Option C — AWS Databricks + client S3

**Staging URL example:** `s3://your-company-datasyncher-staging/dynamics/`

#### Client tasks

**1. Create S3 bucket + prefix**

- Dedicated prefix for temp CSVs, e.g. `s3://your-company-datasyncher-staging/dynamics/`

**2. Unity Catalog — storage credential**

Follow [Databricks — Storage credentials (AWS IAM)](https://docs.databricks.com/aws/en/connect/unity-catalog/cloud-storage/storage-credentials):

- **Type:** IAM role
- **Name:** e.g. `datasyncher_client_staging_cred`
- Trust policy allows Databricks to assume the role for this metastore

**3. Unity Catalog — external location**

- **Name:** e.g. `datasyncher_client_staging`
- **URL:** `s3://your-company-datasyncher-staging/dynamics/`
- **Storage credential:** from step 2

**4. Grant READ FILES**

```sql
GRANT READ FILES ON EXTERNAL LOCATION `datasyncher_client_staging` TO `account users`;
```

**5. S3 IAM for Datasyncher write access**

Grant Datasyncher an IAM user or role with at least on the staging prefix:

- `s3:PutObject`
- `s3:GetObject`
- `s3:DeleteObject` *(optional, for temp file cleanup)*
- `s3:ListBucket`

**6. Return to Datasyncher**

- Staging URL
- External location name (+ credential name)
- AWS access key + secret, or role ARN + external ID for assume-role
- Warehouse hostname, HTTP path, PAT, catalog, schema

---

## Connection fields (Datasyncher destination)

Specification for client-cloud staging on the Databricks destination connection.

### Common fields

| Field | Description | Example |
|-------|-------------|---------|
| `staging_mode` | `client` for client-cloud; `datasyncher` for default GCS | `client` |
| `staging_url` | Full cloud prefix for temp CSVs | `abfss://staging@acct.dfs.core.windows.net/datasyncher/` |
| `gcs_storage_credential_name` | UC storage credential name | `datasyncher_client_staging_cred` |
| `gcs_external_location_name` | UC external location name | `datasyncher_client_staging` |
| `server_hostname` | Databricks workspace host | `adb-xxx.azuredatabricks.net` |
| `http_path` | SQL warehouse HTTP path | `/sql/1.0/warehouses/...` |
| `access_token` | PAT or OAuth token | *(secret)* |
| `catalog` | Target Unity Catalog | `main` |
| `schema` | Target schema | `dynamics_fo` |

### Cloud-specific write credentials

**GCP (client bucket)**

| Field | Description |
|-------|-------------|
| `gcs_service_account_email` | Databricks UC SA (for verification) |
| *(optional)* `staging_writer_type` | `datasyncher_sa` — client grants Datasyncher SA on bucket |

**Azure**

| Field | Description |
|-------|-------------|
| `azure_tenant_id` | Entra tenant ID |
| `azure_client_id` | Service principal application ID |
| `azure_client_secret` | SP secret *(secret)* |
| *or* `azure_sas_token` | Container SAS |

**AWS**

| Field | Description |
|-------|-------------|
| `aws_access_key_id` | IAM user/role access key |
| `aws_secret_access_key` | Secret *(secret)* |
| *or* `aws_role_arn` + `aws_external_id` | Cross-account assume role |

---

## What happens on each sync

1. Datasyncher validates UC storage credential + external location in the **client workspace**
2. Confirms external location URL covers `staging_url`
3. Uploads chunk CSV to **`staging_url`** in **client storage** (not Datasyncher GCS)
4. Runs **`COPY INTO`** staging Delta table from that file URL
5. **`MERGE`** into the target table; truncates/drops staging as today
6. *(Optional)* Deletes temp object in client storage after successful MERGE

If COPY INTO fails and the chunk is small enough, Datasyncher may fall back to **SQL INSERT** over the warehouse connection (slower; row limit applies).

---

## Deliverables checklist (client → Datasyncher)

| # | Item | GCP | Azure | AWS |
|---|------|-----|-------|-----|
| 1 | Staging URL (with trailing `/`) | `gs://.../` | `abfss://.../` | `s3://.../` |
| 2 | UC storage credential name | ✓ | ✓ | ✓ |
| 3 | UC external location name | ✓ | ✓ | ✓ |
| 4 | Databricks warehouse connection | ✓ | ✓ | ✓ |
| 5 | Write credentials for Datasyncher | Datasyncher SA on bucket | SP / SAS | IAM user or role |
| 6 | READ FILES granted to warehouse | ✓ | ✓ | ✓ |

---

## Comparison: Datasyncher GCS vs client-cloud staging

| | Datasyncher GCS (default) | Client-cloud staging |
|---|---------------------------|----------------------|
| Staging location | Datasyncher infra (`GCS_BUCKET_URL`) | Client bucket in same cloud as Databricks |
| Cross-cloud COPY | Yes, if Databricks ≠ GCP | No |
| Client bucket setup | UC only (point at our prefix) | Bucket + UC + write IAM |
| Datasyncher bucket IAM | We grant Databricks SA on **our** bucket | Client grants Datasyncher **write** on **their** bucket |
| Best for | GCP Databricks | Azure/AWS Databricks, residency requirements |
| Reference | [databricks-gcp-unity-catalog-setup.md](./databricks-gcp-unity-catalog-setup.md) | This doc |

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `Permission denied: external location` | External location URL ≠ `staging_url`, or READ FILES not granted |
| COPY INTO works but 0 rows | File uploaded outside external location prefix |
| Datasyncher upload fails | Write credentials missing or IAM/SAS scoped too narrowly |
| Works on GCP Databricks but not Azure | Still using Datasyncher GCS — switch to client ADLS staging |
| `Access denied` on Azure storage | Firewall/VNet on storage account blocking Datasyncher egress |
| Large tables slow / timeout | COPY failing → INSERT fallback; fix UC + staging alignment |

### Smoke test (after setup)

After the first sync uploads a temp file, run in Databricks SQL:

```sql
-- GCP
LIST 'gs://your-bucket/dynamics/temp_data_dynamics_f&o_<table>_<connection_id>.csv';

-- Azure (adjust path)
LIST 'abfss://staging@account.dfs.core.windows.net/datasyncher/temp_data_dynamics_f&o_<table>_<connection_id>.csv';

-- AWS
LIST 's3://your-bucket/dynamics/temp_data_dynamics_f&o_<table>_<connection_id>.csv';
```

---

## Security notes

- Use a **dedicated staging prefix**; do not reuse production lake paths without isolation.
- Prefer **service principal / IAM role** over long-lived account keys where possible.
- Scope write access to the staging prefix only (IAM conditions, container-level SAS).
- Rotate Datasyncher write credentials on the same schedule as other integration secrets.
- Temp CSVs may contain replicated source data — apply the same classification as target tables.

---

## Internal references

- Default (Datasyncher GCS) setup: [databricks-gcp-unity-catalog-setup.md](./databricks-gcp-unity-catalog-setup.md)
- Load implementation: `backend/migration-function/services/databricks_dynamics_load_helpers.py`
- SQL reference: `k8s/batch-cluster/ensure-batch-databricks-external-location.sql`
- Fivetran equivalent: [Databricks destination setup — Hybrid / external location](https://fivetran.com/docs/destinations/databricks/databricks-setup-guide)

---

## Databricks documentation (by cloud)

- [External locations — GCP](https://docs.databricks.com/gcp/en/connect/unity-catalog/cloud-storage/external-locations-gcs)
- [Cloud storage — Azure](https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/)
- [Cloud storage — AWS](https://docs.databricks.com/aws/en/connect/unity-catalog/cloud-storage/)
- [COPY INTO](https://docs.databricks.com/en/sql/language-manual/delta-copy-into.html)
