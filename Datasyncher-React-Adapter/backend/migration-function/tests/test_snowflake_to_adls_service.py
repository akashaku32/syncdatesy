import pytest
from fastapi import HTTPException

from models.snowflake_adls_models import ADLSAuthorizationRequest
from services import snowflake_to_adls_service
from services.snowflake.as_source.to_adls import (
    _map_adls_error,
    _validate_adls_storage_account_dns,
    validate_adls_authorization,
)


def _valid_config(**overrides):
    config = {
        "tenant_id": "tenant-123",
        "client_id": "client-123",
        "client_secret": "secret-value",
        "account_name": "teststorageacct",
        "container_name": "test-container",
    }
    config.update(overrides)
    return config


def _request(config):
    return ADLSAuthorizationRequest(destination_config=config)


def _assert_validation_error(config, expected_detail):
    with pytest.raises(HTTPException) as exc:
        validate_adls_authorization(_request(config))

    assert exc.value.status_code == 422
    assert expected_detail in exc.value.detail


def test_validate_adls_authorization_accepts_valid_payload():
    validate_adls_authorization(_request(_valid_config()))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"tenant_id": ""}, "Missing required field: tenant_id"),
        ({"client_id": ""}, "Missing required field: client_id"),
        ({"client_secret": ""}, "Missing required field: client_secret"),
        ({"account_name": ""}, "Missing required field: account_name"),
        ({"container_name": ""}, "Missing required field: container_name"),
        ({"account_name": "DataSyncherStorage"}, "Invalid account_name"),
        ({"container_name": "DataSyncher-Output"}, "Invalid container_name"),
        ({"container_name": "datasyncher-output/folder"}, "Invalid container_name"),
    ],
)
def test_validate_adls_authorization_rejects_malformed_fields(overrides, expected):
    _assert_validation_error(_valid_config(**overrides), expected)


@pytest.mark.parametrize(
    ("raw_error", "expected"),
    [
        ("AADSTS90002: Tenant not found", "tenant_id is invalid"),
        ("AADSTS700016: Application was not found", "client_id is invalid"),
        ("AADSTS700016: Application with identifier 'bad-client-id' was not found in the directory 'tenant-name'. This can happen if the application has not been installed by the administrator of the tenant.", "client_id is invalid"),
        ("AADSTS7000215: Invalid client secret provided", "client_secret is invalid"),
        ("ClientSecretCredential.get_token failed: Authentication failed: AADSTS7000215: Invalid client secret provided.", "client_secret is invalid"),
        ("AADSTS7000222: The provided client secret keys are expired", "client_secret is expired"),
        ("AADSTS7000112: Application is disabled", "app registration is disabled"),
        ("AuthorizationPermissionMismatch: This request is not authorized", "cannot access container"),
        ("ContainerNotFound: The specified container does not exist", "container not found"),
        ("getaddrinfo failed", "storage account not found"),
        ("connection timed out", "storage account timed out"),
        ("Hierarchical namespace is not enabled", "hierarchical namespace"),
    ],
)
def test_map_adls_error_returns_actionable_creation_errors(raw_error, expected):
    exc = _map_adls_error(Exception(raw_error), "teststorageacct", "test-container")

    assert exc.status_code == 422
    assert expected in exc.detail


def test_validate_adls_storage_account_dns_rejects_unknown_account(monkeypatch):
    def raise_dns_error(*args, **kwargs):
        raise snowflake_to_adls_service.socket.gaierror("getaddrinfo failed")

    monkeypatch.setattr(snowflake_to_adls_service.socket, "getaddrinfo", raise_dns_error)

    with pytest.raises(HTTPException) as exc:
        _validate_adls_storage_account_dns("datasyncherstorag")

    assert exc.value.status_code == 422
    assert exc.value.detail == "ADLS storage account not found or unreachable: datasyncherstorag."


def test_upload_file_to_adls_uses_manual_append_flush_chunks(monkeypatch, tmp_path):
    uploaded = {}

    class FakeFileClient:
        def delete_file(self, **kwargs):
            uploaded.setdefault("delete_calls", []).append(kwargs)

        def create_file(self, **kwargs):
            uploaded.setdefault("create_calls", []).append(kwargs)

        def append_data(self, data, *, offset, length, **kwargs):
            uploaded.setdefault("append_calls", []).append(
                {
                    "data": data,
                    "offset": offset,
                    "length": length,
                    "kwargs": kwargs,
                }
            )

        def flush_data(self, position, **kwargs):
            uploaded["flush_call"] = {
                "position": position,
                "kwargs": kwargs,
            }

    class FakeFileSystemClient:
        def __init__(self):
            self.file_client = FakeFileClient()

        def get_file_client(self, path):
            uploaded["path"] = path
            return self.file_client

    class FakeServiceClient:
        def __init__(self):
            self.file_system_client = FakeFileSystemClient()

        def get_file_system_client(self, container_name):
            uploaded["container_name"] = container_name
            return self.file_system_client

    monkeypatch.setattr(
        snowflake_to_adls_service,
        "_build_adls_service_client",
        lambda _config: FakeServiceClient(),
    )
    monkeypatch.setattr(snowflake_to_adls_service, "ADLS_UPLOAD_CHUNK_SIZE", 4)

    local_path = tmp_path / "export.csv"
    local_path.write_bytes(b"abcdefghij")

    size = snowflake_to_adls_service._upload_file_to_adls(
        _valid_config(),
        remote_path="folder/export.csv",
        local_path=str(local_path),
    )

    assert size == 10
    assert uploaded["path"] == "folder/export.csv"
    assert uploaded["container_name"] == "test-container"
    assert uploaded["delete_calls"] == [{"timeout": snowflake_to_adls_service.ADLS_UPLOAD_TIMEOUT}]
    assert uploaded["create_calls"] == [{"timeout": snowflake_to_adls_service.ADLS_UPLOAD_TIMEOUT}]
    assert uploaded["append_calls"] == [
        {
            "data": b"abcd",
            "offset": 0,
            "length": 4,
            "kwargs": {"timeout": snowflake_to_adls_service.ADLS_UPLOAD_TIMEOUT},
        },
        {
            "data": b"efgh",
            "offset": 4,
            "length": 4,
            "kwargs": {"timeout": snowflake_to_adls_service.ADLS_UPLOAD_TIMEOUT},
        },
        {
            "data": b"ij",
            "offset": 8,
            "length": 2,
            "kwargs": {"timeout": snowflake_to_adls_service.ADLS_UPLOAD_TIMEOUT},
        },
    ]
    assert uploaded["flush_call"] == {
        "position": 10,
        "kwargs": {"timeout": snowflake_to_adls_service.ADLS_UPLOAD_TIMEOUT},
    }


def test_upload_file_to_adls_reports_failed_chunk_offset(monkeypatch, tmp_path):
    class FakeFileClient:
        def delete_file(self, **kwargs):
            return None

        def create_file(self, **kwargs):
            return None

        def append_data(self, data, *, offset, length, **kwargs):
            if offset == 4:
                raise TimeoutError("write operation timed out")

        def flush_data(self, position, **kwargs):
            raise AssertionError("flush_data should not run after append failure")

    class FakeFileSystemClient:
        def get_file_client(self, path):
            return FakeFileClient()

    class FakeServiceClient:
        def get_file_system_client(self, container_name):
            return FakeFileSystemClient()

    monkeypatch.setattr(
        snowflake_to_adls_service,
        "_build_adls_service_client",
        lambda _config: FakeServiceClient(),
    )
    monkeypatch.setattr(snowflake_to_adls_service, "ADLS_UPLOAD_CHUNK_SIZE", 4)

    local_path = tmp_path / "export.csv"
    local_path.write_bytes(b"abcdefghij")

    with pytest.raises(HTTPException) as exc:
        snowflake_to_adls_service._upload_file_to_adls(
            _valid_config(),
            remote_path="folder/export.csv",
            local_path=str(local_path),
        )

    assert exc.value.status_code == 422
    assert "chunk 2" in exc.value.detail
    assert "offset=4" in exc.value.detail
    assert "write operation timed out" in exc.value.detail
