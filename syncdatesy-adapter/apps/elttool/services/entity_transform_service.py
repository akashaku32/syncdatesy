"""Helpers for EntityTransform CRUD and summaries."""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional, Tuple

from django.db import IntegrityError, transaction

from apps.elttool.models import Connection, EntityTransform, Log, SourceTableSelection
from apps.elttool.services.snowflake_destination_name import (
    DESTINATION_NAME_SOURCE_DEFAULT,
    DESTINATION_NAME_SOURCE_MANUAL,
    default_destination_display_name,
    is_valid_snowflake_destination_name,
    resolve_destination_table_name,
)

logger = logging.getLogger(__name__)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_entity_names(table_row: SourceTableSelection) -> tuple[str, str, str]:
    service = (table_row.service_name or "").strip()
    table = table_row.table
    if service and table.startswith(f"{service}_"):
        entity = table[len(service) + 1 :]
    elif "/" in table:
        parts = table.split("/")
        entity = parts[-1]
        if not service:
            service = parts[0] if len(parts) > 1 else ""
    else:
        entity = table
    return service, entity, table


def transform_badge(active: Optional[EntityTransform]) -> Optional[str]:
    if not active:
        return None
    has_mapping = bool(active.mapping_json)
    has_script = bool(active.script_text and active.script_text.strip())
    if has_mapping and has_script:
        return f"Mapped · Script v{active.version}"
    if has_mapping:
        return "Mapped"
    if has_script:
        return f"Script v{active.version}"
    return None


def serialize_transform_summary_flags(
    version: Optional[int] = None,
    has_mapping: bool = False,
    has_script: bool = False,
    entry_point: Optional[str] = None,
    run_order: Optional[str] = None,
    destination_table_name: Optional[str] = None,
    destination_name_source: Optional[str] = None,
    json_table_name: Optional[str] = None,
) -> Dict[str, Any]:
    badge = None
    if has_mapping and has_script:
        badge = f"Mapped · Script v{version}"
    elif has_mapping:
        badge = "Mapped"
    elif has_script:
        badge = f"Script v{version}"
    return {
        "has_mapping": has_mapping,
        "has_script": has_script,
        "version": version,
        "badge": badge,
        "is_active": bool(has_mapping or has_script),
        "entry_point": entry_point or None,
        "run_order": run_order,
        "destination_table_name": destination_table_name,
        "destination_name_source": destination_name_source,
        "json_table_name": json_table_name,
    }


def serialize_transform_summary(active: Optional[EntityTransform]) -> Dict[str, Any]:
    if not active:
        return serialize_transform_summary_flags()
    return serialize_transform_summary_flags(
        version=active.version,
        has_mapping=bool(active.mapping_json),
        has_script=bool(active.script_text and active.script_text.strip()),
        entry_point=active.entry_point,
        run_order=active.run_order,
        destination_table_name=active.destination_table_name,
        destination_name_source=active.destination_name_source,
        json_table_name=active.json_table_name,
    )


def serialize_transform_detail(row: EntityTransform, include_script: bool = True) -> Dict[str, Any]:
    service, entity, _ = row.service_name, row.entity_name, row.table_name
    status = getattr(row, "status", None) or (
        EntityTransform.STATUS_VALIDATED
        if row.is_active
        else EntityTransform.STATUS_DRAFT
    )
    return {
        "id": row.id,
        "version": row.version,
        "is_active": row.is_active,
        "status": status,
        "service_name": row.service_name,
        "entity_name": row.entity_name,
        "table_name": row.table_name,
        "mapping_json": row.mapping_json,
        "mapping_overrides": row.mapping_overrides or {},
        "script_text": row.script_text if include_script else None,
        "script_sha256": row.script_sha256,
        "entry_point": row.entry_point,
        "run_order": row.run_order,
        "row_filter": row.row_filter,
        "json_table_name": row.json_table_name,
        "destination_table_name": row.destination_table_name,
        "destination_name_source": row.destination_name_source
        or EntityTransform.DESTINATION_NAME_DEFAULT,
        "previous_destination_table_name": row.previous_destination_table_name,
        "destination_rename_mode": row.destination_rename_mode,
        "default_display_name": default_destination_display_name(service, entity),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by_id": row.created_by_id,
        "badge": transform_badge(row),
    }


def destination_schema_key_for_connection(connection) -> str:
    """Stable key for one company's Snowflake database and schema.

    Schema name alone is not enough: many customers use ``PUBLIC``. When the
    database cannot be read, the destination config id keeps those rows apart.
    """
    if connection is None:
        return ""
    cmp_id = getattr(connection, "cmp_id", None) or 0
    schema = str(getattr(connection, "dst_schema", None) or "").strip()
    database = ""
    dst_config_id = getattr(connection, "dst_config_id", None) or 0
    try:
        dst = getattr(connection, "dst_config", None)
        data = getattr(dst, "data", None) if dst is not None else None
        if isinstance(data, dict):
            database = str(data.get("database") or data.get("db") or "").strip()
            if not schema:
                schema = str(
                    data.get("destination_schema") or data.get("schema") or ""
                ).strip()
    except Exception:
        logger.debug(
            "Could not read destination config for schema key", exc_info=True
        )
    if not database:
        database = f"cfg{dst_config_id}"
    if not schema:
        schema = "PUBLIC"
    return f"{cmp_id}:{database}:{schema}".upper()[:255]


def connector_label(connection) -> str:
    if connection is None:
        return "another connector"
    src = getattr(connection, "src_config", None)
    name = getattr(src, "name", None) if src else None
    if name and str(name).strip():
        return str(name).strip()
    cid = getattr(connection, "connection_id", None) or getattr(connection, "pk", None)
    return f"connection {cid}"


def destination_collision_message(dest_name: str, collision: EntityTransform) -> str:
    owner = default_destination_display_name(
        collision.service_name, collision.entity_name
    )
    connector = connector_label(getattr(collision, "connection", None))
    return (
        f"Destination table '{dest_name}' is already used by entity '{owner}' "
        f"on connector '{connector}'."
    )


def find_destination_name_collision(
    connection_id: int,
    destination_table_name: str,
    *,
    exclude_service: str = "",
    exclude_entity: str = "",
) -> Optional[EntityTransform]:
    """Return another active transform in the same destination schema using this name."""
    if not destination_table_name:
        return None
    name = destination_table_name.strip().upper()
    connection = (
        Connection.objects.filter(pk=connection_id)
        .select_related("dst_config", "src_config")
        .first()
    )
    key = destination_schema_key_for_connection(connection)
    qs = EntityTransform.objects.filter(
        is_active=True,
        destination_table_name__iexact=name,
    )
    if key:
        qs = qs.filter(destination_schema_key=key)
    else:
        qs = qs.filter(connection_id=connection_id)
    if exclude_service or exclude_entity:
        qs = qs.exclude(
            connection_id=connection_id,
            service_name=exclude_service,
            entity_name=exclude_entity,
        )
    return (
        qs.select_related("connection", "connection__src_config")
        .order_by("-version")
        .first()
    )


def entity_has_loaded_data(table_row: SourceTableSelection) -> bool:
    """True when delta/watermark state indicates data was already loaded for this entity."""
    if getattr(table_row, "initial_completed_flag", False):
        return True
    if getattr(table_row, "delta_token", None):
        return True
    if getattr(table_row, "first_sync_timestamp", None):
        return True
    status = (getattr(table_row, "status", None) or "").lower()
    return status in {"completed", "success"}


def _resolve_destination_fields(
    *,
    connection_id: int,
    service: str,
    entity: str,
    table_name: str,
    mapping_json: Optional[dict],
    destination_name_source: Optional[str],
    destination_table_name: Optional[str],
    strict: bool,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Return ``(resolved, dest_name, meta_bits)``.

    When ``strict`` is False (draft autosave), invalid destination names are
    stored as empty and surfaced as warnings instead of raising.
    """
    source_hint = (destination_name_source or "").strip().upper() or None
    manual_name = None
    if source_hint == DESTINATION_NAME_SOURCE_MANUAL:
        manual_name = destination_table_name
    elif source_hint is None and destination_table_name:
        manual_name = destination_table_name
        source_hint = DESTINATION_NAME_SOURCE_MANUAL

    resolved = resolve_destination_table_name(
        service_name=service,
        entity_name=entity,
        table_name=table_name,
        mapping_json=mapping_json if isinstance(mapping_json, dict) else None,
        destination_name_source=source_hint,
        manual_destination_table_name=manual_name,
    )
    dest_name = (resolved["destination_table_name"] or "").strip().upper()
    warnings = list(resolved.get("warnings") or [])
    meta_bits: Dict[str, Any] = {
        "warnings": warnings,
        "default_display_name": resolved.get("default_display_name"),
        "destination_table_name": dest_name or None,
    }

    if not dest_name or not is_valid_snowflake_destination_name(dest_name):
        if strict:
            raise ValueError(
                warnings[0] if warnings else "Destination table name is invalid."
            )
        meta_bits["destination_invalid"] = True
        dest_name = ""
        return resolved, dest_name, meta_bits

    collision = find_destination_name_collision(
        connection_id,
        dest_name,
        exclude_service=service,
        exclude_entity=entity,
    )
    if collision:
        message = destination_collision_message(dest_name, collision)
        if strict:
            raise ValueError(message)
        meta_bits["warnings"] = warnings + [message]
        meta_bits["collision"] = True

    return resolved, dest_name, meta_bits


def _next_transform_version(connection_id: int, service: str, entity: str) -> int:
    last = (
        EntityTransform.objects.filter(
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
        )
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
    )
    return (last or 0) + 1


def _schema_key_for(connection_id: int) -> Optional[str]:
    schema_connection = (
        Connection.objects.filter(pk=connection_id)
        .select_related("dst_config")
        .first()
    )
    return destination_schema_key_for_connection(schema_connection) or None


@transaction.atomic
def upsert_draft_transform(
    connection_id: int,
    table_row: SourceTableSelection,
    user,
    mapping_json: Optional[dict],
    mapping_overrides: Optional[dict],
    script_text: Optional[str],
    entry_point: str,
    run_order: str,
    row_filter: Optional[dict],
    destination_name_source: Optional[str] = None,
    destination_table_name: Optional[str] = None,
    destination_rename_mode: Optional[str] = None,
    confirm_use_existing_table: bool = False,
) -> Tuple[EntityTransform, Dict[str, Any]]:
    """Create or update the single open draft for an entity (never activates)."""
    service, entity, table_name = parse_entity_names(table_row)
    previous_active = get_active_transform(connection_id, service, entity)
    previous_dest = (
        (previous_active.destination_table_name or "").strip().upper()
        if previous_active
        else ""
    )

    resolved, dest_name, meta = _resolve_destination_fields(
        connection_id=connection_id,
        service=service,
        entity=entity,
        table_name=table_name,
        mapping_json=mapping_json,
        destination_name_source=destination_name_source,
        destination_table_name=destination_table_name,
        strict=False,
    )
    meta.update(
        {
            "requires_rename_confirmation": False,
            "requires_use_existing_confirmation": False,
            "previous_destination_table_name": previous_dest or None,
            "is_draft": True,
        }
    )

    rename_mode = (destination_rename_mode or "").strip().lower() or None
    if rename_mode not in {
        EntityTransform.RENAME_MODE_CREATE_NEW,
        EntityTransform.RENAME_MODE_RENAME_EXISTING,
        None,
    }:
        rename_mode = None

    script_sha = sha256_text(script_text or "")
    schema_key = _schema_key_for(connection_id)
    draft = get_draft_transform(connection_id, service, entity)
    field_values = {
        "table_name": table_name,
        "is_active": False,
        "status": EntityTransform.STATUS_DRAFT,
        "mapping_json": mapping_json,
        "mapping_overrides": mapping_overrides or {},
        "script_text": script_text,
        "script_sha256": script_sha,
        "entry_point": entry_point or "",
        "run_order": run_order or EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
        "row_filter": row_filter,
        "json_table_name": resolved.get("json_table_name"),
        "destination_table_name": dest_name or None,
        "destination_name_source": resolved.get("destination_name_source")
        or DESTINATION_NAME_SOURCE_DEFAULT,
        "previous_destination_table_name": previous_dest or None,
        "destination_rename_mode": rename_mode,
        "destination_schema_key": schema_key,
        "created_by": user,
    }

    update_fields = {k: v for k, v in field_values.items() if k != "created_by"}
    if draft:
        for key, value in update_fields.items():
            setattr(draft, key, value)
        draft.save()
        row = draft
    else:
        try:
            row = EntityTransform.objects.create(
                connection_id=connection_id,
                service_name=service,
                entity_name=entity,
                version=_next_transform_version(connection_id, service, entity),
                **field_values,
            )
        except IntegrityError as exc:
            # Race: another request created the draft — reload and update.
            draft = get_draft_transform(connection_id, service, entity)
            if not draft:
                raise ValueError("Could not save draft transform.") from exc
            for key, value in update_fields.items():
                setattr(draft, key, value)
            draft.save()
            row = draft

    if confirm_use_existing_table:
        meta["confirm_use_existing_table"] = True
    return row, meta


@transaction.atomic
def publish_transform_version(
    connection_id: int,
    table_row: SourceTableSelection,
    user,
    mapping_json: Optional[dict],
    mapping_overrides: Optional[dict],
    script_text: Optional[str],
    entry_point: str,
    run_order: str,
    row_filter: Optional[dict],
    destination_name_source: Optional[str] = None,
    destination_table_name: Optional[str] = None,
    destination_rename_mode: Optional[str] = None,
    confirm_use_existing_table: bool = False,
) -> Tuple[EntityTransform, Dict[str, Any]]:
    """Create a new validated active version; supersede any open draft."""
    service, entity, table_name = parse_entity_names(table_row)

    previous_active = get_active_transform(connection_id, service, entity)
    previous_dest = (
        (previous_active.destination_table_name or "").strip().upper()
        if previous_active
        else ""
    )

    resolved, dest_name, meta = _resolve_destination_fields(
        connection_id=connection_id,
        service=service,
        entity=entity,
        table_name=table_name,
        mapping_json=mapping_json,
        destination_name_source=destination_name_source,
        destination_table_name=destination_table_name,
        strict=True,
    )
    meta.update(
        {
            "requires_rename_confirmation": False,
            "requires_use_existing_confirmation": False,
            "previous_destination_table_name": previous_dest or None,
            "is_draft": False,
            "published": True,
        }
    )

    name_changed = bool(previous_dest and previous_dest != dest_name)
    if name_changed and entity_has_loaded_data(table_row):
        rename_mode = (destination_rename_mode or "").strip().lower() or None
        if rename_mode not in {
            EntityTransform.RENAME_MODE_CREATE_NEW,
            EntityTransform.RENAME_MODE_RENAME_EXISTING,
        }:
            meta["requires_rename_confirmation"] = True
            meta["message"] = (
                f"Destination table name changed from {previous_dest} to {dest_name}. "
                "Choose 'Create new table and full reload' (safer) or "
                "'Rename existing table'."
            )
            raise ValueError(meta["message"])
    else:
        rename_mode = None

    # Drop open draft so the unique draft constraint stays free after publish.
    EntityTransform.objects.filter(
        connection_id=connection_id,
        service_name=service,
        entity_name=entity,
        status=EntityTransform.STATUS_DRAFT,
    ).delete()

    EntityTransform.objects.filter(
        connection_id=connection_id,
        service_name=service,
        entity_name=entity,
        is_active=True,
    ).update(is_active=False)

    script_sha = sha256_text(script_text or "")
    schema_key = _schema_key_for(connection_id)

    try:
        row = EntityTransform.objects.create(
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
            table_name=table_name,
            version=_next_transform_version(connection_id, service, entity),
            is_active=True,
            status=EntityTransform.STATUS_VALIDATED,
            mapping_json=mapping_json,
            mapping_overrides=mapping_overrides or {},
            script_text=script_text,
            script_sha256=script_sha,
            entry_point=entry_point or "",
            run_order=run_order or EntityTransform.RUN_ORDER_FILTER_SCRIPT_MAPPING,
            row_filter=row_filter,
            json_table_name=resolved.get("json_table_name"),
            destination_table_name=dest_name,
            destination_name_source=resolved.get("destination_name_source")
            or DESTINATION_NAME_SOURCE_DEFAULT,
            previous_destination_table_name=previous_dest or None,
            destination_rename_mode=rename_mode,
            destination_schema_key=schema_key,
            created_by=user,
        )
    except IntegrityError as exc:
        raise ValueError(
            f"Destination table '{dest_name}' collides with another mapping in this schema."
        ) from exc

    if name_changed:
        audit_destination_name_change(
            user=user,
            connection_id=connection_id,
            table_name=table_name,
            old_name=previous_dest,
            new_name=dest_name,
            source=row.destination_name_source,
            rename_mode=rename_mode,
        )

    if confirm_use_existing_table:
        meta["confirm_use_existing_table"] = True

    return row, meta


@transaction.atomic
def save_transform_version(
    connection_id: int,
    table_row: SourceTableSelection,
    user,
    mapping_json: Optional[dict],
    mapping_overrides: Optional[dict],
    script_text: Optional[str],
    entry_point: str,
    run_order: str,
    row_filter: Optional[dict],
    destination_name_source: Optional[str] = None,
    destination_table_name: Optional[str] = None,
    destination_rename_mode: Optional[str] = None,
    confirm_use_existing_table: bool = False,
    activate: bool = False,
) -> Tuple[EntityTransform, Dict[str, Any]]:
    """Persist a transform. Default is draft upsert; ``activate=True`` publishes."""
    kwargs = dict(
        connection_id=connection_id,
        table_row=table_row,
        user=user,
        mapping_json=mapping_json,
        mapping_overrides=mapping_overrides,
        script_text=script_text,
        entry_point=entry_point,
        run_order=run_order,
        row_filter=row_filter,
        destination_name_source=destination_name_source,
        destination_table_name=destination_table_name,
        destination_rename_mode=destination_rename_mode,
        confirm_use_existing_table=confirm_use_existing_table,
    )
    if activate:
        return publish_transform_version(**kwargs)
    return upsert_draft_transform(**kwargs)


def audit_transform_action(user, connection, table_name: str, action: str, script_sha256: str = "") -> None:
    msg = f"Entity transform {action} for table '{table_name}'"
    if script_sha256:
        msg += f" (script sha256={script_sha256[:12]}…)"
    Log.objects.create(
        cmp=user.cmp,
        connection=connection,
        user=user,
        status="S",
        # Publishing a transform is an audit event, not a migration run.
        # Keep it outside migration-session navigation and status lookups.
        session=None,
        message=msg,
        job_name=f"transform_{action}_{table_name}",
    )


def audit_destination_name_change(
    *,
    user,
    connection_id: int,
    table_name: str,
    old_name: str,
    new_name: str,
    source: str,
    rename_mode: Optional[str] = None,
) -> None:
    from apps.elttool.models import Connection

    connection = Connection.objects.filter(pk=connection_id).first()
    mode_part = f", rename_mode={rename_mode}" if rename_mode else ""
    msg = (
        f"Destination table name changed for '{table_name}': "
        f"'{old_name}' → '{new_name}' (source={source}{mode_part})"
    )
    Log.objects.create(
        cmp=getattr(user, "cmp", None),
        connection=connection,
        user=user,
        status="S",
        session=None,
        message=msg,
        job_name=f"transform_dest_rename_{table_name}",
    )
    logger.info(msg)


def get_active_transform(connection_id: int, service: str, entity: str) -> Optional[EntityTransform]:
    return (
        EntityTransform.objects.filter(
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
            is_active=True,
        )
        .order_by("-version")
        .first()
    )


def get_draft_transform(connection_id: int, service: str, entity: str) -> Optional[EntityTransform]:
    return (
        EntityTransform.objects.filter(
            connection_id=connection_id,
            service_name=service,
            entity_name=entity,
            status=EntityTransform.STATUS_DRAFT,
        )
        .order_by("-version")
        .first()
    )


def destination_name_preview(
    connection,
    table_row: SourceTableSelection,
    mapping_json: Optional[dict],
    destination_name_source: Optional[str] = None,
    destination_table_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Server-side destination name + collision + existing-Snowflake-table hints."""
    service, entity, table_name = parse_entity_names(table_row)
    source_hint = (destination_name_source or "").strip().upper() or None
    manual_name = None
    if source_hint == DESTINATION_NAME_SOURCE_MANUAL:
        manual_name = destination_table_name
    elif destination_table_name and source_hint is None:
        manual_name = destination_table_name
        source_hint = DESTINATION_NAME_SOURCE_MANUAL

    resolved = resolve_destination_table_name(
        service_name=service,
        entity_name=entity,
        table_name=table_name,
        mapping_json=mapping_json if isinstance(mapping_json, dict) else None,
        destination_name_source=source_hint,
        manual_destination_table_name=manual_name,
    )
    dest_name = (resolved.get("destination_table_name") or "").strip().upper()
    collision = find_destination_name_collision(
        connection.connection_id if hasattr(connection, "connection_id") else connection.pk,
        dest_name,
        exclude_service=service,
        exclude_entity=entity,
    )
    collision_payload = None
    if collision:
        collision_payload = {
            "entity_label": default_destination_display_name(
                collision.service_name, collision.entity_name
            ),
            "connector_name": connector_label(getattr(collision, "connection", None)),
            "entity_name": collision.entity_name,
            "table_name": collision.table_name,
            "destination_table_name": (collision.destination_table_name or "").upper(),
        }

    existing_snowflake_table = False
    try:
        dst = getattr(connection, "dst_config", None)
        dst_model = getattr(dst, "dst", None) if dst else None
        if dst_model and getattr(dst_model, "name", "") == "Snowflake":
            config_data = dst.data if dst else None
            schema = ""
            if isinstance(config_data, dict):
                schema = (config_data.get("schema") or config_data.get("destination_schema") or "")
            if schema and dest_name:
                from apps.elttool.services.snowflake_readonly_listing import (
                    fetch_existing_tables_from_snowflake,
                )

                existing = fetch_existing_tables_from_snowflake(config_data, schema)
                schema_key = destination_schema_key_for_connection(connection)
                owned = (
                    EntityTransform.objects.filter(destination_schema_key=schema_key)
                    if schema_key
                    else EntityTransform.objects.filter(connection_id=connection.pk)
                )
                ours = {
                    (row or "").strip().upper()
                    for row in owned.values_list("destination_table_name", flat=True)
                    if row
                }
                ours.update(
                    {
                        (row or "").strip().upper()
                        for row in owned.values_list(
                            "previous_destination_table_name", flat=True
                        )
                        if row
                    }
                )
                if dest_name in existing and dest_name not in ours:
                    existing_snowflake_table = True
    except Exception as exc:
        logger.debug("Snowflake existing-table check skipped: %s", exc)

    valid = bool(dest_name) and is_valid_snowflake_destination_name(dest_name) and not collision
    return {
        **resolved,
        "destination_table_name": dest_name,
        "valid": valid,
        "collision": collision_payload,
        "existing_snowflake_table": existing_snowflake_table,
        "error": (
            destination_collision_message(dest_name, collision)
            if collision_payload and collision
            else (None if valid else "Destination table name is invalid.")
        ),
    }
