# 🧪 Postman Testing Guide for Billing APIs

## 📋 Setup Instructions

### 1. Environment Variables
Create a Postman environment with these variables:
- `base_url`: `http://localhost:8000` (or your deployed URL)
- `auth_token`: Your JWT token (get from login API)

### 2. Authentication
First, get your authentication token:

**Login Request:**
```
POST {{base_url}}/api/v1/auth/login/
Content-Type: application/json

{
    "email": "your_email@example.com",
    "password": "your_password"
}
```

**Response:**
```json
{
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "user": {
        "id": 123,
        "email": "your_email@example.com"
    }
}
```

Copy the token to your `auth_token` environment variable.

---

## 💰 Billing API Endpoints

### 1. Billing Usage Dashboard
**Main billing dashboard with usage data and invoice history**

```
GET {{base_url}}/api/v1/billing/usage/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
```

**Parameters:**
- `cmp_id` (path): Company ID (e.g., 1, 2, 3)
- `billing_period` (query, optional): `current-month` or `last-12-months`

**Example Requests:**
```
# Current month view
GET {{base_url}}/api/v1/billing/usage/1/

# Last 12 months view  
GET {{base_url}}/api/v1/billing/usage/1/?billing_period=last-12-months
```

**Expected Response:**
```json
{
    "cmp_id": 1,
    "billing_period": "current-month",
    "current_month_labels": ["1", "2", "3", ..., "31"],
    "current_month_billing": [0, 0, 5000, ..., 0],
    "invoices": [
        {
            "period": "January 2024",
            "total_usage": 1250.50,
            "amount": 255.75
        }
    ],
    "billing_details": [
        {
            "period": "January 2024", 
            "total_usage": 1250.50,
            "amount": 255.75
        }
    ],
    "last_12_months": {
        "labels": ["February 2023", "March 2023", ..., "January 2024"],
        "data": [150.00, 200.50, ..., 255.75]
    }
}
```

---

### 2. Monthly Usage Details
**Detailed daily breakdown for a specific month**

```
GET {{base_url}}/api/v1/billing/{{cmp_id}}/monthly-usage/
Authorization: Bearer {{auth_token}}
```

**Query Parameters:**
- `year` (optional): Year (default: current year)
- `month` (optional): Month 1-12 (default: current month)
- `connection_ids` (optional): Filter by specific connection IDs

**Example Requests:**
```
# Current month
GET {{base_url}}/api/v1/billing/1/monthly-usage/

# Specific month/year
GET {{base_url}}/api/v1/billing/1/monthly-usage/?year=2024&month=2

# Specific connections
GET {{base_url}}/api/v1/billing/1/monthly-usage/?year=2024&month=2&connection_ids=123&connection_ids=456
```

**Expected Response:**
```json
{
    "years": [2022, 2023, 2024],
    "daily_labels": ["1", "2", "3", ..., "29"],
    "total_rec": [15000, 22000, 18500, ..., 21000],
    "selected_year": 2024,
    "selected_month": 2,
    "available_connections": [
        {
            "connection_id": 123,
            "connection_name": "Salesforce → Snowflake"
        },
        {
            "connection_id": 456, 
            "connection_name": "MySQL → Redshift"
        }
    ],
    "selected_connection_ids": ["123", "456"],
    "months": [[1, "January"], [2, "February"], [3, "March"], ...]
}
```

---

### 3. Annual Usage Overview
**Yearly summary with monthly totals**

```
GET {{base_url}}/api/v1/billing/{{cmp_id}}/annual-usage/
Authorization: Bearer {{auth_token}}
```

**Query Parameters:**
- `year` (optional): Year (default: current year)

**Example Requests:**
```
# Current year
GET {{base_url}}/api/v1/billing/1/annual-usage/

# Specific year
GET {{base_url}}/api/v1/billing/1/annual-usage/?year=2023
```

**Expected Response:**
```json
{
    "years": [2022, 2023, 2024],
    "selected_year": 2024,
    "monthly_labels": ["January", "February", "March", ..., "December"],
    "monthly_total_rec_values": [1250000, 1350000, 1100000, ..., 1450000],
    "cmp_id": 1
}
```

---

### 4. Billing Chart Data
**Usage chart data for visualization**

```
GET {{base_url}}/api/v1/billing/chart/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
```

**Example Request:**
```
GET {{base_url}}/api/v1/billing/chart/1/
```

**Expected Response:**
```json
{
    "cmp_id": 1,
    "labels": ["Jan", "Feb", "Mar", ..., "Dec"],
    "data": [1250000, 1350000, 1100000, ..., 1450000],
    "title": "Monthly Data Transfer"
}
```

---

### 5. Billing Summary
**High-level billing summary**

```
GET {{base_url}}/api/v1/billing/summary/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
```

**Example Request:**
```
GET {{base_url}}/api/v1/billing/summary/1/
```

**Expected Response:**
```json
{
    "cmp_id": 1,
    "current_month_labels": ["1", "2", ..., "31"],
    "current_month_billing": [0, 5000, ..., 0],
    "billing_period": "February 2024",
    "invoices": [
        {
            "period": "January 2024",
            "total_usage": 1250.50,
            "amount": 255.75
        }
    ],
    "billing_details": [
        {
            "period": "January 2024",
            "total_usage": 1250.50, 
            "amount": 255.75
        }
    ]
}
```

---

### 6. Download Invoice (PDF)
**Download generated invoice PDF**

```
GET {{base_url}}/api/v1/download-invoice/{{invoice_id}}/
Authorization: Bearer {{auth_token}}
```

**Example Request:**
```
GET {{base_url}}/api/v1/download-invoice/456/
```

**Expected Response:**
- Content-Type: `application/pdf`
- Content-Disposition: `attachment; filename="invoice_INV2402-200001.pdf"`
- Binary PDF content

**Note:** This returns the actual PDF file, not JSON.

---

### 7. Update Connection Usage Chart
**Refresh connection usage data**

```
GET {{base_url}}/api/v1/billing/usage/update-connections/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
```

**Example Request:**
```
GET {{base_url}}/api/v1/billing/usage/update-connections/1/
```

**Expected Response:**
```json
{
    "message": "Connection usage data updated successfully",
    "updated_connections": 3
}
```

---

### 8. Billing Plan Information
**Get current billing plan and trial status**

```
GET {{base_url}}/api/v1/billing/plan/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
```

**Example Request:**
```
GET {{base_url}}/api/v1/billing/plan/1/
```

**Expected Response:**
```json
{
    "cmp_id": 1,
    "plan": "Standard",
    "employee_range": "11-75",
    "is_trial": false,
    "trial_pending": false,
    "trial_expired": true,
    "subscription_exists": true,
    "days_remaining": 0,
    "trial_start_date": "2024-01-01T00:00:00Z",
    "trial_end_date": "2024-01-15T00:00:00Z",
    "billing_start_date": "2024-01-16T00:00:00Z",
    "billing_end_date": "2024-02-16T00:00:00Z",
    "billing_plans": ["Free", "Discounted", "Standard"],
    "warning_message": null
}
```

**Select/Change Billing Plan (POST):**

```
POST {{base_url}}/api/v1/billing/plan/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
Content-Type: application/json

{
    "selected_plan": "Standard"
}
```

**Example Request:**
```
POST {{base_url}}/api/v1/billing/plan/1/
Content-Type: application/json

{
    "selected_plan": "Standard"
}
```

**Expected Response (Success):**
```json
{
    "message": "Redirect to payment",
    "redirect_to": "/billing/payment",
    "plan": "Standard",
    "amount": "2000.00",
    "amount_display": "$2,000.00",
    "requires_payment": true,
    "success": true
}
```

**Expected Response (Free Plan - Trial Active):**
```json
{
    "message": "Free plan activated successfully",
    "plan": "Free",
    "is_trial": true,
    "redirect_to": "home",
    "success": true
}
```

**Expected Response (Errors):**
```json
{
    "error": "You are already on a trial version.",
    "success": false,
    "days_remaining": 5
}
```

**Valid Plan Values:**
- `"Free"` - Free trial plan
- `"Discounted"` - Discounted paid plan ($1,000)
- `"Standard"` - Standard paid plan ($2,000)

---

### 9. Credit Card Billing
**Payment and billing information**

**GET - Retrieve Payment Information:**
```
GET {{base_url}}/api/v1/billing/payment/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
```

**Query Parameters:**
- `selected_plan` (optional): Plan name

**Example Requests:**
```
# Get payment info
GET {{base_url}}/api/v1/billing/payment/1/

# With selected plan
GET {{base_url}}/api/v1/billing/payment/1/?selected_plan=Standard
```

**Expected Response:**
```json
{
    "cmp_id": 1,
    "selected_plan": "Standard",
    "amount": "2000.00",
    "amount_display": "$2,000.00",
    "payment_methods": ["credit_card", "bank_transfer"],
    "billing_address": {
        "company_name": "Test Company",
        "address_line1": "123 Main St",
        "city": "New York",
        "state": "NY",
        "zip_code": "10001"
    }
}
```

**POST - Process Payment:**
```
POST {{base_url}}/api/v1/billing/payment/{{cmp_id}}/
Authorization: Bearer {{auth_token}}
Content-Type: application/json

{
    "selected_plan": "Standard",
    "payment_method": "credit_card",
    "card_number": "4111111111111111",
    "expiry_month": "12",
    "expiry_year": "2027",
    "cvv": "123"
}
```

**Example Request:**
```
POST {{base_url}}/api/v1/billing/payment/1/
Content-Type: application/json

{
    "selected_plan": "Standard",
    "payment_method": "credit_card",
    "card_number": "4111111111111111",
    "expiry_month": "12",
    "expiry_year": "2027",
    "cvv": "123"
}
```

**Expected Response (Success):**
```json
{
    "message": "Payment processed successfully",
    "plan": "Standard",
    "amount": "2000.00",
    "success": true
}
```

**Expected Response (Validation Error):**
```json
{
    "error": "Invalid plan for payment processing"
}
```

**Valid Plans for Payment:**
- `"Discounted"` - $1,000
- `"Standard"` - $2,000

**Note:** Free plan selection is handled via the Billing Plan API POST endpoint.

---

## 🔧 Testing Workflow

### Step 1: Authentication
1. Send login request to get JWT token
2. Set `auth_token` environment variable

### Step 2: Test Basic Endpoints
1. `GET /billing/usage/{cmp_id}/` - Verify dashboard loads
2. `GET /billing/{cmp_id}/monthly-usage/` - Check monthly data
3. `GET /billing/{cmp_id}/annual-usage/` - Verify annual overview

### Step 3: Test with Parameters
1. Try different years/months in monthly usage
2. Test filtering by connection IDs
3. Try different billing periods in usage API

### Step 4: Test Special Cases
1. `GET /download-invoice/{id}/` - With existing invoice ID
2. `GET /billing/plan/{cmp_id}/` - Check plan information
3. `GET /billing/payment/{cmp_id}/` - Verify payment details

---

## 🛠️ Common Test Scenarios

### Scenario 1: New Company (No Billing Data)
```
GET /billing/usage/999/
Expected: Empty data arrays, no invoices
```

### Scenario 2: Company with Invoices
```
GET /billing/usage/1/
Expected: Invoice history, usage data
```

### Scenario 3: Specific Time Period
```
GET /billing/1/monthly-usage/?year=2023&month=12
Expected: December 2023 data
```

### Scenario 4: Invalid Company
```
GET /billing/usage/99999/
Expected: 404 Not Found
```

### Scenario 5: Unauthorized Access
```
GET /billing/usage/1/ (without auth token)
Expected: 401 Unauthorized
```

---

## 📊 Expected Response Codes

| Endpoint | Success | Client Error | Server Error |
|----------|---------|--------------|--------------|
| All GET endpoints | 200 OK | 400/401/403/404 | 500 |

---

## 🎯 Tips for Testing

1. **Start with company ID 1** - Usually the first test company
2. **Check permissions** - User must have Billing or Admin role
3. **Verify data exists** - Some endpoints need existing invoices/data transfers
4. **Test edge cases** - Invalid IDs, missing parameters, wrong date formats
5. **Monitor logs** - Check `logs/django.log` for debugging information

---

*Last Updated: February 2026*