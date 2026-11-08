# Databricks notebook source
# MAGIC %md
# MAGIC # Dynamics F&O GCS bulk merge
# MAGIC Reads landed CSVs from ``gs://.../dynamics_fo/...`` and MERGEs into a Delta base table.

# COMMAND ----------

import re

dbutils.widgets.text("catalog", "")
dbutils.widgets.text("schema", "")
dbutils.widgets.text("table_name", "")
dbutils.widgets.text("landing_prefix", "")
dbutils.widgets.text("primary_key", "ID")
dbutils.widgets.text("updated_at_col", "_FIVETRAN_SYNCED")
dbutils.widgets.text("deleted_at_col", "_FIVETRAN_DELETED")
dbutils.widgets.text("connection_id", "")
dbutils.widgets.text("migration_session_id", "")
dbutils.widgets.text("schema_columns", "")
dbutils.widgets.text("is_initial_load", "false")
dbutils.widgets.text("staging_cleanup_after_merge", "true")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
table_name = dbutils.widgets.get("table_name").strip()
landing_prefix = dbutils.widgets.get("landing_prefix").strip().rstrip("/")
primary_key = dbutils.widgets.get("primary_key").strip() or "ID"
updated_at_col = dbutils.widgets.get("updated_at_col").strip()
deleted_at_col = dbutils.widgets.get("deleted_at_col").strip()
schema_columns_raw = dbutils.widgets.get("schema_columns").strip()
is_initial_load = str(dbutils.widgets.get("is_initial_load") or "").strip().lower() in (
    "1",
    "true",
    "yes",
    "y",
)
staging_cleanup_after_merge = str(
    dbutils.widgets.get("staging_cleanup_after_merge") or "true"
).strip().lower() not in ("0", "false", "no", "off")

if not catalog or not schema or not table_name or not landing_prefix:
    raise ValueError("catalog, schema, table_name, and landing_prefix are required")

base_table = f"`{catalog}`.`{schema}`.`{table_name}`"
_SPARK_STAGING_TABLE = None

# COMMAND ----------


def _configure_spark_for_bulk_merge():
    settings = {
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.adaptive.coalescePartitions.enabled": "true",
        "spark.sql.adaptive.skewJoin.enabled": "true",
        "spark.sql.adaptive.localShuffleReader.enabled": "true",
        "spark.sql.files.ignoreMissingFiles": "true",
        "spark.sql.files.maxPartitionBytes": "134217728",
        "spark.sql.files.openCostInBytes": "4194304",
    }
    applied, skipped = [], []
    for key, value in settings.items():
        try:
            spark.conf.set(key, value)
            applied.append(key)
        except Exception as exc:
            skipped.append(f"{key}: {exc}")
    if applied:
        print("Spark bulk-merge tuning applied:", ", ".join(sorted(applied)))
    if skipped:
        print("Spark bulk-merge tuning skipped:", " | ".join(skipped))


_configure_spark_for_bulk_merge()


def _to_snake_upper(name: str) -> str:
    """Match worker ``add_underscore``: CamelCase → SNAKE_UPPER (SinkModifiedOn → SINK_MODIFIED_ON)."""
    s = str(name or "")
    preserve_leading = s.startswith("_")
    s = re.sub(r"(?<=[a-zA-Z])(?=[0-9])", "_", s)
    s = re.sub(r"(?<=[0-9])(?=[a-zA-Z])", "_", s)
    s = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", s)
    s = re.sub(r"\W+", "_", s)
    s = re.sub(r"_+", "_", s)
    core = s.strip("_").upper()
    if preserve_leading and core:
        return f"_{core}"
    return core


def _normalize_col(name: str) -> str:
    return re.sub(r"\W+", "", str(name)).lower().replace("_", "")


def _resolve_named_column(cols, *candidates):
    if not cols:
        return None
    upper_map = {str(c).upper(): c for c in cols}
    compact_map = {_normalize_col(c): c for c in cols}
    for candidate in candidates:
        key = str(candidate or "").strip()
        if not key:
            continue
        if key.upper() in upper_map:
            return upper_map[key.upper()]
        compact = _normalize_col(key)
        if compact in compact_map:
            return compact_map[compact]
    return None


def _parse_schema_columns(raw: str):
    if not raw:
        return []
    cols = []
    for part in raw.split(","):
        name = _to_snake_upper(part.strip())
        if name and name not in cols:
            cols.append(name)
    return cols


def _csv_read_options():
    # Dynamics F&O / CDM export CSVs are headerless (pandas uses header=None).
    return {
        "header": False,
        "inferSchema": False,
        "ignoreLeadingWhiteSpace": True,
        "ignoreTrailingWhiteSpace": True,
        "mode": "PERMISSIVE",
        "columnNameOfCorruptRecord": "_corrupt_record",
    }


def _is_missing_path_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "path does not exist",
            "path_not_found",
            "42k03",
            "cannot find",
            "no such file",
        )
    )


def _cleanup_landing_staging(prefix: str) -> None:
    """
    Best-effort recursive delete under ``landing_prefix`` after a successful MERGE.

    Runs in Databricks so staging is cleared even when the orchestrator worker dies
    while polling ``submit_and_wait`` (worker-side cleanup is still attempted).
    """
    path = str(prefix or "").strip().rstrip("/")
    if not path:
        print("spark_staging_cleanup_skipped reason=empty_prefix")
        return
    try:
        dbutils.fs.rm(path, recurse=True)
        print(f"spark_staging_cleanup_done prefix={path}")
    except Exception as exc:
        if _is_missing_path_error(exc):
            print(f"spark_staging_cleanup_skipped prefix={path} reason=already_empty")
            return
        print(f"spark_staging_cleanup_failed prefix={path} err={exc}")


def _read_csv_glob(glob_path: str, *, optional=False):
    from pyspark.sql.types import StringType, StructField, StructType

    try:
        reader = spark.read
        for key, value in _csv_read_options().items():
            reader = reader.option(key, value)
        # Directory + recursiveFileLookup + pathGlobFilter — do NOT pass ``**/*.csv``
        # as a path (Databricks treats it literally → PATH_NOT_FOUND).
        reader = reader.option("recursiveFileLookup", "true")
        reader = reader.option("pathGlobFilter", "*.csv")
        schema_cols = _parse_schema_columns(schema_columns_raw)
        if not schema_cols:
            raise RuntimeError(
                "schema_columns widget is empty — refusing CSV header inference. "
                "Pass attribute names from Dynamics model.json."
            )
        # If caller passed a glob ending in /**/*.csv, strip to directory prefix.
        prefix = glob_path
        for suffix in ("/**/*.csv", "/**/*.CSV", "/*.csv", "/*.CSV"):
            if prefix.lower().endswith(suffix.lower()):
                prefix = prefix[: -len(suffix)]
                break
        schema = StructType([StructField(c, StringType(), True) for c in schema_cols])
        return reader.schema(schema).csv(prefix)
    except Exception as exc:
        if optional and _is_missing_path_error(exc):
            print(f"No CSV files for optional prefix; skipping {glob_path} ({exc})")
            return None
        print(f"Failed to read CSV prefix {glob_path}: {exc}")
        if optional:
            return None
        raise


def _read_csv_paths(paths, *, optional=False):
    from pyspark.sql.types import StringType, StructField, StructType

    if not paths:
        return None
    try:
        reader = spark.read
        for key, value in _csv_read_options().items():
            reader = reader.option(key, value)
        schema_cols = _parse_schema_columns(schema_columns_raw)
        if schema_cols:
            schema = StructType([StructField(c, StringType(), True) for c in schema_cols])
            return reader.schema(schema).csv(paths)
        return reader.csv(paths)
    except Exception as exc:
        if optional and _is_missing_path_error(exc):
            return None
        print(f"Failed to read CSV paths: {exc}")
        return None


def _sql_try_parse_timestamp(col_sql: str) -> str:
    """
    Parse Dynamics / CDM timestamps — pandas ``pd.to_datetime(..., utc=True)`` parity.

    Never use bare ``try_to_timestamp(raw)`` (can invent year +10400 from numeric strings).
    """
    raw = f"trim(cast({col_sql} as string))"
    return (
        f"CASE "
        f"WHEN {raw} IS NULL OR {raw} = '' "
        f"OR lower({raw}) IN ('null', 'none', 'nan') THEN CAST(NULL AS TIMESTAMP) "
        f"WHEN {raw} RLIKE '^[+-]?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?$' "
        f"THEN CAST(NULL AS TIMESTAMP) "
        f"ELSE coalesce("
        f"try_to_timestamp({raw}, 'M/d/yyyy h:mm:ss a'), "
        f"try_to_timestamp({raw}, 'M/d/yyyy H:mm:ss'), "
        f"try_to_timestamp({raw}, 'MM/dd/yyyy h:mm:ss a'), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSSSXXX\"), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSSSX\"), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSSS'Z'\"), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'\"), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss.SSS'Z'\"), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss'Z'\"), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSSS\"), "
        f"try_to_timestamp({raw}, \"yyyy-MM-dd'T'HH:mm:ss\"), "
        f"try_to_timestamp({raw}, 'yyyy-MM-dd HH:mm:ss.SSSSSSS'), "
        f"try_to_timestamp({raw}, 'yyyy-MM-dd HH:mm:ss'), "
        f"try_to_timestamp({raw}, 'yyyy-MM-dd')"
        f") END"
    )


def _target_typed_columns(base_table):
    try:
        rows = spark.sql(f"DESCRIBE TABLE {base_table}").collect()
    except Exception as exc:
        print(f"Could not DESCRIBE {base_table}: {exc}")
        return {}
    out = {}
    for r in rows:
        name = str(getattr(r, "col_name", None) or (r[0] if r is not None else "") or "")
        dtype = str(
            getattr(r, "data_type", None)
            or (r[1] if r is not None and len(r) > 1 else "")
            or ""
        ).lower()
        if not name or name.startswith("#") or name.startswith("Part "):
            continue
        out[name.upper()] = dtype
    return out


def _source_value_sql(col_name: str, target_types: dict) -> str:
    src = f"source.`{col_name}`"
    dtype = (target_types or {}).get(str(col_name).upper(), "")
    if "timestamp" in dtype or dtype == "date":
        return _sql_try_parse_timestamp(src)
    if "boolean" in dtype or dtype == "bool":
        return (
            f"coalesce(try_cast({src} as boolean), "
            f"case when lower(trim(cast({src} as string))) in "
            f"('true','t','1','yes','y') then true else false end)"
        )
    if any(
        token in dtype
        for token in (
            "bigint",
            "long",
            "int",
            "smallint",
            "tinyint",
            "decimal",
            "numeric",
            "double",
            "float",
            "real",
        )
    ):
        cast_type = dtype if ("decimal" in dtype or "numeric" in dtype) else (
            "bigint"
            if ("bigint" in dtype or "long" in dtype)
            else "double"
            if ("double" in dtype or "float" in dtype or "real" in dtype)
            else "int"
        )
        return f"try_cast({src} as {cast_type})"
    if _normalize_col(col_name) == "sinkmodifiedon":
        return _sql_try_parse_timestamp(src)
    return src


def _ensure_target_columns(base_table, staging_cols):
    try:
        target_upper = {
            str(r.col_name).upper()
            for r in spark.sql(f"SHOW COLUMNS IN {base_table}").collect()
        }
    except Exception as exc:
        print(f"Could not read target columns for {base_table}: {exc}")
        return
    for col in staging_cols:
        if str(col).upper() not in target_upper:
            print(f"Adding missing target column `{col}`")
            spark.sql(f"ALTER TABLE {base_table} ADD COLUMN `{col}` STRING")


_EMPTY_STRING_LITERALS = ("null", "none", "nan")


def _normalize_df(df):
    if df is None:
        return None
    from pyspark.sql import functions as F

    renames = {}
    used = set()
    for col_name in df.columns:
        target = _to_snake_upper(col_name)
        candidate = target
        suffix = 1
        while candidate in used and candidate != col_name:
            candidate = f"{target}_{suffix}"
            suffix += 1
        used.add(candidate)
        if candidate != col_name:
            renames[col_name] = candidate
    for old_name, new_name in renames.items():
        df = df.withColumnRenamed(old_name, new_name)
    if "ISDELETE" in df.columns and "IS_DELETE" not in df.columns:
        df = df.withColumnRenamed("ISDELETE", "IS_DELETE")
    if "IS_DELETE" not in df.columns:
        df = df.withColumn("IS_DELETE", F.lit(False))
    else:
        # Never hard-cast — ANSI throws CAST_INVALID_INPUT on misaligned values.
        c = "`IS_DELETE`"
        df = df.withColumn(
            "IS_DELETE",
            F.expr(
                f"coalesce("
                f"try_cast({c} as boolean), "
                f"case when lower(trim(cast({c} as string))) in "
                f"('true','t','1','yes','y') then true else false end"
                f")"
            ),
        )
    sink_col = _resolve_named_column(df.columns, "SINK_MODIFIED_ON", "SinkModifiedOn")
    if sink_col and sink_col.upper() != "SINK_MODIFIED_ON" and "SINK_MODIFIED_ON" not in df.columns:
        df = df.withColumnRenamed(sink_col, "SINK_MODIFIED_ON")
    return df


def _nullify_empty_string_fields(df):
    if df is None:
        return None
    from pyspark.sql import functions as F

    skip_cols = {"IS_DELETE", "_CORRUPT_RECORD"}
    for col_name in df.columns:
        if str(col_name).upper() in skip_cols:
            continue
        trimmed = F.trim(F.col(col_name).cast("string"))
        lowered = F.lower(trimmed)
        df = df.withColumn(
            col_name,
            F.when(
                F.col(col_name).isNull() | (trimmed == "") | lowered.isin(*_EMPTY_STRING_LITERALS),
                F.lit(None).cast("string"),
            ).otherwise(F.col(col_name)),
        )
    return df


def _coerce_timestamp_column(df, col_name: str):
    """Parse Dynamics timestamps — pandas coerce parity; never bare try_to_timestamp."""
    if df is None or not col_name or col_name not in df.columns:
        return df
    from pyspark.sql import functions as F

    c = "`{}`".format(str(col_name).replace("`", ""))
    df = df.withColumn(col_name, F.expr(_sql_try_parse_timestamp(c)))
    return df


def _normalize_staging_df(df):
    if df is None:
        return None
    df = _nullify_empty_string_fields(_normalize_df(df))
    for candidate in ("SINK_MODIFIED_ON", "SinkModifiedOn", "SINK_CREATED_ON", "SinkCreatedOn"):
        col = _resolve_named_column(df.columns, candidate)
        if col:
            df = _coerce_timestamp_column(df, col)
    return df


def _list_csv_paths(prefix: str):
    try:
        entries = dbutils.fs.ls(prefix)
    except Exception as exc:
        print(f"Failed to list {prefix}: {exc}")
        return []
    paths = []
    for entry in entries:
        name = str(getattr(entry, "name", "") or "")
        path = str(getattr(entry, "path", "") or "")
        if name.lower().endswith(".csv"):
            paths.append(path.rstrip("/"))
    return sorted(paths)


def _load_staging_csvs(landing_prefix: str):
    try:
        df = _read_csv_glob(landing_prefix, optional=True)
        if df is not None:
            print(f"Loaded CSVs recursively from {landing_prefix}")
            return df
        print(f"No CSV files under {landing_prefix}")
    except Exception as exc:
        print(f"Recursive prefix read failed ({exc}); trying flat folder layout")

    try:
        dbutils.fs.ls(landing_prefix)
    except Exception as exc:
        if _is_missing_path_error(exc):
            print(f"Landing prefix missing or empty: {landing_prefix} ({exc})")
            return None
        print(f"Landing prefix list failed ({exc}); trying flat CSV list")

    csv_paths = _list_csv_paths(landing_prefix)
    if not csv_paths:
        return None
    print(f"Loaded {len(csv_paths)} CSV paths from flat layout under {landing_prefix}")
    return _read_csv_paths(csv_paths, optional=True)


def _filter_valid_pk_rows(df, pk_col):
    from pyspark.sql import functions as F

    pk = F.col(pk_col)
    pk_text = F.lower(F.trim(pk.cast("string")))
    return df.filter(
        pk.isNotNull()
        & (F.trim(pk.cast("string")) != "")
        & (~pk_text.isin("null", "none", "nan"))
    )


def _repartition_for_dedupe(df, pk_col):
    if pk_col not in df.columns:
        return df
    try:
        shuffle_parts = int(spark.conf.get("spark.sql.shuffle.partitions", "200"))
        shuffle_parts = max(200, shuffle_parts)
        return df.repartition(shuffle_parts, pk_col)
    except Exception as exc:
        print(f"repartition by {pk_col} skipped: {exc}")
        return df.coalesce(8)


def _staging_is_empty(df):
    if hasattr(df, "isEmpty"):
        return df.isEmpty()
    return df.limit(1).count() == 0


def _drop_spark_staging_table() -> None:
    global _SPARK_STAGING_TABLE
    table_ref = _SPARK_STAGING_TABLE
    if not table_ref:
        return
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_ref}")
        print(f"spark_staging_table_dropped table={table_ref}")
    except Exception as exc:
        print(f"spark_staging_table_drop_failed table={table_ref} err={exc}")
    finally:
        _SPARK_STAGING_TABLE = None


def _materialize_staging_df(
    df,
    *,
    landing_prefix: str,
    catalog: str = "",
    schema: str = "",
    table_name: str = "",
):
    """
    Force-read staged CSVs before MERGE (fail fast on FAILED_READ_FILE).

    Databricks serverless rejects ``df.cache()`` / ``PERSIST TABLE``. Validate with
    ``count()``, then materialize into a short-lived Delta table so MERGE does not
    re-scan landing CSVs.
    """
    global _SPARK_STAGING_TABLE

    try:
        row_count = df.count()
        print(f"Staging CSV validated rows={row_count} prefix={landing_prefix}")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read staged CSVs under {landing_prefix}: {exc}"
        ) from exc

    cat = str(catalog or "").strip()
    sch = str(schema or "").strip()
    if not cat or not sch:
        print("spark_staging_materialize: no catalog/schema; skipping delta staging table")
        return df

    import uuid

    safe_tbl = re.sub(r"\W+", "_", str(table_name or "staging"))[:32].strip("_") or "staging"
    temp_name = f"_ds_stg_{safe_tbl}_{uuid.uuid4().hex[:8]}"
    qualified = f"`{cat}`.`{sch}`.`{temp_name}`"
    try:
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(
            f"{cat}.{sch}.{temp_name}"
        )
        _SPARK_STAGING_TABLE = qualified
        materialized = spark.table(f"{cat}.{sch}.{temp_name}")
        print(
            f"Staging CSV materialized rows={row_count} table={qualified} "
            f"prefix={landing_prefix}"
        )
        return materialized
    except Exception as exc:
        print(
            f"Delta staging materialization failed ({exc}); "
            f"continuing with uncached dataframe (may re-read CSV)"
        )
        return df


def _resolve_pk_column(cols, pk_widget: str) -> str:
    upper_map = {str(c).upper(): c for c in cols}
    for candidate in (pk_widget, "ID", "RECID"):
        key = str(candidate or "").upper()
        if key and key in upper_map:
            return upper_map[key]
    raise RuntimeError(
        f"Primary key {pk_widget!r} not found in staged data columns={cols}"
    )


raw_df = _load_staging_csvs(landing_prefix)
if raw_df is None:
    print(f"No CSV files found under {landing_prefix}; nothing to merge.")
    dbutils.notebook.exit("no_files")

staging_df = _normalize_staging_df(raw_df)
if staging_df is None:
    print(f"No CSV rows loaded under {landing_prefix}; nothing to merge.")
    dbutils.notebook.exit("no_files")

pk = _resolve_pk_column(staging_df.columns, primary_key)
staging_df = _filter_valid_pk_rows(staging_df, pk)
staging_df = _materialize_staging_df(
    staging_df,
    landing_prefix=landing_prefix,
    catalog=catalog,
    schema=schema,
    table_name=table_name,
)
if _staging_is_empty(staging_df):
    print(f"No rows with valid primary key {pk} under {landing_prefix}; nothing to merge.")
    _drop_spark_staging_table()
    dbutils.notebook.exit("no_files")
staging_df = _repartition_for_dedupe(staging_df, pk)
staging_df.createOrReplaceTempView("dynamics_fo_staging")

# COMMAND ----------

cols = [r.col_name for r in spark.sql("SHOW COLUMNS IN dynamics_fo_staging").collect()]
if not cols:
    raise RuntimeError("Staging view has no columns")

pk = _resolve_pk_column(cols, primary_key)

resolved_deleted = None
# Source delete flag from F&O CSV (IsDelete → IS_DELETE). Never use meta col names.
for candidate in ("IS_DELETE", "IS_DELETED", "ISDELETED"):
    if candidate.upper() in [c.upper() for c in cols]:
        resolved_deleted = next(c for c in cols if c.upper() == candidate.upper())
        break

delete_predicate = (
    f"COALESCE(try_cast(source.`{resolved_deleted}` AS BOOLEAN), false)"
    if resolved_deleted
    else "false"
)

sink_modified_col = _resolve_named_column(
    cols, "SINK_MODIFIED_ON", "SinkModifiedOn"
)
sink_modified_upper = sink_modified_col.upper() if sink_modified_col else "SINK_MODIFIED_ON"

# Pandas parity meta columns (stamped at load time — not read from F&O CSV).
sync_col = (updated_at_col or "_FIVETRAN_SYNCED").strip() or "_FIVETRAN_SYNCED"
del_meta_col = (deleted_at_col or "_FIVETRAN_DELETED").strip() or "_FIVETRAN_DELETED"
meta_upper = {sync_col.upper(), del_meta_col.upper()}
for _meta in (sync_col, del_meta_col):
    stripped = str(_meta).upper().lstrip("_")
    if stripped:
        meta_upper.add(stripped)

update_cols = [
    c
    for c in cols
    if c.upper() != str(pk).upper()
    and c.upper() not in {"IS_DELETE", str(deleted_at_col).upper(), str(updated_at_col).upper()}
    and c.upper() != sink_modified_upper
    and c.upper() not in meta_upper
]

set_parts = []
seen = set()


def _append_set(col_name, expr):
    key = col_name.upper()
    if key in seen:
        return
    seen.add(key)
    set_parts.append(expr)


_ensure_target_columns(
    base_table,
    [c for c in cols if str(c).upper() not in meta_upper],
)
_existing = _target_typed_columns(base_table)
if sync_col.upper() not in _existing:
    spark.sql(f"ALTER TABLE {base_table} ADD COLUMN `{sync_col}` TIMESTAMP")
if del_meta_col.upper() not in _existing:
    spark.sql(f"ALTER TABLE {base_table} ADD COLUMN `{del_meta_col}` BOOLEAN")

target_types = _target_typed_columns(base_table)
print(
    "Target timestamp/date columns: "
    f"{sorted(k for k, v in target_types.items() if 'timestamp' in v or v == 'date')}"
)

for col in update_cols:
    src_expr = _source_value_sql(col, target_types)
    _append_set(
        col,
        f"target.`{col}` = CASE WHEN {delete_predicate} THEN target.`{col}` ELSE {src_expr} END",
    )
if resolved_deleted:
    _append_set(
        resolved_deleted,
        f"target.`{resolved_deleted}` = {_source_value_sql(resolved_deleted, target_types)}",
    )
if sink_modified_col:
    _append_set(
        sink_modified_col,
        f"target.`{sink_modified_col}` = {_source_value_sql(sink_modified_col, target_types)}",
    )

# Pandas: df[updated_at] = current_utc_time; df[deleted_at] = IS_DELETE
_append_set(sync_col, f"target.`{sync_col}` = current_timestamp()")
if resolved_deleted:
    _append_set(
        del_meta_col,
        f"target.`{del_meta_col}` = COALESCE(try_cast(source.`{resolved_deleted}` AS BOOLEAN), false)",
    )
else:
    _append_set(del_meta_col, f"target.`{del_meta_col}` = false")

business_cols = [c for c in cols if c.upper() not in meta_upper]
insert_cols = ", ".join(f"`{c}`" for c in business_cols + [sync_col, del_meta_col])
insert_vals = ", ".join(
    [_source_value_sql(c, target_types) for c in business_cols]
    + [
        "current_timestamp()",
        (
            f"COALESCE(try_cast(source.`{resolved_deleted}` AS BOOLEAN), false)"
            if resolved_deleted
            else "false"
        ),
    ]
)

if sink_modified_col:
    order_expr = f"{_sql_try_parse_timestamp(f'`{sink_modified_col}`')} DESC NULLS LAST"
else:
    print("WARNING: no SINK_MODIFIED_ON column found; dedupe ORDER BY is non-deterministic")
    order_expr = f"`{pk}` DESC"

dedupe_sql = f"""
SELECT * FROM (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY `{pk}`
    ORDER BY {order_expr}
  ) AS _rn
  FROM dynamics_fo_staging
) WHERE _rn = 1
"""
spark.sql(dedupe_sql).createOrReplaceTempView("dynamics_fo_staging_deduped")

delete_flag = (
    f"COALESCE(try_cast(`{resolved_deleted}` AS BOOLEAN), false)"
    if resolved_deleted
    else "false"
)
orphan_filter_sql = f"""
SELECT s.*
FROM dynamics_fo_staging_deduped s
WHERE NOT ({delete_flag})
   OR EXISTS (
        SELECT 1 FROM {base_table} t WHERE t.`{pk}` = s.`{pk}`
   )
"""
spark.sql(orphan_filter_sql).createOrReplaceTempView("dynamics_fo_staging_filtered")

merge_sql = f"""
MERGE INTO {base_table} AS target
USING dynamics_fo_staging_filtered AS source
ON target.`{pk}` = source.`{pk}`
WHEN MATCHED THEN UPDATE SET {', '.join(set_parts)}
WHEN NOT MATCHED AND NOT ({delete_predicate}) THEN INSERT ({insert_cols}) VALUES ({insert_vals})
"""

print(merge_sql)
merge_result = spark.sql(merge_sql)

# Initial load: COUNT the base Delta table (cheap vs re-scanning staging; equals
# rows loaded when starting empty). Subsequent: MERGE metrics, else staging COUNT
# (previous incremental behavior).
row_count = None
if is_initial_load:
    row_count = int(
        spark.sql(f"SELECT COUNT(*) AS c FROM {base_table}").collect()[0]["c"]
    )
    print(
        f"Merged staging rows={row_count} into {base_table} "
        f"(from base table COUNT, initial_load=true)"
    )
else:
    try:
        metrics = merge_result.collect()[0].asDict() if merge_result is not None else {}
        inserted = int(metrics.get("num_inserted_rows") or 0)
        updated = int(metrics.get("num_updated_rows") or 0)
        affected = int(metrics.get("num_affected_rows") or 0)
        if inserted or updated:
            row_count = inserted + updated
            print(
                f"Merged staging rows={row_count} into {base_table} "
                f"(MERGE metrics inserted={inserted} updated={updated})"
            )
        elif affected:
            row_count = affected
            print(
                f"Merged staging rows={row_count} into {base_table} "
                f"(MERGE metrics affected={affected})"
            )
    except Exception as metrics_exc:
        print(f"MERGE metrics unavailable: {metrics_exc}")

    if row_count is None:
        row_count = int(
            spark.sql(
                "SELECT COUNT(*) AS c FROM dynamics_fo_staging_filtered"
            ).collect()[0]["c"]
        )
        print(
            f"Merged staging rows={row_count} into {base_table} "
            f"(from staging_filtered COUNT)"
        )

if staging_cleanup_after_merge:
    _cleanup_landing_staging(landing_prefix)
else:
    print("spark_staging_cleanup_skipped reason=disabled")

_drop_spark_staging_table()
dbutils.notebook.exit(str(int(row_count or 0)))
