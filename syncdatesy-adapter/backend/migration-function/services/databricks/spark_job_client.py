"""
Databricks Jobs client for Dynamics F&O bulk Spark MERGE path.

Supports:
  - GCS landing (Azure worker copies to gs://, Spark reads landed prefix)
  - ADLS staging copy (Method A: copy to Datasyncher Azure staging, Spark reads abfss://)
  - ADLS direct read (Method B: Spark reads F&O export in-place via abfss://)

Auto-provisions (when permitted):
  - Unity Catalog external location for the landing/export prefix
  - Workspace notebook import
  - Named Databricks Job with serverless or classic compute
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from services.databricks.load_helpers import (
    normalize_databricks_server_hostname,
    AZURE_SPARK_READ_MODE_STAGING,
    SPARK_LANDING_CLOUD_AZURE,
    SPARK_LANDING_CLOUD_GCS,
    SPARK_LANDING_CLOUD_S3,
    SPARK_STAGING_ACCESS_EXTERNAL_LOCATION,
    SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL,
    _databricks_api_request,
    ensure_databricks_adls_external_location_once,
    ensure_databricks_gcs_external_location_once,
    ensure_databricks_s3_external_location_once,
    resolve_azure_spark_read_mode,
    resolve_databricks_adls_settings,
    resolve_databricks_adls_staging_location_name,
    resolve_databricks_adls_staging_settings,
    resolve_databricks_gcs_connection_location_name,
    resolve_databricks_gcs_settings,
    resolve_databricks_s3_connection_location_name,
    resolve_databricks_s3_settings,
    resolve_dynamics_adls_export_url,
    resolve_dynamics_adls_staging_url,
    resolve_dynamics_gcs_staging_url,
    resolve_dynamics_s3_staging_url,
    resolve_spark_landing_cloud,
    resolve_spark_staging_access,
    DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS,
    spark_scoped_fallback_enabled,
    verify_databricks_adls_prefix_readable,
    verify_databricks_gcs_prefix_readable,
    verify_databricks_s3_prefix_readable,
)
from services.databricks.staging_scoped_credentials import (
    ScopedCredentialError,
    build_scoped_credential_spark_conf,
    default_node_type_for_landing_cloud,
    deliver_scoped_credentials_to_workspace,
    format_scoped_credential_blocker_message,
    is_scoped_credential_workspace_blocker,
    mint_scoped_staging_credentials,
    scoped_job_use_single_node,
)

logger = logging.getLogger(__name__)

DEFAULT_GCS_JOB_NAME = "datasyncher_dynamics_fo_gcs_merge"
DEFAULT_GCS_NOTEBOOK_PATH = "/Shared/datasyncher/dynamics_fo_gcs_merge"
DEFAULT_ADLS_JOB_NAME = "datasyncher_dynamics_fo_adls_merge"
DEFAULT_ADLS_NOTEBOOK_PATH = "/Shared/datasyncher/dynamics_fo_adls_merge"
DEFAULT_S3_JOB_NAME = "datasyncher_dynamics_fo_s3_merge"
DEFAULT_S3_NOTEBOOK_PATH = "/Shared/datasyncher/dynamics_fo_s3_merge"
DEFAULT_JOB_NAME = DEFAULT_GCS_JOB_NAME
DEFAULT_NOTEBOOK_PATH = DEFAULT_GCS_NOTEBOOK_PATH
DEFAULT_JOB_CLUSTER_KEY = "datasyncher_spark_merge"
DEFAULT_NODE_TYPE = os.environ.get("DYNAMICS_DBX_SPARK_NODE_TYPE", "n2-standard-4")
DEFAULT_NUM_WORKERS = int(os.environ.get("DYNAMICS_DBX_SPARK_NUM_WORKERS", "2"))
# ``latest-stable-scala2.12`` is not accepted by Azure/AWS/GCP Jobs API for classic
# clusters. Prefer an explicit env override, else resolve from the workspace, else LTS.
DEFAULT_SPARK_VERSION = os.environ.get(
    "DYNAMICS_DBX_SPARK_VERSION", "15.4.x-scala2.12"
).strip()
_FALLBACK_SPARK_VERSION_BY_CLOUD = {
    SPARK_LANDING_CLOUD_AZURE: "15.4.x-scala2.12",
    SPARK_LANDING_CLOUD_S3: "15.4.x-scala2.12",
    SPARK_LANDING_CLOUD_GCS: "15.4.x-scala2.12",
}
DEFAULT_POLL_SECONDS = int(os.environ.get("DYNAMICS_DBX_SPARK_POLL_SECONDS", "15"))
DEFAULT_JOB_TIMEOUT_SECONDS = int(
    os.environ.get(
        "DYNAMICS_DBX_SPARK_JOB_TIMEOUT_SECONDS",
        str(DEFAULT_DYNAMICS_SPARK_JOB_POLL_TIMEOUT_SECONDS),
    )
)
DEFAULT_COMPUTE_MODE = os.environ.get(
    "DYNAMICS_DBX_SPARK_COMPUTE_MODE", "serverless"
).strip().lower()
COMPUTE_MODE_EXISTING_CLUSTER = "existing_cluster"
_VALID_SPARK_COMPUTE_MODES = frozenset(
    {"serverless", "classic", COMPUTE_MODE_EXISTING_CLUSTER}
)

_SPARK_DIR = Path(__file__).resolve().parents[2] / "spark"
_SPARK_ARTIFACTS = {
    SPARK_LANDING_CLOUD_GCS: _SPARK_DIR / "dynamics_fo_gcs_merge.py",
    SPARK_LANDING_CLOUD_AZURE: _SPARK_DIR / "dynamics_fo_adls_merge.py",
    SPARK_LANDING_CLOUD_S3: _SPARK_DIR / "dynamics_fo_s3_merge.py",
}


class DatabricksSparkJobError(Exception):
    """Raised when Spark job setup or execution fails."""

    def __init__(self, message: str, *, run_id: int | None = None, run_url: str | None = None):
        super().__init__(message)
        self.run_id = run_id
        self.run_url = run_url


class DatabricksSparkJobPermissionError(DatabricksSparkJobError):
    """Workspace token lacks Jobs API permissions."""


class DatabricksSparkJobScopedAccessError(DatabricksSparkJobError):
    """Workspace blocked the scoped-credential classic-cluster fallback."""


def _engine_creds(engine) -> tuple[str, str]:
    cfg = getattr(engine, "config", {}) or {}
    host = normalize_databricks_server_hostname(
        str(cfg.get("server_hostname") or cfg.get("host") or "")
    )
    token = str(cfg.get("access_token") or cfg.get("token") or "").strip()
    if not host or not token:
        raise DatabricksSparkJobError(
            "Databricks engine config missing server_hostname or access_token"
        )
    return host, token


def _pick_dest(destination_connection: dict | None, *keys: str, default: str = "") -> str:
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    for key in keys:
        val = dest.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def _default_job_name(landing_cloud: str) -> str:
    if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
        return os.environ.get("DYNAMICS_DBX_SPARK_ADLS_JOB_NAME", DEFAULT_ADLS_JOB_NAME)
    if landing_cloud == SPARK_LANDING_CLOUD_S3:
        return os.environ.get("DYNAMICS_DBX_SPARK_S3_JOB_NAME", DEFAULT_S3_JOB_NAME)
    return os.environ.get("DYNAMICS_DBX_SPARK_JOB_NAME", DEFAULT_GCS_JOB_NAME)


def dedicated_dynamics_spark_job_name(connection_id: int) -> str:
    """Per-connector Lakeflow Job name when dedicated serverless provisioning is enabled."""
    return f"datasyncher_dynamics_fo_conn_{int(connection_id)}"


def dedicated_dynamics_spark_job_name_for_destination(dst_config_id: int) -> str:
    """Dedicated Lakeflow Job name stored on the Databricks destination config."""
    return f"datasyncher_dynamics_fo_dst_{int(dst_config_id)}"


def _destination_wants_dedicated_serverless_job(destination_connection: dict | None) -> bool:
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    raw = _pick_dest(
        dest,
        "create_dedicated_dbx_serverless_job",
        "dedicated_serverless_job",
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_dedicated_spark_job_name(
    destination_connection: dict | None,
    *,
    connection_id: int,
) -> str:
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    explicit = _pick_dest(dest, "spark_job_name")
    if explicit:
        return explicit
    dst_id = _pick_dest(dest, "dst_config_id")
    if dst_id:
        try:
            return dedicated_dynamics_spark_job_name_for_destination(int(dst_id))
        except (TypeError, ValueError):
            pass
    if connection_id:
        return dedicated_dynamics_spark_job_name(connection_id)
    return _default_job_name(resolve_spark_landing_cloud(dest))


def _default_notebook_path(landing_cloud: str) -> str:
    if landing_cloud == SPARK_LANDING_CLOUD_AZURE:
        return os.environ.get(
            "DYNAMICS_DBX_SPARK_ADLS_NOTEBOOK_PATH", DEFAULT_ADLS_NOTEBOOK_PATH
        )
    if landing_cloud == SPARK_LANDING_CLOUD_S3:
        return os.environ.get(
            "DYNAMICS_DBX_SPARK_S3_NOTEBOOK_PATH", DEFAULT_S3_NOTEBOOK_PATH
        )
    return os.environ.get("DYNAMICS_DBX_SPARK_NOTEBOOK_PATH", DEFAULT_GCS_NOTEBOOK_PATH)


def _landing_bucket_url() -> str:
    gcs = resolve_databricks_gcs_settings()
    url = str(gcs.get("gcs_bucket_url") or os.environ.get("GCS_BUCKET_URL") or "").strip()
    if not url:
        bucket = os.environ.get("GCS_BUCKET_NAME", "").strip()
        if bucket:
            url = f"gs://{bucket}/"
    if not url:
        raise DatabricksSparkJobError("GCS_BUCKET_URL or GCS_BUCKET_NAME is not configured")
    if not url.endswith("/"):
        url = f"{url}/"
    return url


def _read_spark_artifact(landing_cloud: str) -> bytes:
    artifact_path = _SPARK_ARTIFACTS.get(landing_cloud) or _SPARK_ARTIFACTS[SPARK_LANDING_CLOUD_GCS]
    if not artifact_path.is_file():
        raise DatabricksSparkJobError(f"Spark artifact not found at {artifact_path}")
    return artifact_path.read_bytes()


def _workspace_parent_path(notebook_path: str) -> str:
    """Return the workspace directory that must exist before notebook import."""
    path = str(notebook_path or "").strip().rstrip("/")
    if not path or "/" not in path:
        raise DatabricksSparkJobError(
            f"Invalid spark_notebook_path for workspace import: {notebook_path!r}"
        )
    return path.rsplit("/", 1)[0]


def _ensure_workspace_parent_dirs(
    server_hostname: str, access_token: str, notebook_path: str
) -> str:
    """
    Create the notebook parent folder in the Databricks workspace when missing.

    Workspace import fails with RESOURCE_DOES_NOT_EXIST if parent folders are absent.
    mkdirs is idempotent and creates intermediate directories as needed.
    """
    parent_path = _workspace_parent_path(notebook_path)
    _databricks_api_request(
        server_hostname,
        access_token,
        "POST",
        "/api/2.0/workspace/mkdirs",
        {"path": parent_path},
    )
    logger.warning(
        "DBX|spark_job | workspace_parent_ready | path=%s | notebook=%s",
        parent_path,
        notebook_path,
    )
    return parent_path


def _import_notebook(
    server_hostname: str,
    access_token: str,
    notebook_path: str,
    *,
    landing_cloud: str,
) -> None:
    _ensure_workspace_parent_dirs(server_hostname, access_token, notebook_path)
    content_b64 = base64.b64encode(_read_spark_artifact(landing_cloud)).decode("ascii")
    payload = {
        "path": notebook_path,
        "format": "SOURCE",
        "language": "PYTHON",
        "content": content_b64,
        "overwrite": True,
    }
    _databricks_api_request(
        server_hostname,
        access_token,
        "POST",
        "/api/2.0/workspace/import",
        payload,
    )
    logger.warning(
        "DBX|spark_job | notebook_imported | path=%s | landing_cloud=%s",
        notebook_path,
        landing_cloud,
    )


def _find_job_by_name(server_hostname: str, access_token: str, job_name: str) -> dict | None:
    offset = 0
    while True:
        resp = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            f"/api/2.1/jobs/list?name={job_name}&limit=25&offset={offset}",
        )
        jobs = resp.get("jobs") or []
        for job in jobs:
            settings = job.get("settings") or {}
            if str(settings.get("name") or "") == job_name:
                return job
        if not resp.get("has_more"):
            break
        offset += len(jobs)
    return None


def all_purpose_spark_enabled() -> bool:
    """When true, all-purpose destination http_path runs Spark on that cluster."""
    raw = str(os.environ.get("DYNAMICS_DBX_SPARK_USE_ALL_PURPOSE_CLUSTER") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _destination_http_path(destination_connection: dict | None) -> str:
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    return str(_pick_dest(dest, "http_path") or "").strip()


def _resolve_existing_cluster_id(destination_connection: dict | None) -> str | None:
    from datasyncher_connections.databricks.connector import (
        _cluster_id_from_http_path,
        is_databricks_cluster_compute_http_path,
    )

    http_path = _destination_http_path(destination_connection)
    if not is_databricks_cluster_compute_http_path(http_path):
        return None
    try:
        return _cluster_id_from_http_path(http_path)
    except ValueError:
        return None


def _resolve_spark_compute_mode(destination_connection: dict | None = None) -> str:
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    explicit = _pick_dest(dest, "spark_compute_mode")
    if explicit:
        mode = str(explicit).strip().lower()
        if mode in {"all_purpose", "all-purpose", "existing", "existing_cluster"}:
            return COMPUTE_MODE_EXISTING_CLUSTER
        if mode in _VALID_SPARK_COMPUTE_MODES:
            return mode
    env_mode = str(os.environ.get("DYNAMICS_DBX_SPARK_COMPUTE_MODE") or "").strip().lower()
    if env_mode in _VALID_SPARK_COMPUTE_MODES:
        return env_mode
    if all_purpose_spark_enabled() and _resolve_existing_cluster_id(dest):
        return COMPUTE_MODE_EXISTING_CLUSTER
    fallback = str(DEFAULT_COMPUTE_MODE or "serverless").strip().lower()
    return fallback if fallback in _VALID_SPARK_COMPUTE_MODES else "serverless"


def _ensure_all_purpose_cluster_running(destination_connection: dict | None) -> None:
    from datasyncher_connections.databricks.connector import (
        ensure_cluster_compute_running,
        normalize_databricks_config,
    )

    cfg = normalize_databricks_config(destination_connection or {})
    ensure_cluster_compute_running(cfg)


def _resolve_classic_spark_version(
    server_hostname: str,
    access_token: str,
    *,
    landing_cloud: str = SPARK_LANDING_CLOUD_GCS,
    destination_connection: dict | None = None,
) -> str:
    """
    Pick a classic-cluster ``spark_version`` key accepted by this workspace.

    Explicit destination / env wins. Otherwise query ``/api/2.0/clusters/spark-versions``
    and prefer the newest Scala 2.12 LTS key.
    """
    explicit = _pick_dest(destination_connection, "spark_version", "spark_runtime_version")
    if explicit and explicit.lower() not in {"latest-stable-scala2.12", "latest", "auto"}:
        return explicit
    env_explicit = str(os.environ.get("DYNAMICS_DBX_SPARK_VERSION") or "").strip()
    if env_explicit and env_explicit.lower() not in {
        "latest-stable-scala2.12",
        "latest",
        "auto",
    }:
        return env_explicit

    fallback = _FALLBACK_SPARK_VERSION_BY_CLOUD.get(
        str(landing_cloud or "").strip().lower(),
        "15.4.x-scala2.12",
    )
    try:
        resp = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            "/api/2.0/clusters/spark-versions",
        )
        versions = resp.get("versions") or []
        lts_keys: list[str] = []
        scala212_keys: list[str] = []
        for item in versions:
            key = str((item or {}).get("key") or "").strip()
            name = str((item or {}).get("name") or "").lower()
            if not key or "scala2.12" not in key.lower():
                continue
            scala212_keys.append(key)
            if "lts" in name or ".x-scala" in key.lower():
                lts_keys.append(key)
        # Prefer LTS keys sorted descending so 15.4 > 14.3 > 13.3.
        for pool in (lts_keys, scala212_keys):
            if not pool:
                continue
            pool_sorted = sorted(pool, reverse=True)
            chosen = pool_sorted[0]
            logger.warning(
                "DBX|spark_job | spark_version_resolved | version=%s | candidates=%s",
                chosen,
                len(pool_sorted),
            )
            return chosen
    except Exception as exc:
        logger.warning(
            "DBX|spark_job | spark_version_lookup_failed | fallback=%s | err=%s",
            fallback,
            str(exc)[:200],
        )
    return fallback


def _default_adaptive_spark_conf() -> dict[str, str]:
    return {
        "spark.sql.files.ignoreMissingFiles": "true",
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.adaptive.coalescePartitions.enabled": "true",
        "spark.sql.adaptive.skewJoin.enabled": "true",
        "spark.sql.adaptive.localShuffleReader.enabled": "true",
        "spark.sql.files.maxPartitionBytes": "134217728",
        "spark.sql.files.openCostInBytes": "4194304",
    }


def _job_settings_payload(
    notebook_path: str,
    job_name: str,
    *,
    compute_mode: str = "serverless",
    landing_cloud: str = SPARK_LANDING_CLOUD_GCS,
    spark_conf: dict[str, str] | None = None,
    data_security_mode: str | None = None,
    node_type_id: str | None = None,
    spark_version: str | None = None,
    num_workers: int | None = None,
    single_node: bool = False,
    existing_cluster_id: str | None = None,
) -> dict[str, Any]:
    task_key = (
        "dynamics_fo_adls_merge"
        if landing_cloud == SPARK_LANDING_CLOUD_AZURE
        else "dynamics_fo_s3_merge"
        if landing_cloud == SPARK_LANDING_CLOUD_S3
        else "dynamics_fo_gcs_merge"
    )
    task: dict[str, Any] = {
        "task_key": task_key,
        "notebook_task": {
            "notebook_path": notebook_path,
            "base_parameters": {},
        },
    }
    payload: dict[str, Any] = {
        "name": job_name,
        "max_concurrent_runs": 4,
        "tasks": [task],
    }
    if compute_mode == "classic":
        cluster_spark_conf = dict(spark_conf or _default_adaptive_spark_conf())
        workers = 0 if single_node else (
            DEFAULT_NUM_WORKERS if num_workers is None else max(0, int(num_workers))
        )
        if single_node or workers == 0:
            cluster_spark_conf["spark.databricks.cluster.profile"] = "singleNode"
            cluster_spark_conf["spark.master"] = "local[*]"
            workers = 0
        new_cluster: dict[str, Any] = {
            "spark_version": spark_version
            or DEFAULT_SPARK_VERSION
            or "15.4.x-scala2.12",
            "node_type_id": node_type_id
            or default_node_type_for_landing_cloud(landing_cloud)
            or DEFAULT_NODE_TYPE,
            "num_workers": workers,
            "spark_conf": cluster_spark_conf,
        }
        if single_node or workers == 0:
            new_cluster["custom_tags"] = {"ResourceClass": "SingleNode"}
        if data_security_mode:
            new_cluster["data_security_mode"] = data_security_mode
        payload["job_clusters"] = [
            {
                "job_cluster_key": DEFAULT_JOB_CLUSTER_KEY,
                "new_cluster": new_cluster,
            }
        ]
        task["job_cluster_key"] = DEFAULT_JOB_CLUSTER_KEY
    elif compute_mode == COMPUTE_MODE_EXISTING_CLUSTER:
        cluster_id = str(existing_cluster_id or "").strip()
        if not cluster_id:
            raise DatabricksSparkJobError(
                "existing_cluster Spark compute requires a Databricks all-purpose "
                "cluster id (destination http_path must be /sql/protocolv1/...)."
            )
        task["existing_cluster_id"] = cluster_id
    return payload


def _create_or_update_job(
    server_hostname: str,
    access_token: str,
    *,
    job_name: str,
    notebook_path: str,
    compute_mode: str = "serverless",
    landing_cloud: str = SPARK_LANDING_CLOUD_GCS,
    existing_job_id: int | None = None,
    spark_conf: dict[str, str] | None = None,
    data_security_mode: str | None = None,
    node_type_id: str | None = None,
    spark_version: str | None = None,
    num_workers: int | None = None,
    single_node: bool = False,
    existing_cluster_id: str | None = None,
) -> int:
    settings = _job_settings_payload(
        notebook_path,
        job_name,
        compute_mode=compute_mode,
        landing_cloud=landing_cloud,
        spark_conf=spark_conf,
        data_security_mode=data_security_mode,
        node_type_id=node_type_id,
        spark_version=spark_version,
        num_workers=num_workers,
        single_node=single_node,
        existing_cluster_id=existing_cluster_id,
    )
    if existing_job_id:
        _databricks_api_request(
            server_hostname,
            access_token,
            "POST",
            "/api/2.1/jobs/reset",
            {"job_id": existing_job_id, "new_settings": settings},
        )
        logger.warning("DBX|spark_job | job_updated | job_id=%s | name=%s", existing_job_id, job_name)
        return int(existing_job_id)

    resp = _databricks_api_request(
        server_hostname,
        access_token,
        "POST",
        "/api/2.1/jobs/create",
        settings,
    )
    job_id = int(resp["job_id"])
    logger.warning("DBX|spark_job | job_created | job_id=%s | name=%s", job_id, job_name)
    return job_id


def _ensure_uc_external_location_for_spark(
    dest: dict,
    engine,
    *,
    connection_id: int,
    resolved_landing_cloud: str,
    cmp_id=None,
    migration_session_id=None,
    job_name=None,
    usr_id=None,
    session=None,
    container_name: str | None = None,
    connection_string: str | None = None,
    base_folder: str | None = None,
) -> dict[str, Any]:
    """Provision / verify UC external location for the external_location Spark path."""
    ext_result: dict[str, Any] = {}

    if resolved_landing_cloud == SPARK_LANDING_CLOUD_AZURE:
        azure_read_mode = resolve_azure_spark_read_mode(dest)
        if azure_read_mode == AZURE_SPARK_READ_MODE_STAGING:
            staging_settings = resolve_databricks_adls_staging_settings(dest)
            if not str(staging_settings.get("staging_url") or "").strip():
                raise DatabricksSparkJobError(
                    "Azure Spark staging read requires adls_staging_url on the destination "
                    "connection or DATASYNCHER_AZURE_STAGING_URL in the environment."
                )
            adls_url = resolve_dynamics_adls_staging_url(dest, connection_id=connection_id)
            adls_settings = staging_settings
            location_label = "staging"
        else:
            if not str(container_name or "").strip() or not str(connection_string or "").strip():
                raise DatabricksSparkJobError(
                    "ADLS direct Spark requires Dynamics export container_name and "
                    "connection_string (source ADLS). base_folder may be empty for "
                    "root-level date folders."
                )
            if base_folder is None:
                base_folder = ""
            adls_url = resolve_dynamics_adls_export_url(
                dest,
                container_name=container_name,
                connection_string=connection_string,
                base_folder=base_folder,
            )
            adls_settings = resolve_databricks_adls_settings(dest)
            location_label = "export"

        staging_location_name = (
            resolve_databricks_adls_staging_location_name(connection_id, dest)
            if location_label == "staging"
            else adls_settings.get("location_name", "datasyncher_fo_export")
        )
        platform_sp = (
            adls_settings.get("platform_sp")
            if location_label == "staging"
            else None
        )
        ext_result = ensure_databricks_adls_external_location_once(
            connection_id,
            engine,
            adls_url,
            cmp_id=cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
            credential_name=adls_settings.get(
                "credential_name",
                "datasyncher_fo_staging_cred"
                if location_label == "staging"
                else "datasyncher_fo_export_cred",
            ),
            location_name=staging_location_name,
            grant_principal=adls_settings.get("grant_principal", "account users"),
            create_credential=adls_settings.get("mode") != "manual",
            azure_service_principal=platform_sp if isinstance(platform_sp, dict) else None,
        )
        resolved_location = ext_result.get("resolved_location_name") or staging_location_name
        logger.warning(
            "DBX|spark_job | adls_external_location | mode=%s | uc_mode=%s | location=%s | "
            "resolved_location=%s | overlap_reused=%s | grant_applied=%s | url=%s",
            location_label,
            adls_settings.get("mode"),
            staging_location_name,
            resolved_location,
            ext_result.get("overlap_reused"),
            ext_result.get("grant_applied"),
            adls_url,
        )
        if location_label == "staging":
            list_check = verify_databricks_adls_prefix_readable(engine, adls_url)
            if not list_check.get("ok") and not list_check.get("skipped_reason"):
                err = list_check.get("error") or "LIST failed"
                lower = str(err).lower()
                permissionish = any(
                    token in lower
                    for token in (
                        "permission",
                        "unauthorized",
                        "access denied",
                        "does not have",
                        "external location",
                        "credential",
                        "forbidden",
                        "uc_",
                    )
                )
                if permissionish:
                    raise DatabricksSparkJobError(
                        f"Databricks cannot LIST staging prefix {adls_url!r} after "
                        f"external location setup (location={resolved_location}). "
                        f"Fix UC READ FILES / storage credential. Detail: {err}"
                    )
                logger.warning(
                    "DBX|spark_job | adls_prefix_list_soft_fail | url=%s | error=%s",
                    adls_url,
                    str(err)[:300],
                )
            else:
                logger.warning(
                    "DBX|spark_job | adls_prefix_list_ok | url=%s | entries=%s | skipped=%s",
                    adls_url,
                    list_check.get("entry_count"),
                    list_check.get("skipped_reason"),
                )
        return ext_result

    if resolved_landing_cloud == SPARK_LANDING_CLOUD_S3:
        s3_settings = resolve_databricks_s3_settings(dest)
        try:
            bucket_url = resolve_dynamics_s3_staging_url(
                dest, connection_id=connection_id
            )
        except ValueError as exc:
            raise DatabricksSparkJobError(str(exc)) from exc
        location_name = resolve_databricks_s3_connection_location_name(
            connection_id, dest
        )
        ext_result = ensure_databricks_s3_external_location_once(
            connection_id,
            engine,
            bucket_url,
            cmp_id=cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
            credential_name=s3_settings.get(
                "credential_name", "datasyncher_batch_s3_cred"
            ),
            location_name=location_name,
            grant_principal=s3_settings.get("grant_principal", "account users"),
            create_credential=s3_settings.get("mode") == "auto",
            aws_iam_role_arn=s3_settings.get("iam_role_arn") or None,
        )
        resolved_location = ext_result.get("resolved_location_name") or location_name
        logger.warning(
            "DBX|spark_job | s3_external_location | uc_mode=%s | location=%s | "
            "resolved_location=%s | overlap_reused=%s | grant_applied=%s | url=%s",
            s3_settings.get("mode"),
            location_name,
            resolved_location,
            ext_result.get("overlap_reused"),
            ext_result.get("grant_applied"),
            bucket_url,
        )
        list_check = verify_databricks_s3_prefix_readable(engine, bucket_url)
        if not list_check.get("ok") and not list_check.get("skipped_reason"):
            err = list_check.get("error") or "LIST failed"
            lower = str(err).lower()
            permissionish = any(
                token in lower
                for token in (
                    "permission",
                    "unauthorized",
                    "access denied",
                    "does not have",
                    "external location",
                    "credential",
                    "forbidden",
                    "uc_",
                )
            )
            if permissionish:
                raise DatabricksSparkJobError(
                    f"Databricks cannot LIST S3 staging prefix {bucket_url!r} after "
                    f"external location setup (location={resolved_location}). "
                    f"Fix UC READ FILES / storage credential. Detail: {err}"
                )
            logger.warning(
                "DBX|spark_job | s3_prefix_list_soft_fail | url=%s | error=%s",
                bucket_url,
                str(err)[:300],
            )
        else:
            logger.warning(
                "DBX|spark_job | s3_prefix_list_ok | url=%s | entries=%s | skipped=%s",
                bucket_url,
                list_check.get("entry_count"),
                list_check.get("skipped_reason"),
            )
        return ext_result

    gcs_settings = resolve_databricks_gcs_settings(dest)
    try:
        bucket_url = resolve_dynamics_gcs_staging_url(
            dest, connection_id=connection_id
        )
    except ValueError:
        bucket_url = _landing_bucket_url()
    location_name = resolve_databricks_gcs_connection_location_name(
        connection_id, dest
    )
    ext_result = ensure_databricks_gcs_external_location_once(
        connection_id,
        engine,
        bucket_url,
        cmp_id=cmp_id,
        migration_session_id=migration_session_id,
        job_name=job_name,
        usr_id=usr_id,
        session=session,
        credential_name=gcs_settings.get(
            "credential_name", "datasyncher_batch_gcs_cred"
        ),
        location_name=location_name,
        grant_principal=gcs_settings.get("grant_principal", "account users"),
        create_credential=gcs_settings.get("mode") != "manual",
        known_service_account=gcs_settings.get("service_account_email"),
        grant_bucket_iam=gcs_settings.get("mode") != "manual",
    )
    list_check = verify_databricks_gcs_prefix_readable(engine, bucket_url)
    if not list_check.get("ok") and not list_check.get("skipped_reason"):
        err = list_check.get("error") or "LIST failed"
        lower = str(err).lower()
        permissionish = any(
            token in lower
            for token in (
                "permission",
                "unauthorized",
                "access denied",
                "does not have",
                "external location",
                "credential",
                "forbidden",
                "uc_",
            )
        )
        if permissionish:
            logger.warning(
                "DBX|spark_job | gcs_prefix_list_permission_warn | url=%s | error=%s",
                bucket_url,
                str(err)[:300],
            )
        else:
            logger.warning(
                "DBX|spark_job | gcs_prefix_list_soft_fail | url=%s | error=%s",
                bucket_url,
                str(err)[:300],
            )
    else:
        logger.warning(
            "DBX|spark_job | gcs_prefix_list_ok | url=%s | entries=%s | skipped=%s",
            bucket_url,
            list_check.get("entry_count"),
            list_check.get("skipped_reason"),
        )
    return ext_result


def ensure_dynamics_spark_job(
    destination_connection: dict | None,
    engine,
    *,
    connection_id: int,
    cmp_id=None,
    migration_session_id=None,
    job_name: str | None = None,
    usr_id=None,
    session=None,
    landing_cloud: str | None = None,
    container_name: str | None = None,
    connection_string: str | None = None,
    base_folder: str | None = None,
) -> dict[str, Any]:
    """
    Ensure notebook + Databricks Job exist for Spark loads.

    Staging reads use Unity Catalog external locations when permitted; otherwise a
    classic single-user job cluster with short-lived scoped credentials.
    """
    dest = destination_connection if isinstance(destination_connection, dict) else {}
    resolved_landing_cloud = landing_cloud or resolve_spark_landing_cloud(dest)
    server_hostname, access_token = _engine_creds(engine)

    explicit_job_id = _pick_dest(dest, "spark_job_id")
    notebook_path = _pick_dest(
        dest,
        "spark_notebook_path",
        default=_default_notebook_path(resolved_landing_cloud),
    )

    azure_read_mode = (
        resolve_azure_spark_read_mode(dest)
        if resolved_landing_cloud == SPARK_LANDING_CLOUD_AZURE
        else AZURE_SPARK_READ_MODE_STAGING
    )
    use_staging_access_resolver = (
        resolved_landing_cloud != SPARK_LANDING_CLOUD_AZURE
        or azure_read_mode == AZURE_SPARK_READ_MODE_STAGING
    )

    spark_staging_access = SPARK_STAGING_ACCESS_EXTERNAL_LOCATION
    ext_result: dict[str, Any] = {}
    scoped_spark_conf: dict[str, str] | None = None
    job_definition_only = str(
        _pick_dest(dest, "_job_definition_only") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}

    if job_definition_only:
        use_staging_access_resolver = False
        ext_result = {
            "mode": "job_definition_only",
            "skipped_reason": "staging_deferred_until_migration",
        }

    if use_staging_access_resolver:
        if (
            resolved_landing_cloud == SPARK_LANDING_CLOUD_AZURE
            and not str(
                resolve_databricks_adls_staging_settings(dest).get("staging_url") or ""
            ).strip()
        ):
            raise DatabricksSparkJobError(
                "Azure Spark staging read requires adls_staging_url on the destination "
                "connection or DATASYNCHER_AZURE_STAGING_URL in the environment."
            )
        spark_staging_access = resolve_spark_staging_access(
            dest,
            engine,
            connection_id=connection_id,
            landing_cloud=resolved_landing_cloud,
            cmp_id=cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
        )

    if (
        spark_staging_access == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL
        and not spark_scoped_fallback_enabled()
    ):
        logger.warning(
            "DBX|spark_job | scoped_fallback_disabled | forcing external_location+serverless | "
            "connection_id=%s | cloud=%s",
            connection_id,
            resolved_landing_cloud,
        )
        spark_staging_access = SPARK_STAGING_ACCESS_EXTERNAL_LOCATION

    if spark_staging_access == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL:
        try:
            minted = mint_scoped_staging_credentials(
                resolved_landing_cloud,
                connection_id=connection_id,
                destination_connection=dest,
            )
            secret_keys = deliver_scoped_credentials_to_workspace(
                server_hostname,
                access_token,
                connection_id=connection_id,
                minted=minted,
            )
            scoped_spark_conf = build_scoped_credential_spark_conf(
                resolved_landing_cloud,
                connection_id=connection_id,
                minted=minted,
                secret_keys=secret_keys,
            )
            ext_result = {
                "mode": "scoped_credential",
                "skipped_reason": None,
                "scoped_cloud": resolved_landing_cloud,
            }
            logger.warning(
                "DBX|spark_job | scoped_credential_ready | cloud=%s | connection_id=%s",
                resolved_landing_cloud,
                connection_id,
            )
        except ScopedCredentialError as exc:
            if is_scoped_credential_workspace_blocker(exc):
                raise DatabricksSparkJobScopedAccessError(
                    format_scoped_credential_blocker_message(exc)
                ) from exc
            raise DatabricksSparkJobError(str(exc)) from exc
    else:
        ext_result = _ensure_uc_external_location_for_spark(
            dest,
            engine,
            connection_id=connection_id,
            resolved_landing_cloud=resolved_landing_cloud,
            cmp_id=cmp_id,
            migration_session_id=migration_session_id,
            job_name=job_name,
            usr_id=usr_id,
            session=session,
            container_name=container_name,
            connection_string=connection_string,
            base_folder=base_folder,
        )

    single_node = False
    if spark_staging_access == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL:
        compute_mode = "classic"
        data_security_mode = "SINGLE_USER"
        node_type_id = default_node_type_for_landing_cloud(resolved_landing_cloud)
        single_node = scoped_job_use_single_node()
        resolved_job_name = (
            _pick_dest(dest, "spark_job_name")
            or f"{_default_job_name(resolved_landing_cloud)}_scoped"
        )
    else:
        data_security_mode = None
        node_type_id = None
        if _destination_wants_dedicated_serverless_job(dest):
            compute_mode = "serverless"
            resolved_job_name = _resolve_dedicated_spark_job_name(
                dest,
                connection_id=connection_id,
            )
        else:
            compute_mode = _resolve_spark_compute_mode(dest)
            resolved_job_name = (
                _pick_dest(dest, "spark_job_name")
                or _default_job_name(resolved_landing_cloud)
            )

    existing_cluster_id: str | None = None
    if compute_mode == COMPUTE_MODE_EXISTING_CLUSTER:
        existing_cluster_id = _resolve_existing_cluster_id(dest)
        if not existing_cluster_id:
            logger.warning(
                "DBX|spark_job | existing_cluster_unavailable | falling_back=serverless | "
                "connection_id=%s",
                connection_id,
            )
            compute_mode = "serverless"
        else:
            _ensure_all_purpose_cluster_running(dest)
            logger.warning(
                "DBX|spark_job | existing_cluster_ready | cluster_id=%s | connection_id=%s",
                existing_cluster_id,
                connection_id,
            )

    try:
        _import_notebook(
            server_hostname,
            access_token,
            notebook_path,
            landing_cloud=resolved_landing_cloud,
        )
        if explicit_job_id:
            job_id = int(explicit_job_id)
            logger.warning(
                "DBX|spark_job | using_explicit_job_id | job_id=%s | notebook_refreshed=true | "
                "spark_staging_access=%s",
                job_id,
                spark_staging_access,
            )
            return {
                "job_id": job_id,
                "job_name": resolved_job_name,
                "notebook_path": notebook_path,
                "external_location": ext_result,
                "landing_cloud": resolved_landing_cloud,
                "spark_staging_access": spark_staging_access,
            }

        existing = _find_job_by_name(server_hostname, access_token, resolved_job_name)
        existing_id = int(existing["job_id"]) if existing else None
        spark_version = None
        if compute_mode == "classic":
            spark_version = _resolve_classic_spark_version(
                server_hostname,
                access_token,
                landing_cloud=resolved_landing_cloud,
                destination_connection=dest,
            )
        job_id = _create_or_update_job(
            server_hostname,
            access_token,
            job_name=resolved_job_name,
            notebook_path=notebook_path,
            compute_mode=compute_mode,
            landing_cloud=resolved_landing_cloud,
            existing_job_id=existing_id,
            spark_conf=scoped_spark_conf,
            data_security_mode=data_security_mode,
            node_type_id=node_type_id,
            spark_version=spark_version,
            single_node=single_node,
            existing_cluster_id=existing_cluster_id,
        )
        logger.warning(
            "DBX|spark_job | job_ready | job_id=%s | compute_mode=%s | landing_cloud=%s | "
            "spark_staging_access=%s | data_security_mode=%s | spark_version=%s | "
            "single_node=%s | existing_cluster_id=%s",
            job_id,
            compute_mode,
            resolved_landing_cloud,
            spark_staging_access,
            data_security_mode,
            spark_version,
            single_node,
            existing_cluster_id,
        )
    except DatabricksSparkJobScopedAccessError:
        raise
    except Exception as exc:
        msg = str(exc)
        if is_scoped_credential_workspace_blocker(msg) and (
            spark_staging_access == SPARK_STAGING_ACCESS_SCOPED_CREDENTIAL
        ):
            raise DatabricksSparkJobScopedAccessError(
                format_scoped_credential_blocker_message(msg)
            ) from exc
        if "403" in msg or "permission" in msg.lower() or "not authorized" in msg.lower():
            raise DatabricksSparkJobPermissionError(
                "Databricks token cannot manage Jobs/notebooks. Ask a workspace admin to "
                "grant Jobs run/create permissions or set destination_config.spark_job_id "
                f"to a pre-provisioned Job. Detail: {msg}"
            ) from exc
        raise DatabricksSparkJobError(f"Failed to ensure Dynamics Spark job: {msg}") from exc

    return {
        "job_id": job_id,
        "job_name": resolved_job_name,
        "notebook_path": notebook_path,
        "external_location": ext_result,
        "landing_cloud": resolved_landing_cloud,
        "spark_staging_access": spark_staging_access,
        "spark_compute_mode": compute_mode,
        "existing_cluster_id": existing_cluster_id,
    }


_SPARK_MERGE_ROW_COUNT_RE = re.compile(
    r"Merged staging rows=(\d+)",
    re.IGNORECASE,
)
_NO_FILES_MARKERS = frozenset(
    {
        "no_files",
        "no csv files",
        "no csv rows",
        "nothing to merge",
        "no rows with valid primary key",
    }
)


def _parse_spark_row_count_text(value: Any) -> int | None:
    """Parse notebook exit value or log fragment into a non-negative row count."""
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in _NO_FILES_MARKERS or any(marker in lowered for marker in _NO_FILES_MARKERS):
        return 0
    try:
        parsed = int(float(raw))
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def _extract_row_count_from_output(output: dict | None) -> int | None:
    """
    Parse merged row count from a jobs/runs/get-output payload.

    Serverless task runs often omit notebook_output.result; the notebook print
    ``Merged staging rows=N`` in logs is the reliable fallback.
    """
    if not output:
        return None

    nb = (output.get("notebook_output") or {})
    for candidate in (nb.get("result"), output.get("result")):
        parsed = _parse_spark_row_count_text(candidate)
        if parsed is not None:
            return parsed

    for part in _collect_run_output_parts(output):
        text = str(part or "")
        if not text.strip():
            continue
        lowered = text.lower()
        if any(marker in lowered for marker in _NO_FILES_MARKERS):
            return 0
        match = _SPARK_MERGE_ROW_COUNT_RE.search(text)
        if match:
            return int(match.group(1))

    return None


def extract_notebook_result_value(run: dict | None) -> int:
    """Parse integer row count from a completed notebook task run payload."""
    if not run:
        return 0

    for task in run.get("tasks") or []:
        notebook_out = (task or {}).get("notebook_output") or {}
        parsed = _parse_spark_row_count_text(notebook_out.get("result"))
        if parsed is not None and parsed > 0:
            return parsed

    top_nb = (run.get("notebook_output") or {})
    parsed = _parse_spark_row_count_text(top_nb.get("result"))
    if parsed is not None and parsed > 0:
        return parsed

    # Some compute modes omit notebook_output on runs/get — caller may pass
    # get-output payload via run["_output_result"].
    parsed = _parse_spark_row_count_text(run.get("_output_result"))
    if parsed is not None and parsed > 0:
        return parsed

    return 0


def _spark_run_output_run_ids(run: dict) -> list[int]:
    """Task run ids first (required for multi-task/serverless), then parent run id."""
    ordered: list[int] = []
    seen: set[int] = set()
    for task in run.get("tasks") or []:
        task_run_id = task.get("run_id")
        if not task_run_id:
            continue
        rid = int(task_run_id)
        if rid not in seen:
            seen.add(rid)
            ordered.append(rid)
    parent_run_id = run.get("run_id")
    if parent_run_id:
        rid = int(parent_run_id)
        if rid not in seen:
            ordered.append(rid)
    return ordered


def resolve_spark_run_row_count(
    server_hostname: str,
    access_token: str,
    run: dict,
) -> int:
    """
    Resolve merged row count after a successful Spark job run.

    Tries runs/get embedded output, then get-output for each task run id (preferred
    for serverless), then parent run id, parsing notebook exit value or log line.
    """
    embedded = extract_notebook_result_value(run)
    if embedded > 0:
        return embedded

    resolved: int | None = None
    for run_id in _spark_run_output_run_ids(run):
        try:
            output = _databricks_api_request(
                server_hostname,
                access_token,
                "GET",
                f"/api/2.1/jobs/runs/get-output?run_id={run_id}",
            )
            parsed = _extract_row_count_from_output(output)
            if parsed is None:
                continue
            if parsed > 0:
                logger.warning(
                    "DBX|spark_job | row_count_from_output | run_id=%s | row_count=%s",
                    run_id,
                    parsed,
                )
                return parsed
            resolved = 0
        except Exception as exc:
            logger.warning(
                "DBX|spark_job | row_count_output_fetch_failed | run_id=%s | err=%s",
                run_id,
                exc,
            )

    return resolved if resolved is not None else 0


def _collect_run_output_parts(output: dict) -> list[str]:
    parts: list[str] = []
    error = str(output.get("error") or "").strip()
    if error:
        parts.append(error)
    notebook_output = output.get("notebook_output") or {}
    nb_result = str(notebook_output.get("result") or "").strip()
    if nb_result:
        parts.append(nb_result)
    nb_error = str(notebook_output.get("error") or "").strip()
    if nb_error:
        parts.append(nb_error)
    logs = str(output.get("logs") or "").strip()
    if logs:
        parts.append(logs[-4000:])
    return parts


def _fetch_single_run_output(
    server_hostname: str,
    access_token: str,
    run_id: int,
) -> list[str]:
    output = _databricks_api_request(
        server_hostname,
        access_token,
        "GET",
        f"/api/2.1/jobs/runs/get-output?run_id={run_id}",
    )
    return _collect_run_output_parts(output)


def _fetch_run_failure_details(
    server_hostname: str,
    access_token: str,
    run_id: int,
    *,
    run: dict | None = None,
) -> str:
    """Best-effort fetch of notebook error / run output for failed Spark jobs."""
    parts: list[str] = []
    task_runs: list[tuple[str, int]] = []
    if run:
        for task in run.get("tasks") or []:
            task_run_id = task.get("run_id")
            if not task_run_id:
                continue
            task_key = str(task.get("task_key") or "task")
            task_runs.append((task_key, int(task_run_id)))

    try:
        if task_runs:
            for task_key, task_run_id in task_runs:
                try:
                    task_parts = _fetch_single_run_output(
                        server_hostname,
                        access_token,
                        task_run_id,
                    )
                    if task_parts:
                        parts.append(f"{task_key}: " + " | ".join(task_parts))
                except Exception as task_exc:
                    parts.append(f"{task_key}: (run output unavailable: {task_exc})")
        else:
            parts.extend(
                _fetch_single_run_output(server_hostname, access_token, run_id)
            )
    except Exception as exc:
        parts.append(f"(run output unavailable: {exc})")
    return " | ".join(parts) if parts else ""


def submit_and_wait(
    engine,
    job_id: int,
    notebook_params: dict[str, str],
    *,
    timeout_seconds: int | None = None,
    poll_seconds: int | None = None,
    on_poll: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Submit a Databricks Job run and block until terminal state."""
    server_hostname, access_token = _engine_creds(engine)
    timeout = timeout_seconds or DEFAULT_JOB_TIMEOUT_SECONDS
    poll = poll_seconds or DEFAULT_POLL_SECONDS

    run_resp = _databricks_api_request(
        server_hostname,
        access_token,
        "POST",
        "/api/2.1/jobs/run-now",
        {
            "job_id": int(job_id),
            "notebook_params": {str(k): str(v) for k, v in notebook_params.items()},
        },
    )
    run_id = int(run_resp["run_id"])
    run_url = f"https://{server_hostname}/#job/{job_id}/run/{run_id}"
    logger.warning(
        "DBX|spark_job | run_submitted | job_id=%s | run_id=%s | url=%s",
        job_id,
        run_id,
        run_url,
    )

    deadline = time.time() + timeout
    terminal_lifecycle = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}
    terminal_results = {"SUCCESS", "FAILED", "CANCELED", "TIMEDOUT"}
    last_state = ""
    while time.time() < deadline:
        if on_poll is not None:
            try:
                on_poll()
            except Exception as exc:
                logger.debug("DBX|spark_job | on_poll_failed | err=%s", exc)
        run = _databricks_api_request(
            server_hostname,
            access_token,
            "GET",
            f"/api/2.1/jobs/runs/get?run_id={run_id}",
        )
        lifecycle = (run.get("state") or {}).get("life_cycle_state") or ""
        result_state = (run.get("state") or {}).get("result_state") or ""
        last_state = f"{lifecycle}/{result_state}".strip("/")
        if lifecycle in terminal_lifecycle or result_state in terminal_results:
            if result_state == "SUCCESS":
                try:
                    row_count = resolve_spark_run_row_count(
                        server_hostname,
                        access_token,
                        run,
                    )
                except Exception as exc:
                    logger.warning(
                        "DBX|spark_job | row_count_fetch_failed | run_id=%s | err=%s",
                        run_id,
                        exc,
                    )
                    row_count = extract_notebook_result_value(run)
                logger.warning(
                    "DBX|spark_job | run_success | run_id=%s | duration_ms=%s | row_count=%s",
                    run_id,
                    run.get("run_duration"),
                    row_count,
                )
                return {
                    "run_id": run_id,
                    "run_url": run_url,
                    "state": result_state or lifecycle,
                    "run": run,
                    "row_count": row_count,
                }
            message = (run.get("state") or {}).get("state_message") or result_state or lifecycle
            details = _fetch_run_failure_details(
                server_hostname,
                access_token,
                run_id,
                run=run,
            )
            if details:
                message = f"{message} | {details}"
            raise DatabricksSparkJobError(
                f"Databricks Spark job failed: {message}",
                run_id=run_id,
                run_url=run_url,
            )
        time.sleep(poll)

    raise DatabricksSparkJobError(
        f"Databricks Spark job timed out after {timeout}s (last_state={last_state})",
        run_id=run_id,
        run_url=run_url,
    )
