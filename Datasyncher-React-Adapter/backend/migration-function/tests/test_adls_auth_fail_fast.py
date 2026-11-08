import pytest
from fastapi import HTTPException

from models.adls_source_models import ADLSListingTables
from services.adls import source as adls_source_service
from services.adls.source import ADLSSourceService


def _config(**overrides):
    value = {
        "tenant_id": "bad-tenant",
        "client_id": "client-id",
        "client_secret": "secret",
        "account_name": "teststorageacct",
        "container_name": "test-container",
        "folder_name": "landing",
        "file_type": "csv",
    }
    value.update(overrides)
    return value


def test_list_files_propagates_authentication_failure(monkeypatch):
    auth_error = HTTPException(status_code=422, detail="ADLS tenant_id is invalid or not found.")
    monkeypatch.setattr(
        "services.adls.source._get_adls_file_system_client",
        lambda _config: (_ for _ in ()).throw(auth_error),
    )
    request = ADLSListingTables(
        connection_id=1,
        AzureDataLakeStorage=_config(),
        cmp_id=1,
        user_id=1,
    )

    with pytest.raises(HTTPException) as exc:
        ADLSSourceService.list_files(request)

    assert exc.value is auth_error


def test_access_timeout_is_returned_as_terminal_api_error():
    class TimedOutFileSystemClient:
        def get_file_system_properties(self):
            raise TimeoutError("connection timed out")

    with pytest.raises(HTTPException) as exc:
        adls_source_service._validate_adls_destination_access(
            TimedOutFileSystemClient(),
            account_name="teststorageacct",
            container_name="test-container",
        )

    assert exc.value.status_code == 422
    assert "timed out" in exc.value.detail


def test_source_listing_uses_fail_fast_credential_validation(monkeypatch):
    captured = {}

    class FakeServiceClient:
        def get_file_system_client(self, container_name):
            captured["container_name"] = container_name
            return object()

    def build_client(config, *, validation_request=False):
        captured["config"] = config
        captured["validation_request"] = validation_request
        return FakeServiceClient()

    monkeypatch.setattr(adls_source_service, "_build_adls_service_client", build_client)
    monkeypatch.setattr(adls_source_service, "_validate_adls_destination_access", lambda *args, **kwargs: None)

    adls_source_service._get_adls_file_system_client(_config())

    assert captured["validation_request"] is True
    assert captured["container_name"] == "test-container"
