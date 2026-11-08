"""Pydantic models for SFTP source listing and schema discovery."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class SFTPListingTables(BaseModel):
    """Request model for listing files and inferred tables from SFTP."""

    connection_id: int
    SFTP: Dict[str, Any]
    job_name: Optional[str] = None
    migration_session_id: Optional[int] = None
    last_checked_date: Optional[str] = None
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = True

    model_config = ConfigDict(extra="ignore")


class SFTPPatternPreviewRequest(BaseModel):
    """Request model for previewing SFTP file-pattern matches."""

    connection_id: int
    SFTP: Dict[str, Any]
    job_name: Optional[str] = None
    migration_session_id: Optional[int] = None
    multi_files_prefix: Optional[str] = None
    max_results: Optional[int] = 50
    cmp_id: int
    user_id: int
    log_id: Optional[int] = None
    is_source: Optional[bool] = True

    model_config = ConfigDict(extra="ignore")


from models.amazon_s3_models import FileType, FileMappingMethod, LoadMethod, JSONMode, XMLMode, DelimitedFileOptions
from pydantic import Field, validator, root_validator
import json


class SFTPPKConfig(BaseModel):
    # ===== Connection =====
    sftp_host: str
    sftp_port: Optional[int] = 22
    sftp_username: str
    sftp_password: Optional[str] = None
    sftp_private_key: Optional[str] = None
    authentication_type: str = "password"
    root_folder: str = ""
    folder_path: Optional[str] = None
    base_folder_path: str = ""
    sftp_base_prefix: Optional[str] = None
    multi_files_prefix: Optional[str] = None
    include_subfolders: bool = False
    destination_config: Optional[Dict[str, Any]] = None

    @validator('root_folder', pre=True, always=True)
    def sync_root_folder(cls, v, values):
        """Sync folder_path with root_folder."""
        if not v and values.get('folder_path'):
            return values.get('folder_path')
        return v

    @validator('base_folder_path', pre=True, always=True)
    def sync_base_prefix(cls, v, values):
        """Sync sftp_base_prefix with base_folder_path."""
        if not v and values.get('sftp_base_prefix'):
            return values.get('sftp_base_prefix')
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


class SFTPPKSuggestionRequest(BaseModel):
    sample_rows: int = 1000
    connection_id: Optional[int] = None
    cmp_id: Optional[int] = None
    user_id: Optional[int] = None
    session: Optional[int] = None
    job_name: Optional[str] = None
    sftp: SFTPPKConfig

    @root_validator(pre=True)
    def wrap_flat_payload(cls, values):
        """
        Accept flat frontend payload or nested SFTP payload and convert it into:
        {
            sample_rows,
            sftp: SFTPPKConfig
        }
        """
        context_fields = {"connection_id", "cmp_id", "user_id", "session", "job_name", "sample_rows"}

        # Start with a mutable copy and flatten common nestings first.
        temp_values = values.copy()

        # Some callers send payload under form_data.
        if "form_data" in temp_values and isinstance(temp_values["form_data"], dict):
            form_data_nested = temp_values["form_data"]
            for k, v in form_data_nested.items():
                if k not in temp_values:
                    temp_values[k] = v

        # Handle "SFTP" nesting if present
        if "SFTP" in temp_values and isinstance(temp_values["SFTP"], dict):
            sftp_nested = temp_values.pop("SFTP")
            for k, v in sftp_nested.items():
                if k not in temp_values:
                    temp_values[k] = v

        # If wrapped as "sftp", allow fallback from top-level/form_data fields
        # when sftp object is incomplete.
        if "sftp" in temp_values and isinstance(temp_values["sftp"], dict):
            sftp_payload = temp_values["sftp"].copy()
            for k, v in temp_values.items():
                if k in context_fields or k in {"sftp", "form_data"}:
                    continue
                if k not in sftp_payload:
                    sftp_payload[k] = v

            # Backward-compatible key normalization
            if "file_mapping_method" not in sftp_payload and "file_mapping_mode" in sftp_payload:
                sftp_payload["file_mapping_method"] = sftp_payload.get("file_mapping_mode")
            if "root_folder" not in sftp_payload and "folder_path" in sftp_payload:
                sftp_payload["root_folder"] = sftp_payload.get("folder_path")

            return {
                "sample_rows": temp_values.get("sample_rows", 1000),
                **{k: v for k, v in temp_values.items() if k in context_fields and k != "sample_rows"},
                "sftp": sftp_payload,
            }

        # Extract CSV/TSV options
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
                tsv_opts[k.replace("tsv_", "")] = v
            else:
                remaining[k] = v

        sftp_payload = remaining.copy()

        # Backward-compatible key normalization
        if "file_mapping_method" not in sftp_payload and "file_mapping_mode" in sftp_payload:
            sftp_payload["file_mapping_method"] = sftp_payload.get("file_mapping_mode")
        if "root_folder" not in sftp_payload and "folder_path" in sftp_payload:
            sftp_payload["root_folder"] = sftp_payload.get("folder_path")

        if csv_opts:
            sftp_payload["csv_options"] = csv_opts

        if tsv_opts:
            sftp_payload["tsv_options"] = tsv_opts

        return {
            "sample_rows": context.get("sample_rows", 1000),
            **{k: v for k, v in context.items() if k != "sample_rows"},
            "sftp": sftp_payload,
        }


