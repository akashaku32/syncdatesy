"""AWS IAM helpers for Databricks Unity Catalog S3 storage credential auto-provisioning."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_UC_MASTER_ROLE_ARN = (
    "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
)
DATASYNCHER_S3_READ_POLICY_NAME = "datasyncher-databricks-s3-read"


def _iam_client():
    import boto3

    kwargs: dict[str, str] = {}
    region = str(
        os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    ).strip()
    if region:
        kwargs["region_name"] = region
    return boto3.client("iam", **kwargs)


def parse_iam_role_arn(role_arn: str) -> tuple[str, str]:
    """Return ``(role_name, account_id)`` from an IAM role ARN."""
    raw = str(role_arn or "").strip()
    marker = ":role/"
    if marker not in raw:
        raise ValueError(f"Invalid IAM role ARN: {role_arn}")
    prefix, role_name = raw.split(marker, 1)
    account_id = prefix.rsplit(":", 1)[-1]
    if not account_id or not role_name:
        raise ValueError(f"Invalid IAM role ARN: {role_arn}")
    return role_name, account_id


def resolve_uc_master_role_arn(unity_catalog_iam_arn: str = "") -> str:
    """Return the Databricks UC master role ARN used in IAM trust policies."""
    configured = str(
        os.environ.get("DATABRICKS_S3_UC_MASTER_ROLE_ARN", DEFAULT_UC_MASTER_ROLE_ARN)
    ).strip()
    return configured or DEFAULT_UC_MASTER_ROLE_ARN


def normalize_external_ids(*values: Any) -> list[str]:
    """Return a stable, de-duplicated list of external IDs."""
    ids: list[str] = []
    for val in values:
        if val is None:
            continue
        if isinstance(val, (list, tuple, set)):
            for item in val:
                s = str(item or "").strip()
                if s and s not in ids:
                    ids.append(s)
            continue
        s = str(val or "").strip()
        if s and s not in ids:
            ids.append(s)
    return ids


def extract_external_ids_from_trust_policy(policy: dict[str, Any]) -> list[str]:
    """Read any ``sts:ExternalId`` values already present on the IAM role trust policy."""
    ids: list[str] = []
    for stmt in policy.get("Statement") or []:
        if not isinstance(stmt, dict) or stmt.get("Effect") != "Allow":
            continue
        condition = stmt.get("Condition") or {}
        if not isinstance(condition, dict):
            continue
        for operator in ("StringEquals", "StringLike"):
            block = condition.get(operator) or {}
            if not isinstance(block, dict):
                continue
            external_id = block.get("sts:ExternalId") or block.get("sts:externalid")
            ids.extend(normalize_external_ids(external_id))
    return normalize_external_ids(ids)


def build_databricks_trust_policy(
    role_arn: str,
    external_id: str | list[str],
    *,
    uc_master_role_arn: str | None = None,
) -> dict[str, Any]:
    """Build a self-assuming trust policy for one or more Databricks UC external IDs."""
    uc_master = str(uc_master_role_arn or resolve_uc_master_role_arn()).strip()
    principals = list(dict.fromkeys(p for p in (uc_master, role_arn) if p))
    external_ids = normalize_external_ids(external_id)
    if not external_ids:
        raise ValueError("At least one external_id is required for the trust policy")
    external_id_value: str | list[str] = (
        external_ids[0] if len(external_ids) == 1 else external_ids
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": principals},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "sts:ExternalId": external_id_value,
                    }
                },
            }
        ],
    }


def build_s3_read_policy_document(
    bucket_name: str,
    *,
    key_prefix: str = "",
    role_arn: str | None = None,
) -> dict[str, Any]:
    """Build the inline IAM policy document for Databricks read access to staging S3."""
    bucket_arn = f"arn:aws:s3:::{bucket_name}"
    prefix = str(key_prefix or "").strip().strip("/")
    object_arn = f"{bucket_arn}/{prefix}/*" if prefix else f"{bucket_arn}/*"

    list_statement: dict[str, Any] = {
        "Effect": "Allow",
        "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
        "Resource": bucket_arn,
    }
    if prefix:
        list_statement["Condition"] = {
            "StringLike": {
                "s3:prefix": [f"{prefix}/*", prefix],
            }
        }

    statements: list[dict[str, Any]] = [
        list_statement,
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:GetObjectVersion"],
            "Resource": object_arn,
        },
    ]
    if role_arn:
        statements.append(
            {
                "Effect": "Allow",
                "Action": ["sts:AssumeRole"],
                "Resource": [role_arn],
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def _trust_policy_matches(
    existing: dict[str, Any],
    desired: dict[str, Any],
) -> bool:
    return json.dumps(existing, sort_keys=True) == json.dumps(desired, sort_keys=True)


def _policy_document_matches(
    existing: dict[str, Any],
    desired: dict[str, Any],
) -> bool:
    return json.dumps(existing, sort_keys=True) == json.dumps(desired, sort_keys=True)


def ensure_iam_role_for_databricks_s3(
    role_arn: str,
    external_id: str,
    bucket_name: str,
    *,
    key_prefix: str = "",
    grant_iam: bool = True,
) -> dict[str, Any]:
    """
    Idempotently configure IAM trust + S3 read policy for a Databricks UC credential.

    Trust policies merge external IDs instead of replacing them so multiple Databricks
    workspaces can share the same platform IAM role.
    """
    result: dict[str, Any] = {
        "trust_updated": False,
        "policy_updated": False,
        "iam_granted": False,
        "external_ids": [],
        "skipped_reason": None,
    }
    if not grant_iam:
        result["skipped_reason"] = "grant_iam_disabled"
        return result

    role_arn = str(role_arn or "").strip()
    external_id = str(external_id or "").strip()
    bucket_name = str(bucket_name or "").strip()
    if not role_arn or not external_id or not bucket_name:
        result["skipped_reason"] = "missing_role_arn_external_id_or_bucket"
        return result

    try:
        role_name, _account_id = parse_iam_role_arn(role_arn)
    except ValueError as exc:
        result["skipped_reason"] = str(exc)
        return result

    permissions_policy = build_s3_read_policy_document(
        bucket_name,
        key_prefix=key_prefix,
        role_arn=role_arn,
    )

    try:
        iam = _iam_client()
        role = iam.get_role(RoleName=role_name)
        existing_trust = role["Role"]["AssumeRolePolicyDocument"]
        if isinstance(existing_trust, str):
            existing_trust = json.loads(existing_trust)

        merged_external_ids = normalize_external_ids(
            extract_external_ids_from_trust_policy(existing_trust),
            external_id,
        )
        result["external_ids"] = merged_external_ids
        trust_policy = build_databricks_trust_policy(role_arn, merged_external_ids)

        if not _trust_policy_matches(existing_trust, trust_policy):
            iam.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(trust_policy),
            )
            result["trust_updated"] = True
            logger.warning(
                "DBX|ensure_s3_iam | updated trust policy | role=%s | external_ids=%s",
                role_name,
                merged_external_ids,
            )
        else:
            logger.warning(
                "DBX|ensure_s3_iam | trust policy already current | role=%s | external_ids=%s",
                role_name,
                merged_external_ids,
            )

        try:
            existing_policy = iam.get_role_policy(
                RoleName=role_name,
                PolicyName=DATASYNCHER_S3_READ_POLICY_NAME,
            )
            existing_doc = existing_policy["PolicyDocument"]
            if isinstance(existing_doc, str):
                existing_doc = json.loads(existing_doc)
            policy_matches = _policy_document_matches(existing_doc, permissions_policy)
        except iam.exceptions.NoSuchEntityException:
            policy_matches = False

        if not policy_matches:
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName=DATASYNCHER_S3_READ_POLICY_NAME,
                PolicyDocument=json.dumps(permissions_policy),
            )
            result["policy_updated"] = True
            logger.warning(
                "DBX|ensure_s3_iam | updated inline S3 read policy | role=%s | bucket=%s | prefix=%s",
                role_name,
                bucket_name,
                key_prefix or "*",
            )
        else:
            logger.warning(
                "DBX|ensure_s3_iam | inline S3 read policy already current | role=%s",
                role_name,
            )

        result["iam_granted"] = True
        return result
    except Exception as exc:
        logger.error(
            "DBX|ensure_s3_iam | IAM update failed | role=%s | err=%s",
            role_name,
            exc,
        )
        result["skipped_reason"] = str(exc)
        return result
