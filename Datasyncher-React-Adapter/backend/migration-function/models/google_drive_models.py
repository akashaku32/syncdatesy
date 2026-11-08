import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, root_validator, validator

from models.amazon_s3_models import (
    DelimitedFileOptions,
    FileMappingMethod,
    FileType,
    JSONMode,
    LoadMethod,
    XMLMode,
)


class GoogleDriveValidationRequest(BaseModel):
    GoogleDrive: Optional[Any] = None
    connection_id: Optional[int] = None
    job_name: Optional[str] = None
    is_source: Optional[bool] = False
    log_id: Optional[int] = None


class GoogleDriveCallbackRequest(BaseModel):
    code: str
    state: str
    data_new: Optional[str] = None


class GoogleDriveConfig(BaseModel):
    CLIENT_ID: Optional[str] = None
    CLIENT_SECRET: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[int] = None
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    create_folder_if_missing: Optional[bool] = False
    redirect_uri: Optional[str] = None


class GoogleDriveAuthorizationPayload(BaseModel):
    GoogleDrive: Dict[str, Any]


class GoogleDriveListingTables(BaseModel):
    connection_id: int
    GoogleDrive: Dict[str, Any]
    job_name: Optional[str] = None
    migration_session_id: Optional[int] = None
    last_checked_date: Optional[str] = None
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = True

    class Config:
        extra = "ignore"


class GoogleDrivePatternPreviewRequest(BaseModel):
    connection_id: int
    GoogleDrive: Dict[str, Any]
    job_name: Optional[str] = None
    migration_session_id: Optional[int] = None
    multi_files_prefix: Optional[str] = None
    max_results: Optional[int] = 50
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = True

    class Config:
        extra = "ignore"


class GoogleDrivePKConfig(BaseModel):
    CLIENT_ID: Optional[str] = None
    CLIENT_SECRET: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[int] = None
    folder_id: str
    folder_name: Optional[str] = None
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

    @validator("json_mode", "xml_mode", pre=True)
    def empty_optional_modes_to_none(cls, value):
        if value in (None, "", "null", "undefined"):
            return None
        return value

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
                raise ValueError(
                    "single_file_table_mapping is required for single_table_per_file"
                )

        if method == FileMappingMethod.multi_files_single_table:
            if not values.get("multi_files_prefix"):
                file_type = values.get("file_type")
                file_type_value = (
                    file_type.value
                    if isinstance(file_type, FileType)
                    else str(file_type or "").strip()
                )
                if file_type_value:
                    values["multi_files_prefix"] = f"*.{file_type_value}"
                else:
                    raise ValueError(
                        "multi_files_prefix is required for multi_files_single_table"
                    )
            if not values.get("multi_files_table_name"):
                raise ValueError(
                    "multi_files_table_name is required for multi_files_single_table"
                )

        return values


class GoogleDrivePKSuggestionRequest(BaseModel):
    sample_rows: int = 1000
    connection_id: Optional[int] = None
    cmp_id: Optional[int] = None
    user_id: Optional[int] = None
    session: Optional[int] = None
    job_name: Optional[str] = None
    GoogleDrive: Optional[GoogleDrivePKConfig] = None
    googledrive: Optional[GoogleDrivePKConfig] = None

    @property
    def drive(self) -> GoogleDrivePKConfig:
        return self.GoogleDrive or self.googledrive

    @classmethod
    def _normalize_drive_payload(cls, drive_payload):
        if (
            "file_mapping_method" not in drive_payload
            and "file_mapping_mode" in drive_payload
        ):
            drive_payload["file_mapping_method"] = drive_payload.get(
                "file_mapping_mode"
            )

        csv_field_map = {
            "delimiter": "delimiter",
            "quotechar": "quotechar",
            "skipheaderlines": "skipheaderlines",
            "escape_char": "escape_char",
            "null_sequence": "null_sequence",
            "skip_footer_lines": "skip_footer_lines",
            "line_separator": "line_separator",
        }
        tsv_field_map = {
            "tsv_delimiter": "delimiter",
            "tsv_quotechar": "quotechar",
            "tsv_skipheaderlines": "skipheaderlines",
            "tsv_escape_char": "escape_char",
            "tsv_null_sequence": "null_sequence",
            "tsv_skip_footer_lines": "skip_footer_lines",
        }
        csv_opts = {
            target: drive_payload.pop(source)
            for source, target in csv_field_map.items()
            if source in drive_payload
        }
        tsv_opts = {
            target: drive_payload.pop(source)
            for source, target in tsv_field_map.items()
            if source in drive_payload
        }
        if csv_opts and "csv_options" not in drive_payload:
            drive_payload["csv_options"] = csv_opts
        if tsv_opts and "tsv_options" not in drive_payload:
            drive_payload["tsv_options"] = tsv_opts
        for optional_key in ("json_mode", "xml_mode"):
            if drive_payload.get(optional_key) in (None, "", "null", "undefined"):
                drive_payload.pop(optional_key, None)
        return drive_payload

    @root_validator(pre=True)
    def wrap_flat_payload(cls, values):
        context_fields = {
            "connection_id",
            "cmp_id",
            "user_id",
            "session",
            "job_name",
            "sample_rows",
        }
        temp_values = values.copy()

        if "form_data" in temp_values and isinstance(temp_values["form_data"], dict):
            for key, value in temp_values["form_data"].items():
                if key not in temp_values:
                    temp_values[key] = value

        for source_key in ("GoogleDrive", "googledrive"):
            if source_key in temp_values and isinstance(temp_values[source_key], dict):
                drive_payload = temp_values[source_key].copy()
                for key, value in temp_values.items():
                    if key in context_fields or key in {
                        "GoogleDrive",
                        "googledrive",
                        "form_data",
                    }:
                        continue
                    if key not in drive_payload:
                        drive_payload[key] = value
                drive_payload = cls._normalize_drive_payload(drive_payload)
                return {
                    "sample_rows": temp_values.get("sample_rows", 1000),
                    **{
                        key: value
                        for key, value in temp_values.items()
                        if key in context_fields and key != "sample_rows"
                    },
                    "GoogleDrive": drive_payload,
                }

        csv_fields = {
            "delimiter",
            "quotechar",
            "skipheaderlines",
            "escape_char",
            "null_sequence",
            "skip_footer_lines",
            "line_separator",
        }
        tsv_fields = {
            "tsv_delimiter",
            "tsv_quotechar",
            "tsv_skipheaderlines",
            "tsv_escape_char",
            "tsv_null_sequence",
            "tsv_skip_footer_lines",
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

        drive_payload = remaining.copy()
        if csv_opts:
            drive_payload["csv_options"] = csv_opts
        if tsv_opts:
            drive_payload["tsv_options"] = tsv_opts
        drive_payload = cls._normalize_drive_payload(drive_payload)

        return {
            "sample_rows": context.get("sample_rows", 1000),
            **{
                key: value
                for key, value in context.items()
                if key != "sample_rows"
            },
            "GoogleDrive": drive_payload,
        }

    class Config:
        extra = "ignore"


class GoogleDriveMigrationRequest(BaseModel):
    source_config: Dict[str, Optional[str]]
    destination_config: Dict[str, Optional[str]]
    connection_id: int
    cmp_id: int
    user_id: int
    session: int
    is_updated: Optional[str] = "_FIVETRAN_SYNCED"
    is_deleted: Optional[str] = "_FIVETRAN_DELETED"
    safety_interval: str
    selected_tables: List[Any]
    job_name: Optional[str]
    migration_session_id: int
    execution_order: str
    chunk_count: int
    logs_creation: bool
    connection_name: str
    company_name: str
    retry_attempt: int = 0
    max_retries: int = 2
    is_final_attempt: bool = True
    drop_and_reload: Optional[bool] = False
    log_id: Optional[int] = None
    celery_child_mode: bool = False
    folderpath_dict: Optional[Dict[str, Optional[str]]] = None
    table_export_settings: Optional[Dict[str, Any]] = None

    class Config:
        extra = "ignore"
