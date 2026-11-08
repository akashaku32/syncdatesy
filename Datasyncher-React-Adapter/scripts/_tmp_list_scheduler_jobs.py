"""Summarize live APScheduler jobs via scheduler-service HTTP API."""
from __future__ import annotations

import json
import urllib.request
from collections import Counter

URL = "http://scheduler-service:5000/jobs/list"

with urllib.request.urlopen(URL, timeout=30) as resp:
    raw = json.loads(resp.read().decode())

jobs = raw if isinstance(raw, list) else raw.get("jobs", raw)
ids = []
for j in jobs:
    if isinstance(j, dict):
        ids.append(j.get("id") or j.get("job_id") or "")
    else:
        ids.append(str(j))

print(f"SCHEDULER_JOB_COUNT={len(ids)}")

prefixes = Counter()
for i in ids:
    s = str(i)
    if s.startswith("pipeline_"):
        prefixes["pipeline_*"] += 1
    elif "_batch_" in s or s.startswith("conn_"):
        prefixes["conn_*_batch_* or conn_*"] += 1
    elif s.startswith("conn_"):
        prefixes["conn_* (connection sync)"] += 1
    else:
        prefixes[s.split("_")[0] if "_" in s else s or "(empty)"] += 1

print("PREFIX_COUNTS=" + json.dumps(dict(prefixes.most_common(20))))

legacy_batch = [i for i in ids if i and "_batch_" in str(i)]
print(f"LEGACY_BATCH_JOB_COUNT={len(legacy_batch)}")
if legacy_batch[:15]:
    print("LEGACY_BATCH_SAMPLES=" + json.dumps(legacy_batch[:15]))

pipeline = [i for i in ids if i and str(i).startswith("pipeline_")]
print(f"PIPELINE_JOB_COUNT={len(pipeline)}")
