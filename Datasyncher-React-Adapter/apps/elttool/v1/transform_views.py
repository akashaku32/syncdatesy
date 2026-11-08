"""API views for entity mapping + transform."""

from __future__ import annotations

import json
import logging
from typing import Optional

import requests
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.elttool.models import EntityTransform, SourceTableSelection
from apps.elttool.services.entity_transform_service import (
    audit_transform_action,
    destination_name_preview,
    get_active_transform,
    get_draft_transform,
    parse_entity_names,
    publish_transform_version,
    save_transform_version,
    serialize_transform_detail,
    serialize_transform_summary,
    upsert_draft_transform,
)
from apps.elttool.services.tenant_scope import get_connection_for_user
from apps.elttool.transform_limits import (
    MAX_MAPPING_JSON_BYTES,
    MAX_SCRIPT_BYTES,
)
from apps.elttool.v1.permissions import CanManageTransform
from apps.elttool.v1.sap_odata_views import _fastapi_url, _inject_proxy_agent
from apps.elttool.v1.utils import internal_service_headers

logger = logging.getLogger(__name__)


def _get_table_row(user, connection_id: int, table_name: str) -> SourceTableSelection:
    get_connection_for_user(user, connection_id)
    return get_object_or_404(
        SourceTableSelection,
        connection_id=connection_id,
        table=table_name,
    )


def _payload_too_large(message: str) -> Response:
    return Response({"message": message}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)


def _parse_mapping_json(body: dict):
    mapping_json = body.get("mapping_json")
    if mapping_json is not None and isinstance(mapping_json, str):
        if len(mapping_json.encode("utf-8")) > MAX_MAPPING_JSON_BYTES:
            raise ValueError("__payload_too_large__:Mapping JSON exceeds size limit.")
        mapping_json = json.loads(mapping_json)
    return mapping_json


def _check_script_size(script_text) -> Optional[Response]:
    if script_text and len(script_text.encode("utf-8")) > MAX_SCRIPT_BYTES:
        return _payload_too_large("Script exceeds size limit.")
    return None


class EntityTransformAPIView(APIView):
    """GET active + draft + history; POST upserts draft (never activates)."""

    permission_classes = [IsAuthenticated, CanManageTransform]

    def get(self, request, connection_id, table_name):
        table_row = _get_table_row(request.user, connection_id, table_name)
        service, entity, _ = parse_entity_names(table_row)
        active = get_active_transform(connection_id, service, entity)
        draft = get_draft_transform(connection_id, service, entity)
        history = (
            EntityTransform.objects.filter(
                connection_id=connection_id,
                service_name=service,
                entity_name=entity,
            )
            .order_by("-version")
            .values(
                "id",
                "version",
                "is_active",
                "status",
                "created_at",
                "script_sha256",
                "entry_point",
            )
        )
        return Response(
            {
                "active": serialize_transform_detail(active, include_script=True)
                if active
                else None,
                "draft": serialize_transform_detail(draft, include_script=True)
                if draft
                else None,
                "summary": serialize_transform_summary(active),
                "history": list(history),
            }
        )

    def post(self, request, connection_id, table_name):
        """Autosave draft. Use validate?publish=1 to publish a validated version."""
        table_row = _get_table_row(request.user, connection_id, table_name)
        body = request.data if isinstance(request.data, dict) else {}

        try:
            mapping_json = _parse_mapping_json(body)
        except ValueError as exc:
            msg = str(exc)
            if msg.startswith("__payload_too_large__:"):
                return _payload_too_large(msg.split(":", 1)[1])
            raise

        script_text = body.get("script_text")
        oversized = _check_script_size(script_text)
        if oversized:
            return oversized

        activate = bool(body.get("activate"))
        try:
            row, meta = save_transform_version(
                connection_id=connection_id,
                table_row=table_row,
                user=request.user,
                mapping_json=mapping_json,
                mapping_overrides=body.get("mapping_overrides"),
                script_text=script_text,
                entry_point=body.get("entry_point") or "",
                run_order=body.get("run_order")
                or EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
                row_filter=body.get("row_filter"),
                destination_name_source=body.get("destination_name_source"),
                destination_table_name=body.get("destination_table_name"),
                destination_rename_mode=body.get("destination_rename_mode"),
                confirm_use_existing_table=bool(
                    body.get("confirm_use_existing_table")
                ),
                activate=activate,
            )
        except ValueError as exc:
            message = str(exc)
            payload = {"message": message, "error": message}
            lower = message.lower()
            if "create new table" in lower or "rename existing" in lower:
                payload["requires_rename_confirmation"] = True
                payload["previous_destination_table_name"] = body.get(
                    "previous_destination_table_name"
                )
                payload["destination_table_name"] = body.get("destination_table_name")
            if "already used by entity" in lower or "collides" in lower:
                payload["collision"] = True
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

        connection = get_connection_for_user(request.user, connection_id)
        audit_transform_action(
            request.user,
            connection,
            table_name,
            "published" if activate else "draft_saved",
            row.script_sha256,
        )
        detail = serialize_transform_detail(row)
        detail["save_meta"] = meta
        return Response(
            detail,
            status=status.HTTP_201_CREATED if activate else status.HTTP_200_OK,
        )

    def delete(self, request, connection_id, table_name):
        table_row = _get_table_row(request.user, connection_id, table_name)
        service, entity, _ = parse_entity_names(table_row)
        updated = EntityTransform.objects.filter(
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
            is_active=True,
        ).update(is_active=False)
        EntityTransform.objects.filter(
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
            status=EntityTransform.STATUS_DRAFT,
        ).delete()
        connection = get_connection_for_user(request.user, connection_id)
        audit_transform_action(request.user, connection, table_name, "deactivated")
        return Response({"deactivated": updated > 0})


class EntityTransformActivateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageTransform]

    def post(self, request, connection_id, table_name, version):
        table_row = _get_table_row(request.user, connection_id, table_name)
        service, entity, _ = parse_entity_names(table_row)
        target = get_object_or_404(
            EntityTransform,
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
            version=version,
        )
        if target.status == EntityTransform.STATUS_DRAFT:
            return Response(
                {
                    "message": (
                        "Cannot activate a draft. Use Validate to publish a "
                        "validated version."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        EntityTransform.objects.filter(
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
            is_active=True,
        ).update(is_active=False)
        target.is_active = True
        target.status = EntityTransform.STATUS_VALIDATED
        target.save(update_fields=["is_active", "status"])
        connection = get_connection_for_user(request.user, connection_id)
        audit_transform_action(
            request.user, connection, table_name, f"activated_v{version}", target.script_sha256
        )
        return Response(serialize_transform_detail(target))


class EntityTransformValidateAPIView(APIView):
    """Validate mapping/script. When ``publish=true`` and valid, activates a new version."""

    permission_classes = [IsAuthenticated, CanManageTransform]

    def post(self, request, connection_id, table_name):
        table_row = _get_table_row(request.user, connection_id, table_name)
        body = request.data if isinstance(request.data, dict) else {}

        mapping_json = body.get("mapping_json")
        script_text = body.get("script_text") or ""
        publish = bool(body.get("publish"))

        if isinstance(mapping_json, str):
            if len(mapping_json.encode("utf-8")) > MAX_MAPPING_JSON_BYTES:
                return _payload_too_large("Mapping JSON exceeds size limit.")
        if script_text and len(script_text.encode("utf-8")) > MAX_SCRIPT_BYTES:
            return _payload_too_large("Script exceeds size limit.")

        connection = get_connection_for_user(
            request.user,
            connection_id,
            select_related=("src_config", "dst_config", "dst_config__dst"),
        )
        credentials = connection.src_config.data if connection.src_config else {}
        if isinstance(credentials, dict):
            credentials = _inject_proxy_agent(credentials, request.user.cmp_id)

        parsed_mapping = mapping_json
        if isinstance(parsed_mapping, str):
            try:
                parsed_mapping = json.loads(parsed_mapping)
            except json.JSONDecodeError:
                parsed_mapping = None

        payload = {
            "connection_id": connection_id,
            "table_name": table_name,
            "service_name": table_row.service_name,
            "mapping_json": mapping_json,
            "mapping_overrides": body.get("mapping_overrides") or {},
            "script_text": script_text,
            "entry_point": body.get("entry_point") or "",
            "table_fields": table_row.table_fields or {},
            "primary_keys": table_row.primary_keys or [],
        }

        try:
            resp = requests.post(
                _fastapi_url("entity-transform/validate"),
                json=payload,
                headers=internal_service_headers(),
                timeout=120,
            )
            if resp.status_code == 413:
                return _payload_too_large(resp.json().get("detail", "Payload too large"))
            data = resp.json() if resp.content else {}
            if isinstance(data, dict):
                dest_preview = destination_name_preview(
                    connection,
                    table_row,
                    parsed_mapping if isinstance(parsed_mapping, dict) else None,
                    destination_name_source=body.get("destination_name_source"),
                    destination_table_name=body.get("destination_table_name"),
                )
                data["destination"] = dest_preview
                if dest_preview.get("error"):
                    data.setdefault("errors", []).append(
                        {"path": "destination_table_name", "message": dest_preview["error"]}
                    )
                    data["valid"] = False
                for warning in dest_preview.get("warnings") or []:
                    data.setdefault("warnings", []).append(
                        {"type": "destination_table_name", "message": warning}
                    )

                # Always keep draft in sync while validating (even on failure).
                try:
                    draft_row, draft_meta = upsert_draft_transform(
                        connection_id=connection_id,
                        table_row=table_row,
                        user=request.user,
                        mapping_json=parsed_mapping
                        if isinstance(parsed_mapping, dict)
                        else mapping_json
                        if isinstance(mapping_json, dict)
                        else None,
                        mapping_overrides=body.get("mapping_overrides"),
                        script_text=script_text,
                        entry_point=body.get("entry_point") or "",
                        run_order=body.get("run_order")
                        or EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
                        row_filter=body.get("row_filter"),
                        destination_name_source=body.get("destination_name_source"),
                        destination_table_name=body.get("destination_table_name"),
                        destination_rename_mode=body.get("destination_rename_mode"),
                        confirm_use_existing_table=bool(
                            body.get("confirm_use_existing_table")
                        ),
                    )
                    data["draft"] = serialize_transform_detail(draft_row)
                    data["draft_meta"] = draft_meta
                except Exception as draft_exc:
                    logger.warning(
                        "Draft upsert during validate failed: %s", draft_exc, exc_info=True
                    )

                if publish and data.get("valid"):
                    try:
                        published, pub_meta = publish_transform_version(
                            connection_id=connection_id,
                            table_row=table_row,
                            user=request.user,
                            mapping_json=parsed_mapping
                            if isinstance(parsed_mapping, dict)
                            else mapping_json
                            if isinstance(mapping_json, dict)
                            else None,
                            mapping_overrides=body.get("mapping_overrides"),
                            script_text=script_text,
                            entry_point=body.get("entry_point") or "",
                            run_order=body.get("run_order")
                            or EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
                            row_filter=body.get("row_filter"),
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
                        data["publish_meta"] = pub_meta
                        data["draft"] = None
                    except ValueError as exc:
                        message = str(exc)
                        data["valid"] = False
                        data.setdefault("errors", []).append(
                            {"path": "publish", "message": message}
                        )
                        data["publish_error"] = message
                        lower = message.lower()
                        if "create new table" in lower or "rename existing" in lower:
                            data["requires_rename_confirmation"] = True

            return Response(data, status=resp.status_code)
        except requests.Timeout as exc:
            logger.exception("Transform validate proxy timed out")
            return Response(
                {"message": f"Validation timed out: {exc}"},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.exception("Transform validate proxy failed")
            return Response(
                {"message": f"Validation service unavailable: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class EntityTransformDryRunAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageTransform]

    def post(self, request, connection_id, table_name):
        table_row = _get_table_row(request.user, connection_id, table_name)
        body = request.data if isinstance(request.data, dict) else {}

        connection = get_connection_for_user(
            request.user,
            connection_id,
            select_related=("src_config",),
        )
        credentials = connection.src_config.data if connection.src_config else {}
        if isinstance(credentials, dict):
            credentials = _inject_proxy_agent(credentials, request.user.cmp_id)

        payload = {
            "connection_id": connection_id,
            "table_name": table_name,
            "service_name": table_row.service_name,
            "mapping_json": body.get("mapping_json"),
            "mapping_overrides": body.get("mapping_overrides") or {},
            "script_text": body.get("script_text") or "",
            "entry_point": body.get("entry_point") or "",
            "run_order": body.get("run_order") or EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
            "sample_size": body.get("sample_size"),
            "table_fields": table_row.table_fields or {},
            "primary_keys": table_row.primary_keys or [],
            "source_credentials": credentials or {},
        }

        try:
            resp = requests.post(
                _fastapi_url("entity-transform/dry-run"),
                json=payload,
                headers=internal_service_headers(),
                timeout=getattr(settings, "TRANSFORM_DRY_RUN_TIMEOUT", 300),
            )
            if resp.status_code == 413:
                return _payload_too_large(resp.json().get("detail", "Payload too large"))
            data = resp.json() if resp.content else {}
            return Response(data, status=resp.status_code)
        except requests.Timeout as exc:
            logger.exception("Transform dry-run proxy timed out")
            return Response(
                {"message": f"Dry-run timed out: {exc}"},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.exception("Transform dry-run proxy failed")
            return Response(
                {"message": f"Dry-run service unavailable: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
