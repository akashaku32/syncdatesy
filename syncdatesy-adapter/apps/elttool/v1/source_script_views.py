"""API views for generic source-side script validation and dry-run."""

from __future__ import annotations

import logging

import requests
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.elttool.models import EntityTransform, SourceTableSelection
from apps.elttool.services.entity_transform_service import (
    audit_transform_action,
    publish_transform_version,
    serialize_transform_detail,
)
from apps.elttool.services.tenant_scope import get_connection_for_user
from apps.elttool.transform_limits import MAX_SCRIPT_BYTES
from apps.elttool.v1.permissions import CanManageTransform
from apps.elttool.v1.sap_odata_views import _fastapi_url
from apps.elttool.v1.utils import internal_service_headers

logger = logging.getLogger(__name__)


class SourceScriptValidatePublishAPIView(APIView):
    """Validate and optionally publish source Python without mapping analysis.

    This mirrors the publish portion of the entity-transform endpoint while
    deliberately sending no mapping JSON to the validator or version service.
    File exports only need script validation and the existing transform version
    storage; SAP mapping validation belongs to the SAP transform endpoint.
    """

    permission_classes = [IsAuthenticated, CanManageTransform]

    def post(self, request, connection_id, table_name):
        body = request.data if isinstance(request.data, dict) else {}
        connection = get_connection_for_user(request.user, connection_id)
        table_row = get_object_or_404(
            SourceTableSelection,
            connection_id=connection_id,
            table=table_name,
        )
        script_text = body.get("script_text") or ""
        entry_point = body.get("entry_point") or ""

        if script_text and len(script_text.encode("utf-8")) > MAX_SCRIPT_BYTES:
            return Response(
                {"message": "Script exceeds size limit."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            response = requests.post(
                _fastapi_url("source-script/validate"),
                json={
                    "script_text": script_text,
                    "entry_point": entry_point,
                },
                headers=internal_service_headers(),
                timeout=120,
            )
            data = response.json() if response.content else {}
        except requests.Timeout as exc:
            logger.exception("Source script validation timed out")
            return Response(
                {"message": f"Validation timed out: {exc}"},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.exception("Source script validation proxy failed")
            return Response(
                {"message": f"Validation service unavailable: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if response.status_code >= 400 or not isinstance(data, dict):
            return Response(data, status=response.status_code)

        if not body.get("publish") or not data.get("valid"):
            return Response(data, status=response.status_code)

        try:
            published, publish_meta = publish_transform_version(
                connection_id=connection_id,
                table_row=table_row,
                user=request.user,
                mapping_json=None,
                mapping_overrides={},
                script_text=script_text,
                entry_point=entry_point,
                run_order=body.get("run_order")
                or EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
                row_filter=None,
                destination_name_source=body.get("destination_name_source"),
                destination_table_name=body.get("destination_table_name"),
                destination_rename_mode=body.get("destination_rename_mode"),
                confirm_use_existing_table=bool(
                    body.get("confirm_use_existing_table")
                ),
            )
            audit_transform_action(
                request.user,
                connection,
                table_name,
                "published",
                published.script_sha256,
            )
            data["published"] = serialize_transform_detail(published)
            data["publish_meta"] = publish_meta
        except ValueError as exc:
            message = str(exc)
            data["valid"] = False
            data.setdefault("errors", []).append(
                {"path": "publish", "message": message}
            )
            data["publish_error"] = message

        return Response(data, status=response.status_code)


class SnowflakeSourceScriptDryRunAPIView(APIView):
    """Run a source Python script against a bounded Snowflake sample."""

    permission_classes = [IsAuthenticated, CanManageTransform]

    def post(self, request, connection_id, table_name):
        body = request.data if isinstance(request.data, dict) else {}
        try:
            connection = get_connection_for_user(
                request.user,
                connection_id,
                select_related=("src_config",),
            )
        except Exception:
            return Response(
                {"message": "Connection not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        get_object_or_404(
            SourceTableSelection,
            connection_id=connection.pk,
            table=table_name,
        )
        credentials = connection.src_config.data if connection.src_config else {}
        try:
            response = requests.post(
                _fastapi_url("source-script/snowflake/dry-run"),
                json={
                    "source_config": credentials if isinstance(credentials, dict) else {},
                    "table_name": table_name,
                    "selected_fields": body.get("selected_fields"),
                    "sort_columns": body.get("sort_columns"),
                    "sample_size": body.get("sample_size") or 100,
                    "script_text": body.get("script_text") or "",
                    "entry_point": body.get("entry_point") or "",
                },
                headers=internal_service_headers(),
                timeout=300,
            )
            return Response(response.json() if response.content else {}, response.status_code)
        except requests.Timeout as exc:
            logger.exception("Snowflake source script dry-run timed out")
            return Response(
                {"message": f"Dry-run timed out: {exc}"},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.exception("Snowflake source script dry-run proxy failed")
            return Response(
                {"message": f"Dry-run service unavailable: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
