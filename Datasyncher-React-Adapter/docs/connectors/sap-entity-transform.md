# SAP Entity Mapping and Transform

Per-entity column-mapping JSON and optional Python transform scripts for SAP OData → Snowflake and SAP OData → Databricks loads. The mapping JSON, UI wizard (column mapper + Python IDE), and entity-transform APIs are destination-agnostic; only the load layer differs (Snowflake COPY vs Databricks staging COPY/MERGE).

## JSON format

DTP-style export with required keys:

- `source` — `{ objectname, fields[] }`
- `target` — `{ objectname, fields[] }`
- `field_mapping[]` — `{ src_fieldnames[], tgt_fieldname, constant_value?, mapping_type? }`
- `row_filter` — optional `{ present, condition }`
- `transformation` — `{ type, entry_point }`

## Migration path (SAP → Snowflake / Databricks)

When an active transform exists, each extract chunk follows this order:

1. SAP OData extract. `$select` uses the mapped **source** columns (JSON `field_mapping` plus UI overrides). With no mapping JSON, SAP metadata is treated as an identity mapping so the extract still matches the source fields.
2. Optional entity row filter (already applied on extract).
3. Python `transform` **only when** saved `script_text` has a non-empty body. The locked shell with an empty body is treated as no script and is skipped.
4. Column mapping: rename source → target, add missing target columns, order to the target list.
5. Load into the destination table.

**No script:** steps 3 is skipped. Data still passes through mapping, then load.

**No mapping and no script:** the entity is unchanged from the pre-transform load (full SAP extract, destination columns from SAP metadata).

## Destination table

- **Table name** comes from the saved destination name (JSON target object or the name chosen in the UI).
- **Columns**, when a transform is active and mapping rows exist, are created or altered from the effective **target** fields (JSON `target.fields` types, otherwise the mapped SAP source type). New tables are created with those target columns. Existing tables gain any missing target columns; they are not rebuilt.
- Without an active mapping, the table is still created from SAP OData `$metadata` (source names).

## Script contract

The shell is fixed. Only the function body is user code.

```python
import pandas as pd

def transform(__source_df: pd.DataFrame) -> pd.DataFrame:
    __target_df = __source_df.copy()
    df = __target_df
    # transformation code

    return __target_df
```

- Root function name: `transform`
- `__source_df` on the way in uses SAP source column names
- The returned `__target_df` uses the target column set
- `df` is an alias of `__target_df` so older bodies that edit `df` in place still apply
- Must be chunk-safe (row-wise operations)

## Size limits (env-configurable)

| Constant | Default |
|----------|---------|
| `MAX_MAPPING_JSON_MB` | 10 |
| `MAX_MAPPED_COLUMNS` | 2000 |
| `MAX_SCRIPT_KB` | 1024 |
| `MAX_SCRIPT_LINES` | 20000 |
| `MAX_DRY_RUN_ROWS` | 1000 (default sample: 100) |

Over-limit uploads return HTTP 413.

## Sandbox limits

- Allowed imports: `pandas`, `numpy`, `datetime`, `re`, `decimal`, `math`
- Blocked: `os`, `sys`, `subprocess`, `socket`, `requests`, `urllib`, `open`, `eval`, `exec`, `compile`, `__import__`
- Runtime enforcement via restricted builtins in the script loader
- `TRANSFORM_SANDBOX_TIMEOUT_SECONDS` (default 120), `TRANSFORM_SANDBOX_MEMORY_MB` (default 512)

## Draft vs validated (published)

The IDE has **no Save button**. Edits autosave to a single open **draft** row (`status=draft`, `is_active=false`). Drafts may contain validation errors and are **never** loaded by migrations. The IDE shows a single badge: **Draft** while the editor has unvalidated changes (or a server draft), or **Validated vN** when the editor matches the published active version.

**Validate** (validate API with `publish=true`) runs the checklist; on success it creates a new **validated** version, sets `is_active=true`, and deletes the open draft. Runtime continues to load only `is_active=TRUE` via `get_active_entity_transform`. If there is no active transform, the load path runs without mapping/script (badge: **No validated version**).

Intermediate **Next** / preview validate calls use `publish=false` (or omit `publish`) so they do not activate.

## API

```
GET    /api/v1/connection/<id>/tables/<table>/transform/          # active + draft + history
POST   /api/v1/connection/<id>/tables/<table>/transform/          # upsert draft (activate=false by default)
POST   /api/v1/connection/<id>/tables/<table>/transform/validate/ # checklist; publish=true activates
POST   /api/v1/connection/<id>/tables/<table>/transform/dry-run/
POST   /api/v1/connection/<id>/tables/<table>/transform/<version>/activate/  # validated versions only
DELETE /api/v1/connection/<id>/tables/<table>/transform/          # deactivate active + drop draft
```

## Demo fixtures

See [`sap_demo_files/`](../sap_demo_files/) for the 0FI_GL_4 DTP export and Python script used in acceptance tests.
