import json
from unittest.mock import MagicMock

from utils.aws_iam_utils import (
    DATASYNCHER_S3_READ_POLICY_NAME,
    build_databricks_trust_policy,
    build_s3_read_policy_document,
    ensure_iam_role_for_databricks_s3,
    extract_external_ids_from_trust_policy,
    parse_iam_role_arn,
)


def test_parse_iam_role_arn():
    role_arn = "arn:aws:iam::234882402654:role/databricks-uc-s3-read"
    assert parse_iam_role_arn(role_arn) == ("databricks-uc-s3-read", "234882402654")


def test_build_databricks_trust_policy_includes_self_assume():
    role_arn = "arn:aws:iam::234882402654:role/databricks-uc-s3-read"
    policy = build_databricks_trust_policy(role_arn, "ext-123")
    principals = policy["Statement"][0]["Principal"]["AWS"]
    assert role_arn in principals
    assert policy["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == "ext-123"


def test_build_databricks_trust_policy_supports_multiple_external_ids():
    role_arn = "arn:aws:iam::234882402654:role/databricks-uc-s3-read"
    policy = build_databricks_trust_policy(role_arn, ["ext-a", "ext-b"])
    assert policy["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == [
        "ext-a",
        "ext-b",
    ]


def test_extract_external_ids_from_trust_policy():
    policy = build_databricks_trust_policy(
        "arn:aws:iam::234882402654:role/databricks-uc-s3-read",
        ["ext-a", "ext-b"],
    )
    assert extract_external_ids_from_trust_policy(policy) == ["ext-a", "ext-b"]


def test_ensure_iam_role_merges_existing_external_ids(monkeypatch):
    iam = MagicMock()
    role_arn = "arn:aws:iam::234882402654:role/databricks-uc-s3-read"
    existing_trust = build_databricks_trust_policy(role_arn, "ext-existing")
    iam.get_role.return_value = {
        "Role": {"AssumeRolePolicyDocument": json.dumps(existing_trust)}
    }

    class _IamExceptions:
        NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})

    iam.exceptions = _IamExceptions()
    iam.get_role_policy.side_effect = _IamExceptions.NoSuchEntityException()
    monkeypatch.setattr("utils.aws_iam_utils._iam_client", lambda: iam)

    result = ensure_iam_role_for_databricks_s3(
        role_arn,
        "ext-new",
        "datasyncher-stg",
        key_prefix="dynamics_fo",
    )

    assert result["iam_granted"] is True
    assert result["trust_updated"] is True
    assert result["external_ids"] == ["ext-existing", "ext-new"]
    updated = json.loads(iam.update_assume_role_policy.call_args.kwargs["PolicyDocument"])
    assert updated["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == [
        "ext-existing",
        "ext-new",
    ]


def test_build_s3_read_policy_document_scopes_prefix():
    policy = build_s3_read_policy_document(
        "datasyncher-stg",
        key_prefix="dynamics_fo",
        role_arn="arn:aws:iam::234882402654:role/databricks-uc-s3-read",
    )
    assert policy["Statement"][0]["Condition"]["StringLike"]["s3:prefix"] == [
        "dynamics_fo/*",
        "dynamics_fo",
    ]
    assert policy["Statement"][1]["Resource"] == "arn:aws:s3:::datasyncher-stg/dynamics_fo/*"
    assert policy["Statement"][2]["Action"] == ["sts:AssumeRole"]


def test_ensure_iam_role_for_databricks_s3_updates_trust_and_policy(monkeypatch):
    iam = MagicMock()
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"AWS": "old"}}],
    }
    iam.get_role.return_value = {
        "Role": {"AssumeRolePolicyDocument": json.dumps(trust_policy)}
    }
    class _IamExceptions:
        NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})

    iam.exceptions = _IamExceptions()
    iam.get_role_policy.side_effect = _IamExceptions.NoSuchEntityException()
    monkeypatch.setattr("utils.aws_iam_utils._iam_client", lambda: iam)

    role_arn = "arn:aws:iam::234882402654:role/databricks-uc-s3-read"
    result = ensure_iam_role_for_databricks_s3(
        role_arn,
        "ext-456",
        "datasyncher-stg",
        key_prefix="dynamics_fo",
    )

    assert result["iam_granted"] is True
    assert result["trust_updated"] is True
    assert result["policy_updated"] is True
    iam.update_assume_role_policy.assert_called_once()
    iam.put_role_policy.assert_called_once_with(
        RoleName="databricks-uc-s3-read",
        PolicyName=DATASYNCHER_S3_READ_POLICY_NAME,
        PolicyDocument=iam.put_role_policy.call_args.kwargs["PolicyDocument"],
    )


def test_ensure_databricks_s3_external_location_auto_grants_iam(monkeypatch):
    from services.databricks import load_helpers as helpers

    engine = MagicMock()
    engine.config = {
        "server_hostname": "dbc-test.cloud.databricks.com",
        "access_token": "token",
    }
    engine.connect.return_value.__enter__.return_value = MagicMock(
        execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    )
    engine.connect.return_value.__exit__.return_value = False

    monkeypatch.setattr(
        helpers,
        "_databricks_api_request",
        MagicMock(
            side_effect=[
                {"storage_credentials": []},
                {},
                {
                    "aws_iam_role": {
                        "role_arn": "arn:aws:iam::234882402654:role/databricks-uc-s3-read",
                        "external_id": "ext-789",
                        "unity_catalog_iam_arn": "arn:aws:iam::414351767826:user/uc-user",
                    }
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "utils.aws_iam_utils.ensure_iam_role_for_databricks_s3",
        MagicMock(
            return_value={
                "iam_granted": True,
                "trust_updated": True,
                "policy_updated": True,
            }
        ),
    )

    result = helpers.ensure_databricks_s3_external_location(
        engine,
        "s3://datasyncher-stg/dynamics_fo/54/",
        create_credential=True,
        aws_iam_role_arn="arn:aws:iam::234882402654:role/databricks-uc-s3-read",
    )

    assert result["iam_granted"] is True
    assert result["skipped_reason"] is None
    assert helpers._databricks_api_request.call_count == 3


def test_resolve_dynamics_s3_staging_parent_url(monkeypatch):
    from services.databricks import load_helpers as helpers

    monkeypatch.setattr(
        helpers,
        "resolve_databricks_s3_settings",
        lambda dest: {"s3_bucket_url": "s3://datasyncher-stg"},
    )
    assert (
        helpers.resolve_dynamics_s3_staging_parent_url({})
        == "s3://datasyncher-stg/dynamics_fo/"
    )


def test_should_provision_databricks_s3_staging_at_connection(monkeypatch):
    from services.databricks import load_helpers as helpers

    monkeypatch.setattr(
        helpers,
        "resolve_spark_landing_cloud",
        lambda dest: helpers.SPARK_LANDING_CLOUD_S3,
    )
    monkeypatch.setattr(
        helpers,
        "resolve_databricks_s3_settings",
        lambda dest: {"mode": "auto"},
    )
    assert helpers.should_provision_databricks_s3_staging_at_connection({}) is True

    monkeypatch.setattr(
        helpers,
        "resolve_databricks_s3_settings",
        lambda dest: {"mode": "manual"},
    )
    assert helpers.should_provision_databricks_s3_staging_at_connection({}) is False


def test_wait_for_databricks_s3_prefix_readable_retries(monkeypatch):
    from services.databricks import load_helpers as helpers

    checks = iter(
        [
            {"ok": False, "error": "propagating"},
            {"ok": True, "entry_count": 1},
        ]
    )
    monkeypatch.setattr(
        helpers,
        "verify_databricks_s3_prefix_readable",
        lambda engine, prefix, **kwargs: next(checks),
    )
    monkeypatch.setattr(helpers.time, "sleep", lambda _seconds: None)

    result = helpers.wait_for_databricks_s3_prefix_readable(
        MagicMock(),
        "s3://datasyncher-stg/dynamics_fo/54/",
        max_wait_seconds=30,
        poll_interval_seconds=1,
    )

    assert result["ok"] is True
    assert result["attempts"] == 2
