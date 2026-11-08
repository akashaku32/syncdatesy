import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datasyncher.settings.developer")
django.setup()
from apps.elttool.models import PipelineRun
for r in PipelineRun.objects.order_by("-id")[:10]:
    m = r.metadata or {}
    err = m.get("error") or ""
    print(f"=== Run {r.id}: status={r.status} started={r.started_at} finished={r.finished_at}")
    print(f"    executor={m.get('executor')} current={m.get('current_node_ids')} executed={m.get('executed_node_ids')}")
    if err:
        print(f"    error={err[:250]}")
    for k, v in sorted((m.get("nodes") or {}).items(), key=lambda x: int(x[0])):
        print(f"    node {k} ({v.get('batch_name')}): status={v.get('status')} session={v.get('migration_session_id')}")
