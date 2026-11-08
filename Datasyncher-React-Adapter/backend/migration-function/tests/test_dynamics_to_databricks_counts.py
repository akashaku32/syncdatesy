"""Dynamics → Databricks record-count behavior with per-chunk merge."""

from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def load_chunk_mod(monkeypatch):
    """Import databricks_load_chunk with destination helpers stubbed."""
    import services.dynamics.as_source.to_databricks as mod

    monkeypatch.setattr(
        mod,
        "copy_function",
        MagicMock(return_value=("DS_T_STAGING_1", pd.DataFrame())),
    )
    monkeypatch.setattr(mod, "ensure_databricks_base_table_exists", MagicMock())
    return mod


def test_databricks_load_chunk_counts_all_rows_per_chunk(load_chunk_mod):
    """Per-chunk merge: inserts reflect chunk size (no staging id_overlap adjustment)."""
    mod = load_chunk_mod

    df = pd.DataFrame(
        {
            "ID": ["a", "b", "c"],
            "IS_DELETE": [False, False, False],
            "SINK_MODIFIED_ON": [
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
                "2026-01-03T00:00:00Z",
            ],
        }
    )
    inserts, updates, deletes, *_ = mod.databricks_load_chunk(
        df=df,
        table_name="CUSTTABLE",
        primary_key_column="ID",
        deletes_lst=[],
        updates_lst=[],
        table_exist=True,
        destination_engine=MagicMock(),
        add_primary_key=False,
        is_last_chunk=False,
        destination_connection={"catalog": "test", "schema": "LANDING"},
        deleted_at="DELETED_AT",
        updated_at="UPDATED_AT",
        is_integration_checked=False,
        extra_fields=None,
        modified_fields=None,
        existing_fields={},
        attributes_details_field={"ID": "guid"},
        connection_id=24,
        cmp_id=1,
        staging_table_name="DS_CUSTTABLE_STAGING_1",
    )

    assert inserts == 3
    assert updates == 0
    assert deletes == 0


def test_databricks_load_chunk_first_chunk_counts_all_rows(load_chunk_mod):
    mod = load_chunk_mod

    df = pd.DataFrame(
        {
            "ID": ["a", "b"],
            "IS_DELETE": [False, False],
            "SINK_MODIFIED_ON": [
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
            ],
        }
    )
    inserts, *_ = mod.databricks_load_chunk(
        df=df,
        table_name="CUSTTABLE",
        primary_key_column="ID",
        deletes_lst=[],
        updates_lst=[],
        table_exist=False,
        destination_engine=MagicMock(),
        add_primary_key=False,
        is_last_chunk=False,
        destination_connection={"catalog": "test", "schema": "LANDING"},
        deleted_at="DELETED_AT",
        updated_at="UPDATED_AT",
        is_integration_checked=False,
        extra_fields=None,
        modified_fields=None,
        existing_fields={},
        attributes_details_field={"ID": "guid"},
        connection_id=24,
        cmp_id=1,
        staging_table_name="DS_CUSTTABLE_STAGING_1",
    )

    assert inserts == 2


def test_databricks_load_chunk_keeps_deletes_present_in_base(load_chunk_mod):
    """Delete rows are passed through to per-chunk MERGE."""
    mod = load_chunk_mod

    df = pd.DataFrame(
        {
            "ID": ["del-id", "new-id"],
            "IS_DELETE": [True, False],
            "SINK_MODIFIED_ON": [
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
            ],
        }
    )
    inserts, updates, deletes, *_ = mod.databricks_load_chunk(
        df=df,
        table_name="CUSTTABLE",
        primary_key_column="ID",
        deletes_lst=[],
        updates_lst=[],
        table_exist=True,
        destination_engine=MagicMock(),
        add_primary_key=False,
        is_last_chunk=False,
        destination_connection={"catalog": "test", "schema": "LANDING"},
        deleted_at="DELETED_AT",
        updated_at="UPDATED_AT",
        is_integration_checked=False,
        extra_fields=None,
        modified_fields=None,
        existing_fields={},
        attributes_details_field={"ID": "guid"},
        connection_id=24,
        cmp_id=1,
        staging_table_name="DS_CUSTTABLE_STAGING_1",
    )

    assert deletes == 1
    assert inserts == 1
    assert updates == 0


def test_databricks_load_chunk_drops_orphan_deletes_before_copy(load_chunk_mod, monkeypatch):
    """Snowflake parity: orphan delete rows are stripped before COPY/MERGE."""
    mod = load_chunk_mod
    copy_mock = mod.copy_function
    monkeypatch.setattr(mod, "retrieve_snowflake_ids_delete", lambda *a, **k: [])

    df = pd.DataFrame(
        {
            "ID": ["orphan"],
            "IS_DELETE": [True],
            "SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z"],
        }
    )
    inserts, updates, deletes, *_ = mod.databricks_load_chunk(
        df=df,
        table_name="CUSTTABLE",
        primary_key_column="ID",
        deletes_lst=[],
        updates_lst=[],
        table_exist=True,
        destination_engine=MagicMock(),
        add_primary_key=False,
        is_last_chunk=False,
        destination_connection={"catalog": "test", "schema": "LANDING"},
        deleted_at="DELETED_AT",
        updated_at="UPDATED_AT",
        is_integration_checked=False,
        extra_fields=None,
        modified_fields=None,
        existing_fields={},
        attributes_details_field={"ID": "guid"},
        connection_id=24,
        cmp_id=1,
        staging_table_name="DS_CUSTTABLE_STAGING_1",
    )

    assert deletes == 0
    assert inserts == 0
    copy_mock.assert_not_called()


def test_filter_null_primary_key_rows_drops_blank_ids():
    from services.dynamics.source_common import filter_null_primary_key_rows

    df = pd.DataFrame(
        {
            "ID": ["valid-id", None, "", "null", "none"],
            "IS_DELETE": [False] * 5,
        }
    )
    filtered = filter_null_primary_key_rows(df, "ID")
    assert list(filtered["ID"]) == ["valid-id"]


def test_filter_orphan_delete_rows_keeps_existing_deletes():
    from services.dynamics.source_common import filter_orphan_delete_rows

    df = pd.DataFrame(
        {
            "ID": ["known", "orphan"],
            "IS_DELETE": [True, True],
        }
    )
    filtered = filter_orphan_delete_rows(df, ["known"])
    assert list(filtered["ID"]) == ["known"]


def test_databricks_load_chunk_drops_null_ids(load_chunk_mod, monkeypatch):
    mod = load_chunk_mod
    copy_mock = mod.copy_function
    monkeypatch.setattr(mod, "retrieve_snowflake_ids_delete", lambda *a, **k: [])

    df = pd.DataFrame(
        {
            "ID": [None, "valid-id"],
            "IS_DELETE": [False, False],
            "SINK_MODIFIED_ON": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"],
        }
    )
    inserts, updates, deletes, *_ = mod.databricks_load_chunk(
        df=df,
        table_name="CUSTTABLE",
        primary_key_column="ID",
        deletes_lst=[],
        updates_lst=[],
        table_exist=True,
        destination_engine=MagicMock(),
        add_primary_key=False,
        is_last_chunk=False,
        destination_connection={"catalog": "test", "schema": "LANDING"},
        deleted_at="DELETED_AT",
        updated_at="UPDATED_AT",
        is_integration_checked=False,
        extra_fields=None,
        modified_fields=None,
        existing_fields={},
        attributes_details_field={"ID": "guid"},
        connection_id=24,
        cmp_id=1,
        staging_table_name="DS_CUSTTABLE_STAGING_1",
    )

    assert inserts == 1
    assert deletes == 0
    copy_mock.assert_called_once()
    passed_df = copy_mock.call_args[0][0]
    assert list(passed_df["ID"]) == ["valid-id"]


def test_align_fields_with_existing_treats_none_as_empty():
    """Initial-load cleanup can leave table_fields NULL while base table still exists."""
    from services.dynamics.source_common import align_fields_with_existing

    df = pd.DataFrame(columns=["ID", "NAME"])
    attribute_details = {"ID": "guid", "NAME": "string"}

    aligned, modified, extra = align_fields_with_existing(
        df, attribute_details, None, "dimensionfocusbalance"
    )

    assert list(aligned.columns) == ["ID", "NAME"]
    assert modified == {}
    assert extra == ["ID", "NAME"]
