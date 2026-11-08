# RBAC Backend Implementation Guide

## Overview

This document describes the Role-Based Access Control (RBAC) backend implementation for the DataSyncher project, compatible with Django 3.2.

## Roles & Access Model

The system supports exactly **three roles**:

### 1. Administrator (Default Role)
- **Automatically assigned** when a new account is created
- **Full access** to all system features
- **Responsibilities:**
  - Creating and managing users
  - Assigning roles (Analyst, Billing)
  - Creating/editing connectors, destinations, tables
  - Viewing and managing billing and plans

### 2. Analyst (Limited Write Role)
- Can view everything related to data
- **Permissions:**
  - ✅ View connectors
  - ✅ View destinations
  - ✅ View tables
  - ✅ Add and edit tables
  - ✅ View sync status, logs, metrics
- **Restrictions:**
  - ❌ Cannot create/edit/delete connectors
  - ❌ No access to billing or plans

### 3. Billing (Billing-Only Role)
- Has access only to billing-related features
- **Permissions:**
  - ✅ Billing page
  - ✅ Subscription plans
  - ✅ Invoices
  - ✅ Payment history
- **Restrictions:**
  - ❌ No access to connectors, destinations, or data views
  - ❌ No user management
  - ❌ No system configuration

---

## Data Models

### User Model
```python
class User(AbstractUser):
    user_id = models.PositiveIntegerField(primary_key=True)
    cmp = models.ForeignKey(Company, on_delete=models.CASCADE, null=True)
    roles = models.ForeignKey(Role, on_delete=models.CASCADE, null=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    company_email = models.EmailField(unique=True)
    # ... other fields
```

### Role Model
```python
class Role(models.Model):
    role_id = models.PositiveIntegerField(primary_key=True)
    role_name = models.CharField(max_length=100, unique=True)
```

**Default Roles:**
- `role_id=1`: Administrator
- `role_id=2`: Analyst
- `role_id=3`: Billing

### Company Model
```python
class Company(models.Model):
    cmp_id = models.AutoField(primary_key=True)
    cmp_name = models.TextField(null=True)
    is_trial = models.BooleanField(default=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    # ... other fields
```

---

## Permission Classes

All permission classes are located in `apps/elttool/v1/permissions.py`:

### 1. `IsAdministrator`
- Checks if user has Administrator role
- Used for admin-only endpoints (user management, role assignment)

### 2. `IsAnalystOrAdmin`
- Checks if user has Analyst OR Administrator role
- Used for data viewing endpoints

### 3. `IsBillingOrAdmin`
- Checks if user has Billing OR Administrator role
- Used for billing-related endpoints

### 4. `IsAdminForWrite`
- **Read access (GET)**: Analyst and Administrator
- **Write access (POST, PUT, PATCH, DELETE)**: Administrator only
- Used for endpoints where Analysts can view but not modify

### 5. `CheckTrialPeriod`
- Checks if trial period is still active
- Blocks access if trial expired
- Uses caching to avoid repeated database queries

---

## API Endpoints

### Authentication

#### POST `/api/v1/authentication/`
**Sign Up (with company_name in request)**
```json
Request:
{
  "first_name": "John",
  "last_name": "Doe",
  "company_name": "Acme Corp",
  "employee_range": "11-75",
  "company_email": "john@acme.com",
  "password": "SecurePassword123!"
}

Response:
{
  "message": "Registration successful",
  "user": {
    "id": 1,
    "role": "Administrator",
    "role_id": 1,
    "account_id": 1
  },
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "redirect_to": "destination-home"
}
```

**Sign In (without company_name in request)**
```json
Request:
{
  "username": "john@acme.com",
  "password": "SecurePassword123!"
}

Response:
{
  "message": "Login successful",
  "user": {
    "id": 1,
    "role": "Administrator",
    "role_id": 1,
    "account_id": 1
  },
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "redirect_to": "home"
}
```

---

### User Management (Administrator Only)

#### GET `/api/v1/rbac/users/`
**List all users in the company**

**Permission:** Administrator only

```json
Response:
{
  "users": [
    {
      "user_id": 1,
      "first_name": "John",
      "last_name": "Doe",
      "company_email": "john@acme.com",
      "role": "Administrator",
      "role_id": 1,
      "is_active": true,
      "date_joined": "2024-01-01T00:00:00Z"
    },
    {
      "user_id": 2,
      "first_name": "Jane",
      "last_name": "Smith",
      "company_email": "jane@acme.com",
      "role": "Analyst",
      "role_id": 2,
      "is_active": true,
      "date_joined": "2024-01-15T00:00:00Z"
    }
  ]
}
```

#### POST `/api/v1/rbac/users/`
**Create a new user (Analyst or Billing)**

**Permission:** Administrator only

```json
Request:
{
  "first_name": "Jane",
  "last_name": "Smith",
  "company_email": "jane@acme.com",
  "password": "SecurePassword123!",
  "role_id": 2  // 2=Analyst, 3=Billing
}

Response:
{
  "message": "User created successfully",
  "user": {
    "user_id": 2,
    "first_name": "Jane",
    "last_name": "Smith",
    "company_email": "jane@acme.com",
    "role": "Analyst",
    "role_id": 2
  }
}
```

#### PATCH `/api/v1/rbac/users/{user_id}/`
**Update user role**

**Permission:** Administrator only

```json
Request:
{
  "role_id": 3  // Change to Billing role
}

Response:
{
  "message": "User role updated successfully",
  "user": {
    "user_id": 2,
    "role": "Billing",
    "role_id": 3
  }
}
```

---

### Current User Info

#### GET `/api/v1/rbac/me/`
**Get current authenticated user information**

**Permission:** All authenticated users

```json
Response:
{
  "user": {
    "user_id": 1,
    "first_name": "John",
    "last_name": "Doe",
    "company_email": "john@acme.com",
    "role": "Administrator",
    "role_id": 1,
    "account_id": 1,
    "company_name": "Acme Corp",
    "is_active": true
  }
}
```

---

### Roles

#### GET `/api/v1/rbac/roles/`
**Get list of available roles**

**Permission:** Administrator only

```json
Response:
{
  "roles": [
    {"role_id": 1, "role_name": "Administrator"},
    {"role_id": 2, "role_name": "Analyst"},
    {"role_id": 3, "role_name": "Billing"}
  ]
}
```

---

## Permission Matrix

| Feature | Administrator | Analyst | Billing |
|---------|--------------|---------|---------|
| View connectors | ✅ | ✅ | ❌ |
| Create/edit connectors | ✅ | ❌ | ❌ |
| View destinations | ✅ | ✅ | ❌ |
| Add/edit tables | ✅ | ✅ | ❌ |
| View sync logs / metrics | ✅ | ✅ | ❌ |
| User management | ✅ | ❌ | ❌ |
| Billing & plans | ✅ | ❌ | ✅ |

---

## Error Responses

All unauthorized requests return a standardized error response:

```json
{
  "error": "FORBIDDEN",
  "message": "You do not have permission to perform this action"
}
```

**HTTP Status Code:** `403 Forbidden`

---

## Setup Instructions

### 1. Initialize Roles

Run the management command to create the three required roles:

```bash
python manage.py init_roles
```

This creates:
- Administrator (role_id=1)
- Analyst (role_id=2)
- Billing (role_id=3)

### 2. Run Migrations

Ensure all migrations are applied:

```bash
python manage.py migrate
```

### 3. Create First Admin User

When a new account is created via signup, the first user is automatically assigned the Administrator role.

---

## Usage Examples

### Example 1: Protecting a View (Administrator Only)

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsAdministrator

class AdminOnlyView(APIView):
    permission_classes = [IsAuthenticated, IsAdministrator]
    
    def get(self, request):
        # Only administrators can access this
        return Response({"message": "Admin access granted"})
```

### Example 2: Protecting a View (Analyst or Admin)

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsAnalystOrAdmin

class DataViewAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]
    
    def get(self, request):
        # Analysts and Administrators can view data
        return Response({"data": [...]})
```

### Example 3: Read-Only for Analyst, Write for Admin

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsAdminForWrite

class ConnectorAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminForWrite]
    
    def get(self, request):
        # Analysts and Administrators can view
        return Response({"connectors": [...]})
    
    def post(self, request):
        # Only Administrators can create
        return Response({"message": "Connector created"})
```

### Example 4: Billing Access

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsBillingOrAdmin

class BillingAPIView(APIView):
    permission_classes = [IsAuthenticated, IsBillingOrAdmin]
    
    def get(self, request):
        # Billing users and Administrators can access
        return Response({"invoices": [...]})
```

---

## Frontend Integration

### 1. Store User Role on Login/Signup

```javascript
// After successful login/signup
const response = await fetch('/api/v1/authentication/', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username, password})
});

const data = await response.json();

// Store user info in state/context
localStorage.setItem('user_role', data.user.role);
localStorage.setItem('user_role_id', data.user.role_id);
localStorage.setItem('access_token', data.access_token);
```

### 2. Conditional Rendering Based on Role

```javascript
// React example
const UserDashboard = () => {
  const userRole = localStorage.getItem('user_role');
  
  return (
    <div>
      {/* Show to all authenticated users */}
      <h1>Dashboard</h1>
      
      {/* Show only to Administrator and Analyst */}
      {['Administrator', 'Analyst'].includes(userRole) && (
        <ConnectorsList />
      )}
      
      {/* Show only to Administrator */}
      {userRole === 'Administrator' && (
        <UserManagement />
      )}
      
      {/* Show only to Administrator and Billing */}
      {['Administrator', 'Billing'].includes(userRole) && (
        <BillingPage />
      )}
    </div>
  );
};
```

### 3. Backend Validation

**Important:** Frontend role checks are for UI convenience only. The backend **always** validates permissions before processing requests.

```javascript
// Frontend hides the button, but backend still validates
const createConnector = async (connectorData) => {
  try {
    const response = await fetch('/api/v1/connectors/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${accessToken}`
      },
      body: JSON.stringify(connectorData)
    });
    
    if (response.status === 403) {
      // Backend rejected due to insufficient permissions
      alert('You do not have permission to create connectors');
    }
  } catch (error) {
    console.error(error);
  }
};
```

---

## Security Considerations

### 1. Backend Enforcement
- **Never trust the frontend** for authorization
- All permission checks are enforced on the backend
- Frontend role information is for UI convenience only

### 2. Token-Based Authentication
- Uses JWT (JSON Web Tokens) for authentication
- Tokens include user_id for backend validation
- Backend retrieves user role from database on each request

### 3. Trial Period Checks
- Trial expiration is checked on the backend
- Uses caching to avoid performance issues
- Cache is invalidated when subscription changes

### 4. Database Integrity
- Role assignments are stored in the database
- Cannot be modified by the user directly
- Only Administrators can change user roles

---

## Troubleshooting

### Issue: "Role not found" error during signup

**Solution:** Run the `init_roles` management command:
```bash
python manage.py init_roles
```

### Issue: Permission denied for Administrator

**Solution:** Check that the user's `roles` field is correctly set:
```python
from apps.elttool.models import User
user = User.objects.get(user_id=1)
print(user.roles.role_name)  # Should print "Administrator"
```

### Issue: Frontend shows admin features to non-admin users

**Solution:** This is a frontend issue. The backend will still block unauthorized requests. Update frontend conditional rendering logic.

---

## Django 3.2 Compatibility

This implementation is fully compatible with Django 3.2:

- ✅ Uses `rest_framework.permissions.BasePermission`
- ✅ Uses `ForeignKey` for role relationships
- ✅ No Django 4.x-specific features
- ✅ Compatible with `djangorestframework==3.14.0`
- ✅ Compatible with `djangorestframework-simplejwt==4.8.0`

---

## Summary

This RBAC implementation provides:

1. **Three well-defined roles** (Administrator, Analyst, Billing)
2. **Backend-enforced permissions** (frontend cannot bypass)
3. **Clear API contracts** for frontend consumption
4. **Django 3.2 compatibility** (no dependency issues)
5. **Secure by default** (deny access unless explicitly granted)

The backend is safe even if the frontend is bypassed, as all authorization logic is enforced on the server side.
