# Amazon S3 to Snowflake Integration

## Overview

This document provides comprehensive API documentation, test cases, and usage examples for the Amazon S3 to Snowflake data integration service.

## Table of Contents

1. [API Endpoints](#api-endpoints)
2. [Request/Response Models](#requestresponse-models)
3. [Test Cases](#test-cases)
4. [Usage Examples](#usage-examples)
5. [Error Handling](#error-handling)
6. [Configuration](#configuration)

---

## API Endpoints

### 1. Connection Validation

**Endpoint:** `POST /AmazonS3-Authorization`

**Description:** Validates S3 connection credentials and tests bucket access.

**Request Body:**
```json
{
  "AmazonS3": {
    "s3_bucket": "my-bucket-name",
    "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "aws_region": "us-east-1",
    "s3_base_prefix": "data/",
    "file_type": "csv"
  }
}
```

**Response (Success):**
```json
{
  "status_code": 200,
  "body": {
    "message": "Connection validated successfully",
    "sample_files": [
      {
        "file_key": "data/file1.csv",
        "last_modified": "2024-01-15T10:30:00Z",
        "size": 1024000
      }
    ],
    "bucket_name": "my-bucket-name",
    "region": "us-east-1"
  }
}
```

**Response (Error):**
```json
{
  "status_code": 403,
  "detail": "S3 access denied: Please verify your AWS access key ID and secret access key have permission to access the specified bucket."
}
```

---

### 2. List Files (Basic File Listing)

**Endpoint:** `POST /AmazonS3-listing-files`

**Description:** Lists files from S3 bucket without schema inference. Used for file mapping UI.

**Request Body:**
```json
{
  "connection_id": 123,
  "AmazonS3": {
    "s3_bucket": "my-bucket-name",
    "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "aws_region": "us-east-1",
    "s3_base_prefix": "data/",
    "file_type": "csv"
  },
  "cmp_id": 1,
  "user_id": 1,
  "job_name": "file_listing",
  "last_checked_date": null
}
```

**Response:**
```json
{
  "result": {
    "tables": [
      {
        "table": "file1",
        "file_key": "data/file1.csv",
        "last_modified": "2024-01-15T10:30:00Z",
        "size": 1024000
      },
      {
        "table": "file2",
        "file_key": "data/file2.csv",
        "last_modified": "2024-01-16T14:20:00Z",
        "size": 2048000
      }
    ],
    "files_count": 2
  }
}
```

---

### 3. List Tables (With Schema)

**Endpoint:** `POST /AmazonS3-listing-tables`

**Description:** Lists logical tables with inferred column schema. Used for schema refresh in SourceTableSelection.

**Request Body:**
```json
{
  "connection_id": 123,
  "AmazonS3": {
    "s3_bucket": "my-bucket-name",
    "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "aws_region": "us-east-1",
    "s3_base_prefix": "data/",
    "file_type": "csv",
    "file_mapping_mode": "single_table_per_file",
    "load_method": "upsert_file_row",
    "delimiter": ",",
    "skipheaderlines": 1
  },
  "cmp_id": 1,
  "user_id": 1,
  "job_name": "schema_refresh",
  "last_checked_date": null
}
```

**Response:**
```json
{
  "result": {
    "tables": [
      {
        "table": "file1",
        "file_key": "data/file1.csv",
        "columns": {
          "id": {
            "data_type": "INTEGER",
            "is_primary_key": false
          },
          "name": {
            "data_type": "VARCHAR(255)",
            "is_primary_key": false
          },
          "email": {
            "data_type": "VARCHAR(255)",
            "is_primary_key": false
          }
        },
        "last_modified": "2024-01-15T10:30:00Z"
      }
    ],
    "files_count": 1
  }
}
```

---

### 4. Data Migration

**Endpoint:** `POST /Snowflake-AmazonS3-migrate`

**Description:** Migrates data from S3 to Snowflake with streaming chunk processing.

**Request Body:**
```json
{
  "source_config": {
    "s3_bucket": "my-bucket-name",
    "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "aws_region": "us-east-1",
    "s3_base_prefix": "data/",
    "file_type": "csv",
    "file_mapping_mode": "single_table_per_file",
    "single_file_table_mapping": {
      "data/file1.csv": "customers",
      "data/file2.csv": "orders"
    },
    "delimiter": ",",
    "quotechar": "\"",
    "skipheaderlines": 1
  },
  "destination_config": {
    "account": "myaccount",
    "user": "myuser",
    "password": "mypassword",
    "warehouse": "MY_WAREHOUSE",
    "database": "MY_DATABASE",
    "schema": "MY_SCHEMA",
    "role": "MY_ROLE"
  },
  "connection_id": 123,
  "cmp_id": 1,
  "user_id": 1,
  "session": 456,
  "selected_tables": ["data/file1.csv", "data/file2.csv"],
  "file_type": "csv",
  "file_mapping_mode": "single_table_per_file",
  "load_method": "upsert_file_row",
  "delimiter": ",",
  "quotechar": "\"",
  "skipheaderlines": 1,
  "chunk_count": 50000,
  "safety_interval": "120",
  "job_name": "migration_123",
  "migration_session_id": 789,
  "execution_order": "parallel",
  "logs_creation": true,
  "connection_name": "S3 to Snowflake",
  "company_name": "My Company",
  "drop_and_reload": false,
  "is_updated": "_FIVETRAN_SYNCED",
  "is_deleted": "_FIVETRAN_DELETED"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Migration completed successfully",
  "results": [
    {
      "table": "customers",
      "file_key": "data/file1.csv",
      "rows_processed": 100000,
      "rows_inserted": 50000,
      "rows_updated": 50000,
      "status": "success"
    },
    {
      "table": "orders",
      "file_key": "data/file2.csv",
      "rows_processed": 50000,
      "rows_inserted": 50000,
      "rows_updated": 0,
      "status": "success"
    }
  ],
  "total_rows": 150000,
  "execution_time_seconds": 45.2
}
```

---

## Request/Response Models

### AmazonS3ValidationRequest
```python
{
  "AmazonS3": {
    "s3_bucket": str,
    "aws_access_key_id": str,
    "aws_secret_access_key": str,
    "aws_region": str (optional),
    "s3_base_prefix": str (optional),
    "file_type": str  # csv, tsv, json, parquet, avro, xml
  }
}
```

### AmazonS3ListingTables
```python
{
  "connection_id": int,
  "AmazonS3": {
    "s3_bucket": str,
    "aws_access_key_id": str,
    "aws_secret_access_key": str,
    "aws_region": str (optional),
    "s3_base_prefix": str (optional),
    "file_type": str,
    "file_mapping_mode": str (optional),  # single_table_per_file, multi_files_single_table
    "load_method": str (optional),  # upsert_file_row, append_file_modified, upsert_custom_key
    "delimiter": str (optional),
    "quotechar": str (optional),
    "skipheaderlines": int (optional),
    "single_file_table_mapping": dict (optional),
    "multi_files_prefix": str (optional),
    "multi_files_table_name": str (optional)
  },
  "cmp_id": int,
  "user_id": int,
  "job_name": str (optional),
  "last_checked_date": str (optional)
}
```

### AmazonS3MigrationRequest
```python
{
  "source_config": dict,
  "destination_config": dict,
  "connection_id": int,
  "cmp_id": int,
  "user_id": int,
  "session": int,
  "selected_tables": List[str],
  "file_type": str,
  "file_mapping_mode": str,
  "load_method": str,
  "chunk_count": int (default: 50000),
  "safety_interval": str (default: "120"),
  "job_name": str (optional),
  "migration_session_id": int,
  "execution_order": str (default: "parallel"),
  "logs_creation": bool (default: false),
  "drop_and_reload": bool (default: false),
  # ... other optional fields
}
```

---

## Test Cases

### Test Case 1: Connection Validation - Success
**Test ID:** TC_S3_001  
**Description:** Validate successful S3 connection with valid credentials  
**Preconditions:** Valid AWS credentials and accessible bucket  
**Steps:**
1. Send POST request to `/AmazonS3-Authorization`
2. Include valid S3 credentials in request body
3. Verify response status code is 200
4. Verify response contains sample files

**Expected Result:** Connection validated successfully with sample files returned

---

### Test Case 2: Connection Validation - Invalid Credentials
**Test ID:** TC_S3_002  
**Description:** Validate error handling for invalid AWS credentials  
**Preconditions:** Invalid AWS access key or secret key  
**Steps:**
1. Send POST request to `/AmazonS3-Authorization`
2. Include invalid credentials in request body
3. Verify response status code is 401 or 403
4. Verify error message indicates authentication failure

**Expected Result:** Error response with clear authentication failure message

---

### Test Case 3: Connection Validation - Bucket Not Found
**Test ID:** TC_S3_003  
**Description:** Validate error handling for non-existent bucket  
**Preconditions:** Valid credentials but bucket doesn't exist  
**Steps:**
1. Send POST request to `/AmazonS3-Authorization`
2. Include non-existent bucket name
3. Verify response status code is 404
4. Verify error message indicates bucket not found

**Expected Result:** Error response indicating bucket not found

---

### Test Case 4: List Files - Single File Type
**Test ID:** TC_S3_004  
**Description:** List CSV files from S3 bucket  
**Preconditions:** Valid connection, bucket contains CSV files  
**Steps:**
1. Send POST request to `/AmazonS3-listing-files`
2. Set `file_type` to "csv"
3. Verify response contains list of CSV files
4. Verify each file has file_key, last_modified, and size

**Expected Result:** List of CSV files with metadata returned

---

### Test Case 5: List Tables - Schema Inference
**Test ID:** TC_S3_005  
**Description:** List tables with inferred column schema  
**Preconditions:** Valid connection, bucket contains files  
**Steps:**
1. Send POST request to `/AmazonS3-listing-tables`
2. Verify response contains tables with columns
3. Verify each column has data_type
4. Verify primary keys are identified (if applicable)

**Expected Result:** Tables with complete column schema returned

---

### Test Case 6: Migration - Single File Per Table
**Test ID:** TC_S3_006  
**Description:** Migrate single CSV file to Snowflake table  
**Preconditions:** Valid S3 and Snowflake connections  
**Steps:**
1. Send POST request to `/Snowflake-AmazonS3-migrate`
2. Set `file_mapping_mode` to "single_table_per_file"
3. Include single file in `selected_tables`
4. Verify migration completes successfully
5. Verify data appears in Snowflake table

**Expected Result:** Data successfully migrated to Snowflake

---

### Test Case 7: Migration - Multiple Files Single Table
**Test ID:** TC_S3_007  
**Description:** Migrate multiple files to single Snowflake table  
**Preconditions:** Valid connections, files have matching schema  
**Steps:**
1. Send POST request to `/Snowflake-AmazonS3-migrate`
2. Set `file_mapping_mode` to "multi_files_single_table"
3. Configure `multi_files_prefix` (and optional `multi_files_table_name`)
4. Verify all files processed into single table
5. Verify row counts match sum of all files

**Expected Result:** All files merged into single table successfully

---

### Test Case 8: Migration - Upsert with File Row
**Test ID:** TC_S3_008  
**Description:** Test upsert load method using _FILE and _ROW  
**Preconditions:** Valid connections, table already exists  
**Steps:**
1. Send migration request with `load_method` = "upsert_file_row"
2. Include files that partially overlap existing data
3. Verify existing rows are updated
4. Verify new rows are inserted
5. Verify no duplicates created

**Expected Result:** Upsert operation completes without duplicates

---

### Test Case 9: Migration - Append with Modified Date
**Test Case ID:** TC_S3_009  
**Description:** Test append load method using _MODIFIED timestamp  
**Preconditions:** Valid connections, incremental sync scenario  
**Steps:**
1. Send migration request with `load_method` = "append_file_modified"
2. Include files with last_modified timestamps
3. Verify only new/modified rows are appended
4. Verify _MODIFIED column populated correctly

**Expected Result:** Only new/modified data appended to table

---

### Test Case 10: Migration - Custom Primary Key
**Test Case ID:** TC_S3_010  
**Description:** Test upsert with custom primary keys  
**Preconditions:** Valid connections, custom primary keys defined  
**Steps:**
1. Send migration request with `load_method` = "upsert_custom_key"
2. Include `user_pk` field with column names
3. Verify upsert uses custom keys instead of _FILE/_ROW
4. Verify data deduplication works correctly

**Expected Result:** Upsert using custom primary keys works correctly

---

### Test Case 11: File Type Support - CSV
**Test ID:** TC_S3_011  
**Description:** Verify CSV file parsing and migration  
**Preconditions:** Valid connections, CSV files in bucket  
**Steps:**
1. Set `file_type` to "csv"
2. Configure delimiter, quotechar, skipheaderlines
3. Verify CSV parsed correctly
4. Verify data migrated to Snowflake

**Expected Result:** CSV files processed correctly

---

### Test Case 12: File Type Support - JSON
**Test ID:** TC_S3_012  
**Description:** Verify JSON file parsing and migration  
**Preconditions:** Valid connections, JSON files in bucket  
**Steps:**
1. Set `file_type` to "json"
2. Set `json_mode` to "packed"
3. Verify JSON parsed correctly
4. Verify nested structures handled

**Expected Result:** JSON files processed correctly

---

### Test Case 13: File Type Support - Parquet
**Test ID:** TC_S3_013  
**Description:** Verify Parquet file parsing and migration  
**Preconditions:** Valid connections, Parquet files in bucket  
**Steps:**
1. Set `file_type` to "parquet"
2. Verify Parquet schema inferred correctly
3. Verify data types preserved
4. Verify data migrated correctly

**Expected Result:** Parquet files processed correctly

---

### Test Case 14: File Type Support - Avro
**Test ID:** TC_S3_014  
**Description:** Verify Avro file parsing and migration  
**Preconditions:** Valid connections, Avro files in bucket  
**Steps:**
1. Set `file_type` to "avro"
2. Verify Avro schema read correctly
3. Verify data types preserved
4. Verify data migrated correctly

**Expected Result:** Avro files processed correctly

---

### Test Case 15: Error Handling - Network Timeout
**Test ID:** TC_S3_015  
**Description:** Verify graceful handling of network timeouts  
**Preconditions:** Network connectivity issues  
**Steps:**
1. Simulate network timeout during S3 access
2. Verify error message is clear and actionable
3. Verify partial data is not committed
4. Verify retry mechanism works (if applicable)

**Expected Result:** Timeout handled gracefully with clear error message

---

### Test Case 16: Error Handling - Schema Mismatch
**Test ID:** TC_S3_016  
**Description:** Verify error handling for schema mismatches in multi-file mode  
**Preconditions:** Multiple files with different schemas  
**Steps:**
1. Set `file_mapping_mode` to "multi_files_single_table"
2. Include files with incompatible schemas
3. Verify error message indicates schema mismatch
4. Verify migration fails before data commit

**Expected Result:** Schema mismatch detected and reported clearly

---

### Test Case 17: Performance - Large File Processing
**Test ID:** TC_S3_017  
**Description:** Verify handling of large files (>1GB)  
**Preconditions:** Valid connections, large file in bucket  
**Steps:**
1. Process file larger than 1GB
2. Verify chunking works correctly
3. Verify memory usage remains stable
4. Verify migration completes successfully

**Expected Result:** Large files processed efficiently without memory issues

---

### Test Case 18: Performance - Concurrent Migrations
**Test ID:** TC_S3_018  
**Description:** Verify handling of concurrent migration requests  
**Preconditions:** Multiple migration requests for same connection  
**Steps:**
1. Send multiple migration requests simultaneously
2. Verify safety_interval prevents conflicts
3. Verify each migration completes independently
4. Verify no data corruption

**Expected Result:** Concurrent migrations handled safely

---

## Usage Examples

### Example 1: Complete Migration Workflow

```python
import requests

# Step 1: Validate Connection
validation_response = requests.post(
    "https://api.example.com/AmazonS3-Authorization",
    json={
        "AmazonS3": {
            "s3_bucket": "my-bucket",
            "aws_access_key_id": "AKIA...",
            "aws_secret_access_key": "secret...",
            "aws_region": "us-east-1",
            "file_type": "csv"
        }
    }
)
print(f"Validation: {validation_response.json()}")

# Step 2: List Tables with Schema
tables_response = requests.post(
    "https://api.example.com/AmazonS3-listing-tables",
    json={
        "connection_id": 123,
        "AmazonS3": {
            "s3_bucket": "my-bucket",
            "aws_access_key_id": "AKIA...",
            "aws_secret_access_key": "secret...",
            "file_type": "csv",
            "file_mapping_mode": "single_table_per_file"
        },
        "cmp_id": 1,
        "user_id": 1
    }
)
tables = tables_response.json()["result"]["tables"]
print(f"Found {len(tables)} tables")

# Step 3: Migrate Data
migration_response = requests.post(
    "https://api.example.com/Snowflake-AmazonS3-migrate",
    json={
        "source_config": {
            "s3_bucket": "my-bucket",
            "aws_access_key_id": "AKIA...",
            "aws_secret_access_key": "secret...",
            "file_type": "csv",
            "file_mapping_mode": "single_table_per_file"
        },
        "destination_config": {
            "account": "myaccount",
            "user": "myuser",
            "password": "mypassword",
            "warehouse": "MY_WAREHOUSE",
            "database": "MY_DATABASE",
            "schema": "MY_SCHEMA"
        },
        "connection_id": 123,
        "selected_tables": ["data/file1.csv"],
        "file_type": "csv",
        "file_mapping_mode": "single_table_per_file",
        "load_method": "upsert_file_row",
        "chunk_count": 50000,
        "migration_session_id": 789,
        "cmp_id": 1,
        "user_id": 1,
        "session": 456
    }
)
print(f"Migration: {migration_response.json()}")
```

---

## Error Handling

### Common Error Codes

| Status Code | Description | Solution |
|------------|-------------|----------|
| 400 | Bad Request | Check request body format and required fields |
| 401 | Unauthorized | Verify AWS credentials are correct |
| 403 | Forbidden | Check IAM policies and bucket permissions |
| 404 | Not Found | Verify bucket name and file paths are correct |
| 422 | Validation Error | Check request body matches Pydantic model |
| 500 | Internal Server Error | Check logs for detailed error information |

### Error Response Format

```json
{
  "detail": "Error message describing what went wrong",
  "status_code": 500,
  "error_type": "S3ConnectionError"
}
```

---

## Configuration

### Supported File Types

- **CSV**: Comma-separated values
- **TSV**: Tab-separated values
- **JSON**: JSON Lines or JSON array format
- **Parquet**: Apache Parquet format
- **Avro**: Apache Avro format
- **XML**: XML format (packed mode only)

### File Mapping Modes

1. **single_table_per_file**: Each file maps to its own table
2. **multi_files_single_table**: Multiple files merge into one table

### Load Methods

1. **upsert_file_row**: UPSERT using `_FILE` and `_ROW` columns
2. **append_file_modified**: APPEND using `_FILE`, `_ROW`, and `_MODIFIED` columns
3. **upsert_custom_key**: UPSERT using user-defined primary keys

### Metadata Columns

All migrated tables include:
- `_FILE`: Source file path
- `_ROW`: Row number in file
- `_MODIFIED`: File last modified timestamp
- `_DS_SYNCED`: Data sync timestamp

---

## Notes

- All timestamps are in UTC
- File paths are case-sensitive
- Schema inference reads first 1000 rows by default
- Chunk size default is 50,000 rows
- Safety interval default is 120 seconds
- Maximum file size supported: 10GB per file

---

## Support

For issues or questions, please contact the development team or refer to the main project documentation.

