# RBAC Connection/Destination Settings Protection

## ✅ **Issue Fixed**

**Problem:** Analyst users could edit connection settings (name, sync frequency, safety interval, etc.) and destination settings, which should be restricted to Administrators only.

**Solution:** Applied `IsAdministrator` permission to all connection/destination settings update endpoints.

---

## Endpoints Secured

### Connection Settings Endpoints (5 views)

| Endpoint | View Class | Method | Permission | Status |
|----------|------------|--------|------------|--------|
| `GET /api/v1/connection/<id>/` | `ConnectionDetailAPIView` | GET | `IsAnalystOrAdmin` | ✅ Analysts can view |
| `PATCH /api/v1/connection/<id>/` | `ConnectionDetailAPIView` | PATCH | `IsAdministrator` | ✅ Only Admins can edit |
| `POST /api/v1/connection/<id>/update-sync-frequency/` | `UpdateConnectionSyncFrequencyAPIView` | POST | `IsAdministrator` | ✅ Secured |
| `POST /api/v1/connection/<id>/toggle/` | `ConnectionToggleAPIView` | POST | `IsAdministrator` | ✅ Secured |
| `POST /api/v1/connection/<id>/discard-schema-changes/` | `DiscardSchemaChangesAPIView` | POST | `IsAdministrator` | ✅ Secured |

### Destination Settings Endpoints (1 view)

| Endpoint | View Class | Permission | Status |
|----------|------------|------------|--------|
| `PUT /api/v1/destinations/change-name/<id>/` | `ChangeDestinationConfigNameAPIView` | `IsAdministrator` | ✅ Secured |

---

## Settings That Are Now Protected

### Connection Settings (Admin Only)
- ✅ Connection name
- ✅ Sync frequency (time_frequency)
- ✅ Safety interval
- ✅ Sync start date
- ✅ Execution order
- ✅ Connection toggle (activate/deactivate)
- ✅ Discard schema changes

### Destination Settings (Admin Only)
- ✅ Destination name

---

## Access Control Matrix

| Role | View Connection Details | Edit Connection Settings |
|------|------------------------|-------------------------|
| **Administrator** | ✅ Allowed | ✅ Allowed |
| **Analyst** | ✅ Allowed | ❌ Denied (403) |
| **Billing** | ❌ Denied (403) | ❌ Denied (403) |

---

## Code Changes

### File: `apps/elttool/v1/views.py`

**1. Method-Level Permissions for ConnectionDetailAPIView:**

```python
class ConnectionDetailAPIView(APIView):
    # ✅ Method-level permissions: Analysts can view (GET), only Admins can edit (PATCH)
    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsAnalystOrAdmin()]
        elif self.request.method == 'PATCH':
            return [IsAuthenticated(), IsAdministrator()]
        return [IsAuthenticated()]
```

**2. Updated 4 Other View Classes:**

```python
# BEFORE
class UpdateConnectionSyncFrequencyAPIView(APIView):
    permission_classes = [IsAuthenticated]

# AFTER
class UpdateConnectionSyncFrequencyAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdministrator]  # ✅ Only Admins can update sync frequency
```

Applied to:
- `UpdateConnectionSyncFrequencyAPIView` - Update sync frequency
- `ChangeDestinationConfigNameAPIView` - Rename destination
- `ConnectionToggleAPIView` - Toggle connection on/off
- `DiscardSchemaChangesAPIView` - Discard schema changes

---

## Testing

### Test 1: Analyst Can View Connection Details

```javascript
// Login as Analyst
const analystToken = localStorage.getItem('access_token');

// Try to view connection details (should work)
fetch('http://localhost:8000/api/v1/connection/1/', {
  method: 'GET',
  headers: {
    'Authorization': `Bearer ${analystToken}`
  }
})
.then(res => {
  console.log('View Status:', res.status);
  if (res.status === 200) {
    console.log('✅ Analyst can view connection details');
  }
  return res.json();
})
.then(data => console.log('Connection Data:', data));
// Expected: 200 OK
```

### Test 2: Analyst Cannot Edit Connection Settings

```javascript
// Try to update connection settings (should fail)
fetch('http://localhost:8000/api/v1/connection/1/', {
  method: 'PATCH',
  headers: {
    'Authorization': `Bearer ${analystToken}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: "Modified Connection Name",
    time_frequency: "0 */6 * * *",
    safety_interval: 120
  })
})
.then(res => {
  console.log('Edit Status:', res.status);
  if (res.status === 403) {
    console.log('✅ RBAC WORKING - Analyst blocked from editing');
  }
  return res.json();
})
.then(data => console.log('Response:', data));

// Expected Response:
// Status: 403
// {
//   "detail": "You do not have permission to perform this action. Administrator role required."
// }
```

### Test 3: Analyst Cannot Update Sync Frequency

```javascript
// Try to update sync frequency (should fail)
fetch('http://localhost:8000/api/v1/connection/1/update-sync-frequency/', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${analystToken}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    new_sync_frequency: "0 */12 * * *",
    safety_interval: 180
  })
})
.then(res => console.log('Sync Frequency Update Status:', res.status));
// Expected: 403 Forbidden
```

### Test 4: Analyst Cannot Rename Destination

```javascript
// Try to rename destination (should fail)
fetch('http://localhost:8000/api/v1/destinations/change-name/1/', {
  method: 'PUT',
  headers: {
    'Authorization': `Bearer ${analystToken}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    new_name: "Modified Destination Name"
  })
})
.then(res => console.log('Rename Destination Status:', res.status));
// Expected: 403 Forbidden
```

### Test 5: Administrator Can Edit (Should Work)

```javascript
// Login as Administrator
const adminToken = localStorage.getItem('access_token');

// Update connection settings (should work)
fetch('http://localhost:8000/api/v1/connection/1/', {
  method: 'PATCH',
  headers: {
    'Authorization': `Bearer ${adminToken}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: "Admin Modified Connection",
    time_frequency: "0 */8 * * *"
  })
})
.then(res => console.log('Admin Edit Status:', res.status));
// Expected: 200 OK
```

---

## Expected Behavior

### Before Fix ❌
- Analyst could edit connection name
- Analyst could change sync frequency
- Analyst could modify safety interval
- Analyst could rename destinations
- Analyst could toggle connections

### After Fix ✅
- ✅ Analyst can **VIEW** connection details (read-only)
- ❌ Analyst **CANNOT EDIT** connection name (403)
- ❌ Analyst **CANNOT CHANGE** sync frequency (403)
- ❌ Analyst **CANNOT MODIFY** safety interval (403)
- ❌ Analyst **CANNOT RENAME** destinations (403)
- ❌ Analyst **CANNOT TOGGLE** connections (403)
- ✅ Administrator can do everything

---

## Summary

**Total Endpoints Secured:** 6
- 5 connection settings endpoints
- 1 destination settings endpoint

**Settings Protected:**
- Connection name, sync frequency, safety interval, start date, execution order
- Connection toggle (activate/deactivate)
- Schema change management
- Destination name

**Security Improvement:**
- Before: Analysts could modify connection/destination settings ❌
- After: Only Administrators can modify settings ✅

---

## Verification

1. **Logout** and **login as Analyst user**
2. **Try to edit connection settings** in the frontend
3. **Expected:** You should get a 403 error when trying to save changes
4. **Check Django console** for permission check logs:

```
================================================================================
🔍 [IsAdministrator.has_permission] Checking Administrator permission
   User: analyst@example.com (ID: 54)
   User role name: Analyst
   ❌ Permission DENIED - User role is 'Analyst', not Administrator
================================================================================
```

The server should have automatically reloaded. **Test it now!** 🔒
