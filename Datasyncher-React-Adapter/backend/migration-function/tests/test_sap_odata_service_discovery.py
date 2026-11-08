import sys
from pathlib import Path

MIGRATION_FUNCTION_DIR = Path(__file__).resolve().parents[1]
if str(MIGRATION_FUNCTION_DIR) not in sys.path:
    sys.path.insert(0, str(MIGRATION_FUNCTION_DIR))

from services.sap.client import (  # noqa: E402
    _normalize_catalog_service_name,
    _normalize_known_service_names,
    _service_name_from_catalog_item,
    _service_name_from_gateway_path,
    _services_from_catalog_items,
    _services_from_custom_listing_items,
)


def test_service_name_from_gateway_path():
    assert (
        _service_name_from_gateway_path("/sap/opu/odata/sap/ZFI_GL_4_1_SRV/")
        == "ZFI_GL_4_1_SRV"
    )
    assert (
        _service_name_from_gateway_path(
            "https://ecxnet.ddns.net:44310/sap/opu/odata/sap/ZFI_GL_4_1_SRV/"
        )
        == "ZFI_GL_4_1_SRV"
    )


def test_services_from_custom_listing_items():
    items = [
        {"SERVICE": "ZFI_GL_4_1_SRV"},
        {"SERVICE": "Z_I_DS_SRVC_LIST_SRV"},
        {"SERVICE": "ZFI_GL_4_1_SRV"},
    ]
    assert _services_from_custom_listing_items(items) == [
        {"service_name": "ZFI_GL_4_1_SRV"},
    ]


def test_services_from_catalog_items():
    items = [
        {"ID": "/sap/opu/odata/sap/ZFI_GL_4_1_SRV/", "TechnicalName": "ZFI_GL_4_1_SRV"},
        {"ID": "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/"},
    ]
    names = {row["service_name"] for row in _services_from_catalog_items(items)}
    assert names == {"ZFI_GL_4_1_SRV"}


def test_normalize_catalog_service_name_strips_version_suffix():
    assert _normalize_catalog_service_name("ZFI_GL_4_SRV_0001") == "ZFI_GL_4_SRV"
    assert _normalize_catalog_service_name("ZFI_GL_4_1_SRV_0001") == "ZFI_GL_4_1_SRV"
    assert _normalize_catalog_service_name("ZFI_GL_4_1_SRV") == "ZFI_GL_4_1_SRV"


def test_service_name_from_catalog_item_plain_id():
    assert _service_name_from_catalog_item({"ID": "ZFI_GL_4_SRV_0001"}) == "ZFI_GL_4_SRV"
    assert _service_name_from_catalog_item({"ID": "ZFI_GL_4_1_SRV_0001"}) == "ZFI_GL_4_1_SRV"


def test_normalize_known_service_names_skips_technical_and_dedupes():
    assert _normalize_known_service_names(
        ["ZFI_GL_4_1_SRV", "ZFI_GL_4_1_SRV", "Z_I_DS_SRVC_LIST_SRV", "  "]
    ) == ["ZFI_GL_4_1_SRV"]
