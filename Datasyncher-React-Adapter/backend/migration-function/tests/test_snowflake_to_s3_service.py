import pytest
from fastapi import HTTPException

from services.snowflake.as_source.to_s3 import (
    _validate_s3_destination_access,
    validate_s3_base_folder_exists,
)


class _FakeS3Client:
    def __init__(self, key_count):
        self.key_count = key_count
        self.calls = []

    def head_bucket(self, Bucket):
        self.calls.append(("head_bucket", Bucket))

    def list_objects_v2(self, **kwargs):
        self.calls.append(("list_objects_v2", kwargs))
        return {"KeyCount": self.key_count}


def test_validate_s3_destination_access_rejects_missing_base_folder():
    s3 = _FakeS3Client(key_count=0)

    with pytest.raises(HTTPException) as exc_info:
        _validate_s3_destination_access(s3, bucket="test-bucket", base_prefix="exports")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "S3 base folder not found: s3://test-bucket/exports/."
    assert s3.calls == [
        ("head_bucket", "test-bucket"),
        ("list_objects_v2", {"Bucket": "test-bucket", "Prefix": "exports/", "MaxKeys": 1}),
    ]


def test_validate_s3_destination_access_allows_existing_base_folder():
    s3 = _FakeS3Client(key_count=1)

    _validate_s3_destination_access(s3, bucket="test-bucket", base_prefix="/exports/")

    assert s3.calls == [
        ("head_bucket", "test-bucket"),
        ("list_objects_v2", {"Bucket": "test-bucket", "Prefix": "exports/", "MaxKeys": 1}),
    ]


def test_validate_s3_base_folder_exists_returns_error_for_missing_prefix(monkeypatch):
    monkeypatch.setattr(
        "services.snowflake.as_source.to_s3._build_s3_client",
        lambda _config: _FakeS3Client(key_count=0),
    )

    is_valid, error = validate_s3_base_folder_exists(
        aws_access_key_id="access",
        aws_secret_access_key="secret",
        s3_bucket="test-bucket",
        base_prefix="exports",
    )

    assert is_valid is False
    assert error == "S3 base folder not found: s3://test-bucket/exports/."
