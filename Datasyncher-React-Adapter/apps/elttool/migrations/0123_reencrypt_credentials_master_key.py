"""
Re-encrypt connector credentials with FIELD_ENCRYPTION_KEY when configured.

If FIELD_ENCRYPTION_KEY is empty, this migration is a no-op.
"""
from __future__ import annotations

import json
import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def _legacy_key(cmp_id, created_at):
    day = created_at.day if created_at else 1
    return (cmp_id or 0) + day


def reencrypt_configs(apps, schema_editor):
    from django.conf import settings

    master = (getattr(settings, "FIELD_ENCRYPTION_KEY", None) or "").strip()
    if not master:
        logger.warning("0123: FIELD_ENCRYPTION_KEY unset; skipping")
        return

    from apps.utils.encryption import (
        decrypt_sensitive,
        encrypt_sensitive,
        parse_encrypted_payload,
    )

    SourceConfig = apps.get_model("elttool", "SourceConfig")
    DestinationConfig = apps.get_model("elttool", "DestinationConfig")
    Company = apps.get_model("elttool", "Company")

    def _reencrypt_row(model, pk_name, field_name="config_data"):
        updated = 0
        for row in model.objects.select_related("cmp").iterator():
            payload = parse_encrypted_payload(getattr(row, field_name, None))
            if payload is None or len(payload) == 7:
                continue
            cmp = getattr(row, "cmp", None)
            if cmp is None:
                continue
            legacy = _legacy_key(cmp.cmp_id, cmp.created_at)
            try:
                plaintext = decrypt_sensitive(payload, legacy, legacy_fallback=cmp.cmp_id)
            except Exception as exc:
                logger.error("Failed decrypting %s pk=%s: %s", model.__name__, getattr(row, pk_name), exc)
                continue
            if plaintext is None:
                continue
            setattr(row, field_name, json.dumps(encrypt_sensitive(plaintext, legacy)))
            row.save(update_fields=[field_name])
            updated += 1
        logger.info("Re-encrypted %s rows on %s", updated, model.__name__)

    _reencrypt_row(SourceConfig, "src_config_id")
    _reencrypt_row(DestinationConfig, "dst_config_id")

    updated = 0
    for company in Company.objects.iterator():
        payload = parse_encrypted_payload(company.cmp_name)
        if payload is None or len(payload) == 7:
            continue
        legacy = _legacy_key(company.cmp_id, company.created_at)
        try:
            plaintext = decrypt_sensitive(payload, legacy, legacy_fallback=company.cmp_id)
        except Exception as exc:
            logger.error("Failed decrypting Company cmp_id=%s: %s", company.cmp_id, exc)
            continue
        if plaintext is None:
            continue
        company.cmp_name = json.dumps(encrypt_sensitive(plaintext, legacy))
        company.save(update_fields=["cmp_name"])
        updated += 1
    logger.info("Re-encrypted %s Company.cmp_name values", updated)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0122_entity_transform_status"),
    ]

    operations = [
        migrations.RunPython(reencrypt_configs, noop_reverse),
    ]
