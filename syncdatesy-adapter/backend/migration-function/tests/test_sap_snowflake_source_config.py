"""SAP Snowflake connector resolves host/port and per-table service_name."""

from services.sap.as_source.to_snowflake import _resolve_server_address


def test_resolve_server_address_from_host_and_port():
    assert _resolve_server_address({"host": "sap.example.com", "port": 44300}) == "sap.example.com:44300"


def test_resolve_server_address_prefers_server_address():
    assert _resolve_server_address(
        {"server_address": "sap.example.com:8443", "host": "ignored", "port": 44300}
    ) == "sap.example.com:8443"


def test_resolve_server_address_host_only():
    assert _resolve_server_address({"host": "sap.example.com"}) == "sap.example.com"
