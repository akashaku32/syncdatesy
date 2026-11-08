from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.elttool.services.entity_transform_service import (
    entity_has_loaded_data,
    parse_entity_names,
)
from apps.elttool.services.snowflake_destination_name import (
    DESTINATION_NAME_SOURCE_DEFAULT,
    DESTINATION_NAME_SOURCE_JSON,
    DESTINATION_NAME_SOURCE_MANUAL,
    extract_json_target_objectname,
    is_valid_snowflake_destination_name,
    normalize_destination_table_name,
    normalize_objectname_for_compare,
    resolve_destination_table_name,
)


SAMPLE_MAPPING = {
    "source": {"objectname": "0FI_GL_4"},
    "target": {"objectname": "/IMO/CMFIGL04"},
    "field_mapping": [],
}


class NormalizeDestinationTableNameTests(SimpleTestCase):
    def test_imo_sample(self):
        name, warnings = normalize_destination_table_name("/IMO/CMFIGL04")
        self.assertEqual(name, "IMO_CMFIGL04")
        self.assertEqual(warnings, [])

    def test_leading_digit(self):
        name, _ = normalize_destination_table_name("9LEDGER")
        self.assertEqual(name, "T_9LEDGER")

    def test_spaces_collapsed(self):
        name, _ = normalize_destination_table_name("  IMO  CM FIGL  ")
        self.assertEqual(name, "IMO_CM_FIGL")

    def test_unicode_stripped(self):
        name, warnings = normalize_destination_table_name("café///")
        self.assertEqual(name, "CAF")
        self.assertFalse(any("empty" in w.lower() for w in warnings))

    def test_empty_after_normalise(self):
        name, warnings = normalize_destination_table_name("///")
        self.assertEqual(name, "")
        self.assertTrue(warnings)

    def test_truncate_300_chars(self):
        raw = "A" * 300
        name, _ = normalize_destination_table_name(raw)
        self.assertEqual(len(name), 255)

    def test_reserved_word_suffix(self):
        name, warnings = normalize_destination_table_name("SELECT")
        self.assertEqual(name, "SELECT_T")
        self.assertTrue(warnings)

    def test_valid_identifier(self):
        self.assertTrue(is_valid_snowflake_destination_name("IMO_CMFIGL04"))
        self.assertFalse(is_valid_snowflake_destination_name(""))
        self.assertFalse(is_valid_snowflake_destination_name("SELECT"))


class PrecedenceTests(SimpleTestCase):
    def test_json_over_default(self):
        resolved = resolve_destination_table_name(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            table_name="EntityOf0FI_GL_4",
            mapping_json=SAMPLE_MAPPING,
        )
        self.assertEqual(resolved["destination_table_name"], "IMO_CMFIGL04")
        self.assertEqual(resolved["json_table_name"], "/IMO/CMFIGL04")
        self.assertEqual(resolved["destination_name_source"], DESTINATION_NAME_SOURCE_JSON)

    def test_manual_over_json(self):
        resolved = resolve_destination_table_name(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            table_name="EntityOf0FI_GL_4",
            mapping_json=SAMPLE_MAPPING,
            destination_name_source=DESTINATION_NAME_SOURCE_MANUAL,
            manual_destination_table_name="MY_LEDGER",
        )
        self.assertEqual(resolved["destination_table_name"], "MY_LEDGER")
        self.assertEqual(resolved["destination_name_source"], DESTINATION_NAME_SOURCE_MANUAL)
        self.assertEqual(resolved["json_table_name"], "/IMO/CMFIGL04")

    def test_explicit_default_ignores_json(self):
        resolved = resolve_destination_table_name(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            table_name="EntityOf0FI_GL_4",
            mapping_json=SAMPLE_MAPPING,
            destination_name_source=DESTINATION_NAME_SOURCE_DEFAULT,
        )
        self.assertEqual(resolved["destination_name_source"], DESTINATION_NAME_SOURCE_DEFAULT)
        self.assertNotEqual(resolved["destination_table_name"], "IMO_CMFIGL04")

    def test_no_json_keeps_default(self):
        resolved = resolve_destination_table_name(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            table_name="EntityOf0FI_GL_4",
            mapping_json=None,
        )
        self.assertEqual(resolved["destination_name_source"], DESTINATION_NAME_SOURCE_DEFAULT)
        self.assertEqual(resolved["json_table_name"], None)
        self.assertTrue(resolved["destination_table_name"])

    def test_empty_json_name_falls_back(self):
        mapping = {"target": {"objectname": "///"}, "source": {"objectname": "x"}}
        resolved = resolve_destination_table_name(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            table_name="EntityOf0FI_GL_4",
            mapping_json=mapping,
        )
        self.assertEqual(resolved["destination_name_source"], DESTINATION_NAME_SOURCE_DEFAULT)
        self.assertTrue(any("empty" in w.lower() for w in resolved["warnings"]))

    def test_extract_objectname(self):
        self.assertEqual(
            extract_json_target_objectname(SAMPLE_MAPPING), "/IMO/CMFIGL04"
        )
        self.assertIsNone(extract_json_target_objectname({"target": {}}))
        self.assertIsNone(extract_json_target_objectname(None))


class CompareAndLoadedTests(SimpleTestCase):
    def test_letter_o_vs_digit_zero(self):
        self.assertEqual(
            normalize_objectname_for_compare("0FI_GL_4"),
            normalize_objectname_for_compare("EntityOf0FI_GL_4"),
        )
        self.assertEqual(
            normalize_objectname_for_compare("OFI_GL_4"),
            normalize_objectname_for_compare("0FI_GL_4"),
        )

    def test_entity_has_loaded_data(self):
        self.assertTrue(
            entity_has_loaded_data(SimpleNamespace(initial_completed_flag=True))
        )
        self.assertFalse(
            entity_has_loaded_data(
                SimpleNamespace(
                    initial_completed_flag=False,
                    delta_token=None,
                    first_sync_timestamp=None,
                    status=None,
                )
            )
        )

    def test_parse_entity_names(self):
        service, entity, table = parse_entity_names(
            SimpleNamespace(
                service_name="ZFI_GL_4_SRV",
                table="EntityOf0FI_GL_4",
            )
        )
        self.assertEqual(service, "ZFI_GL_4_SRV")
        self.assertEqual(entity, "EntityOf0FI_GL_4")
        self.assertEqual(table, "EntityOf0FI_GL_4")


class CollisionMessageTests(SimpleTestCase):
    @patch(
        "apps.elttool.services.entity_transform_service.find_destination_name_collision"
    )
    def test_collision_names_first_entity(self, mock_find):
        from apps.elttool.services.entity_transform_service import (
            find_destination_name_collision,
        )

        other = SimpleNamespace(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            destination_table_name="IMO_CMFIGL04",
        )
        mock_find.return_value = other
        hit = find_destination_name_collision(1, "IMO_CMFIGL04")
        self.assertEqual(hit.entity_name, "EntityOf0FI_GL_4")

    def test_schema_key_uses_company_database_and_schema(self):
        from apps.elttool.services.entity_transform_service import (
            destination_schema_key_for_connection,
        )

        connection = SimpleNamespace(
            cmp_id=7,
            dst_schema="finance",
            dst_config_id=3,
            dst_config=SimpleNamespace(data={"database": "analytics"}),
        )
        self.assertEqual(
            destination_schema_key_for_connection(connection),
            "7:ANALYTICS:FINANCE",
        )

    def test_collision_message_names_entity_and_connector(self):
        from apps.elttool.services.entity_transform_service import (
            destination_collision_message,
        )

        collision = SimpleNamespace(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            connection=SimpleNamespace(
                src_config=SimpleNamespace(name="SAP GL"),
                connection_id=2,
            ),
        )
        message = destination_collision_message("IMO_CMFIGL04", collision)
        self.assertIn("ZFI_GL_4_SRV_EntityOf0FI_GL_4", message)
        self.assertIn("SAP GL", message)

    @patch("apps.elttool.services.entity_transform_service.EntityTransform.objects")
    @patch("apps.elttool.services.entity_transform_service.Connection.objects")
    def test_collision_lookup_uses_shared_schema_key(self, mock_conn, mock_rows):
        from apps.elttool.services.entity_transform_service import (
            find_destination_name_collision,
        )

        mock_conn.filter.return_value.select_related.return_value.first.return_value = (
            SimpleNamespace(
                cmp_id=7,
                dst_schema="FINANCE",
                dst_config_id=3,
                dst_config=SimpleNamespace(data={"database": "ANALYTICS"}),
            )
        )
        query = mock_rows.filter.return_value
        query.filter.return_value = query
        query.exclude.return_value = query
        other = SimpleNamespace(entity_name="EntityOf0FI_GL_4")
        query.select_related.return_value.order_by.return_value.first.return_value = other
        hit = find_destination_name_collision(
            1,
            "imo_cmfigl04",
            exclude_service="ZFI_GL_4_SRV",
            exclude_entity="OtherEntity",
        )
        self.assertIs(hit, other)
        query.filter.assert_called_with(destination_schema_key="7:ANALYTICS:FINANCE")


class RenameConfirmationTests(SimpleTestCase):
    @patch("apps.elttool.services.entity_transform_service.EntityTransform.objects")
    @patch(
        "apps.elttool.services.entity_transform_service.find_destination_name_collision",
        return_value=None,
    )
    @patch("apps.elttool.services.entity_transform_service.get_active_transform")
    def test_loaded_data_blocks_silent_rename(self, mock_active, _mock_find, mock_rows):
        from apps.elttool.services.entity_transform_service import save_transform_version

        mock_active.return_value = SimpleNamespace(destination_table_name="OLD_TABLE")
        table_row = SimpleNamespace(
            service_name="ZFI_GL_4_SRV",
            table="EntityOf0FI_GL_4",
            initial_completed_flag=True,
            delta_token=None,
            first_sync_timestamp=None,
            status=None,
        )
        with self.assertRaises(ValueError) as ctx:
            save_transform_version(
                1,
                table_row,
                SimpleNamespace(cmp=None),
                None,
                {},
                None,
                "",
                "filter_script_mapping",
                None,
                destination_name_source="MANUAL",
                destination_table_name="NEW_TABLE",
            )
        self.assertIn("OLD_TABLE", str(ctx.exception))
        self.assertIn("NEW_TABLE", str(ctx.exception))
        mock_rows.create.assert_not_called()

    def test_entity_without_json_keeps_default_name(self):
        resolved = resolve_destination_table_name(
            service_name="ZFI_GL_4_SRV",
            entity_name="EntityOf0FI_GL_4",
            table_name="EntityOf0FI_GL_4",
            mapping_json=None,
            destination_name_source=DESTINATION_NAME_SOURCE_DEFAULT,
        )
        self.assertEqual(resolved["destination_name_source"], DESTINATION_NAME_SOURCE_DEFAULT)
        self.assertNotEqual(resolved["destination_table_name"], "IMO_CMFIGL04")
        self.assertTrue(resolved["destination_table_name"])
