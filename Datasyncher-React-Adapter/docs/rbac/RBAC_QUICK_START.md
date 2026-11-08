# RBAC Quick Start Guide

## 🚀 **Get Started in 5 Minutes**

This guide will help you quickly test the RBAC system and start using it.

---

## Step 1: Start the Server

```powershell
# Set encoding for Windows (prevents emoji errors)
$env:PYTHONIOENCODING="utf-8"

# Start Django development server
python manage.py runserver
```

Server will start at: `http://localhost:8000`

---

## Step 2: Create Your First Admin User

If you don't have an admin user yet, sign up:

```bash
curl -X POST http://localhost:8000/api/v1/authentication/ \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "Admin",
    "last_name": "User",
    "company_name": "My Company",
    "employee_range": "11-75",
    "company_email": "admin@mycompany.com",
    "password": "SecurePassword123!"
  }'
```

**Response:**
```json
{
  "message": "Registration successful",
  "user": {
    "id": 1,
    "role": "Administrator",
    "role_id": 1,
    "account_id": 1
  },
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

✅ **First user is automatically assigned Administrator role!**

---

## Step 3: Login and Get Your Token

```bash
curl -X POST http://localhost:8000/api/v1/authentication/ \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin@mycompany.com",
    "password": "SecurePassword123!"
  }'
```

**Save the `access_token` from the response!**

---

## Step 4: Test Your Admin Access

### Get Your User Info
```bash
curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  http://localhost:8000/api/v1/rbac/me/
```

**Response:**
```json
{
  "user": {
    "user_id": 1,
    "first_name": "Admin",
    "last_name": "User",
    "company_email": "admin@mycompany.com",
    "role": "Administrator",
    "role_id": 1,
    "account_id": 1,
    "company_name": "My Company",
    "is_active": true
  }
}
```

### List All Users
```bash
curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  http://localhost:8000/api/v1/rbac/users/
```

### List Available Roles
```bash
curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  http://localhost:8000/api/v1/rbac/roles/
```

**Response:**
```json
{
  "roles": [
    {"role_id": 1, "role_name": "Administrator"},
    {"role_id": 2, "role_name": "Analyst"},
    {"role_id": 3, "role_name": "Billing"}
  ]
}
```

---

## Step 5: Create an Analyst User

```bash
curl -X POST http://localhost:8000/api/v1/rbac/users/ \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "Jane",
    "last_name": "Analyst",
    "company_email": "analyst@mycompany.com",
    "password": "SecurePassword123!",
    "role_id": 2
  }'
```

**Response:**
```json
{
  "message": "User created successfully",
  "user": {
    "user_id": 2,
    "first_name": "Jane",
    "last_name": "Analyst",
    "company_email": "analyst@mycompany.com",
    "role": "Analyst",
    "role_id": 2
  }
}
```

---

## Step 6: Create a Billing User

```bash
curl -X POST http://localhost:8000/api/v1/rbac/users/ \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "Bob",
    "last_name": "Billing",
    "company_email": "billing@mycompany.com",
    "password": "SecurePassword123!",
    "role_id": 3
  }'
```

---

## Step 7: Test Different Role Access

### Login as Analyst
```bash
curl -X POST http://localhost:8000/api/v1/authentication/ \
  -H "Content-Type: application/json" \
  -d '{
    "username": "analyst@mycompany.com",
    "password": "SecurePassword123!"
  }'
```

### Try to Create a User (Should Fail - 403 Forbidden)
```bash
curl -X POST http://localhost:8000/api/v1/rbac/users/ \
  -H "Authorization: Bearer ANALYST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "Test",
    "last_name": "User",
    "company_email": "test@mycompany.com",
    "password": "password123",
    "role_id": 2
  }'
```

**Expected Response:**
```json
{
  "error": "FORBIDDEN",
  "message": "You do not have permission to perform this action. Administrator role required."
}
```

✅ **RBAC is working! Analyst cannot create users.**

---

## Step 8: Update a User's Role

```bash
curl -X PATCH http://localhost:8000/api/v1/rbac/users/2/ \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role_id": 3
  }'
```

**Response:**
```json
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

## 🎯 **Permission Testing Matrix**

Test these scenarios to verify RBAC is working:

| Action | Admin | Analyst | Billing | Expected Result |
|--------|-------|---------|---------|-----------------|
| List users | ✅ | ❌ | ❌ | Admin: 200, Others: 403 |
| Create user | ✅ | ❌ | ❌ | Admin: 201, Others: 403 |
| Update role | ✅ | ❌ | ❌ | Admin: 200, Others: 403 |
| Get own info | ✅ | ✅ | ✅ | All: 200 |
| List roles | ✅ | ❌ | ❌ | Admin: 200, Others: 403 |

---

## 🔧 **Applying RBAC to Your Views**

### Example 1: Admin-Only View

```python
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.elttool.v1.permissions import IsAdministrator

class UserManagementView(APIView):
    permission_classes = [IsAuthenticated, IsAdministrator]
    
    def get(self, request):
        # Only administrators can access
        return Response({"users": [...]})
```

### Example 2: Analyst or Admin View

```python
from apps.elttool.v1.permissions import IsAnalystOrAdmin

class ConnectorListView(APIView):
    permission_classes = [IsAuthenticated, IsAnalystOrAdmin]
    
    def get(self, request):
        # Analysts and Administrators can view
        return Response({"connectors": [...]})
```

### Example 3: Read for Analyst, Write for Admin

```python
from apps.elttool.v1.permissions import IsAdminForWrite

class ConnectorView(APIView):
    permission_classes = [IsAuthenticated, IsAdminForWrite]
    
    def get(self, request):
        # Analysts and Administrators can view
        return Response({"connector": {...}})
    
    def post(self, request):
        # Only Administrators can create
        return Response({"message": "Created"})
```

---

## 📱 **Frontend Integration**

### React Example

```javascript
// After login, store the role
const loginUser = async (username, password) => {
  const response = await fetch('/api/v1/authentication/', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password})
  });
  
  const data = await response.json();
  
  // Store user info
  localStorage.setItem('access_token', data.access_token);
  localStorage.setItem('user_role', data.user.role);
  localStorage.setItem('user_role_id', data.user.role_id);
  
  return data;
};

// Conditional rendering based on role
const Dashboard = () => {
  const userRole = localStorage.getItem('user_role');
  
  return (
    <div>
      {/* Show to all authenticated users */}
      <h1>Dashboard</h1>
      
      {/* Show only to Admin and Analyst */}
      {['Administrator', 'Analyst'].includes(userRole) && (
        <ConnectorsList />
      )}
      
      {/* Show only to Admin */}
      {userRole === 'Administrator' && (
        <UserManagement />
      )}
      
      {/* Show only to Admin and Billing */}
      {['Administrator', 'Billing'].includes(userRole) && (
        <BillingPage />
      )}
    </div>
  );
};
```

---

## 🐛 **Troubleshooting**

### Issue: "Role not found" error during signup

**Solution:** Run the init_roles command:
```bash
$env:PYTHONIOENCODING="utf-8"
python manage.py init_roles
```

### Issue: Unicode encoding error on Windows

**Solution:** Set the encoding environment variable:
```powershell
$env:PYTHONIOENCODING="utf-8"
```

### Issue: 403 Forbidden for admin user

**Solution:** Check the user's role in the database:
```python
from apps.elttool.models import User
user = User.objects.get(company_email='admin@mycompany.com')
print(f"Role: {user.roles.role_name if user.roles else 'None'}")
```

---

## 📚 **More Resources**

- **Complete API Documentation:** [RBAC_IMPLEMENTATION.md](./RBAC_IMPLEMENTATION.md)
- **Developer Reference:** [RBAC_QUICK_REFERENCE.md](./RBAC_QUICK_REFERENCE.md)
- **Implementation Summary:** [RBAC_SUMMARY.md](./RBAC_SUMMARY.md)
- **Final Status:** [RBAC_FINAL_STATUS.md](./RBAC_FINAL_STATUS.md)

---

## ✅ **Success Checklist**

- [ ] Server running on `http://localhost:8000`
- [ ] Admin user created and can login
- [ ] Admin can list users
- [ ] Admin can create Analyst user
- [ ] Admin can create Billing user
- [ ] Analyst cannot create users (403 error)
- [ ] Billing cannot create users (403 error)
- [ ] Role information included in login response

---

**🎉 Congratulations! Your RBAC system is ready to use!**

For production deployment, remember to:
1. Use strong passwords
2. Enable HTTPS
3. Set proper CORS settings
4. Use environment variables for secrets
5. Test all permission scenarios
