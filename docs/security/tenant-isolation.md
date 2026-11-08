# Tenant isolation

**Status:** Partial — company scoping exists in some views; the shared `tenant_scope` module expected by rules is missing.

## Goals

- A user in company A must never read/update company B’s `Connection` (or related pipeline objects) by guessing PKs.
- All agents/humans load connections through one shared helper, not ad-hoc `Connection.objects.get(pk=…)`.

## Intended contract (AGENTS.md)

```python
from apps.elttool.services.tenant_scope import get_connection_for_user

connection = get_connection_for_user(request.user, connection_id)
```

- Raises / returns 404 when the connection is not in `request.user.cmp`.
- Prefer this over raw PK lookups in views, services, and WS helpers.

## What exists today

| Location | Behavior |
|----------|----------|
| `apps/elttool/v1/batch_views.py` | Local `_get_connection_for_user` filters by `request.user.cmp` |
| `apps/elttool/v1/pipeline_views.py` | Local connection + pipeline helpers scoped to user company |
| `apps/elttool/v1/transform_views.py` | Imports `apps.elttool.services.tenant_scope.get_connection_for_user` |

## Gap

- File **`apps/elttool/services/tenant_scope.py` does not exist**.
- Rules, docs, and some imports already assume it — implement the shared module and migrate local `_get_connection_for_user` helpers onto it.
- Until then, treat any new PK-only `Connection` lookup as a security defect in review.

## Review checklist for new APIs

1. Does this endpoint take a `connection_id` / `destination_id` / similar?
2. Is the object loaded through tenant-scoped helpers?
3. Are list endpoints filtered by `user.cmp` (or equivalent) by default?
4. Do error responses avoid leaking whether an ID exists in another tenant? (prefer uniform 404)
