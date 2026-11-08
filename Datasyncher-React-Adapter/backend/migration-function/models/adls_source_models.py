import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, root_validator, validator

from models.amazon_s3_models import (
    DelimitedFileOptions,
    FileMappingMethod,
    FileType,
    JSONMode,
    LoadMethod,
    XMLMode,
)


class ADLSPatternPreviewRequest(BaseModel):
    connection_id: int
    AzureDataLakeStorage: Optional[Dict[str, Any]] = None
    ADLS: Optional[Dict[str, Any]] = None
    job_name: Optional[str] = None
    migration_session_id: Optional[int] = None
    multi_files_prefix: Optional[str] = None
    max_results: Optional[int] = 50
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = True

    model_config = ConfigDict(extra="ignore")

    def resolved_config(self) -> Dict[str, Any]:
        for candidate in (self.AzureDataLakeStorage, self.ADLS):
            if isinstance(candidate, dict):
                return candidate
        return {}


class ADLSListingTables(BaseModel):
    connection_id: int
    AzureDataLakeStorage: Optional[Dict[str, Any]] = None
    ADLS: Optional[Dict[str, Any]] = None
    job_name: Optional[str] = None
    migration_session_id: Optional[int] = None
    last_checked_date: Optional[str] = None
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = True

    model_config = ConfigDict(extra="ignore")

    def resolved_config(self) -> Dict[str, Any]:
        for candidate in (self.AzureDataLakeStorage, self.ADLS):
            if isinstance(candidate, dict):
                return candidate
        return {}


class ADLSPKConfig(BaseModel):
    account_name: str
    storage_account_name: Optional[str] = None
    container_name: str
    file_system: Optional[str] = None
    filesystem: Optional[str] = None
    tenant_id: str
    client_id: str
    client_secret: str

    folder_name: str = ""
    include_subfolders: bool = False
    destination_config: Optional[Dict[str, Any]] = None

    file_type: FileType
    file_mapping_method: FileMappingMethod
    single_file_table_mapping: Dict[str, str] = Field(default_factory=dict)
    table_to_files_mapping: Dict[str, Any] = Field(default_factory=dict)
    multi_files_prefix: Optional[str] = None
    multi_files_table_name: Optional[str] = None
    load_method: LoadMethod

    csv_options: Optional[DelimitedFileOptions] = None
    tsv_options: Optional[DelimitedFileOptions] = None
    json_mode: Optional[JSONMode] = None
    xml_mode: Optional[XMLMode] = None

    @root_validator(pre=True)
    def normalize_aliases(cls, values):
        if not isinstance(values, dict):
            return values
        normalized = values.copy()
        if "account_name" not in normalized and normalized.get("storage_account_name"):
            normalized["account_name"] = normalized.get("storage_account_name")
        if "container_name" not in normalized:
            normalized["container_name"] = normalized.get("file_system") or normalized.get("filesystem")
        if "folder_name" not in normalized:
            normalized["folder_name"] = (
                normalized.get("root_folder")
                or normalized.get("folder_path")
                or normalized.get("base_folder_path")
                or normalized.get("adls_base_prefix")
                or ""
            )
        if "file_mapping_method" not in normalized and normalized.get("file_mapping_mode"):
            normalized["file_mapping_method"] = normalized.get("file_mapping_mode")
        return normalized

    @validator("single_file_table_mapping", "table_to_files_mapping", pre=True)
    def parse_json_mappings(cls, value):
        if value is None:
            return {}
        if isinstance(value, str):
            raw = value.strip()
            if not raw or raw in {"null", "undefined"}:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return value
        return value

    @root_validator(skip_on_failure=True)
    def validate_mapping(cls, values):
        method = values.get("file_mapping_method")

        if method == FileMappingMethod.single_table_per_file:
            if not values.get("single_file_table_mapping"):
                raise ValueError("single_file_table_mapping is required for single_table_per_file")

        if method == FileMappingMethod.multi_files_single_table:
            if not values.get("multi_files_prefix"):
                file_type = values.get("file_type")
                file_type_value = file_type.value if isinstance(file_type, FileType) else str(file_type or "").strip()
                if file_type_value:
                    values["multi_files_prefix"] = f"*.{file_type_value}"
                else:
                    raise ValueError("multi_files_prefix is required for multi_files_single_table")
            if not values.get("multi_files_table_name"):
                raise ValueError("multi_files_table_name is required for multi_files_single_table")

        return values


class ADLSPKSuggestionRequest(BaseModel):
    sample_rows: int = 1000
    connection_id: Optional[int] = None
    cmp_id: Optional[int] = None
    user_id: Optional[int] = None
    session: Optional[int] = None
    job_name: Optional[str] = None
    AzureDataLakeStorage: Optional[ADLSPKConfig] = None
    ADLS: Optional[ADLSPKConfig] = None
    adls: Optional[ADLSPKConfig] = None

    @property
    def source(self) -> Optional[ADLSPKConfig]:
        return self.AzureDataLakeStorage or self.ADLS or self.adls

    @classmethod
    def _normalize_source_payload(cls, source_payload):
        source_payload = source_payload.copy()
        if "file_mapping_method" not in source_payload and "file_mapping_mode" in source_payload:
            source_payload["file_mapping_method"] = source_payload.get("file_mapping_mode")
        if "account_name" not in source_payload and "storage_account_name" in source_payload:
            source_payload["account_name"] = source_payload.get("storage_account_name")
        if "container_name" not in source_payload:
            source_payload["container_name"] = source_payload.get("file_system") or source_payload.get("filesystem")
        if "folder_name" not in source_payload:
            source_payload["folder_name"] = (
                source_payload.get("root_folder")
                or source_payload.get("folder_path")
                or source_payload.get("base_folder_path")
                or source_payload.get("adls_base_prefix")
                or ""
            )

        csv_fields = {
            "delimiter": "delimiter",
            "quotechar": "quotechar",
            "skipheaderlines": "skipheaderlines",
            "escape_char": "escape_char",
            "null_sequence": "null_sequence",
            "skip_footer_lines": "skip_footer_lines",
            "line_separator": "line_separator",
        }
        tsv_fields = {
            "tsv_delimiter": "delimiter",
            "tsv_quotechar": "quotechar",
            "tsv_skipheaderlines": "skipheaderlines",
            "tsv_escape_char": "escape_char",
            "tsv_null_sequence": "null_sequence",
            "tsv_skip_footer_lines": "skip_footer_lines",
        }
        csv_opts = {target: source_payload.pop(source) for source, target in csv_fields.items() if source in source_payload}
        tsv_opts = {target: source_payload.pop(source) for source, target in tsv_fields.items() if source in source_payload}
        if csv_opts and "csv_options" not in source_payload:
            source_payload["csv_options"] = csv_opts
        if tsv_opts and "tsv_options" not in source_payload:
            source_payload["tsv_options"] = tsv_opts
        return source_payload

    @root_validator(pre=True)
    def wrap_flat_payload(cls, values):
        context_fields = {"connection_id", "cmp_id", "user_id", "session", "job_name", "sample_rows"}
        temp_values = values.copy()

        if "form_data" in temp_values and isinstance(temp_values["form_data"], dict):
            for key, value in temp_values["form_data"].items():
                if key not in temp_values:
                    temp_values[key] = value

        if "source_config" in temp_values and isinstance(temp_values["source_config"], dict):
            for key, value in temp_values["source_config"].items():
                if key not in temp_values:
                    temp_values[key] = value

        for wrapper in ("AzureDataLakeStorage", "ADLS", "adls", "source_config"):
            if wrapper in temp_values and isinstance(temp_values[wrapper], dict):
                source_payload = temp_values[wrapper].copy()
                for key, value in temp_values.items():
                    if key in context_fields or key in {"AzureDataLakeStorage", "ADLS", "adls", "source_config", "form_data"}:
                        continue
                    if key not in source_payload:
                        source_payload[key] = value
                source_payload = cls._normalize_source_payload(source_payload)
                return {
                    "sample_rows": temp_values.get("sample_rows", 1000),
                    **{key: value for key, value in temp_values.items() if key in context_fields and key != "sample_rows"},
                    "AzureDataLakeStorage": source_payload,
                }

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

        for key, value in temp_values.items():
            if key in context_fields:
                context[key] = value
            elif key in csv_fields:
                csv_opts[key] = value
            elif key in tsv_fields:
                tsv_opts[key.replace("tsv_", "")] = value
            else:
                remaining[key] = value

        source_payload = remaining.copy()
        if csv_opts:
            source_payload["csv_options"] = csv_opts
        if tsv_opts:
            source_payload["tsv_options"] = tsv_opts
        source_payload = cls._normalize_source_payload(source_payload)

        return {
            "sample_rows": context.get("sample_rows", 1000),
            **{key: value for key, value in context.items() if key != "sample_rows"},
            "AzureDataLakeStorage": source_payload,
        }

    model_config = ConfigDict(extra="ignore")


class ADLSSnowflakeMigrationRequest(BaseModel):
    """Model for ADLS to Snowflake migration request."""

    source_config: Dict[str, Any]
    destination_config: Dict[str, Any]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    selected_tables: List[Any]
    safety_interval: str
    job_name: Optional[str] = None
    migration_session_id: int
    execution_order: str
    chunk_count: int
    logs_creation: bool = False
    connection_name: Optional[str] = None
    company_name: Optional[str] = None
    drop_and_reload: Optional[bool] = False

    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"

    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    log_id: Optional[int] = None
    celery_child_mode: bool = False

    model_config = ConfigDict(extra="ignore")
