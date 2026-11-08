# QA Kubernetes API endpoints

Reference for the **QA GKE** environment (`qa-kubernetes.datasyncher.com`).

| Item | Value |
|------|--------|
| Cluster | `datasyncher-cluster` (GKE, `us-central1`) |
| Project | `datasyncher-kubernetes` |
| Namespace | `default` |
| Public base URL | `https://qa-kubernetes.datasyncher.com` |

## Ingress routing

| Public path | Backend service | Port | Purpose |
|-------------|-----------------|------|---------|
| `/` | `frontend-svc` | 80 | React UI |
| `/api/v1/` | `django-svc` | 8000 | Django REST API |
| `/api/` | `fastapi-svc` | 9090 | Celery orchestration / migration yield |
| `/ws/` | `django-svc` | 8000 | WebSockets (Django Channels) |
| `/admin/` | `django-svc` | 8000 | Django admin site |
| `/api-new/` | `migration-function-svc` | 8001 | Connector auth, listing, migrate (Traefik rewrite) |

**In-cluster only** (not exposed via public ingress):

| Service | Port | Purpose |
|---------|------|---------|
| `migration-function:8001` | 8001 | Internal connector HTTP (used by FastAPI/Celery) |
| `celery-worker-control-metrics:9100` | 9100 | Prometheus metrics for Celery job workers |

---

## QA platform admin account

Created with `scripts/create-qa-platform-admin.ps1` for ops and connectivity testing.

| Field | Value |
|-------|--------|
| Email | `qa-platform-admin@datasyncher.com` |
| Password | `QaK8sAdmin!2026` |
| `is_superuser` | `true` (required for `/api/v1/admin/ops/*`) |

### Login (JWT)

```http
POST /api/v1/authentication/
Content-Type: application/json

{
  "username": "qa-platform-admin@datasyncher.com",
  "password": "QaK8sAdmin!2026"
}
```

Response includes `access_token` and `refresh_token`. Use:

```http
Authorization: Bearer <access_token>
```

---

## Quick health / smoke tests

Replace `TOKEN` with the JWT from login.

```bash
BASE="https://qa-kubernetes.datasyncher.com"

# Django home (authenticated)
curl -sS -H "Authorization: Bearer TOKEN" "$BASE/api/v1/home/"

# Celery connectivity (django -> redis -> celery workers)
curl -sS -H "Authorization: Bearer TOKEN" "$BASE/api/v1/test-celery-connection/"

# Platform ops overview (superuser only)
curl -sS -H "Authorization: Bearer TOKEN" "$BASE/api/v1/admin/ops/overview/"

# Celery workers snapshot (superuser only)
curl -sS -H "Authorization: Bearer TOKEN" "$BASE/api/v1/admin/ops/workers/"

# Migration-function health (public root)
curl -sS "$BASE/api-new/"

# FastAPI metrics (404 if CELERY_PROMETHEUS_METRICS_ENABLED=false)
curl -sS "$BASE/api/metrics"
```

---

## Django REST API (`/api/v1/`)

All paths are relative to `https://qa-kubernetes.datasyncher.com/api/v1/`.

### Authentication & account

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `authentication/` | Public | Sign in (`username` + `password`) or sign up (`company_name` + fields) |
| POST | `verify-email/` | Public | Verify OTP after signup |
| POST | `resend-otp/` | Public | Resend email OTP |
| POST | `password-reset/` | Public | Request password reset |
| POST | `password-reset-confirm/` | Public | Confirm password reset |
| GET | `user/info/` | User | Current user info |
| GET | `user/profile/` | User | User profile |
| GET | `profile-details/` | User | Extended profile |
| GET/PATCH | `account/profile/` | User | Account profile |
| PATCH | `account/profile/update/` | User | Update account profile |
| GET | `account/countries/` | User | Country list |
| GET/PATCH | `account/communication-support/` | User | Communication preferences |
| GET/POST | `account/email-groups/` | User | Email notification groups |
| GET/PATCH/DELETE | `account/email-groups/<group_id>/` | User | Email group detail |

### Connections & sources

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `home/` | User | Connection list (dashboard) |
| GET | `source/all/` | User | Available source connectors |
| GET | `source/schema/<source_name>/` | User | Source config schema |
| GET | `source/detail/<src_config_id>/` | User | Source config detail |
| GET | `source-config/<source_name>/` | User | Source connection form |
| GET | `source-config/<source_name>/<connection_id>/` | User | Source connection form (edit) |
| POST | `source-config/<source_name>/create/` | User | Create source + connection |
| PUT/PATCH | `source-config/<source_name>/<connection_id>/update/` | User | Update source + connection |
| POST | `source/<connection_name>/tables/` | User | Start table listing |
| POST | `connection/<connection_id>/test/` | User | Test source connection |
| GET | `get_table_status/?connection_id=` | User | Table listing status |
| GET | `schema/<connection_id>/` | User | Connection schema |
| GET | `connection/schema-info/<connection_id>/` | User | Schema metadata |
| GET | `connection/setup/<connection_id>/` | User | Connection setup detail |
| GET | `connection/<connection_id>/usage/` | User | Connection usage |
| GET | `connection/<connection_id>/activity/` | User | Activity logs |
| GET | `connection/<connection_id>/session/<session_id>/` | User | Migration session detail |
| GET | `connection/<connection_id>/log/<log_id>/` | User | Log detail |
| POST | `connection/<connection_id>/toggle/` | User | Enable/disable connection |
| DELETE | `connection/<connection_id>/delete/` | User | Delete connection |
| POST | `connection/<connection_id>/discard/` | User | Discard schema changes |
| GET | `connection/migration_status/<migration_session_id>/` | User | Migration status |
| POST | `connection/<connection_id>/fetch-tables/` | User | Fetch source tables |
| GET | `connection/<connection_id>/tables/` | User | List source tables |
| GET | `connection/<connection_id>/selected-tables/` | User | Selected tables |
| POST | `connection/<connection_id>/update-selection/` | User | Update table selection |
| GET | `connection/<connection_id>/table-selection-history/` | User | Selection audit history |
| POST | `connection/<connection_id>/refresh-schema/` | User | Refresh schema + migrate |
| POST | `connection/<connection_id>/update-sync-frequency/` | User | Update sync schedule |
| POST | `connection/update-timestamp/` | User | Update delta timestamp field |
| GET | `connection/<connection_id>/mappings/` | User | Table mappings list |
| POST | `connection/mappings/save/` | User | Save table mappings |
| GET/PATCH | `connection/<connection_id>/update-schema-status/` | User | Update schema job status |
| GET/PATCH | `connection/<connection_id>/tables/<table_name>/fields/` | User | Table field details |
| PATCH | `connection/<connection_id>/tables/<table_name>/table-fields/` | User | SFTP table fields |
| PATCH | `connection/<connection_id>/tables/<table_name>/email-groups/` | User | Table email groups |
| PATCH | `connection/<connection_id>/tables/<table_name>/export-settings/` | User | Export settings |
| POST | `reload-single-table/` | User | Reload one table |
| POST | `refresh-delta-table/` | User | Refresh delta table |
| GET | `full-connections/` | User | Full connection list |
| GET | `full-connections/<connection_id>/` | User | Full connection detail |

### Destinations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `destinations/` | User | Destination config list |
| GET | `destinations/all/` | User | Available destination types |
| GET | `destinations/schema/<destination_name>/` | User | Destination form schema |
| GET | `destinations/config/<destination_name>/` | User | Destination config form |
| GET | `destinations/config/<destination_name>/<dst_config_id>/` | User | Edit form |
| GET | `destinations/status/<dst_config_id>/` | User | Config status |
| GET | `destinations/detail/<dst_config_id>/` | User | Config detail |
| POST | `destinations/select-destination/` | User | Select active destination |
| POST | `destinations/create/` | User | Create destination (auth via migration-function) |
| PUT/PATCH | `destinations/update/<dst_config_id>/` | User | Update destination |
| POST | `destinations/test/<dst_config_id>/` | User | Test destination connection |
| DELETE | `destinations/delete/<dst_config_id>/` | User | Delete destination |
| PATCH | `change-destination-name/<dst_config_id>/` | User | Rename destination |
| GET | `destinations/<destination_name>/export-config/` | User | Export config |
| POST | `dest-check-key-pair/` | User | Check Snowflake key pair (dest) |
| POST | `src-check-key-pair/` | User | Check Snowflake key pair (source) |

### OAuth callbacks (browser redirects)

| Method | Path | Description |
|--------|------|-------------|
| GET | `salesforce/callback/` | Salesforce destination OAuth |
| GET | `salesforcesandbox/callback/` | Salesforce Sandbox destination OAuth |
| GET | `source/salesforce/callback/` | Salesforce source OAuth |
| GET | `source/salesforcesandbox/callback/` | Salesforce Sandbox source OAuth |
| GET | `google-drive/callback/` | Google Drive destination OAuth |
| GET | `source/googlereviews/callback/` | Google Reviews source OAuth |
| GET | `flash-messages/` | OAuth flash messages |

### Users, RBAC, billing, support

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET/POST | `users/` | Admin | List/create users |
| GET/PUT/PATCH | `users/<user_id>/` | Admin | User detail |
| GET/POST | `rbac/users/` | Admin | RBAC user management |
| GET/PATCH | `rbac/users/<user_id>/` | Admin | RBAC user update |
| GET | `rbac/roles/` | User | Role list |
| GET | `roles/` | User | Roles |
| GET | `company-details/<pk>/` | User | Company detail |
| GET | `billing/chart/<cmp_id>/` | Billing | Usage chart |
| GET | `billing/summary/<cmp_id>/` | Billing | Billing summary |
| GET | `billing/usage/<cmp_id>/` | Billing | Usage detail |
| GET | `billing/<cmp_id>/monthly-usage/` | Billing | Monthly usage |
| GET | `billing/<cmp_id>/annual-usage/` | Billing | Annual usage |
| GET/POST | `billing/plan/` | Billing | Plan info |
| GET/POST | `billing/plan/<cmp_id>/` | Billing | Company plan |
| GET/POST | `billing/payment/` | Billing | Payment methods |
| GET/POST | `billing/payment/<cmp_id>/` | Billing | Company payment |
| POST | `billing/setup-intent/` | Billing | Stripe setup intent |
| POST | `pay/<invoice_id>/` | Billing | Stripe payment intent |
| GET | `download-invoice/<id>/` | Billing | Download invoice |
| GET/POST | `support-tickets/` | User | Support tickets |
| GET | `support-tickets/choices/` | User | Ticket field choices |
| GET/PATCH | `support-tickets-detail/<ticket_id>/` | User | Ticket detail |
| POST | `support-tickets-detail/<ticket_id>/replies/` | User | Ticket replies |

### S3 helpers

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `s3/suggest-table-name/` | User | Suggest S3 table name |
| POST | `s3/list-files/` | User | List S3 files |
| POST | `s3/preview-pattern/` | User | Preview file pattern |
| POST | `s3/suggest-primary-keys/` | User | Suggest primary keys |

### Celery & platform ops

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `test-celery-connection/` | User | Test Django → Redis → Celery worker connectivity |
| GET | `admin/ops/overview/` | **Superuser** | Cluster ops overview (jobs, workers, Redis) |
| GET | `admin/ops/jobs/` | **Superuser** | Active/recent migration jobs (`?company_id=`, `?plan=`, `?limit=`) |
| GET | `admin/ops/jobs/<job_name>/` | **Superuser** | Single job detail (Celery task state, pods) |
| GET | `admin/ops/workers/` | **Superuser** | Celery worker pods, queues, KEDA state |

### Internal (cluster callbacks — no auth)

| Method | Path | Description |
|--------|------|-------------|
| POST | `internal/migration-status-push/<migration_session_id>/` | Push migration WS update |
| POST | `internal/connection-table-status-push/<connection_id>/` | Push table status WS |
| POST | `internal/connection-activity-log-push/<connection_id>/` | Push activity log WS |
| POST | `internal/update-schema-status-push/<connection_id>/` | Push schema status WS |
| POST | `internal/company-connections-status-push/<cmp_id>/` | Push company connections WS |
| POST | `internal/admin-ops-push/` | Push admin ops WebSocket snapshot |

### Other Django routes

| Method | Path | Description |
|--------|------|-------------|
| * | `/admin/` | Django admin UI |
| POST | `/api/webhook/` | Stripe webhook |

---

## Support Admin API (`/api/v1/support-admin/`)

Separate JWT auth for internal support staff app.

| Method | Path | Description |
|--------|------|-------------|
| POST | `auth/register/` | Register support admin |
| POST | `auth/login/` | Login |
| POST | `auth/forgot-password/` | Forgot password |
| POST | `auth/reset-password/` | Reset password |
| POST | `auth/refresh/` | Refresh token |
| GET | `auth/me/` | Current admin profile |
| GET | `companies/` | List companies |
| GET | `companies/<cmp_id>/connections/` | Company connections |
| POST | `companies/<cmp_id>/connections/<connection_id>/test/` | Test connection |
| GET | `companies/<cmp_id>/connections/<connection_id>/selected-tables/` | Selected tables |
| POST | `companies/<cmp_id>/connections/<connection_id>/add-offset-hours/` | Add sync offset |
| POST | `companies/<cmp_id>/connections/<connection_id>/subtract-offset-hours/` | Subtract sync offset |
| GET | `companies/<cmp_id>/billing/` | Billing |
| GET | `companies/<cmp_id>/invoices/` | Invoices |
| GET | `companies/<cmp_id>/billing-summary/` | Billing summary |
| GET | `companies/<cmp_id>/destinations/` | Destinations |
| GET | `companies/<cmp_id>/destinations/<dst_config_id>/` | Destination detail |
| POST | `companies/<cmp_id>/destinations/<dst_config_id>/test/` | Test destination |
| GET | `tickets/` | Support tickets |
| GET/PATCH | `tickets/<ticket_id>/` | Ticket detail |
| POST | `tickets/<ticket_id>/replies/` | Ticket replies |

---

## FastAPI (`/api/`)

Base: `https://qa-kubernetes.datasyncher.com/api/`

Celery orchestration layer. Most routes enqueue work to the **migration** Celery queue and return a task id / yield stream.

| Method | Path | Description |
|--------|------|-------------|
| GET | `metrics` | Prometheus metrics (when `CELERY_PROMETHEUS_METRICS_ENABLED=true`) |

### Listing (enqueue to migration-function)

| Method | Path | Forwards to |
|--------|------|-------------|
| POST | `MicrosoftDynamics365_FO-listing-tables` | `/MicrosoftDynamics365_FO-listing-tables` |
| POST | `Snowflake-listing-tables` | `/Snowflake-listing-tables` |
| POST | `Snowflake-listing-tables-readonly` | `/Snowflake-listing-tables-readonly` |
| POST | `Salesforce-listing-tables` | `/Salesforce-listing-tables` |
| POST | `SalesforceSandbox-listing-tables` | `/SalesforceSandbox-listing-tables` |
| POST | `GoogleReviews-listing-tables` | `/GoogleReviews-listing-tables` |
| POST | `AmazonS3-listing-tables` | `/AmazonS3-listing-tables` |
| POST | `AmazonS3-listing-files` | `/AmazonS3-listing-files` |

### Migration yield (orchestrate_migration Celery task)

| Method | Path | Connector pair |
|--------|------|----------------|
| POST | `Snowflake-MicrosoftDynamics-migration-yield` | Snowflake → Dynamics |
| POST | `Snowflake-MicrosoftDynamics365_FO-migration-yield` | Snowflake → Dynamics 365 F&O |
| POST | `Salesforce-Snowflake-migration-yield` | Salesforce → Snowflake |
| POST | `Snowflake-Salesforce-migration-yield` | Snowflake → Salesforce |
| POST | `SalesforceSandbox-Snowflake-migration-yield` | Sandbox → Snowflake |
| POST | `Snowflake-SalesforceSandbox-migration-yield` | Snowflake → Sandbox |
| POST | `Snowflake-Snowflake-migration-yield` | Snowflake → Snowflake |
| POST | `Snowflake-GoogleReviews-migration-yield` | Snowflake → Google Reviews |
| POST | `Snowflake-AmazonS3-migration-yield` | Snowflake → Amazon S3 |
| POST | `SFTP-Snowflake-migration-yield` | SFTP → Snowflake |
| POST | `SharePoint-Snowflake-migration-yield` | SharePoint → Snowflake |
| POST | `AmazonS3-Snowflake-migration-yield` | Amazon S3 → Snowflake |
| POST | `AzureDataLakeStorage-Snowflake-migration-yield` | ADLS → Snowflake |
| POST | `GoogleDrive-Snowflake-migration-yield` | Google Drive → Snowflake |

---

## Migration-function (`/api-new/`)

Base: `https://qa-kubernetes.datasyncher.com/api-new/`

Direct connector HTTP (also called in-cluster at `http://migration-function:8001`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check (`FastAPI App is Running`) |
| GET | `get-details` | Snowflake details schema (duplicate routes from listing routers) |

### Authorization

| Method | Path | Connector |
|--------|------|-----------|
| POST | `Snowflake-Authorization` | Snowflake |
| POST | `MicrosoftDynamics365_FO-Authorization` | Dynamics 365 F&O |
| GET/POST | `Salesforce-Authorization` | Salesforce (destination) |
| GET | `Source-Salesforce-Authorization` | Salesforce (source) |
| GET/POST | `SalesforceSandbox-Authorization` | Salesforce Sandbox (dest) |
| GET | `Source-SalesforceSandbox-Authorization` | Salesforce Sandbox (source) |
| POST | `GoogleReviews-Authorization` | Google Reviews |
| GET | `Source-GoogleReviews-Authorization` | Google Reviews (source) |
| POST | `AmazonS3-Authorization` | Amazon S3 |
| POST | `SFTP-Authorization` | SFTP |
| POST | `SharePoint-Authorization` | SharePoint |
| GET/POST | `GoogleDrive-Authorization` | Google Drive |
| POST | `AzureDataLakeStorage-Authorization` | Azure Data Lake |
| POST | `ADLS-Authorization` | ADLS alias |

### OAuth callbacks (migration-function)

| Method | Path |
|--------|------|
| GET | `salesforce/callback` |
| GET | `source/salesforce/callback` |
| GET | `salesforcesandbox/callback` |
| GET | `source/salesforcesandbox/callback` |
| GET | `source/googlereviews/callback` |
| GET | `google-drive/callback` |
| POST | `salesforce/refresh` |
| POST | `salesforcesandbox/refresh` |

### Listing

| Method | Path |
|--------|------|
| POST | `MicrosoftDynamics365_FO-listing-tables` |
| POST | `Snowflake-listing-tables` |
| POST | `Snowflake-listing-tables-readonly` |
| POST | `Salesforce-listing-tables` |
| POST | `SalesforceSandbox-listing-tables` |
| POST | `GoogleReviews-listing-tables` |
| POST | `AmazonS3-listing-tables` |
| POST | `AmazonS3-listing-files` |
| POST | `AmazonS3-preview-pattern` |
| POST | `AmazonS3-suggest-primary-keys` |

### Migrate

| Method | Path |
|--------|------|
| POST | `migrate` | Dynamics migration |
| POST | `Snowflake-migrate` | Snowflake migration |
| POST | `Salesforce-migrate` | Salesforce migration |
| POST | `Salesforce-Snowflake-migrate` | Salesforce → Snowflake |
| POST | `SalesforceSandbox-migrate` | Sandbox migration |
| POST | `SalesforceSandbox-Snowflake-migrate` | Sandbox → Snowflake |
| POST | `Snowflake-GoogleReviews-migrate` | Snowflake → Google Reviews |
| POST | `Snowflake-AmazonS3-migrate` | Snowflake → S3 |
| POST | `Snowflake-S3-migrate` | Snowflake → S3 (router) |
| POST | `Snowflake-SFTP-migrate` | Snowflake → SFTP |
| POST | `Snowflake-SharePoint-migrate` | Snowflake → SharePoint |
| POST | `Snowflake-ADLS-migrate` | Snowflake → ADLS |
| POST | `Snowflake-GoogleDrive-migrate` | Snowflake → Google Drive |

---

## WebSockets (`/ws/`)

Connect with `?token=<JWT>` query param (or session cookie).

| Path | Description |
|------|-------------|
| `ws/migration_status/<migration_session_id>/` | Live migration progress |
| `ws/connection_table_status/<connection_id>/` | Table listing status |
| `ws/update_schema_status/<connection_id>/` | Schema update status |
| `ws/connection_activity_log/<connection_id>/` | Activity log stream |
| `ws/company_connections_status/<cmp_id>/` | Company connection statuses |
| `ws/admin_ops/` | Platform ops dashboard (superuser JWT) |

---

## Kubernetes workloads (pods)

| Deployment / StatefulSet | Image | Exposed API |
|---------------------------|-------|-------------|
| `django` | `datasyncher-django-app:qa` | `/api/v1/`, `/ws/`, `/admin/` |
| `fastapi` | `datasyncher-fast-api:qa` | `/api/` |
| `migration-function` | `datasyncher-migration-function:qa` | `/api-new/` |
| `celery-worker-control` | `datasyncher-fast-api:qa` | Metrics `:9100` (in-cluster) |
| `celery-worker` | `datasyncher-fast-api:qa` | Celery consumer only (KEDA) |
| `celery-worker-django` | `datasyncher-django-app:qa` | Django Celery consumer |
| `celery-beat-django` | `datasyncher-django-app:qa` | Django beat scheduler |
| `celery-beat-migration` | `datasyncher-fast-api:qa` | Migration beat scheduler |
| `apscheduler` | `datasyncher-django-app:qa` | APScheduler (no HTTP API) |

---

## Deploy & admin setup

```powershell
# Rebuild and rollout QA backend
.\scripts\deploy-qa-django-fastapi-celery.ps1

# Create platform superuser on django pod
.\scripts\create-qa-platform-admin.ps1
```

---

## Related docs

- [k8s/README.md](../k8s/README.md) — cluster architecture
- [k8s/README-POOL-STRUCTURE.md](../k8s/README-POOL-STRUCTURE.md) — node pools and Celery
- [PROMETHEUS_METRICS.md](./PROMETHEUS_METRICS.md) — **Prometheus metrics** (scrape targets, catalog, alerts, verification)
