# Field encryption (credentials at rest)

**Status:** Done in application code; **inactive in cluster** until `FIELD_ENCRYPTION_KEY` is set and migration `0123` is applied with that key.

## Goals

- Connector / destination / company credential fields must not sit as reversible plaintext (or weak local-only schemes) once a master key is available.
- Support versioned payloads so keys can rotate.

## What was implemented

### Library

File: `syncdatesy-adapter/apps/utils/encryption.py`

- AES-GCM with PBKDF2-derived material.
- `encrypt_sensitive` / `decrypt_sensitive`.
- Versioned payloads (length-7 style versioning) use master `FIELD_ENCRYPTION_KEY` when configured.
- Legacy path can still use company-derived material when the master key is absent (compatibility).

### Model usage

File: `apps/elttool/models.py`

- Company / source / destination config credential fields go through `encrypt_sensitive` on write (master key preferred when set).

### Data migration

File: `apps/elttool/migrations/0123_reencrypt_credentials_master_key.py`

- Re-encrypts SourceConfig / DestinationConfig / `Company.cmp_name` material to the master key.
- **No-op** when `FIELD_ENCRYPTION_KEY` is unset (safe to deploy code before the key exists).

## Enabling in an environment

1. Generate a strong key; store it in the secret manager (not git).
2. Set `FIELD_ENCRYPTION_KEY` (and bump `FIELD_ENCRYPTION_KEY_VERSION` when rotating).
3. Add the same vars to k8s Secret / ExternalSecret templates for Django (and any worker that decrypts).
4. Run migrations so `0123` executes with the key present.
5. Follow [`SECRET_ROTATION.md`](../../syncdatesy-adapter/docs/operations/SECRET_ROTATION.md) for later rotations.

## Gaps

- Key/version are **not** in batch-cluster secret example templates yet.
- Until the key is live, new writes may remain on the legacy scheme — treat enabling encryption as an explicit ops task, not “already on in production.”
