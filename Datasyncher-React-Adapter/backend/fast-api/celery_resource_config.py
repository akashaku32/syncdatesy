"""
RFC-001 resource resolution: plan_resource_config + company_resource_override → execution plan.

Precedence: company_resource_override → plan_resource_config → YAML/env defaults.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

PLAN_RESOURCE_CACHE_PREFIX = os.getenv("PLAN_RESOURCE_CACHE_PREFIX", "plan:resource:")
PLAN_RESOURCE_CACHE_TTL = int(os.getenv("PLAN_RESOURCE_CACHE_TTL_SECONDS", "300"))
JOB_EXECUTION_CONFIG_PREFIX = os.getenv("JOB_EXECUTION_CONFIG_PREFIX", "celery:job-execution-config:")
MEMORY_PRESSURE_THRESHOLD = float(os.getenv("CELERY_MEMORY_PRESSURE_RATIO", "0.70"))
DEFAULT_EXPANSION_FACTOR = float(os.getenv("CELERY_DEFAULT_EXPANSION_FACTOR", "3.0"))
DEFAULT_AVG_ROW_BYTES = int(os.getenv("CELERY_DEFAULT_AVG_ROW_BYTES", "2048"))
CONNECTOR_MEMORY_ESTIMATE_BYTES = {
    "salesforce": 8 * 1024 ** 3,
    "salesforce_to_databricks": 8 * 1024 ** 3,
    "salesforce_sandbox_to_databricks": 8 * 1024 ** 3,
    "microsoft_dynamics": 4 * 1024 ** 3,
    "microsoft_dynamics_databricks": 4 * 1024 ** 3,
    "dynamics": 4 * 1024 ** 3,
    "mysql": 2 * 1024 ** 3,
    "postgres": 2 * 1024 ** 3,
    "unknown": 2 * 1024 ** 3,
}

# Flow (connector_type) profiles: workload want under plan hard ceiling.
# preferred_concurrency is soft; memory drives safe concurrency.
FLOW_RESOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "salesforce": {
        "memory_estimate_bytes": 8 * 1024 ** 3,
        "preferred_concurrency": 2,
        "preferred_max_pods": 4,
    },
    "salesforce_to_snowflake": {
        "memory_estimate_bytes": 8 * 1024 ** 3,
        "preferred_concurrency": 2,
        "preferred_max_pods": 4,
    },
    "salesforce_sandbox_to_snowflake": {
        "memory_estimate_bytes": 8 * 1024 ** 3,
        "preferred_concurrency": 2,
        "preferred_max_pods": 4,
    },
    "salesforce_to_databricks": {
        "memory_estimate_bytes": 8 * 1024 ** 3,
        "preferred_concurrency": 2,
        "preferred_max_pods": 4,
    },
    "salesforce_sandbox_to_databricks": {
        "memory_estimate_bytes": 8 * 1024 ** 3,
        "preferred_concurrency": 2,
        "preferred_max_pods": 4,
    },
    "microsoft_dynamics": {
        "memory_estimate_bytes": 4 * 1024 ** 3,
        "preferred_concurrency": 3,
        "preferred_max_pods": 4,
    },
    "microsoft_dynamics_databricks": {
        "memory_estimate_bytes": 4 * 1024 ** 3,
        "preferred_concurrency": 3,
        "preferred_max_pods": 4,
    },
    "dynamics": {
        "memory_estimate_bytes": 4 * 1024 ** 3,
        "preferred_concurrency": 3,
        "preferred_max_pods": 4,
    },
    "amazon_s3": {
        "memory_estimate_bytes": 2 * 1024 ** 3,
        "preferred_concurrency": 3,
        "preferred_max_pods": 2,
    },
    "sftp_to_snowflake": {
        "memory_estimate_bytes": 2 * 1024 ** 3,
        "preferred_concurrency": 3,
        "preferred_max_pods": 2,
    },
    "google_reviews": {
        "memory_estimate_bytes": 2 * 1024 ** 3,
        "preferred_concurrency": 3,
        "preferred_max_pods": 2,
    },
    "unknown": {
        "memory_estimate_bytes": 2 * 1024 ** 3,
        "preferred_concurrency": 3,
        "preferred_max_pods": 2,
    },
}

_YAML_PLAN_DEFAULTS: dict[str, dict[str, Any]] = {
    "free": {
        "max_pods": 2, "concurrency": 3, "max_chunk_size": 50000,
        "chunk_size_pct": 25.0, "max_concurrency": 8,
        "cpu_request": "1500m", "cpu_limit": "2", "memory_request": "4Gi", "memory_limit": "6Gi",
        "max_inflight_tables": 6, "max_concurrent_jobs": 1, "priority": 10,
    },
    "standard": {
        "max_pods": 2, "concurrency": 3, "max_chunk_size": 50000,
        "chunk_size_pct": 25.0, "max_concurrency": 10,
        "cpu_request": "1500m", "cpu_limit": "2", "memory_request": "4Gi", "memory_limit": "6Gi",
        "max_inflight_tables": 6, "max_concurrent_jobs": 2, "priority": 50,
    },
    "discounted": {
        "max_pods": 2, "concurrency": 3, "max_chunk_size": 50000,
        "chunk_size_pct": 25.0, "max_concurrency": 10,
        "cpu_request": "1500m", "cpu_limit": "2", "memory_request": "4Gi", "memory_limit": "6Gi",
        "max_inflight_tables": 6, "max_concurrent_jobs": 1, "priority": 40,
    },
    "premium": {
        "max_pods": 4, "concurrency": 4, "max_chunk_size": 100000,
        "chunk_size_pct": 50.0, "max_concurrency": 16,
        "cpu_request": "3", "cpu_limit": "4", "memory_request": "10Gi", "memory_limit": "12Gi",
        "max_inflight_tables": 16, "max_concurrent_jobs": 2, "priority": 70,
    },
    # e2-standard-8 / 24Gi; 6 pods × concurrency 6; 175k chunk (Snowflake IN-list safety).
    "enterprise": {
        "max_pods": 6, "concurrency": 6, "max_chunk_size": 175000,
        "chunk_size_pct": 100.0, "max_concurrency": 24,
        "cpu_request": "7", "cpu_limit": "8", "memory_request": "22Gi", "memory_limit": "24Gi",
        "max_inflight_tables": 36, "max_concurrent_jobs": 3, "priority": 90,
    },
}

# Snowflake IN (...) max is 200_000 expressions; stay under until soak confirms 200k.
SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX = int(os.getenv("SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX", "175000"))

DYNAMIC_OOM_THRESHOLD = 0.70
DYNAMIC_EXPANSION_FACTOR = 3.5
DYNAMIC_AVG_ROW_BYTES = 1024
DYNAMIC_MIN_CHUNK_FLOOR = 100

# Single-table reload / refresh: one child owns the pod — scale chunk by concurrency
# (capped by memory and connector-specific ceilings). Multi-table jobs keep base chunk.
SINGLE_TABLE_CHUNK_BOOST_ENABLED = os.getenv(
    "CELERY_SINGLE_TABLE_CHUNK_BOOST_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
DATABRICKS_SINGLE_TABLE_CHUNK_MAX = int(
    os.getenv("DATABRICKS_SINGLE_TABLE_CHUNK_MAX", "400000")
)
# Dynamics CSV read + buffer concat can briefly hold ~2x chunk rows in memory.
SINGLE_TABLE_CHUNK_BUFFER_FACTOR = float(
    os.getenv("CELERY_SINGLE_TABLE_CHUNK_BUFFER_FACTOR", "2.0")
)
# When false (default), single-table chunk sizing uses the full pod memory budget.
# Set true to also clamp to Celery worker_max_memory_per_child (~1.2Gi default).
SINGLE_TABLE_RESPECT_CHILD_MEMORY_CAP = os.getenv(
    "CELERY_SINGLE_TABLE_RESPECT_CHILD_MEMORY_CAP", "false"
).lower() in ("1", "true", "yes", "on")


class PlanNodeMismatchError(Exception):
    pass


@dataclass
class NodePoolConfig:
    pool_name: str
    machine_type: str
    allocatable_memory: str
    allocatable_cpu: str
    overhead_memory: str = "2Gi"
    overhead_cpu: str = "200m"
    max_pods_per_node: int = 1
    node_selector: dict = field(default_factory=dict)
    taint_key: Optional[str] = None
    taint_value: Optional[str] = None
    taint_effect: str = "NoSchedule"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "NodePoolConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in known})


@dataclass
class EffectiveResourceConfig:
    plan_name: str
    max_pods: int
    concurrency: int
    effective_concurrency: int
    max_chunk_size: int
    max_inflight_tables: int
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    memory_limit_bytes: int
    priority: int
    max_concurrent_jobs: int = 1
    expansion_factor: float = DEFAULT_EXPANSION_FACTOR
    chunk_size_pct: Optional[float] = None
    max_concurrency: Optional[int] = None
    extra_config: Optional[dict] = None
    source: str = "yaml_fallback"
    company_id: Optional[int] = None
    worker_pool_name: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConnectorLimits:
    chunk_count: int
    src_min_count: Optional[int] = None
    src_max_count: Optional[int] = None
    dst_min_count: Optional[int] = None
    dst_max_count: Optional[int] = None
    src_is_rtl: bool = False


@dataclass
class DynamicExecutionConfig:
    chunk_size: int
    concurrency: int
    replicas: int
    connector_ceiling: Optional[int]
    plan_natural_chunk: int


@dataclass
class ExecutionPlan:
    table_count: int
    required_pods: int
    effective_pods: int
    effective_concurrency: int
    total_parallel_tables: int
    max_inflight_children: int
    config: EffectiveResourceConfig


def normalize_plan_name(plan: Optional[str]) -> str:
    return (plan or "free").strip().lower()


def parse_memory_gb(memory_str: Optional[str]) -> float:
    """Parse K8s memory string to gigabytes (Gi/G/Mi suffixes)."""
    if not memory_str:
        return 8.0
    s = str(memory_str).strip()
    try:
        if s.endswith("Gi"):
            return float(s[:-2])
        if s.endswith("G"):
            return float(s[:-1])
        if s.endswith("Mi"):
            return float(s[:-2]) / 1024
    except ValueError:
        pass
    return 8.0


def _extra_config_get(extra: Any, key: str, default: float) -> float:
    if not extra:
        return default
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            return default
    if not isinstance(extra, dict):
        return default
    val = extra.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def parse_memory_quantity(value: str) -> int:
    """Parse K8s-style memory (8Gi, 512Mi) to bytes."""
    if not value:
        return 8 * 1024 ** 3
    raw = str(value).strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KkMmGgTt]?i?)?$", raw)
    if not match:
        return 8 * 1024 ** 3
    num = float(match.group(1))
    unit = (match.group(2) or "").lower()
    multipliers = {
        "": 1,
        "k": 10 ** 3,
        "ki": 1024,
        "m": 10 ** 6,
        "mi": 1024 ** 2,
        "g": 10 ** 9,
        "gi": 1024 ** 3,
        "t": 10 ** 12,
        "ti": 1024 ** 4,
    }
    return int(num * multipliers.get(unit, 1024 ** 3))


def _yaml_plan_defaults(plan_name: str) -> dict[str, Any]:
    key = normalize_plan_name(plan_name)
    row = dict(_YAML_PLAN_DEFAULTS.get(key, _YAML_PLAN_DEFAULTS["standard"]))
    row["plan_name"] = key
    return row


def _merge_override(plan_row: dict, override: Optional[dict]) -> dict:
    merged = dict(plan_row)
    if not override:
        return merged
    for key, val in override.items():
        if val is not None and (key in merged or key == "worker_pool_name"):
            merged[key] = val
    legacy_pod = override.get("legacy_pod_count")
    if legacy_pod is not None and override.get("max_pods") is None:
        merged["max_pods"] = max(int(merged.get("max_pods") or 1), int(legacy_pod))
    return merged


def _row_to_config(plan_name: str, row: dict, source: str, company_id: Optional[int] = None) -> EffectiveResourceConfig:
    mem_limit = str(row.get("memory_limit") or "8Gi")
    plan_conc = int(row.get("concurrency") or 3)
    mem_bytes = parse_memory_quantity(mem_limit)
    extra = row.get("extra_config") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    expansion = float(extra.get("expansion_factor") or DEFAULT_EXPANSION_FACTOR)
    estimate = CONNECTOR_MEMORY_ESTIMATE_BYTES.get("unknown", 2 * 1024 ** 3)
    safe = compute_safe_concurrency(mem_bytes, estimate, plan_conc)
    eff_conc = max(1, min(plan_conc, safe))
    pct = row.get("chunk_size_pct")
    max_conc = row.get("max_concurrency")
    return EffectiveResourceConfig(
        plan_name=normalize_plan_name(plan_name),
        max_pods=max(1, int(row.get("max_pods") or 1)),
        concurrency=plan_conc,
        effective_concurrency=eff_conc,
        max_chunk_size=max(1, int(row.get("max_chunk_size") or 25000)),
        max_inflight_tables=max(1, int(row.get("max_inflight_tables") or plan_conc)),
        cpu_request=str(row.get("cpu_request") or "2"),
        cpu_limit=str(row.get("cpu_limit") or "2"),
        memory_request=str(row.get("memory_request") or mem_limit),
        memory_limit=mem_limit,
        memory_limit_bytes=mem_bytes,
        priority=int(row.get("priority") or 50),
        max_concurrent_jobs=max(1, int(row.get("max_concurrent_jobs") or 1)),
        expansion_factor=expansion,
        chunk_size_pct=float(pct) if pct is not None else None,
        max_concurrency=int(max_conc) if max_conc is not None else None,
        extra_config=extra if isinstance(extra, dict) else {},
        source=source,
        company_id=company_id,
        worker_pool_name=row.get("worker_pool_name"),
    )


def get_flow_resource_profile(connector_type: Optional[str]) -> dict[str, Any]:
    """Return flow profile for connector_type (workload want under plan ceiling)."""
    key = (connector_type or "unknown").strip().lower()
    if key in FLOW_RESOURCE_PROFILES:
        return dict(FLOW_RESOURCE_PROFILES[key])
    # Keep legacy memory map keys working.
    if key in CONNECTOR_MEMORY_ESTIMATE_BYTES:
        return {
            "memory_estimate_bytes": CONNECTOR_MEMORY_ESTIMATE_BYTES[key],
            "preferred_concurrency": FLOW_RESOURCE_PROFILES["unknown"]["preferred_concurrency"],
            "preferred_max_pods": FLOW_RESOURCE_PROFILES["unknown"]["preferred_max_pods"],
        }
    return dict(FLOW_RESOURCE_PROFILES["unknown"])


def compute_safe_concurrency(
    memory_limit_bytes: int,
    estimated_memory_per_table: int,
    plan_concurrency: int,
) -> int:
    if estimated_memory_per_table <= 0:
        return plan_concurrency
    budget = memory_limit_bytes * MEMORY_PRESSURE_THRESHOLD
    return max(1, int(budget // estimated_memory_per_table))


def partition_even_shard_sizes(item_count: int, shard_count: int) -> list[int]:
    """Return per-shard sizes that differ by at most 1 (first shards get the remainder)."""
    n = max(0, int(item_count))
    p = max(0, int(shard_count))
    if n == 0 or p == 0:
        return []
    p = min(p, n)  # never more shards than items
    base = n // p
    rem = n % p
    return [base + (1 if i < rem else 0) for i in range(p)]


def assign_even_shards(item_count: int, shard_count: int) -> list[int]:
    """
    Map item index -> shard id with contiguous blocks of even sizes.
    Shard sizes differ by at most 1.
    """
    sizes = partition_even_shard_sizes(item_count, shard_count)
    if not sizes:
        return []
    assignment: list[int] = []
    for shard_id, size in enumerate(sizes):
        assignment.extend([shard_id] * size)
    return assignment


def order_indices_round_robin(item_count: int, shard_count: int) -> list[int]:
    """
    Publish order that waves across shards (even start): 
    first item of each shard, then second of each shard, ...
    Uses contiguous even partition under the hood.
    """
    assignment = assign_even_shards(item_count, shard_count)
    if not assignment:
        return []
    p = max(assignment) + 1 if assignment else 0
    buckets: list[list[int]] = [[] for _ in range(p)]
    for idx, shard in enumerate(assignment):
        buckets[shard].append(idx)
    ordered: list[int] = []
    max_len = max((len(b) for b in buckets), default=0)
    for wave in range(max_len):
        for shard in range(p):
            if wave < len(buckets[shard]):
                ordered.append(buckets[shard][wave])
    return ordered


def compute_execution_plan(
    config: EffectiveResourceConfig,
    table_count: int,
    *,
    estimated_memory_per_table: Optional[int] = None,
    flow_preferred_max_pods: Optional[int] = None,
    pod_cap: Optional[int] = None,
) -> ExecutionPlan:
    tables = max(0, int(table_count))
    if tables == 0:
        return ExecutionPlan(0, 0, 0, config.effective_concurrency, 0, 0, config)

    est_mem = estimated_memory_per_table
    if est_mem is not None:
        safe = compute_safe_concurrency(config.memory_limit_bytes, est_mem, config.concurrency)
        eff_conc = max(1, min(config.concurrency, safe))
        config = replace(config, effective_concurrency=eff_conc)

    conc = max(1, int(config.effective_concurrency or config.concurrency or 1))
    required = math.ceil(tables / conc)
    # Hard ceiling: plan max_pods, optional connector preferred, optional pipeline budget slice.
    plan_cap = max(1, int(config.max_pods))
    preferred = int(flow_preferred_max_pods) if flow_preferred_max_pods else plan_cap
    preferred = max(1, preferred)
    request_cap = int(pod_cap) if pod_cap is not None else plan_cap
    if request_cap <= 0:
        request_cap = plan_cap
    hard_cap = max(1, min(plan_cap, preferred, request_cap))
    effective_pods = max(1, min(required, hard_cap, tables))
    total_parallel = effective_pods * conc
    # Full fleet utilization: pods * concurrency, clamped by tables and plan inflight.
    max_inflight = min(total_parallel, tables, config.max_inflight_tables)
    return ExecutionPlan(
        table_count=tables,
        required_pods=required,
        effective_pods=effective_pods,
        effective_concurrency=conc,
        total_parallel_tables=total_parallel,
        max_inflight_children=max(1, max_inflight),
        config=config,
    )


def _is_databricks_connector(connector_type: Optional[str]) -> bool:
    key = (connector_type or "").strip().lower()
    return "databricks" in key


def _worker_child_memory_budget_bytes() -> int:
    """Per prefork child RSS budget (Celery worker_max_memory_per_child)."""
    try:
        kb = int(os.getenv("CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB", "1200000"))
    except (TypeError, ValueError):
        kb = 1200000
    return max(kb, 1) * 1024


def _memory_safe_chunk_cap(
    plan_config: EffectiveResourceConfig,
    *,
    avg_row_bytes: Optional[int] = None,
    buffer_factor: float = SINGLE_TABLE_CHUNK_BUFFER_FACTOR,
    use_full_pod: bool = False,
) -> int:
    """
    Upper bound on chunk rows from memory.

    When ``use_full_pod`` is True (single-table job), budget the whole pod limit
    instead of the per-table 4Gi Dynamics estimate used for multi-table fan-out.
    Always respect the Celery prefork child memory ceiling.
    """
    row_bytes = max(1, int(avg_row_bytes or DYNAMIC_AVG_ROW_BYTES))
    extra = plan_config.extra_config or {}
    expansion = _extra_config_get(extra, "expansion_factor", DYNAMIC_EXPANSION_FACTOR)
    oom_threshold = _extra_config_get(extra, "oom_threshold", DYNAMIC_OOM_THRESHOLD)
    denom = row_bytes * expansion * max(1.0, float(buffer_factor or 1.0))
    pod_budget = int(plan_config.memory_limit_bytes * oom_threshold)
    if use_full_pod:
        budget = pod_budget
        if SINGLE_TABLE_RESPECT_CHILD_MEMORY_CAP:
            child_budget = int(_worker_child_memory_budget_bytes() * 0.85)
            if child_budget > 0:
                budget = min(budget, child_budget)
    else:
        budget = pod_budget
    if denom <= 0:
        return plan_config.max_chunk_size
    return max(DYNAMIC_MIN_CHUNK_FLOOR, int(budget // denom))


def apply_single_table_chunk_boost(
    base_chunk: int,
    plan_config: EffectiveResourceConfig,
    *,
    job_table_count: int,
    connector_type: Optional[str] = None,
    avg_row_bytes: Optional[int] = None,
    connector_ceiling: Optional[int] = None,
) -> int:
    """
    Multiply chunk size when the entire migration job has one table.

    Multi-table batches keep ``base_chunk`` so concurrent children on the same pod
    do not OOM. Single-table reload/refresh (e.g. dimensionfocusbalance-only pipeline)
    can use a larger chunk up to concurrency × plan chunk, memory, and connector caps.
    """
    base = max(DYNAMIC_MIN_CHUNK_FLOOR, int(base_chunk or plan_config.max_chunk_size))
    if not SINGLE_TABLE_CHUNK_BOOST_ENABLED or int(job_table_count or 0) != 1:
        return base

    multiplier = max(
        1,
        int(plan_config.effective_concurrency or plan_config.concurrency or 1),
    )
    boosted = base * multiplier

    if _is_databricks_connector(connector_type):
        # Connection dst_max/chunk (often 200k) is a multi-table safety default.
        # Single-table Databricks jobs may use the dedicated boost cap (400k).
        absolute_cap = max(DYNAMIC_MIN_CHUNK_FLOOR, DATABRICKS_SINGLE_TABLE_CHUNK_MAX)
        if connector_ceiling is not None and int(connector_ceiling) > 0:
            absolute_cap = max(absolute_cap, int(connector_ceiling))
    else:
        absolute_cap = (
            SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX
            if SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX > 0
            else boosted
        )
        if connector_ceiling is not None and int(connector_ceiling) > 0:
            absolute_cap = (
                min(absolute_cap, int(connector_ceiling))
                if absolute_cap > 0
                else int(connector_ceiling)
            )
    boosted = min(boosted, absolute_cap)

    memory_cap = _memory_safe_chunk_cap(
        plan_config,
        avg_row_bytes=avg_row_bytes,
        use_full_pod=True,
    )
    effective = max(base, min(boosted, memory_cap))

    if effective > base:
        row_bytes = max(1, int(avg_row_bytes or DYNAMIC_AVG_ROW_BYTES))
        extra = plan_config.extra_config or {}
        expansion = _extra_config_get(extra, "expansion_factor", DYNAMIC_EXPANSION_FACTOR)
        denom = row_bytes * expansion * max(1.0, float(SINGLE_TABLE_CHUNK_BUFFER_FACTOR))
        child_budget_bytes = int(_worker_child_memory_budget_bytes() * 0.85)
        child_row_cap = (
            max(DYNAMIC_MIN_CHUNK_FLOOR, int(child_budget_bytes // denom))
            if denom > 0
            else base
        )
        if (
            effective > child_row_cap
            and not SINGLE_TABLE_RESPECT_CHILD_MEMORY_CAP
        ):
            logger.warning(
                "CHUNK_SINGLE_TABLE_BOOST | base=%s | boosted=%s | multiplier=%s | "
                "memory_cap=%s | connector=%s | job_tables=1 | "
                "hint=raise CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB or set concurrency=1 "
                "on the job worker for wide tables",
                base,
                effective,
                multiplier,
                memory_cap,
                connector_type or "unknown",
            )
        else:
            logger.warning(
                "CHUNK_SINGLE_TABLE_BOOST | base=%s | boosted=%s | multiplier=%s | "
                "memory_cap=%s | connector=%s | job_tables=1",
                base,
                effective,
                multiplier,
                memory_cap,
                connector_type or "unknown",
            )
    return effective


def clamp_chunk_size(
    user_chunk: Optional[int],
    config: EffectiveResourceConfig,
    *,
    avg_row_bytes: Optional[int] = None,
    table_name: Optional[str] = None,
    connector: Optional[str] = None,
    single_table_job: bool = False,
) -> int:
    """RFC Layer 1 + Layer 2 chunk sizing."""
    requested = max(1, int(user_chunk or config.max_chunk_size))
    if single_table_job:
        effective = requested
    else:
        effective = min(requested, config.max_chunk_size)
    if not single_table_job and requested > effective:
        try:
            import celery_job_metrics as job_metrics
            job_metrics.inc_chunk_event(connector, "clamped")
        except Exception:
            pass
        logger.warning(
            "CHUNK_CLAMPED | table=%s | requested=%s | plan_max=%s | plan=%s",
            table_name or "unknown",
            requested,
            config.max_chunk_size,
            config.plan_name,
        )

    row_bytes = avg_row_bytes or DEFAULT_AVG_ROW_BYTES
    if row_bytes > 0:
        if single_table_job:
            memory_safe = _memory_safe_chunk_cap(
                config,
                avg_row_bytes=row_bytes,
                use_full_pod=True,
            )
        else:
            budget = config.memory_limit_bytes * MEMORY_PRESSURE_THRESHOLD
            denom = row_bytes * config.expansion_factor
            memory_safe = (
                max(1, int(budget // denom)) if denom > 0 else effective
            )
        if memory_safe < effective:
            try:
                import celery_job_metrics as job_metrics
                job_metrics.inc_chunk_event(connector, "memory_estimate")
            except Exception:
                pass
            logger.warning(
                "CHUNK_REDUCED | table=%s | from=%s | to=%s | reason=memory_estimate",
                table_name or "unknown",
                effective,
                memory_safe,
            )
            effective = memory_safe
    return max(1, effective)


def _load_plan_row_from_db(db, plan_name: str) -> Optional[dict]:
    try:
        row = db.execute(
            text("""
                SELECT plan_name, max_pods, cpu_request, cpu_limit,
                       memory_request, memory_limit, concurrency, max_chunk_size,
                       chunk_size_pct, max_concurrency,
                       max_concurrent_jobs, max_inflight_tables, priority, extra_config,
                       worker_pool_name
                FROM plan_resource_config
                WHERE plan_name = :plan
            """),
            {"plan": normalize_plan_name(plan_name)},
        ).mappings().first()
        return dict(row) if row else None
    except Exception as exc:
        logger.debug("PLAN_RESOURCE_DB_MISS | plan=%s | err=%s", plan_name, exc)
        return None


def load_connector_limits(db, connection_id) -> ConnectorLimits:
    """Load per-connection chunk count and source/destination model limits."""
    fallback = ConnectorLimits(chunk_count=10000)
    if not connection_id:
        return fallback
    try:
        row = db.execute(
            text("""
                SELECT cn.chunk_count,
                       sm.min_count AS src_min_count,
                       sm.max_count AS src_max_count,
                       sm.is_rtl AS src_is_rtl,
                       dm.min_count AS dst_min_count,
                       dm.max_count AS dst_max_count
                FROM connection cn
                JOIN source_config sc ON sc.src_config_id = cn.src_config_id
                JOIN source_model sm ON sm.src_id = sc.src_id
                JOIN destination_config dc ON dc.dst_config_id = cn.dst_config_id
                JOIN destination_model dm ON dm.dst_id = dc.dst_id
                WHERE cn.connection_id = :cid
                LIMIT 1
            """),
            {"cid": connection_id},
        ).mappings().first()
        if not row:
            return fallback
        return ConnectorLimits(
            chunk_count=int(row.get("chunk_count") or 10000),
            src_min_count=row.get("src_min_count"),
            src_max_count=row.get("src_max_count"),
            dst_min_count=row.get("dst_min_count"),
            dst_max_count=row.get("dst_max_count"),
            src_is_rtl=bool(row.get("src_is_rtl")),
        )
    except Exception as exc:
        logger.debug("CONNECTOR_LIMITS_DB_MISS | connection_id=%s | err=%s", connection_id, exc)
        return fallback


def resolve_chunk_size(
    connection: ConnectorLimits,
    plan_config: EffectiveResourceConfig,
    override: Optional[dict] = None,
    *,
    pod_memory_limit_gb: Optional[float] = None,
) -> tuple[int, Optional[int], int]:
    """Returns (effective_chunk, connector_ceiling, plan_natural_chunk)."""
    is_reverse_etl = bool(getattr(connection, "src_is_rtl", False))
    if is_reverse_etl:
        connector_ceiling = connection.dst_max_count
    else:
        connector_ceiling = connection.src_max_count
    if connector_ceiling is None and connection.chunk_count:
        connector_ceiling = int(connection.chunk_count)

    max_chunk = max(1, int(plan_config.max_chunk_size))
    # Defense-in-depth: never exceed Snowflake-safe IN-list chunk even if DB mis-set.
    if SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX > 0:
        max_chunk = min(max_chunk, SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX)
    if connector_ceiling is not None and plan_config.chunk_size_pct is not None:
        if not is_reverse_etl and max_chunk <= 50_000:
            # Free / standard / discounted: use plan cap up to connector ceiling (default 50k).
            plan_natural_chunk = min(max_chunk, connector_ceiling)
            path = "plan_cap"
        else:
            plan_natural_chunk = min(
                max_chunk,
                math.floor(
                    connector_ceiling * float(plan_config.chunk_size_pct) / 100
                ),
            )
            path = "percentage"
    else:
        plan_natural_chunk = max_chunk
        path = "absolute_fallback"

    if override and override.get("max_chunk_size"):
        plan_natural_chunk = min(plan_natural_chunk, int(override["max_chunk_size"]))

    if SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX > 0:
        plan_natural_chunk = min(plan_natural_chunk, SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX)

    effective = min(connection.chunk_count, plan_natural_chunk)

    min_floor = DYNAMIC_MIN_CHUNK_FLOOR
    effective = max(effective, min_floor)

    if pod_memory_limit_gb is None:
        pod_memory_limit_gb = parse_memory_gb(plan_config.memory_limit)

    extra = plan_config.extra_config or {}
    oom_threshold = _extra_config_get(extra, "oom_threshold", DYNAMIC_OOM_THRESHOLD)
    expansion_factor = _extra_config_get(extra, "expansion_factor", DYNAMIC_EXPANSION_FACTOR)

    estimated_gb = (
        DYNAMIC_AVG_ROW_BYTES * effective * expansion_factor
    ) / (1024 ** 3)
    if estimated_gb > pod_memory_limit_gb * oom_threshold:
        safe = math.floor(
            (pod_memory_limit_gb * oom_threshold * (1024 ** 3))
            / (DYNAMIC_AVG_ROW_BYTES * expansion_factor)
        )
        if safe < effective:
            logger.warning(
                "CHUNK_SIZE_REDUCED | "
                f"original={effective} | "
                f"reduced_to={safe} | "
                f"estimated_gb={estimated_gb:.2f} | "
                f"pod_memory_gb={pod_memory_limit_gb}"
            )
            effective = max(safe, min_floor)

    logger.info(
        "CHUNK_RESOLVED | "
        f"path={path} | "
        f"connector_ceiling={connector_ceiling} | "
        f"plan_pct={plan_config.chunk_size_pct} | "
        f"plan_natural_chunk={plan_natural_chunk} | "
        f"connection_chunk={connection.chunk_count} | "
        f"effective={effective}"
    )
    return effective, connector_ceiling, plan_natural_chunk


def resolve_effective_concurrency(
    plan_config: EffectiveResourceConfig,
    effective_chunk: int,
    plan_natural_chunk: int,
    *,
    pod_memory_limit_gb: Optional[float] = None,
    override: Optional[dict] = None,
) -> int:
    base = plan_config.concurrency
    if plan_natural_chunk and plan_natural_chunk > 0 and effective_chunk < plan_natural_chunk:
        scaled = math.floor(base * (plan_natural_chunk / effective_chunk))
    else:
        scaled = base

    cap = plan_config.max_concurrency or base
    if override and override.get("concurrency"):
        cap = min(cap, int(override["concurrency"]))
    effective = min(scaled, cap)

    if pod_memory_limit_gb is None:
        pod_memory_limit_gb = parse_memory_gb(plan_config.memory_limit)

    if pod_memory_limit_gb:
        extra = plan_config.extra_config or {}
        oom_threshold = _extra_config_get(extra, "oom_threshold", DYNAMIC_OOM_THRESHOLD)
        expansion_factor = _extra_config_get(extra, "expansion_factor", DYNAMIC_EXPANSION_FACTOR)
        task_memory_gb = (
            DYNAMIC_AVG_ROW_BYTES * effective_chunk * expansion_factor
        ) / (1024 ** 3)
        if task_memory_gb > 0:
            memory_safe = math.floor(
                (pod_memory_limit_gb * oom_threshold) / task_memory_gb
            )
            effective = min(effective, max(memory_safe, 1))

    effective = max(effective, 1)

    logger.info(
        "CONCURRENCY_RESOLVED | "
        f"base={base} | "
        f"plan_natural_chunk={plan_natural_chunk} | "
        f"effective_chunk={effective_chunk} | "
        f"scaled={scaled} | "
        f"cap={cap} | "
        f"effective={effective}"
    )
    return effective


def resolve_effective_pods(
    plan_config: EffectiveResourceConfig,
    effective_concurrency: int,
    override: Optional[dict] = None,
) -> int:
    max_inflight = plan_config.max_inflight_tables
    if max_inflight is None or max_inflight <= 0:
        max_inflight = plan_config.max_pods * plan_config.concurrency

    raw_pods = math.ceil(max_inflight / effective_concurrency)
    plan_cap = plan_config.max_pods
    if override and override.get("max_pods"):
        plan_cap = int(override["max_pods"])
    effective = max(1, min(raw_pods, plan_cap))

    logger.info(
        "PODS_RESOLVED | "
        f"max_inflight={max_inflight} | "
        f"effective_concurrency={effective_concurrency} | "
        f"raw_pods={raw_pods} | "
        f"plan_cap={plan_cap} | "
        f"effective_pods={effective}"
    )
    return effective


def resolve_dynamic_execution_plan(
    connection: ConnectorLimits,
    plan_config: EffectiveResourceConfig,
    override: Optional[dict] = None,
) -> DynamicExecutionConfig:
    """Full dynamic chunk → concurrency → pods resolution chain."""
    pod_memory_limit_gb = parse_memory_gb(plan_config.memory_limit)

    effective_chunk, connector_ceiling, plan_natural_chunk = resolve_chunk_size(
        connection,
        plan_config,
        override,
        pod_memory_limit_gb=pod_memory_limit_gb,
    )
    concurrency_natural = plan_config.max_chunk_size
    if override and override.get("max_chunk_size"):
        concurrency_natural = min(concurrency_natural, int(override["max_chunk_size"]))
    effective_concurrency = resolve_effective_concurrency(
        plan_config=plan_config,
        effective_chunk=effective_chunk,
        plan_natural_chunk=concurrency_natural,
        pod_memory_limit_gb=pod_memory_limit_gb,
        override=override,
    )
    effective_pods = resolve_effective_pods(
        plan_config=plan_config,
        effective_concurrency=effective_concurrency,
        override=override,
    )
    return DynamicExecutionConfig(
        chunk_size=effective_chunk,
        concurrency=effective_concurrency,
        replicas=effective_pods,
        connector_ceiling=connector_ceiling,
        plan_natural_chunk=plan_natural_chunk,
    )


def _load_node_pool_from_db(db, pool_name: str) -> Optional[NodePoolConfig]:
    try:
        row = db.execute(
            text("""
                SELECT pool_name, machine_type, allocatable_memory, allocatable_cpu,
                       overhead_memory, overhead_cpu, max_pods_per_node,
                       node_selector, taint_key, taint_value, taint_effect
                FROM worker_node_pool_config
                WHERE pool_name = :pool_name
            """),
            {"pool_name": pool_name},
        ).mappings().first()
        if not row:
            return None
        node_selector = row.get("node_selector") or {}
        if isinstance(node_selector, str):
            try:
                node_selector = json.loads(node_selector)
            except Exception:
                node_selector = {}
        return NodePoolConfig(
            pool_name=row["pool_name"],
            machine_type=row["machine_type"],
            allocatable_memory=row["allocatable_memory"],
            allocatable_cpu=row["allocatable_cpu"],
            overhead_memory=row.get("overhead_memory") or "2Gi",
            overhead_cpu=row.get("overhead_cpu") or "200m",
            max_pods_per_node=int(row.get("max_pods_per_node") or 1),
            node_selector=node_selector if isinstance(node_selector, dict) else {},
            taint_key=row.get("taint_key"),
            taint_value=row.get("taint_value"),
            taint_effect=row.get("taint_effect") or "NoSchedule",
        )
    except Exception as exc:
        raise exc


def get_node_pool_config(
    plan_config: EffectiveResourceConfig,
    override: Optional[dict] = None,
    db=None,
) -> Optional[NodePoolConfig]:
    pool_name = None

    if override and override.get("worker_pool_name"):
        pool_name = override["worker_pool_name"]
    elif plan_config.worker_pool_name:
        pool_name = plan_config.worker_pool_name

    if not pool_name:
        return None

    try:
        if db is not None:
            return _load_node_pool_from_db(db, pool_name)
        return None
    except Exception as exc:
        logger.warning(
            "NODE_POOL_CONFIG_FETCH_FAILED | pool_name=%s | error=%s",
            pool_name,
            exc,
        )
        return None


def validate_plan_fits_pool(
    plan_config: EffectiveResourceConfig,
    node_pool: Optional[NodePoolConfig],
) -> None:
    if not node_pool:
        return

    requested_gb = parse_memory_gb(plan_config.memory_request)
    allocatable_gb = parse_memory_gb(node_pool.allocatable_memory)
    overhead_gb = parse_memory_gb(node_pool.overhead_memory)
    usable_gb = allocatable_gb - overhead_gb

    if requested_gb > usable_gb:
        logger.error(
            "PLAN_NODE_MISMATCH | plan=%s | memory_request=%s | pool=%s | "
            "machine_type=%s | allocatable=%s | usable=%.1fGi | pod_will_stay_pending=True",
            plan_config.plan_name,
            plan_config.memory_request,
            node_pool.pool_name,
            node_pool.machine_type,
            node_pool.allocatable_memory,
            usable_gb,
        )
        raise PlanNodeMismatchError(
            f"Plan {plan_config.plan_name} requests "
            f"{plan_config.memory_request} but pool "
            f"{node_pool.pool_name} ({node_pool.machine_type}) "
            f"only has {usable_gb:.1f}Gi usable. "
            f"Update worker_node_pool_config or upgrade "
            f"the GKE node pool machine type."
        )

    logger.info(
        "NODE_FIT_VALIDATED | plan=%s | memory_request=%s | pool=%s | "
        "usable=%.1fGi | headroom=%.1fGi",
        plan_config.plan_name,
        plan_config.memory_request,
        node_pool.pool_name,
        usable_gb,
        usable_gb - requested_gb,
    )


def resolve_full_execution_config(
    connection: ConnectorLimits,
    plan_config: EffectiveResourceConfig,
    override: Optional[dict] = None,
    db=None,
) -> dict:
    """Full dynamic chain; returns spec dict for orchestration and monitoring."""
    dynamic = resolve_dynamic_execution_plan(connection, plan_config, override)
    node_pool = get_node_pool_config(plan_config, override, db)
    validate_plan_fits_pool(plan_config, node_pool)
    return {
        "chunk_size": dynamic.chunk_size,
        "concurrency": dynamic.concurrency,
        "replicas": dynamic.replicas,
        "connector_ceiling": dynamic.connector_ceiling,
        "plan_natural_chunk": dynamic.plan_natural_chunk,
        "node_pool": node_pool,
        "cpu_request": plan_config.cpu_request,
        "cpu_limit": plan_config.cpu_limit,
        "memory_request": plan_config.memory_request,
        "memory_limit": plan_config.memory_limit,
    }


def _load_company_context(db, connection_id) -> Optional[dict]:
    try:
        row = db.execute(
            text("""
                SELECT c.cmp_id, c.plan, c.pod_count,
                       cro.max_pods AS override_max_pods,
                       cro.cpu_request AS override_cpu_request,
                       cro.cpu_limit AS override_cpu_limit,
                       cro.memory_request AS override_memory_request,
                       cro.memory_limit AS override_memory_limit,
                       cro.concurrency AS override_concurrency,
                       cro.max_chunk_size AS override_max_chunk_size,
                       cro.max_concurrent_jobs AS override_max_concurrent_jobs,
                       cro.priority AS override_priority,
                       cro.extra_config AS override_extra_config,
                       cro.worker_pool_name AS override_worker_pool_name
                FROM connection cn
                JOIN company c ON c.cmp_id = cn.cmp_id
                LEFT JOIN company_resource_override cro ON cro.company_id = c.cmp_id
                WHERE cn.connection_id = :cid
                LIMIT 1
            """),
            {"cid": connection_id},
        ).mappings().first()
        return dict(row) if row else None
    except Exception as exc:
        logger.debug("COMPANY_RESOURCE_DB_MISS | connection_id=%s | err=%s", connection_id, exc)
        return None


def _override_dict_from_company_row(row: dict) -> dict:
    mapping = {
        "max_pods": row.get("override_max_pods"),
        "cpu_request": row.get("override_cpu_request"),
        "cpu_limit": row.get("override_cpu_limit"),
        "memory_request": row.get("override_memory_request"),
        "memory_limit": row.get("override_memory_limit"),
        "concurrency": row.get("override_concurrency"),
        "max_chunk_size": row.get("override_max_chunk_size"),
        "max_concurrent_jobs": row.get("override_max_concurrent_jobs"),
        "priority": row.get("override_priority"),
        "extra_config": row.get("override_extra_config"),
        "legacy_pod_count": row.get("pod_count"),
        "worker_pool_name": row.get("override_worker_pool_name"),
    }
    return {k: v for k, v in mapping.items() if v is not None}


def resolve_effective_resource_config(
    db,
    connection_id,
    *,
    plan_hint: Optional[str] = None,
    r=None,
    connector_type: Optional[str] = None,
) -> EffectiveResourceConfig:
    """Load merged plan + company override with Redis cache and YAML fallback."""
    company_row = _load_company_context(db, connection_id) if connection_id else None
    plan_name = normalize_plan_name(
        plan_hint or (company_row or {}).get("plan") or "free"
    )
    company_id = (company_row or {}).get("cmp_id")

    cache_key = f"{PLAN_RESOURCE_CACHE_PREFIX}{plan_name}"
    plan_row = None
    source = "yaml_fallback"

    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                plan_row = json.loads(cached)
                source = "redis_cache"
        except Exception:
            pass

    if plan_row is None:
        db_row = _load_plan_row_from_db(db, plan_name)
        if db_row:
            plan_row = db_row
            source = "db"
            if r is not None:
                try:
                    r.set(cache_key, json.dumps(db_row), ex=PLAN_RESOURCE_CACHE_TTL)
                except Exception:
                    pass

    if plan_row is None:
        plan_row = _yaml_plan_defaults(plan_name)
        source = "yaml_fallback"

    override = _override_dict_from_company_row(company_row) if company_row else {}
    merged = _merge_override(plan_row, override)

    flow = get_flow_resource_profile(connector_type)
    est_mem = int(
        flow.get("memory_estimate_bytes")
        or CONNECTOR_MEMORY_ESTIMATE_BYTES.get(
            (connector_type or "unknown").strip().lower(),
            CONNECTOR_MEMORY_ESTIMATE_BYTES["unknown"],
        )
    )
    config = _row_to_config(plan_name, merged, source, company_id=company_id)

    # Flow preferred concurrency is a soft want under plan concurrency / memory safety.
    preferred_conc = int(flow.get("preferred_concurrency") or config.concurrency)
    plan_conc = max(1, int(config.concurrency))
    want_conc = max(1, min(plan_conc, preferred_conc))
    safe = compute_safe_concurrency(config.memory_limit_bytes, est_mem, want_conc)
    config.effective_concurrency = max(1, min(want_conc, safe))
    if config.effective_concurrency < config.concurrency:
        try:
            import celery_job_metrics as job_metrics
            job_metrics.inc_concurrency_reduced(config.plan_name)
        except Exception:
            pass
        logger.warning(
            "CONCURRENCY_REDUCED | plan=%s | connector=%s | plan_concurrency=%s | "
            "flow_preferred=%s | effective=%s | reason=flow_or_memory",
            config.plan_name,
            connector_type or "unknown",
            config.concurrency,
            preferred_conc,
            config.effective_concurrency,
        )

    # Stash flow preferred pods on config via extra_config for execution plan.
    extra = dict(config.extra_config or {})
    extra["flow_connector_type"] = (connector_type or "unknown").strip().lower()
    extra["flow_preferred_max_pods"] = int(flow.get("preferred_max_pods") or config.max_pods)
    config.extra_config = extra

    logger.info(
        "RESOURCE_CONFIG_RESOLVED | plan=%s | source=%s | max_pods=%s | concurrency=%s | "
        "effective_concurrency=%s | max_chunk=%s | company_id=%s | connector=%s | "
        "flow_preferred_max_pods=%s",
        config.plan_name,
        config.source,
        config.max_pods,
        config.concurrency,
        config.effective_concurrency,
        config.max_chunk_size,
        config.company_id,
        connector_type or "unknown",
        extra.get("flow_preferred_max_pods"),
    )
    return config


def store_job_execution_config(
    r,
    job_name: str,
    plan: ExecutionPlan,
    *,
    shard_plan: Optional[dict] = None,
    connector_ceiling: Optional[int] = None,
) -> None:
    if r is None:
        return
    key = f"{JOB_EXECUTION_CONFIG_PREFIX}{job_name}"
    payload = {
        "execution": {
            "effective_pods": plan.effective_pods,
            "max_inflight_children": plan.max_inflight_children,
            "total_parallel_tables": plan.total_parallel_tables,
            "stable_fleet": True,
        },
        "config": plan.config.to_dict(),
        "connector_ceiling": connector_ceiling,
    }
    if shard_plan is not None:
        payload["shard_plan"] = shard_plan
    try:
        r.set(key, json.dumps(payload), ex=86400)
    except Exception as exc:
        logger.warning("JOB_EXECUTION_CONFIG_STORE_FAILED | job=%s | err=%s", job_name, exc)


def load_job_execution_config(r, job_name: str) -> Optional[dict]:
    if r is None:
        return None
    try:
        raw = r.get(f"{JOB_EXECUTION_CONFIG_PREFIX}{job_name}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def config_from_dict(payload: dict) -> EffectiveResourceConfig:
    known = {f.name for f in fields(EffectiveResourceConfig)}
    return EffectiveResourceConfig(**{k: v for k, v in payload.items() if k in known})


def delete_job_execution_config(r, job_name: str) -> None:
    if r is None:
        return
    try:
        r.delete(f"{JOB_EXECUTION_CONFIG_PREFIX}{job_name}")
    except Exception:
        pass


def memory_pressure_should_defer_refill(ratio: Optional[float]) -> bool:
    """RFC Layer 5: defer fan-out refill when RSS > 75% pod limit."""
    return ratio is not None and ratio > 0.75


def memory_pressure_chunk_multiplier(ratio: Optional[float]) -> float:
    """RFC Layer 5: reduce next chunk 25% when RSS > 75%."""
    if ratio is not None and ratio > 0.75:
        return 0.75
    return 1.0


def check_runtime_memory_pressure() -> Optional[float]:
    """RFC Layer 5: return RSS ratio vs cgroup limit if measurable."""
    try:
        import psutil
        proc = psutil.Process()
        rss = proc.memory_info().rss
        limit = parse_memory_quantity(os.getenv("CELERY_POD_MEMORY_LIMIT", ""))
        cgroup_limit = _cgroup_memory_limit_bytes()
        if cgroup_limit:
            limit = cgroup_limit
        if limit <= 0:
            return None
        ratio = rss / limit
        if ratio > 0.85:
            logger.warning("MEMORY_PRESSURE_CRITICAL | rss_ratio=%.2f", ratio)
        elif ratio > 0.75:
            logger.warning("MEMORY_PRESSURE_HIGH | rss_ratio=%.2f", ratio)
        elif ratio > 0.60:
            logger.warning("MEMORY_PRESSURE_WARN | rss_ratio=%.2f", ratio)
        return ratio
    except Exception:
        return None


def _cgroup_memory_limit_bytes() -> int:
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read().strip()
                if raw and raw != "max":
                    return int(raw)
        except OSError:
            continue
    return 0
