# Databricks (GCP) — Unity Catalog setup for Datasyncher

> **Preferred for new connections (any cloud):** [Unity Catalog Managed Volume staging](./databricks-managed-volume-staging-setup.md) — PAT/OAuth only, no GCS credential.

This guide covers the **legacy GCP path**: Datasyncher loads data into Databricks using **COPY INTO** from temporary CSV files in **Datasyncher's GCS staging bucket**, via a Unity Catalog **external location**.

Your team sets up **Unity Catalog once in your Databricks workspace** so the SQL warehouse can read those staging files. New connections default to **managed volume** staging (core-only destination form). This legacy GCS path applies only when `staging_method=external_location` is present on a saved destination config or set by ops.

**Other staging models**

| Model | Doc |
|--------|-----|
| **Unity Catalog managed volume** (recommended) | [databricks-managed-volume-staging-setup.md](./databricks-managed-volume-staging-setup.md) |
| **Datasyncher-managed GCS** (this file) — GCP Databricks | This guide |
| **Client-cloud staging** — Azure/AWS Databricks or client-owned GCS | [databricks-client-cloud-staging-setup.md](./databricks-client-cloud-staging-setup.md) |

---

## Who owns what

| Party | Owns |
|--------|------|
| **Datasyncher** | GCS staging bucket, temp CSV uploads, bucket IAM for your Databricks service account |
| **Your Databricks admin** | Storage credential, external location, `GRANT READ FILES` in Unity Catalog |
| **Connection form (you fill)** | Storage credential name, external location name, Databricks GCP service account email |

You do **not** enter bucket URLs or GCS settings in Datasyncher — only Databricks-side names from your workspace.

---

## Request for Databricks administrator (copy and send)

Use the section below when asking your client’s **Databricks / Unity Catalog expert** to complete one-time setup and return the values needed for COPY INTO.

---

### Context for the Databricks expert

Datasyncher replicates data into your Databricks SQL warehouse using:

1. Temporary CSV files uploaded to a **Datasyncher-managed GCS staging bucket** (our infrastructure)
2. **`COPY INTO`** from that GCS path into staging Delta tables, then **`MERGE`** into target tables

Your workspace must allow the SQL warehouse to **read** from that GCS prefix via Unity Catalog.  
Datasyncher does **not** create storage credentials or external locations in your metastore — that is a **one-time setup on your side**.

We will grant the Databricks-generated GCP service account access to our staging bucket once you provide the service account email.

---

### What Datasyncher provides (give this to the expert)

| Item | Value |
|------|--------|
| **Staging GCS prefix (external location URL)** | `gs://meta_data_bucket_ds_batch/` *(replace with your environment’s prefix if different)* |
| **Suggested storage credential name** | `datasyncher_gcs_cred` *(any valid UC name is fine)* |
| **Suggested external location name** | `datasyncher_gcs_staging` *(any valid UC name is fine)* |
| **Warehouse principal for READ FILES** | `account users` *(or your warehouse group / service principal)* |

The external location URL **must exactly match** the staging prefix above (including trailing `/`).

---

### Tasks for the Databricks expert

**Prerequisites**

- Unity Catalog enabled on the GCP Databricks workspace
- Metastore admin (or equivalent) permissions
- SQL warehouse already configured for Datasyncher (hostname, HTTP path, PAT, catalog)

**Task 1 — Create storage credential**

- **Catalog Explorer** → **External data** → **Storage credentials** → **Create credential**
- **Type:** GCP service account (**Databricks-managed** — not a customer-uploaded JSON key)
- **Name:** e.g. `datasyncher_gcs_cred`
- Record the **GCP service account email** Databricks generates after creation

**Task 2 — Create external location**

- **Catalog Explorer** → **External data** → **External locations** → **Create location**
- **Name:** e.g. `datasyncher_gcs_staging`
- **URL:** `gs://meta_data_bucket_ds_batch/` *(staging prefix from Datasyncher)*
- **Storage credential:** the credential from Task 1

**Task 3 — Grant READ FILES in Unity Catalog**

Run as metastore admin (adjust principal if your warehouse does not use `account users`):

```sql
GRANT READ FILES ON EXTERNAL LOCATION `datasyncher_gcs_staging` TO `account users`;
```

**Task 4 — Optional smoke test**

After Datasyncher confirms bucket IAM for your service account, you can validate read access:

```sql
-- Replace with an actual temp file path after the first Datasyncher sync uploads a CSV
LIST 'gs://meta_data_bucket_ds_batch/temp_data_dynamics_f&o_<table>_<connection_id>.csv';
```

---

### Deliverables — please return these three values to Datasyncher

| # | Field | Example | Source |
|---|--------|---------|--------|
| 1 | **Storage credential name** | `datasyncher_gcs_cred` | Task 1 |
| 2 | **External location name** | `datasyncher_gcs_staging` | Task 2 |
| 3 | **Databricks GCP service account email** | `databricks-uc-xxxxx@project-id.iam.gserviceaccount.com` | Task 1 (credential details) |

These map directly to the Datasyncher destination connection form:

- Storage Credential Name (Databricks)
- External Location Name (Databricks)
- Databricks GCP Service Account Email

**Also confirm separately (standard Datasyncher connection):**

- Workspace URL / server hostname  
- SQL warehouse HTTP path  
- Personal access token (PAT) with UC + warehouse access  
- Target Unity Catalog name  
- Target schema name  

---

### After you receive the deliverables

Enter the three Unity Catalog values on the Datasyncher Databricks destination configuration. Datasyncher will:

1. Grant the provided service account read access on the staging GCS bucket (our side)
2. Verify the credential and external location exist in your workspace on each sync
3. Run COPY INTO → MERGE for each replication batch

---

## What happens on each sync

1. Datasyncher uploads chunk CSVs to **our** GCS staging bucket
2. Verifies your storage credential + external location exist in **your** Databricks workspace
3. Grants/ confirms IAM on our bucket for your Databricks service account
4. **COPY INTO** staging → **MERGE** into target tables

If Unity Catalog is not configured, small chunks may fall back to slower SQL INSERT.

During a table load, rows accumulate in a Databricks staging Delta table until the table finishes; Overview and execution logs show the live staging row count (`staging_records_count`). Merge into the base table runs once at table completion.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `Permission denied: external location` | External location URL in Databricks ≠ Datasyncher staging prefix, or READ FILES not granted |
| COPY INTO works but 0 rows | External location does not cover the path where Datasyncher uploads |
| `Access denied` on GCS | Service account email not saved on connection, or IAM not granted on staging bucket |

---

## Reference

- [Databricks — External locations (GCP)](https://docs.databricks.com/en/connect/unity-catalog/cloud-storage/external-locations.html)
- Client-cloud staging (Azure / AWS / client GCS): [databricks-client-cloud-staging-setup.md](./databricks-client-cloud-staging-setup.md)
- Internal ops: `k8s/batch-cluster/ensure-batch-databricks-external-location.sql`
