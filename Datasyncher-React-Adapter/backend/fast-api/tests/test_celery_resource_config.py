import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from unittest.mock import MagicMock

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

import celery_resource_config as rc  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


def _cfg(**kwargs):
    defaults = dict(
        plan_name="standard",
        max_pods=2,
        concurrency=3,
        effective_concurrency=3,
        max_chunk_size=50000,
        max_inflight_tables=6,
        max_concurrent_jobs=1,
        cpu_request="2",
        cpu_limit="2",
        memory_request="8Gi",
        memory_limit="8Gi",
        memory_limit_bytes=rc.parse_memory_quantity("8Gi"),
        priority=50,
        chunk_size_pct=25.0,
        max_concurrency=10,
        extra_config={},
    )
    defaults.update(kwargs)
    return rc.EffectiveResourceConfig(**defaults)


def _connector(chunk_count=10000, dst_max=500, src_max=None, src_is_rtl=False):
    return rc.ConnectorLimits(
        chunk_count=chunk_count,
        dst_max_count=dst_max,
        src_max_count=src_max,
        dst_min_count=1,
        src_min_count=1,
        src_is_rtl=src_is_rtl,
    )


def test_parse_memory_quantity():
    assert rc.parse_memory_quantity("8Gi") == 8 * 1024 ** 3
    assert rc.parse_memory_quantity("512Mi") == 512 * 1024 ** 2


def test_parse_memory_gb():
    assert rc.parse_memory_gb("8Gi") == 8.0
    assert rc.parse_memory_gb("16G") == 16.0
    assert abs(rc.parse_memory_gb("512Mi") - 0.5) < 0.01
    assert rc.parse_memory_gb("") == 8.0


def test_execution_plan_premium_one_table_one_pod():
    """Per-job orchestration must provision 1 pod for a single-table premium job."""
    cfg = _cfg(
        plan_name="premium",
        max_pods=3,
        concurrency=4,
        effective_concurrency=4,
        max_inflight_tables=12,
        max_chunk_size=100000,
    )
    plan = rc.compute_execution_plan(cfg, 1)
    assert plan.required_pods == 1
    assert plan.effective_pods == 1
    assert plan.max_inflight_children == 1


def test_execution_plan_enterprise_two_tables():
    cfg = _cfg(
        plan_name="enterprise",
        max_pods=4,
        concurrency=6,
        effective_concurrency=6,
        max_chunk_size=200000,
        max_inflight_tables=24,
        max_concurrent_jobs=3,
        cpu_request="8",
        cpu_limit="8",
        memory_request="32Gi",
        memory_limit="32Gi",
        memory_limit_bytes=rc.parse_memory_quantity("32Gi"),
        priority=90,
        chunk_size_pct=100.0,
        max_concurrency=24,
    )
    plan = rc.compute_execution_plan(cfg, 2)
    assert plan.effective_pods == 1
    assert plan.max_inflight_children == 2


def test_clamp_chunk_layer1_and_layer2():
    cfg = _cfg(
        plan_name="free",
        max_pods=1,
        concurrency=3,
        max_chunk_size=25000,
        max_inflight_tables=3,
        memory_limit_bytes=rc.parse_memory_quantity("8Gi"),
        priority=10,
        expansion_factor=3.0,
    )
    assert rc.clamp_chunk_size(100000, cfg) == 25000
    wide_row = rc.clamp_chunk_size(25000, cfg, avg_row_bytes=120_000, table_name="Case")
    assert wide_row < 25000


def test_single_table_chunk_boost_databricks_multiplies_concurrency():
    cfg = _cfg(
        plan_name="premium",
        max_pods=1,
        concurrency=4,
        effective_concurrency=4,
        max_chunk_size=100000,
        memory_limit="12Gi",
        memory_limit_bytes=rc.parse_memory_quantity("12Gi"),
    )
    boosted = rc.apply_single_table_chunk_boost(
        100000,
        cfg,
        job_table_count=1,
        connector_type="microsoft_dynamics_databricks",
    )
    assert boosted == 400000


def test_single_table_chunk_boost_databricks_ignores_low_connector_ceiling():
    """Connection dst_max=200k must not block Premium 100k×4 → 400k single-table boost."""
    cfg = _cfg(
        plan_name="premium",
        max_pods=1,
        concurrency=4,
        effective_concurrency=4,
        max_chunk_size=100000,
        memory_limit="12Gi",
        memory_limit_bytes=rc.parse_memory_quantity("12Gi"),
    )
    boosted = rc.apply_single_table_chunk_boost(
        100000,
        cfg,
        job_table_count=1,
        connector_type="microsoft_dynamics_databricks",
        connector_ceiling=200000,
    )
    assert boosted == 400000


def test_single_table_chunk_boost_skipped_for_multi_table_job():
    cfg = _cfg(max_chunk_size=100000, concurrency=4, effective_concurrency=4)
    assert rc.apply_single_table_chunk_boost(
        100000,
        cfg,
        job_table_count=5,
        connector_type="microsoft_dynamics_databricks",
    ) == 100000


def test_clamp_chunk_single_table_job_allows_above_plan_max():
    cfg = _cfg(
        plan_name="premium",
        max_chunk_size=100000,
        memory_limit="12Gi",
        memory_limit_bytes=rc.parse_memory_quantity("12Gi"),
    )
    clamped = rc.clamp_chunk_size(
        300000,
        cfg,
        connector="microsoft_dynamics_databricks",
        single_table_job=True,
    )
    assert clamped == 300000


def test_merge_company_override_max_pods():
    plan = rc._yaml_plan_defaults("enterprise")
    merged = rc._merge_override(plan, {"max_pods": 8, "legacy_pod_count": 5})
    assert merged["max_pods"] == 8


def test_resolve_falls_back_without_db_tables():
    db = MagicMock()
    db.execute.side_effect = Exception("relation does not exist")
    cfg = rc.resolve_effective_resource_config(db, 1, plan_hint="free")
    assert cfg.plan_name == "free"
    assert cfg.max_pods == 2
    assert cfg.source == "yaml_fallback"


def test_store_and_load_job_execution_config():
    r = FakeRedis()
    cfg = _cfg()
    ep = rc.compute_execution_plan(cfg, 6)
    rc.store_job_execution_config(r, "job_x", ep)
    loaded = rc.load_job_execution_config(r, "job_x")
    assert loaded["execution"]["effective_pods"] == 2
    assert loaded["config"]["plan_name"] == "standard"
    rc.delete_job_execution_config(r, "job_x")
    assert rc.load_job_execution_config(r, "job_x") is None


# --- Chunk resolution ---

def test_chunk_pct_path_dynamics():
    cfg = _cfg(chunk_size_pct=25.0, max_chunk_size=50000)
    conn = _connector(chunk_count=200000, src_max=200000, dst_max=50000)
    effective, ceiling, natural = rc.resolve_chunk_size(conn, cfg)
    assert ceiling == 200000
    assert natural == 50000
    assert effective == 50000


def test_chunk_free_plan_dynamics_uses_plan_cap():
    cfg = _cfg(plan_name="free", chunk_size_pct=25.0, max_chunk_size=50000)
    conn = _connector(chunk_count=50000, src_max=200000, dst_max=50000)
    effective, ceiling, natural = rc.resolve_chunk_size(conn, cfg)
    assert ceiling == 200000
    assert natural == 50000
    assert effective == 50000


def test_chunk_enterprise_dynamics_clamped_to_snowflake_safe_max():
    """DB may say 200k; SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX clamps to 175k."""
    cfg = _cfg(
        plan_name="enterprise",
        chunk_size_pct=100.0,
        max_chunk_size=200000,
    )
    conn = _connector(chunk_count=200000, src_max=200000, dst_max=50000)
    effective, ceiling, natural = rc.resolve_chunk_size(conn, cfg)
    assert ceiling == 200000
    assert natural == rc.SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX
    assert effective == rc.SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX


def test_chunk_pct_path_salesforce_reverse_etl():
    cfg = _cfg(chunk_size_pct=25.0)
    conn = _connector(chunk_count=10000, src_max=50000, dst_max=500, src_is_rtl=True)
    effective, _, natural = rc.resolve_chunk_size(conn, cfg)
    assert natural == 125
    assert effective == 125


def test_chunk_pct_path_enterprise_dynamics():
    cfg = _cfg(
        plan_name="enterprise",
        chunk_size_pct=100.0,
        max_chunk_size=175000,
        max_concurrency=24,
    )
    conn = _connector(chunk_count=200000, src_max=200000, dst_max=50000)
    effective, ceiling, natural = rc.resolve_chunk_size(conn, cfg)
    assert ceiling == 200000
    assert natural == 175000
    assert effective == 175000


def test_chunk_enterprise_100pct_uses_connection_chunk_when_src_max_null():
    cfg = _cfg(
        plan_name="enterprise",
        chunk_size_pct=100.0,
        max_chunk_size=175000,
    )
    conn = _connector(chunk_count=200000, src_max=None, dst_max=50000)
    effective, ceiling, natural = rc.resolve_chunk_size(conn, cfg)
    assert ceiling == 200000
    assert natural == 175000
    assert effective == 175000


def test_chunk_pct_path_enterprise_dynamics_legacy_100pct_clamped():
    cfg = _cfg(
        plan_name="enterprise",
        chunk_size_pct=100.0,
        max_chunk_size=200000,
        max_concurrency=24,
    )
    conn = _connector(chunk_count=200000, src_max=200000, dst_max=50000)
    effective, _, natural = rc.resolve_chunk_size(conn, cfg)
    assert natural == rc.SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX
    assert effective == rc.SNOWFLAKE_SAFE_IN_LIST_CHUNK_MAX


def test_chunk_absolute_fallback_no_ceiling():
    cfg = _cfg(chunk_size_pct=None, max_chunk_size=25000)
    conn = _connector(chunk_count=0, dst_max=None, src_max=None)
    effective, ceiling, natural = rc.resolve_chunk_size(conn, cfg)
    assert ceiling is None
    assert natural == 25000
    assert effective == rc.DYNAMIC_MIN_CHUNK_FLOOR


def test_chunk_clamped_to_connection_value():
    cfg = _cfg(chunk_size_pct=100.0, max_chunk_size=200000)
    conn = _connector(chunk_count=30000, src_max=200000, dst_max=50000)
    effective, _, _ = rc.resolve_chunk_size(conn, cfg)
    assert effective == 30000


def test_chunk_floor_guard():
    cfg = _cfg(chunk_size_pct=25.0)
    conn = _connector(chunk_count=40, src_max=160)
    effective, _, _ = rc.resolve_chunk_size(conn, cfg)
    assert effective == 100


def test_chunk_override_caps_result():
    cfg = _cfg(chunk_size_pct=100.0, max_chunk_size=100000)
    conn = _connector(chunk_count=100000, src_max=100000)
    effective, _, _ = rc.resolve_chunk_size(conn, cfg, override={"max_chunk_size": 40000})
    assert effective == 40000


def test_chunk_normal_etl_ceiling_uses_source_max():
    cfg = _cfg(chunk_size_pct=100.0, max_chunk_size=200000)
    conn = _connector(chunk_count=200000, src_max=10000, dst_max=200000)
    effective, ceiling, _ = rc.resolve_chunk_size(conn, cfg)
    assert ceiling == 10000
    assert effective == 10000


def test_chunk_reverse_etl_ceiling_uses_dest_max():
    cfg = _cfg(chunk_size_pct=100.0, max_chunk_size=200000)
    conn = _connector(
        chunk_count=200000,
        src_max=50000,
        dst_max=10000,
        src_is_rtl=True,
    )
    effective, ceiling, _ = rc.resolve_chunk_size(conn, cfg)
    assert ceiling == 10000
    assert effective == 10000


def test_chunk_returns_tuple_of_three():
    cfg = _cfg()
    conn = _connector()
    result = rc.resolve_chunk_size(conn, cfg)
    assert isinstance(result, tuple)
    assert len(result) == 3
    assert isinstance(result[0], int)


# --- Concurrency resolution ---

def test_concurrency_no_scaling_when_chunk_equals_natural():
    cfg = _cfg(concurrency=3, max_concurrency=10)
    assert rc.resolve_effective_concurrency(cfg, 50000, 50000) == 3


def test_concurrency_scales_up_salesforce_standard():
    cfg = _cfg(concurrency=3, max_concurrency=10)
    assert rc.resolve_effective_concurrency(cfg, 125, 50000) == 10


def test_concurrency_scales_up_snowflake_standard():
    cfg = _cfg(concurrency=3, max_concurrency=10)
    assert rc.resolve_effective_concurrency(cfg, 12500, 50000) == 10


def test_concurrency_scales_up_snowflake_premium():
    cfg = _cfg(concurrency=4, max_concurrency=16, chunk_size_pct=50.0)
    assert rc.resolve_effective_concurrency(cfg, 25000, 100000) == 16


def test_concurrency_enterprise_dynamics_unchanged():
    cfg = _cfg(
        plan_name="enterprise",
        concurrency=6,
        max_concurrency=24,
        chunk_size_pct=100.0,
        max_chunk_size=200000,
    )
    assert rc.resolve_effective_concurrency(cfg, 200000, 200000) == 6


def test_concurrency_enterprise_salesforce_capped():
    cfg = _cfg(
        plan_name="enterprise",
        concurrency=6,
        max_concurrency=24,
        chunk_size_pct=100.0,
    )
    assert rc.resolve_effective_concurrency(cfg, 500, 200000) == 24


def test_concurrency_override_caps_result():
    cfg = _cfg(concurrency=6, max_concurrency=24)
    assert rc.resolve_effective_concurrency(
        cfg, 500, 200000, override={"concurrency": 5},
    ) == 5


def test_concurrency_always_at_least_1():
    cfg = _cfg(concurrency=0, max_concurrency=0)
    assert rc.resolve_effective_concurrency(cfg, 100, 100) >= 1


def test_concurrency_memory_guard_fires():
    cfg = _cfg(concurrency=10, max_concurrency=10, memory_limit="8Gi")
    result = rc.resolve_effective_concurrency(cfg, 5_000_000, 5_000_000)
    assert result < 10


# --- Pod resolution ---

def test_pods_dynamics_standard_unchanged():
    cfg = _cfg(max_inflight_tables=6, concurrency=3, max_pods=2)
    assert rc.resolve_effective_pods(cfg, 3) == 2


def test_pods_reduced_salesforce_standard():
    cfg = _cfg(max_inflight_tables=6, max_pods=2)
    assert rc.resolve_effective_pods(cfg, 10) == 1


def test_pods_reduced_salesforce_enterprise():
    cfg = _cfg(
        plan_name="enterprise",
        max_inflight_tables=24,
        max_pods=4,
        max_concurrency=24,
    )
    assert rc.resolve_effective_pods(cfg, 24) == 1


def test_pods_dynamics_enterprise_full():
    cfg = _cfg(
        plan_name="enterprise",
        max_inflight_tables=36,
        max_pods=6,
        concurrency=6,
        max_concurrency=24,
    )
    assert rc.resolve_effective_pods(cfg, 6) == 6


def test_pods_snowflake_premium_reduced():
    cfg = _cfg(
        plan_name="premium",
        max_inflight_tables=12,
        max_pods=3,
        max_concurrency=16,
    )
    assert rc.resolve_effective_pods(cfg, 16) == 1


def test_pods_floored_at_1():
    cfg = _cfg(max_inflight_tables=1, max_pods=1)
    assert rc.resolve_effective_pods(cfg, 100) == 1


def test_pods_capped_at_plan_max():
    cfg = _cfg(
        plan_name="enterprise",
        max_inflight_tables=24,
        max_pods=4,
    )
    assert rc.resolve_effective_pods(cfg, 1) == 4


def test_pods_override_raises_ceiling():
    cfg = _cfg(
        plan_name="enterprise",
        max_inflight_tables=48,
        max_pods=4,
        concurrency=6,
    )
    assert rc.resolve_effective_pods(cfg, 6, override={"max_pods": 8}) == 8


def test_pods_fallback_when_max_inflight_null():
    cfg = _cfg(max_inflight_tables=0, max_pods=2, concurrency=3)
    assert rc.resolve_effective_pods(cfg, 3) == 2


# --- resolve_full_execution_config ---

def test_resolve_full_execution_config_returns_dict():
    cfg = _cfg()
    conn = _connector()
    dynamic = rc.resolve_dynamic_execution_plan(conn, cfg)
    result = rc.resolve_full_execution_config(conn, cfg)
    assert isinstance(result, dict)
    assert set(result.keys()) == {
        "chunk_size", "concurrency", "replicas",
        "connector_ceiling", "plan_natural_chunk",
        "node_pool", "cpu_request", "cpu_limit",
        "memory_request", "memory_limit",
    }
    assert result["chunk_size"] == dynamic.chunk_size
    assert result["concurrency"] == dynamic.concurrency
    assert result["replicas"] == dynamic.replicas
    assert result["memory_request"] == cfg.memory_request


def test_full_chain_standard_salesforce_via_full_config():
    cfg = _cfg(
        plan_name="standard",
        chunk_size_pct=25.0,
        concurrency=3,
        max_concurrency=10,
        max_inflight_tables=6,
        max_pods=2,
        memory_limit="8Gi",
        max_chunk_size=50000,
    )
    conn = _connector(chunk_count=10000, dst_max=500)
    config = rc.resolve_full_execution_config(conn, cfg)
    assert config["chunk_size"] == 125
    assert config["concurrency"] == 10
    assert config["replicas"] == 1


def test_full_chain_enterprise_dynamics_via_full_config():
    cfg = _cfg(
        plan_name="enterprise",
        chunk_size_pct=100.0,
        concurrency=6,
        max_concurrency=24,
        max_inflight_tables=36,
        max_pods=6,
        memory_limit="24Gi",
        memory_limit_bytes=rc.parse_memory_quantity("24Gi"),
        max_chunk_size=175000,
    )
    conn = _connector(chunk_count=200000, dst_max=200000)
    config = rc.resolve_full_execution_config(conn, cfg)
    assert config["chunk_size"] == 175000
    assert config["concurrency"] == 6
    assert config["replicas"] == 6


def test_full_chain_premium_snowflake_via_full_config():
    cfg = _cfg(
        plan_name="premium",
        chunk_size_pct=50.0,
        concurrency=4,
        max_concurrency=16,
        max_inflight_tables=12,
        max_pods=3,
        memory_limit="16Gi",
        memory_limit_bytes=rc.parse_memory_quantity("16Gi"),
        max_chunk_size=100000,
    )
    conn = _connector(chunk_count=50000, dst_max=50000)
    config = rc.resolve_full_execution_config(conn, cfg)
    assert config["chunk_size"] == 25000
    assert config["concurrency"] == 16
    assert config["replicas"] == 1


# --- node pool config ---

def _pool_db_row(pool_name="worker-premium"):
    rows = {
        "worker-standard": {
            "machine_type": "e2-standard-2",
            "allocatable_memory": "6Gi",
            "allocatable_cpu": "1900m",
            "overhead_memory": "1.5Gi",
        },
        "worker-premium": {
            "machine_type": "e2-standard-4",
            "allocatable_memory": "13Gi",
            "allocatable_cpu": "3800m",
            "overhead_memory": "2Gi",
        },
        "worker-enterprise": {
            "machine_type": "e2-standard-8",
            "allocatable_memory": "26Gi",
            "allocatable_cpu": "7800m",
            "overhead_memory": "2Gi",
        },
    }
    spec = rows.get(pool_name, rows["worker-premium"])
    return {
        "pool_name": pool_name,
        "machine_type": spec["machine_type"],
        "allocatable_memory": spec["allocatable_memory"],
        "allocatable_cpu": spec["allocatable_cpu"],
        "overhead_memory": spec["overhead_memory"],
        "overhead_cpu": "200m",
        "max_pods_per_node": 1,
        "node_selector": {"pool": pool_name},
        "taint_key": "pool",
        "taint_value": pool_name,
        "taint_effect": "NoSchedule",
    }


def _mock_db_with_pool(pool_name="worker-premium"):
    db = MagicMock()
    db.execute.return_value.mappings.return_value.first.return_value = _pool_db_row(pool_name)
    return db


def test_get_node_pool_config_from_plan():
    cfg = _cfg(worker_pool_name="worker-premium")
    pool = rc.get_node_pool_config(cfg, db=_mock_db_with_pool())
    assert pool is not None
    assert pool.pool_name == "worker-premium"


def test_get_node_pool_config_override_takes_precedence():
    cfg = _cfg(worker_pool_name="worker-standard")
    override = {"worker_pool_name": "worker-enterprise"}
    db = _mock_db_with_pool("worker-enterprise")
    pool = rc.get_node_pool_config(cfg, override=override, db=db)
    assert pool.pool_name == "worker-enterprise"


def test_get_node_pool_config_returns_none_gracefully(caplog):
    cfg = _cfg(worker_pool_name="worker-premium")
    db = MagicMock()
    db.execute.side_effect = Exception("connection refused")
    with caplog.at_level("WARNING"):
        pool = rc.get_node_pool_config(cfg, db=db)
    assert pool is None
    assert any("NODE_POOL_CONFIG_FETCH_FAILED" in rec.message for rec in caplog.records)


def test_validate_plan_fits_pool_passes(caplog):
    cfg = _cfg(memory_request="10Gi")
    node_pool = rc.NodePoolConfig(
        pool_name="worker-premium",
        machine_type="e2-standard-4",
        allocatable_memory="13Gi",
        allocatable_cpu="3800m",
        overhead_memory="2Gi",
    )
    with caplog.at_level("INFO"):
        rc.validate_plan_fits_pool(cfg, node_pool)
    assert any("NODE_FIT_VALIDATED" in rec.message for rec in caplog.records)


def test_validate_plan_fits_pool_raises(caplog):
    cfg = _cfg(memory_request="10Gi")
    node_pool = rc.NodePoolConfig(
        pool_name="worker-standard",
        machine_type="e2-standard-2",
        allocatable_memory="6Gi",
        allocatable_cpu="1900m",
        overhead_memory="1.5Gi",
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(rc.PlanNodeMismatchError):
            rc.validate_plan_fits_pool(cfg, node_pool)
    assert any("PLAN_NODE_MISMATCH" in rec.message for rec in caplog.records)


def test_validate_plan_no_pool_config_skips():
    cfg = _cfg(memory_request="10Gi")
    rc.validate_plan_fits_pool(cfg, None)


def test_full_chain_includes_node_pool():
    cfg = _cfg(
        plan_name="premium",
        worker_pool_name="worker-premium",
        memory_request="10Gi",
        memory_limit="12Gi",
    )
    conn = _connector(chunk_count=50000, dst_max=50000)
    result = rc.resolve_full_execution_config(conn, cfg, db=_mock_db_with_pool())
    assert result["node_pool"] is not None
    assert result["node_pool"].pool_name == "worker-premium"
    assert result["memory_request"] == "10Gi"


# --- Full chain (dynamic execution plan) ---

def test_full_chain_standard_salesforce():
    cfg = _cfg(
        plan_name="standard",
        chunk_size_pct=25.0,
        concurrency=3,
        max_concurrency=10,
        max_inflight_tables=6,
        max_pods=2,
        memory_limit="8Gi",
        max_chunk_size=50000,
    )
    conn = _connector(chunk_count=10000, dst_max=500)
    dynamic = rc.resolve_dynamic_execution_plan(conn, cfg)
    assert dynamic.chunk_size == 125
    assert dynamic.concurrency == 10
    assert dynamic.replicas == 1


def test_full_chain_enterprise_dynamics():
    cfg = _cfg(
        plan_name="enterprise",
        chunk_size_pct=100.0,
        concurrency=6,
        max_concurrency=24,
        max_inflight_tables=36,
        max_pods=6,
        memory_limit="24Gi",
        memory_limit_bytes=rc.parse_memory_quantity("24Gi"),
        max_chunk_size=175000,
    )
    conn = _connector(chunk_count=200000, dst_max=200000)
    dynamic = rc.resolve_dynamic_execution_plan(conn, cfg)
    assert dynamic.chunk_size == 175000
    assert dynamic.concurrency == 6
    assert dynamic.replicas == 6


def test_full_chain_premium_snowflake():
    cfg = _cfg(
        plan_name="premium",
        chunk_size_pct=50.0,
        concurrency=4,
        max_concurrency=16,
        max_inflight_tables=12,
        max_pods=3,
        memory_limit="16Gi",
        memory_limit_bytes=rc.parse_memory_quantity("16Gi"),
        max_chunk_size=100000,
    )
    conn = _connector(chunk_count=50000, dst_max=50000)
    dynamic = rc.resolve_dynamic_execution_plan(conn, cfg)
    assert dynamic.chunk_size == 25000
    assert dynamic.concurrency == 16
    assert dynamic.replicas == 1


# --- Flow profiles + even assignment ---

def test_yaml_plan_defaults_pod_caps():
    assert rc._YAML_PLAN_DEFAULTS["free"]["max_pods"] == 2
    assert rc._YAML_PLAN_DEFAULTS["standard"]["max_pods"] == 2
    assert rc._YAML_PLAN_DEFAULTS["discounted"]["max_pods"] == 2
    assert rc._YAML_PLAN_DEFAULTS["premium"]["max_pods"] == 4
    assert rc._YAML_PLAN_DEFAULTS["enterprise"]["max_pods"] == 6
    # Enterprise is larger than Premium (e2-standard-8 / 24Gi), not premium-equal.
    prem = rc._YAML_PLAN_DEFAULTS["premium"]
    ent = rc._YAML_PLAN_DEFAULTS["enterprise"]
    assert ent["concurrency"] == 6
    assert ent["cpu_request"] == "7"
    assert ent["memory_request"] == "22Gi"
    assert ent["memory_limit"] == "24Gi"
    assert ent["max_chunk_size"] == 175000
    assert ent["chunk_size_pct"] == 100.0
    assert ent["max_inflight_tables"] == ent["max_pods"] * ent["concurrency"]
    assert ent["max_pods"] == prem["max_pods"] + 2
    assert ent["memory_limit"] != prem["memory_limit"]
    assert ent["concurrency"] != prem["concurrency"]


def test_flow_resource_profile_memory_and_concurrency():
    dyn = rc.get_flow_resource_profile("microsoft_dynamics")
    assert dyn["memory_estimate_bytes"] == 4 * 1024 ** 3
    assert dyn["preferred_concurrency"] == 3
    sf = rc.get_flow_resource_profile("salesforce")
    assert sf["memory_estimate_bytes"] == 8 * 1024 ** 3
    assert sf["preferred_concurrency"] == 2
    light = rc.get_flow_resource_profile("amazon_s3")
    assert light["preferred_max_pods"] == 2


def test_partition_even_shard_sizes_differ_by_at_most_one():
    assert rc.partition_even_shard_sizes(0, 4) == []
    assert rc.partition_even_shard_sizes(5, 0) == []
    assert rc.partition_even_shard_sizes(1, 4) == [1]
    assert rc.partition_even_shard_sizes(100, 4) == [25, 25, 25, 25]
    assert rc.partition_even_shard_sizes(10, 4) == [3, 3, 2, 2]
    sizes = rc.partition_even_shard_sizes(11, 4)
    assert max(sizes) - min(sizes) <= 1
    assert sum(sizes) == 11


def test_assign_even_shards_contiguous_blocks():
    assignment = rc.assign_even_shards(10, 4)
    assert assignment == [0, 0, 0, 1, 1, 1, 2, 2, 3, 3]
    assert len(set(assignment)) == 4


def test_order_indices_round_robin_waves():
    # Contiguous shards [0,0,0,1,1,1,2,2,3,3] → wave order starts each shard
    ordered = rc.order_indices_round_robin(10, 4)
    assert ordered[0:4] == [0, 3, 6, 8]
    assert len(ordered) == 10
    assert sorted(ordered) == list(range(10))


def test_execution_plan_caps_at_plan_and_tables():
    cfg = _cfg(plan_name="premium", max_pods=4, concurrency=3, effective_concurrency=3, max_inflight_tables=16)
    plan = rc.compute_execution_plan(cfg, 100)
    assert plan.effective_pods == 4
    assert plan.max_inflight_children == 12  # 4 * 3
    one = rc.compute_execution_plan(cfg, 1)
    assert one.effective_pods == 1


def test_execution_plan_respects_flow_preferred_and_pod_cap():
    cfg = _cfg(plan_name="premium", max_pods=4, concurrency=3, effective_concurrency=3, max_inflight_tables=16)
    # Connector prefers 2; request budget slice is 3 → hard cap = min(4, 2, 3) = 2
    plan = rc.compute_execution_plan(
        cfg,
        100,
        flow_preferred_max_pods=2,
        pod_cap=3,
    )
    assert plan.effective_pods == 2
    # Pipeline budget alone (no preferred) caps at 1
    budgeted = rc.compute_execution_plan(cfg, 100, pod_cap=1)
    assert budgeted.effective_pods == 1
    # Preferred alone without pod_cap still applied
    preferred = rc.compute_execution_plan(cfg, 100, flow_preferred_max_pods=2)
    assert preferred.effective_pods == 2
