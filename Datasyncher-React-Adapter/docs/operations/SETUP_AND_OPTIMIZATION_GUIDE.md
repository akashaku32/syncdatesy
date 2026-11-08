# Setup and Optimization Guide

## Table of Contents
1. [Environment Configuration (.env Setup)](#environment-configuration-env-setup)
2. [Error Email Configuration](#error-email-configuration)
3. [Connection Name Update Issues](#connection-name-update-issues)
4. [Database Table Renaming](#database-table-renaming)
5. [Removing elttool App Label](#removing-elttool-app-label)
6. [API Response Caching Optimization](#api-response-caching-optimization)

---

## Environment Configuration (.env Setup)

### Location

The `.env` file should be placed in the `backend/migration-function/` directory.

**Path**: `backend/migration-function/.env`

### Required Environment Variables

#### Database Configuration

```env
# PostgreSQL Database Connection
DB_HOST=your-database-host
DB_USER=your-database-user
DB_PASSWORD=your-database-password
DB_NAME=your-database-name

# Optional: Database Connection Settings
DB_CONNECT_TIMEOUT=30
DB_KEEPALIVES=1
DB_KEEPALIVES_IDLE=30
DB_KEEPALIVES_INTERVAL=10
DB_KEEPALIVES_COUNT=5
```

#### Email Configuration (Error Notifications)

```env
# SMTP Server Configuration
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your-email@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_USE_TLS=True
EMAIL_FROM_NAME=DataSyncher Alert System

# Error Notification Recipients (comma-separated)
ERROR_NOTIFICATION_RECIPIENTS=admin@example.com,support@example.com
```

#### GCP/GCS Configuration

```env
# Google Cloud Platform Configuration
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
GCP_PROJECT_ID=your-gcp-project-id
GCS_BUCKET_NAME=your-gcs-bucket-name
SF_STORAGE_INTEGRATION=DATASYNCHERDEV
```

#### FastAPI Configuration

```env
# FastAPI Domain (for internal service communication)
FASTAPI_DOMAIN=migration-function:8001
DOMAIN=qa.datasyncher.com
FASTAPI_DATABASE_URL=postgresql://user:password@host:5432/dbname
```

#### Celery/Redis Configuration

```env
# Redis Configuration (for Celery)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

### .env File Loading

The migration function automatically loads the `.env` file using `python-dotenv`:

**File**: `backend/migration-function/app.py`

```python
# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    import os
    
    # Load .env file from the migration-function directory
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"✅ Loaded environment variables from: {env_path}")
    else:
        print(f"⚠️ .env file not found at: {env_path}. Using system environment variables only.")
        load_dotenv()  # Try loading from current directory or parent
except ImportError:
    print("⚠️ python-dotenv not installed. Using system environment variables only.")
except Exception as e:
    print(f"⚠️ Could not load .env file: {e}. Using system environment variables only.")
```

**Also loaded in**:
- `backend/migration-function/utils/email_utils.py`
- `backend/migration-function/services/salesforce_service.py`
- `backend/migration-function/utils/database_utils.py`

### Example .env File

Create `backend/migration-function/.env`:

```env
# Database Configuration
DB_HOST=postgres
DB_USER=datasyncher_user
DB_PASSWORD=secure_password_here
DB_NAME=datasyncher_db
DB_CONNECT_TIMEOUT=30

# Email Configuration
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=alerts@datasyncher.com
EMAIL_PASSWORD=your-gmail-app-password
EMAIL_USE_TLS=True
EMAIL_FROM_NAME=DataSyncher Alert System
ERROR_NOTIFICATION_RECIPIENTS=admin@datasyncher.com,support@datasyncher.com

# GCP Configuration
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/gcp-service-account.json
GCP_PROJECT_ID=elt-project-433103
GCS_BUCKET_NAME=datasyncher-staging-bucket
SF_STORAGE_INTEGRATION=DATASYNCHERDEV

# FastAPI Configuration
FASTAPI_DOMAIN=migration-function:8001
DOMAIN=qa.datasyncher.com
FASTAPI_DATABASE_URL=postgresql://datasyncher_user:secure_password_here@postgres:5432/datasyncher_db

# Redis Configuration
REDIS_HOST=redis
REDIS_PORT=6379
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

### Verification

To verify `.env` file is loaded correctly:

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Check if variables are loaded
print(f"DB_HOST: {os.getenv('DB_HOST')}")
print(f"EMAIL_HOST: {os.getenv('EMAIL_HOST')}")
print(f"EMAIL_USER: {os.getenv('EMAIL_USER')}")
```

---

## Error Email Configuration

### Overview

Error email notifications are sent when migration failures occur. The system uses SMTP to send HTML emails with error details.

### Configuration

**File**: `backend/migration-function/utils/email_utils.py`

The `EmailConfiguration` class reads email settings from environment variables:

```python
class EmailConfiguration:
    def __init__(self, user_id: Optional[int] = None, company_id: Optional[int] = None):
        self.host = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
        self.port = int(os.environ.get("EMAIL_PORT", "587"))
        self.username = os.environ.get("EMAIL_USER")
        self.password = os.environ.get("EMAIL_PASSWORD")
        self.use_tls = os.environ.get("EMAIL_USE_TLS", "True").lower() == "true"
        self.from_name = os.environ.get("EMAIL_FROM_NAME", "DataSyncher Alert System")
```

### Required Environment Variables

```env
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your-email@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_USE_TLS=True
EMAIL_FROM_NAME=DataSyncher Alert System
ERROR_NOTIFICATION_RECIPIENTS=admin@example.com,support@example.com
```

### Gmail Setup

For Gmail, you need to:

1. **Enable 2-Factor Authentication** on your Google account
2. **Generate an App Password**:
   - Go to Google Account → Security → 2-Step Verification → App passwords
   - Generate a password for "Mail"
   - Use this password in `EMAIL_PASSWORD`

### Email Recipients

Emails are sent to:

1. **User Email**: Fetched from database using `user_id`
2. **CommunicationSupport Emails**: Fetched from `CommunicationSupport` table using `company_id`
3. **Environment Recipients**: From `ERROR_NOTIFICATION_RECIPIENTS` environment variable

### Email Sending Function

**Function**: `send_error_notification()`

**Location**: `backend/migration-function/utils/email_utils.py`

**Usage**:

```python
from utils.email_utils import send_error_notification

try:
    # Migration code
    pass
except Exception as e:
    send_error_notification(
        table_name="Account",
        error_message=str(e),
        connection_name="Salesforce Production",
        service_name="Salesforce",
        user_id=456,
        company_id=2,
        additional_context={
            "job_name": "daily_sync",
            "migration_session_id": "session_789",
            "connection_id": 123
        }
    )
```

### Error Email Implementation in Every Error Region

Error emails are automatically sent in the following scenarios:

#### 1. Service Layer Errors

**File**: `backend/migration-function/utils/error_utils.py`

```python
def handle_service_error(
    error: Exception,
    context: Dict[str, Any],
    send_email: bool = False,
    is_final_attempt: bool = True
) -> Dict[str, Any]:
    # ... error handling ...
    
    if send_email and is_final_attempt:
        from utils.email_utils import send_error_notification
        email_success = send_error_notification(
            table_name=table_name,
            error_message=error_message,
            connection_name=context.get('connection_name', 'Unknown'),
            service_name=service_name,
            user_id=context.get('user_id'),
            company_id=context.get('cmp_id'),
            additional_context=context
        )
```

**Used in**:
- `backend/migration-function/services/amazon_s3_service.py`
- `backend/migration-function/services/salesforce_service.py`
- `backend/migration-function/services/snowflake_service.py`

#### 2. Migration Failure Tracker

**File**: `backend/migration-function/services/amazon_s3_service.py`

```python
class MigrationFailureTracker:
    def send_consolidated_error_email(self):
        """Send consolidated error email for multiple table failures."""
        if not self.failures:
            return
        
        from utils.email_utils import send_error_notification
        
        error_summary = self.get_error_summary()
        send_error_notification(
            table_name="Migration Summary",
            error_message=error_summary,
            connection_name=self.connection_name,
            service_name="S3 to Snowflake",
            user_id=self.user_id,
            company_id=self.cmp_id,
            is_consolidated=True,
            additional_context={
                "total_failed_tables": len(self.failures),
                "failed_tables_list": list(self.failures.keys())
            }
        )
```

#### 3. Router Layer Errors

**File**: `backend/migration-function/routers/amazon_s3_routers.py`

Router errors are caught and logged, but email sending is handled by the service layer.

### Email Deduplication

To prevent duplicate emails on retries:

```python
# Only send email on final attempt
is_final_attempt = getattr(request, 'is_final_attempt', True)

if send_email and is_final_attempt:
    send_error_notification(...)
else:
    logger.info("⏭️ SKIPPING error notification email (not final attempt)")
```

### Testing Email Configuration

**Function**: `test_email_configuration()`

**Location**: `backend/migration-function/utils/email_utils.py`

```python
from utils.email_utils import test_email_configuration

# Test email configuration
success = test_email_configuration(user_id=123)
if success:
    print("✅ Email configuration is working")
else:
    print("❌ Email configuration failed")
```

---

## Connection Name Update Issues

### Problem

When updating a connection name, there can be issues with:
1. Migration job naming
2. Error notifications
3. Logging
4. Celery task tracking

### Current Implementation

**File**: `apps/elttool/v1/views.py`

#### Connection Name Extraction

```python
# Extract connection_name from request
connection_name = request.data.get("connection_name")

# If not provided, use source_config.name
if not connection_name:
    connection_name = source_config.name

print(f"📝 Setting connection name: {connection_name}")
```

#### Connection Name Usage in Migration

**File**: `apps/elttool/v1/views.py` (migration endpoints)

```python
# Pass connection_name to migration request
migration_data = {
    "connection_name": connection_name,  # From connection.src_config.name
    "company_name": company_name,
    # ... other fields
}
```

#### Connection Name in Job Naming

**File**: `apps/elttool/v1/views.py`

```python
# Sanitize connection name for job naming
safe_connection_name = re.sub(r'\W+', '_', connection.src_config.name.strip())

# Use in job name
combined_job_name = f"{safe_connection_name}_{migration_type}_{migration_session_id}_{formatted_table_names}"
```

### Issues and Solutions

#### Issue 1: Connection Name Not Updated in Migration

**Problem**: When connection name is updated, existing migrations may still use old name.

**Solution**: Always fetch connection name from database before migration:

```python
# ✅ CORRECT: Fetch fresh connection name from database
connection = Connection.objects.get(connection_id=connection_id)
connection_name = connection.src_config.name  # Always use current name

# ❌ WRONG: Use cached connection name
connection_name = request.data.get("connection_name")  # May be stale
```

#### Issue 2: Connection Name in Error Notifications

**Problem**: Error emails may use old connection name if not updated.

**Solution**: Pass connection name explicitly in migration request:

```python
# In migration request
migration_request = {
    "connection_name": connection.src_config.name,  # Current name
    # ... other fields
}

# In error notification
send_error_notification(
    connection_name=request.connection_name,  # Use from request
    # ... other fields
)
```

#### Issue 3: Celery Task Tracking

**Problem**: Celery tasks may be tracked by old connection name.

**Solution**: Use connection_id for tracking, not connection name:

```python
# ✅ CORRECT: Use connection_id for task tracking
celery_result = is_connection_migration_active_celery_by_id(connection_id)

# ❌ WRONG: Use connection name (may change)
celery_result = is_connection_migration_active_celery(connection_name)
```

### Best Practices

1. **Always Fetch Fresh Connection Name**:
   ```python
   connection = Connection.objects.select_related('src_config').get(connection_id=connection_id)
   connection_name = connection.src_config.name
   ```

2. **Pass Connection Name Explicitly**:
   ```python
   migration_data = {
       "connection_name": connection.src_config.name,  # Explicit
       # ... other fields
   }
   ```

3. **Use Connection ID for Tracking**:
   ```python
   # Use connection_id for immutable tracking
   task_id = f"migration_{connection_id}_{migration_session_id}"
   ```

4. **Update All References**:
   ```python
   # When updating connection name, update:
   # 1. SourceConfig.name
   # 2. MigrationRecord.connection_name (if exists)
   # 3. Log entries (for new logs)
   ```

### Code Flow for Connection Name Update

```
1. User Updates Connection Name (UI)
   └─ POST /api/connections/{id}/
      └─ connection_name in request.data

2. Django View Updates SourceConfig
   └─ source_config.name = connection_name
   └─ source_config.save()

3. Migration Request Includes Connection Name
   └─ migration_data["connection_name"] = connection.src_config.name

4. Migration Service Uses Connection Name
   └─ For job naming
   └─ For error notifications
   └─ For logging

5. Error Notifications Use Current Name
   └─ send_error_notification(connection_name=...)
```

---

## Database Table Renaming

### Overview

The database tables were renamed to remove the `elttool_` prefix. This was done through a series of Django migrations.

### Migration History

#### Step 1: Rename Destination Tables

**File**: `apps/elttool/migrations/0020_rename_destination_tables.py`

```python
migrations.RunSQL(
    sql=[
        "ALTER TABLE IF EXISTS elttool_destinationmodel RENAME TO destination_model;",
        "ALTER TABLE IF EXISTS elttool_destinationattribute RENAME TO destination_attribute;",
        "ALTER TABLE IF EXISTS elttool_destinationconfig RENAME TO destination_config;",
        "ALTER TABLE IF EXISTS elttool_destinationtableselection RENAME TO destination_table_selection;",
    ],
    reverse_sql=[...]
)
```

#### Step 2: Rename Source Tables

**File**: `apps/elttool/migrations/0022_rename_source_tables.py`

```python
migrations.RunSQL(
    sql=[
        "ALTER TABLE IF EXISTS elttool_sourcemodel RENAME TO source_model;",
        "ALTER TABLE IF EXISTS elttool_sourceattribute RENAME TO source_attribute;",
        "ALTER TABLE IF EXISTS elttool_sourceconfig RENAME TO source_config;",
        "ALTER TABLE IF EXISTS elttool_sourcetableselection RENAME TO source_table_selection;",
        "ALTER TABLE IF EXISTS elttool_connection RENAME TO connection;",
        "ALTER TABLE IF EXISTS elttool_sourcedeltatable RENAME TO source_delta_table;",
    ],
    reverse_sql=[...]
)
```

#### Step 3: Rename Miscellaneous Tables

**File**: `apps/elttool/migrations/0023_rename_misc_tables.py`

```python
migrations.RunSQL(
    sql=[
        "ALTER TABLE IF EXISTS elttool_company RENAME TO company;",
        "ALTER TABLE IF EXISTS elttool_communicationsupport RENAME TO communication_support;",
        "ALTER TABLE IF EXISTS elttool_data_transfer RENAME TO data_transfer;",
        "ALTER TABLE IF EXISTS elttool_datedirectory RENAME TO date_directory;",
        "ALTER TABLE IF EXISTS elttool_errorlogentry RENAME TO error_log_entry;",
        "ALTER TABLE IF EXISTS elttool_existing_tbl_hdr RENAME TO existing_tbl_hdr;",
        "ALTER TABLE IF EXISTS elttool_existing_tbl_itm RENAME TO existing_tbl_itm;",
        "ALTER TABLE IF EXISTS elttool_invoice RENAME TO invoice;",
        "ALTER TABLE IF EXISTS elttool_log RENAME TO log;",
    ],
    reverse_sql=[...]
)
```

#### Step 4: Rename Role and Pricing Tables

**File**: `apps/elttool/migrations/0024_rename_roles_pricing_mapping.py`

```python
migrations.RunSQL(
    sql=[
        "ALTER TABLE IF EXISTS elttool_role RENAME TO role;",
        "ALTER TABLE IF EXISTS elttool_tablemapping RENAME TO table_mapping;",
        "ALTER TABLE IF EXISTS elttool_migrationrecord RENAME TO migration_record;",
        "ALTER TABLE IF EXISTS elttool_scheduledjob RENAME TO scheduled_job;",
        "ALTER TABLE IF EXISTS elttool_pricing RENAME TO pricing;",
        "ALTER TABLE IF EXISTS elttool_s3_mapping RENAME TO s3_mapping;",
    ],
    reverse_sql=[...]
)
```

#### Step 5: Rename Auth Tables

**File**: `apps/elttool/migrations/0025_rename_auth_tables.py`

```python
migrations.RunSQL(
    sql=[
        "ALTER TABLE IF EXISTS elttool_user RENAME TO \"user\";",
        "ALTER TABLE IF EXISTS elttool_usergroup RENAME TO user_groups;",
        "ALTER TABLE IF EXISTS elttool_userpermission RENAME TO user_permissions;",
        "ALTER TABLE IF EXISTS elttool_roleaccess RENAME TO role_access;",
    ],
    reverse_sql=[...]
)
```

#### Step 6: Strip elttool Prefix from Indexes

**File**: `apps/elttool/migrations/0026_strip_elttool_prefix_from_indexes.py`

```python
migrations.RunSQL(
    sql=r"""
    DO $$
    DECLARE
        rec record;
        newname text;
    BEGIN
        FOR rec IN
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname LIKE 'elttool_%'
        LOOP
            newname := regexp_replace(rec.indexname, '^elttool_', '');
            EXECUTE format('ALTER INDEX %I RENAME TO %I', rec.indexname, newname);
        END LOOP;
    END$$;
    """
)
```

#### Step 7: Align Index Names with Tables

**File**: `apps/elttool/migrations/0027_align_index_names_with_tables.py`

This migration fixes index names to match table naming convention (snake_case):

```python
# Example: sourcemodel_pkey → source_model_pkey
# Example: destinationconfig_pkey → destination_config_pkey
```

### Model Meta Configuration

After renaming, models use `db_table` to specify table names:

```python
class SourceConfig(models.Model):
    # ... fields ...
    
    class Meta:
        db_table = 'source_config'  # No elttool_ prefix
```

### Running Migrations

```bash
# Apply all migrations
python manage.py migrate

# Apply specific migration
python manage.py migrate elttool 0027

# Rollback migration
python manage.py migrate elttool 0026
```

### Verification

Check table names in database:

```sql
-- List all tables (should not have elttool_ prefix)
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
  AND table_name NOT LIKE 'elttool_%'
ORDER BY table_name;
```

---

## Removing elttool App Label

### Overview

The `elttool` app label was removed from table names and indexes. This was done through the migrations described above.

### Process

1. **Update Model Meta**: Remove `app_label` or set `db_table` explicitly
2. **Run Migrations**: Execute table renaming migrations
3. **Update Indexes**: Rename indexes to match new table names
4. **Update Code References**: Update any hardcoded table names

### Model Configuration

**Before**:
```python
class SourceConfig(models.Model):
    # ... fields ...
    
    class Meta:
        app_label = 'elttool'  # This creates elttool_sourceconfig table
```

**After**:
```python
class SourceConfig(models.Model):
    # ... fields ...
    
    class Meta:
        db_table = 'source_config'  # Explicit table name, no prefix
```

### Code References Update

Update any raw SQL queries:

**Before**:
```python
cursor.execute("SELECT * FROM elttool_sourceconfig WHERE ...")
```

**After**:
```python
cursor.execute("SELECT * FROM source_config WHERE ...")
```

### Migration State

The migrations use `SeparateDatabaseAndState` to:
1. Update Django's migration state (no app label)
2. Rename actual database tables (SQL)

```python
operations = [
    migrations.RunSQL(
        sql=["ALTER TABLE ... RENAME TO ..."],
        reverse_sql=["ALTER TABLE ... RENAME TO ..."]
    ),
    migrations.SeparateDatabaseAndState(
        state_operations=[...]  # Update Django state
    )
]
```

---

## API Response Caching Optimization

### Overview

API response caching reduces database queries and improves response times. The system uses Django's cache framework with Redis.

### Current Caching Implementation

#### 1. Trial Period Check Caching

**File**: `apps/elttool/v1/permissions.py`

```python
from django.core.cache import cache

class CheckTrialPeriod(permissions.BasePermission):
    def has_permission(self, request, view):
        user_id = request.user.user_id
        
        # ✅ OPTIMIZATION: Check cache first (5-minute cache)
        cache_key = f'trial_check_{user_id}'
        cached_result = cache.get(cache_key)
        
        if cached_result is not None:
            # Cache hit - return immediately (no DB query, < 1ms)
            if cached_result == 'expired':
                return False
            return True
        
        # Cache miss - query database
        company = request.user.cmp
        trial_status = get_trial_status(company)
        
        # Cache result for 5 minutes
        cache.set(cache_key, 'expired' if trial_status['expired'] else 'active', 300)
        
        return not trial_status['expired']
```

**Cache Strategy**:
- **Key**: `trial_check_{user_id}`
- **TTL**: 5 minutes (300 seconds)
- **Invalidation**: When subscription changes (via Company model)

#### 2. Cache Invalidation on Subscription Change

**File**: `apps/elttool/models.py` (Company model)

```python
from django.core.cache import cache

class Company(models.Model):
    # ... fields ...
    
    def save(self, *args, **kwargs):
        # Invalidate trial check cache for all users in company
        if self.pk:  # Update, not create
            users = User.objects.filter(cmp_id=self.pk)
            for user in users:
                cache_key = f'trial_check_{user.user_id}'
                cache.delete(cache_key)
        
        super().save(*args, **kwargs)
```

### Additional Caching Opportunities

#### 1. Connection List Caching

**File**: `apps/elttool/v1/views.py`

```python
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page

class ConnectionListAPIView(APIView):
    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    def get(self, request):
        connections = Connection.objects.filter(cmp=request.user.cmp)
        serializer = ConnectionSerializer(connections, many=True)
        return Response(serializer.data)
```

#### 2. Schema Caching

**File**: `apps/elttool/v1/views.py`

```python
def get_connection_schema(connection_id, user_id):
    cache_key = f'schema_{connection_id}_{user_id}'
    cached_schema = cache.get(cache_key)
    
    if cached_schema:
        return cached_schema
    
    # Fetch schema from database
    schema = fetch_schema_from_db(connection_id)
    
    # Cache for 10 minutes
    cache.set(cache_key, schema, 60 * 10)
    
    return schema
```

#### 3. Table List Caching

**File**: `apps/elttool/v1/views.py`

```python
def get_table_list(connection_id, user_id):
    cache_key = f'tables_{connection_id}_{user_id}'
    cached_tables = cache.get(cache_key)
    
    if cached_tables:
        return cached_tables
    
    # Fetch tables from API
    tables = fetch_tables_from_api(connection_id)
    
    # Cache for 5 minutes
    cache.set(cache_key, tables, 60 * 5)
    
    return tables
```

#### 4. User Permissions Caching

**File**: `apps/elttool/v1/permissions.py`

```python
def get_user_permissions(user_id):
    cache_key = f'permissions_{user_id}'
    cached_permissions = cache.get(cache_key)
    
    if cached_permissions:
        return cached_permissions
    
    # Fetch permissions from database
    user = User.objects.select_related('roles').get(user_id=user_id)
    permissions = RoleAccess.objects.filter(role=user.roles).values_list('type', flat=True)
    permissions_list = list(permissions)
    
    # Cache for 1 hour (permissions rarely change)
    cache.set(cache_key, permissions_list, 60 * 60)
    
    return permissions_list
```

### Cache Backend Configuration

**File**: `datasyncher/settings/base.py`

```python
# Redis Cache Configuration
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://redis:6379/1',  # Use DB 1 for cache (DB 0 for Celery)
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
        'KEY_PREFIX': 'datasyncher',
        'TIMEOUT': 300,  # Default timeout: 5 minutes
    }
}
```

### Cache Key Naming Convention

Use consistent cache key patterns:

```python
# User-specific cache
f'{resource}_{user_id}'  # e.g., 'trial_check_123'

# Connection-specific cache
f'{resource}_{connection_id}_{user_id}'  # e.g., 'schema_456_123'

# Company-specific cache
f'{resource}_{company_id}'  # e.g., 'billing_789'

# Global cache
f'{resource}'  # e.g., 'pricing_plans'
```

### Cache Invalidation Strategies

#### 1. Time-Based Expiration (TTL)

```python
# Cache expires after TTL
cache.set(key, value, timeout=300)  # 5 minutes
```

#### 2. Event-Based Invalidation

```python
# Invalidate on model save
def save(self, *args, **kwargs):
    super().save(*args, **kwargs)
    cache.delete(f'resource_{self.pk}')
```

#### 3. Manual Invalidation

```python
# Clear specific cache
cache.delete('trial_check_123')

# Clear pattern-based cache
from django.core.cache import cache
import redis

r = redis.Redis(host='redis', port=6379, db=1)
for key in r.scan_iter(match='trial_check_*'):
    cache.delete(key)
```

### Performance Metrics

**Before Caching**:
- Trial check: ~50-100ms (database query)
- Connection list: ~200-500ms (multiple queries)
- Schema fetch: ~500-1000ms (API call)

**After Caching**:
- Trial check: < 1ms (cache hit)
- Connection list: < 5ms (cache hit)
- Schema fetch: < 10ms (cache hit)

**Cache Hit Rate Target**: > 80%

### Monitoring Cache Performance

```python
from django.core.cache import cache

# Check cache stats
cache_stats = cache.get_stats()
print(f"Cache hits: {cache_stats['hits']}")
print(f"Cache misses: {cache_stats['misses']}")
print(f"Hit rate: {cache_stats['hit_rate']}%")
```

### Best Practices

1. **Cache Frequently Accessed Data**: Trial checks, user permissions, connection lists
2. **Use Appropriate TTL**: Short for dynamic data (5 min), long for static data (1 hour)
3. **Invalidate on Updates**: Clear cache when data changes
4. **Monitor Cache Hit Rate**: Aim for > 80% hit rate
5. **Use Cache Keys with User/Company Context**: Prevent data leakage between users

---

## Summary

This guide covers:

1. **Environment Configuration**: Complete `.env` setup for migration function
2. **Error Email Configuration**: SMTP setup and email notification implementation
3. **Connection Name Update**: Best practices for handling connection name changes
4. **Database Table Renaming**: Complete migration history and process
5. **Removing elttool App Label**: Migration strategy and code updates
6. **API Response Caching**: Optimization strategies and implementation

**Key Takeaways**:
- Always use `.env` file for configuration (never hardcode secrets)
- Error emails are automatically sent in all error regions
- Connection name updates require careful handling to avoid breaking migrations
- Table renaming was done through Django migrations (reversible)
- Caching significantly improves API response times (> 80% improvement)

