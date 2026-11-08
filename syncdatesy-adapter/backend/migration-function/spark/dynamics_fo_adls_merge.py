# Databricks notebook source
# MAGIC %md
# MAGIC # Dynamics F&O ADLS merge
# MAGIC Reads CSVs from ``abfss://`` landing prefix and MERGEs into Delta.
# MAGIC Supports staging bulk layout ``.../table/{date}/data/*.csv`` and export flat ``.../date/table/*.csv``.

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
    """
    Best-effort Spark tuning for bulk CSV reads and shuffles.

    Databricks serverless rejects some static SQL conf keys (AQE is already on).
    Skip unavailable settings instead of failing the job.
    """
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


def _normalize_col(name: str) -> str:
    normalized = re.sub(r"\W+", "", str(name)).lower().replace("_", "")
    return normalized


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


def _resolve_named_column(cols, *candidates):
    """Return the actual column name matching any candidate (case-insensitive / alnum-compact)."""
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
    # header=True would skip the first data row of every shard.
    return {
        "header": False,
        "inferSchema": False,
        "ignoreLeadingWhiteSpace": True,
        "ignoreTrailingWhiteSpace": True,
        "mode": "PERMISSIVE",
        "columnNameOfCorruptRecord": "_corrupt_record",
    }


def _csv_reader(paths_or_glob, *, recursive=False, path_glob_filter=None):
    from pyspark.sql.types import StringType, StructField, StructType

    schema_cols = _parse_schema_columns(schema_columns_raw)
    if not schema_cols:
        raise RuntimeError(
            "schema_columns widget is empty — refusing CSV header inference. "
            "Pass attribute names from Dynamics model.json so headerless shards "
            "cannot become column names."
        )
    reader = spark.read
    for key, value in _csv_read_options().items():
        reader = reader.option(key, value)
    if recursive:
        # Prefer directory + recursiveFileLookup + pathGlobFilter.
        # Passing ``prefix/**/*.csv`` as a path is treated literally and raises PATH_NOT_FOUND.
        reader = reader.option("recursiveFileLookup", "true")
        if path_glob_filter:
            reader = reader.option("pathGlobFilter", path_glob_filter)
    schema = StructType([StructField(c, StringType(), True) for c in schema_cols])
    return reader.schema(schema).csv(paths_or_glob)


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


def _read_csv_paths(paths, *, optional=False):
    if not paths:
        return None
    try:
        return _csv_reader(paths, recursive=False)
    except Exception as exc:
        if optional and _is_missing_path_error(exc):
            print(f"No CSV paths for optional read; skipping ({exc})")
            return None
        print(f"Failed to read CSV paths {paths}: {exc}")
        return None


def _read_csv_prefix(prefix: str, *, optional=False):
    """Recursive CSV read under a directory prefix (staging bulk layout)."""
    try:
        return _csv_reader(prefix, recursive=True, path_glob_filter="*.csv")
    except Exception as exc:
        if optional and _is_missing_path_error(exc):
            print(f"No CSV files under optional prefix; skipping {prefix} ({exc})")
            return None
        print(f"Failed to read CSV prefix {prefix}: {exc}")
        if optional:
            return None
        raise


def _sql_try_parse_timestamp(col_sql: str) -> str:
    """
    Parse Dynamics / CDM timestamp strings — pandas parity with ``pd.to_datetime(..., utc=True)``.

    Important: do **not** call bare ``try_to_timestamp(raw)``. That path can cast numeric
    tick/ID strings into absurd timestamps (e.g. year +10400). Prefer explicit formats;
    unmatched values become NULL (same as pandas ``errors='coerce'``).
    """
    raw = f"trim(cast({col_sql} as string))"
    return (
        f"CASE "
        f"WHEN {raw} IS NULL OR {raw} = '' "
        f"OR lower({raw}) IN ('null', 'none', 'nan') THEN CAST(NULL AS TIMESTAMP) "
        # Reject pure numeric strings (ticks / IDs / amounts) — never treat as epoch.
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
    """Return {COL_UPPER: data_type_lower} for existing Delta target columns."""
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
    """Map source column to target type safely (Dynamics CSV strings → typed Delta cols)."""
    src = f"source.`{col_name}`"
    dtype = (target_types or {}).get(str(col_name).upper(), "")
    if "timestamp" in dtype or dtype == "date":
        return _sql_try_parse_timestamp(src)
    if "boolean" in dtype or dtype == "bool":
        # try_cast only — hard CAST throws on Dynamics junk / misaligned shards.
        return (
            f"coalesce(try_cast({src} as boolean), "
            f"case when lower(trim(cast({src} as string))) in "
            f"('true','t','1','yes','y') then true else false end)"
        )
    # Numeric targets: Dynamics often puts sentinel dates / blanks in int columns.
    # Hard CAST → CAST_INVALID_INPUT (e.g. '1900-01-01T00:00:00.0000000Z' → BIGINT).
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
    # SinkModifiedOn must parse even when target column is missing / STRING for dedupe fidelity
    if _normalize_col(col_name) == "sinkmodifiedon":
        return _sql_try_parse_timestamp(src)
    return src


def _ensure_target_columns(base_table, staging_cols):
    """Add any staging columns missing from the Delta target (schema drift across date folders)."""
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
        # Never use hard CAST — ANSI mode throws CAST_INVALID_INPUT on values like
        # '576578278' when a shard/schema misalign puts a non-bool into IS_DELETE.
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
    # Canonicalize SinkModifiedOn variants (SINKMODIFIEDON → SINK_MODIFIED_ON).
    sink_col = _resolve_named_column(df.columns, "SINK_MODIFIED_ON", "SinkModifiedOn")
    if sink_col and sink_col.upper() != "SINK_MODIFIED_ON" and "SINK_MODIFIED_ON" not in df.columns:
        df = df.withColumnRenamed(sink_col, "SINK_MODIFIED_ON")
    return df


def _nullify_empty_string_fields(df):
    """
    Dynamics delete/data CSVs keep PK + IS_DELETE populated; optional middle columns
    often arrive as '', 'null', or whitespace — normalize those to SQL NULL.
    """
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
    """
    Parse Dynamics / CDM timestamp strings without hard-failing on locale formats.

    Matches pandas ``pd.to_datetime(..., errors='coerce', utc=True)`` — never use bare
    ``try_to_timestamp`` (can invent year +10400 from numeric strings).
    """
    if df is None or not col_name or col_name not in df.columns:
        return df
    from pyspark.sql import functions as F

    # Quote identifier safely for SQL expr (column names are SNAKE_UPPER alnum + _).
    c = "`{}`".format(str(col_name).replace("`", ""))
    df = df.withColumn(col_name, F.expr(_sql_try_parse_timestamp(c)))
    return df


def _normalize_staging_df(df):
    if df is None:
        return None
    df = _nullify_empty_string_fields(_normalize_df(df))
    # Coerce known Dynamics datetime columns used for dedupe / display.
    # Remaining TIMESTAMP target columns are parsed at MERGE via _source_value_sql
    # (same formats as pandas pd.to_datetime coerce).
    for candidate in ("SINK_MODIFIED_ON", "SinkModifiedOn", "SINK_CREATED_ON", "SinkCreatedOn"):
        col = _resolve_named_column(df.columns, candidate)
        if col:
            df = _coerce_timestamp_column(df, col)
    return df


def _load_staging_csvs(landing_prefix: str):
    """
    Read all staged/export CSVs in one pass.

    Use directory + recursiveFileLookup + pathGlobFilter (not ``**/*.csv`` glob —
    Databricks treats that as a literal path and raises PATH_NOT_FOUND).
    Empty staging must exit as no_files, not crash the job.
    """
    try:
        df = _read_csv_prefix(landing_prefix, optional=True)
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
    """Drop null/blank/placeholder primary keys (Snowflake/SQL worker parity)."""
    from pyspark.sql import functions as F

    pk = F.col(pk_col)
    pk_text = F.lower(F.trim(pk.cast("string")))
    return df.filter(
        pk.isNotNull()
        & (F.trim(pk.cast("string")) != "")
        & (~pk_text.isin("null", "none", "nan"))
    )


def _repartition_for_dedupe(df, pk_col):
    """Shuffle by high-cardinality GUID PK so window dedupe stays balanced."""
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
    Force-read staged CSVs before MERGE.

    Spark CSV reads are lazy; ``FAILED_READ_FILE`` can otherwise surface only
    when ``MERGE`` executes even though the notebook already printed SQL.

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
staging_df = _materialize_staging_df(staging_df, landing_prefix=landing_prefix)
if _staging_is_empty(staging_df):
    print(f"No rows with valid primary key {pk} under {landing_prefix}; nothing to merge.")
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
# Meta columns stamped at MERGE (pandas: df[updated_at]=now, df[deleted_at]=IS_DELETE).
_existing = _target_typed_columns(base_table)
if sync_col.upper() not in _existing:
    spark.sql(f"ALTER TABLE {base_table} ADD COLUMN `{sync_col}` TIMESTAMP")
if del_meta_col.upper() not in _existing:
    spark.sql(f"ALTER TABLE {base_table} ADD COLUMN `{del_meta_col}` BOOLEAN")

target_types = _target_typed_columns(base_table)
print(f"Target timestamp/date columns: "
      f"{sorted(k for k, v in target_types.items() if 'timestamp' in v or v == 'date')}")

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
    # try_to_timestamp avoids CAST_INVALID_INPUT on Dynamics locale strings.
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
# Snowflake parity: drop orphan deletes (IS_DELETE for IDs not already in target).
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
