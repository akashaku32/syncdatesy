# RBAC Permission Matrix Audit

## Required Permission Matrix

| Feature | Admin | Analyst | Billing |
|---------|-------|---------|---------|
| View connectors | ✅ | ✅ | ❌ |
| Create/edit connectors | ✅ | ❌ | ❌ |
| View destinations | ✅ | ✅ | ❌ |
| Add/edit tables | ✅ | ✅ | ❌ |
| View sync logs / metrics | ✅ | ✅ | ❌ |
| User management | ✅ | ❌ | ❌ |
| Billing & plans | ✅ | ❌ | ✅ |

---

## Endpoint Audit

### ✅ **1. View Connectors (Admin ✅, Analyst ✅, Billing ❌)**

**Endpoints:**
- `GET /api/v1/sources/` - List source connectors
- `GET /api/v1/destinations/` - List destination connectors
- `GET /api/v1/connections/` - List connections

**Required Permission:** `IsAnalystOrAdmin`

**Status:** ❓ **NEEDS VERIFICATION**

Let me check these endpoints...

---

### ✅ **2. Create/Edit Connectors (Admin ✅, Analyst ❌, Billing ❌)**

**Endpoints:**
- `POST /api/v1/destinations/save/` - Create destination
- `PUT /api/v1/destinations/update/<id>/` - Update destination
- `DELETE /api/v1/destinations/delete/<id>/` - Delete destination
- `POST /api/v1/sources/<source_name>/` - Create/update source
- `DELETE /api/v1/connections/delete/<id>/` - Delete connection

**Required Permission:** `IsAdministrator`

**Status:** ✅ **IMPLEMENTED** (just done)

---

### ✅ **3. View Destinations (Admin ✅, Analyst ✅, Billing ❌)**

Same as "View Connectors" - see above.

---

### ✅ **4. Add/Edit Tables (Admin ✅, Analyst ✅, Billing ❌)**

**Endpoints:**
- `POST /api/v1/connection/<id>/tables/` - Select tables
- `PUT /api/v1/connection/<id>/tables/` - Update table selection
- `POST /api/v1/connection/<id>/select-tables/` - Select tables for sync

**Required Permission:** `IsAnalystOrAdmin`

**Status:** ❓ **NEEDS VERIFICATION**

---

### ✅ **5. View Sync Logs / Metrics (Admin ✅, Analyst ✅, Billing ❌)**

**Endpoints:**
- `GET /api/v1/connection/<id>/logs/` - View connection logs
- `GET /api/v1/connection/<id>/status/` - View sync status
- `GET /api/v1/connection/migration_status/<id>/` - View migration status

**Required Permission:** `IsAnalystOrAdmin`

**Status:** ❓ **NEEDS VERIFICATION**

---

### ✅ **6. User Management (Admin ✅, Analyst ❌, Billing ❌)**

**Endpoints:**
- `GET /api/v1/users/` - List users
- `POST /api/v1/users/` - Create user
- `PUT /api/v1/users/<id>/` - Update user
- `GET /api/v1/rbac/users/` - List users (RBAC)
- `POST /api/v1/rbac/users/` - Create user (RBAC)

**Required Permission:** `IsAdministrator`

**Status:** ✅ **IMPLEMENTED**

---

### ✅ **7. Billing & Plans (Admin ✅, Analyst ❌, Billing ✅)**

**Endpoints:**
- `GET /api/v1/billing/chart/<cmp_id>/`
- `GET /api/v1/billing/summary/<cmp_id>/`
- `GET /api/v1/billing/<cmp_id>/monthly-usage/`
- `GET /api/v1/billing/<cmp_id>/annual-usage/`
- `GET /api/v1/billing/plan/`
- `POST /api/v1/billing/plan/`
- `GET /api/v1/billing/payment/`

**Required Permission:** `IsBillingOrAdmin`

**Status:** ✅ **IMPLEMENTED**

---

## Missing Implementations

Based on the audit, we need to secure these endpoint categories:

### 🔴 **CRITICAL: View Connectors/Destinations**

These endpoints currently allow ANY authenticated user (including Billing role) to view connectors:

**Endpoints to fix:**
1. Source list views
2. Destination list views
3. Connection list views

**Required Permission:** `IsAnalystOrAdmin`

---

### 🔴 **CRITICAL: Table Operations**

These endpoints currently allow ANY authenticated user to add/edit tables:

**Endpoints to fix:**
1. Table selection endpoints
2. Table update endpoints

**Required Permission:** `IsAnalystOrAdmin`

---

### 🔴 **CRITICAL: Sync Logs/Metrics**

These endpoints currently allow ANY authenticated user to view logs:

**Endpoints to fix:**
1. Connection logs
2. Migration status
3. Sync metrics

**Required Permission:** `IsAnalystOrAdmin`

---

## Action Required

I need to find and secure these endpoints. Let me search for them now...
