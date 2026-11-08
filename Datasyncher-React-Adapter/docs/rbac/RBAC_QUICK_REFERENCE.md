# RBAC Quick Reference Guide

## How to Apply RBAC to Existing Views

This guide shows how to add role-based access control to your existing API views.

---

## Step 1: Import Permission Classes

Add these imports to your `views.py`:

```python
from apps.elttool.v1.permissions import (
    IsAdministrator,
    IsAnalystOrAdmin,
    IsBillingOrAdmin,
    IsAdminForWrite,
    CheckTrialPeriod,
)
```

---

## Step 2: Apply Permissions to Views

### Administrator Only (User Management, Role Assignment)

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsAdministrator

class UserManagementView(APIView):
    permission_classes = [IsAuthenticated, IsAdministrator]
    
    def get(self, request):
        # Only administrators can access
        pass
```

**Use for:**
- User creation/deletion
- Role assignment
- System configuration

---

### Analyst or Administrator (Data Viewing)

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsAnalystOrAdmin

class ConnectorListView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]
    
    def get(self, request):
        # Analysts and Administrators can view
        pass
```

**Use for:**
- Viewing connectors
- Viewing destinations
- Viewing tables
- Viewing sync logs

---

### Billing or Administrator (Billing Pages)

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsBillingOrAdmin

class BillingView(APIView):
    permission_classes = [IsAuthenticated, IsBillingOrAdmin]
    
    def get(self, request):
        # Billing users and Administrators can access
        pass
```

**Use for:**
- Billing overview
- Subscription plans
- Invoices
- Payment history

---

### Read for Analyst, Write for Administrator

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsAdminForWrite

class ConnectorView(APIView):
    permission_classes = [IsAuthenticated, IsAdminForWrite]
    
    def get(self, request):
        # Analysts and Administrators can view
        pass
    
    def post(self, request):
        # Only Administrators can create
        pass
    
    def put(self, request):
        # Only Administrators can update
        pass
    
    def delete(self, request):
        # Only Administrators can delete
        pass
```

**Use for:**
- Connector CRUD operations
- Destination CRUD operations
- Any resource where Analysts can view but not modify

---

## Step 3: Update Existing Views

### Example: ConnectionListAPIView

**Before:**
```python
class ConnectionListAPIView(APIView):
    permission_classes = [IsAuthenticated]  # Only checks authentication
    
    def get(self, request):
        # Anyone authenticated can access
        pass
```

**After (View Only):**
```python
class ConnectionListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]
    
    def get(self, request):
        # Only Analysts and Administrators can view
        pass
```

**After (View + Create):**
```python
class ConnectionListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminForWrite]
    
    def get(self, request):
        # Analysts and Administrators can view
        pass
    
    def post(self, request):
        # Only Administrators can create
        pass
```

---

## Step 4: Test Permissions

### Test with Different Roles

```python
# Test as Administrator
curl -H "Authorization: Bearer <admin_token>" \
     http://localhost:8000/api/v1/connectors/

# Test as Analyst (should work for GET, fail for POST)
curl -H "Authorization: Bearer <analyst_token>" \
     http://localhost:8000/api/v1/connectors/

curl -X POST \
     -H "Authorization: Bearer <analyst_token>" \
     -H "Content-Type: application/json" \
     -d '{"name": "test"}' \
     http://localhost:8000/api/v1/connectors/
# Expected: 403 Forbidden

# Test as Billing (should fail for connectors)
curl -H "Authorization: Bearer <billing_token>" \
     http://localhost:8000/api/v1/connectors/
# Expected: 403 Forbidden
```

---

## Permission Matrix Reference

| Endpoint | Administrator | Analyst | Billing |
|----------|--------------|---------|---------|
| **Connectors** |
| GET /connectors | ✅ | ✅ | ❌ |
| POST /connectors | ✅ | ❌ | ❌ |
| PUT /connectors/{id} | ✅ | ❌ | ❌ |
| DELETE /connectors/{id} | ✅ | ❌ | ❌ |
| **Destinations** |
| GET /destinations | ✅ | ✅ | ❌ |
| POST /destinations | ✅ | ❌ | ❌ |
| PUT /destinations/{id} | ✅ | ❌ | ❌ |
| DELETE /destinations/{id} | ✅ | ❌ | ❌ |
| **Tables** |
| GET /tables | ✅ | ✅ | ❌ |
| POST /tables | ✅ | ✅ | ❌ |
| PUT /tables/{id} | ✅ | ✅ | ❌ |
| DELETE /tables/{id} | ✅ | ❌ | ❌ |
| **Sync Logs** |
| GET /logs | ✅ | ✅ | ❌ |
| **User Management** |
| GET /users | ✅ | ❌ | ❌ |
| POST /users | ✅ | ❌ | ❌ |
| PATCH /users/{id} | ✅ | ❌ | ❌ |
| **Billing** |
| GET /billing/* | ✅ | ❌ | ✅ |
| POST /billing/* | ✅ | ❌ | ✅ |

---

## Common Patterns

### Pattern 1: List + Create

```python
class ResourceListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAdminForWrite]
    
    def get(self, request):
        # Analysts and Administrators can list
        resources = Resource.objects.filter(company=request.user.cmp)
        return Response({"resources": resources})
    
    def post(self, request):
        # Only Administrators can create
        resource = Resource.objects.create(**request.data)
        return Response({"resource": resource})
```

### Pattern 2: Retrieve + Update + Delete

```python
class ResourceDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminForWrite]
    
    def get(self, request, resource_id):
        # Analysts and Administrators can view
        resource = Resource.objects.get(id=resource_id)
        return Response({"resource": resource})
    
    def put(self, request, resource_id):
        # Only Administrators can update
        resource = Resource.objects.get(id=resource_id)
        resource.update(**request.data)
        return Response({"resource": resource})
    
    def delete(self, request, resource_id):
        # Only Administrators can delete
        resource = Resource.objects.get(id=resource_id)
        resource.delete()
        return Response({"message": "Deleted"})
```

### Pattern 3: Custom Permission Logic

```python
class TableUpdateView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]
    
    def put(self, request, table_id):
        # Both Analysts and Administrators can update tables
        table = Table.objects.get(id=table_id)
        table.update(**request.data)
        return Response({"table": table})
```

---

## Error Handling

### Standard Error Response

When a user lacks permission, the backend returns:

```json
{
  "error": "FORBIDDEN",
  "message": "You do not have permission to perform this action"
}
```

**HTTP Status:** `403 Forbidden`

### Custom Error Messages

You can customize the error message in the permission class:

```python
class IsAdministrator(permissions.BasePermission):
    message = "Administrator role required for this action."
```

---

## Checklist for Adding RBAC

- [ ] Import permission classes
- [ ] Add `permission_classes` to view
- [ ] Test with Administrator token
- [ ] Test with Analyst token
- [ ] Test with Billing token
- [ ] Verify 403 errors for unauthorized access
- [ ] Update frontend to hide/show UI based on role
- [ ] Document permission requirements in API docs

---

## Quick Commands

### Initialize Roles
```bash
python manage.py init_roles
```

### Create Test Users
```python
from apps.elttool.models import User, Role, Company
from django.utils import timezone
from datetime import timedelta

# Get or create company
company = Company.objects.first()

# Get roles
admin_role = Role.objects.get(role_name='Administrator')
analyst_role = Role.objects.get(role_name='Analyst')
billing_role = Role.objects.get(role_name='Billing')

# Create Analyst user
analyst = User.objects.create(
    first_name='Jane',
    last_name='Analyst',
    company_email='analyst@test.com',
    email='analyst@test.com',
    cmp=company,
    roles=analyst_role,
    company_name=company.data,
)
analyst.set_password('password123')
analyst.save()

# Create Billing user
billing = User.objects.create(
    first_name='Bob',
    last_name='Billing',
    company_email='billing@test.com',
    email='billing@test.com',
    cmp=company,
    roles=billing_role,
    company_name=company.data,
)
billing.set_password('password123')
billing.save()
```

### Get User Tokens
```bash
# Login and get token
curl -X POST http://localhost:8000/api/v1/authentication/ \
  -H "Content-Type: application/json" \
  -d '{"username": "analyst@test.com", "password": "password123"}'
```

---

## Summary

1. **Import** the appropriate permission class
2. **Add** `permission_classes` to your view
3. **Test** with different role tokens
4. **Verify** 403 errors for unauthorized access
5. **Update** frontend UI based on role

The backend will **always** enforce permissions, even if the frontend is bypassed.
