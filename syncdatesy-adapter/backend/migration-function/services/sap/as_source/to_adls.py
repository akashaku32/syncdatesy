"""SAP OData to Azure Data Lake Storage (ADLS) Gen2  -  Orchestrator.

This is the **entry-point module** for the SAP OData -> ADLS replication service.
It contains only the orchestration logic (DB lifecycle helpers, the per-entity
worker, and the top-level ``migrate_sap_odata_to_adls`` function).  All
data-processing code lives in the three focused modules below:

    services.sap.source.py    -  shared constants, helpers, and DataFrame utilities
    services.sap.direct.py    -  direct HTTPS SAP load functions (load_initial, load_delta)
    services.sap.proxy.py     -  proxy gRPC SAP load functions (run_proxy_load_step,
                            is_proxy_connection, G--)

Import hierarchy (-> means "imports from"):
    services.sap.source   - no internal service deps
    services.sap.direct   -> services.sap.source
    services.sap.proxy    -> services.sap.source
    services.sap.as_source.to_adls  -> services.sap.source + services.sap.direct + services.sap.proxy
                         (this file)

Supported load methods
----------------------
INITIAL         -  Full snapshot to ``/initial/``.  Skipped on subsequent runs
                 unless ``delete_and_load=True``.
DELTA_ONLY      -  ODQ subscription init on first run (no files written);
                 incremental delta pulls on subsequent runs.
INITIAL_DELTA   -  Full snapshot on first run + delta tracking; incremental
                 delta pulls on subsequent runs.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.sap_odata_models import SAPODataADLSMigrationRequest
from services.snowflake_to_adls_service import (
    MigrationFailureTracker,
    _build_adls_service_client,
    _validate_adls_destination_access,
)
from utils.database_utils import (
    data_transfer_logs,
    postgres_connection,
    update_table_status_snowflake,
)
from utils.error_utils import (
    log_migration_completion,
    log_migration_failure,
    update_migration_status_log,
)
from utils.simple_progress_tracker import SimpleProgressTracker
from utils.timing_utils import (
    log_execution_time,
    print_timing_summary_table,
    reset_timing_records,
)

# -- Shared helpers (constants, DataFrame utils, ADLS write, schema fetch) -----
from services.sap.source import (
    SAPSourceDetails,
    extract_token_from_delta_link,
    fetch_metadata,
    force_https_url,
)

# -- Direct HTTP load functions ------------------------------------------------
from services.sap.direct import load_delta, load_initial

# -- Proxy gRPC load functions -------------------------------------------------
from services.sap.proxy import (
    _get_proxy_src_conn,
    is_proxy_connection,
    run_proxy_load_step,
)

_run_proxy_data_load = run_proxy_load_step

# -- Module-level logger -------------------------------------------------------
# Do NOT call setLevel() here  -  let the application root logger control levels.
logger = logging.getLogger(__name__)


def _resolve_celery_child_mode(request) -> bool:
    """True when Celery fan-out assigned this table group to a child worker."""
    return bool(getattr(request, "celery_child_mode", False)) or bool(
        getattr(request, "celery_fanout_mode", False)
    )


# ---------------------------------------------------------------------------
# ADLS directory management
# ---------------------------------------------------------------------------

@log_execution_time
def _delete_adls_initial_folder(
    folder_name: str,
    adls_config: Dict[str, Any],
) -> bool:
    """Delete the ``/initial/`` sub-directory for an entity from ADLS Gen2.

    Called when ``delete_and_load=True`` and the initial snapshot has already
    been written.  Removing the directory allows :func:`~services.sap.direct.load_initial`
    to write a fresh clean snapshot without stale files from the previous run.

    Uses best-effort deletion  -  a failure logs a warning but does **not** abort
    the pipeline; the fresh upload will still overwrite or coexist with old files.

    Args:
        folder_name: Entity-level base folder path (e.g. ``SERVICE_ENTITY``).
        adls_config: ADLS Gen2 connection parameters (must include
            ``account_name``, ``container_name``, and auth fields).

    Returns:
        ``True`` if the directory was deleted (or did not exist), ``False`` on error.
    """
    import os

    account_name   = adls_config.get("account_name", "UnknownAccount")
    container_name = adls_config.get("container_name", "UnknownContainer")
    initial_dir    = os.path.join(folder_name, "initial").replace("\\", "/")

    logger.info(
        "[DELETE_AND_LOAD] Target ADLS folder: '%s' in container '%s' / account '%s'.",
        initial_dir, container_name, account_name,
    )

    try:
        service_client = _build_adls_service_client(adls_config)
        fs_client      = service_client.get_file_system_client(container_name)

        # -- Non-HNS: delete individual files listed under the directory -------
        try:
            paths         = fs_client.get_paths(path=initial_dir)
            deleted_count = 0
            for path in paths:
                try:
                    if getattr(path, "is_directory", False):
                        continue
                    fs_client.get_file_client(path.name).delete_file()
                    logger.info("[DELETE_AND_LOAD] Deleted file '%s'.", path.name)
                    deleted_count += 1
                except Exception as file_err:
                    logger.warning(
                        "[DELETE_AND_LOAD] Failed to delete '%s': %s  -  continuing.",
                        path.name, file_err,
                    )
            if deleted_count > 0:
                logger.info(
                    "[DELETE_AND_LOAD] Non-HNS cleanup: deleted %d file(s) under '%s'.",
                    deleted_count, initial_dir,
                )
        except Exception as list_err:
            logger.info("[DELETE_AND_LOAD] Benign warning listing paths: %s", list_err)

        # -- HNS: attempt directory-client deletion (required for HNS accounts) -
        try:
            fs_client.get_directory_client(initial_dir).delete_directory()
            logger.info("[DELETE_AND_LOAD] HNS directory '%s' deleted.", initial_dir)
        except Exception as dir_err:
            err_str = str(dir_err).lower()
            if any(k in err_str for k in ("pathnotfound", "blobnotfound", "does not exist")):
                logger.info("[DELETE_AND_LOAD] Directory '%s' already absent.", initial_dir)
            else:
                logger.info("[DELETE_AND_LOAD] HNS delete benign failure: %s", dir_err)

        return True

    except Exception as exc:
        err_str = str(exc).lower()
        if any(k in err_str for k in ("pathnotfound", "blobnotfound", "does not exist")):
            logger.info(
                "[DELETE_AND_LOAD] ADLS directory '%s' did not exist  -  nothing to delete.",
                initial_dir,
            )
            return True
        logger.warning(
            "[DELETE_AND_LOAD] Failed to delete ADLS directory '%s' "
            "(account: %s, container: %s): %s  -  proceeding with fresh upload.",
            initial_dir, account_name, container_name, exc,
        )
        return False


# ---------------------------------------------------------------------------
# DB lifecycle helpers
# ---------------------------------------------------------------------------

@log_execution_time
def _get_table_etl_state(
    connection_id: int,
    table_name: str,
    existing_conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Retrieve the full ETL lifecycle state for a table from ``source_table_selection``.

    Returns a dictionary with keys:
        ``service_name``, ``delta_token``, ``load_method``, ``output_file_name``,
        ``file_format``, ``compression_method``, ``partition_data_by_date``,
        ``delete_and_load``, ``initial_completed_flag``, ``load_method_locked``,
        ``first_sync_timestamp``, ``last_delta_run_timestamp``, ``delta_name``,
        ``row_filter_config``, ``locked_row_filter_config``.

    When no row exists yet for the table, safe default values are returned so
    that callers never need to handle a ``None`` result.

    Args:
        connection_id: Connection identifier.
        table_name: OData entity / table name.
        existing_conn: Existing PostgreSQL connection (optional).

    Returns:
        ETL state dictionary.
    """
    should_close = False
    if existing_conn:
        conn = existing_conn
    else:
        conn = postgres_connection()
        should_close = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    service_name,
                    delta_token,
                    load_method,
                    output_file_name,
                    file_format,
                    compression_method,
                    partition_delta_by_date AS partition_data_by_date,
                    initial_completed_flag,
                    load_method_locked,
                    first_sync_timestamp,
                    last_delta_run_timestamp,
                    delete_and_load,
                    delta_name,
                    row_filter_config,
                    locked_row_filter_config
                FROM source_table_selection
                WHERE connection_id = %s AND "table" = %s
                """,
                (connection_id, table_name),
            )
            row = cursor.fetchone()
            if row:
                def safe_parse_json(val: Any) -> Optional[Any]:
                    """Deserialise JSON safely, returning None on failure."""
                    if not val:
                        return None
                    if isinstance(val, dict):
                        return val
                    if isinstance(val, str):
                        try:
                            return json.loads(val)
                        except Exception:
                            return None
                    return val

                return {
                    "service_name":             row[0],
                    "delta_token":              row[1],
                    "load_method":              row[2] or "initial",
                    "output_file_name":         row[3],
                    "file_format":              row[4] or "parquet",
                    "compression_method":       row[5] or "none",
                    "partition_data_by_date":  bool(row[6]) if row[6] is not None else False,
                    "initial_completed_flag":   bool(row[7]) if row[7] is not None else False,
                    "load_method_locked":       bool(row[8]) if row[8] is not None else False,
                    "first_sync_timestamp":     row[9],
                    "last_delta_run_timestamp": row[10],
                    # delete_and_load: when True, wipe /initial/ and re-run on every pipeline execution.
                    "delete_and_load":          bool(row[11]) if row[11] is not None else False,
                    "delta_name":               row[12],
                    "row_filter_config":         safe_parse_json(row[13]),
                    "locked_row_filter_config":  safe_parse_json(row[14]),
                }

            # No row yet  -  return safe defaults.
            return {
                "service_name":             None,
                "delta_token":              None,
                "load_method":              "initial",
                "output_file_name":         None,
                "file_format":              "parquet",
                "compression_method":       "none",
                "partition_data_by_date":  False,
                "initial_completed_flag":   False,
                "load_method_locked":       False,
                "first_sync_timestamp":     None,
                "last_delta_run_timestamp": None,
                "delete_and_load":          False,
                "delta_name":               None,
                "row_filter_config":         None,
                "locked_row_filter_config":  None,
            }
    finally:
        if should_close and conn:
            conn.close()


@log_execution_time
def _update_table_etl_state(
    connection_id: int,
    table_name: str,
    **kwargs: Any,
) -> None:
    """Persist ETL lifecycle state updates into ``source_table_selection``.

    Only fields that are explicitly passed as keyword arguments are updated;
    all others are left unchanged (partial-update pattern).  This allows
    setting a field to ``None`` / ``NULL`` when the caller passes ``field=None``.

    Supported fields:
        ``delta_token``, ``initial_completed_flag``, ``load_method_locked``,
        ``first_sync_timestamp``, ``last_delta_run_timestamp``, ``delete_and_load``,
        ``row_filter_config``, ``locked_row_filter_config``.

    Args:
        connection_id: Connection identifier.
        table_name: OData entity / table name.
        **kwargs: Fields to update (values may be ``None`` to set NULL). Can include ``existing_conn``.
    """
    existing_conn = kwargs.pop("existing_conn", None)

    supported_fields = [
        "delta_token",
        "initial_completed_flag",
        "load_method_locked",
        "first_sync_timestamp",
        "last_delta_run_timestamp",
        "delete_and_load",
        "row_filter_config",
        "locked_row_filter_config",
    ]

    # Normalise delta_token: extract raw token from a full URL if one was passed.
    if "delta_token" in kwargs and kwargs["delta_token"] is not None:
        kwargs["delta_token"] = extract_token_from_delta_link(kwargs["delta_token"])

    set_clauses: List[str] = []
    values: List[Any]      = []

    for field in supported_fields:
        if field in kwargs:
            set_clauses.append(f"{field} = %s")
            val = kwargs[field]
            # psycopg2 handles dicts natively for JSONB but JSON strings are safer.
            if field in ("row_filter_config", "locked_row_filter_config") and isinstance(val, (dict, list)):
                val = json.dumps(val)
            values.append(val)

    if not set_clauses:
        logger.debug("[_update_table_etl_state] No fields to update for table '%s'.", table_name)
        return

    values.extend([connection_id, table_name])
    sql = (
        f"UPDATE source_table_selection SET {', '.join(set_clauses)} "
        f'WHERE connection_id = %s AND "table" = %s'
    )

    should_close = False
    if existing_conn:
        conn = existing_conn
    else:
        conn = postgres_connection()
        should_close = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, values)
            conn.commit()
            logger.info(
                "[_update_table_etl_state] Updated ETL state for table '%s' "
                "(connection %s): %s",
                table_name, connection_id,
                {k: v for k, v in zip(set_clauses, values[:-2])},
            )
    finally:
        if should_close and conn:
            conn.close()


# ---------------------------------------------------------------------------
# Row-filter dry-run validation
# ---------------------------------------------------------------------------

@log_execution_time
def _dry_run_filter(
    server_address: str,
    service_name: str,
    entity_name: str,
    filter_expr: str,
    username: str,
    password: str,
) -> None:
    """Validate an OData ``$filter`` expression against the live SAP Gateway.

    Sends a ``$top=1`` request with the filter so that SAP returns an error
    immediately if the expression is syntactically or semantically invalid.
    Called before the full pagination / subscription loops to fail fast.

    Args:
        server_address: SAP host:port.
        service_name: OData service name.
        entity_name: OData entity set name.
        filter_expr: ``$filter`` expression to validate.
        username: SAP Basic Auth username.
        password: SAP Basic Auth password.

    Raises:
        ValueError: If SAP rejects the filter (non-200 response).
        requests.exceptions.Timeout: If the request times out.
        requests.exceptions.RequestException: On any network error.
    """
    import requests
    from urllib.parse import quote as _quote
    from services.sap.source import SAP_REQUEST_TIMEOUT

    logger.info(
        "[_dry_run_filter] Validating filter on entity '%s': %s",
        entity_name, filter_expr,
    )
    url = (
        f"https://{server_address}/sap/opu/odata/sap/{service_name}"
        f"/{entity_name}?$format=json&$top=1&$filter={_quote(filter_expr)}"
    )
    url = force_https_url(url)

    try:
        try:
            from utils.sap_tls_helper import build_session_from_config as build_session_for_source
            session = build_session_for_source({"host": server_address})
        except Exception:
            session = requests.Session()
            session.verify = True

        resp = session.get(url, auth=(username, password), timeout=SAP_REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.error(
                "[_dry_run_filter] SAP rejected filter (HTTP %d): %s",
                resp.status_code, resp.text[:500],
            )
            raise ValueError(f"SAP rejected filter for '{entity_name}': {resp.text[:300]}")
        logger.info("[_dry_run_filter] SAP OData dry-run validation succeeded.")
    except requests.exceptions.Timeout as te:
        logger.error("[_dry_run_filter] SAP OData dry-run timed out: %s", te)
        raise
    except requests.exceptions.RequestException as req_err:
        logger.error("[_dry_run_filter] SAP OData dry-run network error: %s", req_err)
        raise


# ---------------------------------------------------------------------------
# Per-entity worker
# ---------------------------------------------------------------------------

@log_execution_time
def _process_sap_odata_to_adls_file(
    request: SAPODataADLSMigrationRequest,
    table_name: str,
    source_config: Dict[str, Any],
    adls_config: Dict[str, Any],
    server_address: str,
    username: str,
    password: str,
    job_name: str,
    container_name: str,
    failure_tracker: MigrationFailureTracker,
    existing_conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute a single SAP OData entity sync and write output to ADLS Gen2.

    Decision flow based on ``load_method`` and current ETL lifecycle state:

    **INITIAL**:
        First run  (``initial_completed_flag=False``) -> full snapshot to ``/initial/``
        Subsequent -> skip (already completed, no delta tracking)

    **DELTA_ONLY**:
        First run  (``initial_completed_flag=False``) -> ODQ subscription init,
                                                         discard snapshot, save delta_token,
                                                         lock load_method, set first_sync_timestamp
        Subsequent (``initial_completed_flag=True``)  -> delta pull using stored delta_token,
                                                         write to ``/delta/`` or ``/delta/YYYY-MM-DD/``,
                                                         update delta_token + last_delta_run_timestamp

    **INITIAL_DELTA**:
        First run  -> full snapshot to ``/initial/``, save delta_token, lock load_method
        Subsequent -> delta pull (same as DELTA_ONLY subsequent)

    Args:
        request: Structured SAP -> ADLS migration request payload.
        table_name: OData entity / table to replicate.
        source_config: SAP connection parameters dict.
        adls_config: ADLS Gen2 connection parameters dict.
        server_address: Formatted SAP host:port string.
        username: SAP Basic Auth username.
        password: SAP Basic Auth password.
        job_name: Migration session job name for logging.
        container_name: ADLS container name.
        failure_tracker: Shared failure aggregator across concurrent workers.
        existing_conn: Optional existing DB connection to use.

    Returns:
        Table-level result dict with ``status``, ``rows``, ``remote_path``,
        and ``error`` keys.
    """
    table_started  = datetime.now(timezone.utc)
    connection_id  = request.connection_id
    migration_session_id = request.migration_session_id

    logger.info(
        "[%s] Starting SAP OData -> ADLS replication  -  connection_id=%s session_id=%s job=%s",
        table_name, connection_id, migration_session_id, job_name,
    )

    db_conn = existing_conn or postgres_connection()
    should_close = existing_conn is None
    try:
        update_table_status_snowflake(connection_id, table_name, "in_progress", job_name, migration_session_id)

        try:
            SimpleProgressTracker.reset_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                existing_conn=db_conn,
            )
        except Exception as exc:
            logger.warning("[%s] Failed to reset staging count: %s", table_name, exc)

        # -- 1. Retrieve ETL lifecycle state ------------------------------------------
        etl_state    = _get_table_etl_state(connection_id, table_name, existing_conn=db_conn)
        service_name = etl_state["service_name"]
        if not service_name:
            raise ValueError(f"Metadata definition not found for table '{table_name}'.")

        # -- 2. Resolve runtime settings ---------------------------------------
        table_settings = (request.table_export_settings or {}).get(table_name) or {}

        # -- 3. Resolve effective load_method ----------------------------------
        if etl_state["load_method_locked"]:
            load_method = etl_state["load_method"]
            logger.info("[%s] load_method locked -> using stored: %s", table_name, load_method)
        else:
            load_method = (
                table_settings.get("load_method")
                or etl_state["load_method"]
                or "initial"
            )
            logger.info("[%s] load_method resolved: %s", table_name, load_method)

        delete_and_load = etl_state.get("delete_and_load", False)

        # -- Row-filter drift detection -----------------------------------------
        # If the filter was changed after the delta subscription was locked, we
        # must refuse to proceed until delete_and_load=True resubscribes.
        if etl_state.get("load_method_locked") and not delete_and_load:
            live_filter   = etl_state.get("row_filter_config")
            locked_filter = etl_state.get("locked_row_filter_config")
            if live_filter != locked_filter:
                logger.error(
                    "[%s] row_filter_config was modified after the delta subscription was "
                    "locked. Set delete_and_load=True to resubscribe.", table_name,
                )
                raise ValueError(
                    f"row_filter_config for table '{table_name}' was modified after the delta "
                    "subscription was locked, but the change was never applied. "
                    "Set delete_and_load=True to resubscribe with the new filter."
                )

        # Determine if this run is a subsequent delta pull (affects filter validation).
        is_delta_pull = False
        if load_method in ("delta", "initial_delta"):
            is_first_run  = not etl_state["initial_completed_flag"] or delete_and_load or not etl_state["delta_token"]
            is_delta_pull = not is_first_run

        # Detect connection mode once  -  used throughout all load-method branches.
        proxy_mode = is_proxy_connection(source_config)

        # -- Row-filter resolution ---------------------------------------------
        row_filter_expr   = None
        row_filter_config = None

        if etl_state.get("row_filter_config"):
            from models.sap_row_filter import RowFilterConfig
            from services.sap.row_filter import (
                build_row_filter_expression,
                validate_row_filter_config,
            )

            row_filter_config = RowFilterConfig(**etl_state["row_filter_config"])

            # Validate against live metadata only on first-run / subscription-init.
            if not is_delta_pull:
                meta_url = f"https://{server_address}/sap/opu/odata/sap/{service_name}/$metadata"
                if proxy_mode:
                    from sap_proxy_agent.client.dataynchor_client import get_metadata
                    meta_data = get_metadata(
                        source_type="sap-odata",
                        cust_id=str(source_config.get("proxy_agent") or ""),
                        url=meta_url,
                        source_config=_get_proxy_src_conn(source_config),
                    )
                else:
                    meta_data = fetch_metadata(meta_url, username, password, entity_name=table_name)

                if not meta_data or "columns" not in meta_data:
                    raise ValueError(
                        f"Failed to fetch metadata schema for entity '{table_name}' to validate filter."
                    )

                validate_row_filter_config(row_filter_config, meta_data["columns"])

            # Compile the configured expression for every load type. Initial
            # runs validate it against metadata; delta runs reuse the exact
            # same persisted configuration without repeating that validation.
            row_filter_expr = build_row_filter_expression(row_filter_config)

            # Dry-run validates the filter expression against the live SAP endpoint.
            if row_filter_expr and not is_delta_pull:
                _dry_run_filter(
                    server_address=server_address,
                    service_name=service_name,
                    entity_name=table_name,
                    filter_expr=row_filter_expr,
                    username=username,
                    password=password,
                )

            logger.info(
                "[%s] Row filter expression resolved for %s load: %r",
                table_name,
                "delta" if is_delta_pull else "initial",
                row_filter_expr,
            )

        # -- Build load configuration ------------------------------------------
        output_name = (
            table_settings.get("output_file_name")
            or etl_state["output_file_name"]
            or f"{service_name}_{table_name}"
        )
        folder_name = output_name.replace("/", "_").replace("\\", "_").replace(" ", "_")

        compression_method = (
            table_settings.get("compression_method")
            or etl_state["compression_method"]
            or "none"
        )
        if compression_method.lower() == "none":
            compress, compress_type = False, None
        else:
            compress, compress_type = True, compression_method.lower()

        file_rec_count          = request.chunk_count or 100000
        partition_data_by_date = etl_state["partition_data_by_date"]
        delta_name              = etl_state.get("delta_name")
        now_utc                 = datetime.now(timezone.utc)

        src_details = SAPSourceDetails(
            name=service_name,
            entity_name=table_name,
            file_rec_count=file_rec_count,
            pagination_count=file_rec_count,
            folder_name=folder_name,
            compress=compress,
            compress_type=compress_type,
            delta_name=delta_name,
            filter_expr=row_filter_expr,
            row_filter_config=row_filter_config,
            timestamp=now_utc,
        )

        rows_affected = 0

        # G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--
        # INITIAL  -  Full snapshot only.  Supports delete_and_load flag.
        #
        # delete_and_load=False (default):
        #   First run  (initial_completed_flag=False) -> full snapshot to /initial/
        #   Subsequent (initial_completed_flag=True)  -> skip (already done)
        #
        # delete_and_load=True:
        #   Every run: delete /initial/ from ADLS, reset ETL flags, re-run snapshot.
        # G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--
        if load_method == "initial":
            delete_and_load = etl_state.get("delete_and_load", False)

            if etl_state["initial_completed_flag"] and not delete_and_load:
                logger.info(
                    "[%s] INITIAL already completed and delete_and_load=False  -  skipping.",
                    table_name,
                )
            else:
                if delete_and_load and etl_state["initial_completed_flag"]:
                    logger.info(
                        "[%s] INITIAL  -  delete_and_load=True; wiping /initial/ and re-running snapshot.",
                        table_name,
                    )
                    _delete_adls_initial_folder(folder_name, adls_config)
                    _update_table_etl_state(
                        connection_id, table_name,
                        initial_completed_flag=False,
                        first_sync_timestamp=None,
                        locked_row_filter_config=None,
                    )
                else:
                    logger.info("[%s] INITIAL  -  running full snapshot to /initial/.", table_name)

                if proxy_mode:
                    logger.info("[%s] INITIAL  -  routing snapshot through proxy agent.", table_name)
                    success = _run_proxy_data_load(
                        source_config=source_config,
                        adls_config=adls_config,
                        src_details=src_details,
                        load_method="initial",
                        partition_data_by_date=partition_data_by_date,
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        connection_id=connection_id,
                        cmp_id=request.cmp_id,
                        user_id=request.user_id,
                    )
                else:
                    success, rows_affected, _ = load_initial(
                        server_address=server_address,
                        src_details=src_details,
                        username=username,
                        password=password,
                        adls_config=adls_config,
                        connection_id=connection_id,
                        migration_session_id=migration_session_id,
                        job_name=job_name,
                        source_config=source_config,
                    )

                if not success:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Initial snapshot failed for entity '{table_name}'.",
                    )
                
                if not proxy_mode:
                    now_utc = datetime.now(timezone.utc)
                    _update_table_etl_state(
                        connection_id, table_name,
                        existing_conn=db_conn,
                        initial_completed_flag=True,
                        first_sync_timestamp=now_utc,
                        locked_row_filter_config=etl_state.get("row_filter_config"),
                    )

        # G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--
        # DELTA_ONLY  -  Subscribe + discard on first run; pull deltas subsequently.
        # G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--
        elif load_method == "delta":
            delete_and_load = etl_state.get("delete_and_load", False)
            is_first_run    = not etl_state["initial_completed_flag"] or delete_and_load or not etl_state["delta_token"]

            if is_first_run:
                if delete_and_load and etl_state["initial_completed_flag"]:
                    logger.info("[%s] DELTA_ONLY  -  delete_and_load=True; resetting ODQ subscription.", table_name)
                    _update_table_etl_state(
                        connection_id, table_name,
                        existing_conn=db_conn,
                        initial_completed_flag=False,
                        delta_token=None,
                        first_sync_timestamp=None,
                        load_method_locked=False,
                        locked_row_filter_config=None,
                    )
                else:
                    logger.info("[%s] DELTA_ONLY  -  initialising ODQ subscription (no snapshot).", table_name)

                if proxy_mode:
                    logger.info("[%s] DELTA_ONLY  -  routing subscription init through proxy agent.", table_name)
                    success = _run_proxy_data_load(
                        source_config=source_config,
                        adls_config=adls_config,
                        src_details=src_details,
                        load_method="delta",
                        partition_data_by_date=partition_data_by_date,
                        delta_token_url=etl_state.get("delta_token"),
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        connection_id=connection_id,
                        cmp_id=request.cmp_id,
                        user_id=request.user_id,
                    )
                    raw_token = etl_state.get("delta_token")
                    if not raw_token:
                        logger.warning(
                            "[%s] Proxy delta init completed without a persisted delta token. "
                            "Subsequent runs need the agent to surface the token via state.",
                            table_name,
                        )
                else:
                    success, _, delta_link = load_initial(
                        server_address=server_address,
                        src_details=src_details,
                        username=username,
                        password=password,
                        adls_config=adls_config,
                        delta_only=True,
                        source_config=source_config,
                    )
                    if not success or not delta_link:
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"DELTA_ONLY initialisation failed for entity '{table_name}'. "
                                "No delta link returned by SAP ODQ."
                            ),
                        )
                    raw_token = extract_token_from_delta_link(delta_link)

                if not proxy_mode:
                    _update_table_etl_state(
                        connection_id, table_name,
                        existing_conn=db_conn,
                        delta_token=raw_token,
                        initial_completed_flag=True,
                        load_method_locked=True,
                        first_sync_timestamp=now_utc,
                        locked_row_filter_config=etl_state.get("row_filter_config"),
                    )

            else:
                # -- Subsequent run: pull incremental changes ------------------
                stored_delta_token = etl_state["delta_token"]
                if not stored_delta_token:
                    raise ValueError(
                        f"Delta synchronization cannot continue because SAP did not return a "
                        "Delta Token during the initial synchronization. "
                        "Reset the entity via the Delete button to reinitialise."
                    )
                logger.info("[%s] DELTA_ONLY  -  pulling incremental changes.", table_name)

                if proxy_mode:
                    success = _run_proxy_data_load(
                        source_config=source_config,
                        adls_config=adls_config,
                        src_details=src_details,
                        load_method="delta",
                        partition_data_by_date=partition_data_by_date,
                        delta_token=stored_delta_token,
                        use_delta_rpc=True,
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        connection_id=connection_id,
                        cmp_id=request.cmp_id,
                        user_id=request.user_id,
                    )
                    next_delta_link = None
                else:
                    success, rows_affected, next_delta_link = load_delta(
                        delta_token_url=stored_delta_token,
                        src_details=src_details,
                        username=username,
                        password=password,
                        adls_config=adls_config,
                        server_address=server_address,
                        partition_data_by_date=partition_data_by_date,
                        filter_expr=row_filter_expr,
                        connection_id=connection_id,
                        migration_session_id=migration_session_id,
                        job_name=job_name,
                        source_config=source_config,
                    )

                if not success:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Delta pull failed for entity '{table_name}'.",
                    )

                if proxy_mode:
                    next_delta_token = stored_delta_token
                elif next_delta_link:
                    next_delta_token = extract_token_from_delta_link(next_delta_link)
                else:
                    next_delta_token = stored_delta_token
                    logger.info(
                        "[%s] No new delta link returned. Retaining stored token: %s",
                        table_name, next_delta_token,
                    )

                if not proxy_mode:
                    _update_table_etl_state(
                        connection_id, table_name,
                        existing_conn=db_conn,
                        delta_token=next_delta_token,
                        last_delta_run_timestamp=datetime.now(timezone.utc),
                    )

        # G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--
        # INITIAL_DELTA  -  Snapshot + track-changes on first run; deltas after.
        # G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--G--
        elif load_method == "initial_delta":
            delete_and_load = etl_state.get("delete_and_load", False)
            is_first_run    = not etl_state["initial_completed_flag"] or delete_and_load or not etl_state["delta_token"]

            if is_first_run:
                if delete_and_load and etl_state["initial_completed_flag"]:
                    logger.info(
                        "[%s] INITIAL_DELTA  -  delete_and_load=True; wiping /initial/ "
                        "and re-running snapshot to capture fresh delta link.", table_name,
                    )
                    _delete_adls_initial_folder(folder_name, adls_config)
                    _update_table_etl_state(
                        connection_id, table_name,
                        existing_conn=db_conn,
                        initial_completed_flag=False,
                        delta_token=None,
                        first_sync_timestamp=None,
                        load_method_locked=False,
                        locked_row_filter_config=None,
                    )
                else:
                    logger.info(
                        "[%s] INITIAL_DELTA  -  running full snapshot + capturing delta link.",
                        table_name,
                    )

                if proxy_mode:
                    logger.info(
                        "[%s] INITIAL_DELTA  -  routing snapshot through proxy agent. "
                        "etl_state: initial_completed_flag=%s load_method_locked=%s stored_delta_token=%r",
                        table_name,
                        etl_state.get("initial_completed_flag"),
                        etl_state.get("load_method_locked"),
                        etl_state.get("delta_token"),
                    )
                    success = _run_proxy_data_load(
                        source_config=source_config,
                        adls_config=adls_config,
                        src_details=src_details,
                        load_method="initial_delta",
                        partition_data_by_date=partition_data_by_date,
                        delta_token=etl_state.get("delta_token"),
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        connection_id=connection_id,
                        cmp_id=request.cmp_id,
                        user_id=request.user_id,
                    )
                    delta_link = None
                else:
                    success, rows_affected, delta_link = load_initial(
                        server_address=server_address,
                        src_details=src_details,
                        username=username,
                        password=password,
                        adls_config=adls_config,
                        connection_id=connection_id,
                        migration_session_id=migration_session_id,
                        job_name=job_name,
                        source_config=source_config,
                    )

                if not success:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Initial snapshot failed for entity '{table_name}'.",
                    )

                if proxy_mode:
                    # Re-read the ETL state from DB  -  the agent callback (InternalMigrationProgressUpdate)
                    # is responsible for writing the delta_token after initial load completes.
                    refreshed_state = _get_table_etl_state(connection_id, table_name, existing_conn=db_conn)
                    raw_token = refreshed_state.get("delta_token")
                    logger.info(
                        "[%s] PROXY INITIAL_DELTA first-run complete. "
                        "Refreshed DB state: initial_completed_flag=%s load_method_locked=%s delta_token=%r. "
                        "Agent callback is responsible for persisting the token.",
                        table_name,
                        refreshed_state.get("initial_completed_flag"),
                        refreshed_state.get("load_method_locked"),
                        raw_token,
                    )
                    if not raw_token:
                        logger.warning(
                            "[%s] Proxy initial_delta completed but delta_token is still NULL in DB. "
                            "The agent callback has NOT written the token yet (async). "
                            "Next run will re-check; if this persists, verify the agent is POSTing "
                            "to InternalMigrationProgressUpdateAPIView with delta_token set.",
                            table_name,
                        )
                    else:
                        logger.info(
                            "[%s] Proxy initial_delta: delta_token already in DB = %r. "
                            "Next run will use delta RPC.", table_name, raw_token,
                        )
                elif delta_link:
                    raw_token = extract_token_from_delta_link(delta_link)
                else:
                    raw_token = None
                    logger.warning(
                        "[%s] INITIAL_DELTA: No delta link returned during initial snapshot. "
                        "Subsequent delta runs will fail.", table_name,
                    )

                if not proxy_mode:
                    _update_table_etl_state(
                        connection_id, table_name,
                        existing_conn=db_conn,
                        delta_token=raw_token,
                        initial_completed_flag=True,
                        load_method_locked=True,
                        first_sync_timestamp=now_utc,
                        locked_row_filter_config=etl_state.get("row_filter_config"),
                    )

            else:
                # -- Subsequent run: pull incremental changes ------------------
                stored_delta_token = etl_state["delta_token"]
                if not stored_delta_token:
                    raise ValueError(
                        f"Delta synchronization cannot continue because SAP did not return a "
                        "Delta Token during the initial synchronization. "
                        "Reset the entity via the Delete button to reinitialise."
                    )
                logger.info("[%s] INITIAL_DELTA  -  pulling incremental changes.", table_name)

                if proxy_mode:
                    logger.info(
                        "[%s] INITIAL_DELTA subsequent run  -  pulling delta via proxy agent. "
                        "stored_delta_token=%r use_delta_rpc=True",
                        table_name, stored_delta_token,
                    )
                    success = _run_proxy_data_load(
                        source_config=source_config,
                        adls_config=adls_config,
                        src_details=src_details,
                        load_method="delta",
                        partition_data_by_date=partition_data_by_date,
                        delta_token=stored_delta_token,
                        use_delta_rpc=True,
                        job_name=job_name,
                        migration_session_id=migration_session_id,
                        connection_id=connection_id,
                        cmp_id=request.cmp_id,
                        user_id=request.user_id,
                    )
                    next_delta_link = None
                else:
                    success, rows_affected, next_delta_link = load_delta(
                        delta_token_url=stored_delta_token,
                        src_details=src_details,
                        username=username,
                        password=password,
                        adls_config=adls_config,
                        server_address=server_address,
                        partition_data_by_date=partition_data_by_date,
                        filter_expr=row_filter_expr,
                        connection_id=connection_id,
                        migration_session_id=migration_session_id,
                        job_name=job_name,
                        source_config=source_config,
                    )

                if not success:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Delta pull failed for entity '{table_name}'.",
                    )

                if proxy_mode:
                    next_delta_token = stored_delta_token
                elif next_delta_link:
                    next_delta_token = extract_token_from_delta_link(next_delta_link)
                else:
                    next_delta_token = stored_delta_token
                    logger.info(
                        "[%s] No new delta link returned. Retaining stored token: %s",
                        table_name, next_delta_token,
                    )

                if not proxy_mode:
                    _update_table_etl_state(
                        connection_id, table_name,
                        existing_conn=db_conn,
                        delta_token=next_delta_token,
                        last_delta_run_timestamp=datetime.now(timezone.utc),
                    )

        else:
            raise ValueError(
                f"Unknown load_method '{load_method}' for entity '{table_name}'. "
                "Expected one of: initial, delta, initial_delta."
            )

        # -- 4. Mark success and log data transfer metrics ---------------------
        if proxy_mode:
            logger.info("[%s] SAP OData -> ADLS proxy agent accepted request. Leaving DB status in_progress but returning success to bypass task drop.", table_name)
            return {
                "table_name":  table_name,
                "status":      "success",
                "is_proxy":    True,
                "rows":        rows_affected,
                "columns":     0,
                "remote_path": (
                    f"abfss://{container_name}@{adls_config.get('account_name')}"
                    f".dfs.core.windows.net/{folder_name}"
                ),
                "error": None,
            }

        table_completed_at = datetime.now(timezone.utc)
        duration_sec       = (table_completed_at - table_started).total_seconds()
        logger.info(
            "[%s] SAP OData -> ADLS replication completed  -  rows=%d duration=%.2fs",
            table_name, rows_affected, duration_sec,
        )

        update_table_status_snowflake(
            connection_id, table_name, "completed", job_name, migration_session_id,
            sync_end_time=table_completed_at,
        )

        data_transfer_logs(
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            usr_id=request.user_id,
            table_name=table_name,
            start_time=table_started,
            end_time=table_completed_at,
            inserts=rows_affected,
            columns_data=0,
            session=migration_session_id,
            job_name=job_name,
            migration_session_id=migration_session_id,
            existing_conn=db_conn,
        )

        # -- 5. Update staging record count immediately (per-table, not post-all) --
        # Calling here  -  inside the worker  -  means the DB row and the WebSocket
        # push happen as soon as THIS table finishes, giving the UI live progress
        # rather than a single bulk update after every table has completed.
        try:
            SimpleProgressTracker.set_staging_count(
                connection_id=connection_id,
                migration_session_id=migration_session_id,
                job_name=job_name,
                table_name=table_name,
                count_value=rows_affected,
                existing_conn=db_conn,
            )
        except Exception as tracker_err:
            # Non-fatal: staging count display is cosmetic; never block the pipeline.
            logger.warning(
                "[%s] Staging count update failed (non-fatal): %s", table_name, tracker_err
            )

        return {
            "table_name":  table_name,
            "status":      "success",
            "rows":        rows_affected,
            "columns":     0,
            "remote_path": (
                f"abfss://{container_name}@{adls_config.get('account_name')}"
                f".dfs.core.windows.net/{folder_name}"
            ),
            "error": None,
        }

    except Exception as table_err:
        logger.exception("Error migrating entity '%s': %s", table_name, table_err)
        error_msg = (
            str(table_err.detail)
            if isinstance(table_err, HTTPException)
            else str(table_err)
        )

        failure_tracker.add_failure(table_name, error_msg, "SAP OData to ADLS")
        update_table_status_snowflake(connection_id, table_name, "failed", job_name, migration_session_id)

        try:
            from utils.database_utils import get_migration_record_id
            from utils.error_utils import (
                update_table_migration_status_log,
                insert_tool_log,
                log_error_to_tool_log,
            )

            conn_err = db_conn
            migration_record_id = None
            if conn_err is not None:
                with conn_err.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO error_log_entry (cmp_id, connection_id, message, function_name, created_on) "
                        "VALUES (%s, %s, %s, %s, NOW())",
                        (
                            request.cmp_id,
                            connection_id,
                            f"Entity '{table_name}' migration failed: {error_msg}",
                            "migrate_sap_odata_to_adls",
                        ),
                    )
                    conn_err.commit()
                    migration_record_id = get_migration_record_id(
                        cursor, job_name=job_name, table_name=table_name, connection_id=connection_id,
                    )

            if migration_record_id:
                updated = update_table_migration_status_log(
                    migration_record_id=migration_record_id,
                    message=f"Entity '{table_name}' migration failed: {error_msg}",
                    status="F",
                    session=migration_session_id,
                    job_name=job_name,
                    connection_id=connection_id,
                    existing_conn=conn_err,
                )
                if not updated:
                    insert_tool_log(
                        cmp_id=request.cmp_id,
                        connection_id=connection_id,
                        usr_id=request.user_id,
                        table_name=table_name,
                        status="F",
                        message=f"Entity '{table_name}' migration failed: {error_msg}",
                        session=migration_session_id,
                        job_name=job_name,
                        migration_record_id=migration_record_id,
                    )
            else:
                log_error_to_tool_log(
                    cmp_id=request.cmp_id,
                    connection_id=connection_id,
                    user_id=request.user_id,
                    table_name=table_name,
                    error_message=f"Entity '{table_name}' migration failed: {error_msg}",
                    session=migration_session_id,
                    job_name=job_name,
                )
        except Exception as db_log_err:
            logger.warning("Failed to log error to database: %s", db_log_err)

        return {
            "table_name":  table_name,
            "status":      "failed",
            "rows":        0,
            "columns":     0,
            "remote_path": None,
            "error":       error_msg,
        }
    finally:
        if should_close and db_conn:
            try:
                db_conn.close()
                logger.info("=--- PostgreSQL connection closed for table '%s'", table_name)
                print(f"=--- Database connection closed (table: '{table_name}')")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Central execution orchestrator (public entry point)
# ---------------------------------------------------------------------------

@log_execution_time
def migrate_sap_odata_to_adls(request: SAPODataADLSMigrationRequest) -> Dict[str, Any]:
    reset_timing_records()
    logger.info(
        "Entering migrate_sap_odata_to_adls | connection_id=%s | tables=%d",
        request.connection_id, len(request.selected_tables or []),
    )
    started_at           = datetime.now(timezone.utc)
    connection_id        = request.connection_id
    migration_session_id = request.migration_session_id
    job_name             = request.job_name or f"SAPOData-ADLS Migration {connection_id}"
    failure_tracker      = MigrationFailureTracker()
    celery_child_mode    = _resolve_celery_child_mode(request)
    execution_order      = request.execution_order or "sequential"
    if celery_child_mode and execution_order == "parallel":
        execution_order = "sequential"

    results = {
        "status":               "success",
        "status_code":          200,
        "job_name":             job_name,
        "connection_id":        connection_id,
        "migration_session_id": migration_session_id,
        "started_at":           started_at.isoformat(),
        "completed_at":         None,
        "tables":               {},
        "total_tables":         len(request.selected_tables or []),
        "successful_tables":    0,
        "failed_tables":        0,
        "total_rows":           0,
        "service_name":         "SAPOData-ADLS",
    }

    top_db_conn = postgres_connection()
    try:
        # 1. Update overall session log state to In-Progress (parent job only).
        if not celery_child_mode:
            update_migration_status_log(
                job_name=job_name,
                message="Migration in progress",
                status="I",
                session=migration_session_id,
                log_id=request.log_id,
            )

        # 2. Extract connection configuration payloads.
        source_config = request.source_config or {}
        adls_config   = dict(request.destination_config or {})

        host   = source_config.get("host")
        port   = source_config.get("port")
        username = source_config.get("username")
        password = source_config.get("password")

        server_address = f"{host}:{port}" if port else host

        # 3. Validate ADLS storage access.
        service_client   = _build_adls_service_client(adls_config)
        container_name   = adls_config.get("container_name")
        file_system_client = service_client.get_file_system_client(container_name)
        _validate_adls_destination_access(
            file_system_client,
            account_name=adls_config.get("account_name"),
            container_name=container_name,
        )

        # 4a. Proxy mode  -  collect every table's settings and fire ONE gRPC call per RPC type.
        if is_proxy_connection(source_config):
            from services.sap.proxy import run_proxy_batch_load

            initial_payloads = []   # startdataload RPC
            delta_payloads   = []   # delta_load RPC

            for tbl in request.selected_tables:
                tbl_state    = _get_table_etl_state(connection_id, tbl)
                tbl_settings = (request.table_export_settings or {}).get(tbl) or {}

                tbl_load_method = (
                    tbl_state["load_method"] if tbl_state["load_method_locked"]
                    else (tbl_settings.get("load_method") or tbl_state["load_method"] or "initial")
                )
                tbl_output_name = (
                    tbl_settings.get("output_file_name")
                    or tbl_state["output_file_name"]
                    or f"{tbl_state['service_name']}_{tbl}"
                )
                tbl_folder  = tbl_output_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
                tbl_comp    = (tbl_settings.get("compression_method") or tbl_state["compression_method"] or "none").lower()
                tbl_compress = tbl_comp != "none"
                tbl_stored_token = tbl_state.get("delta_token")

                # Determine whether this is a first-run (initial snapshot) or a subsequent delta pull.
                if tbl_load_method in ("delta", "initial_delta"):
                    is_first_run = (
                        not tbl_state["initial_completed_flag"]
                        or tbl_state.get("delete_and_load")
                        or not tbl_stored_token
                    )
                else:
                    # Initial-only tables are sent to the proxy only once.
                    # delete_and_load explicitly requests a fresh snapshot.
                    is_first_run = (
                        not tbl_state["initial_completed_flag"]
                        or tbl_state.get("delete_and_load")
                    )

                if tbl_load_method == "initial" and not is_first_run:
                    logger.info(
                        "[BATCH_PROXY] table=%s initial load already completed; skipping.",
                        tbl,
                    )
                    continue

                tbl_src = SAPSourceDetails(
                    name=tbl_state["service_name"],
                    entity_name=tbl,
                    file_rec_count=request.chunk_count or 100000,
                    folder_name=tbl_folder,
                    compress=tbl_compress,
                    compress_type=tbl_comp if tbl_compress else None,
                    delta_name=tbl_state.get("delta_name"),
                )

                payload = {
                    "src_details":             tbl_src,
                    "load_method":             tbl_load_method if is_first_run else "delta",
                    "partition_data_by_date": bool(tbl_state.get("partition_data_by_date")),
                    "delta_token":             tbl_stored_token,
                }

                logger.info(
                    "[BATCH_PROXY] table=%s load_method=%s is_first_run=%s "
                    "initial_completed_flag=%s load_method_locked=%s stored_delta_token=%r",
                    tbl, tbl_load_method, is_first_run,
                    tbl_state.get("initial_completed_flag"),
                    tbl_state.get("load_method_locked"),
                    tbl_stored_token,
                )

                if is_first_run:
                    initial_payloads.append(payload)
                else:
                    delta_payloads.append(payload)

            if initial_payloads:
                logger.info("[BATCH_PROXY] Sending %d table(s) via startdataload RPC.", len(initial_payloads))
                run_proxy_batch_load(
                    source_config=source_config,
                    destination_config=adls_config,
                    table_payloads=initial_payloads,
                    use_delta_rpc=False,
                    job_name=job_name,
                    migration_session_id=migration_session_id,
                    connection_id=connection_id,
                    cmp_id=request.cmp_id,
                    user_id=request.user_id,
                    log_id=request.log_id,
                )

            if delta_payloads:
                logger.info("[BATCH_PROXY] Sending %d table(s) via delta_load RPC.", len(delta_payloads))
                run_proxy_batch_load(
                    source_config=source_config,
                    destination_config=adls_config,
                    table_payloads=delta_payloads,
                    use_delta_rpc=True,
                    job_name=job_name,
                    migration_session_id=migration_session_id,
                    connection_id=connection_id,
                    cmp_id=request.cmp_id,
                    user_id=request.user_id,
                    log_id=request.log_id,
                )

            results["status"]       = "in_progress"
            results["completed_at"] = datetime.now(timezone.utc).isoformat()
            return results

        # 4b. Direct mode  -  replicate entities concurrently or sequentially.
        table_results: List[Dict[str, Any]] = []

        if execution_order == "parallel" and len(request.selected_tables) > 1:
            # Cap at 3 workers to avoid ADLS throttling and SAP ODQ connection limits.
            max_workers = min(3, len(request.selected_tables))
            logger.info("Executing SAP OData -> ADLS in parallel mode | max_workers=%d", max_workers)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _process_sap_odata_to_adls_file,
                        request=request,
                        table_name=tbl,
                        source_config=source_config,
                        adls_config=adls_config,
                        server_address=server_address,
                        username=username,
                        password=password,
                        job_name=job_name,
                        container_name=container_name,
                        failure_tracker=failure_tracker,
                    ): tbl
                    for tbl in request.selected_tables
                }
                for future in as_completed(futures):
                    table_results.append(future.result())
        else:
            logger.info("Executing SAP OData -> ADLS in sequential mode.")
            for tbl in request.selected_tables:
                table_results.append(
                    _process_sap_odata_to_adls_file(
                        request=request,
                        table_name=tbl,
                        source_config=source_config,
                        adls_config=adls_config,
                        server_address=server_address,
                        username=username,
                        password=password,
                        job_name=job_name,
                        container_name=container_name,
                        failure_tracker=failure_tracker,
                        existing_conn=top_db_conn,
                    )
                )

        # 5. Aggregate results.
        # NOTE: staging_records_count was already written per-table inside
        # _process_sap_odata_to_adls_file so the UI sees live updates as each
        # table finishes.  We only tally the job-level counters here.
        for table_result in table_results:
            tbl = table_result["table_name"]
            results["tables"][tbl] = table_result
            if table_result["status"] == "success":
                results["successful_tables"] += 1
                results["total_rows"]        += table_result["rows"]
            else:
                results["failed_tables"] += 1

        has_proxy = any(tr.get("is_proxy") for tr in table_results)

        # 6. Resolve final session state.
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        if results["failed_tables"] == 0:
            if has_proxy:
                results["status"] = "in_progress"
            else:
                results["status"] = "success"
            results["status_code"] = 200
        else:
            results["status"]      = "failed"
            results["status_code"] = 422

        if results["status"] != "in_progress" and not celery_child_mode:
            log_migration_completion(
                execution_time=(datetime.now(timezone.utc) - started_at).total_seconds(),
                total_rows=results["total_rows"],
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                user_id=request.user_id,
                session=migration_session_id,
                total_tables=results["total_tables"],
                successful_tables=results["successful_tables"],
                failed_tables=results["failed_tables"],
                job_name=job_name,
                log_id=request.log_id,
                celery_child_mode=celery_child_mode,
            )

        if not celery_child_mode:
            failure_tracker.send_consolidated_error_email(
                connection_name=request.connection_name or f"SAP OData {connection_id}",
                service_name="SAP OData to ADLS",
                user_id=request.user_id,
                cmp_id=request.cmp_id,
                connection_id=connection_id,
                job_name=job_name,
                migration_session_id=migration_session_id,
                company_name=request.company_name or "Unknown Company",
                is_final_attempt=request.is_final_attempt,
            )

        return results

    except Exception as general_err:
        from utils.error_utils import format_exception_message
        clean_error_msg = format_exception_message(general_err)
        logger.error("SAP OData to ADLS general migration failure: %s", clean_error_msg)
        
        for tbl in request.selected_tables:
            if tbl not in results["tables"]:
                update_table_status_snowflake(connection_id, tbl, "failed", job_name, migration_session_id)

        log_migration_failure(
            job_name=job_name,
            error_message=clean_error_msg,
            session=migration_session_id,
            cmp_id=request.cmp_id,
            connection_id=connection_id,
            user_id=request.user_id,
            execution_time=(datetime.now(timezone.utc) - started_at).total_seconds(),
            total_rows=results.get("total_rows", 0),
            successful_tables=results.get("successful_tables", 0),
            total_tables=results.get("total_tables", 0),
            celery_child_mode=celery_child_mode,
        )
        raise ValueError(clean_error_msg) from general_err
    finally:
        if top_db_conn:
            try:
                top_db_conn.close()
                logger.info("=--- Top-level PostgreSQL connection closed for job '%s'", job_name)
                print(f"=--- Top-level database connection closed successfully (job: '{job_name}')")
            except Exception:
                pass
        print_timing_summary_table("SAP ODATA TO ADLS MIGRATION TIMING SUMMARY")


