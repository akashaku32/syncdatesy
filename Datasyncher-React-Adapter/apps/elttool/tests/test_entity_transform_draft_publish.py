"""Draft autosave vs validate-publish for EntityTransform."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.elttool.models import EntityTransform
from apps.elttool.services import entity_transform_service as ets
from apps.elttool.services.entity_transform_service import (
    get_draft_transform,
    publish_transform_version,
    save_transform_version,
    serialize_transform_detail,
    upsert_draft_transform,
)


def _passthrough_atomic(fn=None, **_kwargs):
    if fn is None:
        return lambda f: f
    return fn


def _table_row(**kwargs):
    defaults = {
        "service_name": "ZSRV",
        "table": "ZSRV_EntityOfX",
        "initial_completed_flag": False,
        "delta_token": None,
        "first_sync_timestamp": None,
        "status": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class EntityTransformDraftPublishTests(SimpleTestCase):
    def setUp(self):
        self._atomic = patch.object(ets.transaction, "atomic", _passthrough_atomic)
        self._atomic.start()
        self.addCleanup(self._atomic.stop)

    def test_serialize_includes_status(self):
        row = SimpleNamespace(
            id=1,
            version=2,
            is_active=True,
            status=EntityTransform.STATUS_VALIDATED,
            service_name="ZSRV",
            entity_name="EntityOfX",
            table_name="ZSRV_EntityOfX",
            mapping_json={"field_mapping": []},
            mapping_overrides={},
            script_text="def transform(__source_df):\n    return __source_df",
            script_sha256="abc",
            entry_point="transform",
            run_order=EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
            row_filter=None,
            json_table_name=None,
            destination_table_name="ENTITY_OF_X",
            destination_name_source="DEFAULT",
            previous_destination_table_name=None,
            destination_rename_mode=None,
            created_at=None,
            created_by_id=None,
        )
        detail = serialize_transform_detail(row)
        assert detail["status"] == "validated"
        assert detail["is_active"] is True

    @patch("apps.elttool.services.entity_transform_service.Log.objects")
    def test_transform_audit_log_is_not_assigned_a_migration_session(self, mock_logs):
        user = SimpleNamespace(cmp=None)

        ets.audit_transform_action(
            user,
            connection=None,
            table_name="CUSTOMER_ORDER",
            action="published",
            script_sha256="8d852dcbd2e8",
        )

        assert mock_logs.create.call_args.kwargs["session"] is None

    @patch(
        "apps.elttool.services.entity_transform_service.resolve_destination_table_name"
    )
    @patch(
        "apps.elttool.services.entity_transform_service.is_valid_snowflake_destination_name",
        return_value=True,
    )
    @patch(
        "apps.elttool.services.entity_transform_service.find_destination_name_collision",
        return_value=None,
    )
    @patch("apps.elttool.services.entity_transform_service.get_active_transform")
    @patch("apps.elttool.services.entity_transform_service.get_draft_transform")
    @patch("apps.elttool.services.entity_transform_service._schema_key_for", return_value="1:DB:PUBLIC")
    @patch("apps.elttool.services.entity_transform_service.EntityTransform.objects")
    def test_upsert_draft_creates_inactive_draft(
        self,
        mock_objects,
        _schema,
        mock_get_draft,
        mock_get_active,
        _collision,
        _valid,
        mock_resolve,
    ):
        mock_resolve.return_value = {
            "destination_table_name": "ENTITY_OF_X",
            "destination_name_source": "DEFAULT",
            "json_table_name": None,
            "warnings": [],
            "default_display_name": "EntityOfX",
        }
        mock_get_active.return_value = None
        mock_get_draft.return_value = None
        mock_objects.filter.return_value.order_by.return_value.values_list.return_value.first.return_value = (
            0
        )
        created = MagicMock()
        created.version = 1
        created.is_active = False
        created.status = EntityTransform.STATUS_DRAFT
        mock_objects.create.return_value = created

        row, meta = upsert_draft_transform(
            connection_id=1,
            table_row=_table_row(),
            user=SimpleNamespace(cmp=None, id=1),
            mapping_json={"field_mapping": []},
            mapping_overrides={},
            script_text="broken (",
            entry_point="transform",
            run_order=EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
            row_filter=None,
        )

        assert row is created
        assert meta["is_draft"] is True
        kwargs = mock_objects.create.call_args.kwargs
        assert kwargs["is_active"] is False
        assert kwargs["status"] == EntityTransform.STATUS_DRAFT
        assert kwargs["script_text"] == "broken ("

    @patch(
        "apps.elttool.services.entity_transform_service.resolve_destination_table_name"
    )
    @patch(
        "apps.elttool.services.entity_transform_service.is_valid_snowflake_destination_name",
        return_value=True,
    )
    @patch(
        "apps.elttool.services.entity_transform_service.find_destination_name_collision",
        return_value=None,
    )
    @patch("apps.elttool.services.entity_transform_service.get_active_transform")
    @patch("apps.elttool.services.entity_transform_service.EntityTransform.objects")
    @patch("apps.elttool.services.entity_transform_service._schema_key_for", return_value="1:DB:PUBLIC")
    @patch(
        "apps.elttool.services.entity_transform_service.audit_destination_name_change"
    )
    def test_publish_activates_and_deletes_draft(
        self,
        _audit,
        _schema,
        mock_objects,
        mock_get_active,
        _collision,
        _valid,
        mock_resolve,
    ):
        mock_resolve.return_value = {
            "destination_table_name": "ENTITY_OF_X",
            "destination_name_source": "DEFAULT",
            "json_table_name": None,
            "warnings": [],
            "default_display_name": "EntityOfX",
        }
        mock_get_active.return_value = None
        mock_objects.filter.return_value.order_by.return_value.values_list.return_value.first.return_value = (
            1
        )
        created = MagicMock()
        created.version = 2
        created.is_active = True
        created.status = EntityTransform.STATUS_VALIDATED
        created.destination_name_source = "DEFAULT"
        mock_objects.create.return_value = created

        row, meta = publish_transform_version(
            connection_id=1,
            table_row=_table_row(),
            user=SimpleNamespace(cmp=None, id=1),
            mapping_json={"field_mapping": []},
            mapping_overrides={},
            script_text="import pandas as pd\n\ndef transform(__source_df):\n    return __source_df.copy()\n",
            entry_point="transform",
            run_order=EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
            row_filter=None,
        )

        assert meta.get("published") is True
        assert row is created
        # Draft delete + deactivate active filters
        assert mock_objects.filter.call_count >= 2
        kwargs = mock_objects.create.call_args.kwargs
        assert kwargs["is_active"] is True
        assert kwargs["status"] == EntityTransform.STATUS_VALIDATED

    @patch(
        "apps.elttool.services.entity_transform_service.publish_transform_version"
    )
    @patch(
        "apps.elttool.services.entity_transform_service.upsert_draft_transform"
    )
    def test_save_transform_version_routes_activate_flag(
        self, mock_upsert, mock_publish
    ):
        mock_upsert.return_value = ("draft", {"is_draft": True})
        mock_publish.return_value = ("pub", {"published": True})
        kwargs = dict(
            connection_id=1,
            table_row=_table_row(),
            user=SimpleNamespace(),
            mapping_json=None,
            mapping_overrides={},
            script_text="",
            entry_point="",
            run_order=EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
            row_filter=None,
        )
        save_transform_version(**kwargs, activate=False)
        mock_upsert.assert_called_once()
        mock_publish.assert_not_called()

        mock_upsert.reset_mock()
        save_transform_version(**kwargs, activate=True)
        mock_publish.assert_called_once()
        mock_upsert.assert_not_called()

    def test_get_draft_transform_filters_status(self):
        with patch(
            "apps.elttool.services.entity_transform_service.EntityTransform.objects"
        ) as mock_objects:
            mock_objects.filter.return_value.order_by.return_value.first.return_value = (
                None
            )
            assert get_draft_transform(1, "ZSRV", "EntityOfX") is None
            mock_objects.filter.assert_called_once_with(
                connection_id=1,
                service_name="ZSRV",
                entity_name="EntityOfX",
                status=EntityTransform.STATUS_DRAFT,
            )
