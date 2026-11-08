#!/usr/bin/env python3
"""Ops: per-job isolation status (Redis registry + optional kubectl)."""
import json
import os
import subprocess
import sys

try:
    import redis
except ImportError:
    print("pip install redis", file=sys.stderr)
    sys.exit(1)

REDIS_URL = os.getenv("REDIS_URL", os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))
NAMESPACE = os.getenv("CELERY_K8S_NAMESPACE", "default")
ACTIVE_KEY = "celery:job-worker:active"


def main():
    r = redis.from_url(REDIS_URL)
    mode = os.getenv("CELERY_JOB_ISOLATION_MODE", "shared")
    print(f"CELERY_JOB_ISOLATION_MODE={mode}")
    members = r.smembers(ACTIVE_KEY) or []
    print(f"active job hashes in Redis: {len(members)}")
    for raw in sorted(members):
        h = raw.decode() if isinstance(raw, bytes) else str(raw)
        meta_raw = r.get(f"celery:job-worker:{h}:meta")
        if meta_raw:
            meta = json.loads(meta_raw)
            print(
                f"  {h} | job={meta.get('job_name')} | replicas={meta.get('replicas')} "
                f"| queue={meta.get('queue')} | deploy={meta.get('deployment_name')}"
            )
        else:
            print(f"  {h} | (no meta)")

    try:
        out = subprocess.check_output(
            [
                "kubectl",
                "get",
                "deploy",
                "-n",
                NAMESPACE,
                "-l",
                "datasyncher.com/managed-by=job-pool",
                "-o",
                "wide",
            ],
            text=True,
            stderr=subprocess.STDOUT,
        )
        print("\n=== kubectl job-pool deployments ===")
        print(out)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"\n(kubectl skipped: {exc})")


if __name__ == "__main__":
    main()
