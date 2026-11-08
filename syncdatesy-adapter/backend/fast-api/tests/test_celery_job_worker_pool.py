import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

os.environ.setdefault("CELERY_JOB_ISOLATION_MODE", "per_job")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

import celery_resource_config as rc  # noqa: E402
import celery_job_worker_pool as pool  # noqa: E402


def test_image_pull_policy_skips_registry_for_digest():
    assert pool._image_pull_policy("gcr.io/app/image@sha256:abc") == "IfNotPresent"
    assert pool._image_pull_policy("gcr.io/app/image:batch") == "Always"


def test_cluster_cap_is_per_pool_so_another_plan_can_still_start():
    import importlib

    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "90",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS_PER_POOL": "2",
        },
    ):
        import celery_job_worker_pool as jp
        importlib.reload(jp)
        r = FakeRedis()
        jp._register_job_worker(
            r,
            "premium_full",
            2,
            "q",
            "dep",
            plan_name="premium",
            node_pool="worker-premium",
        )

        def fake_k8s(method, path, body=None, timeout=10.0):
            if method == "GET":
                return {"_http_status": 404}
            return {}

        blocked = jp.provision_job_workers(
            "premium_next",
            1,
            r=r,
            plan="premium",
            config=_execution_config(_premium_pool()),
            k8s_request=fake_k8s,
        )
        allowed = jp.provision_job_workers(
            "standard_new",
            1,
            r=r,
            plan="standard",
            config=_execution_config(_standard_pool()),
            k8s_request=fake_k8s,
        )

    assert blocked["status"] == "deferred"
    assert blocked["reason"] == "cluster_cap"
    assert allowed["status"] == "ok"
    importlib.reload(jp)


def test_pool_specific_cap_overrides_the_shared_fallback():
    import importlib

    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "254",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS_PER_POOL": "30",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_PREMIUM": "1",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS_POOL_STANDARD": "5",
        },
    ):
        import celery_job_worker_pool as jp
        importlib.reload(jp)
        r = FakeRedis()
        jp._register_job_worker(
            r,
            "premium_full",
            1,
            "q",
            "dep",
            plan_name="premium",
            node_pool="worker-premium",
        )

        def fake_k8s(method, path, body=None, timeout=10.0):
            if method == "GET":
                return {"_http_status": 404}
            return {}

        blocked = jp.provision_job_workers(
            "premium_next",
            1,
            r=r,
            plan="premium",
            config=_execution_config(_premium_pool()),
            k8s_request=fake_k8s,
        )
        allowed = jp.provision_job_workers(
            "standard_new",
            1,
            r=r,
            plan="standard",
            config=_execution_config(_standard_pool()),
            k8s_request=fake_k8s,
        )

    assert blocked["status"] == "deferred"
    assert blocked["reason"] == "cluster_cap"
    assert allowed["status"] == "ok"
    importlib.reload(jp)


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}
        self.zsets = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value if isinstance(value, str) else str(value)

    def delete(self, key):
        self.store.pop(key, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return self.sets.get(key, set())

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zrem(self, key, member):
        self.zsets.get(key, {}).pop(member, None)

    def zrevrange(self, key, start, end):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda x: x[1], reverse=True)
        return [name for name, _ in items[start : end + 1]]

    def incr(self, key):
        self.store[key] = str(int(self.store.get(key) or 0) + 1)
        return int(self.store[key])

    def expire(self, key, _ex):
        return True


def test_job_hash_and_queue_naming_stable():
    job = "ATI_D365_PROD_manual_29"
    h = pool.job_hash(job)
    assert len(h) == 12
    assert pool.job_queue_name(job) == f"migration:job:{h}"
    assert pool.deployment_name(job).startswith("celery-worker-job-")


def test_build_deployment_spec_includes_worker_pool_toleration():
    spec = pool._build_deployment_spec("job_tol", 1, "migration:job:abc", 3)
    pod_spec = spec["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"pool": "worker-standard"}
    assert pod_spec["tolerations"] == []


def _premium_pool():
    return rc.NodePoolConfig(
        pool_name="worker-premium",
        machine_type="e2-standard-4",
        allocatable_memory="13Gi",
        allocatable_cpu="3800m",
        node_selector={"pool": "worker-premium"},
        taint_key="pool",
        taint_value="worker-premium",
    )


def _standard_pool():
    return rc.NodePoolConfig(
        pool_name="worker-standard",
        machine_type="e2-standard-2",
        allocatable_memory="6Gi",
        allocatable_cpu="1900m",
        node_selector={"pool": "worker-standard"},
    )


def _execution_config(node_pool, **kwargs):
    defaults = dict(
        chunk_size=25000,
        concurrency=4,
        replicas=1,
        connector_ceiling=None,
        plan_natural_chunk=25000,
        node_pool=node_pool,
        cpu_request="4",
        cpu_limit="4",
        memory_request="12Gi",
        memory_limit="12Gi",
    )
    defaults.update(kwargs)
    return defaults


def test_provision_uses_pool_node_selector():
    config = _execution_config(_premium_pool())
    spec = pool._build_deployment_spec(
        "job_premium", 1, "migration:job:abc", 4, config=config,
    )
    pod_spec = spec["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"pool": "worker-premium"}


def test_provision_adds_taint_toleration():
    config = _execution_config(_premium_pool())
    spec = pool._build_deployment_spec(
        "job_premium_tol", 1, "migration:job:abc", 4, config=config,
    )
    pod_spec = spec["spec"]["template"]["spec"]
    assert pod_spec["tolerations"] == [{
        "key": "pool",
        "operator": "Equal",
        "value": "worker-premium",
        "effect": "NoSchedule",
    }]


def test_provision_no_taint_for_standard_pool():
    config = _execution_config(_standard_pool())
    spec = pool._build_deployment_spec(
        "job_standard", 1, "migration:job:abc", 3, config=config,
    )
    pod_spec = spec["spec"]["template"]["spec"]
    assert pod_spec["tolerations"] == []


def test_provision_uses_exact_plan_memory():
    config = _execution_config(
        _premium_pool(),
        memory_request="12Gi",
        memory_limit="12Gi",
    )
    spec = pool._build_deployment_spec(
        "job_mem", 1, "migration:job:abc", 4, config=config,
    )
    container = spec["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["requests"]["memory"] == "12Gi"
    assert container["resources"]["limits"]["memory"] == "12Gi"


def test_provision_company_override_pool():
    enterprise_pool = rc.NodePoolConfig(
        pool_name="worker-enterprise",
        machine_type="e2-standard-8",
        allocatable_memory="26Gi",
        allocatable_cpu="7800m",
        node_selector={"pool": "worker-enterprise"},
        taint_key="pool",
        taint_value="worker-enterprise",
    )
    config = _execution_config(enterprise_pool)
    spec = pool._build_deployment_spec(
        "job_override", 1, "migration:job:abc", 3, config=config,
    )
    pod_spec = spec["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"pool": "worker-enterprise"}


def test_compute_effective_pods_plan_caps():
    # Free: 2 pod max, 10 tables -> 2 pods (ceil(10/3)=4 capped to 2)
    assert pool.compute_effective_pods(10, "free") == 2
    # Enterprise: 6 pod max, 2 tables -> 1 pod (ceil(2/6)=1); never empty pods
    assert pool.compute_effective_pods(2, "enterprise") == 1
    assert pool.compute_effective_pods(1, "enterprise") == 1
    # Standard: 6 tables -> ceil(6/3)=2
    assert pool.compute_effective_pods(6, "standard") == 2
    # Premium / enterprise high table count hit plan ceilings
    assert pool.compute_effective_pods(100, "premium") == 4
    assert pool.compute_effective_pods(100, "enterprise") == 6


def test_resolve_flow_fleet_provision_replicas_right_sizes_single_node():
    assert pool.resolve_flow_fleet_provision_replicas(1, plan="premium", pod_cap=1) == 1
    assert pool.resolve_flow_fleet_provision_replicas(4, plan="premium", pod_cap=4) == 4
    assert pool.resolve_flow_fleet_provision_replicas(1, plan="premium", pod_cap=None) == 4
    assert pool.resolve_flow_fleet_provision_replicas(2, plan="premium", pod_cap=2) == 2


def test_provision_registers_redis_and_calls_k8s():
    r = FakeRedis()
    calls = []

    def fake_k8s(method, path, body=None, timeout=10.0):
        calls.append((method, path, body))
        if method == "GET":
            return {"_http_status": 404}
        return {}

    with patch.dict(os.environ, {"CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20", "CELERY_JOB_ISOLATION_MODE": "per_job"}):
        import importlib
        import celery_job_worker_pool as jp
        importlib.reload(jp)
        result = jp.provision_job_workers(
            "job_a",
            2,
            r=r,
            plan="standard",
            k8s_request=fake_k8s,
        )

    assert result["status"] == "ok"
    assert any(c[0] == "POST" for c in calls)
    h = pool.job_hash("job_a")
    assert h in r.sets[pool.REDIS_ACTIVE_SET_KEY]
    meta = json.loads(r.store[pool._meta_key("job_a")])
    assert meta["replicas"] == 2


def test_provision_stable_fleet_never_shrinks_replicas():
    """Mid-run re-provision must keep existing replicas (stable fleet until teardown)."""
    r = FakeRedis()
    calls = []

    def fake_k8s(method, path, body=None, timeout=10.0):
        calls.append((method, path, body))
        if method == "GET":
            return {
                "_http_status": 200,
                "spec": {"replicas": 4},
                "status": {"replicas": 4},
            }
        return {}

    with patch.dict(os.environ, {"CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20", "CELERY_JOB_ISOLATION_MODE": "per_job"}):
        import importlib
        import celery_job_worker_pool as jp
        importlib.reload(jp)
        result = jp.provision_job_workers(
            "job_stable",
            2,  # would shrink without stable-fleet guard
            r=r,
            plan="premium",
            k8s_request=fake_k8s,
        )

    assert result["status"] == "ok"
    assert result["replicas"] == 4
    patch_calls = [c for c in calls if c[0] == "PATCH"]
    assert patch_calls
    assert patch_calls[0][2]["spec"]["replicas"] == 4


def test_teardown_clears_registry():
    r = FakeRedis()
    pool._register_job_worker(
        r, "job_b", 1, "migration:job:x", "celery-worker-job-x",
        state=pool.META_STATE_PROVISIONING,
    )
    deleted = []

    def fake_k8s(method, path, body=None, timeout=10.0):
        deleted.append(method)
        return {}

    with patch("celery_job_worker_pool.time.sleep", return_value=None):
        pool.teardown_job_workers("job_b", r=r, k8s_request=fake_k8s)

    assert pool.job_hash("job_b") not in r.sets.get(pool.REDIS_ACTIVE_SET_KEY, set())
    assert "PATCH" in deleted
    assert "DELETE" in deleted


def test_child_task_queue_routing_in_worker():
    os.environ["CELERY_JOB_ISOLATION_MODE"] = "per_job"
    import importlib
    import celery_job_worker_pool
    import celery_worker

    importlib.reload(celery_job_worker_pool)
    celery_worker = importlib.reload(celery_worker)
    q = celery_worker._child_task_queue("my_job")
    assert q.startswith("migration:job:")


def test_finalize_teardown_calls_job_pool(monkeypatch):
    teardown_calls = []

    def fake_teardown(job_name, fast=True, r=None):
        teardown_calls.append(job_name)
        return {"status": "ok"}

    monkeypatch.setenv("CELERY_JOB_ISOLATION_MODE", "per_job")
    import importlib
    import celery_job_worker_pool
    import celery_worker

    importlib.reload(celery_job_worker_pool)
    celery_worker = importlib.reload(celery_worker)
    monkeypatch.setattr(celery_worker.job_pool, "teardown_job_workers", fake_teardown)
    monkeypatch.setattr(celery_worker, "_cleanup_fanout_stagger", lambda _j: None)
    monkeypatch.setattr(celery_worker, "_enrich_parent_ctx_from_db", lambda *a, **k: {})
    monkeypatch.setattr(celery_worker, "_log_aggregated_completion", lambda **k: None)
    monkeypatch.setattr(celery_worker, "update_job_status", lambda *a, **k: True)

    celery_worker.finalize_migration_orchestration.run(
        [{"status": "completed"}],
        "test_job",
        1,
        is_listing_path=False,
        parent_ctx={},
    )
    assert teardown_calls == ["test_job"]


def test_orchestrate_fails_cleanly_on_mismatch(monkeypatch, caplog):
    status_updates = []

    def fake_update(job_name, status, connection_id, message):
        status_updates.append((job_name, status, message))
        return True

    monkeypatch.setenv("CELERY_JOB_ISOLATION_MODE", "per_job")
    import importlib
    import celery_worker

    cw = importlib.reload(celery_worker)
    monkeypatch.setattr(cw, "update_job_status", fake_update)

    with caplog.at_level("ERROR"):
        result = cw.finalize_migration_orchestration.run(
            [],
            "mismatch_job",
            42,
            status="failed",
            reason="Node pool cannot satisfy plan resources: pool too small",
        )

    assert result["status"] == "failed"
    assert status_updates
    assert status_updates[0][0] == "mismatch_job"
    assert any("JOB_PROVISION_FAILED_NODE_MISMATCH" in rec.message for rec in caplog.records)


def test_flow_fleet_naming_and_queue():
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        job_a = "pipeline_run_42_node_7__CONN_manual_1_t1"
        job_b = "pipeline_run_42_node_8__CONN_manual_2_t2"
        assert jp.parse_pipeline_run_id(job_a) == 42
        assert jp.fleet_key_for_job(job_a) == "pipeline_run_42"
        assert jp.fleet_key_for_job(job_b) == "pipeline_run_42"
        assert jp.fleet_hash(job_a) == jp.fleet_hash(job_b)
        assert jp.fleet_queue_name(job_a).startswith("migration:flow:")
        assert jp.fleet_queue_name(job_a) == jp.fleet_queue_name(job_b)
        assert jp.fleet_deployment_name(job_a).startswith("celery-worker-flow-")
        assert jp.fleet_deployment_name(job_a) == jp.fleet_deployment_name(job_b)
        # Standalone jobs stay per-job.
        solo = "ATI_D365_PROD_manual_29"
        assert jp.fleet_queue_name(solo).startswith("migration:job:")
        assert jp.fleet_deployment_name(solo).startswith("celery-worker-job-")


def test_run_scoped_fleet_teardown_name_when_reuse_disabled():
    """Teardown must still target celery-worker-flow-* after reuse is turned off."""
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "false",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        fleet_key = "pipeline_run_5341"
        assert jp.is_run_scoped_fleet_key(fleet_key)
        assert jp.is_flow_fleet_job(fleet_key)
        assert jp.fleet_deployment_name(fleet_key).startswith("celery-worker-flow-")
        # Per-node job names stay dedicated when reuse is off.
        node_job = "pipeline_run_5341_node_7__CONN_manual_1_t1"
        assert not jp.is_run_scoped_fleet_key(node_job)
        assert not jp.is_flow_fleet_job(node_job)
        assert jp.fleet_deployment_name(node_job).startswith("celery-worker-job-")


def test_flow_fleet_release_keeps_deployment():
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()
        job_a = "pipeline_run_9_node_1"
        job_b = "pipeline_run_9_node_2"
        jp._register_job_worker(
            r,
            job_a,
            2,
            jp.fleet_queue_name(job_a),
            jp.fleet_deployment_name(job_a),
            source_job_name=job_a,
        )
        jp._register_job_worker(
            r,
            job_b,
            2,
            jp.fleet_queue_name(job_b),
            jp.fleet_deployment_name(job_b),
            source_job_name=job_b,
        )
        deleted = []

        def fake_k8s(method, path, body=None, timeout=10.0):
            deleted.append(method)
            return {}

        result = jp.release_flow_fleet_lease(job_a, r=r)
        assert result["status"] == "ok"
        assert job_a not in result["active_jobs"]
        assert job_b in result["active_jobs"]
        assert "DELETE" not in deleted
        assert jp.fleet_hash(job_a) in r.sets.get(jp.REDIS_ACTIVE_SET_KEY, set())
        assert jp.flow_fleet_lease_active(r, 9)

        with patch("celery_job_worker_pool.time.sleep", return_value=None):
            jp.teardown_flow_fleet(9, r=r, k8s_request=fake_k8s)
        assert "DELETE" in deleted
        assert jp.fleet_hash(job_a) not in r.sets.get(jp.REDIS_ACTIVE_SET_KEY, set())
        assert not jp.flow_fleet_lease_active(r, 9)


def test_sweep_keeps_flow_fleet_with_lease():
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()
        job = "pipeline_run_55_node_3"
        jp._register_job_worker(
            r,
            job,
            2,
            jp.fleet_queue_name(job),
            jp.fleet_deployment_name(job),
            source_job_name=job,
        )
        jp.refresh_flow_fleet_lease(r, 55)
        tear_calls = []

        class FakeDB:
            def execute(self, *_a, **_k):
                class R:
                    def fetchall(self_inner):
                        # Gap between nodes: no in_progress rows.
                        return []
                return R()

        def fake_teardown(job_name, fast=True, r=None, k8s_request=None):
            tear_calls.append(job_name)
            return {"status": "ok"}

        with patch.object(jp, "teardown_job_workers", side_effect=fake_teardown):
            deleted = jp.sweep_stale_job_workers(FakeDB(), r)
        assert deleted == 0
        assert tear_calls == []

        jp.clear_flow_fleet_lease(r, 55)
        with patch.object(jp, "teardown_job_workers", side_effect=fake_teardown):
            deleted = jp.sweep_stale_job_workers(FakeDB(), r)
        assert deleted == 1
        assert tear_calls == ["pipeline_run_55"]


def test_sweep_drops_leased_fleet_when_deployment_is_gone():
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()
        job = "pipeline_run_3596_node_1"
        jp._register_job_worker(
            r,
            job,
            2,
            jp.fleet_queue_name(job),
            jp.fleet_deployment_name(job),
            company_id=87,
            plan_name="free",
            source_job_name=job,
        )
        jp.refresh_flow_fleet_lease(r, 3596)

        class FakeDB:
            def execute(self, *_a, **_k):
                class R:
                    def fetchall(self_inner):
                        return []
                return R()

        def fake_k8s(method, path, body=None, timeout=10.0):
            if method == "GET":
                return {"_http_status": 404}
            return {}

        deleted = jp.sweep_stale_job_workers(FakeDB(), r, k8s_request=fake_k8s)
        assert deleted == 1
        assert jp.fleet_hash(job) not in r.sets.get(jp.REDIS_ACTIVE_SET_KEY, set())
        assert not jp.flow_fleet_lease_active(r, 3596)


def test_finalize_flow_job_releases_not_tears_down(monkeypatch):
    release_calls = []
    teardown_calls = []

    monkeypatch.setenv("CELERY_JOB_ISOLATION_MODE", "per_job")
    monkeypatch.setenv("PIPELINE_FLOW_FLEET_REUSE_ENABLED", "true")
    import importlib
    import celery_job_worker_pool
    import celery_worker

    importlib.reload(celery_job_worker_pool)
    celery_worker = importlib.reload(celery_worker)

    monkeypatch.setattr(
        celery_worker.job_pool,
        "release_flow_fleet_lease",
        lambda job_name, r=None: release_calls.append(job_name) or {"status": "ok"},
    )
    monkeypatch.setattr(
        celery_worker.job_pool,
        "teardown_job_workers",
        lambda job_name, fast=True, r=None: teardown_calls.append(job_name) or {"status": "ok"},
    )
    monkeypatch.setattr(celery_worker.job_pool, "is_flow_fleet_job", lambda _j: True)
    monkeypatch.setattr(celery_worker, "_cleanup_fanout_stagger", lambda _j: None)
    monkeypatch.setattr(celery_worker, "_enrich_parent_ctx_from_db", lambda *a, **k: {})
    monkeypatch.setattr(celery_worker, "_log_aggregated_completion", lambda **k: None)
    monkeypatch.setattr(celery_worker, "update_job_status", lambda *a, **k: True)
    monkeypatch.setattr(celery_worker, "_get_redis", lambda: FakeRedis())

    celery_worker.finalize_migration_orchestration.run(
        [{"status": "completed"}],
        "pipeline_run_3_node_1",
        1,
        is_listing_path=False,
        parent_ctx={},
    )
    assert release_calls == ["pipeline_run_3_node_1"]
    assert teardown_calls == []


def test_provision_two_pipeline_nodes_reuse_same_deployment():
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()
        created = []
        existing = {"present": False}

        def fake_k8s(method, path, body=None, timeout=10.0):
            created.append((method, path, body))
            if method == "GET":
                if not existing["present"]:
                    return {"_http_status": 404}
                return {
                    "_http_status": 200,
                    "spec": {"replicas": 2},
                    "status": {"readyReplicas": 2, "replicas": 2},
                }
            if method == "POST":
                existing["present"] = True
                return {}
            return {}

        a = jp.provision_job_workers(
            "pipeline_run_77_node_1",
            2,
            r=r,
            plan="standard",
            k8s_request=fake_k8s,
        )
        b = jp.provision_job_workers(
            "pipeline_run_77_node_2",
            1,
            r=r,
            plan="standard",
            k8s_request=fake_k8s,
        )
        assert a["status"] == "ok"
        assert b["status"] == "ok"
        assert a["deployment"] == b["deployment"]
        assert a["queue"] == b["queue"]
        assert a["fleet_key"] == "pipeline_run_77"
        assert sum(1 for c in created if c[0] == "POST") == 1
        assert sum(1 for c in created if c[0] == "PATCH") == 1
        # Stable fleet: second node requested 1 but existing 2 is kept.
        assert b["replicas"] == 2
        meta = json.loads(r.store[jp._meta_key("pipeline_run_77")])
        assert set(meta.get("active_jobs") or []) >= {
            "pipeline_run_77_node_1",
            "pipeline_run_77_node_2",
        }


def test_same_run_nodes_do_not_hit_company_job_cap_with_fleet_reuse():
    """With reuse on, Batch 2 shares the run fleet and must not defer at max_jobs=1."""
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()
        existing = {"present": False}

        def fake_k8s(method, path, body=None, timeout=10.0):
            if method == "GET":
                if not existing["present"]:
                    return {"_http_status": 404}
                return {
                    "_http_status": 200,
                    "spec": {"replicas": 2},
                    "status": {"readyReplicas": 2, "replicas": 2},
                }
            if method == "POST":
                existing["present"] = True
                return {}
            return {}

        cfg = _org_config(91, max_jobs=1, max_pods=4)
        first = jp.provision_job_workers(
            "pipeline_run_8021_node_1",
            2,
            r=r,
            resource_config=cfg,
            k8s_request=fake_k8s,
        )
        second = jp.provision_job_workers(
            "pipeline_run_8021_node_2",
            2,
            r=r,
            resource_config=cfg,
            k8s_request=fake_k8s,
        )
        assert first["status"] == "ok"
        assert second["status"] == "ok"
        assert second.get("reason") != "company_job_cap"
        assert first["fleet_key"] == second["fleet_key"] == "pipeline_run_8021"


def test_same_run_nodes_share_job_slot_when_fleet_reuse_off():
    """Same pipeline run counts as one job even with node-scoped fleets (reuse off)."""
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "false",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()

        def fake_k8s(method, path, body=None, timeout=10.0):
            if method == "GET":
                return {"_http_status": 404}
            if method == "POST":
                return {}
            return {}

        # Free plan: 1 concurrent job, 2 pods for the whole flow.
        cfg = _org_config(92, max_jobs=1, max_pods=2)
        jp._register_job_worker(
            r,
            "pipeline_run_8021_node_1",
            1,
            "q1",
            "dep-node-1",
            company_id=92,
            plan_name="free",
        )
        second = jp.provision_job_workers(
            "pipeline_run_8021_node_2",
            1,
            r=r,
            resource_config=cfg,
            k8s_request=fake_k8s,
        )
        assert second["status"] == "ok"
        assert second.get("reason") != "company_job_cap"


def test_other_run_still_hits_company_job_cap_when_reuse_off():
    """A different pipeline run must still respect Free max_concurrent_jobs=1."""
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "false",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()

        def fake_k8s(method, path, body=None, timeout=10.0):
            if method == "GET":
                return {"status": {"readyReplicas": 1}}
            return {}

        cfg = _org_config(93, max_jobs=1, max_pods=2)
        jp._register_job_worker(
            r,
            "pipeline_run_8021_node_1",
            1,
            "q1",
            "dep-8021",
            company_id=93,
            plan_name="free",
        )
        blocked = jp.provision_job_workers(
            "pipeline_run_9000_node_1",
            1,
            r=r,
            resource_config=cfg,
            k8s_request=fake_k8s,
        )
        assert blocked["status"] == "deferred"
        assert blocked["reason"] == "company_job_cap"


def test_same_run_exceeding_pod_ceiling_hits_company_pod_cap():
    """Batches of one Free flow may share the job slot but not exceed max_pods."""
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "false",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()

        def fake_k8s(method, path, body=None, timeout=10.0):
            if method == "GET":
                return {"status": {"readyReplicas": 2}}
            return {}

        cfg = _org_config(94, max_jobs=1, max_pods=2)
        jp._register_job_worker(
            r,
            "pipeline_run_8021_node_1",
            2,
            "q1",
            "dep-node-1",
            company_id=94,
            plan_name="free",
        )
        blocked = jp.provision_job_workers(
            "pipeline_run_8021_node_2",
            1,
            r=r,
            resource_config=cfg,
            k8s_request=fake_k8s,
        )
        assert blocked["status"] == "deferred"
        assert blocked["reason"] == "company_pod_cap"


def test_fleet_key_override_shares_run_fleet_when_reuse_off():
    """Explicit fleet_key from Django forces a shared run fleet despite reuse=off."""
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "false",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()
        created = []

        def fake_k8s(method, path, body=None, timeout=10.0):
            created.append((method, path))
            if method == "GET":
                if any(m == "POST" for m, _ in created[:-1]):
                    return {
                        "_http_status": 200,
                        "spec": {"replicas": 1},
                        "status": {"readyReplicas": 1, "replicas": 1},
                    }
                return {"_http_status": 404}
            return {}

        cfg = _org_config(95, max_jobs=1, max_pods=2)
        first = jp.provision_job_workers(
            "pipeline_run_55_node_1",
            1,
            r=r,
            resource_config=cfg,
            k8s_request=fake_k8s,
            fleet_key="pipeline_run_55",
        )
        second = jp.provision_job_workers(
            "pipeline_run_55_node_2",
            1,
            r=r,
            resource_config=cfg,
            k8s_request=fake_k8s,
            fleet_key="pipeline_run_55",
        )
        assert first["status"] == "ok"
        assert second["status"] == "ok"
        assert first["fleet_key"] == second["fleet_key"] == "pipeline_run_55"
        assert first["deployment"] == second["deployment"]


def test_provision_parallel_pipeline_nodes_post_409_reuses_deployment():
    """Both nodes GET 404 then race POST; second must PATCH instead of failing."""
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
            "CELERY_CLUSTER_MAX_JOB_WORKER_PODS": "20",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        jp = importlib.reload(jp)
        r = FakeRedis()
        calls = []
        post_count = {"n": 0}
        stale_reads = {"remaining": 1}  # node 2 GETs before deployment is visible

        def fake_k8s(method, path, body=None, timeout=10.0):
            calls.append((method, path, body))
            if method == "GET":
                if post_count["n"] == 0:
                    return {"_http_status": 404}
                if stale_reads["remaining"] > 0:
                    stale_reads["remaining"] -= 1
                    return {"_http_status": 404}
                return {
                    "_http_status": 200,
                    "spec": {"replicas": 6},
                    "status": {"readyReplicas": 2, "replicas": 6},
                }
            if method == "POST":
                post_count["n"] += 1
                if post_count["n"] > 1:
                    return {
                        "_http_status": 409,
                        "_error": 'deployments.apps "celery-worker-flow-x" already exists',
                    }
                return {}
            if method == "PATCH":
                return {}
            return {}

        a = jp.provision_job_workers(
            "pipeline_run_88_node_1",
            6,
            r=r,
            plan="enterprise",
            k8s_request=fake_k8s,
        )
        b = jp.provision_job_workers(
            "pipeline_run_88_node_2",
            2,
            r=r,
            plan="enterprise",
            k8s_request=fake_k8s,
        )
        assert a["status"] == "ok"
        assert b["status"] == "ok"
        assert a["deployment"] == b["deployment"]
        assert b["action"] == "patched_after_409"
        assert b["replicas"] == 6
        assert sum(1 for c in calls if c[0] == "POST") == 2
        assert sum(1 for c in calls if c[0] == "PATCH") == 1


def _org_config(company_id, max_jobs=1, max_pods=4, priority=50):
    return rc.EffectiveResourceConfig(
        plan_name="premium",
        max_pods=max_pods,
        concurrency=4,
        effective_concurrency=4,
        max_chunk_size=100000,
        max_inflight_tables=16,
        cpu_request="1",
        cpu_limit="1",
        memory_request="1Gi",
        memory_limit="2Gi",
        memory_limit_bytes=2 * 1024 ** 3,
        priority=priority,
        max_concurrent_jobs=max_jobs,
        company_id=company_id,
    )


def test_company_job_cap_does_not_block_other_organisation():
    r = FakeRedis()
    calls = []
    org_a_dep = pool.fleet_deployment_name("org_a_flow")

    def fake_k8s(method, path, body=None, timeout=10.0):
        calls.append(method)
        if method == "GET":
            # Keep org A's seeded fleet alive for cap accounting.
            if path.rstrip("/").endswith(org_a_dep):
                return {
                    "_http_status": 200,
                    "status": {"readyReplicas": 4, "replicas": 4},
                }
            return {"_http_status": 404}
        if method == "POST":
            return {}
        return {}

    pool._register_job_worker(
        r, "org_a_flow", 4, "q", "dep-a", company_id=7, plan_name="premium",
    )
    blocked = pool.provision_job_workers(
        "org_a_flow_2",
        4,
        r=r,
        resource_config=_org_config(7, max_jobs=1, max_pods=4),
        k8s_request=fake_k8s,
    )
    allowed = pool.provision_job_workers(
        "org_b_flow",
        4,
        r=r,
        resource_config=_org_config(8, max_jobs=1, max_pods=4),
        k8s_request=fake_k8s,
    )

    assert blocked["status"] == "deferred"
    assert blocked["reason"] == "company_job_cap"
    assert allowed["status"] == "ok"
    assert calls.count("POST") == 1


def test_company_job_cap_ignores_deleted_deployment():
    r = FakeRedis()
    pool._register_job_worker(
        r, "org_a_flow", 2, "q", "dep-a", company_id=87, plan_name="free",
    )

    def missing(_method, _path, body=None, timeout=10.0):
        return {"_http_status": 404}

    reason = pool.company_provision_defer_reason(
        r,
        "pipeline_run_3814_node_305",
        2,
        _org_config(87, max_jobs=1),
        k8s_request=missing,
    )
    assert reason is None
    assert pool.job_hash("org_a_flow") not in r.sets.get(pool.REDIS_ACTIVE_SET_KEY, set())


def test_company_job_cap_still_counts_live_deployment():
    r = FakeRedis()
    pool._register_job_worker(
        r, "org_a_flow", 2, "q", "dep-a", company_id=87, plan_name="free",
    )

    def present(_method, _path, body=None, timeout=10.0):
        return {"status": {"readyReplicas": 1}}

    reason = pool.company_provision_defer_reason(
        r,
        "pipeline_run_3814_node_305",
        2,
        _org_config(87, max_jobs=1),
        k8s_request=present,
    )
    assert reason == "company_job_cap"


def test_provision_failure_message_reads_flow_fleet_payload():
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_JOB_ISOLATION_MODE": "per_job",
            "PIPELINE_FLOW_FLEET_REUSE_ENABLED": "true",
        },
        clear=False,
    ):
        import celery_job_worker_pool as jp
        import celery_worker
        jp = importlib.reload(jp)
        celery_worker = importlib.reload(celery_worker)
        r = FakeRedis()
        job = "pipeline_run_3814_node_305__DYNAMICS_manual_16486_cashdisc"
        identity = jp.fleet_key_for_job(job)
        r.set(
            f"{jp.PROVISION_PAYLOAD_PREFIX}{identity}",
            json.dumps({"reason": "company_job_cap"}),
        )
        message = celery_worker._provision_failure_message(r, job)
        assert identity == "pipeline_run_3814"
        assert "concurrent job limit" in message


class _FinalizerRedis:
    def __init__(self):
        self.store = {}
        self.counts = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)

    def incr(self, key):
        self.counts[key] = int(self.counts.get(key) or 0) + 1
        return self.counts[key]

    def expire(self, key, ttl):
        return True


def test_finalize_skips_when_another_copy_is_running(monkeypatch):
    import celery_worker

    redis = _FinalizerRedis()
    redis.store["celery:finalizer-run-lock:busy_job"] = "other-worker"
    monkeypatch.setattr(celery_worker, "_get_redis", lambda: redis)
    monkeypatch.setattr(
        celery_worker,
        "_finalize_migration_orchestration_body",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("body should not run")),
    )

    result = celery_worker.finalize_migration_orchestration.run([], "busy_job", 1)

    assert result["status"] == "skipped"
    assert redis.store["celery:finalizer-run-lock:busy_job"] == "other-worker"


def test_finalize_abandons_after_repeated_timeouts(monkeypatch):
    import celery_worker

    redis = _FinalizerRedis()
    redis.counts["celery:finalizer-attempts:poison_job"] = celery_worker.CELERY_FINALIZER_MAX_ATTEMPTS
    monkeypatch.setattr(celery_worker, "_get_redis", lambda: redis)
    deleted = []
    monkeypatch.setattr(celery_worker, "_delete_finalizer_ctx", lambda job: deleted.append(job))
    monkeypatch.setattr(
        celery_worker,
        "_finalize_migration_orchestration_body",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("body should not run")),
    )

    result = celery_worker.finalize_migration_orchestration.run([], "poison_job", 1)

    assert result["status"] == "abandoned"
    assert deleted == ["poison_job"]
    assert "celery:finalizer-run-lock:poison_job" not in redis.store


def test_finalize_is_routed_off_the_orchestrate_queue():
    import importlib
    with patch.dict(
        os.environ,
        {
            "CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE": "migration-control-reconcile",
            "CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE": "migration-control",
        },
        clear=False,
    ):
        import celery_worker
        celery_worker = importlib.reload(celery_worker)
        routes = celery_worker.celery_app.conf.task_routes
        assert routes["celery_worker.finalize_migration_orchestration"]["queue"] == "migration-control-reconcile"
        assert routes["celery_worker.orchestrate_migration"]["queue"] == "migration-control"
        assert celery_worker.CELERY_FINALIZER_TIME_LIMIT == 180
