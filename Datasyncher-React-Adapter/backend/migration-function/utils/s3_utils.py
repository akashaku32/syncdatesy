import os
import re
import fnmatch
import calendar
import contextvars
from datetime import date
from typing import List, Optional, Tuple, Dict, Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, ParamValidationError

from services.source_file_helpers.file_writing_utils import strip_export_filename_suffix

log_prefix = contextvars.ContextVar("log_prefix", default="S3")


def log(msg: str) -> None:
    """Log S3 utility messages with the active connector context prefix."""
    print(f"[{log_prefix.get()}] {msg}")


def normalized_prefix(basefolder: str) -> str:
    """Normalize S3 prefix (remove leading/trailing slashes)."""
    return basefolder.strip("/") if basefolder else ""


def sanitize_identifier(name: str) -> str:
    """Sanitize identifier for Snowflake (uppercase, replace invalid chars)."""
    return re.sub(r"[^A-Z0-9_]", "_", name.upper())


def table_from_file_key(key: str) -> str:
    """Derive table name from S3 file key, ignoring export date/timestamp suffixes."""
    basename = key.split("/")[-1]
    name = strip_export_filename_suffix(os.path.splitext(basename)[0])
    return sanitize_identifier(name)


def extract_date_from_filename(filename: str) -> Tuple[Optional[date], float]:
    """Extract date from filename, return (date, score)."""
    try:
        m = re.search(
            r"(?<![0-9])(20\d{2})_(0[1-9]|1[0-2])_(0[1-9]|[12]\d|3[01])(?![0-9])",
            filename,
        )
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(year, month, day), 1.0

        m = re.search(
            r"(?<![0-9])(0[1-9]|[12]\d|3[01])_(0[1-9]|1[0-2])_(20\d{2})(?![0-9])",
            filename,
        )
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(year, month, day), 0.95

        m = re.search(
            r"(?<![0-9])(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])T(\d{6})Z(?![0-9])",
            filename,
            flags=re.IGNORECASE,
        )
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(year, month, day), 0.9

        m = re.search(r"(20\d{2})[-_ ]?(0[1-9]|1[0-2])", filename)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            return date(year, month, 1), 0.8
        m = re.search(r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[-_ ]?(20\d{2})", filename)
        if m:
            month_str, year = m.group(1).lower(), int(m.group(2))
            month_map = {m.lower(): i + 1 for i, m in enumerate(calendar.month_abbr[1:])}
            return date(year, month_map[month_str[:3]], 1), 0.7
        return None, 0.0
    except Exception:
        return None, 0.0


def suggest_common_table_name(keys: List[str]) -> str:
    """Suggest common table name from file keys."""
    if not keys:
        return "UNIFIED_TABLE"
    basenames = [
        strip_export_filename_suffix(os.path.splitext(k.split("/")[-1])[0])
        for k in keys
    ]
    common_prefix = os.path.commonprefix(basenames)
    if common_prefix:
        return sanitize_identifier(common_prefix)
    return sanitize_identifier(basenames[0])


def validate_s3_bucket_name(bucket_name: str) -> Optional[str]:
    """
    Validate S3 bucket name format for user-friendly pre-checks.
    Returns an error message if invalid, otherwise None.
    """
    value = (bucket_name or "").strip()
    if not value:
        return "Enter valid bucket name."

    # Common user mistake: pasting folder path into bucket field.
    if "/" in value:
        return "Enter valid bucket name (bucket only, no '/')."

    # Conservative bucket format check (DNS-compliant bucket names).
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value):
        return "Enter valid bucket name."
    if ".." in value or ".-" in value or "-." in value:
        return "Enter valid bucket name."
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", value):
        return "Enter valid bucket name."
    return None


def detect_s3_bucket_region(
    bucket_name: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    fallback_region: str = "us-east-1",
) -> str:
    """Auto-detect S3 bucket region using AWS APIs."""
    log(f"[S3][REGION] Auto-detecting region for bucket: {bucket_name}")

    auth_or_bucket_error_codes = {
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidClientTokenId",
        "AccessDenied",
        "Forbidden",
        "AllAccessDisabled",
        "NoSuchBucket",
        "NotFound",
        "403",
        "404",
    }

    try:
        s3_global = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}, signature_version="s3v4"),
        )

        try:
            location_response = s3_global.get_bucket_location(Bucket=bucket_name)
            region = location_response.get("LocationConstraint") or fallback_region
            log(f"[S3][REGION] Auto-detected region: {region}")
            return region if region else fallback_region
        except ClientError as location_error:
            location_code = location_error.response.get("Error", {}).get("Code", "Unknown")
            if location_code in auth_or_bucket_error_codes:
                raise

            try:
                response = s3_global.head_bucket(Bucket=bucket_name)
                region = response.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("x-amz-bucket-region")
                if region:
                    log(f"[S3][REGION] Auto-detected region via HEAD: {region}")
                    return region
                return fallback_region
            except ClientError as head_error:
                head_code = head_error.response.get("Error", {}).get("Code", "Unknown")
                if head_code in auth_or_bucket_error_codes:
                    raise
                return fallback_region

    except ClientError:
        # Let caller map AWS error code to a user-friendly message.
        raise
    except ParamValidationError:
        # Invalid bucket names/params should be surfaced to caller.
        raise
    except Exception as e:
        log(f"[S3][REGION] Could not detect region: {e}, using fallback: {fallback_region}")
        return fallback_region
def s3_client(
    aws_access_key_id: str = None,
    aws_secret_access_key: str = None,
    aws_region: str = None,
    s3_bucket: str = None,
    auto_detect_region: bool = True,
):
    """Create S3 client with optional auto region detection."""
    if auto_detect_region and not aws_region and s3_bucket:
        aws_region = detect_s3_bucket_region(
            s3_bucket, aws_access_key_id, aws_secret_access_key
        )
    return boto3.client(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=aws_region or "us-east-1",
        config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"}),
    )


def list_files_recursive(s3, bucket: str, prefix: str) -> List[str]:
    """Recursively list all files in S3 bucket/prefix."""
    keys: List[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def list_files_non_recursive(s3, bucket: str, prefix: str) -> List[str]:
    """List files only at the immediate level of prefix/root (exclude subfolders)."""
    keys: List[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # Defensive guard: S3 can return the folder marker itself.
            if prefix and key == prefix:
                continue
            keys.append(key)
    return keys


def filter_by_type(keys: List[str], ftype: str) -> List[str]:
    """Filter files by extension."""
    ext_map = {
        "csv": [".csv"],
        "tsv": [".tsv", ".txt"],
        "json": [".json", ".jsonl"],
        "parquet": [".parquet"],
        "avro": [".avro"],
        "xml": [".xml"],
    }
    extensions = ext_map.get(ftype.lower(), [])
    return [k for k in keys if any(k.lower().endswith(ext) for ext in extensions)]


def filter_by_pattern(keys: List[str], pattern: str) -> List[str]:
    """
    Filter files by regex or glob pattern.

    Args:
        keys: List of S3 file keys
        pattern: Regex/glob pattern to match against file keys (full path or filename)

    Returns:
        List of file keys that match the pattern
    """
    if not pattern:
        return keys

    # Auto-detect glob-like input when there is no explicit regex syntax.
    glob_markers = ("*", "?", "[")
    regex_markers = ("^", "$", "\\d", "\\w", "\\s", "\\b", "(", ")", "{", "}", "|", "+")
    looks_glob = any(marker in pattern for marker in glob_markers)
    looks_regex = any(marker in pattern for marker in regex_markers) or ".*" in pattern or ".+" in pattern

    if looks_glob and not looks_regex:
        pattern_lc = pattern.lower()
        match_full_path = "/" in pattern_lc
        matching_keys = []
        for key in keys:
            key_lc = key.lower()
            file_name_lc = os.path.basename(key_lc)
            matched = (
                fnmatch.fnmatch(key_lc, pattern_lc)
                if match_full_path
                else (fnmatch.fnmatch(file_name_lc, pattern_lc) or fnmatch.fnmatch(key_lc, pattern_lc))
            )
            if matched:
                matching_keys.append(key)
        scope = "full path" if match_full_path else "file name"
        log(f"[FILTER_PATTERN] Treated as glob '{pattern}' ({scope}) matched {len(matching_keys)}/{len(keys)} files")
        return matching_keys

    try:
        regex = re.compile(pattern, re.IGNORECASE)
        match_full_path = "/" in pattern
        matching_keys = []
        for key in keys:
            file_name = os.path.basename(key)
            matched = regex.search(key) if match_full_path else (regex.search(file_name) or regex.search(key))
            if matched:
                matching_keys.append(key)
        scope = "full path" if match_full_path else "file name"
        log(f"[FILTER_PATTERN] Treated as regex '{pattern}' ({scope}) matched {len(matching_keys)}/{len(keys)} files")
        return matching_keys
    except re.error as e:
        # Fallback for simple glob patterns that are invalid regex (e.g. '*.csv').
        if looks_glob:
            pattern_lc = pattern.lower()
            match_full_path = "/" in pattern_lc
            matching_keys = []
            for key in keys:
                key_lc = key.lower()
                file_name_lc = os.path.basename(key_lc)
                matched = (
                    fnmatch.fnmatch(key_lc, pattern_lc)
                    if match_full_path
                    else (fnmatch.fnmatch(file_name_lc, pattern_lc) or fnmatch.fnmatch(key_lc, pattern_lc))
                )
                if matched:
                    matching_keys.append(key)
            scope = "full path" if match_full_path else "file name"
            log(
                f"[FILTER_PATTERN] Regex parse failed, fell back to glob '{pattern}' "
                f"({scope}) matched {len(matching_keys)}/{len(keys)} files"
            )
            return matching_keys

        log(f"[FILTER_PATTERN] Invalid pattern '{pattern}': {e}")
        return []


__all__ = [
    "log",
    "normalized_prefix",
    "sanitize_identifier",
    "table_from_file_key",
    "extract_date_from_filename",
    "suggest_common_table_name",
    "validate_s3_bucket_name",
    "detect_s3_bucket_region",
    "s3_client",
    "list_files_recursive",
    "list_files_non_recursive",
    "filter_by_type",
    "filter_by_pattern",
]





