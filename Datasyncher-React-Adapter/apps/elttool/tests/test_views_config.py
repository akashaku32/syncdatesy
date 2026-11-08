"""Tests for config parsing helpers used by pipeline execution (ensure_config_is_dict)."""
import json
import unittest

from apps.elttool.v1.views import ensure_config_is_dict, parse_config_data_safely


class ParseConfigDataSafelyTests(unittest.TestCase):
    def test_returns_dict_unchanged(self):
        payload = {"host": "example.com", "schema": "PUBLIC"}
        self.assertEqual(parse_config_data_safely(payload), payload)

    def test_parses_json_string(self):
        payload = '{"account": "x", "schema": "RAW"}'
        self.assertEqual(
            parse_config_data_safely(payload),
            {"account": "x", "schema": "RAW"},
        )

    def test_parses_utf8_bytes(self):
        payload = b'{"key": "value"}'
        self.assertEqual(parse_config_data_safely(payload), {"key": "value"})

    def test_none_returns_empty_dict_by_default(self):
        self.assertEqual(parse_config_data_safely(None), {})

    def test_none_raises_when_raise_on_error(self):
        with self.assertRaises(ValueError) as ctx:
            parse_config_data_safely(None, config_name="source_config", raise_on_error=True)
        self.assertIn("source_config is None", str(ctx.exception))

    def test_invalid_json_returns_empty_dict_by_default(self):
        self.assertEqual(parse_config_data_safely("{not json"), {})

    def test_invalid_json_raises_when_raise_on_error(self):
        with self.assertRaises(ValueError) as ctx:
            parse_config_data_safely(
                "{bad",
                config_name="destination_config",
                raise_on_error=True,
            )
        self.assertIn("destination_config", str(ctx.exception))

    def test_non_dict_json_returns_empty_dict_by_default(self):
        self.assertEqual(parse_config_data_safely('["a", "b"]'), {})

    def test_unexpected_type_raises_when_raise_on_error(self):
        with self.assertRaises(ValueError) as ctx:
            parse_config_data_safely(42, config_name="source_config", raise_on_error=True)
        self.assertIn("unexpected type", str(ctx.exception))


class EnsureConfigIsDictTests(unittest.TestCase):
    def test_accepts_dict(self):
        cfg = {"schema": "PUBLIC", "warehouse": "WH"}
        self.assertEqual(ensure_config_is_dict(cfg, "destination_config"), cfg)

    def test_parses_json_string(self):
        cfg = json.dumps({"schema": "RAW"})
        self.assertEqual(ensure_config_is_dict(cfg, "destination_config"), {"schema": "RAW"})

    def test_raises_on_none(self):
        with self.assertRaises(ValueError):
            ensure_config_is_dict(None, "source_config")

    def test_raises_on_invalid_json(self):
        with self.assertRaises(ValueError):
            ensure_config_is_dict("not-json", "source_config")


if __name__ == "__main__":
    unittest.main()
