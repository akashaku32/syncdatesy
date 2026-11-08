# Databricks connector documentation

Guides for **Dynamics F&O → Databricks**, **Salesforce → Databricks**, staging, cloud permissions, and load paths.

## Start here

| Doc | When to read |
|-----|--------------|
| **[datasyncher-platform-staging-and-permissions.md](./datasyncher-platform-staging-and-permissions.md)** | **Platform buckets/S3/containers**, IAM details, what auto-provisions vs what admins create — **read this for zero-manual setup** |
| **[databricks-dynamics-load-scenarios.md](./databricks-dynamics-load-scenarios.md)** | **Main reference** — Spark vs SQL warehouse, same-cloud rule, permissions (AWS S3, Azure container, GCS), edge cases |
| **[databricks-managed-volume-staging-setup.md](./databricks-managed-volume-staging-setup.md)** | Default SQL path — Unity Catalog managed volume (any cloud, PAT only) |
| **[databricks-client-cloud-staging-setup.md](./databricks-client-cloud-staging-setup.md)** | Client-owned staging — **AWS S3**, **Azure ADLS container**, client GCS + IAM / UC setup |
| **[databricks-gcp-unity-catalog-setup.md](./databricks-gcp-unity-catalog-setup.md)** | Datasyncher-managed GCS + external location (legacy GCP SQL staging) |
| **[dimensionfocusbalance-spark-bulk-load.md](./dimensionfocusbalance-spark-bulk-load.md)** | Example Spark bulk load for one large Dynamics table |

## Cloud permission cheat sheet

| Databricks workspace | Staging storage | Permissions doc section |
|---------------------|-----------------|-------------------------|
| **AWS** | S3 prefix | [load scenarios — AWS](./databricks-dynamics-load-scenarios.md) · [client S3 setup](./databricks-client-cloud-staging-setup.md#option-c--aws-databricks--client-s3) |
| **Azure** | ADLS / Blob container | [load scenarios — Azure](./databricks-dynamics-load-scenarios.md) · [client ADLS setup](./databricks-client-cloud-staging-setup.md#option-b--azure-databricks--client-adls--blob) |
| **GCP** | GCS bucket | [load scenarios — GCP](./databricks-dynamics-load-scenarios.md) · [GCP UC setup](./databricks-gcp-unity-catalog-setup.md) |
| **Any (SQL incremental)** | UC managed volume | [managed volume setup](./databricks-managed-volume-staging-setup.md) |

## Implementation code

| Component | Path |
|-----------|------|
| Dynamics orchestrator | `backend/migration-function/services/dynamics/as_source/to_databricks.py` |
| Load helpers | `backend/migration-function/services/databricks/load_helpers.py` |
| Spark job client | `backend/migration-function/services/databricks/spark_job_client.py` |
