"""
Short-lived, read-only credentials for Datasyncher Spark staging when Unity Catalog
external locations cannot be created in the customer workspace.

Credentials are minted from platform cloud identities, stored in a Databricks secret
scope in the customer workspace, and referenced from classic job-cluster ``spark_conf``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

DATASYNCHER_STAGING_SECRET_SCOPE = "datasyncher_staging"
DEFAULT_SCOPED_CREDENTIAL_TTL_SECONDS = int(
    os.environ.get("DYNAMICS_DBX_SCOPED_CRED_TTL_SECONDS", "10800")
)


class ScopedCredentialError(Exception):
    """Raised when minting or delivering scoped staging credentials fails."""


def secret_key_for_connection(connection_id: int, suffix: str = "") -> str:
    base = f"conn_{int(connection_id)}"
    return f"{base}_{suffix}" if suffix else base


def _secret_ref(scope: str, key: str) -> str:
    return f"{{{{secrets/{scope}/{key}}}}}"


def ensure_datasyncher_secret_scope(
    server_hostname: str,
    access_token: str,
    *,
    scope_name: str = DATASYNCHER_STAGING_SECRET_SCOPE,
) -> None:
    """Create the Datasyncher staging secret scope if missing (idempotent)."""
    from services.databricks.load_helpers import _databricks_api_request

    try:
        _databricks_api_request(
            server_hostname,
            access_token,
            "POST",
            "/api/2.0/secrets/scopes/create",
            {"scope": scope_name, "initial_manage_principal": "users"},
        )
        logger.warning(
            "DBX|scoped_cred | secret_scope_created | scope=%s",
            scope_name,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "already exists" in msg or "resource_already_exists" in msg:
            return
        if "403" in msg or "permission" in msg or "not authorized" in msg:
            raise ScopedCredentialError(
                "Databricks token cannot create secret scopes. Ask a workspace admin to "
                "allow secret scope create, or grant the token CAN_MANAGE on an existing "
                f"scope named {scope_name!r}. Detail: {exc}"
            ) from exc
        raise ScopedCredentialError(
            f"Failed to ensure Databricks secret scope {scope_name!r}: {exc}"
        ) from exc


def put_secret(
    server_hostname: str,
    access_token: str,
    *,
    scope_name: str,
    key: str,
    value: str,
) -> None:
    from services.databricks.load_helpers import _databricks_api_request

    try:
        _databricks_api_request(
            server_hostname,
            access_token,
            "POST",
            "/api/2.0/secrets/put",
            {
                "scope": scope_name,
                "key": key,
                "string_value": value,
            },
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "403" in msg or "permission" in msg or "not authorized" in msg:
            raise ScopedCredentialError(
                "Databricks token cannot write secret scope "
                f"{scope_name!r}. Ask a workspace admin to grant CAN_MANAGE on that scope. "
                f"Detail: {exc}"
            ) from exc
        raise ScopedCredentialError(
            f"Failed to put Databricks secret {scope_name}/{key}: {exc}"
        ) from exc


def delete_secret(
    server_hostname: str,
    access_token: str,
    *,
    scope_name: str,
    key: str,
) -> None:
    from services.databricks.load_helpers import _databricks_api_request

    try:
        _databricks_api_request(
            server_hostname,
            access_token,
            "POST",
            "/api/2.0/secrets/delete",
            {"scope": scope_name, "key": key},
        )
    except Exception as exc:
        logger.warning(
            "DBX|scoped_cred | secret_delete_soft_fail | scope=%s | key=%s | err=%s",
            scope_name,
            key,
            str(exc)[:200],
        )


def mint_azure_user_delegation_sas(
    *,
    connection_id: int,
    destination_connection: dict | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, str]:
    """
    Mint a user-delegation SAS limited to ``dynamics_fo/<connection_id>/`` on the
    platform Azure staging container.
    """
    try:
        from azure.identity import ClientSecretCredential
        from azure.storage.blob import (
            BlobServiceClient,
            ContainerSasPermissions,
            generate_container_sas,
        )
    except ImportError as exc:
        raise ScopedCredentialError(
            f"Azure SDK packages required for scoped staging SAS: {exc}"
        ) from exc

    from services.databricks.load_helpers import resolve_platform_azure_staging_sp
    from utils.azure_path_utils import join_azure_blob_path, parse_abfss_uri

    dest = destination_connection if isinstance(destination_connection, dict) else {}
    staging_url = str(
        dest.get("adls_staging_url")
        or os.environ.get("DATASYNCHER_AZURE_STAGING_URL")
        or ""
    ).strip()
    if not staging_url:
        raise ScopedCredentialError(
            "DATASYNCHER_AZURE_STAGING_URL (or adls_staging_url) is required to mint "
            "Azure scoped staging credentials"
        )

    sp = resolve_platform_azure_staging_sp(dest)
    if not sp:
        raise ScopedCredentialError(
            "Platform Azure SP (DATABRICKS_AZURE_STAGING_SP_*) is required to mint "
            "user-delegation SAS for scoped Spark staging"
        )

    loc = parse_abfss_uri(staging_url)
    directory_name = join_azure_blob_path(
        loc.blob_prefix, f"dynamics_fo/{int(connection_id)}"
    )
    ttl = max(600, int(ttl_seconds or DEFAULT_SCOPED_CREDENTIAL_TTL_SECONDS))
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    credential = ClientSecretCredential(
        tenant_id=sp["directory_id"],
        client_id=sp["application_id"],
        client_secret=sp["client_secret"],
    )
    account_url = f"https://{loc.account_name}.blob.core.windows.net"
    blob_service = BlobServiceClient(account_url, credential=credential)
    try:
        user_delegation_key = blob_service.get_user_delegation_key(
            key_start_time=start,
            key_expiry_time=expiry,
        )
    except Exception as exc:
        raise ScopedCredentialError(
            "Failed to get Azure user-delegation key for staging SAS. Ensure the "
            "platform SP has the Storage Blob Delegator role on the staging account. "
            f"Detail: {exc}"
        ) from exc

    sas = None
    try:
        from azure.storage.filedatalake import (
            DirectorySasPermissions,
            generate_directory_sas,
        )

        sas = generate_directory_sas(
            account_name=loc.account_name,
            file_system_name=loc.container_name,
            directory_name=directory_name,
            credential=user_delegation_key,
            permission=DirectorySasPermissions(read=True, list=True),
            expiry=expiry,
            start=start,
        )
    except Exception as dir_exc:
        logger.warning(
            "DBX|scoped_cred | azure_directory_sas_unavailable | connection_id=%s | err=%s",
            connection_id,
            str(dir_exc)[:200],
        )
        # Fallback for non-HNS or older SDK paths: container SAS (still short-lived).
        sas = generate_container_sas(
            account_name=loc.account_name,
            container_name=loc.container_name,
            user_delegation_key=user_delegation_key,
            permission=ContainerSasPermissions(read=True, list=True),
            expiry=expiry,
            start=start,
        )
        logger.warning(
            "DBX|scoped_cred | azure_directory_sas_fallback_container | connection_id=%s",
            connection_id,
        )

    sas_token = str(sas).lstrip("?")
    logger.warning(
        "DBX|scoped_cred | azure_sas_minted | connection_id=%s | account=%s | "
        "container=%s | directory=%s | ttl_s=%s",
        connection_id,
        loc.account_name,
        loc.container_name,
        directory_name,
        ttl,
    )
    return {
        "cloud": "azure",
        "account_name": loc.account_name,
        "container_name": loc.container_name,
        "directory_name": directory_name,
        "sas_token": sas_token,
        "ttl_seconds": str(ttl),
    }


def mint_s3_temporary_credentials(
    *,
    connection_id: int,
    destination_connection: dict | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, str]:
    """Mint STS session credentials limited to the connection staging prefix."""
    import boto3

    from services.databricks.load_helpers import resolve_databricks_s3_settings

    settings = resolve_databricks_s3_settings(destination_connection)
    bucket = str(settings.get("bucket_name") or "").strip()
    if not bucket:
        raise ScopedCredentialError(
            "S3_BUCKET_NAME / s3_bucket_name is required to mint S3 scoped staging credentials"
        )

    prefix = f"dynamics_fo/{int(connection_id)}/"
    ttl = max(900, min(int(ttl_seconds or DEFAULT_SCOPED_CREDENTIAL_TTL_SECONDS), 43200))
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": [f"arn:aws:s3:::{bucket}/{prefix}*"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
                "Condition": {
                    "StringLike": {"s3:prefix": [prefix, f"{prefix}*"]}
                },
            },
        ],
    }
    policy_json = json.dumps(policy)
    region = (
        str(os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
        .strip()
    )
    sts = boto3.client("sts", region_name=region)
    role_arn = str(
        os.environ.get("DATABRICKS_S3_SCOPED_ASSUME_ROLE_ARN")
        or os.environ.get("DATABRICKS_S3_IAM_ROLE_ARN")
        or ""
    ).strip()
    session_name = f"datasyncher-spark-{int(connection_id)}"[:64]

    try:
        if role_arn:
            resp = sts.assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                Policy=policy_json,
                DurationSeconds=ttl,
            )
        else:
            resp = sts.get_federation_token(
                Name=session_name[:32],
                Policy=policy_json,
                DurationSeconds=ttl,
            )
    except Exception as exc:
        raise ScopedCredentialError(
            "Failed to mint temporary S3 credentials for scoped Spark staging. "
            "Configure worker AWS credentials and optionally "
            "DATABRICKS_S3_SCOPED_ASSUME_ROLE_ARN. "
            f"Detail: {exc}"
        ) from exc

    creds = resp.get("Credentials") or {}
    access_key = str(creds.get("AccessKeyId") or "").strip()
    secret_key = str(creds.get("SecretAccessKey") or "").strip()
    session_token = str(creds.get("SessionToken") or "").strip()
    if not (access_key and secret_key and session_token):
        raise ScopedCredentialError("STS response missing temporary credentials")

    logger.warning(
        "DBX|scoped_cred | s3_sts_minted | connection_id=%s | bucket=%s | prefix=%s | ttl_s=%s",
        connection_id,
        bucket,
        prefix,
        ttl,
    )
    return {
        "cloud": "s3",
        "bucket_name": bucket,
        "prefix": prefix,
        "access_key_id": access_key,
        "secret_access_key": secret_key,
        "session_token": session_token,
        "ttl_seconds": str(ttl),
    }


def mint_gcs_scoped_credentials(
    *,
    connection_id: int,
    destination_connection: dict | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, str]:
    """
    Prepare GCS credentials for classic Spark ``spark_conf``.

    Prefers a short-lived downscoped access token when google-auth supports it;
    otherwise falls back to the platform SA private-key fields (bucket already
    limited to Datasyncher staging).
    """
    del destination_connection  # reserved for future destination overrides
    raw = str(os.environ.get("DATABRICKS_GCS_SA_JSON") or "").strip()
    if not raw:
        raise ScopedCredentialError(
            "DATABRICKS_GCS_SA_JSON is required to mint GCS scoped staging credentials"
        )

    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        try:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception as exc:
            raise ScopedCredentialError(
                f"DATABRICKS_GCS_SA_JSON is not valid JSON: {exc}"
            ) from exc

    email = str(info.get("client_email") or "").strip()
    private_key = str(info.get("private_key") or "").strip()
    private_key_id = str(info.get("private_key_id") or "").strip()
    if not (email and private_key and private_key_id):
        raise ScopedCredentialError(
            "DATABRICKS_GCS_SA_JSON missing client_email / private_key / private_key_id"
        )

    bucket = str(os.environ.get("GCS_BUCKET_NAME") or "").strip()
    if not bucket:
        bucket_url = str(os.environ.get("GCS_BUCKET_URL") or "").strip()
        if bucket_url.startswith("gs://"):
            bucket = bucket_url[len("gs://") :].split("/", 1)[0]
        elif bucket_url.startswith("gcs://"):
            bucket = bucket_url[len("gcs://") :].split("/", 1)[0]
    if not bucket:
        raise ScopedCredentialError("GCS_BUCKET_NAME is required for GCS scoped staging")

    prefix = f"dynamics_fo/{int(connection_id)}/"
    ttl = max(600, int(ttl_seconds or DEFAULT_SCOPED_CREDENTIAL_TTL_SECONDS))
    access_token = ""
    try:
        from google.auth import downscoped
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        source = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
        )
        rule = downscoped.AccessBoundaryRule(
            available_resource=f"//storage.googleapis.com/projects/_/buckets/{bucket}",
            available_permissions=["inRole:roles/storage.objectViewer"],
            availability_condition=downscoped.AvailabilityCondition(
                expression=(
                    f"resource.name.startsWith("
                    f"'projects/_/buckets/{bucket}/objects/{prefix}')"
                ),
            ),
        )
        cab = downscoped.Credentials(
            source_credentials=source,
            rules=(rule,),
        )
        cab.refresh(Request())
        access_token = str(getattr(cab, "token", "") or "").strip()
    except Exception as exc:
        logger.warning(
            "DBX|scoped_cred | gcs_downscope_unavailable | connection_id=%s | err=%s",
            connection_id,
            str(exc)[:300],
        )

    logger.warning(
        "DBX|scoped_cred | gcs_cred_prepared | connection_id=%s | bucket=%s | "
        "prefix=%s | mode=%s",
        connection_id,
        bucket,
        prefix,
        "access_token" if access_token else "service_account_key",
    )
    result = {
        "cloud": "gcs",
        "bucket_name": bucket,
        "prefix": prefix,
        "client_email": email,
        "private_key_id": private_key_id,
        "private_key": private_key,
        "ttl_seconds": str(ttl),
    }
    if access_token:
        result["access_token"] = access_token
    return result


def mint_scoped_staging_credentials(
    landing_cloud: str,
    *,
    connection_id: int,
    destination_connection: dict | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, str]:
    cloud = str(landing_cloud or "").strip().lower()
    if cloud == "azure":
        return mint_azure_user_delegation_sas(
            connection_id=connection_id,
            destination_connection=destination_connection,
            ttl_seconds=ttl_seconds,
        )
    if cloud == "s3":
        return mint_s3_temporary_credentials(
            connection_id=connection_id,
            destination_connection=destination_connection,
            ttl_seconds=ttl_seconds,
        )
    if cloud == "gcs":
        return mint_gcs_scoped_credentials(
            connection_id=connection_id,
            destination_connection=destination_connection,
            ttl_seconds=ttl_seconds,
        )
    raise ScopedCredentialError(f"Unsupported landing_cloud for scoped credentials: {cloud!r}")


def deliver_scoped_credentials_to_workspace(
    server_hostname: str,
    access_token: str,
    *,
    connection_id: int,
    minted: dict[str, str],
    scope_name: str = DATASYNCHER_STAGING_SECRET_SCOPE,
) -> dict[str, str]:
    """
    Put minted credential material into the Datasyncher secret scope.

    Returns a map of logical names → secret keys written.
    """
    ensure_datasyncher_secret_scope(
        server_hostname, access_token, scope_name=scope_name
    )
    cloud = str(minted.get("cloud") or "").strip().lower()
    keys: dict[str, str] = {}

    if cloud == "azure":
        key = secret_key_for_connection(connection_id)
        put_secret(
            server_hostname,
            access_token,
            scope_name=scope_name,
            key=key,
            value=str(minted.get("sas_token") or ""),
        )
        keys["sas_token"] = key
    elif cloud == "s3":
        for suffix, field in (
            ("ak", "access_key_id"),
            ("sk", "secret_access_key"),
            ("token", "session_token"),
        ):
            key = secret_key_for_connection(connection_id, suffix)
            put_secret(
                server_hostname,
                access_token,
                scope_name=scope_name,
                key=key,
                value=str(minted.get(field) or ""),
            )
            keys[field] = key
    elif cloud == "gcs":
        if minted.get("access_token"):
            key = secret_key_for_connection(connection_id, "token")
            put_secret(
                server_hostname,
                access_token,
                scope_name=scope_name,
                key=key,
                value=str(minted["access_token"]),
            )
            keys["access_token"] = key
        for suffix, field in (
            ("email", "client_email"),
            ("key_id", "private_key_id"),
            ("pkey", "private_key"),
        ):
            key = secret_key_for_connection(connection_id, suffix)
            put_secret(
                server_hostname,
                access_token,
                scope_name=scope_name,
                key=key,
                value=str(minted.get(field) or ""),
            )
            keys[field] = key
    else:
        raise ScopedCredentialError(f"Unknown minted cloud={cloud!r}")

    logger.warning(
        "DBX|scoped_cred | secrets_delivered | connection_id=%s | cloud=%s | keys=%s",
        connection_id,
        cloud,
        sorted(keys.values()),
    )
    return keys


def cleanup_scoped_credentials_from_workspace(
    server_hostname: str,
    access_token: str,
    *,
    connection_id: int,
    cloud: str,
    scope_name: str = DATASYNCHER_STAGING_SECRET_SCOPE,
) -> None:
    """Best-effort delete of connection secret keys after the Spark job finishes."""
    suffixes: tuple[str, ...]
    normalized = str(cloud or "").strip().lower()
    if normalized == "azure":
        suffixes = ("",)
    elif normalized == "s3":
        suffixes = ("ak", "sk", "token")
    else:
        suffixes = ("token", "email", "key_id", "pkey")
    for suffix in suffixes:
        delete_secret(
            server_hostname,
            access_token,
            scope_name=scope_name,
            key=secret_key_for_connection(connection_id, suffix),
        )


def build_scoped_credential_spark_conf(
    landing_cloud: str,
    *,
    connection_id: int,
    minted: dict[str, str],
    secret_keys: dict[str, str],
    scope_name: str = DATASYNCHER_STAGING_SECRET_SCOPE,
) -> dict[str, str]:
    """Build classic-cluster ``spark_conf`` that references secret scope values."""
    cloud = str(landing_cloud or "").strip().lower()
    conf: dict[str, str] = {
        "spark.sql.files.ignoreMissingFiles": "true",
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.adaptive.coalescePartitions.enabled": "true",
        "spark.sql.adaptive.skewJoin.enabled": "true",
        "spark.sql.adaptive.localShuffleReader.enabled": "true",
        "spark.sql.files.maxPartitionBytes": "134217728",
        "spark.sql.files.openCostInBytes": "4194304",
    }

    if cloud == "azure":
        account = str(minted.get("account_name") or "").strip()
        container = str(minted.get("container_name") or "").strip()
        if not account or not container:
            raise ScopedCredentialError("Azure minted credentials missing account/container")
        sas_key = secret_keys.get("sas_token") or secret_key_for_connection(connection_id)
        host = f"{account}.dfs.core.windows.net"
        conf[f"fs.azure.account.auth.type.{host}"] = "SAS"
        conf[f"fs.azure.sas.token.provider.type.{host}"] = (
            "org.apache.hadoop.fs.azurebfs.sas.FixedSASTokenProvider"
        )
        conf[f"fs.azure.sas.fixed.token.{container}.{host}"] = _secret_ref(scope_name, sas_key)
        return conf

    if cloud == "s3":
        bucket = str(minted.get("bucket_name") or "").strip()
        if not bucket:
            raise ScopedCredentialError("S3 minted credentials missing bucket_name")
        ak = secret_keys.get("access_key_id") or secret_key_for_connection(connection_id, "ak")
        sk = secret_keys.get("secret_access_key") or secret_key_for_connection(
            connection_id, "sk"
        )
        tok = secret_keys.get("session_token") or secret_key_for_connection(
            connection_id, "token"
        )
        conf["fs.s3a.aws.credentials.provider"] = (
            "org.apache.hadoop.fs.s3a.TemporaryAWSCredentialsProvider"
        )
        conf[f"fs.s3a.bucket.{bucket}.aws.credentials.provider"] = (
            "org.apache.hadoop.fs.s3a.TemporaryAWSCredentialsProvider"
        )
        conf["fs.s3a.access.key"] = _secret_ref(scope_name, ak)
        conf["fs.s3a.secret.key"] = _secret_ref(scope_name, sk)
        conf["fs.s3a.session.token"] = _secret_ref(scope_name, tok)
        conf[f"fs.s3a.bucket.{bucket}.access.key"] = _secret_ref(scope_name, ak)
        conf[f"fs.s3a.bucket.{bucket}.secret.key"] = _secret_ref(scope_name, sk)
        conf[f"fs.s3a.bucket.{bucket}.session.token"] = _secret_ref(scope_name, tok)
        return conf

    if cloud == "gcs":
        email_key = secret_keys.get("client_email") or secret_key_for_connection(
            connection_id, "email"
        )
        key_id_key = secret_keys.get("private_key_id") or secret_key_for_connection(
            connection_id, "key_id"
        )
        pkey_key = secret_keys.get("private_key") or secret_key_for_connection(
            connection_id, "pkey"
        )
        conf["fs.gs.auth.service.account.enable"] = "true"
        conf["fs.gs.auth.service.account.email"] = _secret_ref(scope_name, email_key)
        conf["fs.gs.auth.service.account.private.key.id"] = _secret_ref(
            scope_name, key_id_key
        )
        conf["fs.gs.auth.service.account.private.key"] = _secret_ref(scope_name, pkey_key)
        # Also set google.cloud.* aliases used by some Databricks runtimes.
        conf["google.cloud.auth.service.account.enable"] = "true"
        conf["fs.gs.auth.service.account.email"] = conf["fs.gs.auth.service.account.email"]
        return conf

    raise ScopedCredentialError(f"Unsupported landing_cloud for spark_conf: {cloud!r}")


_NODE_TYPE_BY_CLOUD = {
    # Prefer a small general-purpose SKU; scoped jobs default to single-node.
    "azure": os.environ.get("DYNAMICS_DBX_SPARK_NODE_TYPE_AZURE", "Standard_DS3_v2"),
    "s3": os.environ.get("DYNAMICS_DBX_SPARK_NODE_TYPE_AWS", "m5.xlarge"),
    "gcs": os.environ.get("DYNAMICS_DBX_SPARK_NODE_TYPE_GCP", "n2-standard-4"),
}


def scoped_job_use_single_node() -> bool:
    """Scoped-credential classic clusters default to one VM to avoid Azure quota pressure."""
    raw = str(os.environ.get("DYNAMICS_DBX_SCOPED_SINGLE_NODE") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def default_node_type_for_landing_cloud(landing_cloud: str) -> str:
    cloud = str(landing_cloud or "").strip().lower()
    explicit = str(os.environ.get("DYNAMICS_DBX_SPARK_NODE_TYPE") or "").strip()
    if explicit:
        return explicit
    return _NODE_TYPE_BY_CLOUD.get(cloud, "n2-standard-4")


def is_scoped_credential_workspace_blocker(exc_or_message: Any) -> bool:
    """True when the workspace blocks classic clusters or secret scopes."""
    text = str(exc_or_message or "").lower()
    markers = (
        "secret scope",
        "cannot create secret",
        "can_manage",
        "cluster create",
        "cannot create cluster",
        "job cluster",
        "data_security_mode",
        "single_user",
        "serverless-only",
        "serverless only",
        "classic compute",
        "not authorized to create",
        "max number of clusters",
        "cluster policy",
    )
    return any(m in text for m in markers)


def format_scoped_credential_blocker_message(exc_or_message: Any) -> str:
    detail = str(exc_or_message or "").strip()
    return (
        "Databricks workspace blocked the zero-manual Spark staging fallback "
        "(scoped credential on a dedicated job cluster). Allow the destination token to "
        "create job clusters (classic / single-user) and create or manage the "
        f"{DATASYNCHER_STAGING_SECRET_SCOPE!r} secret scope. "
        "Serverless-only workspaces still need a Unity Catalog external location. "
        f"Detail: {detail[:800]}"
    )


_SAFE_SECRET_KEY_RE = re.compile(r"^conn_\d+(?:_[a-z0-9]+)?$")


def assert_safe_secret_key(key: str) -> str:
    normalized = str(key or "").strip()
    if not _SAFE_SECRET_KEY_RE.match(normalized):
        raise ScopedCredentialError(f"Refusing unsafe Databricks secret key: {key!r}")
    return normalized
