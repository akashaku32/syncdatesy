# RBAC (Role-Based Access Control) Implementation Guide

## Table of Contents
1. [Overview](#overview)
2. [Current RBAC Structure](#current-rbac-structure)
3. [Role Definitions](#role-definitions)
4. [Implementation Steps](#implementation-steps)
5. [Permission Checks](#permission-checks)
6. [View-Level Implementation](#view-level-implementation)
7. [API Endpoint Protection](#api-endpoint-protection)
8. [Testing](#testing)

---

## Overview

This guide explains how to implement Role-Based Access Control (RBAC) in the DataSyncher application. The system supports multiple roles with different permission levels:

- **Administrator**: Full access to all features
- **Feature Analyst**: Monitoring only, limited create/edit/delete permissions

---

## Current RBAC Structure

### Database Models

**File**: `apps/elttool/models.py`

#### 1. Role Model
```python
class Role(models.Model):
    role_id = models.AutoField(primary_key=True)
    role_name = models.CharField(max_length=100, unique=True)
    
    class Meta:
        db_table = 'role'
```

**Existing Roles**:
- Administrator
- Creator
- Billing
- Analyst
- Reviewer

#### 2. RoleAccess Model
```python
class RoleAccess(models.Model):
    role_access_id = models.AutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    type = models.CharField(max_length=100)  # 'Create', 'Table', 'Billing', etc.
    
    class Meta:
        db_table = 'role_access'
        unique_together = ['role', 'type']
```

**Access Types**:
- `Create`: Can create/edit/delete connections and destinations
- `Table`: Can view and manage tables
- `Billing`: Can access billing features

#### 3. User Model
```python
class User(AbstractUser):
    user_id = models.PositiveIntegerField(primary_key=True)
    cmp = models.ForeignKey(Company, on_delete=models.CASCADE, null=True)
    roles = models.ForeignKey(Role, on_delete=models.CASCADE, null=True)
    # ... other fields
    
    class Meta:
        db_table = 'user'
```

**Key Field**: `roles` - Foreign key to `Role` table

---

## Role Definitions

### 1. Administrator

**Permissions**:
- ✅ **Full Access** to all features
- ✅ Create, edit, delete connections
- ✅ Create, edit, delete destinations
- ✅ Load and update schemas
- ✅ Monitor billing
- ✅ Manage users
- ✅ All monitoring features

**RoleAccess Entries**:
```python
RoleAccess(role=administrator_role, type='Create')
RoleAccess(role=administrator_role, type='Table')
RoleAccess(role=administrator_role, type='Billing')
RoleAccess(role=administrator_role, type='Monitor')
RoleAccess(role=administrator_role, type='Manage')
```

### 2. Feature Analyst

**Permissions**:
- ✅ **Monitoring Only** - View dashboards, logs, status
- ✅ **Load Schemas** - Can update/refresh table schemas
- ✅ **Monitor Billing** - View billing information (read-only)
- ❌ **Cannot Create** connections or destinations
- ❌ **Cannot Edit** connections or destinations
- ❌ **Cannot Delete** connections or destinations

**RoleAccess Entries**:
```python
RoleAccess(role=analyst_role, type='Table')      # Can load/update schemas
RoleAccess(role=analyst_role, type='Billing')   # Can monitor billing (read-only)
RoleAccess(role=analyst_role, type='Monitor')   # Can view monitoring data
# NO 'Create' access - prevents create/edit/delete
```

---

## Implementation Steps

### Step 1: Create Permission Helper Functions

**File**: `apps/elttool/v1/permissions.py` (extend existing file)

Add new permission check functions:

```python
from rest_framework import permissions
from ..models import RoleAccess, Role

def has_create_permission(user):
    """
    Check if user has permission to create/edit/delete connections and destinations.
    
    Args:
        user: User instance
        
    Returns:
        bool: True if user has Create permission
    """
    if not user or not user.is_authenticated:
        return False
    
    if not user.roles:
        return False
    
    return RoleAccess.objects.filter(
        role=user.roles,
        type='Create'
    ).exists()

def has_table_permission(user):
    """
    Check if user has permission to load/update schemas.
    
    Args:
        user: User instance
        
    Returns:
        bool: True if user has Table permission
    """
    if not user or not user.is_authenticated:
        return False
    
    if not user.roles:
        return False
    
    return RoleAccess.objects.filter(
        role=user.roles,
        type='Table'
    ).exists()

def has_billing_permission(user):
    """
    Check if user has permission to access billing features.
    
    Args:
        user: User instance
        
    Returns:
        bool: True if user has Billing permission
    """
    if not user or not user.is_authenticated:
        return False
    
    if not user.roles:
        return False
    
    return RoleAccess.objects.filter(
        role=user.roles,
        type='Billing'
    ).exists()

def has_monitor_permission(user):
    """
    Check if user has permission to view monitoring data.
    
    Args:
        user: User instance
        
    Returns:
        bool: True if user has Monitor permission
    """
    if not user or not user.is_authenticated:
        return False
    
    if not user.roles:
        return False
    
    # Administrator always has monitor permission
    if user.roles.role_name == 'Administrator':
        return True
    
    return RoleAccess.objects.filter(
        role=user.roles,
        type='Monitor'
    ).exists()

def is_administrator(user):
    """
    Check if user is an Administrator.
    
    Args:
        user: User instance
        
    Returns:
        bool: True if user is Administrator
    """
    if not user or not user.is_authenticated:
        return False
    
    if not user.roles:
        return False
    
    return user.roles.role_name == 'Administrator'

def is_feature_analyst(user):
    """
    Check if user is a Feature Analyst.
    
    Args:
        user: User instance
        
    Returns:
        bool: True if user is Feature Analyst
    """
    if not user or not user.is_authenticated:
        return False
    
    if not user.roles:
        return False
    
    return user.roles.role_name == 'Analyst'  # Assuming 'Analyst' is Feature Analyst
```

### Step 2: Create DRF Permission Classes

**File**: `apps/elttool/v1/permissions.py`

Add DRF permission classes:

```python
from rest_framework.permissions import BasePermission

class CanCreateConnection(BasePermission):
    """
    Permission class to check if user can create/edit/delete connections.
    """
    message = "You do not have permission to create, edit, or delete connections."
    
    def has_permission(self, request, view):
        return has_create_permission(request.user)

class CanLoadSchema(BasePermission):
    """
    Permission class to check if user can load/update schemas.
    """
    message = "You do not have permission to load or update schemas."
    
    def has_permission(self, request, view):
        return has_table_permission(request.user)

class CanAccessBilling(BasePermission):
    """
    Permission class to check if user can access billing features.
    """
    message = "You do not have permission to access billing features."
    
    def has_permission(self, request, view):
        return has_billing_permission(request.user)

class CanMonitor(BasePermission):
    """
    Permission class to check if user can view monitoring data.
    """
    message = "You do not have permission to view monitoring data."
    
    def has_permission(self, request, view):
        return has_monitor_permission(request.user)
```

### Step 3: Update Views to Use Permission Classes

**File**: `apps/elttool/v1/views.py`

#### 3.1 Connection Creation/Update/Delete Endpoints

```python
from .permissions import CanCreateConnection

class ConnectionCreateAPIView(APIView):
    """
    Create a new connection.
    Only users with 'Create' permission can access this endpoint.
    """
    permission_classes = [IsAuthenticated, CanCreateConnection]
    
    def post(self, request):
        # Existing connection creation logic
        pass

class ConnectionUpdateAPIView(APIView):
    """
    Update an existing connection.
    Only users with 'Create' permission can access this endpoint.
    """
    permission_classes = [IsAuthenticated, CanCreateConnection]
    
    def put(self, request, connection_id):
        # Existing connection update logic
        pass
    
    def patch(self, request, connection_id):
        # Existing connection partial update logic
        pass

class ConnectionDeleteAPIView(APIView):
    """
    Delete a connection.
    Only users with 'Create' permission can access this endpoint.
    """
    permission_classes = [IsAuthenticated, CanCreateConnection]
    
    def delete(self, request, connection_id):
        # Existing connection deletion logic
        pass
```

#### 3.2 Destination Creation/Update/Delete Endpoints

```python
class DestinationConfigCreateAPIView(APIView):
    """
    Create a new destination configuration.
    Only users with 'Create' permission can access this endpoint.
    """
    permission_classes = [IsAuthenticated, CanCreateConnection]
    
    def post(self, request):
        # Existing destination creation logic
        pass

class DestinationConfigUpdateAPIView(APIView):
    """
    Update an existing destination configuration.
    Only users with 'Create' permission can access this endpoint.
    """
    permission_classes = [IsAuthenticated, CanCreateConnection]
    
    def put(self, request, dst_config_id):
        # Existing destination update logic
        pass
```

#### 3.3 Schema Loading Endpoints

```python
from .permissions import CanLoadSchema

class RefreshSchemaAPIView(APIView):
    """
    Refresh/load table schemas.
    Users with 'Table' permission can access this endpoint.
    Feature Analyst can use this to update schemas.
    """
    permission_classes = [IsAuthenticated, CanLoadSchema]
    
    def post(self, request, connection_id):
        # Existing schema refresh logic
        pass
```

#### 3.4 Billing Endpoints

```python
from .permissions import CanAccessBilling

class BillingPlanAPIView(APIView):
    """
    View billing plans and usage.
    Users with 'Billing' permission can access this endpoint.
    Feature Analyst can monitor billing (read-only).
    """
    permission_classes = [IsAuthenticated, CanAccessBilling]
    
    def get(self, request):
        # Existing billing view logic (read-only for Feature Analyst)
        pass
    
    def post(self, request):
        # Only Administrator can update billing
        if not is_administrator(request.user):
            return Response(
                {"message": "Only administrators can update billing plans."},
                status=status.HTTP_403_FORBIDDEN
            )
        # Existing billing update logic
        pass
```

#### 3.5 Monitoring Endpoints

```python
from .permissions import CanMonitor

class MigrationStatusAPIView(APIView):
    """
    View migration status and logs.
    Users with 'Monitor' permission can access this endpoint.
    Both Administrator and Feature Analyst can view monitoring data.
    """
    permission_classes = [IsAuthenticated, CanMonitor]
    
    def get(self, request, connection_id):
        # Existing monitoring logic
        pass

class LogsAPIView(APIView):
    """
    View connection logs.
    Users with 'Monitor' permission can access this endpoint.
    """
    permission_classes = [IsAuthenticated, CanMonitor]
    
    def get(self, request, connection_id):
        # Existing logs view logic
        pass
```

### Step 4: Add Inline Permission Checks in Views

For views that need more granular control, add inline checks:

```python
from .permissions import has_create_permission, is_administrator, is_feature_analyst

class ConnectionDetailAPIView(APIView):
    """
    Get connection details.
    All authenticated users can view, but only users with 'Create' permission can see edit/delete options.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, connection_id):
        connection = get_object_or_404(Connection, connection_id=connection_id)
        
        # Check if user's company owns this connection
        if connection.cmp != request.user.cmp:
            return Response(
                {"message": "You do not have access to this connection."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = ConnectionDetailSerializer(connection)
        data = serializer.data
        
        # Add permission flags to response
        data['can_edit'] = has_create_permission(request.user)
        data['can_delete'] = has_create_permission(request.user)
        data['can_monitor'] = has_monitor_permission(request.user)
        
        return Response(data)
    
    def put(self, request, connection_id):
        # Check permission before allowing update
        if not has_create_permission(request.user):
            return Response(
                {"message": "You do not have permission to edit connections."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Existing update logic
        pass
```

### Step 5: Update Frontend to Hide/Disable Actions

**Frontend Changes** (React):

```javascript
// In connection detail component
const ConnectionDetail = ({ connection }) => {
  const { user } = useAuth();
  const canEdit = user.permissions?.includes('Create');
  const canDelete = user.permissions?.includes('Create');
  const canMonitor = user.permissions?.includes('Monitor');
  
  return (
    <div>
      <h1>{connection.name}</h1>
      
      {/* Only show edit button if user has Create permission */}
      {canEdit && (
        <button onClick={handleEdit}>Edit Connection</button>
      )}
      
      {/* Only show delete button if user has Create permission */}
      {canDelete && (
        <button onClick={handleDelete}>Delete Connection</button>
      )}
      
      {/* Show monitor button if user has Monitor permission */}
      {canMonitor && (
        <button onClick={handleMonitor}>View Logs</button>
      )}
      
      {/* Always show schema refresh if user has Table permission */}
      {user.permissions?.includes('Table') && (
        <button onClick={handleRefreshSchema}>Refresh Schema</button>
      )}
    </div>
  );
};
```

---

## Permission Checks

### Permission Matrix

| Action | Administrator | Feature Analyst |
|--------|--------------|-----------------|
| **Create Connection** | ✅ | ❌ |
| **Edit Connection** | ✅ | ❌ |
| **Delete Connection** | ✅ | ❌ |
| **Create Destination** | ✅ | ❌ |
| **Edit Destination** | ✅ | ❌ |
| **Delete Destination** | ✅ | ❌ |
| **Load/Update Schema** | ✅ | ✅ |
| **Monitor Billing** | ✅ | ✅ (Read-only) |
| **Update Billing** | ✅ | ❌ |
| **View Logs** | ✅ | ✅ |
| **View Migration Status** | ✅ | ✅ |
| **View Dashboards** | ✅ | ✅ |

### Permission Check Functions Usage

```python
# In views.py
from .permissions import (
    has_create_permission,
    has_table_permission,
    has_billing_permission,
    has_monitor_permission,
    is_administrator,
    is_feature_analyst
)

# Example: Check before allowing connection creation
if not has_create_permission(request.user):
    return Response(
        {"message": "You do not have permission to create connections."},
        status=status.HTTP_403_FORBIDDEN
    )

# Example: Allow schema refresh for both Administrator and Feature Analyst
if has_table_permission(request.user):
    # Allow schema refresh
    pass

# Example: Restrict billing updates to Administrator only
if not is_administrator(request.user):
    return Response(
        {"message": "Only administrators can update billing plans."},
        status=status.HTTP_403_FORBIDDEN
    )
```

---

## View-Level Implementation

### Complete Example: Connection Management View

```python
class ConnectionManagementAPIView(APIView):
    """
    Comprehensive connection management endpoint with RBAC.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, connection_id=None):
        """
        Get connection(s).
        All authenticated users can view connections in their company.
        """
        if connection_id:
            connection = get_object_or_404(Connection, connection_id=connection_id)
            
            # Check company ownership
            if connection.cmp != request.user.cmp:
                return Response(
                    {"message": "You do not have access to this connection."},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            serializer = ConnectionDetailSerializer(connection)
            data = serializer.data
            
            # Add permission flags
            data['permissions'] = {
                'can_edit': has_create_permission(request.user),
                'can_delete': has_create_permission(request.user),
                'can_monitor': has_monitor_permission(request.user),
                'can_refresh_schema': has_table_permission(request.user)
            }
            
            return Response(data)
        else:
            # List all connections for user's company
            connections = Connection.objects.filter(cmp=request.user.cmp)
            serializer = ConnectionSerializer(connections, many=True)
            return Response(serializer.data)
    
    def post(self, request):
        """
        Create new connection.
        Only users with 'Create' permission can create connections.
        """
        if not has_create_permission(request.user):
            return Response(
                {"message": "You do not have permission to create connections."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Existing connection creation logic
        serializer = ConnectionSerializer(data=request.data)
        if serializer.is_valid():
            connection = serializer.save(cmp=request.user.cmp)
            return Response(
                ConnectionDetailSerializer(connection).data,
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def put(self, request, connection_id):
        """
        Update connection.
        Only users with 'Create' permission can update connections.
        """
        if not has_create_permission(request.user):
            return Response(
                {"message": "You do not have permission to edit connections."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        connection = get_object_or_404(Connection, connection_id=connection_id)
        
        # Check company ownership
        if connection.cmp != request.user.cmp:
            return Response(
                {"message": "You do not have access to this connection."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Existing update logic
        serializer = ConnectionUpdateSerializer(connection, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(ConnectionDetailSerializer(connection).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, connection_id):
        """
        Delete connection.
        Only users with 'Create' permission can delete connections.
        """
        if not has_create_permission(request.user):
            return Response(
                {"message": "You do not have permission to delete connections."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        connection = get_object_or_404(Connection, connection_id=connection_id)
        
        # Check company ownership
        if connection.cmp != request.user.cmp:
            return Response(
                {"message": "You do not have access to this connection."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Existing deletion logic
        connection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
```

---

## API Endpoint Protection

### URL Configuration

**File**: `apps/elttool/v1/urls.py`

```python
from django.urls import path
from .views import (
    ConnectionManagementAPIView,
    RefreshSchemaAPIView,
    BillingPlanAPIView,
    MigrationStatusAPIView
)
from .permissions import (
    CanCreateConnection,
    CanLoadSchema,
    CanAccessBilling,
    CanMonitor
)

urlpatterns = [
    # Connection management (requires Create permission)
    path('connections/', ConnectionManagementAPIView.as_view(), name='connection-list'),
    path('connections/<int:connection_id>/', ConnectionManagementAPIView.as_view(), name='connection-detail'),
    
    # Update schema (requires Table permission)
    path('connections/<int:connection_id>/refresh-schema/', RefreshSchemaAPIView.as_view(), name='refresh-schema'),
    
    # Billing (requires Billing permission)
    path('billing/plans/', BillingPlanAPIView.as_view(), name='billing-plans'),
    
    # Monitoring (requires Monitor permission)
    path('connections/<int:connection_id>/status/', MigrationStatusAPIView.as_view(), name='migration-status'),
]
```

---

## Testing

### Unit Tests

**File**: `apps/elttool/v1/tests/test_rbac.py`

```python
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from ..models import Role, RoleAccess, Connection, Company

User = get_user_model()

class RBACTestCase(TestCase):
    def setUp(self):
        # Create company
        self.company = Company.objects.create(name="Test Company")
        
        # Create roles
        self.admin_role = Role.objects.create(role_name="Administrator")
        self.analyst_role = Role.objects.create(role_name="Analyst")
        
        # Create role accesses
        RoleAccess.objects.create(role=self.admin_role, type='Create')
        RoleAccess.objects.create(role=self.admin_role, type='Table')
        RoleAccess.objects.create(role=self.admin_role, type='Billing')
        RoleAccess.objects.create(role=self.admin_role, type='Monitor')
        
        RoleAccess.objects.create(role=self.analyst_role, type='Table')
        RoleAccess.objects.create(role=self.analyst_role, type='Billing')
        RoleAccess.objects.create(role=self.analyst_role, type='Monitor')
        # NO 'Create' access for analyst
        
        # Create users
        self.admin_user = User.objects.create_user(
            company_email="admin@test.com",
            roles=self.admin_role,
            cmp=self.company
        )
        
        self.analyst_user = User.objects.create_user(
            company_email="analyst@test.com",
            roles=self.analyst_role,
            cmp=self.company
        )
        
        self.client = APIClient()
    
    def test_admin_can_create_connection(self):
        """Administrator should be able to create connections."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post('/api/connections/', {
            'name': 'Test Connection',
            # ... other fields
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
    
    def test_analyst_cannot_create_connection(self):
        """Feature Analyst should NOT be able to create connections."""
        self.client.force_authenticate(user=self.analyst_user)
        response = self.client.post('/api/connections/', {
            'name': 'Test Connection',
            # ... other fields
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_analyst_can_refresh_schema(self):
        """Feature Analyst should be able to refresh schemas."""
        self.client.force_authenticate(user=self.analyst_user)
        # Create a connection first (by admin)
        connection = Connection.objects.create(
            connection_id=1,
            cmp=self.company,
            # ... other fields
        )
        response = self.client.post(f'/api/connections/{connection.connection_id}/refresh-schema/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_analyst_can_monitor_billing(self):
        """Feature Analyst should be able to view billing (read-only)."""
        self.client.force_authenticate(user=self.analyst_user)
        response = self.client.get('/api/billing/plans/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_analyst_cannot_update_billing(self):
        """Feature Analyst should NOT be able to update billing."""
        self.client.force_authenticate(user=self.analyst_user)
        response = self.client.post('/api/billing/plans/', {
            'plan': 'premium'
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
```

---

## Summary

This RBAC implementation provides:

1. **Role-Based Permissions**: Administrator (full access) and Feature Analyst (monitoring only)
2. **Granular Control**: Permission checks at view and endpoint levels
3. **Database-Driven**: Roles and permissions stored in database
4. **DRF Integration**: Uses Django REST Framework permission classes
5. **Frontend Support**: Permission flags in API responses for UI control

**Key Points**:
- Administrator has full access to all features
- Feature Analyst can only monitor, load schemas, and view billing (read-only)
- Feature Analyst cannot create, edit, or delete connections or destinations
- All permission checks are enforced at both API and view levels

