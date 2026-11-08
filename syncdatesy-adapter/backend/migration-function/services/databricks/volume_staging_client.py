"""
Unity Catalog Managed Volume staging for Databricks destinations.

Uploads chunk Parquet files via the Databricks Files REST API
(`PUT /api/2.0/fs/files/Volumes/...`) using the same PAT/OAuth token as the
SQL warehouse connection — no separate cloud credential required.

Works on AWS, Azure, and GCP Databricks workspaces alike.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from utils.retry_utils import RetryableHTTPError, retry_with_exponential_backoff

logger = logging.getLogger(__name__)

# Single-PUT Files API limit is 5 GiB; stay under with a safety margin.
DEFAULT_MULTIPART_THRESHOLD_BYTES = int(
    os.getenv("DATABRICKS_VOLUME_MULTIPART_THRESHOLD_BYTES", str(4 * 1024**3))
)
DEFAULT_UPLOAD_TIMEOUT_SECONDS = int(
    os.getenv("DATABRICKS_VOLUME_UPLOAD_TIMEOUT_SECONDS", "300")
)
DEFAULT_MAX_RETRIES = int(os.getenv("DATABRICKS_VOLUME_UPLOAD_MAX_RETRIES", "5"))


def _encode_volume_fs_path(abs_path: str) -> str:
    """
    Percent-encode each path segment for Files API URLs.

    Dynamics chunk names include ``f&o``; an unencoded ``&`` before
    ``?overwrite=true`` would be parsed as a query separator.
    """
    raw = str(abs_path or "")
    if not raw.startswith("/"):
        raw = "/" + raw
    return "/".join(quote(segment, safe="") for segment in raw.split("/"))


class DatabricksVolumeStagingError(Exception):
    """Base error for managed-volume staging failures."""

    failure_mode: str = "volume_staging_error"

    def __init__(self, message: str, *, failure_mode: Optional[str] = None):
        if failure_mode:
            self.failure_mode = failure_mode
        super().__init__(message)


class VolumeNotFoundError(DatabricksVolumeStagingError):
    failure_mode = "volume_not_found"


class VolumePermissionError(DatabricksVolumeStagingError):
    failure_mode = "volume_insufficient_privileges"


class VolumeUploadTimeoutError(DatabricksVolumeStagingError):
    failure_mode = "volume_upload_timeout"


class VolumeCopyIntoError(DatabricksVolumeStagingError):
    failure_mode = "volume_copy_into_failure"


def volume_chunk_blob_name(table_name: str, connection_id: int, blob_name: str | None = None) -> str:
    """
    Object name for one staged chunk under the UC managed volume.

    Uses a per-call UUID so concurrent workers / retries for the same
    ``table_name`` + ``connection_id`` never overwrite or delete each other's
    file (fixed ``temp_data_dynamics_f&o_{table}_{conn}`` names caused COPY
    ``does not exist`` races after the other worker's finally-cleanup).

    Ampersand is avoided in volume object names (``fo`` not ``f&o``) so Files API
    encoding and SQL COPY paths stay simple; GCS Dynamics paths are unchanged.
    """
    if blob_name:
        # Allow callers (e.g. Salesforce) to override; force .parquet for volume path.
        if not str(blob_name).lower().endswith(".parquet"):
            base = str(blob_name).rsplit(".", 1)[0]
            return f"{base}.parquet"
        return str(blob_name)
    import uuid

    safe_table = str(table_name or "table").replace("/", "_").replace("\\", "_")
    uniq = uuid.uuid4().hex[:12]
    return f"temp_data_dynamics_fo_{safe_table}_{connection_id}_{uniq}.parquet"


def dataframe_for_spark_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize dtypes so COPY INTO FILEFORMAT=PARQUET succeeds on Databricks/Spark.

    Pandas/pyarrow default datetime64[ns] is written as INT64 TIMESTAMP_NANOS,
    which Spark rejects with PARQUET_TYPE_ILLEGAL / SQLSTATE 42846.
    Coerce datetime columns to microsecond resolution before write.
    """
    if df is None:
        return pd.DataFrame()
    out = df.copy()
    for col in out.columns:
        series = out[col]
        if pd.api.types.is_datetime64_any_dtype(series):
            # datetime64[ns] / datetime64[ns, tz] → us (Spark-supported)
            try:
                if getattr(series.dtype, "tz", None) is not None:
                    out[col] = series.dt.tz_convert("UTC").dt.as_unit("us")
                else:
                    out[col] = series.dt.as_unit("us")
            except (AttributeError, TypeError, ValueError):
                # Older pandas: astype to datetime64[us]
                try:
                    out[col] = series.astype("datetime64[us]")
                except (TypeError, ValueError):
                    out[col] = pd.to_datetime(series, utc=True, errors="coerce").astype(
                        "datetime64[us]"
                    )
    return out


def volume_path(catalog: str, schema: str, volume_name: str, relative: str = "") -> str:
    """Absolute UC volume path: /Volumes/{catalog}/{schema}/{volume}/..."""
    root = f"/Volumes/{catalog.strip()}/{schema.strip()}/{volume_name.strip()}"
    rel = str(relative or "").lstrip("/")
    return f"{root}/{rel}" if rel else root


def classify_volume_http_error(
    status: int,
    body: str,
    *,
    path: str,
) -> DatabricksVolumeStagingError:
    """Map Files / UC API errors to actionable Datasyncher failure modes."""
    text = (body or "").strip()
    lower = text.lower()
    if status in (401, 403) or any(
        tok in lower
        for tok in (
            "permission denied",
            "not authorized",
            "insufficient privileges",
            "access denied",
            "unauthorized",
            "privileges",
            "write volume",
            "read volume",
        )
    ):
        return VolumePermissionError(
            "Databricks managed volume staging failed: insufficient privileges. "
            "Grant READ VOLUME and WRITE VOLUME on "
            f"`{path}` to the principal behind the connection token. "
            f"HTTP {status}: {text[:800]}"
        )
    if status == 404 or any(
        tok in lower
        for tok in (
            "does not exist",
            "not found",
            "volume_does_not_exist",
            "no such volume",
        )
    ):
        return VolumeNotFoundError(
            "Databricks managed volume staging failed: volume or path does not exist. "
            f"Create the Unity Catalog volume and confirm catalog/schema/volume names. "
            f"Path={path!r}. HTTP {status}: {text[:800]}"
        )
    if status in (408, 504) or "timeout" in lower:
        return VolumeUploadTimeoutError(
            f"Databricks managed volume upload timed out for {path!r}. "
            f"HTTP {status}: {text[:800]}"
        )
    return DatabricksVolumeStagingError(
        f"Databricks managed volume staging failed for {path!r}. "
        f"HTTP {status}: {text[:800]}"
    )


class DatabricksVolumeStagingClient:
    """
    Upload / delete chunk files on a Unity Catalog managed volume via Files API.
    """

    def __init__(
        self,
        server_hostname: str,
        access_token: str,
        catalog: str,
        schema: str,
        volume_name: str,
        *,
        multipart_threshold_bytes: int = DEFAULT_MULTIPART_THRESHOLD_BYTES,
        upload_timeout_seconds: int = DEFAULT_UPLOAD_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        host = str(server_hostname or "").strip().rstrip("/")
        if host.startswith("https://"):
            host = host[len("https://") :]
        elif host.startswith("http://"):
            host = host[len("http://") :]
        self.server_hostname = host
        self.access_token = str(access_token or "").strip()
        self.catalog = str(catalog or "").strip()
        self.schema = str(schema or "").strip()
        self.volume_name = str(volume_name or "").strip()
        self.multipart_threshold_bytes = int(multipart_threshold_bytes)
        self.upload_timeout_seconds = int(upload_timeout_seconds)
        self.max_retries = int(max_retries)

        if not self.server_hostname or not self.access_token:
            raise DatabricksVolumeStagingError(
                "Databricks volume staging requires server_hostname and access_token",
                failure_mode="volume_config_error",
            )
        if not self.catalog or not self.schema or not self.volume_name:
            raise DatabricksVolumeStagingError(
                "Databricks managed_volume staging requires catalog, schema, and volume_name",
                failure_mode="volume_config_error",
            )

    @property
    def volume_root(self) -> str:
        return volume_path(self.catalog, self.schema, self.volume_name)

    def absolute_path(self, relative: str) -> str:
        return volume_path(self.catalog, self.schema, self.volume_name, relative)

    def full_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.volume_name}"

    @staticmethod
    def _is_already_exists_error(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return any(
            tok in msg
            for tok in (
                "already exists",
                "already_exist",
                "resource_already_exists",
            )
        )

    def _schema_full_name(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def _ensure_schema_exists(self) -> None:
        """Create UC schema when missing (idempotent)."""
        schema_full = self._schema_full_name()
        api_path = f"/api/2.1/unity-catalog/schemas/{quote(schema_full, safe='._-')}"
        try:
            self._json_request("GET", api_path)
            logger.warning("DBX|schema_ensure_ok | schema=%s", schema_full)
            return
        except VolumeNotFoundError:
            logger.warning("DBX|schema_missing_creating | schema=%s", schema_full)
        except DatabricksVolumeStagingError:
            raise

        payload = {
            "catalog_name": self.catalog,
            "name": self.schema,
        }
        try:
            self._json_request("POST", "/api/2.1/unity-catalog/schemas", payload)
            logger.warning("DBX|schema_created_ok | schema=%s", schema_full)
        except DatabricksVolumeStagingError as exc:
            if self._is_already_exists_error(exc):
                logger.warning(
                    "DBX|schema_create_race_ok | schema=%s | reason=already_exists",
                    schema_full,
                )
                return
            raise

    def _create_managed_volume(self) -> None:
        """Create a MANAGED UC volume (idempotent)."""
        payload = {
            "catalog_name": self.catalog,
            "schema_name": self.schema,
            "name": self.volume_name,
            "volume_type": "MANAGED",
        }
        try:
            self._json_request("POST", "/api/2.1/unity-catalog/volumes", payload)
            logger.warning(
                "DBX|volume_created_ok | volume=%s | path=%s",
                self.full_name(),
                self.volume_root,
            )
        except DatabricksVolumeStagingError as exc:
            if self._is_already_exists_error(exc):
                logger.warning(
                    "DBX|volume_create_race_ok | volume=%s | reason=already_exists",
                    self.full_name(),
                )
                return
            raise

    def ensure_volume_exists(self, *, create_if_missing: bool | None = None) -> None:
        """
        Verify the UC volume exists (GET /api/2.1/unity-catalog/volumes/{full_name}).

        When ``create_if_missing`` is true (default), missing schema/volume are
        created via Unity Catalog REST APIs before re-checking.
        """
        if create_if_missing is None:
            create_if_missing = str(
                os.getenv("DATABRICKS_VOLUME_AUTO_CREATE", "1")
            ).strip().lower() not in ("0", "false", "no", "off")

        full = self.full_name()
        api_path = f"/api/2.1/unity-catalog/volumes/{quote(full, safe='._-')}"
        try:
            self._json_request("GET", api_path)
        except VolumeNotFoundError:
            if not create_if_missing:
                raise
            logger.warning(
                "DBX|volume_missing_creating | volume=%s | path=%s",
                full,
                self.volume_root,
            )
            self._ensure_schema_exists()
            self._create_managed_volume()
            try:
                self._json_request("GET", api_path)
            except DatabricksVolumeStagingError:
                raise
            except Exception as exc:
                raise DatabricksVolumeStagingError(
                    f"Unity Catalog volume '{full}' was not found after create attempt: {exc}",
                    failure_mode="volume_not_found",
                ) from exc
        except DatabricksVolumeStagingError:
            raise
        except Exception as exc:
            raise DatabricksVolumeStagingError(
                f"Failed to verify Unity Catalog volume '{full}': {exc}",
                failure_mode="volume_not_found",
            ) from exc
        logger.warning(
            "DBX|volume_ensure_ok | volume=%s | path=%s",
            full,
            self.volume_root,
        )

    def upload_dataframe(
        self,
        df: pd.DataFrame,
        table_name: str,
        connection_id: int,
        *,
        overwrite: bool = True,
        blob_name: str | None = None,
        column_types: dict | None = None,
    ) -> str:
        """
        Serialize ``df`` to Parquet and upload under the standard Dynamics blob name
        (or a caller-supplied ``blob_name`` for Salesforce isolation).

        Returns the absolute ``/Volumes/...`` path for COPY INTO.
        """
        blob = volume_chunk_blob_name(table_name, connection_id, blob_name=blob_name)
        row_count = len(df.index) if df is not None and hasattr(df, "index") else 0
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".parquet",
                delete=False,
            ) as tmp:
                tmp_path = tmp.name
            # Typed columnar file — smaller upload and fewer CAST clashes vs CSV.
            # coerce_timestamps=us: Spark/Databricks reject TIMESTAMP_NANOS.
            out = dataframe_for_spark_parquet(df)
            if column_types:
                try:
                    from services.salesforce.as_source.destinations.databricks import (
                        prepare_dataframe_for_databricks_parquet,
                    )

                    out = prepare_dataframe_for_databricks_parquet(out, column_types)
                    out = dataframe_for_spark_parquet(out)
                except Exception as prep_exc:
                    logger.warning(
                        "DBX|volume_parquet_type_prepare_failed | table=%s | err=%s",
                        table_name,
                        prep_exc,
                    )
            out.to_parquet(
                tmp_path,
                index=False,
                engine="pyarrow",
                coerce_timestamps="us",
                allow_truncated_timestamps=True,
            )
            size = os.path.getsize(tmp_path)
            logger.warning(
                "DBX|volume_upload_prepare | table=%s | connection_id=%s | rows=%s | "
                "bytes=%s | format=parquet | ts=us | path=%s",
                table_name,
                connection_id,
                row_count,
                size,
                self.absolute_path(blob),
            )
            return self.upload_file(tmp_path, blob, overwrite=overwrite)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.warning(
                        "DBX|volume_local_temp_cleanup_failed | path=%s",
                        tmp_path,
                    )

    def upload_file(
        self,
        local_path: str,
        relative_path: str,
        *,
        overwrite: bool = True,
    ) -> str:
        """Upload a local file to the volume; return ``/Volumes/...`` COPY INTO source."""
        abs_path = self.absolute_path(relative_path)
        size = os.path.getsize(local_path)
        t0 = time.time()
        try:
            if size > self.multipart_threshold_bytes:
                logger.warning(
                    "DBX|volume_upload_multipart_fallback | path=%s | bytes=%s | "
                    "threshold=%s | reason=exceeds_single_put_safety_margin",
                    abs_path,
                    size,
                    self.multipart_threshold_bytes,
                )
                self._upload_multipart(local_path, abs_path, overwrite=overwrite)
            else:
                self._upload_put(local_path, abs_path, overwrite=overwrite)
        except VolumeUploadTimeoutError:
            raise
        except DatabricksVolumeStagingError:
            raise
        except TimeoutError as exc:
            raise VolumeUploadTimeoutError(
                f"Databricks managed volume upload timed out for {abs_path!r}: {exc}"
            ) from exc
        except URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            if "timed out" in reason.lower() or isinstance(
                getattr(exc, "reason", None), TimeoutError
            ):
                raise VolumeUploadTimeoutError(
                    f"Databricks managed volume upload timed out for {abs_path!r}: {reason}"
                ) from exc
            raise DatabricksVolumeStagingError(
                f"Databricks managed volume upload network error for {abs_path!r}: {reason}"
            ) from exc

        logger.warning(
            "DBX|volume_upload_ok | path=%s | bytes=%s | duration_s=%.3f",
            abs_path,
            size,
            time.time() - t0,
        )
        return abs_path

    def delete_file(self, relative_path: str) -> None:
        """Delete a staged file from the volume (best-effort; mirrors GCS cleanup)."""
        abs_path = self.absolute_path(relative_path)
        api_path = f"/api/2.0/fs/files{_encode_volume_fs_path(abs_path)}"
        try:
            self._raw_request("DELETE", api_path, body=None, content_type=None)
            logger.warning("DBX|volume_temp_csv_delete_ok | path=%s", abs_path)
        except VolumeNotFoundError:
            logger.warning(
                "DBX|volume_temp_csv_delete_miss | path=%s | reason=not_found",
                abs_path,
            )
        except Exception as exc:
            logger.warning(
                "DBX|volume_temp_csv_delete_failed | path=%s | err=%s",
                abs_path,
                exc,
            )
            raise

    def delete_chunk(self, table_name: str, connection_id: int) -> None:
        """
        Best-effort cleanup of the legacy fixed blob name.

        Prefer ``delete_absolute`` / ``delete_file`` with the path returned from
        ``upload_dataframe`` — chunk names are now unique per upload.
        """
        legacy = f"temp_data_dynamics_f&o_{table_name}_{connection_id}.parquet"
        legacy_fo = f"temp_data_dynamics_fo_{table_name}_{connection_id}.parquet"
        for name in (legacy, legacy_fo):
            try:
                self.delete_file(name)
            except Exception:
                pass

    def delete_absolute(self, abs_path: str) -> None:
        """Delete a ``/Volumes/...`` object previously returned by ``upload_*``."""
        root = self.volume_root.rstrip("/") + "/"
        raw = str(abs_path or "").strip()
        if raw.startswith(root):
            self.delete_file(raw[len(root) :])
            return
        if raw.startswith("/Volumes/"):
            # Absolute path on this volume but unexpected prefix shape — delete via API.
            encoded = _encode_volume_fs_path(raw)
            api_path = f"/api/2.0/fs/files{encoded}"
            try:
                self._raw_request("DELETE", api_path)
            except VolumeNotFoundError:
                logger.warning(
                    "DBX|volume_temp_csv_delete_miss | path=%s | reason=not_found",
                    raw,
                )
            return
        self.delete_file(raw)

    # ------------------------------------------------------------------ internals

    def _upload_put(self, local_path: str, abs_path: str, *, overwrite: bool) -> None:
        encoded = _encode_volume_fs_path(abs_path)
        api_path = (
            f"/api/2.0/fs/files{encoded}"
            f"?overwrite={'true' if overwrite else 'false'}"
        )
        with open(local_path, "rb") as fh:
            data = fh.read()
        # Retry lives in _raw_request (same Salesforce-style backoff).
        self._raw_request(
            "PUT",
            api_path,
            body=data,
            content_type="application/octet-stream",
        )

    def _upload_multipart(
        self, local_path: str, abs_path: str, *, overwrite: bool
    ) -> None:
        """
        Fall back to Databricks SDK multipart upload when the file exceeds the
        single-PUT safety threshold (default 4 GiB; API hard limit 5 GiB).
        """
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as exc:
            raise DatabricksVolumeStagingError(
                "Chunk file exceeds single-PUT threshold "
                f"({self.multipart_threshold_bytes} bytes) but databricks-sdk is not "
                "installed. Install databricks-sdk or reduce chunk size. "
                f"path={abs_path}",
                failure_mode="volume_multipart_unavailable",
            ) from exc

        @retry_with_exponential_backoff(
            max_retries=self.max_retries,
            log_prefix=f"DBX|volume_multipart_retry path={abs_path}",
        )
        def _do_sdk_upload() -> None:
            client = WorkspaceClient(
                host=f"https://{self.server_hostname}",
                token=self.access_token,
            )
            with open(local_path, "rb") as fh:
                # SDK handles chunked multipart via Files API when size is large.
                client.files.upload(abs_path, fh, overwrite=overwrite)

        try:
            _do_sdk_upload()
        except DatabricksVolumeStagingError:
            raise
        except Exception as exc:
            # Re-classify SDK HTTP-ish errors when possible.
            msg = str(exc)
            status = getattr(exc, "status", None) or getattr(exc, "http_status_code", None)
            if status:
                raise classify_volume_http_error(int(status), msg, path=abs_path) from exc
            lower = msg.lower()
            if "permission" in lower or "unauthorized" in lower or "403" in lower:
                raise VolumePermissionError(
                    f"Databricks managed volume multipart upload denied for {abs_path!r}: {msg}"
                ) from exc
            if "not found" in lower or "404" in lower or "does not exist" in lower:
                raise VolumeNotFoundError(
                    f"Databricks managed volume multipart upload path missing {abs_path!r}: {msg}"
                ) from exc
            if "timed out" in lower or "timeout" in lower:
                raise VolumeUploadTimeoutError(
                    f"Databricks managed volume multipart upload timed out for {abs_path!r}: {msg}"
                ) from exc
            raise DatabricksVolumeStagingError(
                f"Databricks managed volume multipart upload failed for {abs_path!r}: {msg}"
            ) from exc

    def _json_request(self, method: str, api_path: str, payload: Optional[dict] = None) -> dict:
        import json as _json

        body = _json.dumps(payload).encode() if payload is not None else None
        raw = self._raw_request(
            method,
            api_path,
            body=body,
            content_type="application/json" if body is not None else None,
        )
        if not raw:
            return {}
        return _json.loads(raw.decode())

    def _raw_request(
        self,
        method: str,
        api_path: str,
        *,
        body: Optional[bytes],
        content_type: Optional[str],
    ) -> bytes:
        url = f"https://{self.server_hostname}{api_path}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if content_type:
            headers["Content-Type"] = content_type

        @retry_with_exponential_backoff(
            max_retries=self.max_retries,
            log_prefix=f"DBX|volume_api_retry method={method} path={api_path}",
        )
        def _do() -> bytes:
            req = Request(url, data=body, headers=headers, method=method)
            try:
                with urlopen(req, timeout=self.upload_timeout_seconds) as resp:
                    return resp.read() or b""
            except HTTPError as http_exc:
                err_body = ""
                try:
                    err_body = http_exc.read().decode(errors="replace")
                except Exception:
                    pass
                status = int(getattr(http_exc, "code", 0) or 0)
                classified = classify_volume_http_error(
                    status, err_body, path=api_path
                )
                if status in (408, 429, 500, 502, 503, 504):
                    raise RetryableHTTPError(status, str(classified)) from http_exc
                raise classified from http_exc
            except TimeoutError as exc:
                raise VolumeUploadTimeoutError(
                    f"Databricks API {method} {api_path} timed out: {exc}"
                ) from exc
            except URLError as exc:
                reason = str(getattr(exc, "reason", exc))
                if "timed out" in reason.lower():
                    raise VolumeUploadTimeoutError(
                        f"Databricks API {method} {api_path} timed out: {reason}"
                    ) from exc
                # Network blips are retryable.
                raise RetryableHTTPError(503, reason) from exc

        return _do()
