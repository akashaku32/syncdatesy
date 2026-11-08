# Migration services (security-adjacent)

**Status:** Partial — `migration_common` registry exists; keep using it for new pairs.

## Why this is a security topic

Copy-pasted `to_*.py` loaders tend to diverge on:

- How credentials are read/decrypted
- TLS / verify flags
- Error logging (accidental secret leakage)
- Retry / failure handling

A shared registry reduces that surface.

## What exists

Under `Datasyncher-React-Adapter/backend/migration-function/services/migration_common/`:

| Piece | Role |
|-------|------|
| `registry.py` | Source→destination handler registration |
| `bootstrap.py` | Registers known handlers at startup |
| `protocols.py` | `DestinationLoader` / `SourceExtractor` contracts |
| `retry.py` / `failure_tracker.py` | Shared operational behavior |
| Tests | `tests/test_migration_common_registry.py` |

Root `AGENTS.md` rule: **new source→dest pairs go through this registry + shared Snowflake/Databricks loaders — never copy a whole `to_*.py`.**

## Related

- Service auth for the migration-function HTTP surface: [service-auth.md](./service-auth.md)
- Credential encryption before jobs run: [field-encryption.md](./field-encryption.md)
