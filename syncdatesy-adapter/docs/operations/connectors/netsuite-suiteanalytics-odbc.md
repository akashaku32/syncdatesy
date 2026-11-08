# NetSuite SuiteAnalytics Connect (ODBC)

NetSuite is supported as a source via two **different** transports:

| UI method | Runtime | ODBC required |
|-----------|---------|---------------|
| **SuiteQL (REST API)** | HTTPS + OAuth 2.0 M2M certificate | No |
| **SuiteAnalytics Connect (ODBC)** | `pyodbc` + Oracle NetSuite ODBC driver → `NetSuite2.com` | Yes |

Listing, authorization tests, and migrations for **SuiteAnalytics** run in:

- [`migration-function`](../../../k8s/deployments/migration-function.yaml) (HTTP auth)
- **Celery job workers** using the **fast-api** image (update schema + migrate)

Both images must be built with the same ODBC stack when customers use Connect.

## 1. Obtain the driver (one-time)

1. In NetSuite: **Settings** → **Set Up Analytics Connect** (driver download).
2. Download the **Linux 64-bit** ODBC bundle (`.tgz` / `.tar.gz`).
3. Do **not** commit the bundle to git (Oracle license).

Upload to private GCS, for example:

`gs://YOUR-BUCKET/netsuite-odbc/NetSuiteODBC_linux64.tgz`

Grant the Cloud Build service account **Storage Object Viewer** on that object.

## 2. Cloud Build (QA or batch)

Substitutions (defaults keep ODBC **off**):

- `_INSTALL_NETSUITE_ODBC` — `0` or `1`
- `_NETSUITE_ODBC_GCS_URI` — GCS path to the tarball when `1`

**QA migration-function:**

```bash
gcloud builds submit --config=backend/cloudbuild/qa/migration-function.yaml backend/ \
  --substitutions=_INSTALL_NETSUITE_ODBC=1,_NETSUITE_ODBC_GCS_URI=gs://YOUR-BUCKET/netsuite-odbc/NetSuiteODBC_linux64.tgz
```

**QA fast-api (Celery / listing / migrate):**

```bash
gcloud builds submit --config=backend/cloudbuild/qa/fastapi.yaml backend/ \
  --substitutions=_INSTALL_NETSUITE_ODBC=1,_NETSUITE_ODBC_GCS_URI=gs://YOUR-BUCKET/netsuite-odbc/NetSuiteODBC_linux64.tgz
```

Batch cluster uses [`backend/cloudbuild/batch/migration-function.yaml`](../../../backend/cloudbuild/batch/migration-function.yaml) and [`backend/cloudbuild/batch/fastapi.yaml`](../../../backend/cloudbuild/batch/fastapi.yaml) with the same substitutions.

## 3. Local Docker build

```bash
mkdir -p backend/vendor/netsuite-odbc
cp /path/to/NetSuiteODBC_linux64.tgz backend/vendor/netsuite-odbc/

docker build -f backend/migration-function/Dockerfile backend/ \
  --build-arg INSTALL_NETSUITE_ODBC=1 \
  -t datasyncher-migration-function:local-odbc
```

Same `--build-arg` for `backend/fast-api/Dockerfile`.

## 4. Kubernetes

Optional overrides: [`k8s/configmaps/netsuite-odbc-env.yaml`](../../../k8s/configmaps/netsuite-odbc-env.yaml) (referenced by `migration-function` deployment).

Image defaults (when ODBC installed at build):

- `LD_LIBRARY_PATH=/opt/netsuite/odbcclient/lib64`
- `NETSUITE_ODBC_TRUSTSTORE=/opt/netsuite/odbcclient/cert/ca4.cer`
- `NETSUITE_ODBC_DRIVER={NetSuite ODBC Drivers}`
- `NETSUITE_ODBC_INSTALLED=1`

Override driver name if `odbcinst -q -d` shows a different label after install.

## 5. Verify

**In a running migration-function pod:**

```bash
kubectl exec -it deploy/migration-function -- odbcinst -q -d
kubectl exec -it deploy/migration-function -- curl -s localhost:8001/healthz/netsuite-odbc
```

- `/healthz` — always lightweight (probes).
- `/healthz/netsuite-odbc` — returns **503** if the image was built with ODBC enabled but the driver is missing.

**In the UI:** create a NetSuite source with **SuiteAnalytics Connect (ODBC)**, pick **Connect authentication** (TBA, OAuth 2.0 M2M, or email/password), run connection test and update schema.

## 6. Install script reference

Shared installer: [`backend/scripts/install-netsuite-odbc.sh`](../../../backend/scripts/install-netsuite-odbc.sh)

If `install.sh` flags differ for your NetSuite bundle version, adjust the script after inspecting the tarball layout.
