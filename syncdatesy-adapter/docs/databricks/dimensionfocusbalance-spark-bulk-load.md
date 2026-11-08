# dimensionfocusbalance — Spark Bulk Load Plan

One-time Databricks Spark bulk load for the `dimensionfocusbalance` table (Pipeline 17 / `dimesnionfocus_pipeline`), replacing the multi-day Celery pod + deferred-staging path.

---

## Problem summary

| Item | Current state |
|------|----------------|
| Pipeline | `dimesnionfocus_pipeline` (id **17**) |
| Batch | `dimesninfocusbalance_batch` (node **151**) |
| Table | `dimensionfocusbalance` |
| Connection | **24** (`databricks_connection_new`) |
| Latest run | **1306** — running ~20+ hours |
| Staging table | `DS_DIMENSIONFOCUSBALANCE_STAGING_20260827_092500` — **~60M rows** (from failed run 1190) |
| Target | `test.LANDING.dimensionfocusbalance` |
| Throughput | ~100k rows / ~5 min (~20k rows/min) due to staging bloat + per-chunk SQL |

**Root cause:** Deferred merge + resuming a 60M-row staging table makes every chunk pay `id_overlap`, `DELETE`, and `COPY INTO` against 60M rows. Pod flow is sequential (1 worker × 100k chunks).

---

## Recommended approach

**Hybrid model:**

1. **One-time:** Databricks Spark Job reads all Dataverse export CSVs in parallel and writes Delta.
2. **Ongoing:** Keep existing Celery pod flow for incremental sync (small delta folders only).

```
ONE TIME (Spark bulk):
  Azure Dataverse CSV blobs → Databricks Job cluster (parallel) → Delta base table

ONGOING (existing pods):
  New export folders only → Celery flow → small staging → merge
```

---

## Architecture comparison

### Current (pod path)

```text
Azure Dataverse CSV blobs
    ↓  GKE flow pod (adlfs, 100k pandas chunks)
Databricks managed volume (parquet per chunk)
    ↓  SQL warehouse: COPY INTO, DELETE overlap, COUNT
Staging Delta (~60M rows)
    ↓  deferred MERGE (not reached)
Base: test.LANDING.dimensionfocusbalance
```

### Target (Spark bulk)

```text
Azure Dataverse CSV blobs
    ↓  Spark parallel read (all CSVs at once)
Databricks Job cluster (auto-start / auto-terminate)
    ↓  transform + dedupe + write
Base: test.LANDING.dimensionfocusbalance
```

---

## Prerequisites (one-time setup)

### 1. Confirm Databricks workspace placement

| Workspace location | Blob read strategy |
|--------------------|-------------------|
| **Azure** (same region as Dataverse storage) | Direct `abfss://` read — preferred |
| **GCP / other** | One-time copy of export blobs to storage near Databricks, then Spark from there |

### 2. Unity Catalog — Azure external location

Allow Databricks to read Dataverse export container directly.

- **Storage credential** (UC): Databricks-managed or customer Azure SP
- **External location** pointing at Dataverse export prefix, e.g.:
  - `abfss://<container>@<storage-account>.dfs.core.windows.net/<base-folder>/`
- **GRANT READ FILES** to job cluster / service principal

Reference: [databricks-client-cloud-staging-setup.md](./databricks-client-cloud-staging-setup.md)

### 3. Job cluster configuration

| Setting | Recommended |
|---------|-------------|
| Cluster type | Job cluster (not all-purpose) |
| Workers | 4–8 (autoscale) |
| `autoterminate_minutes` | 10–15 after job ends |
| Runtime | DBR with Delta + Unity Catalog |
| Access | READ on external location; WRITE on `test.LANDING` |

### 4. Source paths (from live logs)

- Storage account prefix: `dataverse-atiprod-unq51b5dc85a07aee118bc56045bd003`
- Table path pattern: `<date_dir>/dimensionfocusbalance/*.csv`
- Example date dirs: `2026-02-12T03.59.09Z`, `2026-02-12T04.59.09Z`
- Schema JSON per folder: `<date_dir>/dimensionfocusbalance/schema.json` (if used)

### 5. Datasyncher metadata (after bulk job succeeds)

Update so incremental pod sync resumes correctly:

- `source_table_selection.last_synced` / `last_checked_datedirectory` for `dimensionfocusbalance` on connection 24
- `staging_table_details` — clear or set `load_phase` to completed (do not resume 60M staging)
- `migration_record` / pipeline run — mark table completed for run 1306 (or cancel and close cleanly)

---

## Implementation steps

### Phase 0 — Stop the bleeding (ops, no deploy)

1. Cancel or fail pipeline run **1306** (after current chunk if needed).
2. In Databricks, **drop** bloated staging:
   ```sql
   DROP TABLE IF EXISTS `test`.`LANDING`.`DS_DIMENSIONFOCUSBALANCE_STAGING_20260827_092500`;
   ```
3. Clear resume metadata in `staging_table_details` for connection 24 / `dimensionfocusbalance`.
4. Scale down / tear down flow fleet for run 1306 if still running.

### Phase 1 — Pilot notebook (one date folder)

1. Create Databricks notebook: `bulk_load_dimensionfocusbalance_pilot`.
2. Read a single folder first (e.g. `2026-02-12T04.59.09Z`).
3. Validate:
   - Column names / types match `test.LANDING.dimensionfocusbalance`
   - Dedupe on `ID` + latest `SINK_MODIFIED_ON`
   - Row counts vs expected (~1.7M per large folder from logs)
4. Write to a **pilot** table: `test.LANDING.dimensionfocusbalance_pilot`.

### Phase 2 — Full bulk job

1. List all `date_dir` folders under the Dataverse export path for `dimensionfocusbalance`.
2. Spark read all `*.csv` under those paths (wildcard or explicit list).
3. Apply same transforms as Dynamics → Databricks pod path:
   - Uppercase columns
   - `_SPECT_SYNCED`, `_SPECT_DELETED` if required
   - Dedupe on `ID` / `SINK_MODIFIED_ON`
   - Handle `IS_DELETE` if present
4. **Initial load:** `MERGE` or `overwrite` into `test.LANDING.dimensionfocusbalance`.
5. Run as **Databricks Job** (scheduled once or API-triggered).
6. Cluster terminates when done.

### Phase 3 — Hand back to Datasyncher

1. Update watermarks / checkpoints to latest processed `date_dir`.
2. Mark migration / pipeline node completed in Datasyncher DB.
3. Re-enable `dimesnionfocus_pipeline` for **incremental only** (pod flow).
4. Monitor first incremental run — should be small and fast.

---

## Example Spark notebook (starter)

```python
# Widgets / job params
catalog = "test"
schema = "LANDING"
table = "dimensionfocusbalance"
blob_base = "abfss://<container>@<account>.dfs.core.windows.net/<base-folder>"
date_dirs = ["2026-02-12T03.59.09Z", "2026-02-12T04.59.09Z"]  # expand to full list

paths = [f"{blob_base}/{d}/{table}/*.csv" for d in date_dirs]

df = spark.read.option("header", True).csv(paths)

# Normalize column names (match pod path)
for c in df.columns:
    df = df.withColumnRenamed(c, c.upper())

# Dedupe: latest SINK_MODIFIED_ON per ID
from pyspark.sql import functions as F
from pyspark.sql.window import Window

w = Window.partitionBy("ID").orderBy(F.col("SINK_MODIFIED_ON").desc_nulls_last())
df = (
    df.withColumn("_rn", F.row_number().over(w))
    .filter(F.col("_rn") == 1)
    .drop("_rn")
)

target = f"{catalog}.{schema}.{table}"

# Initial historical load
df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)

# OR incremental-style merge (if base already has data):
# from delta.tables import DeltaTable
# DeltaTable.forName(spark, target).alias("t").merge(
#     df.alias("s"), "t.ID = s.ID"
# ).whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
```

Adjust paths, credentials, and transforms after pilot validation.

---

## Cost estimate (this table)

| Approach | Duration | Rough cost |
|----------|----------|------------|
| **Current pod + SQL warehouse** (runs 1172, 1190, 1306) | 4+ days | **$150–400+** (GKE pod + SQL warehouse on 60M staging + egress) |
| **Spark bulk job** (one-time) | 2–6 hours | **$60–200** (job cluster DBUs only) |

**Conclusion:** Spark bulk is **not** more expensive for this table — it is cheaper and faster than continuing the current path.

---

## When to use which path

| Scenario | Use |
|----------|-----|
| Full historical backfill, 10M+ rows, multi-day pod runs | **Spark bulk** |
| Daily / hourly incremental, small delta folders | **Celery pod flow** (existing) |
| Table stuck on 60M+ resumed staging | **Drop staging + Spark bulk OR fresh pod start** |

---

## Optional product / code follow-ups (later)

These are not required for the one-time Spark load but improve future monster tables:

| Change | Benefit |
|--------|---------|
| Per date-folder merge + staging truncate | **Addressed** by per-chunk MERGE + TRUNCATE in ongoing pod sync |
| Skip / cap `should_resume_staging` when `staging_records_count` > threshold | Avoid inheriting 60M staging |
| Replace batched `IN` overlap with temp-table JOIN | Faster per-chunk overlap checks |
| `bulk_load_mode` flag on table/batch | Trigger Databricks Job instead of pod for configured tables |

---

## Checklist

- [ ] Confirm Databricks workspace region vs Azure Dataverse storage region
- [ ] Create UC storage credential + external location for export blobs
- [ ] Create job cluster policy (4–8 workers, autoterminate)
- [ ] Stop run 1306 and drop 60M staging table
- [ ] Run pilot notebook on one `date_dir`
- [ ] Run full bulk job on all `date_dir` folders
- [ ] Validate row counts and sample data in `test.LANDING.dimensionfocusbalance`
- [ ] Update Datasyncher watermarks + mark table completed
- [ ] Re-enable pipeline for incremental pod sync only
- [ ] Document job ID / notebook path for ops rerun if needed

---

## References

- [databricks-client-cloud-staging-setup.md](./databricks-client-cloud-staging-setup.md) — Azure external location pattern
- [databricks-managed-volume-staging-setup.md](./databricks-managed-volume-staging-setup.md) — current pod staging (managed volume)
- Dynamics → Databricks pod implementation: `backend/migration-function/services/dynamics/as_source/to_databricks.py`
- Pipeline 17 setup: `scripts/setup_conn24_pipeline.py` (`dimesninfocusbalance_batch`)
