"""Liveness check for the Django pipeline Celery worker.

Exits 0 when the pipeline worker answers and no reserved task has sat unstarted
for more than five minutes. A frozen prefork child leaves tasks reserved with
no start time; this probe restarts that pod.
"""
from __future__ import annotations

import json
import os
import sys
import time

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

STUCK_SECONDS = int(os.getenv("PIPELINE_RESERVED_STUCK_SECONDS", "300"))
STATE_PATH = os.getenv(
    "PIPELINE_RESERVED_STATE_PATH",
    "/tmp/pipeline-reserved-unstarted.json",
)


def _pipeline_destination() -> str:
    host = os.getenv("HOSTNAME", "")
    return f"pipeline@{host}" if host else "pipeline@"


def main() -> int:
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        os.getenv("DJANGO_SETTINGS_MODULE", "datasyncher.settings.kubernetes"),
    )
    try:
        from datasyncher.celery_app import app
    except Exception as exc:
        print(f"healthcheck import failed: {exc}", file=sys.stderr)
        return 1

    destination = _pipeline_destination()
    try:
        inspect = app.control.inspect(destination=[destination], timeout=8)
        ping = inspect.ping() or {}
    except Exception as exc:
        print(f"healthcheck ping failed: {exc}", file=sys.stderr)
        return 1
    if not ping:
        print("healthcheck ping: no pipeline worker replied", file=sys.stderr)
        return 1

    try:
        reserved = inspect.reserved() or {}
    except Exception as exc:
        print(f"healthcheck reserved failed: {exc}", file=sys.stderr)
        return 1

    unstarted = []
    for tasks in reserved.values():
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            if task.get("time_start") is None and task.get("id"):
                unstarted.append(str(task["id"]))

    now = time.time()
    previous = {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if isinstance(loaded, dict):
                previous = loaded
    except (OSError, ValueError):
        previous = {}

    current = {}
    for task_id in unstarted:
        current[task_id] = float(previous.get(task_id) or now)
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as handle:
            json.dump(current, handle)
    except OSError as exc:
        print(f"healthcheck state write failed: {exc}", file=sys.stderr)
        return 1

    stuck = [
        task_id
        for task_id, seen_at in current.items()
        if now - seen_at >= STUCK_SECONDS
    ]
    if stuck:
        print(
            f"reserved tasks unstarted for >{STUCK_SECONDS}s: {len(stuck)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
