"""
Shared chunk-limit resolution for Django (connection create, API validation).

Celery mirrors this logic in celery_resource_config.resolve_chunk_size().
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHUNK_FLOOR = 100
DEFAULT_OOM_THRESHOLD = 0.70
DEFAULT_EXPANSION_FACTOR = 3.5
DEFAULT_AVG_ROW_BYTES = 1024
# Mirror celery_resource_config.SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX (Snowflake IN-list safety).
SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX = 175000


@dataclass
class ChunkResolutionResult:
    effective_chunk: int
    connector_ceiling: Optional[int]
    plan_natural_chunk: int
    path: str = "absolute_fallback"


def normalize_plan_name(plan: Optional[str]) -> str:
    return (plan or "free").strip().lower()


def parse_memory_gb(memory_str: Optional[str]) -> float:
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


def _extra_config_value(extra_config: Any, key: str, default: float) -> float:
    if not extra_config:
        return default
    if isinstance(extra_config, str):
        try:
            import json
            extra_config = json.loads(extra_config)
        except Exception:
            return default
    if not isinstance(extra_config, dict):
        return default
    val = extra_config.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def compute_connector_ceiling(
    src_max: Optional[int],
    dst_max: Optional[int],
    *,
    is_reverse_etl: bool = False,
    connection_chunk_count: Optional[int] = None,
) -> Optional[int]:
    """Reverse ETL (source.is_rtl): destination max_count; otherwise source max_count."""
    if is_reverse_etl:
        ceiling = dst_max
    else:
        ceiling = src_max
    if ceiling is None and connection_chunk_count:
        ceiling = int(connection_chunk_count)
    return ceiling


def compute_connector_min(
    src_min: Optional[int],
    dst_min: Optional[int],
    *,
    is_reverse_etl: bool = False,
) -> int:
    if is_reverse_etl:
        return int(dst_min or 0)
    return int(src_min or 0)


def resolve_chunk_size_django(
    *,
    connection_chunk_count: Optional[int],
    plan_config: Any,
    src_min: Optional[int] = None,
    src_max: Optional[int] = None,
    dst_min: Optional[int] = None,
    dst_max: Optional[int] = None,
    is_reverse_etl: bool = False,
    override: Any = None,
    pod_memory_limit_gb: Optional[float] = None,
) -> ChunkResolutionResult:
    """Resolve effective chunk size from plan, connector limits, and optional connection value."""
    connector_ceiling = compute_connector_ceiling(
        src_max,
        dst_max,
        is_reverse_etl=is_reverse_etl,
        connection_chunk_count=connection_chunk_count,
    )

    max_chunk = max(1, int(getattr(plan_config, "max_chunk_size", 0) or 0))
    if SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX > 0:
        max_chunk = min(max_chunk, SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX)
    chunk_size_pct = getattr(plan_config, "chunk_size_pct", None)
    if connector_ceiling is not None and chunk_size_pct is not None:
        if not is_reverse_etl and max_chunk <= 50_000:
            plan_natural_chunk = min(max_chunk, connector_ceiling)
            path = "plan_cap"
        else:
            plan_natural_chunk = min(
                max_chunk,
                math.floor(connector_ceiling * float(chunk_size_pct) / 100),
            )
            path = "percentage"
    else:
        plan_natural_chunk = max_chunk
        path = "absolute_fallback"

    if override and getattr(override, "max_chunk_size", None):
        plan_natural_chunk = min(plan_natural_chunk, int(override.max_chunk_size))

    if SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX > 0:
        plan_natural_chunk = min(plan_natural_chunk, SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX)

    if connection_chunk_count is not None:
        effective = min(int(connection_chunk_count), plan_natural_chunk)
    else:
        effective = plan_natural_chunk

    connector_min = compute_connector_min(
        src_min, dst_min, is_reverse_etl=is_reverse_etl
    )
    if connector_min > 0:
        effective = max(effective, connector_min)
    if connector_ceiling is not None:
        effective = min(effective, connector_ceiling)

    min_floor = getattr(plan_config, "min_chunk_size", None) or DEFAULT_MIN_CHUNK_FLOOR
    effective = max(effective, int(min_floor))

    if pod_memory_limit_gb is None:
        pod_memory_limit_gb = parse_memory_gb(getattr(plan_config, "memory_limit", None))

    extra = getattr(plan_config, "extra_config", None)
    oom_threshold = _extra_config_value(extra, "oom_threshold", DEFAULT_OOM_THRESHOLD)
    expansion_factor = _extra_config_value(extra, "expansion_factor", DEFAULT_EXPANSION_FACTOR)

    estimated_gb = (
        DEFAULT_AVG_ROW_BYTES * effective * expansion_factor
    ) / (1024 ** 3)
    if estimated_gb > pod_memory_limit_gb * oom_threshold:
        safe = math.floor(
            (pod_memory_limit_gb * oom_threshold * (1024 ** 3))
            / (DEFAULT_AVG_ROW_BYTES * expansion_factor)
        )
        if safe < effective:
            logger.warning(
                "CHUNK_SIZE_REDUCED | "
                f"original={effective} | "
                f"reduced_to={safe} | "
                f"estimated_gb={estimated_gb:.2f} | "
                f"pod_memory_gb={pod_memory_limit_gb}"
            )
            effective = max(safe, int(min_floor))

    logger.info(
        "CHUNK_RESOLVED | "
        f"path={path} | "
        f"connector_ceiling={connector_ceiling} | "
        f"plan_pct={chunk_size_pct} | "
        f"plan_natural_chunk={plan_natural_chunk} | "
        f"connection_chunk={connection_chunk_count} | "
        f"effective={effective}"
    )
    return ChunkResolutionResult(
        effective_chunk=effective,
        connector_ceiling=connector_ceiling,
        plan_natural_chunk=plan_natural_chunk,
        path=path,
    )


def resolve_effective_max_chunk_for_plan(
    plan_config: Any,
    src_max: Optional[int],
    dst_max: Optional[int],
    override: Any = None,
    *,
    is_reverse_etl: bool = False,
) -> int:
    """Upper bound for UI/API validation (plan path without connection clamp)."""
    result = resolve_chunk_size_django(
        connection_chunk_count=None,
        plan_config=plan_config,
        src_max=src_max,
        dst_max=dst_max,
        is_reverse_etl=is_reverse_etl,
        override=override,
    )
    ceiling = result.connector_ceiling
    if ceiling is not None:
        return min(result.plan_natural_chunk, ceiling)
    return result.plan_natural_chunk


def resolve_default_connection_chunk_count(company, source_model, destination_model) -> int:
    from apps.elttool.models import PlanResourceConfig

    plan_name = normalize_plan_name(getattr(company, "plan", None) if company else "free")
    try:
        plan_row = PlanResourceConfig.objects.get(plan_name=plan_name)
    except PlanResourceConfig.DoesNotExist:
        plan_row = PlanResourceConfig.objects.filter(plan_name="free").first()
        if plan_row is None:
            return DEFAULT_MIN_CHUNK_FLOOR

    override = None
    if company is not None:
        override = getattr(company, "resource_override", None)

    is_reverse_etl = bool(getattr(source_model, "is_rtl", False))
    result = resolve_chunk_size_django(
        connection_chunk_count=None,
        plan_config=plan_row,
        src_min=getattr(source_model, "min_count", None),
        src_max=getattr(source_model, "max_count", None),
        dst_min=getattr(destination_model, "min_count", None),
        dst_max=getattr(destination_model, "max_count", None),
        is_reverse_etl=is_reverse_etl,
        override=override,
    )
    return result.effective_chunk
