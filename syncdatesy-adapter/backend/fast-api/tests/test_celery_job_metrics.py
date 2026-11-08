import json
import os
import sys
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value if isinstance(value, str) else str(value)

    def smembers(self, key):
        return self.sets.get(key, set())

    def zcard(self, key):
        return 0


def _sample(registry, metric_substring: str) -> str:
    text = generate_latest(registry).decode("utf-8")
    for line in text.splitlines():
        if metric_substring in line and not line.startswith("#"):
            return line
    return ""


def test_counters_increment_when_enabled():
    import importlib
    import celery_job_metrics as metrics

    registry = CollectorRegistry()
    metrics.init_metrics_for_test(registry)

    metrics.inc_teardown(True)
    metrics.inc_ready_timeout("standard")
    metrics.inc_chunk_event("salesforce", "clamped")
    metrics.inc_oom("abc123def456")
    metrics.inc_provision_deferred("cluster_cap")

    assert 'datasyncher_job_teardown_total{status="success"} 1.0' in _sample(
        registry, "datasyncher_job_teardown_total"
    )
    assert 'datasyncher_job_worker_ready_timeout_total{plan="standard"} 1.0' in _sample(
        registry, "datasyncher_job_worker_ready_timeout_total"
    )
    assert 'datasyncher_chunk_reduced_total{connector="salesforce",reason="clamped"} 1.0' in _sample(
        registry, "datasyncher_chunk_reduced_total"
    )
    assert 'datasyncher_oom_killed_total{job_hash="abc123def456"} 1.0' in _sample(
        registry, "datasyncher_oom_killed_total"
    )
    assert 'datasyncher_provision_deferred_total{reason="cluster_cap"} 1.0' in _sample(
        registry, "datasyncher_provision_deferred_total"
    )

    importlib.reload(metrics)


def test_noop_when_disabled(monkeypatch):
    import importlib
    import celery_job_metrics as metrics

    monkeypatch.setenv("CELERY_PROMETHEUS_METRICS_ENABLED", "false")
    importlib.reload(metrics)

    metrics.inc_teardown(True)
    metrics.inc_ready_timeout("free")
    assert metrics.get_registry() is None
    assert metrics.start_metrics_server() is False

    importlib.reload(metrics)


def test_refresh_cluster_gauges_with_fake_redis(monkeypatch):
    import importlib
    import celery_job_metrics as metrics

    registry = CollectorRegistry()
    metrics.init_metrics_for_test(registry)

    r = FakeRedis()
    job_hash = "deadbeefcafe"
    r.sets["celery:job-worker:active"] = {job_hash}
    r.store[f"celery:job-worker:{job_hash}:meta"] = json.dumps({
        "replicas": 2,
        "company_id": 42,
        "plan_name": "enterprise",
    })

    metrics.refresh_cluster_gauges(r)

    assert 'datasyncher_cluster_worker_pods_used 2.0' in _sample(
        registry, "datasyncher_cluster_worker_pods_used"
    )
    assert 'datasyncher_job_worker_replicas{company_id="42",job_hash="deadbeefcafe",plan="enterprise"} 2.0' in _sample(
        registry, "datasyncher_job_worker_replicas"
    )
    assert 'datasyncher_provision_queue_depth 0.0' in _sample(
        registry, "datasyncher_provision_queue_depth"
    )

    importlib.reload(metrics)


def test_provision_latency_observed():
    import importlib
    import celery_job_metrics as metrics

    registry = CollectorRegistry()
    metrics.init_metrics_for_test(registry)

    metrics.mark_provision_started("job-a")
    metrics.observe_provision_ready("job-a", plan="standard")

    output = generate_latest(registry).decode("utf-8")
    assert "datasyncher_job_provision_latency_seconds_count{plan=\"standard\"} 1.0" in output

    importlib.reload(metrics)
