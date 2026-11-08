from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.http import Http404
from django.test import SimpleTestCase

from apps.elttool.services.tenant_scope import get_connection_for_user


class TenantScopeTests(SimpleTestCase):
    def test_raises_when_user_has_no_company(self):
        user = SimpleNamespace(cmp=None)
        with self.assertRaises(Http404):
            get_connection_for_user(user, 1)

    @patch("apps.elttool.services.tenant_scope.get_object_or_404")
    @patch("apps.elttool.services.tenant_scope.Connection.objects")
    def test_filters_by_company_and_select_related(self, mock_manager, mock_get):
        user = SimpleNamespace(cmp=SimpleNamespace(pk=7))
        chain = mock_manager.filter.return_value
        chain.select_related.return_value = chain
        mock_get.return_value = MagicMock(connection_id=42)

        conn = get_connection_for_user(
            user,
            42,
            select_related=("src_config", "dst_config"),
        )

        mock_manager.filter.assert_called_once_with(cmp=user.cmp)
        chain.select_related.assert_called_once_with("src_config", "dst_config")
        mock_get.assert_called_once_with(chain, connection_id=42)
        self.assertEqual(conn.connection_id, 42)
