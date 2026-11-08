from services.sap.source import build_sap_delta_start_url


def test_build_sap_delta_start_url_uses_bang_deltatoken_syntax():
    url = build_sap_delta_start_url(
        "D20260630_002",
        server_address="host.example:44300",
        service_name="Z_MASTER01_SRV",
        entity_name="EntityOf0FI_AR_30",
    )
    assert "!deltatoken='D20260630_002'" in url
    assert "$deltatoken=" not in url
    assert "$format=json" in url


def test_build_sap_delta_start_url_preserves_full_link():
    full = (
        "https://host.example:44300/sap/opu/odata/sap/Z_MASTER01_SRV/"
        "EntityOf0FI_AR_30?&!deltatoken='D20260630_002'"
    )
    url = build_sap_delta_start_url(
        full,
        server_address="host.example:44300",
        service_name="Z_MASTER01_SRV",
        entity_name="EntityOf0FI_AR_30",
    )
    assert url.startswith("https://")
    assert "!deltatoken='D20260630_002'" in url
