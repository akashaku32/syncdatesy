"""Tests for Salesforce JWT/OAuth config sanitization helpers."""

from django.test import SimpleTestCase

from apps.elttool.v1.views import (
    _is_salesforce_jwt_config,
    _merge_salesforce_jwt_fields_on_edit,
    remove_old_key_pair_fields,
    sanitize_salesforce_config_for_auth_type,
)


class SalesforceJwtConfigTests(SimpleTestCase):
    def test_jwt_config_strips_oauth_fields(self):
        cleaned = sanitize_salesforce_config_for_auth_type(
            {
                "authentication_type": "jwt_bearer",
                "CLIENT_ID": "id",
                "refresh_token": "rt",
                "private_key": "pem",
            }
        )
        self.assertNotIn("refresh_token", cleaned)
        self.assertIn("private_key", cleaned)

    def test_oauth_config_strips_jwt_fields(self):
        cleaned = sanitize_salesforce_config_for_auth_type(
            {
                "authentication_type": "oauth",
                "CLIENT_SECRET": "secret",
                "private_key": "pem",
            }
        )
        self.assertNotIn("private_key", cleaned)
        self.assertEqual(cleaned["authentication_type"], "oauth")

    def test_merge_private_key_on_edit(self):
        merged = _merge_salesforce_jwt_fields_on_edit(
            {"private_key": "stored-pem", "integration_username": "user@example.com"},
            {"private_key": "", "integration_username": "user@example.com"},
        )
        self.assertEqual(merged["private_key"], "stored-pem")

    def test_is_salesforce_jwt_config(self):
        self.assertTrue(_is_salesforce_jwt_config({"authentication_type": "jwt_bearer"}))
        self.assertFalse(_is_salesforce_jwt_config({"authentication_type": "oauth"}))

    def test_remove_old_key_pair_fields_preserves_jwt_private_key(self):
        config_data = {
            "authentication_type": "jwt_bearer",
            "private_key": "pem-content",
            "passphrase": "secret",
            "CLIENT_ID": "consumer-key",
        }
        removed = remove_old_key_pair_fields(config_data, config_type="source")
        self.assertEqual(removed, [])
        self.assertEqual(config_data["private_key"], "pem-content")
        self.assertEqual(config_data["passphrase"], "secret")
