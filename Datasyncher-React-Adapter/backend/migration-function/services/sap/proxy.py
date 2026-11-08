"""SAP OData proxy gRPC load functions.

This module handles all SAP data extraction that is routed **through** a
Datasynchor proxy agent via gRPC rather than connecting directly to SAP Gateway.
It contains the five helpers used by the orchestrator when
``source_config["connection_method"] == "proxy"``.

Public surface:
    - :func:`is_proxy_connection`       -  detect proxy mode from source_config
    - :func:`run_proxy_load_step`       -  execute any load step (initial/delta) via proxy

Internal helpers (used only within this module):
    - :func:`_get_proxy_credentials`    -  validate and extract proxy config fields
    - :func:`_get_proxy_src_conn`       -  build gRPC SrcConnConfig proto
    - :func:`_get_proxy_source_payload` -  build gRPC DataSourceConfig proto
    - :func:`_run_proxy_data_load`      -  fire the correct gRPC RPC call

All gRPC imports are done **lazily** (inside functions) so that the module can
be imported in environments without the sap_proxy_agent package installed  -  the
import error is deferred to the first actual proxy call.

Import hierarchy (-> means "imports from"):
    services.sap.source  - no internal service deps
    services.sap.proxy   -> services.sap.source          (this file)
    services.sap.as_source.to_adls -> services.sap.source + services.sap.direct + services.sap.proxy
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, List

import grpc

from services.sap.source import (
    SAPSourceDetails,
    extract_token_from_delta_link,
)

# -- Module-level logger -------------------------------------------------------
logger = logging.getLogger(__name__)


def is_proxy_connection(source_config: Dict[str, Any]) -> bool:
    """Return ``True`` when the source_config specifies proxy connection mode.

    Defaults to ``False`` (direct) when ``connection_method`` is absent or blank.

    Args:
        source_config: Source connection parameters dict from the migration request.

    Returns:
        ``True`` if ``connection_method`` equals ``"proxy"`` (case-insensitive),
        ``False`` otherwise.
    """
    return str(source_config.get("connection_method") or "direct").strip().lower() == "proxy"


# ---------------------------------------------------------------------------
# Credential / proto helpers (internal)
# ---------------------------------------------------------------------------

def _get_proxy_credentials(
    source_config: Dict[str, Any],
) -> Tuple[str, str, int, str, str]:
    """Extract and validate proxy connection credentials from source_config.

    Args:
        source_config: Source connection parameters dict.

    Returns:
        Five-tuple ``(proxy_agent, host, port, username, password)``.

    Raises:
        ValueError: If ``proxy_agent`` is missing or ``port`` is not a valid integer.
    """
    proxy_agent = str(source_config.get("proxy_agent") or "").strip()
    if not proxy_agent:
        raise ValueError("Proxy agent id is required when connection_method='proxy'.")

    host     = str(source_config.get("host") or "").strip()
    username = str(source_config.get("username") or "").strip()
    password = str(source_config.get("password") or "").strip()

    port_value = source_config.get("port")
    try:
        port = int(port_value) if port_value not in (None, "") else 443
    except (TypeError, ValueError):
        raise ValueError("Invalid SAP port in proxy source_config; expected an integer.")

    return proxy_agent, host, port, username, password


def _get_proxy_src_conn(source_config: Dict[str, Any]) -> Any:
    """Build a gRPC ``SrcConnConfig`` proto from the source connection parameters.

    Args:
        source_config: Source connection parameters dict.

    Returns:
        ``service_pb2.SrcConnConfig`` proto instance.
    """
    from sap_proxy_agent.generated import service_pb2  # Lazy import - only available at runtime

    _, host, port, username, password = _get_proxy_credentials(source_config)
    return service_pb2.SrcConnConfig(
        host=host,
        port=port,
        username=username,
        password=password,
    )


def _get_proxy_source_payload(
    *,
    src_details: SAPSourceDetails,
    load_method: str,
    partition_data_by_date: bool,
    delta_token: Optional[str] = None,
) -> Any:
    """Build a gRPC ``DataSourceConfig`` proto from entity-level configuration.

    Args:
        src_details: Entity-level load configuration bundle.
        load_method: One of ``"initial"``, ``"delta"``, ``"initial_delta"``.
        partition_data_by_date: Whether delta output is date-partitioned on the
            agent side.
        delta_token: Stored delta token or full delta link URL to resume from.

    Returns:
        ``service_pb2.DataSourceConfig`` proto instance.
    """
    from sap_proxy_agent.generated import service_pb2  # Lazy import - only available at runtime

    formatted_utc_time = (
        src_details.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        if getattr(src_details, "timestamp", None)
        else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    )

    return service_pb2.DataSourceConfig(
        name=src_details.name,
        compress=bool(src_details.compress),
        compress_type=str(src_details.compress_type or ""),
        folder_name=src_details.folder_name,
        load_method=load_method,
        partition_data_by_date=bool(partition_data_by_date),
        entity_name=src_details.entity_name,
        file_rec_count=int(src_details.file_rec_count or 0),
        pagination_count=int(src_details.pagination_count or src_details.file_rec_count or 0),
        delta_enabled=load_method in ("delta", "initial_delta"),
        delta_token=(
            extract_token_from_delta_link(delta_token)
            if delta_token
            else ""
        ),
        utc_time=formatted_utc_time,
    )


def format_proxy_error_msg(err: Exception, agent_id: str) -> str:
    """Format raw gRPC errors (_InactiveRpcError) into clean, user-understandable messages.

    The hub uses ``StatusCode.UNAVAILABLE`` when the **on‑premises** agent has no active
    session (see ``hub_grpc_server.py``). That must not be shown as "cannot reach hub":
    ``str(RpcError)`` often omits ``details()``, so we read code and details explicitly.
    """
    if isinstance(err, grpc.RpcError):
        code = err.code()
        details = (err.details() or "").strip()
        details_lower = details.lower()

        if code == grpc.StatusCode.UNAVAILABLE:
            # Hub: no agent registered for this cust_id / proxy_agent id.
            if (
                "not connected" in details_lower
                or "agent for customer" in details_lower
            ):
                return (
                    f"Proxy Agent '{agent_id}' is offline or not connected to the hub. "
                    "Ensure the on-premises proxy agent is running and enrolled with the same id."
                )
            if details:
                return (
                    "Unable to reach the SAP proxy hub over gRPC "
                    f"({details}). Check PROXY_AGENT_SERVER_HOST/PORT, mTLS mounts, and network policies. "
                    f"The configured proxy agent id is '{agent_id}'."
                )
            return (
                "Unable to reach the SAP proxy hub (gRPC UNAVAILABLE). "
                "Check PROXY_AGENT_SERVER_HOST/PORT and mTLS. "
                f"The configured proxy agent id is '{agent_id}'."
            )

        if code == grpc.StatusCode.UNAUTHENTICATED:
            return (
                f"Authentication failed for SAP proxy hub or agent '{agent_id}'. "
                "Verify client certificate and bootstrap token."
            )

        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            return (
                f"Proxy Agent '{agent_id}' request timed out. "
                f"The target SAP gateway or agent host did not respond in time."
            )

        if details:
            return f"Proxy Agent error ({code.name}): {details}"

    err_str = str(err)
    err_lower = err_str.lower()

    if "not connected" in err_lower:
        return (
            f"Proxy Agent '{agent_id}' is offline or disconnected. "
            f"Please ensure the proxy agent service is running on your server."
        )
    if "unavailable" in err_lower or "connection refused" in err_lower:
        return (
            f"Unable to reach the SAP proxy hub for proxy agent id '{agent_id}'. "
            f"Please check platform→hub network access, host/port, and mTLS; "
            f"ensure the on-premises agent is running if the hub is reachable."
        )
    if "unauthenticated" in err_lower or "certificate" in err_lower:
        return (
            f"Authentication failed for Proxy Agent '{agent_id}'. "
            f"Please verify client certificate and bootstrap token."
        )
    if "deadline exceeded" in err_lower or "timeout" in err_lower:
        return (
            f"Proxy Agent '{agent_id}' request timed out. "
            f"The target SAP gateway or agent host did not respond in time."
        )

    # Extract clean grpc_message if present in raw _InactiveRpcError string
    if "grpc_message:" in err_str:
        match = re.search(r'grpc_message:"([^"]+)"', err_str)
        if match:
            return f"Proxy Agent error: {match.group(1)}"

    return f"Proxy agent '{agent_id}' error: {err_str}"


def _format_proxy_error(err: Exception, agent_id: str) -> ValueError:
    """Format raw gRPC errors into a ValueError with a user-friendly message."""
    return ValueError(format_proxy_error_msg(err, agent_id))

# ---------------------------------------------------------------------------
# Core proxy dispatcher (internal)
# ---------------------------------------------------------------------------

def _run_proxy_data_load(
    *,
    cust_id: str,
    source_config: Dict[str, Any],
    adls_config: Dict[str, Any],
    src_details: SAPSourceDetails,
    load_method: str,
    use_delta_rpc: bool = False,
    delta_token: Optional[str] = None,
    partition_data_by_date: bool = False,
    job_name: Optional[str] = None,
    migration_session_id: Optional[str] = None,
    connection_id: Optional[int] = None,
    cmp_id: Optional[int] = None,
    user_id: Optional[int] = None,
    log_id: Optional[int] = None,
) -> bool:
    """Route a SAP load step through the proxy agent gRPC client.

    Selects the correct gRPC call based on ``use_delta_rpc``:
    - ``False`` -> ``startdataload`` RPC (initial / initial_delta / delta first-run)
    - ``True``  -> ``delta_load`` RPC (delta subsequent runs)

    Args:
        cust_id: Proxy agent identifier (from ``source_config["proxy_agent"]``).
        source_config: Source connection parameters dict.
        adls_config: ADLS Gen2 connection parameters dict.
        src_details: Entity-level load configuration bundle.
        load_method: One of ``"initial"``, ``"delta"``, ``"initial_delta"``.
        use_delta_rpc: When ``True``, fire the ``delta_load`` RPC instead of
            ``startdataload`` (used for subsequent delta pull runs).
        delta_token: Stored delta token or full delta link URL to resume from.
        partition_data_by_date: Whether to partition delta output by date on the
            agent side.

    Returns:
        ``True`` if the gRPC call reported success, ``False`` otherwise.
    """
    # Lazy imports: sap_proxy_agent is only available when backend/ is on PYTHONPATH.
    from sap_proxy_agent.client.dataynchor_client import delta_load, startdataload

    source_payload_list = [
        _get_proxy_source_payload(
            src_details=src_details,
            load_method=load_method,
            partition_data_by_date=partition_data_by_date,
            delta_token=delta_token,
        )
    ]
    source_conn      = _get_proxy_src_conn(source_config)
    destination_type = str(source_config.get("destination_type") or "adls")

    details_for_sts_update = {
        "migration_session_id": str(migration_session_id) if migration_session_id else "",
        "job_name": str(job_name) if job_name else "",
        "connection_id": str(connection_id) if connection_id else "",
        "cmp_id": str(cmp_id) if cmp_id else "",
        "user_id": str(user_id) if user_id else "",
        "log_id": str(log_id) if log_id else "",
    }

    agent_adls_config = dict(adls_config or {})
    if agent_adls_config:
        agent_adls_config["TENANT_ID"] = agent_adls_config.get("tenant_id", "")
        agent_adls_config["CLIENT_ID"] = agent_adls_config.get("client_id", "")
        agent_adls_config["CLIENT_SECRET"] = agent_adls_config.get("client_secret", "")
        agent_adls_config["CONTAINER_NAME"] = agent_adls_config.get("container_name", "")
        agent_adls_config["STORAGE_ACCOUNT_NAME"] = agent_adls_config.get("account_name") or agent_adls_config.get("storage_account_name", "")

    try:
        if use_delta_rpc:
            logger.info(
                "[SAP_ODATA_PROXY] Triggering delta RPC for entity '%s' via agent '%s'.",
                src_details.entity_name, cust_id,
            )
            accepted = bool(
                delta_load(
                    cust_id=cust_id,
                    sources=source_payload_list,
                    source_config=source_conn,
                    destination_config=agent_adls_config,
                    destination_type=destination_type,
                    details_for_sts_update=details_for_sts_update,
                )
            )
        else:
            logger.info(
                "[SAP_ODATA_PROXY] Triggering start-data-load RPC for entity '%s' via agent '%s' "
                "(load_method=%s).",
                src_details.entity_name, cust_id, load_method,
            )
            accepted = bool(
                startdataload(
                    cust_id=cust_id,
                    sources=source_payload_list,
                    source_config=source_conn,
                    destination_config=agent_adls_config,
                    destination_type=destination_type,
                    details_for_sts_update=details_for_sts_update,
                )
            )
        if not accepted:
            raise ValueError(f"Proxy Agent '{cust_id}' is offline or disconnected. Please ensure the agent process is running.")
        return accepted
    except Exception as err:
        raise _format_proxy_error(err, cust_id) from err


# ---------------------------------------------------------------------------
# Public proxy step runner
# ---------------------------------------------------------------------------

def run_proxy_load_step(
    *,
    source_config: Dict[str, Any],
    adls_config: Dict[str, Any],
    src_details: SAPSourceDetails,
    load_method: str,
    partition_data_by_date: bool,
    delta_token: Optional[str] = None,
    use_delta_rpc: bool = False,
    job_name: Optional[str] = None,
    migration_session_id: Optional[str] = None,
    connection_id: Optional[int] = None,
    cmp_id: Optional[int] = None,
    user_id: Optional[int] = None,
    log_id: Optional[int] = None,
) -> bool:
    """Execute a single load step through the proxy agent."""
    proxy_agent, _, _, _, _ = _get_proxy_credentials(source_config)
    return _run_proxy_data_load(
        cust_id=proxy_agent,
        source_config=source_config,
        adls_config=adls_config,
        src_details=src_details,
        load_method=load_method,
        use_delta_rpc=use_delta_rpc,
        delta_token=delta_token,
        partition_data_by_date=partition_data_by_date,
        job_name=job_name,
        migration_session_id=migration_session_id,
        connection_id=connection_id,
        cmp_id=cmp_id,
        user_id=user_id,
        log_id=log_id,
    )


# ---------------------------------------------------------------------------
# Public proxy batch runner (all tables in a single gRPC call)
# ---------------------------------------------------------------------------

def _normalize_agent_destination_config(
    destination_type: str,
    destination_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Shape destination credentials for the on-prem agent (ADLS key aliases only)."""
    dest_type = str(destination_type or "adls").strip().lower()
    agent_config = dict(destination_config or {})
    if dest_type == "adls":
        agent_config["TENANT_ID"] = agent_config.get("tenant_id", "")
        agent_config["CLIENT_ID"] = agent_config.get("client_id", "")
        agent_config["CLIENT_SECRET"] = agent_config.get("client_secret", "")
        agent_config["CONTAINER_NAME"] = agent_config.get("container_name", "")
        agent_config["STORAGE_ACCOUNT_NAME"] = (
            agent_config.get("account_name") or agent_config.get("storage_account_name", "")
        )
    return agent_config


def _get_proxy_snowflake_source_payload(
    *,
    service_name: str,
    entity_name: str,
    load_method: str,
    initial_completed_flag: bool,
    drop_and_reload: bool,
    pagination_count: int,
    filter_expr: Optional[str],
    delta_name: Optional[str],
    delta_token: Optional[str],
    is_updated: str,
    is_deleted: str,
    transform_state: Optional[Dict[str, Any]] = None,
) -> Any:
    from google.protobuf.struct_pb2 import Struct

    from sap_proxy_agent.generated import service_pb2

    transform_struct = Struct()
    if transform_state:
        transform_struct.update(transform_state)

    formatted_utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    kwargs: Dict[str, Any] = {
        "service_name": service_name,
        "entity_name": entity_name,
        "load_method": load_method,
        "initial_completed_flag": bool(initial_completed_flag),
        "drop_and_reload": bool(drop_and_reload),
        "is_updated": str(is_updated or "_FIVETRAN_SYNCED"),
        "is_deleted": str(is_deleted or "_FIVETRAN_DELETED"),
        "pagination_count": int(pagination_count or 30),
        "filter_expr": filter_expr or "",
        "utc_time": formatted_utc_time,
        "delta_name": delta_name or "",
        "delta_token": (
            extract_token_from_delta_link(delta_token) if delta_token else ""
        ),
    }
    if transform_state:
        kwargs["transform_state"] = transform_struct
    return service_pb2.SnowflakeDataSourceConfig(**kwargs)


def run_proxy_batch_load(
    *,
    source_config: Dict[str, Any],
    destination_config: Dict[str, Any],
    table_payloads: List[Dict[str, Any]],
    use_delta_rpc: bool = False,
    job_name: Optional[str] = None,
    migration_session_id: Optional[str] = None,
    connection_id: Optional[int] = None,
    cmp_id: Optional[int] = None,
    user_id: Optional[int] = None,
    log_id: Optional[int] = None,
    adls_config: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send a batch of tables to the proxy agent in a single gRPC call.

    Args:
        use_delta_rpc: ``True`` -> fire ``delta_load`` RPC (ADLS subsequent deltas).
                       ``False`` -> fire ``startdataload`` RPC (initial / Snowflake).
        adls_config: Deprecated alias for ``destination_config`` (ADLS call sites).
    """
    from sap_proxy_agent.client.dataynchor_client import delta_load, startdataload

    if adls_config is not None and not destination_config:
        destination_config = adls_config

    proxy_agent, _, _, _, _ = _get_proxy_credentials(source_config)
    source_conn = _get_proxy_src_conn(source_config)
    destination_type = str(
        source_config.get("destination_type")
        or destination_config.get("destination_type")
        or "adls"
    ).strip().lower()

    agent_destination_config = _normalize_agent_destination_config(
        destination_type,
        destination_config,
    )

    details_for_sts_update = {
        "migration_session_id": str(migration_session_id) if migration_session_id else "",
        "job_name": str(job_name) if job_name else "",
        "connection_id": str(connection_id) if connection_id else "",
        "cmp_id": str(cmp_id) if cmp_id else "",
        "user_id": str(user_id) if user_id else "",
        "log_id": str(log_id) if log_id else "",
    }

    snowflake_mode = destination_type == "snowflake"
    if snowflake_mode and use_delta_rpc:
        logger.warning(
            "[SAP_ODATA_PROXY] Snowflake proxy ignores TriggerDelta; using StartDataLoad."
        )
        use_delta_rpc = False

    if snowflake_mode:
        snowflake_sources = [
            _get_proxy_snowflake_source_payload(**p["snowflake_fields"])
            for p in table_payloads
        ]
        source_payload_list = snowflake_sources
    else:
        source_payload_list = [
            _get_proxy_source_payload(
                src_details=p["src_details"],
                load_method=p["load_method"],
                partition_data_by_date=bool(p.get("partition_data_by_date")),
                delta_token=p.get("delta_token"),
            )
            for p in table_payloads
        ]

    rpc_name = "delta_load" if use_delta_rpc else "startdataload"
    logger.info(
        "[SAP_ODATA_PROXY] Batch %s | agent=%s tables=%d dest=%s",
        rpc_name,
        proxy_agent,
        len(source_payload_list),
        destination_type,
    )

    try:
        if use_delta_rpc:
            accepted = bool(
                delta_load(
                    cust_id=proxy_agent,
                    sources=source_payload_list,
                    source_config=source_conn,
                    destination_config=agent_destination_config,
                    destination_type=destination_type,
                    details_for_sts_update=details_for_sts_update,
                )
            )
        elif snowflake_mode:
            accepted = bool(
                startdataload(
                    cust_id=proxy_agent,
                    sources=None,
                    source_config=source_conn,
                    destination_config=agent_destination_config,
                    destination_type=destination_type,
                    details_for_sts_update=details_for_sts_update,
                    snowflake_sources=source_payload_list,
                    src_type="sap-odata",
                )
            )
        else:
            accepted = bool(
                startdataload(
                    cust_id=proxy_agent,
                    sources=source_payload_list,
                    source_config=source_conn,
                    destination_config=agent_destination_config,
                    destination_type=destination_type,
                    details_for_sts_update=details_for_sts_update,
                )
            )
        if not accepted:
            raise ValueError(
                f"Proxy Agent '{proxy_agent}' is offline or disconnected. "
                "Please ensure the agent process is running."
            )
        return accepted
    except Exception as err:
        raise _format_proxy_error(err, proxy_agent) from err




