# Secrets and settings

**Status:** Done for required app keys in settings; cluster encryption key still optional / unset.

## Goals

- Never ship real secrets in git.
- Crash or refuse to serve when required secrets are missing (fail closed).
- Document rotation so keys can be changed without guesswork.

## What was implemented

### Django settings

| Setting | Behavior | File |
|---------|----------|------|
| `SECRET_KEY` | Required from env — no default | `Datasyncher-React-Adapter/datasyncher/settings/base.py` |
| `INTERNAL_API_KEY` | Required from env — no default | same |
| `FIELD_ENCRYPTION_KEY` | Optional string (default `""`); encryption path activates when set | same |
| DB URL | `DATABASE_URL` via env (developer settings) — no embedded password | `datasyncher/settings/developer.py` |
| Cookies / HSTS / SSL | Driven by env in kubernetes/production settings | `datasyncher/settings/kubernetes.py`, `production.py` |

### Documentation & templates

- Example env: `Datasyncher-React-Adapter/.env.example` (`SECRET_KEY`, `INTERNAL_API_KEY`, `FIELD_ENCRYPTION_KEY`).
- Rotation checklist: [`Datasyncher-React-Adapter/docs/operations/SECRET_ROTATION.md`](../../Datasyncher-React-Adapter/docs/operations/SECRET_ROTATION.md).
- K8s secrets hygiene: [`Datasyncher-React-Adapter/k8s/secrets/README.md`](../../Datasyncher-React-Adapter/k8s/secrets/README.md) (External Secrets / Sealed Secrets / GCP Secret Manager).

## Operator checklist

1. Set `SECRET_KEY` and `INTERNAL_API_KEY` in every environment (Django, FastAPI, migration-function, and **proxy-agent-gateway** must share the same internal key).
2. Prefer GCP Secret Manager / External Secrets over raw `kubectl apply` of Secret YAML with live values.
3. Never commit `.env`, `*.key`, or filled-in Secret manifests.
4. When rotating, follow `SECRET_ROTATION.md` (especially step that updates Django + FastAPI + migration-function + proxy-agent-gateway together).
5. Proxy agent: set `PROXY_AGENT_PUBLIC_API_HOST` / CA paths; rotate agent signing secrets by re-issuing `POST /api/v1/proxy-agent/generate/`.

## Gaps

- `FIELD_ENCRYPTION_KEY` / version are **not** present in `k8s/batch-cluster/secrets` templates — see [field-encryption.md](./field-encryption.md).
- `scripts/security_checks.ps1` is referenced from root `AGENTS.md` but is not in the tree yet.
