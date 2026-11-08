"""Tests for fast Dataverse date-folder listing helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from utils.azure_path_utils import list_dataverse_date_folder_names
from services import listing_tables_service as lts


def test_list_dataverse_date_folder_names_uses_walk_blobs_delimiter():
    container = MagicMock()
    container.walk_blobs.return_value = [
        SimpleNamespace(name="org/2026-06-16T10.00.00Z/"),
        SimpleNamespace(name="org/2026-06-17T11.00.00Z/"),
    ]

    folders = list_dataverse_date_folder_names(
        container,
        base_folder="org",
        name_prefix=None,
    )

    container.walk_blobs.assert_called_once_with(
        name_starts_with="org/",
        delimiter="/",
    )
    assert folders == {
        "2026-06-16T10.00.00Z",
        "2026-06-17T11.00.00Z",
    }


def test_build_monthly_listing_prefixes():
    prefixes = lts.build_monthly_listing_prefixes("2026-01-15", "2026-03-02")
    assert prefixes == ["2026-01", "2026-02", "2026-03"]


def test_build_listing_prefixes_uses_months_for_long_gaps(monkeypatch):
    monkeypatch.setattr(lts, "LISTING_INCREMENTAL_MONTH_PREFIX_MIN_DAYS", 30)
    prefixes = lts.build_listing_prefixes("2025-01-01T00.00.00Z")
    assert prefixes[0] == "2025-01"
    assert prefixes[-1].startswith("2026-")


def test_list_table_csv_blobs_empty_base_folder_has_no_leading_slash():
    from services.dynamics.source_common import list_table_csv_blobs

    container = MagicMock()
    container.list_blobs.return_value = [
        SimpleNamespace(name="2026-06-16T10.00.00Z/accountingevent/1.csv"),
    ]

    prefix, names = list_table_csv_blobs(
        container,
        "",
        "2026-06-16T10.00.00Z",
        "accountingevent",
    )

    assert prefix == "2026-06-16T10.00.00Z/accountingevent/"
    assert not prefix.startswith("/")
    container.list_blobs.assert_called_once_with(name_starts_with=prefix)
    assert names == ["2026-06-16T10.00.00Z/accountingevent/1.csv"]


def test_resolve_dataverse_base_folder_prefers_config(monkeypatch):
    from services.dynamics.source_common import resolve_dataverse_base_folder

    container = MagicMock()
    monkeypatch.setattr(
        "services.dynamics.source_common.infer_base_folder",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not infer")),
    )

    resolved = resolve_dataverse_base_folder(
        container,
        1,
        57,
        {"base_folder": "myorg/export"},
    )
    assert resolved == "myorg/export"
