"""Tests for Snowflake→Salesforce failure roll-up helpers (no service imports)."""
import unittest


def failed_table_names(failed_tables) -> set:
    names = set()
    for item in failed_tables or []:
        if isinstance(item, dict):
            name = item.get("table_name")
        else:
            name = item
        if name:
            names.add(str(name))
    return names


class SalesforceFailureRollupTests(unittest.TestCase):
    def test_failed_table_names_from_dict_entries(self):
        failed = [
            {"table_name": "FINANCIALI__C", "error_message": "boom"},
            {"table_name": "OTHER__C", "error_message": "boom"},
        ]
        self.assertEqual(
            failed_table_names(failed),
            {"FINANCIALI__C", "OTHER__C"},
        )

    def test_successful_mapping_filter_avoids_set_on_dicts(self):
        failed = [{"table_name": "BAD__C", "error_message": "err"}]
        all_tables = [
            {"source_table": "GOOD__C", "destination_table": "Good__c"},
            {"source_table": "BAD__C", "destination_table": "Bad__c"},
        ]
        failed_names = failed_table_names(failed)
        successful = [
            mapping for mapping in all_tables
            if str(mapping.get("source_table") or "") not in failed_names
        ]
        self.assertEqual(len(successful), 1)
        self.assertEqual(successful[0]["source_table"], "GOOD__C")

    def test_set_on_failed_dicts_raises_unhashable(self):
        failed = [{"table_name": "X", "error_message": "err"}]
        with self.assertRaises(TypeError):
            set(failed)


if __name__ == "__main__":
    unittest.main()
