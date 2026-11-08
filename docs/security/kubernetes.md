# Kubernetes hardening

**Status:** Done for selected deployments + NetworkPolicies/PDBs. Not yet uniform across every workload.

## Goals

- Pods run as non-root with minimal capabilities.
- Default-deny ingress between pods; allow only required paths.
- Secrets never committed as live values.

## Pod securityContext

Applied on (at least):

- `k8s/deployments/django.yaml`
- `k8s/deployments/fastapi.yaml`
- `k8s/deployments/migration-function.yaml`

Typical controls:

- `runAsNonRoot: true`
- UID/GID `1000`
- `seccompProfile.type: RuntimeDefault`
- `allowPrivilegeEscalation: false`
- `capabilities.drop: ["ALL"]`

Notes: `k8s/deployments/README-HARDENING.md`.

## NetworkPolicies

- `k8s/network-policies/default-deny-ingress.yaml`
- `k8s/network-policies/allow-app-ingress.yaml`
- Folder README explains intent.

## Availability

- PodDisruptionBudgets under `k8s/pdb/` (django, frontend).

## Secrets

- Example / local templates only in git.
- Prefer External Secrets, Sealed Secrets, or GCP Secret Manager (`k8s/secrets/README.md`).
- Batch ingress: `k8s/batch-cluster/ingress/datasyncher-ingress.yaml` (TLS via cert-manager).

## Gaps

- Celery and other deployments may still lack the same `securityContext` — extend the same profile.
- Image digest pinning not fully adopted.
- FastAPI/frontend probes and related hardening called out in README as follow-ups.
- Add `FIELD_ENCRYPTION_KEY` to secret templates when enabling field encryption ([field-encryption.md](./field-encryption.md)).
