# 📊 Billing System Workflow Explanation

## 🎯 Overview

This document explains the complete billing workflow including:
- Billing Usage APIs (monthly/annual)
- Invoice generation system
- Scheduler implementation
- How to test each component

---

## 🏗️ System Architecture

### Core Components

1. **Models** (`apps/elttool/models.py`)
   - `Company` - Company information with billing dates
   - `Invoice` - Generated invoices with PDF content
   - `Data_Transfer` - Records transferred (used for billing calculation)
   - `Pricing` - Tiered pricing structure

2. **Services** (`apps/elttool/billing/services/`)
   - `billing_amount.py` - Calculates billing amounts using tiered pricing
   - `billing_invoice.py` - Generates invoices and handles business logic
   - `billing_pdf.py` - Creates PDF invoices from templates

3. **API Views** (`apps/elttool/v1/views.py`)
   - `BillingUsageAPIView` - Main billing dashboard data
   - `BillingMonthlyUsageAPIView` - Monthly usage charts
   - `BillingAnnualUsageAPIView` - Annual usage charts
   - `DownloadInvoiceAPIView` - Downloads generated PDF invoices

4. **Management Commands** (`apps/elttool/management/commands/`)
   - `generate_invoices.py` - Manual invoice generation
   - `scheduler.py` - Celery task scheduling

5. **Celery Tasks** (`apps/elttool/tasks.py`)
   - `generate_invoices_task` - Async invoice generation task

---

## 💰 Billing Calculation Logic

### Tiered Pricing Structure

The system uses a progressive tiered pricing model:

```
Tier 1: 1-500 units ($140 fixed)
Tier 2: 501-1000 units ($0.16 per unit)
Tier 3: 1001-5000 units ($0.14 per unit)
Tier 4: 5001+ units ($0.12 per unit)
```

**Example Calculation:**
```
Records: 1,250,000
Units: 1,250.00 (1,250,000 ÷ 1,000)

Cost:
- Tier 1: $140 (fixed for first 500 units)
- Tier 2: 500 units × $0.16 = $80
- Tier 3: 250 units × $0.14 = $35
Total: $255
```

### Minimum Threshold
- Minimum 1,000 records required for billing
- Connections with <1,000 records are not billed

### Connection Classification
- **Big connections**: ≥500,000 records (billed together)
- **Small connections**: 1,000-499,999 records (billed individually)

---

## 📈 Billing APIs

### 1. Billing Usage Dashboard API

**Endpoint:** `GET /api/v1/billing/usage/{cmp_id}/`

**Purpose:** Main billing dashboard showing current usage and invoice history

**Query Parameters:**
- `billing_period` (optional): `current-month` or `last-12-months`

**Response:**
```json
{
  "cmp_id": 123,
  "billing_period": "current-month",
  "current_month_labels": ["1", "2", ..., "31"],
  "current_month_billing": [0, 0, ..., 0],
  "invoices": [
    {
      "period": "January 2024",
      "total_usage": 1250.50,
      "amount": 255.75
    }
  ],
  "billing_details": [...],
  "last_12_months": {
    "labels": ["February 2023", ..., "January 2024"],
    "data": [150.00, 200.50, ..., 255.75]
  }
}
```

### 2. Monthly Usage API

**Endpoint:** `GET /api/v1/billing/{cmp_id}/monthly-usage/`

**Purpose:** Detailed monthly usage data with daily breakdown

**Query Parameters:**
- `year` (optional): Year to filter (default: current year)
- `month` (optional): Month to filter (default: current month)
- `connection_ids` (optional): Specific connections to include

**Response:**
```json
{
  "years": [2022, 2023, 2024],
  "daily_labels": ["1", "2", ..., "31"],
  "total_rec": [15000, 22000, ..., 18500],
  "selected_year": 2024,
  "selected_month": 2,
  "available_connections": [
    {"connection_id": 123, "connection_name": "Salesforce → Snowflake"}
  ],
  "selected_connection_ids": ["123"],
  "months": [[1, "January"], [2, "February"], ...]
}
```

### 3. Annual Usage API

**Endpoint:** `GET /api/v1/billing/{cmp_id}/annual-usage/`

**Purpose:** Yearly overview with monthly totals

**Query Parameters:**
- `year` (optional): Year to filter (default: current year)

**Response:**
```json
{
  "years": [2022, 2023, 2024],
  "selected_year": 2024,
  "monthly_labels": ["January", "February", ..., "December"],
  "monthly_total_rec_values": [1250000, 1350000, ..., 1100000],
  "cmp_id": 123
}
```

### 4. Download Invoice API

**Endpoint:** `GET /api/v1/download-invoice/{invoice_id}/`

**Purpose:** Download generated PDF invoice

**Response:** PDF file attachment

---

## 🧾 Invoice Generation Workflow

### Automatic Invoice Generation

**Process:**
1. System runs monthly (typically 1st of each month)
2. For each company:
   - Checks last generated invoice date
   - Calculates usage for billing period
   - Applies tiered pricing
   - Creates `Invoice` record
   - Generates PDF
   - Saves to database

**Business Rules:**
- Only bills connections with ≥1,000 records
- Big connections (≥500k records) billed together
- Small connections (1k-500k records) billed individually
- Custom date ranges supported for special billing periods

### Manual Generation

**Command:**
```bash
python manage.py generate_invoices
```

**Output:**
```
Starting invoice generation...
Successfully generated invoices
```

### Scheduled Generation (Celery)

**Setup Task:**
```bash
python manage.py scheduler
```

**Crontab Schedule:** Runs every minute (for testing)

**Celery Task:** `generate_invoices_task`

---

## ⏰ Scheduler Implementation

### Components

1. **Celery Beat Scheduler** (`apps/elttool/management/commands/scheduler.py`)
   - Sets up periodic tasks in Django admin
   - Configures crontab schedules

2. **Celery Worker Task** (`apps/elttool/tasks.py`)
   - `generate_invoices_task()` - Calls billing service

3. **Django-Celery-Beat Integration**
   - Stores schedules in database
   - Manages task execution timing

### How It Works

```
Django Startup
    ↓
Celery Beat starts
    ↓
Checks scheduled tasks
    ↓
Every minute: Triggers generate_invoices_task
    ↓
Calls generate_monthly_invoices()
    ↓
Processes all companies
    ↓
Creates invoices if needed
```

---

## 🧪 Testing Guide

### 1. Test Billing APIs

#### Prerequisites
- Company with connections
- Data transfers recorded
- Authenticated user with Billing/Admin role

#### Test Script
```bash
# Export auth token
export TOKEN="your-jwt-token"

# Test Billing Usage API
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/billing/usage/123/"

# Test Monthly Usage
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/billing/123/monthly-usage/?year=2024&month=2"

# Test Annual Usage
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/billing/123/annual-usage/?year=2024"

# Test Invoice Download
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/download-invoice/456/" \
  -o invoice_456.pdf
```

### 2. Test Manual Invoice Generation

```bash
# Generate invoices manually
python manage.py generate_invoices

# Expected output:
# Starting invoice generation...
# Found X companies
# Processing company: Company Name
# ✅ [Monthly] Invoice INV2402-200001 for Company Name | February 2024 | $255.75
# ✅ Total 1 invoice(s) generated for Company Name
# Monthly invoice generation completed
```

### 3. Test Scheduled Invoice Generation

```bash
# 1. Setup Celery scheduler
python manage.py scheduler

# 2. Start Celery worker (separate terminal)
celery -A datasyncher worker -l info

# 3. Start Celery beat (separate terminal)
celery -A datasyncher beat -l info

# 4. Monitor logs for task execution
# Look for: "Starting monthly invoice generation"
```

### 4. Test with Sample Data

#### Create Test Data
```python
# In Django shell
python manage.py shell

from apps.elttool.models import *
from django.utils import timezone
from datetime import timedelta

# Create company
company = Company.objects.create(
    cmp_name="Test Company",
    pricing="0.02",
    start_date=timezone.now(),
    end_date=timezone.now() + timedelta(days=14),
    is_trial=True,
    billing_start_date=timezone.now(),
    billing_end_date=timezone.now() + timedelta(days=30),
    plan='Standard'
)

# Create connection
source_config = SourceConfig.objects.create(...)
destination_config = DestinationConfig.objects.create(...)
connection = Connection.objects.create(
    cmp=company,
    src_config=source_config,
    dst_config=destination_config,
    status='A'
)

# Create data transfers
Data_Transfer.objects.create(
    connection=connection,
    table_name="test_table",
    start_time=timezone.now(),
    end_time=timezone.now(),
    new_rec=500000,
    total_rec=500000,
    mod_rec=0,
    del_rec=0
)
```

### 5. Verify Results

#### Check Database
```sql
-- View generated invoices
SELECT 
    invoice_number,
    billing_start_date,
    billing_end_date,
    total_amount,
    total_records,
    units,
    status
FROM invoice 
ORDER BY created_at DESC;

-- View pricing tiers
SELECT * FROM pricing ORDER BY unit_volume_range;

-- View data transfers for billing period
SELECT 
    dt.table_name,
    SUM(dt.new_rec + dt.mod_rec) as total_records
FROM data_transfer dt
JOIN migration_record mr ON dt.migration_record_id = mr.id
WHERE mr.connection_id = 123
  AND dt.start_time >= '2024-02-01'
  AND dt.start_time <= '2024-02-29'
GROUP BY dt.table_name;
```

#### Check Logs
```bash
# View Django logs
tail -f logs/django.log | grep -i "invoice\|billing"

# View Celery logs
tail -f logs/celery.log | grep -i "invoice"
```

---

## 🐛 Troubleshooting

### Common Issues

#### 1. No Invoices Generated
**Check:**
- Company has billing_start_date and billing_end_date
- Data transfers exist in billing period
- Record count ≥ 1,000
- Pricing tiers exist in database

#### 2. Wrong Amount Calculated
**Check:**
- Pricing table has correct tier ranges
- Effective dates cover billing period
- Record count calculation is correct

#### 3. PDF Generation Fails
**Check:**
- WeasyPrint is installed
- Template exists at `templates/documents/invoice.html`
- Static files are accessible
- Logo image exists

#### 4. Scheduler Not Running
**Check:**
- Redis is running
- Celery worker is started
- Celery beat is started
- Task is registered in Django admin

### Debug Commands

```bash
# Check Celery tasks
celery -A datasyncher inspect registered

# Check active tasks
celery -A datasyncher inspect active

# Check scheduled tasks
celery -A datasyncher inspect scheduled

# List periodic tasks
python manage.py shell
from django_celery_beat.models import PeriodicTask
PeriodicTask.objects.all().values('name', 'enabled', 'last_run_at')
```

---

## 📊 Monitoring

### Key Metrics to Track

1. **Invoice Generation Success Rate**
   ```sql
   SELECT 
     DATE(created_at) as date,
     COUNT(*) as total_invoices,
     COUNT(CASE WHEN status = 'GENERATED' THEN 1 END) as successful,
     COUNT(CASE WHEN status = 'FAILED' THEN 1 END) as failed
   FROM invoice 
   GROUP BY DATE(created_at)
   ORDER BY date DESC;
   ```

2. **Revenue Recognition**
   ```sql
   SELECT 
     DATE_TRUNC('month', billing_end_date) as month,
     SUM(total_amount) as revenue,
     COUNT(*) as invoices
   FROM invoice 
   WHERE status = 'GENERATED'
   GROUP BY DATE_TRUNC('month', billing_end_date)
   ORDER BY month DESC;
   ```

3. **Usage Patterns**
   ```sql
   SELECT 
     cmp_id,
     AVG(units) as avg_monthly_usage,
     MAX(units) as peak_usage,
     MIN(units) as min_usage
   FROM invoice 
   WHERE status = 'GENERATED'
   GROUP BY cmp_id;
   ```

---

## 🔐 Security & Permissions

### Required Permissions

- `IsAuthenticated` - User must be logged in
- `IsBillingOrAdmin` - User must have Billing or Admin role

### Data Protection

- Company financial data is protected by role-based access
- Invoice PDFs contain sensitive billing information
- API endpoints validate company ownership

---

## 🚀 Production Considerations

### Performance Optimization

1. **Database Indexes**
   ```sql
   CREATE INDEX idx_invoice_cmp_period ON invoice(cmp_id, billing_end_date);
   CREATE INDEX idx_datatransfer_connection_time ON data_transfer(migration_record_id, start_time);
   ```

2. **Caching**
   - Cache pricing tiers (rarely change)
   - Cache company billing data
   - Use Redis for session storage

3. **Background Processing**
   - Invoice generation runs async via Celery
   - PDF generation happens in background
   - Large data calculations are batched

### Scalability

- Horizontal scaling with multiple Celery workers
- Redis for distributed locking
- PostgreSQL for job persistence
- Load balancing for API requests

---

## 📚 Related Documentation

- `docs/FULL_API_SPECIFICATIONS.md` - Complete API reference
- `docs/SCHEDULER_IMPLEMENTATION_COMPLETE.md` - Scheduler details
- [RBAC_IMPLEMENTATION.md](../rbac/RBAC_IMPLEMENTATION.md) - Role-based access control
- `templates/documents/invoice.html` - Invoice template

---

## ✅ Quick Reference

### Management Commands
```bash
# Generate invoices manually
python manage.py generate_invoices

# Setup scheduler
python manage.py scheduler

# View periodic tasks
python manage.py shell -c "from django_celery_beat.models import PeriodicTask; print([t.name for t in PeriodicTask.objects.all()])"
```

### API Endpoints
```
GET /api/v1/billing/usage/{cmp_id}/              # Billing dashboard
GET /api/v1/billing/{cmp_id}/monthly-usage/      # Monthly usage
GET /api/v1/billing/{cmp_id}/annual-usage/       # Annual usage
GET /api/v1/download-invoice/{id}/               # Download invoice
```

### Environment Variables
```bash
# Required for scheduler
REDIS_URL=redis://localhost:6379/0

# For PDF generation
STATIC_ROOT=/path/to/static/files
```

---

*Last Updated: February 2026*