"""Tests for in-use staging table protection during long migrations."""

from unittest.mock import MagicMock, patch

from utils import snowflake_utils as su


def test_collect_protected_staging_names_scans_redis():
    mock_client = MagicMock()
    mock_client.scan_iter.return_value = [
        b"migration:session_staging:4055:INVENTJOURNALTRANS",
        b"migration:session_staging:4056:INVENTJOURNALTRANS",
    ]
    mock_client.get.side_effect = [
        b"DS_INVENTJOURNALTRANS_STAGING_20260819_175237",
        b"DS_INVENTJOURNALTRANS_STAGING_20260819_180001",
    ]

    with patch.object(su, "_migration_redis_client", return_value=mock_client):
        protected = su.collect_protected_staging_names_for_table("inventjournaltrans")

    assert protected == {
        "DS_INVENTJOURNALTRANS_STAGING_20260819_175237",
        "DS_INVENTJOURNALTRANS_STAGING_20260819_180001",
    }


def test_drop_existing_skips_protected_staging():
    engine = MagicMock()
    conn = MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [
        ("DS_CUSTTABLE_STAGING_20260819_120000",),
        ("DS_CUSTTABLE_STAGING_20260819_130000",),
    ]

    protected = {"DS_CUSTTABLE_STAGING_20260819_130000"}

    with patch.object(su, "collect_protected_staging_names_for_table", return_value=protected):
        with patch.object(su, "lookup_session_staging_name", return_value=None):
            dropped = su.drop_existing_ds_staging_tables_for_table(
                engine,
                "BATCH_KUBERNETES",
                "custtable",
                migration_session_id=99,
            )

    assert dropped == 1
    drop_calls = [
        str(c.args[0])
        for c in conn.execute.call_args_list
        if c.args and "DROP TABLE" in str(c.args[0])
    ]
    assert len(drop_calls) == 1
    assert "120000" in drop_calls[0]
    assert "130000" not in drop_calls[0]


def test_touch_session_staging_refreshes_ttl():
    mock_client = MagicMock()
    mock_client.exists.return_value = True

    with patch.object(su, "_migration_redis_client", return_value=mock_client):
        su.touch_session_staging_name(4055, "custtable")

    mock_client.expire.assert_called_once()
    args = mock_client.expire.call_args[0]
    assert args[1] == su._SESSION_STAGING_TTL_SECONDS
