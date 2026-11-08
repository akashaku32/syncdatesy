# Datasyncher Master API Specifications

This document provides a 100% comprehensive technical reference for every API endpoint available in the Datasyncher application (`v1`).

## Table of Contents
1.  [Authentication & Session](#1-authentication--session)
2.  [Destination Configuration](#2-destination-configuration)
3.  [Source Discovery](#3-source-discovery)
4.  [Connection Lifecycle & Setup](#4-connection-lifecycle--setup)
5.  [OAuth Callbacks](#5-oauth-callbacks)
6.  [Table & Schema Management](#6-table--schema-management)
7.  [Data Migration & Sync Control](#7-data-migration--sync-control)
8.  [Monitoring & Observability](#8-monitoring--observability)
9.  [User & Profile Management](#9-user--profile-management)
10. [RBAC (Role-Based Access Control)](#10-rbac)
11. [Billing, Usage & Plans](#11-billing-usage--plans)
12. [Technical Utilities & Internal APIs](#12-technical-utilities--internal-apis)

---

## 1. Authentication & Session

### [POST] Login / Sign Up
`{{baseUrl}}/api/v1/authentication/`

**Request Body (Login):**
```json
{
  "username": "user@example.com",
  "password": "yourpassword"
}
```

**Response (Success):**
| Field | Type | Description |
| :--- | :--- | :--- |
| `token` | string | DRF Token used for Authorization header (`Token <key>`) |

---

### [POST] Password Reset Initiation
`{{baseUrl}}/api/v1/password-reset/`

**Request Body:**
```json
{
  "email": "user@example.com"
}
```

---

### [POST] Password Reset Confirmation
`{{baseUrl}}/api/v1/password-reset-confirm/`

**Request Body:**
```json
{
  "uid": "MTU",
  "token": "token-string",
  "new_password": "newsecurepassword123"
}
```

---

### [GET] Dashboard Connections List
`{{baseUrl}}/api/v1/home/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `content` | array[object] | List of serialized connections (see below) |
| `totalElements` | integer | Total count for pagination |
| `size` | integer | Page size |
| `sourceconfigslist`| array[object] | Unique source platforms configured for the company |
| `cmp_id` | integer | Company ID |

**Connection Object (inside `content`):**
| Field | Type | Description |
| :--- | :--- | :--- |
| `connection_id` | integer | Unique identifier for the connection |
| `connector_name` | string | User-provided name for the connector |
| `source_name` | string | Platform name of the source (e.g., Salesforce) |
| `destination_name` | string | Platform name of the destination (e.g., Snowflake) |
| `status` | string | Connection status: `A` (Active), `P` (Paused) |
| `readable_time_frequency` | string | Human-readable sync interval (e.g., "30 minutes") |
| `last_synced_new` | string | ISO timestamp or "Never Synced" |
| `next_sync_time` | string | Calculated ISO timestamp of next sync |

---

### [GET] OAuth Flash Messages
`{{baseUrl}}/api/v1/flash-messages/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `message` | string | Temporary status message from OAuth redirects |
| `status` | string | Message category (success/error) |

---

## 2. Destination Configuration

### [GET] List Configured Destinations / Status
`{{baseUrl}}/api/v1/destinations/`
`{{baseUrl}}/api/v1/destinations/status/{{dst_config_id}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `destination` | object | Details of the destination (see below) |
| `destination.id`| integer | ID of the configuration |
| `destination.name`| string | User-provided name |
| `destination.is_active`| boolean| Valid status |
| `destination.config_data`| object | Technical configuration payload |
| `connection_exists` | boolean | True if an active connection uses this config |
| `cmp_id` | integer | Company ID |

---

### [GET] Available Destination Platforms
`{{baseUrl}}/api/v1/destinations/all/`

**Response:** List of supported destination platforms with their ID and icons.

---

### [GET] Dynamic Form Schema
`{{baseUrl}}/api/v1/destinations/schema/{{destination_name}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `destination` | string | Platform name (e.g., Snowflake) |
| `fields` | array[object] | JSON array of field objects (name, label, type, etc.) |

---

### [POST] Save/Test Destination Config
`{{baseUrl}}/api/v1/destinations/create/`
`{{baseUrl}}/api/v1/destinations/test/{{dst_config_id}}/`

**Request Body:**
```json
{
  "name": "My Snowflake Production",
  "dst": 5,
  "config_data": {
    "account": "xy12345",
    "user": "admin",
    "password": "...",
    "warehouse": "COMPUTE_WH",
    "database": "RAW_DB",
    "schema": "PUBLIC",
    "role": "ACCOUNTADMIN"
  }
}
```

---

---

## 3. Source Discovery

### [GET] Available Source Platforms
`{{baseUrl}}/api/v1/source/all/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `content` | array[object] | List of available platforms (see below) |
| `totalElements` | integer | Total platforms available |
| `size` | integer | Results per page |

**Platform Object (inside `content`):**
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | integer | Platform ID |
| `name` | string | Platform Name (e.g., Salesforce, MySQL) |
| `image_url` | string | URL for the platform icon |
| `is_rtl` | boolean | True if this source supports Reverse ETL |

---

### [POST] Create Source & Connection
`{{baseUrl}}/api/v1/source-config/{{source_name}}/create/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `connection_id` | integer | ID of the newly created connection |
| `message` | string | Success message |

---

### [GET] Source Config Detail
`{{baseUrl}}/api/v1/source/detail/{{src_config_id}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `src_config_id`| integer | Unique config ID |
| `name` | string | User-provided connector name |
| `src` | integer | Source platform ID |
| `config_data` | object | The saved configuration keys |

---

## 4. Connection Lifecycle & Setup

### [GET] Connection Setup Detail
`{{baseUrl}}/api/v1/connection/setup/{{connection_id}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `connection_id` | integer | Unique connection ID |
| `source_name` | string | Source platform name |
| `destination_name`| string | Destination platform name |
| `source_title` | string | User-provided name for source config |
| `destination_title`| string | User-provided name for dest config |
| `status` | string | `A` (Active) or `P` (Paused) |
| `connected_on` | string | ISO timestamp of connection creation |
| `target_schema` | string | Destination schema name |
| `is_reverse_etl` | boolean | True if source supports reverse ETL |

---

### [POST] Toggle Connection
`{{baseUrl}}/api/v1/connection/{{connection_id}}/toggle/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `connection_id` | integer | Connection ID |
| `new_status` | string | New status (`A` or `P`) |

### [GET] Connection Usage Statistics
`{{baseUrl}}/api/v1/connection/{{connection_id}}/usage/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `daily_labels` | array[string]| List of dates (YYYY-MM-DD) for the past 7 days |
| `new_rec_values_daily`| array[integer]| New records count per day |
| `mod_rec_values_daily`| array[integer]| Modified records count per day |
| `monthly_labels` | array[string]| List of months (YYYY-MM) for the past year |
| `new_rec_values_monthly`| array[integer]| New records count per month |
| `readable_time_frequency`| string | Human-readable sync frequency |
| `cmp_id` | integer | Company ID |

---

### [GET] Connection Activity Logs
`{{baseUrl}}/api/v1/connection/{{connection_id}}/activity/`

**Query Parameters:**
- `days` (optional): Number of past days (defaults to 1).

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `logs` | array[object] | List of logs: `{ "message": string, "status": "S/W/E", "timestamp": string }` |

---

### [GET] Session Detail Logs
`{{baseUrl}}/api/v1/connection/{{connection_id}}/session/{{session_id}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `session_id` | integer | Session identifier |
| `logs` | array[object] | List of detailed logs for this specific session |
| `logs[].table`| string | Table name associated with the log |
| `logs[].message`| string | Log message |
| `logs[].status`| string | `S` (Success), `W` (Warning), `E` (Error) |

---

## 5. OAuth Callbacks

| Endpoint | View | Description |
| :--- | :--- | :--- |
| `{{baseUrl}}/api/v1/salesforce/callback/` | `SalesforceCallbackAPIView` | Salesforce Dest callback. |
| `{{baseUrl}}/api/v1/salesforcesandbox/callback/` | `SalesforceCallbackAPIView` | Salesforce Sandbox Dest callback. |
| `{{baseUrl}}/api/v1/source/salesforce/callback/` | `SalesforceSourceCallbackAPIView` | Salesforce Source callback. |
| `{{baseUrl}}/api/v1/source/salesforcesandbox/callback/` | `SalesforceSourceCallbackAPIView` | Salesforce Sandbox Source callback. |
| `{{baseUrl}}/api/v1/source/googlereviews/callback/` | `GoogleReviewsSourceCallbackAPIView` | Google Reviews callback. |

---

## 6. Table & Schema Management

### [GET] Detailed Schema View
`{{baseUrl}}/api/v1/schema/{{connection_id}}/`

Retrieves the complete hierarchical view of tables and fields available for sync, including current selections.

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `selected_tables` | array[string] | List of table names currently selected for sync |
| `tables` | array[object] | Comprehensive list of available tables |
| `tables[].table_fields` | object | Dictionary of fields: `{ "column_name": "data_type" }` |
| `destination_tables` | array[object] | (Salesforce only) Available destination objects |
| `mapping_tables_data`| array[object] | Current source-to-destination table mappings |

---

### [POST] Update Table Selection
`{{baseUrl}}/api/v1/connection/{{connection_id}}/update-selection/`

**Request Body:**
```json
{
  "selected_tables": ["Users", "Orders", "Products"]
}
```

---

### [GET] Table Fields Detail
`{{baseUrl}}/api/v1/connection/{{connection_id}}/tables/{{table_name}}/fields/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `table` | string | Name of the table |
| `table_fields` | object | Dictionary of columns: `{ "column": { "data_type": "...", "is_primary_key": boolean } }` |
| `primary_keys` | array[string] | List of primary key column names |

---

### [GET] S3 Table Mappings
`{{baseUrl}}/api/v1/connection/{{connection_id}}/mappings/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | string | `success` or `error` |
| `connection_id` | integer | ID of the connection |
| `plan_type` | string | Company plan (Free, Pro, etc.) |
| `total_mappings` | integer | Count of mappings found |
| `mappings` | array[object] | List of mappings: `{ "source_table": string, "destination_table": string, "status": string }` |
| `message` | string | Status message from backend |

---

---

## 7. Data Migration & Sync Control

### [POST] Reload Single Table
`{{baseUrl}}/api/v1/reload-single-table/`

**Request Body:**
```json
{
  "connection_id": 12,
  "table_name": "Users"
}
```

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `success` | boolean | True if reload task initiated |
| `message` | string | Status details |

---

### [POST] Refresh Delta Table
`{{baseUrl}}/api/v1/refresh-delta-table/`

**Request Body:**
```json
{
  "connection_id": 12,
  "table_name": "Orders"
}
```

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `success` | boolean | True if delta refresh initiated |
| `message` | string | Status details |

---

## 8. Monitoring & Observability

### [GET] Real-time Migration Status
`{{baseUrl}}/api/v1/connection/migration_status/{{migration_session_id}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `migration_record` | object | Summary of the migration session |
| `migration_record.overall_status` | string | `completed`, `in_progress`, or `failed` |
| `migration_record.num_tables` | integer | Total tables in this session |
| `table_records` | array[object]| Detailed status per table |
| `table_records[].table_name`| string | Name of the table |
| `table_records[].status` | string | `synced`, `failed`, or `in_progress` |
| `table_records[].num_new` | integer | Number of new records inserted |

---

### [GET] Connector Discovery Status
`{{baseUrl}}/api/v1/get_table_status/?connection_id={{connection_id}}`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `connection_id` | integer | ID of the connection |
| `is_schema_refresh_in_progress` | boolean | True if background discovery is active |
| `next_sync_time` | string | ISO timestamp of next sync |
| `table_statuses` | array[object]| Individual sync status for each table |

---

## 9. User & Profile Management

### [GET] User Profile
`{{baseUrl}}/api/v1/user/profile/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `user_id` | integer | Unique user ID |
| `first_name` | string | First name |
| `last_name` | string | Last name |
| `company_email` | string | User email address |
| `role` | string | Current role name |
| `is_trial` | boolean | Trial status |
| `trial_days_remaining`| integer | Days left in trial |
| `permissions` | object | Dictionary of feature-level permissions |

---

### [GET] User Info
`{{baseUrl}}/api/v1/user/info/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `user_id` | integer | Unique user ID |
| `email` | string | User email address |
| `cmp_id` | integer | Company ID |
| `cmp_name` | string | Company name |

---

## 10. RBAC (Role-Based Access Control)

### [GET] Manage User Roles / User List
`{{baseUrl}}/api/v1/users/`
`{{baseUrl}}/api/v1/rbac/users/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `users` | array[object] | List of users (see below) |
| `users[].user_id` | integer | User identifier |
| `users[].company_email`| string | User email |
| `users[].role` | string | Current role name |
| `users[].is_active` | boolean | Status |

---

## 11. Billing, Usage & Plans

### [GET] Billing Summary
`{{baseUrl}}/api/v1/billing/summary/{{cmp_id}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `cmp_id` | integer | Company ID |
| `current_month_labels` | array[string]| Day labels for the current month chart |
| `current_month_billing`| array[number]| Usage data points for the current month |
| `billing_period` | string | Current billing cycle identifier |
| `invoices` | array[object]| List of previous invoices |
| `billing_details` | array[object]| Monthly breakdown: `{ "period": "January 2026", "total_usage": 5000, "amount": 100.00 }` |

---

### [GET] Plan Selection & Trial Status
`{{baseUrl}}/api/v1/billing/plan/{{cmp_id}}/`

**Response:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `plan` | string | Current plan name |
| `is_trial` | boolean | True if currently in trial period |
| `days_remaining` | integer | Days until trial expires |
| `trial_end_date` | string | ISO timestamp of trial expiration |

---

## 12. Technical Utilities & Internal APIs

### [POST] S3: Suggest Table Name
`{{baseUrl}}/api/v1/s3/suggest-table-name/`
Body: `{ "file_list": ["sales_2023.csv", "sales_2024.csv"] }`
Response: `{ "suggested_name": "sales" }`

---

### [POST] Internal: WebSocket Push
`{{baseUrl}}/api/v1/internal/migration-status-push/{{migration_session_id}}/`

*Reserved for Internal Service communication.*
Triggers a broadcast to the WebSocket group `migration_status_{{migration_session_id}}` with the current migration state.

---

### [POST] Internal: Proxy Agent Migration Progress
`{{baseUrl}}/api/v1/internal/proxy-agent/migration-progress/`

*Cluster-only.* Called by `proxy-agent-gateway` (or phase-2 hub relay) after agent table/job events.

**Headers:** `X-INTERNAL-KEY`, `X-Agent-UID` (cert CN), `X-Agent-Timestamp`, `X-Agent-Signature`, optional `Idempotency-Key`.

**Body:** `{ "migration_session_id", "job_name", "connection_id", "event", "table_name?", "idempotency_key?", ... }`

**Events:** `table_started` | `table_progress` | `table_completed` | `table_failed` | `activity_log` | `batch_completed`

**Response:** `{ "accepted": true, "overall_status": "in_progress"|"completed"|"failed", "tables_remaining": N }`

Public agent URL (mTLS): `https://agent-api.<domain>/v1/migration-progress` → gateway → this endpoint.  
See `docs/operations/proxy-agent/` (ops) and monorepo `docs/security/proxy-agent-progress.md` (mTLS/HMAC).

---

> [!NOTE]
> This is a complete technical reference for the Datasyncher API v1. All endpoints require an `Authorization: Token <key>` header unless marked otherwise (e.g., OAuth callbacks).
