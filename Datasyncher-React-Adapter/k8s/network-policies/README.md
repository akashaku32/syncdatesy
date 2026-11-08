# Network policies

Apply after workloads are running in the target namespace:

```bash
kubectl apply -f k8s/network-policies/
```

## Files

| File | Purpose |
|------|---------|
| `default-deny-ingress.yaml` | Zero-trust baseline: block all pod ingress unless explicitly allowed. |
| `allow-app-ingress.yaml` | Permit traffic from the `ingress-nginx` namespace to frontend, Django, FastAPI, migration-function, documentation, and **proxy-agent-gateway** pods (includes TCP 8080). Also permits app and Celery worker pods to reach in-cluster Redis (`app: redis`) on TCP 6379. |
| `allow-gateway-to-django.yaml` | Allow `proxy-agent-gateway` → `django` on TCP 8000 for internal migration-progress ingest. |
| `allow-hub-to-django.yaml` | Allow `sap-proxy-agent-hub` → `django` on TCP 8000 for gRPC progress relay. |
| `allow-migration-function-to-sap-proxy-agent-hub.yaml` | Allow `migration-function` → `sap-proxy-agent-hub` on TCP 50051 (SAP OData proxy gRPC client). |

## Prerequisites

- **CNI** must enforce `NetworkPolicy` (GKE with Calico or Dataplane V2).
- **ingress-nginx** controller must run in namespace `ingress-nginx` (default label `kubernetes.io/metadata.name: ingress-nginx`). Adjust `namespaceSelector` if your install uses a different namespace.
- **Redis**: batch/QA often use [Memorystore](https://cloud.google.com/memorystore) outside the cluster. The `allow-redis-from-apps` policy applies only when Redis runs as pods with label `app: redis`. External Redis is governed by VPC firewall rules, not these policies.

## Order of apply

1. `default-deny-ingress.yaml`
2. `allow-app-ingress.yaml`
3. `allow-gateway-to-django.yaml` (when proxy-agent-gateway is deployed)

Verify connectivity from the ingress controller and from an app pod before enabling in production.
