import pytest
from fastapi import HTTPException

from models.adls_source_models import ADLSListingTables, ADLSPatternPreviewRequest
from services.adls import source as adls_source_service
from services.adls.source import ADLSSourceService, _resolve_adls_table_groups_from_mapping
from services.adls.as_source.to_snowflake import (
    _build_file_to_table_lookup,
    _matching_existing_key,
    _resolve_table_to_files_map,
    _should_apply_delta_check,
)
from services.source_file_helpers.file_source_base import SourceFileEntry


def _entry(file_key):
    return SourceFileEntry(
        file_key=file_key,
        relative_path=file_key,
        basename=file_key.rsplit("/", 1)[-1],
    )


def test_adls_schema_listing_resolves_table_to_files_as_multiple_tables():
    entries = [
        _entry("landing/customer.csv"),
        _entry("landing/orders.csv"),
    ]

    table_groups = _resolve_adls_table_groups_from_mapping(
        {
            "CSV_CUSTOMER": ["customer.csv"],
            "CSV_ORDERS": ["landing/orders.csv"],
        },
        entries,
    )

    assert sorted(table_groups.keys()) == ["CSV_CUSTOMER", "CSV_ORDERS"]
    assert [entry.file_key for entry in table_groups["CSV_CUSTOMER"]] == ["landing/customer.csv"]
    assert [entry.file_key for entry in table_groups["CSV_ORDERS"]] == ["landing/orders.csv"]


def test_adls_migration_keeps_table_to_files_targets_separate():
    entries = [
        _entry("landing/customer.csv"),
        _entry("landing/orders.csv"),
    ]

    resolved = _resolve_table_to_files_map(
        {
            "CSV_CUSTOMER": ["customer.csv"],
            "CSV_ORDERS": ["landing/orders.csv"],
        },
        entries,
    )
    file_to_table = _build_file_to_table_lookup(resolved)

    assert resolved == {
        "CSV_CUSTOMER": ["landing/customer.csv"],
        "CSV_ORDERS": ["landing/orders.csv"],
    }
    assert file_to_table == {
        "landing/customer.csv": "CSV_CUSTOMER",
        "landing/orders.csv": "CSV_ORDERS",
    }


def test_adls_manual_file_row_upsert_keeps_delta_check_enabled():
    assert _should_apply_delta_check("upsert_file_row", is_manual_migration=True)
    assert not _should_apply_delta_check("upsert_custom_key", is_manual_migration=True)
    assert not _should_apply_delta_check("append_file_modified", is_manual_migration=True)
    assert _should_apply_delta_check("upsert_file_row", is_manual_migration=False)


def test_adls_deleted_full_path_does_not_match_unrelated_same_basename():
    existing = {"employees.csv", "landing/customers.csv"}

    assert _matching_existing_key("archive/employees.csv", existing) is None
    assert _matching_existing_key("customers.csv", existing) == "landing/customers.csv"


def test_multi_file_mapping_passes_configured_type_to_reader_without_filtering(monkeypatch):
    captured = {}
    entries = [
        SourceFileEntry(
            file_key="CSV/CUSTOMERS.csv",
            relative_path="CUSTOMERS.csv",
            basename="CUSTOMERS.csv",
        ),
        SourceFileEntry(
            file_key="CSV/PRODUCTS.csv",
            relative_path="PRODUCTS.csv",
            basename="PRODUCTS.csv",
        ),
    ]

    monkeypatch.setattr(
        adls_source_service,
        "_get_adls_file_system_client",
        lambda _config: (object(), "sapodata", "odatasap"),
    )
    monkeypatch.setattr(adls_source_service, "_list_adls_files", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(adls_source_service, "_load_existing_file_metadata", lambda _connection_id: {})
    monkeypatch.setattr(adls_source_service, "_open_adls_file_sample", lambda *_args: object())
    monkeypatch.setattr(adls_source_service, "_add_metadata_to_df", lambda frame, *_args: frame)
    monkeypatch.setattr(adls_source_service, "infer_columns_from_dataframe", lambda *_args, **_kwargs: {})

    def read_sample(_remote_file, **reader_kwargs):
        captured.update(reader_kwargs)
        return object()

    monkeypatch.setattr(adls_source_service, "read_dataframe_sample", read_sample)

    request = ADLSListingTables(
        connection_id=64,
        cmp_id=1,
        user_id=1,
        AzureDataLakeStorage={
            "tenant_id": "tenant-id",
            "client_id": "client-id",
            "client_secret": "secret",
            "account_name": "sapodata",
            "container_name": "odatasap",
            "folder_name": "CSV",
            "include_subfolders": True,
            "file_type": "CSV",
            "file_mapping_method": "multi_files_single_table",
            "multi_files_prefix": "*.csv",
            "multi_files_table_name": "ALL_FILES",
            "load_method": "upsert_file_row",
        },
    )

    result = ADLSSourceService.list_tables_with_schema(request)

    assert captured["file_type"] == "csv"
    assert result["compatible_files_count"] == 2
    assert result["table_to_files_mapping"] == {
        "ALL_FILES": ["CSV/CUSTOMERS.csv", "CSV/PRODUCTS.csv"]
    }


def test_adls_schema_listing_rejects_single_mapped_wrong_extension_before_sample_read(monkeypatch):
    entries = [
        SourceFileEntry(
            file_key="landing/customers.csv",
            relative_path="customers.csv",
            basename="customers.csv",
            inferred_file_type="csv",
        )
    ]
    sample_reader_calls = []

    monkeypatch.setattr(
        adls_source_service,
        "_get_adls_file_system_client",
        lambda _config: (object(), "sapodata", "odatasap"),
    )
    monkeypatch.setattr(adls_source_service, "_list_adls_files", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(adls_source_service, "_load_existing_file_metadata", lambda _connection_id: {})
    monkeypatch.setattr(adls_source_service, "_open_adls_file_sample", lambda *_args: object())

    def read_sample(_remote_file, **reader_kwargs):
        sample_reader_calls.append(reader_kwargs)
        return object()

    monkeypatch.setattr(adls_source_service, "read_dataframe_sample", read_sample)
    monkeypatch.setattr(adls_source_service, "_add_metadata_to_df", lambda frame, *_args: frame)
    monkeypatch.setattr(adls_source_service, "infer_columns_from_dataframe", lambda *_args, **_kwargs: {})

    request = ADLSListingTables(
        connection_id=64,
        cmp_id=1,
        user_id=1,
        AzureDataLakeStorage={
            "tenant_id": "tenant-id",
            "client_id": "client-id",
            "client_secret": "secret",
            "account_name": "sapodata",
            "container_name": "odatasap",
            "folder_name": "landing",
            "file_type": "json",
            "file_mapping_method": "single_table_per_file",
            "single_file_table_mapping": {"landing/customers.csv": "CUSTOMERS"},
            "load_method": "upsert_file_row",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        ADLSSourceService.list_tables_with_schema(request)

    assert exc_info.value.status_code == 422
    detail = str(exc_info.value.detail).lower()
    assert "file type" in detail or "file_type" in detail
    assert "json" in detail
    assert "csv" in detail
    assert "customers.csv" in detail
    assert sample_reader_calls == []


def test_adls_schema_listing_rejects_mixed_extensions_in_multi_file_group(monkeypatch):
    entries = [
        SourceFileEntry(
            file_key="landing/customers.csv",
            relative_path="customers.csv",
            basename="customers.csv",
            inferred_file_type="csv",
        ),
        SourceFileEntry(
            file_key="landing/orders.json",
            relative_path="orders.json",
            basename="orders.json",
            inferred_file_type="json",
        ),
    ]
    sample_reader_calls = []

    monkeypatch.setattr(
        adls_source_service,
        "_get_adls_file_system_client",
        lambda _config: (object(), "sapodata", "odatasap"),
    )
    monkeypatch.setattr(adls_source_service, "_list_adls_files", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(adls_source_service, "_load_existing_file_metadata", lambda _connection_id: {})
    monkeypatch.setattr(adls_source_service, "_open_adls_file_sample", lambda *_args: object())

    def read_sample(_remote_file, **reader_kwargs):
        sample_reader_calls.append(reader_kwargs)
        return object()

    monkeypatch.setattr(adls_source_service, "read_dataframe_sample", read_sample)
    monkeypatch.setattr(adls_source_service, "_add_metadata_to_df", lambda frame, *_args: frame)
    monkeypatch.setattr(adls_source_service, "infer_columns_from_dataframe", lambda *_args, **_kwargs: {})

    request = ADLSListingTables(
        connection_id=64,
        cmp_id=1,
        user_id=1,
        AzureDataLakeStorage={
            "tenant_id": "tenant-id",
            "client_id": "client-id",
            "client_secret": "secret",
            "account_name": "sapodata",
            "container_name": "odatasap",
            "folder_name": "landing",
            "file_type": "csv",
            "file_mapping_method": "multi_files_single_table",
            "table_to_files_mapping": {
                "ALL_FILES": ["landing/customers.csv", "landing/orders.json"]
            },
            "multi_files_table_name": "ALL_FILES",
            "load_method": "upsert_file_row",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        ADLSSourceService.list_tables_with_schema(request)

    assert exc_info.value.status_code == 422
    detail = str(exc_info.value.detail).lower()
    assert "file type" in detail or "file_type" in detail
    assert "csv" in detail
    assert "json" in detail
    assert "orders.json" in detail
    assert sample_reader_calls == []


def test_adls_schema_listing_keeps_matching_single_mapped_file_behavior(monkeypatch):
    entries = [
        SourceFileEntry(
            file_key="landing/customers.csv",
            relative_path="customers.csv",
            basename="customers.csv",
            inferred_file_type="csv",
        )
    ]
    captured_reader_kwargs = []

    monkeypatch.setattr(
        adls_source_service,
        "_get_adls_file_system_client",
        lambda _config: (object(), "sapodata", "odatasap"),
    )
    monkeypatch.setattr(adls_source_service, "_list_adls_files", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(adls_source_service, "_load_existing_file_metadata", lambda _connection_id: {})
    monkeypatch.setattr(adls_source_service, "_open_adls_file_sample", lambda *_args: object())

    def read_sample(_remote_file, **reader_kwargs):
        captured_reader_kwargs.append(reader_kwargs)
        return object()

    monkeypatch.setattr(adls_source_service, "read_dataframe_sample", read_sample)
    monkeypatch.setattr(adls_source_service, "_add_metadata_to_df", lambda frame, *_args: frame)
    monkeypatch.setattr(adls_source_service, "infer_columns_from_dataframe", lambda *_args, **_kwargs: {})

    request = ADLSListingTables(
        connection_id=64,
        cmp_id=1,
        user_id=1,
        AzureDataLakeStorage={
            "tenant_id": "tenant-id",
            "client_id": "client-id",
            "client_secret": "secret",
            "account_name": "sapodata",
            "container_name": "odatasap",
            "folder_name": "landing",
            "file_type": "CSV",
            "file_mapping_method": "single_table_per_file",
            "single_file_table_mapping": {"landing/customers.csv": "CUSTOMERS"},
            "load_method": "upsert_file_row",
        },
    )

    result = ADLSSourceService.list_tables_with_schema(request)

    assert len(captured_reader_kwargs) == 1
    assert captured_reader_kwargs[0]["file_type"] == "csv"
    assert result["compatible_files_count"] == 1
    assert result["incompatible_files_count"] == 0
    assert result["table_to_files_mapping"] == {
        "CUSTOMERS": ["landing/customers.csv"]
    }


def test_adls_schema_listing_marks_removed_mapped_file_for_pruning(monkeypatch):
    entries = [
        SourceFileEntry(
            file_key="landing/customers.csv",
            relative_path="customers.csv",
            basename="customers.csv",
            inferred_file_type="csv",
        )
    ]

    monkeypatch.setattr(
        adls_source_service,
        "_get_adls_file_system_client",
        lambda _config: (object(), "sapodata", "odatasap"),
    )
    monkeypatch.setattr(adls_source_service, "_list_adls_files", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(adls_source_service, "_load_existing_file_metadata", lambda _connection_id: {})
    monkeypatch.setattr(adls_source_service, "_open_adls_file_sample", lambda *_args: object())
    monkeypatch.setattr(adls_source_service, "read_dataframe_sample", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(adls_source_service, "_add_metadata_to_df", lambda frame, *_args: frame)
    monkeypatch.setattr(adls_source_service, "infer_columns_from_dataframe", lambda *_args, **_kwargs: {})

    request = ADLSListingTables(
        connection_id=64,
        cmp_id=1,
        user_id=1,
        AzureDataLakeStorage={
            "tenant_id": "tenant-id",
            "client_id": "client-id",
            "client_secret": "secret",
            "account_name": "sapodata",
            "container_name": "odatasap",
            "folder_name": "landing",
            "file_type": "csv",
            "file_mapping_method": "single_table_per_file",
            "single_file_table_mapping": {
                "customers.csv": "CUSTOMERS",
                "orders.csv": "ORDERS",
            },
            "load_method": "upsert_file_row",
        },
    )

    result = ADLSSourceService.list_tables_with_schema(request)

    assert [table["table"] for table in result["tables"]] == ["CUSTOMERS"]
    assert result["prune_missing_tables"] is True
    assert result["stale_tables_to_prune"] == ["ORDERS"]


def test_adls_preview_pattern_returns_all_file_types(monkeypatch):
    entries = [
        _entry("ann/customers.csv"),
        _entry("ann/orders.tsv"),
        _entry("ann/products.json"),
    ]

    monkeypatch.setattr(
        adls_source_service,
        "_get_adls_file_system_client",
        lambda _config: (object(), "sapodata", "odatasap"),
    )
    monkeypatch.setattr(
        adls_source_service,
        "_list_adls_files",
        lambda *_args, **_kwargs: entries,
    )

    request = ADLSPatternPreviewRequest(
        connection_id=64,
        cmp_id=1,
        user_id=1,
        multi_files_prefix="*.tsv",
        AzureDataLakeStorage={
            "account_name": "sapodata",
            "container_name": "odatasap",
            "folder_name": "ann",
            "file_type": "TSV",
        },
    )

    result = ADLSSourceService.preview_pattern(request)

    assert result["status"] == "success"
    assert result["pattern_ignored"] is True
    assert result["total_files_in_folder"] == 3
    assert result["total_files_by_type"] == 3
    assert result["matched_files_count"] == 3
    assert [item["file_key"] for item in result["matched_files"]] == [
        "ann/customers.csv",
        "ann/orders.tsv",
        "ann/products.json",
    ]
    assert "file_type" not in result


def test_open_adls_file_sample_downloads_only_leading_bytes():
    from services.adls.source import ADLS_SCHEMA_SAMPLE_MAX_BYTES, _open_adls_file_sample

    class FakeDownload:
        def __init__(self):
            self.offset = None
            self.length = None

        def readall(self):
            return b"x" * self.length

    class FakeFileClient:
        def __init__(self):
            self.size = 5_493_354_699
            self.download = FakeDownload()

        def get_file_properties(self):
            return type("Props", (), {"size": self.size})()

        def download_file(self, *, offset=0, length=None):
            self.download.offset = offset
            self.download.length = length
            return self.download

    class FakeFileSystemClient:
        def get_file_client(self, key):
            assert key == "CSV/big.csv"
            return FakeFileClient()

    buf = _open_adls_file_sample(FakeFileSystemClient(), "CSV/big.csv")
    data = buf.read()
    assert len(data) == ADLS_SCHEMA_SAMPLE_MAX_BYTES


def test_open_adls_file_sample_downloads_entire_small_file():
    from services.adls.source import _open_adls_file_sample

    class FakeDownload:
        def __init__(self, length):
            self.length = length

        def readall(self):
            return b"abc"

    class FakeFileClient:
        def __init__(self, size):
            self.size = size

        def get_file_properties(self):
            return type("Props", (), {"size": self.size})()

        def download_file(self, *, offset=0, length=None):
            assert offset == 0
            assert length == self.size
            return FakeDownload(length)

    class FakeFileSystemClient:
        def get_file_client(self, _key):
            return FakeFileClient(size=3)

    buf = _open_adls_file_sample(FakeFileSystemClient(), "small.csv")
    assert buf.read() == b"abc"
