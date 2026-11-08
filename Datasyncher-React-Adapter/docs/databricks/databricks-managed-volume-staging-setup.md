# Databricks — Unity Catalog Managed Volume staging

Datasyncher can stage replication chunk CSVs into a **Unity Catalog managed volume**
in your Databricks workspace, then run **COPY INTO → MERGE** using only the same
**PAT / OAuth token** already used for the SQL warehouse.

This path is **cloud-agnostic**: it works the same on **AWS, Azure, and GCP**
Databricks workspaces. No separate cloud credential (GCS SA, IAM role, or ADLS
identity) is required for staging.

**Related docs**

| Model | When to use | Doc |
|--------|-------------|-----|
| **Managed volume** (this file) — recommended default | Any cloud; simplest client setup | This guide |
| **Datasyncher-managed GCS + external location** | Existing GCP setups | [databricks-gcp-unity-catalog-setup.md](./databricks-gcp-unity-catalog-setup.md) |
| **Client-cloud object storage** (future) | Azure/AWS external locations | [databricks-client-cloud-staging-setup.md](./databricks-client-cloud-staging-setup.md) |

---

## Who owns what

| Party | Owns |
|--------|------|
| **Your Databricks admin** | Unity Catalog volume + `READ VOLUME` / `WRITE VOLUME` grants |
| **Connection form (you fill)** | Catalog, schema (destination schema), **volume name**, staging method |
| **Datasyncher** | Chunk CSV upload via Files API, COPY INTO, MERGE, volume file cleanup |

---

## One-time setup (Databricks admin)

**Prerequisites**

- Unity Catalog enabled
- SQL warehouse already configured for Datasyncher (hostname, HTTP path, PAT, catalog)
- Metastore / catalog privileges to create a managed volume

**Task 1 — Create a managed volume**

```sql
CREATE VOLUME IF NOT EXISTS `your_catalog`.`your_schema`.`datasyncher_stage`;
```

Or in Catalog Explorer → catalog → schema → **Create** → **Volume** (Managed).

**Task 2 — Grant volume privileges to the warehouse principal**

```sql
GRANT READ VOLUME, WRITE VOLUME
ON VOLUME `your_catalog`.`your_schema`.`datasyncher_stage`
TO `account users`;
-- Or your warehouse group / service principal
```

**Task 3 — Optional smoke test**

```sql
-- After the first Datasyncher sync uploads a CSV:
LIST '/Volumes/your_catalog/your_schema/datasyncher_stage/';
```

---

## Datasyncher connection fields

The Databricks **destination** form only needs core connection fields:

| Field | Required | Notes |
|-------|----------|--------|
| Server Hostname / HTTP Path / Access Token | Yes | SQL warehouse auth |
| Catalog | Yes | UC catalog for tables **and** staging volume |
| Destination schema | Yes | Connection `dst_schema` — volume lives here too |

**Automatic backend behavior (no form fields):**

- **Salesforce → Databricks:** always SQL warehouse with managed-volume staging.
- **Dynamics → Databricks initial load:** Spark Job (ADLS direct read or GCS land, inferred from workspace hostname).
- **Dynamics → Databricks incremental:** SQL warehouse COPY/MERGE.
- **Default staging volume:** `datasyncher_stage` (auto-created when missing).

Advanced overrides (legacy `load_engine`, Spark job IDs, GCS/ADLS external location) remain in saved destination config or platform env vars for ops — not shown on the destination form.

---

## What happens on each sync

1. Datasyncher uploads chunk CSVs to  
   `/Volumes/{catalog}/{schema}/{volume}/temp_data_dynamics_f&o_{table}_{connection_id}.csv`  
   via `PUT /api/2.0/fs/files/...` (overwrite-safe; same naming as the GCS path)
2. **COPY INTO** staging Delta table from that `/Volumes/...` path
3. **MERGE** into the target table; truncate staging
4. Delete the staged CSV from the volume (best-effort cleanup)

Files larger than ~4 GiB fall back to Databricks SDK multipart upload (API hard limit is 5 GiB per single PUT).

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `volume does not exist` / `volume_not_found` | Volume name/catalog/schema mismatch; create the managed volume |
| `insufficient privileges` / `WRITE VOLUME` | Grant READ VOLUME + WRITE VOLUME to the token principal |
| Upload timeout | Network or oversized chunk; check `DATABRICKS_VOLUME_UPLOAD_TIMEOUT_SECONDS` |
| COPY INTO failure on `/Volumes/...` | Warehouse missing READ VOLUME, or path not under the volume |

---

## Switching from GCS external location

Existing GCP connections that use Datasyncher-managed GCS keep working when
`staging_method=external_location` (and GCS credential fields) are present on the
saved destination config.

To move a connection to managed volumes:

1. Create the volume + grants (above)
2. Remove legacy GCS staging fields from destination config (or leave unset on new destinations — backend defaults to managed volume + `datasyncher_stage`)
3. Clear GCS UC fields if desired (no longer used for that connection)
