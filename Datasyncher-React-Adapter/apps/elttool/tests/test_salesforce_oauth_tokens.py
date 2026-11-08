"""Tests for Salesforce OAuth token persistence service."""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.elttool.services.salesforce_oauth_tokens import (
    get_salesforce_oauth_tokens,
    update_salesforce_oauth_tokens,
)


class SalesforceOAuthTokensServiceTests(TestCase):
    def test_update_skips_when_refresh_token_already_rotated(self):
        connection = MagicMock()
        connection.src_config_id = 10
        connection.dst_config_id = None

        config = MagicMock()
        config.data = {
            "access_token": "old-access",
            "instance_url": "https://example.sandbox.my.salesforce.com",
            "refresh_token": "token-v2",
        }

        with patch(
            "apps.elttool.services.salesforce_oauth_tokens.Connection.objects"
        ) as conn_mgr, patch(
            "apps.elttool.services.salesforce_oauth_tokens.SourceConfig.objects"
        ) as src_mgr, patch(
            "apps.elttool.services.salesforce_oauth_tokens.transaction.atomic"
        ):
            conn_mgr.select_related.return_value.get.return_value = connection
            src_mgr.select_for_update.return_value.get.return_value = config

            ok, message = update_salesforce_oauth_tokens(
                26,
                "source",
                access_token="new-access",
                instance_url="https://example.sandbox.my.salesforce.com",
                refresh_token="token-v3",
                previous_refresh_token="token-v1",
            )

        self.assertTrue(ok)
        self.assertEqual(message, "skipped_stale_refresh_token")
        config.save.assert_not_called()

    def test_get_parses_json_string_config(self):
        connection = MagicMock()
        connection.dst_config = MagicMock()
        connection.dst_config.data = json.dumps(
            {
                "access_token": "a",
                "instance_url": "https://x.salesforce.com",
                "refresh_token": "r",
            }
        )

        with patch(
            "apps.elttool.services.salesforce_oauth_tokens.Connection.objects"
        ) as conn_mgr:
            conn_mgr.select_related.return_value.get.return_value = connection
            tokens = get_salesforce_oauth_tokens(27, "destination")

        self.assertEqual(tokens["refresh_token"], "r")

    def test_get_returns_token_fields(self):
        connection = MagicMock()
        connection.src_config = MagicMock()
        connection.src_config.data = {
            "access_token": "a",
            "instance_url": "https://x.salesforce.com",
            "refresh_token": "r",
            "CLIENT_ID": "cid",
        }

        with patch(
            "apps.elttool.services.salesforce_oauth_tokens.Connection.objects"
        ) as conn_mgr:
            conn_mgr.select_related.return_value.get.return_value = connection
            tokens = get_salesforce_oauth_tokens(26, "source")

        self.assertEqual(tokens["refresh_token"], "r")
        self.assertEqual(tokens["CLIENT_ID"], "cid")
