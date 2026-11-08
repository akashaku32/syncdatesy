from __future__ import annotations

import io
import json
import logging
import re
import time
import tempfile
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import requests
from fastapi import HTTPException
from google.cloud import storage

from models.amazon_s3_models import FileMappingMethod, FileType, LoadMethod
from models.google_drive_models import GoogleDriveListingTables, GoogleDrivePKSuggestionRequest, GoogleDrivePatternPreviewRequest
from models.google_drive_snowflake_models import GoogleDriveSnowflakeMigrationRequest
from services.amazon_s3.service import (
    GCP_PROJECT_ID,
    GCS_BUCKET_NAME,
    Q,
    _as_bool,
    _build_file_basename_counts,
    _fetch_snowflake_tables_from_config,
    _resolve_single_file_mapped_name,
    _snowflake_table_exists,
    _unique_table_name_for_file,
    align_chunk_to_table_column_order,
    copy_into_staging_from_gcs_batch,
    ensure_tables,
    merge_staging_into_final,
    normalize_and_log_snowflake_identifier,
    parse_datetime_from_db,
    reorder_chunk_columns_for_snowflake,
    resolve_gcs_bucket_name,
    resolve_snowflake_storage_integration,
    sanitize_identifier,
    extract_columns_for_pk_suggestion,
    sf_connect,
    staging_name,
    upload_chunk_to_gcs,
)
from services.google_drive.client import (
    GOOGLE_DRIVE_API_BASE,
    _extract_google_error,
    refresh_token,
    validate_google_drive_connection,
)
from services.sftp.as_source.to_snowflake import (
    is_sftp_file_compatible_with_type,
    read_sftp_file_in_chunks,
)
from utils.celery_cleanup_utils import handle_migration_completion
from utils.database_utils import (
    data_transfer_logs,
    get_migration_record_id,
    get_last_checked_dates_and_fields,
    update_table_status_snowflake,
    postgres_connection,
)
from utils.error_utils import handle_service_error, insert_tool_log, log_migration_completion
from utils.gcp_utils import (
    assert_allowed_prefix_matches,
    ensure_bucket_iam_for_integration,
    ensure_gcs_storage_integration,
    gcs_client,
    get_gcp_service_account,
)
from utils.simple_progress_tracker import SimpleProgressTracker
from utils.s3_utils import log, log_prefix, table_from_file_key
from services.source_file_helpers.file_path_utils import join_posix_path, relative_to_root
from services.source_file_helpers.file_format_readers import read_dataframe_sample
from services.source_file_helpers.file_source_base import SourceFileEntry, infer_columns_from_dataframe, utc_isoformat
from services.source_file_helpers.file_source_mapping import resolve_file_entries

logger = logging.getLogger(__name__)


GOOGLE_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_APPS_MIME_PREFIX = "application/vnd.google-apps."
GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SHEETS_EXPORT_MIME_BY_TYPE = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
}
SUPPORTED_GOOGLE_DRIVE_SNOWFLAKE_FILE_TYPES = {"csv", "tsv", "json", "xml", "parquet", "avro"}


def _drive_headers(access_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _drive_request(method: str, url: str, access_token: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    merged = _drive_headers(access_token)
    merged.update(headers)
    return requests.request(method, url, headers=merged, timeout=60, **kwargs)


def _get_access_token(source_config: Dict[str, Any]) -> str:
    access_token = str(source_config.get("access_token") or "").strip()
    refresh_tok = str(source_config.get("refresh_token") or "").strip()
    client_id = str(source_config.get("CLIENT_ID") or source_config.get("client_id") or "").strip()
    client_secret = str(source_config.get("CLIENT_SECRET") or source_config.get("client_secret") or "").strip()

    expires_at = source_config.get("token_expires_at")
    should_refresh = bool(refresh_tok and client_id and client_secret)
    try:
        should_refresh = should_refresh and (not expires_at or int(expires_at) <= int(time.time()) + 120)
    except Exception:
        should_refresh = bool(refresh_tok and client_id and client_secret)

    if should_refresh:
        refreshed = refresh_token(refresh_tok, client_id, client_secret)
        access_token = refreshed.get("access_token") or access_token
        source_config["access_token"] = access_token
        source_config["refresh_token"] = refreshed.get("refresh_token") or refresh_tok
        if refreshed.get("token_expires_at"):
            source_config["token_expires_at"] = refreshed["token_expires_at"]

    if not access_token:
        raise HTTPException(status_code=400, detail="Google Drive access_token is required")
    return access_token


def _list_child_files(access_token: str, folder_id: str) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed=false"

    while True:
        params = {
            "q": query,
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "spaces": "drive",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = _drive_request("GET", f"{GOOGLE_DRIVE_API_BASE}/files", access_token, params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=422, detail=_extract_google_error(resp))

        payload = resp.json()
        files.extend(payload.get("files", []) or [])
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    return files


def _is_google_sheet(item: Optional[Dict[str, Any]]) -> bool:
    return str((item or {}).get("mimeType") or "") == GOOGLE_SHEETS_MIME


def _drive_file_key(item: Dict[str, Any]) -> str:
    return str(item.get("path") or item.get("id") or item.get("name") or "").strip()


def _drive_item_table_name(item: Dict[str, Any]) -> str:
    return sanitize_identifier(table_from_file_key(str(item.get("path") or item.get("name") or "")))


def _entry_effective_file_type(entry: SourceFileEntry, configured_file_type: Optional[str]) -> Optional[str]:
    configured = str(configured_file_type or "").strip().lower()
    if configured:
        return configured
    if str(entry.inferred_file_type or "").strip().lower() == "google_sheet":
        return "csv"
    return str(entry.inferred_file_type or "").strip().lower() or None


def _entry_display_file_type(entry: SourceFileEntry, configured_file_type: Optional[str]) -> Optional[str]:
    effective_type = _entry_effective_file_type(entry, configured_file_type)
    return effective_type or None


def _walk_drive_files(access_token: str, folder_id: str, include_subfolders: bool) -> List[Dict[str, Any]]:
    discovered: List[Dict[str, Any]] = []
    stack = [(folder_id, "")]
    seen_folders = set()

    while stack:
        current_folder, current_prefix = stack.pop()
        if current_folder in seen_folders:
            continue
        seen_folders.add(current_folder)

        for item in _list_child_files(access_token, current_folder):
            mime_type = item.get("mimeType")
            name = str(item.get("name") or item.get("id") or "").strip()
            if mime_type == GOOGLE_DRIVE_FOLDER_MIME:
                if include_subfolders:
                    child_prefix = join_posix_path(current_prefix, name)
                    stack.append((item.get("id"), child_prefix))
                continue
            if str(mime_type or "").startswith(GOOGLE_APPS_MIME_PREFIX) and mime_type != GOOGLE_SHEETS_MIME:
                log(f"[DRIVE][LIST] Skipping Google-native file '{item.get('name')}' ({mime_type})")
                continue
            item = dict(item)
            item["path"] = join_posix_path(current_prefix, name) or name or str(item.get("id") or "")
            discovered.append(item)

    return discovered


def _drive_items_to_entries(files: List[Dict[str, Any]]) -> List[SourceFileEntry]:
    entries: List[SourceFileEntry] = []
    for item in files or []:
        name = str(item.get("name") or item.get("id") or "").strip()
        path = str(item.get("path") or name or item.get("id") or "").strip()
        if not path:
            continue
        try:
            size_bytes = int(item.get("size") or 0)
        except Exception:
            size_bytes = 0
        entries.append(
            SourceFileEntry(
                file_key=path,
                relative_path=relative_to_root(path, ""),
                basename=name or path.rsplit("/", 1)[-1],
                size_bytes=size_bytes,
                modified_at=_parse_modified_time(item.get("modifiedTime")),
                inferred_file_type="google_sheet"
                if _is_google_sheet(item)
                else (name.rsplit(".", 1)[-1].lower() if "." in name else None),
            )
        )
    return entries


def _drive_item_matches_file_type(item: Optional[Dict[str, Any]], file_key: Any, file_type: str) -> bool:
    normalized_type = str(file_type or "").strip().lower()
    if not normalized_type:
        return True
    if _is_google_sheet(item) and normalized_type in GOOGLE_SHEETS_EXPORT_MIME_BY_TYPE:
        return True
    name = str((item or {}).get("name") or (item or {}).get("path") or file_key or "").strip().lower()
    return name.endswith(f".{normalized_type}")


def _extract_google_drive_config(request: GoogleDriveListingTables) -> Dict[str, Any]:
    config = request.GoogleDrive if isinstance(request.GoogleDrive, dict) else {}
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Invalid GoogleDrive config; expected object.")
    return config


def _validated_drive_listing_context(config: Dict[str, Any]) -> Dict[str, Any]:
    validated = validate_google_drive_connection({"GoogleDrive": config})
    access_token = validated.get("access_token") or config.get("access_token")
    folder_id = validated.get("folder_id") or config.get("folder_id")
    if not access_token:
        raise HTTPException(status_code=422, detail="Google Drive access_token is required")
    if not folder_id:
        raise HTTPException(status_code=422, detail="Google Drive folder_id is required")
    return {
        **config,
        "access_token": access_token,
        "refresh_token": validated.get("refresh_token") or config.get("refresh_token"),
        "token_expires_at": validated.get("token_expires_at") or config.get("token_expires_at"),
        "folder_id": folder_id,
        "folder_name": validated.get("folder_name") or config.get("folder_name"),
    }


def _load_existing_file_metadata(connection_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    existing_file_metadata: Dict[str, Dict[str, Any]] = {}
    if not connection_id:
        return existing_file_metadata

    try:
        conn_db = postgres_connection()
        cursor_db = conn_db.cursor()
        cursor_db.execute(
            """
            SELECT "table", folder_path, last_synced, primary_keys
            FROM source_table_selection
            WHERE connection_id = %s
            """,
            (connection_id,),
        )
        for table_name_raw, folder_path_raw, last_synced_raw, primary_keys_raw in cursor_db.fetchall() or []:
            file_path = str(folder_path_raw or "").strip()
            if not file_path:
                continue
            pk_list = []
            if isinstance(primary_keys_raw, list):
                pk_list = [str(k).strip() for k in primary_keys_raw if str(k).strip()]
            elif isinstance(primary_keys_raw, str):
                try:
                    parsed_pk = json.loads(primary_keys_raw)
                    if isinstance(parsed_pk, list):
                        pk_list = [str(k).strip() for k in parsed_pk if str(k).strip()]
                except Exception:
                    pk_list = []
            existing_file_metadata[file_path] = {
                "table": sanitize_identifier(str(table_name_raw).strip()) if str(table_name_raw).strip() else "",
                "is_migrated": bool(str(last_synced_raw or "").strip()),
                "primary_keys": pk_list,
                "last_synced": str(last_synced_raw or "").strip(),
            }
        cursor_db.close()
        conn_db.close()
    except Exception as exc:
        logger.warning("Could not load existing Google Drive file metadata: %s", exc)
    return existing_file_metadata


def _parse_modified_time(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _add_metadata_to_df(df, file_key: str, last_modified: Optional[datetime]):
    if df is None or df.empty:
        return df

    import pandas as pd

    source_file_key = str(file_key or "").strip()
    if "_FILE" not in df.columns:
        df.insert(0, "_FILE", source_file_key)
    else:
        df["_FILE"] = source_file_key

    if "_ROW" not in df.columns:
        df.insert(1, "_ROW", range(len(df)))
    else:
        df["_ROW"] = df["_ROW"].astype(int)

    lm_utc = pd.to_datetime(last_modified or datetime.now(timezone.utc), utc=True)
    ds_utc = pd.Timestamp.now(tz="UTC")

    if "_MODIFIED" not in df.columns:
        df.insert(2, "_MODIFIED", lm_utc)
    else:
        df["_MODIFIED"] = lm_utc

    if "_SPECT_SYNCED" not in df.columns:
        df.insert(3, "_SPECT_SYNCED", ds_utc)
    else:
        df["_SPECT_SYNCED"] = ds_utc

    if "_SPECT_DELETED" not in df.columns:
        df.insert(4, "_SPECT_DELETED", False)
    else:
        df["_SPECT_DELETED"] = False

    return df


def _build_reader_kwargs(config: Dict[str, Any], file_type: str) -> Dict[str, Any]:
    return {
        "file_type": file_type,
        "delimiter": config.get("delimiter"),
        "quotechar": config.get("quotechar"),
        "escape_char": config.get("escape_char"),
        "skipheaderlines": config.get("skipheaderlines"),
        "skip_footer_lines": config.get("skip_footer_lines"),
        "line_separator": config.get("line_separator"),
        "json_mode": config.get("json_mode"),
        "xml_mode": config.get("xml_mode"),
    }


def _validate_drive_schema_file_types(
    table_groups: Dict[str, List[SourceFileEntry]],
    configured_file_type: str,
    client: "GoogleDriveFileClient",
    config: Dict[str, Any],
) -> None:
    """Validate every mapped Drive file before Update Schema changes metadata.

    Keep schema refresh behaviour aligned with the SFTP/S3 file connectors:
    reject an obvious extension mismatch and perform the same content preflight
    used by the Google Drive migration reader.  Without this check, a JSON/XML
    file selected as CSV can be accepted by pandas as a one-column CSV during
    schema refresh.
    """
    expected_type = str(configured_file_type or "").strip().lower()
    if not expected_type:
        return

    reader_options = _build_reader_kwargs(config, expected_type)
    for files_for_table in table_groups.values():
        for entry in files_for_table or []:
            detected_type = str(entry.inferred_file_type or "").strip().lower()

            # Google Sheets are exported as either CSV or TSV, so their Drive
            # MIME type is compatible with both supported export formats.
            is_supported_sheet_export = (
                detected_type == "google_sheet"
                and expected_type in GOOGLE_SHEETS_EXPORT_MIME_BY_TYPE
            )
            if detected_type and not is_supported_sheet_export and detected_type != expected_type:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Update schema failed: configured file_type "
                        f"'{expected_type}' does not match file '{entry.file_key}' "
                        f"(detected '{detected_type}')."
                    ),
                )

            try:
                prepare_drive_file_for_read(
                    client,
                    entry.file_key,
                    file_type=expected_type,
                    **{
                        option: reader_options.get(option)
                        for option in (
                            "delimiter",
                            "quotechar",
                            "skipheaderlines",
                            "escape_char",
                            "skip_footer_lines",
                            "line_separator",
                        )
                    },
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Update schema failed: {exc}",
                ) from exc


def _infer_google_drive_migration_file_type(
    source_config: Dict[str, Any],
    client: "GoogleDriveFileClient",
    selected_files: List[str],
) -> str:
    configured_type = str(source_config.get("file_type") or "").strip().lower()
    if configured_type:
        return configured_type

    first_key = selected_files[0] if selected_files else ""
    first = client.resolve(first_key)
    first_name = str(first.get("name") if first else first_key).lower()
    for candidate in ("tsv", "json", "xml", "parquet", "avro"):
        if first_name.endswith(f".{candidate}"):
            return candidate
    return "csv"


class GoogleDriveFileClient:
    def __init__(self, access_token: str, files: List[Dict[str, Any]], export_file_type: Optional[str] = None):
        self.access_token = access_token
        self.export_file_type = str(export_file_type or "csv").strip().lower()
        self.files_by_id = {str(item.get("id")): item for item in files if item.get("id")}
        self.files_by_name = {str(item.get("name")): item for item in files if item.get("name")}
        self.files_by_path = {str(item.get("path")): item for item in files if item.get("path")}

    def resolve(self, key: str) -> Optional[Dict[str, Any]]:
        raw = str(key or "").strip()
        return self.files_by_id.get(raw) or self.files_by_path.get(raw) or self.files_by_name.get(raw)

    def stat(self, key: str) -> SimpleNamespace:
        item = self.resolve(key)
        if not item:
            raise FileNotFoundError(f"Google Drive file not found: {key}")
        modified = _parse_modified_time(item.get("modifiedTime"))
        try:
            size = int(item.get("size") or 0)
        except Exception:
            size = 0
        return SimpleNamespace(st_mtime=modified.timestamp(), st_size=size)

    def open(self, key: str, mode: str = "rb"):
        item = self.resolve(key)
        if not item:
            raise FileNotFoundError(f"Google Drive file not found: {key}")
        file_id = item.get("id")
        if _is_google_sheet(item):
            export_mime = GOOGLE_SHEETS_EXPORT_MIME_BY_TYPE.get(self.export_file_type)
            if not export_mime:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Google Sheets export is only supported for CSV/TSV migrations, "
                        f"not file_type '{self.export_file_type}'"
                    ),
                )
            resp = _drive_request(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}/export",
                self.access_token,
                params={"mimeType": export_mime},
                stream=True,
            )
        else:
            resp = _drive_request(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
                self.access_token,
                params={"alt": "media", "supportsAllDrives": "true"},
                stream=True,
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=422, detail=_extract_google_error(resp))

        stream = tempfile.SpooledTemporaryFile(mode="w+b", max_size=5 * 1024 * 1024)
        try:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    stream.write(chunk)
            stream.seek(0)
            return stream
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise


class _GoogleDriveBytesClient:
    """Expose one downloaded Drive file as a fresh pandas-compatible stream."""

    def __init__(self, key: str, content: bytes, stat_result: SimpleNamespace):
        self.key = key
        self.content = content
        self.stat_result = stat_result

    def stat(self, key: str) -> SimpleNamespace:
        if key != self.key:
            raise FileNotFoundError(f"Google Drive file not found: {key}")
        return self.stat_result

    def open(self, key: str, mode: str = "rb") -> io.BytesIO:
        if key != self.key:
            raise FileNotFoundError(f"Google Drive file not found: {key}")
        return io.BytesIO(self.content)


def _download_drive_file_as_bytes_client(
    client: GoogleDriveFileClient,
    key: str,
) -> _GoogleDriveBytesClient:
    """Download one Drive file and expose repeatable, parser-safe streams.

    Python 3.10 SpooledTemporaryFile does not provide ``readable()``, which
    pandas requires for CSV/TSV parsing. Materialize the download once so the
    format preflight and parser inspect exactly the same bytes.
    """
    source_stream = client.open(key, "rb")
    try:
        try:
            source_stream.seek(0)
        except Exception:
            pass
        content = source_stream.read()
    finally:
        try:
            source_stream.close()
        except Exception:
            pass

    if isinstance(content, str):
        content = content.encode("utf-8")
    stat_result = client.stat(key)
    return _GoogleDriveBytesClient(key, content or b"", stat_result)


def _read_drive_dataframe_sample(
    client: GoogleDriveFileClient,
    key: str,
    **reader_options,
):
    """Read a Drive sample from a normalized, rewound ``BytesIO`` stream."""
    bytes_client = _download_drive_file_as_bytes_client(client, key)
    with bytes_client.open(key, "rb") as stream:
        return read_dataframe_sample(stream, **reader_options)


def prepare_drive_file_for_read(
    client: GoogleDriveFileClient,
    key: str,
    *,
    file_type: str,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    escape_char: Optional[str] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
) -> _GoogleDriveBytesClient:
    """Download and content-validate a Drive file before schema/data parsing."""
    bytes_client = _download_drive_file_as_bytes_client(client, key)
    ok, reason = is_sftp_file_compatible_with_type(
        bytes_client,
        key,
        file_type,
        delimiter=delimiter,
        quotechar=quotechar,
        skipheaderlines=skipheaderlines,
        escape_char=escape_char,
        skip_footer_lines=skip_footer_lines,
        line_separator=line_separator,
    )
    if not ok:
        raise ValueError(
            f"Google Drive file '{key}' is not valid {str(file_type).upper()} content: {reason}"
        )
    return bytes_client


def read_drive_file_in_chunks(
    client: GoogleDriveFileClient,
    key: str,
    **reader_options,
):
    """Validate and parse a Drive file with the proven SFTP format readers."""
    validation_options = {
        option: reader_options.get(option)
        for option in (
            "delimiter",
            "quotechar",
            "skipheaderlines",
            "escape_char",
            "skip_footer_lines",
            "line_separator",
        )
    }
    bytes_client = prepare_drive_file_for_read(
        client,
        key,
        file_type=reader_options.get("file_type"),
        **validation_options,
    )
    yield from read_sftp_file_in_chunks(
        bytes_client,
        key,
        **reader_options,
    )


def _normalize_single_file_map(source_config: Dict[str, Any]) -> Dict[str, str]:
    mapping = source_config.get("single_file_table_mapping") or {}
    if isinstance(mapping, str):
        try:
            mapping = json.loads(mapping)
        except Exception:
            mapping = {}
    if not isinstance(mapping, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip() and str(v).strip()}


def _resolve_selected_files(
    selected_tables: List[Any],
    drive_files: List[Dict[str, Any]],
    single_file_map: Dict[str, str],
    file_mapping_mode: str,
    multi_files_table_name: str,
    multi_files_prefix: str = "",
) -> List[str]:
    if file_mapping_mode == "multi_files_single_table":
        prefix = str(multi_files_prefix or "").strip()
        if prefix:
            log(f"[DRIVE][MIGRATION] multi_files_prefix is deprecated and ignored (pattern='{prefix}')")
        return [
            str(item.get("id") or item.get("path") or item.get("name")).strip()
            for item in drive_files
            if item.get("id") or item.get("path") or item.get("name")
        ]

    if not selected_tables:
        return list(single_file_map.keys()) or [
            str(item.get("id") or item.get("name")).strip() for item in drive_files if item.get("id") or item.get("name")
        ]

    files_by_id = {str(item.get("id")): item for item in drive_files if item.get("id")}
    files_by_name = {str(item.get("name")): item for item in drive_files if item.get("name")}
    files_by_key = {
        key: item
        for item in drive_files
        for key in {str(item.get("id") or "").strip(), str(item.get("name") or "").strip(), str(item.get("path") or "").strip()}
        if key
    }
    selected: List[str] = []
    mapped_table_lookup = {table.upper(): key for key, table in single_file_map.items()}

    def newer_matching_sheet_key(table_name: str, current_key: Optional[str]) -> Optional[str]:
        normalized_table = sanitize_identifier(table_name)
        if not normalized_table:
            return None
        current_item = files_by_key.get(str(current_key or "").strip())
        if _is_google_sheet(current_item):
            return None
        current_modified = _parse_modified_time(current_item.get("modifiedTime")) if current_item else None
        candidates = [
            item
            for item in drive_files
            if _is_google_sheet(item) and _drive_item_table_name(item).upper() == normalized_table.upper()
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: _parse_modified_time(item.get("modifiedTime")), reverse=True)
        latest = candidates[0]
        latest_modified = _parse_modified_time(latest.get("modifiedTime"))
        if current_modified and latest_modified <= current_modified:
            return None
        latest_key = _drive_file_key(latest)
        if latest_key:
            log(
                f"[DRIVE][MIGRATION] Using newer Google Sheet '{latest_key}' for table '{normalized_table}' "
                f"instead of mapped file '{current_key}'"
            )
        return latest_key

    for value in selected_tables:
        raw = str(value or "").strip()
        if not raw:
            continue
        if raw in single_file_map or raw in files_by_id or raw in files_by_name:
            table_name = single_file_map.get(raw) or _drive_item_table_name(files_by_key.get(raw) or {})
            selected.append(newer_matching_sheet_key(table_name, raw) or raw)
            continue
        mapped_key = mapped_table_lookup.get(raw.upper())
        if mapped_key:
            selected.append(newer_matching_sheet_key(raw, mapped_key) or mapped_key)
            continue

    return selected


def _table_for_file(file_key: str, client: GoogleDriveFileClient, single_file_map: Dict[str, str]) -> str:
    mapped = single_file_map.get(file_key)
    item = client.resolve(file_key)
    if not mapped and item:
        mapped = single_file_map.get(str(item.get("id") or "")) or single_file_map.get(str(item.get("name") or ""))
    if mapped:
        return sanitize_identifier(mapped)
    if item and item.get("name"):
        return table_from_file_key(str(item["name"]))
    return table_from_file_key(file_key)


def _build_safety_cutoff(safety_interval: Any) -> Optional[datetime]:
    raw = str(safety_interval or "").strip()
    if not raw or raw.lower() == "none":
        return None
    try:
        minutes = int(raw)
    except Exception:
        return None
    if minutes <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _is_manual_google_drive_migration(job_name: Any) -> bool:
    return "_manual_" in str(job_name or "").lower()


def _should_skip_unchanged_file(load_method: Any, last_synced_dt: Optional[datetime], file_modified: datetime) -> bool:
    if not last_synced_dt:
        return False
    return file_modified <= last_synced_dt


def _should_apply_delta_check(load_method: Any, is_manual_migration: bool) -> bool:
    """Match ADLS: manual runs bypass delta checks except file+row upserts."""
    return not is_manual_migration or str(load_method or "").strip() == "upsert_file_row"


def _should_treat_no_op_merge_as_failure(load_method: Any) -> bool:
    return str(load_method or "").strip() != "append_file_modified"


def _last_synced_from_table_state(table_state: Any) -> Any:
    """Read the timestamp key returned by get_last_checked_dates_and_fields()."""
    if not isinstance(table_state, dict):
        return None
    return table_state.get("last_checked_date")


def _log_skipped_transfer(
    *,
    cmp_id: int,
    connection_id: int,
    user_id: int,
    table_name: str,
    skipped_at: datetime,
    session: Any,
    job_name: str,
    message: Optional[str] = None,
    status: str = "S",
    migration_session_id: Any = None,
) -> None:
    if not job_name:
        return
    if message:
        migration_record_id = None
        conn = None
        cursor = None
        try:
            conn = postgres_connection()
            cursor = conn.cursor()
            migration_record_id = get_migration_record_id(
                cursor=cursor,
                job_name=job_name,
                table_name=table_name,
                connection_id=connection_id,
                allow_fallback=True,
            )
        except Exception as record_error:
            log(f"[DRIVE][SKIP] Failed to resolve migration record for skipped table '{table_name}': {record_error}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        insert_tool_log(
            cmp_id=cmp_id,
            connection_id=connection_id,
            usr_id=user_id,
            table_name=table_name,
            status=status,
            message=message,
            session=migration_session_id or session,
            job_name=job_name,
            migration_record_id=migration_record_id,
        )

    data_transfer_logs(
        cmp_id=cmp_id,
        connection_id=connection_id,
        user_id=user_id,
        table_name=table_name,
        start_time=skipped_at,
        end_time=skipped_at,
        columns_data=0,
        session=session,
        insert_success=0,
        insert_failure=0,
        upsert_success=0,
        upsert_failure=0,
        job_name=job_name,
    )


def _log_no_op_merge(
    *,
    cmp_id: int,
    connection_id: int,
    user_id: int,
    table_name: str,
    source_rows: int,
    session: Any,
    job_name: str,
    migration_session_id: Any = None,
) -> None:
    if not job_name:
        return

    no_op_message = (
        f"No changes detected for '{table_name}': {source_rows:,} source row(s) read, "
        "but all values already match the target table. Nothing to insert/update/delete."
    )
    log(f"[DRIVE][MERGE] {no_op_message}")


def _complete_google_drive_table_zero_rows(
    *,
    cmp_id: int,
    connection_id: int,
    user_id: int,
    table_name: str,
    session: Any,
    job_name: str,
    migration_session_id: Any,
    file_start_time: datetime | None = None,
) -> None:
    """Mark a table completed when the source file has no data rows."""
    complete_time = datetime.now(timezone.utc)
    update_table_status_snowflake(
        connection_id,
        table_name,
        "completed",
        job_name,
        migration_session_id,
        complete_time,
    )
    try:
        data_transfer_logs(
            cmp_id=cmp_id,
            connection_id=connection_id,
            usr_id=user_id,
            table_name=table_name,
            start_time=file_start_time or complete_time,
            end_time=complete_time,
            columns_data=0,
            session=session,
            total_rows=0,
            inserts=0,
            updates=0,
            deletes=0,
            job_name=job_name,
        )
    except Exception as transfer_log_error:
        log(
            f"[DRIVE][EMPTY] Failed to log zero-row completion for table '{table_name}': "
            f"{transfer_log_error}"
        )


def _record_drive_no_op_merge_failure(
    *,
    cmp_id: int,
    connection_id: int,
    user_id: int,
    table_name: str,
    source_rows: int,
    session: Any,
    job_name: str,
    migration_session_id: Any,
    log_id: Any,
    connection_name: str,
    company_name: str,
) -> str:
    """Persist and publish a hard failure when a migration reads rows but writes none."""
    message = (
        f"No changes detected for '{table_name}': {source_rows:,} source row(s) read, "
        "but nothing was written to Snowflake."
    )
    _persist_drive_table_failure(
        error=ValueError(message),
        connection_id=connection_id,
        table_name=table_name,
        job_name=job_name,
        migration_session_id=migration_session_id,
        log_id=log_id,
        cmp_id=cmp_id,
        user_id=user_id,
        connection_name=connection_name,
        company_name=company_name,
    )
    return message


def _http_error_detail(error: Exception) -> str:
    detail = getattr(error, "detail", None)
    if detail:
        return str(detail)
    return str(error).strip() or repr(error)


def _persist_drive_table_failure(
    *,
    error: Exception,
    connection_id: int,
    table_name: str,
    job_name: str,
    migration_session_id: int,
    log_id: Any,
    cmp_id: int,
    user_id: int,
    connection_name: str,
    company_name: str,
) -> None:
    """Persist the failure message before publishing the failed table state."""
    error_result = handle_service_error(
        error=error,
        context={
            "job_name": job_name,
            "log_id": log_id,
            "cmp_id": cmp_id,
            "connection_id": connection_id,
            "user_id": user_id,
            "operation": "MIGRATION",
            "table_name": table_name,
            "is_terminal_failure": False,
            "service_name": "Google Drive to Snowflake",
            "connection_name": connection_name,
            "company_name": company_name,
            "session": migration_session_id,
            "migration_session_id": migration_session_id,
        },
        send_email=True,
        is_final_attempt=True,
    )
    if not error_result.get("log_logged"):
        raise RuntimeError(
            f"Failed to persist Google Drive failure message for table '{table_name}'"
        ) from error

    # The status builder also treats the persisted F log as failed, so even if
    # this independently retried status update fails, it can never emit a
    # failed table with a null error_message.
    update_table_status_snowflake(
        connection_id,
        table_name,
        "failed",
        job_name,
        migration_session_id,
    )


def _report_drive_partial_table_failure(
    *,
    connection_id: int,
    table_name: str,
    failed_file_messages: List[str],
    job_name: str,
    migration_session_id: int,
    cmp_id: int,
    user_id: int,
    connection_name: str,
    company_name: str,
) -> Dict[str, bool]:
    """Publish one table warning and email for a partially loaded file group."""
    failure_count = len(failed_file_messages)
    details = "; ".join(failed_file_messages[:5])
    if failure_count > 5:
        details += f"; and {failure_count - 5} more failed file(s)"
    message = (
        f"Table '{table_name}' loaded partially: {failure_count} file(s) failed. "
        f"{details}"
    )

    warning_logged = False
    conn_db = None
    cursor_db = None
    try:
        conn_db = postgres_connection()
        cursor_db = conn_db.cursor()
        migration_record_id = get_migration_record_id(
            cursor=cursor_db,
            job_name=job_name,
            table_name=table_name,
            connection_id=connection_id,
            allow_fallback=True,
        )
        if migration_record_id:
            insert_tool_log(
                cmp_id=cmp_id,
                connection_id=connection_id,
                usr_id=user_id,
                table_name=table_name,
                status="P",
                message=message,
                session=migration_session_id,
                job_name=job_name,
                migration_record_id=migration_record_id,
            )
            warning_logged = True
    except Exception as warning_error:
        log(
            f"[DRIVE][MIGRATION] Failed to publish partial warning for "
            f"table '{table_name}': {warning_error}"
        )
    finally:
        if cursor_db:
            try:
                cursor_db.close()
            except Exception:
                pass
        if conn_db:
            try:
                conn_db.close()
            except Exception:
                pass

    email_sent = False
    try:
        from utils.email_utils import send_error_notification

        email_sent = send_error_notification(
            table_name=table_name,
            error_message=message,
            connection_name=connection_name,
            service_name="Google Drive to Snowflake",
            user_id=user_id,
            company_id=cmp_id,
            additional_context={
                "job_name": job_name,
                "connection_id": connection_id,
                "migration_session_id": migration_session_id,
                "company_name": company_name,
                "failed_files_count": failure_count,
                "failed_files": list(failed_file_messages),
                "partial_success": True,
            },
        )
    except Exception as email_error:
        log(
            f"[DRIVE][MIGRATION] Failed to send partial failure email for "
            f"table '{table_name}': {email_error}"
        )

    return {"warning_logged": warning_logged, "email_sent": bool(email_sent)}


def _mark_selected_drive_tables_failed(
    *,
    connection_id: int,
    selected_tables: List[Any],
    source_config: Dict[str, Any],
    file_mapping_mode: str,
    multi_files_table_name: str,
    job_name: str,
    migration_session_id: int,
) -> None:
    candidates = set()
    if file_mapping_mode == "multi_files_single_table":
        if multi_files_table_name:
            candidates.add(sanitize_identifier(multi_files_table_name))
    else:
        single_file_map = _normalize_single_file_map(source_config)
        for value in selected_tables or []:
            raw = str(value or "").strip()
            if not raw:
                continue
            mapped = (
                single_file_map.get(raw)
                or single_file_map.get(raw.split("/")[-1])
            )
            candidates.add(sanitize_identifier(mapped or raw))

    for table_name in sorted(name for name in candidates if name):
        try:
            update_table_status_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)
        except Exception as status_error:
            log(f"[MIGRATION] Failed to mark Google Drive table '{table_name}' as failed: {status_error}")


class GoogleDriveSnowflakeService:
    @staticmethod
    def list_files(data: GoogleDriveListingTables) -> Dict[str, Any]:
        config = _extract_google_drive_config(data)
        config = _validated_drive_listing_context(config)

        include_subfolders = _as_bool(config.get("include_subfolders"))
        file_type = str(config.get("file_type") or "").strip().lower()
        files = _walk_drive_files(config["access_token"], config["folder_id"], include_subfolders)
        entries = _drive_items_to_entries(files)
        entries = sorted(entries, key=lambda item: item.file_key.lower())

        file_mapping_mode = str(
            config.get("file_mapping_method")
            or config.get("file_mapping_mode")
            or "single_table_per_file"
        ).strip()
        single_file_map = config.get("single_file_table_mapping") or {}

        mapped_lookup: Dict[str, str] = {}
        if file_mapping_mode == "single_table_per_file" and isinstance(single_file_map, dict):
            for mapped_file_key, mapped_table in single_file_map.items():
                file_key = str(mapped_file_key or "").strip()
                mapped_table_name = sanitize_identifier(str(mapped_table or "").strip())
                if file_key and mapped_table_name:
                    mapped_lookup[file_key] = mapped_table_name
        elif file_mapping_mode == "multi_files_single_table":
            table_to_files = config.get("table_to_files_mapping") or {}
            if isinstance(table_to_files, dict):
                for table_name, file_list in table_to_files.items():
                    mapped_table_name = sanitize_identifier(str(table_name or "").strip())
                    if not mapped_table_name or not isinstance(file_list, list):
                        continue
                    for mapped_file_key in file_list:
                        file_key = str(mapped_file_key or "").strip()
                        if file_key:
                            mapped_lookup[file_key] = mapped_table_name

        all_file_keys = [entry.file_key for entry in entries]
        basename_counts = _build_file_basename_counts(all_file_keys)

        existing_sf_tables = None
        try:
            if config.get("destination_config"):
                existing_sf_tables = _fetch_snowflake_tables_from_config(config.get("destination_config"))
        except Exception as exc:
            logger.warning("Failed to check Snowflake tables for Google Drive listing: %s", exc)

        existing_file_metadata = _load_existing_file_metadata(data.connection_id)

        tables = []
        used_table_names = set()
        file_id_by_path = {
            str(item.get("path") or item.get("name") or "").strip(): item.get("id")
            for item in files
        }
        for entry in entries:
            key = entry.file_key
            existing_meta = existing_file_metadata.get(key) or {}
            locked_table_name = sanitize_identifier(str(existing_meta.get("table") or "").strip())
            is_migrated = bool(existing_meta.get("is_migrated"))
            mapped_table = mapped_lookup.get(key)

            if not mapped_table and file_mapping_mode == "single_table_per_file":
                mapped_table = _resolve_single_file_mapped_name(
                    file_key=key,
                    single_file_map=single_file_map,
                    basename_counts=basename_counts,
                )

            snowflake_table_exists = None
            if existing_sf_tables is not None:
                if is_migrated and locked_table_name:
                    snowflake_table_exists = locked_table_name.upper() in existing_sf_tables
                elif mapped_table:
                    snowflake_table_exists = str(mapped_table).upper() in existing_sf_tables

            lock_table_name = bool(
                is_migrated
                and locked_table_name
                and (snowflake_table_exists is True)
            )

            if (
                mapped_table
                and existing_sf_tables is not None
                and snowflake_table_exists is False
                and is_migrated
            ):
                mapped_table = None

            table_name = _unique_table_name_for_file(
                file_key=key,
                mapped_table_name=locked_table_name if lock_table_name else mapped_table,
                used_names=used_table_names,
            )

            tables.append(
                {
                    "table": table_name,
                    "file_key": key,
                    "file_id": file_id_by_path.get(key),
                    "relative_path": entry.relative_path,
                    "basename": entry.basename,
                    "size_bytes": entry.size_bytes,
                    "last_modified": utc_isoformat(entry.modified_at),
                    "file_type": _entry_display_file_type(entry, file_type),
                    "already_mapped": bool(mapped_table),
                    "mapped_table": mapped_table,
                    "is_migrated": is_migrated,
                    "table_name_locked": bool(lock_table_name),
                    "locked_table_name": locked_table_name if lock_table_name else None,
                    "locked_primary_keys": existing_meta.get("primary_keys") if lock_table_name else [],
                    "locked_last_synced": existing_meta.get("last_synced") or None,
                }
            )

        return {
            "tables": tables,
            "files_count": len(entries),
            "folder_id": config.get("folder_id"),
            "folder_name": config.get("folder_name"),
            "include_subfolders": include_subfolders,
            "file_type": file_type or None,
        }

    @staticmethod
    def list_tables_with_schema(data: GoogleDriveListingTables) -> Dict[str, Any]:
        config = _extract_google_drive_config(data)
        config = _validated_drive_listing_context(config)

        include_subfolders = _as_bool(config.get("include_subfolders"))
        file_type = str(config.get("file_type") or "").strip().lower()
        drive_files = _walk_drive_files(config["access_token"], config["folder_id"], include_subfolders)
        all_entries = _drive_items_to_entries(drive_files)
        all_entries = sorted(all_entries, key=lambda item: item.file_key.lower())

        file_mapping_mode = str(
            config.get("file_mapping_method")
            or config.get("file_mapping_mode")
            or "single_table_per_file"
        ).strip()
        single_file_map = _normalize_single_file_map(config)

        if not single_file_map and file_mapping_mode == "single_table_per_file" and data.connection_id:
            try:
                from utils.database_utils import get_s3_mapping

                existing_mapping = get_s3_mapping(data.connection_id)
                if (
                    existing_mapping
                    and str(existing_mapping.get("file_mapping_mode") or "").strip() == "single_table_per_file"
                ):
                    db_map = existing_mapping.get("single_file_table_mapping") or {}
                    if isinstance(db_map, dict):
                        single_file_map = {
                            str(key).strip(): str(value).strip()
                            for key, value in db_map.items()
                            if str(key).strip() and str(value).strip()
                        }
            except Exception as exc:
                logger.warning("Could not load Google Drive file mapping from database: %s", exc)

        custom_primary_key = config.get("custom_primary_key")
        if isinstance(custom_primary_key, str):
            try:
                custom_primary_key = json.loads(custom_primary_key)
            except Exception:
                custom_primary_key = {}
        if not isinstance(custom_primary_key, dict):
            custom_primary_key = {}

        load_method = str(config.get("load_method") or "upsert_file_row").strip()
        if load_method == "upsert_custom_key" and not custom_primary_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    "custom_primary_key must be provided as a non-empty dict in the request "
                    "when load_method is 'upsert_custom_key'"
                ),
            )

        existing_file_keys = {
            str(candidate).strip()
            for item in drive_files
            for candidate in (item.get("id"), item.get("name"), item.get("path"))
            if str(candidate or "").strip()
        }
        stale_single_table_names = set()
        active_single_file_map = dict(single_file_map)
        if file_mapping_mode == "single_table_per_file" and single_file_map:
            active_single_file_map = {}
            for mapped_file_key, mapped_table in single_file_map.items():
                matched = str(mapped_file_key or "").strip() in existing_file_keys
                if not matched:
                    normalized_table = sanitize_identifier(str(mapped_table).strip())
                    if normalized_table:
                        stale_single_table_names.add(normalized_table)
                else:
                    active_single_file_map[mapped_file_key] = mapped_table

        if not all_entries:
            return {
                "tables": [],
                "files_count": 0,
                "compatible_files_count": 0,
                "incompatible_files_count": 0,
                "folder_id": config.get("folder_id"),
                "folder_name": config.get("folder_name"),
                "include_subfolders": include_subfolders,
                "file_mapping_method": file_mapping_mode,
                "base_folder_path": config.get("base_folder_path") or config.get("folder_id") or "",
                "single_file_table_mapping": single_file_map,
                "table_to_files_mapping": {},
                "cmp_id": data.cmp_id,
                "user_id": data.user_id,
                "source_id": config.get("source_id"),
                "prune_missing_tables": bool(stale_single_table_names),
                "stale_tables_to_prune": sorted(stale_single_table_names),
            }

        existing_file_metadata = _load_existing_file_metadata(data.connection_id)

        existing_sf_tables = None
        try:
            if config.get("destination_config"):
                existing_sf_tables = _fetch_snowflake_tables_from_config(config.get("destination_config"))
        except Exception as exc:
            logger.warning("Failed to check Snowflake tables for Google Drive schema listing: %s", exc)

        if (
            file_mapping_mode == "single_table_per_file"
            and single_file_map
            and not active_single_file_map
        ):
            # Every configured mapping is stale. Do not let the shared basename
            # matcher attach an unrelated same-name file to the deleted path.
            table_groups = {}
            meta = {
                "file_mapping_method": file_mapping_mode,
                "candidate_files_count": len(all_entries),
            }
        else:
            table_groups, meta = resolve_file_entries(
                all_entries,
                single_file_table_mapping=active_single_file_map,
                file_mapping_method=file_mapping_mode,
                multi_files_prefix=config.get("multi_files_prefix"),
                multi_files_table_name=config.get("multi_files_table_name"),
            )

        client = GoogleDriveFileClient(config["access_token"], drive_files, export_file_type=file_type)
        _validate_drive_schema_file_types(
            table_groups,
            file_type,
            client,
            config,
        )
        tables: List[Dict[str, Any]] = []
        grouped_file_mapping: Dict[str, List[str]] = {}

        for table_name in sorted(table_groups.keys()):
            files_for_table = table_groups.get(table_name) or []
            if not files_for_table:
                continue

            representative = files_for_table[0]
            effective_file_type = _entry_effective_file_type(representative, file_type)
            if not effective_file_type:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unable to infer file_type for Google Drive file: {representative.file_key}",
                )

            sample_df = _read_drive_dataframe_sample(
                client,
                representative.file_key,
                **_build_reader_kwargs(config, effective_file_type),
            )
            sample_df = _add_metadata_to_df(sample_df, representative.file_key, representative.modified_at)

            columns = infer_columns_from_dataframe(sample_df, load_method=load_method)

            if load_method == "upsert_custom_key" and custom_primary_key:
                table_name_upper = str(table_name).upper()
                pk_cols = None
                for key, cols in custom_primary_key.items():
                    if str(key).upper() == table_name_upper:
                        pk_cols = [str(col).upper() for col in cols]
                        break
                if pk_cols:
                    for col_name, column_meta in columns.items():
                        if str(col_name).upper() in pk_cols:
                            column_meta["is_primary_key"] = True
                            column_meta["data_type"] = "VARCHAR(512)"

            file_key_to_use = representative.file_key
            if file_mapping_mode == "single_table_per_file" and active_single_file_map:
                for mapped_file_key in active_single_file_map.keys():
                    if (
                        representative.file_key == mapped_file_key
                        or representative.file_key.endswith("/" + mapped_file_key)
                        or mapped_file_key.endswith("/" + representative.file_key)
                    ):
                        file_key_to_use = mapped_file_key
                        break

            lookup_key = (
                config.get("base_folder_path") or config.get("folder_id")
                if file_mapping_mode == "multi_files_single_table"
                else file_key_to_use
            )
            existing_meta = existing_file_metadata.get(str(lookup_key or "").strip()) or {}
            locked_table_name = sanitize_identifier(str(existing_meta.get("table") or "").strip())
            is_migrated = bool(existing_meta.get("is_migrated"))

            snowflake_table_exists = None
            if existing_sf_tables is not None:
                if is_migrated and locked_table_name:
                    snowflake_table_exists = locked_table_name.upper() in existing_sf_tables
                elif table_name:
                    snowflake_table_exists = str(table_name).upper() in existing_sf_tables

            lock_table_name = bool(
                is_migrated
                and locked_table_name
                and (snowflake_table_exists is True)
            )

            resolved_file = client.resolve(representative.file_key)

            tables.append(
                {
                    "table": table_name,
                    "file_key": file_key_to_use,
                    "file_id": resolved_file.get("id") if resolved_file else None,
                    "relative_path": representative.relative_path,
                    "basename": representative.basename,
                    "last_modified": utc_isoformat(representative.modified_at),
                    "columns": columns,
                    "grouped_files": [entry.file_key for entry in files_for_table],
                    "already_mapped": True,
                    "mapped_table": table_name,
                    "is_migrated": is_migrated,
                    "table_name_locked": bool(lock_table_name),
                    "locked_table_name": locked_table_name if lock_table_name else None,
                    "locked_primary_keys": existing_meta.get("primary_keys") if lock_table_name else [],
                    "locked_last_synced": existing_meta.get("last_synced") or None,
                }
            )
            grouped_file_mapping[table_name] = [entry.file_key for entry in files_for_table]

        return {
            "tables": tables,
            "files_count": meta.get("candidate_files_count", len(all_entries)),
            "compatible_files_count": len(all_entries),
            "incompatible_files_count": 0,
            "folder_id": config.get("folder_id"),
            "folder_name": config.get("folder_name"),
            "include_subfolders": include_subfolders,
            "file_type": file_type or None,
            "file_mapping_method": meta.get("file_mapping_method") or file_mapping_mode,
            "base_folder_path": config.get("base_folder_path") or config.get("folder_id") or "",
            "single_file_table_mapping": single_file_map,
            "table_to_files_mapping": grouped_file_mapping,
            "cmp_id": data.cmp_id,
            "user_id": data.user_id,
            "source_id": config.get("source_id"),
            "prune_missing_tables": bool(stale_single_table_names),
            "stale_tables_to_prune": sorted(stale_single_table_names),
        }

    @staticmethod
    def refresh_and_update_google_drive_tables_in_database(
        connection_id: int,
        tables_result: Dict[str, Any],
        job_name: Optional[str],
        migration_session_id: Optional[int] = None,
    ) -> Dict[str, int]:
        from services.sftp.source import SFTPSourceService

        return SFTPSourceService.refresh_and_update_sftp_tables_in_database(
            connection_id=connection_id,
            tables_result=tables_result,
            job_name=job_name,
            migration_session_id=migration_session_id,
        )

    @staticmethod
    def preview_pattern(data: GoogleDrivePatternPreviewRequest) -> Dict[str, Any]:
        config = _extract_google_drive_config(data)
        config = _validated_drive_listing_context(config)

        include_subfolders = _as_bool(config.get("include_subfolders"))
        file_type = str(config.get("file_type") or "").strip().lower()
        pattern = str(data.multi_files_prefix or config.get("multi_files_prefix") or "").strip()
        max_results = int(data.max_results or 50)

        files = _walk_drive_files(config["access_token"], config["folder_id"], include_subfolders)
        all_files = _drive_items_to_entries(files)

        is_valid_regex = True
        error_message = ""
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                is_valid_regex = False
                error_message = str(exc)
            logger.info("[GoogleDrive][PREVIEW] multi_files_prefix is deprecated and ignored (pattern='%s')", pattern)

        matched_files = sorted(all_files, key=lambda item: item.file_key.lower())
        suggested_table_name = table_from_file_key(matched_files[0].file_key if matched_files else "")

        return {
            "status": "success",
            "pattern": pattern,
            "total_files_in_folder": len(all_files),
            "matched_files_count": len(matched_files),
            "matched_files": [
                {
                    "file_key": entry.file_key,
                    "relative_path": entry.relative_path,
                    "basename": entry.basename,
                    "last_modified": utc_isoformat(entry.modified_at),
                }
                for entry in matched_files[:max_results]
            ],
            "sample_non_matched_files": [],
            "pattern_validation": {
                "is_valid_regex": is_valid_regex,
                "error_message": error_message,
            },
            "suggested_table_name": suggested_table_name,
            "folder_id": config.get("folder_id"),
            "folder_name": config.get("folder_name"),
            "include_subfolders": include_subfolders,
            "file_type": file_type or None,
            "pattern_ignored": bool(pattern),
        }

    @staticmethod
    async def suggest_primary_keys(request: GoogleDrivePKSuggestionRequest) -> Dict[str, Any]:
        drive_config = request.drive
        if drive_config is None:
            raise HTTPException(status_code=422, detail="GoogleDrive config is required")

        log("[GoogleDrive][PK_SUGGEST] Starting PK suggestion")

        if drive_config.load_method != LoadMethod.upsert_custom_key:
            raise HTTPException(
                status_code=422,
                detail="Primary key suggestion is only supported for load_method = upsert_custom_key",
            )

        config = drive_config.dict() if hasattr(drive_config, "dict") else drive_config.model_dump()
        config = _validated_drive_listing_context(config)

        include_subfolders = _as_bool(config.get("include_subfolders"))
        file_type = drive_config.file_type.value
        drive_files = _walk_drive_files(config["access_token"], config["folder_id"], include_subfolders)
        all_entries = _drive_items_to_entries(drive_files)
        all_entries = sorted(all_entries, key=lambda item: item.file_key.lower())

        client = GoogleDriveFileClient(config["access_token"], drive_files, export_file_type=file_type or "csv")
        entry_by_key = {entry.file_key: entry for entry in all_entries}
        existing_file_keys = set(entry_by_key.keys())

        if drive_config.file_mapping_method == FileMappingMethod.single_table_per_file:
            file_keys = []
            seen_file_keys = set()
            for mapped_file_key in (drive_config.single_file_table_mapping or {}).keys():
                mapped_key = str(mapped_file_key or "").strip()
                matched_key = None
                for file_key in existing_file_keys:
                    if file_key == mapped_key or file_key.endswith("/" + mapped_key) or mapped_key.endswith("/" + file_key):
                        matched_key = file_key
                        break
                if not matched_key and client.resolve(mapped_key):
                    matched_key = mapped_key
                if matched_key and matched_key not in seen_file_keys:
                    seen_file_keys.add(matched_key)
                    file_keys.append(matched_key)
            is_multi_file_mode = False
        else:
            prefix = str(drive_config.multi_files_prefix or "").strip()
            if prefix:
                log(f"[GoogleDrive][PK_SUGGEST] multi_files_prefix is deprecated and ignored (pattern='{prefix}')")
            file_keys = [entry.file_key for entry in all_entries]
            is_multi_file_mode = True

        if not file_keys:
            return {
                "status": "success",
                "tables": [],
                "message": "No files provided for analysis",
            }

        log(f"[GoogleDrive][PK_SUGGEST] Files to analyze: {len(file_keys)}")

        if drive_config.file_type == FileType.csv:
            opts = drive_config.csv_options
        elif drive_config.file_type == FileType.tsv:
            opts = drive_config.tsv_options
        else:
            opts = None

        reader_kwargs = {
            "file_type": file_type,
            "delimiter": opts.delimiter if opts else None,
            "quotechar": opts.quotechar if opts else None,
            "escape_char": opts.escape_char if opts else None,
            "skipheaderlines": opts.skipheaderlines if opts else None,
            "skip_footer_lines": opts.skip_footer_lines if opts else None,
            "line_separator": opts.line_separator if opts else None,
            "json_mode": drive_config.json_mode.value if drive_config.json_mode else None,
            "xml_mode": drive_config.xml_mode.value if drive_config.xml_mode else None,
            "sample_rows": request.sample_rows,
        }

        tables_buffer = []
        files_to_read = file_keys[:5] if is_multi_file_mode else file_keys
        log(f"[GoogleDrive][PK_SUGGEST] Analyzing {len(files_to_read)} files (out of {len(file_keys)})")

        for file_key in files_to_read:
            try:
                log(f"[GoogleDrive][PK_SUGGEST] Reading file: {file_key}")
                entry = entry_by_key.get(file_key)
                mtime = entry.modified_at if entry else None
                if mtime is None:
                    try:
                        mtime = datetime.fromtimestamp(client.stat(file_key).st_mtime, tz=timezone.utc)
                    except Exception:
                        mtime = datetime.now(timezone.utc)

                df = _read_drive_dataframe_sample(client, file_key, **reader_kwargs)
                df = _add_metadata_to_df(df, file_key, mtime)
            except Exception as read_error:
                log(f"[GoogleDrive][PK_SUGGEST] Error reading file {file_key}: {read_error}")
                continue

            if df is None or df.empty:
                log(f"[GoogleDrive][PK_SUGGEST] Skipping empty file: {file_key}")
                continue

            mapping = drive_config.single_file_table_mapping or {}
            if is_multi_file_mode and drive_config.multi_files_table_name:
                table_name = sanitize_identifier(str(drive_config.multi_files_table_name).strip())
            else:
                mapped_name = mapping.get(file_key) or mapping.get(file_key.split("/")[-1])
                if not mapped_name:
                    for mapped_file_key, mapped_table in mapping.items():
                        mapped_key = str(mapped_file_key or "").strip()
                        if file_key == mapped_key or file_key.endswith("/" + mapped_key) or mapped_key.endswith("/" + file_key):
                            mapped_name = mapped_table
                            break
                table_name = sanitize_identifier(str(mapped_name).strip()) if mapped_name else _table_for_file(file_key, client, mapping)

            tables_buffer.append({
                "table_name": table_name,
                "file_key": file_key,
                "columns": extract_columns_for_pk_suggestion(df),
                "df": df,
            })

        if not tables_buffer:
            raise HTTPException(
                status_code=400,
                detail="No readable files found for primary key suggestion. Check file paths and format options.",
            )

        if is_multi_file_mode and len(tables_buffer) > 1:
            expected_columns = None
            if request.connection_id:
                try:
                    target_table = (
                        sanitize_identifier(str(drive_config.multi_files_table_name).strip())
                        if drive_config.multi_files_table_name
                        else None
                    )
                    if target_table:
                        conn_db = postgres_connection()
                        cur_db = conn_db.cursor()
                        cur_db.execute(
                            """
                            SELECT table_fields
                            FROM source_table_selection
                            WHERE connection_id = %s AND UPPER("table") = UPPER(%s)
                            """,
                            (request.connection_id, target_table),
                        )
                        row = cur_db.fetchone()
                        cur_db.close()
                        conn_db.close()
                        if row and row[0]:
                            table_fields = row[0]
                            if isinstance(table_fields, str):
                                try:
                                    table_fields = json.loads(table_fields)
                                except Exception:
                                    table_fields = {}
                            if isinstance(table_fields, dict):
                                expected_columns = {str(key).strip() for key in table_fields.keys() if str(key).strip()}
                except Exception as expected_error:
                    log(f"[GoogleDrive][PK_SUGGEST] Failed to read expected schema: {expected_error}")

            valid_files = []
            skipped_files = []
            if expected_columns:
                for item in tables_buffer:
                    if set(item["columns"].keys()) == expected_columns:
                        valid_files.append(item)
                    else:
                        skipped_files.append(item["file_key"])
                if not valid_files:
                    expected_columns = None

            if not expected_columns:
                reference = tables_buffer[0]["columns"]
                for item in tables_buffer:
                    if set(item["columns"].keys()) == set(reference.keys()):
                        valid_files.append(item)
                    else:
                        skipped_files.append(item["file_key"])

            if not valid_files:
                raise HTTPException(status_code=400, detail="No files with compatible schemas found for primary key suggestion.")

            tables_buffer = valid_files[:1]
            tables_buffer[0]["file_keys"] = [item["file_key"] for item in valid_files]
            tables_buffer[0]["skipped_files"] = skipped_files

        analyzed_tables = []
        table_pk_state = {}
        snowflake_tables = _fetch_snowflake_tables_from_config(drive_config.destination_config)
        if snowflake_tables is not None:
            log(f"[GoogleDrive][PK_SUGGEST][DEBUG] Snowflake tables available: {len(snowflake_tables)}")

        if request.connection_id:
            try:
                conn_db = postgres_connection()
                cur_db = conn_db.cursor()
                cur_db.execute(
                    """
                    SELECT "table", primary_keys, last_synced
                    FROM source_table_selection
                    WHERE connection_id = %s
                    """,
                    (request.connection_id,),
                )
                for table_name, primary_keys_raw, last_synced in (cur_db.fetchall() or []):
                    table_key = str(table_name or "").strip().upper()
                    if not table_key:
                        continue
                    existing_primary_keys = []
                    if isinstance(primary_keys_raw, list):
                        existing_primary_keys = [str(pk).strip() for pk in primary_keys_raw if str(pk).strip()]
                    elif isinstance(primary_keys_raw, str):
                        try:
                            parsed = json.loads(primary_keys_raw)
                            if isinstance(parsed, list):
                                existing_primary_keys = [str(pk).strip() for pk in parsed if str(pk).strip()]
                        except Exception:
                            existing_primary_keys = []
                    table_pk_state[table_key] = {
                        "pk_locked": bool(last_synced) and bool(existing_primary_keys),
                        "existing_primary_keys": existing_primary_keys,
                    }
                cur_db.close()
                conn_db.close()
            except Exception as pk_state_error:
                log(f"[GoogleDrive][PK_SUGGEST] Failed to read PK lock metadata: {pk_state_error}")

        for item in tables_buffer:
            df_sample = item["df"].head(request.sample_rows)
            rows_analyzed = len(df_sample)
            analyzed_columns = []

            for col_name, col_meta in item["columns"].items():
                col_upper = str(col_name).strip().upper()
                if col_upper in {"_SPECT_SYNCED", "_SPECT_DELETED"}:
                    continue

                series = None
                for original_col in df_sample.columns:
                    if (
                        original_col == col_name
                        or sanitize_identifier(original_col) == col_name
                        or str(original_col).upper() == col_name.upper()
                    ):
                        series = df_sample[original_col]
                        break

                if series is None:
                    column_obj = {
                        "column_name": col_name,
                        "data_type": col_meta.get("data_type"),
                        "uniqueness_score": 0.0,
                        "non_null_score": 0.0,
                        "warnings": ["Column not found in sample"],
                    }
                    if "is_selected" in col_meta:
                        column_obj["is_selected"] = col_meta["is_selected"]
                    analyzed_columns.append(column_obj)
                    continue

                if rows_analyzed == 0:
                    unique_pct = 0.0
                    non_null_pct = 0.0
                else:
                    unique_pct = (series.nunique() / rows_analyzed) * 100
                    non_null_pct = ((rows_analyzed - series.isna().sum()) / rows_analyzed) * 100

                warnings = []
                if unique_pct < 100:
                    warnings.append("Contains duplicate values")
                if non_null_pct < 100:
                    warnings.append("Contains NULL values")

                column_obj = {
                    "column_name": col_name,
                    "data_type": col_meta.get("data_type"),
                    "uniqueness_score": round(unique_pct, 2),
                    "non_null_score": round(non_null_pct, 2),
                    "warnings": warnings,
                }
                if "is_selected" in col_meta:
                    column_obj["is_selected"] = col_meta["is_selected"]
                analyzed_columns.append(column_obj)

            analyzed_columns.sort(key=lambda column: item["columns"][column["column_name"]]["ordinal_position"])
            table_key = str(item["table_name"]).strip().upper()
            table_state = table_pk_state.get(table_key, {})
            if (
                snowflake_tables is not None
                and table_key
                and table_key not in snowflake_tables
                and table_state.get("pk_locked")
            ):
                table_state = {
                    "pk_locked": False,
                    "existing_primary_keys": table_state.get("existing_primary_keys", []),
                }

            analyzed_tables.append({
                "table_name": item["table_name"],
                "file_key": item.get("file_key"),
                "file_keys": item.get("file_keys"),
                "rows_analyzed": rows_analyzed,
                "columns": analyzed_columns,
                "skipped_files": item.get("skipped_files"),
                "pk_locked": table_state.get("pk_locked", False),
                "existing_primary_keys": table_state.get("existing_primary_keys", []),
            })

        return {
            "status": "success",
            "tables": analyzed_tables,
            "total_files_analyzed": len(file_keys),
            "total_rows_analyzed": sum(table["rows_analyzed"] for table in analyzed_tables),
        }

    @staticmethod
    async def migrate(request: GoogleDriveSnowflakeMigrationRequest) -> Dict[str, Any]:
        token = log_prefix.set("GoogleDrive")
        try:
            return await GoogleDriveSnowflakeService._sync_google_drive_to_snowflake(request)
        finally:
            log_prefix.reset(token)

    @staticmethod
    async def _sync_google_drive_to_snowflake(request: GoogleDriveSnowflakeMigrationRequest) -> Dict[str, Any]:
        log("=" * 80)
        log("[MIGRATION] Starting Google Drive to Snowflake migration")
        log("=" * 80)

        source_config = dict(request.source_config or {})
        dest_config = dict(request.destination_config or {})
        connection_id = request.connection_id
        cmp_id = request.cmp_id
        user_id = request.user_id
        migration_session_id = request.migration_session_id
        job_name = request.job_name or f"google_drive_migration_{migration_session_id}"
        chunk_count = request.chunk_count or 5000
        connection_name = (request.connection_name or f"GOOGLE_DRIVE_CONNECTION_{connection_id}").strip()
        company_name = (request.company_name or "").strip()
        drop_and_reload = bool(request.drop_and_reload)
        log_id = getattr(request, "log_id", None)
        start_time = time.time()

        access_token = _get_access_token(source_config)
        file_mapping_mode = (
            source_config.get("file_mapping_mode")
            or source_config.get("file_mapping_method")
            or "single_table_per_file"
        )
        if not isinstance(file_mapping_mode, str):
            file_mapping_mode = "single_table_per_file"
        multi_files_table_name = sanitize_identifier(source_config.get("multi_files_table_name") or "GOOGLE_DRIVE_FILES")

        if source_config.get("folder_id") or source_config.get("folder_name"):
            try:
                validation = validate_google_drive_connection({"GoogleDrive": source_config})
                access_token = validation.get("access_token") or access_token
                source_config["folder_id"] = validation.get("folder_id") or source_config.get("folder_id")
                source_config["folder_name"] = validation.get("folder_name") or source_config.get("folder_name")
            except HTTPException as validation_error:
                error_message = _http_error_detail(validation_error)
                log(f"[MIGRATION] Google Drive folder validation failed: {error_message}")
                _mark_selected_drive_tables_failed(
                    connection_id=connection_id,
                    selected_tables=request.selected_tables or [],
                    source_config=source_config,
                    file_mapping_mode=file_mapping_mode,
                    multi_files_table_name=multi_files_table_name,
                    job_name=job_name,
                    migration_session_id=migration_session_id,
                )
                handle_service_error(
                    error=validation_error,
                    context={
                        "job_name": job_name,
                        "log_id": log_id,
                        "cmp_id": cmp_id,
                        "connection_id": connection_id,
                        "user_id": user_id,
                        "operation": "MIGRATION",
                        "table_name": "Migration Setup",
                        "is_terminal_failure": True,
                        "successful_tables": 0,
                        "total_tables": len(request.selected_tables or []),
                        "total_rows": 0,
                        "session": migration_session_id,
                        "migration_session_id": migration_session_id,
                        "service_name": "Google Drive to Snowflake",
                        "connection_name": connection_name,
                        "company_name": company_name,
                        "execution_time": time.time() - start_time,
                    },
                    send_email=True,
                    is_final_attempt=True,
                )
                return {
                    "status": "failed",
                    "message": error_message,
                    "files_processed": 0,
                    "failed_tables": len(request.selected_tables or []),
                    "processed_tables": [],
                }

        folder_id = str(source_config.get("folder_id") or "").strip()
        if not folder_id:
            error_msg = "Google Drive source folder_id or folder_name is required"
            _mark_selected_drive_tables_failed(
                connection_id=connection_id,
                selected_tables=request.selected_tables or [],
                source_config=source_config,
                file_mapping_mode=file_mapping_mode,
                multi_files_table_name=multi_files_table_name,
                job_name=job_name,
                migration_session_id=migration_session_id,
            )
            handle_service_error(
                error=ValueError(error_msg),
                context={
                    "job_name": job_name,
                    "log_id": log_id,
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "operation": "MIGRATION",
                    "table_name": "Migration Setup",
                    "is_terminal_failure": True,
                    "successful_tables": 0,
                    "total_tables": len(request.selected_tables or []),
                    "total_rows": 0,
                    "session": migration_session_id,
                    "migration_session_id": migration_session_id,
                    "service_name": "Google Drive to Snowflake",
                    "connection_name": connection_name,
                    "company_name": company_name,
                    "execution_time": time.time() - start_time,
                },
                send_email=True,
                is_final_attempt=True,
            )
            return {
                "status": "failed",
                "message": error_msg,
                "files_processed": 0,
                "failed_tables": len(request.selected_tables or []),
                "processed_tables": [],
            }

        include_subfolders = _as_bool(source_config.get("include_subfolders"))
        drive_files = _walk_drive_files(access_token, folder_id, include_subfolders)
        log(f"[DRIVE][LIST] Found {len(drive_files)} file(s) in source folder")

        single_file_map = _normalize_single_file_map(source_config)
        selected_files = _resolve_selected_files(
            request.selected_tables or [],
            drive_files,
            single_file_map,
            file_mapping_mode,
            multi_files_table_name,
            source_config.get("multi_files_prefix") or "",
        )
        if not selected_files:
            error_msg = "No Google Drive files selected for migration"
            _mark_selected_drive_tables_failed(
                connection_id=connection_id,
                selected_tables=request.selected_tables or [],
                source_config=source_config,
                file_mapping_mode=file_mapping_mode,
                multi_files_table_name=multi_files_table_name,
                job_name=job_name,
                migration_session_id=migration_session_id,
            )
            error = FileNotFoundError(error_msg)
            handle_service_error(
                error=error,
                context={
                    "job_name": job_name,
                    "log_id": log_id,
                    "cmp_id": cmp_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "operation": "MIGRATION",
                    "table_name": "Migration Setup",
                    "is_terminal_failure": True,
                    "successful_tables": 0,
                    "total_tables": len(request.selected_tables or []),
                    "total_rows": 0,
                    "session": migration_session_id,
                    "migration_session_id": migration_session_id,
                    "service_name": "Google Drive to Snowflake",
                    "connection_name": connection_name,
                    "company_name": company_name,
                    "execution_time": time.time() - start_time,
                },
                send_email=True,
                is_final_attempt=True,
            )
            return {
                "status": "failed",
                "message": error_msg,
                "files_processed": 0,
                "failed_tables": len(request.selected_tables or []),
                "processed_tables": [],
            }

        client = GoogleDriveFileClient(
            access_token,
            drive_files,
            export_file_type=source_config.get("file_type") or "csv",
        )

        file_type = _infer_google_drive_migration_file_type(source_config, client, selected_files)
        if file_type not in SUPPORTED_GOOGLE_DRIVE_SNOWFLAKE_FILE_TYPES:
            raise HTTPException(status_code=400, detail=f"Google Drive to Snowflake does not support file_type '{file_type}' yet")
        client.export_file_type = file_type

        if file_mapping_mode == "multi_files_single_table":
            selected_before_type_filter = len(selected_files)
            selected_files = [
                file_key
                for file_key in selected_files
                if _drive_item_matches_file_type(client.resolve(file_key), file_key, file_type)
            ]
            skipped_by_type = selected_before_type_filter - len(selected_files)
            if skipped_by_type > 0:
                log(
                    f"[DRIVE][MIGRATION] Ignoring {skipped_by_type} non-{file_type.upper()} file(s) "
                    f"for multi-file single-table migration"
                )
            if not selected_files:
                error_msg = f"No Google Drive {file_type.upper()} files selected for migration"
                _mark_selected_drive_tables_failed(
                    connection_id=connection_id,
                    selected_tables=request.selected_tables or [],
                    source_config=source_config,
                    file_mapping_mode=file_mapping_mode,
                    multi_files_table_name=multi_files_table_name,
                    job_name=job_name,
                    migration_session_id=migration_session_id,
                )
                handle_service_error(
                    error=FileNotFoundError(error_msg),
                    context={
                        "job_name": job_name,
                        "log_id": log_id,
                        "cmp_id": cmp_id,
                        "connection_id": connection_id,
                        "user_id": user_id,
                        "operation": "MIGRATION",
                        "table_name": "Migration Setup",
                        "is_terminal_failure": True,
                        "successful_tables": 0,
                        "total_tables": 1,
                        "total_rows": 0,
                        "session": migration_session_id,
                        "migration_session_id": migration_session_id,
                        "service_name": "Google Drive to Snowflake",
                        "connection_name": connection_name,
                        "company_name": company_name,
                        "execution_time": time.time() - start_time,
                    },
                    send_email=True,
                    is_final_attempt=True,
                )
                return {
                    "status": "failed",
                    "message": error_msg,
                    "files_processed": 0,
                    "failed_tables": 1,
                    "processed_tables": [],
                }

        delimiter = source_config.get("delimiter")
        quotechar = source_config.get("quotechar")
        skipheaderlines = source_config.get("skipheaderlines")
        json_mode = source_config.get("json_mode")
        xml_mode = source_config.get("xml_mode")
        escape_char = source_config.get("escape_char")
        null_sequence = source_config.get("null_sequence")
        skip_footer_lines = source_config.get("skip_footer_lines")
        line_separator = source_config.get("line_separator")

        if file_type == "tsv":
            delimiter = source_config.get("tsv_delimiter") or delimiter
            quotechar = source_config.get("tsv_quotechar") or quotechar
            skipheaderlines = (
                source_config.get("tsv_skipheaderlines")
                if source_config.get("tsv_skipheaderlines") is not None
                else skipheaderlines
            )
            escape_char = source_config.get("tsv_escape_char") or escape_char
            null_sequence = source_config.get("tsv_null_sequence") or null_sequence
            skip_footer_lines = (
                source_config.get("tsv_skip_footer_lines")
                if source_config.get("tsv_skip_footer_lines") is not None
                else skip_footer_lines
            )

        load_method = source_config.get("load_method", "upsert_file_row")
        custom_pk_mapping = source_config.get("custom_primary_key") or {}
        if isinstance(custom_pk_mapping, str):
            try:
                custom_pk_mapping = json.loads(custom_pk_mapping)
            except Exception:
                custom_pk_mapping = {}
        if not isinstance(custom_pk_mapping, dict):
            custom_pk_mapping = {}

        sf_account = dest_config.get("account")
        sf_user = dest_config.get("user") or dest_config.get("username")
        sf_password = dest_config.get("password")
        sf_warehouse = normalize_and_log_snowflake_identifier(dest_config.get("warehouse"), "warehouse")
        sf_database = normalize_and_log_snowflake_identifier(dest_config.get("database"), "database")
        sf_schema = normalize_and_log_snowflake_identifier(dest_config.get("schema"), "schema")
        sf_role = normalize_and_log_snowflake_identifier(dest_config.get("role"), "role") or "ACCOUNTADMIN"

        gcs_service_account_json = dest_config.get("gcs_service_account_json") or source_config.get("gcs_service_account_json")
        gcp_project_id = dest_config.get("gcp_project_id") or source_config.get("gcp_project_id")
        gcs_bucket_name = resolve_gcs_bucket_name(dest_config, source_config)
        sf_storage_integration = resolve_snowflake_storage_integration(dest_config, source_config)

        is_manual_migration = _is_manual_google_drive_migration(job_name)
        safety_cutoff = None if is_manual_migration else _build_safety_cutoff(request.safety_interval)
        if is_manual_migration and request.safety_interval:
            log("[MIGRATION] Manual Google Drive migration - safety interval bypassed")

        table_names = []
        for file_key in selected_files:
            table_name = multi_files_table_name if file_mapping_mode == "multi_files_single_table" else _table_for_file(file_key, client, single_file_map)
            if table_name:
                table_names.append(table_name)
        last_checked_data = get_last_checked_dates_and_fields(connection_id, table_names) if table_names else {}

        conn = None
        processed = 0
        failed = 0
        total_rows = 0
        total_source_rows_read = 0
        total_inserts = 0
        total_updates = 0
        total_deletes = 0
        processed_tables = set()
        no_op_failed_tables = set()
        skipped_tables_delta = set()
        skipped_tables_safety = set()
        processed_file_keys: List[str] = []
        failed_file_messages: List[str] = []
        warning_tables = set()
        missing_files_single_mode = set()
        multi_table_start_time = None
        multi_table_columns_count = 0
        staging_tables = []
        table_exists_cache: Dict[str, bool] = {}

        try:
            log("[MIGRATION] Connecting to GCS...")
            try:
                gcs = gcs_client(gcs_service_account_json, gcp_project_id)
                log("[MIGRATION] GCS connected")
            except Exception as e:
                log(f"[MIGRATION] GCS connection with config failed: {e}")
                log("[MIGRATION] Attempting fallback to default GCS credentials...")
                try:
                    from google.auth import compute_engine

                    credentials = compute_engine.Credentials()
                    gcs = storage.Client(credentials=credentials, project=gcp_project_id or GCP_PROJECT_ID)
                    log("[MIGRATION] GCS connected using default credentials")
                except Exception as fallback_error:
                    try:
                        gcs = storage.Client(project=gcp_project_id or GCP_PROJECT_ID)
                        log("[MIGRATION] GCS connected using environment/default credentials")
                    except Exception as final_error:
                        raise Exception(
                            f"GCS connection failed: {e}. "
                            f"Fallback attempts also failed: {fallback_error}, {final_error}"
                        ) from final_error

            conn = sf_connect(
                sf_account=sf_account,
                sf_user=sf_user,
                sf_password=sf_password,
                sf_warehouse=sf_warehouse,
                sf_database=sf_database,
                sf_schema=sf_schema,
                sf_role=sf_role,
                private_key=dest_config.get("private_key"),
                passphrase=dest_config.get("passphrase"),
                authentication_type=dest_config.get("authentication_type", "password"),
            )

            try:
                gcs_allowed_url_prefix = f"gcs://{gcs_bucket_name}/"
                existed = ensure_gcs_storage_integration(conn, sf_storage_integration, gcs_allowed_url_prefix)
                try:
                    service_account = get_gcp_service_account(conn, sf_storage_integration)
                    ensure_bucket_iam_for_integration(gcs, gcs_bucket_name, service_account)
                    log("[MIGRATION] Storage integration IAM configured")
                except Exception as iam_error:
                    log(f"[MIGRATION] Storage integration IAM check: {iam_error if not existed else 'continuing...'}")
                assert_allowed_prefix_matches(conn, gcs_allowed_url_prefix, sf_storage_integration)
            except Exception as preflight_error:
                raise Exception(f"Storage integration validation failed: {preflight_error}") from preflight_error

            shared_staging_table = None
            if file_mapping_mode == "multi_files_single_table":
                try:
                    update_table_status_snowflake(connection_id, multi_files_table_name, "in_progress", job_name, migration_session_id)
                except Exception:
                    pass

            for file_key in selected_files:
                item = client.resolve(file_key)
                display_key = str(item.get("name") if item else file_key)
                table = multi_files_table_name if file_mapping_mode == "multi_files_single_table" else _table_for_file(file_key, client, single_file_map)
                table = sanitize_identifier(table)
                file_start_time = datetime.now(timezone.utc)
                gcs_urls: List[str] = []
                current_staging_table = shared_staging_table

                try:
                    stat = client.stat(file_key)
                    file_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)

                    apply_delta_check = _should_apply_delta_check(load_method, is_manual_migration)
                    if not drop_and_reload and apply_delta_check:
                        table_state = last_checked_data.get(table) or last_checked_data.get(table.upper()) or {}
                        last_synced = _last_synced_from_table_state(table_state)
                        last_synced_dt = parse_datetime_from_db(last_synced) if last_synced else None

                        # Keep Google Drive in sync with ADLS/S3: a stale PostgreSQL
                        # last_synced value must never skip a missing Snowflake table.
                        if last_synced_dt:
                            table_key = table.upper()
                            if table_key not in table_exists_cache:
                                table_exists_cache[table_key] = _snowflake_table_exists(
                                    conn, sf_database, sf_schema, table_key
                                )
                            if not table_exists_cache[table_key]:
                                log(
                                    f"[DRIVE][MIGRATION] Destination table '{table}' is missing in Snowflake; "
                                    "forcing full load and clearing last_synced."
                                )
                                last_synced_dt = None

                        if safety_cutoff and file_modified > safety_cutoff:
                            log(f"[DRIVE][SKIP] Skipping '{display_key}' due to safety interval")
                            if file_mapping_mode != "multi_files_single_table":
                                update_table_status_snowflake(connection_id, table, "completed", job_name, migration_session_id, sync_end_time=None)
                            skipped_tables_safety.add(table)
                            if file_mapping_mode != "multi_files_single_table":
                                try:
                                    _log_skipped_transfer(
                                        cmp_id=cmp_id,
                                        connection_id=connection_id,
                                        user_id=user_id,
                                        table_name=table,
                                        skipped_at=datetime.now(timezone.utc),
                                        session=request.session,
                                        job_name=job_name,
                                        message=(
                                            f"Skipped: file '{display_key}' is within the safety interval "
                                            f"(modified at {file_modified.isoformat()})."
                                        ),
                                        migration_session_id=migration_session_id,
                                    )
                                except Exception as transfer_log_error:
                                    log(f"[DRIVE][SKIP] Failed to log safety-skipped table '{table}': {transfer_log_error}")
                            processed_tables.add(table)
                            continue

                        if _should_skip_unchanged_file(load_method, last_synced_dt, file_modified):
                            log(f"[DRIVE][SKIP] Skipping '{display_key}' because it is not newer than last sync")
                            skip_time_utc = datetime.now(timezone.utc)
                            if file_mapping_mode != "multi_files_single_table":
                                update_table_status_snowflake(connection_id, table, "completed", job_name, migration_session_id, skip_time_utc)
                            if file_mapping_mode != "multi_files_single_table":
                                try:
                                    _log_skipped_transfer(
                                        cmp_id=cmp_id,
                                        connection_id=connection_id,
                                        user_id=user_id,
                                        table_name=table,
                                        skipped_at=skip_time_utc,
                                        session=request.session,
                                        job_name=job_name,
                                        message=(
                                            f"Skipped: file '{display_key}' unchanged since last sync "
                                            f"({last_synced_dt.isoformat() if last_synced_dt else 'unknown'})."
                                        ),
                                        migration_session_id=migration_session_id,
                                    )
                                except Exception as transfer_log_error:
                                    log(f"[DRIVE][SKIP] Failed to log skipped table '{table}': {transfer_log_error}")
                            skipped_tables_delta.add(table)
                            processed_tables.add(table)
                            continue
                    elif is_manual_migration and not apply_delta_check:
                        log(
                            f"[DRIVE][MIGRATION] Manual migration - delta skip bypassed for "
                            f"'{display_key}' (file_modified={file_modified.isoformat()})"
                        )

                    prepared_file_client = prepare_drive_file_for_read(
                        client,
                        file_key,
                        file_type=file_type,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        escape_char=escape_char,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                    )

                    if file_mapping_mode != "multi_files_single_table":
                        update_table_status_snowflake(connection_id, table, "in_progress", job_name, migration_session_id)

                    chunks = read_sftp_file_in_chunks(
                        prepared_file_client,
                        file_key,
                        file_type=file_type,
                        chunk_size=chunk_count,
                        delimiter=delimiter,
                        quotechar=quotechar,
                        skipheaderlines=skipheaderlines,
                        json_mode=json_mode,
                        xml_mode=xml_mode,
                        escape_char=escape_char,
                        null_sequence=null_sequence,
                        skip_footer_lines=skip_footer_lines,
                        line_separator=line_separator,
                    )

                    file_rows = 0
                    for chunk_df, chunk_num, is_last_chunk, _last_modified, total_rows_val in chunks:
                        if chunk_df.empty:
                            if is_last_chunk:
                                break
                            continue

                        if chunk_num == 1 and not current_staging_table:
                            current_staging_table = ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                custom_pk_mapping.get(table.upper(), []),
                                sf_database=sf_database,
                                sf_schema=sf_schema,
                                staging_table_name=staging_name(table) if file_mapping_mode == "multi_files_single_table" else None,
                            )
                            staging_tables.append(current_staging_table)
                            if file_mapping_mode == "multi_files_single_table":
                                shared_staging_table = current_staging_table
                        elif current_staging_table:
                            ensure_tables(
                                conn,
                                table,
                                chunk_df,
                                load_method,
                                custom_pk_mapping.get(table.upper(), []),
                                sf_database=sf_database,
                                sf_schema=sf_schema,
                                staging_table_name=current_staging_table,
                            )

                        chunk_df = align_chunk_to_table_column_order(
                            conn,
                            chunk_df,
                            current_staging_table,
                            sf_database=sf_database,
                            sf_schema=sf_schema,
                        )
                        chunk_df = reorder_chunk_columns_for_snowflake(
                            chunk_df,
                            load_method,
                            custom_pk_mapping.get(table.upper(), []),
                        )
                        if file_mapping_mode == "multi_files_single_table":
                            if multi_table_start_time is None:
                                multi_table_start_time = file_start_time
                            multi_table_columns_count = max(multi_table_columns_count, len(chunk_df.columns))
                        gcs_path = upload_chunk_to_gcs(
                            gcs,
                            chunk_df,
                            table,
                            chunk_num,
                            gcs_bucket_name=gcs_bucket_name,
                            delimiter=",",
                            quotechar='"',
                            file_type="csv",
                        )
                        gcs_urls.append(f"gcs://{gcs_bucket_name}/{gcs_path}")
                        file_rows = int(total_rows_val or file_rows)
                        SimpleProgressTracker.accumulate_staging_progress_batch(
                            connection_id=connection_id,
                            migration_session_id=migration_session_id,
                            job_name=job_name,
                            table_name=table,
                            batch_rows=len(chunk_df),
                        )

                    if not gcs_urls:
                        log(
                            f"[DRIVE][EMPTY] No data rows in file '{display_key}' "
                            f"for table '{table}'"
                        )
                        if file_mapping_mode == "multi_files_single_table":
                            continue
                        _complete_google_drive_table_zero_rows(
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            user_id=user_id,
                            table_name=table,
                            session=request.session,
                            job_name=job_name,
                            migration_session_id=migration_session_id,
                            file_start_time=file_start_time,
                        )
                        processed_tables.add(table)
                        continue

                    copy_into_staging_from_gcs_batch(
                        conn,
                        gcs_urls,
                        current_staging_table,
                        sf_database,
                        sf_schema,
                        sf_storage_integration,
                        gcs_bucket_name,
                        delimiter=",",
                        quotechar='"',
                        skipheaderlines=1,
                        file_type="csv",
                        connection_id=connection_id,
                        migration_session_id=str(migration_session_id),
                        job_name=job_name,
                        table_name=table,
                    )

                    if file_mapping_mode == "multi_files_single_table":
                        processed += 1
                        total_source_rows_read += file_rows
                        processed_tables.add(table)
                        skipped_tables_delta.discard(table)
                        skipped_tables_safety.discard(table)
                        processed_file_keys.append(file_key)
                        continue

                    merge_result = merge_staging_into_final(
                        conn,
                        table,
                        current_staging_table,
                        load_method,
                        custom_pk_mapping.get(table.upper(), []),
                        sf_database,
                        sf_schema,
                        source_file_key=file_key,
                        delete_missing_rows=(not drop_and_reload) and load_method == "upsert_custom_key",
                    )
                    inserts = merge_result.get("inserted", 0)
                    updates = merge_result.get("updated", 0)
                    deletes = merge_result.get("deleted", 0)
                    total_inserts += inserts
                    total_updates += updates
                    total_deletes += deletes
                    total_rows += inserts + updates + deletes
                    total_source_rows_read += file_rows
                    processed += 1
                    processed_tables.add(table)
                    skipped_tables_delta.discard(table)
                    skipped_tables_safety.discard(table)

                    effective_rows = inserts + updates + deletes
                    if file_rows > 0 and effective_rows == 0:
                        _log_no_op_merge(
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            user_id=user_id,
                            table_name=table,
                            source_rows=file_rows,
                            session=request.session,
                            job_name=job_name,
                            migration_session_id=migration_session_id,
                        )
                        if _should_treat_no_op_merge_as_failure(load_method):
                            no_op_failed_tables.add(table)
                            failed += 1
                            failure_detail = _record_drive_no_op_merge_failure(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                user_id=user_id,
                                table_name=table,
                                source_rows=file_rows,
                                session=request.session,
                                job_name=job_name,
                                migration_session_id=migration_session_id,
                                log_id=log_id,
                                connection_name=connection_name,
                                company_name=company_name,
                            )
                            failed_file_messages.append(f"{display_key}: {failure_detail}")
                            continue

                    if all([connection_id, migration_session_id, job_name, table]):
                        try:
                            SimpleProgressTracker.set_staging_count(
                                connection_id=connection_id,
                                migration_session_id=str(migration_session_id),
                                job_name=job_name,
                                table_name=table,
                                count_value=effective_rows,
                            )
                            log(
                                f"[MIGRATION] Updated staging_records_count with effective rows for table '{table}': "
                                f"{effective_rows:,} (Inserted+Updated+Deleted)"
                            )
                        except Exception as progress_err:
                            log(
                                f"[MIGRATION] Failed to set effective staging_records_count for table '{table}': {progress_err}"
                            )

                    update_table_status_snowflake(
                        connection_id,
                        table,
                        "completed",
                        job_name,
                        migration_session_id,
                        datetime.now(timezone.utc),
                    )
                    data_transfer_logs(
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        usr_id=user_id,
                        table_name=table,
                        start_time=file_start_time,
                        end_time=datetime.now(timezone.utc),
                        columns_data=len(chunk_df.columns) if "chunk_df" in locals() else 0,
                        session=request.session,
                        total_rows=effective_rows,
                        inserts=inserts,
                        updates=updates,
                        deletes=deletes,
                        job_name=job_name,
                    )
                except Exception as file_error:
                    failed += 1
                    failure_detail = _http_error_detail(file_error)
                    failed_file_messages.append(f"{display_key}: {failure_detail}")
                    log(f"[DRIVE][ERROR] Failed processing '{display_key}' as table '{table}': {file_error}")
                    if file_mapping_mode == "multi_files_single_table":
                        continue
                    if isinstance(file_error, FileNotFoundError):
                        missing_files_single_mode.add(str(file_key).strip())
                    _persist_drive_table_failure(
                        error=file_error,
                        connection_id=connection_id,
                        table_name=table,
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        log_id=log_id,
                        cmp_id=cmp_id,
                        user_id=user_id,
                        connection_name=connection_name,
                        company_name=company_name,
                    )

            if file_mapping_mode == "multi_files_single_table" and failed and not processed:
                multi_file_failure_detail = (
                    failed_file_messages[0]
                    if failed_file_messages
                    else "No Google Drive files could be parsed"
                )
                _persist_drive_table_failure(
                    error=ValueError(multi_file_failure_detail),
                    connection_id=connection_id,
                    table_name=multi_files_table_name,
                    job_name=job_name,
                    migration_session_id=migration_session_id,
                    log_id=log_id,
                    cmp_id=cmp_id,
                    user_id=user_id,
                    connection_name=connection_name,
                    company_name=company_name,
                )
            elif (
                file_mapping_mode == "multi_files_single_table"
                and multi_files_table_name
                and processed == 0
                and failed == 0
                and multi_files_table_name not in processed_tables
            ):
                log(
                    f"[DRIVE][EMPTY] No data rows across files for table "
                    f"'{multi_files_table_name}'; completing with 0 rows"
                )
                _complete_google_drive_table_zero_rows(
                    cmp_id=cmp_id,
                    connection_id=connection_id,
                    user_id=user_id,
                    table_name=multi_files_table_name,
                    session=request.session,
                    job_name=job_name,
                    migration_session_id=migration_session_id,
                )
                processed_tables.add(multi_files_table_name)

            if file_mapping_mode == "multi_files_single_table" and shared_staging_table and processed > 0:
                merge_result = merge_staging_into_final(
                    conn,
                    multi_files_table_name,
                    shared_staging_table,
                    load_method,
                    custom_pk_mapping.get(multi_files_table_name.upper(), []),
                    sf_database,
                    sf_schema,
                    source_file_keys=processed_file_keys,
                    delete_missing_rows=(not drop_and_reload) and load_method == "upsert_custom_key",
                )
                inserts = merge_result.get("inserted", 0)
                updates = merge_result.get("updated", 0)
                deletes = merge_result.get("deleted", 0)
                total_inserts += inserts
                total_updates += updates
                total_deletes += deletes
                effective_rows = inserts + updates + deletes
                total_rows += effective_rows
                final_merge_no_op_failed = False
                if total_source_rows_read > 0 and effective_rows == 0:
                    _log_no_op_merge(
                        cmp_id=cmp_id,
                        connection_id=connection_id,
                        user_id=user_id,
                        table_name=multi_files_table_name,
                        source_rows=total_source_rows_read,
                        session=request.session,
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                    )
                    if _should_treat_no_op_merge_as_failure(load_method):
                        no_op_failed_tables.add(multi_files_table_name)
                        failed += 1
                        failure_detail = _record_drive_no_op_merge_failure(
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            user_id=user_id,
                            table_name=multi_files_table_name,
                            source_rows=total_source_rows_read,
                            session=request.session,
                            job_name=job_name,
                            migration_session_id=migration_session_id,
                            log_id=log_id,
                            connection_name=connection_name,
                            company_name=company_name,
                        )
                        failed_file_messages.append(f"{multi_files_table_name}: {failure_detail}")
                        final_merge_no_op_failed = True

                if not final_merge_no_op_failed:
                    if all([connection_id, migration_session_id, job_name, multi_files_table_name]):
                        try:
                            SimpleProgressTracker.set_staging_count(
                                connection_id=connection_id,
                                migration_session_id=str(migration_session_id),
                                job_name=job_name,
                                table_name=multi_files_table_name,
                                count_value=effective_rows,
                            )
                            log(
                                f"[MIGRATION] Updated staging_records_count with effective rows for table "
                                f"'{multi_files_table_name}': {effective_rows:,} (Inserted+Updated+Deleted)"
                            )
                        except Exception as progress_err:
                            log(
                                f"[MIGRATION] Failed to set effective staging_records_count for table "
                                f"'{multi_files_table_name}': {progress_err}"
                            )
                    migration_end_time = datetime.now(timezone.utc)
                    update_table_status_snowflake(
                        connection_id,
                        multi_files_table_name,
                        "completed",
                        job_name,
                        migration_session_id,
                        migration_end_time,
                    )
                    if job_name:
                        try:
                            data_transfer_logs(
                                cmp_id=cmp_id,
                                connection_id=connection_id,
                                usr_id=user_id,
                                table_name=multi_files_table_name,
                                start_time=multi_table_start_time or datetime.now(timezone.utc),
                                end_time=migration_end_time,
                                columns_data=multi_table_columns_count,
                                session=request.session,
                                total_rows=effective_rows,
                                inserts=inserts,
                                updates=updates,
                                deletes=deletes,
                                job_name=job_name,
                            )
                        except Exception as transfer_log_error:
                            log(
                                f"[MIGRATION] Failed to write data_transfer log for Google Drive "
                                f"multi-file table '{multi_files_table_name}': {transfer_log_error}"
                            )

            if file_mapping_mode == "multi_files_single_table" and failed and processed > 0 and not no_op_failed_tables:
                warning_tables.add(multi_files_table_name)
                _report_drive_partial_table_failure(
                    connection_id=connection_id,
                    table_name=multi_files_table_name,
                    failed_file_messages=failed_file_messages,
                    job_name=job_name,
                    migration_session_id=migration_session_id,
                    cmp_id=cmp_id,
                    user_id=user_id,
                    connection_name=connection_name,
                    company_name=company_name,
                )

            failed_table_count = failed
            status_override = "F" if no_op_failed_tables or (failed and not processed) else ("P" if failed else "S")
            if file_mapping_mode == "multi_files_single_table":
                if no_op_failed_tables:
                    failed_table_count = max(failed_table_count, len(no_op_failed_tables))
                elif failed and processed > 0:
                    failed_table_count = 0
                elif failed and not processed:
                    failed_table_count = len(set(table_names)) or 1

            include_operation_counts = load_method not in ("upsert_file_row", "append_file_modified")
            successful_tables = len(processed_tables - no_op_failed_tables)
            log_migration_completion(
                execution_time=time.time() - start_time,
                total_rows=total_rows,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                session=migration_session_id,
                total_tables=len(set(table_names)) or len(selected_files),
                successful_tables=successful_tables,
                failed_tables=failed_table_count,
                job_name=job_name,
                inserts=total_inserts,
                updates=total_updates,
                deletes=total_deletes,
                include_operation_counts=include_operation_counts,
                log_id=log_id,
                status_override=status_override,
                suppress_reason_in_completion=True,
                celery_child_mode=request.celery_child_mode,
            )

            try:
                handle_migration_completion(
                    connection_name=connection_name,
                    job_name=job_name,
                    completion_message=(
                        f"Google Drive migration completed: {processed}/{len(selected_files)} files, "
                        f"{total_source_rows_read:,} source rows, {total_rows:,} changed rows"
                    ),
                )
            except Exception:
                pass

            # Fan-out children report missing files; the finalizer refreshes once.
            if (
                file_mapping_mode == "single_table_per_file"
                and missing_files_single_mode
                and not request.celery_child_mode
            ):
                try:
                    log(
                        "[DRIVE][MIGRATION] Post-migration schema refresh triggered "
                        f"for missing file(s): {sorted(missing_files_single_mode)}"
                    )
                    refresh_job_name = (
                        f"{job_name}_post_missing_file_schema_refresh"
                        if job_name
                        else f"GoogleDrive_post_missing_file_schema_refresh_{migration_session_id}"
                    )
                    try:
                        insert_tool_log(
                            cmp_id=cmp_id,
                            connection_id=connection_id,
                            usr_id=user_id,
                            table_name="Update Schema",
                            status="I",
                            message="Update schema in progress...",
                            session=migration_session_id,
                            job_name=refresh_job_name,
                            migration_record_id=None,
                        )
                    except Exception as start_log_error:
                        log(
                            "[DRIVE][MIGRATION] Could not create post-refresh start log "
                            f"(non-fatal): {start_log_error}"
                        )

                    listing_request = GoogleDriveListingTables(
                        connection_id=connection_id,
                        GoogleDrive=source_config,
                        job_name=refresh_job_name,
                        migration_session_id=migration_session_id,
                        cmp_id=cmp_id,
                        user_id=user_id,
                        log_id=log_id,
                    )
                    refreshed_tables_result = GoogleDriveSnowflakeService.list_tables_with_schema(
                        listing_request
                    )
                    GoogleDriveSnowflakeService.refresh_and_update_google_drive_tables_in_database(
                        connection_id=connection_id,
                        tables_result=refreshed_tables_result,
                        job_name=refresh_job_name,
                        migration_session_id=migration_session_id,
                    )
                    try:
                        from utils.websocket_utils import (
                            trigger_connection_ws_status_update,
                            trigger_update_schema_ws_status_update,
                        )

                        trigger_update_schema_ws_status_update(connection_id)
                        trigger_connection_ws_status_update(connection_id)
                    except Exception as ws_error:
                        log(
                            "[DRIVE][MIGRATION] Post-migration schema refresh WS trigger "
                            f"failed: {ws_error}"
                        )
                    log(
                        "[DRIVE][MIGRATION] Post-migration schema refresh completed "
                        "after missing-file detection"
                    )
                except Exception as post_refresh_error:
                    log(
                        "[DRIVE][MIGRATION] Post-migration schema refresh failed "
                        f"(non-fatal): {post_refresh_error}"
                    )
                    try:
                        from utils.error_utils import update_migration_status_log

                        update_migration_status_log(
                            job_name=refresh_job_name,
                            message=f"Update schema failed: {post_refresh_error}",
                            status="F",
                            session=migration_session_id,
                        )
                        from utils.websocket_utils import trigger_update_schema_ws_status_update

                        trigger_update_schema_ws_status_update(connection_id)
                    except Exception as log_err:
                        log(
                            "[DRIVE][MIGRATION] Could not write post-refresh failure log: "
                            f"{log_err}"
                        )

            return {
                "status": "failed" if no_op_failed_tables or (failed and not processed) else "success",
                "files_processed": processed,
                "total_rows": total_rows,
                "total_inserts": total_inserts,
                "total_updates": total_updates,
                "total_deleted_recids": total_deletes,
                "execution_time": time.time() - start_time,
                "failed_tables": failed,
                "successful_tables_count": successful_tables,
                "failed_tables_count": failed_table_count,
                "successful_tables_list": sorted(processed_tables - no_op_failed_tables),
                "failed_tables_list": [
                    table_name
                    for table_name in set(table_names)
                    if table_name not in set(processed_tables - no_op_failed_tables)
                ],
                "warning_tables_list": sorted(warning_tables),
                "failed_files_list": list(failed_file_messages),
                "processed_tables": list(processed_tables),
                "skipped_tables_delta": list(skipped_tables_delta),
                "skipped_tables_safety": list(skipped_tables_safety),
                "single_mode_missing_files_detected": bool(missing_files_single_mode),
                "single_mode_missing_files": sorted(missing_files_single_mode),
            }
        finally:
            if conn:
                try:
                    for staging_table in set(staging_tables):
                        if staging_table:
                            with conn.cursor() as cur:
                                cur.execute(f"DROP TABLE IF EXISTS {Q(sf_database)}.{Q(sf_schema)}.{Q(staging_table)}")
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
