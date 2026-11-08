#!/usr/bin/env bash
# Manual load-test checklist for ~100 concurrent accounts.
# This script does not create accounts; it prints verification commands.
set -euo pipefail

echo "== Scale-to-100 load test checklist =="
echo
echo "1. Confirm non-blocking + orchestrator scale"
echo "   kubectl get deploy celery-worker-django celery-worker-django-ops -o wide"
echo "   kubectl get deploy celery-worker-django -o jsonpath='{.spec.replicas}{\"\\n\"}'"
echo "   kubectl set env deploy/celery-worker-django --list | grep -E 'PIPELINE_NONBLOCKING|PIPELINE_SCHEDULE|FLEET_REUSE|VISIBILITY'"
echo
echo "2. Confirm pod caps on control"
echo "   kubectl set env deploy/celery-worker-control --list | grep -E 'CLUSTER_MAX|PROVISION_RATE|POOL_'"
echo
echo "3. Trigger 100 small flows (API or scheduler), then watch:"
echo "   kubectl get pods -l app=celery-worker-job --field-selector=status.phase=Pending | wc -l"
echo "   kubectl top pods -l app=celery-worker-django"
echo "   # Redis / logs: company_pod_cap, company_rate_limit, PIPELINE_SCHEDULE_JITTER"
echo
echo "4. Postgres connections (Cloud SQL or psql):"
echo "   SELECT count(*) FROM pg_stat_activity;"
echo "   SHOW max_connections;"
echo
echo "5. Pass criteria"
echo "   - Flows leave queued without multi-hour waits (except plan-cap waits)"
echo "   - Pending job pods drain as nodes scale"
echo "   - No orchestrator OOM / crash loops"
echo "   - DB connections stay under max_connections with headroom"
echo
echo "See docs/operations/SCALE_100_ACCOUNTS.md for full notes."
