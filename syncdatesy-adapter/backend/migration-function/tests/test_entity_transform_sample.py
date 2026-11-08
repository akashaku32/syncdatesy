"""Tests for entity-transform SAP sample parsing helpers."""

import pandas as pd

from routers.entity_transform_router import (
    _merge_sample_frames,
    _normalize_sap_credentials,
    _odata_records_from_payload,
)


def test_normalize_sap_credentials_maps_server_to_host():
    creds = _normalize_sap_credentials(
        {"server": "sap.example.com:8443", "username": "u"}
    )
    assert creds["host"] == "sap.example.com"
    assert creds["port"] == 8443


def test_odata_records_from_direct_payload():
    payload = {"d": {"results": [{"BUKRS": "1000"}]}}
    assert _odata_records_from_payload(payload) == [{"BUKRS": "1000"}]


def test_merge_sample_frames_combines_select_batches():
    first = pd.DataFrame({"A": [1], "B": [2]})
    second = pd.DataFrame({"C": [3], "D": [4]})
    merged = _merge_sample_frames([first, second])
    assert list(merged.columns) == ["A", "B", "C", "D"]
    assert merged.iloc[0].tolist() == [1, 2, 3, 4]


def test_odata_records_from_proxy_wrapped_payload():
    payload = {
        "alldataJson": {"d": {"results": [{"BUKRS": "2000"}]}},
    }
    assert _odata_records_from_payload(payload) == [{"BUKRS": "2000"}]
