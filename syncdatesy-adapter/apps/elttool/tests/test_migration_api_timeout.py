"""Tests for Django -> migration-function HTTP timeout selection."""

import os
from unittest import mock

from django.test import SimpleTestCase

from apps.elttool.v1.utils import (
    DEFAULT_MIGRATION_API_TIMEOUT_SECONDS,
    DEFAULT_SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS,
    migration_api_timeout_seconds,
)


class MigrationApiTimeoutTests(SimpleTestCase):
    def test_default_timeout_for_generic_connectors(self):
        self.assertEqual(migration_api_timeout_seconds("Snowflake"), 30)
        self.assertEqual(
            migration_api_timeout_seconds(None),
            DEFAULT_MIGRATION_API_TIMEOUT_SECONDS,
        )

    def test_salesforce_uses_longer_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS", None)
            self.assertEqual(
                migration_api_timeout_seconds("Salesforce"),
                DEFAULT_SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS,
            )
            self.assertEqual(
                migration_api_timeout_seconds("SalesforceSandbox"),
                DEFAULT_SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS,
            )

    def test_salesforce_respects_env_override(self):
        with mock.patch.dict(
            os.environ,
            {"SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS": "180"},
            clear=False,
        ):
            self.assertEqual(migration_api_timeout_seconds("Salesforce"), 180)

    def test_combined_source_and_destination_uses_longest_timeout(self):
        with mock.patch.dict(
            os.environ,
            {
                "SALESFORCE_AUTH_REQUEST_TIMEOUT_SECONDS": "120",
                "DATABRICKS_AUTH_REQUEST_TIMEOUT_SECONDS": "960",
            },
            clear=False,
        ):
            self.assertEqual(
                migration_api_timeout_seconds("Salesforce", "Databricks"),
                960,
            )
            self.assertEqual(
                migration_api_timeout_seconds("SalesforceSandbox", "Snowflake"),
                120,
            )
