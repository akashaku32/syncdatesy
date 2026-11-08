# Chunk and plan limits

DataSyncher resolves migration chunk size, Celery concurrency, and per-job pod count from three layers:

1. **Connector caps** — `source_model` and `destination_model` `min_count` / `max_count`
2. **Commercial caps** — `plan_resource_config` (`chunk_size_pct`, `max_chunk_size`, `max_concurrency`, `concurrency`, `max_inflight_tables`, `max_pods`)
3. **Per-connection value** — `connection.chunk_count` (default at create, user-editable within bounds)

## Tables

### `source_model` (new columns)

| Column | Purpose |
|--------|---------|
| `min_count` | Minimum rows per read batch (default 1) |
| `max_count` | Platform read ceiling (nullable = no cap) |

### `destination_model`

| Column | Purpose |
|--------|---------|
| `min_count` | Minimum write batch size |
| `max_count` | Platform write ceiling |

### `plan_resource_config`

| Column | Purpose |
|--------|---------|
| `max_chunk_size` | Absolute fallback when `chunk_size_pct` or connector ceiling is unavailable |
| `chunk_size_pct` | Percent of `min(src.max_count, dst.max_count)` for plan-natural chunk |
| `concurrency` | Base Celery concurrency per worker pod |
| `max_concurrency` | Upper cap after adaptive scaling |
| `max_inflight_tables` | Target parallel tables; drives dynamic pod count |
| `max_pods` | Pod ceiling per job |

Seeded values (migration `0061`):

| Plan | chunk_size_pct | max_concurrency |
|------|----------------|-----------------|
| free | 25 | 8 |
| standard | 25 | 10 |
| discounted | 25 | 10 |
| premium | 50 | 16 |
| enterprise | 100 | 24 |

## Resolution formula

```
connector_ceiling = min(src.max_count, dst.max_count)   # ignore nulls

if chunk_size_pct and connector_ceiling:
    plan_natural_chunk = floor(connector_ceiling × chunk_size_pct / 100)
else:
    plan_natural_chunk = plan.max_chunk_size

effective_chunk = min(connection.chunk_count, plan_natural_chunk)
effective_chunk = max(effective_chunk, min_floor)        # default 100
# memory guard may reduce further → logs CHUNK_SIZE_REDUCED

if effective_chunk < plan_natural_chunk:
    scaled_concurrency = floor(base_concurrency × plan_natural_chunk / effective_chunk)
else:
    scaled_concurrency = base_concurrency

effective_concurrency = min(scaled_concurrency, max_concurrency, override.concurrency)

raw_pods = ceil(max_inflight_tables / effective_concurrency)
effective_pods = clamp(raw_pods, 1, max_pods)
```

## Runtime log markers

| Marker | Level | Meaning |
|--------|-------|---------|
| `CHUNK_RESOLVED` | INFO | Chunk path resolved |
| `CHUNK_SIZE_REDUCED` | WARNING | Memory guard reduced chunk |
| `CONCURRENCY_RESOLVED` | INFO | Adaptive concurrency applied |
| `PODS_RESOLVED` | INFO | Dynamic pod count computed |

## Code locations

- Django resolver: `apps/elttool/services/chunk_limit_resolver.py`
- Connection create default: `Connection.save()` → `resolve_default_connection_chunk_count()`
- Celery resolver: `backend/fast-api/celery_resource_config.py`
- Orchestration: `orchestrate_migration()` uses `resolve_dynamic_execution_plan()` for chunk (all modes) and pods/concurrency (per-job mode)

## Rollback

Disable percentage-based sizing without redeploy:

```sql
UPDATE plan_resource_config
SET chunk_size_pct = NULL, max_concurrency = NULL;
```

Resolver falls back to `max_chunk_size` and static concurrency on the next job.

## Maintenance examples

```sql
-- Salesforce destination write cap
UPDATE destination_model SET max_count = 500
WHERE name IN ('Salesforce', 'SalesforceSandbox');

-- Salesforce source read cap
UPDATE source_model SET max_count = 10000
WHERE name IN ('Salesforce', 'SalesforceSandbox');
```
