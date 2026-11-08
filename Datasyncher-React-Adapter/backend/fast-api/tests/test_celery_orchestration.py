import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

# Ensure module import does not fail in test environment.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

from celery_worker import (
    CELERY_ACTIVE_MIGRATION_COUNT_KEY,
    CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS,
    CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS,
    CELERY_KEDA_ACTIVE_SYNC_MAX_IDS,
    CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY,
    CELERY_KEDA_MAX_REPLICAS,
    CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB,
    CELERY_ONE_TABLE_PER_CHILD,
    CELERY_TASK_TIME_LIMIT,
    _build_migration_fanout_groups,
    _compute_desired_migration_pods,
    _desired_pods_from_workload,
    _desired_pods_from_workload_with_plan,
    _fanout_inflight_count,
    _fanout_inflight_key,
    _fanout_pending_key,
    _init_fanout_stagger,
    _is_reverse_etl_to_salesforce_path,
    _is_snowflake_to_salesforce_path,
    _keda_bulk_push_candidates,
    _normalize_salesforce_mapping_entries,
    _parse_pod_events_for_oom,
    _parse_pod_termination_from_k8s_body,
    _pods_for_table_count,
    _plan_pod_cap,
    _requeue_reason_for_pod_death,
    _should_orphan_requeue_assigned_pod,
    _release_fanout_slot,
    _reserve_fanout_publish_slots,
    _set_active_migration_pods,
    _sync_active_migration_count_from_db,
    _split_table_groups,
    _execute_connector,
    celery_app,
)


def test_migration_http_timeout_covers_default_spark_poll():
    assert CELERY_MIGRATION_HTTP_TIMEOUT_SECONDS >= 36 * 3600


def test_broker_visibility_timeout_exceeds_redis_default_and_task_limit():
    assert CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS > 3600
    if CELERY_TASK_TIME_LIMIT is not None:
        assert CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS > CELERY_TASK_TIME_LIMIT
    assert (
        celery_app.conf.broker_transport_options.get("visibility_timeout")
        == CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS
    )


def test_split_table_groups_caps_at_six_groups():
    tables = [f"table_{idx}" for idx in range(1, 21)]
    groups = _split_table_groups(tables, max_groups=6)
    assert len(groups) == 6
    assert sum(len(group) for group in groups) == len(tables)


def test_split_table_groups_preserves_all_tables_for_small_input():
    tables = ["a", "b", "c"]
    groups = _split_table_groups(tables, max_groups=6)
    flattened = [table for group in groups for table in group]
    assert sorted(flattened) == sorted(tables)
    assert len(groups) == 3


def test_normalize_salesforce_mapping_flat_table_names():
    assert _normalize_salesforce_mapping_entries(["Account", "Contact"]) == [
        ["Account", "Account"],
        ["Contact", "Contact"],
    ]


def test_normalize_salesforce_mapping_pairs_unchanged():
    pairs = [["Account", "Account__c"], ["Contact", "Contact"]]
    assert _normalize_salesforce_mapping_entries(pairs) == pairs


def test_build_fanout_groups_snowflake_to_salesforce_child_gets_mapping_pairs(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ONE_TABLE_PER_CHILD", True)
    groups = _build_migration_fanout_groups(
        ["Account", "Contact"],
        function_path="/Salesforce-Snowflake-migration-yield",
    )
    assert groups == [[["Account", "Account"]], [["Contact", "Contact"]]]


def test_build_fanout_groups_salesforce_to_snowflake_keeps_flat_names(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ONE_TABLE_PER_CHILD", True)
    groups = _build_migration_fanout_groups(
        ["Account", "Contact"],
        function_path="/Snowflake-Salesforce-migration-yield",
    )
    assert groups == [["Account"], ["Contact"]]


def test_snowflake_to_salesforce_path_detection():
    assert _is_snowflake_to_salesforce_path("/Salesforce-Snowflake-migration-yield") is True
    assert _is_snowflake_to_salesforce_path("/Snowflake-Salesforce-migration-yield") is False


def test_reverse_etl_to_salesforce_path_detection():
    assert _is_reverse_etl_to_salesforce_path("/Salesforce-Snowflake-migration-yield") is True
    assert _is_reverse_etl_to_salesforce_path("/Salesforce-Databricks-migration-yield") is True
    assert _is_reverse_etl_to_salesforce_path("/Salesforce-DatabricksSandbox-migration-yield") is True
    assert _is_reverse_etl_to_salesforce_path("/Databricks-Salesforce-migration-yield") is False
    assert _is_snowflake_to_salesforce_path("/Salesforce-Databricks-migration-yield") is False


def test_build_fanout_groups_databricks_to_salesforce_uses_mapping_pairs(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ONE_TABLE_PER_CHILD", True)
    groups = _build_migration_fanout_groups(
        ["Account", "Contact"],
        function_path="/Salesforce-Databricks-migration-yield",
    )
    assert groups == [[["Account", "Account"]], [["Contact", "Contact"]]]


def test_dispatch_direct_connector_salesforce_to_snowflake_uses_flat_tables(monkeypatch):
    import importlib

    captured = {}

    class FakeSnowflakeMigration:
        def __init__(self, **kwargs):
            captured["selected_tables"] = kwargs.get("selected_tables")

    async def fake_sync_salesforce_to_snowflake(request, environment):
        captured["service"] = "sync_salesforce_to_snowflake"
        captured["environment"] = environment
        return {"total_rows": 0}

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "models.salesforce_models":
            class Mod:
                SalesforceSnowflakeMigration = FakeSnowflakeMigration
            return Mod
        if name == "services.salesforce.as_source.to_snowflake":
            class Mod:
                sync_salesforce_to_snowflake = staticmethod(fake_sync_salesforce_to_snowflake)
            return Mod
        return real_import_module(name)

    from celery_worker import _dispatch_direct_connector

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    monkeypatch.setattr("celery_worker._execute_async", lambda coro: asyncio.run(coro))

    payload = {
        "source_config": {},
        "destination_config": {},
        "connection_id": 1,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "safety_interval": "120",
        "selected_tables": ["Account", "Contact"],
        "job_name": "test_job",
        "migration_session_id": 1,
        "execution_order": "parallel",
        "chunk_count": 1000,
        "logs_creation": False,
        "connection_name": "SF",
        "company_name": "Co",
    }

    _dispatch_direct_connector("/Snowflake-Salesforce-migration-yield", payload)

    assert captured["service"] == "sync_salesforce_to_snowflake"
    assert captured["selected_tables"] == ["Account", "Contact"]
    assert captured["environment"] == "production"


def test_execute_connector_uses_http_when_direct_disabled(monkeypatch):
    captured = {}

    class DummyResponse:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"status": "completed"}

    def fake_post(url, json, headers, timeout, verify):
        captured["url"] = url
        captured["payload"] = json
        return DummyResponse()

    monkeypatch.setattr("celery_worker.CELERY_DIRECT_CONNECTOR_EXECUTION", False)
    monkeypatch.setattr("celery_worker.requests.post", fake_post)
    body, status_code = _execute_connector("http://dummy", {"job_name": "job1"}, {"Content-Type": "application/json"})
    assert status_code == 200
    assert body["status"] == "completed"
    assert captured["url"] == "http://dummy"


def test_execute_connector_falls_back_to_http_when_direct_fails(monkeypatch):
    class DummyResponse:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"status": "completed"}

    monkeypatch.setattr("celery_worker.CELERY_DIRECT_CONNECTOR_EXECUTION", True)
    monkeypatch.setattr("celery_worker.CELERY_HTTP_FALLBACK_ENABLED", True)
    monkeypatch.setattr(
        "celery_worker._dispatch_direct_connector",
        lambda function_path, payload: (_ for _ in ()).throw(RuntimeError("direct failed")),
    )
    monkeypatch.setattr("celery_worker.requests.post", lambda *args, **kwargs: DummyResponse())
    body, status_code = _execute_connector(
        "http://dummy",
        {"job_name": "job1", "function_path": "/x"},
        {"Content-Type": "application/json"},
    )
    assert status_code == 200
    assert body["status"] == "completed"


def _simulate_slot_based_child_assignment(child_ids, pod_slots):
    """
    Lightweight scheduling simulation:
    assign queued child ids to the next available pod slot.
    """
    remaining = list(child_ids)
    assignments = {pod: [] for pod in pod_slots}
    pod_order = list(pod_slots.keys())
    pod_idx = 0
    while remaining:
        pod = pod_order[pod_idx % len(pod_order)]
        if len(assignments[pod]) < pod_slots[pod]:
            assignments[pod].append(remaining.pop(0))
        pod_idx += 1
        # Safety for malformed test inputs with zero capacity.
        if pod_idx > 1000:
            raise RuntimeError("Scheduling simulation did not converge")
    return assignments


def test_child_assignment_uses_next_available_pod_after_three_slots():
    """
    Scenario requested by ops:
    - 6 child groups produced by fan-out
    - pod-a has concurrency/slots=3
    - pod-b has concurrency/slots=3
    Expected:
    - First pod can take up to 3 children
    - Remaining children are assigned to next pod (not dropped)
    """
    child_ids = [f"child-{idx}" for idx in range(1, 7)]
    assignments = _simulate_slot_based_child_assignment(
        child_ids,
        {"pod-a": 3, "pod-b": 3},
    )

    assert len(assignments["pod-a"]) == 3
    assert len(assignments["pod-b"]) == 3
    assert sorted(assignments["pod-a"] + assignments["pod-b"]) == sorted(child_ids)


def test_build_one_table_groups(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ONE_TABLE_PER_CHILD", True)
    tables = [f"table_{idx}" for idx in range(1, 76)]
    groups = _build_migration_fanout_groups(tables, is_listing_path=False)
    assert len(groups) == 75
    assert all(len(group) == 1 for group in groups)
    flattened = [group[0] for group in groups]
    assert flattened == tables


def test_legacy_fanout_groups_when_one_table_disabled(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ONE_TABLE_PER_CHILD", False)
    tables = [f"table_{idx}" for idx in range(1, 21)]
    groups = _build_migration_fanout_groups(tables, is_listing_path=False)
    assert len(groups) == 6
    assert sum(len(group) for group in groups) == len(tables)


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}
        self.sets = {}

    @staticmethod
    def _key(key):
        return key.decode("utf-8") if isinstance(key, bytes) else str(key)

    def type(self, key):
        k = self._key(key)
        if k in self.sets:
            return b"set"
        if k in self.values:
            return b"string"
        if k in self.lists:
            return b"list"
        return b"none"

    def get(self, key):
        return self.values.get(self._key(key))

    def set(self, key, value, ex=None):
        self.values[self._key(key)] = str(value)

    def delete(self, *keys):
        for key in keys:
            k = self._key(key)
            self.values.pop(k, None)
            self.lists.pop(k, None)
            self.sets.pop(k, None)

    def exists(self, key):
        k = self._key(key)
        return int(k in self.values or k in self.lists or k in self.sets)

    def rpush(self, key, *values):
        k = self._key(key)
        bucket = self.lists.setdefault(k, [])
        for value in values:
            bucket.append(self._key(value))

    def lpush(self, key, *values):
        k = self._key(key)
        bucket = self.lists.setdefault(k, [])
        for value in values:
            bucket.insert(0, self._key(value))

    def lpop(self, key):
        k = self._key(key)
        bucket = self.lists.get(k, [])
        if not bucket:
            return None
        return bucket.pop(0)

    def llen(self, key):
        return len(self.lists.get(self._key(key), []))

    def sadd(self, key, *values):
        k = self._key(key)
        bucket = self.sets.setdefault(k, set())
        added = 0
        for value in values:
            if self._key(value) not in bucket:
                bucket.add(self._key(value))
                added += 1
        return added

    def srem(self, key, *values):
        k = self._key(key)
        bucket = self.sets.setdefault(k, set())
        removed = 0
        for value in values:
            if self._key(value) in bucket:
                bucket.remove(self._key(value))
                removed += 1
        return removed

    def scard(self, key):
        return len(self.sets.get(self._key(key), set()))

    def expire(self, key, ttl):
        return True

    def scan_iter(self, match=None):
        import fnmatch

        for key in list(self.values.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    def eval(self, script, numkeys, pending_key, inflight_key, max_inflight, ttl):
        reserved = []
        limit = int(max_inflight)
        while self.scard(inflight_key) < limit:
            child_id = self.lpop(pending_key)
            if not child_id:
                break
            self.sadd(inflight_key, child_id)
            reserved.append(child_id)
        return reserved


def test_set_active_migration_pods_floors_by_busy_pods(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "jobs")
    fake = _FakeRedis()
    fake.set("celery:pod:busy-worker-1:active_tasks", "1")
    fake.set("celery:pod:busy-worker-2:active_tasks", "1")
    assert _set_active_migration_pods(fake, 1) == 2
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 2


def test_set_active_migration_pods_maps_jobs_to_replica_count(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "jobs")
    fake = _FakeRedis()
    assert _set_active_migration_pods(fake, 0) == 0
    assert fake.get(CELERY_ACTIVE_MIGRATION_COUNT_KEY) == "0"
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 0
    assert _set_active_migration_pods(fake, 1) == 2
    assert fake.get(CELERY_ACTIVE_MIGRATION_COUNT_KEY) == "2"
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 2
    assert _set_active_migration_pods(fake, 2) == 4
    assert fake.get(CELERY_ACTIVE_MIGRATION_COUNT_KEY) == "4"
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 4
    assert _set_active_migration_pods(fake, 3) == CELERY_KEDA_MAX_REPLICAS
    assert fake.get(CELERY_ACTIVE_MIGRATION_COUNT_KEY) == str(CELERY_KEDA_MAX_REPLICAS)
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == CELERY_KEDA_MAX_REPLICAS


def test_pods_for_table_count():
    assert _pods_for_table_count(0) == 0
    assert _pods_for_table_count(1) == 1
    assert _pods_for_table_count(3) == 1
    assert _pods_for_table_count(4) == 2
    assert _pods_for_table_count(50) == CELERY_KEDA_MAX_REPLICAS


def test_desired_pods_from_workload():
    assert _desired_pods_from_workload({}) == 0
    assert _desired_pods_from_workload({"job_a": 1}) == 1
    assert _desired_pods_from_workload({"job_a": 1, "job_b": 1}) == 2
    assert _desired_pods_from_workload({"job_a": 50, "job_b": 50}) == CELERY_KEDA_MAX_REPLICAS


def test_plan_pod_cap_mapping():
    assert _plan_pod_cap("Free") == 2
    assert _plan_pod_cap("free") == 2
    assert _plan_pod_cap("Standard") == 2
    assert _plan_pod_cap("Premium") == 4
    assert _plan_pod_cap("Enterprise") == 6
    # Existing tenant plan is still treated like Standard.
    assert _plan_pod_cap("Discounted") == 2
    assert _plan_pod_cap("Free", pod_cap=3) == 3
    assert _plan_pod_cap("Free", pod_cap=0) == 2  # invalid override → plan default


def test_desired_pods_from_workload_with_plan_caps():
    table_counts = {"job_free": 30, "job_standard": 30, "job_enterprise": 30}
    plans = {"job_free": "Free", "job_standard": "Standard", "job_enterprise": "Enterprise"}
    # Base is capped by cluster max; then plan cap applies per job.
    # Free gets 2, Standard gets 2, Enterprise gets 6, then cluster max applies.
    assert _desired_pods_from_workload_with_plan(table_counts, plans) == CELERY_KEDA_MAX_REPLICAS


def test_compute_desired_migration_pods_workload_mode(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "workload")
    assert _compute_desired_migration_pods(1, {"job": 1}) == 1
    assert _compute_desired_migration_pods(1, {"job": 4}) == 2
    # Strict workload mode: no jobs×N hybrid floor.
    assert _compute_desired_migration_pods(1, {"job": 3}) == 1
    assert _compute_desired_migration_pods(2, {"a": 1, "b": 1}) == 2
    capped = _compute_desired_migration_pods(2, {"a": 50, "b": 50})
    assert capped == CELERY_KEDA_MAX_REPLICAS


def test_set_active_migration_pods_workload_mode(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "workload")
    fake = _FakeRedis()
    assert _set_active_migration_pods(fake, 1, job_table_counts={"job": 1}) == 1
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 1
    assert _set_active_migration_pods(fake, 1, job_table_counts={"job": 4}) == 2
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 2


def test_set_active_migration_pods_workload_mode_plan_cap(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "workload")
    fake = _FakeRedis()
    desired = _set_active_migration_pods(
        fake,
        1,
        job_table_counts={"job": 20},
        job_plans={"job": "Free"},
    )
    # Free plan max_pods=2; ceil(20/3)=7 capped to 2
    assert desired == 2
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 2


def test_sync_active_migration_count_from_db_workload_plan_cap(monkeypatch):
    """Reconcile path uses DB workload + plan + pod_cap via _sync_active_migration_count_from_db."""
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "workload")
    fake = _FakeRedis()
    monkeypatch.setattr(
        "celery_worker._count_in_progress_job_workload_and_plan",
        lambda db: {
            # pod_cap comes from plans/company join (Enterprise plan pod_count = 3).
            "job_large": {"table_count": 27, "plan": "Enterprise", "pod_cap": 3},
            "job_small": {"table_count": 1, "plan": "Enterprise", "pod_cap": 3},
        },
    )
    desired = _sync_active_migration_count_from_db(fake, db=None)
    # job_large: min(ceil(27/3), pod_cap=3) = 3; job_small: 1; cluster max 4.
    assert desired == 4
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 4


def test_sync_active_migration_count_from_db_workload_yaml_pod_cap(monkeypatch):
    """CELERY_KEDA_MAX_PODS_PER_JOB=2 style cap from DB limits per-job pods."""
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "workload")
    fake = _FakeRedis()
    monkeypatch.setattr(
        "celery_worker._count_in_progress_job_workload_and_plan",
        lambda db: {
            "job_large": {"table_count": 27, "plan": "Enterprise", "pod_cap": 2},
            "job_small": {"table_count": 1, "plan": "Enterprise", "pod_cap": 2},
        },
    )
    desired = _sync_active_migration_count_from_db(fake, db=None)
    # job_large: min(4, pod_cap=2) = 2; job_small: 1; total = 3.
    assert desired == 3
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 3


def test_sync_active_migration_count_from_db_single_table_enterprise(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_KEDA_SCALE_MODE", "workload")
    fake = _FakeRedis()
    monkeypatch.setattr(
        "celery_worker._count_in_progress_job_workload_and_plan",
        lambda db: {"job_one": {"table_count": 1, "plan": "Enterprise"}},
    )
    desired = _sync_active_migration_count_from_db(fake, db=None)
    assert desired == 1
    assert fake.llen(CELERY_KEDA_DESIRED_REPLICAS_LIST_KEY) == 1


def test_keda_bulk_push_candidates_filters_missing_and_caps():
    live = ["task-a", "task-b", "task-c"]
    redis_ids = {"task-a"}
    assert _keda_bulk_push_candidates(live, redis_ids) == ["task-b", "task-c"]
    assert _keda_bulk_push_candidates([], redis_ids) == []
    assert _keda_bulk_push_candidates(live, {"task-a", "task-b", "task-c"}) == []

    many = [f"task-{idx}" for idx in range(40)]
    capped = _keda_bulk_push_candidates(many, set(), max_ids=5)
    assert len(capped) == 5
    assert capped == sorted(many)[:5]
    assert CELERY_KEDA_ACTIVE_SYNC_MAX_IDS >= 32


def test_refill_fanout_respects_max_inflight(monkeypatch):
    fake = _FakeRedis()
    job_name = "job-stagger-test"
    child_ids = [f"child-{idx}" for idx in range(1, 11)]
    monkeypatch.setattr("celery_worker._get_redis", lambda: fake)
    monkeypatch.setattr(
        "celery_worker._publish_child_task",
        lambda child_id, parent_task_id=None: True,
    )

    published = _init_fanout_stagger(job_name, child_ids, max_inflight=6)

    assert published == 6
    assert fake.llen(_fanout_pending_key(job_name)) == 4
    assert _fanout_inflight_count(job_name) == 6


def test_refill_after_complete_publishes_next_child(monkeypatch):
    fake = _FakeRedis()
    job_name = "job-stagger-refill"
    child_ids = [f"child-{idx}" for idx in range(1, 8)]
    published_ids = []
    monkeypatch.setattr("celery_worker._get_redis", lambda: fake)
    monkeypatch.setattr(
        "celery_worker._publish_child_task",
        lambda child_id, parent_task_id=None: published_ids.append(child_id) or True,
    )

    _init_fanout_stagger(job_name, child_ids, max_inflight=6)
    assert len(published_ids) == 6

    _release_fanout_slot(job_name, child_ids[0])

    assert len(published_ids) == 7
    assert fake.llen(_fanout_pending_key(job_name)) == 0
    assert _fanout_inflight_count(job_name) == CELERY_MAX_INFLIGHT_CHILDREN_PER_JOB


def test_reserve_fanout_publish_slots_returns_child_ids_in_order(monkeypatch):
    fake = _FakeRedis()
    job_name = "job-reserve-order"
    monkeypatch.setattr("celery_worker._get_redis", lambda: fake)
    fake.rpush(_fanout_pending_key(job_name), "child-a", "child-b", "child-c")

    reserved = _reserve_fanout_publish_slots(job_name, max_inflight=2)

    assert reserved == ["child-a", "child-b"]
    assert fake.llen(_fanout_pending_key(job_name)) == 1
    assert _fanout_inflight_count(job_name) == 2


def test_parse_pod_termination_detects_oom_killed():
    body = {
        "status": {
            "containerStatuses": [
                {
                    "name": "celery",
                    "lastState": {
                        "terminated": {
                            "reason": "OOMKilled",
                            "exitCode": 137,
                        }
                    },
                }
            ]
        }
    }
    parsed = _parse_pod_termination_from_k8s_body(body)
    assert parsed["oom_killed"] is True
    assert parsed["termination_reason"] == "OOMKilled"
    assert parsed["exit_code"] == 137


def test_parse_pod_events_for_oom_from_k8s_events():
    events = {
        "items": [
            {
                "reason": "OOMKilling",
                "message": "Memory cgroup out of memory: Killed process 1234 (celery)",
            }
        ]
    }
    parsed = _parse_pod_events_for_oom(events)
    assert parsed["oom_killed"] is True
    assert parsed["termination_reason"] == "OOMKilling"


def test_requeue_reason_for_pod_death():
    assert _requeue_reason_for_pod_death({"oom_killed": True}) == "worker_pod_oom_killed"
    assert _requeue_reason_for_pod_death({"termination_reason": "Evicted"}) == "worker_pod_evicted"
    assert _requeue_reason_for_pod_death({"termination_reason": "Completed"}) == "worker_pod_gone"


def test_should_orphan_requeue_skips_running_pod_with_stale_heartbeat(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ORPHAN_REQUEUE_REQUIRE_K8S_CONFIRMATION", True)
    monkeypatch.setattr("celery_worker.CELERY_K8S_POD_STATUS_ENABLED", True)
    monkeypatch.setattr(
        "celery_worker._get_pod_status_snapshot",
        lambda pod: {
            "exists": True,
            "phase": "Running",
            "k8s_available": True,
            "termination_info": {
                "oom_killed": False,
                "termination_reason": "Unknown",
                "exit_code": None,
                "detail": "",
                "source": "pod_status",
            },
        },
    )
    should, reason, _ = _should_orphan_requeue_assigned_pod("celery-worker-6db6fdbfd4-busy")
    assert should is False
    assert reason == "k8s_pod_running"


def test_should_orphan_requeue_confirms_missing_pod(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ORPHAN_REQUEUE_REQUIRE_K8S_CONFIRMATION", True)
    monkeypatch.setattr("celery_worker.CELERY_K8S_POD_STATUS_ENABLED", True)
    monkeypatch.setattr(
        "celery_worker._get_pod_status_snapshot",
        lambda pod: {
            "exists": False,
            "phase": None,
            "k8s_available": True,
            "termination_info": {
                "oom_killed": False,
                "termination_reason": "PodNotFound",
                "exit_code": None,
                "detail": "gone",
                "source": "pod_missing",
            },
        },
    )
    should, reason, info = _should_orphan_requeue_assigned_pod("celery-worker-6db6fdbfd4-dead")
    assert should is True
    assert reason == ""
    assert info["termination_reason"] == "PodNotFound"


def test_should_orphan_requeue_skips_when_k8s_unavailable(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ORPHAN_REQUEUE_REQUIRE_K8S_CONFIRMATION", True)
    monkeypatch.setattr("celery_worker.CELERY_K8S_POD_STATUS_ENABLED", True)
    monkeypatch.setattr(
        "celery_worker._get_pod_status_snapshot",
        lambda pod: {
            "exists": None,
            "phase": None,
            "k8s_available": False,
            "termination_info": {
                "oom_killed": False,
                "termination_reason": "Unknown",
                "exit_code": None,
                "detail": "k8s_api_unavailable",
                "source": "unavailable",
            },
        },
    )
    should, reason, _ = _should_orphan_requeue_assigned_pod("celery-worker-6db6fdbfd4-x")
    assert should is False
    assert reason == "k8s_api_unavailable"


def test_is_monolithic_call_function_payload():
    from celery_worker import _is_monolithic_call_function_payload

    assert _is_monolithic_call_function_payload({"task_kind": "call_function"}) is True
    assert _is_monolithic_call_function_payload({"url": "http://x", "data": {}}) is True
    assert _is_monolithic_call_function_payload({"group_index": 0, "url": "http://x"}) is False


def test_assigned_pod_for_task_prefers_payload():
    from celery_worker import _assigned_pod_for_task

    payload = {"assigned_pod": "celery-worker-abc"}
    assert _assigned_pod_for_task("tid-1", payload) == "celery-worker-abc"


def test_maybe_requeue_orphan_started_child_requeues_dead_pod(monkeypatch):
    from datetime import datetime, timezone
    from celery_worker import _maybe_requeue_orphan_started_child

    class FakeRedis:
        def __init__(self):
            self.store = {}

        def exists(self, key):
            return key in self.store

        def set(self, key, value, ex=None):
            self.store[key] = value

        def delete(self, key):
            self.store.pop(key, None)

    fake_r = FakeRedis()
    now = datetime.now(timezone.utc)
    payload = {
        "url": "http://migration-function-svc:8001/migrate",
        "data": {"selected_tables": ["custtable"]},
        "headers": {"Content-Type": "application/json"},
        "job_name": "job_a",
        "connection_id": 4,
        "group_index": 2,
        "dispatched_at": "2000-01-01T00:00:00+00:00",
        "requeue_count": 0,
        "assigned_pod": "celery-worker-dead",
    }

    monkeypatch.setattr("celery_worker.CELERY_ORPHAN_AUTO_REQUEUE_ENABLED", True)
    monkeypatch.setattr("celery_worker.CELERY_STARTED_CHILD_REQUEUE_ENABLED", True)
    monkeypatch.setattr("celery_worker.CELERY_OOM_ORPHAN_REQUEUE_AFTER_SECONDS", 60)
    monkeypatch.setattr("celery_worker._is_worker_pod_alive", lambda _pod: False)
    monkeypatch.setattr(
        "celery_worker._should_orphan_requeue_assigned_pod",
        lambda _pod: (True, "", {"termination_reason": "PodNotFound"}),
    )
    monkeypatch.setattr("celery_worker._record_worker_pod_death", lambda **_kwargs: None)
    monkeypatch.setattr("celery_worker.celery_app.control.revoke", lambda *a, **k: None)

    class FakeAsyncResult:
        def __init__(self, _tid):
            self.state = "STARTED"

    monkeypatch.setattr("celery_worker.celery_app.AsyncResult", FakeAsyncResult)
    monkeypatch.setattr(
        "celery_worker._requeue_child_task",
        lambda **_kwargs: ("new-task-id", {"group_index": 2}),
    )

    class FakeDb:
        def execute(self, *_args, **_kwargs):
            return None

    new_id = _maybe_requeue_orphan_started_child(
        db=FakeDb(),
        r=fake_r,
        now=now,
        job_name="job_a",
        task_id="old-task-id",
        connection_id=4,
        payload=payload,
        source="test",
    )
    assert new_id == "new-task-id"
    assert fake_r.exists("celery:orphan-requeue-lock:old-task-id")


def test_should_orphan_requeue_confirms_oom_on_running_phase(monkeypatch):
    monkeypatch.setattr("celery_worker.CELERY_ORPHAN_REQUEUE_REQUIRE_K8S_CONFIRMATION", True)
    monkeypatch.setattr("celery_worker.CELERY_K8S_POD_STATUS_ENABLED", True)
    monkeypatch.setattr(
        "celery_worker._get_pod_status_snapshot",
        lambda pod: {
            "exists": True,
            "phase": "Running",
            "k8s_available": True,
            "termination_info": {
                "oom_killed": True,
                "termination_reason": "OOMKilled",
                "exit_code": 137,
                "detail": "container died",
                "source": "pod_status",
            },
        },
    )
    should, reason, info = _should_orphan_requeue_assigned_pod("celery-worker-6db6fdbfd4-oom")
    assert should is True
    assert reason == ""
    assert info["oom_killed"] is True


def test_per_job_isolation_routes_child_tasks_to_job_queue(monkeypatch):
    monkeypatch.setenv("CELERY_JOB_ISOLATION_MODE", "per_job")
    import importlib
    import celery_job_worker_pool
    import celery_worker

    importlib.reload(celery_job_worker_pool)
    cw = importlib.reload(celery_worker)
    assert cw._child_task_queue("job_alpha").startswith("migration:job:")
    assert cw._control_task_queue() == cw.CELERY_MIGRATION_CONTROL_QUEUE


def test_capped_pods_uses_effective_pods_formula_in_per_job_mode(monkeypatch):
    monkeypatch.setenv("CELERY_JOB_ISOLATION_MODE", "per_job")
    import importlib
    import celery_job_worker_pool
    import celery_worker

    importlib.reload(celery_job_worker_pool)
    cw = importlib.reload(celery_worker)
    # Enterprise: 2 tables -> ceil(2/6)=1 pod (not 2)
    assert cw._capped_pods_for_job(2, "enterprise") == 1
    # Free: capped at plan max_pods=2
    assert cw._capped_pods_for_job(10, "free") == 2


def test_should_fail_stale_pending_no_payload():
    import celery_worker as cw

    assert cw._should_fail_stale_pending_no_payload(cw.CELERY_STALE_NO_PAYLOAD_SECONDS + 1) is True
    assert cw._should_fail_stale_pending_no_payload(cw.CELERY_STALE_NO_PAYLOAD_SECONDS) is False
    # Long-run default must exceed 24h so multi-day Dynamics jobs are not killed early.
    assert cw.CELERY_STALE_NO_PAYLOAD_SECONDS >= 86_400
    assert cw.CELERY_CHILD_PAYLOAD_TTL_SECONDS >= 86_400
    assert cw.CELERY_RESULT_EXPIRES_SECONDS >= 86_400


def test_touch_payload_renews_ttl():
    import celery_worker as cw

    class FakeRedis:
        def __init__(self):
            self.keys = {"celery:child-payload:t1": "x"}
            self.expires = {}

        def exists(self, key):
            return 1 if key in self.keys else 0

        def expire(self, key, ttl):
            self.expires[key] = ttl
            return True

    fake = FakeRedis()
    monkey_get = lambda: fake
    original = cw._get_redis
    cw._get_redis = monkey_get
    try:
        cw._touch_payload("t1")
        cw._touch_payload(None)
        cw._touch_payload("missing")
        assert fake.expires[cw._payload_key("t1")] == cw.CELERY_CHILD_PAYLOAD_TTL_SECONDS
        assert cw._payload_key("missing") not in fake.expires
    finally:
        cw._get_redis = original


def test_acquire_check_job_status_running_lock_blocks_second_caller():
    import celery_worker as cw

    class FakeRedis:
        def __init__(self):
            self.store = {}

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.store:
                return False
            self.store[key] = value
            return True

        def delete(self, key):
            self.store.pop(key, None)

    r = FakeRedis()
    assert cw._acquire_check_job_status_running_lock(r) is True
    assert cw._acquire_check_job_status_running_lock(r) is False
    cw._release_check_job_status_running_lock(r)
    assert cw._acquire_check_job_status_running_lock(r) is True


def test_reconcile_max_jobs_default_is_positive():
    import celery_worker as cw

    assert cw.CELERY_RECONCILE_MAX_JOBS >= 1


def test_schedule_check_job_status_respects_schedule_lock(monkeypatch):
    import celery_worker as cw

    scheduled = []

    class FakeRedis:
        def __init__(self):
            self.locked = False

        def set(self, key, value, nx=False, ex=None):
            if nx and self.locked:
                return False
            self.locked = True
            return True

    monkeypatch.setattr(
        cw,
        "check_job_status",
        type("T", (), {"apply_async": staticmethod(lambda **kwargs: scheduled.append(kwargs))})(),
    )
    r = FakeRedis()
    assert cw._schedule_check_job_status(r) is True
    assert cw._schedule_check_job_status(r) is False
    assert len(scheduled) == 1
    assert scheduled[0]["priority"] == cw.CELERY_CHECK_JOB_STATUS_PRIORITY
    assert scheduled[0]["queue"] == cw._resolve_check_job_status_queue()


def test_check_job_status_skips_on_orchestrator_control_role(monkeypatch):
    import celery_worker as cw

    impl_called = []

    monkeypatch.setattr(cw.job_pool, "is_control_worker", lambda: True)
    monkeypatch.setattr(cw.job_pool, "is_reconcile_worker", lambda: False)
    monkeypatch.setattr(cw, "_check_job_status_impl", lambda r: impl_called.append(True))
    monkeypatch.setattr(cw, "_acquire_check_job_status_running_lock", lambda r: True)
    monkeypatch.setattr(cw, "_release_check_job_status_running_lock", lambda r: None)

    cw.check_job_status()
    assert impl_called == []


def test_resolve_check_job_status_queue_prefers_reconcile(monkeypatch):
    import celery_worker as cw

    monkeypatch.setattr(cw, "CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE", "migration-control-reconcile")
    monkeypatch.setattr(cw, "CELERY_MIGRATION_CONTROL_ORCHESTRATE_QUEUE", "migration-control")
    assert cw._resolve_check_job_status_queue() == "migration-control-reconcile"


def test_beat_schedule_targets_reconcile_queue_only():
    import pytest
    import celery_worker as cw

    q = cw.CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE
    if not q:
        pytest.skip("CELERY_MIGRATION_CONTROL_RECONCILE_QUEUE not set in test env")
    entry = cw.celery_app.conf.beat_schedule.get("check-job-status-reconcile-watchdog")
    assert entry is not None
    assert entry["options"]["queue"] == q
    assert entry["task"] == "celery_worker.check_job_status"


def test_check_job_status_skips_when_running_lock_held(monkeypatch):
    import celery_worker as cw

    impl_called = []

    def fake_impl(r):
        impl_called.append(True)

    monkeypatch.setattr(cw.job_pool, "is_control_worker", lambda: False)
    monkeypatch.setattr(cw.job_pool, "is_reconcile_worker", lambda: True)
    monkeypatch.setattr(cw, "_check_job_status_impl", fake_impl)
    monkeypatch.setattr(cw, "_acquire_check_job_status_running_lock", lambda r: False)
    monkeypatch.setattr(cw, "_release_check_job_status_running_lock", lambda r: None)

    cw.check_job_status()
    assert impl_called == []


def test_check_job_status_runs_impl_and_releases_lock(monkeypatch):
    import celery_worker as cw

    released = []
    impl_called = []

    monkeypatch.setattr(cw.job_pool, "is_control_worker", lambda: False)
    monkeypatch.setattr(cw.job_pool, "is_reconcile_worker", lambda: True)
    monkeypatch.setattr(cw, "_check_job_status_impl", lambda r: impl_called.append(True))
    monkeypatch.setattr(cw, "_acquire_check_job_status_running_lock", lambda r: True)
    monkeypatch.setattr(
        cw,
        "_release_check_job_status_running_lock",
        lambda r: released.append(True),
    )
    monkeypatch.setattr(cw, "_get_redis", lambda: MagicMock())

    cw.check_job_status()
    assert impl_called == [True]
    assert released == [True]


def test_orchestrate_and_check_priorities():
    import celery_worker as cw

    # Redis: lower number = higher priority; orchestrate must win over reconcile polls.
    assert cw.CELERY_ORCHESTRATE_TASK_PRIORITY < cw.CELERY_CHECK_JOB_STATUS_PRIORITY


def test_dispatch_direct_connector_covers_all_migration_paths(monkeypatch):
    import importlib

    from celery_worker import _dispatch_direct_connector

    migration_paths = {
        "/Snowflake-MicrosoftDynamics-migration-yield",
        "/Snowflake-MicrosoftDynamics365_FO-migration-yield",
        "/Databricks-MicrosoftDynamics365_FO-migration-yield",
        "/Databricks-Salesforce-migration-yield",
        "/Databricks-SalesforceSandbox-migration-yield",
        "/Salesforce-Snowflake-migration-yield",
        "/Snowflake-Salesforce-migration-yield",
        "/SalesforceSandbox-Snowflake-migration-yield",
        "/Snowflake-SalesforceSandbox-migration-yield",
        "/Snowflake-Snowflake-migration-yield",
        "/Snowflake-GoogleReviews-migration-yield",
        "/Snowflake-AmazonS3-migration-yield",
        "/SFTP-Snowflake-migration-yield",
        "/Snowflake-SFTP-migration-yield",
        "/SharePoint-Snowflake-migration-yield",
        "/AmazonS3-Snowflake-migration-yield",
        "/AzureDataLakeStorage-Snowflake-migration-yield",
        "/Snowflake-AzureDataLakeStorage-migration-yield",
        "/Snowflake-ADLS-migration-yield",
        "/GoogleDrive-Snowflake-migration-yield",
        "/Snowflake-GoogleDrive-migration-yield",
    }

    dispatched = []

    class FakeModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def __getattr__(self, name):
            return None

    def make_service(name):
        def sync_fn(*args, **kwargs):
            dispatched.append(name)
            return {"status": "ok"}

        async def async_fn(*args, **kwargs):
            dispatched.append(name)
            return {"status": "ok"}

        return sync_fn, async_fn

    service_map = {
        "dynamics.as_source.to_snowflake": make_service("dynamics")[1],
        "dynamics.as_source.to_databricks": make_service("dynamics_databricks")[1],
        "salesforce.as_source.to_databricks": make_service("salesforce_databricks")[1],
        "salesforce.as_source.to_snowflake": make_service("salesforce")[1],
        "salesforce.as_destination.from_snowflake": make_service("salesforce")[1],
        "snowflake.as_source.to_snowflake": make_service("snowflake")[0],
        "google_reviews.as_source.to_snowflake": make_service("google_reviews")[0],
        "amazon_s3.service": make_service("amazon_s3")[1],
        "snowflake.as_source.to_s3": make_service("snowflake_to_s3")[0],
        "snowflake.as_source.to_sftp": make_service("snowflake_to_sftp")[0],
        "snowflake.as_source.to_sharepoint": make_service("snowflake_to_sharepoint")[0],
        "snowflake.as_source.to_adls": make_service("snowflake_to_adls")[0],
        "google_drive.as_destination.from_snowflake": make_service("google_drive")[0],
        "sftp.as_source.to_snowflake": make_service("sftp_to_snowflake")[1],
        "adls.as_source.to_snowflake": make_service("adls_to_snowflake")[1],
        "google_drive.as_source.to_snowflake": make_service("googledrive_to_snowflake")[1],
    }
    model_map = {
        "microsoft_dynamics_models": "MigrationRequest",
        "salesforce_models": "SalesforceSnowflakeMigration",
        "snowflake_models": "SnowflakeMigration",
        "google_reviews_models": "GoogleReviewsMigration",
        "amazon_s3_models": "AmazonS3MigrationRequest",
        "snowflake_s3_models": "SnowflakeS3MigrationRequest",
        "snowflake_sftp_models": "SnowflakeSFTPMigrationRequest",
        "snowflake_sharepoint_models": "SnowflakeSharePointMigrationRequest",
        "snowflake_adls_models": "SnowflakeADLSMigrationRequest",
        "google_drive_models": "GoogleDriveMigrationRequest",
        "sftp_snowflake_models": "SFTPSnowflakeMigrationRequest",
        "adls_source_models": "ADLSSnowflakeMigrationRequest",
        "google_drive_snowflake_models": "GoogleDriveSnowflakeMigrationRequest",
    }

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name.startswith("models."):
            mod_name = name.split(".", 1)[1]
            class Mod:
                pass

            setattr(Mod, model_map[mod_name], FakeModel)
            if mod_name == "salesforce_models":
                setattr(Mod, "SalesforceMigration", FakeModel)
                setattr(Mod, "SalesforceDatabricksMigration", FakeModel)
            return Mod
        if name.startswith("services."):
            svc_name = name.split(".", 1)[1]
            class Mod:
                pass

            if svc_name == "dynamics.as_source.to_snowflake":
                setattr(Mod, "migrate", service_map[svc_name])
            elif svc_name == "dynamics.as_source.to_databricks":
                setattr(Mod, "migrate", service_map[svc_name])
            elif svc_name == "salesforce.as_source.to_databricks":
                setattr(Mod, "sync_salesforce_to_databricks", staticmethod(service_map[svc_name]))
            elif svc_name == "salesforce.as_source.to_snowflake":
                setattr(Mod, "sync_salesforce_to_snowflake", staticmethod(service_map[svc_name]))
            elif svc_name == "salesforce.as_destination.from_snowflake":
                setattr(Mod, "sync_snowflake_to_salesforce", staticmethod(service_map[svc_name]))
            elif svc_name == "snowflake.as_source.to_snowflake":
                setattr(Mod, "migrate", service_map[svc_name])
            elif svc_name == "google_reviews.as_source.to_snowflake":
                setattr(Mod, "sync_google_reviews_to_snowflake", service_map[svc_name])
            elif svc_name == "amazon_s3.service":
                class AmazonS3Service:
                    migrate = staticmethod(service_map[svc_name])
                setattr(Mod, "AmazonS3Service", AmazonS3Service)
            elif svc_name == "snowflake.as_source.to_s3":
                setattr(Mod, "migrate_snowflake_to_s3", service_map[svc_name])
            elif svc_name == "snowflake.as_source.to_sftp":
                setattr(Mod, "migrate_snowflake_to_sftp", service_map[svc_name])
            elif svc_name == "snowflake.as_source.to_sharepoint":
                setattr(Mod, "migrate_snowflake_to_sharepoint", service_map[svc_name])
            elif svc_name == "snowflake.as_source.to_adls":
                setattr(Mod, "migrate_snowflake_to_adls", service_map[svc_name])
            elif svc_name == "google_drive.as_destination.from_snowflake":
                setattr(Mod, "sync_snowflake_to_google_drive", service_map[svc_name])
            elif svc_name == "sftp.as_source.to_snowflake":
                class SFTPSnowflakeService:
                    migrate = staticmethod(service_map[svc_name])
                setattr(Mod, "SFTPSnowflakeService", SFTPSnowflakeService)
            elif svc_name == "adls.as_source.to_snowflake":
                class ADLSSnowflakeService:
                    migrate = staticmethod(service_map[svc_name])
                setattr(Mod, "ADLSSnowflakeService", ADLSSnowflakeService)
            elif svc_name == "google_drive.as_source.to_snowflake":
                class GoogleDriveSnowflakeService:
                    migrate = staticmethod(service_map[svc_name])
                setattr(Mod, "GoogleDriveSnowflakeService", GoogleDriveSnowflakeService)
            return Mod
        return real_import_module(name)

    def fake_execute_async(coro):
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)
    monkeypatch.setattr("celery_worker._execute_async", fake_execute_async)

    base_payload = {
        "source_config": {},
        "destination_config": {},
        "connection_id": 1,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "start_date": "2024-01-01",
        "end_date": "2024-01-31",
        "safety_interval": "120",
        "selected_tables": ["t1"],
        "job_name": "test_job",
        "migration_session_id": 1,
        "execution_order": "parallel",
        "chunk_count": 1000,
        "logs_creation": False,
        "connection_name": "conn",
        "company_name": "Co",
    }

    for path in sorted(migration_paths):
        dispatched.clear()
        result = _dispatch_direct_connector(path, dict(base_payload))
        assert result == {"status": "ok"}, path
        assert dispatched, path


def test_child_exec_lock_blocks_duplicate_delivery(monkeypatch):
    from celery_worker import (
        _try_acquire_child_exec_lock,
        _get_child_exec_lock_holder,
        _release_child_exec_lock,
        _mark_child_exec_lock_completed,
        _CHILD_EXEC_LOCK_COMPLETED,
    )

    class FakeRedis:
        def __init__(self):
            self.store = {}

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.store:
                return False
            self.store[key] = value
            return True

        def get(self, key):
            return self.store.get(key)

        def delete(self, key):
            self.store.pop(key, None)

    fake = FakeRedis()
    monkeypatch.setattr("celery_worker._get_redis", lambda: fake)

    assert _try_acquire_child_exec_lock("task-1", "pod-a:1") is True
    assert _try_acquire_child_exec_lock("task-1", "pod-b:2") is False
    assert _get_child_exec_lock_holder("task-1") == "pod-a:1"

    _mark_child_exec_lock_completed("task-1")
    assert _get_child_exec_lock_holder("task-1") == _CHILD_EXEC_LOCK_COMPLETED
    assert _try_acquire_child_exec_lock("task-1", "pod-c:3") is False

    _release_child_exec_lock("task-2", "pod-x:1")
    assert _try_acquire_child_exec_lock("task-2", "pod-x:1") is True
    _release_child_exec_lock("task-2", "pod-x:1")
    assert _try_acquire_child_exec_lock("task-2", "pod-y:2") is True


def test_dispatch_direct_connector_persists_file_source_listing_tables(monkeypatch):
    import importlib

    from celery_worker import _dispatch_direct_connector

    calls = []

    class FakeModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class FakeService:
        @staticmethod
        def list_tables_with_schema(request):
            calls.append(("list", request.connection_id))
            return {"tables": [{"table": "CUSTOMERS"}], "files_count": 1}

        @staticmethod
        def refresh_and_update_sftp_tables_in_database(**kwargs):
            calls.append(("persist_sftp", kwargs))

        @staticmethod
        def refresh_and_update_adls_tables_in_database(**kwargs):
            calls.append(("persist_adls", kwargs))

        @staticmethod
        def refresh_and_update_google_drive_tables_in_database(**kwargs):
            calls.append(("persist_google_drive", kwargs))

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "models.sftp_source_models":
            class Mod:
                SFTPListingTables = FakeModel
            return Mod
        if name == "models.adls_source_models":
            class Mod:
                ADLSListingTables = FakeModel
            return Mod
        if name == "models.google_drive_models":
            class Mod:
                GoogleDriveListingTables = FakeModel
            return Mod
        if name == "services.sftp.source":
            class Mod:
                SFTPSourceService = FakeService
            return Mod
        if name == "services.adls.source":
            class Mod:
                ADLSSourceService = FakeService
            return Mod
        if name == "services.google_drive.as_source.to_snowflake":
            class Mod:
                GoogleDriveSnowflakeService = FakeService
            return Mod
        return real_import_module(name)

    monkeypatch.setattr("celery_worker.importlib.import_module", fake_import_module)

    base_payload = {
        "connection_id": 42,
        "cmp_id": 7,
        "user_id": 8,
        "job_name": "file_source_listing_tables_25296",
        "migration_session_id": 25296,
    }
    cases = (
        ("/SFTP-listing-tables", "SFTP", "persist_sftp"),
        ("/AzureDataLakeStorage-listing-tables", "AzureDataLakeStorage", "persist_adls"),
        ("/ADLS-listing-tables", "ADLS", "persist_adls"),
        ("/GoogleDrive-listing-tables", "GoogleDrive", "persist_google_drive"),
    )

    for path, config_key, persist_marker in cases:
        calls.clear()
        payload = dict(base_payload)
        payload[config_key] = {}

        result = _dispatch_direct_connector(path, payload)

        expected_tables = {"tables": [{"table": "CUSTOMERS"}], "files_count": 1}
        assert result == {"result": expected_tables}
        assert calls[0] == ("list", 42)
        assert calls[1] == (
            persist_marker,
            {
                "connection_id": 42,
                "tables_result": expected_tables,
                "job_name": "file_source_listing_tables_25296",
                "migration_session_id": 25296,
            },
        )


def test_finalize_partial_failure_skips_generic_job_log_before_aggregation(monkeypatch):
    import celery_worker as cw

    fail_open_kwargs = {}

    def _capture_fail_open(*args, **kwargs):
        fail_open_kwargs.update(kwargs)
        return True

    aggregated = {"called": False}

    def _capture_agg(**kwargs):
        aggregated["called"] = True
        aggregated["has_failures"] = kwargs.get("has_failures")

    monkeypatch.setattr(cw, "_fail_open_job_tables", _capture_fail_open)
    monkeypatch.setattr(cw, "_log_aggregated_completion", _capture_agg)
    monkeypatch.setattr(cw, "_enrich_parent_ctx_from_db", lambda *_a, **_k: {"cmp_id": 1, "user_id": 2})
    monkeypatch.setattr(cw, "_maybe_trigger_source_post_missing_file_schema_refresh", lambda *_a, **_k: None)
    monkeypatch.setattr(cw, "_cleanup_fanout_stagger", lambda *_a, **_k: None)
    monkeypatch.setattr(cw, "_teardown_job_workers_if_isolated", lambda *_a, **_k: None)
    monkeypatch.setattr(cw, "_delete_finalizer_ctx", lambda *_a, **_k: None)

    child_results = [
        {
            "status": "partial_failure",
            "successful_tables_list": ["a", "b", "c"],
            "failed_tables_list": ["d"],
            "successful_tables_count": 3,
            "failed_tables_count": 1,
            "total_rows": 10,
            "execution_time": 1.0,
        }
    ]

    result = cw._finalize_migration_orchestration_body(
        child_results,
        "job_partial_test",
        75,
        parent_ctx={"session": "301"},
    )

    assert result["status"] == "failed"
    assert fail_open_kwargs.get("update_job_level_log") is False
    assert aggregated["called"] is True
    assert aggregated["has_failures"] is True


def test_orchestrate_failure_after_defer_does_not_fail_tables(monkeypatch):
    import celery_worker as cw

    failed = {}
    monkeypatch.setattr(
        cw.job_pool,
        "load_awaiting_fanout_state",
        lambda _r, _job: {"child_ids": ["child-1"]},
    )
    monkeypatch.setattr(cw, "_get_redis", lambda: object())
    monkeypatch.setattr(
        cw,
        "update_job_status",
        lambda *args, **kwargs: failed.setdefault("called", True),
    )

    result = cw._fail_task_from_signal(
        "tid-orchestrate",
        "celery_worker.orchestrate_migration",
        ["http://migration", {}, {}, "pipeline_run_5005_node_272", 49],
        {},
        "TimeLimitExceeded(360,)",
    )

    assert result is False
    assert "called" not in failed
