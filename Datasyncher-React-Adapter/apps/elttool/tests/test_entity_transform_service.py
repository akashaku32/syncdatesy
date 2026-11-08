import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.elttool.services.entity_transform_service import (
    parse_entity_names,
    serialize_transform_summary,
    transform_badge,
)


class EntityTransformServiceTests(TestCase):
    def test_parse_entity_names_with_service_prefix(self):
        row = SimpleNamespace(
            service_name="ZFI_GL_4_SRV",
            table="ZFI_GL_4_SRV_EntityOf0FI_GL_4",
        )
        service, entity, table = parse_entity_names(row)
        assert service == "ZFI_GL_4_SRV"
        assert entity == "EntityOf0FI_GL_4"
        assert table == "ZFI_GL_4_SRV_EntityOf0FI_GL_4"

    def test_transform_badge_mapped_script(self):
        active = SimpleNamespace(
            version=3,
            mapping_json={"field_mapping": []},
            script_text="def f(df): return df",
            is_active=True,
        )
        assert transform_badge(active) == "Mapped · Script v3"

    def test_serialize_empty_summary(self):
        summary = serialize_transform_summary(None)
        assert summary["has_mapping"] is False
        assert summary["badge"] is None
