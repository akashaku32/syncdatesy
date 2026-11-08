import logging
import os
import re
from typing import Optional, Set

from sqlalchemy import text

logger = logging.getLogger(__name__)

_SESSION_STAGING_KEY_PREFIX = "migration:session_staging:"
# Default 4 days — long Dynamics tables can run 2–3+ days; TTL must outlive the migration.
_SESSION_STAGING_TTL_SECONDS = int(
    os.getenv("MIGRATION_SESSION_STAGING_TTL_SECONDS", str(4 * 86_400))
)


def _migration_redis_client():
    url = (os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        import redis

        return redis.from_url(url)
    except Exception as exc:
        logger.debug("migration redis unavailable: %s", exc)
        return None


def _session_staging_redis_key(migration_session_id, table_name: str) -> str:
    return f"{_SESSION_STAGING_KEY_PREFIX}{migration_session_id}:{(table_name or '').strip().upper()}"


def lookup_session_staging_name(migration_session_id, table_name: str) -> Optional[str]:
    """Return staging table name allocated earlier in this migration session, if any."""
    if migration_session_id is None or not table_name:
        return None
    client = _migration_redis_client()
    if not client:
        return None
    try:
        raw = client.get(_session_staging_redis_key(migration_session_id, table_name))
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    except Exception as exc:
        logger.debug(
            "lookup_session_staging_name failed session=%s table=%s err=%s",
            migration_session_id,
            table_name,
            exc,
        )
        return None


def remember_session_staging_name(migration_session_id, table_name: str, staging_table_name: str) -> None:
    if migration_session_id is None or not table_name or not staging_table_name:
        return
    client = _migration_redis_client()
    if not client:
        return
    try:
        client.setex(
            _session_staging_redis_key(migration_session_id, table_name),
            _SESSION_STAGING_TTL_SECONDS,
            staging_table_name,
        )
    except Exception as exc:
        logger.debug(
            "remember_session_staging_name failed session=%s table=%s err=%s",
            migration_session_id,
            table_name,
            exc,
        )


def touch_session_staging_name(migration_session_id, table_name: str) -> None:
    """Extend Redis TTL while a long-running table migration is still using staging."""
    if migration_session_id is None or not table_name:
        return
    client = _migration_redis_client()
    if not client:
        return
    key = _session_staging_redis_key(migration_session_id, table_name)
    try:
        if client.exists(key):
            client.expire(key, _SESSION_STAGING_TTL_SECONDS)
    except Exception as exc:
        logger.debug(
            "touch_session_staging_name failed session=%s table=%s err=%s",
            migration_session_id,
            table_name,
            exc,
        )


def forget_session_staging_name(migration_session_id, table_name: str) -> None:
    if migration_session_id is None or not table_name:
        return
    client = _migration_redis_client()
    if not client:
        return
    try:
        client.delete(_session_staging_redis_key(migration_session_id, table_name))
    except Exception as exc:
        logger.debug(
            "forget_session_staging_name failed session=%s table=%s err=%s",
            migration_session_id,
            table_name,
            exc,
        )


def collect_protected_staging_names_for_table(table_name: str) -> Set[str]:
    """
    Return all staging table names registered in Redis for ``table_name`` across sessions.

    Prevents pre-chunk cleanup from dropping in-use staging when another worker/session,
    a requeued child, or a long-running parallel migration still references the table.
    """
    tbl_u = (table_name or "").strip().upper()
    if not tbl_u:
        return set()
    client = _migration_redis_client()
    if not client:
        return set()
    protected: Set[str] = set()
    pattern = f"{_SESSION_STAGING_KEY_PREFIX}*:{tbl_u}"
    try:
        for key in client.scan_iter(match=pattern, count=200):
            try:
                raw = client.get(key)
            except Exception:
                continue
            if not raw:
                continue
            name = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            norm = normalize_name(name)
            if norm:
                protected.add(norm)
    except Exception as exc:
        logger.debug(
            "collect_protected_staging_names_for_table failed table=%s err=%s",
            table_name,
            exc,
        )
    return protected


def normalize_name(name: str) -> str:
    """
    Normalizes a name for Snowflake by replacing Salesforce-style __c with _C,
    handling multiple underscores, and converting to uppercase.
    """
    # Replace Salesforce-style __c with _C first
    name = name.replace("__c", "_C")

    # Replace multiple underscores with a single one
    name = re.sub(r"__+", "_", name)

    # Insert underscore before uppercase letters preceded by lowercase letters (e.g., shownMoon → shown_Moon)
    name = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", name)

    # Insert underscore before digits (e.g., Field2 → Field_2)
    name = re.sub(r"(?<=[A-Za-z])(\d)", r"_\1", name)

    # Insert underscore after digits if followed by a letter (e.g., 2Field → 2_Field)
    name = re.sub(r"(\d)(?=[A-Za-z])", r"\1_", name)

    return name.upper()


def drop_existing_ds_staging_tables_for_table(
    engine,
    schema: str,
    table_name: str,
    keep_staging_name: Optional[str] = None,
    migration_session_id=None,
) -> int:
    """
    One-shot cleanup before chunking: drop prior DS_<TABLE>_STAGING_<YYYYMMDD>_<HHMMSS>
    tables for this source table in `schema`. Does not run between chunk iterations.

    Never drops staging names registered in Redis for any active migration session, or
    ``keep_staging_name`` / this session's remembered staging table.
    """
    if not engine or not schema or not table_name:
        return 0
    schema_u = (schema or "").strip().upper()
    tbl_u = (table_name or "").strip().upper()
    if not schema_u or not tbl_u:
        return 0

    protected = collect_protected_staging_names_for_table(table_name)
    if keep_staging_name:
        protected.add(normalize_name(keep_staging_name))
    remembered = lookup_session_staging_name(migration_session_id, table_name)
    if remembered:
        protected.add(normalize_name(remembered))

    pattern = rf"^DS_{re.escape(tbl_u)}_STAGING_[0-9]{{8}}_[0-9]{{6}}$"
    dropped = 0
    skipped_protected = 0
    try:
        with engine.begin() as con:
            rows = con.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = :schema
                      AND REGEXP_LIKE(table_name, :pattern, 'i')
                    """
                ),
                {"schema": schema_u, "pattern": pattern},
            ).fetchall()
            for (tname,) in rows:
                if not tname:
                    continue
                norm = normalize_name(tname)
                if norm in protected:
                    skipped_protected += 1
                    continue
                con.execute(
                    text(f'DROP TABLE IF EXISTS "{schema_u}"."{norm}"'),
                )
                dropped += 1
        if dropped or skipped_protected:
            logger.warning(
                "Pre-chunk staging cleanup | dropped=%s skipped_protected=%s | "
                "source_table=%s schema=%s session=%s",
                dropped,
                skipped_protected,
                table_name,
                schema_u,
                migration_session_id,
            )
    except Exception as e:
        logger.error(
            "Pre-chunk staging cleanup failed table=%s schema=%s err=%s",
            table_name,
            schema_u,
            e,
        )
    return dropped


def drop_staging_table(engine, staging_table: str, schema: str = "PUBLIC"):
    """
    Drops a staging table in Snowflake using schema-qualified table name.

    Args:
        engine: SQLAlchemy engine
        staging_table: Name of the staging table to drop
        schema: Schema name (defaults to 'PUBLIC')
    """
    norm_staging = normalize_name(staging_table)
    try:
        with engine.connect() as con:
            # Use fully qualified table name to avoid "no current schema" errors
            con.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{norm_staging}"'))
            con.commit()
            logger.info(f"✅ Dropped staging table: {schema}.{norm_staging}")
    except Exception as e:
        logger.error(f"❌ Failed to drop staging table {schema}.{norm_staging}: {e}")
