# Deployment hardening notes

## Pod and container `securityContext`

Deployments under this directory should run as non-root with dropped capabilities. Example (adjust `runAsUser` / `runAsGroup` to match the image `USER`):

```yaml
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: app
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            runAsUser: 1000
            capabilities:
              drop:
                - ALL
```

`readOnlyRootFilesystem: true` is ideal but often requires writable `emptyDir` mounts for Python caches, temp files, or nginx runtime dirs — enable per image after testing.

## Health probes

| Workload | Readiness | Liveness | Notes |
|----------|-----------|----------|-------|
| `django.yaml` | `/health/` :8000 | same | Host header must match ingress host for Django `ALLOWED_HOSTS`. |
| `migration-function.yaml` | `/healthz` :8001 | same | `startupProbe` avoids slow-start restarts. |
| `fastapi.yaml` | — | — | **Follow-up:** add HTTP readiness/liveness on a stable path (e.g. `/health` or metrics) before production PDB/HPA reliance. |
| `frontend.yaml` | — | — | **Follow-up:** nginx `/` or dedicated `/healthz` once the frontend image exposes it. |

Tune `initialDelaySeconds` and timeouts for cold starts (large images, DB/Redis on first request).

## Image tags

Manifests currently use moving tags such as `:qa` or `:batch`. **Follow-up:** pin immutable digests or semver tags in production, and record the tag in rollout scripts / Cloud Build substitutions.

## Related Phase 5 manifests

- PDBs: `k8s/pdb/`
- Network policies: `k8s/network-policies/`
- Secrets guidance: `k8s/secrets/README.md`
