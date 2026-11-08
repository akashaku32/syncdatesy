# Secret rotation checklist

1. Rotate Postgres password previously embedded in `developer.py`; update `DATABASE_URL`.
2. Generate new Django `SECRET_KEY` and set in every environment.
3. Generate new `INTERNAL_API_KEY`; update Django, FastAPI, migration-function together.
4. Set `FIELD_ENCRYPTION_KEY`, bump `FIELD_ENCRYPTION_KEY_VERSION`, run migration `0123`.
5. Rotate SAP mTLS key; mount via K8s secrets (never commit `*.key`).
6. Rotate spectra JWT keys under `spectra-sf-jwt/`.
7. Recreate `k8s/secrets/*.yaml` via External Secrets / Sealed Secrets / GCP Secret Manager.
