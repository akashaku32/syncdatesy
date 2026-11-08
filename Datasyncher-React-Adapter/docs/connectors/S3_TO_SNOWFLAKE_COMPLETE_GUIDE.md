# S3 to Snowflake Migration - Complete Technical Documentation

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Router Layer](#router-layer)
3. [Service Layer](#service-layer)
4. [Model Layer](#model-layer)
5. [Views Layer (Django)](#views-layer-django)
6. [Loading Methods](#loading-methods)
7. [Primary Key Methods](#primary-key-methods)
8. [File Types and Dependencies](#file-types-and-dependencies)
9. [Parsing Mechanisms](#parsing-mechanisms)
10. [Delta and Drop & Reload](#delta-and-drop--reload)
11. [Complete Flow Diagram](#complete-flow-diagram)

---

## Architecture Overview

The S3 to Snowflake migration follows a **3-tier architecture**:

```
┌─────────────────────────────────────────────────────────────┐
│                    Django Views Layer                        │
│  (apps/elttool/v1/views.py)                                 │
│  - User interface endpoints                                  │
│  - Connection management                                     │
│  - Table selection and mapping                               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI Router Layer                            │
│  (backend/migration-function/routers/amazon_s3_routers.py) │
│  - Request validation                                        │
│  - Error handling                                           │
│  - Response formatting                                      │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Service Layer                                   │
│  (backend/migration-function/services/amazon_s3_service.py)│
│  - Business logic                                           │
│  - S3 file processing                                       │
│  - Snowflake loading                                         │
│  - GCS staging                                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Model Layer                                     │
│  (backend/migration-function/models/amazon_s3_models.py)   │
│  - Request/Response models                                   │
│  - Data validation                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Router Layer

**File**: `backend/migration-function/routers/amazon_s3_routers.py`

### Endpoints

#### 1. `/AmazonS3-Authorization` (POST)
**Purpose**: Validate S3 connection credentials

**Request Model**: `AmazonS3ValidationRequest`
```python
{
    "connection_id": int,
    "AmazonS3": {
        "s3_bucket": str,
        "aws_access_key_id": str,
        "aws_secret_access_key": str,
        "aws_region": str (optional)
    }
}
```

**Flow**:
1. Receives validation request
2. Calls `AmazonS3Service.authenticate()`
3. Tests bucket access
4. Returns sample files as proof of connection

**Response**:
```python
{
    "status_code": 200,
    "body": {
        "message": "Connection successful",
        "sample_files": [...]
    }
}
```

#### 2. `/AmazonS3-listing-files` (POST)
**Purpose**: List files from S3 bucket (no schema inference)

**Request Model**: `AmazonS3ListingTables`
```python
{
    "connection_id": int,
    "AmazonS3": {
        "s3_bucket": str,
        "aws_access_key_id": str,
        "aws_secret_access_key": str,
        "aws_region": str,
        "s3_base_prefix": str,
        "file_type": str  # "csv", "tsv", "json", "parquet", "avro", "xml"
    },
    "cmp_id": int,
    "user_id": int
}
```

**Flow**:
1. Receives listing request
2. Calls `AmazonS3Service.list_tables()`
3. Returns file listing with metadata (table name, file_key, last_modified)
4. **No schema inference** - just file metadata

**Response**:
```python
{
    "result": {
        "tables": [
            {
                "table": "file_name",
                "file_key": "path/to/file.csv",
                "last_modified": "2024-01-01T00:00:00Z"
            }
        ],
        "files_count": 10
    }
}
```

#### 3. `/AmazonS3-listing-tables` (POST)
**Purpose**: List S3 logical tables WITH inferred schema (columns)

**Request Model**: `AmazonS3ListingTables` (same as above)

**Flow**:
1. Receives listing request
2. Calls `AmazonS3Service.list_tables_with_schema()`
3. Reads sample data from files to infer column structure
4. Writes tables to `SourceTableSelection` database
5. Returns tables with column details

**Response**:
```python
{
    "result": {
        "tables": [
            {
                "table": "file_name",
                "columns": {
                    "col1": "STRING",
                    "col2": "NUMBER",
                    ...
                },
                "file_key": "path/to/file.csv",
                "last_modified": "2024-01-01T00:00:00Z"
            }
        ],
        "files_count": 10
    }
}
```

**Database Write**:
- Automatically writes to `SourceTableSelection` table
- Updates existing records or creates new ones
- Used by Django's `fetch_tables_from_api` for schema refresh

#### 4. `/Snowflake-AmazonS3-migrate` (POST)
**Purpose**: Migrate data from S3 to Snowflake

**Request Model**: `AmazonS3MigrationRequest`
```python
{
    "job_name": str,
    "source_config": {
        "s3_bucket": str,
        "aws_access_key_id": str,
        "aws_secret_access_key": str,
        "aws_region": str,
        "s3_base_prefix": str,
        "file_type": str,
        "file_mapping_mode": str,  # "single_table_per_file" or "multi_files_single_table"
        "load_method": str,  # "upsert_file_row", "append_file_modified", "upsert_custom_key"
        "single_file_table_mapping": dict,  # file_key -> table_name
        "multi_files_table_name": str,  # For multi_files_single_table mode
        "multi_files_prefix": str,  # Pattern for auto-discovery
        "user_pk": list,  # Custom primary keys (for upsert_custom_key)
        # File parsing options
        "delimiter": str,
        "quotechar": str,
        "skipheaderlines": int,
        "json_mode": str,  # "packed" or None
        "xml_mode": str,  # "packed" only
        "escape_char": str,
        "null_sequence": str,
        "skip_footer_lines": int,
        "line_separator": str
    },
    "destination_config": {
        "account": str,
        "user": str,
        "password": str,
        "warehouse": str,
        "database": str,
        "schema": str,
        "role": str,
        "gcs_service_account_json": str,
        "gcp_project_id": str,
        "gcs_bucket_name": str,
        "sf_storage_integration": str
    },
    "selected_tables": list,  # List of file keys or table names
    "connection_id": int,
    "cmp_id": int,
    "user_id": int,
    "migration_session_id": int,
    "chunk_count": int,  # Rows per chunk (default: 50000)
    "safety_interval": str,  # Seconds (default: 120)
    "drop_and_reload": bool,  # True for full reload
    "connection_name": str,
    "company_name": str
}
```

**Flow**:
1. Validates request
2. Calls `AmazonS3Service.migrate()`
3. Processes files in chunks
4. Uploads to GCS
5. Loads into Snowflake
6. Returns migration results

**Response**:
```python
{
    "status": "success" | "partial" | "failed",
    "files_processed": int,
    "total_rows": int,
    "failed_tables": int,
    "duration_seconds": float,
    "tables": [
        {
            "table_name": str,
            "status": "success" | "failed",
            "rows_loaded": int,
            "error": str (if failed)
        }
    ]
}
```

---

## Service Layer

**File**: `backend/migration-function/services/amazon_s3_service.py`

### Class: `AmazonS3Service`

All methods are **static methods** (stateless service).

#### 1. `authenticate(data: AmazonS3ValidationRequest)`
**Purpose**: Validate S3 connection

**Steps**:
1. Create S3 client with provided credentials
2. Test bucket access (`s3.head_bucket()`)
3. List sample files (first 10 files)
4. Return validation result

**Error Handling**:
- `403 Forbidden`: Access denied (IAM permissions)
- `404 Not Found`: Bucket doesn't exist
- `401 Unauthorized`: Invalid credentials

#### 2. `list_tables(data: AmazonS3ListingTables)`
**Purpose**: List files without schema inference

**Steps**:
1. Create S3 client
2. List files recursively under `s3_base_prefix`
3. Filter by `file_type`
4. Group files by table name (derived from filename)
5. Return file listing with metadata

**Table Name Derivation**:
- For `single_table_per_file`: Uses filename (without extension)
- Sanitized to Snowflake identifier (alphanumeric + underscore)

#### 3. `list_tables_with_schema(data: AmazonS3ListingTables)`
**Purpose**: List tables WITH schema inference

**Steps**:
1. List files (same as `list_tables()`)
2. For each file:
   - Read sample data (first 1000 rows)
   - Parse file based on `file_type`
   - Infer column types from data
   - Return table with columns
3. Write to `SourceTableSelection` database
4. Return tables with schema

**Schema Inference**:
- Reads first 1000 rows from each file
- Infers data types: STRING, NUMBER, BOOLEAN, DATE, TIMESTAMP
- Handles nested JSON (flattened)
- Adds metadata columns: `_FILE`, `_ROW`, `_MODIFIED`, `_DS_SYNCED`

#### 4. `migrate(request: AmazonS3MigrationRequest)`
**Purpose**: Main migration function

**Internal Function**: `_sync_amazon_s3_to_snowflake()`

**Complete Flow**:

```
1. Extract Configuration
   ├─ S3 Config (bucket, credentials, region, prefix)
   ├─ Snowflake Config (account, user, password, warehouse, database, schema)
   └─ GCS Config (service account, bucket, storage integration)

2. Validate S3 Mapping
   ├─ Check if mapping exists in database (S3Mapping table)
   ├─ If exists and locked: Validate current mapping matches
   ├─ If new: Create and persist mapping
   └─ Lock mapping after first sync

3. Process Each Selected Table/File
   ├─ Determine file mapping mode
   │  ├─ single_table_per_file: One file → One table
   │  └─ multi_files_single_table: Multiple files → One table
   ├─ For each file:
   │  ├─ Stream file in chunks (chunk_count rows)
   │  ├─ Parse chunk based on file_type
   │  ├─ Add metadata columns
   │  ├─ Upload chunk to GCS as Parquet
   │  └─ Load chunk into Snowflake using COPY INTO
   └─ Track success/failure per table

4. Return Results
   ├─ Status (success/partial/failed)
   ├─ Files processed count
   ├─ Total rows loaded
   ├─ Failed tables list
   └─ Duration
```

**Chunk Processing**:
- Files are processed in chunks of `chunk_count` rows (default: 50,000)
- Each chunk is:
  1. Parsed from S3
  2. Converted to Parquet format
  3. Uploaded to GCS
  4. Loaded into Snowflake using `COPY INTO`
  5. Merged/appended based on `load_method`

**Error Handling**:
- Individual table failures don't stop entire migration
- Errors are logged and tracked
- Email notifications sent on final attempt only
- Consolidated error emails for multiple failures

---

## Model Layer

**File**: `backend/migration-function/models/amazon_s3_models.py`

### Models

#### 1. `AmazonS3ValidationRequest`
```python
class AmazonS3ValidationRequest(BaseModel):
    connection_id: int
    AmazonS3: Dict[str, Any]  # S3 credentials and config
```

#### 2. `AmazonS3ListingTables`
```python
class AmazonS3ListingTables(BaseModel):
    connection_id: int
    AmazonS3: Dict[str, Any]  # S3 config with file_type, s3_base_prefix
    job_name: Optional[str]
    migration_session_id: Optional[int]
    cmp_id: int
    user_id: int
    is_source: Optional[bool]
    last_checked_date: Optional[str]
```

#### 3. `AmazonS3MigrationRequest`
```python
class AmazonS3MigrationRequest(BaseModel):
    job_name: str
    source_config: Dict[str, Any]  # Complete S3 config
    destination_config: Dict[str, Any]  # Complete Snowflake config
    selected_tables: List[str]  # File keys or table names
    attribute_dict: Dict[str, Any]
    folderpath_dict: Dict[str, Any]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    is_deleted: str
    is_updated: str
    safety_interval: str
    migration_session_id: int
    last_checked_dates: Dict[str, str]
    execution_order: str
    chunk_count: int
    logs_creation: bool
    connection_name: str
    company_name: str
    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    drop_and_reload: Optional[bool] = False
    model_config = ConfigDict(extra="ignore")  # Ignore extra fields
```

---

## Views Layer (Django)

**File**: `apps/elttool/v1/views.py`

### Key Functions

#### 1. `suggest_s3_table_name(request)`
**Purpose**: Suggest table name for multi-files mode

**Flow**:
- Receives list of file keys
- Extracts common prefix from filenames
- Sanitizes to valid Snowflake identifier
- Returns suggested table name

#### 2. `list_s3_files(request)`
**Purpose**: List files for mapping UI

**Flow**:
- Receives S3 connection details
- Calls FastAPI `/AmazonS3-listing-files` endpoint
- Returns file listing for UI selection

#### 3. Connection Update Flow
**Purpose**: Update connection name and configuration

**Flow**:
1. User updates connection name in UI
2. Django view receives `connection_name` in request
3. Updates `SourceConfig.name` field
4. Passes `connection_name` to migration function
5. Migration uses `connection_name` for:
   - Job naming
   - Error notifications
   - Logging

**Important**: Connection name update must be handled carefully to avoid breaking existing migrations.

---

## Loading Methods

### 1. `upsert_file_row` (Default)

**Purpose**: UPSERT using `_FILE` and `_ROW` columns as composite primary key

**How It Works**:
1. Each row gets metadata columns:
   - `_FILE`: Source file path
   - `_ROW`: Row number in file
   - `_MODIFIED`: File last modified timestamp
   - `_DS_SYNCED`: Sync timestamp
2. Snowflake MERGE statement:
   ```sql
   MERGE INTO target_table t
   USING staging_table s
   ON t._FILE = s._FILE AND t._ROW = s._ROW
   WHEN MATCHED THEN UPDATE SET ...
   WHEN NOT MATCHED THEN INSERT ...
   ```

**Use Case**: 
- Files that may be updated/reprocessed
- Need to track which file each row came from
- Need row-level deduplication

**Primary Key**: `(_FILE, _ROW)`

### 2. `append_file_modified`

**Purpose**: APPEND using `_FILE`, `_ROW`, and `_MODIFIED` for change detection

**How It Works**:
1. Each row gets metadata columns (same as above)
2. Snowflake INSERT statement:
   ```sql
   INSERT INTO target_table
   SELECT * FROM staging_table
   WHERE NOT EXISTS (
       SELECT 1 FROM target_table t
       WHERE t._FILE = s._FILE 
         AND t._ROW = s._ROW
         AND t._MODIFIED = s._MODIFIED
   )
   ```

**Use Case**:
- Incremental loads (only new/changed rows)
- Files that are versioned by modification time
- Append-only data warehouse pattern

**Primary Key**: `(_FILE, _ROW, _MODIFIED)`

### 3. `upsert_custom_key`

**Purpose**: UPSERT using user-defined primary keys

**How It Works**:
1. User specifies `user_pk` list (e.g., `["id", "email"]`)
2. Snowflake MERGE statement:
   ```sql
   MERGE INTO target_table t
   USING staging_table s
   ON t.id = s.id AND t.email = s.email
   WHEN MATCHED THEN UPDATE SET ...
   WHEN NOT MATCHED THEN INSERT ...
   ```

**Use Case**:
- Business keys exist in data
- Need to update existing records
- Custom deduplication logic

**Primary Key**: User-defined columns from `user_pk`

**Configuration**:
- `user_pk`: List of column names (e.g., `["customer_id", "order_date"]`)
- Must be provided in `source_config` when `load_method="upsert_custom_key"`

---

## Primary Key Methods

### Automatic Primary Keys

All loading methods automatically add metadata columns that can serve as primary keys:

1. **`_FILE`**: Source file path (STRING)
   - Format: `s3://bucket/path/to/file.csv`
   - Used for tracking data lineage

2. **`_ROW`**: Row number within file (NUMBER)
   - Starts at 0 or 1 (depending on header skip)
   - Used for row-level identification

3. **`_MODIFIED`**: File last modified timestamp (TIMESTAMP_NTZ)
   - UTC timestamp from S3 object metadata
   - Used for change detection

4. **`_DS_SYNCED`**: DataSyncher sync timestamp (TIMESTAMP_NTZ)
   - UTC timestamp when row was synced
   - Used for audit trail

### Composite Primary Keys

- **`upsert_file_row`**: `(_FILE, _ROW)`
- **`append_file_modified`**: `(_FILE, _ROW, _MODIFIED)`
- **`upsert_custom_key`**: User-defined columns

### Primary Key Selection Logic

```python
if load_method == "upsert_file_row":
    primary_key = ["_FILE", "_ROW"]
elif load_method == "append_file_modified":
    primary_key = ["_FILE", "_ROW", "_MODIFIED"]
elif load_method == "upsert_custom_key":
    primary_key = user_pk  # From source_config
else:
    primary_key = ["_FILE", "_ROW"]  # Default
```

---

## File Types and Dependencies

### 1. CSV (Comma-Separated Values)

**Dependencies**:
- `pandas` (for parsing)
- `io.BytesIO` (for in-memory processing)

**Configuration Fields**:
- `delimiter`: Field delimiter (default: `","`)
- `quotechar`: Quote character (default: `'"'`)
- `skipheaderlines`: Number of header lines to skip (default: `0`)
- `escape_char`: Escape character (optional)
- `null_sequence`: String representing null values (optional)
- `skip_footer_lines`: Number of footer lines to skip (optional)
- `line_separator`: Line separator (`\n`, `\r\n`, `\r`)

**Parsing Function**: `read_s3_file_to_df()` → `pd.read_csv()`

**Schema Inference**:
- Reads first 1000 rows
- Infers types: STRING, NUMBER, DATE, TIMESTAMP
- Handles quoted fields and escaped characters

**Example**:
```python
source_config = {
    "file_type": "csv",
    "delimiter": ",",
    "quotechar": '"',
    "skipheaderlines": 1,
    "escape_char": "\\",
    "null_sequence": "NULL"
}
```

### 2. TSV (Tab-Separated Values)

**Dependencies**: Same as CSV

**Configuration Fields**:
- `tsv_delimiter`: Field delimiter (default: `"\t"`)
- `tsv_quotechar`: Quote character (default: `'"'`)
- `tsv_skipheaderlines`: Number of header lines to skip
- `tsv_escape_char`: Escape character
- `tsv_null_sequence`: Null value representation
- `tsv_skip_footer_lines`: Footer lines to skip

**Parsing Function**: `read_s3_file_to_df()` → `pd.read_csv()` with `delimiter="\t"`

**Note**: TSV uses separate config fields (prefixed with `tsv_`) to allow different settings from CSV.

### 3. JSON

**Dependencies**:
- `json` (standard library)
- `pandas` (for DataFrame conversion)

**Configuration Fields**:
- `json_mode`: `"packed"` or `None` (normal)
  - `"packed"`: Store entire JSON object in `_DATA` column
  - `None`: Flatten JSON structure into columns

**Parsing Function**: `read_s3_file_to_df()`

**Modes**:

**Normal Mode** (flattened):
```json
[
    {"id": 1, "name": "John", "address": {"city": "NYC"}},
    {"id": 2, "name": "Jane", "address": {"city": "LA"}}
]
```
Result: Columns: `id`, `name`, `address_city`

**Packed Mode**:
```json
[
    {"id": 1, "name": "John"},
    {"id": 2, "name": "Jane"}
]
```
Result: Columns: `_DATA` (contains full JSON object as string)

**Schema Inference**:
- Normal mode: Flattens nested structures (max depth: 10)
- Packed mode: Single `_DATA` column (STRING)

### 4. Parquet

**Dependencies**:
- `pyarrow` (for Parquet reading)
- `pandas` (for DataFrame conversion)

**Configuration Fields**: None (Parquet is self-describing)

**Parsing Function**: `read_s3_file_to_df()` → `pd.read_parquet()`

**Schema Inference**:
- Uses Parquet schema directly
- Preserves data types from Parquet metadata
- No configuration needed

**Advantages**:
- Columnar format (faster reads)
- Built-in compression
- Schema embedded in file

### 5. Avro

**Dependencies**:
- `fastavro` (for Avro reading)
- `pandas` (for DataFrame conversion)

**Configuration Fields**: None (Avro schema is embedded)

**Parsing Function**: `read_s3_file_to_df()` → `fastavro.reader()`

**Schema Inference**:
- Uses Avro schema from file
- Converts Avro types to Snowflake types
- Handles complex types (unions, arrays, maps)

**Type Mapping**:
- `string` → STRING
- `int`, `long` → NUMBER
- `float`, `double` → FLOAT
- `boolean` → BOOLEAN
- `bytes` → BINARY
- `timestamp-millis` → TIMESTAMP_NTZ

### 6. XML

**Dependencies**:
- `xml.etree.ElementTree` (standard library)
- `pandas` (for DataFrame conversion)

**Configuration Fields**:
- `xml_mode`: Only `"packed"` supported
  - Stores entire XML as string in `_DATA` column

**Parsing Function**: `read_s3_file_to_df()` → `parse_xml_packed()`

**Limitations**:
- Only packed mode supported (XML stored as string)
- No schema inference (single `_DATA` column)
- Large XML files may be slow to parse

**Example**:
```xml
<root>
    <item id="1">Value 1</item>
    <item id="2">Value 2</item>
</root>
```
Result: Columns: `_DATA` (contains full XML as string)

---

## Parsing Mechanisms

### Streaming vs Non-Streaming

#### Non-Streaming: `read_s3_file_to_df()`
**Use Case**: 
- Schema inference (small sample)
- Small files (< 100MB)
- File structure validation

**How It Works**:
1. Downloads entire file from S3
2. Loads into memory
3. Parses into pandas DataFrame
4. Returns complete DataFrame

**Memory Usage**: Entire file in memory

#### Streaming: `read_s3_file_in_chunks()`
**Use Case**:
- Large files (> 100MB)
- Production migrations
- Memory-efficient processing

**How It Works**:
1. Streams file from S3 in chunks
2. Parses chunk-by-chunk
3. Yields DataFrame per chunk
4. Processes chunks sequentially

**Memory Usage**: Only one chunk in memory at a time

**Chunk Size**: Configurable via `chunk_count` (default: 50,000 rows)

### Parsing Flow

```
1. Read File from S3
   ├─ Get object from S3 bucket
   ├─ Read content (streaming or full)
   └─ Get last_modified timestamp

2. Determine File Type
   ├─ From source_config["file_type"]
   └─ Route to appropriate parser

3. Parse Based on Type
   ├─ CSV/TSV: pd.read_csv() with config
   ├─ JSON: json.loads() + flatten/pack
   ├─ Parquet: pd.read_parquet()
   ├─ Avro: fastavro.reader()
   └─ XML: ET.parse() + pack

4. Add Metadata Columns
   ├─ _FILE: Source file path
   ├─ _ROW: Row number
   ├─ _MODIFIED: File timestamp
   └─ _DS_SYNCED: Sync timestamp

5. Return DataFrame
   └─ Ready for GCS upload and Snowflake load
```

### Error Handling in Parsing

**Common Errors**:
1. **UnicodeDecodeError**: Tries ISO-8859-1 encoding as fallback
2. **JSONDecodeError**: Logs error, returns None
3. **Empty File**: Returns None with warning
4. **Schema Mismatch**: Validates structure, logs mismatch

**Recovery**:
- Individual file failures don't stop migration
- Errors logged with file path and error message
- Email notification sent on final attempt

---

## Delta and Drop & Reload

### Delta Reload (Incremental Sync)

**Purpose**: Load only new or changed data

**How It Works**:

1. **File-Level Delta**:
   - Tracks `last_checked_date` per file
   - Only processes files modified after `last_checked_date`
   - Uses S3 object `LastModified` timestamp

2. **Row-Level Delta** (for `append_file_modified`):
   - Uses `_MODIFIED` column for change detection
   - Only inserts rows with new `_MODIFIED` timestamp
   - Skips rows that already exist with same `(_FILE, _ROW, _MODIFIED)`

3. **Safety Interval**:
   - Waits `safety_interval` seconds (default: 120)
   - Ensures files are fully written before processing
   - Prevents reading partial files

**Configuration**:
```python
{
    "safety_interval": "120",  # Seconds
    "last_checked_dates": {
        "file1.csv": "2024-01-01T00:00:00Z",
        "file2.csv": "2024-01-01T00:00:00Z"
    }
}
```

**Flow**:
```
1. List files in S3
2. Filter by last_checked_date:
   ├─ If file.LastModified > last_checked_date: Process
   └─ Else: Skip
3. Process files:
   ├─ Read file
   ├─ Filter rows by _MODIFIED (if append_file_modified)
   └─ Load into Snowflake
4. Update last_checked_dates after successful load
```

### Drop & Reload (Full Sync)

**Purpose**: Completely replace table data

**How It Works**:

1. **Drop Table** (if exists):
   ```sql
   DROP TABLE IF EXISTS target_table;
   ```

2. **Create Table**:
   ```sql
   CREATE TABLE target_table (
       -- Columns from schema inference
       -- Metadata columns: _FILE, _ROW, _MODIFIED, _DS_SYNCED
   );
   ```

3. **Load Data**:
   - Processes all files (ignores `last_checked_dates`)
   - Loads all rows (no filtering)
   - Uses `COPY INTO` for bulk load

**Configuration**:
```python
{
    "drop_and_reload": True
}
```

**When to Use**:
- Initial load
- Schema changes
- Data corruption recovery
- Full refresh required

**Flow**:
```
1. Check drop_and_reload flag
2. If True:
   ├─ DROP TABLE IF EXISTS target_table
   ├─ CREATE TABLE target_table (schema)
   └─ Load all data (no filtering)
3. If False:
   └─ Delta reload (incremental)
```

### Delta vs Drop & Reload Comparison

| Feature | Delta Reload | Drop & Reload |
|---------|-------------|---------------|
| **Speed** | Fast (only new data) | Slow (all data) |
| **Storage** | Incremental | Full replacement |
| **Use Case** | Regular syncs | Initial load, recovery |
| **Safety Interval** | Yes (120s default) | No (processes all) |
| **Last Checked Dates** | Used for filtering | Ignored |
| **Table Drop** | No | Yes (if exists) |

---

## Complete Flow Diagram

### End-to-End Migration Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER INITIATES MIGRATION                      │
│                    (Django UI)                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              Django View (views.py)                             │
│  - Validates user permissions                                  │
│  - Prepares migration request                                  │
│  - Calls FastAPI endpoint                                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         FastAPI Router (amazon_s3_routers.py)                   │
│  - Validates request (Pydantic models)                         │
│  - Calls service layer                                         │
│  - Handles errors and returns response                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│         Service Layer (amazon_s3_service.py)                    │
│                                                                 │
│  1. Extract Configuration                                      │
│     ├─ S3 credentials and bucket                               │
│     ├─ Snowflake credentials                                  │
│     └─ GCS staging config                                      │
│                                                                 │
│  2. Validate S3 Mapping                                        │
│     ├─ Check S3Mapping table                                  │
│     ├─ Validate mapping immutability                          │
│     └─ Persist new mapping                                     │
│                                                                 │
│  3. Process Each File/Table                                    │
│     ├─ Determine file mapping mode                            │
│     ├─ For each file:                                         │
│     │  ├─ Stream file in chunks                               │
│     │  ├─ Parse chunk (CSV/JSON/Parquet/etc)                  │
│     │  ├─ Add metadata columns                                │
│     │  ├─ Convert to Parquet                                  │
│     │  ├─ Upload to GCS                                       │
│     │  └─ Load into Snowflake (COPY INTO)                      │
│     └─ Track success/failure                                  │
│                                                                 │
│  4. Return Results                                             │
│     ├─ Status, row counts, errors                            │
│     └─ Send email notifications (if failures)                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DATABASE UPDATES                              │
│  - Update MigrationRecord                                       │
│  - Update SourceTableSelection                                  │
│  - Update S3Mapping (lock after first sync)                     │
└─────────────────────────────────────────────────────────────────┘
```

### File Processing Flow (Per File)

```
┌─────────────────────────────────────────────────────────────────┐
│                    FILE PROCESSING                               │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. Read File from S3                                           │
│     ├─ Get object metadata (LastModified)                       │
│     ├─ Check delta (if not drop_and_reload)                    │
│     └─ Stream file content                                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. Parse File in Chunks                                        │
│     ├─ Read chunk_count rows                                   │
│     ├─ Parse based on file_type (CSV/JSON/etc)                │
│     ├─ Add metadata columns (_FILE, _ROW, _MODIFIED, _DS_SYNCED)│
│     └─ Yield DataFrame                                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. Convert to Parquet                                          │
│     ├─ Convert DataFrame to Parquet format                      │
│     ├─ Compress (snappy)                                        │
│     └─ Generate unique filename                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. Upload to GCS                                               │
│     ├─ Create GCS client                                        │
│     ├─ Upload Parquet file                                     │
│     └─ Get GCS URL (gs://bucket/file.parquet)                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. Load into Snowflake                                         │
│     ├─ Connect to Snowflake                                    │
│     ├─ Create/Update table schema                              │
│     ├─ COPY INTO from GCS                                      │
│     ├─ MERGE/INSERT based on load_method                       │
│     └─ Update last_checked_date                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  6. Repeat for Next Chunk                                       │
│     └─ Continue until all chunks processed                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Summary

This documentation covers the complete S3 to Snowflake migration system:

1. **Architecture**: 3-tier (Views → Router → Service → Model)
2. **Router Layer**: 4 endpoints (auth, list files, list tables, migrate)
3. **Service Layer**: Business logic for S3 processing and Snowflake loading
4. **Model Layer**: Pydantic models for request/response validation
5. **Views Layer**: Django endpoints for UI integration
6. **Loading Methods**: 3 methods (upsert_file_row, append_file_modified, upsert_custom_key)
7. **Primary Keys**: Automatic metadata columns + custom keys
8. **File Types**: 6 types (CSV, TSV, JSON, Parquet, Avro, XML)
9. **Parsing**: Streaming and non-streaming modes
10. **Delta & Drop & Reload**: Incremental and full sync strategies

The system is designed for:
- **Scalability**: Chunk-based processing for large files
- **Reliability**: Error handling and retry mechanisms
- **Flexibility**: Multiple file types and loading methods
- **Observability**: Comprehensive logging and email notifications

