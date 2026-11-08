"""Exercise the PK proxy without initializing Django or connecting to a database."""
import ast
import json
import logging
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import requests


class S3PKMappingTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).parents[1] / "v1" / "views.py"
        names = {"suggest_s3_primary_keys", "parse_s3_mapping_json", "normalize_s3_base_prefix",
                 "overlay_s3_mapping_from_source_file_mapping", "extract_api_error_message",
                 "_normalize_pk_suggestion_response"}
        nodes = [n for n in ast.parse(source.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        for node in nodes:
            node.decorator_list = []
        self.connection = SimpleNamespace(
            src_config=SimpleNamespace(data={"s3_bucket": "test-bucket", "base_folder_path": "data/"}),
            dst_config=None,
            source_file_mapping=SimpleNamespace(file_mapping_mode="single_table_per_file",
                single_file_table_mapping={"data/products.csv": "PRODUCTS"}),
        )
        self.model = Mock()
        self.model.DoesNotExist = type("DoesNotExist", (Exception,), {})
        self.model.objects.select_related.return_value.get.return_value = self.connection
        self.post = Mock()
        self.post.return_value.json.return_value = {"status": "success", "tables": []}
        self.ns = dict(json=json, re=re, logger=logging.getLogger(__name__), domain="example.test",
            Connection=self.model, SourceFileMapping=SimpleNamespace(DoesNotExist=LookupError),
            ensure_config_is_dict=lambda value, name: value,
            JsonResponse=lambda data, **kwargs: (data, kwargs.get("status", 200)),
            requests=SimpleNamespace(post=self.post, HTTPError=requests.HTTPError,
                                     RequestException=requests.RequestException))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.ns)
        self.payload = {"connection_id": 36, "s3_bucket": "test-bucket", "base_folder_path": "data/",
            "file_type": "csv", "file_mapping_method": "single_table_per_file",
            "single_file_table_mapping": "{}", "load_method": "upsert_custom_key"}

    def call(self):
        return self.ns["suggest_s3_primary_keys"](SimpleNamespace(
            method="POST", body=json.dumps(self.payload), user=SimpleNamespace(cmp_id=7)))

    def test_empty_mapping_restored_for_edit(self):
        self.assertEqual(self.call()[1], 200)
        sent = self.post.call_args.kwargs["json"]
        self.assertEqual(sent["single_file_table_mapping"], {"data/products.csv": "PRODUCTS"})
        self.model.objects.select_related.return_value.get.assert_called_once_with(connection_id=36, cmp_id=7)

    def test_explicit_mapping_preserved(self):
        self.payload["single_file_table_mapping"] = '{"data/new.csv":"NEW"}'
        self.call()
        self.assertEqual(self.post.call_args.kwargs["json"]["single_file_table_mapping"], {"data/new.csv": "NEW"})

    def test_nested_payload(self):
        self.payload = {"connection_id": 36, "s3": self.payload}
        self.assertEqual(self.call()[1], 200)
        self.assertEqual(self.post.call_args.kwargs["json"]["s3"]["single_file_table_mapping"],
                         {"data/products.csv": "PRODUCTS"})

    def test_no_saved_mapping_returns_actionable_error(self):
        self.connection.source_file_mapping.single_file_table_mapping = {}
        data, status = self.call()
        self.assertEqual(status, 400)
        self.assertIn("Select and map S3 files", data["message"])
        self.post.assert_not_called()

    def test_changed_bucket_or_folder_does_not_reuse_mapping(self):
        for field, value in (("s3_bucket", "another-bucket"), ("base_folder_path", "another-folder/")):
            with self.subTest(field=field):
                old = self.payload[field]
                self.payload[field] = value
                self.assertEqual(self.call()[1], 400)
                self.payload[field] = old
        self.post.assert_not_called()

    def test_inaccessible_connection(self):
        self.model.objects.select_related.return_value.get.side_effect = self.model.DoesNotExist
        self.assertEqual(self.call()[1], 404)
        self.post.assert_not_called()

    def test_backend_validation_details_preserved(self):
        response = requests.Response()
        response.status_code = 422
        response._content = b'{"detail":"Invalid format options"}'
        self.post.return_value.raise_for_status.side_effect = requests.HTTPError(response=response)
        data, status = self.call()
        self.assertEqual((status, data["message"]), (422, "Invalid format options"))


if __name__ == "__main__":
    unittest.main()
