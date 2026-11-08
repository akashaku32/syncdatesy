# models/amazon_s3_models.py
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, List, Any

class AmazonS3ValidationRequest(BaseModel):
    """Model for S3 connection validation request."""
    AmazonS3: Dict[str, Any]  # Changed from Dict[str, str] to support JSON fields like single_file_table_mapping

class AmazonS3ListingTables(BaseModel):
    """Model for listing S3 files/tables request."""
    connection_id: int
    AmazonS3: Dict[str, Any]  # Changed from Dict[str, str] to support JSON fields like single_file_table_mapping
    job_name: Optional[str] = None
    last_checked_date: Optional[str] = None
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates

class AmazonS3MigrationRequest(BaseModel):
    """Model for S3 to Snowflake migration request.
    
    This model matches the structure of other sources (Salesforce, MicrosoftDynamics).
    All S3-specific configuration (file_type, file_mapping_mode, load_method, etc.)
    should be stored in source_config dictionary, not as top-level fields.
    """
    source_config: Dict[str, Any]  # Contains all S3 config: file_type, file_mapping_mode, load_method, delimiter, etc.
    destination_config: Dict[str, Any]  # Changed to Any for consistency
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    selected_tables: List[str]  # List of file keys/paths
    safety_interval: str
    job_name: Optional[str] = None
    migration_session_id: int
    execution_order: str
    chunk_count: int
    logs_creation: bool = False
    connection_name: Optional[str] = None
    company_name: Optional[str] = None
    drop_and_reload: Optional[bool] = False
    # ✅ Common fields for consistency with other services (Django sends these)
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"  # Default sync column name
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"  # Default delete column name
    # ✅ Retry attempt tracking fields (prevent duplicate emails)
    retry_attempt: int = 0  # Default to 0 (first attempt)
    max_retries: int = 2    # Default to 2 (total 3 attempts: 1 initial + 2 retries)
    is_final_attempt: bool = True  # Default to True for backward compatibility
    log_id: Optional[int] = None  # ✅ Add log_id for stable updates
    
    celery_child_mode: bool = False

    model_config = ConfigDict(extra="ignore")  # Ignore extra fields instead of raising validation error


class AmazonS3PatternPreviewRequest(BaseModel):
    """Model for S3 pattern preview request (multi-file single table mode)."""
    connection_id: int
    AmazonS3: Dict[str, Any]  # S3 credentials and configuration
    multi_files_prefix: str  # Regex pattern to match files
    max_results: Optional[int] = 50  # Limit number of results returned
    cmp_id: int
    user_id: int

from enum import Enum

class FileType(str, Enum):
    csv = "csv"
    tsv = "tsv"
    json = "json"
    xml = "xml"
    avro = "avro"
    parquet = "parquet"


class FileMappingMethod(str, Enum):
    single_table_per_file = "single_table_per_file"
    multi_files_single_table = "multi_files_single_table"


class LoadMethod(str, Enum):
    upsert_custom_key = "upsert_custom_key"


class JSONMode(str, Enum):
    packed = "packed"
    unpacked = "unpacked"


class XMLMode(str, Enum):
    packed = "packed"
    unpacked = "unpacked"

from pydantic import BaseModel
from typing import Optional

from pydantic import BaseModel
from typing import Optional

class DelimitedFileOptions(BaseModel):
    delimiter: Optional[str] = None
    quotechar: Optional[str] = None
    skipheaderlines: Optional[int] = None
    escape_char: Optional[str] = None
    null_sequence: Optional[str] = None
    skip_footer_lines: Optional[int] = None
    line_separator: Optional[str] = None


from pydantic import BaseModel, Field, root_validator, validator
from typing import Dict, List, Optional
import json

class AmazonS3PKConfig(BaseModel):
    # ===== Connection =====
    s3_bucket: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: Optional[str] = None
    base_folder_path: str = ""
    s3_base_prefix: Optional[str] = None
    multi_files_prefix: Optional[str] = None
    include_subfolders: bool = False
    destination_config: Optional[Dict[str, Any]] = None

    @validator('base_folder_path', pre=True, always=True)
    def sync_base_prefix(cls, v, values):
        """Sync s3_base_prefix with base_folder_path."""
        if not v and values.get('s3_base_prefix'):
            return values.get('s3_base_prefix')
        return v

    # ===== File setup =====
    file_type: FileType
    file_mapping_method: FileMappingMethod

    # ===== Mapping =====
    single_file_table_mapping: Dict[str, str] = Field(default_factory=dict)
    multi_files_table_name: Optional[str] = None

    @validator('single_file_table_mapping', pre=True)
    def parse_json_mappings(cls, v):
        """Handle stringified JSON or empty/null values for mapping fields."""
        if v is None:
            return {}
        if isinstance(v, str):
            val = v.strip()
            if not val or val == 'null' or val == 'undefined':
                return {}
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                # If it's not valid JSON, return as-is and let Pydantic's 
                # type validation handle the error if it's not a dict
                return v
        return v

    # ===== PK logic =====
    load_method: LoadMethod

    # ===== Format options =====
    csv_options: Optional[DelimitedFileOptions] = None
    tsv_options: Optional[DelimitedFileOptions] = None
    json_mode: Optional[JSONMode] = None
    xml_mode: Optional[XMLMode] = None

    @root_validator(skip_on_failure=True)
    def validate_mapping(cls, values):
        method = values.get("file_mapping_method")

        if method == FileMappingMethod.single_table_per_file:
            if not values.get("single_file_table_mapping"):
                raise ValueError(
                    "single_file_table_mapping is required for single_table_per_file"
                )

        if method == FileMappingMethod.multi_files_single_table:
            if not values.get("multi_files_prefix"):
                file_type = values.get("file_type")
                file_type_value = file_type.value if isinstance(file_type, FileType) else str(file_type or "").strip()
                if file_type_value:
                    values["multi_files_prefix"] = f"*.{file_type_value}"
                else:
                    raise ValueError(
                        "multi_files_prefix is required for multi_files_single_table"
                    )
            if not values.get("multi_files_table_name"):
                raise ValueError("multi_files_table_name is required for multi_files_single_table")

        return values

class AmazonS3PKSuggestionRequest(BaseModel):
    sample_rows: int = 1000
    connection_id: Optional[int] = None
    cmp_id: Optional[int] = None
    user_id: Optional[int] = None
    session: Optional[int] = None
    job_name: Optional[str] = None
    s3: AmazonS3PKConfig

    @root_validator(pre=True)
    def wrap_flat_payload(cls, values):
        """
        Accept flat frontend payload or nested AmazonS3 payload and convert it into:
        {
            sample_rows,
            s3: AmazonS3PKConfig
        }
        """
        context_fields = {"connection_id", "cmp_id", "user_id", "session", "job_name", "sample_rows"}

        # Start with a mutable copy and flatten common nestings first.
        temp_values = values.copy()

        # Some callers send payload under form_data (update flow style).
        if "form_data" in temp_values and isinstance(temp_values["form_data"], dict):
            form_data_nested = temp_values["form_data"]
            for k, v in form_data_nested.items():
                if k not in temp_values:
                    temp_values[k] = v

        # Handle "AmazonS3" nesting if present (consistency with other S3 endpoints)
        if "AmazonS3" in temp_values and isinstance(temp_values["AmazonS3"], dict):
            s3_nested = temp_values.pop("AmazonS3")
            for k, v in s3_nested.items():
                if k not in temp_values:
                    temp_values[k] = v

        # If wrapped as "s3", allow fallback from top-level/form_data fields
        # when s3 object is incomplete.
        if "s3" in temp_values and isinstance(temp_values["s3"], dict):
            s3_payload = temp_values["s3"].copy()
            for k, v in temp_values.items():
                if k in context_fields or k in {"s3", "form_data"}:
                    continue
                if k not in s3_payload:
                    s3_payload[k] = v

            # Backward-compatible key normalization
            if "file_mapping_method" not in s3_payload and "file_mapping_mode" in s3_payload:
                s3_payload["file_mapping_method"] = s3_payload.get("file_mapping_mode")
            if "base_folder_path" not in s3_payload and "s3_base_prefix" in s3_payload:
                s3_payload["base_folder_path"] = s3_payload.get("s3_base_prefix")

            return {
                "sample_rows": temp_values.get("sample_rows", 1000),
                **{k: v for k, v in temp_values.items() if k in context_fields and k != "sample_rows"},
                "s3": s3_payload,
            }

        # ----------------------------
        # Extract CSV/TSV options
        # ----------------------------
        csv_fields = {
            "delimiter", "quotechar", "skipheaderlines", "escape_char",
            "null_sequence", "skip_footer_lines", "line_separator",
        }

        tsv_fields = {
            "tsv_delimiter", "tsv_quotechar", "tsv_skipheaderlines",
            "tsv_escape_char", "tsv_null_sequence", "tsv_skip_footer_lines",
        }

        csv_opts = {}
        tsv_opts = {}
        context = {}
        remaining = {}

        for k, v in temp_values.items():
            if k in context_fields:
                context[k] = v
            elif k in csv_fields:
                csv_opts[k] = v
            elif k in tsv_fields:
                # normalize TSV field names
                tsv_opts[k.replace("tsv_", "")] = v
            else:
                remaining[k] = v

        s3_payload = remaining.copy()

        # Backward-compatible key normalization
        if "file_mapping_method" not in s3_payload and "file_mapping_mode" in s3_payload:
            s3_payload["file_mapping_method"] = s3_payload.get("file_mapping_mode")
        if "base_folder_path" not in s3_payload and "s3_base_prefix" in s3_payload:
            s3_payload["base_folder_path"] = s3_payload.get("s3_base_prefix")

        if csv_opts:
            s3_payload["csv_options"] = csv_opts

        if tsv_opts:
            s3_payload["tsv_options"] = tsv_opts

        return {
            "sample_rows": context.get("sample_rows", 1000),
            **{k: v for k, v in context.items() if k != "sample_rows"},
            "s3": s3_payload,
        }
