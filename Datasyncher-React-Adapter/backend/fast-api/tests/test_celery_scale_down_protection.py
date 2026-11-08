import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

os.environ.setdefault("CELERY_SCALE_DOWN_PROTECTION_ENABLED", "true")

import celery_scale_down_protection as sdp  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = str(value)

    def delete(self, key):
        self.store.pop(key, None)

    def incr(self, key):
        current = int(self.store.get(key) or 0)
        self.store[key] = str(current + 1)
        return current + 1

    def decr(self, key):
        current = int(self.store.get(key) or 0)
        new_val = max(0, current - 1)
        self.store[key] = str(new_val)
        return new_val

    def expire(self, key, _ex):
        return True

    def scan_iter(self, match=None):
        import fnmatch

        for key in list(self.store.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


def test_deletion_cost_for_active_count():
    assert sdp.deletion_cost_for_active_count(0) == sdp.CELERY_POD_DELETION_COST_IDLE
    assert sdp.deletion_cost_for_active_count(2) == sdp.CELERY_POD_DELETION_COST_BUSY


def test_increment_decrement_never_negative():
    r = FakeRedis()
    with patch.object(sdp, "current_pod_name", return_value="worker-test"):
        with patch.object(sdp, "sync_deletion_cost_from_redis"):
            assert sdp.increment_active_tasks(r) == 1
            assert sdp.increment_active_tasks(r) == 2
            sdp.decrement_active_tasks(r)
            sdp.decrement_active_tasks(r)
            sdp.decrement_active_tasks(r)
            assert sdp.get_active_task_count(r) == 0


def test_sync_deletion_cost_patches_only_on_change():
    r = FakeRedis()
    patches = []

    def fake_patch(pod, key, value):
        patches.append((pod, key, value))
        return True

    with patch.object(sdp, "current_pod_name", return_value="worker-abc"):
        sdp.sync_deletion_cost_from_redis(r, k8s_patch_fn=fake_patch)
        with patch.object(sdp, "sync_deletion_cost_from_redis") as mock_sync:
            sdp.increment_active_tasks(r)
            mock_sync.assert_called_once()
        sdp.sync_deletion_cost_from_redis(r, k8s_patch_fn=fake_patch)
    assert patches == [
        ("worker-abc", sdp.POD_DELETION_COST_ANNOTATION, "0"),
        ("worker-abc", sdp.POD_DELETION_COST_ANNOTATION, "100"),
    ]


def test_task_prerun_and_postrun_track_migration_tasks_only():
    r = FakeRedis()
    with patch.object(sdp, "current_pod_name", return_value="worker-xyz"):
        with patch.object(sdp, "sync_deletion_cost_from_redis"):
            sdp.on_task_prerun(r, task_id="tid-1", task_name="celery_worker.call_function_child")
            assert sdp.get_active_task_count(r) == 1
            assert r.get(sdp.task_heartbeat_key("tid-1"))
            sdp.on_task_postrun(
                r,
                task_id="tid-1",
                task_name="celery_worker.call_function_child",
                state="SUCCESS",
            )
            assert sdp.get_active_task_count(r) == 0
        sdp.on_task_prerun(r, task_id="tid-2", task_name="celery_worker.check_job_status")
        assert sdp.get_active_task_count(r) == 0


def test_detect_stale_tasks_marks_orphan():
    r = FakeRedis()
    old = (datetime.now(timezone.utc) - timedelta(seconds=2000)).isoformat()
    payload = json.dumps(
        {"task_id": "stale-1", "pod_name": "worker-old", "updated_at": old}
    )
    r.set(sdp.task_heartbeat_key("stale-1"), payload)
    with patch.object(sdp, "sync_deletion_cost_from_redis"):
        orphans = sdp.detect_and_mark_stale_tasks(r, stale_after_seconds=1200)
    assert len(orphans) == 1
    assert orphans[0]["task_id"] == "stale-1"
    assert r.get(sdp.task_orphaned_key("stale-1"))
    assert r.get(sdp.task_heartbeat_key("stale-1")) is None


def test_detect_stale_tasks_caps_scan_per_tick():
    r = FakeRedis()
    old = (datetime.now(timezone.utc) - timedelta(seconds=2000)).isoformat()
    for idx in range(5):
        r.set(
            sdp.task_heartbeat_key(f"stale-{idx}"),
            json.dumps(
                {
                    "task_id": f"stale-{idx}",
                    "pod_name": "worker-old",
                    "updated_at": old,
                }
            ),
        )
    with patch.object(sdp, "sync_deletion_cost_from_redis"):
        orphans = sdp.detect_and_mark_stale_tasks(r, stale_after_seconds=1200, max_keys=2)
    assert len(orphans) == 2


def test_enter_worker_drain_sets_flag_and_redis():
    r = FakeRedis()
    app = MagicMock()
    with patch.object(sdp, "current_pod_name", return_value="worker-drain"):
        sdp.enter_worker_drain(r, celery_app=app, migration_queue="migration")
        assert sdp.is_draining() is True
        assert r.get(sdp.pod_drain_key("worker-drain")) == "1"
        app.control.cancel_consumer.assert_called_once()


def test_count_busy_pods_ignores_idle_and_zero():
    r = FakeRedis()
    r.set(sdp.pod_active_tasks_key("idle-a"), "0")
    r.set(sdp.pod_active_tasks_key("busy-b"), "1")
    r.set(sdp.pod_active_tasks_key("busy-c"), "2")
    assert sdp.count_busy_pods(r) == 2


def test_floor_desired_replicas_by_busy_pods_raises_target():
    r = FakeRedis()
    r.set(sdp.pod_active_tasks_key("busy-1"), "1")
    r.set(sdp.pod_active_tasks_key("busy-2"), "1")
    assert sdp.floor_desired_replicas_by_busy_pods(1, r) == 2


def test_floor_desired_replicas_by_busy_pods_no_change_when_workload_higher():
    r = FakeRedis()
    r.set(sdp.pod_active_tasks_key("busy-1"), "1")
    assert sdp.floor_desired_replicas_by_busy_pods(4, r) == 4


def test_count_tracked_tasks_from_inspect_maps_active_and_reserved():
    active = {
        "celery@celery-worker-a": [
            {"name": "celery_worker.call_function_child", "id": "t1"},
            {"name": "celery_worker.check_job_status", "id": "t2"},
        ],
        "celery@celery-worker-b": [
            {"name": "celery_worker.orchestrate_migration", "id": "t3"},
        ],
    }
    reserved = {
        "celery@celery-worker-a": [
            {"name": "celery_worker.call_function_child", "id": "t4"},
        ],
    }
    counts = sdp.count_tracked_tasks_from_inspect_maps(active, reserved)
    assert counts == {"celery-worker-a": 2, "celery-worker-b": 1}


def test_reconcile_pod_active_tasks_raises_redis_and_patches_cost():
    r = FakeRedis()
    patches = []

    def fake_patch(pod, key, value):
        patches.append((pod, value))
        return True

    inspect_active = {
        "celery@celery-worker-busy": [
            {"name": "celery_worker.call_function_child", "id": "t1"},
            {"name": "celery_worker.call_function_child", "id": "t2"},
        ],
        "celery@celery-worker-idle": [],
    }
    with patch.object(sdp, "current_pod_name", return_value="celery-worker-local"):
        summary = sdp.reconcile_pod_active_tasks_from_inspect(
            r,
            celery_app=MagicMock(),
            inspect_active=inspect_active,
            inspect_reserved={},
            k8s_patch_fn=fake_patch,
        )
    assert summary["celery-worker-busy"]["before"] == 0
    assert summary["celery-worker-busy"]["after"] == 2
    assert sdp.get_active_task_count(r, "celery-worker-busy") == 2
    assert ("celery-worker-busy", "100") in patches
    assert sdp.get_active_task_count(r, "celery-worker-idle") == 0
    assert ("celery-worker-idle", "0") in patches


def test_reconcile_pod_active_tasks_lowers_overcounted_redis():
    r = FakeRedis()
    r.set(sdp.pod_active_tasks_key("celery-worker-a"), "3")
    inspect_active = {"celery@celery-worker-a": []}
    with patch.object(sdp, "current_pod_name", return_value="celery-worker-a"):
        summary = sdp.reconcile_pod_active_tasks_from_inspect(
            r,
            celery_app=MagicMock(),
            inspect_active=inspect_active,
            inspect_reserved={},
            k8s_patch_fn=lambda pod, key, value: True,
        )
    assert summary["celery-worker-a"]["before"] == 3
    assert summary["celery-worker-a"]["after"] == 0
    assert sdp.get_active_task_count(r, "celery-worker-a") == 0


def test_reconcile_cleans_stale_redis_for_terminated_pods():
    r = FakeRedis()
    r.set(sdp.pod_active_tasks_key("celery-worker-job-dead-abc"), "0")
    r.set(sdp.pod_drain_key("celery-worker-job-dead-abc"), "1")
    with patch.object(sdp, "current_pod_name", return_value="celery-worker-control"):
        summary = sdp.reconcile_pod_active_tasks_from_inspect(
            r,
            celery_app=MagicMock(),
            inspect_active={},
            inspect_reserved={},
            k8s_patch_fn=lambda pod, key, value: True,
        )
    assert summary["celery-worker-job-dead-abc"]["after"] == 0
    assert sdp.pod_active_tasks_key("celery-worker-job-dead-abc") not in r.store
    assert sdp.pod_drain_key("celery-worker-job-dead-abc") not in r.store


def test_reset_after_fork_replaces_a_held_lock():
    held = sdp._last_deletion_cost_lock
    assert held.acquire(timeout=0) is True
    try:
        assert held.acquire(timeout=0) is False
        sdp.reset_after_fork()
        assert sdp._last_deletion_cost_lock is not held
        assert sdp._last_deletion_cost_lock.acquire(timeout=0) is True
        sdp._last_deletion_cost_lock.release()
    finally:
        if held.locked():
            held.release()


def test_prerun_returns_while_deletion_cost_patch_hangs():
    r = FakeRedis()

    def slow_sync(*_args, **_kwargs):
        time.sleep(0.4)

    with patch.object(sdp, "current_pod_name", return_value="worker-hang"):
        with patch.object(sdp, "sync_deletion_cost_from_redis", side_effect=slow_sync):
            started = time.monotonic()
            sdp.on_task_prerun(
                r,
                task_id="tid-hang",
                task_name="celery_worker.call_function_child",
            )
            elapsed = time.monotonic() - started
    assert elapsed < 0.2
    assert sdp.get_active_task_count(r, "worker-hang") == 1
