import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

from celery_worker import (  # noqa: E402
    CELERY_KEDA_ACTIVE_FORCE_PRUNE_LEN,
    CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD,
    CELERY_KEDA_ACTIVE_LIST_KEY,
    _diagnose_pending_child,
    _pending_requeue_skip_reason,
    _repair_inflated_keda_active_list,
    _rebuild_keda_active_list,
)


class FakeRedisList:
    def __init__(self, items=None):
        self.lists = {CELERY_KEDA_ACTIVE_LIST_KEY: list(items or [])}

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]

    def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        removed = 0
        if count == 0:
            while value in items:
                items.remove(value)
                removed += 1
        self.lists[key] = items
        return removed

    def lpush(self, key, *values):
        items = self.lists.setdefault(key, [])
        for value in reversed(values):
            items.insert(0, value)
        return len(items)

    def delete(self, key):
        self.lists.pop(key, None)

    def pipeline(self):
        redis = self
        ops = []

        class Pipe:
            def delete(self, key):
                ops.append(("delete", key))
                return self

            def lpush(self, key, *values):
                ops.append(("lpush", key, values))
                return self

            def execute(self):
                for op in ops:
                    if op[0] == "delete":
                        redis.delete(op[1])
                    elif op[0] == "lpush":
                        redis.lpush(op[1], *op[2])

        return Pipe()


def test_diagnose_pending_child_keda_inflated():
    threshold = CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD
    assert _diagnose_pending_child(5, threshold + 1) == "KEDA_ACTIVE_LIST_INFLATED"
    assert _diagnose_pending_child(5, threshold) == "IN_QUEUE_WAITING_FOR_KEDA_SCALE"


def test_diagnose_pending_child_prefetch_hold():
    assert _diagnose_pending_child(0, 3) == "DEQUEUED_NOT_STARTED_prefetch_hold"
    assert _diagnose_pending_child(0, 0) == "TASK_INVISIBLE_likely_lost"


def test_pending_requeue_skip_reason():
    assert _pending_requeue_skip_reason("DEQUEUED_NOT_STARTED_prefetch_hold") == "prefetch_hold_wait_for_slot"
    assert _pending_requeue_skip_reason("IN_QUEUE_WAITING_FOR_KEDA_SCALE") == "wait_for_keda_scale"
    assert _pending_requeue_skip_reason("KEDA_ACTIVE_LIST_INFLATED") == "wait_for_keda_scale"
    assert _pending_requeue_skip_reason("TASK_INVISIBLE_likely_lost") is None


def test_repair_inflated_keda_active_list_noop_when_small():
    r = FakeRedisList(["tid-1", "tid-2"])
    with patch("celery_worker._get_redis", return_value=r):
        assert _repair_inflated_keda_active_list(set()) == 0
        assert r.llen(CELERY_KEDA_ACTIVE_LIST_KEY) == 2


def test_repair_inflated_keda_active_list_prunes_terminal_ids():
    inflated = [f"tid-{idx}" for idx in range(CELERY_KEDA_ACTIVE_INFLATED_THRESHOLD + 10)]
    r = FakeRedisList(inflated)
    terminal = {"SUCCESS", "FAILURE", "REVOKED"}

    def fake_state(tid):
        return "SUCCESS" if tid.endswith("0") else "PENDING"

    with patch("celery_worker._get_redis", return_value=r):
        with patch("celery_worker.celery_app") as mock_app:
            mock_app.AsyncResult.side_effect = lambda tid: MagicMock(state=fake_state(tid))
            repaired = _repair_inflated_keda_active_list(set())
    assert repaired > 0
    remaining = r.lrange(CELERY_KEDA_ACTIVE_LIST_KEY, 0, -1)
    assert all(fake_state(tid) not in terminal for tid in remaining)


def test_rebuild_keda_active_list_replaces_list():
    r = FakeRedisList([f"stale-{idx}" for idx in range(CELERY_KEDA_ACTIVE_FORCE_PRUNE_LEN)])
    live_ids = {"live-a", "live-b"}
    with patch("celery_worker._get_redis", return_value=r):
        removed = _rebuild_keda_active_list(live_ids, "TEST_REBUILD")
    assert removed > 0
    assert sorted(r.lrange(CELERY_KEDA_ACTIVE_LIST_KEY, 0, -1)) == ["live-a", "live-b"]


def test_repair_inflated_keda_active_list_force_rebuild_with_live_ids():
    inflated = [f"tid-{idx}" for idx in range(CELERY_KEDA_ACTIVE_FORCE_PRUNE_LEN)]
    r = FakeRedisList(inflated)
    live_ids = {"keep-1", "keep-2"}

    with patch("celery_worker._get_redis", return_value=r):
        with patch("celery_worker.celery_app") as mock_app:
            mock_app.AsyncResult.return_value = MagicMock(state="PENDING")
            _repair_inflated_keda_active_list(live_ids)
    assert sorted(r.lrange(CELERY_KEDA_ACTIVE_LIST_KEY, 0, -1)) == ["keep-1", "keep-2"]
