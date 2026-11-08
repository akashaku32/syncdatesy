# Prometheus metrics (quick reference)

**Full endpoint documentation:** **[docs/operations/PROMETHEUS_METRICS.md](../../docs/operations/PROMETHEUS_METRICS.md)**

## All endpoints at a glance

| Endpoint | Auth | Type |
|----------|------|------|
| `GET /api/metrics` | None | Raw Prometheus (FastAPI) |
| `GET :9100/metrics` on `celery-worker-control` | In-cluster | Raw Prometheus (primary) |
| `GET /api/v1/admin/ops/overview/` | Superuser JWT | JSON + `prometheus_gauges` |
| `GET /api/v1/admin/ops/workers/` | Superuser JWT | Celery worker inspect |
| `GET /api/v1/admin/ops/jobs/` | Superuser JWT | Migration jobs list |
| `GET /api/v1/test-celery-connection/` | User JWT | Celery connectivity |
| `WS /ws/admin_ops/?token=` | Superuser JWT | Live ops + metrics snapshot |

## Scrape targets (QA)

| Workload | URL |
|----------|-----|
| `celery-worker-control` | `http://celery-worker-control-metrics:9100/metrics` |
| `fastapi` (public) | `https://qa-kubernetes.datasyncher.com/api/metrics` |

Enable: `CELERY_PROMETHEUS_METRICS_ENABLED=true`

## Apply manifests

```bash
kubectl apply -f k8s/monitoring/celery-control-metrics-service.yaml
kubectl apply -f k8s/monitoring/celery-control-podmonitoring.yaml
kubectl apply -f k8s/monitoring/fastapi-podmonitoring.yaml
```

## Verify

```bash
kubectl port-forward deploy/celery-worker-control 9100:9100
curl -s localhost:9100/metrics | grep datasyncher_
```

See [docs/operations/PROMETHEUS_METRICS.md](../../docs/operations/PROMETHEUS_METRICS.md) for auth, response examples, metric catalog, and QA checklist.
